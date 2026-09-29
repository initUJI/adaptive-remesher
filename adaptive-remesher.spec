# -*- mode: python ; coding: utf-8 -*-
#
# Generates a standalone desktop executable (see BUILD.md). It was
# regenerated with `pyinstaller --windowed --name adaptive-remesher app.py`
# and hasn't needed manual hidden-imports or datas: PyInstaller detects
# PySide6/pyvista/VTK/PyMeshLab on its own via their bundled hooks.
# Rebuild with `pyinstaller adaptive-remesher.spec` after any change.

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
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
