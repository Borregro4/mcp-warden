"""Read-only resource samples for a test-owned Windows process tree.
No command lines, environment, usernames or application data are read.
"""
import ctypes
from ctypes import wintypes
import json
import os
import sys

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

def main():
    root = int(sys.argv[1])
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise OSError('process_snapshot_unavailable')
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    rows = []
    try:
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            rows.append((entry.th32ProcessID, entry.th32ParentProcessID))
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    selected = {root}
    for _ in range(64):
        expanded = selected | {pid for pid, parent in rows if parent in selected and pid != os.getpid()}
        if expanded == selected:
            break
        selected = expanded
    samples = []
    for pid in sorted(selected):
        handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            continue
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            memory = MemoryCounters()
            memory.cb = ctypes.sizeof(memory)
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                continue
            if not kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
                continue
            created = times[0].dwLowDateTime | times[0].dwHighDateTime << 32
            cpu = sum(t.dwLowDateTime | t.dwHighDateTime << 32 for t in times[2:]) / 10_000_000
            samples.append({'pid': pid, 'created': str(created), 'workingSetBytes': memory.WorkingSetSize,
                            'cpuSeconds': cpu})
        finally:
            kernel.CloseHandle(handle)
    available = MemoryStatus()
    available.dwLength = ctypes.sizeof(available)
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(available)):
        raise OSError('memory_status_unavailable')
    print(json.dumps({'rootPid': root, 'processCount': len(samples), 'processes': samples,
                      'workingSetBytes': sum(p['workingSetBytes'] for p in samples),
                      'availablePhysicalBytes': available.ullAvailPhys,
                      'scope': 'test_tree_including_gateway_connector_and_backend'}))

if __name__ == '__main__':
    main()
