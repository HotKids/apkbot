import hashlib
import logging
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from pypinyin import lazy_pinyin
try:
    from curl_cffi import requests as _cffi_requests
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _cffi_requests = None
    _CURL_CFFI_AVAILABLE = False
from bs4 import BeautifulSoup, Tag

from config import (
    BASE_URL,
    DOWNLOAD_DIR,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from selector import Variant, config_from_env, select_best_variant

logger = logging.getLogger("apkdl-bot")

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

def new_session():
    """创建 HTTP session。优先使用 curl_cffi（Chrome TLS 指纹，可绕过 Cloudflare），
    不可用时降级为标准 requests.Session。"""
    if _CURL_CFFI_AVAILABLE:
        # impersonate="chrome120" 使用 Chrome 120 的完整 TLS/JA3/HTTP2 指纹
        s = _cffi_requests.Session(impersonate="chrome120")
        # curl_cffi 会自动设置 Chrome UA，只追加 Accept-Language/Referer
        s.headers.update({
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE_URL + "/",
        })
        logger.info("HTTP session: curl_cffi Chrome120 impersonation")
    else:
        s = requests.Session()
        s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE_URL + "/",
        })
        logger.warning("HTTP session: curl_cffi 不可用，降级为 requests（可能被 Cloudflare 拦截）")
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
    """判断链接是否为 release 页下的某个 variant（子页面）。

    规则：href 必须比 release_url 恰好深一层路径，且包含 release 路径片段。
    用路径深度代替"6 位数字"启发式，以兼容无大版本号的 app（如微信）。
    """
    base = release_url.rstrip("/")
    clean_href = href.rstrip("/")
    if clean_href == base:
        return False
    # 必须包含 release 路径片段（排除无关链接）
    if base.split("/apk/", 1)[-1].rstrip("/") not in href:
        return False
    # 必须恰好比 release 页深一层（排除 /download/、/variant/download/ 等子页面）
    base_path = "/" + base.split("://")[-1].split("/", 1)[-1]
    suffix = clean_href[len(base_path):]
    inner = suffix.lstrip("/")
    if not inner or "/" in inner:
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

def fetch_app_name_from_rss(session: requests.Session, apk_url: str) -> str:
    """从 APKMirror RSS channel title 提取 App 名。
    channel title 格式：'Download {AppName} APKs for Android – APKMirror'
    失败时回退到 URL 路径段。"""
    import xml.etree.ElementTree as ET
    rss_url = apk_url.rstrip("/") + "/feed/"
    try:
        r = session.get(rss_url, timeout=10,
                        headers={"Accept": "application/rss+xml, text/xml, */*"})
        root = ET.fromstring(r.content)
        channel = root.find("channel")
        if channel is None:
            raise ValueError("no channel")
        title = (channel.findtext("title") or "").strip()
        # "Download WeChat APKs for Android – APKMirror" → "WeChat"
        title = re.sub(r"^Download\s+", "", title, flags=re.I)
        title = re.sub(r"\s+APKs?\s+for\s+Android.*$", "", title, flags=re.I)
        if title:
            return title
    except Exception as e:
        logger.debug("RSS app name fetch 失败 %s: %s", apk_url, e)
    # 回退：URL 末尾路径段转 Title Case
    seg = apk_url.rstrip("/").split("/")[-1]
    return seg.replace("-", " ").title()


def fetch_rss_latest_release_url(session: requests.Session, apk_url: str) -> Optional[str]:
    """从 APKMirror RSS feed 获取最新一条 release 的页面 URL。
    失败时返回 None，调用方回退到完整 HTML 抓取流程。"""
    import xml.etree.ElementTree as ET
    rss_url = apk_url.rstrip("/") + "/feed/"
    try:
        r = session.get(rss_url, timeout=10,
                        headers={"Accept": "application/rss+xml, text/xml, */*"})
        root = ET.fromstring(r.content)
        channel = root.find("channel")
        item = channel.find("item") if channel is not None else None
        if item is None:
            return None
        link = (item.findtext("link") or "").strip()
        return link.rstrip("/") + "/" if link else None
    except Exception as e:
        logger.debug("RSS fetch 失败 %s: %s", apk_url, e)
        return None


