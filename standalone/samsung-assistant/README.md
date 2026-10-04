# Samsung Assistant direct download

These standalone clients request the current CN release of Samsung Assistant
(`com.samsung.android.app.sreminder`) and a fresh download grant from Samsung.
The APK goes directly from Samsung to the browser or Python client. They do not
use Telegram, bot configuration, a proxy, or an APK parsing service.

## Android browser

Open `install.html` to copy the bookmark URL and follow the steps on that page.
Alternatively, copy the complete single line from `bookmarklet.txt`.

1. In Chrome, save any page as a bookmark named **三星生活助手下载**.
2. Edit that bookmark and replace its entire address with the copied
   `javascript:...` URL. Do not paste it into the address bar to run it.
3. Open <https://cn-ms.galaxyappstore.com/>. A blank page is normal.
4. Type **三星生活助手下载** into the address bar and select its bookmark suggestion.
5. Wait for the app information, then tap **下载**. If the link expires,
   run the bookmark again to request a new one.

The bookmark and installation page display Simplified Chinese.

The bookmark runs on Samsung's origin so that its requests are same-origin.
Running an ordinary local HTML page cannot read these responses across origins.
No browser security setting needs to be changed.

[Chrome's Android help](https://support.google.com/chrome/answer/188842?co=GENIE.Platform%3DAndroid&hl=en)
documents bookmark creation and editing. [Chromium's security FAQ](https://github.com/chromium/chromium/blob/main/docs/security/faq.md)
documents bookmarklet execution and the protection applied to pasted scripts.
Samsung Internet bookmarklet execution and the phone's actual bookmark-editing
flow remain **unverified**.

## Python fallback

Copy `download.py` to a device with Python 3.8 or newer. It uses only the Python
standard library; no `pip install`, bot files, or environment configuration is
required. In an Android Python runner such as Termux, run:

```sh
python3 download.py
```

To choose an existing writable directory:

```sh
python3 download.py --output-dir /path/to/downloads
```

The script queries the current release, gets a new grant and immediately
downloads `Samsung-Assistant_<version>_<versionCode>.apk`. It exclusively creates
the output and refuses to overwrite an existing file. Wait for `Downloaded:`
before using the APK. It verifies the received byte count against the grant and
removes its incomplete output if an ordinary failure occurs. If the process is
forcibly killed or the device shuts down, an incomplete APK may remain; delete
that file before retrying. The transfer has a finite deadline and
accepts only HTTPS Samsung download addresses and redirects. Signed URLs and
response bodies are not printed.

## Validation and limits

On 2026-10-04, desktop Chrome with an Android user agent queried version
**9.4.02.7**, obtained a fresh grant and saved the complete **106,232,505-byte**
APK through the download button. This is desktop browser evidence; execution on
a physical Android phone remains **unverified**. It does not establish that an
older 403 issue is resolved. Browser downloads rely on the browser's download
manager; the bookmark does not inspect the downloaded APK.

The Python fallback also completed a live desktop download of the same release
and byte count on 2026-10-04. The repository's 286 offline Python tests, including
37 standalone-client cases, passed. Fourteen offline browser checks and Ruff
also passed. The tests exercise the protocol, error and file-handling paths with
synthetic responses. A live Python download on Android remains **unverified**.

For maintainers, regenerate the copyable artifacts after changing `browser.js`:

```sh
python3 standalone/samsung-assistant/build_bookmarklet.py
```

The offline browser check requires Playwright and its Chromium runtime. Set
`CHROME_BINARY` to use an already installed Chrome instead:

```sh
node standalone/samsung-assistant/test_browser.cjs
pytest tests/test_assistant_download.py
```
