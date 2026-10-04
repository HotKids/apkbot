"""Build the copyable bookmarklet and its offline installation page."""

from html import escape
from pathlib import Path
from urllib.parse import quote


directory = Path(__file__).resolve().parent
bookmarklet = "javascript:" + quote((directory / "browser.js").read_text(), safe="~()*!.'-")
(directory / "bookmarklet.txt").write_text(bookmarklet + "\n")
(directory / "install.html").write_text("""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Samsung Assistant download</title>
<style>body{font:16px/1.65 system-ui;max-width:640px;margin:24px auto;padding:0 20px;color:#17202a}textarea{box-sizing:border-box;width:100%;height:150px;font:12px/1.4 monospace}button,.open{display:inline-block;background:#1769e0;color:white;border:0;border-radius:8px;padding:12px 18px;font:inherit;text-decoration:none}li{margin:12px 0}small{color:#52606d}</style>
<h1>Samsung Assistant download</h1>
<p>Run a bookmark in Chrome on Android to request a fresh APK link directly from Samsung.</p>
<ol>
<li>Save any page as a bookmark. Name it <strong>Assistant APK</strong>.</li>
<li>Copy the bookmark URL below. Edit the saved bookmark and replace its entire address with this URL.</li>
<li>Open the Samsung page. A blank page is normal. Type <strong>Assistant APK</strong> in the address bar, then select the matching bookmark suggestion.</li>
<li>Wait for the app information, then tap <strong>Download APK</strong>.</li>
</ol>
<button id="copy" type="button">Copy bookmark URL</button>
<a class="open" href="https://cn-ms.galaxyappstore.com/" target="_blank" rel="noopener">Open Samsung page</a>
<p id="status" role="status"></p>
<textarea id="bookmark" readonly aria-label="Bookmark URL">""" + escape(bookmarklet) + """</textarea>
<p><small>For com.samsung.android.app.sreminder, Samsung CN. If the link expires, run the bookmark again. Android phone execution has not yet been tested; desktop Chrome download has been verified.</small></p>
<p><a href="https://support.google.com/chrome/answer/188842?co=GENIE.Platform%3DAndroid&amp;hl=en">Chrome bookmark editing instructions</a></p>
<script>
document.getElementById('copy').onclick = async () => {
  const field = document.getElementById('bookmark');
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(field.value);
    else { field.select(); field.setSelectionRange(0, field.value.length); if (!document.execCommand('copy')) throw new Error(); }
    document.getElementById('status').textContent = 'Copied. Paste into the saved bookmark address.';
  } catch { field.focus(); field.select(); field.setSelectionRange(0, field.value.length); document.getElementById('status').textContent = 'Select the full URL and copy it manually.'; }
};
</script></html>
""")
