from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import LanguageRequest, VideoRequest


WORD_RE = re.compile(r"[a-z0-9]+")
SEASON_EPISODE_PATTERNS = (
    re.compile(r"s(?P<season>\d{1,2})\s*e(?P<episode>\d{1,3})", re.IGNORECASE),
    re.compile(r"(?P<season>\d{1,2})x(?P<episode>\d{1,3})", re.IGNORECASE),
)
EPISODE_LIST_PATTERN = re.compile(
    r"(?:^|\s+-\s+|\s+–\s+|\s+—\s+|\[|\()"
    r"(?:e(?:p(?:isode)?)?\s*)?"
    r"(?P<episodes>\d{1,3}(?:\s*[,+&]\s*\d{1,3})*)"
    r"(?=\s*(?:\]|\)|\[|$|[-_. ]))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ScoreResult:
    score: int
    matches: list[str]


def normalize_title(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(WORD_RE.findall(normalized.lower()))


def title_aliases(video: VideoRequest) -> list[str]:
    values = [video.title] + list(video.alternative_titles or [])
    seen = set()
    aliases = []
    for value in values:
        normalized = normalize_title(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            aliases.append(normalized)
    return aliases


def parse_season_episode(release_info: list[str]) -> tuple[int | None, int | None]:
    for item in release_info:
        for pattern in SEASON_EPISODE_PATTERNS:
            match = pattern.search(item or "")
            if match:
                return int(match.group("season")), int(match.group("episode"))
        episodes = parse_episode_numbers([item])
        if episodes:
            return None, episodes[0]
    return None, None


def parse_episode_numbers(release_info: list[str]) -> list[int]:
    for item in release_info:
        text = item or ""
        for pattern in SEASON_EPISODE_PATTERNS:
            match = pattern.search(text)
            if match:
                return [int(match.group("episode"))]
        match = EPISODE_LIST_PATTERN.search(text)
        if match:
            return [int(value) for value in re.findall(r"\d{1,3}", match.group("episodes"))]
    return []


def score_candidate(
    video: VideoRequest,
    requested_language: LanguageRequest,
    candidate: dict,
) -> ScoreResult:
    score = 0
    matches: set[str] = set()

    if candidate.get("language_alpha3") == requested_language.alpha3:
        score += 20

    forced = bool(candidate.get("forced"))
    hi = bool(candidate.get("hearing_impaired"))
    if forced == requested_language.forced:
        score += 10
    else:
        score -= 25
    if requested_language.hi:
        score += 5 if hi else -20
    else:
        score += 5

    release_info = candidate.get("release_info") or []
    release_text = normalize_title(" ".join(release_info))

    if video.media_type == "series":
        matches.add("series")
        score += 20
        season = candidate.get("season")
        episode = candidate.get("episode")
        episodes = candidate.get("episodes") or ([] if episode is None else [episode])
        if video.season and (season is None or season == video.season):
            score += 15
            matches.add("season")
        elif video.season:
            score -= 30
        if video.episode and (not episodes or video.episode in episodes):
            score += 25
            matches.add("episode")
        elif video.episode:
            score -= 50
    else:
        matches.add("title")
        score += 25
        if video.year and candidate.get("year") == video.year:
            score += 15

    for field, match_name, points in (
        (video.release_group, "release_group", 8),
        (video.resolution, "resolution", 4),
        (video.source, "source", 4),
        (video.video_codec, "video_codec", 3),
        (video.audio_codec, "audio_codec", 3),
        (video.streaming_service, "streaming_service", 3),
    ):
        token = normalize_title(field)
        if token and token in release_text:
            score += points
            matches.add(match_name)

    if hi or not requested_language.hi:
        matches.add("hearing_impaired")

    return ScoreResult(score=max(0, min(100, score)), matches=sorted(matches))
