#!/usr/bin/env python3
"""
Windows Mobile Device Connectivity Simulator

Simulates the full lifecycle of a Windows Mobile device connecting to the
USB Relay tethering tool via RNDIS:

  1. DHCP handshake (DISCOVER -> OFFER -> REQUEST -> ACK)
  2. Network configuration validation
  3. End-to-end connectivity test (DNS + TCP + HTTP through the tether)

Supports three operating modes:

  --mode loopback   (default)
      Starts the DHCP server in-process on high ports. No hardware, no
      root/admin privileges needed. Ideal for CI/CD and development.

  --mode integration
      Connects to an already-running USB Relay instance over the real
      RNDIS adapter (192.168.137.x subnet). Requires an actual device
      or virtual adapter to be connected.

  --mode unit
      Runs protocol-level unit tests on the DHCP client/server exchange
      without any network I/O (all in-memory). Fastest, no permissions.

Usage:
  python tests/simulate_wmdc_device.py                    # loopback mode
  python tests/simulate_wmdc_device.py --mode unit        # unit tests
  python tests/simulate_wmdc_device.py --mode integration # real hardware
  python tests/simulate_wmdc_device.py --url https://example.com/api/health

Licensed under GPL v3
"""

import argparse
import os
import socket
import struct
import sys
import time
from typing import List, Optional

# Add project root to path so we can import from src/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'src'))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'tests'))

from dhcp_client import DHCPClient, DHCPLease, DHCP_MAGIC_COOKIE, DHCP_HEADER_SIZE
from dhcp_client import BOOTREPLY, DHCPOFFER, DHCPACK, DHCPNAK
from dhcp_client import OPT_MSG_TYPE, OPT_SUBNET_MASK, OPT_ROUTER, OPT_DNS
from dhcp_client import OPT_LEASE_TIME, OPT_SERVER_ID, OPT_END
from dhcp_client import _parse_option, _parse_lease_from_response
from connectivity_validator import ConnectivityValidator, ConnectivityResult


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXPECTED_SERVER_IP = '192.168.137.1'
EXPECTED_CLIENT_IP = '192.168.137.2'
EXPECTED_SUBNET = '255.255.255.0'

# High ports for loopback testing (no root needed)
LOOPBACK_SERVER_PORT = 16767
LOOPBACK_CLIENT_PORT = 16768


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

