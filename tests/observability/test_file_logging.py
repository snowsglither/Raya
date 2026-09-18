"""Tests logging fichier V2 — création, rotation, redaction, pas de conversion mémoire."""

from __future__ import annotations

import logging
import socket
from datetime import date
from pathlib import Path

import pytest

from raya.observability.logger import log, setup_file_logging


@pytest.fixture(autouse=True)
def reset_file_logging():
    """Remet _file_logging_initialized à False avant et après chaque test."""
    import raya.observability.logger as logger_module
    _LOGGER = logging.getLogger("raya")

    original_flag = logger_module._file_logging_initialized
    original_handlers = list(_LOGGER.handlers)

    # Reset BEFORE the test so each test starts with a clean slate
    logger_module._file_logging_initialized = False
    for h in [h for h in _LOGGER.handlers if hasattr(h, 'baseFilename')]:
        _LOGGER.removeHandler(h)
        h.close()

    yield

    # Cleanup AFTER the test — remove any handlers the test added
    for h in [h for h in _LOGGER.handlers if hasattr(h, 'baseFilename')]:
        _LOGGER.removeHandler(h)
        h.close()
    # Restore original state
    logger_module._file_logging_initialized = original_flag
    for h in original_handlers:
        if h not in _LOGGER.handlers:
            _LOGGER.addHandler(h)


def test_log_file_created_on_setup(tmp_path):
    setup_file_logging(tmp_path)
    hostname = socket.gethostname().lower().replace(" ", "_").replace("-", "_")
    expected = tmp_path / f"raya_{date.today().strftime('%Y%m%d')}_{hostname}.log"
    log("info", "test message")
    # File should exist after logging
    assert expected.exists()


def test_log_file_name_includes_machine(tmp_path):
    setup_file_logging(tmp_path)
    hostname = socket.gethostname().lower().replace(" ", "_").replace("-", "_")
    files = list(tmp_path.glob("*.log"))
    assert len(files) == 1
    assert hostname in files[0].name


def test_log_file_name_includes_date(tmp_path):
    setup_file_logging(tmp_path)
    files = list(tmp_path.glob("*.log"))
    assert date.today().strftime("%Y%m%d") in files[0].name


def test_no_file_logging_when_dir_is_none():
    import raya.observability.logger as logger_module
    setup_file_logging(None)
    assert not logger_module._file_logging_initialized


def test_file_logging_idempotent(tmp_path):
    setup_file_logging(tmp_path)
    setup_file_logging(tmp_path)  # second call must not add a second handler
    _LOGGER = logging.getLogger("raya")
    file_handlers = [h for h in _LOGGER.handlers if hasattr(h, 'baseFilename')]
    assert len(file_handlers) <= 1


def test_log_creates_dir_if_missing(tmp_path):
    log_dir = tmp_path / "nested" / "logs"
    assert not log_dir.exists()
    setup_file_logging(log_dir)
    assert log_dir.exists()
