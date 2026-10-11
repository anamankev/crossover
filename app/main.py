"""Crossover web app. Start it with:  uvicorn app.main:app --reload"""

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import compare, config, metrics
from app.agent import Agent
from app.domains import DOMAINS, allowed_pair
from app.llm import LLM, LLMError
from app.qloo import QlooClient, QlooError

STATIC_DIR = Path(__file__).parent.parent / "static"

# Render's free plan puts the app to sleep after 15 quiet minutes.
KEEPALIVE_SECONDS = 10 * 60

# Each visitor may make this many requests per window, per endpoint, to
# protect the Qloo and LLM quotas from one runaway browser tab or script.
RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 10 * 60

EXAMPLES = [
    {"label": "Amélie · Before Sunrise · Call Me by Your Name",
     "input_domain": "movie", "output_domain": "artist",
     "seeds": ["Amélie", "Before Sunrise", "Call Me by Your Name"]},
    {"label": "Mad Max: Fury Road · Blade Runner 2049 · Dune",
     "input_domain": "movie", "output_domain": "artist",
     "seeds": ["Mad Max: Fury Road", "Blade Runner 2049", "Dune"]},
    {"label": "Radiohead · Björk · Frank Ocean",
     "input_domain": "artist", "output_domain": "movie",
     "seeds": ["Radiohead", "Björk", "Frank Ocean"]},
    {"label": "Johnny Cash · Dolly Parton · Willie Nelson",
     "input_domain": "artist", "output_domain": "movie",
     "seeds": ["Johnny Cash", "Dolly Parton", "Willie Nelson"]},
]

VOTE_CHOICES = ("agent", "llm", "same")


async def keepalive():
    """Ping our own public address so Render does not put the app to sleep."""
    if not config.PUBLIC_URL:
        return  # running locally
    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            try:
                await client.get(f"{config.PUBLIC_URL}/healthz")
            except httpx.HTTPError:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.qloo = QlooClient(config.QLOO_API_URL, config.QLOO_API_KEY)
    app.state.llm = LLM(config.LLM_API_KEY, config.LLM_MODELS, config.LLM_BASE_URL)
    app.state.agent = Agent(app.state.qloo, app.state.llm)
    app.state.hits = {}
    app.state.votes = {choice: 0 for choice in VOTE_CHOICES}
    app.state.voted = set()
    task = asyncio.create_task(keepalive())
    yield
    task.cancel()
    await app.state.qloo.close()
    await app.state.llm.close()


app = FastAPI(
    title="Crossover",
    description="A taste check for AI agents, built on Qloo. POST /api/check verifies and scores "
                "any list of recommendations against Qloo's taste graph.",
    lifespan=lifespan,
)


# ---- request shapes ---------------------------------------------------

class CompareRequest(BaseModel):
    input_domain: str
    output_domain: str
    seeds: list[str] = Field(min_length=1, max_length=3)


class Candidate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    year: int | None = None


class CheckRequest(BaseModel):
    input_domain: str = Field(description='"movie" or "artist": what the person likes')
    output_domain: str = Field(description='"artist" or "movie": what is being recommended')
    likes: list[str] = Field(min_length=1, max_length=5)
    candidates: list[Candidate] = Field(min_length=1, max_length=20)


class VoteRequest(BaseModel):
    comparison_id: str = Field(min_length=1, max_length=64)
    choice: str


# ---- helpers ----------------------------------------------------------

def clean_names(input_domain: str, output_domain: str, names: list[str]) -> list[str]:
    if not allowed_pair(input_domain, output_domain):
        raise HTTPException(400, "Choose films to music, or music to films.")
    cleaned = [n.strip()[:100] for n in names if n.strip()]
    if not cleaned:
        raise HTTPException(400, "Enter at least one thing you love.")
    return cleaned


def visitor_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def check_rate_limit(request: Request, endpoint: str):
    visitor = (visitor_address(request), endpoint)  # each endpoint is counted separately
    now = time.monotonic()
    hits = request.app.state.hits
    recent = [t for t in hits.get(visitor, []) if now - t < RATE_WINDOW_SECONDS]
    if len(recent) >= RATE_LIMIT:
        raise HTTPException(429, "That is a lot of requests. Give it a few minutes and try again.")
    recent.append(now)
    hits[visitor] = recent


def not_found(exc: compare.InputNotFound, input_domain: str) -> HTTPException:
    return HTTPException(404, f'Qloo has nothing called "{exc}" in {DOMAINS[input_domain]["label"]}.')


# ---- endpoints --------------------------------------------------------

@app.get("/healthz")
def healthz():
    # Used by Render's health check and the keepalive ping.
    # Must stay cheap: it never calls Qloo or the LLM.
    return {"ok": True}


