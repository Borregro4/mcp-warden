"""Gateway bridge backpressure, ordered EOF and blocked-pipe cleanup."""
import importlib.util
import io
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
spec = importlib.util.spec_from_file_location('gateway_io_bridge', ROOT/'runtime'/'lazy_stdio.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class EventTests(unittest.TestCase):
    def test_client_eof_interrupts_full_data_queue(self):
        stopping = threading.Event()
        events = bridge.BridgeEvents(stopping)
        for _ in range(16):
            self.assertTrue(events.put(('backend', b'{}\n')))
        self.assertTrue(events.put(('client_eof', None)))
        self.assertTrue(events.client_closed.is_set())
        self.assertEqual(events.get(), ('client_eof', None))

    def test_full_reader_releases_on_stop_and_backend_eof_stays_ordered(self):
        stopping = threading.Event()
        events = bridge.BridgeEvents(stopping)
        for _ in range(16):
            events.put(('backend', b'{}\n'))
        ended = threading.Event()
        def blocked():
            events.put(('backend_eof', None))
            ended.set()
        thread = threading.Thread(target=blocked, daemon=True)
        thread.start()
        self.assertFalse(ended.wait(.05))
        self.assertEqual(events.get(), ('backend', b'{}\n'))
        self.assertTrue(ended.wait(.2))
        self.assertEqual([events.get() for _ in range(16)][-1], ('backend_eof', None))
        for _ in range(16):
            events.put(('backend', b'{}\n'))
        thread = threading.Thread(target=blocked, daemon=True)
        ended.clear()
        thread.start()
        stopping.set()
        self.assertTrue(ended.wait(.2))

    def test_oversized_frames_are_rejected_without_line_allocation(self):
        obj = bridge.Bridge({'command':sys.executable, 'cache_dir':str(ROOT), 'max_rpc_line_bytes':1024})
        for source in ('client', 'backend'):
            obj.events = bridge.BridgeEvents(obj.stopping)
            obj.read_stream(io.BytesIO(b'x'*1025), obj.events, source)
            self.assertEqual(obj.events.get(), (source+'_invalid', 'message_too_large'))
            self.assertEqual(obj.events.get(), (source+'_eof', None))

    def test_complete_frame_backpressure_is_not_a_partial_message_timeout(self):
        obj=bridge.Bridge({'command':sys.executable,'cache_dir':str(ROOT),
            'partial_message_timeout_seconds':.1,'backend_write_timeout_seconds':5})
        for _ in range(16):
            obj.events.put(('backend',b'{}\n'))
        reader=threading.Thread(target=obj.read_stream,
            args=(io.BytesIO(b'{}\n'),obj.events,'backend'),daemon=True)
        reader.start()
        try:
            time.sleep(.15)
            self.assertTrue(reader.is_alive(),'reader must be waiting for queue capacity')
            self.assertFalse(obj.partial,'complete queued frames are not partial')
            obj._poll_transport()
        finally:
            obj.stopping.set()
            reader.join(timeout=.3)
        self.assertFalse(reader.is_alive())

    def test_full_writer_checks_disconnect_instead_of_blocking_main(self):
        stopping, gate = threading.Event(), threading.Event()
        started = threading.Event()
        class Blocked:
            def write(self, value):
                started.set()
                gate.wait(2)
                return len(value)
            def flush(self): pass
        writer = bridge.BoundedWriter(Blocked(), stopping, lambda: None, 1, capacity=1)
        try:
            writer.send(b'one')
            self.assertTrue(started.wait(.3))
            writer.send(b'two')
            def closed():
                raise bridge.BridgeClientClosed('client_closed')
            before = time.monotonic()
            with self.assertRaises(bridge.BridgeClientClosed):
                writer.send(b'three', closed)
            self.assertLess(time.monotonic()-before, .2)
        finally:
            stopping.set()
            gate.set()


@unittest.skipUnless(os.name == 'nt', 'real Windows Job/pipe cleanup')
class NativeBridgeTests(unittest.TestCase):
    def start_fixture(self, mode):
        # Match the existing suite: fresh system-temp fixtures are retained
        # for diagnosis and never depend on a checkout-specific sibling path.
        folder = Path(tempfile.mkdtemp(prefix='warden-gateway-'+mode+'-'))
        backend = folder/'backend.py'
        backend.write_text('''import json,os,sys,time
from pathlib import Path
folder=Path(sys.argv[1]); mode=sys.argv[2]
(folder/'backend-pid').write_text(str(os.getpid()))
sys.stdin.buffer.readline()
if mode=='blocked-input':
 time.sleep(30)
else:
 count=64 if mode=='blocked-output' else 24
 size=131072 if mode=='blocked-output' else 4096
 for n in range(count):
  sys.stdout.buffer.write((json.dumps({'jsonrpc':'2.0','method':'notifications/message','params':{'n':n,'data':'x'*size}})+'\\n').encode())
  sys.stdout.buffer.flush()
 (folder/'burst-finished').write_text('done')
 if mode=='blocked-output': sys.stdin.buffer.read()
''', encoding='utf-8')
        manifest = folder/'manifest.json'
        manifest.write_text(json.dumps({'command':sys.executable, 'args':['-I','-B','-u',str(backend),str(folder),mode],
            'cache_dir':str(folder/'cache'), 'cache_ttl_seconds':0, 'stderr_log_dir':str(folder/'stderr'),
            'shutdown_grace_seconds':.1, 'backend_write_timeout_seconds':5,
            'partial_message_timeout_seconds':.1}), encoding='utf-8')
        error = (folder/'parent-stderr.log').open('wb')
        child = subprocess.Popen([sys.executable,'-I','-B','-u',str(ROOT/'runtime'/'lazy_stdio.py'),
            '--manifest',str(manifest)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(error.close)
        def cleanup():
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=4)
            for stream in (child.stdin,child.stdout):
                try: stream.close()
                except OSError: pass
        self.addCleanup(cleanup)
        child.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        child.stdin.flush()
        until=time.monotonic()+3
        while not (folder/'backend-pid').exists() and time.monotonic()<until:
            time.sleep(.01)
        self.assertTrue((folder/'backend-pid').exists(), 'backend must start')
        ownspec=importlib.util.spec_from_file_location('gateway_test_owned',ROOT/'runtime'/'owned_bridge.py')
        owned=importlib.util.module_from_spec(ownspec); ownspec.loader.exec_module(owned)
        pinned=None
        if mode!='ordered-eof':
            pinned=owned.PinnedProcess(int((folder/'backend-pid').read_text()))
            self.addCleanup(pinned.close)
        return child,folder,pinned

    def test_slow_output_backpressures_backend_and_eof_reaps_owned_child(self):
        child,folder,pinned=self.start_fixture('blocked-output')
        time.sleep(.5)
        self.assertFalse((folder/'burst-finished').exists(), '8MiB must not be buffered behind a paused reader')
        child.stdin.close()
        self.assertEqual(child.wait(timeout=3),0)
        self.assertFalse(pinned.alive(), 'Job-owned backend must be gone after client EOF')

    def test_blocked_backend_input_still_observes_client_eof(self):
        child,_folder,pinned=self.start_fixture('blocked-input')
        child.stdin.write(b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"data":"'+b'x'*262144+b'"}}\n')
        child.stdin.flush()
        child.stdin.close()
        self.assertEqual(child.wait(timeout=3),0)
        self.assertFalse(pinned.alive())

    def test_backend_eof_flushes_every_complete_frame_once(self):
        child,_folder,_pinned=self.start_fixture('ordered-eof')
        chunks=[]
        reader=threading.Thread(target=lambda: chunks.append(child.stdout.read()),daemon=True)
        reader.start()
        self.assertEqual(child.wait(timeout=5),0)
        reader.join(timeout=1)
        self.assertFalse(reader.is_alive())
        messages=[json.loads(line) for line in b''.join(chunks).splitlines()]
        self.assertEqual([item['params']['n'] for item in messages],list(range(24)))
        self.assertTrue(all(len(item['params']['data'])==4096 for item in messages))


if __name__=='__main__':
    unittest.main()
