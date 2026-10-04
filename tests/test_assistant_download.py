import ast
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
import urllib.request

import pytest

from galaxy_store import OdsProfile
from tests.test_galaxy_store import metadata, ods, release


SCRIPT = Path(__file__).resolve().parents[1] / "samsung-assistant/download.py"


def test_script_is_independent_of_bot_and_external_dependencies():
    assert SCRIPT.is_file()
    tree = ast.parse(SCRIPT.read_text())
    imports = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports <= {
        "argparse",
        "datetime",
        "hashlib",
        "http",
        "os",
        "pathlib",
        "re",
        "sys",
        "tempfile",
        "time",
        "urllib",
        "uuid",
        "xml",
    }


@pytest.fixture
def downloader(monkeypatch):
    spec = spec_from_file_location("assistant_download", SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    def blocked(*args, **kwargs):
        raise AssertionError("Real urllib network access is forbidden in offline tests")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", blocked)
    return module


@pytest.fixture
def output_dir(tmp_path):
    directory = tmp_path / "downloads"
    directory.mkdir()
    return directory


class Response(BytesIO):
    status = 200

    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


class Opener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def grant(**changes):
    fields = dict(
        productID="00001",
        GUID="com.samsung.android.app.sreminder",
        version="01.02.3",
        versionCode="123",
        binaryArch="32n64",
        contentsSize="42",
        downLoadURI="https://download.samsungapps.com/app.apk?signature=synthetic-only",
    )
    fields.update(changes)
    return ods(fields)


def assistant_metadata(**changes):
    return metadata(GUID="com.samsung.android.app.sreminder", **changes)


def test_default_flow_matches_canonical_ods_and_downloads_full_32n64(
    downloader, monkeypatch, output_dir, capsys
):
    http = Opener(
        [
            Response(assistant_metadata()),
            Response(grant()),
            Response(b"a" * 42, {"Content-Length": "42"}),
        ]
    )
    monkeypatch.setattr(downloader, "build_opener", lambda *_: http)
    monkeypatch.setattr(
        downloader,
        "uuid4",
        lambda: SimpleNamespace(hex="0123456789abcdef0123456789abcdef"),
    )
    monkeypatch.chdir(output_dir)
    assert downloader.main([]) == 0
    output = output_dir / "Samsung-Assistant_01.02.3_123.apk"
    assert output.read_bytes() == b"a" * 42
    assert list(output_dir.iterdir()) == [output]
    assert len(http.calls) == 3
    requests = [call[0] for call in http.calls]
    assert [request.get_method() for request in requests] == ["POST", "POST", "GET"]
    assert requests[0].full_url.endswith("?reqId=2298&ot=01&ct=B")
    assert requests[1].full_url.endswith("?reqId=2316&ot=01&ct=B")
    profile = OdsProfile("0123456789abcdef")
    expected = [
        profile.metadata(downloader.PACKAGE),
        profile.download(release(package=downloader.PACKAGE)),
    ]
    for request, payload in zip(requests[:2], expected):
        actual_root = downloader.ET.fromstring(request.data)
        expected_root = downloader.ET.fromstring(payload)
        for field in ("systemId", "sessionId"):
            actual_root.attrib.pop(field)
            expected_root.attrib.pop(field)
        actual_root.find("request").attrib.pop("transactionId")
        expected_root.find("request").attrib.pop("transactionId")
        assert downloader.ET.tostring(actual_root) == downloader.ET.tostring(
            expected_root
        )
        assert request.get_header("Content-type") == "text/plain; charset=UTF-8"
        assert request.get_header("Accept") == "image/webp"
    output_text = capsys.readouterr()
    assert "signature" not in output_text.out + output_text.err
    assert "https://" not in output_text.out + output_text.err


@pytest.mark.parametrize(
    "changes",
    [
        {"needToLogin": "1"},
        {"installableYN": "N"},
        {"GUID": "com.other.app"},
        {"productID": "invalid"},
        {"versionCode": "0"},
    ],
)
def test_metadata_rejects_unusable_or_mismatched_release(downloader, changes):
    fields = dict(GUID=downloader.PACKAGE)
    fields.update(changes)
    with pytest.raises(downloader.DownloadError):
        downloader.parse_metadata(metadata(**fields))


@pytest.mark.parametrize(
    "changes",
    [
        {"productID": "2"},
        {"GUID": "com.other.app"},
        {"version": "2.0"},
        {"versionCode": "124"},
        {"contentsSize": "43"},
        {"downLoadURI": "https://evil.test/a.apk"},
    ],
)
def test_grant_must_match_metadata_before_any_file_request(downloader, changes):
    selected = downloader.parse_metadata(assistant_metadata())
    with pytest.raises(downloader.DownloadError):
        downloader.parse_grant(grant(**changes), selected)


@pytest.mark.parametrize(
    "body",
    [
        b'<!DOCTYPE SamsungProtocol [<!ENTITY x "synthetic">]><SamsungProtocol>&x;</SamsungProtocol>',
        b"\xff",
        b'<SamsungProtocol><value name="version">1</value><value name="version">2</value></SamsungProtocol>',
        b"\x00<\x00!\x00D\x00O\x00C\x00T\x00Y\x00P\x00E\x00>",
    ],
)
def test_remote_xml_rejects_entities_bad_encoding_and_conflicting_fields(
    downloader, body
):
    with pytest.raises(downloader.DownloadError):
        downloader.xml_fields(body)


def test_store_rejection_is_not_reported_as_app_absence(downloader):
    with pytest.raises(downloader.DownloadError) as caught:
        downloader.parse_metadata(ods(error="synthetic failure", code="5"))
    assert "not exist" not in str(caught.value)
    assert "synthetic failure" not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://download.samsungapps.com/a",
        "https://samsungapps.com.evil.test/a",
        "https://user:pass@download.samsungapps.com/a",
        "https://download.samsungapps.com:8443/a",
        "https://download.samsungapps.com/a#x",
    ],
)
def test_untrusted_urls_never_reach_transport(downloader, url):
    http = Opener([])
    with pytest.raises(downloader.DownloadError):
        downloader.open_response(
            http, "GET", url, deadline=downloader.time.monotonic() + 60
        )
    assert http.calls == []


