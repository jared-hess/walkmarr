"""Radarr provider client and media item builder."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from walkmarr.exceptions import ProviderError
from walkmarr.models import MediaItem, PathMapping
from walkmarr.paths import build_movie_output_path, map_remote_path_to_local


class RadarrProvider:
    """Radarr API client for listing and selecting movies."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        timeout_seconds: float = 20.0,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def list_movies(self) -> list[dict[str, Any]]:
        """Fetch all Radarr movies."""
        payload = self._get_json("/api/v3/movie")
        if not isinstance(payload, list):
            raise ProviderError("Radarr returned unexpected movie payload")
        return [item for item in payload if isinstance(item, dict)]

    def get_movie_by_id(self, movie_id: int) -> dict[str, Any]:
        """Fetch one Radarr movie by ID."""
        payload = self._get_json(f"/api/v3/movie/{movie_id}")
        if not isinstance(payload, dict):
            raise ProviderError(f"Radarr returned unexpected movie payload for id {movie_id}")
        return payload

    def match_movie(self, title: str, movies: list[dict[str, Any]]) -> dict[str, Any]:
        """Match movie by exact title first, then case-insensitive title."""
        exact = [m for m in movies if m.get("title") == title]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            candidates = ", ".join(str(m.get("title")) for m in exact)
            raise ProviderError(f"Ambiguous Radarr exact title match for '{title}': {candidates}")

        folded = [m for m in movies if str(m.get("title", "")).casefold() == title.casefold()]
        if len(folded) == 1:
            return folded[0]
        if len(folded) > 1:
            candidates = ", ".join(str(m.get("title")) for m in folded)
            raise ProviderError(
                f"Ambiguous Radarr case-insensitive title match for '{title}': {candidates}"
            )

        raise ProviderError(f"No matching Radarr movie found for '{title}'")

    def build_media_item(
        self,
        *,
        movie: dict[str, Any],
        profile_name: str,
        path_mappings: list[PathMapping],
        output_root: Path,
        allow_unmapped_existing_local: bool = False,
    ) -> MediaItem:
        """Build normalized MediaItem for a selected movie."""
        movie_title = movie.get("title")
        if not isinstance(movie_title, str) or not movie_title:
            raise ProviderError("Radarr movie entry missing title")

        year: int | None = None
        year_raw = movie.get("year")
        if isinstance(year_raw, int):
            year = year_raw

        release_date = _first_release_date(movie)
        if year is None and release_date is not None:
            year = int(release_date[:4])

        genre: str | None = None
        genres_raw = movie.get("genres")
        if isinstance(genres_raw, list):
            for value in genres_raw:
                if isinstance(value, str) and value.strip():
                    genre = value.strip()
                    break
        artwork_url = self.poster_url(movie)

        movie_file = movie.get("movieFile")
        if not isinstance(movie_file, dict):
            raise ProviderError(f"Radarr movie '{movie_title}' has no movieFile")

        remote_path = movie_file.get("path")
        if not isinstance(remote_path, str) or not remote_path:
            raise ProviderError(f"Radarr movie '{movie_title}' has no movie file path")

        mapped_local = map_remote_path_to_local(
            remote_path,
            path_mappings,
            allow_unmapped_existing_local=allow_unmapped_existing_local,
        )

        output_path = build_movie_output_path(output_root, movie_title=movie_title, year=year)

        return MediaItem(
            kind="movie",
            source_path=mapped_local,
            output_path=output_path,
            profile_name=profile_name,
            title=movie_title,
            remote_source_path=remote_path,
            movie_title=movie_title,
            year=year,
            release_date=release_date,
            genre=genre,
            artwork_url=artwork_url,
        )

    def poster_url(self, movie: dict[str, Any]) -> str | None:
        """Return the best poster URL from a Radarr movie payload."""
        return _poster_url(movie, self.url)

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = {"X-Api-Key": self.api_key}
        url = f"{self.url}{path}"
        try:
            response = self.session.get(url, headers=headers, params=params, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise ProviderError(f"Radarr request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(f"Radarr API error {response.status_code} for {path}")

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Radarr returned non-JSON response for {path}") from exc


def _first_release_date(movie: dict[str, Any]) -> str | None:
    for key in ("inCinemas", "digitalRelease", "physicalRelease"):
        parsed = _extract_iso_date(movie.get(key))
        if parsed is not None:
            return parsed
    return None


def _poster_url(payload: dict[str, Any], base_url: str) -> str | None:
    images = payload.get("images")
    if not isinstance(images, list):
        return None

    poster_images = [image for image in images if _image_cover_type(image) == "poster"]
    for image in [*poster_images, *images]:
        if not isinstance(image, dict):
            continue
        url = _image_url(image, base_url)
        if url is not None:
            return url
    return None


def _image_cover_type(image: object) -> str | None:
    if not isinstance(image, dict):
        return None
    cover_type = image.get("coverType")
    if not isinstance(cover_type, str):
        return None
    return cover_type.casefold().strip()


def _image_url(image: dict[object, object], base_url: str) -> str | None:
    for key in ("remoteUrl", "url"):
        value = image.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned = value.strip()
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            return cleaned
        return f"{base_url}/{cleaned.lstrip('/')}"
    return None


def _extract_iso_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) >= 10 and cleaned[4] == "-" and cleaned[7] == "-":
        candidate = cleaned[:10]
        try:
            datetime.strptime(candidate, "%Y-%m-%d")
        except ValueError:
            return None
        return candidate
    return None
