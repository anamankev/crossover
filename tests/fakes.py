"""A pretend Qloo server for tests. All data here is invented."""

import httpx

from app.qloo import QlooClient

MOVIE = "urn:entity:movie"
ARTIST = "urn:entity:artist"


def artist(entity_id, name, affinity, popularity, genres=()):
    return {
        "entity_id": entity_id,
        "name": name,
        "disambiguation": name,
        "popularity": popularity,
        "properties": {"image": {"url": f"https://img.test/{entity_id}.jpg"}},
        "tags": [{"id": f"urn:tag:genre:music:{g}", "name": g, "type": "urn:tag:genre:music"} for g in genres],
        "query": {"affinity": affinity},
    }


def movie(entity_id, name, year, affinity, popularity, genres=()):
    return {
        "entity_id": entity_id,
        "name": name,
        "disambiguation": str(year),
        "popularity": popularity,
        "properties": {"release_year": year},
        "tags": [{"id": f"urn:tag:genre:media:{g}", "name": g, "type": "urn:tag:genre:media"} for g in genres],
        "query": {"affinity": affinity},
    }


def hit(entity_id, name, disambiguation=None, popularity=0.5):
    """A search result. Search results carry no affinity."""
    return {"entity_id": entity_id, "name": name, "disambiguation": disambiguation or name,
            "popularity": popularity, "properties": {}}


SEARCH = {
    (MOVIE, "film one"): [hit("M1", "Film One", "2001")],
    (MOVIE, "film two"): [hit("M2", "Film Two", "2002")],
    (MOVIE, "film thre"): [hit("M3", "Film Three", "2003")],
    # Two films share this title, thirty years apart.
    (MOVIE, "remake"): [hit("R72", "Remake", "1972"), hit("R02", "Remake", "2002")],
    (ARTIST, "alpha band"): [hit("A1", "Alpha Band")],
    (ARTIST, "beta singer"): [hit("A2", "Beta Singer")],
    (ARTIST, "delta duo"): [hit("A6", "Delta Duo")],
    # Two artists share this name. Search lists the obscure one first.
    (ARTIST, "twins"): [hit("TW_SMALL", "Twins"), hit("TW_BIG", "Twins")],
    # In the catalog, but Qloo will not score it.
    (ARTIST, "ghost act"): [hit("G1", "Ghost Act")],
    # Fuzzy search returns a near-miss for an invented name.
    (ARTIST, "the madeups"): [hit("A9", "Made Up Orchestra")],
}

# Everything the taste graph can score, best first, per result type.
GRAPH = {
    ARTIST: [
        artist("A1", "Alpha Band", 0.90, 0.60, ["folk", "indie"]),
        artist("A3", "Gamma Trio", 0.80, 0.40, ["folk"]),
        artist("TW_BIG", "Twins", 0.70, 0.99, ["jazz"]),
        artist("A2", "Beta Singer", 0.50, 1.00, ["pop"]),
        artist("TW_SMALL", "Twins", 0.30, 0.10, ["pop", "indie"]),
        artist("A6", "Delta Duo", 0.20, 0.30, ["metal"]),
    ],
    MOVIE: [
        movie("R02", "Remake", 2002, 0.85, 0.90, ["drama"]),
        movie("M1", "Film One", 2001, 0.75, 0.80, ["comedy"]),
        movie("R72", "Remake", 1972, 0.65, 0.95, ["drama", "scifi"]),
    ],
}


def handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    if request.url.path == "/search":
        key = (params["types"], params["query"].lower())
        return httpx.Response(200, json={"results": SEARCH.get(key, [])})

    if request.url.path == "/v2/insights":
        seeds = params["signal.interests.entities"].split(",")
        entities = GRAPH[params["filter.type"]]
        if "filter.results.entities" in params:
            wanted = set(params["filter.results.entities"].split(","))
            entities = [e for e in entities if e["entity_id"] in wanted]
        entities = entities[: int(params.get("take", 20))]
        if params.get("feature.explainability") == "true":
            share = 1 / len(seeds)
            entities = [
                {**e, "query": {**e["query"], "explainability": {"signal.interests.entities": [
                    {"entity_id": s, "score": share} for s in seeds]}}}
                for e in entities
            ]
        return httpx.Response(200, json={"success": True, "results": {"entities": entities}})

    return httpx.Response(404, json={"error_msg": "no such path"})


def fake_qloo() -> QlooClient:
    return QlooClient("https://qloo.test", "test-key", transport=httpx.MockTransport(handler), min_interval=0)
