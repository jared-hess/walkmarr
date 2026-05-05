"""Walkmarr Click CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console

from walkmarr.config import load_config, profile_name_for_title, resolve_api_key
from walkmarr.exceptions import ConfigError, ProviderError, WalkmarrError
from walkmarr.models import AppConfig
from walkmarr.process import ensure_required_tools, process_media_item
from walkmarr.providers.radarr import RadarrProvider
from walkmarr.providers.sonarr import SonarrProvider


@dataclass
class RuntimeContext:
    """Runtime context for CLI commands."""

    config_path: Path | None
    verbose: bool
    console: Console
    loaded_path: Path | None = None
    config: AppConfig | None = None


def _get_config(ctx: RuntimeContext) -> AppConfig:
    if ctx.config is None:
        loaded_path, loaded_config = load_config(ctx.config_path)
        ctx.loaded_path = loaded_path
        ctx.config = loaded_config
    return ctx.config


def _as_click_error(exc: Exception) -> click.ClickException:
    return click.ClickException(str(exc))


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Path to YAML config file.",
)
@click.option("--verbose", is_flag=True, help="Enable verbose output.")
@click.pass_context
def main(click_ctx: click.Context, config_path: Path | None, verbose: bool) -> None:
    """Walkmarr CLI."""
    click_ctx.obj = RuntimeContext(config_path=config_path, verbose=verbose, console=Console())


@main.group()
def config() -> None:
    """Config commands."""


@config.command("check")
@click.pass_obj
def config_check(runtime: RuntimeContext) -> None:
    """Validate config and provider API key resolution."""
    try:
        app_config = _get_config(runtime)
        assert runtime.loaded_path is not None
        resolve_api_key(app_config, "sonarr")
        resolve_api_key(app_config, "radarr")
    except (ConfigError, AssertionError) as exc:
        raise _as_click_error(exc) from exc

    runtime.console.print(f"Config OK: {runtime.loaded_path}")


@main.group()
def sonarr() -> None:
    """Sonarr commands."""


@sonarr.command("list")
@click.pass_obj
def sonarr_list(runtime: RuntimeContext) -> None:
    """List Sonarr series titles."""
    try:
        app_config = _get_config(runtime)
        provider = SonarrProvider(
            url=app_config.providers["sonarr"].url,
            api_key=resolve_api_key(app_config, "sonarr"),
        )
        series = provider.list_series()
    except (ConfigError, ProviderError) as exc:
        raise _as_click_error(exc) from exc

    for item in sorted(series, key=lambda s: str(s.get("title", "")).casefold()):
        runtime.console.print(str(item.get("title", "")))


@sonarr.command("convert")
@click.argument("series_title")
@click.option("--dry-run", is_flag=True, help="Print plan without writing output files.")
@click.option(
    "--missing-only",
    is_flag=True,
    default=True,
    show_default=True,
    help="Skip existing outputs (default behavior).",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing output files.")
@click.pass_obj
def sonarr_convert(
    runtime: RuntimeContext,
    series_title: str,
    dry_run: bool,
    missing_only: bool,
    overwrite: bool,
) -> None:
    """Convert a Sonarr series to portable MP4 outputs."""
    del missing_only
    try:
        app_config = _get_config(runtime)
        atomicparsley_bin = ensure_required_tools()

        provider = SonarrProvider(
            url=app_config.providers["sonarr"].url,
            api_key=resolve_api_key(app_config, "sonarr"),
        )

        all_series = provider.list_series()
        selected_series = provider.match_series(series_title, all_series)
        selected_title = str(selected_series.get("title"))
        selected_id = selected_series.get("id")
        if not isinstance(selected_id, int):
            raise ProviderError(f"Selected series '{selected_title}' has invalid Sonarr id")

        profile_name = profile_name_for_title(app_config, "sonarr", selected_title)
        profile = app_config.profiles.get(profile_name)
        if profile is None:
            raise ConfigError(f"Missing profile '{profile_name}' for Sonarr title '{selected_title}'")

        episodes = provider.list_episodes(selected_id)
        episode_files = provider.list_episode_files(selected_id)
        items = provider.build_media_items(
            series_title=selected_title,
            episodes=episodes,
            episode_files=episode_files,
            profile_name=profile_name,
            path_mappings=app_config.path_mappings,
            output_root=app_config.output_roots["tv"],
            allow_unmapped_existing_local=app_config.allow_unmapped_existing_local,
        )
        if not items:
            raise ProviderError(f"No episode files found for Sonarr series '{selected_title}'")

        converted = 0
        skipped = 0
        for item in items:
            result = process_media_item(
                config=app_config,
                media_item=item,
                provider_name="sonarr",
                profile=profile,
                atomicparsley_bin=atomicparsley_bin,
                console=runtime.console,
                dry_run=dry_run,
                overwrite=overwrite,
            )
            if result.status in {"converted", "dry-run"}:
                converted += 1
            else:
                skipped += 1

        runtime.console.print(f"Done. Processed: {converted}, Skipped: {skipped}")
    except (WalkmarrError, ConfigError, ProviderError) as exc:
        raise _as_click_error(exc) from exc


@main.group()
def radarr() -> None:
    """Radarr commands."""


@radarr.command("list")
@click.pass_obj
def radarr_list(runtime: RuntimeContext) -> None:
    """List Radarr movie titles."""
    try:
        app_config = _get_config(runtime)
        provider = RadarrProvider(
            url=app_config.providers["radarr"].url,
            api_key=resolve_api_key(app_config, "radarr"),
        )
        movies = provider.list_movies()
    except (ConfigError, ProviderError) as exc:
        raise _as_click_error(exc) from exc

    for item in sorted(movies, key=lambda m: str(m.get("title", "")).casefold()):
        title = str(item.get("title", ""))
        year = item.get("year")
        suffix = f" ({year})" if isinstance(year, int) else ""
        runtime.console.print(f"{title}{suffix}")


@radarr.command("convert")
@click.argument("movie_title")
@click.option("--dry-run", is_flag=True, help="Print plan without writing output files.")
@click.option(
    "--missing-only",
    is_flag=True,
    default=True,
    show_default=True,
    help="Skip existing outputs (default behavior).",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing output files.")
@click.pass_obj
def radarr_convert(
    runtime: RuntimeContext,
    movie_title: str,
    dry_run: bool,
    missing_only: bool,
    overwrite: bool,
) -> None:
    """Convert a Radarr movie to portable MP4 output."""
    del missing_only
    try:
        app_config = _get_config(runtime)
        atomicparsley_bin = ensure_required_tools()

        provider = RadarrProvider(
            url=app_config.providers["radarr"].url,
            api_key=resolve_api_key(app_config, "radarr"),
        )

        movies = provider.list_movies()
        selected_movie = provider.match_movie(movie_title, movies)
        selected_title = str(selected_movie.get("title"))

        profile_name = profile_name_for_title(app_config, "radarr", selected_title)
        profile = app_config.profiles.get(profile_name)
        if profile is None:
            raise ConfigError(f"Missing profile '{profile_name}' for Radarr title '{selected_title}'")

        media_item = provider.build_media_item(
            movie=selected_movie,
            profile_name=profile_name,
            path_mappings=app_config.path_mappings,
            output_root=app_config.output_roots["movies"],
            allow_unmapped_existing_local=app_config.allow_unmapped_existing_local,
        )

        result = process_media_item(
            config=app_config,
            media_item=media_item,
            provider_name="radarr",
            profile=profile,
            atomicparsley_bin=atomicparsley_bin,
            console=runtime.console,
            dry_run=dry_run,
            overwrite=overwrite,
        )

        if result.status == "skipped":
            runtime.console.print(f"Skipped existing output: {result.output_path}")
        else:
            runtime.console.print(f"Done: {result.output_path}")
    except (WalkmarrError, ConfigError, ProviderError) as exc:
        raise _as_click_error(exc) from exc


if __name__ == "__main__":
    main()
