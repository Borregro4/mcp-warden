"""Read-only Windows test-tree samples and exact known-identity liveness.
Parent edges require child creation after parent creation. Known identities are
queried directly even if their parent exited. No command lines are read.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path

class ProcessEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

class MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                  'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                  'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]

class MemoryStatus(ctypes.Structure):
    _fields_ = [('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD),
                *[(name, ctypes.c_ulonglong) for name in ('ullTotalPhys', 'ullAvailPhys',
                  'ullTotalPageFile', 'ullAvailPageFile', 'ullTotalVirtual',
                  'ullAvailVirtual', 'ullAvailExtendedVirtual')]]

def select_tree(rows, root, query, cutoff, excluded=()):
    root_value = query(root)
    if root_value is None:
        raise ValueError('root_identity_unavailable')
    selected = {root: root_value}
    rejected, unavailable = set(), set()
    for _ in range(64):
        before = len(selected)
        for pid, parent in rows:
            if parent not in selected or pid in selected or pid in excluded or (pid, parent) in rejected:
                continue
            try:
                child = query(pid)
            except OSError:
                unavailable.add(pid)
                continue
            if child is None:
                continue
            # Stale PPID after parent PID reuse must not adopt an older child.
            # A PID created after snapshot start may itself have been reused.
            if int(child['created']) < int(selected[parent]['created']) or int(child['created']) > cutoff:
                rejected.add((pid, parent))
                continue
            selected[pid] = child
        if len(selected) == before:
            break
    return list(selected.values()), len(rejected), len(unavailable)

def select_known(identities, query):
    alive, unavailable = [], []
    for identity in identities:
        try:
            current = query(identity['pid'])
        except OSError:
            unavailable.append(identity)
            continue
        if current is not None and str(current['created']) == str(identity['created']):
            alive.append(current)
    return alive, unavailable

class WindowsReader:
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel = self.kernel
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]

    def snapshot(self):
        before = wintypes.FILETIME()
        self.kernel.GetSystemTimePreciseAsFileTime(ctypes.byref(before))
        cutoff = before.dwLowDateTime | before.dwHighDateTime << 32
        handle = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError('process_snapshot_unavailable')
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        rows = []
        try:
            found = self.kernel.Process32FirstW(handle, ctypes.byref(entry))
            while found:
                rows.append((entry.th32ProcessID, entry.th32ParentProcessID))
                found = self.kernel.Process32NextW(handle, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(handle)
        return rows, cutoff

    def query(self, pid):
        handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            code = ctypes.get_last_error()
            if code == 87:
                return None
            raise OSError(code, 'process_identity_unavailable')
        try:
            state = self.kernel.WaitForSingleObject(handle, 0)
            if state == 0:
                return None
            if state != 258:
                raise OSError('process_liveness_unavailable')
            times = [wintypes.FILETIME() for _ in range(4)]
            memory = MemoryCounters()
            memory.cb = ctypes.sizeof(memory)
            if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise OSError('process_identity_unavailable')
            if not self.kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
                raise OSError('process_usage_unavailable')
            created = times[0].dwLowDateTime | times[0].dwHighDateTime << 32
            cpu = sum(t.dwLowDateTime | t.dwHighDateTime << 32 for t in times[2:]) / 10_000_000
            return {'pid': pid, 'created': str(created), 'workingSetBytes': memory.WorkingSetSize, 'cpuSeconds': cpu}
        finally:
            self.kernel.CloseHandle(handle)

    def available(self):
        value = MemoryStatus()
        value.dwLength = ctypes.sizeof(value)
        if not self.kernel.GlobalMemoryStatusEx(ctypes.byref(value)):
            raise OSError('memory_status_unavailable')
        return value.ullAvailPhys

def read_identities(path):
    raw = Path(path).read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('identity_file_too_large')
    values = json.loads(raw)
    if not isinstance(values, list) or len(values) > 512:
        raise ValueError('invalid_identity_list')
    for value in values:
        if (not isinstance(value, dict) or type(value.get('pid')) is not int
                or not 0 < value['pid'] <= 0xffffffff or not isinstance(value.get('created'), str)
                or not value['created'].isdigit() or not 0 < int(value['created']) < 2**64):
            raise ValueError('invalid_process_identity')
    return values

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=int)
    parser.add_argument('--identities')
    args = parser.parse_args()
    reader = WindowsReader()
    rows, cutoff = reader.snapshot()
    samples, rejected, unavailable = select_tree(rows, args.root, reader.query, cutoff, (os.getpid(),))
    known, unknown = select_known(read_identities(args.identities), reader.query) if args.identities else ([], [])
    print(json.dumps({'rootPid': args.root, 'processCount': len(samples), 'processes': samples,
                      'workingSetBytes': sum(p['workingSetBytes'] for p in samples),
                      'availablePhysicalBytes': reader.available(), 'rejectedParentEdges': rejected,
                      'unavailableTreeCandidates': unavailable, 'knownAlive': known, 'knownUnavailable': unknown,
                      'scope': 'pid_and_creation_time_validated_test_tree'}))

if __name__ == '__main__':
    main()
