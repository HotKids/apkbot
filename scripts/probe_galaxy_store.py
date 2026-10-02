#!/usr/bin/env python3
"""Credential-free probe. Metadata by default; explicit authorization/download options."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from galaxy_store import AppRequest, StoreError
from scraper import GalaxyStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    parser.add_argument("--region", choices=("AUTO", "US", "CN"), default="AUTO")
    parser.add_argument("--channel", choices=("default", "stub"), default="default")
    parser.add_argument("--notes", action="store_true")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--authorize",
        action="store_true",
        help="Authorize without fetching APK bytes or printing its URL; may record anonymous store activity",
    )
    action.add_argument(
        "--download-to",
        type=Path,
        help="Authorize and download a full APK; may record anonymous store activity",
    )
    args = parser.parse_args()
    try:
        app = AppRequest(args.package, args.region)
        with GalaxyStore() as store:
            if args.channel == "stub":
                if args.region == "AUTO":
                    parser.error("--channel stub requires explicit CN or US")
                release = store.stub(app.package, app.region).release
            else:
                release = store.metadata(app)
            result = {"metadata": asdict(release)}
            if args.notes:
                result["cn_notes"] = store.notes(release)
            if args.authorize:
                grant = store.authorize(release)
                result["authorization"] = dict(
                    host=urlsplit(grant.url).hostname,
                    bytes=grant.size,
                    apk_downloaded=False,
                )
            if args.download_to:
                download = store.download(release, args.download_to)
                result["download"] = dict(
                    path=str(download.path),
                    bytes=download.size,
                    sha256=download.sha256,
                    manifest_verified=True,
                    signature_verified=False,
                )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except StoreError as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
