# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller --onedir spec file for PersonSort Windows/macOS packaging."""

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

spec_dir = Path(SPECPATH).resolve()
repo_root = spec_dir.parent
src_dir = repo_root / "PersonSort"

datas = []
binaries = []
hiddenimports = [
    "keyring.backends.Windows",
    "win32ctypes",
    "win32ctypes.pywin32",
    "dropbox",
    "onnxruntime",
    "cv2",
    "PIL",
    "sklearn",
    "tkinter",
    "tkinter.filedialog",
]

for pkg in ("easyocr", "ultralytics", "insightface"):
    tmp_datas, tmp_bins, tmp_hidden = collect_all(pkg)
    datas.extend(tmp_datas)
    binaries.extend(tmp_bins)
    hiddenimports.extend(tmp_hidden)

# Bundle .env (containing the public PKCE Dropbox App Key) if present so non-tech users
# can click Connect Dropbox immediately without entering an App Key.
dotenv_file = src_dir / ".env"
if not dotenv_file.is_file():
    dotenv_file = repo_root / ".env"
if dotenv_file.is_file():
    datas.append((str(dotenv_file), "."))

# Optional offline model pre-bundling
if os.environ.get("PERSONSORT_PREBUNDLE_MODELS") == "1":
    models_cache = src_dir / ".models_cache"
    if models_cache.is_dir():
        datas.append((str(models_cache), ".models_cache"))

block_cipher = None

a = Analysis(
    [str(src_dir / "PersonSort.py")],
    pathex=[str(src_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PersonSort",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PersonSort",
)
