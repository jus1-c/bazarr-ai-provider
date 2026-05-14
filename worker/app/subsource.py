from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from pathlib import PurePosixPath

import httpx

from .language_map import to_subsource_language
from .models import LanguageRequest, VideoRequest
from .scoring import normalize_title, parse_season_episode, title_aliases


logger = logging.getLogger(__name__)
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}


class SubsourceClient:
    def __init__(self, api_key: str, timeout: float = 30.0, proxy: str | None = None):
        self.api_key = api_key
        self.base_url = "https://api.subsource.net/api/v1"
        self.client = httpx.Client(timeout=timeout, proxy=proxy, headers={"User-Agent": "Bazarr-AIProvider/0.1"})

    def close(self) -> None:
        self.client.close()

    def search(self, video: VideoRequest, language: LanguageRequest) -> list[dict]:
        subsource_language = to_subsource_language(language.alpha3)
        if not subsource_language:
            logger.info("Subsource language mapping missing for %s", language.alpha3)
            return []

        movie_ids = self._find_movie_ids(video)
        results: list[dict] = []
        for movie_id in movie_ids:
            params = {
                "api_key": self.api_key,
                "language": subsource_language.lower(),
                "limit": 100,
                "movieId": movie_id,
            }
            if video.media_type == "series":
                if video.season is not None:
                    params["seasonNumber"] = video.season
                if video.episode is not None:
                    params["episodeNumber"] = video.episode

            response = self.client.get(f"{self.base_url}/subtitles", params=params)
            response.raise_for_status()
            data = response.json().get("data") or []
            for item in data:
                candidate = self._candidate_from_item(item, video, language)
                if candidate:
                    results.append(candidate)
        return results

    def download(self, provider_id: str) -> tuple[str, str, bytes]:
        response = self.client.get(
            f"{self.base_url}/subtitles/{provider_id}/download",
            params={"api_key": self.api_key},
        )
        response.raise_for_status()
        content = response.content
        if zipfile.is_zipfile(io.BytesIO(content)):
            return _subtitle_from_zip(content)
        return f"{provider_id}.srt", "srt", content

    def _find_movie_ids(self, video: VideoRequest) -> list[int]:
        ids: list[int] = []
        seen = set()

        if video.imdb_id:
            for item in self._search_titles(video, search_type="imdb", query=video.imdb_id):
                movie_id = item.get("movieId")
                if movie_id and movie_id not in seen:
                    seen.add(movie_id)
                    ids.append(movie_id)

        for alias in _search_title_values(video):
            for item in self._search_titles(video, search_type="text", query=alias):
                if not _title_matches(video, item):
                    continue
                movie_id = item.get("movieId")
                if movie_id and movie_id not in seen:
                    seen.add(movie_id)
                    ids.append(movie_id)
        return ids

    def _search_titles(self, video: VideoRequest, search_type: str, query: str) -> list[dict]:
        params = {"api_key": self.api_key, "searchType": search_type}
        if search_type == "imdb":
            params["imdb"] = query
        else:
            params["q"] = query.lower()
        if video.media_type == "series" and video.season is not None:
            params["season"] = video.season

        response = self.client.get(f"{self.base_url}/movies/search", params=params)
        response.raise_for_status()
        return response.json().get("data") or []

    def _candidate_from_item(self, item: dict, video: VideoRequest, language: LanguageRequest) -> dict | None:
        provider_id = item.get("subtitleId")
        if provider_id is None:
            return None

        release_info = item.get("releaseInfo") or []
        if isinstance(release_info, str):
            release_info = [release_info]

        season, episode = parse_season_episode(release_info)
        if video.media_type == "series":
            season = season if season is not None else video.season
            episode = episode if episode is not None else video.episode

        candidate_id = _candidate_id("subsource", str(provider_id), language.alpha3 or "")
        page_link = item.get("link")
        if page_link and page_link.startswith("/"):
            page_link = f"https://subsource.net{page_link}"

        return {
            "id": candidate_id,
            "provider": "subsource",
            "provider_id": str(provider_id),
            "language_alpha3": language.alpha3,
            "forced": _is_forced(item),
            "hearing_impaired": _is_hi(item),
            "release_info": release_info,
            "page_link": page_link,
            "uploader": _uploader(item),
            "season": season,
            "episode": episode,
        }


def _search_title_values(video: VideoRequest) -> list[str]:
    values = []
    for alias in [video.title] + list(video.alternative_titles or []):
        if alias and alias not in values:
            values.append(alias)
    return values


def _title_matches(video: VideoRequest, item: dict) -> bool:
    aliases = title_aliases(video)
    title_values = [item.get("title"), item.get("alternateTitle")]
    normalized = [normalize_title(value) for value in title_values if value]
    if not aliases or not normalized:
        return True
    if video.year and item.get("releaseYear"):
        try:
            if int(item["releaseYear"]) != video.year:
                return False
        except (TypeError, ValueError):
            pass
    return any(alias in title or title in alias for alias in aliases for title in normalized)


def _is_hi(item: dict) -> bool:
    if item.get("hearingImpaired"):
        return True
    commentary = str(item.get("commentary") or "").lower()
    if any(tag in commentary for tag in ("non hi", "non-hi", "non sdh", "non-sdh")):
        return False
    return any(tag in commentary for tag in ("sdh", "closed caption", " cc ", ".cc.", "_cc_", " hi ", ".hi."))


def _is_forced(item: dict) -> bool:
    if item.get("foreignParts"):
        return True
    commentary = str(item.get("commentary") or "").lower()
    return "forced" in commentary or "foreign" in commentary


def _uploader(item: dict) -> str | None:
    uploader_id = item.get("uploaderId")
    for contributor in item.get("contributors") or []:
        if contributor.get("id") == uploader_id:
            return contributor.get("displayname")
    return None


def _candidate_id(provider: str, provider_id: str, language: str) -> str:
    digest = hashlib.sha256(f"{provider}:{provider_id}:{language}".encode("utf-8")).hexdigest()
    return digest[:24]


def _subtitle_from_zip(content: bytes) -> tuple[str, str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for member in archive.infolist():
            name = PurePosixPath(member.filename).name
            suffix = PurePosixPath(name).suffix.lower()
            if not name or suffix not in SUBTITLE_EXTENSIONS:
                continue
            data = archive.read(member)
            if data:
                return name, suffix.lstrip("."), data
    raise ValueError("No supported subtitle file found in Subsource archive")
