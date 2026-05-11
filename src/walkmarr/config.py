"""Config discovery, parsing, and validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, cast

from dotenv import load_dotenv
import yaml

from walkmarr.exceptions import ConfigError
from walkmarr.models import (
    AppConfig,
    ArtworkConfig,
    ArtworkProviderConfig,
    ArtworkFallbackProviderConfig,
    GenreProfileRule,
    PathMapping,
    ProviderConfig,
    VideoProfile,
)


def config_search_paths() -> list[Path]:
    """Return default config path search order."""
    return [
        Path("./walkmarr.yml"),
        Path("./config.yml"),
        Path("~/.config/walkmarr/config.yml").expanduser(),
    ]


def default_bootstrap_config_path() -> Path:
    """Return the default bootstrap config path."""
    return Path("~/.config/walkmarr/config.yml").expanduser()


def default_bootstrap_payload() -> dict[str, Any]:
    """Return a full default config payload for bootstrapping."""
    return {
        "providers": {
            "sonarr": {"url": "http://localhost:8989", "api_key_env": "SONARR_API_KEY"},
            "radarr": {"url": "http://localhost:7878", "api_key_env": "RADARR_API_KEY"},
        },
        "path_mappings": [
            {"remote": "/shows", "local": "/mnt/media/shows"},
            {"remote": "/movies", "local": "/mnt/media/movies"},
        ],
        "output_roots": {"shows": "/mnt/walkmarr/shows", "movies": "/mnt/walkmarr/movies"},
        "staging": {"mode": "auto", "directory": "/tmp/walkmarr-staging"},
        "debug": {"keep_failed_temps": False},
        "queue": {
            "workers": 1,
            "continue_on_error": True,
            "start_paused": False,
            "default_mode": "missing_only",
            "remember_completed_until_exit": True,
        },
        "artwork": {
            "enabled": True,
            "providers": {
                "itunes_tv_season": {
                    "enabled": True,
                    "apply_to": ["tv"],
                    "country": "US",
                    "image_size": 320,
                    "timeout_seconds": 10,
                    "minimum_confidence": "parsed",
                    "sonarr_fallback": {"enabled": True},
                    "radarr_fallback": {"enabled": True},
                }
            },
        },
        "default_profiles": {"sonarr": "live_action", "radarr": "movie"},
        "genre_profile_map": {
            "sonarr": [
                {"genres": ["animation", "anime"], "profile": "animation"},
            ],
            "radarr": [
                {"genres": ["animation", "anime"], "profile": "animation"},
            ],
        },
        "profiles": {
            "animation": {
                "crf": 30,
                "maxrate_floor_kbps": 250,
                "maxrate_cap_kbps": 768,
                "bufsize_kbps": 1500,
                "bitrate_multiplier": 1.5,
                "audio_bitrate_mono_kbps": 160,
                "audio_bitrate_stereo_kbps": 160,
                "max_width": 320,
                "h264_profile": "baseline",
                "h264_level": "1.3",
                "preferred_audio_languages": ["eng"],
            },
            "live_action": {
                "crf": 30,
                "maxrate_floor_kbps": 250,
                "maxrate_cap_kbps": 768,
                "bufsize_kbps": 1500,
                "bitrate_multiplier": 1.5,
                "audio_bitrate_mono_kbps": 160,
                "audio_bitrate_stereo_kbps": 160,
                "max_width": 320,
                "h264_profile": "baseline",
                "h264_level": "1.3",
                "preferred_audio_languages": ["eng"],
            },
            "movie": {
                "crf": 30,
                "maxrate_floor_kbps": 250,
                "maxrate_cap_kbps": 768,
                "bufsize_kbps": 1500,
                "bitrate_multiplier": 1.5,
                "audio_bitrate_mono_kbps": 160,
                "audio_bitrate_stereo_kbps": 160,
                "max_width": 320,
                "h264_profile": "baseline",
                "h264_level": "1.3",
                "preferred_audio_languages": ["eng"],
            },
        },
        "overrides": {"sonarr": {}, "radarr": {}},
    }


def bootstrap_config(
    target_path: Path,
    *,
    payload: dict[str, Any],
    force: bool,
) -> list[Path]:
    """Write bootstrap config.

    Args:
        target_path: Destination config path.
        payload: Config payload to serialize as YAML.
        force: Overwrite existing files when true.

    Returns:
        Paths that were written.
    """
    written: list[Path] = []
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists() and not force:
        raise ConfigError(f"Config already exists: {target_path}. Use --force to overwrite.")

    target_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    written.append(target_path)

    return written


def discover_config_path(explicit_path: Path | None) -> Path:
    """Discover config path from explicit value or default locations."""
    if explicit_path is not None:
        if explicit_path.exists():
            return explicit_path
        raise ConfigError(f"Config file not found: {explicit_path}")

    for path in config_search_paths():
        if path.exists():
            return path

    paths = ", ".join(str(p) for p in config_search_paths())
    raise ConfigError(f"No config file found. Searched: {paths}")


def _require_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"Config key '{key}' must be a mapping")
    return value


def _is_subpath(child: Path, parent: Path) -> bool:
    child_resolved = child.resolve(strict=False)
    parent_resolved = parent.resolve(strict=False)
    return child_resolved == parent_resolved or parent_resolved in child_resolved.parents


def _build_video_profile(name: str, data: dict[str, Any]) -> VideoProfile:
    try:
        preferred_languages_raw = data.get("preferred_audio_languages")
        preferred_language_raw = data.get("preferred_audio_language")

        preferred_languages: tuple[str, ...]
        if isinstance(preferred_languages_raw, list) and preferred_languages_raw:
            normalized = [str(item) for item in preferred_languages_raw if str(item).strip()]
            preferred_languages = tuple(normalized) if normalized else ("eng",)
        elif isinstance(preferred_language_raw, str) and preferred_language_raw.strip():
            preferred_languages = (preferred_language_raw.strip(),)
        else:
            preferred_languages = ("eng",)

        return VideoProfile(
            crf=int(data["crf"]),
            maxrate_floor_kbps=int(data["maxrate_floor_kbps"]),
            maxrate_cap_kbps=int(data["maxrate_cap_kbps"]),
            bufsize_kbps=int(data.get("bufsize_kbps", 1500)),
            bitrate_multiplier=float(data["bitrate_multiplier"]),
            audio_bitrate_mono_kbps=int(data["audio_bitrate_mono_kbps"]),
            audio_bitrate_stereo_kbps=int(data["audio_bitrate_stereo_kbps"]),
            max_width=int(data["max_width"]),
            h264_profile=str(data["h264_profile"]),
            h264_level=str(data["h264_level"]),
            preferred_audio_languages=preferred_languages,
        )
    except KeyError as exc:
        raise ConfigError(f"Profile '{name}' is missing required key: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Profile '{name}' has invalid value type: {exc}") from exc


def _parse_genre_profile_map(payload: dict[str, Any]) -> dict[str, tuple[GenreProfileRule, ...]]:
    raw = payload.get("genre_profile_map", {})
    if not isinstance(raw, dict):
        raise ConfigError("genre_profile_map must be a mapping")

    parsed: dict[str, tuple[GenreProfileRule, ...]] = {"sonarr": (), "radarr": ()}
    for provider_name in ("sonarr", "radarr"):
        provider_rules_raw = raw.get(provider_name, [])
        if provider_rules_raw is None:
            provider_rules_raw = []
        if not isinstance(provider_rules_raw, list):
            raise ConfigError(f"genre_profile_map.{provider_name} must be a list")

        rules: list[GenreProfileRule] = []
        for index, rule_raw in enumerate(provider_rules_raw):
            if not isinstance(rule_raw, dict):
                raise ConfigError(
                    f"genre_profile_map.{provider_name}[{index}] must be a mapping"
                )

            profile_raw = rule_raw.get("profile")
            if not isinstance(profile_raw, str) or not profile_raw.strip():
                raise ConfigError(
                    f"genre_profile_map.{provider_name}[{index}].profile must be a non-empty string"
                )

            genres_raw = rule_raw.get("genres")
            if genres_raw is None:
                genre_raw = rule_raw.get("genre")
                if isinstance(genre_raw, str):
                    genres_raw = [genre_raw]

            if not isinstance(genres_raw, list):
                raise ConfigError(
                    f"genre_profile_map.{provider_name}[{index}] must include 'genres' list or 'genre' string"
                )

            normalized_genres: list[str] = []
            for genre in genres_raw:
                if not isinstance(genre, str) or not genre.strip():
                    continue
                normalized = genre.casefold().strip()
                if normalized and normalized not in normalized_genres:
                    normalized_genres.append(normalized)

            if not normalized_genres:
                raise ConfigError(
                    f"genre_profile_map.{provider_name}[{index}] must include at least one non-empty genre"
                )

            rules.append(
                GenreProfileRule(
                    genres=tuple(normalized_genres),
                    profile=profile_raw.strip(),
                )
            )

        parsed[provider_name] = tuple(rules)

    return parsed


_KNOWN_APPLY_TO = {"tv", "movie"}
_KNOWN_CONFIDENCE = {"exact", "parsed"}


def _parse_artwork_fallback_provider(
    provider_name: str,
    fallback_name: str,
    raw: dict[str, Any],
    *,
    default: bool,
) -> bool:
    fallback_raw = raw.get(fallback_name, {"enabled": default})
    if fallback_raw is None:
        fallback_raw = {"enabled": default}
    if not isinstance(fallback_raw, dict):
        raise ConfigError(
            f"artwork.providers.{provider_name}.{fallback_name} must be a mapping"
        )

    enabled = fallback_raw.get("enabled", default)
    if not isinstance(enabled, bool):
        raise ConfigError(
            f"artwork.providers.{provider_name}.{fallback_name}.enabled must be a boolean"
        )
    return enabled


def _parse_artwork_provider_config(
    provider_name: str,
    raw: dict[str, Any] | None,
) -> ArtworkProviderConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"artwork.providers.{provider_name} must be a mapping")

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"artwork.providers.{provider_name}.enabled must be a boolean")

    apply_to_raw = raw.get("apply_to", ["tv"])
    if not isinstance(apply_to_raw, list) or not apply_to_raw:
        raise ConfigError(f"artwork.providers.{provider_name}.apply_to must be a non-empty list")
    apply_to: list[Literal["tv", "movie"]] = []
    for index, value in enumerate(apply_to_raw):
        if not isinstance(value, str):
            raise ConfigError(
                f"artwork.providers.{provider_name}.apply_to[{index}] must be a non-empty string"
            )
        target = value.casefold().strip()
        if not target:
            raise ConfigError(
                f"artwork.providers.{provider_name}.apply_to[{index}] must be a non-empty string"
            )
        if target not in _KNOWN_APPLY_TO:
            raise ConfigError(
                f"artwork.providers.{provider_name}.apply_to[{index}] must be 'tv' or 'movie'"
            )
        if provider_name == "itunes_tv_season" and target != "tv":
            raise ConfigError("itunes_tv_season.apply_to can only be 'tv'")
        if target not in apply_to:
            apply_to.append(cast(Literal["tv", "movie"], target))

    country_raw = raw.get("country", "US")
    if not isinstance(country_raw, str) or not country_raw.strip():
        raise ConfigError(f"artwork.providers.{provider_name}.country must be a non-empty string")
    country = country_raw.strip().upper()
    if len(country) != 2 or not country.isalpha():
        raise ConfigError(f"artwork.providers.{provider_name}.country must be a two-letter country code")

    image_size_raw = raw.get("image_size", 320)
    if not isinstance(image_size_raw, int) or isinstance(image_size_raw, bool) or image_size_raw <= 0:
        raise ConfigError(f"artwork.providers.{provider_name}.image_size must be a positive integer")

    timeout_seconds_raw = raw.get("timeout_seconds", 10)
    if (
        not isinstance(timeout_seconds_raw, int)
        or isinstance(timeout_seconds_raw, bool)
        or timeout_seconds_raw <= 0
    ):
        raise ConfigError(
            f"artwork.providers.{provider_name}.timeout_seconds must be a positive integer"
        )

    minimum_confidence_raw = raw.get("minimum_confidence", "parsed")
    if not isinstance(minimum_confidence_raw, str) or not minimum_confidence_raw.strip():
        raise ConfigError(
            f"artwork.providers.{provider_name}.minimum_confidence must be a non-empty string"
        )
    minimum_confidence = minimum_confidence_raw.strip().casefold()
    if minimum_confidence not in _KNOWN_CONFIDENCE:
        raise ConfigError(
            f"artwork.providers.{provider_name}.minimum_confidence must be one of: {', '.join(sorted(_KNOWN_CONFIDENCE))}"
        )

    fallback_providers_raw = raw.get("fallback_providers")
    if fallback_providers_raw is None:
        sonarr_fallback_enabled = _parse_artwork_fallback_provider(
            provider_name,
            "sonarr_fallback",
            raw,
            default=True,
        )
        radarr_fallback_enabled = _parse_artwork_fallback_provider(
            provider_name,
            "radarr_fallback",
            raw,
            default=True,
        )
    else:
        if not isinstance(fallback_providers_raw, list) or not fallback_providers_raw:
            raise ConfigError(
                f"artwork.providers.{provider_name}.fallback_providers must be a non-empty list"
            )
        fallback_providers: list[Literal["sonarr", "radarr"]] = []
        for index, value in enumerate(fallback_providers_raw):
            if not isinstance(value, str):
                raise ConfigError(
                    f"artwork.providers.{provider_name}.fallback_providers[{index}] must be 'sonarr' or 'radarr'"
                )
            provider = value.casefold().strip()
            if provider not in {"sonarr", "radarr"}:
                raise ConfigError(
                    f"artwork.providers.{provider_name}.fallback_providers[{index}] must be 'sonarr' or 'radarr'"
                )
            if provider not in fallback_providers:
                fallback_providers.append(cast(Literal["sonarr", "radarr"], provider))
        if not fallback_providers:
            raise ConfigError(
                f"artwork.providers.{provider_name}.fallback_providers must include at least one provider"
            )
        sonarr_fallback_enabled = "sonarr" in fallback_providers
        radarr_fallback_enabled = "radarr" in fallback_providers

    if not (sonarr_fallback_enabled or radarr_fallback_enabled):
        raise ConfigError(
            f"artwork.providers.{provider_name} must enable at least one fallback provider"
        )

    return ArtworkProviderConfig(
        enabled=enabled,
        apply_to=tuple(apply_to),
        country=country,
        image_size=image_size_raw,
        timeout_seconds=timeout_seconds_raw,
        minimum_confidence=cast(
            Literal["exact", "parsed"],
            minimum_confidence,
        ),
        sonarr_fallback=ArtworkFallbackProviderConfig(enabled=sonarr_fallback_enabled),
        radarr_fallback=ArtworkFallbackProviderConfig(enabled=radarr_fallback_enabled),
    )


def _parse_artwork_config(payload: dict[str, Any]) -> ArtworkConfig:
    raw = payload.get("artwork", {})
    if not isinstance(raw, dict):
        raise ConfigError("artwork must be a mapping")

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("artwork.enabled must be a boolean")

    providers_raw = raw.get("providers", {})
    if providers_raw is None:
        providers_raw = {}
    if not isinstance(providers_raw, dict):
        raise ConfigError("artwork.providers must be a mapping")

    providers = {
        "itunes_tv_season": _parse_artwork_provider_config(
            "itunes_tv_season",
            providers_raw.get("itunes_tv_season", {}),
        )
    }
    return ArtworkConfig(enabled=enabled, providers=providers)


def load_config(explicit_path: Path | None = None) -> tuple[Path, AppConfig]:
    """Load, parse, and validate Walkmarr config.

    Args:
        explicit_path: Optional explicit config path.

    Returns:
        Tuple of resolved config path and validated AppConfig.
    """
    config_path = discover_config_path(explicit_path)
    _load_dotenv(config_path)

    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in config '{config_path}': {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigError("Config root must be a mapping")

    providers_raw = _require_mapping(payload, "providers")
    providers: dict[str, ProviderConfig] = {}
    for provider_name in ("sonarr", "radarr"):
        provider_data = providers_raw.get(provider_name)
        if not isinstance(provider_data, dict):
            raise ConfigError(f"Missing provider config: providers.{provider_name}")
        url = provider_data.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(f"Missing provider URL: providers.{provider_name}.url")
        providers[provider_name] = ProviderConfig(
            url=url.rstrip("/"),
            api_key_env=provider_data.get("api_key_env"),
            api_key=provider_data.get("api_key"),
        )

    mappings_raw = payload.get("path_mappings")
    if not isinstance(mappings_raw, list) or not mappings_raw:
        raise ConfigError("Config key 'path_mappings' must be a non-empty list")
    path_mappings: list[PathMapping] = []
    for idx, item in enumerate(mappings_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"path_mappings[{idx}] must be a mapping")
        remote = item.get("remote")
        local = item.get("local")
        if not isinstance(remote, str) or not remote:
            raise ConfigError(f"path_mappings[{idx}].remote must be a non-empty string")
        if not isinstance(local, str) or not local:
            raise ConfigError(f"path_mappings[{idx}].local must be a non-empty string")
        path_mappings.append(PathMapping(remote=remote, local=Path(local)))

    output_roots_raw = _require_mapping(payload, "output_roots")
    shows_root = output_roots_raw.get("shows")
    movies_root = output_roots_raw.get("movies")
    if not isinstance(shows_root, str) or not shows_root:
        raise ConfigError("output_roots.shows must be a non-empty string")
    if not isinstance(movies_root, str) or not movies_root:
        raise ConfigError("output_roots.movies must be a non-empty string")
    output_roots = {"shows": Path(shows_root), "movies": Path(movies_root)}

    default_profiles = _require_mapping(payload, "default_profiles")
    for provider_name in ("sonarr", "radarr"):
        if provider_name not in default_profiles:
            raise ConfigError(f"Missing default profile key: default_profiles.{provider_name}")

    profiles_raw = _require_mapping(payload, "profiles")
    profiles: dict[str, VideoProfile] = {}
    for profile_name, profile_data in profiles_raw.items():
        if not isinstance(profile_name, str):
            raise ConfigError("Profile names must be strings")
        if not isinstance(profile_data, dict):
            raise ConfigError(f"Profile '{profile_name}' must be a mapping")
        profiles[profile_name] = _build_video_profile(profile_name, profile_data)

    overrides_raw = payload.get("overrides", {})
    if not isinstance(overrides_raw, dict):
        raise ConfigError("overrides must be a mapping")
    overrides: dict[str, dict[str, dict[str, Any]]] = {"sonarr": {}, "radarr": {}}
    for provider_name in ("sonarr", "radarr"):
        provider_overrides = overrides_raw.get(provider_name, {})
        if not isinstance(provider_overrides, dict):
            raise ConfigError(f"overrides.{provider_name} must be a mapping")
        normalized: dict[str, dict[str, Any]] = {}
        for title, values in provider_overrides.items():
            if not isinstance(title, str):
                raise ConfigError(f"overrides.{provider_name} keys must be strings")
            if not isinstance(values, dict):
                raise ConfigError(f"overrides.{provider_name}.{title} must be a mapping")
            normalized[title] = values
        overrides[provider_name] = normalized

    staging_raw = payload.get("staging", {})
    if not isinstance(staging_raw, dict):
        raise ConfigError("staging must be a mapping")
    staging_mode_raw = staging_raw.get("mode", "auto")
    if not isinstance(staging_mode_raw, str):
        raise ConfigError("staging.mode must be a string: auto, always, or never")
    staging_mode = staging_mode_raw.casefold()
    if staging_mode not in {"auto", "always", "never"}:
        raise ConfigError("staging.mode must be one of: auto, always, never")
    validated_staging_mode = cast(Literal["auto", "always", "never"], staging_mode)
    staging_directory_raw = staging_raw.get("directory", "/tmp/walkmarr-staging")
    if not isinstance(staging_directory_raw, str) or not staging_directory_raw.strip():
        raise ConfigError("staging.directory must be a non-empty string")

    queue_raw = payload.get("queue", {})
    if not isinstance(queue_raw, dict):
        raise ConfigError("queue must be a mapping")
    queue_workers = int(queue_raw.get("workers", 1))
    if queue_workers != 1:
        raise ConfigError("queue.workers must be 1 in v2")
    queue_continue_on_error = bool(queue_raw.get("continue_on_error", True))
    queue_start_paused = bool(queue_raw.get("start_paused", False))
    queue_default_mode_raw = queue_raw.get("default_mode", "missing_only")
    if not isinstance(queue_default_mode_raw, str):
        raise ConfigError("queue.default_mode must be a string")
    queue_default_mode = queue_default_mode_raw.casefold()
    if queue_default_mode not in {"missing_only", "overwrite"}:
        raise ConfigError("queue.default_mode must be one of: missing_only, overwrite")
    queue_remember_completed_until_exit = bool(
        queue_raw.get("remember_completed_until_exit", True)
    )

    debug_raw = payload.get("debug", {})
    if not isinstance(debug_raw, dict):
        raise ConfigError("debug must be a mapping")
    keep_failed_temps = bool(debug_raw.get("keep_failed_temps", False))

    genre_profile_map = _parse_genre_profile_map(payload)
    artwork = _parse_artwork_config(payload)

    app_config = AppConfig(
        providers=providers,
        path_mappings=path_mappings,
        output_roots=output_roots,
        default_profiles={
            "sonarr": str(default_profiles["sonarr"]),
            "radarr": str(default_profiles["radarr"]),
        },
        profiles=profiles,
        overrides=overrides,
        genre_profile_map=genre_profile_map,
        staging_mode=validated_staging_mode,
        staging_directory=Path(staging_directory_raw),
        allow_unmapped_existing_local=bool(payload.get("allow_unmapped_existing_local", False)),
        queue_workers=queue_workers,
        queue_continue_on_error=queue_continue_on_error,
        queue_start_paused=queue_start_paused,
        queue_default_mode=cast(Literal["missing_only", "overwrite"], queue_default_mode),
        queue_remember_completed_until_exit=queue_remember_completed_until_exit,
        keep_failed_temps=keep_failed_temps,
        artwork=artwork,
    )

    _validate_profiles_exist(app_config)
    _validate_output_roots_are_not_inside_sources(app_config)
    return config_path, app_config


def _load_dotenv(config_path: Path) -> None:
    """Load environment variables from a sibling .env file when present."""
    dotenv_path = config_path.parent / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path, override=False)


def _validate_profiles_exist(config: AppConfig) -> None:
    for provider_name in ("sonarr", "radarr"):
        profile_name = config.default_profiles[provider_name]
        if profile_name not in config.profiles:
            raise ConfigError(
                f"Default profile '{profile_name}' for '{provider_name}' is not defined"
            )

    for provider_name, items in config.overrides.items():
        for title, override_data in items.items():
            override_profile_name = override_data.get("profile")
            if override_profile_name is None:
                continue
            if not isinstance(override_profile_name, str):
                raise ConfigError(
                    f"Override profile for {provider_name}:{title} must be a string"
                )
            if override_profile_name not in config.profiles:
                raise ConfigError(
                    f"Override profile '{override_profile_name}' for {provider_name}:{title} is not defined"
                )

    for provider_name in ("sonarr", "radarr"):
        for index, rule in enumerate(config.genre_profile_map.get(provider_name, ())):
            if rule.profile not in config.profiles:
                raise ConfigError(
                    f"genre_profile_map.{provider_name}[{index}] profile '{rule.profile}' is not defined"
                )


def _validate_output_roots_are_not_inside_sources(config: AppConfig) -> None:
    for output_kind, output_root in config.output_roots.items():
        for mapping in config.path_mappings:
            if _is_subpath(output_root, mapping.local):
                raise ConfigError(
                    f"Unsafe config: output root '{output_root}' ({output_kind}) is inside "
                    f"source root '{mapping.local}'"
                )


def resolve_api_key(config: AppConfig, provider_name: str) -> str:
    """Resolve API key for provider from direct value or environment."""
    provider = config.providers[provider_name]
    if provider.api_key:
        return provider.api_key
    if provider.api_key_env:
        value = os.environ.get(provider.api_key_env)
        if value:
            return value
        raise ConfigError(
            f"Provider '{provider_name}' API key env var '{provider.api_key_env}' is not set"
        )
    raise ConfigError(
        f"Provider '{provider_name}' must define either api_key or api_key_env in config"
    )


def profile_name_for_title(config: AppConfig, provider_name: str, title: str) -> str:
    """Select effective profile for provider title using overrides first."""
    override = config.overrides.get(provider_name, {}).get(title, {})
    override_profile = override.get("profile")
    if isinstance(override_profile, str) and override_profile:
        return override_profile
    return config.default_profiles[provider_name]


def profile_name_for_sonarr_series(config: AppConfig, series: dict[str, Any]) -> str:
    """Select Sonarr profile using override and series genres."""
    title = str(series.get("title", ""))
    if title:
        override = config.overrides.get("sonarr", {}).get(title, {})
        override_profile = override.get("profile")
        if isinstance(override_profile, str) and override_profile:
            return override_profile

    genres = _normalized_genre_set(series.get("genres"))

    mapped_profile = _mapped_profile_for_genres(config, "sonarr", genres)
    if mapped_profile is not None:
        return mapped_profile

    if "animation" in genres or "anime" in genres:
        if "animation" in config.profiles:
            return "animation"
        return config.default_profiles["sonarr"]

    if "live_action" in config.profiles:
        return "live_action"
    return config.default_profiles["sonarr"]


def profile_name_for_radarr_movie(config: AppConfig, movie: dict[str, Any]) -> str:
    """Select Radarr profile using override and movie genres."""
    title = str(movie.get("title", ""))
    if title:
        override = config.overrides.get("radarr", {}).get(title, {})
        override_profile = override.get("profile")
        if isinstance(override_profile, str) and override_profile:
            return override_profile

    genres = _normalized_genre_set(movie.get("genres"))

    mapped_profile = _mapped_profile_for_genres(config, "radarr", genres)
    if mapped_profile is not None:
        return mapped_profile

    if "animation" in genres or "anime" in genres:
        if "animation" in config.profiles:
            return "animation"
        return config.default_profiles["radarr"]

    if "movie" in config.profiles:
        return "movie"
    return config.default_profiles["radarr"]


def sonarr_specials_show_name(config: AppConfig, series_title: str) -> str | None:
    """Return configured specials show name override, if any."""
    override = config.overrides.get("sonarr", {}).get(series_title, {})
    value = override.get("specials_show_name")
    if isinstance(value, str) and value:
        return value
    return None


def _normalized_genre_set(genres_raw: object) -> set[str]:
    if not isinstance(genres_raw, list):
        return set()
    return {
        str(genre).casefold().strip()
        for genre in genres_raw
        if isinstance(genre, str) and genre.strip()
    }


def _mapped_profile_for_genres(
    config: AppConfig,
    provider_name: Literal["sonarr", "radarr"],
    genres: set[str],
) -> str | None:
    rules = config.genre_profile_map.get(provider_name, ())
    if not rules:
        return None

    for rule in rules:
        if any(genre in genres for genre in rule.genres):
            return rule.profile
    return config.default_profiles[provider_name]
