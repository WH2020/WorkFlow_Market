"""Opt-in, same-user process key capture for Windows and macOS.

Windows uses QUERY_INFORMATION | VM_READ; macOS uses libproc plus task_for_pid
and mach_vm_read_overwrite. There is no elevation, injection, pause, memory dump,
directory discovery, key persistence or raw-key API result.
WCDB Config.Cipher x64 is an EXPERIMENTAL observed layout, not a build guarantee.
Format evidence: fanyuantaier/wechatauto-replica, wechatauto/db.py (Config.Cipher).
Unknown layouts fail closed. Every accepted key is authenticated against an
explicitly uploaded SQLCipher4 page and later against the complete database.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import hmac
import os
from pathlib import Path
import plistlib
import platform
import re
import struct
import sys
import threading
import time

from .wechat_store import WechatStoreError
from .wxdecipher_crypto import PAGE_SIZE, SQLITE_HEADER, derive_keys

PROCESS_ACCESS = 0x0400 | 0x0010
ALLOWED_IMAGES = {"weixin.exe", "wechat"}
MAX_SCAN_BYTES = 1024 * 1024 * 1024  # total across both passes, not retained
MAX_SCAN_SECONDS = 30
MAX_CANDIDATES = 4096
CHUNK_BYTES = 1024 * 1024
MARKER = b"com.Tencent.WCDB.Config.Cipher"
CONFIG_MASK = bytes.fromhex("d2c7442458020000004889442450488b450048844c2448488944254048584c24")
LITERAL = re.compile(rb"(?<![0-9a-fA-F])[xX]'([0-9a-fA-F]{96}|[0-9a-fA-F]{64})'(?![0-9a-fA-F])")
WIDE_LITERAL = re.compile(rb"[xX]\x00'\x00((?:[0-9a-fA-F]\x00){96}|(?:[0-9a-fA-F]\x00){64})'\x00")
_CAPTURE_LOCK = threading.Lock()


def available() -> bool:
    return ctypes.sizeof(ctypes.c_void_p) == 8 and (os.name == "nt" or sys.platform == "darwin")


class _MemoryInfo(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
                ("Protect", wt.DWORD), ("Type", wt.DWORD)]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


class _WindowsAPI:
    def __init__(self):
        if not available():
            raise WechatStoreError("CAPTURE_UNSUPPORTED", "自动取密钥仅支持 Windows 64 位；其他环境请手动提供密钥")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        signatures = {
            "OpenProcess": (wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD]),
            "CloseHandle": (wt.BOOL, [wt.HANDLE]),
            "GetCurrentProcess": (wt.HANDLE, []), "GetCurrentProcessId": (wt.DWORD, []),
            "QueryFullProcessImageNameW": (wt.BOOL, [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]),
            "GetProcessTimes": (wt.BOOL, [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4),
            "GetExitCodeProcess": (wt.BOOL, [wt.HANDLE, ctypes.POINTER(wt.DWORD)]),
            "ProcessIdToSessionId": (wt.BOOL, [wt.DWORD, ctypes.POINTER(wt.DWORD)]),
            "IsWow64Process2": (wt.BOOL, [wt.HANDLE, ctypes.POINTER(wt.WORD), ctypes.POINTER(wt.WORD)]),
            "VirtualQueryEx": (ctypes.c_size_t, [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MemoryInfo), ctypes.c_size_t]),
            "ReadProcessMemory": (wt.BOOL, [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]),
            "CreateToolhelp32Snapshot": (wt.HANDLE, [wt.DWORD, wt.DWORD]),
            "Process32FirstW": (wt.BOOL, [wt.HANDLE, ctypes.POINTER(_ProcessEntry)]),
            "Process32NextW": (wt.BOOL, [wt.HANDLE, ctypes.POINTER(_ProcessEntry)]),
        }
        try:
            for name, (result, arguments) in signatures.items():
                function = getattr(self.kernel, name)
                function.restype, function.argtypes = result, arguments
            for name, result, arguments in (
                ("OpenProcessToken", wt.BOOL, [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]),
                ("GetTokenInformation", wt.BOOL, [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]),
                ("GetLengthSid", wt.DWORD, [ctypes.c_void_p]),
                ("LookupPrivilegeValueW", wt.BOOL, [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p]),
            ):
                function = getattr(self.advapi, name)
                function.restype, function.argtypes = result, arguments
        except AttributeError as error:
            raise WechatStoreError("CAPTURE_UNSUPPORTED", "当前 Windows 缺少所需只读进程 API，请手动提供密钥") from error

    def token_info(self, process, *, check_privileges=False) -> bytes:
        token = wt.HANDLE()
        if not self.advapi.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            raise WechatStoreError("CAPTURE_DENIED", "无法验证进程所属用户；不提权重试")
        try:
            def query(kind):
                length = wt.DWORD()
                self.advapi.GetTokenInformation(token, kind, None, 0, ctypes.byref(length))
                if not 1 <= length.value <= 65536:
                    raise WechatStoreError("CAPTURE_DENIED", "进程令牌信息无效")
                buffer = ctypes.create_string_buffer(length.value)
                if not self.advapi.GetTokenInformation(token, kind, buffer, length.value, ctypes.byref(length)):
                    raise WechatStoreError("CAPTURE_DENIED", "无法校验进程令牌")
                return buffer
            if check_privileges:
                if struct.unpack_from("<I", query(20).raw)[0]:
                    raise WechatStoreError("CAPTURE_PRIVILEGED", "自动取钥不在管理员工作台中运行，请使用普通用户启动工作台")
                debug_luid = ctypes.create_string_buffer(8)
                if not self.advapi.LookupPrivilegeValueW(None, "SeDebugPrivilege", debug_luid):
                    raise WechatStoreError("CAPTURE_DENIED", "无法确认调试权限状态")
                privileges = query(3).raw
                count = struct.unpack_from("<I", privileges)[0]
                if count > (len(privileges) - 4) // 12:
                    raise WechatStoreError("CAPTURE_DENIED", "进程权限列表无效")
                for index in range(count):
                    offset = 4 + index * 12
                    if privileges[offset:offset + 8] == debug_luid.raw and struct.unpack_from("<I", privileges, offset + 8)[0] & 2:
                        raise WechatStoreError("CAPTURE_PRIVILEGED", "检测到已启用的调试权限，已停止自动取钥")
            user = query(1)
            sid = ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0]
            length = self.advapi.GetLengthSid(sid)
            if not 8 <= length <= 256:
                raise WechatStoreError("CAPTURE_DENIED", "进程用户标识无效")
            return ctypes.string_at(sid, length)
        finally:
            self.kernel.CloseHandle(token)

    def session_id(self, pid: int) -> int:
        session = wt.DWORD()
        if not self.kernel.ProcessIdToSessionId(pid, ctypes.byref(session)):
            raise WechatStoreError("CAPTURE_DENIED", "无法确认 Windows 登录会话")
        return session.value


def _validate_identity(pid: object, created_at: object) -> tuple[int, str]:
    if type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF or not isinstance(created_at, str) or not re.fullmatch(r"\d{1,20}", created_at):
        raise WechatStoreError("CAPTURE_SELECTION", "请重新明确选择微信进程，不能只提供进程号")
    return pid, created_at


class _WindowsProcess:
    def __init__(self, pid: int, created_at: str | None, *, memory: bool = True):
        self.api = _WindowsAPI()
        self.pid, self.expected_time = pid, created_at
        self.handle = None
        self.memory = memory

    def __enter__(self):
        api = self.api
        self.owner = api.token_info(api.kernel.GetCurrentProcess(), check_privileges=True)
        self.session = api.session_id(api.kernel.GetCurrentProcessId())
        self.handle = api.kernel.OpenProcess(PROCESS_ACCESS if self.memory else 0x0400, False, self.pid)
        if not self.handle:
            self.handle = None
            raise WechatStoreError("CAPTURE_DENIED", "无法只读打开所选微信进程；可能权限不足或进程已退出，不会提权重试")
        try:
            self.identity = self.check_identity()
            process_machine, native_machine = wt.WORD(), wt.WORD()
            if not api.kernel.IsWow64Process2(self.handle, ctypes.byref(process_machine), ctypes.byref(native_machine)) or process_machine.value != 0 or native_machine.value != 0x8664:
                raise WechatStoreError("CAPTURE_UNSUPPORTED", "首版只支持原生 x64 微信进程，不支持 32 位或 ARM64")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.handle:
            self.api.kernel.CloseHandle(self.handle)
            self.handle = None

    def check_identity(self) -> tuple[str, str]:
        api = self.api
        path_buffer, length = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
        creation, end, kernel, user = (wt.FILETIME() for _ in range(4))
        code = wt.DWORD()
        if (not api.kernel.QueryFullProcessImageNameW(self.handle, 0, path_buffer, ctypes.byref(length))
                or not api.kernel.GetProcessTimes(self.handle, ctypes.byref(creation), ctypes.byref(end), ctypes.byref(kernel), ctypes.byref(user))
                or not api.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)) or code.value != 259):
            raise WechatStoreError("CAPTURE_PROCESS_CHANGED", "所选微信进程已退出或无法重新验证，请重新选择")
        created = str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        path = path_buffer.value
        if Path(path).name.casefold() not in ALLOWED_IMAGES:
            raise WechatStoreError("CAPTURE_SELECTION", "只允许用户选择的 Weixin.exe，不读取其他应用进程")
        if self.expected_time is not None and created != self.expected_time:
            raise WechatStoreError("CAPTURE_PROCESS_CHANGED", "进程号已被重新使用，请重新选择微信进程")
        if api.token_info(self.handle) != self.owner or api.session_id(self.pid) != self.session:
            raise WechatStoreError("CAPTURE_DENIED", "只允许当前 Windows 用户、当前登录会话的微信进程")
        identity = (path.casefold(), created)
        if getattr(self, "identity", identity) != identity:
            raise WechatStoreError("CAPTURE_PROCESS_CHANGED", "所选进程身份变化，已停止读取")
        return identity

    @staticmethod
    def readable(info: _MemoryInfo) -> bool:
        return info.State == 0x1000 and not info.Protect & (0x100 | 0x01) and (info.Protect & 0xFF) in {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}

    def read(self, address: int, length: int) -> bytes | None:
        if (not 0x10000 <= address < 0x800000000000 or not 1 <= length <= CHUNK_BYTES
                or address + length > 0x800000000000):
            return None
        info = _MemoryInfo()
        if not self.api.kernel.VirtualQueryEx(self.handle, address, ctypes.byref(info), ctypes.sizeof(info)) or not self.readable(info):
            return None
        if address + length > int(info.BaseAddress or 0) + info.RegionSize:
            return None
        buffer, received = ctypes.create_string_buffer(length), ctypes.c_size_t()
        try:
            success = self.api.kernel.ReadProcessMemory(self.handle, address, buffer, length, ctypes.byref(received))
            return buffer.raw if success and received.value == length else None
        finally:
            ctypes.memset(buffer, 0, length)

    def regions(self):
        address, count = 0, 0
        while address < 0x800000000000:
            info = _MemoryInfo()
            if not self.api.kernel.VirtualQueryEx(self.handle, address, ctypes.byref(info), ctypes.sizeof(info)):
                break
            start, length = int(info.BaseAddress or 0), int(info.RegionSize)
            if length <= 0 or start + length <= address:
                break
            if self.readable(info):
                yield start, length, info.Type
            address = start + length
            count += 1
            if count > 100000:
                raise WechatStoreError("CAPTURE_LIMIT", "进程内存区间数量超过安全扫描上限")


class _ProcBSDInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32), ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32), ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32), ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32), ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64), ("pbi_start_tvusec", ctypes.c_uint64),
    ]


class _VMRegionBasicInfo64(ctypes.Structure):
    # Darwin declares this MIG structure as an array of nine natural_t values.
    _pack_ = 4
    _fields_ = [
        ("protection", ctypes.c_int32), ("max_protection", ctypes.c_int32),
        ("inheritance", ctypes.c_int32), ("shared", ctypes.c_int32),
        ("reserved", ctypes.c_int32), ("offset", ctypes.c_uint64),
        ("behavior", ctypes.c_int32), ("user_wired_count", ctypes.c_uint16),
    ]


def _darwin_bundle(path: str) -> dict[str, str]:
    executable = Path(path)
    try:
        bundle = executable.parents[2]
    except IndexError:
        return {"bundle_id": "", "version": "", "build": ""}
    plist = bundle / "Contents/Info.plist"
    if bundle.suffix.casefold() != ".app" or not plist.is_file() or plist.is_symlink():
        return {"bundle_id": "", "version": "", "build": ""}
    try:
        if plist.stat().st_size > 1024 * 1024:
            raise ValueError("oversized plist")
        with plist.open("rb") as source:
            info = plistlib.load(source)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {"bundle_id": "", "version": "", "build": ""}
    return {
        "bundle_id": str(info.get("CFBundleIdentifier") or ""),
        "version": str(info.get("WeChatBundleVersion") or info.get("CFBundleShortVersionString") or ""),
        "build": str(info.get("CFBundleVersion") or ""),
    }


def _darwin_profile(version: str) -> tuple[str, bool]:
    if re.fullmatch(r"4\.1\.\d+(?:\.\d+)?", version):
        return "wechat-macos-4.1", True
    if re.fullmatch(r"4\.\d+(?:\.\d+){1,2}", version):
        return "wechat-macos-4.x-generic", False
    return "wechat-macos-unknown", False


class _DarwinAPI:
    PROC_ALL_PIDS = 1
    PROC_PIDTBSDINFO = 3
    PROC_FLAG_LP64 = 0x10
    VM_REGION_BASIC_INFO_64 = 9
    VM_PROT_READ = 0x01
    KERN_SUCCESS = 0
    KERN_INVALID_ADDRESS = 1

    def __init__(self):
        if sys.platform != "darwin" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise WechatStoreError("CAPTURE_UNSUPPORTED", "macOS 自动取钥需要 64 位 Darwin 运行环境")
        self.proc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.system = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        self.proc.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        self.proc.proc_listpids.restype = ctypes.c_int
        self.proc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        self.proc.proc_pidinfo.restype = ctypes.c_int
        self.proc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        self.proc.proc_pidpath.restype = ctypes.c_int
        self.system.task_for_pid.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32)]
        self.system.task_for_pid.restype = ctypes.c_int
        self.system.mach_vm_region.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
            ctypes.c_int, ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self.system.mach_vm_region.restype = ctypes.c_int
        self.system.mach_vm_read_overwrite.argtypes = [
            ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
        ]
        self.system.mach_vm_read_overwrite.restype = ctypes.c_int
        self.system.mach_port_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        self.system.mach_port_deallocate.restype = ctypes.c_int
        self.self_task = ctypes.c_uint32.in_dll(self.system, "mach_task_self_").value

    def process_info(self, pid: int) -> dict:
        info = _ProcBSDInfo()
        received = self.proc.proc_pidinfo(pid, self.PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info))
        if received != ctypes.sizeof(info):
            raise WechatStoreError("CAPTURE_PROCESS_CHANGED", "微信进程已退出或无法读取身份信息，请重新选择")
        path_buffer = ctypes.create_string_buffer(4096)
        if self.proc.proc_pidpath(pid, path_buffer, len(path_buffer)) <= 0:
            raise WechatStoreError("CAPTURE_PROCESS_CHANGED", "无法确认所选微信进程的可执行文件")
        path = os.fsdecode(path_buffer.value)
        bundle = _darwin_bundle(path)
        profile_id, version_supported = _darwin_profile(bundle["version"])
        return {
            "pid": int(info.pbi_pid), "uid": int(info.pbi_uid), "path": path,
            "created_at": str(info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec),
            "is_64_bit": bool(info.pbi_flags & self.PROC_FLAG_LP64),
            "architecture": platform.machine() or "64-bit",
            "version": bundle["version"], "build": bundle["build"],
            "bundle_id": bundle["bundle_id"], "profile_id": profile_id,
            "version_supported": version_supported,
        }

    def pids(self) -> list[int]:
        required = self.proc.proc_listpids(self.PROC_ALL_PIDS, 0, None, 0)
        if required <= 0:
            raise WechatStoreError("CAPTURE_DENIED", "macOS 无法列出当前用户进程")
        count = required // ctypes.sizeof(ctypes.c_int) + 64
        buffer = (ctypes.c_int * count)()
        received = self.proc.proc_listpids(self.PROC_ALL_PIDS, 0, buffer, ctypes.sizeof(buffer))
        if received <= 0:
            raise WechatStoreError("CAPTURE_DENIED", "macOS 无法列出当前用户进程")
        return sorted({int(buffer[index]) for index in range(received // ctypes.sizeof(ctypes.c_int)) if buffer[index] > 0})

    def task(self, pid: int) -> int:
        task = ctypes.c_uint32()
        result = self.system.task_for_pid(self.self_task, pid, ctypes.byref(task))
        if result != self.KERN_SUCCESS or not task.value:
            raise WechatStoreError(
                "CAPTURE_DENIED",
                "已发现微信进程，但 macOS 拒绝只读内存访问（task_for_pid）。微信启用了 Hardened Runtime；"
                "当前工作台不会关闭 SIP、重签名微信或提权，请改用手动密钥。",
            )
        return task.value

    def region(self, task: int, requested: int) -> tuple[int, int, int] | None:
        address, size = ctypes.c_uint64(requested), ctypes.c_uint64()
        info = _VMRegionBasicInfo64()
        count = ctypes.c_uint32(ctypes.sizeof(info) // ctypes.sizeof(ctypes.c_int32))
        object_name = ctypes.c_uint32()
        result = self.system.mach_vm_region(
            task, ctypes.byref(address), ctypes.byref(size), self.VM_REGION_BASIC_INFO_64,
            ctypes.cast(ctypes.byref(info), ctypes.POINTER(ctypes.c_int32)), ctypes.byref(count), ctypes.byref(object_name),
        )
        if object_name.value:
            self.system.mach_port_deallocate(self.self_task, object_name.value)
        if result == self.KERN_INVALID_ADDRESS:
            return None
        if result != self.KERN_SUCCESS or size.value <= 0:
            raise WechatStoreError("CAPTURE_DENIED", "macOS 无法枚举所选微信进程的内存区域")
        return address.value, size.value, info.protection

    def close_task(self, task: int) -> None:
        if task:
            self.system.mach_port_deallocate(self.self_task, task)


class _DarwinProcess:
    MAX_ADDRESS = 0x0000FFFFFFFFFFFF

    def __init__(self, pid: int, created_at: str | None, *, memory: bool = True):
        self.api = _DarwinAPI()
        self.pid, self.expected_time, self.memory = pid, created_at, memory
        self.task = 0
        self.identity = None
        self.capture_metadata = {}
        self.supports_wcdb_x64_layout = False
        self.capture_layout = "literal-only"

    def __enter__(self):
        self.identity = self.check_identity()
        if self.memory:
            self.task = self.api.task(self.pid)
        return self

    def __exit__(self, *_):
        if self.task:
            self.api.close_task(self.task)
            self.task = 0

    def check_identity(self) -> tuple[str, str]:
        info = self.api.process_info(self.pid)
        path = info["path"]
        if Path(path).name.casefold() not in ALLOWED_IMAGES:
            raise WechatStoreError("CAPTURE_SELECTION", "只允许用户选择的 macOS 微信主进程，不读取其他应用进程")
        if Path(path).name.casefold() == "wechat" and info["bundle_id"] != "com.tencent.xinWeChat":
            raise WechatStoreError("CAPTURE_SELECTION", "所选进程不是腾讯 macOS 微信主应用")
        if info["uid"] != os.getuid():
            raise WechatStoreError("CAPTURE_DENIED", "只允许当前 macOS 用户启动的微信进程")
        if not info["is_64_bit"]:
            raise WechatStoreError("CAPTURE_UNSUPPORTED", "macOS 自动取钥仅支持 64 位微信进程")
        if self.expected_time is not None and info["created_at"] != self.expected_time:
            raise WechatStoreError("CAPTURE_PROCESS_CHANGED", "所选微信进程已退出或 PID 被重新使用，请重新选择")
        identity = (path.casefold(), info["created_at"])
        if self.identity is not None and self.identity != identity:
            raise WechatStoreError("CAPTURE_PROCESS_CHANGED", "所选微信进程身份发生变化，已停止读取")
        self.capture_metadata = {key: info[key] for key in (
            "architecture", "version", "build", "profile_id", "version_supported",
        )}
        if info["profile_id"] == "wechat-macos-4.1":
            self.supports_wcdb_x64_layout = True
            self.capture_layout = "darwin-wcdb-4.1"
        return identity

    def regions(self):
        if not self.task:
            raise WechatStoreError("CAPTURE_DENIED", "macOS 微信进程尚未取得只读任务端口")
        address, count = 0, 0
        while address < self.MAX_ADDRESS:
            region = self.api.region(self.task, address)
            if region is None:
                break
            start, length, protection = region
            if start < address or length <= 0 or start + length <= address:
                raise WechatStoreError("CAPTURE_DENIED", "macOS 返回了无效的进程内存区域")
            if protection & self.api.VM_PROT_READ:
                yield start, length, 0x20000
            address = start + length
            count += 1
            if count > 100000:
                raise WechatStoreError("CAPTURE_LIMIT", "进程内存区间数量超过扫描上限")

    def read(self, address: int, length: int) -> bytes | None:
        if (not self.task or not 0x1000 <= address < self.MAX_ADDRESS or not 1 <= length <= CHUNK_BYTES
                or address + length > self.MAX_ADDRESS):
            return None
        region = self.api.region(self.task, address)
        if region is None:
            return None
        start, size, protection = region
        if start > address or address + length > start + size or not protection & self.api.VM_PROT_READ:
            return None
        buffer, received = ctypes.create_string_buffer(length), ctypes.c_uint64()
        try:
            result = self.api.system.mach_vm_read_overwrite(
                self.task, address, length, ctypes.addressof(buffer), ctypes.byref(received),
            )
            return buffer.raw if result == self.api.KERN_SUCCESS and received.value == length else None
        finally:
            ctypes.memset(buffer, 0, length)


def _capture_process(pid: int, created_at: str | None, *, memory: bool = True):
    if os.name == "nt":
        return _WindowsProcess(pid, created_at, memory=memory)
    if sys.platform == "darwin":
        return _DarwinProcess(pid, created_at, memory=memory)
    raise WechatStoreError("CAPTURE_UNSUPPORTED", "自动取密钥仅支持 Windows 64 位和 macOS 64 位")


def list_processes(payload: dict) -> dict:
    if payload.get("ownership_confirmed") is not True or payload.get("capture_confirmed") is not True:
        raise WechatStoreError("AUTHORIZATION_REQUIRED", "请确认本人账号并明确允许列出微信进程及本次只读取钥")
    found = []
    skipped = 0
    if os.name == "nt":
        api = _WindowsAPI()
        api.token_info(api.kernel.GetCurrentProcess(), check_privileges=True)
        snapshot = api.kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == ctypes.c_void_p(-1).value or not snapshot:
            raise WechatStoreError("CAPTURE_DENIED", "无法列出微信进程")
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            valid = api.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while valid:
                if entry.szExeFile.casefold() == "weixin.exe":
                    try:
                        with _WindowsProcess(entry.th32ProcessID, None, memory=False) as selected:
                            found.append({"process_id": entry.th32ProcessID, "created_at": selected.identity[1], "name": "Weixin.exe"})
                    except WechatStoreError:
                        skipped += 1
                    if len(found) >= 32:
                        break
                valid = api.kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            api.kernel.CloseHandle(snapshot)
        message = "仅列出当前 Windows 用户/登录会话下可验证的 x64 微信进程。兼容扫描是实验性功能。"
    elif sys.platform == "darwin":
        api = _DarwinAPI()
        for pid in api.pids():
            try:
                info = api.process_info(pid)
                if Path(info["path"]).name.casefold() != "wechat" or info["bundle_id"] != "com.tencent.xinWeChat":
                    continue
                with _DarwinProcess(pid, None, memory=False) as selected:
                    found.append({
                        "process_id": pid, "created_at": selected.identity[1], "name": "微信",
                        **selected.capture_metadata,
                    })
            except WechatStoreError:
                continue
            if len(found) >= 32:
                break
        message = ("仅列出当前 macOS 用户的 64 位微信主进程；发现进程不代表 Hardened Runtime 允许读取内存。"
                   "微信 4.1.x 使用专用版本描述，其他 4.x 使用通用实验性扫描。")
    else:
        raise WechatStoreError("CAPTURE_UNSUPPORTED", "当前平台不支持自动取钥")
    return {"processes": found, "skipped": skipped, "message": message}


def literal_candidates(data: bytes):
    for match in LITERAL.finditer(data):
        run = match.group(1)
        yield match.start(), match.end(), bytes.fromhex(run[:64].decode("ascii")), bytes.fromhex(run[64:].decode("ascii")) if len(run) == 96 else None
    for match in WIDE_LITERAL.finditer(data):
        # Tight literal boundaries; do not search arbitrary windows in hex runs.
        start, end = match.span()
        if start >= 2 and data[start - 1:start] == b"\0" and data[start - 2:start - 1] in b"0123456789abcdefABCDEF":
            continue
        if data[end + 1:end + 2] == b"\0" and data[end:end + 1] in b"0123456789abcdefABCDEF":
            continue
        run = match.group(1)[::2]
        yield start, end, bytes.fromhex(run[:64].decode("ascii")), bytes.fromhex(run[64:].decode("ascii")) if len(run) == 96 else None


def scan_keys(process, pages: dict[str, bytes], *, clock=time.monotonic) -> tuple[dict[str, str], dict]:
    """Testable bounded scanner; process is an already verified single handle."""
    started = clock()
    scanned = candidates = 0
    keys: dict[str, str] = {}
    tested = set()
    markers = set()
    methods = set()

    def budget():
        if clock() - started > MAX_SCAN_SECONDS or scanned > MAX_SCAN_BYTES or candidates > MAX_CANDIDATES:
            raise WechatStoreError("CAPTURE_LIMIT", "本次取钥已到 30 秒、1 GiB 读取量或候选数上限；未完成的数据库不会导入，可改用手动密钥")

    def read(address, length):
        nonlocal scanned
        budget()
        if not 1 <= length <= CHUNK_BYTES:
            return None
        if scanned + length > MAX_SCAN_BYTES:
            raise WechatStoreError("CAPTURE_LIMIT", "本次只读扫描已达到 1 GiB 上限，请改用手动密钥")
        scanned += length
        result = process.read(address, length)
        budget()
        return result

    def accept(key, salt, method):
        nonlocal candidates
        identity = (key, salt)
        if identity in tested:
            return
        tested.add(identity)
        candidates += 1
        budget()
        for name, page in pages.items():
            if name in keys or (salt is not None and salt != page[:16]):
                continue
            _, mac = derive_keys(key, page[:16], "sqlcipher4-raw")
            actual = hmac.digest(mac, page[16:4032] + struct.pack("<I", 1), "sha512")
            if hmac.compare_digest(actual, page[4032:4096]):
                keys[name] = key.hex()
                methods.add(method)

    def chunks(regions):
        for base, size, _ in regions:
            offset, carry = 0, b""
            while offset < size:
                budget()
                length = min(CHUNK_BYTES, size - offset)
                address = base + offset
                block = read(address, length)
                if block is not None:
                    yield address - len(carry), carry + block
                    carry = block[-256:]
                else:
                    carry = b""
                offset += length

    # Image regions first: the WCDB marker is usually a DLL constant. Reads
    # remain strictly on the selected process; no DLL/path/version discovery.
    regions = []
    for region in process.regions():
        budget()
        regions.append(region)
    regions.sort(key=lambda region: region[0])
    for address, block in chunks([region for region in regions if region[2] == 0x1000000]):
        start = 0
        while (start := block.find(MARKER, start)) >= 0:
            markers.add(address + start)
            start += len(MARKER)
            if len(markers) > 64:
                raise WechatStoreError("CAPTURE_LIMIT", "兼容结构锚点过多，无法安全确定布局，请手动提供密钥")
        for first, end, key, salt in literal_candidates(block):
            if read(address + first, end - first) == block[first:end]:
                accept(key, salt, "raw-literal")
        if len(keys) == len(pages):
            break
    if len(keys) < len(pages):
        # All platforms scan exact key literals in ordinary readable regions.
        # The observed Windows WCDB pointer layout is an additional strategy.
        nodes = 0
        pointer_layout = getattr(process, "supports_wcdb_x64_layout", True)
        for address, block in chunks([region for region in regions if region[2] != 0x1000000]):
            start = 0
            while (start := block.find(MARKER, start)) >= 0:
                markers.add(address + start)
                start += len(MARKER)
                if len(markers) > 64:
                    raise WechatStoreError("CAPTURE_LIMIT", "兼容结构锚点过多，请手动提供密钥")
            for first, end, key, salt in literal_candidates(block):
                if read(address + first, end - first) == block[first:end]:
                    accept(key, salt, "raw-literal")
            if len(keys) == len(pages):
                break
            if not pointer_layout:
                continue
            patterns = [(marker, struct.pack("<QQ", marker, len(MARKER))) for marker in sorted(markers)]
            for marker, pattern in patterns:
                position = 0
                while (position := block.find(pattern, position)) >= 0:
                    node_address = address + position - 0x10
                    position += len(pattern)
                    nodes += 1
                    budget()
                    if nodes > 4096:
                        raise WechatStoreError("CAPTURE_LIMIT", "兼容结构候选超过上限，请改用手动密钥")
                    node = read(node_address, 0x50)
                    if node is None or node[0x10:0x20] != pattern or read(marker, len(MARKER)) != MARKER:
                        continue
                    config = struct.unpack_from("<Q", node, 0x28)[0]
                    if getattr(process, "capture_layout", "windows-wcdb-x64") == "darwin-wcdb-4.1":
                        # WCDB CipherConfig contains Data m_key/m_rawKey. On 64-bit
                        # Darwin a Data-compatible object exposes vptr, buffer and
                        # size in its first three words. Minor 4.1 builds can move
                        # the member, so examine a bounded aligned window and use
                        # the uploaded page HMAC as the only acceptance criterion.
                        for data_offset in range(0x60, 0x181, 8):
                            obj = read(config + data_offset, 0x20)
                            if obj is None:
                                continue
                            pointer, length = struct.unpack_from("<QQ", obj, 8)
                            if length not in {32, 48} and not 67 <= length <= 192:
                                continue
                            blob = read(pointer, length)
                            if (blob is None or read(pointer, length) != blob
                                    or read(config + data_offset, 0x20) != obj):
                                continue
                            if length in {32, 48}:
                                accept(blob[:32], blob[32:48] if length == 48 else None, "experimental-wcdb-darwin-4.1")
                            else:
                                for _, _, key, salt in literal_candidates(blob):
                                    accept(key, salt, "experimental-wcdb-darwin-4.1")
                            if len(keys) == len(pages):
                                break
                        if len(keys) == len(pages):
                            break
                        continue
                    obj = read(config + 0x88, 0x28)
                    if obj is None:
                        continue
                    pointer, length = struct.unpack_from("<QQ", obj, 8)
                    if not 67 <= length <= 1024:
                        continue
                    blob = read(pointer, length)
                    if (blob is None or read(pointer, length) != blob or read(config + 0x88, 0x28) != obj
                            or read(node_address, 0x50) != node):
                        continue
                    decoded = bytes(value ^ CONFIG_MASK[index % len(CONFIG_MASK)] for index, value in enumerate(blob))
                    for _, _, key, salt in literal_candidates(decoded):
                        accept(key, salt, "experimental-wcdb-x64")
            if len(keys) == len(pages):
                break
    process.check_identity()
    budget()
    if len(keys) != len(pages):
        raise WechatStoreError("CAPTURE_UNAVAILABLE", f"只找到 {len(keys)}/{len(pages)} 个匹配数据库的密钥；可能版本布局不支持、密钥未驻留或副本账号不匹配。未导入，请手动提供密钥或重新选择进程。")
    metadata = getattr(process, "capture_metadata", {})
    return keys, {"verified_databases": len(keys), "methods": sorted(methods), "scanned_bytes": scanned,
                  "elapsed_seconds": round(clock() - started, 2), "experimental": True,
                  **{key: metadata[key] for key in ("architecture", "version", "build", "profile_id", "version_supported")
                     if key in metadata}}


def capture_keys(sources: dict[str, Path], payload: dict) -> tuple[dict[str, str], dict]:
    if not isinstance(payload, dict) or payload.get("confirmed") is not True:
        raise WechatStoreError("AUTHORIZATION_REQUIRED", "自动取钥必须由用户明确授权本次只读进程访问")
    pid, created = _validate_identity(payload.get("process_id"), payload.get("created_at"))
    pages = {}
    for name, path in sources.items():
        with path.open("rb") as reader:
            page = reader.read(PAGE_SIZE)
        if page.startswith(SQLITE_HEADER):
            continue
        if len(page) != PAGE_SIZE:
            raise WechatStoreError("INVALID_DATABASE", "所选加密库没有完整首页，不能校验捕获密钥")
        pages[name] = page
    if not pages:
        return {}, {"verified_databases": 0, "methods": [], "scanned_bytes": 0, "experimental": True}
    if not _CAPTURE_LOCK.acquire(blocking=False):
        raise WechatStoreError("DECIPHER_BUSY", "已有一次取钥在运行，请等待结束后重试")
    try:
        with _capture_process(pid, created) as process:
            return scan_keys(process, pages)
    finally:
        _CAPTURE_LOCK.release()
