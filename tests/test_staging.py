from pathlib import Path

import pytest

from walkmarr.process import (
    is_network_mount_path,
    planned_staging_path,
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
