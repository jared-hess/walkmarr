"""Aspect-ratio scan helpers for provider metadata."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any, Literal


AspectMatchMode = Literal["near", "wider", "taller", "exact"]
AspectSource = Literal["provider", "probe"]
_RESOLUTION_RE = re.compile(r"^\s*(?P<width>\d+)\s*x\s*(?P<height>\d+)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class AspectMetadata:
    """Width and height metadata from a scan source."""

    width: int
    height: int
    source: AspectSource
    display_aspect_ratio: float | None = None


@dataclass(frozen=True)
class AspectScanRecord:
    """A provider media item with usable aspect-ratio metadata."""

    provider: Literal["sonarr", "radarr"]
    title: str
    item: str
    path: str
    metadata: AspectMetadata


def parse_ratio(value: str | float | int) -> float:
    """Parse a ratio string such as ``4:3`` or a decimal ratio."""
    if isinstance(value, int | float):
        ratio = float(value)
    else:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Aspect ratio must not be empty")
        if ":" in normalized:
            left, right = normalized.split(":", 1)
            numerator = float(left.strip())
            denominator = float(right.strip())
            if denominator <= 0:
                raise ValueError("Aspect ratio denominator must be greater than zero")
            ratio = numerator / denominator
        else:
            ratio = float(normalized)

    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("Aspect ratio must be greater than zero")
    return ratio


def aspect_ratio(width: int, height: int) -> float:
    """Return width divided by height for positive dimensions."""
    if width <= 0 or height <= 0:
        raise ValueError("Aspect dimensions must be positive")
    return width / height


def aspect_delta(metadata: AspectMetadata, target_ratio: float) -> float:
    """Return media aspect minus target aspect."""
    media_aspect = metadata.display_aspect_ratio or aspect_ratio(metadata.width, metadata.height)
    return media_aspect - target_ratio


def matches_aspect(
    metadata: AspectMetadata,
    *,
    target_ratio: float,
    tolerance: float,
    mode: str,
) -> bool:
    """Return whether metadata matches the requested aspect mode."""
    if not math.isfinite(target_ratio) or target_ratio <= 0:
        raise ValueError("Aspect ratio must be greater than zero")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Aspect tolerance must be finite and non-negative")
    delta = aspect_delta(metadata, target_ratio)
    if mode == "near":
        return abs(delta) <= tolerance
    if mode == "wider":
        return delta >= tolerance
    if mode == "taller":
        return delta <= -tolerance
    if mode == "exact":
        return delta == 0
    raise ValueError(f"Unsupported aspect match mode: {mode}")


def extract_sonarr_metadata(
    *,
    series: dict[str, Any],
    episodes: list[dict[str, Any]],
    episode_files: list[dict[str, Any]],
) -> list[AspectScanRecord]:
    """Extract Sonarr episode-file aspect metadata records."""
    title = _string_value(series.get("title")) or ""
    episodes_by_file_id: dict[int, list[dict[str, Any]]] = {}
    for episode in episodes:
        if not episode.get("hasFile"):
            continue
        file_id = episode.get("episodeFileId")
        if isinstance(file_id, int):
            episodes_by_file_id.setdefault(file_id, []).append(episode)

    records: list[AspectScanRecord] = []
    valid_file_records = [
        file_record for file_record in episode_files if isinstance(file_record.get("id"), int)
    ]
    for file_record in sorted(valid_file_records, key=lambda item: int(item["id"])):
        file_id = file_record["id"]
        metadata = _metadata_from_media_info(file_record.get("mediaInfo"), source="provider")
        if metadata is None:
            continue
        path = _string_value(file_record.get("path"))
        if path is None:
            continue
        related_episodes = episodes_by_file_id.get(file_id, [])
        if not related_episodes:
            continue
        item = _sonarr_item_label(related_episodes)
        records.append(
            AspectScanRecord(
                provider="sonarr",
                title=title,
                item=item,
                path=path,
                metadata=metadata,
            )
        )
    return records


def probe_aspect_metadata(path: Path, ffprobe_bin: str = "ffprobe") -> AspectMetadata | None:
    """Read display aspect metadata from a local media file with ffprobe."""
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,display_aspect_ratio,sample_aspect_ratio",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        payload = json.loads(completed.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return None
    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams:
        return None
    stream = streams[0]
    if not isinstance(stream, dict):
        return None
    width = _positive_int(stream.get("width"))
    height = _positive_int(stream.get("height"))
    if width is None or height is None:
        return None
    display_ratio = _ratio_from_ffprobe_value(stream.get("display_aspect_ratio"))
    if display_ratio is None:
        sample_ratio = _ratio_from_ffprobe_value(stream.get("sample_aspect_ratio"))
        if sample_ratio is not None:
            display_ratio = aspect_ratio(width, height) * sample_ratio
    return AspectMetadata(
        width=width,
        height=height,
        source="probe",
        display_aspect_ratio=display_ratio,
    )


def extract_radarr_metadata(*, movie: dict[str, Any]) -> list[AspectScanRecord]:
    """Extract Radarr movie-file aspect metadata records."""
    movie_file = movie.get("movieFile")
    if not isinstance(movie_file, dict):
        return []
    metadata = _metadata_from_media_info(movie_file.get("mediaInfo"), source="provider")
    path = _string_value(movie_file.get("path"))
    if metadata is None or path is None:
        return []
    year = movie.get("year")
    return [
        AspectScanRecord(
            provider="radarr",
            title=_string_value(movie.get("title")) or "",
            item=str(year) if isinstance(year, int) else "",
            path=path,
            metadata=metadata,
        )
    ]


def matching_records(
    records: list[AspectScanRecord],
    *,
    target_ratio: float,
    tolerance: float,
    mode: str,
) -> list[AspectScanRecord]:
    """Return records that match a target aspect ratio deterministically sorted."""
    matched = [
        record
        for record in records
        if matches_aspect(
            record.metadata,
            target_ratio=target_ratio,
            tolerance=tolerance,
            mode=mode,
        )
    ]
    return sorted(matched, key=lambda record: (record.provider, record.title.casefold(), record.item, record.path))


def format_tsv(records: list[AspectScanRecord], *, target_ratio: float) -> list[str]:
    """Format aspect scan records as deterministic TSV rows."""
    lines = ["provider\ttitle\titem\tpath\twidth\theight\taspect\tsource\tdelta"]
    for record in records:
        media_aspect = record.metadata.display_aspect_ratio or aspect_ratio(
            record.metadata.width,
            record.metadata.height,
        )
        delta = media_aspect - target_ratio
        lines.append(
            "\t".join(
                [
                    record.provider,
                    _tsv_field(record.title),
                    _tsv_field(record.item),
                    _tsv_field(record.path),
                    str(record.metadata.width),
                    str(record.metadata.height),
                    f"{media_aspect:.4f}",
                    record.metadata.source,
                    f"{delta:.4f}",
                ]
            )
        )
    return lines


def _metadata_from_media_info(value: object, *, source: AspectSource) -> AspectMetadata | None:
    if not isinstance(value, dict):
        return None
    width = _positive_int(value.get("width"))
    height = _positive_int(value.get("height"))
    if width is None or height is None:
        width, height = _dimensions_from_resolution(value.get("resolution"))
    if width is None or height is None:
        return None
    return AspectMetadata(width=width, height=height, source=source)


def _dimensions_from_resolution(value: object) -> tuple[int | None, int | None]:
    if not isinstance(value, str):
        return None, None
    match = _RESOLUTION_RE.match(value)
    if match is None:
        return None, None
    width = _positive_int(match.group("width"))
    height = _positive_int(match.group("height"))
    return width, height


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        if parsed > 0:
            return parsed
    return None


def _ratio_from_ffprobe_value(value: object) -> float | None:
    if not isinstance(value, str) or not value or value == "N/A":
        return None
    try:
        return parse_ratio(value)
    except ValueError:
        return None


def _tsv_field(value: str) -> str:
    return "".join(" " if ch in "\t\r\n" or ord(ch) < 32 else ch for ch in value)


def _string_value(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _sonarr_item_label(episodes: list[dict[str, Any]]) -> str:
    numbered_episodes = [
        (season, episode_number)
        for episode in episodes
        if (season := _positive_int(episode.get("seasonNumber"))) is not None
        and (episode_number := _positive_int(episode.get("episodeNumber"))) is not None
    ]
    if not numbered_episodes:
        return ""
    sorted_episodes = sorted(numbered_episodes)
    first = sorted_episodes[0]
    season = first[0]
    episode_number = first[1]
    numbers = [episode for item_season, episode in sorted_episodes if item_season == season]
    if numbers and max(numbers) > episode_number:
        return f"S{season:02d}E{episode_number:02d}-E{max(numbers):02d}"
    return f"S{season:02d}E{episode_number:02d}"
