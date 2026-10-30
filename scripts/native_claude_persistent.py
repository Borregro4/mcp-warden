"""Opt-in, bounded native Claude work on one persistent SDK connection.

No model is started without --execute. Plans, raw messages, diagnostics and
runtime state must be private, outside the repository. The SDK is optional.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import dataclasses
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SDK_VERSION = '0.2.175'


def load_local(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def full_tool_name(server_name, name):
    return name if name.startswith('mcp__') else f'mcp__{server_name}__{name}'


class TurnCapture:
    """Validate observed tool exchanges; the model's final text is not proof."""
    def __init__(self):
        self.calls = []
        self.replies = []
        self.result = None
        self.models = set()

    def observe(self, message, elapsed):
        kind = type(message).__name__
        if kind in ('AssistantMessage', 'UserMessage'):
            model = getattr(message, 'model', None)
            if model:
                self.models.add(model)
            content = getattr(message, 'content', [])
            for block in content if isinstance(content, list) else []:
                if type(block).__name__ == 'ToolUseBlock':
                    self.calls.append({'id': block.id, 'name': block.name,
                                       'observed_seconds': elapsed})
                elif type(block).__name__ == 'ToolResultBlock':
                    self.replies.append({'tool_use_id': block.tool_use_id,
                                         'is_error': bool(getattr(block, 'is_error', False)),
                                         'observed_seconds': elapsed})
        elif kind == 'ResultMessage':
            self.result = message

    def summary(self, required_groups, allowed, max_tool_calls=16, required=()):
        call_ids = [call['id'] for call in self.calls]
        reply_ids = [reply['tool_use_id'] for reply in self.replies]
        counts = Counter(call['name'] for call in self.calls)
        required_counts = Counter(required)
        checks = {
            'result_success': self.result is not None
                and self.result.subtype == 'success' and not self.result.is_error,
            'required_tools_observed': all(counts[name] >= number for name, number in required_counts.items()),
            'required_tool_groups_observed': all(any(counts[name] for name in group) for group in required_groups),
            'only_allowed_tools': bool(call_ids) and all(call['name'] in allowed for call in self.calls),
            'tool_call_limit_respected': 0 < len(call_ids) <= max_tool_calls,
            'unique_tool_ids': len(call_ids) == len(set(call_ids)),
            'every_call_has_one_reply': bool(call_ids) and Counter(call_ids) == Counter(reply_ids),
            'no_tool_errors': bool(self.replies) and not any(reply['is_error'] for reply in self.replies),
        }
        starts = {call['id']: call['observed_seconds'] for call in self.calls}
        intervals = [{'name': call['name'], 'observed_seconds': reply['observed_seconds'] - starts[call['id']]}
                     for call in self.calls for reply in self.replies
                     if call['id'] == reply['tool_use_id']]
        result = self.result
        return {
            'checks': checks,
            'tool_calls': [{'name': call['name'], 'observed_seconds': call['observed_seconds']} for call in self.calls],
            'tool_exchange_intervals': intervals,
            'tool_timing_scope': 'SDK message observation interval; includes client scheduling, not backend execution time',
            'models_observed': sorted(self.models),
            'result_subtype': getattr(result, 'subtype', None),
            'native_duration_ms': getattr(result, 'duration_ms', None),
            'native_api_duration_ms': getattr(result, 'duration_api_ms', None),
            'session_id_sha256': sha256(result.session_id.encode()) if result else None,
            'usage': getattr(result, 'usage', None),
            'usage_scope': 'client-reported token counts; not subscription quota or billed cost',
        }


class ToolBudget:
    """A PreToolUse hook bounds actual work, including parallel tool batches."""
    def __init__(self, allowed, limit):
        self.allowed = set(allowed)
        self.limit = limit
        self.turn_id = None
        self.attempts = 0
        self.denied = 0

    def begin(self, turn_id):
        self.turn_id, self.attempts, self.denied = turn_id, 0, 0

    async def guard(self, value, tool_use_id, context):
        # No awaits before incrementing: concurrent hooks share one event loop.
        self.attempts += 1
        if self.turn_id is None or value.get('tool_name') not in self.allowed or self.attempts > self.limit:
            self.denied += 1
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny', 'permissionDecisionReason': 'The fixed native-workload tool allowlist or per-turn limit was reached.'}}
        return {}


