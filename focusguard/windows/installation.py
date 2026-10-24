"""当前用户安装、自启与可核验的卸载；不需要系统服务或管理员持久化。"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
import winreg
from focusguard import VERSION
from focusguard.windows.autostart_policy import (before_deadline, new_policy, validate_policy,
                                                DEADLINE_TEXT)

APP_ID = "FocusGuard.StudyRoom.1"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\FocusGuardStudyRoom"
STARTUP_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
RUN_NAME = "FocusGuardStudyRoom"
UNINSTALL_NAMES = ("DisplayName", "DisplayVersion", "Publisher", "InstallLocation", "DisplayIcon",
                   "UninstallString", "NoModify", "NoRepair")


@dataclass(frozen=True)
class AutostartState:
    enabled: bool = False
    limited: bool = False
    expired: bool = False
    system_disabled: bool = False


def root_dir():
    return Path(os.environ["LOCALAPPDATA"]) / "FocusGuard"


def exe_path():
    return root_dir() / "App" / "FocusGuard.exe"


def is_installed():
    return bool(getattr(sys, "frozen", False)) and Path(sys.executable).resolve() == exe_path().resolve()


def launch_command(executable, *args):
    return subprocess.list2cmdline([str(executable), *args])


def _run_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0]
    except FileNotFoundError:
        return None


def _system_startup_disabled():
    # 只读 Windows 的禁用选择，绝不写入 StartupApproved 或试图绕过它。
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY) as key:
            value, kind = winreg.QueryValueEx(key, RUN_NAME)
        return (kind != winreg.REG_BINARY or not isinstance(value, bytes) or len(value) < 4
                or int.from_bytes(value[:4], "little") not in (2, 6))
    except FileNotFoundError:
        return False


def autostart_enabled():
    try:
        return (_run_value() == launch_command(exe_path(), "--autostart")
                and not _system_startup_disabled())
    except OSError:
        return False


def _write_autostart(enabled):
    expected = launch_command(exe_path(), "--autostart")
    current = _run_value()
    if current is not None and current != expected:
        if enabled:
            raise ValueError("同名启动项指向其他命令，已停止覆盖。")
        return  # 不删除已被用户或其他软件替换的值。
    if enabled:
        if len(expected) > 260:
            raise ValueError("启动路径过长，无法注册登录自启。")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, expected)
    elif current is not None:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def _check_install_paths():
    paths = (root_dir(), exe_path().parent, exe_path(), exe_path().parent / "install.json")
    if any(path.is_symlink() or path.is_junction() for path in paths):
        raise ValueError("安装路径是链接，已停止覆盖或删除。")


def _read_install_record():
    _check_install_paths()
    manifest = json.loads((exe_path().parent / "install.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("id") != APP_ID or manifest.get("exe") != "FocusGuard.exe":
        raise ValueError("安装标记不匹配，已停止修改。")
    validate_policy(manifest.get("autostart_policy"))
    return manifest


def read_manifest():
    manifest = _read_install_record()
    if hashlib.sha256(exe_path().read_bytes()).hexdigest() != manifest.get("sha256"):
        raise ValueError("程序文件与安装记录不一致，已停止覆盖或删除。")
    return manifest


def _write_manifest(manifest):
    path = exe_path().parent / "install.json"
    descriptor, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def autostart_state(now=None):
    policy = _read_install_record().get("autostart_policy") if is_installed() else None
    expired = bool(policy and (policy["expired"] or not before_deadline(now)))
    disabled = _system_startup_disabled()
    return AutostartState(_run_value() == launch_command(exe_path(), "--autostart") and not disabled,
                          bool(policy and not expired), expired, disabled)


def set_autostart(enabled):
    if enabled and not is_installed():
        raise ValueError("请先点击“安装到本机”，再开启登录自启。")
    state = autostart_state()
    if not enabled and state.limited:
        raise ValueError(f"备考限期自启持续到 {DEADLINE_TEXT}。可在 Windows 启动应用中禁用，或退出、卸载本程序。")
    if enabled and (state.expired or not before_deadline()):
        raise ValueError("本版备考自启期限已结束，不再开启登录自启。")
    if enabled and state.system_disabled:
        raise ValueError("登录自启已被 Windows 禁用，程序不会覆盖该选择；如需恢复，请在 Windows 启动应用中自行开启。")
    _write_autostart(enabled)


def expire_autostart(now=None):
    """只移除自己的登记，不恢复被禁用/移除的项；到期标记不可因时钟回调而复活。"""
    if not is_installed():
        return False
    manifest = _read_install_record()
    policy = manifest.get("autostart_policy")
    if not policy or policy["expired"] or before_deadline(now):
        return False
    _write_autostart(False)
    manifest["autostart_policy"] = {**policy, "expired": True}
    _write_manifest(manifest)
    return True


def _uninstall_snapshot():
    result = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            for name in UNINSTALL_NAMES:
                try:
                    result[name] = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        return None
    return result


def _owns_uninstall(snapshot, target):
    expected = {"InstallLocation": str(target.parent), "Publisher": "FocusGuard",
                "UninstallString": launch_command(target, "--uninstall")}
    return snapshot is not None and all(snapshot.get(name) == (value, winreg.REG_SZ)
                                        for name, value in expected.items())


def _restore_uninstall(snapshot):
    was_missing = snapshot is None
    snapshot = snapshot or {}
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name in UNINSTALL_NAMES:
            if name in snapshot:
                value, kind = snapshot[name]
                winreg.SetValueEx(key, name, 0, kind, value)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        empty = winreg.QueryInfoKey(key)[:2] == (0, 0)
    if was_missing and empty:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)


def _register_uninstall(target):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        values = {"DisplayName": "自习室 · 游戏自律", "DisplayVersion": VERSION,
                  "Publisher": "FocusGuard", "InstallLocation": str(target.parent),
                  "DisplayIcon": str(target), "UninstallString": launch_command(target, "--uninstall")}
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def install(now=None):
    if not getattr(sys, "frozen", False):
        raise ValueError("源码模式请先打包，再使用安装功能。")
    _check_install_paths()
    target = exe_path()
    previous = read_manifest() if target.exists() else None
    old_policy = previous.get("autostart_policy") if previous else None
    policy = dict(old_policy) if old_policy else new_policy(now)
    elapsed = not before_deadline(now)
    policy["expired"] = policy["expired"] or elapsed
    # 同一策略的升级不重新登记：尊重用户在 Windows 中禁用或移除启动项。
    enroll = not old_policy and not elapsed and not _system_startup_disabled()
    old_run = _run_value()
    expected = launch_command(target, "--autostart")
    if enroll and old_run not in (None, expected):
        raise ValueError("同名启动项指向其他命令，已停止安装，未覆盖该项。")
    snapshot = _uninstall_snapshot()
    if snapshot is not None and not _owns_uninstall(snapshot, target):
        raise ValueError("同名卸载记录不属于本程序，已停止安装，未覆盖该记录。")
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = target.parent / "install.json"
    original_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    changed_exe = changed_manifest = registry_attempted = run_attempted = False
    with tempfile.TemporaryDirectory(prefix=".install-", dir=target.parent) as temp:
        backup = Path(temp) / "previous.exe"
        staged = Path(temp) / "new.exe"
        if previous:
            shutil.copy2(target, backup)
        shutil.copy2(sys.executable, staged)
        manifest = {"id": APP_ID, "exe": target.name, "version": VERSION,
                    "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
                    "autostart_policy": policy}
        try:
            if Path(sys.executable).resolve() != target.resolve():
                os.replace(staged, target)
                changed_exe = True
            _write_manifest(manifest)
            changed_manifest = True
            registry_attempted = True
            _register_uninstall(target)
            if enroll or elapsed:
                run_attempted = True
                _write_autostart(enroll)
        except Exception as exc:
            errors = []
            def restore(action):
                try:
                    action()
                except Exception as failure:
                    errors.append(str(failure))
            if run_attempted and old_run in (None, expected):
                restore(lambda: _write_autostart(old_run == expected))
            if registry_attempted:
                restore(lambda: _restore_uninstall(snapshot))
            if changed_manifest:
                restore(lambda: manifest_path.write_bytes(original_manifest) if original_manifest is not None else manifest_path.unlink())
            if changed_exe:
                restore(lambda: os.replace(backup, target) if previous else target.unlink())
            if errors:
                raise RuntimeError(f"安装未完成：{exc}；部分回滚失败，请检查本程序安装目录：{'；'.join(errors)}") from exc
            raise
    return target


def uninstall_script(folder, process_id, remove_data):
    """仅删除固定名单；绝不递归删除路径、用户游戏或下载目录。"""
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    data = folder.parent / "Data"
    files = [folder / "FocusGuard.exe", folder / "install.json"]
    if remove_data:
        files.extend([data / "config.json", data / "events.log"])
    lines = ["$ErrorActionPreference = 'Stop'", f"Wait-Process -Id {int(process_id)} -ErrorAction SilentlyContinue",
             "Start-Sleep -Milliseconds 700"]
    for file in files:
        lines.extend([
            f"$target = {literal(file)}",
            "for ($attempt = 0; $attempt -lt 40; $attempt++) {",
            "  if (-not (Test-Path -LiteralPath $target)) { break }",
            "  try { Remove-Item -LiteralPath $target -Force; break }",
            "  catch { if ($attempt -eq 39) { throw }; Start-Sleep -Milliseconds 250 }",
            "}",
        ])
    for directory in ([data, folder, folder.parent] if remove_data else [folder]):
        lines.append(f"if ((Test-Path -LiteralPath {literal(directory)}) -and @(Get-ChildItem -LiteralPath {literal(directory)} -Force).Count -eq 0) {{ Remove-Item -LiteralPath {literal(directory)} }}")
    lines.append("Remove-Item -LiteralPath $PSCommandPath -Force")
    return "\n".join(lines)


def schedule_uninstall(remove_data=False):
    read_manifest()
    _write_autostart(False)  # 卸载始终可移除自启，不受备考期限限制。
    if _owns_uninstall(_uninstall_snapshot(), exe_path()):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except FileNotFoundError:
            pass
    script = Path(tempfile.gettempdir()) / f"FocusGuard-uninstall-{uuid.uuid4().hex}.ps1"
    script.write_text(uninstall_script(exe_path().parent, os.getpid(), remove_data), encoding="utf-8-sig")
    shell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.Popen([str(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


def hide_console():
    # 程序由 windowed 可执行文件启动，此处保留为扩展点。
    pass
