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
