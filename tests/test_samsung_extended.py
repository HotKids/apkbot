"""Regional ODS and batched subscriptions preserve identity and delivery."""

from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

import pytest

import galaxy_store as protocol
import scraper
from tests.test_galaxy_store import metadata, release
from tests.test_scraper import Response, session


def lists(rows, *, request_id="2389", code="0"):
    root = ET.Element("SamsungProtocol")
    response = ET.SubElement(root, "response", id=request_id, returnCode=code)
    info = ET.SubElement(response, "errorInfo")
    ET.SubElement(info, "errorString", errorCode=code)
    for fields in rows:
        item = ET.SubElement(response, "list")
        for key, value in fields.items():
            ET.SubElement(item, "value", name=key).text = str(value)
    return ET.tostring(root)


def profile_fields(payload):
    return ET.fromstring(payload).attrib


def test_us_stub_rejection_uses_us_ods_before_auto_can_try_cn(monkeypatch):
    store = scraper.GalaxyStore(Mock())
    monkeypatch.setattr(store, "stub", Mock(side_effect=protocol.StubRestricted("restricted")))
    post = Mock(return_value=metadata())
    monkeypatch.setattr(store, "_ods", post)
    assert store.metadata(protocol.AppRequest("com.example.app", "US")).region == "US"
    assert profile_fields(post.call_args.args[1])["csc"] == "XAA"
    assert post.call_args.kwargs["region"] == "US"


def test_regional_protocol_parameters_are_shared_by_metadata_and_full_download():
    profile = protocol.OdsProfile(region="US")
    selected = release(region="US")
    for data in (profile.metadata(selected.package), profile.download(selected)):
        fields = profile_fields(data)
        assert (fields["mcc"], fields["mnc"], fields["csc"], fields["lang"]) == (
            "310", "260", "XAA", "en_US"
        )


def test_structured_details_ignore_repeated_unrelated_fields_but_not_identity():
    fields = {"GUID":"com.example.app", "productID":"00001", "version":"01.02.3", "versionCode":"123", "realContentsSize":"42", "lastUpdateDate":"2026;08;25;", "updateDescription":"Publisher text"}
    main = lists([fields], request_id="2290")
    overview = ET.fromstring(lists([fields], request_id="2291"))
    item = overview.find("response/list")
    for _ in range(2):
        ET.SubElement(item, "value", name="dataSafety").text = "unrelated"
    selected, notes = protocol.match_ods_details(main, ET.tostring(overview), main, release())
    assert selected.updated_date == "2026-08-25" and notes == "Publisher text"
    ET.SubElement(item, "value", name="GUID").text = "com.other.app"
    with pytest.raises(protocol.InvalidResponse):
        protocol.match_ods_details(main, ET.tostring(overview), main, release())


@pytest.mark.parametrize("change", [
    {"GUID":"com.other.app"}, {"productID":"00002"}, {"version":"2.0"}, {"versionCode":"124"}
])
def test_details_cannot_attach_a_different_application_or_version(change):
    fields = {"GUID":"com.example.app", "productID":"00001", "version":"01.02.3", "versionCode":"123", "realContentsSize":"42", "lastUpdateDate":"2026;08;25;", "updateDescription":"Publisher text"}
    main = lists([fields], request_id="2290")
    fields.update(change)
    with pytest.raises(protocol.StoreError):
        protocol.match_ods_details(main, lists([fields], request_id="2291"), main, release())


def detail_fields(**changes):
    values = dict(GUID="com.example.app", productID="00001", version="01.02.3", versionCode="123", realContentsSize="42")
    values.update(changes)
    return values


def test_real_overview_without_identity_is_bound_by_two_matching_main_responses():
    overview = dict(version="01.02.3", realContentsSize="42", lastUpdateDate="2026;08;25;", updateDescription="  Publisher text\n")
    main = lists([detail_fields()], request_id="2290")
    selected, notes = protocol.match_ods_details(main, lists([overview], request_id="2291"), main, release())
    assert selected.updated_date == "2026-08-25" and notes == "  Publisher text\n"
    with pytest.raises(protocol.VersionDrift):
        protocol.match_ods_details(main, lists([overview], request_id="2291"), lists([detail_fields(versionCode="124")], request_id="2290"), release())


