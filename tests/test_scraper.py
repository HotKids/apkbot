from unittest.mock import Mock
import json
import time
import pytest
import requests

import scraper
from galaxy_store import (
    AppRequest,
    DownloadError,
    DownloadGrant,
    InvalidResponse,
    LoginRequired,
    NoAvailableVersion,
    ServiceError,
    StubRestricted,
    TransportError,
    VersionDrift,
)
from tests.test_galaxy_store import grant, metadata, ods, release, stub


class Response:
    def __init__(self, data=b"", status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}
        self.closed = False

    def iter_content(self, size):
        yield self.data

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def session(*responses):
    return Mock(request=Mock(side_effect=list(responses)))


@pytest.mark.parametrize(
    "url",
    [
        "http://download.samsungapps.com/a",
        "https://evil.test/a",
        "https://samsungapps.com.evil.test/a",
        "https://evil-samsungapps.com/a",
        "https://user:pass@download.samsungapps.com/a",
        "https://download.samsungapps.com:8443/a",
        "https://download.samsungapps.com/a#frag",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://download.samsungapps.com/evil\npath",
    ],
)
def test_reject_untrusted_urls(url):
    with pytest.raises(InvalidResponse):
        scraper.validate_url(url)


def test_get_redirect_is_validated_before_next_request():
    first = Response(status=302, headers={"Location": "https://evil.test/private"})
    s = session(first)
    with pytest.raises(InvalidResponse):
        scraper.request_bytes(s, "GET", scraper.STUB_URL)
    assert s.request.call_count == 1 and first.closed


def test_post_redirect_is_never_replayed():
    s = session(Response(status=307, headers={"Location": scraper.ODS_URL}))
    with pytest.raises(TransportError):
        scraper.request_bytes(s, "POST", scraper.ODS_URL, data=b"xml")
    assert s.request.call_count == 1


def test_known_get_redirect_and_tls_timeout_options():
    s = session(
        Response(status=302, headers={"Location": scraper.ODS_URL}), Response(b"ok")
    )
    assert scraper.request_bytes(s, "GET", scraper.STUB_URL) == b"ok"
    kwargs = s.request.call_args.kwargs
    assert kwargs["stream"] and kwargs["allow_redirects"] is False
    assert kwargs["timeout"][0] <= 15
    assert kwargs.get("verify", True) is True


@pytest.mark.parametrize("status", [403, 404, 500])
def test_http_failure_is_never_absence(status):
    with pytest.raises(TransportError):
        scraper.request_bytes(session(Response(status=status)), "GET", scraper.STUB_URL)


def test_metadata_size_and_deadline_bounds():
    with pytest.raises(DownloadError):
        scraper.request_bytes(
            session(Response(b"x" * 2_000_001)), "GET", scraper.STUB_URL
        )
    s = session()
    with pytest.raises(TransportError):
        scraper.open_response(s, "GET", scraper.STUB_URL, deadline=time.monotonic() - 1)
    s.request.assert_not_called()


def test_network_error_text_does_not_leak_signed_url():
    s = session(
        requests.ConnectionError("https://download.samsungapps.com/a?secret=leak")
    )
    with pytest.raises(TransportError) as caught:
        scraper.request_bytes(s, "GET", scraper.STUB_URL)
    assert "secret" not in str(caught.value)


def test_cn_metadata_does_not_authorize_download():
    s = session(Response(metadata()))
    store = scraper.GalaxyStore(s)
    assert store.metadata(AppRequest("com.example.app", "CN")).version_name == "01.02.3"
    assert s.request.call_count == 1
    assert "reqId=2298" in s.request.call_args.args[1]


@pytest.mark.parametrize(
    "failure",
    [StubRestricted("restricted"), ServiceError("unknown"), TransportError("network")],
)
@pytest.mark.parametrize("region", ["AUTO", "US"])
def test_auto_falls_back_after_us_query_error_but_explicit_us_does_not(
    monkeypatch, failure, region
):
    store = scraper.GalaxyStore(Mock())
    monkeypatch.setattr(store, "stub", Mock(side_effect=failure))
    post = Mock(return_value=metadata())
    monkeypatch.setattr(store, "_ods", post)
    if region == "AUTO":
        assert store.metadata(AppRequest("com.example.app")).region == "CN"
        post.assert_called_once()
    else:
        with pytest.raises(type(failure)):
            store.metadata(AppRequest("com.example.app", "US"))
        post.assert_not_called()


