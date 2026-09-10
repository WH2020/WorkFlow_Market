"""Synthetic child used by one Windows API test. Does not inspect other processes."""
import ctypes
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import sys

payload = ctypes.create_string_buffer(("synthetic-only x'" + bytes(range(32)).hex() + "' end").encode())
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetCurrentProcess.restype = wt.HANDLE
kernel.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
kernel.GetProcessTimes.restype = wt.BOOL
kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel.QueryFullProcessImageNameW.restype = wt.BOOL
creation, end, system, user = (wt.FILETIME() for _ in range(4))
if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(creation), ctypes.byref(end), ctypes.byref(system), ctypes.byref(user)):
    raise RuntimeError("Cannot read synthetic helper creation time")
image_path, image_length = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
if not kernel.QueryFullProcessImageNameW(kernel.GetCurrentProcess(), 0, image_path, ctypes.byref(image_length)):
    raise RuntimeError("Cannot identify synthetic helper")
print(json.dumps({"pid": os.getpid(), "image_name": Path(image_path.value).name,
                  "created_at": str((creation.dwHighDateTime << 32) | creation.dwLowDateTime),
                  "address": ctypes.addressof(payload), "length": ctypes.sizeof(payload)}), flush=True)
sys.stdin.readline()
