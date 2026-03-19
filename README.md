# APKMirror Telegram Bot

从 APKMirror 自动抓取指定应用的最新 APK，定时推送给订阅用户。

- 多用户 RSS 订阅模式，直接与 Bot 私聊交互
- 支持任意 APKMirror 应用页或 release 页
- 首次订阅立即推送当前最新版本
- Variant 结构化解析 + 过滤 + 打分排序
- SQLite 去重，避免重复推送
- 白名单访问控制，仅允许指定用户使用
- Docker 单容器部署

---

## 前置条件

- VPS（已安装 Docker & Docker Compose）
- Telegram Bot Token（从 [@BotFather](https://t.me/BotFather) 获取）
- 你的 Telegram 账号 user_id（可通过 [@userinfobot](https://t.me/userinfobot) 查询）

---

## 部署步骤

### 1. 拉取代码

```bash
git clone https://github.com/HotKids/apkdl_tg_bot.git
cd apkdl_tg_bot
```

### 2. 配置环境变量

```bash
cp .env.example .env
nano .env
```

必填项（共 2 个）：

```env
BOT_TOKEN=123456:ABCDEF
OWNER_ID=123456789
```

| 变量 | 说明 |
|------|------|
| `BOT_TOKEN` | BotFather 给的 token |
| `OWNER_ID` | 你的 Telegram user_id（整数） |

### 3. 启动

```bash
docker compose up -d --build
```

### 4. 查看日志

```bash
docker compose logs -f
```

看到以下输出说明启动成功：

```
Bot started. Check interval: 60m (Asia/Shanghai)
```

---

## 使用流程

启动后，私信 Bot 即可管理订阅。其他用户需先由 OWNER 通过 `/adduser` 加入白名单。

**订阅应用：**

```
/sub https://www.apkmirror.com/apk/google-inc/google-play-store/
```

Bot 会立即抓取并发送当前最新版本，后续有新版本时自动推送。

支持两种 URL 格式：
- **应用列表页**（自动找最新 release）：`.../apk/<developer>/<app-name>/`
- **指定 release 页**（固定抓这一版）：`.../apk/<developer>/<app-name>/<app-name>-x.x.x-release/`

---

## Bot 命令

### 订阅管理（OWNER + 白名单用户，仅私聊）

| 命令 | 说明 |
|------|------|
| `/sub <url>` | 订阅应用，立即推送当前最新版本 |
| `/unsub [url\|all]` | 取消指定订阅；`all` 取消全部；无参数显示帮助 |
| `/sublist` | 查看当前所有订阅 |

### 管理命令（仅 OWNER，私聊）

| 命令 | 说明 |
|------|------|
| `/check` | 立即触发全量检查，有新版则推送 |
| `/status` | 查看 Bot 状态、订阅数及各应用最新版本 |
| `/adduser <user_id>` | 将用户加入白名单 |
| `/deluser <user_id>` | 从白名单移除用户 |
| `/listusers` | 查看白名单列表 |

---

## 升级

```bash
git pull
docker compose up -d --build
```

数据库保存在 `./data/`，升级不影响历史记录。

---

## 停止

```bash
docker compose down
```

---

## 数据目录

```
./data/
  └── app.db        # SQLite（订阅、白名单、版本状态）
```

APK 文件推送成功后自动删除。

---

## 常见问题

**Q：`/check` 提示"当前无订阅"**
先由任一白名单用户发送 `/sub <url>` 添加订阅。

**Q：`/check` 提示"没有 variant 通过过滤条件"**
APKMirror 页面可能无法解析到 variant，查看日志获取具体错误信息。

**Q：其他用户发消息 Bot 无响应**
正常现象——非白名单用户会被静默丢弃。请 OWNER 使用 `/adduser <user_id>` 授权。

**Q：推送失败**
查看日志获取具体错误信息。常见原因：网络问题、APKMirror 页面结构变更、文件过大超出 Telegram 限制。

**Q：一直提示"无更新"**
当前版本已推送过。等待下一个版本发布，或重新 `/sub` 同一 URL（先 `/unsub` 再 `/sub` 会重新发送当前版本）。

**Q：抓取失败**
APKMirror 可能变更了页面结构或触发了反爬虫，查看日志获取具体错误信息。
