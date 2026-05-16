from pathlib import Path
from typing import Literal

from click.testing import CliRunner
import pytest

from walkmarr.cli import main
from walkmarr.models import AppConfig, PathMapping, ProviderConfig, VideoProfile


def _profile(
    *,
    scan_target_aspect_ratio: str = "4:3",
    scan_tolerance: float = 0.03,
    scan_match_mode: Literal["near", "wider", "taller", "exact"] = "near",
) -> VideoProfile:
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
        scan_target_aspect_ratio=scan_target_aspect_ratio,
        scan_tolerance=scan_tolerance,
        scan_match_mode=scan_match_mode,
    )


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        providers={
            "sonarr": ProviderConfig(url="http://sonarr", api_key="x"),
            "radarr": ProviderConfig(url="http://radarr", api_key="y"),
        },
        path_mappings=[PathMapping(remote="/shows", local=tmp_path / "shows")],
        output_roots={"shows": tmp_path / "out-shows", "movies": tmp_path / "out-movies"},
        default_profiles={"sonarr": "animation", "radarr": "movie"},
        profiles={"animation": _profile(), "movie": _profile()},
        overrides={"sonarr": {}, "radarr": {}},
    )


class FakeSonarrProvider:
    def __init__(self, *, url: str, api_key: str) -> None:
        del url, api_key

    def list_series(self) -> list[dict[str, object]]:
        return [{"title": "Wide Show", "id": 2}, {"title": "Four Three Show", "id": 1}]

    def list_episodes(self, series_id: int) -> list[dict[str, object]]:
        return [
            {
                "title": "Episode",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "hasFile": True,
                "episodeFileId": series_id * 10,
            }
        ]

    def list_episode_files(self, series_id: int) -> list[dict[str, object]]:
        dimensions = {1: (640, 480), 2: (1920, 1080)}[series_id]
        return [
            {
                "id": series_id * 10,
                "path": f"/shows/{series_id}/Episode.mkv",
                "mediaInfo": {"width": dimensions[0], "height": dimensions[1]},
            }
        ]


class FakeRadarrProvider:
    def __init__(self, *, url: str, api_key: str) -> None:
        del url, api_key

    def list_movies(self) -> list[dict[str, object]]:
        return [
            {
                "title": "Four Three Movie",
                "year": 2001,
                "movieFile": {
                    "path": "/movies/Four Three Movie.mkv",
                    "mediaInfo": {"width": 720, "height": 540},
                },
            },
            {
                "title": "Wide Movie",
                "year": 2002,
                "movieFile": {
                    "path": "/movies/Wide Movie.mkv",
                    "mediaInfo": {"width": 1920, "height": 1080},
                },
            },
        ]


def _patch_cli(monkeypatch: pytest.MonkeyPatch, config: AppConfig) -> None:
    monkeypatch.setattr("walkmarr.cli._get_config", lambda _runtime: config)
    monkeypatch.setattr("walkmarr.cli.resolve_api_key", lambda _config, _provider: "key")
    monkeypatch.setattr("walkmarr.cli.SonarrProvider", FakeSonarrProvider)
    monkeypatch.setattr("walkmarr.cli.RadarrProvider", FakeRadarrProvider)


def test_scan_aspect_sonarr_provider_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_cli(monkeypatch, _config(tmp_path))

    result = CliRunner().invoke(main, ["scan", "aspect", "--provider", "sonarr"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "provider\ttitle\titem\tpath\twidth\theight\taspect\tsource\tdelta",
        "sonarr\tFour Three Show\tS01E01\t/shows/1/Episode.mkv\t640\t480\t1.3333\tprovider\t0.0000",
    ]


def test_scan_aspect_radarr_provider_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_cli(monkeypatch, _config(tmp_path))

    result = CliRunner().invoke(main, ["scan", "aspect", "--provider", "radarr"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "provider\ttitle\titem\tpath\twidth\theight\taspect\tsource\tdelta",
        "radarr\tFour Three Movie\t2001\t/movies/Four Three Movie.mkv\t720\t540\t1.3333\tprovider\t0.0000",
    ]


def test_scan_aspect_all_providers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_cli(monkeypatch, _config(tmp_path))

    result = CliRunner().invoke(main, ["scan", "aspect", "--provider", "all"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "provider\ttitle\titem\tpath\twidth\theight\taspect\tsource\tdelta",
        "radarr\tFour Three Movie\t2001\t/movies/Four Three Movie.mkv\t720\t540\t1.3333\tprovider\t0.0000",
        "sonarr\tFour Three Show\tS01E01\t/shows/1/Episode.mkv\t640\t480\t1.3333\tprovider\t0.0000",
    ]


def test_scan_aspect_uses_profile_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    profile = _profile(scan_target_aspect_ratio="16:9", scan_tolerance=0.01, scan_match_mode="near")
    config = AppConfig(
        providers=config.providers,
        path_mappings=config.path_mappings,
        output_roots=config.output_roots,
        default_profiles=config.default_profiles,
        profiles={"animation": profile, "movie": profile},
        overrides=config.overrides,
    )
    _patch_cli(monkeypatch, config)

    result = CliRunner().invoke(main, ["scan", "aspect", "--provider", "radarr", "--profile", "movie"])

    assert result.exit_code == 0, result.output
    assert "radarr\tWide Movie\t2002\t/movies/Wide Movie.mkv\t1920\t1080\t1.7778\tprovider\t0.0000" in result.output


def test_scan_aspect_probe_source_uses_local_path_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _patch_cli(monkeypatch, config)
    captured: list[Path] = []

    def fake_probe(path: Path) -> object:
        captured.append(path)
        from walkmarr.scan.aspect import AspectMetadata

        return AspectMetadata(width=720, height=480, source="probe", display_aspect_ratio=4 / 3)

    monkeypatch.setattr("walkmarr.cli.probe_aspect_metadata", fake_probe)

    result = CliRunner().invoke(main, ["scan", "aspect", "--provider", "sonarr", "--source", "probe"])

    assert result.exit_code == 0, result.output
    assert captured == [tmp_path / "shows" / "2" / "Episode.mkv", tmp_path / "shows" / "1" / "Episode.mkv"]
    assert result.output.splitlines() == [
        "provider\ttitle\titem\tpath\twidth\theight\taspect\tsource\tdelta",
        "sonarr\tFour Three Show\tS01E01\t/shows/1/Episode.mkv\t720\t480\t1.3333\tprobe\t0.0000",
        "sonarr\tWide Show\tS01E01\t/shows/2/Episode.mkv\t720\t480\t1.3333\tprobe\t0.0000",
    ]
