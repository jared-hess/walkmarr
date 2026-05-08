"""AtomicParsley command planning and execution for MP4 metadata."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from walkmarr.exceptions import TaggingError


def detect_atomicparsley_binary() -> str:
    """Detect AtomicParsley executable name from PATH."""
    for name in ("AtomicParsley", "atomicparsley"):
        if shutil.which(name) is not None:
            return name
    raise TaggingError("AtomicParsley is not installed (tried 'AtomicParsley' and 'atomicparsley')")


def build_tv_tag_command(
    atomicparsley_bin: str,
    media_path: Path,
    *,
    episode_title: str,
    show_title: str,
    season_number: int,
    episode_number: int,
    year: int | str | None = None,
    tv_episode_id: str | None = None,
    genre: str | None = None,
) -> list[str]:
    """Build AtomicParsley command for TV metadata tags."""
    episode_id = tv_episode_id or f"S{season_number:02d}E{episode_number:02d}"
    command = [
        atomicparsley_bin,
        str(media_path),
        "--stik",
        "TV Show",
        "--title",
        episode_title,
        "--TVShowName",
        show_title,
        "--TVSeasonNum",
        str(season_number),
        "--TVEpisodeNum",
        str(episode_number),
        "--TVEpisode",
        episode_id,
        "--artist",
        show_title,
        "--album",
        show_title,
    ]
    if year is not None:
        command.extend(["--year", str(year)])
    if genre is not None and genre.strip():
        command.extend(["--genre", genre.strip()])
    command.append("--overWrite")
    return command


def build_movie_tag_command(
    atomicparsley_bin: str,
    media_path: Path,
    *,
    movie_title: str,
    year: int | None,
    genre: str | None = None,
) -> list[str]:
    """Build AtomicParsley command for movie metadata tags."""
    command: list[str] = [
        atomicparsley_bin,
        str(media_path),
        "--stik",
        "Movie",
        "--title",
        movie_title,
    ]
    if year is not None:
        command.extend(["--year", str(year)])
    if genre is not None and genre.strip():
        command.extend(["--genre", genre.strip()])
    command.extend([
        "--artist",
        movie_title,
        "--album",
        movie_title,
        "--overWrite",
    ])
    return command


def run_atomicparsley(command: list[str]) -> None:
    """Run AtomicParsley command and raise TaggingError on failure."""
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise TaggingError(f"AtomicParsley binary not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise TaggingError(f"AtomicParsley failed with exit code {exc.returncode}") from exc
