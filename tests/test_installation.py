"""只在临时目录和内存注册表测试安装；绝不执行真实安装、自启或卸载。"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from focusguard.windows import installation as installer
from focusguard.windows.autostart_policy import (AUTOSTART_UNTIL, BEIJING, before_deadline,
                                                new_policy, validate_policy)

BEFORE = AUTOSTART_UNTIL - timedelta(microseconds=1)
AFTER = AUTOSTART_UNTIL + timedelta(microseconds=1)


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class MemoryRegistry:
    def __init__(self):
        self.keys = {}
        self.writes = []
        self.fail_once = None

    def open(self, hive, path, *args):
        if hive != installer.winreg.HKEY_CURRENT_USER:
            raise AssertionError("只允许当前用户注册表")
        if path not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(path)

    def create(self, hive, path):
        if hive != installer.winreg.HKEY_CURRENT_USER:
            raise AssertionError("只允许当前用户注册表")
        self.keys.setdefault(path, {})
        return FakeKey(path)

    def query(self, key, name):
        try:
            return self.keys[key.path][name]
        except KeyError:
            raise FileNotFoundError(name) from None

    def set(self, key, name, reserved, kind, value):
        if (key.path, name) == self.fail_once:
            self.fail_once = None
            raise PermissionError("模拟注册表写入失败")
        self.writes.append((key.path, name, value))
        self.keys[key.path][name] = (value, kind)

    def delete(self, key, name):
        if name not in self.keys[key.path]:
            raise FileNotFoundError(name)
        self.writes.append((key.path, name, None))
        del self.keys[key.path][name]

    def delete_key(self, hive, path):
        self.open(hive, path)
        del self.keys[path]

    def info(self, key):
        return (0, len(self.keys[key.path]), 0)

    def put(self, path, name, value, kind=None):
        self.keys.setdefault(path, {})[name] = (value, installer.winreg.REG_SZ if kind is None else kind)


class DeadlineTests(unittest.TestCase):
    def test_december_20_whole_day_is_covered(self):
        self.assertTrue(before_deadline(datetime(2026, 12, 20, 23, 59, 59, tzinfo=BEIJING)))
        self.assertTrue(before_deadline(BEFORE))
        self.assertFalse(before_deadline(AUTOSTART_UNTIL))
        self.assertFalse(before_deadline(AFTER))

    def test_cutoff_is_same_in_other_timezones(self):
        self.assertFalse(before_deadline(datetime(2026, 12, 20, 16, tzinfo=timezone.utc)))
        self.assertTrue(before_deadline(BEFORE.astimezone(timezone(timedelta(hours=-7)))))
        self.assertEqual(AUTOSTART_UNTIL.isoformat(), "2026-12-21T00:00:00+08:00")

    def test_naive_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            before_deadline(datetime(2026, 12, 20))

    def test_unknown_or_extended_policy_is_rejected(self):
        self.assertIsNone(validate_policy(None))
        for value in ({}, {**new_policy(BEFORE), "until": "2099-12-21"},
                      {**new_policy(BEFORE), "expired": "false"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_policy(value)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "FocusGuard"
        self.target = self.root / "App" / "FocusGuard.exe"
        self.source = self.base / "downloaded.exe"
        self.source.write_bytes(b"synthetic release bytes; never executed")
        self.registry = MemoryRegistry()
        patches = [
            patch.object(installer, "root_dir", return_value=self.root),
            patch.object(installer.sys, "frozen", True, create=True),
            patch.object(installer.sys, "executable", str(self.source)),
            patch.object(installer, "before_deadline", side_effect=lambda now=None: before_deadline(BEFORE if now is None else now)),
            patch.multiple(installer.winreg, OpenKey=self.registry.open, CreateKey=self.registry.create,
                           QueryValueEx=self.registry.query, SetValueEx=self.registry.set,
                           DeleteValue=self.registry.delete, DeleteKey=self.registry.delete_key,
                           QueryInfoKey=self.registry.info),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    @contextmanager
    def installed(self):
        with patch.object(installer.sys, "executable", str(self.target)):
            yield

    def run_value(self):
        return self.registry.keys.get(installer.RUN_KEY, {}).get(installer.RUN_NAME, (None,))[0]

    def disable_in_windows(self, state=3):
        self.registry.put(installer.STARTUP_APPROVED_KEY, installer.RUN_NAME,
                          state.to_bytes(4, "little") + b"\0" * 8, installer.winreg.REG_BINARY)

    def test_install_enrolls_current_user_once_and_records_fixed_deadline(self):
        self.assertEqual(installer.install(BEFORE), self.target)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.run_value(), installer.launch_command(self.target, "--autostart"))
        record = installer.read_manifest()
        self.assertEqual(record["autostart_policy"], new_policy(BEFORE))
        self.assertEqual(record["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertTrue(set(p for p, _, _ in self.registry.writes) <= {installer.RUN_KEY, installer.UNINSTALL_KEY})
        with self.installed():
            state = installer.autostart_state(BEFORE)
        self.assertTrue(state.enabled and state.limited)

    def test_install_does_not_create_or_modify_monitor_config(self):
        config = self.root / "Data" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_bytes(b"existing config must remain separate")
        installer.install(BEFORE)
        self.assertEqual(config.read_bytes(), b"existing config must remain separate")

    def test_install_after_deadline_does_not_enable_startup(self):
        installer.install(AUTOSTART_UNTIL)
        self.assertIsNone(self.run_value())
        self.assertTrue(installer.read_manifest()["autostart_policy"]["expired"])

    def test_upgrade_does_not_recreate_removed_run_entry(self):
        installer.install(BEFORE)
        del self.registry.keys[installer.RUN_KEY][installer.RUN_NAME]
        self.registry.writes.clear()
        installer.install(BEFORE)
        self.assertIsNone(self.run_value())
        self.assertFalse(any(path == installer.RUN_KEY for path, _, _ in self.registry.writes))

    def test_windows_disabled_choice_is_not_overwritten_on_install(self):
        self.disable_in_windows()
        installer.install(BEFORE)
        self.assertIsNone(self.run_value())
        with self.installed():
            state = installer.autostart_state(BEFORE)
            with self.assertRaisesRegex(ValueError, "Windows"):
                installer.set_autostart(True)
        self.assertTrue(state.limited and state.system_disabled)
        self.assertFalse(state.enabled)
        self.assertFalse(any(path == installer.STARTUP_APPROVED_KEY for path, _, _ in self.registry.writes))

    def test_unknown_startup_approval_is_conservatively_respected(self):
        for state in (3, 7, 42):
            self.disable_in_windows(state)
            self.assertTrue(installer._system_startup_disabled())
        self.disable_in_windows(2)
        self.assertFalse(installer._system_startup_disabled())

    def test_disabled_existing_run_is_reported_not_repaired(self):
        installer.install(BEFORE)
        self.disable_in_windows()
        self.registry.writes.clear()
        with self.installed():
            self.assertFalse(installer.autostart_enabled())
            for _ in range(3):
                self.assertFalse(installer.expire_autostart(BEFORE))
                self.assertFalse(installer.autostart_state(BEFORE).enabled)
        self.assertEqual(self.registry.writes, [])

    def test_application_cannot_switch_off_limited_startup_but_uninstall_can(self):
        installer.install(BEFORE)
        with self.installed():
            with self.assertRaisesRegex(ValueError, "备考限期"):
                installer.set_autostart(False)
            with patch.object(installer.subprocess, "Popen") as spawn, patch.object(installer.tempfile, "gettempdir", return_value=self.temp.name):
                installer.schedule_uninstall()
        self.assertIsNone(self.run_value())
        self.assertNotIn(installer.UNINSTALL_KEY, self.registry.keys)
        spawn.assert_called_once()  # 仅检查命令，不运行卸载脚本。

    def test_expiration_removes_only_own_run_and_is_idempotent(self):
        installer.install(BEFORE)
        self.registry.put(installer.RUN_KEY, "AnotherApp", "keep")
        with self.installed():
            self.assertFalse(installer.expire_autostart(BEFORE))
            self.assertTrue(installer.expire_autostart(AUTOSTART_UNTIL))
            self.assertFalse(installer.expire_autostart(AFTER))
            state = installer.autostart_state(BEFORE)  # 时钟回调不恢复策略。
        self.assertTrue(state.expired)
        self.assertFalse(state.limited)
        self.assertIsNone(self.run_value())
        self.assertEqual(self.registry.keys[installer.RUN_KEY]["AnotherApp"][0], "keep")
        installer.install(BEFORE)
        self.assertIsNone(self.run_value())

    def test_expiration_preserves_replaced_run_value(self):
        installer.install(BEFORE)
        self.registry.put(installer.RUN_KEY, installer.RUN_NAME, "user replacement")
        with self.installed():
            self.assertTrue(installer.expire_autostart(AFTER))
        self.assertEqual(self.run_value(), "user replacement")

    def test_install_refuses_colliding_startup_without_touching_files(self):
        self.registry.put(installer.RUN_KEY, installer.RUN_NAME, "unrelated command")
        with self.assertRaisesRegex(ValueError, "同名启动项"):
            installer.install(BEFORE)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.run_value(), "unrelated command")

    def test_foreign_uninstall_record_prevents_install_without_touching_files(self):
        for name, value in (("UninstallString", "unrelated.exe /remove"), ("UnrelatedField", "keep")):
            with self.subTest(name=name):
                self.registry.keys = {}
                self.registry.put(installer.UNINSTALL_KEY, name, value)
                original = {path: dict(values) for path, values in self.registry.keys.items()}
                with self.assertRaisesRegex(ValueError, "同名卸载记录"):
                    installer.install(BEFORE)
                self.assertEqual(self.registry.keys, original)
                self.assertEqual(self.registry.writes, [])
                self.assertFalse(self.target.exists())

    def test_uninstall_preserves_replaced_foreign_uninstall_record(self):
        installer.install(BEFORE)
        foreign = {"UninstallString": ("unrelated.exe /remove", installer.winreg.REG_SZ)}
        self.registry.keys[installer.UNINSTALL_KEY] = dict(foreign)
        with self.installed(), patch.object(installer.subprocess, "Popen"), patch.object(installer.tempfile, "gettempdir", return_value=self.temp.name):
            installer.schedule_uninstall()
        self.assertEqual(self.registry.keys[installer.UNINSTALL_KEY], foreign)
        self.assertIsNone(self.run_value())

    def test_new_install_rolls_back_if_autostart_registration_fails(self):
        self.registry.fail_once = (installer.RUN_KEY, installer.RUN_NAME)
        with self.assertRaises(PermissionError):
            installer.install(BEFORE)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.target.parent / "install.json").exists())
        self.assertIsNone(self.run_value())
        self.assertNotIn("DisplayName", self.registry.keys.get(installer.UNINSTALL_KEY, {}))
        self.assertEqual(list(self.target.parent.glob(".install-*")), [])
        installer.install(BEFORE)  # 失败后可以重试，不会被当作用户已禁用。
        self.assertIsNotNone(self.run_value())

    def test_upgrade_failure_restores_previous_binary_manifest_and_registration(self):
        installer.install(BEFORE)
        old_manifest = (self.target.parent / "install.json").read_bytes()
        old_binary = self.target.read_bytes()
        self.source.write_bytes(b"newer synthetic binary")
        self.registry.fail_once = (installer.UNINSTALL_KEY, "DisplayVersion")
        with self.assertRaises(PermissionError):
            installer.install(BEFORE)
        self.assertEqual(self.target.read_bytes(), old_binary)
        self.assertEqual((self.target.parent / "install.json").read_bytes(), old_manifest)
        self.assertEqual(self.run_value(), installer.launch_command(self.target, "--autostart"))
        self.assertEqual(list(self.target.parent.glob("*.tmp")), [])

    def test_manifest_failure_does_not_register_autostart(self):
        with patch.object(installer, "_write_manifest", side_effect=OSError("磁盘已满")):
            with self.assertRaises(OSError):
                installer.install(BEFORE)
        self.assertFalse(self.target.exists())
        self.assertIsNone(self.run_value())
        self.assertEqual(self.registry.writes, [])

    def test_expiration_manifest_failure_never_restores_startup(self):
        installer.install(BEFORE)
        with self.installed():
            with patch.object(installer, "_write_manifest", side_effect=OSError("磁盘已满")):
                with self.assertRaises(OSError):
                    installer.expire_autostart(AFTER)
            self.assertIsNone(self.run_value())
            self.assertTrue(installer.expire_autostart(AFTER))

    def test_legacy_install_is_not_silently_enrolled_during_startup(self):
        installer.install(BEFORE)
        manifest = installer.read_manifest()
        manifest.pop("autostart_policy")
        installer._write_manifest(manifest)
        del self.registry.keys[installer.RUN_KEY][installer.RUN_NAME]
        self.registry.writes.clear()
        with self.installed():
            self.assertFalse(installer.expire_autostart(BEFORE))
            self.assertFalse(installer.autostart_state(BEFORE).limited)
        self.assertEqual(self.registry.writes, [])
        installer.install(BEFORE)  # 仅明确安装/升级时加入。
        self.assertIsNotNone(self.run_value())

    def test_portable_expiration_is_read_only(self):
        self.assertFalse(installer.expire_autostart(AFTER))
        self.assertEqual(self.registry.writes, [])
        self.assertFalse(self.target.exists())

    def test_bad_policy_does_not_trigger_registry_cleanup(self):
        installer.install(BEFORE)
        path = self.target.parent / "install.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["autostart_policy"]["until"] = "2099-01-01"
        path.write_text(json.dumps(record), encoding="utf-8")
        self.registry.writes.clear()
        with self.installed(), self.assertRaises(ValueError):
            installer.expire_autostart(AFTER)
        self.assertEqual(self.registry.writes, [])


if __name__ == "__main__":
    unittest.main()
