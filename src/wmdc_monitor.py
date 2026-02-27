#!/usr/bin/env python3
"""
USB Relay Manager - Windows Mobile Device Monitor

Detects Windows Mobile/CE devices connected via USB RNDIS and configures
NAT on the host PC so the device can reach the internet. Replaces the
tethering functionality previously provided by Windows Mobile Device
Center (WMDC), which is no longer supported on Windows 11.

Two operating modes:
  - Admin:     Configures NAT directly (WinNAT -> ICS -> IP Forwarding).
  - Non-admin: Uses pre-configured NAT set up by setup_admin.ps1.
               A SYSTEM-level scheduled task handles IP assignment;
               the app only performs read-only verification.

Licensed under GPL v3
"""

import re
import subprocess
import threading
import time
import sys
from typing import Callable, Optional

IS_WINDOWS = sys.platform == 'win32'

# Network configuration for the USB local link
SUBNET_PREFIX = "192.168.137.0/24"
GATEWAY_IP = "192.168.137.1"
PREFIX_LENGTH = 24
NAT_NAME = "USBRelayNAT"


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


class WMDCMonitor:
    """Monitors for Windows Mobile/CE RNDIS USB connections and configures NAT."""

    def __init__(
        self,
        on_device_connected: Optional[Callable[[str], None]] = None,
        on_device_disconnected: Optional[Callable[[], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        poll_interval: float = 2.0
    ):
        self.on_device_connected = on_device_connected
        self.on_device_disconnected = on_device_disconnected
        self.on_log = on_log
        self.poll_interval = poll_interval

        self._running = False
        self._admin = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._current_adapter: Optional[str] = None
        self._nat_method: Optional[str] = None  # 'winnat', 'ics', 'forwarding', or 'preconfigured'

    def start(self):
        """Start RNDIS device monitoring.

        Works for both admin and non-admin users.  Non-admin users require
        that an administrator has run setup_admin.ps1 first to pre-configure
        the WinNAT rule and the RNDIS IP-assignment scheduled task.
        """
        if self._running:
            return

        if not IS_WINDOWS:
            self._log("Windows Mobile mode is only available on Windows", 'error')
            return

        self._admin = self._is_admin()
        if self._admin:
            self._log("Running with Administrator privileges", 'info')
        else:
            self._log("Running as standard user — checking pre-configuration...", 'info')
            issues = self._check_preconfiguration()
            if issues:
                for issue in issues:
                    self._log(issue, 'error')
                self._log(
                    "An administrator must run setup_admin.ps1 first. "
                    "See setup_admin.ps1 for details.",
                    'error'
                )
                return

        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True
        )
        self._monitor_thread.start()
        self._log("Windows Mobile device monitoring started", 'info')

    def stop(self):
        """Stop monitoring and clean up NAT configuration."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)

        # Clean up NAT if we configured it
        if self._nat_method:
            self._cleanup_nat()

        self._current_adapter = None
        self._nat_method = None
        self._log("Windows Mobile device monitoring stopped", 'info')

    def is_running(self) -> bool:
        """Check if monitor is actively running."""
        return self._running

    # -- Monitoring loop --

    def _monitor_loop(self):
        """Poll for RNDIS adapter connect/disconnect events."""
        while self._running:
            try:
                adapter_name = self._find_rndis_adapter()

                if adapter_name and not self._current_adapter:
                    # New connection
                    self._on_adapter_connected(adapter_name)
                elif not adapter_name and self._current_adapter:
                    # Disconnection
                    self._on_adapter_disconnected()

            except Exception as e:
                self._log(f"Monitor error: {e}", 'error')

            time.sleep(self.poll_interval)

    # -- RNDIS adapter detection --

    def _find_rndis_adapter(self) -> Optional[str]:
        """Find a connected RNDIS USB network adapter via PowerShell."""
        try:
            result = _run_powershell(
                "Get-NetAdapter | Where-Object {"
                "  $_.InterfaceDescription -match 'RNDIS|Remote NDIS' -and"
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

        if self._admin:
            # Admin path: configure everything directly
            if not self._configure_adapter_ip(adapter_name):
                self._log("Failed to configure adapter IP — skipping this adapter", 'error')
                return

            if self._setup_winnat():
                self._nat_method = 'winnat'
                self._log("NAT configured via WinNAT", 'success')
            elif self._setup_ics(adapter_name):
                self._nat_method = 'ics'
                self._log("NAT configured via ICS (fallback)", 'success')
            elif self._setup_ip_forwarding(adapter_name):
                self._nat_method = 'forwarding'
                self._log("IP Forwarding enabled (last resort fallback)", 'warning')
            else:
                self._log("All NAT methods failed. Device will not have internet.", 'error')
                return
        else:
            # Non-admin path: wait for the scheduled task to assign the IP,
            # then verify the pre-configured NAT is in place.
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

            self._nat_method = 'preconfigured'
            self._log("Using pre-configured NAT (setup by administrator)", 'success')

        # Only track adapter after successful setup so that disconnect
        # callbacks aren't fired for adapters we never fully configured.
        self._current_adapter = adapter_name

        if self.on_device_connected:
            self.on_device_connected(adapter_name)

    def _on_adapter_disconnected(self):
        """Handle RNDIS adapter disappearing."""
        adapter_name = self._current_adapter
        self._log(f"RNDIS adapter disconnected: {adapter_name}", 'warning')

        self._cleanup_nat()
        self._current_adapter = None
        self._nat_method = None

        if self.on_device_disconnected:
            self.on_device_disconnected()

    # -- Pre-configuration checks (non-admin) --

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

        # Check scheduled task exists
        try:
            result = _run_powershell(
                f"Get-ScheduledTask -TaskName 'USBRelay-RNDIS-IPConfig' "
                "-ErrorAction Stop | Select-Object -ExpandProperty State"
            )
            state = result.stdout.strip()
            if result.returncode != 0 or not state:
                issues.append("Scheduled task 'USBRelay-RNDIS-IPConfig' not found")
            elif state == 'Disabled':
                issues.append("Scheduled task 'USBRelay-RNDIS-IPConfig' is disabled")
        except Exception:
            issues.append("Scheduled task 'USBRelay-RNDIS-IPConfig' not found")

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

    # -- IP configuration --

    def _configure_adapter_ip(self, adapter_name: str) -> bool:
        """Assign static gateway IP to the RNDIS adapter."""
        self._log(f"Configuring {adapter_name} with IP {GATEWAY_IP}/{PREFIX_LENGTH}...", 'info')
        safe_name = _ps_quote(adapter_name)
        try:
            # Remove any existing IP first
            _run_powershell(
                f"Remove-NetIPAddress -InterfaceAlias '{safe_name}' -Confirm:$false "
                "-ErrorAction SilentlyContinue"
            )
            # Assign the gateway IP
            result = _run_powershell(
                f"New-NetIPAddress -InterfaceAlias '{safe_name}' "
                f"-IPAddress '{GATEWAY_IP}' -PrefixLength {PREFIX_LENGTH} "
                "-ErrorAction Stop"
            )
            if result.returncode != 0:
                self._log(f"IP config error: {result.stderr.strip()}", 'error')
                return False
            return True
        except subprocess.TimeoutExpired:
            self._log("IP configuration timed out", 'error')
            return False
        except Exception as e:
            self._log(f"IP configuration failed: {e}", 'error')
            return False

    # -- NAT Method 1: WinNAT --

    def _ensure_winnat_service(self) -> bool:
        """Make sure the WinNAT service is running, starting it if needed."""
        try:
            result = _run_powershell(
                "(Get-Service -Name 'winnat' -ErrorAction Stop).Status"
            )
            status = result.stdout.strip()
            if status == 'Running':
                return True

            self._log(f"WinNAT service is '{status}' — starting it...", 'info')
            start_result = _run_powershell(
                "Start-Service -Name 'winnat' -ErrorAction Stop"
            )
            if start_result.returncode != 0:
                self._log(
                    f"Failed to start WinNAT service: {start_result.stderr.strip()}",
                    'warning'
                )
                return False

            self._log("WinNAT service started", 'info')
            return True
        except subprocess.TimeoutExpired:
            self._log("WinNAT service check timed out", 'warning')
            return False
        except Exception as e:
            self._log(f"WinNAT service check failed: {e}", 'warning')
            return False

    def _setup_winnat(self) -> bool:
        """Create a WinNAT network for the RNDIS subnet."""
        self._log("Attempting WinNAT setup...", 'info')

        if not self._ensure_winnat_service():
            self._log("WinNAT service unavailable — skipping to next method", 'warning')
            return False

        try:
            # Remove any stale NAT with the same name
            _run_powershell(
                f"Remove-NetNat -Name '{NAT_NAME}' -Confirm:$false "
                "-ErrorAction SilentlyContinue"
            )

            result = _run_powershell(
                f"New-NetNat -Name '{NAT_NAME}' "
                f"-InternalIPInterfaceAddressPrefix '{SUBNET_PREFIX}' "
                "-ErrorAction Stop"
            )
            if result.returncode != 0:
                self._log(f"WinNAT failed: {result.stderr.strip()}", 'warning')
                return False

            self._log(f"WinNAT created: {NAT_NAME} ({SUBNET_PREFIX})", 'info')
            return True
        except subprocess.TimeoutExpired:
            self._log("WinNAT setup timed out", 'warning')
            return False
        except Exception as e:
            self._log(f"WinNAT setup error: {e}", 'warning')
            return False

    def _remove_winnat(self):
        """Remove the WinNAT network."""
        try:
            _run_powershell(
                f"Remove-NetNat -Name '{NAT_NAME}' -Confirm:$false "
                "-ErrorAction SilentlyContinue"
            )
            self._log("WinNAT removed", 'info')
        except Exception:
            pass

    # -- NAT Method 2: ICS (Internet Connection Sharing) --

    def _setup_ics(self, rndis_adapter: str) -> bool:
        """Enable ICS from the internet adapter to the RNDIS adapter."""
        self._log("Attempting ICS fallback...", 'info')
        safe_name = _ps_quote(rndis_adapter)
        try:
            # PowerShell script that:
            # 1. Finds the internet-connected adapter
            # 2. Disables any existing ICS
            # 3. Enables sharing from internet adapter to RNDIS adapter
            ics_script = f"""
$netShare = New-Object -ComObject HNetCfg.HNetShare
$connections = $netShare.EnumEveryConnection

# Disable all existing ICS first
foreach ($conn in $connections) {{
    $config = $netShare.INetSharingConfigurationForINetConnection($conn)
    if ($config.SharingEnabled) {{
        $config.DisableSharing()
    }}
}}

# Re-enumerate to get fresh handles
$connections = $netShare.EnumEveryConnection
$internetConn = $null
$rndisConn = $null

foreach ($conn in $connections) {{
    $props = $netShare.NetConnectionProps($conn)
    $name = $props.Name
    $status = $props.Status

    # Status 2 = Connected
    if ($status -eq 2 -and $name -ne '{safe_name}') {{
        $internetConn = $conn
    }}
    if ($name -eq '{safe_name}') {{
        $rndisConn = $conn
    }}
}}

if (-not $internetConn) {{ throw 'No internet adapter found' }}
if (-not $rndisConn) {{ throw 'RNDIS adapter not found in ICS' }}

# Enable public sharing (internet) on internet adapter
$pubConfig = $netShare.INetSharingConfigurationForINetConnection($internetConn)
$pubConfig.EnableSharing(0)  # 0 = ICSSHARINGTYPE_PUBLIC

# Enable private sharing (local) on RNDIS adapter
$privConfig = $netShare.INetSharingConfigurationForINetConnection($rndisConn)
$privConfig.EnableSharing(1)  # 1 = ICSSHARINGTYPE_PRIVATE

Write-Output 'ICS enabled'
"""
            result = _run_powershell(ics_script, timeout=30)
            if result.returncode != 0:
                self._log(f"ICS failed: {result.stderr.strip()}", 'warning')
                return False

            self._log("ICS sharing enabled", 'info')
            return True
        except subprocess.TimeoutExpired:
            self._log("ICS setup timed out", 'warning')
            return False
        except Exception as e:
            self._log(f"ICS setup error: {e}", 'warning')
            return False

    def _disable_ics(self):
        """Disable all ICS sharing."""
        try:
            _run_powershell(
                "$ns = New-Object -ComObject HNetCfg.HNetShare; "
                "foreach ($c in $ns.EnumEveryConnection) { "
                "  $cfg = $ns.INetSharingConfigurationForINetConnection($c); "
                "  if ($cfg.SharingEnabled) { $cfg.DisableSharing() } "
                "}",
                timeout=15
            )
            self._log("ICS sharing disabled", 'info')
        except Exception:
            pass

    # -- NAT Method 3: IP Forwarding (last resort) --

    def _setup_ip_forwarding(self, rndis_adapter: str) -> bool:
        """Enable IP forwarding on both the internet and RNDIS adapters."""
        self._log("Attempting IP Forwarding (last resort)...", 'info')
        safe_name = _ps_quote(rndis_adapter)
        try:
            # Enable forwarding on the RNDIS adapter
            result = _run_powershell(
                f"Set-NetIPInterface -InterfaceAlias '{safe_name}' "
                "-Forwarding Enabled -ErrorAction Stop"
            )
            if result.returncode != 0:
                self._log(f"IP forwarding failed on RNDIS: {result.stderr.strip()}", 'warning')
                return False

            # Enable forwarding on all connected non-RNDIS adapters
            result = _run_powershell(
                "Get-NetAdapter | Where-Object { "
                "  $_.Status -eq 'Up' -and "
                f"  $_.Name -ne '{safe_name}'"
                "} | ForEach-Object { "
                "  Set-NetIPInterface -InterfaceAlias $_.Name "
                "  -Forwarding Enabled -ErrorAction SilentlyContinue "
                "}"
            )

            self._log("IP forwarding enabled", 'info')
            return True
        except subprocess.TimeoutExpired:
            self._log("IP forwarding setup timed out", 'warning')
            return False
        except Exception as e:
            self._log(f"IP forwarding setup error: {e}", 'warning')
            return False

    def _disable_ip_forwarding(self, rndis_adapter: str):
        """Disable IP forwarding on the RNDIS adapter."""
        try:
            safe_name = _ps_quote(rndis_adapter)
            _run_powershell(
                f"Set-NetIPInterface -InterfaceAlias '{safe_name}' "
                "-Forwarding Disabled -ErrorAction SilentlyContinue"
            )
            self._log("IP forwarding disabled", 'info')
        except Exception:
            pass

    # -- Cleanup --

    def _cleanup_nat(self):
        """Remove whichever NAT method was configured."""
        method = self._nat_method
        adapter = self._current_adapter
        self._log(f"Cleaning up NAT ({method})...", 'info')

        if method == 'preconfigured':
            # Don't remove admin-provisioned configuration
            self._log("Pre-configured NAT left in place (managed by administrator)", 'info')
        elif method == 'winnat':
            self._remove_winnat()
        elif method == 'ics':
            self._disable_ics()
        elif method == 'forwarding' and adapter:
            self._disable_ip_forwarding(adapter)

    # -- Helpers --

    def _is_admin(self) -> bool:
        """Check if the process has Administrator privileges."""
        try:
            result = _run_powershell(
                "([Security.Principal.WindowsPrincipal] "
                "[Security.Principal.WindowsIdentity]::GetCurrent()"
                ").IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"
            )
            return result.stdout.strip().lower() == 'true'
        except Exception:
            return False

    def _log(self, message: str, level: str = 'info'):
        """Send log message to callback."""
        if self.on_log:
            self.on_log(message, level)
