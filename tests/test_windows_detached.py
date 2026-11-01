"""Opt-in DETACHED_PROCESS with real pipes, suspended Jobs and shared-child safety.

Run via the task's qualified GUI Python launcher; do not create a new console
to launch this suite while investigating a blocked Windows console service.
"""
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
PYTHON = str(Path(sys.executable).with_name('python.exe')) if os.name == 'nt' else sys.executable


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'runtime' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lazy = load('test_detached_lazy', 'lazy_stdio.py')


class DetachedConfigurationTests(unittest.TestCase):
    def test_flag_is_opt_in_and_always_preserves_create_suspended(self):
        self.assertEqual(lazy.windows_creation_flags(), 0x08000004)
        self.assertEqual(lazy.windows_creation_flags(False), 0x08000004)
        self.assertEqual(lazy.windows_creation_flags(True), 0x0000000C)
        for value in (None, 0, 1, 'true', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lazy.windows_creation_flags(value)

    def test_manifest_rejects_non_boolean_detached_console(self):
        for value in (None, 0, 1, 'true', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lazy.Bridge({'command': sys.executable, 'windows_detached_console': value})


@unittest.skipUnless(os.name == 'nt', 'Windows native process ownership')
class DetachedOwnershipTests(unittest.TestCase):
    def test_detached_owned_child_executes_ack_and_preserves_all_stdio_and_exit(self):
        job = lazy.WindowsJob()
        child = None
        with tempfile.TemporaryDirectory(prefix='warden-detached-ack-') as temporary:
            marker = Path(temporary) / 'ack.json'
            program = (
                'import json,os,pathlib,sys; '
                'data=sys.stdin.buffer.read(); '
                'pathlib.Path(sys.argv[1]).write_text(json.dumps({"ack":True,"pid":os.getpid()})); '
                'sys.stdout.buffer.write(b"OUT:"+data);sys.stdout.buffer.flush(); '
                'sys.stderr.write("DETACHED_STDERR_ACK\\n");sys.stderr.flush(); '
                'raise SystemExit(17)'
            )
            try:
                child = lazy._OwnedPopen([PYTHON, '-I', '-u', '-c', program, str(marker)],
                    job=job, creationflags=lazy.windows_creation_flags(True),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=temporary)
                output, error = child.communicate(b'DETACHED_STDIN_ACK', timeout=8)
                self.assertEqual(child.returncode, 17)
                self.assertEqual(output, b'OUT:DETACHED_STDIN_ACK')
                self.assertIn(b'DETACHED_STDERR_ACK', error)
                self.assertTrue(json.loads(marker.read_text())['ack'])
            finally:
                job.close()
                if child and child.poll() is None:
                    child.wait(timeout=5)

    def test_detached_bridge_hard_exit_preserves_tree_and_process_ownership(self):
        owned = load('test_detached_owned', 'owned_bridge.py')
        for scope in ('tree', 'process'):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory(prefix='warden-detached-owner-') as temporary:
                directory = Path(temporary)
                ledger = directory / 'identities.json'
                backend = directory / 'backend.py'
                backend.write_text(
                    'import json,os,pathlib,subprocess,sys\n'
                    'child=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"],'
                    'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,'
                    'creationflags=0x00000008)\n'
                    'pathlib.Path(sys.argv[1]).write_text(json.dumps({"root":os.getpid(),"shared":child.pid}))\n'
                    'sys.stdin.buffer.read()\n', encoding='utf-8')
                manifest = directory / 'manifest.json'
                manifest.write_text(json.dumps({'command': PYTHON,
                    'args': ['-I', '-u', str(backend), str(ledger)],
                    'cwd': str(directory), 'cache_dir': str(directory / 'cache'),
                    'cleanup_scope': scope, 'windows_detached_console': True}), encoding='utf-8')
                proxy_source = directory / 'proxy.py'
                proxy_source.write_text(
                    'import importlib.util,json,pathlib,sys,time\n'
                    'spec=importlib.util.spec_from_file_location("detached_fixture_lazy",sys.argv[1])\n'
                    'module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n'
                    'bridge=module.Bridge(json.loads(pathlib.Path(sys.argv[2]).read_text()))\n'
                    'try:\n'
                    ' bridge.start()\n'
                    ' deadline=time.monotonic()+8\n'
                    ' while not pathlib.Path(sys.argv[3]).exists():\n'
                    '  if time.monotonic()>deadline: raise RuntimeError("fixture_ack_timeout")\n'
                    '  time.sleep(.02)\n'
                    ' print("DETACHED_OWNER_ACK",flush=True)\n'
                    ' sys.stdin.buffer.read()\n'
                    'finally: bridge.shutdown()\n', encoding='utf-8')
                # The outer Job owns this test's synthetic subtree only. It
                # retains shared descendants until the process-only assertion.
                outer = lazy.WindowsJob()
                proxy = root = shared = None
                try:
                    proxy = lazy._OwnedPopen([PYTHON, '-I', '-u', str(proxy_source),
                        str(ROOT / 'runtime' / 'lazy_stdio.py'), str(manifest), str(ledger)],
                        job=outer, creationflags=lazy.windows_creation_flags(True),
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    ack = queue.Queue()
                    threading.Thread(target=lambda: ack.put(proxy.stdout.readline()), daemon=True).start()
                    self.assertEqual(ack.get(timeout=10).strip(), b'DETACHED_OWNER_ACK')
                    identities = json.loads(ledger.read_text())
                    root = owned.PinnedProcess(identities['root'])
                    shared = owned.PinnedProcess(identities['shared'])
                    self.assertTrue(root.alive())
                    self.assertTrue(shared.alive())
                    proxy.kill()  # Exact handle: deliberately bypass normal EOF cleanup.
                    proxy.wait(timeout=5)
                    deadline = time.monotonic() + 5
                    while root.alive() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertFalse(root.alive(), 'owned backend root survived proxy death')
                    if scope == 'tree':
                        deadline = time.monotonic() + 5
                        while shared.alive() and time.monotonic() < deadline:
                            time.sleep(.02)
                        self.assertFalse(shared.alive(), 'tree-owned descendant survived proxy death')
                    else:
                        self.assertTrue(shared.alive(), 'process-only mode killed a shared descendant')
                finally:
                    outer.close()
                    if proxy:
                        if proxy.poll() is None:
                            proxy.wait(timeout=5)
                        for stream in (proxy.stdin, proxy.stdout, proxy.stderr):
                            stream.close()
                    for identity in (root, shared):
                        if identity:
                            deadline = time.monotonic() + 5
                            while identity.alive() and time.monotonic() < deadline:
                                time.sleep(.02)
                            self.assertFalse(identity.alive(), 'synthetic child survived fixture Job cleanup')
                            identity.close()


if __name__ == '__main__':
    unittest.main()
