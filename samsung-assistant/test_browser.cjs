// Offline browser checks. Every request is intercepted; no Samsung calls occur.
const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const source = fs.readFileSync(path.join(__dirname, "browser.js"), "utf8");
const bookmarklet = fs.readFileSync(path.join(__dirname, "bookmarklet.txt"), "utf8").trim();
assert.ok(bookmarklet.startsWith("javascript:"));
assert.ok(bookmarklet.length <= 2048, "The native bookmark editor must retain the entire URL");
const loader = decodeURIComponent(bookmarklet.slice("javascript:".length));
const sourceUrl = loader.match(/https:\/\/raw\.githubusercontent\.com\/HotKids\/apkbot\/[a-f0-9]{40}\/samsung-assistant\/browser\.js/)[0];
assert.ok(loader.includes(createHash("sha256").update(source).digest("hex")), "The pinned payload must match local browser.js");
const sourceResponse = { contentType: "text/plain; charset=utf-8", headers: { "Access-Control-Allow-Origin": "*" }, body: source };
const metadata = {
  GUID: "com.samsung.android.app.sreminder", productID: "12345",
  productName: "Samsung Assistant", version: "9.4.02.7", versionCode: "940207000",
  realContentsSize: "1234", needToLogin: "0", installableYN: "Y"
};
const grant = {
  productID: "12345", contentsSize: "1234",
  downLoadURI: "https://download.samsungapps.com/fixture.apk"
};
const escapeXml = text => String(text).replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"
}[c]));
const xml = fields => `<SamsungProtocol><response><errorString errorCode="0">Success</errorString>${Object.entries(fields).map(([key, value]) => `<param name="${key}">${escapeXml(value)}</param>`).join("")}</response></SamsungProtocol>`;

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROME_BINARY ? { executablePath: process.env.CHROME_BINARY } : {})
  });
  let checks = 0;
  try {
    async function run(name, responses, expected, verify, loaderResponse = sourceResponse, setupPage) {
      const page = await browser.newPage({ viewport: { width: 412, height: 915 } });
      const requests = [];
      const downloadRequests = [];
      const downloads = [];
      page.on("download", download => downloads.push(download));
      const automaticDownload = expected === "ready" ? page.waitForEvent("download", { timeout: 5000 }) : null;
      automaticDownload?.catch(() => {});
      let sourceRequests = 0;
      if (setupPage) await setupPage(page);
      await page.route("**/*", async route => {
        const request = route.request();
        if (request.url() === "https://cn-ms.galaxyappstore.com/") {
          return route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body></body></html>" });
        }
        if (request.url() === sourceUrl) {
          assert.equal(request.method(), "GET");
          sourceRequests++;
          return typeof loaderResponse === "function" ? loaderResponse(route) : route.fulfill(loaderResponse);
        }
        if (request.url() === grant.downLoadURI) {
          assert.equal(request.method(), "GET");
          downloadRequests.push(request.url());
          return route.fulfill({
            contentType: "application/vnd.android.package-archive",
            headers: { "Content-Disposition": "attachment; filename=fixture.apk" },
            body: Buffer.alloc(Number(grant.contentsSize))
          });
        }
        assert.equal(request.method(), "POST");
        assert.match(request.url(), /^https:\/\/cn-ms\.galaxyappstore\.com\/ods\.as\?reqId=(2298|2316)&ot=01&ct=B$/);
        requests.push(request.postData());
        return route.fulfill({ contentType: "application/xml", body: responses[requests.length - 1] });
      });
      try {
        await page.goto("https://cn-ms.galaxyappstore.com/");
        await page.evaluate(url => { location.href = url; }, bookmarklet);
        if (expected === "loader-error") {
          await page.waitForFunction(() => document.getElementById("assistant-loader")?.textContent.startsWith("书签加载失败"));
          assert.equal(await page.locator("#assistant-download").count(), 0);
          assert.equal(requests.length, 0);
          assert.equal(sourceRequests, 1);
        } else {
          await page.waitForFunction(() => {
            const status = document.querySelector("[data-state]");
            return status && status.dataset.state !== "pending";
          });
          assert.equal(await page.locator("[data-state]").getAttribute("data-state"), expected, name);
          assert.equal(await page.locator("#assistant-download a").isVisible(), expected === "ready", name);
          assert.equal(await page.locator("#assistant-loader").count(), 0);
        }
        if (automaticDownload) {
          const download = await automaticDownload;
          assert.equal(download.url(), grant.downLoadURI);
          assert.equal(download.suggestedFilename(), "fixture.apk");
          assert.equal(await download.failure(), null);
        }
        if (verify) await verify(page, requests);
        assert.equal(downloadRequests.length, expected === "ready" ? 1 : 0, `${name}: download requests`);
        assert.equal(downloads.length, expected === "ready" ? 1 : 0, `${name}: download events`);
        checks++;
        console.log(`PASS ${name}`);
      } finally {
        await page.close();
      }
    }

    await run("same-origin bookmarklet validates ODS and starts one download", [xml(metadata), xml(grant)], "ready", async (page, requests) => {
      assert.equal(requests.length, 2);
      const identities = requests.map(body => /logId="([a-f0-9]{16})"/.exec(body)[1]);
      assert.equal(identities[0], identities[1]);
      assert.match(requests[0], /name="getDownloadInfo" id="2298" numParam="12"/);
      assert.match(requests[1], /name="downloadForRestore" id="2316" numParam="11"/);
      assert.equal(await page.locator("#assistant-download a").getAttribute("href"), grant.downLoadURI);
      assert.equal(await page.locator("#assistant-download a").textContent(), "下载");
      assert.equal(await page.locator("[data-state]").textContent(), "下载链接已获取。如未开始下载，请点击「下载」。");
      assert.match(await page.locator("#assistant-download p:nth-of-type(2)").textContent(), /^版本：/);
    });
    await run("rerun removes the old download link", [xml(metadata), xml(grant), '<SamsungProtocol><errorString errorCode="1">Denied</errorString></SamsungProtocol>'], "ready", async page => {
      await page.evaluate(url => { location.href = url; }, bookmarklet);
      await page.waitForFunction(() => document.querySelector("[data-state]")?.dataset.state === "error");
      assert.equal(await page.locator("#assistant-download").count(), 1);
      assert.equal(await page.locator("#assistant-download a").isVisible(), false);
      assert.equal(await page.locator("#assistant-download a").getAttribute("href"), null);
    });
    await run("login stops before authorization", [xml({ ...metadata, needToLogin: "1" })], "error", (_, requests) => assert.equal(requests.length, 1));
    await run("authorization failure hides download", [xml(metadata), '<SamsungProtocol><errorString errorCode="1">Denied</errorString></SamsungProtocol>'], "error");
    await run("empty optional identity is rejected", [xml(metadata), xml({ ...grant, GUID: "" })], "error");
    await run("version drift is rejected", [xml(metadata), xml({ ...grant, version: "10.0" })], "error");
    await run("size mismatch is rejected", [xml(metadata), xml({ ...grant, contentsSize: "4321" })], "error");
    await run("unrelated download host is rejected", [xml(metadata), xml({ ...grant, downLoadURI: "https://samsungapps.com.example.org/a.apk" })], "error");
    await run("DTD is rejected", ['<!DOCTYPE SamsungProtocol [<!ENTITY x "test">]><SamsungProtocol/>'], "error");
    await run("duplicate response fields are rejected", [xml(metadata).replace("</response>", '<param name="GUID">duplicate</param></response>')], "error");
    await run("oversized XML is rejected while reading", ["x".repeat(2000001)], "error");
    await run("positive numbers tolerate leading zeroes", [xml({ ...metadata, versionCode: "0940207000", realContentsSize: "01234" }), xml({ ...grant, versionCode: "940207000" })], "ready");
    await run("modified script is rejected before execution", [], "loader-error", async page => {
      assert.equal(await page.evaluate(() => window.untrustedScriptExecuted), undefined);
    }, { ...sourceResponse, body: "window.untrustedScriptExecuted=true;" });
    await run("script HTTP failure stops before Samsung requests", [], "loader-error", null, { ...sourceResponse, status: 503 });
    await run("oversized script is rejected before execution", [], "loader-error", null, { ...sourceResponse, body: "x".repeat(65537) });
    await run("script load timeout stops before Samsung requests", [], "loader-error", null, async route => {
      await new Promise(resolve => setTimeout(resolve, 100));
      // The loader may abort the intercepted request before its response arrives.
      await route.fulfill(sourceResponse).catch(() => {});
    }, page => page.addInitScript(() => {
      const setTimer = window.setTimeout.bind(window);
      window.setTimeout = (callback, delay, ...args) => setTimer(callback, delay === 15000 ? 25 : delay, ...args);
    }));
    const wrongOrigin = await browser.newPage();
    try {
      let sourceRequests = 0;
      let downloads = 0;
      wrongOrigin.on("download", () => downloads++);
      await wrongOrigin.route("**/*", route => {
        if (route.request().url() === sourceUrl) sourceRequests++;
        return route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body></body></html>" });
      });
      wrongOrigin.on("dialog", async dialog => {
        assert.equal(dialog.message(), "请先打开三星页面，再次运行此书签。");
        await dialog.accept();
      });
      await wrongOrigin.goto("https://example.org/");
      await wrongOrigin.evaluate(url => { location.href = url; }, bookmarklet);
      await wrongOrigin.waitForURL("https://cn-ms.galaxyappstore.com/");
      assert.equal(sourceRequests, 0);
      assert.equal(downloads, 0);
      assert.equal(await wrongOrigin.locator("#assistant-download").count(), 0);
      checks++;
      console.log("PASS wrong origin redirects without loading or querying");
    } finally {
      await wrongOrigin.close();
    }
    const nativeFailure = await browser.newPage();
    try {
      let downloads = 0;
      nativeFailure.on("download", () => downloads++);
      await nativeFailure.route("**/*", route => route.fulfill(route.request().url() === sourceUrl ? sourceResponse : { contentType: "text/html", body: "<html><body></body></html>" }));
      await nativeFailure.goto("https://cn-ms.galaxyappstore.com/");
      await nativeFailure.evaluate(sourceSize => {
        const digest = crypto.subtle.digest.bind(crypto.subtle);
        crypto.subtle.digest = (algorithm, bytes) => bytes.byteLength === sourceSize ? digest(algorithm, bytes) : Promise.reject(new DOMException("Operation failed.", "OperationError"));
      }, Buffer.byteLength(source));
      await nativeFailure.evaluate(url => { location.href = url; }, bookmarklet);
      await nativeFailure.waitForFunction(() => document.querySelector("[data-state]")?.dataset.state === "error");
      assert.equal(await nativeFailure.locator("[data-state]").textContent(), "暂时无法完成请求，请稍后重试。");
      assert.equal(downloads, 0);
      checks++;
      console.log("PASS native errors use the Chinese fallback");
    } finally { await nativeFailure.close(); }
    const installer = await browser.newPage({ viewport: { width: 412, height: 915 } });
    try {
      await installer.setContent(fs.readFileSync(path.join(__dirname, "install.html"), "utf8"));
      assert.equal(await installer.locator("#bookmark").inputValue(), bookmarklet);
      assert.equal(await installer.locator(".open").getAttribute("href"), "https://cn-ms.galaxyappstore.com/");
      await installer.locator("#copy").click();
      assert.match(await installer.locator("#status").textContent(), /已复制|手动复制/);
      assert.equal(await installer.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      checks++;
      console.log("PASS installation page matches source and fits mobile width");
    } finally { await installer.close(); }
    console.log(`${checks} browser checks passed.`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