class Colors:
    """ANSI color codes for terminal output."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'

    @staticmethod
    def enabled():
        """Check if the terminal supports colors."""
        return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    """Apply color if terminal supports it."""
    if Colors.enabled():
        return f"{color}{text}{Colors.RESET}"
    return text


def print_header(title: str):
    width = 60
    print()
    print(_c("=" * width, Colors.CYAN))
    print(_c(f"  {title}", Colors.BOLD))
    print(_c("=" * width, Colors.CYAN))
    print()


def print_section(title: str):
    print()
    print(_c(f"--- {title} ---", Colors.BLUE))


def print_pass(msg: str):
    print(f"  {_c('[PASS]', Colors.GREEN)} {msg}")


def print_fail(msg: str):
    print(f"  {_c('[FAIL]', Colors.RED)} {msg}")


def print_info(msg: str):
    print(f"  {_c('[INFO]', Colors.CYAN)} {msg}")


def print_warn(msg: str):
    print(f"  {_c('[WARN]', Colors.YELLOW)} {msg}")


# ---------------------------------------------------------------------------
# Unit Tests (in-memory, no network)
# ---------------------------------------------------------------------------

def run_unit_tests() -> bool:
    """Run protocol-level unit tests on DHCP packet construction/parsing."""
    print_header("UNIT TESTS: DHCP Protocol Validation")

    passed = 0
    failed = 0

    def assert_test(name: str, condition: bool, detail: str = ''):
        nonlocal passed, failed
        if condition:
            print_pass(name)
            passed += 1
        else:
            print_fail(f"{name}" + (f" — {detail}" if detail else ""))
            failed += 1

    # Test 1: DHCP Client packet construction
    print_section("DHCP Client Packet Construction")

    client = DHCPClient(
        mac_address=b'\x02\x00\x00\x00\x00\x01',
        server_port=LOOPBACK_SERVER_PORT,
        client_port=LOOPBACK_CLIENT_PORT,
    )

    discover = client._build_discover()
    assert_test(
        "DISCOVER packet length >= 240 bytes",
        len(discover) >= 240,
        f"got {len(discover)}",
    )
    assert_test(
        "DISCOVER op code is BOOTREQUEST (1)",
        discover[0] == 1,
    )
    assert_test(
        "DISCOVER htype is Ethernet (1)",
        discover[1] == 1,
    )
    assert_test(
        "DISCOVER hlen is 6",
        discover[2] == 6,
    )
    assert_test(
        "DISCOVER contains magic cookie",
        discover[236:240] == DHCP_MAGIC_COOKIE,
    )
    assert_test(
        "DISCOVER contains message type option",
        _parse_option(discover[240:], OPT_MSG_TYPE) == bytes([1]),  # DHCPDISCOVER
    )
    assert_test(
        "DISCOVER chaddr contains client MAC",
        discover[28:34] == b'\x02\x00\x00\x00\x00\x01',
    )

    # Test 2: DHCP REQUEST packet construction
    print_section("DHCP REQUEST Packet Construction")

    request = client._build_request('192.168.137.2', '192.168.137.1')
    assert_test(
        "REQUEST packet length >= 240 bytes",
        len(request) >= 240,
    )
    assert_test(
        "REQUEST contains message type option",
        _parse_option(request[240:], OPT_MSG_TYPE) == bytes([3]),  # DHCPREQUEST
    )
    assert_test(
        "REQUEST contains requested IP option",
        _parse_option(request[240:], 50) == socket.inet_aton('192.168.137.2'),
    )
    assert_test(
        "REQUEST contains server ID option",
        _parse_option(request[240:], OPT_SERVER_ID) == socket.inet_aton('192.168.137.1'),
    )

    # Test 3: DHCP response parsing
    print_section("DHCP Response Parsing")

    # Build a synthetic DHCPOFFER response
    header = bytearray(DHCP_HEADER_SIZE)
    header[0] = BOOTREPLY
    header[1] = 1
    header[2] = 6
    header[4:8] = client.xid
    struct.pack_into('!4s', header, 16, socket.inet_aton('192.168.137.2'))
    struct.pack_into('!4s', header, 20, socket.inet_aton('192.168.137.1'))
    header[28:34] = b'\x02\x00\x00\x00\x00\x01'

    options = bytearray()
    options += DHCP_MAGIC_COOKIE
    options += bytes([OPT_MSG_TYPE, 1, DHCPOFFER])
    options += bytes([OPT_SERVER_ID, 4]) + socket.inet_aton('192.168.137.1')
    options += bytes([OPT_LEASE_TIME, 4]) + struct.pack('!I', 3600)
    options += bytes([OPT_SUBNET_MASK, 4]) + socket.inet_aton('255.255.255.0')
    options += bytes([OPT_ROUTER, 4]) + socket.inet_aton('192.168.137.1')
    options += bytes([OPT_DNS, 4]) + socket.inet_aton('8.8.8.8')
    options += bytes([OPT_END])

    offer_packet = bytes(header) + bytes(options)
    lease = _parse_lease_from_response(offer_packet)

    assert_test("Parse OFFER: lease object created", lease is not None)
    if lease:
        assert_test(
            "Parse OFFER: client_ip is 192.168.137.2",
            lease.client_ip == '192.168.137.2',
            f"got {lease.client_ip}",
        )
        assert_test(
            "Parse OFFER: server_ip is 192.168.137.1",
            lease.server_ip == '192.168.137.1',
            f"got {lease.server_ip}",
        )
        assert_test(
            "Parse OFFER: subnet_mask is 255.255.255.0",
            lease.subnet_mask == '255.255.255.0',
            f"got {lease.subnet_mask}",
        )
        assert_test(
            "Parse OFFER: gateway is 192.168.137.1",
            lease.gateway == '192.168.137.1',
            f"got {lease.gateway}",
        )
        assert_test(
            "Parse OFFER: DNS contains 8.8.8.8",
            '8.8.8.8' in lease.dns_servers,
            f"got {lease.dns_servers}",
        )
        assert_test(
            "Parse OFFER: lease_time is 3600",
            lease.lease_time == 3600,
            f"got {lease.lease_time}",
        )

    # Test 4: Edge cases
    print_section("Edge Cases")

    # Too-short packet
    short_lease = _parse_lease_from_response(b'\x00' * 100)
    assert_test("Reject packet shorter than minimum", short_lease is None)

    # Wrong op code (BOOTREQUEST instead of BOOTREPLY)
    bad_op = bytearray(offer_packet)
    bad_op[0] = 1  # BOOTREQUEST
    assert_test(
        "Reject packet with wrong op code",
        _parse_lease_from_response(bytes(bad_op)) is None,
    )

    # Bad magic cookie
    bad_cookie = bytearray(offer_packet)
    bad_cookie[236:240] = b'\x00\x00\x00\x00'
    assert_test(
        "Reject packet with bad magic cookie",
        _parse_lease_from_response(bytes(bad_cookie)) is None,
    )

    # Multiple DNS servers
    multi_dns_options = bytearray()
    multi_dns_options += DHCP_MAGIC_COOKIE
    multi_dns_options += bytes([OPT_MSG_TYPE, 1, DHCPOFFER])
    multi_dns_options += bytes([OPT_DNS, 8])
    multi_dns_options += socket.inet_aton('8.8.8.8')
    multi_dns_options += socket.inet_aton('8.8.4.4')
    multi_dns_options += bytes([OPT_END])

    multi_dns_packet = bytes(header) + bytes(multi_dns_options)
    multi_lease = _parse_lease_from_response(multi_dns_packet)
    assert_test(
        "Parse multiple DNS servers",
        multi_lease is not None and len(multi_lease.dns_servers) == 2,
        f"got {multi_lease.dns_servers if multi_lease else 'None'}",
    )

    # Summary
    print_section("Unit Test Summary")
    total = passed + failed
    print_info(f"Total: {total}  Passed: {passed}  Failed: {failed}")

    if failed == 0:
        print_pass("All unit tests passed!")
    else:
        print_fail(f"{failed} unit test(s) failed")

    return failed == 0


# ---------------------------------------------------------------------------
# Loopback Test (in-process DHCP server + client on localhost)
# ---------------------------------------------------------------------------

def run_loopback_test(
    test_url: Optional[str] = None,
    skip_connectivity: bool = False,
) -> bool:
    """Run a full DHCP handshake against an in-process server on localhost.

    This mode requires no hardware and no root/admin privileges.
    """
    print_header("LOOPBACK TEST: In-Process DHCP Handshake")

    # Import the real DHCP server from the project
    from dhcp_server import DHCPServer

    # Start DHCP server on high port (no root needed)
    print_section("Starting DHCP Server (in-process)")
    print_info(f"Server port: {LOOPBACK_SERVER_PORT}")
    print_info(f"Server IP: {EXPECTED_SERVER_IP}")
    print_info(f"Client IP: {EXPECTED_CLIENT_IP}")

    # Subclass DHCPServer to redirect broadcast responses to localhost
    # (255.255.255.255 doesn't reach the loopback interface)
    class LoopbackDHCPServer(DHCPServer):
        def _send_broadcast(self, packet: bytes):
            try:
                self._sock.sendto(packet, ('127.0.0.1', LOOPBACK_CLIENT_PORT))
            except OSError as e:
                self._log(f"Failed to send DHCP response: {e}", 'error')

    # Monkey-patch the server port since DHCPServer._run() binds to it
    import dhcp_server as dhcp_mod
    original_port = dhcp_mod.DHCP_SERVER_PORT
    dhcp_mod.DHCP_SERVER_PORT = LOOPBACK_SERVER_PORT

    log_messages = []

    def server_log(msg, level):
        log_messages.append((msg, level))
        if level == 'error':
            print_warn(f"Server: {msg}")

    server = LoopbackDHCPServer(
        server_ip=EXPECTED_SERVER_IP,
        client_ip=EXPECTED_CLIENT_IP,
        dns_servers=['8.8.8.8', '8.8.4.4'],
        on_log=server_log,
    )

    try:
        server.start()
        # Give the server a moment to bind
        time.sleep(0.5)

        if not server.is_running():
            print_fail("DHCP server failed to start")
            dhcp_mod.DHCP_SERVER_PORT = original_port
            return False

        print_pass("DHCP server started successfully")

        # Run DHCP client handshake
        print_section("DHCP Client Handshake")

        client = DHCPClient(
            server_port=LOOPBACK_SERVER_PORT,
            client_port=LOOPBACK_CLIENT_PORT,
            bind_address='127.0.0.1',
            target_address='127.0.0.1',
            timeout=5.0,
        )

        lease = client.perform_handshake()

        # Print client log
        for line in client.get_log():
            print_info(line)

        if not lease:
            print_fail("DHCP handshake failed — no lease obtained")
            return False

        print_pass("DHCP handshake completed successfully")

        # Validate lease parameters
        print_section("Lease Validation")
        all_ok = True

        checks = [
            ("Client IP matches expected",
             lease.client_ip == EXPECTED_CLIENT_IP,
             f"expected {EXPECTED_CLIENT_IP}, got {lease.client_ip}"),
            ("Server IP matches expected",
             lease.server_ip == EXPECTED_SERVER_IP,
             f"expected {EXPECTED_SERVER_IP}, got {lease.server_ip}"),
            ("Subnet mask is correct",
             lease.subnet_mask == EXPECTED_SUBNET,
             f"expected {EXPECTED_SUBNET}, got {lease.subnet_mask}"),
            ("Gateway matches server IP",
             lease.gateway == EXPECTED_SERVER_IP,
             f"expected {EXPECTED_SERVER_IP}, got {lease.gateway}"),
            ("DNS servers provided",
             len(lease.dns_servers) > 0,
             "no DNS servers in lease"),
            ("Lease time is positive",
             lease.lease_time > 0,
             f"got {lease.lease_time}"),
        ]

        for name, ok, detail in checks:
            if ok:
                print_pass(name)
            else:
                print_fail(f"{name} — {detail}")
                all_ok = False

        # Print server log
        print_section("DHCP Server Log")
        for msg, level in log_messages:
            if level in ('success', 'info'):
                print_info(f"Server: {msg}")
            elif level == 'warning':
                print_warn(f"Server: {msg}")

        # Connectivity test (uses system network, not the simulated tether)
        if not skip_connectivity:
            print_section("Connectivity Validation (via system network)")
            print_info("Note: In loopback mode, connectivity tests use the "
                       "host's own network — not the simulated tether.")
            print_info("Use --mode integration to test actual tethered connectivity.")

            hostname = 'www.example.com'
            path = '/'
            use_https = True
            if test_url:
                from urllib.parse import urlparse
                parsed = urlparse(test_url)
                hostname = parsed.hostname or hostname
                path = parsed.path or '/'
                use_https = (parsed.scheme == 'https')

            validator = ConnectivityValidator(
                test_hostname=hostname,
                test_path=path,
                use_https=use_https,
            )
            result = validator.validate()
            for line in validator.get_log():
                print_info(line)

            if result.all_passed:
                print_pass("Connectivity validation passed (system network)")
            else:
                print_warn("Connectivity validation had failures (this may be "
                           "expected in restricted environments)")

        return all_ok

    finally:
        server.stop()
        dhcp_mod.DHCP_SERVER_PORT = original_port
        print_info("DHCP server stopped")


# ---------------------------------------------------------------------------
# Integration Test (real RNDIS adapter)
# ---------------------------------------------------------------------------

def run_integration_test(
    test_url: Optional[str] = None,
    skip_connectivity: bool = False,
) -> bool:
    """Run a full test against an already-running USB Relay instance.

    Requires:
      - A Windows Mobile device connected via USB RNDIS (or a virtual
        adapter on the 192.168.137.x subnet)
      - The USB Relay application running with Windows Mobile mode active
      - Admin/root to send DHCP packets on privileged ports (67/68)
    """
    print_header("INTEGRATION TEST: Real RNDIS Connection")

    # Check if we can bind to privileged ports
    if os.name != 'nt' and os.getuid() != 0:
        print_warn("Integration mode requires root/admin to bind DHCP ports.")
        print_info("Run with: sudo python tests/simulate_wmdc_device.py --mode integration")
        print_info("")
        print_info("Alternatively, use --mode loopback for unprivileged testing.")
        return False

    # Check for RNDIS adapter (Windows only)
    if sys.platform == 'win32':
        print_section("RNDIS Adapter Detection")
        import subprocess
        try:
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 "Get-NetAdapter | Where-Object {"
                 "  $_.InterfaceDescription -match 'RNDIS|Remote NDIS' -and"
                 "  $_.Status -eq 'Up'"
                 "} | Select-Object Name, InterfaceDescription, Status"],
                capture_output=True, text=True, timeout=15,
            )
            if result.stdout.strip():
                print_pass(f"RNDIS adapter found:")
                for line in result.stdout.strip().split('\n'):
                    print_info(f"  {line}")
            else:
                print_warn("No RNDIS adapter detected — will attempt DHCP anyway")
        except Exception as e:
            print_warn(f"Could not check for RNDIS adapter: {e}")
    else:
        print_info("Non-Windows platform — skipping RNDIS adapter detection")

    # Perform DHCP handshake on real network
    print_section("DHCP Client Handshake (broadcast)")

    client = DHCPClient(
        server_port=67,
        client_port=68,
        bind_address='',
        target_address='255.255.255.255',
        timeout=10.0,
    )

    lease = client.perform_handshake()

    for line in client.get_log():
        print_info(line)

    if not lease:
        print_fail("DHCP handshake failed — is the USB Relay application running?")
        return False

    print_pass("DHCP handshake completed")

    # Validate configuration
    print_section("Configuration Validation")
    all_ok = True

    checks = [
        ("Server assigned IP in expected subnet",
         lease.client_ip.startswith('192.168.137.'),
         f"got {lease.client_ip}"),
        (f"Gateway is {EXPECTED_SERVER_IP}",
         lease.gateway == EXPECTED_SERVER_IP,
         f"got {lease.gateway}"),
        ("DNS servers provided",
         len(lease.dns_servers) > 0,
         "no DNS servers"),
    ]

    for name, ok, detail in checks:
        if ok:
            print_pass(name)
        else:
            print_fail(f"{name} — {detail}")
            all_ok = False

    # Connectivity test through the tether
    if not skip_connectivity:
        print_section("End-to-End Connectivity (through tether)")

        hostname = 'www.example.com'
        path = '/'
        use_https = True
        if test_url:
            from urllib.parse import urlparse
            parsed = urlparse(test_url)
            hostname = parsed.hostname or hostname
            path = parsed.path or '/'
            use_https = (parsed.scheme == 'https')

        dns_server = lease.dns_servers[0] if lease.dns_servers else None

        validator = ConnectivityValidator(
            test_hostname=hostname,
            test_path=path,
            dns_server=dns_server,
            use_https=use_https,
        )
        result = validator.validate()

        for line in validator.get_log():
            print_info(line)

        if result.all_passed:
            print_pass("End-to-end connectivity validated through tether!")
        else:
            print_fail("Connectivity validation failed through tether")
            all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Windows Mobile Device Connectivity Simulator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                              Run loopback test (no hardware needed)
  %(prog)s --mode unit                  Run DHCP protocol unit tests
  %(prog)s --mode loopback              Full handshake on localhost
  %(prog)s --mode integration           Test with real RNDIS adapter
  %(prog)s --url https://example.com    Test connectivity to specific URL
  %(prog)s --skip-connectivity          Skip HTTP connectivity tests
        """,
    )

    parser.add_argument(
        '--mode', '-m',
        choices=['unit', 'loopback', 'integration'],
        default='loopback',
        help='Test mode (default: loopback)',
    )
    parser.add_argument(
        '--url', '-u',
        default=None,
        help='URL to test connectivity against (default: https://www.example.com)',
    )
    parser.add_argument(
        '--skip-connectivity',
        action='store_true',
        help='Skip the HTTP connectivity validation step',
    )

    args = parser.parse_args()

    print(_c("USB Relay Manager — Windows Mobile Device Simulator", Colors.BOLD))
    print(f"Mode: {args.mode}")
    if args.url:
        print(f"Test URL: {args.url}")
    print()

    success = False

    if args.mode == 'unit':
        success = run_unit_tests()

    elif args.mode == 'loopback':
        success = run_loopback_test(
            test_url=args.url,
            skip_connectivity=args.skip_connectivity,
        )

    elif args.mode == 'integration':
        success = run_integration_test(
            test_url=args.url,
            skip_connectivity=args.skip_connectivity,
        )

    # Final result
    print()
    print("=" * 60)
    if success:
        print(_c("  RESULT: ALL TESTS PASSED", Colors.GREEN))
    else:
        print(_c("  RESULT: SOME TESTS FAILED", Colors.RED))
    print("=" * 60)
    print()

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
