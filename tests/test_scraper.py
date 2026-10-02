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
from tests.test_galaxy_store import metadata, release


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
def test_auto_does_not_fallback_for_restriction_or_error(monkeypatch, failure):
    store = scraper.GalaxyStore(Mock())
    monkeypatch.setattr(store, "stub", Mock(side_effect=failure))
    post = Mock()
    monkeypatch.setattr(store, "_ods", post)
    with pytest.raises(type(failure)):
        store.metadata(AppRequest("com.example.app"))
    post.assert_not_called()


def test_auto_fallback_only_on_explicit_absence_and_us_preference(monkeypatch):
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


def test_login_and_installability_block_authorization(monkeypatch):
    store = scraper.GalaxyStore(Mock())
    post = Mock()
    monkeypatch.setattr(store, "_ods", post)
    with pytest.raises(LoginRequired):
        store.authorize(release(needs_login=True))
    with pytest.raises(ServiceError):
        store.authorize(release(installable=False))
    post.assert_not_called()


def test_grant_download_checks_bytes_and_manifest_then_renames(monkeypatch, tmp_path):
    payload = b"PK-fake-APK"
    r = release(size=len(payload))
    grant = DownloadGrant(r, "https://download.samsungapps.com/a.apk", len(payload))
    monkeypatch.setattr(
        scraper,
        "manifest_identity",
        lambda path: (r.package, r.version_name, r.version_code),
    )
    result = scraper.download_apk(session(Response(payload)), grant, tmp_path)
    assert result.path.read_bytes() == payload
    assert result.size == len(payload) and len(result.sha256) == 64
    assert not list(tmp_path.glob("*.part"))


def test_maximum_package_length_does_not_overflow_filesystem_name(
    monkeypatch, tmp_path
):
    package = "com." + "a" * 251
    assert AppRequest(package, "CN").package == package
    selected = release(package=package, size=3)
    monkeypatch.setattr(
        scraper,
        "manifest_identity",
        lambda path: (package, selected.version_name, selected.version_code),
    )
    grant = DownloadGrant(selected, "https://download.samsungapps.com/a.apk", 3)
    result = scraper.download_apk(session(Response(b"APK")), grant, tmp_path)
    assert len(result.path.name.encode()) <= 255
    assert result.release.package == package and result.path.read_bytes() == b"APK"


@pytest.mark.parametrize(
    "body,headers,identity",
    [
        (b"ab", {}, ("com.example.app", "01.02.3", 123)),
        (b"abcd", {}, ("com.example.app", "01.02.3", 123)),
        (b"abc", {"Content-Length": "4"}, ("com.example.app", "01.02.3", 123)),
        (b"abc", {"Content-Encoding": "gzip"}, ("com.example.app", "01.02.3", 123)),
        (b"abc", {}, ("com.example.app", "01.02.4", 124)),
        (b"abc", {}, ("com.other.app", "01.02.3", 123)),
    ],
)
def test_failed_download_removes_only_its_partial(
    monkeypatch, tmp_path, body, headers, identity
):
    existing = tmp_path / "keep.apk"
    existing.write_bytes(b"keep")
    original_files = set(tmp_path.iterdir())
    monkeypatch.setattr(scraper, "manifest_identity", lambda path: identity)
    grant = DownloadGrant(release(size=3), "https://download.samsungapps.com/a.apk", 3)
    with pytest.raises(DownloadError):
        scraper.download_apk(session(Response(body, headers=headers)), grant, tmp_path)
    assert set(tmp_path.iterdir()) == original_files
    assert existing.read_bytes() == b"keep"


def test_invalid_archive_rejected(tmp_path):
    path = tmp_path / "bad.apk"
    path.write_bytes(b"not a ZIP")
    with pytest.raises(DownloadError):
        scraper.manifest_identity(path)


