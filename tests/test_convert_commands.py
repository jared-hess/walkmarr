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
    assert "-xerror" in cmd
    assert "-profile:v" in cmd and cmd[cmd.index("-profile:v") + 1] == "baseline"
    assert "-level" in cmd and cmd[cmd.index("-level") + 1] == "3.0"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert "-x264-params" in cmd and cmd[cmd.index("-x264-params") + 1] == "ref=1:bframes=0:cabac=0"
    assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "30"
    assert "-maxrate" in cmd
    assert "-bufsize" in cmd
    assert "-movflags" in cmd and cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert "-sn" in cmd
    assert "-dn" in cmd


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
