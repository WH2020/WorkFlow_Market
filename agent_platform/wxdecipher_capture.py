"""Opt-in, same-user/session Windows key capture. Never called at startup.

Only QUERY_INFORMATION | VM_READ; no debug privilege, elevated host, injection,
pause, memory dump, directory discovery, key persistence or raw-key API result.
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
import re
import struct
import threading
import time

from .wechat_store import WechatStoreError
from .wxdecipher_crypto import PAGE_SIZE, SQLITE_HEADER, derive_keys

PROCESS_ACCESS = 0x0400 | 0x0010
ALLOWED_IMAGES = {"weixin.exe"}
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
    return os.name == "nt" and ctypes.sizeof(ctypes.c_void_p) == 8


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


def list_processes(payload: dict) -> dict:
    if payload.get("ownership_confirmed") is not True or payload.get("capture_confirmed") is not True:
        raise WechatStoreError("AUTHORIZATION_REQUIRED", "请确认本人账号并明确允许列出微信进程及本次只读取钥")
    api = _WindowsAPI()
    api.token_info(api.kernel.GetCurrentProcess(), check_privileges=True)
    snapshot = api.kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value or not snapshot:
        raise WechatStoreError("CAPTURE_DENIED", "无法列出微信进程")
    found = []
    skipped = 0
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        valid = api.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            if entry.szExeFile.casefold() in ALLOWED_IMAGES:
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
    return {"processes": found, "skipped": skipped,
            "message": "仅列出当前 Windows 用户/登录会话下可验证的 x64 微信进程。兼容扫描是实验性功能，未覆盖所有微信版本。"}


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
        # This observed layout is not used to accept keys by itself: pointers,
        # bounded readable ranges and stable double reads precede page HMAC.
        nodes = 0
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
    return keys, {"verified_databases": len(keys), "methods": sorted(methods), "scanned_bytes": scanned,
                  "elapsed_seconds": round(clock() - started, 2), "experimental": True}


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
        with _WindowsProcess(pid, created) as process:
            return scan_keys(process, pages)
    finally:
        _CAPTURE_LOCK.release()
