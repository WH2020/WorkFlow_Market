"""Authenticate local loopback HTTP peers before issuing UI capabilities.

Loopback/Host checks and a public bootstrap token do not distinguish local OS
users. Windows uses IP Helper and process tokens; macOS uses the system
lsof/libproc view and process identity. Both bind the exact TCP client to the
same OS user and protect against PID reuse. No process memory, directories,
names or command lines are read. This does not protect against malware already
running as this OS user, administrators, or trusted-user proxies.

https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcprow_owner_module
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import os
import socket
import struct
import subprocess
import sys
from datetime import datetime


class LocalAccessError(PermissionError):
    pass


class _TcpRow(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "state", "local_addr", "local_port", "remote_addr", "remote_port", "pid"
    )] + [("created", ctypes.c_int64), ("module", ctypes.c_uint64 * 16)]


class _TcpTable(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("rows", _TcpRow * 1)]


class _WindowsPeerAPI:
    def __init__(self):
        self.ip = ctypes.WinDLL("iphlpapi", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.ip.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD), wt.BOOL, wt.ULONG, ctypes.c_int, wt.ULONG]
        self.ip.GetExtendedTcpTable.restype = wt.DWORD
        for name, result, arguments in (
            ("OpenProcess", wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD]),
            ("CloseHandle", wt.BOOL, [wt.HANDLE]),
            ("GetCurrentProcess", wt.HANDLE, []),
            ("GetProcessTimes", wt.BOOL, [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4),
            ("GetExitCodeProcess", wt.BOOL, [wt.HANDLE, ctypes.POINTER(wt.DWORD)]),
        ):
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = result, arguments
        for name, result, arguments in (
            ("OpenProcessToken", wt.BOOL, [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]),
            ("GetTokenInformation", wt.BOOL, [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]),
            ("GetLengthSid", wt.DWORD, [ctypes.c_void_p]),
        ):
            function = getattr(self.advapi, name)
            function.restype, function.argtypes = result, arguments

    def connection_owner(self, client: tuple, server: tuple) -> tuple[int, int]:
        """Return a unique live client-side context. Never trust just a PID."""
        size = wt.DWORD()
        for _ in range(3):
            buffer = ctypes.create_string_buffer(size.value) if size.value else None
            capacity = size.value
            status = self.ip.GetExtendedTcpTable(buffer, ctypes.byref(size), False, socket.AF_INET, 8, 0)
            if status == 122:  # ERROR_INSUFFICIENT_BUFFER; table can grow.
                if not _TcpTable.rows.offset <= size.value <= 8 * 1024 * 1024:
                    break
                continue
            if status or buffer is None or size.value > capacity:
                break
            data = buffer.raw[:size.value]
            offset, stride = _TcpTable.rows.offset, ctypes.sizeof(_TcpRow)
            if len(data) < offset:
                break
            count = struct.unpack_from("<I", data)[0]
            if count > (len(data) - offset) // stride:
                break
            expected = (int.from_bytes(socket.inet_aton(client[0]), "little"), client[1],
                        int.from_bytes(socket.inet_aton(server[0]), "little"), server[1])
            found = []
            for index in range(count):
                row = _TcpRow.from_buffer_copy(data, offset + index * stride)
                actual = (row.local_addr, socket.ntohs(row.local_port & 0xFFFF),
                          row.remote_addr, socket.ntohs(row.remote_port & 0xFFFF))
                if actual == expected and row.state == 5 and row.pid > 0 and row.created > 0:
                    found.append((int(row.pid), int(row.created)))
            if len(found) == 1:
                return found[0]
            break
        raise LocalAccessError("无法验证本机连接的所属用户，请从同一 Windows 登录会话重新打开工作台")

    def identity(self, handle) -> tuple[bytes, int, int]:
        token = wt.HANDLE()
        if not self.advapi.OpenProcessToken(handle, 0x0008, ctypes.byref(token)):
            raise LocalAccessError("无法确认本机客户端身份，已拒绝访问")
        try:
            def query(kind):
                length = wt.DWORD()
                self.advapi.GetTokenInformation(token, kind, None, 0, ctypes.byref(length))
                if not 1 <= length.value <= 65536:
                    raise LocalAccessError("本机客户端令牌无效")
                buffer = ctypes.create_string_buffer(length.value)
                if not self.advapi.GetTokenInformation(token, kind, buffer, length.value, ctypes.byref(length)):
                    raise LocalAccessError("无法确认本机客户端令牌")
                return buffer
            user = query(1)
            sid_pointer = ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0]
            sid_size = self.advapi.GetLengthSid(sid_pointer)
            if not 8 <= sid_size <= 256:
                raise LocalAccessError("本机客户端用户标识无效")
            sid = ctypes.string_at(sid_pointer, sid_size)
            session = struct.unpack_from("<I", query(12).raw)[0]
            creation, exit_time, kernel_time, user_time = (wt.FILETIME() for _ in range(4))
            code = wt.DWORD()
            if (not self.kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                                ctypes.byref(kernel_time), ctypes.byref(user_time))
                    or not self.kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259):
                raise LocalAccessError("本机客户端进程已退出，请重新打开工作台")
            return sid, session, (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        finally:
            self.kernel.CloseHandle(token)


def require_windows_peer(connection, *, api=None) -> None:
    """Fail closed; inspect only the actual connected socket, never headers."""
    try:
        client, server = connection.getpeername(), connection.getsockname()
        if (len(client) != 2 or len(server) != 2 or client[0] != "127.0.0.1" or server[0] != "127.0.0.1"
                or not 1 <= client[1] <= 65535 or not 1 <= server[1] <= 65535):
            raise LocalAccessError("工作台仅接受同用户的本机连接")
        api = api or _WindowsPeerAPI()
        owner = api.connection_owner(client, server)
        handle = api.kernel.OpenProcess(0x1000, False, owner[0])  # QUERY_LIMITED_INFORMATION only
        if not handle:
            raise LocalAccessError("无法只读确认本机客户端所属用户，已拒绝访问")
        try:
            current = api.identity(api.kernel.GetCurrentProcess())
            selected = api.identity(handle)
            if selected[:2] != current[:2] or not 0 < selected[2] <= owner[1]:
                raise LocalAccessError("请从运行工作台的同一 Windows 用户和登录会话访问")
            # Context time prevents PID-reuse confusion; re-query while the
            # exact process handle remains open to catch closed/replaced links.
            if api.connection_owner(client, server) != owner or api.identity(handle) != selected:
                raise LocalAccessError("本机客户端连接身份变化，已拒绝访问")
        finally:
            api.kernel.CloseHandle(handle)
    except LocalAccessError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, struct.error) as error:
        raise LocalAccessError("无法验证本机客户端身份，已拒绝访问") from error


def require_local_user(connection) -> None:
    # Windows uses the native TCP table and process token. macOS uses the
    # system lsof/libproc view of this one established loopback connection to
    # prove that the caller belongs to the same local user. Other POSIX
    # platforms remain closed until an equivalent check is implemented.
    if os.name == "nt":
        require_windows_peer(connection)
    elif sys.platform == "darwin":
        require_macos_peer(connection)


class _MacPeerAPI:
    """Read-only mapping from one loopback TCP tuple to its owning process.

    macOS does not expose Windows' TCP-owner table to Python, but its native
    ``lsof`` build is backed by libproc and reports the PID/UID for an
    established socket. We query only the exact client port and repeat the
    lookup around identity checks so a reused PID or changed connection fails
    closed. No process command line, files or memory are read.
    """

    LSOF = "/usr/sbin/lsof"

    @staticmethod
    def _records(output: str) -> list[tuple[int, int]]:
        records: list[tuple[int, int]] = []
        current: dict[str, str] = {}
        for line in output.splitlines():
            if line.startswith("p") and current:
                try:
                    records.append((int(current["p"]), int(current["u"])))
                except (KeyError, ValueError):
                    pass
                current = {}
            if not line:
                continue
            marker, value = line[0], line[1:]
            if marker in {"p", "u"}:
                current[marker] = value
        if current:
            try:
                records.append((int(current["p"]), int(current["u"])))
            except (KeyError, ValueError):
                pass
        return records

    def connection_owner(self, client: tuple, server: tuple) -> tuple[int, int, int]:
        if (len(client) != 2 or len(server) != 2 or client[0] != "127.0.0.1"
                or server[0] != "127.0.0.1" or not 1 <= client[1] <= 65535
                or not 1 <= server[1] <= 65535):
            raise LocalAccessError("工作台仅接受同用户的本机连接")
        try:
            result = subprocess.run(
                [self.LSOF, "-nP", "-a", f"-iTCP@{client[0]}:{client[1]}",
                 "-sTCP:ESTABLISHED", "-FpcuT"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=2, check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise LocalAccessError("无法验证本机连接所属用户") from error
        if result.returncode != 0:
            raise LocalAccessError("无法验证本机连接所属用户")
        records = set(self._records(result.stdout))
        external = {(pid, uid) for pid, uid in records if pid != os.getpid()}
        # Unit/in-process clients legitimately share the server PID. A real
        # WebView has its own process and therefore must be the sole external
        # owner of the exact client port.
        selected = external or ({(os.getpid(), os.geteuid())} if (os.getpid(), os.geteuid()) in records else set())
        if len(selected) != 1:
            raise LocalAccessError("本机连接身份不唯一，已拒绝访问")
        pid, uid = selected.pop()
        return pid, uid, self.process_start(pid)

    @staticmethod
    def process_start(pid: int) -> int:
        try:
            result = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "lstart="],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=1, check=False,
            )
            value = result.stdout.strip()
            if result.returncode != 0 or not value:
                raise ValueError("missing process start")
            return int(datetime.strptime(value, "%a %b %d %H:%M:%S %Y").timestamp())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise LocalAccessError("无法确认本机客户端进程") from error

    @staticmethod
    def identity(pid: int) -> tuple[int, int]:
        try:
            result = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "uid=,lstart="],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=1, check=False,
            )
            fields = result.stdout.strip().split(None, 1)
            if result.returncode != 0 or len(fields) != 2:
                raise ValueError("missing process identity")
            return int(fields[0]), int(datetime.strptime(fields[1], "%a %b %d %H:%M:%S %Y").timestamp())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise LocalAccessError("无法确认本机客户端身份") from error


def require_macos_peer(connection, *, api=None) -> None:
    """Allow only a same-UID macOS process owning this exact loopback socket."""
    try:
        client, server = connection.getpeername(), connection.getsockname()
        if (len(client) != 2 or len(server) != 2 or client[0] != "127.0.0.1"
                or server[0] != "127.0.0.1" or not 1 <= client[1] <= 65535
                or not 1 <= server[1] <= 65535):
            raise LocalAccessError("工作台仅接受同用户的本机连接")
        api = api or _MacPeerAPI()
        owner = api.connection_owner(client, server)
        current_uid, _ = api.identity(os.getpid())
        selected_uid, selected_start = api.identity(owner[0])
        if selected_uid != current_uid or selected_uid != owner[1] or selected_start != owner[2]:
            raise LocalAccessError("请从运行工作台的同一 macOS 用户访问")
        if api.connection_owner(client, server) != owner:
            raise LocalAccessError("本机连接身份变化，已拒绝访问")
    except LocalAccessError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, struct.error) as error:
        raise LocalAccessError("无法验证本机客户端身份，已拒绝访问") from error


def wechat_http_supported() -> bool:
    # macOS has the same-UID loopback check above. Automatic key capture still
    # remains Windows-only; Mac users provide a key for a selected database.
    return ((os.name == "nt" or sys.platform == "darwin")
            and ctypes.sizeof(ctypes.c_void_p) == 8)
