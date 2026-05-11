from pathlib import Path

import pytest

from walkmarr.scan.aspect import (
    AspectMetadata,
    aspect_ratio,
    extract_radarr_metadata,
    extract_sonarr_metadata,
    format_tsv,
    matches_aspect,
    parse_ratio,
    probe_aspect_metadata,
)


def test_parse_ratio_accepts_colon_and_decimal_values() -> None:
    assert parse_ratio("4:3") == pytest.approx(4 / 3)
    assert parse_ratio("1.777") == pytest.approx(1.777)


@pytest.mark.parametrize("value", ["", "0", "4:0", "abc", "4:three", "nan", "inf"])
def test_parse_ratio_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_ratio(value)


def test_aspect_ratio_computes_width_over_height() -> None:
    assert aspect_ratio(640, 480) == pytest.approx(4 / 3)


@pytest.mark.parametrize(
    ("width", "height", "mode", "expected"),
    [
        (640, 480, "near", True),
        (720, 480, "near", False),
        (720, 480, "wider", True),
        (640, 520, "taller", True),
        (640, 480, "exact", True),
        (641, 480, "exact", False),
    ],
)
def test_matches_aspect_modes(width: int, height: int, mode: str, expected: bool) -> None:
    metadata = AspectMetadata(width=width, height=height, source="provider")

    assert matches_aspect(metadata, target_ratio=parse_ratio("4:3"), tolerance=0.03, mode=mode) is expected


@pytest.mark.parametrize("tolerance", [float("nan"), float("inf"), -0.01])
def test_matches_aspect_rejects_invalid_tolerance(tolerance: float) -> None:
    metadata = AspectMetadata(width=640, height=480, source="provider")

    with pytest.raises(ValueError):
        matches_aspect(metadata, target_ratio=parse_ratio("4:3"), tolerance=tolerance, mode="near")


def test_extract_sonarr_metadata_uses_episode_file_media_info() -> None:
    episode = {
        "title": "Pilot",
        "seasonNumber": 1,
        "episodeNumber": 1,
        "hasFile": True,
        "episodeFileId": 10,
    }
    file_record = {
        "id": 10,
        "path": "/shows/Test/Pilot.mkv",
        "mediaInfo": {"width": 640, "height": 480},
    }

    records = extract_sonarr_metadata(
        series={"title": "Test Show"},
        episodes=[episode],
        episode_files=[file_record],
    )

    assert len(records) == 1
    assert records[0].provider == "sonarr"
    assert records[0].title == "Test Show"
    assert records[0].item == "S01E01"
    assert records[0].path == "/shows/Test/Pilot.mkv"
    assert records[0].metadata == AspectMetadata(width=640, height=480, source="provider")


def test_extract_sonarr_metadata_skips_missing_media_info() -> None:
    records = extract_sonarr_metadata(
        series={"title": "Test Show"},
        episodes=[{"hasFile": True, "episodeFileId": 10}],
        episode_files=[{"id": 10, "path": "/shows/Test/Pilot.mkv"}],
    )

    assert records == []


def test_extract_sonarr_metadata_uses_media_info_resolution_when_dimensions_missing() -> None:
    records = extract_sonarr_metadata(
        series={"title": "Test Show"},
        episodes=[
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "hasFile": True,
                "episodeFileId": 10,
            }
        ],
        episode_files=[
            {
                "id": 10,
                "path": "/shows/Test/Pilot.mkv",
                "mediaInfo": {"resolution": "1440x1080"},
            }
        ],
    )

    assert records[0].metadata == AspectMetadata(width=1440, height=1080, source="provider")


def test_extract_sonarr_metadata_skips_malformed_episode_file_ids() -> None:
    records = extract_sonarr_metadata(
        series={"title": "Test Show"},
        episodes=[{"hasFile": True, "episodeFileId": 10}],
        episode_files=[
            {"id": None, "path": "/shows/Test/Broken.mkv", "mediaInfo": {"width": 640, "height": 480}},
            {"id": "bad", "path": "/shows/Test/Bad.mkv", "mediaInfo": {"width": 640, "height": 480}},
        ],
    )

    assert records == []


def test_extract_sonarr_metadata_handles_missing_episode_numbers() -> None:
    records = extract_sonarr_metadata(
        series={"title": "Test Show"},
        episodes=[{"hasFile": True, "episodeFileId": 10}],
        episode_files=[
            {"id": 10, "path": "/shows/Test/Pilot.mkv", "mediaInfo": {"width": 640, "height": 480}},
        ],
    )

    assert records[0].item == ""


def test_extract_radarr_metadata_uses_movie_file_media_info() -> None:
    records = extract_radarr_metadata(
        movie={
            "title": "Test Movie",
            "year": 1999,
            "movieFile": {
                "path": "/movies/Test Movie.mkv",
                "mediaInfo": {"width": 1920, "height": 1080},
            },
        }
    )

    assert len(records) == 1
    assert records[0].provider == "radarr"
    assert records[0].title == "Test Movie"
    assert records[0].item == "1999"
    assert records[0].path == "/movies/Test Movie.mkv"
    assert records[0].metadata == AspectMetadata(width=1920, height=1080, source="provider")


def test_extract_radarr_metadata_uses_media_info_resolution_when_dimensions_missing() -> None:
    records = extract_radarr_metadata(
        movie={
            "title": "Test Movie",
            "year": 1999,
            "movieFile": {
                "path": "/movies/Test Movie.mkv",
                "mediaInfo": {"resolution": "720x540"},
            },
        }
    )

    assert records[0].metadata == AspectMetadata(width=720, height=540, source="provider")


def test_extract_radarr_metadata_skips_missing_media_info() -> None:
    assert extract_radarr_metadata(movie={"title": "Test Movie", "movieFile": {"path": "x"}}) == []


def test_format_tsv_sanitizes_control_characters() -> None:
    records = extract_radarr_metadata(
        movie={
            "title": "Bad\tMovie\nName",
            "year": 2001,
            "movieFile": {
                "path": "/movies/Bad\rMovie.mkv",
                "mediaInfo": {"width": 640, "height": 480},
            },
        }
    )

    lines = format_tsv(records, target_ratio=parse_ratio("4:3"))

    assert lines[1] == "radarr\tBad Movie Name\t2001\t/movies/Bad Movie.mkv\t640\t480\t1.3333\tprovider\t0.0000"


def test_probe_aspect_metadata_uses_display_aspect_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    class Completed:
        stdout = '{"streams":[{"width":720,"height":480,"display_aspect_ratio":"4:3"}]}'

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: Completed())

    metadata = probe_aspect_metadata(Path("/media/in.mkv"))

    assert metadata == AspectMetadata(
        width=720,
        height=480,
        source="probe",
        display_aspect_ratio=parse_ratio("4:3"),
    )
