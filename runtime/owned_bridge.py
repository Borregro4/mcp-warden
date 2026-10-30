"""Bind the existing lazy stdio bridge to a Windows client process identity.

Supergateway owns protocol forwarding. This adapter adds only Windows ownership;
the existing bridge continues to own backend startup, cache and Mobile cleanup.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time


class MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                *[(name, ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]


class PinnedProcess:
    def __init__(self, pid, expected_created=None):
        self.pid = int(pid)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.GetProcessTimes.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, self.pid)
        if not self.handle:
            raise ValueError('owner_unavailable')
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(self.handle, *(ctypes.byref(t) for t in times)):
                raise ValueError('owner_identity_unavailable')
            self.created = times[0].dwLowDateTime | times[0].dwHighDateTime << 32
            if expected_created is not None and self.created != int(expected_created):
                raise ValueError('owner_identity_mismatch')
            if not self.alive():
                raise ValueError('owner_exited')
        except BaseException:
            self.close()
            raise

    def alive(self):
        return bool(self.handle) and self.kernel.WaitForSingleObject(self.handle, 0) == 258

    def usage(self):
        times = [wintypes.FILETIME() for _ in range(4)]
        memory = MemoryCounters()
        memory.cb = ctypes.sizeof(memory)
        query = self.kernel.K32GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
        query.restype = wintypes.BOOL
        if not self.kernel.GetProcessTimes(self.handle, *(ctypes.byref(t) for t in times)):
            raise OSError('process_usage_unavailable')
        if not query(self.handle, ctypes.byref(memory), memory.cb):
            raise OSError('process_usage_unavailable')
        ticks = sum(t.dwLowDateTime | t.dwHighDateTime << 32 for t in times[2:])
        return memory.WorkingSetSize, ticks / 10_000_000

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def run(args):
    owner = PinnedProcess(args.owner_pid, args.owner_created)
    own = None
    done = threading.Event()
    watcher = None
    try:
        own = PinnedProcess(os.getpid())
        path = Path(args.bridge).resolve(strict=True)
        spec = importlib.util.spec_from_file_location('existing_lazy_bridge', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
        if not isinstance(manifest.get('command'), str) or not isinstance(manifest.get('args', []), list):
            raise ValueError('manifest_invalid')
        module.redirect_diagnostics(manifest, args.manifest)
        bridge = module.Bridge(manifest)
        identity = {'status': 'ready', 'owner_pid': owner.pid, 'owner_created': str(owner.created),
                    'bridge_pid': own.pid, 'bridge_created': str(own.created)}
        target = Path(args.identity_file)
        if not target.is_absolute() or target.exists():
            raise ValueError('identity_destination_invalid')
        temporary = target.with_suffix('.tmp')
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(identity, output)
        if not owner.alive():
            raise ValueError('owner_exited')
        os.replace(temporary, target)

        def watch_owner():
            backend_identity = None
            next_sample = 0
            while not done.wait(.1):
                if not owner.alive():
                    # Exactly the normal EOF route; shutdown is serialized by
                    # Bridge.run and preserves process-only Mobile ownership.
                    bridge.events.put(('client_eof', b''))
                    if backend_identity:
                        backend_identity.close()
                    return
                if time.monotonic() >= next_sample:
                    next_sample = time.monotonic() + 2
                    try:
                        if bridge.proc is not None and backend_identity is None:
                            backend_identity = PinnedProcess(bridge.proc.pid)
                        processes = [own] + ([backend_identity] if backend_identity and backend_identity.alive() else [])
                        values = [process.usage() for process in processes]
                        usage = {'sampled_at': time.time(), 'working_set_bytes': sum(v[0] for v in values),
                                 'cpu_seconds': sum(v[1] for v in values), 'process_count': len(values)}
                        usage_path = target.parent / 'usage.json'
                        temporary_usage = usage_path.with_suffix('.tmp')
                        temporary_usage.write_text(json.dumps(usage), encoding='utf-8')
                        os.replace(temporary_usage, usage_path)
                    except (OSError, ValueError):
                        pass  # Missing sample is not zero usage and must not interrupt MCP.
            if backend_identity:
                backend_identity.close()

        watcher = threading.Thread(target=watch_owner, daemon=True)
        watcher.start()
        return bridge.run()
    finally:
        done.set()
        if watcher:
            watcher.join(timeout=1)
        if own:
            own.close()
        owner.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--bridge', required=True)
    parser.add_argument('--owner-pid', type=int, required=True)
    parser.add_argument('--owner-created', required=True)
    parser.add_argument('--identity-file', required=True)
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as error:
        # No argument, environment, protocol payload or raw exception text.
        print('owned-bridge: setup/transport failed (' + type(error).__name__ + ')', file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
