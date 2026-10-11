"""The Qloo-grounded agent.

An LLM is given three tools and decides for itself how to use them:

    get_taste_graph_picks   ask Qloo what people with this taste love
    check_my_ideas          have Qloo verify and score the LLM's own ideas
    submit_picks            give the final answer

The one hard rule is enforced here in code, not left to the prompt:
every final pick must be something a tool returned, which means Qloo
has confirmed it exists and scored how well it fits. Anything else the
model submits is thrown away.

The agent uses the same model as the plain-LLM baseline. The only
difference between the two sides is access to Qloo.
"""

import json
import time

from app import metrics
from app.compare import check_candidates, pick_to_dict, resolve_seeds, seed_label, seeds_to_list
from app.domains import DOMAINS
from app.llm import LLM, LLMError
from app.qloo import Entity, QlooClient

MAX_TURNS = 6  # one turn = one message from the model
DEFAULT_GRAPH_PICKS = 12
MAX_IDEAS = 8
CACHE_SECONDS = 24 * 60 * 60


def build_tools(output_noun: str, needs_year: bool) -> list[dict]:
    """The tool descriptions the model sees."""
    idea = {"name": {"type": "string"}}
    if needs_year:
        idea["year"] = {"type": "integer", "description": "Release year"}
    return [
        {"type": "function", "function": {
            "name": "get_taste_graph_picks",
            "description": (
                f"Ask Qloo's taste graph for the {output_noun} that people with this person's taste "
                "love most. Returns candidates, each with an id and a fit score from 0 to 1."),
            "parameters": {"type": "object", "properties": {
                "how_many": {"type": "integer", "description": "Between 5 and 20. Default 12."}}},
        }},
        {"type": "function", "function": {
            "name": "check_my_ideas",
            "description": (
                f"Check your own ideas for {output_noun} against Qloo. For each one Qloo says whether "
                "it is in the catalog and how well it fits this person, and gives it an id you can use."),
            "parameters": {"type": "object", "required": ["ideas"], "properties": {
                "ideas": {"type": "array", "maxItems": MAX_IDEAS, "items": {
                    "type": "object", "required": sorted(idea), "properties": idea}}}},
        }},
        {"type": "function", "function": {
            "name": "submit_picks",
            "description": "Give your final answer. Every id must come from an earlier tool result.",
            "parameters": {"type": "object", "required": ["picks"], "properties": {
                "picks": {"type": "array", "items": {
                    "type": "object", "required": ["id", "reason"], "properties": {
                        "id": {"type": "string"}, "reason": {"type": "string"}}}}}},
        }},
    ]


def build_system_prompt(output_noun: str, take: int) -> str:
    return (
        f"You recommend {output_noun} to one person. You have Qloo's taste graph, which is built "
        "from what real people love, and you must ground your answer in it.\n\n"
        "How to work:\n"
        "1. Call get_taste_graph_picks to see what the graph suggests.\n"
        "2. Think of a few ideas of your own that the graph may have missed, and call "
        "check_my_ideas to see how they really fit.\n"
        f"3. Call submit_picks with exactly {take} picks.\n\n"
        "Rules:\n"
        "- Every pick must be an id from a tool result. Never recommend anything Qloo has not checked.\n"
        "- Prefer a higher fit. Keep one of your own ideas only if its fit is close to the graph's picks.\n"
        "- If the best picks are all one genre and a nearly-as-good pick adds range, include it.\n"
        "- Each reason is one sentence written to the person, based on the fit and genres you were "
        "shown. Do not invent facts."
    )


def describe(handle: str, entity: Entity) -> str:
    """One compact line of tool output: 'g1 | Nick Drake | fit 0.956 | Folk, Acoustic'."""
    name = f"{entity.name} ({entity.year})" if entity.year else entity.name
    genres = sorted({t["name"] for t in entity.tags if t.get("type", "").startswith(metrics.GENRE_TAG_PREFIX)})
    fit = f"{entity.affinity:.3f}" if entity.affinity is not None else "unknown"
    return f"{handle} | {name} | fit {fit} | {', '.join(genres[:3]) or 'no genre tags'}"


