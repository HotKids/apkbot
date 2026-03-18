import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from config import (
    BASE_URL,
    DOWNLOAD_DIR,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from selector import Variant, config_from_env, select_best_variant

logger = logging.getLogger("apkmirror-bot")

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE = 1  # 秒；延迟依次为 1s, 2s, 4s

# Android (major, minor) → API 等级（含 .1 小版本）
_ANDROID_API_MAP: dict[tuple[int, int], int] = {
    (4, 0): 14, (4, 1): 16, (4, 2): 17, (4, 3): 18, (4, 4): 19,
    (5, 0): 21, (5, 1): 22,
    (6, 0): 23,
    (7, 0): 24, (7, 1): 25,
    (8, 0): 26, (8, 1): 27,
    (9, 0): 28,
    (10, 0): 29, (11, 0): 30, (12, 0): 31,
    (13, 0): 33, (14, 0): 34, (15, 0): 35,
}

_KNOWN_ARCHITECTURES = ["arm64-v8a", "armeabi-v7a", "x86_64", "x86", "universal"]
_KNOWN_DPI = ["nodpi", "160dpi", "240dpi", "320dpi", "480dpi", "640dpi"]

# Variant 数据类由 selector 模块提供（避免重复定义）


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------

def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE_URL + "/",
        }
    )
    return s


def session_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """带重试的 GET 请求（最多 3 次，指数退避 1s/2s/4s）。

    重试：ConnectionError、Timeout、HTTP 5xx
    不重试：HTTP 4xx（直接抛出）
    """
    last_exc: Exception = RuntimeError("unreachable")
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code < 500:
                raise  # 4xx 不重试
            last_exc = e
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
        if attempt < _RETRY_ATTEMPTS - 1:
            wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.warning(
                "请求失败（第 %d/%d 次），%ds 后重试：%s",
                attempt + 1, _RETRY_ATTEMPTS, wait, url,
            )
            time.sleep(wait)
    raise last_exc


# ---------------------------------------------------------------------------
# APK_URL 类型检测 & release 页获取
# ---------------------------------------------------------------------------

def is_release_url(url: str) -> bool:
    """判断 URL 是否为 release 页（以 -release/ 结尾）。"""
    return bool(re.search(r"-release/?$", url.rstrip("/")))


def get_release_url(session: requests.Session, apk_url: str) -> str:
    """若 apk_url 已是 release 页则直接返回；否则从 app 列表页抓取最新 release 链接。"""
    if is_release_url(apk_url):
        return apk_url.rstrip("/") + "/"
    r = session_get(session, apk_url)
    soup = BeautifulSoup(r.text, "lxml")
    # 精准定位主列表，规避侧边栏"热门下载"中的旧版本链接
    for a in soup.select(".listWidget .appRow a.fontBlack, .appRow > div > a.fontBlack"):
        href = a.get("href", "")
        if href and is_release_url(href):
            return urljoin(BASE_URL, href)
    # 降级：全页搜索（CSS 结构变更时的保底）
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if href and is_release_url(href):
            return urljoin(BASE_URL, href)
    raise RuntimeError(f"无法在以下页面找到 release 链接：{apk_url}")


# ---------------------------------------------------------------------------
# Variant 解析辅助函数
# ---------------------------------------------------------------------------

def _extract_app_name(soup: BeautifulSoup) -> str:
    h = soup.find("h1")
    if h:
        return h.get_text(" ", strip=True)
    title = soup.find("title")
    if title:
        return title.get_text(" ", strip=True).split("|")[0].strip()
    return "Unknown App"


def _extract_release_version(release_url: str) -> str:
    m = re.search(r"-(\d[\d\.\-]+)-release", release_url)
    if m:
        return m.group(1).replace("-", ".")
    return "unknown"


def _is_variant_link(href: str, release_url: str) -> bool:
    """判断链接是否为 release 页下的某个 variant（子页面）。"""
    base = release_url.rstrip("/")
    clean_href = href.rstrip("/")
    if clean_href == base:
        return False
    # variant 页 href 通常包含 release 路径片段，且有更深的路径
    if base.split("/apk/", 1)[-1].rstrip("/") not in href:
        return False
    if not href.endswith("/") and not re.search(r"/[^/]+-\d+[^/]*/?$", href):
        return False
    # 排除"汇总下载页"（URL 末段仅含短版本号，如 50-5-19）；
    # 真实 variant 的 URL 含 build 码（6 位以上数字，如 pr-160218067）
    last_segment = clean_href.split("/")[-1]
    if not re.search(r"\d{6,}", last_segment):
        return False
    return True


def _find_row(tag: Tag) -> Optional[Tag]:
    """向上找最近的 table 行元素（.table-row 或 tr）。"""
    p = tag.parent
    for _ in range(8):
        if p is None:
            break
        if isinstance(p, Tag):
            classes = p.get("class") or []
            if "table-row" in classes or p.name == "tr":
                return p
        p = p.parent if isinstance(p, Tag) else None
    return None


