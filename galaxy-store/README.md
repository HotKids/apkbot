# Galaxy Store 油猴脚本

通过 Galaxy Store 网页下载应用 APK，支持美国区和中国区。APK 由设备直接从 Samsung 服务器下载。

## 使用

1. 在安卓 Edge 的「扩展」中安装 [Tampermonkey](https://www.tampermonkey.net/)。
2. 点击 [安装脚本](https://raw.githubusercontent.com/HotKids/apkbot/main/galaxy-store/galaxy-store.user.js)，在油猴界面确认安装。
3. 打开 Galaxy Store 网页的应用详情页，点击「下载」并确认下载。若未自动开始下载，再点击一次「下载」。

电脑可使用已安装 Tampermonkey 的浏览器，安卓也可使用 [Firefox](https://addons.mozilla.org/zh-CN/firefox/addon/tampermonkey/)。

示例：[三星生活助手](https://galaxystore.samsung.com/detail/com.samsung.android.app.sreminder)。

安装此应用时，建议使用 Universal Installer 并将安装来源设为三星应用商店后安装，以避免出现权限无法开启的情况。

## 地区与下载

未指定地区时依次尝试美国区、中国区。网址携带 `cntyCd=CHN` 或 `cntyCd=USA` 时，仅查询对应地区；也接受 `CN`、`US`。应用名、版本及国旗以实际查询结果为准。

网页提示「不支持」时，仍可使用下载按钮。安卓若打开应用商店，可启用此网站的桌面网站模式后重试。

下载链接有效期约为 10 分钟，失效后请点击「刷新」。应用可能受地区、设备或账户限制。
