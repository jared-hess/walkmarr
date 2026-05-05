"""Audio stream normalization and selection helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioStreamInfo:
    """Normalized ffprobe audio stream metadata."""

    index: int
    codec_name: str | None
    channels: int | None
    channel_layout: str | None
    language_raw: str | None
    language_normalized: str | None
    title: str | None
    is_default: bool


def normalize_language_tag(language: str | None) -> str | None:
    """Normalize language tags into canonical values.

    Canonical mappings currently include English, Portuguese, and Japanese.
    """
    if language is None:
        return None

    normalized = language.strip().casefold().replace("_", "-")
    if not normalized:
        return None

    aliases = {
        "en": "eng",
        "eng": "eng",
        "en-us": "eng",
        "en-gb": "eng",
        "pt": "por",
        "por": "por",
        "pt-br": "por",
        "pt-pt": "por",
        "ja": "jpn",
        "jp": "jpn",
        "jpn": "jpn",
    }

    if normalized in aliases:
        return aliases[normalized]

    prefix = normalized.split("-", maxsplit=1)[0]
    if prefix in aliases:
        return aliases[prefix]
    return normalized


def select_audio_stream(
    audio_streams: list[AudioStreamInfo],
    preferred_languages: list[str],
) -> AudioStreamInfo | None:
    """Select best audio stream according to language and quality rules."""
    if not audio_streams:
        return None

    normalized_preferences = [
        value
        for value in (normalize_language_tag(language) for language in preferred_languages)
        if value is not None
    ]

    ranked = sorted(
        audio_streams,
        key=lambda stream: _audio_rank(stream, normalized_preferences),
    )
    return ranked[0]


def _audio_rank(
    stream: AudioStreamInfo,
    normalized_preferences: list[str],
) -> tuple[int, int, int, int, int]:
    preferred_score = _preferred_language_score(stream.language_normalized, normalized_preferences)
    bad_variant_score = 1 if _is_bad_audio_variant(stream.title) else 0
    default_score = 0 if stream.is_default else 1
    channel_score = -stream.channels if stream.channels is not None else 0
    index_score = stream.index
    return (preferred_score, bad_variant_score, default_score, channel_score, index_score)


def _preferred_language_score(language: str | None, preferences: list[str]) -> int:
    if language is None:
        return len(preferences) + 1
    try:
        return preferences.index(language)
    except ValueError:
        return len(preferences) + 1


def _is_bad_audio_variant(title: str | None) -> bool:
    if title is None:
        return False
    text = title.casefold()
    bad_terms = (
        "commentary",
        "comment",
        "director",
        "descriptive",
        "description",
        "audio description",
        "visually impaired",
    )
    return any(term in text for term in bad_terms)
