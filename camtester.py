#!/usr/bin/env python3
"""
CamTester - NDI & RTSP Camera Testing Tool
For Raspberry Pi 5 + Touch Display 2 (5") + Stream Deck
Resolution: 1280x720 (landscape rotated)
"""

import tkinter as tk
from tkinter import font as tkfont
import subprocess
import threading
import socket
import json
import time
import os
import signal
import sys
import netifaces
from PIL import Image, ImageDraw, ImageFont

APP_VERSION = "1.4.5"

# ─────────────────────────────────────────────
#  CONFIGURATION — edit these to match your setup
# ─────────────────────────────────────────────
SCAN_SUBNET      = "10.0.100"       # First 3 octets of your PoE network
SCAN_TIMEOUT     = 10               # Seconds to wait for network scan
RTSP_PORT        = 554
RTSP_PATHS       = [                # Common RTSP stream paths to try — Hanwha first
    "/profile2/media.smp",          # Hanwha H.264 30fps (preferred)
    "/profile1/media.smp",          # Hanwha MJPEG fallback
    "/stream",
    "/live",
    "/live/ch0",
    "/h264Preview_01_main",
    "/cam/realmonitor?channel=1&subtype=0",
    "/video1",
    "/ch001.264",
    "/MediaInput/h264",
    "/1",
    "/stream1",
]
RTSP_CREDENTIALS = [                # username:password combos to try
    ("admin", "RoboSpot10"),        # Robe RoboSpot factory default
    ("admin", "Repair2023!"),       # Christie Lites shop default
    ("admin", "4321"),              # Hanwha factory default
    ("admin", ""),                  # Blank password
    ("admin", "admin"),
    ("admin", "12345"),
    ("admin", "123456"),
    ("root", "root"),
    ("user", "user"),
    ("", ""),
]
STREAM_DECK_DEVICE = "/dev/input/by-id/usb-Elgato_Stream_Deck_*"

# ─────────────────────────────────────────────
#  COLOURS & FONTS — Christie Lites Brand Theme
# ─────────────────────────────────────────────
BG_DARK      = "#0a0a0a"       # Near-black background
BG_CARD      = "#141414"       # Card surface
BG_CARD2     = "#1e1e1e"       # Selected card
BG_HEADER    = "#1a1a1a"       # Header bar
ACCENT       = "#cc0000"       # Christie Lites red
ACCENT2      = "#a30000"       # Darker red
ACCENT_LIGHT = "#ff1a1a"       # Lighter red for highlights
SUCCESS      = "#2ecc71"       # Green — stream active
WARNING      = "#f39c12"       # Amber — warning
DANGER       = "#cc0000"       # Red — stop/reboot
TEXT_PRIMARY = "#ffffff"       # White
TEXT_DIM     = "#666666"       # Dimmed text
TEXT_MID     = "#aaaaaa"       # Mid text
BORDER       = "#2a2a2a"       # Subtle border

# ─────────────────────────────────────────────
#  VISCA ZOOM CONTROLLER
# ─────────────────────────────────────────────
class VISCAZoomController:
    """
    Controls camera zoom via VISCA over IP (TCP port 52381).
    Sends continuous zoom commands while button is held,
    stops when button is released.
    """
    VISCA_PORT = 52381
    ZOOM_MAX   = 16384  # 0x4000 standard VISCA max zoom

    CMD_ZOOM_TELE = bytes([0x81, 0x01, 0x04, 0x07, 0x02, 0xFF])
    CMD_ZOOM_WIDE = bytes([0x81, 0x01, 0x04, 0x07, 0x03, 0xFF])
    CMD_ZOOM_STOP = bytes([0x81, 0x01, 0x04, 0x07, 0x00, 0xFF])
    CMD_ZOOM_INQ  = bytes([0x81, 0x09, 0x04, 0x47, 0xFF])

    def __init__(self, ip, zoom_update_cb=None, connect_delay=0):
        self.ip             = ip
        self.zoom_update_cb = zoom_update_cb
        self._sock          = None
        self._zooming       = False
        self._lock          = threading.Lock()
        self._connect(connect_delay)

    def _connect(self, delay=0):
        """Connect to VISCA port in background, optionally after a delay."""
        def _try():
            if delay:
                time.sleep(delay)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect((self.ip, self.VISCA_PORT))
                s.settimeout(0.5)
                with self._lock:
                    self._sock = s
                print(f"VISCA connected to {self.ip}:{self.VISCA_PORT}")
                self._poll_zoom_position()
            except Exception as e:
                print(f"VISCA connection failed: {e}")
        threading.Thread(target=_try, daemon=True).start()

    def _send(self, cmd):
        """Send a VISCA command and return response."""
        with self._lock:
            if not self._sock:
                return None
            try:
                self._sock.send(cmd)
                return self._sock.recv(16)
            except Exception:
                self._sock = None
                return None

    def _parse_zoom_position(self, response):
        """Extract zoom position from VISCA inquiry response."""
        if not response or len(response) < 7:
            return None
        # Response: 90 50 0p 0q 0r 0s FF
        try:
            p = (response[2] & 0x0F) << 12
            q = (response[3] & 0x0F) << 8
            r = (response[4] & 0x0F) << 4
            s = (response[5] & 0x0F)
            return p | q | r | s
        except Exception:
            return None

    def _poll_zoom_position(self):
        """Poll zoom position every 500ms and update display."""
        def _poll():
            while self._sock:
                resp = self._send(self.CMD_ZOOM_INQ)
                pos  = self._parse_zoom_position(resp)
                if pos is not None and self.zoom_update_cb:
                    self.zoom_update_cb(pos, self.ZOOM_MAX)
                time.sleep(0.5)
        threading.Thread(target=_poll, daemon=True).start()

    def start_zoom(self, direction):
        """Start continuous zoom. direction: 'tele' or 'wide'."""
        if self._zooming:
            return
        self._zooming = True
        cmd = self.CMD_ZOOM_TELE if direction == "tele" else self.CMD_ZOOM_WIDE

        def _zoom():
            self._send(cmd)
        threading.Thread(target=_zoom, daemon=True).start()

    def stop_zoom(self):
        """Stop zoom."""
        self._zooming = False
        threading.Thread(target=lambda: self._send(self.CMD_ZOOM_STOP),
                         daemon=True).start()

    def close(self):
        self.stop_zoom()
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None


# ─────────────────────────────────────────────
#  HANWHA ZOOM CONTROLLER (RTSP cameras)
# ─────────────────────────────────────────────
class HanwhaZoomController:
    """
    Controls zoom on Hanwha/Wisenet RTSP cameras via the ptzcontrol.cgi API.
    Uses continuous zoom — send speed to start, send 0 to stop.
    Mirrors the VISCAZoomController interface so playback code is the same.
    """
    def __init__(self, ip, user, pwd, zoom_update_cb=None):
        self.ip             = ip
        self.user           = user
        self.pwd            = pwd
        self.zoom_update_cb = zoom_update_cb   # not used — Hanwha gives no zoom position
        self._zooming       = False

    def _send(self, zoom_val):
        url = (f"http://{self.ip}/stw-cgi/ptzcontrol.cgi"
               f"?msubmenu=continuous&action=control&Channel=0"
               f"&NormalizedSpeed=True&Zoom={zoom_val}")
        try:
            subprocess.run(
                ["curl", "-s", "--digest", "-u", f"{self.user}:{self.pwd}",
                 "--max-time", "3", url],
                capture_output=True, timeout=4
            )
        except Exception as e:
            print(f"Hanwha zoom error: {e}")

    def start_zoom(self, direction):
        """direction: 'tele' (in) or 'wide' (out)."""
        if self._zooming:
            return
        self._zooming = True
        speed = 50 if direction == "tele" else -50
        threading.Thread(target=lambda: self._send(speed), daemon=True).start()

    def stop_zoom(self):
        self._zooming = False
        threading.Thread(target=lambda: self._send(0), daemon=True).start()

    def close(self):
        self.stop_zoom()


