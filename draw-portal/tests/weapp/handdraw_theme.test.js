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

// ---------- 最小 WXSS 层叠解析：主题 → app.wxss → 页面 wxss，按 (!important, 特异度, 源码顺序) 取胜 ----------
// 只模拟静止态：含 :active、:nth-child、[disabled]、伪元素的选择器不参与匹配。

function parseRules(text, origin) {
  const src = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/@import[^;]*;/g, "");
  const rules = [];
  let i = 0;
  while (i < src.length) {
    const open = src.indexOf("{", i);
    if (open < 0) break;
    const head = src.slice(i, open).trim();
    let depth = 1;
    let k = open + 1;
    while (depth && k < src.length) {
      if (src[k] === "{") depth += 1;
      else if (src[k] === "}") depth -= 1;
      k += 1;
    }
    const body = src.slice(open + 1, k - 1);
    i = k;
    if (head.startsWith("@")) continue;
    const decls = [];
    for (const part of body.split(";")) {
      const colon = part.indexOf(":");
      if (colon < 0) continue;
      const prop = part.slice(0, colon).trim().toLowerCase();
      let value = part.slice(colon + 1).trim();
      const important = /!\s*important\s*$/.test(value);
      value = value.replace(/\s*!\s*important\s*$/, "");
      decls.push({ prop, value, important });
    }
    for (const selector of head.split(",").map((s) => s.trim().replace(/\s+/g, " ")).filter(Boolean)) {
      rules.push({ selector, decls, origin });
    }
  }
  return rules;
}

function compoundTokens(compound) {
  const stripped = compound.replace(/:not\([^)]*\)/g, "");
  if (/::|:(?!not)|\[/.test(stripped)) return null;
  const tokens = new Set();
  const tag = stripped.match(/^[a-zA-Z][\w-]*/);
  if (tag) tokens.add(`tag:${tag[0].toLowerCase()}`);
  for (const m of stripped.matchAll(/\.([\w-]+)/g)) tokens.add(`.${m[1]}`);
  return tokens;
}

function specificity(selector) {
  const s = selector.replace(/:not\(([^)]*)\)/g, " $1");
  const classes = (s.match(/\.[\w-]+/g) || []).length + (s.match(/\[[^\]]+\]/g) || []).length;
  const tags = (s.match(/(?:^|[\s>+~])[a-zA-Z][\w-]*/g) || []).length;
  return classes * 100 + tags;
}

function elementTokens(tag, cls) {
  return new Set([`tag:${tag}`, ...cls.split(/\s+/).filter(Boolean).map((c) => `.${c}`)]);
}

function selectorMatches(selector, element, ancestors) {
  const compounds = selector.split(/\s*[>+~]\s*|\s+/).filter(Boolean);
  const last = compoundTokens(compounds.pop());
  if (!last || ![...last].every((t) => element.has(t))) return false;
  let from = 0;
  for (const compound of compounds.reverse()) {
    const need = compoundTokens(compound);
    if (!need) return false;
    const hit = ancestors.findIndex((a, idx) => idx >= from && [...need].every((t) => a.has(t)));
    if (hit < 0) return false;
    from = hit + 1;
  }
  return true;
}

function loadCascade(page) {
  const sources = [
    ["theme", "weapp/styles/handdraw-theme.wxss"],
    ["app", "weapp/app.wxss"],
    ["page", `weapp/pages/${page}.wxss`],
  ];
  const rules = [];
  for (const [origin, rel] of sources) {
    if (fs.existsSync(path.join(ROOT, rel))) rules.push(...parseRules(read(rel), origin));
  }
  const vars = {};
  for (const rule of rules) {
    if (rule.selector !== "page") continue;
    for (const d of rule.decls) if (d.prop.startsWith("--")) vars[d.prop] = d.value;
  }
  return { rules, vars };
}

function winner(cascade, element, ancestors, props) {
  let best = null;
  let order = 0;
  for (const rule of cascade.rules) {
    const matched = selectorMatches(rule.selector, element, ancestors);
    for (const d of rule.decls) {
      order += 1;
      if (!matched || !props.includes(d.prop)) continue;
      const prio = [d.important ? 1 : 0, specificity(rule.selector), order];
      if (!best || prio[0] > best.prio[0] || (prio[0] === best.prio[0] && (prio[1] > best.prio[1]
        || (prio[1] === best.prio[1] && prio[2] > best.prio[2])))) {
        best = { prio, value: d.value, selector: rule.selector, origin: rule.origin };
      }
    }
  }
  return best;
}