def test_notes_json_binding_and_ambiguity():
    detail = dict(
        countryCode="CHN", contentBinaryVersion="01.02.3", contentNewDescription="fixed"
    )
    doc = json.dumps(dict(appId="com.example.app", DetailMain=detail)).encode()
    assert scraper.notes_from_response(doc, release()) == "fixed"
    assert (
        scraper.notes_from_response(
            b'{"appId":"com.example.app","appId":"other"}', release()
        )
        is None
    )
    assert scraper.notes_from_response(doc, release(version_name="1.2.3")) is None
    assert scraper.notes_from_response(b"<html>failure</html>", release()) is None


def binary_manifest(
    package="com.example.app", version="01.02.3", code=123, split=False, duplicate=False
):
    """Minimal Android binary XML fixture, serialized using the public chunk layout."""
    import struct

    pack = struct.pack
    values = [
        "manifest",
        "package",
        package,
        "android",
        "http://schemas.android.com/apk/res/android",
        "versionName",
        version,
        "versionCode",
        "split",
        "config.arm64_v8a",
    ]
    strings = b""
    offsets = []
    for value in values:
        encoded = value.encode()
        offsets.append(len(strings))
        strings += bytes([len(value), len(encoded)]) + encoded + b"\0"
    strings += b"\0" * ((-len(strings)) % 4)
    pool_size = 28 + len(values) * 4 + len(strings)
    pool = pack(
        "<HHIIIIII", 1, 28, pool_size, len(values), 0, 0x100, 28 + len(values) * 4, 0
    )
    pool += pack("<" + "I" * len(offsets), *offsets) + strings

    def node(kind, body):
        return pack("<HHIII", kind, 16, 16 + len(body), 1, 0xFFFFFFFF) + body

    namespace = node(0x100, pack("<II", 3, 4))

    def attr(ns, name, raw, kind, value):
        return pack("<IIIHBBI", ns, name, raw, 8, 0, kind, value)

    attrs = (
        attr(0xFFFFFFFF, 1, 2, 3, 2)
        + attr(4, 5, 6, 3, 6)
        + attr(4, 7, 0xFFFFFFFF, 16, code)
    )
    if split:
        attrs += attr(0xFFFFFFFF, 8, 9, 3, 9)
    if duplicate:
        attrs += attr(0xFFFFFFFF, 1, 2, 3, 2)
    start = node(
        0x102,
        pack(
            "<IIHHHHHH", 0xFFFFFFFF, 0, 20, 20, 3 + int(split) + int(duplicate), 0, 0, 0
        )
        + attrs,
    )
    end = node(0x103, pack("<II", 0xFFFFFFFF, 0)) + node(0x101, pack("<II", 3, 4))
    data = pool + namespace + start + end
    return pack("<HHI", 3, 8, 8 + len(data)) + data


def test_actual_binary_manifest_binding(tmp_path):
    import zipfile

    path = tmp_path / "real-layout.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", binary_manifest())
    assert scraper.manifest_identity(path) == ("com.example.app", "01.02.3", 123)


def test_actual_split_manifest_rejected(tmp_path):
    import zipfile

    path = tmp_path / "split.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", binary_manifest(split=True))
    with pytest.raises(DownloadError):
        scraper.manifest_identity(path)


def test_actual_duplicate_manifest_attributes_rejected(tmp_path):
    import zipfile

    path = tmp_path / "ambiguous.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", binary_manifest(duplicate=True))
    with pytest.raises(DownloadError, match="重复属性"):
        scraper.manifest_identity(path)


def test_full_download_checks_real_binary_manifest(monkeypatch, tmp_path):
    import io, zipfile

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("AndroidManifest.xml", binary_manifest())
    body = output.getvalue()
    grant = DownloadGrant(
        release(size=len(body)), "https://download.samsungapps.com/a.apk", len(body)
    )
    result = scraper.download_apk(session(Response(body)), grant, tmp_path / "valid")
    assert result.path.read_bytes() == body
    drift = DownloadGrant(
        release(version_code=124, size=len(body)), grant.url, len(body)
    )
    with pytest.raises(VersionDrift):
        scraper.download_apk(session(Response(body)), drift, tmp_path / "drift")
    assert not list((tmp_path / "drift").iterdir())
