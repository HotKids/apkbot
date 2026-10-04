# apkbot

通过 Telegram 查询 Galaxy Store 应用、获取 APK 下载链接并订阅版本更新。APK 由设备直接从 Samsung 服务器下载。

## 使用

仅限管理员及白名单用户私聊使用。发送包名或 Galaxy Store 详情链接即可查询，也可输入 `/` 选择命令。

```text
com.samsung.android.app.sreminder CN
```

| 命令 | 用途 |
| --- | --- |
| `/dl <包名或链接> [CN\|US]` | 获取应用信息及下载链接 |
| `/sub <包名或链接> [CN\|US]` | 订阅版本更新 |
| `/list` | 查看个人订阅 |
| `/unsub <包名或链接> [CN\|US]` | 取消指定订阅 |
| `/unsub all` | 取消全部个人订阅 |

以下命令仅限管理员：

| 命令 | 用途 |
| --- | --- |
| `/add <用户ID> [备注]` | 将用户加入白名单 |
| `/del <用户ID>` | 将用户移出白名单 |
| `/user` | 查看白名单 |
| `/status` | 查看全部用户的订阅 |
| `/check` | 立即检查更新 |
| `/help` | 查看帮助 |

未指定地区时依次尝试 US、CN；指定地区时仅查询对应商店。取消订阅时未指定地区，将取消该应用所有地区的订阅。

默认每 24 小时检查更新，启动约 1 分钟后首次检查。订阅首次检查成功后通知当前版本，后续按版本代码判断更新，地区切换不视为更新。

下载链接有效期约为 10 分钟，失效后请点击「刷新」。订阅通知中点击「获取下载链接」，再点击「下载」。应用可能受地区、设备或账户限制。

## 部署

服务器须安装 Git、[Docker Engine 与 Compose 插件](https://docs.docker.com/engine/install/)，并能访问 Telegram 和 Samsung。无需域名或开放入站端口。

```sh
git clone https://github.com/HotKids/apkbot.git
cd apkbot
cp -n .env.example .env
chmod 600 .env
nano .env
```

在 `.env` 中填写 [BotFather](https://t.me/BotFather) 提供的 Token 和管理员的 Telegram 数字用户 ID：

```dotenv
BOT_TOKEN=your_bot_token
OWNER_ID=your_numeric_user_id
```

```sh
docker compose up -d --build
docker compose logs --tail=100 apkdl-bot
```

日志显示 `apkbot started` 后，在 Telegram 中发送包名验证。备份时须保留 `.env` 和 `data`，订阅与白名单保存在 `data/app.db`。

## VPS 目录迁移

以下命令将 `~/apkdl-tg-bot` 迁移至 `~/apkbot`，保留配置及数据。目标目录须不存在；原目录不同时，请替换对应路径。迁移期间服务暂时停止。

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

Compose 服务名仍为 `apkdl-bot`。迁移后均在 `~/apkbot` 中操作。命令出错时会停止；启动失败时，请在该目录排查并重新启动。

## 维护

更新：

```sh
git pull --ff-only
docker compose up -d --build
```

停止：

```sh
docker compose down
```

另提供 [Galaxy Store 油猴脚本](galaxy-store/README.md)，可在应用详情页获取 APK 下载链接。
