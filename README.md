# APKDL

通过 Telegram 查询 **Samsung Galaxy Store** 应用版本、下载 APK，并订阅更新。

下载卡片提供三个按钮：

- **下载**：内置 Samsung 临时直链，点击后由手机直接下载 APK。
- **刷新**：重新查询最新版本并获取直链，直接更新原卡片及“下载”按钮，不另发消息。链接失效后点它，再点同一张卡片上的“下载”。刷新失败时弹出提示；若查询太久、提示已无法弹出，则在卡片下回复失败原因。
- **复制文件名**：复制“应用名_版本号.apk”，下载后用它给文件改名（Samsung 下发的文件名固定为 `App_时间戳.apk`，见[下载说明](#下载说明)）。

卡片顶部突出应用名和版本，版本代码、商店更新时间、地区、大小和包名以对齐的表格直接显示，包名为可复制的代码样式；更新说明默认折叠。更新时间采用同包名、同版本的 CN 商店日期，缺少匹配数据时显示“暂无数据”。下载卡片底部提示“链接约 10 分钟有效，过期请点刷新”。订阅更新在标题标明“有更新”。订阅确认和列表显示上次成功抓取的应用名，无缓存时保留包名。

APK 文件不经过 VPS。部署只需 Bot Token 和管理员 ID，无需域名、反向代理或开放入站端口。

## 部署

VPS 需要安装 Git、[Docker Engine 和 Compose 插件](https://docs.docker.com/engine/install/)，并能访问 Telegram 和 Samsung Galaxy Store。

### 1. 获取代码

```sh
git clone https://github.com/HotKids/apkdl-tg-bot.git
cd apkdl-tg-bot
cp -n .env.example .env
chmod 600 .env
nano .env
```

`.env` 只需填写两项：

```dotenv
BOT_TOKEN=填写BotFather提供的Token
OWNER_ID=填写你的Telegram数字ID
```

`OWNER_ID` 是数字用户 ID，不是 `@用户名`。Token 只填在服务器的 `.env` 中。

### 2. 启动

```sh
docker compose up -d --build
docker compose logs --tail=100 -f apkdl-bot
```

默认只运行一个 bot 容器。看到 `APKDL started` 后，在 Telegram 私聊 bot：

```text
/dl com.lucky.luckyclient CN
```

“正在查询”消息会直接变成下载卡片。点“下载”获取 APK；点“刷新”后，原卡片会更新，不再发送第二条消息。退出日志查看可按 `Ctrl+C`，bot 仍在后台运行。

### 3. 更新与停止

在项目目录更新：

```sh
git pull --ff-only
docker compose up -d --build
```

停止：

```sh
docker compose down
```

订阅和白名单保存在 `./data/app.db`。升级时保留 `data` 目录和 `.env`，不需要重新配置。

容器以 `apkdl` 用户（UID 10001）运行，启动时自动把 `./data` 的所有权交给它，旧版本以 root 写入的数据无需手动处理。若所有权无法更改（如只读挂载），会在日志提示并继续以 root 运行。

## 使用

直接发送包名或 Galaxy Store 详情链接，也可以使用 `/dl`。分享链接末尾的 `?session_id=…` 等参数会被忽略：

```text
com.lucky.luckyclient CN
/dl com.tencent.qqmusic CN
https://galaxystore.samsung.com/detail/com.lucky.luckyclient CN
```

| 命令 | 用途 |
| --- | --- |
| `/dl <包名或详情链接> [CN\|US]` | 查询版本并发送下载卡片 |
| `/sub <包名或详情链接> [CN\|US]` | 订阅更新 |
| `/unsub <包名或详情链接> [CN\|US]` | 取消订阅；不写地区时取消该应用所有地区的订阅 |
| `/unsub all` | 取消自己的全部订阅 |
| `/list` | 查看自己的订阅及缓存版本 |
| `/check` | 立即检查所有订阅，仅管理员 |
| `/status` | 查看所有订阅的缓存状态，仅管理员 |
| `/add <用户ID> [备注]` | 添加白名单，仅管理员 |
| `/del <用户ID>` | 移除白名单，仅管理员 |
| `/user` | 查看白名单，仅管理员 |
| `/help` | 查看帮助，仅管理员 |

仅管理员和白名单用户可在私聊使用。bot 启动约 1 分钟后先检查一次更新，之后默认每 24 小时检查；只有版本代码高于上次通知时才推送，同一版本在 US/CN 之间切换或版本回退不会推送；订阅回执和更新通知只有一个“获取下载链接”按钮；点击后原位更新卡片，按钮变成“下载”和“刷新”，订阅继续保留。

地区显示为 `🇨🇳 CN`、`🇺🇸 US`。省略地区时使用 `🌐 AUTO`：先尝试 US，查询或获取下载链接失败时再尝试 CN。显式指定 `CN` 或 `US` 时只查询对应地区。

## 可选设置

通常无需修改。需要时在 `.env` 追加：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `CHECK_INTERVAL` | `1440` | 检查更新的间隔，单位分钟；启动约 1 分钟后先查一次 |
| `TZ` | `Asia/Shanghai` | 时区 |
| `REQUEST_TIMEOUT` | `60` | 单次商店请求总时限，单位秒 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

修改后运行 `docker compose up -d` 生效。

## 下载说明

实测 CN 下载链接约 10 分钟有效，最终有效期由 Samsung 决定。bot 无法感知手机端的下载失败；旧链接失效时使用“刷新”，不会自动续期。刷新可能返回更新版本，原卡片的版本信息和下载按钮会一起更新。

临时直链仅放在 Telegram 按钮里，不显示长链接，不写入数据库或应用日志。白名单限制的是向 bot 获取链接的权限；已经发出的 Samsung 链接在商店判定失效前仍可能使用。

下载的文件名由 Samsung 服务器的 `Content-Disposition` 固定（如 `App_20260924084213356.apk`），bot 无法改成“应用名_版本号”：链接参数会被忽略，改动路径会使签名失效。要改名只能让 APK 经过 VPS 或其他中转，与本项目不经手 APK 的设计相悖。因此卡片提供“复制文件名”按钮，下载完成后在文件管理器里粘贴改名。

Samsung 自家的部分 US 版本以内部发布名上架（如 `[260921] GALAXY Store 9CR Update -  US`，US 商店页面同样如此）。遇到这种名称时，卡片改用同包名 CN 商店页面的应用名。

部分应用有地区、设备或登录限制，无法保证所有应用都可匿名下载。CN 使用 ODS，US 使用 stub；更新说明取自版本匹配的 CN 商店页面。bot 不读取 APK 文件，也不进行文件签名或哈希校验。

## 卡片样式

消息使用 Telegram Bot API 的富文本卡片（`sendRichMessage` / `editMessageText(rich_message=…)`），版式参考 kdbot：

- 卡片与 HTML 回退版由同一份数据渲染，文案一致；说明类文字放灰色页脚，不用斜体区分层级。
- 按钮画在卡片内部：“下载”为绿色，“刷新”“获取下载链接”为蓝色，“复制文件名”为默认样式。服务端不接受卡内按钮时，本次运行改为“卡片 + 行内键盘”。
- `/help` 每节一个折叠块，命令和说明排成两列表；`/list`、`/status` 每个应用一段，应用名加粗、包名为代码样式。
- 服务端明确拒绝富文本时回退为 HTML 消息；超时等结果不确定的情况不重发，避免重复消息。

## 开发与测试

使用 Python 3.12：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r app/requirements.txt pytest==9.1.1
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q -p no:cacheprovider
```

测试使用临时数据库和模拟请求，不需要真实 Token。直接运行 `app/main.py` 时须通过进程环境提供 `BOT_TOKEN`、`OWNER_ID`，并将 `DB_PATH` 设为可写路径；Python 不会自动加载 `.env`。

来源诊断仅查询元数据或授权，不下载 APK：

```sh
.venv/bin/python scripts/probe_galaxy_store.py com.lucky.luckyclient --region CN --notes --authorize
```

验证记录见 [docs/verification.md](docs/verification.md)。
