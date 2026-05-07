from __future__ import annotations

import logging
from pathlib import Path

from walkmarr.logging_config import configure_file_logging


def test_configure_file_logging_uses_env_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "logs" / "walkmarr.log"
    monkeypatch.setenv("WALKMARR_LOG_PATH", str(log_path))

    configured = configure_file_logging(verbose=True)
    assert configured == log_path

    logger = logging.getLogger("walkmarr")
    logger.info("hello from test")

    content = log_path.read_text(encoding="utf-8")
    assert "Logging initialized" in content
    assert "hello from test" in content
