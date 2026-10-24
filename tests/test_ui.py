"""Qt 控件与新自动识别流程；系统关机、自启写入、安装动作全部隔离。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import os
from focusguard.monitoring.core import ProcessRecord, Store, Settings, CONSENT_VERSION
from focusguard.recognition.discovery import Index, build_index
from focusguard.ui.app import Window, Indexer, Poller, handle_instance_request, autostart_launch_allowed, main
from focusguard.windows import installation
from helpers import make_steam, engine_for, write_pe, cache_bytes
from focusguard.recognition.rules import RULES


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = Window(self.temp.name, simulate=True, index_loader=lambda cancel: Index())
        self.window.poller.stopped.set()
        self.window.poller.wait()
        self.window.indexer.stopped.set()
        self.window.indexer.wake.set()
        self.window.indexer.wait()
        self.window.recognizer.publish(Index())
        self.app.processEvents()
        self.window.refresh()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.app.processEvents()
        self.window.stop_workers()
        self.window.closing = True
        self.window.log_handler.close()
        self.window.tray.hide()
        self.window.close()

    def test_navigation_all_pages(self):
        for i in range(4):
            self.window.nav[i].click()
            self.assertEqual(self.window.pages.currentIndex(), i)
        self.assertIn("识别范围", self.window.nav[1].text())

    def test_no_list_required_to_arm_pause(self):
        self.window.arm_button.click()
        self.assertTrue(self.window.settings.armed)
        self.window.arm_button.click()
        self.assertFalse(self.window.settings.armed)
        self.assertNotIn("games", self.window.settings.to_dict())

    def test_range_is_readonly_and_shows_candidates(self):
        self.assertEqual(self.window.rule_list.count(), len(RULES))
        self.assertTrue(any("仅名称候选" in self.window.rule_list.item(i).text() for i in range(len(RULES))))
        self.assertTrue(any("客户端大厅" in self.window.rule_list.item(i).text() for i in range(len(RULES))))
        self.assertFalse(self.window.rule_list.item(0).flags() & Qt.ItemIsUserCheckable)
        self.assertFalse(hasattr(self.window, "add_game"))

    def test_initialization_disables_enable_button(self):
        self.window.recognizer.begin_refresh()
        self.window.refresh()
        self.assertFalse(self.window.arm_button.isEnabled())
        self.assertIn("准备中", self.window.pill.text())

    def test_error_is_visible_and_blocks_new_enable(self):
        self.window.recognizer.fail("样本磁盘不可用")
        self.window.on_index_updated()
        self.assertIn("识别异常", self.window.pill.text())
        self.assertIn("样本磁盘不可用", self.window.range_status.text())
        self.assertFalse(self.window.arm_button.isEnabled())

    def test_armed_monitor_can_pause_during_error(self):
        self.window.toggle_monitor()
        self.window.recognizer.fail("失败")
        self.window.refresh()
        self.assertTrue(self.window.arm_button.isEnabled())
        self.window.arm_button.click()
        self.assertFalse(self.window.settings.armed)

    def test_recognition_hit_updates_ui_without_shutdown(self):
        fixture = make_steam(Path(self.temp.name) / "fixture")
        engine = self.window.recognizer
        engine.publish(build_index(steam_locations=[fixture["steam"]], records=[]))
        self.window.monitor.validate_process = Mock(return_value=True)
        process = ProcessRecord(9876, str(fixture["exe"]), self.window.monitor.owner, 1)
        self.window.toggle_monitor()
        with patch("focusguard.windows.system.subprocess.run") as run:
            engine.observe([process], self.window.monitor.owner)
            self.window.monitor.inspect([process])
            self.app.processEvents()
            self.window.refresh()
        run.assert_not_called()
        self.assertFalse(self.window.settings.armed)
        self.assertIn("模拟成功", self.window.logs.toPlainText())
        self.assertIn("确认游戏", self.window.process_results.item(0).text())

    def test_unknown_candidate_visible_but_does_not_trigger(self):
        process = ProcessRecord(9876, str(Path(self.temp.name) / "GenshinImpact.exe"), self.window.monitor.owner, 1)
        self.window.recognizer.observe([process], self.window.monitor.owner)
        self.window.refresh()
        self.assertIn("不触发", self.window.process_results.item(0).text())

    def test_index_updates_do_not_spam_same_log(self):
        self.window.on_index_updated()
        before = self.window.logs.toPlainText()
        for _ in range(5):
            self.window.on_index_updated()
        self.assertEqual(before, self.window.logs.toPlainText())

    def test_view_simulation_results_pauses_real_monitor(self):
        self.window.toggle_monitor()
        with patch("focusguard.ui.app.QMessageBox.information"):
            self.window.simulate_once()
        self.assertFalse(self.window.settings.armed)

    def test_simulation_rejects_real_autostart(self):
        with patch("focusguard.windows.installation.set_autostart") as setter, patch.object(self.window, "error"):
            self.window.set_autostart(True)
        setter.assert_not_called()

    def test_simulation_rejects_install(self):
        with patch("focusguard.windows.installation.install") as installer, patch.object(self.window, "error"):
            self.window.install()
        installer.assert_not_called()

    def test_simulation_rejects_uninstall(self):
        with patch("focusguard.windows.installation.schedule_uninstall") as uninstaller, patch.object(self.window, "error"):
            self.window.uninstall()
        uninstaller.assert_not_called()

    def test_custom_index_inputs_forbidden_in_real_mode(self):
        with self.assertRaises(ValueError):
            Window(Path(self.temp.name) / "unsafe", index_loader=lambda cancel: Index())

    def test_simulation_refresh_never_expires_or_writes_real_autostart(self):
        with patch.object(installation, "expire_autostart") as expire, patch.object(installation, "autostart_state") as state:
            self.window.refresh()
        expire.assert_not_called()
        state.assert_not_called()
        self.assertFalse(self.window.autostart.isEnabled())
        self.assertIn("北京时间", self.window.autostart_policy_text.text())

    def test_limited_startup_state_is_visible_and_checkbox_locked(self):
        self.window.simulate = False
        with patch.object(installation, "is_installed", return_value=True), patch.object(installation, "expire_autostart", return_value=False), patch.object(installation, "autostart_state", return_value=installation.AutostartState(True, True)):
            self.window.refresh_autostart()
        self.assertTrue(self.window.autostart.isChecked())
        self.assertFalse(self.window.autostart.isEnabled())
        self.assertIn("2026 年 12 月 21 日", self.window.autostart_policy_text.text())
        self.assertIn("Windows", self.window.autostart_policy_text.text())
        self.assertIn("不自动开启关机监测", self.window.autostart_policy_text.text())
        self.assertFalse(self.window.settings.armed)

    def test_windows_disabled_choice_is_displayed_without_repair(self):
        self.window.simulate = False
        with patch.object(installation, "is_installed", return_value=True), patch.object(installation, "expire_autostart", return_value=False), patch.object(installation, "autostart_state", return_value=installation.AutostartState(False, True, False, True)), patch.object(installation, "set_autostart") as setter:
            self.window.refresh_autostart()
        setter.assert_not_called()
        self.assertFalse(self.window.autostart.isChecked())
        self.assertIn("程序不会恢复", self.window.autostart_policy_text.text())

    def test_autostart_error_is_visible_without_repeated_logs(self):
        self.window.simulate = False
        with patch.object(installation, "expire_autostart", side_effect=OSError("模拟检查失败")):
            self.window.refresh_autostart()
            self.window.refresh_autostart()
        self.assertEqual(self.window.start_stat.text(), "待检查")
        self.assertEqual(self.window.logs.toPlainText().count("自启状态检查失败"), 1)

    def test_install_confirmation_can_cancel_without_changes(self):
        self.window.simulate = False
        with patch.object(installation, "is_installed", return_value=False), patch("focusguard.ui.app.before_deadline", return_value=True), patch("focusguard.ui.app.QMessageBox.question", return_value=QMessageBox.No) as question, patch.object(installation, "install") as install, patch.object(self.window.monitor, "pause") as pause:
            self.window.install()
        install.assert_not_called()
        pause.assert_not_called()
        self.assertIn("限期", question.call_args.args[2])
        self.assertIn("真实关机必须另行确认", question.call_args.args[2])
        self.assertEqual(question.call_args.args[-1], QMessageBox.No)

    def test_confirmed_install_pauses_then_relaunches_explicitly_paused(self):
        self.window.monitor.arm(True)
        self.window.simulate = False
        target = Path(self.temp.name) / "installed.exe"
        with patch.object(installation, "is_installed", return_value=False), patch("focusguard.ui.app.QMessageBox.question", return_value=QMessageBox.Yes), patch.object(installation, "install", return_value=target) as install, patch("focusguard.ui.app.subprocess.Popen") as spawn, patch.object(self.window, "finish") as finish:
            self.window.install()
        install.assert_called_once()
        self.assertFalse(self.window.settings.armed)
        self.assertFalse(self.window.store.load().armed)
        self.assertIn("--after-install", spawn.call_args.args[0])
        finish.assert_called_once_with(preserve=True)

    def test_failed_install_does_not_relaunch_or_arm(self):
        self.window.simulate = False
        with patch.object(installation, "is_installed", return_value=False), patch("focusguard.ui.app.QMessageBox.question", return_value=QMessageBox.Yes), patch.object(installation, "install", side_effect=OSError("模拟失败")), patch("focusguard.ui.app.subprocess.Popen") as spawn, patch.object(self.window, "error") as error:
            self.window.install()
        spawn.assert_not_called()
        error.assert_called_once()
        self.assertFalse(self.window.settings.armed)

    def test_after_install_start_disarms_before_any_worker_starts(self):
        data = Path(self.temp.name) / "after-install"
        store = Store(data / "config.json")
        store.save(Settings(armed=True, consent_version=CONSENT_VERSION))
        observed = []
        def worker_start(worker):
            observed.append(store.load().armed)
        with patch.object(Poller, "start", worker_start), patch.object(Indexer, "start", worker_start), patch.object(Window, "refresh_autostart"):
            window = Window(data, start_paused=True)
        try:
            self.assertEqual(observed, [False, False])
            self.assertFalse(window.settings.armed)
        finally:
            window.stop_workers()
            window.log_handler.close()
            window.tray.hide()
            window.closing = True
            window.close()

    def test_expired_login_exits_before_window_or_monitor_is_created(self):
        with patch.object(installation, "expire_autostart") as expire, patch("focusguard.ui.app.before_deadline", return_value=False), patch("focusguard.ui.app.Window") as window:
            self.assertFalse(autostart_launch_allowed())
        expire.assert_called_once()
        window.assert_not_called()

    def test_simulated_login_never_cleans_real_autostart(self):
        with patch.object(installation, "expire_autostart") as expire:
            self.assertTrue(autostart_launch_allowed(simulate=True))
        expire.assert_not_called()

    def test_existing_instance_receives_install_and_uninstall_requests(self):
        window = Mock()
        handle_instance_request(window, b"install")
        window.install.assert_called_once()
        window.uninstall.assert_not_called()
        handle_instance_request(window, b"uninstall")
        window.uninstall.assert_called_once()
        self.assertEqual(window.reveal.call_count, 2)

    def test_maintenance_cli_disarms_before_constructing_monitoring_window(self):
        for flag in ("--install", "--uninstall"):
            with self.subTest(flag=flag), patch("focusguard.ui.app.sys.argv", ["FocusGuard.exe", flag]), patch("focusguard.ui.app.QApplication") as app_class, patch("focusguard.ui.app.QLocalServer"), patch("focusguard.ui.app.QLocalSocket") as socket, patch("focusguard.ui.app.Window") as window, patch("focusguard.ui.app.QTimer.singleShot"):
                socket.return_value.waitForConnected.return_value = False
                app_class.return_value.exec.return_value = 0
                self.assertEqual(main(), 0)
            self.assertTrue(window.call_args.kwargs["start_paused"])

    def test_changed_install_sources_refresh_on_live_candidate(self):
        fixture = make_steam(Path(self.temp.name) / "fresh-steam")
        self.window.indexer.loader = lambda cancel: build_index(cancel, steam_locations=[fixture["steam"]], records=[])
        self.window.recognizer.begin_refresh()
        self.window.indexer.stopped.clear()
        self.window.indexer.start()
        for _ in range(250):
            self.app.processEvents()
            if self.window.recognizer.status()["ready"]:
                break
            QTest.qWait(20)
        self.assertTrue(self.window.recognizer.status()["bindings"])
        # Steam 启动游戏时重写缓存，即使启动数据未变，也会使旧来源快照失效。
        stamp = fixture["cache"].stat()
        os.utime(fixture["cache"], ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
        process = ProcessRecord(9876, str(fixture["exe"]), self.window.monitor.owner, 1)
        self.window.monitor.validate_process = Mock(return_value=True)
        self.window.monitor.arm(True)
        self.window.poller.stopped.clear()
        with patch("focusguard.ui.app.processes", return_value=[process]):
            self.window.poller.start()
            for _ in range(250):
                self.app.processEvents()
                if self.window.monitor.fired:
                    break
                QTest.qWait(20)
        self.assertTrue(self.window.monitor.fired, "应立即重读来源，而不是等到 60 秒定时刷新")

    def test_auto_steam_rules_display_in_readonly_range(self):
        data = make_steam(Path(self.temp.name) / "auto", appid=901001)
        self.window.recognizer.publish(build_index(steam_locations=[data["steam"]], records=[]))
        self.window.on_index_updated()
        self.assertEqual(self.window.count.text(), "1 款")
        self.assertTrue(any("Steam 自动收录" in self.window.rule_list.item(i).text() for i in range(self.window.rule_list.count())))

    def test_new_unknown_game_auto_refreshes_and_triggers_simulation(self):
        data = make_steam(Path(self.temp.name) / "new-steam")
        loader = lambda cancel: build_index(cancel, steam_locations=[data["steam"]], records=[])
        self.window.indexer.loader = loader
        self.window.recognizer.begin_refresh()
        self.window.indexer.stopped.clear()
        self.window.indexer.wake.clear()
        self.window.indexer.start()
        for _ in range(250):
            self.app.processEvents()
            if self.window.recognizer.status()["ready"]:
                break
            QTest.qWait(20)
        self.assertTrue(self.window.recognizer.status()["ready"])
        new_root = data["root"].parent / "NewGame"
        executable = write_pe(new_root / "NewGameBody.exe")
        data["manifest"].with_name("appmanifest_901001.acf").write_text('"AppState" { "appid" "901001" "name" "New Game" "installdir" "NewGame" }')
        metadata = {"appid":901001,"common":{"type":"game","name":"New Game"},
                    "config":{"launch":{"0":{"executable":"NewGameBody.exe","config":{"oslist":"windows"}}}}}
        data["cache"].write_bytes(cache_bytes({730:data["values"],901001:metadata}))
        process = ProcessRecord(9876, str(executable), self.window.monitor.owner, 1)
        self.window.monitor.validate_process = Mock(return_value=True)
        self.window.monitor.arm(True)
        self.window.poller.stopped.clear()
        with patch("focusguard.ui.app.processes", return_value=[process]):
            self.window.poller.start()
            for _ in range(250):
                self.app.processEvents()
                if self.window.monitor.fired:
                    break
                QTest.qWait(20)
        self.assertTrue(self.window.monitor.fired)
        self.assertFalse(self.window.settings.armed)

    def test_indexer_recovers_after_failure_and_waits_60_seconds(self):
        from focusguard.recognition.engine import Recognizer
        loader = Mock(side_effect=[OSError("暂时离线"), Index()])
        engine = Recognizer()
        worker = Indexer(engine, loader)
        intervals = []
        def wake(seconds):
            intervals.append(seconds)
            if len(intervals) == 2:
                worker.stopped.set()
        worker.wake.wait = wake
        worker.run()
        self.assertEqual(intervals, [60, 60])
        self.assertTrue(engine.status()["ready"])
        self.assertEqual(engine.status()["error"], "")

    def test_poller_does_not_repeat_candidate_logs(self):
        process = ProcessRecord(9876, str(Path(self.temp.name) / "GenshinImpact.exe"), self.window.monitor.owner, 1)
        worker = Poller(self.window.monitor)
        messages, intervals = [], []
        worker.message.connect(messages.append)
        def wake(seconds):
            intervals.append(seconds)
            if len(intervals) == 3:
                worker.stopped.set()
        worker.stopped.wait = wake
        with patch("focusguard.ui.app.processes", return_value=[process]):
            worker.run()
        self.assertEqual(intervals, [0.25, 0.25, 0.25])
        self.assertEqual(len(messages), 1)


if __name__ == "__main__":
    unittest.main()
