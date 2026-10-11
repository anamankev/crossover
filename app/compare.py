"""The two sides of the comparison, step by step.

grounded(): the Qloo-grounded agent.
    1. find the user's three inputs in Qloo's catalog
    2. ask the taste graph for the best fits in a different domain
    3. read back which input drove each pick

baseline(): a plain LLM answering from memory, then *measured* with Qloo.
    1. ask the LLM the same question
    2. look each of its picks up in Qloo (exact name, and the country must agree)
    3. ask Qloo to score those picks against the same three inputs

Both return plain dictionaries ready to send to the browser.
"""

import asyncio
import time

from app import metrics
from app.domains import INPUT_DOMAINS, OUTPUT_DOMAINS
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
    """'Parasite (2019)'. The year or author tells same-named things apart."""
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
        "disambiguation": entity.disambiguation,
        "affinity": entity.affinity,
        "popularity": entity.popularity,
        "country_code": entity.country_code,
        "region": entity.region,
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
        "disambiguation": s["entity"].disambiguation,
        "image_url": s["entity"].image_url,
        "exact": s["exact"],
    } for s in seeds]


async def grounded(qloo: QlooClient, names: list[str], input_domain: str, output_domain: str, take: int) -> dict:
    source, target = INPUT_DOMAINS[input_domain], OUTPUT_DOMAINS[output_domain]
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
        "metrics": metrics.summarize(picks, include_countries=True),
        "trace": trace,
    }


def lookup_names(name: str) -> list[str]:
    """Names to try in Qloo for one LLM pick.

    An LLM often writes 'Kyoto, Japan'. Qloo's destination is 'Kyoto', so
    the part before the comma is tried as well.
    """
    names = [name]
    if "," in name:
        names.append(name.split(",")[0].strip())
    return names


def choose_match(candidates: list[Entity], scored: dict[str, Entity], country_code: str):
    """Decide which Qloo entity, if any, an LLM pick really refers to.

    `candidates` are the catalog entries with exactly the pick's name.
    `scored` holds the ones Qloo returned a score (and a country) for.
    Returns (entity, None) on a confident match, or (None, why_not).

    A pick only counts when the name AND the country agree. Without the
    country check, "Venice" would be measured as Venice, California.
    """
    if not candidates:
        return None, "No place with exactly this name in Qloo's catalog"
    with_score = [scored[c.entity_id] for c in candidates if c.entity_id in scored]
    if not with_score:
        return None, "In Qloo's catalog, but Qloo returned no score for it"
    if not country_code:
        return None, "The LLM gave no country, so the match could not be checked"
    for entity in with_score:
        if (entity.country_code or "").upper() == country_code.upper():
            return entity, None
    return None, f"Qloo has a place with this name, but not in {country_code.upper()}"


async def baseline(qloo: QlooClient, llm: LLMBaseline, names: list[str],
                   input_domain: str, output_domain: str, take: int) -> dict:
    source, target = INPUT_DOMAINS[input_domain], OUTPUT_DOMAINS[output_domain]

    seeds = await resolve_seeds(qloo, names, source["qloo_type"])
    seed_ids = [s["entity"].entity_id for s in seeds]
    seed_labels = [seed_label(s["entity"]) for s in seeds]

    llm_picks = await llm.recommend(seed_labels, source["noun"], target["noun"], target["naming"], take)

    async def candidates_for(pick: dict) -> list[Entity]:
        for name in lookup_names(pick["name"]):
            found = await qloo.find_all_exact(name, target["qloo_type"])
            if found:
                return found
        return []

    candidates = await asyncio.gather(*(candidates_for(p) for p in llm_picks))

    # One call scores every candidate against the same inputs the Qloo side used.
    candidate_ids = list(dict.fromkeys(e.entity_id for group in candidates for e in group))
    scored = await qloo.score(seed_ids, target["qloo_type"], candidate_ids)

    rows, measured, used = [], [], set()
    for pick, group in zip(llm_picks, candidates):
        entity, why_not = choose_match(group, scored, pick["country_code"])
        row = {
            "llm_name": pick["name"],
            "llm_country_code": pick["country_code"],
            "reason": pick["reason"],
            "measured": entity is not None,
            "why_not": why_not,
        }
        if entity:
            row.update(pick_to_dict(entity))
            if entity.entity_id not in used:  # the same place named twice counts once
                used.add(entity.entity_id)
                measured.append(entity)
        rows.append(row)

    qloo_picks = await qloo.recommend(seed_ids, target["qloo_type"], take)

    return {
        "model": llm.model,
        "prompt": build_prompt(seed_labels, source["noun"], target["noun"], target["naming"], take),
        "picks": rows,
        "measured": {"count": sum(1 for r in rows if r["measured"]), "of": len(rows)},
        "metrics": metrics.summarize(measured, include_countries=True),
        "overlap": metrics.overlap(qloo_picks, measured),
    }
