# Plan: Consolidate Tethering Paths & Eliminate Runtime Admin Requirements

## Problem Statement

The two tethering modes currently have completely separate data paths:
- **Android**: ADB reverse tunnel → gnirehtet relay (userspace proxy, zero admin)
- **Windows Mobile**: RNDIS NIC → OS-level NAT via WinNAT/ICS (requires admin)

The Windows Mobile path needs admin privileges for **four** operations:
1. **IP assignment** (`New-NetIPAddress`) — assign 192.168.137.1 to RNDIS adapter
2. **NAT creation** (`New-NetNat`) — kernel-level packet rewriting
3. **ICS** (`HNetCfg.HNetShare`) — COM-based network sharing
4. **IP Forwarding** (`Set-NetIPInterface -Forwarding`) — kernel routing

Goal: eliminate admin at **runtime** and share as much infrastructure as
possible between the two modes.

### What "Zero Admin" Actually Means

**Full zero-admin is not achievable on Windows for RNDIS.** The OS requires
elevated privileges to assign an IP address to a network adapter — there is
no workaround (no userspace alternative, no `netsh` trick, no registry hack).

What IS achievable:
- **One-time admin setup** (already exists: `setup_admin.ps1`) handles
  IP assignment via a SYSTEM scheduled task — runs once, works forever
- **Zero admin at runtime** — the app itself never needs elevation
- **Eliminate runtime NAT admin** — replace WinNAT/ICS/forwarding with a
  userspace proxy that needs no privileges

This means: keep `setup_admin.ps1` for the scheduled task (IP assignment),
but **delete all the WinNAT/ICS/IP-Forwarding code** from `wmdc_monitor.py`
and replace it with a SOCKS5 proxy.

---

## How Gnirehtet Works (Reference)

Gnirehtet's relay is a **userspace TCP/IP proxy**, not a NAT. It:
1. Receives raw IPv4 packets over a TCP connection
2. Parses IP/TCP/UDP headers (`ipv4_packet_buffer.rs`)
3. Opens **real sockets** on the host on the device's behalf (`router.rs`)
4. Relays data back

The host OS never sees foreign source IPs. No kernel NAT, no admin.

### Wire Protocol (for future raw-bridge work only)

```
Client connects to relay on TCP port 31416.
Relay → Client:  4 bytes (big-endian u32 client ID)
Client → Relay:  stream of raw IPv4 packets, concatenated
Relay → Client:  stream of raw IPv4 packets, concatenated

Packet framing: no length prefix. The relay reads the IPv4 header's
"total length" field (bytes 2-3, big-endian u16) to determine packet
boundaries. See vendor/gnirehtet-relay-rust/src/relay/ipv4_packet_buffer.rs:40-55.

Supported transport: TCP and UDP only. Other protocols (ICMP etc.) are dropped.
```

This protocol requires a VPN-like client on the device to capture and
tunnel raw IP traffic — not practical for enterprise scanners with
locked-down software. Hence the proxy approach below.

---

## Architecture

```
ANDROID (unchanged — uses gnirehtet relay):

  Phone VPN ──ADB reverse──▶ relay (port 31416) ──▶ real sockets ──▶ internet
              tunnel          parses raw IPv4         opened as host


WINDOWS MOBILE (new — uses SOCKS5 proxy, separate from relay):

  Scanner ──RNDIS USB──▶ SOCKS5 proxy (port 1080) ──▶ real sockets ──▶ internet
            192.168.137.x    on 192.168.137.1            opened as host
            (device IP)      (host gateway)

                         DNS relay (port 53) ──▶ upstream DNS ──▶ response
```

**These are two parallel paths, NOT a unified relay.** The gnirehtet relay
speaks a raw-IPv4-over-TCP wire protocol that only the Android VPN client
implements. The SOCKS5 proxy is a separate, simpler mechanism that works
with standard network stacks — no VPN client needed on the device.

The shared infrastructure is: device monitoring base class, DNS resolution,
subprocess utilities, GUI callbacks, and logging.

---

## Implementation Steps

### Step 1: Extract Common `DeviceMonitor` Base Class

