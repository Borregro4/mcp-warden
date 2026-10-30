"""Opt-in persistent Codex app-server workload; saved login, bounded turns.

No API key, installation, global config edits, or GUI actions. Raw events and
plans stay in an explicit private directory outside this repository. A real
model invocation requires --execute; --preflight checks transport only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from native_workload import (ROOT, backend_identity, check_tools, connector_config, json_hash, load_plan,
    outside_repository, profile_sample, proxy_identity, sha256_file, source_hashes,
    process_metrics, status_snapshot, verify_files, wait_profile, workspace_file, write_json)

# The official Codex temporary-thread implementation uses the same feature
# isolation pattern. Keep tool discovery available for deferred MCP tools.
ISOLATED_FEATURES = ('apps', 'plugins', 'hooks', 'memories', 'multi_agent',
    'multi_agent_v2', 'enable_fanout', 'goals', 'shell_tool', 'shell_snapshot',
    'unified_exec', 'standalone_web_search', 'image_generation', 'view_image',
    'request_permissions_tool', 'code_mode', 'code_mode_only', 'deferred_executor',
    'context_management', 'tool_suggest', 'current_time_reminder', 'token_budget')
FORBIDDEN_ITEMS = {'commandExecution', 'fileChange', 'webSearch', 'collabToolCall',
                   'imageView', 'imageGeneration', 'dynamicToolCall'}


class JsonlRpc:
    """One owned stdio process, continuously drained during long idle gaps."""
    def __init__(self, command, output, cwd):
        self.output = Path(output)
        self.incoming = queue.Queue()
        self.events = []
        self.unexpected_requests = []
        self.next_id = 1
        self.sensitive_ids = set()
        self.log_lock = threading.Lock()
        self.log = (self.output / 'app-server-events.jsonl').open('w', encoding='utf-8')
        self.errors = (self.output / 'app-server-stderr.log').open('wb')
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.errors, bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _record(self, direction, value):
        # config/read can include user-defined backend env. Never copy that
        # response into raw logs, even though the output directory is private.
        if direction == 'received' and value.get('id') in self.sensitive_ids and 'method' not in value:
            value = {'id': value['id'], 'redacted': 'effective configuration payload excluded'}
        with self.log_lock:
            self.log.write(json.dumps({'monotonic': time.monotonic(), 'direction': direction,
                                       'message': value}, ensure_ascii=False) + '\n')
            self.log.flush()

    def _read(self):
        try:
            for raw in self.process.stdout:
                try:
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ValueError()
                except (ValueError, UnicodeDecodeError):
                    self.incoming.put(RuntimeError('invalid app-server JSONL output'))
                    return
                self._record('received', value)
                self.incoming.put(value)
        finally:
            self.incoming.put(EOFError('app-server stdout closed'))

    def send(self, value):
        self._record('sent', value)
        self.process.stdin.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))
        self.process.stdin.flush()

    def _handle(self, value):
        if isinstance(value, BaseException):
            raise value
        if 'method' in value and 'id' in value:
            self.unexpected_requests.append({'method': value['method'], 'id': value['id']})
            self.send({'id': value['id'], 'error': {'code': -32601,
                'message': 'Interactive requests are unavailable in this bounded workload'}})
        elif 'method' in value:
            self.events.append(value)
        return value

    def receive(self, timeout):
        try:
            return self._handle(self.incoming.get(timeout=max(.001, timeout)))
        except queue.Empty:
            raise TimeoutError('app-server response deadline exceeded') from None

    def call(self, method, params, timeout=90, sensitive=False):
        request_id = self.next_id
        self.next_id += 1
        if sensitive:
            self.sensitive_ids.add(request_id)
        self.send({'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.receive(deadline - time.monotonic())
            if value.get('id') == request_id and 'method' not in value:
                if 'error' in value:
                    raise RuntimeError('app-server RPC failed: ' + method + ': ' + str(value['error'].get('code')))
                return value.get('result', {})
        raise TimeoutError('app-server RPC deadline exceeded: ' + method)

    def drain(self):
        while True:
            try:
                self._handle(self.incoming.get_nowait())
            except queue.Empty:
                return

    def wait_turn(self, thread_id, turn_id, timeout, start_index, max_mcp_calls=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            calls = [e for e in self.events[start_index:] if e.get('method') == 'item/started'
                and e.get('params', {}).get('threadId') == thread_id
                and e['params'].get('turnId') == turn_id
                and e['params'].get('item', {}).get('type') == 'mcpToolCall']
            if len(calls) > max_mcp_calls:
                self.call('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id}, timeout=10)
                raise RuntimeError('fixed MCP call budget exceeded; turn interrupted without retry')
            for event in self.events[start_index:]:
                params = event.get('params', {})
                if event.get('method') == 'turn/completed' and params.get('threadId') == thread_id \
                        and params.get('turn', {}).get('id') == turn_id:
                    return params['turn']
            self.receive(deadline - time.monotonic())
        raise TimeoutError('model turn deadline exceeded; no automatic retry')

    def close(self):
        forced = False
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            code = self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            forced = True
            self.process.terminate()  # Only the Popen handle created by this driver.
            code = self.process.wait(timeout=10)
        self.reader.join(timeout=3)
        self.process.stdout.close()
        self.errors.close()
        self.log.close()
        return {'returncode': code, 'forced_termination': forced}


def isolated_config(effective, connector, server, tools):
    config = {'features.' + name: False for name in ISOLATED_FEATURES}
    config.update({'cloud.skills.enabled': False, 'skills.include_instructions': False,
        'tools.experimental_request_user_input.enabled': False, 'tools.update_plan.enabled': False,
        'web_search': 'disabled', 'project_doc_max_bytes': 0, 'model_reasoning_effort': 'low'})
    existing = effective.get('mcp_servers', {})
    if not isinstance(existing, dict):
        raise ValueError('effective MCP config could not be isolated')
    if server in existing:
        raise ValueError('test server name collides with existing client configuration')
    servers = {name: {'enabled': False} for name in existing}
    servers[server] = {**connector, 'enabled': True, 'required': True,
        'enabled_tools': tools, 'default_tools_approval_mode': 'auto',
        'startup_timeout_sec': 120, 'tool_timeout_sec': 60}
    config['mcp_servers'] = servers
    return config


def inventory(rpc, thread_id, server):
    response = rpc.call('mcpServerStatus/list', {'threadId': thread_id, 'detail': 'toolsAndAuthOnly', 'limit': 100})
    if response.get('nextCursor'):
        raise RuntimeError('unexpected paginated MCP inventory; isolation not established')
    rows = response.get('data', [])
    active = [row for row in rows if row.get('runtimeStatus') != 'disabled']
    if len(active) != 1 or active[0].get('name') != server or active[0].get('runtimeStatus') != 'connected':
        raise RuntimeError('MCP inventory is not exclusively the connected test server')
    selected = active[0]
    metadata = {key: selected.get(key) for key in ('tools', 'serverInfo', 'serverCapabilities')}
    return {'connected_names': [server], 'metadata_sha256': json_hash(metadata),
            'tool_names': sorted(selected.get('tools', {}))}


def completed_items(events, thread_id, turn_id):
    return [e['params']['item'] for e in events if e.get('method') == 'item/completed'
            and e.get('params', {}).get('threadId') == thread_id
            and e['params'].get('turnId') == turn_id]


def transport_probe(rpc, plan, thread_id):
    """Direct app-server MCP calls; no model turn or workload-result mutation."""
    workspace = Path(plan['workspace'])
    target = workspace_file(workspace, 'results/probe.json')
    orders = workspace_file(workspace, 'data/orders.json')
    if target.exists():
        raise RuntimeError('probe requires a fresh results/probe.json destination')
    marker = {'transport_probe': 'native-codex-filesystem', 'model_calls': 0}
    calls = []
    for tool, arguments in [('read_text_file', {'path': str(orders)}),
            ('write_file', {'path': str(target), 'content': json.dumps(marker) + '\n'}),
            ('read_text_file', {'path': str(target)})]:
        response = rpc.call('mcpServer/tool/call', {'threadId': thread_id,
            'server': plan['server_name'], 'tool': tool, 'arguments': arguments})
        if response.get('isError'):
            raise RuntimeError('direct filesystem transport probe returned an MCP error')
        calls.append({'tool': tool, 'response': response})
    checks = verify_files(workspace, [{'path': 'results/probe.json', 'json_equals': marker}])
    original = json.loads(orders.read_text(encoding='utf-8-sig'))
    input_observed = original.get('batch_id') in json.dumps(calls[0]['response'])
    output_observed = 'native-codex-filesystem' in json.dumps(calls[2]['response'])
    if not input_observed or not output_observed or not all(check['passed'] for check in checks):
        raise RuntimeError('direct filesystem probe did not prove input/output contents')
    return {'passed': True, 'model_turns': 0, 'calls': calls, 'file_checks': checks}


def wait_gap(rpc, plan, output, target, last_completed, identity):
    idle_observation = None
    deadline = max(target, last_completed + plan['minimum_idle_seconds'])
    next_sample = 0
    while time.monotonic() < deadline:
        if rpc.process.poll() is not None:
            raise RuntimeError('persistent app-server exited during inactivity')
        rpc.drain()
        now = time.monotonic()
        if now >= next_sample:
            health = status_snapshot(plan['warden_state'])
            sample = profile_sample(health, plan['profile'])
            if proxy_identity(sample) != identity:
                raise RuntimeError('persistent proxy identity changed during inactivity')
            if sample.get('backendPid') is None:
                idle_observation = health
            write_json(output / 'progress.json', {'phase': 'idle', 'remaining_seconds': round(deadline - now, 2),
                       'backend_expiry_observed': idle_observation is not None, 'health': health,
                       'driver_metrics': process_metrics(os.getpid()),
                       'app_server_metrics': process_metrics(rpc.process.pid)})
            next_sample = now + min(30, max(.25, deadline - now))
        time.sleep(min(1, max(.001, deadline - time.monotonic())))
    if idle_observation is None:
        idle_observation = wait_profile(plan['warden_state'], plan['profile'], backend_active=False,
                                        timeout=10, identity=identity)
    return {'idle_elapsed_seconds': time.monotonic() - last_completed, 'expired_status': idle_observation}


def run(args, plan):
    output = outside_repository(args.out)
    output.mkdir(parents=True, exist_ok=False)
    connector = connector_config(plan)
    frozen = source_hashes(plan)
    frozen[str(Path(__file__).resolve())] = sha256_file(__file__)
    frozen[str(args.codex.resolve())] = sha256_file(args.codex)
    criteria = {'version': 1, 'client': 'Codex app-server stdio; saved local login',
        'model_requested': args.model, 'effort_requested': 'low', 'max_model_turns': len(plan['turns']),
        'automatic_model_retries': 0, 'plan_sha256': plan['_plan_sha256'], 'source_hashes': frozen,
        'duration_seconds': plan['duration_seconds'], 'minimum_idle_seconds': plan['minimum_idle_seconds'],
        'checks': ['same app-server/thread/proxy identity', 'real successful allowed MCP events each turn',
            'verified file effects each turn', 'idle backend exit and wake', 'unchanged tool metadata and sources',
            'clean connector exit'], 'preflight_only': args.preflight}
    write_json(output / 'criteria.json', criteria)
    version = subprocess.check_output([str(args.codex), '--version'], text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, timeout=10).strip()
    command = [str(args.codex), 'app-server', '--listen', 'stdio://']
    # No thread exists yet, so MCP backends cannot start before config/read.
    for name in ISOLATED_FEATURES:
        command.extend(['-c', 'features.' + name + '=false'])
    command.extend(['-c', 'cloud.skills.enabled=false', '-c', 'skills.include_instructions=false',
                    '-c', 'project_doc_max_bytes=0', '-c', 'web_search="disabled"'])
    rpc = None
    result = {'client_version': version, 'client': 'codex-app-server', 'model_requested': args.model,
              'preflight_only': args.preflight, 'passed': False, 'turns': [], 'source_hashes': frozen}
    total_began = began = time.monotonic()
    try:
        rpc = JsonlRpc(command, output, plan['workspace'])
        result['app_server_pid'] = rpc.process.pid
        result['driver_metrics_at_start'] = process_metrics(os.getpid())
        result['app_server_metrics_at_start'] = process_metrics(rpc.process.pid)
        rpc.call('initialize', {'clientInfo': {'name': 'mcp_warden_endurance', 'title': 'MCP Warden bounded endurance',
                                              'version': '0.1.0'}})
        rpc.send({'method': 'initialized', 'params': {}})
        effective = rpc.call('config/read', {'includeLayers': False, 'cwd': plan['workspace']}, sensitive=True)
        thread_config = isolated_config(effective.get('config', {}), connector, plan['server_name'], plan['allowed_tools'])
        result['disabled_existing_server_count'] = len(thread_config['mcp_servers']) - 1
        effective = None  # Do not retain or persist unrelated global configuration.
        started = rpc.call('thread/start', {'model': args.model, 'cwd': plan['workspace'], 'ephemeral': True,
            'approvalPolicy': 'never', 'sandbox': 'read-only', 'config': thread_config,
            'developerInstructions': 'Execute the bounded synthetic filesystem MCP workload exactly. '
                'Use only the configured MCP tools and tool_search for their discovery. Do not use shell, '
                'web, delegates, built-in file tools or skills. Never simulate tool results. '
                'After the requested operations report only the observed result briefly.'}, timeout=150)
        thread_id = started['thread']['id']
        result['thread_id'] = thread_id
        result['model_observed'] = started.get('model')
        result['reasoning_effort_observed'] = started.get('reasoningEffort')
        if started.get('model') != args.model or started.get('reasoningEffort') != 'low':
            raise RuntimeError('requested model and low effort were not confirmed before inference')
        baseline_inventory = inventory(rpc, thread_id, plan['server_name'])
        result['initial_inventory'] = baseline_inventory
        if args.preflight:
            if args.probe_tools:
                result['direct_transport_probe'] = transport_probe(rpc, plan, thread_id)
            result['transport_preflight_passed'] = not rpc.unexpected_requests
        else:
            began = time.monotonic()
            identity = None
            previous_backend = None
            completed = None
            for index, turn in enumerate(plan['turns']):
                phase = {'id': turn['id'], 'index': index, 'scheduled_offset_seconds': plan['turn_offsets_seconds'][index]}
                result['turns'].append(phase)
                if index:
                    phase['gap'] = wait_gap(rpc, plan, output,
                        began + plan['turn_offsets_seconds'][index], completed, identity)
                    phase['previous_backend_after_idle'] = process_metrics(*previous_backend)
                    if phase['previous_backend_after_idle'].get('alive') is not False:
                        raise RuntimeError('previous backend identity did not verifiably exit while idle')
                if any(sha256_file(path) != value for path, value in frozen.items()):
                    raise RuntimeError('frozen source, backend or plan changed before inference')
                event_index = len(rpc.events)
                now = time.monotonic()
                if index == 0:
                    began = now  # Schedule all later stages from the actual first turn start.
                phase['started_offset_seconds'] = now - began
                write_json(output / 'progress.json', {'phase': 'turn', 'turn_id': turn['id'], 'index': index})
                response = rpc.call('turn/start', {'threadId': thread_id, 'model': args.model, 'effort': 'low',
                    'input': [{'type': 'text', 'text': turn['prompt']}]}, timeout=30)
                turn_id = response['turn']['id']
                phase['turn_id'] = turn_id
                final = rpc.wait_turn(thread_id, turn_id, plan['turn_timeout_seconds'], event_index,
                    max_mcp_calls=plan.get('max_mcp_calls_per_turn', 12))
                completed = time.monotonic()
                phase['completed_offset_seconds'] = completed - began
                phase['turn_status'] = final.get('status')
                items = completed_items(rpc.events[event_index:], thread_id, turn_id)
                calls = [item for item in items if item.get('type') == 'mcpToolCall']
                phase['mcp_calls'] = calls
                phase['file_checks'] = verify_files(plan['workspace'], turn['verify'])
                health = wait_profile(plan['warden_state'], plan['profile'], backend_active=True, timeout=10,
                                      identity=identity)
                sample = profile_sample(health, plan['profile'])
                phase['active_status'] = health
                if identity is None:
                    identity = proxy_identity(sample)
                current_inventory = inventory(rpc, thread_id, plan['server_name'])
                phase['inventory'] = current_inventory
                checks = check_tools(calls, plan['server_name'], plan['allowed_tools'], turn['required_tools'],
                                     turn['required_tool_groups'])
                checks.update({'turn_completed': final.get('status') == 'completed',
                    'file_effects_verified': all(c['passed'] for c in phase['file_checks']),
                    'no_unrelated_actions': not any(i.get('type') in FORBIDDEN_ITEMS for i in items),
                    'no_interactive_requests': not rpc.unexpected_requests,
                    'same_proxy': proxy_identity(sample) == identity,
                    'backend_woke_after_expiry': index == 0 or (backend_identity(sample) != previous_backend
                        and phase['gap']['idle_elapsed_seconds'] >= plan['minimum_idle_seconds']),
                    'tool_metadata_unchanged': current_inventory['metadata_sha256'] == baseline_inventory['metadata_sha256'],
                    'frozen_sources_unchanged': all(sha256_file(path) == value for path, value in frozen.items())})
                phase['checks'] = checks
                phase['passed'] = all(checks.values())
                previous_backend = backend_identity(sample)
                phase['driver_metrics'] = process_metrics(os.getpid())
                phase['app_server_metrics'] = process_metrics(rpc.process.pid)
                write_json(output / 'result.json', result)
                if not phase['passed']:
                    raise RuntimeError('native workload acceptance checks failed; no automatic model retry')
            # Honor a plan with a final observation tail without extra inference.
            if time.monotonic() < began + plan['duration_seconds']:
                result['final_idle'] = wait_gap(rpc, plan, output, began + plan['duration_seconds'], completed, identity)
            result['elapsed_workload_seconds'] = time.monotonic() - began
            result['workload_passed'] = len(result['turns']) == len(plan['turns']) and all(t['passed'] for t in result['turns'])
    except (Exception, KeyboardInterrupt) as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if rpc:
            result['shutdown'] = rpc.close()
            result['unexpected_requests'] = rpc.unexpected_requests
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            health = status_snapshot(plan['warden_state'])
            if health.get('sessionCount') == 0:
                break
            time.sleep(.25)
        result['health_after_exit'] = health
        result['elapsed_total_seconds'] = time.monotonic() - total_began
        result['cleanup_passed'] = bool(rpc) and health.get('sessionCount') == 0 \
            and result['shutdown']['returncode'] == 0 and not result['shutdown']['forced_termination']
        result['passed'] = result.get('transport_preflight_passed' if args.preflight else 'workload_passed', False) \
            and result['cleanup_passed'] and not result.get('error')
        write_json(output / 'result.json', result)
        write_json(output / 'progress.json', {'phase': 'completed', 'passed': result['passed'],
                                             'preflight_only': args.preflight})
    print(json.dumps({k: result.get(k) for k in ('passed', 'preflight_only', 'client_version',
                      'model_observed', 'cleanup_passed', 'elapsed_workload_seconds', 'error')}))
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--codex', type=Path)
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--probe-tools', action='store_true',
                        help='with --preflight, directly read/write/read a fresh results/probe.json without a model')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--preflight', action='store_true', help='start isolated transport, without any model turn')
    args = parser.parse_args()
    if args.probe_tools and not args.preflight:
        parser.error('--probe-tools requires --preflight')
    try:
        plan = load_plan(args.plan)
        connector_config(plan)
        outside_repository(args.out)
        if not args.execute and not args.preflight:
            print(json.dumps({'prepared': True, 'model_turns_planned': len(plan['turns']),
                'duration_seconds': plan['duration_seconds'], 'plan_sha256': plan['_plan_sha256'],
                'note': '--execute consumes the existing signed-in account quota; no automatic retries'}))
            return 0
        if os.name != 'nt' or not args.codex or not args.codex.is_file():
            parser.error('execution requires Windows and an explicit installed --codex executable')
        return run(args, plan)
    except (ValueError, OSError, KeyError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
