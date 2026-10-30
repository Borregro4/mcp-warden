"""Deterministic native-driver checks. No model, auth, or global configuration."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import native_workload as common
import native_codex_persistent as codex


class WorkloadChecks(unittest.TestCase):
    def setUp(self):
        scratch = ROOT.parents[1] / 'work'
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='native-driver-test-', dir=scratch)
        self.directory = Path(self.temporary.name)
        self.workspace = self.directory / 'workspace'
        self.state = self.directory / 'state'
        self.workspace.mkdir()
        self.state.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def plan(self):
        return {'version': 1, 'workspace': str(self.workspace), 'warden_state': str(self.state),
            'server_name': 'warden-work', 'allowed_tools': ['read_text_file', 'read_multiple_files', 'write_file'],
            'turn_offsets_seconds': [0, 5], 'duration_seconds': 5, 'minimum_idle_seconds': 5,
            'turns': [{'id': str(i), 'prompt': 'Read seed and write result.',
                'required_tool_groups': [['read_text_file', 'read_multiple_files'], ['write_file']],
                'verify': [{'path': 'result.json', 'json_equals': {'ok': True}}]} for i in range(2)]}

    def test_plan_seals_groups_and_limits_four_hour_claim(self):
        path = self.directory / 'plan.json'
        common.write_json(path, self.plan())
        plan = common.load_plan(path)
        self.assertEqual(plan['turns'][0]['required_tools'], [])
        self.assertEqual(plan['_plan_sha256'], common.sha256_file(path))
        changed = self.plan()
        changed['duration_seconds'] = 14400
        common.write_json(path, changed)
        with self.assertRaisesRegex(ValueError, 'four-hour'):
            common.load_plan(path)
        changed = self.plan()
        changed['max_mcp_calls_per_turn'] = 17
        common.write_json(path, changed)
        with self.assertRaisesRegex(ValueError, '1 through 16'):
            common.load_plan(path)

    def test_file_checks_require_real_bytes_and_confine_paths(self):
        file = self.workspace / 'result.json'
        file.write_bytes(b'{"ok":true}\n')
        checks = [{'path': file.name, 'json_equals': {'ok': True}},
                  {'path': file.name, 'text_equals': '{"ok":true}\n'},
                  {'path': file.name, 'sha256': common.sha256_file(file)},
                  {'path': file.name, 'text_equals': '{"ok":true}'},
                  {'path': 'missing', 'contains': 'PASS'}]
        self.assertEqual([c['passed'] for c in common.verify_files(self.workspace, checks)],
                         [True, True, True, False, False])
        for path in ('../outside', str(self.directory / 'outside')):
            with self.assertRaises(ValueError):
                common.workspace_file(self.workspace, path)

    def test_required_groups_are_alternatives_and_mcp_errors_fail(self):
        calls = [{'server': 'warden-work', 'tool': x, 'status': 'completed', 'result': {}}
                 for x in ('read_multiple_files', 'write_file')]
        checks = common.check_tools(calls, 'warden-work', self.plan()['allowed_tools'],
            required_groups=[['read_text_file', 'read_multiple_files'], ['write_file']])
        self.assertTrue(all(checks.values()))
        calls[1]['result'] = {'isError': True}
        self.assertFalse(common.check_tools(calls, 'warden-work', self.plan()['allowed_tools'])['successful_mcp_calls'])
        self.assertFalse(common.check_tools(calls[:1], 'warden-work', self.plan()['allowed_tools'],
            required_groups=[['write_file']])['required_tool_groups_observed'])

    def test_isolation_never_copies_global_backend_values(self):
        original = {'mcp_servers': {'private': {'env': {'SECRET': 'never-copy-canary'}, 'command': 'private.exe'}}}
        config = codex.isolated_config(original, {'command': 'python', 'args': ['owned.py']},
                                       'warden-work', ['read_text_file'])
        self.assertNotIn('never-copy-canary', json.dumps(config))
        self.assertEqual(config['mcp_servers']['private'], {'enabled': False})
        self.assertFalse(config['features.hooks'])
        self.assertEqual(config['mcp_servers']['warden-work']['enabled_tools'], ['read_text_file'])
        with self.assertRaises(ValueError):
            codex.isolated_config(original, {}, 'private', [])

    def test_only_authorized_fixture_write_tools_are_preapproved(self):
        tools = ['read_text_file', 'write_file', 'edit_file']
        config = codex.isolated_config({}, {'command': 'owned'}, 'warden-work', tools)
        selected = config['mcp_servers']['warden-work']
        self.assertEqual(selected['default_tools_approval_mode'], 'auto')
        self.assertEqual(selected['tools'], {'write_file': {'approval_mode': 'approve'},
                                            'edit_file': {'approval_mode': 'approve'}})
        other = codex.isolated_config({}, {'command': 'owned'}, 'other', tools)
        self.assertNotIn('tools', other['mcp_servers']['other'])

    def test_mcp_observer_captures_backend_before_final_reply_idle_expiry(self):
        live = {'samples': [{'profile': 'filesystem', 'ownerPid': 1, 'ownerCreated': '10',
                            'backendPid': 2, 'backendCreated': '20'}]}
        event = {'method': 'item/completed', 'params': {'threadId': 'thread', 'item': {
            'id': 'mcp1', 'type': 'mcpToolCall', 'tool': 'write_file', 'status': 'completed', 'result': {}}}}
        phase = {}
        with patch.object(codex, 'observe_backend_now', return_value=live):
            codex.observe_mcp_result(event, {}, 'thread', phase)
        with patch.object(codex, 'observe_backend_now', return_value=None):
            self.assertEqual(codex.phase_backend_health({}, phase), live)
        event['params']['item']['status'] = 'failed'
        failed = {}
        with patch.object(codex, 'observe_backend_now') as observer:
            codex.observe_mcp_result(event, {}, 'thread', failed)
        observer.assert_not_called()
        self.assertEqual(failed, {})

    def test_unknown_child_is_never_substituted_for_published_backend(self):
        health = {'samples': [{'profile': 'filesystem', 'ownerPid': 1, 'ownerCreated': '10', 'backendPid': None}]}
        with patch.object(codex, 'status_snapshot', return_value=health):
            observed = codex.observe_backend_now({'warden_state': 'synthetic', 'profile': 'filesystem'})
        self.assertIsNone(observed)

    def test_phase_sampler_preserves_published_identity_when_backend_expires(self):
        live = {'samples': [{'profile': 'filesystem', 'ownerPid': 1, 'ownerCreated': '10',
                            'backendPid': 2, 'backendCreated': '20'}]}
        phase = {}
        with patch.object(codex, 'observe_backend_now', return_value=live):
            with codex.PhaseSampler({}, phase):
                deadline = time.monotonic() + 1
                while 'sampled_active_observation' not in phase and time.monotonic() < deadline:
                    time.sleep(.001)
        with patch.object(codex, 'observe_backend_now', return_value=None):
            self.assertEqual(codex.phase_backend_health({}, phase), live)

    def test_gap_measures_idle_after_previous_turn_completion(self):
        class FakeProcess:
            pid = 99
            def poll(self):
                return None
        class FakeRpc:
            process = FakeProcess()
            def drain(self):
                pass
        health = {'sessionCount': 1, 'samples': [{'profile': 'filesystem', 'ownerPid': 1,
                  'ownerCreated': '2', 'backendPid': None}]}
        last = time.monotonic()
        plan = {**self.plan(), 'minimum_idle_seconds': .03, 'profile': 'filesystem'}
        with patch.object(codex, 'status_snapshot', return_value=health), patch.object(codex, 'process_metrics', return_value={}):
            result = codex.wait_gap(FakeRpc(), plan, self.directory, last - 10, last, (1, '2'))
        self.assertGreaterEqual(result['idle_elapsed_seconds'], .03)
        self.assertEqual(result['expired_status'], health)

    def test_rpc_handles_interleaved_events_and_redacts_effective_config(self):
        fixture = r'''
import json,sys
for line in sys.stdin:
    message=json.loads(line)
    if message.get('method') == 'config/read':
        print(json.dumps({'method':'thread/status/changed','params':{'threadId':'test'}}),flush=True)
        print(json.dumps({'id':message['id'],'result':{'config':{'secret':'do-not-log-canary'}}}),flush=True)
    elif message.get('method') == 'turn/start':
        print(json.dumps({'method':'turn/completed','params':{'threadId':'test','turn':{'id':'old','status':'failed'}}}),flush=True)
        print(json.dumps({'id':message['id'],'result':{'turn':{'id':'new'}}}),flush=True)
        print(json.dumps({'method':'turn/completed','params':{'threadId':'test','turn':{'id':'new','status':'completed'}}}),flush=True)
'''
        rpc = codex.JsonlRpc([sys.executable, '-I', '-B', '-u', '-c', fixture], self.directory, self.workspace)
        try:
            result = rpc.call('config/read', {}, sensitive=True)
            self.assertEqual(result['config']['secret'], 'do-not-log-canary')
            begin = len(rpc.events)
            rpc.call('turn/start', {})
            final = rpc.wait_turn('test', 'new', 2, begin)
            self.assertEqual(final['status'], 'completed')
        finally:
            shutdown = rpc.close()
        self.assertEqual(shutdown, {'returncode': 0, 'forced_termination': False})
        raw = (self.directory / 'app-server-events.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('do-not-log-canary', raw)
        self.assertIn('payload excluded', raw)

    def test_inventory_rejects_unrelated_live_servers(self):
        class FakeRpc:
            def call(self, *args, **kwargs):
                return {'data': [{'name': 'warden-work', 'runtimeStatus': 'connected', 'tools': {}},
                                 {'name': 'unrelated', 'runtimeStatus': 'connected', 'tools': {}}]}
        with self.assertRaisesRegex(RuntimeError, 'exclusively'):
            codex.inventory(FakeRpc(), 'thread', 'warden-work')

    def test_backend_identity_exit_and_pid_reuse_are_distinct_from_unknown(self):
        class Reader:
            value = None
            def query(self, pid):
                return self.value
        reader = Reader()
        with patch.object(common, 'process_reader', return_value=reader):
            self.assertFalse(common.process_metrics(100, '10')['alive'])
            reader.value = {'pid': 100, 'created': '20', 'workingSetBytes': 1, 'cpuSeconds': 0}
            self.assertFalse(common.process_metrics(100, '10')['alive'])
            self.assertTrue(common.process_metrics(100, '20')['alive'])
            with patch.object(reader, 'query', side_effect=OSError('access unavailable')):
                self.assertIsNone(common.process_metrics(100, '20')['alive'])

    def test_tool_budget_is_enforced_even_when_turn_completed(self):
        rpc = object.__new__(codex.JsonlRpc)
        rpc.events = [{'method': 'item/started', 'params': {'threadId': 'thread', 'turnId': 'turn',
                      'item': {'type': 'mcpToolCall'}}} for _ in range(3)]
        rpc.events.append({'method': 'turn/completed', 'params': {'threadId': 'thread',
                           'turn': {'id': 'turn', 'status': 'completed'}}})
        rpc.call = unittest.mock.Mock(return_value={})
        with self.assertRaisesRegex(RuntimeError, 'budget exceeded'):
            rpc.wait_turn('thread', 'turn', 1, 0, max_mcp_calls=2)
        rpc.call.assert_called_once_with('turn/interrupt', {'threadId': 'thread', 'turnId': 'turn'}, timeout=10)

    def test_direct_probe_requires_real_file_and_never_overwrites(self):
        (self.workspace / 'data').mkdir()
        (self.workspace / 'results').mkdir()
        (self.workspace / 'data' / 'orders.json').write_bytes(b'{"batch_id":"synthetic"}')
        class FakeRpc:
            def call(self, method, params):
                arguments = params['arguments']
                path = Path(arguments['path'])
                if params['tool'] == 'write_file':
                    path.write_bytes(arguments['content'].encode('utf-8'))
                return {'content': [{'type': 'text', 'text': path.read_text(encoding='utf-8')}], 'isError': False}
        plan = self.plan()
        self.assertTrue(codex.transport_probe(FakeRpc(), plan, 'thread')['passed'])
        with self.assertRaisesRegex(RuntimeError, 'fresh'):
            codex.transport_probe(FakeRpc(), plan, 'thread')


if __name__ == '__main__':
    unittest.main()
