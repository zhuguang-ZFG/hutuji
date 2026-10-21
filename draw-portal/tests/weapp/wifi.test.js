// 使用真实扫码/HTTP/页面代码注入乱序与断连，不访问微信或设备。
const assert = require("node:assert/strict");
const test = require("node:test");
const crypto = require("node:crypto");
const { createWeapp, createClock, flush } = require("./harness");

const BASE = "https://hutuji.donglicao.com/draw-upload/";
const MAC = "aa:bb:cc:dd:ee:01";
const key = crypto.createECDH("prime256v1");
key.setPrivateKey(Buffer.from("1".padStart(64, "0"), "hex"));
const publicKey = key.getPublicKey();
const fields = {
  s: "小派-01", m: MAC, v: "2",
  d: crypto.createHash("sha256").update(publicKey).digest("hex").slice(0, 32),
  k: publicKey.toString("base64url"), n: Buffer.alloc(16, 7).toString("base64url"),
};
const query = (data) => Object.entries(data).map(([k, v]) => encodeURIComponent(k) + "=" + encodeURIComponent(v)).join("&");
const qr = (kind = "wifi", data = fields) => BASE + kind + "?" + query(data);
const bindFields = Object.assign({}, fields, { c: "ABC123" });
delete bindFields.s;
const plain = (value) => JSON.parse(JSON.stringify(value));

function setup(options = {}) {
  const clock = createClock();
  const h = createWeapp(Object.assign({ clock }, options));
  h.clock = clock;
  h.starts = [];
  h.connections = [];
  h.wx.startWifi = (options) => h.starts.push(options);
  h.wx.connectWifi = (options) => h.connections.push(options);
  h.wx.getConnectedWifi = (options) => options.success({ wifi: { SSID: "家庭 Wi-Fi" } });
  return h;
}

async function openPage(kind = "wifi", launch = { q: qr() }, options = {}) {
  const h = setup(options);
  h.app.ensureSession = () => Promise.resolve({ phone_bound: true });
  h.load(`pages/${kind}/${kind}.js`);
  h.page.onLoad(launch);
  await flush();
  const auth = h.requests.find((request) => request.url.endsWith("/api/auth/me"));
  assert.ok(auth);
  auth.success({ statusCode: 200, data: { phone_bound: true } });
  await flush();
  return h;
}

function installTcp(h) {
  const sockets = [];
  h.wx.createTCPSocket = () => {
    const handlers = {};
    const socket = {
      handlers, writes: [], closed: 0,
      onConnect: (fn) => { handlers.connect = fn; },
      onMessage: (fn) => { handlers.message = fn; },
      onClose: (fn) => { handlers.close = fn; },
      onError: (fn) => { handlers.error = fn; },
      connect: (options) => { socket.address = options; },
      write: (buffer) => socket.writes.push(Buffer.from(buffer)),
      close: () => { socket.closed++; if (handlers.close) handlers.close(); },
      receive: (buffer) => handlers.message({ message: Uint8Array.from(buffer).buffer }),
    };
    sockets.push(socket);
    return socket;
  };
  return sockets;
}

function http(data, headers = {}) {
  const body = Buffer.from(JSON.stringify(data));
  const header = Object.assign({ "Content-Length": body.length }, headers);
  return Buffer.concat([Buffer.from("HTTP/1.1 200 OK\r\n" +
    Object.entries(header).map(([k, v]) => `${k}: ${v}\r\n`).join("") + "\r\n"), body]);
}

for (const mode of ["plain-q", "encoded-q", "fields", "reverse"]) {
  test(`扫码入口 ${mode} 保留完整身份，参数顺序不影响结果`, () => {
    const h = setup();
    const helper = h.load("utils/bind_identity.js");
    const reversed = Object.fromEntries(Object.entries(fields).reverse());
    const options = mode === "fields" ? Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, encodeURIComponent(v)]))
      : { q: mode === "encoded-q" ? encodeURIComponent(qr()) : qr("wifi", mode === "reverse" ? reversed : fields) };
    const scan = helper.fromLaunch(options, "wifi");
    assert.equal(scan.ssid, fields.s);
    assert.deepEqual(plain(helper.claimPayload(scan, false)), {
      mac: MAC, force: false, device_id: fields.d, public_key: fields.k,
      nonce: fields.n, identity_version: 2,
    });
    assert.equal(helper.parseQr(qr("bind", bindFields), "bind").code, "ABC123");
  });
}