Create `src/device_monitor.py` with shared infrastructure currently
duplicated across `adb_monitor.py`, `wmdc_monitor.py`, and `relay_manager.py`.

```python
import subprocess
import sys
import threading
import time
import re
from pathlib import Path
from typing import Callable, Optional, List

IS_WINDOWS = sys.platform == 'win32'

def subprocess_kwargs():
    """Platform-specific subprocess keyword arguments.
    Currently duplicated in adb_monitor.py, wmdc_monitor.py, relay_manager.py.
    """
    kwargs = {}
    if IS_WINDOWS and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs

def get_system_dns_servers() -> List[str]:
    """Get DNS servers configured on the host system.
    Currently only in adb_monitor.py — both modes need this.
    """
    ...  # Move existing implementation from adb_monitor.py

class DeviceMonitor:
    """Base class for USB device monitors."""

    def __init__(
        self,
        on_device_connected: Optional[Callable[[str], None]] = None,
        on_device_disconnected: Optional[Callable[[], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        poll_interval: float = 2.0
    ):
        self._on_connected_cb = on_device_connected
        self._on_disconnected_cb = on_device_disconnected
        self._on_log_cb = on_log
        self._poll_interval = poll_interval
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._current_device: Optional[str] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop(self):
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        if self._current_device:
            self._on_device_lost(self._current_device)
            self._current_device = None

    def is_running(self) -> bool:
        return self._running

    def _monitor_loop(self):
        while self._running:
            try:
                device_id = self._detect_device()
                if device_id and not self._current_device:
                    self._current_device = device_id
                    self._on_device_found(device_id)
                    if self._on_connected_cb:
                        self._on_connected_cb(device_id)
                elif not device_id and self._current_device:
                    old = self._current_device
                    self._current_device = None
                    self._on_device_lost(old)
                    if self._on_disconnected_cb:
                        self._on_disconnected_cb()
            except Exception as e:
                self._log(f"Monitor error: {e}", 'error')
            time.sleep(self._poll_interval)

    # -- Subclass hooks (override these) --
    def _detect_device(self) -> Optional[str]:
        raise NotImplementedError
    def _on_device_found(self, device_id: str):
        pass
    def _on_device_lost(self, device_id: str):
        pass

    def _log(self, message: str, level: str = 'info'):
        if self._on_log_cb:
            self._on_log_cb(message, level)
```

### Step 2: Refactor `ADBMonitor` to Inherit from `DeviceMonitor`

```python
from device_monitor import DeviceMonitor, subprocess_kwargs, get_system_dns_servers

RELAY_PORT = 31416

class ADBMonitor(DeviceMonitor):
    def __init__(self, adb_path: Path, **kwargs):
        super().__init__(**kwargs)
        self.adb_path = adb_path
        self._known_devices: Set[str] = set()

    def stop(self, kill_server: bool = True):
        super().stop()
        if kill_server:
            self._kill_adb_server()

    def _detect_device(self) -> Optional[str]:
        # Parse `adb devices`, return first device ID or None
        ...  # Move existing _get_connected_devices logic here

    def _on_device_found(self, device_id: str):
        self._log(f"Device detected: {device_id}", 'info')
        self._setup_reverse_tunnel(device_id)
        self._start_usb_relay_on_device(device_id)

    def _on_device_lost(self, device_id: str):
        self._log(f"Device disconnected: {device_id}", 'warning')

    # Keep existing private methods unchanged:
    # _setup_reverse_tunnel, _start_usb_relay_on_device, _kill_adb_server
```

**Note on multi-device**: `ADBMonitor` currently tracks a set of devices
but only acts on one. Base class tracks single device. This matches
current behavior. Multi-device can be added to the base class later.

### Step 3: Create `src/rndis_proxy.py` — SOCKS5 Proxy + DNS Relay

Pure-Python, zero-dependency, zero-admin. This replaces ALL the
WinNAT/ICS/IP-Forwarding code (~250 lines deleted).

