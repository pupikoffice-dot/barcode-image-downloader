# -*- mode: python ; coding: utf-8 -*-
import os
import sys

import customtkinter
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

sys.path.insert(0, SPECPATH)
from updater import APP_VERSION  # noqa: E402

_ctk_path = os.path.dirname(customtkinter.__file__)

# Windows version resource. Unsigned PyInstaller exes with no publisher/product
# metadata are scored as far more suspicious by Defender and other AV engines.
_ver = tuple(int(p) for p in APP_VERSION.split(".")) + (0,) * (4 - len(APP_VERSION.split(".")))
_version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_ver, prodvers=_ver, mask=0x3F, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Pupik"),
            StringStruct("FileDescription", "Barcode Image Downloader"),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("InternalName", "BarcodeImageDownloader"),
            StringStruct("LegalCopyright", "Pupik. Internal use."),
            StringStruct("OriginalFilename", "Barcode Image Downloader.exe"),
            StringStruct("ProductName", "Barcode Image Downloader"),
            StringStruct("ProductVersion", APP_VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        (_ctk_path, 'customtkinter'),
    ],
    hiddenimports=[
        'PIL._tkinter_finder',
        'PIL._imagingtk',
        'customtkinter',
        'requests',
        'urllib3',
        'updater',
        'ui_helpers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Test/dev tooling never belongs in the shipped exe.
    excludes=['pytest', '_pytest', 'unittest', 'pydoc', 'doctest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Barcode Image Downloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX-packed executables are a classic antivirus false-positive trigger.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version_info,
    # Never ask for admin rights.
    uac_admin=False,
)
