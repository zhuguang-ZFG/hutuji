// 使用真实浏览器和本地拦截应答验证操作语义，不访问生产设备。
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
before(async () => {
  browser = await chromium.launch({ executablePath: process.env.WEB_BROWSER, headless: true });
});
after(async () => { if (browser) await browser.close(); });
async function pageFor(kind, overrides = {}) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const calls = [];
  const defaults = {
    '/auth/me': { email: 'test@qq.com', binding: { bound: false } },
    '/draw/saved': { items: [] }, '/stats': { users: 1 },
    '/settings/draw-limits': { keys: [] }, '/settings/image-backends': { keys: [] },
    '/image-providers': { items: [] }, '/users': { items: [] },
    '/bindings': { items: [] }, '/ops/notes': {}, '/ops/checklist': { sections: [] },
    '/faqs': [], '/publish-records': [],
  };
  const prefix = kind === 'portal' ? '/draw-upload' : kind === 'admin' ? '/draw-admin' : '';
  await page.route('http://review.local/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') {
      const file = kind === 'portal' ? '../../draw_portal' : kind === 'admin' ? '../../draw_admin' : '../../../kb-admin/kb_admin';
      return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(__dirname, file, 'static/index.html'), 'utf8') });
    }
    const endpoint = url.pathname.slice((prefix + '/api').length);
    calls.push(endpoint);
    if (overrides[endpoint]) return overrides[endpoint](route);
    return route.fulfill({ json: defaults[endpoint] || {} });
  });
  await page.goto('http://review.local/');
  await page.locator(kind === 'portal' ? '#home-section' : kind === 'admin' ? '#dash-section' : '#app-view').waitFor({ state: 'visible' });
  return { page, calls, errors };
}
test('取消封禁不会产生写请求', async () => {
  const { page, calls } = await pageFor('admin');
  try {
    page.on('dialog', d => d.dismiss());
    await page.evaluate(() => handleUserAction('user-1', 'ban'));
    assert.equal(calls.includes('/users/user-1/ban'), false);
  } finally { await page.close(); }
});
test('用户字段作为文本展示，不创建 HTML 元素', async () => {
  const attack = '<img src=x onerror="window.injected=1">';
  const { page } = await pageFor('admin', { '/users': r => r.fulfill({ json: { items: [{ id: 'u1', email: attack, phone: attack, status: 'active' }] } }) });
  try {
    await page.locator('#users-cards .user-card').waitFor();
    assert.equal(await page.locator('#users-cards img').count(), 0);
    assert.ok((await page.locator('#users-cards').textContent()).includes(attack));
  } finally { await page.close(); }
});
test('关闭的弹窗不可聚焦，打开后焦点受限且退出恢复', async () => {
  const { page } = await pageFor('portal');
  try {
    assert.equal(await page.locator('#upload-sheet').evaluate(e => e.inert), true);
    await page.locator('#fab-upload').click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'btn-close-sheet');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(() => document.querySelector('#upload-sheet').contains(document.activeElement)), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'fab-upload');
  } finally { await page.close(); }
});
test('发送期间重复触发只有一次下发', async () => {
  let release;
  const pending = new Promise(r => { release = r; });
  const { page, calls } = await pageFor('portal', { '/draw/job-1/send': async r => { await pending; await r.fulfill({ json: { status: 'sent' } }); } });
  try {
    await page.evaluate(() => { openSheet(); showJobResult({ job_id: 'job-1', preview_url: '/preview' }, '完成'); $('btn-send').click(); $('btn-send').click(); });
    await page.waitForTimeout(100);
    assert.equal(calls.filter(x => x === '/draw/job-1/send').length, 1);
  } finally { release(); await page.close(); }
});
test('快选新文件后慢压缩不能覆盖，也不能继续发送旧产物', async () => {
  const { page } = await pageFor('portal');
  try {
    const state = await page.evaluate(async () => {
      let finish;
      maybeDownscale = f => f.name === 'old.txt' ? new Promise(r => { finish = () => r(f); }) : Promise.resolve(f);
      showJobResult({ job_id: 'stale', preview_url: '/preview' }, '完成');
      const old = selectFile(new File(['old'], 'old.txt'));
      await selectFile(new File(['new'], 'new.txt'));
      finish(); await old;
      return { name: pendingFile.name, job: currentJobId };
    });
    assert.deepEqual(state, { name: 'new.txt', job: null });
  } finally { await page.close(); }
});
test('后台单模块失败不阻止用户列表加载', async () => {
  const { page, calls } = await pageFor('admin', { '/settings/draw-limits': r => r.fulfill({ status: 503, json: { detail: '暂不可用' } }) });
  try {
    await page.waitForTimeout(150);
    assert.ok(calls.includes('/users'));
    assert.ok((await page.locator('#dash-section').textContent()).includes('加载失败'));
  } finally { await page.close(); }
});

