from walkmarr.convert.audio_select import AudioStreamInfo, normalize_language_tag, select_audio_stream


def _stream(
    *,
    index: int,
    language_raw: str | None,
    title: str | None = None,
    is_default: bool = False,
    channels: int | None = 2,
) -> AudioStreamInfo:
    return AudioStreamInfo(
        index=index,
        codec_name="aac",
        channels=channels,
        channel_layout="stereo",
        language_raw=language_raw,
        language_normalized=normalize_language_tag(language_raw),
        title=title,
        is_default=is_default,
    )


def test_normalize_language_aliases() -> None:
    assert normalize_language_tag("en") == "eng"
    assert normalize_language_tag("eng") == "eng"
    assert normalize_language_tag("en-US") == "eng"
    assert normalize_language_tag("en_GB") == "eng"
    assert normalize_language_tag("pt") == "por"
    assert normalize_language_tag("pt-BR") == "por"
    assert normalize_language_tag("ja") == "jpn"
    assert normalize_language_tag("jp") == "jpn"


def test_selects_english_when_not_first_or_default() -> None:
    streams = [
        _stream(index=1, language_raw="por", is_default=True),
        _stream(index=2, language_raw="eng", is_default=False),
    ]
    selected = select_audio_stream(streams, preferred_languages=["eng"])
    assert selected is not None
    assert selected.index == 2


def test_demotes_commentary_english_below_normal_english() -> None:
    streams = [
        _stream(index=1, language_raw="eng", title="Director Commentary", is_default=True),
        _stream(index=2, language_raw="eng", title="Main Audio", is_default=False),
    ]
    selected = select_audio_stream(streams, preferred_languages=["eng"])
    assert selected is not None
    assert selected.index == 2


def test_default_breaks_ties_between_equal_streams() -> None:
    streams = [
        _stream(index=1, language_raw="eng", title="Main", is_default=False),
        _stream(index=2, language_raw="eng", title="Main", is_default=True),
    ]
    selected = select_audio_stream(streams, preferred_languages=["eng"])
    assert selected is not None
    assert selected.index == 2


def test_higher_channel_count_breaks_ties_after_default_and_variant() -> None:
    streams = [
        _stream(index=1, language_raw="eng", title="Main", is_default=False, channels=2),
        _stream(index=2, language_raw="eng", title="Main", is_default=False, channels=6),
    ]
    selected = select_audio_stream(streams, preferred_languages=["eng"])
    assert selected is not None
    assert selected.index == 2


def test_fallback_when_preferred_language_missing() -> None:
    streams = [
        _stream(index=1, language_raw="por", title="Director Commentary", is_default=False, channels=2),
        _stream(index=2, language_raw="jpn", title="Main", is_default=True, channels=2),
        _stream(index=3, language_raw="por", title="Main", is_default=False, channels=6),
    ]
    selected = select_audio_stream(streams, preferred_languages=["eng"])
    assert selected is not None
    assert selected.index == 2


def test_returns_none_when_no_audio_streams() -> None:
    selected = select_audio_stream([], preferred_languages=["eng"])
    assert selected is None
