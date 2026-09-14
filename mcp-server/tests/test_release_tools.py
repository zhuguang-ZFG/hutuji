"""运维工具必须忠实报告 Git、协议和发布状态；仅操作临时仓与本地目录。"""
from datetime import datetime, timezone
import importlib.util
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _script(name):
    spec = importlib.util.spec_from_file_location('_release_test_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root).decode('utf-8').strip()


def _repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    _git(root, 'init', '-q')
    _git(root, 'config', 'user.name', 'Release test')
    _git(root, 'config', 'user.email', 'release@example.invalid')
    _git(root, 'config', 'core.autocrlf', 'false')
    (root / 'old.py').write_text('value = 1\n', encoding='utf-8')
    _git(root, 'add', 'old.py')
    _git(root, 'commit', '-qm', 'fixture')
    return root


def test_repo_snapshot_includes_staged_rename_and_untracked_unicode(tmp_path):
    sync = _script('cross_repo_sync')
    root = _repository(tmp_path)
    _git(root, 'mv', 'old.py', '新 名称.py')
    (root / '待审.py').write_text('draft = 1\n', encoding='utf-8')
    state = sync.get_repo_state('fixture', root)
    assert state is not None and not state.is_clean()
    assert {'old.py', '新 名称.py'} <= set(state.dirty_files)
    assert state.untracked_files == ['待审.py']


def test_repo_json_is_parseable_without_status_prose(tmp_path, monkeypatch, capsys):
    sync = _script('cross_repo_sync')
    root = _repository(tmp_path)
    for name in ('HUTUJI_ROOT', 'GRBL_ROOT', 'XIAOZHI_ROOT', 'FZ_ROOT'):
        monkeypatch.setattr(sync, name, root)
    monkeypatch.setattr(sys, 'argv', ['cross_repo_sync.py', '--json'])
    sync.main()
    output = json.loads(capsys.readouterr().out)
    assert len(output['repos']) == 4


def test_missing_repositories_cannot_pass_all_gates():
    sync = _script('cross_repo_sync')
    results = sync.run_all_gates({})
    assert results and not all(results.values())


def test_protocol_finds_real_repository_layout_and_env_roots(tmp_path, monkeypatch):
    grbl = tmp_path / 'motion'
    s3 = tmp_path / 'voice'
    for path in (grbl / 'Grbl_Esp32/src/Protocol.h', s3 / 'main/boards/lichuang-dev/hutuji_pipe.h'):
        path.parent.mkdir(parents=True)
        path.write_text('#define PROTOCOL_VERSION "0.5"\n', encoding='utf-8')
    monkeypatch.setenv('GRBL_ROOT', str(grbl))
    monkeypatch.setenv('XIAOZHI_ROOT', str(s3))
    check = _script('protocol_version_check')
    for result in (check.check_grbl_protocol_version('0.5'), check.check_xiaozhi_protocol_version('0.5')):
        assert result and all(row.passed is True for row in result)
        assert all(row.actual == '0.5' for row in result)


def test_protocol_report_accepts_foreign_path_and_current_timestamp(tmp_path, monkeypatch):
    check = _script('protocol_version_check')
    hub = tmp_path / 'hub'
    hub.mkdir()
    monkeypatch.setattr(check, 'HUTUJI_ROOT', hub)
    version = check.ProtocolVersion('0.5', hub / 'docs/protocol.md', 1, '契约 v0.5')
    external = tmp_path / 'firmware/main.h'
    row = check.VersionCheckResult('firmware', True, '0.5', '0.5', external, 4, '匹配')
    report = check.generate_report(version, [row])
    assert Path(report['results'][0]['file']).resolve() == external.resolve()
    stamp = datetime.fromisoformat(report['timestamp'].replace('Z', '+00:00'))
    assert abs((datetime.now(timezone.utc) - stamp).total_seconds()) < 15


def test_protocol_does_not_treat_another_line_firmware_version_as_contract(tmp_path, monkeypatch):
    check = _script('protocol_version_check')
    monkeypatch.setattr(check, 'HUTUJI_ROOT', tmp_path)
    (tmp_path / 'README.md').write_text('协议详见 protocol.md。\n\n设备版本 v1.0.18\n', encoding='utf-8')
    rows = check.check_documentation_versions('0.5')
    readme = [row for row in rows if row.file == tmp_path / 'README.md']
    assert readme and all(row.passed is not False for row in readme)


def test_missing_mcp_source_is_a_failed_check(tmp_path, monkeypatch):
    check = _script('protocol_version_check')
    monkeypatch.setattr(check, 'HUTUJI_ROOT', tmp_path)
    result = check.check_mcp_protocol_references('0.5')
    assert result and any(row.passed is False for row in result)


def _release_repository(tmp_path):
    root = _repository(tmp_path)
    for name, data in {'mcp-server/hutuji_mcp/server.py': b'VALUE = 1\n',
                       'mcp-server/hutuji_mcp/hutuji_gwrite.toml': b'[gwrite]\n',
                       'draw-portal/draw_portal/static/index.html': b'<p>ready</p>\n'}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    _git(root, 'add', 'mcp-server', 'draw-portal')
    commit = _git(root, 'commit', '-qm', 'release')
    return root, _git(root, 'rev-parse', 'HEAD')


def test_deployment_package_uses_committed_files_including_profile_and_static(tmp_path, monkeypatch):
    deploy = _script('auto_deploy')
    root, commit = _release_repository(tmp_path)
    monkeypatch.setattr(deploy, 'ROOT', root)
    (root / 'mcp-server/hutuji_mcp/server.py').write_bytes(b'UNREVIEWED = True\r\n')
    (root / 'mcp-server/hutuji_mcp/secret.py').write_bytes(b'UNTRACKED = True\n')
    package = deploy.build_deployment_package('aliyun', commit=commit)
    manifest, files = deploy.read_package(package, target='aliyun')
    assert manifest['git_commit'] == commit
    assert files['mcp-server/hutuji_mcp/server.py'] == b'VALUE = 1\n'
    assert 'mcp-server/hutuji_mcp/hutuji_gwrite.toml' in files
    assert 'draw-portal/draw_portal/static/index.html' in files
    assert 'mcp-server/hutuji_mcp/secret.py' not in files
    assert 'hutuji-draw-portal' in manifest['services']


def test_deployment_rejects_wrong_node_before_network(tmp_path, monkeypatch):
    deploy = _script('auto_deploy')
    root, commit = _release_repository(tmp_path)
    monkeypatch.setattr(deploy, 'ROOT', root)
    package = deploy.build_deployment_package('aliyun', commit=commit)
    monkeypatch.setattr(deploy, 'run_ssh', lambda *a, **k: pytest.fail('不合法包不能开始 SSH'))
    with pytest.raises(ValueError):
        deploy.read_package(package, target='jdcloud')
    assert deploy.deploy_package('jdcloud', package) is False


@pytest.mark.parametrize('kind', ['traversal', 'symlink', 'duplicate', 'corrupted', 'service'])
def test_deployment_archive_rejects_unsafe_or_inconsistent_input(tmp_path, monkeypatch, kind):
    deploy = _script('auto_deploy')
    root, commit = _release_repository(tmp_path)
    monkeypatch.setattr(deploy, 'ROOT', root)
    package = deploy.build_deployment_package('aliyun', commit=commit)
    with tarfile.open(package, 'r:gz') as source:
        entries = [(member, source.extractfile(member).read()) for member in source.getmembers()]
    bad = root / (kind + '.tar.gz')
    with tarfile.open(bad, 'w:gz') as archive:
        for member, data in entries:
            if kind == 'service' and member.name.endswith('DEPLOYMENT_MANIFEST.json'):
                manifest = json.loads(data)
                manifest['services'].append('malicious.service')
                data = json.dumps(manifest).encode()
                member.size = len(data)
            elif kind == 'corrupted' and member.name.endswith('server.py'):
                data = b'changed\n'
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        if kind in ('traversal', 'symlink', 'duplicate'):
            member = tarfile.TarInfo('../../escape' if kind == 'traversal' else entries[-1][0].name)
            if kind == 'symlink':
                member.type, member.linkname = tarfile.SYMTYPE, '/etc/passwd'
            archive.addfile(member, io.BytesIO(b''))
    with pytest.raises(ValueError):
        deploy.read_package(bad, target='aliyun')
    assert not (tmp_path / 'escape').exists()


def test_failed_activation_restores_old_files_and_removes_only_new_release_files(tmp_path, monkeypatch):
    deploy = _script('auto_deploy')
    root, commit = _release_repository(tmp_path)
    monkeypatch.setattr(deploy, 'ROOT', root)
    manifest, files = deploy.read_package(deploy.build_deployment_package('aliyun', commit=commit), target='aliyun')
    runtime = tmp_path / 'runtime'
    old = runtime / 'mcp-server/hutuji_mcp/server.py'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'PREVIOUS = 1\n')
    unrelated = old.parent / 'keep.txt'
    unrelated.write_bytes(b'local-data')
    phases = []

    def activate():
        phases.append('activate')
        assert old.read_bytes() == files['mcp-server/hutuji_mcp/server.py']
        raise RuntimeError('health failed')

    with pytest.raises(RuntimeError, match='health failed'):
        deploy.install_files(runtime, tmp_path / 'backup', manifest, files,
                             pause=lambda: phases.append('pause'), activate=activate,
                             recover=lambda: phases.append('recover'))
    assert old.read_bytes() == b'PREVIOUS = 1\n'
    assert unrelated.read_bytes() == b'local-data'
    assert not (runtime / 'draw-portal/draw_portal/static/index.html').exists()
    assert phases == ['pause', 'activate', 'pause', 'recover']


