#!/usr/bin/env python3
"""
USB Relay Manager - Windows Mobile Device Monitor

Detects Windows Mobile/CE devices connected via USB RNDIS and verifies
that NAT is pre-configured on the host PC so the device can reach the
internet. Replaces the tethering functionality previously provided by
Windows Mobile Device Center (WMDC), which is no longer supported on
Windows 10 1703+ / Windows 11.

When a device connects, a lightweight DHCP server is started on the
RNDIS interface so that the device automatically receives its IP
configuration — no manual static IP setup is needed on the device.

Requires that an administrator has run setup_admin.ps1 (or followed the
steps in ADMIN_SETUP_GUIDE.md) before first use.  The admin setup creates:
  - A persistent WinNAT rule for the RNDIS subnet
  - A SYSTEM-level scheduled task that assigns the gateway IP to RNDIS
    adapters when they connect
  - A firewall rule allowing inbound DHCP (UDP port 67)

At runtime this monitor performs only read-only checks — no admin
privileges are needed.

Licensed under GPL v3
"""

import re
import subprocess
import sys
import time
from typing import Callable, Optional

from device_monitor import DeviceMonitor
from dhcp_server import DHCPServer

IS_WINDOWS = sys.platform == 'win32'

# Network configuration for the USB local link (must match setup_admin.ps1)
SUBNET_PREFIX = "192.168.137.0/24"
GATEWAY_IP = "192.168.137.1"
DEVICE_IP = "192.168.137.2"
NAT_NAME = "USBRelayNAT"
TASK_NAME = "USBRelay-RNDIS-IPConfig"
FW_RULE_NAME = "USBRelay-DHCP-Server"


