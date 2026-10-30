"""Shared deterministic evidence helpers for opt-in native-client workloads.

This module never starts a model, changes client settings, or reads auth files.
Plans and raw results belong outside the repository.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
from functools import lru_cache
import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def outside_repository(path):
    path = Path(path).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError('private plan/output must be outside the repository')
    return path


def workspace_file(workspace, value):
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('verification path must be relative and remain in the workspace')
    root = Path(workspace).resolve()
    path = (root / relative).resolve()
    if path == root or root not in path.parents:
        raise ValueError('verification path escapes the workspace')
    return path


def load_plan(path):
    path = outside_repository(path)
    plan = json.loads(path.read_text(encoding='utf-8-sig'))
    if plan.get('version', 1) != 1:
        raise ValueError('unsupported native workload plan version')
    for name in ('workspace', 'warden_state', 'server_name'):
        if not isinstance(plan.get(name), str) or not plan[name]:
            raise ValueError('plan requires ' + name)
    for name in ('workspace', 'warden_state'):
        plan[name] = str(outside_repository(plan[name]))
        if not Path(plan[name]).is_dir():
            raise ValueError(name + ' must already exist')
    plan.setdefault('profile', 'filesystem')
    turns = plan.get('turns')
    if not isinstance(turns, list) or not 1 <= len(turns) <= 4:
        raise ValueError('plan must contain one to four turns; no automatic retries')
    offsets = plan.get('turn_offsets_seconds')
    if not isinstance(offsets, list) or len(offsets) != len(turns):
        raise ValueError('one scheduled offset is required for each turn')
    numeric = lambda x: type(x) in (int, float) and math.isfinite(x) and x >= 0
    if not all(numeric(x) for x in offsets) or offsets[0] != 0 or any(
            b <= a for a, b in zip(offsets, offsets[1:])):
        raise ValueError('turn offsets must start at zero and increase')
    duration = plan.setdefault('duration_seconds', offsets[-1])
    idle = plan.setdefault('minimum_idle_seconds', 900)
    timeout = plan.setdefault('turn_timeout_seconds', 240)
    if not numeric(duration) or duration < offsets[-1] or not numeric(idle) or not numeric(timeout) or timeout == 0:
        raise ValueError('invalid duration, idle interval or turn timeout')
    if duration >= 14400 and (len(turns) != 4 or idle < 900 or offsets[-1] < 14400):
        raise ValueError('a four-hour claim requires four turns and at least 900 seconds idle')
    cap = plan.setdefault('max_mcp_calls_per_turn', 16)
    if type(cap) is not int or not 1 <= cap <= 16:
        raise ValueError('max_mcp_calls_per_turn must be an integer from 1 through 16')
    if 'max_agent_turns' in plan and (type(plan['max_agent_turns']) is not int or not 1 <= plan['max_agent_turns'] <= 8):
        raise ValueError('max_agent_turns must be an integer from 1 through 8')
    allowed = plan.get('allowed_tools')
    if not isinstance(allowed, list) or not allowed or any(not isinstance(x, str) or not x for x in allowed):
        raise ValueError('allowed_tools must contain explicit MCP tool names')
    ids = set()
    for turn in turns:
        if not isinstance(turn.get('id'), str) or not turn['id'] or turn['id'] in ids:
            raise ValueError('turn IDs must be unique nonempty strings')
        ids.add(turn['id'])
        if not isinstance(turn.get('prompt'), str) or not turn['prompt']:
            raise ValueError('every turn requires a fixed prompt')
        required = turn.get('required_tools', [])
        groups = turn.get('required_tool_groups', [])
        if not isinstance(required, list) or any(x not in allowed for x in required):
            raise ValueError('required_tools must contain allowed tool names')
        if not isinstance(groups, list) or any(not isinstance(g, list) or not g
                or any(x not in allowed for x in g) for g in groups) or not (required or groups):
            raise ValueError('every turn requires allowed required_tools or required_tool_groups')
        turn['required_tools'] = required
        turn['required_tool_groups'] = groups
        checks = turn.get('verify')
        if not isinstance(checks, list) or not checks:
            raise ValueError('every turn requires deterministic file checks')
        for check in checks:
            workspace_file(plan['workspace'], check['path'])
            if sum(k in check for k in ('sha256', 'contains', 'json_equals', 'text_equals')) != 1:
                raise ValueError('each file check requires exactly one sha256, contains, json_equals or text_equals')
    plan['_plan_path'] = str(path)
    plan['_plan_sha256'] = sha256_file(path)
    return plan


def verify_files(workspace, checks):
    results = []
    for check in checks:
        path = workspace_file(workspace, check['path'])
        entry = {'path': check['path'], 'exists': path.is_file(), 'passed': False, 'sha256': None}
        if path.is_file():
            raw = path.read_bytes()
            entry['sha256'] = hashlib.sha256(raw).hexdigest()
            if 'sha256' in check:
                entry['passed'] = entry['sha256'] == check['sha256']
            elif 'contains' in check:
                entry['passed'] = check['contains'] in raw.decode('utf-8', errors='replace')
            elif 'text_equals' in check:
                entry['passed'] = raw.decode('utf-8', errors='replace') == check['text_equals']
            else:
                try:
                    entry['passed'] = json.loads(raw) == check['json_equals']
                except (ValueError, UnicodeDecodeError):
                    pass
        results.append(entry)
    return results


@lru_cache(maxsize=1)
def warden_module():
    spec = importlib.util.spec_from_file_location('native_workload_warden', ROOT / 'warden.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def connector_config(plan):
    """Derive one stdio connector without exposing its token or backend env."""
    import sys
    state = Path(plan['warden_state'])
    config = warden_module().load_config(state)
    selected = config['profiles'].get(plan['profile'], {})
    if selected.get('transport') != 'stdio' or selected.get('restart_policy') != 'stateless':
        raise ValueError('native idle/wake workloads require an explicit stateless stdio profile')
    idle = selected.get('idle_session_timeout_seconds', config.get('idle_session_timeout_seconds', 900))
    if type(idle) not in (int, float) or idle <= 0:
        raise ValueError('profile must have a positive idle timeout')
    if plan['minimum_idle_seconds'] < idle:
        raise ValueError('workload idle interval must reach the backend idle timeout')
    return {'command': sys.executable,
            'args': ['-I', '-B', '-u', str(ROOT / 'warden.py'), 'connect', '--state', str(state),
                     '--profile', plan['profile']]}


def source_hashes(plan):
    files = [ROOT / name for name in ('warden.py', 'runtime/idle_stdio.py', 'runtime/lazy_stdio.py',
             'runtime/owned_bridge.py', 'scripts/native_workload.py', 'scripts/soak_metrics.py')]
    state = Path(plan['warden_state'])
    config = warden_module().load_config(state)
    files.append(state / 'config.json')
    files.extend(Path(p).resolve() for p in config['profiles'][plan['profile']]['manifest'].get('fingerprint_files', []))
    files.append(Path(plan['_plan_path']))
    return {str(path): sha256_file(path) for path in files}


def status_snapshot(state):
    module = warden_module()
    status = module.get_stdio_status(Path(state))
    for sample in status.get('samples', []):
        owner = process_metrics(sample['ownerPid'], sample['ownerCreated'])
        sample['owner_metrics'] = owner
        if sample.get('backendPid') is not None:
            backend = process_metrics(sample['backendPid'])
            if backend.get('created') and int(backend['created']) >= int(sample['ownerCreated']):
                sample['backendCreated'] = backend['created']
                sample['backend_metrics'] = backend
    return status


@lru_cache(maxsize=1)
def process_reader():
    spec = importlib.util.spec_from_file_location('native_workload_sampler', ROOT / 'scripts' / 'soak_metrics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.WindowsReader()


def process_metrics(pid, created=None):
    """Query a read-only native process handle; never inspect command lines."""
    try:
        observed = process_reader().query(pid)
        if observed is None:
            return {'pid': pid, 'created': str(created) if created else None, 'alive': False}
        if created is not None and str(created) != observed['created']:
            return {'pid': pid, 'created': str(created), 'alive': False, 'pid_reused': True}
        return {**observed, 'alive': True}
    except (OSError, ValueError):
        return {'pid': pid, 'created': str(created) if created else None, 'alive': None}


def backend_identity(sample):
    if sample and type(sample.get('backendPid')) is int and sample.get('backendCreated'):
        return sample['backendPid'], str(sample['backendCreated'])
    return None


def profile_sample(status, profile):
    samples = [s for s in status.get('samples', []) if s.get('profile') == profile]
    return samples[0] if len(samples) == 1 else None


def proxy_identity(sample):
    return (sample.get('ownerPid'), str(sample.get('ownerCreated'))) if sample else None


def wait_profile(state, profile, *, backend_active, timeout=10, identity=None):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = status_snapshot(state)
        sample = profile_sample(last, profile)
        if sample and (identity is None or proxy_identity(sample) == identity):
            active = backend_identity(sample) is not None
            if (active if backend_active else sample.get('backendPid') is None):
                return last
        time.sleep(.25)
    raise RuntimeError('expected live proxy/backend phase was not observed')


def check_tools(calls, server, allowed, required=(), required_groups=()):
    names = [c.get('tool') for c in calls]
    counts = Counter(names)
    needed = Counter(required)
    return {'actual_mcp_calls': bool(calls),
            'successful_mcp_calls': bool(calls) and all(c.get('status') == 'completed'
                and not c.get('error') and not (c.get('result') or {}).get('isError') for c in calls),
            'only_allowed_mcp_tools': all(c.get('server') == server and c.get('tool') in allowed for c in calls),
            'required_tools_observed': all(counts[name] >= count for name, count in needed.items()),
            'required_tool_groups_observed': all(any(counts[name] for name in group) for group in required_groups)}
