#!/usr/bin/env python3
"""
Connectivity Validator

Validates that a tethered connection can actually reach the internet by
performing DNS resolution and HTTP requests through the assigned gateway.

Used after a successful DHCP handshake to confirm end-to-end connectivity
through the tethering tool's NAT.

Licensed under GPL v3
"""

import http.client
import os
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ConnectivityResult:
    """Results from a connectivity validation run."""
    dns_ok: bool = False
    dns_resolved_ip: str = ''
    dns_time_ms: float = 0.0

    tcp_ok: bool = False
    tcp_time_ms: float = 0.0

    http_ok: bool = False
    http_status: int = 0
    http_body_length: int = 0
    http_time_ms: float = 0.0

    errors: List[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.dns_ok and self.tcp_ok and self.http_ok


class ConnectivityValidator:
    """Validates internet connectivity through a tethered connection.

    Performs three levels of validation:
      1. DNS resolution — can the device resolve hostnames?
      2. TCP connection — can the device open a socket to a remote host?
      3. HTTP request  — can the device fetch content from a website?

    Args:
        test_hostname: Hostname to test against (default: www.example.com,
                       which is IANA-reserved and always available).
        test_path:     HTTP path to request (default: /).
        dns_server:    Optional DNS server IP to use for resolution.
                       If None, uses the system resolver.
        timeout:       Timeout for each individual operation in seconds.
        use_https:     Whether to use HTTPS (default True).
    """

    def __init__(
        self,
        test_hostname: str = 'www.example.com',
        test_path: str = '/',
        dns_server: Optional[str] = None,
        timeout: float = 10.0,
        use_https: bool = True,
    ):
        self.test_hostname = test_hostname
        self.test_path = test_path
        self.dns_server = dns_server
        self.timeout = timeout
        self.use_https = use_https
        self._log_lines: List[str] = []

    def _log(self, msg: str):
        self._log_lines.append(msg)

    def get_log(self) -> List[str]:
        return list(self._log_lines)

    def validate(self) -> ConnectivityResult:
        """Run all connectivity checks and return results.

        Each check is independent — later checks run even if earlier ones
        fail, so you get a complete picture of what works and what doesn't.
        """
        result = ConnectivityResult()

        self._log(f"Connectivity validation starting")
        self._log(f"  Target: {self.test_hostname}{self.test_path}")
        self._log(f"  Protocol: {'HTTPS' if self.use_https else 'HTTP'}")
        if self.dns_server:
            self._log(f"  DNS Server: {self.dns_server}")
        self._log("")

        # Test 1: DNS Resolution
        resolved_ip = self._test_dns(result)

        # Test 2: TCP Connection
        self._test_tcp(result, resolved_ip)

        # Test 3: HTTP Request
        self._test_http(result)

        # Summary
        self._log("")
        self._log("=" * 50)
        self._log("CONNECTIVITY VALIDATION SUMMARY")
        self._log("=" * 50)
        self._log(f"  DNS Resolution: {'PASS' if result.dns_ok else 'FAIL'}"
                  f"  ({result.dns_time_ms:.1f}ms)")
        self._log(f"  TCP Connection: {'PASS' if result.tcp_ok else 'FAIL'}"
                  f"  ({result.tcp_time_ms:.1f}ms)")
        self._log(f"  HTTP Request:   {'PASS' if result.http_ok else 'FAIL'}"
                  f"  ({result.http_time_ms:.1f}ms)")
        self._log(f"  Overall:        {'ALL PASSED' if result.all_passed else 'FAILED'}")

        if result.errors:
            self._log("")
            self._log("Errors:")
            for err in result.errors:
                self._log(f"  - {err}")

        return result

    # -- Individual tests --

    def _test_dns(self, result: ConnectivityResult) -> str:
        """Test DNS resolution of the target hostname."""
        self._log("Test 1: DNS Resolution")

        try:
            start = time.monotonic()

            if self.dns_server:
                # Use specific DNS server via raw UDP query
                resolved_ip = self._resolve_via_dns_server(
                    self.test_hostname, self.dns_server
                )
            else:
                # Use system resolver
                resolved_ip = socket.gethostbyname(self.test_hostname)

            elapsed = (time.monotonic() - start) * 1000

            result.dns_ok = True
            result.dns_resolved_ip = resolved_ip
            result.dns_time_ms = elapsed
            self._log(f"  PASS: {self.test_hostname} -> {resolved_ip} ({elapsed:.1f}ms)")
            return resolved_ip

        except Exception as e:
            result.dns_ok = False
            result.errors.append(f"DNS resolution failed: {e}")
            self._log(f"  FAIL: {e}")
            return ''

    def _test_tcp(self, result: ConnectivityResult, resolved_ip: str):
        """Test TCP connectivity to the target host."""
        self._log("Test 2: TCP Connection")

        port = 443 if self.use_https else 80
        target = resolved_ip if resolved_ip else self.test_hostname

        try:
            start = time.monotonic()
            sock = socket.create_connection(
                (target, port),
                timeout=self.timeout,
            )
            elapsed = (time.monotonic() - start) * 1000
            sock.close()

            result.tcp_ok = True
            result.tcp_time_ms = elapsed
            self._log(f"  PASS: Connected to {target}:{port} ({elapsed:.1f}ms)")

        except Exception as e:
            result.tcp_ok = False
            result.errors.append(f"TCP connection to {target}:{port} failed: {e}")
            self._log(f"  FAIL: {e}")

    def _test_http(self, result: ConnectivityResult):
        """Test HTTP(S) request to the target host."""
        self._log(f"Test 3: HTTP{'S' if self.use_https else ''} Request")

        conn = None
        try:
            start = time.monotonic()

            if self.use_https:
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(
                    self.test_hostname,
                    timeout=self.timeout,
                    context=ctx,
                )
            else:
                conn = http.client.HTTPConnection(
                    self.test_hostname,
                    timeout=self.timeout,
                )

            conn.request('GET', self.test_path, headers={
                'User-Agent': 'USBRelay-ConnectivityTest/1.0',
                'Accept': 'text/html,*/*',
            })
            response = conn.getresponse()
            body = response.read()
            elapsed = (time.monotonic() - start) * 1000

            result.http_ok = 200 <= response.status < 400
            result.http_status = response.status
            result.http_body_length = len(body)
            result.http_time_ms = elapsed

            if result.http_ok:
                self._log(f"  PASS: HTTP {response.status} — "
                          f"{len(body)} bytes ({elapsed:.1f}ms)")
            else:
                result.errors.append(
                    f"HTTP request returned status {response.status}")
                self._log(f"  FAIL: HTTP {response.status} ({elapsed:.1f}ms)")

        except Exception as e:
            result.http_ok = False
            result.errors.append(f"HTTP request failed: {e}")
            self._log(f"  FAIL: {e}")

        finally:
            if conn:
                conn.close()

    # -- DNS helper --

    def _resolve_via_dns_server(self, hostname: str, dns_server: str) -> str:
        """Resolve a hostname using a specific DNS server via raw UDP.

        Builds a minimal DNS A-record query and parses the response.
        """
        import struct as st

        # Build DNS query
        query_id = int.from_bytes(os.urandom(2), 'big')
        # Header: ID, flags=0x0100 (standard query, recursion desired),
        # QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
        header = st.pack('!HHHHHH', query_id, 0x0100, 1, 0, 0, 0)

        # Question section: encode hostname as DNS labels
        question = b''
        for label in hostname.split('.'):
            encoded = label.encode('ascii')
            question += bytes([len(encoded)]) + encoded
        question += b'\x00'  # root label
        question += st.pack('!HH', 1, 1)  # QTYPE=A, QCLASS=IN

        # Send query
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        try:
            sock.sendto(header + question, (dns_server, 53))
            data, _ = sock.recvfrom(512)
        finally:
            sock.close()

        # Parse response — find first A record in answers
        # Skip header (12 bytes) and question section
        offset = 12
        # Skip question section
        qdcount = st.unpack_from('!H', data, 4)[0]
        for _ in range(qdcount):
            while data[offset] != 0:
                label_len = data[offset]
                if label_len >= 0xC0:
                    offset += 2
                    break
                offset += 1 + label_len
            else:
                offset += 1
            offset += 4  # QTYPE + QCLASS

        # Parse answer records
        ancount = st.unpack_from('!H', data, 6)[0]
        for _ in range(ancount):
            # Name (may be compressed)
            if data[offset] >= 0xC0:
                offset += 2
            else:
                while data[offset] != 0:
                    offset += 1 + data[offset]
                offset += 1

            rtype, rclass, rttl, rdlength = st.unpack_from('!HHIH', data, offset)
            offset += 10

            if rtype == 1 and rdlength == 4:  # A record
                ip = socket.inet_ntoa(data[offset:offset + 4])
                return ip

            offset += rdlength

        raise Exception(f"No A record found for {hostname}")
