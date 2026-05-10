from __future__ import annotations

from importlib import import_module
import logging
from pathlib import Path
from typing import Any

import pytest

from walkmarr.models import (
    AppConfig,
    ArtworkConfig,
    ArtworkFallbackProviderConfig,
    ArtworkProviderConfig,
)

itunes = import_module("walkmarr.artwork.itunes")

MatchConfidence = itunes.MatchConfidence
match_itunes_tv_season_result = itunes.match_itunes_tv_season_result
normalize_title = itunes.normalize_title
parse_season_number = itunes.parse_season_number
parse_season_numbers = itunes.parse_season_numbers
expected_itunes_collection_name = itunes.expected_itunes_collection_name
is_special_collection_name = itunes.is_special_collection_name
upscale_itunes_artwork_url = itunes.upscale_itunes_artwork_url
resolve_itunes_tv_season_artwork = itunes.resolve_itunes_tv_season_artwork


def _result(artist_name: str, collection_name: str, artwork: str = "https://example.com/100x100bb.jpg") -> dict[str, str]:
    return {
        "artistName": artist_name,
        "collectionType": "TV Season",
        "collectionName": collection_name,
        "artworkUrl100": artwork,
    }


def _app_config(
    *,
    artwork_enabled: bool = True,
    provider_enabled: bool = True,
    sonarr_fallback_enabled: bool = True,
) -> AppConfig:
    return AppConfig(
        providers={},
        path_mappings=[],
        output_roots={},
        default_profiles={},
        profiles={},
        overrides={},
        artwork=ArtworkConfig(
            enabled=artwork_enabled,
            providers={
                "itunes_tv_season": ArtworkProviderConfig(
                    enabled=provider_enabled,
                    timeout_seconds=3,
                    sonarr_fallback=ArtworkFallbackProviderConfig(
                        enabled=sonarr_fallback_enabled
                    ),
                )
            },
        ),
    )


class _FakeResponse:
    def __init__(self, results: list[dict[str, str]]) -> None:
        self._results = results

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, list[dict[str, str]]]:
        return {"results": self._results}


def _assert_jpeg_suffix(jpeg_path: Path) -> None:
    if jpeg_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise AssertionError(
            f"normalize_artwork expected JPEG temp path, got {jpeg_path.suffix!r}"
        )


@pytest.fixture(autouse=True)
def _capture_walkmarr_logger() -> Any:
    """Keep resolver log assertions isolated from runtime logging setup tests."""

    logger = logging.getLogger("walkmarr")
    original_propagate = logger.propagate
    original_level = logger.level
    original_handlers = list(logger.handlers)
    logger.propagate = True
    logger.handlers.clear()
    try:
        yield
    finally:
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate


def test_normalize_title_removes_punctuation_and_normalizes_case() -> None:
    assert normalize_title("Friends, The") == "friends the"
    assert normalize_title("Spaced   Title!!!") == "spaced title"


def test_parse_season_number() -> None:
    assert parse_season_number("The Office, Season 2") == 2
    assert parse_season_number("Season 3 Deluxe") == 3
    assert parse_season_number("Seinfeld, Seasons 1 & 2") == 1
    assert parse_season_number("No season info") is None


def test_parse_season_numbers() -> None:
    assert parse_season_numbers("The Office, Seasons 1 & 2") == {1, 2}
    assert parse_season_numbers("Space Show, Season 3") == {3}
    assert parse_season_numbers("The Office, Seasons 1-3") == {1, 2, 3}
    assert parse_season_numbers("No season info") == set()


def test_expected_itunes_collection_name() -> None:
    assert expected_itunes_collection_name("Friends", 1) == "friends season 1"
    assert expected_itunes_collection_name("The Voyager", 10) == "the voyager season 10"


def test_is_special_collection_name() -> None:
    assert is_special_collection_name("Friends, The Best of Phoebe")
    assert is_special_collection_name("Spaced and the Holidays")
    assert is_special_collection_name("Friends, Volume 2")
    assert not is_special_collection_name("Friends, Season 1")


def test_upscale_itunes_artwork_url_rewrites_resolution() -> None:
    assert (
        upscale_itunes_artwork_url("https://is1-ssl.mzstatic.com/image/thumb/Music117/v4/..../100x100bb.jpg")
        == "https://is1-ssl.mzstatic.com/image/thumb/Music117/v4/..../320x320bb.jpg"
    )


