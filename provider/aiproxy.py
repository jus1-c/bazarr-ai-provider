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
from pathlib import PurePath

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
        self.profile_languages_enabled = _env_bool("AIPROXY_PROFILE_LANGUAGES_ENABLED", True)
        self.verbose = _env_bool("AIPROXY_VERBOSE", False)
        self.session = None

    def initialize(self):
        self.session = Session()
        self.session.headers.update({"User-Agent": "Bazarr-AIProxy/0.1"})
        _vlog(
            "initialized endpoint=%s builtin=%s ai_fallback=%s worker_fallback=%s profile_languages=%s max_results=%s",
            self.endpoint,
            self.builtin_enabled,
            self.ai_fallback_enabled,
            self.worker_fallback,
            self.profile_languages_enabled,
            self.max_results,
        )

    def terminate(self):
        if self.session:
            self.session.close()

    def list_subtitles(self, video, languages):
        if not self.session:
            self.initialize()

        requested_languages = list(languages)
        target_languages = _target_languages(video, requested_languages, self.profile_languages_enabled)

        _vlog(
            "search start video=%s requested_languages=%s target_languages=%s",
            _video_summary(video),
            [_language_summary(language) for language in requested_languages],
            [_language_summary(language) for language in target_languages],
        )

        subtitles = []
        missing_languages = list(target_languages)
        media_type = "series" if isinstance(video, Episode) else "movie"
        original_subtitles = []

        if self.builtin_enabled:
            media_type, original_subtitles = _search_builtin_subtitles(video, target_languages)
            subtitles = _list_builtin_subtitles(video, target_languages, media_type, original_subtitles)
            missing_languages = _missing_languages(target_languages, subtitles)
            _vlog("builtin strict results=%s raw_candidates=%s", len(subtitles), len(original_subtitles))

            if self.ai_fallback_enabled and missing_languages and not self.worker_fallback:
                ai_subtitles = self._list_ai_fallback_subtitles(video, missing_languages, media_type, original_subtitles)
                subtitles = _dedupe_subtitles([*subtitles, *ai_subtitles])
                missing_languages = _missing_languages(target_languages, subtitles)
                _vlog(
                    "AI fallback results=%s remaining_missing=%s",
                    len(ai_subtitles),
                    [_language_summary(language) for language in missing_languages],
                )

        if not self.worker_fallback:
            if subtitles:
                return _sorted_subtitles(subtitles, self.max_results)
            _vlog("worker fallback disabled; returning no candidates")
            return []

        if missing_languages:
            worker_subtitles = self._list_worker_fallback_subtitles(video, missing_languages, media_type, original_subtitles)
            subtitles = _dedupe_subtitles([*subtitles, *worker_subtitles])
            missing_languages = _missing_languages(target_languages, subtitles)
            _vlog(
                "worker fallback results=%s remaining_missing=%s",
                len(worker_subtitles),
                [_language_summary(language) for language in missing_languages],
            )

        subtitles = _sorted_subtitles(subtitles, self.max_results)
        if subtitles:
            _vlog("returning candidates=%s", [_subtitle_summary(item) for item in subtitles])
        return subtitles

    def _list_worker_fallback_subtitles(self, video, languages, media_type, original_subtitles):
        if not languages:
            return []

        provider_candidates = []
        if self.ai_fallback_enabled:
            for original in original_subtitles or []:
                candidate = _candidate_from_builtin_for_ai(original, video, languages, media_type)
                if candidate is not None:
                    provider_candidates.append(candidate)
                if len(provider_candidates) >= self.ai_fallback_max_candidates:
                    break

        payload = {
            "video": _serialize_video(video),
            "languages": [_serialize_language(language) for language in languages],
            "candidates": [_candidate_for_worker_search_request(candidate) for candidate in provider_candidates],
        }

        try:
            _vlog("worker fallback POST %s/v1/search provider_candidates=%s", self.endpoint, len(provider_candidates))
            response = self.session.post(
                f"{self.endpoint}/v1/search",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as error:
            logger.exception("aiproxy search failed: %r", error)
            return []

        fallback_language = languages[0] if languages else None
        subtitles = []
        for candidate in response.json().get("subtitles", []):
            language = _language_from_candidate(candidate, fallback_language)
            if language is None:
                continue
            subtitles.append(AIProxySubtitle(language, candidate))
        _vlog("worker fallback returned=%s candidates=%s", len(subtitles), [_subtitle_summary(item) for item in subtitles])
        return subtitles

    def download_subtitle(self, subtitle):
        if not self.session:
            self.initialize()

        if getattr(subtitle, "origin", None) == "builtin":
            _vlog("download via builtin provider candidate=%s", _subtitle_summary(subtitle))
            return _download_builtin_subtitle(subtitle)

        try:
            _vlog("download via worker candidate_id=%s provider=%s", subtitle.candidate_id, subtitle.source_provider)
            response = self.session.get(
                f"{self.endpoint}/v1/download/{subtitle.candidate_id}",
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            subtitle.content = base64.b64decode(payload["content_b64"])
            subtitle.format = payload.get("format") or "srt"
            _vlog("worker download complete candidate_id=%s format=%s bytes=%s", subtitle.candidate_id, subtitle.format, len(subtitle.content or b""))
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
            _vlog("AI fallback skipped: no relaxed candidates with target language and ID/hash match")
            return []

        payload = {
            "video": _serialize_video(video),
            "candidates": [_candidate_for_ai_request(candidate) for candidate in candidates],
        }
        try:
            _vlog("AI fallback scoring candidates=%s", [_candidate_summary(candidate) for candidate in candidates])
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
                _vlog("AI fallback rejected candidate=%s score_payload=%s", _candidate_summary(candidate), scored)
                continue
            candidate["score"] = scored.get("score", candidate.get("score", 0))
            candidate["ai_score"] = scored.get("ai_score")
            candidate["rule_score"] = scored.get("rule_score", candidate.get("rule_score", 0))
            candidate["uploader"] = candidate.get("uploader") or f"AI accepted {candidate['source_provider']}"
            _vlog("AI fallback accepted candidate=%s score_payload=%s", _candidate_summary(candidate), scored)
            accepted.append(AIProxySubtitle(_language_from_candidate(candidate, None), candidate))

        return sorted(accepted, key=lambda item: item.score or 0, reverse=True)[: self.max_results]


def _target_languages(video, requested_languages, profile_languages_enabled):
    if not profile_languages_enabled:
        return list(requested_languages)

    profile_languages = _profile_languages_for_video(video)
    if not profile_languages:
        return list(requested_languages)

    merged = _dedupe_languages([*profile_languages, *requested_languages])
    _vlog(
        "profile language expansion profile_languages=%s merged=%s",
        [_language_summary(language) for language in profile_languages],
        [_language_summary(language) for language in merged],
    )
    return merged


def _profile_languages_for_video(video):
    media_type = "series" if isinstance(video, Episode) else "movie"
    profile_id = _profile_id_for_video(video, media_type)
    if profile_id is None:
        return []

    try:
        from app.database import get_profiles_list

        profile = get_profiles_list(profile_id=int(profile_id))
    except Exception as error:
        _vlog("profile language lookup failed profile_id=%s error=%r", profile_id, error)
        logger.debug("aiproxy could not load language profile %s", profile_id, exc_info=True)
        return []

    if not profile:
        return []

    languages = []
    for item in profile.get("items") or []:
        language = _language_from_profile_item(item)
        if language is not None:
            languages.append(language)
    return _dedupe_languages(languages)


def _profile_id_for_video(video, media_type):
    path = getattr(video, "original_path", None)
    if not path:
        return None

    try:
        from app.database import TableEpisodes, TableMovies, TableShows, database, select
        from utilities.path_mappings import path_mappings

        candidate_paths = [path]
        try:
            mapped_path = (
                path_mappings.path_replace_reverse(path)
                if media_type == "series"
                else path_mappings.path_replace_reverse_movie(path)
            )
            if mapped_path and mapped_path not in candidate_paths:
                candidate_paths.append(mapped_path)
        except Exception:
            logger.debug("aiproxy could not reverse path mapping for profile lookup", exc_info=True)

        if media_type == "series":
            row = database.execute(
                select(TableShows.profileId)
                .select_from(TableEpisodes)
                .join(TableShows)
                .where(TableEpisodes.path.in_(candidate_paths))
            ).first()
        else:
            row = database.execute(
                select(TableMovies.profileId)
                .where(TableMovies.path.in_(candidate_paths))
            ).first()
    except Exception as error:
        _vlog("profile id lookup failed media_type=%s error=%r", media_type, error)
        logger.debug("aiproxy could not resolve language profile for video", exc_info=True)
        return None

    profile_id = getattr(row, "profileId", None) if row else None
    _vlog("profile id lookup media_type=%s profile_id=%s", media_type, profile_id)
    return profile_id


def _language_from_profile_item(item):
    try:
        from languages.get_languages import alpha3_from_alpha2
        from subtitles.utils import _get_lang_obj

        alpha3 = alpha3_from_alpha2(item.get("language"))
        language = _get_lang_obj(alpha3)
        if item.get("forced") == "True":
            language = Language.rebuild(language, forced=True)
        if item.get("hi") == "True":
            language = Language.rebuild(language, hi=True)
        return language
    except Exception as error:
        _vlog("profile language item skipped item=%s error=%r", item, error)
        logger.debug("aiproxy could not build profile language from item", exc_info=True)
        return None


def _search_builtin_subtitles(video, languages):
    media_type = "series" if isinstance(video, Episode) else "movie"
    try:
        providers = _builtin_provider_names()
        if not providers:
            _vlog("builtin search skipped: no configured providers")
            return media_type, []

        _vlog("builtin search providers=%s media_type=%s", providers, media_type)

        pool = _builtin_pool(media_type, providers)

        from subliminal_patch.core_persistent import list_all_subtitles

        subtitles_by_video = list_all_subtitles([video], set(languages), pool)
        subtitles = subtitles_by_video.get(video, [])
        _vlog("builtin search raw_count=%s", len(subtitles))
        return media_type, subtitles
    except Exception as error:
        logger.exception("aiproxy builtin search failed: %r", error)
        return media_type, []


def _list_builtin_subtitles(video, languages, media_type, original_subtitles, max_results=None):
    if not original_subtitles:
        return []

    wrapped = []
    for original in original_subtitles:
        candidate = _candidate_from_builtin(original, video, languages, media_type)
        if candidate is None:
            continue
        wrapped.append(AIProxySubtitle(_language_from_candidate(candidate, original.language), candidate))

    wrapped = sorted(wrapped, key=lambda item: item.score or 0, reverse=True)
    if max_results is None:
        return wrapped
    return wrapped[:max_results]


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
        _vlog("builtin download complete provider=%s candidate_id=%s format=%s bytes=%s", subtitle.source_provider, subtitle.candidate_id, subtitle.format, len(subtitle.content or b""))
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
    except Exception as error:
        _vlog("builtin strict reject provider=%s reason=%r", getattr(subtitle, "provider_name", None), error)
        logger.debug("aiproxy could not score builtin subtitle: %r", subtitle, exc_info=True)
        return None

    threshold = _builtin_threshold(media_type)
    if score < threshold:
        _vlog(
            "builtin strict reject provider=%s score=%s threshold=%s matches=%s release=%s",
            getattr(subtitle, "provider_name", None),
            score,
            threshold,
            sorted(matches),
            _release_info(subtitle),
        )
        return None

    source_provider = getattr(subtitle, "provider_name", None)
    if not source_provider or source_provider == "aiproxy":
        _vlog("builtin strict reject invalid source_provider=%s", source_provider)
        return None

    try:
        original_payload = codecs.encode(pickle.dumps(subtitle.make_picklable()), "base64").decode()
    except Exception:
        _vlog("builtin strict reject provider=%s reason=pickle_failed", source_provider)
        logger.debug("aiproxy could not pickle builtin subtitle: %r", subtitle, exc_info=True)
        return None

    language = getattr(subtitle, "language", None)
    release_info = _release_info(subtitle)
    provider_id = str(getattr(subtitle, "id", "") or "unknown")
    digest = hashlib.sha256(f"{source_provider}:{provider_id}".encode("utf-8")).hexdigest()[:16]
    candidate_id = f"builtin:{source_provider}:{digest}"
    _vlog("builtin strict accept provider=%s score=%s matches=%s release=%s", source_provider, score, sorted(matches), release_info)
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
        _vlog("AI relaxed reject provider=%s reason=language_mismatch language=%s", getattr(subtitle, "provider_name", None), _language_summary(language))
        return None

    try:
        matches = set(subtitle.get_matches(video))
    except Exception:
        logger.debug("aiproxy could not get AI fallback matches: %r", subtitle, exc_info=True)
        return None

    if not _has_id_match(matches):
        _vlog("AI relaxed reject provider=%s reason=no_id_or_hash_match matches=%s", getattr(subtitle, "provider_name", None), sorted(matches))
        return None

    source_provider = getattr(subtitle, "provider_name", None)
    if not source_provider or source_provider == "aiproxy":
        _vlog("AI relaxed reject invalid source_provider=%s", source_provider)
        return None

    try:
        original_payload = codecs.encode(pickle.dumps(subtitle.make_picklable()), "base64").decode()
    except Exception:
        _vlog("AI relaxed reject provider=%s reason=pickle_failed", source_provider)
        logger.debug("aiproxy could not pickle AI fallback subtitle: %r", subtitle, exc_info=True)
        return None

    rule_score = _relaxed_percent_score(subtitle, video, matches, media_type)
    provider_id = str(getattr(subtitle, "id", "") or "unknown")
    digest = hashlib.sha256(
        f"ai:{source_provider}:{provider_id}:{str(language)}".encode("utf-8")
    ).hexdigest()[:16]
    _vlog("AI relaxed candidate provider=%s rule_score=%s matches=%s release=%s", source_provider, rule_score, sorted(matches), _release_info(subtitle))
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


def _candidate_for_worker_search_request(candidate):
    return {
        "id": candidate["id"],
        "provider": candidate.get("provider", "builtin"),
        "source_provider": candidate.get("source_provider"),
        "provider_id": candidate["provider_id"],
        "origin": candidate.get("origin"),
        "original_subtitle": candidate.get("original_subtitle"),
        "media_type": candidate.get("media_type"),
        "language": candidate["language"],
        "rule_score": candidate.get("rule_score", 0),
        "matches": candidate.get("matches", []),
        "release_info": candidate.get("release_info", []),
        "page_link": candidate.get("page_link"),
        "uploader": candidate.get("uploader"),
        "forced": candidate.get("forced", False),
        "hearing_impaired": candidate.get("hearing_impaired", False),
        "original_format": candidate.get("original_format", True),
        "hash_verifiable": candidate.get("hash_verifiable", False),
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


def _missing_languages(languages, subtitles):
    missing = []
    for language in languages:
        if any(_matching_requested_language(getattr(subtitle, "language", None), [language]) for subtitle in subtitles):
            continue
        missing.append(language)
    return missing


def _dedupe_languages(languages):
    deduped = []
    seen = set()
    for language in languages:
        key = _language_key(language)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(language)
    return deduped


def _language_key(language):
    return (
        getattr(language, "basename", None) or getattr(language, "alpha3", None) or str(language),
        bool(getattr(language, "forced", False)),
        bool(getattr(language, "hi", False)),
    )


def _dedupe_subtitles(subtitles):
    deduped = []
    seen = set()
    for subtitle in subtitles:
        key = getattr(subtitle, "candidate_id", None) or id(subtitle)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(subtitle)
    return deduped


def _sorted_subtitles(subtitles, max_results):
    return sorted(subtitles, key=lambda item: item.score or 0, reverse=True)[:max_results]


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


def _video_summary(video):
    if isinstance(video, Episode):
        title = getattr(video, "series", None)
        season = getattr(video, "season", None)
        episode = getattr(video, "episode", None)
        label = f"series={title!r} S{season}E{episode}"
    else:
        label = f"movie={getattr(video, 'title', None)!r} year={getattr(video, 'year', None)}"
    original_name = getattr(video, "original_name", None)
    original_path = getattr(video, "original_path", None)
    filename = original_name or (PurePath(original_path).name if original_path else None)
    return {
        "label": label,
        "file": filename,
        "imdb": getattr(video, "series_imdb_id", None) or getattr(video, "imdb_id", None),
        "tvdb": getattr(video, "tvdb_id", None),
    }


def _serialize_language(language):
    return {
        "alpha3": getattr(language, "alpha3", None),
        "basename": getattr(language, "basename", None),
        "ietf": str(language),
        "forced": bool(getattr(language, "forced", False)),
        "hi": bool(getattr(language, "hi", False)),
    }


def _language_summary(language):
    if language is None:
        return None
    return {
        "alpha3": getattr(language, "alpha3", None),
        "basename": getattr(language, "basename", None),
        "forced": bool(getattr(language, "forced", False)),
        "hi": bool(getattr(language, "hi", False)),
    }


def _candidate_summary(candidate):
    return {
        "id": candidate.get("id"),
        "provider": candidate.get("source_provider") or candidate.get("provider"),
        "score": candidate.get("score"),
        "rule_score": candidate.get("rule_score"),
        "ai_score": candidate.get("ai_score"),
        "language": candidate.get("language"),
        "matches": candidate.get("matches"),
        "release": candidate.get("release_info"),
    }


def _subtitle_summary(subtitle):
    return {
        "id": getattr(subtitle, "candidate_id", None),
        "origin": getattr(subtitle, "origin", None),
        "provider": getattr(subtitle, "source_provider", None),
        "score": getattr(subtitle, "score", None),
        "rule_score": getattr(subtitle, "rule_score", None),
        "ai_score": getattr(subtitle, "ai_score", None),
        "language": _language_summary(getattr(subtitle, "language", None)),
        "matches": sorted(getattr(subtitle, "matches", []) or []),
        "release": getattr(subtitle, "releases", None),
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


def _verbose_enabled():
    return _env_bool("AIPROXY_VERBOSE", False)


def _vlog(message, *args):
    if _verbose_enabled():
        logger.info("BAZARR AIProxy: " + message, *args)
