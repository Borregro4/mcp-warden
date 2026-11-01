"""Tasks must never be mistaken for completed work at the RPC boundary."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('task_idle_bridge', ROOT / 'runtime' / 'idle_stdio.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def rpc(ident, method, params):
    return module.encoded({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params}) + b'\n'


class TaskIdlePolicyTests(unittest.TestCase):
    def bridge(self):
        bridge = module.IdleBridge({'command': sys.executable, 'idle_session_timeout_seconds': .1})
        bridge.init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}
        bridge.proc = object()
        bridge.state = 'active'
        bridge.ready = True
        bridge.started_at = bridge.last_activity = time.monotonic() - 1
        return bridge

    def assert_retained(self, bridge, reason):
        self.assertEqual(bridge.idle_expiry_blocked_reason, reason)
        bridge.last_activity = time.monotonic() - 1
        with patch.object(bridge, 'stop_backend') as stop:
            bridge.poll_idle()
        stop.assert_not_called()

    def test_empty_client_task_capability_blocks_idle(self):
        bridge = self.bridge()
        bridge.init = None
        with patch.object(bridge, 'start'), patch.object(bridge, 'forward'):
            bridge.accept_client(rpc(1, 'initialize', {'capabilities': {'tasks': {}}}))
        bridge.client_pending.clear()
        self.assert_retained(bridge, 'mcp_tasks_capability')

    def test_empty_server_task_capability_blocks_idle(self):
        bridge = self.bridge()
        response = {'jsonrpc': '2.0', 'id': 1, 'result': {'protocolVersion': '2025-11-25',
            'capabilities': {'tasks': {}}, 'serverInfo': {'name': 'fixture', 'version': '1'}}}
        with patch.object(bridge, 'output'):
            bridge.accept_backend(module.encoded(response) + b'\n')
        self.assert_retained(bridge, 'mcp_tasks_capability')

    def test_task_augmented_client_request_blocks_even_without_negotiation(self):
        bridge = self.bridge()
        with patch.object(bridge, 'forward'):
            bridge.accept_client(rpc(3, 'tools/call', {'name': 'task', 'task': {}}))
        bridge.client_pending.clear()
        self.assert_retained(bridge, 'task_augmented_request')

    def test_task_augmented_server_callback_blocks_even_without_negotiation(self):
        bridge = self.bridge()
        with patch.object(bridge, 'output'):
            bridge.accept_backend(rpc(7, 'sampling/createMessage', {'task': {}}))
        bridge.server_pending.clear()
        self.assert_retained(bridge, 'task_augmented_request')

    def test_later_ordinary_messages_do_not_clear_task_protection(self):
        bridge = self.bridge()
        bridge.observe_task_support({'id': 3, 'method': 'tools/call', 'params': {'task': {}}})
        bridge.observe_task_support({'id': 4, 'method': 'tools/call', 'params': {'name': 'echo'}})
        bridge.observe_task_support({'id': 4, 'result': {}}, initialize_result=True)
        self.assert_retained(bridge, 'task_augmented_request')

    def test_ordinary_rpc_keeps_existing_idle_policy(self):
        bridge = self.bridge()
        bridge.observe_task_support({'id': 3, 'method': 'tools/call', 'params': {'arguments': {'task': {}}}})
        self.assertIsNone(bridge.idle_expiry_blocked_reason)
        with patch.object(bridge, 'stop_backend') as stop:
            bridge.poll_idle()
        stop.assert_called_once_with()
        self.assertEqual(bridge.state, 'sleeping')


# This fixture deliberately emits no task status notifications: the protocol
# makes them optional, and a peer can legitimately recommend polling after idle.
BACKEND = r'''
import json, os, pathlib, sys, threading, time
ledger, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
lock=threading.Lock()
def record(kind):
    with lock, ledger.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'kind':kind,'pid':os.getpid()})+'\n')
def send(ident,result):
    print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
record('started')
for raw in sys.stdin:
    msg=json.loads(raw); ident=msg.get('id'); method=msg.get('method')
    if method=='initialize':
        capabilities={'tools':{}}
        if mode=='server':capabilities['tasks']={'requests':{'tools':{'call':{}}}}
        send(ident,{'protocolVersion':'2025-11-25','capabilities':capabilities,'serverInfo':{'name':'task-fixture','version':'1'}})
    elif method=='tools/list':
        send(ident,{'tools':[{'name':'slow','inputSchema':{'type':'object'},'execution':{'taskSupport':'optional'}}]})
    elif method=='tools/call' and 'task' in msg.get('params',{}):
        record('task_started')
        def work():
            time.sleep(.5)
            record('task_completed')
        threading.Thread(target=work,daemon=True).start()
        send(ident,{'task':{'taskId':'fixture-task','status':'working','createdAt':'2026-11-01T00:00:00Z','lastUpdatedAt':'2026-11-01T00:00:00Z','ttl':60000,'pollInterval':2000}})
    elif method=='tools/call':
        send(ident,{'content':[{'type':'text','text':'ok'}],'structuredContent':{'pid':os.getpid()}})
'''


@unittest.skipUnless(os.name == 'nt', 'real Windows stdio ownership')
class TaskIdleEndToEndTests(unittest.TestCase):
    def fixture(self, mode):
        directory = tempfile.TemporaryDirectory(prefix='warden-task-idle-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        ledger = root / 'ledger.jsonl'
        manifest = root / 'manifest.json'
        manifest.write_text(json.dumps({'command': sys.executable,
            'args': ['-I', '-B', '-u', '-c', BACKEND, str(ledger), mode], 'cwd': str(root),
            'restart_policy': 'stateless', 'idle_session_timeout_seconds': .1,
            'shutdown_grace_seconds': .02}), encoding='utf-8')
        process = subprocess.Popen([sys.executable, '-I', '-B', '-u', str(ROOT / 'runtime' / 'idle_stdio.py'),
            '--manifest', str(manifest)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        messages = queue.Queue()
        def read():
            for line in process.stdout:
                messages.put(json.loads(line))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        def close():
            process.stdin.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
                self.fail('proxy did not exit on client EOF')
            reader.join(timeout=1)
            errors = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
            self.assertEqual(errors, b'')
            self.assertEqual(process.returncode, 0)
        self.addCleanup(close)
        def request(ident, method, params):
            process.stdin.write(rpc(ident, method, params))
            process.stdin.flush()
            response = messages.get(timeout=5)
            self.assertEqual(response['id'], ident)
            self.assertNotIn('error', response)
            return response['result']
        caps = {'tasks': {}} if mode == 'client' else {}
        request(1, 'initialize', {'protocolVersion': '2025-11-25', 'capabilities': caps,
            'clientInfo': {'name': 'task-test', 'version': '1'}})
        process.stdin.write(module.encoded({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + b'\n')
        process.stdin.flush()
        request(2, 'tools/list', {})
        def rows():
            return [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
        return request, rows

    def test_async_work_completes_after_idle_deadline(self):
        for mode in ('client', 'server', 'unnegotiated'):
            with self.subTest(mode=mode):
                request, rows = self.fixture(mode)
                accepted = request(3, 'tools/call', {'name': 'slow', 'arguments': {}, 'task': {'ttl': 60000}})
                self.assertEqual(accepted['task']['status'], 'working')
                deadline = time.monotonic() + 2
                while not any(row['kind'] == 'task_completed' for row in rows()) and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(any(row['kind'] == 'task_completed' for row in rows()))
                self.assertEqual(len([row for row in rows() if row['kind'] == 'started']), 1)

    def test_no_task_session_still_sleeps_and_wakes_without_replay(self):
        request, rows = self.fixture('ordinary')
        first = request(3, 'tools/call', {'name': 'slow', 'arguments': {}})
        time.sleep(.4)
        second = request(4, 'tools/call', {'name': 'slow', 'arguments': {}})
        self.assertNotEqual(first['structuredContent']['pid'], second['structuredContent']['pid'])
        self.assertEqual(len([row for row in rows() if row['kind'] == 'started']), 2)


if __name__ == '__main__':
    unittest.main()
