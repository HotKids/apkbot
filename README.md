# APKDL

查询 Samsung Galaxy Store 应用、直接下载 APK，并通过 Telegram 订阅版本更新。

APK 文件由三星直接传输到使用者设备。浏览器书签和 Python 脚本专用于**三星生活助手**；其他应用及更新订阅请使用 Telegram bot。

## 选择使用方式

| 需求 | 使用方式 |
| --- | --- |
| 查询 Galaxy Store 应用、下载 APK、订阅更新 | [Telegram bot](#telegram-bot) |
| 在浏览器中下载三星生活助手 | [浏览器书签](#浏览器书签) |
| 浏览器书签无法运行，或已有 Python 环境 | [Python 兜底脚本](#python-兜底脚本) |

## 浏览器书签

仅下载中国区三星生活助手（`com.samsung.android.app.sreminder`），无需 Telegram bot 或服务器。

1. 在 Chrome 中保存任意页面为书签，名称设为「三星生活助手下载」。
2. 打开 [书签网址文件](standalone/samsung-assistant/bookmarklet.txt)，点击 GitHub 文件页右上角的复制原文图标，复制完整内容。
3. 编辑刚保存的书签，把网址完整替换为复制的内容，保留开头的 `javascript:`。
4. 打开 [三星页面](https://cn-ms.galaxyappstore.com/)。页面空白属正常现象。
5. 在地址栏输入书签名称，选择对应书签；桌面浏览器也可直接点击书签栏中的书签。
6. 等待应用信息显示后，点击「下载」。

下载链接有效期约为 10 分钟，失效后请再次运行书签。请通过已保存的书签执行，不要直接把脚本粘贴到地址栏运行。

需要安装引导时，可打开 [HTML 安装页文件](standalone/samsung-assistant/install.html)，点击 GitHub 文件页右上角的下载原始文件图标，保存为 `install.html`，再用支持本地 HTML 的浏览器打开。安装页提供「复制书签网址」按钮。手机无法打开该文件时，直接按上面的步骤复制书签网址即可。

详见 [三星生活助手使用说明](standalone/samsung-assistant/README.md)。

## Python 兜底脚本

仅下载中国区三星生活助手，需要 Python 3.8 或更新版本。脚本使用 Python 标准库，无需安装其他 Python 包，也不需要 bot 配置。

打开 [download.py](standalone/samsung-assistant/download.py)，点击 GitHub 文件页右上角的下载原始文件图标，保存文件。在文件所在目录的终端中运行：

```sh
python3 download.py
```

APK 保存到当前目录。需要指定已有的可写目录时：

```sh
python3 download.py --output-dir /path/to/downloads
```

安卓需已有可运行 Python 的环境，例如 Termux。下载完成后会显示 `Downloaded:`；已有同版本文件不会被覆盖。详细步骤和中断后的处理见 [三星生活助手使用说明](standalone/samsung-assistant/README.md#python-下载)。

## Telegram bot

在已部署的 bot 私聊中使用。只有管理员和已加入白名单的用户有使用权限；输入 `/` 可选择现有命令，需要参数的命令仍须填写包名或用户 ID。

### 查询与下载

直接发送包名、Galaxy Store 详情链接，或使用 `/dl`：

```text
com.samsung.android.app.sreminder CN
/dl com.samsung.android.app.sreminder CN
https://galaxystore.samsung.com/detail/com.samsung.android.app.sreminder CN
```

不写地区时先查询 US，查询或获取下载链接失败后再尝试 CN；写明 `CN` 或 `US` 时只查询该地区。

查询消息会变成下载卡片，显示应用名、版本、文件大小、商店更新时间、包名及更新日志。更新时间缺失时显示「暂无信息」。

- **刷新**：更新同一张卡片的应用信息和下载链接。
- **下载**：直接从三星下载 APK。

链接失效时，先点击「刷新」，再点击「下载」。

### 订阅更新

```text
/sub com.samsung.android.app.sreminder CN
/list
```

订阅后，首次检查成功会通知当前可用版本，后续按版本代码判断更新，地区切换本身不视为版本更新。默认每 24 小时检查一次，bot 启动约 1 分钟后先检查一次。

订阅确认和更新通知中的「获取下载链接」会将当前消息转换为下载卡片。订阅列表显示最近一次查询的数据；列表下方的应用按钮会打开下载卡片。

| 命令 | 用途 |
| --- | --- |
| `/dl <包名或链接> [CN\|US]` | 查询应用并获取下载链接 |
| `/sub <包名或链接> [CN\|US]` | 订阅应用更新 |
| `/list` | 查看自己的订阅 |
| `/unsub <包名或链接> [CN\|US]` | 取消订阅；不写地区时取消该应用所有地区的订阅 |
| `/unsub all` | 取消自己的全部订阅 |

### 管理员命令

| 命令 | 用途 |
| --- | --- |
| `/add <用户ID> [备注]` | 将用户加入白名单 |
| `/del <用户ID>` | 将用户移出白名单 |
| `/user` | 查看白名单 |
| `/status` | 查看所有用户的订阅 |
| `/check` | 立即检查所有订阅 |
| `/help` | 查看帮助 |

## 部署 Telegram bot

仅使用浏览器或 Python 脚本时，无需部署 bot。

服务器需要 Git、[Docker Engine 和 Compose 插件](https://docs.docker.com/engine/install/)，并能访问 Telegram 和三星商店。无需域名、反向代理或开放入站端口。

### 首次部署

先通过 [BotFather](https://t.me/BotFather) 创建 bot 并获取 Token，准备管理员的 Telegram 数字用户 ID。

```sh
git clone https://github.com/HotKids/apkdl-tg-bot.git
cd apkdl-tg-bot
cp -n .env.example .env
chmod 600 .env
nano .env
```

在 `.env` 中填写以下两项，替换占位值：

```dotenv
BOT_TOKEN=your_bot_token
OWNER_ID=your_numeric_user_id
```

`OWNER_ID` 使用数字 ID，不是 `@用户名`。Token 保存在服务器的 `.env` 中。

```sh
docker compose up -d --build
docker compose logs --tail=100 -f apkdl-bot
```

看到 `APKDL started` 后，在 Telegram 私聊自己的 bot，发送包名即可。按 `Ctrl+C` 退出日志查看不会停止 bot。

### 更新、备份与停止

在项目目录更新：

```sh
git pull --ff-only
docker compose up -d --build
```

订阅和白名单保存在 `data/app.db`。备份和升级时保留 `data` 目录与 `.env`。

停止 bot：

```sh
docker compose down
```

### 可选设置

通常只需填写 `BOT_TOKEN` 和 `OWNER_ID`。需要调整时，可在 `.env` 中设置：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `CHECK_INTERVAL` | `1440` | 更新检查间隔，单位为分钟 |
| `TZ` | `Asia/Shanghai` | 时区 |
| `REQUEST_TIMEOUT` | `60` | 单次商店请求时限，单位为秒 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

修改 `.env` 后运行 `docker compose up -d` 使配置生效。

## 常见问题

**三星页面为什么是空白的？**

需要在该页面运行已保存的书签，才会显示应用信息和下载按钮。

**下载失败或出现 403 怎么办？**

bot 用户先点击「刷新」；书签用户再次运行书签；Python 用户重新运行脚本以获取新链接。新链接仍无法下载时，请稍后重试。部分应用受地区、设备或三星账户限制，无法保证所有请求都能取得可用下载。

**为什么 bot 提示无使用权限？**

请联系管理员，通过 `/add <用户ID>` 加入白名单，并在私聊中使用。

**可以修改 APK 文件名吗？**

浏览器和 bot 下载的文件名由三星提供，下载完成后可自行重命名。Python 脚本保存为 `Samsung-Assistant_<版本>_<版本代码>.apk`。

**哪些浏览器和设备可用？**

书签建议使用 Chrome。安卓真机及 Samsung Internet 的完整操作尚未验证；手机无法运行书签时可使用 Python 兜底。