```python
import socket
import selectors
import struct
import threading
from typing import Optional, Callable, List

class SOCKS5Proxy:
    """SOCKS5 proxy for RNDIS-connected devices.

    Binds to the RNDIS adapter IP on a configurable port (default 1080).
    No admin required — only uses high ports.
    """

    def __init__(self, bind_ip: str, bind_port: int = 1080,
                 on_log: Optional[Callable] = None):
        self._bind_ip = bind_ip
        self._bind_port = bind_port
        self._on_log = on_log
        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Start proxy. Returns True on success."""
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self._bind_ip, self._bind_port))
            self._server_sock.listen(32)
            self._running = True
            self._thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._thread.start()
            return True
        except OSError as e:
            if self._on_log:
                self._on_log(f"Proxy bind failed: {e}", 'error')
            return False

    def stop(self):
        self._running = False
        if self._server_sock:
            self._server_sock.close()
        if self._thread:
            self._thread.join(timeout=3)

    def _accept_loop(self):
        """Accept SOCKS5 connections and spawn handler threads."""
        while self._running:
            try:
                client_sock, addr = self._server_sock.accept()
                threading.Thread(
                    target=self._handle_client,
                    args=(client_sock,),
                    daemon=True
                ).start()
            except OSError:
                break  # Socket closed

    def _handle_client(self, client_sock: socket.socket):
        """SOCKS5 CONNECT handshake then bidirectional relay.

        1. Client: 0x05, n_methods, methods...
        2. Server: 0x05, 0x00 (no auth)
        3. Client: 0x05, 0x01(CONNECT), 0x00, addr_type, dest_addr, dest_port
        4. Server: connect to dest, reply success/fail
        5. Relay data bidirectionally
        """
        ...

    def _relay(self, sock_a: socket.socket, sock_b: socket.socket):
        """Bidirectional relay using selectors for efficiency."""
        ...


class DNSRelay:
    """UDP DNS relay for RNDIS devices.

    Listens on the RNDIS adapter IP for DNS queries and forwards
    to the host's configured upstream DNS server.
    """

    def __init__(self, bind_ip: str, bind_port: int = 53,
                 upstream_dns: str = '8.8.8.8',
                 on_log: Optional[Callable] = None):
        self._bind_ip = bind_ip
        self._bind_port = bind_port
        self._upstream_dns = upstream_dns
        self._on_log = on_log
        self._running = False

    def start(self) -> bool:
        """Start DNS relay. Falls back to port 5353 if 53 is taken."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.bind((self._bind_ip, self._bind_port))
        except OSError:
            # Port 53 may be taken by Windows DNS Client service
            if self._bind_port == 53:
                self._bind_port = 5353
                try:
                    self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self._sock.bind((self._bind_ip, self._bind_port))
                except OSError as e:
                    if self._on_log:
                        self._on_log(f"DNS relay failed on both 53 and 5353: {e}", 'error')
                    return False
            else:
                return False
        self._running = True
        self._thread = threading.Thread(target=self._relay_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if hasattr(self, '_sock'):
            self._sock.close()

    def _relay_loop(self):
        """Receive DNS queries, forward to upstream, return responses."""
        ...
```

**DNS port 53 on Windows**: Unlike Unix, Windows does NOT enforce privileged
port restrictions for ports < 1024. A non-admin process CAN bind to port 53.
However, the Windows DNS Client service may already occupy it. The code
attempts 53 first, falls back to 5353.

### Step 4: Refactor `WMDCMonitor` — Replace NAT with Proxy

