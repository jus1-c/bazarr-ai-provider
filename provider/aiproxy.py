# coding=utf-8
"""Bazarr provider shim with built-in provider search and AI fallback.

This file is copied into Bazarr's subliminal_patch providers directory. Bazarr
imports providers in-process, so this shim can reuse Bazarr provider configs and
only calls the worker when Bazarr's own scoring cannot produce a good result.
"""

from __future__ import annotations

import base64
import codecs
import hashlib
import logging
import os
import pickle

from requests import Session
from subzero.language import Language
from subliminal import Episode, Movie
from subliminal_patch.providers import Provider
from subliminal_patch.subtitle import Subtitle


logger = logging.getLogger(__name__)


COMMON_ALPHA3 = (
    "afr", "ara", "aze", "bel", "ben", "bos", "bre", "bul", "cat", "ces",
    "chi", "dan", "deu", "ell", "eng", "epo", "est", "eus", "fas", "fin",
    "fra", "gle", "glg", "heb", "hin", "hrv", "hun", "hye", "ind", "isl",
    "ita", "jpn", "kat", "kaz", "kor", "lav", "lit", "mkd", "mon", "msa",
    "nld", "nor", "pol", "por", "ron", "rus", "slk", "slv", "spa", "sqi",
    "srp", "swe", "tam", "tel", "tha", "tur", "ukr", "urd", "vie", "zho",
)


def _build_languages():
    languages = set()
    for code in COMMON_ALPHA3:
        try:
            languages.add(Language(code))
        except Exception:
            logger.debug("aiproxy skipping unsupported language code: %s", code)

    rebuilt = set()
    for language in languages:
        try:
            rebuilt.add(Language.rebuild(language, forced=True))
            rebuilt.add(Language.rebuild(language, hi=True))
        except Exception:
            logger.debug("aiproxy skipping language modifier for: %s", language)
    languages.update(rebuilt)
    return languages


class AIProxySubtitle(Subtitle):
    provider_name = "aiproxy"
    hash_verifiable = False
    hearing_impaired_verifiable = True

    def __init__(self, language, candidate):
        super().__init__(language)
        self.candidate_id = candidate["id"]
        self.origin = candidate.get("origin", "worker")
        self.source_provider = candidate.get("source_provider") or candidate.get("provider")
        self.provider_id = candidate.get("provider_id")
        self.original_subtitle = candidate.get("original_subtitle")
        self.media_type = candidate.get("media_type")
        self.language = language
        self.matches = set(candidate.get("matches") or [])
        self.ai_score = candidate.get("ai_score")
        self.rule_score = candidate.get("rule_score")
        self.score = candidate.get("score")
        self.page_link = candidate.get("page_link")
        self.uploader = candidate.get("uploader") or "aiproxy"
        release_info = candidate.get("release_info") or []
        if isinstance(release_info, list):
            self.releases = release_info
            self.release_info = ", ".join(release_info)
        else:
            self.releases = [str(release_info)]
            self.release_info = str(release_info)
        self.hearing_impaired = bool(candidate.get("hearing_impaired"))
        self.forced = bool(candidate.get("forced"))
        self.use_original_format = candidate.get("original_format", True)
        self.hash_verifiable = bool(candidate.get("hash_verifiable", False))

    @property
    def id(self):
        return self.candidate_id

    def get_matches(self, video):
        return self.matches