def test_release_paths_do_not_follow_existing_symlinks(tmp_path):
    deploy = _script('auto_deploy')
    runtime, outside = tmp_path / 'runtime', tmp_path / 'outside'
    runtime.mkdir()
    outside.mkdir()
    try:
        (runtime / 'mcp-server').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('当前 Windows 权限不允许创建测试符号链接')
    with pytest.raises(ValueError):
        deploy.runtime_path(runtime, 'mcp-server/hutuji_mcp/server.py')


def test_offline_rollback_recovers_original_services_without_a_live_mcp(monkeypatch, tmp_path):
    deploy = _script('auto_deploy')
    queue_url = 'redis://127.0.0.1:6379/0'
    config = tmp_path / 'mcp.env'
    config.write_text('HUTUJI_DRAW_QUEUE_URL="' + queue_url + '"\n', encoding='utf-8')
    monkeypatch.setattr(deploy, 'MCP_ENV_FILE', config, raising=False)
    monkeypatch.setattr(deploy, '_active', lambda service: False)
    monkeypatch.setattr(deploy, '_queue_is_idle', lambda url: url == queue_url)
    calls = []
    monkeypatch.setattr(deploy, '_systemctl', lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(deploy, '_verify_services', lambda *args, **kwargs: None)
    state = {'services': deploy.DEPLOY_TARGETS['aliyun']['services'],
             'queue_sha256': hashlib.sha256(queue_url.encode()).hexdigest()}

    pause, activate, recovered = deploy._service_hooks('aliyun', recovery_state=state)
    pause()
    activate()

    assert recovered == state
    started = [service for call in calls if call[0] == 'start' for service in call[1:]]
    assert set(started) == set(state['services'])
    assert started[-1] == 'hutuji-cloud-bridge'


def test_offline_rollback_refuses_changed_queue_before_stopping_services(monkeypatch, tmp_path):
    deploy = _script('auto_deploy')
    config = tmp_path / 'mcp.env'
    config.write_text('HUTUJI_DRAW_QUEUE_URL=redis://127.0.0.1:6379/1\n', encoding='utf-8')
    monkeypatch.setattr(deploy, 'MCP_ENV_FILE', config, raising=False)
    monkeypatch.setattr(deploy, '_active', lambda service: False)
    monkeypatch.setattr(deploy, '_systemctl', lambda *a, **k: pytest.fail('队列身份不符不能控制服务'))
    state = {'services': deploy.DEPLOY_TARGETS['aliyun']['services'],
             'queue_sha256': hashlib.sha256(b'redis://127.0.0.1:6379/0').hexdigest()}
    with pytest.raises(RuntimeError, match='队列'):
        deploy._service_hooks('aliyun', recovery_state=state)


@pytest.mark.parametrize('service', ['sshd', 'hutuji-cloud-bridge', 'hutuji-draw-worker@1'])
def test_standby_recovery_record_cannot_introduce_services(monkeypatch, service):
    deploy = _script('auto_deploy')
    state = {'services': deploy.DEPLOY_TARGETS['jdcloud']['services'] + [service],
             'queue_sha256': hashlib.sha256(b'').hexdigest()}
    monkeypatch.setattr(deploy, '_active', lambda service: pytest.fail('先验证服务清单'))
    with pytest.raises(ValueError):
        deploy._service_hooks('jdcloud', recovery_state=state)


def test_rollback_write_failure_stays_stopped_and_can_resume(tmp_path, monkeypatch):
    deploy = _script('auto_deploy')
    root, commit = _release_repository(tmp_path)
    monkeypatch.setattr(deploy, 'ROOT', root)
    manifest, files = deploy.read_package(deploy.build_deployment_package('jdcloud', commit=commit), target='jdcloud')
    runtime = tmp_path / 'runtime'
    for name in files:
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'previous\n')
    backup = tmp_path / 'backup'
    deploy.install_files(runtime, backup, manifest, files, pause=lambda: None,
                         activate=lambda: None, recover=lambda: None)
    record = json.loads((backup / 'ROLLBACK.json').read_text(encoding='utf-8'))
    original_write = deploy._atomic_write
    names = list(files)
    failing_path = runtime / names[1]
    phases = []

    def fail_during_restore(path, data, *args, **kwargs):
        if path == failing_path and data == b'previous\n':
            raise OSError('injected storage failure')
        original_write(path, data, *args, **kwargs)

    monkeypatch.setattr(deploy, '_atomic_write', fail_during_restore)
    with pytest.raises(OSError, match='injected'):
        deploy.rollback_files(runtime, backup, record, pause=lambda: phases.append('pause'),
                              activate=lambda: phases.append('activate'))
    assert phases == ['pause']
    failed_record = json.loads((backup / 'ROLLBACK.json').read_text(encoding='utf-8'))
    assert failed_record['phase'] == 'rollback_failed'

    monkeypatch.setattr(deploy, '_atomic_write', original_write)
    deploy.rollback_files(runtime, backup, failed_record, pause=lambda: phases.append('pause'),
                          activate=lambda: phases.append('activate'))
    assert phases == ['pause', 'pause', 'activate']
    assert all((runtime / name).read_bytes() == b'previous\n' for name in files)
    assert json.loads((backup / 'ROLLBACK.json').read_text(encoding='utf-8'))['phase'] == 'rolled_back'


