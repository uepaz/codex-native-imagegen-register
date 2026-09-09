"""Build the standalone PowerShell launcher and release ZIP without downloads."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "codex-native-imagegen-register"
ACTIONS = ("install", "check", "status", "restore")
# Never collect arbitrary working-tree files: local credentials/logs stay out.
RELEASE_FILES = (
    "LICENSE",
    "README.md",
    "CONTRIBUTING.md",
    "docs/usage.zh-CN.md",
    "src/native_imagegen_register.py",
    "scripts/build_bundle.py",
    "scripts/templates/launcher-template.ps1",
)


def release_contents(root: Path) -> dict[str, bytes]:
    files = {name: (root / name).read_bytes() for name in RELEASE_FILES}
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=0) as stream:
        stream.write(files["src/native_imagegen_register.py"])
    payload = base64.b64encode(compressed.getvalue()).decode("ascii")
    template = files["scripts/templates/launcher-template.ps1"].decode("utf-8-sig")
    if template.count("__PAYLOAD__") != 1:
        raise ValueError("Launcher template must contain exactly one __PAYLOAD__ marker")
    launcher = template.replace("\r\n", "\n").replace("__PAYLOAD__", payload)
    files[NAME + ".ps1"] = launcher.replace("\n", "\r\n").encode("utf-8-sig")
    for action in ACTIONS:
        wrapper = f'''@echo off
setlocal
set "REPORT=%TEMP%\\codex-native-imagegen-{action}-%RANDOM%-%RANDOM%.log"
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0{NAME}.ps1" -Action {action.title()} > "%REPORT%" 2>&1
set "RC=%ERRORLEVEL%"
type "%REPORT%"
start "" notepad.exe "%REPORT%"
exit /b %RC%
'''
        files[action + ".cmd"] = wrapper.replace("\n", "\r\n").encode("ascii")
    files["SHA256SUMS.txt"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(files.items())
    ).encode("utf-8")
    return files


def build(root: Path = ROOT) -> tuple[Path, Path]:
    files = release_contents(root)
    dist = root / "dist"
    package = dist / NAME
    for name, data in sorted(files.items()):
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    standalone = dist / (NAME + ".ps1")
    standalone.write_bytes(files[NAME + ".ps1"])
    archive = dist / (NAME + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(f"{NAME}/{name}", date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            bundle.writestr(entry, data)
    (dist / "SHA256SUMS.txt").write_text(
        "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in (standalone, archive)),
        encoding="utf-8", newline="\n",
    )
    return standalone, archive


if __name__ == "__main__":
    for artifact in build():
        print(artifact)
