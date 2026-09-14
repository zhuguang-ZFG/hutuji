// 摘要是本次展示的设备快照；换账号、换目标或离页后不接收旧配置。
const api = require('./api');
const paper = require('./paper_config');
const pageSession = require('./page_session');

function reset(page) {
  page._paperSummaryRequest = {};
  page.setData({ paperSummary: { title: '纸张与方向', detail: '正在读取当前设备…', state: 'loading', landscape: false } });
}

function refresh(page) {
  const visible = pageSession.capture(page);
  const token = api.getSession();
  const mac = (getApp().globalData.user || {}).active_device_mac || '';
  const request = page._paperSummaryRequest = {};
  const current = () => visible() && token === api.getSession() && page._paperSummaryRequest === request &&
    ((getApp().globalData.user || {}).active_device_mac || '') === mac;
  if (!mac) {
    page.setData({ paperSummary: { title: '纸张与方向', detail: '连接写字机后即可设置', state: 'unbound', landscape: false } });
    return Promise.resolve();
  }
  page.setData({ paperSummary: { title: '纸张与方向', detail: '正在读取当前设备…', state: 'loading', landscape: false } });
  return api.request('/api/settings/paper?device_mac=' + encodeURIComponent(mac), { timeout: 25000 })
    .then((res) => {
      if (!current()) return;
      if (!res || res.device_mac !== mac || res.marker !== paper.marker(res.config) ||
          typeof res.online !== 'boolean' || typeof res.capable !== 'boolean') throw new Error('纸张信息未确认，点此查看');
      const d = paper.dimensions(res.config);
      const name = res.config.paper === 'custom' ? '自定义 ' + d.width + ' × ' + d.height : res.config.paper;
      page.setData({ paperSummary: {
        title: name + ' · ' + (res.config.landscape ? '横版' : '竖版'),
        detail: (res.online ? '' : '上次确认 · ') + '绘图区 ' + d.pageWidth + ' × ' + d.pageHeight + ' mm',
        state: res.online ? 'ready' : 'offline', landscape: d.width > d.height,
        hint: res.capable ? '' : '升级后可调整'
      } });
    }).catch((error) => {
      if (!current()) return;
      page.setData({ paperSummary: { title: '纸张与方向',
        detail: paper.requestError(error, '纸张暂未读取，点此查看'),
        state: 'unavailable', landscape: false } });
    });
}

module.exports = { reset, refresh };
