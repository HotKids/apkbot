// ==UserScript==
// @name         Galaxy Store APK 下载
// @namespace    https://github.com/HotKids/apkbot
// @version      1.0.0
// @description  在 Galaxy Store 应用详情页获取 Samsung APK 下载链接。
// @match        https://galaxystore.samsung.com/detail/*
// @match        https://apps.galaxyappstore.com/detail/*
// @grant        GM_xmlhttpRequest
// @grant        GM_addElement
// @connect      vas.samsungapps.com
// @connect      cn-ms.galaxyappstore.com
// @run-at       document-start
// @noframes
// @homepageURL  https://github.com/HotKids/apkbot
// @downloadURL  https://raw.githubusercontent.com/HotKids/apkbot/main/galaxy-store/galaxy-store.user.js
// @updateURL    https://raw.githubusercontent.com/HotKids/apkbot/main/galaxy-store/galaxy-store.user.js
// ==/UserScript==

(() => {
  "use strict";
  class StoreError extends Error {}
  const origin = "https://cn-ms.galaxyappstore.com";
  const readPackage = () => {
    const name = location.pathname.match(/^\/detail\/([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)$/)?.[1];
    return name && name.length <= 255 ? name : null;
  };
  let packageName = readPackage();
  if (!["https://galaxystore.samsung.com", "https://apps.galaxyappstore.com"].includes(location.origin) || !packageName || window.top !== window.self) return;
  // Capture the package before Samsung's client can replace a restricted detail route.
  const regionFor = country => ({CN:"CN", CHN:"CN", US:"US", USA:"US"})[country?.toUpperCase()] || "AUTO";
  const requestedRegion = regionFor(new URL(location.href).searchParams.get("cntyCd"));
  // Install in the page realm so the site's popup calls use the same guard.
  const installGuard = () => {
    const guard = GM_addElement("script", {textContent:`(() => {
    if (window.__apkbotStoreGuard) return;
    window.__apkbotStoreGuard = true;
    const isStore = value => {
      const url = String(value || "");
      return /^samsungapps:/i.test(url) || (/^intent:/i.test(url) && /(?:scheme=samsungapps|package=com\\.sec\\.android\\.app\\.samsungapps)(?:;|$)/i.test(url));
    };
    const open = window.open;
    window.open = function(url, ...args) {
      return isStore(url) ? null : open.call(this, url, ...args);
    };
    const click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function() {
      if (!isStore(this.href)) return click.call(this);
    };
    document.addEventListener("click", event => {
      const anchor = event.target.closest?.("a[href]");
      if (anchor && isStore(anchor.href)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
  })();`});
    guard?.remove();
  };
  if (document.documentElement) installGuard();
  else {
    const observer = new MutationObserver(() => {
      if (document.documentElement) {
        observer.disconnect();
        installGuard();
      }
    });
    observer.observe(document, {childList:true});
  }
  document.getElementById("apkbot-download")?.remove();
  const panel = document.createElement("main");
  panel.id = "apkbot-download";
  panel.style.cssText = "position:fixed;z-index:2147483647;bottom:16px;right:12px;left:12px;box-sizing:border-box;width:calc(100% - 24px);max-width:480px;margin-left:auto;padding:16px;font:16px/1.6 system-ui;color:#17202a;background:#fff;border:1px solid #ddd;border-radius:16px;overflow-wrap:anywhere;box-shadow:0 4px 20px #0002";
  const title = document.createElement("h2");
  title.textContent = "Galaxy Store APK 下载";
  title.style.cssText = "margin:0 0 8px;font-size:18px;color:inherit";
  const status = document.createElement("p");
  status.textContent = "点击「下载 APK」获取下载链接。";
  status.dataset.state = "idle";
  status.setAttribute("role", "status");
  const info = document.createElement("p");
  info.textContent = packageName;
  const action = document.createElement("a");
  action.textContent = "下载 APK";
  action.href = "#";
  action.style.cssText = "display:inline-block;padding:12px 20px;background:#1769e0;color:#fff;border-radius:10px;text-decoration:none";
  const hint = document.createElement("p");
  panel.append(title, status, info, action, hint);
  const showPanel = () => (document.body || document.documentElement).append(panel);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", showPanel, {once:true});
  else showPanel();

  const identity = Array.from(crypto.getRandomValues(new Uint8Array(8)), b => b.toString(16).padStart(2, "0")).join("");
  const xmlEscape = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&apos;"}[c]));
  const positive = value => /^\d{1,19}$/.test(value || "") && BigInt(value) > 0n ? BigInt(value) : null;

  async function envelope(method, id, params) {
    const time = new Date(Date.now() + 8 * 3600000).toISOString();
    const hour = time.slice(0, 13).replace(/[-T]/g, "");
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(identity + hour + "GalaxyApps"));
    const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
    const transaction = Number(time.slice(8, 10)) + hash.slice(0, 7);
    const attrs = {
      networkType:"0", version2:"0", lang:"zh_CN", openApiVersion:"36",
      deviceModel:"SM-S9480", deviceMakerName:"samsung", deviceMakerType:"0",
      mcc:"460", mnc:"00", csc:"CHC", odcVersion:"4.6.11.4",
      storeFilter:"themeDeviceModel=SM-S9480_TM", supportFeature:"", version:"7.9",
      filter:"1", odcType:"01", storeMode:"0", cacheVersion:"1", systemId:Date.now(),
      sessionId:transaction + time.slice(0, 16).replace(/[-T:]/g, ""), logId:identity,
      deviceFeature:"locale=zh_CN||abi32=armeabi-v7a:armeabi||abi64=arm64-v8a",
      userMode:"0", asaaMode:"0"
    };
    const attributes = Object.entries(attrs).map(([k,v]) => `${k}="${xmlEscape(v)}"`).join(" ");
    const fields = Object.entries(params).map(([k,v]) => `<param name="${k}">${xmlEscape(v)}</param>`).join("");
    return `<?xml version="1.0" encoding="utf-8"?><SamsungProtocol ${attributes}><request name="${method}" id="${id}" numParam="${Object.keys(params).length}" transactionId="${transaction}">${fields}</request></SamsungProtocol>`;
  }

  function requestBytes(url, method = "GET", data) {
    return new Promise((resolve, reject) => {
      let handle, settled = false;
      const finish = (error, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error) reject(error);
        else resolve(value);
      };
      const cancel = message => {
        finish(new StoreError(message));
        handle?.abort();
      };
      // The outer deadline also applies when the manager uses fetch for redirects.
      const timer = setTimeout(() => cancel("请求超时，请重试。"), 40000);
      try {
        handle = GM_xmlhttpRequest({
          url, method, data, responseType:"arraybuffer", redirect:"error", timeout:40000,
          headers:method === "POST" ? {"Content-Type":"text/plain; charset=UTF-8", "Accept":"image/webp"} : {},
          onprogress:response => {
            if (response.loaded > 2000000 || response.total > 2000000) cancel("三星商店返回的信息超出限制，请稍后重试。");
          },
          onerror:() => finish(new StoreError("无法连接 Galaxy Store，请稍后重试。")),
          ontimeout:() => cancel("请求超时，请重试。"),
          onabort:() => finish(new StoreError("请求已中止，请重试。")),
          onload:response => {
            try {
              if (response.status !== 200) throw new StoreError(`三星服务暂时不可用（HTTP ${response.status}），请稍后重试。`);
              if (response.finalUrl !== url) throw new StoreError("三星商店返回了异常跳转，请稍后重试。");
              const bytes = new Uint8Array(response.response);
              if (!bytes.byteLength || bytes.byteLength > 2000000) throw new StoreError("三星商店返回的信息为空或超出限制，请稍后重试。");
              finish(null, new TextDecoder("utf-8", {fatal:true}).decode(bytes));
            } catch (error) { finish(error); }
          }
        });
      } catch (error) { finish(error); }
    });
  }

  function xmlFields(text, rootName) {
      if (/<!DOCTYPE|<!ENTITY/.test(text)) throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
      const doc = new DOMParser().parseFromString(text, "application/xml");
      if (doc.querySelector("parsererror") || doc.documentElement.nodeName !== rootName) throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
      const fields = Object.create(null);
      for (const node of doc.querySelectorAll("*")) {
        if (node.children.length) continue;
        const key = node.getAttribute("name") || node.nodeName;
        if (Object.hasOwn(fields, key)) throw new StoreError("三星商店返回的信息存在冲突，请稍后重试。");
        fields[key] = node.textContent.trim();
        if (key === "errorString" && node.hasAttribute("errorCode")) {
          if (Object.hasOwn(fields, "errorCode")) throw new StoreError("三星商店返回的信息存在冲突，请稍后重试。");
          fields.errorCode = node.getAttribute("errorCode");
        }
      }
      return fields;
  }

  async function request(method, id, params) {
    const fields = xmlFields(await requestBytes(`${origin}/ods.as?reqId=${id}&ot=01&ct=B`, "POST", await envelope(method, id, params)), "SamsungProtocol");
    if (fields.errorCode !== "0" || !["", "success"].includes((fields.errorString || "").toLowerCase())) throw new StoreError("三星商店未批准此请求，请稍后重试。");
    return fields;
  }

  function downloadUrl(value) {
    let url;
    try { url = new URL(value); } catch { throw new StoreError("三星商店返回的下载地址无效，请稍后重试。"); }
    const host = url.hostname;
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443") || url.hash || /[\x00-\x20\s\\]/.test(value) || !(host === "galaxystore.samsung.com" || ["samsungapps.com", "galaxyappstore.com"].some(suffix => host === suffix || host.endsWith("." + suffix)))) throw new StoreError("三星商店返回的下载地址无效，请稍后重试。");
    return url.href;
  }

  async function usDownload() {
    const params = new URLSearchParams({
      appId:packageName, deviceId:"SM-S9480", mcc:"310", mnc:"260", csc:"XAA",
      sdkVer:"36", abiType:"64", extuk:identity, systemId:String(Date.now())
    });
    const fields = xmlFields(await requestBytes("https://vas.samsungapps.com/stub/stubDownload.as?" + params), "result");
    if (fields.resultCode !== "1") throw new StoreError("US 商店未返回可用结果，请指定 CN 或稍后重试。");
    if (fields.appId !== packageName || !/^\d{1,30}$/.test(fields.productId || "") || !fields.versionName || !positive(fields.versionCode) || !positive(fields.contentSize)) throw new StoreError("三星商店返回的应用信息不完整，请稍后重试。");
    return {name:fields.productName || packageName, version:fields.versionName, size:fields.contentSize, region:"US", url:downloadUrl(fields.downloadURI)};
  }

  async function cnDownload() {
    const metadata = await request("getDownloadInfo", "2298", {
      mode:"directDownload", guid:packageName, productID:"", imei:identity, extuk:identity,
      stduk:identity, predeployed:"0", unifiedPaymentYN:"Y", lkAppIncludedYN:"Y",
      betaTestYN:"N", minorYN:"N", stateCode:""
    });
    if (metadata.GUID !== packageName || !/^\d{1,30}$/.test(metadata.productID || "") || !metadata.version || !positive(metadata.versionCode) || (metadata.realContentsSize && !positive(metadata.realContentsSize))) throw new StoreError("三星商店返回的应用信息不完整，请稍后重试。");
    if (metadata.needToLogin === "1") throw new StoreError("此版本需要登录 Samsung 账户，当前无法获取下载链接。");
    if (metadata.needToLogin !== "0" || metadata.installableYN !== "Y") throw new StoreError("当前无法获取此版本的下载链接。");
    if (readPackage() && readPackage() !== packageName) throw new StoreError("应用页面已切换，请重新打开详情页。");
    title.textContent = metadata.productName || packageName;
    info.textContent = `版本：${metadata.version}`;
    status.textContent = "正在获取下载链接，请稍候。";
    const grant = await request("downloadForRestore", "2316", {
      GUID:packageName, productID:metadata.productID, imei:identity, extuk:identity, stduk:identity,
      downloadType:"new", autoUpdateYN:"N", triggeredFrom:"DETAIL_PAGE", predeployed:"0",
      deepLinkSource:"", resumeYN:"N"
    });
    if (grant.productID !== metadata.productID || (Object.hasOwn(grant, "GUID") && grant.GUID !== packageName) || (Object.hasOwn(grant, "version") && grant.version !== metadata.version) || (Object.hasOwn(grant, "versionCode") && positive(grant.versionCode) !== positive(metadata.versionCode))) throw new StoreError("下载信息与查询到的版本不一致，请重试。");
    if (!positive(grant.contentsSize) || (metadata.realContentsSize && positive(grant.contentsSize) !== positive(metadata.realContentsSize))) throw new StoreError("三星商店返回的下载信息不匹配，请重试。");
    if (!grant.downLoadURI) throw new StoreError("三星商店返回的下载信息不完整，请稍后重试。");
    return {name:metadata.productName || packageName, version:metadata.version, size:grant.contentsSize, region:"CN", url:downloadUrl(grant.downLoadURI)};
  }

  action.addEventListener("click", async event => {
    if (status.dataset.state === "pending") {
      event.preventDefault();
      return;
    }
    const currentPackage = readPackage();
    if (currentPackage && currentPackage !== packageName) {
      packageName = currentPackage;
      status.dataset.state = "idle";
      action.href = "#";
    }
    if (status.dataset.state === "ready") return;
    event.preventDefault();
    status.dataset.state = "pending";
    status.textContent = "正在查询应用信息，请稍候。";
    action.setAttribute("aria-disabled", "true");
    hint.textContent = "";
    const selectedPackage = packageName;
    try {
      let result;
      if (requestedRegion !== "CN") {
        try { result = await usDownload(); }
        catch (error) { if (requestedRegion === "US") throw error; }
      }
      if (!result) result = await cnDownload();
      if (readPackage() && readPackage() !== selectedPackage) throw new StoreError("应用页面已切换，请重新打开详情页。");
      title.textContent = result.name;
      info.textContent = `版本：${result.version} · ${result.region === "CN" ? "🇨🇳" : "🇺🇸"} · ${(Number(result.size) / 1000000).toFixed(2)} MB`;
      action.href = result.url;
      status.dataset.state = "ready";
      status.textContent = "下载链接已获取。如未开始下载，请再次点击「下载 APK」。";
      hint.textContent = "下载链接有效期约为 10 分钟，失效后请刷新页面。";
      action.click();
    } catch (error) {
      action.href = "#";
      status.dataset.state = "error";
      status.textContent = error instanceof StoreError ? error.message : "暂时无法完成请求，请稍后重试。";
    } finally { action.removeAttribute("aria-disabled"); }
  });
})();