@pytest.mark.parametrize("date", ["20260825", "2026-08-25", "2026;02;30;", "2026;8;25;", "", "2026;08;25;10:30"])
def test_invalid_or_non_store_date_is_never_displayed(date):
    main = lists([detail_fields()], request_id="2290")
    overview = lists([dict(version="01.02.3", realContentsSize="42", lastUpdateDate=date)], request_id="2291")
    assert protocol.match_ods_details(main, overview, main, release())[0].updated_date is None


def test_structured_metadata_never_takes_identity_from_nested_unknown_lists():
    values = detail_fields(needToLogin="0", installableYN="Y")
    del values["GUID"]
    doc = ET.fromstring(lists([values], request_id="2298"))
    extra = ET.SubElement(doc.find("response/list"), "extList", name="dataSafetyList")
    ET.SubElement(extra, "value", name="GUID").text = "com.example.app"
    with pytest.raises(protocol.InvalidResponse):
        protocol.parse_ods_metadata(ET.tostring(doc), "com.example.app")


def test_batched_protocol_is_bounded_and_does_not_authorize_downloads(monkeypatch):
    baselines = [release(package=f"com.example.app{i}") for i in range(121)]
    store = scraper.GalaxyStore(Mock())
    calls = []
    def post(request_id, payload, *, region):
        assert request_id == "2389" and region == "CN"
        params = {node.get("name"): node.text for node in ET.fromstring(payload).find("request")}
        names = [part.split("@@")[0] for part in params["loadApp"].split("||")]
        calls.append(names)
        return lists([detail_fields(GUID=name, realContentSize="42") for name in names])
    monkeypatch.setattr(store, "_ods", post)
    assert len(store.batch_updates(baselines, "CN")) == 121
    assert [len(group) for group in calls] == [120, 1]


@pytest.mark.parametrize("rows", [
    [detail_fields(GUID="com.other.app", realContentSize="42")],
    [detail_fields(realContentSize="42"), detail_fields(realContentSize="42")],
    [detail_fields(realContentSize="42", versionCode="invalid")],
])
def test_bad_batch_rows_cannot_be_used_as_update_candidates(rows):
    with pytest.raises(protocol.InvalidResponse):
        protocol.parse_update_list(lists(rows), [release()], "CN")


def test_batch_candidate_requires_current_same_region_permission_metadata(monkeypatch):
    store = scraper.GalaxyStore(Mock())
    fetch = Mock(return_value=release(needs_login=True))
    monkeypatch.setattr(store, "ods_metadata", fetch)
    assert store.update_metadata({"GUID":"com.example.app", "region":"CN"}).needs_login
    fetch.assert_called_once_with("com.example.app", "CN")


def rejection():
    from tests.test_galaxy_store import ods
    return Response(ods(error="Native authorization rejected", code="4002"))


def test_cn_mirror_is_attempted_only_after_both_native_rejections():
    from tests.test_galaxy_store import grant
    http = session(rejection(), rejection(), Response(grant()))
    store = scraper.GalaxyStore(http)
    assert store.authorize(release()).release == release()
    assert [parse_qs(urlsplit(call.args[1]).query)["reqId"][0] for call in http.request.call_args_list] == ["2311", "2316", "2801"]


@pytest.mark.parametrize("changes", [
    {"GUID":None}, {"GUID":"com.other.app"}, {"versionCode":None}, {"version":None},
    {"contentsSize":"43"}, {"downLoadURI":"https://third-party.test/file.apk"},
])
def test_mirror_cannot_weaken_full_package_identity_or_samsung_host(changes):
    from tests.test_galaxy_store import grant
    http = session(rejection(), rejection(), Response(grant(**changes)))
    store = scraper.GalaxyStore(http)
    with pytest.raises(protocol.StoreError):
        store.authorize(release())
    assert http.request.call_count == 3


def test_us_native_rejection_never_uses_cn_mirror():
    http = session(rejection(), rejection())
    store = scraper.GalaxyStore(http)
    with pytest.raises(protocol.StoreError):
        store.authorize(release(region="US"))
    assert http.request.call_count == 2
    assert all("us-odc.samsungapps.com" in call.args[1] for call in http.request.call_args_list)


def test_malformed_restore_cannot_trigger_mirror():
    http = session(rejection(), Response(b"malformed"))
    store = scraper.GalaxyStore(http)
    with pytest.raises(protocol.InvalidResponse):
        store.authorize(release())
    assert http.request.call_count == 2


