# Cloud rewrite and deployment review

Date: 2026-10-02. Results apply to the Galaxy Store implementation and its one-click redirect workflow.

## Current delivery mode: one click gets fresh authorization and redirects

Cards contain a single button labeled `下载`, pointing to a stable HTTPS redirect entry. `/dl`, bare package/link input and old `gdl:` callbacks query metadata/notes and send that entry; they do not request CN download authorization. Opening the button validates its signature and current whitelist access, retrieves current metadata and fresh authorization, and returns an empty HTTP 302 response with Samsung's URL in `Location`. The browser downloads directly from Samsung. Every click renews authorization; there is no refresh button or expired URL cached in the Telegram card. Displayed regions include `🇨🇳 CN` and `🇺🇸 US`; automatic preference is `🌐 AUTO`.

Signatures bind the user, durable application key and region preference, using an HMAC domain-separated with the Bot Token. App records survive restarts; signed paths and Samsung URLs are not stored in SQLite. Whitelist revocation and token rotation invalidate access. The entry is a bearer link, not a separate browser login. Requests cannot supply arbitrary destinations, and the Samsung target must pass the existing URL validator. Responses prohibit caching and referrer forwarding. HTTP handling is bounded to 8 concurrent connections; HEAD and health checks never authorize downloads.

Bot and HTTP handlers never fetch APK bytes, including CDN HEAD requests, or upload attachments. Samsung URLs appear only in the 302 response to the browser. Telegram upload limits do not apply; local Bot API remains optional. A public HTTPS origin in `PUBLIC_DOWNLOAD_BASE_URL` is required. Compose publishes only host-loopback port 8080 for an HTTPS reverse proxy. Public DNS/TLS and phone access were not tested.

The result card describes metadata at query time; its button always chooses the latest version at click time. Neither a card nor a successful redirect proves that the phone downloaded the file. No APK manifest/hash/signature verification is possible without fetching bytes. Existing complete-download diagnostics remain available only through explicit CLI `--download-to`.

A fresh live `--notes --authorize` probe for `com.lucky.luckyclient CN` succeeded: version 5.6.1/code 5601, 127784966 bytes authorized on `cdnet-dn.galaxyappstore.com`, matching CN notes. The probe did not fetch APK bytes or print the signed URL. Phone access from another network, browser behavior and live Telegram button delivery remain deployment smoke checks.

## Checks executed

- Python 3.12.14. Both the working virtual environment and an independently created clean environment passed the current suite: **154 passed**, no skipped tests. Dependency checks and `git diff --check` passed.
- Tests use synthetic settings, temporary SQLite/download directories, and blocked HTTP/Telegram transport. They exercise region/error classification, XML ambiguity/DTD rejection, ODS request separation, byte bounds, redirects, diagnostic binary XML parsing and version binding, legacy data preservation, per-subscriber notification state, private-chat permissions, durable callbacks, and rich-message transport/fallback.
- Redirect tests start a real temporary loopback HTTP server and client with mocked external source I/O. Repeated clicks on the same URL return fresh grants/versions and empty 302 bodies without CDN requests. Tests cover tampered signatures, user/app binding, revoked whitelist access, token rotation, source failures, untrusted redirect targets, cache/referrer headers, no URL leakage, health/HEAD handling, concurrency saturation and socket shutdown. Bot tests verify single-button cards without authorization/upload, recipient-bound URLs, old callback compatibility, and per-subscriber notification state. External HTTP and Telegram calls remain blocked in the test suite.
- POST-body transport and HTML fallback still preserve buttons and suppress previews. CLI authorization remains mutually exclusive with complete download.
- Startup tests intercept polling and verify API/webhook preflight, explicit callback updates, and SIGTERM handling. No production polling or Telegram message was sent.
- Compose configuration was rendered with the blank `.env.example`, without production credentials. Download service container binding, loopback-only host port 8080, and the optional internal Bot API were verified. Blank public origin remains a runtime configuration requirement, not a working public address.
- `deploy/Caddyfile.example` passed Caddy 2.10.2 configuration validation. The official release binary was verified against its published SHA512 checksum before execution. The example omits access logging and filters request URIs/referrers and Location fields from proxy logs. Validation did not start a public listener, obtain a certificate or deploy the application.
- No deployment or APK installation was performed.

## Issues fixed during deployment review

- The custom TeleBot sender transmits API fields via POST bodies, rejects file uploads, and avoids redirects or automatic retries. TeleBot/urllib3 DEBUG output is suppressed to protect signed URLs, including those echoed in responses.
- The optional Bot API container retains `TELEGRAM_LOCAL=1` and publishes no host port. Its 2000 MB upload support is no longer needed for this application flow.
- Startup verifies the public HTTPS origin, `getMe` and `getWebhookInfo`, then binds the redirect listener before announcing readiness. It rejects existing webhooks, explicitly requests messages and callback queries, and closes polling and the HTTP listener on shutdown.
- Generated APK filenames are bounded so a valid long Android package name cannot exceed filesystem `NAME_MAX` after adding version and unique suffixes.

## Live Samsung evidence

CN `getDownloadInfo` (2298) returned:

| Package | version | versionCode | productID | realContentsSize |
| --- | --- | --- | --- | --- |
| com.lucky.luckyclient | 5.6.1 | 5601 | 000004758723 | 127784966 |
| com.xingin.xhs | 9.49.0 | 9490803 | 000004560728 | 182639070 |
| me.ele | 12.9.68 | 1602 | 000001073702 | 85193594 |
| com.larus.nova | 15.2.0 | 15020040 | 000007108509 | 408191411 |