class AgentRun:
    """One request's worth of state: the pool of verified candidates and the trace."""

    def __init__(self, qloo: QlooClient, seeds: list[dict], output_domain: str, take: int):
        self.qloo = qloo
        self.seeds = seeds
        self.seed_ids = [s["entity"].entity_id for s in seeds]
        self.output_domain = output_domain
        self.target = DOMAINS[output_domain]
        self.take = take
        self.pool: dict[str, Entity] = {}  # handle -> entity, e.g. "g1" or "m2"
        self.source: dict[str, str] = {}  # handle -> "graph" or "own"
        self.by_entity: dict[str, str] = {}  # entity_id -> handle
        self.graph_order: list[str] = []
        self.trace: list[dict] = []
        self.final: list[dict] | None = None

    def _add(self, entity: Entity, source: str) -> str:
        """Put a verified entity in the pool and return its handle."""
        if entity.entity_id in self.by_entity:
            return self.by_entity[entity.entity_id]
        prefix = "g" if source == "graph" else "m"
        handle = f"{prefix}{sum(1 for h in self.pool if h.startswith(prefix)) + 1}"
        self.pool[handle] = entity
        self.source[handle] = source
        self.by_entity[entity.entity_id] = handle
        if source == "graph":
            self.graph_order.append(handle)
        return handle

    async def get_taste_graph_picks(self, args: dict) -> str:
        try:
            how_many = int(args.get("how_many") or DEFAULT_GRAPH_PICKS)
        except (TypeError, ValueError):
            how_many = DEFAULT_GRAPH_PICKS
        how_many = max(5, min(20, how_many))
        started = time.monotonic()
        entities = await self.qloo.recommend(self.seed_ids, self.target["qloo_type"], how_many)
        lines = [describe(self._add(e, "graph"), e) for e in entities]
        fits = [e.affinity for e in entities if e.affinity is not None]
        self.trace.append({
            "kind": "graph",
            "title": f"Asked Qloo's taste graph for {self.target['noun']}",
            "detail": (f"{len(entities)} candidates, fit {max(fits):.3f} down to {min(fits):.3f}"
                       if fits else f"{len(entities)} candidates"),
            "call": "GET /v2/insights",
            "ms": round((time.monotonic() - started) * 1000),
        })
        return "\n".join(lines) or "The taste graph returned nothing."

    async def check_my_ideas(self, args: dict) -> str:
        ideas = []
        for item in (args.get("ideas") or [])[:MAX_IDEAS]:
            if isinstance(item, dict) and str(item.get("name", "")).strip():
                year = item.get("year")
                ideas.append({"name": str(item["name"]).strip(),
                              "year": year if isinstance(year, int) and not isinstance(year, bool) else None})
        if not ideas:
            return "No ideas were given. Pass a list like [{\"name\": \"...\"}]."

        started = time.monotonic()
        results = await check_candidates(self.qloo, self.seed_ids, self.output_domain, ideas)
        lines, items = [], []
        for result in results:
            entity = result["entity"]
            if entity is None:
                lines.append(f"{result['name']} | not usable: {result['why_not']}")
                items.append({"name": result["name"], "fit": None, "why_not": result["why_not"], "handle": None})
                continue
            already = entity.entity_id in self.by_entity
            handle = self._add(entity, "own")
            note = " | already in the graph's list" if already and self.source[handle] == "graph" else ""
            lines.append(describe(handle, entity) + note)
            items.append({"name": entity.name, "fit": entity.affinity, "why_not": None, "handle": handle})
        self.trace.append({
            "kind": "check",
            "title": f"Checked {len(ideas)} of its own ideas against Qloo",
            "items": items,
            "call": "GET /search, then GET /v2/insights with filter.results.entities",
            "ms": round((time.monotonic() - started) * 1000),
        })
        return "\n".join(lines)

    def submit_picks(self, args: dict) -> str:
        chosen, seen, rejected = [], set(), 0
        for item in args.get("picks") or []:
            handle = str(item.get("id", "")).strip() if isinstance(item, dict) else ""
            if handle not in self.pool or handle in seen:
                rejected += 1  # not something Qloo verified, or a repeat
                continue
            seen.add(handle)
            chosen.append({"handle": handle, "reason": str(item.get("reason", "")).strip()})
        chosen = chosen[: self.take]
        if not chosen:
            return "None of those ids came from a tool result. Use ids like g1 or m2 and submit again."

        # If the model came up short, top up from the graph's list in fit order.
        filled = 0
        for handle in self.graph_order:
            if len(chosen) >= self.take:
                break
            if handle not in seen:
                seen.add(handle)
                chosen.append({"handle": handle, "reason": ""})
                filled += 1

        own = sum(1 for c in chosen if self.source[c["handle"]] == "own")
        detail = f"{len(chosen) - own} from the graph's list, {own} of its own ideas that Qloo verified"
        if rejected:
            detail += f". {rejected} submitted pick(s) were discarded because Qloo had not verified them"
        if filled:
            detail += f". {filled} added from the graph's list to reach {self.take}"
        self.trace.append({"kind": "submit", "title": f"Chose its final {len(chosen)}", "detail": detail,
                           "call": "", "ms": 0})
        self.final = chosen
        return "Submitted."

    def mark_kept_ideas(self):
        """Note in the trace which of the agent's own ideas made the final list."""
        kept = {c["handle"] for c in self.final or []}
        for step in self.trace:
            for item in step.get("items", []):
                item["kept"] = item["handle"] in kept