def test_auto_prefers_us_and_falls_back_on_absence(monkeypatch):
    store = scraper.GalaxyStore(Mock())
    stub = Mock(return_value=DownloadGrant(release(region="US"), "unused", 42))
    monkeypatch.setattr(store, "stub", stub)
    post = Mock(return_value=metadata())
    monkeypatch.setattr(store, "_ods", post)
    assert store.metadata(AppRequest("com.example.app")).region == "US"
    post.assert_not_called()
    stub.side_effect = NoAvailableVersion("region")
    assert store.metadata(AppRequest("com.example.app")).region == "CN"
    post.reset_mock()
    with pytest.raises(NoAvailableVersion):
        store.metadata(AppRequest("com.example.app", "US"))
    post.assert_not_called()


@pytest.mark.parametrize("region", ["AUTO", "US"])
def test_download_link_retries_cn_when_us_authorization_fails(region):
    url = "https://download.samsungapps.com/cn.apk"
    http = session(
        Response(stub(downloadURI="https://evil.test/file.apk")),
        Response(metadata()),
        Response(
            ods(
                dict(
                    productID="00001",
                    version="01.02.3",
                    versionCode="123",
                    binaryArch="32n64",
                    contentsSize=42,
                    downLoadURI=url,
                )
            )
        ),
    )
    store = scraper.GalaxyStore(http)
    if region == "US":
        with pytest.raises(InvalidResponse):
            store.download_link(AppRequest("com.example.app", region))
        assert http.request.call_count == 1
    else:
        grant = store.download_link(AppRequest("com.example.app"))
        assert grant.release.region == "CN" and grant.url == url
        assert http.request.call_count == 3
        assert "reqId=2298" in http.request.call_args_list[1].args[1]
        assert "reqId=2311" in http.request.call_args_list[2].args[1]


def test_download_link_reuses_the_us_stub_answer():
    http = session(Response(stub()))
    grant = scraper.GalaxyStore(http).download_link(AppRequest("com.example.app"))
    assert grant.release.region == "US"
    assert grant.url == "https://download.samsungapps.com/file.apk?secret=private"
    assert http.request.call_count == 1
    assert http.request.call_args.args[0] == "GET"


def test_stub_authorization_requeries_a_release_it_did_not_just_see():
    selected = scraper.parse_stub(stub(), "com.example.app", "US").release
    http = session(Response(stub()), Response(stub(versionCode="124")))
    store = scraper.GalaxyStore(http)
    assert store.authorize(selected).release == selected
    assert store.authorize(selected).release == selected
    assert http.request.call_count == 1
    store._stub_grant = None
    with pytest.raises(VersionDrift):
        store.authorize(selected)
    assert http.request.call_count == 2


def test_download_link_does_not_loop_when_both_regions_fail():
    http = session(
        Response(stub("Service error", "0")),
        Response(metadata()),
        Response(ods(error="Service error", code="-1")),
        Response(ods(error="Service error", code="-1")),
    )
    with pytest.raises(ServiceError):
        scraper.GalaxyStore(http).download_link(AppRequest("com.example.app"))
    assert http.request.call_count == 4


def test_cn_download_link_uses_metadata_then_stateless_full_package_authorization():
    http = session(Response(metadata()), Response(grant()))
    selected = scraper.GalaxyStore(http).download_link(AppRequest("com.example.app", "CN"))
    assert selected.release == release()
    assert selected.size == 42
    assert selected.url == "https://download.samsungapps.com/full.apk"
    assert http.request.call_count == 2
    assert all(call.args[0] == "POST" for call in http.request.call_args_list)
    assert "reqId=2298" in http.request.call_args_list[0].args[1]
    assert "reqId=2311" in http.request.call_args_list[1].args[1]


@pytest.mark.parametrize("status", [400, 403, 500])
def test_cn_authorization_uses_restore_after_primary_http_rejection(status):
    http = session(
        Response(status=status),
        Response(grant(version=None, versionCode=None)),
    )
    assert scraper.GalaxyStore(http).authorize(release()).release == release()
    assert http.request.call_count == 2
    assert "reqId=2311" in http.request.call_args_list[0].args[1]
    assert "reqId=2316" in http.request.call_args_list[1].args[1]


