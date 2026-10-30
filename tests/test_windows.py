"""Real Windows handles and Job Objects; no agent apps or live backends."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'runtime' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.name == 'nt', 'Windows process ownership contract')
class WindowsOwnershipTests(unittest.TestCase):
    def test_failed_spawn_closes_job_without_resuming(self):
        lazy = load('test_lazy_spawn_error', 'lazy_stdio.py')
        with tempfile.TemporaryDirectory() as temporary:
            bridge = lazy.Bridge({'command': 'missing-fixture', 'cache_dir': temporary})
            job = Mock()
            with patch.object(lazy, 'WindowsJob', return_value=job), \
                    patch.object(lazy, '_OwnedPopen', side_effect=FileNotFoundError):
                with self.assertRaises(FileNotFoundError):
                    bridge.start()
            job.close.assert_called_once_with()
            job.attach_and_resume.assert_not_called()
            self.assertIsNone(bridge.proc)

    def test_identity_and_usage_are_observed_from_the_handle(self):
        owned = load('test_owned', 'owned_bridge.py')
        process = owned.PinnedProcess(os.getpid())
        try:
            self.assertTrue(process.alive())
            memory, cpu = process.usage()
            self.assertGreater(memory, 0)
            self.assertGreaterEqual(cpu, 0)
            self.assertGreater(process.created, 0)
            with self.assertRaisesRegex(ValueError, 'owner_identity_mismatch'):
                owned.PinnedProcess(os.getpid(), process.created + 1)
        finally:
            process.close()

    def test_job_close_stops_private_root_and_descendant(self):
        owned = load('test_owned_job', 'owned_bridge.py')
        lazy = load('test_lazy_job', 'lazy_stdio.py')
        program = (
            'import subprocess,sys,json; '
            'p=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"],'
            'creationflags=0x08000000); '
            'print(json.dumps({"child":p.pid}),flush=True); '
            'sys.stdin.buffer.read()'
        )
        job = lazy.WindowsJob()
        process = subprocess.Popen([sys.executable, '-I', '-u', '-c', program],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 | 0x00000004)
        child_identity = None
        try:
            job.attach_and_resume(process)
            child = json.loads(process.stdout.readline())['child']
            child_identity = owned.PinnedProcess(child)
            self.assertTrue(child_identity.alive())
            job.close()
            process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while child_identity.alive() and time.monotonic() < deadline:
                time.sleep(.03)
            self.assertFalse(child_identity.alive())
        finally:
            job.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            if child_identity:
                child_identity.close()

    def test_process_scope_hard_proxy_exit_kills_only_backend_root(self):
        """A live shared child survives our inner Job; the test owns an outer Job."""
        owned = load('test_owned_hard_exit', 'owned_bridge.py')
        lazy = load('test_lazy_hard_exit', 'lazy_stdio.py')
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ledger = directory / 'backend-identities.json'
            backend = directory / 'backend.py'
            backend.write_text(
                'import json,os,pathlib,subprocess,sys,time\n'
                'child=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"],'
                'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)\n'
                'pathlib.Path(sys.argv[1]).write_text(json.dumps({"root":os.getpid(),"shared":child.pid}))\n'
                'time.sleep(60)\n', encoding='utf-8')
            manifest = directory / 'manifest.json'
            manifest.write_text(json.dumps({'command': sys.executable,
                'args': ['-I', '-u', str(backend), str(ledger)],
                'cwd': str(directory), 'cache_dir': str(directory / 'cache'),
                'cleanup_scope': 'process'}), encoding='utf-8')
            proxy_source = directory / 'proxy.py'
            proxy_source.write_text(
                'import importlib.util,json,pathlib,sys\n'
                'spec=importlib.util.spec_from_file_location("probe_lazy",sys.argv[1])\n'
                'module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n'
                'bridge=module.Bridge(json.loads(pathlib.Path(sys.argv[2]).read_text()))\n'
                'bridge.start()\n'
                'sys.stdin.buffer.read()\n', encoding='utf-8')
            outer = lazy.WindowsJob()  # Only this test's subtree; never unrelated apps.
            proxy = subprocess.Popen([sys.executable, '-I', '-u', str(proxy_source),
                str(ROOT / 'runtime' / 'lazy_stdio.py'), str(manifest)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000 | 0x00000004)
            root = shared = None
            try:
                outer.attach_and_resume(proxy)
                deadline = time.monotonic() + 10
                while not ledger.exists() and time.monotonic() < deadline:
                    if proxy.poll() is not None:
                        self.fail('proxy exited before creating the synthetic backend')
                    time.sleep(.02)
                self.assertTrue(ledger.exists())
                identities = json.loads(ledger.read_text())
                root = owned.PinnedProcess(identities['root'])
                shared = owned.PinnedProcess(identities['shared'])
                self.assertGreater(root.created, 0)
                self.assertGreater(shared.created, 0)
                self.assertTrue(root.alive())
                self.assertTrue(shared.alive())
                proxy.kill()  # Exact Popen process handle; no cleanup handler runs.
                proxy.wait(timeout=5)
                deadline = time.monotonic() + 5
                while root.alive() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertFalse(root.alive(), 'the directly owned backend survived a hard proxy exit')
                self.assertTrue(shared.alive(), 'process-only cleanup killed a deliberately shared descendant')
            finally:
                outer.close()  # Releases every remaining synthetic descendant, by our own Job handle.
                if proxy.poll() is None:
                    proxy.kill()  # Also covers failure to attach the initially suspended proxy.
                    proxy.wait(timeout=5)
                proxy.stdin.close()
                for process in (root, shared):
                    if process:
                        deadline = time.monotonic() + 5
                        while process.alive() and time.monotonic() < deadline:
                            time.sleep(.02)
                        self.assertFalse(process.alive(), 'synthetic process remained after closing the test Job')
                        process.close()


if __name__ == '__main__':
    unittest.main()
