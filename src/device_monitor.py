#!/usr/bin/env python3
"""
USB Relay Manager - Device Monitor Base Class

Provides the common polling loop, threading, lifecycle management, and
callback wiring shared by ADBMonitor and WMDCMonitor.

Licensed under GPL v3
"""

import threading
import time
from typing import Callable, Optional


class DeviceMonitor:
    """Base class for USB device monitors.

    Subclasses must override ``_poll()`` to implement device-specific
    detection logic.  They may also override ``_pre_start()`` (for
    validation before the monitor thread launches) and ``_post_stop()``
    (for cleanup after the thread exits).
    """

    def __init__(
        self,
        *,
        on_device_connected: Optional[Callable[[str], None]] = None,
        on_device_disconnected: Optional[Callable[[], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        poll_interval: float = 2.0,
    ):
        self.on_device_connected = on_device_connected
        self.on_device_disconnected = on_device_disconnected
        self.on_log = on_log
        self.poll_interval = poll_interval

        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None

    # -- Public API --

    def start(self):
        """Start device monitoring."""
        if self._running:
            return

        if not self._pre_start():
            return

        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
        )
        self._monitor_thread.start()

    def stop(self):
        """Stop device monitoring."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        self._post_stop()

    def is_running(self) -> bool:
        """Check if the monitor is actively running."""
        return self._running

    # -- Hooks for subclasses --

    def _pre_start(self) -> bool:
        """Called before the monitor thread starts.

        Return ``True`` to proceed, ``False`` to abort.
        """
        return True

    def _post_stop(self):
        """Called after the monitor thread has been joined."""

    def _poll(self):
        """Called once per polling interval.  Must be overridden."""
        raise NotImplementedError

    # -- Internal --

    def _monitor_loop(self):
        """Main polling loop (runs on the monitor thread)."""
        while self._running:
            try:
                self._poll()
            except Exception as e:
                self._log(f"Monitor error: {e}", 'error')

            time.sleep(self.poll_interval)

    def _log(self, message: str, level: str = 'info'):
        """Send a log message to the registered callback."""
        if self.on_log:
            self.on_log(message, level)
