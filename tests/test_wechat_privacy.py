"""Private-directory tests use only newly created synthetic paths."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_platform import wechat_privacy as privacy
from agent_platform.wechat_store import WechatStoreError


class _PrivatePathMixin:
    def setUp(self):
        # The system temp directory may intentionally be shared.  A unique
        # child under the current user's profile gives the native Windows
        # checks a secure ancestor chain while remaining entirely synthetic.
        self.temporary = tempfile.TemporaryDirectory(
            prefix="wechat-privacy-test-", dir=Path.home()
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        privacy.ensure_private_directory(self.root)

    def assert_private_error(self, action, *, secret: str | None = None):
        with self.assertRaises(WechatStoreError) as caught:
            action()
        self.assertEqual("UNSAFE_PATH", caught.exception.code)
        self.assertEqual(privacy._ERROR_MESSAGE, str(caught.exception))
        self.assertNotIn("S-1-", str(caught.exception))
        if secret:
            self.assertNotIn(secret, str(caught.exception))

    def test_create_verify_repair_and_collision(self):
        target = self.root / "private"
        self.assertEqual(target, privacy.ensure_private_directory(target))
        self.assertEqual(target, privacy.verify_private_directory(target))
        self.assertEqual(target, privacy.ensure_private_directory(target))

        collision = self.root / "collision"
        collision.write_bytes(b"do-not-replace")
        self.assert_private_error(lambda: privacy.ensure_private_directory(collision),
                                  secret=str(collision))
        self.assertEqual(b"do-not-replace", collision.read_bytes())

    def test_invalid_and_relative_paths_are_sanitised(self):
        self.assert_private_error(lambda: privacy.ensure_private_directory("relative/path"),
                                  secret="relative/path")
        self.assert_private_error(lambda: privacy.verify_private_directory(self.root / "missing"),
                                  secret=str(self.root / "missing"))

    def test_symlink_or_junction_target_is_rejected(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        if os.name == "nt":
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(real)],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                self.skipTest("directory junction creation is unavailable")
        else:
            link.symlink_to(real, target_is_directory=True)
        try:
            self.assert_private_error(lambda: privacy.ensure_private_directory(link),
                                      secret=str(link))
            self.assert_private_error(lambda: privacy.verify_private_directory(link),
                                      secret=str(link))
        finally:
            if link.exists() or link.is_symlink():
                os.rmdir(link) if os.name == "nt" else link.unlink()

    def test_new_directory_is_removed_if_hardening_fails(self):
        target = self.root / "rollback"
        helper = "_win_set_private_dacl" if os.name == "nt" else "_posix_check_target"
        with patch.object(privacy, helper, side_effect=RuntimeError("secret-path S-1-5-21")):
            self.assert_private_error(lambda: privacy.ensure_private_directory(target),
                                      secret="secret-path")
        self.assertFalse(target.exists())

    def test_private_file_creation_is_exclusive_and_verified(self):
        target = self.root / "created.sqlite3"
        self.assertEqual(target, privacy.create_private_file(target))
        self.assertEqual(target, privacy.verify_private_file(target))
        self.assertTrue(privacy.verify_private_file_if_present(target))
        self.assertFalse(
            privacy.verify_private_file_if_present(self.root / "vanished.sqlite3")
        )
        target.write_bytes(b"preserve-existing")

        self.assertFalse(privacy.create_private_file_if_absent(target))
        self.assert_private_error(lambda: privacy.create_private_file(target))
        self.assertEqual(b"preserve-existing", target.read_bytes())

    def test_new_file_is_removed_if_hardening_fails(self):
        target = self.root / "rollback.sqlite3"
        helper = "_win_set_private_file_dacl" if os.name == "nt" else "_posix_check_file"
        with patch.object(privacy, helper, side_effect=RuntimeError("secret-path S-1-5-21")):
            self.assert_private_error(lambda: privacy.create_private_file_if_absent(target),
                                      secret="secret-path")
        self.assertFalse(target.exists())

    def test_private_file_identity_changes_after_replacement(self):
        target = self.root / "identity.sqlite3"
        privacy.create_private_file(target)
        with privacy.hold_private_file(target) as before:
            target.unlink()
            privacy.create_private_file(target)
            self.assertNotEqual(before, privacy.private_file_identity(target))

    def test_if_present_rejects_persistent_identity_churn(self):
        target = self.root / "churning.sqlite3"
        with patch.object(
            privacy,
            "_private_file_identity_if_present",
            side_effect=privacy._PrivateFileChanged(),
        ) as identity:
            self.assert_private_error(
                lambda: privacy.verify_private_file_if_present(target)
            )
        self.assertEqual(3, identity.call_count)


@unittest.skipUnless(os.name == "nt", "native Windows ACL tests")
class WindowsPrivatePathTests(_PrivatePathMixin, unittest.TestCase):
    @staticmethod
    def _set_sddl(path: Path, sddl: str, *, directory: bool) -> None:
        opener = privacy._win_open_directory if directory else privacy._win_open_file
        handle = opener(path, privacy._READ_CONTROL | privacy._WRITE_DAC)
        descriptor = ctypes.c_void_p()
        try:
            ok = privacy._advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, privacy._SECURITY_DESCRIPTOR_REVISION,
                ctypes.byref(descriptor), None,
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            present = privacy.wintypes.BOOL()
            defaulted = privacy.wintypes.BOOL()
            dacl = ctypes.c_void_p()
            ok = privacy._advapi.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl),
                ctypes.byref(defaulted),
            )
            if not ok or not present.value or not dacl.value:
                raise ctypes.WinError(ctypes.get_last_error())
            status = privacy._advapi.SetSecurityInfo(
                handle, privacy._SE_FILE_OBJECT,
                privacy._DACL_SECURITY_INFORMATION |
                privacy._PROTECTED_DACL_SECURITY_INFORMATION,
                None, None, dacl, None,
            )
            if status:
                raise OSError(status, "SetSecurityInfo failed")
        finally:
            if descriptor.value:
                privacy._kernel.LocalFree(descriptor)
            privacy._win_close(handle)

    def test_only_if_present_identity_verification_shares_delete(self):
        target = self.root / "volatile-wal"
        result = ((1, 2, 3), ())
        with patch.object(privacy, "_windows_hold_private_file", return_value=result) as hold:
            self.assertEqual(result[0], privacy._windows_private_file_identity(target))
            hold.assert_called_once_with(target)

        with patch.object(privacy, "_windows_hold_private_file", return_value=result) as hold:
            self.assertEqual(
                result[0], privacy._windows_private_file_identity_if_present(target)
            )
            hold.assert_called_once_with(target, share_delete=True)

    def test_if_present_retries_native_delete_pending_as_missing(self):
        target = self.root / "delete-pending-wal"
        privacy.create_private_file(target)
        handle = privacy._win_open_file(
            target, privacy._DELETE, share_delete=True
        )
        closed = False
        try:
            self.assertTrue(privacy._win_mark_created_for_deletion(handle))

            def finish_delete(_seconds):
                nonlocal closed
                privacy._win_close(handle)
                closed = True

            with patch.object(privacy.time, "sleep", side_effect=finish_delete):
                self.assertFalse(privacy.verify_private_file_if_present(target))
        finally:
            if not closed:
                privacy._win_close(handle)

    def test_native_protected_dacl_has_only_user_and_system(self):
        target = self.root / "native"
        privacy.ensure_private_directory(target)
        handle = privacy._win_open_directory(target, privacy._READ_CONTROL)
        try:
            owner, protected, aces = privacy._win_security_snapshot(handle)
        finally:
            privacy._win_close(handle)

        current = privacy._win_current_sid()
        system = privacy._win_well_known_sid(22)
        world = privacy._win_well_known_sid(1)
        authenticated_users = privacy._win_well_known_sid(17)
        self.assertEqual(current, owner)
        self.assertTrue(protected)
        self.assertEqual({current, system}, {ace[3] for ace in aces})
        self.assertNotIn(world, {ace[3] for ace in aces})
        self.assertNotIn(authenticated_users, {ace[3] for ace in aces})
        self.assertTrue(all(ace[0] == privacy._ACCESS_ALLOWED_ACE_TYPE and
                            ace[1] == (privacy._OBJECT_INHERIT_ACE |
                                       privacy._CONTAINER_INHERIT_ACE) and
                            ace[2] == privacy._FILE_ALL_ACCESS for ace in aces))

    def test_volume_root_is_included_in_the_parent_boundary(self):
        target = self.root / "nested" / "private"
        parents = privacy._win_parent_paths(target)
        self.assertEqual(Path(target.anchor), parents[0])
        self.assertEqual(target.parent, parents[-1])

        root_handle = privacy._win_open_directory(Path(target.anchor),
                                                  privacy._READ_CONTROL)
        try:
            owner, _protected, _aces = privacy._win_security_snapshot(root_handle)
            # Standard system volumes may be owned by TrustedInstaller rather
            # than SYSTEM/Administrators; that owner is explicitly trusted,
            # while the root DACL is still checked for replacement rights.
            trusted_owners = {
                privacy._win_current_sid(),
                privacy._win_well_known_sid(22),
                privacy._win_well_known_sid(26),
                privacy._win_sid_from_text(privacy._TRUSTED_INSTALLER_SID),
            }
            self.assertIn(owner, trusted_owners)
            privacy._win_check_parent(root_handle, privacy._win_current_sid())
        finally:
            privacy._win_close(root_handle)

    def test_owner_rights_applies_only_after_owner_is_trusted(self):
        current = privacy._win_current_sid()
        owner_rights = privacy._win_sid_from_text(privacy._OWNER_RIGHTS_SID)
        ace = (privacy._ACCESS_ALLOWED_ACE_TYPE, 0,
               privacy._DANGEROUS_PARENT_ACCESS, owner_rights)
        with patch.object(privacy, "_win_security_snapshot",
                          return_value=(current, True, [ace])):
            privacy._win_check_parent(1, current)
        with patch.object(privacy, "_win_security_snapshot",
                          return_value=(owner_rights, True, [])):
            with self.assertRaises(privacy._PrivacyFailure):
                privacy._win_check_parent(1, current)

    def test_file_disposition_abi_and_rollback_fallbacks(self):
        self.assertEqual(1, ctypes.sizeof(privacy._FILE_DISPOSITION_INFO))

        fallback = self.root / "fallback-delete"
        with (patch.object(privacy, "_win_set_private_dacl",
                           side_effect=RuntimeError("hardening failed")),
              patch.object(privacy._kernel, "SetFileInformationByHandle",
                           return_value=False)):
            self.assert_private_error(lambda: privacy.ensure_private_directory(fallback))
        self.assertFalse(fallback.exists())

        double_failure = self.root / "double-failure"
        with (patch.object(privacy, "_win_set_private_dacl",
                           side_effect=RuntimeError("hardening failed")),
              patch.object(privacy._kernel, "SetFileInformationByHandle",
                           return_value=False),
              patch.object(privacy._kernel, "RemoveDirectoryW", return_value=False)):
            self.assert_private_error(
                lambda: privacy.ensure_private_directory(double_failure)
            )
        # The rollback failure is surfaced, never accepted as a successful
        # private directory.  Its inherited ACL came from the already-private
        # synthetic parent and teardown removes this test-owned residue.
        self.assertTrue(double_failure.is_dir())

    def test_default_system_temp_shape_follows_ancestor_replacement_acl(self):
        # This is the exact shape used by integration tests.  Some Windows
        # hosts configure %TEMP% as per-user; hardened/sandboxed hosts may
        # grant other principals DELETE_CHILD there.  The result must track
        # the native ACL and must never create through an unsafe parent.
        with tempfile.TemporaryDirectory(prefix="wechat-system-temp-") as temporary:
            data = Path(temporary) / "data"
            data.mkdir()
            target = data / "wechat"
            current = privacy._win_current_sid()
            trusted_owners = {
                current,
                privacy._win_well_known_sid(22),
                privacy._win_well_known_sid(26),
                privacy._win_sid_from_text(privacy._TRUSTED_INSTALLER_SID),
            }
            trusted_access = trusted_owners | {
                privacy._win_sid_from_text(privacy._OWNER_RIGHTS_SID),
            }
            parent = target.parent
            current_path = Path(parent.anchor)
            paths = [current_path]
            for component in parent.parts[1:]:
                current_path = current_path / component
                paths.append(current_path)

            unsafe = False
            for parent_path in paths:
                handle = privacy._win_open_directory(parent_path, privacy._READ_CONTROL)
                try:
                    owner, _protected, aces = privacy._win_security_snapshot(handle)
                finally:
                    privacy._win_close(handle)
                if owner not in trusted_owners:
                    unsafe = True
                    break
                for ace_type, ace_flags, mask, sid in aces:
                    if (ace_type not in privacy._ACCESS_ALLOWED_ACE_TYPES or
                            sid in trusted_access):
                        continue
                    applies = not (ace_flags & privacy._INHERIT_ONLY_ACE)
                    replaces = applies and bool(mask & privacy._DANGEROUS_PARENT_ACCESS)
                    if replaces:
                        unsafe = True
                        break
                if unsafe:
                    break

            if not unsafe:
                self.skipTest("this host's default system temp ancestor chain is private")
            self.assert_private_error(lambda: privacy.ensure_private_directory(target))
            self.assertFalse(target.exists())

    def test_inherited_directory_is_repaired_not_implicitly_accepted(self):
        target = self.root / "inherited-directory"
        target.mkdir()
        self.assert_private_error(lambda: privacy.verify_private_directory(target))
        privacy.ensure_private_directory(target)
        privacy.verify_private_directory(target)

    def test_untrusted_delete_or_acl_right_on_parent_is_rejected(self):
        unsafe = self.root / "unsafe-parent"
        unsafe.mkdir()
        current_text = privacy._win_sid_text(privacy._win_current_sid())
        self._set_sddl(
            unsafe,
            f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{current_text})(A;;GA;;;AU)",
            directory=True,
        )
        child = unsafe / "must-not-exist"
        self.assert_private_error(lambda: privacy.ensure_private_directory(child),
                                  secret=str(child))
        self.assertFalse(child.exists())

    def test_noninheriting_create_right_is_not_mistaken_for_replacement(self):
        parent = self.root / "create-only-parent"
        parent.mkdir()
        current_text = privacy._win_sid_text(privacy._win_current_sid())
        self._set_sddl(
            parent,
            # 0x4 is FILE_ADD_SUBDIRECTORY on a directory.  With no CI flag it
            # neither deletes an existing child nor leaks onto the new child.
            f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{current_text})(A;;0x4;;;AU)",
            directory=True,
        )
        privacy.ensure_private_directory(parent / "private-child")

    def test_inheritable_untrusted_right_is_excluded_atomically(self):
        parent = self.root / "inheritable-parent"
        parent.mkdir()
        current_text = privacy._win_sid_text(privacy._win_current_sid())
        self._set_sddl(
            parent,
            f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{current_text})(A;CI;GR;;;AU)",
            directory=True,
        )
        child = parent / "private-child"
        privacy.ensure_private_directory(child)
        privacy.verify_private_directory(child)
        handle = privacy._win_open_directory(child, privacy._READ_CONTROL)
        try:
            _owner, protected, aces = privacy._win_security_snapshot(handle)
        finally:
            privacy._win_close(handle)
        self.assertTrue(protected)
        self.assertNotIn(
            privacy._win_well_known_sid(17), {ace[3] for ace in aces},
        )

    def test_private_inherited_file_and_hard_link_boundary(self):
        target = self.root / "index.sqlite3"
        target.write_bytes(b"synthetic")
        self.assertEqual(target, privacy.verify_private_file(target))
        handle = privacy._win_open_file(target, privacy._READ_CONTROL)
        try:
            owner, protected, aces = privacy._win_security_snapshot(handle)
        finally:
            privacy._win_close(handle)
        self.assertEqual(privacy._win_current_sid(), owner)
        self.assertFalse(protected)
        self.assertTrue(all(ace[1] == privacy._INHERITED_ACE for ace in aces))

        alias = self.root / "alias.sqlite3"
        os.link(target, alias)
        try:
            self.assert_private_error(lambda: privacy.verify_private_file(target))
        finally:
            alias.unlink()

    def test_file_with_extra_authenticated_users_ace_is_rejected(self):
        target = self.root / "legacy.db"
        target.write_bytes(b"synthetic")
        current_text = privacy._win_sid_text(privacy._win_current_sid())
        self._set_sddl(
            target,
            f"D:P(A;;FA;;;SY)(A;;FA;;;{current_text})(A;;GR;;;AU)",
            directory=False,
        )
        self.assert_private_error(lambda: privacy.create_private_file(target),
                                  secret=str(target))
        self.assert_private_error(lambda: privacy.verify_private_file(target),
                                  secret=str(target))


@unittest.skipIf(os.name == "nt", "POSIX permission tests")
class PosixPrivatePathTests(_PrivatePathMixin, unittest.TestCase):
    def test_posix_anchor_is_checked_first(self):
        target = self.root / "anchor-check"
        original = privacy._posix_check_parent
        checked = []

        def record(info):
            checked.append((info.st_dev, info.st_ino))
            original(info)

        with patch.object(privacy, "_posix_check_parent", side_effect=record):
            parent_fd, _name = privacy._posix_open_parent(target)
            os.close(parent_fd)
        root_info = os.stat(target.anchor)
        self.assertEqual((root_info.st_dev, root_info.st_ino), checked[0])

    def test_if_present_treats_unlink_after_open_as_missing(self):
        target = self.root / "transient-wal"
        privacy.create_private_file(target)
        original_stat = privacy.os.stat

        def disappear_after_open(path, *args, **kwargs):
            if path == target.name and kwargs.get("dir_fd") is not None:
                raise FileNotFoundError(path)
            return original_stat(path, *args, **kwargs)

        with patch.object(privacy.os, "stat", side_effect=disappear_after_open):
            self.assertFalse(privacy.verify_private_file_if_present(target))

    def test_if_present_retries_safe_entry_replacement(self):
        target = self.root / "transient-replacement"
        privacy.create_private_file(target)
        original_stat = privacy.os.stat
        replaced = False

        def replace_after_open(path, *args, **kwargs):
            nonlocal replaced
            if path == target.name and kwargs.get("dir_fd") is not None and not replaced:
                replaced = True
                target.unlink()
                privacy.create_private_file(target)
            return original_stat(path, *args, **kwargs)

        with patch.object(privacy.os, "stat", side_effect=replace_after_open):
            self.assertTrue(privacy.verify_private_file_if_present(target))

    def test_posix_rollback_failure_is_reported_and_residue_stays_0700(self):
        target = self.root / "rollback-failure"
        with (patch.object(privacy, "_posix_check_target",
                           side_effect=RuntimeError("hardening failed")),
              patch.object(privacy.os, "rmdir", side_effect=OSError("busy"))):
            self.assert_private_error(lambda: privacy.ensure_private_directory(target))
        self.assertTrue(target.is_dir())
        self.assertEqual(0o700, target.stat().st_mode & 0o777)

    def test_directory_mode_parent_and_sticky_boundaries(self):
        target = self.root / "private-mode"
        privacy.ensure_private_directory(target)
        self.assertEqual(0o700, target.stat().st_mode & 0o777)

        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        self.assert_private_error(lambda: privacy.ensure_private_directory(unsafe / "child"))

        sticky = self.root / "sticky"
        sticky.mkdir()
        sticky.chmod(0o1777)
        privacy.ensure_private_directory(sticky / "child")

    def test_private_file_permissions_and_hard_link_boundary(self):
        target = self.root / "index.sqlite3"
        target.write_bytes(b"synthetic")
        target.chmod(0o600)
        privacy.verify_private_file(target)
        target.chmod(0o644)
        self.assert_private_error(lambda: privacy.verify_private_file(target))
        target.chmod(0o600)

        alias = self.root / "alias.sqlite3"
        os.link(target, alias)
        try:
            self.assert_private_error(lambda: privacy.verify_private_file(target))
        finally:
            alias.unlink()

    def test_create_private_file_ignores_permissive_umask_and_rejects_legacy_mode(self):
        target = self.root / "new.sqlite3"
        previous = os.umask(0o022)
        try:
            privacy.create_private_file(target)
        finally:
            os.umask(previous)
        self.assertEqual(0o600, target.stat().st_mode & 0o777)

        legacy = self.root / "legacy.sqlite3"
        legacy.write_bytes(b"do-not-repair")
        legacy.chmod(0o644)
        self.assert_private_error(lambda: privacy.create_private_file(legacy))
        self.assertEqual(0o644, legacy.stat().st_mode & 0o777)
        self.assertEqual(b"do-not-repair", legacy.read_bytes())


if __name__ == "__main__":
    unittest.main()