def test_exact_match_uses_exact_collection_name_and_artist_filter() -> None:
    results = [
        _result("Friends", "Friends, Season 1"),
        _result("Friends", "Friends, Season 2"),
        _result("Not Friends", "Friends, Season 1"),
    ]

    match = match_itunes_tv_season_result(
        results=results,
        requested_artist="Friends",
        requested_title="Friends",
        requested_season=1,
    )

    assert match.confidence == MatchConfidence.EXACT
    assert match.result is not None
    assert match.result["collectionName"] == "Friends, Season 1"
    assert match.artwork_url.endswith("320x320bb.jpg")


def test_parsed_match_selects_request_season_when_results_unsorted() -> None:
    results = [
        _result("Star Trek Voyager", "Star Trek Voyager, Season 2"),
        _result("Star Trek Voyager", "Star Trek Voyager, Season 1"),
        _result("Other Show", "Star Trek Voyager, Season 1"),
    ]

    match = match_itunes_tv_season_result(
        results=results,
        requested_artist="Star Trek Voyager",
        requested_title="Voyager",
        requested_season=1,
    )

    assert match.confidence == MatchConfidence.PARSED
    assert match.result is not None
    assert match.result["collectionName"] == "Star Trek Voyager, Season 1"
    assert match.artwork_url.endswith("320x320bb.jpg")


@pytest.mark.parametrize(
    "requested_season",
    [1, 2],
)
def test_parsed_match_accepts_tv_season_collection_with_multiple_seasons(requested_season: int) -> None:
    results = [_result("Seinfeld", "Seinfeld, Seasons 1 & 2")]

    match = match_itunes_tv_season_result(
        results=results,
        requested_artist="Seinfeld",
        requested_title="Seinfeld",
        requested_season=requested_season,
    )

    assert match.confidence == MatchConfidence.PARSED
    assert match.result is not None
    assert match.result["collectionName"] == "Seinfeld, Seasons 1 & 2"


def test_parsed_match_rejects_non_matching_season_from_multi_season_label() -> None:
    results = [_result("Seinfeld", "Seinfeld, Seasons 1 & 2")]

    match = match_itunes_tv_season_result(
        results=results,
        requested_artist="Seinfeld",
        requested_title="Seinfeld",
        requested_season=3,
    )

    assert match.confidence == MatchConfidence.NONE
    assert match.result is None


def test_false_positive_friends_artists_are_rejected() -> None:
    results = [
        _result("The Best of Phoebe", "The Best of Phoebe"),
        _result("The One With All the Holidays", "The One With All the Holidays"),
        _result("Smiling Friends", "Smiling Friends, Season 1"),
        _result("Spidey and His Amazing Friends", "Spidey and His Amazing Friends, Season 1"),
    ]

    match = match_itunes_tv_season_result(
        results=results,
        requested_artist="Friends",
        requested_title="Friends",
        requested_season=1,
    )

    assert match.confidence == MatchConfidence.NONE
    assert match.result is None


def test_special_collections_are_rejected_by_parsed_matcher() -> None:
    results = [
        _result("Friends", "Friends, The Best of Phoebe", "https://example.com/100x100bb.jpg"),
        _result("Friends", "Friends, The One With All the Holidays", "https://example.com/100x100bb.jpg"),
        _result("Friends", "Friends, Season 1"),
    ]

    match = match_itunes_tv_season_result(
        results=results,
        requested_artist="Friends",
        requested_title="Friends",
        requested_season=1,
    )

    assert match.confidence == MatchConfidence.EXACT
    assert match.result is not None
    assert match.result["collectionName"] == "Friends, Season 1"


def test_ambiguous_matching_returns_ambiguous() -> None:
    results = [
        _result("Friends", "Friends, Season 1", artwork="https://example.com/100x100bb-a.jpg"),
        _result("Friends", "Friends, Season 1", artwork="https://example.com/100x100bb-b.jpg"),
    ]

    match = match_itunes_tv_season_result(
        results=results,
        requested_artist="Friends",
        requested_title="Friends",
        requested_season=1,
    )

    assert match.confidence == MatchConfidence.AMBIGUOUS
    assert match.result is None
    assert match.artwork_url is None


