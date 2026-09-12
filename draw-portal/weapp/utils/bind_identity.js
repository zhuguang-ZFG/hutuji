// 二维码是一次绑定的整体，任何入口都不能把新热点名与旧 MAC/nonce 拼在一起。
const ORIGIN = "https://hutuji.donglicao.com/draw-upload/";
const PENDING_KEY = "pending_device_binding";
const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

function invalid() {
  throw new Error("二维码信息不完整，请重新扫描设备屏幕上的二维码");
}

function decode(value) {
  try { return decodeURIComponent(String(value).replace(/\+/g, " ")); }
  catch (e) { return invalid(); }
}

function canonical(value, bytes) {
  if (!new RegExp("^[A-Za-z0-9_-]{" + Math.ceil(bytes * 8 / 6) + "}$").test(value)) return false;
  const unused = (6 - bytes * 8 % 6) % 6;
  return ALPHABET.indexOf(value.slice(-1)) % Math.pow(2, unused) === 0;
}

function parseFields(fields, kind) {
  const result = { ssid: fields.s || "", code: fields.c || "", mac: "", identity: null };
  if (fields.m !== undefined) {
    const mac = String(fields.m).toLowerCase();
    if (!/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/.test(mac)) invalid();
    result.mac = mac;
  }
  if (kind === "wifi") {
    if (!result.ssid || /[\x00-\x1f\x7f]/.test(result.ssid)) invalid();
    if (encodeURIComponent(result.ssid).replace(/%[0-9A-F]{2}|./g, "x").length > 32) invalid();
  } else {
    if (!/^[0-9a-z]{6,8}$/i.test(result.code)) invalid();
    result.code = result.code.toUpperCase();
  }
  if (["v", "d", "k", "n"].some((key) => fields[key] !== undefined)) {
    if (fields.v !== "2" || !/^[0-9a-f]{32}$/.test(fields.d || "") ||
        !canonical(fields.k || "", 65) || !canonical(fields.n || "", 16) || !result.mac ||
        fields.k[0] !== "B" || ALPHABET.indexOf(fields.k[1]) >= 16) invalid();
    result.identity = {
      device_id: fields.d, public_key: fields.k, nonce: fields.n, identity_version: 2,
    };
  }
  return result;
}

function parseQr(raw, kind) {
  raw = String(raw || "").trim();
  const prefix = ORIGIN + kind + "?";
  if (raw.startsWith(prefix)) {
    const fields = Object.create(null);
    if (raw.includes("#")) invalid();
    raw.slice(prefix.length).split("&").forEach((part) => {
      const split = part.indexOf("=");
      if (split < 1) invalid();
      const key = decode(part.slice(0, split));
      if (Object.prototype.hasOwnProperty.call(fields, key)) invalid();
      fields[key] = decode(part.slice(split + 1));
    });
    return parseFields(fields, kind);
  }
  if (kind === "wifi" && !/:\/\//.test(raw)) {
    if (/^WIFI:/i.test(raw)) {
      const match = /(?:^WIFI:|;)S:((?:\\.|[^;])*)/i.exec(raw);
      if (!match) invalid();
      return parseFields({ s: match[1].replace(/\\(.)/g, "$1") }, kind);
    }
    return parseFields({ s: raw }, kind);
  }
  return invalid();
}

function fromLaunch(options, kind) {
  options = options || {};
  if (options.q) {
    const raw = String(options.q);
    return parseQr(raw.startsWith("https://") ? raw : decodeURIComponent(raw), kind);
  }
  if (!options.s && !options.c) {
    if (["m", "v", "d", "k", "n"].some((key) => options[key] !== undefined)) invalid();
    return null;
  }
  const fields = Object.create(null);
  ["s", "c", "m", "v", "d", "k", "n"].forEach((key) => {
    if (options[key] !== undefined) fields[key] = decode(options[key]);
  });
  return parseFields(fields, kind);
}

function fieldsFor(scan) {
  return Object.assign({}, scan && scan.identity || {});
}

function claimPayload(scan, force) {
  return Object.assign({ mac: scan.mac, force: !!force }, fieldsFor(scan));
}

function query(fields) {
  return Object.keys(fields).map((key) => encodeURIComponent(key) + "=" + encodeURIComponent(fields[key])).join("&");
}

function scanQuery(scan, kind) {
  const fields = kind === "wifi" ? { s: scan.ssid } : { c: scan.code };
  if (scan.mac) fields.m = scan.mac;
  if (scan.identity) Object.assign(fields, {
    v: scan.identity.identity_version, d: scan.identity.device_id,
    k: scan.identity.public_key, n: scan.identity.nonce,
  });
  return query(fields);
}

function remember(scan, kind, phase, authPending) {
  if (!scan) return;
  const old = pending(kind);
  const q = scanQuery(scan, kind);
  // 只保存扫码信息和阶段；Wi-Fi 密码始终留在当前页面内存。
  wx.setStorageSync(PENDING_KEY, {
    kind, query: q, phase: phase || "scan", authPending: !!authPending,
    savedAt: old && old.query === q ? old.savedAt : Date.now(),
  });
}

function pending(kind) {
  const record = wx.getStorageSync(PENDING_KEY);
  if (!record || (kind && record.kind !== kind)) return null;
  if (!["wifi", "bind"].includes(record.kind) || !record.savedAt || Date.now() - record.savedAt > 600000) {
    clearPending();
    return null;
  }
  return record;
}

function restore(kind) {
  const record = pending(kind);
  if (!record) return null;
  try { return { scan: parseQr(ORIGIN + kind + "?" + record.query, kind), phase: record.phase }; }
  catch (e) { clearPending(); return null; }
}

function clearPending() { wx.removeStorageSync(PENDING_KEY); }

function resumeAfterAuth() {
  const record = pending();
  if (!record || !record.authPending) return;
  wx.setStorageSync(PENDING_KEY, Object.assign({}, record, { authPending: false }));
  wx.navigateTo({
    url: "/pages/" + record.kind + "/" + record.kind,
    fail: () => wx.setStorageSync(PENDING_KEY, record),
  });
}

module.exports = { parseQr, fromLaunch, fieldsFor, claimPayload, query, remember, restore, clearPending, resumeAfterAuth };
