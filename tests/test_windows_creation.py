"""Exact native-handle ownership, failure cleanup and concurrent launch isolation."""
import concurrent.futures
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_native_creation', ROOT / 'runtime' / 'lazy_stdio.py')
lazy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lazy)


@unittest.skipUnless(os.name == 'nt', 'Windows native process handles')
class CreationTests(unittest.TestCase):
    def test_setup_failures_terminate_unpublished_child_without_running_or_leaking_handles(self):
        import _winapi

        class NativeProxy:
            def __init__(self):
                self.pinned = None

            def __getattr__(self, name):
                return getattr(_winapi, name)

            def CreateProcess(self, *args, **kwargs):
                handles = _winapi.CreateProcess(*args, **kwargs)
                current = _winapi.GetCurrentProcess()
                self.pinned = _winapi.DuplicateHandle(current, handles[0], current,
                    0, False, _winapi.DUPLICATE_SAME_ACCESS)
                return handles

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]

        def handle_count():
            value = wintypes.DWORD()
            self.assertTrue(kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(value)))
            return value.value

        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'must-not-run'
            before = handle_count()
            for _ in range(3):
                for function, result in [('AssignProcessToJobObject', 0), ('ResumeThread', 0xFFFFFFFF), ('ResumeThread', 2)]:
                    with self.subTest(function=function, result=result):
                        job, native = lazy.WindowsJob(), NativeProxy()
                        original = lazy._CreateProcessProxy.__init__

                        def injected(proxy, real, selected):
                            original(proxy, native, selected)

                        try:
                            with patch.object(job.k, function, return_value=result), \
                                    patch.object(lazy._CreateProcessProxy, '__init__', injected):
                                with self.assertRaises((OSError, RuntimeError)):
                                    lazy._OwnedPopen([sys.executable, '-I', '-c',
                                        'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("ran")', str(marker)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        job=job, creationflags=0x08000004)
                            self.assertIsNotNone(native.pinned)
                            self.assertEqual(_winapi.WaitForSingleObject(native.pinned, 0), 0)
                            self.assertFalse(marker.exists())
                        finally:
                            if native.pinned is not None:
                                _winapi.CloseHandle(native.pinned)
                            job.close()
            self.assertEqual(handle_count(), before)

    def test_concurrent_launches_have_distinct_jobs_without_patching_global_winapi(self):
        native = subprocess.Popen._execute_child.__globals__['_winapi']
        jobs = [lazy.WindowsJob(), lazy.WindowsJob()]
        children = []
        try:
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(lazy._OwnedPopen,
                    [sys.executable, '-I', '-c', 'import sys;sys.stdin.buffer.read()'],
                    stdin=subprocess.PIPE, job=job, creationflags=0x08000004) for job in jobs]
                children = [future.result(timeout=5) for future in futures]
            kernel = jobs[0].k
            kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
            for index, child in enumerate(children):
                for selected, job in enumerate(jobs):
                    member = wintypes.BOOL()
                    self.assertTrue(kernel.IsProcessInJob(int(child._handle), job.handle, ctypes.byref(member)))
                    self.assertEqual(bool(member.value), index == selected)
            self.assertIs(subprocess.Popen._execute_child.__globals__['_winapi'], native)
            jobs[0].close()
            children[0].wait(timeout=5)
            self.assertIsNone(children[1].poll())
        finally:
            for job in jobs:
                job.close()
            for child in children:
                child.wait(timeout=5)
                child.stdin.close()

    def test_private_launcher_retains_cpython_quoting_environment_and_cwd(self):
        job = lazy.WindowsJob()
        with tempfile.TemporaryDirectory(prefix='warden quoting ') as directory:
            arguments = ['space and quote " slash\\', '日本語 México', '']
            environment = {**os.environ, 'WARDEN_CREATION_FIXTURE': 'local-only'}
            process = None
            try:
                process = lazy._OwnedPopen([sys.executable, '-I', '-c',
                    'import json,os,sys;print(json.dumps([sys.argv[1:],os.getcwd(),os.environ["WARDEN_CREATION_FIXTURE"]]))',
                    *arguments], job=job, cwd=directory, env=environment, stdout=subprocess.PIPE,
                    creationflags=0x08000000)
                output, _ = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0)
                observed = json.loads(output)
                self.assertEqual(observed[0], arguments)
                self.assertEqual(Path(observed[1]).resolve(), Path(directory).resolve())
                self.assertEqual(observed[2], 'local-only')
            finally:
                job.close()
                if process and process.poll() is None:
                    process.wait(timeout=5)

    def test_unsupported_cpython_shape_fails_before_process_creation(self):
        job = lazy.WindowsJob()
        try:
            with patch.object(subprocess.Popen, '_execute_child', new=lambda *args, **kwargs: None):
                with self.assertRaisesRegex(RuntimeError, 'unsupported_process_creation'):
                    lazy._OwnedPopen([sys.executable, '-I', '-c', 'raise SystemExit(0)'],
                        job=job, creationflags=0x08000004)
        finally:
            job.close()


if __name__ == '__main__':
    unittest.main()
