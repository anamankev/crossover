"""The evaluation: numbers that compare two lists of recommendations.

Rules this file follows:

1. Every number is computed from fields Qloo actually returned.
2. A pick with a missing field is left out of that metric, and the
   metric reports how many picks it used ("n") out of how many it was
   given ("of"). Nothing is filled in, averaged over, or guessed.
3. If too few picks have the field, the value is None and the screen
   shows "not enough data" instead of a number.

Everything here is a plain function of its inputs: no network, no
randomness. That is what makes it testable against hand-worked answers.
"""

from itertools import combinations

from app.qloo import Entity

# Only "genre" tags are used for variety. Qloo attaches a hundred other
# tags (instruments, themes, keywords) to some entities and few to
# others, so comparing full tag sets would mostly measure how well
# documented an entity is, not how different two picks are.
GENRE_TAG_PREFIX = "urn:tag:genre"


def _result(value, n, of):
    return {"value": value, "n": n, "of": of}


def mean_affinity(picks: list[Entity]) -> dict:
    """Average Qloo affinity (0..1) of the picks to the user's inputs."""
    scores = [p.affinity for p in picks if p.affinity is not None]
    if not scores:
        return _result(None, 0, len(picks))
    return _result(sum(scores) / len(scores), len(scores), len(picks))


def median_popularity(picks: list[Entity]) -> dict:
    """The middle pick's Qloo popularity percentile (0..1).

    0.999 means the typical pick is more popular than 99.9% of Qloo's
    catalog in its category. The median is used, not the mean, because
    well-known films and artists all sit between 0.99 and 1.0 and one
    obscure pick would drag a mean far from what the list is really like.
    With an even number of picks it is the average of the middle two.
    """
    scores = sorted(p.popularity for p in picks if p.popularity is not None)
    if not scores:
        return _result(None, 0, len(picks))
    middle = len(scores) // 2
    if len(scores) % 2:
        value = scores[middle]
    else:
        value = (scores[middle - 1] + scores[middle]) / 2
    return _result(value, len(scores), len(picks))


def genre_tags(pick: Entity) -> set[str]:
    return {t["id"] for t in pick.tags if t.get("type", "").startswith(GENRE_TAG_PREFIX)}


def tag_variety(picks: list[Entity]) -> dict:
    """How different the picks are from each other, 0..1.

    For every pair of picks: 1 - (shared genre tags / all genre tags of
    the two). That is the Jaccard distance. The result is the average
    over all pairs. 0 means every pick has identical genre tags, 1 means
    no two picks share any.

    Picks with no genre tags are left out. Needs at least two picks.
    """
    tag_sets = [tags for tags in (genre_tags(p) for p in picks) if tags]
    if len(tag_sets) < 2:
        return _result(None, len(tag_sets), len(picks))
    distances = []
    for a, b in combinations(tag_sets, 2):
        distances.append(1 - len(a & b) / len(a | b))
    return _result(sum(distances) / len(distances), len(tag_sets), len(picks))


def overlap(list_a: list[Entity], list_b: list[Entity]) -> dict:
    """How many entities appear in both lists (same Qloo entity id)."""
    ids_a = {p.entity_id for p in list_a}
    ids_b = {p.entity_id for p in list_b}
    shared = ids_a & ids_b
    return {"count": len(shared), "ids": sorted(shared), "of_a": len(ids_a), "of_b": len(ids_b)}


def summarize(picks: list[Entity]) -> dict:
    """All per-list metrics for one side of the comparison."""
    return {
        "affinity": mean_affinity(picks),
        "popularity": median_popularity(picks),
        "variety": tag_variety(picks),
    }
