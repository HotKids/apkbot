"""One card model renders both Bot API rich blocks and escaped HTML."""

from dataclasses import dataclass
from datetime import date
from html import escape


def bold(text):
    return {"type": "bold", "text": text}


def code(text):
    return {"type": "code", "text": text}


def app_title(package, name=None):
    return {
        "type": "url",
        "text": bold(short(name, 100) if name and name != package else package),
        "url": "https://galaxystore.samsung.com/detail/" + package,
    }


def rich_html(text):
    if isinstance(text, str):
        return escape(text)
    if isinstance(text, (list, tuple)):
        return "".join(rich_html(part) for part in text)
    if text["type"] == "url":
        return (
            f'<a href="{escape(text["url"], quote=True)}">{rich_html(text["text"])}</a>'
        )
    tag = {"bold": "b", "code": "code"}[text["type"]]
    return f"<{tag}>{rich_html(text['text'])}</{tag}>"


def rich_text(text):
    # Rich blocks take str, a node, or a list; tuples are only our shorthand.
    if isinstance(text, (list, tuple)):
        return [rich_text(part) for part in text]
    if isinstance(text, dict):
        return {**text, "text": rich_text(text["text"])}
    return text


def table(rows):
    # Telegram owns column widths; omit compact for regular cell padding.
    return {
        "type": "table",
        "cells": [
            [
                {"text": rich_text(cell), "align": "left", "valign": "top"}
                for cell in row
            ]
            for row in rows
        ],
        "is_bordered": True,
        "is_striped": True,
    }


@dataclass(frozen=True)
class Section:
    summary: str
    text: object = ""
    rows: tuple = ()
    note: str = ""
    quoted: bool = False

    def blocks(self):
        blocks = [{"type": "heading", "size": 5, "text": self.summary}]
        if self.rows:
            blocks.append(table(self.rows))
        if self.text:
            paragraph = {"type": "paragraph", "text": rich_text(self.text)}
            blocks.append(
                {"type": "blockquote", "blocks": [paragraph]}
                if self.quoted
                else paragraph
            )
        if self.note:
            blocks.append({"type": "footer", "text": self.note})
        return blocks

    def html(self):
        lines = [f"<b>{escape(self.summary)}</b>"]
        lines.extend(f"{rich_html(a)} — {rich_html(b)}" for a, b in self.rows)
        if self.text:
            body = rich_html(self.text)
            lines.append(f"<blockquote>{body}</blockquote>" if self.quoted else body)
        if self.note:
            lines.append(f"<i>{escape(self.note)}</i>")
        return "\n".join(lines)


@dataclass(frozen=True)
class Entry:
    """A linked title and quoted facts keep list entries visually separate."""

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
    title: object
    paragraphs: tuple = ()
    footer: str = ""
    sections: tuple[Section, ...] = ()
    highlight: object = ""
    facts: tuple = ()
    entries: tuple[Entry, ...] = ()

    def blocks(self):
        blocks = [{"type": "heading", "size": 4, "text": rich_text(self.title)}]
        blocks.extend(
            {"type": "paragraph", "text": rich_text(p)} for p in self.paragraphs
        )
        if self.highlight:
            blocks.append(
                {"type": "heading", "size": 5, "text": rich_text(self.highlight)}
            )
        if self.facts:
            blocks.append(table(self.facts))
        for entry in self.entries:
            blocks.extend(entry.blocks())
        for section in self.sections:
            blocks.extend(section.blocks())
        if self.footer:
            blocks.append({"type": "footer", "text": self.footer})
        return blocks

    def html(self):
        lines = [
            f"<b>{escape(self.title)}</b>"
            if isinstance(self.title, str)
            else rich_html(self.title)
        ]
        lines.extend(rich_html(p) for p in self.paragraphs)
        if self.highlight:
            lines.append(f"<b>{rich_html(self.highlight)}</b>")
        if self.facts:
            lines.append(
                "\n".join(f"{escape(k)}：{rich_html(v)}" for k, v in self.facts)
            )
        lines.extend(entry.html() for entry in self.entries)
        lines.extend(section.html() for section in self.sections)
        if self.footer:
            lines.append(f"<i>{escape(self.footer)}</i>")
        return "\n\n".join(lines)


def short(value, maximum):
    return value if len(value) <= maximum else value[: maximum - 1] + "…"


def region_label(region):
    return {"CN": "🇨🇳", "US": "🇺🇸"}.get(region, "")


def version_line(version, region=None):
    flag = region_label(region)
    return f"版本：{short(version, 100)}" + (f" · {flag}" if flag else "")


def updated_date(value):
    if value:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return "暂无信息"


def release_card(release, notes=None, *, update=False):
    size = (
        f"{release.size / 1_000_000:.2f} MB" if release.size is not None else "暂无信息"
    )
    title = app_title(release.package, release.name)
    footer = "" if update else (
        "下载链接失效后，请点击「刷新」。" if release.linked_product
        else "下载链接有效期约为 10 分钟，失效后请点击「刷新」。"
    )
    return Card(
        title=(title, " · 版本更新") if update else title,
        highlight=version_line(release.version_name, release.region),
        facts=(
            ("文件大小", size),
            ("更新时间", updated_date(release.updated_date)),
            ("版本代码", code(str(release.version_code))),
            ("包名", code(release.package)),
        ),
        sections=(Section("更新日志", short(notes, 1200), quoted=True),)
        if notes
        else (),
        footer=footer,
    )


def subscription_card(app, added, release=None, name=None):
    if release and (
        release.package != app.package
        or (app.region != "AUTO" and release.region != app.region)
    ):
        release = None
    return Card(
        app_title(app.package, name or (release.name if release else None)),
        paragraphs=("已订阅。" if added else "该应用已在订阅列表中。",),
        highlight=(
            version_line(release.version_name, app.region)
            if release and release.version_name
            else "暂无版本信息。"
        ),
        facts=(("包名", code(app.package)),),
        footer="检测到新版本时将自动通知。",
    )


def help_card():
    return Card(
        "apkbot",
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
                note="直接发送包名或 Galaxy Store 详情链接也可获取下载链接。"
                "下载文件由设备通过商店提供的链接直接获取。链接失效后，请点击「刷新」。",
            ),
            Section(
                "地区",
                "未指定地区时依次查询 US、CN；指定 CN 或 US 时仅查询该地区。\n"
                "更新时间和更新日志仅展示与当前版本匹配的商店信息。",
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
