// 配网 HTTP 通过原生 TCP 发出；Content-Length 按 UTF-8 字节计算，接收端先拼字节再解码。
const MAX_RESPONSE = 65536;

function utf8(text) {
  const bytes = [];
  for (let i = 0; i < text.length; ++i) {
    let code = text.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length &&
        text.charCodeAt(i + 1) >= 0xdc00 && text.charCodeAt(i + 1) <= 0xdfff) {
      code = 0x10000 + ((code - 0xd800) << 10) + text.charCodeAt(++i) - 0xdc00;
    } else if (code >= 0xd800 && code <= 0xdfff) code = 0xfffd;
    if (code < 0x80) bytes.push(code);
    else if (code < 0x800) bytes.push(0xc0 | code >> 6, 0x80 | code & 63);
    else if (code < 0x10000) bytes.push(0xe0 | code >> 12, 0x80 | code >> 6 & 63, 0x80 | code & 63);
    else bytes.push(0xf0 | code >> 18, 0x80 | code >> 12 & 63, 0x80 | code >> 6 & 63, 0x80 | code & 63);
  }
  return new Uint8Array(bytes);
}

function decode(bytes) {
  return decodeURIComponent(Array.from(bytes, (b) => "%" + b.toString(16).padStart(2, "0")).join(""));
}

function parseResponse(bytes, closed) {
  // 拉丁字节串仅用于切 HTTP 帧，JSON 在完整 UTF-8 边界上解码。
  let raw = "";
  for (let i = 0; i < bytes.length; ++i) raw += String.fromCharCode(bytes[i]);
  const split = raw.indexOf("\r\n\r\n");
  if (split < 0) {
    if (closed || bytes.length > 8192) throw new Error("bad headers");
    return null;
  }
  const headers = raw.slice(0, split).split("\r\n");
  const status = /^HTTP\/1\.[01] (\d{3})(?: |$)/.exec(headers.shift());
  if (!status) throw new Error("bad status");
  let length = null, chunked = false;
  headers.forEach((line) => {
    if (/^content-length:/i.test(line)) {
      const value = line.slice(line.indexOf(":") + 1).trim();
      if (length !== null || !/^\d+$/.test(value)) throw new Error("bad length");
      length = Number(value);
    }
    if (/^transfer-encoding:/i.test(line)) {
      if (!/^transfer-encoding:\s*chunked\s*$/i.test(line)) throw new Error("bad encoding");
      chunked = true;
    }
  });
  let body = bytes.slice(split + 4);
  if (chunked) {
    if (length !== null) throw new Error("ambiguous length");
    const chunks = [];
    let offset = split + 4, done = false;
    while (!done) {
      const end = raw.indexOf("\r\n", offset);
      if (end < 0) break;
      const sizeText = raw.slice(offset, end).split(";")[0];
      if (!/^[0-9a-f]+$/i.test(sizeText)) throw new Error("bad chunk");
      const size = parseInt(sizeText, 16);
      if (size > MAX_RESPONSE) throw new Error("large chunk");
      const start = end + 2;
      if (size === 0) {
        done = raw.slice(start, start + 2) === "\r\n" || raw.indexOf("\r\n\r\n", start) >= 0;
        break;
      }
      if (raw.length < start + size + 2) break;
      if (raw.slice(start + size, start + size + 2) !== "\r\n") throw new Error("bad chunk end");
      for (let i = start; i < start + size; ++i) chunks.push(bytes[i]);
      offset = start + size + 2;
    }
    if (!done) {
      if (closed) throw new Error("short chunk");
      return null;
    }
    body = new Uint8Array(chunks);
  } else if (length !== null) {
    if (length > MAX_RESPONSE) throw new Error("large response");
    if (body.length < length) {
      if (closed) throw new Error("short response");
      return null;
    }
    body = body.slice(0, length);
  } else if (!closed) return null;
  return { status: Number(status[1]), data: JSON.parse(decode(body)) };
}

function request({ method, path, body, timeout, done }) {
  const payload = body === undefined ? "" : JSON.stringify(body);
  const header = method + " " + path + " HTTP/1.1\r\nHost: 192.168.4.1\r\nConnection: close\r\n" +
    (body === undefined ? "" : "Content-Type: application/json\r\nContent-Length: " + utf8(payload).length + "\r\n") + "\r\n";
  let tcp, timer, finished = false, sent = false, bytes = new Uint8Array(0);
  const finish = (result, cancel) => {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    if (tcp) { try { tcp.close(); } catch (e) {} }
    bytes = new Uint8Array(0);
    if (!cancel) done(Object.assign({ sent }, result || {}));
  };
  const read = (closed) => {
    try {
      const result = parseResponse(bytes, closed);
      if (result) finish(result);
    } catch (e) { finish(); }
  };
  timer = setTimeout(() => finish(), timeout || 15000);
  try {
    tcp = wx.createTCPSocket();
    tcp.onConnect(() => {
      if (finished) return;
      try { sent = true; tcp.write(utf8(header + payload).buffer); }
      catch (e) { finish(); }
    });
    tcp.onMessage((res) => {
      if (finished) return;
      const part = typeof res.message === "string" ? utf8(res.message) : new Uint8Array(res.message || []);
      if (bytes.length + part.length > MAX_RESPONSE) { finish(); return; }
      const combined = new Uint8Array(bytes.length + part.length);
      combined.set(bytes); combined.set(part, bytes.length); bytes = combined;
      read(false);
    });
    tcp.onClose(() => { if (!finished) { read(true); finish(); } });
    tcp.onError(() => finish());
    tcp.connect({ address: "192.168.4.1", port: 80, timeout: 8 });
  } catch (e) { finish(); }
  return () => finish(null, true);
}

module.exports = { utf8, parseResponse, request };
