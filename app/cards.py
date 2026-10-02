"""One card model renders both InputRichMessage blocks and escaped HTML.

RichText follows the Bot API shape: str, {"type": "bold"|"code", "text": ...},
or a list of those. The HTML fallback renders the same data, so both forms
carry the same wording. Explanations go to footers (grey) rather than italics,
which are hard to see in Chinese on real clients.
"""

from dataclasses import dataclass
from html import escape


def bold(text):
    return {"type": "bold", "text": text}


def code(text):
    return {"type": "code", "text": text}


def rich_html(text):
    if isinstance(text, str):
        return escape(text)
    if isinstance(text, (list, tuple)):
        return "".join(rich_html(part) for part in text)
    tag = {"bold": "b", "code": "code"}[text["type"]]
    return f"<{tag}>{rich_html(text['text'])}</{tag}>"


def rich_text(text):
    # Rich blocks take str, a node, or a list; tuples are only our shorthand.
    if isinstance(text, (list, tuple)):
        return [rich_text(part) for part in text]
    return text


def table(rows):
    # Borderless compact two-column table: labels and values line up.
    return {
        "type": "table",
        "cells": [
            [
                {"text": rich_text(cell), "align": "left", "valign": "top"}
                for cell in row
            ]
            for row in rows
        ],
        "is_bordered": False,
        "is_striped": False,
        "is_compact": True,
    }


@dataclass(frozen=True)
class Section:
    summary: str
    text: object = ""
    rows: tuple = ()
    note: str = ""
    opened: bool = False

    def blocks(self):
        inner = []
        if self.rows:
            inner.append(table(self.rows))
        if self.text:
            inner.append({"type": "paragraph", "text": rich_text(self.text)})
        if self.note:
            inner.append({"type": "footer", "text": self.note})
        return {
            "type": "details",
            "summary": self.summary,
            "is_open": self.opened,
            "blocks": inner,
        }

    def html(self):
        lines = [f"<b>{escape(self.summary)}</b>"]
        lines.extend(f"{rich_html(a)} — {rich_html(b)}" for a, b in self.rows)
        if self.text:
            lines.append(rich_html(self.text))
        if self.note:
            lines.append(f"<i>{escape(self.note)}</i>")
        body = "\n".join(lines)
        return body if self.opened else f"<blockquote expandable>{body}</blockquote>"


@dataclass(frozen=True)
class Entry:
    """A list item after kdbot: a head line plus a quote, so items stay apart.

    Each line is its own block; no RichText mixes nodes across line breaks.
    """

    head: object
    quote: object
    credit: str = ""

    def blocks(self):
        quote = {
            "type": "blockquote",
            "blocks": [{"type": "paragraph", "text": rich_text(self.quote)}],
        }
        if self.credit:
            quote["credit"] = self.credit
        return [{"type": "paragraph", "text": rich_text(self.head)}, quote]

    def html(self):
        credit = f"\n{escape(self.credit)}" if self.credit else ""
        return (
            f"{rich_html(self.head)}\n"
            f"<blockquote>{rich_html(self.quote)}{credit}</blockquote>"
        )


@dataclass(frozen=True)
class Card:
    title: str
    paragraphs: tuple = ()
    footer: str = "APKDL · Galaxy Store"
    sections: tuple[Section, ...] = ()
    highlight: str = ""
    facts: tuple = ()
    entries: tuple[Entry, ...] = ()

    def blocks(self):
        blocks = [{"type": "heading", "size": 4, "text": self.title}]
        if self.highlight:
            blocks.append({"type": "heading", "size": 5, "text": self.highlight})
        if self.facts:
            blocks.append(table(self.facts))
        blocks.extend(
            {"type": "paragraph", "text": rich_text(p)} for p in self.paragraphs
        )
        for entry in self.entries:
            blocks.extend(entry.blocks())
        blocks.extend(section.blocks() for section in self.sections)
        if self.footer:
            blocks.append({"type": "footer", "text": self.footer})
        return blocks

    def html(self):
        lines = [f"<b>{escape(self.title)}</b>"]
        if self.highlight:
            lines.append(f"<b>{escape(self.highlight)}</b>")
        if self.facts:
            lines.append(
                "\n".join(f"{escape(k)}：{rich_html(v)}" for k, v in self.facts)
            )
        lines.extend(rich_html(p) for p in self.paragraphs)
        lines.extend(entry.html() for entry in self.entries)
        lines.extend(section.html() for section in self.sections)
        if self.footer:
            lines.append(f"<i>{escape(self.footer)}</i>")
        return "\n\n".join(lines)


def short(value, maximum):
    return value if len(value) <= maximum else value[: maximum - 1] + "…"


def region_label(region):
    return {"CN": "🇨🇳 CN", "US": "🇺🇸 US", "AUTO": "🌐 AUTO"}.get(region, region)


def release_card(release, notes=None, *, update=False):
    size = f"{release.size / 1_000_000:.2f} MB" if release.size is not None else "未知"
    return Card(
        title=short(release.name, 100) + (" · 有更新" if update else ""),
        highlight=f"版本 {short(release.version_name, 100)}",
        facts=(
            ("版本代码", str(release.version_code)),
            ("更新日期", release.updated_date or "暂无数据"),
            ("地区", region_label(release.region)),
            ("大小", size),
            ("包名", code(release.package)),
        ),
        sections=(Section("更新说明（CN）", short(notes, 1200)),) if notes else (),
        footer="" if update else "链接约 10 分钟有效，过期请点「刷新」。",
    )


def subscription_card(app, added, name=None):
    return Card(
        f"{short(name, 100) if name else 'APKDL'} · 订阅",
        highlight="订阅成功" if added else "已经订阅过了",
        facts=(("包名", code(app.package)), ("地区", region_label(app.region))),
        footer="下次检查时会先推送当前版本，之后有新版本再通知。",
    )


def help_card():
    return Card(
        "APKDL",
        ("查询 Samsung Galaxy Store 应用版本，获取 APK 下载链接并订阅更新。",),
        sections=(
            Section(
                "下载与订阅",
                rows=(
                    (code("/dl <包名或链接> [CN|US]"), "获取下载链接"),
                    (code("/sub <包名或链接> [CN|US]"), "订阅更新"),
                    (
                        code("/unsub <包名或链接> [CN|US]"),
                        "取消订阅；不写地区则取消该应用所有地区",
                    ),
                    (code("/unsub all"), "取消全部订阅"),
                    (code("/list"), "查看我的订阅"),
                ),
                note="直接发送包名或 Galaxy Store 详情链接也能获取下载链接。"
                "点「下载」由手机直接从 Samsung 下载；链接约 10 分钟有效，过期点「刷新」。",
                opened=True,
            ),
            Section(
                "地区",
                "不写地区时先查 US，失败再查 CN；写明 CN 或 US 时只查该地区。\n"
                "更新日期和更新说明取自 CN 商店，仅在版本一致时显示。",
            ),
            Section(
                "管理员",
                rows=(
                    (code("/check"), "立即检查所有订阅"),
                    (code("/status"), "查看所有用户的订阅"),
                    (code("/add <用户ID> [备注]"), "加入白名单"),
                    (code("/del <用户ID>"), "移出白名单"),
                    (code("/user"), "查看白名单"),
                    (code("/help"), "显示本帮助"),
                ),
            ),
        ),
    )
