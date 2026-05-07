from pathlib import Path

import pytest

from walkmarr.convert.video import validate_encoded_output
from walkmarr.exceptions import ConversionError


def _write_file(path: Path, size: int = 1024) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_validate_output_passes_for_equal_duration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mpg"
    output = tmp_path / "output.tmp.mp4"
    _write_file(source, size=1024)
    _write_file(output, size=5000)

    durations = {source: 1313.50, output: 1313.50}
    monkeypatch.setattr(
        "walkmarr.convert.video.probe_duration_seconds",
        lambda path, ffprobe_bin="ffprobe": durations[path],
    )

    result = validate_encoded_output(source, output, min_size_bytes=1)
    assert result.source_duration_seconds == 1313.50
    assert result.output_duration_seconds == 1313.50


def test_validate_output_passes_for_small_shortfall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mpg"
    output = tmp_path / "output.tmp.mp4"
    _write_file(source)
    _write_file(output, size=5000)

    durations = {source: 1313.50, output: 1313.47}
    monkeypatch.setattr(
        "walkmarr.convert.video.probe_duration_seconds",
        lambda path, ffprobe_bin="ffprobe": durations[path],
    )

    validate_encoded_output(source, output, min_size_bytes=1)


def test_validate_output_rejects_truncated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mpg"
    output = tmp_path / "output.tmp.mp4"
    _write_file(source)
    _write_file(output, size=5000)

    durations = {source: 1313.50, output: 474.91}
    monkeypatch.setattr(
        "walkmarr.convert.video.probe_duration_seconds",
        lambda path, ffprobe_bin="ffprobe": durations[path],
    )

    with pytest.raises(ConversionError, match="Output appears truncated"):
        validate_encoded_output(source, output, min_size_bytes=1)


def test_validate_output_rejects_missing_output(tmp_path: Path) -> None:
    source = tmp_path / "source.mpg"
    _write_file(source)
    output = tmp_path / "missing.tmp.mp4"

    with pytest.raises(ConversionError, match="Output file was not created"):
        validate_encoded_output(source, output)


def test_validate_output_rejects_too_small(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mpg"
    output = tmp_path / "output.tmp.mp4"
    _write_file(source)
    _write_file(output, size=100)

    monkeypatch.setattr(
        "walkmarr.convert.video.probe_duration_seconds",
        lambda path, ffprobe_bin="ffprobe": 10.0,
    )

    with pytest.raises(ConversionError, match="suspiciously small"):
        validate_encoded_output(source, output)


def test_validate_output_allows_short_clip_under_default_min_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mov"
    output = tmp_path / "output.tmp.mp4"
    _write_file(source)
    _write_file(output, size=701_849)

    durations = {source: 26.27, output: 26.20}
    monkeypatch.setattr(
        "walkmarr.convert.video.probe_duration_seconds",
        lambda path, ffprobe_bin="ffprobe": durations[path],
    )

    result = validate_encoded_output(source, output)
    assert result.output_size_bytes == 701_849
    assert result.minimum_size_bytes == 210_160


def test_validate_output_rejects_unreadable_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mpg"
    output = tmp_path / "output.tmp.mp4"
    _write_file(source)
    _write_file(output, size=5000)

    def _raise_probe(path: Path, ffprobe_bin: str = "ffprobe") -> float:
        del path, ffprobe_bin
        raise ConversionError("ffprobe failed")

    monkeypatch.setattr("walkmarr.convert.video.probe_duration_seconds", _raise_probe)

    with pytest.raises(ConversionError, match="ffprobe failed"):
        validate_encoded_output(source, output, min_size_bytes=1)


def test_validate_output_rejects_suspiciously_long(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mpg"
    output = tmp_path / "output.tmp.mp4"
    _write_file(source)
    _write_file(output, size=5000)

    durations = {source: 1300.0, output: 1400.0}
    monkeypatch.setattr(
        "walkmarr.convert.video.probe_duration_seconds",
        lambda path, ffprobe_bin="ffprobe": durations[path],
    )

    with pytest.raises(ConversionError, match="suspiciously longer"):
        validate_encoded_output(source, output, min_size_bytes=1)