test('暂时离线不会清空已登录门户，401 才结束会话', async () => {
  const { page } = await pageFor('portal');
  try {
    await page.route('**/api/auth/me', r => r.abort('internetdisconnected'));
    await page.evaluate(() => refreshMe());
    assert.equal(await page.locator('#home-section').isVisible(), true);
    assert.match(await page.locator('#gallery-msg').textContent(), /网络/);
    await page.route('**/api/auth/me', r => r.fulfill({ status: 401, json: { detail: '登录已过期' } }));
    await page.evaluate(() => refreshMe());
    assert.equal(await page.locator('#auth-section').isVisible(), true);
    assert.equal(await page.locator('#home-section').isVisible(), false);
  } finally { await page.close(); }
});

test('上传与生成共用忙状态，旧生成结果不能覆盖新选择', async () => {
  let release;
  const pending = new Promise(r => { release = r; });
  const { page, calls } = await pageFor('portal', { '/draw/generate': async r => { await pending; await r.fulfill({ json: { job_id: 'old', lines: 12, preview_url: '/preview' } }); } });
  try {
    await page.evaluate(async () => {
      await selectFile(new File(['first'], 'first.txt'));
      $('gen-desc').value = '小猫';
      $('btn-generate').click();
      $('btn-upload').click();
      await selectFile(new File(['second'], 'second.txt'));
    });
    release();
    await page.waitForFunction(() => !jobBusy);
    assert.equal(calls.includes('/draw/upload'), false);
    assert.deepEqual(await page.evaluate(() => ({ job: currentJobId, name: pendingFile.name })), { job: null, name: 'second.txt' });
    assert.equal(await page.locator('#btn-upload').isEnabled(), true);
  } finally { release(); await page.close(); }
});

test('重复保存只写一次，旧保存完成不能清掉新文件', async () => {
  let release;
  const pending = new Promise(r => { release = r; });
  const { page, calls } = await pageFor('portal', { '/draw/old/save': async r => { await pending; await r.fulfill({ json: { status: 'saved' } }); } });
  try {
    await page.evaluate(async () => {
      openSheet(); showJobResult({ job_id: 'old', preview_url: '/preview' }, '完成');
      $('btn-save').click(); $('btn-save').click();
      await selectFile(new File(['second'], 'second.txt'));
    });
    release();
    await page.waitForFunction(() => !saveBusy);
    assert.equal(calls.filter(x => x === '/draw/old/save').length, 1);
    assert.equal(await page.evaluate(() => pendingFile.name), 'second.txt');
    assert.equal(await page.evaluate(() => sheetOpen), true);
  } finally { release(); await page.close(); }
});

test('旧用户详情不能覆盖后选用户', async () => {
  let release;
  const pending = new Promise(r => { release = r; });
  const { page } = await pageFor('admin', {
    '/users/old': async r => { await pending; await r.fulfill({ json: { email: 'old@qq.com' } }); },
    '/users/new': r => r.fulfill({ json: { email: 'new@qq.com' } }),
  });
  try {
    await page.evaluate(() => { window.oldDetail = handleUserAction('old', 'detail'); });
    await page.evaluate(() => handleUserAction('new', 'detail'));
    release();
    await page.evaluate(() => window.oldDetail);
    assert.match(await page.locator('#detail').textContent(), /new@qq.com/);
  } finally { release(); await page.close(); }
});

