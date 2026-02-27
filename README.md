#!/usr/bin/env python3
"""
USB Relay Manager - Build Script

Automates the PyInstaller build process to create the final executable.
Supports both Windows (.exe) and macOS (.app) builds.

Usage:
  python build.py              # Auto-detect platform
  python build.py --windows    # Force Windows build
  python build.py --macos      # Force macOS build

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


def check_resources(project_dir: Path, platform: str) -> bool:
    """Verify all required resources are present for the target platform."""
    resources_dir = project_dir / 'resources'

    if platform == 'windows':
        required_files = ['gnirehtet.exe', 'adb.exe']
    else:
        required_files = ['gnirehtet', 'adb']

    missing = []
    for filename in required_files:
        if not (resources_dir / filename).exists():
            missing.append(filename)

    if missing:
        print(f"ERROR: Missing required resources for {platform}:")
        for f in missing:
            print(f"  - {f}")
        print(f"\nPlease ensure these files are in: {resources_dir}")

        if platform == 'macos':
            print("\nTo obtain macOS binaries:")
            print("  gnirehtet: Download from https://github.com/Genymobile/gnirehtet/releases")
            print("             or build from source: cargo build --release")
            print("  adb:       Download Android SDK Platform Tools for macOS from")
            print("             https://developer.android.com/tools/releases/platform-tools")
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
