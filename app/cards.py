"""One card model renders both InputRichMessage blocks and escaped HTML."""

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class Card:
    title: str
    paragraphs: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    footer: str = "APKDL · Galaxy Store"
    sections: tuple[tuple[str, str, bool], ...] = ()

    def blocks(self):
        blocks = [{"type": "heading", "size": 4, "text": self.title}]
        blocks.extend({"type": "paragraph", "text": p} for p in self.paragraphs)
        for summary, text, opened in self.sections:
            blocks.append(
                {
                    "type": "details",
                    "summary": summary,
                    "is_open": opened,
                    "blocks": [{"type": "paragraph", "text": text}],
                }
            )
        if self.details:
            blocks.append(
                {
                    "type": "details",
                    "summary": "技术信息",
                    "is_open": False,
                    "blocks": [{"type": "paragraph", "text": "\n".join(self.details)}],
                }
            )
        blocks.append({"type": "footer", "text": self.footer})
        return blocks

    def html(self):
        lines = [f"<b>{escape(self.title)}</b>"]
        lines.extend(escape(p) for p in self.paragraphs)
        for summary, text, opened in self.sections:
            body = f"<b>{escape(summary)}</b>\n{escape(text)}"
            lines.append(
                body if opened else f"<blockquote expandable>{body}</blockquote>"
            )
        if self.details:
            lines.append(
                "<blockquote expandable>"
                + escape("\n".join(self.details))
                + "</blockquote>"
            )
        lines.append(f"<i>{escape(self.footer)}</i>")
        return "\n\n".join(lines)


def short(value, maximum):
    return value if len(value) <= maximum else value[: maximum - 1] + "…"


def region_label(region):
    return {"CN": "🇨🇳 CN", "US": "🇺🇸 US", "AUTO": "🌐 AUTO"}.get(region, region)


def release_card(release, outcome, notes=None):
    size = release.size
    body = [
        outcome,
        f"Source: Galaxy Store · APK region: {region_label(release.region)}"
        + (f" · {size / 1_000_000:.2f} MB" if size is not None else ""),
    ]
    body.append(
        "CN release notes:\n" + short(notes, 1200)
        if notes
        else "CN release notes: unavailable."
    )
    details = [
        f"Package: {release.package}",
        f"VersionCode: {release.version_code}",
        f"APK product ID: {release.product_id}",
    ]
    return Card(
        f"{short(release.name, 100)} · {short(release.version_name, 100)}",
        tuple(body),
        tuple(details),
    )


def subscription_card(app, added):
    return Card(
        "APKDL · 订阅",
        (
            "已保存订阅。" if added else "此订阅已存在。",
            f"{app.package} · {region_label(app.region)}",
            "尚未确认当前版本或首次通知；后台检查后再通知。",
        ),
    )


def help_card():
    return Card(
        "APKDL",
        ("Source: Galaxy Store",),
        sections=(
            (
                "下载与订阅",
                "/dl <包名或详情链接> [CN|US] — 获取下载入口\n/sub <输入> [CN|US] — 保存订阅\n"
                "/unsub <输入> [CN|US] 或 all — 取消订阅\n/list — 查看已缓存的订阅\n直接发送包名或 Galaxy Store 详情链接也可获取链接。\n"
                "点击「下载」自动获取新链接并开始下载，每次下载当时的最新版本。",
                True,
            ),
            (
                "地区",
                "默认 AUTO 优先 US，仅在明确地区不可用时尝试 CN。\n显式 CN/US 不切区。更新说明仅采用版本完全匹配的 CN 说明。",
                False,
            ),
            (
                "管理员",
                "/check — 检查更新\n/status — 查看缓存状态\n/add <id> [备注]\n/del <id>\n/user — 白名单\n/help — 帮助",
                False,
            ),
        ),
    )
