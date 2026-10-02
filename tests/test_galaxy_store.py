from dataclasses import replace
import xml.etree.ElementTree as ET
import pytest

from galaxy_store import (
    AppRequest,
    InvalidInput,
    InvalidResponse,
    LoginRequired,
    NoAvailableVersion,
    OdsProfile,
    Release,
    ServiceError,
    StubRestricted,
    VersionDrift,
    match_cn_details,
    parse_input,
    parse_ods_grant,
    parse_ods_metadata,
    parse_stub,
    xml_fields,
)


def ods(fields=None, error="", code="0"):
    root = ET.Element("SamsungProtocol")
    info = ET.SubElement(root, "errorInfo")
    ET.SubElement(info, "errorString", errorCode=code).text = error
    for key, value in (fields or {}).items():
        ET.SubElement(root, "value", name=key).text = str(value)
    return ET.tostring(root)


def metadata(**changes):
    fields = dict(
        GUID="com.example.app",
        productID="00001",
        productName="Example",
        version="01.02.3",
        versionCode="123",
        needToLogin="0",
        installableYN="Y",
        realContentsSize="42",
    )
    fields.update(changes)
    return ods(fields)


def release(**changes):
    return replace(
        Release("com.example.app", "CN", "00001", "Example", "01.02.3", 123, 42),
        **changes,
    )


def stub(message="success", code="1", **changes):
    fields = dict(
        resultCode=code,
        resultMsg=message,
        appId="com.example.app",
        productId="00001",
        productName="Example",
        versionName="01.02.3",
        versionCode="123",
        contentSize="42",
        downloadURI="https://download.samsungapps.com/file.apk?secret=private",
    )
    fields.update(changes)
    root = ET.Element("result")
    for key, value in fields.items():
        ET.SubElement(root, key).text = value
    return ET.tostring(root)


@pytest.mark.parametrize(
    "text,region",
    [
        ("com.example.app", "AUTO"),
        ("com.example.app CN", "CN"),
        ("https://galaxystore.samsung.com/detail/com.example.app US", "US"),
        (
            "https://galaxystore.samsung.com/detail/com.example.app?session_id=W_ab12",
            "AUTO",
        ),
        ("https://galaxystore.samsung.com/detail/com.example.app?q=1#x cn", "CN"),
        ("https://galaxystore.samsung.com/detail/com.example.app?", "AUTO"),
        ("https://galaxystore.samsung.com/detail/com.example.app#", "AUTO"),
    ],
)
def test_accepted_inputs(text, region):
    assert parse_input(text) == AppRequest("com.example.app", region)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "com.example.app CN extra",
        "example",
        "com..app",
        "https://evil.test/detail/com.example.app",
        "https://galaxystore.samsung.com/detail/com.example.app/",
        "https://galaxystore.samsung.com:443/detail/com.example.app",
        "https://galaxystore.samsung.com/detail/com.example.app/extra?x=1",
        "https://galaxystore.samsung.com/detail/?id=com.example.app",
        "com.example.app HK",
        "https://[galaxystore.samsung.com/detail/com.example.app",
    ],
)
def test_rejects_ambiguous_inputs(text):
    with pytest.raises(InvalidInput):
        parse_input(text)


def test_durable_key_includes_preference():
    assert AppRequest("com.example.app").key == AppRequest("com.example.app").key
    assert (
        AppRequest("com.example.app", "CN").key
        != AppRequest("com.example.app", "US").key
    )
    assert len("gdl:" + AppRequest("com.example.app").key) <= 64


@pytest.mark.parametrize(
    "message,kind",
    [
        ("Application is not approved as stub", StubRestricted),
        ("Application is not allowed to use stubDownload", StubRestricted),
        ("No app matched device/country/MCC/MNC/CSC/API conditions", ServiceError),
        ("Application is not available in this country", NoAvailableVersion),
        ("Login required", LoginRequired),
        ("Service error", ServiceError),
    ],
)
def test_stub_restriction_is_not_regional_absence(message, kind):
    with pytest.raises(kind):
        parse_stub(stub(message, "0"), "com.example.app", "US")


def test_stub_identity_and_ephemeral_url():
    grant = parse_stub(stub(), "com.example.app", "US")
    assert grant.release.region == "US"
    assert "secret" not in repr(grant)
    with pytest.raises(InvalidResponse):
        parse_stub(stub(appId="com.other.app"), "com.example.app", "CN")


def test_real_metadata_contract_uses_version_and_error_attribute():
    parsed = parse_ods_metadata(metadata(), "com.example.app")
    assert parsed == release()
    assert parsed.version_name == "01.02.3"
    with pytest.raises(InvalidResponse):
        parse_ods_metadata(metadata(version=""), "com.example.app")


@pytest.mark.parametrize(
    "changes",
    [
        dict(GUID="com.other.app"),
        dict(productID="bad"),
        dict(versionCode="0"),
        dict(versionCode="-1"),
        dict(versionCode="12x"),
        dict(needToLogin="maybe"),
        dict(installableYN="?"),
    ],
)
def test_rejects_inconsistent_metadata(changes):
    with pytest.raises(InvalidResponse):
        parse_ods_metadata(metadata(**changes), "com.example.app")


