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
      4. Creates a firewall rule allowing inbound DHCP (UDP port 67) so the
         app can auto-configure connected devices — no manual static IP
         setup is needed on each device.

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
$FwRuleName   = 'USBRelay-DHCP-Server'

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
if ($Uninstall) {
    Write-Host "`n--- Removing USB Relay configuration ---`n" -ForegroundColor Yellow

    # Remove firewall rule
    $fw = Get-NetFirewallRule -DisplayName $FwRuleName -ErrorAction SilentlyContinue
    if ($fw) {
        Remove-NetFirewallRule -DisplayName $FwRuleName
        Write-Host "[OK] Firewall rule '$FwRuleName' removed." -ForegroundColor Green
    } else {
        Write-Host "[--] Firewall rule '$FwRuleName' not found (already removed)." -ForegroundColor Gray
    }

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
Write-Host "[1/4] Configuring WinNAT service..." -ForegroundColor White
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
Write-Host "[2/4] Creating NAT rule..." -ForegroundColor White
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
Write-Host "[3/4] Creating scheduled task for RNDIS IP auto-configuration..." -ForegroundColor White

$taskScript = @(
    '$adapter = Get-NetAdapter | Where-Object {'
    '    $_.InterfaceDescription -match ''RNDIS|Remote NDIS'' -and'
    '    $_.Status -eq ''Up'''
    '} | Select-Object -First 1'
    ''
    'if (-not $adapter) { exit 0 }'
    ''
    "`$existing = Get-NetIPAddress -InterfaceIndex `$adapter.ifIndex -IPAddress '$GatewayIP' -ErrorAction SilentlyContinue"
    'if ($existing) { exit 0 }'
    ''
    '# Remove any existing IPs on this adapter and assign the gateway IP'
    'Remove-NetIPAddress -InterfaceIndex $adapter.ifIndex -Confirm:$false -ErrorAction SilentlyContinue'
    "New-NetIPAddress -InterfaceIndex `$adapter.ifIndex -IPAddress '$GatewayIP' -PrefixLength $PrefixLength -ErrorAction SilentlyContinue | Out-Null"
) -join "`r`n"

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

# Trigger: run indefinitely every 1 minute
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 9999)

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

# Step 4: Firewall rule for DHCP server
#   The USB Relay Manager runs a lightweight DHCP server (UDP port 67)
#   on the RNDIS interface so that connected devices get their IP
#   configuration automatically — no manual static IP setup required.
Write-Host "[4/4] Creating firewall rule for DHCP server..." -ForegroundColor White

$existingFw = Get-NetFirewallRule -DisplayName $FwRuleName -ErrorAction SilentlyContinue
if ($existingFw) {
    Remove-NetFirewallRule -DisplayName $FwRuleName
}
New-NetFirewallRule `
    -DisplayName $FwRuleName `
    -Description 'Allows USB Relay Manager DHCP server to configure connected devices' `
    -Direction Inbound `
    -Protocol UDP `
    -LocalPort 67 `
    -Action Allow `
    -Profile Private,Public | Out-Null

Write-Host "  Firewall rule '$FwRuleName' created (inbound UDP 67)." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host "`n=== Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "  WinNAT service:  Running (Automatic)" -ForegroundColor White
Write-Host "  NAT rule:        $NatName ($SubnetPrefix)" -ForegroundColor White
Write-Host "  Scheduled task:  $TaskName (auto-assigns $GatewayIP to RNDIS)" -ForegroundColor White
Write-Host "  Firewall rule:   $FwRuleName (inbound UDP 67 for DHCP)" -ForegroundColor White
Write-Host ""
Write-Host "  Standard users can now use USBRelay.exe Windows Mobile mode" -ForegroundColor Green
Write-Host "  without needing Administrator privileges." -ForegroundColor Green
Write-Host "  Devices will receive IP configuration automatically via DHCP." -ForegroundColor Green
Write-Host ""
Write-Host "  To remove this configuration later, run:" -ForegroundColor Gray
Write-Host "    powershell -ExecutionPolicy Bypass -File setup_admin.ps1 -Uninstall" -ForegroundColor Gray
Write-Host ""
