from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    bazarr_config_path: str
    subsource_api_key: str | None
    openai_base_url: str | None
    openai_api_key: str
    openai_model: str
    ai_enabled: bool
    ai_threshold: int
    ai_lower_bound: int
    ai_upper_bound: int
    max_candidates: int
    http_timeout: float
    log_level: str
    verbose_logs: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bazarr_config_path=os.environ.get("BAZARR_CONFIG_PATH", "/bazarr-config/config/config.yaml"),
            subsource_api_key=os.environ.get("SUBSOURCE_API_KEY"),
            openai_base_url=os.environ.get("OPENAI_BASE_URL"),
            openai_api_key=os.environ.get("OPENAI_API_KEY", "dummy"),
            openai_model=os.environ.get("OPENAI_MODEL", "local-model"),
            ai_enabled=_env_bool("AI_ENABLED", True),
            ai_threshold=_env_int("AI_THRESHOLD", 85),
            ai_lower_bound=_env_int("AI_LOWER_BOUND", 60),
            ai_upper_bound=_env_int("AI_UPPER_BOUND", 90),
            max_candidates=_env_int("MAX_CANDIDATES", 25),
            http_timeout=float(os.environ.get("HTTP_TIMEOUT", "30")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            verbose_logs=_env_bool("VERBOSE_LOGS", False),
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default
