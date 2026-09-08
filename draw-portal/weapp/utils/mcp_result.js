// 小智设备 tools/call 回包解包（与 draw_portal.mcp_result.unwrap_tool_text 同口径）
// 现网：{"success":true,"data":{connected,ready,...}}
// 兼容：{"content":[{"type":"text","text":"<payload>"}],"isError":false}

function unwrapToolText(raw) {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    // 偶发：上层已 JSON.parse 成对象
    return _unwrapObject(raw);
  }
  if (typeof raw !== "string" || !raw) return raw;
  var data;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    return raw;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return raw;
  var unwrapped = _unwrapObject(data);
  if (typeof unwrapped === "string") return unwrapped;
  try {
    return JSON.stringify(unwrapped);
  } catch (e2) {
    return raw;
  }
}

function _unwrapObject(data) {
  if (data.data !== undefined && (data.success === true || typeof data.data === "object" || typeof data.data === "string")) {
    var payload = data.data;
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      if (typeof payload.text === "string" && payload.connected === undefined) {
        return payload.text;
      }
      return payload;
    }
    if (typeof payload === "string") return payload;
  }
  var content = data.content;
  if (Array.isArray(content) && content.length) {
    var first = content[0];
    if (first && typeof first.text === "string") return first.text;
  }
  if (typeof content === "string") return content;
  return data;
}

function parseToolJson(raw) {
  var textOrObj = unwrapToolText(raw);
  if (textOrObj && typeof textOrObj === "object" && !Array.isArray(textOrObj)) {
    return textOrObj;
  }
  if (typeof textOrObj !== "string") return null;
  try {
    var obj = JSON.parse(textOrObj);
    return obj && typeof obj === "object" && !Array.isArray(obj) ? obj : null;
  } catch (e2) {
    return null;
  }
}

module.exports = { unwrapToolText: unwrapToolText, parseToolJson: parseToolJson };
