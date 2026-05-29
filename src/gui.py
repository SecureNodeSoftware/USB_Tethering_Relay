#!/usr/bin/env python3
"""
USB Relay Manager - GUI Module

Provides the tkinter-based graphical interface with
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

# Optional: drag-and-drop support for the Files tab. tkinterdnd2 wraps
# the Tcl/Tk TkDnD extension, which provides OS-level file drop events
# that stock Tkinter does not expose. If the package is not installed,
# the tool falls back to the "Add Files" button as the only input path.
# Packaged builds bundle tkinterdnd2 via the .spec files.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    TkinterDnD = None
    DND_FILES = None
    _DND_AVAILABLE = False


IS_WINDOWS = sys.platform == 'win32'

try:
    from build_config import ENABLED_MODES
except ImportError:
    ENABLED_MODES = ['android', 'winmobile']

# Brand Colors
BRAND_BLUE = '#4169E1'  # Royal Blue
BRAND_BLUE_DARK = '#2850b8'

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

        self._pressed = False

        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)

    def _draw(self, hover=False, pressed=False):
        """Draw the rounded button."""
        self.delete('all')

        if not self._enabled:
            color = self.disabled_bg
        elif pressed:
            # Darken more on press
            color = self._darken_color(self._darken_color(self.bg_color))
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

    def _on_press(self, event):
        if self._enabled:
            self._pressed = True
            self._draw(pressed=True)

    def _on_release(self, event):
        if self._enabled and self._pressed:
            self._pressed = False
            self._draw(hover=True)
            if self.command:
                self.command()
        self._pressed = False

    def _on_enter(self, event):
        if self._enabled:
            self.config(cursor='hand2')
            self._draw(hover=True, pressed=self._pressed)

    def _on_leave(self, event):
        self._pressed = False
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

        # Use TkinterDnD.Tk if the optional package is available AND its
        # native Tcl extension actually loads at runtime. On failure at
        # either stage, fall back to plain tk.Tk — the GUI works
        # identically, it just can't receive OS file drops.
        self._dnd_enabled = False
        if _DND_AVAILABLE:
            try:
                self.root = TkinterDnD.Tk()
                self._dnd_enabled = True
            except Exception as e:
                # Log to stderr instead of self.log — the log widget
                # doesn't exist yet this early in construction.
                print(f"Drag-and-drop disabled ({e}); falling back to tk.Tk",
                      file=sys.stderr)
                self.root = tk.Tk()
        else:
            self.root = tk.Tk()

        self.root.title("USB Relay Manager")
        self.root.geometry("400x380")
        self.root.resizable(False, True)

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
        # Main container - fill horizontally only, pack at top
        main_frame = tk.Frame(self.root, bg=BG_COLOR)
        main_frame.pack(fill=tk.X, padx=20, pady=0)

        # Logo Section
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

        # Device mode selector — only show modes enabled by the build config
        android_enabled = 'android' in ENABLED_MODES
        winmobile_enabled = 'winmobile' in ENABLED_MODES and IS_WINDOWS

        # Pick a sensible default
        if android_enabled:
            default_mode = 'android'
        elif winmobile_enabled:
            default_mode = 'winmobile'
        else:
            default_mode = 'android'

        self.device_mode = tk.StringVar(value=default_mode)

        # Only show radio buttons when more than one mode is available
        show_radios = android_enabled and winmobile_enabled

        if show_radios:
            mode_frame = tk.Frame(main_frame, bg=BG_COLOR)
            mode_frame.pack(pady=(5, 0))

            tk.Radiobutton(
                mode_frame,
                text="Android",
                variable=self.device_mode,
                value='android',
                font=('Arial', 11),
                bg=BG_COLOR,
                fg=TEXT_COLOR,
                selectcolor=BG_COLOR,
                activebackground=BG_COLOR,
                command=self._on_mode_change
            ).pack(side=tk.LEFT, padx=(0, 10))

            tk.Radiobutton(
                mode_frame,
                text="Windows Mobile",
                variable=self.device_mode,
                value='winmobile',
                font=('Arial', 11),
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

        # Notebook panel: Log and Files share the same space as two tabs.
        # Defaults to Log; the Files tab is where the user adds/removes
        # selection and clicks UPLOAD. The old stacked layout (file list
        # packed below the log with a header link) was confusing because
        # the Upload Files link sat above the log even when no files were
        # selected. Tabs make the mode switch obvious.
        log_frame = tk.Frame(self.root, bg=BG_COLOR)
        log_frame.pack(pady=(5, 20), padx=20, fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(log_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # ----- LOG TAB -----
        log_tab = tk.Frame(self.notebook, bg=BG_COLOR)
        self.notebook.add(log_tab, text='Log')

        # Export Logs link (right-aligned above the log text).
        log_header = tk.Frame(log_tab, bg=BG_COLOR)
        log_header.pack(fill=tk.X, padx=5, pady=(5, 0))

        export_label = tk.Label(
            log_header,
            text="Export Logs",
            font=('Arial', 9, 'underline'),
            fg=BRAND_BLUE,
            bg=BG_COLOR,
            cursor='hand2'
        )
        export_label.pack(side=tk.RIGHT)
        export_label.bind('<Button-1>', lambda e: self._export_logs())
        export_label.bind('<Enter>', lambda e: export_label.config(fg=BRAND_BLUE_DARK))
        export_label.bind('<Leave>', lambda e: export_label.config(fg=BRAND_BLUE))

        clear_log_label = tk.Label(
            log_header,
            text="Clear",
            font=('Arial', 9, 'underline'),
            fg=BRAND_BLUE,
            bg=BG_COLOR,
            cursor='hand2'
        )
        clear_log_label.pack(side=tk.RIGHT, padx=(0, 10))
        clear_log_label.bind('<Button-1>', lambda e: self._clear_logs())
        clear_log_label.bind('<Enter>', lambda e: clear_log_label.config(fg=BRAND_BLUE_DARK))
        clear_log_label.bind('<Leave>', lambda e: clear_log_label.config(fg=BRAND_BLUE))

        log_border = tk.Frame(log_tab, relief=tk.SOLID, borderwidth=1)
        log_border.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 5))

        self.log_text = scrolledtext.ScrolledText(
            log_border,
            width=55,
            font=('Consolas', 9),
            bg=LOG_BG,
            fg=LOG_TEXT,
            insertbackground='black',
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # ----- FILES TAB -----
        files_tab = tk.Frame(self.notebook, bg=BG_COLOR)
        self.notebook.add(files_tab, text='Files')

        # File list header: count label on the left, action links on the right.
        file_list_header = tk.Frame(files_tab, bg=BG_COLOR)
        file_list_header.pack(fill=tk.X, padx=5, pady=(5, 0))

        self.file_count_label = tk.Label(
            file_list_header,
            text="No files selected",
            font=('Arial', 9),
            fg=TEXT_SECONDARY,
            bg=BG_COLOR
        )
        self.file_count_label.pack(side=tk.LEFT)

        # Clear All link
        self.clear_files_label = tk.Label(
            file_list_header,
            text="Clear All",
            font=('Arial', 9, 'underline'),
            fg=BRAND_BLUE,
            bg=BG_COLOR,
            cursor='hand2'
        )
        self.clear_files_label.pack(side=tk.RIGHT)
        self.clear_files_label.bind('<Button-1>', lambda e: self._clear_upload_files())
        self.clear_files_label.bind('<Enter>', lambda e: self.clear_files_label.config(fg=BRAND_BLUE_DARK))
        self.clear_files_label.bind('<Leave>', lambda e: self.clear_files_label.config(fg=BRAND_BLUE))

        # Remove Selected link
        self.remove_file_label = tk.Label(
            file_list_header,
            text="Remove",
            font=('Arial', 9, 'underline'),
            fg=BRAND_BLUE,
            bg=BG_COLOR,
            cursor='hand2'
        )
        self.remove_file_label.pack(side=tk.RIGHT, padx=(0, 10))
        self.remove_file_label.bind('<Button-1>', lambda e: self._remove_selected_files())
        self.remove_file_label.bind('<Enter>', lambda e: self.remove_file_label.config(fg=BRAND_BLUE_DARK))
        self.remove_file_label.bind('<Leave>', lambda e: self.remove_file_label.config(fg=BRAND_BLUE))

        # Add Files link (replaces the old log-header link).
        self.add_files_label = tk.Label(
            file_list_header,
            text="Add Files",
            font=('Arial', 9, 'underline'),
            fg=BRAND_BLUE,
            bg=BG_COLOR,
            cursor='hand2'
        )
        self.add_files_label.pack(side=tk.RIGHT, padx=(0, 10))
        self.add_files_label.bind('<Button-1>', lambda e: self._select_upload_files())
        self.add_files_label.bind('<Enter>', lambda e: self.add_files_label.config(fg=BRAND_BLUE_DARK))
        self.add_files_label.bind('<Leave>', lambda e: self.add_files_label.config(fg=BRAND_BLUE))

        # File listbox (always visible inside the Files tab; empty when
        # nothing is selected).
        listbox_border = tk.Frame(files_tab, relief=tk.SOLID, borderwidth=1)
        listbox_border.pack(fill=tk.X, padx=5, pady=(3, 5))

        self.upload_file_listbox = tk.Listbox(
            listbox_border,
            height=6,
            font=('Consolas', 9),
            bg=LOG_BG,
            fg=LOG_TEXT,
            selectmode=tk.EXTENDED,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0
        )
        self.upload_file_listbox.pack(fill=tk.X)

        # Upload button row at the bottom of the Files tab.
        upload_btn_frame = tk.Frame(files_tab, bg=BG_COLOR)
        upload_btn_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        self.upload_btn = RoundedButton(
            upload_btn_frame,
            text="UPLOAD",
            command=self._on_upload,
            bg_color=BRAND_BLUE,
            width=80,
            height=30,
            corner_radius=8,
            font=('Arial', 9, 'bold')
        )
        self.upload_btn.pack(side=tk.RIGHT)
        self.upload_btn.set_enabled(False)

        # Retained as an attribute for internal method compatibility; with
        # the tab layout, the file list is always visible inside its tab
        # and no longer needs show/hide at the parent-frame level.
        self.upload_file_frame = files_tab

        # Drag-and-drop registration. Register at the root so drops
        # anywhere on the window add the files; the drop handler
        # auto-switches to the Files tab so the user sees what landed.
        if self._dnd_enabled:
            try:
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind('<<Drop>>', self._on_file_drop)
            except Exception as e:
                print(f"Drop target registration failed ({e}); drag-and-drop disabled",
                      file=sys.stderr)
                self._dnd_enabled = False

        # Progress frame lives BELOW the notebook so it's visible
        # regardless of which tab is active. Hidden until an upload starts.
        self.upload_progress_frame = tk.Frame(log_frame, bg=BG_COLOR)

        self.upload_status_label = tk.Label(
            self.upload_progress_frame,
            text="",
            font=('Arial', 9),
            fg=TEXT_SECONDARY,
            bg=BG_COLOR
        )
        self.upload_status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.upload_progress_bar = ttk.Progressbar(
            self.upload_progress_frame,
            mode='determinate',
            length=200
        )
        self.upload_progress_bar.pack(side=tk.RIGHT, padx=(10, 0))

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
            text="USB Relay Manager",
            font=('Arial', 20, 'bold'),
            fg=BRAND_BLUE,
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
        """Initialize relay, ADB, and (on Windows) WMDC managers.

        Only initializes managers for modes enabled in build_config.
        """
        if 'android' in ENABLED_MODES:
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
                on_log=self._ts_log,
                apk_path=self.resources.get('apk')
            )

            from file_uploader import FileUploader

            self.file_uploader = FileUploader(
                adb_path=self.resources['adb'],
                on_log=self._ts_log,
                on_progress=self._ts_on_upload_progress,
                on_complete=self._ts_on_upload_complete,
                on_overwrite_prompt=self._prompt_overwrite,
            )

        if not hasattr(self, 'file_uploader'):
            self.file_uploader = None

        # Windows Mobile monitor (Windows-only, and only if enabled)
        if 'winmobile' in ENABLED_MODES and IS_WINDOWS:
            try:
                from wmdc_monitor import WMDCMonitor
                self.wmdc_monitor = WMDCMonitor(
                    on_device_connected=self._ts_on_device_connected,
                    on_device_disconnected=self._ts_on_device_disconnected,
                    on_log=self._ts_log
                )
            except ImportError:
                self.wmdc_monitor = None

        # Start ADB device detection up front (relay disabled) when Android
        # is the selected mode, so file transfer is available as soon as a
        # device is connected — no need to start the relay first.
        if self.device_mode.get() == 'android':
            self._ensure_adb_monitoring()

    def _ensure_adb_monitoring(self):
        """Start relay-independent ADB device detection if not already running.

        Detection populates the connected device id used by file transfer,
        regardless of whether the relay is running.
        """
        if self.adb_monitor and not self.adb_monitor.is_running():
            self.adb_monitor.start()

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
            # Detection may already be running (it starts independently of
            # the relay); ensure it is, then enable relay-specific setup so
            # the connected device gets the tunnel/VPN configured.
            self._ensure_adb_monitoring()
            self.adb_monitor.set_relay_enabled(True)
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
            self.device_id = None
            self.device_label.config(text="Device: None")
        else:
            self.log("Stopping relay server...", 'info')
            self.relay_manager.stop()
            # Disable relay-specific setup but keep detecting devices so file
            # transfer stays available without the relay. The device id and
            # upload button are intentionally left intact.
            if self.adb_monitor:
                self.adb_monitor.set_relay_enabled(False)

        self._active_mode = None
        self.update_status('stopped')
        self._update_upload_button_state()


    def _on_mode_change(self):
        """Handle mode radio button change."""
        new_mode = self.device_mode.get()

        if self._active_mode and self._active_mode != new_mode:
            # A mode is actively running — stop current mode in a background
            # thread, then start the new mode on the main thread to avoid
            # freezing the GUI during cleanup.
            self._stop_managers_async(then=self._on_start)
            return

        # Nothing running: just follow the selected mode for background ADB
        # detection so file-transfer availability tracks the chosen mode.
        if new_mode == 'android':
            self._ensure_adb_monitoring()
        elif self.adb_monitor and self.adb_monitor.is_running():
            self.adb_monitor.stop()
            self.device_id = None
            self.device_label.config(text="Device: None")
            self._update_upload_button_state()

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

        self._update_upload_button_state()


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

        self._update_upload_button_state()


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

    def _clear_logs(self):
        """Clear all log text from the log panel."""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
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

    def _select_upload_files(self):
        """Open file picker and add to upload file selection."""
        if not self.file_uploader:
            return
        files = filedialog.askopenfilenames(
            title="Select Files to Upload",
            initialdir=str(Path.home()),
            filetypes=[("All files", "*.*")]
        )
        if files:
            self._add_files_from_paths(files, switch_to_files_tab=False)

    def _on_file_drop(self, event):
        """Handle OS-level file drops (tkinterdnd2 <<Drop>> event).

        `event.data` is a Tcl-formatted list of file paths. The Tcl
        convention wraps paths containing spaces or special characters
        in braces; tk.splitlist parses it safely across platforms.
        After adding, we jump to the Files tab so the user sees what
        they dropped and can review before clicking UPLOAD.
        """
        if not self.file_uploader:
            return
        raw = getattr(event, 'data', '') or ''
        try:
            paths = self.root.tk.splitlist(raw)
        except Exception:
            # Last-ditch fallback: whitespace-split. Paths with spaces
            # may break, but at least something lands in the listbox.
            paths = raw.split()
        self._add_files_from_paths(paths, switch_to_files_tab=True)

    def _add_files_from_paths(self, paths, switch_to_files_tab: bool):
        """Filter a raw path list down to real files and add to the uploader.

        Shared by the file picker and the drag-and-drop handler. Drops
        non-existent paths, directories, and anything the filesystem
        rejects. The file picker doesn't need the tab-switch (user is
        already on the Files tab to click Add Files); the drop handler
        does because the drop might have happened on any tab.
        """
        if not self.file_uploader:
            return
        valid = []
        skipped = 0
        for p in paths:
            try:
                path = Path(str(p)).expanduser()
                if path.is_file():
                    valid.append(path)
                else:
                    skipped += 1
            except (OSError, ValueError):
                skipped += 1
        if valid:
            self.file_uploader.add_files(valid)
            self._refresh_file_list()
            if switch_to_files_tab:
                # Index 1 = Files tab (0 is Log).
                self.notebook.select(1)
        if skipped:
            self.log(
                f"Skipped {skipped} dropped item(s) that were not regular files",
                'warning',
            )

    def _remove_selected_files(self):
        """Remove selected files from the upload list."""
        if not self.file_uploader:
            return
        selected_indices = self.upload_file_listbox.curselection()
        if not selected_indices:
            return
        # Remove in reverse order to keep indices valid
        files = self.file_uploader.get_files()
        for i in sorted(selected_indices, reverse=True):
            if i < len(files):
                self.file_uploader.remove_file(files[i])
        self._refresh_file_list()

    def _clear_upload_files(self):
        """Clear the upload file selection."""
        if not self.file_uploader:
            return
        self.file_uploader.clear_files()
        self._refresh_file_list()

    def _refresh_file_list(self):
        """Update the file listbox, count label, and button state.

        The Files tab is always visible in the notebook, so this no
        longer needs to pack/unpack a frame or resize the root window —
        it just repopulates the listbox and updates the count label.
        """
        files = self.file_uploader.get_files()
        count = len(files)

        self.upload_file_listbox.delete(0, tk.END)
        for f in files:
            self.upload_file_listbox.insert(tk.END, f.name)

        if count > 0:
            self.file_count_label.config(
                text=f"{count} file{'s' if count != 1 else ''} selected"
            )
        else:
            empty_text = (
                "Drop files here or click Add Files"
                if self._dnd_enabled
                else "No files selected"
            )
            self.file_count_label.config(text=empty_text)

        self._update_upload_button_state()

    def _update_upload_button_state(self):
        """Enable/disable upload button based on device connection and file selection."""
        if self.file_uploader is None:
            return
        enabled = (
            self.device_id is not None
            and self.file_uploader.has_files()
            and not self.file_uploader.is_uploading()
        )
        self.upload_btn.set_enabled(enabled)

    def _on_upload(self):
        """Handle Upload button click — push selected files to /sdcard/Download/."""
        if not self.device_id or not self.file_uploader.has_files():
            return

        # Jump to the Log tab so the user sees per-file progress lines
        # without having to click over manually.
        self.notebook.select(0)

        # Simple push-to-Download flow.
        self.upload_btn.set_enabled(False)
        total = len(self.file_uploader.selected_files)
        self.upload_progress_bar['maximum'] = total
        self.upload_progress_bar['value'] = 0
        self.upload_status_label.config(text=f"Uploading 0/{total} files...")
        self.upload_progress_frame.pack(fill=tk.X, pady=(3, 0))

        self.log(f"Starting upload of {total} file{'s' if total != 1 else ''} to device...", 'info')

        self.file_uploader.upload_async(self.device_id)

    # -- Upload progress callbacks --

    def _ts_on_upload_progress(self, current: int, total: int):
        self.root.after(0, self._on_upload_progress, current, total)

    def _ts_on_upload_complete(self, success: int, total: int):
        self.root.after(0, self._on_upload_complete, success, total)

    def _on_upload_progress(self, current: int, total: int):
        """Handle upload progress update."""
        self.upload_progress_bar['value'] = current
        self.upload_status_label.config(text=f"Uploading {current}/{total} files...")

    def _on_upload_complete(self, success: int, total: int):
        """Handle upload completion."""
        self.upload_status_label.config(
            text=f"Upload complete — {success}/{total} files transferred"
        )
        self.log(f"Upload complete — {success}/{total} files transferred",
                 'success' if success == total else 'warning')

        # Auto-clear progress after 5 seconds
        self.root.after(5000, self._hide_upload_progress)

        self._update_upload_button_state()

    def _hide_upload_progress(self):
        """Hide the progress bar and status label."""
        self.upload_progress_frame.pack_forget()
        self.upload_status_label.config(text="")
        self.upload_progress_bar['value'] = 0

    def _prompt_overwrite(self, filename: str) -> bool:
        """Prompt user to overwrite a file on the device. Thread-safe."""
        result = [False]
        event = threading.Event()

        def _ask():
            from tkinter import messagebox
            result[0] = messagebox.askyesno(
                "File Exists",
                f"{filename} already exists on the device.\n\nOverwrite?"
            )
            event.set()

        self.root.after(0, _ask)
        event.wait()
        return result[0]

    def run(self):
        """Start the application."""
        self.log("USB Relay Manager started", 'info')
        self.log("Click START to begin relaying", 'info')
        self.log("File transfer is available whenever a device is connected — "
                 "the relay does not need to be running", 'info')

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

        # Log if upload was in progress (daemon thread will be killed on exit)
        if self.file_uploader and self.file_uploader.is_uploading():
            self.log("Upload in progress — closing anyway", 'warning')

        self.root.destroy()
