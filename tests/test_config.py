from pathlib import Path

import pytest

from walkmarr.config import load_config, resolve_api_key
from walkmarr.exceptions import ConfigError


def _write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_load_config_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        """
providers:
  sonarr:
    url: "http://localhost:8989"
    api_key_env: "SONARR_API_KEY"
  radarr:
    url: "http://localhost:7878"
    api_key_env: "RADARR_API_KEY"
path_mappings:
  - remote: "/tv"
    local: "/mnt/z/shows"
  - remote: "/movies"
    local: "/mnt/z/movies"
output_roots:
  shows: "/mnt/d/ipod/shows"
  movies: "/mnt/d/ipod/movies"
default_profiles:
  sonarr: "animation"
  radarr: "movie"
profiles:
  animation:
    crf: 30
    maxrate_floor_kbps: 250
    maxrate_cap_kbps: 1200
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
  movie:
    crf: 27
    maxrate_floor_kbps: 400
    maxrate_cap_kbps: 1500
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
overrides:
  sonarr:
    Futurama:
      profile: "animation"
  radarr:
    American Psycho:
      profile: "movie"
""",
    )

    monkeypatch.setenv("SONARR_API_KEY", "abc")
    monkeypatch.setenv("RADARR_API_KEY", "def")

    loaded_path, config = load_config(cfg_path)
    assert loaded_path == cfg_path
    assert resolve_api_key(config, "sonarr") == "abc"
    assert resolve_api_key(config, "radarr") == "def"


def test_missing_profile_error(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        """
providers:
  sonarr:
    url: "http://localhost:8989"
    api_key: "x"
  radarr:
    url: "http://localhost:7878"
    api_key: "y"
path_mappings:
  - remote: "/tv"
    local: "/mnt/z/shows"
output_roots:
  shows: "/mnt/d/ipod/shows"
  movies: "/mnt/d/ipod/movies"
default_profiles:
  sonarr: "animation"
  radarr: "missing"
profiles:
  animation:
    crf: 30
    maxrate_floor_kbps: 250
    maxrate_cap_kbps: 1200
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
""",
    )

    with pytest.raises(ConfigError, match="Default profile 'missing'"):
        load_config(cfg_path)


def test_loads_api_key_from_sibling_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        """
providers:
  sonarr:
    url: "http://localhost:8989"
    api_key_env: "SONARR_API_KEY"
  radarr:
    url: "http://localhost:7878"
    api_key_env: "RADARR_API_KEY"
path_mappings:
  - remote: "/tv"
    local: "/mnt/z/shows"
  - remote: "/movies"
    local: "/mnt/z/movies"
output_roots:
  shows: "/mnt/d/ipod/shows"
  movies: "/mnt/d/ipod/movies"
default_profiles:
  sonarr: "animation"
  radarr: "movie"
profiles:
  animation:
    crf: 30
    maxrate_floor_kbps: 250
    maxrate_cap_kbps: 1200
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
  movie:
    crf: 27
    maxrate_floor_kbps: 400
    maxrate_cap_kbps: 1500
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
""",
    )
    (tmp_path / ".env").write_text("SONARR_API_KEY=from_dotenv\nRADARR_API_KEY=from_dotenv2\n")

    monkeypatch.delenv("SONARR_API_KEY", raising=False)
    monkeypatch.delenv("RADARR_API_KEY", raising=False)

    _, config = load_config(cfg_path)
    assert resolve_api_key(config, "sonarr") == "from_dotenv"
    assert resolve_api_key(config, "radarr") == "from_dotenv2"


def test_load_config_queue_defaults(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        """
providers:
  sonarr:
    url: "http://localhost:8989"
    api_key: "x"
  radarr:
    url: "http://localhost:7878"
    api_key: "y"
path_mappings:
  - remote: "/tv"
    local: "/mnt/z/shows"
  - remote: "/movies"
    local: "/mnt/z/movies"
output_roots:
  shows: "/mnt/d/ipod/shows"
  movies: "/mnt/d/ipod/movies"
default_profiles:
  sonarr: "animation"
  radarr: "movie"
profiles:
  animation:
    crf: 30
    maxrate_floor_kbps: 250
    maxrate_cap_kbps: 1200
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
  movie:
    crf: 27
    maxrate_floor_kbps: 400
    maxrate_cap_kbps: 1500
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
""",
    )
    _, config = load_config(cfg_path)
    assert config.queue_workers == 1
    assert config.queue_continue_on_error is True
    assert config.queue_start_paused is False
    assert config.queue_default_mode == "missing_only"


def test_load_config_rejects_queue_workers_not_one(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        """
providers:
  sonarr:
    url: "http://localhost:8989"
    api_key: "x"
  radarr:
    url: "http://localhost:7878"
    api_key: "y"
path_mappings:
  - remote: "/tv"
    local: "/mnt/z/shows"
  - remote: "/movies"
    local: "/mnt/z/movies"
output_roots:
  shows: "/mnt/d/ipod/shows"
  movies: "/mnt/d/ipod/movies"
default_profiles:
  sonarr: "animation"
  radarr: "movie"
profiles:
  animation:
    crf: 30
    maxrate_floor_kbps: 250
    maxrate_cap_kbps: 1200
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
  movie:
    crf: 27
    maxrate_floor_kbps: 400
    maxrate_cap_kbps: 1500
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
queue:
  workers: 2
""",
    )

    with pytest.raises(ConfigError, match="queue.workers must be 1"):
        load_config(cfg_path)


