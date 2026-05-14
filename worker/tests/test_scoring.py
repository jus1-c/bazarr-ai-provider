import unittest

from app.models import LanguageRequest, VideoRequest
from app.scoring import normalize_title, parse_episode_numbers, parse_season_episode, score_candidate


class ScoringTests(unittest.TestCase):
    def test_normalize_title(self):
        self.assertEqual(normalize_title("Witch Hat Atelier!"), "witch hat atelier")

    def test_parse_season_episode(self):
        self.assertEqual(parse_season_episode(["Group.Show.S01E03.1080p"]), (1, 3))
        self.assertEqual(parse_season_episode(["Show - 1x04"]), (1, 4))

    def test_parse_anime_episode_list(self):
        release = "[Erai-raws] Tongari Boushi no Atelier - 01,02 [1080p CR WEB-DL AVC AAC]"
        self.assertEqual(parse_season_episode([release]), (None, 1))
        self.assertEqual(parse_episode_numbers([release]), [1, 2])
        self.assertEqual(parse_episode_numbers(["[Erai-raws] Tongari Boushi no Atelier - 07 [1080p]"]), [7])

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

    def test_wrong_anime_episode_is_rejected(self):
        video = VideoRequest(media_type="series", title="Witch Hat Atelier", season=1, episode=1)
        language = LanguageRequest(alpha3="vie")
        candidate = {
            "language_alpha3": "vie",
            "forced": False,
            "hearing_impaired": False,
            "season": 1,
            "episode": 7,
            "episodes": [7],
            "release_info": ["[Erai-raws] Tongari Boushi no Atelier - 07 [1080p CR WEB-DL AVC AAC]"],
        }
        result = score_candidate(video, language, candidate)
        self.assertLess(result.score, 85)
        self.assertNotIn("episode", result.matches)

    def test_multi_episode_release_can_match_requested_episode(self):
        video = VideoRequest(media_type="series", title="Witch Hat Atelier", season=1, episode=1)
        language = LanguageRequest(alpha3="vie")
        candidate = {
            "language_alpha3": "vie",
            "forced": False,
            "hearing_impaired": False,
            "season": 1,
            "episode": 1,
            "episodes": [1, 2],
            "release_info": ["[Erai-raws] Tongari Boushi no Atelier - 01,02 [1080p CR WEB-DL AVC AAC]"],
        }
        result = score_candidate(video, language, candidate)
        self.assertGreaterEqual(result.score, 85)
        self.assertIn("episode", result.matches)


if __name__ == "__main__":
    unittest.main()
