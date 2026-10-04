# apkbot

Samsung Galaxy Store APK 下载与版本订阅工具。支持 Telegram bot，并提供三星生活助手的浏览器书签及 Python 下载脚本。APK 由三星直接传输至用户设备。

## 使用方式

| 方式 | 适用范围 | 入口 |
| --- | --- | --- |
| Telegram bot | Galaxy Store 应用查询、下载及更新订阅 | [命令说明](#telegram-命令) |
| 浏览器书签 | 中国区三星生活助手 | [书签网址](standalone/samsung-assistant/bookmarklet.txt) · [HTML 安装页](standalone/samsung-assistant/install.html) |
| Python 脚本 | 中国区三星生活助手 | [download.py](standalone/samsung-assistant/download.py) |

独立下载方式详见 [使用说明](standalone/samsung-assistant/README.md)。HTML 和 Python 文件须在 GitHub 文件页下载原始文件；书签网址可直接复制原文。浏览器建议使用 Chrome，Python 需 3.8 或更新版本。

下载链接有效期约为 10 分钟。失效后，bot 用户点击「刷新」，书签用户重新运行书签，Python 用户重新运行脚本。部分应用受地区、设备或账户限制，无法保证所有请求均可下载。

## Telegram 命令

仅限管理员及白名单用户在私聊中使用。可直接发送包名或 Galaxy Store 详情链接，也可输入 `/` 选择命令。

```text
com.samsung.android.app.sreminder CN
```

| 命令 | 用途 |
| --- | --- |
| `/dl <包名或链接> [CN\|US]` | 查询应用并获取下载链接 |
| `/sub <包名或链接> [CN\|US]` | 订阅版本更新 |
| `/list` | 查看自己的订阅 |
| `/unsub <包名或链接> [CN\|US]` | 取消订阅；未指定地区时取消该应用所有地区的订阅 |
| `/unsub all` | 取消自己的全部订阅 |
| `/add <用户ID> [备注]` | 加入白名单，仅限管理员 |
| `/del <用户ID>` | 移出白名单，仅限管理员 |
| `/user` | 查看白名单，仅限管理员 |
| `/status` | 查看全部订阅，仅限管理员 |
| `/check` | 立即检查更新，仅限管理员 |
| `/help` | 查看帮助，仅限管理员 |

未指定地区时依次尝试 US、CN；指定地区时仅查询对应商店。订阅首次检查成功后通知当前可用版本，后续按版本代码判断更新，地区切换本身不视为更新。默认每 24 小时检查，启动约 1 分钟后执行首次检查。

## 首次部署

服务器需安装 Git、[Docker Engine 与 Compose 插件](https://docs.docker.com/engine/install/)，并可访问 Telegram 和三星商店。无需域名或开放入站端口。仅使用独立下载脚本时，无需部署 bot。

```sh
git clone https://github.com/HotKids/apkbot.git
cd apkbot
cp -n .env.example .env
chmod 600 .env
nano .env
```

在 `.env` 中填写 [BotFather](https://t.me/BotFather) 提供的 Token 和管理员数字用户 ID，替换以下占位值：

```dotenv
BOT_TOKEN=your_bot_token
OWNER_ID=your_numeric_user_id
```

```sh
docker compose up -d --build
docker compose logs --tail=100 apkdl-bot
```

出现 `apkbot started` 后可在 Telegram 发送包名验证。订阅与白名单保存在 `data/app.db`，备份时保留 `data` 和 `.env`。

## 原 VPS 部署迁移

以下命令将原目录 `~/apkdl-tg-bot` 迁移至 `~/apkbot`，保留配置及数据。目标目录须不存在。迁移期间会短暂停机；原目录不同的，应替换对应路径。

在 VPS 终端执行：

```sh
(
  set -e
  cd "$HOME/apkdl-tg-bot"
  if [ -e "$HOME/apkbot" ] || [ -L "$HOME/apkbot" ]; then
    printf '%s\n' 'Target directory already exists: ~/apkbot' >&2
    exit 1
  fi
  git remote set-url origin https://github.com/HotKids/apkbot.git
  git pull --ff-only
  docker compose down
  mv "$HOME/apkdl-tg-bot" "$HOME/apkbot"
  cd "$HOME/apkbot"
  docker compose up -d --build
  docker compose ps
  docker compose logs --tail=100 apkdl-bot
)
```

Compose 服务名继续使用 `apkdl-bot`。确认启动日志及 Telegram 查询正常后，后续操作均在 `~/apkbot` 中执行。命令遇到错误会停止；若启动失败，应在该目录排查后重新启动。

## 更新与停止

在 `~/apkbot` 中更新：

```sh
git pull --ff-only
docker compose up -d --build
```

停止 bot：

```sh
docker compose down
```
