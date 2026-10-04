void (async () => {
  class StoreError extends Error {}
  const origin = "https://cn-ms.galaxyappstore.com";
  const packageName = "com.samsung.android.app.sreminder";
  if (location.origin !== origin) {
    alert("请先打开三星页面，再次运行此书签。页面空白属正常现象。");
    location.assign(origin + "/");
    return;
  }
  document.getElementById("assistant-download")?.remove();
  const panel = document.createElement("main");
  panel.id = "assistant-download";
  panel.style.cssText = "font:16px/1.6 system-ui;margin:24px auto;padding:20px;max-width:480px;color:#17202a;background:#fff;border:1px solid #ddd;border-radius:16px";
  const title = document.createElement("h2");
  title.textContent = "三星生活助手";
  const status = document.createElement("p");
  status.textContent = "正在查询应用信息，请稍候。";
  status.dataset.state = "pending";
  const info = document.createElement("p");
  const action = document.createElement("a");
  action.textContent = "下载";
  action.hidden = true;
  action.style.cssText = "padding:12px 20px;background:#1769e0;color:#fff;border-radius:10px;text-decoration:none";
  const hint = document.createElement("p");
  panel.append(title, status, info, action, hint);
  (document.body || document.documentElement).append(panel);

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

  async function request(method, id, params) {
    const body = await envelope(method, id, params);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 40000);
    try {
      const response = await fetch(`${origin}/ods.as?reqId=${id}&ot=01&ct=B`, {
        method:"POST", redirect:"error", signal:controller.signal,
        headers:{"Content-Type":"text/plain; charset=UTF-8", "Accept":"image/webp"}, body
      });
      if (!response.ok) throw new StoreError(`三星服务暂时不可用（HTTP ${response.status}），请稍后重试。`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8", {fatal:true});
      let text = "", size = 0;
      try {
        while (true) {
          const {value, done} = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 2000000) {
            await reader.cancel();
            throw new StoreError("三星商店返回的信息超出限制，请稍后重试。");
          }
          text += decoder.decode(value, {stream:true});
        }
        text += decoder.decode();
      } finally {
        reader.releaseLock();
      }
      if (/<!DOCTYPE|<!ENTITY/.test(text)) throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
      const doc = new DOMParser().parseFromString(text, "application/xml");
      if (doc.querySelector("parsererror") || doc.documentElement.nodeName !== "SamsungProtocol") throw new StoreError("三星商店返回的信息格式不正确，请稍后重试。");
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
      if (fields.errorCode !== "0" || !["", "success"].includes((fields.errorString || "").toLowerCase())) throw new StoreError("三星商店未批准此请求，请稍后重试。");
      return fields;
    } finally {
      clearTimeout(timer);
    }
  }

  try {
    const metadata = await request("getDownloadInfo", "2298", {
      mode:"directDownload", guid:packageName, productID:"", imei:identity, extuk:identity,
      stduk:identity, predeployed:"0", unifiedPaymentYN:"Y", lkAppIncludedYN:"Y",
      betaTestYN:"N", minorYN:"N", stateCode:""
    });
    if (metadata.GUID !== packageName || !/^\d{1,30}$/.test(metadata.productID || "") || !metadata.version || !positive(metadata.versionCode) || (metadata.realContentsSize && !positive(metadata.realContentsSize))) throw new StoreError("三星商店返回的应用信息不完整，请稍后重试。");
    if (metadata.needToLogin === "1") throw new StoreError("此版本需要登录三星账户，当前书签无法获取下载链接。");
    if (metadata.needToLogin !== "0" || metadata.installableYN !== "Y") throw new StoreError("当前无法获取此版本的下载链接。");
    title.textContent = metadata.productName || "三星生活助手";
    info.textContent = `版本：${metadata.version}`;
    status.textContent = "正在获取下载链接，请稍候。";
    const grant = await request("downloadForRestore", "2316", {
      GUID:packageName, productID:metadata.productID, imei:identity, extuk:identity, stduk:identity,
      downloadType:"new", autoUpdateYN:"N", triggeredFrom:"DETAIL_PAGE", predeployed:"0",
      deepLinkSource:"", resumeYN:"N"
    });
    if (grant.productID !== metadata.productID || (Object.hasOwn(grant, "GUID") && grant.GUID !== packageName) || (Object.hasOwn(grant, "version") && grant.version !== metadata.version) || (Object.hasOwn(grant, "versionCode") && positive(grant.versionCode) !== positive(metadata.versionCode))) throw new StoreError("下载信息与查询到的版本不一致，请再次运行此书签。");
    if (!positive(grant.contentsSize) || (metadata.realContentsSize && positive(grant.contentsSize) !== positive(metadata.realContentsSize))) throw new StoreError("三星商店返回的下载信息不匹配，请再次运行此书签。");
    if (!grant.downLoadURI) throw new StoreError("三星商店返回的下载信息不完整，请稍后重试。");
    const url = new URL(grant.downLoadURI);
    const host = url.hostname;
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443") || url.hash || /[\x00-\x20\s\\]/.test(grant.downLoadURI) || !(host === "galaxystore.samsung.com" || ["samsungapps.com", "galaxyappstore.com"].some(suffix => host === suffix || host.endsWith("." + suffix)))) throw new StoreError("三星商店返回的下载地址无效，请稍后重试。");
    info.textContent += ` · ${(Number(grant.contentsSize) / 1000000).toFixed(2)} MB`;
    action.href = url.href;
    action.hidden = false;
    action.style.display = "inline-block";
    status.textContent = "下载链接已获取。如未开始下载，请点击「下载」。";
    status.dataset.state = "ready";
    hint.textContent = "下载链接有效期约为 10 分钟，失效后请再次运行此书签。";
    action.click();
  } catch (error) {
    status.dataset.state = "error";
    status.textContent = error.name === "AbortError" ? "请求超时，请再次运行此书签。" : error instanceof StoreError ? error.message : "暂时无法完成请求，请稍后重试。";
  }
})();
