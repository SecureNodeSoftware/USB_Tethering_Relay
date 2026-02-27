#!/usr/bin/env python3
"""
USB Relay Manager - ADB Device Monitor

Monitors for device connections via ADB and sets up the reverse tunnel
for USB relay connectivity.

For SCAN Mobile devices the built-in VPN activity is started directly.
For other Android devices the bundled gnirehtet APK is installed and
launched as a fallback.

Based on gnirehtet by Genymobile (https://github.com/Genymobile/gnirehtet)
Licensed under Apache 2.0
"""

import subprocess
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional, Set

from device_monitor import DeviceMonitor


# Relay port for gnirehtet
RELAY_PORT = 31416

IS_WINDOWS = sys.platform == 'win32'

def _subprocess_kwargs():
    """Platform-specific subprocess keyword arguments."""
    kwargs = {}
    if IS_WINDOWS and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


def get_system_dns_servers() -> List[str]:
    """Get DNS servers configured on the host system."""
    dns_servers = []

    try:
        if sys.platform == 'win32':
            # Windows: parse ipconfig /all output
            result = subprocess.run(
                ['ipconfig', '/all'],
                capture_output=True,
                text=True,
                timeout=10,
                **_subprocess_kwargs()
            )

            # Match DNS Server lines (handles both primary and secondary)
            # Pattern matches lines like "   DNS Servers . . . . . . . . . . . : 10.1.10.7"
            # and continuation lines like "                                       8.8.8.8"
            in_dns_section = False
            for line in result.stdout.split('\n'):
                if 'DNS Servers' in line:
                    in_dns_section = True
                    # Extract IP from this line
                    match = re.search(r':\s*([\d.]+)', line)
                    if match:
                        dns_servers.append(match.group(1))
                elif in_dns_section:
                    # Check for continuation line (indented IP address)
                    match = re.match(r'^\s+([\d.]+)\s*$', line)
                    if match:
                        dns_servers.append(match.group(1))
                    elif line.strip() and not line.startswith(' ' * 20):
                        # Non-continuation, non-empty line - end of DNS section
                        in_dns_section = False
        else:
            # Unix/Linux/Mac: read resolv.conf
            resolv_path = Path('/etc/resolv.conf')
            if resolv_path.exists():
                content = resolv_path.read_text()
                for line in content.split('\n'):
                    if line.strip().startswith('nameserver'):
                        parts = line.split()
                        if len(parts) >= 2:
                            dns_servers.append(parts[1])
    except Exception:
        pass

    # Remove duplicates while preserving order
    seen = set()
    unique_dns = []
    for dns in dns_servers:
        if dns not in seen:
            seen.add(dns)
            unique_dns.append(dns)

    return unique_dns if unique_dns else ['8.8.8.8']  # Fallback to Google DNS only if nothing found


