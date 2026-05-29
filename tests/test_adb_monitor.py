import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from adb_monitor import ADBMonitor


class TestRelayDecoupling(unittest.TestCase):
    """Device detection must work independently of the relay so that file
    transfer can be used without enabling the relay."""

    def setUp(self):
        self.connected = MagicMock()
        self.monitor = ADBMonitor(
            adb_path=Path('/usr/bin/adb'),
            on_device_connected=self.connected,
            apk_path=Path('/tmp/gnirehtet.apk'),
        )
        # Spy on the relay-specific setup so we can assert it does/doesn't run.
        self.monitor._setup_relay_for_device = MagicMock()

    def test_relay_disabled_by_default(self):
        self.assertFalse(self.monitor.is_relay_enabled())

    def test_detection_reports_device_without_relay_setup(self):
        """A detected device fires the connection callback but does not get
        the relay tunnel/VPN configured while the relay is disabled."""
        self.monitor._on_device_found('SERIAL123')

        self.connected.assert_called_once_with('SERIAL123')
        self.monitor._setup_relay_for_device.assert_not_called()
        self.assertEqual(self.monitor._current_device, 'SERIAL123')

    def test_detection_runs_relay_setup_when_enabled(self):
        """With the relay enabled, a newly detected device gets configured."""
        self.monitor.set_relay_enabled(True)
        self.monitor._on_device_found('SERIAL123')

        self.connected.assert_called_once_with('SERIAL123')
        self.monitor._setup_relay_for_device.assert_called_once_with('SERIAL123')

    def test_enabling_relay_configures_already_connected_device(self):
        """Toggling the relay on after a device is already present configures
        that device."""
        self.monitor._on_device_found('SERIAL123')
        self.monitor._setup_relay_for_device.assert_not_called()

        self.monitor.set_relay_enabled(True)
        # set_relay_enabled runs the setup on a daemon thread.
        for t in [t for t in __import__('threading').enumerate()
                  if t is not __import__('threading').current_thread()]:
            t.join(timeout=2)

        self.assertTrue(self.monitor.is_relay_enabled())
        self.monitor._setup_relay_for_device.assert_called_once_with('SERIAL123')

    def test_enabling_relay_with_no_device_is_noop(self):
        self.monitor.set_relay_enabled(True)
        self.monitor._setup_relay_for_device.assert_not_called()


if __name__ == '__main__':
    unittest.main()
