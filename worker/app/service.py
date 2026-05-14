from __future__ import annotations

import logging

from .bazarr_config import load_bazarr_config, proxy_url, subsource_api_key
from .cache import CandidateCache
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
        if not api_key:
            logger.warning("Subsource API key missing; returning no aiproxy results")
            return []

        client = SubsourceClient(api_key=api_key, timeout=self.settings.http_timeout, proxy=proxy_url(config))
        try:
            candidates = []
            for language in request.languages:
                candidates.extend(self._search_language(client, request, language))
        finally:
            client.close()

        unique = {candidate.id: candidate for candidate in candidates}
        return sorted(unique.values(), key=lambda item: item.score, reverse=True)[: self.settings.max_candidates]

    def score(self, request: ScoreRequest) -> list[ScoredCandidate]:
        scored = []
        for candidate in request.candidates:
            raw = candidate.model_dump()
            raw["language_alpha3"] = candidate.language.alpha3
            raw["forced"] = candidate.forced
            raw["hearing_impaired"] = candidate.hearing_impaired
            ai_score = ai_score_candidate(self.settings, request.video, raw, candidate.rule_score, force=True)
            final_score = ai_score if ai_score is not None else candidate.rule_score
            scored.append(
                ScoredCandidate(
                    id=candidate.id,
                    accepted=ai_score is not None and ai_score >= self.settings.ai_threshold,
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
                continue

            raw["score"] = final_score
            raw["rule_score"] = rule.score
            raw["ai_score"] = ai_score
            raw["matches"] = rule.matches
            self.cache.set(raw["id"], raw)
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
