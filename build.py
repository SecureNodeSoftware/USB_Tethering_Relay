#!/usr/bin/env python3
"""
USB Relay Manager - Build Script

Automates the PyInstaller build process to create the final executable.
Supports both Windows (.exe) and macOS (.app) builds.

Usage:
  python build.py              # Auto-detect platform
  python build.py --windows    # Force Windows build
  python build.py --macos      # Force macOS build
  python build.py --mode android|winmobile|both  # Select device modes

If the gnirehtet binary is missing from resources/, the build will
automatically compile it from the vendored Rust source in
vendor/gnirehtet-relay-rust/ (requires the Rust toolchain: https://rustup.rs/).

Based on gnirehtet by Genymobile (https://github.com/Genymobile/gnirehtet)
Licensed under Apache 2.0
"""

import io
import os
import sys
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'


def build_gnirehtet_from_source(project_dir: Path, platform: str) -> bool:
    """Compile gnirehtet relay from vendored Rust source as a fallback."""
    vendor_dir = project_dir / 'vendor' / 'gnirehtet-relay-rust'
    resources_dir = project_dir / 'resources'

    if not (vendor_dir / 'Cargo.toml').exists():
        print("  Vendored source not found at vendor/gnirehtet-relay-rust/")
        return False

    # Check for cargo (Rust toolchain)
    cargo_bin = shutil.which('cargo')
    if not cargo_bin:
        print("  Rust toolchain not found. Install from https://rustup.rs/")
        return False

    print(f"  Found cargo: {cargo_bin}")
    print("  Compiling gnirehtet from vendored source (this may take a minute)...")

    # Determine build command and output path
    cargo_cmd = [cargo_bin, 'build', '--release']
    if platform == 'windows':
        binary_name = 'gnirehtet.exe'
        # Cross-compile for Windows if not on Windows
        if not IS_WINDOWS:
            target = 'x86_64-pc-windows-gnu'
            cargo_cmd += ['--target', target]
            output_binary = vendor_dir / 'target' / target / 'release' / binary_name
        else:
            output_binary = vendor_dir / 'target' / 'release' / binary_name
    else:
        binary_name = 'gnirehtet'
        output_binary = vendor_dir / 'target' / 'release' / binary_name

    try:
        result = subprocess.run(
            cargo_cmd,
            cwd=str(vendor_dir),
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode != 0:
            print(f"  Cargo build failed (exit code {result.returncode}):")
            # Show last few lines of stderr for diagnostics
            for line in result.stderr.strip().splitlines()[-10:]:
                print(f"    {line}")
            return False
    except FileNotFoundError:
        print("  Failed to execute cargo.")
        return False
    except subprocess.TimeoutExpired:
        print("  Cargo build timed out after 5 minutes.")
        return False

    if not output_binary.exists():
        print(f"  Expected binary not found at: {output_binary}")
        return False

    # Copy compiled binary into resources/
    resources_dir.mkdir(exist_ok=True)
    dest = resources_dir / binary_name
    shutil.copy2(str(output_binary), str(dest))
    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"  Built successfully: {dest} ({size_mb:.1f} MB)")
    return True


PLATFORM_TOOLS_URL = 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'

# Files that must come from the same platform-tools release
ADB_FILES = ['adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll']


def download_platform_tools(resources_dir: Path) -> bool:
    """Download official Android SDK Platform Tools and extract ADB files.

    All three files (adb.exe, AdbWinApi.dll, AdbWinUsbApi.dll) are pulled
    from the same zip so they are guaranteed version-matched.
    """
    print(f"  Downloading Android SDK Platform Tools...")
    print(f"  URL: {PLATFORM_TOOLS_URL}")

    try:
        resp = urlopen(PLATFORM_TOOLS_URL, timeout=60)
        data = resp.read()
    except (URLError, OSError) as e:
        print(f"  Download failed: {e}")
        return False

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            extracted = 0
            for adb_file in ADB_FILES:
                zip_path = f'platform-tools/{adb_file}'
                try:
                    info = zf.getinfo(zip_path)
                except KeyError:
                    print(f"  {adb_file} not found in zip archive")
                    continue

                dest = resources_dir / adb_file
                with zf.open(info) as src, open(dest, 'wb') as dst:
                    dst.write(src.read())
                size_kb = dest.stat().st_size / 1024
                print(f"  Extracted {adb_file} ({size_kb:.0f} KB)")
                extracted += 1

            if extracted == len(ADB_FILES):
                print("  All ADB files downloaded successfully (version-matched)")
                return True
            else:
                print(f"  Only extracted {extracted}/{len(ADB_FILES)} files")
                return False

    except zipfile.BadZipFile:
        print("  Downloaded file is not a valid zip archive")
        return False


