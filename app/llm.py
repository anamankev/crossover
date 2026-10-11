"""The baseline: an LLM asked the same question with no Qloo data.

It gets the same three inputs the Qloo side gets and is asked for the
same number of picks. It is not told to be mainstream or obscure. The
prompt is deliberately plain so the comparison is fair: this is what you
get when an agent recommends from its own memory.

The call uses the "OpenAI-compatible" chat format, which Groq, Google,
OpenRouter, OpenAI and others all accept. Changing provider or model is
a matter of changing LLM_BASE_URL and LLM_MODEL, not the code.
"""

import json
import time

import httpx

# Groq has a free tier that needs no credit card.
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
CACHE_SECONDS = 24 * 60 * 60

SYSTEM_PROMPT = "You are a recommendation assistant. Recommend things the person will genuinely love."

class LLMError(Exception):
    """The LLM could not be reached, refused, or answered in the wrong shape."""


def build_prompt(seed_names: list[str], input_noun: str, output_noun: str, naming: str,
                 take: int, with_year: bool) -> str:
    """The exact text the baseline LLM sees. Also shown on the page."""
    seeds = "\n".join(f"- {name}" for name in seed_names)
    example = '{"name": "...", "year": 1999, "reason": "..."}' if with_year else '{"name": "...", "reason": "..."}'
    return (
        f"A person loves these {input_noun}:\n{seeds}\n\n"
        f"Recommend exactly {take} {output_noun} this person would love.\n"
        f"{naming}\n"
        "For each pick give a one-sentence reason tied to their taste.\n"
        f'Answer as JSON: {{"picks": [{example}]}}'
    )


def parse_picks(text: str, take: int) -> list[dict]:
    """Read the LLM's JSON answer into a clean list of {"name", "year", "reason"}.

    `year` is None when the LLM gave none. Duplicates and blank names are
    dropped, and the list is cut to `take`.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("The LLM did not answer in JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("picks"), list):
        raise LLMError('The LLM answer has no "picks" list')

    picks, seen = [], set()
    for item in data["picks"]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        year = item.get("year")
        if isinstance(year, bool) or not isinstance(year, int):
            year = None
        key = (name.lower(), year)
        if not name or key in seen:
            continue
        seen.add(key)
        picks.append({"name": name, "year": year, "reason": str(item.get("reason", "")).strip()})
    if not picks:
        raise LLMError("The LLM returned no usable picks")
    return picks[:take]


class LLMBaseline:
    def __init__(self, api_key: str, model: str = "", base_url: str = "", complete=None):
        """`complete` can replace the real API call in tests: async (prompt) -> str."""
        self.model = model or DEFAULT_MODEL
        self.configured = bool(api_key) or complete is not None
        self._http = httpx.AsyncClient(
            base_url=(base_url or DEFAULT_BASE_URL).rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        ) if api_key else None
        self._complete = complete or self._call_llm
        self._cache: dict[tuple, tuple[float, list[dict]]] = {}

    async def close(self):
        if self._http:
            await self._http.aclose()

    async def _call_llm(self, prompt: str) -> str:
        try:
            response = await self._http.post("/chat/completions", json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                # Ask for a JSON object back. parse_picks() still checks the shape.
                "response_format": {"type": "json_object"},
            })
        except httpx.HTTPError as exc:
            raise LLMError("Could not reach the LLM") from exc

        if response.status_code == 401:
            raise LLMError("The LLM API key was rejected")
        if response.status_code == 429:
            raise LLMError("The LLM is rate limited right now. Try again in a minute")
        if response.status_code != 200:
            raise LLMError(f"The LLM returned HTTP {response.status_code}")

        try:
            choice = response.json()["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("The LLM answered in an unexpected format") from exc
        if choice.get("finish_reason") == "length":
            raise LLMError("The LLM answer was cut off")
        if not text:
            raise LLMError("The LLM returned no text")
        return text

    async def recommend(self, seed_names: list[str], input_noun: str, output_noun: str,
                        naming: str, take: int, with_year: bool) -> list[dict]:
        """Ask the plain LLM for `take` picks. Returns [{"name", "year", "reason"}]."""
        if not self.configured:
            raise LLMError("No LLM API key is configured")
        key = (tuple(sorted(seed_names)), output_noun, take)
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        prompt = build_prompt(seed_names, input_noun, output_noun, naming, take, with_year)
        picks = parse_picks(await self._complete(prompt), take)
        self._cache[key] = (time.monotonic() + CACHE_SECONDS, picks)
        return picks
