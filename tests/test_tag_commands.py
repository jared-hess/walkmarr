from pathlib import Path

from walkmarr.tag.mp4 import build_movie_tag_command, build_tv_tag_command


def test_tv_tag_command_contains_required_fields() -> None:
    cmd = build_tv_tag_command(
        "AtomicParsley",
        Path("/tmp/episode.mp4"),
        episode_title="Space Pilot 3000",
        show_title="Futurama",
        season_number=1,
        episode_number=1,
    )
    assert "--stik" in cmd and cmd[cmd.index("--stik") + 1] == "TV Show"
    assert "--title" in cmd and cmd[cmd.index("--title") + 1] == "Space Pilot 3000"
    assert "--TVShowName" in cmd and cmd[cmd.index("--TVShowName") + 1] == "Futurama"
    assert "--TVSeasonNum" in cmd and cmd[cmd.index("--TVSeasonNum") + 1] == "1"
    assert "--TVEpisodeNum" in cmd and cmd[cmd.index("--TVEpisodeNum") + 1] == "1"
    assert "--TVEpisode" in cmd and cmd[cmd.index("--TVEpisode") + 1] == "S01E01"


def test_tv_tag_command_accepts_episode_id_override() -> None:
    cmd = build_tv_tag_command(
        "AtomicParsley",
        Path("/tmp/episode.mp4"),
        episode_title="Pilot",
        show_title="Show",
        season_number=1,
        episode_number=1,
        tv_episode_id="S01E01-E02",
    )
    assert "--TVEpisode" in cmd
    assert cmd[cmd.index("--TVEpisode") + 1] == "S01E01-E02"


def test_movie_tag_command_contains_required_fields() -> None:
    cmd = build_movie_tag_command(
        "AtomicParsley",
        Path("/tmp/movie.mp4"),
        movie_title="American Psycho",
        year=2000,
    )
    assert "--stik" in cmd and cmd[cmd.index("--stik") + 1] == "Movie"
    assert "--title" in cmd and cmd[cmd.index("--title") + 1] == "American Psycho"
    assert "--year" in cmd and cmd[cmd.index("--year") + 1] == "2000"


def test_tag_commands_include_genre_when_provided() -> None:
    tv_cmd = build_tv_tag_command(
        "AtomicParsley",
        Path("/tmp/episode.mp4"),
        episode_title="Pilot",
        show_title="Show",
        season_number=1,
        episode_number=1,
        genre="Comedy",
    )
    movie_cmd = build_movie_tag_command(
        "AtomicParsley",
        Path("/tmp/movie.mp4"),
        movie_title="Movie",
        year=None,
        genre="Action",
    )
    assert "--genre" in tv_cmd and tv_cmd[tv_cmd.index("--genre") + 1] == "Comedy"
    assert "--genre" in movie_cmd and movie_cmd[movie_cmd.index("--genre") + 1] == "Action"
