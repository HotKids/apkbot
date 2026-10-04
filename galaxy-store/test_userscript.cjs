// Offline browser checks; the GM bridge is emulated and all HTTP is intercepted.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const source = fs.readFileSync(path.join(__dirname, "galaxy-store.user.js"), "utf8");
assert.match(source, /^\/\/ ==UserScript==/);
assert.match(source, /@grant\s+GM_xmlhttpRequest/);
assert.match(source, /@run-at\s+document-start/);
assert.match(source, /@match\s+https:\/\/apps\.galaxyappstore\.com\/detail\/\*/);
assert.deepEqual([...source.matchAll(/@connect\s+(\S+)/g)].map(match => match[1]).sort(), ["cn-ms.galaxyappstore.com", "vas.samsungapps.com"]);

const packageName = "com.samsung.android.app.sreminder";
const metadata = {
  GUID:packageName, productID:"12345", productName:"三星生活助手",
  version:"9.4.02.7", versionCode:"940207000", realContentsSize:"1234", needToLogin:"0", installableYN:"Y"
};
const grant = {productID:"12345", contentsSize:"1234", downLoadURI:"https://download.samsungapps.com/fixture.apk"};
const us = {resultCode:"1", appId:packageName, productId:"12345", productName:"US app", versionName:"1.0", versionCode:"100", contentSize:"1234", downloadURI:grant.downLoadURI};
const escapeXml = text => String(text).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&apos;"}[c]));
const xml = fields => `<SamsungProtocol><response><errorString errorCode="0">Success</errorString>${Object.entries(fields).map(([key, value]) => `<param name="${key}">${escapeXml(value)}</param>`).join("")}</response></SamsungProtocol>`;
const stub = fields => `<result>${Object.entries(fields).map(([key, value]) => `<${key}>${escapeXml(value)}</${key}>`).join("")}</result>`;
const stubDenied = stub({resultCode:"0", resultMsg:"Application is not approved as stub"});

