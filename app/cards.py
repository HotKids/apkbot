"""One card model renders both InputRichMessage blocks and escaped HTML."""

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class Card:
    title: str
    paragraphs: tuple[str, ...] = ()
    footer: str = "APKDL · Galaxy Store"
    sections: tuple[tuple[str, str, bool], ...] = ()
    highlight: str = ""

    def blocks(self):
        blocks = [{"type": "heading", "size": 4, "text": self.title}]
        if self.highlight:
            blocks.append({"type": "heading", "size": 5, "text": self.highlight})
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
        if self.footer:
            blocks.append({"type": "footer", "text": self.footer})
        return blocks

    def html(self):
        lines = [f"<b>{escape(self.title)}</b>"]
        if self.highlight:
            lines.append(f"<b>{escape(self.highlight)}</b>")
        lines.extend(escape(p) for p in self.paragraphs)
        for summary, text, opened in self.sections:
            body = f"<b>{escape(summary)}</b>\n{escape(text)}"
            lines.append(
                body if opened else f"<blockquote expandable>{body}</blockquote>"
            )
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
        highlight=f"版本：{short(release.version_name, 100)}",
        paragraphs=(
            f"版本代码：{release.version_code}",
            f"更新时间：{release.updated_date or '暂无数据'}",
            f"{region_label(release.region)} · {size}",
            f"包名：{release.package}",
        ),
        sections=(("更新说明（CN）", short(notes, 1200), False),) if notes else (),
        footer="" if update else "链接约 10 分钟有效，过期请点「刷新」。",
    )


def subscription_card(app, added, name=None):
    return Card(
        f"{short(name, 100) if name else 'APKDL'} · 订阅",
        (
            "已保存订阅。" if added else "此订阅已存在。",
            f"{app.package} · {region_label(app.region)}",
            "尚未确认当前版本或首次通知；后台检查后再通知。",
        ),
    )


def help_card():
    return Card(
        "APKDL",
        ("来源：Galaxy Store",),
        sections=(
            (
                "下载与订阅",
                "/dl <包名或详情链接> [CN|US] — 获取下载链接\n/sub <输入> [CN|US] — 保存订阅\n"
                "/unsub <输入> [CN|US] 或 all — 取消订阅\n/list — 查看已缓存的订阅\n直接发送包名或 Galaxy Store 详情链接也可获取链接。\n"
                "点击「下载」直接从 Samsung 下载。链接失效后点「刷新」，原卡片更新后再点「下载」。",
                True,
            ),
            (
                "地区",
                "默认先尝试 US，查询或获取下载链接失败时再尝试 CN。\n显式 CN/US 不切区。更新说明仅采用版本完全匹配的 CN 说明。",
                False,
            ),
            (
                "管理员",
                "/check — 检查更新\n/status — 查看缓存状态\n/add <id> [备注]\n/del <id>\n/user — 白名单\n/help — 帮助",
                False,
            ),
        ),
    )
