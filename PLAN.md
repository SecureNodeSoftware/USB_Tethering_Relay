# Plan: Add Windows Mobile Tethering via WinNAT

## Background

The existing TETHERING_TOOL provides USB reverse tethering for Android (CN80G) devices
using ADB reverse tunnels and gnirehtet VPN. Windows 11 dropped support for Windows
Mobile Device Center (WMDC), which previously handled USB tethering for Windows Mobile/CE
handheld scanners (Honeywell CK65, Zebra MC3300, etc.). These devices still connect via
USB using RNDIS (Remote NDIS) — they just need NAT/routing configured on the PC side.

## Approach: WinNAT (Primary) with Fallbacks

**WinNAT** (`New-NetNat`) is Windows' built-in NAT engine — the same mechanism Docker for
Windows and WSL2 use. It is more reliable and cleaner than ICS (Internet Connection Sharing).

| Aspect         | WinNAT                        | ICS (Fallback)              |
|----------------|-------------------------------|-----------------------------|
| API            | PowerShell cmdlets — reliable | COM objects (HNetCfg) — flaky |
| DHCP           | None (static IP on device)    | Built-in                    |
| Limits         | Multiple NAT networks         | Only one sharing at a time  |
| Stability      | Stable across USB reconnects  | Resets when adapter changes ports |

**NAT method fallback chain:** WinNAT → ICS → IP Forwarding (last resort)

**Network topology (USB local link only):**
```
┌─────────────────┐    USB RNDIS (local link)    ┌─────────────────┐
│  WinMobile/CE   │◄───────────────────────────►│  Host PC        │
│  Scanner        │                              │                 │
│                 │   Static IP: 192.168.137.2   │  192.168.137.1  │
│                 │   Gateway:   192.168.137.1   │  (NAT gateway)  │
│                 │   DNS:       8.8.8.8         │                 │
└─────────────────┘                              └────────┬────────┘
                                                          │
                                                     NAT translates
                                                          │
                                                          ▼
                                                   Internet / LAN
```

---

## Implementation Steps

### Step 1: Create `src/wmdc_monitor.py` — Windows Mobile Device Monitor

New module (~300 lines) that replaces WMDC's tethering functionality. Key responsibilities:

**1a. RNDIS Adapter Detection**
- Poll `Get-NetAdapter` via PowerShell every 2 seconds to detect RNDIS USB network adapters
- Filter by adapter description containing "RNDIS" or "Remote NDIS"
- Detect connect/disconnect events by tracking known adapter set

**1b. Automatic IP Configuration**
- When RNDIS adapter detected, configure it with static IP `192.168.137.1/24` via
  `New-NetIPAddress` PowerShell cmdlet
- This makes the PC the gateway for the local USB link

**1c. WinNAT Setup (Primary Method)**
- Create NAT network: `New-NetNat -Name "USBRelayNAT" -InternalIPInterfaceAddressPrefix "192.168.137.0/24"`
- Clean up any existing NAT with the same name first to avoid conflicts
- Log success/failure

**1d. ICS Fallback**
- If WinNAT fails (e.g., conflict with Docker/WSL NAT, older Windows builds), fall back to
  ICS via the `HNetCfg.HNetShare` COM library
- Enumerate network connections, find the internet-connected adapter, enable sharing to
  the RNDIS adapter
- Disable any existing ICS first to avoid "only one sharing" conflicts

**1e. IP Forwarding Fallback (Last Resort)**
- If both WinNAT and ICS fail, enable `Set-NetIPInterface -Forwarding Enabled` on both
  the internet adapter and the RNDIS adapter
- This provides basic routing without NAT (requires upstream router to have return route)

**1f. Cleanup on Disconnect**
- When RNDIS adapter disappears, remove whichever NAT method was active:
  - WinNAT: `Remove-NetNat -Name "USBRelayNAT" -Confirm:$false`
  - ICS: Disable sharing via COM
  - IP Forwarding: `Set-NetIPInterface -Forwarding Disabled`

**1g. Class Interface**
```python
class WMDCMonitor:
    def __init__(self, on_device_connected, on_device_disconnected, on_log, poll_interval=2.0)
    def start(self)        # Begin monitoring thread
    def stop(self)         # Stop monitoring, clean up NAT
    def is_running(self)   # Check monitor state
```

### Step 2: Modify `src/gui.py` — Add Device Mode Selector

**2a. Add Mode Radio Buttons**
- Add a radio button selector between the buttons and the status row:
  - "Android (CN80G)" — existing ADB/gnirehtet mode
  - "Windows Mobile" — new RNDIS/WinNAT mode
- Default to "Android" to preserve existing behavior
- Store selection in a `tk.StringVar` (`device_mode`)

**2b. Import and Initialize WMDCMonitor**
- In `_setup_managers()`, conditionally import `wmdc_monitor` (Windows-only)
- Initialize `self.wmdc_monitor = None` on non-Windows or when WMDCMonitor import fails
- Platform-gate: only show "Windows Mobile" radio option on Windows (`sys.platform == 'win32'`)

**2c. Update Start/Stop Handlers**
- `_on_start()`: Check `device_mode` value
  - Android: start `adb_monitor` + `relay_manager` (existing behavior)
  - Windows Mobile: start `wmdc_monitor` only (no gnirehtet relay needed)
- `_on_stop()`: Stop whichever mode is active

**2d. Add Mode Switch Handler**
- `_on_mode_change()`: When user switches mode while running, stop current mode,
  start new mode
