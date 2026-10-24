"""当前用户安装、自启与可核验的卸载；不需要系统服务或管理员持久化。"""
import ctypes
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

APP_ID = "FocusGuard.StudyRoom.1"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\FocusGuardStudyRoom"


def root_dir():
    return Path(os.environ["LOCALAPPDATA"]) / "FocusGuard"


def exe_path():
    return root_dir() / "App" / "FocusGuard.exe"


def is_installed():
    return bool(getattr(sys, "frozen", False)) and Path(sys.executable).resolve() == exe_path().resolve()


def launch_command(executable, *args):
    return subprocess.list2cmdline([str(executable), *args])


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value = winreg.QueryValueEx(key, "FocusGuardStudyRoom")[0]
            return value == launch_command(exe_path(), "--autostart")
    except OSError:
        return False


def set_autostart(enabled):
    if enabled and not is_installed():
        raise ValueError("请先点击“安装到本机”，再开启登录自启。")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            value = launch_command(exe_path(), "--autostart")
            if len(value) > 260:
                raise ValueError("启动路径过长，无法注册登录自启。")
            winreg.SetValueEx(key, "FocusGuardStudyRoom", 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, "FocusGuardStudyRoom")
            except FileNotFoundError:
                pass


def read_manifest():
    folder = exe_path().parent
    manifest = json.loads((folder / "install.json").read_text(encoding="utf-8"))
    if manifest.get("id") != APP_ID or manifest.get("exe") != "FocusGuard.exe":
        raise ValueError("安装标记不匹配，已停止卸载。")
    if folder.is_symlink() or folder.is_junction() or root_dir().is_symlink() or root_dir().is_junction():
        raise ValueError("安装目录是链接，已停止删除。")
    if exe_path().is_symlink() or hashlib.sha256(exe_path().read_bytes()).hexdigest() != manifest.get("sha256"):
        raise ValueError("程序文件与安装记录不一致，已停止覆盖或删除。")
    return manifest


def install():
    if not getattr(sys, "frozen", False):
        raise ValueError("源码模式请先打包，再使用安装功能。")
    target = exe_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        read_manifest()
    if Path(sys.executable).resolve() != target.resolve():
        shutil.copy2(sys.executable, target)
    manifest = {"id": APP_ID, "exe": target.name,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    (target.parent / "install.json").write_text(json.dumps(manifest), encoding="utf-8")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        values = {"DisplayName": "自习室 · 游戏自律", "DisplayVersion": VERSION,
                  "Publisher": "FocusGuard", "InstallLocation": str(target.parent),
                  "DisplayIcon": str(target), "UninstallString": launch_command(target, "--uninstall")}
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
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
    set_autostart(False)
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
