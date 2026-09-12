"""Closed-manifest Windows program updates; never migrate application data.

This module also runs from an immutable, private job copy with the staged
embedded Python. It must depend only on the standard library and the two
explicitly copied filesystem-policy modules, not on files being replaced.
"""
from __future__ import annotations

import hashlib
import ctypes
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import sys
from contextlib import contextmanager


PROTOCOL_FILE = "agent_platform/windows_update_protocol.json"
MAX_MANIFEST = 32 * 1024 * 1024
MAX_FILES = 100000
MAX_PAYLOAD = 4 * 1024 ** 3
VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z")
JOB_ID = re.compile(r"[a-f0-9]{32}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
PROGRAM_DIRS = {"agent_platform", "profiles", "vertical_plugins", "pi", "plugin", "ui", "scripts", "library", "runtime", "node_modules", ".venv"}
PROGRAM_FILES = {"AGENTS.md", "README.md", "LICENSE", "package.json", "pnpm-lock.yaml", "tsconfig.json", "requirements.txt", "requirements-wxdecipher.txt", "Agent4Market.exe", "INSTALL-NOTES.md"}
_modules = {}


class UpdateFailure(ValueError):
    """Only a short, non-sensitive code may leave an updater process."""


def require(value, code):
    if not value:
        raise UpdateFailure(code)


def load_policy(name):
    if name not in _modules:
        directory = Path(__file__).resolve().parent
        if (directory / "privacy.py").is_file():
            path = directory / ("privacy.py" if name == "privacy" else "installer_policy.py")
        else:
            path = directory / "wechat_privacy.py" if name == "privacy" else directory.parent / "scripts/windows-installer-bootstrap.py"
        spec = importlib.util.spec_from_file_location("update_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _modules[name] = module
    return _modules[name]


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def private_directory(path, *, create=False):
    path = Path(path)
    privacy = load_policy("privacy")
    if create and not path.exists():
        if os.name == "nt":
            load_policy("installer").prepare(path, privacy)
        else:
            path.mkdir(mode=0o700)  # Synthetic, same-user transaction tests only.
    if os.name == "nt":
        sid = privacy._win_current_sid()
        parents = privacy._win_open_safe_parents(path, sid)
        handle = None
        try:
            handle = privacy._win_open_directory(path, privacy._READ_CONTROL)
            owner, _protected, aces = privacy._win_security_snapshot(handle)
            expected = {sid, privacy._win_well_known_sid(22)}
            require(owner == sid and len(aces) == 2 and {ace[3] for ace in aces} == expected, "PRIVATE_DIRECTORY_REQUIRED")
            require(all(kind == 0 and flags in (3, 19) and mask == privacy._FILE_ALL_ACCESS for kind, flags, mask, _sid in aces), "PRIVATE_DIRECTORY_REQUIRED")
        finally:
            privacy._win_close(handle)
            for parent in reversed(parents):
                privacy._win_close(parent)
    else:
        privacy._posix_verify(path)
    return path


def regular(root, relative, *, missing=False):
    """No ambiguous Windows paths, links, named streams or non-regular files."""
    root = Path(root)
    require(isinstance(relative, str) and relative and len(relative) <= 4096, "INVALID_PATH")
    parts = relative.split("/")
    require(not PurePosixPath(relative).is_absolute() and "\\" not in relative and ":" not in relative, "INVALID_PATH")
    path = root
    for index, part in enumerate(parts):
        require(part not in {"", ".", ".."} and not part.endswith((".", " ")) and not re.search(r'[<>"|?*\x00-\x1f]', part), "INVALID_PATH")
        require(not re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part), "INVALID_PATH")
        path = path / part
        try:
            info = path.lstat()
        except FileNotFoundError:
            require(missing, "MISSING_FILE")
            continue
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400, "LINK_REFUSED")
        if index < len(parts) - 1:
            require(stat.S_ISDIR(info.st_mode), "INVALID_PARENT")
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "NON_REGULAR_FILE")
    require(path.is_relative_to(root), "INVALID_PATH")
    return path


def program_path(relative):
    require(isinstance(relative, str), "INVALID_PATH")
    parts = relative.split("/")
    require((parts[0] in PROGRAM_DIRS and len(parts) > 1) or relative in PROGRAM_FILES, "USER_DATA_REFUSED")
    require(parts[0].casefold() not in {"data", "inputs", "outputs", ".pi"}
            and relative.casefold() != "library/templates/company"
            and not relative.casefold().startswith("library/templates/company/")
            and not any(part.casefold() == "%systemdrive%" for part in parts), "USER_DATA_REFUSED")
    require(relative != "runtime/install-manifest.json", "MANIFEST_IN_PAYLOAD")
    # Validate syntax even when the file does not exist yet.
    require(not PurePosixPath(relative).is_absolute() and "\\" not in relative and ":" not in relative
            and all(part not in {"", ".", ".."} and not part.endswith((".", " "))
                    and not re.search(r'[<>"|?*\x00-\x1f]', part)
                    and not re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part) for part in parts), "INVALID_PATH")
    return relative


