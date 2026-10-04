"""Galaxy Store protocol and identity rules; no I/O or Telegram dependencies."""

from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import sha256
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException


class StoreError(Exception):
    """Safe error text, without service bodies or signed URLs."""


class InvalidInput(StoreError):
    pass


class NoAvailableVersion(StoreError):
    pass


class StubRestricted(StoreError):
    pass


class LoginRequired(StoreError):
    pass


class ServiceError(StoreError):
    pass


class TransportError(StoreError):
    pass


class HttpStatusError(TransportError):
    """An HTTP rejection, distinct from connection and redirect failures."""


class InvalidResponse(StoreError):
    pass


class DownloadError(StoreError):
    pass


class VersionDrift(DownloadError):
    pass


USAGE = "请发送包名或 Galaxy Store 详情链接，可指定地区 CN 或 US。例如：com.lucky.luckyclient CN"
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
# Samsung lists some of its own US builds under release labels such as
# "[260921] GALAXY Store 9CR Update -  US" (stub and US web page alike).
RELEASE_LABEL = re.compile(r"\[\d+\]\s")
ODS_CRITICAL = {"GUID", "productID", "version", "versionCode", "realContentsSize", "contentsSize", "downLoadURI", "productName", "needToLogin", "installableYN", "errorCode", "errorString"}
REGIONS = {
    "CN": dict(mcc="460", mnc="00", csc="CHC", lang="zh_CN", country="CHN"),
    "US": dict(mcc="310", mnc="260", csc="XAA", lang="en_US", country="USA"),
}


@dataclass(frozen=True)
class AppRequest:
    package: str
    region: str = "AUTO"

    def __post_init__(self):
        if (
            len(self.package) > 255
            or not PACKAGE.fullmatch(self.package)
            or self.region not in {"AUTO", "US", "CN"}
        ):
            raise InvalidInput(USAGE)

    @property
    def key(self):
        return sha256(f"{self.package}:{self.region}".encode()).hexdigest()[:32]


def parse_input(text):
    parts = text.split()
    if not 1 <= len(parts) <= 2:
        raise InvalidInput(USAGE)
    value = parts[0]
    if ":" in value or "/" in value:
        try:
            u = urlsplit(value)
        except ValueError:
            raise InvalidInput(
                "无法识别该链接，请发送 Galaxy Store 应用详情页链接。"
            ) from None
        # Share links append ?session_id=…; only the path names the package.
        if (
            u.scheme != "https"
            or u.netloc not in {"galaxystore.samsung.com", "apps.galaxyappstore.com"}
            or not u.path.startswith("/detail/")
            or u.path.count("/") != 2
        ):
            raise InvalidInput(
                "只支持 Galaxy Store 应用详情页链接："
                "https://galaxystore.samsung.com/detail/<包名>"
            )
        value = u.path[len("/detail/") :]
    return AppRequest(value, parts[1].upper() if len(parts) == 2 else "AUTO")


@dataclass(frozen=True)
class Release:
    package: str
    region: str
    product_id: str
    name: str
    version_name: str
    version_code: int
    size: int | None = None
    channel: str = "ods"
    needs_login: bool = False
    installable: bool = True
    updated_date: str | None = None

    @property
    def identity(self):
        return f"{self.region}:{self.product_id}:{self.version_code}"


@dataclass(frozen=True)
class DownloadGrant:
    release: Release
    url: str = field(repr=False)
    size: int = 0


