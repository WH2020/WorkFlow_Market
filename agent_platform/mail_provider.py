from __future__ import annotations

import base64
import ctypes
import hashlib
import imaplib
import ipaddress
import json
import os
import platform
import re
import socket
import ssl
import subprocess
import tempfile
from contextlib import contextmanager
from ctypes import wintypes
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterator


PROVIDER_ID = "agent4market-reimbursement-mail"
MAX_MESSAGE_BYTES = 20 * 1024 * 1024
MAX_IMPORTED_BYTES = 50 * 1024 * 1024
MAX_IMPORTED_ATTACHMENTS = 50
MAX_SELECTED_MESSAGES = 20
ALLOWED_ATTACHMENT_SUFFIXES = {
    ".pdf", ".ofd", ".png", ".jpg", ".jpeg", ".heic", ".xlsx", ".xls", ".csv", ".docx", ".zip",
}
MAIL_PRESETS = {
    "qq": {"label": "QQ 邮箱", "host": "imap.qq.com", "port": 993, "credential_label": "客户端授权码"},
    "163": {"label": "163 邮箱", "host": "imap.163.com", "port": 993, "credential_label": "客户端授权码"},
    "126": {"label": "126 邮箱", "host": "imap.126.com", "port": 993, "credential_label": "客户端授权码"},
    "aliyun": {"label": "阿里企业邮箱", "host": "imap.qiye.aliyun.com", "port": 993, "credential_label": "三方客户端安全密码"},
    "gmail": {"label": "Gmail", "host": "imap.gmail.com", "port": 993, "credential_label": "应用专用密码"},
    "custom": {"label": "其他 IMAP 邮箱", "host": "", "port": 993, "credential_label": "客户端授权码或应用专用密码"},
}


class MailProviderError(ValueError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def settings_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".pi" / "director-runtime" / "mail-provider.json"


def _atomic_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_create(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_replace(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
        raise MailProviderError("本地邮箱配置缺失或不安全")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MailProviderError("无法读取本地邮箱配置") from error
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("provider_id") != PROVIDER_ID:
        raise MailProviderError("本地邮箱配置格式无效")
    return value


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    source, source_buffer = _blob(data)
    extra, extra_buffer = _blob(entropy)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "Agent4Market reimbursement mailbox", ctypes.byref(extra), None, None, 0x1,
        ctypes.byref(output),
    ):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI 加密失败")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer, extra_buffer


def _dpapi_unprotect(data: bytes, entropy: bytes) -> bytes:
    source, source_buffer = _blob(data)
    extra, extra_buffer = _blob(entropy)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(output)
    ):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI 解密失败")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer, extra_buffer


def _keychain_service(project_root: Path) -> str:
    suffix = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"Agent4Market.ReimbursementMail.{suffix}"


def _entropy(project_root: Path, config: dict[str, Any]) -> bytes:
    identity = f"{project_root.resolve()}|{PROVIDER_ID}|{config['email_address']}|{config['host']}"
    return hashlib.sha256(identity.encode("utf-8")).digest()


def _valid_email(value: str) -> bool:
    return bool(re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+",
        value,
    ))


def _address_is_internal(address: str) -> bool:
    parsed = ipaddress.ip_address(address.split("%", 1)[0])
    return not parsed.is_global


def normalize_mail_settings(
    payload: dict[str, Any], *, resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo
) -> dict[str, Any]:
    provider = str(payload.get("provider") or "").strip()
    if provider not in MAIL_PRESETS:
        raise MailProviderError("请选择支持的邮箱类型")
    email_address = str(payload.get("email_address") or "").strip()
    username = str(payload.get("username") or email_address).strip()
    if not _valid_email(email_address):
        raise MailProviderError("请填写有效的邮箱地址")
    if not username or len(username) > 254 or any(ord(character) < 32 for character in username):
        raise MailProviderError("邮箱登录账号无效")
    preset = MAIL_PRESETS[provider]
    host = str(preset["host"] or payload.get("host") or "").strip().lower().rstrip(".")
    if (
        not host or len(host) > 253 or
        re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host) is None
    ):
        raise MailProviderError("请填写有效的 IMAP 服务器域名")
    allow_private_network = payload.get("allow_private_network", False)
    if not isinstance(allow_private_network, bool):
        raise MailProviderError("邮箱网络范围设置无效")
    try:
        addresses = sorted({
            item[4][0]
            for item in resolver(host, 993, type=socket.SOCK_STREAM)
            if item and len(item) > 4 and item[4]
        })
    except OSError as error:
        raise MailProviderError("无法解析 IMAP 服务器域名") from error
    if not addresses:
        raise MailProviderError("IMAP 服务器域名没有可用地址")
    if any(_address_is_internal(address) for address in addresses) and not allow_private_network:
        raise MailProviderError("该邮箱服务器位于本机或局域网；确认可信后再允许私有网络")
    return {
        "provider": provider,
        "provider_label": str(preset["label"]),
        "email_address": email_address,
        "username": username,
        "host": host,
        "port": 993,
        "mailbox": "INBOX",
        "allow_private_network": allow_private_network,
    }


