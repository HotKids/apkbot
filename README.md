# APKMirror Telegram Push Bot

从 APKMirror 自动抓取指定应用的最新 APK，按条件筛选后定时推送到 Telegram 频道或群组。

- 纯 ENV 配置驱动，无需交互式绑定
- 支持任意 APKMirror 应用页或 release 页
- Variant 结构化解析 + 过滤 + 打分排序
- SQLite 去重，避免重复推送
- Docker 单容器部署

---

## 前置条件

- VPS（已安装 Docker & Docker Compose）
- Telegram Bot Token（从 [@BotFather](https://t.me/BotFather) 获取）
- 你的 Telegram 账号 user_id（可通过 [@userinfobot](https://t.me/userinfobot) 查询）
- 目标频道或群组的 chat_id（频道格式通常为 `-100xxxxxxxxxx`）
- Bot 已加入目标频道 / 群组，并拥有**发送消息、发送文件**权限

---

## 部署步骤

### 1. 拉取代码

```bash
git clone https://github.com/HotKids/apkmirror_tg_bot.git
cd apkmirror_tg_bot
```

### 2. 配置环境变量

```bash
cp .env.example .env
nano .env
```

必填项（共 4 个）：

```env
BOT_TOKEN=123456:ABCDEF
OWNER_ID=123456789
TARGET_CHAT_ID=-1001234567890
CRON_SCHEDULE=0 9 * * *
```

| 变量 | 说明 |
|------|------|
| `BOT_TOKEN` | BotFather 给的 token |
| `OWNER_ID` | 你的 Telegram user_id（整数） |
| `TARGET_CHAT_ID` | 推送目标频道 / 群组的 chat_id |
| `CRON_SCHEDULE` | 定时表达式（标准 5 字段，如 `0 9 * * *` = 每天 09:00） |

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
Bot started. Target: (未配置) | Cron: 0 9 * * * (Asia/Shanghai)
```

---

## 初始化：设置监控地址

启动后，私信 bot 发送 `/sub` 命令配置要监控的 APKMirror 页面：

```
/sub https://www.apkmirror.com/apk/google-inc/google-play-store/
```

支持两种 URL 格式：
- **应用列表页**（自动找最新 release）：`.../apk/<developer>/<app-name>/`
- **指定 release 页**（固定抓这一版）：`.../apk/<developer>/<app-name>/<app-name>-x.x.x-release/`

配置成功后发 `/check` 立即验证。

---

## Bot 命令

所有命令仅限私信，且只有 `OWNER_ID` 可用。

| 命令 | 说明 |
|------|------|
| `/sub <url>` | 设置监控地址（持久化到本地数据库） |
| `/unsub` | 清除监控地址（取消订阅） |
| `/sublist` | 查看当前监控地址 |
| `/check` | 立即触发一次抓取和推送 |
| `/status` | 查看当前配置和最近一次运行状态 |

---

## 升级

```bash
git pull
docker compose up -d --build
```

数据库和下载文件保存在 `./data/`，升级不影响历史记录。

---

## 停止

```bash
docker compose down
```

---

## 数据目录

```
./data/
  ├── app.db        # SQLite（状态、去重记录、监控地址）
  └── downloads/    # 下载的 APK 文件
```

---

## 可选过滤参数

在 `.env` 中按需配置，不填则不过滤。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PREFER_APK` | `true` | 优先选 APK，跳过 BUNDLE |
| `ALLOW_BUNDLE` | `false` | 是否允许 BUNDLE |
| `REQUIRED_SIGNATURES` | 空（不过滤） | 逗号分隔，必须同时包含所有签名（AND）。示例：`3891,bd32` |
| `REQUIRED_ARCHITECTURES` | 空（不过滤） | 逗号分隔，至少命中一个（OR）。示例：`arm64-v8a,armeabi-v7a` |
| `REQUIRED_DPI` | 空（不过滤） | 指定 DPI。示例：`nodpi` |
| `REQUIRED_DEVICE_TYPE` | 空（不过滤） | 指定设备类型。示例：`universal` |
| `MIN_ANDROID_FLOOR` | 空（不过滤） | 最低 API 等级下限（整数）。示例：`21` |
| `MIN_ANDROID_CEILING` | 空（不过滤） | 最低 API 等级上限（整数） |
| `MATCH_KEYWORDS` | 空（不过滤） | 逗号分隔，命中任一才保留 |
| `EXCLUDE_KEYWORDS` | 空（不过滤） | 逗号分隔，命中任一则排除 |
| `DELETE_AFTER_PUSH` | `false` | 推送后立即删除 APK 文件 |
| `MAX_KEEP_FILES` | `5` | 最多保留 N 个 APK（`0` = 不限） |

---

## 常见问题

**Q：`/check` 提示"未配置监控地址"**
先发送 `/sub <url>` 配置。

**Q：`/check` 提示"没有 variant 通过过滤条件"**
检查 `REQUIRED_SIGNATURES` / `REQUIRED_ARCHITECTURES` 是否过严，或暂时清空这些参数再测试。

**Q：推送失败**
确认 bot 已加入目标频道且拥有发送文件权限，chat_id 格式正确（频道通常为 `-100` 开头）。

**Q：一直提示"没有新版本"**
当前版本已推送过。修改 `APK_URL` 到新 release 页，或等下一个版本发布。

**Q：抓取失败**
APKMirror 可能变更了页面结构，查看日志获取具体错误信息。
