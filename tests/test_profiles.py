from pathlib import Path
from dataclasses import replace

from walkmarr.config import (
    profile_name_for_radarr_movie,
    profile_name_for_sonarr_series,
    profile_name_for_title,
)
from walkmarr.convert.video import calculate_maxrate_kbps
from walkmarr.models import AppConfig, GenreProfileRule, PathMapping, ProviderConfig, VideoProfile


def _profile() -> VideoProfile:
    return VideoProfile(
        crf=30,
        maxrate_floor_kbps=250,
        maxrate_cap_kbps=1200,
        bitrate_multiplier=1.5,
        audio_bitrate_mono_kbps=64,
        audio_bitrate_stereo_kbps=96,
        max_width=640,
        h264_profile="baseline",
        h264_level="3.0",
    )


def _config() -> AppConfig:
    profile = _profile()
    return AppConfig(
        providers={
            "sonarr": ProviderConfig(url="http://sonarr"),
            "radarr": ProviderConfig(url="http://radarr"),
        },
        path_mappings=[PathMapping(remote="/tv", local=Path("/mnt/z/shows"))],
        output_roots={
            "shows": Path("/mnt/d/ipod/shows"),
            "movies": Path("/mnt/d/ipod/movies"),
        },
        default_profiles={"sonarr": "animation", "radarr": "movie"},
        profiles={"animation": profile, "movie": profile, "live_action": profile},
        overrides={
            "sonarr": {"Arrested Development": {"profile": "live_action"}},
            "radarr": {"American Psycho": {"profile": "movie"}},
        },
    )


def test_default_provider_profile() -> None:
    config = _config()
    assert profile_name_for_title(config, "sonarr", "Futurama") == "animation"


def test_series_override_profile() -> None:
    config = _config()
    assert profile_name_for_title(config, "sonarr", "Arrested Development") == "live_action"


def test_movie_override_profile() -> None:
    config = _config()
    assert profile_name_for_title(config, "radarr", "American Psycho") == "movie"


def test_maxrate_calculation_clamps_to_cap() -> None:
    profile = _profile()
    assert calculate_maxrate_kbps(3175, profile) == 1200


def test_maxrate_unknown_source_uses_cap() -> None:
    profile = _profile()
    assert calculate_maxrate_kbps(None, profile) == 1200


def test_sonarr_profile_uses_animation_genre() -> None:
    config = _config()
    series = {"title": "Futurama", "genres": ["Comedy", "Animation"]}
    assert profile_name_for_sonarr_series(config, series) == "animation"


def test_sonarr_profile_uses_live_action_when_not_animation() -> None:
    config = _config()
    series = {"title": "Breaking Bad", "genres": ["Drama", "Crime"]}
    assert profile_name_for_sonarr_series(config, series) == "live_action"


def test_sonarr_profile_override_wins_over_genres() -> None:
    config = _config()
    series = {"title": "Arrested Development", "genres": ["Animation"]}
    assert profile_name_for_sonarr_series(config, series) == "live_action"


def test_radarr_profile_uses_animation_genre() -> None:
    config = _config()
    movie = {"title": "Spider-Verse", "genres": ["Animation", "Action"]}
    assert profile_name_for_radarr_movie(config, movie) == "animation"


def test_radarr_profile_defaults_to_movie_for_non_animation() -> None:
    config = _config()
    movie = {"title": "Heat", "genres": ["Crime", "Drama"]}
    assert profile_name_for_radarr_movie(config, movie) == "movie"


def test_radarr_profile_override_wins_over_genres() -> None:
    config = _config()
    movie = {"title": "American Psycho", "genres": ["Animation"]}
    assert profile_name_for_radarr_movie(config, movie) == "movie"


def test_genre_profile_map_matches_top_down() -> None:
    config = replace(
        _config(),
        default_profiles={"sonarr": "movie", "radarr": "movie"},
        genre_profile_map={
            "sonarr": (
                GenreProfileRule(genres=("comedy",), profile="live_action"),
                GenreProfileRule(genres=("action",), profile="animation"),
            ),
            "radarr": (),
        },
    )
    series = {"title": "Mixed Show", "genres": ["Action", "Comedy"]}
    assert profile_name_for_sonarr_series(config, series) == "live_action"


def test_genre_profile_map_falls_back_to_default_profile() -> None:
    config = replace(
        _config(),
        default_profiles={"sonarr": "live_action", "radarr": "movie"},
        genre_profile_map={
            "sonarr": (GenreProfileRule(genres=("documentary",), profile="movie"),),
            "radarr": (),
        },
    )
    series = {"title": "Drama Show", "genres": ["Drama"]}
    assert profile_name_for_sonarr_series(config, series) == "live_action"
