from pathlib import Path

import pytest

from walkmarr.exceptions import PathMappingError
from walkmarr.models import PathMapping
from walkmarr.paths import map_remote_path_to_local


def test_map_windows_backslashes() -> None:
    mappings = [PathMapping(remote="Z:/shows", local=Path("/mnt/z/shows"))]
    mapped = map_remote_path_to_local(r"Z:\shows\Futurama\Season 1\file.mkv", mappings)
    assert mapped == Path("/mnt/z/shows/Futurama/Season 1/file.mkv")


def test_map_windows_slashes() -> None:
    mappings = [PathMapping(remote="Z:/shows", local=Path("/mnt/z/shows"))]
    mapped = map_remote_path_to_local("Z:/shows/Futurama/Season 1/file.mkv", mappings)
    assert mapped == Path("/mnt/z/shows/Futurama/Season 1/file.mkv")


def test_map_docker_style_prefix() -> None:
    mappings = [PathMapping(remote="/tv", local=Path("/mnt/z/shows"))]
    mapped = map_remote_path_to_local("/tv/Futurama/Season 1/file.mkv", mappings)
    assert mapped == Path("/mnt/z/shows/Futurama/Season 1/file.mkv")


def test_longest_prefix_match_wins() -> None:
    mappings = [
        PathMapping(remote="Z:/shows", local=Path("/mnt/z/shows")),
        PathMapping(remote="Z:/shows/anime", local=Path("/mnt/z/shows/anime")),
    ]
    mapped = map_remote_path_to_local("Z:/shows/anime/Futurama/file.mkv", mappings)
    assert mapped == Path("/mnt/z/shows/anime/Futurama/file.mkv")


def test_no_mapping_raises_clear_error() -> None:
    mappings = [PathMapping(remote="Z:/shows", local=Path("/mnt/z/shows"))]
    with pytest.raises(PathMappingError, match="No path mapping matched"):
        map_remote_path_to_local("/movies/American Psycho/file.mkv", mappings)


def test_weird_characters_preserved_in_suffix() -> None:
    mappings = [PathMapping(remote="Z:/shows", local=Path("/mnt/z/shows"))]
    mapped = map_remote_path_to_local(
        "Z:/shows/Name [1080p]/S01E01 - Pilot + Bonus's (cut).mkv",
        mappings,
    )
    assert mapped == Path("/mnt/z/shows/Name [1080p]/S01E01 - Pilot + Bonus's (cut).mkv")
