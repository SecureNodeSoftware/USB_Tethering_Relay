# -*- mode: python ; coding: utf-8 -*-
# USBRelay.spec - PyInstaller spec for Windows build

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources/gnirehtet.exe', '.'),
        ('resources/adb.exe', '.'),
        ('resources/AdbWinApi.dll', '.'),
        ('resources/AdbWinUsbApi.dll', '.'),
        ('resources/scan_logo.png', '.'),
        ('resources/scan_icon.ico', '.'),
        ('resources/gnirehtet.apk', '.'),
    ],
    hiddenimports=['gui', 'relay_manager', 'adb_monitor', 'wmdc_monitor'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='USBRelay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/scan_icon.ico',
)
