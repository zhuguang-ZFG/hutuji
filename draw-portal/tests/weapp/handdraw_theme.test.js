const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}

test("小程序启用奶油卖萌手绘主题并退出深空霓虹导航", () => {
  const app = JSON.parse(read("weapp/app.json"));
  const appStyle = read("weapp/app.wxss");
  const theme = read("weapp/styles/handdraw-theme.wxss");

  assert.equal(app.window.navigationBarTitleText, "丹青 · 小派");
  assert.equal(app.window.navigationBarBackgroundColor, "#FFF7ED");
  assert.equal(app.window.navigationBarTextStyle, "black");
  assert.equal(app.tabBar.selectedColor, "#D85F7B");
  assert.match(appStyle, /^@import "\.\/styles\/handdraw-theme\.wxss";/);
  assert.match(theme, /FH-031/);
  assert.match(theme, /#fffdf8/i);
  assert.match(theme, /#f3a3b5/i);
  assert.match(theme, /#bfe4d7/i);
});

test("所有显式页面背景都使用纸本色，跳转和下拉刷新不闪回深色", () => {
  const app = JSON.parse(read("weapp/app.json"));
  for (const page of app.pages) {
    const rel = `weapp/${page}.json`;
    if (!fs.existsSync(path.join(ROOT, rel))) continue;
    const config = JSON.parse(read(rel));
    if (config.backgroundColor) assert.equal(config.backgroundColor.toUpperCase(), "#FFF7ED", rel);
    assert.notEqual((config.backgroundColor || "").toUpperCase(), "#0B0C13", rel);
  }
});

test("五条主路径保留手绘工坊的叙事入口", () => {
  const checks = [
    ["weapp/pages/index/index.wxml", "小派陪你，把灵感画出来"],
    ["weapp/pages/control/control.wxml", "小派绘画控制台"],
    ["weapp/pages/history/history.wxml", "小派帮你收好每一张画"],
    ["weapp/pages/profile/profile.wxml", "我的<text class=\"accent\">小派画画屋</text>"],
  ];
  for (const [rel, text] of checks) assert.match(read(rel), new RegExp(text));
  assert.match(read("weapp/pages/chat/chat.wxml"), /今天想和小派画什么？/);
});

test("四条主路径复用可动的小派卖萌脸作为品牌记忆点", () => {
  for (const rel of [
    "weapp/pages/index/index.wxml",
    "weapp/pages/control/control.wxml",
    "weapp/pages/history/history.wxml",
    "weapp/pages/profile/profile.wxml",
  ]) {
    assert.match(read(rel), /class="pai-mascot/);
    assert.match(read(rel), /class="pai-face"/);
  }
  const theme = read("weapp/styles/handdraw-theme.wxss");
  assert.match(theme, /@keyframes pai-float/);
  assert.match(theme, /@keyframes pai-blink/);
  assert.match(theme, /padding-right: 150rpx/);
});

test("设备控件的动态色同步到珊瑚粉与奶油纸色", () => {
  assert.match(read("weapp/pages/control/control.wxml"), /activeColor="#D85F7B"/);
  assert.match(read("weapp/pages/control/control.wxml"), /backgroundColor="#F3DFC8"/);
  const paper = read("weapp/pages/paper/paper.wxml");
  assert.equal((paper.match(/color="#D85F7B"/g) || []).length, 2);
});

test("非主 Tab 流程页和对话页也被手绘主题覆盖", () => {
  const theme = read("weapp/styles/handdraw-theme.wxss");
  for (const selector of [".log", ".progress-wrap", ".model-item", ".preview-card", ".handraw-credit"]) {
    assert.match(theme, new RegExp(selector.replace(".", "\\.")));
  }
  assert.match(theme, /避免流程中途退回深色旧主题/);
});

test("二次开发在产品与文档中保留 handraw-style 作者和仓库地址", () => {
  const profile = read("weapp/pages/profile/profile.wxml");
  const profileJs = read("weapp/pages/profile/profile.js");
  const readme = read("README.md");
  assert.match(profile, /原作者 yang0/);
  assert.match(profile, /handrawCreditUrl/);
  assert.match(profileJs, /https:\/\/github\.com\/yang0\/handraw-style/);
  assert.match(readme, /https:\/\/github\.com\/yang0\/handraw-style/);
});
