from pathlib import Path

from walkmarr.models import MediaItem


def test_media_item_episode_shape() -> None:
    item = MediaItem(
        kind="episode",
        source_path=Path("/src/file.mkv"),
        output_path=Path("/out/file.mp4"),
        profile_name="animation",
        title="Space Pilot 3000",
        series_title="Futurama",
        season_number=1,
        episode_number=1,
        episode_id="S01E01",
    )
    assert item.kind == "episode"
    assert item.series_title == "Futurama"
    assert item.episode_id == "S01E01"


def test_media_item_movie_shape() -> None:
    item = MediaItem(
        kind="movie",
        source_path=Path("/src/movie.mkv"),
        output_path=Path("/out/movie.mp4"),
        profile_name="movie",
        title="American Psycho",
        movie_title="American Psycho",
        year=2000,
    )
    assert item.kind == "movie"
    assert item.movie_title == "American Psycho"
    assert item.year == 2000
