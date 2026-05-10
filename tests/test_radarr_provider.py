from pathlib import Path
from typing import Any, cast

import pytest

from walkmarr.exceptions import ProviderError
from walkmarr.models import PathMapping
from walkmarr.providers.radarr import RadarrProvider


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


def _provider(session: FakeSession) -> RadarrProvider:
    return RadarrProvider(url="http://localhost:7878", api_key="x", session=cast(Any, session))


def test_list_movies() -> None:
    session = FakeSession(
        {
            ("http://localhost:7878/api/v3/movie", ()): [
                {"id": 1, "title": "American Psycho", "year": 2000},
                {"id": 2, "title": "Heat", "year": 1995},
            ]
        }
    )
    provider = _provider(session)
    movies = provider.list_movies()
    assert [m["title"] for m in movies] == ["American Psycho", "Heat"]


def test_get_movie_by_id() -> None:
    session = FakeSession(
        {
            ("http://localhost:7878/api/v3/movie/28", ()): {
                "id": 28,
                "title": "Alien",
                "year": 1979,
                "movieFile": {"path": "Z:/movies/Alien (1979)/Alien.mkv"},
            }
        }
    )
    provider = _provider(session)
    movie = provider.get_movie_by_id(28)
    assert movie["id"] == 28
    assert movie["title"] == "Alien"


def test_exact_title_match() -> None:
    provider = _provider(FakeSession({}))
    matched = provider.match_movie(
        "Heat",
        [{"id": 1, "title": "heat"}, {"id": 2, "title": "Heat"}],
    )
    assert matched["id"] == 2


def test_case_insensitive_title_match() -> None:
    provider = _provider(FakeSession({}))
    matched = provider.match_movie("heat", [{"id": 1, "title": "Heat"}])
    assert matched["id"] == 1


def test_ambiguous_case_insensitive_match() -> None:
    provider = _provider(FakeSession({}))
    with pytest.raises(ProviderError, match="Ambiguous"):
        provider.match_movie("heat", [{"id": 1, "title": "Heat"}, {"id": 2, "title": "HEAT"}])


def test_build_movie_media_item() -> None:
    provider = _provider(FakeSession({}))
    item = provider.build_media_item(
        movie={
            "title": "American Psycho",
            "year": 2000,
            "genres": ["Drama", "Crime"],
            "inCinemas": "2000-01-21T00:00:00Z",
            "images": [
                {"coverType": "fanart", "remoteUrl": "https://image.example/fanart.jpg"},
                {"coverType": "poster", "remoteUrl": "https://image.example/poster.jpg"},
            ],
            "movieFile": {"path": "Z:/movies/American Psycho (2000)/source.mkv"},
        },
        profile_name="movie",
        path_mappings=[PathMapping(remote="Z:/movies", local=Path("/mnt/z/movies"))],
        output_root=Path("/mnt/d/ipod/movies"),
    )
    assert item.source_path == Path("/mnt/z/movies/American Psycho (2000)/source.mkv")
    assert item.output_path == Path(
        "/mnt/d/ipod/movies/American Psycho (2000)/American Psycho (2000).mp4"
    )
    assert item.release_date == "2000-01-21"
    assert item.genre == "Drama"
    assert item.artwork_url == "https://image.example/poster.jpg"
    assert item.provider_item_id is None
    assert item.series_title is None
    assert item.season_number is None


def test_movie_poster_url_accepts_relative_provider_url() -> None:
    provider = _provider(FakeSession({}))
    url = provider.poster_url(
        {
            "title": "American Psycho",
            "images": [{"coverType": "poster", "url": "/MediaCover/1/poster.jpg"}],
        }
    )
    assert url == "http://localhost:7878/MediaCover/1/poster.jpg"


def test_build_movie_media_item_uses_release_date_year_when_missing() -> None:
    provider = _provider(FakeSession({}))
    item = provider.build_media_item(
        movie={
            "title": "Classic",
            "inCinemas": "1979-05-25T00:00:00Z",
            "movieFile": {"path": "Z:/movies/Classic/source.mkv"},
        },
        profile_name="movie",
        path_mappings=[PathMapping(remote="Z:/movies", local=Path("/mnt/z/movies"))],
        output_root=Path("/mnt/d/ipod/movies"),
    )

    assert item.year == 1979
    assert item.release_date == "1979-05-25"
