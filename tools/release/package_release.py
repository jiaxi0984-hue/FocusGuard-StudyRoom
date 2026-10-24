"""按明确清单打包；缺失截图、无效日期或已有发布输出时拒绝发布。"""
from datetime import date
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import struct
import tempfile
import zipfile
import zlib
from focusguard import VERSION

DOCS = ("README.md", "docs/DESIGN.md", "docs/SUPPORTED_GAMES.md", "docs/AUTOSTART.md", "examples/config.example.json")
SOURCE_FILES = ("main.py", "requirements.txt", "requirements-dev.txt", "run_tests.py", ".gitignore", ".gitattributes")
SOURCE_DIRECTORIES = ("focusguard", "tests", "tools")
SCREENSHOTS = ("01-home.png", "02-recognition.png", "03-settings.png", "04-logs.png")
EVIDENCE_FILES = ("automated-tests.log", "live-simulation.json", "window-check.json", "exe-startup.json")
SCRIPTS = {
    "模拟模式.cmd": '@echo off\r\nstart "" "%~dp0FocusGuard.exe" --simulate\r\n',
    "安装到本机.cmd": '@echo off\r\nstart "" "%~dp0FocusGuard.exe" --install\r\n',
    "卸载.cmd": '@echo off\r\nif exist "%LOCALAPPDATA%\\FocusGuard\\App\\FocusGuard.exe" (\r\n  start "" "%LOCALAPPDATA%\\FocusGuard\\App\\FocusGuard.exe" --uninstall\r\n) else (\r\n  start "" "%~dp0FocusGuard.exe" --uninstall\r\n)\r\n',
}


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def required_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"缺失、空文件或符号链接不可发布：{path}")
    return path


def validate_screenshot(path):
    required_file(path)
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是 PNG 截图：{path}")
    offset, chunks = 8, []
    while offset + 12 <= len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + size
        if end > len(data):
            raise ValueError(f"截图已截断：{path}")
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + size]
        crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + body) & 0xffffffff != crc:
            raise ValueError(f"截图校验失败：{path}")
        if not chunks and (kind != b"IHDR" or size != 13 or min(struct.unpack(">II", body[:8])) < 100):
            raise ValueError(f"截图头或尺寸无效：{path}")
        chunks.append(kind)
        offset = end
        if kind == b"IEND":
            if size or offset != len(data):
                raise ValueError(f"截图尾部无效：{path}")
            break
    if not chunks or chunks[-1] != b"IEND" or b"IDAT" not in chunks:
        raise ValueError(f"截图数据不完整：{path}")


def build_package(root, release_date=None):
    root = Path(root).resolve()
    stamp = release_date if release_date is not None else date.today().strftime("%Y%m%d")
    # 严格校验，避免日期字段成为任意路径，也允许明确指定版本日期。
    if len(stamp) != 8 or not stamp.isascii() or not stamp.isdigit():
        raise ValueError("发布日期必须为 YYYYMMDD。")
    date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
    name = f"FocusGuard-{VERSION}-Windows-x64"
    out = root / "outputs"
    release = out / name
    archive = out / f"{name}-{stamp}.zip"
    checksum_path = out / (archive.name + ".sha256")
    for target in (release, archive, checksum_path):
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"拒绝复用已有发布输出，请先归档：{target}")

    # 每一个入包文件均有明确来源；不会遍历 build、_deps、配置目录或历史输出。
    entries = {"FocusGuard.exe": required_file(root / "dist" / "FocusGuard.exe")}
    for doc in DOCS:
        entries[doc] = required_file(root / doc)
        entries[f"源码/{doc}"] = root / doc
    for filename in SOURCE_FILES:
        entries[f"源码/{filename}"] = required_file(root / filename)
    for directory in SOURCE_DIRECTORIES:
        files = sorted(file for file in (root / directory).rglob("*.py")
                       if "__pycache__" not in file.relative_to(root).parts)
        if not files:
            raise ValueError(f"缺失源码目录：{directory}")
        for file in files:
            entries[f"源码/{file.relative_to(root).as_posix()}"] = required_file(file)
    entries["源码/assets/app.ico"] = required_file(root / "assets" / "app.ico")
    for image in SCREENSHOTS:
        path = root / f"screenshots-{VERSION}" / image
        validate_screenshot(path)
        entries[f"界面预览/{image}"] = path
    preview = root / f"screenshots-{VERSION}" / "index.html"
    if preview.exists():
        entries["界面预览/index.html"] = required_file(preview)

    evidence = root / f"verification-{VERSION}"
    if evidence.exists():
        for filename in EVIDENCE_FILES:
            entries[f"验证资料/{filename}"] = required_file(evidence / filename)
    out.mkdir(exist_ok=True)
    scratch = os.environ.get("PI_SCRATCH_DIR") or tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="FocusGuard-package-", dir=scratch) as temp:
        stage = Path(temp) / name
        stage.mkdir()
        for relative, source in entries.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for filename, content in SCRIPTS.items():
            (stage / filename).write_bytes(content.encode("ascii"))
        manifest = {file.relative_to(stage).as_posix(): sha256(file)
                    for file in sorted(stage.rglob("*")) if file.is_file()}
        metadata = {"version": VERSION, "release_date": stamp, "files": manifest}
        (stage / "SHA256SUMS.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staged_zip = Path(temp) / archive.name
        with zipfile.ZipFile(staged_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
            for file in sorted(stage.rglob("*")):
                if file.is_file():
                    handle.write(file, Path(name) / file.relative_to(stage))
        checksum = sha256(staged_zip)
        # 创建新目录而非合并，旧文件不会悄悄进入交付物。
        shutil.copytree(stage, release)
        shutil.copy2(staged_zip, archive)
        checksum_path.write_text(checksum + "  " + archive.name + "\n", encoding="ascii")
    return {"zip": str(archive), "release": str(release), "release_date": stamp,
            "size_bytes": archive.stat().st_size, "sha256": checksum,
            "exe_sha256": manifest["FocusGuard.exe"], "file_count": len(manifest) + 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-date", help="发布日期 YYYYMMDD；默认当前本机日期")
    args = parser.parse_args()
    print(json.dumps(build_package(Path(__file__).resolve().parents[2], args.release_date), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