def xml_fields(data: bytes, root_name: str, request_id=None):
    if not data or len(data) > 2_000_000:
        raise InvalidResponse("商店返回的信息为空或超出限制，请稍后重试。")
    try:
        root = fromstring(
            data, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except (ET.ParseError, DefusedXmlException, ValueError):
        raise InvalidResponse("商店返回的信息无法读取，请稍后重试。") from None
    if root.tag != root_name:
        raise InvalidResponse("商店返回的信息格式不正确，请稍后重试。")
    if root_name == "SamsungProtocol":
        responses = root.findall("response")
        if responses:
            if len(responses) != 1:
                raise InvalidResponse("商店返回的信息存在冲突，请稍后重试。")
            response = responses[0]
            if request_id is not None and response.get("id", request_id) != request_id:
                raise InvalidResponse("商店返回的接口信息不一致，请稍后重试。")
            code = response.get("returnCode", "")
            if not re.fullmatch(r"-?[0-9]{1,19}", code):
                raise InvalidResponse("商店返回的信息无效，请稍后重试。")
            errors = response.findall("errorInfo/errorString")
            if len(errors) != 1:
                raise InvalidResponse("商店返回的信息不完整，请稍后重试。")
            status = dict(errorCode=errors[0].get("errorCode", ""), errorString=(errors[0].text or "").strip())
            check_ods_status(status)
            if code != "0":
                raise ServiceError("商店拒绝了请求，请稍后重试。")
            rows = response.findall("list")
            if len(rows) != 1:
                raise InvalidResponse("商店返回的信息不完整，请稍后重试。")
            nodes = list(rows[0])
        else:
            # Early protocol fixtures and older responses have direct values.
            status = {}
            nodes = list(root) + root.findall("errorInfo/errorString")
    else:
        status = {}
        nodes = list(root)
    fields = dict(status)
    for node in nodes:
        key = node.attrib.get("name", node.tag)
        if root_name == "SamsungProtocol" and key in ODS_CRITICAL and node.tag not in {"value", "errorString", "errorCode"}:
            raise InvalidResponse("商店返回的信息存在冲突，请稍后重试。")
        if len(node):
            if key in ODS_CRITICAL:
                raise InvalidResponse("商店返回的信息存在冲突，请稍后重试。")
            continue
        if root_name == "SamsungProtocol" and node.tag not in {"value", "errorString", "errorCode"}:
            continue
        if key in fields:
            raise InvalidResponse("商店返回的信息存在冲突，请稍后重试。")
        fields[key] = (node.text or "") if key == "updateDescription" else (node.text or "").strip()
        if key == "errorString" and "errorCode" in node.attrib:
            if "errorCode" in fields:
                raise InvalidResponse("商店返回的信息存在冲突，请稍后重试。")
            fields["errorCode"] = node.attrib["errorCode"]
    return fields


def required(fields, key):
    value = fields.get(key, "")
    if not value:
        raise InvalidResponse("商店返回的信息不完整，请稍后重试。")
    return value


def positive(fields, key):
    raw = required(fields, key)
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 19 or int(raw) <= 0:
        raise InvalidResponse("商店返回的信息无效，请稍后重试。")
    return int(raw)


def _identity(fields, package, guid_key, product_key):
    if required(fields, guid_key) != package:
        raise InvalidResponse("商店返回的应用与请求不一致，请稍后重试。")
    product = required(fields, product_key)
    if not re.fullmatch(r"[0-9]{1,30}", product):
        raise InvalidResponse("商店返回的应用信息无效，请稍后重试。")
    return product


def check_stub_status(fields):
    if fields.get("resultCode") == "1":
        return
    message = fields.get("resultMsg", "").casefold().rstrip(".")
    if message in {
        "application is not approved as stub",
        "application is not allowed to use stubdownload",
    }:
        raise StubRestricted(
            "商店未提供此应用的直连下载接口。"
        )
    # Device/carrier/profile mismatch and unknown responses are not absence.
    if message == "application is not available in this country":
        raise NoAvailableVersion("此应用在所选地区不可用，请指定其他地区后重试。")
    if "login" in message or "log in" in message:
        raise LoginRequired("此应用需要登录 Samsung 账户，当前无法获取下载链接。")
    raise ServiceError("商店未返回可用结果，请稍后重试。")


def parse_stub(data, package, region):
    fields = xml_fields(data, "result")
    check_stub_status(fields)
    product = _identity(fields, package, "appId", "productId")
    release = Release(
        package,
        region,
        product,
        fields.get("productName") or package,
        required(fields, "versionName"),
        positive(fields, "versionCode"),
        positive(fields, "contentSize"),
        "stub",
    )
    return DownloadGrant(release, required(fields, "downloadURI"), release.size)


def check_ods_status(fields):
    status = fields.get("errorString", "")
    code = required(fields, "errorCode")
    if not re.fullmatch(r"-?[0-9]{1,19}", code):
        raise InvalidResponse("商店返回的信息无效，请稍后重试。")
    if code != "0" or status.casefold() not in {"", "success"}:
        if "login" in status.casefold() or "log in" in status.casefold():
            raise LoginRequired(
                "此应用需要登录 Samsung 账户，当前无法获取下载链接。"
            )
        raise ServiceError("商店拒绝了请求，请稍后重试。")


def parse_ods_metadata(data, package, region="CN"):
    fields = xml_fields(data, "SamsungProtocol", "2298")
    check_ods_status(fields)
    product = _identity(fields, package, "GUID", "productID")
    login = required(fields, "needToLogin")
    installable = required(fields, "installableYN")
    if login not in {"0", "1"} or installable not in {"Y", "N"}:
        raise InvalidResponse("商店返回的应用信息无效，请稍后重试。")
    return Release(
        package,
        region,
        product,
        fields.get("productName") or package,
        required(fields, "version"),
        positive(fields, "versionCode"),
        positive(fields, "realContentsSize")
        if fields.get("realContentsSize")
        else None,
        needs_login=login == "1",
        installable=installable == "Y",
    )


def parse_ods_grant(data, release, *, restore=False, request_id=None):
    fields = xml_fields(data, "SamsungProtocol", request_id or ("2316" if restore else "2311"))
    check_ods_status(fields)
    if required(fields, "productID") != release.product_id:
        raise InvalidResponse("商店返回的下载信息与所选应用不一致，请重试。")
    if "GUID" in fields and fields["GUID"] != release.package:
        raise InvalidResponse("商店返回的下载信息与所选应用不一致，请重试。")
    # binaryArch describes CPU coverage (e.g. 32n64), not full vs. delta APKs.
    # Use the full download's downLoadURI and contentsSize below.
    # 2311 binds the grant to the selected version; 2316 may omit these fields.
    if not restore or "version" in fields:
        if required(fields, "version") != release.version_name:
            raise VersionDrift("商店版本已发生变化，请重试。")
    if not restore or "versionCode" in fields:
        if positive(fields, "versionCode") != release.version_code:
            raise VersionDrift("商店版本已发生变化，请重试。")
    size = positive(fields, "contentsSize")
    if release.size is not None and size != release.size:
        raise VersionDrift("下载文件大小与所选版本不一致，请重试。")
    # Only a version-bound full grant can fill missing metadata for subsequent
    # date/log checks. Restore grants can omit their version and remain unbound.
    if release.size is None and fields.get("version") and fields.get("versionCode"):
        release = replace(release, size=size)
    return DownloadGrant(release, required(fields, "downLoadURI"), size)


@dataclass
class OdsProfile:
    identity: str = field(default_factory=lambda: uuid4().hex[:16], repr=False)
    region: str = "CN"

    def __post_init__(self):
        if self.region not in REGIONS:
            raise InvalidInput(USAGE)

    def envelope(self, method, request_id, params):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        transaction = (
            str(now.day)
            + sha256(
                (self.identity + now.strftime("%Y%m%d%H") + "GalaxyApps").encode()
            ).hexdigest()[:7]
        )
        region = REGIONS[self.region]
        attrs = dict(
            networkType="0",
            version2="0",
            lang=region["lang"],
            openApiVersion="36",
            deviceModel="SM-S9480",
            deviceMakerName="samsung",
            deviceMakerType="0",
            mcc=region["mcc"],
            mnc=region["mnc"],
            csc=region["csc"],
            odcVersion="4.6.11.4",
            storeFilter="themeDeviceModel=SM-S9480_TM",
            supportFeature="",
            version="7.9",
            filter="1",
            odcType="01",
            storeMode="0",
            cacheVersion="1",
            systemId=str(int(time.time() * 1000)),
            sessionId=transaction + now.strftime("%Y%m%d%H%M"),
            logId=self.identity,
            deviceFeature=f"locale={region['lang']}||abi32=armeabi-v7a:armeabi||abi64=arm64-v8a",
            userMode="0",
            asaaMode="0",
        )
        root = ET.Element("SamsungProtocol", attrs)
        request = ET.SubElement(
            root,
            "request",
            name=method,
            id=request_id,
            numParam=str(len(params)),
            transactionId=transaction,
        )
        for key, value in params.items():
            ET.SubElement(request, "param", name=key).text = value
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def metadata(self, package):
        return self.envelope(
            "getDownloadInfo",
            "2298",
            dict(
                mode="directDownload",
                guid=package,
                productID="",
                imei=self.identity,
                extuk=self.identity,
                stduk=self.identity,
                predeployed="0",
                unifiedPaymentYN="Y",
                lkAppIncludedYN="Y",
                betaTestYN="N",
                minorYN="N",
                stateCode="",
            ),
        )

    def download(self, release, *, restore=False):
        params = dict(
            GUID=release.package,
            productID=release.product_id,
            imei=self.identity,
            extuk=self.identity,
            stduk=self.identity,
            autoUpdateYN="N",
            predeployed="0",
            resumeYN="N",
        )
        if restore:
            params.update(
                downloadType="new", triggeredFrom="DETAIL_PAGE", deepLinkSource=""
            )
            return self.envelope("downloadForRestore", "2316", params)
        # versionCode/loadType describe an installed base; omitting them asks
        # for a full APK. Samsung's 2311 parameter is spelled dowloadType.
        params.update(dowloadType="new", deepLinkSource="N")
        return self.envelope("downloadEx2", "2311", params)


    def details(self, release, *, overview=False):
        params = dict(GUID=release.package, productID=release.product_id,
                      imei=self.identity, stduk=self.identity, extuk=self.identity)
        if overview:
            params.update(imgWidth="1080", imgHeight="1920", runestoneYn="N", userAge="")
            return self.envelope("guidProductDetailExOverview", "2291", params)
        params.update(productImgWidth="135", productImgHeight="135", lkAppIncludedYN="Y",
                      predeployed="0", triggeredFrom="detail")
        return self.envelope("guidProductDetailExMain", "2290", params)

    def updates(self, releases):
        if not 1 <= len(releases) <= 120 or any(r.version_code <= 0 for r in releases):
            raise InvalidInput("更新检查缺少有效的版本信息。")
        return self.envelope("getUpdateList", "2389", dict(
            imgWidth="135", imgHeight="135",
            loadApp="||".join(f"{r.package}@@{r.version_code}@1@N" for r in releases),
            predeployed="0", justForCount="N", autoUpdateYN="N",
            imei=self.identity, stduk=self.identity, extuk=self.identity,
        ))


    def mirror(self, release):
        return self.envelope("downloadInfoForTencent", "2801", dict(
            stduk=self.identity, extuk=self.identity, GUID=release.package,
            tencentSource="general", lastInterfaceName="searchProductListEx2Notc",
        ))


def ods_lists(data, request_id, keys=None):
    """Read product lists without treating nested unrelated lists as product fields."""
    if not data or len(data) > 2_000_000:
        raise InvalidResponse("商店返回的信息为空或超出限制，请稍后重试。")
    try:
        root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ET.ParseError, DefusedXmlException, ValueError):
        raise InvalidResponse("商店返回的信息无法读取，请稍后重试。") from None
    responses = root.findall("response")
    if root.tag != "SamsungProtocol" or len(responses) != 1:
        raise InvalidResponse("商店返回的信息格式不正确，请稍后重试。")
    response = responses[0]
    if response.get("id", request_id) != request_id:
        raise InvalidResponse("商店返回的接口信息不一致，请稍后重试。")
    code = response.get("returnCode", "")
    if not re.fullmatch(r"-?[0-9]{1,19}", code):
        raise InvalidResponse("商店返回的信息无效，请稍后重试。")
    errors = response.findall("errorInfo/errorString")
    if len(errors) != 1:
        raise InvalidResponse("商店返回的信息不完整，请稍后重试。")
    check_ods_status(dict(errorCode=errors[0].get("errorCode", ""), errorString=(errors[0].text or "").strip()))
    if code != "0":
        raise ServiceError("商店拒绝了请求，请稍后重试。")
    rows = []
    for item in response.findall("list"):
        fields = {}
        for node in item:
            key = node.get("name", node.tag)
            if keys is not None and key not in keys:
                continue
            if node.tag != "value":
                if keys is not None and key in keys:
                    raise InvalidResponse("商店返回的信息格式不正确，请稍后重试。")
                continue
            if len(node) or key in fields:
                raise InvalidResponse("商店返回的信息存在冲突，请稍后重试。")
            fields[key] = (node.text or "") if key == "updateDescription" else (node.text or "").strip()
        rows.append(fields)
    return rows


