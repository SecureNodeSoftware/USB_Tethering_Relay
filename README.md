# USB Relay Manager

A desktop application for USB reverse tethering on Android devices. Enables network connectivity for devices connected via USB by routing traffic through the host computer's internet connection.

Built on [gnirehtet](https://github.com/Genymobile/gnirehtet) by Genymobile (Apache 2.0).

## Overview

USB reverse tethering allows a mobile device to use a computer's internet connection through a USB cable. This is designed for Honeywell CN80G devices that need network connectivity when docked via USB.

```
┌─────────────────┐         USB          ┌─────────────────┐
│   CN80G Device  │◄────────────────────►│    Computer     │
│                 │                       │                 │
│  VPN Service    │      ADB Tunnel      │  Relay Server   │
│  (USB Relay)    │◄────────────────────►│  (USB Relay)    │
│                 │                       │                 │
└─────────────────┘                       └────────┬────────┘
                                                   │
                                                   ▼
                                              Internet
```

## Supported Platforms

| Platform | Output | Status |
|----------|--------|--------|
| Windows  | `USBRelay.exe` (~25 MB) | Available |
| macOS    | `USBRelay.app` (.zip ~30 MB) | Available |

## Quick Start

### Windows

1. Download `USBRelay.exe` from Tangent (or build from source)
2. Double-click to run - relay starts automatically
3. Connect CN80G device via USB dock
4. Approve USB debugging on device when prompted
5. Approve VPN permission on device (first time only)
6. Device is now online

### macOS

1. Download `USBRelay.app.zip` from Tangent (or build from source)
2. Extract and move to Applications (or run from Downloads)
3. Right-click and select "Open" (first time, to bypass Gatekeeper)
4. Relay starts automatically
5. Connect CN80G device via USB dock
6. Approve USB debugging on device when prompted
7. Approve VPN permission on device (first time only)
8. Device is now online

**macOS note**: You may need to allow the app in System Settings > Privacy & Security if macOS blocks it.

## Features

- Single-file portable application (no installation required)
- Start/Stop buttons with visual status indicator
- Auto-start relay on launch
- Automatic device detection and tunnel setup
- Automatic reconnection when device is unplugged/replugged
- Automatic DNS server detection from host system
- Scrolling log panel with timestamps and log export
- Cross-platform (Windows and macOS)

## Project Structure

```
TETHERING_TOOL/
├── src/                  # Python source code
│   ├── main.py           # Entry point - resource extraction and app launch
│   ├── gui.py            # Tkinter GUI with SCAN branding
│   ├── relay_manager.py  # Gnirehtet relay subprocess manager
│   └── adb_monitor.py    # ADB device detection and tunnel setup
├── resources/            # Bundled binaries and assets
│   ├── adb.exe           # Android Debug Bridge (Windows)
│   ├── AdbWinApi.dll     # ADB Windows API DLL
│   ├── AdbWinUsbApi.dll  # ADB Windows USB API DLL
│   ├── gnirehtet.exe     # Gnirehtet relay server (Windows)
│   ├── gnirehtet.apk     # Gnirehtet APK for device-side VPN
│   ├── app_icon.png      # Application icon (512x512)
│   ├── scan_logo.png     # SCAN brand logo
│   └── scan_icon.ico     # Windows icon
├── scripts/              # Command-line tools
│   └── install-relay-windows.bat  # Windows CLI installer/manager
├── build.py              # PyInstaller build script
├── USBRelay.spec         # PyInstaller spec (Windows)
├── USBRelay.macos.spec   # PyInstaller spec (macOS)
├── requirements.txt      # Python dependencies
├── LICENSE               # GPL v3
└── README.md
```

## Building from Source

### Prerequisites

- Python 3.8+
- PyInstaller (`pip install pyinstaller`)

### Windows

```bash
# Install build dependencies
pip install -r requirements.txt

# Build the executable
python build.py --windows
```

**Required resources** in `resources/`:
- `gnirehtet.exe` - Relay server binary
- `adb.exe` - Android Debug Bridge
- `AdbWinApi.dll` - ADB Windows DLL
- `AdbWinUsbApi.dll` - ADB USB DLL
- `scan_logo.png` - SCAN brand logo
- `scan_icon.ico` - Windows icon

Output: `dist/USBRelay.exe`

### macOS

```bash
# Install build dependencies
pip install -r requirements.txt

# Build the .app bundle
python build.py --macos
```

**Required resources** in `resources/`:
- `gnirehtet` - Relay server binary (no extension, download or build from source)
- `adb` - Android Debug Bridge (no extension, from Android SDK Platform Tools)
- `scan_logo.png` - SCAN brand logo

**Obtaining macOS binaries:**
- **gnirehtet**: Download from [gnirehtet releases](https://github.com/Genymobile/gnirehtet/releases) or build from Rust source (`cargo build --release`)
- **adb**: Download [Android SDK Platform Tools for macOS](https://developer.android.com/tools/releases/platform-tools)

Output: `dist/USBRelay.app` and `dist/USBRelay.app.zip`

## Command Line Tools (Alternative)

For scripted or headless environments on Windows, use the batch script:

```batch
scripts\install-relay-windows.bat install   # Download and install gnirehtet
scripts\install-relay-windows.bat start     # Start relay server
scripts\install-relay-windows.bat stop      # Stop relay server
scripts\install-relay-windows.bat status    # Show installation and connection status
scripts\install-relay-windows.bat autorun   # Start relay with automatic device detection
```

## Troubleshooting

### Device not detected after docking

1. Check USB cable/dock is properly connected
2. Enable USB debugging on device (Settings > Developer Options > USB Debugging)
3. Run `adb devices` to verify connection
4. If device shows "unauthorized", check device screen for authorization prompt
5. Try a different USB port

### macOS: "USBRelay.app is damaged" or cannot be opened

macOS Gatekeeper may block unsigned applications:
1. Right-click the app and select "Open"
2. Click "Open" in the dialog that appears
3. Or: System Settings > Privacy & Security > "Open Anyway"

### VPN permission denied

The first time USB tethering is enabled, Android will prompt for VPN permission. This must be approved for tethering to work.

### Relay disconnected notification on device

1. Ensure USB Relay Manager is running on computer
2. Check ADB tunnel: `adb reverse --list`
3. Stop and restart the relay using the GUI buttons

### Connected but device has no internet

1. Verify the computer itself has internet access
2. Check if a firewall is blocking port 31416
3. DNS issues: the relay auto-detects DNS from the host. Falls back to Google DNS (8.8.8.8) if detection fails

## Technical Details

- **Relay Port**: 31416 (TCP)
- **VPN Address**: 172.16.0.2/32
- **DNS**: Auto-detected from host system (falls back to 8.8.8.8)
- **Default Route**: 0.0.0.0/0 (all traffic)
- **ADB Reverse**: `localabstract:gnirehtet` -> TCP 31416

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

USB Relay Manager is based on [gnirehtet](https://github.com/Genymobile/gnirehtet) developed by Genymobile, licensed under Apache 2.0.
