# 三星生活助手下载

提供中国区三星生活助手（`com.samsung.android.app.sreminder`）的最新版本下载，支持浏览器书签及 Python 脚本。APK 由设备直接从 Samsung 服务器下载。

建议使用 Universal Installer 并将安装来源设为三星应用商店后安装，以避免出现权限无法开启的情况。

## 浏览器书签

**请使用安卓 Chrome。**

打开 [三星页面](https://cn-ms.galaxyappstore.com/)，在地址栏输入「三星生活助手下载」，点击对应书签即可自动下载。页面空白属正常现象。

首次设置：将任意页面保存为「三星生活助手下载」书签，用 [书签代码](bookmarklet.txt) 的完整内容替换书签网址。

也可下载 [HTML 说明页](install.html) 的原始文件，在支持本地 HTML 的浏览器中打开，点击「复制书签网址」获取代码。

需能访问 GitHub 和三星服务器。链接有效期约为 10 分钟；下载失败时请重新运行书签。浏览器可能要求确认下载。

## Python 脚本

下载 [download.py](download.py) 的原始文件。需要 Python 3.8 或更高版本，无需额外依赖或 bot 配置。安卓须具备 Python 运行环境（如 Termux）及可写目录。

在脚本所在目录运行：

```sh
python3 download.py
```

默认保存至当前目录，也可指定已存在的可写目录：

```sh
python3 download.py --output-dir /path/to/downloads
```

显示 `Downloaded:` 即表示下载完成，文件名为 `Samsung-Assistant_<版本>_<版本代码>.apk`。

已有同名文件时，脚本停止且不覆盖。普通下载失败会清理未完成文件；强制终止进程或设备关机可能留下不完整 APK，请删除后重试。
