from __future__ import annotations

import base64
import logging

from fastapi import FastAPI, HTTPException

from .bazarr_config import load_bazarr_config, proxy_url, subsource_api_key
from .cache import CandidateCache
from .logging_utils import vlog
from .models import DownloadResponse, ScoreRequest, ScoreResponse, SearchRequest, SearchResponse
from .service import SearchService
from .settings import Settings
from .subsource import SubsourceClient


settings = Settings.from_env()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
logger.info(
    "Bazarr AI Provider starting log_level=%s verbose=%s ai_enabled=%s openai_base_url_configured=%s",
    settings.log_level,
    settings.verbose_logs,
    settings.ai_enabled,
    bool(settings.openai_base_url),
)
cache = CandidateCache()
service = SearchService(settings=settings, cache=cache)
app = FastAPI(title="Bazarr AI Provider", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict:
    config = load_bazarr_config(settings.bazarr_config_path)
    result = {
        "status": "ok",
        "has_bazarr_config": bool(config),
        "has_subsource_api_key": bool(subsource_api_key(config, settings.subsource_api_key)),
        "ai_enabled": bool(settings.ai_enabled and settings.openai_base_url),
        "verbose_logs": settings.verbose_logs,
        "log_level": settings.log_level,
    }
    logger.info("healthz %s", result)
    return result


@app.post("/v1/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    vlog(logger, "/v1/search video=%s languages=%s", request.video.model_dump(), [item.model_dump() for item in request.languages])
    subtitles = service.search(request)
    logger.info("/v1/search result_count=%s", len(subtitles))
    return SearchResponse(subtitles=subtitles)


@app.post("/v1/score", response_model=ScoreResponse)
def score(request: ScoreRequest) -> ScoreResponse:
    vlog(logger, "/v1/score video=%s candidate_count=%s", request.video.model_dump(), len(request.candidates))
    candidates = service.score(request)
    logger.info("/v1/score accepted=%s rejected=%s", sum(1 for item in candidates if item.accepted), sum(1 for item in candidates if not item.accepted))
    return ScoreResponse(candidates=candidates)


@app.get("/v1/download/{candidate_id}", response_model=DownloadResponse)
def download(candidate_id: str) -> DownloadResponse:
    vlog(logger, "/v1/download candidate_id=%s", candidate_id)
    candidate = cache.get(candidate_id)
    if candidate is None:
        logger.warning("/v1/download candidate_id=%s cache_miss", candidate_id)
        raise HTTPException(status_code=404, detail="candidate not found or expired")

    config = load_bazarr_config(settings.bazarr_config_path)
    api_key = subsource_api_key(config, settings.subsource_api_key)
    if not api_key:
        raise HTTPException(status_code=503, detail="Subsource API key missing")

    client = SubsourceClient(api_key=api_key, timeout=settings.http_timeout, proxy=proxy_url(config))
    try:
        filename, subtitle_format, content = client.download(candidate["provider_id"], candidate)
    finally:
        client.close()

    logger.info(
        "/v1/download complete candidate_id=%s provider=%s filename=%s format=%s bytes=%s",
        candidate_id,
        candidate["provider"],
        filename,
        subtitle_format,
        len(content),
    )

    return DownloadResponse(
        id=candidate_id,
        provider=candidate["provider"],
        filename=filename,
        format=subtitle_format,
        content_b64=base64.b64encode(content).decode("ascii"),
    )