- Update status indicators and log messages to reflect mode

**2e. Update Connection Callbacks**
- `_on_device_connected()` / `_on_device_disconnected()` already generic enough
- WMDCMonitor will pass adapter name as device_id (e.g., "RNDIS Adapter")

**2f. Update Close Handler**
- `_on_close()`: Also stop `wmdc_monitor` if active

### Step 3: Update `src/main.py` — No Changes Expected

The main.py entry point doesn't need changes since WMDCMonitor initialization is handled
inside gui.py's `_setup_managers()`. The existing resource extraction flow remains the same
(WinNAT uses only built-in Windows PowerShell cmdlets — no additional binaries needed).

### Step 4: Update `README.md`

**4a. Overview Section**
- Add Windows Mobile/CE to the supported device list
- Add second architecture diagram showing the RNDIS/WinNAT path

**4b. Quick Start — Windows Mobile Mode**
- Document the "Windows Mobile" radio button in the GUI
- Note the Administrator requirement
- Document required device-side static IP configuration:
  - IP: `192.168.137.2`
  - Subnet: `255.255.255.0`
  - Gateway: `192.168.137.1`
  - DNS: `8.8.8.8` (or match host)

**4c. Troubleshooting — Windows Mobile Section**
- "RNDIS adapter not detected" — check device driver, USB connection
- "WinNAT failed" — check for Docker/WSL conflicts, try `Get-NetNat` to see existing NATs
- "ICS failed" — run as Administrator, check Windows Network Connections
- "Device connected but no internet" — verify static IP on device, check PC has internet

**4d. Technical Details**
- Add Windows Mobile mode parameters:
  - NAT Method: WinNAT (primary) → ICS → IP Forwarding
  - Subnet: `192.168.137.0/24`
  - PC Gateway: `192.168.137.1`
  - Device IP: `192.168.137.2` (static)
  - Requires: Run as Administrator
  - Platform: Windows only

### Step 5: Update Build Configuration

**5a. `USBRelay.spec`**
- Add `wmdc_monitor` to `hiddenimports` list

**5b. `build.py`**
- No changes needed (wmdc_monitor.py is auto-discovered as a Python module)

### Step 6: Update Project Structure in README

Add `wmdc_monitor.py` to the project structure listing:
```
src/
├── main.py           # Entry point
├── gui.py            # Tkinter GUI (Android + Windows Mobile modes)
├── relay_manager.py  # Gnirehtet relay subprocess manager
├── adb_monitor.py    # ADB device detection and tunnel setup
└── wmdc_monitor.py   # Windows Mobile RNDIS detection + WinNAT/ICS config
```

---

## Key Design Decisions

1. **WinNAT first, not ICS** — WinNAT is more reliable, doesn't suffer from COM reference
   staleness when RNDIS adapters change USB ports, and coexists better with other network
   configurations.

2. **Static IP on device** — WinNAT doesn't include DHCP. Enterprise scanners (CK65,
   MC9300) are fleet-managed with static profiles, so this is acceptable. The ICS fallback
   does provide DHCP if WinNAT fails.

3. **Windows-only** — Windows Mobile/CE RNDIS tethering only applies to Windows PCs. The
   mode selector is platform-gated so macOS users see only the Android option.

4. **No additional binaries** — WinNAT and ICS both use built-in Windows tools (PowerShell
   cmdlets and COM objects). No new resources need to be bundled.

5. **Same GUI, radio toggle** — Both modes share the same window, status indicators, and
   log panel. A simple radio button switches between them. This keeps the tool unified.

6. **Administrator requirement** — Configuring WinNAT/ICS/IP forwarding requires elevated
   privileges. The tool should log a clear message if it detects it's not running as admin
   when Windows Mobile mode is selected.

---

## Testing

Testing uses **Microsoft Device Emulator 3.0** to emulate Windows Mobile/CE devices with
RNDIS connectivity on a Windows 10/11 PC. No physical device is required for initial
validation.

**Emulator testing covers:**
- RNDIS adapter detection (emulator presents as RNDIS USB adapter to host)
- WinNAT creation and cleanup
- ICS fallback path (disable WinNAT first to force fallback)
- IP Forwarding fallback path
- Mode switching between Android and Windows Mobile in the GUI
- Disconnect/reconnect cycles

**Requirements:**
- Windows 10/11 PC running as Administrator
- Microsoft Device Emulator 3.0 installed
- If Docker Desktop or WSL2 is active, test the WinNAT conflict → ICS fallback path

**Final validation** should be performed on physical hardware (CK65, MC3300, etc.) once
emulator testing passes.

---

## Files Changed

| File | Action | Description |
|------|--------|-------------|
| `src/wmdc_monitor.py` | **CREATE** | RNDIS detection + WinNAT/ICS/forwarding NAT config |
| `src/gui.py` | **MODIFY** | Add mode selector, WMDCMonitor integration |
| `README.md` | **MODIFY** | Add Windows Mobile docs, architecture, troubleshooting |
| `USBRelay.spec` | **MODIFY** | Add `wmdc_monitor` to hiddenimports |

## Files Unchanged

| File | Reason |
|------|--------|
| `src/main.py` | WMDCMonitor init handled in gui.py |
| `src/relay_manager.py` | Only used by Android mode, no changes |
| `src/adb_monitor.py` | Only used by Android mode, no changes |
| `build.py` | Auto-discovers Python modules |
| `USBRelay.macos.spec` | Windows Mobile is Windows-only |
