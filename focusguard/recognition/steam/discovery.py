"""自动收录 Steam 本机全部已安装游戏；不把普通软件或辅助入口作为游戏。"""
from dataclasses import replace
from itertools import islice
from pathlib import Path, PureWindowsPath
import os
import re
from focusguard.monitoring.core import canonical
from ..discovery import Binding, Index, normalized, local_absolute, relative_exe, read_stable
from ..file_evidence import fingerprint, watch_fingerprint
from ..rules import Rule, auxiliary_entry
from .cache import text_vdf, appinfo


def scan_steam(location, overrides, cancelled):
    steam = Path(location)
    warnings, bindings, discovered, watches = [], [], {}, {}
    def watch(path):
        key = canonical(str(path))
        watches.setdefault(key, watch_fingerprint(path))
    def result():
        return Index(tuple(bindings), tuple(dict.fromkeys(warnings)), tuple(discovered.values()), tuple(watches.items()))
    library_file = steam / "steamapps/libraryfolders.vdf"
    cache_file = steam / "appcache/appinfo.vdf"
    watch(library_file)
    watch(cache_file)
    libraries = {canonical(str(steam)): ()}
    try:
        if library_file.is_file():
            raw, library_stamp = read_stable(library_file, 2 * 1024 * 1024)
            values = text_vdf(raw.decode("utf-8-sig"))["libraryfolders"]
            if not isinstance(values, dict):
                raise ValueError("Steam 库信息不完整")
            libraries[canonical(str(steam))] = (library_stamp,)
            for key, value in values.items():
                if key.isdigit():
                    path = value.get("path", "") if isinstance(value, dict) else value
                    if local_absolute(path):
                        libraries[canonical(path)] = (library_stamp,)
    except (OSError, ValueError, KeyError, UnicodeError) as exc:
        warnings.append(f"Steam 库信息不可用：{exc}")
        return result()
    installed = []
    for library, stamps in libraries.items():
        apps = Path(library) / "steamapps"
        watch(apps)
        for number, manifest in enumerate(islice(apps.glob("appmanifest_*.acf"), 5001)):
            if cancelled():
                return Index()
            if number == 5000:
                warnings.append("Steam 库安装项超过安全读取上限，部分条目未处理")
                break
            match = re.fullmatch(r"appmanifest_([0-9]+)\.acf", manifest.name)
            if not match:
                continue
            appid = int(match[1])
            try:
                if not 1 <= appid <= 0xffffffff:
                    raise ValueError("游戏标识无效")
                raw, stamp = read_stable(manifest, 1024 * 1024)
                state = text_vdf(raw.decode("utf-8-sig")).get("appstate", {})
                if not isinstance(state, dict) or str(state.get("appid")) != str(appid):
                    raise ValueError("安装清单标识不符")
                folder = state.get("installdir", "")
                part = PureWindowsPath(folder) if isinstance(folder, str) else PureWindowsPath()
                if len(part.parts) != 1 or part.drive or part.root or folder in (".", "..") or ":" in folder or "\0" in folder:
                    raise ValueError("安装目录无效")
                common_root = canonical(str(apps / "common"))
                game_root = canonical(str(Path(common_root) / folder))
                if game_root == common_root or os.path.commonpath([common_root, game_root]) != common_root or not Path(game_root).is_dir():
                    raise ValueError("安装目录不存在或越界")
                installed.append((appid, game_root, state.get("name", ""), stamps + (stamp,)))
            except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
                warnings.append(f"Steam {appid}：{exc}，未确认")
    if not installed:
        return result()
    known = {rule.steam_id: rule for rule in overrides}
    try:
        raw, cache_stamp = read_stable(cache_file, 256 * 1024 * 1024)
        cache = appinfo(raw, {item[0] for item in installed})
    except (OSError, ValueError, UnicodeError) as exc:
        warnings.append(f"Steam 启动配置不可用：{exc}；未确认这些安装项为游戏")
        return result()
    for appid, game_root, manifest_name, stamps in installed:
        if cancelled():
            return Index()
        try:
            info = cache.get(appid, {})
            common = info.get("common", {})
            if str(info.get("appid")) != str(appid) or normalized(common.get("type", "")) != "game":
                if appid in known:
                    warnings.append(f"{known[appid].name}：缓存没有确认游戏身份")
                continue
            name = common.get("name", "")
            if not isinstance(name, str) or not name.strip() or normalized(name) != normalized(manifest_name):
                raise ValueError("安装清单与游戏资料名称不一致")
            launch = info.get("config", {}).get("launch", {})
            if not isinstance(launch, dict):
                launch = {}
            options, candidate_names = [], []
            for item in launch.values():
                if not isinstance(item, dict) or not isinstance(item.get("config", {}), dict):
                    continue
                executable = item.get("executable", "")
                if not isinstance(executable, str):
                    continue
                config = item.get("config", {})
                systems = {v.strip().casefold() for v in str(config.get("oslist", common.get("oslist", ""))).split(",")}
                if "windows" not in systems:
                    continue
                try:
                    path = relative_exe(game_root, executable)
                except ValueError:
                    continue
                candidate_names.append(PureWindowsPath(executable).name.casefold())
                options.append((item, executable, path))
            rule = known.get(appid)
            if rule is None:
                rule = Rule(f"steam:{appid}", name.strip()[:160], tuple(dict.fromkeys(candidate_names)), appid, auto_discovered=True)
            elif candidate_names:
                direct_names = [PureWindowsPath(exe).name.casefold() for item, exe, path in options
                                if not auxiliary_entry(exe, item.get("description", ""), item.get("type", ""))]
                rule = replace(rule, executables=tuple(dict.fromkeys((*rule.executables, *direct_names))))
            discovered[rule.id] = rule
            count, indirect = 0, []
            for item, executable, path in options:
                if not Path(path).is_file():
                    continue
                configured = PureWindowsPath(executable).as_posix().casefold()
                if configured in {PureWindowsPath(v).as_posix().casefold() for v in rule.indirect_launchers}:
                    indirect.append((path, fingerprint(path)))
                if auxiliary_entry(executable, item.get("description", ""), item.get("type", "")):
                    continue
                bindings.append(Binding(rule.id, path, f"Steam AppID {appid} 游戏分类、安装身份与官方客户端直接启动路径一致", stamps + (cache_stamp,), steam_direct=True))
                count += 1
            if not count and indirect and rule.file_products and rule.companies:
                for relative in rule.relative_paths:
                    path = relative_exe(game_root, relative)
                    if Path(path).is_file() and Path(path).name.casefold() in rule.executables:
                        reason = f"Steam AppID {appid} 安装身份、已登记引导配置与游戏本体产品信息一致"
                        bindings.append(Binding(rule.id, path, reason, stamps + (cache_stamp,) + tuple(indirect), metadata_required=True))
                        count += 1
            if not count:
                warnings.append(f"{rule.name}：已收录游戏身份，但尚无可靠 Windows 游戏本体路径，不触发")
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            warnings.append(f"Steam {appid}：{exc}，未确认")
    return result()
