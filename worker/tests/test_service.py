import unittest
import sys
import types
from unittest.mock import patch

sys.modules.setdefault("yaml", types.SimpleNamespace(safe_load=lambda handle: {}))

from app.cache import CandidateCache
from app.models import LanguageRequest, ScoreCandidateRequest, SearchRequest, VideoRequest
from app.service import SearchService
from app.settings import Settings


def _settings() -> Settings:
    return Settings(
        bazarr_config_path="/tmp/missing-bazarr-config.yaml",
        subsource_api_key=None,
        openai_base_url="http://local/v1",
        openai_api_key="dummy",
        openai_model="local-model",
        ai_enabled=True,
        ai_threshold=85,
        ai_lower_bound=60,
        ai_upper_bound=90,
        max_candidates=25,
        http_timeout=30,
        log_level="INFO",
        verbose_logs=False,
    )


class ServiceTests(unittest.TestCase):
    def test_search_accepts_ai_scored_provider_candidates_without_subsource_key(self):
        service = SearchService(settings=_settings(), cache=CandidateCache())
        language = LanguageRequest(alpha3="vie", basename="vi")
        request = SearchRequest(
            video=VideoRequest(media_type="series", title="Witch Hat Atelier", season=1, episode=1),
            languages=[language],
            candidates=[
                ScoreCandidateRequest(
                    id="builtin-ai:opensubtitlescom:1",
                    provider="builtin",
                    source_provider="opensubtitlescom",
                    provider_id="1",
                    origin="builtin",
                    original_subtitle="pickled-subtitle",
                    media_type="series",
                    language=language,
                    rule_score=50,
                    matches=["series", "season", "episode"],
                    release_info=["Witch.Hat.Atelier.S01E01.1080p"],
                )
            ],
        )

        with patch("app.service.ai_score_candidate", return_value=91):
            results = service.search(request)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].origin, "builtin")
        self.assertEqual(results[0].source_provider, "opensubtitlescom")
        self.assertEqual(results[0].original_subtitle, "pickled-subtitle")
        self.assertEqual(results[0].score, 91)

    def test_search_rejects_low_ai_provider_candidate(self):
        service = SearchService(settings=_settings(), cache=CandidateCache())
        language = LanguageRequest(alpha3="vie", basename="vi")
        request = SearchRequest(
            video=VideoRequest(media_type="series", title="Witch Hat Atelier", season=1, episode=1),
            languages=[language],
            candidates=[
                ScoreCandidateRequest(
                    id="builtin-ai:opensubtitlescom:1",
                    provider="builtin",
                    source_provider="opensubtitlescom",
                    provider_id="1",
                    origin="builtin",
                    media_type="series",
                    language=language,
                    rule_score=50,
                )
            ],
        )

        with patch("app.service.ai_score_candidate", return_value=30):
            results = service.search(request)

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
