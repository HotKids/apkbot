# 验证记录

日期：2026-10-02。当前版本使用 Telegram 按钮内的 Samsung 临时直链，失效后手动刷新。

## 当前行为

- `/dl` 或直接发送包名：查询版本和更新说明、申请下载授权，发送“下载”和“刷新链接”按钮。临时 URL 只出现在“下载”按钮中。
- “刷新链接”通过 Telegram 回调重新查询并授权，发送新的下载卡片。应用记录保存在 SQLite，刷新不创建订阅。
- `/sub` 只保存订阅；后台检查只查询版本和更新说明。订阅回执和更新通知使用“获取链接”按钮，用户点击时才申请授权。
- bot 和诊断脚本均不下载 APK，不请求 CDN 文件或上传 Telegram 附件。临时链接不写入 SQLite 或日志。
- 使用 Telegram 官方 API 和长轮询。部署只需 `BOT_TOKEN`、`OWNER_ID`；Compose 只有一个 bot 容器，不发布端口。
- 已移除 HTTP 跳转服务、签名入口、域名/监听配置、Caddy 示例、本地 Bot API 容器，以及完整 APK 下载、Manifest 解析和相关依赖。

## 本轮验证

- 离线测试：工作环境和全新 Python 3.12 环境均为 **122 passed**；依赖检查、Ruff 静态检查和格式检查通过。覆盖来源解析、地区选择、错误处理、Samsung URL 校验、权限、订阅持久化、通知送达状态、Telegram 发送与回退，以及启动和停止流程。
- 真实来源解析器配合模拟 HTTP 响应验证 CN 下载卡片：仅请求元数据、更新说明和授权，三个请求后直接把授权 URL 放入按钮，没有请求 APK。
- 刷新测试从首张卡片读取回调，重新打开数据库，再执行回调；确认新卡片使用新版本和新 URL，且没有创建订阅或持久化临时链接。
- 发送超时不自动重发；来源或授权失败不会发出错误下载卡片。移除白名单后，用户不能继续刷新链接。
- 启动测试拦截生产轮询，并禁止绑定 socket；确认不需要公网地址、不监听端口，订阅调度和 SIGTERM 停止仍工作。
- Compose 使用合成的两项配置校验：只有 `apkdl-bot`，没有发布端口，`/data` 持久化。
- 本轮未发送真实 Telegram 消息，也未在手机上下载 APK。

## 来源实测记录

以下是此前同一轮 Galaxy Store 开发中的实测结果，本次删除部署组件未重新发起这些外部请求：

| 包名 | CN 版本 | VersionCode | 授权情况 |
| --- | --- | --- | --- |
| `com.lucky.luckyclient` | 5.6.1 | 5601 | 成功 |
| `com.xingin.xhs` | 9.49.0 | 9490803 | 成功 |
| `me.ele` | 12.9.68 | 1602 | 成功 |
| `com.larus.nova` | 15.2.0 | 15020040 | 元数据成功，授权返回 ServiceError |

Lucky Coffee 授权返回的 CDN 为 `cdnet-dn.galaxyappstore.com`，文件大小为 127784966 字节。前三项应用的 CN 更新说明与查询版本匹配。

US stub 曾成功查询 `com.sec.android.app.samsungapps`，版本 4.6.11.4、VersionCode 461104100。这不代表 US ODS、所有应用或登录受限应用可用。

## 部署后仍需验证

本轮 Docker 构建在获取 `python:3.12-slim` 时被 Docker Hub 的 HTTP 429 限流阻断，未构建出镜像。请在 VPS 按 README 构建并启动；离线测试通过不代表已经部署。

在 Telegram 私聊验证 `/dl com.lucky.luckyclient CN`，点击“下载”，再点击“刷新链接”并使用新卡片下载。然后用 `/sub`、`/check` 验证订阅通知及其“获取链接”按钮。

实际 Telegram 客户端展示、手机到 Samsung CDN 的访问，以及容器在 VPS 上的运行仍需部署验证。临时链接有效期由 Samsung 决定；白名单撤销无法撤回已经发出的 Samsung 链接。
