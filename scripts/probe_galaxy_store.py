#!/usr/bin/env python3
"""Credential-free metadata/authorization probe. Never fetches APK files."""

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
    parser.add_argument(
        "--authorize",
        action="store_true",
        help="Authorize without fetching APK bytes or printing its URL; may record anonymous store activity",
    )
    args = parser.parse_args()
    try:
        app = AppRequest(args.package, args.region)
        with GalaxyStore() as store:
            if args.channel == "stub":
                if args.region == "AUTO":
                    parser.error("--channel stub requires explicit CN or US")
                release = store.stub(app.package, app.region).release
                if args.authorize:
                    grant = store.authorize(release)
            elif args.authorize:
                grant = store.download_link(app)
                release = grant.release
            else:
                release = store.metadata(app)
            result = {}
            if args.notes:
                release, result["cn_notes"] = store.details(release)
            result["metadata"] = asdict(release)
            if args.authorize:
                result["authorization"] = dict(
                    host=urlsplit(grant.url).hostname,
                    bytes=grant.size,
                    apk_downloaded=False,
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
