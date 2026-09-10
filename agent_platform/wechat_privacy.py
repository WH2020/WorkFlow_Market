"""Fail-closed private-directory creation for local WeChat working copies.

The caller must provide an absolute path whose parent already exists.  This
module deliberately creates only the final path component: recursively
changing an application or user directory's permissions would be surprising
and can break unrelated software.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import stat
from typing import NoReturn


_ERROR_CODE = "UNSAFE_PATH"
_ERROR_MESSAGE = "微信资料目录不安全或无法设置私有权限"


class _PrivacyFailure(RuntimeError):
    """An internal error whose details must not cross the API boundary."""


def _fail() -> NoReturn:
    raise _PrivacyFailure()


def _public_error() -> Exception:
    # Lazy import keeps this module safe when wechat_store imports the helper.
    from .wechat_store import WechatStoreError

    return WechatStoreError(_ERROR_CODE, _ERROR_MESSAGE)


def _normalise_path(path: os.PathLike[str] | str) -> Path:
    try:
        raw = os.fspath(path)
    except TypeError:
        _fail()
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        _fail()

    candidate = Path(raw)
    if not candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        _fail()

    if os.name == "nt":
        anchor = candidate.anchor
        # Network shares, device paths, alternate data streams and Win32 name
        # aliases are outside this local-directory security contract.
        if not candidate.drive or anchor.startswith("\\\\"):
            _fail()
        for index, part in enumerate(candidate.parts):
            if index == 0:
                continue
            if not part or ":" in part or part.endswith((" ", ".")):
                _fail()

    normalised = Path(os.path.abspath(raw))
    if normalised == Path(normalised.anchor):
        _fail()
    return normalised


def ensure_private_directory(path: os.PathLike[str] | str) -> Path:
    """Create or repair one private directory and return its absolute path.

    The immediate parent must exist and no ancestor may grant an untrusted
    identity deletion or ACL-takeover rights.  Merely allowing creation of a
    differently named child is not treated as replacement authority. Existing
    directories must belong to the current user. Windows grants only the
    current user and LOCAL SYSTEM full access with a protected DACL; POSIX uses
    owner-only mode 0700.
    """

    try:
        target = _normalise_path(path)
        if os.name == "nt":
            _windows_ensure(target)
        else:
            _posix_ensure(target)
        return target
    except Exception as error:
        # Never expose a native error, path, account name or SID to an API
        # caller.  Preserve only an already-sanitised public error.
        from .wechat_store import WechatStoreError

        if isinstance(error, WechatStoreError):
            raise
        raise _public_error() from None


def verify_private_directory(path: os.PathLike[str] | str) -> Path:
    """Fail unless *path* currently satisfies the private-directory policy."""

    try:
        target = _normalise_path(path)
        if os.name == "nt":
            _windows_verify(target)
        else:
            _posix_verify(target)
        return target
    except Exception as error:
        from .wechat_store import WechatStoreError

        if isinstance(error, WechatStoreError):
            raise
        raise _public_error() from None


def verify_private_file(path: os.PathLike[str] | str) -> Path:
    """Fail unless *path* is a current-user-owned private regular file.

    This is intentionally read-only.  On Windows a newly created file may have
    inherited its two allowed ACEs from a protected private parent, so the file
    security descriptor itself need not carry the ``DACL_PROTECTED`` flag.
    """

    try:
        target = _normalise_path(path)
        if os.name == "nt":
            _windows_verify_file(target)
        else:
            _posix_verify_file(target)
        return target
    except Exception as error:
        from .wechat_store import WechatStoreError

        if isinstance(error, WechatStoreError):
            raise
        raise _public_error() from None


def _posix_open_parent(target: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow or not getattr(os, "O_DIRECTORY", 0):
        _fail()

    current_fd = os.open(target.anchor, flags)
    try:
        _posix_check_parent(os.fstat(current_fd))
        for component in target.parts[1:-1]:
            next_fd = os.open(component, flags | nofollow, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
            _posix_check_parent(os.fstat(current_fd))
        return current_fd, target.name
    except Exception:
        os.close(current_fd)
        raise


def _posix_check_parent(info: os.stat_result) -> None:
    if not stat.S_ISDIR(info.st_mode):
        _fail()
    current_uid = os.geteuid()
    if info.st_uid not in (0, current_uid):
        _fail()
    writable_by_others = info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if writable_by_others and not (info.st_mode & stat.S_ISVTX):
        _fail()


def _posix_check_target(info: os.stat_result) -> None:
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        _fail()
    if stat.S_IMODE(info.st_mode) != 0o700:
        _fail()


def _posix_ensure(target: Path) -> None:
    parent_fd, name = _posix_open_parent(target)
    target_fd = -1
    created = False
    flags = (os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY |
             getattr(os, "O_CLOEXEC", 0))
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        target_fd = os.open(name, flags, dir_fd=parent_fd)
        info = os.fstat(target_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            _fail()
        os.fchmod(target_fd, 0o700)
        _posix_check_target(os.fstat(target_fd))
    except Exception:
        if target_fd >= 0:
            os.close(target_fd)
            target_fd = -1
        if created:
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError:
                # The directory was created atomically as 0700, so a residue
                # is not exposed, but cleanup failure must not be swallowed.
                _fail()
        raise
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        os.close(parent_fd)


def _posix_verify(target: Path) -> None:
    parent_fd, name = _posix_open_parent(target)
    target_fd = -1
    flags = (os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY |
             getattr(os, "O_CLOEXEC", 0))
    try:
        target_fd = os.open(name, flags, dir_fd=parent_fd)
        _posix_check_target(os.fstat(target_fd))
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        os.close(parent_fd)


def _posix_verify_file(target: Path) -> None:
    parent_fd, name = _posix_open_parent(target)
    target_fd = -1
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        target_fd = os.open(name, flags, dir_fd=parent_fd)
        info = os.fstat(target_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
            _fail()
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        os.close(parent_fd)


if os.name == "nt":
    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _FILE_ATTRIBUTE_DIRECTORY = 0x10
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _OPEN_EXISTING = 3
    _FILE_SHARE_READ = 0x1
    _FILE_SHARE_WRITE = 0x2
    _READ_CONTROL = 0x00020000
    _WRITE_DAC = 0x00040000
    _DELETE = 0x00010000
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1
    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x00000001
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _SE_DACL_PROTECTED = 0x1000
    _SECURITY_DESCRIPTOR_REVISION = 1
    _ACL_SIZE_INFORMATION_CLASS = 2
    _ACCESS_ALLOWED_ACE_TYPES = frozenset((0, 5, 9, 11))
    _ACCESS_ALLOWED_ACE_TYPE = 0
    _OBJECT_ACE_TYPES = frozenset((5, 11))
    _OBJECT_TYPE_PRESENT = 0x1
    _INHERITED_OBJECT_TYPE_PRESENT = 0x2
    _OBJECT_INHERIT_ACE = 0x1
    _CONTAINER_INHERIT_ACE = 0x2
    _NO_PROPAGATE_INHERIT_ACE = 0x4
    _INHERIT_ONLY_ACE = 0x8
    _INHERITED_ACE = 0x10
    _FILE_ALL_ACCESS = 0x001F01FF
    _TRUSTED_INSTALLER_SID = (
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    )
    _DANGEROUS_PARENT_ACCESS = (
        0x00000040 |  # FILE_DELETE_CHILD
        _DELETE | _WRITE_DAC | 0x00080000 |  # WRITE_OWNER
        0x10000000  # GENERIC_ALL
    )

    class _FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", _FILETIME),
            ("access_time", _FILETIME),
            ("write_time", _FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

    class _TOKEN_USER_VALUE(ctypes.Structure):
        _fields_ = [("user", _SID_AND_ATTRIBUTES)]

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("ace_count", wintypes.DWORD),
            ("bytes_in_use", wintypes.DWORD),
            ("bytes_free", wintypes.DWORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("ace_type", ctypes.c_ubyte),
            ("ace_flags", ctypes.c_ubyte),
            ("ace_size", wintypes.WORD),
        ]

    class _FILE_DISPOSITION_INFO(ctypes.Structure):
        # FILE_DISPOSITION_INFO uses Win32 BOOLEAN (BYTE), not BOOL (LONG).
        _fields_ = [("delete_file", ctypes.c_ubyte)]

    _kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.HANDLE)]
    _advapi.OpenProcessToken.restype = wintypes.BOOL
    _advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    _advapi.GetTokenInformation.restype = wintypes.BOOL
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    _kernel.CreateDirectoryW.restype = wintypes.BOOL
    _kernel.RemoveDirectoryW.argtypes = [wintypes.LPCWSTR]
    _kernel.RemoveDirectoryW.restype = wintypes.BOOL
    _kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE,
                                                    ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
    _kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD]
    _kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    _advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi.GetSecurityInfo.restype = wintypes.DWORD
    _advapi.SetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                                        ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p]
    _advapi.SetSecurityInfo.restype = wintypes.DWORD
    _advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p,
                                                   ctypes.POINTER(wintypes.BOOL),
                                                   ctypes.POINTER(ctypes.c_void_p),
                                                   ctypes.POINTER(wintypes.BOOL)]
    _advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    _advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p,
                                                      ctypes.POINTER(wintypes.WORD),
                                                      ctypes.POINTER(wintypes.DWORD)]
    _advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _advapi.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.c_int]
    _advapi.GetAclInformation.restype = wintypes.BOOL
    _advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(ctypes.c_void_p)]
    _advapi.GetAce.restype = wintypes.BOOL
    _advapi.IsValidSid.argtypes = [ctypes.c_void_p]
    _advapi.IsValidSid.restype = wintypes.BOOL
    _advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
    _advapi.GetLengthSid.restype = wintypes.DWORD
    _advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_void_p)]
    _advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR,
                                               ctypes.POINTER(ctypes.c_void_p)]
    _advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _advapi.CreateWellKnownSid.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    _advapi.CreateWellKnownSid.restype = wintypes.BOOL
    _kernel.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel.LocalFree.restype = ctypes.c_void_p


def _win_close(handle: int | None) -> None:
    if os.name == "nt" and handle not in (None, _INVALID_HANDLE_VALUE):
        _kernel.CloseHandle(handle)


def _win_open_directory(path: Path, access: int) -> int:
    handle = _kernel.CreateFileW(
        str(path), access, _FILE_SHARE_READ | _FILE_SHARE_WRITE, None,
        _OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle in (None, _INVALID_HANDLE_VALUE):
        _fail()
    info = _BY_HANDLE_FILE_INFORMATION()
    if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        _win_close(handle)
        _fail()
    if not (info.attributes & _FILE_ATTRIBUTE_DIRECTORY) or (
            info.attributes & _FILE_ATTRIBUTE_REPARSE_POINT):
        _win_close(handle)
        _fail()
    return handle


def _win_open_file(path: Path, access: int) -> int:
    handle = _kernel.CreateFileW(
        str(path), access, _FILE_SHARE_READ | _FILE_SHARE_WRITE, None,
        _OPEN_EXISTING, _FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if handle in (None, _INVALID_HANDLE_VALUE):
        _fail()
    info = _BY_HANDLE_FILE_INFORMATION()
    if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        _win_close(handle)
        _fail()
    if (info.attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT) or
            info.links != 1):
        _win_close(handle)
        _fail()
    return handle


def _win_sid_bytes(sid: int | ctypes.c_void_p) -> bytes:
    pointer = sid if isinstance(sid, int) else sid.value
    if not pointer or not _advapi.IsValidSid(pointer):
        _fail()
    length = _advapi.GetLengthSid(pointer)
    if not length:
        _fail()
    return ctypes.string_at(pointer, length)


def _win_current_sid() -> bytes:
    token = wintypes.HANDLE()
    if not _advapi.OpenProcessToken(_kernel.GetCurrentProcess(), _TOKEN_QUERY,
                                    ctypes.byref(token)):
        _fail()
    try:
        required = wintypes.DWORD()
        _advapi.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(required))
        if not required.value:
            _fail()
        buffer = ctypes.create_string_buffer(required.value)
        if not _advapi.GetTokenInformation(token, _TOKEN_USER, buffer,
                                           required.value, ctypes.byref(required)):
            _fail()
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER_VALUE)).contents
        return _win_sid_bytes(token_user.user.sid)
    finally:
        _win_close(token.value)


def _win_well_known_sid(kind: int) -> bytes:
    required = wintypes.DWORD(68)
    buffer = ctypes.create_string_buffer(required.value)
    if not _advapi.CreateWellKnownSid(kind, None, buffer, ctypes.byref(required)):
        _fail()
    return _win_sid_bytes(ctypes.addressof(buffer))


def _win_sid_text(sid: bytes) -> str:
    buffer = ctypes.create_string_buffer(sid)
    text_pointer = ctypes.c_void_p()
    if not _advapi.ConvertSidToStringSidW(ctypes.addressof(buffer),
                                         ctypes.byref(text_pointer)):
        _fail()
    try:
        value = ctypes.cast(text_pointer, wintypes.LPWSTR).value
        if not value:
            _fail()
        return value
    finally:
        _kernel.LocalFree(text_pointer)


def _win_sid_from_text(value: str) -> bytes:
    sid_pointer = ctypes.c_void_p()
    if not _advapi.ConvertStringSidToSidW(value, ctypes.byref(sid_pointer)):
        _fail()
    try:
        return _win_sid_bytes(sid_pointer)
    finally:
        _kernel.LocalFree(sid_pointer)


def _win_aces(dacl: int) -> list[tuple[int, int, int, bytes]]:
    information = _ACL_SIZE_INFORMATION()
    if not _advapi.GetAclInformation(dacl, ctypes.byref(information),
                                     ctypes.sizeof(information),
                                     _ACL_SIZE_INFORMATION_CLASS):
        _fail()
    result: list[tuple[int, int, int, bytes]] = []
    for index in range(information.ace_count):
        ace_pointer = ctypes.c_void_p()
        if not _advapi.GetAce(dacl, index, ctypes.byref(ace_pointer)):
            _fail()
        address = ace_pointer.value
        if not address:
            _fail()
        header = ctypes.cast(address, ctypes.POINTER(_ACE_HEADER)).contents
        if header.ace_size < 8:
            _fail()
        mask = ctypes.c_uint32.from_address(address + 4).value
        sid = b""
        if header.ace_type in _ACCESS_ALLOWED_ACE_TYPES:
            sid_offset = 8
            if header.ace_type in _OBJECT_ACE_TYPES:
                if header.ace_size < 12:
                    _fail()
                object_flags = ctypes.c_uint32.from_address(address + 8).value
                sid_offset = 12
                if object_flags & _OBJECT_TYPE_PRESENT:
                    sid_offset += 16
                if object_flags & _INHERITED_OBJECT_TYPE_PRESENT:
                    sid_offset += 16
            if sid_offset >= header.ace_size:
                _fail()
            sid_pointer = address + sid_offset
            sid = _win_sid_bytes(sid_pointer)
            if sid_offset + len(sid) > header.ace_size:
                _fail()
        result.append((header.ace_type, header.ace_flags, mask, sid))
    return result


def _win_security_snapshot(handle: int) -> tuple[bytes, bool, list[tuple[int, int, int, bytes]]]:
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = _advapi.GetSecurityInfo(
        handle, _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner), None, ctypes.byref(dacl), None,
        ctypes.byref(descriptor),
    )
    if status != 0 or not descriptor.value or not dacl.value:
        if descriptor.value:
            _kernel.LocalFree(descriptor)
        _fail()
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not _advapi.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)):
            _fail()
        return (_win_sid_bytes(owner), bool(control.value & _SE_DACL_PROTECTED),
                _win_aces(dacl.value))
    finally:
        _kernel.LocalFree(descriptor)


def _win_check_parent(handle: int, current_sid: bytes, *, inheritance_source: bool = False) -> None:
    system_sid = _win_well_known_sid(22)  # WinLocalSystemSid
    administrators_sid = _win_well_known_sid(26)  # WinBuiltinAdministratorsSid
    trusted_installer_sid = _win_sid_from_text(_TRUSTED_INSTALLER_SID)
    trusted = {current_sid, system_sid, administrators_sid, trusted_installer_sid}
    owner, _protected, aces = _win_security_snapshot(handle)
    if owner not in trusted:
        _fail()
    for ace_type, ace_flags, mask, sid in aces:
        if ace_type in _ACCESS_ALLOWED_ACE_TYPES and sid not in trusted:
            if (not (ace_flags & _INHERIT_ONLY_ACE) and
                    mask & _DANGEROUS_PARENT_ACCESS):
                _fail()
            # A create-only ACE on a parent cannot replace an existing child.
            # For the immediate creation parent, however, any untrusted ACE
            # that applies to child directories can be inherited during the
            # short create/harden interval and used to retain an open handle.
            if inheritance_source and ace_flags & _CONTAINER_INHERIT_ACE and mask:
                _fail()


def _win_check_target(handle: int, current_sid: bytes) -> None:
    system_sid = _win_well_known_sid(22)
    owner, protected, aces = _win_security_snapshot(handle)
    if owner != current_sid or not protected or len(aces) != 2:
        _fail()
    expected = {current_sid, system_sid}
    seen: set[bytes] = set()
    inheritance_mask = (_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE |
                        _NO_PROPAGATE_INHERIT_ACE | _INHERIT_ONLY_ACE |
                        _INHERITED_ACE)
    for ace_type, ace_flags, mask, sid in aces:
        if (ace_type != _ACCESS_ALLOWED_ACE_TYPE or mask != _FILE_ALL_ACCESS or
                ace_flags & inheritance_mask !=
                (_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE) or
                sid not in expected or sid in seen):
            _fail()
        seen.add(sid)
    if seen != expected:
        _fail()


def _win_check_file(handle: int, current_sid: bytes) -> None:
    system_sid = _win_well_known_sid(22)
    owner, _protected, aces = _win_security_snapshot(handle)
    if owner != current_sid or len(aces) != 2:
        _fail()
    expected = {current_sid, system_sid}
    seen: set[bytes] = set()
    forbidden_flags = (_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE |
                       _NO_PROPAGATE_INHERIT_ACE | _INHERIT_ONLY_ACE)
    for ace_type, ace_flags, mask, sid in aces:
        # Inherited file ACEs normally retain only INHERITED_ACE.  Explicit
        # current/SYSTEM ACEs are equally private, but directory inheritance
        # flags on a file indicate a descriptor not produced by this policy.
        if (ace_type != _ACCESS_ALLOWED_ACE_TYPE or mask != _FILE_ALL_ACCESS or
                ace_flags & forbidden_flags or sid not in expected or sid in seen):
            _fail()
        seen.add(sid)
    if seen != expected:
        _fail()


def _win_set_private_dacl(handle: int, current_sid: bytes) -> None:
    sid_text = _win_sid_text(current_sid)
    # D:P makes inheritance protection part of the descriptor itself.  The
    # two OI/CI ACEs protect both this directory and descendants.
    sddl = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid_text})"
    descriptor = ctypes.c_void_p()
    if not _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SECURITY_DESCRIPTOR_REVISION, ctypes.byref(descriptor), None):
        _fail()
    try:
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not _advapi.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl),
                ctypes.byref(defaulted)) or not present.value or not dacl.value:
            _fail()
        status = _advapi.SetSecurityInfo(
            handle, _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, dacl, None,
        )
        if status != 0:
            _fail()
    finally:
        _kernel.LocalFree(descriptor)


def _win_parent_paths(target: Path) -> list[Path]:
    parent = target.parent
    anchor = Path(parent.anchor)
    if parent == anchor:
        return [parent]
    # The volume root controls replacement of the first path component via
    # FILE_DELETE_CHILD, so it is part of the security boundary too.
    result: list[Path] = [anchor]
    current = anchor
    for component in parent.parts[1:]:
        current = current / component
        result.append(current)
    return result


def _win_open_safe_parents(target: Path, current_sid: bytes, *, for_ensure: bool = False) -> list[int]:
    handles: list[int] = []
    try:
        paths = _win_parent_paths(target)
        for index, parent in enumerate(paths):
            handle = _win_open_directory(parent, _READ_CONTROL)
            handles.append(handle)
            _win_check_parent(
                handle, current_sid,
                inheritance_source=for_ensure and index == len(paths) - 1,
            )
        return handles
    except Exception:
        for handle in reversed(handles):
            _win_close(handle)
        raise


def _win_mark_created_for_deletion(handle: int) -> bool:
    disposition = _FILE_DISPOSITION_INFO(True)
    return bool(_kernel.SetFileInformationByHandle(
        handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition),
    ))


def _windows_ensure(target: Path) -> None:
    current_sid = _win_current_sid()
    parents = _win_open_safe_parents(target, current_sid, for_ensure=True)
    handle: int | None = None
    created = False
    try:
        if _kernel.CreateDirectoryW(str(target), None):
            created = True
        elif ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            _fail()
        access = _READ_CONTROL | _WRITE_DAC | (_DELETE if created else 0)
        handle = _win_open_directory(target, access)
        owner, _protected, _aces = _win_security_snapshot(handle)
        if owner != current_sid:
            _fail()
        _win_set_private_dacl(handle, current_sid)
        _win_check_target(handle, current_sid)
    except Exception:
        if created and handle not in (None, _INVALID_HANDLE_VALUE):
            if not _win_mark_created_for_deletion(handle):
                # RemoveDirectoryW cannot run while our non-share-delete handle
                # is open.  Close it only after all ancestors have been locked
                # and the target was verified as a non-reparse directory.
                _win_close(handle)
                handle = None
                if not _kernel.RemoveDirectoryW(str(target)):
                    # Never conceal a rollback failure as a successful ensure.
                    # The creation parent was checked before mkdir to prevent
                    # untrusted inheritable ACEs, so a residual empty directory
                    # is not accepted by this API and did not silently expose
                    # contents, but it may require caller-owned cleanup.
                    _fail()
        elif created:
            if not _kernel.RemoveDirectoryW(str(target)):
                _fail()
        raise
    finally:
        _win_close(handle)
        for parent_handle in reversed(parents):
            _win_close(parent_handle)


def _windows_verify(target: Path) -> None:
    current_sid = _win_current_sid()
    parents = _win_open_safe_parents(target, current_sid)
    handle: int | None = None
    try:
        handle = _win_open_directory(target, _READ_CONTROL)
        _win_check_target(handle, current_sid)
    finally:
        _win_close(handle)
        for parent_handle in reversed(parents):
            _win_close(parent_handle)


def _windows_verify_file(target: Path) -> None:
    current_sid = _win_current_sid()
    parents = _win_open_safe_parents(target, current_sid)
    handle: int | None = None
    try:
        handle = _win_open_file(target, _READ_CONTROL)
        _win_check_file(handle, current_sid)
    finally:
        _win_close(handle)
        for parent_handle in reversed(parents):
            _win_close(parent_handle)
