"""Loopback identity fixtures; never access a running workbench or WeChat."""
from __future__ import annotations

import ctypes
import os
import socket
import struct
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from agent_platform import local_http_security as security


def synthetic_api():
    api = Mock()
    api.kernel.GetCurrentProcess.return_value = "current"
    api.kernel.OpenProcess.return_value = "selected"
    api.connection_owner.return_value = (123, 1000)
    api.identity.side_effect = lambda handle: (b"same-user", 2, 100 if handle == "current" else 500)
    return api


class LocalHTTPPeerTests(unittest.TestCase):
    def connection(self):
        connection = Mock()
        connection.getpeername.return_value = ("127.0.0.1", 54321)
        connection.getsockname.return_value = ("127.0.0.1", 8765)
        return connection

    def test_exact_tuple_same_user_and_minimal_rights(self):
        api = synthetic_api()
        security.require_windows_peer(self.connection(), api=api)
        api.kernel.OpenProcess.assert_called_once_with(0x1000, False, 123)
        self.assertEqual(2, api.connection_owner.call_count)
        api.connection_owner.assert_called_with(("127.0.0.1", 54321), ("127.0.0.1", 8765))
        api.kernel.CloseHandle.assert_called_once_with("selected")

    def test_mismatched_users_sessions_reused_pid_and_connection_fail_closed(self):
        for identity in ((b"other-user", 2, 500), (b"same-user", 3, 500),
                         (b"same-user", 2, 1001), (b"same-user", 2, 0)):
            with self.subTest(identity=identity):
                api = synthetic_api()
                api.identity.side_effect = lambda handle: (b"same-user", 2, 100) if handle == "current" else identity
                with self.assertRaises(security.LocalAccessError):
                    security.require_windows_peer(self.connection(), api=api)
                api.kernel.CloseHandle.assert_called_once_with("selected")
        for replaced in ((124, 1000), (123, 1002)):
            with self.subTest(replaced=replaced):
                api = synthetic_api(); api.connection_owner.side_effect = [(123, 1000), replaced]
                with self.assertRaises(security.LocalAccessError):
                    security.require_windows_peer(self.connection(), api=api)
                api.kernel.CloseHandle.assert_called_once_with("selected")

    def test_missing_socket_metadata_denied_access_and_unreadable_token(self):
        api = synthetic_api(); api.kernel.OpenProcess.return_value = None
        with self.assertRaises(security.LocalAccessError):
            security.require_windows_peer(self.connection(), api=api)
        api.kernel.CloseHandle.assert_not_called()
        for error in (OSError("unavailable"), security.LocalAccessError("unavailable")):
            api = synthetic_api(); api.identity.side_effect = error
            with self.assertRaises(security.LocalAccessError):
                security.require_windows_peer(self.connection(), api=api)
            api.kernel.CloseHandle.assert_called_once_with("selected")
        with self.assertRaises(security.LocalAccessError):
            security.require_windows_peer(None, api=synthetic_api())
        for peer in (("192.0.2.1", 4567), ("::1", 4567, 0, 0), ("127.0.0.1", 0)):
            connection = self.connection(); connection.getpeername.return_value = peer
            with self.assertRaises(security.LocalAccessError):
                security.require_windows_peer(connection, api=synthetic_api())

    def test_tcp_table_alignment_byte_order_exact_direction_and_invalid_rows(self):
        row = security._TcpRow(state=5, local_addr=0x0100007F, local_port=socket.htons(54321),
                               remote_addr=0x0100007F, remote_port=socket.htons(8765), pid=123, created=1000)

        def query_table(data):
            api = security._WindowsPeerAPI.__new__(security._WindowsPeerAPI)
            api.ip = Mock()
            def query(buffer, size, ordered, family, table_class, reserved):
                self.assertEqual((False, socket.AF_INET, 8, 0), (ordered, family, table_class, reserved))
                size._obj.value = len(data)
                if buffer is None: return 122
                ctypes.memmove(buffer, data, len(data))
                return 0
            api.ip.GetExtendedTcpTable.side_effect = query
            return api.connection_owner(("127.0.0.1", 54321), ("127.0.0.1", 8765))

        header = struct.pack("<I", 1) + b"\0" * (security._TcpTable.rows.offset - 4)
        self.assertEqual((123, 1000), query_table(header + bytes(row)))
        for field, value in (("state", 11), ("pid", 0), ("created", 0), ("local_port", socket.htons(8765)),
                             ("remote_port", socket.htons(54321)), ("local_addr", 0x0200007F)):
            changed = security._TcpRow.from_buffer_copy(bytes(row)); setattr(changed, field, value)
            with self.subTest(field=field), self.assertRaises(security.LocalAccessError):
                query_table(header + bytes(changed))
        duplicate = struct.pack("<I", 2) + header[4:] + bytes(row) * 2
        for data in (duplicate, header + bytes(row)[:-1], b"\x01\0", struct.pack("<I", 999999) + header[4:] + bytes(row)):
            with self.subTest(length=len(data)), self.assertRaises(security.LocalAccessError):
                query_table(data)

    def test_tcp_table_unavailable_or_unbounded_never_falls_back_to_host(self):
        for result, size in ((5, 0), (122, 20 * 1024 * 1024), (122, 0), (0, 1000)):
            api = security._WindowsPeerAPI.__new__(security._WindowsPeerAPI); api.ip = Mock()
            def query(buffer, length, *_):
                length._obj.value = size
                return result
            api.ip.GetExtendedTcpTable.side_effect = query
            with self.subTest(result=result, size=size), self.assertRaises(security.LocalAccessError):
                api.connection_owner(("127.0.0.1", 54321), ("127.0.0.1", 8765))
            self.assertLessEqual(api.ip.GetExtendedTcpTable.call_count, 3)

    def test_macos_exact_tuple_and_same_uid_are_required(self):
        api = Mock()
        api.connection_owner.side_effect = [(456, 501, 200), (456, 501, 200)]
        api.identity.side_effect = lambda pid: (501, 100) if pid == 999 else (501, 200)
        with patch.object(security.os, "getpid", return_value=999):
            security.require_macos_peer(self.connection(), api=api)
        self.assertEqual(2, api.connection_owner.call_count)

        for identity in ((500, 200), (501, 201)):
            api = Mock()
            api.connection_owner.return_value = (456, 501, 200)
            api.identity.side_effect = lambda pid, value=identity: (501, 100) if pid == 999 else value
            with patch.object(security.os, "getpid", return_value=999), self.assertRaises(security.LocalAccessError):
                security.require_macos_peer(self.connection(), api=api)

    def test_macos_replaced_connection_and_non_loopback_fail_closed(self):
        api = Mock()
        api.connection_owner.side_effect = [(456, 501, 200), (457, 501, 200)]
        api.identity.side_effect = lambda pid: (501, 100) if pid == 999 else (501, 200)
        with patch.object(security.os, "getpid", return_value=999), self.assertRaises(security.LocalAccessError):
            security.require_macos_peer(self.connection(), api=api)
        for peer in (("::1", 54321), ("192.0.2.1", 54321), ("127.0.0.1", 0)):
            connection = self.connection(); connection.getpeername.return_value = peer
            with self.assertRaises(security.LocalAccessError):
                security.require_macos_peer(connection, api=Mock())

    def test_macos_capability_is_enabled_only_for_64_bit_darwin(self):
        with patch.object(security.sys, "platform", "darwin"), patch.object(security.os, "name", "posix"), patch.object(security.ctypes, "sizeof", return_value=8):
            self.assertTrue(security.wechat_http_supported())
        with patch.object(security.sys, "platform", "linux"), patch.object(security.os, "name", "posix"), patch.object(security.ctypes, "sizeof", return_value=8):
            self.assertFalse(security.wechat_http_supported())

    @unittest.skipUnless(os.name == "nt", "native Windows TCP/token API")
    def test_native_windows_api_only_on_test_owned_tcp_child(self):
        self.assertEqual(160, ctypes.sizeof(security._TcpRow))
        self.assertEqual(8, security._TcpTable.rows.offset)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        child = accepted = None
        try:
            listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(10)
            code = ("import socket,sys; s=socket.create_connection(('127.0.0.1',int(sys.argv[1]))); "
                    "print('connected',flush=True); sys.stdin.readline(); s.close()")
            child = subprocess.Popen([sys.executable, "-u", "-c", code, str(listener.getsockname()[1])],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            accepted, _ = listener.accept()
            self.assertEqual("connected", child.stdout.readline().strip())
            api = security._WindowsPeerAPI()
            owner = api.connection_owner(accepted.getpeername(), accepted.getsockname())
            self.assertEqual(child.pid, owner[0])
            security.require_windows_peer(accepted, api=api)
        finally:
            if accepted is not None: accepted.close()
            listener.close()
            if child is not None:
                try:
                    child.communicate("quit\n", timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill(); child.communicate(timeout=5)

    @unittest.skipUnless(sys.platform == "darwin", "native macOS lsof/libproc API")
    def test_native_macos_api_only_on_test_owned_tcp_child(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        child = accepted = None
        try:
            listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(10)
            code = ("import socket,sys; s=socket.create_connection(('127.0.0.1',int(sys.argv[1]))); "
                    "print('connected',flush=True); sys.stdin.readline(); s.close()")
            child = subprocess.Popen([sys.executable, "-u", "-c", code, str(listener.getsockname()[1])],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True)
            accepted, _ = listener.accept()
            self.assertEqual("connected", child.stdout.readline().strip())
            owner = security._MacPeerAPI().connection_owner(accepted.getpeername(), accepted.getsockname())
            self.assertEqual((child.pid, os.geteuid()), owner[:2])
            security.require_macos_peer(accepted)
        finally:
            if accepted is not None: accepted.close()
            listener.close()
            if child is not None:
                try:
                    child.communicate("quit\n", timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill(); child.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