def scheduler_store(monkeypatch, selected=None):
    import handlers
    store = Mock()
    store.metadata.return_value = selected or release()
    store.ods_metadata.return_value = selected or release()
    store.details.side_effect = lambda selected: (selected, "new notes")
    store.batch_updates.return_value = {}
    store.update_metadata.side_effect = lambda fields: scraper.GalaxyStore.update_metadata(store, fields)
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=store)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(handlers, "GalaxyStore", factory)
    return store


@pytest.fixture
def scheduler_transport(monkeypatch):
    import handlers
    bot, cards = Mock(), Mock()
    monkeypatch.setattr(handlers, "bot", bot)
    monkeypatch.setattr(handlers, "messages", cards)
    return bot, cards


def test_batch_missing_row_rechecks_app_and_retries_cached_unconfirmed_notification(monkeypatch, scheduler_transport):
    import database as db
    import handlers
    app = protocol.AppRequest("com.example.app", "CN")
    db.add_subscription(100, app)
    selected = release(updated_date="2026-08-25")
    db.cache_release(app, selected, "old notes")
    store = scheduler_store(monkeypatch, selected)
    handlers.run_check_all(triggered_by=100)
    store.batch_updates.assert_called_once_with([selected], "CN")
    store.metadata.assert_called_once_with(app)
    store.details.assert_not_called()
    scheduler_transport[1].send.assert_called_once()
    assert db.pending_subscribers(app, selected) == []
    assert "old notes" in scheduler_transport[1].send.call_args.args[1].html()
    assert scheduler_transport[0].send_message.call_args.args[1] == "更新检查已完成。1 项查询成功，0 项查询失败。"


def test_batch_and_single_failure_preserve_cache_but_can_retry_pending_delivery(monkeypatch, scheduler_transport):
    import database as db
    import handlers
    app = protocol.AppRequest("com.example.app", "CN")
    db.add_subscription(100, app)
    selected = release(updated_date="2026-08-25")
    db.cache_release(app, selected, "old notes")
    store = scheduler_store(monkeypatch)
    store.batch_updates.side_effect = protocol.TransportError("Synthetic timeout")
    store.metadata.side_effect = protocol.ServiceError("Synthetic rejection")
    handlers.run_check_all(triggered_by=100)
    assert db.app_cache(app) == (selected, "old notes")
    assert db.pending_subscribers(app, selected) == []
    assert scheduler_transport[0].send_message.call_args.args[1] == "更新检查已完成。0 项查询成功，1 项查询失败。"


def test_first_subscription_with_no_baseline_uses_single_app_metadata(monkeypatch, scheduler_transport):
    import database as db
    import handlers
    app = protocol.AppRequest("com.example.app", "CN")
    db.add_subscription(100, app)
    store = scheduler_store(monkeypatch)
    handlers.run_check_all()
    store.batch_updates.assert_not_called()
    store.metadata.assert_called_once_with(app)
    store.details.assert_called_once()


def test_batch_new_candidate_confirms_metadata_then_marks_only_confirmed_delivery(monkeypatch, scheduler_transport):
    import database as db
    import handlers
    import requests
    app = protocol.AppRequest("com.example.app", "CN")
    db.add_subscription(100, app)
    db.add_to_whitelist(200)
    db.add_subscription(200, app)
    db.cache_release(app, release(), "old notes")
    selected = release(version_code=124, version_name="1.2.4")
    store = scheduler_store(monkeypatch, selected)
    store.batch_updates.return_value = {app.package: {"GUID":app.package, "region":"CN"}}
    scheduler_transport[1].send.side_effect = [Mock(message_id=1), requests.Timeout("uncertain")]
    handlers.run_check_all()
    store.batch_updates.assert_called_once()
    store.ods_metadata.assert_called_once_with(app.package, "CN")
    store.metadata.assert_not_called()
    store.details.assert_called_once_with(selected)
    store.authorize.assert_not_called()
    assert db.pending_subscribers(app, selected) == [200]
    assert db.app_cache(app)[0] == selected


def test_batch_us_confirmation_failure_keeps_auto_single_app_fallback(monkeypatch, scheduler_transport):
    import database as db
    import handlers
    app = protocol.AppRequest("com.example.app")
    db.add_subscription(100, app)
    db.cache_release(app, release(region="US", updated_date="2026-08-25"), "old notes")
    store = scheduler_store(monkeypatch)
    store.batch_updates.return_value = {app.package: {"GUID":app.package, "region":"US"}}
    store.ods_metadata.side_effect = protocol.ServiceError("Synthetic US rejection")
    handlers.run_check_all()
    store.batch_updates.assert_called_once_with([release(region="US", updated_date="2026-08-25")], "US")
    store.metadata.assert_called_once_with(app)
    assert db.app_cache(app)[0].region == "CN"


