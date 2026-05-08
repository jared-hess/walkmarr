"""Core data models for Walkmarr."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class PathMapping:
    """Maps provider-visible path roots to local filesystem roots."""

    remote: str
    local: Path


@dataclass(frozen=True)
class ProviderConfig:
    """Connection config for a media provider."""

    url: str
    api_key_env: str | None = None
    api_key: str | None = None


@dataclass(frozen=True)
class VideoProfile:
    """Video conversion profile options."""

    crf: int
    maxrate_floor_kbps: int
    maxrate_cap_kbps: int
    bitrate_multiplier: float
    audio_bitrate_mono_kbps: int
    audio_bitrate_stereo_kbps: int
    max_width: int
    h264_profile: str
    h264_level: str
    preferred_audio_languages: tuple[str, ...] = ("eng",)


@dataclass(frozen=True)
class MediaItem:
    """Normalized media item used by conversion and tagging pipelines."""

    kind: Literal["episode", "movie"]
    source_path: Path
    output_path: Path
    profile_name: str
    title: str
    remote_source_path: str | None = None
    series_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    episode_end_number: int | None = None
    episode_id: str | None = None
    air_date: str | None = None
    movie_title: str | None = None
    year: int | None = None
    release_date: str | None = None
    genre: str | None = None


@dataclass(frozen=True)
class GenreProfileRule:
    """Ordered genre-to-profile selection rule."""

    genres: tuple[str, ...]
    profile: str


@dataclass(frozen=True)
class AppConfig:
    """Validated runtime configuration."""

    providers: dict[str, ProviderConfig]
    path_mappings: list[PathMapping]
    output_roots: dict[str, Path]
    default_profiles: dict[str, str]
    profiles: dict[str, VideoProfile]
    overrides: dict[str, dict[str, dict[str, Any]]]
    genre_profile_map: dict[str, tuple[GenreProfileRule, ...]] = field(
        default_factory=lambda: {"sonarr": (), "radarr": ()}
    )
    staging_mode: Literal["auto", "always", "never"] = "auto"
    staging_directory: Path = Path("/tmp/walkmarr-staging")
    allow_unmapped_existing_local: bool = False
    queue_workers: int = 1
    queue_continue_on_error: bool = True
    queue_start_paused: bool = False
    queue_default_mode: Literal["missing_only", "overwrite"] = "missing_only"
    queue_remember_completed_until_exit: bool = True


class QueueItemStatus(Enum):
    """Queue item lifecycle state."""

    PENDING = "pending"
    EXPANDING = "expanding"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class QueueItem:
    """A queued provider entity that expands to MediaItems at execution time."""

    id: str
    provider: Literal["sonarr", "radarr"]
    provider_item_id: int
    title: str
    year: int | None = None
    mode: Literal["missing_only", "overwrite"] = "missing_only"
    dry_run: bool = False
    status: QueueItemStatus = QueueItemStatus.PENDING
    profile_name: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_files: int | None = None
    completed_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    current_label: str | None = None
    output_root: str | None = None
    last_message: str | None = None
    error: str | None = None
