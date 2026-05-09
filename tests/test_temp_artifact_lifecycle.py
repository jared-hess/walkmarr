from __future__ import annotations

import errno
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from walkmarr.convert.video import ConversionPlan
from walkmarr.exceptions import ConversionError
from walkmarr.models import AppConfig, MediaItem, PathMapping, ProviderConfig, VideoProfile
from walkmarr.process import process_media_items


def _config(tmp_path: Path, *, keep_failed_temps: bool = False) -> AppConfig:
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
        keep_failed_temps=keep_failed_temps,
    )


def _make_item(tmp_path: Path) -> MediaItem:
    source = tmp_path / "shows" / "ep.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"x")
    return MediaItem(
        kind="episode",
        source_path=source,
        output_path=tmp_path / "out-shows" / "Ep.mp4",
        profile_name="animation",
        title="Ep",
        series_title="Series",
        season_number=1,
        episode_number=1,
    )


def _fake_validation() -> object:
    return SimpleNamespace(
        source_duration_seconds=100.0,
        output_duration_seconds=100.0,
        allowed_shortfall_seconds=5.0,
        allowed_overage_seconds=10.0,
        output_size_bytes=2_000_000,
        minimum_size_bytes=1_000_000,
    )


def _setup_plan(tmp_path: Path) -> tuple[Path, Path, Path, ConversionPlan]:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    wav = staging / "fake.audio.wav"
    m4a = staging / "fake.audio.m4a"
    mp4 = staging / "fake.tmp.mp4"
    plan = ConversionPlan(
        audio_wav_command=["ffmpeg", "-o", str(wav)],
        fdkaac_command=["fdkaac", "-o", str(m4a), str(wav)],
        video_mux_command=["ffmpeg", "-o", str(mp4)],
        tmp_audio_wav_path=wav,
        tmp_audio_m4a_path=m4a,
        tmp_output_path=mp4,
        source_video_bitrate_kbps=1000,
        selected_audio_index=1,
        selected_audio_language="eng",
        video_bitrate_kbps=500,
        maxrate_kbps=768,
        bufsize_kbps=1500,
        audio_channels=2,
        audio_bitrate_kbps=160,
        filter_expr="scale=320:240",
    )
    return wav, m4a, mp4, plan