class ADBMonitor(DeviceMonitor):
    """Monitors ADB for device connections and sets up USB relay tunnel."""

    def __init__(
        self,
        adb_path: Path,
        on_device_connected: Optional[Callable[[str], None]] = None,
        on_device_disconnected: Optional[Callable[[], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        poll_interval: float = 2.0,
        apk_path: Optional[Path] = None
    ):
        super().__init__(
            on_device_connected=on_device_connected,
            on_device_disconnected=on_device_disconnected,
            on_log=on_log,
            poll_interval=poll_interval,
        )
        self.adb_path = adb_path
        self.apk_path = apk_path
        self._known_devices: Set[str] = set()
        self._current_device: Optional[str] = None

    def stop(self, kill_server: bool = True):
        """Stop device monitoring and optionally kill ADB server."""
        super().stop()
        if kill_server:
            self._kill_adb_server()

    # -- DeviceMonitor hooks --

    def _pre_start(self) -> bool:
        self._log("ADB device monitoring started", 'info')
        return True

    def _post_stop(self):
        self._log("ADB device monitoring stopped", 'info')

    def _poll(self):
        devices = self._get_connected_devices()
        self._process_device_changes(devices)

    # -- ADB-specific logic --

    def _kill_adb_server(self):
        """Kill the ADB server daemon and any remaining adb processes."""
        # Try graceful shutdown first
        try:
            subprocess.run(
                [str(self.adb_path), 'kill-server'],
                capture_output=True,
                timeout=5,
                cwd=str(self.adb_path.parent),
                **_subprocess_kwargs()
            )
        except Exception:
            pass

        # Force kill any remaining adb processes
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ['taskkill', '/f', '/im', 'adb.exe'],
                    capture_output=True,
                    timeout=5,
                    **_subprocess_kwargs()
                )
            else:
                subprocess.run(
                    ['pkill', '-f', 'adb'],
                    capture_output=True,
                    timeout=5
                )
        except Exception:
            pass

        self._log("ADB server stopped", 'info')

    def _get_connected_devices(self) -> Set[str]:
        """Get list of connected device IDs."""
        try:
            result = subprocess.run(
                [str(self.adb_path), 'devices'],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.adb_path.parent),
                **_subprocess_kwargs()
            )

            devices = set()
            for line in result.stdout.strip().split('\n')[1:]:  # Skip header
                if '\tdevice' in line:
                    device_id = line.split('\t')[0]
                    devices.add(device_id)

            return devices

        except subprocess.TimeoutExpired:
            self._log("ADB command timed out", 'warning')
            return self._known_devices
        except FileNotFoundError:
            self._log(f"ADB not found at {self.adb_path}", 'error')
            return set()
        except Exception as e:
            self._log(f"Error getting devices: {e}", 'error')
            return self._known_devices

    def _process_device_changes(self, current_devices: Set[str]):
        """Process device connection/disconnection events."""
        # Check for new devices
        new_devices = current_devices - self._known_devices
        for device_id in new_devices:
            self._on_device_found(device_id)

        # Check for disconnected devices
        disconnected = self._known_devices - current_devices
        for device_id in disconnected:
            self._on_device_lost(device_id)

        self._known_devices = current_devices

    def _on_device_found(self, device_id: str):
        """Handle new device connection."""
        self._log(f"Device detected: {device_id}", 'info')

        # Set up reverse tunnel
        self._setup_reverse_tunnel(device_id)

        # Branch on SCAN Mobile detection
        if self._has_scan_mobile(device_id):
            self._log("SCAN Mobile detected — using built-in VPN", 'info')
            self._start_usb_relay_on_device(device_id)
        else:
            self._log("SCAN Mobile not found — using gnirehtet APK", 'info')
            self._install_and_start_gnirehtet(device_id)

        self._current_device = device_id
        if self.on_device_connected:
            self.on_device_connected(device_id)

    def _on_device_lost(self, device_id: str):
        """Handle device disconnection."""
        self._log(f"Device disconnected: {device_id}", 'warning')

        if device_id == self._current_device:
            self._current_device = None
            if self.on_device_disconnected:
                self.on_device_disconnected()

    def _setup_reverse_tunnel(self, device_id: str):
        """Set up ADB reverse tunnel for relay connection."""
        self._log(f"Setting up reverse tunnel on {device_id}...", 'info')

        try:
            result = subprocess.run(
                [
                    str(self.adb_path), '-s', device_id, 'reverse',
                    f'localabstract:gnirehtet', f'tcp:{RELAY_PORT}'
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.adb_path.parent),
                **_subprocess_kwargs()
            )

            if result.returncode == 0:
                self._log(f"Reverse tunnel established on port {RELAY_PORT}", 'success')
            else:
                self._log(f"Reverse tunnel failed: {result.stderr}", 'error')

        except Exception as e:
            self._log(f"Reverse tunnel error: {e}", 'error')

    def _has_scan_mobile(self, device_id: str) -> bool:
        """Check whether SCAN Mobile is installed on the device."""
        try:
            result = subprocess.run(
                [
                    str(self.adb_path), '-s', device_id, 'shell',
                    'pm', 'list', 'packages', 'com.scan.mobile.ionic2'
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.adb_path.parent),
                **_subprocess_kwargs()
            )
            return 'com.scan.mobile.ionic2' in result.stdout
        except Exception as e:
            self._log(f"Package check failed: {e}", 'warning')
            return False

    def _start_usb_relay_on_device(self, device_id: str):
        """Start SCAN Mobile's built-in USB relay VPN on the device."""
        self._log(f"Starting USB relay on {device_id}...", 'info')

        try:
            # Get DNS servers from host system
            dns_servers = get_system_dns_servers()
            dns_string = ','.join(dns_servers)
            self._log(f"Using DNS servers: {dns_string}", 'info')

            # Start SCAN Mobile's USB Relay activity with host's DNS servers
            # Note: Uses full class path because Java package is com.scan.mobile.network.usbrelay
            result = subprocess.run(
                [
                    str(self.adb_path), '-s', device_id, 'shell',
                    'am', 'start', '-a', 'com.scan.mobile.usbrelay.START',
                    '-n', 'com.scan.mobile.ionic2/com.scan.mobile.network.usbrelay.ScanUsbRelayActivity',
                    '--esa', 'dnsServers', dns_string
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.adb_path.parent),
                **_subprocess_kwargs()
            )

            if result.returncode == 0:
                self._log("USB relay started on device", 'success')
            else:
                self._log(f"Failed to start USB relay: {result.stderr}", 'warning')

        except Exception as e:
            self._log(f"Error starting USB relay: {e}", 'error')

    def _install_and_start_gnirehtet(self, device_id: str):
        """Install the gnirehtet APK and start the VPN on a non-SCAN device."""
        if not self.apk_path or not self.apk_path.exists():
            self._log("gnirehtet.apk not available — cannot set up VPN", 'error')
            return

        # Install the APK (overwrite if already present)
        self._log(f"Installing gnirehtet.apk on {device_id}...", 'info')
        try:
            result = subprocess.run(
                [
                    str(self.adb_path), '-s', device_id,
                    'install', '-r', str(self.apk_path)
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(self.adb_path.parent),
                **_subprocess_kwargs()
            )
            if result.returncode != 0:
                self._log(f"APK install failed: {result.stderr}", 'error')
                return
            self._log("gnirehtet.apk installed", 'success')
        except Exception as e:
            self._log(f"APK install error: {e}", 'error')
            return

        # Start the standard gnirehtet VPN activity
        self._log(f"Starting gnirehtet VPN on {device_id}...", 'info')
        try:
            result = subprocess.run(
                [
                    str(self.adb_path), '-s', device_id, 'shell',
                    'am', 'start', '-a', 'com.genymobile.gnirehtet.START'
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.adb_path.parent),
                **_subprocess_kwargs()
            )
            if result.returncode == 0:
                self._log("gnirehtet VPN started on device", 'success')
            else:
                self._log(f"Failed to start gnirehtet VPN: {result.stderr}", 'warning')
        except Exception as e:
            self._log(f"Error starting gnirehtet VPN: {e}", 'error')
