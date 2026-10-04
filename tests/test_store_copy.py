from unittest.mock import Mock

import pytest
import requests

import scraper
from galaxy_store import (
    InvalidResponse,
    LoginRequired,
    NoAvailableVersion,
    ServiceError,
    StubRestricted,
    TransportError,
    VersionDrift,
    check_stub_status,
    parse_ods_grant,
    parse_ods_metadata,
)
from tests.test_galaxy_store import metadata, ods, release
from tests.test_scraper import session


@pytest.mark.parametrize(
    "field,value",
    [
        ("productID", ""),
        ("needToLogin", ""),
        ("versionCode", "invalid"),
        ("realContentsSize", "-42"),
    ],
)
def test_metadata_error_reason_hides_protocol_names(field, value):
    with pytest.raises(InvalidResponse) as caught:
        parse_ods_metadata(metadata(**{field: value}), "com.example.app")
    reason = str(caught.value)
    assert field not in reason
    assert "商店" in reason and "请稍后重试。" in reason


@pytest.mark.parametrize(
    "message,exception",
    [
        ("application is not approved as stub", StubRestricted),
        ("application is not available in this country", NoAvailableVersion),
        ("login required", LoginRequired),
        ("unknown service failure", ServiceError),
    ],
)
def test_store_status_reasons_are_formal_and_preserve_outcome(message, exception):
    with pytest.raises(exception) as caught:
        check_stub_status({"resultCode": "0", "resultMsg": message})
    reason = str(caught.value)
    assert all(word not in reason for word in ("再试", "换个", "bot "))
    assert "不存在" not in reason or "不代表应用不存在" in reason


def test_download_version_change_has_formal_retry_reason():
    with pytest.raises(VersionDrift) as caught:
        parse_ods_grant(ods({"productID": "00001", "version": "2.0"}), release())
    assert str(caught.value) == "商店版本已发生变化，请重试。"


def test_login_requirement_does_not_expose_bot_implementation():
    with pytest.raises(LoginRequired) as caught:
        scraper.GalaxyStore(Mock()).authorize(release(needs_login=True))
    assert str(caught.value) == "此应用需要登录 Samsung 账户，当前无法获取下载链接。"


def test_network_failure_reason_is_user_facing_and_does_not_leak_the_cause():
    http = session(requests.ConnectionError("synthetic private URL"))
    with pytest.raises(TransportError) as caught:
        scraper.request_bytes(http, "GET", scraper.STUB_URL)
    assert str(caught.value) == "无法连接 Galaxy Store，请稍后重试。"
