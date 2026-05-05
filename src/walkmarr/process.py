"""Core conversion pipeline orchestration."""

from __future__ import annotations

import hashlib
import errno
import queue
import shutil
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn

from walkmarr.config import sonarr_specials_show_name
from walkmarr.convert.video import (
    ConversionPlan,
    build_ffmpeg_command,
    probe_media,
    require_binary,
    run_ffmpeg,
)
from walkmarr.exceptions import ConversionError, TaggingError, WalkmarrError
from walkmarr.models import AppConfig, MediaItem, VideoProfile
from walkmarr.tag.mp4 import (
    build_movie_tag_command,
    build_tv_tag_command,
    detect_atomicparsley_binary,
    run_atomicparsley,
)


@dataclass(frozen=True)
class ProcessResult:
    """Result of processing one media item."""

    status: str
    output_path: Path


@dataclass(frozen=True)
class BatchProcessResult:
    """Aggregated batch processing result."""

    converted: int
    skipped: int


@dataclass(frozen=True)
class PreparedMediaItem:
    """Prepared item containing source path used for conversion."""

    media_item: MediaItem
    processing_source_path: Path
    probe_source_path: Path
    staging_applied: bool
    staged_file_path: Path | None
    skip_only: bool = False


def ensure_required_tools() -> str:
    """Ensure required binaries are present and return AtomicParsley name."""
    require_binary("ffmpeg")
    require_binary("ffprobe")
    return detect_atomicparsley_binary()


def process_media_item(
    *,
    config: AppConfig,
    media_item: MediaItem,
    provider_name: str,
    profile: VideoProfile,
    atomicparsley_bin: str,
    console: Console,
    dry_run: bool,
    overwrite: bool,
) -> ProcessResult:
    """Process one media item through inspect, convert, tag, and finalize."""
    if not media_item.source_path.exists():
        raise WalkmarrError(f"Source file does not exist after mapping: {media_item.source_path}")

    prepared = _prepare_media_item(
        config=config,
        media_item=media_item,
        console=console,
        dry_run=dry_run,
        overwrite=overwrite,
    )
    try:
        return _process_prepared_media_item(
            config=config,
            prepared=prepared,
            provider_name=provider_name,
            profile=profile,
            atomicparsley_bin=atomicparsley_bin,
            console=console,
            dry_run=dry_run,
            overwrite=overwrite,
        )
    finally:
        _cleanup_staged_file(prepared)


def process_media_items(
    *,
    config: AppConfig,
    media_items: list[MediaItem],
    provider_name: str,
    profile: VideoProfile,
    atomicparsley_bin: str,
    console: Console,
    dry_run: bool,
    overwrite: bool,
) -> BatchProcessResult:
    """Process a list of media items with stage/convert overlap."""
    if dry_run or len(media_items) <= 1:
        converted = 0
        skipped = 0
        for item in media_items:
            result = process_media_item(
                config=config,
                media_item=item,
                provider_name=provider_name,
                profile=profile,
                atomicparsley_bin=atomicparsley_bin,
                console=console,
                dry_run=dry_run,
                overwrite=overwrite,
            )
            if result.status in {"converted", "dry-run"}:
                converted += 1
            else:
                skipped += 1
        return BatchProcessResult(converted=converted, skipped=skipped)

    work_queue: queue.Queue[PreparedMediaItem | object] = queue.Queue(maxsize=1)
    sentinel = object()
    stop_event = threading.Event()
    result_lock = threading.Lock()
    counts = {"converted": 0, "skipped": 0}
    first_error: list[Exception] = []

    def stage_worker() -> None:
        try:
            for item in media_items:
                if stop_event.is_set():
                    break
                prepared = _prepare_media_item(
                    config=config,
                    media_item=item,
                    console=console,
                    dry_run=False,
                    overwrite=overwrite,
                    show_staging_progress=False,
                )
                if not _queue_put_with_stop(work_queue, prepared, stop_event):
                    _cleanup_staged_file(prepared)
                    break
        except Exception as exc:  # pragma: no cover - covered via integration flow
            if not first_error:
                first_error.append(exc)
            stop_event.set()
        finally:
            work_queue.put(sentinel)

    def convert_worker() -> None:
        while True:
            queued = work_queue.get()
            if queued is sentinel:
                break
            if not isinstance(queued, PreparedMediaItem):
                continue

            prepared = queued
            try:
                if prepared.skip_only:
                    with result_lock:
                        counts["skipped"] += 1
                    continue

                if stop_event.is_set():
                    continue

                result = _process_prepared_media_item(
                    config=config,
                    prepared=prepared,
                    provider_name=provider_name,
                    profile=profile,
                    atomicparsley_bin=atomicparsley_bin,
                    console=console,
                    dry_run=False,
                    overwrite=overwrite,
                )
                with result_lock:
                    if result.status == "converted":
                        counts["converted"] += 1
                    else:
                        counts["skipped"] += 1
            except Exception as exc:  # pragma: no cover - covered via integration flow
                if not first_error:
                    first_error.append(exc)
                stop_event.set()
            finally:
                _cleanup_staged_file(prepared)

    stage_thread = threading.Thread(target=stage_worker, name="walkmarr-stage-worker", daemon=True)
    convert_thread = threading.Thread(
        target=convert_worker,
        name="walkmarr-convert-worker",
        daemon=True,
    )

    stage_thread.start()
    convert_thread.start()
    stage_thread.join()
    convert_thread.join()

    if first_error:
        exc = first_error[0]
        if isinstance(exc, WalkmarrError):
            raise exc
        raise WalkmarrError(str(exc)) from exc

    return BatchProcessResult(converted=counts["converted"], skipped=counts["skipped"])


