#!/usr/bin/env python3
"""Download Samsung Assistant directly from Samsung CN using Python's standard library.

A force-killed run can leave an incomplete APK. Delete it before retrying.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.client import HTTPException
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4
import xml.etree.ElementTree as ET


PACKAGE = "com.samsung.android.app.sreminder"
ODS_URL = "https://cn-ms.galaxyappstore.com/ods.as"
USER_AGENT = "APKDL/1.0 (Galaxy Store; Android 16; SM-S9480)"
METADATA_LIMIT = 2_000_000
REDIRECT_LIMIT = 5


class DownloadError(Exception):
    """A readable error without service bodies or signed URLs."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def validate_url(url):
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname or "", parsed.port
    except ValueError:
        raise DownloadError("Samsung returned an invalid download address.") from None
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or not parsed.path.startswith("/")
        or "\\" in url
        or any(ord(character) < 33 for character in url)
        or not (
            host == "galaxystore.samsung.com"
            or any(
                host == suffix or host.endswith("." + suffix)
                for suffix in ("samsungapps.com", "galaxyappstore.com")
            )
        )
    ):
        raise DownloadError("Samsung returned an unsupported download address.")


def open_response(opener, method, url, *, deadline, data=None):
    for _ in range(REDIRECT_LIMIT + 1):
        validate_url(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DownloadError("The request timed out. Please retry.")
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if method == "POST":
            headers.update(
                {"Content-Type": "text/plain; charset=UTF-8", "Accept": "image/webp"}
            )
        try:
            response = opener.open(
                Request(url, data=data, headers=headers, method=method),
                timeout=min(30, remaining),
            )
        except HTTPError as error:
            status = error.code
            location = error.headers.get("Location")
            error.close()
            if status in (301, 302, 303, 307, 308):
                if method != "GET" or not location:
                    raise DownloadError(
                        "Samsung returned an unexpected redirect."
                    ) from None
                try:
                    url = urljoin(url, location)
                except ValueError:
                    raise DownloadError(
                        "Samsung returned an invalid redirect address."
                    ) from None
                continue
            raise DownloadError(
                f"Samsung is unavailable (HTTP {status}). Please retry."
            ) from None
        except (URLError, OSError, HTTPException, UnicodeError, ValueError):
            raise DownloadError("Unable to connect to Samsung. Please retry.") from None
        if response.status != 200:
            response.close()
            raise DownloadError(
                "Samsung returned an unexpected response. Please retry."
            )
        return response
    raise DownloadError("Samsung returned too many redirects. Please retry.")


def chunks(response, deadline, limit):
    count = 0
    try:
        while True:
            if time.monotonic() >= deadline:
                raise DownloadError("The transfer timed out. Please retry.")
            block = response.read1(min(64 * 1024, limit - count + 1))
            if not block:
                return
            count += len(block)
            if count > limit:
                raise DownloadError(
                    "The response size does not match the expected limit."
                )
            yield block
    except (OSError, HTTPException):
        raise DownloadError("The transfer was interrupted. Please retry.") from None


def xml_fields(data):
    if not data or len(data) > METADATA_LIMIT:
        raise DownloadError("Samsung returned empty or oversized information.")
    try:
        text = data.decode("utf-8-sig")
        # Decode first so alternative byte encodings cannot hide declarations.
        if "\x00" in text or re.search(
            r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE
        ):
            raise DownloadError("Samsung returned unsupported XML declarations.")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError, ValueError):
        raise DownloadError("Samsung returned unreadable information.") from None
    if root.tag != "SamsungProtocol":
        raise DownloadError("Samsung returned an unexpected information format.")
    fields = {}
    for node in root.iter():
        if len(node):
            continue
        key = node.attrib.get("name", node.tag)
        if key in fields:
            raise DownloadError("Samsung returned conflicting information.")
        fields[key] = (node.text or "").strip()
        if key == "errorString" and "errorCode" in node.attrib:
            if "errorCode" in fields:
                raise DownloadError("Samsung returned conflicting information.")
            fields["errorCode"] = node.attrib["errorCode"]
    return fields


def required(fields, key):
    value = fields.get(key, "")
    if not value:
        raise DownloadError("Samsung returned incomplete information.")
    return value


def positive(fields, key):
    raw = required(fields, key)
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 19 or int(raw) <= 0:
        raise DownloadError("Samsung returned invalid information.")
    return int(raw)


def successful_fields(data):
    fields = xml_fields(data)
    status = fields.get("errorString", "").casefold()
    if required(fields, "errorCode") != "0" or status not in {"", "success"}:
        if "login" in status or "log in" in status:
            raise DownloadError("This application requires a Samsung account login.")
        raise DownloadError("Samsung rejected the request. Please retry.")
    return fields


def parse_metadata(data):
    fields = successful_fields(data)
    if required(fields, "GUID") != PACKAGE:
        raise DownloadError("Samsung returned information for a different application.")
    product = required(fields, "productID")
    if not re.fullmatch(r"[0-9]{1,30}", product):
        raise DownloadError("Samsung returned invalid application information.")
    login, installable = (
        required(fields, "needToLogin"),
        required(fields, "installableYN"),
    )
    if login not in {"0", "1"} or installable not in {"Y", "N"}:
        raise DownloadError("Samsung returned invalid installation information.")
    if login == "1":
        raise DownloadError("This application requires a Samsung account login.")
    if installable != "Y":
        raise DownloadError(
            "Samsung does not currently permit installing this release."
        )
    return {
        "product": product,
        "version": required(fields, "version"),
        "code": positive(fields, "versionCode"),
        "size": positive(fields, "realContentsSize")
        if fields.get("realContentsSize")
        else None,
    }


