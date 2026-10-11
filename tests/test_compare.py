"""Tests for the two sides of the comparison, run against the pretend
Qloo server in fakes.py and a pretend LLM."""

import asyncio
import json

import pytest

from app import compare
from app.llm import LLMBaseline
from app.qloo import Entity
from tests.fakes import fake_qloo

FILMS = ["Film One", "Film Two"]


def llm_saying(*picks):
    """A pretend LLM. Each pick is a name, or a (name, year) pair."""
    async def complete(prompt):
        rows = []
        for pick in picks:
            name, year = pick if isinstance(pick, tuple) else (pick, None)
            row = {"name": name, "reason": "r"}
            if year is not None:
                row["year"] = year
            rows.append(row)
        return json.dumps({"picks": rows})
    return LLMBaseline("", complete=complete)


# ---- choose_match: films --------------------------------------------

def film(entity_id, year):
    return Entity(entity_id=entity_id, name="Remake", affinity=0.5, year=year)


BOTH_REMAKES = [Entity("R72", "Remake"), Entity("R02", "Remake")]
BOTH_SCORED = {"R72": film("R72", 1972), "R02": film("R02", 2002)}


def test_film_match_uses_the_year_to_choose_between_same_titled_films():
    entity, why_not = compare.choose_match(BOTH_REMAKES, BOTH_SCORED, 2002, needs_year=True)
    assert entity.entity_id == "R02" and why_not is None


def test_film_match_allows_the_year_to_be_one_off():
    entity, _ = compare.choose_match(BOTH_REMAKES, BOTH_SCORED, 1973, needs_year=True)
    assert entity.entity_id == "R72"


def test_film_match_refuses_a_same_titled_film_from_another_year():
    entity, why_not = compare.choose_match(BOTH_REMAKES, BOTH_SCORED, 1990, needs_year=True)
    assert entity is None and "not from 1990" in why_not


def test_film_match_without_a_year_does_not_guess():
    entity, why_not = compare.choose_match(BOTH_REMAKES, BOTH_SCORED, None, needs_year=True)
    assert entity is None and "no year" in why_not


def test_film_match_skips_a_candidate_whose_year_qloo_did_not_give():
    scored = {"R72": film("R72", None)}
    entity, why_not = compare.choose_match([Entity("R72", "Remake")], scored, 1972, needs_year=True)
    assert entity is None and "not from 1972" in why_not


# ---- choose_match: artists and shared cases -------------------------

def test_artist_match_takes_the_most_popular_of_same_named_artists():
    candidates = [Entity("TW_SMALL", "Twins"), Entity("TW_BIG", "Twins")]
    scored = {"TW_SMALL": Entity("TW_SMALL", "Twins", popularity=0.10, affinity=0.3),
              "TW_BIG": Entity("TW_BIG", "Twins", popularity=0.99, affinity=0.7)}
    entity, why_not = compare.choose_match(candidates, scored, None, needs_year=False)
    assert entity.entity_id == "TW_BIG" and why_not is None


def test_match_with_no_candidates():
    entity, why_not = compare.choose_match([], {}, None, needs_year=False)
    assert entity is None and "No exact name match" in why_not


def test_match_when_qloo_returned_no_score():
    entity, why_not = compare.choose_match([Entity("G1", "Ghost Act")], {}, None, needs_year=False)
    assert entity is None and "no score" in why_not


# ---- grounded ------------------------------------------------------

def test_grounded_returns_qloo_picks_in_order_with_metrics_and_drivers():
    result = asyncio.run(compare.grounded(fake_qloo(), FILMS, "movie", "artist", take=3))
    assert [s["label"] for s in result["seeds"]] == ["Film One (2001)", "Film Two (2002)"]
    assert [p["name"] for p in result["picks"]] == ["Alpha Band", "Gamma Trio", "Twins"]
    # (0.90 + 0.80 + 0.70) / 3 = 0.80
    assert result["metrics"]["affinity"]["value"] == pytest.approx(0.80)
    # popularity 0.60, 0.40, 0.99 -> sorted 0.40, 0.60, 0.99 -> middle is 0.60
    assert result["metrics"]["popularity"]["value"] == pytest.approx(0.60)
    drivers = result["picks"][0]["drivers"]
    assert {d["seed"] for d in drivers} == {"Film One", "Film Two"}
    assert sum(d["share"] for d in drivers) == pytest.approx(1.0)
    assert len(result["trace"]) == 3


