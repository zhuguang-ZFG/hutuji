#!/usr/bin/env python3
"""只读核对显式的板间协议版本声明；兼容性仍由契约测试与实机证据决定。"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HUTUJI_ROOT = Path(os.environ.get('HUTUJI_ROOT', Path(__file__).resolve().parents[1])).resolve()
GRBL_ROOT = Path(os.environ.get('GRBL_ROOT', HUTUJI_ROOT.parent / 'Grbl_Esp32')).resolve()
XIAOZHI_ROOT = Path(os.environ.get('XIAOZHI_ROOT', HUTUJI_ROOT.parent / 'xiaozhi-esp32')).resolve()
VERSION = r'([0-9]+\.[0-9]+(?:\.[0-9]+)?)'
DECLARATION = re.compile(r'(?:#define\s+(?:HUTUJI_)?PROTOCOL_VERSION\s+|'
                         r'\b(?:kProtocolVersion|kHutujiProtocolVersion)\s*=\s*)"' + VERSION + r'"')
# 只接受同一行紧邻 protocol.md / 板间协议的版本，不跨行追到应用版本。
REFERENCE = re.compile(r'(?:protocol\.md[)\]]*|板间协议(?:与 G-code 契约)?)'
                       r'[ \t]*(?:[:：][ \t]*)?v' + VERSION, re.IGNORECASE)


@dataclass
class ProtocolVersion:
    version: str
    file: Path
    line_number: int
    context: str


@dataclass
class VersionCheckResult:
    name: str
    passed: bool | None
    expected: str | None
    actual: str | None
    file: Path | None
    line: int | None
    message: str


def extract_protocol_version() -> ProtocolVersion | None:
    path = HUTUJI_ROOT / 'docs/protocol.md'
    if not path.is_file():
        return None
    # 标题是真值；版本历史内的 v0.2/v0.3 不能被当作当前契约。
    for index, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if line.startswith('# '):
            match = re.search(r'\bv' + VERSION + r'\b', line)
            return ProtocolVersion(match[1], path, index, line) if match else None
    return None


def _check_files(name: str, files: list[Path], expected: str, pattern=DECLARATION) -> list[VersionCheckResult]:
    present = [path for path in files if path.is_file()]
    if not present:
        return [VersionCheckResult(name, False, expected, None, files[0] if files else None, None, '源码或文档缺失')]
    results = []
    for path in present:
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            for match in pattern.finditer(line.replace(chr(96), '')):
                actual = match[1]
                results.append(VersionCheckResult(name, actual == expected, expected, actual, path, number,
                                                  '显式声明匹配' if actual == expected else '显式声明不匹配'))
    if not results:
        # 未声明与通过严格分开，不能把缺少常量写成已验证兼容。
        results.append(VersionCheckResult(name, None, expected, None, present[0], None, '未声明版本；实现兼容性未由此检查验证'))
    return results


def check_grbl_protocol_version(expected: str) -> list[VersionCheckResult]:
    return _check_files('Grbl 板间协议', [GRBL_ROOT / 'Grbl_Esp32/src/Protocol.h',
                                       GRBL_ROOT / 'Grbl_Esp32/src/ProtocolDecisionCore.h'], expected)


def check_xiaozhi_protocol_version(expected: str) -> list[VersionCheckResult]:
    boards = XIAOZHI_ROOT / 'main/boards'
    files = sorted(path for path in boards.rglob('hutuji*') if path.suffix in ('.h', '.cc'))
    return _check_files('S3 板间协议', files or [boards / 'lichuang-dev/hutuji_pipe.h'], expected)


def check_mcp_protocol_references(expected: str) -> list[VersionCheckResult]:
    return _check_files('MCP G-code 契约引用', [HUTUJI_ROOT / 'mcp-server/hutuji_mcp/gcode_gen.py'], expected, REFERENCE)


def check_documentation_versions(expected: str) -> list[VersionCheckResult]:
    results = []
    for relative in ('AGENTS.md', 'CLAUDE.md', 'README.md', 'docs/release-readiness.md'):
        results.extend(_check_files('文档 ' + relative, [HUTUJI_ROOT / relative], expected, REFERENCE))
    return results


def _report_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(HUTUJI_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def generate_report(protocol_ver: ProtocolVersion | None, all_results: list[VersionCheckResult]) -> dict:
    return {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'protocol_version': protocol_ver.version if protocol_ver else None,
        'protocol_source': {'file': _report_path(protocol_ver.file), 'line': protocol_ver.line_number,
                            'context': protocol_ver.context} if protocol_ver else None,
        'summary': {'total': len(all_results), 'passed': sum(r.passed is True for r in all_results),
                    'failed': sum(r.passed is False for r in all_results),
                    'not_declared': sum(r.passed is None for r in all_results)},
        'results': [{'check': r.name, 'passed': r.passed, 'expected': r.expected, 'actual': r.actual,
                     'file': _report_path(r.file), 'line': r.line, 'message': r.message} for r in all_results],
        'implementation_compatibility_verified': False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', action='store_true', help='保存 JSON 核对报告')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--fix', action='store_true', help='保留旧参数入口，明确拒绝自动改写契约')
    args = parser.parse_args()
    if args.fix:
        parser.error('版本差异须核对契约与实机记录，本工具不自动改写版本')
    version = extract_protocol_version()
    if version is None:
        print('无法从 protocol.md 标题读取当前版本', file=sys.stderr)
        return 1
    results = []
    for check in (check_grbl_protocol_version, check_xiaozhi_protocol_version,
                  check_mcp_protocol_references, check_documentation_versions):
        results.extend(check(version.version))
    report = generate_report(version, results)
    for result in results:
        label = '通过' if result.passed is True else ('失败' if result.passed is False else '未声明')
        print(f'{label} {result.name}：{result.message}')
    if args.report:
        output = args.output or HUTUJI_ROOT / 'results/protocol_version_check.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('报告：', output)
    print('显式版本核对：', json.dumps(report['summary'], ensure_ascii=False))
    print('实现兼容性仍须契约回归、构建和实机验证。')
    return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