@pytest.mark.parametrize(
    "location,method",
    [
        ("https://evil.test/synthetic", "GET"),
        ("https://download.samsungapps.com/a", "POST"),
    ],
)
def test_redirects_are_validated_and_posts_are_never_replayed(
    downloader, location, method
):
    error = HTTPError(
        "https://download.samsungapps.com/a",
        302,
        "redirect",
        {"Location": location},
        BytesIO(),
    )
    http = Opener([error])
    with pytest.raises(downloader.DownloadError):
        downloader.open_response(
            http,
            method,
            "https://download.samsungapps.com/a",
            deadline=downloader.time.monotonic() + 60,
        )
    assert len(http.calls) == 1


def test_valid_samsung_redirects_keep_finite_limits(downloader):
    error = HTTPError(
        "https://download.samsungapps.com/a",
        302,
        "redirect",
        {"Location": "/b"},
        BytesIO(),
    )
    http = Opener([error, Response(b"42")])
    with downloader.open_response(
        http,
        "GET",
        "https://download.samsungapps.com/a",
        deadline=downloader.time.monotonic() + 60,
    ) as response:
        assert response.read() == b"42"
    assert len(http.calls) == 2
    assert http.calls[1][0].full_url == "https://download.samsungapps.com/b"
    assert all(0 < timeout <= 30 for _, timeout in http.calls)


@pytest.mark.parametrize("body", [b"a" * 41, b"a" * 43])
def test_size_mismatch_removes_partial_file(downloader, monkeypatch, output_dir, body):
    http = Opener([Response(assistant_metadata()), Response(grant()), Response(body)])
    monkeypatch.setattr(downloader, "build_opener", lambda *_: http)
    with pytest.raises(downloader.DownloadError):
        downloader.download(output_dir)
    assert list(output_dir.iterdir()) == []


def test_existing_completed_file_is_not_modified(downloader, monkeypatch, output_dir):
    existing = output_dir / "Samsung-Assistant_01.02.3_123.apk"
    existing.write_bytes(b"existing completed file")
    http = Opener([Response(assistant_metadata())])
    monkeypatch.setattr(downloader, "build_opener", lambda *_: http)
    with pytest.raises(downloader.DownloadError):
        downloader.download(output_dir)
    assert existing.read_bytes() == b"existing completed file"
    assert len(http.calls) == 1
    assert list(output_dir.iterdir()) == [existing]


