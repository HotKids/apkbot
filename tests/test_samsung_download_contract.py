"""Official share URLs and full-package metadata keep the selected identity."""

from urllib.parse import parse_qs, urlsplit

import pytest

from galaxy_store import AppRequest, InvalidInput, StoreError, parse_input, parse_ods_grant
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


def linked_store(metadata_changes=None, grant_changes=None):
    fields = dict(
        version="01.02.3", versionCode="123", contentsSize="42",
        downLoadURI="https://auto-dd.myapp.com/full.apk?fixture=synthetic",
        appId="partner-directory-id",
    )
    fields.update(grant_changes or {})
    fields = {key: value for key, value in fields.items() if value is not None}
    http = session(
        Response(metadata(linkProductYn="1", **(metadata_changes or {}))),
        Response(lists([fields], request_id="2801")),
    )
    return scraper.GalaxyStore(http), http


def test_confirmed_cn_linked_product_uses_matching_partner_download_directly():
    store, http = linked_store()
    authorized = store.download_link(AppRequest("com.example.app", "CN"))
    assert authorized.release.size == authorized.size == 42
    assert authorized.release.linked_product
    assert [parse_qs(urlsplit(call.args[1]).query)["reqId"][0] for call in http.request.call_args_list] == ["2298", "2801"]


@pytest.mark.parametrize("change", [
    {"GUID":"com.other.app"}, {"GUID":""}, {"productID":"00002"},
    {"version":"2.0"}, {"versionCode":"124"}, {"contentsSize":"43"},
    {"version":None}, {"versionCode":None}, {"contentsSize":None},
    {"downLoadURI":"http://auto-dd.myapp.com/file.apk"},
    {"downLoadURI":"https://auto-dd.myapp.com.evil.test/file.apk"},
    {"downLoadURI":"https://other.myapp.com/file.apk"},
    {"downLoadURI":"https://user@auto-dd.myapp.com/file.apk"},
    {"downLoadURI":"https://auto-dd.myapp.com:8443/file.apk"},
])
def test_linked_product_cannot_weaken_identity_version_size_or_host(change):
    store, http = linked_store(grant_changes=change)
    with pytest.raises(StoreError):
        store.download_link(AppRequest("com.example.app", "CN"))
    assert http.request.call_count == 2


@pytest.mark.parametrize("region,size", [("US","42"),("CN","")])
def test_partner_download_requires_cn_and_known_full_size(region,size):
    store, http = linked_store(metadata_changes={"realContentsSize":size})
    selected = store.ods_metadata("com.example.app", region)
    with pytest.raises(StoreError):
        store.authorize(selected)
    assert http.request.call_count == 1


def test_unknown_linked_product_flag_is_not_used_for_authorization():
    http=session(Response(metadata(linkProductYn="unexpected")))
    with pytest.raises(StoreError):
        scraper.GalaxyStore(http).download_link(AppRequest("com.example.app","CN"))
    assert http.request.call_count == 1


def test_native_grant_cannot_use_a_partner_host():
    http=session(Response(metadata()),Response(grant(downLoadURI="https://auto-dd.myapp.com/file.apk")))
    with pytest.raises(StoreError):
        scraper.GalaxyStore(http).download_link(AppRequest("com.example.app","CN"))
    assert http.request.call_count == 2


def test_linked_card_does_not_claim_an_unconfirmed_link_lifetime():
    from cards import help_card, release_card
    card=release_card(release(linked_product=True))
    assert "10 分钟" not in card.html()
    assert "下载链接失效后，请点击「刷新」。" in card.html()
    assert "直接从 Samsung 获取" not in help_card().html()
