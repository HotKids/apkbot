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
assert.doesNotMatch(source, /GM_addElement|__apkbotStoreGuard|samsungapps:/);
assert.deepEqual([...source.matchAll(/@connect\s+(\S+)/g)].map(match => match[1]).sort(), ["cn-ms.galaxyappstore.com", "hub-odc.samsungapps.com", "us-odc.samsungapps.com", "vas.samsungapps.com"]);

const packageName = "com.samsung.android.app.sreminder";
const metadata = {
  GUID:packageName, productID:"12345", productName:"三星生活助手",
  version:"9.4.02.7", versionCode:"940207000", realContentsSize:"1234", needToLogin:"0", installableYN:"Y"
};
const grant = {productID:"12345", version:metadata.version, versionCode:metadata.versionCode, contentsSize:"1234", downLoadURI:"https://download.samsungapps.com/fixture.apk"};
const us = {resultCode:"1", appId:packageName, productId:"12345", productName:"US app", versionName:"1.0", versionCode:"100", contentSize:"1234", downloadURI:grant.downLoadURI};
const escapeXml = text => String(text).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&apos;"}[c]));
const xml = fields => `<SamsungProtocol><response><errorString errorCode="0">Success</errorString>${Object.entries(fields).map(([key, value]) => `<param name="${key}">${escapeXml(value)}</param>`).join("")}</response></SamsungProtocol>`;
const ods = (fields, id) => `<SamsungProtocol><response id="${id}" returnCode="0"><errorInfo><errorString errorCode="0">Success</errorString></errorInfo><list>${Object.entries(fields).map(([key, value]) => `<value name="${key}">${escapeXml(value)}</value>`).join("")}</list></response></SamsungProtocol>`;
const stub = fields => `<result>${Object.entries(fields).map(([key, value]) => `<${key}>${escapeXml(value)}</${key}>`).join("")}</result>`;
const stubDenied = stub({resultCode:"0", resultMsg:"Application is not approved as stub"});

