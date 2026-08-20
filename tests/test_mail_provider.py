from __future__ import annotations

import json
import platform
import socket
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from agent_platform.mail_provider import (
    MailProviderError,
    configure_mail_provider,
    import_reimbursement_mail,
    mail_settings_summary,
    normalize_mail_settings,
    search_reimbursement_mail,
    settings_path,
    _dpapi_protect,
    _dpapi_unprotect,
)


def public_resolver(host: str, port: int, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def message_bytes(subject: str = "差旅报销发票", *, attachment_name: str = "invoice.pdf", content: bytes = b"%PDF receipt") -> bytes:
    message = EmailMessage(policy=policy.default)
    message["Subject"] = subject
    message["From"] = "财务助手 <finance@example.com>"
    message["To"] = "sales@example.com"
    message["Date"] = "Wed, 19 Aug 2026 10:30:00 +0800"
    message["Message-ID"] = "<message-1@example.com>"
    message.set_content("这段邮件正文不应返回给工作台。")
    suffix = Path(attachment_name).suffix.lower()
    subtype = "pdf" if suffix == ".pdf" else "octet-stream"
    message.add_attachment(content, maintype="application", subtype=subtype, filename=attachment_name)
    return message.as_bytes()


class FakeMailbox:
    def __init__(self, messages: dict[str, bytes]):
        self.messages = messages
        self.readonly = None
        self.logged_out = False

    def select(self, mailbox: str, readonly: bool = False):
        self.readonly = readonly
        return "OK", [b""]

    def uid(self, command: str, *args):
        if command == "search":
            return "OK", [" ".join(self.messages).encode("ascii")]
        if command == "fetch":
            uid, query = str(args[0]), str(args[1])
            raw = self.messages[uid]
            if query == "(RFC822.SIZE)":
                return "OK", [(f"1 (UID {uid} RFC822.SIZE {len(raw)})".encode("ascii"), b"")]
            if query == "(BODY.PEEK[])":
                return "OK", [(f"1 (UID {uid} BODY[] {{{len(raw)}}})".encode("ascii"), raw)]
        return "NO", []

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]


class MailProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = message_bytes()
        self.mailbox = FakeMailbox({"101": self.raw})
        self.payload = {
            "provider": "qq",
            "email_address": "sales@example.com",
            "username": "sales@example.com",
            "allow_private_network": False,
        }

    def tearDown(self):
        self.temporary.cleanup()

    def configure(self, mailbox: FakeMailbox | None = None):
        selected = mailbox or self.mailbox
        with patch("agent_platform.mail_provider.platform.system", return_value="Linux"):
            result = configure_mail_provider(
                self.root,
                self.payload,
                "client-app-password",
                resolver=public_resolver,
                connector=lambda _config, _password: selected,
            )
        self.assertTrue(selected.readonly)
        return result

    def test_presets_fix_host_and_private_custom_server_requires_opt_in(self):
        normalized = normalize_mail_settings(
            {**self.payload, "host": "attacker.example"}, resolver=public_resolver
        )
        self.assertEqual(normalized["host"], "imap.qq.com")
        self.assertEqual(normalized["port"], 993)
        with self.assertRaises(MailProviderError):
            normalize_mail_settings(
                {
                    "provider": "custom", "email_address": "sales@example.com",
                    "username": "sales@example.com", "host": "mail.internal.example",
                    "allow_private_network": False,
                },
                resolver=lambda *_args, **_kwargs: [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 993))
                ],
            )

    def test_configuration_keeps_credential_out_of_plaintext_metadata(self):
        result = self.configure()
        self.assertTrue(result["configured"])
        raw_record = settings_path(self.root).read_text(encoding="utf-8")
        self.assertNotIn("client-app-password", raw_record)
        self.assertNotIn("password", json.loads(raw_record)["config"])
        self.assertTrue(mail_settings_summary(self.root)["configured"])

    @unittest.skipUnless(platform.system().lower() == "windows", "Windows DPAPI only")
    def test_windows_dpapi_credential_round_trip(self):
        entropy = b"agent4market-mail-test-entropy"
        encrypted = _dpapi_protect(b"test-mail-credential", entropy)
        self.assertNotIn(b"test-mail-credential", encrypted)
        self.assertEqual(_dpapi_unprotect(encrypted, entropy), b"test-mail-credential")

    def test_search_is_read_only_and_returns_metadata_without_body(self):
        self.configure()
        mailbox = FakeMailbox({"101": self.raw})
        with patch("agent_platform.mail_provider.platform.system", return_value="Linux"):
            result = search_reimbursement_mail(
                self.root,
                {"date_from": "2026-08-01", "date_to": "2026-08-20", "query": "发票"},
                connector=lambda _config, _password: mailbox,
            )
        self.assertTrue(mailbox.readonly)
        self.assertTrue(mailbox.logged_out)
        self.assertEqual(len(result["messages"]), 1)
        record = result["messages"][0]
        self.assertEqual(record["attachments"][0]["name"], "invoice.pdf")
        self.assertNotIn("body", record)
        self.assertNotIn("这段邮件正文", json.dumps(result, ensure_ascii=False))

    def test_import_writes_selected_attachment_and_manifest_idempotently(self):
        self.configure()
        with patch("agent_platform.mail_provider.platform.system", return_value="Linux"):
            found = search_reimbursement_mail(
                self.root,
                {"date_from": "2026-08-01", "date_to": "2026-08-20", "query": ""},
                connector=lambda _config, _password: FakeMailbox({"101": self.raw}),
            )["messages"][0]
            first = import_reimbursement_mail(
                self.root, self.root / "inputs", self.root / "outputs", "project-default",
                [{"uid": found["uid"], "message_key": found["message_key"]}],
                connector=lambda _config, _password: FakeMailbox({"101": self.raw}),
            )
            second = import_reimbursement_mail(
                self.root, self.root / "inputs", self.root / "outputs", "project-default",
                [{"uid": found["uid"], "message_key": found["message_key"]}],
                connector=lambda _config, _password: FakeMailbox({"101": self.raw}),
            )
        material = self.root / first["materials"][0]["path"]
        self.assertEqual(material.read_bytes(), b"%PDF receipt")
        self.assertTrue((self.root / first["manifest_path"]).is_file())
        self.assertTrue((self.root / second["manifest_path"]).is_file())
        self.assertEqual(len(list((self.root / "inputs" / "projects" / "project-default").glob("*.pdf"))), 1)

    def test_changed_message_is_rejected_before_any_material_is_written(self):
        self.configure()
        with patch("agent_platform.mail_provider.platform.system", return_value="Linux"):
            found = search_reimbursement_mail(
                self.root,
                {"date_from": "2026-08-01", "date_to": "2026-08-20", "query": ""},
                connector=lambda _config, _password: FakeMailbox({"101": self.raw}),
            )["messages"][0]
            with self.assertRaises(MailProviderError):
                import_reimbursement_mail(
                    self.root, self.root / "inputs", self.root / "outputs", "project-default",
                    [{"uid": "101", "message_key": found["message_key"]}],
                    connector=lambda _config, _password: FakeMailbox({"101": message_bytes("已被替换的邮件")}),
                )
        project_root = self.root / "inputs" / "projects" / "project-default"
        self.assertFalse(project_root.exists() and any(project_root.iterdir()))

    def test_unsafe_attachment_is_not_exposed_or_imported(self):
        mixed = EmailMessage(policy=policy.default)
        mixed["Subject"] = "报销附件"
        mixed["From"] = "finance@example.com"
        mixed["To"] = "sales@example.com"
        mixed["Date"] = "Wed, 19 Aug 2026 10:30:00 +0800"
        mixed["Message-ID"] = "<mixed@example.com>"
        mixed.set_content("body")
        mixed.add_attachment(b"safe", maintype="application", subtype="pdf", filename="invoice.pdf")
        mixed.add_attachment(b"unsafe", maintype="application", subtype="octet-stream", filename="run.exe")
        raw = mixed.as_bytes()
        self.configure()
        with patch("agent_platform.mail_provider.platform.system", return_value="Linux"):
            found = search_reimbursement_mail(
                self.root,
                {"date_from": "2026-08-01", "date_to": "2026-08-20", "query": ""},
                connector=lambda _config, _password: FakeMailbox({"202": raw}),
            )["messages"][0]
            imported = import_reimbursement_mail(
                self.root, self.root / "inputs", self.root / "outputs", "project-default",
                [{"uid": "202", "message_key": found["message_key"]}],
                connector=lambda _config, _password: FakeMailbox({"202": raw}),
            )
        self.assertEqual([item["name"] for item in found["attachments"]], ["invoice.pdf"])
        self.assertEqual(imported["attachment_count"], 1)
        self.assertFalse(list((self.root / "inputs").rglob("*.exe")))


if __name__ == "__main__":
    unittest.main()
