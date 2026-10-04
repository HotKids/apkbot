# Galaxy Store 油猴脚本

在 Galaxy Store 应用详情网页中提供「下载」按钮，适用于美国区和中国区的应用。APK 由设备直接从 Samsung 服务器下载。

## 安装与使用

1. 安装 [Tampermonkey](https://www.tampermonkey.net/)。Android 可在 Edge 的「扩展」中安装；电脑可使用支持 Tampermonkey 的浏览器。
2. 点击 [安装脚本](https://raw.githubusercontent.com/HotKids/apkbot/main/galaxy-store/galaxy-store.user.js)，在 Tampermonkey 界面确认安装。
3. 打开 Galaxy Store 应用详情网页，点击「下载」，按浏览器提示确认下载。若未自动开始，再点击一次「下载」。

下载链接有效期约为 10 分钟，失效后请点击「刷新」，再点击「下载」。安装或更新脚本后，请重新加载已打开的应用详情页。

## 地区选择

默认先尝试美国区，失败后再尝试中国区。可在详情链接中指定地区，仅查询对应商店：

| 地区 | 链接参数 |
| --- | --- |
| 中国区 | `cntyCd=CHN` 或 `cntyCd=CN` |
| 美国区 | `cntyCd=USA` 或 `cntyCd=US` |

应用名、版本及国旗以查询结果为准。能否获取下载链接取决于商店的地区、设备及账户限制。

网页提示「不支持」时，可尝试页面中的「下载」按钮。若链接直接打开应用商店，可启用浏览器的桌面网站模式后重新打开链接。

## 使用示例

打开 [三星生活助手](https://galaxystore.samsung.com/detail/com.samsung.android.app.sreminder) 的网页详情页，点击「下载」。

安装此应用时，建议使用 Universal Installer 并将安装来源设为三星应用商店后安装，以避免出现权限无法开启的情况。