def test_login_and_installability_are_metadata_not_absence():
    assert parse_ods_metadata(metadata(needToLogin="1"), "com.example.app").needs_login
    assert not parse_ods_metadata(
        metadata(installableYN="N"), "com.example.app"
    ).installable


@pytest.mark.parametrize(
    "payload",
    [
        b"<result/>",
        b"bad",
        b'<!DOCTYPE SamsungProtocol [<!ENTITY x "a">]><SamsungProtocol>&x;</SamsungProtocol>',
        b'<SamsungProtocol><value name="GUID">a</value><value name="GUID">b</value></SamsungProtocol>',
        b'<SamsungProtocol><errorString errorCode="0"/><errorCode>0</errorCode></SamsungProtocol>',
    ],
)
def test_rejects_malformed_ambiguous_xml(payload):
    with pytest.raises(InvalidResponse):
        xml_fields(payload, "SamsungProtocol")


def test_xml_size_bound():
    with pytest.raises(InvalidResponse):
        xml_fields(b"x" * 2_000_001, "SamsungProtocol")


def test_nova_authorization_failure_is_not_absence():
    with pytest.raises(ServiceError):
        parse_ods_grant(ods(error="Service error", code="-1"), release())


def test_ods_grant_case_and_product_binding():
    fields = dict(
        productID="00001",
        downLoadURI="https://download.samsungapps.com/a.apk",
        contentsSize="42",
        binaryArch="64",
    )
    grant = parse_ods_grant(ods(fields), release())
    assert grant.size == 42
    with pytest.raises(InvalidResponse):
        parse_ods_grant(ods(dict(fields, productID="00002")), release())
    with pytest.raises(VersionDrift):
        parse_ods_grant(ods(dict(fields, version="1.2.3")), release())
    with pytest.raises(VersionDrift):
        parse_ods_grant(ods(dict(fields, versionCode="124")), release())


@pytest.mark.parametrize("arch", ["32n64", "32", None])
def test_ods_grant_does_not_confuse_architecture_with_full_apk(arch):
    # Samsung Reminder returns a full 32n64 APK alongside optional delta fields.
    selected = release(
        package="com.samsung.android.app.sreminder",
        product_id="000009060570",
        version_name="9.4.02.7",
        version_code=940207000,
        size=106232505,
    )
    fields = dict(
        productID=selected.product_id,
        contentsSize=selected.size,
        downLoadURI="https://cdnet-dn.galaxyappstore.com/full.apk",
        deltaContentsSize="42",
        deltaDownloadURL="https://cdnet-dn.galaxyappstore.com/delta.patch",
    )
    if arch is not None:
        fields["binaryArch"] = arch
    grant = parse_ods_grant(ods(fields), selected)
    assert grant.release == selected
    assert grant.size == selected.size
    assert grant.url == fields["downLoadURI"]
    with pytest.raises(VersionDrift):
        parse_ods_grant(ods(dict(fields, contentsSize=42)), selected)


def test_ods_profile_reuses_only_synthetic_identity_and_correct_guid_case():
    profile = OdsProfile()
    meta = ET.fromstring(profile.metadata("com.example.app"))
    auth = ET.fromstring(profile.download(release()))
    for root in (meta, auth):
        request = root.find("request")
        assert int(request.attrib["numParam"]) == len(request)
        values = {n.attrib["name"]: n.text for n in request}
        assert values["imei"] == values["extuk"] == values["stduk"] == profile.identity
    assert meta.find("request/param[@name='guid']").text == "com.example.app"
    assert auth.find("request/param[@name='GUID']").text == "com.example.app"
    assert meta.find("request").attrib["id"] == "2298"
    assert auth.find("request").attrib["id"] == "2316"


def test_cn_details_exact_match_never_changes_apk_region():
    chosen = release(region="US", product_id="99999")
    detail = dict(
        countryCode="CHN",
        contentBinaryVersion="01.02.3",
        contentNewDescription=" 更新 ",
        modifyDate="2026.08.25.",
    )

    def response(**changes):
        return dict(appId=chosen.package, DetailMain=dict(detail, **changes))

    assert match_cn_details(response(), chosen) == (chosen, "更新")
    assert match_cn_details(response(contentBinaryVersion="1.2.3"), chosen) == (
        chosen,
        None,
    )
    assert match_cn_details(response(countryCode="USA"), chosen) == (chosen, None)
    assert match_cn_details(dict(response(), appId="com.other.app"), chosen) == (
        chosen,
        None,
    )
    assert match_cn_details(response(contentNewDescription="  "), chosen) == (
        chosen,
        None,
    )
    assert chosen.region == "US"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026.08.25.", "2026-08-25"),
        ("2026.02.30.", None),
        (None, None),
        (1790936758, None),
    ],
)
def test_store_update_date_is_optional_and_does_not_change_release_identity(
    value, expected
):
    original = release()
    response = dict(
        appId=original.package,
        DetailMain=dict(
            countryCode="CHN",
            contentBinaryVersion=original.version_name,
            modifyDate=value,
        ),
    )
    enriched, notes = match_cn_details(response, original)
    assert enriched.updated_date == expected
    assert enriched.identity == original.identity
    assert notes is None
    response["DetailMain"]["contentBinaryVersion"] = "other"
    assert match_cn_details(response, original) == (original, None)
