#!/usr/bin/env python3
"""
USB Relay Manager - Main Entry Point

Extracts embedded resources and launches the GUI application.
Supports Windows and macOS platforms.

Based on gnirehtet by Genymobile (https://github.com/Genymobile/gnirehtet)
Licensed under Apache 2.0
"""

import os
import sys
import shutil
import tempfile
from pathlib import Path

IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'

# Platform-specific binary extensions
BIN_EXT = '.exe' if IS_WINDOWS else ''
ICON_EXT = '.ico' if IS_WINDOWS else '.icns' if IS_MACOS else '.png'


def get_resource_path(relative_path: str) -> Path:
    """Get absolute path to resource, works for dev and PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = Path(sys._MEIPASS)
    else:
        # Development mode - resources are in sibling directory
        base_path = Path(__file__).parent.parent / 'resources'
    return base_path / relative_path


def get_app_data_dir() -> Path:
    """Get application data directory for extracted binaries."""
    if IS_WINDOWS:
        app_data = Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir()))
    elif IS_MACOS:
        app_data = Path.home() / 'Library' / 'Application Support'
    else:
        app_data = Path.home() / '.local' / 'share'

    usb_relay_dir = app_data / 'USBRelay'
    usb_relay_dir.mkdir(parents=True, exist_ok=True)
    return usb_relay_dir


def extract_resources() -> dict:
    """Extract embedded binaries to app data directory."""
    app_dir = get_app_data_dir()
    resources = {
        'gnirehtet': app_dir / f'gnirehtet{BIN_EXT}',
        'adb': app_dir / f'adb{BIN_EXT}',
        'logo': app_dir / 'scan_logo.png',
        'icon': app_dir / f'scan_icon{ICON_EXT}',
    }

    # Extract each resource if not already present or if source is newer
    for name, dest in resources.items():
        if name == 'logo':
            source = get_resource_path('scan_logo.png')
        elif name == 'icon':
            source = get_resource_path(f'scan_icon{ICON_EXT}')
        else:
            source = get_resource_path(f'{name}{BIN_EXT}')

        if source.exists():
            # Always copy to ensure latest version
            shutil.copy2(source, dest)
            if not IS_WINDOWS and name not in ('logo', 'icon'):
                os.chmod(dest, 0o755)  # Make executable on Unix

    return resources


def main():
    """Main entry point."""
    # Extract resources first
    resources = extract_resources()

    # Import and launch GUI
    from gui import USBRelayApp
    app = USBRelayApp(resources)
    app.run()


if __name__ == '__main__':
    main()
