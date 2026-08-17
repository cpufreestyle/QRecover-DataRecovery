# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 - QRecover Desktop"""

import os
import sys
from pathlib import Path

BASE = Path(SPECPATH)

# 打包前压缩前端静态资源到 build/web_min（零依赖保守压缩，见 web_minify.py）
sys.path.insert(0, str(BASE))
import web_minify
WEB_MIN = BASE / 'build' / 'web_min'
web_minify.minify_to(str(BASE / 'web'), WEB_MIN)

a = Analysis(
    ['qrecover_desktop.py'],
    pathex=[str(BASE)],
    binaries=[],
    datas=[
        # 注意：qrecover.py / ai_assistant.py 是经 import 收集的模块，PyInstaller 会将其
        # 编译进归档并以 UTF-8 加载，不要再以原始 .py 形式打包为数据文件，否则在某些环境下
        # 可能以系统 ANSI 代码页（GBK）读取而引发中文乱码。仅保留真正的资源文件。
        # TestDisk/PhotoRec 与 Recuva 不再随包分发，改为运行时按需联网下载
        # （见 qrecover.py ensure_testdisk / recuva 无感更新），以将 EXE 体积压到 100MB 内。
        ('qrecover_icon.ico', '.'),
        ('qrecover_icon.png', '.'),
        # 前端静态资源（打包用压缩版；qrecover.py 在 frozen 模式下从 sys._MEIPASS/web 读取）
        (str(WEB_MIN), 'web'),
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
        'scipy',
        'pydoc',
        'pydoc_data',
        'lib2to3',
        'doctest',
        'unittest',
        'pyaudio',
        'cryptography',
        'OpenSSL',
    ],
    noarchive=False,
    optimize=0,
)

version_info = {
    'version': '2.0.6',
    'company_name': 'QRecover',
    'file_description': 'QRecover Desktop - Data Recovery Toolkit',
    'internal_name': 'QRecoverDesktop',
    'legal_copyright': 'GPL v2+',
    'original_filename': 'QRecoverDesktop.exe',
    'product_name': 'QRecover Desktop',
}

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
    version_info=version_info,
)