def _parse_signatures(text: str) -> list[str]:
    """从 raw_text 提取 4 位小写十六进制串（APKMirror 签名格式），排除年份干扰。"""
    sigs = re.findall(r"\b[0-9a-f]{4}\b", text.lower())
    return list(dict.fromkeys(s for s in sigs if not re.match(r"^20[1-3]\d$", s)))


def _parse_architectures(text: str) -> list[str]:
    found = [a for a in _KNOWN_ARCHITECTURES if a in text.lower()]
    return found if found else []


def _parse_android_text(text: str) -> Optional[str]:
    m = re.search(r"Android\s+[\d\.]+\+?", text, re.IGNORECASE)
    return m.group(0) if m else None


def _parse_android_api(text: str) -> Optional[int]:
    m = re.search(r"Android\s+(\d+)(?:\.(\d+))?", text, re.IGNORECASE)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2)) if m.group(2) else 0
        return _ANDROID_API_MAP.get((major, minor), _ANDROID_API_MAP.get((major, 0)))
    return None


def _parse_dpi(text: str) -> Optional[str]:
    lower = text.lower()
    for d in _KNOWN_DPI:
        if d in lower:
            return d
    m = re.search(r"\d+-\d+dpi", lower)
    if m:
        return m.group(0)
    return None


def _parse_version_code(text: str) -> Optional[int]:
    # APKMirror 通常在括号内显示版本号，如 (519684117)
    m = re.search(r"\((\d{6,})\)", text)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# 主解析函数
# ---------------------------------------------------------------------------

def parse_variants(session: requests.Session, release_url: str) -> list[Variant]:
    """解析 release 页的全部 variant，返回结构化列表。"""
    r = session_get(session, release_url)
    soup = BeautifulSoup(r.text, "lxml")

    app_name = _extract_app_name(soup)
    release_version = _extract_release_version(release_url)
    variants: list[Variant] = []
    seen_urls: set[str] = set()

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href or href in seen_urls:
            continue
        if not _is_variant_link(href, release_url):
            continue

        # 收集 raw_text（从链接向上 5 层父节点）
        raw_parts = [a.get_text(" ", strip=True)]
        p = a.parent
        for _ in range(5):
            if p is None:
                break
            if isinstance(p, Tag):
                raw_parts.append(p.get_text(" ", strip=True))
                p = p.parent
            else:
                break
        raw_text = " ".join(filter(None, raw_parts))

        # 仅在行级作用域检测类型，避免将同表格其他行的 "BUNDLE" 文字误判到本行
        row = _find_row(a)
        row_text = row.get_text(" ", strip=True) if row else raw_text
        is_bundle = "BUNDLE" in row_text
        archs = _parse_architectures(raw_text)
        dpi = _parse_dpi(raw_text)
        android_text = _parse_android_text(raw_text)
        android_api = _parse_android_api(raw_text)

        v = Variant(
            app_name=app_name,
            release_version_name=release_version,
            version_code=_parse_version_code(raw_text),
            variant_label=a.get_text(" ", strip=True),
            type="BUNDLE" if is_bundle else "APK",
            is_bundle=is_bundle,
            signatures=_parse_signatures(raw_text),
            architectures=archs,
            min_android_text=android_text,
            min_android_api=android_api,
            dpi=dpi,
            device_type="universal" if "universal" in raw_text.lower() else None,
            release_url=release_url,
            variant_url=urljoin(BASE_URL, href),
            raw_text=raw_text,
        )
        seen_urls.add(href)
        variants.append(v)

    return variants


# ---------------------------------------------------------------------------
# 过滤 & 打分 & 选择（委托给 selector 模块）
# ---------------------------------------------------------------------------

def scrape_and_pick(session: requests.Session, apk_url: str) -> Variant:
    """完整抓取 + 过滤 + 打分，返回最佳 Variant（未下载）。"""
    release_url = get_release_url(session, apk_url)
    logger.info("Release 页：%s", release_url)
    all_variants = parse_variants(session, release_url)
    logger.info("共解析 %d 个 variant", len(all_variants))
    if not all_variants:
        raise RuntimeError("Release 页未找到任何 variant")

    cfg = config_from_env(os.environ)
    best = select_best_variant(all_variants, cfg)
    if best is None:
        raise RuntimeError(f"没有 variant 通过过滤条件（共 {len(all_variants)} 个）。")
    logger.info(
        "选中：%s | %s | %s | api=%s | dpi=%s",
        best.variant_label, best.type,
        best.architectures, best.min_android_api,
        best.dpi,
    )
    return best


# ---------------------------------------------------------------------------
# 下载流程
# ---------------------------------------------------------------------------

