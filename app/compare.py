"""The two sides of the comparison, step by step.

grounded(): the Qloo-grounded agent.
    1. find the user's inputs in Qloo's catalog
    2. ask the taste graph for the best fits in the other domain
    3. read back which input drove each pick

baseline(): a plain LLM answering from memory, then *measured* with Qloo.
    1. ask the LLM the same question
    2. look each of its picks up in Qloo (exact name; for films the year must agree too)
    3. ask Qloo to score those picks against the same inputs

Both return plain dictionaries ready to send to the browser.
"""

import asyncio
import time

from app import metrics
from app.domains import DOMAINS
from app.llm import LLMBaseline, build_prompt
from app.qloo import Entity, QlooClient, normalize_name


class InputNotFound(Exception):
    """One of the user's inputs matched nothing in Qloo."""


async def resolve_seeds(qloo: QlooClient, names: list[str], qloo_type: str) -> list[dict]:
    """Find each input in Qloo. Returns [{"query", "entity", "exact"}].

    Prefers an exact name match. Otherwise takes Qloo's top hit and marks
    it `exact: False` so the screen can show what was actually used.
    """
    async def one(name: str) -> dict:
        results = await qloo.search(name, qloo_type)
        if not results:
            raise InputNotFound(name)
        wanted = normalize_name(name)
        for entity in results:
            if normalize_name(entity.name) == wanted:
                return {"query": name, "entity": entity, "exact": True}
        return {"query": name, "entity": results[0], "exact": False}

    seeds = await asyncio.gather(*(one(name) for name in names))

    # The same entity typed twice counts once.
    unique, seen = [], set()
    for seed in seeds:
        if seed["entity"].entity_id not in seen:
            seen.add(seed["entity"].entity_id)
            unique.append(seed)
    return unique


def seed_label(entity: Entity) -> str:
    """'Parasite (2019)'. The year tells same-named films apart."""
    if entity.disambiguation and entity.disambiguation != entity.name:
        return f"{entity.name} ({entity.disambiguation})"
    return entity.name


def pick_to_dict(entity: Entity, seeds: list[dict] | None = None) -> dict:
    drivers = []
    if seeds and entity.contributions:
        for seed in seeds:
            share = entity.contributions.get(seed["entity"].entity_id)
            if share is not None:
                drivers.append({"seed": seed["entity"].name, "share": share})
        drivers.sort(key=lambda d: d["share"], reverse=True)
    return {
        "entity_id": entity.entity_id,
        "name": entity.name,
        "year": entity.year,
        "affinity": entity.affinity,
        "popularity": entity.popularity,
        "image_url": entity.image_url,
        "genres": sorted({t["name"] for t in entity.tags
                          if t.get("type", "").startswith(metrics.GENRE_TAG_PREFIX)}),
        "drivers": drivers,
    }


def seeds_to_list(seeds: list[dict]) -> list[dict]:
    return [{
        "query": s["query"],
        "entity_id": s["entity"].entity_id,
        "name": s["entity"].name,
        "label": seed_label(s["entity"]),
        "image_url": s["entity"].image_url,
        "exact": s["exact"],
    } for s in seeds]


async def grounded(qloo: QlooClient, names: list[str], input_domain: str, output_domain: str, take: int) -> dict:
    source, target = DOMAINS[input_domain], DOMAINS[output_domain]
    trace = []

    started = time.monotonic()
    seeds = await resolve_seeds(qloo, names, source["qloo_type"])
    trace.append({
        "step": f"Found your {source['noun']} in Qloo's catalog",
        "detail": ", ".join(seed_label(s["entity"]) for s in seeds),
        "call": "GET /search",
        "ms": round((time.monotonic() - started) * 1000),
    })

    started = time.monotonic()
    seed_ids = [s["entity"].entity_id for s in seeds]
    picks = await qloo.recommend(seed_ids, target["qloo_type"], take)
    trace.append({
        "step": f"Asked the taste graph for {target['noun']}",
        "detail": f"{len(seed_ids)} {source['noun']} as signals, results filtered to {target['qloo_type']}",
        "call": "GET /v2/insights",
        "ms": round((time.monotonic() - started) * 1000),
    })
    trace.append({
        "step": "Read which input drove each pick",
        "detail": "Qloo's explainability data, returned with the same call",
        "call": "feature.explainability=true",
        "ms": 0,
    })

    return {
        "seeds": seeds_to_list(seeds),
        "picks": [pick_to_dict(p, seeds) for p in picks],
        "metrics": metrics.summarize(picks),
        "trace": trace,
    }