def read_json(path, limit=MAX_MANIFEST):
    path = Path(path)
    regular(path.parent, path.name)
    require(path.stat().st_size <= limit, "FILE_TOO_LARGE")
    try:
        return json.loads(path.read_bytes())
    except (ValueError, UnicodeError):
        raise UpdateFailure("INVALID_JSON") from None


def manifest(raw, expected_version=None):
    require(isinstance(raw, dict) and VERSION.fullmatch(str(raw.get("version", ""))), "INVALID_VERSION")
    require(expected_version is None or raw["version"] == expected_version, "VERSION_CHANGED")
    rows = raw.get("files")
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_FILES, "INVALID_MANIFEST")
    result, seen, total = {}, set(), 0
    for row in rows:
        require(isinstance(row, dict), "INVALID_MANIFEST")
        path = row.get("path")
        require(isinstance(path, str) and path.casefold() not in seen, "DUPLICATE_PATH")
        seen.add(path.casefold())
        require(SHA256.fullmatch(str(row.get("sha256", ""))) and type(row.get("bytes")) is int and 0 <= row["bytes"] <= MAX_PAYLOAD, "INVALID_MANIFEST")
        total += row["bytes"]
        require(total <= MAX_PAYLOAD, "PAYLOAD_TOO_LARGE")
        if path.startswith("data/"):
            require(re.fullmatch(r"data/[A-Za-z0-9_./-]+\.example\.(csv|json)", path)
                    and all(part not in {"", ".", ".."} for part in path.split("/")), "USER_DATA_REFUSED")
            continue  # Even public examples in data/ are not changed in place.
        program_path(path)
        result[path] = {"path": path, "sha256": row["sha256"], "bytes": row["bytes"]}
    require(not any(str(parent).casefold() in seen for path in seen for parent in PurePosixPath(path).parents if str(parent) != "."), "PATH_COLLISION")
    require({"package.json", "Agent4Market.exe", PROTOCOL_FILE, "runtime/private-runtime.marker"} <= result.keys(), "UPDATE_PROTOCOL_UNSUPPORTED")
    return result


def verify_programs(root, rows):
    private_directory(root)
    for name, row in rows.items():
        path = regular(root, name)
        require(path.stat().st_size == row["bytes"] and digest(path) == row["sha256"], "PROGRAM_MODIFIED")


def installation(root):
    require(os.name == "nt" and sys.maxsize > 2 ** 32, "WINDOWS_ONLY")
    root = Path(os.path.abspath(root))
    match = re.fullmatch(r"Agent4Market-(\d+\.\d+\.\d+)", root.name)
    require(match and root.parent == load_policy("installer").profile_path(), "INSTALLED_DESKTOP_REQUIRED")
    private_directory(root)
    load_policy("privacy")._windows_verify(root)
    version = read_json(regular(root, "package.json"), 65536).get("version")
    rows = manifest(read_json(regular(root, "runtime/install-manifest.json")), version)
    require(regular(root, "runtime/private-runtime.marker").read_text().strip() == "Agent4Market private runtime v1", "INVALID_RUNTIME")
    protocol = read_json(regular(root, PROTOCOL_FILE), 4096)
    require(protocol == {"application": "Agent4Market", "platform": "windows-x64", "update_protocol": 1, "data_compatibility": 1}, "UPDATE_PROTOCOL_UNSUPPORTED")
    require(digest(root / PROTOCOL_FILE) == rows[PROTOCOL_FILE]["sha256"], "UPDATE_PROTOCOL_UNSUPPORTED")
    return {"root": root, "version": version, "origin_version": match.group(1), "rows": rows}


def create_job(root, identifier):
    require(JOB_ID.fullmatch(identifier), "INVALID_JOB")
    parent = Path(root)
    for name in (".pi", "app-updates"):
        parent = private_directory(parent / name, create=True)
    job = parent / identifier
    require(not job.exists(), "JOB_EXISTS")
    return private_directory(job, create=True)


def mkdirs(root, relative):
    path = Path(root)
    for part in PurePosixPath(relative).parts:
        path = private_directory(path / part, create=True)
    return path