def test_resolver_downloads_to_staging_artwork_path(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _app_config()
    staging_artwork_path = tmp_path / "staging" / "artwork.abc123.itunes.320x320.jpg"
    downloaded_paths: list[Path] = []
    normalized_paths: list[tuple[Path, Path]] = []

    def fake_http_get(_url: str, *, params: dict[str, Any], timeout: int) -> _FakeResponse:
        assert params["term"] == "Friends season 2"
        assert timeout == 3
        return _FakeResponse([_result("Friends", "Friends, Season 2")])

    def fake_download(_url: str, path: Path) -> None:
        downloaded_paths.append(path)
        path.write_bytes(b"raw image")

    def fake_normalize(raw_path: Path, jpeg_path: Path, _token: object | None) -> None:
        normalized_paths.append((raw_path, jpeg_path))
        _assert_jpeg_suffix(jpeg_path)
        jpeg_path.write_bytes(b"normalized jpg")

    caplog.set_level(logging.INFO, logger="walkmarr")
    resolution = resolve_itunes_tv_season_artwork(
        config=config,
        provider_kind="sonarr",
        series_id=42,
        series_title="Friends",
        season_number=2,
        fallback_artwork="fallback.jpg",
        staging_artwork_path=staging_artwork_path,
        http_get=fake_http_get,
        download_artwork=fake_download,
        normalize_artwork=fake_normalize,
    )

    assert resolution.artwork == staging_artwork_path
    assert resolution.source == "itunes"
    assert staging_artwork_path.read_bytes() == b"normalized jpg"
    assert downloaded_paths == [staging_artwork_path.with_suffix(".download")]
    assert normalized_paths == [
        (
            staging_artwork_path.with_suffix(".download"),
            staging_artwork_path.with_name(f"{staging_artwork_path.stem}.normalized.jpg"),
        )
    ]
    assert not downloaded_paths[0].exists()
    assert not normalized_paths[0][1].exists()
    assert "iTunes artwork resolved" in caplog.text


def test_resolver_queries_season_term_then_generic_and_falls_back_on_no_match(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _app_config()
    calls: list[dict[str, Any]] = []

    def fake_http_get(_url: str, *, params: dict[str, Any], timeout: int) -> _FakeResponse:
        calls.append({"params": params, "timeout": timeout})
        return _FakeResponse([])

    caplog.set_level(logging.INFO, logger="walkmarr")
    resolution = resolve_itunes_tv_season_artwork(
        config=config,
        provider_kind="sonarr",
        series_id=42,
        series_title="Friends",
        season_number=2,
        fallback_artwork="fallback.jpg",
        staging_artwork_path=tmp_path / "artwork.itunes.jpg",
        http_get=fake_http_get,
        download_artwork=lambda _url, _path: None,
        normalize_artwork=lambda _raw, _jpg, _token: None,
    )

    assert resolution.artwork == "fallback.jpg"
    assert resolution.source == "fallback"
    assert [call["params"]["term"] for call in calls] == ["Friends season 2", "Friends"]
    for call in calls:
        assert call["params"] | {"term": call["params"]["term"]} == {
            "term": call["params"]["term"],
            "media": "tvShow",
            "entity": "tvSeason",
            "country": "US",
            "limit": 50,
        }
        assert call["timeout"] == 3
    assert "no match" in caplog.text
    assert "using provider fallback artwork" in caplog.text


def test_resolver_tries_original_artwork_when_upscaled_download_fails(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _app_config()
    staging_artwork_path = tmp_path / "staging" / "artwork.def456.itunes.320x320.jpg"
    downloaded_urls: list[str] = []

    def fake_http_get(_url: str, *, params: dict[str, Any], timeout: int) -> _FakeResponse:
        assert params["term"] == "Friends season 1"
        assert timeout == 3
        return _FakeResponse([_result("Friends", "Friends, Season 1")])

    def fake_download(url: str, path: Path) -> None:
        downloaded_urls.append(url)
        if "320x320bb" in url:
            raise RuntimeError("upscaled image unavailable")
        path.write_bytes(b"raw image")

    def fake_normalize(_raw_path: Path, jpeg_path: Path, _token: object | None) -> None:
        _assert_jpeg_suffix(jpeg_path)
        jpeg_path.write_bytes(b"normalized jpg")

    caplog.set_level(logging.INFO, logger="walkmarr")
    resolution = resolve_itunes_tv_season_artwork(
        config=config,
        provider_kind="sonarr",
        series_id=42,
        series_title="Friends",
        season_number=1,
        fallback_artwork="fallback.jpg",
        staging_artwork_path=staging_artwork_path,
        http_get=fake_http_get,
        download_artwork=fake_download,
        normalize_artwork=fake_normalize,
    )

    assert downloaded_urls == [
        "https://example.com/320x320bb.jpg",
        "https://example.com/100x100bb.jpg",
    ]
    assert resolution.artwork == staging_artwork_path
    assert resolution.source == "itunes"
    assert resolution.confidence == MatchConfidence.EXACT
    assert not staging_artwork_path.with_suffix(".download").exists()
    assert not staging_artwork_path.with_suffix(".normalized").exists()
    assert not staging_artwork_path.with_name(f"{staging_artwork_path.stem}.normalized.jpg").exists()
    assert "download failed" in caplog.text
    assert "iTunes artwork resolved" in caplog.text


def test_resolver_cleans_raw_temp_file_after_success(tmp_path: Path) -> None:
    config = _app_config()
    staging_artwork_path = tmp_path / "staging" / "artwork.ghi789.itunes.320x320.jpg"
    raw_paths: list[Path] = []

    def fake_http_get(_url: str, *, params: dict[str, Any], timeout: int) -> _FakeResponse:
        return _FakeResponse([_result("Friends", "Friends, Season 1")])

    def fake_download(_url: str, path: Path) -> None:
        raw_paths.append(path)
        path.write_bytes(b"raw image")

    def fake_normalize(raw_path: Path, jpeg_path: Path, _token: object | None) -> None:
        assert raw_path.exists()
        _assert_jpeg_suffix(jpeg_path)
        jpeg_path.write_bytes(b"normalized jpg")

    resolution = resolve_itunes_tv_season_artwork(
        config=config,
        provider_kind="sonarr",
        series_id=42,
        series_title="Friends",
        season_number=1,
        fallback_artwork="fallback.jpg",
        staging_artwork_path=staging_artwork_path,
        http_get=fake_http_get,
        download_artwork=fake_download,
        normalize_artwork=fake_normalize,
    )

    assert resolution.artwork == staging_artwork_path
    assert staging_artwork_path.exists()
    assert raw_paths == [staging_artwork_path.with_suffix(".download")]
    assert not raw_paths[0].exists()
    assert not staging_artwork_path.with_suffix(".normalized").exists()
    assert not staging_artwork_path.with_name(f"{staging_artwork_path.stem}.normalized.jpg").exists()


def test_resolver_still_uses_itunes_when_sonarr_fallback_disabled(tmp_path: Path) -> None:
    config = _app_config(
        sonarr_fallback_enabled=False,
    )
    staging_artwork_path = tmp_path / "staging" / "artwork.jkl012.itunes.320x320.jpg"
    http_terms: list[str] = []

    def fake_http_get(_url: str, *, params: dict[str, Any], timeout: int) -> _FakeResponse:
        http_terms.append(params["term"])
        return _FakeResponse([_result("Friends", "Friends, Season 1")])

    def fake_download(_url: str, path: Path) -> None:
        path.write_bytes(b"raw image")

    def fake_normalize(_raw_path: Path, jpeg_path: Path, _token: object | None) -> None:
        _assert_jpeg_suffix(jpeg_path)
        jpeg_path.write_bytes(b"normalized jpg")

    resolution = resolve_itunes_tv_season_artwork(
        config=config,
        provider_kind="sonarr",
        series_id=42,
        series_title="Friends",
        season_number=1,
        fallback_artwork=None,
        staging_artwork_path=staging_artwork_path,
        http_get=fake_http_get,
        download_artwork=fake_download,
        normalize_artwork=fake_normalize,
    )

    assert resolution.artwork == staging_artwork_path
    assert resolution.source == "itunes"
    assert http_terms == ["Friends season 1"]


def test_resolver_logs_ambiguity_and_uses_fallback(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _app_config()

    def fake_http_get(_url: str, *, params: dict[str, Any], timeout: int) -> _FakeResponse:
        return _FakeResponse(
            [
                _result("Friends", "Friends, Season 1", artwork="https://example.com/a/100x100bb.jpg"),
                _result("Friends", "Friends, Season 1", artwork="https://example.com/b/100x100bb.jpg"),
            ]
        )

    caplog.set_level(logging.INFO, logger="walkmarr")
    resolution = resolve_itunes_tv_season_artwork(
        config=config,
        provider_kind="sonarr",
        series_id=42,
        series_title="Friends",
        season_number=1,
        fallback_artwork="fallback.jpg",
        staging_artwork_path=tmp_path / "artwork.itunes.jpg",
        http_get=fake_http_get,
        download_artwork=lambda _url, _path: None,
        normalize_artwork=lambda _raw, _jpg, _token: None,
    )

    assert resolution.artwork == "fallback.jpg"
    assert "ambiguous match" in caplog.text
    assert "using provider fallback artwork" in caplog.text


def test_resolver_dry_run_returns_fallback_without_staging_or_network_writes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _app_config()
    calls: list[str] = []

    caplog.set_level(logging.INFO, logger="walkmarr")
    resolution = resolve_itunes_tv_season_artwork(
        config=config,
        provider_kind="sonarr",
        series_id=42,
        series_title="Friends",
        season_number=1,
        fallback_artwork="fallback.jpg",
        staging_artwork_path=tmp_path / "artwork.itunes.jpg",
        dry_run=True,
        http_get=lambda *_args, **_kwargs: calls.append("http"),
        download_artwork=lambda _url, _path: calls.append("download"),
        normalize_artwork=lambda _raw, _jpg, _token: calls.append("normalize"),
    )

    assert resolution.artwork == "fallback.jpg"
    assert resolution.reason == "dry-run"
    assert calls == []
    assert not (tmp_path / "artwork.itunes.jpg").exists()
    assert "dry-run would query iTunes" in caplog.text


@pytest.mark.parametrize(
    ("provider_kind", "season_number", "artwork_enabled", "provider_enabled", "reason"),
    [
        ("radarr", 1, True, True, "provider is not sonarr"),
        ("sonarr", 0, True, True, "season zero uses provider fallback"),
        ("sonarr", 1, False, True, "global artwork disabled"),
        ("sonarr", 1, True, False, "itunes provider disabled"),
    ],
)
def test_resolver_skips_unsupported_contexts_without_network(
    tmp_path: Path,
    provider_kind: str,
    season_number: int,
    artwork_enabled: bool,
    provider_enabled: bool,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _app_config(
        artwork_enabled=artwork_enabled,
        provider_enabled=provider_enabled,
    )

    def fail_http_get(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("network should not be called for skipped contexts")

    caplog.set_level(logging.INFO, logger="walkmarr")
    resolution = resolve_itunes_tv_season_artwork(
        config=config,
        provider_kind=provider_kind,
        series_id=42,
        series_title="Friends",
        season_number=season_number,
        fallback_artwork="fallback.jpg",
        staging_artwork_path=tmp_path / "artwork.itunes.jpg",
        http_get=fail_http_get,
    )

    assert resolution.artwork == "fallback.jpg"
    assert resolution.reason == reason
    assert reason in caplog.text


def test_exact_threshold_blocks_parsed_matches() -> None:
    results = [
        _result("Star Trek Voyager", "Star Trek Voyager, Season 2"),
        _result("Star Trek Voyager", "Star Trek Voyager, Season 1"),
    ]

    match = match_itunes_tv_season_result(
        results=results,
        requested_artist="Star Trek Voyager",
        requested_title="Voyager",
        requested_season=1,
        minimum_confidence=MatchConfidence.EXACT,
    )

    assert match.confidence == MatchConfidence.NONE
    assert match.result is None


def test_minimum_confidence_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="minimum_confidence must be one of: exact or parsed"):
        match_itunes_tv_season_result(
            results=[],
            requested_artist="Friends",
            requested_title="Friends",
            requested_season=1,
            minimum_confidence="either",
        )
