"""Offline evidence and bounded-driver tests. Never imports or starts Claude."""
import asyncio
from dataclasses import make_dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('claude_persistent_test_driver',
    Path(__file__).resolve().parents[1] / 'scripts' / 'native_claude_persistent.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)

ToolUseBlock = make_dataclass('ToolUseBlock', ['id', 'name'])
ToolResultBlock = make_dataclass('ToolResultBlock', ['tool_use_id', ('is_error', bool, False)])
AssistantMessage = make_dataclass('AssistantMessage', ['content', ('model', str, 'test-model')])
UserMessage = make_dataclass('UserMessage', ['content'])
ResultMessage = make_dataclass('ResultMessage', ['subtype', 'is_error', ('session_id', str, 'fixed-session')])
READ, WRITE = 'mcp__isolated__read_text_file', 'mcp__isolated__write_file'


class EvidenceTests(unittest.TestCase):
    def test_model_pass_text_without_observed_tools_fails(self):
        capture = driver.TurnCapture()
        capture.observe(ResultMessage('success', False), 1)
        checks = capture.summary([[READ]], {READ})['checks']
        self.assertFalse(checks['only_allowed_tools'])
        self.assertFalse(checks['every_call_has_one_reply'])
        self.assertFalse(checks['required_tool_groups_observed'])

    def test_or_group_and_correlated_successful_replies(self):
        capture = driver.TurnCapture()
        capture.observe(AssistantMessage([ToolUseBlock('a', READ), ToolUseBlock('b', WRITE)]), 1)
        capture.observe(UserMessage([ToolResultBlock('b'), ToolResultBlock('a')]), 2)
        capture.observe(ResultMessage('success', False), 3)
        result = capture.summary([[READ, 'mcp__isolated__read_multiple_files'], [WRITE]], {READ, WRITE})
        self.assertTrue(all(result['checks'].values()))
        self.assertEqual(result['session_id_sha256'], hashlib.sha256(b'fixed-session').hexdigest())

    def test_missing_duplicate_error_foreign_and_excess_calls_fail(self):
        for replies, allowed, limit, failed_check in [
            ([ToolResultBlock('a')], {READ, WRITE}, 16, 'every_call_has_one_reply'),
            ([ToolResultBlock('a'), ToolResultBlock('a')], {READ, WRITE}, 16, 'every_call_has_one_reply'),
            ([ToolResultBlock('a'), ToolResultBlock('b', True)], {READ, WRITE}, 16, 'no_tool_errors'),
            ([ToolResultBlock('a'), ToolResultBlock('b')], {READ}, 16, 'only_allowed_tools'),
            ([ToolResultBlock('a'), ToolResultBlock('b')], {READ, WRITE}, 1, 'tool_call_limit_respected'),
        ]:
            capture = driver.TurnCapture()
            capture.observe(AssistantMessage([ToolUseBlock('a', READ), ToolUseBlock('b', WRITE)]), 1)
            capture.observe(UserMessage(replies), 2)
            capture.observe(ResultMessage('success', False), 3)
            self.assertFalse(capture.summary([[READ], [WRITE]], allowed, limit)['checks'][failed_check])

    def test_saved_login_guard_rejects_api_environment_without_exposing_values(self):
        with patch.dict(driver.os.environ, {'ANTHROPIC_API_KEY': 'synthetic-secret'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'saved subscription login') as caught:
                driver.validate_saved_login_environment()
            self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_lifecycle_uses_creation_identity_and_requires_confirmed_idle_exit(self):
        def profile(status, name):
            samples = status.get('samples', [])
            return samples[0] if len(samples) == 1 else None
        workload = SimpleNamespace(profile_sample=profile,
            proxy_identity=lambda row: (row['ownerPid'], row['ownerCreated']),
            backend_identity=lambda row: (row['backendPid'], row['backendCreated']) if row.get('backendCreated') else None)
        def sample(at, backend, birth, known, proxies=()):
            row = {'ownerPid': 1, 'ownerCreated': '10', 'backendPid': backend, 'backendCreated': birth}
            return {'event': 'sample', 'phase': 'test', 'elapsed_seconds': at,
                    'warden': {'samples': [row]}, 'known_backends': known, 'known_proxies': proxies}
        old_dead = {'pid': 2, 'created': '20', 'alive': False}
        samples = [sample(.5, 2, '20', []), sample(5, None, None, [old_dead]), sample(6.5, 2, '21', [old_dead])]
        samples.append({'event': 'sample', 'phase': 'after_disconnect', 'elapsed_seconds': 8,
            'warden': {'samples': []}, 'known_backends': [old_dead, {'pid': 2, 'created': '21', 'alive': False}],
            'known_proxies': [{'pid': 1, 'created': '10', 'alive': False}]})
        turns = [{'began_seconds': 0, 'ended_seconds': 1}, {'began_seconds': 6, 'ended_seconds': 7}]
        self.assertTrue(all(driver.lifecycle_checks(samples, turns, 'filesystem', workload).values()))
        samples[1]['known_backends'][0] = {**old_dead, 'alive': None}
        self.assertFalse(driver.lifecycle_checks(samples, turns, 'filesystem', workload)['idle_backend_stop_between_turns'])


class FakeRecorder:
    def __init__(self):
        self.now = 0
        self.events = []

    def elapsed(self):
        return self.now

    def note(self, event, **data):
        self.events.append((event, data))

    def sample(self, phase):
        self.events.append(('sample', phase))

    async def wait_until(self, deadline):
        self.now = max(self.now, deadline)

    def message(self, message, turn):
        self.now += .25


class FakeClient:
    def __init__(self, failed=False):
        self.queries = []
        self.failed = failed

    async def get_mcp_status(self):
        return {'mcpServers': [{'name': 'isolated', 'status': 'connected',
                               'tools': [{'name': 'read_text_file'}, {'name': 'write_file'}]}]}

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        suffix = str(len(self.queries))
        yield AssistantMessage([ToolUseBlock('a'+suffix, READ), ToolUseBlock('b'+suffix, WRITE)])
        yield UserMessage([ToolResultBlock('a'+suffix), ToolResultBlock('b'+suffix)])
        yield ResultMessage('success', self.failed)


class AsyncDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_pre_tool_hooks_enforce_fixed_budget_and_reset(self):
        budget = driver.ToolBudget({READ}, 16)
        budget.begin('first')
        responses = await asyncio.gather(*(budget.guard({'tool_name': READ}, str(i), {}) for i in range(18)))
        self.assertEqual(sum(not response for response in responses), 16)
        self.assertEqual(budget.denied, 2)
        budget.begin('second')
        self.assertEqual(await budget.guard({'tool_name': READ}, 'new', {}), {})
        self.assertEqual(budget.denied, 0)
        self.assertTrue(await budget.guard({'tool_name': 'foreign'}, 'bad', {}))

    async def test_hide_adapter_restores_module_and_only_passes_spawn_flags(self):
        calls, spawned = [], []
        async def open_process(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(pid=1, returncode=0)
        original = SimpleNamespace(open_process=open_process, marker='preserved')
        module = SimpleNamespace(anyio=original)
        with patch.object(driver.os, 'name', 'nt'):
            with driver.hide_sdk_windows(module, spawned):
                self.assertEqual(module.anyio.marker, 'preserved')
                await module.anyio.open_process(['native.exe'], stdout=-1)
        self.assertIs(module.anyio, original)
        self.assertEqual(calls[0][1]['creationflags'], driver.subprocess.CREATE_NO_WINDOW)
        self.assertEqual(len(spawned), 1)

    def plan(self):
        return {'server_name': 'isolated', 'workspace': 'unused',
                'allowed_tools': ['read_text_file', 'write_file'],
                'minimum_idle_seconds': 900, 'duration_seconds': 14400,
                'turn_offsets_seconds': [0, 4800, 9600, 14400],
                'turns': [{'id': str(i), 'prompt': 'fixed task '+str(i),
                           'required_tool_groups': [['read_text_file'], ['write_file']],
                           'verify': [{'path': 'file', 'json_equals': {}}]} for i in range(4)]}

    async def test_four_turn_schedule_uses_one_client_and_actual_file_verification(self):
        client, recorder, verified = FakeClient(), FakeRecorder(), []
        def verify(workspace, checks):
            verified.append((workspace, checks))
            return [{'passed': True}]
        workload = SimpleNamespace(verify_files=verify, json_hash=lambda value: driver.sha256(json.dumps(value).encode()))
        turns = await driver.run_session(client, self.plan(), recorder, workload)
        self.assertEqual(len(client.queries), 4)
        self.assertEqual(len(verified), 4)
        self.assertEqual(len({turn['session_id_sha256'] for turn in turns}), 1)
        self.assertGreaterEqual(turns[-1]['began_seconds']-turns[0]['began_seconds'], 14400)
        self.assertTrue(all(turn['idle_gap_seconds'] >= 900 for turn in turns[1:]))

    async def test_failure_stops_without_retrying_or_later_turns(self):
        client, recorder = FakeClient(failed=True), FakeRecorder()
        workload = SimpleNamespace(verify_files=lambda *_: [{'passed': False}], json_hash=lambda _: 'catalog')
        turns = await driver.run_session(client, self.plan(), recorder, workload)
        self.assertEqual(len(turns), 1)
        self.assertEqual(len(client.queries), 1)

    async def test_short_smoke_waits_for_actual_idle_after_slow_turn(self):
        plan = self.plan()
        plan.update(minimum_idle_seconds=5, duration_seconds=5, turn_offsets_seconds=[0, 5], turns=plan['turns'][:2])
        client, recorder = FakeClient(), FakeRecorder()
        workload = SimpleNamespace(verify_files=lambda *_: [{'passed': True}], json_hash=lambda _: 'catalog')
        turns = await driver.run_session(client, plan, recorder, workload)
        self.assertGreaterEqual(turns[1]['idle_gap_seconds'], 5)


if __name__ == '__main__':
    unittest.main()
