"""Synthetic child used by native process-reader tests. Does not inspect other processes."""
import ctypes
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

payload = ctypes.create_string_buffer(("synthetic-only x'" + bytes(range(32)).hex() + "' end").encode())
if os.name == "nt":
    from ctypes import wintypes as wt
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
    created_at = str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    image_name = Path(image_path.value).name
elif sys.platform == "darwin":
    from agent_platform.wxdecipher_capture import _DarwinAPI
    info = _DarwinAPI().process_info(os.getpid())
    created_at, image_name = info["created_at"], Path(info["path"]).name
else:
    raise RuntimeError("Unsupported native process-reader test platform")
print(json.dumps({"pid": os.getpid(), "image_name": image_name, "created_at": created_at,
                  "address": ctypes.addressof(payload), "length": ctypes.sizeof(payload)}), flush=True)
sys.stdin.readline()