const invalidQrs = [
  qr() + "&n=" + fields.n,
  qr("wifi", Object.fromEntries(Object.entries(fields).filter(([k]) => k !== "k"))),
  qr("wifi", Object.assign({}, fields, { v: "3" })),
  qr("wifi", Object.assign({}, fields, { n: "A".repeat(21) + "B" })),
  qr("wifi", Object.assign({}, fields, { k: "A".repeat(87) })),
  qr("wifi", Object.assign({}, fields, { m: "" })),
  qr("wifi", Object.assign({}, fields, { s: "字".repeat(11) })),
  qr() + "#fragment",
  qr().replace("hutuji.donglicao.com", "other.example.test"),
];
for (const [index, value] of invalidQrs.entries()) {
  test(`重复、部分或非法二维码拒绝（${index + 1}）`, () => {
    const helper = setup().load("utils/bind_identity.js");
    assert.throws(() => helper.parseQr(value, "wifi"));
  });
}

test("旧 WIFI 二维码转义 SSID 可读，不能遗留身份字段", () => {
  const helper = setup().load("utils/bind_identity.js");
  const scan = helper.parseQr("WIFI:T:nopass;S:小派\\;旧款;;", "wifi");
  assert.equal(scan.ssid, "小派;旧款");
  assert.equal(scan.mac, "");
  assert.equal(scan.identity, null);
  assert.throws(() => helper.fromLaunch({ n: fields.n }, "wifi"));
});

test("授权跳转恢复扫码和等待阶段，但不持久化 Wi-Fi 密码", async () => {
  const h = await openPage();
  h.page.onPwdInput({ detail: { value: "只存在内存的密码" } });
  const helper = h.load("utils/bind_identity.js");
  helper.remember(h.page._scan, "wifi", "waiting", true);
  helper.resumeAfterAuth();
  assert.equal(h.navigations.at(-1).url, "/pages/wifi/wifi");
  assert.equal(JSON.stringify([...h.storage]).includes("只存在内存的密码"), false);
  h.page.onUnload();
  assert.equal(h.page.data.homePassword, "");
  const restored = await openPage("wifi", {}, { storage: h.storage });
  assert.equal(restored.page._scan.identity.nonce, fields.n);
  const check = restored.requests.find((request) => request.url.includes("/api/user/bind/claim/status?"));
  assert.ok(check);
  assert(check.url.includes("device_id=" + fields.d));
  assert.equal(restored.requests.filter((request) => request.method === "POST").length, 0);
  check.success({ statusCode: 200, data: { status: "expired" } });
  await flush();
  restored.page.onRetryBind();
  const claim = restored.requests.at(-1);
  assert.equal(claim.data.device_id, fields.d);
  assert.equal(restored.page.data.homePassword, "");
});

test("重复保存同一扫码不延长过期窗口", () => {
  const h = setup();
  const helper = h.load("utils/bind_identity.js");
  const scan = helper.parseQr(qr(), "wifi");
  helper.remember(scan, "wifi", "scan", true);
  h.clock.advance(300000);
  helper.remember(scan, "wifi", "waiting", false);
  h.clock.advance(300001);
  assert.equal(helper.restore("wifi"), null);
});

for (const operation of ["legacy-scan", "manual-input"]) {
  test(`更换目标 ${operation} 清除旧 MAC 和 nonce`, async () => {
    const h = await openPage();
    if (operation === "legacy-scan") {
      h.page.onScanAp();
      h.scans.at(-1).success({ result: qr("wifi", { s: "old-device" }) });
      assert.equal(h.page._scan.identity, null);
    } else {
      h.page.onManualAp();
      h.page.onApInput({ detail: { value: "manual-device" } });
      assert.equal(h.page._scan, null);
    }
    assert.equal(h.page.data.deviceMac, "");
    assert.equal(JSON.stringify([...h.storage]).includes(fields.n), false);
  });
}

test("中文与 emoji 请求按 UTF-8 计 Content-Length，分片响应跨字符仍完整", () => {
  const h = setup();
  const sockets = installTcp(h);
  const transport = h.load("utils/wifi_transport.js");
  const results = [];
  const body = { ssid: "家里的 Wi-Fi 🐱", password: "中文密码🔐" };
  transport.request({ method: "POST", path: "/submit", body, done: (res) => results.push(res) });
  const socket = sockets[0];
  socket.handlers.connect();
  const request = socket.writes[0];
  const split = request.indexOf("\r\n\r\n") + 4;
  const length = Number(/Content-Length: (\d+)/.exec(request.toString())[1]);
  assert.equal(length, request.length - split);
  assert.equal(request.subarray(split).toString(), JSON.stringify(body));
  const response = http({ success: true, message: "已接收 🐱" });
  for (let i = 0; i < response.length; i++) socket.receive(response.subarray(i, i + 1));
  assert.equal(results.length, 1);
  assert.equal(results[0].data.message, "已接收 🐱");
  assert.equal(socket.closed, 1);
  assert.equal(h.clock.pending.size, 0);
});

