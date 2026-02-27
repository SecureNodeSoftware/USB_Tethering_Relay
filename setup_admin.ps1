#Requires -RunAsAdministrator
<#
.SYNOPSIS
    One-time administrator setup for USB Relay Manager (Windows Mobile mode).

.DESCRIPTION
    This script must be run ONCE by an administrator before standard users
    can use the Windows Mobile tethering feature.  It:

      1. Ensures the WinNAT service is running and set to start automatically.
      2. Creates a persistent NAT rule for the RNDIS USB subnet.
      3. Creates a scheduled task (runs as SYSTEM) that automatically assigns
         the gateway IP to any RNDIS adapter when it connects.

    After this script runs, standard (non-admin) users can launch
    USBRelay.exe and use the Windows Mobile tethering mode without needing
    any elevated privileges.

.NOTES
    Run from an elevated PowerShell prompt:
        powershell -ExecutionPolicy Bypass -File setup_admin.ps1

    To undo all changes:
        powershell -ExecutionPolicy Bypass -File setup_admin.ps1 -Uninstall
#>

param(
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

# Must match the values in wmdc_monitor.py
$SubnetPrefix = '192.168.137.0/24'
$GatewayIP    = '192.168.137.1'
$PrefixLength = 24
$NatName      = 'USBRelayNAT'
$TaskName     = 'USBRelay-RNDIS-IPConfig'

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
if ($Uninstall) {
    Write-Host "`n--- Removing USB Relay configuration ---`n" -ForegroundColor Yellow

    # Remove scheduled task
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[OK] Scheduled task '$TaskName' removed." -ForegroundColor Green
    } else {
        Write-Host "[--] Scheduled task '$TaskName' not found (already removed)." -ForegroundColor Gray
    }

    # Remove NAT rule
    $nat = Get-NetNat -Name $NatName -ErrorAction SilentlyContinue
    if ($nat) {
        Remove-NetNat -Name $NatName -Confirm:$false
        Write-Host "[OK] NAT rule '$NatName' removed." -ForegroundColor Green
    } else {
        Write-Host "[--] NAT rule '$NatName' not found (already removed)." -ForegroundColor Gray
    }

    Write-Host "`nUninstall complete.`n" -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
Write-Host "`n=== USB Relay Manager - Administrator Setup ===`n" -ForegroundColor Cyan

# Step 1: WinNAT service
Write-Host "[1/3] Configuring WinNAT service..." -ForegroundColor White
$svc = Get-Service -Name 'winnat' -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "  ERROR: WinNAT service not found. This Windows edition may not support it." -ForegroundColor Red
    exit 1
}
if ($svc.StartType -ne 'Automatic') {
    Set-Service -Name 'winnat' -StartupType Automatic
    Write-Host "  Set WinNAT startup type to Automatic." -ForegroundColor Green
}
if ($svc.Status -ne 'Running') {
    Start-Service -Name 'winnat'
    Write-Host "  Started WinNAT service." -ForegroundColor Green
} else {
    Write-Host "  WinNAT service is already running." -ForegroundColor Green
}

# Step 2: NAT rule
Write-Host "[2/3] Creating NAT rule..." -ForegroundColor White
$existing = Get-NetNat -Name $NatName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  NAT rule '$NatName' already exists — removing stale rule." -ForegroundColor Yellow
    Remove-NetNat -Name $NatName -Confirm:$false
}
New-NetNat -Name $NatName -InternalIPInterfaceAddressPrefix $SubnetPrefix | Out-Null
Write-Host "  Created NAT rule: $NatName ($SubnetPrefix)" -ForegroundColor Green

# Step 3: Scheduled task for RNDIS IP assignment
#   This task runs as SYSTEM and is triggered by a network adapter event.
#   It checks for an RNDIS adapter without the correct IP and assigns it.
Write-Host "[3/3] Creating scheduled task for RNDIS IP auto-configuration..." -ForegroundColor White

$taskScript = @"
`$adapter = Get-NetAdapter | Where-Object {
    `$_.InterfaceDescription -match 'RNDIS|Remote NDIS' -and
    `$_.Status -eq 'Up'
} | Select-Object -First 1

if (-not `$adapter) { exit 0 }

`$existing = Get-NetIPAddress -InterfaceIndex `$adapter.ifIndex -IPAddress '$GatewayIP' -ErrorAction SilentlyContinue
if (`$existing) { exit 0 }

# Remove any existing IPs on this adapter and assign the gateway IP
Remove-NetIPAddress -InterfaceIndex `$adapter.ifIndex -Confirm:`$false -ErrorAction SilentlyContinue
New-NetIPAddress -InterfaceIndex `$adapter.ifIndex -IPAddress '$GatewayIP' -PrefixLength $PrefixLength -ErrorAction SilentlyContinue | Out-Null
"@

$encodedScript = [Convert]::ToBase64String(
    [Text.Encoding]::Unicode.GetBytes($taskScript)
)

# Remove old task if it exists
$oldTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($oldTask) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action  = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand $encodedScript"

# Trigger: run every 30 seconds (catches plug-in events reliably)
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
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Assigns gateway IP to RNDIS adapters for USB Relay Manager' | Out-Null

Write-Host "  Scheduled task '$TaskName' created (runs as SYSTEM)." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host "`n=== Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "  WinNAT service:  Running (Automatic)" -ForegroundColor White
Write-Host "  NAT rule:        $NatName ($SubnetPrefix)" -ForegroundColor White
Write-Host "  Scheduled task:  $TaskName (auto-assigns $GatewayIP to RNDIS)" -ForegroundColor White
Write-Host ""
Write-Host "  Standard users can now use USBRelay.exe Windows Mobile mode" -ForegroundColor Green
Write-Host "  without needing Administrator privileges." -ForegroundColor Green
Write-Host ""
Write-Host "  To remove this configuration later, run:" -ForegroundColor Gray
Write-Host "    powershell -ExecutionPolicy Bypass -File setup_admin.ps1 -Uninstall" -ForegroundColor Gray
Write-Host ""
