from pathlib import Path
import os
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
from focusguard.monitoring.core import ProcessRecord
from focusguard.recognition.discovery import build_index, relative_exe, InstalledRecord, read_stable
from focusguard.recognition.file_evidence import FileEvidence, pe_image, version_strings
from focusguard.recognition.engine import Recognizer
from focusguard.recognition.rules import RULES
from focusguard.recognition.steam.cache import appinfo, text_vdf
from helpers import make_steam, engine_for, write_pe, cache_bytes


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = make_steam(self.temp.name)
        self.proc = ProcessRecord(1, str(self.fixture["exe"]), "user", 1)

    def rewrite_cache(self, values=None):
        self.fixture["cache"].write_bytes(cache_bytes({730: self.fixture["values"] if values is None else values}))

    def test_full_steam_evidence_confirms(self):
        decision = engine_for(self.fixture).classify(self.proc)
        self.assertTrue(decision.confirmed)
        self.assertIn("Steam AppID 730", decision.reason)

    def test_only_filename_is_not_enough(self):
        engine = Recognizer()
        engine.publish(build_index(steam_locations=[], records=[]))
        self.assertFalse(engine.classify(self.proc).confirmed)

    def test_same_name_outside_installation_unknown(self):
        engine = engine_for(self.fixture)
        other = write_pe(Path(self.temp.name) / "other" / "cs2.exe")
        self.assertFalse(engine.classify(ProcessRecord(2, str(other), "user", 1)).confirmed)

    def test_steam_application_is_not_game(self):
        self.fixture["values"]["common"]["type"] = "Application"
        self.rewrite_cache()
        index = build_index(steam_locations=[self.fixture["steam"]], records=[])
        self.assertFalse(index.bindings)
        self.assertTrue(index.warnings)

    def test_unlisted_verified_steam_game_now_binds(self):
        fixture = make_steam(Path(self.temp.name) / "other", appid=999999)
        self.assertTrue(build_index(steam_locations=[fixture["steam"]], records=[]).bindings)

    def test_manifest_appid_mismatch(self):
        self.fixture["manifest"].write_text('"AppState" { "appid" "570" "name" "Counter-Strike 2" "installdir" "TestGame" }')
        self.assertFalse(engine_for(self.fixture).classify(self.proc).confirmed)

    def test_cache_appid_mismatch(self):
        self.fixture["values"]["appid"] = 570
        self.rewrite_cache()
        self.assertFalse(engine_for(self.fixture).classify(self.proc).confirmed)

    def test_manifest_and_cache_name_must_agree(self):
        self.fixture["values"]["common"]["name"] = "Another application"
        self.rewrite_cache()
        self.assertFalse(engine_for(self.fixture).classify(self.proc).confirmed)

    def test_linux_launch_not_bound(self):
        self.fixture["values"]["config"]["launch"]["0"]["config"]["oslist"] = "linux"
        self.rewrite_cache()
        self.assertFalse(engine_for(self.fixture).classify(self.proc).confirmed)

    def test_launcher_and_tools_not_bound(self):
        for name in ("steam.exe", "csgocfg.exe", "javaw.exe", "RiotClientServices.exe"):
            self.fixture["values"]["config"]["launch"]["0"]["executable"] = "game/" + name
            self.rewrite_cache()
            engine = engine_for(self.fixture)
            self.assertFalse(engine.status()["bindings"])
            candidate = ProcessRecord(4, str(self.fixture["root"] / "game" / name), "user", 1)
            self.assertFalse(engine.classify(candidate).confirmed)

    def test_missing_cache_is_candidate_only(self):
        self.fixture["cache"].unlink()
        engine = engine_for(self.fixture)
        decision = engine.classify(self.proc)
        self.assertFalse(decision.confirmed)
        self.assertEqual(decision.name, "反恐精英 2")
        self.assertTrue(engine.status()["warnings"])

    def test_secondary_library_install_matches_its_own_path(self):
        library = Path(self.temp.name) / "AdditionalLibrary-中文"
        library.mkdir()
        moved = library / "steamapps"
        (self.fixture["steam"] / "steamapps").rename(moved)
        (self.fixture["steam"] / "steamapps").mkdir()
        escaped = str(library).replace("\\", "\\\\")
        (self.fixture["steam"] / "steamapps/libraryfolders.vdf").write_text(f'"libraryfolders" {{ "1" {{ "path" "{escaped}" }} }}', encoding="utf-8")
        engine = engine_for(self.fixture)
        new_process = ProcessRecord(1, str(moved / "common/TestGame/game/bin/win64/cs2.exe"), "user", 1)
        self.assertTrue(engine.classify(new_process).confirmed)
        self.assertFalse(engine.classify(self.proc).confirmed)

    def test_refresh_removes_reclassified_software(self):
        engine = engine_for(self.fixture)
        self.assertTrue(engine.classify(self.proc).confirmed)
        self.fixture["values"]["common"]["type"] = "Application"
        self.rewrite_cache()
        engine.publish(build_index(steam_locations=[self.fixture["steam"]], records=[]))
        self.assertFalse(engine.classify(self.proc).confirmed)

    def test_broken_cache_is_candidate_only(self):
        self.fixture["cache"].write_bytes(b"corrupt cache")
        self.assertFalse(engine_for(self.fixture).classify(self.proc).confirmed)

    def test_source_change_immediately_invalidates(self):
        engine = engine_for(self.fixture)
        old = engine.classify(self.proc)
        self.assertTrue(old.confirmed)
        self.fixture["manifest"].write_text(self.fixture["manifest"].read_text() + "\n")
        self.assertFalse(engine.still_valid(old))
        self.assertFalse(engine.classify(self.proc).confirmed)

    def test_index_refresh_invalidates_old_decision(self):
        engine = engine_for(self.fixture)
        old = engine.classify(self.proc)
        engine.begin_refresh()
        self.assertFalse(engine.still_valid(old))
        engine.publish(build_index(steam_locations=[self.fixture["steam"]], records=[]))
        self.assertTrue(engine.classify(self.proc).confirmed)
        self.assertFalse(engine.still_valid(old))

    def test_moved_file_requires_refreshed_install(self):
        engine = engine_for(self.fixture)
        new = self.fixture["root"].parent / "MovedGame"
        self.fixture["root"].rename(new)
        moved = ProcessRecord(1, str(new / "game/bin/win64/cs2.exe"), "user", 1)
        self.assertFalse(engine.classify(self.proc).confirmed)
        self.assertFalse(engine.classify(moved).confirmed)
        self.fixture["manifest"].write_text('"AppState" { "appid" "730" "name" "Counter-Strike 2" "installdir" "MovedGame" }')
        engine.publish(build_index(steam_locations=[self.fixture["steam"]], records=[]))
        self.assertTrue(engine.classify(moved).confirmed)

    def test_file_change_invalidates_confirmation(self):
        engine = engine_for(self.fixture)
        old = engine.classify(self.proc)
        self.fixture["exe"].write_bytes(b"not an executable")
        self.assertFalse(engine.still_valid(old))
        self.assertFalse(engine.classify(self.proc).confirmed)

    def test_unknown_non_game_not_logged_as_candidate(self):
        engine = engine_for(self.fixture)
        word = ProcessRecord(2, str(Path(self.temp.name) / "WINWORD.EXE"), "user", 1)
        self.assertEqual(engine.observe([word], "user"), ())

    def test_observations_filter_other_users(self):
        engine = engine_for(self.fixture)
        self.assertEqual(engine.observe([self.proc], "other"), ())

    def test_candidate_only_game_no_shutdown_evidence(self):
        engine = engine_for(self.fixture)
        decision = engine.classify(ProcessRecord(2, str(Path(self.temp.name) / "GenshinImpact.exe"), "user", 1))
        self.assertFalse(decision.confirmed)
        self.assertIn("尚无完整可信规则", decision.reason)

    def test_relative_path_escape_rejected(self):
        for value in ("../cs2.exe", "C:/cs2.exe", "//server/share/cs2.exe", "game/../../cs2.exe", "game/cs2.exe:extra", "%TEMP%/cs2.exe", "game.bat"):
            with self.subTest(path=value), self.assertRaises(ValueError):
                relative_exe(self.fixture["root"], value)

    def test_escaped_launch_path_cannot_bind(self):
        self.fixture["values"]["config"]["launch"]["0"]["executable"] = "../cs2.exe"
        self.rewrite_cache()
        self.assertFalse(engine_for(self.fixture).status()["bindings"])

    def test_no_whole_disk_scan(self):
        with patch("os.walk", side_effect=AssertionError("不应扫描全盘")):
            self.assertTrue(engine_for(self.fixture).classify(self.proc).confirmed)

    def test_cancelled_install_scan_empty(self):
        index = build_index(lambda: True, steam_locations=[self.fixture["steam"]], records=[])
        self.assertEqual(index.bindings, ())

    def test_read_stable_rejects_changed_source(self):
        with patch("focusguard.recognition.discovery.fingerprint", side_effect=[(0, 1, 128, 1, 1), (0, 1, 128, 2, 1)]):
            with self.assertRaises(OSError):
                read_stable(self.fixture["manifest"], 1024 * 1024)

    def test_install_record_and_metadata_needed_together(self):
        root = Path(self.temp.name) / "Riot" / "League of Legends"
        path = write_pe(root / "Game" / "League of Legends.exe")
        records = [InstalledRecord("League of Legends", "Riot Games, Inc.", str(root))]
        index = build_index(steam_locations=[], records=records)
        process = ProcessRecord(2, str(path), "user", 1)
        engine = Recognizer(files=FileEvidence(metadata_reader=lambda p: ({"ProductName": "League of Legends", "CompanyName": "Riot Games, Inc."},)))
        engine.publish(index)
        self.assertTrue(engine.classify(process).confirmed)
        no_metadata = Recognizer(files=FileEvidence(metadata_reader=lambda p: ()))
        no_metadata.publish(index)
        self.assertFalse(no_metadata.classify(process).confirmed)

    def test_wrong_metadata_product_or_company_not_confirmed(self):
        root = Path(self.temp.name) / "League of Legends"
        path = write_pe(root / "Game/League of Legends.exe")
        index = build_index(steam_locations=[], records=[InstalledRecord("League of Legends", "Riot Games", str(root))])
        for metadata in ({"ProductName": "Windows", "CompanyName": "Riot Games"},
                         {"ProductName": "League of Legends", "CompanyName": "Another Company"},
                         {"ProductName": "League of Legends", "CompanyName": "Riot Games", "OriginalFilename": "ping.exe"}):
            engine = Recognizer(files=FileEvidence(metadata_reader=lambda p: (metadata,)))
            engine.publish(index)
            self.assertFalse(engine.classify(ProcessRecord(2, str(path), "user", 1)).confirmed)

    def test_wrong_install_publisher_not_bound(self):
        root = Path(self.temp.name) / "League of Legends"
        write_pe(root / "Game/League of Legends.exe")
        index = build_index(steam_locations=[], records=[InstalledRecord("League of Legends", "Not Riot", str(root))])
        self.assertFalse(index.bindings)

    def test_file_cache_reuses_and_invalidates_by_file_change(self):
        reader = Mock(return_value=())
        files = FileEvidence(metadata_reader=reader)
        files.read(self.fixture["exe"])
        files.read(self.fixture["exe"])
        self.assertEqual(reader.call_count, 1)
        self.fixture["exe"].write_bytes(self.fixture["exe"].read_bytes() + b"updated")
        files.read(self.fixture["exe"])
        self.assertEqual(reader.call_count, 2)

    def test_replaced_file_same_timestamp_not_reused(self):
        reader = Mock(return_value=())
        files = FileEvidence(metadata_reader=reader)
        old = files.read(self.fixture["exe"])
        replacement = write_pe(Path(self.temp.name) / "replacement.exe")
        os.utime(replacement, ns=(old.stamp[3], old.stamp[3]))
        os.replace(replacement, self.fixture["exe"])
        files.read(self.fixture["exe"])
        self.assertEqual(reader.call_count, 2)

    def test_metadata_change_during_read_rejected(self):
        path = self.fixture["exe"]
        def mutate(p):
            path.write_bytes(path.read_bytes() + b"change")
            return ()
        with self.assertRaises(OSError):
            FileEvidence(metadata_reader=mutate).read(path)

    def test_file_cache_is_bounded(self):
        files = FileEvidence(max_entries=2, metadata_reader=lambda p: ())
        for i in range(4):
            files.read(write_pe(Path(self.temp.name) / f"{i}.exe"))
        self.assertEqual(len(files.cache), 2)

    def test_real_system_binary_metadata_is_not_game(self):
        versions = version_strings(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/ping.exe")
        self.assertTrue(versions)
        self.assertFalse(any(v.get("ProductName") == "League of Legends" for v in versions))


class CacheFormatTests(unittest.TestCase):
    def test_supported_binary_versions(self):
        for version in (39, 40, 41):
            data = cache_bytes({730: {"appid": 730, "common": {"type": "game"}}}, version)
            self.assertEqual(appinfo(data, {730})[730]["common"]["type"], "game")

    def test_unsupported_version(self):
        with self.assertRaises(ValueError):
            appinfo(struct.pack("<II", 0x07564430, 1), {730})

    def test_only_requested_apps_returned(self):
        data = cache_bytes({730: {"appid": 730}, 570: {"appid": 570}}, 41)
        self.assertEqual(set(appinfo(data, {730})), {730})

    def test_truncated_cache_rejected(self):
        data = cache_bytes({730: {"appid": 730}})
        for count in (0, 7, 15, len(data) - 5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                appinfo(data[:count], {730})

    def test_binary_checksum_rejected(self):
        data = bytearray(cache_bytes({730: {"appid": 730}}, 40))
        data[60] ^= 1
        with self.assertRaises(ValueError):
            appinfo(bytes(data), {730})

    def test_text_duplicate_keys_rejected(self):
        with self.assertRaises(ValueError):
            text_vdf('"AppState" { "appid" "730" "appid" "570" }')

    def test_text_truncation_rejected(self):
        with self.assertRaises(ValueError):
            text_vdf('"AppState" { "appid" "730"')

    def test_text_path_and_comments(self):
        result = text_vdf('// comment\n"libraryfolders" { "0" { "path" "E:\\\\Steam" } }')
        self.assertEqual(result["libraryfolders"]["0"]["path"], "E:\\Steam")


if __name__ == "__main__":
    unittest.main()