def test_load_config_rejects_genre_profile_map_unknown_profile(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        """
providers:
  sonarr:
    url: "http://localhost:8989"
    api_key: "x"
  radarr:
    url: "http://localhost:7878"
    api_key: "y"
path_mappings:
  - remote: "/tv"
    local: "/mnt/z/shows"
  - remote: "/movies"
    local: "/mnt/z/movies"
output_roots:
  shows: "/mnt/d/ipod/shows"
  movies: "/mnt/d/ipod/movies"
default_profiles:
  sonarr: "animation"
  radarr: "movie"
profiles:
  animation:
    crf: 30
    maxrate_floor_kbps: 250
    maxrate_cap_kbps: 1200
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
  movie:
    crf: 27
    maxrate_floor_kbps: 400
    maxrate_cap_kbps: 1500
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
genre_profile_map:
  sonarr:
    - genres: ["documentary"]
      profile: "docs"
""",
    )

    with pytest.raises(
        ConfigError,
        match=r"genre_profile_map\.sonarr\[0\] profile 'docs' is not defined",
    ):
        load_config(cfg_path)


_MINIMUM_VALID_CONFIG = """
providers:
  sonarr:
    url: "http://localhost:8989"
    api_key: "x"
  radarr:
    url: "http://localhost:7878"
    api_key: "y"
path_mappings:
  - remote: "/tv"
    local: "/mnt/z/shows"
  - remote: "/movies"
    local: "/mnt/z/movies"
output_roots:
  shows: "/mnt/d/ipod/shows"
  movies: "/mnt/d/ipod/movies"
default_profiles:
  sonarr: "animation"
  radarr: "movie"
profiles:
  animation:
    crf: 30
    maxrate_floor_kbps: 250
    maxrate_cap_kbps: 1200
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
  movie:
    crf: 27
    maxrate_floor_kbps: 400
    maxrate_cap_kbps: 1500
    bitrate_multiplier: 1.5
    audio_bitrate_mono_kbps: 64
    audio_bitrate_stereo_kbps: 96
    max_width: 640
    h264_profile: "baseline"
    h264_level: "3.0"
"""


def test_keep_failed_temps_defaults_false(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path / "walkmarr.yml", _MINIMUM_VALID_CONFIG)
    _, config = load_config(cfg_path)
    assert config.keep_failed_temps is False


def test_keep_failed_temps_true(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        _MINIMUM_VALID_CONFIG
        + """
debug:
  keep_failed_temps: true
""",
    )
    _, config = load_config(cfg_path)
    assert config.keep_failed_temps is True


def test_keep_failed_temps_false_explicit(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        _MINIMUM_VALID_CONFIG
        + """
debug:
  keep_failed_temps: false
""",
    )
    _, config = load_config(cfg_path)
    assert config.keep_failed_temps is False


def test_artwork_defaults_enable_itunes_tv_only(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path / "walkmarr.yml", _MINIMUM_VALID_CONFIG)
    _, config = load_config(cfg_path)

    assert config.artwork.enabled is True
    itunes_provider = config.artwork.providers["itunes_tv_season"]
    assert itunes_provider.enabled is True
    assert itunes_provider.apply_to == ("tv",)
    assert itunes_provider.country == "US"
    assert itunes_provider.image_size == 320
    assert itunes_provider.timeout_seconds == 10
    assert not hasattr(itunes_provider, "cache_enabled")
    assert not hasattr(itunes_provider, "cache_dir")
    assert itunes_provider.minimum_confidence == "parsed"
    assert itunes_provider.sonarr_fallback.enabled is True
    assert itunes_provider.radarr_fallback.enabled is True


def test_artwork_disable_provider_keeps_fallbacks_default(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        _MINIMUM_VALID_CONFIG
        + """
artwork:
  providers:
    itunes_tv_season:
      enabled: false
""",
    )

    _, config = load_config(cfg_path)
    itunes_provider = config.artwork.providers["itunes_tv_season"]
    assert itunes_provider.enabled is False
    assert itunes_provider.sonarr_fallback.enabled is True
    assert itunes_provider.radarr_fallback.enabled is True


def test_artwork_minimum_confidence_allows_only_exact_or_parsed(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        _MINIMUM_VALID_CONFIG
        + """
artwork:
  providers:
    itunes_tv_season:
      minimum_confidence: exact
""",
    )

    _, config = load_config(cfg_path)
    assert config.artwork.providers["itunes_tv_season"].minimum_confidence == "exact"


def test_artwork_rejects_invalid_minimum_confidence(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        _MINIMUM_VALID_CONFIG
        + """
artwork:
  providers:
    itunes_tv_season:
      minimum_confidence: medium
""",
    )

    with pytest.raises(
        ConfigError,
        match=r"artwork\.providers\.itunes_tv_season\.minimum_confidence must be one of: exact, parsed",
    ):
        load_config(cfg_path)


def test_artwork_rejects_itunes_movie_apply_to(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        _MINIMUM_VALID_CONFIG
        + """
artwork:
  providers:
    itunes_tv_season:
      apply_to:
        - movie
""",
    )

    with pytest.raises(ConfigError, match="itunes_tv_season.apply_to can only be 'tv'"):
        load_config(cfg_path)


def test_artwork_fallback_provider_flags_default_true(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "walkmarr.yml",
        _MINIMUM_VALID_CONFIG
        + """
artwork:
  providers:
    itunes_tv_season:
      sonarr_fallback:
        enabled: false
      radarr_fallback:
        enabled: true
""",
    )

    _, config = load_config(cfg_path)
    itunes_provider = config.artwork.providers["itunes_tv_season"]
    assert itunes_provider.sonarr_fallback.enabled is False
    assert itunes_provider.radarr_fallback.enabled is True
