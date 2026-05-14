import unittest

from app.models import LanguageRequest, VideoRequest
from app.scoring import normalize_title, parse_season_episode, score_candidate


class ScoringTests(unittest.TestCase):
    def test_normalize_title(self):
        self.assertEqual(normalize_title("Witch Hat Atelier!"), "witch hat atelier")

    def test_parse_season_episode(self):
        self.assertEqual(parse_season_episode(["Group.Show.S01E03.1080p"]), (1, 3))
        self.assertEqual(parse_season_episode(["Show - 1x04"]), (1, 4))

    def test_series_score_has_required_bazarr_matches(self):
        video = VideoRequest(media_type="series", title="Witch Hat Atelier", season=1, episode=2)
        language = LanguageRequest(alpha3="eng")
        candidate = {
            "language_alpha3": "eng",
            "forced": False,
            "hearing_impaired": False,
            "season": 1,
            "episode": 2,
            "release_info": ["Witch.Hat.Atelier.S01E02.1080p.WEB-DL"],
        }
        result = score_candidate(video, language, candidate)
        self.assertGreaterEqual(result.score, 80)
        self.assertTrue({"series", "season", "episode"}.issubset(set(result.matches)))

    def test_forced_request_penalizes_normal_candidate(self):
        video = VideoRequest(media_type="series", title="Witch Hat Atelier", season=1, episode=2)
        language = LanguageRequest(alpha3="eng", forced=True)
        candidate = {
            "language_alpha3": "eng",
            "forced": False,
            "hearing_impaired": False,
            "season": 1,
            "episode": 2,
            "release_info": ["Witch.Hat.Atelier.S01E02.1080p.WEB-DL"],
        }
        result = score_candidate(video, language, candidate)
        self.assertLess(result.score, 85)


if __name__ == "__main__":
    unittest.main()
