"""离线安装证据索引。只读安装记录、Steam 库清单和启动配置。"""
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import os
import winreg
from focusguard.monitoring.core import canonical
from .rules import RULES
from .steam.cache import text_vdf, appinfo
from .file_evidence import fingerprint


@dataclass(frozen=True)
class RegistryProof:
    hive: int
    key: str
    value: str
    expected: str
    view: int = 0

    def valid(self):
        try:
            with winreg.OpenKey(self.hive, self.key, 0, winreg.KEY_READ | self.view) as handle:
                current = winreg.QueryValueEx(handle, self.value)[0]
                return isinstance(current, str) and current == self.expected
        except OSError:
            return False


@dataclass(frozen=True)
class InstalledRecord:
    name: str
    publisher: str
    root: str
    product_id: str = ""
    registry_sources: tuple = ()


@dataclass(frozen=True)
class Binding:
    rule_id: str
    path: str
    reason: str
    sources: tuple = ()
    metadata_required: bool = False
    registry_sources: tuple = ()
    steam_direct: bool = False


@dataclass(frozen=True)
class Index:
    bindings: tuple = ()
    warnings: tuple = ()
    steam_rules: tuple = ()
    watch_sources: tuple = ()


def normalized(text):
    return " ".join(str(text).split()).casefold()


def local_absolute(value):
    return isinstance(value, str) and Path(value).is_absolute() and not PureWindowsPath(value).drive.startswith("\\\\")


def relative_exe(root, value):
    if not isinstance(value, str) or not value or "\0" in value or ":" in value or "%" in value:
        raise ValueError("启动路径不是安全相对路径")
    relative = PureWindowsPath(value)
    if relative.is_absolute() or relative.root or relative.drive or ".." in relative.parts or relative.suffix.casefold() != ".exe":
        raise ValueError("启动路径不是安全的游戏程序路径")
    base = canonical(str(root))
    target = canonical(str(Path(root).joinpath(*relative.parts)))
    if os.path.commonpath([base, target]) != base or target == base:
        raise ValueError("启动路径逃离安装目录")
    return target


def read_stable(path, limit):
    before = fingerprint(path)
    if before[2] > limit:
        raise ValueError("安装信息超过读取上限")
    with open(path, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit or before != fingerprint(path):
        raise OSError("安装信息正在更新，请稍后重试")
    return data, (canonical(str(path)), before)


def _value(key, name):
    try:
        value = winreg.QueryValueEx(key, name)[0]
        return value if isinstance(value, str) else ""
    except FileNotFoundError:
        return ""


def uninstall_records():
    records, accessible = [], False
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view) as key:
                    accessible = True
                    for i in range(min(winreg.QueryInfoKey(key)[0], 20000)):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                                name, publisher, location = _value(sub, "DisplayName"), _value(sub, "Publisher"), _value(sub, "InstallLocation")
                                if name and local_absolute(location) and Path(location).is_dir():
                                    records.append(InstalledRecord(name, publisher, canonical(location)))
                        except OSError:
                            continue
            except FileNotFoundError:
                accessible = True
            except PermissionError:
                continue
    if not accessible:
        raise OSError("无法读取当前安装信息")
    return records


def steam_roots():
    roots = set()
    for hive, key_path, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                location = _value(key, value)
                if local_absolute(location):
                    roots.add(canonical(location))
        except OSError:
            pass
    default = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam"
    if (default / "steam.exe").is_file():
        roots.add(canonical(str(default)))
    return sorted(roots)


def product_install_records():
    """读取具体游戏的安装键；不把 WeGame 平台目录当作游戏目录。"""
    records = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            key_path = r"SOFTWARE\Tencent\LOL"
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                    raw = _value(key, "InstallPath")
                    location = raw.strip('"')
                    if not local_absolute(location) or not Path(location).is_dir():
                        continue
                    proof = RegistryProof(hive, key_path, "InstallPath", raw, view)
                    records.append(InstalledRecord("英雄联盟", "", canonical(location), "tencent_lol", (proof,)))
            except OSError:
                continue
    return records


def build_index(cancelled=lambda: False, *, steam_locations=None, records=None, rules=RULES):
    from .steam.discovery import scan_steam
    bindings, warnings, discovered, watches = [], [], {}, {}
    locations = steam_roots() if steam_locations is None else steam_locations
    for location in locations:
        try:
            scan = scan_steam(location, [r for r in rules if r.steam_id], cancelled)
            bindings.extend(scan.bindings)
            warnings.extend(scan.warnings)
            discovered.update((r.id, r) for r in scan.steam_rules)
            for path, stamp in scan.watch_sources:
                watches.setdefault(path, stamp)
        except (OSError, ValueError, KeyError, UnicodeError) as exc:
            warnings.append(f"Steam 安装信息不可用：{exc}")
    for record in (uninstall_records() + product_install_records()) if records is None else records:
        if cancelled():
            return Index()
        for rule in rules:
            registry_match = (rule.registry_product and record.product_id == rule.registry_product
                              and record.registry_sources and all(proof.valid() for proof in record.registry_sources))
            uninstall_match = (rule.products and normalized(record.name) in {normalized(v) for v in rule.products}
                               and normalized(record.publisher) in {normalized(v) for v in rule.publishers})
            if not (registry_match or uninstall_match):
                continue
            for relative in rule.relative_paths:
                try:
                    path = relative_exe(record.root, relative)
                    if Path(path).is_file():
                        reason = "腾讯英雄联盟专用安装键、准确路径与文件产品信息一致" if registry_match else "准确安装记录、发布者与文件产品信息一致"
                        bindings.append(Binding(rule.id, path, reason, metadata_required=True, registry_sources=record.registry_sources))
                except (OSError, ValueError):
                    continue
    unique = {(b.rule_id, b.path, b.reason): b for b in bindings}
    return Index(tuple(unique.values()), tuple(dict.fromkeys(warnings)), tuple(discovered.values()), tuple(watches.items()))
