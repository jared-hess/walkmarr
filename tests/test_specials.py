from pathlib import Path

from walkmarr.models import AppConfig, MediaItem, PathMapping, ProviderConfig, VideoProfile
from walkmarr.process import _build_tag_command


def test_specials_show_name_override_changes_tag_season_to_one() -> None:
    config = AppConfig(
        providers={
            "sonarr": ProviderConfig(url="http://sonarr", api_key="x"),
            "radarr": ProviderConfig(url="http://radarr", api_key="y"),
        },
        path_mappings=[PathMapping(remote="/tv", local=Path("/mnt/z/shows"))],
        output_roots={"tv": Path("/out/tv"), "movies": Path("/out/movies")},
        default_profiles={"sonarr": "animation", "radarr": "movie"},
        profiles={
            "animation": VideoProfile(30, 250, 1200, 1.5, 64, 96, 640, "baseline", "3.0"),
            "movie": VideoProfile(27, 400, 1500, 1.5, 64, 96, 640, "baseline", "3.0"),
        },
        overrides={"sonarr": {"The Simpsons": {"specials_show_name": "The Simpsons Shorts"}}, "radarr": {}},
    )
    item = MediaItem(
        kind="episode",
        source_path=Path("/mnt/z/shows/The Simpsons/Season 0/ep.mkv"),
        output_path=Path("/out/tv/The Simpsons/Season 0/out.mp4"),
        profile_name="animation",
        title="Short 1",
        series_title="The Simpsons",
        season_number=0,
        episode_number=3,
    )

    command, metadata = _build_tag_command(
        config=config,
        media_item=item,
        atomicparsley_bin="AtomicParsley",
        media_path=Path("/tmp/out.tmp.mp4"),
    )

    assert metadata["show"] == "The Simpsons Shorts"
    assert metadata["season"] == 1
    assert metadata["episode"] == 3
    assert "--TVShowName" in command
    assert command[command.index("--TVShowName") + 1] == "The Simpsons Shorts"
