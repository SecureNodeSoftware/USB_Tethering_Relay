#!/usr/bin/env python3
"""
USB Relay Manager - Lightweight DHCP Server

Provides automatic IP configuration to a single device connected via USB
RNDIS, eliminating the need for manual static IP setup on each device.

Implements only the minimal subset of DHCP (RFC 2131) required for a
single-client point-to-point USB link:

    DHCPDISCOVER -> DHCPOFFER
    DHCPREQUEST  -> DHCPACK

The server assigns a single fixed IP address to whichever device is
connected.  No lease tracking or address pool management is needed
because only one device can be connected over USB at a time.

Licensed under GPL v3
"""

import socket
import struct
import threading
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# DHCP protocol constants
# ---------------------------------------------------------------------------

DHCP_SERVER_PORT = 67
DHCP_CLIENT_PORT = 68
DHCP_MAGIC_COOKIE = b'\x63\x82\x53\x63'

# BOOTP op codes
BOOTREQUEST = 1
BOOTREPLY = 2

# DHCP message types (option 53)
DHCPDISCOVER = 1
DHCPOFFER = 2
DHCPREQUEST = 3
DHCPDECLINE = 4
DHCPACK = 5
DHCPNAK = 6
DHCPRELEASE = 7
DHCPINFORM = 8

_MSG_TYPE_NAMES = {
    DHCPDISCOVER: 'DISCOVER',
    DHCPOFFER: 'OFFER',
    DHCPREQUEST: 'REQUEST',
    DHCPDECLINE: 'DECLINE',
    DHCPACK: 'ACK',
    DHCPNAK: 'NAK',
    DHCPRELEASE: 'RELEASE',
    DHCPINFORM: 'INFORM',
}

# DHCP option codes
OPT_PAD = 0
OPT_SUBNET_MASK = 1
OPT_ROUTER = 3
OPT_DNS = 6
OPT_HOSTNAME = 12
OPT_REQUESTED_IP = 50
OPT_LEASE_TIME = 51
OPT_MSG_TYPE = 53
OPT_SERVER_ID = 54
OPT_RENEWAL_TIME = 58
OPT_REBINDING_TIME = 59
OPT_END = 255

# Header layout: 236 fixed bytes + 4-byte magic cookie = 240 bytes before options
DHCP_HEADER_SIZE = 236
DHCP_MIN_PACKET = DHCP_HEADER_SIZE + 4  # header + magic cookie


