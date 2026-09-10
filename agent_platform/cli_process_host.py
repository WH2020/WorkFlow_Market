"""Constrained process host for the Agent4Market CLI adapters.

The TypeScript caller supplies a frozen native command in ``A4M_CLI_LAUNCH``
and a JSON output schema in ``A4M_CLI_SCHEMA``.  This host deliberately does
not read or proxy stdin/stdout: the CLI receives the caller's stdin handle and
writes to its stdout handle directly.

On Windows the CLI is created suspended and assigned to a kill-on-close Job
Object before it is allowed to run.  Consequently, terminating this host also
terminates descendants of the CLI.  On POSIX the child remains in the process
group established by the caller, which owns group termination.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time
from types import ModuleType
from typing import NoReturn


_LAUNCH_ENV = "A4M_CLI_LAUNCH"
_SCHEMA_ENV = "A4M_CLI_SCHEMA"
_MODEL_CATALOG_ENV = "A4M_CLI_MODEL_CATALOG"
_SCHEMA_PLACEHOLDER = "__A4M_CLI_OUTPUT_SCHEMA__"
_MODEL_CATALOG_PLACEHOLDER = "__A4M_CLI_MODEL_CATALOG__"
_MODEL_CATALOG_ARGUMENT = (
    f'model_catalog_json="{_MODEL_CATALOG_PLACEHOLDER}"'
)
_RUNTIME_DIRECTORY = "Agent4MarketCli"
_REQUEST_PREFIX = "request-"
_REQUEST_MARKER = ".a4m-request"
_MARKER_CONTENT = b"A4M_CLI_REQUEST_V1\n"
_SCHEMA_NAME = "schema.json"
_MODEL_CATALOG_NAME = "models.json"
_STALE_AFTER_SECONDS = 24 * 60 * 60
_MAX_STALE_SCAN = 256
_MAX_LAUNCH_JSON = 128 * 1024
_MAX_SCHEMA_JSON = 1024 * 1024
_MAX_MODEL_CATALOG_JSON = 1024 * 1024
_MAX_ARGUMENTS = 256
_MAX_ARGUMENT_LENGTH = 64 * 1024

_ERROR_CONFIG = "A4M_CLI_HOST_INVALID_CONFIG"
_ERROR_PRIVATE = "A4M_CLI_HOST_PRIVATE_PATH"
_ERROR_LAUNCH = "A4M_CLI_HOST_LAUNCH_FAILED"
_ERROR_INTERNAL = "A4M_CLI_HOST_INTERNAL_ERROR"
_ERROR_EXIT_STATUS = 70

CATALOG_QUERY_ARGS = ["app-server", "--stdio", "--strict-config", "-c", "project_doc_max_bytes=0",
                      "-c", "skills.include_instructions=false"]
for _feature in ("shell_tool", "browser_use", "browser_use_external", "code_mode", "code_mode_host", "computer_use",
                 "image_generation", "apps", "plugins", "multi_agent", "multi_agent_v2", "view_image", "memories",
                 "hooks", "workspace_dependencies", "shell_snapshot", "goals", "skill_search", "tool_suggest",
                 "unbounded_connection_retries"):
    CATALOG_QUERY_ARGS.extend(["--disable", _feature])


class _HostError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise _HostError(code)


def _strict_json(text: str) -> object:
    def reject_constant(_value: str) -> NoReturn:
        _fail(_ERROR_CONFIG)

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                _fail(_ERROR_CONFIG)
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except _HostError:
        raise
    except (TypeError, ValueError, RecursionError):
        _fail(_ERROR_CONFIG)


def _read_configuration() -> tuple[list[str], object, object | None, dict[str, str]]:
    launch_text = os.environ.pop(_LAUNCH_ENV, None)
    schema_text = os.environ.pop(_SCHEMA_ENV, None)
    model_catalog_text = os.environ.pop(_MODEL_CATALOG_ENV, None)
    child_environment = os.environ.copy()
    if (
        launch_text is None
        or schema_text is None
        or len(launch_text) > _MAX_LAUNCH_JSON
        or len(schema_text) > _MAX_SCHEMA_JSON
        or (
            model_catalog_text is not None
            and len(model_catalog_text) > _MAX_MODEL_CATALOG_JSON
        )
    ):
        _fail(_ERROR_CONFIG)

    launch = _strict_json(launch_text)
    schema = _strict_json(schema_text)
    model_catalog = (
        None if model_catalog_text is None else _strict_json(model_catalog_text)
    )
    if (
        not isinstance(launch, list)
        or not launch
        or len(launch) > _MAX_ARGUMENTS
        or not isinstance(schema, dict)
    ):
        _fail(_ERROR_CONFIG)

    if model_catalog is not None:
        if not isinstance(model_catalog, dict):
            _fail(_ERROR_CONFIG)
        models = model_catalog.get("models")
        if not isinstance(models, list) or not models:
            _fail(_ERROR_CONFIG)
        for model in models:
            if not isinstance(model, dict):
                _fail(_ERROR_CONFIG)
            for required in ("slug", "display_name"):
                value = model.get(required)
                if not isinstance(value, str) or not value or "\x00" in value:
                    _fail(_ERROR_CONFIG)

    arguments: list[str] = []
    schema_placeholder_count = 0
    catalog_placeholder_count = 0
    for index, value in enumerate(launch):
        if (
            not isinstance(value, str)
            or "\x00" in value
            or len(value) > _MAX_ARGUMENT_LENGTH
            or (index == 0 and not value)
        ):
            _fail(_ERROR_CONFIG)
        if value == _SCHEMA_PLACEHOLDER:
            schema_placeholder_count += 1
        elif _SCHEMA_PLACEHOLDER in value:
            # Requiring a complete argv token avoids accidental partial path
            # substitution inside an unrelated option or prompt fragment.
            _fail(_ERROR_CONFIG)
        if value == _MODEL_CATALOG_ARGUMENT:
            catalog_placeholder_count += 1
        elif _MODEL_CATALOG_PLACEHOLDER in value:
            _fail(_ERROR_CONFIG)
        arguments.append(value)
    if schema_placeholder_count > 1:
        _fail(_ERROR_CONFIG)
    if model_catalog is None and catalog_placeholder_count != 0:
        _fail(_ERROR_CONFIG)
    if model_catalog is not None and catalog_placeholder_count != 1:
        _fail(_ERROR_CONFIG)

    executable = Path(arguments[0])
    if not executable.is_absolute():
        _fail(_ERROR_CONFIG)
    try:
        info = executable.lstat()
    except OSError:
        _fail(_ERROR_CONFIG)
    if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
        _fail(_ERROR_CONFIG)
    if os.name == "nt":
        if executable.suffix.casefold() != ".exe":
            _fail(_ERROR_CONFIG)
    elif not os.access(executable, os.X_OK):
        _fail(_ERROR_CONFIG)

    arguments[0] = str(executable)
    return arguments, schema, model_catalog, child_environment


def _load_privacy_helper() -> ModuleType:
    helper_path = Path(__file__).resolve().with_name("wechat_privacy.py")
    try:
        info = helper_path.lstat()
        if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
            _fail(_ERROR_PRIVATE)
        spec = importlib.util.spec_from_file_location(
            "_a4m_cli_wechat_privacy", helper_path
        )
        if spec is None or spec.loader is None:
            _fail(_ERROR_PRIVATE)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        if not callable(getattr(helper, "ensure_private_directory", None)):
            _fail(_ERROR_PRIVATE)
        if not callable(getattr(helper, "verify_private_directory", None)):
            _fail(_ERROR_PRIVATE)
        if not callable(getattr(helper, "verify_private_file", None)):
            _fail(_ERROR_PRIVATE)
        return helper
    except _HostError:
        raise
    except Exception:
        _fail(_ERROR_PRIVATE)


def _runtime_paths() -> list[Path]:
    if os.name == "nt":
        base_text = os.environ.get("LOCALAPPDATA")
        if not base_text or "\x00" in base_text:
            _fail(_ERROR_PRIVATE)
        base = Path(base_text)
        if not base.is_absolute():
            _fail(_ERROR_PRIVATE)
        candidates = [Path(os.path.abspath(base)) / _RUNTIME_DIRECTORY]
        profile_text = os.environ.get("USERPROFILE")
        if profile_text and "\x00" not in profile_text:
            profile = Path(profile_text)
            if not profile.is_absolute():
                _fail(_ERROR_PRIVATE)
            fallback = Path(os.path.abspath(profile)) / _RUNTIME_DIRECTORY
            if fallback != candidates[0]:
                candidates.append(fallback)
        return candidates
    else:
        try:
            base = Path.home()
        except (OSError, RuntimeError):
            _fail(_ERROR_PRIVATE)
    if not base.is_absolute():
        _fail(_ERROR_PRIVATE)
    return [Path(os.path.abspath(base)) / _RUNTIME_DIRECTORY]


def _runtime_path() -> Path:
    """Return the preferred runtime location (kept small for unit probes)."""

    return _runtime_paths()[0]


def _prepare_runtime(helper: ModuleType) -> Path:
    targets = _runtime_paths()
    for index, target in enumerate(targets):
        if os.path.lexists(target):
            # Existing paths are verification-only.  In particular, do not
            # turn a pre-existing weak directory into a trusted secret store.
            try:
                helper.verify_private_directory(target)
                return target
            except Exception:
                _fail(_ERROR_PRIVATE)
        try:
            if os.name == "nt":
                _windows_create_private_root(target, helper)
            else:
                helper.ensure_private_directory(target)
            helper.verify_private_directory(target)
            return target
        except Exception:
            # Another host may have won the create race.  Accept only its
            # already-complete private directory; never call the repairing API.
            if os.path.lexists(target):
                try:
                    helper.verify_private_directory(target)
                    return target
                except Exception:
                    _fail(_ERROR_PRIVATE)
            if index + 1 >= len(targets):
                _fail(_ERROR_PRIVATE)
            # The only Windows fallback is the same current user's profile.
            # It is reached solely when the preferred path does not exist and
            # LocalAppData's ancestor policy prevents an atomic safe create.
            continue
    _fail(_ERROR_PRIVATE)


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


def _windows_create_private_root(target: Path, helper: ModuleType) -> None:
    """Create the new LocalAppData root with its final DACL atomically.

    LocalAppData commonly has benign inheritable AppContainer read ACEs.  The
    generic helper rejects creating beneath such a parent because its normal
    create-then-harden sequence would briefly inherit them.  Supplying the
    final protected DACL to CreateDirectoryW removes that interval.  Parent
    handles are still held and checked against replacement/ACL-takeover rights
    for the entire operation.
    """

    parents: list[int] = []
    descriptor = ctypes.c_void_p()
    created = False
    try:
        current_sid = helper._win_current_sid()
        parents = helper._win_open_safe_parents(target, current_sid)
        sid_text = helper._win_sid_text(current_sid)
        sddl = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid_text})"
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        kernel.CreateDirectoryW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
        ]
        kernel.CreateDirectoryW.restype = wintypes.BOOL
        kernel.RemoveDirectoryW.argtypes = [wintypes.LPCWSTR]
        kernel.RemoveDirectoryW.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            _fail(_ERROR_PRIVATE)
        attributes = _SECURITY_ATTRIBUTES(
            ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
        )
        if not kernel.CreateDirectoryW(str(target), ctypes.byref(attributes)):
            _fail(_ERROR_PRIVATE)
        created = True
        handle = helper._win_open_directory(target, helper._READ_CONTROL)
        try:
            helper._win_check_target(handle, current_sid)
        finally:
            helper._win_close(handle)
    except _HostError:
        if created:
            kernel.RemoveDirectoryW(str(target))
        raise
    except Exception:
        if created:
            try:
                kernel.RemoveDirectoryW(str(target))
            except Exception:
                pass
        _fail(_ERROR_PRIVATE)
    finally:
        if descriptor.value:
            try:
                kernel.LocalFree(descriptor)
            except Exception:
                pass
        for parent_handle in reversed(parents):
            try:
                helper._win_close(parent_handle)
            except Exception:
                pass


def _create_request_directory(root: Path, helper: ModuleType) -> Path:
    for _attempt in range(32):
        target = root / f"{_REQUEST_PREFIX}{secrets.token_hex(16)}"
        if os.path.lexists(target):
            continue
        try:
            helper.ensure_private_directory(target)
            helper.verify_private_directory(target)
            return target
        except Exception:
            # A collision is overwhelmingly unlikely.  Any other privacy
            # failure is terminal rather than an invitation to weaken policy.
            _fail(_ERROR_PRIVATE)
    _fail(_ERROR_PRIVATE)


def _open_request_marker(request: Path) -> int:
    path = request / _REQUEST_MARKER
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            written = os.write(descriptor, _MARKER_CONTENT)
            if written != len(_MARKER_CONTENT):
                raise OSError("short marker write")
            os.fsync(descriptor)
            if os.name != "nt":
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except Exception:
            os.close(descriptor)
            raise
    except Exception:
        _fail(_ERROR_PRIVATE)


def _write_private_json(
    request: Path, name: str, value: object, helper: ModuleType, maximum: int
) -> Path:
    path = request / name
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > maximum:
            _fail(_ERROR_CONFIG)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            view = memoryview(encoded)
            while view:
                count = os.write(descriptor, view)
                if count <= 0:
                    raise OSError("short schema write")
                view = view[count:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        helper.verify_private_file(path)
        return path
    except _HostError:
        raise
    except Exception:
        _fail(_ERROR_PRIVATE)


def _write_schema(request: Path, schema: object, helper: ModuleType) -> Path:
    return _write_private_json(
        request, _SCHEMA_NAME, schema, helper, _MAX_SCHEMA_JSON
    )


def _write_model_catalog(
    request: Path, model_catalog: object, helper: ModuleType
) -> Path:
    return _write_private_json(
        request,
        _MODEL_CATALOG_NAME,
        model_catalog,
        helper,
        _MAX_MODEL_CATALOG_JSON,
    )


def _is_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    if os.name == "nt":
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(getattr(info, "st_file_attributes", 0) & reparse)
    return False


def _request_shape_is_owned(request: Path, helper: ModuleType) -> bool:
    try:
        info = request.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            return False
        helper.verify_private_directory(request)
        entries = {entry.name: entry for entry in os.scandir(request)}
        if _REQUEST_MARKER not in entries:
            return False
        if not set(entries).issubset(
            {_REQUEST_MARKER, _SCHEMA_NAME, _MODEL_CATALOG_NAME}
        ):
            return False
        for entry in entries.values():
            entry_info = entry.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(entry_info.st_mode)
                or _is_reparse(entry_info)
                or (os.name != "nt" and entry_info.st_nlink != 1)
            ):
                return False
            if os.name != "nt":
                if entry_info.st_uid != os.geteuid() or stat.S_IMODE(entry_info.st_mode) & 0o077:
                    return False
            else:
                helper.verify_private_file(Path(entry.path))
        if (request / _REQUEST_MARKER).read_bytes() != _MARKER_CONTENT:
            return False
        return True
    except Exception:
        return False


def _try_lock_stale_marker(path: Path) -> tuple[object, bool]:
    """Return a held marker lock, or ``(None, False)`` when it is active."""

    if os.name != "nt":
        import fcntl

        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor, True
        except (OSError, BlockingIOError):
            try:
                os.close(descriptor)  # type: ignore[possibly-undefined]
            except (NameError, OSError):
                pass
            return None, False

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(
        str(path),
        0x80000000,  # GENERIC_READ
        0,  # exclusive: conflicts with the active writer handle
        None,
        3,  # OPEN_EXISTING
        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        return None, False
    return (kernel, handle), True


def _close_stale_lock(lock: object) -> None:
    if lock is None:
        return
    if os.name != "nt":
        try:
            os.close(int(lock))
        except OSError:
            pass
        return
    kernel, handle = lock  # type: ignore[misc]
    kernel.CloseHandle(handle)


def _delete_owned_request(request: Path, helper: ModuleType) -> bool:
    if not _request_shape_is_owned(request, helper):
        return False
    lock, acquired = _try_lock_stale_marker(request / _REQUEST_MARKER)
    if not acquired:
        return False
    try:
        # Delete only the two names validated above.  Unknown files, child
        # directories, links and reparse points make the whole cleanup fail
        # closed, so this never becomes a general recursive-delete primitive.
        schema_path = request / _SCHEMA_NAME
        if os.path.lexists(schema_path):
            schema_info = schema_path.lstat()
            if not stat.S_ISREG(schema_info.st_mode) or _is_reparse(schema_info):
                return False
            os.unlink(schema_path)
        model_catalog_path = request / _MODEL_CATALOG_NAME
        if os.path.lexists(model_catalog_path):
            catalog_info = model_catalog_path.lstat()
            if not stat.S_ISREG(catalog_info.st_mode) or _is_reparse(catalog_info):
                return False
            os.unlink(model_catalog_path)
    except OSError:
        return False
    finally:
        _close_stale_lock(lock)

    try:
        marker_path = request / _REQUEST_MARKER
        marker_info = marker_path.lstat()
        if not stat.S_ISREG(marker_info.st_mode) or _is_reparse(marker_info):
            return False
        os.unlink(marker_path)
        os.rmdir(request)
        return True
    except OSError:
        return False


def _cleanup_stale(root: Path, helper: ModuleType, *, now: float | None = None) -> None:
    cutoff = (time.time() if now is None else now) - _STALE_AFTER_SECONDS
    try:
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= _MAX_STALE_SCAN:
                    break
                if (
                    not entry.name.startswith(_REQUEST_PREFIX)
                    or len(entry.name) != len(_REQUEST_PREFIX) + 32
                    or any(character not in "0123456789abcdef" for character in entry.name[len(_REQUEST_PREFIX):])
                ):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISDIR(info.st_mode)
                        or _is_reparse(info)
                        or info.st_mtime >= cutoff
                    ):
                        continue
                    _delete_owned_request(Path(entry.path), helper)
                except Exception:
                    continue
    except Exception:
        # Stale cleanup is bounded and best-effort.  The current request still
        # gets a fresh private directory even when an old residue is malformed.
        return


def _replace_schema_placeholder(arguments: list[str], schema_path: Path) -> list[str]:
    replacement = str(schema_path)
    return [replacement if argument == _SCHEMA_PLACEHOLDER else argument for argument in arguments]


def _replace_model_catalog_placeholder(
    arguments: list[str], model_catalog_path: Path
) -> list[str]:
    replacement = "model_catalog_json=" + json.dumps(str(model_catalog_path))
    return [
        replacement if argument == _MODEL_CATALOG_ARGUMENT else argument
        for argument in arguments
    ]


def _run_posix(
    arguments: list[str], request: Path, child_environment: dict[str, str]
) -> int:
    try:
        with open(os.devnull, "wb") as null:
            child = subprocess.Popen(
                arguments,
                executable=arguments[0],
                stdin=None,
                stdout=None,
                stderr=null,
                cwd=request,
                env=child_environment,
                close_fds=True,
                start_new_session=False,
            )
            return child.wait()
    except Exception:
        _fail(_ERROR_LAUNCH)


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESTDHANDLES = 0x00000100
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_INFINITE = 0xFFFFFFFF


def _create_kill_job() -> tuple[object, int]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel.SetHandleInformation.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        _fail(_ERROR_LAUNCH)
    limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel.CloseHandle(handle)
        _fail(_ERROR_LAUNCH)
    if not kernel.SetHandleInformation(handle, 0x00000001, 0):  # HANDLE_FLAG_INHERIT
        kernel.CloseHandle(handle)
        _fail(_ERROR_LAUNCH)
    return kernel, int(handle)


def _configure_process_api(kernel: object) -> None:
    kernel.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel.DeleteProcThreadAttributeList.restype = None
    kernel.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    kernel.CreateProcessW.restype = wintypes.BOOL
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL


def _windows_environment_buffer(environment: dict[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    # CreateProcessW requires a case-insensitively sorted sequence of
    # null-terminated key=value strings followed by one more null.  The
    # environment received from Node is already allowlisted; serialize it
    # exactly instead of consulting or expanding the host's registry defaults.
    entries = sorted(environment.items(), key=lambda item: item[0].casefold())
    if any(
        not key
        or "=" in key
        or "\x00" in key
        or "\x00" in value
        for key, value in entries
    ):
        _fail(_ERROR_CONFIG)
    text = "\x00".join(f"{key}={value}" for key, value in entries) + "\x00"
    return ctypes.create_unicode_buffer(text)


def _create_suspended_process_in_job(
    kernel: object,
    arguments: list[str],
    request: Path,
    child_environment: dict[str, str],
    inherited_handles: list[int],
    job_handle: int,
) -> _PROCESS_INFORMATION:
    """Atomically create a suspended process as a member of ``job_handle``."""

    _configure_process_api(kernel)
    if len(inherited_handles) != 3:
        _fail(_ERROR_LAUNCH)

    command_line = subprocess.list2cmdline(arguments)
    if len(command_line) >= 32767:
        _fail(_ERROR_CONFIG)
    command_buffer = ctypes.create_unicode_buffer(command_line)
    environment_buffer = _windows_environment_buffer(child_environment)

    attribute_size = ctypes.c_size_t()
    # The sizing call is documented to fail while returning the required size.
    kernel.InitializeProcThreadAttributeList(
        None, 2, 0, ctypes.byref(attribute_size)
    )
    if not attribute_size.value:
        _fail(_ERROR_LAUNCH)
    attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
    attribute_list = ctypes.cast(attribute_buffer, ctypes.c_void_p)
    if not kernel.InitializeProcThreadAttributeList(
        attribute_list, 2, 0, ctypes.byref(attribute_size)
    ):
        _fail(_ERROR_LAUNCH)

    process_information = _PROCESS_INFORMATION()
    created = False
    try:
        handle_values = (wintypes.HANDLE * len(inherited_handles))(
            *inherited_handles
        )
        job_values = (wintypes.HANDLE * 1)(job_handle)
        if not kernel.UpdateProcThreadAttribute(
            attribute_list,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(handle_values, ctypes.c_void_p),
            ctypes.sizeof(handle_values),
            None,
            None,
        ):
            _fail(_ERROR_LAUNCH)
        if not kernel.UpdateProcThreadAttribute(
            attribute_list,
            0,
            _PROC_THREAD_ATTRIBUTE_JOB_LIST,
            ctypes.cast(job_values, ctypes.c_void_p),
            ctypes.sizeof(job_values),
            None,
            None,
        ):
            _fail(_ERROR_LAUNCH)

        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        (
            startup.StartupInfo.hStdInput,
            startup.StartupInfo.hStdOutput,
            startup.StartupInfo.hStdError,
        ) = inherited_handles
        startup.lpAttributeList = attribute_list.value
        creation_flags = (
            _CREATE_SUSPENDED
            | _CREATE_NO_WINDOW
            | _CREATE_UNICODE_ENVIRONMENT
            | _EXTENDED_STARTUPINFO_PRESENT
        )
        if not kernel.CreateProcessW(
            arguments[0],
            command_buffer,
            None,
            None,
            True,
            creation_flags,
            ctypes.cast(environment_buffer, ctypes.c_void_p),
            str(request),
            ctypes.byref(startup),
            ctypes.byref(process_information),
        ):
            _fail(_ERROR_LAUNCH)
        created = True

        in_job = wintypes.BOOL()
        if not kernel.IsProcessInJob(
            process_information.hProcess, job_handle, ctypes.byref(in_job)
        ) or not in_job.value:
            _fail(_ERROR_LAUNCH)
        return process_information
    except BaseException:
        if created:
            try:
                kernel.TerminateProcess(
                    process_information.hProcess, _ERROR_EXIT_STATUS
                )
                kernel.WaitForSingleObject(process_information.hProcess, 5000)
            except Exception:
                pass
            for handle in (
                process_information.hThread,
                process_information.hProcess,
            ):
                if handle:
                    try:
                        kernel.CloseHandle(handle)
                    except Exception:
                        pass
        raise
    finally:
        kernel.DeleteProcThreadAttributeList(attribute_list)


def _run_windows(
    arguments: list[str], request: Path, child_environment: dict[str, str]
) -> int:
    import _winapi
    import msvcrt

    process_handle: int | None = None
    thread_handle: int | None = None
    job: tuple[object, int] | None = None
    duplicated: list[int] = []
    null_descriptor = -1
    resumed = False
    try:
        # The Job exists before CreateProcessW and is attached by
        # PROC_THREAD_ATTRIBUTE_JOB_LIST as part of process creation.  If this
        # host is killed during CreateProcessW, handle closure therefore cannot
        # leave an unowned suspended child behind.
        job = _create_kill_job()
        kernel, job_handle = job
        current_process = _winapi.GetCurrentProcess()
        input_handle = _winapi.GetStdHandle(_winapi.STD_INPUT_HANDLE)
        output_handle = _winapi.GetStdHandle(_winapi.STD_OUTPUT_HANDLE)
        if input_handle in (None, 0, _winapi.INVALID_HANDLE_VALUE):
            _fail(_ERROR_LAUNCH)
        if output_handle in (None, 0, _winapi.INVALID_HANDLE_VALUE):
            _fail(_ERROR_LAUNCH)
        null_descriptor = os.open(os.devnull, os.O_WRONLY | getattr(os, "O_BINARY", 0))
        error_handle = msvcrt.get_osfhandle(null_descriptor)
        for source in (input_handle, output_handle, error_handle):
            duplicate = _winapi.DuplicateHandle(
                current_process,
                source,
                current_process,
                0,
                True,
                _winapi.DUPLICATE_SAME_ACCESS,
            )
            duplicated.append(duplicate)

        process_information = _create_suspended_process_in_job(
            kernel,
            arguments,
            request,
            child_environment,
            duplicated,
            job_handle,
        )
        process_handle = int(process_information.hProcess)
        thread_handle = int(process_information.hThread)
        for duplicate in duplicated:
            _winapi.CloseHandle(duplicate)
        duplicated.clear()
        os.close(null_descriptor)
        null_descriptor = -1

        if kernel.ResumeThread(thread_handle) == 0xFFFFFFFF:
            _fail(_ERROR_LAUNCH)
        resumed = True
        _winapi.CloseHandle(thread_handle)
        thread_handle = None
        if kernel.WaitForSingleObject(process_handle, _INFINITE) != _WAIT_OBJECT_0:
            _fail(_ERROR_LAUNCH)
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
            _fail(_ERROR_LAUNCH)
        return int(exit_code.value)
    except _HostError:
        raise
    except Exception:
        _fail(_ERROR_LAUNCH)
    finally:
        for duplicate in duplicated:
            try:
                _winapi.CloseHandle(duplicate)
            except Exception:
                pass
        if null_descriptor >= 0:
            try:
                os.close(null_descriptor)
            except OSError:
                pass
        if process_handle is not None and not resumed:
            try:
                kernel.TerminateProcess(process_handle, _ERROR_EXIT_STATUS)
                kernel.WaitForSingleObject(process_handle, 5000)
            except Exception:
                pass
        if thread_handle is not None:
            try:
                _winapi.CloseHandle(thread_handle)
            except Exception:
                pass
        if process_handle is not None:
            try:
                _winapi.CloseHandle(process_handle)
            except Exception:
                pass
        if job is not None:
            try:
                kernel, job_handle = job
                kernel.CloseHandle(job_handle)
            except Exception:
                pass


def run() -> int:
    arguments, schema, model_catalog, child_environment = _read_configuration()
    helper = _load_privacy_helper()
    root = _prepare_runtime(helper)
    _cleanup_stale(root, helper)
    request = _create_request_directory(root, helper)
    marker_descriptor = -1
    try:
        marker_descriptor = _open_request_marker(request)
        schema_path = _write_schema(request, schema, helper)
        arguments = _replace_schema_placeholder(arguments, schema_path)
        if model_catalog is not None:
            model_catalog_path = _write_model_catalog(request, model_catalog, helper)
            arguments = _replace_model_catalog_placeholder(
                arguments, model_catalog_path
            )
        if os.name == "nt":
            return _run_windows(arguments, request, child_environment)
        return _run_posix(arguments, request, child_environment)
    finally:
        if marker_descriptor >= 0:
            try:
                os.close(marker_descriptor)
            except OSError:
                pass
        _delete_owned_request(request, helper)


def main() -> int:
    try:
        if sys.argv[1:] == ["--catalog-query"]:
            return run_catalog_query()
        return run()
    except _HostError as error:
        code = error.code
    except BaseException:
        code = _ERROR_INTERNAL
    try:
        os.write(2, (code + "\n").encode("ascii"))
    except Exception:
        pass
    return _ERROR_EXIT_STATUS


def run_catalog_query() -> int:
    """Metadata only: a fresh CLI home, no real login/config or private payload.

    This path intentionally does not use the business-payload runtime directory.
    The same suspended-process/Job boundary still owns the CLI and descendants.
    """
    arguments, schema, catalog, environment = _read_configuration()
    if schema != {} or catalog is not None or arguments[-len(CATALOG_QUERY_ARGS):] != CATALOG_QUERY_ARGS:
        _fail(_ERROR_CONFIG)
    request = Path.cwd()
    if not request.name.startswith("agent4market-model-catalog-") or any(request.iterdir()):
        _fail(_ERROR_CONFIG)
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR"):
        environment[name] = str(request)
    environment["CODEX_HOME"] = str(request / "codex")
    (request / "codex").mkdir()
    environment["HTTP_PROXY"] = environment["HTTPS_PROXY"] = environment["ALL_PROXY"] = "http://127.0.0.1:1"
    environment["NO_PROXY"] = ""
    environment["NO_COLOR"] = "1"
    if os.name == "nt":
        return _run_windows(arguments, request, environment)
    return _run_posix(arguments, request, environment)


if __name__ == "__main__":
    raise SystemExit(main())
