"""iTunes TV season artwork matching and resolver helpers.

The matcher helpers are deterministic and side-effect free. The resolver is
dependency-injected so HTTP, download, and normalization work remains mockable in
tests and safe for callers to treat as a non-fatal fallback source.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import logging
from pathlib import Path
import re
from typing import Any, cast
from urllib.parse import urlencode

import requests

from walkmarr.models import AppConfig


LOGGER = logging.getLogger("walkmarr")
ITUNES_TV_SEASON_PROVIDER = "itunes_tv_season"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


class MatchConfidence(str, Enum):
    """Confidence levels for iTunes season match results."""

    EXACT = "exact"
    PARSED = "parsed"
    NONE = "none"
    AMBIGUOUS = "ambiguous"


_NON_ALPHANUMERIC_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")
_SEASON_EXPRESSION_RE = re.compile(
    r"\bseason(?:s)?\s*(?P<season_numbers>(?:\d+\s*(?:[,&-]|\b(?:to|through|and)\b)\s*)*\d+)",
    re.IGNORECASE,
)
_SEASON_TO_RANGE_RE = re.compile(r"\b(?:to|through)\b", re.IGNORECASE)
_SEASON_LIST_SEPARATOR_RE = re.compile(r"\band\b|[,&]", re.IGNORECASE)
_SIZE_RE = re.compile(r"\b\d+x\d+bb\b", re.IGNORECASE)
_SPECIAL_COLLECTION_PATTERNS = (
    re.compile(r"\bbest of\b", re.IGNORECASE),
    re.compile(r"\bholidays\b", re.IGNORECASE),
    re.compile(r"\bcollection\b", re.IGNORECASE),
    re.compile(r"\bspecials\b", re.IGNORECASE),
    re.compile(r"\bvol\.?\b", re.IGNORECASE),
    re.compile(r"\bvolume\b", re.IGNORECASE),
)


def normalize_title(title: str) -> str:
    """Normalize a title for strict comparisons.

    We drop punctuation, normalize whitespace, and lowercase values.
    """

    normalized = title.strip().lower()
    normalized = _NON_ALPHANUMERIC_RE.sub(" ", normalized)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def parse_season_number(collection_name: str) -> int | None:
    """Extract the season number from an iTunes collection name.

    Returns ``None`` when a parseable season is not present.
    """

    seasons = parse_season_numbers(collection_name)
    if not seasons:
        return None
    return min(seasons)


def parse_season_numbers(collection_name: str) -> set[int]:
    """Extract all season numbers from an iTunes collection name.

    Supports pluralized labels and range/list style expressions, for example
    ``"Season 1"``, ``"Seasons 1 & 2"`` and ``"Seasons 1-2"``.
    """

    season_numbers: set[int] = set()

    for season_match in _SEASON_EXPRESSION_RE.finditer(collection_name):
        expression = season_match.group("season_numbers")
        if not expression:
            continue

        expression = _SEASON_TO_RANGE_RE.sub("-", expression)
        expression = _SEASON_LIST_SEPARATOR_RE.sub(",", expression)

        for raw_segment in expression.split(","):
            segment = raw_segment.strip()
            if not segment:
                continue

            if "-" in segment:
                range_segments = [part.strip() for part in segment.split("-") if part.strip()]
                if len(range_segments) != 2:
                    continue
                start_str, end_str = range_segments
                if not (start_str.isdigit() and end_str.isdigit()):
                    continue
                start = int(start_str)
                end = int(end_str)
                if start <= end:
                    season_numbers.update(range(start, end + 1))
                else:
                    season_numbers.update(range(end, start + 1))
            else:
                if segment.isdigit():
                    season_numbers.add(int(segment))

    return season_numbers


def collection_includes_season(collection_name: str, requested_season: int) -> bool:
    """Return True when a collection name includes the requested season."""

    return requested_season in parse_season_numbers(collection_name)


def expected_itunes_collection_name(series_title: str, season_number: int) -> str:
    """Build the expected normalized iTunes collection name for a season."""

    return f"{normalize_title(series_title)} season {season_number}"


def is_special_collection_name(collection_name: str) -> bool:
    """Return True for collection names that are likely non-episode seasons."""

    for pattern in _SPECIAL_COLLECTION_PATTERNS:
        if pattern.search(collection_name):
            return True
    return False


def upscale_itunes_artwork_url(url: str) -> str:
    """Rewrite a 100x100 iTunes artwork URL to 320x320.

    The iTunes API often returns low-res ``100x100bb`` URLs. This helper rewrites
    the first size token so callers can fetch a higher quality image.
    """

    return _SIZE_RE.sub("320x320bb", url, count=1)


@dataclass(frozen=True)
class ItunesTVSeasonMatch:
    """Result from ``match_itunes_tv_season_result``.

    ``result`` contains the selected API result dictionary when a single match
    exists; otherwise it is ``None``.
    """

    confidence: MatchConfidence
    result: Mapping[str, Any] | None
    artwork_url: str | None


ArtworkDownloadHook = Callable[[str, Path], None]
ArtworkNormalizeHook = Callable[[Path, Path, Any], None]
ItunesSearchGetter = Callable[..., Any]


@dataclass(frozen=True)
class ItunesArtworkResolution:
    """Resolved artwork path or fallback decision from iTunes lookup."""

    artwork: str | Path | None
    source: str
    reason: str
    confidence: MatchConfidence = MatchConfidence.NONE


def _coerce_confidence(value: MatchConfidence | str) -> MatchConfidence:
    """Convert raw string values into ``MatchConfidence``.

    Raises ``ValueError`` for unknown values.
    """

    if isinstance(value, MatchConfidence):
        return value
    try:
        return MatchConfidence(value)
    except ValueError as err:
        raise ValueError(
            "minimum_confidence must be one of: exact or parsed"
        ) from err


def _to_mapping(result: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize mapping input without mutating the caller's object."""

    return dict(result)