async def run_tool(run: AgentRun, name: str, raw_arguments: str) -> str:
    try:
        args = json.loads(raw_arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return "Could not read the arguments. Send a JSON object."
    if name == "get_taste_graph_picks":
        return await run.get_taste_graph_picks(args)
    if name == "check_my_ideas":
        return await run.check_my_ideas(args)
    if name == "submit_picks":
        return run.submit_picks(args)
    return f"There is no tool called {name}."


async def converse(run: AgentRun, llm: LLM, model: str, seed_labels: list[str], input_noun: str) -> int:
    """Let the model use the tools until it submits. Returns how many times it was called."""
    tools = build_tools(run.target["noun"], run.target["needs_year"])
    likes = "\n".join(f"- {label}" for label in seed_labels)
    messages = [
        {"role": "system", "content": build_system_prompt(run.target["noun"], run.take)},
        {"role": "user", "content": f"This person loves these {input_noun}:\n{likes}\n\n"
                                    f"Recommend {run.take} {run.target['noun']} for them."},
    ]
    for turn in range(1, MAX_TURNS + 1):
        message = await llm.chat(messages, model, tools=tools)
        calls = message.get("tool_calls") or []
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls}
                        if calls else {"role": "assistant", "content": message.get("content") or ""})
        if not calls:
            messages.append({"role": "user", "content": "Use the tools, then call submit_picks with your final picks."})
            continue
        for call in calls:
            function = call.get("function") or {}
            result = await run_tool(run, function.get("name", ""), function.get("arguments", ""))
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
        if run.final:
            return turn
    raise LLMError("The agent did not finish within its turn limit")


class Agent:
    def __init__(self, qloo: QlooClient, llm: LLM):
        self.qloo = qloo
        self.llm = llm
        self._cache: dict[tuple, tuple[float, dict]] = {}

    async def recommend(self, names: list[str], input_domain: str, output_domain: str, take: int) -> dict:
        source = DOMAINS[input_domain]
        started = time.monotonic()
        seeds = await resolve_seeds(self.qloo, names, source["qloo_type"])
        key = (tuple(sorted(s["entity"].entity_id for s in seeds)), output_domain, take)
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]

        seed_labels = [seed_label(s["entity"]) for s in seeds]
        found_step = {
            "kind": "seeds",
            "title": f"Found your {source['noun']} in Qloo's catalog",
            "detail": ", ".join(seed_labels),
            "call": "GET /search",
            "ms": round((time.monotonic() - started) * 1000),
        }

        run, model_used, llm_calls, problem = None, None, 0, "No LLM API key is configured"
        if self.llm.configured:
            for model in self.llm.models:
                attempt = AgentRun(self.qloo, seeds, output_domain, take)
                try:
                    llm_calls = await converse(attempt, self.llm, model, seed_labels, source["noun"])
                    run, model_used = attempt, model
                    break
                except LLMError as exc:
                    problem = str(exc)  # try the next model

        if run is None:
            result = await self._without_llm(seeds, output_domain, take, found_step, problem)
        else:
            run.mark_kept_ideas()
            picks, entities = [], []
            for choice in run.final:
                entity = run.pool[choice["handle"]]
                entities.append(entity)
                picks.append({**pick_to_dict(entity, seeds), "reason": choice["reason"],
                              "source": run.source[choice["handle"]]})
            result = {
                "seeds": seeds_to_list(seeds),
                "agent_ran": True,
                "note": None,
                "model": model_used,
                "llm_calls": llm_calls,
                "picks": picks,
                "metrics": metrics.summarize(entities),
                "trace": [found_step] + run.trace,
            }
            self._cache[key] = (time.monotonic() + CACHE_SECONDS, result)
        return result

    async def _without_llm(self, seeds, output_domain, take, found_step, problem) -> dict:
        """Qloo's direct answer, used when the LLM cannot be reached. Clearly labelled as such."""
        target = DOMAINS[output_domain]
        started = time.monotonic()
        entities = await self.qloo.recommend([s["entity"].entity_id for s in seeds], target["qloo_type"], take)
        return {
            "seeds": seeds_to_list(seeds),
            "agent_ran": False,
            "note": f"The agent's LLM was unavailable ({problem}). These are the taste graph's top picks, "
                    "taken directly from Qloo with no LLM involved.",
            "model": None,
            "llm_calls": 0,
            "picks": [{**pick_to_dict(e, seeds), "reason": "", "source": "graph"} for e in entities],
            "metrics": metrics.summarize(entities),
            "trace": [found_step, {
                "kind": "graph",
                "title": f"Asked Qloo's taste graph for {target['noun']}",
                "detail": f"{len(entities)} candidates",
                "call": "GET /v2/insights",
                "ms": round((time.monotonic() - started) * 1000),
            }],
        }