class HiddenAnyio:
    """Adapt only the SDK transport's module reference, never its installed file."""
    def __init__(self, wrapped, spawned):
        self.wrapped = wrapped
        self.spawned = spawned

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    async def open_process(self, *args, **kwargs):
        if os.name == 'nt':
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW
        process = await self.wrapped.open_process(*args, **kwargs)
        self.spawned.append(process)
        return process


@contextmanager
def hide_sdk_windows(module, spawned):
    original = module.anyio
    module.anyio = HiddenAnyio(original, spawned)
    try:
        yield
    finally:
        module.anyio = original


def validate_saved_login_environment():
    # Saved account login only: never silently select paid API/third-party auth.
    direct = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN')
    providers = ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY')
    if any(os.environ.get(name) for name in direct) or any(os.environ.get(name) not in (None, '', '0', 'false') for name in providers):
        raise ValueError('external authentication environment is present; this check requires saved subscription login')


def native_preflight(executable):
    common = {'capture_output': True, 'text': True, 'timeout': 20,
              'creationflags': subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0}
    auth = subprocess.run([str(executable), 'auth', 'status'], **common)
    if auth.returncode != 0:
        raise RuntimeError('saved Claude authentication check did not succeed')
    status = json.loads(auth.stdout)
    if status.get('authMethod') != 'claude.ai' or status.get('loggedIn') is not True:
        raise RuntimeError('native workload requires an existing Claude subscription login')
    version = subprocess.run([str(executable), '--version'], **common)
    if version.returncode != 0:
        raise RuntimeError('native client version check did not succeed')
    return {'auth_method': status['authMethod'], 'version': version.stdout.strip()}


class Recorder:
    def __init__(self, output, workload, state, sample_seconds=30):
        self.output = output
        self.workload = workload
        self.state = state
        self.sample_seconds = sample_seconds
        self.began = time.monotonic()
        self.metric_module = load_local('claude_work_metrics', 'benchmark_processes.py') if os.name == 'nt' else None
        self.reader = self.metric_module.BenchmarkReader() if self.metric_module else None
        self.root_created = self.reader.query(os.getpid())['created'] if self.reader else None
        self.samples = []
        self.known_backends = set()
        self.known_proxies = set()
        self.raw = (output / 'claude-output.jsonl').open('w', encoding='utf-8')
        self.phases = (output / 'phase-observations.jsonl').open('w', encoding='utf-8')
        self.errors = (output / 'claude-stderr.log').open('w', encoding='utf-8')

    def elapsed(self):
        return time.monotonic() - self.began

    def note(self, event, **data):
        value = {'event': event, 'elapsed_seconds': self.elapsed(),
                 'utc': datetime.now(timezone.utc).isoformat(), **data}
        self.phases.write(json.dumps(value, default=str) + '\n')
        self.phases.flush()
        # A compact readback allows a supervising chat to observe progress.
        (self.output / 'progress.json').write_text(json.dumps(value, default=str), encoding='utf-8')
        return value

    def sample(self, phase):
        status = self.workload.status_snapshot(self.state)
        metric = self.metric_module.collect(self.reader, [os.getpid()], {os.getpid(): self.root_created}) if self.reader else {'unavailable': 'Windows only'}
        for sample in status.get('samples', []):
            owner = next((row for row in metric.get('processes', []) if row['pid'] == sample.get('ownerPid')
                          and row['created'] == str(sample.get('ownerCreated'))), None)
            children = [row for row in metric.get('processes', []) if owner and row.get('parent') == owner['pid']]
            # Status files refresh every two seconds. At a short smoke wake,
            # observe the proxy's sole direct child without waiting for that
            # telemetry refresh, and retain the original reported PID too.
            backend = children[0] if len(children) == 1 else None
            sample['backendObservedPid'] = backend['pid'] if backend else None
            sample['backendObservedCreated'] = backend['created'] if backend else None
            identity = observed_backend_identity(sample, self.workload)
            if identity:
                self.known_backends.add(identity)
            owner = self.workload.proxy_identity(sample)
            if owner:
                self.known_proxies.add(owner)
        known = [self.workload.process_metrics(pid, created) for pid, created in sorted(self.known_backends)]
        proxies = [self.workload.process_metrics(pid, created) for pid, created in sorted(self.known_proxies)]
        value = self.note('sample', phase=phase, warden=status, process_tree=metric,
                          known_backends=known, known_proxies=proxies)
        self.samples.append(value)
        return value

    async def wait_until(self, deadline, phase='idle'):
        while self.elapsed() < deadline:
            self.sample(phase)
            await asyncio.sleep(min(self.sample_seconds, max(0, deadline - self.elapsed())))

    def message(self, message, turn):
        self.raw.write(json.dumps({'turn': turn, 'elapsed_seconds': self.elapsed(),
                                  'class': type(message).__name__,
                                  'message': dataclasses.asdict(message)}, default=str) + '\n')
        self.raw.flush()

    def stderr(self, line):
        self.errors.write(line + '\n')
        self.errors.flush()

    def close(self):
        self.raw.close()
        self.phases.close()
        self.errors.close()


