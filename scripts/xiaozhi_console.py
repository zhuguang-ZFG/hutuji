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

环境变量:
    XIAOZHI_TOKEN  — Bearer token（必须；浏览器控制台 localStorage.getItem('token')）
    XIAOZHI_API    — API 基址（默认 https://xiaozhi.me/api）
"""

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API_BASE = os.environ.get("XIAOZHI_API", "https://xiaozhi.me/api")
REPO_ROOT = Path(__file__).resolve().parent.parent
ROLE_PROMPT_PATH = REPO_ROOT / "docs" / "xiaozhi-agent" / "role-prompt.md"
KNOWLEDGE_BASE_PATH = REPO_ROOT / "docs" / "xiaozhi-agent" / "knowledge-base.md"


def get_token():
    token = os.environ.get("XIAOZHI_TOKEN", "").strip()
    if not token:
        print("错误: 未设置 XIAOZHI_TOKEN 环境变量", file=sys.stderr)
        print("获取方式: 浏览器打开 xiaozhi.me/console → 控制台输入 localStorage.getItem('token')",
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
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body_text = e.read().decode(errors="replace")
        print(f"API 错误 {e.code}: {body_text}", file=sys.stderr)
        sys.exit(1)


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
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body_text = e.read().decode(errors="replace")
        print(f"上传错误 {e.code}: {body_text}", file=sys.stderr)
        sys.exit(1)


def cmd_list():
    result = api("GET", "/agents")
    agents = result.get("data", result.get("data", {}) if isinstance(result.get("data"), dict) else {}).get("list", result.get("data", []))
    if isinstance(result.get("data"), list):
        agents = result["data"]
    if not agents:
        print("未找到智能体")
        return
    for a in agents:
        aid = a.get("id", "?")
        name = a.get("agent_name", "未命名") or "未命名"
        online = a.get("onlineDeviceCount", 0)
        total = a.get("deviceCount", 0)
        model = a.get("llm_model", "?")
        kb = a.get("knowledge_base_ids", [])
        print(f"  {aid}  {name}  设备={online}/{total}  模型={model}  知识库={kb}")


def cmd_show(agent_id):
    result = api("GET", f"/agents/{agent_id}")
    agent = result.get("data", {}).get("agent", {})
    if not agent:
        print(f"未找到智能体 {agent_id}")
        return
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
    print(f"MCP 端点: {agent.get('mcp_endpoints', [])}")
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
    if not KNOWLEDGE_BASE_PATH.exists():
        print(f"文件不存在: {KNOWLEDGE_BASE_PATH}", file=sys.stderr)
        sys.exit(1)

    # 先记下现有文档，**上传 + 建档成功后才删旧**：任一步失败时远端仍保留旧知识库。
    # 旧实现是「先删全部再上传」，上传失败即把远端知识库清空，且不可逆。
    docs_result = api("GET", f"/knowledge-bases/document?knowledgeBaseId={kb_id}&page=1&pageSize=50")
    old_docs = docs_result.get("data", {}).get("list", [])
    print(f"现有文档 {len(old_docs)} 篇；先上传新文件，成功后再删旧")

    # 上传新文件
    print(f"上传 {KNOWLEDGE_BASE_PATH.name}...")
    upload_result = api_upload("/knowledge-bases/upload-file", str(KNOWLEDGE_BASE_PATH))
    if not upload_result.get("success"):
        print(f"✗ 上传失败: {upload_result.get('message', '?')}（旧文档保留）", file=sys.stderr)
        sys.exit(1)

    file_name = upload_result["data"]["file_name"]
    file_md5 = upload_result["data"]["file_md5"]

    # 创建文档
    create_result = api("POST", "/knowledge-bases/document", {
        "knowledgeBaseId": int(kb_id),
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
        del_result = api("DELETE", f"/knowledge-bases/document?knowledgeBaseId={kb_id}&documentId={doc_id}")
        # api() 对 HTTP 错误已 sys.exit；这里只拦「HTTP 200 但 success=false」
        if isinstance(del_result, dict) and del_result.get("success") is False:
            delete_failed.append(f"{title}(id={doc_id})")
    if delete_failed:
        print(f"✗ 旧文档删除失败，需人工清理: {delete_failed}", file=sys.stderr)
        sys.exit(1)
    print("✓ 知识库文档替换成功")


def cmd_kb_list():
    result = api("GET", "/knowledge-bases?page=1&pageSize=100")
    bases = result.get("data", {}).get("list", [])
    if not bases:
        print("未找到知识库")
        return
    for kb in bases:
        kb_id = kb.get("id", "?")
        name = kb.get("name", "未命名")
        doc_count = kb.get("document_count", "?")
        print(f"  {kb_id}  {name}  文档数={doc_count}")


def cmd_recall(kb_id, query):
    result = api("POST", "/knowledge-bases/recall-test", {
        "knowledgeBaseId": int(kb_id),
        "query": query,
    })
    if not result.get("success"):
        print(f"召回失败: {result.get('message', '?')}", file=sys.stderr)
        return
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
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "list":
        cmd_list()
    elif cmd == "show":
        if len(sys.argv) < 3:
            print("用法: show <agent_id>", file=sys.stderr)
            sys.exit(1)
        cmd_show(sys.argv[2])
    elif cmd == "update-role":
        if len(sys.argv) < 3:
            print("用法: update-role <agent_id>", file=sys.stderr)
            sys.exit(1)
        cmd_update_role(sys.argv[2])
    elif cmd == "update-kb":
        if len(sys.argv) < 3:
            print("用法: update-kb <kb_id>", file=sys.stderr)
            sys.exit(1)
        cmd_update_kb(sys.argv[2])
    elif cmd == "kb-list":
        cmd_kb_list()
    elif cmd == "recall":
        if len(sys.argv) < 4:
            print("用法: recall <kb_id> <query>", file=sys.stderr)
            sys.exit(1)
        cmd_recall(sys.argv[2], sys.argv[3])
    elif cmd == "sync":
        if len(sys.argv) < 3:
            print("用法: sync <agent_id>", file=sys.stderr)
            sys.exit(1)
        cmd_sync(sys.argv[2])
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
