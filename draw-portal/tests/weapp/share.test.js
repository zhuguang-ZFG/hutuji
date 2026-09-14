const assert = require('node:assert/strict');
const test = require('node:test');
const { createWeapp, flush } = require('./harness');

function setup() {
  const h = createWeapp(); h.load('pages/share/share.js');
  h.page.onLoad({ share: 'public-share' });
  if (h.page.onShow) h.page.onShow();
  return h;
}
async function metadata(h) {
  h.requests.at(-1).success({ statusCode: 200, data: { title: '测试作品', preview_url: '/public-preview' } });
  await flush();
}

test('分享图片失败结束占位并可单独重试，成功后恢复预览', async () => {
  const h = setup(); await metadata(h);
  h.downloads[0].fail(); await flush();
  assert.match(h.page.data.previewError, /加载/);
  assert.equal(h.page.data.previewLoading, false);
  h.page.onRetryPreview();
  assert.equal(h.requests.length, 1);
  h.downloads[1].success({ statusCode: 200, tempFilePath: 'wxfile://public' }); await flush();
  assert.equal(h.page.data.previewUrl, 'wxfile://public');
  assert.equal(h.page.data.previewError, '');
});

test('网络错误可重试，不能误报分享过期', async () => {
  const h = setup(); h.requests[0].fail(); await flush();
  assert.equal(h.page.data.retryable, true);
  assert.doesNotMatch(h.page.data.failureTitle, /失效/);
  h.page.onRetry(); await metadata(h);
  assert.equal(h.page.data.failed, false);
});

test('404 分享按实际失效显示，不无限重试', async () => {
  const h = setup(); h.requests[0].success({ statusCode: 404, data: {} }); await flush();
  assert.equal(h.page.data.failed, true);
  assert.equal(h.page.data.retryable, false);
  assert.match(h.page.data.failureTitle, /失效/);
});

test('离页的分享元数据不再触发图片下载，回屏从当前请求恢复', async () => {
  const h = setup(); const old = h.requests[0];
  h.page.onHide();
  old.success({ statusCode: 200, data: { preview_url: '/old' } }); await flush();
  assert.equal(h.downloads.length, 0);
  h.page.onShow(); await metadata(h);
  assert.equal(h.downloads.length, 1);
});

test('图片下载后页面已卸载，迟到回包不写入页面', async () => {
  const h = setup(); await metadata(h);
  h.page.onUnload(); h.downloads[0].success({ statusCode: 200, tempFilePath: 'wxfile://old' }); await flush();
  assert.equal(h.page.data.previewUrl, '');
});

test('图片组件解码失败也显示可重试状态', async () => {
  const h = setup(); await metadata(h);
  h.downloads[0].success({ statusCode: 200, tempFilePath: 'wxfile://bad' }); await flush();
  h.page.onPreviewError();
  assert.equal(h.page.data.previewUrl, '');
  assert.match(h.page.data.previewError, /加载/);
});