def _prepare_media_item(
    *,
    config: AppConfig,
    media_item: MediaItem,
    console: Console,
    dry_run: bool,
    overwrite: bool,
    show_staging_progress: bool = True,
) -> PreparedMediaItem:
    if not media_item.source_path.exists():
        raise WalkmarrError(f"Source file does not exist after mapping: {media_item.source_path}")

    if media_item.output_path.exists() and not overwrite:
        return PreparedMediaItem(
            media_item=media_item,
            processing_source_path=media_item.source_path,
            probe_source_path=media_item.source_path,
            staging_applied=False,
            staged_file_path=None,
            skip_only=True,
        )

    use_staging = should_stage_source_path(media_item.source_path, config.staging_mode)
    if not use_staging:
        return PreparedMediaItem(
            media_item=media_item,
            processing_source_path=media_item.source_path,
            probe_source_path=media_item.source_path,
            staging_applied=False,
            staged_file_path=None,
        )

    planned_stage = planned_staging_path(media_item.source_path, config.staging_directory)
    if dry_run:
        return PreparedMediaItem(
            media_item=media_item,
            processing_source_path=planned_stage,
            probe_source_path=media_item.source_path,
            staging_applied=True,
            staged_file_path=None,
        )

    try:
        staged_source = stage_source_file(
            media_item.source_path,
            config.staging_directory,
            console,
            show_progress=show_staging_progress,
        )
    except OSError as exc:
        raise WalkmarrError(f"Failed to stage source file '{media_item.source_path}': {exc}") from exc

    return PreparedMediaItem(
        media_item=media_item,
        processing_source_path=staged_source,
        probe_source_path=staged_source,
        staging_applied=True,
        staged_file_path=staged_source,
    )


def _process_prepared_media_item(
    *,
    config: AppConfig,
    prepared: PreparedMediaItem,
    provider_name: str,
    profile: VideoProfile,
    atomicparsley_bin: str,
    console: Console,
    dry_run: bool,
    overwrite: bool,
) -> ProcessResult:
    media_item = prepared.media_item
    final_output = media_item.output_path

    if prepared.skip_only:
        return ProcessResult(status="skipped", output_path=final_output)

    if final_output.exists() and not overwrite:
        return ProcessResult(status="skipped", output_path=final_output)

    probe = probe_media(prepared.probe_source_path)
    tmp_output = final_output.with_name(f"{final_output.stem}.tmp.mp4")
    ffmpeg_plan = build_ffmpeg_command(
        source_path=prepared.processing_source_path,
        tmp_output_path=tmp_output,
        profile=profile,
        probe=probe,
    )
    tag_command, metadata = _build_tag_command(
        config=config,
        media_item=media_item,
        atomicparsley_bin=atomicparsley_bin,
        media_path=tmp_output,
    )

    _print_conversion_plan(
        console=console,
        media_item=media_item,
        provider_name=provider_name,
        metadata=metadata,
        ffmpeg_plan=ffmpeg_plan,
        tag_command=tag_command,
        staging_applied=prepared.staging_applied,
        staging_path=prepared.processing_source_path if prepared.staging_applied else None,
    )

    if dry_run:
        return ProcessResult(status="dry-run", output_path=final_output)

    final_output.parent.mkdir(parents=True, exist_ok=True)
    if tmp_output.exists():
        tmp_output.unlink()

    try:
        run_ffmpeg(ffmpeg_plan.command)
        run_atomicparsley(tag_command)
        tmp_output.replace(final_output)
    except (ConversionError, TaggingError, OSError) as exc:
        if tmp_output.exists():
            tmp_output.unlink()
        raise WalkmarrError(f"Failed processing '{media_item.source_path}': {exc}") from exc

    return ProcessResult(status="converted", output_path=final_output)


def _cleanup_staged_file(prepared: PreparedMediaItem) -> None:
    path = prepared.staged_file_path
    if path is not None and path.exists():
        path.unlink()


