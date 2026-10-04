// ==UserScript==
// @name         Galaxy Store APK 下载
// @namespace    https://github.com/HotKids/apkbot
// @version      1.2.4
// @description  在 Galaxy Store 应用详情页获取 Samsung APK 下载链接。
// @match        https://galaxystore.samsung.com/detail/*
// @match        https://apps.galaxyappstore.com/detail/*
// @grant        GM_xmlhttpRequest
// @connect      vas.samsungapps.com
// @connect      cn-ms.galaxyappstore.com
// @connect      hub-odc.samsungapps.com
// @connect      us-odc.samsungapps.com
// @run-at       document-start
// @noframes
// @homepageURL  https://github.com/HotKids/apkbot
// @downloadURL  https://raw.githubusercontent.com/HotKids/apkbot/main/galaxy-store/galaxy-store.user.js
// @updateURL    https://raw.githubusercontent.com/HotKids/apkbot/main/galaxy-store/galaxy-store.user.js
// ==/UserScript==

(() => {
  "use strict";
  class StoreError extends Error {}
  class HttpStatusError extends StoreError {}
  class ServiceError extends StoreError {}
  const regions = {
    CN:{mcc:"460", mnc:"00", csc:"CHC", language:"zh_CN", endpoint:"https://cn-ms.galaxyappstore.com/ods.as"},
    US:{mcc:"310", mnc:"260", csc:"XAA", language:"en_US", endpoint:"https://us-odc.samsungapps.com/ods.as"}
  };
  const endpoints = new Map();
  const readPackage = () => {
    const name = location.pathname.match(/^\/detail\/([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)$/)?.[1];
    return name && name.length <= 255 ? name : null;
  };
  let packageName = readPackage();
  if (!["https://galaxystore.samsung.com", "https://apps.galaxyappstore.com"].includes(location.origin) || !packageName || window.top !== window.self) return;
  const checkPackage = () => {
    const current = readPackage();
    if ((current && current !== packageName) || readRegion() !== requestedRegion) throw new StoreError("应用页面已切换，请重新打开详情页。");
  };
  // Capture the package before Samsung's client can replace a restricted detail route.
  const regionFor = country => ({CN:"CN", CHN:"CN", US:"US", USA:"US"})[country?.toUpperCase()] || "AUTO";
  const readRegion = () => readPackage() ? regionFor(new URL(location.href).searchParams.get("cntyCd")) : requestedRegion;
  let requestedRegion = readRegion();
  document.getElementById("apkbot-download")?.remove();
  const panel = document.createElement("main");
  panel.id = "apkbot-download";
  panel.style.cssText = "position:fixed;z-index:2147483647;bottom:16px;right:12px;left:12px;box-sizing:border-box;width:calc(100% - 24px);max-width:480px;max-height:calc(100dvh - 32px);overflow-y:auto;margin-left:auto;padding:16px;font:16px/1.6 system-ui;color:#17202a;background:#fff;border:1px solid #ddd;border-radius:16px;overflow-wrap:anywhere;box-shadow:0 4px 20px #0002";
  const title = document.createElement("h2");
  const titleLink = document.createElement("a");
  titleLink.href = "https://galaxystore.samsung.com/detail/" + packageName;
  titleLink.textContent = packageName;
  titleLink.style.cssText = "color:inherit;text-decoration:none";
  title.append(titleLink);
  title.style.cssText = "margin:0 0 8px;font-size:18px;color:inherit";
  const status = document.createElement("p");
  status.textContent = "点击「获取」查询应用信息并下载 APK。";
  status.dataset.state = "idle";
  status.setAttribute("role", "status");
  const info = document.createElement("p");
  info.textContent = `包名：${packageName}`;
  info.style.whiteSpace = "pre-line";
  const controls = document.createElement("div");
  const refresh = document.createElement("button");
  refresh.textContent = "刷新";
  refresh.hidden = true;
  refresh.style.cssText = "margin-right:8px;padding:12px 20px;background:#eef2f6;color:#17202a;border:0;border-radius:10px;font:inherit;cursor:pointer";
  const action = document.createElement("a");
  action.textContent = "获取";
  action.href = "#";
  action.style.cssText = "display:inline-block;padding:12px 20px;background:#1769e0;color:#fff;border-radius:10px;text-decoration:none";
  const hint = document.createElement("p");
  controls.append(refresh, action);
  panel.append(title, status, info, controls, hint);
  const showPanel = () => (document.body || document.documentElement).append(panel);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", showPanel, {once:true});
  else showPanel();

  const identity = Array.from(crypto.getRandomValues(new Uint8Array(8)), b => b.toString(16).padStart(2, "0")).join("");
  const xmlEscape = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&apos;"}[c]));
  const positive = value => /^\d{1,19}$/.test(value || "") && BigInt(value) > 0n ? BigInt(value) : null;

  async function envelope(region, method, id, params) {
    const profile = regions[region];
    const time = new Date(Date.now() + 8 * 3600000).toISOString();
    const hour = time.slice(0, 13).replace(/[-T]/g, "");
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(identity + hour + "GalaxyApps"));
    const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
    const transaction = Number(time.slice(8, 10)) + hash.slice(0, 7);
    const attrs = {
      networkType:"0", version2:"0", lang:profile.language, openApiVersion:"36",
      deviceModel:"SM-S9480", deviceMakerName:"samsung", deviceMakerType:"0",
      mcc:profile.mcc, mnc:profile.mnc, csc:profile.csc, odcVersion:"4.6.11.4",
      storeFilter:"themeDeviceModel=SM-S9480_TM", supportFeature:"", version:"7.9",
      filter:"1", odcType:"01", storeMode:"0", cacheVersion:"1", systemId:Date.now(),
      sessionId:transaction + time.slice(0, 16).replace(/[-T:]/g, ""), logId:identity,
      deviceFeature:`locale=${profile.language}||abi32=armeabi-v7a:armeabi||abi64=arm64-v8a`,
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
              if (response.finalUrl !== url) throw new StoreError("三星商店返回了异常跳转，请稍后重试。");
              if (response.status !== 200) {
                if (response.status >= 400 && response.status <= 599) throw new HttpStatusError(`三星服务暂时不可用（HTTP ${response.status}），请稍后重试。`);
                throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
              }
              const bytes = new Uint8Array(response.response);
              if (!bytes.byteLength || bytes.byteLength > 2000000) throw new StoreError("三星商店返回的信息为空或超出限制，请稍后重试。");
              finish(null, new TextDecoder("utf-8", {fatal:true}).decode(bytes));
            } catch (error) { finish(error); }
          }
        });
      } catch (error) { finish(error); }
    });
  }

  function xmlDocument(text, rootName) {
    if (/<!DOCTYPE|<!ENTITY/.test(text)) throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
    const doc = new DOMParser().parseFromString(text, "application/xml");
    if (doc.querySelector("parsererror") || doc.documentElement.nodeName !== rootName) throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
    return doc.documentElement;
  }

  function collectFields(root, keys) {
    const fields = Object.create(null);
    for (const node of root.querySelectorAll("*")) {
      if (node.children.length) continue;
      const key = node.getAttribute("name") || node.nodeName;
      if (keys && !keys.includes(key)) continue;
      if (Object.hasOwn(fields, key)) throw new StoreError("三星商店返回的信息存在冲突，请稍后重试。");
      fields[key] = node.textContent.trim();
    }
    return fields;
  }

  function xmlFields(text, rootName) {
    return collectFields(xmlDocument(text, rootName));
  }

  function odsFields(text, keys, requestId) {
    const root = xmlDocument(text, "SamsungProtocol");
    const responses = [...root.children].filter(node => node.nodeName === "response");
    const structured = ["2300", "2290", "2291", "2801"].includes(requestId);
    if (responses.length > 1 || (structured && responses.length !== 1)) throw new StoreError("三星商店返回的信息存在冲突，请稍后重试。");
    const response = responses[0] || root;
    if (response.hasAttribute("id") && response.getAttribute("id") !== requestId) throw new StoreError("三星商店返回的接口信息不一致，请稍后重试。");
    const errorInfo = [...response.children].filter(node => node.nodeName === "errorInfo");
    if (errorInfo.length > 1 || (structured && errorInfo.length !== 1)) throw new StoreError("三星商店返回的信息存在冲突，请稍后重试。");
    const errors = [...(errorInfo[0] || response).children].filter(node => node.nodeName === "errorString");
    if (errors.length !== 1 || !/^-?\d{1,19}$/.test(errors[0].getAttribute("errorCode") || "")) throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
    const code = response.getAttribute("returnCode");
    if ((structured && code === null) || (code !== null && !/^-?\d{1,19}$/.test(code))) throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
    if (errors[0].getAttribute("errorCode") !== "0" || (code !== null && code !== "0")) throw new ServiceError("三星商店未批准此请求，请稍后重试。");
    if (!["", "success"].includes(errors[0].textContent.trim().toLowerCase())) throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
    const lists = [...response.children].filter(node => node.nodeName === "list");
    if (lists.length > 1 || (structured && lists.length !== 1)) throw new StoreError("三星商店返回的信息存在冲突，请稍后重试。");
    const fields = Object.create(null);
    // Data-safety sublists do not describe the selected product or its version.
    for (const node of (lists[0] || response).children) {
      const key = node.getAttribute("name") || node.nodeName;
      if (!keys.includes(key)) continue;
      if ((lists[0] && node.nodeName !== "value") || node.children.length || Object.hasOwn(fields, key)) throw new StoreError("三星商店返回的信息存在冲突，请稍后重试。");
      fields[key] = node.textContent.trim();
    }
    return fields;
  }

  async function request(region, method, id, params, keys, endpoint = regions[region].endpoint) {
    checkPackage();
    const fields = odsFields(await requestBytes(`${endpoint}?reqId=${id}&ot=01&ct=B`, "POST", await envelope(region, method, id, params)), keys, id);
    checkPackage();
    return fields;
  }

  function trustedEndpoint(value, region) {
    const expected = new URL(regions[region].endpoint);
    let url;
    try { url = new URL(value); } catch { throw new StoreError("三星商店返回的地区地址无效，请稍后重试。"); }
    if (!["http:", "https:"].includes(url.protocol) || url.hostname !== expected.hostname || url.pathname !== "/ods.as" || url.username || url.password || url.port || url.search || url.hash || /[\x00-\x20\s\\]/.test(value)) throw new StoreError("三星商店返回的地区地址无效，请稍后重试。");
    url.protocol = "https:";
    return url.href;
  }

  async function resolveEndpoint(region) {
    if (endpoints.has(region)) return endpoints.get(region);
    let endpoint = regions[region].endpoint;
    try {
      const fields = await request(region, "countrySearchEx", "2300", {accountCountry:"", accountMcc:"", latestCountryCode:regions[region].mcc, whoAmI:"odc"}, ["countryURL", "MCC", "countryCode"], region === "CN" ? endpoint : "https://hub-odc.samsungapps.com/ods.as");
      if (fields.MCC !== regions[region].mcc || fields.countryCode !== (region === "CN" ? "CHN" : "USA")) throw new StoreError("三星商店返回的地区信息不一致，请稍后重试。");
      endpoint = trustedEndpoint(fields.countryURL, region);
    } catch { checkPackage(); }
    endpoints.set(region, endpoint);
    return endpoint;
  }

  function downloadUrl(value) {
    let url;
    try { url = new URL(value); } catch { throw new StoreError("三星商店返回的下载地址无效，请稍后重试。"); }
    const host = url.hostname;
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443") || url.hash || !url.pathname.split("/").some(Boolean) || /[\x00-\x20\s\\]/.test(value) || !(host === "galaxystore.samsung.com" || ["samsungapps.com", "galaxyappstore.com"].some(suffix => host === suffix || host.endsWith("." + suffix)))) throw new StoreError("三星商店返回的下载地址无效，请稍后重试。");
    return url.href;
  }

  async function stubDownload() {
    const params = new URLSearchParams({
      appId:packageName, deviceId:"SM-S9480", mcc:"310", mnc:"260", csc:"XAA",
      sdkVer:"36", abiType:"64", extuk:identity, systemId:String(Date.now())
    });
    const fields = xmlFields(await requestBytes("https://vas.samsungapps.com/stub/stubDownload.as?" + params), "result");
    checkPackage();
    if (fields.resultCode !== "1") throw new StoreError("三星商店未返回可用结果，请稍后重试。");
    if (fields.appId !== packageName || !/^\d{1,30}$/.test(fields.productId || "") || !fields.versionName || !positive(fields.versionCode) || !positive(fields.contentSize)) throw new StoreError("三星商店返回的应用信息不完整，请稍后重试。");
    return {name:fields.productName || packageName, productId:fields.productId, version:fields.versionName, versionCode:fields.versionCode, size:fields.contentSize, region:"US", url:downloadUrl(fields.downloadURI)};
  }

  async function odsDownload(region) {
    const endpoint = await resolveEndpoint(region);
    const metadata = await request(region, "getDownloadInfo", "2298", {
      mode:"directDownload", guid:packageName, productID:"", imei:identity, extuk:identity,
      stduk:identity, predeployed:"0", unifiedPaymentYN:"Y", lkAppIncludedYN:"Y",
      betaTestYN:"N", minorYN:"N", stateCode:""
    }, ["GUID", "productID", "productName", "version", "versionCode", "realContentsSize", "needToLogin", "installableYN"], endpoint);
    if (metadata.GUID !== packageName || !/^\d{1,30}$/.test(metadata.productID || "") || !metadata.version || !positive(metadata.versionCode) || (metadata.realContentsSize && !positive(metadata.realContentsSize))) throw new StoreError("三星商店返回的应用信息不完整，请稍后重试。");
    if (metadata.needToLogin === "1") throw new StoreError("此版本需要登录 Samsung 账户，当前无法获取下载链接。");
    if (metadata.needToLogin !== "0" || metadata.installableYN !== "Y") throw new StoreError("当前无法获取此版本的下载链接。");
    checkPackage();
    if (status.dataset.phase !== "refresh") status.dataset.phase = "authorization";
    status.textContent = "正在获取下载链接，请稍候。";
    const params = {
      GUID:packageName, productID:metadata.productID, imei:identity, extuk:identity, stduk:identity,
      autoUpdateYN:"N", predeployed:"0", resumeYN:"N"
    };
    const keys = ["GUID", "productID", "version", "versionCode", "contentsSize", "downLoadURI"];
    let grant, usedRestore = false, usedMirror = false;
    try {
      // Request the full APK without assuming a locally installed version.
      grant = await request(region, "downloadEx2", "2311", {...params, dowloadType:"new", deepLinkSource:"N"}, keys, endpoint);
    } catch (error) {
      if (!(error instanceof HttpStatusError || error instanceof ServiceError)) throw error;
      checkPackage();
      usedRestore = true;
      try {
        grant = await request(region, "downloadForRestore", "2316", {...params, downloadType:"new", triggeredFrom:"DETAIL_PAGE", deepLinkSource:""}, keys, endpoint);
      } catch (restoreError) {
        if (region !== "CN" || !(restoreError instanceof HttpStatusError || restoreError instanceof ServiceError)) throw restoreError;
        checkPackage();
        // The partner endpoint is usable only when it proves the same full Samsung package.
        grant = await request(region, "downloadInfoForTencent", "2801", {
          stduk:identity, extuk:identity, GUID:packageName, tencentSource:"general",
          lastInterfaceName:"searchProductListEx2Notc"
        }, keys, endpoint);
        usedMirror = true;
      }
    }
    if (grant.productID !== metadata.productID || ((usedMirror || Object.hasOwn(grant, "GUID")) && grant.GUID !== packageName) || ((!usedRestore || usedMirror || Object.hasOwn(grant, "version")) && grant.version !== metadata.version) || ((!usedRestore || usedMirror || Object.hasOwn(grant, "versionCode")) && positive(grant.versionCode) !== positive(metadata.versionCode))) throw new StoreError("下载信息与查询到的版本不一致，请重试。");
    if ((usedMirror && !positive(metadata.realContentsSize)) || !positive(grant.contentsSize) || (metadata.realContentsSize && positive(grant.contentsSize) !== positive(metadata.realContentsSize))) throw new StoreError("三星商店返回的下载信息不匹配，请重试。");
    const uri = grant.downLoadURI;
    if (!uri) throw new StoreError("三星商店返回的下载信息不完整，请稍后重试。");
    return {name:metadata.productName || packageName, productId:metadata.productID, version:metadata.version, versionCode:metadata.versionCode, size:grant.contentsSize, region, url:downloadUrl(uri)};
  }

  async function regionDownload(region) {
    if (region === "US") {
      try { return await stubDownload(); }
      catch { checkPackage(); }
    }
    return odsDownload(region);
  }

  function storeDate(value) {
    const match = /^(\d{4});(\d{2});(\d{2});$/.exec(value || "");
    if (!match) return null;
    const [year, month, day] = match.slice(1).map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day ? `${match[1]}-${match[2]}-${match[3]}` : null;
  }

  async function releaseDetails(result) {
    const endpoint = await resolveEndpoint(result.region);
    const params = {GUID:packageName, productID:result.productId, imei:identity, stduk:identity, extuk:identity};
    const keys = ["GUID", "productID", "version", "versionCode", "realContentsSize", "productName", "lastUpdateDate"];
    const mainParams = {...params, productImgWidth:"135", productImgHeight:"135", lkAppIncludedYN:"Y", predeployed:"0", triggeredFrom:"detail"};
    const matchesMain = fields => fields.GUID === packageName && fields.productID === result.productId && fields.version === result.version && positive(fields.versionCode) === positive(result.versionCode) && positive(fields.realContentsSize) === positive(result.size);
    const before = await request(result.region, "guidProductDetailExMain", "2290", mainParams, keys, endpoint);
    if (!matchesMain(before)) return result;
    const overview = await request(result.region, "guidProductDetailExOverview", "2291", {...params, imgWidth:"1080", imgHeight:"1920", runestoneYn:"N", userAge:""}, keys, endpoint);
    if (overview.version !== result.version || positive(overview.realContentsSize) !== positive(result.size) || (overview.GUID && overview.GUID !== packageName) || (overview.productID && overview.productID !== result.productId) || (overview.versionCode && positive(overview.versionCode) !== positive(result.versionCode))) return result;
    // Overview omits the product ID and version code; bind it between matching main responses.
    const after = await request(result.region, "guidProductDetailExMain", "2290", mainParams, keys, endpoint);
    if (!matchesMain(after)) return result;
    return {...result, name:after.productName || result.name, updated:storeDate(overview.lastUpdateDate)};
  }

  function resetPackage(value) {
    packageName = value;
    status.dataset.state = "idle";
    action.textContent = "获取";
    action.href = "#";
    titleLink.textContent = value;
    titleLink.href = "https://galaxystore.samsung.com/detail/" + value;
    info.textContent = `包名：${value}`;
    hint.textContent = "";
    refresh.hidden = true;
  }

  async function loadDownload(isRefresh = false) {
    if (status.dataset.state === "pending") return;
    const currentPackage = readPackage();
    const currentRegion = readRegion();
    if ((currentPackage && currentPackage !== packageName) || currentRegion !== requestedRegion) {
      resetPackage(currentPackage || packageName);
      requestedRegion = currentRegion;
    }
    const hadLink = status.dataset.state === "ready";
    status.dataset.state = "pending";
    status.dataset.phase = isRefresh ? "refresh" : "query";
    status.textContent = isRefresh ? "正在获取下载链接，请稍候。" : "正在查询应用信息，请稍候。";
    action.setAttribute("aria-disabled", "true");
    refresh.disabled = true;
    const selectedPackage = packageName;
    const selectedRegion = requestedRegion;
    try {
      let result;
      if (requestedRegion !== "CN") {
        try { result = await regionDownload("US"); }
        catch (error) { if (requestedRegion === "US") throw error; }
      }
      if (!result) {
        checkPackage();
        result = await regionDownload("CN");
      }
      try { result = await releaseDetails(result); } catch { checkPackage(); }
      checkPackage();
      titleLink.textContent = result.name;
      info.textContent = `版本：${result.version}${result.updated ? ` · ${result.updated}` : ""}\n大小：${(Number(result.size) / 1000000).toFixed(2)} MB · ${result.region === "CN" ? "🇨🇳" : "🇺🇸"}\n包名：${selectedPackage}`;
      action.href = result.url;
      action.textContent = "下载";
      status.dataset.state = "ready";
      refresh.hidden = false;
      status.textContent = isRefresh ? "下载链接已更新。" : "已获取下载链接。若未开始下载，请点击「下载」。";
      hint.textContent = "下载链接有效期约为 10 分钟，失效后请点击「刷新」。";
      if (!isRefresh) action.click();
    } catch (error) {
      const latestPackage = readPackage();
      const samePackage = (!latestPackage || latestPackage === selectedPackage) && readRegion() === selectedRegion;
      if (!samePackage) {
        resetPackage(latestPackage || packageName);
        requestedRegion = readRegion();
      }
      if (!hadLink || !samePackage) {
        action.href = "#";
        action.textContent = "获取";
      }
      status.dataset.state = hadLink && samePackage ? "ready" : "error";
      const failure = isRefresh ? "下载链接更新失败" : status.dataset.phase === "query" ? "应用信息查询失败" : "下载链接获取失败";
      status.textContent = `${failure}：${error instanceof StoreError ? error.message : "暂时无法完成请求，请稍后重试。"}`;
    } finally {
      action.removeAttribute("aria-disabled");
      refresh.disabled = false;
    }
  }

  action.addEventListener("click", event => {
    if (status.dataset.state === "ready" && (!readPackage() || readPackage() === packageName) && readRegion() === requestedRegion) return;
    event.preventDefault();
    loadDownload();
  });
  refresh.addEventListener("click", () => loadDownload(true));
})();
