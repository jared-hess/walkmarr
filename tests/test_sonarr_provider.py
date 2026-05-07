from pathlib import Path
from typing import Any, cast

import pytest

from walkmarr.exceptions import ProviderError
from walkmarr.models import PathMapping
from walkmarr.providers.sonarr import SonarrProvider


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, route_payloads: dict[tuple[str, tuple[tuple[str, int], ...]], Any]) -> None:
        self.route_payloads = route_payloads

    def get(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        timeout: float,
    ) -> FakeResponse:
        del headers, timeout
        key_params: tuple[tuple[str, int], ...]
        if params is None:
            key_params = ()
        else:
            key_params = tuple(sorted((str(k), int(v)) for k, v in params.items()))
        payload = self.route_payloads[(url, key_params)]
        return FakeResponse(payload)


def _provider(session: FakeSession) -> SonarrProvider:
    return SonarrProvider(url="http://localhost:8989", api_key="x", session=cast(Any, session))


def test_list_series() -> None:
    session = FakeSession(
        {
            ("http://localhost:8989/api/v3/series", ()): [
                {"id": 1, "title": "Futurama"},
                {"id": 2, "title": "The Simpsons"},
            ]
        }
    )
    provider = _provider(session)
    series = provider.list_series()
    assert [s["title"] for s in series] == ["Futurama", "The Simpsons"]


def test_get_series_by_id() -> None:
    session = FakeSession(
        {
            ("http://localhost:8989/api/v3/series/1", ()): {"id": 1, "title": "Futurama"}
        }
    )
    provider = _provider(session)
    series = provider.get_series_by_id(1)
    assert series["id"] == 1
    assert series["title"] == "Futurama"


def test_exact_title_match() -> None:
    provider = _provider(FakeSession({}))
    matched = provider.match_series(
        "Futurama",
        [{"id": 1, "title": "Futurama"}, {"id": 2, "title": "futurama"}],
    )
    assert matched["id"] == 1


def test_case_insensitive_title_match() -> None:
    provider = _provider(FakeSession({}))
    matched = provider.match_series("futurama", [{"id": 1, "title": "Futurama"}])
    assert matched["id"] == 1


def test_ambiguous_case_insensitive_match() -> None:
    provider = _provider(FakeSession({}))
    with pytest.raises(ProviderError, match="Ambiguous"):
        provider.match_series(
            "futurama",
            [{"id": 1, "title": "Futurama"}, {"id": 2, "title": "FUTURAMA"}],
        )


def test_build_episode_media_items() -> None:
    provider = _provider(FakeSession({}))
    episodes = [
        {
            "id": 101,
            "title": "Space Pilot 3000",
            "seasonNumber": 1,
            "episodeNumber": 1,
            "hasFile": True,
            "episodeFileId": 201,
        }
    ]
    episode_files = [{"id": 201, "path": "Z:/shows/Futurama/Season 1/S01E01.mkv"}]
    items = provider.build_media_items(
        series_title="Futurama",
        episodes=episodes,
        episode_files=episode_files,
        profile_name="animation",
        path_mappings=[PathMapping(remote="Z:/shows", local=Path("/mnt/z/shows"))],
        output_root=Path("/mnt/d/ipod/shows"),
        series_genre="Animation",
    )

    assert len(items) == 1
    item = items[0]
    assert item.source_path == Path("/mnt/z/shows/Futurama/Season 1/S01E01.mkv")
    assert item.output_path == Path(
        "/mnt/d/ipod/shows/Futurama/Season 1/Futurama - S01E01 - Space Pilot 3000.mp4"
    )
    assert item.title == "Space Pilot 3000"
    assert item.genre == "Animation"


def test_build_episode_media_items_multi_episode_range() -> None:
    provider = _provider(FakeSession({}))
    episodes = [
        {
            "id": 101,
            "title": "Part 1",
            "seasonNumber": 1,
            "episodeNumber": 1,
            "hasFile": True,
            "episodeFileId": 201,
        },
        {
            "id": 102,
            "title": "Part 2",
            "seasonNumber": 1,
            "episodeNumber": 2,
            "hasFile": True,
            "episodeFileId": 201,
        },
    ]
    episode_files = [{"id": 201, "path": "Z:/shows/Futurama/Season 1/S01E01E02.mkv"}]
    items = provider.build_media_items(
        series_title="Futurama",
        episodes=episodes,
        episode_files=episode_files,
        profile_name="animation",
        path_mappings=[PathMapping(remote="Z:/shows", local=Path("/mnt/z/shows"))],
        output_root=Path("/mnt/d/ipod/shows"),
    )

    assert len(items) == 1
    item = items[0]
    assert item.episode_id == "S01E01-E02"
    assert item.episode_end_number == 2
    assert item.output_path == Path(
        "/mnt/d/ipod/shows/Futurama/Season 1/Futurama - S01E01-E02 - Part 1.mp4"
    )