def _queue_put_with_stop(
    work_queue: queue.Queue[PreparedMediaItem | object],
    item: PreparedMediaItem,
    stop_event: threading.Event,
) -> bool:
    while True:
        if stop_event.is_set():
            return False
        try:
            work_queue.put(item, timeout=0.2)
            return True
        except queue.Full:
            continue


def _build_tag_command(
    *,
    config: AppConfig,
    media_item: MediaItem,
    atomicparsley_bin: str,
    media_path: Path,
) -> tuple[list[str], dict[str, str | int | None]]:
    if media_item.kind == "episode":
        if media_item.series_title is None:
            raise WalkmarrError("Episode media item is missing series_title")
        if media_item.season_number is None or media_item.episode_number is None:
            raise WalkmarrError("Episode media item is missing season/episode number")

        tag_show_title = media_item.series_title
        tag_season = media_item.season_number
        tag_episode = media_item.episode_number

        if media_item.season_number == 0:
            specials_show_name = sonarr_specials_show_name(config, media_item.series_title)
            if specials_show_name:
                tag_show_title = specials_show_name
                tag_season = 1
                tag_episode = media_item.episode_number

        command = build_tv_tag_command(
            atomicparsley_bin,
            media_path,
            episode_title=media_item.title,
            show_title=tag_show_title,
            season_number=tag_season,
            episode_number=tag_episode,
            tv_episode_id=media_item.episode_id,
        )
        metadata: dict[str, str | int | None] = {
            "kind": "TV Show",
            "title": media_item.title,
            "show": tag_show_title,
            "season": tag_season,
            "episode": tag_episode,
            "episode_id": media_item.episode_id or f"S{tag_season:02d}E{tag_episode:02d}",
            "artist": tag_show_title,
            "album": tag_show_title,
        }
        return command, metadata

    if media_item.kind == "movie":
        movie_title = media_item.movie_title or media_item.title
        command = build_movie_tag_command(
            atomicparsley_bin,
            media_path,
            movie_title=movie_title,
            year=media_item.year,
        )
        metadata = {
            "kind": "Movie",
            "title": movie_title,
            "year": media_item.year,
            "artist": movie_title,
            "album": movie_title,
        }
        return command, metadata

    raise WalkmarrError(f"Unsupported media kind: {media_item.kind}")


def _print_conversion_plan(
    *,
    console: Console,
    media_item: MediaItem,
    provider_name: str,
    metadata: dict[str, str | int | None],
    ffmpeg_plan: ConversionPlan,
    tag_command: list[str],
    staging_applied: bool,
    staging_path: Path | None,
) -> None:
    plan = ffmpeg_plan
    source_label = media_item.remote_source_path or str(media_item.source_path)

    console.print("Converting:")
    console.print(f"  Provider: {provider_name.capitalize()}")
    console.print(f"  Source: {source_label}")
    console.print(f"  Mapped local path: {media_item.source_path}")
    if staging_applied and staging_path is not None:
        console.print(f"  Staged source: {staging_path}")
    console.print(f"  Output: {media_item.output_path}")

    if media_item.kind == "episode":
        console.print(f"  Show: {metadata['show']}")
        console.print(f"  Season: {metadata['season']}")
        console.print(f"  Episode: {metadata['episode']}")
        console.print(f"  Title: {media_item.title}")
    else:
        console.print(f"  Movie: {metadata['title']}")
        console.print(f"  Year: {metadata['year']}")

    console.print(f"  Profile: {media_item.profile_name}")
    console.print(f"  CRF: {plan.command[plan.command.index('-crf') + 1]}")
    source_vbit = plan.source_video_bitrate_kbps
    vbit_display = f"{source_vbit} kbps" if source_vbit is not None else "unknown"
    console.print(f"  Source vbit: {vbit_display}")
    console.print(f"  Maxrate: {plan.maxrate_kbps}k")
    selected_language = plan.selected_audio_language or "unknown"
    console.print(f"  Audio stream: {plan.selected_audio_index} ({selected_language})")
    console.print(f"  Audio: {plan.audio_channels}ch @ {plan.audio_bitrate_kbps}k")
    console.print(f"  Filter: {plan.filter_expr}")
    console.print(f"  Metadata: {metadata}")
    console.print(f"  ffmpeg command: {shlex.join(plan.command)}")
    console.print(f"  AtomicParsley command: {shlex.join(tag_command)}")


def should_stage_source_path(source_path: Path, staging_mode: str) -> bool:
    """Decide whether source should be staged to local disk first."""
    mode = staging_mode.casefold()
    if mode == "always":
        return True
    if mode == "never":
        return False
    return is_network_mount_path(source_path)


