"""缓存 Windows PE 文件信息；读取前后身份一致才使用，不加载或运行游戏。"""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import ctypes
import os
from ctypes import wintypes
import struct
from focusguard.monitoring.core import canonical


def fingerprint(path):
    stat = Path(path).stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def watch_fingerprint(path):
    try:
        return fingerprint(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return ("unavailable", exc.errno)


def pe_image(path):
    with open(path, "rb") as handle:
        header = handle.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            return False
        offset = struct.unpack_from("<I", header, 60)[0]
        if not 64 <= offset <= 16 * 1024 * 1024:
            return False
        handle.seek(offset)
        signature = handle.read(24)
        if len(signature) != 24 or signature[:4] != b"PE\0\0":
            return False
        machine = struct.unpack_from("<H", signature, 4)[0]
        flags = struct.unpack_from("<H", signature, 22)[0]
        return machine in (0x14c, 0x8664, 0xaa64) and bool(flags & 2) and not bool(flags & 0x2000)


def version_strings(path):
    version = ctypes.WinDLL(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/version.dll"), use_last_error=True)
    size_fn = version.GetFileVersionInfoSizeW
    size_fn.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    size_fn.restype = wintypes.DWORD
    read_fn = version.GetFileVersionInfoW
    read_fn.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    read_fn.restype = wintypes.BOOL
    query = version.VerQueryValueW
    query.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    query.restype = wintypes.BOOL
    unused = wintypes.DWORD()
    size = size_fn(str(path), ctypes.byref(unused))
    if not size or size > 8 * 1024 * 1024:
        return ()
    buffer = ctypes.create_string_buffer(size)
    if not read_fn(str(path), 0, size, buffer):
        return ()
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not query(buffer, r"\VarFileInfo\Translation", ctypes.byref(pointer), ctypes.byref(length)) or not pointer.value:
        return ()
    if not 4 <= length.value <= 128 or length.value % 4:
        return ()
    translations = ctypes.string_at(pointer, length.value)
    results = []
    for offset in range(0, len(translations), 4):
        lang, codepage = struct.unpack_from("<HH", translations, offset)
        values = {}
        for key in ("ProductName", "CompanyName", "OriginalFilename", "FileDescription", "InternalName"):
            prefix = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{key}"
            if query(buffer, prefix, ctypes.byref(pointer), ctypes.byref(length)) and pointer.value and 0 < length.value <= 4096:
                values[key] = ctypes.wstring_at(pointer, length.value).rstrip("\0").strip()
        results.append(values)
    return tuple(results)


@dataclass(frozen=True)
class Evidence:
    stamp: tuple
    executable: bool
    versions: tuple


class FileEvidence:
    def __init__(self, max_entries=256, metadata_reader=version_strings):
        self.cache, self.max_entries = OrderedDict(), max_entries
        self.metadata_reader = metadata_reader

    def read(self, path):
        path = canonical(path)
        before = fingerprint(path)
        old = self.cache.get(path)
        if old and old.stamp == before:
            self.cache.move_to_end(path)
            return old
        executable = pe_image(path)
        evidence = Evidence(before, executable, self.metadata_reader(path) if executable else ())
        if fingerprint(path) != before:
            raise OSError("程序文件正在更新，暂不确认")
        self.cache[path] = evidence
        self.cache.move_to_end(path)
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)
        return evidence
