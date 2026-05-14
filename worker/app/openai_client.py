from __future__ import annotations

import json
import logging
import re

import httpx

from .models import VideoRequest
from .settings import Settings


logger = logging.getLogger(__name__)
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def ai_score_candidate(settings: Settings, video: VideoRequest, candidate: dict, rule_score: int, force: bool = False) -> int | None:
    if not settings.ai_enabled or not settings.openai_base_url:
        return None
    if not force and (rule_score < settings.ai_lower_bound or rule_score > settings.ai_upper_bound):
        return None

    prompt = {
        "task": "Score whether this subtitle release matches the requested media. Return JSON only.",
        "media": video.model_dump(),
        "candidate": {
            "provider": candidate.get("provider"),
            "provider_id": candidate.get("provider_id"),
            "release_info": candidate.get("release_info"),
            "language": candidate.get("language_alpha3"),
            "forced": candidate.get("forced"),
            "hearing_impaired": candidate.get("hearing_impaired"),
            "matches": candidate.get("matches"),
        },
        "rule_score": rule_score,
        "schema": {"score": "0-100 integer", "decision": "accept|reject", "reason": "short text"},
    }
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    body = {
        "model": settings.openai_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "You are a strict subtitle matching scorer. Return JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
        ],
    }
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=settings.http_timeout)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        match = JSON_RE.search(content)
        parsed = json.loads(match.group(0) if match else content)
        return max(0, min(100, int(parsed["score"])))
    except Exception as error:
        logger.warning("AI scoring failed, keeping rule score: %r", error)
        return None
