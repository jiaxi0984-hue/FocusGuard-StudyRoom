"""在模拟动作下，识别真实启动的无害测试进程；不会调用系统关机。"""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_deps"))
sys.path.insert(0, str(ROOT / "tests"))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from focusguard import VERSION
from focusguard.recognition.discovery import build_index
from helpers import make_steam
from focusguard.ui.app import Window, STYLE

work = ROOT / "build" / f"live-simulation-v{VERSION}".replace(".", "-")
work.mkdir(parents=True, exist_ok=True)
if (work / "result.json").exists():
    raise SystemExit("已有模拟结果，请先归档该独立样本目录后再运行。")
fixture = make_steam(work)
test_game = fixture["exe"]
shutil.copy2("C:/Windows/System32/ping.exe", test_game)
app = QApplication([])
app.setStyleSheet(STYLE)
app.setQuitOnLastWindowClosed(False)
window = Window(work / "data", simulate=True, index_loader=lambda cancel: build_index(cancel, steam_locations=[fixture["steam"]], records=[]))
window.monitor.arm(consent=True)
window.show()
started = time.perf_counter()
proc = subprocess.Popen([str(test_game), "127.0.0.1", "-t"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW)
result = {"version": VERSION, "simulation": True, "shutdown_called": False, "installation_evidence": "synthetic Steam fixtures", "manual_game_list_used": False}

def complete():
    if not window.monitor.fired or "模拟成功" not in window.logs.toPlainText():
        return
    result.update(detected=True, elapsed_seconds=round(time.perf_counter() - started, 3),
                  paused_after_trigger=not window.settings.armed,
                  log_has_simulation="模拟成功" in window.logs.toPlainText())
    (work / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    window.finish()

poll = QTimer()
poll.timeout.connect(complete)
poll.start(100)
QTimer.singleShot(8000, window.finish)
try:
    app.exec()
finally:
    proc.terminate()
    proc.wait(timeout=5)
    (work / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
if not result.get("detected") or not result.get("paused_after_trigger") or not result.get("log_has_simulation"):
    raise SystemExit("真实测试进程模拟识别未通过")
print(json.dumps(result, ensure_ascii=False))
