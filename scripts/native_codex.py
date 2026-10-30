"""Opt-in native Codex model acceptance. Uses the existing local account.

No credentials are read, copied or printed. Raw model output stays in the
explicit output directory. This script consumes the signed-in account's quota.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--codex', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--transport', choices=('gateway', 'stdio'), default='gateway')
    args = parser.parse_args()
    if not args.execute:
        print('Prepared only. --execute calls the signed-in Codex model for this test.')
        return 0
    if os.name != 'nt' or not args.codex.is_file():
        parser.error('Windows and an explicit Codex executable are required')
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error('raw output directory must be outside the repository')
    out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('acceptance_warden', ROOT / 'warden.py')
    warden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(warden)
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    state = out / 'state'
    config = warden.initialize(state, port=port, idle_seconds=20,
                              transport=args.transport, restart_policy='stateless' if args.transport == 'stdio' else 'manual')
    marker = 'native-codex-' + uuid.uuid4().hex
    criteria = {
        'client': 'native Codex CLI using saved subscription auth; no user config',
        'model_requested': args.model,
        'checks': ['MCP startup', 'echo marker', 'sleep completion', 'same backend PID',
                   'second echo marker', 'no unrelated tools', 'clean connector exit'],
        'marker': marker, 'timeout_seconds': 180, 'transport': args.transport,
        'source_hashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
            ['warden.py', 'runtime/gateway.mjs', 'runtime/connect.mjs', 'runtime/lazy_stdio.py',
             'runtime/owned_bridge.py', 'examples/echo_server.py']
            + (['runtime/idle_stdio.py'] if args.transport == 'stdio' else [])},
    }
    (out / 'criteria.json').write_text(json.dumps(criteria, indent=2), encoding='utf-8')
    prompt = (
        'This is a bounded MCP transport acceptance test. If the warden tools are deferred, '
        'use tool_search to discover their definitions first. Do not read files, run shell commands, '
        'search the web, delegate, or invoke unrelated tools. Then use the warden MCP tools in this exact order: '
        f'1) echo value={marker}; 2) sleep seconds=2 value={marker}-sleep; '
        f'3) echo value={marker}-after. Verify all three return the same backend PID. '
        'Then reply with only PASS and the PID if observed, otherwise FAIL with a short reason. '
        'Do not simulate tool results. Make exactly these three MCP calls.'
    )
    connector = ['-I', '-B', '-u', str(ROOT / 'warden.py'), 'connect', '--state', str(state), '--profile', 'example']
    command = [str(args.codex), 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
               '--json', '--sandbox', 'read-only', '-C', str(out), '-m', args.model,
               '-c', 'model_reasoning_effort="low"', '-c', 'project_doc_max_bytes=0',
               '-c', 'mcp_servers.warden.command=' + json.dumps(sys.executable),
               '-c', 'mcp_servers.warden.args=' + json.dumps(connector),
               '-c', 'mcp_servers.warden.required=true',
               '-c', 'mcp_servers.warden.default_tools_approval_mode="auto"', prompt]
    stdout = (out / 'codex.jsonl').open('wb')
    stderr = (out / 'codex.stderr').open('wb')
    gateway_log = (out / 'gateway.stderr').open('wb')
    gateway = subprocess.Popen([shutil.which('node'), str(ROOT / 'runtime/gateway.mjs'),
        '--config', str(state / 'config.json'), '--stdin-control'], stdin=subprocess.PIPE,
        stdout=gateway_log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW) if args.transport == 'gateway' else None
    process = None
    began = time.monotonic()
    result = {'client_version': subprocess.check_output([str(args.codex), '--version'], text=True).strip(),
              'model_requested': args.model, 'completed': False, 'transport': args.transport,
              'source_hashes': criteria['source_hashes']}
    try:
        for _ in range(100 if gateway else 0):
            try:
                warden.get_status(state)
                break
            except warden.WardenError:
                time.sleep(.05)
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            result['returncode'] = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            result['timeout'] = True
            process.terminate()
            process.wait(timeout=10)
        result['elapsed_seconds'] = round(time.monotonic() - began, 3)
        stdout.close()
        rows = []
        for line in (out / 'codex.jsonl').read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        calls = [row['item'] for row in rows if row.get('type') == 'item.completed'
                 and row.get('item', {}).get('type') == 'mcp_tool_call']
        result['calls'] = calls
        result['usage'] = next((row.get('usage') for row in rows if row.get('type') == 'turn.completed'), None)
        result['final'] = [row['item']['text'] for row in rows if row.get('type') == 'item.completed'
                           and row.get('item', {}).get('type') == 'agent_message']
        expected_tools = ['echo', 'sleep', 'echo']
        expected_values = [marker, marker + '-sleep', marker + '-after']
        observed = []
        for call in calls:
            response = call.get('result') or {}
            data = response.get('structuredContent')
            if not isinstance(data, dict):
                for block in response.get('content', []):
                    try:
                        data = json.loads(block.get('text', ''))
                    except (ValueError, TypeError):
                        continue
                    if isinstance(data, dict) and 'pid' in data:
                        break
            observed.append(data if isinstance(data, dict) else {})
        other_actions = [row['item']['type'] for row in rows if row.get('type') == 'item.completed'
                         and row.get('item', {}).get('type') in ('command_execution', 'web_search', 'file_change')]
        checks = {
            'exit_success': result.get('returncode') == 0,
            'three_successful_calls': len(calls) == 3 and all(not c.get('error') and c.get('status') == 'completed' for c in calls),
            'ordered_tools': [c.get('tool') for c in calls] == expected_tools,
            'actual_values': [d.get('value') for d in observed] == expected_values,
            'same_positive_pid': len(observed) == 3 and all(type(d.get('pid')) is int and d['pid'] > 0 for d in observed)
                and len({d['pid'] for d in observed}) == 1,
            'no_unrelated_actions': not other_actions,
        }
        for _ in range(60):
            health = warden.get_stdio_status(state) if args.transport == 'stdio' else warden.get_status(state)
            if health['sessionCount'] == 0:
                break
            time.sleep(.1)
        checks['session_released_after_exit'] = health['sessionCount'] == 0
        result['checks'] = checks
        result['completed'] = result.get('returncode') == 0
        result['passed'] = all(checks.values())
        result['gateway_health_after_exit'] = health
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if gateway:
            gateway.stdin.close()
            try:
                gateway.wait(timeout=15)
            except subprocess.TimeoutExpired:
                gateway.terminate()
                gateway.wait(timeout=5)
        stdout.close()
        stderr.close()
        gateway_log.close()
        (out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({key: result.get(key) for key in ('completed', 'passed', 'checks', 'returncode', 'timeout', 'elapsed_seconds', 'client_version')}))
    return 0 if result.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