```python
from device_monitor import DeviceMonitor, subprocess_kwargs, get_system_dns_servers
from rndis_proxy import SOCKS5Proxy, DNSRelay

GATEWAY_IP = "192.168.137.1"
PROXY_PORT = 1080

class WMDCMonitor(DeviceMonitor):
    def __init__(self, proxy_port: int = PROXY_PORT, **kwargs):
        super().__init__(**kwargs)
        self._proxy: Optional[SOCKS5Proxy] = None
        self._dns_relay: Optional[DNSRelay] = None
        self._proxy_port = proxy_port

    def start(self):
        if not IS_WINDOWS:
            self._log("Windows Mobile mode is only available on Windows", 'error')
            return
        # No admin check needed — proxy works for all users
        super().start()
        self._log("Windows Mobile device monitoring started", 'info')

    def _detect_device(self) -> Optional[str]:
        # Existing: poll Get-NetAdapter for RNDIS + Up
        ...  # Keep existing _find_rndis_adapter logic

    def _on_device_found(self, adapter_name: str):
        self._log(f"RNDIS adapter detected: {adapter_name}", 'success')

        # Wait for IP (assigned by setup_admin.ps1 scheduled task)
        if not self._wait_for_adapter_ip(adapter_name):
            self._log(
                f"Gateway IP {GATEWAY_IP} not assigned to {adapter_name}. "
                "Run setup_admin.ps1 (one-time) or assign IP manually.",
                'error'
            )
            return

        # Start SOCKS5 proxy on RNDIS interface
        self._proxy = SOCKS5Proxy(
            bind_ip=GATEWAY_IP,
            bind_port=self._proxy_port,
            on_log=self._log
        )
        if not self._proxy.start():
            self._log("Failed to start proxy", 'error')
            return

        # Start DNS relay
        dns_servers = get_system_dns_servers()
        self._dns_relay = DNSRelay(
            bind_ip=GATEWAY_IP,
            upstream_dns=dns_servers[0],
            on_log=self._log
        )
        self._dns_relay.start()  # Non-fatal if this fails

        self._log(
            f"Proxy ready — configure device proxy: {GATEWAY_IP}:{self._proxy_port}",
            'success'
        )

    def _on_device_lost(self, adapter_name: str):
        self._log(f"RNDIS adapter disconnected: {adapter_name}", 'warning')
        if self._proxy:
            self._proxy.stop()
            self._proxy = None
        if self._dns_relay:
            self._dns_relay.stop()
            self._dns_relay = None

    def _wait_for_adapter_ip(self, adapter_name, timeout=15.0) -> bool:
        # Keep existing implementation — polls for IP assigned by scheduled task
        ...
```

**Deleted code** (~250 lines): `_setup_winnat`, `_remove_winnat`,
`_ensure_winnat_service`, `_setup_ics`, `_disable_ics`,
`_setup_ip_forwarding`, `_disable_ip_forwarding`, `_is_admin`,
`_check_preconfiguration`, `_verify_nat_exists`, `_cleanup_nat`,
and the entire admin/non-admin branching in `_on_adapter_connected`.

### Step 5: Simplify `setup_admin.ps1`

Remove WinNAT steps. The script's only remaining job: register the
SYSTEM scheduled task for RNDIS IP assignment.

**Delete**:
- Step 1 (WinNAT service configuration) — no longer used
- Step 2 (NAT rule creation) — no longer used
- Uninstall: remove WinNAT/NAT cleanup

**Keep**:
- Step 3 (scheduled task) — still needed for IP assignment
- Uninstall: scheduled task removal

Script shrinks from ~177 lines to ~60 lines.

### Step 6: Update GUI

```python
def _on_start(self):
    mode = self.device_mode.get()
    if mode == 'winmobile':
        # Proxy-only — no gnirehtet relay needed
        self.wmdc_monitor.start()
        self.update_status('waiting')
    else:
        # ADB + gnirehtet relay
        self.adb_monitor.start()
        self.relay_manager.start()
    self._active_mode = mode

def _on_stop(self):
    if self._active_mode == 'winmobile':
        self.wmdc_monitor.stop()
    else:
        self.relay_manager.stop()
        self.adb_monitor.stop()
```

Key change: Windows Mobile mode does NOT start the gnirehtet relay.
The SOCKS5 proxy is self-contained and independent.

---

## What `setup_admin.ps1` Still Does (One-Time)

| Task | Why Admin? | Eliminable? |
|------|-----------|-------------|
| SYSTEM scheduled task (IP assignment) | `New-NetIPAddress` requires admin | **No** — Windows API limitation |
| ~~WinNAT service~~ | ~~`Start-Service`~~ | **Eliminated** |
| ~~NAT rule~~ | ~~`New-NetNat`~~ | **Eliminated** |

**Bottom line**: Admin runs the script ONCE. After that, every app
launch is zero-admin. The scheduled task runs as SYSTEM and assigns
the gateway IP within seconds of the RNDIS adapter appearing.