class _PinnedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, port: int, pinned_address: str, timeout: float) -> None:
        self._pinned_address = pinned_address
        super().__init__(host=host, port=port, ssl_context=ssl.create_default_context(), timeout=timeout)

    def _create_socket(self, timeout: float | None):  # type: ignore[override]
        sock = socket.create_connection((self._pinned_address, self.port), timeout)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


def _resolve_runtime_address(config: dict[str, Any]) -> str:
    try:
        addresses = sorted({
            item[4][0]
            for item in socket.getaddrinfo(config["host"], config["port"], type=socket.SOCK_STREAM)
            if item and len(item) > 4 and item[4]
        })
    except OSError as error:
        raise MailProviderError("无法解析 IMAP 服务器域名") from error
    if not addresses:
        raise MailProviderError("IMAP 服务器域名没有可用地址")
    if any(_address_is_internal(address) for address in addresses) and not config.get("allow_private_network"):
        raise MailProviderError("IMAP 服务器解析到了私有网络地址，连接已停止")
    return addresses[0]


@contextmanager
def connected_mailbox(
    config: dict[str, Any], password: str, *, timeout: float = 15.0,
    connector: Callable[[dict[str, Any], str], Any] | None = None,
) -> Iterator[Any]:
    connection = None
    try:
        if connector is not None:
            connection = connector(config, password)
        else:
            address = _resolve_runtime_address(config)
            connection = _PinnedIMAP4SSL(config["host"], int(config["port"]), address, timeout)
            status, _ = connection.login(config["username"], password)
            if status != "OK":
                raise MailProviderError("邮箱登录失败，请检查授权码或应用专用密码")
        yield connection
    except MailProviderError:
        raise
    except (imaplib.IMAP4.error, OSError, TimeoutError, ssl.SSLError) as error:
        raise MailProviderError("无法连接邮箱，请检查 IMAP 服务、账号和授权码") from error
    finally:
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                pass


def _password_record(project_root: Path, config: dict[str, Any], password: str) -> dict[str, Any]:
    secret = password.strip()
    if not secret or len(secret) > 4096 or any(ord(character) < 32 for character in secret):
        raise MailProviderError("请填写客户端授权码或应用专用密码，不要填写普通网页登录密码")
    system = platform.system().lower()
    if system == "windows":
        encrypted = _dpapi_protect(secret.encode("utf-8"), _entropy(project_root, config))
        return {"backend": "windows-dpapi", "ciphertext": base64.b64encode(encrypted).decode("ascii")}
    if system == "darwin":
        service = _keychain_service(project_root)
        completed = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", config["email_address"], "-w", secret],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            raise MailProviderError("无法写入 macOS 钥匙串")
        return {"backend": "macos-keychain", "service": service, "account": config["email_address"]}
    return {"backend": "private-file", "secret": base64.b64encode(secret.encode("utf-8")).decode("ascii")}


def _load_password(project_root: Path, record: dict[str, Any]) -> str:
    backend = record.get("backend")
    config = record.get("config")
    if not isinstance(config, dict):
        raise MailProviderError("邮箱配置缺少账号信息")
    try:
        if backend == "windows-dpapi":
            encrypted = base64.b64decode(str(record.get("ciphertext") or ""), validate=True)
            return _dpapi_unprotect(encrypted, _entropy(project_root, config)).decode("utf-8")
        if backend == "macos-keychain":
            completed = subprocess.run(
                ["security", "find-generic-password", "-s", str(record.get("service") or ""),
                 "-a", str(record.get("account") or ""), "-w"],
                check=False, capture_output=True, text=True,
            )
            if completed.returncode == 0:
                return completed.stdout.rstrip("\r\n")
        if backend == "private-file":
            return base64.b64decode(str(record.get("secret") or ""), validate=True).decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise MailProviderError("无法解密本地邮箱授权码") from error
    raise MailProviderError("本地邮箱授权码不可用")


