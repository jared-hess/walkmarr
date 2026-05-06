from pathlib import Path

from walkmarr.models import AppConfig, PathMapping, ProviderConfig, QueueItem, QueueItemStatus, VideoProfile
from walkmarr.tui import WalkmarrTUI


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
        path_mappings=[
            PathMapping(remote="/shows", local=tmp_path / "shows"),
            PathMapping(remote="/movies", local=tmp_path / "movies"),
        ],
        output_roots={"shows": tmp_path / "out-shows", "movies": tmp_path / "out-movies"},
        default_profiles={"sonarr": "animation", "radarr": "movie"},
        profiles={"animation": profile, "movie": profile},
        overrides={"sonarr": {}, "radarr": {}},
        staging_mode="never",
        staging_directory=tmp_path / "staging",
    )


def test_tui_app_constructs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("walkmarr.queue_manager.ensure_required_tools", lambda: "AtomicParsley")
    app = WalkmarrTUI(_config(tmp_path))
    assert app.provider == "sonarr"


def test_add_overwrite_opens_confirmation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("walkmarr.queue_manager.ensure_required_tools", lambda: "AtomicParsley")
    app = WalkmarrTUI(_config(tmp_path))
    app._media_items = [{"id": 1, "title": "Futurama"}]
    app._selected_media_id = 1

    called = {"count": 0}

    def _fake_push(_screen, _callback) -> None:
        called["count"] += 1

    monkeypatch.setattr(app, "push_screen", _fake_push)
    app.action_add_overwrite()
    assert called["count"] == 1


def test_readd_completed_item_requires_confirmation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("walkmarr.queue_manager.ensure_required_tools", lambda: "AtomicParsley")
    app = WalkmarrTUI(_config(tmp_path))
    app._media_items = [{"id": 1, "title": "Futurama"}]
    app._selected_media_id = 1

    completed = QueueItem(
        id="q1",
        provider="sonarr",
        provider_item_id=1,
        title="Futurama",
        status=QueueItemStatus.COMPLETE,
    )
    monkeypatch.setattr(app._queue, "get_items", lambda: [completed])

    pushed = {"count": 0}
    monkeypatch.setattr(app, "push_screen", lambda _screen, _callback: pushed.__setitem__("count", 1))

    app._add_selected_media_to_queue(mode="missing_only", dry_run=False)
    assert pushed["count"] == 1
