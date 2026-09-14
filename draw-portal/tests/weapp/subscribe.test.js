const assert = require('node:assert/strict');
const test = require('node:test');
const { createWeapp, flush } = require('./harness');

function setup() {
  const h = createWeapp();
  const prompts = [];
  h.wx.requestSubscribeMessage = (options) => prompts.push(options);
  h.storage.set('subscribe_tid', 'previous-template');
  return { h, prompts, subscribe: h.load('utils/subscribe.js') };
}

test('服务器关闭订阅后清除旧模板，不再弹无效授权', async () => {
  const { h, prompts, subscribe } = setup();
  subscribe.prepare(); h.requests[0].success({ statusCode: 200, data: { template_id: null } }); await flush();
  subscribe.requestAtTap();
  assert.equal(prompts.length, 0);
  assert.equal(h.storage.has('subscribe_tid'), false);
});

test('配置请求失败可使用已有模板，旧的成功回包不能覆盖新的关闭状态', async () => {
  const { h, prompts, subscribe } = setup();
  subscribe.prepare(); h.requests[0].fail(); await flush();
  subscribe.requestAtTap(); assert.equal(prompts.length, 1);
  subscribe.prepare(); subscribe.prepare();
  h.requests[2].success({ statusCode: 200, data: {} }); await flush();
  h.requests[1].success({ statusCode: 200, data: { template_id: 'old-template' } }); await flush();
  subscribe.requestAtTap(); assert.equal(prompts.length, 1);
});