def choose_match(candidates: list[Entity], scored: dict[str, Entity], year: int | None, needs_year: bool):
    """Decide which Qloo entity, if any, an LLM pick really refers to.

    `candidates` are the catalog entries with exactly the pick's name.
    `scored` holds the ones Qloo returned a score for.
    Returns (entity, None) on a confident match, or (None, why_not).

    Films: the release year must agree too, give or take one year
    (festival and cinema release dates often differ by a year). Without
    this, the 2002 "Solaris" could be measured as the 1972 one.

    Artists: there is no year to check. If several artists share the
    exact name, the most popular one in Qloo is taken, on the reasoning
    that an LLM naming "Nirvana" means the famous one.
    """
    if not candidates:
        return None, "No exact name match in Qloo's catalog"
    with_score = [scored[c.entity_id] for c in candidates if c.entity_id in scored]
    if not with_score:
        return None, "In Qloo's catalog, but Qloo returned no score for it"

    if needs_year:
        if year is None:
            return None, "The LLM gave no year, so the match could not be checked"
        for entity in with_score:
            if entity.year is not None and abs(entity.year - year) <= 1:
                return entity, None
        return None, f"Qloo has this title, but not from {year}"

    return max(with_score, key=lambda e: e.popularity if e.popularity is not None else -1), None


async def baseline(qloo: QlooClient, llm: LLMBaseline, names: list[str],
                   input_domain: str, output_domain: str, take: int) -> dict:
    source, target = DOMAINS[input_domain], DOMAINS[output_domain]
    needs_year = target["needs_year"]

    seeds = await resolve_seeds(qloo, names, source["qloo_type"])
    seed_ids = [s["entity"].entity_id for s in seeds]
    seed_labels = [seed_label(s["entity"]) for s in seeds]

    llm_picks = await llm.recommend(seed_labels, source["noun"], target["noun"],
                                    target["naming"], take, needs_year)

    candidates = await asyncio.gather(
        *(qloo.find_all_exact(p["name"], target["qloo_type"]) for p in llm_picks))

    # One call scores every candidate against the same inputs the Qloo side used.
    candidate_ids = list(dict.fromkeys(e.entity_id for group in candidates for e in group))
    scored = await qloo.score(seed_ids, target["qloo_type"], candidate_ids)

    rows, measured, used = [], [], set()
    for pick, group in zip(llm_picks, candidates):
        entity, why_not = choose_match(group, scored, pick["year"], needs_year)
        row = {
            "llm_name": pick["name"],
            "llm_year": pick["year"],
            "reason": pick["reason"],
            "measured": entity is not None,
            "why_not": why_not,
        }
        if entity:
            row.update(pick_to_dict(entity))
            if entity.entity_id not in used:  # the same thing named twice counts once
                used.add(entity.entity_id)
                measured.append(entity)
        rows.append(row)

    qloo_picks = await qloo.recommend(seed_ids, target["qloo_type"], take)

    return {
        "model": llm.model,
        "prompt": build_prompt(seed_labels, source["noun"], target["noun"], target["naming"], take, needs_year),
        "picks": rows,
        "measured": {"count": sum(1 for r in rows if r["measured"]), "of": len(rows)},
        "metrics": metrics.summarize(measured),
        "overlap": metrics.overlap(qloo_picks, measured),
    }