def test_network_error_hides_signed_address_and_leaves_no_output(
    downloader, monkeypatch, output_dir, capsys
):
    http = Opener(
        [
            Response(assistant_metadata()),
            Response(grant()),
            URLError("https://download.samsungapps.com/a?signature=synthetic-only"),
        ]
    )
    monkeypatch.setattr(downloader, "build_opener", lambda *_: http)
    assert downloader.main(["--output-dir", str(output_dir)]) == 1
    assert list(output_dir.iterdir()) == []
    output = capsys.readouterr()
    assert "signature" not in output.out + output.err
    assert "https://" not in output.out + output.err


def test_metadata_and_transfer_limits_are_enforced_before_exceeding_the_bound(
    downloader,
):
    with pytest.raises(downloader.DownloadError):
        downloader.xml_fields(b"x" * (downloader.METADATA_LIMIT + 1))
    http = Opener([])
    with pytest.raises(downloader.DownloadError):
        downloader.open_response(
            http, "GET", "https://download.samsungapps.com/a", deadline=0
        )
    assert http.calls == []
    with pytest.raises(downloader.DownloadError):
        list(downloader.chunks(Response(b"abc"), downloader.time.monotonic() + 60, 2))


def test_authorization_rejection_does_not_create_an_output(
    downloader, monkeypatch, output_dir
):
    class NoOutputBeforeGrant(Opener):
        def open(self, request, timeout):
            assert list(output_dir.iterdir()) == []
            return super().open(request, timeout)

    http = NoOutputBeforeGrant(
        [Response(assistant_metadata()), Response(ods(error="denied", code="5"))]
    )
    monkeypatch.setattr(downloader, "build_opener", lambda *_: http)
    with pytest.raises(downloader.DownloadError):
        downloader.download(output_dir)
    assert len(http.calls) == 2
    assert list(output_dir.iterdir()) == []


def test_interrupted_body_removes_incomplete_output(
    downloader, monkeypatch, output_dir
):
    class Interrupted(Response):
        def read1(self, count):
            raise OSError("synthetic interrupted stream")

    http = Opener([Response(assistant_metadata()), Response(grant()), Interrupted(b"")])
    monkeypatch.setattr(downloader, "build_opener", lambda *_: http)
    with pytest.raises(downloader.DownloadError):
        downloader.download(output_dir)
    assert list(output_dir.iterdir()) == []


def test_destination_replacement_is_preserved(downloader, monkeypatch, output_dir):
    destination = output_dir / "Samsung-Assistant_01.02.3_123.apk"
    published = output_dir / "other.apk"

    class Replaced(Response):
        def read1(self, count):
            if not published.exists() and self.tell() == 0:
                published.write_bytes(b"other writer's completed file")
                published.replace(destination)
            return super().read1(count)

    http = Opener(
        [Response(assistant_metadata()), Response(grant()), Replaced(b"a" * 42)]
    )
    monkeypatch.setattr(downloader, "build_opener", lambda *_: http)
    with pytest.raises(downloader.DownloadError):
        downloader.download(output_dir)
    assert destination.read_bytes() == b"other writer's completed file"
    assert list(output_dir.iterdir()) == [destination]


def test_completed_download_never_replaces_any_destination(
    downloader, monkeypatch, output_dir
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Download must never replace an existing filesystem path")

    monkeypatch.setattr(downloader.os, "replace", forbidden)
    http = Opener(
        [Response(assistant_metadata()), Response(grant()), Response(b"a" * 42)]
    )
    monkeypatch.setattr(downloader, "build_opener", lambda *_: http)
    result = downloader.download(output_dir)
    assert result.read_bytes() == b"a" * 42
    assert list(output_dir.iterdir()) == [result]


@pytest.mark.parametrize("length", ["41", "9" * 5000])
def test_invalid_content_length_is_rejected_and_cleaned(
    downloader, monkeypatch, output_dir, length
):
    http = Opener(
        [
            Response(assistant_metadata()),
            Response(grant()),
            Response(b"a" * 42, {"Content-Length": length}),
        ]
    )
    monkeypatch.setattr(downloader, "build_opener", lambda *_: http)
    with pytest.raises(downloader.DownloadError):
        downloader.download(output_dir)
    assert list(output_dir.iterdir()) == []
