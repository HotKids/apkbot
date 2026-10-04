// Offline browser checks. Every request is intercepted; no Samsung calls occur.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const source = fs.readFileSync(path.join(__dirname, "browser.js"), "utf8");
const bookmarklet = "javascript:" + encodeURIComponent(source);
assert.equal(fs.readFileSync(path.join(__dirname, "bookmarklet.txt"), "utf8").trim(), bookmarklet);
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
    async function run(name, responses, expected, verify) {
      const page = await browser.newPage({ viewport: { width: 412, height: 915 } });
      const requests = [];
      await page.route("**/*", async route => {
        const request = route.request();
        if (request.url() === "https://cn-ms.galaxyappstore.com/") {
          return route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body></body></html>" });
        }
        assert.equal(request.method(), "POST");
        assert.match(request.url(), /^https:\/\/cn-ms\.galaxyappstore\.com\/ods\.as\?reqId=(2298|2316)&ot=01&ct=B$/);
        requests.push(request.postData());
        return route.fulfill({ contentType: "application/xml", body: responses[requests.length - 1] });
      });
      try {
        await page.goto("https://cn-ms.galaxyappstore.com/");
        await page.evaluate(url => { location.href = url; }, bookmarklet);
        await page.waitForFunction(() => {
          const status = document.querySelector("[data-state]");
          return status && status.dataset.state !== "pending";
        });
        assert.equal(await page.locator("[data-state]").getAttribute("data-state"), expected, name);
        assert.equal(await page.locator("#assistant-download a").isVisible(), expected === "ready", name);
        if (verify) await verify(page, requests);
        checks++;
        console.log(`PASS ${name}`);
      } finally {
        await page.close();
      }
    }

    await run("same-origin bookmarklet and ODS identity", [xml(metadata), xml(grant)], "ready", async (page, requests) => {
      assert.equal(requests.length, 2);
      const identities = requests.map(body => /logId="([a-f0-9]{16})"/.exec(body)[1]);
      assert.equal(identities[0], identities[1]);
      assert.match(requests[0], /name="getDownloadInfo" id="2298" numParam="12"/);
      assert.match(requests[1], /name="downloadForRestore" id="2316" numParam="11"/);
      assert.equal(await page.locator("#assistant-download a").getAttribute("href"), grant.downLoadURI);
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
    const installer = await browser.newPage({ viewport: { width: 412, height: 915 } });
    try {
      await installer.setContent(fs.readFileSync(path.join(__dirname, "install.html"), "utf8"));
      assert.equal(await installer.locator("#bookmark").inputValue(), bookmarklet);
      assert.equal(await installer.locator(".open").getAttribute("href"), "https://cn-ms.galaxyappstore.com/");
      await installer.locator("#copy").click();
      assert.match(await installer.locator("#status").textContent(), /Copied|copy it manually/);
      assert.equal(await installer.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      checks++;
      console.log("PASS installation page matches source and fits mobile width");
    } finally { await installer.close(); }
    console.log(`${checks} browser checks passed.`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
