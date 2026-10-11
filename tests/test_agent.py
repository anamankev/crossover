"""Tests for the tool-using agent. Qloo is the pretend server in fakes.py
and the LLM is a script: a fixed list of replies it gives in order."""

import asyncio
import json

import pytest

from app.agent import Agent, build_system_prompt, build_tools
from app.llm import LLM, LLMError
from tests.fakes import fake_qloo

FILMS = ["Film One", "Film Two"]


def call(name, **arguments):
    """An assistant message asking to use one tool."""
    return {"content": "", "tool_calls": [
        {"id": f"call_{name}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def scripted_llm(*replies):
    """A pretend LLM that gives `replies` in order and records what it was sent."""
    seen = []
    queue = list(replies)

    async def chat(messages, tools):
        seen.append([dict(m) for m in messages])
        if not queue:
            raise LLMError("script ran out")
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    llm = LLM("", models=["test-model"], chat=chat)
    llm.seen = seen
    return llm


def run(llm, names=FILMS, take=3, input_domain="movie", output_domain="artist"):
    agent = Agent(fake_qloo(), llm)
    return asyncio.run(agent.recommend(names, input_domain, output_domain, take))


def picks(*pairs):
    return call("submit_picks", picks=[{"id": i, "reason": r} for i, r in pairs])


# ---- the normal path -----------------------------------------------

def test_agent_uses_the_graph_then_submits():
    llm = scripted_llm(
        call("get_taste_graph_picks", how_many=5),
        picks(("g1", "first"), ("g2", "second"), ("g3", "third")),
    )
    result = run(llm)
    assert result["agent_ran"] is True
    assert result["model"] == "test-model"
    assert result["llm_calls"] == 2
    assert [p["name"] for p in result["picks"]] == ["Alpha Band", "Gamma Trio", "Twins"]
    assert [p["reason"] for p in result["picks"]] == ["first", "second", "third"]
    assert {p["source"] for p in result["picks"]} == {"graph"}
    # (0.90 + 0.80 + 0.70) / 3 = 0.80
    assert result["metrics"]["affinity"]["value"] == pytest.approx(0.80)
    assert [s["kind"] for s in result["trace"]] == ["seeds", "graph", "submit"]


def test_tool_results_are_sent_back_to_the_model():
    llm = scripted_llm(call("get_taste_graph_picks"), picks(("g1", "r")))
    run(llm)
    second_request = llm.seen[1]
    tool_message = second_request[-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_get_taste_graph_picks"
    assert "g1 | Alpha Band | fit 0.900" in tool_message["content"]


def test_agent_can_keep_one_of_its_own_ideas_once_qloo_verified_it():
    # The graph is asked for 5, so Delta Duo (6th by fit) is not in its list.
    llm = scripted_llm(
        call("get_taste_graph_picks", how_many=5),
        call("check_my_ideas", ideas=[{"name": "Delta Duo"}, {"name": "The Madeups"}, {"name": "Ghost Act"}]),
        picks(("g1", "a"), ("g2", "b"), ("m1", "my own")),
    )
    result = run(llm)
    assert [p["name"] for p in result["picks"]] == ["Alpha Band", "Gamma Trio", "Delta Duo"]
    assert result["picks"][2]["source"] == "own"
    assert result["picks"][2]["affinity"] == 0.20
    check = next(s for s in result["trace"] if s["kind"] == "check")
    by_name = {item["name"]: item for item in check["items"]}
    assert by_name["Delta Duo"]["fit"] == 0.20 and by_name["Delta Duo"]["kept"] is True
    assert by_name["The Madeups"]["fit"] is None and "No exact name match" in by_name["The Madeups"]["why_not"]
    assert by_name["Ghost Act"]["fit"] is None and by_name["Ghost Act"]["kept"] is False
    assert "1 of its own ideas" in result["trace"][-1]["detail"]


def test_an_idea_already_in_the_graph_list_keeps_its_graph_id():
    llm = scripted_llm(
        call("get_taste_graph_picks"),
        call("check_my_ideas", ideas=[{"name": "Alpha Band"}]),
        picks(("g1", "a")),
    )
    result = run(llm, take=1)
    tool_output = llm.seen[2][-1]["content"]
    assert tool_output.startswith("g1 | Alpha Band") and "already in the graph's list" in tool_output
    assert result["picks"][0]["source"] == "graph"


def test_film_ideas_are_checked_with_their_year():
    llm = scripted_llm(
        call("get_taste_graph_picks"),
        call("check_my_ideas", ideas=[{"name": "Remake", "year": 1950}, {"name": "Remake", "year": 1972}]),
        picks(("g1", "a"), ("g3", "b")),
    )
    result = run(llm, names=["Alpha Band"], input_domain="artist", output_domain="movie", take=2)
    check = next(s for s in result["trace"] if s["kind"] == "check")
    assert "not from 1950" in check["items"][0]["why_not"]
    assert check["items"][1]["fit"] == 0.65


# ---- the hard rule: only verified picks -----------------------------

def test_picks_qloo_never_verified_are_discarded():
    llm = scripted_llm(
        call("get_taste_graph_picks"),
        picks(("g1", "real"), ("Hans Zimmer", "made up id"), ("g99", "no such id"), ("g2", "real")),
    )
    result = run(llm, take=2)
    assert [p["name"] for p in result["picks"]] == ["Alpha Band", "Gamma Trio"]
    assert "2 submitted pick(s) were discarded" in result["trace"][-1]["detail"]


def test_a_short_answer_is_topped_up_from_the_graph_and_the_trace_says_so():
    llm = scripted_llm(call("get_taste_graph_picks"), picks(("g2", "only one")))
    result = run(llm, take=3)
    assert [p["name"] for p in result["picks"]] == ["Gamma Trio", "Alpha Band", "Twins"]
    assert result["picks"][1]["reason"] == ""  # the model wrote no reason, so none is shown
    assert "2 added from the graph's list" in result["trace"][-1]["detail"]


def test_a_repeated_pick_counts_once():
    llm = scripted_llm(call("get_taste_graph_picks"), picks(("g1", "a"), ("g1", "again"), ("g2", "b")))
    result = run(llm, take=2)
    assert [p["name"] for p in result["picks"]] == ["Alpha Band", "Gamma Trio"]


def test_submitting_only_unverified_picks_is_refused_and_the_model_can_try_again():
    llm = scripted_llm(
        call("get_taste_graph_picks"),
        picks(("nope", "x")),
        picks(("g1", "ok")),
    )
    result = run(llm, take=1)
    assert result["picks"][0]["name"] == "Alpha Band"
    assert "None of those ids came from a tool result" in llm.seen[2][-1]["content"]


# ---- when things go wrong -------------------------------------------

def test_a_model_that_only_talks_is_nudged_to_use_the_tools():
    llm = scripted_llm(
        {"content": "I think they would like jazz."},
        call("get_taste_graph_picks"),
        picks(("g1", "a")),
    )
    result = run(llm, take=1)
    assert result["agent_ran"] is True
    assert "call submit_picks" in llm.seen[1][-1]["content"]


def test_bad_tool_arguments_get_an_error_message_not_a_crash():
    bad = {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                          "function": {"name": "check_my_ideas", "arguments": "{not json"}}]}
    llm = scripted_llm(bad, call("get_taste_graph_picks"), picks(("g1", "a")))
    result = run(llm, take=1)
    assert result["agent_ran"] is True
    assert "Could not read the arguments" in llm.seen[1][-1]["content"]


def test_an_unknown_tool_name_gets_an_error_message():
    llm = scripted_llm(call("delete_everything"), call("get_taste_graph_picks"), picks(("g1", "a")))
    run(llm, take=1)
    assert "no tool called delete_everything" in llm.seen[1][-1]["content"]


def test_if_the_llm_fails_the_result_is_qloos_direct_answer_and_says_so():
    llm = scripted_llm(LLMError("The LLM is rate limited right now"))
    result = run(llm)
    assert result["agent_ran"] is False
    assert result["model"] is None
    assert "rate limited" in result["note"] and "no LLM involved" in result["note"]
    assert [p["name"] for p in result["picks"]] == ["Alpha Band", "Gamma Trio", "Twins"]
    assert all(p["reason"] == "" for p in result["picks"])  # no invented reasons


def test_without_an_llm_key_the_result_is_qloos_direct_answer():
    result = asyncio.run(Agent(fake_qloo(), LLM("")).recommend(FILMS, "movie", "artist", 3))
    assert result["agent_ran"] is False
    assert "No LLM API key" in result["note"]


def test_the_next_model_is_tried_when_the_first_fails():
    attempts = []

    async def chat(messages, tools):
        attempts.append(len(messages))
        if len(attempts) == 1:
            raise LLMError("first model is rate limited")
        return call("get_taste_graph_picks") if len(attempts) == 2 else picks(("g1", "a"))

    llm = LLM("", models=["model-a", "model-b"], chat=chat)
    result = asyncio.run(Agent(fake_qloo(), llm).recommend(FILMS, "movie", "artist", 1))
    assert result["agent_ran"] is True
    assert result["model"] == "model-b"


def test_a_model_that_never_submits_is_stopped():
    llm = scripted_llm(*[call("get_taste_graph_picks")] * 10)
    result = run(llm)
    assert result["agent_ran"] is False
    assert "turn limit" in result["note"]


def test_a_finished_run_is_cached_but_a_failed_one_is_not():
    llm = scripted_llm(LLMError("down"), call("get_taste_graph_picks"), picks(("g1", "a")))
    agent = Agent(fake_qloo(), llm)

    async def three_times():
        first = await agent.recommend(FILMS, "movie", "artist", 1)
        second = await agent.recommend(FILMS, "movie", "artist", 1)
        third = await agent.recommend(FILMS, "movie", "artist", 1)
        return first, second, third

    first, second, third = asyncio.run(three_times())
    assert first["agent_ran"] is False
    assert second["agent_ran"] is True
    assert third is second  # served from the cache, the script was not asked again


# ---- what the model is told ----------------------------------------

def test_the_prompt_states_the_rule_and_the_count():
    prompt = build_system_prompt("music artists", 8)
    assert "exactly 8" in prompt
    assert "Never recommend anything Qloo has not checked" in prompt


def test_ideas_need_a_year_only_for_films():
    def idea_fields(tools):
        check = next(t for t in tools if t["function"]["name"] == "check_my_ideas")
        return check["function"]["parameters"]["properties"]["ideas"]["items"]["required"]

    assert idea_fields(build_tools("films", needs_year=True)) == ["name", "year"]
    assert idea_fields(build_tools("music artists", needs_year=False)) == ["name"]