def bound_main_details(data, release):
    keys = {"GUID", "productID", "version", "versionCode", "productName", "realContentsSize"}
    rows = ods_lists(data, "2290", keys)
    if len(rows) != 1:
        raise InvalidResponse("商店返回的应用详情不完整，请稍后重试。")
    fields = rows[0]
    if _identity(fields, release.package, "GUID", "productID") != release.product_id:
        raise InvalidResponse("商店返回的应用详情与所选应用不一致，请重试。")
    if required(fields, "version") != release.version_name or positive(fields, "versionCode") != release.version_code:
        raise VersionDrift("商店版本已发生变化，请重试。")
    if release.size is None or positive(fields, "realContentsSize") != release.size:
        raise VersionDrift("商店文件大小与所选版本不一致，请重试。")
    return fields


def match_ods_details(main, overview, confirmed_main, release):
    keys = {"GUID", "productID", "version", "versionCode", "productName", "realContentsSize", "lastUpdateDate", "updateDescription"}
    first = bound_main_details(main, release)
    bound_main_details(confirmed_main, release)
    rows = ods_lists(overview, "2291", keys)
    if len(rows) != 1:
        raise InvalidResponse("商店返回的应用详情不完整，请稍后重试。")
    fields = rows[0]
    # 2291 omits product identity/code. Stable matching 2290 responses surround
    # its version and full size so a concurrent store update cannot supply logs.
    if required(fields, "version") != release.version_name or positive(fields, "realContentsSize") != release.size:
        raise VersionDrift("商店版本已发生变化，请重试。")
    for key, expected in (("GUID", release.package), ("productID", release.product_id)):
        if key in fields and required(fields, key) != expected:
            raise VersionDrift("商店返回的应用详情与所选版本不一致，请重试。")
    if "versionCode" in fields and positive(fields, "versionCode") != release.version_code:
        raise VersionDrift("商店版本已发生变化，请重试。")
    value = fields.get("lastUpdateDate", "")
    if re.fullmatch(r"[0-9]{4};[0-9]{2};[0-9]{2};", value):
        try:
            date = datetime.strptime(value, "%Y;%m;%d;").date().isoformat()
        except ValueError:
            pass
        else:
            release = replace(release, updated_date=date)
    name = first.get("productName")
    if name:
        release = replace(release, name=name)
    return release, fields.get("updateDescription") or None


