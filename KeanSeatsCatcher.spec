# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo,
)

# Keep Windows system DLLs ahead of unrelated copies when PyInstaller scans PATH.
if os.name == 'nt':
    system32 = os.path.join(os.environ['SystemRoot'], 'System32')
    os.environ['PATH'] = os.pathsep.join((system32, os.environ.get('PATH', '')))

version = (Path(SPECPATH) / 'VERSION').read_text(encoding='ascii').strip()
windows_version = (*map(int, version.split('.')), 0)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=windows_version, prodvers=windows_version,
                      mask=0x3F, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'Limitime'),
            StringStruct('FileDescription', 'KeanSeatsCatcher'),
            StringStruct('FileVersion', version),
            StringStruct('OriginalFilename', 'KeanSeatsCatcher.exe'),
            StringStruct('ProductName', 'KeanSeatsCatcher'),
            StringStruct('ProductVersion', version),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ],
)

license_metadata = copy_metadata('PyInstaller')
for package in ('PyQt6-Fluent-Widgets', 'selenium', 'requests', 'winotify'):
    license_metadata.extend(copy_metadata(package, recursive=True))

a = Analysis(
    ['ui_main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('VERSION', '.'),
        ('LICENSE', 'licenses'),
        (str(Path(sys.base_prefix) / 'LICENSE.txt'), 'licenses'),
        ('locales/en_US.json', 'locales'),
        ('locales/zh_CN.json', 'locales'),
        ('assets/logo.ico', 'assets'),
        ('assets/logo.png', 'assets'),
        ('assets/github.png', 'assets'),
        ('assets/bilibili.png', 'assets'),
    ] + license_metadata,
    hiddenimports=[
        'selenium.webdriver.edge.webdriver',
        'selenium.webdriver.edge.options',
        'selenium.webdriver.edge.service',
        'selenium.webdriver.chrome.webdriver',
        'winotify',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='KeanSeatsCatcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\logo.ico'],
    version=version_info,
)
