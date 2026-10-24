"""仅检查本程序自己的测试窗口，不启动游戏或进入关机状态机。"""
from pathlib import Path
import json
import os
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "_deps"))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
from focusguard.windows.system import has_visible_window

app = QApplication([])
window = QWidget()
window.setWindowTitle("自习室 · 可见窗口读取测试")
window.resize(400, 120)
window.show()
result = {}

def check():
    result["visible_detected"] = has_visible_window(os.getpid())
    window.hide()
    app.processEvents()
    result["hidden_not_detected"] = not has_visible_window(os.getpid())
    output = root / "build" / "lobby-window-check.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    app.quit()

QTimer.singleShot(300, check)
app.exec()
print(json.dumps(result))
raise SystemExit(not all(result.values()) or len(result) != 2)
