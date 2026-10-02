# APKDL

从 **Samsung Galaxy Store** 查询版本、提供一键下载入口，并通过 Telegram 管理订阅。Python 3.12、同步 TeleBot、SQLite 和 APScheduler，保留单 Bot 架构。

点击结果卡中的“下载”，VPS 当场查询最新版本并申请新的 Samsung 授权，然后返回 HTTP 302，让手机自动跳转下载。**每次点击都会获取新链接，无需手动刷新。** VPS 只处理少量元数据及跳转响应，不下载或上传 APK，文件内容由 Samsung 直接传到手机。

## 开发与测试

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r app/requirements.txt pytest==9.1.1
.venv/bin/python -m pytest -q -p no:cacheprovider
```

测试使用合成设置和临时数据库，阻止外部 HTTP/Telegram 请求。跳转测试会启动临时回环 HTTP 服务并发出本地请求，Samsung 响应仍被模拟。不需要真实 Bot Token，不启动生产轮询。

GitHub Actions 在 `main` 推送和 Pull Request 时执行同一套 Python 3.12 测试及依赖检查，不读取生产凭据。

无需凭据的来源探测：

```sh
# 仅查询元数据及匹配的 CN 更新说明。
.venv/bin/python scripts/probe_galaxy_store.py com.lucky.luckyclient --region CN --notes
# 验证下载授权；只输出 CDN 主机和大小，不打印临时 URL，也不请求 APK 内容。
.venv/bin/python scripts/probe_galaxy_store.py com.lucky.luckyclient --region CN --notes --authorize
```

`--authorize` 和 `--download-to` 互斥。申请下载授权可能记录 Samsung 匿名下载/订单活动。两者都不会发送 Telegram 消息或安装 APK。

保留完整 APK 下载作为**独立诊断工具**，只在显式指定目标目录时执行，Bot 不调用它：

```sh
.venv/bin/python scripts/probe_galaxy_store.py com.lucky.luckyclient --region CN --download-to data/verification
# 旧 stub 通道须显式指定地区；这不代表 ODS 查询结果。
.venv/bin/python scripts/probe_galaxy_store.py com.tencent.qqmusic --region CN --channel stub
```

当前结果和未验证项见 [docs/verification.md](docs/verification.md)。

## Bot 配置与运行

在运行环境中安全设置 `BOT_TOKEN` 和正整数 `OWNER_ID`，不要将凭据提交到 Git 或发到聊天中。`.env.example` 仅为配置模板。

| 变量 | 默认值 / 用途 |
| --- | --- |
| `BOT_TOKEN` | BotFather Token，Bot 运行时必填 |
| `OWNER_ID` | 管理员 Telegram user ID，Bot 运行时必填 |
| `PUBLIC_DOWNLOAD_BASE_URL` | 必填，跳转入口的公网 HTTPS 地址，例如 `https://dl.example.com`；不带路径或参数 |
| `DOWNLOAD_BIND_HOST` | 直接运行默认 `127.0.0.1`；Compose 容器内为 `0.0.0.0` |
| `DOWNLOAD_BIND_PORT` | `8080`；Compose 内固定使用此端口 |
| `TZ` | `Asia/Shanghai` |
| `CHECK_INTERVAL` | `1440` 分钟 |
| `REQUEST_TIMEOUT` | `60` 秒，单次来源请求时限 |
| `DB_PATH` | `/data/app.db` |
| `LOG_LEVEL` | `INFO`；TeleBot/HTTP 调试日志保持关闭，避免记录临时链接 |
| `LOCAL_BOT_API_URL` | 留空即可使用 Telegram 官方 API |
| `API_ID` / `API_HASH` | 仅可选的本地 Bot API 容器需要；链接模式不需要 |

直接运行时，Python 不会自动读取 `.env`。通过进程环境注入凭据和公网入口配置，将数据库放到有写权限的目录：

```sh
export DB_PATH="$PWD/data/app.db"
export PUBLIC_DOWNLOAD_BASE_URL="https://dl.example.com"
.venv/bin/python app/main.py
```

Docker Compose 会读取本地 `.env`：

```sh
# 首次运行且 .env 不存在时，从模板创建并填写 Bot 凭据和公网地址。
cp -n .env.example .env
docker compose up -d --build apkdl-bot
```

**链接交付不需要本地 Bot API，也不受 Telegram 的文件上传限额约束。** 默认保留 `LOCAL_BOT_API_URL`、`API_ID` 和 `API_HASH` 为空。要让手机点击后自动下载，需要把上述公网地址反向代理到跳转服务。

