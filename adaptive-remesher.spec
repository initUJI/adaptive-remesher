# -*- mode: python ; coding: utf-8 -*-
#
# Generates a standalone desktop executable (see BUILD.md). It was
# regenerated with `pyinstaller --windowed --name adaptive-remesher app.py`
# and hasn't needed manual hidden-imports: PyInstaller detects PySide6/
# pyvista/VTK/PyMeshLab on its own via their bundled hooks. `datas` bundles
# the app icon (assets/icon.png) so `app.py`'s runtime `setWindowIcon` call
# still finds it inside the frozen bundle (via `sys._MEIPASS`).
# Rebuild with `pyinstaller adaptive-remesher.spec` after any change.

import sys

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/icon.png', 'assets')],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='adaptive-remesher',
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
    # PyInstaller only embeds an executable icon on Windows (.ico); on
    # macOS/Linux the OS icon comes from the .app bundle / desktop entry
    # instead, which this project doesn't package, so the on-disk exe/binary
    # has no icon there — the runtime `setWindowIcon` call still applies.
    icon='assets/icon.ico' if sys.platform == 'win32' else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='adaptive-remesher',
)
