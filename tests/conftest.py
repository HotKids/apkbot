"""Synthetic configuration and fail-closed outbound network for all tests."""

import os
from pathlib import Path
import sys
import tempfile

import pytest
import requests
from telebot import apihelper

_scratch = tempfile.TemporaryDirectory(prefix="apkdl-tests-")
os.environ["BOT_TOKEN"] = "123456:offline-test-only"
os.environ["OWNER_ID"] = "100"
os.environ["DB_PATH"] = str(Path(_scratch.name) / "bootstrap.db")
os.environ["LOCAL_BOT_API_URL"] = ""
os.environ["PUBLIC_DOWNLOAD_BASE_URL"] = "https://downloads.example.test"
os.environ["DOWNLOAD_BIND_HOST"] = "127.0.0.1"
os.environ["DOWNLOAD_BIND_PORT"] = "8080"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    import database

    def blocked(*args, **kwargs):
        raise AssertionError(
            "Real network/Telegram access is forbidden in offline tests"
        )

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(apihelper, "_make_request", blocked)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()


def pytest_sessionfinish(session, exitstatus):
    _scratch.cleanup()
