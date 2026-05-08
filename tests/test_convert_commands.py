from pathlib import Path

import pytest

from walkmarr.convert.audio_select import AudioStreamInfo
from walkmarr.convert.video import ProbeInfo, build_ffmpeg_command
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


def test_ffmpeg_command_contains_required_flags() -> None:
    profile = VideoProfile(
        crf=30,
        video_bitrate_kbps=500,
        maxrate_floor_kbps=250,
        maxrate_cap_kbps=768,
        bufsize_kbps=1500,
        bitrate_multiplier=1.5,
        audio_bitrate_mono_kbps=160,
        audio_bitrate_stereo_kbps=160,
        max_width=320,
        h264_profile="baseline",
        h264_level="1.3",
    )
    probe = ProbeInfo(
        source_video_bitrate_kbps=3175,
        width=1920,
        height=1080,
        audio_streams=[_audio_stream(index=1, language="eng", channels=2, is_default=True)],
    )
    plan = build_ffmpeg_command(
        source_path=Path("/src/in.mkv"),
        tmp_output_path=Path("/out/out.tmp.mp4"),
        profile=profile,
        probe=probe,
    )

    cmd = plan.command
    assert "-xerror" not in cmd
    assert "-fflags" in cmd
    fflags_index = cmd.index("-fflags")
    assert cmd[fflags_index + 1] == "+genpts"
    assert cmd.index("-fflags") < cmd.index("-i")
    assert "-vf" in cmd
    assert cmd[cmd.index("-vf") + 1] == (
        "scale=320:240:force_original_aspect_ratio=decrease:force_divisible_by=16,setsar=1"
    )
    assert "-af" in cmd and cmd[cmd.index("-af") + 1] == "aresample=async=1:first_pts=0"
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "-profile:v" in cmd and cmd[cmd.index("-profile:v") + 1] == "baseline"
    assert "-level:v" in cmd and cmd[cmd.index("-level:v") + 1] == "1.3"
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "medium"
    assert "-b:v" in cmd and cmd[cmd.index("-b:v") + 1] == "500k"
    assert "-maxrate" in cmd and cmd[cmd.index("-maxrate") + 1] == "768k"
    assert "-bufsize" in cmd and cmd[cmd.index("-bufsize") + 1] == "1500k"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert "-x264-params" in cmd and cmd[cmd.index("-x264-params") + 1] == "ref=1:bframes=0:cabac=0"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-b:a" in cmd and cmd[cmd.index("-b:a") + 1] == "160k"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "2"
    assert "-movflags" in cmd and cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert "-sn" in cmd
    assert "-dn" in cmd
    assert "-crf" not in cmd
    assert "-ar" not in cmd
    assert "-r" not in cmd
    assert "-level" not in cmd
    assert "3.0" not in cmd
    assert "96k" not in cmd
    assert "scale='min(640,iw)':-2" not in cmd


def test_ffmpeg_command_uses_selected_audio_stream_map() -> None:
    profile = VideoProfile(
        crf=30,
        maxrate_floor_kbps=250,
        maxrate_cap_kbps=1200,
        bitrate_multiplier=1.5,
        audio_bitrate_mono_kbps=64,
        audio_bitrate_stereo_kbps=96,
        max_width=640,
        h264_profile="baseline",
        h264_level="3.0",
    )
    probe = ProbeInfo(
        source_video_bitrate_kbps=1000,
        width=1280,
        height=720,
        audio_streams=[
            _audio_stream(index=1, language="por", channels=2, is_default=True),
            _audio_stream(index=3, language="eng", channels=6, is_default=False),
        ],
    )
    plan = build_ffmpeg_command(
        source_path=Path("/src/in.mkv"),
        tmp_output_path=Path("/out/out.tmp.mp4"),
        profile=profile,
        probe=probe,
    )

    cmd = plan.command
    map_indices = [i for i, v in enumerate(cmd) if v == "-map"]
    assert cmd[map_indices[0] + 1] == "0:v:0"
    assert cmd[map_indices[1] + 1] == "0:3"


def test_build_ffmpeg_command_raises_when_no_audio_streams() -> None:
    profile = VideoProfile(
        crf=30,
        maxrate_floor_kbps=250,
        maxrate_cap_kbps=1200,
        bitrate_multiplier=1.5,
        audio_bitrate_mono_kbps=64,
        audio_bitrate_stereo_kbps=96,
        max_width=640,
        h264_profile="baseline",
        h264_level="3.0",
    )
    probe = ProbeInfo(
        source_video_bitrate_kbps=1200,
        width=1280,
        height=720,
        audio_streams=[],
    )

    with pytest.raises(ConversionError, match="No audio streams"):
        build_ffmpeg_command(
            source_path=Path("/src/in.mkv"),
            tmp_output_path=Path("/out/out.tmp.mp4"),
            profile=profile,
            probe=probe,
        )
