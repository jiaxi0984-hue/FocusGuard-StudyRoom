"""可复现的单文件 Windows 打包；只收集实际使用的 Qt 模块。"""
import argparse
from pathlib import Path
import sys
import os
import subprocess

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dist-dir", default="dist", help="输出目录；发布新版本时指定独立目录以保留旧 EXE")
parser.add_argument("--work-dir", default="build", help="PyInstaller 中间文件目录")
args = parser.parse_args()
sys.path.insert(0, str(root / "_deps"))
os.chdir(root)
# 避免 PATH 中其他软件的同名 ICU / Qt 运行库污染发布文件。
# Qt 6.12 在当前 Windows 环境使用系统 ICU，不能混入 Poppler 等软件的 ICU。
windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
os.environ["PATH"] = os.pathsep.join(map(str, [root / "_deps" / "shiboken6", root / "_deps" / "PySide6",
                                             Path(sys.executable).parent, Path(sys.executable).parent / "DLLs",
                                             windows / "System32", windows]))
import PyInstaller.__main__
from PIL import Image, ImageDraw, ImageFont

assets = root / "assets"
assets.mkdir(exist_ok=True)
image = Image.new("RGBA", (256, 256))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((0, 0, 255, 255), 54, fill="#254B40")
font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 152)
draw.text((128, 120), "习", font=font, fill="#D9E7A4", anchor="mm")
image.save(assets / "app.ico", sizes=[(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)])

PyInstaller.__main__.run([
    "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "FocusGuard",
    "--distpath", str(root / args.dist_dir), "--workpath", str(root / args.work_dir),
    "--specpath", str(root / args.work_dir),
    "--icon", str(assets / "app.ico"), "--paths", str(root / "_deps"),
    "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "tkinter", "--exclude-module", "numpy", "--exclude-module", "PIL",
    str(root / "main.py")])
