"""A pretend Qloo server for tests. All data here is invented."""

import httpx

from app.qloo import QlooClient

MOVIE = "urn:entity:movie"
DEST = "urn:entity:destination"


def dest(entity_id, name, country, affinity, popularity, genres=()):
    return {
        "entity_id": entity_id,
        "name": name,
        "disambiguation": name,
        "popularity": popularity,
        "properties": {"geocode": {"country_code": country, "admin1_region": f"{name} Region"}},
        "tags": [{"id": f"urn:tag:genre:destination:{g}", "name": g, "type": "urn:tag:genre:destination"}
                 for g in genres],
        "query": {"affinity": affinity},
    }


# What a search for each name returns (search results carry no country or affinity).
SEARCH = {
    (MOVIE, "film one"): [{"entity_id": "M1", "name": "Film One", "disambiguation": "2001", "popularity": 0.9}],
    (MOVIE, "film two"): [{"entity_id": "M2", "name": "Film Two", "disambiguation": "2002", "popularity": 0.8}],
    (MOVIE, "film thre"): [{"entity_id": "M3", "name": "Film Three", "disambiguation": "2003", "popularity": 0.7}],
    (DEST, "alphaville"): [{"entity_id": "D1", "name": "Alphaville", "popularity": 0.5}],
    (DEST, "betatown"): [{"entity_id": "D2", "name": "Betatown", "popularity": 0.5}],
    # Two real places share this name, in different countries.
    (DEST, "twin city"): [{"entity_id": "T_US", "name": "Twin City", "popularity": 0.9},
                          {"entity_id": "T_IT", "name": "Twin City", "popularity": 0.4}],
    # In the catalog, but Qloo will not score it.
    (DEST, "ghostport"): [{"entity_id": "G1", "name": "Ghostport", "popularity": 0.6}],
    # Fuzzy search returns a near-miss for an invented name.
    (DEST, "madeupistan"): [{"entity_id": "D9", "name": "Made Upon Avon", "popularity": 0.3}],
}

# Everything the taste graph can score, best first.
GRAPH = [
    dest("D1", "Alphaville", "AA", 0.90, 0.60, ["beaches", "food"]),
    dest("D3", "Gammaburg", "AA", 0.80, 0.40, ["beaches"]),
    dest("T_IT", "Twin City", "IT", 0.70, 0.50, ["history"]),
    dest("D2", "Betatown", "BB", 0.50, 1.00, ["shopping"]),
    dest("T_US", "Twin City", "US", 0.30, 0.90, ["shopping", "food"]),
]


def handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    if request.url.path == "/search":
        key = (params["types"], params["query"].lower())
        return httpx.Response(200, json={"results": SEARCH.get(key, [])})

    if request.url.path == "/v2/insights":
        seeds = params["signal.interests.entities"].split(",")
        entities = GRAPH
        if "filter.results.entities" in params:
            wanted = set(params["filter.results.entities"].split(","))
            entities = [e for e in GRAPH if e["entity_id"] in wanted]
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
