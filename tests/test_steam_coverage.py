"""自动收录 Steam 游戏的能力测试；系统关机入口始终由 Mock 拦截。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from focusguard.monitoring.core import Monitor, Settings, Store, ProcessRecord
from focusguard.recognition.discovery import build_index
from focusguard.recognition.file_evidence import FileEvidence
from focusguard.recognition.engine import Recognizer
from focusguard.windows.system import ShutdownAction
from helpers import make_steam, write_pe, cache_bytes


class SteamCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def fixture(self, *, kind="game", relative="GameMain.exe"):
        data = make_steam(self.base, appid=901001, app_type=kind, relative=relative)
        data["values"]["common"]["name"] = "Unlisted Sample Game"
        data["cache"].write_bytes(cache_bytes({901001: data["values"]}))
        data["manifest"].write_text('"AppState" { "appid" "901001" "name" "Unlisted Sample Game" "installdir" "TestGame" }')
        data["exe"] = write_pe(data["root"] / "GameMain.exe")
        return data

    def engine(self, data, metadata=()):
        engine = Recognizer(files=FileEvidence(metadata_reader=lambda p: metadata))
        engine.publish(build_index(steam_locations=[data["steam"]], records=[]))
        return engine

    def test_unlisted_steam_game_is_automatically_recognized(self):
        data = self.fixture()
        decision = self.engine(data).classify(ProcessRecord(123, str(data["exe"]), "user", 1))
        self.assertTrue(decision.confirmed)
        self.assertEqual(decision.name, "Unlisted Sample Game")

    def test_auto_game_reaches_shutdown_request_once(self):
        data = self.fixture()
        monitor = Monitor(Settings(), Store(self.base / "config.json"), "user", ShutdownAction(False), Mock(), self.engine(data), lambda p: True)
        monitor.arm(True)
        process = ProcessRecord(123, str(data["exe"]), "user", 1)
        with patch("focusguard.windows.system.subprocess.run") as system_call:
            monitor.inspect([process])
            monitor.inspect([process])
        system_call.assert_called_once()
        self.assertEqual(system_call.call_args.args[0][1:], ["/s", "/f", "/t", "0"])

    def test_steam_application_is_never_added_as_game(self):
        data = self.fixture(kind="application")
        self.assertFalse(self.engine(data).classify(ProcessRecord(123, str(data["exe"]), "user", 1)).confirmed)

    def test_same_name_outside_install_is_not_confirmed(self):
        data = self.fixture()
        unrelated = write_pe(self.base / "other/GameMain.exe")
        self.assertFalse(self.engine(data).classify(ProcessRecord(123, str(unrelated), "user", 1)).confirmed)

    def test_global_launcher_path_is_not_confirmed(self):
        data = self.fixture(relative="steam.exe")
        launcher = write_pe(data["root"] / "steam.exe")
        self.assertFalse(self.engine(data).classify(ProcessRecord(123, str(launcher), "user", 1)).confirmed)

    def test_tool_entry_is_not_confirmed(self):
        data = self.fixture(relative="GameEditor.exe")
        tool = write_pe(data["root"] / "GameEditor.exe")
        self.assertFalse(self.engine(data).classify(ProcessRecord(123, str(tool), "user", 1)).confirmed)

    def test_bootstrap_image_not_game_body(self):
        data = self.fixture()
        metadata = ({"OriginalFilename": "BootstrapPackagedGame-Win64-Shipping.exe", "FileDescription": "BootstrapPackagedGame"},)
        self.assertFalse(self.engine(data, metadata).classify(ProcessRecord(123, str(data["exe"]), "user", 1)).confirmed)

    def test_without_manifest_or_cache_not_confirmed(self):
        data = self.fixture()
        data["manifest"].unlink()
        self.assertFalse(self.engine(data).classify(ProcessRecord(123, str(data["exe"]), "user", 1)).confirmed)

    def test_missing_option_os_uses_verified_common_windows(self):
        data = self.fixture()
        data["values"]["common"]["oslist"] = "windows,linux"
        data["values"]["config"]["launch"]["0"]["config"] = {}
        data["cache"].write_bytes(cache_bytes({901001: data["values"]}))
        self.assertTrue(self.engine(data).classify(ProcessRecord(123, str(data["exe"]), "user", 1)).confirmed)

    def test_software_with_same_entry_does_not_gain_game_identity(self):
        data = self.fixture(kind="tool")
        engine = self.engine(data)
        self.assertEqual(engine.status()["steam_games"], 0)
        self.assertFalse(engine.classify(ProcessRecord(123, str(data["exe"]), "user", 1)).confirmed)

    def test_new_install_detected_by_watch_and_same_names_use_paths(self):
        data = self.fixture()
        engine = self.engine(data)
        self.assertIsNone(engine.refresh_token())
        second_root = data["root"].parent / "SecondGame"
        second = write_pe(second_root / "GameMain.exe")
        manifest = data["manifest"].with_name("appmanifest_901002.acf")
        manifest.write_text('"AppState" { "appid" "901002" "name" "Second Game" "installdir" "SecondGame" }')
        info = {"appid":901002, "common":{"name":"Second Game","type":"game"},
                "config":{"launch":{"0":{"executable":"GameMain.exe","config":{"oslist":"windows"}}}}}
        data["cache"].write_bytes(cache_bytes({901001:data["values"],901002:info}))
        self.assertIsNotNone(engine.refresh_token())
        engine.publish(build_index(steam_locations=[data["steam"]], records=[]))
        self.assertIsNone(engine.refresh_token())
        first = engine.classify(ProcessRecord(123, str(data["exe"]), "user", 1))
        other = engine.classify(ProcessRecord(124, str(second), "user", 1))
        self.assertTrue(first.confirmed and other.confirmed)
        self.assertEqual(first.name, "Unlisted Sample Game")
        self.assertEqual(other.name, "Second Game")
        self.assertNotEqual(first.rule_id, other.rule_id)

    def test_conflicting_ids_for_same_path_are_not_confirmed(self):
        data = self.fixture()
        other = data["manifest"].with_name("appmanifest_901002.acf")
        other.write_text('"AppState" { "appid" "901002" "name" "Second Game" "installdir" "TestGame" }')
        info = {"appid":901002,"common":{"name":"Second Game","type":"game"},
                "config":{"launch":{"0":{"executable":"GameMain.exe","config":{"oslist":"windows"}}}}}
        data["cache"].write_bytes(cache_bytes({901001:data["values"],901002:info}))
        decision = self.engine(data).classify(ProcessRecord(123, str(data["exe"]), "user", 1))
        self.assertFalse(decision.confirmed)
        self.assertIn("冲突", decision.name)

    def test_uninstalled_auto_game_removed_from_runtime_rules(self):
        data = self.fixture()
        engine = self.engine(data)
        self.assertTrue(engine.classify(ProcessRecord(123, str(data["exe"]), "user", 1)).confirmed)
        data["manifest"].unlink()
        engine.publish(build_index(steam_locations=[data["steam"]], records=[]))
        self.assertNotIn("steam:901001", engine.rules)
        self.assertFalse(engine.classify(ProcessRecord(123, str(data["exe"]), "user", 1)).confirmed)

    def test_linux_entry_not_accepted_even_if_game(self):
        data = self.fixture()
        data["values"]["config"]["launch"]["0"]["config"]["oslist"] = "linux"
        data["cache"].write_bytes(cache_bytes({901001:data["values"]}))
        self.assertFalse(self.engine(data).classify(ProcessRecord(123, str(data["exe"]), "user", 1)).confirmed)


if __name__ == "__main__":
    unittest.main()
