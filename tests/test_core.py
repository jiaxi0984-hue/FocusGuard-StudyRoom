import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch
from focusguard.monitoring.core import Game, Settings, Store, Monitor, ProcessRecord, CONSENT_VERSION
from focusguard.recognition.engine import Recognizer
from focusguard.recognition.discovery import Index
from focusguard.windows.system import ShutdownAction, same_process
from focusguard.windows import installation
from helpers import make_steam, engine_for


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = make_steam(self.temp.name)
        self.engine = engine_for(self.fixture)
        self.settings = Settings()
        self.store = Store(Path(self.temp.name) / "config.json")
        self.action, self.report = Mock(), Mock()
        self.alive = Mock(return_value=True)
        self.monitor = Monitor(self.settings, self.store, "DOMAIN\\user", self.action, self.report, self.engine, self.alive)
        self.proc = ProcessRecord(123, str(self.fixture["exe"]), "DOMAIN\\user", 1)

    def test_default_disarmed(self):
        self.monitor.inspect([self.proc])
        self.action.assert_not_called()

    def test_requires_consent(self):
        with self.assertRaises(ValueError):
            self.monitor.arm()
        self.assertFalse(self.settings.armed)

    def test_no_manual_list_needed_to_arm(self):
        self.engine.publish(Index())
        self.monitor.arm(True)
        self.assertTrue(self.settings.armed)
        self.assertNotIn("games", self.settings.to_dict())

    def test_confirmed_game_triggers_once(self):
        self.monitor.arm(True)
        for _ in range(8):
            self.monitor.inspect([self.proc, self.proc])
        self.action.assert_called_once()
        self.assertEqual(self.action.call_args.args[0].name, "反恐精英 2")
        self.assertTrue(self.store.load().tripped)
        self.assertFalse(self.store.load().armed)
        self.assertIn("Steam AppID", self.report.call_args.args[0])

    def test_same_name_different_path_does_not_trigger(self):
        self.monitor.arm(True)
        other = str(Path(self.temp.name) / "cs2.exe")
        self.monitor.inspect([ProcessRecord(1, other, self.proc.owner, 1)])
        self.action.assert_not_called()

    def test_other_user_not_targeted(self):
        self.monitor.arm(True)
        self.monitor.inspect([ProcessRecord(1, self.proc.path, "DOMAIN\\other", 1)])
        self.action.assert_not_called()

    def test_missing_identity_not_targeted(self):
        self.monitor.arm(True)
        self.monitor.inspect([ProcessRecord(1, self.proc.path, "", 1), ProcessRecord(2, "", self.proc.owner, 1), ProcessRecord(3, self.proc.path, self.proc.owner, 0)])
        self.action.assert_not_called()

    def test_owner_and_path_case_insensitive(self):
        self.monitor.arm(True)
        self.monitor.inspect([ProcessRecord(1, self.proc.path.upper(), self.proc.owner.upper(), 1)])
        self.action.assert_called_once()

    def test_exited_process_does_not_trigger(self):
        self.monitor.arm(True)
        self.alive.return_value = False
        self.monitor.inspect([self.proc])
        self.action.assert_not_called()

    def test_exit_during_persistence_cancels_action(self):
        self.monitor.arm(True)
        self.alive.side_effect = [True, False]
        self.assertIsNone(self.monitor.inspect([self.proc]))
        self.action.assert_not_called()
        self.assertFalse(self.settings.armed)
        self.assertIn("本次已取消", self.report.call_args.args[0])

    def test_pause_cancels_detection(self):
        self.monitor.arm(True)
        self.monitor.pause()
        self.monitor.inspect([self.proc])
        self.action.assert_not_called()

    def test_initializing_does_not_trigger(self):
        self.monitor.arm(True)
        self.engine.begin_refresh()
        self.monitor.inspect([self.proc])
        self.action.assert_not_called()

    def test_recognition_error_does_not_trigger(self):
        self.monitor.arm(True)
        self.engine.fail("磁盘不可用")
        self.monitor.inspect([self.proc])
        self.action.assert_not_called()

    def test_rearm_can_trigger_new_session(self):
        self.monitor.arm(True)
        self.monitor.inspect([self.proc])
        self.monitor.arm(True)
        self.monitor.inspect([self.proc])
        self.assertEqual(self.action.call_count, 2)

    def test_action_failure_does_not_loop(self):
        self.action.side_effect = RuntimeError("没有权限")
        self.monitor.arm(True)
        self.monitor.inspect([self.proc])
        self.monitor.inspect([self.proc])
        self.action.assert_called_once()
        self.assertFalse(self.settings.armed)

    def test_save_failure_prevents_shutdown(self):
        self.monitor.arm(True)
        with patch.object(self.store, "save", side_effect=OSError("磁盘不可写")):
            self.monitor.inspect([self.proc])
        self.action.assert_not_called()

    def test_arm_save_failure_disarms(self):
        with patch.object(self.store, "save", side_effect=OSError("磁盘不可写")):
            with self.assertRaises(OSError):
                self.monitor.arm(True)
        self.assertFalse(self.settings.armed)

    def test_config_roundtrip(self):
        self.monitor.arm(True)
        loaded = self.store.load()
        self.assertTrue(loaded.armed)
        self.assertEqual(loaded.consent_version, CONSENT_VERSION)

    def test_old_consent_does_not_arm(self):
        self.settings.armed = True
        self.settings.consent_version = CONSENT_VERSION - 1
        self.store.save(self.settings)
        self.assertFalse(self.store.load().armed)

    def test_migrate_v1_backs_up_and_disarms(self):
        original = json.dumps({"schema": 1, "armed": True, "consent_version": 1, "games": [{"path": "C:/anything.exe", "name": "Anything", "enabled": True}], "unknown_parameter": 42}).encode()
        self.store.path.write_bytes(original)
        loaded = self.store.load()
        self.assertFalse(loaded.armed)
        self.assertEqual(self.store.migration_backup.read_bytes(), original)
        self.assertEqual(json.loads(self.store.path.read_text())["schema"], 2)
        second = Store(self.store.path)
        self.assertFalse(second.load().armed)
        self.assertIsNone(second.migration_backup)
        self.assertEqual(len(list(self.store.path.parent.glob("config.v1-backup-*.json"))), 1)

    def test_migration_preserves_trigger_latch(self):
        self.store.path.write_text('{"schema":1,"tripped":true}')
        self.assertTrue(self.store.load().tripped)

    def test_migration_backup_failure_keeps_original(self):
        original = '{"schema":1,"armed":true}'
        self.store.path.write_text(original)
        with patch.object(self.store, "save", side_effect=OSError("失败")):
            with self.assertRaises(OSError):
                self.store.load()
        self.assertEqual(self.store.path.read_text(), original)

    def test_invalid_config_rejected(self):
        self.store.path.write_text("bad json")
        with self.assertRaises(json.JSONDecodeError):
            self.store.load()

    def test_unknown_schema_rejected(self):
        self.store.path.write_text('{"schema":999,"armed":true}')
        with self.assertRaises(ValueError):
            self.store.load()

    def test_unknown_recognition_mode_rejected(self):
        self.store.path.write_text('{"schema":2,"recognition_mode":"manual"}')
        with self.assertRaises(ValueError):
            self.store.load()

    def test_no_atomic_tempfiles_left(self):
        self.store.save(self.settings)
        self.assertEqual(list(self.store.path.parent.glob("*.tmp")), [])

    def test_simulate_never_runs_subprocess(self):
        with patch("focusguard.windows.system.subprocess.run") as run:
            ShutdownAction(True, self.report)(Game("测试", self.proc.path))
        run.assert_not_called()

    def test_real_action_is_mocked(self):
        with patch("focusguard.windows.system.subprocess.run") as run:
            ShutdownAction(False)(Game("测试", self.proc.path))
        self.assertEqual(run.call_args.args[0][1:], ["/s", "/f", "/t", "0"])
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_same_process_rejects_pid_reuse(self):
        process = Mock()
        process.create_time.return_value = self.proc.created + 1
        with patch("focusguard.windows.system.psutil.Process", return_value=process):
            self.assertFalse(same_process(self.proc))

    def test_uninstall_deletes_fixed_files_only(self):
        text = installation.uninstall_script(Path(self.temp.name) / "App", 123, True)
        self.assertNotIn("-Recurse", text)
        self.assertIn("-LiteralPath", text)
        self.assertNotIn("*.exe", text)
        self.assertNotIn("taskkill", text)

    def test_uninstall_keeps_data_by_default(self):
        text = installation.uninstall_script(Path(self.temp.name) / "App", 123, False)
        self.assertNotIn("config.json", text)

    def test_command_quotes_spaces(self):
        command = installation.launch_command("C:/A B/FocusGuard.exe", "--autostart")
        self.assertTrue(command.startswith('"C:/A B/FocusGuard.exe"'))
        self.assertTrue(command.endswith("--autostart"))

    def test_autostart_current_user_only(self):
        with patch.object(installation, "is_installed", return_value=True), patch.object(installation.winreg, "CreateKey", return_value=MagicMock()) as create, patch.object(installation.winreg, "SetValueEx") as write:
            installation.set_autostart(True)
        self.assertEqual(create.call_args.args, (installation.winreg.HKEY_CURRENT_USER, installation.RUN_KEY))
        self.assertEqual(write.call_args.args[1], "FocusGuardStudyRoom")

    def test_autostart_off_removes_only_owned_value(self):
        with patch.object(installation.winreg, "CreateKey", return_value=MagicMock()), patch.object(installation.winreg, "DeleteValue") as remove:
            installation.set_autostart(False)
        self.assertEqual(remove.call_args.args[1], "FocusGuardStudyRoom")

    def test_portable_autostart_refused(self):
        with patch.object(installation, "is_installed", return_value=False), patch.object(installation.winreg, "CreateKey") as create:
            with self.assertRaises(ValueError):
                installation.set_autostart(True)
        create.assert_not_called()

    def test_install_refuses_unmarked_existing_file(self):
        target = Path(self.temp.name) / "App" / "FocusGuard.exe"
        target.parent.mkdir()
        target.write_bytes(b"other program")
        with patch.object(installation, "exe_path", return_value=target), patch.object(installation.sys, "frozen", True, create=True):
            with self.assertRaises(FileNotFoundError):
                installation.install()
        self.assertEqual(target.read_bytes(), b"other program")

    def test_manifest_refuses_replaced_binary(self):
        folder = Path(self.temp.name) / "FocusGuard" / "App"
        folder.mkdir(parents=True)
        exe = folder / "FocusGuard.exe"
        exe.write_bytes(b"changed")
        (folder / "install.json").write_text(json.dumps({"id": installation.APP_ID, "exe": exe.name, "sha256": "wrong"}))
        with patch.object(installation, "exe_path", return_value=exe), patch.object(installation, "root_dir", return_value=folder.parent):
            with self.assertRaises(ValueError):
                installation.read_manifest()


if __name__ == "__main__":
    unittest.main()
