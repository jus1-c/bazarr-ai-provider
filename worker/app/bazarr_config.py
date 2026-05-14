from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

import yaml


def load_bazarr_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def subsource_api_key(config: dict, override: str | None = None) -> str | None:
    if override:
        return override
    return (config.get("subsource") or {}).get("apikey") or None


def proxy_url(config: dict) -> str | None:
    proxy = config.get("proxy") or {}
    proxy_type = proxy.get("type")
    host = proxy.get("url")
    port = proxy.get("port")
    if not proxy_type or not host or not port:
        return None

    username = proxy.get("username")
    password = proxy.get("password")
    if username and password:
        auth = f"{quote_plus(str(username))}:{quote_plus(str(password))}@"
    else:
        auth = ""
    return f"{proxy_type}://{auth}{host}:{port}"


def enabled_providers(config: dict) -> list[str]:
    providers = (config.get("general") or {}).get("enabled_providers") or []
    return providers if isinstance(providers, list) else []
