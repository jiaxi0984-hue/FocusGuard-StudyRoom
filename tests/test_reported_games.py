"""复现国服大厅／对局及 Apex 间接启动的真实结构，所有系统动作均隔离。"""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
import winreg
from focusguard.monitoring.core import ProcessRecord, Settings, Store, Monitor
from focusguard.recognition.discovery import build_index, InstalledRecord
from focusguard.recognition.file_evidence import FileEvidence
from focusguard.recognition.engine import Recognizer
from helpers import make_steam, write_pe, cache_bytes


@contextmanager
def tencent_install(root):
    values = {"path": str(root)}
    handle = object()
    def open_key(hive, key, *args):
        if hive != winreg.HKEY_CURRENT_USER or key.casefold() != r"software\tencent\lol":
            raise FileNotFoundError(key)
        context = MagicMock()
        context.__enter__.return_value = handle
        return context
    def query(key, name):
        if key is handle and name == "InstallPath":
            return values["path"], winreg.REG_SZ
        raise FileNotFoundError(name)
    with patch("focusguard.recognition.discovery.uninstall_records", return_value=[]), patch("focusguard.recognition.discovery.steam_roots", return_value=[]), patch.object(winreg, "OpenKey", side_effect=open_key), patch.object(winreg, "QueryValueEx", side_effect=query):
        yield values


def riot_metadata(path):
    filename = Path(path).name
    product = "League of Legends (TM) Client" if filename.casefold() == "league of legends.exe" else "League of Legends"
    return ({"ProductName": product, "CompanyName": "Riot Games, Inc.", "OriginalFilename": filename},)


class ReportedGameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_cn_lol_product_suffix_supported(self):
        root = self.root / "League of Legends"
        body = write_pe(root / "Game/League of Legends.exe")
        index = build_index(steam_locations=[], records=[InstalledRecord("League of Legends", "Riot Games", str(root))])
        engine = Recognizer(files=FileEvidence(metadata_reader=riot_metadata))
        engine.publish(index)
        self.assertTrue(engine.classify(ProcessRecord(1, str(body), "user", 1)).confirmed)

    def test_tencent_install_key_supports_game_and_lobby(self):
        root = self.root / "WeGameApps" / "英雄联盟"
        body = write_pe(root / "Game/League of Legends.exe")
        lobby = write_pe(root / "LeagueClient/LeagueClientUx.exe")
        with tencent_install(root), patch("focusguard.windows.system.has_visible_window", return_value=True, create=True):
            engine = Recognizer(files=FileEvidence(metadata_reader=riot_metadata))
            engine.publish(build_index())
            self.assertTrue(engine.classify(ProcessRecord(1, str(body), "user", 1)).confirmed)
            self.assertTrue(engine.classify(ProcessRecord(2, str(lobby), "user", 1)).confirmed)

    def test_hidden_lobby_does_not_trigger(self):
        root = self.root / "LOL"
        lobby = write_pe(root / "LeagueClient/LeagueClientUx.exe")
        with tencent_install(root), patch("focusguard.windows.system.has_visible_window", return_value=True, create=True) as visible:
            engine = Recognizer(files=FileEvidence(metadata_reader=riot_metadata))
            engine.publish(build_index())
            process = ProcessRecord(2, str(lobby), "user", 1)
            decision = engine.classify(process)
            self.assertTrue(decision.confirmed)
            visible.return_value = False
            self.assertFalse(engine.classify(process).confirmed)
            self.assertFalse(engine.still_valid(decision))

    def test_changed_tencent_key_invalidates_confirmation(self):
        root = self.root / "LOL"
        body = write_pe(root / "Game/League of Legends.exe")
        with tencent_install(root) as registry:
            engine = Recognizer(files=FileEvidence(metadata_reader=riot_metadata))
            engine.publish(build_index())
            process = ProcessRecord(1, str(body), "user", 1)
            decision = engine.classify(process)
            self.assertTrue(decision.confirmed)
            registry["path"] = str(self.root / "OtherLOL")
            self.assertFalse(engine.classify(process).confirmed)
            self.assertFalse(engine.still_valid(decision))

    def test_lobby_close_during_persistence_cancels_action(self):
        root = self.root / "LOL"
        lobby = write_pe(root / "LeagueClient/LeagueClientUx.exe")
        with tencent_install(root), patch("focusguard.windows.system.has_visible_window", return_value=True, create=True) as visible:
            engine = Recognizer(files=FileEvidence(metadata_reader=riot_metadata))
            engine.publish(build_index())
            store = Store(self.root / "config.json")
            real_save = store.save
            def save(settings):
                real_save(settings)
                if settings.tripped:
                    visible.return_value = False
            store.save = save
            action = Mock()
            monitor = Monitor(Settings(), store, "user", action, Mock(), engine, lambda record: True)
            monitor.arm(True)
            self.assertIsNone(monitor.inspect([ProcessRecord(2, str(lobby), "user", 1)]))
            action.assert_not_called()

    def test_renamed_lobby_without_riot_product_rejected(self):
        root = self.root / "LOL"
        lobby = write_pe(root / "LeagueClient/LeagueClientUx.exe")
        with tencent_install(root), patch("focusguard.windows.system.has_visible_window", return_value=True, create=True):
            engine = Recognizer(files=FileEvidence(metadata_reader=lambda p: ({"ProductName": "Windows", "CompanyName": "Microsoft", "OriginalFilename": "PING.EXE"},)))
            engine.publish(build_index())
            self.assertFalse(engine.classify(ProcessRecord(2, str(lobby), "user", 1)).confirmed)

    def test_wegame_itself_never_counts_as_lobby(self):
        root = self.root / "LOL"
        platform = write_pe(root / "wegame.exe")
        with tencent_install(root), patch("focusguard.windows.system.has_visible_window", return_value=True, create=True):
            engine = Recognizer(files=FileEvidence(metadata_reader=riot_metadata))
            engine.publish(build_index())
            self.assertFalse(engine.classify(ProcessRecord(2, str(platform), "user", 1)).confirmed)

    def apex(self, name="r5apex_dx12.exe"):
        fixture = make_steam(self.root, appid=1172470, relative="EAAntiCheat.GameServiceLauncher.exe")
        fixture["values"]["common"]["name"] = "Apex Legends"
        fixture["cache"].write_bytes(cache_bytes({1172470: fixture["values"]}))
        fixture["manifest"].write_text('"AppState" { "appid" "1172470" "name" "Apex Legends" "installdir" "TestGame" }')
        launcher = write_pe(fixture["root"] / "EAAntiCheat.GameServiceLauncher.exe")
        body = write_pe(fixture["root"] / name)
        return fixture, launcher, body

    def apex_engine(self, fixture, metadata=None):
        if metadata is None:
            metadata = ({"ProductName": "Apex Legends", "CompanyName": "Respawn Entertainment", "OriginalFilename": "r5apex.exe"},)
        engine = Recognizer(files=FileEvidence(metadata_reader=lambda p: metadata))
        engine.publish(build_index(steam_locations=[fixture["steam"]], records=[]))
        return engine

    def test_apex_dx12_indirect_launcher_identifies_body(self):
        fixture, launcher, body = self.apex()
        engine = self.apex_engine(fixture)
        self.assertTrue(engine.classify(ProcessRecord(1, str(body), "user", 1)).confirmed)
        self.assertFalse(engine.classify(ProcessRecord(2, str(launcher), "user", 1)).confirmed)

    def test_apex_legacy_name_also_needs_indirect_binding(self):
        fixture, _, body = self.apex("r5apex.exe")
        self.assertTrue(self.apex_engine(fixture).classify(ProcessRecord(1, str(body), "user", 1)).confirmed)

    def test_apex_renamed_non_game_not_confirmed(self):
        fixture, _, body = self.apex()
        metadata = ({"ProductName": "Microsoft Windows", "CompanyName": "Microsoft", "OriginalFilename": "PING.EXE"},)
        self.assertFalse(self.apex_engine(fixture, metadata).classify(ProcessRecord(1, str(body), "user", 1)).confirmed)

    def test_apex_missing_metadata_not_confirmed(self):
        fixture, _, body = self.apex()
        self.assertFalse(self.apex_engine(fixture, ()).classify(ProcessRecord(1, str(body), "user", 1)).confirmed)

    def test_apex_non_game_appid_never_qualifies(self):
        fixture, _, body = self.apex()
        fixture["values"]["common"]["type"] = "Application"
        fixture["cache"].write_bytes(cache_bytes({1172470: fixture["values"]}))
        self.assertFalse(self.apex_engine(fixture).classify(ProcessRecord(1, str(body), "user", 1)).confirmed)

    def test_apex_unregistered_loader_not_enough(self):
        fixture, _, body = self.apex()
        write_pe(fixture["root"] / "SomeLoader.exe")
        fixture["values"]["config"]["launch"]["0"]["executable"] = "SomeLoader.exe"
        fixture["cache"].write_bytes(cache_bytes({1172470: fixture["values"]}))
        self.assertFalse(self.apex_engine(fixture).classify(ProcessRecord(1, str(body), "user", 1)).confirmed)

    def test_apex_removed_indirect_source_invalidates(self):
        fixture, launcher, body = self.apex()
        engine = self.apex_engine(fixture)
        decision = engine.classify(ProcessRecord(1, str(body), "user", 1))
        self.assertTrue(decision.confirmed)
        launcher.unlink()
        self.assertFalse(engine.still_valid(decision))
        self.assertFalse(engine.classify(ProcessRecord(1, str(body), "user", 1)).confirmed)


if __name__ == "__main__":
    unittest.main()