def test_grounded_works_in_the_other_direction_and_reports_film_years():
    result = asyncio.run(compare.grounded(fake_qloo(), ["Alpha Band"], "artist", "movie", take=2))
    assert [(p["name"], p["year"]) for p in result["picks"]] == [("Remake", 2002), ("Film One", 2001)]


def test_grounded_marks_an_inexact_input_match():
    result = asyncio.run(compare.grounded(fake_qloo(), ["film thre"], "movie", "artist", take=3))
    assert result["seeds"][0]["name"] == "Film Three"
    assert result["seeds"][0]["exact"] is False


def test_grounded_counts_a_repeated_input_once():
    result = asyncio.run(compare.grounded(fake_qloo(), ["Film One", "film one"], "movie", "artist", take=3))
    assert len(result["seeds"]) == 1


def test_grounded_raises_when_an_input_is_not_in_qloo():
    with pytest.raises(compare.InputNotFound):
        asyncio.run(compare.grounded(fake_qloo(), ["No Such Film"], "movie", "artist", take=3))


# ---- baseline ------------------------------------------------------

def run_baseline(*picks, take=3):
    return asyncio.run(compare.baseline(fake_qloo(), llm_saying(*picks), FILMS, "movie", "artist", take))


def test_baseline_scores_matched_picks_with_qloo_affinity():
    result = run_baseline("Alpha Band", "Beta Singer")
    assert result["measured"] == {"count": 2, "of": 2}
    assert [p["affinity"] for p in result["picks"]] == [0.90, 0.50]
    # (0.90 + 0.50) / 2 = 0.70
    assert result["metrics"]["affinity"]["value"] == pytest.approx(0.70)
    # popularity 0.60 and 1.00 -> even count -> (0.60 + 1.00) / 2 = 0.80
    assert result["metrics"]["popularity"]["value"] == pytest.approx(0.80)


def test_baseline_leaves_an_invented_name_out_of_the_numbers():
    result = run_baseline("Alpha Band", "The Madeups")
    assert result["measured"] == {"count": 1, "of": 2}
    invented = result["picks"][1]
    assert invented["measured"] is False
    assert "No exact name match" in invented["why_not"]
    assert "affinity" not in invented
    # The mean is over the one measured pick, not dragged down by a made-up zero.
    assert result["metrics"]["affinity"] == {"value": pytest.approx(0.90), "n": 1, "of": 1}


def test_baseline_reports_a_catalog_entry_qloo_would_not_score():
    result = run_baseline("Ghost Act")
    assert result["picks"][0]["measured"] is False
    assert "no score" in result["picks"][0]["why_not"]
    assert result["metrics"]["affinity"]["value"] is None


def test_baseline_takes_the_famous_one_of_two_same_named_artists():
    result = run_baseline("Twins")
    assert result["picks"][0]["entity_id"] == "TW_BIG"
    assert result["picks"][0]["affinity"] == 0.70


def test_baseline_counts_the_same_artist_once():
    result = run_baseline("Alpha Band", "ALPHA BAND!")
    assert result["measured"]["count"] == 2  # both rows are shown as measured
    assert result["metrics"]["affinity"]["n"] == 1  # but the artist counts once


def test_baseline_overlap_counts_picks_both_sides_chose():
    # Qloo's top 3 are A1, A3, TW_BIG. The LLM names A1 and A2.
    result = run_baseline("Alpha Band", "Beta Singer")
    assert result["overlap"]["count"] == 1
    assert result["overlap"]["ids"] == ["A1"]


def test_baseline_for_films_uses_the_year_the_llm_gave():
    llm = llm_saying(("Remake", 1972), ("Remake", 2002), ("Remake", 1950))
    result = asyncio.run(compare.baseline(fake_qloo(), llm, ["Alpha Band"], "artist", "movie", take=3))
    assert [p.get("entity_id") for p in result["picks"]] == ["R72", "R02", None]
    assert "not from 1950" in result["picks"][2]["why_not"]
    # (0.65 + 0.85) / 2 = 0.75
    assert result["metrics"]["affinity"]["value"] == pytest.approx(0.75)


def test_baseline_reports_the_exact_prompt_and_model():
    result = run_baseline("Alpha Band")
    assert "Film One (2001)" in result["prompt"]
    assert result["model"] == "openai/gpt-oss-120b"