def test_backup_records_recovery_identity_without_queue_credentials(tmp_path, monkeypatch):
    deploy = _script('auto_deploy')
    root, commit = _release_repository(tmp_path)
    monkeypatch.setattr(deploy, 'ROOT', root)
    manifest, files = deploy.read_package(deploy.build_deployment_package('aliyun', commit=commit), target='aliyun')
    queue_url = 'redis://operator:fake-password@127.0.0.1:6379/0'
    state = {'services': deploy.DEPLOY_TARGETS['aliyun']['services'],
             'queue_sha256': hashlib.sha256(queue_url.encode()).hexdigest()}
    backup = tmp_path / 'backup'
    deploy.install_files(tmp_path / 'runtime', backup, manifest, files, pause=lambda: None,
                         activate=lambda: None, recover=lambda: None, service_state=state)
    raw = (backup / 'ROLLBACK.json').read_text(encoding='utf-8')
    assert json.loads(raw)['service_state'] == state
    assert queue_url not in raw and 'fake-password' not in raw


@pytest.mark.parametrize('with_pending_index', [False, True])
def test_queue_drain_reads_installed_keys_without_importing_broken_application(tmp_path, monkeypatch, live_redis,
                                                                            with_pending_index):
    deploy = _script('auto_deploy')
    url, client = live_redis
    monkeypatch.setattr(deploy, 'REMOTE_ROOT', tmp_path)
    source = tmp_path / 'mcp-server/hutuji_mcp/draw_queue.py'
    source.parent.mkdir(parents=True)
    source.write_text('from unavailable_dependency import value\n'
                      'QUEUE_KEY = "hutuji:release:queue"\nJOB_PREFIX = "hutuji:release:job:"\n'
                      + ('PENDING_KEY = "hutuji:release:pending"\n' if with_pending_index else ''), encoding='utf-8')
    assert deploy._queue_is_idle(url) is True
    client.lpush('hutuji:release:queue', 'old-format')
    assert deploy._queue_is_idle(url) is False
    client.delete('hutuji:release:queue')
    client.hset('hutuji:release:job:first', 'status', 'processing')
    assert deploy._queue_is_idle(url) is False
    client.hset('hutuji:release:job:first', 'status', 'done')
    if with_pending_index:
        client.zadd('hutuji:release:pending', {'awaiting-recovery': 1})
        assert deploy._queue_is_idle(url) is False
        client.delete('hutuji:release:pending')
    assert deploy._queue_is_idle(url) is True


