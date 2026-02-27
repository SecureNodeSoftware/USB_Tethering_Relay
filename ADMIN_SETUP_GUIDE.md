# Windows Mobile Tethering — Administrator Setup Guide

This guide explains the manual steps an administrator must perform to enable
Windows Mobile USB tethering for standard (non-admin) users. These steps
are equivalent to running `setup_admin.ps1` and are provided for
administrators who prefer to understand and execute each step individually.

**All commands must be run in an elevated PowerShell prompt** (right-click
PowerShell → "Run as administrator").

---

## Background

When a Windows Mobile device connects via USB, Windows creates an RNDIS
(Remote Network Driver Interface Specification) virtual network adapter.
Previously, Windows Mobile Device Center (WMDC) handled IP assignment,
DHCP, and NAT automatically. WMDC is not supported on Windows 10 1703+
or Windows 11.

This setup replaces WMDC's functionality with four persistent components:

| Component | Purpose | Survives reboot? |
|-----------|---------|-----------------|
| WinNAT service | Kernel-level NAT engine | Yes (service set to Automatic) |
| NAT rule | Routes 192.168.137.0/24 traffic through NAT | Yes (persistent) |
| Scheduled task | Assigns 192.168.137.1 to RNDIS adapter | Yes (runs as SYSTEM) |
| Firewall rule | Allows DHCP server (inbound UDP port 67) | Yes (persistent) |

After setup, the USB Relay Manager app runs without admin privileges.
**Devices receive their IP configuration automatically via DHCP** — no
manual static IP setup is needed on each device.

---

## Prerequisites

- Windows 10 (version 1703+) or Windows 11, Pro or Enterprise edition
- Administrator access (one-time)
- The WinNAT service must be available (included in Pro/Enterprise;
  may not be available on Home edition)

### Check WinNAT availability

```powershell
Get-Service -Name 'winnat'
```