def test_success_cleanup_removes_wav_m4a(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    item = _make_item(tmp_path)
    wav, m4a, mp4, plan = _setup_plan(tmp_path)

    monkeypatch.setattr("walkmarr.process.probe_media", lambda _path: object())
    monkeypatch.setattr("walkmarr.process.build_ipod_conversion_plan", lambda **_kw: plan)
    monkeypatch.setattr("walkmarr.process.validate_encoded_output", lambda _s, _o: _fake_validation())
    monkeypatch.setattr("walkmarr.process._run_atomicparsley_with_cancellation", lambda _cmd, _token: None)
    monkeypatch.setattr("walkmarr.process._run_command_with_cancellation", lambda _cmd, _label, _token: None)

    def _fake_run_ffmpeg(command: list[str]) -> None:
        out_path = Path(command[-1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"data")

    monkeypatch.setattr("walkmarr.process.run_ffmpeg", _fake_run_ffmpeg)

    wav.write_bytes(b"wav-data")
    m4a.write_bytes(b"m4a-data")

    result = process_media_items(
        config=config,
        media_items=[item],
        provider_name="sonarr",
        profile=config.profiles["animation"],
        atomicparsley_bin="AtomicParsley",
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
        dry_run=False,
        overwrite=False,
    )

    assert result.converted == 1
    assert item.output_path.exists(), "Final output must exist"
    assert not wav.exists(), "WAV temp file must be deleted after success"
    assert not m4a.exists(), "M4A temp file must be deleted after success"


def test_conversion_creates_missing_temp_parent_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    item = _make_item(tmp_path)
    staging = tmp_path / "missing-staging"
    wav = staging / "fake.audio.wav"
    m4a = staging / "fake.audio.m4a"
    mp4 = staging / "fake.tmp.mp4"
    plan = ConversionPlan(
        audio_wav_command=["ffmpeg", "-o", str(wav)],
        fdkaac_command=["fdkaac", "-o", str(m4a), str(wav)],
        video_mux_command=["ffmpeg", "-o", str(mp4)],
        tmp_audio_wav_path=wav,
        tmp_audio_m4a_path=m4a,
        tmp_output_path=mp4,
        source_video_bitrate_kbps=1000,
        selected_audio_index=1,
        selected_audio_language="eng",
        video_bitrate_kbps=500,
        maxrate_kbps=768,
        bufsize_kbps=1500,
        audio_channels=2,
        audio_bitrate_kbps=160,
        filter_expr="scale=320:240",
    )

    monkeypatch.setattr("walkmarr.process.probe_media", lambda _path: object())
    monkeypatch.setattr("walkmarr.process.build_ipod_conversion_plan", lambda **_kw: plan)
    monkeypatch.setattr("walkmarr.process.validate_encoded_output", lambda _s, _o: _fake_validation())
    monkeypatch.setattr("walkmarr.process._run_atomicparsley_with_cancellation", lambda _cmd, _token: None)

    def _fake_run_ffmpeg(command: list[str]) -> None:
        out_path = Path(command[-1])
        assert out_path.parent.exists()
        out_path.write_bytes(b"data")

    def _fake_run_command_with_cancellation(cmd: list[str], label: str, token: object) -> None:
        del label, token
        out_path = Path(cmd[2])
        assert out_path.parent.exists()
        out_path.write_bytes(b"m4a-data")

    monkeypatch.setattr("walkmarr.process.run_ffmpeg", _fake_run_ffmpeg)
    monkeypatch.setattr(
        "walkmarr.process._run_command_with_cancellation",
        _fake_run_command_with_cancellation,
    )

    result = process_media_items(
        config=config,
        media_items=[item],
        provider_name="sonarr",
        profile=config.profiles["animation"],
        atomicparsley_bin="AtomicParsley",
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
        dry_run=False,
        overwrite=False,
    )

    assert result.converted == 1
    assert item.output_path.exists()


def test_conversion_promotes_temp_output_across_filesystems(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    item = _make_item(tmp_path)
    wav, m4a, mp4, plan = _setup_plan(tmp_path)

    monkeypatch.setattr("walkmarr.process.probe_media", lambda _path: object())
    monkeypatch.setattr("walkmarr.process.build_ipod_conversion_plan", lambda **_kw: plan)
    monkeypatch.setattr("walkmarr.process.validate_encoded_output", lambda _s, _o: _fake_validation())
    monkeypatch.setattr("walkmarr.process._run_atomicparsley_with_cancellation", lambda _cmd, _token: None)
    monkeypatch.setattr("walkmarr.process._run_command_with_cancellation", lambda _cmd, _label, _token: None)

    def _fake_run_ffmpeg(command: list[str]) -> None:
        out_path = Path(command[-1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"converted")

    original_replace = Path.replace

    def _fake_replace(self: Path, target: Path) -> Path:
        if self == mp4 and target == item.output_path:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return original_replace(self, target)

    monkeypatch.setattr("walkmarr.process.run_ffmpeg", _fake_run_ffmpeg)
    monkeypatch.setattr(Path, "replace", _fake_replace)
    wav.write_bytes(b"wav-data")
    m4a.write_bytes(b"m4a-data")

    result = process_media_items(
        config=config,
        media_items=[item],
        provider_name="sonarr",
        profile=config.profiles["animation"],
        atomicparsley_bin="AtomicParsley",
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
        dry_run=False,
        overwrite=False,
    )

    assert result.converted == 1
    assert item.output_path.read_bytes() == b"converted"
    assert not mp4.exists()


def test_failure_cleanup_deletes_temps_when_keep_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, keep_failed_temps=False)
    item = _make_item(tmp_path)
    wav, m4a, mp4, plan = _setup_plan(tmp_path)

    monkeypatch.setattr("walkmarr.process.probe_media", lambda _path: object())
    monkeypatch.setattr("walkmarr.process.build_ipod_conversion_plan", lambda **_kw: plan)

    def _fake_run_command_with_cancellation(cmd: list[str], label: str, token: object) -> None:
        raise ConversionError("fdkaac failed")

    def _fake_run_ffmpeg(command: list[str]) -> None:
        out_path = Path(command[-1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"data")

    monkeypatch.setattr("walkmarr.process.run_ffmpeg", _fake_run_ffmpeg)
    monkeypatch.setattr(
        "walkmarr.process._run_command_with_cancellation",
        _fake_run_command_with_cancellation,
    )

    wav.write_bytes(b"wav-data")
    m4a.write_bytes(b"m4a-data")
    mp4.write_bytes(b"mp4-data")

    with pytest.raises(Exception):
        process_media_items(
            config=config,
            media_items=[item],
            provider_name="sonarr",
            profile=config.profiles["animation"],
            atomicparsley_bin="AtomicParsley",
            console=Console(file=StringIO(), force_terminal=False, color_system=None),
            dry_run=False,
            overwrite=False,
        )

    assert not wav.exists(), "WAV temp must be deleted on failure with keep_failed_temps=False"
    assert not m4a.exists(), "M4A temp must be deleted on failure with keep_failed_temps=False"
    assert not mp4.exists(), "MP4 temp must be deleted on failure with keep_failed_temps=False"


def test_failure_keeps_temps_when_keep_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, keep_failed_temps=True)
    item = _make_item(tmp_path)
    wav, m4a, mp4, plan = _setup_plan(tmp_path)

    monkeypatch.setattr("walkmarr.process.probe_media", lambda _path: object())
    monkeypatch.setattr("walkmarr.process.build_ipod_conversion_plan", lambda **_kw: plan)

    def _fake_run_command_with_cancellation(cmd: list[str], label: str, token: object) -> None:
        raise ConversionError("fdkaac failed")

    def _fake_run_ffmpeg(command: list[str]) -> None:
        out_path = Path(command[-1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"data")

    monkeypatch.setattr("walkmarr.process.run_ffmpeg", _fake_run_ffmpeg)
    monkeypatch.setattr(
        "walkmarr.process._run_command_with_cancellation",
        _fake_run_command_with_cancellation,
    )

    wav.write_bytes(b"wav-data")
    m4a.write_bytes(b"m4a-data")

    with pytest.raises(Exception):
        process_media_items(
            config=config,
            media_items=[item],
            provider_name="sonarr",
            profile=config.profiles["animation"],
            atomicparsley_bin="AtomicParsley",
            console=Console(file=StringIO(), force_terminal=False, color_system=None),
            dry_run=False,
            overwrite=False,
        )

    assert wav.exists(), "WAV temp must be retained with keep_failed_temps=True"
    assert m4a.exists(), "M4A temp must be retained with keep_failed_temps=True"