# ─────────────────────────────────────────────
#  STREAM DECK MANAGER
# ─────────────────────────────────────────────
class StreamDeckManager:
    """
    Manages the Stream Deck Mini directly via python-elgato-streamdeck.
    Renders button images and handles key press callbacks.
    Button layout (Mini, 2 rows x 3 cols):
      [1][2][3]
      [4][5][6]
    """

    BTN_SIZE   = 80      # pixels
    BTN_ROTATE = 0       # degrees — no rotation needed for this unit

    # Button colour schemes: (background, text)
    STYLE = {
        "active":    ("#cc0000", "#ffffff"),   # Christie red
        "action":    ("#cc0000", "#ffffff"),   # Christie red
        "danger":    ("#cc0000", "#ffffff"),   # Red — reboot/stop
        "success":   ("#2ecc71", "#000000"),   # Green — connect
        "inactive":  ("#0a0a0a", "#333333"),   # Black — unused
        "warning":   ("#f39c12", "#000000"),   # Amber — back/home
        "reboot":    ("#cc0000", "#ffffff"),   # Red — reboot
    }

    def __init__(self, callback, combo_callback=None, release_callback=None):
        self.callback         = callback
        self.combo_callback   = combo_callback
        self.release_callback = release_callback
        self.deck             = None
        self._running         = False
        self._lock            = threading.Lock()
        self._font            = None
        self._held            = set()
        self._connect()

    def _connect(self):
        """Try to open the Stream Deck. Retries in background if not found."""
        def _try():
            while not self._running:
                try:
                    from StreamDeck.DeviceManager import DeviceManager
                    decks = DeviceManager().enumerate()
                    if decks:
                        self.deck = decks[0]
                        self.deck.open()
                        self.deck.reset()
                        self.deck.set_brightness(80)
                        self.deck.set_key_callback(self._on_key)
                        self._running = True
                        print(f"Stream Deck connected: {self.deck.deck_type()}")
                        return
                except Exception as e:
                    print(f"Stream Deck not ready: {e}")
                time.sleep(3)

        threading.Thread(target=_try, daemon=True).start()

    def _on_key(self, deck, key_index, state):
        """Called by the library on any key event."""
        btn = key_index + 1  # convert to 1-based

        if state:  # button pressed
            self._held.add(btn)
            # Check for combo — fire if 2+ buttons held
            if len(self._held) >= 2 and self.combo_callback:
                self.combo_callback(frozenset(self._held))
            else:
                # Single press — fire after short delay to allow combo detection
                held_snapshot = frozenset(self._held)
                def _delayed_single(b=btn, snap=held_snapshot):
                    import time
                    time.sleep(0.08)
                    if snap == self._held:  # no other button was pressed
                        self.callback(b)
                threading.Thread(target=_delayed_single, daemon=True).start()
        else:  # button released
            self._held.discard(btn)
            if self.release_callback:
                self.release_callback(btn)

    def _get_font(self, size=16):
        """Load a font for button labels, falling back to default."""
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
        except Exception:
            try:
                return ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", size)
            except Exception:
                return ImageFont.load_default()

    def _make_button_image(self, label, style="active", icon=None):
        """
        Render an 80x80 BMP image for a button.
        Supports a two-line label split by newline.
        """
        bg_col, fg_col = self.STYLE.get(style, self.STYLE["active"])
        img  = Image.new("RGB", (self.BTN_SIZE, self.BTN_SIZE), bg_col)
        draw = ImageDraw.Draw(img)

        if not label:
            img = img.rotate(self.BTN_ROTATE)
            return img

        lines = label.split("\n")

        if len(lines) == 1:
            font = self._get_font(18 if len(label) <= 6 else 14)
            bbox = draw.textbbox((0, 0), label, font=font)
            tw   = bbox[2] - bbox[0]
            th   = bbox[3] - bbox[1]
            x    = (self.BTN_SIZE - tw) // 2
            y    = (self.BTN_SIZE - th) // 2
            draw.text((x, y), label, font=font, fill=fg_col)
        else:
            # Two lines — top line smaller, bottom line larger
            font_top = self._get_font(12)
            font_bot = self._get_font(16)
            # Top line
            bb  = draw.textbbox((0, 0), lines[0], font=font_top)
            tw  = bb[2] - bb[0]
            draw.text(((self.BTN_SIZE - tw) // 2, 14), lines[0],
                      font=font_top, fill=fg_col)
            # Bottom line
            bb  = draw.textbbox((0, 0), lines[1], font=font_bot)
            tw  = bb[2] - bb[0]
            draw.text(((self.BTN_SIZE - tw) // 2, 42), lines[1],
                      font=font_bot, fill=fg_col)

        img = img.rotate(self.BTN_ROTATE)
        return img

    def set_buttons(self, mapping):
        """
        Update all 6 buttons.
        mapping: dict of {button_number: (label, style)}
        e.g. {1: ("SCAN", "action"), 6: ("QUIT", "danger")}
        Buttons not in mapping are set to inactive/blank.
        """
        if not self._running or not self.deck:
            return
        with self._lock:
            for btn in range(1, 7):
                config = mapping.get(btn)
                if config:
                    label, style = config
                    img = self._make_button_image(label, style)
                else:
                    img = self._make_button_image("", "inactive")
                try:
                    from StreamDeck.ImageHelpers import PILHelper
                    native = PILHelper.to_native_format(self.deck, img)
                    self.deck.set_key_image(btn - 1, native)
                except Exception as e:
                    print(f"Button image error: {e}")

    def clear(self):
        """Turn off all buttons."""
        if self._running and self.deck:
            with self._lock:
                try:
                    self.deck.reset()
                except Exception:
                    pass

    def close(self):
        self._running = False
        if self.deck:
            try:
                self.deck.reset()
                self.deck.close()
            except Exception:
                pass


# ─────────────────────────────────────────────
#  MAIN APPLICATION
# ─────────────────────────────────────────────
class CamTesterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CamTester")
        self.geometry("1280x720")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)

        # Try to go fullscreen
        try:
            self.attributes("-fullscreen", True)
        except Exception:
            pass

        self.overrideredirect(True)   # Remove window decorations

        # State
        self.mpv_process     = None
        self.scan_thread     = None
        self.scanning        = False
        self.found_cameras   = []
        self.selected_index  = 0
        self.current_screen  = "home"

        # NDI playback state
        self.ndi_running     = False
        self.ndi_thread      = None
        self.ndi_canvas      = None
        self.ndi_photo       = None

        # Health check state
        self._health_check_running = False
        self._pending_update_bytes = None

        # Stream Deck — connects in background, won't block startup
        self.sd = StreamDeckManager(callback=self.sd_button,
                                    combo_callback=self.sd_combo,
                                    release_callback=self.sd_release)

        # Fonts
        self.font_xl    = tkfont.Font(family="DejaVu Sans", size=28, weight="bold")
        self.font_lg    = tkfont.Font(family="DejaVu Sans", size=18, weight="bold")
        self.font_md    = tkfont.Font(family="DejaVu Sans", size=14)
        self.font_sm    = tkfont.Font(family="DejaVu Sans", size=11)
        self.font_xs    = tkfont.Font(family="DejaVu Sans", size=9)

        # Container frame — all screens live inside this
        self.container = tk.Frame(self, bg=BG_DARK)
        self.container.pack(fill="both", expand=True)

        self.pi_ip = "Waiting for network…"
        self.show_splash()

        # Keyboard bindings kept as fallback (F1-F6)
        self.bind("<F1>", lambda e: self.sd_button(1))
        self.bind("<F2>", lambda e: self.sd_button(2))
        self.bind("<F3>", lambda e: self.sd_button(3))
        self.bind("<F4>", lambda e: self.sd_button(4))
        self.bind("<F5>", lambda e: self.sd_button(5))
        self.bind("<F6>", lambda e: self.sd_button(6))
        self.bind("<Escape>", lambda e: self.go_back())
        self.focus_set()

    def sd_release(self, n):
        """Handle Stream Deck button release."""
        if self.current_screen == "playback":
            if n in (1, 4):
                if hasattr(self, "_zoom_controller") and self._zoom_controller:
                    self._zoom_controller.stop_zoom()

    def sd_combo(self, buttons):
        """Handle Stream Deck button combos."""
        # Bottom row (4+5+6) = hidden update trigger
        if frozenset({4, 5, 6}).issubset(buttons):
            if self.current_screen == "home":
                self.after(0, self._confirm_update)

    def _confirm_update(self):
        """Show update confirmation before proceeding."""
        self.current_screen = "update_confirm"
        self.clear_container()

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        tk.Label(centre, text="Check for Update?",
                 font=self.font_xl, bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.30, anchor="center")
        tk.Label(centre, text="The device will connect to the internet to check for updates.",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.43, anchor="center")
        tk.Label(centre, text="Make sure the Pi is plugged into a network with internet access.",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.51, anchor="center")

        btn_frame = tk.Frame(centre, bg=BG_DARK)
        btn_frame.place(relx=0.5, rely=0.68, anchor="center")

        tk.Button(btn_frame, text="  CHECK FOR UPDATE  ", font=self.font_lg,
                  bg=ACCENT, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=self.check_for_update).pack(side="left", padx=20)
        tk.Button(btn_frame, text="  CANCEL  ", font=self.font_lg,
                  bg=BG_CARD2, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=self.show_home).pack(side="left", padx=20)

        self._draw_sd_hints(self.container, {1: "UPDATE", 6: "CANCEL"})
    #  Map your 6 buttons here. BitFocus Companion
    #  should run: xdotool key F1  (through F6)
    # ─────────────────────────────────────────
    def sd_button(self, n):
        if self.current_screen == "home":
            if n == 1:
                self.start_scan()
            elif n == 3:
                self.reboot_device()

        elif self.current_screen == "update_confirm":
            if n == 1:
                self.check_for_update()
            elif n == 6:
                self.show_home()

        elif self.current_screen == "scanning":
            if n == 6:
                self.cancel_scan()

        elif self.current_screen == "results":
            if n == 1:
                self.start_scan()
            elif n == 4:
                self.connect_selected()
            elif n == 6:
                self.show_home()

        elif self.current_screen == "updating":
            if n == 1:
                # Trigger install if the button is available
                if hasattr(self, "_pending_update_bytes") and self._pending_update_bytes:
                    self._do_update(self._pending_update_bytes)
            elif n == 6:
                self.show_home()

        elif self.current_screen == "reset_success":
            if n == 1:
                self.show_home()

        elif self.current_screen == "manual_setup":
            state = getattr(self, "_setup_state", "initial")
            if state == "initial":
                if n == 1:      # Browser
                    if hasattr(self, "_setup_cam"):
                        self._launch_setup_browser(self._setup_cam)
                elif n == 6:    # Cancel → back to results
                    self.show_results()
            elif state == "browser":
                if n == 2:      # Auto-fill
                    self._autofill_password()
                elif n == 6:    # Cancel → back to results
                    subprocess.run(["pkill", "chromium"], capture_output=True)
                    self.deiconify()
                    self.show_results()
            elif state == "done":
                if n == 1:      # Done → connect directly
                    self._connect_after_setup(self._setup_cam)
                elif n == 6:    # Cancel → back to results
                    self.show_results()

        elif self.current_screen == "reboot":
            if n == 1:
                self._do_reboot()
            elif n == 6:
                self.show_home()

        elif self.current_screen == "reset_confirm":
            if n == 1:
                if hasattr(self, "_current_cam"):
                    self._do_factory_reset(self._current_cam)
            elif n == 6:
                self.current_screen = "playback"
                # Remove overlay by rebuilding playback screen isn't needed
                # just dismiss — the overlay has its own cancel button

        elif self.current_screen == "playback":
            if n == 1:
                if hasattr(self, "_zoom_controller") and self._zoom_controller:
                    self._zoom_controller.start_zoom("tele")
            elif n == 4:
                if hasattr(self, "_zoom_controller") and self._zoom_controller:
                    self._zoom_controller.start_zoom("wide")
            elif n == 5:
                if hasattr(self, "_current_cam"):
                    self._confirm_factory_reset(self._current_cam)
            elif n == 6:
                self.stop_playback()
                self.show_results()

    # ─────────────────────────────────────────
    #  NETWORK HELPER
    # ─────────────────────────────────────────
    def get_ip_address(self, iface="eth0"):
        """Return current IP of eth0. Returns DHCP/static first, link-local as fallback."""
        try:
            # Try routable IP first
            addrs = netifaces.ifaddresses(iface)
            for addr in addrs.get(netifaces.AF_INET, []):
                ip = addr["addr"]
                if not ip.startswith("169.254"):
                    return ip
            # Fall back to link-local
            for addr in addrs.get(netifaces.AF_INET, []):
                return addr["addr"]
        except Exception:
            pass
        return None

    def get_any_ip(self, iface="eth0"):
        """Return any IP including link-local, just to confirm interface is up."""
        try:
            out = subprocess.run(
                ["ip", "-4", "addr", "show", iface],
                capture_output=True, text=True
            )
            import re
            ips = re.findall(r'inet\s+(\d+\.\d+\.\d+\.\d+)', out.stdout)
            return ips[0] if ips else None
        except Exception:
            return None

    # ─────────────────────────────────────────
    #  SCREEN: SPLASH / BOOT
    # ─────────────────────────────────────────
    def show_splash(self):
        self.current_screen = "splash"
        self.clear_container()

        # Red top stripe
        tk.Frame(self.container, bg=ACCENT, height=6).pack(fill="x")

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        # Company name
        tk.Label(centre, text="CHRISTIE LITES",
                 font=self.font_xl, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.25, anchor="center")

        tk.Label(centre, text="Camera Tester",
                 font=self.font_lg, bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.38, anchor="center")

        tk.Label(centre, text=f"v{APP_VERSION}",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.46, anchor="center")

        # Divider
        tk.Frame(centre, bg=ACCENT, height=2, width=400).place(relx=0.5, rely=0.48, anchor="center")

        # Status line
        self.splash_status_var = tk.StringVar(value="Waiting for network…")
        tk.Label(centre, textvariable=self.splash_status_var,
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_MID).place(relx=0.5, rely=0.60, anchor="center")

        # Animated dots
        self.splash_dots_var = tk.StringVar(value="●  ○  ○")
        tk.Label(centre, textvariable=self.splash_dots_var,
                 font=self.font_md, bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.72, anchor="center")

        # Red bottom stripe
        tk.Frame(self.container, bg=ACCENT, height=6).pack(side="bottom", fill="x")

        self._splash_dot_frame = 0
        self._animate_splash_dots()
        self._wait_for_network()

        # Show waiting state on Stream Deck during splash
        threading.Thread(target=lambda: self.sd.set_buttons({
            3: ("WAIT\nNETWORK", "warning"),
        }), daemon=True).start()

    def _animate_splash_dots(self):
        """Cycle through a simple 3-dot animation while waiting."""
        frames = ["●  ○  ○", "○  ●  ○", "○  ○  ●", "○  ●  ○"]
        if self.current_screen != "splash":
            return
        self.splash_dots_var.set(frames[self._splash_dot_frame % len(frames)])
        self._splash_dot_frame += 1
        self.after(400, self._animate_splash_dots)

    def _wait_for_network(self):
        """Show splash for a fixed time then go to home regardless of network state."""
        def _poll():
            # Wait up to 8 seconds for a real IP, then proceed anyway
            for _ in range(16):
                ip = self.get_ip_address("eth0")
                if ip and not ip.startswith("169.254"):
                    self.pi_ip = ip
                    self.after(0, lambda i=ip: self.splash_status_var.set(f"Network ready — {i}"))
                    time.sleep(0.5)
                    self.after(0, self.show_home)
                    return
                time.sleep(0.5)
            # No IP after 8 seconds — go to home anyway
            self.after(0, self.show_home)

        threading.Thread(target=_poll, daemon=True).start()

    # ─────────────────────────────────────────
    #  SCREEN: HOME
    # ─────────────────────────────────────────
    def show_home(self):
        self.current_screen = "home"
        self._stop_health_checks()
        self.stop_playback()
        self.clear_container()

        # Header bar
        header = tk.Frame(self.container, bg=ACCENT, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        # Red left accent stripe
        tk.Frame(header, bg=ACCENT_LIGHT, width=6).pack(side="left", fill="y")

        tk.Label(header, text="CHRISTIE LITES", font=self.font_lg,
                 bg=ACCENT, fg=TEXT_PRIMARY).pack(side="left", padx=16, pady=10)
        tk.Label(header, text="Camera Tester",
                 font=self.font_sm, bg=ACCENT, fg="#ffcccc").pack(side="left", padx=2)
        tk.Label(header, text=f"v{APP_VERSION}",
                 font=self.font_xs, bg=ACCENT2, fg="#ffcccc",
                 padx=8, pady=4).pack(side="left", padx=8)

        # Pi IP address — right side of header
        self.home_ip_var = tk.StringVar(value=f"IP:  {self.pi_ip}")
        tk.Label(header, textvariable=self.home_ip_var,
                 font=self.font_sm, bg=ACCENT, fg="#ffcccc").pack(side="right", padx=20)
        self._refresh_home_ip()

        # Centre content
        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        # Big scan button
        scan_frame = tk.Frame(centre, bg=BG_DARK)
        scan_frame.place(relx=0.5, rely=0.42, anchor="center")

        scan_btn = tk.Button(
            scan_frame,
            text="SCAN FOR CAMERAS",
            font=self.font_xl,
            bg=ACCENT, fg=TEXT_PRIMARY,
            activebackground=ACCENT2,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            padx=40, pady=22,
            cursor="hand2",
            command=self.start_scan
        )
        scan_btn.pack()

        tk.Label(centre, text="NDI + RTSP  ·  Tap to scan the network",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.62, anchor="center")

        # Stream Deck hint bar — delayed slightly to ensure previous state clears
        self.after(300, lambda: self._draw_sd_hints(self.container, {1: "SCAN", 3: "REBOOT"}))

    def _refresh_home_ip(self):
        """Keep the IP label on the home screen up to date every 5 seconds."""
        if self.current_screen != "home":
            return
        ip = self.get_ip_address("eth0")
        if ip:
            self.pi_ip = ip
            self._no_ip_count = 0
            if hasattr(self, "home_ip_var"):
                self.home_ip_var.set(f"IP:  {ip}")
        else:
            # Only show "No network" after 3 consecutive misses (~15 seconds)
            # so brief DHCP retries don't cause a flicker
            self._no_ip_count = getattr(self, "_no_ip_count", 0) + 1
            if self._no_ip_count >= 3:
                if hasattr(self, "home_ip_var"):
                    self.home_ip_var.set("IP:  No network")
        self.after(5000, self._refresh_home_ip)

    # ─────────────────────────────────────────
    #  SCREEN: SCANNING (overlay on home)
    # ─────────────────────────────────────────
    def show_scanning(self):
        self.current_screen = "scanning"
        self.clear_container()

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        self.scan_status_var = tk.StringVar(value="Initialising scan…")
        self.scan_detail_var = tk.StringVar(value="")
        self.scan_ndi_var    = tk.StringVar(value="NDI:  searching…")
        self.scan_rtsp_var   = tk.StringVar(value="RTSP: searching…")

        tk.Label(centre, text="Scanning…", font=self.font_xl,
                 bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.25, anchor="center")

        tk.Label(centre, textvariable=self.scan_status_var,
                 font=self.font_md, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.40, anchor="center")

        tk.Label(centre, textvariable=self.scan_ndi_var,
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.52, anchor="center")

        tk.Label(centre, textvariable=self.scan_rtsp_var,
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.60, anchor="center")

        tk.Label(centre, textvariable=self.scan_detail_var,
                 font=self.font_xs, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.70, anchor="center")

        cancel_btn = tk.Button(
            centre, text="✕  Cancel", font=self.font_sm,
            bg=BG_CARD, fg=TEXT_DIM, relief="flat", padx=20, pady=8,
            command=self.cancel_scan
        )
        cancel_btn.place(relx=0.5, rely=0.85, anchor="center")

        self._draw_sd_hints(self.container, {6: "CANCEL"})

    # ─────────────────────────────────────────
    #  SCREEN: RESULTS
    # ─────────────────────────────────────────
    def show_results(self):
        self.current_screen = "results"
        self._stop_health_checks()   # stop any existing checker first
        self.clear_container()

        # Header
        header = tk.Frame(self.container, bg=ACCENT, height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Frame(header, bg=ACCENT_LIGHT, width=6).pack(side="left", fill="y")
        count = len(self.found_cameras)
        label = f"Found {count} camera{'s' if count != 1 else ''}"
        tk.Label(header, text=f"  {label}", font=self.font_md,
                 bg=ACCENT, fg=TEXT_PRIMARY).pack(side="left", padx=10, pady=10)
        tk.Button(header, text="  Back  ", font=self.font_sm,
                  bg=ACCENT2, fg=TEXT_PRIMARY, relief="flat",
                  command=self.show_home).pack(side="right", padx=12)
        tk.Button(header, text="  Rescan  ", font=self.font_sm,
                  bg=ACCENT2, fg=TEXT_PRIMARY, relief="flat",
                  command=self.start_scan).pack(side="right", padx=4)

        if not self.found_cameras:
            centre = tk.Frame(self.container, bg=BG_DARK)
            centre.pack(fill="both", expand=True)
            tk.Label(centre, text="No cameras found", font=self.font_lg,
                     bg=BG_DARK, fg=DANGER).place(relx=0.5, rely=0.4, anchor="center")
            tk.Label(centre, text="Check that cameras are powered and on the same subnet as the Pi.",
                     font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.52, anchor="center")
            self._draw_sd_hints(self.container, {1: "RESCAN", 6: "HOME"})
            return

        # Scrollable camera list
        list_frame = tk.Frame(self.container, bg=BG_DARK)
        list_frame.pack(fill="both", expand=True, padx=10, pady=8)

        canvas = tk.Canvas(list_frame, bg=BG_DARK, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self.scroll_inner = tk.Frame(canvas, bg=BG_DARK)

        self.scroll_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.camera_buttons = []
        for i, cam in enumerate(self.found_cameras):
            self._draw_camera_row(i, cam)

        self._highlight_selected()
        self._draw_sd_hints(self.container, {1: "RESCAN", 4: "CONNECT", 6: "HOME"})
        # Start health checks now that results are displayed
        self._start_health_checks()

    def _draw_camera_row(self, index, cam):
        is_ndi  = cam["type"] == "NDI"
        type_col = ACCENT if is_ndi else SUCCESS
        type_label = "NDI" if is_ndi else "RTSP"

        row = tk.Frame(self.scroll_inner, bg=BG_CARD, pady=0)
        row.pack(fill="x", pady=3, padx=4)

        # Coloured left stripe
        stripe = tk.Frame(row, bg=type_col, width=6)
        stripe.pack(side="left", fill="y")

        # Content
        info = tk.Frame(row, bg=BG_CARD, padx=12, pady=10)
        info.pack(side="left", fill="both", expand=True)

        top_row = tk.Frame(info, bg=BG_CARD)
        top_row.pack(fill="x")

        tk.Label(top_row, text=f"  {type_label}  ", font=self.font_xs,
                 bg=type_col, fg=TEXT_PRIMARY).pack(side="left")
        tk.Label(top_row, text=f"  {cam['name']}", font=self.font_md,
                 bg=BG_CARD, fg=TEXT_PRIMARY).pack(side="left", padx=6)

        bottom_row = tk.Frame(info, bg=BG_CARD)
        bottom_row.pack(fill="x", pady=(3, 0))

        detail = cam.get("url", cam.get("ip", ""))
        codec  = cam.get("codec", "")
        res    = cam.get("resolution", "")
        detail_str = detail
        if codec:
            detail_str += f"   |   {codec.upper()}"
        if res:
            detail_str += f"   {res}"

        tk.Label(bottom_row, text=detail_str, font=self.font_xs,
                 bg=BG_CARD, fg=TEXT_DIM).pack(side="left")

        # Connect button on right
        conn_btn = tk.Button(
            row, text="▶  Connect",
            font=self.font_sm, bg=ACCENT2, fg=TEXT_PRIMARY,
            relief="flat", padx=14, pady=12,
            cursor="hand2",
            command=lambda i=index: self.connect_camera(i)
        )
        conn_btn.pack(side="right", padx=8, pady=6)

        # Touch select
        for widget in [row, info, top_row, bottom_row]:
            widget.bind("<Button-1>", lambda e, i=index: self._set_selected(i))

        self.camera_buttons.append(row)

    def _set_selected(self, index):
        self.selected_index = index
        self._highlight_selected()

    def _highlight_selected(self):
        for i, row in enumerate(self.camera_buttons):
            col = BG_CARD2 if i == self.selected_index else BG_CARD
            row.configure(bg=col)
            for child in row.winfo_children():
                try:
                    child.configure(bg=col)
                except Exception:
                    pass

    def select_prev_camera(self):
        if self.found_cameras:
            self.selected_index = (self.selected_index - 1) % len(self.found_cameras)
            self._highlight_selected()

    def select_next_camera(self):
        if self.found_cameras:
            self.selected_index = (self.selected_index + 1) % len(self.found_cameras)
            self._highlight_selected()

    def connect_selected(self):
        if self.found_cameras:
            self.connect_camera(self.selected_index)

    # ─────────────────────────────────────────
    #  SCREEN: PLAYBACK
    # ─────────────────────────────────────────
    def show_playback(self, cam):
        self.current_screen = "playback"
        self._stop_health_checks()
        self.clear_container()

        # Top bar (stays above video)
        bar = tk.Frame(self.container, bg="#0a0a0a", height=44)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        is_ndi   = cam["type"] == "NDI"
        type_col = ACCENT if is_ndi else SUCCESS

        tk.Frame(bar, bg=type_col, width=6).pack(side="left", fill="y")
        tk.Label(bar, text=f"  {'NDI' if is_ndi else 'RTSP'}  ",
                 font=self.font_xs, bg=type_col, fg=TEXT_PRIMARY).pack(side="left")
        tk.Label(bar, text=f"  {cam['name']}",
                 font=self.font_sm, bg="#0a0a0a", fg=TEXT_PRIMARY).pack(side="left", padx=8)

        if cam.get("codec"):
            tk.Label(bar, text=f"{cam['codec'].upper()}  {cam.get('resolution','')}",
                     font=self.font_xs, bg="#0a0a0a", fg=TEXT_DIM).pack(side="left", padx=12)

        stop_btn = tk.Button(
            bar, text="  STOP  ",
            font=self.font_sm, bg=ACCENT, fg=TEXT_PRIMARY,
            relief="flat", padx=16,
            command=lambda: [self.stop_playback(), self.show_results()]
        )
        stop_btn.pack(side="right", padx=8)

        # Factory reset button — available for NDI and RTSP cameras
        if cam["type"] in ("NDI", "RTSP"):
            self.reset_btn = tk.Button(
                bar, text="  FACTORY RESET  ",
                font=self.font_sm, bg=BG_CARD2, fg=WARNING,
                relief="flat", padx=16,
                command=lambda c=cam: self._confirm_factory_reset(c)
            )
            self.reset_btn.pack(side="right", padx=4)

        tk.Label(bar, text=cam.get("url", cam.get("ip", "")),
                 font=self.font_xs, bg="#0a0a0a", fg=TEXT_DIM).pack(side="right", padx=12)

        # Video frame
        self.video_frame = tk.Frame(self.container, bg="#000000")
        self.video_frame.pack(fill="both", expand=True)

        # Zoom overlay — NDI (VISCA, with position) and RTSP (Hanwha, no position)
        if cam["type"] == "NDI":
            zoom_bar = tk.Frame(self.container, bg="#0a0a0a", height=44)
            zoom_bar.pack(fill="x", side="bottom")
            zoom_bar.pack_propagate(False)

            tk.Label(zoom_bar, text="ZOOM", font=self.font_xs,
                     bg="#0a0a0a", fg=TEXT_DIM).pack(side="left", padx=12)

            # Zoom level bar
            self.zoom_canvas = tk.Canvas(zoom_bar, bg="#0a0a0a",
                                         width=300, height=16,
                                         highlightthickness=0)
            self.zoom_canvas.pack(side="left", padx=8, pady=14)
            self.zoom_canvas.create_rectangle(0, 0, 300, 16,
                                              fill=BG_CARD, outline="", tags="bg")
            self.zoom_canvas.create_rectangle(0, 0, 0, 16,
                                              fill=ACCENT, outline="", tags="fill")

            self.zoom_pct_var = tk.StringVar(value="0%")
            tk.Label(zoom_bar, textvariable=self.zoom_pct_var,
                     font=self.font_xs, bg="#0a0a0a",
                     fg=TEXT_PRIMARY, width=5).pack(side="left")
        elif cam["type"] == "RTSP":
            zoom_bar = tk.Frame(self.container, bg="#0a0a0a", height=44)
            zoom_bar.pack(fill="x", side="bottom")
            zoom_bar.pack_propagate(False)
            tk.Label(zoom_bar, text="ZOOM  —  hold B1 to zoom in, B4 to zoom out",
                     font=self.font_xs, bg="#0a0a0a", fg=TEXT_DIM).pack(side="left", padx=12)

        self.update_idletasks()

        self._current_cam = cam
        wid = self.video_frame.winfo_id()

        # Initialise the right zoom controller for the camera type
        if cam["type"] == "NDI":
            self._zoom_controller = VISCAZoomController(
                cam.get("ip", ""),
                zoom_update_cb=self._update_zoom_display,
                connect_delay=3.0
            )
        elif cam["type"] == "RTSP":
            # Extract credentials from the working RTSP URL
            import re
            user, pwd = "admin", "Repair2023!"
            m = re.search(r'rtsp://([^:]+):([^@]*)@', cam.get("url", ""))
            if m:
                user, pwd = m.group(1), m.group(2)
            self._zoom_controller = HanwhaZoomController(
                cam.get("ip", ""), user, pwd
            )
        else:
            self._zoom_controller = None

        self._launch_stream(cam)
        if cam["type"] in ("NDI", "RTSP"):
            self._draw_sd_hints(self.container, {1: "ZOOM +", 4: "ZOOM -", 5: "RESET", 6: "STOP"})
        else:
            self._draw_sd_hints(self.container, {5: "RESET", 6: "STOP"})

    def _update_zoom_display(self, position, max_pos=1024):
        """Update the zoom level bar and percentage label."""
        if not hasattr(self, "zoom_canvas") or not self.zoom_canvas:
            return
        pct = int((position / max_pos) * 100)
        fill_w = int((position / max_pos) * 300)
        def _do():
            try:
                self.zoom_canvas.coords("fill", 0, 0, fill_w, 16)
                self.zoom_pct_var.set(f"{pct}%")
            except Exception:
                pass
        self.after(0, _do)

    def _launch_stream(self, cam):
        """Route to the correct playback method based on camera type."""
        if cam["type"] == "NDI":
            self._launch_ndi(cam)
        else:
            self._launch_mpv(cam)

    # ── NDI playback via ndi-python + Pillow ──────────────────────────────────
    def _launch_ndi(self, cam):
        """Receive NDI frames in a background thread and render to canvas."""
        try:
            import NDIlib as ndi
            from PIL import Image, ImageTk
        except ImportError as e:
            self._show_playback_error(f"Missing library: {e}  —  run: pip3 install ndi-python pillow")
            return

        # Create a canvas to draw frames onto
        self.ndi_canvas = tk.Canvas(self.video_frame, bg="#000000",
                                    highlightthickness=0)
        self.ndi_canvas.pack(fill="both", expand=True)
        self.update_idletasks()

        canvas_w = self.ndi_canvas.winfo_width()
        canvas_h = self.ndi_canvas.winfo_height()

        self.ndi_running = True
        source_name = cam.get("ndi_name", cam.get("name", ""))

        def _receive():
            if not ndi.initialize():
                self.after(0, lambda: self._show_playback_error("NDI initialisation failed"))
                return

            # Find the source — do a fresh discovery to get a live source object
            find_create = ndi.FindCreate()
            finder = ndi.find_create_v2(find_create)
            if not finder:
                self.after(0, lambda: self._show_playback_error("NDI finder failed"))
                ndi.destroy()
                return

            self.after(0, lambda: self._show_playback_error("🔍  Locating NDI source…", tag="locating"))

            # Poll up to 8 seconds for the exact source
            target_source = None
            for attempt in range(16):
                if not self.ndi_running:
                    ndi.find_destroy(finder)
                    ndi.destroy()
                    return
                sources = ndi.find_get_current_sources(finder)
                for s in sources:
                    if s.ndi_name == source_name:
                        target_source = s
                        break
                if target_source:
                    break
                time.sleep(0.5)

            ndi.find_destroy(finder)

            if not target_source:
                self.after(0, lambda: self._show_playback_error(
                    f"⚠  Source not found on network:\n{source_name}\n\nCheck camera is powered and on same network."))
                ndi.destroy()
                return

            # Connect receiver
            recv_create = ndi.RecvCreateV3()
            recv_create.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
            recv_create.bandwidth    = ndi.RECV_BANDWIDTH_HIGHEST
            recv_create.allow_video_fields = False
            receiver = ndi.recv_create_v3(recv_create)
            if not receiver:
                self.after(0, lambda: self._show_playback_error("NDI receiver creation failed"))
                ndi.destroy()
                return

            ndi.recv_connect(receiver, target_source)

            # Clear any status messages from the canvas before frames arrive
            self.after(0, lambda: self.ndi_canvas.delete("all") if hasattr(self, "ndi_canvas") and self.ndi_canvas else None)

            # Get canvas size once
            self.after(0, lambda: None)
            time.sleep(0.1)

            TARGET_W = 1280
            TARGET_H = 676   # canvas height = 720 - 44px top bar

            last_draw = 0
            FRAME_INTERVAL = 1 / 30  # cap at 30fps for Pi performance

            # Frame receive loop
            while self.ndi_running:
                t, v, a, _ = ndi.recv_capture_v2(receiver, 100)

                if t == ndi.FRAME_TYPE_VIDEO and v is not None:
                    try:
                        import numpy as np
                        w, h = v.xres, v.yres

                        # Clear any status messages on first frame
                        self.after(0, self._clear_playback_status)

                        # v.data is already shaped (h, w, 4) in BGRA
                        arr = np.asarray(v.data, dtype=np.uint8)
                        if arr.shape != (h, w, 4):
                            arr = arr.reshape((h, w, 4))

                        # Swap B and R: BGRA → RGBA
                        rgba = arr[:, :, [2, 1, 0, 3]]

                        img = Image.fromarray(rgba, "RGBA")

                        # Resize to fit canvas BEFORE making PhotoImage
                        # (much faster than thumbnail on large frames)
                        scale = min(TARGET_W / w, TARGET_H / h)
                        new_w = int(w * scale)
                        new_h = int(h * scale)
                        img = img.resize((new_w, new_h), Image.BILINEAR)

                        photo = ImageTk.PhotoImage(img)

                        now = time.time()
                        if now - last_draw >= FRAME_INTERVAL:
                            last_draw = now

                            def _draw(p=photo, iw=new_w, ih=new_h):
                                if not self.ndi_running or not hasattr(self, "ndi_canvas") or not self.ndi_canvas:
                                    return
                                self.ndi_photo = p
                                self.ndi_canvas.delete("frame")
                                self.ndi_canvas.create_image(
                                    TARGET_W // 2, TARGET_H // 2,
                                    anchor="center", image=p, tags="frame")

                            self.after(0, _draw)

                    except Exception as ex:
                        print(f"NDI frame error: {ex}")
                    finally:
                        ndi.recv_free_video_v2(receiver, v)

                elif t == ndi.FRAME_TYPE_NONE:
                    time.sleep(0.005)

            ndi.recv_destroy(receiver)
            ndi.destroy()

        self.ndi_thread = threading.Thread(target=_receive, daemon=True)
        self.ndi_thread.start()

    # ── RTSP playback via mpv ─────────────────────────────────────────────────
    def _launch_mpv(self, cam):
        wid = self.video_frame.winfo_id()
        url = cam.get("url", "")
        cmd = [
            "mpv",
            f"--wid={wid}",
            "--no-osc",
            "--no-input-default-bindings",
            "--hwdec=auto",
            "--vo=gpu",
            "--keep-open=yes",
            "--idle=yes",
            "--rtsp-transport=tcp",
            "--cache=no",
            "--demuxer-readahead-secs=0",
            url,
        ]
        try:
            self.mpv_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid
            )
        except FileNotFoundError:
            self._show_playback_error("mpv not found — run setup.sh")
        except Exception as ex:
            self._show_playback_error(str(ex))

    def _show_playback_error(self, msg, tag="error"):
        if not hasattr(self, "video_frame"):
            return
        if not hasattr(self, "_status_labels"):
            self._status_labels = {}
        # Remove existing label with this tag
        if tag in self._status_labels:
            try:
                self._status_labels[tag].destroy()
            except Exception:
                pass
        fg = DANGER if tag == "error" else TEXT_DIM
        lbl = tk.Label(self.video_frame, text=f"  {msg}  ",
                       font=self.font_md, bg="#000000", fg=fg, wraplength=900)
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        self._status_labels[tag] = lbl

    def _clear_playback_status(self):
        if hasattr(self, "_status_labels"):
            for lbl in self._status_labels.values():
                try:
                    lbl.destroy()
                except Exception:
                    pass
            self._status_labels = {}

    def stop_playback(self):
        # Stop zoom controller
        if hasattr(self, "_zoom_controller") and self._zoom_controller:
            self._zoom_controller.close()
            self._zoom_controller = None
        # Stop NDI
        self.ndi_running = False
        if hasattr(self, "ndi_canvas"):
            self.ndi_canvas = None
        if hasattr(self, "ndi_photo"):
            self.ndi_photo  = None

        # Stop mpv
        if self.mpv_process:
            try:
                os.killpg(os.getpgid(self.mpv_process.pid), signal.SIGTERM)
            except Exception:
                pass
            self.mpv_process = None

    # ─────────────────────────────────────────
    #  SCAN LOGIC
    # ─────────────────────────────────────────
    def start_scan(self):
        if self.scanning:
            return
        self.scanning      = True
        self.found_cameras = []
        self.selected_index = 0
        self.show_scanning()

        self.scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self.scan_thread.start()

    def cancel_scan(self):
        self.scanning = False
        self.show_home()

    def _update_scan_ui(self, status=None, detail=None, ndi=None, rtsp=None):
        """Thread-safe UI update during scan."""
        def _do():
            if status and hasattr(self, "scan_status_var"):
                self.scan_status_var.set(status)
            if detail is not None and hasattr(self, "scan_detail_var"):
                self.scan_detail_var.set(detail)
            if ndi and hasattr(self, "scan_ndi_var"):
                self.scan_ndi_var.set(ndi)
            if rtsp and hasattr(self, "scan_rtsp_var"):
                self.scan_rtsp_var.set(rtsp)
        self.after(0, _do)

    def _scan_worker(self):
        results = []

        # ── NDI Discovery ──────────────────────────────
        self._update_scan_ui(status="Scanning for NDI cameras…", ndi="NDI:  scanning…")
        ndi_results = self._discover_ndi()
        results.extend(ndi_results)
        ndi_count = len(ndi_results)
        self._update_scan_ui(
            ndi=f"NDI:  {'found ' + str(ndi_count) + ' source' + ('s' if ndi_count!=1 else '') if ndi_count else 'none found'}"
        )

        if not self.scanning:
            return

        # ── Stop early if NDI camera already found ──────
        if results:
            self._update_scan_ui(
                status="Camera found — skipping RTSP scan",
                rtsp="RTSP: skipped"
            )
            self.found_cameras = results
            self.scanning = False
            self.after(0, self.show_results)
            return

        # ── RTSP Discovery (only if no NDI found) ───────
        self._update_scan_ui(status="No NDI found — scanning for RTSP cameras…", rtsp="RTSP: scanning network…")
        rtsp_results = self._discover_rtsp()
        results.extend(rtsp_results)
        rtsp_count = len(rtsp_results)
        self._update_scan_ui(
            rtsp=f"RTSP: {'found ' + str(rtsp_count) + ' camera' + ('s' if rtsp_count!=1 else '') if rtsp_count else 'none found'}"
        )

        if not self.scanning:
            return

        self.found_cameras = results
        self.scanning = False
        self.after(0, self.show_results)

    # ── NDI ───────────────────────────────────────────
    def _ndi_source_alive(self, ip, url_addr, timeout=1.0):
        """Quick TCP check to the NDI source port to filter stale mDNS cache entries."""
        # Parse port from url_address (e.g. "10.0.0.1:5961"), default to 5960
        port = 5960
        if ":" in url_addr:
            try:
                port = int(url_addr.split(":")[1])
            except (ValueError, IndexError):
                port = 5960
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            result = s.connect_ex((ip, port))
            s.close()
            return result == 0
        except Exception:
            return False

    def _discover_ndi(self):
        """Discover NDI sources directly via ndi-python."""
        results = []
        try:
            import NDIlib as ndi

            if not ndi.initialize():
                return results

            find_create = ndi.FindCreate()
            finder = ndi.find_create_v2(find_create)
            if not finder:
                ndi.destroy()
                return results

            # Poll for up to 6 seconds
            for _ in range(12):
                if not self.scanning:
                    break
                time.sleep(0.5)
                sources = ndi.find_get_current_sources(finder)
                if sources:
                    break

            sources = ndi.find_get_current_sources(finder)
            for s in sources:
                ndi_name = s.ndi_name  or ""
                url_addr = s.url_address or ""
                ip = url_addr.split(":")[0] if ":" in url_addr else url_addr

                # Verify the source is actually reachable — the mDNS cache keeps
                # advertising unplugged cameras for up to a minute. A quick TCP
                # check to the NDI port (5960/5961) filters out stale entries.
                if ip and not self._ndi_source_alive(ip, url_addr):
                    print(f"Skipping stale NDI source: {ndi_name} ({ip})")
                    continue

                # Add matching subnet IP in background so discovery isn't delayed
                if ip:
                    threading.Thread(
                        target=self._add_host_route,
                        args=(ip,),
                        daemon=True
                    ).start()

                results.append({
                    "type":       "NDI",
                    "name":       ndi_name,
                    "ndi_name":   ndi_name,
                    "ip":         ip,
                    "url":        url_addr,
                    "codec":      "NDI",
                    "resolution": "",
                })

            ndi.find_destroy(finder)
            ndi.destroy()

        except ImportError:
            pass
        except Exception:
            pass

        return results

    # ── RTSP ─────────────────────────────────────────
    def _discover_rtsp(self):
        """
        Discover RTSP cameras using ONVIF WS-Discovery first,
        then fall back to subnet scan if nothing found.
        """
        results  = []
        all_hosts = []

        # ── Strategy 1: ONVIF WS-Discovery ───────────
        # Works regardless of IP/subnet — cameras announce themselves
        self._update_scan_ui(detail="Scanning for ONVIF cameras via WS-Discovery…")
        onvif_ips = self._onvif_discover()
        for ip in onvif_ips:
            if not self.scanning:
                break
            all_hosts.append((ip, 554))

        if all_hosts:
            self._update_scan_ui(detail=f"ONVIF found {len(all_hosts)} device(s) — probing streams…")
        else:
            # ── Strategy 2: Subnet scan fallback ─────
            own_ip = self.get_ip_address("eth0")
            if not own_ip:
                self._update_scan_ui(detail="No network interface — skipping RTSP scan")
                return results

            subnet = ".".join(own_ip.split(".")[:3])
            self._update_scan_ui(detail=f"Scanning {subnet}.0/24 for RTSP ports…")
            for port in [554, 8554]:
                try:
                    nmap_out = subprocess.run(
                        ["nmap", "-p", str(port), "--open", "-oG", "-",
                         f"{subnet}.0/24"],
                        capture_output=True, text=True, timeout=SCAN_TIMEOUT
                    )
                    for line in nmap_out.stdout.splitlines():
                        if f"{port}/open" in line:
                            parts = line.split()
                            if len(parts) >= 2:
                                all_hosts.append((parts[1], port))
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    all_hosts.extend(self._tcp_sweep(subnet, port))

        if not all_hosts:
            return results

        # Deduplicate by IP
        seen = {}
        for ip, port in all_hosts:
            if ip not in seen:
                seen[ip] = port
        hosts = list(seen.items())

        for ip, port in hosts:
            if not self.scanning:
                break
            self._update_scan_ui(detail=f"Probing {ip}:{port}…")
            # Ensure Pi can reach this IP
            self._add_host_route(ip)
            cam = self._probe_rtsp_camera(ip, port)
            if cam:
                results.append(cam)
                break

        return results

    def _onvif_discover(self, timeout=6):
        """
        Use ONVIF WS-Discovery to find cameras on eth0 regardless of subnet.
        Filters out anything on the WiFi subnet — cameras live only on eth0.
        Returns list of IP addresses.
        """
        ips = []

        # Get WiFi subnet so we can exclude it (cameras are never on WiFi)
        wlan_ip = self.get_ip_address("wlan0")
        wlan_subnet = ".".join(wlan_ip.split(".")[:2]) if wlan_ip else None

        try:
            from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
            wsd = WSDiscovery()
            wsd.start()
            time.sleep(timeout)
            services = wsd.searchServices()
            for s in services:
                types = str(s.getTypes())
                # Only ONVIF cameras — skip Windows/printers
                if 'onvif' not in types.lower() and 'networkvideotransmitter' not in types.lower():
                    continue
                for addr in s.getXAddrs():
                    # Extract IP from URL like http://192.168.100.55/onvif/device_service
                    import re
                    match = re.search(r'http://(\d+\.\d+\.\d+\.\d+)', addr)
                    if match:
                        ip = match.group(1)
                        # Skip anything on the WiFi subnet — cameras are on eth0 only
                        if wlan_subnet and ip.startswith(wlan_subnet + "."):
                            print(f"Skipping WiFi-side device: {ip}")
                            continue
                        if ip not in ips:
                            ips.append(ip)
                            print(f"ONVIF discovered: {ip}")
            wsd.stop()
        except ImportError:
            print("wsdiscovery not installed — skipping ONVIF discovery")
        except Exception as e:
            print(f"ONVIF discovery error: {e}")
        return ips

    def _arp_scan(self):
        """
        Read the ARP table to find devices directly connected on eth0.
        Returns list of IPs seen on eth0 regardless of subnet.
        """
        ips = []
        try:
            # First send a broadcast ping to populate the ARP table
            subprocess.run(
                ["ping", "-b", "-c", "2", "-W", "1",
                 "-I", "eth0", "255.255.255.255"],
                capture_output=True, timeout=4
            )
        except Exception:
            pass

        try:
            # Also try link-local range which cameras often use as fallback
            subprocess.run(
                ["ping", "-c", "1", "-W", "1", "-I", "eth0", "169.254.255.255"],
                capture_output=True, timeout=3
            )
        except Exception:
            pass

        try:
            # Read ARP table entries for eth0
            out = subprocess.run(
                ["ip", "neigh", "show", "dev", "eth0"],
                capture_output=True, text=True, timeout=3
            )
            for line in out.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[3] != "FAILED":
                    ip = parts[0]
                    # Skip our own IP and multicast/broadcast addresses
                    if ip != self.get_ip_address("eth0") and \
                       not ip.startswith("224.") and \
                       not ip.startswith("255."):
                        ips.append(ip)
        except Exception:
            pass

        return ips

    def _add_host_route(self, ip):
        """
        Ensure the Pi has an IP on the same subnet as the camera.
        Adds a secondary IP and announces it via gratuitous ARP.
        """
        try:
            cam_subnet = ".".join(ip.split(".")[:3])

            # Check if we already have an IP on the camera's subnet
            out = subprocess.run(
                ["ip", "-4", "addr", "show", "dev", "eth0"],
                capture_output=True, text=True
            )
            existing_pi_ip = None
            for line in out.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    existing_ip = line.split()[1].split("/")[0]
                    existing_sub = ".".join(existing_ip.split(".")[:3])
                    if existing_sub == cam_subnet:
                        existing_pi_ip = existing_ip
                        break

            if existing_pi_ip:
                # Already have IP on this subnet — just send gratuitous ARP
                print(f"Already on camera subnet ({existing_pi_ip}), sending ARP")
                subprocess.run(
                    ["sudo", "arping", "-c", "2", "-A", "-I", "eth0", existing_pi_ip],
                    capture_output=True, timeout=5
                )
                return

            # Pick an IP on the camera's subnet
            cam_last = ip.split(".")[-1]
            pi_last  = "3" if cam_last == "2" else "2"
            pi_ip    = f"{cam_subnet}.{pi_last}"

            print(f"Adding {pi_ip}/24 to eth0 to match camera subnet {cam_subnet}.x")
            subprocess.run(
                ["sudo", "ip", "addr", "add", f"{pi_ip}/24", "dev", "eth0"],
                capture_output=True, timeout=3
            )
            time.sleep(0.5)

            # Send gratuitous ARP so camera immediately knows how to reach us
            subprocess.run(
                ["sudo", "arping", "-c", "3", "-A", "-I", "eth0", pi_ip],
                capture_output=True, timeout=5
            )
            time.sleep(0.5)

        except Exception as e:
            print(f"_add_host_route error: {e}")

    def _tcp_sweep(self, subnet, port=554):
        """Fallback: TCP connect sweep for given port across /24."""
        hosts = []
        def _try(ip):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.4)
                if s.connect_ex((ip, port)) == 0:
                    hosts.append((ip, port))
                s.close()
            except Exception:
                pass

        threads = []
        for i in range(1, 255):
            ip = f"{subnet}.{i}"
            t  = threading.Thread(target=_try, args=(ip,), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=1)
        return hosts

    def _probe_rtsp_camera(self, ip, port=554):
        """
        Probe RTSP camera using fast DESCRIBE first (no credentials needed).
        200 = open stream, 401 = auth required, None = not an RTSP camera.
        Only uses ffprobe to get codec/resolution after successful auth.
        """
        # Try each path with DESCRIBE first to find the right path
        found_path = None
        needs_auth = False

        for path in RTSP_PATHS:
            if not self.scanning:
                return None
            status = self._rtsp_describe(ip, port, path)
            if status == 200:
                found_path = path
                needs_auth = False
                break
            elif status == 401:
                found_path = path
                needs_auth = True
                break

        if found_path is None:
            return None  # Not an RTSP camera

        if not needs_auth:
            # Open stream — no credentials needed
            url = f"rtsp://{ip}:{port}{found_path}"
            info = self._ffprobe_stream(url) or {}
            return {
                "type":       "RTSP",
                "name":       f"RTSP @ {ip}",
                "ip":         ip,
                "url":        url,
                "codec":      info.get("codec", ""),
                "resolution": info.get("resolution", ""),
            }

        # Needs auth — try known credentials
        for user, pwd in RTSP_CREDENTIALS:
            if not self.scanning:
                return None
            url = f"rtsp://{user}:{pwd}@{ip}:{port}{found_path}"
            info = self._ffprobe_stream(url)
            if info:
                return {
                    "type":       "RTSP",
                    "name":       f"RTSP @ {ip}",
                    "ip":         ip,
                    "url":        url,
                    "codec":      info.get("codec", ""),
                    "resolution": info.get("resolution", ""),
                }

        # Known credentials failed — needs manual setup
        return {
            "type":           "RTSP",
            "name":           f"RTSP @ {ip}",
            "ip":             ip,
            "url":            "",
            "codec":          "",
            "resolution":     "",
            "needs_password": True,
            "port":           port,
        }

    def _ffprobe_stream(self, url):
        """Run ffprobe on an RTSP URL. Returns dict with codec/resolution or None."""
        try:
            out = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_streams",
                    "-rtsp_transport", "tcp",
                    "-timeout", "2000000",   # 2s in microseconds
                    url
                ],
                capture_output=True, text=True, timeout=4
            )
            data = json.loads(out.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    w = stream.get("width", "?")
                    h = stream.get("height", "?")
                    return {
                        "codec":      stream.get("codec_name", ""),
                        "resolution": f"{w}×{h}",
                    }
        except Exception:
            pass
        return None

    def _port_open(self, ip, port, timeout=1.5):
        """Quick TCP check to see if port is open before trying ffprobe."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            result = s.connect_ex((ip, port))
            s.close()
            return result == 0
        except Exception:
            return False

    def _rtsp_describe(self, ip, port=554, path="/profile2/media.smp", timeout=2.0):
        """
        Send a raw RTSP DESCRIBE without credentials.
        Returns HTTP status code (200=open, 401=auth required, None=no response).
        Much faster than ffprobe for discovery — same approach as RoboViewer.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, port))
            request = (
                f"DESCRIBE rtsp://{ip}{path} RTSP/1.0\r\n"
                f"CSeq: 1\r\n"
                f"Accept: application/sdp\r\n"
                f"User-Agent: CL-CamTester\r\n\r\n"
            )
            s.send(request.encode())
            response = s.recv(1024).decode(errors="ignore")
            s.close()
            import re
            m = re.search(r"RTSP/1\.0 (\d{3})", response)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────
    #  CONNECT TO CAMERA
    # ─────────────────────────────────────────
    def connect_camera(self, index):
        if 0 <= index < len(self.found_cameras):
            cam = self.found_cameras[index]
            if cam.get("needs_password"):
                self.show_password_entry(cam)
            else:
                self.show_playback(cam)

    def show_password_entry(self, cam):
        """Manual setup wizard — state 1: Open Browser or Cancel."""
        self.current_screen = "manual_setup"
        self._setup_cam = cam
        self._setup_state = "initial"
        self._stop_health_checks()
        self._chromium_proc = None
        self.clear_container()

        tk.Frame(self.container, bg=ACCENT, height=6).pack(fill="x")

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        tk.Label(centre, text="⚠  Manual Setup Required",
                 font=self.font_xl, bg=BG_DARK, fg=WARNING).place(relx=0.5, rely=0.22, anchor="center")

        tk.Label(centre, text=f"Camera found at {cam['ip']} — password not set.",
                 font=self.font_md, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.36, anchor="center")

        tk.Label(centre, text="Press Open Browser to begin initial camera setup.",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.46, anchor="center")

        self._setup_status_var = tk.StringVar(value="")
        tk.Label(centre, textvariable=self._setup_status_var,
                 font=self.font_sm, bg=BG_DARK, fg=SUCCESS).place(relx=0.5, rely=0.56, anchor="center")

        btn_frame = tk.Frame(centre, bg=BG_DARK)
        btn_frame.place(relx=0.5, rely=0.70, anchor="center")

        tk.Button(btn_frame, text="  OPEN BROWSER  ", font=self.font_lg,
                  bg=ACCENT2, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=lambda: self._launch_setup_browser(cam)
                  ).pack(side="left", padx=16)

        tk.Button(btn_frame, text="  CANCEL  ", font=self.font_lg,
                  bg=BG_CARD2, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=self.show_results
                  ).pack(side="left", padx=16)

        tk.Frame(self.container, bg=ACCENT, height=6).pack(side="bottom", fill="x")
        self._draw_sd_hints(self.container, {1: "BROWSER", 6: "CANCEL"})

    def _launch_setup_browser(self, cam):
        """Launch Chromium — state 2: Auto-fill or Cancel (SD only, app hidden)."""
        if hasattr(self, "_chromium_proc") and self._chromium_proc:
            try:
                self._chromium_proc.terminate()
            except Exception:
                pass
        subprocess.run(["pkill", "chromium"], capture_output=True)
        time.sleep(0.5)

        self._setup_state = "browser"
        # Update SD buttons — app is about to hide, deck is the only control
        self._draw_sd_hints(self.container, {2: "AUTO-FILL", 6: "CANCEL"})

        try:
            # Hide our app so Chromium can be seen
            self.withdraw()
            time.sleep(0.3)

            self._chromium_proc = subprocess.Popen([
                "chromium",
                "--window-size=1280,720",
                "--window-position=0,0",
                "--user-data-dir=/tmp/chromium-camtester",
                "--disable-translate",
                "--disable-infobars",
                "--start-maximized",
                f"http://{cam['ip']}",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.deiconify()
            self._setup_status_var.set(f"Could not open browser: {e}")

    def _autofill_password(self):
        """Use xdotool to auto-fill the password in the Chromium browser."""
        def _fill():
            time.sleep(0.5)
            try:
                # Use wmctrl to find and focus Chromium
                result = subprocess.run(
                    ["wmctrl", "-l"],
                    capture_output=True, text=True,
                    env={**os.environ, "DISPLAY": ":0"}
                )
                win_id = None
                for line in result.stdout.splitlines():
                    if "chromium" in line.lower() or "wisenet" in line.lower():
                        win_id = line.split()[0]
                        break

                if not win_id:
                    self.after(0, lambda: self._setup_status_var.set("⚠  Browser not found — open browser first"))
                    return

                # Focus the Chromium window
                subprocess.run(["wmctrl", "-ia", win_id],
                               capture_output=True,
                               env={**os.environ, "DISPLAY": ":0"})
                time.sleep(0.8)

                # Tab to first password field
                subprocess.run(["xdotool", "key", "Tab"],
                               capture_output=True,
                               env={**os.environ, "DISPLAY": ":0"})
                time.sleep(0.3)
                subprocess.run(["xdotool", "type", "--clearmodifiers", "Repair2023!"],
                               capture_output=True,
                               env={**os.environ, "DISPLAY": ":0"})
                time.sleep(0.3)

                # Tab to confirm field
                subprocess.run(["xdotool", "key", "Tab"],
                               capture_output=True,
                               env={**os.environ, "DISPLAY": ":0"})
                time.sleep(0.3)
                subprocess.run(["xdotool", "type", "--clearmodifiers", "Repair2023!"],
                               capture_output=True,
                               env={**os.environ, "DISPLAY": ":0"})
                time.sleep(0.3)

                # Tab to Apply and press Enter
                subprocess.run(["xdotool", "key", "Tab"],
                               capture_output=True,
                               env={**os.environ, "DISPLAY": ":0"})
                time.sleep(0.2)
                subprocess.run(["xdotool", "key", "Return"],
                               capture_output=True,
                               env={**os.environ, "DISPLAY": ":0"})

                # Wait for the confirmation popup, then press Enter to confirm
                time.sleep(3.0)
                subprocess.run(["xdotool", "key", "Return"],
                               capture_output=True,
                               env={**os.environ, "DISPLAY": ":0"})

                time.sleep(1.0)
                # Auto-fill done — restore the app and move to state 3 (Done/Cancel)
                self.after(0, self._show_setup_done_state)

            except Exception as e:
                self.after(0, lambda err=str(e): self._setup_status_var.set(f"⚠  Auto-fill error: {err}"))

        threading.Thread(target=_fill, daemon=True).start()

    def _show_setup_done_state(self):
        """Manual setup wizard — state 3: password set, Done or Cancel."""
        subprocess.run(["pkill", "chromium"], capture_output=True)
        if hasattr(self, "_chromium_proc"):
            self._chromium_proc = None
        self.deiconify()
        self._setup_state = "done"

        cam = self._setup_cam
        self.clear_container()

        tk.Frame(self.container, bg=SUCCESS, height=6).pack(fill="x")

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        tk.Label(centre, text="✓  Password Set",
                 font=self.font_xl, bg=BG_DARK, fg=SUCCESS).place(relx=0.5, rely=0.28, anchor="center")

        tk.Label(centre, text="The camera password has been set to  Repair2023!",
                 font=self.font_md, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.42, anchor="center")

        tk.Label(centre, text="Press Done to reconnect to the camera.",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.52, anchor="center")

        btn_frame = tk.Frame(centre, bg=BG_DARK)
        btn_frame.place(relx=0.5, rely=0.68, anchor="center")

        tk.Button(btn_frame, text="  DONE  ", font=self.font_lg,
                  bg=SUCCESS, fg="#000000", relief="flat",
                  padx=30, pady=14,
                  command=lambda: self._connect_after_setup(cam)
                  ).pack(side="left", padx=16)

        tk.Button(btn_frame, text="  CANCEL  ", font=self.font_lg,
                  bg=BG_CARD2, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=self.show_results
                  ).pack(side="left", padx=16)

        tk.Frame(self.container, bg=SUCCESS, height=6).pack(side="bottom", fill="x")
        self._draw_sd_hints(self.container, {1: "DONE", 6: "CANCEL"})

    def _connect_after_setup(self, cam):
        """After password setup, connect directly to the known camera — no full rescan."""
        # Close browser and restore app
        if hasattr(self, "_chromium_proc") and self._chromium_proc:
            try:
                self._chromium_proc.terminate()
                self._chromium_proc = None
            except Exception:
                pass
        subprocess.run(["pkill", "chromium"], capture_output=True)
        time.sleep(0.3)
        self.deiconify()

        ip   = cam.get("ip", "")
        port = cam.get("port", 554)

        self.current_screen = "probing"
        self.clear_container()
        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)
        status_var = tk.StringVar(value="Connecting to camera…")
        tk.Label(centre, text="Connecting", font=self.font_xl,
                 bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.35, anchor="center")
        tk.Label(centre, textvariable=status_var,
                 font=self.font_md, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.50, anchor="center")

        def _probe():
            # Password is now Repair2023! — find the working stream path
            for path in RTSP_PATHS:
                url = f"rtsp://admin:Repair2023!@{ip}:{port}{path}"
                self.after(0, lambda p=path: status_var.set(f"Trying {p}…"))
                info = self._ffprobe_stream(url)
                if info:
                    cam["url"]        = url
                    cam["codec"]      = info.get("codec", "")
                    cam["resolution"] = info.get("resolution", "")
                    cam.pop("needs_password", None)
                    self.after(0, lambda: self.show_playback(cam))
                    return
            # Couldn't connect — fall back to results
            self.after(0, lambda: status_var.set("⚠  Could not connect — returning to results"))
            self.after(2500, self.show_results)

        threading.Thread(target=_probe, daemon=True).start()

    def _close_browser_and_scan(self, cam):
        """Close browser, restore app window and scan again."""
        if hasattr(self, "_chromium_proc") and self._chromium_proc:
            try:
                self._chromium_proc.terminate()
                self._chromium_proc = None
            except Exception:
                pass
        subprocess.run(["pkill", "chromium"], capture_output=True)
        time.sleep(0.3)
        self.deiconify()
        self.start_scan()

    def _try_rtsp_with_password(self, cam, password):
        """Try connecting to RTSP camera with manually entered password."""
        self.current_screen = "probing"
        self.clear_container()

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        status_var = tk.StringVar(value="Trying password…")
        tk.Label(centre, text="Connecting", font=self.font_xl,
                 bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.35, anchor="center")
        tk.Label(centre, textvariable=status_var,
                 font=self.font_md, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.50, anchor="center")

        ip   = cam.get("ip", "")
        port = cam.get("port", 554)

        def _probe():
            for path in RTSP_PATHS:
                if password:
                    url = f"rtsp://admin:{password}@{ip}:{port}{path}"
                else:
                    url = f"rtsp://{ip}:{port}{path}"
                self.after(0, lambda u=url: status_var.set(f"Trying {path}…"))
                info = self._ffprobe_stream(url)
                if info:
                    cam["url"]        = url
                    cam["codec"]      = info.get("codec", "")
                    cam["resolution"] = info.get("resolution", "")
                    cam.pop("needs_password", None)
                    self.after(0, lambda: self.show_playback(cam))
                    return

            self.after(0, lambda: status_var.set("⚠  Wrong password or stream not found"))
            self.after(2000, lambda: self.show_password_entry(cam))

        threading.Thread(target=_probe, daemon=True).start()

    # ─────────────────────────────────────────
    #  CAMERA HEALTH CHECKING
    # ─────────────────────────────────────────
    def _start_health_checks(self):
        """Start background health checks once results screen is shown."""
        self._health_check_running = True
        t = threading.Thread(target=self._health_check_worker, daemon=True)
        t.start()

    def _stop_health_checks(self):
        self._health_check_running = False

    def _health_check_worker(self):
        """Every 10 seconds, verify each discovered camera is still alive."""
        while self._health_check_running:
            time.sleep(10)
            if not self._health_check_running:
                break
            if self.current_screen not in ("results",):
                continue
            if self.scanning:
                continue

            removed = []
            for cam in list(self.found_cameras):
                if not self._health_check_running:
                    break
                alive = self._is_camera_alive(cam)
                if not alive:
                    removed.append(cam)

            if removed and self._health_check_running:
                for cam in removed:
                    self.found_cameras.remove(cam)
                # Clamp selected index
                if self.selected_index >= len(self.found_cameras):
                    self.selected_index = max(0, len(self.found_cameras) - 1)
                # Refresh results screen on main thread
                self.after(0, self.show_results)

    def _is_camera_alive(self, cam):
        """Quick check if a camera is still reachable."""
        if cam["type"] == "NDI":
            try:
                import NDIlib as ndi
                ndi.initialize()
                f = ndi.find_create_v2(ndi.FindCreate())
                time.sleep(2)
                sources = ndi.find_get_current_sources(f)
                found = any(s.ndi_name == cam["ndi_name"] for s in sources)
                ndi.find_destroy(f)
                ndi.destroy()
                return found
            except Exception:
                return True  # Don't remove on error

        elif cam["type"] == "RTSP":
            # Quick TCP connect check — much faster than ffprobe
            ip   = cam.get("ip", "")
            url  = cam.get("url", "")
            port = int(url.split(":")[2].split("/")[0]) if url else 554
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                result = s.connect_ex((ip, port))
                s.close()
                return result == 0
            except Exception:
                return True  # Don't remove on error

        return True

    # ─────────────────────────────────────────
    #  HELPERS
    # ─────────────────────────────────────────
    def go_back(self):
        if self.current_screen == "playback":
            self.stop_playback()
            self.show_results()
        elif self.current_screen in ("results", "scanning"):
            self.show_home()

    def clear_container(self):
        for widget in self.container.winfo_children():
            widget.destroy()

    # ─────────────────────────────────────────
    #  FACTORY RESET
    # ─────────────────────────────────────────
    def _confirm_factory_reset(self, cam):
        """Show confirmation screen before sending factory reset."""
        # Stop video playback first to free the canvas safely
        self.ndi_running = False
        time.sleep(0.2)

        self.current_screen = "reset_confirm"
        self.clear_container()

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        tk.Label(centre, text="Factory Reset Camera?",
                 font=self.font_xl, bg=BG_DARK, fg=WARNING).place(relx=0.5, rely=0.25, anchor="center")

        tk.Label(centre, text=f"{cam['name']}",
                 font=self.font_md, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.38, anchor="center")

        tk.Label(centre, text="This will reset ALL camera settings to factory defaults.",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.48, anchor="center")

        tk.Label(centre, text="The camera will reboot after reset.",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.56, anchor="center")

        btn_frame = tk.Frame(centre, bg=BG_DARK)
        btn_frame.place(relx=0.5, rely=0.70, anchor="center")

        tk.Button(btn_frame, text="  RESET  ", font=self.font_lg,
                  bg=ACCENT, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=lambda: self._do_factory_reset(cam)).pack(side="left", padx=20)

        tk.Button(btn_frame, text="  CANCEL  ", font=self.font_lg,
                  bg=BG_CARD2, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=lambda: self.show_results()).pack(side="left", padx=20)

        self._draw_sd_hints(self.container, {1: "RESET", 6: "CANCEL"})

    def _do_factory_reset(self, cam):
        """Route factory reset based on camera type."""
        if cam.get("type") == "RTSP":
            self._do_rtsp_factory_reset(cam)
        else:
            self._do_ndi_factory_reset(cam)

    def _do_rtsp_factory_reset(self, cam):
        """Factory reset a Hanwha RTSP camera via CGI API. No config push."""
        self.current_screen = "resetting"
        self.clear_container()

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        self.reset_status_var = tk.StringVar(value="Connecting to camera…")
        tk.Label(centre, text="Factory Reset", font=self.font_xl,
                 bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.30, anchor="center")
        tk.Label(centre, textvariable=self.reset_status_var,
                 font=self.font_md, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.45, anchor="center")

        ip = cam.get("ip", "")

        def _update(msg):
            self.after(0, lambda m=msg: self.reset_status_var.set(m))

        def _reset():
            # Use curl with digest auth — confirmed working approach
            _update("Sending factory reset command…")
            reset_url = (f"http://{ip}/stw-cgi/system.cgi"
                         f"?msubmenu=factoryreset&action=control")

            success = False
            for user, pwd in RTSP_CREDENTIALS:
                try:
                    result = subprocess.run(
                        ["curl", "-s", "--digest", "-u", f"{user}:{pwd}",
                         "--max-time", "6", reset_url],
                        capture_output=True, text=True, timeout=8
                    )
                    # Camera returns "OK" on success, "NG"/error on failure
                    if "OK" in result.stdout and "Error" not in result.stdout:
                        success = True
                        break
                except Exception:
                    continue

            if success:
                _update("✓  Factory reset sent — camera is rebooting")
                self.after(0, self._show_reset_success)
            else:
                _update("⚠  Could not reset — check camera password")
                self.after(3500, self.show_home)

        threading.Thread(target=_reset, daemon=True).start()

    def _do_ndi_factory_reset(self, cam):
        """Send factory reset, wait for reboot, then push recommended config."""
        self.current_screen = "resetting"
        self.clear_container()

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        self.reset_status_var = tk.StringVar(value="Connecting to camera…")
        tk.Label(centre, text="Factory Reset", font=self.font_xl,
                 bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.30, anchor="center")
        tk.Label(centre, textvariable=self.reset_status_var,
                 font=self.font_md, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.45, anchor="center")

        # Progress dots animation
        self.reset_dots_var = tk.StringVar(value="")
        tk.Label(centre, textvariable=self.reset_dots_var,
                 font=self.font_sm, bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.55, anchor="center")

        ip          = cam.get("ip", "")
        default_ip  = "192.168.1.188"
        default_sub = "192.168.1"

        def _update(msg, dots=""):
            self.after(0, lambda m=msg: self.reset_status_var.set(m))
            self.after(0, lambda d=dots: self.reset_dots_var.set(d))

        def _reset():
            import urllib.request
            import json as jsonlib

            url     = f"http://{ip}/cgi-bin/web.fcgi?func=set"
            headers = {"Content-Type": "application/json"}

            try:
                # Step 1 — Login
                _update("Logging in to camera…")
                login_payload = jsonlib.dumps({
                    "key": 0,
                    "system": {"login": "admin:admin"}
                }).encode()
                req = urllib.request.Request(url, data=login_payload,
                                             headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = jsonlib.loads(resp.read())

                if not data.get("status"):
                    _update("⚠  Login failed — check credentials")
                    self.after(3000, self.show_home)
                    return

                key = data["system"]["login"]

                # Step 2 — Factory reset
                _update("Sending factory reset…")
                reset_payload = jsonlib.dumps({
                    "key": key,
                    "system": {
                        "system_control": "factory_reset",
                        "login": "admin:admin"
                    }
                }).encode()
                req = urllib.request.Request(url, data=reset_payload,
                                             headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    result = jsonlib.loads(resp.read())

                if not result.get("status"):
                    _update("⚠  Reset command failed")
                    self.after(3000, self.show_home)
                    return

                # Step 3 — Add 192.168.1.x to eth0 so we can reach camera at default IP
                _update("Camera rebooting…", "Configuring network for default camera IP")
                try:
                    subprocess.run(
                        ["sudo", "ip", "addr", "add", "192.168.1.2/24", "dev", "eth0"],
                        capture_output=True, timeout=5
                    )
                    subprocess.run(
                        ["sudo", "arping", "-c", "2", "-A", "-I", "eth0", "192.168.1.2"],
                        capture_output=True, timeout=5
                    )
                except Exception as e:
                    print(f"Route add error: {e}")
                time.sleep(1)

                # Step 4 — Wait for camera to come back online.
                # After reset the AIDA camera may either use its default IP (192.168.1.188)
                # OR grab a DHCP lease from our dnsmasq (192.168.100.x). Check both.
                _update("Waiting for camera to reboot…", "This may take 30–60 seconds")
                online = False
                found_ip = None
                key2 = 0

                def _try_login(test_ip):
                    """Try to log in at a given IP. Returns key or None."""
                    try:
                        u = f"http://{test_ip}/cgi-bin/web.fcgi?func=set"
                        test_req = urllib.request.Request(
                            u,
                            data=jsonlib.dumps({"key": 0, "system": {"login": "admin:admin"}}).encode(),
                            headers=headers, method="POST"
                        )
                        with urllib.request.urlopen(test_req, timeout=3) as r:
                            test_data = jsonlib.loads(r.read())
                            if test_data.get("status"):
                                return test_data["system"]["login"]
                    except Exception:
                        pass
                    return None

                for attempt in range(60):
                    time.sleep(2)
                    _update(f"Waiting for camera… ({attempt * 2}s)", "")

                    # Check default IP first
                    k = _try_login(default_ip)
                    if k is not None:
                        found_ip = default_ip
                        key2 = k
                        online = True
                        break

                    # Check dnsmasq leases for a camera that grabbed DHCP
                    try:
                        with open("/var/lib/misc/dnsmasq.leases") as lf:
                            for line in lf:
                                parts = line.split()
                                if len(parts) >= 4:
                                    lease_ip = parts[2]
                                    # Try logging in at this leased IP
                                    k = _try_login(lease_ip)
                                    if k is not None:
                                        found_ip = lease_ip
                                        key2 = k
                                        online = True
                                        break
                    except Exception:
                        pass
                    if online:
                        break

                if not online:
                    _update("⚠  Camera did not come back online")
                    self.after(3000, self.show_home)
                    return

                cam_ip      = found_ip
                default_url = f"http://{found_ip}/cgi-bin/web.fcgi?func=set"

                # Step 5 — Push recommended config
                _update("Camera online — applying settings…")
                config = {
                    "key": key2,
                    "venc": {
                        "main": {
                            "enable": 1,
                            "format": "1920X1080P@60Hz",
                            "mode": "h264",
                            "profile": "MP",
                            "bitrate": 16384,
                            "rcmode": "cbr",
                            "interval": 5
                        },
                        "sub": {
                            "enable": 0,
                            "format": "1280X720P@30Hz",
                            "mode": "h264",
                            "profile": "MP",
                            "bitrate": 2048,
                            "rcmode": "cbr",
                            "interval": 30
                        }
                    },
                    "audio": {
                        "enable": 1,
                        "samplerate": 44100,
                        "bitwidth": 16,
                        "soundMode": "Stereo",
                        "encMode": "AAC",
                        "bitrate": 96000,
                        "volume": 60
                    },
                    "image": {
                        "focus_mode": "auto",
                        "focus_distance": "1.5m",
                        "exposure_mode": "auto",
                        "anti_flicker": 2,
                        "gain_limit": 15,
                        "WB_mode": "one push",
                        "mirror": 0,
                        "flip": 0,
                        "backlight_compensation": 0,
                        "gamma": 0,
                        "WDR_enable": 0,
                        "WDR_level": 0,
                        "brightness": 8,
                        "sharpness": 3,
                        "contrast": 8,
                        "saturation": 8,
                        "noise_reduction_2D": 1,
                        "noise_reduction_3D": 0,
                        "ircut": 0
                    }
                }

                config_req = urllib.request.Request(
                    default_url,
                    data=jsonlib.dumps(config).encode(),
                    headers=headers, method="POST"
                )
                with urllib.request.urlopen(config_req, timeout=10) as r:
                    config_result = jsonlib.loads(r.read())

                if config_result.get("status"):
                    _update("✓  Reset complete — settings applied")
                    self.after(0, self._show_reset_success)
                else:
                    _update("⚠  Reset done but config push failed")
                    self.after(3000, self.show_home)

            except Exception as e:
                err = str(e)
                _update(f"⚠  Error: {err}")
                self.after(4000, self.show_home)

        threading.Thread(target=_reset, daemon=True).start()
        self._draw_sd_hints(self.container, {})

    def _show_toast(self, msg, duration=3000):
        """Show a temporary status message overlay."""
        toast = tk.Label(self.container, text=f"  {msg}  ",
                         font=self.font_sm, bg=BG_CARD2, fg=TEXT_PRIMARY,
                         padx=16, pady=10)
        toast.place(relx=0.5, rely=0.92, anchor="center")
        self.after(duration, toast.destroy)
        self.stop_playback()
        self.sd.close()
        self.destroy()

    # ─────────────────────────────────────────
    #  UPDATE FROM GITHUB
    # ─────────────────────────────────────────
    REPO_RAW = "https://raw.githubusercontent.com/GCrot/CL-Cam-Tester/main"
    APP_PATH = os.path.expanduser("~/camtester/camtester.py")

    def check_for_update(self):
        """Check GitHub for a newer version and prompt to update."""
        self.current_screen = "updating"
        self.clear_container()

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        self.update_status_var = tk.StringVar(value="Checking internet connection…")
        tk.Label(centre, text="Software Update", font=self.font_xl,
                 bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.30, anchor="center")
        tk.Label(centre, textvariable=self.update_status_var,
                 font=self.font_md, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.45, anchor="center")

        self.update_btn_frame = tk.Frame(centre, bg=BG_DARK)
        self.update_btn_frame.place(relx=0.5, rely=0.65, anchor="center")

        self._draw_sd_hints(self.container, {6: "CANCEL"})

        def _check():
            import urllib.request
            import hashlib

            # Step 1 — Check if we have internet
            has_internet = False
            try:
                urllib.request.urlopen("https://github.com", timeout=5)
                has_internet = True
            except Exception:
                pass

            if not has_internet:
                # Try switching to DHCP to get internet
                self.after(0, lambda: self.update_status_var.set(
                    "No internet detected.\nConnecting to network via DHCP…"))
                try:
                    subprocess.run(
                        ["sudo", "nmcli", "connection", "up", "eth0-dhcp"],
                        capture_output=True, timeout=15
                    )
                    # Wait up to 20 seconds for internet
                    for i in range(20):
                        time.sleep(1)
                        self.after(0, lambda s=i+1: self.update_status_var.set(
                            f"Waiting for network… ({s}s)\nPlug into a network with internet access"))
                        try:
                            urllib.request.urlopen("https://github.com", timeout=3)
                            has_internet = True
                            break
                        except Exception:
                            pass
                except Exception:
                    pass

            if not has_internet:
                self.after(0, lambda: self.update_status_var.set(
                    "⚠  No internet connection.\nPlug into a network and try again."))
                self.after(0, self._show_update_back_btn)
                # Restore static IP
                subprocess.run(["sudo", "nmcli", "connection", "up", "eth0-static"],
                               capture_output=True, timeout=10)
                return

            # Step 2 — Check for update
            self.after(0, lambda: self.update_status_var.set("Checking for updates…"))
            try:
                url = f"{self.REPO_RAW}/camtester.py"
                req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    latest = resp.read()

                with open(self.APP_PATH, "rb") as f:
                    current = f.read()

                # Compare by version string — more reliable than MD5
                import re
                latest_ver  = re.search(rb'APP_VERSION\s*=\s*"([^"]+)"', latest)
                current_ver = re.search(rb'APP_VERSION\s*=\s*"([^"]+)"', current)
                latest_ver  = latest_ver.group(1).decode()  if latest_ver  else "0"
                current_ver = current_ver.group(1).decode() if current_ver else "0"

                if latest_ver == current_ver:
                    self.after(0, lambda: self.update_status_var.set(f"✓  Already on latest version ({current_ver})"))
                    self.after(0, self._show_update_back_btn)
                else:
                    self._pending_update_bytes = latest
                    self.after(0, lambda lv=latest_ver, cv=current_ver: self.update_status_var.set(
                        f"Update available: v{cv} → v{lv}"))
                    self.after(0, lambda l=latest: self._show_update_install_btn(l))

            except Exception as e:
                err = str(e)
                self.after(0, lambda: self.update_status_var.set(f"⚠  Could not check for updates:\n{err}"))
                self.after(0, self._show_update_back_btn)

            # Restore static IP after check
            subprocess.run(["sudo", "nmcli", "connection", "up", "eth0-static"],
                           capture_output=True, timeout=10)

        threading.Thread(target=_check, daemon=True).start()

    def _show_update_back_btn(self):
        for w in self.update_btn_frame.winfo_children():
            w.destroy()
        tk.Button(self.update_btn_frame, text="  BACK  ", font=self.font_lg,
                  bg=BG_CARD2, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=self.show_home).pack()
        self._draw_sd_hints(self.container, {6: "BACK"})

    def _show_update_install_btn(self, latest_bytes):
        for w in self.update_btn_frame.winfo_children():
            w.destroy()
        tk.Button(self.update_btn_frame, text="  INSTALL UPDATE  ", font=self.font_lg,
                  bg=SUCCESS, fg="#000000", relief="flat",
                  padx=30, pady=14,
                  command=lambda: self._do_update(latest_bytes)).pack(side="left", padx=10)
        tk.Button(self.update_btn_frame, text="  CANCEL  ", font=self.font_lg,
                  bg=BG_CARD2, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=self.show_home).pack(side="left", padx=10)
        self._draw_sd_hints(self.container, {1: "INSTALL", 6: "CANCEL"})

    def _do_update(self, latest_bytes):
        """Write new version to disk and restart the app."""
        self.update_status_var.set("Installing update…")
        for w in self.update_btn_frame.winfo_children():
            w.destroy()

        def _install():
            try:
                # Back up current version
                backup = self.APP_PATH + ".bak"
                import shutil
                shutil.copy2(self.APP_PATH, backup)

                # Write new version
                with open(self.APP_PATH, "wb") as f:
                    f.write(latest_bytes)

                self.after(0, lambda: self.update_status_var.set("✓  Update installed — restarting…"))
                time.sleep(2)

                # Restart the app
                self.after(0, self._restart_app)

            except Exception as e:
                err = str(e)
                self.after(0, lambda: self.update_status_var.set(f"⚠  Update failed: {err}"))
                self.after(0, self._show_update_back_btn)

        threading.Thread(target=_install, daemon=True).start()

    def _restart_app(self):
        """Restart the application by re-executing Python with the new file."""
        import subprocess
        self.sd.clear()
        self.destroy()
        subprocess.Popen(
            ["python3", self.APP_PATH],
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
        )
        """Show a clear success screen that the operator must dismiss."""
        self.current_screen = "reset_success"
        self.clear_container()

        # Green top stripe
        tk.Frame(self.container, bg=SUCCESS, height=6).pack(fill="x")

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        tk.Label(centre, text="✓", font=tkfont.Font(family="DejaVu Sans", size=72),
                 bg=BG_DARK, fg=SUCCESS).place(relx=0.5, rely=0.22, anchor="center")

        tk.Label(centre, text="Camera Reset Complete",
                 font=self.font_xl, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.45, anchor="center")

        tk.Label(centre, text="Factory defaults restored  ·  Recommended settings applied",
                 font=self.font_sm, bg=BG_DARK, fg=SUCCESS).place(relx=0.5, rely=0.56, anchor="center")

        tk.Label(centre, text="Camera is ready for deployment.",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.64, anchor="center")

        tk.Button(centre, text="  DONE  ", font=self.font_lg,
                  bg=SUCCESS, fg="#000000", relief="flat",
                  padx=40, pady=14,
                  command=self.show_home).place(relx=0.5, rely=0.80, anchor="center")

        # Green bottom stripe
        tk.Frame(self.container, bg=SUCCESS, height=6).pack(side="bottom", fill="x")

        self._draw_sd_hints(self.container, {1: "DONE"})

    def _show_reset_success(self):
        """Show a clear success screen that the operator must dismiss."""
        self.current_screen = "reset_success"
        self.clear_container()

        tk.Frame(self.container, bg=SUCCESS, height=6).pack(fill="x")

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        tk.Label(centre, text="✓", font=tkfont.Font(family="DejaVu Sans", size=72),
                 bg=BG_DARK, fg=SUCCESS).place(relx=0.5, rely=0.22, anchor="center")

        tk.Label(centre, text="Camera Reset Complete",
                 font=self.font_xl, bg=BG_DARK, fg=TEXT_PRIMARY).place(relx=0.5, rely=0.45, anchor="center")

        tk.Label(centre, text="Factory defaults restored  ·  Recommended settings applied",
                 font=self.font_sm, bg=BG_DARK, fg=SUCCESS).place(relx=0.5, rely=0.56, anchor="center")

        tk.Label(centre, text="Camera is ready for deployment.",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.64, anchor="center")

        tk.Button(centre, text="  DONE  ", font=self.font_lg,
                  bg=SUCCESS, fg="#000000", relief="flat",
                  padx=40, pady=14,
                  command=self.show_home).place(relx=0.5, rely=0.80, anchor="center")

        tk.Frame(self.container, bg=SUCCESS, height=6).pack(side="bottom", fill="x")

        self._draw_sd_hints(self.container, {1: "DONE"})

    def reboot_device(self):
        """Show a confirmation screen then reboot the Pi."""
        self.current_screen = "reboot"
        self._stop_health_checks()
        self.stop_playback()
        self.clear_container()

        centre = tk.Frame(self.container, bg=BG_DARK)
        centre.pack(fill="both", expand=True)

        tk.Label(centre, text="Reboot?", font=self.font_xl,
                 bg=BG_DARK, fg=ACCENT).place(relx=0.5, rely=0.30, anchor="center")
        tk.Label(centre, text="Press REBOOT to confirm, or CANCEL to go back.",
                 font=self.font_sm, bg=BG_DARK, fg=TEXT_DIM).place(relx=0.5, rely=0.45, anchor="center")

        btn_frame = tk.Frame(centre, bg=BG_DARK)
        btn_frame.place(relx=0.5, rely=0.62, anchor="center")

        tk.Button(btn_frame, text="REBOOT", font=self.font_lg,
                  bg=ACCENT, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=self._do_reboot).pack(side="left", padx=20)

        tk.Button(btn_frame, text="CANCEL", font=self.font_lg,
                  bg=BG_CARD2, fg=TEXT_PRIMARY, relief="flat",
                  padx=30, pady=14,
                  command=self.show_home).pack(side="left", padx=20)

        self._draw_sd_hints(self.container, {1: "REBOOT", 6: "CANCEL"})

    def _do_reboot(self):
        self.sd.clear()
        self.stop_playback()
        subprocess.run(["sudo", "reboot"], check=False)

    def _draw_sd_hints(self, parent, mapping):
        """Update physical Stream Deck buttons only — no on-screen bar needed."""
        def _style(label):
            l = label.upper()
            if l in ("QUIT", "STOP", "REBOOT"):  return "danger"
            if l in ("SCAN", "RESCAN"):           return "action"
            if l in ("CONNECT", "DONE", "RESET"): return "success"
            if l in ("HOME", "BACK", "CANCEL"):   return "warning"
            return "active"

        deck_mapping = {}
        for btn_num, label in mapping.items():
            deck_mapping[btn_num] = (label, _style(label))

        threading.Thread(
            target=self.sd.set_buttons,
            args=(deck_mapping,),
            daemon=True
        ).start()

# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = CamTesterApp()
    app.mainloop()
