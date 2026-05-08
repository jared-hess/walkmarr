"""Sonarr provider client and media item builders."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from walkmarr.exceptions import ProviderError
from walkmarr.models import MediaItem, PathMapping
from walkmarr.paths import build_tv_output_path, map_remote_path_to_local


class SonarrProvider:
    """Sonarr API client for listing and selecting series/episodes."""

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

    def list_series(self) -> list[dict[str, Any]]:
        """Fetch all Sonarr series."""
        payload = self._get_json("/api/v3/series")
        if not isinstance(payload, list):
            raise ProviderError("Sonarr returned unexpected series payload")
        return [item for item in payload if isinstance(item, dict)]

    def get_series_by_id(self, series_id: int) -> dict[str, Any]:
        """Fetch one Sonarr series by ID."""
        payload = self._get_json(f"/api/v3/series/{series_id}")
        if not isinstance(payload, dict):
            raise ProviderError(f"Sonarr returned unexpected series payload for id {series_id}")
        return payload

    def match_series(self, title: str, series_list: list[dict[str, Any]]) -> dict[str, Any]:
        """Match a series by exact title first, then case-insensitive title."""
        exact = [s for s in series_list if s.get("title") == title]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            candidates = ", ".join(str(s.get("title")) for s in exact)
            raise ProviderError(f"Ambiguous Sonarr exact title match for '{title}': {candidates}")

        folded = [s for s in series_list if str(s.get("title", "")).casefold() == title.casefold()]
        if len(folded) == 1:
            return folded[0]
        if len(folded) > 1:
            candidates = ", ".join(str(s.get("title")) for s in folded)
            raise ProviderError(
                f"Ambiguous Sonarr case-insensitive title match for '{title}': {candidates}"
            )

        raise ProviderError(f"No matching Sonarr series found for '{title}'")

    def list_episodes(self, series_id: int) -> list[dict[str, Any]]:
        """Fetch episodes for a Sonarr series ID."""
        payload = self._get_json("/api/v3/episode", params={"seriesId": series_id})
        if not isinstance(payload, list):
            raise ProviderError("Sonarr returned unexpected episode payload")
        return [item for item in payload if isinstance(item, dict)]

    def list_episode_files(self, series_id: int) -> list[dict[str, Any]]:
        """Fetch episode file records for a Sonarr series ID."""
        payload = self._get_json("/api/v3/episodefile", params={"seriesId": series_id})
        if not isinstance(payload, list):
            raise ProviderError("Sonarr returned unexpected episodefile payload")
        return [item for item in payload if isinstance(item, dict)]

    def build_media_items(
        self,
        *,
        series_title: str,
        episodes: list[dict[str, Any]],
        episode_files: list[dict[str, Any]],
        profile_name: str,
        path_mappings: list[PathMapping],
        output_root: Path,
        series_genre: str | None = None,
        allow_unmapped_existing_local: bool = False,
    ) -> list[MediaItem]:
        """Build normalized MediaItem list from Sonarr episode metadata."""
        file_id_to_episodes: dict[int, list[dict[str, Any]]] = {}
        for episode in episodes:
            if not episode.get("hasFile"):
                continue
            file_id = episode.get("episodeFileId")
            if not isinstance(file_id, int) or file_id <= 0:
                continue
            file_id_to_episodes.setdefault(file_id, []).append(episode)

        items: list[MediaItem] = []
        for file_record in sorted(episode_files, key=lambda e: int(e.get("id", 0))):
            file_id = file_record.get("id")
            if not isinstance(file_id, int):
                continue

            related_episodes = file_id_to_episodes.get(file_id, [])
            if not related_episodes:
                continue

            sorted_related_episodes = sorted(
                related_episodes,
                key=lambda ep: (int(ep.get("seasonNumber", 0)), int(ep.get("episodeNumber", 0))),
            )
            selected_episode = sorted_related_episodes[0]

            remote_path = file_record.get("path")
            if not isinstance(remote_path, str) or not remote_path:
                continue

            mapped_local = map_remote_path_to_local(
                remote_path,
                path_mappings,
                allow_unmapped_existing_local=allow_unmapped_existing_local,
            )

            season = int(selected_episode.get("seasonNumber", 0))
            episode_num = int(selected_episode.get("episodeNumber", 0))
            episode_end_num: int | None = None
            if len(sorted_related_episodes) > 1:
                same_season_episodes = [
                    int(ep.get("episodeNumber", episode_num))
                    for ep in sorted_related_episodes
                    if int(ep.get("seasonNumber", season)) == season
                ]
                if same_season_episodes:
                    candidate_end = max(same_season_episodes)
                    if candidate_end > episode_num:
                        episode_end_num = candidate_end
            episode_title = str(selected_episode.get("title", ""))
            air_date = _extract_iso_date(selected_episode.get("airDate"))
            if air_date is None:
                air_date = _extract_iso_date(selected_episode.get("airDateUtc"))

            output_path = build_tv_output_path(
                output_root=output_root,
                series_title=series_title,
                season_number=season,
                episode_number=episode_num,
                episode_title=episode_title,
                episode_end_number=episode_end_num,
            )

            episode_id = f"S{season:02d}E{episode_num:02d}"
            if episode_end_num is not None:
                episode_id = f"S{season:02d}E{episode_num:02d}-E{episode_end_num:02d}"

            items.append(
                MediaItem(
                    kind="episode",
                    source_path=mapped_local,
                    output_path=output_path,
                    profile_name=profile_name,
                    title=episode_title,
                    remote_source_path=remote_path,
                    series_title=series_title,
                    season_number=season,
                    episode_number=episode_num,
                    episode_end_number=episode_end_num,
                    episode_id=episode_id,
                    air_date=air_date,
                    genre=series_genre,
                )
            )

        return sorted(
            items,
            key=lambda item: (
                item.series_title or "",
                item.season_number or 0,
                item.episode_number or 0,
            ),
        )

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = {"X-Api-Key": self.api_key}
        url = f"{self.url}{path}"
        try:
            response = self.session.get(url, headers=headers, params=params, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise ProviderError(f"Sonarr request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(f"Sonarr API error {response.status_code} for {path}")

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Sonarr returned non-JSON response for {path}") from exc


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