def test_transport_cleanup_disconnect_keeps_confirmed_remote_result(tmp_path, monkeypatch, capsys):
    deploy = _script('auto_deploy')
    monkeypatch.setattr(deploy, 'ROOT', tmp_path)
    monkeypatch.setattr(deploy.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0))

    def ssh(target, argv, **kwargs):
        if argv[0] in ('rm', 'rmdir'):
            raise subprocess.TimeoutExpired('ssh', 1)
        output = '/tmp/hutuji-deploy.Test123\n' if argv[0] == 'mktemp' else 'PASS verified\n'
        if argv[0] == 'sha256sum':
            output = deploy.sha256(Path(deploy.__file__).read_bytes()) + '  deploy.py\n'
        return subprocess.CompletedProcess(argv, 0, output, '')

    monkeypatch.setattr(deploy, '_ssh', ssh)
    assert deploy._transport('jdcloud', 'verify') is True
    assert len(list((tmp_path / 'results').glob('deploy-*/jdcloud-verify.log'))) == 1
    assert 'TimeoutExpired' in capsys.readouterr().err


def test_all_gates_include_portal_and_fz_even_with_clean_motion_tree(tmp_path, monkeypatch):
    sync = _script('cross_repo_sync')
    repos = {name: sync.RepoState(name, tmp_path, 'a' * 40, 'a' * 7, 'main', [], [], '')
             for name in ('hutuji', 'Grbl_Esp32', 'xiaozhi-esp32', 'fz')}
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs['cwd']))
        return subprocess.CompletedProcess(argv, 0, '', '')

    monkeypatch.setattr(sync, 'run_command', run)
    results = sync.run_all_gates(repos)
    assert results['fz_standard'] is True
    assert results['draw_portal'] is True
    assert any(cwd == sync.HUTUJI_ROOT / 'draw-portal' and 'pytest' in argv for argv, cwd in calls)
