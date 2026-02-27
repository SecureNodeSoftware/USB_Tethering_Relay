"""
USB Relay Manager - Build Configuration

This module is overwritten by build.py at build time to reflect the
selected --mode (android, winmobile, or both).  The defaults here are
used when running from source during development.
"""

ENABLED_MODES = ['android', 'winmobile']