def parse_grant(data, release):
    fields = successful_fields(data)
    if required(fields, "productID") != release["product"] or (
        "GUID" in fields and fields["GUID"] != PACKAGE
    ):
        raise DownloadError(
            "Samsung returned download information for a different application."
        )
    if ("version" in fields and fields["version"] != release["version"]) or (
        "versionCode" in fields and positive(fields, "versionCode") != release["code"]
    ):
        raise DownloadError("The store version changed. Please retry.")
    size = positive(fields, "contentsSize")
    if release["size"] is not None and size != release["size"]:
        raise DownloadError("The store file size changed. Please retry.")
    # 32n64 is CPU coverage; downLoadURI names the full APK, not a delta.
    url = required(fields, "downLoadURI")
    validate_url(url)
    return url, size


def envelope(method, request_id, params, identity):
    now = datetime.now(timezone(timedelta(hours=8)))
    transaction = (
        str(now.day)
        + sha256(
            (identity + now.strftime("%Y%m%d%H") + "GalaxyApps").encode()
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
        logId=identity,
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


def ods(opener, identity, method, request_id, params):
    deadline = time.monotonic() + 60
    url = ODS_URL + "?" + urlencode({"reqId": request_id, "ot": "01", "ct": "B"})
    with open_response(
        opener,
        "POST",
        url,
        deadline=deadline,
        data=envelope(method, request_id, params, identity),
    ) as response:
        return b"".join(chunks(response, deadline, METADATA_LIMIT))


def owned_file_matches(path, identity):
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.st_dev, stat.st_ino) == identity


def download(output_directory=Path(".")):
    directory = Path(output_directory)
    if not directory.is_dir():
        raise DownloadError("The output directory does not exist.")
    opener, identity = build_opener(NoRedirect()), uuid4().hex[:16]
    selected = parse_metadata(
        ods(
            opener,
            identity,
            "getDownloadInfo",
            "2298",
            dict(
                mode="directDownload",
                guid=PACKAGE,
                productID="",
                imei=identity,
                extuk=identity,
                stduk=identity,
                predeployed="0",
                unifiedPaymentYN="Y",
                lkAppIncludedYN="Y",
                betaTestYN="N",
                minorYN="N",
                stateCode="",
            ),
        )
    )
    version = (
        re.sub(r"[^A-Za-z0-9._-]", "_", selected["version"]).strip("._")[:80]
        or "version"
    )
    destination = directory / f"Samsung-Assistant_{version}_{selected['code']}.apk"
    if destination.exists() or destination.is_symlink():
        raise DownloadError(
            "The version-named APK already exists; it was not modified."
        )
    url, size = parse_grant(
        ods(
            opener,
            identity,
            "downloadForRestore",
            "2316",
            dict(
                GUID=PACKAGE,
                productID=selected["product"],
                imei=identity,
                extuk=identity,
                stduk=identity,
                downloadType="new",
                autoUpdateYN="N",
                triggeredFrom="DETAIL_PAGE",
                predeployed="0",
                deepLinkSource="",
                resumeYN="N",
            ),
        ),
        selected,
    )
    owned, complete = None, False
    try:
        try:
            output = destination.open("xb")
        except FileExistsError:
            raise DownloadError(
                "The version-named APK already exists; it was not modified."
            ) from None
        # Writing the exclusive descriptor avoids a rename overwriting another writer.
        with output:
            stat = os.fstat(output.fileno())
            owned = stat.st_dev, stat.st_ino
            deadline = time.monotonic() + 15 * 60
            with open_response(opener, "GET", url, deadline=deadline) as response:
                length = response.headers.get("Content-Length")
                if length is not None and (
                    not length.isascii()
                    or not length.isdecimal()
                    or len(length) > 19
                    or int(length) != size
                ):
                    raise DownloadError(
                        "The download response size differs from the authorized release."
                    )
                count = 0
                for block in chunks(response, deadline, size):
                    output.write(block)
                    count += len(block)
                if count != size:
                    raise DownloadError("The download is incomplete. Please retry.")
            output.flush()
            os.fsync(output.fileno())
        if not owned_file_matches(destination, owned):
            raise DownloadError(
                "The destination changed during download; the replacement was not modified."
            )
        complete = True
        return destination
    finally:
        if (
            not complete
            and owned is not None
            and owned_file_matches(destination, owned)
        ):
            destination.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Existing output directory (default: current directory)",
    )
    arguments = parser.parse_args(argv)
    try:
        result = download(arguments.output_dir)
    except DownloadError as error:
        print(f"Download failed: {error}", file=sys.stderr)
        return 1
    except OSError:
        print(
            "Download failed: unable to save the APK. Check directory access and free space.",
            file=sys.stderr,
        )
        return 1
    print(f"Downloaded: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