def observed_backend_identity(sample, workload):
    if sample and sample.get('backendObservedPid') and sample.get('backendObservedCreated'):
        return sample['backendObservedPid'], sample['backendObservedCreated']
    return workload.backend_identity(sample)


def lifecycle_checks(samples, turns, profile, workload):
    observed = [(sample['elapsed_seconds'], workload.profile_sample(sample['warden'], profile), sample['phase'])
                for sample in samples if sample.get('event') == 'sample']
    active = [(at, value) for at, value, phase in observed if value and observed_backend_identity(value, workload)]
    owners = {workload.proxy_identity(value) for _, value, _ in observed if value}
    backend_generations = []
    for turn in turns:
        candidates = [value for at, value in active if turn['began_seconds'] <= at <= turn['ended_seconds'] + 3]
        backend_generations.append(observed_backend_identity(candidates[-1], workload) if candidates else None)
    idle_gaps = []
    for index, (previous, current) in enumerate(zip(turns, turns[1:])):
        identity = backend_generations[index]
        idle_gaps.append(identity is not None and any(previous['ended_seconds'] <= sample['elapsed_seconds'] <= current['began_seconds']
            and workload.profile_sample(sample['warden'], profile)
            and workload.profile_sample(sample['warden'], profile).get('backendPid') is None
            and any((value.get('pid'), value.get('created')) == identity and value.get('alive') is False
                    for value in sample.get('known_backends', [])) for sample in samples))
    final = samples[-1]['warden'] if samples else {}
    return {
        'same_live_proxy_identity': bool(owners) and len(owners) == 1,
        'idle_backend_stop_between_turns': bool(idle_gaps) and all(idle_gaps),
        'backend_generation_observed_per_turn': bool(backend_generations) and None not in backend_generations,
        'fresh_backend_after_each_idle': len(backend_generations) > 1 and None not in backend_generations
            and all(a != b for a, b in zip(backend_generations, backend_generations[1:])),
        'no_live_warden_session_after_disconnect': workload.profile_sample(final, profile) is None,
        'owned_backend_cleanup_verified': bool(samples) and bool(samples[-1].get('known_backends'))
            and all(value.get('alive') is False for value in samples[-1]['known_backends']),
        'owned_proxy_cleanup_verified': bool(samples) and bool(samples[-1].get('known_proxies'))
            and all(value.get('alive') is False for value in samples[-1]['known_proxies']),
    }


async def wait_connected(client, server_name, timeout):
    began = time.monotonic()
    while time.monotonic() - began < timeout:
        async with asyncio.timeout(max(.01, timeout - (time.monotonic() - began))):
            status = await client.get_mcp_status()
        all_servers = status.get('mcpServers', [])
        if any(server.get('name') != server_name for server in all_servers):
            raise RuntimeError('unexpected MCP server appeared in the isolated native client')
        servers = [server for server in all_servers if server.get('name') == server_name]
        if len(servers) == 1 and servers[0].get('status') == 'connected':
            return servers[0]
        if any(server.get('status') in ('failed', 'needs-auth', 'disabled') for server in servers):
            raise RuntimeError('isolated MCP was not connected before inference')
        await asyncio.sleep(.25)
    raise TimeoutError('isolated MCP startup deadline exceeded')


def catalog_snapshot(server):
    fields = ('name', 'description', 'inputSchema', 'outputSchema', 'annotations')
    tools = [{name: tool[name] for name in fields if name in tool}
             for tool in server.get('tools', []) if isinstance(tool, dict)]
    tools.sort(key=lambda tool: tool.get('name', ''))
    return {'server_info': server.get('serverInfo'), 'tools': tools}