@app.get("/api/config")
def api_config(request: Request):
    llm = request.app.state.llm
    return {
        "domains": {k: {"label": v["label"], "noun": v["noun"]} for k, v in DOMAINS.items()},
        "examples": EXAMPLES,
        "take": config.TAKE,
        "repo_url": config.REPO_URL,
        "llm_configured": llm.configured,
        "llm_model": llm.model,
    }


@app.post("/api/compare")
async def api_compare(body: CompareRequest, request: Request):
    """Run the Qloo-grounded agent and the plain LLM on the same inputs."""
    names = clean_names(body.input_domain, body.output_domain, body.seeds)
    check_rate_limit(request, "compare")
    state = request.app.state

    async def run_baseline():
        if not state.llm.configured:
            raise LLMError("The plain-LLM baseline is not connected on this server")
        return await compare.baseline(state.qloo, state.llm, names,
                                      body.input_domain, body.output_domain, config.TAKE)

    agent_result, baseline_result = await asyncio.gather(
        state.agent.recommend(names, body.input_domain, body.output_domain, config.TAKE),
        run_baseline(),
        return_exceptions=True,
    )

    # Without the agent's side there is nothing to show.
    if isinstance(agent_result, compare.InputNotFound):
        raise not_found(agent_result, body.input_domain)
    if isinstance(agent_result, QlooError):
        raise HTTPException(502, str(agent_result))
    if isinstance(agent_result, BaseException):
        raise agent_result

    # The baseline may fail on its own. Say so; never fill it in.
    baseline_error = None
    if isinstance(baseline_result, (LLMError, QlooError)):
        baseline_result, baseline_error = None, str(baseline_result)
    elif isinstance(baseline_result, BaseException):
        raise baseline_result

    overlap = None
    if baseline_result:
        overlap = metrics.overlap(
            [p["entity_id"] for p in agent_result["picks"]],
            [p["entity_id"] for p in baseline_result["picks"] if p["measured"]],
        )

    seed_ids = sorted(s["entity_id"] for s in agent_result["seeds"])
    comparison_id = hashlib.sha1(f"{body.output_domain}|{'|'.join(seed_ids)}".encode()).hexdigest()[:16]
    return {
        "comparison_id": comparison_id,
        "agent": agent_result,
        "baseline": baseline_result,
        "baseline_error": baseline_error,
        "overlap": overlap,
    }


@app.post("/api/check")
async def api_check(body: CheckRequest, request: Request):
    """The taste check, as a tool for other agents.

    Send what a person likes and the recommendations your agent wants to
    make. For each recommendation you get back whether Qloo can verify it
    exists and how well it fits (Qloo affinity, 0 to 1), plus better-fitting
    alternatives from the taste graph.
    """
    likes = clean_names(body.input_domain, body.output_domain, body.likes)
    check_rate_limit(request, "check")
    qloo = request.app.state.qloo
    try:
        seeds = await compare.resolve_seeds(qloo, likes, DOMAINS[body.input_domain]["qloo_type"])
        seed_ids = [s["entity"].entity_id for s in seeds]
        candidates = [{"name": c.name.strip(), "year": c.year} for c in body.candidates]
        checked = await compare.check_candidates(qloo, seed_ids, body.output_domain, candidates)
        top = await qloo.recommend(seed_ids, DOMAINS[body.output_domain]["qloo_type"], config.TAKE)
    except compare.InputNotFound as exc:
        raise not_found(exc, body.input_domain)
    except QlooError as exc:
        raise HTTPException(502, str(exc))

    results, verified = [], []
    for item in checked:
        entity = item["entity"]
        row = {"name": item["name"], "year": item["year"], "verified": entity is not None,
               "why_not": item["why_not"]}
        if entity:
            row.update({"matched_name": entity.name, "matched_year": entity.year,
                        "entity_id": entity.entity_id, "fit": entity.affinity,
                        "popularity": entity.popularity})
            verified.append(entity)
        results.append(row)

    already = {e.entity_id for e in verified}
    return {
        "likes": compare.seeds_to_list(seeds),
        "results": results,
        "summary": {"verified": len(verified), "of": len(results), **metrics.summarize(verified)},
        "better_fits": [{"name": e.name, "year": e.year, "entity_id": e.entity_id, "fit": e.affinity}
                        for e in top if e.entity_id not in already],
    }


@app.post("/api/vote")
def api_vote(body: VoteRequest, request: Request):
    """Record one blind preference. One vote per visitor per comparison."""
    if body.choice not in VOTE_CHOICES:
        raise HTTPException(400, "Unknown choice.")
    state = request.app.state
    voter = (visitor_address(request), body.comparison_id)
    if voter not in state.voted:
        state.voted.add(voter)
        state.votes[body.choice] += 1
        # Also written to the server log, so votes can be recounted after a restart.
        print(f"VOTE comparison={body.comparison_id} choice={body.choice}", flush=True)
    return state.votes


@app.get("/api/votes")
def api_votes(request: Request):
    return request.app.state.votes


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
