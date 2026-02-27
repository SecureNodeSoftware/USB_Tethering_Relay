#!/usr/bin/env python3
"""
USB Relay Manager - Relay Process Manager

Manages the relay subprocess including start/stop,
output capture, and status monitoring.

Based on gnirehtet by Genymobile (https://github.com/Genymobile/gnirehtet)
Licensed under Apache 2.0
"""

import subprocess
import sys
import threading
import re
from pathlib import Path
from typing import Callable, Optional

IS_WINDOWS = sys.platform == 'win32'

def _subprocess_kwargs():
    """Platform-specific subprocess keyword arguments."""
    kwargs = {}
    if IS_WINDOWS and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


class RelayManager:
    """Manages the gnirehtet relay subprocess."""

    # Status patterns in relay output
    STATUS_PATTERNS = {
        'connected': [
            r'Client #\d+ connected',
            r'Tunnel established',
        ],
        'waiting': [
            r'Relay server started',
            r'Listening on port',
        ],
    }

    def __init__(
        self,
        gnirehtet_path: Path,
        on_output: Optional[Callable[[str], None]] = None,
        on_status_change: Optional[Callable[[str], None]] = None
    ):
        self.gnirehtet_path = gnirehtet_path
        self.on_output = on_output
        self.on_status_change = on_status_change

        self.process: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._current_status = 'stopped'

    def start(self) -> bool:
        """Start the relay server."""
        if self._running:
            return True

        try:
            # Start gnirehtet relay command
            self.process = subprocess.Popen(
                [str(self.gnirehtet_path), 'relay'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # Line buffered
                **_subprocess_kwargs()
            )

            self._running = True
            self._update_status('waiting')

            # Start output reader thread
            self.reader_thread = threading.Thread(
                target=self._read_output,
                daemon=True
            )
            self.reader_thread.start()

            self._emit_output("Relay server started")
            return True

        except FileNotFoundError:
            binary_name = 'gnirehtet.exe' if IS_WINDOWS else 'gnirehtet'
            self._emit_output(f"Error: {binary_name} not found at {self.gnirehtet_path}")
            return False
        except Exception as e:
            self._emit_output(f"Error starting relay: {e}")
            return False

    def stop(self):
        """Stop the relay server."""
        self._running = False

        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except Exception:
                pass
            finally:
                self.process = None

        # Force kill any remaining gnirehtet processes
        self._force_kill_gnirehtet()

        self._update_status('stopped')
        self._emit_output("Relay server stopped")

    def _force_kill_gnirehtet(self):
        """Force kill any remaining gnirehtet processes."""
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ['taskkill', '/f', '/im', 'gnirehtet.exe'],
                    capture_output=True,
                    timeout=5,
                    **_subprocess_kwargs()
                )
            else:
                subprocess.run(
                    ['pkill', '-f', 'gnirehtet'],
                    capture_output=True,
                    timeout=5
                )
        except Exception:
            pass  # Ignore errors if no process found

    def is_running(self) -> bool:
        """Check if relay is running."""
        if not self._running or not self.process:
            return False

        # Check if process is still alive
        return self.process.poll() is None

    def _read_output(self):
        """Read and process relay output in background thread."""
        if not self.process or not self.process.stdout:
            return

        try:
            for line in iter(self.process.stdout.readline, ''):
                if not self._running:
                    break

                line = line.strip()
                if line:
                    self._emit_output(line)
                    self._check_status_change(line)

        except Exception as e:
            self._emit_output(f"Output reader error: {e}")
        finally:
            if self._running:
                self._emit_output("Relay process ended unexpectedly")
                self._update_status('stopped')
                self._running = False

    def _check_status_change(self, line: str):
        """Check if output line indicates status change."""
        for status, patterns in self.STATUS_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    self._update_status(status)
                    return

    def _update_status(self, status: str):
        """Update status and notify callback."""
        if status != self._current_status:
            self._current_status = status
            if self.on_status_change:
                self.on_status_change(status)

    def _emit_output(self, message: str):
        """Send output to callback."""
        if self.on_output:
            self.on_output(message)
