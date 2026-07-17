# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 - QRecover Desktop"""

import os
import sys
from pathlib import Path

BASE = Path(SPECPATH)

a = Analysis(
    ['qrecover_desktop.py'],
    pathex=[str(BASE)],
    binaries=[],
    datas=[
        ('qrecover.py', '.'),
        ('ai_assistant.py', '.'),
        ('qrecover_icon.ico', '.'),
        ('qrecover_icon.png', '.'),
        ('testdisk-7.3-WIP', 'testdisk-7.3-WIP'),
        ('recuva_portable', 'recuva_portable'),
        ('Recuva_1.54.120_Machine_X64_nullsoft_en-US.exe', '.'),
    ],
    hiddenimports=[
        'flask',
        'flask.app',
        'flask.templating',
        'flask.json',
        'jinja2',
        'jinja2.ext',
        'webview',
        'webview.platforms.cef',
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
        'clr',
        'pythonnet',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PIL',
        'sqlalchemy',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='QRecoverDesktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # 无控制台窗口
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(BASE / 'qrecover_icon.ico') if (BASE / 'qrecover_icon.ico').exists() else None,
    version_info=None,
)