def match_itunes_tv_season_result(
    results: Iterable[Mapping[str, Any]],
    requested_artist: str,
    requested_title: str,
    requested_season: int,
    *,
    minimum_confidence: MatchConfidence | str = MatchConfidence.PARSED,
) -> ItunesTVSeasonMatch:
    """Match iTunes TV season results with deterministic precedence.

    Matching rules:

    - exact: ``artistName`` matches normalized request, ``collectionType`` is
      ``TV Season``, collection name exactly matches the expected normalized
      target, and artwork is present.
    - parsed: ``artistName`` matches normalized request, season parsed from
      collection name equals requested season, collection contains ``Season``,
      is not a special collection, and artwork is present.

    Returns ``AMBIGUOUS`` when multiple equally strong matches exist.
    """

    minimum_confidence = _coerce_confidence(minimum_confidence)

    normalized_artist = normalize_title(requested_artist)
    expected_collection_name = expected_itunes_collection_name(requested_title, requested_season)

    exact_matches: list[ItunesTVSeasonMatch] = []
    parsed_matches: list[ItunesTVSeasonMatch] = []

    for item in results:
        collection_type = cast(str, item.get("collectionType", ""))
        if collection_type != "TV Season":
            continue

        artist = item.get("artistName")
        if not isinstance(artist, str):
            continue
        if normalize_title(artist) != normalized_artist:
            continue

        collection = item.get("collectionName")
        if not isinstance(collection, str):
            continue

        artwork = item.get("artworkUrl100")
        if not isinstance(artwork, str) or not artwork:
            continue

        normalized_collection = normalize_title(collection)
        scaled_artwork = upscale_itunes_artwork_url(artwork)
        mapped_result = _to_mapping(item)
        match = ItunesTVSeasonMatch(
            confidence=MatchConfidence.NONE,
            result=mapped_result,
            artwork_url=scaled_artwork,
        )

        if normalized_collection == expected_collection_name:
            exact_matches.append(match)
            continue

        if not collection_includes_season(collection, requested_season):
            continue
        if is_special_collection_name(collection):
            continue
        parsed_matches.append(match)

    # Exact match has priority.
    if minimum_confidence in {MatchConfidence.EXACT, MatchConfidence.PARSED}:
        if len(exact_matches) == 1:
            exact = exact_matches[0]
            return ItunesTVSeasonMatch(
                confidence=MatchConfidence.EXACT,
                result=exact.result,
                artwork_url=exact.artwork_url,
            )
        if len(exact_matches) > 1:
            return ItunesTVSeasonMatch(
                confidence=MatchConfidence.AMBIGUOUS,
                result=None,
                artwork_url=None,
            )

    if minimum_confidence == MatchConfidence.EXACT:
        return ItunesTVSeasonMatch(confidence=MatchConfidence.NONE, result=None, artwork_url=None)

    # Parsed fallback.
    if len(parsed_matches) == 1:
        parsed = parsed_matches[0]
        return ItunesTVSeasonMatch(
            confidence=MatchConfidence.PARSED,
            result=parsed.result,
            artwork_url=parsed.artwork_url,
        )
    if len(parsed_matches) > 1:
        return ItunesTVSeasonMatch(
            confidence=MatchConfidence.AMBIGUOUS,
            result=None,
            artwork_url=None,
        )

    return ItunesTVSeasonMatch(confidence=MatchConfidence.NONE, result=None, artwork_url=None)


