"""Run existing CI suites with normal per-user ownership on Windows runners."""
import ctypes
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys


@contextmanager
def current_user_default_owner():
    # Hosted Windows runners use an Administrator token whose default owner
    # is the Administrators group. Match ordinary user file creation without
    # relaxing any production ACL/owner check or changing existing files.
    if (os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true" or
            os.environ.get("RUNNER_OS") != "Windows"):
        yield
        return

    from agent_platform import wechat_privacy as privacy

    token = ctypes.c_void_p()
    if not privacy._advapi.OpenProcessToken(
        privacy._kernel.GetCurrentProcess(), 0x0080 | 0x0008, ctypes.byref(token),
    ):  # TOKEN_ADJUST_DEFAULT | TOKEN_QUERY
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        required = ctypes.c_uint32()
        ok = privacy._advapi.GetTokenInformation(token, 4, None, 0, ctypes.byref(required))
        if ok or ctypes.get_last_error() != 122 or required.value < ctypes.sizeof(ctypes.c_void_p):
            raise ctypes.WinError(ctypes.get_last_error())
        original = ctypes.create_string_buffer(required.value)
        if not privacy._advapi.GetTokenInformation(
            token, 4, original, len(original), ctypes.byref(required),
        ):  # TokenOwner: one SID pointer followed by its backing SID data
            raise ctypes.WinError(ctypes.get_last_error())
        set_information = privacy._advapi.SetTokenInformation
        set_information.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                    ctypes.c_void_p, ctypes.c_uint32]
        set_information.restype = ctypes.c_int
        sid = ctypes.create_string_buffer(privacy._win_current_sid())
        owner = ctypes.c_void_p(ctypes.addressof(sid))
        if not set_information(token, 4, ctypes.byref(owner), ctypes.sizeof(owner)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not set_information(token, 4, original, ctypes.sizeof(owner)):
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        privacy._win_close(token.value)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with current_user_default_owner():
        for suite in (["discover", "-s", "tests", "-t", ".", "-v"], ["ui.test_server", "-v"]):
            subprocess.run([sys.executable, "-m", "unittest", *suite], cwd=root, check=True)
