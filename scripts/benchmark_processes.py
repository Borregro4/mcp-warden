"""Read-only Windows resource samples for explicit benchmark-owned PID trees.

No process is killed, and no command line or environment is inspected.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
import time

parser = argparse.ArgumentParser()
parser.add_argument('--roots', nargs='+', type=int, required=True)
args = parser.parse_args()
if os.name != 'nt':
    parser.error('Windows only')

ULONG_PTR = ctypes.c_size_t
class ProcessEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ULONG_PTR),
        ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

class Counters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
        *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
        'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
        'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]]

class MemoryStatus(ctypes.Structure):
    _fields_ = [('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD),
        *[(name, ctypes.c_ulonglong) for name in ('ullTotalPhys', 'ullAvailPhys',
        'ullTotalPageFile', 'ullAvailPageFile', 'ullTotalVirtual', 'ullAvailVirtual',
        'ullAvailExtendedVirtual')]]

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]

snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
if snapshot == ctypes.c_void_p(-1).value:
    raise OSError('process snapshot unavailable')
parents = {}
try:
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
    while ok:
        parents[entry.th32ProcessID] = entry.th32ParentProcessID
        ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
finally:
    kernel.CloseHandle(snapshot)

selected = {pid for pid in args.roots if pid in parents}
while True:
    children = {pid for pid, parent in parents.items() if parent in selected}
    if children <= selected:
        break
    selected |= children

rows, unavailable = [], []
for pid in sorted(selected):
    handle = kernel.OpenProcess(0x1000 | 0x0010, False, pid)
    if not handle:
        unavailable.append(pid)
        continue
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            unavailable.append(pid)
            continue
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            unavailable.append(pid)
            continue
        ticks = [t.dwLowDateTime | t.dwHighDateTime << 32 for t in times]
        rows.append({'pid': pid, 'created': str(ticks[0]), 'parent': parents[pid],
            'workingSetBytes': counters.WorkingSetSize, 'privateBytes': counters.PrivateUsage,
            'cpuSeconds': (ticks[2] + ticks[3]) / 10_000_000})
    finally:
        kernel.CloseHandle(handle)

memory = MemoryStatus()
memory.dwLength = ctypes.sizeof(memory)
if not kernel.GlobalMemoryStatusEx(ctypes.byref(memory)):
    raise OSError('system memory status unavailable')
print(json.dumps({'sampledAt': time.time(), 'roots': args.roots, 'processes': rows,
    'processCount': len(rows), 'unavailable': unavailable,
    'workingSetBytes': sum(row['workingSetBytes'] for row in rows),
    'privateBytes': sum(row['privateBytes'] for row in rows),
    'cpuSeconds': sum(row['cpuSeconds'] for row in rows),
    'availableSystemBytes': memory.ullAvailPhys}))
