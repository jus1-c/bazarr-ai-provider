from __future__ import annotations

import logging
import os


def verbose_enabled() -> bool:
    return os.environ.get("VERBOSE_LOGS", "false").lower() in {"1", "true", "yes", "on"}


def vlog(logger: logging.Logger, message: str, *args) -> None:
    if verbose_enabled():
        logger.info("verbose: " + message, *args)
