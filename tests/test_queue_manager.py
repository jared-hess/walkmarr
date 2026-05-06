from __future__ import annotations

import time
from collections.abc import Callable
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from walkmarr.models import AppConfig, MediaItem, PathMapping, ProviderConfig, QueueItem, QueueItemStatus, VideoProfile
from walkmarr.process import BatchProcessResult, ProgressEvent
from walkmarr.queue_manager import QueueManager


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
        queue_start_paused=False,
    )


class FakeSonarrProvider:
    def list_series(self) -> list[dict[str, object]]:
        return [{"id": 1, "title": "Futurama"}]

    def list_episodes(self, series_id: int) -> list[dict[str, object]]:
        del series_id
        return []

    def list_episode_files(self, series_id: int) -> list[dict[str, object]]:
        del series_id
        return []

    def build_media_items(self, **kwargs: object) -> list[MediaItem]:
        output_root = kwargs["output_root"]
        assert isinstance(output_root, Path)
        return [
            MediaItem(
                kind="episode",
                source_path=Path("/tmp/source-a.mkv"),
                output_path=output_root / "Futurama S01E01.mp4",
                profile_name="animation",
                title="Space Pilot 3000",
                series_title="Futurama",
                season_number=1,
                episode_number=1,
            )
        ]


class FakeRadarrProvider:
    def list_movies(self) -> list[dict[str, object]]:
        return [{"id": 2, "title": "American Psycho", "year": 2000, "movieFile": {"path": "/x.mkv"}}]

    def build_media_item(self, **kwargs: object) -> MediaItem:
        output_root = kwargs["output_root"]
        assert isinstance(output_root, Path)
        return MediaItem(
            kind="movie",
            source_path=Path("/tmp/source-movie.mkv"),
            output_path=output_root / "American Psycho (2000).mp4",
            profile_name="movie",
            title="American Psycho",
            movie_title="American Psycho",
            year=2000,
        )


def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for condition")


def test_queue_manager_prevents_duplicate_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("walkmarr.queue_manager.ensure_required_tools", lambda: "AtomicParsley")
    manager = QueueManager(
        config=_config(tmp_path),
        sonarr_provider=FakeSonarrProvider(),
        radarr_provider=FakeRadarrProvider(),
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
    )
    first_id = manager.add_item(
        QueueItem(id="", provider="sonarr", provider_item_id=1, title="Futurama")
    )
    assert first_id

    with pytest.raises(Exception, match="already in the queue"):
        manager.add_item(QueueItem(id="", provider="sonarr", provider_item_id=1, title="Futurama"))


def test_queue_manager_processes_fifo_and_accepts_new_items_while_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("walkmarr.queue_manager.ensure_required_tools", lambda: "AtomicParsley")

    def _fake_process_media_items(**kwargs: object) -> BatchProcessResult:
        callback = kwargs.get("progress_callback")
        queue_item_id = kwargs.get("queue_item_id")
        provider_name = kwargs.get("provider_name")
        media_items = kwargs.get("media_items")
        assert isinstance(media_items, list)
        for index, media_item in enumerate(media_items, start=1):
            if callback is not None:
                callback(
                    ProgressEvent(
                        level="info",
                        message=f"Converting {media_item.title}",
                        queue_item_id=str(queue_item_id),
                        provider=provider_name if provider_name in {"sonarr", "radarr"} else None,
                        current_index=index,
                        total=len(media_items),
                        current_stage="converting",
                        item=media_item,
                    )
                )
                callback(
                    ProgressEvent(
                        level="success",
                        message=f"Completed {media_item.title}",
                        queue_item_id=str(queue_item_id),
                        provider=provider_name if provider_name in {"sonarr", "radarr"} else None,
                        current_index=index,
                        total=len(media_items),
                        current_stage="complete",
                        item=media_item,
                    )
                )
            time.sleep(0.05)
        return BatchProcessResult(converted=len(media_items), skipped=0, failed=0)

    monkeypatch.setattr("walkmarr.queue_manager.process_media_items", _fake_process_media_items)

    manager = QueueManager(
        config=_config(tmp_path),
        sonarr_provider=FakeSonarrProvider(),
        radarr_provider=FakeRadarrProvider(),
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
    )

    id_a = manager.add_item(QueueItem(id="", provider="sonarr", provider_item_id=1, title="Futurama"))
    _wait_for(lambda: any(item.id == id_a and item.status == QueueItemStatus.RUNNING for item in manager.get_items()))

    id_b = manager.add_item(
        QueueItem(id="", provider="radarr", provider_item_id=2, title="American Psycho", year=2000)
    )

    _wait_for(
        lambda: any(item.id == id_b and item.status == QueueItemStatus.COMPLETE for item in manager.get_items()),
        timeout=4.0,
    )
    items = manager.get_items()
    first = next(item for item in items if item.id == id_a)
    second = next(item for item in items if item.id == id_b)
    assert first.status == QueueItemStatus.COMPLETE
    assert second.status == QueueItemStatus.COMPLETE
    assert first.completed_files >= 1
    assert second.completed_files >= 1
