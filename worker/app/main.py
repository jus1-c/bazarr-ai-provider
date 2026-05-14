from __future__ import annotations

import base64
import logging

from fastapi import FastAPI, HTTPException

from .bazarr_config import load_bazarr_config, proxy_url, subsource_api_key
from .cache import CandidateCache
from .models import DownloadResponse, ScoreRequest, ScoreResponse, SearchRequest, SearchResponse
from .service import SearchService
from .settings import Settings
from .subsource import SubsourceClient


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = Settings.from_env()
cache = CandidateCache()
service = SearchService(settings=settings, cache=cache)
app = FastAPI(title="Bazarr AI Provider", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict:
    config = load_bazarr_config(settings.bazarr_config_path)
    return {
        "status": "ok",
        "has_bazarr_config": bool(config),
        "has_subsource_api_key": bool(subsource_api_key(config, settings.subsource_api_key)),
        "ai_enabled": bool(settings.ai_enabled and settings.openai_base_url),
    }


@app.post("/v1/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    return SearchResponse(subtitles=service.search(request))


@app.post("/v1/score", response_model=ScoreResponse)
def score(request: ScoreRequest) -> ScoreResponse:
    return ScoreResponse(candidates=service.score(request))


@app.get("/v1/download/{candidate_id}", response_model=DownloadResponse)
def download(candidate_id: str) -> DownloadResponse:
    candidate = cache.get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found or expired")

    config = load_bazarr_config(settings.bazarr_config_path)
    api_key = subsource_api_key(config, settings.subsource_api_key)
    if not api_key:
        raise HTTPException(status_code=503, detail="Subsource API key missing")

    client = SubsourceClient(api_key=api_key, timeout=settings.http_timeout, proxy=proxy_url(config))
    try:
        filename, subtitle_format, content = client.download(candidate["provider_id"])
    finally:
        client.close()

    return DownloadResponse(
        id=candidate_id,
        provider=candidate["provider"],
        filename=filename,
        format=subtitle_format,
        content_b64=base64.b64encode(content).decode("ascii"),
    )