test('会话名转义且请求路径编码，接口异常可见', async () => {
  const session = '"<img src=x>/branch?x=1';
  const { page, calls, errors } = await pageFor('admin', {
    '/users/u1/conversations': r => r.fulfill({ json: { items: [{ session_id: session, message_count: 1 }] } }),
    ['/users/u1/conversations/' + encodeURIComponent(session)]: r => r.fulfill({ status: 503, json: { detail: '会话暂不可用' } }),
  });
  try {
    await page.evaluate(() => handleUserAction('u1', 'conv'));
    assert.equal(await page.locator('#conv-sessions img').count(), 0);
    await page.locator('#conv-sessions button').click();
    await page.waitForFunction(() => document.querySelector('#toast-stack').textContent.includes('会话暂不可用'));
    assert.ok(calls.includes('/users/u1/conversations/' + encodeURIComponent(session)));
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('设置加载失败时禁止保存空表，重试恢复', async () => {
  let broken = true;
  const { page } = await pageFor('admin', {
    '/ops/notes': r => broken ? r.fulfill({ status: 503, json: {} }) : r.fulfill({ json: { shipping_note: '量产无换纸' } }),
  });
  try {
    await page.locator('#dash-error').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#btn-ops-save').isDisabled(), true);
    broken = false;
    await page.locator('#btn-retry-dash').click();
    await page.waitForFunction(() => document.querySelector('#ops-ship').value === '量产无换纸');
    assert.equal(await page.locator('#btn-ops-save').isEnabled(), true);
  } finally { await page.close(); }
});

for (const kind of ['portal', 'admin', 'kb']) {
  test(`${kind} 移动端与桌面无水平溢出，保留截图`, async () => {
    const { page, errors } = await pageFor(kind);
    try {
      for (const width of [375, 390, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        if (kind === 'portal') await page.locator('#fab-upload').click();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        if (process.env.WEB_SCREENSHOT_DIR) {
          fs.mkdirSync(process.env.WEB_SCREENSHOT_DIR, { recursive: true });
          await page.screenshot({ path: path.join(process.env.WEB_SCREENSHOT_DIR, `${kind}-${width}.png`), fullPage: true, animations: 'disabled' });
        }
        if (kind === 'portal') await page.keyboard.press('Escape');
      }
      assert.deepEqual(errors, []);
    } finally { await page.close(); }
  });
}

test('手机后台能够看到设备绑定行', async () => {
  const { page } = await pageFor('admin', { '/bindings': r => r.fulfill({ json: { items: [{ device_mac: 'test-device', phone: '138****0000' }] } }) });
  try {
    await page.locator('#bindings-body tr').waitFor({ state: 'attached' });
    assert.equal(await page.locator('#bindings-body tr').isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  } finally { await page.close(); }
});

test('知识库导入断网后退出忙状态且可见错误', async () => {
  const { page, errors } = await pageFor('kb', { '/import': r => r.abort('internetdisconnected') });
  try {
    await page.locator('#btn-import').click();
    await page.waitForTimeout(150);
    assert.equal(await page.locator('#loading-overlay').isVisible(), false);
    assert.match(await page.locator('#toast-stack').textContent(), /尚未确认/);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('知识库表单重复提交不重复创建，处理中不能切换编辑目标', async () => {
  let release;
  const pending = new Promise(r => { release = r; });
  let writes = 0;
  const { page } = await pageFor('kb', {
    '/faqs': async r => {
      if (r.request().method() === 'POST') { writes++; await pending; return r.fulfill({ json: { id: 'new' } }); }
      return r.fulfill({ json: [] });
    },
  });
  try {
    await page.locator('#btn-new').click();
    await page.locator('#edit-title').fill('量产装纸');
    await page.locator('#edit-body').fill('每次装一张纸');
    await page.evaluate(() => {
      editForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      editForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      closeEdit();
    });
    await page.waitForTimeout(100);
    assert.equal(writes, 1);
    assert.equal(await page.locator('#edit-modal').isVisible(), true);
  } finally { release(); await page.close(); }
});

test('知识库关闭修改草稿须确认，弹窗焦点受限', async () => {
  const { page } = await pageFor('kb');
  try {
    await page.locator('#btn-new').click();
    await page.locator('#edit-title').fill('未保存的内容');
    let dialogs = 0;
    page.on('dialog', d => { dialogs++; d.dismiss(); });
    await page.keyboard.press('Escape');
    assert.equal(dialogs, 1);
    assert.equal(await page.locator('#edit-modal').isVisible(), true);
    await page.locator('#btn-save').focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => editModal.contains(document.activeElement)), true);
  } finally { await page.close(); }
});

test('图库刷新和预览入口共用设备发送占位', async () => {
  let release;
  const pending = new Promise(r => { release = r; });
  const { page, calls } = await pageFor('portal', {
    '/draw/saved': r => r.fulfill({ json: { items: [{ id: 'saved-1', title: '小猫', has_gcode: true, preview_url: '/preview', saved_at: 1 }] } }),
    '/draw/saved/saved-1/send': async r => { await pending; await r.fulfill({ json: { status: 'sent' } }); },
  });
  try {
    await page.locator('[data-send]').click();
    await page.evaluate(async () => {
      await loadSaved();
      document.querySelector('[data-send]').click();
      showJobResult({ job_id: 'job-2', preview_url: '/preview' }, '完成');
      $('btn-send').click();
    });
    assert.equal(calls.filter(x => x.endsWith('/send')).length, 1);
    release();
    await page.waitForFunction(() => !sendBusy);
    assert.equal(await page.locator('[data-send]').isEnabled(), true);
  } finally { release(); await page.close(); }
});

test('后台保存后刷新失败不会解锁不可用设置，显式刷新可恢复', async () => {
  let failRead = false;
  const { page } = await pageFor('admin', {
    '/settings/draw-limits': r => {
      if (r.request().method() === 'PUT') { failRead = true; return r.fulfill({ json: {} }); }
      return failRead ? r.fulfill({ status: 503, json: {} }) : r.fulfill({ json: { keys: [] } });
    },
  });
  try {
    await page.locator('#btn-save-limits').click();
    await page.waitForFunction(() => document.querySelector('#toast-stack').textContent.includes('暂不可用'));
    assert.equal(await page.locator('#btn-save-limits').isDisabled(), true);
    failRead = false;
    await page.locator('#btn-refresh-admin').click();
    await page.waitForFunction(() => !document.querySelector('#btn-save-limits').disabled);
  } finally { await page.close(); }
});

test('知识库筛选复用完整列表，避免每次选分类请求两遍', async () => {
  const { page, calls } = await pageFor('kb', { '/faqs': r => r.fulfill({ json: [
    { id: 'a', title: '装机说明', body: '装纸', category: 'install', aliases: [], enabled: true },
    { id: 'b', title: '故障说明', body: '排查', category: 'fault', aliases: [], enabled: true },
  ] }) });
  try {
    await page.locator('#filter-category').selectOption('install');
    assert.equal(await page.locator('#faq-tbody tr').count(), 1);
    assert.match(await page.locator('#faq-tbody').textContent(), /装机说明/);
    await page.locator('#filter-category').selectOption('');
    assert.equal(await page.locator('#faq-tbody tr').count(), 2);
    assert.equal(calls.filter(x => x === '/faqs').length, 1);
  } finally { await page.close(); }
});

test('门户写请求超时释放忙状态并提示核对设备，不自动重试', async () => {
  const { page, calls } = await pageFor('portal', { '/draw/test/send': r => r.abort('timedout') });
  try {
    await page.evaluate(async () => {
      showJobResult({ job_id: 'test', preview_url: '/preview' }, '完成');
      await $('btn-send').onclick();
    });
    assert.match(await page.locator('#upload-msg').textContent(), /尚未确认/);
    assert.equal(await page.evaluate(() => sendBusy), false);
    assert.equal(calls.filter(x => x === '/draw/test/send').length, 1);
  } finally { await page.close(); }
});

test('后台重新登录后旧401不能退出新会话，旧模块加载不阻断新面板', async () => {
  let release;
  const pending = new Promise(r => { release = r; });
  let reads = 0;
  const { page } = await pageFor('admin', {
    '/ops/notes': async r => {
      if (++reads === 1) { await pending; return r.fulfill({ status: 401, json: {} }); }
      return r.fulfill({ json: { shipping_note: '新的会话' } });
    },
  });
  try {
    await page.evaluate(async () => {
      $('login-section').classList.remove('hidden');
      $('dash-section').classList.add('hidden');
      $('admin-password').value = 'test-only';
      await $('btn-admin-login').onclick();
    });
    release();
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#dash-section').isVisible(), true);
    assert.equal(await page.locator('#ops-ship').inputValue(), '新的会话');
  } finally { release(); await page.close(); }
});
