# APKMirror Google Play Store → Telegram Channel Bot

这是一个可直接部署到 VPS 的 Docker 版本，功能如下：

- 每天定时检查 APKMirror 上的 Google Play Store 最新版本
- 按条件筛选 `signature=3891` + `variant=bd32`
- 默认优先选择 `APK`，不选 `BUNDLE`
- 下载后自动发到已绑定的 Telegram 频道
- 首次私聊 bot 的用户自动成为 owner
- owner 把频道中的任意一条消息转发给 bot，即可完成频道绑定
- 用 SQLite 保存已推送版本，避免重复发送

## 重要说明

这个版本是“能直接部署”的版本，但 APKMirror 页面结构或风控策略将来可能变化，所以抓取逻辑不保证永久稳定。

## 部署步骤

### 1. 上传到 VPS

把整个目录上传到 VPS，例如：

```bash
scp -r apkmirror_tg_bot root@your-vps:/opt/
cd /opt/apkmirror_tg_bot
```

### 2. 准备环境变量

```bash
cp .env.example .env
nano .env
```

至少改掉：

```env
BOT_TOKEN=你的TelegramBotToken
```

### 3. 启动

```bash
docker compose up -d --build
```

### 4. 查看日志

```bash
docker compose logs -f
```

## 初始化和绑定频道

### 第一步：私聊 bot

给 bot 发送：

```text
/start
```

如果当前还没有 owner，bot 会把第一个私聊它的人自动设为 owner。

### 第二步：把 bot 加进频道

把 bot 加入目标频道，并给它管理员权限。

至少要有：

- 发送消息
- 发送媒体
- 发送文件

### 第三步：转发频道消息给 bot

在频道里随便发一条消息，再把这条消息转发给 bot 私聊。

bot 识别成功后，就会保存这个频道。

## 命令

在私聊里由 owner 使用：

```text
/help
/channels
/unbind -100xxxxxxxxxx
/checknow
/status
```

## 升级

代码更新后执行：

```bash
docker compose up -d --build
```

## 停止

```bash
docker compose down
```

## 数据目录

数据保存在宿主机：

```text
./data/
  ├── app.db
  └── downloads/
```

## 常见问题

### 1. bot 绑定不了频道

通常是这几个原因：

- bot 没有加入频道
- bot 不是频道管理员
- 转发的不是频道消息，而是普通群消息

### 2. 能绑定但发不出去文件

一般是频道管理员权限不够。

### 3. 一直提示没有新版本

说明当前筛选条件下没有新包，或者之前已经推送过了。

### 4. 抓取失败

APKMirror 可能变更了页面结构或启用了更强的风控。这个版本已经尽量做宽松匹配，但不保证永久可用。
