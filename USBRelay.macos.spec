# -*- mode: python ; coding: utf-8 -*-
# USBRelay.macos.spec - PyInstaller spec for macOS build

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources/scan_logo.png', '.'),
        ('resources/gnirehtet.apk', '.'),
    ],
    hiddenimports=['gui', 'relay_manager', 'adb_monitor'],
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
    [],
    exclude_binaries=True,
    name='USBRelay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
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
    name='USBRelay',
)

app = BUNDLE(
    coll,
    name='USBRelay.app',
    icon=None,
    bundle_identifier='com.scan.usbrelay',
    info_plist={
        'CFBundleName': 'USB Relay Manager',
        'CFBundleDisplayName': 'USB Relay Manager',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
    },
)
