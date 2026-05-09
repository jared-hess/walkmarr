"""Walkmarr Click CLI."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from pathlib import Path
from typing import Literal, cast

import click
from rich.console import Console

from walkmarr.config import (
    bootstrap_config,
    default_bootstrap_config_path,
    default_bootstrap_payload,
    load_config,
    profile_name_for_radarr_movie,
    profile_name_for_sonarr_series,
    resolve_api_key,
)
from walkmarr.exceptions import ConfigError, ProviderError, WalkmarrError
from walkmarr.logging_config import configure_file_logging
from walkmarr.models import AppConfig
from walkmarr.process import ensure_required_tools, process_media_items
from walkmarr.providers.radarr import RadarrProvider
from walkmarr.providers.sonarr import SonarrProvider


STAGING_MODE_CHOICES = ("auto", "always", "never")


@dataclass
class RuntimeContext:
    """Runtime context for CLI commands."""

    config_path: Path | None
    verbose: bool
    console: Console
    loaded_path: Path | None = None
    config: AppConfig | None = None
    log_path: Path | None = None


def _get_config(ctx: RuntimeContext) -> AppConfig:
    if ctx.config is None:
        loaded_path, loaded_config = load_config(ctx.config_path)
        ctx.loaded_path = loaded_path
        ctx.config = loaded_config
    return ctx.config


def _as_click_error(exc: Exception) -> click.ClickException:
    return click.ClickException(str(exc))


def _apply_staging_mode_override(config: AppConfig, staging_mode: str | None) -> AppConfig:
    """Return config with optional per-command staging mode override."""
    if staging_mode is None:
        return config
    normalized = cast(Literal["auto", "always", "never"], staging_mode.casefold())
    return replace(config, staging_mode=normalized)


def _primary_genre(payload: dict[str, object]) -> str | None:
    genres = payload.get("genres")
    if not isinstance(genres, list):
        return None
    for value in genres:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


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
    log_path = configure_file_logging(verbose=verbose)
    logging.getLogger("walkmarr").info(
        "CLI start config_path=%s verbose=%s",
        config_path,
        verbose,
    )
    click_ctx.obj = RuntimeContext(
        config_path=config_path,
        verbose=verbose,
        console=Console(),
        log_path=log_path,
    )


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


@config.command("init")
@click.option(
    "--path",
    "target_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Output config path (default: ~/.config/walkmarr/config.yml).",
)
@click.option("--force", is_flag=True, help="Overwrite existing config files.")
@click.option(
    "--prompt",
    "interactive_prompt",
    is_flag=True,
    help="Prompt for provider URLs, API key mode, mappings, and output roots.",
)
@click.pass_obj
def config_init(
    runtime: RuntimeContext,
    target_path: Path | None,
    force: bool,
    interactive_prompt: bool,
) -> None:
    """Bootstrap a new Walkmarr config file."""
    del runtime
    resolved_target_path = target_path or default_bootstrap_config_path()

    if resolved_target_path.exists() and not force:
        raise _as_click_error(
            ConfigError(f"Config already exists: {resolved_target_path}. Use --force to overwrite.")
        )

    payload = default_bootstrap_payload()
    if interactive_prompt:
        payload = _prompt_bootstrap_payload(payload)

    try:
        written = bootstrap_config(
            resolved_target_path,
            payload=payload,
            force=force,
        )
    except ConfigError as exc:
        raise _as_click_error(exc) from exc

    click.echo("Wrote config bootstrap files:")
    for path in written:
        click.echo(f"- {path}")


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


@main.command("tui")
@click.pass_obj
def tui(runtime: RuntimeContext) -> None:
    """Launch interactive queue-oriented TUI."""
    try:
        from walkmarr.tui import run_tui

        app_config = _get_config(runtime)
        run_tui(app_config)
    except (WalkmarrError, ConfigError, ProviderError) as exc:
        raise _as_click_error(exc) from exc


@sonarr.command("convert")
@click.argument("series_title")
@click.option("--dry-run", is_flag=True, help="Print plan without writing output files.")
@click.option(
    "--staging-mode",
    type=click.Choice(STAGING_MODE_CHOICES, case_sensitive=False),
    default=None,
    help="Override source staging behavior for this run: auto, always, or never.",
)
@click.option(
    "--missing-only",
    is_flag=True,
    default=True,
    show_default=True,
    help="Skip existing outputs (default behavior).",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing output files.")
@click.option(
    "--keep-temp",
    is_flag=True,
    help="Keep failed conversion temp files for debugging.",
)
@click.pass_obj
def sonarr_convert(
    runtime: RuntimeContext,
    series_title: str,
    dry_run: bool,
    staging_mode: str | None,
    missing_only: bool,
    overwrite: bool,
    keep_temp: bool,
) -> None:
    """Convert a Sonarr series to portable MP4 outputs."""
    del missing_only
    try:
        app_config = _get_config(runtime)
        effective_config = _apply_staging_mode_override(app_config, staging_mode)
        if keep_temp:
            effective_config = replace(effective_config, keep_failed_temps=True)
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

        profile_name = profile_name_for_sonarr_series(app_config, selected_series)
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
            output_root=app_config.output_roots["shows"],
            series_genre=_primary_genre(selected_series),
            allow_unmapped_existing_local=app_config.allow_unmapped_existing_local,
        )
        if not items:
            raise ProviderError(f"No episode files found for Sonarr series '{selected_title}'")

        result = process_media_items(
            config=effective_config,
            media_items=items,
            provider_name="sonarr",
            profile=profile,
            atomicparsley_bin=atomicparsley_bin,
            console=runtime.console,
            dry_run=dry_run,
            overwrite=overwrite,
        )

        runtime.console.print(f"Done. Processed: {result.converted}, Skipped: {result.skipped}")
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
    "--staging-mode",
    type=click.Choice(STAGING_MODE_CHOICES, case_sensitive=False),
    default=None,
    help="Override source staging behavior for this run: auto, always, or never.",
)
@click.option(
    "--missing-only",
    is_flag=True,
    default=True,
    show_default=True,
    help="Skip existing outputs (default behavior).",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing output files.")
@click.option(
    "--keep-temp",
    is_flag=True,
    help="Keep failed conversion temp files for debugging.",
)
@click.pass_obj
def radarr_convert(
    runtime: RuntimeContext,
    movie_title: str,
    dry_run: bool,
    staging_mode: str | None,
    missing_only: bool,
    overwrite: bool,
    keep_temp: bool,
) -> None:
    """Convert a Radarr movie to portable MP4 output."""
    del missing_only
    try:
        app_config = _get_config(runtime)
        effective_config = _apply_staging_mode_override(app_config, staging_mode)
        if keep_temp:
            effective_config = replace(effective_config, keep_failed_temps=True)
        atomicparsley_bin = ensure_required_tools()

        provider = RadarrProvider(
            url=app_config.providers["radarr"].url,
            api_key=resolve_api_key(app_config, "radarr"),
        )

        movies = provider.list_movies()
        selected_movie = provider.match_movie(movie_title, movies)
        selected_title = str(selected_movie.get("title"))

        profile_name = profile_name_for_radarr_movie(app_config, selected_movie)
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

        result = process_media_items(
            config=effective_config,
            media_items=[media_item],
            provider_name="radarr",
            profile=profile,
            atomicparsley_bin=atomicparsley_bin,
            console=runtime.console,
            dry_run=dry_run,
            overwrite=overwrite,
        )
        runtime.console.print(f"Done. Processed: {result.converted}, Skipped: {result.skipped}")
    except (WalkmarrError, ConfigError, ProviderError) as exc:
        raise _as_click_error(exc) from exc


def _prompt_bootstrap_payload(base_payload: dict[str, object]) -> dict[str, object]:
    """Collect interactive config bootstrap values from user prompts."""
    payload = dict(base_payload)

    click.echo("Path mapping setup:")
    click.echo("- 'Provider path' is what Sonarr/Radarr report in their API.")
    click.echo("- 'Local path' is where Walkmarr can read that same media on this machine.")
    click.echo("- Use one mapping per media type (shows + movies).")

    providers: dict[str, dict[str, str]] = {}
    provider_defaults = {
        "sonarr": {
            "url": "http://localhost:8989",
            "api_key_env": "SONARR_API_KEY",
            "name": "Sonarr",
        },
        "radarr": {
            "url": "http://localhost:7878",
            "api_key_env": "RADARR_API_KEY",
            "name": "Radarr",
        },
    }

    for provider_key in ("sonarr", "radarr"):
        defaults = provider_defaults[provider_key]
        provider_name = defaults["name"]
        click.echo("")
        click.echo(f"{provider_name} settings:")
        provider_url = click.prompt(f"{provider_name} URL", default=defaults["url"], type=str)
        key_mode = click.prompt(
            f"{provider_name} API key storage mode",
            type=click.Choice(["env", "inline"], case_sensitive=False),
            default="env",
            show_choices=True,
        ).lower()

        provider_config: dict[str, str] = {"url": provider_url}
        if key_mode == "inline":
            provider_config["api_key"] = click.prompt(
                f"{provider_name} API key",
                hide_input=True,
                type=str,
            )
        else:
            provider_config["api_key_env"] = click.prompt(
                f"{provider_name} API key env var",
                default=defaults["api_key_env"],
                type=str,
            )

        providers[provider_key] = provider_config

    shows_remote = click.prompt("Provider shows root path", default="/shows", type=str)
    shows_local = click.prompt("Local shows root path", default="/mnt/media/shows", type=str)
    movies_remote = click.prompt("Provider movies root path", default="/movies", type=str)
    movies_local = click.prompt("Local movies root path", default="/mnt/media/movies", type=str)
    output_shows = click.prompt("Output shows root", default="/mnt/walkmarr/shows", type=str)
    output_movies = click.prompt("Output movies root", default="/mnt/walkmarr/movies", type=str)

    payload["providers"] = providers
    payload["path_mappings"] = [
        {"remote": shows_remote, "local": shows_local},
        {"remote": movies_remote, "local": movies_local},
    ]
    payload["output_roots"] = {"shows": output_shows, "movies": output_movies}
    return payload


if __name__ == "__main__":
    main()