def resolve_download_page(session: requests.Session, variant_url: str) -> str:
    r = session_get(session, variant_url)
    soup = BeautifulSoup(r.text, "lxml")

    # 1. 精准匹配 APKMirror 的主下载按钮
    download_btn = soup.select_one("a.downloadButton")
    if download_btn and download_btn.get("href"):
        return urljoin(BASE_URL, download_btn.get("href"))

    # 2. 备用：寻找带有 /download/?key= 的链接
    for a in soup.select('a[href*="/download/?key="]'):
        return urljoin(BASE_URL, a.get("href"))

    raise RuntimeError("无法在 Variant 页面找到下载页入口")


def resolve_final_apk_url(session: requests.Session, download_page_url: str) -> str:
    r = session_get(session, download_page_url)
    soup = BeautifulSoup(r.text, "lxml")

    # 1. 真正的直链通常带有 download.php?id= (APKMirror 真实的下载网关)
    for a in soup.select('a[href*="download.php?id="]'):
        return urljoin(BASE_URL, a.get("href"))

    # 2. 寻找 "click here" 提示中的链接
    for a in soup.select("a[href]"):
        text = a.get_text(" ", strip=True).lower()
        href = a.get("href", "")
        if "here" in text and "key=" in href:
            return urljoin(BASE_URL, href)

    raise RuntimeError("无法在中间页解析出真实的 APK 直链，可能是页面结构变更或触发了反爬")


def download_file(session: requests.Session, file_url: str, fallback_name: str) -> Path:
    with session.get(file_url, stream=True, timeout=300, allow_redirects=True) as r:
        r.raise_for_status()

        # 终极防御：如果服务器返回的是 HTML 网页，直接报错拦截
        content_type = r.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            raise RuntimeError(
                f"下载失败：获取到了 HTML 网页而非安装包 (Content-Type: {content_type})。"
                "大概率触发了反爬或抓取了错误链接。"
            )

        filename = None
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)"?', cd)
        if m:
            filename = m.group(1).strip()
        if not filename:
            url_name = file_url.split("/")[-1].split("?")[0]
            _SKIP = {"download", "download.php"}
            filename = url_name if url_name and url_name.lower() not in _SKIP else fallback_name
        out_path = DOWNLOAD_DIR / filename
        with out_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)
    return out_path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_and_download(session: requests.Session, variant: Variant) -> tuple[Path, str]:
    """解析 variant 的最终下载链接并下载，返回 (apk_path, sha256)。"""
    download_page = resolve_download_page(session, variant.variant_url)
    variant.download_page_url = download_page
    final_url = resolve_final_apk_url(session, download_page)
    variant.final_download_url = final_url
    logger.info("开始下载：%s", final_url)
    raw_name = f"{variant.app_name}_{variant.variant_label}"
    safe_name = re.sub(r'\s+', "_", re.sub(r'[\\/*?:"<>|]', "_", raw_name))
    ext = ".apkm" if variant.is_bundle else ".apk"
    fallback_name = f"{safe_name}{ext}"
    apk_path = download_file(session, final_url, fallback_name)
    file_hash = sha256_file(apk_path)
    logger.info("下载完成：%s (%.2f MB, sha256=%s...)",
                apk_path.name, apk_path.stat().st_size / 1024 / 1024, file_hash[:12])
    return apk_path, file_hash


# ---------------------------------------------------------------------------
# 包名 → APKMirror URL 解析
# ---------------------------------------------------------------------------

def resolve_package_to_apkmirror_url(session: requests.Session, package_name: str) -> Optional[str]:
    """通过包名搜索 APKMirror，返回该应用的 base URL。"""
    search_url = f"{BASE_URL}/?searchtype=app&s={package_name}"
    r = session_get(session, search_url)
    # 1. 直接重定向到应用主页
    if re.match(r"^https?://(www\.)?apkmirror\.com/apk/[^/]+/[^/]+/?$", r.url):
        return r.url
    # 2. 搜索结果页中提取
    soup = BeautifulSoup(r.text, "lxml")
    for a in soup.select("a.fontBlack"):
        href = a.get("href", "")
        if re.match(r"^/apk/[^/]+/[^/]+/?$", href):
            return urljoin(BASE_URL, href)
    # 3. 降级：全页搜索
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if re.match(r"^/apk/[^/]+/[^/]+/?$", href):
            return urljoin(BASE_URL, href)
    return None


# ---------------------------------------------------------------------------
# 文件清理
# ---------------------------------------------------------------------------

def cleanup_after_push(apk_path: Path) -> None:
    """推送成功后删除 APK 文件。"""
    try:
        apk_path.unlink(missing_ok=True)
        logger.info("已删除 APK：%s", apk_path.name)
    except OSError:
        logger.exception("删除 APK 失败：%s", apk_path)
