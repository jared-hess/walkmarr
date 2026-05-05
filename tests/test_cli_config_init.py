from pathlib import Path
from typing import Any

from click.testing import CliRunner
import pytest

from walkmarr.cli import main


def test_config_init_prompt_fails_before_prompting_when_target_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "config.yml"
    target.write_text("existing: true\n", encoding="utf-8")

    def _unexpected_prompt(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        raise AssertionError("prompt should not be called when config exists")

    monkeypatch.setattr("walkmarr.cli.click.prompt", _unexpected_prompt)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "init", "--prompt", "--path", str(target)])

    assert result.exit_code != 0
    assert "Config already exists" in result.output
