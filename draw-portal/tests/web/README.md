# 网页量产交互回归

覆盖买家门户、运营后台、知识库后台的真实 DOM、键盘、并发请求和移动布局。使用 Playwright + 本机 Chromium 浏览器，所有请求在 `http://review.local/` 拦截，不访问生产或真实设备。后端与微信原生能力仍分别跑 pytest / `tests/weapp`。

从枢纽根目录执行（Node 内置测试运行器）：

```powershell
npm.cmd install --prefix tmp/web-review-runtime --no-save --ignore-scripts playwright@1.58.2
$env:PLAYWRIGHT_MODULE = "$PWD/tmp/web-review-runtime/node_modules/playwright"
$env:WEB_BROWSER = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
$env:WEB_SCREENSHOT_DIR = "$PWD/results/web-review"
node --test draw-portal/tests/web/production.test.cjs
```

也可使用已有 Playwright 安装和 Chrome 路径。截图目录可省略；浏览器依赖不进入产品镜像或小程序包。默认截图覆盖 375 / 390 / 1440 CSS 像素，禁用截图期间动画以捕获展开后的弹窗。截图必须人工查看，DOM 无溢出不等于完整视觉/无障碍验收。

关键用例：取消封禁不写入、HTML 文本转义、乱序用户/文件/生成/保存回包、跨图库/预览防重、设置加载失败不可保存、断网恢复、手机设备表可见、弹窗 Tab/Escape/焦点恢复、知识库草稿放弃确认与筛选不重复请求。
