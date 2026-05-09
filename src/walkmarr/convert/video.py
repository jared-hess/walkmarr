"""ffprobe inspection and ffmpeg command planning/execution."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from walkmarr.convert.audio_select import AudioStreamInfo, normalize_language_tag, select_audio_stream
from walkmarr.exceptions import ConversionError
from walkmarr.models import VideoProfile


@dataclass(frozen=True)
class ProbeInfo:
    """Useful source media characteristics from ffprobe."""

    source_video_bitrate_kbps: int | None
    width: int | None
    height: int | None
    audio_streams: list[AudioStreamInfo]


@dataclass(frozen=True)
class ConversionPlan:
    audio_wav_command: list[str]
    fdkaac_command: list[str]
    video_mux_command: list[str]
    tmp_audio_wav_path: Path
    tmp_audio_m4a_path: Path
    tmp_output_path: Path
    source_video_bitrate_kbps: int | None
    selected_audio_index: int
    selected_audio_language: str | None
    video_bitrate_kbps: int
    maxrate_kbps: int
    bufsize_kbps: int
    audio_channels: int
    audio_bitrate_kbps: int
    filter_expr: str


@dataclass(frozen=True)
class OutputValidationResult:
    """Validation metrics for encoded output acceptance."""

    source_duration_seconds: float
    output_duration_seconds: float
    allowed_shortfall_seconds: float
    allowed_overage_seconds: float
    output_size_bytes: int
    minimum_size_bytes: int


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
        "stream=index,codec_type,bit_rate,width,height,channels,codec_name,channel_layout:stream_tags=language,title:stream_disposition=default",
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
    audio_streams: list[AudioStreamInfo] = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video_stream is None:
            video_stream = stream
        elif codec_type == "audio":
            parsed = _parse_audio_stream(stream)
            if parsed is not None:
                audio_streams.append(parsed)

    video_bitrate_kbps = _parse_kbps(video_stream.get("bit_rate") if video_stream else None)
    width = _parse_int(video_stream.get("width") if video_stream else None)
    height = _parse_int(video_stream.get("height") if video_stream else None)
    return ProbeInfo(
        source_video_bitrate_kbps=video_bitrate_kbps,
        width=width,
        height=height,
        audio_streams=audio_streams,
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


def build_ipod_conversion_plan(
    source_path: Path,
    staging_directory: Path,
    profile: VideoProfile,
    probe: ProbeInfo,
    ffmpeg_bin: str = "ffmpeg",
    fdkaac_bin: str = "fdkaac",
) -> ConversionPlan:
    maxrate_kbps = profile.maxrate_cap_kbps

    selected_audio = select_audio_stream(
        probe.audio_streams,
        preferred_languages=list(profile.preferred_audio_languages),
    )
    if selected_audio is None:
        raise ConversionError(f"No audio streams found in source media: {source_path}")

    audio_channels = 2
    audio_bitrate_kbps = profile.audio_bitrate_stereo_kbps
    filter_expr = "scale=320:240:force_original_aspect_ratio=decrease:force_divisible_by=16,setsar=1"

    digest = hashlib.sha1(str(source_path).encode()).hexdigest()[:12]
    stem = source_path.stem
    tmp_audio_wav_path = staging_directory / f"{stem}.{digest}.audio.wav"
    tmp_audio_m4a_path = staging_directory / f"{stem}.{digest}.audio.m4a"
    tmp_output_path = staging_directory / f"{stem}.{digest}.tmp.mp4"

    audio_wav_command = [
        ffmpeg_bin, "-y", "-fflags", "+genpts",
        "-i", str(source_path),
        "-map", "0:a:0",
        "-vn",
        "-af", "aresample=async=1:first_pts=0",
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "pcm_s16le",
        str(tmp_audio_wav_path),
    ]

    fdkaac_command = [
        fdkaac_bin, "-b", "160",
        "-o", str(tmp_audio_m4a_path),
        str(tmp_audio_wav_path),
    ]

    video_mux_command = [
        ffmpeg_bin, "-y", "-fflags", "+genpts",
        "-i", str(source_path),
        "-i", str(tmp_audio_m4a_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-sn", "-dn",
        "-vf", filter_expr,
        "-c:v", "libx264",
        "-profile:v", profile.h264_profile,
        "-level:v", profile.h264_level,
        "-preset", "medium",
        "-b:v", f"{profile.video_bitrate_kbps}k",
        "-maxrate", f"{maxrate_kbps}k",
        "-bufsize", f"{profile.bufsize_kbps}k",
        "-pix_fmt", "yuv420p",
        "-x264-params", "ref=1:bframes=0:cabac=0",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(tmp_output_path),
    ]

    return ConversionPlan(
        audio_wav_command=audio_wav_command,
        fdkaac_command=fdkaac_command,
        video_mux_command=video_mux_command,
        tmp_audio_wav_path=tmp_audio_wav_path,
        tmp_audio_m4a_path=tmp_audio_m4a_path,
        tmp_output_path=tmp_output_path,
        source_video_bitrate_kbps=probe.source_video_bitrate_kbps,
        selected_audio_index=selected_audio.index,
        selected_audio_language=selected_audio.language_normalized,
        video_bitrate_kbps=profile.video_bitrate_kbps,
        maxrate_kbps=maxrate_kbps,
        bufsize_kbps=profile.bufsize_kbps,
        audio_channels=audio_channels,
        audio_bitrate_kbps=audio_bitrate_kbps,
        filter_expr=filter_expr,
    )



def probe_duration_seconds(path: Path, ffprobe_bin: str = "ffprobe") -> float:
    """Read media duration seconds from ffprobe format metadata."""
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ConversionError(f"ffprobe binary not found: {ffprobe_bin}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise ConversionError(f"ffprobe failed for '{path}': {stderr}") from exc

    value = completed.stdout.strip()
    if not value:
        raise ConversionError(f"ffprobe returned empty duration for '{path}'")

    try:
        duration = float(value)
    except ValueError as exc:
        raise ConversionError(f"ffprobe returned invalid duration '{value}' for '{path}'") from exc

    if not math.isfinite(duration) or duration <= 0:
        raise ConversionError(f"ffprobe returned non-positive duration {duration} for '{path}'")

    return duration


def validate_encoded_output(
    source_path: Path,
    output_path: Path,
    *,
    min_size_bytes: int = 1_000_000,
    ffprobe_bin: str = "ffprobe",
) -> OutputValidationResult:
    """Validate encoded output is readable and not suspiciously truncated."""
    if not output_path.exists():
        raise ConversionError(f"Output file was not created: {output_path}")

    source_duration = probe_duration_seconds(source_path, ffprobe_bin=ffprobe_bin)
    duration_scaled_min_size = int(source_duration * 8_000)
    effective_min_size = min(min_size_bytes, max(128_000, duration_scaled_min_size))

    size = output_path.stat().st_size
    if size < effective_min_size:
        raise ConversionError(
            "Output file is suspiciously small: "
            f"{output_path} size={size} bytes min={effective_min_size}"
        )
    output_duration = probe_duration_seconds(output_path, ffprobe_bin=ffprobe_bin)

    allowed_shortfall = max(5.0, source_duration * 0.005)
    allowed_overage = max(10.0, source_duration * 0.01)

    if output_duration + allowed_shortfall < source_duration:
        raise ConversionError(
            "Output appears truncated: "
            f"source={source_duration:.2f}s "
            f"output={output_duration:.2f}s "
            f"allowed_shortfall={allowed_shortfall:.2f}s"
        )

    if output_duration > source_duration + allowed_overage:
        raise ConversionError(
            "Output duration is suspiciously longer than source: "
            f"source={source_duration:.2f}s "
            f"output={output_duration:.2f}s "
            f"allowed_overage={allowed_overage:.2f}s"
        )

    return OutputValidationResult(
        source_duration_seconds=source_duration,
        output_duration_seconds=output_duration,
        allowed_shortfall_seconds=allowed_shortfall,
        allowed_overage_seconds=allowed_overage,
        output_size_bytes=size,
        minimum_size_bytes=effective_min_size,
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


def _parse_audio_stream(stream: dict[str, object]) -> AudioStreamInfo | None:
    index = _parse_int(stream.get("index"))
    if index is None:
        return None

    tags = stream.get("tags")
    raw_language: str | None = None
    title: str | None = None
    if isinstance(tags, dict):
        language_value = tags.get("language")
        if isinstance(language_value, str) and language_value.strip():
            raw_language = language_value.strip()
        title_value = tags.get("title")
        if isinstance(title_value, str) and title_value.strip():
            title = title_value.strip()

    normalized_language = normalize_language_tag(raw_language)
    disposition = stream.get("disposition")
    is_default = False
    if isinstance(disposition, dict):
        default_flag = disposition.get("default")
        if isinstance(default_flag, int):
            is_default = default_flag == 1
        elif isinstance(default_flag, str) and default_flag.isdigit():
            is_default = int(default_flag) == 1

    codec_name = stream.get("codec_name")
    parsed_codec = codec_name if isinstance(codec_name, str) else None
    channel_layout = stream.get("channel_layout")
    parsed_layout = channel_layout if isinstance(channel_layout, str) else None

    return AudioStreamInfo(
        index=index,
        codec_name=parsed_codec,
        channels=_parse_int(stream.get("channels")),
        channel_layout=parsed_layout,
        language_raw=raw_language,
        language_normalized=normalized_language,
        title=title,
        is_default=is_default,
    )
