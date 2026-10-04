# Galaxy Store 油猴脚本

在 Galaxy Store 应用详情页显示「下载 APK」按钮。APK 由设备直接从 Samsung 服务器下载。

## 使用

1. 在浏览器中安装 [Tampermonkey](https://www.tampermonkey.net/)。安卓 Edge 可在「扩展」中安装；也支持 [Firefox 扩展](https://addons.mozilla.org/zh-CN/firefox/addon/tampermonkey/)。
2. 点击 [安装脚本](https://raw.githubusercontent.com/HotKids/apkbot/main/galaxy-store/galaxy-store.user.js)，确认安装。
3. 打开应用详情页，点击「下载 APK」，按浏览器提示确认下载。

例如：[三星生活助手](https://galaxystore.samsung.com/detail/com.samsung.android.app.sreminder)。安装此应用时，建议使用 Universal Installer 并将安装来源设为三星应用商店后安装，以避免出现权限无法开启的情况。

## 地区与下载

默认依次尝试美国区、中国区，不受网页当前目录地区影响。网址携带 `cntyCd=CHN` 或 `cntyCd=USA` 时，仅查询指定地区；也接受 `CN`、`US`。应用名、版本及国旗以实际查询结果为准。

网页提示「不支持」时，仍可使用脚本的下载按钮。脚本拦截网页链接和弹窗中的商店唤起；服务器在脚本运行前发出的商店跳转无法由油猴拦截。安卓如直接跳转到商店，请为此网站启用浏览器的桌面网站模式。

下载链接有效期约为 10 分钟，失效后请刷新页面重新获取。应用可能受地区、设备或账户限制。
