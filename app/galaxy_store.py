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


class InvalidResponse(StoreError):
    pass


class DownloadError(StoreError):
    pass


class VersionDrift(DownloadError):
    pass


USAGE = "请发送包名或 Galaxy Store 详情链接，可在后面加地区 CN 或 US，例如：com.lucky.luckyclient CN"
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
# Samsung lists some of its own US builds under release labels such as
# "[260921] GALAXY Store 9CR Update -  US" (stub and US web page alike).
RELEASE_LABEL = re.compile(r"\[\d+\]\s")


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
                "无法识别这个链接，请发送 Galaxy Store 应用详情页链接。"
            ) from None
        # Share links append ?session_id=…; only the path names the package.
        if (
            u.scheme != "https"
            or u.netloc != "galaxystore.samsung.com"
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


def xml_fields(data: bytes, root_name: str):
    if not data or len(data) > 2_000_000:
        raise InvalidResponse("商店数据异常（响应为空或过大），请稍后重试。")
    try:
        root = fromstring(
            data, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except (ET.ParseError, DefusedXmlException, ValueError):
        raise InvalidResponse("商店数据异常（无法解析），请稍后重试。") from None
    if root.tag != root_name:
        raise InvalidResponse("商店数据异常（结构不符），请稍后重试。")
    fields = {}
    for node in root.iter():
        if len(node):
            continue
        key = node.attrib.get("name", node.tag)
        if key in fields:
            raise InvalidResponse("商店数据异常（字段重复），请稍后重试。")
        fields[key] = (node.text or "").strip()
        if key == "errorString" and "errorCode" in node.attrib:
            if "errorCode" in fields:
                raise InvalidResponse("商店数据异常（错误码重复），请稍后重试。")
            fields["errorCode"] = node.attrib["errorCode"]
    return fields


def required(fields, key):
    value = fields.get(key, "")
    if not value:
        raise InvalidResponse(f"商店数据异常（缺少 {key}），请稍后重试。")
    return value


def positive(fields, key):
    raw = required(fields, key)
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 19 or int(raw) <= 0:
        raise InvalidResponse(f"商店数据异常（{key} 无效），请稍后重试。")
    return int(raw)


def _identity(fields, package, guid_key, product_key):
    if required(fields, guid_key) != package:
        raise InvalidResponse("商店数据异常（包名不符），请稍后重试。")
    product = required(fields, product_key)
    if not re.fullmatch(r"[0-9]{1,30}", product):
        raise InvalidResponse("商店数据异常（产品 ID 无效），请稍后重试。")
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
            "US 商店不提供此应用的匿名下载，这不代表应用不存在；可改用 CN 再试。"
        )
    # Device/carrier/profile mismatch and unknown responses are not absence.
    if message == "application is not available in this country":
        raise NoAvailableVersion("此应用在所选地区不可用，可换个地区再试。")
    if "login" in message or "log in" in message:
        raise LoginRequired("此应用需要登录 Samsung 账号才能下载，bot 无法匿名获取。")
    raise ServiceError("US 商店没有返回可用结果，可改用 CN 或稍后再试。")


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
    if code != "0" or status.casefold() not in {"", "success"}:
        if "login" in status.casefold() or "log in" in status.casefold():
            raise LoginRequired(
                "此应用需要登录 Samsung 账号才能下载，bot 无法匿名获取。"
            )
        raise ServiceError("CN 商店拒绝了请求，这不代表应用不存在；请稍后重试。")


def parse_ods_metadata(data, package):
    fields = xml_fields(data, "SamsungProtocol")
    check_ods_status(fields)
    product = _identity(fields, package, "GUID", "productID")
    login = required(fields, "needToLogin")
    installable = required(fields, "installableYN")
    if login not in {"0", "1"} or installable not in {"Y", "N"}:
        raise InvalidResponse("商店数据异常（登录或安装状态无效），请稍后重试。")
    return Release(
        package,
        "CN",
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


def parse_ods_grant(data, release):
    fields = xml_fields(data, "SamsungProtocol")
    check_ods_status(fields)
    if required(fields, "productID") != release.product_id:
        raise InvalidResponse("商店数据异常（下载授权的产品 ID 不符），请稍后重试。")
    if "GUID" in fields and fields["GUID"] != release.package:
        raise InvalidResponse("商店数据异常（下载授权的包名不符），请稍后重试。")
    # binaryArch describes CPU coverage (e.g. 32n64), not full vs. delta APKs.
    # Use the full download's downLoadURI and contentsSize below.
    if "version" in fields and fields["version"] != release.version_name:
        raise VersionDrift("商店版本刚刚更新，请再试一次。")
    if (
        "versionCode" in fields
        and positive(fields, "versionCode") != release.version_code
    ):
        raise VersionDrift("商店版本刚刚更新，请再试一次。")
    size = positive(fields, "contentsSize")
    if release.size is not None and size != release.size:
        raise VersionDrift("下载授权与所选版本的大小不符，请再试一次。")
    return DownloadGrant(release, required(fields, "downLoadURI"), size)


@dataclass
class OdsProfile:
    identity: str = field(default_factory=lambda: uuid4().hex[:16], repr=False)

    def envelope(self, method, request_id, params):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        transaction = (
            str(now.day)
            + sha256(
                (self.identity + now.strftime("%Y%m%d%H") + "GalaxyApps").encode()
            ).hexdigest()[:7]
        )
        attrs = dict(
            networkType="0",
            version2="0",
            lang="zh_CN",
            openApiVersion="36",
            deviceModel="SM-S9480",
            deviceMakerName="samsung",
            deviceMakerType="0",
            mcc="460",
            mnc="00",
            csc="CHC",
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
            deviceFeature="locale=zh_CN||abi32=armeabi-v7a:armeabi||abi64=arm64-v8a",
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

    def download(self, release):
        return self.envelope(
            "downloadForRestore",
            "2316",
            dict(
                GUID=release.package,
                productID=release.product_id,
                imei=self.identity,
                extuk=self.identity,
                stduk=self.identity,
                downloadType="new",
                autoUpdateYN="N",
                triggeredFrom="DETAIL_PAGE",
                predeployed="0",
                deepLinkSource="",
                resumeYN="N",
            ),
        )


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