All four reported `needToLogin=0`, `installableYN=Y`. The successful ODS syntax uses `version` and an `errorCode="0"` attribute on `errorString`.

During the earlier deployment review, fresh `downloadForRestore` (2316) authorization and **complete diagnostic downloads succeeded for the first three applications**. Earlier CDN proxy failures no longer describe the observed runtime. Each download matched its authorized byte count and its binary manifest's package, versionName and VersionCode. Full ZIP CRC checks passed independently. These results do not mean the current bot still downloads files.

| Package | SHA256 | Verified APK signature schemes |
| --- | --- | --- |
| com.lucky.luckyclient | `4c50ca082839ccfd6ee350acee832def8ca3898f50b38ed2a764e1e0ccf0d5e5` | v2 |
| com.xingin.xhs | `23b80d136235af38f4ef59448339d24eaf4a23b1b1fe50acfb65a307f6afb299` | v1, v2, v3 |
| me.ele | `987971fbcf158b26666561561f12ab4d22a7e6a203bed464bfc372ebd471524a` | v1, v2 |

Signature verification used Google's `com.android.tools.build:apksig:8.7.3`, fetched from Google's HTTPS Maven repository and checked against its published checksum before use. `ApkVerifier.verify().isVerified()` returned true for all three. These manual checks do not establish an independently known publisher identity. Only the explicit CLI downloader validates manifest/length and computes SHA256; it does not automatically perform full ZIP CRC or signature verification. The link-only bot performs none of these file checks.

The APKs are retained in the ignored `data/verification/deployment-review/` directory. Signed download URLs were not saved in the report, database or application logs.

Nova's metadata succeeded, but download authorization returned `ServiceError`. The cause remains unknown. This is not absence and must not trigger region fallback.

US stub metadata was also verified for `com.sec.android.app.samsungapps`: version 4.6.11.4, code 461104100, product 000009233937, size 78307995. This does not establish US ODS support or a complete US APK download. The previous CN QQ Music stub control succeeded with product 000003809641, version 20.9.0.8, code 7458, size 201245733.

## CN website notes

The inspected official frontend obtains detail JSON from `/api/detail/<package>`, reads `DetailMain.contentNewDescription`, and maps CN to `cntyCd=CHN`. The implementation independently checks the response's package, country and literal versionName; leading zeros are significant. It does not infer matching VersionCode from website data.

The validated redirect to `apps.galaxyappstore.com` now succeeds. Matching CN notes were retrieved for all three downloaded APKs and for the US Galaxy Store metadata control. The latter remains a US release with CN notes; no regional identity was changed. Missing or mismatched notes remain unavailable without blocking link generation.

## Telegram evidence and limits

Authoritative server source inspected: [tdlib/telegram-bot-api Client.cpp at e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1](https://github.com/tdlib/telegram-bot-api/blob/e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1/telegram-bot-api/Client.cpp).

It registers `sendRichMessage` and accepts a JSON `rich_message` containing heading/paragraph/details/footer blocks. Heading requires `size`; details uses `summary`, `blocks`, and `is_open`. Progress remains an ordinary HTML edit; no rich-edit method is assumed. The implementation uses the existing synchronous TeleBot token/API URL, and only explicit rejection permits an HTML retry. Live server support, client rendering and callback interaction still require deployment validation.

Presentation reference: [HotKids/kdbot richmsg.py at 67fbd269fb9d1ba968d15a36a8d2f0f644770c68](https://github.com/HotKids/kdbot/blob/67fbd269fb9d1ba968d15a36a8d2f0f644770c68/richmsg.py).

For the earlier Telegram URL-fetch question, Bot API's `get_input_file` passes URL input as `inputFileRemote`; [TDLib DocumentsManager at 42e6a5259551178d1dab54a22ad96d14bd906e20](https://github.com/tdlib/td/blob/42e6a5259551178d1dab54a22ad96d14bd906e20/td/telegram/DocumentsManager.cpp#L689) represents it as `inputMediaDocumentExternal`. Local 2000 MB upload support does not establish a matching remote URL-fetch limit. That mechanism is not used here: a normal URL button opens the signed redirect entry, which automatically sends the browser to a newly authorized Samsung URL.

## Container and remaining validation

`docker build` reached Docker Hub but the `python:3.12-slim` manifest request returned HTTP 429. A build-context-only attempt to use Google's Docker Hub mirror was denied by network policy; the repository's Dockerfile was not changed to use that mirror. **No image was built or deployed.** A clean native Python installation and suite passed independently.

The cloud environment draft preserves the package-manager preset and these hosts:

- galaxystore.samsung.com
- vas.samsungapps.com
- cn-ms.galaxyappstore.com
- cdnet-dn.galaxyappstore.com
- apps.galaxyappstore.com

Live success establishes connectivity for these observed requests, not publication of the saved cloud configuration. Runtime restoration, Docker image startup and Telegram delivery remain unverified. Git publication does not deploy the service or validate public HTTPS connectivity.

Before ordinary use, configure the real HTTPS origin, DNS and reverse proxy; build on the target server and exercise the README's private-chat smoke checks. Click the same `下载` button twice and verify automatic redirects and phone downloads; check subscription cards too. Public Bot API is sufficient; no attachment or local Bot API is required. If changing API servers for an existing bot, follow the official `logOut` procedure and stop the old instance.

US ODS, broad store coverage, login-required/paid applications, split APKs and OBB/delta delivery remain outside the verified scope.
