"""Galaxy Store metadata and download authorization; never fetches APK files."""

import json
import logging
import time
from dataclasses import replace
from urllib.parse import urlencode, urljoin, urlsplit

import requests

import config
from galaxy_store import (
    AppRequest,
    bound_main_details,
    check_ods_status,
    DownloadError,
    HttpStatusError,
    InvalidResponse,
    LoginRequired,
    OdsProfile,
    REGIONS,
    ServiceError,
    StoreError,
    TransportError,
    VersionDrift,
    match_cn_details,
    match_ods_details,
    parse_update_list,
    xml_fields,
    parse_ods_grant,
    parse_ods_metadata,
    parse_stub,
)

STUB_URL = "https://vas.samsungapps.com/stub/stubDownload.as"
ODS_URL = "https://cn-ms.galaxyappstore.com/ods.as"
ODS_ENDPOINTS = {"CN": ODS_URL, "US": "https://us-odc.samsungapps.com/ods.as"}
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
        raise InvalidResponse("商店返回的下载地址无效，请稍后重试。") from None
    # No arbitrary hosts, HTTP, userinfo, custom ports, or suffix lookalikes.
    if (
        u.scheme != "https"
        or u.username is not None
        or u.password is not None
        or port not in (None, 443)
        or u.fragment
        or not u.path.startswith("/")
        or not u.path.strip("/")
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
        raise InvalidResponse("商店返回的地址不属于 Samsung，请稍后重试。")
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
                "无法连接 Galaxy Store，请稍后重试。"
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
            if not 400 <= status <= 599:
                raise TransportError("商店返回的信息无效，请稍后重试。")
            raise HttpStatusError(
                f"商店暂时不可用（HTTP {status}），请稍后重试。"
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
                raise DownloadError("商店返回的信息超出限制，请稍后重试。")
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

    def _profile(self, region):
        return replace(self.profile, region=region)

    def _post(self, endpoint, request_id, payload):
        return request_bytes(
            self.session, "POST",
            endpoint + "?" + urlencode({"reqId": request_id, "ot": "01", "ct": "B"}),
            data=payload,
            headers={"Content-Type": "text/plain; charset=UTF-8", "Accept": "image/webp"},
        )

    def _ods(self, request_id, payload, *, region="CN"):
        return self._post(ODS_ENDPOINTS[region], request_id, payload)

    def ods_metadata(self, package, region):
        return parse_ods_metadata(
            self._ods("2298", self._profile(region).metadata(package), region=region), package, region
        )

    def stub(self, package, region):
        params = dict(
            appId=package,
            deviceId="SM-S9480",
            mcc=REGIONS[region]["mcc"],
            mnc=REGIONS[region]["mnc"],
            csc=REGIONS[region]["csc"],
            sdkVer="36",
            abiType="64",
            extuk=self.profile.identity,
            systemId=str(int(time.time() * 1000)),
        )
        data = request_bytes(self.session, "GET", STUB_URL + "?" + urlencode(params))
        self._stub_grant = parse_stub(data, package, region)
        return self._stub_grant

    def regional_metadata(self, package, region):
        if region == "US":
            try:
                return self.stub(package, region).release
            except StoreError:
                pass
        return self.ods_metadata(package, region)

    def metadata(self, app: AppRequest):
        for region in (("US", "CN") if app.region == "AUTO" else (app.region,)):
            try:
                return self.regional_metadata(app.package, region)
            except StoreError:
                if app.region != "AUTO" or region == "CN":
                    raise

    def download_link(self, app):
        self.link_stage = "query"
        release = self.metadata(app)
        self.link_stage = "download"
        try:
            return self.authorize(release)
        except StoreError:
            if release.channel == "stub":
                try:
                    self.link_stage = "query"
                    release = self.ods_metadata(app.package, release.region)
                    self.link_stage = "download"
                    return self.authorize(release)
                except StoreError:
                    if app.region != "AUTO" or release.region != "US":
                        raise
            elif app.region != "AUTO" or release.region != "US":
                raise
        self.link_stage = "query"
        release = self.metadata(AppRequest(app.package, "CN"))
        self.link_stage = "download"
        return self.authorize(release)

    def authorize(self, release):
        if release.needs_login:
            raise LoginRequired(
                "此应用需要登录 Samsung 账户，当前无法获取下载链接。"
            )
        if not release.installable:
            raise ServiceError("此版本暂不支持安装，无法获取下载链接。")
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
                raise VersionDrift("商店版本已发生变化，请重试。")
        else:
            try:
                grant = parse_ods_grant(
                    self._ods("2311", self._profile(release.region).download(release), region=release.region), release
                )
            except (HttpStatusError, ServiceError, LoginRequired):
                # Only an HTTP/API rejection permits restore authorization;
                # malformed or mismatched grants must fail without another try.
                try:
                    grant = parse_ods_grant(
                        self._ods("2316", self._profile(release.region).download(release, restore=True), region=release.region),
                        release, restore=True,
                    )
                except (HttpStatusError, ServiceError, LoginRequired):
                    if release.region != "CN":
                        raise
                    # This endpoint has no established successful contract for
                    # all apps. Never accept an unbound or third-party mirror.
                    data = self._ods("2801", self._profile("CN").mirror(release), region="CN")
                    fields = xml_fields(data, "SamsungProtocol", "2801")
                    check_ods_status(fields)
                    if fields.get("GUID") != release.package or release.size is None:
                        raise InvalidResponse("商店备用下载信息无法确认，请稍后重试。")
                    grant = parse_ods_grant(data, release, request_id="2801")
        validate_url(grant.url)
        return grant

    def batch_updates(self, baselines, region):
        results = {}
        # 120 is the observed AppMarket batching contract, not an API maximum.
        for offset in range(0, len(baselines), 120):
            group = baselines[offset:offset + 120]
            data = self._ods("2389", self._profile(region).updates(group), region=region)
            results.update(parse_update_list(data, group, region))
        return results

    def update_metadata(self, fields):
        # 2389 reports candidates but omits login/installability. Confirm the
        # same-region current metadata instead of inventing permission flags.
        return self.ods_metadata(fields["GUID"], fields["region"]) if fields else None

    def details(self, release):
        try:
            profile = self._profile(release.region)
            main = self._ods("2290", profile.details(release), region=release.region)
            bound_main_details(main, release)
            overview = self._ods("2291", profile.details(release, overview=True), region=release.region)
            confirmed = self._ods("2290", profile.details(release), region=release.region)
            return match_ods_details(main, overview, confirmed, release)
        except StoreError:
            pass
        # Preserve the existing, version-bound CN website fallback only in CN.
        if release.region != "CN":
            return release, None
        try:
            data = request_bytes(
                self.session, "GET",
                "https://galaxystore.samsung.com/api/detail/" + release.package + "?cntyCd=CHN",
                headers={"Accept-Language": "zh-CN"},
            )
            return details_from_response(data, release)
        except (StoreError, UnicodeError, ValueError):
            return release, None
