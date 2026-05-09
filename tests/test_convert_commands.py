import hashlib
from pathlib import Path

import pytest

from walkmarr.convert.audio_select import AudioStreamInfo
from walkmarr.convert.video import ProbeInfo, build_ipod_conversion_plan
from walkmarr.exceptions import ConversionError
from walkmarr.models import VideoProfile


def _audio_stream(index: int, language: str, channels: int, is_default: bool) -> AudioStreamInfo:
    return AudioStreamInfo(
        index=index,
        codec_name="aac",
        channels=channels,
        channel_layout="stereo",
        language_raw=language,
        language_normalized=language,
        title=None,
        is_default=is_default,
    )


def _make_profile(
    video_bitrate_kbps: int = 500,
    maxrate_cap_kbps: int = 768,
    bufsize_kbps: int = 1500,
    h264_level: str = "1.3",
    h264_profile: str = "baseline",
) -> VideoProfile:
    return VideoProfile(
        crf=30,
        video_bitrate_kbps=video_bitrate_kbps,
        maxrate_floor_kbps=250,
        maxrate_cap_kbps=maxrate_cap_kbps,
        bufsize_kbps=bufsize_kbps,
        bitrate_multiplier=1.5,
        audio_bitrate_mono_kbps=160,
        audio_bitrate_stereo_kbps=160,
        max_width=320,
        h264_profile=h264_profile,
        h264_level=h264_level,
    )


def _make_probe(
    source_video_bitrate_kbps: int = 3175,
    audio_index: int = 1,
    language: str = "eng",
    channels: int = 2,
) -> ProbeInfo:
    return ProbeInfo(
        source_video_bitrate_kbps=source_video_bitrate_kbps,
        width=1920,
        height=1080,
        audio_streams=[_audio_stream(index=audio_index, language=language, channels=channels, is_default=True)],
    )


def _sha1_12(source_path: Path) -> str:
    return hashlib.sha1(str(source_path).encode()).hexdigest()[:12]


def test_temp_paths_use_sha1_digest() -> None:
    source = Path("/src/in.mkv")
    staging = Path("/staging")
    digest = _sha1_12(source)

    plan = build_ipod_conversion_plan(
        source_path=source,
        staging_directory=staging,
        profile=_make_profile(),
        probe=_make_probe(),
    )

    assert plan.tmp_audio_wav_path == staging / f"in.{digest}.audio.wav"
    assert plan.tmp_audio_m4a_path == staging / f"in.{digest}.audio.m4a"
    assert plan.tmp_output_path == staging / f"in.{digest}.tmp.mp4"


def test_audio_wav_command_exact_order() -> None:
    source = Path("/src/in.mkv")
    staging = Path("/staging")
    plan = build_ipod_conversion_plan(
        source_path=source,
        staging_directory=staging,
        profile=_make_profile(),
        probe=_make_probe(),
    )
    expected = [
        "ffmpeg", "-y", "-fflags", "+genpts",
        "-i", str(source),
        "-map", "0:a:0",
        "-vn",
        "-af", "aresample=async=1:first_pts=0",
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "pcm_s16le",
        str(plan.tmp_audio_wav_path),
    ]
    assert plan.audio_wav_command == expected


def test_fdkaac_command_exact() -> None:
    source = Path("/src/in.mkv")
    staging = Path("/staging")
    plan = build_ipod_conversion_plan(
        source_path=source,
        staging_directory=staging,
        profile=_make_profile(),
        probe=_make_probe(),
    )
    expected = [
        "fdkaac", "-b", "160",
        "-o", str(plan.tmp_audio_m4a_path),
        str(plan.tmp_audio_wav_path),
    ]
    assert plan.fdkaac_command == expected


