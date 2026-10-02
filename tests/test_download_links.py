from http.client import HTTPConnection
from urllib.parse import urlsplit
from unittest.mock import Mock
import logging

import pytest
import requests

import config
import database as db
import download_links as links
from galaxy_store import AppRequest, DownloadGrant, ServiceError, VersionDrift
from tests.test_galaxy_store import release, metadata, ods

APP = AppRequest("com.example.app", "CN")
CDN = "https://download.samsungapps.com/file.apk?token=private-link&name=a%2Fb"


@pytest.fixture
def store(monkeypatch):
    source = Mock()
    source.metadata.return_value = release()
    source.authorize.return_value = DownloadGrant(release(), CDN, 42)
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=source)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(links, "GalaxyStore", factory)
    return source


@pytest.fixture
def request_entry(monkeypatch):
    # A real HTTP listener and client, with all external source I/O mocked.
    monkeypatch.setattr(config, "DOWNLOAD_BIND_PORT", 0)
    with links.running_download_server() as server:

        def request(path, method="GET"):
            client = HTTPConnection(*server.server_address, timeout=3)
            try:
                client.request(method, path)
                response = client.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                client.close()

        yield request


def test_repeated_clicks_on_same_url_get_fresh_redirect_without_apk_bytes(
    store, request_entry, caplog, tmp_path
):
    caplog.set_level(logging.DEBUG)
    url = links.download_url(APP, 100)
    path = urlsplit(url).path
    newer = release(version_name="01.02.4", version_code=124)
    fresh = CDN.replace("private-link", "refreshed-link")
    store.metadata.side_effect = [release(), newer]
    store.authorize.side_effect = [
        DownloadGrant(release(), CDN, 42),
        DownloadGrant(newer, fresh, 42),
    ]
    for expected in (CDN, fresh):
        status, headers, body = request_entry(path)
        assert status == 302 and headers["Location"] == expected and body == b""
        assert (
            "no-store" in headers["Cache-Control"] and headers["Pragma"] == "no-cache"
        )
        assert headers["Referrer-Policy"] == "no-referrer"
    assert [call.args[0] for call in store.metadata.call_args_list] == [APP, APP]
    assert [call.args[0] for call in store.authorize.call_args_list] == [
        release(),
        newer,
    ]
    store.download.assert_not_called()
    store.notes.assert_not_called()
    assert set(tmp_path.iterdir()) == {db.DB_PATH}
    assert "private-link" not in db.DB_PATH.read_bytes().decode(errors="ignore")
    assert "refreshed-link" not in db.DB_PATH.read_bytes().decode(errors="ignore")
    assert path not in caplog.text and "private-link" not in caplog.text
    assert config.BOT_TOKEN not in url


@pytest.mark.parametrize("change", ["signature", "user", "app", "query", "path"])
def test_tampered_or_unknown_paths_never_query_source(store, request_entry, change):
    path = urlsplit(links.download_url(APP, 100)).path
    if change == "signature":
        path = path[:-1] + ("0" if path[-1] != "0" else "1")
    if change == "user":
        path = path.replace("/100/", "/200/")
    if change == "app":
        path = path.replace(APP.key, AppRequest(APP.package, "US").key)
    if change == "query":
        path += "?next=https://evil.test"
    if change == "path":
        path = "/d/not-a-link"
    status, headers, _ = request_entry(path)
    assert status == 404 and "Location" not in headers
    store.metadata.assert_not_called()


def test_whitelist_revocation_and_token_rotation_revoke_old_buttons(
    store, request_entry, monkeypatch
):
    db.add_to_whitelist(200)
    path = urlsplit(links.download_url(APP, 200)).path
    assert request_entry(path)[0] == 302
    store.reset_mock()
    db.remove_from_whitelist(200)
    assert request_entry(path)[0] == 403
    store.metadata.assert_not_called()
    db.add_to_whitelist(200)
    monkeypatch.setattr(config, "BOT_TOKEN", "654321:rotated-test-token")
    assert request_entry(path)[0] == 404
    store.metadata.assert_not_called()


@pytest.mark.parametrize("method", ["HEAD", "POST"])
def test_non_get_requests_do_not_authorize(store, request_entry, method):
    path = urlsplit(links.download_url(APP, 100)).path
    status, headers, body = request_entry(path, method)
    assert status == 405 and headers["Allow"] == "GET" and body == b""
    store.authorize.assert_not_called()
    store.metadata.assert_not_called()


def test_health_probe_is_read_only(store, request_entry):
    assert request_entry("/healthz")[0] == 200
    assert request_entry("/healthz", "HEAD")[0] == 200
    store.metadata.assert_not_called()


def test_busy_server_returns_retryable_error_without_source_request(monkeypatch, store):
    monkeypatch.setattr(config, "DOWNLOAD_BIND_PORT", 0)
    with links.running_download_server() as server:
        for _ in range(8):
            assert server._slots.acquire(blocking=False)
        client = HTTPConnection(*server.server_address, timeout=3)
        try:
            client.request("GET", urlsplit(links.download_url(APP, 100)).path)
            response = client.getresponse()
            assert response.status == 503 and response.getheader("Retry-After") == "5"
            assert response.read() == b""
            store.metadata.assert_not_called()
        finally:
            client.close()
            for _ in range(8):
                server._slots.release()


@pytest.mark.parametrize(
    "failure",
    [
        ServiceError("Source unavailable"),
        VersionDrift("Version changed"),
        requests.Timeout(CDN),
    ],
)
def test_authorization_failure_has_no_redirect_retry_or_secret_leak(
    store, request_entry, caplog, failure
):
    store.authorize.side_effect = failure
    path = urlsplit(links.download_url(APP, 100)).path
    status, headers, body = request_entry(path)
    assert status in (502, 503) and "Location" not in headers
    assert "刷新".encode() in body
    store.authorize.assert_called_once()
    store.download.assert_not_called()
    assert "private-link" not in body.decode() + caplog.text


def test_untrusted_cdn_cannot_be_an_open_redirect(store, request_entry):
    store.authorize.return_value = DownloadGrant(
        release(), "https://evil.test/private-link", 42
    )
    status, headers, body = request_entry(urlsplit(links.download_url(APP, 100)).path)
    assert status == 502 and "Location" not in headers
    assert b"private-link" not in body


def test_real_source_parser_only_calls_metadata_and_authorization(
    monkeypatch, request_entry
):
    from scraper import GalaxyStore
    from tests.test_scraper import Response, session

    grant = ods(
        dict(productID="00001", binaryArch="64", contentsSize=42, downLoadURI=CDN)
    )
    http = session(Response(metadata()), Response(grant))
    monkeypatch.setattr(links, "GalaxyStore", lambda: GalaxyStore(http))
    status, headers, body = request_entry(urlsplit(links.download_url(APP, 100)).path)
    assert status == 302 and headers["Location"] == CDN and body == b""
    assert len(http.request.call_args_list) == 2
    assert "reqId=2298" in http.request.call_args_list[0].args[1]
    assert "reqId=2316" in http.request.call_args_list[1].args[1]


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://dl.example.com",
        "https://user:password@dl.example.com",
        "https://dl.example.com/path",
        "https://dl.example.com?token=x",
        "https://dl.example.com#part",
        "https://dl.example.com:bad",
        "https://dl.example.com\n",
    ],
)
def test_startup_rejects_missing_or_ambiguous_public_origin(monkeypatch, url):
    monkeypatch.setattr(config, "PUBLIC_DOWNLOAD_BASE_URL", url)
    with pytest.raises(RuntimeError, match="PUBLIC_DOWNLOAD_BASE_URL"):
        config.validate_bot_config()
