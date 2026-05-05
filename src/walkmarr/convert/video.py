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
        "stream=index,codec_type,bit_rate,width,height,channels",
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
    audio_stream: dict[str, object] | None = None
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video_stream is None:
            video_stream = stream
        elif codec_type == "audio" and audio_stream is None:
            audio_stream = stream

    video_bitrate_kbps = _parse_kbps(video_stream.get("bit_rate") if video_stream else None)
    width = _parse_int(video_stream.get("width") if video_stream else None)
    height = _parse_int(video_stream.get("height") if video_stream else None)
    audio_channels = _parse_int(audio_stream.get("channels") if audio_stream else None)

    return ProbeInfo(
        source_video_bitrate_kbps=video_bitrate_kbps,
        width=width,
        height=height,
        audio_channels=audio_channels,
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
        "0:a:0",
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