If this returns an error ("Cannot find any service with service name
'winnat'"), your Windows edition does not support WinNAT and Windows
Mobile tethering will not work. WinNAT is included in Pro and Enterprise
editions but may not be available on Home edition.

---

## Step 1: Configure the WinNAT Service

The WinNAT service provides kernel-level Network Address Translation.
It must be running and set to start automatically.

### Check current status

```powershell
Get-Service -Name 'winnat' | Select-Object Name, Status, StartType
```

### Set to automatic start (if not already)

```powershell
Set-Service -Name 'winnat' -StartupType Automatic
```

### Start the service (if not running)

```powershell
Start-Service -Name 'winnat'
```

### Verify

```powershell
Get-Service -Name 'winnat' | Select-Object Name, Status, StartType
```

Expected output:
```
Name   Status  StartType
----   ------  ---------
winnat Running Automatic
```

### Troubleshooting

- **"Access is denied"** — You are not running as administrator.
- **"Cannot find any service"** — WinNAT is not available on this
  Windows edition.
- **Service starts then stops** — Another NAT configuration may conflict.
  Check `Get-NetNat` for existing rules (Docker, Hyper-V, WSL2 can
  create competing NAT networks).

---

## Step 2: Create the NAT Rule

This rule tells WinNAT to perform NAT for traffic originating from the
192.168.137.0/24 subnet (the RNDIS USB network).

### Remove any stale rule with the same name

```powershell
Remove-NetNat -Name 'USBRelayNAT' -Confirm:$false -ErrorAction SilentlyContinue
```

### Create the NAT rule

```powershell
New-NetNat -Name 'USBRelayNAT' -InternalIPInterfaceAddressPrefix '192.168.137.0/24'
```

### Verify

```powershell
Get-NetNat -Name 'USBRelayNAT'
```

Expected output:
```
Name          InternalIPInterfaceAddressPrefix  Active
----          --------------------------------  ------
USBRelayNAT   192.168.137.0/24                  True
```

### Troubleshooting

- **"An instance already exists"** — A NAT rule for this subnet already
  exists (possibly from Docker or Hyper-V). Run
  `Get-NetNat | Format-Table Name, InternalIPInterfaceAddressPrefix`
  to see all rules. Windows allows only one NAT network at a time.
  You may need to remove the conflicting rule first.
- **"The parameter is incorrect"** — Check the subnet prefix format.
  It must be CIDR notation (`192.168.137.0/24`).

---

## Step 3: Create the RNDIS IP Assignment Scheduled Task

This is the most complex step. When a Windows Mobile device is plugged in
via USB, the RNDIS adapter appears but has no IP address. This task
automatically assigns `192.168.137.1` to the adapter.

### Why a scheduled task?

`New-NetIPAddress` requires administrator privileges — there is no
non-admin workaround on Windows. The scheduled task runs as the SYSTEM
account (highest privilege level), so it can assign IPs regardless of
which user is logged in.

### What the task does (every 30 seconds)

1. Checks if an RNDIS adapter is present and Up
2. If no adapter found, exits (does nothing)
3. Checks if `192.168.137.1` is already assigned to the adapter
4. If already assigned, exits (idempotent)
5. Removes any stale IPs from the adapter
6. Assigns `192.168.137.1/24` to the adapter

### Create the task

Copy and paste this entire block into an elevated PowerShell prompt:

```powershell
# Define the script that runs every 30 seconds
$taskScript = @"
`$adapter = Get-NetAdapter | Where-Object {
    `$_.InterfaceDescription -match 'RNDIS|Remote NDIS' -and
    `$_.Status -eq 'Up'
} | Select-Object -First 1

if (-not `$adapter) { exit 0 }

`$existing = Get-NetIPAddress -InterfaceIndex `$adapter.ifIndex ``
    -IPAddress '192.168.137.1' -ErrorAction SilentlyContinue
if (`$existing) { exit 0 }

Remove-NetIPAddress -InterfaceIndex `$adapter.ifIndex ``
    -Confirm:`$false -ErrorAction SilentlyContinue
New-NetIPAddress -InterfaceIndex `$adapter.ifIndex ``
    -IPAddress '192.168.137.1' -PrefixLength 24 ``
    -ErrorAction SilentlyContinue | Out-Null
"@

# Encode for safe transport
$encodedScript = [Convert]::ToBase64String(
    [Text.Encoding]::Unicode.GetBytes($taskScript)
)

# Remove old task if it exists
$oldTask = Get-ScheduledTask -TaskName 'USBRelay-RNDIS-IPConfig' -ErrorAction SilentlyContinue
if ($oldTask) {
    Unregister-ScheduledTask -TaskName 'USBRelay-RNDIS-IPConfig' -Confirm:$false
}

# Create the task
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand $encodedScript"

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Seconds 30) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

$principal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName 'USBRelay-RNDIS-IPConfig' `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Assigns gateway IP to RNDIS adapters for USB Relay Manager'
```

### Verify

```powershell
Get-ScheduledTask -TaskName 'USBRelay-RNDIS-IPConfig' | Select-Object TaskName, State
```

Expected output:
```
TaskName                    State
--------                    -----
USBRelay-RNDIS-IPConfig     Ready
```

You can also verify in Task Scheduler (GUI):
1. Open Task Scheduler (`taskschd.msc`)
2. Navigate to Task Scheduler Library
3. Find `USBRelay-RNDIS-IPConfig`
4. Right-click → Run to test it manually

### Verify IP assignment works

1. Connect a Windows Mobile device via USB
2. Wait up to 30 seconds
3. Check the adapter IP:

```powershell
Get-NetAdapter | Where-Object {
    $_.InterfaceDescription -match 'RNDIS|Remote NDIS'
} | Get-NetIPAddress -AddressFamily IPv4
```

Expected: `192.168.137.1` with PrefixLength 24.

### Troubleshooting

- **Task shows "Running" but IP not assigned** — The task may be waiting
  for the adapter. Check that the RNDIS adapter shows as "Up" in
  `Get-NetAdapter`.
- **Task shows "Disabled"** — Right-click → Enable in Task Scheduler, or:
  `Enable-ScheduledTask -TaskName 'USBRelay-RNDIS-IPConfig'`
- **IP assigned but device can't ping gateway** — Check that the device's
  static IP is on the same subnet (`192.168.137.x`, mask `255.255.255.0`).

---

## Step 4: Create the DHCP Firewall Rule

The USB Relay Manager includes a built-in DHCP server that automatically
assigns IP configuration to connected devices.  This eliminates the need
for manual static IP setup on each device.  The DHCP server listens on
UDP port 67, which requires a Windows Firewall rule to allow inbound
traffic.

### Create the firewall rule

```powershell
New-NetFirewallRule `
    -DisplayName 'USBRelay-DHCP-Server' `
    -Description 'Allows USB Relay Manager DHCP server to configure connected devices' `
    -Direction Inbound `
    -Protocol UDP `
    -LocalPort 67 `
    -Action Allow `
    -Profile Private
```

### Verify

```powershell
Get-NetFirewallRule -DisplayName 'USBRelay-DHCP-Server' | Select-Object DisplayName, Enabled, Direction
```

Expected output:
```
DisplayName            Enabled Direction
-----------            ------- ---------
USBRelay-DHCP-Server      True   Inbound
```

### Troubleshooting

- **"Access is denied"** — You are not running as administrator.
- **DHCP not working after setup** — Verify the rule is enabled and that
  no other application is already using UDP port 67.  If the Windows
  DHCP Server role is installed, it may conflict.

---

## Step 5: Device Configuration (Automatic via DHCP)

**In most cases, no manual device configuration is needed.**  When a
device connects via USB, the USB Relay Manager's DHCP server
automatically provides:

| Setting | Value |
|---------|-------|
| IP Address | `192.168.137.2` |
| Subnet Mask | `255.255.255.0` |
| Default Gateway | `192.168.137.1` |
| DNS Server | Auto-detected from host (falls back to `8.8.8.8`) |

The device must be set to obtain its IP address automatically (DHCP),
which is the default on most Windows Mobile/CE devices.

### Fallback: Manual static IP (if DHCP is unavailable)

If the device does not support DHCP on the RNDIS adapter, or if the DHCP
server cannot start (e.g. port 67 is in use), you can still configure a
static IP manually using the settings above.

#### Windows Embedded Handheld 6.5 (e.g. Intermec CN70)

1. **Disable ActiveSync networking** (critical):
   Start → Settings → Connections → **USB to PC** →
   **uncheck** "Enable advanced network functionality"

   > If this box is checked, Windows Mobile tries to use ActiveSync/WMDC
   > networking over USB, which conflicts with IP configuration.

2. **Set to DHCP (recommended)** or assign a static IP:
   Start → Settings → Connections → **Network Cards** →
   tap the RNDIS or USB adapter → select **Use server-assigned IP address**

   For static IP: select **Use specific IP address** and enter the values
   from the table above.

#### Windows Mobile 5.0 (e.g. Intermec CN3)

1. Start → Settings → Connections → **Network Cards** tab
2. Tap the RNDIS or USB network adapter in the list
3. Select **Use server-assigned IP address** (for DHCP) or
   **Use specific IP address** (for static) and enter the values above
4. Tap OK and soft-reset if prompted

> WM 5.0 does not have a "USB to PC" toggle.  RNDIS is always active
> when the device is connected via USB.

#### Windows Mobile 2003 / Pocket PC (e.g. Intermec 700C)

1. Start → Settings → **Connections** tab → **Connections**
2. Tap **Advanced** → **Network Adapters** (or **Network Cards**)
3. Select the USB or RNDIS adapter from the list
4. Select **Use server-assigned IP address** (for DHCP) or
   **Use specific IP address** (for static) and enter the values above
5. Tap OK and soft-reset if prompted

> On WM 2003 the adapter may appear as "USB" rather than "RNDIS" in the
> list.  It is the same physical adapter — select whichever entry appears
> when the device is connected via USB.

---

## Verification Checklist

After completing all steps, verify the full chain:

```powershell
# 1. WinNAT service running
(Get-Service -Name 'winnat').Status
# Expected: Running

# 2. NAT rule exists
(Get-NetNat -Name 'USBRelayNAT').InternalIPInterfaceAddressPrefix
# Expected: 192.168.137.0/24

# 3. Scheduled task exists and ready
(Get-ScheduledTask -TaskName 'USBRelay-RNDIS-IPConfig').State
# Expected: Ready

# 4. Firewall rule exists and enabled
(Get-NetFirewallRule -DisplayName 'USBRelay-DHCP-Server').Enabled
# Expected: True

# 5. (With device connected) IP assigned
Get-NetIPAddress -InterfaceAlias '*RNDIS*','*Remote NDIS*' `
    -IPAddress '192.168.137.1' -ErrorAction SilentlyContinue
# Expected: Shows IP address entry
```

All five checks passing means standard users can launch USB Relay Manager
in Windows Mobile mode without administrator privileges.  Connected
devices will receive their IP configuration automatically via DHCP.

---

## Removing the Configuration

To undo all changes:

### Remove the firewall rule

```powershell
Remove-NetFirewallRule -DisplayName 'USBRelay-DHCP-Server'
```

### Remove the scheduled task

```powershell
Unregister-ScheduledTask -TaskName 'USBRelay-RNDIS-IPConfig' -Confirm:$false
```

### Remove the NAT rule

```powershell
Remove-NetNat -Name 'USBRelayNAT' -Confirm:$false
```

### (Optional) Stop WinNAT service

Only do this if no other applications use WinNAT (Docker, Hyper-V, WSL2
all may depend on it):

```powershell
Stop-Service -Name 'winnat'
Set-Service -Name 'winnat' -StartupType Manual
```

---

## Quick Reference

| Item | Name / Value |
|------|-------------|
| WinNAT service | `winnat` (Automatic, Running) |
| NAT rule name | `USBRelayNAT` |
| NAT subnet | `192.168.137.0/24` |
| Gateway IP (host side) | `192.168.137.1` |
| Device IP (via DHCP) | `192.168.137.2` |
| Scheduled task name | `USBRelay-RNDIS-IPConfig` |
| Task runs as | `SYSTEM` |
| Task interval | Every 30 seconds |
| Task matches adapters | `InterfaceDescription -match 'RNDIS\|Remote NDIS'` |
| Firewall rule name | `USBRelay-DHCP-Server` |
| DHCP server port | UDP 67 (inbound) |

These values must match the constants in `src/wmdc_monitor.py` and
`src/dhcp_server.py`.
