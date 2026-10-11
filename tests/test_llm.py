"""Tests for the LLM baseline. The real API is never called."""

import asyncio
import json

import pytest

from app.llm import LLMBaseline, LLMError, build_prompt, parse_picks, picks_schema


def answer(*names, year=None):
    rows = []
    for name in names:
        row = {"name": name, "reason": f"because {name}"}
        if year is not None:
            row["year"] = year
        rows.append(row)
    return json.dumps({"picks": rows})


def test_prompt_contains_the_inputs_and_the_count_and_nothing_about_qloo():
    prompt = build_prompt(["Film One (2001)", "Film Two (2002)"], "films", "music artists",
                          "Give each artist or band name only.", take=8, with_year=False)
    assert "Film One (2001)" in prompt and "Film Two (2002)" in prompt
    assert "exactly 8 music artists" in prompt
    assert "qloo" not in prompt.lower()
    # The baseline is not nudged toward famous or obscure picks.
    for word in ("popular", "famous", "obscure", "hidden", "mainstream"):
        assert word not in prompt.lower()


def test_prompt_and_schema_ask_for_a_year_only_for_films():
    assert '"year"' in build_prompt(["A"], "music artists", "films", "naming", 8, with_year=True)
    assert '"year"' not in build_prompt(["A"], "films", "music artists", "naming", 8, with_year=False)
    assert "year" in picks_schema(True)["properties"]["picks"]["items"]["required"]
    assert "year" not in picks_schema(False)["properties"]["picks"]["items"]["properties"]


def test_parse_picks_reads_names_years_and_reasons():
    picks = parse_picks(answer("Solaris", "Stalker", year=1972), take=8)
    assert [p["name"] for p in picks] == ["Solaris", "Stalker"]
    assert picks[0]["year"] == 1972
    assert picks[0]["reason"] == "because Solaris"


def test_parse_picks_year_is_none_when_missing_or_not_a_number():
    assert parse_picks(answer("Lisbon"), take=8)[0]["year"] is None
    text = json.dumps({"picks": [{"name": "X", "year": "1999", "reason": ""}]})
    assert parse_picks(text, take=8)[0]["year"] is None


def test_parse_picks_drops_duplicates_and_blanks_and_cuts_to_take():
    picks = parse_picks(answer("Lisbon", "lisbon", "  ", "Kyoto", "Oslo"), take=2)
    assert [p["name"] for p in picks] == ["Lisbon", "Kyoto"]


def test_parse_picks_keeps_same_titled_films_from_different_years():
    text = json.dumps({"picks": [{"name": "Solaris", "year": 1972, "reason": ""},
                                 {"name": "Solaris", "year": 2002, "reason": ""}]})
    assert len(parse_picks(text, take=8)) == 2


@pytest.mark.parametrize("bad", ["not json", "[]", '{"picks": "Lisbon"}', '{"picks": []}', '{"other": 1}'])
def test_parse_picks_rejects_answers_in_the_wrong_shape(bad):
    with pytest.raises(LLMError):
        parse_picks(bad, take=8)


def test_recommend_uses_the_llm_once_then_the_cache():
    calls = []

    async def complete(prompt, schema):
        calls.append(prompt)
        return answer("Lisbon")

    llm = LLMBaseline("", complete=complete)

    async def twice():
        first = await llm.recommend(["A", "B"], "films", "music artists", "naming", 8, False)
        second = await llm.recommend(["B", "A"], "films", "music artists", "naming", 8, False)
        return first, second

    first, second = asyncio.run(twice())
    assert first == second
    assert len(calls) == 1


def test_recommend_without_a_key_fails_clearly_instead_of_inventing_picks():
    llm = LLMBaseline("")
    assert llm.configured is False
    with pytest.raises(LLMError, match="No LLM API key"):
        asyncio.run(llm.recommend(["A"], "films", "music artists", "naming", 8, False))
