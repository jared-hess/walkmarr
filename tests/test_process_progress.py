from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from walkmarr.exceptions import WalkmarrError
from walkmarr.models import AppConfig, MediaItem, PathMapping, ProviderConfig, VideoProfile
from walkmarr.process import (
    CancellationToken,
    ProcessResult,
    ProgressEvent,
    _run_subprocess_cancellable,
    process_media_items,
)


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


def test_process_media_items_emits_canceled_event(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source = tmp_path / "shows" / "a.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"x")

    item = MediaItem(
        kind="episode",
        source_path=source,
        output_path=tmp_path / "out-shows" / "A.mp4",
        profile_name="animation",
        title="A",
        series_title="Series",
        season_number=1,
        episode_number=1,
    )
    events: list[ProgressEvent] = []
    token = CancellationToken()
    token.cancel()

    with pytest.raises(WalkmarrError, match="canceled"):
        process_media_items(
            config=config,
            media_items=[item],
            provider_name="sonarr",
            profile=config.profiles["animation"],
            atomicparsley_bin="AtomicParsley",
            console=Console(file=StringIO(), force_terminal=False, color_system=None),
            dry_run=True,
            overwrite=False,
            queue_item_id="q1",
            cancellation_token=token,
            progress_callback=events.append,
        )

    assert any(event.current_stage == "canceled" for event in events)


def test_process_media_items_continue_on_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source = tmp_path / "shows" / "a.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"x")

    items = [
        MediaItem(
            kind="episode",
            source_path=source,
            output_path=tmp_path / "out-shows" / "A.mp4",
            profile_name="animation",
            title="A",
            series_title="Series",
            season_number=1,
            episode_number=1,
        ),
        MediaItem(
            kind="episode",
            source_path=source,
            output_path=tmp_path / "out-shows" / "B.mp4",
            profile_name="animation",
            title="B",
            series_title="Series",
            season_number=1,
            episode_number=2,
        ),
    ]

    calls = {"count": 0}

    def _fake_process_media_item(**kwargs: object) -> ProcessResult:
        calls["count"] += 1
        media_item = kwargs["media_item"]
        assert isinstance(media_item, MediaItem)
        if calls["count"] == 1:
            raise WalkmarrError("boom")
        return ProcessResult(status="converted", output_path=media_item.output_path)

    monkeypatch.setattr("walkmarr.process.process_media_item", _fake_process_media_item)

    result = process_media_items(
        config=config,
        media_items=items,
        provider_name="sonarr",
        profile=config.profiles["animation"],
        atomicparsley_bin="AtomicParsley",
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
        dry_run=True,
        overwrite=False,
        continue_on_error=True,
        progress_callback=lambda _event: None,
    )

    assert result.converted == 1
    assert result.failed == 1


def test_cancellable_subprocess_uses_devnull(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeProc:
        def poll(self) -> int | None:
            return 0

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

        def kill(self) -> None:
            return None

    def _fake_popen(command: list[str], **kwargs: object) -> FakeProc:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("walkmarr.process.subprocess.Popen", _fake_popen)

    token = CancellationToken()
    _run_subprocess_cancellable(["ffmpeg", "-version"], "ffmpeg", token)

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "stdin" in kwargs and kwargs["stdin"] is not None
    assert "stdout" in kwargs and kwargs["stdout"] is not None
    assert "stderr" in kwargs and kwargs["stderr"] is not None