async def run_session(client, plan, recorder, workload, budget=None, turns=None):
    """One client object, one connected lifetime, sequential bounded user turns."""
    server_name = plan['server_name']
    allowed = {full_tool_name(server_name, name) for name in plan['allowed_tools']}
    turns = [] if turns is None else turns
    server = await wait_connected(client, server_name, plan.get('startup_timeout_seconds', 120))
    initial_catalog = workload.json_hash(catalog_snapshot(server))
    recorder.note('client_ready', client_catalog_sha256=initial_catalog)
    recorder.sample('cold')
    schedule_base = recorder.elapsed()
    previous_end = None
    for index, turn in enumerate(plan['turns']):
        target = schedule_base + plan['turn_offsets_seconds'][index]
        if previous_end is not None:
            target = max(target, previous_end + plan.get('minimum_idle_seconds', 900))
        await recorder.wait_until(target)
        recorder.sample('cold_before_work' if index == 0 else 'wake_before_work')
        began = recorder.elapsed()
        if index == 0:
            schedule_base = began
        idle_gap = None if previous_end is None else began - previous_end
        recorder.note('turn_start', turn=turn['id'], index=index, idle_gap_seconds=idle_gap)
        capture = TurnCapture()
        if budget is not None:
            budget.begin(turn['id'])
        async with asyncio.timeout(plan.get('turn_timeout_seconds', 300)):
            await client.query(turn['prompt'])
            async for message in client.receive_response():
                recorder.message(message, turn['id'])
                capture.observe(message, recorder.elapsed())
                if type(message).__name__ == 'UserMessage' and any(type(block).__name__ == 'ToolResultBlock'
                        for block in message.content if isinstance(message.content, list)):
                    recorder.sample('tool_result_observed')
        ended = recorder.elapsed()
        checks = workload.verify_files(plan['workspace'], turn['verify'])
        groups = [[full_tool_name(server_name, name) for name in group] for group in turn['required_tool_groups']]
        required = [full_tool_name(server_name, name) for name in turn.get('required_tools', [])]
        result = capture.summary(groups, allowed, plan.get('max_mcp_calls_per_turn', 16), required)
        result['checks']['no_tool_budget_denials'] = budget is None or budget.denied == 0
        server = await wait_connected(client, server_name, plan.get('startup_timeout_seconds', 120))
        result['client_catalog_sha256'] = workload.json_hash(catalog_snapshot(server))
        result['checks']['client_catalog_unchanged'] = result['client_catalog_sha256'] == initial_catalog
        result.update(id=turn['id'], began_seconds=began, ended_seconds=ended,
                      elapsed_seconds=ended-began, idle_gap_seconds=idle_gap,
                      file_verification=checks)
        turns.append(result)
        recorder.note('turn_end', turn=turn['id'], index=index, summary=result)
        recorder.sample('cold_after_work' if index == 0 else 'wake_after_work')
        previous_end = ended
        if not all(result['checks'].values()) or not all(check['passed'] for check in checks):
            break  # Fail closed; no model retries or additional user turns.
    if len(turns) == len(plan['turns']) and all(all(turn['checks'].values()) and
            all(check['passed'] for check in turn['file_verification']) for turn in turns):
        await recorder.wait_until(schedule_base + plan['duration_seconds'])
    return turns


