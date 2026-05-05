from pathlib import Path

from walkmarr.convert.video import ProbeInfo, _select_preferred_audio_stream, build_ffmpeg_command
from walkmarr.models import VideoProfile


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
    probe = ProbeInfo(source_video_bitrate_kbps=3175, width=1920, height=1080, audio_channels=2)
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
        audio_channels=6,
        audio_map_selector="0:3",
        audio_language="eng",
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


def test_audio_selection_prefers_english_default_stream() -> None:
    audio_streams = [
        {"index": 1, "tags": {"language": "jpn"}, "disposition": {"default": 1}},
        {"index": 2, "tags": {"language": "eng"}, "disposition": {"default": 0}},
        {"index": 3, "tags": {"language": "eng"}, "disposition": {"default": 1}},
    ]

    selected = _select_preferred_audio_stream(audio_streams)
    assert selected is not None
    assert selected["index"] == 3
