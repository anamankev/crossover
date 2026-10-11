"""Tests for the Qloo client.

No real network calls. A fake transport answers with made-up data in the
same shape Qloo uses. (Qloo's terms say real response data must not be
stored in a public repo, so nothing here is copied from the API.)
"""

import asyncio

import httpx
import pytest

from app.qloo import QlooClient, QlooError, normalize_name, parse_entity


def make_client(handler):
    return QlooClient("https://qloo.test", "test-key",
                      transport=httpx.MockTransport(handler), min_interval=0)


def fake_entity(entity_id, name, **extra):
    raw = {"entity_id": entity_id, "name": name, "popularity": 0.5, "properties": {}}
    raw.update(extra)
    return raw


# ---- normalize_name ------------------------------------------------

@pytest.mark.parametrize("a, b", [
    ("Cancún", "cancun"),
    ("The Grand Budapest Hotel", "grand budapest hotel"),
    ("  KYOTO ", "Kyoto"),
    ("Tom & Jerry", "Tom and Jerry"),
    ("Amélie!", "amelie"),
])
def test_normalize_name_treats_these_as_equal(a, b):
    assert normalize_name(a) == normalize_name(b)


def test_normalize_name_keeps_different_names_different():
    assert normalize_name("Paris") != normalize_name("Paris Texas")


# ---- parse_entity --------------------------------------------------

def test_parse_entity_reads_the_fields_we_use():
    raw = {
        "entity_id": "E1",
        "name": "Testville",
        "disambiguation": "Testland",
        "popularity": 0.75,
        "properties": {"geocode": {"country_code": "TL"}, "image": {"url": "https://img.test/e1.jpg"}},
        "tags": [{"id": "urn:tag:genre:destination:beaches", "name": "Beaches", "type": "urn:tag:genre:destination"}],
        "query": {
            "affinity": 0.9,
            "explainability": {"signal.interests.entities": [
                {"entity_id": "S1", "score": 0.6}, {"entity_id": "S2", "score": 0.4}]},
        },
    }
    entity = parse_entity(raw)
    assert entity.entity_id == "E1"
    assert entity.popularity == 0.75
    assert entity.affinity == 0.9
    assert entity.country_code == "TL"
    assert entity.image_url == "https://img.test/e1.jpg"
    assert entity.tags[0]["name"] == "Beaches"
    assert entity.contributions == {"S1": 0.6, "S2": 0.4}


def test_parse_entity_leaves_missing_numbers_as_none():
    entity = parse_entity({"entity_id": "E2", "name": "Bare"})
    assert entity.popularity is None
    assert entity.affinity is None
    assert entity.tags == []
    assert entity.country_code is None


# ---- requests ------------------------------------------------------

def test_search_sends_key_in_header_and_type_in_query():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("X-Api-Key")
        seen["params"] = dict(request.url.params)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"results": [fake_entity("E1", "Alpha")]})

    client = make_client(handler)
    results = asyncio.run(client.search("alpha", "urn:entity:movie"))
    assert seen["key"] == "test-key"
    assert seen["path"] == "/search"
    assert seen["params"]["query"] == "alpha"
    assert seen["params"]["types"] == "urn:entity:movie"
    assert [e.name for e in results] == ["Alpha"]


def test_find_exact_rejects_a_fuzzy_hit():
    # Qloo's search returns its nearest guess even for invented names.
    def handler(request):
        return httpx.Response(200, json={"results": [fake_entity("E9", "Hilton Head Island")]})

    client = make_client(handler)
    assert asyncio.run(client.find_exact("Zxqv Flarnbuckle Island", "urn:entity:destination")) is None


def test_find_exact_accepts_a_match_that_differs_only_by_accent_or_case():
    def handler(request):
        return httpx.Response(200, json={"results": [
            fake_entity("E0", "Cancun Bay Resort"), fake_entity("E1", "Cancún")]})

    client = make_client(handler)
    found = asyncio.run(client.find_exact("cancun", "urn:entity:destination"))
    assert found is not None and found.entity_id == "E1"


def test_recommend_passes_seeds_and_asks_for_explainability():
    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"success": True, "results": {"entities": [
            fake_entity("R1", "One", query={"affinity": 0.9}),
            fake_entity("R2", "Two", query={"affinity": 0.8}),
        ]}})

    client = make_client(handler)
    results = asyncio.run(client.recommend(["S1", "S2"], "urn:entity:destination", take=2))
    assert seen["filter.type"] == "urn:entity:destination"
    assert seen["signal.interests.entities"] == "S1,S2"
    assert seen["feature.explainability"] == "true"
    assert [e.affinity for e in results] == [0.9, 0.8]


def test_score_only_returns_the_entities_we_asked_about():
    def handler(request):
        assert request.url.params["filter.results.entities"] == "A,B"
        return httpx.Response(200, json={"success": True, "results": {"entities": [
            fake_entity("A", "Aye", query={"affinity": 0.7}),
            fake_entity("STRAY", "Not asked for", query={"affinity": 0.99}),
        ]}})

    client = make_client(handler)
    scored = asyncio.run(client.score(["S1"], "urn:entity:destination", ["A", "B"]))
    assert set(scored) == {"A"}  # B was not returned, so it is absent, not guessed
    assert scored["A"].affinity == 0.7


def test_score_with_nothing_to_score_makes_no_call():
    def handler(request):
        raise AssertionError("should not call Qloo")

    client = make_client(handler)
    assert asyncio.run(client.score(["S1"], "urn:entity:destination", [])) == {}


def test_repeat_search_is_served_from_cache():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"results": [fake_entity("E1", "Alpha")]})

    client = make_client(handler)

    async def twice():
        await client.search("Alpha", "urn:entity:movie")
        await client.search("alpha", "urn:entity:movie")

    asyncio.run(twice())
    assert len(calls) == 1


def test_rate_limited_call_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error_msg": "Rate limit exceeded"},
                                  headers={"x-second-ratelimit-reset": "0.01"})
        return httpx.Response(200, json={"results": []},
                              headers={"x-month-ratelimit-remaining": "9000"})

    client = make_client(handler)
    assert asyncio.run(client.search("Alpha", "urn:entity:movie")) == []
    assert len(calls) == 2
    assert client.month_remaining == 9000


def test_error_response_raises_with_qloo_message():
    def handler(request):
        return httpx.Response(400, json={"errors": [{"message": "invalid entity ids"}]})

    client = make_client(handler)
    with pytest.raises(QlooError, match="invalid entity ids"):
        asyncio.run(client.recommend(["bad"], "urn:entity:destination", take=3))


def test_network_failure_raises_qloo_error():
    def handler(request):
        raise httpx.ConnectError("no route")

    client = make_client(handler)
    with pytest.raises(QlooError, match="Could not reach Qloo"):
        asyncio.run(client.search("Alpha", "urn:entity:movie"))
