#!/usr/bin/env python3
"""双机代码发布：明确提交的 LF blob、逐文件校验、备份、队列排空与失败回滚。

构建：python scripts/auto_deploy.py --build --target both --commit <commit>
部署：python scripts/auto_deploy.py --deploy --target aliyun --package <该节点包>
验证：python scripts/auto_deploy.py --verify --target both
回滚：python scripts/auto_deploy.py --rollback --target aliyun --backup-id <明确备份号>

只更新包内列出的代码、profile 和静态资源；依赖、服务单元、凭据与数据迁移另按部署 SOP。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
REMOTE_ROOT = Path('/opt/hutuji')
MCP_ENV_FILE = Path('/etc/hutuji/mcp.env')
ARCHIVE_ROOT = 'hutuji-deploy/'
MANIFEST_NAME = ARCHIVE_ROOT + 'DEPLOYMENT_MANIFEST.json'
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
DEPLOY_TARGETS = {
    'aliyun': {'host': 'lima', 'user': '', 'services': [
        'hutuji-mcp', 'hutuji-draw-portal', *[f'hutuji-draw-worker@{i}' for i in range(1, 9)],
        'hutuji-cloud-bridge']},
    'jdcloud': {'host': '117.72.118.95', 'user': 'root',
                'services': ['hutuji-mcp', 'hutuji-draw-portal']},
}
OPTIONAL_SERVICES = ('hutuji-draw-admin', 'hutuji-kb-admin', 'hutuji-curated-prep')
PREFIXES = ('mcp-server/hutuji_mcp/', 'draw-portal/draw_portal/', 'draw-portal/draw_admin/')
EXTRA_PATHS = ('mcp-server/scripts/e2e_local.py', 'scripts/xiaozhi_console.py', 'deploy/preflight.sh')
TEXT_SUFFIXES = {'.py', '.toml', '.json', '.html', '.css', '.js', '.svg', '.sh'}
SUFFIXES = TEXT_SUFFIXES | {'.png', '.ico', '.woff2'}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def valid_release_path(relative: str) -> bool:
    if not isinstance(relative, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', relative):
        return False
    parts = PurePosixPath(relative).parts
    return (not relative.startswith('/') and '..' not in parts
            and all(part not in ('', '.') and not part.startswith('.') for part in parts)
            and '/'.join(parts) == relative
            and (relative.startswith(PREFIXES) or relative in EXTRA_PATHS)
            and PurePosixPath(relative).suffix in SUFFIXES)


def runtime_path(root: Path, relative: str) -> Path:
    if not valid_release_path(relative):
        raise ValueError('发布路径不在代码白名单')
    root = root.absolute()
    if root.is_symlink() or root.resolve() != root:
        raise ValueError('发布根目录不能经过链接')
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('发布路径不能经过链接')
    if not path.resolve().is_relative_to(root):
        raise ValueError('发布路径越界')
    return path


def _validate_manifest(manifest: dict, files: dict[str, bytes], target: str) -> None:
    if (target not in DEPLOY_TARGETS or not isinstance(manifest, dict) or type(manifest.get('schema')) is not int
            or manifest['schema'] != 1 or manifest.get('target') != target
            or not re.fullmatch(r'[0-9a-f]{40}', str(manifest.get('git_commit', '')))
            or manifest.get('services') != DEPLOY_TARGETS[target]['services']):
        raise ValueError('发布清单或目标节点不符')
    rows = manifest.get('files')
    if not isinstance(rows, list) or not rows or len(rows) > 1024:
        raise ValueError('发布清单文件数无效')
    names = set()
    for row in rows:
        if not isinstance(row, dict) or not valid_release_path(row.get('path')):
            raise ValueError('发布清单路径无效')
        name = row['path']
        if name in names or name not in files:
            raise ValueError('发布清单存在重复或缺失文件')
        names.add(name)
        data = files[name]
        if (type(row.get('size')) is not int or row['size'] != len(data)
                or not 0 <= len(data) <= MAX_FILE_BYTES or sha256(data) != row.get('sha256')
                or type(row.get('mode')) is not int or row['mode'] not in (0o644, 0o755)):
            raise ValueError('发布文件完整性校验失败')
        if Path(name).suffix in TEXT_SUFFIXES and b'\r' in data:
            raise ValueError('发布文本必须 CR=0')
        if name.endswith('.py'):
            compile(data, name, 'exec')
    if names != files.keys() or sum(map(len, files.values())) > MAX_PACKAGE_BYTES:
        raise ValueError('发布包存在清单外文件或超过预算')


def build_deployment_package(target: str, *, commit: str, paths: list[str] | None = None) -> Path:
    if target not in DEPLOY_TARGETS:
        raise ValueError('未知节点')
    revision = subprocess.check_output(
        ['git', 'rev-parse', '--verify', '--end-of-options', commit + '^{commit}'], cwd=ROOT
    ).decode('ascii').strip()
    tree = subprocess.check_output(['git', 'ls-tree', '-rz', '--full-tree', revision], cwd=ROOT)
    selected = set(paths) if paths else None
    if selected is not None and (not selected or not all(valid_release_path(path) for path in selected)):
        raise ValueError('选择路径不在白名单')
    files, rows = {}, []
    for entry in tree.split(b'\0'):
        if not entry:
            continue
        metadata, name_bytes = entry.split(b'\t', 1)
        mode, kind, oid = metadata.decode('ascii').split()
        name = name_bytes.decode('utf-8')
        if not valid_release_path(name) or (selected is not None and name not in selected):
            continue
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise ValueError('发布源必须是普通 Git blob')
        data = subprocess.check_output(['git', 'cat-file', 'blob', oid], cwd=ROOT)
        files[name] = data
        rows.append({'path': name, 'sha256': sha256(data), 'size': len(data),
                     'mode': 0o755 if mode == '100755' else 0o644})
    if selected is not None and selected != files.keys():
        raise ValueError('所选文件未包含在该提交中')
    manifest = {'schema': 1, 'timestamp': datetime.now(timezone.utc).isoformat(),
                'git_commit': revision, 'target': target, 'services': DEPLOY_TARGETS[target]['services'],
                'files': sorted(rows, key=lambda row: row['path'])}
    _validate_manifest(manifest, files, target)
    folder = ROOT / 'tmp'
    folder.mkdir(exist_ok=True)
    output = folder / f'deploy-{target}-{revision[:12]}-{uuid.uuid4().hex[:8]}.tar.gz'
    with tarfile.open(output, 'w:gz') as archive:
        for name, data, mode in [(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2).encode(), 0o600),
                                 *((ARCHIVE_ROOT + row['path'], files[row['path']], row['mode']) for row in manifest['files'])]:
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), mode
            archive.addfile(member, io.BytesIO(data))
    print('发布包', output, 'sha256=' + sha256(output.read_bytes()))
    return output


def read_package(package: Path, *, target: str) -> tuple[dict, dict[str, bytes]]:
    if target not in DEPLOY_TARGETS or package.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError('发布包或节点无效')
    entries, total = {}, 0
    # 不执行 extractall：链接、目录、路径穿越及重复项均在任何落盘前拒绝。
    with tarfile.open(package, 'r:gz') as archive:
        for member in archive:
            if (not member.isfile() or member.name in entries or len(entries) >= 1025
                    or member.size < 0 or member.size > MAX_FILE_BYTES):
                raise ValueError('发布归档成员无效')
            if member.name != MANIFEST_NAME and (
                    not member.name.startswith(ARCHIVE_ROOT)
                    or not valid_release_path(member.name[len(ARCHIVE_ROOT):])):
                raise ValueError('发布归档路径无效')
            total += member.size
            if total > MAX_PACKAGE_BYTES:
                raise ValueError('发布包解压预算超限')
            with archive.extractfile(member) as stream:
                entries[member.name] = stream.read(member.size + 1)
            if len(entries[member.name]) != member.size:
                raise ValueError('发布文件截断')
    try:
        manifest = json.loads(entries.pop(MANIFEST_NAME))
    except (KeyError, ValueError, UnicodeError):
        raise ValueError('发布清单不可读') from None
    files = {name[len(ARCHIVE_ROOT):]: data for name, data in entries.items()}
    _validate_manifest(manifest, files, target)
    return manifest, files


def _atomic_write(path: Path, data: bytes, mode=0o644, owner=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.deploy-' + uuid.uuid4().hex)
    try:
        with temp.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        if owner is not None and os.name == 'posix':
            os.chown(temp, *owner)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _restore(root: Path, backup: Path, record: dict, names: list[str]) -> None:
    originals = {}
    # 先全部验证再回滚，发现后续修改时不能覆盖它。
    for name in names:
        path = runtime_path(root, name)
        current = path.read_bytes() if path.is_file() else None
        row = record['files'][name]
        if (sha256(current) if current is not None else None) not in (row['before'], row['after']):
            raise RuntimeError('运行文件已被其他操作修改，拒绝覆盖')
        if row['before'] is not None:
            old = runtime_path(backup / 'originals', name).read_bytes()
            if sha256(old) != row['before']:
                raise RuntimeError('回滚备份校验失败')
            originals[name] = old
    for name in names:
        row = record['files'][name]
        path = runtime_path(root, name)
        if row['before'] is None:
            if path.exists():
                path.unlink()
        else:
            _atomic_write(path, originals[name], row['mode'], row['owner'])
    for name in names:
        path = runtime_path(root, name)
        actual = sha256(path.read_bytes()) if path.is_file() else None
        if actual != record['files'][name]['before']:
            raise RuntimeError('回滚后文件校验失败')


def _record_phase(backup: Path, record: dict, phase: str) -> None:
    record['phase'] = phase
    _atomic_write(backup / 'ROLLBACK.json', json.dumps(record, indent=2).encode(), 0o600)


def _validate_rollback_record(record: dict, target: str) -> None:
    if (not isinstance(record, dict) or type(record.get('schema')) is not int or record['schema'] != 1
            or record.get('target') != target or target not in DEPLOY_TARGETS
            or record.get('phase') not in ('prepared', 'active', 'rolling_back', 'restored', 'rollback_failed')
            or not re.fullmatch(r'[0-9a-f]{40}', str(record.get('git_commit', '')))):
        raise ValueError('备份不适用于此节点或已回滚')
    rows = record.get('files')
    if not isinstance(rows, dict) or not 0 < len(rows) <= 1024:
        raise ValueError('回滚清单文件数无效')
    for name, row in rows.items():
        if not valid_release_path(name) or not isinstance(row, dict):
            raise ValueError('回滚清单路径无效')
        if (row.get('before') is not None and not re.fullmatch(r'[0-9a-f]{64}', str(row['before']))
                or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('after', '')))
                or type(row.get('mode')) is not int or not 0 <= row['mode'] <= 0o777):
            raise ValueError('回滚文件记录无效')
        owner = row.get('owner')
        if owner is not None and (not isinstance(owner, (list, tuple)) or len(owner) != 2
                                  or any(type(value) is not int or not 0 <= value < 2 ** 32 for value in owner)):
            raise ValueError('回滚属主记录无效')


def rollback_files(root: Path, backup: Path, record: dict, *, pause, activate,
                   names: list[str] | None = None) -> None:
    _validate_rollback_record(record, record.get('target'))
    selected = list(record['files']) if names is None else names
    if any(name not in record['files'] for name in selected):
        raise ValueError('回滚范围超出备份')
    try:
        pause()
        _record_phase(backup, record, 'rolling_back')
        _restore(root, backup, record, selected)
        _record_phase(backup, record, 'restored')
        activate()
        _record_phase(backup, record, 'rolled_back')
    except Exception:
        # 磁盘或校验失败时不启动混合版本；下次仍能按 before/after 校验后继续恢复。
        try:
            _record_phase(backup, record, 'rollback_failed')
        except OSError as error:
            print('回滚检查点写入失败：' + type(error).__name__, file=sys.stderr)
        raise


def install_files(root: Path, backup: Path, manifest: dict, files: dict[str, bytes],
                  *, pause, activate, recover, owner=None, service_state: dict | None = None) -> None:
    _validate_manifest(manifest, files, manifest['target'])
    if service_state is not None:
        service_state = _validate_recovery_state(manifest['target'], service_state)
    before = {}
    for row in manifest['files']:
        path = runtime_path(root, row['path'])
        raw = path.read_bytes() if path.is_file() else None
        info = path.stat() if path.exists() else None
        before[row['path']] = {'before': sha256(raw) if raw is not None else None, 'after': row['sha256'],
                               'mode': stat.S_IMODE(info.st_mode) if info else 0o644,
                               'owner': (info.st_uid, info.st_gid) if info and os.name == 'posix' else None,
                               'raw': raw}
    backup.mkdir(parents=True, exist_ok=False)
    os.chmod(backup, 0o700)
    for name, row in before.items():
        if row['raw'] is not None:
            _atomic_write(runtime_path(backup / 'originals', name), row['raw'], 0o600)
    record = {'schema': 1, 'target': manifest['target'], 'git_commit': manifest['git_commit'],
              'phase': 'prepared', 'files': {name: {k: v for k, v in row.items() if k != 'raw'}
                                             for name, row in before.items()}}
    if service_state is not None:
        record['service_state'] = service_state
    record_path = backup / 'ROLLBACK.json'
    _atomic_write(record_path, json.dumps(record, indent=2).encode(), 0o600)
    installed = []
    try:
        pause()
        for row in manifest['files']:
            name = row['path']
            path = runtime_path(root, name)
            current = path.read_bytes() if path.is_file() else None
            if current != before[name]['raw']:
                raise RuntimeError('发布前运行文件变化，拒绝覆盖')
            # 先登记再写，异常或掉电时可根据 before/after 判定文件状态。
            installed.append(name)
            _atomic_write(path, files[name], row['mode'], owner)
        activate()
        for row in manifest['files']:
            if sha256(runtime_path(root, row['path']).read_bytes()) != row['sha256']:
                raise RuntimeError('发布后文件校验失败')
        record['phase'] = 'active'
        _atomic_write(record_path, json.dumps(record, indent=2).encode(), 0o600)
    except Exception:
        try:
            rollback_files(root, backup, record, names=installed,
                           pause=pause if installed else lambda: None, activate=recover)
        except Exception as error:
            raise RuntimeError('发布失败且回滚未完成；使用已输出的备份号恢复') from error
        raise


def run_ssh(host: str, user: str, cmd: str, check=True) -> subprocess.CompletedProcess:
    address = f'{user}@{host}' if user else host
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', address, cmd],
                          capture_output=True, text=True, encoding='utf-8', check=check, timeout=1200)


def _ssh(target: str, argv: list[str], *, check=True):
    config = DEPLOY_TARGETS[target]
    return run_ssh(config['host'], config['user'], shlex.join(argv), check=check)


def _bridge_is_inactive(target: str) -> bool:
    reply = _ssh(target, ['systemctl', 'is-active', 'hutuji-cloud-bridge'], check=False)
    return reply.returncode == 3 and reply.stdout.strip() == 'inactive'


def _transport(target: str, action: str, package: Path | None = None, backup_id: str | None = None) -> bool:
    manifest = None
    if package is not None:
        manifest, _ = read_package(package, target=target)
    if action in ('deploy', 'rollback') and not _bridge_is_inactive('jdcloud'):
        raise RuntimeError('备机 bridge 不是明确 inactive，拒绝发布')
    if backup_id is not None and not re.fullmatch(r'[0-9a-f]{32}', backup_id):
        raise ValueError('必须提供明确的备份号')
    remote = _ssh(target, ['mktemp', '-d', '/tmp/hutuji-deploy.XXXXXXXX']).stdout.strip()
    if not re.fullmatch(r'/tmp/hutuji-deploy\.[A-Za-z0-9]+', remote):
        raise RuntimeError('远端临时目录无效')
    config = DEPLOY_TARGETS[target]
    address = f"{config['user']}@{config['host']}" if config['user'] else config['host']
    script = Path(__file__).resolve()
    uploaded = []
    try:
        for local, name in ((script, 'deploy.py'), *(([(package, 'package.tar.gz')]) if package else [])):
            destination = remote + '/' + name
            subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', str(local), address + ':' + destination],
                           check=True, timeout=120)
            uploaded.append(destination)
            actual = _ssh(target, ['sha256sum', '--', destination]).stdout.split()[0]
            if actual != sha256(local.read_bytes()):
                raise RuntimeError('上传字节校验失败')
        command = ['/opt/hutuji/mcp-server/.venv/bin/python', remote + '/deploy.py', '--remote', action,
                   '--target', target]
        if package:
            command.extend(['--package', remote + '/package.tar.gz'])
        if backup_id:
            command.extend(['--backup-id', backup_id])
        result = _ssh(target, command, check=False)
        # 任何远端输出落盘前复用仓内唯一脱敏实现。
        sys.path.insert(0, str(ROOT / 'mcp-server'))
        from hutuji_mcp.redact import _redact_secret_text
        safe = _redact_secret_text(result.stdout + result.stderr)
        out = ROOT / 'results' / ('deploy-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        out.mkdir(parents=True, exist_ok=False)
        (out / (target + '-' + action + '.log')).write_text(safe, encoding='utf-8')
        print(safe[-3000:], end='')
        print('发布记录', out, 'exit=' + str(result.returncode))
        return result.returncode == 0
    finally:
        cleanup = ([['rm', '-f', '--', *uploaded]] if uploaded else []) + [['rmdir', '--', remote]]
        for command in cleanup:
            try:
                result = _ssh(target, command, check=False)
                if result.returncode:
                    print('远端临时文件清理未完成，不改变已记录的发布结果', file=sys.stderr)
            except (OSError, subprocess.SubprocessError) as error:
                print('远端临时文件清理失败：' + type(error).__name__, file=sys.stderr)


def deploy_package(target: str, package_path: Path) -> bool:
    try:
        return _transport(target, 'deploy', package_path)
    except (OSError, ValueError, SyntaxError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print('部署未通过：' + type(exc).__name__, file=sys.stderr)
        return False


def verify_deployment(target: str, package_path: Path | None = None) -> bool:
    return _transport(target, 'verify', package_path)


def rollback_deployment(target: str, backup_id: str) -> bool:
    return _transport(target, 'rollback', backup_id=backup_id)


def _systemctl(*args, check=True):
    return subprocess.run(['systemctl', *args], check=check, capture_output=True, text=True, timeout=360)


def _active(service: str) -> bool:
    result = _systemctl('is-active', service, check=False)
    if result.returncode == 0 and result.stdout.strip() == 'active':
        return True
    if result.returncode == 3 and result.stdout.strip() in ('inactive', 'failed'):
        return False
    if result.returncode == 4 and result.stdout.strip() in ('inactive', 'unknown'):
        return False
    raise RuntimeError('服务状态不可读')


def _queue_is_idle(queue_url: str) -> bool:
    if not queue_url:
        return True
    import redis
    # 从安装版本的字面声明读取键名，回滚不能依赖故障应用仍可 import。
    source = REMOTE_ROOT / 'mcp-server/hutuji_mcp/draw_queue.py'
    names = {'QUEUE_KEY', 'JOB_PREFIX', 'PENDING_KEY'}
    keys = {}
    for node in ast.parse(source.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for assigned in node.targets:
                if isinstance(assigned, ast.Name) and assigned.id in names:
                    keys[assigned.id] = node.value.value
    if (not {'QUEUE_KEY', 'JOB_PREFIX'} <= keys.keys()
            or any(not re.fullmatch(r'hutuji:[A-Za-z0-9:_-]{1,128}', value) for value in keys.values())):
        raise RuntimeError('已安装队列的键声明不可读')
    client = redis.from_url(queue_url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3)
    try:
        if client.llen(keys['QUEUE_KEY']):
            return False
        count = 0
        for key in client.scan_iter(match=keys['JOB_PREFIX'] + '*', count=100):
            count += 1
            if count > 100000:
                raise RuntimeError('任务扫描超过预算')
            if client.hget(key, 'status') in ('pending', 'processing'):
                return False
        if 'PENDING_KEY' in keys and client.zcard(keys['PENDING_KEY']):
            return False
        return client.llen(keys['QUEUE_KEY']) == 0
    finally:
        client.close()


def _validate_recovery_state(target: str, state: dict) -> dict:
    if target not in DEPLOY_TARGETS or not isinstance(state, dict):
        raise ValueError('备份缺少原服务与队列快照')
    services = state.get('services')
    required = set(DEPLOY_TARGETS[target]['services'])
    if (not isinstance(services, list) or not all(isinstance(service, str) for service in services)
            or len(services) != len(set(services)) or not required <= set(services)
            or not set(services) <= required | set(OPTIONAL_SERVICES)
            or not re.fullmatch(r'[0-9a-f]{64}', str(state.get('queue_sha256', '')))):
        raise ValueError('备份服务或队列快照无效')
    return {'services': services.copy(), 'queue_sha256': state['queue_sha256']}


def _queue_url_for_services(services: list[str], expected_sha256: str | None) -> str:
    urls = []
    for service in services:
        if (service != 'hutuji-mcp' and not service.startswith('hutuji-draw-worker@')) or not _active(service):
            continue
        pid = _systemctl('show', '--property=MainPID', '--value', service).stdout.strip()
        if not pid.isdigit() or int(pid) <= 1:
            raise RuntimeError('队列进程身份不可读')
        values = dict(item.split(b'=', 1) for item in Path('/proc', pid, 'environ').read_bytes().split(b'\0')
                      if b'=' in item)
        urls.append(values.get(b'HUTUJI_DRAW_QUEUE_URL', b'').decode())
    if urls:
        if len(set(urls)) != 1:
            raise RuntimeError('运行服务的队列配置不一致')
        queue_url = urls[0]
    else:
        if expected_sha256 is None:
            raise RuntimeError('没有可读取队列配置的运行进程')
        # 整个应用停机时只读配置文件，不执行 shell；摘要必须匹配部署前实际进程。
        queue_url = ''
        for line in MCP_ENV_FILE.read_text(encoding='utf-8').splitlines():
            key, separator, value = line.strip().partition('=')
            if separator and key.strip() == 'HUTUJI_DRAW_QUEUE_URL':
                tokens = shlex.split(value.strip(), comments=False, posix=True)
                if len(tokens) > 1:
                    raise RuntimeError('队列配置文件格式无效')
                queue_url = tokens[0] if tokens else ''
    if expected_sha256 is not None and sha256(queue_url.encode()) != expected_sha256:
        raise RuntimeError('队列配置与备份身份不符，拒绝回滚')
    return queue_url


def _service_hooks(target: str, *, recovery_state: dict | None = None):
    required = DEPLOY_TARGETS[target]['services']
    if recovery_state is None:
        active = [service for service in (*required, *OPTIONAL_SERVICES) if _active(service)]
        if set(required) - set(active):
            raise RuntimeError('部署前必需服务未处于 active')
    else:
        recovery_state = _validate_recovery_state(target, recovery_state)
        active = recovery_state['services']
    if target == 'jdcloud' and (_active('hutuji-cloud-bridge')
                               or any(_active(f'hutuji-draw-worker@{i}') for i in range(1, 9))):
        raise RuntimeError('备机 bridge/worker 必须关闭')
    queue_url = _queue_url_for_services(active, recovery_state['queue_sha256'] if recovery_state else None)
    if target == 'aliyun' and not queue_url:
        raise RuntimeError('主节点队列配置不可读')
    workers = [service for service in active if service.startswith('hutuji-draw-worker@')]
    ingress = [service for service in reversed(active) if service not in workers]
    state = {'services': active, 'queue_sha256': sha256(queue_url.encode())}

    def pause():
        # 先停止入口，旧 worker 排空；不会把旧 payload 交给新 worker。
        _systemctl('stop', *ingress)
        deadline = time.monotonic() + 300
        while not _queue_is_idle(queue_url):
            if not any(_active(service) for service in workers):
                raise RuntimeError('队列有未完成任务但 worker 未运行，未修改代码')
            if time.monotonic() >= deadline:
                raise RuntimeError('队列未排空，未修改代码')
            time.sleep(2)
        if workers:
            _systemctl('stop', *workers)
        if not _queue_is_idle(queue_url):
            raise RuntimeError('停机后队列状态变化')

    def activate():
        _systemctl('start', *[service for service in active if service != 'hutuji-cloud-bridge'])
        if 'hutuji-cloud-bridge' in active:
            _systemctl('start', 'hutuji-cloud-bridge')
        _verify_services(target, expected_services=active)

    return pause, activate, state


def _verify_services(target: str, *, expected_services=None):
    import urllib.request
    deadline = time.monotonic() + 60
    while True:
        try:
            if not all(_active(service) for service in (expected_services or DEPLOY_TARGETS[target]['services'])):
                raise RuntimeError('必需服务未启动')
            if target == 'jdcloud' and _active('hutuji-cloud-bridge'):
                raise RuntimeError('备机 bridge 被启动')
            with urllib.request.urlopen('http://127.0.0.1:8302/api/health', timeout=5) as response:
                body = json.loads(response.read(4096))
                if response.status != 200 or body != {'status': 'ok', 'service': 'draw-portal'}:
                    raise RuntimeError('Portal 健康响应无效')
            if target == 'aliyun':
                with urllib.request.urlopen('http://127.0.0.1:8300/bridge-health', timeout=5) as response:
                    if response.status != 200:
                        raise RuntimeError('bridge 健康检查失败')
            return
        except (OSError, ValueError, RuntimeError):
            if time.monotonic() >= deadline:
                raise RuntimeError('服务健康检查未通过') from None
            time.sleep(2)


def _remote_main(action: str, target: str, package: Path | None, backup_id: str | None) -> int:
    if os.name != 'posix' or os.geteuid() != 0 or not REMOTE_ROOT.is_dir():
        raise RuntimeError('远端安装仅用于已配置的 Linux 节点')
    import fcntl
    import pwd
    lock_path = REMOTE_ROOT / '.hutuji-deploy.lock'
    if lock_path.is_symlink():
        raise RuntimeError('部署锁路径无效')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == 'verify':
            _verify_services(target)
            if package:
                manifest, _ = read_package(package, target=target)
                for row in manifest['files']:
                    if sha256(runtime_path(REMOTE_ROOT, row['path']).read_bytes()) != row['sha256']:
                        raise RuntimeError('运行文件与发布包不同')
            print('PASS services' + (' and file hashes' if package else '; file hashes not checked'))
            return 0
        backup_root = REMOTE_ROOT / '.deploy-backups'
        if backup_root.is_symlink():
            raise RuntimeError('备份目录不能是链接')
        backup_root.mkdir(exist_ok=True, mode=0o700)
        if action == 'deploy':
            manifest, files = read_package(package, target=target)
            pause, activate, service_state = _service_hooks(target)
            identifier = uuid.uuid4().hex
            backup = backup_root / identifier
            account = pwd.getpwnam('hutuji')
            print('BACKUP_ID=' + identifier, flush=True)
            install_files(REMOTE_ROOT, backup, manifest, files, pause=pause, activate=activate,
                          recover=activate, owner=(account.pw_uid, account.pw_gid), service_state=service_state)
            print('PASS deployed commit=' + manifest['git_commit'])
        else:
            if not backup_id or not re.fullmatch(r'[0-9a-f]{32}', backup_id):
                raise ValueError('回滚必须明确备份号')
            backup = backup_root / backup_id
            if backup.is_symlink():
                raise ValueError('回滚备份不能是链接')
            record_path = backup / 'ROLLBACK.json'
            if record_path.is_symlink() or record_path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('回滚记录文件无效')
            record = json.loads(record_path.read_text(encoding='utf-8'))
            _validate_rollback_record(record, target)
            service_state = _validate_recovery_state(target, record.get('service_state'))
            pause, activate, _ = _service_hooks(target, recovery_state=service_state)
            rollback_files(REMOTE_ROOT, backup, record, pause=pause, activate=activate)
            print('PASS rollback=' + backup_id)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    for name in ('build', 'deploy', 'verify', 'rollback'):
        actions.add_argument('--' + name, action='store_true')
    actions.add_argument('--remote', choices=('deploy', 'verify', 'rollback'), help=argparse.SUPPRESS)
    parser.add_argument('--target', choices=(*DEPLOY_TARGETS, 'both'), default='aliyun')
    parser.add_argument('--commit')
    parser.add_argument('--paths', nargs='+')
    parser.add_argument('--package', type=Path)
    parser.add_argument('--backup-id')
    args = parser.parse_args()
    if args.remote:
        if args.target == 'both':
            parser.error('远端一次只操作一个节点')
        if args.remote == 'deploy' and args.package is None:
            parser.error('远端部署缺少发布包')
        return _remote_main(args.remote, args.target, args.package, args.backup_id)
    if args.build and not args.commit:
        parser.error('构建必须明确 --commit')
    if args.deploy and (args.package is None or args.target == 'both'):
        parser.error('部署须指定一个节点和该节点的 --package；双机逐节点验证')
    if args.rollback and (not args.backup_id or args.target == 'both'):
        parser.error('回滚须指定一个节点及明确 --backup-id')
    targets = list(DEPLOY_TARGETS) if args.target == 'both' else [args.target]
    for target in targets:
        if args.build:
            build_deployment_package(target, commit=args.commit, paths=args.paths)
        elif args.deploy:
            if not deploy_package(target, args.package):
                return 1
        elif args.verify:
            if not verify_deployment(target, args.package):
                return 1
        elif not rollback_deployment(target, args.backup_id):
            return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, SyntaxError, subprocess.SubprocessError, tarfile.TarError) as error:
        print('发布操作失败：' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
