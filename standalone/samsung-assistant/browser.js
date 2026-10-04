void (async () => {
  const origin = "https://cn-ms.galaxyappstore.com";
  const packageName = "com.samsung.android.app.sreminder";
  if (location.origin !== origin) {
    alert("Open the Samsung page, then run this bookmark again. A blank page is normal.");
    location.assign(origin + "/");
    return;
  }
  document.getElementById("assistant-download")?.remove();
  const panel = document.createElement("main");
  panel.id = "assistant-download";
  panel.style.cssText = "font:16px/1.6 system-ui;margin:24px auto;padding:20px;max-width:480px;color:#17202a;background:#fff;border:1px solid #ddd;border-radius:16px";
  const title = document.createElement("h2");
  title.textContent = "Samsung Assistant";
  const status = document.createElement("p");
  status.textContent = "Querying the latest version…";
  status.dataset.state = "pending";
  const info = document.createElement("p");
  const action = document.createElement("a");
  action.textContent = "Download APK";
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
      if (!response.ok) throw new Error(`Samsung returned HTTP ${response.status}.`);
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
            throw new Error("Samsung returned too much data.");
          }
          text += decoder.decode(value, {stream:true});
        }
        text += decoder.decode();
      } finally {
        reader.releaseLock();
      }
      if (/<!DOCTYPE|<!ENTITY/.test(text)) throw new Error("Samsung returned invalid data.");
      const doc = new DOMParser().parseFromString(text, "application/xml");
      if (doc.querySelector("parsererror") || doc.documentElement.nodeName !== "SamsungProtocol") throw new Error("Samsung returned invalid data.");
      const fields = Object.create(null);
      for (const node of doc.querySelectorAll("*")) {
        if (node.children.length) continue;
        const key = node.getAttribute("name") || node.nodeName;
        if (Object.hasOwn(fields, key)) throw new Error("Samsung returned conflicting data.");
        fields[key] = node.textContent.trim();
        if (key === "errorString" && node.hasAttribute("errorCode")) {
          if (Object.hasOwn(fields, "errorCode")) throw new Error("Samsung returned conflicting data.");
          fields.errorCode = node.getAttribute("errorCode");
        }
      }
      if (fields.errorCode !== "0" || !["", "success"].includes((fields.errorString || "").toLowerCase())) throw new Error("Samsung did not approve this request. Please try again later.");
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
    if (metadata.GUID !== packageName || !/^\d{1,30}$/.test(metadata.productID || "") || !metadata.version || !positive(metadata.versionCode) || (metadata.realContentsSize && !positive(metadata.realContentsSize))) throw new Error("Samsung returned incomplete app information.");
    if (metadata.needToLogin === "1") throw new Error("This version requires a Samsung account. This script cannot authorize it.");
    if (metadata.needToLogin !== "0" || metadata.installableYN !== "Y") throw new Error("Samsung has not made this version installable for this request.");
    title.textContent = metadata.productName || "Samsung Assistant";
    info.textContent = `Version: ${metadata.version}`;
    status.textContent = "Getting a fresh download link…";
    const grant = await request("downloadForRestore", "2316", {
      GUID:packageName, productID:metadata.productID, imei:identity, extuk:identity, stduk:identity,
      downloadType:"new", autoUpdateYN:"N", triggeredFrom:"DETAIL_PAGE", predeployed:"0",
      deepLinkSource:"", resumeYN:"N"
    });
    if (grant.productID !== metadata.productID || (Object.hasOwn(grant, "GUID") && grant.GUID !== packageName) || (Object.hasOwn(grant, "version") && grant.version !== metadata.version) || (Object.hasOwn(grant, "versionCode") && positive(grant.versionCode) !== positive(metadata.versionCode))) throw new Error("The store version changed. Run the bookmark again.");
    if (!positive(grant.contentsSize) || (metadata.realContentsSize && positive(grant.contentsSize) !== positive(metadata.realContentsSize))) throw new Error("Samsung returned mismatched download information.");
    if (!grant.downLoadURI) throw new Error("Samsung returned incomplete download information.");
    const url = new URL(grant.downLoadURI);
    const host = url.hostname;
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443") || url.hash || /[\x00-\x20\s\\]/.test(grant.downLoadURI) || !(host === "galaxystore.samsung.com" || ["samsungapps.com", "galaxyappstore.com"].some(suffix => host === suffix || host.endsWith("." + suffix)))) throw new Error("Samsung returned an invalid download address.");
    info.textContent += ` · ${(Number(grant.contentsSize) / 1000000).toFixed(2)} MB`;
    action.href = url.href;
    action.hidden = false;
    action.style.display = "inline-block";
    status.textContent = "Ready. Tap Download APK to save the file.";
    status.dataset.state = "ready";
    hint.textContent = "The link usually lasts about 10 minutes. If it expires, run this bookmark again.";
  } catch (error) {
    status.dataset.state = "error";
    status.textContent = error.name === "AbortError" ? "The request timed out. Run the bookmark again." : error instanceof TypeError ? "The Samsung request could not be completed. Check your connection and try again." : error.message;
  }
})();
