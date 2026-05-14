from __future__ import annotations

import logging

from .bazarr_config import load_bazarr_config, proxy_url, subsource_api_key
from .cache import CandidateCache
from .logging_utils import vlog
from .models import LanguageRequest, ScoreRequest, ScoredCandidate, SearchRequest, SubtitleCandidate
from .openai_client import ai_score_candidate
from .scoring import score_candidate
from .settings import Settings
from .subsource import SubsourceClient


logger = logging.getLogger(__name__)


class SearchService:
    def __init__(self, settings: Settings, cache: CandidateCache):
        self.settings = settings
        self.cache = cache

    def search(self, request: SearchRequest) -> list[SubtitleCandidate]:
        config = load_bazarr_config(self.settings.bazarr_config_path)
        api_key = subsource_api_key(config, self.settings.subsource_api_key)

        vlog(
            logger,
            "worker enhanced search start media_type=%s title=%r season=%s episode=%s languages=%s provider_candidates=%s config_loaded=%s proxy_configured=%s",
            request.video.media_type,
            request.video.title,
            request.video.season,
            request.video.episode,
            [language.model_dump() for language in request.languages],
            len(request.candidates),
            bool(config),
            bool(proxy_url(config)),
        )

        candidates = self._score_provided_candidates(request)

        if api_key:
            client = SubsourceClient(api_key=api_key, timeout=self.settings.http_timeout, proxy=proxy_url(config))
            try:
                for language in request.languages:
                    language_candidates = self._search_language(client, request, language)
                    vlog(logger, "worker enhanced search language=%s accepted=%s", language.model_dump(), len(language_candidates))
                    candidates.extend(language_candidates)
            finally:
                client.close()
        else:
            logger.warning("Subsource API key missing; skipping Subsource worker fallback")

        unique = {candidate.id: candidate for candidate in candidates}
        results = sorted(unique.values(), key=lambda item: item.score, reverse=True)[: self.settings.max_candidates]
        logger.info("worker enhanced search complete unique=%s returned=%s", len(unique), len(results))
        return results

    def _score_provided_candidates(self, request: SearchRequest) -> list[SubtitleCandidate]:
        results = []
        for candidate in request.candidates:
            if not _language_matches(candidate.language, request.languages):
                continue

            raw = candidate.model_dump()
            raw["provider"] = candidate.source_provider or candidate.provider
            raw["language_alpha3"] = candidate.language.alpha3
            raw["forced"] = candidate.forced
            raw["hearing_impaired"] = candidate.hearing_impaired
            ai_score = ai_score_candidate(self.settings, request.video, raw, candidate.rule_score, force=True)
            final_score = ai_score if ai_score is not None else candidate.rule_score
            if ai_score is None or ai_score < self.settings.ai_threshold:
                vlog(
                    logger,
                    "worker provider candidate reject id=%s provider=%s rule_score=%s ai_score=%s final_score=%s threshold=%s matches=%s release=%s",
                    candidate.id,
                    candidate.source_provider or candidate.provider,
                    candidate.rule_score,
                    ai_score,
                    final_score,
                    self.settings.ai_threshold,
                    candidate.matches,
                    candidate.release_info,
                )
                continue

            raw["score"] = final_score
            raw["rule_score"] = candidate.rule_score
            raw["ai_score"] = ai_score
            raw["matches"] = candidate.matches
            self.cache.set(candidate.id, raw)
            vlog(
                logger,
                "worker provider candidate accept id=%s provider=%s score=%s rule_score=%s ai_score=%s matches=%s release=%s",
                candidate.id,
                candidate.source_provider or candidate.provider,
                final_score,
                candidate.rule_score,
                ai_score,
                candidate.matches,
                candidate.release_info,
            )
            results.append(
                SubtitleCandidate(
                    id=candidate.id,
                    provider=candidate.provider,
                    provider_id=candidate.provider_id,
                    language=candidate.language,
                    origin=candidate.origin,
                    source_provider=candidate.source_provider,
                    original_subtitle=candidate.original_subtitle,
                    media_type=candidate.media_type,
                    score=final_score,
                    rule_score=candidate.rule_score,
                    ai_score=ai_score,
                    matches=candidate.matches,
                    release_info=candidate.release_info,
                    page_link=candidate.page_link,
                    uploader=candidate.uploader,
                    forced=candidate.forced,
                    hearing_impaired=candidate.hearing_impaired,
                    original_format=candidate.original_format,
                    hash_verifiable=candidate.hash_verifiable,
                    extra={"source_provider": candidate.source_provider or candidate.provider},
                )
            )
        if request.candidates:
            logger.info("worker provider candidates complete input=%s accepted=%s", len(request.candidates), len(results))
        return results

    def score(self, request: ScoreRequest) -> list[ScoredCandidate]:
        logger.info("worker AI score start candidates=%s threshold=%s", len(request.candidates), self.settings.ai_threshold)
        scored = []
        for candidate in request.candidates:
            raw = candidate.model_dump()
            raw["language_alpha3"] = candidate.language.alpha3
            raw["forced"] = candidate.forced
            raw["hearing_impaired"] = candidate.hearing_impaired
            ai_score = ai_score_candidate(self.settings, request.video, raw, candidate.rule_score, force=True)
            final_score = ai_score if ai_score is not None else candidate.rule_score
            accepted = ai_score is not None and ai_score >= self.settings.ai_threshold
            vlog(
                logger,
                "worker AI score candidate id=%s provider=%s rule_score=%s ai_score=%s final_score=%s accepted=%s matches=%s release=%s",
                candidate.id,
                candidate.provider,
                candidate.rule_score,
                ai_score,
                final_score,
                accepted,
                candidate.matches,
                candidate.release_info,
            )
            scored.append(
                ScoredCandidate(
                    id=candidate.id,
                    accepted=accepted,
                    score=final_score,
                    rule_score=candidate.rule_score,
                    ai_score=ai_score,
                )
            )
        return scored

    def _search_language(
        self,
        client: SubsourceClient,
        request: SearchRequest,
        language: LanguageRequest,
    ) -> list[SubtitleCandidate]:
        results = []
        for raw in client.search(request.video, language):
            rule = score_candidate(request.video, language, raw)
            ai_score = ai_score_candidate(self.settings, request.video, raw, rule.score)
            final_score = ai_score if ai_score is not None else rule.score
            if final_score < self.settings.ai_threshold:
                vlog(
                    logger,
                    "worker enhanced reject provider=%s provider_id=%s rule_score=%s ai_score=%s final_score=%s threshold=%s matches=%s release=%s",
                    raw.get("provider"),
                    raw.get("provider_id"),
                    rule.score,
                    ai_score,
                    final_score,
                    self.settings.ai_threshold,
                    rule.matches,
                    raw.get("release_info"),
                )
                continue

            raw["score"] = final_score
            raw["rule_score"] = rule.score
            raw["ai_score"] = ai_score
            raw["matches"] = rule.matches
            self.cache.set(raw["id"], raw)
            vlog(
                logger,
                "worker enhanced accept provider=%s provider_id=%s score=%s rule_score=%s ai_score=%s matches=%s release=%s",
                raw.get("provider"),
                raw.get("provider_id"),
                final_score,
                rule.score,
                ai_score,
                rule.matches,
                raw.get("release_info"),
            )
            results.append(
                SubtitleCandidate(
                    id=raw["id"],
                    provider=raw["provider"],
                    provider_id=raw["provider_id"],
                    language=LanguageRequest(
                        alpha3=raw.get("language_alpha3"),
                        forced=raw.get("forced", False),
                        hi=raw.get("hearing_impaired", False),
                    ),
                    score=final_score,
                    rule_score=rule.score,
                    ai_score=ai_score,
                    matches=rule.matches,
                    release_info=raw.get("release_info") or [],
                    page_link=raw.get("page_link"),
                    uploader=raw.get("uploader"),
                    forced=raw.get("forced", False),
                    hearing_impaired=raw.get("hearing_impaired", False),
                    extra={"source_provider": "subsource"},
                )
            )
        return results


def _language_matches(language: LanguageRequest, requested_languages: list[LanguageRequest]) -> bool:
    for requested in requested_languages:
        if requested.alpha3 and language.alpha3 != requested.alpha3:
            continue
        if requested.basename and language.basename and language.basename != requested.basename:
            continue
        if requested.forced and not language.forced:
            continue
        if not requested.forced and language.forced:
            continue
        if requested.hi and not language.hi:
            continue
        return True
    return False