def configure_mail_provider(
    project_root: Path | str, payload: dict[str, Any], password: str,
    *, resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    connector: Callable[[dict[str, Any], str], Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config = normalize_mail_settings(payload, resolver=resolver)
    secret = password.strip()
    if not secret:
        existing = _read_record(settings_path(root))
        existing_config = existing.get("config")
        if not isinstance(existing_config, dict) or any(existing_config.get(key) != config.get(key) for key in ("email_address", "username", "host")):
            raise MailProviderError("邮箱账号或服务器已改变，请重新填写授权码")
        secret = _load_password(root, existing)
    with connected_mailbox(config, secret, connector=connector) as mailbox:
        status, _ = mailbox.select(config["mailbox"], readonly=True)
        if status != "OK":
            raise MailProviderError("无法以只读方式打开邮箱收件箱")
    protected = _password_record(root, config, secret)
    record = {
        "version": 1, "provider_id": PROVIDER_ID, "config": config, "updated_at": _now(), **protected,
    }
    _atomic_json(settings_path(root), record)
    return mail_settings_summary(root)


def clear_mail_provider(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = settings_path(root)
    record: dict[str, Any] = {}
    if path.is_file():
        try:
            record = _read_record(path)
        except MailProviderError:
            record = {}
    if record.get("backend") == "macos-keychain":
        subprocess.run(
            ["security", "delete-generic-password", "-s", str(record.get("service") or ""),
             "-a", str(record.get("account") or "")],
            check=False, capture_output=True, text=True,
        )
    path.unlink(missing_ok=True)
    return mail_settings_summary(root)


def mail_settings_summary(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = settings_path(root)
    if not path.is_file():
        return {"status": "unconfigured", "configured": False, "presets": MAIL_PRESETS}
    try:
        record = _read_record(path)
        config = record.get("config")
        if not isinstance(config, dict):
            raise MailProviderError("邮箱配置缺少账号信息")
        backend = record.get("backend")
        if backend == "windows-dpapi":
            base64.b64decode(str(record.get("ciphertext") or ""), validate=True)
        elif backend == "macos-keychain":
            if not record.get("service") or not record.get("account"):
                raise MailProviderError("本地邮箱授权码不可用")
        elif backend == "private-file":
            base64.b64decode(str(record.get("secret") or ""), validate=True)
        else:
            raise MailProviderError("本地邮箱授权码不可用")
        return {
            "status": "configured", "configured": True,
            "provider": config.get("provider"), "provider_label": config.get("provider_label"),
            "email_address": config.get("email_address"), "username": config.get("username"),
            "host": config.get("host"), "port": config.get("port"),
            "allow_private_network": bool(config.get("allow_private_network")),
            "updated_at": record.get("updated_at"), "presets": MAIL_PRESETS,
        }
    except (MailProviderError, ValueError) as error:
        message = str(error) if isinstance(error, MailProviderError) else "本地邮箱授权码不可用"
        return {"status": "error", "configured": False, "error": message, "presets": MAIL_PRESETS}


def _configured(project_root: Path | str) -> tuple[dict[str, Any], str]:
    root = Path(project_root).resolve()
    record = _read_record(settings_path(root))
    config = record.get("config")
    if not isinstance(config, dict):
        raise MailProviderError("邮箱配置缺少账号信息")
    return config, _load_password(root, record)


def _decode_header(value: Any) -> str:
    try:
        decoded = str(make_header(decode_header(str(value or ""))))
    except (LookupError, UnicodeDecodeError):
        decoded = str(value or "")
    return re.sub(r"[\x00-\x1f\x7f]+", " ", decoded).strip()[:500]


def _message_date(message: Any) -> str:
    try:
        parsed = parsedate_to_datetime(str(message.get("Date") or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().isoformat(timespec="minutes")
    except (TypeError, ValueError, OverflowError):
        return ""


def _message_bytes(mailbox: Any, uid: str) -> bytes:
    status, size_data = mailbox.uid("fetch", uid, "(RFC822.SIZE)")
    if status != "OK":
        raise MailProviderError("无法读取邮件大小")
    size_parts: list[bytes] = []
    for item in size_data or []:
        if isinstance(item, bytes):
            size_parts.append(item)
        elif isinstance(item, tuple) and item and isinstance(item[0], bytes):
            size_parts.append(item[0])
    size_text = b" ".join(size_parts)
    match = re.search(rb"RFC822\.SIZE\s+(\d+)", size_text)
    if match is None or int(match.group(1)) > MAX_MESSAGE_BYTES:
        raise MailProviderError("邮件超过 20 兆字节，已跳过以避免占用过多内存")
    status, data = mailbox.uid("fetch", uid, "(BODY.PEEK[])")
    if status != "OK":
        raise MailProviderError("无法读取邮件内容")
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            if len(item[1]) > MAX_MESSAGE_BYTES:
                raise MailProviderError("邮件实际内容超过 20 兆字节")
            return item[1]
    raise MailProviderError("邮箱没有返回可解析的邮件内容")


def _safe_attachment_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(value).name).rstrip(". ")
    if not cleaned or len(cleaned) > 120:
        suffix = Path(cleaned).suffix[:10]
        stem = Path(cleaned).stem[:100] or "报销凭证"
        cleaned = f"{stem}{suffix}"
    return cleaned


def _attachments(message: Any, *, include_content: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in message.walk():
        filename = part.get_filename()
        if not filename:
            continue
        name = _safe_attachment_name(_decode_header(filename))
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
            continue
        content = part.get_payload(decode=True) or b""
        if not isinstance(content, bytes) or not content or len(content) > MAX_MESSAGE_BYTES:
            continue
        record: dict[str, Any] = {
            "name": name, "size": len(content), "content_type": str(part.get_content_type() or "application/octet-stream")[:100],
        }
        if include_content:
            record["content"] = content
        result.append(record)
    return result


def _message_key(config: dict[str, Any], uid: str, message: Any) -> str:
    identity = "|".join([
        str(config.get("email_address") or ""), str(config.get("host") or ""), uid,
        _decode_header(message.get("Message-ID")), _decode_header(message.get("Subject")), _decode_header(message.get("Date")),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _date_range(payload: dict[str, Any]) -> tuple[date, date]:
    try:
        date_from = date.fromisoformat(str(payload.get("date_from") or ""))
        date_to = date.fromisoformat(str(payload.get("date_to") or ""))
    except ValueError as error:
        raise MailProviderError("邮箱检索日期格式无效") from error
    if date_to < date_from or (date_to - date_from).days > 366:
        raise MailProviderError("邮箱检索时间范围必须为 0–366 天")
    return date_from, date_to


def search_reimbursement_mail(
    project_root: Path | str, payload: dict[str, Any],
    *, connector: Callable[[dict[str, Any], str], Any] | None = None,
) -> dict[str, Any]:
    config, password = _configured(project_root)
    date_from, date_to = _date_range(payload)
    query = str(payload.get("query") or "").strip().casefold()
    if len(query) > 100:
        raise MailProviderError("邮箱搜索词不能超过 100 字")
    results: list[dict[str, Any]] = []
    skipped_large = 0
    with connected_mailbox(config, password, connector=connector) as mailbox:
        status, _ = mailbox.select(config["mailbox"], readonly=True)
        if status != "OK":
            raise MailProviderError("无法以只读方式打开邮箱收件箱")
        since = date_from.strftime("%d-%b-%Y")
        before = (date_to + timedelta(days=1)).strftime("%d-%b-%Y")
        status, data = mailbox.uid("search", None, "SINCE", since, "BEFORE", before)
        if status != "OK":
            raise MailProviderError("邮箱检索失败")
        uids = (data[0].split() if data and isinstance(data[0], bytes) else [])[-100:]
        for raw_uid in reversed(uids):
            uid = raw_uid.decode("ascii", errors="ignore")
            if not uid.isdigit():
                continue
            try:
                raw_message = _message_bytes(mailbox, uid)
            except MailProviderError as error:
                if "超过 20 兆字节" in str(error):
                    skipped_large += 1
                    continue
                raise
            message = BytesParser(policy=policy.default).parsebytes(raw_message)
            attachments = _attachments(message, include_content=False)
            if not attachments:
                continue
            subject = _decode_header(message.get("Subject")) or "无主题邮件"
            sender = _decode_header(message.get("From")) or "未知发件人"
            if query and query not in "\n".join([subject, sender, *[item["name"] for item in attachments]]).casefold():
                continue
            results.append({
                "uid": uid, "message_key": _message_key(config, uid, message),
                "subject": subject, "sender": sender, "received_at": _message_date(message),
                "attachments": attachments,
            })
            if len(results) >= 50:
                break
    return {"messages": results, "skipped_large": skipped_large, "read_only": True}


def import_reimbursement_mail(
    project_root: Path | str, inputs_root: Path, outputs_root: Path,
    selected: Any,
    *, connector: Callable[[dict[str, Any], str], Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(selected, list) or not 1 <= len(selected) <= MAX_SELECTED_MESSAGES:
        raise MailProviderError(f"请选择 1–{MAX_SELECTED_MESSAGES} 封报销邮件")
    normalized: list[tuple[str, str]] = []
    seen_uids: set[str] = set()
    for item in selected:
        if not isinstance(item, dict):
            raise MailProviderError("所选报销邮件记录无效")
        uid = str(item.get("uid") or "")
        message_key = str(item.get("message_key") or "")
        if not uid.isdigit() or re.fullmatch(r"[a-f0-9]{64}", message_key) is None:
            raise MailProviderError("所选报销邮件标识无效")
        if uid in seen_uids:
            raise MailProviderError("不能重复选择同一封报销邮件")
        seen_uids.add(uid)
        normalized.append((uid, message_key))
    resolved_project_root = Path(project_root).resolve()
    controlled_inputs = inputs_root.resolve()
    controlled_outputs = outputs_root.resolve()
    if not controlled_inputs.is_relative_to(resolved_project_root) or controlled_inputs == resolved_project_root:
        raise MailProviderError("报销材料 inputs 目录不属于当前项目")
    if not controlled_outputs.is_relative_to(resolved_project_root) or controlled_outputs == resolved_project_root:
        raise MailProviderError("报销材料 outputs 目录不属于当前项目")
    config, password = _configured(resolved_project_root)
    batch_digest = hashlib.sha256("|".join(sorted(key for _, key in normalized)).encode("ascii")).hexdigest()
    batch_id = f"reimbursement-{batch_digest[:16]}"
    reimbursements_input = inputs_root / "reimbursements"
    batch_root = reimbursements_input / batch_id
    if inputs_root.is_symlink() or reimbursements_input.is_symlink() or batch_root.is_symlink():
        raise MailProviderError("报销材料目录不能是符号链接")
    messages: list[dict[str, Any]] = []
    total_bytes = 0
    total_attachments = 0
    with connected_mailbox(config, password, connector=connector) as mailbox:
        status, _ = mailbox.select(config["mailbox"], readonly=True)
        if status != "OK":
            raise MailProviderError("无法以只读方式打开邮箱收件箱")
        for uid, expected_key in normalized:
            raw_message = _message_bytes(mailbox, uid)
            message = BytesParser(policy=policy.default).parsebytes(raw_message)
            actual_key = _message_key(config, uid, message)
            if actual_key != expected_key:
                raise MailProviderError("邮件内容已变化，请重新搜索后选择")
            attachments = _attachments(message, include_content=True)
            if not attachments:
                raise MailProviderError("所选邮件已没有可导入的报销附件")
            total_attachments += len(attachments)
            total_bytes += sum(len(item["content"]) for item in attachments)
            if total_attachments > MAX_IMPORTED_ATTACHMENTS or total_bytes > MAX_IMPORTED_BYTES:
                raise MailProviderError("本次最多导入 50 个附件且合计不超过 50 兆字节")
            messages.append({
                "uid": uid, "message_key": actual_key,
                "subject": _decode_header(message.get("Subject")) or "无主题邮件",
                "sender": _decode_header(message.get("From")) or "未知发件人",
                "received_at": _message_date(message), "attachments": attachments,
            })

    batch_root.mkdir(parents=True, exist_ok=True)
    actual_batch_root = batch_root.resolve()
    if not actual_batch_root.is_relative_to(controlled_inputs) or actual_batch_root == controlled_inputs:
        raise MailProviderError("报销材料目录越出受控 inputs 范围")

    created: list[Path] = []
    materials: list[dict[str, Any]] = []
    try:
        for message in messages:
            for index, attachment in enumerate(message["attachments"], start=1):
                original = attachment["name"]
                suffix = Path(original).suffix
                stem = Path(original).stem[:72] or "报销凭证"
                base_name = f"mail-{message['message_key'][:10]}-{index}-{stem}{suffix}"
                if len(base_name) > 120:
                    base_name = f"mail-{message['message_key'][:10]}-{index}-{stem[:50]}{suffix}"
                target = batch_root / base_name
                content = attachment["content"]
                digest = hashlib.sha256(content).hexdigest()
                if target.exists():
                    if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                        raise MailProviderError("同名报销材料已存在但内容不同，导入已停止")
                else:
                    _atomic_create(target, content)
                    created.append(target)
                actual_target = target.resolve(strict=True)
                if not actual_target.is_relative_to(actual_batch_root) or actual_target == actual_batch_root:
                    raise MailProviderError("报销材料文件越出受控批次目录")
                materials.append({
                    "material_id": "material-" + hashlib.sha256(
                        f"{message['message_key']}:{index}".encode("ascii")
                    ).hexdigest()[:16],
                    "name": original, "stored_name": target.name,
                    "path": actual_target.relative_to(resolved_project_root).as_posix(),
                    "size": len(content), "sha256": digest,
                    "message_key": message["message_key"], "location": "reimbursement",
                })
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise

    try:
        reimbursements_root = outputs_root / "reimbursements"
        manifest_root = reimbursements_root / batch_id
        if outputs_root.is_symlink() or reimbursements_root.is_symlink() or manifest_root.is_symlink():
            raise MailProviderError("报销输出目录不能是符号链接")
        manifest_root.mkdir(parents=True, exist_ok=True)
        actual_manifest_root = manifest_root.resolve()
        if not actual_manifest_root.is_relative_to(controlled_outputs) or actual_manifest_root == controlled_outputs:
            raise MailProviderError("报销输出目录越出受控 outputs 范围")
        lines = ["# 邮箱报销材料清单", "", f"导入时间：{_now()}", f"邮箱：{config['email_address']}", ""]
        for message in messages:
            lines.extend([
                f"## {message['subject']}", f"- 发件人：{message['sender']}",
                f"- 邮件时间：{message['received_at'] or '未知'}", "- 已导入附件：",
            ])
            for item in materials:
                if item["message_key"] == message["message_key"]:
                    lines.append(f"  - {item['name']} → `{item['path']}`")
            lines.append("")
        manifest = manifest_root / "material-list.md"
        _atomic_replace(manifest, ("\n".join(lines) + "\n").encode("utf-8"))
        actual_manifest = manifest.resolve(strict=True)
        if not actual_manifest.is_relative_to(actual_manifest_root) or actual_manifest == actual_manifest_root:
            raise MailProviderError("报销材料清单越出受控批次目录")
        manifest_relative = actual_manifest.relative_to(resolved_project_root).as_posix()
        record_path = resolved_project_root / ".pi" / "director-runtime" / "reimbursement-batches" / f"{batch_id}.json"
        created_at = _now()
        if record_path.is_file() and not record_path.is_symlink() and record_path.stat().st_size <= 256_000:
            try:
                previous = json.loads(record_path.read_text(encoding="utf-8"))
                if isinstance(previous, dict) and isinstance(previous.get("created_at"), str):
                    created_at = previous["created_at"]
            except (OSError, json.JSONDecodeError):
                pass
        _atomic_json(record_path, {
            "schema_version": "1.0", "batch_id": batch_id,
            "created_at": created_at, "updated_at": _now(),
            "mailbox": config["email_address"], "message_count": len(messages),
            "manifest_path": manifest_relative,
            "messages": [
                {
                    "message_key": message["message_key"], "subject": message["subject"],
                    "sender": message["sender"], "received_at": message["received_at"],
                }
                for message in messages
            ],
            "materials": [{key: value for key, value in item.items() if key != "message_key"} for item in materials],
        })
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return {
        "batch_id": batch_id,
        "message_count": len(messages), "attachment_count": len(materials),
        "materials": [{key: value for key, value in item.items() if key != "message_key"} for item in materials],
        "manifest_path": manifest_relative,
        "message": f"已从 {len(messages)} 封邮件导入 {len(materials)} 个报销附件，保存在独立报销材料库。",
    }
