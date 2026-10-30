"""Read-only benchmark samples using the canonical ownership validator.

Only explicit roots and timestamp-validated descendants are measured. No
command lines or environments are read, and no process is killed.
"""
import argparse
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import time

_spec = importlib.util.spec_from_file_location('benchmark_canonical_metrics',
    Path(__file__).resolve().with_name('soak_metrics.py'))
_metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metrics)


class PrivateCounters(_metrics.MemoryCounters):
    _fields_ = [('PrivateUsage', ctypes.c_size_t)]


class BenchmarkReader(_metrics.WindowsReader):
    def __init__(self):
        super().__init__()
        self.kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        self.parents = {}

    def query(self, pid):
        observed = super().query(pid)
        if observed is None:
            return None
        handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            raise OSError('process_private_usage_unavailable')
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            counters = PrivateCounters()
            counters.cb = ctypes.sizeof(counters)
            if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise OSError('process_identity_unavailable')
            created = times[0].dwLowDateTime | times[0].dwHighDateTime << 32
            if str(created) != observed['created']:
                raise OSError('process_identity_changed_during_sample')
            if not self.kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                raise OSError('process_private_usage_unavailable')
            observed['privateBytes'] = counters.PrivateUsage
            observed['parent'] = self.parents.get(pid)
            return observed
        finally:
            self.kernel.CloseHandle(handle)


def collect(reader, roots, expected):
    snapshot, cutoff = reader.snapshot()
    reader.parents = dict(snapshot)
    cache, unavailable = {}, set()

    def query(pid):
        if pid not in cache:
            try:
                cache[pid] = reader.query(pid)
            except OSError:
                unavailable.add(pid)
                raise
        return cache[pid]

    selected, root_identities = {}, {}
    rejected_edges, rejected_roots = 0, []
    for root in sorted(set(roots)):
        if root <= 0 or root not in reader.parents:
            continue
        try:
            value = query(root)
            if value is None:
                continue
            if root in expected and str(value['created']) != expected[root]:
                rejected_roots.append({'pid': root, 'expectedCreated': expected[root],
                    'observedCreated': value['created']})
                continue
            if int(value['created']) > cutoff:
                unavailable.add(root)
                continue
            values, rejected, _ = _metrics.select_tree(snapshot, root, query, cutoff, (os.getpid(),))
        except (OSError, ValueError):
            unavailable.add(root)
            continue
        root_identities[str(root)] = value['created']
        rejected_edges += rejected
        selected.update((row['pid'], row) for row in values)
    rows = list(selected.values())
    return {'sampledAt': time.time(), 'roots': roots, 'rootIdentities': root_identities,
        'processes': rows, 'processCount': len(rows), 'unavailable': sorted(unavailable),
        'workingSetBytes': sum(row['workingSetBytes'] for row in rows),
        'privateBytes': sum(row['privateBytes'] for row in rows),
        'cpuSeconds': sum(row['cpuSeconds'] for row in rows),
        'availableSystemBytes': reader.available(), 'rejectedParentEdges': rejected_edges,
        'rejectedRootIdentities': rejected_roots,
        'scope': 'explicit_roots_and_creation_validated_descendants'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--roots', nargs='+', type=int, required=True)
    parser.add_argument('--root-birth', action='append', default=[])
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows only')
    expected = {}
    for pair in args.root_birth:
        try:
            pid_text, created = pair.split(':', 1)
            pid = int(pid_text)
            if pid not in args.roots or not created.isdigit() or not 0 < int(created) < 2**64:
                raise ValueError()
            expected[pid] = str(int(created))
        except ValueError:
            parser.error('invalid explicit root birth identity')
    print(json.dumps(collect(BenchmarkReader(), args.roots, expected)))


if __name__ == '__main__':
    main()