def _release_path_from_variant_url(variant_url: str) -> str:
    """从 variant URL 推导上一级的 release URL 路径。
    e.g. /apk/foo/bar/bar-1-0-release/bar-1-0-download/ → /apk/foo/bar/bar-1-0-release/"""
    return "/".join(variant_url.rstrip("/").split("/")[:-1]) + "/"


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


def _extract_post_id(soup: BeautifulSoup, html: str) -> Optional[str]:
    """从 WordPress 页面提取文章 ID（用于构造 download.php?id= 链接）。"""
    # 1. <body class="... postid-12345 ...">
    body = soup.find("body")
    if body:
        classes = body.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        for cls in classes:
            m = re.match(r"^postid-(\d+)$", cls)
            if m:
                return m.group(1)
    # 2. <link rel="shortlink" href="/?p=12345">
    shortlink = soup.find("link", rel="shortlink")
    if shortlink:
        m = re.search(r"[?&]p=(\d+)", shortlink.get("href", ""))
        if m:
            return m.group(1)
    # 3. 原始 HTML 中的 "post_id":12345 / "postid":12345
    m = re.search(r'"post(?:id|_id)"\s*:\s*"?(\d+)"?', html)
    if m:
        return m.group(1)
    return None




def _extract_js_str(html: str, key: str) -> Optional[str]:
    """从页面内嵌 JS 中提取指定 key 的字符串/数字值。"""
    for pattern in [
        rf'"{re.escape(key)}"\s*:\s*"([^"]+)"',
        rf"'{re.escape(key)}'\s*:\s*'([^']+)'",
        rf'"{re.escape(key)}"\s*:\s*(\d+)',
        rf"var\s+{re.escape(key)}\s*=\s*['\"]([^'\"]+)['\"]",
    ]:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def _extract_wp_nonce(html: str) -> Optional[str]:
    """从 HTML 中提取 WordPress nonce（含 _wpnonce 和 nonce 变量，支持注释中的值）。"""
    for pattern in [
        r'"nonce"\s*:\s*"([a-f0-9]+)"',
        r"'nonce'\s*:\s*'([a-f0-9]+)'",
        r"var\s+nonce\s*=\s*['\"]([a-f0-9]+)['\"]",
        # _wpnonce 可能在注释（/**...*/）或赋值中
        r"_wpnonce['\",\s:=/\*]+\s*['\"]([a-f0-9]+)['\"]",
    ]:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def _extract_ajaxurl(html: str) -> str:
    """从页面中提取 WordPress ajaxurl（可能带 /wordpress/ 前缀）。"""
    m = re.search(r"ajaxurl\s*=\s*['\"]([^'\"]+)['\"]", html)
    return m.group(1) if m else "/wp-admin/admin-ajax.php"


# 运行时缓存：JS 文件 URL → 找到的 AJAX action 列表，避免重复拉取
_download_action_cache: dict[str, list[str]] = {}
# 记忆成功跑通的 action，以后直接优先用它，0 延迟！
_KNOWN_SUCCESSFUL_ACTION: str = ""


def _fetch_apkm_download_actions(
    session: requests.Session, page_html: str, referer: str
) -> list[str]:
    """从确认页 HTML 内联代码及外部 JS 文件中，提取所有可能的字符串。"""
    actions = []
    # 终极启发式正则：不再要求包含 action，直接提取所有 6~50 位、只有字母/数字/下划线的字符串
    pattern = r'["\']([a-zA-Z_][a-zA-Z0-9_-]{4,49})["\']'

    # 1. 搜索 HTML 内联脚本
    soup = BeautifulSoup(page_html, "lxml")
    for tag in soup.find_all("script"):
        if not tag.get("src") and tag.string:
            actions.extend(re.findall(pattern, tag.string))

    # 2. 搜索外部 JS 文件
    for tag in soup.select("script[src]"):
        src = tag.get("src", "")
        if not src or "apkmirror.com" not in src:
            continue
        # 跳过已知无关的公共库和广告库，只刮削核心 JS
        src_lower = src.lower()
        if any(lib in src_lower for lib in ("jquery", "lodash", "bootstrap", "recaptcha", "cmp.inmobi")):
            continue
        full_src = urljoin(BASE_URL, src)
        if full_src in _download_action_cache:
            actions.extend(_download_action_cache[full_src])
            continue
        try:
            r = session.get(full_src, timeout=10, headers={"Referer": referer})
            found = re.findall(pattern, r.text)
            _download_action_cache[full_src] = found
            actions.extend(found)
        except Exception as e:
            logger.debug("获取 JS 文件失败 %s: %s", full_src, e)

    return list(dict.fromkeys(actions))


