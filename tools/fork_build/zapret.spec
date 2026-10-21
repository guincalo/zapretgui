# PyInstaller spec for the guincalo/zapretgui fork.
# Produces dist/Zapret/ (Zapret.exe + runtime side by side, contents_directory='.'),
# which build.py moves into <install root>/_internal as the app requires.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src"
ICON = os.environ.get("ZAPRET_ICON") or None

# The app imports many modules lazily by name (feature facades, pages), so
# every module under src/ is declared as a hidden import.
hidden = []
for py in SRC.rglob("*.py"):
    rel = py.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if "__pycache__" in parts or parts[-1] == "main" and len(parts) == 1:
        continue
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if parts:
        hidden.append(".".join(parts))

datas = []
for f in SRC.rglob("*"):
    if f.is_file() and f.suffix not in (".py", ".pyc") and "__pycache__" not in f.parts:
        datas.append((str(f), str(f.parent.relative_to(SRC))))

for pkg in ("qfluentwidgets", "qframelesswindow", "qtawesome", "certifi", "curl_cffi"):
    try:
        datas += collect_data_files(pkg)
        hidden += collect_submodules(pkg)
    except Exception as exc:  # noqa: BLE001
        print(f"collect {pkg}: {exc}")

a = Analysis(
    [str(SRC / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(set(hidden)),
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "PyInstaller", "nuitka", "paramiko", "pyrogram", "telethon", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Zapret",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=True,
    icon=ICON,
    contents_directory=".",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Zapret")
