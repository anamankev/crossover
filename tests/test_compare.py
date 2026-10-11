"""Tests for the two sides of the comparison, run against the pretend
Qloo server in fakes.py and a pretend LLM."""

import asyncio
import json

import pytest

from app import compare
from app.llm import LLMBaseline
from app.qloo import Entity
from tests.fakes import fake_qloo

SEEDS = ["Film One", "Film Two"]


def llm_saying(*picks):
    """A pretend LLM that answers with the given (name, country_code) pairs."""
    async def complete(prompt):
        return json.dumps({"picks": [{"name": n, "country_code": c, "reason": "r"} for n, c in picks]})
    return LLMBaseline("", complete=complete)


# ---- choose_match --------------------------------------------------

def scored_entity(entity_id, country):
    return Entity(entity_id=entity_id, name="Twin City", affinity=0.5, country_code=country)


def test_choose_match_picks_the_candidate_in_the_right_country():
    candidates = [Entity("T_US", "Twin City"), Entity("T_IT", "Twin City")]
    scored = {"T_US": scored_entity("T_US", "US"), "T_IT": scored_entity("T_IT", "IT")}
    entity, why_not = compare.choose_match(candidates, scored, "it")
    assert entity.entity_id == "T_IT" and why_not is None


def test_choose_match_refuses_a_same_name_place_in_another_country():
    candidates = [Entity("T_US", "Twin City")]
    scored = {"T_US": scored_entity("T_US", "US")}
    entity, why_not = compare.choose_match(candidates, scored, "IT")
    assert entity is None and "not in IT" in why_not


def test_choose_match_with_no_candidates():
    entity, why_not = compare.choose_match([], {}, "IT")
    assert entity is None and "No place with exactly this name" in why_not


def test_choose_match_when_qloo_returned_no_score():
    entity, why_not = compare.choose_match([Entity("G1", "Ghostport")], {}, "IT")
    assert entity is None and "no score" in why_not


def test_choose_match_without_a_country_does_not_guess():
    candidates = [Entity("T_US", "Twin City")]
    scored = {"T_US": scored_entity("T_US", "US")}
    entity, why_not = compare.choose_match(candidates, scored, "")
    assert entity is None and "no country" in why_not


# ---- grounded ------------------------------------------------------

def test_grounded_returns_qloo_picks_in_order_with_metrics_and_drivers():
    result = asyncio.run(compare.grounded(fake_qloo(), SEEDS, "movie", "destination", take=3))
    assert [s["name"] for s in result["seeds"]] == ["Film One", "Film Two"]
    assert [p["name"] for p in result["picks"]] == ["Alphaville", "Gammaburg", "Twin City"]
    # (0.90 + 0.80 + 0.70) / 3 = 0.80
    assert result["metrics"]["affinity"]["value"] == pytest.approx(0.80)
    # (0.60 + 0.40 + 0.50) / 3 = 0.50
    assert result["metrics"]["popularity"]["value"] == pytest.approx(0.50)
    assert result["metrics"]["countries"]["value"] == 2  # AA, AA, IT
    drivers = result["picks"][0]["drivers"]
    assert {d["seed"] for d in drivers} == {"Film One", "Film Two"}
    assert sum(d["share"] for d in drivers) == pytest.approx(1.0)
    assert len(result["trace"]) == 3


def test_grounded_marks_an_inexact_input_match():
    result = asyncio.run(compare.grounded(fake_qloo(), ["film thre"], "movie", "destination", take=3))
    assert result["seeds"][0]["name"] == "Film Three"
    assert result["seeds"][0]["exact"] is False


def test_grounded_counts_a_repeated_input_once():
    result = asyncio.run(compare.grounded(fake_qloo(), ["Film One", "film one"], "movie", "destination", take=3))
    assert len(result["seeds"]) == 1


def test_grounded_raises_when_an_input_is_not_in_qloo():
    with pytest.raises(compare.InputNotFound):
        asyncio.run(compare.grounded(fake_qloo(), ["No Such Film"], "movie", "destination", take=3))


# ---- baseline ------------------------------------------------------

def run_baseline(*picks, take=3):
    return asyncio.run(compare.baseline(fake_qloo(), llm_saying(*picks), SEEDS, "movie", "destination", take))


def test_baseline_scores_matched_picks_with_qloo_affinity():
    result = run_baseline(("Alphaville", "AA"), ("Betatown", "BB"))
    assert result["measured"] == {"count": 2, "of": 2}
    assert [p["affinity"] for p in result["picks"]] == [0.90, 0.50]
    # (0.90 + 0.50) / 2 = 0.70
    assert result["metrics"]["affinity"]["value"] == pytest.approx(0.70)
    # (0.60 + 1.00) / 2 = 0.80
    assert result["metrics"]["popularity"]["value"] == pytest.approx(0.80)
    assert result["metrics"]["countries"]["value"] == 2


def test_baseline_leaves_an_invented_place_out_of_the_numbers():
    result = run_baseline(("Alphaville", "AA"), ("Madeupistan", "ZZ"))
    assert result["measured"] == {"count": 1, "of": 2}
    invented = result["picks"][1]
    assert invented["measured"] is False
    assert "No place with exactly this name" in invented["why_not"]
    assert "affinity" not in invented
    # Mean is over the one measured pick, not dragged down by a made-up zero.
    assert result["metrics"]["affinity"] == {"value": pytest.approx(0.90), "n": 1, "of": 1}


def test_baseline_uses_the_country_to_choose_between_same_named_places():
    result = run_baseline(("Twin City", "IT"))
    pick = result["picks"][0]
    assert pick["entity_id"] == "T_IT"  # not T_US, which search lists first
    assert pick["affinity"] == 0.70


def test_baseline_does_not_measure_a_same_named_place_in_the_wrong_country():
    result = run_baseline(("Alphaville", "FR"))
    assert result["measured"]["count"] == 0
    assert "not in FR" in result["picks"][0]["why_not"]
    assert result["metrics"]["affinity"]["value"] is None


def test_baseline_reports_a_catalog_entry_qloo_would_not_score():
    result = run_baseline(("Ghostport", "AA"))
    assert result["picks"][0]["measured"] is False
    assert "no score" in result["picks"][0]["why_not"]


def test_baseline_handles_city_comma_country_names():
    result = run_baseline(("Betatown, Betaland", "BB"))
    assert result["picks"][0]["entity_id"] == "D2"


def test_baseline_counts_the_same_place_once():
    result = run_baseline(("Alphaville", "AA"), ("Alphaville, Alphaland", "AA"))
    assert result["measured"]["count"] == 2  # both rows are shown as measured
    assert result["metrics"]["affinity"]["n"] == 1  # but the place counts once


def test_baseline_overlap_counts_picks_both_sides_chose():
    # Qloo's top 3 are D1, D3, T_IT. The LLM names D1 and D2.
    result = run_baseline(("Alphaville", "AA"), ("Betatown", "BB"))
    assert result["overlap"]["count"] == 1
    assert result["overlap"]["ids"] == ["D1"]


def test_baseline_reports_the_exact_prompt_and_model():
    result = run_baseline(("Alphaville", "AA"))
    assert "Film One (2001)" in result["prompt"]
    assert result["model"] == "claude-opus-5-5"
