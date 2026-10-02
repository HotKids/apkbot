"""Bounded Galaxy Store HTTP and APK download boundary."""

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import logging
from pathlib import Path
import tempfile
import time
from urllib.parse import urlencode, urljoin, urlsplit
from uuid import uuid4
import zipfile

import requests
from pyaxmlparser.axmlprinter import AXMLPrinter
from pyaxmlparser.axmlparser import AXMLParser
from pyaxmlparser import constants as axml_constants

import config
from galaxy_store import (
    AppRequest,
    DownloadError,
    InvalidResponse,
    LoginRequired,
    NoAvailableVersion,
    OdsProfile,
    Release,
    ServiceError,
    StoreError,
    TransportError,
    VersionDrift,
    match_cn_notes,
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
        raise InvalidResponse("商店返回了不合法的下载地址。") from None
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
        raise InvalidResponse("商店返回了未经允许的 HTTPS 地址。")
    return url


def open_response(session, method, url, *, deadline, **kwargs):
    for _ in range(REDIRECT_LIMIT + 1):
        validate_url(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransportError("商店请求超过总时限。")
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
                "无法连接 Galaxy Store，请检查网络和代理策略。"
            ) from None
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            # Never replay an ODS POST at another destination.
            if method != "GET" or not location:
                raise TransportError("商店返回了不允许的重定向。")
            url = urljoin(url, location)
            continue
        if response.status_code != 200:
            status = response.status_code
            response.close()
            raise TransportError(f"商店返回 HTTP {status}；不能据此认定应用不存在。")
        return response
    raise TransportError("商店重定向次数过多。")


def chunks(response, *, deadline, limit):
    count = 0
    try:
        for block in response.iter_content(64 * 1024):
            if time.monotonic() > deadline:
                raise TransportError("传输超过总时限。")
            count += len(block)
            if count > limit:
                raise DownloadError("响应超过允许的字节数。")
            yield block
    except requests.RequestException:
        raise TransportError("传输中断，请重新查询后重试。") from None


def request_bytes(session, method, url, **kwargs):
    deadline = time.monotonic() + config.REQUEST_TIMEOUT
    with open_response(session, method, url, deadline=deadline, **kwargs) as response:
        return b"".join(chunks(response, deadline=deadline, limit=METADATA_LIMIT))


@dataclass(frozen=True)
class Downloaded:
    release: Release
    path: Path
    sha256: str
    size: int


def manifest_identity(path):
    """Read only a bounded binary manifest, without extracting or installing APKs."""
    try:
        with zipfile.ZipFile(path) as archive:
            matches = [
                i for i in archive.infolist() if i.filename == "AndroidManifest.xml"
            ]
            if len(matches) != 1 or matches[0].file_size > 4_000_000:
                raise DownloadError("APK 缺少唯一且大小受限的清单。")
            data = archive.read(matches[0])  # ZIP CRC checked for the manifest.
        if not data.startswith(b"\x03\x00"):
            raise DownloadError("APK 清单不是 Android 二进制 XML。")
        # AXMLPrinter tolerates duplicate attributes and malformed nesting. Reject
        # those first, so our package/version interpretation cannot differ from
        # Android's parser merely because a later duplicate overwrote a value.
        raw_parser = AXMLParser(data)
        stack = []
        roots = 0
        for _ in range(200_000):
            if not raw_parser.is_valid():
                raise DownloadError("APK 清单结构不合法。")
            event = next(raw_parser)
            if event == axml_constants.START_TAG:
                if not stack:
                    roots += 1
                stack.append((raw_parser.namespace, raw_parser.name))
                if len(stack) > 256 or roots > 1:
                    raise DownloadError("APK 清单结构过于复杂。")
                seen = set()
                for index in range(raw_parser.getAttributeCount()):
                    key = (
                        raw_parser.getAttributeNamespace(index),
                        raw_parser.getAttributeName(index),
                    )
                    if key in seen:
                        raise DownloadError("APK 清单含有重复属性。")
                    seen.add(key)
            elif event == axml_constants.END_TAG:
                if not stack or stack.pop() != (raw_parser.namespace, raw_parser.name):
                    raise DownloadError("APK 清单标签不匹配。")
            elif event == axml_constants.END_DOCUMENT:
                if stack or roots != 1:
                    raise DownloadError("APK 清单未完整结束。")
                break
        else:
            raise DownloadError("APK 清单结构过于复杂。")
        parser = AXMLPrinter(data)
        if not parser.is_valid():
            raise DownloadError("APK 清单无法解析。")
        root = parser.get_xml_obj()
        ns = "{http://schemas.android.com/apk/res/android}"
        if root is None or root.tag != "manifest" or root.get("split"):
            raise DownloadError("不是受支持的完整 APK。")
        major = int(root.get(ns + "versionCodeMajor", "0"), 0)
        code_raw = root.get(ns + "versionCode", "")
        code = int(code_raw, 16 if code_raw.startswith("0x") else 10)
        return root.get("package"), root.get(ns + "versionName"), (major << 32) | code
    except DownloadError:
        raise
    except Exception:
        raise DownloadError("APK 清单损坏或无法读取。") from None


def download_apk(session, grant, directory):
    if not 0 < grant.size <= config.MAX_APK_BYTES:
        raise DownloadError("APK 大小超出允许范围。")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + config.DOWNLOAD_DEADLINE
    partial = None
    try:
        with open_response(session, "GET", grant.url, deadline=deadline) as response:
            encoding = response.headers.get("Content-Encoding", "identity").lower()
            if encoding not in ("", "identity"):
                raise DownloadError("APK 使用了不支持的内容编码。")
            length = response.headers.get("Content-Length")
            if length is not None and (
                not length.isdecimal() or int(length) != grant.size
            ):
                raise DownloadError("APK 长度与授权不匹配。")
            digest = sha256()
            count = 0
            with tempfile.NamedTemporaryFile(
                dir=directory, suffix=".part", delete=False
            ) as output:
                partial = Path(output.name)
                for block in chunks(response, deadline=deadline, limit=grant.size):
                    output.write(block)
                    digest.update(block)
                    count += len(block)
            if count != grant.size:
                raise DownloadError("APK 未完整下载。")
        if time.monotonic() > deadline:
            raise DownloadError("APK 下载超过总时限。")
        if manifest_identity(partial) != (
            grant.release.package,
            grant.release.version_name,
            grant.release.version_code,
        ):
            raise VersionDrift("APK 清单与选定版本不一致；未交付文件，请重新查询。")
        # Valid Android package IDs may already consume NAME_MAX. Keep the
        # complete identity in metadata while bounding the generated filename.
        name = f"{grant.release.package[:160]}_{grant.release.region}_{grant.release.version_code}_{uuid4().hex[:12]}.apk"
        final = directory / name
        partial.replace(final)
        return Downloaded(
            replace(grant.release, size=count), final, digest.hexdigest(), count
        )
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


def notes_from_response(data, release):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous website JSON")
            result[key] = value
        return result

    try:
        response = json.loads(data, object_pairs_hook=unique_object)
        return match_cn_notes(response, release)
    except (ValueError, UnicodeError, RecursionError):
        return None


class GalaxyStore:
    def __init__(self, session=None):
        self.session = session or new_session()
        self.profile = OdsProfile()

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
        return parse_stub(data, package, region)

    def metadata(self, app: AppRequest):
        if app.region != "CN":
            try:
                return self.stub(app.package, "US").release
            except NoAvailableVersion:
                if app.region == "US":
                    raise
        return parse_ods_metadata(
            self._ods("2298", self.profile.metadata(app.package)), app.package
        )

    def authorize(self, release):
        if release.needs_login:
            raise LoginRequired("该应用要求 Samsung 登录，不能匿名下载。")
        if not release.installable:
            raise ServiceError("商店标记该版本不可安装；不会自动切换地区。")
        if release.channel == "stub":
            grant = self.stub(release.package, release.region)
            if (
                grant.release.identity != release.identity
                or grant.release.version_name != release.version_name
            ):
                raise VersionDrift("商店版本已改变，请重新查询后下载。")
        else:
            grant = parse_ods_grant(
                self._ods("2316", self.profile.download(release)), release
            )
        validate_url(grant.url)
        return grant

    def download(self, release, directory):
        return download_apk(self.session, self.authorize(release), directory)

    def notes(self, release):
        try:
            data = request_bytes(
                self.session,
                "GET",
                "https://galaxystore.samsung.com/api/detail/"
                + release.package
                + "?cntyCd=CHN",
                headers={"Accept-Language": "zh-CN"},
            )
            return notes_from_response(data, release)
        except (StoreError, UnicodeError, ValueError):
            return None
