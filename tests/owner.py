"""Synthetic process owner used only by Windows lifecycle tests."""
import ctypes
from ctypes import wintypes
import json
import os
import sys

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
kernel.GetProcessTimes.restype = wintypes.BOOL
times = [wintypes.FILETIME() for _ in range(4)]
if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(t) for t in times)):
    raise ctypes.WinError(ctypes.get_last_error())
created = times[0].dwLowDateTime | times[0].dwHighDateTime << 32
print(json.dumps({"pid": os.getpid(), "created": str(created), "python": sys.executable}), flush=True)
sys.stdin.buffer.read()
