"""Tests for the web endpoints, with Qloo and the LLM replaced by fakes."""

import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import LLMBaseline
from tests.fakes import fake_qloo

BODY = {"input_domain": "movie", "output_domain": "destination", "seeds": ["Film One", "Film Two"]}


async def fake_complete(prompt):
    return json.dumps({"picks": [{"name": "Alphaville", "country_code": "AA", "reason": "r"}]})


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        main.app.state.qloo = fake_qloo()
        main.app.state.llm = LLMBaseline("", complete=fake_complete)
        main.app.state.hits = {}
        yield test_client


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Crossover" in response.text


def test_config_lists_domains_and_examples(client):
    data = client.get("/api/config").json()
    assert "movie" in data["input_domains"]
    assert "destination" in data["output_domains"]
    assert data["llm_configured"] is True
    assert len(data["examples"]) >= 1


def test_grounded_endpoint(client):
    response = client.post("/api/grounded", json=BODY)
    assert response.status_code == 200
    data = response.json()
    assert data["picks"][0]["name"] == "Alphaville"
    assert data["metrics"]["affinity"]["n"] == len(data["picks"])


def test_baseline_endpoint(client):
    response = client.post("/api/baseline", json=BODY)
    assert response.status_code == 200
    assert response.json()["measured"] == {"count": 1, "of": 1}


def test_baseline_says_so_when_no_llm_key_is_configured(client):
    main.app.state.llm = LLMBaseline("")
    response = client.post("/api/baseline", json=BODY)
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_unknown_input_gives_a_readable_404(client):
    response = client.post("/api/grounded", json={**BODY, "seeds": ["No Such Film"]})
    assert response.status_code == 404
    assert "No Such Film" in response.json()["detail"]


def test_same_domain_on_both_sides_is_rejected(client):
    response = client.post("/api/grounded", json={**BODY, "output_domain": "movie"})
    assert response.status_code == 400


def test_blank_inputs_are_rejected(client):
    response = client.post("/api/grounded", json={**BODY, "seeds": ["  "]})
    assert response.status_code == 400


def test_too_many_inputs_are_rejected(client):
    response = client.post("/api/grounded", json={**BODY, "seeds": ["a", "b", "c", "d"]})
    assert response.status_code == 422


def test_rate_limit_stops_a_runaway_visitor(client, monkeypatch):
    monkeypatch.setattr(main, "RATE_LIMIT", 2)
    assert client.post("/api/grounded", json=BODY).status_code == 200
    assert client.post("/api/grounded", json=BODY).status_code == 200
    assert client.post("/api/grounded", json=BODY).status_code == 429
