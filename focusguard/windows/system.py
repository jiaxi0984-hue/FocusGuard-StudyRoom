"""只对当前用户的进程和本地计算机操作。"""
import os
from pathlib import Path
import subprocess
import ctypes
from ctypes import wintypes
import psutil
from focusguard.monitoring.core import ProcessRecord, canonical


def current_owner():
    return psutil.Process().username()


def processes():
    result = []
    for proc in psutil.process_iter(["pid", "exe", "username", "create_time"]):
        try:
            data = proc.info
            if data.get("exe") and data.get("username"):
                result.append(ProcessRecord(data["pid"], data["exe"], data["username"], data.get("create_time") or 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return result


def same_process(record):
    """识别耗时后再次检查，避免已退出进程和 PID 复用导致过期触发。"""
    try:
        proc = psutil.Process(record.pid)
        return (proc.is_running() and proc.create_time() == record.created
                and canonical(proc.exe()) == canonical(record.path)
                and proc.username().casefold() == record.owner.casefold())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def has_visible_window(pid):
    """只读检查游戏专用客户端窗口；后台守护进程不算已打开大厅。"""
    if pid <= 0:
        return False
    user32 = ctypes.WinDLL(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/user32.dll"), use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    found = False
    def visit(hwnd, unused):
        nonlocal found
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found = True
            return False
        return True
    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return found


def shutdown_command():
    if os.name != "nt":
        raise RuntimeError("仅支持 Windows。")
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return [str(root / "System32" / "shutdown.exe"), "/s", "/f", "/t", "0"]


class ShutdownAction:
    def __init__(self, simulate=False, report=print):
        self.simulate, self.report = simulate, report

    def __call__(self, game):
        if self.simulate:
            self.report(f"模拟成功：已识别 {game.name}；未发送任何关机命令。")
            return
        # 只有真实监测状态机通过明确开启、路径匹配和落盘后才调用这里。
        subprocess.run(shutdown_command(), shell=False, check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=8)
