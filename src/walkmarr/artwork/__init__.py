"""Artwork helper package."""

from .itunes import (
    MatchConfidence,
    ItunesTVSeasonMatch,
    expected_itunes_collection_name,
    is_special_collection_name,
    match_itunes_tv_season_result,
    normalize_title,
    parse_season_number,
    upscale_itunes_artwork_url,
)

__all__ = [
    "MatchConfidence",
    "ItunesTVSeasonMatch",
    "expected_itunes_collection_name",
    "is_special_collection_name",
    "match_itunes_tv_season_result",
    "normalize_title",
    "parse_season_number",
    "upscale_itunes_artwork_url",
]