def planned_staging_path(source_path: Path, staging_directory: Path) -> Path:
    """Build deterministic staging path for a source file."""
    suffix = source_path.suffix or ".bin"
    digest = hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()[:12]
    return staging_directory / f"{source_path.stem}.{digest}{suffix}"


def stage_source_file(
    source_path: Path,
    staging_directory: Path,
    console: Console,
    *,
    show_progress: bool = True,
) -> Path:
    """Copy source file to local staging directory and return staged path."""
    staged_path = planned_staging_path(source_path, staging_directory)
    staged_path.parent.mkdir(parents=True, exist_ok=True)

    source_size = source_path.stat().st_size
    if show_progress:
        console.print(f"Staging source: {source_path}")
        console.print(f"  -> {staged_path}")
    else:
        console.print(f"Staging source: {source_path}")
        console.print(f"  -> {staged_path}")

    max_attempts = 3
    staged_ok = False
    last_error: OSError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            _copy_file_chunked(
                source_path=source_path,
                staged_path=staged_path,
                source_size=source_size,
                console=console,
                show_progress=show_progress,
            )
            shutil.copystat(source_path, staged_path)
            staged_ok = True
            break
        except OSError as exc:
            last_error = exc
            if staged_path.exists():
                staged_path.unlink()
            is_retryable = exc.errno == errno.EIO
            if is_retryable and attempt < max_attempts:
                console.print(
                    f"Staging hit I/O error (attempt {attempt}/{max_attempts}); retrying..."
                )
                time.sleep(float(attempt))
                continue

    if not staged_ok:
        if last_error is None:
            raise OSError(errno.EIO, f"Failed staging source file: {source_path}")

        if last_error.errno == errno.EIO:
            console.print("Staging fallback: trying system cp...")
            try:
                _copy_file_with_cp(source_path, staged_path)
                shutil.copystat(source_path, staged_path)
                staged_ok = True
            except OSError as exc:
                if staged_path.exists():
                    staged_path.unlink()
                raise exc from last_error
        else:
            raise last_error

    if show_progress:
        console.print("Staging complete.")
    else:
        console.print("Staging complete.")
    return staged_path


def _copy_file_chunked(
    *,
    source_path: Path,
    staged_path: Path,
    source_size: int,
    console: Console,
    show_progress: bool,
) -> None:
    chunk_size = 8 * 1024 * 1024
    if not show_progress:
        with source_path.open("rb") as src, staged_path.open("wb") as dst:
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                dst.write(chunk)
        return

    with (
        source_path.open("rb") as src,
        staged_path.open("wb") as dst,
        Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as progress,
    ):
        task = progress.add_task("Staging", total=source_size if source_size > 0 else None)
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            dst.write(chunk)
            progress.update(task, advance=len(chunk))


def _copy_file_with_cp(source_path: Path, staged_path: Path) -> None:
    cp_binary = shutil.which("cp")
    if cp_binary is None:
        raise OSError(errno.ENOENT, "'cp' binary not found for staging fallback")

    try:
        subprocess.run([cp_binary, str(source_path), str(staged_path)], check=True)
    except subprocess.CalledProcessError as exc:
        raise OSError(errno.EIO, f"cp fallback failed with exit code {exc.returncode}") from exc


def is_network_mount_path(path: Path) -> bool:
    """Return true when path appears to be on a network-like mount."""
    mounts = _read_mount_entries()
    entry = _best_mount_entry_for_path(path.resolve(strict=False), mounts)
    if entry is None:
        return False

    _mount_path, fs_type, options = entry
    network_fs_types = {
        "9p",
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "sshfs",
        "fuse.sshfs",
        "davfs",
        "glusterfs",
        "ceph",
        "afpfs",
    }
    if fs_type in network_fs_types:
        return True

    if fs_type.startswith("fuse"):
        return True

    if "aname=drvfs;path=UNC" in options:
        return True

    return False


def _read_mount_entries() -> list[tuple[Path, str, str]]:
    entries: list[tuple[Path, str, str]] = []
    mounts_path = Path("/proc/mounts")
    if not mounts_path.exists():
        return entries

    try:
        content = mounts_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return entries

    for raw_line in content.splitlines():
        parts = raw_line.split(" ")
        if len(parts) < 4:
            continue
        mount_point = Path(parts[1].replace("\\040", " "))
        fs_type = parts[2]
        options = parts[3]
        entries.append((mount_point, fs_type, options))
    return entries


def _best_mount_entry_for_path(
    path: Path,
    entries: list[tuple[Path, str, str]],
) -> tuple[Path, str, str] | None:
    best: tuple[Path, str, str] | None = None
    best_len = -1
    for entry in entries:
        mount_path = entry[0]
        try:
            path.relative_to(mount_path)
        except ValueError:
            continue
        mount_len = len(mount_path.parts)
        if mount_len > best_len:
            best = entry
            best_len = mount_len
    return best