function gmBridge(overrides) {
  let index = 0;
  window.GM_xmlhttpRequest = options => {
    const auxiliary = ["2300", "2290", "2291"].includes(new URL(options.url).searchParams.get("reqId"));
    const responseOverride = auxiliary ? null : overrides?.[index++];
    const controller = new AbortController();
    const timer = setTimeout(() => options.ontimeout(), options.timeout);
    fetch(options.url, {method:options.method, body:options.data, headers:options.headers, signal:controller.signal, redirect:options.redirect})
      .then(async response => {
        const bytes = await response.arrayBuffer();
        options.onprogress({loaded:bytes.byteLength, total:bytes.byteLength});
        options.onload({status:response.status, finalUrl:response.url, response:bytes, ...responseOverride});
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
      requests.extra = [];
      const auxiliaryCount = {};
      let apkRequests = 0;
      page.on("download", download => downloads.push(download));
      if (options.setup) await options.setup(page);
      await page.addInitScript(gmBridge, options.gmResponseOverrides);
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
        assert.ok(["vas.samsungapps.com", "cn-ms.galaxyappstore.com", "hub-odc.samsungapps.com", "us-odc.samsungapps.com"].includes(url.hostname), "Only Samsung protocol hosts are queried");
        if (url.hostname === "vas.samsungapps.com") {
          assert.equal(request.method(), "GET");
          assert.equal(url.pathname, "/stub/stubDownload.as");
          assert.equal(url.searchParams.get("appId"), options.allowPackageSwitch ? url.searchParams.get("appId") : options.packageName || packageName);
          assert.equal(url.searchParams.get("mcc"), "310");
          assert.equal(url.searchParams.get("csc"), "XAA");
        } else {
          assert.equal(request.method(), "POST");
          assert.ok(["2300", "2290", "2291", "2298", "2311", "2316", "2801"].includes(url.searchParams.get("reqId")));
          const region = url.hostname === "cn-ms.galaxyappstore.com" ? "CN" : "US";
          assert.match(request.postData(), region === "CN" ? /mcc="460" mnc="00" csc="CHC"/ : /mcc="310" mnc="260" csc="XAA"/);
          if (["2300", "2290", "2291"].includes(url.searchParams.get("reqId"))) {
            requests.extra.push({url, body:request.postData()});
            const response = url.searchParams.get("reqId") === "2300"
              ? (typeof options.discovery === "function" ? options.discovery(region) : options.discovery) ?? ods({countryURL:region === "CN" ? "https://cn-ms.galaxyappstore.com/ods.as" : "https://us-odc.samsungapps.com/ods.as", MCC:region === "CN" ? "460" : "310", countryCode:region === "CN" ? "CHN" : "USA"}, "2300")
              : (typeof options.details?.[url.searchParams.get("reqId")] === "function" ? options.details[url.searchParams.get("reqId")](region, auxiliaryCount[url.searchParams.get("reqId")] = (auxiliaryCount[url.searchParams.get("reqId")] || 0) + 1) : options.details?.[url.searchParams.get("reqId")]) ?? ods({}, url.searchParams.get("reqId"));
            if (options.auxiliaryDelay) await new Promise(resolve => setTimeout(resolve, options.auxiliaryDelay));
            return route.fulfill({contentType:"application/xml", headers:{"Access-Control-Allow-Origin":"*"}, ...(typeof response === "string" ? {body:response} : response)}).catch(() => {});
          }
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
        assert.equal(requests.extra.length, 0);
        assert.equal(await page.locator("#apkbot-download div a").textContent(), "获取");
        assert.equal(await page.locator("#apkbot-download button").isVisible(), false);
        assert.equal(await page.locator("#apkbot-download > p:last-child").textContent(), "");
        const downloadEvent = expected === "ready" && !options.blockAutomaticDownload ? page.waitForEvent("download", {timeout:5000}) : null;
        downloadEvent?.catch(() => {});
        await page.locator("#apkbot-download div a").click();
        if (options.duringRequest) await options.duringRequest(page);
        await page.waitForFunction(() => ["ready", "error"].includes(document.querySelector("#apkbot-download [data-state]")?.dataset.state));
        assert.equal(await page.locator("[data-state]").getAttribute("data-state"), expected, name);
        assert.equal(await page.locator("#apkbot-download div a").textContent(), expected === "ready" ? "下载" : "获取");
        assert.equal(await page.locator("#apkbot-download div a").getAttribute("aria-disabled"), null);
        assert.equal(await page.locator("#apkbot-download section, #apkbot-download blockquote, #apkbot-download h3").count(), 0);
        if (expected === "ready") {
          assert.equal(await page.locator("#apkbot-download div a").getAttribute("href"), grant.downLoadURI);
          const queryCount = requests.length;
          const detailCount = requests.extra.length;
          let download;
          if (downloadEvent) download = await downloadEvent;
          else {
            assert.equal(await page.evaluate(() => window.automaticDownloadAttempts), 1);
            assert.equal(apkRequests, 0);
            assert.equal(downloads.length, 0);
            const manualDownload = page.waitForEvent("download", {timeout:5000});
            await page.locator("#apkbot-download div a").click();
            download = await manualDownload;
          }
          assert.equal(download.url(), grant.downLoadURI);
          assert.equal(download.suggestedFilename(), "fixture.apk");
          assert.equal(await download.failure(), null);
          assert.equal(await page.locator("#apkbot-download div a").getAttribute("href"), grant.downLoadURI);
          assert.equal(requests.length, queryCount, "Downloading uses the ready URL without another query");
          assert.equal(requests.extra.length, detailCount);
        } else {
          assert.equal(await page.locator("#apkbot-download div a").getAttribute("href"), "#");
        }
        assert.equal(apkRequests, expected === "ready" ? 1 : 0);
        assert.equal(downloads.length, expected === "ready" ? 1 : 0);
        if (verify) await verify(page, requests);
        await page.waitForTimeout(30);
        assert.equal(apkRequests, expected === "ready" ? 1 : 0, "Refreshing or failed catalog switches never start an automatic transfer");
        assert.equal(downloads.length, expected === "ready" ? 1 : 0);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        checks++;
        console.log(`PASS ${name}`);
      } finally { await page.close(); }
    }

    await run("explicit US recovers through its own ODS without changing region", [stubDenied, xml(metadata), xml(grant)], "ready", async page => {
      assert.match(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /🇺🇸/);
    }, {query:"?cntyCd=USA"});
    await run("getting CN information on the unsupported page starts one direct download", [xml(metadata), xml(grant)], "ready", async (page, requests) => {
      assert.equal(requests.length, 2);
      const identities = requests.map(request => /logId="([a-f0-9]{16})"/.exec(request.body)[1]);
      assert.equal(identities[0], identities[1]);
      assert.match(requests[0].body, /name="getDownloadInfo" id="2298" numParam="12"/);
      assert.match(requests[1].body, /name="downloadEx2" id="2311" numParam="10"/);
      assert.match(requests[1].body, /name="dowloadType">new</);
      assert.match(requests[1].body, /name="deepLinkSource">N</);
      assert.doesNotMatch(requests[1].body, /name="(?:versionCode|loadType)"/);
      const info = await page.locator("#apkbot-download p:nth-of-type(2)").textContent();
      assert.match(info, /版本：9\.4\.02\.7\n大小：0\.00 MB · 🇨🇳\n包名：/);
      assert.doesNotMatch(info, /文件大小：|版本代码：/);
      const order = await page.locator("#apkbot-download div button, #apkbot-download div a").allTextContents();
      assert.deepEqual(order, ["刷新", "下载"]);
    });
    await run("a blocked automatic attempt leaves a ready URL for manual downloading without another query", [xml(metadata), xml(grant)], "ready", null, {
      blockAutomaticDownload:true,
      setup:page => page.addInitScript(() => {
        window.automaticDownloadAttempts = 0;
        const click = HTMLAnchorElement.prototype.click;
        // Model a browser blocking script-generated navigation, while trusted clicks still work.
        HTMLAnchorElement.prototype.click = function () {
          if (this.closest("#apkbot-download") && this.href.startsWith("https://download.samsungapps.com/")) {
            window.automaticDownloadAttempts++;
            return;
          }
          return click.call(this);
        };
      })
    });
    const denied = '<SamsungProtocol><errorString errorCode="4002">Denied</errorString></SamsungProtocol>';
    const legacyGrant = {...grant};
    delete legacyGrant.version;
    delete legacyGrant.versionCode;
    for (const rejection of [denied, {status:503, body:"unavailable"}]) {
      await run("HTTP or API rejection retains one restore authorization", [xml(metadata), rejection, xml(legacyGrant)], "ready", (_, requests) => {
        assert.deepEqual(requests.map(r => r.url.searchParams.get("reqId")), ["2298", "2311", "2316"]);
        assert.match(requests[2].body, /name="downloadForRestore" id="2316" numParam="11"/);
        assert.match(requests[2].body, /name="downloadType">new</);
        assert.match(requests[2].body, /name="triggeredFrom">DETAIL_PAGE</);
      });
    }
    for (const field of ["version", "versionCode"]) {
      const incomplete = {...grant};
      delete incomplete[field];
      await run(`primary authorization requires ${field} without another grant`, [xml(metadata), xml(incomplete)], "error", (_, requests) => assert.equal(requests.length, 2));
    }
    for (const override of [{status:0}, {status:302}, {status:204}, {status:403, finalUrl:"https://example.org/redirect"}]) {
      await run("non-HTTP responses and redirects never authorize through restore", [xml(metadata), xml(grant), xml(legacyGrant)], "error", (_, requests) => assert.equal(requests.length, 2), {gmResponseOverrides:[{}, override]});
    }
    for (const malformed of ['<html>error</html>', '<SamsungProtocol><errorString errorCode="invalid">Denied</errorString></SamsungProtocol>']) {
      await run("malformed authorization never invokes restore", [xml(metadata), malformed], "error", (_, requests) => assert.equal(requests.length, 2));
    }
    await run("refresh updates one card without starting another download", [xml(metadata), xml(grant), xml(metadata), xml(grant)], "ready", async (page, requests) => {
      await page.locator("#apkbot-download button").click();
      await page.waitForFunction(() => document.querySelector('#apkbot-download [role="status"]').textContent === "下载链接已更新。");
      assert.equal(requests.length, 4);
      assert.equal(await page.locator("#apkbot-download").count(), 1);
      assert.equal(await page.locator("#apkbot-download div a").getAttribute("href"), grant.downLoadURI);
      assert.equal(await page.locator("#apkbot-download div a").textContent(), "下载");
      assert.equal(await page.locator("#apkbot-download > p:last-child").textContent(), "下载链接有效期约为 10 分钟，失效后请点击「刷新」。");
    });
    await run("failed refresh preserves the previous card and direct URL", [xml(metadata), xml(grant), xml({...metadata, version:"10.0", versionCode:"1000000000", productName:"New app name"}), denied, denied, denied], "ready", async (page, requests) => {
      await page.locator("#apkbot-download button").click();
      await page.waitForFunction(() => document.querySelector('#apkbot-download [role="status"]').textContent.startsWith("下载链接更新失败："));
      assert.equal(requests.length, 6);
      assert.equal(await page.locator("#apkbot-download").count(), 1);
      assert.equal(await page.locator("#apkbot-download h2").textContent(), "三星生活助手");
      assert.match(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /版本：9\.4\.02\.7/);
      assert.equal(await page.locator("#apkbot-download div a").getAttribute("href"), grant.downLoadURI);
      assert.equal(await page.locator("[data-state]").getAttribute("data-state"), "ready");
      assert.equal(await page.locator("#apkbot-download div a").textContent(), "下载");
    });
    await run("switching apps clears old information when the new query fails", [xml(metadata), xml(grant), xml({...metadata, GUID:"com.example.other", needToLogin:"1"})], "ready", async (page, requests) => {
      await page.evaluate(() => history.pushState(null, "", "/detail/com.example.other?cntyCd=CHN"));
      await page.locator("#apkbot-download div a").click();
      await page.waitForFunction(() => document.querySelector('#apkbot-download [role="status"]').dataset.state === "error");
      assert.equal(requests.length, 3);
      assert.match(requests[2].body, /name="guid">com\.example\.other</);
      assert.equal(await page.locator("#apkbot-download h2").textContent(), "com.example.other");
      assert.equal(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), "包名：com.example.other");
      assert.equal(await page.locator("#apkbot-download > p:last-child").textContent(), "");
      assert.equal(await page.locator("#apkbot-download button").isVisible(), false);
      assert.equal(await page.locator("#apkbot-download div a").getAttribute("href"), "#");
      assert.equal(await page.locator("#apkbot-download div a").textContent(), "获取");
    });
    await run("switching apps stops restore after primary authorization rejection", [xml(metadata), denied, xml(legacyGrant)], "error", (_, requests) => assert.equal(requests.length, 2), {
      delay:100,
      duringRequest:async page => {
        await page.waitForRequest(request => new URL(request.url()).searchParams.get("reqId") === "2311");
        await page.evaluate(() => history.pushState(null, "", "/detail/com.example.other?cntyCd=CHN"));
      }
    });
    await run("switching apps stops AUTO before querying another catalog", [stubDenied, xml(metadata), xml(grant)], "error", (_, requests) => assert.equal(requests.length, 1), {
      query:"", delay:100,
      duringRequest:page => page.evaluate(() => history.pushState(null, "", "/detail/com.example.other?cntyCd=CHN"))
    });
    await run("AUTO tries US then CN when the catalog is restricted", [stubDenied, denied, xml(metadata), xml(grant)], "ready", (_, requests) => assert.equal(requests.length, 4), {query:""});
    await run("AUTO keeps an available US full package", [stub(us)], "ready", async (page, requests) => {
      assert.equal(requests.length, 1);
      assert.match(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /🇺🇸/);
    }, {query:""});
    await run("explicit USA never falls back to CN", [stubDenied, denied], "error", (_, requests) => { assert.equal(requests.length, 2); assert.ok(requests.every(r => r.url.hostname !== "cn-ms.galaxyappstore.com")); }, {query:"?cntyCd=USA"});
    await run("invalid US authorization falls back in AUTO", [stub({...us, downloadURI:"https://evil.example/fixture.apk"}), denied, xml(metadata), xml(grant)], "ready", null, {query:""});
    const otherPackage = "org.example.long_application_name";
    await run("generic package names and untrusted titles render as text", [xml({...metadata, GUID:otherPackage, productName:"<b>Another app</b>"}), xml({...grant, GUID:otherPackage})], "ready", async page => {
      assert.equal(await page.locator("#apkbot-download h2").textContent(), "<b>Another app</b>");
      assert.equal(await page.locator("#apkbot-download h2 b").count(), 0);
    }, {packageName:otherPackage, host:"apps.galaxyappstore.com", query:"?cntyCd=CN"});
    await run("the captured package remains usable after a same-document error route", [xml(metadata), xml(grant)], "ready", null, {html:'<script>history.replaceState(null,"","/error/4002?cntyCd=CHN")</script>'});
    await run("login stops before authorization", [xml({...metadata, needToLogin:"1"})], "error", (_, requests) => assert.equal(requests.length, 1));
    await run("authorization rejection leaves no URL and never loops", [xml(metadata), denied, denied, denied], "error", (_, requests) => assert.equal(requests.length, 4));
    await run("wrong package is rejected", [xml({...metadata, GUID:"com.other.app"})], "error");
    await run("version drift is rejected", [xml(metadata), xml({...grant, version:"10.0"})], "error");
    await run("size mismatch is rejected", [xml(metadata), xml({...grant, contentsSize:"4321"})], "error");
    await run("unrelated download host is rejected", [xml(metadata), xml({...grant, downLoadURI:"https://samsungapps.com.example.org/a.apk"})], "error");
    await run("a CDN root is not accepted as an APK", [xml(metadata), xml({...grant, downLoadURI:"https://download.samsungapps.com/"})], "error");
    await run("DTD is rejected", ['<!DOCTYPE SamsungProtocol [<!ENTITY x "test">]><SamsungProtocol/>'], "error");
    await run("conflicting response fields are rejected", [xml(metadata).replace("</response>", '<param name="GUID">duplicate</param></response>')], "error");
    await run("oversized XML aborts the request", ["x".repeat(2000001)], "error");
    await run("positive numbers tolerate leading zeroes", [xml({...metadata, versionCode:"0940207000", realContentsSize:"01234"}), xml({...grant, versionCode:"940207000"})], "ready");
    await run("HTTP failures retain a retryable button", [{status:503, body:"unavailable"}], "error");
    await run("a page change during a request never authorizes the old app or duplicates the query", [xml(metadata)], "error", (_, requests) => assert.equal(requests.length, 1), {
      delay:100,
      duringRequest:async page => {
        await page.evaluate(() => history.pushState(null, "", "/detail/com.example.other?cntyCd=CHN"));
        await page.evaluate(() => document.querySelector("#apkbot-download div a").click());
      }
    });
    await run("external deadlines are finite", [xml(metadata)], "error", null, {
      delay:100,
      setup:page => page.addInitScript(() => {
        const timer = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => timer(callback, delay === 40000 ? 25 : delay, ...args);
      })
    });
    const mainDetails = {...metadata};
    const overviewDetails = {version:metadata.version, realContentsSize:metadata.realContentsSize, lastUpdateDate:"2026;08;25;", updateDescription:"  发布方原文\n<b>保留文本</b>\n完整更新日志\n"};
    const detailResponses = {
      "2290":ods(mainDetails, "2290"),
      "2291":ods(overviewDetails, "2291").replace('</list>', '<extList name="dataSafetyList"><extList name="dataSafety">first</extList><extList name="dataSafety">second</extList></extList><extList name="curatedComponentList"><extList name="componentInfo"><type>one</type></extList><extList name="componentInfo"><type>two</type></extList></extList></list>')
    };
    await run("matching detail sandwich displays the store date without an update log", [xml(metadata), xml(grant)], "ready", async (page, requests) => {
      assert.deepEqual(requests.extra.map(r => r.url.searchParams.get("reqId")), ["2300", "2290", "2291", "2290"]);
      assert.match(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /版本：9\.4\.02\.7 · 2026-08-25\n大小：/);
      assert.equal(await page.locator("#apkbot-download details").count(), 0);
      assert.equal(await page.locator("#apkbot-download h2 a").getAttribute("href"), `https://galaxystore.samsung.com/detail/${packageName}`);
    }, {details:detailResponses});
    await run("refresh reuses discovery and preserves one card without another automatic transfer", [xml(metadata), xml(grant), xml(metadata), xml(grant)], "ready", async (page, requests) => {
      await page.locator("#apkbot-download button").click();
      await page.waitForFunction(() => document.querySelector('#apkbot-download [role="status"]').textContent === "下载链接已更新。");
      assert.equal(requests.extra.filter(r => r.url.searchParams.get("reqId") === "2300").length, 1);
      assert.match(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /版本：9\.4\.02\.7 · 2026-08-25\n大小：/);
    }, {details:detailResponses});
    await run("refresh hides a date that the store no longer supplies", [xml(metadata), xml(grant), xml(metadata), xml(grant)], "ready", async page => {
      assert.match(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /版本：9\.4\.02\.7 · 2026-08-25\n大小：/);
      await page.locator("#apkbot-download button").click();
      await page.waitForFunction(() => document.querySelector('#apkbot-download [role="status"]').textContent === "下载链接已更新。");
      assert.doesNotMatch(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /\d{4}-\d{2}-\d{2}/);
    }, {details:{...detailResponses, "2291":(_, count) => ods({...overviewDetails, lastUpdateDate:count === 1 ? "2026;08;25;" : ""}, "2291")}});
    for (const endpoint of ["http://cn-ms.galaxyappstore.com/ods.as", "https://evil.example/ods.as", "https://cn-ms.galaxyappstore.com.example.org/ods.as", "https://cn-ms.galaxyappstore.com/other", "https://cn-ms.galaxyappstore.com/ods.as?secret=1", "https://user@cn-ms.galaxyappstore.com/ods.as", "https://cn-ms.galaxyappstore.com:8443/ods.as"]) {
      await run("discovery only upgrades or retains a fixed trusted region endpoint", [xml(metadata), xml(grant)], "ready", (_, requests) => {
        assert.equal(requests[0].url.origin, "https://cn-ms.galaxyappstore.com");
        assert.equal(requests.extra.filter(r => r.url.searchParams.get("reqId") === "2300").length, 1);
      }, {discovery:ods({countryURL:endpoint, MCC:"460", countryCode:"CHN"}, "2300")});
    }
    await run("failed endpoint discovery uses the fixed catalog without another discovery loop", [xml(metadata), xml(grant)], "ready", null, {discovery:{status:503, body:"unavailable"}});
    for (const date of ["", "2026;02;30;", "2026-08-25", "2026;08;25;extra"]) {
      await run("missing or invalid store dates hide the date field", [xml(metadata), xml(grant)], "ready", async page => {
        assert.doesNotMatch(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /\d{4}-\d{2}-\d{2}/);
      }, {details:{...detailResponses, "2291":ods({...overviewDetails, lastUpdateDate:date}, "2291")}});
    }
    await run("overview with a different version is not attached to the download", [xml(metadata), xml(grant)], "ready", async (page, requests) => {
      assert.doesNotMatch(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /\d{4}-\d{2}-\d{2}/);
      assert.equal(requests.extra.filter(r => r.url.searchParams.get("reqId") === "2290").length, 1);
    }, {details:{...detailResponses, "2291":ods({...overviewDetails, version:"10.0"}, "2291")}});
    await run("a version change across the overview discards its date", [xml(metadata), xml(grant)], "ready", async page => {
      assert.doesNotMatch(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /\d{4}-\d{2}-\d{2}/);
    }, {details:{...detailResponses, "2290":(_, count) => ods(count === 1 ? mainDetails : {...mainDetails, versionCode:"999999999"}, "2290")}});
    await run("critical overview duplicates are rejected while unrelated nested fields remain ignored", [xml(metadata), xml(grant)], "ready", async page => {
      assert.doesNotMatch(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /\d{4}-\d{2}-\d{2}/);
    }, {details:{...detailResponses, "2291":ods(overviewDetails, "2291").replace('</list>', '<value name="version">conflict</value></list>')}});
    await run("detail responses with another method ID do not contribute display information", [xml(metadata), xml(grant)], "ready", async page => {
      assert.doesNotMatch(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /\d{4}-\d{2}-\d{2}/);
    }, {details:{...detailResponses, "2291":ods(overviewDetails, "2290")}});
    await run("a partner response cannot replace primary authorization", [xml(metadata), ods(grant, "2801")], "error", (_, requests) => {
      assert.equal(requests.length, 2);
    });
    await run("a complex critical field cannot shadow a scalar grant field", [xml(metadata), ods(grant, "2311").replace('</list>', '<extList name="versionCode"><value name="versionCode">999</value></extList></list>')], "error", (_, requests) => {
      assert.equal(requests.length, 2);
    });
    await run("US restore stays in US and yields a full APK", [stubDenied, xml(metadata), denied, xml(legacyGrant)], "ready", (_, requests) => {
      assert.ok(requests.every(r => r.url.hostname !== "cn-ms.galaxyappstore.com"));
      assert.deepEqual(requests.map(r => r.url.searchParams.get("reqId")), [null, "2298", "2311", "2316"]);
    }, {query:"?cntyCd=USA"});
    const mirrorGrant = {...grant, GUID:packageName};
    await run("CN partner fallback is bounded and requires the same full Samsung package", [xml(metadata), denied, denied, ods(mirrorGrant, "2801")], "ready", (_, requests) => {
      assert.deepEqual(requests.map(r => r.url.searchParams.get("reqId")), ["2298", "2311", "2316", "2801"]);
      assert.match(requests[3].body, /name="tencentSource">general</);
      assert.doesNotMatch(requests[3].body, /createOrder|easybuyPurchase/);
    });
    for (const unsafe of [{GUID:"com.other.app"}, {versionCode:"100"}, {contentsSize:"999"}, {downLoadURI:"https://download.example.com/a.apk"}]) {
      await run("CN partner responses must prove identity, version, size and Samsung origin", [xml(metadata), denied, denied, ods({...mirrorGrant, ...unsafe}, "2801")], "error", (_, requests) => assert.equal(requests.length, 4));
    }
    const mirrorWithoutCode = {...mirrorGrant};
    delete mirrorWithoutCode.versionCode;
    await run("CN partner data lacking a version code remains unusable", [xml(metadata), denied, denied, ods(mirrorWithoutCode, "2801")], "error");
    await run("US rejection never invokes the CN partner endpoint", [stubDenied, xml(metadata), denied, denied], "error", (_, requests) => assert.equal(requests.filter(r => r.url.searchParams.get("reqId") === "2801").length, 0), {query:"?cntyCd=USA"});
    await run("changing region clears the former link before querying the new region", [xml(metadata), xml(grant), stubDenied, denied], "ready", async (page, requests) => {
      await page.evaluate(() => history.pushState(null, "", `?cntyCd=USA`));
      await page.locator("#apkbot-download div a").click();
      await page.waitForFunction(() => document.querySelector('#apkbot-download [role="status"]').dataset.state === "error");
      assert.equal(await page.locator("#apkbot-download div a").getAttribute("href"), "#");
      assert.equal(await page.locator("#apkbot-download div a").textContent(), "获取");
      assert.equal(await page.locator("#apkbot-download > p:last-child").textContent(), "");
      assert.equal(await page.locator("#apkbot-download button").isVisible(), false);
      assert.equal(requests[3].url.hostname, "us-odc.samsungapps.com");
    });
    await run("changing region during a request stops old catalog authorization", [xml(metadata)], "error", (_, requests) => assert.equal(requests.length, 1), {
      delay:100,
      duringRequest:async page => {
        await page.waitForRequest(request => new URL(request.url()).searchParams.get("reqId") === "2298");
        await page.evaluate(() => history.pushState(null, "", "?cntyCd=USA"));
      }
    });
    await run("a wrong product main response never contributes a date", [xml(metadata), xml(grant)], "ready", async (page, requests) => {
      assert.doesNotMatch(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /\d{4}-\d{2}-\d{2}/);
      assert.equal(requests.extra.filter(r => r.url.searchParams.get("reqId") === "2291").length, 0);
    }, {details:{...detailResponses, "2290":ods({...mainDetails, productID:"99999"}, "2290")}});
    await run("overview size mismatch never attaches another binary's date", [xml(metadata), xml(grant)], "ready", async page => {
      assert.doesNotMatch(await page.locator("#apkbot-download p:nth-of-type(2)").textContent(), /\d{4}-\d{2}-\d{2}/);
    }, {details:{...detailResponses, "2291":ods({...overviewDetails, realContentsSize:"999"}, "2291")}});
    await run("malformed protocol returnCode is not an authorization rejection", [xml(metadata), denied.replace('<SamsungProtocol>', '<SamsungProtocol><response returnCode="invalid">').replace('</SamsungProtocol>', '</response></SamsungProtocol>')], "error", (_, requests) => assert.equal(requests.length, 2));
    await run("a captured explicit region survives the site's same-document error page", [xml(metadata), xml(grant)], "ready", null, {html:'<script>history.replaceState(null,"","/error/4002")</script>'});
    await run("US endpoint discovery cannot switch to the CN catalog", [stubDenied, xml(metadata), xml(grant)], "ready", (_, requests) => {
      assert.equal(requests[1].url.hostname, "us-odc.samsungapps.com");
      assert.ok(requests.every(r => r.url.hostname !== "cn-ms.galaxyappstore.com"));
    }, {query:"?cntyCd=USA", discovery:ods({countryURL:"http://cn-ms.galaxyappstore.com/ods.as"}, "2300")});
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
