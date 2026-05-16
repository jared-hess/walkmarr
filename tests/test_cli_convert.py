from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from walkmarr.cli import main
from walkmarr.models import AppConfig, MediaItem, PathMapping, ProviderConfig, VideoProfile


def _config(tmp_path: Path) -> AppConfig:
    profile = VideoProfile(
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
    return AppConfig(
        providers={
            "sonarr": ProviderConfig(url="http://sonarr", api_key="x"),
            "radarr": ProviderConfig(url="http://radarr", api_key="y"),
        },
        path_mappings=[
            PathMapping(remote="/shows", local=tmp_path / "shows"),
            PathMapping(remote="/movies", local=tmp_path / "movies"),
        ],
        output_roots={"shows": tmp_path / "out-shows", "movies": tmp_path / "out-movies"},
        default_profiles={"sonarr": "animation", "radarr": "movie"},
        profiles={"animation": profile, "movie": profile},
        overrides={"sonarr": {}, "radarr": {}},
        staging_mode="auto",
        staging_directory=tmp_path / "staging",
    )


def test_sonarr_convert_accepts_staging_mode_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source_path = tmp_path / "shows" / "episode.mkv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"x")

    item = MediaItem(
        kind="episode",
        source_path=source_path,
        output_path=tmp_path / "out-shows" / "Episode.mp4",
        profile_name="animation",
        title="Episode",
        series_title="Show",
        season_number=1,
        episode_number=1,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr("walkmarr.cli._get_config", lambda _runtime: config)
    monkeypatch.setattr("walkmarr.cli.ensure_required_tools", lambda: "AtomicParsley")
    monkeypatch.setattr("walkmarr.cli.resolve_api_key", lambda _config, _provider: "key")

    class FakeSonarrProvider:
        def __init__(self, *, url: str, api_key: str) -> None:
            del url, api_key

        def list_series(self) -> list[dict[str, object]]:
            return [{"title": "Show", "id": 1}]

        def match_series(
            self,
            title: str,
            all_series: list[dict[str, object]],
        ) -> dict[str, object]:
            del title
            return all_series[0]

        def list_episodes(self, selected_id: int) -> list[dict[str, object]]:
            del selected_id
            return []

        def list_episode_files(self, selected_id: int) -> list[dict[str, object]]:
            del selected_id
            return []

        def build_media_items(
            self,
            **kwargs: object,
        ) -> list[MediaItem]:
            captured["series_id"] = kwargs["series_id"]
            return [item]

        def poster_url(self, series: dict[str, object]) -> str | None:
            del series
            return "https://image.example/show-poster.jpg"

    monkeypatch.setattr("walkmarr.cli.SonarrProvider", FakeSonarrProvider)

    def _fake_process_media_items(**kwargs: object) -> SimpleNamespace:
        local_config = kwargs["config"]
        assert isinstance(local_config, AppConfig)
        captured["staging_mode"] = local_config.staging_mode
        return SimpleNamespace(converted=1, skipped=0)

    monkeypatch.setattr("walkmarr.cli.process_media_items", _fake_process_media_items)

    runner = CliRunner()
    result = runner.invoke(main, ["sonarr", "convert", "Show", "--staging-mode", "never"])

    assert result.exit_code == 0
    assert captured["staging_mode"] == "never"
    assert captured["series_id"] == 1


def test_radarr_convert_accepts_staging_mode_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source_path = tmp_path / "movies" / "movie.mkv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"x")

    item = MediaItem(
        kind="movie",
        source_path=source_path,
        output_path=tmp_path / "out-movies" / "Movie.mp4",
        profile_name="movie",
        title="Movie",
        movie_title="Movie",
        year=2000,
    )
    captured: dict[str, str] = {}

    monkeypatch.setattr("walkmarr.cli._get_config", lambda _runtime: config)
    monkeypatch.setattr("walkmarr.cli.ensure_required_tools", lambda: "AtomicParsley")
    monkeypatch.setattr("walkmarr.cli.resolve_api_key", lambda _config, _provider: "key")

    class FakeRadarrProvider:
        def __init__(self, *, url: str, api_key: str) -> None:
            del url, api_key

        def list_movies(self) -> list[dict[str, object]]:
            return [{"title": "Movie", "year": 2000}]

        def match_movie(
            self,
            title: str,
            movies: list[dict[str, object]],
        ) -> dict[str, object]:
            del title
            return movies[0]

        def build_media_item(
            self,
            **kwargs: object,
        ) -> MediaItem:
            del kwargs
            return item

    monkeypatch.setattr("walkmarr.cli.RadarrProvider", FakeRadarrProvider)

    def _fake_process_media_items(**kwargs: object) -> SimpleNamespace:
        local_config = kwargs["config"]
        assert isinstance(local_config, AppConfig)
        captured["staging_mode"] = local_config.staging_mode
        return SimpleNamespace(converted=1, skipped=0)

    monkeypatch.setattr("walkmarr.cli.process_media_items", _fake_process_media_items)

    runner = CliRunner()
    result = runner.invoke(main, ["radarr", "convert", "Movie", "--staging-mode", "always"])

    assert result.exit_code == 0
    assert captured["staging_mode"] == "always"
