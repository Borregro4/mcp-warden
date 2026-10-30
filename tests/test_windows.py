"""Real Windows handles and Job Objects; no agent apps or live backends."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'runtime' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.name == 'nt', 'Windows process ownership contract')
class WindowsOwnershipTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