class DHCPServer:
    """Minimal DHCP server for single-client USB tethering.

    Binds to UDP port 67 and responds to DISCOVER/REQUEST messages with
    a fixed IP assignment.  Designed for the point-to-point RNDIS USB
    link where exactly one device is connected at a time.

    Args:
        server_ip:   IP address of the host-side RNDIS adapter (gateway).
        client_ip:   IP address to assign to the connected device.
        subnet_mask: Subnet mask for the link (default 255.255.255.0).
        dns_servers: List of DNS server IPs to advertise.
        lease_time:  DHCP lease duration in seconds (default 3600).
        on_log:      Optional ``(message, level)`` callback for log output.
    """

    def __init__(
        self,
        server_ip: str,
        client_ip: str,
        subnet_mask: str = '255.255.255.0',
        dns_servers: Optional[List[str]] = None,
        lease_time: int = 3600,
        on_log: Optional[Callable[[str, str], None]] = None,
    ):
        self.server_ip = server_ip
        self.client_ip = client_ip
        self.subnet_mask = subnet_mask
        self.dns_servers = dns_servers or ['8.8.8.8']
        self.lease_time = lease_time
        self.on_log = on_log

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None

    # -- Public API --

    def start(self):
        """Start the DHCP server in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the DHCP server and release the socket."""
        self._running = False
        # Close the socket to unblock recvfrom()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._sock = None

    def is_running(self) -> bool:
        return self._running

    # -- Main loop --

    def _run(self):
        """Bind to port 67 and serve DHCP responses until stopped."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.settimeout(2.0)
            self._sock.bind(('', DHCP_SERVER_PORT))

            self._log(
                f"DHCP server started — will assign {self.client_ip} "
                f"(gateway {self.server_ip})",
                'info',
            )

            while self._running:
                try:
                    data, addr = self._sock.recvfrom(4096)
                    self._handle_packet(data, addr)
                except socket.timeout:
                    continue
                except OSError:
                    # Socket closed by stop()
                    if self._running:
                        raise
                    break

        except OSError as e:
            self._log(f"DHCP server failed to start: {e}", 'error')
            self._log(
                "Devices will need a static IP configured manually. "
                "Check that no other DHCP server is using port 67.",
                'warning',
            )
        except Exception as e:
            self._log(f"DHCP server error: {e}", 'error')
        finally:
            self._running = False
            self._log("DHCP server stopped", 'info')

    # -- Packet handling --

    def _handle_packet(self, data: bytes, addr):
        """Parse an incoming DHCP packet and dispatch to the appropriate handler."""
        if len(data) < DHCP_MIN_PACKET:
            return

        # Must be a BOOTREQUEST
        if data[0] != BOOTREQUEST:
            return

        # Verify magic cookie
        if data[DHCP_HEADER_SIZE:DHCP_HEADER_SIZE + 4] != DHCP_MAGIC_COOKIE:
            return

        # Extract header fields we need
        xid = data[4:8]
        chaddr = data[28:44]
        options_data = data[DHCP_MIN_PACKET:]

        msg_type_raw = _parse_option(options_data, OPT_MSG_TYPE)
        if not msg_type_raw:
            return
        msg_type = msg_type_raw[0]

        client_mac = ':'.join(f'{b:02x}' for b in chaddr[:6])
        type_name = _MSG_TYPE_NAMES.get(msg_type, str(msg_type))

        if msg_type == DHCPDISCOVER:
            self._log(f"DHCP {type_name} from {client_mac}", 'info')
            self._send_offer(xid, chaddr)

        elif msg_type == DHCPREQUEST:
            # Verify the request is for us (or is a renewal with no server ID)
            requested_server = _parse_option(options_data, OPT_SERVER_ID)
            if requested_server and requested_server != socket.inet_aton(self.server_ip):
                # Request is for a different DHCP server — ignore
                return

            self._log(f"DHCP {type_name} from {client_mac}", 'info')
            self._send_ack(xid, chaddr)

        elif msg_type in (DHCPRELEASE, DHCPDECLINE):
            self._log(f"DHCP {type_name} from {client_mac}", 'info')
            # Nothing to do — we always offer the same IP

        elif msg_type == DHCPINFORM:
            # Device already has an IP, just wants config (DNS, gateway, etc.)
            self._log(f"DHCP {type_name} from {client_mac}", 'info')
            self._send_ack(xid, chaddr, inform=True)

    def _send_offer(self, xid: bytes, chaddr: bytes):
        """Respond with a DHCPOFFER."""
        packet = self._build_response(xid, chaddr, DHCPOFFER)
        self._send_broadcast(packet)
        self._log(f"DHCP OFFER sent: {self.client_ip}", 'info')

    def _send_ack(self, xid: bytes, chaddr: bytes, inform: bool = False):
        """Respond with a DHCPACK."""
        packet = self._build_response(xid, chaddr, DHCPACK, inform=inform)
        self._send_broadcast(packet)
        self._log(
            f"DHCP ACK sent: {self.client_ip} "
            f"(lease {self.lease_time}s, DNS {', '.join(self.dns_servers)})",
            'success',
        )

    # -- Packet construction --

    def _build_response(
        self, xid: bytes, chaddr: bytes, msg_type: int, inform: bool = False
    ) -> bytes:
        """Build a DHCP response packet (OFFER or ACK)."""
        header = bytearray(DHCP_HEADER_SIZE)

        # Fixed header fields
        header[0] = BOOTREPLY
        header[1] = 1   # htype: Ethernet
        header[2] = 6   # hlen: MAC address length
        header[3] = 0   # hops

        # Transaction ID (echo back)
        header[4:8] = xid

        # yiaddr — "your" IP address (skip for INFORM since device already has one)
        if not inform:
            struct.pack_into('!4s', header, 16, socket.inet_aton(self.client_ip))

        # siaddr — server IP
        struct.pack_into('!4s', header, 20, socket.inet_aton(self.server_ip))

        # chaddr — client hardware address (echo back)
        header[28:44] = chaddr

        # Build options
        options = bytearray()
        options += DHCP_MAGIC_COOKIE

        # Option 53: DHCP Message Type
        options += bytes([OPT_MSG_TYPE, 1, msg_type])

        # Option 54: Server Identifier
        options += bytes([OPT_SERVER_ID, 4])
        options += socket.inet_aton(self.server_ip)

        if not inform:
            # Option 51: Lease Time
            options += bytes([OPT_LEASE_TIME, 4])
            options += struct.pack('!I', self.lease_time)

            # Option 58: Renewal Time (T1 = lease/2)
            options += bytes([OPT_RENEWAL_TIME, 4])
            options += struct.pack('!I', self.lease_time // 2)

            # Option 59: Rebinding Time (T2 = lease * 7/8)
            options += bytes([OPT_REBINDING_TIME, 4])
            options += struct.pack('!I', self.lease_time * 7 // 8)

        # Option 1: Subnet Mask
        options += bytes([OPT_SUBNET_MASK, 4])
        options += socket.inet_aton(self.subnet_mask)

        # Option 3: Router (gateway)
        options += bytes([OPT_ROUTER, 4])
        options += socket.inet_aton(self.server_ip)

        # Option 6: DNS Servers
        dns_data = b''.join(socket.inet_aton(dns) for dns in self.dns_servers)
        options += bytes([OPT_DNS, len(dns_data)])
        options += dns_data

        # End marker
        options += bytes([OPT_END])

        return bytes(header) + bytes(options)

    def _send_broadcast(self, packet: bytes):
        """Send a DHCP response as a broadcast."""
        try:
            self._sock.sendto(packet, ('255.255.255.255', DHCP_CLIENT_PORT))
        except OSError as e:
            self._log(f"Failed to send DHCP response: {e}", 'error')

    # -- Logging --

    def _log(self, message: str, level: str = 'info'):
        if self.on_log:
            self.on_log(message, level)


# ---------------------------------------------------------------------------
# Option parsing helper
# ---------------------------------------------------------------------------

def _parse_option(options_data: bytes, option_code: int) -> Optional[bytes]:
    """Extract the value of a specific DHCP option from raw options data."""
    i = 0
    while i < len(options_data):
        code = options_data[i]
        if code == OPT_END:
            break
        if code == OPT_PAD:
            i += 1
            continue
        if i + 1 >= len(options_data):
            break
        length = options_data[i + 1]
        if i + 2 + length > len(options_data):
            break
        if code == option_code:
            return options_data[i + 2:i + 2 + length]
        i += 2 + length
    return None
