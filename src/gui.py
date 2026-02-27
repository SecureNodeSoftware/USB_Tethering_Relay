#!/usr/bin/env python3
"""
USB Relay Manager - GUI Module

Provides the tkinter-based graphical interface with SCAN branding,
Start/Stop buttons, status indicator, and scrolling log panel.

Based on gnirehtet by Genymobile (https://github.com/Genymobile/gnirehtet)
Licensed under Apache 2.0
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
from datetime import datetime
from typing import Dict, Callable, Optional
from pathlib import Path
import sys
import threading
import webbrowser

IS_WINDOWS = sys.platform == 'win32'

# SCAN Brand Colors
SCAN_BRAND_BLUE = '#4169E1'  # Royal Blue
SCAN_BRAND_BLUE_DARK = '#2850b8'

# Theme colors (light theme)
BG_COLOR = '#ffffff'
TEXT_COLOR = '#333333'
TEXT_SECONDARY = '#666666'
LOG_BG = '#f5f5f5'
LOG_TEXT = '#333333'

# Status colors
STATUS_COLORS = {
    'stopped': '#dc3545',      # Red
    'starting': '#ffc107',     # Yellow
    'waiting': '#ffc107',      # Yellow
    'connected': '#28a745',    # Green
}

STATUS_LABELS = {
    'stopped': 'Stopped',
    'starting': 'Starting...',
    'waiting': 'Waiting for Device',
    'connected': 'Connected',
}


class RoundedButton(tk.Canvas):
    """A button with rounded corners."""

    def __init__(self, parent, text, command, bg_color, fg_color='white',
                 width=120, height=40, corner_radius=8, font=('Arial', 11, 'bold')):
        super().__init__(parent, width=width, height=height,
                        bg=BG_COLOR, highlightthickness=0)

        self.command = command
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.disabled_bg = '#cccccc'
        self.text = text
        self.width = width
        self.height = height
        self.corner_radius = corner_radius
        self.font = font
        self._enabled = True

        self._draw()

        self.bind('<Button-1>', self._on_click)
        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)

    def _draw(self, hover=False):
        """Draw the rounded button."""
        self.delete('all')

        if not self._enabled:
            color = self.disabled_bg
        elif hover:
            # Darken color on hover
            color = self._darken_color(self.bg_color)
        else:
            color = self.bg_color

        # Draw rounded rectangle
        self._create_rounded_rect(0, 0, self.width, self.height,
                                  self.corner_radius, fill=color, outline='')

        # Draw text
        text_color = self.fg_color if self._enabled else '#888888'
        self.create_text(self.width // 2, self.height // 2,
                        text=self.text, fill=text_color, font=self.font)

    def _create_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        """Draw a rounded rectangle."""
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _darken_color(self, hex_color):
        """Darken a hex color by 15%."""
        hex_color = hex_color.lstrip('#')
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        r = max(0, int(r * 0.85))
        g = max(0, int(g * 0.85))
        b = max(0, int(b * 0.85))
        return f'#{r:02x}{g:02x}{b:02x}'

    def _on_click(self, event):
        if self._enabled and self.command:
            self.command()

    def _on_enter(self, event):
        if self._enabled:
            self.config(cursor='hand2')
            self._draw(hover=True)

    def _on_leave(self, event):
        self.config(cursor='')
        self._draw(hover=False)

    def set_enabled(self, enabled):
        """Enable or disable the button."""
        self._enabled = enabled
        self._draw()


class USBRelayApp:
    """Main application window for USB Relay Manager."""

    def __init__(self, resources: Dict[str, Path]):
        self.resources = resources
        self.root = tk.Tk()
        self.root.title("USB Relay Manager")
        self.root.geometry("400x400")
        self.root.resizable(False, False)

        # Configure light theme
        self.root.configure(bg=BG_COLOR)

        # Set window icon
        icon_path = resources.get('icon')
        if icon_path and icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass  # Fallback to default icon if loading fails

        # State
        self.status = 'stopped'
        self.device_id: Optional[str] = None
        self.relay_manager = None
        self.adb_monitor = None
        self.wmdc_monitor = None
        self._active_mode: Optional[str] = None  # tracks which mode is currently running

        self._setup_ui()
        self._setup_managers()

    def _setup_ui(self):
        """Set up the user interface."""
        # Credit footer - pack FIRST with side=BOTTOM to reserve space
        credit_label = tk.Label(
            self.root,
            text="Powered by gnirehtet",
            font=('Arial', 8),
            fg='#999999',
            bg=BG_COLOR,
            cursor='hand2'
        )
        credit_label.pack(pady=(2, 5), side=tk.BOTTOM)
        credit_label.bind('<Button-1>', lambda e: webbrowser.open('https://github.com/Genymobile/gnirehtet'))
        credit_label.bind('<Enter>', lambda e: credit_label.config(fg=SCAN_BRAND_BLUE))
        credit_label.bind('<Leave>', lambda e: credit_label.config(fg='#999999'))

        # Main container - fill horizontally only, pack at top
        main_frame = tk.Frame(self.root, bg=BG_COLOR)
        main_frame.pack(fill=tk.X, padx=20, pady=0)

        # SCAN Logo Section
        logo_frame = tk.Frame(main_frame, bg=BG_COLOR)
        logo_frame.pack(pady=(10, 10))

        # Load and display logo image
        self._load_logo(logo_frame)

        # Buttons frame - minimal spacing
        button_frame = tk.Frame(main_frame, bg=BG_COLOR)
        button_frame.pack(pady=(0, 0))

        # Start button (rounded)
        self.start_btn = RoundedButton(
            button_frame,
            text="START",
            command=self._on_start,
            bg_color='#28a745',
            width=96,
            height=35,
            corner_radius=10
        )
        self.start_btn.pack(side=tk.LEFT, padx=10)

        # Stop button (rounded)
        self.stop_btn = RoundedButton(
            button_frame,
            text="STOP",
            command=self._on_stop,
            bg_color='#dc3545',
            width=96,
            height=35,
            corner_radius=10
        )
        self.stop_btn.pack(side=tk.LEFT, padx=10)
        self.stop_btn.set_enabled(False)

        # Device mode selector (only show Windows Mobile option on Windows)
        self.device_mode = tk.StringVar(value='android')
        mode_frame = tk.Frame(main_frame, bg=BG_COLOR)
        mode_frame.pack(pady=(5, 0))

        tk.Radiobutton(
            mode_frame,
            text="Android (CN80G)",
            variable=self.device_mode,
            value='android',
            font=('Arial', 9),
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            selectcolor=BG_COLOR,
            activebackground=BG_COLOR,
            command=self._on_mode_change
        ).pack(side=tk.LEFT, padx=(0, 10))

        if IS_WINDOWS:
            tk.Radiobutton(
                mode_frame,
                text="Windows Mobile",
                variable=self.device_mode,
                value='winmobile',
                font=('Arial', 9),
                bg=BG_COLOR,
                fg=TEXT_COLOR,
                selectcolor=BG_COLOR,
                activebackground=BG_COLOR,
                command=self._on_mode_change
            ).pack(side=tk.LEFT)

        # Status frame
        status_frame = tk.Frame(main_frame, bg=BG_COLOR)
        status_frame.pack(pady=(5, 0), fill=tk.X)

        # Status indicator
        status_row = tk.Frame(status_frame, bg=BG_COLOR)
        status_row.pack()

        tk.Label(
            status_row,
            text="Status: ",
            font=('Arial', 11),
            fg=TEXT_COLOR,
            bg=BG_COLOR
        ).pack(side=tk.LEFT)

        self.status_dot = tk.Label(
            status_row,
            text="●",
            font=('Arial', 14),
            fg=STATUS_COLORS['stopped'],
            bg=BG_COLOR
        )
        self.status_dot.pack(side=tk.LEFT)

        self.status_label = tk.Label(
            status_row,
            text=STATUS_LABELS['stopped'],
            font=('Arial', 11),
            fg=TEXT_COLOR,
            bg=BG_COLOR
        )
        self.status_label.pack(side=tk.LEFT, padx=(5, 0))

        # Device info
        self.device_label = tk.Label(
            status_frame,
            text="Device: None",
            font=('Arial', 10),
            fg=TEXT_SECONDARY,
            bg=BG_COLOR
        )
        self.device_label.pack(pady=(3, 0))

        # Log panel - separate frame that expands to fill remaining space
        log_frame = tk.Frame(self.root, bg=BG_COLOR)
        log_frame.pack(pady=(5, 0), padx=20, fill=tk.BOTH, expand=True)

        # Log header with Export button
        log_header = tk.Frame(log_frame, bg=BG_COLOR)
        log_header.pack(fill=tk.X)

        tk.Label(
            log_header,
            text="Log:",
            font=('Arial', 10),
            fg=TEXT_SECONDARY,
            bg=BG_COLOR,
            anchor='w'
        ).pack(side=tk.LEFT)

        # Export Logs link
        export_label = tk.Label(
            log_header,
            text="Export Logs",
            font=('Arial', 9, 'underline'),
            fg=SCAN_BRAND_BLUE,
            bg=BG_COLOR,
            cursor='hand2'
        )
        export_label.pack(side=tk.RIGHT)
        export_label.bind('<Button-1>', lambda e: self._export_logs())
        export_label.bind('<Enter>', lambda e: export_label.config(fg=SCAN_BRAND_BLUE_DARK))
        export_label.bind('<Leave>', lambda e: export_label.config(fg=SCAN_BRAND_BLUE))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            width=55,
            height=6,
            font=('Consolas', 9),
            bg=LOG_BG,
            fg=LOG_TEXT,
            insertbackground='black',
            relief=tk.SOLID,
            borderwidth=1
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        # Configure log text tags for colored output
        self.log_text.tag_configure('timestamp', foreground='#888888')
        self.log_text.tag_configure('info', foreground='#0066cc')
        self.log_text.tag_configure('success', foreground='#28a745')
        self.log_text.tag_configure('warning', foreground='#cc8800')
        self.log_text.tag_configure('error', foreground='#dc3545')

    def _load_logo(self, parent):
        """Load and display the logo PNG image."""
        logo_path = self.resources.get('logo')

        if logo_path and logo_path.exists():
            try:
                # Load PNG image
                self.logo_image = tk.PhotoImage(file=str(logo_path))

                # Create label to display image
                logo_label = tk.Label(
                    parent,
                    image=self.logo_image,
                    bg=BG_COLOR
                )
                logo_label.pack()
            except Exception as e:
                # Fallback to text if image fails to load
                self._show_fallback_logo(parent, str(e))
        else:
            # Fallback if logo file not found
            self._show_fallback_logo(parent, "Logo file not found")

    def _show_fallback_logo(self, parent, error_msg=None):
        """Show text fallback if logo image can't be loaded."""
        tk.Label(
            parent,
            text="SCAN",
            font=('Arial', 28, 'bold'),
            fg=SCAN_BRAND_BLUE,
            bg=BG_COLOR
        ).pack()

        tk.Label(
            parent,
            text="Secure Code Acquisition Node",
            font=('Arial', 9),
            fg=TEXT_SECONDARY,
            bg=BG_COLOR
        ).pack()

    # -- Thread-safe callback wrappers --
    # Monitor threads call these from background threads.  Each wrapper
    # schedules the real handler on the tkinter main-loop via root.after()
    # so that widget updates never happen off the main thread.

    def _ts_on_device_connected(self, device_id: str):
        self.root.after(0, self._on_device_connected, device_id)

    def _ts_on_device_disconnected(self):
        self.root.after(0, self._on_device_disconnected)

    def _ts_log(self, message: str, level: str = 'info'):
        self.root.after(0, self.log, message, level)

    def _ts_on_relay_output(self, line: str):
        self.root.after(0, self._on_relay_output, line)

    def _ts_on_status_change(self, status: str):
        self.root.after(0, self._on_status_change, status)

    def _setup_managers(self):
        """Initialize relay, ADB, and (on Windows) WMDC managers."""
        from relay_manager import RelayManager
        from adb_monitor import ADBMonitor

        self.relay_manager = RelayManager(
            gnirehtet_path=self.resources['gnirehtet'],
            on_output=self._ts_on_relay_output,
            on_status_change=self._ts_on_status_change
        )

        self.adb_monitor = ADBMonitor(
            adb_path=self.resources['adb'],
            on_device_connected=self._ts_on_device_connected,
            on_device_disconnected=self._ts_on_device_disconnected,
            on_log=self._ts_log
        )

        # Windows Mobile monitor (Windows-only)
        if IS_WINDOWS:
            try:
                from wmdc_monitor import WMDCMonitor
                self.wmdc_monitor = WMDCMonitor(
                    on_device_connected=self._ts_on_device_connected,
                    on_device_disconnected=self._ts_on_device_disconnected,
                    on_log=self._ts_log
                )
            except ImportError:
                self.wmdc_monitor = None

    def _on_start(self):
        """Handle Start button click."""
        mode = self.device_mode.get()
        self.start_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        self.update_status('starting')

        if mode == 'winmobile':
            if not self.wmdc_monitor:
                self.log("Windows Mobile mode not available", 'error')
                self.start_btn.set_enabled(True)
                self.stop_btn.set_enabled(False)
                self.update_status('stopped')
                return
            self.log("Starting Windows Mobile tethering...", 'info')
            self.wmdc_monitor.start()
            self.update_status('waiting')
        else:
            self.log("Starting relay server...", 'info')
            self.adb_monitor.start()
            self.relay_manager.start()

        self._active_mode = mode

    def _on_stop(self):
        """Handle Stop button click."""
        self.start_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)

        if self._active_mode == 'winmobile':
            self.log("Stopping Windows Mobile tethering...", 'info')
            if self.wmdc_monitor:
                self.wmdc_monitor.stop()
        else:
            self.log("Stopping relay server...", 'info')
            self.relay_manager.stop()
            self.adb_monitor.stop()

        self._active_mode = None
        self.update_status('stopped')
        self.device_label.config(text="Device: None")

    def _on_mode_change(self):
        """Handle mode radio button change while running."""
        if self._active_mode and self._active_mode != self.device_mode.get():
            # Stop current mode in a background thread, then start new mode
            # on the main thread to avoid freezing the GUI during cleanup.
            self._stop_managers_async(then=self._on_start)

    def _on_relay_output(self, line: str):
        """Handle output from relay process."""
        self.log(line)

    def _on_status_change(self, status: str):
        """Handle relay status changes."""
        self.update_status(status)

    def _on_device_connected(self, device_id: str):
        """Handle device connection."""
        self.device_id = device_id
        self.device_label.config(text=f"Device: {device_id}")
        self.log(f"Device connected: {device_id}", 'success')

        is_active = (
            (self._active_mode == 'winmobile' and self.wmdc_monitor and self.wmdc_monitor.is_running())
            or (self._active_mode == 'android' and self.relay_manager.is_running())
        )
        if is_active:
            self.update_status('connected')

    def _on_device_disconnected(self):
        """Handle device disconnection."""
        self.device_id = None
        self.device_label.config(text="Device: None")
        self.log("Device disconnected", 'warning')

        is_active = (
            (self._active_mode == 'winmobile' and self.wmdc_monitor and self.wmdc_monitor.is_running())
            or (self._active_mode == 'android' and self.relay_manager.is_running())
        )
        if is_active:
            self.update_status('waiting')

    def update_status(self, status: str):
        """Update status indicator."""
        self.status = status
        self.status_dot.config(fg=STATUS_COLORS.get(status, '#888888'))
        self.status_label.config(text=STATUS_LABELS.get(status, status))

    def log(self, message: str, level: str = 'info'):
        """Add timestamped message to log panel."""
        timestamp = datetime.now().strftime('[%H:%M:%S]')
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, timestamp + ' ', 'timestamp')
        self.log_text.insert(tk.END, message + '\n', level)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _export_logs(self):
        """Export log contents to a text file."""
        # Get current log content
        log_content = self.log_text.get('1.0', tk.END).strip()

        if not log_content:
            self.log("No logs to export", 'warning')
            return

        # Generate default filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        default_filename = f"usb_relay_log_{timestamp}.txt"

        # Open save dialog
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=default_filename,
            title="Export Logs"
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    # Add header
                    f.write("USB Relay Manager - Log Export\n")
                    f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write("=" * 50 + "\n\n")
                    f.write(log_content)
                self.log(f"Logs exported to {file_path}", 'success')
            except Exception as e:
                self.log(f"Export failed: {e}", 'error')

    def run(self):
        """Start the application."""
        self.log("USB Relay Manager started", 'info')
        self.log("Click START to begin", 'info')

        # Auto-start relay on launch
        self.root.after(500, self._on_start)

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.root.mainloop()

    def _stop_managers_async(self, then=None):
        """Stop active managers in a background thread to avoid GUI freeze.

        Args:
            then: Optional callback to run on the main thread after stopping.
        """
        active = self._active_mode
        self._active_mode = None
        self.start_btn.set_enabled(False)
        self.stop_btn.set_enabled(False)
        self.update_status('stopped')
        self.device_label.config(text="Device: None")

        def _do_stop():
            if active == 'winmobile':
                if self.wmdc_monitor:
                    self.wmdc_monitor.stop()
            else:
                self.relay_manager.stop()
                self.adb_monitor.stop()

            # Schedule follow-up on main thread
            if then:
                self.root.after(0, then)
            else:
                self.root.after(0, lambda: self.start_btn.set_enabled(True))

        threading.Thread(target=_do_stop, daemon=True).start()

    def _on_close(self):
        """Handle window close."""
        self.log("Shutting down...", 'info')

        # Stop relay first
        if self.relay_manager and self.relay_manager.is_running():
            self.relay_manager.stop()

        # Stop ADB monitor and kill ADB server
        if self.adb_monitor:
            self.adb_monitor.stop(kill_server=True)

        # Stop Windows Mobile monitor
        if self.wmdc_monitor and self.wmdc_monitor.is_running():
            self.wmdc_monitor.stop()

        self.root.destroy()