def resolve_final_apk_url(session: requests.Session, download_page_url: str, referer_url: str = "") -> str:
    # 核心：必须携带上一步 Variant 页面的 Referer，否则 APKMirror 隐藏下载信息
    logger.info("请求 Download Page: %s", download_page_url)
    req_headers = {"Referer": referer_url} if referer_url else {}
    r = session_get(session, download_page_url, headers=req_headers)
    soup = BeautifulSoup(r.text, "lxml")
    html = r.text
    html_clean = html.replace("\\/", "/").replace("\\u002F", "/")

    dl_php_url = None

    # 1. 寻找直接暴露的 download.php 链接
    for a in soup.select('a[href*="download.php"]'):
        dl_php_url = urljoin(BASE_URL, a.get("href"))
        break

    if not dl_php_url:
        for a in soup.select("a[href]"):
            text = a.get_text(" ", strip=True).lower()
            href = a.get("href", "")
            if "here" in text and "key=" in href:
                dl_php_url = urljoin(BASE_URL, href)
                break

    if not dl_php_url:
        m = re.search(r'(/wp-content/themes/APKMirror/download\.php[^"\'<>\s\\]+)', html_clean)
        if m:
            dl_php_url = urljoin(BASE_URL, m.group(1))

    # 2. 页面无直链时，手动组装 download.php
    if not dl_php_url:
        key_m = re.search(r"[?&]key=([a-f0-9]+)", download_page_url)
        post_id = _extract_post_id(soup, html)
        if key_m and post_id:
            forcebase = "&forcebaseapk=true" if "forcebaseapk" in download_page_url else ""
            dl_php_url = urljoin(
                BASE_URL,
                f"/wp-content/themes/APKMirror/download.php"
                f"?id={post_id}&key={key_m.group(1)}{forcebase}",
            )

    # 3. 解析 download.php，深度抽取 CDN 直链（带 download_page Referer）
    if dl_php_url:
        logger.info("抓取到 download.php，开始深度解剖获取 CDN 链接: %s", dl_php_url)
        try:
            r_dl = session.get(
                dl_php_url,
                headers={"Referer": download_page_url},
                timeout=20,
                allow_redirects=True,
            )
            ct = r_dl.headers.get("Content-Type", "").lower()
            # 若服务器经 302 直接给了文件，返回落地 URL
            if "application/" in ct or "zip" in ct or "octet-stream" in ct:
                return r_dl.url
            # HTML 过渡页：暴力抽 CDN 链接
            h = r_dl.text
            soup_dl = BeautifulSoup(h, "lxml")
            # 策略A：APKMirror CDN 固定特征
            cdn_m = re.search(
                r'href=["\'](https?://[a-zA-Z0-9.-]*apkmirror\.com/wp-content/uploads/[^"\']+)["\']', h
            )
            if cdn_m:
                return cdn_m.group(1)
            # 策略B：Meta 自动刷新
            for meta in soup_dl.select("meta[http-equiv]"):
                if "refresh" in (meta.get("http-equiv") or "").lower():
                    m2 = re.search(r"url=([^\s;,]+)", meta.get("content", ""), re.I)
                    if m2:
                        return urljoin(BASE_URL, m2.group(1).strip("'\""))
            # 策略C：含 "here"/"download" 文字的 APK 链接
            for a in soup_dl.select("a[href]"):
                text = a.get_text(" ", strip=True).lower()
                href = a.get("href", "")
                if ("here" in text or "download" in text) and (
                    "downloadr" in href or "uploads" in href
                    or re.search(r"\.(apk|apkm|xapk)(\?|$)", href, re.I)
                ):
                    return urljoin(BASE_URL, href)
            # 策略D：全局泛匹配
            m3 = re.search(
                r'(https?://[^\s"\'<>]+\.(?:apk|apkm|xapk)(?:\?[^\s"\'<>]*)?)(?=["\'\s<>]|$)',
                h, re.I,
            )
            if m3:
                return m3.group(1)
            logger.warning("download.php 返回了无特征 HTML，将回退至 AJAX 暴力尝试")
        except Exception as e:
            logger.warning("请求 download.php 失败：%s", e)

    # 4. AJAX 终极兜底（暴力探索 + 特征打分 + 成功 action 记忆）
    global _KNOWN_SUCCESSFUL_ACTION
    _key_m = re.search(r"[?&]key=([a-f0-9]+)", download_page_url)
    _post_id = _extract_post_id(soup, html)
    if _key_m and _post_id:
        _dl_key = _key_m.group(1)
        _forcebase = "true" if "forcebaseapk" in download_page_url else "false"
        _ajax_base = _extract_ajaxurl(html)
        _ajax_full = urljoin(BASE_URL, _ajax_base)
        _nonce = _extract_wp_nonce(html)

        _js_actions = _fetch_apkm_download_actions(session, html, download_page_url)

        def action_score(s: str) -> int:
            score = 0
            s_lower = s.lower()
            if "apkm" in s_lower: score += 10
            if "download" in s_lower: score += 10
            if "ajax" in s_lower: score += 5
            if "key" in s_lower: score += 5
            if "gen" in s_lower: score += 5
            return -score

        _js_actions.sort(key=action_score)
        _actions = []
        if _KNOWN_SUCCESSFUL_ACTION:
            _actions.append(_KNOWN_SUCCESSFUL_ACTION)
        _actions.extend([
            "apkm_generate_download_key_ajax",
            "generate_download_key_ajax",
            "get_download_key",
            "apkm_download_v2",
            "apkm_download",
        ])
        _actions.extend(_js_actions)
        _actions = list(dict.fromkeys(_actions))[:30]

        logger.info("Step4 AJAX 兜底，尝试 %d 个 Action", len(_actions))
        for _action in _actions:
            _data: dict = {
                "action": _action,
                "id": _post_id,
                "key": _dl_key,
                "forcebaseapk": _forcebase,
            }
            if _nonce:
                _data["nonce"] = _nonce
                _data["_wpnonce"] = _nonce
            try:
                _resp = session.post(
                    _ajax_full, data=_data, timeout=10,
                    headers={
                        "Referer": download_page_url,
                        "Origin": BASE_URL,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                _txt = _resp.text.strip()
                if _resp.ok and _txt.startswith("{"):
                    _d = _resp.json()
                    _url = (
                        _d.get("url") or _d.get("download_url")
                        or _d.get("link")
                        or (_d.get("data") or {}).get("url")
                    )
                    if _url:
                        _KNOWN_SUCCESSFUL_ACTION = _action
                        logger.info("✅ 成功匹配到正确 AJAX action: %s", _action)
                        return _url.replace("\\/", "/")
            except Exception:
                pass

    raise RuntimeError("无法解析真实的 APK 直链，防盗链和反爬手段已阻断抓取。")


def download_file(session: requests.Session, file_url: str, fallback_name: str, referer: str = "") -> Path:
    # 必须带上 Referer 以免下载被防盗链阻断（报 403 / HTML）
    req_headers = {"Referer": referer} if referer else {}
    r = session.get(file_url, stream=True, timeout=300, allow_redirects=True, headers=req_headers)
    r.raise_for_status()

    # 终极防御：如果服务器返回的是 HTML 网页，直接报错拦截
    content_type = r.headers.get("Content-Type", "").lower()
    if "text/html" in content_type:
        # 读取前 2000 字符用于诊断（页面内容可能揭示失败原因）
        html_preview = ""
        try:
            for chunk in r.iter_content(2000):
                html_preview = chunk.decode("utf-8", errors="replace")[:2000]
                break
        except Exception:
            pass
        logger.error(
            "download_file 拦截：URL=%s 返回 HTML\n  前2000字符：%s",
            file_url, html_preview,
        )
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
    # 加唯一后缀避免并发下载时不同线程写入同一文件路径
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    out_path = DOWNLOAD_DIR / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"
    with out_path.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 512):
            if chunk:
                f.write(chunk)
    # 防止 HTML 错误页绕过 Content-Type 检查被当作 APK 保存（如 0 字节响应）
    file_size = out_path.stat().st_size
    if file_size < 10_000:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"下载文件异常过小（{file_size} 字节），可能是 HTML 错误页或空响应。URL={file_url}"
        )
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
    # 传入 variant_url 作为 Referer，模拟真人点击跳转链路
    final_url = resolve_final_apk_url(session, download_page, referer_url=variant.variant_url)
    variant.final_download_url = final_url
    logger.info("开始下载：%s", final_url)
    raw_name = f"{variant.app_name}_{variant.variant_label}"
    safe_name = re.sub(r'\s+', "_", re.sub(r'[\\/*?:"<>|]', "_", raw_name))
    ext = ".apkm" if variant.is_bundle else ".apk"
    fallback_name = f"{safe_name}{ext}"
    # 传入 download_page 作为下载时的 Referer
    apk_path = download_file(session, final_url, fallback_name, referer=download_page)
    file_hash = sha256_file(apk_path)
    logger.info("下载完成：%s (%.2f MB, sha256=%s...)",
                apk_path.name, apk_path.stat().st_size / 1024 / 1024, file_hash[:12])
    return apk_path, file_hash


