#!/usr/bin/env python3
"""
DHCP Client Simulator

Simulates the DHCP handshake that a Windows Mobile device performs when
it connects via USB RNDIS to the tethering tool.  Implements the
client side of the DISCOVER -> OFFER -> REQUEST -> ACK exchange.

Can operate in two modes:

  1. **Loopback mode** — Communicates with a DHCPServer instance running
     in the same process on localhost (for automated testing without
     hardware).

  2. **Network mode** — Sends real DHCP broadcast packets on a specified
     interface (for integration testing with an actual RNDIS adapter or
     virtual network adapter).

Licensed under GPL v3
"""

import os
import random
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# DHCP protocol constants (mirrors dhcp_server.py)
# ---------------------------------------------------------------------------

DHCP_SERVER_PORT = 67
DHCP_CLIENT_PORT = 68
DHCP_MAGIC_COOKIE = b'\x63\x82\x53\x63'

BOOTREQUEST = 1
BOOTREPLY = 2

DHCPDISCOVER = 1
DHCPOFFER = 2
DHCPREQUEST = 3
DHCPACK = 5
DHCPNAK = 6

OPT_PAD = 0
OPT_SUBNET_MASK = 1
OPT_ROUTER = 3
OPT_DNS = 6
OPT_REQUESTED_IP = 50
OPT_LEASE_TIME = 51
OPT_MSG_TYPE = 53
OPT_SERVER_ID = 54
OPT_END = 255

DHCP_HEADER_SIZE = 236
DHCP_MIN_PACKET = DHCP_HEADER_SIZE + 4

