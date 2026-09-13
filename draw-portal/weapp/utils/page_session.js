// 页面异步任务同时属于一次展示与一次登录，跨任一边界的回包不得写界面或续发请求。
function show(page) {
  page._visible = true;
  page._viewGeneration = (page._viewGeneration || 0) + 1;
}

function hide(page) {
  page._visible = false;
  page._viewGeneration = (page._viewGeneration || 0) + 1;
}

function capture(page) {
  const view = page._viewGeneration || 0;
  const session = getApp()._sessionGeneration;
  return () => page._visible !== false && (page._viewGeneration || 0) === view &&
    getApp()._sessionGeneration === session;
}

// 已发出的生成请求要保留同账号结果；暂时切页不等于取消已付费的工作。
// 只保存结果，通知、轮询和后续控制仍须使用展示代次或回到页面后执行。
function captureTask(page) {
  const session = getApp()._sessionGeneration;
  return () => !page._unloaded && getApp()._sessionGeneration === session;
}

module.exports = { show, hide, capture, captureTask };