test("chunked 分片、扩展与尾头在完整终帧后才解 JSON", () => {
  const h = setup();
  const transport = h.load("utils/wifi_transport.js");
  const body = Buffer.from(JSON.stringify({ message: "网络就绪🌟" }));
  const parts = [body.subarray(0, 15), body.subarray(15)];
  const chunks = parts.map((part) => Buffer.concat([Buffer.from(part.length.toString(16) + ";x=y\r\n"), part, Buffer.from("\r\n")]));
  const frame = Buffer.concat([Buffer.from("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"), ...chunks,
    Buffer.from("0\r\nX-Test: yes\r\n\r\n")]);
  assert.equal(transport.parseResponse(frame.subarray(0, frame.length - 1), false), null);
  assert.equal(transport.parseResponse(frame, false).data.message, "网络就绪🌟");
});

for (const frame of [
  "HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
  "HTTP/1.1 200 OK\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
  "HTTP/1.1 200 OK\r\nContent-Length: 999999\r\n\r\n",
  "HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n{}",
  "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n",
]) {
  test(`HTTP 歧义、超限或截断帧拒绝（${frame.length} 字节）`, () => {
    const transport = setup().load("utils/wifi_transport.js");
    assert.throws(() => transport.parseResponse(Buffer.from(frame), true));
  });
}

for (const ending of ["cancel", "timeout", "oversize", "close"]) {
  test(`TCP ${ending} 只结算一次且释放定时器`, () => {
    const h = setup();
    const sockets = installTcp(h);
    const results = [];
    const cancel = h.load("utils/wifi_transport.js").request({ method: "GET", path: "/scan", done: (res) => results.push(res) });
    const socket = sockets[0];
    socket.handlers.connect();
    if (ending === "cancel") cancel();
    else if (ending === "timeout") h.clock.advance(15001);
    else if (ending === "oversize") socket.receive(Buffer.alloc(65537));
    else socket.handlers.close();
    socket.handlers.error();
    socket.handlers.close();
    socket.receive(http({ success: true }));
    assert.equal(results.length, ending === "cancel" ? 0 : 1);
    assert.equal(results[0] && results[0].data, undefined);
    assert.equal(h.clock.pending.size, 0);
  });
}

test("AP 发送后断连只进入等待，确认绑定后才成功并清密码", async () => {
  const h = await openPage();
  const sockets = installTcp(h);
  h.page.setData({ homeSsid: "家庭 Wi-Fi", homePassword: "memory-only" });
  h.page.submitCreds(h.page._generation, 0);
  sockets[0].handlers.connect();
  sockets[0].handlers.close();
  assert.equal(h.page.data.bindState, "waiting");
  assert.equal(h.toasts.some((toast) => toast.title === "绑定成功"), false);
  h.clock.advance(2500);
  assert.equal(h.connections.at(-1).SSID, "家庭 Wi-Fi");
  h.connections.at(-1).success();
  const claim = h.requests.at(-1);
  assert.equal(claim.data.nonce, fields.n);
  claim.success({ statusCode: 200, data: { status: "claimed" } });
  await flush();
  assert.equal(h.page.data.bindState, "waiting");
  h.clock.advance(5000);
  const status = h.requests.at(-1);
  assert.ok(status.url.includes("nonce=" + fields.n));
  status.success({ statusCode: 200, data: { status: "bound" } });
  await flush();
  assert.equal(h.page.data.bindState, "bound");
  assert.equal(h.page.data.homePassword, "");
  assert.equal(h.clock.pending.size, 0);
});

for (const end of ["rescan", "unload", "logout"]) {
  test(`Wi-Fi 页面 ${end} 作废旧成功回包与重试定时器`, async () => {
    const h = await openPage();
    h.page.autoBind();
    const old = h.requests.at(-1);
    let timerRan = false;
    h.page._later(h.page._generation, () => { timerRan = true; }, 1000);
    if (end === "rescan") h.page.onScanAp();
    else if (end === "unload") h.page.onUnload();
    else h.app.logout();
    old.success({ statusCode: 200, data: { status: "bound" } });
    await flush();
    h.clock.advance(1001);
    assert.notEqual(h.page.data.bindState, "bound");
    assert.equal(timerRan, false);
    assert.equal(h.toasts.some((toast) => toast.title === "绑定成功"), false);
  });
}

