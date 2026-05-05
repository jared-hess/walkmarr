from pathlib import Path

import pytest
import yaml

from walkmarr.config import bootstrap_config, default_bootstrap_payload
from walkmarr.exceptions import ConfigError


def test_bootstrap_writes_config(tmp_path: Path) -> None:
    target = tmp_path / "walkmarr" / "config.yml"
    payload = default_bootstrap_payload()

    written = bootstrap_config(target, payload=payload, force=False)
    assert target in written
    parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert parsed["providers"]["sonarr"]["url"] == "http://localhost:8989"


def test_bootstrap_refuses_overwrite_without_force(tmp_path: Path) -> None:
    target = tmp_path / "config.yml"
    target.write_text("x: y\n", encoding="utf-8")
    payload = default_bootstrap_payload()

    with pytest.raises(ConfigError, match="already exists"):
        bootstrap_config(target, payload=payload, force=False)
