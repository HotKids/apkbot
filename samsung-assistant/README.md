# 三星生活助手下载

下载中国区三星生活助手（`com.samsung.android.app.sreminder`）的最新版本。支持浏览器书签和 Python 脚本，APK 由设备直接从 Samsung 服务器下载。

建议使用 Universal Installer 并将安装来源设为三星应用商店后安装。

## 浏览器书签

1. 打开 [bookmarklet.txt](bookmarklet.txt)，点击 **Copy raw file（复制原始文件）**，复制以 `javascript:` 开头的完整单行内容。
2. 在 Chrome 中保存任意页面为书签，命名为 **三星生活助手下载**，将书签网址完整替换为复制的内容。
3. 打开 [三星页面](https://cn-ms.galaxyappstore.com/)，页面空白属正常现象。
4. 在地址栏输入书签名称，选择对应的书签建议执行，待应用信息显示后点击 **下载**。

链接有效期约为 10 分钟；失效或请求失败时，请重新运行书签。

可选中文说明页：[install.html](install.html)。点击 **Download raw file（下载原始文件）** 保存，在支持本地 HTML 的浏览器中打开，点击 **复制书签网址** 后从第 2 步继续；手机无法打开时，直接使用 `bookmarklet.txt`。

## Python 脚本

下载 [download.py](download.py) 的原始文件。需要 Python 3.8 或更高版本，仅使用标准库，无需额外依赖或 bot 配置；安卓需准备 Python 运行环境（如 Termux）及目录读写权限。

在脚本所在目录运行：

```sh
python3 download.py
```

默认保存到当前目录，也可指定已存在、可写的目录：

```sh
python3 download.py --output-dir /path/to/downloads
```

文件名为 `Samsung-Assistant_<版本>_<版本代码>.apk`，请等待显示 `Downloaded:` 后再使用。

同名文件已存在时，脚本会停止且不覆盖，普通下载失败会清理未完成文件；强制终止进程或设备关机可能留下不完整 APK，请删除后重试。

安卓真机及 Samsung Internet 的兼容性尚未验证。