def parse_update_list(data, baselines, region):
    keys = {"GUID", "productID", "version", "versionCode", "productName", "realContentsSize", "realContentSize", "contentsSize", "needToLogin", "installableYN"}
    packages = {r.package for r in baselines}
    results = {}
    for fields in ods_lists(data, "2389", keys):
        package = required(fields, "GUID")
        if package not in packages or package in results:
            raise InvalidResponse("商店返回的更新列表与请求不一致，请重试。")
        _identity(fields, package, "GUID", "productID")
        required(fields, "version")
        positive(fields, "versionCode")
        size_key = "realContentSize" if "realContentSize" in fields else "realContentsSize"
        positive(fields, size_key)
        results[package] = dict(fields, region=region)
    return results


def match_cn_details(response, release):
    """Bind CN website details to the exact package/version; dates stay in CN."""
    if not isinstance(response, dict):
        return release, None
    detail = response.get("DetailMain")
    if not isinstance(detail, dict):
        return release, None
    if response.get("appId") != release.package or detail.get("countryCode") != "CHN":
        return release, None
    # The display name does not depend on the version; only replace labels.
    name = detail.get("contentName")
    if RELEASE_LABEL.match(release.name) and isinstance(name, str) and name.strip():
        release = replace(release, name=name.strip())
    if detail.get("contentBinaryVersion") != release.version_name:
        return release, None
    # modifyDate is the store's update date, not our query or link expiry time.
    # A CN date must not be presented as the US release's update date.
    value = detail.get("modifyDate")
    if release.region == "CN" and isinstance(value, str):
        try:
            updated_date = datetime.strptime(value.strip(), "%Y.%m.%d.").date()
        except ValueError:
            pass
        else:
            release = replace(release, updated_date=updated_date.isoformat())
    notes = detail.get("contentNewDescription")
    return release, notes.strip() if isinstance(notes, str) and notes.strip() else None
