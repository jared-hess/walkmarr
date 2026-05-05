from pathlib import Path

from walkmarr.paths import build_movie_output_path, build_tv_output_path


def test_tv_output_path_format() -> None:
    path = build_tv_output_path(
        output_root=Path("/mnt/d/ipod/shows"),
        series_title="Futurama",
        season_number=1,
        episode_number=1,
        episode_title="Space Pilot 3000",
    )
    assert path == Path(
        "/mnt/d/ipod/shows/Futurama/Season 1/Futurama - S01E01 - Space Pilot 3000.mp4"
    )


def test_movie_output_path_format() -> None:
    path = build_movie_output_path(
        output_root=Path("/mnt/d/ipod/movies"),
        movie_title="American Psycho",
        year=2000,
    )
    assert path == Path(
        "/mnt/d/ipod/movies/American Psycho (2000)/American Psycho (2000).mp4"
    )


def test_padding_and_sanitization_for_tv() -> None:
    path = build_tv_output_path(
        output_root=Path("/out"),
        series_title="A/B\\C ",
        season_number=3,
        episode_number=9,
        episode_title="Ep/Name\\Cut...",
    )
    assert path == Path("/out/A-B-C/Season 3/A-B-C - S03E09 - Ep-Name-Cut.mp4")