### 公网 HTTPS 跳转入口

将自己的域名指向 VPS，在 `.env` 中设置 `PUBLIC_DOWNLOAD_BASE_URL=https://实际域名`。Compose 只向宿主机回环地址开放 `127.0.0.1:8080`，由宿主机上的 HTTPS 反向代理对外提供服务，不公开裸 HTTP 端口。

仓库提供 [Caddy 配置示例](deploy/Caddyfile.example)。替换示例域名后，将其合并到宿主机的 Caddy 配置；开放 Caddy 所需的 80/443 端口，并在部署机器上执行配置校验及重载。示例假设 Caddy 运行在宿主机；容器内代理须改用实际容器网络地址。

```sh
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

其他代理需将 `/d/` 和 `/healthz` 原样转发到 `http://127.0.0.1:8080`，为来源查询预留至少 150 秒，不缓存 302 响应，也不记录含签名的完整请求路径。公网 `/healthz` 返回 `ok` 只证明跳转服务可达，实际下载仍需手机验证。

示例域名不可直接使用。未配置合法 `PUBLIC_DOWNLOAD_BASE_URL` 时 Bot 会拒绝启动，避免发出不可用的下载按钮。

已有本地 API 的部署仍可继续使用：设置 `LOCAL_BOT_API_URL=http://telegram-bot-api:8081`、`API_ID`、`API_HASH`，通过 `docker compose --profile local-api up -d --build` 启动。该服务不公开宿主机端口；不需要为了链接模式额外部署它。切换 API 服务端时，停止旧 Bot 实例并遵循 [Telegram 官方迁移说明](https://github.com/tdlib/telegram-bot-api#switching) 的 `logOut` 流程，避免同一 Bot 同时连接两个服务端。

启动会先执行 `getMe` 和 `getWebhookInfo`，初始化数据库并启动跳转服务。凭据、API 连接、webhook 或 HTTP 端口不符合要求时直接失败。轮询结束时关闭跳转服务；Compose 为退出预留 45 秒。`./data` 持久保存 SQLite 和已有文件，升级不清空数据卷。

部署后查看 `docker compose logs --tail=100 apkdl-bot`，在私聊验证：

1. `/help` 显示获取链接的新说明。
2. `/dl com.lucky.luckyclient CN` 返回应用卡，只有一个“下载”按钮。
3. 点击“下载”，确认浏览器自动跳转到 Samsung 并下载。稍后再次点击原消息中的同一按钮，应重新申请新链接；无需再点“刷新”。浏览器可能按手机设置询问是否保存文件。
4. `/sub com.lucky.luckyclient CN`、`/check` 验证通知与持久按钮；订阅检查不应申请 CN 下载授权或下载文件。

## 使用方式

支持包名或严格的详情链接，可追加 `CN` 或 `US`：

```text
com.lucky.luckyclient CN
https://galaxystore.samsung.com/detail/com.tencent.qqmusic CN
```

详情链接不接受查询串、fragment、额外路径或其他域名。

| 命令 | 行为 | 权限 |
| --- | --- | --- |
| `/dl <输入>`、直接发送输入 | 查询版本并发送固定下载入口，不创建订阅 | OWNER / 白名单，私聊 |
| `/sub <输入>` | 只保存订阅，不查询当前可用性 | OWNER / 白名单，私聊 |
| `/unsub <输入>` 或 `all` | 取消指定或全部订阅 | OWNER / 白名单，私聊 |
| `/list` | 查看缓存的订阅状态 | OWNER / 白名单，私聊 |
| `/app` | 提示旧版搜索已停用 | OWNER / 白名单，私聊 |
| `/check` | 执行一次订阅检查 | 仅 OWNER，私聊 |
| `/status` | 显示缓存及保留的旧订阅数 | 仅 OWNER，私聊 |
| `/add <id> [备注]`、`/del <id>`、`/user` | 白名单管理 | 仅 OWNER，私聊 |
| `/help` | APKDL 帮助 | 仅 OWNER，私聊 |

没有 `/start` 命令。旧来源搜索和旧按钮不会请求 APKMirror/APKPure。Galaxy Store 旧的 `gdl:` 回调仍可用，会返回新下载卡。已经发送过的 Samsung 原始 URL 按钮不会自动改写，升级后重新获取一次下载卡即可使用固定入口。

默认地区 `AUTO` 优先 US，仅识别到明确地区不可用响应时才查询 CN。设备/运营商不匹配、stub 通道限制、登录要求、未知错误和网络错误均不触发切区。显式 US/CN 从不切区，AUTO 不比较两区版本高低。

卡片、订阅回执和缓存列表显示 `🇨🇳 CN`、`🇺🇸 US`，自动地区偏好显示 `🌐 AUTO`。命令输入仍使用 `CN` / `US`，应用卡片上的国旗对应实际查询地区。

CN 使用 ODS `getDownloadInfo` 查询元数据，手机打开“下载”入口时才调用 `downloadForRestore`。生成卡片或检查订阅不申请 CN 下载授权。US 保留 stub 通道；US ODS 未实现。若 AUTO 遇到 US stub 限制，可显式选择 CN。

## 订阅与链接交付

- 更新身份是**实际地区 + 产品 ID + VersionCode**。每位订阅者只有在通知消息确认成功后才标记已通知。
- “下载”是带签名的固定公网入口，绑定用户、应用及地区偏好。无需缓存 Samsung 临时地址，程序重启后仍可通过 SQLite 恢复应用记录。每次访问都重新查询最新版本和授权。
- 入口签名由 Bot Token 派生，不暴露 Token，也不能通过修改用户或应用路径伪造权限。它是持有即可使用的链接；白名单撤销后再次访问被拒绝。更换 Bot Token 会使旧入口失效，需要重新获取卡片。
- Samsung 临时 URL 只放在本次 302 的 `Location` 中传给浏览器，不写入 Telegram 卡片、SQLite 或日志。入口不代理文件内容、不发 CDN HEAD/GET，也不使用 `sendDocument` 代拉。
- 跳转响应禁止缓存，浏览器不会被固定到旧的临时地址。原始链接有效期仍由 Samsung 决定，不宣称固定有效时长。授权失败时显示可重试错误，不回退到 VPS 下载。
- 返回下载入口或 302 不代表手机已完成下载。Bot 不读取 APK，所以不验证清单、SHA256 或签名；卡片显示查询时的商店版本，而下载按钮始终选择点击时的最新版本。
- `/list`、`/status` 只使用缓存。订阅回执只确认保存，不宣称已有可用版本或通知成功。
- CN 更新说明必须来自同一包名、CN 国家和逐字相同的 `contentBinaryVersion`。`01.02` 不等于 `1.2`，不据此推断 VersionCode 相同，也不要求两区产品 ID 相同。缺失或不匹配时显示 `CN release notes: unavailable.`。
- 富消息使用现有 TeleBot。仅明确不支持方法或明确拒绝结构时回退 HTML，回退保留下载按钮并关闭链接预览。通用 404、认证错误、限流或超时不触发第二次发送。
- Telegram 发送字段通过 POST 正文传递，关闭 TeleBot/HTTP 调试输出和入口访问日志。发送请求不自动重试或跟随 Telegram 重定向；送达未确认时提示检查聊天记录，不自动重发卡片。

## 来源与数据边界

应用只接受允许列表中的 Samsung HTTPS 地址。来源 GET 采用有限且逐跳校验的重定向；ODS POST 不跟随重定向。302 目标须通过同样的 URL 检查，不能变成任意站点的开放跳转。手机后续的 CDN 请求由浏览器处理。跳转入口同时最多处理 8 个连接，忙时返回 503；健康检查和 HEAD 不申请下载授权。

独立诊断工具的完整下载仍限制为 30 分钟、20 亿字节，要求长度及二进制 Manifest 的包名、versionName、VersionCode 匹配，并计算 SHA256。它不自动验证 APK 签名或全 ZIP CRC。日常链接交付不执行这些步骤。

数据库迁移只增加 `galaxy_apps` 和 `galaxy_subscriptions`，保留白名单及旧 `subscriptions`、`apk_versions` 数据，旧来源订阅保持停用。不要删除数据卷来升级。

Bot 需要网络访问：

- `api.telegram.org`（使用官方 Bot API 时）
- `vas.samsungapps.com`、`cn-ms.galaxyappstore.com`
- `galaxystore.samsung.com`、`apps.galaxyappstore.com`（CN 网站实际重定向）

手机需能访问 Samsung 授权返回的 CDN（实测为 `cdnet-dn.galaxyappstore.com`）。仅使用独立完整下载诊断时，VPS 才需要访问该 CDN。CDN 可能变化，不应因此放宽应用 URL 检查。
