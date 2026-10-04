"""Build the copyable bookmarklet and its offline installation page."""

from html import escape
from pathlib import Path
from urllib.parse import quote


directory = Path(__file__).resolve().parent
bookmarklet = "javascript:" + quote((directory / "browser.js").read_text(), safe="~()*!.'-")
(directory / "bookmarklet.txt").write_text(bookmarklet + "\n")
(directory / "install.html").write_text("""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>三星生活助手下载</title>
<style>body{font:16px/1.65 system-ui;max-width:640px;margin:24px auto;padding:0 20px;color:#17202a}textarea{box-sizing:border-box;width:100%;height:150px;font:12px/1.4 monospace}button,.open{display:inline-block;background:#1769e0;color:white;border:0;border-radius:8px;padding:12px 18px;font:inherit;text-decoration:none}li{margin:12px 0}small{color:#52606d}</style>
<h1>三星生活助手下载</h1>
<p>在安卓 Chrome 中运行书签，即可直接向三星获取最新 APK 下载链接。</p>
<ol>
<li>将任意页面保存为书签，名称设为<strong>三星生活助手下载</strong>。</li>
<li>复制下方书签网址，编辑已保存的书签，将网址完整替换为复制的内容。</li>
<li>打开三星页面，页面空白属正常现象。在地址栏输入<strong>三星生活助手下载</strong>，选择对应的书签建议。</li>
<li>等待应用信息显示后，点击<strong>下载</strong>。</li>
</ol>
<button id="copy" type="button">复制书签网址</button>
<a class="open" href="https://cn-ms.galaxyappstore.com/" target="_blank" rel="noopener">打开三星页面</a>
<p id="status" role="status"></p>
<textarea id="bookmark" readonly aria-label="书签网址">""" + escape(bookmarklet) + """</textarea>
<p><small>应用：com.samsung.android.app.sreminder，使用三星中国区商店。链接失效后请再次运行书签。桌面 Chrome 下载已验证，安卓真机运行尚未验证。</small></p>
<p><a href="https://support.google.com/chrome/answer/188842?co=GENIE.Platform%3DAndroid&amp;hl=zh-CN">Chrome 书签编辑说明</a></p>
<script>
document.getElementById('copy').onclick = async () => {
  const field = document.getElementById('bookmark');
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(field.value);
    else { field.select(); field.setSelectionRange(0, field.value.length); if (!document.execCommand('copy')) throw new Error(); }
    document.getElementById('status').textContent = '已复制，请粘贴到书签的网址栏。';
  } catch { field.focus(); field.select(); field.setSelectionRange(0, field.value.length); document.getElementById('status').textContent = '请选中完整网址并手动复制。'; }
};
</script></html>
""")
