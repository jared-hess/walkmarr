"""Core conversion pipeline orchestration."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from walkmarr.config import sonarr_specials_show_name
from walkmarr.convert.video import (
    ConversionPlan,
    build_ffmpeg_command,
    probe_media,
    require_binary,
    run_ffmpeg,
)
from walkmarr.exceptions import ConversionError, TaggingError, WalkmarrError
from walkmarr.models import AppConfig, MediaItem, VideoProfile
from walkmarr.tag.mp4 import (
    build_movie_tag_command,
    build_tv_tag_command,
    detect_atomicparsley_binary,
    run_atomicparsley,
)


@dataclass(frozen=True)
class ProcessResult:
    """Result of processing one media item."""

    status: str
    output_path: Path


def ensure_required_tools() -> str:
    """Ensure required binaries are present and return AtomicParsley name."""
    require_binary("ffmpeg")
    require_binary("ffprobe")
    return detect_atomicparsley_binary()


def process_media_item(
    *,
    config: AppConfig,
    media_item: MediaItem,
    provider_name: str,
    profile: VideoProfile,
    atomicparsley_bin: str,
    console: Console,
    dry_run: bool,
    overwrite: bool,
) -> ProcessResult:
    """Process one media item through inspect, convert, tag, and finalize."""
    if not media_item.source_path.exists():
        raise WalkmarrError(f"Source file does not exist after mapping: {media_item.source_path}")

    final_output = media_item.output_path
    if final_output.exists() and not overwrite:
        return ProcessResult(status="skipped", output_path=final_output)

    probe = probe_media(media_item.source_path)
    tmp_output = final_output.with_name(f"{final_output.stem}.tmp.mp4")
    ffmpeg_plan = build_ffmpeg_command(
        source_path=media_item.source_path,
        tmp_output_path=tmp_output,
        profile=profile,
        probe=probe,
    )
    tag_command, metadata = _build_tag_command(
        config=config,
        media_item=media_item,
        atomicparsley_bin=atomicparsley_bin,
        media_path=tmp_output,
    )

    _print_conversion_plan(
        console=console,
        media_item=media_item,
        provider_name=provider_name,
        metadata=metadata,
        ffmpeg_plan=ffmpeg_plan,
        tag_command=tag_command,
    )

    if dry_run:
        return ProcessResult(status="dry-run", output_path=final_output)

    final_output.parent.mkdir(parents=True, exist_ok=True)

    if tmp_output.exists():
        tmp_output.unlink()

    try:
        run_ffmpeg(ffmpeg_plan.command)
        run_atomicparsley(tag_command)
        tmp_output.replace(final_output)
    except (ConversionError, TaggingError, OSError) as exc:
        if tmp_output.exists():
            tmp_output.unlink()
        raise WalkmarrError(f"Failed processing '{media_item.source_path}': {exc}") from exc

    return ProcessResult(status="converted", output_path=final_output)


def _build_tag_command(
    *,
    config: AppConfig,
    media_item: MediaItem,
    atomicparsley_bin: str,
    media_path: Path,
) -> tuple[list[str], dict[str, str | int | None]]:
    if media_item.kind == "episode":
        if media_item.series_title is None:
            raise WalkmarrError("Episode media item is missing series_title")
        if media_item.season_number is None or media_item.episode_number is None:
            raise WalkmarrError("Episode media item is missing season/episode number")

        tag_show_title = media_item.series_title
        tag_season = media_item.season_number
        tag_episode = media_item.episode_number

        if media_item.season_number == 0:
            specials_show_name = sonarr_specials_show_name(config, media_item.series_title)
            if specials_show_name:
                tag_show_title = specials_show_name
                tag_season = 1
                tag_episode = media_item.episode_number

        command = build_tv_tag_command(
            atomicparsley_bin,
            media_path,
            episode_title=media_item.title,
            show_title=tag_show_title,
            season_number=tag_season,
            episode_number=tag_episode,
        )
        metadata: dict[str, str | int | None] = {
            "kind": "TV Show",
            "title": media_item.title,
            "show": tag_show_title,
            "season": tag_season,
            "episode": tag_episode,
            "episode_id": f"S{tag_season:02d}E{tag_episode:02d}",
            "artist": tag_show_title,
            "album": tag_show_title,
        }
        return command, metadata

    if media_item.kind == "movie":
        movie_title = media_item.movie_title or media_item.title
        command = build_movie_tag_command(
            atomicparsley_bin,
            media_path,
            movie_title=movie_title,
            year=media_item.year,
        )
        metadata = {
            "kind": "Movie",
            "title": movie_title,
            "year": media_item.year,
            "artist": movie_title,
            "album": movie_title,
        }
        return command, metadata

    raise WalkmarrError(f"Unsupported media kind: {media_item.kind}")


def _print_conversion_plan(
    *,
    console: Console,
    media_item: MediaItem,
    provider_name: str,
    metadata: dict[str, str | int | None],
    ffmpeg_plan: ConversionPlan,
    tag_command: list[str],
) -> None:
    plan = ffmpeg_plan
    source_label = media_item.remote_source_path or str(media_item.source_path)

    console.print("Converting:")
    console.print(f"  Provider: {provider_name.capitalize()}")
    console.print(f"  Source: {source_label}")
    console.print(f"  Mapped local path: {media_item.source_path}")
    console.print(f"  Output: {media_item.output_path}")

    if media_item.kind == "episode":
        console.print(f"  Show: {metadata['show']}")
        console.print(f"  Season: {metadata['season']}")
        console.print(f"  Episode: {metadata['episode']}")
        console.print(f"  Title: {media_item.title}")
    else:
        console.print(f"  Movie: {metadata['title']}")
        console.print(f"  Year: {metadata['year']}")

    console.print(f"  Profile: {media_item.profile_name}")
    console.print(f"  CRF: {plan.command[plan.command.index('-crf') + 1]}")
    source_vbit = plan.source_video_bitrate_kbps
    vbit_display = f"{source_vbit} kbps" if source_vbit is not None else "unknown"
    console.print(f"  Source vbit: {vbit_display}")
    console.print(f"  Maxrate: {plan.maxrate_kbps}k")
    selected_language = plan.selected_audio_language or "unknown"
    console.print(f"  Audio stream: {plan.selected_audio_index} ({selected_language})")
    console.print(f"  Audio: {plan.audio_channels}ch @ {plan.audio_bitrate_kbps}k")
    console.print(f"  Filter: {plan.filter_expr}")
    console.print(f"  Metadata: {metadata}")
    console.print(f"  ffmpeg command: {shlex.join(plan.command)}")
    console.print(f"  AtomicParsley command: {shlex.join(tag_command)}")
