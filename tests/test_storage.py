"""私有存储回归；仅处理测试目录中的假配置，Windows 使用真实 NTFS ACL。"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('yaoyin_storage_test',
    Path(__file__).resolve().parents[1] / 'storage.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


class StorageFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='yaoyin-storage-')
        self.addCleanup(temporary.cleanup)
        # macOS 的系统临时目录可能通过 /var 链接，先固定测试根的实际路径。
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'private' / 'config.json'
        self.config = {'endpoint': 'https://api.example.test/v1/chat/completions',
                       'model': 'fake-model', 'api_key': 'fake-test-key',
                       'proxy': 'http://127.0.0.1:7890', 'text': '原文\n"\\\x00'}

    def assert_saved(self, expected):
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), expected)
        storage.check_private(self.path)
        self.assertEqual(list(self.path.parent.glob('.yaoyin-config-*')), [])


class StorageTests(StorageFixture, unittest.TestCase):
    def test_create_and_atomic_update_roundtrip(self):
        storage.write_config(self.config, self.path)
        self.assert_saved(self.config)
        updated = dict(self.config, model='updated-fake-model')
        storage.write_config(updated, self.path)
        self.assert_saved(updated)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_default_path_uses_runtime(self):
        runtime = types.SimpleNamespace(config_path=lambda: self.path)
        with mock.patch.dict(sys.modules, {'runtime': runtime}):
            storage.write_config(self.config)
        self.assert_saved(self.config)

    def test_invalid_json_data_creates_nothing(self):
        for invalid in ([], {'number': float('nan')}, {'object': object()}):
            with self.subTest(value=type(invalid).__name__):
                with self.assertRaises((ValueError, TypeError)):
                    storage.write_config(invalid, self.path)
                self.assertFalse(self.path.parent.exists())

    def test_missing_config_is_not_treated_as_private(self):
        storage.write_config(self.config, self.path)
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            storage.check_private(self.path)

    def test_replace_failure_keeps_original_and_removes_temporary(self):
        storage.write_config(self.config, self.path)
        if os.name == 'nt':
            _, _, files, _, _, _ = storage._win()
            patcher = mock.patch.object(files, 'MoveFileEx', side_effect=OSError('模拟写入失败'))
        else:
            patcher = mock.patch.object(storage.os, 'replace', side_effect=OSError('模拟写入失败'))
        with patcher, self.assertRaises(OSError):
            storage.write_config(dict(self.config, model='must-not-replace'), self.path)
        self.assert_saved(self.config)

    def test_hardlinked_config_is_rejected_without_modification(self):
        storage.write_config(self.config, self.path)
        alias = self.root / 'linked-config.json'
        os.link(self.path, alias)
        before = self.path.read_bytes()
        with self.assertRaises(PermissionError):
            storage.check_private(self.path)
        with self.assertRaises(PermissionError):
            storage.write_config({'api_key': 'new-fake-key'}, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(alias.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.yaoyin-config-*')), [])

    def test_symbolic_file_and_parent_are_rejected(self):
        storage.write_config(self.config, self.path)
        alias = self.root / 'alias'
        try:
            alias.symlink_to(self.path.parent, target_is_directory=True)
        except OSError as exc:
            if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
                self.skipTest('当前 Windows token 无创建符号链接权限；junction 另有真实测试')
            raise
        with self.assertRaises(PermissionError):
            storage.check_private(alias / self.path.name)
        with self.assertRaises(PermissionError):
            storage.write_config(self.config, alias / self.path.name)
        alias.unlink()
        alias.symlink_to(self.path)
        with self.assertRaises(PermissionError):
            storage.write_config(self.config, alias)
        self.assert_saved(self.config)


@unittest.skipIf(os.name == 'nt', 'Unix 权限检查')
class UnixStorageTests(StorageFixture, unittest.TestCase):
    def test_unsafe_file_or_directory_is_never_repaired_or_overwritten(self):
        storage.write_config(self.config, self.path)
        for target, unsafe, safe in ((self.path, 0o644, 0o600),
                                     (self.path.parent, 0o755, 0o700)):
            with self.subTest(target=target.name):
                target.chmod(unsafe)
                with self.assertRaises(PermissionError):
                    storage.check_private(self.path)
                with self.assertRaises(PermissionError):
                    storage.write_config({'api_key': 'new-fake-key'}, self.path)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), unsafe)
                self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), self.config)
                self.assertEqual(list(self.path.parent.glob('.yaoyin-config-*')), [])
                target.chmod(safe)

    def test_wrong_owner_is_rejected(self):
        storage.write_config(self.config, self.path)
        # 不需要管理员 chown；模拟不同进程身份，文件和 stat 仍来自真实文件系统。
        uid = os.getuid()
        with mock.patch.object(storage.os, 'getuid', return_value=uid + 1):
            with self.assertRaises(PermissionError):
                storage.check_private(self.path)
            with self.assertRaises(PermissionError):
                storage.write_config(self.config, self.path)
        self.assert_saved(self.config)

    def test_fifo_is_rejected_without_opening_it(self):
        self.path.parent.mkdir(mode=0o700)
        os.mkfifo(self.path, 0o600)
        with self.assertRaises(PermissionError):
            storage.check_private(self.path)
        with self.assertRaises(PermissionError):
            storage.write_config(self.config, self.path)


@unittest.skipUnless(os.name == 'nt', 'Windows NTFS 和 pywin32 真实检查')
class WindowsStorageTests(StorageFixture, unittest.TestCase):
    def security(self, path):
        _, _, _, security, _, _ = storage._win()
        return security.GetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
            security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)

    def set_acl(self, path, acl, protected=True):
        _, _, _, security, _, _ = storage._win()
        flags = (security.PROTECTED_DACL_SECURITY_INFORMATION if protected else
                 security.UNPROTECTED_DACL_SECURITY_INFORMATION)
        security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
            security.DACL_SECURITY_INFORMATION | flags, None, None, acl, None)

    def test_created_dacl_is_protected_and_only_user_and_system(self):
        storage.write_config(self.config, self.path)
        _, _, _, security, _, rights = storage._win()
        user, system = storage._windows_identity()
        for target in (self.path.parent, self.path):
            with self.subTest(target=target.name):
                descriptor = self.security(target)
                self.assertEqual(descriptor.GetSecurityDescriptorOwner(), user)
                self.assertTrue(descriptor.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED)
                acl = descriptor.GetSecurityDescriptorDacl()
                entries = [acl.GetAce(index) for index in range(acl.GetAceCount())]
                self.assertEqual({security.ConvertSidToStringSid(ace[2]) for ace in entries},
                    {security.ConvertSidToStringSid(user), security.ConvertSidToStringSid(system)})
                self.assertTrue(all(ace[0][0] == 0 and ace[1] == rights.FILE_ALL_ACCESS for ace in entries))
        self.assert_saved(self.config)

    def test_broad_ace_on_file_or_directory_is_rejected(self):
        storage.write_config(self.config, self.path)
        _, _, _, security, _, rights = storage._win()
        everyone = security.ConvertStringSidToSid('S-1-1-0')
        for target in (self.path, self.path.parent):
            with self.subTest(target=target.name):
                original = self.security(target).GetSecurityDescriptorDacl()
                broad = self.security(target).GetSecurityDescriptorDacl()
                broad.AddAccessAllowedAce(security.ACL_REVISION, rights.FILE_GENERIC_READ, everyone)
                self.set_acl(target, broad)
                try:
                    with self.assertRaises(PermissionError):
                        storage.check_private(self.path)
                    with self.assertRaises(PermissionError):
                        storage.write_config({'api_key': 'new-fake-key'}, self.path)
                    self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), self.config)
                    self.assertEqual(list(self.path.parent.glob('.yaoyin-config-*')), [])
                finally:
                    self.set_acl(target, original)

    def test_null_and_unprotected_dacl_are_rejected(self):
        storage.write_config(self.config, self.path)
        original = self.security(self.path).GetSecurityDescriptorDacl()
        for acl, protected in ((None, True), (original, False)):
            with self.subTest(null=acl is None, protected=protected):
                self.set_acl(self.path, acl, protected)
                try:
                    with self.assertRaises(PermissionError):
                        storage.check_private(self.path)
                finally:
                    self.set_acl(self.path, original)

    def test_wrong_owner_descriptor_is_rejected(self):
        storage.write_config(self.config, self.path)
        _, _, _, security, _, _ = storage._win()
        descriptor = self.security(self.path.parent)
        descriptor.SetSecurityDescriptorOwner(security.ConvertStringSidToSid('S-1-1-0'), False)
        # 不依赖 CI 的 SeRestorePrivilege，其他 ACL 测试均真实写入 NTFS。
        with mock.patch.object(security, 'GetSecurityInfo', return_value=descriptor):
            with self.assertRaises(PermissionError):
                storage.check_private(self.path)

    def test_directory_junction_is_rejected(self):
        storage.write_config(self.config, self.path)
        junction = self.root / 'junction'
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction),
                        str(self.path.parent)], check=True, capture_output=True)
        try:
            with self.assertRaises(PermissionError):
                storage.check_private(junction / self.path.name)
            with self.assertRaises(PermissionError):
                storage.write_config(self.config, junction / self.path.name)
        finally:
            junction.rmdir()
        self.assert_saved(self.config)


if __name__ == '__main__':
    unittest.main()
