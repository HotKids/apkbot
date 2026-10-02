import json
from unittest.mock import Mock

import pytest

from galaxy_store import DownloadGrant
from scripts import probe_galaxy_store as probe
from tests.test_galaxy_store import release


def test_authorize_probe_reports_no_url_and_never_downloads(monkeypatch, capsys):
    store = Mock()
    store.metadata.return_value = release()
    store.authorize.return_value = DownloadGrant(
        release(), "https://download.samsungapps.com/a.apk?token=private", 42
    )
    store.download_link.return_value = store.authorize.return_value
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=store)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(probe, "GalaxyStore", factory)
    monkeypatch.setattr(
        probe.sys, "argv", ["probe", "com.example.app", "--region", "CN", "--authorize"]
    )
    assert probe.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output)["authorization"] == dict(
        host="download.samsungapps.com", bytes=42, apk_downloaded=False
    )
    assert "private" not in output and "https://" not in output
    store.download_link.assert_called_once()
    store.download.assert_not_called()


def test_probe_rejects_removed_full_download_option(monkeypatch, capsys):
    factory = Mock()
    monkeypatch.setattr(probe, "GalaxyStore", factory)
    monkeypatch.setattr(
        probe.sys,
        "argv",
        ["probe", "com.example.app", "--download-to", "unused"],
    )
    with pytest.raises(SystemExit) as caught:
        probe.main()
    assert caught.value.code == 2
    factory.assert_not_called()
