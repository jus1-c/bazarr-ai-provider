from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LanguageRequest(BaseModel):
    alpha3: str | None = None
    basename: str | None = None
    ietf: str | None = None
    forced: bool = False
    hi: bool = False


class VideoRequest(BaseModel):
    media_type: str
    title: str | None = None
    alternative_titles: list[str] = Field(default_factory=list)
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    imdb_id: str | None = None
    tvdb_id: int | None = None
    original_name: str | None = None
    original_path: str | None = None
    release_group: str | None = None
    resolution: str | None = None
    source: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    streaming_service: str | None = None


class ScoreCandidateRequest(BaseModel):
    id: str
    provider: str
    provider_id: str
    language: LanguageRequest
    origin: str | None = None
    source_provider: str | None = None
    original_subtitle: str | None = None
    media_type: str | None = None
    rule_score: int = 0
    matches: list[str] = Field(default_factory=list)
    release_info: list[str] = Field(default_factory=list)
    page_link: str | None = None
    uploader: str | None = None
    forced: bool = False
    hearing_impaired: bool = False
    original_format: bool = True
    hash_verifiable: bool = False


class SearchRequest(BaseModel):
    video: VideoRequest
    languages: list[LanguageRequest]
    candidates: list[ScoreCandidateRequest] = Field(default_factory=list)


class ScoreRequest(BaseModel):
    video: VideoRequest
    candidates: list[ScoreCandidateRequest]


class ScoredCandidate(BaseModel):
    id: str
    accepted: bool
    score: int
    rule_score: int
    ai_score: int | None = None


class SubtitleCandidate(BaseModel):
    id: str
    provider: str
    provider_id: str
    language: LanguageRequest
    origin: str | None = None
    source_provider: str | None = None
    original_subtitle: str | None = None
    media_type: str | None = None
    score: int
    rule_score: int
    ai_score: int | None = None
    matches: list[str] = Field(default_factory=list)
    release_info: list[str] = Field(default_factory=list)
    page_link: str | None = None
    uploader: str | None = None
    forced: bool = False
    hearing_impaired: bool = False
    original_format: bool = True
    hash_verifiable: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    subtitles: list[SubtitleCandidate]


class ScoreResponse(BaseModel):
    candidates: list[ScoredCandidate]


class DownloadResponse(BaseModel):
    id: str
    provider: str
    filename: str
    format: str
    content_b64: str
