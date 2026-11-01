"""A non-reading client must not prevent EOF, deadlines, or owned cleanup."""
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

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('idle_stdout_bridge', ROOT / 'runtime' / 'idle_stdio.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CompleteFrameBackpressureTests(unittest.TestCase):
    def test_queued_complete_frame_does_not_start_a_partial_timeout(self):
        bridge = module.IdleBridge({'command': sys.executable, 'partial_message_timeout_seconds': .1})
        destination_queue = queue.Queue(maxsize=1)
        destination_queue.put(('already-full', 0, b''))
        blocked = threading.Event()
        class Destination(module.TaggedQueue):
            def put(self, event):
                blocked.set()
                return super().put(event)
        class Stream:
            def read(self, count):
                return b'{"jsonrpc":"2.0","method":"notifications/message","params":{}}\n'
        destination = Destination(destination_queue, bridge.generation, bridge.stopping)
        reader = threading.Thread(target=bridge.read_stream, args=(Stream(), destination, 'backend'), daemon=True)
        reader.start()
        try:
            self.assertTrue(blocked.wait(timeout=1))
            time.sleep(.15)
            self.assertEqual(bridge.partial, {})
            bridge.poll_idle()  # Complete-frame backpressure is not malformed input.
        finally:
            bridge.stopping.set()
            reader.join(timeout=1)
        self.assertFalse(reader.is_alive())


BACKEND = r'''
import json,os,pathlib,sys
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()),encoding='utf-8')
for raw in sys.stdin:
    msg=json.loads(raw);m=msg.get('method');i=msg.get('id')
    if m=='initialize':
        result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'blocked-output','version':'1'}}
    elif m=='tools/list':
        result={'tools':[{'name':'large','inputSchema':{'type':'object'}}]}
    elif m=='tools/call':
        if msg.get('params',{}).get('arguments',{}).get('flood'):
            for _ in range(40):
                print(json.dumps({'jsonrpc':'2.0','method':'notifications/message','params':{'level':'info','data':'x'*(128*1024)}}),flush=True)
        result={'content':[{'type':'text','text':'x'*(256*1024)}]}
    else:
        continue
    print(json.dumps({'jsonrpc':'2.0','id':i,'result':result}),flush=True)
'''


@unittest.skipUnless(os.name == 'nt', 'real Windows pipes and ownership')
class StdoutDeadlineEndToEndTests(unittest.TestCase):
    def fixture(self, **changes):
        directory = tempfile.TemporaryDirectory(prefix='warden-stdout-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        ledger = root / 'backend-pid.txt'
        manifest = root / 'manifest.json'
        manifest.write_text(json.dumps({'command': sys.executable,
            'args': ['-I', '-B', '-u', '-c', BACKEND, str(ledger)], 'cwd': str(root),
            'idle_session_timeout_seconds': .1, 'shutdown_grace_seconds': .02,
            'backend_write_timeout_seconds': 5, **changes}), encoding='utf-8')
        process = subprocess.Popen([sys.executable, '-I', '-B', '-u', str(ROOT / 'runtime' / 'idle_stdio.py'),
            '--manifest', str(manifest)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        def close():
            if not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()
        self.addCleanup(close)
        def request(ident, method, params=None):
            value = {'jsonrpc': '2.0', 'id': ident, 'method': method}
            if params is not None:
                value['params'] = params
            process.stdin.write((json.dumps(value) + '\n').encode())
            process.stdin.flush()
        def receive():
            messages = queue.Queue()
            reader = threading.Thread(target=lambda: messages.put(json.loads(process.stdout.readline())), daemon=True)
            reader.start()
            response = messages.get(timeout=3)
            reader.join(timeout=1)
            self.assertNotIn('error', response)
            return response
        request(1, 'initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
            'clientInfo': {'name': 'slow-reader-test', 'version': '1'}})
        receive()
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        request(2, 'tools/list')
        receive()
        metric_spec = importlib.util.spec_from_file_location('stdout_fixture_metrics', ROOT / 'runtime' / 'owned_bridge.py')
        metrics = importlib.util.module_from_spec(metric_spec)
        metric_spec.loader.exec_module(metrics)
        backend = metrics.PinnedProcess(int(ledger.read_text(encoding='utf-8')))
        self.addCleanup(backend.close)
        return process, backend, request

    def assert_closed(self, process, backend, code, timeout=2):
        process.wait(timeout=timeout)
        self.assertEqual(process.returncode, code)
        self.assertFalse(backend.alive(), 'owned backend must be gone after proxy exits')
        errors = process.stderr.read()
        self.assertNotIn(b'Fatal Python', errors)
        self.assertNotIn(b'xxxx', errors)
        return errors

    def test_client_eof_interrupts_full_stdout_queue_and_cleans_backend(self):
        process, backend, request = self.fixture()
        request(3, 'tools/call', {'name': 'large', 'arguments': {'flood': True}})
        time.sleep(.2)  # Leave stdout open, deliberately do not consume it.
        started = time.monotonic()
        process.stdin.close()
        errors = self.assert_closed(process, backend, 0)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(errors, b'')

    def test_stalled_stdout_write_has_a_deadline_with_client_still_connected(self):
        process, backend, request = self.fixture(backend_write_timeout_seconds=.2)
        request(3, 'tools/call', {'name': 'large', 'arguments': {}})
        started = time.monotonic()
        errors = self.assert_closed(process, backend, 2)
        self.assertLess(time.monotonic() - started, 1)
        self.assertIn(b'stream_write_timeout', errors)

    def test_partial_input_deadline_survives_full_output_backpressure(self):
        process, backend, request = self.fixture(partial_message_timeout_seconds=.15)
        request(3, 'tools/call', {'name': 'large', 'arguments': {'flood': True}})
        process.stdin.write(b'{')
        process.stdin.flush()
        started = time.monotonic()
        errors = self.assert_closed(process, backend, 2)
        self.assertLess(time.monotonic() - started, 1)
        self.assertIn(b'partial_message_timeout', errors)


if __name__ == '__main__':
    unittest.main()
