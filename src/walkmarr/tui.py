"""Textual TUI for queue-oriented Walkmarr workflows."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import threading
from typing import Any

from rich.console import Console
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static

from walkmarr.config import resolve_api_key
from walkmarr.exceptions import WalkmarrError
from walkmarr.models import AppConfig, QueueItem, QueueItemStatus
from walkmarr.process import ProgressEvent
from walkmarr.providers.radarr import RadarrProvider
from walkmarr.providers.sonarr import SonarrProvider
from walkmarr.queue_manager import QueueManager


STATUS_ICON = {
    QueueItemStatus.PENDING: "○",
    QueueItemStatus.EXPANDING: "…",
    QueueItemStatus.RUNNING: "▶",
    QueueItemStatus.PAUSED: "⏸",
    QueueItemStatus.COMPLETE: "✓",
    QueueItemStatus.FAILED: "✗",
    QueueItemStatus.CANCELED: "×",
}


@dataclass(frozen=True)
class TableNavigator:
    """Centralized DataTable cursor and viewport movement behavior."""

    prefetch_margin: int = 3

    def move(self, table: DataTable, delta: int) -> None:
        if delta > 0:
            table.action_cursor_down()
        else:
            table.action_cursor_up()

    def ensure_cursor_visible(self, table: DataTable) -> None:
        row = table.cursor_row
        viewport_height = int(table.content_region.height)
        if viewport_height <= 0:
            return

        header_rows = 1 if getattr(table, "show_header", False) else 0
        visible_rows = max(1, viewport_height - header_rows)

        top_row = int(table.scroll_y)
        bottom_row = top_row + visible_rows - 1
        prefetch_margin = min(self.prefetch_margin, max(0, visible_rows - 1))

        if row < top_row:
            table.scroll_to(y=row, animate=False, force=True, immediate=True)
        elif row > bottom_row - prefetch_margin:
            target_top = row - visible_rows + 1 + prefetch_margin
            if target_top < 0:
                target_top = 0
            table.scroll_to(y=target_top, animate=False, force=True, immediate=True)


class QueueChanged(Message):
    def __init__(self, item: QueueItem | None) -> None:
        self.item = item
        super().__init__()


class LogEvent(Message):
    def __init__(self, event: ProgressEvent) -> None:
        self.event = event
        super().__init__()


class ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation modal."""

    BINDINGS = [
        Binding("enter", "confirm", "Confirm", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("left", "focus_no", show=False),
        Binding("right", "focus_yes", show=False),
        Binding("tab", "focus_next", show=False),
        Binding("shift+tab", "focus_previous", show=False),
    ]

    CSS = """
    ConfirmScreen {
        align: center middle;
    }

    #confirm-box {
        width: 70;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }

    #confirm-actions {
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self._prompt)
            with Horizontal(id="confirm-actions"):
                yield Button("Cancel", id="confirm-no")
                yield Button("Confirm", id="confirm-yes", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#confirm-yes", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_focus_no(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def action_focus_yes(self) -> None:
        self.query_one("#confirm-yes", Button).focus()


class WalkmarrTUI(App[None]):
    """Queue-first TUI that remains interactive during background work."""

    CSS = """
    #top-bar { height: 3; }
    #main { height: 1fr; }
    #bottom { height: 1fr; }
    #media-pane, #details-pane, #queue-pane, #log-pane { border: solid gray; }
    #summary { height: 1; }
    #actions { height: 7; }
    Button { margin: 0 1 0 0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "focus_media", "Focus Media", priority=True),
        Binding("tab", "cycle_focus", "Focus"),
        Binding("j", "cursor_down", "Down"),
        Binding("k", "cursor_up", "Up"),
        Binding("a", "add_missing", "Add Missing"),
        Binding("A", "add_overwrite", "Add Overwrite"),
        Binding("d", "add_dry_run", "Add Dry Run"),
        Binding("space", "toggle_pause", "Pause/Resume"),
        Binding("x", "cancel_current", "Cancel Current"),
        Binding("delete", "remove_pending", "Remove Pending"),
        Binding("u", "move_up", "Move Up"),
        Binding("J", "queue_move_down", "Queue Down"),
        Binding("K", "queue_move_up", "Queue Up"),
        Binding("C", "clear_completed", "Clear Completed"),
        Binding("X", "clear_pending", "Clear Pending"),
        Binding("r", "refresh_media", "Refresh"),
        Binding("p", "toggle_provider", "Provider"),
        Binding("slash", "focus_search", "Search"),
    ]

    provider = reactive("sonarr")
    focus_zone = reactive("media")

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._table_navigator = TableNavigator(prefetch_margin=3)
        self._sonarr = SonarrProvider(
            url=config.providers["sonarr"].url,
            api_key=resolve_api_key(config, "sonarr"),
        )
        self._radarr = RadarrProvider(
            url=config.providers["radarr"].url,
            api_key=resolve_api_key(config, "radarr"),
        )
        self._queue = QueueManager(
            config=config,
            sonarr_provider=self._sonarr,
            radarr_provider=self._radarr,
            console=Console(file=StringIO(), force_terminal=False, color_system=None),
        )
        self._queue.add_observer(self._on_queue_observer)
        self._media_items: list[dict[str, Any]] = []
        self._filtered_media_items: list[dict[str, Any]] = []
        self._selected_media_id: int | None = None
        self._selected_queue_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Provider: sonarr", id="top-bar")
        yield Static(self._queue.summary(), id="summary")
        with Horizontal(id="main"):
            with Vertical(id="media-pane"):
                yield Input(placeholder="Search", id="search")
                yield DataTable(id="media-table")
            with Vertical(id="details-pane"):
                yield Static("Details", id="details")
                with Horizontal(id="actions"):
                    yield Button("Add Missing", id="btn-add-missing")
                    yield Button("Add Overwrite", id="btn-add-overwrite")
                    yield Button("Dry Run", id="btn-add-dry")
                with Horizontal(id="actions-queue"):
                    yield Button("Pause/Resume", id="btn-pause")
                    yield Button("Cancel Current", id="btn-cancel")
                    yield Button("Remove Pending", id="btn-remove")
                    yield Button("Move Up", id="btn-up")
                    yield Button("Move Down", id="btn-down")
                    yield Button("Clear Done", id="btn-clear-done")
                    yield Button("Clear Pending", id="btn-clear-pending")
        with Horizontal(id="bottom"):
            with Vertical(id="queue-pane"):
                yield DataTable(id="queue-table")
            with Vertical(id="log-pane"):
                yield RichLog(id="log", wrap=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        media_table = self.query_one("#media-table", DataTable)
        media_table.cursor_type = "row"
        media_table.add_columns("Title", "Year", "Provider ID")
        queue_table = self.query_one("#queue-table", DataTable)
        queue_table.cursor_type = "row"
        queue_table.add_columns("S", "Title", "Progress", "Mode", "Profile")
        self.action_refresh_media()
        self._refresh_queue_table()
        self._refresh_summary()
        self.focus_zone = "media"
        media_table.focus()

    def on_unmount(self) -> None:
        self._queue.stop()

    def action_cycle_focus(self) -> None:
        order = [
            ("media", "#media-table"),
            ("details", "#btn-add-missing"),
            ("queue", "#queue-table"),
            ("log", "#log"),
            ("search", "#search"),
        ]
        index = next((i for i, (zone, _) in enumerate(order) if zone == self.focus_zone), 0)
        next_index = (index + 1) % len(order)
        next_zone, selector = order[next_index]
        self.focus_zone = next_zone
        self.query_one(selector).focus()

    def action_cursor_down(self) -> None:
        self._move_cursor(1)

    def action_cursor_up(self) -> None:
        self._move_cursor(-1)

    def action_focus_search(self) -> None:
        self.focus_zone = "search"
        self.query_one("#search", Input).focus()

    def action_focus_media(self) -> None:
        self.focus_zone = "media"
        self.query_one("#media-table", DataTable).focus()

    def action_refresh_media(self) -> None:
        try:
            if self.provider == "sonarr":
                self._media_items = self._sonarr.list_series()
            else:
                self._media_items = self._radarr.list_movies()
        except Exception as exc:
            self._log_message(f"Provider refresh failed: {exc}")
            return
        self._apply_search_filter(self.query_one("#search", Input).value)

    def action_toggle_provider(self) -> None:
        self.provider = "radarr" if self.provider == "sonarr" else "sonarr"
        self.action_refresh_media()
        self._refresh_summary()

    def action_add_missing(self) -> None:
        self._add_selected_media_to_queue(mode="missing_only", dry_run=False)

    def action_add_overwrite(self) -> None:
        selected = self._selected_media()
        if selected is None:
            self._log_message("No media item selected")
            return

        title = str(selected.get("title", ""))

        def _after_confirm(confirmed: bool) -> None:
            if confirmed:
                self._add_selected_media_to_queue(mode="overwrite", dry_run=False)

        self.push_screen(
            ConfirmScreen(
                f"Add '{title}' to queue in overwrite mode? Existing exported files may be replaced. "
                "Source files will not be modified."
            ),
            _after_confirm,
        )

    def action_add_dry_run(self) -> None:
        self._add_selected_media_to_queue(mode="missing_only", dry_run=True)

    def action_toggle_pause(self) -> None:
        if self._queue.is_paused():
            self._queue.resume()
        else:
            self._queue.pause()
        self._refresh_summary()

    def action_cancel_current(self) -> None:
        self._queue.cancel_current()

    def action_remove_pending(self) -> None:
        if self._selected_queue_id is None:
            return
        try:
            self._queue.remove_item(self._selected_queue_id)
        except Exception as exc:
            self._log_message(str(exc))

    def action_move_up(self) -> None:
        if self._selected_queue_id is None:
            return
        try:
            self._queue.move_up(self._selected_queue_id)
        except Exception as exc:
            self._log_message(str(exc))

    def action_move_down(self) -> None:
        if self._selected_queue_id is None:
            return
        try:
            self._queue.move_down(self._selected_queue_id)
        except Exception as exc:
            self._log_message(str(exc))

    def action_queue_move_up(self) -> None:
        self.action_move_up()

    def action_queue_move_down(self) -> None:
        self.action_move_down()

    def action_clear_completed(self) -> None:
        self._queue.clear_completed()

    def action_clear_pending(self) -> None:
        def _after_confirm(confirmed: bool) -> None:
            if confirmed:
                self._queue.clear_pending()

        self.push_screen(
            ConfirmScreen("Clear all pending queue items?"),
            _after_confirm,
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._apply_search_filter(event.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._handle_table_row_change(event.data_table, event.cursor_row)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._handle_table_row_change(event.data_table, event.cursor_row)

    def _handle_table_row_change(self, table: DataTable, cursor_row: int) -> None:
        if table.id == "media-table":
            if 0 <= cursor_row < len(self._filtered_media_items):
                selected = self._filtered_media_items[cursor_row]
                selected_id = selected.get("id")
                if isinstance(selected_id, int):
                    self._selected_media_id = selected_id
                self._update_details_for_media(selected)
        elif table.id == "queue-table":
            items = self._queue.get_items()
            if 0 <= cursor_row < len(items):
                queue_id = items[cursor_row].id
                self._selected_queue_id = queue_id
                self._update_details_for_queue(queue_id)

        self._table_navigator.ensure_cursor_visible(table)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "btn-add-missing": self.action_add_missing,
            "btn-add-overwrite": self.action_add_overwrite,
            "btn-add-dry": self.action_add_dry_run,
            "btn-pause": self.action_toggle_pause,
            "btn-cancel": self.action_cancel_current,
            "btn-remove": self.action_remove_pending,
            "btn-up": self.action_move_up,
            "btn-down": self.action_move_down,
            "btn-clear-done": self.action_clear_completed,
            "btn-clear-pending": self.action_clear_pending,
        }
        action = mapping.get(event.button.id or "")
        if action is not None:
            action()

    def on_queue_changed(self, _message: QueueChanged) -> None:
        self._refresh_queue_table()
        self._refresh_summary()
        if self._selected_queue_id is not None:
            self._update_details_for_queue(self._selected_queue_id)

    def on_log_event(self, message: LogEvent) -> None:
        stage = message.event.current_stage or "info"
        self._log_message(f"[{stage}] {message.event.message}")

    def _on_queue_observer(
        self,
        event_type: str,
        item: QueueItem | None,
        event: ProgressEvent | None,
    ) -> None:
        if event_type == "queue":
            self._post_ui_message(QueueChanged(item))
        elif event_type == "log" and event is not None:
            self._post_ui_message(LogEvent(event))

    def _post_ui_message(self, message: Message) -> None:
        if threading.get_ident() == self._thread_id:
            self.post_message(message)
            return
        self.call_from_thread(self.post_message, message)

    def _apply_search_filter(self, term: str) -> None:
        query = term.casefold().strip()
        if query:
            self._filtered_media_items = [
                item
                for item in self._media_items
                if query in str(item.get("title", "")).casefold()
            ]
        else:
            self._filtered_media_items = list(self._media_items)
        self._refresh_media_table()

    def _refresh_media_table(self) -> None:
        table = self.query_one("#media-table", DataTable)
        table.clear()
        for index, item in enumerate(self._filtered_media_items):
            title = str(item.get("title", ""))
            year = str(item.get("year", ""))
            provider_id = str(item.get("id", ""))
            table.add_row(title, year, provider_id, key=str(index))

        if self._filtered_media_items:
            first = self._filtered_media_items[0]
            first_id = first.get("id")
            if isinstance(first_id, int):
                self._selected_media_id = first_id
                self._update_details_for_media(first)

    def _refresh_queue_table(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        for item in self._queue.get_items():
            icon = STATUS_ICON[item.status]
            total = item.total_files or 0
            progress = f"{item.completed_files}/{total}" if total > 0 else item.status.value
            profile = item.profile_name or "-"
            table.add_row(icon, self._display_title(item), progress, item.mode, profile, key=item.id)

    def _refresh_summary(self) -> None:
        self.query_one("#summary", Static).update(self._queue.summary())
        self.query_one("#top-bar", Static).update(f"Provider: {self.provider}")

    def _display_title(self, item: QueueItem) -> str:
        if item.year is not None:
            return f"{item.title} ({item.year})"
        return item.title

    def _update_details_for_media(self, item: dict[str, Any]) -> None:
        details = self.query_one("#details", Static)
        title = str(item.get("title", ""))
        year = item.get("year")
        suffix = f" ({year})" if isinstance(year, int) else ""
        details.update(
            f"Title: {title}{suffix}\n"
            f"Provider: {self.provider}\n"
            "Actions: [Add Missing] [Add Overwrite] [Dry Run]"
        )

    def _update_details_for_queue(self, queue_item_id: str) -> None:
        item = next((it for it in self._queue.get_items() if it.id == queue_item_id), None)
        if item is None:
            return
        total = item.total_files or 0
        details = self.query_one("#details", Static)
        details.update(
            f"Queue Item: {self._display_title(item)}\n"
            f"Provider: {item.provider}\n"
            f"Mode: {item.mode}\n"
            f"Status: {item.status.value}\n"
            f"Files: {item.completed_files}/{total}\n"
            f"Skipped: {item.skipped_files} Failed: {item.failed_files}\n"
            f"Current: {item.current_label or '-'}"
        )

    def _selected_media(self) -> dict[str, Any] | None:
        if self._selected_media_id is None:
            return None
        for item in self._media_items:
            if item.get("id") == self._selected_media_id:
                return item
        return None

    def _existing_queue_item(self, provider_item_id: int) -> QueueItem | None:
        for item in self._queue.get_items():
            if item.provider == self.provider and item.provider_item_id == provider_item_id:
                return item
        return None

    def _add_selected_media_to_queue(self, *, mode: str, dry_run: bool) -> None:
        selected = self._selected_media()
        if selected is None:
            self._log_message("No media item selected")
            return
        provider_item_id = selected.get("id")
        if not isinstance(provider_item_id, int):
            self._log_message("Selected row has no valid provider id")
            return
        title = str(selected.get("title", ""))
        year_value = selected.get("year")
        year = year_value if isinstance(year_value, int) else None
        existing = self._existing_queue_item(provider_item_id)
        if existing is not None and existing.status == QueueItemStatus.COMPLETE:
            def _after_complete_readd(confirmed: bool) -> None:
                if confirmed:
                    self._enqueue_item(provider_item_id, title, year, mode, dry_run)

            self.push_screen(
                ConfirmScreen(f"'{title}' is already complete. Add it to queue again?"),
                _after_complete_readd,
            )
            return

        self._enqueue_item(provider_item_id, title, year, mode, dry_run)

    def _enqueue_item(
        self,
        provider_item_id: int,
        title: str,
        year: int | None,
        mode: str,
        dry_run: bool,
    ) -> None:
        try:
            queue_item = QueueItem(
                id="",
                provider=self.provider,
                provider_item_id=provider_item_id,
                title=title,
                year=year,
                mode="overwrite" if mode == "overwrite" else "missing_only",
                dry_run=dry_run,
            )
            self._queue.add_item(queue_item)
        except WalkmarrError as exc:
            self._log_message(str(exc))

    def _move_cursor(self, delta: int) -> None:
        if self.focus_zone == "media":
            table = self.query_one("#media-table", DataTable)
            self._table_navigator.move(table, delta)
            return

        if self.focus_zone == "queue":
            table = self.query_one("#queue-table", DataTable)
            self._table_navigator.move(table, delta)

    def _log_message(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)


def run_tui(config: AppConfig) -> None:
    app = WalkmarrTUI(config)
    app.run()