def validate_adb_version_match(resources_dir: Path) -> bool:
    """Check that adb.exe and its companion DLLs are from the same release.

    Compares file modification times — files extracted from the same
    platform-tools zip will have timestamps within seconds of each other.
    A large gap suggests they were sourced separately and may be mismatched.
    """
    paths = [resources_dir / f for f in ADB_FILES]

    # Can't validate if any file is missing
    if not all(p.exists() for p in paths):
        return True

    try:
        mtimes = [p.stat().st_mtime for p in paths]
    except OSError:
        return True

    # If modification times differ by more than 24 hours, they likely
    # came from different downloads/releases
    max_gap = max(mtimes) - min(mtimes)
    if max_gap > 86400:  # 24 hours in seconds
        print("  WARNING: ADB files appear to be from different releases!")
        print(f"  Modification time gap: {max_gap / 3600:.0f} hours")
        print("  adb.exe, AdbWinApi.dll, and AdbWinUsbApi.dll must all")
        print("  come from the same platform-tools download.")
        return False

    return True


VALID_MODES = ('android', 'winmobile', 'both')


def detect_mode(args: list) -> str:
    """Parse --mode flag from command line arguments."""
    for i, arg in enumerate(args):
        if arg == '--mode' and i + 1 < len(args):
            return args[i + 1]
    return 'both'


def write_build_config(project_dir: Path, mode: str):
    """Write src/build_config.py with the selected enabled modes."""
    if mode == 'both':
        modes = ['android', 'winmobile']
    else:
        modes = [mode]

    config_path = project_dir / 'src' / 'build_config.py'
    config_path.write_text(
        '"""\nUSB Relay Manager - Build Configuration\n\n'
        'This module is overwritten by build.py at build time to reflect the\n'
        'selected --mode (android, winmobile, or both).  The defaults here are\n'
        'used when running from source during development.\n"""\n\n'
        f'ENABLED_MODES = {modes!r}\n'
    )
    print(f"  Wrote build_config.py: ENABLED_MODES = {modes!r}")


def generate_spec(project_dir: Path, platform: str, mode: str) -> Path:
    """Generate a PyInstaller spec file tailored to the selected mode.

    Returns the path to the generated spec file.
    """
    android = mode in ('android', 'both')
    winmobile = mode in ('winmobile', 'both')

    # --- Binaries (Windows only) ---
    binaries = []
    if platform == 'windows' and android:
        binaries.extend([
            "('resources/gnirehtet.exe', '.')",
            "('resources/adb.exe', '.')",
            "('resources/AdbWinApi.dll', '.')",
            "('resources/AdbWinUsbApi.dll', '.')",
        ])

    # --- Data files ---
    datas = [
        "('resources/scan_logo.png', '.')",
    ]
    if platform == 'windows':
        datas.append("('resources/scan_icon.ico', '.')")
    if android:
        datas.append("('resources/gnirehtet.apk', '.')")

    # --- Hidden imports ---
    hiddenimports = ['gui', 'build_config']
    if android:
        hiddenimports.extend(['relay_manager', 'adb_monitor'])
    if winmobile:
        hiddenimports.extend(['wmdc_monitor', 'dhcp_server'])

    binaries_str = ',\n        '.join(binaries)
    datas_str = ',\n        '.join(datas)
    hiddenimports_str = repr(hiddenimports)

    # --- UPX exclusions (only for bundled android binaries) ---
    upx_exclude = []
    if platform == 'windows' and android:
        upx_exclude = ['adb.exe', 'gnirehtet.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll']

    if platform == 'windows':
        spec_content = f"""\
# -*- mode: python ; coding: utf-8 -*-
# Auto-generated by build.py (mode={mode})

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[
        {binaries_str}
    ],
    datas=[
        {datas_str}
    ],
    hiddenimports={hiddenimports_str},
    hookspath=[],
    hooksconfig={{}},
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
    upx_exclude={upx_exclude!r},
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/scan_icon.ico',
)
"""
    else:
        # macOS spec
        spec_content = f"""\
# -*- mode: python ; coding: utf-8 -*-
# Auto-generated by build.py (mode={mode})

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        {datas_str}
    ],
    hiddenimports={hiddenimports_str},
    hookspath=[],
    hooksconfig={{}},
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
    info_plist={{
        'CFBundleName': 'USB Relay Manager',
        'CFBundleDisplayName': 'USB Relay Manager',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
    }},
)
"""

    # Write to a temp file inside the project so PyInstaller resolves
    # relative resource paths correctly.
    spec_path = project_dir / f'USBRelay.generated.spec'
    spec_path.write_text(spec_content)
    return spec_path


