#!/usr/bin/env bash
# 上线前/变更后本机冒烟（不打印密钥、不访问公网 MCP）。
set -euo pipefail

EXPECTED_CHECKS=22
PASS=0
FAIL=0

# 只有 pass / fail 两种状态，没有 skip。
# 历史上有过 OPTIONAL_SKIP(125) 机制：判据取不到前提条件时返回它，计为 skip。
# 但门禁算术是 (PASS+SKIP)==EXPECTED_CHECKS，skip 与 pass 在放行上完全等价——
# 于是「公网访问 :80 /files 被拒」这条铁律可以从未被验证过，脚本却打印 15/15 全绿
# （已实测：pass=14 fail=0 skip=1 照样输出 PASS）。任何「这项没法验」都必须是红，
# 由人确认，不得由脚本自行豁免。请勿重新引入 skip 状态。
check() {
  local name="$1"; shift
  if "$@"; then
    echo "[pass] ${name}"
    PASS=$((PASS + 1))
  else
    echo "[fail] ${name}"
    FAIL=$((FAIL + 1))
  fi
}

# 只按 systemd EnvironmentFile 的简单 KEY=VALUE 语法读取目标键：不 source、不 eval，
# 重复键按 systemd 的最后一次赋值生效；引号只去除成对的首尾单/双引号。
env_value() {
  local file="$1" key="$2" line name value
  ENV_VALUE=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#${line%%[![:space:]]*}}"
    [[ -z "$line" || "${line:0:1}" == "#" || "${line:0:1}" == ";" ]] && continue
    [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
    name="${BASH_REMATCH[1]}"
    [[ "$name" == "$key" ]] || continue
    value="${BASH_REMATCH[2]}"
    while [[ "$value" == *[[:space:]] ]]; do value="${value%?}"; done
    if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' && ${#value} -ge 2 ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" && ${#value} -ge 2 ]]; then
      value="${value:1:${#value}-2}"
    fi
    ENV_VALUE="$value"
  done < "$file"
}

check_no_placeholder() {
  local file="$1" rc
  [[ -r "$file" ]] || return 1
  if grep -q REPLACE_ME "$file"; then
    return 1
  else
    rc=$?
    [[ "$rc" -eq 1 ]]
  fi
}

check_public_base_url() {
  local value="${1,,}" host
  [[ "$value" =~ ^https://([a-z0-9-]+\.)*[a-z0-9-]+(:443)?/?$ ]] || return 1
  host="${value#https://}"; host="${host%/}"; host="${host%:443}"
  # loopback 与私网都不是公网可达前缀：S3 拿到它下载必失败。旧版只排除
  # localhost / 127.*，于是 https://192.168.1.5、https://10.0.0.8、https://intranet
  # 全部报 pass（已实测），属「门禁绿但功能坏」。
  case "$host" in
    localhost|127.*|0.0.0.0) return 1 ;;
    10.*|192.168.*|169.254.*) return 1 ;;
    172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 1 ;;
  esac
  # 单标签主机名（无点）不可能是公网 DNS 名，如 https://intranet。
  [[ "$host" == *.* ]]
}

safe_output_dir() {
  local value="$1" resolved
  [[ "$value" == /opt/* ]] || return 1
  resolved="$(realpath -m -- "$value" 2>/dev/null)" || return 1
  [[ "$resolved" == /opt/* ]]
}

check_mcp_env() {
  [[ -f /etc/hutuji/mcp.env ]] && check_no_placeholder /etc/hutuji/mcp.env &&
    [[ -n "$LLM_BASE_URL" && -n "$LLM_API_KEY" && -n "$LLM_MODEL" ]] &&
    check_public_base_url "$PUBLIC_BASE_URL" &&
    safe_output_dir "$OUTPUT_DIR"
}

check_bridge_ready() {
  local pid ready_pid
  systemctl is-active --quiet hutuji-cloud-bridge || return 1
  pid="$(systemctl show --property=MainPID --value hutuji-cloud-bridge)"
  [[ "$pid" =~ ^[1-9][0-9]*$ && -r /run/hutuji/cloud-bridge.ready ]] || return 1
  ready_pid="$(tr -d '[:space:]' < /run/hutuji/cloud-bridge.ready)"
  [[ "$ready_pid" == "$pid" ]]
}

check_bridge_env() {
  [[ -f /etc/hutuji/bridge.env ]] && check_no_placeholder /etc/hutuji/bridge.env || return 1
  # endpoint 常带 token，走 stdin 避免完整 URL 出现在进程命令行。
  python3 -c '
import sys
from urllib.parse import urlsplit

raw = sys.stdin.read()
value = raw[:-1] if raw.endswith("\n") else raw
try:
    parsed = urlsplit(value)
    parsed.port
except ValueError:
    raise SystemExit(1)
valid = (
    parsed.scheme == "wss"
    and bool(parsed.hostname)
    and parsed.username is None
    and parsed.password is None
    and not parsed.fragment
    and not any(ch.isspace() for ch in value)
)
raise SystemExit(0 if valid else 1)
' <<<"$BRIDGE_ENDPOINT"
}

check_active_nginx_acl() {
  local conf site port80 files acl deny_line allow_v4_line allow_v6_line
  nginx -t >/dev/null 2>&1 || return 1
  conf="$(nginx -T 2>/dev/null)" || return 1
  site="$(awk '/^# configuration file \/etc\/nginx\/sites-enabled\/hutuji:/{inside=1; next} /^# configuration file /{if(inside) exit} inside{print}' <<<"$conf")"
  [[ -n "$site" ]] || return 1
  port80="$(sed -n '/^[[:space:]]*listen 80;[[:space:]]*$/,$p' <<<"$site")"
  files="$(sed -n '/^[[:space:]]*location \/files\/ {/,/^[[:space:]]*}/p' <<<"$port80")"
  [[ -n "$files" ]] || return 1
  # `deny all;` 在本块内出现两次：ACL 的兜底 deny，与 `limit_except … { deny all; }`
  # 行内那条。`grep -Fq 'deny all;'` 分不清二者——删掉兜底 deny 后 allow/deny 链
  # 只剩两条 allow、默认放行，`:80 /files` 对全公网可读，而 grep 仍命中 limit_except
  # 那行报 pass（已用本函数原样管道实测复现）。故先剔除 limit_except 行，只在纯
  # ACL 语句里找独立成行的兜底 deny。
  acl="$(grep -v 'limit_except' <<<"$files")"
  deny_line="$(grep -n '^[[:space:]]*deny all;[[:space:]]*$' <<<"$acl" | head -1 | cut -d: -f1 || true)"
  allow_v4_line="$(grep -n '^[[:space:]]*allow 127\.0\.0\.1;[[:space:]]*$' <<<"$acl" | head -1 | cut -d: -f1 || true)"
  allow_v6_line="$(grep -n '^[[:space:]]*allow ::1;[[:space:]]*$' <<<"$acl" | head -1 | cut -d: -f1 || true)"
  [[ -n "$deny_line" && -n "$allow_v4_line" && -n "$allow_v6_line" ]] || return 1
  # nginx allow/deny 按出现顺序匹配：兜底 deny 必须排在两条 allow 之后，否则
  # cloudflared 的 loopback 回源会被先拒，公网经隧道下载全挂。
  [[ "$allow_v4_line" -lt "$deny_line" && "$allow_v6_line" -lt "$deny_line" ]]
}

# 取本机非 loopback IPv4：依次 hostname -I / 默认路由接口。取不到即 fail（见
# check_acl_response 注释）：拿不到地址意味着「公网访问 :80 /files 被拒」这条铁律
# 无法验证，不得当作已验证。
probe_non_loopback_ip() {
  local ip
  ip="$(hostname -I 2>/dev/null | tr " " "\n" |
    awk '/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && $0 !~ /^127\./ {print; exit}')"
  if [[ -n "$ip" ]]; then
    printf '%s' "$ip"
    return 0
  fi
  # 按 dev 关键字定位接口名，不依赖字段序号：无网关的 `default dev ppp0 scope link`
  # 用 $5 会取到 "link"。
  ip="$(ip -4 route show default 2>/dev/null |
    awk '{for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i + 1); exit}}')"
  if [[ -n "$ip" ]]; then
    ip="$(ip -4 -o addr show dev "$ip" 2>/dev/null |
      awk '{split($4,a,"/"); if (a[1] !~ /^127\./) {print a[1]; exit}}')"
    if [[ -n "$ip" ]]; then
      printf '%s' "$ip"
      return 0
    fi
  fi
  return 1
}

# 用隧道 Host 头探 :80 /files 的 ACL：loopback 放行（GET 404 / POST 403 / /mcp 404），
# 非 loopback 拒绝。
#
# 非 loopback 探针是全脚本唯一真正验证「公网访问 :80 /files 被拒」的断言——
# check_active_nginx_acl 只看配置文本，配置对了不代表运行时行为对。取不到非
# loopback IPv4 时**必须 fail**，不能 skip：旧版返回 OPTIONAL_SKIP，而门禁算术
# 是 (PASS+SKIP)==EXPECTED，skip 与 pass 等价，于是这条铁律可以从未被验证过却
# 报 15/15 全绿。宁可在无公网 IPv4 的机器上显式红、由运维确认，也不要静默放过。
check_acl_response() {
  local h="$1" get post mcp ip denied
  [[ -n "$h" ]] || return 1
  get=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -H "Host: $h" http://127.0.0.1/files/__nonexistent__)
  post=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -X POST -H "Host: $h" http://127.0.0.1/files/__nonexistent__)
  mcp=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -H "Host: $h" http://127.0.0.1/mcp)
  [[ "$get" == "404" && "$post" == "403" && "$mcp" == "404" ]] || return 1
  ip="$(probe_non_loopback_ip || true)"
  if [[ -z "$ip" ]]; then
    echo "取不到非 loopback IPv4：无法验证公网 :80 /files 被拒，判 fail（不得跳过）" >&2
    return 1
  fi
  denied=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -H "Host: $h" "http://$ip/files/__nonexistent__")
  [[ "$denied" == "403" ]]
}

# 两个 unit 与固定模板一致，且 systemd 当前加载并启动的就是本次部署版本。
#
# 此前只有「运行中 nginx conf 与模板一致」这一项，两个 unit 却没有对应判据——而它们
# 的安装方式完全同构（`install-from-repo.sh:58-59` 从 `/opt/hutuji/deploy/` 复制到
# `/etc/systemd/system/`）。`systemctl is-active` 只证明「在跑」，不证明跑的是哪一版：
# unit 里的 `--host 127.0.0.1`、`ProtectSystem=strict`、`SystemCallFilter` 等加固项
# 全部可以缺失而 active 照样绿（监听面另有第 5/6 项覆盖，加固项则无人验）。
#
# 前四条判据缺一不可，每条堵一种「文件对了但跑的不对」：
#   ①LF 归一化 md5 与模板一致——堵手改与漏同步；
#   ②`NeedDaemonReload=no`——systemd 用的是**加载时快照**，scp 完忘了 daemon-reload
#     时磁盘文件与模板一致（①绿）而在跑的进程仍是旧配置，正是 nginx 那项要求
#     「reload 后再探」的同一类坑；
#   ③`FragmentPath` 必须就是 `/etc/systemd/system/` 那份——排除加载到 `/lib` 残留；
#   ④不得存在 drop-in。drop-in 能静默覆盖任何指令（含 ExecStart 与全部加固项），
#     存在时「主文件与模板一致」什么都证明不了。用文件系统 glob 判定而非只信
#     `DropInPaths`：老版 systemd 不认该属性会返回空串，那是 fail-open。
check_unit_matches_template() {
  local name="$1" tmpl inst a b need frag dir
  tmpl="/opt/hutuji/deploy/${name}.service"
  inst="/etc/systemd/system/${name}.service"
  [[ -f "$tmpl" && -f "$inst" ]] || return 1
  a=$(tr -d "\r" < "$tmpl" | md5sum | cut -d" " -f1)
  b=$(tr -d "\r" < "$inst" | md5sum | cut -d" " -f1)
  [[ -n "$a" && "$a" == "$b" ]] || return 1
  need="$(systemctl show "$name" -p NeedDaemonReload 2>/dev/null | cut -d= -f2-)"
  [[ "$need" == "no" ]] || return 1
  frag="$(systemctl show "$name" -p FragmentPath 2>/dev/null | cut -d= -f2-)"
  [[ "$frag" == "$inst" ]] || return 1
  for dir in /etc/systemd/system /run/systemd/system /usr/lib/systemd/system /lib/systemd/system; do
    if compgen -G "$dir/${name}.service.d/*.conf" >/dev/null 2>&1; then
      echo "存在 drop-in $dir/${name}.service.d/：可静默覆盖 ExecStart 与加固项，判 fail" >&2
      return 1
    fi
  done
}

# `daemon-reload` 只更新 unit 快照，不会重启已经 active 的服务；`enable --now` 对
# 已 active 的服务同样不会重启。因此还要证明 hutuji-mcp 的本次启动确实执行了
# 两条沙箱内自检（生成链 + 位图描摹链），且主进程不早于本次部署的 unit、代码和 profile。
check_mcp_prestart_and_restart() {
  local prestart started start_epoch newest path mtime line
  local saw_gcode=0 saw_bitmap=0
  local -a deployed_files=(
    /etc/systemd/system/hutuji-mcp.service
    /opt/hutuji/deploy/hutuji-mcp.service
    /opt/hutuji/mcp-server/hutuji_mcp/hutuji_gwrite.toml
    /opt/hutuji/mcp-server/hutuji_mcp/*.py
  )

  prestart="$(systemctl show hutuji-mcp -p ExecStartPre --value 2>/dev/null)" || return 1
  # 逐条判，不能对整段做子串匹配：两条 ExecStartPre 会输出两条记录，整段里只要有
  # 一个 status=0 就能被「含 status=0」蒙过——那正好是「生成链绿、位图链红」的形状。
  # 每条都必须自己 code=exited 且 status=0；没跑过的条目没有 status 字段，同样判红。
  while IFS= read -r line; do
    [[ -z "${line//[[:space:]]/}" ]] && continue
    case "$line" in
      *"hutuji_mcp.gcode_gen --self-test"*) saw_gcode=1 ;;
      *"hutuji_mcp.bitmap_svg --self-test"*) saw_bitmap=1 ;;
    esac
    [[ "$line" == *"code=exited"* && "$line" == *"status=0"* ]] || {
      echo "hutuji-mcp 有 ExecStartPre 未在本次启动成功执行，须显式 restart：$line" >&2
      return 1
    }
  done <<< "$prestart"
  (( saw_gcode == 1 )) || {
    echo "hutuji-mcp 缺生成链自检 ExecStartPre（gcode_gen --self-test）" >&2
    return 1
  }
  (( saw_bitmap == 1 )) || {
    echo "hutuji-mcp 缺位图描摹链自检 ExecStartPre（bitmap_svg --self-test）：缺依赖会 fail-open" >&2
    return 1
  }

  started="$(LC_ALL=C systemctl show hutuji-mcp -p ExecMainStartTimestamp --value 2>/dev/null)" || return 1
  start_epoch="$(LC_ALL=C date -d "$started" +%s 2>/dev/null)" || return 1
  [[ "$start_epoch" =~ ^[0-9]+$ ]] || return 1

  newest=0
  for path in "${deployed_files[@]}"; do
    [[ -f "$path" ]] || return 1
    mtime="$(stat -c %Y "$path" 2>/dev/null)" || return 1
    (( mtime > newest )) && newest="$mtime"
  done
  if (( start_epoch < newest )); then
    echo "hutuji-mcp 主进程早于本次 unit/代码/profile，须显式 restart" >&2
    return 1
  fi
}

check_units_match_templates() {
  check_unit_matches_template hutuji-mcp &&
    check_unit_matches_template hutuji-cloud-bridge &&
    check_mcp_prestart_and_restart
}

# 用**生产 venv 的真实转换链**造产物：vpype → gwrite --profile hutuji → §5 校验。
#
# 旧版这里是 `printf "G21\nG90\nF8000\n"`，即人工拼的三行文本。那份内容连
# validate_gcode 自己都不接受（实测「第 4 行缺少 §5 文件头 G1G90 Z0.0F10000」），
# 所以第 15 项只证明了「nginx 能把一个文件按字节发出去」，完全没碰生成链：
# hutuji_gwrite.toml 漏传、vpype/vpype-gcode 装坏、profile 与 gcode_gen.py 的
# expected_head 漂移，全都逃过 15 项。已实测复现——把 hutuji_gwrite.toml 移走后
# `import hutuji_mcp.server` 依然成功、服务保持 active、/mcp 与 /files 照常响应，
# 而真实 svg_to_gcode() 报「vpype 转换失败（exit 2）: --config … does not exist」。
# 即 `pass=15 fail=0` 与「一画就整单失败」可以同时成立。根因是 _PROFILE_PATH 只在
# 请求期被 _vpype_command() 用到，import 期不加载、不校验，systemd active 抓不到。
# 现在改为真的跑一遍生成链，同时覆盖 profile、vpype 依赖、生成器与 §5 校验器，
# 再拿这份真产物验完整下载契约。
PROBE_SVG='<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" viewBox="0 0 210 297"><polyline points="20,40 60,40 60,100 20,100 20,40" fill="none" stroke="black" stroke-width="0.5"/></svg>'

# 必须用生产 venv 的解释器，不能用系统 python3：vpype/vpype-gcode 只装在 venv 里，
# 且 _vpype_cmd() 按 sys.executable 同目录定位 vpype，换解释器即换掉被验对象。
VENV_PY=/opt/hutuji/mcp-server/.venv/bin/python

# 这一项刻意不依赖 OUTPUT_DIR：产物落 mktemp -u，不往 /files 留文件，
# 于是「生成链坏了」与「产物目录坏了」不会互相染色——目录本身另有判据。
check_generate_chain() (
  local svg out
  [[ -x "$VENV_PY" ]] || exit 1
  svg="$(mktemp)"; out="$(mktemp -u)"
  trap 'rm -f -- "$svg" "$out"' EXIT
  printf '%s' "$PROBE_SVG" > "$svg"
  "$VENV_PY" -c '
import sys
from pathlib import Path

sys.path.insert(0, "/opt/hutuji/mcp-server")
from hutuji_mcp.gcode_gen import svg_to_gcode

stats = svg_to_gcode(Path(sys.argv[1]), Path(sys.argv[2]))
# 空产物也是坏产物：行数/字节必须为正，否则「转换成功」毫无意义。
raise SystemExit(0 if stats.lines > 0 and stats.bytes > 0 else 1)
' "$svg" "$out" >/dev/null 2>&1
)

# 位图描摹链正例。与上面的生成链是两条独立依赖栈（vpype ≠ skimage/sknw/imageio），
# 且描摹依赖是 trace_bitmap_to_polylines 里的**延迟 import**：缺失时 import
# hutuji_mcp.server 成功、服务 active、生成链自检也照样绿，只有真实描摹会炸。
# 2026-08-09 本机实测复现过这个三绿一红的组合，故必须单独真跑一次。
#
# 刻意走 `-m … --self-test` 而不是 import 内部函数：这正是 unit 里 ExecStartPre
# 的 argv，连 _main 的参数解析一起覆盖，避免 preflight 验的入口与开机验的入口漂移。
# 也刻意不复用「ExecStartPre status=0」那条判据——那只说明**本次启动时**的代码是好的，
# scp 了坏依赖但没 restart 时它仍是绿的；这一项真跑当前落盘的代码，把那个洞堵掉。
# 自检不碰网络（探针 PNG 由 stdlib zlib 现造），故不会因生图 API 抖动误红。
check_bitmap_chain() (
  local out
  [[ -x "$VENV_PY" ]] || exit 1
  # 用 `if !` 包住赋值，不写 `out=$(...); rc=$?`：errexit 下失败的命令替换会让子 shell
  # 直接退出，rc 那行根本执行不到，诊断也就永远打不出来。
  if ! out="$(PYTHONPATH=/opt/hutuji/mcp-server "$VENV_PY" -m hutuji_mcp.bitmap_svg --self-test 2>&1)"; then
    # 只回最后一行：BitmapError 的那句话就是可行动的诊断，traceback 无用且刷屏。
    printf '%s\n' "$out" | tail -1 >&2
    exit 1
  fi
)

check_download() (
  local h="$TUNNEL_HOST" d="$OUTPUT_DIR" tok f svg headers body code expected_len actual_len expected_crc actual_crc
  [[ -n "$h" && -d "$d" ]] && safe_output_dir "$d" || exit 1
  [[ -x "$VENV_PY" ]] || exit 1
  tok="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
  f="draw_$(date +%Y%m%d_%H%M%S)_${tok}.gcode"; svg="$(mktemp)"; headers="$(mktemp)"; body="$(mktemp)"
  trap 'rm -f -- "$d/$f" "$svg" "$headers" "$body"' EXIT
  printf '%s' "$PROBE_SVG" > "$svg"
  # 真产物：过 §5 校验才落到 OUTPUT_DIR，随后验 HTTP 字节/长度/CRC32 完整一致。
  "$VENV_PY" -c '
import sys
from pathlib import Path

sys.path.insert(0, "/opt/hutuji/mcp-server")
from hutuji_mcp.gcode_gen import svg_to_gcode

stats = svg_to_gcode(Path(sys.argv[1]), Path(sys.argv[2]))
raise SystemExit(0 if stats.lines > 0 and stats.bytes > 0 else 1)
' "$svg" "$d/$f" >/dev/null 2>&1 || exit 1
  code="$(curl --noproxy "*" --max-time 5 -sS -D "$headers" -o "$body" -w "%{http_code}" -H "Host: $h" "http://127.0.0.1/files/$f")" || exit 1
  expected_len="$(wc -c < "$d/$f" | tr -d " ")"
  actual_len="$(awk 'tolower($1) == "content-length:" {gsub("\r", "", $2); print $2}' "$headers" | tail -1)"
  expected_crc="$(python3 -c 'import sys, zlib; print("%08x" % (zlib.crc32(open(sys.argv[1], "rb").read()) & 0xffffffff))' "$d/$f")"
  actual_crc="$(awk 'tolower($1) == "x-hutuji-crc32:" {gsub("\r", "", $2); print tolower($2)}' "$headers" | tail -1)"
  [[ "$code" == "200" && "$actual_len" == "$expected_len" && "$actual_crc" == "$expected_crc" ]] && cmp -s "$d/$f" "$body"
)

PUBLIC_BASE_URL=""
OUTPUT_DIR=""
LLM_BASE_URL=""
LLM_API_KEY=""
LLM_MODEL=""
if [[ -f /etc/hutuji/mcp.env ]]; then
  env_value /etc/hutuji/mcp.env HUTUJI_PUBLIC_BASE_URL; PUBLIC_BASE_URL="$ENV_VALUE"
  env_value /etc/hutuji/mcp.env HUTUJI_OUTPUT_DIR; OUTPUT_DIR="$ENV_VALUE"
  env_value /etc/hutuji/mcp.env HUTUJI_LLM_BASE_URL; LLM_BASE_URL="$ENV_VALUE"
  env_value /etc/hutuji/mcp.env HUTUJI_LLM_API_KEY; LLM_API_KEY="$ENV_VALUE"
  env_value /etc/hutuji/mcp.env HUTUJI_LLM_MODEL; LLM_MODEL="$ENV_VALUE"
fi
BRIDGE_ENDPOINT=""
if [[ -f /etc/hutuji/bridge.env ]]; then
  env_value /etc/hutuji/bridge.env MCP_ENDPOINT; BRIDGE_ENDPOINT="$ENV_VALUE"
fi
TUNNEL_HOST=""
if [[ "$PUBLIC_BASE_URL" =~ ^https://([A-Za-z0-9-]+\.)*[A-Za-z0-9-]+(:443)?/?$ ]]; then
  TUNNEL_HOST="${PUBLIC_BASE_URL#https://}"
  TUNNEL_HOST="${TUNNEL_HOST%/}"
  TUNNEL_HOST="${TUNNEL_HOST%:443}"
fi

check "mcp.env 权限 600" bash -c '[[ "$(stat -c %a /etc/hutuji/mcp.env 2>/dev/null || echo x)" == "600" ]]'
check "bridge.env 权限 600" bash -c '[[ "$(stat -c %a /etc/hutuji/bridge.env 2>/dev/null || echo x)" == "600" ]]'
check "mcp.env 无占位、LLM 完整且公网前缀为 HTTPS:443" check_mcp_env
check "bridge.env 无占位且 MCP_ENDPOINT 为 wss://" check_bridge_env
check "8300 仅本机监听" bash -c 'ss -lnt | grep -q "127.0.0.1:8300" || ss -lnt | grep -q "\[::1\]:8300"'
# `ss` 不在时 `! ss | grep` 会因无输出而假绿（第 5 项那条 fail-closed，这条不是），先探二进制。
check "公网未监听 8300" bash -c 'command -v ss >/dev/null && ! ss -lnt | grep -E ":8300" | grep -vqE "127.0.0.1|\[::1\]"'
check "hutuji-mcp active" systemctl is-active --quiet hutuji-mcp
check "hutuji-cloud-bridge active 且当前 WSS 已连接" check_bridge_ready
# active 不证明跑的是哪一版 unit；daemon-reload 也不会重启旧进程。
check "两个 unit 与模板一致，且已重启并通过 ExecStartPre" check_units_match_templates
# `check … | grep` 会丢失 [pass] 行并在子 shell 中丢计数；判据必须自包含。
# 404 必须排除：MCP 子应用挂载失败（server.py 的 app.mount("/", _mcp_app) 未生效）时
# 裸 POST /mcp 由 FastAPI 返回 404，落在 ^[234] 内会把「端点已不存在」报成 pass。
# 合法响应是 2xx/3xx，或 FastMCP 对不合规 body 的 400/406/415；5xx 与 404 都不算。
check "本机 /mcp 可达" bash -c 'code=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -X POST -H "content-type: application/json" -d "{}" http://127.0.0.1:8300/mcp); [[ "$code" =~ ^[23] || "$code" == "400" || "$code" == "406" || "$code" == "415" ]]'
# /files 无具体 token 时允许 404（4xx 在列）；连接失败（000）与 5xx 都不得报 pass。
check "本机 /files 路径有响应" bash -c 'code=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8300/files/); [[ "$code" =~ ^[234] ]]'

# FastAPI 默认自动挂 /docs、/redoc、/openapi.json、/docs/oauth2-redirect，实测四条原本
# 全回 200。公网访问不到，但那份安全**完全来自 nginx 兜底**（443 的 `location /` 返回
# 404），应用层原本零防御：`location /` 一旦为加健康检查之类放开、或有人给 :8300 开直连
# 调试，攻击面即静默打开。对比 /mcp 有三层保护（nginx 显式 404 + 安全组不放行 8300 +
# 上面那条专项探针），这四条原本只有第一层，preflight 与 pytest 都没有判据会红。
# **必须直连 :8300 探**：经 nginx 探只能证明 nginx 在拦，证不到应用层是否已关——那正是
# 本项要覆盖的盲区。判据取「必须 404」：`docs_url=None` 后这些路径不再注册，落到
# app.mount("/", _mcp_app) 由 MCP 子应用回 404；200/3xx 说明开关被去掉了。000（连不上）
# 同样判 fail，否则服务没起来也能靠「探不到」蒙成绿。
check_docs_endpoints_disabled() {
  local p code
  for p in /docs /redoc /openapi.json /docs/oauth2-redirect; do
    code=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8300${p}")
    if [[ "$code" != "404" ]]; then
      echo "应用层 ${p} 返回 ${code}（期望 404）：FastAPI 自动文档端点未关" >&2
      return 1
    fi
  done
}
check "应用层已关 FastAPI 自动文档端点（/docs /redoc /openapi.json）" check_docs_endpoints_disabled

# ---- nginx 层（应用直连绿≠经 nginx 绿）----
check "nginx -t 通过且 enabled 配置含 loopback-only :80 /files ACL" check_active_nginx_acl
check "sites-enabled/hutuji 指向 sites-available/hutuji" bash -c '
  [[ -L /etc/nginx/sites-enabled/hutuji ]] &&
  [[ "$(readlink -f /etc/nginx/sites-enabled/hutuji)" == "/etc/nginx/sites-available/hutuji" ]]'
check "运行中 nginx conf 与固定部署模板一致（LF 归一化）" bash -c '
  [[ -f /opt/hutuji/deploy/nginx-hutuji.conf && -f /etc/nginx/sites-enabled/hutuji ]] || exit 1
  a=$(tr -d "\r" < /opt/hutuji/deploy/nginx-hutuji.conf | md5sum | cut -d" " -f1)
  b=$(tr -d "\r" < /etc/nginx/sites-enabled/hutuji | md5sum | cut -d" " -f1)
  [[ -n "$a" && "$a" == "$b" ]]'

# 「配置文件与模板一致」证不到「在跑的 nginx 读了它」。nginx 的 reload 有一条静默
# 失败路径，`nginx -t` 与 `systemctl reload` 都抓不到：
#   - `nginx -t` 起的是**新进程**，没有继承的共享内存区，故必然通过；
#   - `systemctl reload` 走 `nginx -s reload`，只负责发信号，不等 master 受理，
#     恒返回 status=0/SUCCESS；
#   - master 若拒绝新配置，只在 error.log 写 [emerg] 并**继续用旧配置服务**，
#     旧 worker 不被替换，`systemctl is-active` 照样 active。
# 2026-08-08 实测踩中：把限速 key 从 $binary_remote_addr 改为 $hutuji_client_ip 后
# reload 报 `[emerg] limit_req "hutuji_files" uses the "$hutuji_client_ip" key while
# previously it used the "$binary_remote_addr" key`（共享内存区的 key 不能热改），
# 于是 nginx 整整 37 分钟跑着旧配置，而当时 19 项 preflight **全绿**——因为其余判据
# 在旧配置下同样成立，唯一变化的限速身份没有任何判据覆盖。此类 key/zone 变更必须
# `systemctl restart nginx`，reload 不足。
#
# 下面两项一个查行为、一个查进程，缺一不可：
#   ①行为：从 loopback 打一发带唯一标记的探针，要求它出现在 `hutuji_client` 自定义
#     格式的专用日志里。旧配置没有该 access_log 指令，请求会落到 http 级默认
#     /var/log/nginx/access.log 的 combined 格式——正是当时观察到的现象。这是唯一
#     直接证明「运行时用的是这份配置」的判据，且顺带验证限速身份变量已生效。
#   ②进程：全部 worker 的启动时间不得早于配置 mtime，并扫「最老 worker 启动之后」的
#     [emerg]。行为判据在「旧配置恰好也含同名 access_log」时会假绿，进程判据补住；
#     反过来 mtime 可被 touch 干扰，故不能只靠进程判据。
#     两处易踩的坑，均已实测：
#       - `stat -c %Y /etc/nginx/sites-enabled/hutuji` 取到的是**符号链接自身**的
#         mtime（即 `ln -sfn` 的时刻，实测 2026/08/07 17:31:15），不是配置本体
#         （2026/08/08 20:54:01）。必须 `stat -L` 解引用，否则判据会被 ln 时刻带偏。
#       - [emerg] 的基准不能用配置 mtime：那样一次被拒后即使已 restart 修好，旧
#         [emerg] 仍永久判红。基准取最老 worker 的启动时刻——reload 被拒时 emerg
#         晚于当时的旧 worker 故命中；restart 成功后 worker 更新，旧 emerg 自动出局。
#     已知盲区：worker 启动后 error.log 若被 logrotate 轮转，[emerg] 会落到 .1 而漏扫；
#     此路径由行为判据兜住，不在此项内重复实现。
check_nginx_running_config_is_live() {
  local h="$1" marker log line
  [[ -n "$h" ]] || return 1
  log=/var/log/nginx/hutuji-access.log
  if [[ ! -r "$log" ]]; then
    echo "读不到 ${log}：无法证明在跑的 nginx 用的是本次配置，判 fail" >&2
    return 1
  fi
  marker="hutuji-preflight-live-$$-$(date +%s)"
  curl --noproxy "*" --max-time 5 -s -o /dev/null \
    -H "Host: $h" -A "$marker" http://127.0.0.1/files/__nonexistent__ || return 1
  line="$(grep -F "$marker" "$log" | tail -1)"
  if [[ -z "$line" ]]; then
    echo "探针未进 ${log}：在跑的 nginx 仍是旧配置（reload 被 [emerg] 拒绝时即如此），须 systemctl restart nginx" >&2
    return 1
  fi
  # hutuji_client 格式首字段是限速身份 $hutuji_client_ip，第二字段是 via=$remote_addr。
  # 本机不带 CF-Connecting-IP，两者都应为 127.0.0.1；缺 via= 说明日志格式不是这一版。
  if [[ "$line" != "127.0.0.1 via=127.0.0.1 "* ]]; then
    echo "专用日志行首不是 '127.0.0.1 via=127.0.0.1'：$line（限速身份变量未生效）" >&2
    return 1
  fi
}

check_nginx_workers_fresh() {
  local conf mtime pid started epoch oldest emerg since
  conf=/etc/nginx/sites-enabled/hutuji
  [[ -e "$conf" ]] || return 1
  # -L 必须有：见上方注释，不解引用会读到 symlink 自己的 mtime。
  mtime="$(stat -L -c %Y "$conf" 2>/dev/null)" || return 1
  [[ "$mtime" =~ ^[0-9]+$ ]] || return 1

  # master 可以是旧的（reload 不换 master），worker 必须是新的：受理成功才换 worker。
  oldest=""
  for pid in $(pgrep -x nginx); do
    grep -qs 'master process' "/proc/$pid/cmdline" && continue
    started="$(LC_ALL=C ps -o lstart= -p "$pid" 2>/dev/null | head -1)"
    [[ -n "$started" ]] || return 1
    epoch="$(LC_ALL=C date -d "$started" +%s 2>/dev/null)" || return 1
    [[ "$epoch" =~ ^[0-9]+$ ]] || return 1
    # 容 1s：mtime 与进程启动同秒时 stat/ps 的取整方向可能相反。
    if (( epoch + 1 < mtime )); then
      echo "nginx worker $pid 启动于 $started，早于配置 mtime $(date -d "@$mtime" '+%F %T')：新配置未被受理，须 systemctl restart nginx" >&2
      return 1
    fi
    if [[ -z "$oldest" ]] || (( epoch < oldest )); then oldest="$epoch"; fi
  done
  if [[ -z "$oldest" ]]; then
    echo "找不到 nginx worker 进程：无法证明新配置已被受理，判 fail" >&2
    return 1
  fi

  # error.log 的时间戳形如 `2026/08/08 20:54:44`，定宽故可与同格式基准做字典序比较。
  since="$(date -d "@$oldest" '+%Y/%m/%d %H:%M:%S' 2>/dev/null)" || return 1
  emerg="$(awk -v s="$since" '/\[emerg\]/ {ts = $1 " " $2; if (ts >= s) print}' \
    /var/log/nginx/error.log 2>/dev/null | tail -1)"
  if [[ -n "$emerg" ]]; then
    echo "当前 worker 启动后 error.log 有 [emerg]，新配置曾被拒绝：$emerg" >&2
    return 1
  fi
}
check "在跑的 nginx 确实是本次配置（探针进专用日志且限速身份生效）" check_nginx_running_config_is_live "$TUNNEL_HOST"
check "nginx worker 不早于配置 mtime 且启动后无 [emerg]" check_nginx_workers_fresh
check "活动 :80 /files ACL：loopback 放行、非 loopback 拒绝" check_acl_response "$TUNNEL_HOST"
# 兜底路径行为：隧道 `service: http://localhost:80` 使**全部**公网流量落进 :80 块，
# 于是 :443 的 `location / { return 404; }` 隧道永远走不到。旧模板 :80 兜底无条件
# 301 到 https://<隧道主机名>$request_uri —— 边缘再回源 :80 —— 自指死循环：实测
# 公网 /docs、/zzz-nonexistent 全是 `301 Location: 自身`，`curl -L` 也停在 301。
# 后果不是暴露而是判据造假:「公网非白名单路径须 404」从未成立过，却因 301∉2xx
# 被一切「须失败」式复验蒙过。修法是按 `X-Forwarded-Proto` 分流，故本项必须**同时**
# 验两个方向：带头(模拟隧道回源)须 404，不带头(真明文直连)须 301——只验一边，
# 无条件 404 会让明文 /files 之外的路径丢掉强制 HTTPS。
check_catchall_behavior() {
  local code location response
  response=$(curl --noproxy "*" --max-time 5 -sS -D - -o /dev/null -w '\n%{http_code}' \
    -H "Host: ${TUNNEL_HOST}" -H "X-Forwarded-Proto: https" \
    "http://127.0.0.1:80/zzz-preflight-probe") || return 1
  code="${response##*$'\n'}"
  if [[ "$code" != "404" ]]; then
    echo "隧道回源态兜底路径返回 ${code}（期望 404）：301 则为自指死循环" >&2
    return 1
  fi
  response=$(curl --noproxy "*" --max-time 5 -sS -D - -o /dev/null -w '\n%{http_code}' \
    -H "Host: ${TUNNEL_HOST}" "http://127.0.0.1:80/zzz-preflight-probe") || return 1
  code="${response##*$'\n'}"
  location="$(printf '%s\n' "$response" | awk 'tolower($1) == "location:" {gsub("\r", "", $2); print $2}' | tail -1)"
  if [[ "$code" != "301" || "$location" != "https://${TUNNEL_HOST}/zzz-preflight-probe" ]]; then
    echo "明文直连兜底路径返回 ${code} / Location ${location}（期望 301 + https://${TUNNEL_HOST}/zzz-preflight-probe）：强制 HTTPS 已失效" >&2
    return 1
  fi
}
check "兜底路径：隧道回源态 404、明文直连 301（无自指 301 环）" check_catchall_behavior

# 生成链正例：生产 venv 真跑 vpype + §5 校验（覆盖 profile / 依赖 / 校验器漂移）。
check "生产 venv 真实生成链可用（vpype + profile + §5 校验）" check_generate_chain
# 位图描摹链正例：生产 venv 真跑 skimage/sknw/imageio 描摹（缺依赖会 fail-open）。
check "生产 venv 真实位图描摹链可用（skimage + sknw + imageio）" check_bitmap_chain
# 真 200 正例：用真实生成链造一份合法命名产物（TTL 内自清），验完整下载契约。
check "localhost :80 /files 真下 G-code 200" check_download

echo "---- preflight pass=${PASS} fail=${FAIL} expected=${EXPECTED_CHECKS} ----"
# 必须全 pass：无 skip 可抵扣，少一项都不放行（漏跑与失败同等对待）。
if ! [[ "${PASS}" -eq "${EXPECTED_CHECKS}" && "${FAIL}" -eq 0 ]]; then
  exit 1
fi
echo "PASS — 再验：公网 curl /mcp 须失败；HTTPS /files 经 nginx；小智控制台 tools/list"
