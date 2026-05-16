from __future__ import annotations

import time
import threading
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import Any, Literal, cast

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
    def __init__(self) -> None:
        self.get_series_by_id_calls = 0
        self.list_episodes_calls = 0
        self.list_episode_files_calls = 0
        self.build_media_items_kwargs: dict[str, object] | None = None

    def list_series(self) -> list[dict[str, object]]:
        return [{"id": 1, "title": "Futurama"}]

    def get_series_by_id(self, series_id: int) -> dict[str, object]:
        self.get_series_by_id_calls += 1
        return {"id": series_id, "title": "Futurama"}

    def list_episodes(self, series_id: int) -> list[dict[str, object]]:
        del series_id
        self.list_episodes_calls += 1
        return []

    def list_episode_files(self, series_id: int) -> list[dict[str, object]]:
        del series_id
        self.list_episode_files_calls += 1
        return []

    def build_media_items(self, **kwargs: object) -> list[MediaItem]:
        self.build_media_items_kwargs = kwargs
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

    def poster_url(self, series: dict[str, object]) -> str | None:
        del series
        return "https://image.example/series-poster.jpg"


class FakeRadarrProvider:
    def __init__(self) -> None:
        self.get_movie_by_id_calls = 0

    def list_movies(self) -> list[dict[str, object]]:
        return [{"id": 2, "title": "American Psycho", "year": 2000, "movieFile": {"path": "/x.mkv"}}]

    def get_movie_by_id(self, movie_id: int) -> dict[str, object]:
        self.get_movie_by_id_calls += 1
        return {
            "id": movie_id,
            "title": "American Psycho",
            "year": 2000,
            "movieFile": {"path": "/x.mkv"},
        }

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
        sonarr_provider=cast(Any, FakeSonarrProvider()),
        radarr_provider=cast(Any, FakeRadarrProvider()),
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
        callback = cast(Callable[[ProgressEvent], None] | None, kwargs.get("progress_callback"))
        queue_item_id = kwargs.get("queue_item_id")
        provider_name_raw = kwargs.get("provider_name")
        provider_name: Literal["sonarr", "radarr"] | None = (
            cast(Literal["sonarr", "radarr"], provider_name_raw)
            if provider_name_raw in {"sonarr", "radarr"}
            else None
        )
        media_items = kwargs.get("media_items")
        assert isinstance(media_items, list)
        for index, media_item in enumerate(media_items, start=1):
            if callback is not None:
                callback(
                    ProgressEvent(
                        level="info",
                        message=f"Converting {media_item.title}",
                        queue_item_id=str(queue_item_id),
                        provider=provider_name,
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
                        provider=provider_name,
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
        sonarr_provider=cast(Any, FakeSonarrProvider()),
        radarr_provider=cast(Any, FakeRadarrProvider()),
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


def test_queue_pause_blocks_next_episode_until_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("walkmarr.queue_manager.ensure_required_tools", lambda: "AtomicParsley")

    class TwoEpisodeSonarrProvider(FakeSonarrProvider):
        def build_media_items(self, **kwargs: object) -> list[MediaItem]:
            output_root = kwargs["output_root"]
            assert isinstance(output_root, Path)
            return [
                MediaItem(
                    kind="episode",
                    source_path=Path("/tmp/source-a.mkv"),
                    output_path=output_root / "Futurama S01E01.mp4",
                    profile_name="animation",
                    title="A",
                    series_title="Futurama",
                    season_number=1,
                    episode_number=1,
                ),
                MediaItem(
                    kind="episode",
                    source_path=Path("/tmp/source-b.mkv"),
                    output_path=output_root / "Futurama S01E02.mp4",
                    profile_name="animation",
                    title="B",
                    series_title="Futurama",
                    season_number=1,
                    episode_number=2,
                ),
            ]

    first_finished = threading.Event()
    proceed_to_second = threading.Event()
    second_started = threading.Event()
    processed_titles: list[str] = []

    def _fake_process_media_items(**kwargs: object) -> BatchProcessResult:
        media_items = kwargs["media_items"]
        pause_callback = kwargs["pause_callback"]
        assert isinstance(media_items, list)
        assert callable(pause_callback)
        for index, media_item in enumerate(media_items, start=1):
            assert isinstance(media_item, MediaItem)
            pause_callback()
            processed_titles.append(media_item.title)
            if index == 1:
                first_finished.set()
                assert proceed_to_second.wait(timeout=2.0)
            else:
                second_started.set()
        return BatchProcessResult(converted=len(media_items), skipped=0, failed=0)

    monkeypatch.setattr("walkmarr.queue_manager.process_media_items", _fake_process_media_items)

    manager = QueueManager(
        config=_config(tmp_path),
        sonarr_provider=cast(Any, TwoEpisodeSonarrProvider()),
        radarr_provider=cast(Any, FakeRadarrProvider()),
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
    )
    try:
        queue_id = manager.add_item(
            QueueItem(id="", provider="sonarr", provider_item_id=1, title="Futurama")
        )

        assert first_finished.wait(timeout=2.0)
        manager.pause()
        proceed_to_second.set()
        time.sleep(0.1)

        assert processed_titles == ["A"]
        assert not second_started.is_set()
        paused = next(item for item in manager.get_items() if item.id == queue_id)
        assert paused.status == QueueItemStatus.PAUSED

        manager.resume()
        _wait_for(lambda: second_started.is_set())
        _wait_for(
            lambda: any(
                item.id == queue_id and item.status == QueueItemStatus.COMPLETE
                for item in manager.get_items()
            )
        )
        assert processed_titles == ["A", "B"]
    finally:
        manager.stop()


def test_queue_notifications_do_not_deadlock_on_reentrant_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("walkmarr.queue_manager.ensure_required_tools", lambda: "AtomicParsley")

    def _fake_process_media_items(**kwargs: object) -> BatchProcessResult:
        del kwargs
        return BatchProcessResult(converted=0, skipped=1, failed=0)

    monkeypatch.setattr("walkmarr.queue_manager.process_media_items", _fake_process_media_items)

    manager = QueueManager(
        config=_config(tmp_path),
        sonarr_provider=cast(Any, FakeSonarrProvider()),
        radarr_provider=cast(Any, FakeRadarrProvider()),
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
    )

    observed = {"seen": False}

    def _observer(_event_type: str, _item: QueueItem | None, _event: ProgressEvent | None) -> None:
        _ = manager.get_items()
        observed["seen"] = True

    manager.add_observer(_observer)
    manager.add_item(QueueItem(id="", provider="sonarr", provider_item_id=1, title="Futurama"))

    _wait_for(lambda: observed["seen"])


def test_queue_manager_stop_returns_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("walkmarr.queue_manager.ensure_required_tools", lambda: "AtomicParsley")
    manager = QueueManager(
        config=_config(tmp_path),
        sonarr_provider=cast(Any, FakeSonarrProvider()),
        radarr_provider=cast(Any, FakeRadarrProvider()),
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
    )
    manager.stop()


def test_sonarr_expansion_uses_cache_for_repeat_item(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("walkmarr.queue_manager.ensure_required_tools", lambda: "AtomicParsley")

    sonarr = FakeSonarrProvider()
    manager = QueueManager(
        config=_config(tmp_path),
        sonarr_provider=cast(Any, sonarr),
        radarr_provider=cast(Any, FakeRadarrProvider()),
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
    )
    queue_item = QueueItem(id="q1", provider="sonarr", provider_item_id=1, title="Futurama")

    first_items, _ = manager._expand_queue_item(queue_item)
    second_items, _ = manager._expand_queue_item(queue_item)

    assert len(first_items) == 1
    assert len(second_items) == 1
    assert sonarr.get_series_by_id_calls == 1
    assert sonarr.list_episodes_calls == 1
    assert sonarr.list_episode_files_calls == 1
    assert sonarr.build_media_items_kwargs is not None
    assert sonarr.build_media_items_kwargs["series_id"] == 1

    manager.stop()
