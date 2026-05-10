"""In-memory queue manager for V2 TUI workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import queue
from threading import Condition, Lock, Thread
from typing import Any
from uuid import uuid4

from rich.console import Console

from walkmarr.config import profile_name_for_radarr_movie, profile_name_for_sonarr_series
from walkmarr.exceptions import ProviderError, WalkmarrError
from walkmarr.models import AppConfig, MediaItem, QueueItem, QueueItemStatus
from walkmarr.process import CancellationToken, ProgressEvent, ensure_required_tools, process_media_items
from walkmarr.providers.radarr import RadarrProvider
from walkmarr.providers.sonarr import SonarrProvider


QueueObserver = Callable[[str, QueueItem | None, ProgressEvent | None], None]
_NOTIFY_STOP: tuple[str, QueueItem | None, ProgressEvent | None] = ("__stop__", None, None)


class QueueManager:
    """Single-worker in-memory queue manager with progress event fan-out."""

    def __init__(
        self,
        *,
        config: AppConfig,
        sonarr_provider: SonarrProvider,
        radarr_provider: RadarrProvider,
        console: Console,
    ) -> None:
        self._config = config
        self._sonarr = sonarr_provider
        self._radarr = radarr_provider
        self._console = console

        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._observers: list[QueueObserver] = []
        self._notifications: queue.SimpleQueue[tuple[str, QueueItem | None, ProgressEvent | None]] = (
            queue.SimpleQueue()
        )
        self._items: list[QueueItem] = []
        self._shutdown = False
        self._paused = config.queue_start_paused
        self._current_queue_item_id: str | None = None
        self._current_cancellation: CancellationToken | None = None
        self._atomicparsley_bin = ensure_required_tools()
        self._series_cache: dict[int, dict[str, Any]] = {}
        self._sonarr_expansion_cache: dict[int, tuple[list[MediaItem], str]] = {}
        self._movie_cache: dict[int, dict[str, Any]] = {}
        self._notifier = Thread(
            target=self._notification_loop,
            name="walkmarr-queue-notifier",
            daemon=True,
        )
        self._notifier.start()
        self._worker = Thread(target=self._worker_loop, name="walkmarr-queue-worker", daemon=True)
        self._worker.start()

    def add_observer(self, callback: QueueObserver) -> None:
        with self._lock:
            self._observers.append(callback)

    def add_item(self, item: QueueItem) -> str:
        with self._condition:
            if self._shutdown:
                raise WalkmarrError("Queue manager is shutting down")
            duplicate = self._find_duplicate_locked(item.provider, item.provider_item_id)
            if duplicate is not None and duplicate.status in {
                QueueItemStatus.PENDING,
                QueueItemStatus.EXPANDING,
                QueueItemStatus.RUNNING,
                QueueItemStatus.PAUSED,
            }:
                raise WalkmarrError(f"'{item.title}' is already in the queue")
            queue_id = item.id or uuid4().hex
            queued = replace(item, id=queue_id, status=QueueItemStatus.PENDING)
            self._items.append(queued)
            self._notify_locked("queue", queued, None)
            self._notify_locked(
                "log",
                queued,
                ProgressEvent(
                    level="info",
                    message=f"[{queued.title}] Queued",
                    queue_item_id=queued.id,
                    provider=queued.provider,
                    current_stage="queued",
                ),
            )
            self._condition.notify_all()
            return queue_id

    def remove_item(self, queue_item_id: str) -> None:
        with self._condition:
            index = self._index_for_id_locked(queue_item_id)
            item = self._items[index]
            if item.status != QueueItemStatus.PENDING:
                raise WalkmarrError("Only pending queue items can be removed")
            removed = self._items.pop(index)
            self._notify_locked("queue", removed, None)

    def move_up(self, queue_item_id: str) -> None:
        with self._condition:
            index = self._index_for_id_locked(queue_item_id)
            if index == 0:
                return
            if self._items[index].status != QueueItemStatus.PENDING:
                raise WalkmarrError("Only pending queue items can be moved")
            self._items[index - 1], self._items[index] = self._items[index], self._items[index - 1]
            self._notify_locked("queue", self._items[index - 1], None)

    def move_down(self, queue_item_id: str) -> None:
        with self._condition:
            index = self._index_for_id_locked(queue_item_id)
            if index >= len(self._items) - 1:
                return
            if self._items[index].status != QueueItemStatus.PENDING:
                raise WalkmarrError("Only pending queue items can be moved")
            self._items[index], self._items[index + 1] = self._items[index + 1], self._items[index]
            self._notify_locked("queue", self._items[index], None)

    def pause(self) -> None:
        with self._condition:
            self._paused = True
            current = (
                self._find_by_id_locked(self._current_queue_item_id)
                if self._current_queue_item_id is not None
                else None
            )
            if current is not None and current.status in {QueueItemStatus.EXPANDING, QueueItemStatus.RUNNING}:
                self._update_item_locked(current.id, status=QueueItemStatus.PAUSED)
                current = self._find_by_id_locked(current.id)
            self._notify_locked(
                "log",
                current,
                ProgressEvent(level="warning", message="Queue paused", current_stage="queued"),
            )

    def resume(self) -> None:
        with self._condition:
            self._paused = False
            current = (
                self._find_by_id_locked(self._current_queue_item_id)
                if self._current_queue_item_id is not None
                else None
            )
            if current is not None and current.status == QueueItemStatus.PAUSED:
                self._update_item_locked(current.id, status=QueueItemStatus.RUNNING)
                current = self._find_by_id_locked(current.id)
            self._notify_locked(
                "log",
                current,
                ProgressEvent(level="info", message="Queue resumed", current_stage="queued"),
            )
            self._condition.notify_all()

    def cancel_current(self) -> None:
        with self._condition:
            if self._current_cancellation is not None:
                self._current_cancellation.cancel()
                current = (
                    self._find_by_id_locked(self._current_queue_item_id)
                    if self._current_queue_item_id is not None
                    else None
                )
                self._notify_locked(
                    "log",
                    current,
                    ProgressEvent(level="warning", message="Cancel requested", current_stage="canceled"),
                )

    def clear_pending(self) -> None:
        with self._condition:
            self._items = [i for i in self._items if i.status != QueueItemStatus.PENDING]
            self._notify_locked("queue", None, None)

    def clear_completed(self) -> None:
        with self._condition:
            self._items = [i for i in self._items if i.status != QueueItemStatus.COMPLETE]
            self._notify_locked("queue", None, None)

    def get_items(self) -> list[QueueItem]:
        with self._lock:
            return list(self._items)

    def get_current_item(self) -> QueueItem | None:
        with self._lock:
            if self._current_queue_item_id is None:
                return None
            return self._find_by_id_locked(self._current_queue_item_id)

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def summary(self) -> str:
        with self._lock:
            pending = sum(1 for item in self._items if item.status == QueueItemStatus.PENDING)
            complete = sum(1 for item in self._items if item.status == QueueItemStatus.COMPLETE)
            failed = sum(1 for item in self._items if item.status == QueueItemStatus.FAILED)
            running = self._find_running_locked()
            state = "paused" if self._paused else ("running" if running is not None else "idle")
            current = "none"
            if running is not None:
                total = running.total_files or 0
                current = f"{running.title} {running.completed_files}/{total}"
            return (
                f"Queue: {state} | Pending: {pending} | Complete: {complete} "
                f"| Failed: {failed} | Current: {current}"
            )

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._shutdown or self._has_work_locked())
                if self._shutdown:
                    return
                next_item = self._next_pending_locked()
                if next_item is None:
                    continue
                self._current_queue_item_id = next_item.id
                current_cancellation = CancellationToken()
                self._current_cancellation = current_cancellation
                self._update_item_locked(
                    next_item.id,
                    status=QueueItemStatus.EXPANDING,
                    started_at=datetime.now(),
                    last_message="Expanding provider item",
                )
                self._notify_locked(
                    "log",
                    self._find_by_id_locked(next_item.id),
                    ProgressEvent(
                        level="info",
                        message=f"[{next_item.title}] Expanding",
                        queue_item_id=next_item.id,
                        provider=next_item.provider,
                        current_stage="expanding",
                    ),
                )

            try:
                media_items, profile_name = self._expand_queue_item(next_item)
                with self._condition:
                    status = QueueItemStatus.PAUSED if self._paused else QueueItemStatus.RUNNING
                    self._update_item_locked(
                        next_item.id,
                        status=status,
                        profile_name=profile_name,
                        total_files=len(media_items),
                        output_root=str(self._output_root_for_provider(next_item.provider)),
                        last_message="Running",
                    )
                    self._notify_locked("queue", self._find_by_id_locked(next_item.id), None)

                result = process_media_items(
                    config=self._config,
                    media_items=media_items,
                    provider_name=next_item.provider,
                    profile=self._config.profiles[profile_name],
                    atomicparsley_bin=self._atomicparsley_bin,
                    console=self._console,
                    dry_run=next_item.dry_run,
                    overwrite=next_item.mode == "overwrite",
                    continue_on_error=self._config.queue_continue_on_error,
                    queue_item_id=next_item.id,
                    cancellation_token=current_cancellation,
                    pause_callback=lambda: self._wait_while_paused(current_cancellation),
                    progress_callback=self._on_progress,
                )

                with self._condition:
                    status = QueueItemStatus.COMPLETE
                    if current_cancellation.is_canceled:
                        status = QueueItemStatus.CANCELED
                    self._update_item_locked(
                        next_item.id,
                        status=status,
                        finished_at=datetime.now(),
                        last_message="Complete" if status == QueueItemStatus.COMPLETE else "Canceled",
                    )
                    if result.failed > 0 and status == QueueItemStatus.COMPLETE:
                        self._update_item_locked(next_item.id, status=QueueItemStatus.FAILED)
                    self._notify_locked("queue", self._find_by_id_locked(next_item.id), None)
            except Exception as exc:
                with self._condition:
                    status = QueueItemStatus.CANCELED
                    if self._current_cancellation is None or not self._current_cancellation.is_canceled:
                        status = QueueItemStatus.FAILED
                    self._update_item_locked(
                        next_item.id,
                        status=status,
                        finished_at=datetime.now(),
                        error=str(exc),
                        last_message=str(exc),
                    )
                    self._notify_locked(
                        "log",
                        self._find_by_id_locked(next_item.id),
                        ProgressEvent(
                            level="error",
                            message=f"[{next_item.title}] {exc}",
                            queue_item_id=next_item.id,
                            provider=next_item.provider,
                            current_stage="failed",
                        ),
                    )
                    self._notify_locked("queue", self._find_by_id_locked(next_item.id), None)
            finally:
                with self._condition:
                    self._current_cancellation = None
                    self._current_queue_item_id = None

    def stop(self) -> None:
        """Stop queue worker and notifier threads safely."""
        with self._condition:
            self._shutdown = True
            if self._current_cancellation is not None:
                self._current_cancellation.cancel()
            self._condition.notify_all()

        self._notifications.put(_NOTIFY_STOP)
        self._worker.join(timeout=5)
        self._notifier.join(timeout=5)

    def _wait_while_paused(self, cancellation_token: CancellationToken) -> None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._shutdown or not self._paused or cancellation_token.is_canceled
            )

    def _on_progress(self, event: ProgressEvent) -> None:
        queue_item_id = event.queue_item_id
        if queue_item_id is None:
            return
        with self._condition:
            item = self._find_by_id_locked(queue_item_id)
            if item is None:
                return

            completed = item.completed_files
            skipped = item.skipped_files
            failed = item.failed_files

            if event.current_stage == "complete" and event.item is not None:
                completed += 1
            elif event.current_stage == "skipping" and event.item is not None:
                skipped += 1
            elif event.current_stage == "failed" and event.item is not None:
                failed += 1

            current_label = item.current_label
            if event.item is not None:
                current_label = self._media_label(event.item)

            self._update_item_locked(
                queue_item_id,
                completed_files=completed,
                skipped_files=skipped,
                failed_files=failed,
                current_label=current_label,
                last_message=event.message,
            )
            prefixed_message = event.message
            if item.title not in prefixed_message:
                prefixed_message = f"[{item.title}] {event.message}"
            self._notify_locked(
                "log",
                self._find_by_id_locked(queue_item_id),
                replace(event, message=prefixed_message),
            )
            self._notify_locked("queue", self._find_by_id_locked(queue_item_id), None)

    def _expand_queue_item(self, queue_item: QueueItem) -> tuple[list[MediaItem], str]:
        if queue_item.provider == "sonarr":
            cached = self._sonarr_expansion_cache.get(queue_item.provider_item_id)
            if cached is not None:
                self._notify_locked(
                    "log",
                    self._find_by_id_locked(queue_item.id),
                    ProgressEvent(
                        level="debug",
                        message=f"[{queue_item.title}] Using cached expansion payload",
                        queue_item_id=queue_item.id,
                        provider=queue_item.provider,
                        current_stage="expanding",
                    ),
                )
                return cached

            self._notify_locked(
                "log",
                self._find_by_id_locked(queue_item.id),
                ProgressEvent(
                    level="info",
                    message=f"[{queue_item.title}] Fetching series details",
                    queue_item_id=queue_item.id,
                    provider=queue_item.provider,
                    current_stage="expanding",
                ),
            )
            selected = self._series_cache.get(queue_item.provider_item_id)
            if selected is None:
                selected = self._sonarr.get_series_by_id(queue_item.provider_item_id)
                self._series_cache[queue_item.provider_item_id] = selected

            selected_title = str(selected.get("title", queue_item.title))
            profile_name = profile_name_for_sonarr_series(self._config, selected)

            self._notify_locked(
                "log",
                self._find_by_id_locked(queue_item.id),
                ProgressEvent(
                    level="info",
                    message=f"[{queue_item.title}] Fetching episodes",
                    queue_item_id=queue_item.id,
                    provider=queue_item.provider,
                    current_stage="expanding",
                ),
            )
            episodes = self._sonarr.list_episodes(queue_item.provider_item_id)

            self._notify_locked(
                "log",
                self._find_by_id_locked(queue_item.id),
                ProgressEvent(
                    level="info",
                    message=f"[{queue_item.title}] Fetching episode files",
                    queue_item_id=queue_item.id,
                    provider=queue_item.provider,
                    current_stage="expanding",
                ),
            )
            episode_files = self._sonarr.list_episode_files(queue_item.provider_item_id)

            self._notify_locked(
                "log",
                self._find_by_id_locked(queue_item.id),
                ProgressEvent(
                    level="info",
                    message=f"[{queue_item.title}] Building media file list",
                    queue_item_id=queue_item.id,
                    provider=queue_item.provider,
                    current_stage="expanding",
                ),
            )
            media_items = self._sonarr.build_media_items(
                series_title=selected_title,
                episodes=episodes,
                episode_files=episode_files,
                profile_name=profile_name,
                path_mappings=self._config.path_mappings,
                output_root=self._config.output_roots["shows"],
                series_id=queue_item.provider_item_id,
                series_genre=_primary_genre(selected),
                series_artwork_url=self._sonarr.poster_url(selected),
                allow_unmapped_existing_local=self._config.allow_unmapped_existing_local,
            )
            if not media_items:
                raise ProviderError(f"No episode files found for '{selected_title}'")
            result = (media_items, profile_name)
            self._sonarr_expansion_cache[queue_item.provider_item_id] = result
            return result

        if queue_item.provider == "radarr":
            self._notify_locked(
                "log",
                self._find_by_id_locked(queue_item.id),
                ProgressEvent(
                    level="info",
                    message=f"[{queue_item.title}] Fetching movie details",
                    queue_item_id=queue_item.id,
                    provider=queue_item.provider,
                    current_stage="expanding",
                ),
            )
            selected_movie = self._movie_cache.get(queue_item.provider_item_id)
            if selected_movie is None:
                selected_movie = self._radarr.get_movie_by_id(queue_item.provider_item_id)
                self._movie_cache[queue_item.provider_item_id] = selected_movie

            profile_name = profile_name_for_radarr_movie(self._config, selected_movie)

            self._notify_locked(
                "log",
                self._find_by_id_locked(queue_item.id),
                ProgressEvent(
                    level="info",
                    message=f"[{queue_item.title}] Building media file",
                    queue_item_id=queue_item.id,
                    provider=queue_item.provider,
                    current_stage="expanding",
                ),
            )
            media_item = self._radarr.build_media_item(
                movie=selected_movie,
                profile_name=profile_name,
                path_mappings=self._config.path_mappings,
                output_root=self._config.output_roots["movies"],
                allow_unmapped_existing_local=self._config.allow_unmapped_existing_local,
            )
            return [media_item], profile_name

        raise WalkmarrError(f"Unsupported provider: {queue_item.provider}")

    def _output_root_for_provider(self, provider: str) -> Any:
        return self._config.output_roots["shows" if provider == "sonarr" else "movies"]

    def _media_label(self, media_item: MediaItem) -> str:
        if media_item.kind == "episode":
            season = media_item.season_number or 0
            episode = media_item.episode_number or 0
            return f"S{season:02d}E{episode:02d} {media_item.title}"
        return media_item.title

    def _has_work_locked(self) -> bool:
        return (not self._paused) and any(item.status == QueueItemStatus.PENDING for item in self._items)

    def _find_running_locked(self) -> QueueItem | None:
        for item in self._items:
            if item.status in {QueueItemStatus.RUNNING, QueueItemStatus.EXPANDING, QueueItemStatus.PAUSED}:
                return item
        return None

    def _next_pending_locked(self) -> QueueItem | None:
        for item in self._items:
            if item.status == QueueItemStatus.PENDING:
                return item
        return None

    def _find_duplicate_locked(self, provider: str, provider_item_id: int) -> QueueItem | None:
        for item in self._items:
            if item.provider == provider and item.provider_item_id == provider_item_id:
                return item
        return None

    def _find_by_id_locked(self, queue_item_id: str) -> QueueItem | None:
        for item in self._items:
            if item.id == queue_item_id:
                return item
        return None

    def _index_for_id_locked(self, queue_item_id: str) -> int:
        for index, item in enumerate(self._items):
            if item.id == queue_item_id:
                return index
        raise WalkmarrError(f"Queue item not found: {queue_item_id}")

    def _update_item_locked(self, queue_item_id: str, **fields: object) -> None:
        index = self._index_for_id_locked(queue_item_id)
        self._items[index] = replace(self._items[index], **fields)

    def _notify_locked(self, event_type: str, item: QueueItem | None, event: ProgressEvent | None) -> None:
        self._notifications.put((event_type, item, event))

    def _notification_loop(self) -> None:
        while True:
            event_type, item, event = self._notifications.get()
            if (event_type, item, event) == _NOTIFY_STOP:
                return
            with self._lock:
                observers = list(self._observers)
            for observer in observers:
                try:
                    observer(event_type, item, event)
                except Exception:
                    continue


def _primary_genre(payload: dict[str, Any]) -> str | None:
    genres = payload.get("genres")
    if not isinstance(genres, list):
        return None
    for value in genres:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