def write_json(path, value):
    content = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()
    require(len(content) <= MAX_MANIFEST, "FILE_TOO_LARGE")
    write_atomic(path, content)


def durable_replace(source, target):
    if os.name == "nt":
        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move.restype = ctypes.c_int
        # Same volume only. Never schedule reboot deletion or copy across disks.
        source_path = "\\\\?\\" + os.path.abspath(source)
        target_path = "\\\\?\\" + os.path.abspath(target)
        if not move(source_path, target_path, 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(source, target)
        for parent in {Path(source).parent, Path(target).parent}:
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def write_atomic(path, content):
    path = Path(path)
    private_directory(path.parent)
    regular(path.parent, path.name, missing=True)
    temporary = path.with_name(path.name + ".writing-" + secrets.token_hex(8))
    with temporary.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    durable_replace(temporary, path)


def copy_new(source, target, expected):
    source, target = Path(source), Path(target)
    require(not target.exists(), "TARGET_EXISTS")
    regular(source.parent, source.name)
    private_directory(target.parent)
    # Do not append to a long payload filename: a valid Windows target can
    # otherwise exceed MAX_PATH solely because of the temporary suffix.
    temporary = target.with_name(".copying-" + secrets.token_hex(8))
    with source.open("rb") as incoming, temporary.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    require(temporary.stat().st_size == expected["bytes"] and digest(temporary) == expected["sha256"], "COPY_HASH_MISMATCH")
    durable_replace(temporary, target)


def make_plan(old, new):
    old_rows, new_rows = manifest(old), manifest(new)
    require(old_rows[PROTOCOL_FILE]["sha256"] == new_rows[PROTOCOL_FILE]["sha256"], "DATA_FORMAT_CHANGED")
    require(tuple(map(int, new["version"].split("."))) > tuple(map(int, old["version"].split("."))), "NOT_AN_UPGRADE")
    # Never delete a parent tree or merge an untracked file with a payload.
    return [{"path": name, "old": old_rows.get(name), "new": new_rows.get(name)}
            for name in sorted(old_rows.keys() | new_rows.keys()) if old_rows.get(name) != new_rows.get(name)]


def file_matches(root, name, row):
    path = regular(root, name, missing=True)
    if row is None:
        return not path.exists()
    return path.is_file() and path.stat().st_size == row["bytes"] and digest(path) == row["sha256"]


def backup(root, stage, job, old, new):
    operations = make_plan(old, new)
    journal = {"phase": "backed_up", "operations": operations,
               "old_manifest_sha256": digest(Path(root) / "runtime/install-manifest.json"),
               "new_manifest_sha256": digest(Path(job) / "new-manifest.json")}
    # Bound the expanded journal before creating a backup or mutating programs.
    require(len(json.dumps(journal, ensure_ascii=True, sort_keys=True, indent=2).encode()) + 4096 <= MAX_MANIFEST, "PLAN_TOO_LARGE")
    verify_programs(root, manifest(old))
    verify_programs(stage, manifest(new))
    for operation in operations:
        require(file_matches(root, operation["path"], operation["old"]), "PROGRAM_MODIFIED")
    needed = sum(op["old"]["bytes"] for op in operations if op["old"]) + max([op["new"]["bytes"] for op in operations if op["new"]] or [0]) + 64 * 1024 ** 2
    require(shutil.disk_usage(root).free >= needed, "NOT_ENOUGH_SPACE")
    backup_root = private_directory(Path(job) / "backup", create=True)
    for operation in operations:
        if operation["old"]:
            name = operation["path"]
            if str(PurePosixPath(name).parent) != ".":
                mkdirs(backup_root, str(PurePosixPath(name).parent))
            copy_new(regular(root, name), backup_root / name, operation["old"])
    old_manifest = Path(job) / "old-manifest.json"
    copy_new(regular(root, "runtime/install-manifest.json"), old_manifest,
             {"bytes": (Path(root) / "runtime/install-manifest.json").stat().st_size,
              "sha256": digest(Path(root) / "runtime/install-manifest.json")})
    require(digest(old_manifest) == journal["old_manifest_sha256"], "PLAN_CHANGED")
    write_json(Path(job) / "journal.json", journal)
    return operations


def _replace(root, source_root, job, name, wanted, current):
    program_path(name)
    require(file_matches(root, name, current), "PROGRAM_MODIFIED")
    path = regular(root, name, missing=True)
    if wanted is None:
        if path.exists():
            # Durable rename removes one program path without recursive delete.
            # Retain the retired bytes inside this job until explicit cleanup.
            retired = private_directory(Path(job) / "retired", create=True)
            durable_replace(path, retired / secrets.token_hex(16))
        return
    parent_relative = str(PurePosixPath(name).parent)
    if parent_relative != ".":
        mkdirs(root, parent_relative)
    pending = private_directory(Path(job) / "pending", create=True)
    temporary = pending / secrets.token_hex(16)
    copy_new(regular(source_root, name), temporary, wanted)
    require(file_matches(root, name, current), "PROGRAM_MODIFIED")
    durable_replace(temporary, path)


def replace_manifest(root, source):
    path = regular(root, "runtime/install-manifest.json")
    write_atomic(path, Path(source).read_bytes())


def verify_outcome(root, job, side):
    require(side in {"old", "new"}, "INVALID_PHASE")
    root, job = Path(root), Path(job)
    journal = read_json(job / "journal.json")
    old, new = read_json(job / "old-manifest.json"), read_json(job / "new-manifest.json")
    require(digest(job / "old-manifest.json") == journal["old_manifest_sha256"]
            and digest(job / "new-manifest.json") == journal["new_manifest_sha256"], "PLAN_CHANGED")
    require(journal["operations"] == make_plan(old, new), "PLAN_CHANGED")
    verify_programs(root, manifest(old if side == "old" else new))
    for operation in journal["operations"]:
        require(file_matches(root, operation["path"], operation[side]), "PROGRAM_MODIFIED")
    require(digest(regular(root, "runtime/install-manifest.json")) == journal[side + "_manifest_sha256"], "MANIFEST_CHANGED")


def apply(root, stage, job):
    journal = read_json(Path(job) / "journal.json")
    require(journal["phase"] == "backed_up", "INVALID_PHASE")
    old = read_json(Path(job) / "old-manifest.json")
    new = read_json(Path(job) / "new-manifest.json")
    require(digest(Path(job) / "old-manifest.json") == journal["old_manifest_sha256"]
            and digest(Path(job) / "new-manifest.json") == journal["new_manifest_sha256"], "PLAN_CHANGED")
    require(journal["operations"] == make_plan(old, new), "PLAN_CHANGED")
    journal["phase"] = "applying"
    write_json(Path(job) / "journal.json", journal)
    # Dependencies/additions precede consumers; the desktop EXE is last.
    operations = sorted(journal["operations"], key=lambda op: (op["path"] == "Agent4Market.exe", op["old"] is not None))
    for operation in operations:
        _replace(root, stage, job, operation["path"], operation["new"], operation["old"])
    replace_manifest(root, Path(job) / "new-manifest.json")
    verify_outcome(root, job, "new")
    journal["phase"] = "validating"
    write_json(Path(job) / "journal.json", journal)


def rollback(root, job):
    """Idempotent recovery accepts only known old/new/absent program bytes."""
    root, job = Path(root), Path(job)
    journal = read_json(job / "journal.json")
    require(journal["phase"] in {"backed_up", "applying", "validating", "rolled_back"}, "INVALID_PHASE")
    old, new = read_json(job / "old-manifest.json"), read_json(job / "new-manifest.json")
    require(digest(job / "old-manifest.json") == journal["old_manifest_sha256"]
            and digest(job / "new-manifest.json") == journal["new_manifest_sha256"], "PLAN_CHANGED")
    operations = make_plan(old, new)
    require(journal["operations"] == operations, "PLAN_CHANGED")
    current_manifest = regular(root, "runtime/install-manifest.json")
    require(digest(current_manifest) in {journal["old_manifest_sha256"], journal["new_manifest_sha256"]}, "MANIFEST_CHANGED")
    for op in operations:
        require(file_matches(root, op["path"], op["old"]) or file_matches(root, op["path"], op["new"]), "PROGRAM_MODIFIED")
        if op["old"]:
            require(file_matches(job / "backup", op["path"], op["old"]), "BACKUP_CHANGED")
    # Complete preflight precedes the first restoration/deletion.
    for op in operations:
        if not file_matches(root, op["path"], op["old"]):
            _replace(root, job / "backup", job, op["path"], op["old"], op["new"])
    replace_manifest(root, job / "old-manifest.json")
    verify_outcome(root, job, "old")
    journal["phase"] = "rolled_back"
    write_json(job / "journal.json", journal)


@contextmanager
def exclusive_job(job):
    """OS-released lock, so a crashed worker cannot strand a stale lockfile."""
    path = Path(job) / "worker.lock"
    private_directory(path.parent)
    regular(path.parent, path.name, missing=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise UpdateFailure("UPDATE_ALREADY_RUNNING") from None
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