---

## Device-Side Configuration

### Android (CN80G) — No Changes
- SCAN Mobile app with built-in gnirehtet VPN (already deployed)

### Windows Mobile (CK65, MC3300, etc.) — Updated

| Setting | Value | Changed? |
|---------|-------|----------|
| IP Address | `192.168.137.2` | No (same as before) |
| Subnet Mask | `255.255.255.0` | No |
| Gateway | `192.168.137.1` | No |
| DNS | `192.168.137.1` | No |
| **Proxy** | **`192.168.137.1:1080`** | **NEW — SOCKS5** |

The proxy setting can be pushed via MDM (Avalanche, SOTI, StageNow)
or configured manually in the device network settings.

---

## Files Changed

| File | Action | Description |
|------|--------|-------------|
| `src/device_monitor.py` | **CREATE** | Base class + shared utilities (`subprocess_kwargs`, `get_system_dns_servers`, `IS_WINDOWS`) |
| `src/rndis_proxy.py` | **CREATE** | SOCKS5 proxy + DNS relay (~200 lines) |
| `src/adb_monitor.py` | **MODIFY** | Inherit from `DeviceMonitor`, delete duplicated code |
| `src/wmdc_monitor.py` | **MODIFY** | Inherit from `DeviceMonitor`, replace NAT with proxy (~250 lines deleted, ~30 added) |
| `src/relay_manager.py` | **MODIFY** | Import shared `subprocess_kwargs` from `device_monitor` |
| `src/gui.py` | **MODIFY** | WinMobile start/stop simplified (no relay needed) |
| `setup_admin.ps1` | **MODIFY** | Remove WinNAT/NAT steps, keep scheduled task only |

### Files Unchanged

| File | Reason |
|------|--------|
| `src/main.py` | Resource extraction unchanged |
| `vendor/gnirehtet-relay-rust/` | Relay binary untouched |
| `resources/` | No new binaries needed |

---

## Testing Strategy

### Unit Tests
1. `DeviceMonitor` base class — mock `_detect_device`, verify callback dispatch
2. `SOCKS5Proxy` — test client connects, CONNECT handshake, data relay
3. `DNSRelay` — send UDP DNS query, verify forwarded and response returned

### Integration Tests
4. `ADBMonitor` regression — existing Android flow unchanged
5. `WMDCMonitor` with mock RNDIS adapter — verify proxy starts on detection
6. Proxy end-to-end: `curl --proxy socks5://192.168.137.1:1080 http://example.com`

### Device Tests (Physical Hardware)
7. Android (CN80G) — full end-to-end, verify no regression
8. Windows Mobile (CK65) — configure SOCKS5 proxy, verify HTTP
9. Windows Mobile (MC3300) — same, different model
10. DNS — `nslookup` through the relay
11. Disconnect/reconnect — proxy restarts cleanly

### Edge Cases
12. Port conflict on 1080 — graceful error message
13. DNS port 53 occupied — fallback to 5353
14. No scheduled task installed — clear error (not silent failure)
15. Multiple RNDIS adapters — first-detected wins

---

## Open Questions (Must Resolve Before Implementation)

1. **Do CK65/MC3300 support SOCKS5 proxy config?**
   Verify on physical hardware. If only HTTP proxy is supported,
   implement HTTP CONNECT proxy instead (simpler protocol, TCP-only,
   same concept). This changes `rndis_proxy.py` but not the architecture.

2. **DNS port 53 availability**: Test whether binding to port 53 on the
   RNDIS adapter IP works without admin on target Windows versions
   (10/11). If the DNS Client service blocks it, the device must support
   custom DNS port, or DNS must route through the SOCKS5 proxy (some
   apps resolve DNS through the proxy via SOCKS5 domain-name mode).

3. **UDP beyond DNS**: If scanners use UDP for anything other than DNS
   (SNMP, syslog, NTP), the SOCKS5 proxy won't cover it without
   UDP ASSOCIATE support. Audit scanner traffic to determine if this
   matters. If needed, UDP ASSOCIATE can be added to the proxy.
