# -*- mode: python ; coding: utf-8 -*-

import os

# Keep Windows system DLLs ahead of unrelated copies when PyInstaller scans PATH.
if os.name == 'nt':
    system32 = os.path.join(os.environ['SystemRoot'], 'System32')
    os.environ['PATH'] = os.pathsep.join((system32, os.environ.get('PATH', '')))

a = Analysis(
    ['ui_main.py'],
    pathex=[],
    binaries=[],
    datas=[('locales', 'locales'), ('assets', 'assets')],
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
)
