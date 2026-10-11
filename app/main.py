"""Crossover web app. Start it with:  uvicorn app.main:app --reload"""

from fastapi import FastAPI

app = FastAPI(title="Crossover")


@app.get("/")
def home():
    return {"message": "hello from Crossover"}


@app.get("/healthz")
def healthz():
    # Used by Render's health check and the keepalive ping.
    # Must stay cheap: it never calls Qloo or the LLM.
    return {"ok": True}
