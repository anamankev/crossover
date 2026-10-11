"""Tests for the web endpoints, with Qloo and the LLM replaced by fakes."""

import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import Agent
from app.llm import LLM, LLMError
from tests.fakes import fake_qloo

BODY = {"input_domain": "movie", "output_domain": "artist", "seeds": ["Film One", "Film Two"]}


def tool_call(name, **arguments):
    return {"content": "", "tool_calls": [{"id": "c", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def working_llm():
    """Baseline answers with two artists. The agent takes the graph's picks."""
    async def complete(prompt):
        return json.dumps({"picks": [{"name": "Alpha Band", "reason": "r"}, {"name": "Beta Singer", "reason": "r"},
                                     {"name": "The Madeups", "reason": "r"}]})

    async def chat(messages, tools):
        if messages[-1]["role"] == "tool":
            ids = ["g1", "g2", "g3", "g4", "g5", "g6"]
            return tool_call("submit_picks", picks=[{"id": i, "reason": "because"} for i in ids])
        return tool_call("get_taste_graph_picks")

    return LLM("", complete=complete, chat=chat)


def install(llm):
    main.app.state.qloo = fake_qloo()
    main.app.state.llm = llm
    main.app.state.agent = Agent(main.app.state.qloo, llm)
    main.app.state.hits = {}
    main.app.state.votes = {choice: 0 for choice in main.VOTE_CHOICES}
    main.app.state.voted = set()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        install(working_llm())
        yield test_client


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Crossover" in response.text


def test_config_lists_domains_and_examples(client):
    data = client.get("/api/config").json()
    assert set(data["domains"]) == {"movie", "artist"}
    assert data["llm_configured"] is True
    assert len(data["examples"]) >= 1


# ---- /api/compare ---------------------------------------------------

def test_compare_returns_both_sides_and_their_overlap(client):
    response = client.post("/api/compare", json=BODY)
    assert response.status_code == 200
    data = response.json()
    assert data["agent"]["agent_ran"] is True
    assert [p["name"] for p in data["agent"]["picks"]] == [
        "Alpha Band", "Gamma Trio", "Twins", "Beta Singer", "Twins", "Delta Duo"]
    assert data["baseline"]["measured"] == {"count": 2, "of": 3}
    assert data["baseline_error"] is None
    # Agent: A1, A3, TW_BIG, A2, TW_SMALL, A6. LLM measured: A1, A2. Shared: A1 and A2.
    assert data["overlap"]["count"] == 2
    assert data["overlap"]["ids"] == ["A1", "A2"]
    assert len(data["comparison_id"]) == 16


def test_compare_gives_the_same_id_for_the_same_inputs_in_any_order(client):
    first = client.post("/api/compare", json=BODY).json()["comparison_id"]
    second = client.post("/api/compare", json={**BODY, "seeds": ["film two", "FILM ONE"]}).json()["comparison_id"]
    assert first == second


def test_compare_without_an_llm_shows_qloo_only_and_no_made_up_baseline(client):
    install(LLM(""))
    data = client.post("/api/compare", json=BODY).json()
    assert data["agent"]["agent_ran"] is False
    assert data["agent"]["picks"][0]["name"] == "Alpha Band"
    assert data["baseline"] is None
    assert "not connected" in data["baseline_error"]
    assert data["overlap"] is None


def test_compare_reports_a_baseline_failure_without_losing_the_agent_side(client):
    async def complete(prompt):
        raise LLMError("The LLM is rate limited right now")

    llm = working_llm()
    llm._complete = complete
    install(llm)
    data = client.post("/api/compare", json=BODY).json()
    assert data["agent"]["agent_ran"] is True
    assert data["baseline"] is None
    assert "rate limited" in data["baseline_error"]


def test_unknown_input_gives_a_readable_404(client):
    response = client.post("/api/compare", json={**BODY, "seeds": ["No Such Film"]})
    assert response.status_code == 404
    assert "No Such Film" in response.json()["detail"]


def test_same_domain_on_both_sides_is_rejected(client):
    assert client.post("/api/compare", json={**BODY, "output_domain": "movie"}).status_code == 400


def test_blank_inputs_are_rejected(client):
    assert client.post("/api/compare", json={**BODY, "seeds": ["  "]}).status_code == 400


def test_too_many_inputs_are_rejected(client):
    assert client.post("/api/compare", json={**BODY, "seeds": ["a", "b", "c", "d"]}).status_code == 422


def test_rate_limit_stops_a_runaway_visitor(client, monkeypatch):
    monkeypatch.setattr(main, "RATE_LIMIT", 2)
    assert client.post("/api/compare", json=BODY).status_code == 200
    assert client.post("/api/compare", json=BODY).status_code == 200
    assert client.post("/api/compare", json=BODY).status_code == 429


# ---- /api/check: the tool for other agents ---------------------------

CHECK = {"input_domain": "movie", "output_domain": "artist", "likes": ["Film One"],
         "candidates": [{"name": "Beta Singer"}, {"name": "The Madeups"}, {"name": "Twins"}]}


def test_check_verifies_and_scores_each_candidate(client):
    data = client.post("/api/check", json=CHECK).json()
    beta, madeups, twins = data["results"]
    assert beta["verified"] is True and beta["fit"] == 0.50 and beta["entity_id"] == "A2"
    assert madeups["verified"] is False and "No exact name match" in madeups["why_not"]
    assert "fit" not in madeups  # no score is invented for something unverified
    assert twins["entity_id"] == "TW_BIG"
    assert data["summary"]["verified"] == 2 and data["summary"]["of"] == 3
    # (0.50 + 0.70) / 2 = 0.60
    assert data["summary"]["affinity"]["value"] == pytest.approx(0.60)


def test_check_suggests_better_fits_the_caller_did_not_already_have(client):
    data = client.post("/api/check", json=CHECK).json()
    names = [b["name"] for b in data["better_fits"]]
    assert names[:2] == ["Alpha Band", "Gamma Trio"]
    assert "Beta Singer" not in names


def test_check_uses_the_year_for_films(client):
    body = {"input_domain": "artist", "output_domain": "movie", "likes": ["Alpha Band"],
            "candidates": [{"name": "Remake", "year": 2002}, {"name": "Remake", "year": 1950}]}
    first, second = client.post("/api/check", json=body).json()["results"]
    assert first["entity_id"] == "R02" and first["matched_year"] == 2002
    assert second["verified"] is False and "not from 1950" in second["why_not"]


def test_check_rejects_an_empty_candidate_list(client):
    assert client.post("/api/check", json={**CHECK, "candidates": []}).status_code == 422


def test_check_works_without_any_llm(client):
    install(LLM(""))
    assert client.post("/api/check", json=CHECK).status_code == 200


# ---- blind votes ----------------------------------------------------

def test_votes_start_at_zero_and_count_up(client):
    assert client.get("/api/votes").json() == {"agent": 0, "llm": 0, "same": 0}
    assert client.post("/api/vote", json={"comparison_id": "abc", "choice": "agent"}).json()["agent"] == 1
    assert client.post("/api/vote", json={"comparison_id": "xyz", "choice": "llm"}).json() == {"agent": 1, "llm": 1, "same": 0}


def test_one_visitor_cannot_vote_twice_on_the_same_comparison(client):
    client.post("/api/vote", json={"comparison_id": "abc", "choice": "agent"})
    again = client.post("/api/vote", json={"comparison_id": "abc", "choice": "agent"}).json()
    assert again["agent"] == 1


def test_an_unknown_vote_choice_is_rejected(client):
    assert client.post("/api/vote", json={"comparison_id": "abc", "choice": "qloo-is-best"}).status_code == 400