class AIProxyProvider(Provider):
    """Provider that asks bazarr-ai-provider for enhanced search results."""

    languages = _build_languages()
    video_types = (Episode, Movie)

    def __init__(self):
        self.endpoint = os.environ.get("AIPROXY_ENDPOINT", "http://bazarr-ai-provider:8787").rstrip("/")
        self.timeout = float(os.environ.get("AIPROXY_TIMEOUT", "60"))
        self.builtin_enabled = _env_bool("AIPROXY_BUILTIN_ENABLED", True)
        self.ai_fallback_enabled = _env_bool("AIPROXY_AI_FALLBACK_ENABLED", True)
        self.ai_fallback_max_candidates = _env_int("AIPROXY_AI_FALLBACK_MAX_CANDIDATES", 10)
        self.worker_fallback = _env_bool("AIPROXY_WORKER_FALLBACK", True)
        self.max_results = _env_int("AIPROXY_MAX_RESULTS", 25)
        self.session = None

    def initialize(self):
        self.session = Session()
        self.session.headers.update({"User-Agent": "Bazarr-AIProxy/0.1"})

    def terminate(self):
        if self.session:
            self.session.close()

    def list_subtitles(self, video, languages):
        if not self.session:
            self.initialize()

        if self.builtin_enabled:
            media_type, original_subtitles = _search_builtin_subtitles(video, languages)
            subtitles = _list_builtin_subtitles(video, languages, media_type, original_subtitles, self.max_results)
            if subtitles:
                return subtitles

            if self.ai_fallback_enabled:
                subtitles = self._list_ai_fallback_subtitles(video, languages, media_type, original_subtitles)
                if subtitles:
                    return subtitles

        if not self.worker_fallback:
            return []

        payload = {
            "video": _serialize_video(video),
            "languages": [_serialize_language(language) for language in languages],
        }

        try:
            response = self.session.post(
                f"{self.endpoint}/v1/search",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as error:
            logger.exception("aiproxy search failed: %r", error)
            return []

        requested_languages = list(languages)
        fallback_language = requested_languages[0] if requested_languages else None
        subtitles = []
        for candidate in response.json().get("subtitles", []):
            language = _language_from_candidate(candidate, fallback_language)
            if language is None:
                continue
            subtitles.append(AIProxySubtitle(language, candidate))
        return subtitles

    def download_subtitle(self, subtitle):
        if not self.session:
            self.initialize()

        if getattr(subtitle, "origin", None) == "builtin":
            return _download_builtin_subtitle(subtitle)

        try:
            response = self.session.get(
                f"{self.endpoint}/v1/download/{subtitle.candidate_id}",
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            subtitle.content = base64.b64decode(payload["content_b64"])
            subtitle.format = payload.get("format") or "srt"
        except Exception as error:
            logger.exception("aiproxy download failed for %s: %r", subtitle.candidate_id, error)
            subtitle.content = None
        return subtitle

    def _list_ai_fallback_subtitles(self, video, languages, media_type, original_subtitles):
        candidates = []
        for original in original_subtitles:
            candidate = _candidate_from_builtin_for_ai(original, video, languages, media_type)
            if candidate is not None:
                candidates.append(candidate)
            if len(candidates) >= self.ai_fallback_max_candidates:
                break

        if not candidates:
            return []

        payload = {
            "video": _serialize_video(video),
            "candidates": [_candidate_for_ai_request(candidate) for candidate in candidates],
        }
        try:
            response = self.session.post(
                f"{self.endpoint}/v1/score",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as error:
            logger.exception("aiproxy AI fallback scoring failed: %r", error)
            return []

        scored_by_id = {item["id"]: item for item in response.json().get("candidates", [])}
        accepted = []
        for candidate in candidates:
            scored = scored_by_id.get(candidate["id"])
            if not scored or not scored.get("accepted"):
                continue
            candidate["score"] = scored.get("score", candidate.get("score", 0))
            candidate["ai_score"] = scored.get("ai_score")
            candidate["rule_score"] = scored.get("rule_score", candidate.get("rule_score", 0))
            candidate["uploader"] = candidate.get("uploader") or f"AI accepted {candidate['source_provider']}"
            accepted.append(AIProxySubtitle(_language_from_candidate(candidate, None), candidate))

        return sorted(accepted, key=lambda item: item.score or 0, reverse=True)[: self.max_results]


def _search_builtin_subtitles(video, languages):
    media_type = "series" if isinstance(video, Episode) else "movie"
    try:
        providers = _builtin_provider_names()
        if not providers:
            return media_type, []

        pool = _builtin_pool(media_type, providers)

        from subliminal_patch.core_persistent import list_all_subtitles

        subtitles_by_video = list_all_subtitles([video], set(languages), pool)
        return media_type, subtitles_by_video.get(video, [])
    except Exception as error:
        logger.exception("aiproxy builtin search failed: %r", error)
        return media_type, []


def _list_builtin_subtitles(video, languages, media_type, original_subtitles, max_results):
    if not original_subtitles:
        return []

    wrapped = []
    for original in original_subtitles:
        candidate = _candidate_from_builtin(original, video, languages, media_type)
        if candidate is None:
            continue
        wrapped.append(AIProxySubtitle(_language_from_candidate(candidate, original.language), candidate))

    return sorted(wrapped, key=lambda item: item.score or 0, reverse=True)[:max_results]


def _download_builtin_subtitle(subtitle):
    if not subtitle.original_subtitle or not subtitle.source_provider:
        subtitle.content = None
        return subtitle

    try:
        original = pickle.loads(codecs.decode(subtitle.original_subtitle.encode(), "base64"))
        pool = _builtin_pool(subtitle.media_type or "series", [subtitle.source_provider])

        from subliminal_patch.core_persistent import download_subtitles

        download_subtitles([original], pool)
        subtitle.content = original.content
        subtitle.format = getattr(original, "format", None) or getattr(subtitle, "format", None) or "srt"
    except Exception as error:
        logger.exception("aiproxy builtin download failed for %s: %r", subtitle.candidate_id, error)
        subtitle.content = None
    return subtitle


def _builtin_provider_names():
    configured = [item.strip() for item in os.environ.get("AIPROXY_BUILTIN_PROVIDERS", "").split(",") if item.strip()]
    if configured:
        providers = configured
    else:
        from app.get_providers import get_providers

        providers = get_providers() or []

    try:
        from subliminal_patch.extensions import provider_registry

        existing = set(provider_registry.names())
    except Exception:
        existing = set(providers)

    return [provider for provider in providers if provider != "aiproxy" and provider in existing]


def _builtin_pool(media_type, providers):
    from app.get_providers import get_language_equals, get_providers_auth, provider_pool, provider_throttle
    from subtitles.utils import get_ban_list

    if media_type == "series":
        from sonarr.blacklist import get_blacklist

        blacklist = get_blacklist()
    else:
        from radarr.blacklist import get_blacklist_movie

        blacklist = get_blacklist_movie()

    pool_factory = provider_pool()
    return pool_factory(
        providers=providers,
        provider_configs=get_providers_auth(),
        blacklist=blacklist,
        throttle_callback=provider_throttle,
        ban_list=get_ban_list(None),
        language_hook=None,
        language_equals=get_language_equals(),
    )


def _candidate_from_builtin(subtitle, video, languages, media_type):
    try:
        score, matches = _builtin_percent_score(subtitle, video, languages, media_type)
    except Exception:
        logger.debug("aiproxy could not score builtin subtitle: %r", subtitle, exc_info=True)
        return None

    threshold = _builtin_threshold(media_type)
    if score < threshold:
        return None

    source_provider = getattr(subtitle, "provider_name", None)
    if not source_provider or source_provider == "aiproxy":
        return None

    try:
        original_payload = codecs.encode(pickle.dumps(subtitle.make_picklable()), "base64").decode()
    except Exception:
        logger.debug("aiproxy could not pickle builtin subtitle: %r", subtitle, exc_info=True)
        return None

    language = getattr(subtitle, "language", None)
    release_info = _release_info(subtitle)
    provider_id = str(getattr(subtitle, "id", "") or "unknown")
    digest = hashlib.sha256(f"{source_provider}:{provider_id}".encode("utf-8")).hexdigest()[:16]
    candidate_id = f"builtin:{source_provider}:{digest}"
    return {
        "id": candidate_id,
        "origin": "builtin",
        "provider": "builtin",
        "source_provider": source_provider,
        "provider_id": provider_id,
        "original_subtitle": original_payload,
        "media_type": media_type,
        "language": _serialize_language(language) if language else {},
        "score": score,
        "rule_score": score,
        "ai_score": None,
        "matches": list(matches),
        "release_info": release_info,
        "page_link": getattr(subtitle, "page_link", None),
        "uploader": getattr(subtitle, "uploader", None) or source_provider,
        "forced": bool(getattr(language, "forced", False)),
        "hearing_impaired": bool(getattr(subtitle, "hearing_impaired", False)),
        "original_format": bool(getattr(subtitle, "use_original_format", True)),
        "hash_verifiable": bool(getattr(subtitle, "hash_verifiable", False)),
    }


def _candidate_from_builtin_for_ai(subtitle, video, languages, media_type):
    language = getattr(subtitle, "language", None)
    requested_language = _matching_requested_language(language, languages)
    if requested_language is None:
        return None

    try:
        matches = set(subtitle.get_matches(video))
    except Exception:
        logger.debug("aiproxy could not get AI fallback matches: %r", subtitle, exc_info=True)
        return None

    if not _has_id_match(matches):
        return None

    source_provider = getattr(subtitle, "provider_name", None)
    if not source_provider or source_provider == "aiproxy":
        return None

    try:
        original_payload = codecs.encode(pickle.dumps(subtitle.make_picklable()), "base64").decode()
    except Exception:
        logger.debug("aiproxy could not pickle AI fallback subtitle: %r", subtitle, exc_info=True)
        return None

    rule_score = _relaxed_percent_score(subtitle, video, matches, media_type)
    provider_id = str(getattr(subtitle, "id", "") or "unknown")
    digest = hashlib.sha256(
        f"ai:{source_provider}:{provider_id}:{str(language)}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "id": f"builtin-ai:{source_provider}:{digest}",
        "origin": "builtin",
        "provider": "builtin",
        "source_provider": source_provider,
        "provider_id": provider_id,
        "original_subtitle": original_payload,
        "media_type": media_type,
        "language": _serialize_language(language),
        "score": rule_score,
        "rule_score": rule_score,
        "ai_score": None,
        "matches": list(matches),
        "release_info": _release_info(subtitle),
        "page_link": getattr(subtitle, "page_link", None),
        "uploader": getattr(subtitle, "uploader", None) or source_provider,
        "forced": bool(getattr(language, "forced", False)),
        "hearing_impaired": bool(getattr(subtitle, "hearing_impaired", False)),
        "original_format": bool(getattr(subtitle, "use_original_format", True)),
        "hash_verifiable": bool(getattr(subtitle, "hash_verifiable", False)),
    }


def _candidate_for_ai_request(candidate):
    return {
        "id": candidate["id"],
        "provider": candidate["source_provider"],
        "provider_id": candidate["provider_id"],
        "language": candidate["language"],
        "rule_score": candidate.get("rule_score", 0),
        "matches": candidate.get("matches", []),
        "release_info": candidate.get("release_info", []),
        "page_link": candidate.get("page_link"),
        "uploader": candidate.get("uploader"),
        "forced": candidate.get("forced", False),
        "hearing_impaired": candidate.get("hearing_impaired", False),
    }


def _builtin_percent_score(subtitle, video, languages, media_type):
    language_set = set(languages)
    if language_set and subtitle.language not in language_set and not _normal_language_request(language_set):
        raise ValueError("subtitle language is not requested")

    matches = set(subtitle.get_matches(video))
    if media_type == "series":
        can_verify_series = True
        if not getattr(subtitle, "hash_verifiable", False) and "hash" in matches:
            can_verify_series = False
        if can_verify_series and not {"series", "season", "episode"}.issubset(matches):
            raise ValueError("subtitle does not match requested series episode")

    if getattr(subtitle, "hearing_impaired", False) or _normal_language_request(language_set):
        matches.add("hearing_impaired")

    from app.config import get_scores, settings
    from subliminal_patch.score import ComputeScore
    from subtitles.utils import _get_scores

    _, max_score, _ = _get_scores(media_type, settings.general.minimum_score_movie, settings.general.minimum_score)
    raw_score, score_without_hash = ComputeScore(get_scores())(
        matches,
        subtitle,
        video,
        hearing_impaired=getattr(subtitle, "hearing_impaired", False),
    )
    if "hash" not in matches:
        raw_score = score_without_hash
    return round(raw_score / max_score * 100), matches


def _relaxed_percent_score(subtitle, video, matches, media_type):
    try:
        from app.config import get_scores, settings
        from subliminal_patch.score import ComputeScore
        from subtitles.utils import _get_scores

        _, max_score, _ = _get_scores(media_type, settings.general.minimum_score_movie, settings.general.minimum_score)
        raw_score, score_without_hash = ComputeScore(get_scores())(
            matches,
            subtitle,
            video,
            hearing_impaired=getattr(subtitle, "hearing_impaired", False),
        )
        if "hash" not in matches:
            raw_score = score_without_hash
        return round(raw_score / max_score * 100)
    except Exception:
        return 0


def _matching_requested_language(language, requested_languages):
    if language is None:
        return None
    for requested in requested_languages:
        if getattr(language, "basename", None) != getattr(requested, "basename", None):
            continue
        if getattr(requested, "forced", False) and not getattr(language, "forced", False):
            continue
        if not getattr(requested, "forced", False) and getattr(language, "forced", False):
            continue
        if getattr(requested, "hi", False) and not getattr(language, "hi", False):
            continue
        return requested
    return None


def _has_id_match(matches):
    id_matches = {
        "hash",
        "imdb_id",
        "series_imdb_id",
        "tvdb_id",
        "series_tvdb_id",
        "tmdb_id",
        "series_tmdb_id",
    }
    return bool(id_matches.intersection(matches))


def _builtin_threshold(media_type):
    override_name = "AIPROXY_BUILTIN_MIN_SCORE_MOVIE" if media_type == "movie" else "AIPROXY_BUILTIN_MIN_SCORE"
    if os.environ.get(override_name):
        return _env_int(override_name, 0)
    from app.config import settings

    return settings.general.minimum_score_movie if media_type == "movie" else settings.general.minimum_score


def _normal_language_request(language_set):
    return bool(language_set) and all(
        not getattr(language, "forced", False) and not getattr(language, "hi", False)
        for language in language_set
    )


def _release_info(subtitle):
    releases = getattr(subtitle, "releases", None)
    if isinstance(releases, list):
        return [str(item) for item in releases]
    release_info = getattr(subtitle, "release_info", None)
    if isinstance(release_info, str):
        return [item.strip() for item in release_info.split(",") if item.strip()]
    if release_info:
        return [str(release_info)]
    return []


def _serialize_video(video):
    if isinstance(video, Episode):
        media_type = "series"
        title = getattr(video, "series", None)
        imdb_id = getattr(video, "series_imdb_id", None)
        alternative_titles = list(getattr(video, "alternative_series", []) or [])
    else:
        media_type = "movie"
        title = getattr(video, "title", None)
        imdb_id = getattr(video, "imdb_id", None)
        alternative_titles = list(getattr(video, "alternative_titles", []) or [])

    return {
        "media_type": media_type,
        "title": title,
        "alternative_titles": alternative_titles,
        "year": getattr(video, "year", None),
        "season": getattr(video, "season", None),
        "episode": getattr(video, "episode", None),
        "imdb_id": imdb_id,
        "tvdb_id": getattr(video, "tvdb_id", None),
        "original_name": getattr(video, "original_name", None),
        "original_path": getattr(video, "original_path", None),
        "release_group": getattr(video, "release_group", None),
        "resolution": getattr(video, "resolution", None),
        "source": getattr(video, "source", None),
        "video_codec": getattr(video, "video_codec", None),
        "audio_codec": getattr(video, "audio_codec", None),
        "streaming_service": getattr(video, "streaming_service", None),
    }


def _serialize_language(language):
    return {
        "alpha3": getattr(language, "alpha3", None),
        "basename": getattr(language, "basename", None),
        "ietf": str(language),
        "forced": bool(getattr(language, "forced", False)),
        "hi": bool(getattr(language, "hi", False)),
    }


def _language_from_candidate(candidate, fallback):
    language_payload = candidate.get("language") or {}
    alpha3 = language_payload.get("alpha3")
    try:
        language = Language(alpha3) if alpha3 else fallback
        if language is None:
            return None
        if language_payload.get("forced"):
            language = Language.rebuild(language, forced=True)
        if language_payload.get("hi"):
            language = Language.rebuild(language, hi=True)
        return language
    except Exception:
        logger.debug("aiproxy could not rebuild language from payload: %r", language_payload)
        return fallback


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default
