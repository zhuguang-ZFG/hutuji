const assert = require('node:assert/strict');
const test = require('node:test');
const { createWeapp, createClock, flush } = require('./harness');

async function setup(permission = true) {
  const clock = createClock();
  const h = createWeapp({ clock });
  const authorizations = [], settings = [], subscriptions = [];
  h.wx.getSetting = (options) => options.success({ authSetting: permission === null ? {} : { 'scope.record': permission } });
  h.wx.authorize = (options) => authorizations.push(options);
  h.wx.openSetting = (options) => settings.push(options);
  h.wx.requestSubscribeMessage = (options) => subscriptions.push(options);
  h.load('pages/chat/chat.js'); h.page.onLoad();
  h.requests[0].success({ statusCode: 200, data: { template_id: 'notice' } });
  h.page.onShow(); await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
  await flush();
  return { h, clock, authorizations, settings, subscriptions };
}
function stop(h, duration = 1800) {
  h.page.onHoldStart({ touches: [{ clientY: 400 }] });
  h.recorder.emit('start');
  h.page.onHoldEnd();
  h.recorder.emit('stop', { tempFilePath: 'wxfile://voice.mp3', duration });
}

test('按住只启动录音，不被订阅弹窗打断，音频参数匹配 16k 识别', async () => {
  const { h, subscriptions } = await setup();
  h.page.onHoldStart();
  assert.equal(subscriptions.length, 0);
  assert.equal(h.recorder.starts.length, 1);
  assert.equal(h.recorder.starts[0].sampleRate, 16000);
  assert.equal(h.recorder.starts[0].numberOfChannels, 1);
});

test('首次授权单独完成，批准后须再次按住才录，不追录松手后的声音', async () => {
  const { h, authorizations } = await setup(null);
  assert.equal(h.page.data.recordPermission, 'unknown');
  h.page.onHoldStart();
  assert.equal(h.recorder.starts.length, 0);
  h.page.onEnableMicrophone();
  assert.equal(authorizations[0].scope, 'scope.record');
  authorizations[0].success();
  assert.equal(h.page.data.recordPermission, 'granted');
  assert.equal(h.recorder.starts.length, 0);
  h.page.onHoldStart();
  assert.equal(h.recorder.starts.length, 1);
});

test('已拒绝麦克风时提供设置入口，不反复发起录音', async () => {
  const { h, settings } = await setup(false);
  h.page.onEnableMicrophone();
  assert.equal(settings.length, 1);
  assert.equal(h.recorder.starts.length, 0);
  settings[0].success({ authSetting: { 'scope.record': true } });
  assert.equal(h.page.data.recordPermission, 'granted');
});

test('授权窗口跨过离页，回包不会开始录音或改新页面', async () => {
  const { h, authorizations } = await setup(null);
  h.page.onEnableMicrophone();
  h.page.onHide();
  authorizations[0].success();
  assert.equal(h.recorder.starts.length, 0);
  assert.equal(h.page.data.recordPermission, 'unknown');
});

test('上滑取消录音，取消后能立即进行下一次按住', async () => {
  const { h } = await setup();
  h.page.onHoldStart({ touches: [{ clientY: 400 }] });
  h.recorder.emit('start');
  h.page.onHoldMove({ touches: [{ clientY: 300 }] });
  assert.equal(h.page.data.recordCancel, true);
  h.page.onHoldEnd();
  h.recorder.emit('stop', { tempFilePath: 'wxfile://cancel.mp3', duration: 1800 });
  assert.equal(h.uploads.length, 0);
  assert.equal(h.page.data.recordingStopping, false);
  h.page.onHoldStart();
  assert.equal(h.recorder.starts.length, 2);
});

test('误触短录音不上传，显示可以再次按住的提示', async () => {
  const { h } = await setup();
  stop(h, 120);
  assert.equal(h.uploads.length, 0);
  assert.match(h.page.data.voiceHint, /太短/);
  assert.equal(h.page.data.busy, false);
});

test('系统报错释放录音占用，重按可启动', async () => {
  const { h } = await setup();
  h.page.onHoldStart();
  h.recorder.emit('error', { errMsg: 'record audio device unavailable' });
  assert.equal(h.page.data.recordingStarting, false);
  assert.match(h.page.data.voiceHint, /录音/);
  h.page.onHoldStart();
  assert.equal(h.recorder.starts.length, 2);
});

test('未录音离开页面也能卸载，不依赖平台不存在的解绑接口', async () => {
  const { h } = await setup();
  assert.doesNotThrow(() => h.page.onUnload());
});

test('旧页面卸载后新页面不会抢走迟到的录音回调，取消完成后可再录', async () => {
  const { h } = await setup();
  const old = h.page;
  old.onHoldStart();
  old.onUnload();
  const next = { data: JSON.parse(JSON.stringify(old.data)) };
  for (const [name, value] of Object.entries(old)) {
    if (typeof value === 'function' && name !== 'setData') next[name] = value;
  }
  next.setData = (patch) => Object.assign(next.data, JSON.parse(JSON.stringify(patch)));
  next.onLoad(); next.onShow(); await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } }); await flush();
  h.recorder.emit('start');
  assert.equal(h.recorder.stops, 1);
  h.recorder.emit('stop', { tempFilePath: 'wxfile://discarded.mp3', duration: 1800 });
  assert.equal(h.uploads.length, 0);
  next.onHoldStart(); h.recorder.emit('start');
  assert.equal(h.recorder.starts.length, 2);
  assert.equal(next.data.recording, true);
  next.onHoldCancel(); h.recorder.emit('stop', { duration: 1000 });
});

test('识别结束时页面隐藏不发语音控制，回屏后同一次请求继续', async () => {
  const { h } = await setup();
  stop(h);
  assert.equal(h.uploads[0].timeout, 45000);
  h.page.onHide();
  h.uploads[0].success({ statusCode: 200, data: JSON.stringify({ text: '查一下状态' }) });
  await flush();
  assert.equal(h.requests.filter((r) => r.url.endsWith('/voice/turn')).length, 0);
  h.page.onShow(); await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
  await flush();
  assert.equal(h.requests.filter((r) => r.url.endsWith('/voice/turn')).length, 1);
  assert.equal(h.uploads.length, 1);
});

for (const beforeShow of [false, true]) {
  test(`已经提交的语音回复在回屏${beforeShow ? '前' : '后'}到达仍保留，不重复录音`, async () => {
    const { h } = await setup();
    stop(h);
    h.uploads[0].success({ statusCode: 200, data: JSON.stringify({ text: '你好' }) });
    await flush();
    const turn = h.requests.at(-1);
    assert(turn.url.endsWith('/voice/turn'));
    h.page.onHide();
    if (beforeShow) { turn.success({ statusCode: 200, data: { reply: '语音的回复' } }); await flush(); }
    h.page.onShow(); await flush();
    h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
    await flush();
    if (!beforeShow) {
      assert.equal(h.page.data.busy, true);
      h.page.onHoldStart();
      assert.equal(h.recorder.starts.length, 1);
      turn.success({ statusCode: 200, data: { reply: '语音的回复' } }); await flush();
    }
    assert.equal(h.page.data.messages.at(-1).content, '语音的回复');
    assert.equal(h.page.data.busy, false);
  });
}