# ---------------------------------------------------------------------------
# 关键词多结果搜索
# ---------------------------------------------------------------------------

_APKMIRROR_APP_RE = re.compile(r"^https?://(?:www\.)?apkmirror\.com(/apk/[^/]+/[^/]+)/?$")


def _pkg_to_apkmirror(session: requests.Session, package_name: str) -> tuple[str, str] | None:
    """将包名解析为 (app_name, apkmirror_url)；找不到返回 None。"""
    search_url = f"{BASE_URL}/?searchtype=app&s={package_name}"
    try:
        r = session_get(session, search_url)
    except Exception:
        return None
    soup = BeautifulSoup(r.text, "lxml")
    # 直接重定向到 app 页面
    m = _APKMIRROR_APP_RE.match(r.url)
    if m:
        app_url = BASE_URL + m.group(1) + "/"
        h1 = soup.select_one("h1.app-title, h1")
        name = h1.get_text(strip=True) if h1 else package_name
        return (name, app_url)
    # 搜索结果页
    for a in soup.select("a.fontBlack"):
        href = a.get("href", "")
        if re.match(r"^/apk/[^/]+/[^/]+/?$", href):
            name = a.get_text(strip=True)
            if name:
                return (name, BASE_URL + href.rstrip("/") + "/")
    # 降级：全页扫描
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if re.match(r"^/apk/[^/]+/[^/]+/?$", href):
            name = a.get_text(strip=True)
            if name:
                return (name, BASE_URL + href.rstrip("/") + "/")
    return None