function parseColor(value, vars) {
  let v = value;
  for (let n = 0; n < 3; n += 1) v = v.replace(/var\((--[\w-]+)\)/, (_, name) => vars[name] || "");
  if (/gradient/.test(v)) {
    const first = v.match(/#[0-9a-fA-F]{6}\b/);
    return first ? parseColor(first[0], vars) : null;
  }
  const hex = v.match(/#([0-9a-fA-F]{6})\b/);
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16)).concat(1);
  const rgba = v.match(/rgba?\(([^)]*)\)/);
  if (rgba) {
    const parts = rgba[1].split(",").map((x) => parseFloat(x));
    return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
  }
  if (/transparent|none/.test(v)) return [0, 0, 0, 0];
  return null;
}

function over(fg, bg) {
  return [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
}

function luminance(rgb) {
  const lin = rgb.slice(0, 3).map((c) => {
    const x = c / 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const CREAM = [255, 247, 237, 1];

// chain：外层到内层的 [tag, class] 列表，最后一项是目标元素；背景沿祖先叠加到奶油底。
function effectiveContrast(page, chain) {
  const cascade = loadCascade(page);
  const nodes = chain.map(([tag, cls]) => elementTokens(tag, cls));
  let base = CREAM;
  for (let i = 0; i < nodes.length; i += 1) {
    const ancestors = nodes.slice(0, i).reverse();
    const bg = winner(cascade, nodes[i], ancestors, ["background", "background-color"]);
    const rgba = bg && parseColor(bg.value, cascade.vars);
    if (rgba && rgba[3] > 0) base = over(rgba, base);
  }
  // color 可继承：目标未命中时取最近祖先的生效色。
  let fg = null;
  for (let i = nodes.length - 1; i >= 0 && !fg; i -= 1) {
    fg = winner(cascade, nodes[i], nodes.slice(0, i).reverse(), ["color"]);
  }
  assert.ok(fg, `${page} ${chain.map((c) => c[1]).join(" > ")} 未命中 color`);
  const rgba = parseColor(fg.value, cascade.vars);
  return { ratio: contrast(over(rgba, base), base), color: fg.value, selector: fg.selector };
}

const TEXT_CASES = [
  ["market/market", [["view", "page market-page"], ["view", "gallery-notice"], ["text", ""]]],
  ["market/market", [["view", "gallery-notice"], ["button", "state-button"]]],
  ["market/market", [["view", "gallery-card"], ["text", "card-arrow"]]],
  ["market/market", [["view", "preview-placeholder"]]],
  ["market/detail", [["view", "preview-placeholder"]]],
  ["market/detail", [["text", "eyebrow"]]],
  ["chat/chat", [["view", "page chat-page"], ["view", "msg-failed"]]],
  ["chat/chat", [["view", "page chat-page"], ["view", "voice-status"]]],
  ["chat/chat", [["view", "page chat-page"], ["view", "voice-status cancel-status"]]],
  ["chat/chat", [["view", "page chat-page"], ["view", "load-error"], ["text", ""]]],
  ["chat/chat", [["view", "page chat-page"], ["view", "hold-btn"]]],
  ["chat/chat", [["view", "page chat-page"], ["view", "hold-btn active"]]],
  ["chat/chat", [["view", "page chat-page"], ["view", "hold-btn active cancel"]]],
  ["index/index", [["view", "page"], ["text", "step-val"]]],
  ["index/index", [["view", "page"], ["text", "page-nav-label"]]],
  ["index/index", [["view", "page"], ["text", "bind-sub"]]],
  ["index/index", [["view", "page"], ["view", "muted center-text"]]],
  ["control/control", [["view", "page"], ["view", "status-bottom"]]],
  ["control/control", [["view", "page"], ["view", "operation-note"]]],
  ["control/control", [["view", "connection-step"], ["text", "step-number"]]],
  ["control/control", [["view", "page"], ["view", "connection-step"]]],
  ["drawpad/drawpad", [["view", "studio"], ["view", "paper-notice"], ["button", ""]]],
  ["drawpad/drawpad", [["view", "studio"], ["view", "paper-notice"], ["text", ""]]],
  ["history/history", [["view", "page"], ["view", "weui-cell__ft row-actions"], ["button", "redraw-btn"]]],
  ["history/history", [["view", "page"], ["view", "weui-cell__ft row-actions"], ["button", "del-btn"]]],
  ["history/history", [["view", "page"], ["view", "weui-cell__ft row-actions"], ["button", "send-btn"]]],
  ["history/history", [["view", "page"], ["view", "muted"]]],
  ["history/history", [["view", "page"], ["view", "thumb thumb-placeholder"]]],
  ["share/share", [["view", "page"], ["button", "retry-btn"]]],
  ["share/share", [["view", "page"], ["button", "go-btn"]]],
  ["paper/paper", [["view", "paper-settings"], ["button", "reset-button"]]],
  ["firmware/firmware", [["view", "page"], ["view", "hero-sub"]]],
  ["firmware/firmware", [["view", "page"], ["text", "badge"]]],
  ["profile/profile", [["view", "page"], ["text", "credit-link"]]],
  ["xiaopai/xiaopai", [["view", "page"], ["view", "model-desc"]]],
];

test("单层主题：不残留旧墨蓝/朱砂层标记与废弃色", () => {
  const theme = read("weapp/styles/handdraw-theme.wxss").toLowerCase();
  for (const marker of ["旧纸本", "墨蓝", "朱砂", "奶油卖萌层"]) assert.ok(!theme.includes(marker), marker);
  for (const color of ["#26384a", "#b84c3f", "#a43e36", "#7f342e", "#a94439", "#9f3f36", "#8d3e35",
    "#81786b", "#756d61", "#5f7259", "#65785e", "rgba(38, 56, 74", "rgba(184, 76, 63"]) {
    assert.ok(!theme.includes(color), `主题残留废弃色 ${color}`);
  }
});

test("单层主题：同一选择器不重复声明同一属性（不再靠后层覆盖前层）", () => {
  const seen = new Map();
  for (const rule of parseRules(read("weapp/styles/handdraw-theme.wxss"), "theme")) {
    for (const d of rule.decls) {
      const key = `${rule.selector} { ${d.prop} }`;
      assert.ok(!seen.has(key), `重复声明 ${key}`);
      seen.set(key, true);
    }
  }
});

test("全局次要字令牌在奶油承载面上达到 WCAG AA", () => {
  const { vars } = loadCascade("index/index");
  for (const name of ["--text", "--muted"]) {
    for (const surface of ["#fff7ed", "#fffdf8", "#fdebdc"]) {
      const ratio = contrast(parseColor(vars[name], vars), parseColor(surface, vars));
      assert.ok(ratio >= 4.5, `${name} ${vars[name]} on ${surface} = ${ratio.toFixed(2)}`);
    }
  }
  if (vars["--accent-text"]) {
    const ratio = contrast(parseColor(vars["--accent-text"], vars), CREAM);
    assert.ok(ratio >= 4.5, `--accent-text = ${ratio.toFixed(2)}`);
  }
});

test("关键正文/状态文字最终生效色在奶油底上 ≥4.5:1", () => {
  const failures = [];
  for (const [page, chain] of TEXT_CASES) {
    const { ratio, color, selector } = effectiveContrast(page, chain);
    if (ratio < 4.5) failures.push(`${page} ${chain.map((c) => c[1] || c[0]).join(" > ")}: ${color} (${selector}) ${ratio.toFixed(2)}`);
  }
  assert.deepEqual(failures, []);
});

test("录音取消提示不被 .voice-status 主题色吞掉", () => {
  const cascade = loadCascade("chat/chat");
  const page = [elementTokens("view", "page chat-page")];
  const voice = winner(cascade, elementTokens("view", "voice-status"), page, ["color"]);
  const cancel = winner(cascade, elementTokens("view", "voice-status cancel-status"), page, ["color"]);
  assert.notEqual(cancel.value, voice.value);
  assert.match(cancel.selector, /cancel-status/);
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
