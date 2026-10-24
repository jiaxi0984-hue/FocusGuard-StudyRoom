"""仅在工作区独立样本目录验证清理脚本，不修改真实安装和注册表。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from focusguard.windows.installation import uninstall_script

root = Path(__file__).resolve().parents[2] / "build" / "uninstall-fixtures"
root.mkdir(parents=True, exist_ok=True)
results = []
for remove_data in (False, True):
    folder = Path(tempfile.mkdtemp(dir=root))
    install = folder / "App"
    install.mkdir()
    data = folder / "Data"
    data.mkdir()
    for file in [install / "FocusGuard.exe", install / "install.json", data / "config.json", data / "events.log", install / "keep-user-file.txt"]:
        file.write_text("fixture", encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    script = folder / "uninstall-test.ps1"
    script.write_text(uninstall_script(install, child.pid, remove_data), encoding="utf-8-sig")
    completed = subprocess.run(["C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)], capture_output=True, timeout=20)
    child.wait(timeout=5)
    passed = (completed.returncode == 0 and not (install / "FocusGuard.exe").exists()
              and not (install / "install.json").exists() and (install / "keep-user-file.txt").exists()
              and (data / "config.json").exists() == (not remove_data))
    results.append({"remove_data": remove_data, "passed": passed, "returncode": completed.returncode})
    if not passed:
        print(completed.stderr.decode(errors="replace"))
(root / "result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results))
raise SystemExit(not all(r["passed"] for r in results))