_apk_2seg_re = re.compile(r"^/apk/[^/]+/[^/]+/?$")
_apk_ver_re = re.compile(r"^(/apk/[^/]+/[^/]+)/[^/]+/?$")


def _parse_fontblack_apps(soup: BeautifulSoup, max_results: int) -> list[tuple[str, str]]:
    """APPS tab：只收 2 段 app URL，3 段版本 URL（侧边栏热门）直接跳过。"""
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.select("a.fontBlack"):
        href = a.get("href", "")
        if not _apk_2seg_re.match(href):
            continue
        url = BASE_URL + href.rstrip("/") + "/"
        name = a.get_text(strip=True)
        if url not in seen and name:
            seen.add(url)
            results.append((name, url))
            if len(results) >= max_results:
                break
    return results


def _parse_fontblack_apks(soup: BeautifulSoup, max_results: int) -> list[tuple[str, str]]:
    """APKS tab：将 3 段版本 URL 截断为 2 段 app URL，用出现次数≥2 过滤侧边栏。
    真实搜索结果（同一 app 多个版本）同一 2 段 URL 重复出现；
    侧边栏热门每个 app 只出现 1 次，count=1 → 跳过。
    """
    url_order: list[str] = []
    url_counts: dict[str, int] = {}
    url_names: dict[str, str] = {}
    for a in soup.select("a.fontBlack"):
        href = a.get("href", "")
        m = _apk_ver_re.match(href)
        if not m:
            continue
        two_seg = m.group(1)
        url_counts[two_seg] = url_counts.get(two_seg, 0) + 1
        if two_seg not in url_names:
            url_names[two_seg] = a.get_text(strip=True)
            url_order.append(two_seg)
    results: list[tuple[str, str]] = []
    for two_seg in url_order:
        if url_counts[two_seg] >= 2:
            name = url_names.get(two_seg, "")
            if name:
                results.append((name, BASE_URL + two_seg + "/"))
                if len(results) >= max_results:
                    break
    return results


