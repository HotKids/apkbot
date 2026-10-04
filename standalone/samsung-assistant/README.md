# 三星生活助手下载

通过浏览器书签或 Python 脚本下载三星生活助手（包名：`com.samsung.android.app.sreminder`）。两种方式均固定使用中国区商店，获取最新版本和新的下载链接，APK 由设备直接从 Samsung 下载。

## 安卓浏览器书签

1. 在 GitHub 打开 [bookmarklet.txt](bookmarklet.txt)，点击 **Copy raw file（复制原始文件）**，复制以 `javascript:` 开头的完整单行内容。也可打开原始文件内容后全选复制。
2. 在 Chrome 中将任意页面保存为书签，名称设为 **三星生活助手下载**。编辑该书签，将网址完整替换为刚复制的内容。
3. 打开 [三星页面](https://cn-ms.galaxyappstore.com/)。页面空白属正常现象。
4. 在地址栏输入 **三星生活助手下载**，选择对应的书签建议执行。等待应用信息显示后，点击 **下载**。

代码应保存到书签的网址字段，通过书签建议执行。下载链接有效期约为 10 分钟，失效或请求失败时，再次运行书签以获取新链接。

也可使用中文说明页：在 GitHub 打开 [install.html](install.html)，点击 **Download raw file（下载原始文件）**，保存为 `install.html`。在支持打开本地 HTML 的浏览器中打开文件，点击 **复制书签网址**，再从第 2 步继续。若手机无法打开这个文件，使用上面的 `bookmarklet.txt` 方式即可。

## Python 下载

在 GitHub 打开 [download.py](download.py)，点击 **Download raw file（下载原始文件）** 保存，使用 Python 3.8 或更新版本运行。脚本只使用标准库，无需安装额外 Python 包，也无需 Telegram 或 bot 配置。安卓需先准备 Python 运行环境（例如 Termux），并确保能够读写下载目录。

在终端进入 `download.py` 所在目录，运行：

```sh
python3 download.py
```

默认保存到当前目录。指定其他已存在、可写的目录：

```sh
python3 download.py --output-dir /path/to/downloads
```

文件名为 `Samsung-Assistant_<版本>_<版本代码>.apk`。等待终端显示 `Downloaded:` 后再使用文件。

- 同名文件已存在时，脚本会停止，不会覆盖。需要重新下载时，请先自行删除该文件。
- 普通下载失败会清理本次未完成的文件，可重新运行。
- 强制关闭进程或设备关机可能留下不完整 APK，请删除该文件后重试。

安卓真机及 Samsung Internet 的运行情况尚未验证。
