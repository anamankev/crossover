"""The baseline: a strong LLM asked the same question with no Qloo data.

It gets the same three inputs the Qloo side gets and is asked for the
same number of picks. It is not told to be mainstream or obscure. The
prompt is deliberately plain so the comparison is fair: this is what you
get when an agent recommends from its own memory.
"""

import json
import time

import anthropic

DEFAULT_MODEL = "claude-opus-5-5"
CACHE_SECONDS = 24 * 60 * 60

SYSTEM_PROMPT = "You are a recommendation assistant. Recommend things the person will genuinely love."

def picks_schema(with_year: bool) -> dict:
    """The JSON shape the LLM must answer in."""
    properties = {"name": {"type": "string"}, "reason": {"type": "string"}}
    if with_year:
        properties["year"] = {"type": "integer"}
    return {
        "type": "object",
        "properties": {
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": properties,
                    "required": sorted(properties),
                    "additionalProperties": False,
                },
            }
        },
        "required": ["picks"],
        "additionalProperties": False,
    }


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
    def __init__(self, api_key: str, model: str = "", complete=None):
        """`complete` can replace the real API call in tests: async (prompt, schema) -> str."""
        self.model = model or DEFAULT_MODEL
        self.configured = bool(api_key) or complete is not None
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=60.0) if api_key else None
        self._complete = complete or self._call_claude
        self._cache: dict[tuple, tuple[float, list[dict]]] = {}

    async def _call_claude(self, prompt: str, schema: dict) -> str:
        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    # The model's normal effort level, so the baseline is not handicapped.
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": schema},
                },
                # If the model declines, let the API retry on its recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("The LLM API key was rejected") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("The LLM is rate limited right now") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"The LLM returned HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the LLM") from exc

        if response.stop_reason == "refusal":
            raise LLMError("The LLM declined to answer")
        if response.stop_reason == "max_tokens":
            raise LLMError("The LLM answer was cut off")
        for block in response.content:
            if block.type == "text":
                return block.text
        raise LLMError("The LLM returned no text")

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
        picks = parse_picks(await self._complete(prompt, picks_schema(with_year)), take)
        self._cache[key] = (time.monotonic() + CACHE_SECONDS, picks)
        return picks