def _search_apkmirror_direct(session: requests.Session, keyword: str, max_results: int) -> list[tuple[str, str]]:
    """直接在 APKMirror 关键词搜索，返回 [(name, url), ...]。
    先走 APPS tab（2 段 URL，sidebar 为 3 段可过滤）；
    APPS tab 无结果时 fallback 到 APKS tab（count≥2 过滤 sidebar）。
    """
    def _fetch(url: str):
        try:
            return session_get(session, url)
        except Exception:
            return None

    # ── APPS tab ────────────────────────────────────────────────────────────
    apps_url = f"{BASE_URL}/?searchtype=app&sortby=date&s={requests.utils.quote(keyword)}"
    r = _fetch(apps_url)
    if r is not None and "No results found matching your query" not in r.text:
        m = _APKMIRROR_APP_RE.match(r.url)
        if m:
            soup = BeautifulSoup(r.text, "lxml")
            name = (soup.select_one("h1.app-title, h1") or object()).get_text(strip=True) if hasattr(BeautifulSoup, "select_one") else keyword
            return [(name or keyword, BASE_URL + m.group(1) + "/")]
        results = _parse_fontblack_apps(BeautifulSoup(r.text, "lxml"), max_results)
        if results:
            return results

    # ── APKS tab fallback（APPS tab 无结果时）───────────────────────────────
    apks_url = f"{BASE_URL}/?searchtype=apk&s={requests.utils.quote(keyword)}"
    r2 = _fetch(apks_url)
    if r2 is None or "No results found matching your query" in r2.text:
        return []
    m2 = _APKMIRROR_APP_RE.match(r2.url)
    if m2:
        soup2 = BeautifulSoup(r2.text, "lxml")
        h1 = soup2.select_one("h1.app-title, h1")
        name = h1.get_text(strip=True) if h1 else keyword
        return [(name, BASE_URL + m2.group(1) + "/")]
    return _parse_fontblack_apks(BeautifulSoup(r2.text, "lxml"), max_results)


# APKPure 应用页 URL 格式：/slug/com.package.name（第二段为合法包名）
_APKPURE_APP_URL_RE = re.compile(
    r"^/[^/?#]+/([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)+)$"
)


def search_apkpure(session: requests.Session, keyword: str, max_results: int = 5) -> list[tuple[str, str]]:
    """关键词搜索 APKPure，返回 [(app_name, url), …]，最多 max_results 条。
    不依赖易变的 CSS 类名，改为按 URL 结构（/slug/package.name）识别应用链接。
    """
    search_url = f"{_APKPURE_BASE}/search?q={requests.utils.quote(keyword)}"
    try:
        r = session_get(session, search_url, headers={"Referer": _APKPURE_BASE + "/"})
    except Exception:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not _APKPURE_APP_URL_RE.match(href):
            continue
        full_url = _APKPURE_BASE + href.rstrip("/")
        # 优先取链接内的标题元素，避免把 developer/version 也拼进名字
        title_el = a.select_one("p.title-name, .title, h3, h2, span.title, p")
        if title_el:
            name = title_el.get_text(strip=True)
        else:
            # 回退：取第一行非空文本
            lines = [l.strip() for l in a.get_text().splitlines() if l.strip()]
            name = lines[0] if lines else ""
        if not name:
            continue
        if full_url not in seen:
            seen.add(full_url)
            results.append((name, full_url))
            if len(results) >= max_results:
                break
    return results


_HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _to_pinyin(text: str) -> str:
    """将汉字转为拼音连写（无声调），非汉字字符保留原样。"""
    return "".join(lazy_pinyin(text))