function gmBridge() {
  window.GM_addElement = (tag, attributes) => {
    const element = document.createElement(tag);
    Object.assign(element, attributes);
    (document.head || document.documentElement).append(element);
    return element;
  };
  window.GM_xmlhttpRequest = options => {
    const controller = new AbortController();
    const timer = setTimeout(() => options.ontimeout(), options.timeout);
    fetch(options.url, {method:options.method, body:options.data, headers:options.headers, signal:controller.signal, redirect:options.redirect})
      .then(async response => {
        const bytes = await response.arrayBuffer();
        options.onprogress({loaded:bytes.byteLength, total:bytes.byteLength});
        options.onload({status:response.status, finalUrl:response.url, response:bytes});
      }).catch(error => error.name === "AbortError" ? options.onabort() : options.onerror())
      .finally(() => clearTimeout(timer));
    return {abort:() => controller.abort()};
  };
}

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.CHROME_BINARY ? {executablePath:process.env.CHROME_BINARY} : {})});
  let checks = 0;
  try {
    async function run(name, responses, expected, verify, options = {}) {
      const page = await browser.newPage({viewport:{width:412, height:915}});
      const requests = [], downloads = [];
      let apkRequests = 0;
      page.on("download", download => downloads.push(download));
      if (options.setup) await options.setup(page);
      await page.addInitScript(gmBridge);
      await page.addInitScript({content:source});
      await page.route("**/*", async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (["galaxystore.samsung.com", "apps.galaxyappstore.com"].includes(url.hostname)) {
          return route.fulfill({status:options.pageStatus || 404, contentType:"text/html", body:`<!doctype html><html><body><h1>应用程序不受支持</h1><p>此应用程序不再出售或在此国家不受支持。</p>${options.html || ""}</body></html>`});
        }
        if (request.url() === grant.downLoadURI) {
          apkRequests++;
          return route.fulfill({contentType:"application/vnd.android.package-archive", headers:{"Content-Disposition":"attachment; filename=fixture.apk"}, body:Buffer.alloc(1234)});
        }
        assert.ok(["vas.samsungapps.com", "cn-ms.galaxyappstore.com"].includes(url.hostname), "Only Samsung protocol hosts are queried");
        if (url.hostname === "vas.samsungapps.com") {
          assert.equal(request.method(), "GET");
          assert.equal(url.pathname, "/stub/stubDownload.as");
          assert.equal(url.searchParams.get("appId"), options.packageName || packageName);
          assert.equal(url.searchParams.get("mcc"), "310");
          assert.equal(url.searchParams.get("csc"), "XAA");
        } else {
          assert.equal(request.method(), "POST");
          assert.ok(["2298", "2316"].includes(url.searchParams.get("reqId")));
          assert.match(request.postData(), /mcc="460" mnc="00" csc="CHC"/);
        }
        requests.push({url, body:request.postData()});
        const response = responses[requests.length - 1];
        assert.ok(response, "No unexpected extra metadata or authorization requests");
        if (options.delay) await new Promise(resolve => setTimeout(resolve, options.delay));
        return route.fulfill({contentType:"application/xml", headers:{"Access-Control-Allow-Origin":"*"}, ...(typeof response === "string" ? {body:response} : response)}).catch(() => {});
      });
      try {
        await page.goto(`https://${options.host || "galaxystore.samsung.com"}/detail/${options.packageName || packageName}${options.query ?? "?cntyCd=CHN"}`);
        assert.equal(await page.locator("#apkbot-download").count(), 1);
        assert.equal(requests.length, 0, "Opening the page does not authorize a download");
        const downloadEvent = expected === "ready" ? page.waitForEvent("download", {timeout:5000}) : null;
        downloadEvent?.catch(() => {});
        await page.locator("#apkbot-download a").click();
        if (options.duringRequest) await options.duringRequest(page);
        await page.waitForFunction(() => ["ready", "error"].includes(document.querySelector("#apkbot-download [data-state]")?.dataset.state));
        assert.equal(await page.locator("[data-state]").getAttribute("data-state"), expected, name);
        assert.equal(await page.locator("#apkbot-download a").textContent(), "下载 APK");
        assert.equal(await page.locator("#apkbot-download a").getAttribute("aria-disabled"), null);
        if (downloadEvent) {
          const download = await downloadEvent;
          assert.equal(download.url(), grant.downLoadURI);
          assert.equal(download.suggestedFilename(), "fixture.apk");
          assert.equal(await download.failure(), null);
          assert.equal(await page.locator("#apkbot-download a").getAttribute("href"), grant.downLoadURI);
        } else {
          assert.equal(await page.locator("#apkbot-download a").getAttribute("href"), "#");
        }
        assert.equal(apkRequests, expected === "ready" ? 1 : 0);
        assert.equal(downloads.length, expected === "ready" ? 1 : 0);
        if (verify) await verify(page, requests);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        checks++;
        console.log(`PASS ${name}`);
      } finally { await page.close(); }
    }

    await run("CN mapping works on the unsupported page and starts one direct download", [xml(metadata), xml(grant)], "ready", async (page, requests) => {
      assert.equal(requests.length, 2);
      const identities = requests.map(request => /logId="([a-f0-9]{16})"/.exec(request.body)[1]);
      assert.equal(identities[0], identities[1]);
      assert.match(requests[0].body, /name="getDownloadInfo" id="2298" numParam="12"/);
      assert.match(requests[1].body, /name="downloadForRestore" id="2316" numParam="11"/);
      assert.match(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /9\.4\.02\.7 · 🇨🇳/);
    });
    await run("AUTO tries US then CN when the catalog is restricted", [stubDenied, xml(metadata), xml(grant)], "ready", (_, requests) => assert.equal(requests.length, 3), {query:""});
    await run("AUTO keeps an available US full package", [stub(us)], "ready", async (page, requests) => {
      assert.equal(requests.length, 1);
      assert.match(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /🇺🇸/);
    }, {query:""});
    await run("explicit USA never falls back to CN", [stubDenied], "error", (_, requests) => assert.equal(requests.length, 1), {query:"?cntyCd=USA"});
    await run("invalid US authorization falls back in AUTO", [stub({...us, downloadURI:"https://evil.example/fixture.apk"}), xml(metadata), xml(grant)], "ready", null, {query:""});
    const otherPackage = "org.example.long_application_name";
    await run("generic package names and untrusted titles render as text", [xml({...metadata, GUID:otherPackage, productName:"<b>Another app</b>"}), xml({...grant, GUID:otherPackage})], "ready", async page => {
      assert.equal(await page.locator("#apkbot-download h2").textContent(), "<b>Another app</b>");
      assert.equal(await page.locator("#apkbot-download h2 b").count(), 0);
    }, {packageName:otherPackage, host:"apps.galaxyappstore.com", query:"?cntyCd=CN"});
    await run("the captured package remains usable after a same-document error route", [xml(metadata), xml(grant)], "ready", null, {html:'<script>history.replaceState(null,"","/error/4002?cntyCd=CHN")</script>'});
    await run("store links and popup launches are blocked while APK download works", [xml(metadata), xml(grant)], "ready", async page => {
      assert.equal(await page.evaluate(() => window.storePopup), null);
      await page.locator("#store-link").click();
      assert.equal(await page.evaluate(() => window.storeClick), undefined);
      await page.evaluate(() => document.querySelector("#store-link").click());
      assert.equal(await page.evaluate(() => window.storeClick), undefined);
      assert.equal(new URL(page.url()).pathname, `/detail/${packageName}`);
    }, {html:'<a id="store-link" href="samsungapps://productdetail/com.samsung.android.app.sreminder" onclick="window.storeClick=true">打开商店</a><script>window.storePopup=window.open("intent://productdetail/com.example.app#Intent;scheme=samsungapps;package=com.sec.android.app.samsungapps;end")</script>'});
    await run("login stops before authorization", [xml({...metadata, needToLogin:"1"})], "error", (_, requests) => assert.equal(requests.length, 1));
    await run("authorization rejection leaves no URL", [xml(metadata), '<SamsungProtocol><errorString errorCode="1">Denied</errorString></SamsungProtocol>'], "error");
    await run("wrong package is rejected", [xml({...metadata, GUID:"com.other.app"})], "error");
    await run("version drift is rejected", [xml(metadata), xml({...grant, version:"10.0"})], "error");
    await run("size mismatch is rejected", [xml(metadata), xml({...grant, contentsSize:"4321"})], "error");
    await run("unrelated download host is rejected", [xml(metadata), xml({...grant, downLoadURI:"https://samsungapps.com.example.org/a.apk"})], "error");
    await run("DTD is rejected", ['<!DOCTYPE SamsungProtocol [<!ENTITY x "test">]><SamsungProtocol/>'], "error");
    await run("conflicting response fields are rejected", [xml(metadata).replace("</response>", '<param name="GUID">duplicate</param></response>')], "error");
    await run("oversized XML aborts the request", ["x".repeat(2000001)], "error");
    await run("positive numbers tolerate leading zeroes", [xml({...metadata, versionCode:"0940207000", realContentsSize:"01234"}), xml({...grant, versionCode:"940207000"})], "ready");
    await run("HTTP failures retain a retryable button", [{status:503, body:"unavailable"}], "error");
    await run("a page change during a request never authorizes the old app or duplicates the query", [xml(metadata)], "error", (_, requests) => assert.equal(requests.length, 1), {
      delay:100,
      duringRequest:async page => {
        await page.evaluate(() => history.pushState(null, "", "/detail/com.example.other?cntyCd=CHN"));
        await page.evaluate(() => document.querySelector("#apkbot-download a").click());
      }
    });
    await run("external deadlines are finite", [xml(metadata)], "error", null, {
      delay:100,
      setup:page => page.addInitScript(() => {
        const timer = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => timer(callback, delay === 40000 ? 25 : delay, ...args);
      })
    });
    const outside = await browser.newPage();
    try {
      await outside.addInitScript({content:source});
      await outside.route("**/*", route => route.fulfill({contentType:"text/html", body:"<html><body></body></html>"}));
      await outside.goto("https://example.org/detail/com.example.app");
      assert.equal(await outside.locator("#apkbot-download").count(), 0);
      checks++;
      console.log("PASS unrelated pages are not modified");
    } finally { await outside.close(); }
    console.log(`${checks} userscript browser checks passed.`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
