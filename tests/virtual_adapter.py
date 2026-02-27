#!/usr/bin/env python3
"""
Virtual Network Adapter for DHCP Simulation Testing

Creates and manages a Microsoft Loopback Adapter so the full DHCP
handshake and NAT connectivity can be tested without physical hardware.

Requires:
  - Windows OS
  - Administrator privileges (adapter creation + privileged port binding)

The adapter is installed using the Windows SetupDi API (equivalent of
``devcon install netloop.inf *MSLOOP``), renamed for easy identification,
and assigned the gateway IP on the tethering subnet.  Cleanup is handled
by the context manager protocol and an atexit safety net.

Licensed under GPL v3
"""

import atexit
import subprocess
import time


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADAPTER_NAME = 'USBRelaySimulated'
LOOPBACK_HWID = r'*MSLOOP'
GATEWAY_IP = '192.168.137.1'
PREFIX_LENGTH = 24  # /24 = 255.255.255.0


class VirtualAdapter:
    """Context manager that provisions a Microsoft Loopback Adapter.

    Usage::

        with VirtualAdapter() as va:
            print(va.adapter_name, va.gateway_ip, va.interface_index)
            # ... run DHCP tests against the adapter ...
        # adapter is removed automatically

    Attributes:
        adapter_name:    The friendly name assigned to the adapter.
        gateway_ip:      The IP address configured on the adapter.
        interface_index: The Windows interface index (int or None).
        is_ready:        True once setup() has completed successfully.
    """

    def __init__(
        self,
        adapter_name: str = ADAPTER_NAME,
        gateway_ip: str = GATEWAY_IP,
        prefix_length: int = PREFIX_LENGTH,
    ):
        self.adapter_name = adapter_name
        self.gateway_ip = gateway_ip
        self.prefix_length = prefix_length

        self.interface_index: int | None = None
        self.is_ready: bool = False

        self._instance_id: str | None = None
        self._original_name: str | None = None
        self._cleanup_registered: bool = False

    # -- Context manager protocol --

    def __enter__(self):
        self.setup()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.teardown()
        return False  # don't suppress exceptions

    # -- Public API --

    def setup(self):
        """Install the loopback adapter, rename it, and assign the IP."""
        self._install_adapter()
        self._rename_adapter()
        self._assign_ip()

        # Safety net: ensure teardown runs even if the caller forgets
        if not self._cleanup_registered:
            atexit.register(self.teardown)
            self._cleanup_registered = True

        self.is_ready = True

    def teardown(self):
        """Remove the IP configuration and uninstall the adapter."""
        if not self._instance_id:
            return

        # Remove IP address (best-effort, use index since alias may be stale)
        if self.interface_index is not None:
            self._run_ps(
                f"Remove-NetIPAddress -InterfaceIndex {self.interface_index} "
                f"-Confirm:$false -ErrorAction SilentlyContinue"
            )

        # Remove the adapter device
        self._run_ps(
            f"pnputil /remove-device '{self._instance_id}'"
        )

        self._instance_id = None
        self.interface_index = None
        self.is_ready = False

    # -- Internal helpers --

    def _install_adapter(self):
        """Create a Microsoft Loopback Adapter device instance.

        Uses the Windows SetupDi API via inline C# to create a PnP
        device node and install the loopback driver — the programmatic
        equivalent of ``devcon install %windir%\\inf\\netloop.inf *MSLOOP``.
        """
        # Inline C# that calls SetupDiCreateDeviceInfo + UpdateDriver
        # to create a new loopback adapter without requiring devcon.exe.
        install_script = r"""
$csSource = @'
using System;
using System.Runtime.InteropServices;

public static class LoopbackInstaller
{
    static readonly Guid GUID_DEVCLASS_NET =
        new Guid("4d36e972-e325-11ce-bfc1-08002be10318");

    [StructLayout(LayoutKind.Sequential)]
    public struct SP_DEVINFO_DATA
    {
        public uint   cbSize;
        public Guid   ClassGuid;
        public uint   DevInst;
        public IntPtr Reserved;
    }

    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern IntPtr SetupDiCreateDeviceInfoList(
        ref Guid ClassGuid, IntPtr hwndParent);

    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool SetupDiCreateDeviceInfo(
        IntPtr DeviceInfoSet, string DeviceName, ref Guid ClassGuid,
        string DeviceDescription, IntPtr hwndParent, uint CreationFlags,
        ref SP_DEVINFO_DATA DeviceInfoData);

    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool SetupDiSetDeviceRegistryProperty(
        IntPtr DeviceInfoSet, ref SP_DEVINFO_DATA DeviceInfoData,
        uint Property, byte[] PropertyBuffer, uint PropertyBufferSize);

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiCallClassInstaller(
        uint InstallFunction, IntPtr DeviceInfoSet,
        ref SP_DEVINFO_DATA DeviceInfoData);

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);

    [DllImport("newdev.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool UpdateDriverForPlugAndPlayDevices(
        IntPtr hwndParent, string HardwareId, string FullInfPath,
        uint InstallFlags, out bool bRebootRequired);

    const uint DICD_GENERATE_ID    = 0x01;
    const uint SPDRP_HARDWAREID    = 0x01;
    const uint DIF_REGISTERDEVICE  = 0x19;
    const uint INSTALLFLAG_FORCE   = 0x01;

    public static void Install()
    {
        string hwId    = "*MSLOOP";
        string infPath = Environment.GetFolderPath(
                             Environment.SpecialFolder.Windows)
                         + @"\INF\netloop.inf";
        Guid cls = GUID_DEVCLASS_NET;

        IntPtr devs = SetupDiCreateDeviceInfoList(ref cls, IntPtr.Zero);
        if (devs == (IntPtr)(-1))
            throw new Exception(
                "SetupDiCreateDeviceInfoList failed: "
                + Marshal.GetLastWin32Error());
        try
        {
            var d = new SP_DEVINFO_DATA();
            d.cbSize = (uint)Marshal.SizeOf(typeof(SP_DEVINFO_DATA));

            if (!SetupDiCreateDeviceInfo(devs, "NET", ref cls,
                    null, IntPtr.Zero, DICD_GENERATE_ID, ref d))
                throw new Exception(
                    "SetupDiCreateDeviceInfo failed: "
                    + Marshal.GetLastWin32Error());

            byte[] hwBytes =
                System.Text.Encoding.Unicode.GetBytes(hwId + "\0\0");
            if (!SetupDiSetDeviceRegistryProperty(devs, ref d,
                    SPDRP_HARDWAREID, hwBytes, (uint)hwBytes.Length))
                throw new Exception(
                    "SetupDiSetDeviceRegistryProperty failed: "
                    + Marshal.GetLastWin32Error());

            if (!SetupDiCallClassInstaller(DIF_REGISTERDEVICE, devs, ref d))
                throw new Exception(
                    "SetupDiCallClassInstaller failed: "
                    + Marshal.GetLastWin32Error());

            bool reboot;
            if (!UpdateDriverForPlugAndPlayDevices(
                    IntPtr.Zero, hwId, infPath,
                    INSTALLFLAG_FORCE, out reboot))
                throw new Exception(
                    "UpdateDriverForPlugAndPlayDevices failed: "
                    + Marshal.GetLastWin32Error());
        }
        finally
        {
            SetupDiDestroyDeviceInfoList(devs);
        }
    }
}
'@

Add-Type -TypeDefinition $csSource
[LoopbackInstaller]::Install()
Write-Output 'OK'
"""
        result = self._run_ps(install_script, check=False)
        if result.returncode != 0 or 'OK' not in result.stdout:
            raise RuntimeError(
                "Failed to install loopback adapter.\n"
                f"  stdout: {result.stdout.strip()}\n"
                f"  stderr: {result.stderr.strip()}"
            )

        # Give Windows a moment to finish bringing the adapter online
        time.sleep(2.0)

        # Find the newly-created loopback adapter instance
        find_cmd = (
            "Get-PnpDevice -Class Net -Status OK | "
            "Where-Object { $_.HardwareID -contains '*MSLOOP' } | "
            "Sort-Object -Property InstanceId -Descending | "
            "Select-Object -First 1 -ExpandProperty InstanceId"
        )
        result = self._run_ps(find_cmd, check=True)
        instance_id = result.stdout.strip()
        if not instance_id:
            raise RuntimeError(
                "Loopback adapter driver installed but device not found "
                "via Get-PnpDevice."
            )
        self._instance_id = instance_id

    def _rename_adapter(self):
        """Rename the adapter to our well-known name for easy targeting."""
        # The network adapter may take a few seconds to appear after the
        # PnP device is created.  Poll Get-NetAdapter until it shows up.
        find_cmd = (
            "Get-NetAdapter | Where-Object {"
            "  $_.DriverDescription -match 'Loopback' -or"
            "  $_.InterfaceDescription -match 'Loopback'"
            "} | Sort-Object -Property ifIndex -Descending |"
            " Select-Object -First 1 |"
            " Format-List -Property Name, InterfaceIndex"
        )

        adapter_name = None
        adapter_idx = None
        for attempt in range(10):
            result = self._run_ps(find_cmd, check=False)
            output = result.stdout.strip()
            if output:
                for line in output.splitlines():
                    line = line.strip()
                    if line.startswith('Name'):
                        adapter_name = line.split(':', 1)[1].strip()
                    elif line.startswith('InterfaceIndex'):
                        adapter_idx = line.split(':', 1)[1].strip()
                if adapter_name and adapter_idx:
                    break
            time.sleep(1.0)

        if not adapter_name or not adapter_idx:
            raise RuntimeError(
                "Loopback adapter was created but never appeared in "
                "Get-NetAdapter after 10 seconds."
            )

        self._original_name = adapter_name
        self.interface_index = int(adapter_idx)

        if adapter_name != self.adapter_name:
            self._run_ps(
                f"Rename-NetAdapter -Name '{adapter_name}' "
                f"-NewName '{self.adapter_name}' -Confirm:$false",
                check=True,
            )

    def _assign_ip(self):
        """Configure the gateway IP address on the adapter."""
        ifidx = self.interface_index
        if ifidx is None:
            raise RuntimeError("No interface index — _rename_adapter must "
                               "run before _assign_ip")

        # Ensure the adapter is enabled and wait for it to be ready
        self._run_ps(
            f"Enable-NetAdapter -InterfaceIndex {ifidx} "
            f"-Confirm:$false -ErrorAction SilentlyContinue"
        )
        time.sleep(1.0)

        # Remove all existing IP addresses (IPv4 and IPv6) so
        # New-NetIPAddress doesn't conflict with APIPA/link-local.
        self._run_ps(
            f"Get-NetIPAddress -InterfaceIndex {ifidx} "
            f"-ErrorAction SilentlyContinue | "
            f"Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue"
        )

        # Disable the automatic APIPA address so it doesn't race back
        self._run_ps(
            f"Set-NetIPInterface -InterfaceIndex {ifidx} "
            f"-AddressFamily IPv4 -Dhcp Disabled -ErrorAction SilentlyContinue"
        )

        result = self._run_ps(
            f"New-NetIPAddress -InterfaceIndex {ifidx} "
            f"-IPAddress {self.gateway_ip} "
            f"-PrefixLength {self.prefix_length}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to assign IP {self.gateway_ip} to "
                f"interface index {ifidx}.\n"
                f"  stdout: {result.stdout.strip()}\n"
                f"  stderr: {result.stderr.strip()}"
            )

        # Wait until the IP is actually bindable (shows up in ipconfig).
        # New-NetIPAddress can return before the stack is ready.
        verify_cmd = (
            f"(Get-NetIPAddress -InterfaceIndex {ifidx} "
            f"-IPAddress {self.gateway_ip} "
            f"-ErrorAction SilentlyContinue).IPAddress"
        )
        for attempt in range(15):
            time.sleep(1.0)
            r = self._run_ps(verify_cmd, check=False)
            if self.gateway_ip in r.stdout:
                break
        else:
            raise RuntimeError(
                f"IP {self.gateway_ip} was assigned but never became "
                f"visible on interface index {ifidx} after 15 seconds."
            )

    @staticmethod
    def _run_ps(
        command: str,
        check: bool = False,
    ) -> subprocess.CompletedProcess:
        """Execute a PowerShell command and return the result."""
        return subprocess.run(
            ['powershell', '-NoProfile', '-Command', command],
            capture_output=True,
            text=True,
            timeout=30,
            check=check,
        )