_MSG_TYPE_NAMES = {
    DHCPDISCOVER: 'DISCOVER',
    DHCPOFFER: 'OFFER',
    DHCPREQUEST: 'REQUEST',
    DHCPACK: 'ACK',
    DHCPNAK: 'NAK',
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DHCPLease:
    """Represents the network configuration received from a DHCP server."""
    client_ip: str = ''
    server_ip: str = ''
    subnet_mask: str = ''
    gateway: str = ''
    dns_servers: List[str] = field(default_factory=list)
    lease_time: int = 0


# ---------------------------------------------------------------------------
# DHCP option parsing
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


def _parse_all_options(options_data: bytes) -> dict:
    """Parse all DHCP options from raw options data into a dict."""
    result = {}
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
        result[code] = options_data[i + 2:i + 2 + length]
        i += 2 + length
    return result


def _parse_lease_from_response(data: bytes) -> Optional[DHCPLease]:
    """Extract a DHCPLease from a raw DHCP response packet."""
    if len(data) < DHCP_MIN_PACKET:
        return None
    if data[0] != BOOTREPLY:
        return None
    if data[DHCP_HEADER_SIZE:DHCP_HEADER_SIZE + 4] != DHCP_MAGIC_COOKIE:
        return None

    options_data = data[DHCP_MIN_PACKET:]
    options = _parse_all_options(options_data)

    lease = DHCPLease()

    # yiaddr — assigned IP
    yiaddr = data[16:20]
    if yiaddr != b'\x00\x00\x00\x00':
        lease.client_ip = socket.inet_ntoa(yiaddr)

    # siaddr — server IP
    siaddr = data[20:24]
    if siaddr != b'\x00\x00\x00\x00':
        lease.server_ip = socket.inet_ntoa(siaddr)

    # Subnet mask (option 1)
    if OPT_SUBNET_MASK in options:
        lease.subnet_mask = socket.inet_ntoa(options[OPT_SUBNET_MASK])

    # Gateway (option 3)
    if OPT_ROUTER in options:
        lease.gateway = socket.inet_ntoa(options[OPT_ROUTER])

    # DNS servers (option 6)
    if OPT_DNS in options:
        dns_raw = options[OPT_DNS]
        lease.dns_servers = [
            socket.inet_ntoa(dns_raw[i:i + 4])
            for i in range(0, len(dns_raw), 4)
        ]

    # Lease time (option 51)
    if OPT_LEASE_TIME in options:
        lease.lease_time = struct.unpack('!I', options[OPT_LEASE_TIME])[0]

    # Server identifier (option 54) — prefer this over siaddr
    if OPT_SERVER_ID in options:
        lease.server_ip = socket.inet_ntoa(options[OPT_SERVER_ID])

    return lease


# ---------------------------------------------------------------------------
# DHCP Client
# ---------------------------------------------------------------------------

class DHCPClient:
    """Simulates a Windows Mobile DHCP client for testing the tethering tool.

    Args:
        mac_address:  Simulated MAC address (6 bytes or colon-separated hex).
                      If None, a random locally-administered MAC is generated.
        server_port:  DHCP server port (default 67; use a high port for
                      loopback testing without root).
        client_port:  DHCP client port (default 68; use a high port for
                      loopback testing without root).
        bind_address: Address to bind the client socket to (default '' for
                      broadcast, or '127.0.0.1' for loopback).
        target_address: Address to send DHCP packets to (default
                        '255.255.255.255' for broadcast, or '127.0.0.1'
                        for loopback).
        timeout:      Socket receive timeout in seconds.
    """

    def __init__(
        self,
        mac_address: Optional[bytes] = None,
        server_port: int = DHCP_SERVER_PORT,
        client_port: int = DHCP_CLIENT_PORT,
        bind_address: str = '',
        target_address: str = '255.255.255.255',
        timeout: float = 5.0,
    ):
        if mac_address is None:
            # Generate a random locally-administered unicast MAC
            mac = bytearray(os.urandom(6))
            mac[0] = (mac[0] & 0xFE) | 0x02  # locally administered, unicast
            self.mac_address = bytes(mac)
        elif isinstance(mac_address, str):
            self.mac_address = bytes(int(b, 16) for b in mac_address.split(':'))
        else:
            self.mac_address = mac_address

        self.server_port = server_port
        self.client_port = client_port
        self.bind_address = bind_address
        self.target_address = target_address
        self.timeout = timeout

        # Transaction ID for this session
        self.xid = struct.pack('!I', random.randint(0, 0xFFFFFFFF))

        self._sock: Optional[socket.socket] = None
        self._log_lines: List[str] = []

    @property
    def mac_str(self) -> str:
        return ':'.join(f'{b:02x}' for b in self.mac_address)

    def _log(self, msg: str):
        self._log_lines.append(msg)

    def get_log(self) -> List[str]:
        """Return all log messages from this session."""
        return list(self._log_lines)

    # -- Socket management --

    def _open_socket(self):
        """Create and bind the UDP socket."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.settimeout(self.timeout)
        self._sock.bind((self.bind_address, self.client_port))

    def _close_socket(self):
        """Close the UDP socket."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # -- Packet construction --

    def _build_discover(self) -> bytes:
        """Build a DHCPDISCOVER packet."""
        return self._build_packet(DHCPDISCOVER)

    def _build_request(self, offered_ip: str, server_ip: str) -> bytes:
        """Build a DHCPREQUEST packet accepting an offer."""
        extra_options = bytearray()

        # Option 50: Requested IP Address
        extra_options += bytes([OPT_REQUESTED_IP, 4])
        extra_options += socket.inet_aton(offered_ip)

        # Option 54: Server Identifier
        extra_options += bytes([OPT_SERVER_ID, 4])
        extra_options += socket.inet_aton(server_ip)

        return self._build_packet(DHCPREQUEST, extra_options=bytes(extra_options))

    def _build_packet(self, msg_type: int, extra_options: bytes = b'') -> bytes:
        """Build a generic DHCP client packet."""
        header = bytearray(DHCP_HEADER_SIZE)

        header[0] = BOOTREQUEST
        header[1] = 1   # htype: Ethernet
        header[2] = 6   # hlen
        header[3] = 0   # hops
        header[4:8] = self.xid

        # chaddr — 16-byte field, first 6 bytes are MAC
        chaddr = bytearray(16)
        chaddr[:6] = self.mac_address
        header[28:44] = chaddr

        # Options
        options = bytearray()
        options += DHCP_MAGIC_COOKIE
        options += bytes([OPT_MSG_TYPE, 1, msg_type])
        options += extra_options
        options += bytes([OPT_END])

        return bytes(header) + bytes(options)

    # -- Send / receive --

    def _send(self, packet: bytes):
        """Send a DHCP packet to the server."""
        self._sock.sendto(packet, (self.target_address, self.server_port))

    def _receive(self) -> Optional[bytes]:
        """Wait for a DHCP response from the server."""
        try:
            data, addr = self._sock.recvfrom(4096)
            return data
        except socket.timeout:
            return None

    def _receive_response(self, expected_type: int) -> Optional[DHCPLease]:
        """Receive and validate a DHCP response of the expected type."""
        data = self._receive()
        if data is None:
            return None

        if len(data) < DHCP_MIN_PACKET:
            self._log(f"  Response too short ({len(data)} bytes)")
            return None

        # Check xid matches
        if data[4:8] != self.xid:
            self._log("  Response xid mismatch — ignoring")
            return None

        # Check message type
        options_data = data[DHCP_MIN_PACKET:]
        msg_type_raw = _parse_option(options_data, OPT_MSG_TYPE)
        if not msg_type_raw:
            self._log("  Response has no message type option")
            return None

        msg_type = msg_type_raw[0]
        type_name = _MSG_TYPE_NAMES.get(msg_type, str(msg_type))

        if msg_type == DHCPNAK:
            self._log(f"  Received DHCPNAK from server")
            return None

        if msg_type != expected_type:
            expected_name = _MSG_TYPE_NAMES.get(expected_type, str(expected_type))
            self._log(f"  Expected {expected_name} but got {type_name}")
            return None

        return _parse_lease_from_response(data)

    # -- High-level DHCP handshake --

    def perform_handshake(self) -> Optional[DHCPLease]:
        """Perform the full DHCP handshake: DISCOVER -> OFFER -> REQUEST -> ACK.

        Returns:
            A DHCPLease with the assigned configuration, or None on failure.
        """
        self._log(f"DHCP Client starting (MAC: {self.mac_str})")
        self._log(f"  Target: {self.target_address}:{self.server_port}")

        try:
            self._open_socket()

            # Step 1: DISCOVER
            self._log("Step 1: Sending DHCPDISCOVER...")
            discover = self._build_discover()
            self._send(discover)

            # Step 2: Wait for OFFER
            self._log("Step 2: Waiting for DHCPOFFER...")
            offer = self._receive_response(DHCPOFFER)
            if not offer:
                self._log("  FAILED: No DHCPOFFER received (timeout)")
                return None

            self._log(f"  Received DHCPOFFER:")
            self._log(f"    Offered IP:   {offer.client_ip}")
            self._log(f"    Server IP:    {offer.server_ip}")
            self._log(f"    Subnet Mask:  {offer.subnet_mask}")
            self._log(f"    Gateway:      {offer.gateway}")
            self._log(f"    DNS Servers:  {', '.join(offer.dns_servers)}")
            self._log(f"    Lease Time:   {offer.lease_time}s")

            # Step 3: REQUEST (accept the offer)
            self._log("Step 3: Sending DHCPREQUEST...")
            request = self._build_request(offer.client_ip, offer.server_ip)
            self._send(request)

            # Step 4: Wait for ACK
            self._log("Step 4: Waiting for DHCPACK...")
            ack = self._receive_response(DHCPACK)
            if not ack:
                self._log("  FAILED: No DHCPACK received (timeout)")
                return None

            self._log(f"  Received DHCPACK:")
            self._log(f"    Assigned IP:  {ack.client_ip}")
            self._log(f"    Server IP:    {ack.server_ip}")
            self._log(f"    Subnet Mask:  {ack.subnet_mask}")
            self._log(f"    Gateway:      {ack.gateway}")
            self._log(f"    DNS Servers:  {', '.join(ack.dns_servers)}")
            self._log(f"    Lease Time:   {ack.lease_time}s")

            self._log("DHCP handshake completed successfully!")
            return ack

        except Exception as e:
            self._log(f"DHCP handshake failed: {e}")
            return None

        finally:
            self._close_socket()