def resolve_itunes_tv_season_artwork(
    *,
    config: AppConfig,
    provider_kind: str,
    series_id: int | str | None,
    series_title: str | None,
    season_number: int | None,
    fallback_artwork: str | Path | None,
    staging_artwork_path: Path,
    dry_run: bool = False,
    download_artwork: ArtworkDownloadHook | None = None,
    normalize_artwork: ArtworkNormalizeHook | None = None,
    http_get: ItunesSearchGetter | None = None,
    cancellation_token: Any | None = None,
) -> ItunesArtworkResolution:
    """Resolve Sonarr TV season artwork through iTunes with provider fallback.

    All lookup, matching, download, and normalization failures are non-fatal:
    callers receive ``fallback_artwork`` and a logged reason instead of a tagging
    or conversion failure.
    """

    provider_config = config.artwork.providers.get(ITUNES_TV_SEASON_PROVIDER)
    fallback = ItunesArtworkResolution(
        artwork=fallback_artwork,
        source="fallback",
        reason="fallback",
    )

    skip_reason = _itunes_skip_reason(
        config=config,
        provider_kind=provider_kind,
        series_id=series_id,
        series_title=series_title,
        season_number=season_number,
    )
    if skip_reason is not None:
        LOGGER.info("iTunes artwork fallback: %s", skip_reason)
        return ItunesArtworkResolution(
            artwork=fallback_artwork,
            source="fallback",
            reason=skip_reason,
        )

    assert provider_config is not None
    assert series_id is not None
    assert series_title is not None
    assert season_number is not None

    if dry_run:
        LOGGER.info(
            "iTunes artwork fallback: dry-run would query iTunes before using fallback for sonarr series=%s season=%s",
            series_id,
            season_number,
        )
        return ItunesArtworkResolution(
            artwork=fallback_artwork,
            source="fallback",
            reason="dry-run",
        )

    if download_artwork is None or normalize_artwork is None:
        LOGGER.info("iTunes artwork fallback: download/normalize hooks missing")
        return fallback

    getter = http_get or requests.get
    terms = (f"{series_title} season {season_number}", series_title)
    for term in terms:
        try:
            results = _query_itunes_tv_seasons(
                http_get=getter,
                term=term,
                country=provider_config.country,
                timeout_seconds=provider_config.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - lookup failures must be non-fatal.
            LOGGER.info("iTunes artwork fallback: lookup failed for %r: %s", term, exc)
            continue

        match = match_itunes_tv_season_result(
            results=results,
            requested_artist=series_title,
            requested_title=series_title,
            requested_season=season_number,
            minimum_confidence=provider_config.minimum_confidence,
        )
        if match.confidence == MatchConfidence.AMBIGUOUS:
            LOGGER.info("iTunes artwork fallback: ambiguous match for %r", term)
            continue
        if match.confidence == MatchConfidence.NONE or match.result is None:
            LOGGER.info("iTunes artwork fallback: no match for %r", term)
            continue

        original_url = _original_artwork_url(match.result)
        candidate_urls = _candidate_artwork_urls(match.artwork_url, original_url)
        for artwork_url in candidate_urls:
            if _download_and_normalize_artwork(
                artwork_url=artwork_url,
                artwork_path=staging_artwork_path,
                download_artwork=download_artwork,
                normalize_artwork=normalize_artwork,
                cancellation_token=cancellation_token,
            ):
                LOGGER.info(
                    "iTunes artwork resolved: confidence=%s term=%r artwork=%s",
                    match.confidence.value,
                    term,
                    staging_artwork_path,
                )
                return ItunesArtworkResolution(
                    artwork=staging_artwork_path,
                    source="itunes",
                    reason="matched",
                    confidence=match.confidence,
                )
            LOGGER.info("iTunes artwork fallback: download failed for %s", artwork_url)

    LOGGER.info("iTunes artwork fallback: using provider fallback artwork")
    return fallback


def _itunes_skip_reason(
    *,
    config: AppConfig,
    provider_kind: str,
    series_id: int | str | None,
    series_title: str | None,
    season_number: int | None,
) -> str | None:
    provider_config = config.artwork.providers.get(ITUNES_TV_SEASON_PROVIDER)
    if not config.artwork.enabled:
        return "global artwork disabled"
    if provider_config is None:
        return "itunes provider missing"
    if not provider_config.enabled:
        return "itunes provider disabled"
    if "tv" not in provider_config.apply_to:
        return "itunes provider does not apply to tv"
    if provider_kind != "sonarr":
        return "provider is not sonarr"
    if series_id is None:
        return "missing sonarr series id"
    if series_title is None or not series_title.strip():
        return "missing series title"
    if season_number is None:
        return "missing season number"
    if season_number == 0:
        return "season zero uses provider fallback"
    if season_number < 0:
        return "invalid season number"
    return None


def _is_valid_artwork_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _query_itunes_tv_seasons(
    *,
    http_get: ItunesSearchGetter,
    term: str,
    country: str,
    timeout_seconds: int,
) -> list[Mapping[str, Any]]:
    params = {
        "term": term,
        "media": "tvShow",
        "entity": "tvSeason",
        "country": country,
        "limit": 50,
    }
    try:
        response = http_get(ITUNES_SEARCH_URL, params=params, timeout=timeout_seconds)
    except TypeError:
        response = http_get(f"{ITUNES_SEARCH_URL}?{urlencode(params)}")

    if isinstance(response, Mapping):
        payload = cast(Mapping[str, object], response)
    else:
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            _ = raise_for_status()
        json_method = getattr(response, "json", None)
        if not callable(json_method):
            raise TypeError("iTunes response does not provide JSON")
        payload_raw = json_method()
        if not isinstance(payload_raw, Mapping):
            raise TypeError("iTunes response JSON must be a mapping")
        payload = cast(Mapping[str, object], payload_raw)

    results = payload.get("results", [])
    if not isinstance(results, list):
        raise TypeError("iTunes results must be a list")
    return [item for item in results if isinstance(item, Mapping)]


def _original_artwork_url(result: Mapping[str, Any]) -> str | None:
    artwork = result.get("artworkUrl100")
    if isinstance(artwork, str) and artwork:
        return artwork
    return None


def _candidate_artwork_urls(
    upscaled_url: str | None,
    original_url: str | None,
) -> tuple[str, ...]:
    urls: list[str] = []
    for url in (upscaled_url, original_url):
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


def _download_and_normalize_artwork(
    *,
    artwork_url: str,
    artwork_path: Path,
    download_artwork: ArtworkDownloadHook,
    normalize_artwork: ArtworkNormalizeHook,
    cancellation_token: Any | None,
) -> bool:
    raw_path = artwork_path.with_suffix(".download")
    normalized_temp_path = artwork_path.with_name(f"{artwork_path.stem}.normalized{artwork_path.suffix}")
    legacy_normalized_temp_path = artwork_path.with_suffix(".normalized")
    try:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        download_artwork(artwork_url, raw_path)
        normalize_artwork(raw_path, normalized_temp_path, cancellation_token)
        if not _is_valid_artwork_file(normalized_temp_path):
            return False
        normalized_temp_path.replace(artwork_path)
        return _is_valid_artwork_file(artwork_path)
    except Exception as exc:  # noqa: BLE001 - artwork failures are non-fatal.
        LOGGER.info("iTunes artwork attempt failed: %s", exc)
        return False
    finally:
        try:
            raw_path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.info("iTunes artwork raw temp cleanup failed for %s: %s", raw_path, exc)
        try:
            normalized_temp_path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.info(
                "iTunes artwork normalized temp cleanup failed for %s: %s",
                normalized_temp_path,
                exc,
            )
        try:
            legacy_normalized_temp_path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.info(
                "iTunes artwork legacy normalized temp cleanup failed for %s: %s",
                legacy_normalized_temp_path,
                exc,
            )