@pytest.mark.parametrize("error", ["Service error", "Login required"])
def test_cn_authorization_uses_restore_after_primary_api_rejection(error):
    http = session(
        Response(ods(error=error, code="-1")),
        Response(grant(version=None, versionCode=None)),
    )
    assert scraper.GalaxyStore(http).authorize(release()).release == release()
    assert http.request.call_count == 2
    assert "reqId=2311" in http.request.call_args_list[0].args[1]
    assert "reqId=2316" in http.request.call_args_list[1].args[1]


@pytest.mark.parametrize(
    "payload, exception",
    [
        (b"malformed", InvalidResponse),
        (b"<SamsungProtocol/>", InvalidResponse),
        (ods(error="Service error", code="invalid"), InvalidResponse),
        (grant(productID="00002"), InvalidResponse),
        (grant(GUID="com.other.app"), InvalidResponse),
        (grant(version=None), InvalidResponse),
        (grant(versionCode=None), InvalidResponse),
        (grant(version="2.0"), VersionDrift),
        (grant(versionCode="124"), VersionDrift),
        (grant(contentsSize="43"), VersionDrift),
        (grant(contentsSize="0"), InvalidResponse),
        (grant(downLoadURI=None), InvalidResponse),
        (grant(downLoadURI="https://evil.test/private"), InvalidResponse),
        (grant(downLoadURI="https://download.samsungapps.com/"), InvalidResponse),
    ],
)
def test_cn_primary_invalid_grant_does_not_retry_restore(payload, exception):
    http = session(Response(payload))
    with pytest.raises(exception):
        scraper.GalaxyStore(http).authorize(release())
    assert http.request.call_count == 1
    assert "reqId=2311" in http.request.call_args.args[1]


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("synthetic"),
        Response(status=307, headers={"Location": scraper.ODS_URL}),
        Response(status=304),
        Response(status=204),
    ],
)
def test_cn_primary_network_or_redirect_failure_does_not_retry_restore(failure):
    http = session(failure)
    with pytest.raises(TransportError):
        scraper.GalaxyStore(http).authorize(release())
    assert http.request.call_count == 1


@pytest.mark.parametrize("payload", [grant(version="2.0"), grant(versionCode="124")])
def test_cn_restore_present_versions_must_match_selected_release(payload):
    http = session(Response(status=403), Response(payload))
    with pytest.raises(VersionDrift):
        scraper.GalaxyStore(http).authorize(release())
    assert http.request.call_count == 2


def test_cn_authorization_does_not_loop_after_restore_rejection():
    http = session(
        Response(ods(error="Service error", code="-1")),
        Response(ods(error="Login required", code="-1")),
    )
    with pytest.raises(LoginRequired):
        scraper.GalaxyStore(http).authorize(release())
    assert http.request.call_count == 2


def test_login_and_installability_block_authorization(monkeypatch):
    store = scraper.GalaxyStore(Mock())
    post = Mock()
    monkeypatch.setattr(store, "_ods", post)
    with pytest.raises(LoginRequired):
        store.authorize(release(needs_login=True))
    with pytest.raises(ServiceError):
        store.authorize(release(installable=False))
    post.assert_not_called()


def test_details_json_binding_and_ambiguity():
    detail = dict(
        countryCode="CHN", contentBinaryVersion="01.02.3", contentNewDescription="fixed"
    )
    doc = json.dumps(dict(appId="com.example.app", DetailMain=detail)).encode()
    assert scraper.details_from_response(doc, release()) == (release(), "fixed")
    assert scraper.details_from_response(
        b'{"appId":"com.example.app","appId":"other"}', release()
    ) == (release(), None)
    other = release(version_name="1.2.3")
    assert scraper.details_from_response(doc, other) == (other, None)
    assert scraper.details_from_response(b"<html>failure</html>", release()) == (
        release(),
        None,
    )


def test_website_unavailable_keeps_download_metadata():
    store = scraper.GalaxyStore(session(Response(b"Unavailable", status=503)))
    assert store.details(release()) == (release(), None)
