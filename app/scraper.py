import hashlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    BASE_URL,
    DELETE_AFTER_PUSH,
    DOWNLOAD_DIR,
    LIST_URL,
    MAX_KEEP_FILES,
    PREFER_APK,
    REQUEST_TIMEOUT,
    REQUIRE_SIGNATURE,
    REQUIRE_VARIANT,
    USER_AGENT,
)

logger = logging.getLogger("apkmirror-bot")

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE = 1  # 秒；延迟依次为 1s, 2s, 4s


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
    """带重试的 GET 请求。

    对以下情况进行最多 3 次重试（指数退避 1s/2s/4s）：
    - ConnectionError（DNS 解析失败、TCP 连接重置等）
    - Timeout
    - HTTP 5xx（服务器端临时错误）

    HTTP 4xx 视为确定性错误，直接抛出不重试。
    """
    last_exc: Exception = RuntimeError("unreachable")
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
            if resp.status_code < 500:
                resp.raise_for_status()
                return resp
            last_exc = requests.HTTPError(
                f"Server error {resp.status_code}", response=resp
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
        if attempt < _RETRY_ATTEMPTS - 1:
            wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.warning(
                "请求失败（第 %d/%d 次），%ds 后重试：%s",
                attempt + 1,
                _RETRY_ATTEMPTS,
                wait,
                url,
            )
            time.sleep(wait)
    raise last_exc


def get_latest_release_page(session: requests.Session) -> str:
    r = session_get(session, LIST_URL)
    soup = BeautifulSoup(r.text, "lxml")
    links = soup.select('a[href*="/apk/google-inc/google-play-store/google-play-store-"]')
    seen = set()
    for a in links:
        href = a.get("href", "")
        if href in seen:
            continue
        seen.add(href)
        if href and "google-play-store-" in href and href.endswith("/"):
            return urljoin(BASE_URL, href)
    raise RuntimeError("Could not find latest release page")


def extract_version_from_url(url: str) -> str:
    m = re.search(r"google-play-store-([\d\-]+)-release", url)
    if m:
        return m.group(1).replace("-", ".")
    return "unknown"


def parse_variants_page(session: requests.Session, release_url: str):
    r = session_get(session, release_url)
    soup = BeautifulSoup(r.text, "lxml")

    candidates = []
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href or href in seen:
            continue
        if "/apk/google-inc/google-play-store/google-play-store-" not in href:
            continue
        if href.rstrip("/") == release_url.rstrip("/"):
            continue

        text_candidates = [a.get_text(" ", strip=True)]
        p = a.parent
        steps = 0
        while p is not None and steps < 4:
            text_candidates.append(p.get_text(" ", strip=True))
            p = p.parent
            steps += 1
        combined = " ".join(t for t in text_candidates if t)

        if REQUIRE_SIGNATURE not in combined:
            continue
        if REQUIRE_VARIANT not in combined.lower():
            continue

        is_apk = "APK" in combined
        if PREFER_APK and not is_apk:
            continue

        is_bundle = "BUNDLE" in combined
        seen.add(href)
        candidates.append(
            {
                "url": urljoin(BASE_URL, href),
                "text": combined,
                "is_apk": is_apk,
                "is_bundle": is_bundle,
            }
        )

    if not candidates:
        raise RuntimeError("No matching variant found for configured signature/variant")

    candidates.sort(key=lambda x: (not x["is_apk"], x["url"]))
    return candidates


def resolve_download_page(session: requests.Session, variant_url: str) -> str:
    r = session_get(session, variant_url)
    soup = BeautifulSoup(r.text, "lxml")

    for a in soup.select("a[href]"):
        text = a.get_text(" ", strip=True).lower()
        href = a.get("href", "")
        if "download apk" in text or "see available downloads" in text:
            return urljoin(BASE_URL, href)
    for a in soup.select('a[href*="/download/"]'):
        return urljoin(BASE_URL, a.get("href"))
    raise RuntimeError("Could not resolve download page")


def resolve_final_apk_url(session: requests.Session, download_page_url: str) -> str:
    r = session_get(session, download_page_url)
    soup = BeautifulSoup(r.text, "lxml")

    for a in soup.select("a[href]"):
        text = a.get_text(" ", strip=True).lower()
        href = a.get("href", "")
        if "download apk" in text or href.endswith(".apk"):
            return urljoin(BASE_URL, href)

    for a in soup.select('a[href*="/wp-content/"]'):
        return urljoin(BASE_URL, a.get("href"))

    raise RuntimeError("Could not resolve final apk URL")


def download_file(session: requests.Session, file_url: str) -> Path:
    with session.get(file_url, stream=True, timeout=300, allow_redirects=True) as r:
        r.raise_for_status()
        filename = None
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)"?', cd)
        if m:
            filename = m.group(1)
        if not filename:
            filename = file_url.split("/")[-1].split("?")[0] or f"playstore_{int(time.time())}.apk"
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


def scrape_latest() -> dict:
    session = new_session()
    release_url = get_latest_release_page(session)
    version = extract_version_from_url(release_url)
    variants = parse_variants_page(session, release_url)
    picked = variants[0]
    download_page = resolve_download_page(session, picked["url"])
    final_url = resolve_final_apk_url(session, download_page)
    apk_path = download_file(session, final_url)
    file_hash = sha256_file(apk_path)
    return {
        "version": version,
        "release_url": release_url,
        "variant_url": picked["url"],
        "final_url": final_url,
        "apk_path": apk_path,
        "sha256": file_hash,
        "is_apk": picked["is_apk"],
    }


def cleanup_after_push(apk_path: Path) -> None:
    """推送成功后按配置清理 APK 文件。"""
    if DELETE_AFTER_PUSH:
        try:
            apk_path.unlink(missing_ok=True)
            logger.info("已删除推送后的 APK：%s", apk_path.name)
        except OSError:
            logger.exception("删除 APK 失败：%s", apk_path)
        return

    if MAX_KEEP_FILES > 0:
        _trim_download_dir(MAX_KEEP_FILES)


def _trim_download_dir(keep: int) -> None:
    """按修改时间排序，保留最新 keep 个 .apk 文件，删除旧文件。"""
    apks = sorted(
        DOWNLOAD_DIR.glob("*.apk"),
        key=lambda p: p.stat().st_mtime,
    )
    to_delete = apks[:-keep] if len(apks) > keep else []
    for path in to_delete:
        try:
            path.unlink()
            logger.info("已删除旧 APK：%s", path.name)
        except OSError:
            logger.exception("删除旧 APK 失败：%s", path)
