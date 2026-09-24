const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

async function finishLogin(h, login, token) {
  login.success({ code: "test-code" });
  h.requests.at(-1).success({ statusCode: 200, data: { session: token } });
  await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { id: token, phone_bound: true } });
  await flush();
}

test("并发静默登录共用请求并发布完整会话", async () => {
  const h = createWeapp();
  const first = h.app.ensureSession();
  const second = h.app.ensureSession();
  assert.equal(h.logins.length, 1);
  await finishLogin(h, h.logins[0], "current-session");
  assert.equal((await first).id, "current-session");
  assert.equal((await second).id, "current-session");
  assert.equal(h.storage.get("session_token"), "current-session");
  assert.equal(h.app.globalData.phoneBound, true);
});

for (const stage of ["wx", "session", "me"]) {
  test(`退出登录作废 ${stage} 阶段的成功回包`, async () => {
    const h = createWeapp();
    const pending = h.app.ensureSession();
    if (stage !== "wx") h.logins[0].success({ code: "old-code" });
    if (stage === "me") {
      h.requests[0].success({ statusCode: 200, data: { session: "old-session" } });
      await flush();
    }
    h.app.logout();
    if (stage === "wx") {
      h.logins[0].success({ code: "old-code" });
    } else if (stage === "session") {
      h.requests[0].success({ statusCode: 200, data: { session: "old-session" } });
    } else {
      h.requests[1].success({ statusCode: 200, data: { phone_bound: true } });
    }
    await flush();
    assert.equal(h.storage.get("session_token"), undefined);
    assert.equal(h.storage.get("logged_out"), true);
    assert.equal(h.app.globalData.user, null);
    assert.equal(h.app.globalData.phoneBound, false);
    assert.equal(h.requests.length, { wx: 0, session: 1, me: 2 }[stage]);
    assert.equal(await pending, null);
    assert.equal(await h.app.ensureSession(), null);
    assert.equal(h.logins.length, 1);
  });
}

for (const stage of ["session", "me"]) {
  for (const status of [200, 401, 503]) {
    test(`重新登录后忽略旧 ${stage} 的 ${status} 回包`, async () => {
      const h = createWeapp();
      const old = h.app.ensureSession();
      h.logins[0].success({ code: "old-code" });
      if (stage === "me") {
        h.requests[0].success({ statusCode: 200, data: { session: "old-session" } });
        await flush();
      }
      const staleRequest = h.requests.at(-1);
      h.app.logout();
      const fresh = h.app.loginAgain();
      await finishLogin(h, h.logins[1], "new-session");
      assert.equal((await fresh).id, "new-session");
      staleRequest.success({
        statusCode: status,
        data: stage === "session" ? { session: "old-session" } : { id: "old-user" },
      });
      await flush();
      assert.equal(h.storage.get("session_token"), "new-session");
      assert.equal(h.app.globalData.user.id, "new-session");
      assert.equal(h.app.globalData.phoneBound, true);
      assert.equal(h.toasts.length, 0);
      assert.equal(await old, null);
    });
  }
}

test("旧 wx.login 失败不能提示新登录失败", async () => {
  const h = createWeapp();
  const old = h.app.ensureSession();
  h.app.logout();
  const fresh = h.app.loginAgain();
  h.logins[0].fail();
  await flush();
  assert.equal(h.toasts.length, 0);
  assert.equal(await old, null);
  const attached = h.app.ensureSession();
  assert.equal(h.logins.length, 2, "旧任务收尾不能清掉新登录的在途登记");
  await finishLogin(h, h.logins[1], "new-session");
  assert.equal((await fresh).id, "new-session");
  assert.equal((await attached).id, "new-session");
});

for (const kind of ["request", "upload"]) {
  for (const newToken of ["new-session", "old-session"]) {
    test(`${kind} 的旧 401 不得清除后续登录（${newToken}）`, async () => {
      const h = createWeapp();
      const api = h.load("utils/api.js");
      h.storage.set("session_token", "old-session");
      const pending = kind === "request"
        ? api.request("/api/auth/me")
        : api.uploadFile("/api/draw/upload", "/tmp/photo.png");
      const rejected = assert.rejects(pending, (error) => error.status === 401);
      const stale = (kind === "request" ? h.requests : h.uploads)[0];
      h.app.logout();
      const fresh = h.app.loginAgain();
      // 同秒重签 HMAC 时 token 可能不变；请求所属的登录代次仍然已经不同。
      await finishLogin(h, h.logins[0], newToken);
      await fresh;
      stale.success({ statusCode: 401, data: {} });
      await rejected;
      assert.equal(h.storage.get("session_token"), newToken);
      assert.equal(h.app.globalData.user.id, newToken);
      assert.equal(h.app.globalData.phoneBound, true);
    });
  }
}

test("当前会话的 401 仍会清除登录与手机号状态", async () => {
  const h = createWeapp();
  const pending = h.app.ensureSession();
  await finishLogin(h, h.logins[0], "current-session");
  await pending;
  const request = h.load("utils/api.js").request("/api/auth/me");
  const rejected = assert.rejects(request, (error) => error.status === 401);
  h.requests.at(-1).success({ statusCode: 401, data: {} });
  await rejected;
  assert.equal(h.storage.get("session_token"), undefined);
  assert.equal(h.app.globalData.user, null);
  assert.equal(h.app.globalData.phoneBound, false);
});

test("退出登录后的 401 只拒请求，不强跳不弹授权（0.3.18 驳回合规回归）", async () => {
  const h = createWeapp();
  h.storage.set("session_token", "old-session");
  h.app.logout();
  const api = h.load("utils/api.js");
  const pending = api.request("/api/auth/me");
  const rejected = assert.rejects(pending, (e) => e.status === 401 && /请先登录/.test(e.detail));
  h.requests.at(-1).success({ statusCode: 401, data: {} });
  await rejected;
  assert.equal(h.tabs.length, 0, "浏览态接口 401 不得 switchTab 到「我的」");
  assert.equal(h.toasts.length, 0, "浏览态接口 401 不得弹授权提示");
});

test("软闸 ensurePhone 遇 401 回 null（可浏览未登录态）且不跳不弹", async () => {
  const h = createWeapp();
  const api = h.load("utils/api.js");
  const gate = h.load("utils/phone_gate.js");
  const pending = gate.ensurePhone(api);
  await flush();
  h.requests.at(-1).success({ statusCode: 401, data: {} });
  assert.equal(await pending, null);
  assert.equal(h.tabs.length, 0);
  assert.equal(h.toasts.length, 0);
});
