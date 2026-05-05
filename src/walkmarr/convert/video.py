"""ffprobe inspection and ffmpeg command planning/execution."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from walkmarr.exceptions import ConversionError
from walkmarr.models import VideoProfile


@dataclass(frozen=True)
class ProbeInfo:
    """Useful source media characteristics from ffprobe."""

    source_video_bitrate_kbps: int | None
    width: int | None
    height: int | None
    audio_channels: int | None
    audio_map_selector: str = "0:a:0"
    audio_language: str | None = None


@dataclass(frozen=True)
class ConversionPlan:
    """Built ffmpeg command and computed encoding values."""

    command: list[str]
    source_video_bitrate_kbps: int | None
    maxrate_kbps: int
    audio_channels: int
    audio_bitrate_kbps: int
    filter_expr: str


def require_binary(binary_name: str) -> None:
    """Ensure a binary exists in PATH."""
    if shutil.which(binary_name) is None:
        raise ConversionError(f"Required binary not found in PATH: {binary_name}")


def probe_media(source_path: Path, ffprobe_bin: str = "ffprobe") -> ProbeInfo:
    """Inspect source media with ffprobe.

    Args:
        source_path: Input media path.
        ffprobe_bin: ffprobe binary name.

    Returns:
        Parsed probe information.
    """
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,bit_rate,width,height,channels:stream_tags=language:stream_disposition=default",
        "-of",
        "json",
        str(source_path),
    ]

    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ConversionError(f"ffprobe binary not found: {ffprobe_bin}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise ConversionError(f"ffprobe failed for '{source_path}': {stderr}") from exc

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ConversionError(f"Failed to parse ffprobe output for '{source_path}'") from exc

    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        streams = []

    video_stream: dict[str, object] | None = None
    audio_streams: list[dict[str, object]] = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video_stream is None:
            video_stream = stream
        elif codec_type == "audio":
            audio_streams.append(stream)

    selected_audio_stream = _select_preferred_audio_stream(audio_streams)

    video_bitrate_kbps = _parse_kbps(video_stream.get("bit_rate") if video_stream else None)
    width = _parse_int(video_stream.get("width") if video_stream else None)
    height = _parse_int(video_stream.get("height") if video_stream else None)
    audio_channels = _parse_int(selected_audio_stream.get("channels") if selected_audio_stream else None)

    audio_map_selector = "0:a:0"
    audio_language: str | None = None
    if selected_audio_stream is not None:
        audio_index = _parse_int(selected_audio_stream.get("index"))
        if audio_index is not None:
            audio_map_selector = f"0:{audio_index}"
        tags = selected_audio_stream.get("tags")
        if isinstance(tags, dict):
            raw_language = tags.get("language")
            if isinstance(raw_language, str) and raw_language.strip():
                audio_language = raw_language.strip()

    return ProbeInfo(
        source_video_bitrate_kbps=video_bitrate_kbps,
        width=width,
        height=height,
        audio_channels=audio_channels,
        audio_map_selector=audio_map_selector,
        audio_language=audio_language,
    )


def calculate_maxrate_kbps(
    source_video_bitrate_kbps: int | None,
    profile: VideoProfile,
) -> int:
    """Calculate VBV maxrate cap using source bitrate and profile bounds."""
    if source_video_bitrate_kbps is None:
        return profile.maxrate_cap_kbps
    candidate = int(source_video_bitrate_kbps * profile.bitrate_multiplier)
    return max(profile.maxrate_floor_kbps, min(profile.maxrate_cap_kbps, candidate))


def build_ffmpeg_command(
    source_path: Path,
    tmp_output_path: Path,
    profile: VideoProfile,
    probe: ProbeInfo,
    ffmpeg_bin: str = "ffmpeg",
) -> ConversionPlan:
    """Build ffmpeg command for Walkmarr conversion."""
    maxrate_kbps = calculate_maxrate_kbps(probe.source_video_bitrate_kbps, profile)

    source_channels = probe.audio_channels if probe.audio_channels is not None else 2
    if source_channels <= 1:
        audio_channels = 1
        audio_bitrate_kbps = profile.audio_bitrate_mono_kbps
    else:
        audio_channels = 2
        audio_bitrate_kbps = profile.audio_bitrate_stereo_kbps

    filter_expr = f"scale='min({profile.max_width},iw)':-2"

    command = [
        ffmpeg_bin,
        "-y",
        "-xerror",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        probe.audio_map_selector,
        "-vf",
        filter_expr,
        "-c:v",
        "libx264",
        "-profile:v",
        profile.h264_profile,
        "-level",
        profile.h264_level,
        "-pix_fmt",
        "yuv420p",
        "-x264-params",
        "ref=1:bframes=0:cabac=0",
        "-crf",
        str(profile.crf),
        "-maxrate",
        f"{maxrate_kbps}k",
        "-bufsize",
        f"{maxrate_kbps * 2}k",
        "-c:a",
        "aac",
        "-b:a",
        f"{audio_bitrate_kbps}k",
        "-ac",
        str(audio_channels),
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(tmp_output_path),
    ]

    return ConversionPlan(
        command=command,
        source_video_bitrate_kbps=probe.source_video_bitrate_kbps,
        maxrate_kbps=maxrate_kbps,
        audio_channels=audio_channels,
        audio_bitrate_kbps=audio_bitrate_kbps,
        filter_expr=filter_expr,
    )


def run_ffmpeg(command: list[str]) -> None:
    """Run ffmpeg command and raise ConversionError on failure."""
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise ConversionError(f"ffmpeg binary not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise ConversionError(f"ffmpeg conversion failed with exit code {exc.returncode}") from exc


def _parse_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _parse_kbps(value: object) -> int | None:
    bits_per_second = _parse_int(value)
    if bits_per_second is None:
        return None
    return max(1, bits_per_second // 1000)


def _select_preferred_audio_stream(
    audio_streams: list[dict[str, object]],
) -> dict[str, object] | None:
    if not audio_streams:
        return None

    ranked = sorted(audio_streams, key=_audio_stream_rank)
    return ranked[0]


def _audio_stream_rank(stream: dict[str, object]) -> tuple[int, int, int]:
    language = _audio_language(stream)
    is_english = 0 if _is_english_language(language) else 1
    is_default = 0 if _is_default_audio_stream(stream) else 1
    index = _parse_int(stream.get("index"))
    index_rank = index if index is not None else 999_999
    return (is_english, is_default, index_rank)


def _audio_language(stream: dict[str, object]) -> str | None:
    tags = stream.get("tags")
    if not isinstance(tags, dict):
        return None
    language = tags.get("language")
    if isinstance(language, str) and language.strip():
        return language.strip().casefold()
    return None


def _is_english_language(language: str | None) -> bool:
    if language is None:
        return False
    normalized = language.replace("_", "-")
    return normalized == "en" or normalized.startswith("en-") or normalized == "eng"


def _is_default_audio_stream(stream: dict[str, object]) -> bool:
    disposition = stream.get("disposition")
    if not isinstance(disposition, dict):
        return False
    default_flag = disposition.get("default")
    if isinstance(default_flag, int):
        return default_flag == 1
    if isinstance(default_flag, str) and default_flag.isdigit():
        return int(default_flag) == 1
    return False