async def execute(plan, executable, output, workload):
    validate_saved_login_environment()
    sources_before = workload.source_hashes(plan)
    driver_before = sha256(Path(__file__).read_bytes())
    if importlib.metadata.version('claude-agent-sdk') != SDK_VERSION:
        raise RuntimeError(f'this driver requires the verified isolated claude-agent-sdk=={SDK_VERSION}')
    native = native_preflight(executable)
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
    from claude_agent_sdk._internal.transport import subprocess_cli
    connector = workload.connector_config(plan)
    recorder = Recorder(output, workload, plan['warden_state'], plan.get('sample_seconds', 30))
    budget = ToolBudget([full_tool_name(plan['server_name'], name) for name in plan['allowed_tools']], plan.get('max_mcp_calls_per_turn', 16))
    spawned = []
    turns, error = [], None
    try:
        options = ClaudeAgentOptions(
            cli_path=str(executable), cwd=str(plan['workspace']), model=plan.get('model', 'sonnet'), effort='low',
            mcp_servers={plan['server_name']: connector}, strict_mcp_config=True,
            tools=[], allowed_tools=[full_tool_name(plan['server_name'], name) for name in plan['allowed_tools']],
            disallowed_tools=['EndConversation'],
            permission_mode='dontAsk', setting_sources=[], max_turns=plan.get('max_agent_turns', 8),
            hooks={'PreToolUse': [HookMatcher(hooks=[budget.guard])]},
            system_prompt='Perform only the local filesystem task requested through the explicitly allowed MCP tools. Do not delegate or use any other tools. Read requested source files fresh; do not replace tool results with remembered content.',
            env={'MCP_TIMEOUT': str(plan.get('startup_timeout_seconds', 120) * 1000)},
            stderr=recorder.stderr,
            extra_args={'restricted': None, 'disable-slash-commands': None,
                        'no-session-persistence': None, 'permission-prompts': 'none'},
        )
        with hide_sdk_windows(subprocess_cli, spawned):
            recorder.note('client_connecting')
            async with ClaudeSDKClient(options=options) as client:
                await run_session(client, plan, recorder, workload, budget, turns)
    except Exception as exception:
        error = type(exception).__name__
        # Error text is private: it may contain account or local path details.
        recorder.stderr(repr(exception))
        recorder.note('failed', error_type=error)
    finally:
        for process in spawned:
            if process.returncode is None:
                try:
                    process.terminate()  # Only handles opened by this SDK adapter.
                    await asyncio.wait_for(process.wait(), 10)
                except ProcessLookupError:
                    pass
                except asyncio.TimeoutError:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 5)
        recorder.note('client_disconnected')
        # Let the proxy's owned process cleanup update status; no new MCP call.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if workload.profile_sample(workload.status_snapshot(plan['warden_state']), plan['profile']) is None:
                break
            await asyncio.sleep(.1)
        recorder.sample('after_disconnect')
        recorder.close()
    summary = {
        'client': 'Claude Code via official Python Agent SDK', 'sdk_version': SDK_VERSION,
        'native_preflight': native,
        'model_requested': plan.get('model', 'sonnet'), 'turns': turns, 'error_type': error,
        'elapsed_seconds': recorder.elapsed(), 'planned_duration_seconds': plan['duration_seconds'],
        'driver_sha256': driver_before,
        'source_hashes': sources_before,
        'plan_sha256': plan['_plan_sha256'], 'raw_output_sha256': sha256((output/'claude-output.jsonl').read_bytes()),
        'phase_observations_sha256': sha256((output/'phase-observations.jsonl').read_bytes()),
        'stderr_bytes': (output/'claude-stderr.log').stat().st_size,
        'scope': 'existing saved subscription login; isolated filesystem MCP; no quota or savings measurement',
    }
    session_hashes = [turn['session_id_sha256'] for turn in turns]
    summary['checks'] = {
        'all_planned_turns_completed': len(turns) == len(plan['turns']),
        'all_turn_checks_passed': bool(turns) and all(all(turn['checks'].values()) and all(check['passed'] for check in turn['file_verification']) for turn in turns),
        'same_native_session': bool(session_hashes) and None not in session_hashes and len(set(session_hashes)) == 1,
        'minimum_idle_observed': len(turns) > 1 and all(turn['idle_gap_seconds'] >= plan.get('minimum_idle_seconds', 900) for turn in turns[1:]),
        'planned_duration_observed': bool(turns) and recorder.elapsed() - turns[0]['began_seconds'] >= plan['duration_seconds'],
        'no_driver_error': error is None,
        'sources_unchanged_during_run': workload.source_hashes(plan) == sources_before
            and sha256(Path(__file__).read_bytes()) == driver_before,
    }
    summary['checks'].update(lifecycle_checks(recorder.samples, turns, plan['profile'], workload))
    summary['passed'] = all(summary['checks'].values())
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--claude', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    workload = load_local('claude_native_workload', 'native_workload.py')
    plan = workload.load_plan(args.plan)
    if not args.execute:
        print(json.dumps({'mode': 'plan-only', 'turns': len(plan['turns']),
                          'duration_seconds': plan['duration_seconds'],
                          'plan_sha256': plan['_plan_sha256'], 'model_started': False}))
        return 0
    if args.claude is None or args.out is None:
        parser.error('--execute requires --claude and --out')
    executable = args.claude.resolve(strict=True)
    output = args.out.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('--out must be outside the repository')
    output.mkdir(parents=True, exist_ok=False)
    summary = asyncio.run(execute(plan, executable, output, workload))
    print(json.dumps({'passed': summary['passed'], 'turns': len(summary['turns']),
                      'elapsed_seconds': summary['elapsed_seconds'], 'checks': summary['checks']}))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
