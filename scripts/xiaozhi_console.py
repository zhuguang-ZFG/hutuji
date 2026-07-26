"""小智控制台 API 客户端：自动更新智能体角色提示与知识库。

用法:
    # 首次获取 token（浏览器 F12 → Network → 任意 API 请求的 Authorization 头）
    set XIAOZHI_TOKEN=eyJhbG...

    # 查看当前智能体列表
    python scripts/xiaozhi_console.py list

    # 查看指定智能体的当前配置
    python scripts/xiaozhi_console.py show <agent_id>

    # 更新角色提示（从 docs/xiaozhi-agent/role-prompt.md 读取）
    python scripts/xiaozhi_console.py update-role <agent_id>

    # 查看知识库列表
    python scripts/xiaozhi_console.py kb-list

    # 全量更新（角色提示 + 显示知识库状态）
    python scripts/xiaozhi_console.py sync <agent_id>

环境变量:
    XIAOZHI_TOKEN  — Bearer token（必须）
    XIAOZHI_API    — API 基址（默认 https://xiaozhi.me/api）
"""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

API_BASE = os.environ.get("XIAOZHI_API", "https://xiaozhi.me/api")
REPO_ROOT = Path(__file__).resolve().parent.parent
ROLE_PROMPT_PATH = REPO_ROOT / "docs" / "xiaozhi-agent" / "role-prompt.md"
KNOWLEDGE_BASE_PATH = REPO_ROOT / "docs" / "xiaozhi-agent" / "knowledge-base.md"


def get_token():
    token = os.environ.get("XIAOZHI_TOKEN", "").strip()
    if not token:
        print("错误: 未设置 XIAOZHI_TOKEN 环境变量", file=sys.stderr)
        print("获取方式: 浏览器打开 xiaozhi.me/console → F12 → Network → 复制 Authorization 头的 Bearer 值",
              file=sys.stderr)
        sys.exit(1)
    return token


def api(method, path, body=None):
    token = get_token()
    url = f"{API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        body_text = e.read().decode(errors="replace")
        print(f"API 错误 {e.code}: {body_text}", file=sys.stderr)
        sys.exit(1)


def cmd_list():
    result = api("GET", "/agents")
    agents = result.get("data", {}).get("list", [])
    if not agents:
        print("未找到智能体")
        return
    print(f"{'ID':<8} {'名称':<20} {'在线设备':<10} {'模型'}")
    print("-" * 60)
    for a in agents:
        agent_id = a.get("id", "?")
        name = a.get("agent_name", "未命名") or "未命名"
        online = a.get("onlineDeviceCount", 0)
        total = a.get("deviceCount", 0)
        model = a.get("llm_model", "?")
        print(f"{agent_id:<8} {name:<20} {online}/{total:<8} {model}")


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
    print(f"青少年:   {agent.get('teen_mode')}")
    char = agent.get("character", "")
    if char:
        preview = char[:100] + ("..." if len(char) > 100 else "")
        print(f"角色提示: {preview}")
        print(f"          ({len(char)} 字符)")
    else:
        print("角色提示: (空)")
    kb_ids = agent.get("knowledge_base_ids", [])
    print(f"知识库ID: {kb_ids if kb_ids else '(无)'}")
    mcp = agent.get("mcp_endpoints", [])
    print(f"MCP 端点: {mcp if mcp else '(无)'}")


def cmd_update_role(agent_id):
    if not ROLE_PROMPT_PATH.exists():
        print(f"角色提示文件不存在: {ROLE_PROMPT_PATH}", file=sys.stderr)
        sys.exit(1)

    content = ROLE_PROMPT_PATH.read_text(encoding="utf-8").strip()
    # 去掉 markdown 标题行（控制台不需要）
    lines = content.split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    role_text = "\n".join(lines).strip()

    print(f"读取角色提示: {len(role_text)} 字符")
    print(f"前 80 字: {role_text[:80]}...")

    result = api("POST", f"/agents/{agent_id}/config", {
        "character": role_text,
    })
    if result.get("success"):
        print("角色提示更新成功")
    else:
        print(f"更新失败: {result.get('message', '未知错误')}", file=sys.stderr)


def cmd_kb_list(agent_id=None):
    params = ""
    if agent_id:
        params = f"?agent_template_id={agent_id}"
    result = api("GET", f"/knowledge-bases/enum{params}")
    bases = result.get("data", {}).get("list", [])
    if not bases:
        print("未找到知识库")
        return
    print(f"{'ID':<8} {'名称':<30} {'类型':<10}")
    print("-" * 50)
    for kb in bases:
        kb_id = kb.get("id", "?")
        name = kb.get("name", "未命名")
        kb_type = kb.get("knowledge_base_type", "?")
        print(f"{kb_id:<8} {name:<30} {kb_type:<10}")


def cmd_sync(agent_id):
    print("=== 同步智能体配置 ===\n")

    print("[1/3] 查看当前配置...")
    cmd_show(agent_id)
    print()

    print("[2/3] 更新角色提示...")
    cmd_update_role(agent_id)
    print()

    print("[3/3] 知识库状态（需在控制台手动上传/关联）...")
    cmd_kb_list()
    print()

    if KNOWLEDGE_BASE_PATH.exists():
        kb_text = KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8")
        print(f"知识库文件: {KNOWLEDGE_BASE_PATH} ({len(kb_text)} 字符)")
        print("提示: 知识库内容需在小智控制台手动上传（API 暂不支持文件上传）")

    print("\n=== 同步完成 ===")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "list":
        cmd_list()
    elif cmd == "show":
        if len(sys.argv) < 3:
            print("用法: xiaozhi_console.py show <agent_id>", file=sys.stderr)
            sys.exit(1)
        cmd_show(sys.argv[2])
    elif cmd == "update-role":
        if len(sys.argv) < 3:
            print("用法: xiaozhi_console.py update-role <agent_id>", file=sys.stderr)
            sys.exit(1)
        cmd_update_role(sys.argv[2])
    elif cmd == "kb-list":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_kb_list(agent_id)
    elif cmd == "sync":
        if len(sys.argv) < 3:
            print("用法: xiaozhi_console.py sync <agent_id>", file=sys.stderr)
            sys.exit(1)
        cmd_sync(sys.argv[2])
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
