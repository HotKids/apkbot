# apkbot

Galaxy Store 应用下载与更新订阅工具，提供 Telegram bot 和通用油猴脚本。支持美国区和中国区，APK 由设备直接从 Samsung 服务器下载。

## Telegram bot

查询应用信息、获取下载链接并订阅版本更新。仅限管理员及白名单用户私聊使用。

发送包名或 Galaxy Store 应用详情链接即可查询，也可输入 `/` 选择命令。例如：

```text
com.samsung.android.app.sreminder CN
```

| 命令 | 用途 |
| --- | --- |
| `/dl <包名或链接> [CN\|US]` | 获取应用信息及下载链接 |
| `/sub <包名或链接> [CN\|US]` | 订阅版本更新 |
| `/list` | 查看我的订阅 |
| `/unsub <包名或链接> [CN\|US]` | 取消指定订阅 |
| `/unsub all` | 取消我的全部订阅 |

以下命令仅限管理员：

| 命令 | 用途 |
| --- | --- |
| `/add <用户ID> [备注]` | 将用户加入白名单 |
| `/del <用户ID>` | 将用户移出白名单 |
| `/user` | 查看白名单 |
| `/status` | 查看全部用户的订阅 |
| `/check` | 立即检查更新 |
| `/help` | 查看帮助 |

未指定地区时先尝试 US，失败后再尝试 CN；指定 `CN` 或 `US` 时仅查询对应商店。取消订阅时省略地区，将取消该应用所有地区的订阅。

默认每 24 小时检查更新，启动约 1 分钟后首次检查。订阅首次检查成功时通知当前版本，之后仅在版本代码增加时通知。订阅列表显示最近一次查询的结果。

查询结果中点击「下载」即可下载 APK；订阅确认或更新通知中先点击「获取下载链接」，再点击「下载」。链接有效期约为 10 分钟，失效后请点击「刷新」。

## 油猴脚本

在 Galaxy Store 应用详情网页中下载 APK，可独立使用。

安装 [Tampermonkey](https://www.tampermonkey.net/) 后，点击 [安装脚本](https://raw.githubusercontent.com/HotKids/apkbot/main/galaxy-store/galaxy-store.user.js)。随后打开应用详情网页，点击「获取」以查询应用信息并尝试下载；若未开始，请点击「下载」。Android 可在 Edge 的「扩展」中安装 Tampermonkey。

安装步骤、地区选择及使用示例见 [油猴脚本说明](galaxy-store/README.md)。

## 部署 bot

服务器须安装 Git、[Docker Engine 与 Compose 插件](https://docs.docker.com/engine/install/)，并能访问 Telegram 和 Samsung。

```sh
git clone https://github.com/HotKids/apkbot.git
cd apkbot
cp -n .env.example .env
chmod 600 .env
nano .env
```

在 `.env` 中填写 [BotFather](https://t.me/BotFather) 提供的 bot Token 和管理员的 Telegram 数字用户 ID：

```dotenv
BOT_TOKEN=your_bot_token
OWNER_ID=your_numeric_user_id
```

```sh
docker compose up -d --build
docker compose logs --tail=100 apkdl-bot
```

日志显示 `apkbot started` 后，在 Telegram 中发送包名验证。订阅与白名单保存在 `data/app.db`；备份时保留 `.env` 和 `data` 目录。

更新：

```sh
git pull --ff-only
docker compose up -d --build
```
