from html import unescape
from html.parser import HTMLParser

import pytest

from cards import help_card, release_card, subscription_card
from galaxy_store import AppRequest
from tests.test_galaxy_store import release


class ParsedHTML(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.content = []
        self.stack = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, data):
        self.content.append(data)


@pytest.mark.parametrize("region,flag", [("CN", "🇨🇳"), ("US", "🇺🇸")])
def test_release_title_link_and_compact_version_facts(region, flag):
    card = release_card(
        release(name="Application <name>&", region=region, updated_date="2026-08-25")
    )
    html = card.html()
    parsed = ParsedHTML(html)
    assert not parsed.stack
    assert (
        "a",
        {"href": "https://galaxystore.samsung.com/detail/com.example.app"},
    ) in parsed.tags
    assert "Application <name>&" in "".join(parsed.content)
    assert f"版本：01.02.3 · {flag}" in html
    assert f"{flag} {region}" not in html
    assert "地区：" not in html
    facts = next(block for block in card.blocks() if block["type"] == "table")
    assert [row[0]["text"] for row in facts["cells"]] == [
        "文件大小",
        "更新时间",
        "版本代码",
        "包名",
    ]
    assert facts["cells"][1][1]["text"] == "2026-08-25"
    assert facts["cells"][2][1]["text"] == {"type": "code", "text": "123"}
    assert facts["cells"][3][1]["text"] == {"type": "code", "text": "com.example.app"}
    assert "下载链接有效期约为 10 分钟，失效后请点击「刷新」。" in html
    assert "APKDL · Galaxy Store" not in html


def test_store_notes_stay_unchanged_in_an_ordinary_expanded_quote():
    notes = "Publisher notes:\nFixed <special> & characters.\n  Preserve spacing."
    card = release_card(release(), notes)
    blocks = card.blocks()
    quote = next(block for block in blocks if block["type"] == "blockquote")
    assert quote == {
        "type": "blockquote",
        "blocks": [{"type": "paragraph", "text": notes}],
    }
    assert any(block.get("text") == "更新日志" for block in blocks)
    assert all(
        block["type"] not in {"details", "expandable_blockquote"} for block in blocks
    )
    assert (
        f"<blockquote>{notes.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}</blockquote>"
        in card.html()
    )
    assert "expandable" not in card.html()
    assert "更新日志（CN）" not in card.html()


def test_update_notice_uses_real_region_and_has_no_link_expiry_text():
    card = release_card(release(region="US"), "notes", update=True)
    assert " · 版本更新" in card.html()
    assert "版本：01.02.3 · 🇺🇸" in card.html()
    assert "10 分钟" not in card.html()
    assert all(block["type"] != "footer" for block in card.blocks())


@pytest.mark.parametrize(
    "region,cached,expected",
    [
        ("AUTO", release(region="CN"), "版本：01.02.3"),
        ("CN", release(region="CN"), "版本：01.02.3 · 🇨🇳"),
        ("US", release(region="US"), "版本：01.02.3 · 🇺🇸"),
        ("US", release(region="CN"), "暂无版本信息。"),
        ("CN", release(package="com.other.app"), "暂无版本信息。"),
        ("CN", release(version_name=""), "暂无版本信息。"),
        ("AUTO", None, "暂无版本信息。"),
    ],
)
def test_subscription_uses_matching_history_and_only_explicit_region(
    region, cached, expected
):
    card = subscription_card(AppRequest("com.example.app", region), True, cached)
    html = card.html()
    assert expected in html
    assert "已订阅。" in html
    assert "检测到新版本时将自动通知。" in html
    assert "包名：<code>com.example.app</code>" in html
    assert "AUTO" not in html and "🌐" not in html and "地区：" not in html
    assert "10 分钟" not in html
    assert html.index("已订阅。") < html.index(expected) < html.index("包名：")
    if region == "AUTO" or expected == "暂无版本信息。":
        assert "🇨🇳" not in html and "🇺🇸" not in html


def test_subscription_can_use_latest_name_without_a_matching_version():
    card = subscription_card(
        AppRequest("com.example.app", "US"), False, None, "Known name"
    )
    html = card.html()
    assert "Known name" in html
    assert 'href="https://galaxystore.samsung.com/detail/com.example.app"' in html
    assert "该应用已在订阅列表中。" in html
    assert "暂无版本信息。" in html


def test_missing_name_size_and_date_use_neutral_placeholders():
    card = release_card(release(name="", size=None, updated_date=None))
    html = card.html()
    assert 'href="https://galaxystore.samsung.com/detail/com.example.app"' in html
    assert ">com.example.app<" in html
    assert "文件大小：暂无信息" in html
    assert "更新时间：暂无信息" in html


@pytest.mark.parametrize(
    "value,expected",
    [
        ("20260825", "2026-08-25"),
        ("2026-08-25", "2026-08-25"),
        ("2026-02-31", "暂无信息"),
        ("", "暂无信息"),
    ],
)
def test_date_formatting_never_substitutes_query_or_expiry_time(value, expected):
    assert f"更新时间：{expected}" in release_card(release(updated_date=value)).html()


def test_long_publisher_content_is_bounded_before_html_escaping():
    card = release_card(
        release(name="😀<&" * 200, version_name="2.0" * 200),
        "原文😀<&\n" * 1500,
    )
    parsed = ParsedHTML(card.html())
    assert not parsed.stack
    visible = "".join(parsed.content)
    assert len(visible.encode("utf-16-le")) // 2 <= 4096
    assert "…" in unescape(card.html())
    assert "<blockquote expandable" not in card.html()


def test_help_is_expanded_and_keeps_region_input_parameters():
    card = help_card()
    html = card.html()
    assert "/dl &lt;包名或链接&gt; [CN|US]" in html
    assert "/sub &lt;包名或链接&gt; [CN|US]" in html
    assert all(block["type"] != "details" for block in card.blocks())
    assert "expandable" not in html
    assert "点「" not in html and "抓取" not in html and "缓存" not in html
    assert "APKDL · Galaxy Store" not in html
