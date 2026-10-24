"""仅供模拟测试的安装清单与 PE 样本；不连接平台、不下载游戏。"""
import hashlib
from pathlib import Path
import struct
from focusguard.recognition.file_evidence import FileEvidence
from focusguard.recognition.engine import Recognizer
from focusguard.recognition.discovery import build_index


def write_pe(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(128)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 64)
    header[64:68] = b"PE\0\0"
    struct.pack_into("<H", header, 68, 0x8664)
    struct.pack_into("<H", header, 86, 2)
    path.write_bytes(header)
    return path


def encode_kv(data, keys=None):
    result = bytearray()
    for key, value in data.items():
        key_bytes = (key.encode() + b"\0") if keys is None else struct.pack("<I", keys.index(key))
        if isinstance(value, dict):
            result.extend(b"\0" + key_bytes + encode_kv(value, keys))
        elif isinstance(value, int):
            result.extend(b"\x02" + key_bytes + struct.pack("<i", value))
        else:
            result.extend(b"\x01" + key_bytes + str(value).encode() + b"\0")
    return bytes(result) + b"\x08"


def cache_bytes(records, version=41):
    keys = []
    def collect(values):
        for key, value in values.items():
            if key not in keys:
                keys.append(key)
            if isinstance(value, dict):
                collect(value)
    for values in records.values():
        collect({"appinfo": values})
    sections = bytearray()
    for appid, values in records.items():
        payload = encode_kv({"appinfo": values}, keys if version == 41 else None)
        fixed = struct.pack("<IIQ20sI", 2, 0, 0, b"\0" * 20, 0)
        if version >= 40:
            fixed += hashlib.sha1(payload).digest()
        sections.extend(struct.pack("<II", appid, len(fixed) + len(payload)) + fixed + payload)
    sections.extend(b"\0" * 4)
    header = struct.pack("<II", 0x07564400 + version, 1)
    if version == 41:
        header += struct.pack("<Q", 16 + len(sections))
        return header + sections + struct.pack("<I", len(keys)) + b"".join(k.encode() + b"\0" for k in keys)
    return header + sections


def make_steam(base, *, appid=730, app_type="game", relative="game/bin/win64/cs2.exe", version=41):
    steam = Path(base) / "Steam"
    steamapps = steam / "steamapps"
    cache = steam / "appcache" / "appinfo.vdf"
    cache.parent.mkdir(parents=True, exist_ok=True)
    steamapps.mkdir(exist_ok=True)
    game_root = steamapps / "common" / "TestGame"
    executable = write_pe(game_root / "game" / "bin" / "win64" / "cs2.exe")
    manifest = steamapps / f"appmanifest_{appid}.acf"
    manifest.write_text(f'"AppState" {{ "appid" "{appid}" "name" "Counter-Strike 2" "installdir" "TestGame" }}', encoding="utf-8")
    values = {"appid": appid, "common": {"name": "Counter-Strike 2", "type": app_type},
              "config": {"launch": {"0": {"executable": relative, "config": {"oslist": "windows"}}}}}
    cache.write_bytes(cache_bytes({appid: values}, version))
    return {"steam": steam, "exe": executable, "cache": cache, "manifest": manifest, "values": values, "root": game_root}


def engine_for(fixture, metadata=()):
    engine = Recognizer(files=FileEvidence(metadata_reader=lambda path: metadata))
    engine.publish(build_index(steam_locations=[fixture["steam"]], records=[]))
    return engine
