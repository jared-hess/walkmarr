"""Core data models for Walkmarr."""

from dataclasses import dataclass
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
    movie_title: str | None = None
    year: int | None = None


@dataclass(frozen=True)
class AppConfig:
    """Validated runtime configuration."""

    providers: dict[str, ProviderConfig]
    path_mappings: list[PathMapping]
    output_roots: dict[str, Path]
    default_profiles: dict[str, str]
    profiles: dict[str, VideoProfile]
    overrides: dict[str, dict[str, dict[str, Any]]]
    staging_mode: Literal["auto", "always", "never"] = "auto"
    staging_directory: Path = Path("/tmp/walkmarr-staging")
    allow_unmapped_existing_local: bool = False
