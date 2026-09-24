const assert = require('node:assert/strict');
const test = require('node:test');
const { createWeapp } = require('./harness');

for (const kind of ['request', 'upload']) {
  for (const [statusCode, data] of [[302, {}], [undefined, {}], [200, '<html>error</html>'], [200, null]]) {
    test(`${kind} 拒绝无效成功应答 ${statusCode}/${typeof data}`, async () => {
      const h = createWeapp();
      const api = h.load('utils/api.js');
      const pending = kind === 'request' ? api.request('/api/test') : api.uploadFile('/api/test', '/tmp/file');
      const rejected = assert.rejects(pending, e => /响应异常|请求失败|上传失败/.test(e.detail));
      (kind === 'request' ? h.requests : h.uploads).at(-1).success({ statusCode, data });
      await rejected;
    });
  }
  test(`${kind} 提交超时提示核对结果，不误报未执行`, async () => {
    const h = createWeapp();
    const api = h.load('utils/api.js');
    const pending = kind === 'request'
      ? api.request('/api/draw/1/send', { method: 'POST' }) : api.uploadFile('/api/test', '/tmp/file');
    const rejected = assert.rejects(pending, e => e.status === 0 && /尚未确认/.test(e.detail) && e.message === e.detail);
    (kind === 'request' ? h.requests : h.uploads).at(-1).fail({ errMsg: 'request:fail timeout' });
    await rejected;
  });
}

test('只读请求超时仍可明确提示重试', async () => {
  const h = createWeapp();
  const pending = h.load('utils/api.js').request('/api/auth/me');
  const rejected = assert.rejects(pending, e => /超时/.test(e.detail) && !/尚未确认/.test(e.detail));
  h.requests.at(-1).fail({ errMsg: 'request:fail timeout' });
  await rejected;
});