def check_resources(project_dir: Path, platform: str, mode: str = 'both') -> bool:
    """Verify all required resources are present for the target platform.

    Only checks resources that are actually needed for the selected mode.
    If the gnirehtet relay binary is missing, attempts to compile it from
    the vendored Rust source before giving up.
    """
    resources_dir = project_dir / 'resources'
    android = mode in ('android', 'both')

    # Always need logo
    required_files = ['scan_logo.png']

    if platform == 'windows':
        required_files.append('scan_icon.ico')
        if android:
            gnirehtet_binary = 'gnirehtet.exe'
            required_files.extend([
                gnirehtet_binary,
                'adb.exe',
                'AdbWinApi.dll',
                'AdbWinUsbApi.dll',
                'gnirehtet.apk',
            ])
    else:
        if android:
            gnirehtet_binary = 'gnirehtet'
            required_files.extend([
                gnirehtet_binary,
                'adb',
                'gnirehtet.apk',
            ])

    # Build gnirehtet from source if missing
    if android:
        gnirehtet_binary = 'gnirehtet.exe' if platform == 'windows' else 'gnirehtet'
        if not (resources_dir / gnirehtet_binary).exists():
            print(f"  {gnirehtet_binary} not found in resources/, building from source...")
            if not build_gnirehtet_from_source(project_dir, platform):
                print(f"  Could not build {gnirehtet_binary} from source.")

    # On Windows, auto-download ADB files if missing or version-mismatched
    if platform == 'windows' and android:
        adb_missing = any(
            not (resources_dir / f).exists() for f in ADB_FILES
        )
        adb_mismatched = not validate_adb_version_match(resources_dir)

        if adb_missing or adb_mismatched:
            reason = "missing" if adb_missing else "version-mismatched"
            print(f"  ADB files are {reason}, downloading from official release...")
            if not download_platform_tools(resources_dir):
                print("  Failed to download platform-tools.")
                print("  Manually download from: https://developer.android.com/tools/releases/platform-tools")
                if adb_mismatched:
                    print("  IMPORTANT: adb.exe, AdbWinApi.dll, and AdbWinUsbApi.dll")
                    print("  must ALL come from the same platform-tools release.")

    missing = []
    for filename in required_files:
        if not (resources_dir / filename).exists():
            missing.append(filename)

    if missing:
        print(f"ERROR: Missing required resources for {platform} (mode={mode}):")
        for f in missing:
            print(f"  - {f}")
        print(f"\nPlease ensure these files are in: {resources_dir}")

        if android:
            gnirehtet_binary = 'gnirehtet.exe' if platform == 'windows' else 'gnirehtet'
            if gnirehtet_binary in missing:
                print(f"\n  To build gnirehtet from source, install Rust (https://rustup.rs/)")
                print(f"  and re-run this build script.")
            if platform == 'macos' and 'adb' in missing:
                print("\n  adb: Download Android SDK Platform Tools for macOS from")
                print("       https://developer.android.com/tools/releases/platform-tools")
        return False

    # Final version validation (after any downloads)
    if platform == 'windows' and android and not validate_adb_version_match(resources_dir):
        print("ERROR: ADB version mismatch persists after download attempt.")
        print("Manually replace adb.exe, AdbWinApi.dll, and AdbWinUsbApi.dll")
        print("with files from the same platform-tools release.")
        return False

    return True


def clean_build(project_dir: Path):
    """Clean previous build artifacts."""
    dirs_to_clean = ['build', 'dist', '__pycache__']

    for dir_name in dirs_to_clean:
        dir_path = project_dir / dir_name
        if dir_path.exists():
            print(f"Cleaning {dir_path}...")
            try:
                shutil.rmtree(dir_path)
            except PermissionError as e:
                print(f"WARNING: Could not delete {dir_path}")
                print(f"  {e}")
                if IS_WINDOWS:
                    print("  Close any running USBRelay.exe and try again.")
                    print("  Or run: taskkill /f /im USBRelay.exe")
                else:
                    print("  Close any running USBRelay and try again.")
                    print("  Or run: pkill -f USBRelay")
                return False

    # Clean pycache in src
    src_pycache = project_dir / 'src' / '__pycache__'
    if src_pycache.exists():
        try:
            shutil.rmtree(src_pycache)
        except PermissionError:
            pass  # Ignore pycache errors

    return True