def test_batches_keep_explicit_regions_separate_and_do_not_repeat_same_user_app(monkeypatch, scheduler_transport):
    import database as db
    import handlers
    cn = protocol.AppRequest("com.example.app", "CN")
    us = protocol.AppRequest("com.example.app", "US")
    for app in (cn, us):
        db.add_subscription(100, app)
        db.add_subscription(100, app)
        db.cache_release(app, release(region=app.region, updated_date="2026-08-25"), "old notes")
    store = scheduler_store(monkeypatch)
    store.metadata.side_effect = lambda app: release(region=app.region)
    handlers.run_check_all()
    assert [(call.args[1], len(call.args[0])) for call in store.batch_updates.call_args_list] == [("US", 1), ("CN", 1)]
    assert store.metadata.call_count == 2
    store.details.assert_not_called()


@pytest.mark.parametrize("nested", [False, True])
def test_authorization_rejects_a_non_scalar_shadow_of_a_critical_field(nested):
    values = detail_fields(contentsSize="42", downLoadURI="https://download.samsungapps.com/full.apk")
    doc = ET.fromstring(lists([values], request_id="2311"))
    extra = ET.SubElement(doc.find("response/list"), "extList", name="versionCode")
    if nested:
        ET.SubElement(extra, "value", name="versionCode").text = "999"
    else:
        extra.text = "999"
    with pytest.raises(protocol.InvalidResponse):
        protocol.parse_ods_grant(ET.tostring(doc), release())


@pytest.mark.parametrize("restore, actual_id", [(False,"2801"), (True,"2311")])
def test_authorization_response_id_is_bound_to_the_called_endpoint(restore, actual_id):
    values = detail_fields(contentsSize="42", downLoadURI="https://download.samsungapps.com/full.apk")
    with pytest.raises(protocol.InvalidResponse):
        protocol.parse_ods_grant(lists([values], request_id=actual_id), release(), restore=restore)


def test_metadata_response_id_cannot_impersonate_a_different_operation():
    values = detail_fields(needToLogin="0", installableYN="Y")
    with pytest.raises(protocol.InvalidResponse):
        protocol.parse_ods_metadata(lists([values], request_id="2311"), "com.example.app")



def test_auto_cn_baseline_is_not_sent_as_an_installed_us_version(monkeypatch, scheduler_transport):
    import database as db
    import handlers
    app = protocol.AppRequest("com.example.app")
    baseline = release(updated_date="2026-08-25")
    db.add_subscription(100, app)
    db.cache_release(app, baseline, "old notes")
    store = scheduler_store(monkeypatch, baseline)
    handlers.run_check_all()
    store.batch_updates.assert_not_called()
    store.metadata.assert_called_once_with(app)
    store.details.assert_not_called()


def test_same_version_with_a_changed_full_size_cannot_reuse_previous_details(monkeypatch, scheduler_transport):
    import database as db
    import handlers
    app = protocol.AppRequest("com.example.app", "CN")
    db.add_subscription(100, app)
    db.cache_release(app, release(updated_date="2026-08-25"), "old notes")
    selected = release(size=43)
    matched = release(size=43, updated_date="2026-08-26")
    store = scheduler_store(monkeypatch, selected)
    store.details.side_effect = lambda selected: (matched, "matched notes")
    handlers.check_app(app)
    store.details.assert_called_once_with(selected)
    assert db.app_cache(app) == (matched, "matched notes")


def test_missing_cached_date_is_retried_then_known_exact_details_are_reused(monkeypatch, scheduler_transport):
    import database as db
    import handlers
    app = protocol.AppRequest("com.example.app", "CN")
    db.add_subscription(100, app)
    db.cache_release(app, release(), "old notes")
    matched = release(updated_date="2026-08-25")
    store = scheduler_store(monkeypatch)
    store.details.side_effect = lambda selected: (matched, "matched notes")
    handlers.check_app(app)
    handlers.check_app(app)
    store.details.assert_called_once_with(release())
    assert db.app_cache(app) == (matched, "matched notes")
