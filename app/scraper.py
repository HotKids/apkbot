"""Galaxy Store metadata and download authorization; never fetches APK files."""

import json
import logging
import time
from urllib.parse import urlencode, urljoin, urlsplit

import requests

import config
from galaxy_store import (
    AppRequest,
    DownloadError,
    InvalidResponse,
    LoginRequired,
    OdsProfile,
    ServiceError,
    StoreError,
    TransportError,
    VersionDrift,
    match_cn_details,
    parse_ods_grant,
    parse_ods_metadata,
    parse_stub,
)

STUB_URL = "https://vas.samsungapps.com/stub/stubDownload.as"
ODS_URL = "https://cn-ms.galaxyappstore.com/ods.as"
METADATA_LIMIT = 2_000_000
REDIRECT_LIMIT = 5


def new_session():
    # urllib3 DEBUG output contains complete request paths, including signed
    # download queries. Application DEBUG must not turn those logs on.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    session = requests.Session()
    session.headers.update(
        {"User-Agent": config.USER_AGENT, "Accept-Encoding": "identity"}
    )
    return session


def validate_url(url):
    try:
        u = urlsplit(url)
        host = u.hostname or ""
        port = u.port
    except ValueError:
        raise InvalidResponse("商店数据异常（下载地址无效），请稍后重试。") from None
    # No arbitrary hosts, HTTP, userinfo, custom ports, or suffix lookalikes.
    if (
        u.scheme != "https"
        or u.username is not None
        or u.password is not None
        or port not in (None, 443)
        or u.fragment
        or not u.path.startswith("/")
        or "\\" in url
        or any(ord(c) < 33 for c in url)
        or not (
            host == "galaxystore.samsung.com"
            or any(
                host == suffix or host.endswith("." + suffix)
                for suffix in ("samsungapps.com", "galaxyappstore.com")
            )
        )
    ):
        raise InvalidResponse("商店数据异常（地址不属于 Samsung），请稍后重试。")
    return url


def open_response(session, method, url, *, deadline, **kwargs):
    for _ in range(REDIRECT_LIMIT + 1):
        validate_url(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransportError("商店响应超时，请稍后重试。")
        try:
            response = session.request(
                method,
                url,
                stream=True,
                allow_redirects=False,
                timeout=(min(15, remaining), min(30, remaining)),
                **kwargs,
            )
        except requests.RequestException:
            raise TransportError(
                "无法连接 Galaxy Store，请检查服务器网络后重试。"
            ) from None
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            # Never replay an ODS POST at another destination.
            if method != "GET" or not location:
                raise TransportError("商店返回了异常跳转，请稍后重试。")
            url = urljoin(url, location)
            continue
        if response.status_code != 200:
            status = response.status_code
            response.close()
            raise TransportError(
                f"商店暂时不可用（HTTP {status}），这不代表应用不存在；请稍后重试。"
            )
        return response
    raise TransportError("商店跳转次数过多，请稍后重试。")


def chunks(response, *, deadline, limit):
    count = 0
    try:
        for block in response.iter_content(64 * 1024):
            if time.monotonic() > deadline:
                raise TransportError("商店响应超时，请稍后重试。")
            count += len(block)
            if count > limit:
                raise DownloadError("商店数据异常（响应过大），请稍后重试。")
            yield block
    except requests.RequestException:
        raise TransportError("与商店的连接中断，请稍后重试。") from None


def request_bytes(session, method, url, **kwargs):
    deadline = time.monotonic() + config.REQUEST_TIMEOUT
    with open_response(session, method, url, deadline=deadline, **kwargs) as response:
        return b"".join(chunks(response, deadline=deadline, limit=METADATA_LIMIT))


def details_from_response(data, release):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous website JSON")
            result[key] = value
        return result

    try:
        response = json.loads(data, object_pairs_hook=unique_object)
        return match_cn_details(response, release)
    except (ValueError, UnicodeError, RecursionError):
        return release, None


class GalaxyStore:
    def __init__(self, session=None):
        self.session = session or new_session()
        self.profile = OdsProfile()
        self._stub_grant = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.session.close()

    def _ods(self, request_id, payload):
        return request_bytes(
            self.session,
            "POST",
            ODS_URL + "?" + urlencode({"reqId": request_id, "ot": "01", "ct": "B"}),
            data=payload,
            headers={
                "Content-Type": "text/plain; charset=UTF-8",
                "Accept": "image/webp",
            },
        )

    def stub(self, package, region):
        params = dict(
            appId=package,
            deviceId="SM-S9480",
            mcc="460" if region == "CN" else "310",
            mnc="00" if region == "CN" else "260",
            csc="CHC" if region == "CN" else "XAA",
            sdkVer="36",
            abiType="64",
            extuk=self.profile.identity,
            systemId=str(int(time.time() * 1000)),
        )
        data = request_bytes(self.session, "GET", STUB_URL + "?" + urlencode(params))
        self._stub_grant = parse_stub(data, package, region)
        return self._stub_grant

    def metadata(self, app: AppRequest):
        if app.region != "CN":
            try:
                return self.stub(app.package, "US").release
            except StoreError:
                if app.region == "US":
                    raise
        return parse_ods_metadata(
            self._ods("2298", self.profile.metadata(app.package)), app.package
        )

    def download_link(self, app):
        release = self.metadata(app)
        try:
            return self.authorize(release)
        except StoreError:
            if app.region != "AUTO" or release.region != "US":
                raise
        # A US release can still fail authorization (e.g. an unusable URL).
        # AUTO then tries CN; an explicit region never switches.
        return self.authorize(self.metadata(AppRequest(app.package, "CN")))

    def authorize(self, release):
        if release.needs_login:
            raise LoginRequired(
                "此应用需要登录 Samsung 账号才能下载，bot 无法匿名获取。"
            )
        if not release.installable:
            raise ServiceError("商店将此版本标为不可安装，暂时无法下载。")
        if release.channel == "stub":
            # The stub answer that selected this release already carries its
            # URL; asking again only adds a request and a chance of drift.
            grant = self._stub_grant
            if grant is None or grant.release != release:
                grant = self.stub(release.package, release.region)
            if (
                grant.release.identity != release.identity
                or grant.release.version_name != release.version_name
            ):
                raise VersionDrift("商店版本刚刚更新，请再试一次。")
        else:
            grant = parse_ods_grant(
                self._ods("2316", self.profile.download(release)), release
            )
        validate_url(grant.url)
        return grant

    def details(self, release):
        try:
            data = request_bytes(
                self.session,
                "GET",
                "https://galaxystore.samsung.com/api/detail/"
                + release.package
                + "?cntyCd=CHN",
                headers={"Accept-Language": "zh-CN"},
            )
            return details_from_response(data, release)
        except (StoreError, UnicodeError, ValueError):
            return release, None
