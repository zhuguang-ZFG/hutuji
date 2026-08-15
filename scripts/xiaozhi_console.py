"""小智控制台 API 客户端：自动更新智能体角色提示与知识库。

用法:
    # 首次获取 token（浏览器控制台输入 localStorage.getItem('token')）
    set XIAOZHI_TOKEN=eyJhbG...

    # 查看智能体列表
    python scripts/xiaozhi_console.py list

    # 查看指定智能体配置
    python scripts/xiaozhi_console.py show <agent_id>

    # 一键同步（角色提示 + 知识库文档替换）
    python scripts/xiaozhi_console.py sync <agent_id>

    # 单独更新角色提示
    python scripts/xiaozhi_console.py update-role <agent_id>

    # 单独替换知识库文档（删旧上新）
    python scripts/xiaozhi_console.py update-kb <kb_id>

    # 查看知识库列表
    python scripts/xiaozhi_console.py kb-list

    # 知识库召回测试
    python scripts/xiaozhi_console.py recall <kb_id> "画一只猫"

    # 设备侧 MCP 工具列表（设备消息 token，不是控制台 JWT）
    python scripts/xiaozhi_console.py device-tools

    # 直接调用白名单内的设备侧工具
    python scripts/xiaozhi_console.py device-call hutuji.status
    python scripts/xiaozhi_console.py device-call hutuji.draw '{"url":"https://.../files/...gcode"}'

环境变量:
    XIAOZHI_TOKEN  — Bearer token（必须；浏览器控制台 localStorage.getItem('token')）
    XIAOZHI_DEVICE_TOKEN — 设备消息 Bearer token（自定义主题 URL 的 token 参数）
    XIAOZHI_API    — API 基址（默认 https://xiaozhi.me/api）

退出码（TOOL-UX-03 统一口径）:
    0 — 成功
    1 — 运行期错误（缺 token / 网络 / API 错误 / 文件不存在 / 应答形状不符 /
        知识库删除失败等）
    2 — 用法或参数错误（无命令、未知命令、子命令缺参数、kb_id 非整数、
        device-call 参数非法）
"""

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLE_PROMPT_PATH = REPO_ROOT / "docs" / "xiaozhi-agent" / "role-prompt.md"
KNOWLEDGE_BASE_PATH = REPO_ROOT / "docs" / "xiaozhi-agent" / "knowledge-base.md"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_DEVICE_TOOL_ARGUMENTS = {
    "hutuji.status": frozenset(),
    "hutuji.draw": frozenset({"url"}),
    "hutuji.abort": frozenset(),
    "hutuji.pause": frozenset(),
    "hutuji.resume": frozenset(),
    "hutuji.repeat": frozenset(),
    "hutuji.pen_test": frozenset(),
}
_CAPABILITY_URL_RE = re.compile(
    r"(?P<prefix>/files/)draw_\d{8}_\d{6}_[A-Za-z0-9_-]{22}\.gcode"
)
_TOKEN_FIELD_RE = re.compile(
    r'(?i)("?(?:token|api[_-]?key|authorization)"?\s*[:=]\s*)'
    r'("[^"]*"|[^,\s}]+)'
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SK_KEY_RE = re.compile(r"(?i)(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{8,}")
# 控制台 JWT 形状（header.payload.signature，base64url 三段）。字段名白名单
# （token/api_key/authorization）不覆盖非标准回显名（如 "jwt"/"credential"/裸 data），
# 按形状兜底：任何 eyJ...x.y.z 形态的凭据串整体涂红。
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def _validate_api_base(value: str) -> str:
    """控制台 token 只允许发往 HTTPS；本机测试允许 loopback HTTP。"""
    value = value.strip()
    if "?" in value or "#" in value:
        raise ValueError("XIAOZHI_API 不允许 query/fragment")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("XIAOZHI_API 端口无效") from exc
    if not host or parsed.username or parsed.password:
        raise ValueError("XIAOZHI_API 必须是无凭据的绝对 URL")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("XIAOZHI_API scheme 不受支持")
    if host not in _LOOPBACK_HOSTS and parsed.scheme != "https":
        raise ValueError("公网 XIAOZHI_API 必须使用 HTTPS")
    return value.rstrip("/")


def _url_origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    port = parsed.port
    if port is None:
        if parsed.scheme.lower() == "https":
            port = 443
        elif parsed.scheme.lower() == "http":
            port = 80
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


class _SafeRedirectHandler(HTTPRedirectHandler):
    """跨 origin 重定向必须剥掉凭据；同 origin 兼容 API 的路径规范化。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and _url_origin(req.full_url) != _url_origin(newurl):
            for name in ("Authorization", "Proxy-Authorization", "Cookie"):
                redirected.remove_header(name)
        return redirected


API_BASE = _validate_api_base(os.environ.get("XIAOZHI_API", "https://xiaozhi.me/api"))
_URL_OPENER = build_opener(_SafeRedirectHandler())


def get_token():
    token = os.environ.get("XIAOZHI_TOKEN", "").strip()
    if not token:
        print("错误: 未设置 XIAOZHI_TOKEN 环境变量", file=sys.stderr)
        print("获取方式: 浏览器打开 xiaozhi.me/console → 控制台输入 localStorage.getItem('token')",
              file=sys.stderr)
        sys.exit(1)
    return token


def get_device_token():
    """设备消息 token 与控制台 JWT 分离，避免拿错凭据跨接口发送。"""
    token = os.environ.get("XIAOZHI_DEVICE_TOKEN", "").strip()
    if not token:
        print("错误: 未设置 XIAOZHI_DEVICE_TOKEN 环境变量", file=sys.stderr)
        print("获取方式: 小智控制台打开设备自定义主题，读取 URL 的 token 参数",
              file=sys.stderr)
        sys.exit(1)
    return token


def api(method, path, body=None):
    token = get_token()
    url = f"{API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with _URL_OPENER.open(req, timeout=15) as resp:
            try:
                return json.loads(resp.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                # 2xx 但正文非 JSON/非 UTF-8（网关 WAF 页标 200 很常见）：
                # 与错误路径同口径收成一行，不裸抛 traceback。
                print(f"API 响应不可解析: {type(exc).__name__}", file=sys.stderr)
                sys.exit(1)
    except HTTPError as e:
        # 必须脱敏：控制台 JWT 是敏感凭据（仓规禁止入库 小智控制台token.txt）。
        # 上游网关/WAF 的错误页常把请求头或 query 回显进正文，裸打印会把 JWT 落进
        # 终端回滚缓冲与任何日志采集。孪生的 device_api 早已这样做，此处曾漏。
        body_text = _redact_text(e.read().decode(errors="replace"))
        print(f"API 错误 {e.code}: {body_text}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        # 无此分支时网络错误会裸抛 traceback；只记 reason 的类型名，与 device_api 同口径。
        print(f"API 网络错误: {type(e.reason).__name__}", file=sys.stderr)
        sys.exit(1)


def device_api(method, path, body=None):
    """调用官方设备消息 API；响应和错误正文先脱敏再输出。"""
    token = get_device_token()
    url = f"{API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with _URL_OPENER.open(req, timeout=30) as resp:
            try:
                return json.loads(resp.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                print(f"设备 API 响应不可解析: {type(exc).__name__}", file=sys.stderr)
                sys.exit(1)
    except HTTPError as exc:
        body_text = _redact_text(exc.read().decode(errors="replace"))
        print(f"设备 API 错误 {exc.code}: {body_text}", file=sys.stderr)
        sys.exit(1)
    except URLError as exc:
        print(f"设备 API 网络错误: {type(exc.reason).__name__}", file=sys.stderr)
        sys.exit(1)


def _redact_text(text: str) -> str:
    text = _CAPABILITY_URL_RE.sub(r"\g<prefix><redacted>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _TOKEN_FIELD_RE.sub(r'\1"<redacted>"', text)
    text = _JWT_RE.sub("<redacted>", text)
    return _SK_KEY_RE.sub("<redacted>", text)


def _parse_device_arguments(tool_name: str, raw: str | None) -> dict:
    expected = _DEVICE_TOOL_ARGUMENTS.get(tool_name)
    if expected is None:
        allowed = ", ".join(sorted(_DEVICE_TOOL_ARGUMENTS))
        raise ValueError(f"设备工具不在白名单：{tool_name}；允许：{allowed}")
    try:
        arguments = {} if raw is None else json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("arguments 必须是 JSON object") from exc
    if not isinstance(arguments, dict):
        raise ValueError("arguments 必须是 JSON object")
    if frozenset(arguments) != expected:
        names = ", ".join(sorted(expected)) or "无参数"
        raise ValueError(f"{tool_name} 参数必须严格为：{names}")
    if tool_name == "hutuji.draw":
        url = arguments.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("hutuji.draw 的 url 必须是非空字符串")
    return arguments


def api_upload(path, file_path):
    """multipart/form-data 文件上传。"""
    import mimetypes
    token = get_token()
    url = f"{API_BASE}{path}"
    boundary = "----XiaozhiUpload9527"
    filename = Path(file_path).name
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    with open(file_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    try:
        with _URL_OPENER.open(req, timeout=30) as resp:
            try:
                return json.loads(resp.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                print(f"上传响应不可解析: {type(exc).__name__}", file=sys.stderr)
                sys.exit(1)
    except HTTPError as e:
        # 与 api/device_api 同口径：上传错误正文可能回显 Authorization 头或能力 URL。
        body_text = _redact_text(e.read().decode(errors="replace"))
        print(f"上传错误 {e.code}: {body_text}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"上传网络错误: {type(e.reason).__name__}", file=sys.stderr)
        sys.exit(1)


def cmd_list():
    result = api("GET", "/agents")
    # API 返回形状两种：data 直接是数组，或包在 data.list 里。
    # 旧写法在 list 上 .get() 必崩、dict 无 list 键时返回整个 dict 再遍历键，
    # 两路都是 AttributeError——先归一成列表再处理。
    data = result.get("data", [])
    if isinstance(data, dict):
        agents = data.get("list", [])
    else:
        agents = data
    if not agents:
        print("未找到智能体", file=sys.stderr)
        sys.exit(1)
    for a in agents:
        aid = a.get("id", "?")
        name = a.get("agent_name", "未命名") or "未命名"
        online = a.get("onlineDeviceCount", 0)
        total = a.get("deviceCount", 0)
        model = a.get("llm_model", "?")
        kb = a.get("knowledge_base_ids", [])
        print(f"  {aid}  {name}  设备={online}/{total}  模型={model}  知识库={kb}")


def _endpoint_origin(url: str) -> str:
    """端点只显示 scheme+host（origin），丢弃 query/fragment/userinfo——token 常在 query 里。"""
    if not isinstance(url, str):
        return "<redacted>"
    try:
        parsed = urlparse(url)
        if not (parsed.scheme and parsed.hostname):
            return "<redacted>"
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        origin = f"{parsed.scheme}://{host}"
        if parsed.port is not None:
            origin += f":{parsed.port}"
        return origin
    except ValueError:
        return "<redacted>"


def cmd_show(agent_id):
    result = api("GET", f"/agents/{agent_id}")
    agent = result.get("data", {}).get("agent", {})
    if not agent:
        print(f"未找到智能体 {agent_id}", file=sys.stderr)
        sys.exit(1)
    print(f"ID:       {agent.get('id')}")
    print(f"名称:     {agent.get('agent_name')}")
    print(f"模型:     {agent.get('llm_model')}")
    print(f"语音:     {agent.get('tts_voice')}")
    print(f"语言:     {agent.get('language')}")
    print(f"记忆:     {agent.get('memory_type')}")
    char = agent.get("character", "")
    if char:
        print(f"角色提示: {char[:80]}...")
        print(f"          ({len(char)} 字符)")
    else:
        print("角色提示: (空)")
    print(f"知识库:   {agent.get('knowledge_base_ids', [])}")
    endpoints = agent.get("mcp_endpoints") or []
    shown = ", ".join(_endpoint_origin(e) for e in endpoints) if endpoints else "[]"
    print(f"MCP 端点: {shown}")
    print(f"设备:     {agent.get('deviceCount', 0)} 台（在线 {agent.get('onlineDeviceCount', 0)}）")


def cmd_update_role(agent_id):
    if not ROLE_PROMPT_PATH.exists():
        print(f"文件不存在: {ROLE_PROMPT_PATH}", file=sys.stderr)
        sys.exit(1)

    lines = ROLE_PROMPT_PATH.read_text(encoding="utf-8").strip().split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    role_text = "\n".join(lines).strip()

    print(f"读取 {ROLE_PROMPT_PATH.name}: {len(role_text)} 字符")

    result = api("POST", f"/agents/{agent_id}/config", {"character": role_text})
    if not result.get("success"):
        print(f"✗ 更新失败: {result.get('message', '?')}", file=sys.stderr)
        sys.exit(1)
    print("✓ 角色提示更新成功")


def cmd_update_kb(kb_id):
    try:
        kb_id_int = int(kb_id)
    except ValueError:
        # kb_id 非整数是参数错误，不是运行期故障：退出码 2（TOOL-UX-03 统一口径）。
        print(f"kb_id 必须是整数: {kb_id!r}", file=sys.stderr)
        sys.exit(2)
    if not KNOWLEDGE_BASE_PATH.exists():
        print(f"文件不存在: {KNOWLEDGE_BASE_PATH}", file=sys.stderr)
        sys.exit(1)

    # 先记下现有文档，**上传 + 建档成功后才删旧**：任一步失败时远端仍保留旧知识库。
    # 旧实现是「先删全部再上传」，上传失败即把远端知识库清空，且不可逆。
    docs_result = api("GET", f"/knowledge-bases/document?knowledgeBaseId={kb_id_int}&page=1&pageSize=50")
    old_docs = docs_result.get("data", {}).get("list", [])
    print(f"现有文档 {len(old_docs)} 篇；先上传新文件，成功后再删旧")

    # 上传新文件
    print(f"上传 {KNOWLEDGE_BASE_PATH.name}...")
    upload_result = api_upload("/knowledge-bases/upload-file", str(KNOWLEDGE_BASE_PATH))
    if not upload_result.get("success"):
        print(f"✗ 上传失败: {upload_result.get('message', '?')}（旧文档保留）", file=sys.stderr)
        sys.exit(1)

    upload_data = upload_result.get("data") or {}
    if "file_name" not in upload_data or "file_md5" not in upload_data:
        # success=true 却缺 file_name/file_md5：应答形状不符。裸取 upload_result["data"]
        # 会 KeyError traceback；与 api() 的单行脱敏风格同口径收成一行（F-XZ-08）。
        print("✗ 上传应答缺少 file_name/file_md5（旧文档保留）", file=sys.stderr)
        sys.exit(1)
    file_name = upload_data["file_name"]
    file_md5 = upload_data["file_md5"]

    # 创建文档
    create_result = api("POST", "/knowledge-bases/document", {
        "knowledgeBaseId": kb_id_int,
        "fileName": file_name,
        "fileMd5": file_md5,
        "optimizeParse": False,
    })
    if not create_result.get("success"):
        print(f"✗ 创建文档失败: {create_result.get('message', '?')}（旧文档保留）", file=sys.stderr)
        sys.exit(1)
    print(f"✓ 新文档已建立 (md5={file_md5[:12]}...)")

    # 新文档就位后才清理旧文档；删除失败不丢数据（新旧并存），但要非零退出提示人工清理
    delete_failed = []
    for doc in old_docs:
        doc_id = doc["id"]
        title = doc.get("title", "?")
        print(f"删除旧文档: {title} (id={doc_id})")
        del_result = api("DELETE", f"/knowledge-bases/document?knowledgeBaseId={kb_id_int}&documentId={doc_id}")
        # api() 对 HTTP 错误已 sys.exit；这里只拦「HTTP 200 但 success=false」
        if isinstance(del_result, dict) and del_result.get("success") is False:
            delete_failed.append(f"{title}(id={doc_id})")
    if delete_failed:
        # 汇总提示「新旧并存需人工清理」：新文档已建档、部分旧文档没删掉，远端是
        # 新旧两版并存的状态——不点明并存，操作员会以为只是「有一条没删干净」。
        print(f"✗ 旧文档删除失败，新旧并存需人工清理: {delete_failed}", file=sys.stderr)
        sys.exit(1)
    print("✓ 知识库文档替换成功")


def cmd_kb_list():
    result = api("GET", "/knowledge-bases?page=1&pageSize=100")
    bases = result.get("data", {}).get("list", [])
    if not bases:
        print("未找到知识库", file=sys.stderr)
        sys.exit(1)
    for kb in bases:
        kb_id = kb.get("id", "?")
        name = kb.get("name", "未命名")
        doc_count = kb.get("document_count", "?")
        print(f"  {kb_id}  {name}  文档数={doc_count}")


def cmd_recall(kb_id, query):
    try:
        kb_id_int = int(kb_id)
    except ValueError:
        # 参数错误退出码 2（TOOL-UX-03 统一口径）。
        print(f"kb_id 必须是整数: {kb_id!r}", file=sys.stderr)
        sys.exit(2)
    result = api("POST", "/knowledge-bases/recall-test", {
        "knowledgeBaseId": kb_id_int,
        "query": query,
    })
    if not result.get("success"):
        print(f"召回失败: {result.get('message', '?')}", file=sys.stderr)
        sys.exit(1)
    data = result.get("data", [])
    chunks = data.get("chunks", data) if isinstance(data, dict) else data
    if isinstance(chunks, list):
        print(f"召回 {len(chunks)} 条：")
        for i, c in enumerate(chunks):
            content = str(c.get("content", c) if isinstance(c, dict) else c)
            preview = content[:100].replace("\n", " ")
            score = c.get("score", "") if isinstance(c, dict) else ""
            print(f"  [{i+1}] {score}  {preview}...")
    else:
        print(f"召回结果: {chunks}")


def cmd_device_tools():
    result = device_api("POST", "/messaging/device/tools/list")
    print(_redact_text(json.dumps(result, ensure_ascii=False, indent=2)))


def cmd_device_call(tool_name: str, raw_arguments: str | None = None):
    try:
        arguments = _parse_device_arguments(tool_name, raw_arguments)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(2)
    result = device_api("POST", "/messaging/device/tools/call", {
        "name": tool_name,
        "arguments": arguments,
    })
    print(_redact_text(json.dumps(result, ensure_ascii=False, indent=2)))


def cmd_sync(agent_id):
    print("=== 同步呼图机智能体 ===\n")

    # 查配置
    result = api("GET", f"/agents/{agent_id}")
    agent = result.get("data", {}).get("agent", {})
    if not agent:
        print(f"未找到智能体 {agent_id}", file=sys.stderr)
        sys.exit(1)

    name = agent.get("agent_name", "?")
    kb_ids = agent.get("knowledge_base_ids", [])
    print(f"智能体: {name} (id={agent_id})")
    print(f"知识库: {kb_ids}\n")

    # 更新角色提示
    print("[1/2] 更新角色提示...")
    cmd_update_role(agent_id)
    print()

    # 更新知识库
    if kb_ids:
        print(f"[2/2] 替换知识库文档 (kb_id={kb_ids[0]})...")
        cmd_update_kb(str(kb_ids[0]))
    else:
        print("[2/2] 未关联知识库，跳过")

    print("\n=== 同步完成 ===")


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        # 无命令 = 用法错误：退出码 2（TOOL-UX-03 统一口径，与未知命令/缺参数一致）。
        print(__doc__)
        sys.exit(2)

    cmd = sys.argv[1]

    if cmd == "list":
        cmd_list()
    elif cmd == "show":
        if len(sys.argv) < 3:
            print("用法: show <agent_id>", file=sys.stderr)
            sys.exit(2)
        cmd_show(sys.argv[2])
    elif cmd == "update-role":
        if len(sys.argv) < 3:
            print("用法: update-role <agent_id>", file=sys.stderr)
            sys.exit(2)
        cmd_update_role(sys.argv[2])
    elif cmd == "update-kb":
        if len(sys.argv) < 3:
            print("用法: update-kb <kb_id>", file=sys.stderr)
            sys.exit(2)
        cmd_update_kb(sys.argv[2])
    elif cmd == "kb-list":
        cmd_kb_list()
    elif cmd == "recall":
        if len(sys.argv) < 4:
            print("用法: recall <kb_id> <query>", file=sys.stderr)
            sys.exit(2)
        cmd_recall(sys.argv[2], sys.argv[3])
    elif cmd == "device-tools":
        cmd_device_tools()
    elif cmd == "device-call":
        if len(sys.argv) not in {3, 4}:
            print("用法: device-call <tool_name> [arguments_json]", file=sys.stderr)
            sys.exit(2)
        raw_arguments = sys.argv[3] if len(sys.argv) == 4 else None
        cmd_device_call(sys.argv[2], raw_arguments)
    elif cmd == "sync":
        if len(sys.argv) < 3:
            print("用法: sync <agent_id>", file=sys.stderr)
            sys.exit(2)
        cmd_sync(sys.argv[2])
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
