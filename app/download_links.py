"""Stable signed entry points; Samsung authorization is fresh on every click."""

from contextlib import contextmanager
from hashlib import sha256
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import re
import threading

import config
import database as db
from galaxy_store import StoreError
from scraper import GalaxyStore, validate_url

logger = logging.getLogger("apkdl-download")
_PATH = re.compile(r"/d/([1-9][0-9]{0,18})/([0-9a-f]{32})/([0-9a-f]{64})")


class LinkError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _signature(user_id, app_key):
    payload = f"apkdl-download-v1:{user_id}:{app_key}".encode()
    return hmac.new(config.BOT_TOKEN.encode(), payload, sha256).hexdigest()


def download_url(app, user_id):
    # The application key is durable; neither this signature nor Samsung's URL
    # needs to be stored. Token rotation intentionally invalidates old buttons.
    db.remember_app(app)
    return f"{config.PUBLIC_DOWNLOAD_BASE_URL}/d/{user_id}/{app.key}/{_signature(user_id, app.key)}"


def resolve_download(path):
    match = _PATH.fullmatch(path)
    if not match:
        raise LinkError(404, "下载入口不存在，请回到 Bot 重新获取。")
    raw_user_id, app_key, signature = match.groups()
    user_id = int(raw_user_id)
    if user_id >= 2**63 or not hmac.compare_digest(
        signature, _signature(user_id, app_key)
    ):
        raise LinkError(404, "下载入口已失效，请回到 Bot 重新获取。")
    if user_id != config.OWNER_ID and not db.is_in_whitelist(user_id):
        raise LinkError(403, "此下载入口的访问权限已取消。")
    app = db.get_app(app_key)
    if app is None:
        raise LinkError(404, "应用记录不存在，请回到 Bot 重新获取。")
    with GalaxyStore() as store:
        release = store.metadata(app)
        grant = store.authorize(release)
    # Never fetch the CDN, including HEAD, and never cache an expiring grant.
    return validate_url(grant.url)


class DownloadHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "APKDL"
    sys_version = ""

    def log_message(self, *_):
        # Access logs would expose the bearer signature in the request path.
        pass

    def _reply(self, status, body=b"", location=None):
        self.send_response(status)
        self.send_header("Cache-Control", "private, no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if location is not None:
            self.send_header("Location", location)
        if status == 405:
            self.send_header("Allow", "GET")
        self.end_headers()
        self.close_connection = True
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._reply(200, b"ok\n")
            return
        try:
            location = resolve_download(self.path)
        except LinkError as exc:
            self._reply(exc.status, str(exc).encode())
        except StoreError as exc:
            self._reply(
                502, ("获取下载授权失败：" + str(exc) + "\n请刷新此页面重试。").encode()
            )
        except Exception:
            logger.warning("Download redirect failed; sensitive details suppressed")
            self._reply(503, "暂时无法获取下载链接，请刷新此页面重试。".encode())
        else:
            # A browser follows this directly to Samsung; no APK body passes
            # through this server. A 302 must not become a permanent redirect.
            self._reply(302, location=location)

    def do_HEAD(self):
        # Health monitors/link previews must not create download authorizations.
        self._reply(200 if self.path == "/healthz" else 405)

    def do_POST(self):
        self._reply(405)


class DownloadServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(address, DownloadHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Cache-Control: no-store\r\nRetry-After: 5\r\nConnection: close\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._slots.release()

    def handle_error(self, *_):
        logger.warning("Download HTTP request failed; sensitive details suppressed")


@contextmanager
def running_download_server():
    server = DownloadServer((config.DOWNLOAD_BIND_HOST, config.DOWNLOAD_BIND_PORT))
    worker = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    )
    try:
        worker.start()
        yield server
    finally:
        if worker.is_alive():
            server.shutdown()
            worker.join()
        server.server_close()
