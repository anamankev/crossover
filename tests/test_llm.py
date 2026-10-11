"""Tests for the LLM baseline. The real API is never called."""

import asyncio
import json

import pytest

from app.llm import LLMBaseline, LLMError, build_prompt, parse_picks


def answer(*names):
    return json.dumps({"picks": [{"name": n, "country_code": "xx", "reason": f"because {n}"} for n in names]})


def test_prompt_contains_the_inputs_and_the_count_and_nothing_about_qloo():
    prompt = build_prompt(["Film One (2001)", "Film Two (2002)"], "films", "travel destinations",
                          "Give the city name only.", take=8)
    assert "Film One (2001)" in prompt and "Film Two (2002)" in prompt
    assert "exactly 8 travel destinations" in prompt
    assert "qloo" not in prompt.lower()
    # The baseline is not nudged toward famous or obscure picks.
    for word in ("popular", "famous", "obscure", "hidden", "mainstream"):
        assert word not in prompt.lower()


def test_parse_picks_reads_names_countries_and_reasons():
    picks = parse_picks(answer("Lisbon", "Kyoto"), take=8)
    assert [p["name"] for p in picks] == ["Lisbon", "Kyoto"]
    assert picks[0]["country_code"] == "XX"  # upper-cased
    assert picks[0]["reason"] == "because Lisbon"


def test_parse_picks_drops_duplicates_and_blanks_and_cuts_to_take():
    picks = parse_picks(answer("Lisbon", "lisbon", "  ", "Kyoto", "Oslo"), take=2)
    assert [p["name"] for p in picks] == ["Lisbon", "Kyoto"]


@pytest.mark.parametrize("bad", ["not json", "[]", '{"picks": "Lisbon"}', '{"picks": []}', '{"other": 1}'])
def test_parse_picks_rejects_answers_in_the_wrong_shape(bad):
    with pytest.raises(LLMError):
        parse_picks(bad, take=8)


def test_recommend_uses_the_llm_once_then_the_cache():
    calls = []

    async def complete(prompt):
        calls.append(prompt)
        return answer("Lisbon")

    llm = LLMBaseline("", complete=complete)

    async def twice():
        first = await llm.recommend(["A", "B"], "films", "travel destinations", "naming", 8)
        second = await llm.recommend(["B", "A"], "films", "travel destinations", "naming", 8)
        return first, second

    first, second = asyncio.run(twice())
    assert first == second
    assert len(calls) == 1


def test_recommend_without_a_key_fails_clearly_instead_of_inventing_picks():
    llm = LLMBaseline("")
    assert llm.configured is False
    with pytest.raises(LLMError, match="No LLM API key"):
        asyncio.run(llm.recommend(["A"], "films", "travel destinations", "naming", 8))