def run_pyinstaller(project_dir: Path, platform: str, spec_file: Path) -> bool:
    """Run PyInstaller to build the executable using the given spec file."""
    if not spec_file.exists():
        print(f"ERROR: Spec file not found: {spec_file}")
        return False

    print(f"\nBuilding {platform} application with PyInstaller...")
    print(f"Spec file: {spec_file.name}")
    print("-" * 50)

    try:
        result = subprocess.run(
            [sys.executable, '-m', 'PyInstaller', str(spec_file), '--clean'],
            cwd=str(project_dir),
            check=True
        )
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"ERROR: PyInstaller failed with code {e.returncode}")
        return False
    except FileNotFoundError:
        print("ERROR: PyInstaller not found. Install with: pip install pyinstaller")
        return False


def verify_output(project_dir: Path, platform: str) -> bool:
    """Verify the build output exists."""
    if platform == 'macos':
        app_path = project_dir / 'dist' / 'USBRelay.app'
        if app_path.exists():
            # Calculate total .app bundle size
            total_size = sum(
                f.stat().st_size for f in app_path.rglob('*') if f.is_file()
            )
            size_mb = total_size / (1024 * 1024)
            print(f"\nBuild successful!")
            print(f"Output: {app_path}")
            print(f"Size: {size_mb:.1f} MB")

            # Create zip for distribution
            zip_path = project_dir / 'dist' / 'USBRelay.app.zip'
            print(f"\nCreating distribution archive: {zip_path}")
            shutil.make_archive(
                str(project_dir / 'dist' / 'USBRelay.app'),
                'zip',
                str(project_dir / 'dist'),
                'USBRelay.app'
            )
            if zip_path.exists():
                zip_mb = zip_path.stat().st_size / (1024 * 1024)
                print(f"Archive: {zip_path} ({zip_mb:.1f} MB)")
            return True
        else:
            print("\nERROR: Build output not found")
            return False
    else:
        exe_path = project_dir / 'dist' / 'USBRelay.exe'
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"\nBuild successful!")
            print(f"Output: {exe_path}")
            print(f"Size: {size_mb:.1f} MB")
            return True
        else:
            print("\nERROR: Build output not found")
            return False


def detect_platform(args: list) -> str:
    """Detect target platform from args or current OS."""
    if '--windows' in args:
        return 'windows'
    if '--macos' in args:
        return 'macos'
    if IS_WINDOWS:
        return 'windows'
    if IS_MACOS:
        return 'macos'
    return 'unknown'


def main():
    """Main build process."""
    # Get project directory (where this script is located)
    project_dir = Path(__file__).parent.absolute()
    platform = detect_platform(sys.argv)
    mode = detect_mode(sys.argv)

    print("=" * 50)
    print("USB Relay Manager - Build Script")
    print("=" * 50)
    print(f"\nProject directory: {project_dir}")
    print(f"Target platform:  {platform}")
    print(f"Build mode:       {mode}")

    if platform == 'unknown':
        print("\nERROR: Unsupported platform. Use --windows or --macos to specify.")
        return 1

    if mode not in VALID_MODES:
        print(f"\nERROR: Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}")
        return 1

    # Windows Mobile mode requires Windows platform
    if mode == 'winmobile' and platform != 'windows':
        print(f"\nERROR: Windows Mobile mode requires --windows platform.")
        print("Windows Mobile tethering is only available on Windows.")
        return 1

    # Cross-compilation warning
    if platform == 'windows' and not IS_WINDOWS:
        print("\nWARNING: Building Windows target on non-Windows platform.")
        print("The resulting executable may not work. Build on Windows for best results.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            print("Build cancelled.")
            return 1
    elif platform == 'macos' and not IS_MACOS:
        print("\nWARNING: Building macOS target on non-macOS platform.")
        print("The resulting app may not work. Build on macOS for best results.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            print("Build cancelled.")
            return 1

    # Step 1: Check resources
    print("\n[1/5] Checking resources...")
    if not check_resources(project_dir, platform, mode):
        return 1
    print("All resources found.")

    # Step 2: Write build config
    print("\n[2/5] Writing build configuration...")
    write_build_config(project_dir, mode)

    # Step 3: Clean previous build
    print("\n[3/5] Cleaning previous build...")
    if not clean_build(project_dir):
        return 1
    print("Clean complete.")

    # Step 4: Generate spec and run PyInstaller
    print("\n[4/5] Running PyInstaller...")
    spec_file = generate_spec(project_dir, platform, mode)
    if not run_pyinstaller(project_dir, platform, spec_file):
        return 1

    # Clean up generated spec file
    if spec_file.exists():
        spec_file.unlink()

    # Step 5: Verify output
    print("\n[5/5] Verifying output...")
    if not verify_output(project_dir, platform):
        return 1

    print("\n" + "=" * 50)
    print(f"Build completed successfully! ({platform}, mode={mode})")
    print("=" * 50)
    return 0


if __name__ == '__main__':
    sys.exit(main())
