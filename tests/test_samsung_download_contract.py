"""Official share URLs and full-package metadata keep the selected identity."""

from urllib.parse import parse_qs, urlsplit

import pytest

from galaxy_store import AppRequest, InvalidInput, parse_input, parse_ods_grant
import scraper
from tests.test_galaxy_store import grant, metadata, release
from tests.test_scraper import Response, session
from tests.test_samsung_extended import detail_fields, lists


def test_official_browser_detail_url_is_accepted():
    assert parse_input(
        "https://apps.galaxyappstore.com/detail/com.example.app?cntyCd=CHN CN"
    ) == AppRequest("com.example.app", "CN")


@pytest.mark.parametrize("host", [
    "apps.galaxyappstore.com.evil.test", "user@apps.galaxyappstore.com",
    "apps.galaxyappstore.com:443", "other.galaxyappstore.com",
])
def test_browser_detail_alias_does_not_expand_the_host_boundary(host):
    with pytest.raises(InvalidInput):
        parse_input(f"https://{host}/detail/com.example.app")


def test_version_bound_full_grant_supplies_missing_size_for_details():
    selected = release(size=None)
    authorized = parse_ods_grant(grant(), selected)
    assert authorized.release == release()
    main = lists([detail_fields()], request_id="2290")
    overview = lists([dict(version="01.02.3", realContentsSize="42", lastUpdateDate="2026;08;25;")], request_id="2291")
    from galaxy_store import match_ods_details
    assert match_ods_details(main, overview, main, authorized.release)[0].updated_date == "2026-08-25"


def test_unbound_restore_size_is_not_promoted_to_version_metadata():
    selected = release(size=None)
    authorized = parse_ods_grant(grant(version=None, versionCode=None), selected, restore=True)
    assert authorized.size == 42
    assert authorized.release.size is None


@pytest.mark.parametrize("region", ["US", "CN"])
def test_fixed_region_metadata_uses_one_request_without_discovery(region):
    http = session(Response(metadata()))
    assert scraper.GalaxyStore(http).ods_metadata("com.example.app", region).region == region
    http.request.assert_called_once()
    url = urlsplit(http.request.call_args.args[1])
    assert url.hostname == urlsplit(scraper.ODS_ENDPOINTS[region]).hostname
    assert parse_qs(url.query)["reqId"] == ["2298"]
