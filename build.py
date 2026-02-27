#!/usr/bin/env python3
"""
USB Relay Manager - Build Script

Automates the PyInstaller build process to create the final executable.
Supports both Windows (.exe) and macOS (.app) builds.

Usage:
  python build.py              # Auto-detect platform
  python build.py --windows    # Force Windows build
  python build.py --macos      # Force macOS build

If the gnirehtet binary is missing from resources/, the build will
automatically compile it from the vendored Rust source in
vendor/gnirehtet-relay-rust/ (requires the Rust toolchain: https://rustup.rs/).

Based on gnirehtet by Genymobile (https://github.com/Genymobile/gnirehtet)
Licensed under Apache 2.0
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

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


def check_resources(project_dir: Path, platform: str) -> bool:
    """Verify all required resources are present for the target platform.

    If gnirehtet is missing, attempts to compile it from the vendored Rust
    source in vendor/gnirehtet-relay-rust/ before giving up.
    """
    resources_dir = project_dir / 'resources'

    if platform == 'windows':
        gnirehtet_binary = 'gnirehtet.exe'
        required_files = [
            gnirehtet_binary,
            'adb.exe',
            'AdbWinApi.dll',
            'AdbWinUsbApi.dll',
            'gnirehtet.apk',
            'scan_logo.png',
            'scan_icon.ico',
        ]
    else:
        gnirehtet_binary = 'gnirehtet'
        required_files = [
            gnirehtet_binary,
            'adb',
            'gnirehtet.apk',
            'scan_logo.png',
        ]

    # Try building gnirehtet from source if it's missing
    if not (resources_dir / gnirehtet_binary).exists():
        print(f"  {gnirehtet_binary} not found in resources/, attempting to build from source...")
        if not build_gnirehtet_from_source(project_dir, platform):
            print(f"  Could not build {gnirehtet_binary} from source.")

    missing = []
    for filename in required_files:
        if not (resources_dir / filename).exists():
            missing.append(filename)

    if missing:
        print(f"ERROR: Missing required resources for {platform}:")
        for f in missing:
            print(f"  - {f}")
        print(f"\nPlease ensure these files are in: {resources_dir}")

        if gnirehtet_binary in missing:
            print(f"\n  To build gnirehtet from source, install Rust (https://rustup.rs/)")
            print(f"  and re-run this build script.")
        if platform == 'macos' and 'adb' in missing:
            print("\n  adb: Download Android SDK Platform Tools for macOS from")
            print("       https://developer.android.com/tools/releases/platform-tools")
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


def run_pyinstaller(project_dir: Path, platform: str) -> bool:
    """Run PyInstaller to build the executable."""
    if platform == 'macos':
        spec_file = project_dir / 'USBRelay.macos.spec'
    else:
        spec_file = project_dir / 'USBRelay.spec'

    if not spec_file.exists():
        print(f"ERROR: Spec file not found: {spec_file}")
        return False

    print(f"\nBuilding {platform} application with PyInstaller...")
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

    print("=" * 50)
    print("USB Relay Manager - Build Script")
    print("=" * 50)
    print(f"\nProject directory: {project_dir}")
    print(f"Target platform:  {platform}")

    if platform == 'unknown':
        print("\nERROR: Unsupported platform. Use --windows or --macos to specify.")
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
    print("\n[1/4] Checking resources...")
    if not check_resources(project_dir, platform):
        return 1
    print("All resources found.")

    # Step 2: Clean previous build
    print("\n[2/4] Cleaning previous build...")
    if not clean_build(project_dir):
        return 1
    print("Clean complete.")

    # Step 3: Run PyInstaller
    print("\n[3/4] Running PyInstaller...")
    if not run_pyinstaller(project_dir, platform):
        return 1

    # Step 4: Verify output
    print("\n[4/4] Verifying output...")
    if not verify_output(project_dir, platform):
        return 1

    print("\n" + "=" * 50)
    print(f"Build completed successfully! ({platform})")
    print("=" * 50)
    return 0


if __name__ == '__main__':
    sys.exit(main())