def _subprocess_kwargs():
    """Platform-specific subprocess keyword arguments."""
    kwargs = {}
    if IS_WINDOWS and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _run_powershell(command: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run a PowerShell command and return the result."""
    return subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True,
        text=True,
        timeout=timeout,
        **_subprocess_kwargs()
    )


def _ps_quote(value: str) -> str:
    """Escape a string for safe use inside PowerShell single quotes.

    PowerShell single-quoted strings treat everything as literal except
    embedded single quotes, which must be doubled ('').
    """
    return value.replace("'", "''")


# Adapter names from Get-NetAdapter should only contain safe characters.
# Reject anything that looks like it contains shell metacharacters.
_SAFE_ADAPTER_NAME = re.compile(r'^[\w\s\-().#]+$')


class WMDCMonitor(DeviceMonitor):
    """Monitors for Windows Mobile/CE RNDIS USB connections.

    Verifies that the pre-configured WinNAT rule and scheduled-task-based
    IP assignment are working correctly.  All runtime operations are
    read-only — no admin privileges required.
    """

    def __init__(
        self,
        on_device_connected: Optional[Callable[[str], None]] = None,
        on_device_disconnected: Optional[Callable[[], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        poll_interval: float = 2.0,
        adapter_pattern: str = 'RNDIS|Remote NDIS',
    ):
        super().__init__(
            on_device_connected=on_device_connected,
            on_device_disconnected=on_device_disconnected,
            on_log=on_log,
            poll_interval=poll_interval,
        )
        self._adapter_pattern = adapter_pattern
        self._current_adapter: Optional[str] = None
        self._dhcp_server: Optional[DHCPServer] = None

    # -- DeviceMonitor hooks --

    def _pre_start(self) -> bool:
        if not IS_WINDOWS:
            self._log("Windows Mobile mode is only available on Windows", 'error')
            return False

        self._log("Checking pre-configuration...", 'info')
        issues = self._check_preconfiguration()
        if issues:
            for issue in issues:
                self._log(issue, 'error')
            self._log(
                "An administrator must run setup_admin.ps1 first. "
                "See ADMIN_SETUP_GUIDE.md for details.",
                'error'
            )
            return False

        self._log("Windows Mobile device monitoring started", 'info')
        return True

    def _post_stop(self):
        self._stop_dhcp_server()
        self._current_adapter = None
        self._log("Windows Mobile device monitoring stopped", 'info')

    def _poll(self):
        adapter_name = self._find_rndis_adapter()

        if adapter_name and not self._current_adapter:
            self._on_adapter_connected(adapter_name)
        elif not adapter_name and self._current_adapter:
            self._on_adapter_disconnected()

    # -- RNDIS adapter detection --

    def _find_rndis_adapter(self) -> Optional[str]:
        """Find a connected RNDIS (or simulated) USB network adapter.

        Matches adapters whose InterfaceDescription matches the
        configured pattern (default ``RNDIS|Remote NDIS``), **or**
        whose Name starts with ``USBRelay`` (used by the simulate
        test's virtual loopback adapter).
        """
        try:
            safe_pattern = _ps_quote(self._adapter_pattern)
            result = _run_powershell(
                "Get-NetAdapter | Where-Object {"
                f"  ($_.InterfaceDescription -match '{safe_pattern}' -or"
                "   $_.Name -like 'USBRelay*') -and"
                "  $_.Status -eq 'Up'"
                "} | Select-Object -First 1 -ExpandProperty Name"
            )
            name = result.stdout.strip()
            if not name:
                return None
            if not _SAFE_ADAPTER_NAME.match(name):
                self._log(f"RNDIS adapter name contains invalid characters: {name!r}", 'error')
                return None
            return name
        except Exception:
            return None

    # -- Connection handling --

    def _on_adapter_connected(self, adapter_name: str):
        """Handle RNDIS adapter appearing."""
        self._log(f"RNDIS adapter detected: {adapter_name}", 'success')

        # Wait for the scheduled task to assign the gateway IP
        if not self._wait_for_adapter_ip(adapter_name):
            self._log(
                f"Gateway IP {GATEWAY_IP} was not assigned to {adapter_name}. "
                "The scheduled task may not be running — ask your administrator.",
                'error'
            )
            return

        if not self._verify_nat_exists():
            self._log(
                "NAT rule not found. An administrator must run setup_admin.ps1.",
                'error'
            )
            return

        self._log("Using pre-configured NAT (setup by administrator)", 'success')

        # Start DHCP server so the device gets its IP automatically
        self._start_dhcp_server()

        self._current_adapter = adapter_name

        if self.on_device_connected:
            self.on_device_connected(adapter_name)

    def _on_adapter_disconnected(self):
        """Handle RNDIS adapter disappearing."""
        adapter_name = self._current_adapter
        self._log(f"RNDIS adapter disconnected: {adapter_name}", 'warning')

        self._stop_dhcp_server()
        self._current_adapter = None

        if self.on_device_disconnected:
            self.on_device_disconnected()

    # -- Pre-configuration checks --

    def _check_preconfiguration(self) -> list:
        """Verify that an administrator has run setup_admin.ps1.

        Returns a list of human-readable issues (empty = all good).
        All checks here are read-only and safe for standard users.
        """
        issues = []

        # Check WinNAT service is running
        try:
            result = _run_powershell(
                "(Get-Service -Name 'winnat' -ErrorAction Stop).Status"
            )
            status = result.stdout.strip()
            if status != 'Running':
                issues.append(f"WinNAT service is '{status}' (must be Running)")
        except Exception:
            issues.append("WinNAT service not found on this system")

        # Check NAT rule exists
        try:
            result = _run_powershell(
                f"Get-NetNat -Name '{NAT_NAME}' -ErrorAction Stop "
                "| Select-Object -ExpandProperty InternalIPInterfaceAddressPrefix"
            )
            prefix = result.stdout.strip()
            if result.returncode != 0 or not prefix:
                issues.append(f"NAT rule '{NAT_NAME}' not found")
            elif prefix != SUBNET_PREFIX:
                issues.append(
                    f"NAT rule '{NAT_NAME}' has wrong subnet: {prefix} "
                    f"(expected {SUBNET_PREFIX})"
                )
        except Exception:
            issues.append(f"NAT rule '{NAT_NAME}' not found")

        # NOTE: The scheduled task check is intentionally skipped here.
        # Standard (non-admin) users cannot query SYSTEM-level tasks on many
        # Windows configurations.  The NAT rule and firewall checks above
        # already confirm that setup_admin.ps1 was run successfully (the task
        # is created in the same script).  If the task is missing or broken,
        # the user will see a clear symptom when a device connects (no IP
        # assigned to the RNDIS adapter).

        # Check firewall rule for DHCP server
        try:
            result = _run_powershell(
                f"Get-NetFirewallRule -DisplayName '{FW_RULE_NAME}' "
                "-ErrorAction Stop | Select-Object -ExpandProperty Enabled"
            )
            enabled = result.stdout.strip()
            if result.returncode != 0 or not enabled:
                issues.append(f"Firewall rule '{FW_RULE_NAME}' not found "
                              "(DHCP auto-configuration will not work)")
            elif enabled != 'True':
                issues.append(f"Firewall rule '{FW_RULE_NAME}' is disabled "
                              "(DHCP auto-configuration will not work)")
        except Exception:
            issues.append(f"Firewall rule '{FW_RULE_NAME}' not found "
                          "(DHCP auto-configuration will not work)")

        return issues

    def _wait_for_adapter_ip(self, adapter_name: str, timeout: float = 15.0) -> bool:
        """Wait for the scheduled task to assign the gateway IP to the adapter.

        The SYSTEM-level scheduled task polls every 30s, so we may need to
        wait briefly after the adapter appears.
        """
        safe_name = _ps_quote(adapter_name)
        deadline = time.monotonic() + timeout
        interval = 2.0

        while time.monotonic() < deadline:
            try:
                result = _run_powershell(
                    f"Get-NetIPAddress -InterfaceAlias '{safe_name}' "
                    f"-IPAddress '{GATEWAY_IP}' -ErrorAction Stop"
                )
                if result.returncode == 0 and GATEWAY_IP in result.stdout:
                    self._log(f"Gateway IP {GATEWAY_IP} confirmed on {adapter_name}", 'info')
                    return True
            except Exception:
                pass

            self._log(
                f"Waiting for IP assignment on {adapter_name}... "
                f"({int(deadline - time.monotonic())}s remaining)",
                'info'
            )
            time.sleep(interval)

        return False

    def _verify_nat_exists(self) -> bool:
        """Check if the pre-configured NAT rule exists (read-only)."""
        try:
            result = _run_powershell(
                f"Get-NetNat -Name '{NAT_NAME}' -ErrorAction Stop"
            )
            return result.returncode == 0 and NAT_NAME in result.stdout
        except Exception:
            return False

    # -- DHCP server management --

    def _start_dhcp_server(self):
        """Start the DHCP server to auto-configure the connected device."""
        self._stop_dhcp_server()

        # Detect host DNS servers to pass through to the device
        dns_servers = self._get_dns_servers()

        self._dhcp_server = DHCPServer(
            server_ip=GATEWAY_IP,
            client_ip=DEVICE_IP,
            dns_servers=dns_servers,
            on_log=self._log,
        )
        self._dhcp_server.start()

    def _stop_dhcp_server(self):
        """Stop the DHCP server if it is running."""
        if self._dhcp_server:
            self._dhcp_server.stop()
            self._dhcp_server = None

    @staticmethod
    def _get_dns_servers():
        """Detect system DNS servers (best-effort, falls back to 8.8.8.8)."""
        try:
            from adb_monitor import get_system_dns_servers
            return get_system_dns_servers()
        except Exception:
            return ['8.8.8.8']