def search_apkmirror(session: requests.Session, keyword: str, max_results: int = 20) -> list[tuple[str, str]]:
    """主路径：APKMirror 直搜；含汉字时同时搜拼音；两者均无结果时 fallback APKPure。"""
    if _HAS_CJK_RE.search(keyword):
        pinyin_kw = _to_pinyin(keyword)
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_zh = ex.submit(_search_apkmirror_direct, session, keyword, max_results)
            f_py = ex.submit(_search_apkmirror_direct, new_session(), pinyin_kw, max_results)
        zh_res = f_zh.result() or []
        py_res = f_py.result() or []
        seen: set[str] = set()
        results: list[tuple[str, str]] = []
        for name, url in zh_res + py_res:
            if url not in seen:
                seen.add(url)
                results.append((name, url))
                if len(results) >= max_results:
                    break
        if results:
            return results
    else:
        results = _search_apkmirror_direct(session, keyword, max_results)
        if results:
            return results

    # Fallback：取 APKPure 第一条结果名称再搜 APKMirror
    ap_hits = search_apkpure(new_session(), keyword, 1)
    if not ap_hits:
        return []
    first_name, _ = ap_hits[0]
    return _search_apkmirror_direct(session, first_name, max_results)


# ---------------------------------------------------------------------------
# 包名 → APKMirror / APKPure URL 解析
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


# ---------------------------------------------------------------------------
# APKPure 兜底（仅包名找不到时触发，不支持订阅）
# ---------------------------------------------------------------------------

_APKPURE_BASE = "https://apkpure.net"


def resolve_package_to_apkpure_url(session: requests.Session, package_name: str) -> Optional[str]:
    """通过包名在 APKPure 搜索，返回应用页面 URL；未找到返回 None。"""
    search_url = f"{_APKPURE_BASE}/search?q={package_name}"
    try:
        r = session_get(session, search_url,
                        headers={"Referer": _APKPURE_BASE + "/"})
        soup = BeautifulSoup(r.text, "lxml")
        # 结果列表：<a class="first-info" href="/slug/package.name"> 或 <a class="title" href=...>
        for selector in ("a.first-info", "a.title", ".search-res a.first-info",
                         ".search-row .title a", "a[href*='{}']".format(package_name)):
            a = soup.select_one(selector)
            if a:
                href = a.get("href", "")
                if package_name.lower() in href.lower() and href.startswith("/"):
                    return _APKPURE_BASE + href.rstrip("/")
        # 降级：遍历所有链接
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if package_name.lower() in href.lower() and re.match(r"^/[^/]+/" + re.escape(package_name), href, re.I):
                return _APKPURE_BASE + href.rstrip("/")
        logger.debug("APKPure 搜索未找到：%s", package_name)
        return None
    except Exception as e:
        logger.debug("APKPure 搜索失败 %s: %s", package_name, e)
        return None