def test_video_mux_command_required_flags() -> None:
    source = Path("/src/in.mkv")
    staging = Path("/staging")
    plan = build_ipod_conversion_plan(
        source_path=source,
        staging_directory=staging,
        profile=_make_profile(),
        probe=_make_probe(),
    )
    cmd = plan.video_mux_command

    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert "-xerror" not in cmd
    assert "-fflags" in cmd and cmd[cmd.index("-fflags") + 1] == "+genpts"

    i_indices = [i for i, v in enumerate(cmd) if v == "-i"]
    assert len(i_indices) == 2
    assert cmd[i_indices[0] + 1] == str(source)
    assert cmd[i_indices[1] + 1] == str(plan.tmp_audio_m4a_path)

    map_indices = [i for i, v in enumerate(cmd) if v == "-map"]
    assert cmd[map_indices[0] + 1] == "0:v:0"
    assert cmd[map_indices[1] + 1] == "1:a:0"

    assert "-sn" in cmd
    assert "-dn" in cmd
    assert "-vf" in cmd and cmd[cmd.index("-vf") + 1] == (
        "scale=320:240:force_original_aspect_ratio=decrease:force_divisible_by=16,setsar=1"
    )
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "-profile:v" in cmd and cmd[cmd.index("-profile:v") + 1] == "baseline"
    assert "-level:v" in cmd and cmd[cmd.index("-level:v") + 1] == "1.3"
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "medium"
    assert "-b:v" in cmd and cmd[cmd.index("-b:v") + 1] == "500k"
    assert "-maxrate" in cmd and cmd[cmd.index("-maxrate") + 1] == "768k"
    assert "-bufsize" in cmd and cmd[cmd.index("-bufsize") + 1] == "1500k"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert "-x264-params" in cmd and cmd[cmd.index("-x264-params") + 1] == "ref=1:bframes=0:cabac=0"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy"
    assert "-movflags" in cmd and cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert cmd[-1] == str(plan.tmp_output_path)

    assert "-af" not in cmd
    assert "-ar" not in cmd
    assert "-b:a" not in cmd
    assert "-crf" not in cmd
    assert "-r" not in cmd
    assert "aac" not in cmd
    assert "3.0" not in cmd
    assert "scale='min(640,iw)':-2" not in cmd


def test_video_mux_command_exact() -> None:
    source = Path("/src/in.mkv")
    staging = Path("/staging")
    plan = build_ipod_conversion_plan(
        source_path=source,
        staging_directory=staging,
        profile=_make_profile(),
        probe=_make_probe(),
    )
    expected = [
        "ffmpeg", "-y", "-fflags", "+genpts",
        "-i", str(source),
        "-i", str(plan.tmp_audio_m4a_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-sn", "-dn",
        "-vf", "scale=320:240:force_original_aspect_ratio=decrease:force_divisible_by=16,setsar=1",
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level:v", "1.3",
        "-preset", "medium",
        "-b:v", "500k",
        "-maxrate", "768k",
        "-bufsize", "1500k",
        "-pix_fmt", "yuv420p",
        "-x264-params", "ref=1:bframes=0:cabac=0",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(plan.tmp_output_path),
    ]
    assert plan.video_mux_command == expected


def test_plan_metadata_fields() -> None:
    source = Path("/src/in.mkv")
    staging = Path("/staging")
    plan = build_ipod_conversion_plan(
        source_path=source,
        staging_directory=staging,
        profile=_make_profile(),
        probe=_make_probe(source_video_bitrate_kbps=3175, audio_index=1, language="eng"),
    )
    assert plan.source_video_bitrate_kbps == 3175
    assert plan.selected_audio_index == 1
    assert plan.selected_audio_language == "eng"
    assert plan.video_bitrate_kbps == 500
    assert plan.maxrate_kbps == 768
    assert plan.bufsize_kbps == 1500
    assert plan.audio_channels == 2
    assert plan.audio_bitrate_kbps == 160
    assert plan.filter_expr == "scale=320:240:force_original_aspect_ratio=decrease:force_divisible_by=16,setsar=1"


def test_conversion_plan_has_no_command_field() -> None:
    source = Path("/src/in.mkv")
    staging = Path("/staging")
    plan = build_ipod_conversion_plan(
        source_path=source,
        staging_directory=staging,
        profile=_make_profile(),
        probe=_make_probe(),
    )
    assert not hasattr(plan, "command")


def test_build_ipod_conversion_plan_raises_when_no_audio_streams() -> None:
    probe = ProbeInfo(
        source_video_bitrate_kbps=1200,
        width=1280,
        height=720,
        audio_streams=[],
    )
    with pytest.raises(ConversionError, match="No audio streams"):
        build_ipod_conversion_plan(
            source_path=Path("/src/in.mkv"),
            staging_directory=Path("/staging"),
            profile=_make_profile(),
            probe=probe,
        )


def test_custom_ffmpeg_and_fdkaac_bins() -> None:
    source = Path("/src/in.mkv")
    staging = Path("/staging")
    plan = build_ipod_conversion_plan(
        source_path=source,
        staging_directory=staging,
        profile=_make_profile(),
        probe=_make_probe(),
        ffmpeg_bin="/usr/local/bin/ffmpeg",
        fdkaac_bin="/usr/local/bin/fdkaac",
    )
    assert plan.audio_wav_command[0] == "/usr/local/bin/ffmpeg"
    assert plan.fdkaac_command[0] == "/usr/local/bin/fdkaac"
    assert plan.video_mux_command[0] == "/usr/local/bin/ffmpeg"
