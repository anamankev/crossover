"""Tests for the LLM baseline. The real API is never called."""

import asyncio
import json

import httpx
import pytest

from app.llm import LLMBaseline, LLMError, build_prompt, parse_picks


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


def test_prompt_asks_for_a_year_only_for_films():
    assert '"year"' in build_prompt(["A"], "music artists", "films", "naming", 8, with_year=True)
    assert '"year"' not in build_prompt(["A"], "films", "music artists", "naming", 8, with_year=False)


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

    async def complete(prompt):
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


# ---- the real HTTP call, against a pretend LLM server ---------------

def llm_with_server(handler):
    llm = LLMBaseline("test-key", model="test-model", base_url="https://llm.test/v1")
    llm._http = httpx.AsyncClient(base_url="https://llm.test/v1", transport=httpx.MockTransport(handler),
                                  headers={"Authorization": "Bearer test-key"})
    return llm


def chat_reply(content, finish_reason="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]})


def test_real_call_sends_the_key_model_and_prompt_and_reads_the_answer():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return chat_reply(answer("Nick Drake"))

    llm = llm_with_server(handler)
    picks = asyncio.run(llm.recommend(["Film One"], "films", "music artists", "naming", 8, False))
    assert picks[0]["name"] == "Nick Drake"
    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "test-model"
    assert "Film One" in seen["body"]["messages"][-1]["content"]
    assert seen["body"]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("status, message", [(401, "rejected"), (429, "rate limited"), (500, "HTTP 500")])
def test_real_call_turns_error_statuses_into_clear_messages(status, message):
    llm = llm_with_server(lambda request: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(LLMError, match=message):
        asyncio.run(llm.recommend(["A"], "films", "music artists", "naming", 8, False))


def test_real_call_reports_a_cut_off_answer():
    llm = llm_with_server(lambda request: chat_reply('{"picks": [', finish_reason="length"))
    with pytest.raises(LLMError, match="cut off"):
        asyncio.run(llm.recommend(["A"], "films", "music artists", "naming", 8, False))


def test_real_call_reports_an_unexpected_reply_shape():
    llm = llm_with_server(lambda request: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(LLMError, match="unexpected format"):
        asyncio.run(llm.recommend(["A"], "films", "music artists", "naming", 8, False))
