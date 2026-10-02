# APKDL

在 Telegram 里查询 **Samsung Galaxy Store** 应用版本、获取 APK 下载链接，并订阅更新。

APK 由手机直接从 Samsung 下载，不经过 VPS。部署只需 Bot Token 和管理员 ID，无需域名、反向代理或开放入站端口。

## 卡片

发送包名后，“正在查询”消息会原地变成下载卡片：

- 标题是应用名，下方突出版本号；版本代码、更新日期、地区、大小和包名排成表格，包名可点按复制。
- 更新日期和更新说明取自同包名、同版本的 CN 商店页面，没有匹配数据时显示“暂无数据”；更新说明默认折叠。
- 底部提示“链接约 10 分钟有效，过期请点「刷新」”。

卡片内有两个按钮：

- **⬇️ 下载**：Samsung 临时直链，点击后由手机直接下载 APK。
- **🔄 刷新**：重新查询并获取新链接，原卡片的版本信息和按钮一起更新，不另发消息。链接失效后点它，再点同一张卡片上的“下载”。刷新失败时弹出提示；若查询太久、提示已无法弹出，则在卡片下回复失败原因。

订阅确认和更新通知只有一个“🔗 获取下载链接”按钮，点击后同一张卡片变成下载卡片，订阅保留。更新通知的标题带“有更新”。订阅确认和列表显示上次查询到的应用名，没有记录时显示包名。

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

“正在查询”消息会变成下载卡片。点“下载”获取 APK；点“刷新”会更新原卡片，不会再发一条消息。按 `Ctrl+C` 退出日志查看，bot 仍在后台运行。

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

容器以 `apkdl` 用户（UID 10001）运行，启动时会自动把 `./data` 交给该用户，旧版本以 root 写入的数据无需手动处理。若无法更改所有权（如只读挂载），会在日志中提示并继续以 root 运行。

## 使用

直接发送包名或 Galaxy Store 详情链接，或使用 `/dl`。分享链接末尾的 `?session_id=…` 等参数会自动忽略：

```text
com.lucky.luckyclient CN
/dl com.tencent.qqmusic CN
https://galaxystore.samsung.com/detail/com.lucky.luckyclient CN
```

| 命令 | 用途 |
| --- | --- |
| `/dl <包名或链接> [CN\|US]` | 获取下载链接 |
| `/sub <包名或链接> [CN\|US]` | 订阅更新 |
| `/unsub <包名或链接> [CN\|US]` | 取消订阅；不写地区时取消该应用所有地区 |
| `/unsub all` | 取消自己的全部订阅 |
| `/list` | 查看我的订阅和上次检查到的版本，点应用按钮可直接获取下载链接 |
| `/check` | 立即检查所有订阅（管理员） |
| `/status` | 查看所有用户的订阅（管理员） |
| `/add <用户ID> [备注]` | 加入白名单（管理员） |
| `/del <用户ID>` | 移出白名单（管理员） |
| `/user` | 查看白名单（管理员） |
| `/help` | 显示帮助（管理员） |

- 只有管理员和白名单用户可以使用，且仅限私聊。
- bot 启动约 1 分钟后检查一次更新，之后默认每 24 小时检查。只有版本代码高于上次通知时才推送；同一版本在 US/CN 之间切换或版本回退不推送。
- 地区显示为 `🇨🇳 CN`、`🇺🇸 US`。不写地区时为 `🌐 AUTO`：先查 US，查询或获取下载链接失败再查 CN；写明 `CN` 或 `US` 时只查该地区。

## 可选设置

通常无需修改。需要时在 `.env` 追加：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `CHECK_INTERVAL` | `1440` | 检查更新的间隔（分钟）；启动约 1 分钟后先查一次 |
| `TZ` | `Asia/Shanghai` | 时区 |
| `REQUEST_TIMEOUT` | `60` | 单次商店请求的总时限（秒） |
| `LOG_LEVEL` | `INFO` | 日志级别 |

修改后运行 `docker compose up -d` 生效。

## 下载说明

实测 CN 下载链接约 10 分钟有效，实际有效期由 Samsung 决定。链接不会自动续期，bot 也无法感知手机端的下载失败；失效后点“刷新”即可。刷新时若商店已有新版本，卡片会直接换成新版本。

临时链接只放在按钮里，不在消息中显示，也不写入数据库或日志。白名单只限制向 bot 获取链接；已发出的 Samsung 链接在过期前仍可能被使用。

下载的文件名由 Samsung 服务器的 `Content-Disposition` 固定（如 `App_20260924084213356.apk`），bot 无法改成“应用名_版本号”：链接参数会被忽略，改动路径会使签名失效。要改名只能让 APK 经过 VPS 或其他中转，这与本项目不经手 APK 的设计相悖。

Samsung 自家的部分 US 版本以内部发布名上架（如 `[260921] GALAXY Store 9CR Update -  US`，US 商店页面也是如此）。遇到这类名称时，卡片改用同包名 CN 商店页面上的应用名。

部分应用有地区、设备或登录限制，不保证都能匿名下载。CN 使用 ODS 接口，US 使用 stub 接口。bot 不读取 APK 文件，也不做签名或哈希校验。

## 富文本实现

消息使用 Telegram Bot API 的富文本卡片（`sendRichMessage` / `editMessageText(rich_message=…)`），版式参考 kdbot：

- 卡片与 HTML 回退版由同一份数据渲染，文案一致；说明类文字放灰色页脚，不用斜体区分层级。
- 按钮画在卡片内部：“下载”为绿色，“刷新”“获取下载链接”为蓝色。服务端不接受卡内按钮时，本次运行改用“卡片 + 行内键盘”。
- `/help` 每节一个折叠块，命令和说明排成两列表。
- `/list`、`/status` 每个应用一行标题（应用名加粗 · 订阅地区）加一个引用块（版本 · 实际地区，落款为包名），项与项之间靠引用块分开。`/list` 每个应用配一个“⬇️ 应用名”按钮，点击后另发一张下载卡片，列表保持不变。
- 服务端明确拒绝富文本时回退为 HTML 消息，并在日志记录 `Rich card rejected` 及 Telegram 给出的原因（链接会被隐去），便于排查哪张卡片没有显示成富文本；超时等结果不确定的情况不重发，避免出现重复消息。
- 报错先说明发生了什么、再给出下一步（重试、换地区或重新发送包名）；需要排查时，括号里保留简短的原因。

## 开发与测试

使用 Python 3.12：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r app/requirements.txt pytest==9.1.1
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q -p no:cacheprovider
```

测试使用临时数据库和模拟请求，不需要真实 Token。直接运行 `app/main.py` 时，需要通过环境变量提供 `BOT_TOKEN`、`OWNER_ID`，并把 `DB_PATH` 设为可写路径；Python 不会自动读取 `.env`。

商店诊断脚本只查询元数据或下载授权，不下载 APK：

```sh
.venv/bin/python scripts/probe_galaxy_store.py com.lucky.luckyclient --region CN --notes --authorize
```

验证记录见 [docs/verification.md](docs/verification.md)。
