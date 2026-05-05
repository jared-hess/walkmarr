from pathlib import Path
from io import StringIO
import errno

import pytest
from rich.console import Console

from walkmarr.process import (
    is_network_mount_path,
    planned_staging_path,
    stage_source_file,
    should_stage_source_path,
)


def test_should_stage_mode_always() -> None:
    assert should_stage_source_path(Path("/media/source.mkv"), "always")


def test_should_stage_mode_never() -> None:
    assert not should_stage_source_path(Path("/media/source.mkv"), "never")


def test_planned_staging_path_is_deterministic() -> None:
    source = Path("/mnt/z/movies/Alien (1979).mkv")
    staging_dir = Path("/tmp/walkmarr-staging")
    first = planned_staging_path(source, staging_dir)
    second = planned_staging_path(source, staging_dir)
    assert first == second
    assert first.parent == staging_dir
    assert first.suffix == ".mkv"


def test_is_network_mount_path_detects_9p(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "walkmarr.process._read_mount_entries",
        lambda: [
            (Path("/"), "ext4", "rw,relatime"),
            (Path("/mnt/z"), "9p", "rw,dirsync,aname=drvfs;path=UNC\\server\\media"),
        ],
    )
    assert is_network_mount_path(Path("/mnt/z/movies/file.mkv"))


def test_is_network_mount_path_false_for_ext4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "walkmarr.process._read_mount_entries",
        lambda: [
            (Path("/"), "ext4", "rw,relatime"),
            (Path("/mnt/local"), "ext4", "rw,relatime"),
        ],
    )
    assert not is_network_mount_path(Path("/mnt/local/movies/file.mkv"))


def test_stage_source_file_copies_to_staging(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"abcdef" * 1024)
    staging_dir = tmp_path / "staging"
    console = Console(file=StringIO(), force_terminal=False, color_system=None)

    staged = stage_source_file(source, staging_dir, console)

    assert staged.exists()
    assert staged.parent == staging_dir
    assert staged.read_bytes() == source.read_bytes()


def test_stage_source_file_retries_on_eio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"abcdef")
    staging_dir = tmp_path / "staging"
    console = Console(file=StringIO(), force_terminal=False, color_system=None)

    calls = {"count": 0}

    def _fake_copy_file_chunked(
        *,
        source_path: Path,
        staged_path: Path,
        source_size: int,
        console: Console,
        show_progress: bool,
    ) -> None:
        del source_path, source_size, console, show_progress
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError(errno.EIO, "Input/output error")
        staged_path.write_bytes(b"ok")

    monkeypatch.setattr("walkmarr.process._copy_file_chunked", _fake_copy_file_chunked)
    monkeypatch.setattr("walkmarr.process.time.sleep", lambda _seconds: None)

    staged = stage_source_file(source, staging_dir, console, show_progress=False)
    assert calls["count"] == 2
    assert staged.read_bytes() == b"ok"
