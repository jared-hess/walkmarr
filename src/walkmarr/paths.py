"""Path normalization, mapping, and output path helpers."""

from pathlib import Path

from walkmarr.exceptions import PathMappingError
from walkmarr.models import PathMapping


def normalize_provider_path(path: str) -> str:
    """Normalize provider path separators and trailing slashes.

    Args:
        path: Raw path reported by a provider.

    Returns:
        Normalized path with forward slashes.
    """
    normalized = path.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized


def map_remote_path_to_local(
    path: str,
    mappings: list[PathMapping],
    *,
    allow_unmapped_existing_local: bool = False,
) -> Path:
    """Map a provider path to a local path using longest-prefix matching.

    Args:
        path: Provider path to map.
        mappings: Available mapping rules.
        allow_unmapped_existing_local: If true, allows passthrough only when
            the original path already exists locally.

    Returns:
        Mapped local path.

    Raises:
        PathMappingError: No mapping was found.
    """
    source = normalize_provider_path(path)

    best_mapping: PathMapping | None = None
    best_remote: str | None = None

    for mapping in mappings:
        remote = normalize_provider_path(mapping.remote)
        source_cf = source.casefold()
        remote_cf = remote.casefold()
        if source_cf == remote_cf or source_cf.startswith(remote_cf + "/"):
            if best_remote is None or len(remote) > len(best_remote):
                best_mapping = mapping
                best_remote = remote

    if best_mapping is None or best_remote is None:
        original = Path(path)
        if allow_unmapped_existing_local and original.exists():
            return original
        configured = ", ".join(sorted(normalize_provider_path(m.remote) for m in mappings))
        raise PathMappingError(
            f"No path mapping matched '{path}'. Configured remote prefixes: {configured}"
        )

    suffix = source[len(best_remote) :]
    if suffix.startswith("/"):
        suffix = suffix[1:]
    if suffix:
        return best_mapping.local / Path(suffix)
    return best_mapping.local


def sanitize_path_component(component: str) -> str:
    """Sanitize a filesystem component while preserving normal punctuation."""
    cleaned = component.replace("/", "-").replace("\\", "-")
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 32 and ch != "\x00")
    cleaned = cleaned.rstrip(" .")
    return cleaned or "_"


def build_tv_output_path(
    output_root: Path,
    series_title: str,
    season_number: int,
    episode_number: int,
    episode_title: str,
    episode_end_number: int | None = None,
) -> Path:
    """Build output path for a TV episode."""
    safe_series = sanitize_path_component(series_title)
    safe_episode_title = sanitize_path_component(episode_title)
    season_dir = output_root / safe_series / f"Season {season_number}"
    episode_token = f"S{season_number:02d}E{episode_number:02d}"
    if episode_end_number is not None and episode_end_number > episode_number:
        episode_token = f"S{season_number:02d}E{episode_number:02d}-E{episode_end_number:02d}"
    filename = (
        f"{safe_series} - {episode_token} - "
        f"{safe_episode_title}.mp4"
    )
    return season_dir / filename


def build_movie_output_path(output_root: Path, movie_title: str, year: int | None) -> Path:
    """Build output path for a movie."""
    safe_title = sanitize_path_component(movie_title)
    year_suffix = f" ({year})" if year is not None else ""
    folder = f"{safe_title}{year_suffix}"
    filename = f"{safe_title}{year_suffix}.mp4"
    return output_root / folder / filename
