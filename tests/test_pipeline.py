from pathlib import Path
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from walkmarr.convert.video import ConversionPlan
from walkmarr.models import AppConfig, MediaItem, PathMapping, ProviderConfig, VideoProfile
from walkmarr.process import process_media_items


def _config(tmp_path: Path) -> AppConfig:
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
    return AppConfig(
        providers={
            "sonarr": ProviderConfig(url="http://sonarr", api_key="x"),
            "radarr": ProviderConfig(url="http://radarr", api_key="y"),
        },
        path_mappings=[PathMapping(remote="/shows", local=tmp_path / "shows")],
        output_roots={"shows": tmp_path / "out-shows", "movies": tmp_path / "out-movies"},
        default_profiles={"sonarr": "animation", "radarr": "movie"},
        profiles={"animation": profile, "movie": profile},
        overrides={"sonarr": {}, "radarr": {}},
        staging_mode="never",
        staging_directory=tmp_path / "staging",
    )


def test_process_media_items_handles_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    src_dir = tmp_path / "shows"
    src_dir.mkdir(parents=True, exist_ok=True)

    source_a = src_dir / "a.mkv"
    source_b = src_dir / "b.mkv"
    source_a.write_bytes(b"a")
    source_b.write_bytes(b"b")

    item_a = MediaItem(
        kind="episode",
        source_path=source_a,
        output_path=tmp_path / "out-shows" / "A.mp4",
        profile_name="animation",
        title="A",
        series_title="Series",
        season_number=1,
        episode_number=1,
    )
    item_b = MediaItem(
        kind="episode",
        source_path=source_b,
        output_path=tmp_path / "out-shows" / "B.mp4",
        profile_name="animation",
        title="B",
        series_title="Series",
        season_number=1,
        episode_number=2,
    )

    monkeypatch.setattr("walkmarr.process.probe_media", lambda _path: object())

    def _fake_build_ffmpeg(
        source_path: Path,
        tmp_output_path: Path,
        profile: VideoProfile,
        probe: object,
    ) -> ConversionPlan:
        del source_path, profile, probe
        return ConversionPlan(
            command=["ffmpeg", "-crf", "30", str(tmp_output_path)],
            source_video_bitrate_kbps=1000,
            selected_audio_index=1,
            selected_audio_language="eng",
            maxrate_kbps=1200,
            audio_channels=2,
            audio_bitrate_kbps=96,
            filter_expr="scale='min(640,iw)':-2",
        )

    monkeypatch.setattr("walkmarr.process.build_ffmpeg_command", _fake_build_ffmpeg)
    monkeypatch.setattr("walkmarr.process.run_atomicparsley", lambda _cmd: None)
    monkeypatch.setattr(
        "walkmarr.process.validate_encoded_output",
        lambda _source, _output: SimpleNamespace(
            source_duration_seconds=100.0,
            output_duration_seconds=100.0,
            allowed_shortfall_seconds=5.0,
            allowed_overage_seconds=10.0,
        ),
    )

    def _fake_run_ffmpeg(command: list[str]) -> None:
        out_path = Path(command[-1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"converted")

    monkeypatch.setattr("walkmarr.process.run_ffmpeg", _fake_run_ffmpeg)

    result = process_media_items(
        config=config,
        media_items=[item_a, item_b],
        provider_name="sonarr",
        profile=config.profiles["animation"],
        atomicparsley_bin="AtomicParsley",
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
        dry_run=False,
        overwrite=False,
    )

    assert result.converted == 2
    assert result.skipped == 0
    assert item_a.output_path.exists()
    assert item_b.output_path.exists()