for (const kind of ["wifi", "bind"]) {
  test(`${kind} 身份不匹配不弹强制换绑确认`, async () => {
    const h = await openPage(kind, { q: kind === "wifi" ? qr() : qr("bind", bindFields) });
    if (kind === "wifi") h.page.autoBind();
    h.requests.at(-1).success({ statusCode: 409, data: { detail: { code: "identity_mismatch", message: "请重新扫码" } } });
    await flush();
    assert.equal(h.modals.some((modal) => modal.confirmText === "换绑"), false);
    assert.equal(h.clock.pending.size, 0);
  });
}

test("手动绑定输入作废扫码身份，旧设备列表回包不能冒充本次成功", async () => {
  const h = await openPage("bind", {});
  const devices = h.requests.at(-1);
  h.page.onCodeInput({ detail: { value: "def456" } });
  devices.success({ statusCode: 200, data: { devices: [{ active: true, device_mac: MAC }] } });
  await flush();
  assert.equal(h.page.data.step, "input");
  h.page.onSubmit();
  assert.deepEqual(plain(h.requests.at(-1).data), { bind_code: "DEF456", force: false });
});

for (const kind of ["wifi", "bind"]) {
  test(`${kind} 真实静默登录换代后仍能继续扫码流程`, async () => {
    const h = setup();
    h.load(`pages/${kind}/${kind}.js`);
    h.page.onLoad({ q: kind === "wifi" ? qr() : qr("bind", bindFields) });
    h.logins[0].success({ code: "login-code" });
    h.requests.at(-1).success({ statusCode: 200, data: { session: "new-session" } });
    await flush();
    h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
    await flush();
    const gate = h.requests.at(-1);
    assert.ok(gate.url.endsWith("/api/auth/me"));
    gate.success({ statusCode: 200, data: { phone_bound: true } });
    await flush();
    assert.equal(h.page.data.authReady || h.page._authReady, true);
    assert.equal(h.page._sessionGeneration, h.app._sessionGeneration);
    if (kind === "bind") assert.equal(h.requests.at(-1).data.nonce, fields.n);
  });

  test(`${kind} 授权中重扫会启动新代次，旧请求不能清掉新任务`, async () => {
    const h = setup();
    h.app.ensureSession = () => Promise.resolve({ phone_bound: true });
    h.load(`pages/${kind}/${kind}.js`);
    h.page.onLoad({ q: kind === "wifi" ? qr() : qr("bind", bindFields) });
    await flush();
    const old = h.requests.at(-1);
    if (kind === "wifi") h.page.onScanAp();
    else h.page.onScanBind();
    const updated = Object.assign({}, kind === "wifi" ? fields : bindFields, {
      n: Buffer.alloc(16, 8).toString("base64url"),
    });
    h.scans.at(-1).success({ result: qr(kind, updated) });
    await flush();
    const fresh = h.requests.at(-1);
    assert.notEqual(fresh, old);
    old.success({ statusCode: 200, data: { phone_bound: true, nickname: "过时" } });
    await flush();
    assert.equal(h.page._authPending, h.page._generation);
    assert.equal(h.app.globalData.user, null);
    fresh.success({ statusCode: 200, data: { phone_bound: true, nickname: "当前" } });
    await flush();
    assert.equal(h.page.data.authReady || h.page._authReady, true);
    assert.equal(h.page._scan.identity.nonce, updated.n);
    assert.equal(h.app.globalData.user.nickname, "当前");
  });

  test(`${kind} 授权中卸载不再跳转登录页`, async () => {
    const h = setup();
    let finishLogin;
    h.app.ensureSession = () => new Promise((resolve) => { finishLogin = resolve; });
    h.load(`pages/${kind}/${kind}.js`);
    h.page.onLoad({ q: kind === "wifi" ? qr() : qr("bind", bindFields) });
    h.page.onUnload();
    finishLogin(null);
    await flush();
    assert.equal(h.tabs.length, 0);
    assert.equal(h.requests.length, 0);
  });

  test(`${kind} 授权中退出登录不复活用户资料或开始绑定`, async () => {
    const h = setup();
    h.app.ensureSession = () => Promise.resolve({ phone_bound: true });
    h.load(`pages/${kind}/${kind}.js`);
    h.page.onLoad({ q: kind === "wifi" ? qr() : qr("bind", bindFields) });
    await flush();
    const auth = h.requests.at(-1);
    h.app.logout();
    auth.success({ statusCode: 200, data: { phone_bound: true, nickname: "旧账号" } });
    await flush();
    assert.equal(h.app.globalData.user, null);
    assert.equal(h.app.globalData.phoneBound, false);
    assert.equal(h.requests.filter((req) => req.url.includes("/api/user/bind/")).length, 0);
    assert.equal(h.page.data.authReady || h.page._authReady || false, false);
  });
}
