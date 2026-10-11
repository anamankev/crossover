"""Crossover web app. Start it with:  uvicorn app.main:app --reload"""

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import compare, config
from app.domains import DOMAINS, allowed_pair
from app.llm import LLMBaseline, LLMError
from app.qloo import QlooClient, QlooError

STATIC_DIR = Path(__file__).parent.parent / "static"

# Render's free plan puts the app to sleep after 15 quiet minutes.
KEEPALIVE_SECONDS = 10 * 60

# Each visitor may run this many comparisons per window, to protect the
# monthly Qloo quota from one runaway browser tab.
RATE_LIMIT = 30
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
    app.state.llm = LLMBaseline(config.LLM_API_KEY, config.LLM_MODEL)
    app.state.hits = {}
    task = asyncio.create_task(keepalive())
    yield
    task.cancel()
    await app.state.qloo.close()


app = FastAPI(title="Crossover", lifespan=lifespan)


class CompareRequest(BaseModel):
    input_domain: str
    output_domain: str
    seeds: list[str] = Field(min_length=1, max_length=3)


def clean_request(body: CompareRequest) -> list[str]:
    if not allowed_pair(body.input_domain, body.output_domain):
        raise HTTPException(400, "Choose films to music, or music to films.")
    names = [s.strip()[:100] for s in body.seeds if s.strip()]
    if not names:
        raise HTTPException(400, "Enter at least one thing you love.")
    return names


def check_rate_limit(request: Request):
    forwarded = request.headers.get("x-forwarded-for", "")
    visitor = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    now = time.monotonic()
    hits = request.app.state.hits
    recent = [t for t in hits.get(visitor, []) if now - t < RATE_WINDOW_SECONDS]
    if len(recent) >= RATE_LIMIT:
        raise HTTPException(429, "That is a lot of comparisons. Give it a few minutes and try again.")
    recent.append(now)
    hits[visitor] = recent


@app.get("/healthz")
def healthz():
    # Used by Render's health check and the keepalive ping.
    # Must stay cheap: it never calls Qloo or the LLM.
    return {"ok": True}


@app.get("/api/config")
def api_config(request: Request):
    return {
        "domains": {k: {"label": v["label"], "noun": v["noun"]} for k, v in DOMAINS.items()},
        "examples": EXAMPLES,
        "take": config.TAKE,
        "repo_url": config.REPO_URL,
        "llm_configured": request.app.state.llm.configured,
        "llm_model": request.app.state.llm.model,
    }


@app.post("/api/grounded")
async def api_grounded(body: CompareRequest, request: Request):
    names = clean_request(body)
    check_rate_limit(request)
    try:
        return await compare.grounded(request.app.state.qloo, names,
                                      body.input_domain, body.output_domain, config.TAKE)
    except compare.InputNotFound as exc:
        raise HTTPException(404, f'Qloo has nothing called "{exc}" in {DOMAINS[body.input_domain]["label"]}.')
    except QlooError as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/baseline")
async def api_baseline(body: CompareRequest, request: Request):
    names = clean_request(body)
    llm = request.app.state.llm
    if not llm.configured:
        raise HTTPException(503, "The LLM baseline is not configured on this server.")
    try:
        return await compare.baseline(request.app.state.qloo, llm, names,
                                      body.input_domain, body.output_domain, config.TAKE)
    except compare.InputNotFound as exc:
        raise HTTPException(404, f'Qloo has nothing called "{exc}" in {DOMAINS[body.input_domain]["label"]}.')
    except LLMError as exc:
        raise HTTPException(502, str(exc))
    except QlooError as exc:
        raise HTTPException(502, str(exc))


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
