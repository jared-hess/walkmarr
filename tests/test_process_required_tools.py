from __future__ import annotations

from types import SimpleNamespace

import walkmarr.process as process
import walkmarr.tag.mp4 as mp4

import pytest

from walkmarr.exceptions import ConversionError


def test_ensure_required_tools_raises_clear_fdkaac_message(monkeypatch) -> None:
    monkeypatch.setattr(process, "require_binary", lambda binary_name: None)
    monkeypatch.setattr(
        process,
        "shutil",
        SimpleNamespace(which=lambda binary_name: None if binary_name == "fdkaac" else f"/usr/bin/{binary_name}"),
    )
    monkeypatch.setattr(mp4, "shutil", SimpleNamespace(which=lambda name: f"/usr/bin/{name}"))

    expected = "fdkaac is required for the default Linux iPod encode path.\n\nInstall it with:\n  sudo apt install fdkaac"

    with pytest.raises(ConversionError, match=expected):
        process.ensure_required_tools()


def test_ensure_required_tools_returns_atomicparsley_when_all_tools_exist(monkeypatch) -> None:
    monkeypatch.setattr(process, "require_binary", lambda binary_name: None)
    monkeypatch.setattr(process, "shutil", SimpleNamespace(which=lambda binary_name: f"/usr/bin/{binary_name}"))
    monkeypatch.setattr(
        mp4,
        "shutil",
        SimpleNamespace(which=lambda name: "AtomicParsley" if name == "AtomicParsley" else None),
    )

    assert process.ensure_required_tools() == "AtomicParsley"
