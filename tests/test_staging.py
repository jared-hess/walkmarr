from pathlib import Path
from io import StringIO

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