def scrape_and_pick_apkpure(session: requests.Session, apkpure_url: str) -> Variant:
    """抓取 APKPure 应用页面，返回 Variant（优先 APK，其次 XAPK）。"""
    r = session_get(session, apkpure_url,
                    headers={"Referer": _APKPURE_BASE + "/"})
    soup = BeautifulSoup(r.text, "lxml")

    # 从 URL 提取包名，用于过滤下载链接（避免误选 APKPure 自身的推广按钮）
    # URL 格式：https://apkpure.net/<slug>/<package.name>
    url_parts = apkpure_url.rstrip("/").split("/")
    pkg_from_url = url_parts[-1] if len(url_parts) >= 2 else ""

    # 应用名
    app_name = ""
    for sel in ("h1.title-like", "h1.detail-main-title", "h1", ".title"):
        tag = soup.select_one(sel)
        if tag:
            app_name = tag.get_text(strip=True)
            break
    if not app_name:
        app_name = apkpure_url.rstrip("/").split("/")[-2].replace("-", " ").title()

    # 版本名与版本号：严格匹配"纯版本号"文本节点，避免吸入整个容器文本
    version_name = ""
    version_code: Optional[int] = None
    for sel in (".info-sdk span", ".detail-info-tag", "p.additional-info span",
                ".details-sdk span", ".ver span", "[class*='version'] span",
                ".info span"):
        for tag in soup.select(sel):
            text = tag.get_text(strip=True)
            # 版本号必须是"纯数字.数字"形式，不能混有字母词
            if re.match(r"^\d+(\.\d+)+$", text) and not version_name:
                version_name = text
            m = re.search(r"\((\d{5,})\)", text)
            if m:
                version_code = int(m.group(1))
    if not version_name:
        version_name = "unknown"

    # 下载按钮链接 — 只取属于目标包名的 /download 链接，避免误选"下载 APKPure"推广按钮
    variant_url = ""
    is_bundle = False
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # 必须包含目标包名路径，排除对 APKPure 自身的引用
        if pkg_from_url and pkg_from_url not in href:
            continue
        full_href = href if href.startswith("http") else _APKPURE_BASE + href
        text = a.get_text(" ", strip=True).lower()
        if "/download" in href and "xapk" in text and not variant_url:
            variant_url = full_href
            is_bundle = True
        if "/download" in href and not is_bundle and not variant_url:
            variant_url = full_href
    if not variant_url and pkg_from_url:
        # 兜底：直接构造标准下载 URL（APKPure 规律：<app_url>/download）
        variant_url = apkpure_url.rstrip("/") + "/download"

    if not variant_url:
        raise RuntimeError(f"APKPure：找不到下载链接（{apkpure_url}）")

    # 架构、最低 Android（尽量解析）
    architectures: list[str] = []
    min_android_text: Optional[str] = None
    for tag in soup.find_all(string=True):
        s = str(tag).strip()
        for arch in _KNOWN_ARCHITECTURES:
            if arch in s and arch not in architectures:
                architectures.append(arch)
        if re.search(r"Android\s+\d+\.\d+", s) and not min_android_text:
            m = re.search(r"Android\s+[\d.]+\+?", s)
            if m:
                min_android_text = m.group()
    if not architectures:
        architectures = ["universal"]

    return Variant(
        app_name=app_name,
        release_version_name=version_name,
        version_code=version_code,
        display_build=f"({version_code})" if version_code else None,
        variant_label="APKPure",
        type="BUNDLE" if is_bundle else "APK",
        is_bundle=is_bundle,
        signatures=[],
        architectures=architectures,
        min_android_text=min_android_text,
        min_android_api=None,
        dpi="nodpi",
        device_type=None,
        release_url=apkpure_url,
        variant_url=variant_url,
        download_page_url=None,
        final_download_url=None,
        raw_text="",
    )


def resolve_and_download_apkpure(session: requests.Session, variant: Variant) -> tuple[Path, str]:
    """从 APKPure 解析最终下载链接并下载文件。"""
    r = session_get(session, variant.variant_url,
                    headers={"Referer": variant.release_url})
    soup = BeautifulSoup(r.text, "lxml")

    final_url = ""
    # 策略 1：id="download_link" 直链
    a = soup.select_one("a#download_link, a.ga[href*='download.apkpure']")
    if a:
        final_url = a["href"]
    # 策略 2：meta refresh 跳转
    if not final_url:
        meta = soup.select_one("meta[http-equiv='refresh']")
        if meta:
            content = meta.get("content", "")
            m = re.search(r"url=(.+)", content, re.I)
            if m:
                final_url = m.group(1).strip("'\"")
    # 策略 3：页面中任意 CDN 直链
    if not final_url:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r"\.(apk|xapk|apkm)(\?|$)", href, re.I):
                final_url = href
                break
    # 策略 4：尝试 GET variant_url 后跟随 30x 重定向（某些下载页直接重定向到文件）
    if not final_url:
        if re.search(r"\.(apk|xapk|apkm)(\?|$)", r.url, re.I):
            final_url = r.url

    if not final_url:
        raise RuntimeError(f"APKPure：无法解析下载链接（{variant.variant_url}）")

    variant.final_download_url = final_url
    logger.info("APKPure 开始下载：%s", final_url)
    safe_name = re.sub(r'\s+', "_", re.sub(r'[\\/*?:"<>|]', "_", variant.app_name))
    ext = ".xapk" if variant.is_bundle else ".apk"
    fallback_name = f"{safe_name}{ext}"
    apk_path = download_file(session, final_url, fallback_name, referer=variant.variant_url)
    file_hash = sha256_file(apk_path)
    logger.info("APKPure 下载完成：%s (sha256=%s...)", apk_path.name, file_hash[:12])

    # 从实际文件名提取版本号（页面抓取可能受评分等数字干扰）
    clean_stem = re.sub(r'_[0-9a-f]{8}$', '', apk_path.stem)
    ver_m = re.search(r'(\d+\.\d+(?:\.\d+)*)', clean_stem)
    if ver_m:
        variant.release_version_name = ver_m.group(1)

    return apk_path, file_hash
