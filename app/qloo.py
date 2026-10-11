"""A small client for the Qloo API.

Only the two endpoints the app needs:
  /search       turn a name into a Qloo entity
  /v2/insights  ask the taste graph for recommendations, or for scores

See docs/qloo-api-notes.md for what was confirmed against the real API.
"""

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass, field

import httpx

# Qloo allows 5 requests per second. We space calls out to stay under it.
MIN_SECONDS_BETWEEN_CALLS = 0.22
MAX_RETRIES_ON_429 = 4
CACHE_SECONDS = 24 * 60 * 60
CACHE_MAX_ITEMS = 3000


class QlooError(Exception):
    """Qloo answered with an error, or could not be reached."""


@dataclass
class Entity:
    """One thing in Qloo's catalog: a film or a music artist."""

    entity_id: str
    name: str
    disambiguation: str = ""  # e.g. "2019" for a film
    popularity: float | None = None  # percentile 0..1, None if Qloo did not send it
    affinity: float | None = None  # fit to the input signals 0..1, None outside insights calls
    tags: list[dict] = field(default_factory=list)  # each {"id", "name", "type"}
    year: int | None = None  # release year, films only
    image_url: str | None = None
    description: str | None = None
    # entity_id of an input -> share of this recommendation it explains
    contributions: dict[str, float] = field(default_factory=dict)


def normalize_name(name: str) -> str:
    """Reduce a name to a comparable form: 'Cancún' and 'cancun' become equal.

    Lowercases, strips accents and punctuation, drops a leading 'the'.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    text = re.sub(r"^the ", "", text)
    return text


def parse_entity(raw: dict) -> Entity:
    """Pick the fields we use out of one raw Qloo entity."""
    props = raw.get("properties") or {}
    query = raw.get("query") or {}

    description = props.get("description") or props.get("short_description")
    if not description:
        for item in props.get("short_descriptions") or []:
            if item.get("language") == "en":
                description = item.get("value")
                break

    contributions = {}
    explain = (query.get("explainability") or {}).get("signal.interests.entities") or []
    for item in explain:
        if "entity_id" in item and "score" in item:
            contributions[item["entity_id"]] = item["score"]

    tags = [
        {"id": t["id"], "name": t.get("name", ""), "type": t.get("type", "")}
        for t in raw.get("tags") or []
        if "id" in t
    ]

    return Entity(
        entity_id=raw["entity_id"],
        name=raw.get("name", ""),
        disambiguation=raw.get("disambiguation") or "",
        popularity=raw.get("popularity"),
        affinity=query.get("affinity"),
        tags=tags,
        year=props.get("release_year") if isinstance(props.get("release_year"), int) else None,
        image_url=(props.get("image") or {}).get("url"),
        description=description,
        contributions=contributions,
    )


class QlooClient:
    def __init__(self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None,
                 min_interval: float = MIN_SECONDS_BETWEEN_CALLS):
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Api-Key": api_key},
            timeout=30.0,
            transport=transport,
        )
        self._min_interval = min_interval
        self._gate = asyncio.Lock()
        self._next_slot = 0.0
        self._cache: dict[tuple, tuple[float, object]] = {}
        self.calls_made = 0
        self.month_remaining: int | None = None  # from Qloo's response headers

    async def close(self):
        await self._http.aclose()

    # ---- low level -------------------------------------------------

    async def _wait_for_slot(self):
        """Make callers take turns so we never exceed Qloo's per-second limit."""
        async with self._gate:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._min_interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def _get(self, path: str, params: dict) -> dict:
        for attempt in range(MAX_RETRIES_ON_429 + 1):
            await self._wait_for_slot()
            try:
                response = await self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                raise QlooError(f"Could not reach Qloo: {exc.__class__.__name__}") from exc
            self.calls_made += 1

            remaining = response.headers.get("x-month-ratelimit-remaining")
            if remaining and remaining.isdigit():
                self.month_remaining = int(remaining)

            if response.status_code == 429 and attempt < MAX_RETRIES_ON_429:
                try:
                    reset = float(response.headers.get("x-second-ratelimit-reset", "1"))
                except ValueError:
                    reset = 1.0
                await asyncio.sleep(max(reset, 0.3) * (attempt + 1))
                continue
            if response.status_code != 200:
                raise QlooError(f"Qloo returned HTTP {response.status_code}: {_error_text(response)}")
            return response.json()
        raise QlooError("Qloo rate limit: still refused after retries")

    def _cache_get(self, key: tuple):
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        return None

    def _cache_put(self, key: tuple, value):
        if len(self._cache) >= CACHE_MAX_ITEMS:
            self._cache.clear()
        self._cache[key] = (time.monotonic() + CACHE_SECONDS, value)

    # ---- what the app calls ----------------------------------------

    async def search(self, name: str, qloo_type: str, take: int = 5) -> list[Entity]:
        """Qloo's best guesses for a name, best first. The search is fuzzy."""
        key = ("search", normalize_name(name), qloo_type, take)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        data = await self._get("/search", {"query": name, "types": qloo_type, "take": take})
        entities = [parse_entity(raw) for raw in data.get("results") or []]
        self._cache_put(key, entities)
        return entities

    async def find_all_exact(self, name: str, qloo_type: str) -> list[Entity]:
        """Every search hit whose name really matches, best first.

        Qloo's search returns its closest guess even for names that do
        not exist, so a hit alone proves nothing. There can also be more
        than one real match: "Solaris" is a 1972 film and a 2002 film.
        """
        wanted = normalize_name(name)
        if not wanted:
            return []
        return [e for e in await self.search(name, qloo_type) if normalize_name(e.name) == wanted]

    async def recommend(self, seed_ids: list[str], qloo_type: str, take: int) -> list[Entity]:
        """Top `take` entities of `qloo_type` for people who like the seeds."""
        key = ("recommend", tuple(sorted(seed_ids)), qloo_type, take)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        data = await self._get("/v2/insights", {
            "filter.type": qloo_type,
            "signal.interests.entities": ",".join(seed_ids),
            "take": take,
            "feature.explainability": "true",
        })
        entities = [parse_entity(raw) for raw in (data.get("results") or {}).get("entities") or []]
        self._cache_put(key, entities)
        return entities

    async def score(self, seed_ids: list[str], qloo_type: str, entity_ids: list[str]) -> dict[str, Entity]:
        """Ask Qloo how well specific entities fit the seeds.

        Returns entity_id -> Entity with `affinity` filled in. An id Qloo
        leaves out of its answer is simply missing from the result.
        """
        if not entity_ids:
            return {}
        key = ("score", tuple(sorted(seed_ids)), qloo_type, tuple(sorted(entity_ids)))
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        data = await self._get("/v2/insights", {
            "filter.type": qloo_type,
            "signal.interests.entities": ",".join(seed_ids),
            "filter.results.entities": ",".join(entity_ids),
            "take": min(max(len(entity_ids), 1), 50),
            "feature.explainability": "true",
        })
        entities = [parse_entity(raw) for raw in (data.get("results") or {}).get("entities") or []]
        scored = {e.entity_id: e for e in entities if e.entity_id in set(entity_ids)}
        self._cache_put(key, scored)
        return scored


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        if body.get("error_msg"):
            return str(body["error_msg"])
        errors = body.get("errors") or []
        if errors and isinstance(errors[0], dict):
            return str(errors[0].get("message", errors[0]))
    return str(body)[:200]
