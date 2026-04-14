import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, simpledialog
import requests
import threading
import time
import socket
import xml.etree.ElementTree as ET
import os
import ctypes
import subprocess
from datetime import datetime
import sys
import json
import websocket  # websocket-client library
import customtkinter as ctk
from PIL import Image
import time
import os
import sys

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# ─────────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────────

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def indent_xml(elem, level=0):
    indent = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        for child in elem:
            indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent
    if level == 0:
        elem.tail = "\n"

CONFIG_FILE = "config.xml"
NUM_RELAYS  = 5   # default; actual count comes from hardware hello packet

BAND_TABLE = [
    (1800,  2000, "160m"), (3500,  4000,  "80m"), (5330, 5410,  "60m"),
    (7000,  7300,  "40m"), (10100, 10150, "30m"), (14000, 14350, "20m"),
    (18068, 18168, "17m"), (21000, 21450, "15m"), (24890, 24990, "12m"),
    (28000, 29700, "10m"), (50000, 54000,  "6m"), (144000, 148000, "2m"),
]

def freq_to_band(freq_khz):
    for low, high, name in BAND_TABLE:
        if low <= freq_khz <= high:
            return name
    return None

def freq_to_mhz_str(freq_khz):
    if freq_khz <= 0:
        return "—.— MHz"
    mhz = freq_khz / 1000.0
    return f"{mhz:.4f} MHz"

def make_draggable(window, handle_widget):
    """Attach mouse-drag behaviour to a borderless window via a handle widget."""
    state = {}

    def on_press(e):
        state['x'] = e.x_root
        state['y'] = e.y_root

    def on_drag(e):
        dx = e.x_root - state['x']
        dy = e.y_root - state['y']
        x = window.winfo_x() + dx
        y = window.winfo_y() + dy
        window.geometry(f"+{x}+{y}")
        state['x'] = e.x_root
        state['y'] = e.y_root

    handle_widget.bind("<ButtonPress-1>",   on_press, add="+")
    handle_widget.bind("<B1-Motion>",       on_drag,  add="+")

# ─────────────────────────────────────────────
#  N1MM LISTENER
# ─────────────────────────────────────────────

class N1MMListener:
    def __init__(self, port, callback, timeout_callback=None):
        self.port = port
        self.callback = callback
        self.timeout_callback = timeout_callback
        self._running = False
        self._thread  = None
        self._sock    = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._sock:
            try: self._sock.close()
            except Exception: pass

    def update_port(self, new_port):
        if new_port != self.port:
            self.stop()
            self.port = new_port
            time.sleep(0.2)
            self.start()

    def _listen(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("", self.port))
            self._sock.settimeout(2.0)
        except Exception as e:
            print(f"N1MM bind error on port {self.port}: {e}")
            self._running = False
            return

        timeout_count = 0
        while self._running:
            try:
                data, _ = self._sock.recvfrom(4096)
                timeout_count = 0
                self._parse_packet(data)
            except socket.timeout:
                timeout_count += 1
                if timeout_count >= 10 and self.timeout_callback:
                    self.timeout_callback()
            except Exception:
                if self._running: continue
                break

        try: self._sock.close()
        except Exception: pass

    def _parse_packet(self, data):
        try:
            text = data.decode("utf-8", errors="ignore")
            if "<RadioInfo>" not in text:
                return
            root = ET.fromstring(text)
            freq_raw = int(root.findtext("Freq", "0"))
            freq_khz = freq_raw // 100
            if freq_khz > 0:
                self.callback(freq_khz)
        except Exception:
            pass

# ─────────────────────────────────────────────
#  UDP DISCOVERY SERVICE
# ─────────────────────────────────────────────

class DiscoveryService:
    @staticmethod
    def find_devices(timeout=1.5):
        devices = []
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.25)
        try:
            sock.sendto(b"COAX_DISCOVERY", ("255.255.255.255", 5000))
            start = time.time()
            while time.time() - start < timeout:
                try:
                    data, addr = sock.recvfrom(1024)
                    text = data.decode("utf-8", errors="ignore")
                    # Format: COAX_DEVICE:Name:IP:type
                    if text.startswith("COAX_DEVICE:"):
                        parts = text.split(":")
                        if len(parts) >= 3:
                            name = parts[1]
                            ip   = parts[2]
                            dtype = parts[3] if len(parts) >= 4 else "ant_switch"
                            if not any(d['ip'] == ip for d in devices):
                                devices.append({"name": name, "ip": ip, "type": dtype})
                except socket.timeout:
                    pass
        except Exception:
            pass
        finally:
            sock.close()
        return devices

# ─────────────────────────────────────────────
#  ESP WEBSOCKET CONNECTION
# ─────────────────────────────────────────────

class ESPConnection:
    """
    Persistent WebSocket connection to one ESP device.
    Runs in its own daemon thread. Reconnects automatically.
    """
    def __init__(self, device_info, on_state_cb, on_ptt_cb, log_cb):
        self.name       = device_info.get("name", "ESP")
        self.ip         = device_info.get("ip", "")
        self.dtype      = device_info.get("type", "ant_switch")  # ant_switch | ptt_reader | both
        self._on_state  = on_state_cb   # fn(ip, relays: list[bool])
        self._on_ptt    = on_ptt_cb     # fn(active: bool)
        self._log       = log_cb
        self._ws        = None
        self._running   = False
        self._thread    = None
        self.online     = False
        self.relay_count = NUM_RELAYS
        self._send_lock = threading.Lock()
        self.last_send_t = 0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(target=self._connect_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ws:
            try: self._ws.close()
            except Exception: pass

    def send_relay(self, index: int, state: bool):
        """Send a relay command. Non-blocking (uses _ws.send in calling thread)."""
        if not self._ws or not self.online:
            return
        msg = json.dumps({"cmd": "set", "relay": index, "state": state}, separators=(',', ':'))
        self.last_send_t = time.perf_counter()
        with self._send_lock:
            try:
                self._ws.send(msg)
            except Exception as e:
                self._log(f"WS SEND ERR ({self.ip}): {e}")

    def send_relay_batch(self, relay_dict: dict):
        """Send multiple relay changes as individual commands."""
        for idx, state in relay_dict.items():
            self.send_relay(idx, state)

    def _connect_loop(self):
        while self._running:
            url = f"ws://{self.ip}/ws"
            self._log(f"WS CONNECT: {url}")
            try:
                self._ws = websocket.WebSocketApp(
                    url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=10, ping_timeout=5)
            except Exception as e:
                self._log(f"WS ERR ({self.ip}): {e}")
            finally:
                self.online = False
                if self._on_state:
                    self._on_state(self.ip, None)  # signal offline

            if self._running:
                time.sleep(3)  # reconnect delay

    def _on_open(self, ws):
        self.online = True
        self._log(f"WS CONNECTED: {self.ip}")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            mtype = data.get("type", "")

            if mtype == "hello":
                self.relay_count = data.get("relays", NUM_RELAYS)
                self._log(f"WS HELLO from {self.ip}: {data.get('name','?')} ({self.relay_count} relays)")

            elif mtype == "state":
                if self.last_send_t > 0:
                    rtt = int((time.perf_counter() - self.last_send_t) * 1000)
                    self._log(f"⏱️ RTT: {rtt}ms")
                    self.last_send_t = 0
                relays = data.get("relays", [])
                if self._on_state:
                    self._on_state(self.ip, relays)

            elif mtype == "ptt":
                active = data.get("active", False)
                self._log(f"PTT from {self.ip}: {'TX' if active else 'RX'}")
                if self._on_ptt:
                    self._on_ptt(active)

        except Exception as e:
            self._log(f"WS MSG PARSE ERR ({self.ip}): {e}")

    def _on_error(self, ws, error):
        self._log(f"WS ERROR ({self.ip}): {error}")
        self.online = False

    def _on_close(self, ws, code, msg):
        self._log(f"WS CLOSED ({self.ip}): {code}")
        self.online = False


# ─────────────────────────────────────────────
#  DEBUG CONSOLE
# ─────────────────────────────────────────────

class DebugConsole(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Debug Console")
        self.geometry("520x400")
        self.attributes('-topmost', True)
        self.overrideredirect(True)
        self.configure(fg_color="#1A1A1A")

        # ── Drag handle (header) ──────────────────────
        hdr = ctk.CTkFrame(self, fg_color="#101012", corner_radius=0, height=28)
        hdr.pack(fill="x")
        lbl = ctk.CTkLabel(hdr, text="DEBUG CONSOLE", font=("Inter", 10, "bold"), text_color="#555")
        lbl.pack(side="left", padx=10)
        ctk.CTkButton(hdr, text="✕", width=28, height=28, fg_color="transparent",
                      text_color="#555", hover_color="#330000",
                      command=self.destroy).pack(side="right")
        make_draggable(self, hdr)
        make_draggable(self, lbl)

        self.log_view = ctk.CTkTextbox(self, font=("Consolas", 11),
                                        fg_color="#000000", text_color="#00FF41")
        self.log_view.pack(expand=True, fill="both", padx=10, pady=10)

        if hasattr(master, 'debug_history'):
            for msg in master.debug_history:
                self.log_view.insert("end", f"{msg}\n")
        self.log_view.see("end")

        ctk.CTkLabel(self, text="LIVE TRAFFIC MONITOR",
                     font=("Inter", 10, "bold"), text_color="#555555").pack(side="bottom", pady=5)

    def log_raw(self, fmsg):
        try:
            self.log_view.insert("end", f"{fmsg}\n")
            self.log_view.see("end")
        except Exception:
            pass


# ─────────────────────────────────────────────
#  GLASS BUTTON
# ─────────────────────────────────────────────

class GlassButton(ctk.CTkFrame):
    def __init__(self, master, name, command, rename_command, scale=1.0, **kwargs):
        super().__init__(master, fg_color="#18181A", corner_radius=0,
                         border_width=0, **kwargs)
        self.command        = command
        self.rename_command = rename_command
        self._scale         = scale
        self._name_text     = name.upper()
        self.grid_columnconfigure(0, weight=1)

        self.label = ctk.CTkLabel(self, text=self._name_text,
                                   font=ctk.CTkFont(family="Inter", size=12, weight="bold"),
                                   text_color="#E0E0E0")
        self.label.grid(row=0, column=0,
                        padx=(int(20*scale), int(50*scale)),
                        pady=int(15*scale), sticky="w")

        canvas_dim = int(22 * scale)
        self.canvas = tk.Canvas(self, width=canvas_dim, height=canvas_dim,
                                 bg="#242424", highlightthickness=0)
        self.canvas.grid(row=0, column=0, padx=int(15*scale), sticky="e")

        led_margin = int(5 * scale)
        self.led = self.canvas.create_oval(
            led_margin, led_margin,
            canvas_dim - led_margin, canvas_dim - led_margin,
            fill="#333333", outline="")

        self._disabled = False

        def on_enter(e):
            if not self._disabled:
                self.configure(fg_color="#2A2A2E")
        def on_leave(e):
            self.configure(fg_color="#18181A")

        for w in [self, self.label, self.canvas]:
            w.bind("<Button-1>", self._handle_click)
            w.bind("<Button-3>", self._handle_rename)
            w.bind("<Enter>",    on_enter)
            w.bind("<Leave>",    on_leave)

        self.bind("<Configure>", self._on_resize)

    def _handle_click(self, event=None):
        if not self._disabled and self.command:
            self.command()

    def _handle_rename(self, event=None):
        if not self._disabled and self.rename_command:
            self.rename_command()

    def set_disabled(self, disabled: bool):
        self._disabled = disabled
        self.label.configure(text_color="#555555" if disabled else "#E0E0E0")

    def _on_resize(self, event):
        w = event.width
        if w < 50: return
        scale      = w / 200.0
        font_size  = max(8, int(14 * scale))
        self.label.configure(font=ctk.CTkFont(family="Inter", size=font_size, weight="bold"))
        px = max(5, int(20 * scale))
        py = max(5, int(15 * scale))
        self.label.grid_configure(padx=(px, int(px * 2.5)), pady=py)
        c_dim   = max(10, int(22 * scale))
        margin  = max(2,  int(5 * scale))
        self.canvas.configure(width=c_dim, height=c_dim)
        self.canvas.coords(self.led, margin, margin, c_dim-margin, c_dim-margin)

    def set_status(self, state):
        colors = {"active": "#00FF7F", "rx": "#1E90FF",
                  "locked": "#FF3131", "idle": "#333333"}
        self.canvas.itemconfig(self.led, fill=colors.get(state, "#333333"))


# ─────────────────────────────────────────────
#  ANTENNA SUB-PANEL  (spawned beside main window)
# ─────────────────────────────────────────────

class AntennaSubPanel(ctk.CTkToplevel):
    """
    Shows MAIN (TX/RX) + all RX antennas for a given band.
    Spawns to the right of the main window.
    """
    def __init__(self, master, band, tx_cfg, rx_cfgs, on_select):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.configure(fg_color="#0A0A0A")
        self._on_select = on_select
        self._buttons   = {}    # key -> GlassButton
        self.cfg_map    = {}    # key -> config_index

        # tx_cfg is (index, cfg_dict)
        tx_idx, tx_data = tx_cfg
        self.cfg_map["main"] = tx_idx

        # Calculate geometry
        self.update_idletasks()
        mx = master.winfo_x()
        my = master.winfo_y()
        mw = master.winfo_width()
        self.geometry(f"+{mx + mw + 4}+{my}")

        # ── Drag handle (header) ──────────────────────
        hdr = ctk.CTkFrame(self, fg_color="#141416", corner_radius=0, height=22)
        hdr.pack(fill="x")
        lbl = ctk.CTkLabel(hdr, text=f"{band}  ·  RX SELECT", font=("Inter", 8, "bold"),
                     text_color="#444444")
        lbl.pack(side="left", padx=8)
        ctk.CTkButton(hdr, text="✕", width=22, height=22, fg_color="transparent",
                      text_color="#555", hover_color="#330000",
                      command=self.destroy).pack(side="right")
        make_draggable(self, hdr)
        make_draggable(self, lbl)

        # ── MAIN (TX/RX) row ─────────────────────────
        self._build_row(f"MAIN ({tx_data['name']})", "main")

        # ── RX rows ──────────────────────────────────
        # rx_cfgs is [(index, cfg_dict), ...]
        for i, (rx_idx, rx_data) in enumerate(rx_cfgs):
            self.cfg_map[i] = rx_idx
            self._build_row(rx_data['name'], i)

    def _build_row(self, name, key):
        btn = GlassButton(self, name=name,
                          command=lambda k=key: self._on_select(k),
                          rename_command=None,
                          scale=0.8)
        btn.pack(fill="x", pady=1)
        self._buttons[key] = btn

    def set_locked(self, locked: bool):
        for btn in self._buttons.values():
            btn.set_disabled(locked)
            if locked:
                btn.set_status("locked")


class SwitchWarningDialog(ctk.CTkToplevel):
    """Custom confirmation dialog shown before a requested antenna switch."""
    def __init__(self, master, profile_name: str, source_label: str):
        super().__init__(master)
        self.result = False
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.transient(master)
        self.configure(fg_color="#1A1A1A")

        width = 360
        height = 220
        self.update_idletasks()
        px = master.winfo_x() + max(0, (master.winfo_width() - width) // 2)
        py = master.winfo_y() + max(0, (master.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{px}+{py}")

        hdr = ctk.CTkFrame(self, fg_color="#101012", corner_radius=0, height=28)
        hdr.pack(fill="x")
        lbl = ctk.CTkLabel(hdr, text="SAFETY WARNING", font=("Inter", 10, "bold"), text_color="#555")
        lbl.pack(side="left", padx=10)
        ctk.CTkButton(
            hdr, text="X", width=28, height=28,
            fg_color="transparent", text_color="#555", hover_color="#330000",
            command=self._cancel
        ).pack(side="right")
        make_draggable(self, hdr)
        make_draggable(self, lbl)

        card = ctk.CTkFrame(self, fg_color="#111111", corner_radius=10)
        card.pack(expand=True, fill="both", padx=14, pady=14)

        ctk.CTkLabel(
            card, text="PTT INACTIVE",
            font=("Inter", 18, "bold"), text_color="#FFB347"
        ).pack(anchor="w", padx=16, pady=(16, 4))

        ctk.CTkLabel(
            card,
            text=(
                f"PTT is not active.\n\n"
                f"{source_label} is requesting a switch to {profile_name.upper()}.\n"
                f"Confirm before changing the coax path."
            ),
            font=("Inter", 11),
            text_color="#D8D8D8",
            justify="left",
            wraplength=300,
        ).pack(anchor="w", padx=16, pady=(0, 16))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        ctk.CTkButton(
            btn_row, text="CANCEL",
            fg_color="#2B2B2B", hover_color="#3A3A3A",
            command=self._cancel
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(
            btn_row, text="SWITCH",
            fg_color="#B22222", hover_color="#8B1A1A",
            command=self._confirm
        ).pack(side="left", expand=True, fill="x", padx=(6, 0))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._confirm())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.focus_force()

    def _confirm(self):
        self.result = True
        self.destroy()

    def _cancel(self):
        self.result = False
        self.destroy()


# ─────────────────────────────────────────────
#  DISCOVERY WINDOW
# ─────────────────────────────────────────────

class DiscoveryWindow(ctk.CTkToplevel):
    DEVICE_TYPES = [
        ("ant_switch",  "Antenna Switch"),
        ("ptt_reader",  "PTT Reader"),
        ("both",        "Both (Switch + PTT)"),
    ]

    def __init__(self, master, on_add_callback):
        super().__init__(master)
        self.title("Network Discovery")
        self.geometry("440x560")
        self.attributes('-topmost', True)
        self.overrideredirect(True)
        self.configure(fg_color="#1A1A1A")
        self.on_add = on_add_callback

        # ── Drag handle (header) ──────────────────────
        hdr = ctk.CTkFrame(self, fg_color="#101012", corner_radius=0, height=28)
        hdr.pack(fill="x")
        lbl = ctk.CTkLabel(hdr, text="NETWORK DISCOVERY", font=("Inter", 10, "bold"), text_color="#555")
        lbl.pack(side="left", padx=10)
        ctk.CTkButton(hdr, text="✕", width=28, height=28, fg_color="transparent",
                      text_color="#555", hover_color="#330000",
                      command=self.destroy).pack(side="right")
        make_draggable(self, hdr)
        make_draggable(self, lbl)

        self.label = ctk.CTkLabel(self, text="SCANNING NETWORK…",
                                   font=("Inter", 12, "bold"))
        self.label.pack(pady=20)

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="#111111", corner_radius=10)
        self.list_frame.pack(expand=True, fill="both", padx=20, pady=5)

        # Manual entry ────────────────────────────────
        sep = ctk.CTkFrame(self, fg_color="#222222", height=1)
        sep.pack(fill="x", padx=20, pady=(10, 0))

        man = ctk.CTkFrame(self, fg_color="transparent")
        man.pack(fill="x", padx=20, pady=8)
        ctk.CTkLabel(man, text="Manual IP:", font=("Inter", 10),
                     text_color="#666").pack(side="left")
        self.ip_entry = ctk.CTkEntry(man, width=130, placeholder_text="192.168.x.x")
        self.ip_entry.pack(side="left", padx=8)
        ctk.CTkButton(man, text="ADD", width=50, fg_color="#1E90FF",
                      command=self._add_manual).pack(side="left")

        self.status = ctk.CTkLabel(self, text="Searching on port 5000…",
                                    font=("Inter", 9), text_color="#555")
        self.status.pack(pady=6)

        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        found = DiscoveryService.find_devices(timeout=1.5)
        self.after(0, lambda: self._show_results(found))

    def _show_results(self, devices):
        for c in self.list_frame.winfo_children(): c.destroy()
        self.label.configure(text=f"DISCOVERY COMPLETE  ({len(devices)} found)")

        if not devices:
            ctk.CTkLabel(self.list_frame,
                         text="No devices responded.\nCheck UDP on port 5000.",
                         text_color="#555").pack(pady=40)
            return

        for dev in devices:
            self._build_device_row(dev)

    def _build_device_row(self, dev):
        f = ctk.CTkFrame(self.list_frame, fg_color="#222222", corner_radius=6)
        f.pack(fill="x", pady=3, padx=3)

        info = ctk.CTkFrame(f, fg_color="transparent")
        info.pack(fill="x", padx=12, pady=8)

        ctk.CTkLabel(info, text=dev['name'],
                     font=("Inter", 11, "bold")).pack(anchor="w")
        ctk.CTkLabel(info, text=dev['ip'],
                     font=("Inter", 9), text_color="#666").pack(anchor="w")

        # Name override
        name_var = tk.StringVar(value=dev['name'])
        ctk.CTkEntry(info, textvariable=name_var,
                     width=180, placeholder_text="Device Name").pack(anchor="w", pady=(4,0))

        # Type selector
        label_list = [t[1] for t in self.DEVICE_TYPES]
        key_list   = [t[0] for t in self.DEVICE_TYPES]

        default_idx = 0
        for i, k in enumerate(key_list):
            if dev.get('type', '') == k:
                default_idx = i
                break

        type_var = ctk.StringVar(value=label_list[default_idx])
        ctk.CTkOptionMenu(info, values=label_list,
                          variable=type_var, width=180,
                          fg_color="#1A1A1A", button_color="#333").pack(anchor="w", pady=(4,0))

        def do_add(d=dev, nv=name_var, tv=type_var, kl=key_list, ll=label_list):
            chosen_type = kl[ll.index(tv.get())]
            self.on_add({"name": nv.get(), "ip": d['ip'], "type": chosen_type})
            self.destroy()

        ctk.CTkButton(info, text="ADD DEVICE", fg_color="#1E90FF",
                      command=do_add).pack(anchor="w", pady=(6,0))

    def _add_manual(self):
        ip = self.ip_entry.get().strip()
        if not ip:
            return
        # Create a placeholder device entry for manual add
        dev = {"name": "ESP Device", "ip": ip, "type": "ant_switch"}
        self._build_device_row(dev)
        self.ip_entry.delete(0, "end")


# ─────────────────────────────────────────────
#  SPLASH SCREEN
# ─────────────────────────────────────────────

class SplashScreen(ctk.CTkToplevel):
    """
    Branded boot screen.
    - Window: 60% of screen height (20% larger than before), centered.
    - All sizing is proportional — no hard-coded pixels.
    - Progress bar is part of the centered content block.
    - Click anywhere or wait DURATION_MS to dismiss.
    """
    DURATION_MS = 10000




    def __init__(self, master, on_done):
        super().__init__(master)
        self._on_done = on_done
        self._done    = False

        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.configure(fg_color="#0A0A0A")

        # ── Dimensions ───────────────────────────────────────────────
        self.update_idletasks()
        sh = self.winfo_screenheight()
        sw = self.winfo_screenwidth()
        h  = int(sh * 0.40)
        w  = int(h * 0.65)
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.update_idletasks()

        # ── Proportional sizes ───────────────────────────────────────
        logo_h   = max(90,  min(int(h * 0.34), 160+10))
        fs_title = max(16,  min(int(h * 0.040), 24+10))
        fs_sub   = max(10,  min(int(h * 0.026), 14+10))
        fs_dis   = max(9,   min(int(h * 0.020), 11+10))
        gap_logo = max(2,   h // 30)
        gap_sm   = max(2,   h // 10)
        div_pad  = max(5,   h // 32)
        pb_top   = max(6,   h // 28)
        px_side  = max(22,  w // 6)

        # ── Accent border ─────────────────────────────────────────────
        outer = ctk.CTkFrame(self, fg_color="#0A0A0A", corner_radius=8)
        outer.pack(fill="both", expand=True, padx=2, pady=2)
        inner = ctk.CTkFrame(outer, fg_color="#0A0A0A", corner_radius=7)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        content = ctk.CTkFrame(inner, fg_color="transparent")
        content.pack(fill="both", expand=True)

        top_spc = ctk.CTkFrame(content, fg_color="transparent", height=gap_logo)
        top_spc.pack(side="top", expand=True, fill="both")

        # ── Logo ──────────────────────────────────────────────────────
        self._logo_img = None
        logo_path = resource_path("RZSFIN.ico")
        try:
            img = Image.open(logo_path).convert("RGBA")
            img = img.resize((logo_h, logo_h), Image.LANCZOS)
            self._logo_img = ctk.CTkImage(light_image=img, dark_image=img, size=(logo_h, logo_h))
            ctk.CTkLabel(content, image=self._logo_img, text="").pack()
        except Exception:
            ph = ctk.CTkFrame(content, fg_color="#1565C0", width=logo_h, height=logo_h, corner_radius=10)
            ph.pack()

        ctk.CTkFrame(content, fg_color="transparent", height=gap_sm).pack()

        # ── Text ──────────────────────────────────────────────────────
        ctk.CTkLabel(content, text="RadioZuluSystems",
                     font=ctk.CTkFont(family="Inter", size=fs_title, weight="bold"),
                     text_color="#FFFFFF").pack()

        ctk.CTkLabel(content, text="The Paragon Suite",
                     font=ctk.CTkFont(family="Inter", size=fs_sub),
                     text_color="#1E90FF").pack(pady=(2, 0))

        ctk.CTkFrame(content, fg_color="#1E1E26", height=1).pack(fill="x", padx=px_side, pady=div_pad)

        # ── Progress Bar ──────────────────────────────────────────────
        ctk.CTkFrame(content, fg_color="transparent", height=pb_top).pack()
        self._pb = ctk.CTkProgressBar(content, height=3, fg_color="#141418", progress_color="#1E90FF")
        self._pb.pack(fill="x", padx=px_side)
        self._pb.set(0)

        # ── Footer Info (Side-by-Side) ───────────────────────────────
        footer_frame = ctk.CTkFrame(content, fg_color="transparent")
        footer_frame.pack(fill="x", padx=px_side, pady=(4, 0))

        # Copyright Left
        self.lbl_copy = ctk.CTkLabel(footer_frame, text="© YL3RZ 2026",
                                     font=ctk.CTkFont(family="Inter", size=fs_dis),
                                     text_color="#888888")
        self.lbl_copy.pack(side="left")

        # Email Right
        self.lbl_mail = ctk.CTkLabel(footer_frame, text="YL3RZ@LRAL.LV",
                                     font=ctk.CTkFont(family="Inter", size=fs_dis),
                                     text_color="#888888")
        self.lbl_mail.pack(side="right")

        bot_spc = ctk.CTkFrame(content, fg_color="transparent")
        bot_spc.pack(side="top", expand=True, fill="both")

        # ── Click-anywhere dismiss ────────────────────────────────────
        # Added the new footer frame and labels to the binding list
        clickable_widgets = [
            self, outer, inner, content, top_spc, bot_spc, 
            footer_frame, self.lbl_copy, self.lbl_mail,
        ]
        for w_ in clickable_widgets:
            w_.bind("<Button-1>", lambda _e: self._close())

        # ── Start animation ──────────────────────────────────────────
        self._start_ms = int(time.time() * 1000)
        self._animate()

    def _animate(self):
        if self._done:
            return
        elapsed = int(time.time() * 1000) - self._start_ms
        frac    = min(elapsed / self.DURATION_MS, 1.0)
        self._pb.set(frac)
        if frac >= 1.0:
            self._close()
        else:
            self.after(30, self._animate)

    def _close(self):
        if self._done:
            return
        self._done = True
        self.destroy()
        self._on_done()

class AntennaMini(ctk.CTk):
    def __init__(self):
        super().__init__()

        try:
            myappid = 'yl3rz.antennamini.switch'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

        icon_path = resource_path("RZSFIN.ico")
        if os.path.exists(icon_path):
            try: self.iconbitmap(icon_path)
            except Exception: pass

        # ── Core state ────────────────────────────────
        self.on_top            = tk.BooleanVar(value=True)
        self.n1mm_auto_switch  = tk.BooleanVar(value=False)
        self.n1mm_port         = 12060
        self.ui_scale          = 1.0
        self.config_data       = []   # list of button dicts
        self.esp_devices       = []   # list of {name, ip, type}
        self.ui_buttons        = []
        self.debug_win         = None
        self.debug_history     = []
        self.current_freq_khz  = 0
        self.freq_label        = None
        self.last_auto_fired_idx = -1
        self.current_active_band = ""

        # ── Per-device relay state (ip → list[bool]) ──
        self.relay_states      = {}   # ip → [bool, ...]
        self.esp_online        = {}   # ip → bool
        self.connections       = {}   # ip → ESPConnection

        # ── TX/RX band state ─────────────────────────
        # band_name → {"tx_idx": int, "rx_idx": int|None, "rx_active": bool}
        self.band_state    = {}
        self.sub_panel     = None    # active AntennaSubPanel
        self.ptt_active    = False
        self.n1mm_warning_suppressed_idx = None

        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.load_config()
        self.refresh_ui()

        self.n1mm_listener = N1MMListener(
            self.n1mm_port, self._on_n1mm_freq, self._on_n1mm_timeout)
        self.n1mm_listener.start()

        self._start_connections()

    # ── Connection management ──────────────────────────

    def _start_connections(self):
        """Create and start an ESPConnection for every configured device."""
        for dev in self.esp_devices:
            ip = dev['ip']
            if ip not in self.connections:
                conn = ESPConnection(
                    dev,
                    on_state_cb=self._on_esp_state,
                    on_ptt_cb=self._on_ptt_event,
                    log_cb=self.log_debug,
                )
                self.connections[ip] = conn
                self.relay_states[ip] = [False] * NUM_RELAYS
                self.esp_online[ip]   = False
                conn.start()

    def _stop_connections(self):
        for conn in self.connections.values():
            conn.stop()
        self.connections.clear()

    def _on_esp_state(self, ip, relays):
        """Called from ESPConnection thread when relay state arrives."""
        if relays is None:
            self.esp_online[ip] = False
        else:
            self.esp_online[ip]   = True
            self.relay_states[ip] = relays
        if self.winfo_exists():
            self.after(0, self._update_ui_leds)

    def _on_ptt_event(self, active: bool):
        """Called when PTT reader ESP pushes a PTT state change."""
        self.ptt_active = active
        self.n1mm_warning_suppressed_idx = None
        self.log_debug(f"PTT: {'ACTIVE (TX)' if active else 'INACTIVE (RX)'}")
        if self.winfo_exists():
            self.after(0, self._handle_ptt_switch, active)

    def _handle_ptt_switch(self, active: bool):
        """On PTT active: if RX antenna selected for current band, fire TX."""
        band = freq_to_band(self.current_freq_khz) or self.current_active_band
        if band:
            if band not in self.band_state:
                # Search for any button that matches this band to find its tx_idx
                for i, cfg in enumerate(self.config_data):
                    if cfg.get("role") == "tx_rx" and band in [x.strip() for x in cfg.get("band","").split(",")]:
                        self.band_state[band] = {"tx_idx": i, "rx_idx": None, "rx_active": False}
                        break

            if band in self.band_state:
                bs = self.band_state[band]
                if active:
                    # TRANSMITTING
                    if bs.get("rx_active"):
                        tx_idx = bs.get("tx_idx")
                        if tx_idx is not None:
                            self.log_debug(f"PTT TX: Hopping RX → TX ({band})")
                            self.fire_profile(tx_idx, record_band=False)
                else:
                    # RECEIVING
                    if bs.get("rx_active"):
                        rx_idx = bs.get("rx_idx")
                        if rx_idx is not None:
                            self.log_debug(f"PTT RX: Returning to RX antenna ({band})")
                            self.fire_profile(rx_idx, record_band=False)
        
        if self.winfo_exists():
            self._update_ui_leds()

    # ── Config load / save ─────────────────────────────

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            self.create_default()
        try:
            tree = ET.parse(CONFIG_FILE)
            root = tree.getroot()

            self.on_top.set(root.findtext("always_on_top", "true") == "true")
            w = int(root.findtext("width",  "240"))
            h = int(root.findtext("height", "420"))
            x = root.findtext("pos_x", "100")
            y = root.findtext("pos_y", "100")
            self.ui_scale = w / 240.0
            self.geometry(f"{w}x{h}+{x}+{y}")

            # Devices
            self.esp_devices = []
            devs_elem = root.find("esp_devices")
            if devs_elem is not None:
                for d in devs_elem.findall("esp"):
                    self.esp_devices.append({
                        "name": d.get("name", "ESP"),
                        "ip":   d.get("ip",   "127.0.0.1"),
                        "type": d.get("type",  "ant_switch"),
                    })

            # N1MM
            n1mm = root.find("n1mm")
            if n1mm is not None:
                self.n1mm_port = int(n1mm.get("port", "12060"))
                self.n1mm_auto_switch.set(n1mm.get("auto_switch", "false") == "true")

            # Buttons — support both new sub-element and legacy flat format
            self.config_data = []
            for b in root.findall("button"):
                name  = b.get("name", "UNNAMED")
                role  = b.get("role", "tx_rx")   # tx_rx | rx_only | off
                band  = b.get("band", "").strip()
                flow  = int(b.get("freq_low",  "0"))
                fhigh = int(b.get("freq_high", "0"))

                if not band and flow > 0:
                    inferred = freq_to_band(flow + 2)
                    if inferred: band = inferred

                # New multi-ESP format: <relay esp="..." index="N" state="true"/>
                relay_map = {}   # (esp_name_or_ip, relay_idx) → bool
                sub_relays = b.findall("relay")
                if sub_relays:
                    for r in sub_relays:
                        esp_ref = r.get("esp", "")
                        idx     = int(r.get("index", "0"))
                        state   = r.get("state", "false").lower() == "true"
                        relay_map[(esp_ref, idx)] = state
                else:
                    # Legacy flat format: relay0="true" relay1="false" …
                    # Assign to the first ant_switch device
                    first_switch = next(
                        (d['name'] for d in self.esp_devices
                         if d['type'] in ('ant_switch', 'both')), "")
                    for i in range(NUM_RELAYS):
                        val = b.get(f"relay{i}", "false")
                        relay_map[(first_switch, i)] = (val.lower() == "true")

                self.config_data.append({
                    "name":      name,
                    "role":      role,
                    "band":      band,
                    "freq_low":  flow,
                    "freq_high": fhigh,
                    "relay_map": relay_map,
                })

        except ET.ParseError as e:
            # Create a backup before failing
            try:
                import shutil
                if os.path.exists(CONFIG_FILE):
                    shutil.copy(CONFIG_FILE, CONFIG_FILE + ".error.bak")
            except: pass
            
            messagebox.showerror("Config Error",
                f"Your config.xml has a syntax error:\n{e}\n\n"
                "The app cannot start with this error. A backup was created as config.xml.error.bak. "
                "Please fix the XML syntax or delete the file to reset.")
            # Do NOT call create_default here; let the user fix it.
            sys.exit(1)
        except Exception as e:
            print(f"Error loading config: {e}")
            # If the file is missing entirely, then we create default
            if not os.path.exists(CONFIG_FILE):
                self.create_default()
                self.load_config()

    def save_config(self):
        root = ET.Element("config")
        ET.SubElement(root, "always_on_top").text = str(self.on_top.get()).lower()
        # Get dimensions, but guard against tiny dimensions if app is closing/unmapped
        curr_w = self.winfo_width()
        curr_h = self.winfo_height()
        if curr_w < 50 or curr_h < 50:
            return  # Don't save garbage dimensions

        ET.SubElement(root, "width").text  = str(curr_w)
        ET.SubElement(root, "height").text = str(curr_h)
        ET.SubElement(root, "pos_x").text  = str(self.winfo_x())
        ET.SubElement(root, "pos_y").text  = str(self.winfo_y())
        ET.SubElement(root, "scale").text  = str(round(curr_w / 240.0, 2))

        devs_elem = ET.SubElement(root, "esp_devices")
        for d in self.esp_devices:
            ET.SubElement(devs_elem, "esp",
                          name=d["name"], ip=d["ip"], type=d.get("type","ant_switch"))

        ET.SubElement(root, "n1mm",
                      port=str(self.n1mm_port),
                      auto_switch=str(self.n1mm_auto_switch.get()).lower())

        for cfg in self.config_data:
            attrib = {
                "name":      cfg.get("name", "UNNAMED"),
                "role":      cfg.get("role", "tx_rx"),
                "band":      cfg.get("band", ""),
                "freq_low":  str(cfg.get("freq_low", 0)),
                "freq_high": str(cfg.get("freq_high", 0)),
            }
            btn_elem = ET.SubElement(root, "button", **attrib)
            for (esp_ref, idx), state in cfg.get("relay_map", {}).items():
                ET.SubElement(btn_elem, "relay",
                              esp=str(esp_ref),
                              index=str(idx),
                              state="true" if state else "false")

        indent_xml(root)
        try:
            ET.ElementTree(root).write(CONFIG_FILE, xml_declaration=False, encoding="unicode")
        except Exception as e:
            self.log_debug(f"Error saving config: {e}")

    def create_default(self):
        root = ET.Element("config")
        for tag, val in [("always_on_top","true"),("width","240"),
                         ("height","420"),("pos_x","100"),("pos_y","100"),("scale","1.0")]:
            ET.SubElement(root, tag).text = val

        devs = ET.SubElement(root, "esp_devices")
        ET.SubElement(devs, "esp", name="AntSwitch", ip="192.168.88.30", type="ant_switch")

        ET.SubElement(root, "n1mm", port="12060", auto_switch="false")

        defaults = [
            ("80m",  "tx_rx", "80m",  "3500",  "4000",  0),
            ("40m",  "tx_rx", "40m",  "7000",  "7300",  1),
            ("20m",  "tx_rx", "20m",  "14000", "14350", 2),
            ("15m",  "tx_rx", "15m",  "21000", "21450", 3),
            ("10m",  "tx_rx", "10m",  "28000", "29700", 4),
        ]
        for name, role, band, fl, fh, relay_idx in defaults:
            b = ET.SubElement(root, "button",
                              name=name, role=role, band=band,
                              freq_low=fl, freq_high=fh)
            for i in range(NUM_RELAYS):
                ET.SubElement(b, "relay", esp="AntSwitch",
                              index=str(i), state="true" if i == relay_idx else "false")

        b = ET.SubElement(root, "button", name="Off", role="off", band="",
                          freq_low="0", freq_high="0")
        for i in range(NUM_RELAYS):
            ET.SubElement(b, "relay", esp="AntSwitch", index=str(i), state="false")

        indent_xml(root)
        ET.ElementTree(root).write(CONFIG_FILE, xml_declaration=False, encoding="unicode")

    # ── Logging ───────────────────────────────────────

    def log_debug(self, msg):
        now  = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        fmsg = f"[{now}] {msg}"
        print(fmsg)
        self.debug_history.append(fmsg)
        if len(self.debug_history) > 1000:
            self.debug_history.pop(0)
        if self.debug_win and self.debug_win.winfo_exists():
            self.debug_win.log_raw(fmsg)

    # ── N1MM callbacks ────────────────────────────────

    def _on_n1mm_timeout(self):
        if self.current_freq_khz != 0:
            self.current_freq_khz = 0
            self.log_debug("N1MM: timed out")
            self.after(0, self._update_freq_display, "—.— MHz")
        if self.n1mm_auto_switch.get():
            self.n1mm_auto_switch.set(False)
            self.after(0, self._on_n1mm_toggle)

    def _on_n1mm_freq(self, freq_khz):
        self.current_freq_khz = freq_khz
        band    = freq_to_band(freq_khz)
        display = freq_to_mhz_str(freq_khz)
        if band:
            display = f"{display}  ({band})"
        self.log_debug(f"N1MM: {freq_khz} kHz → {display}")
        if self.freq_label:
            self.after(0, self._update_freq_display, display)
        if self.n1mm_auto_switch.get():
            self.after(0, self._auto_switch_band, freq_khz)

    def _update_freq_display(self, text):
        try:
            if self.freq_label and self.freq_label.winfo_exists():
                self.freq_label.configure(text=text)
        except Exception:
            pass

    def _auto_switch_band(self, freq_khz):
        for i, cfg in enumerate(self.config_data):
            if cfg.get("role") not in ("tx_rx",):
                continue
            fl, fh = cfg.get("freq_low", 0), cfg.get("freq_high", 0)
            if fl > 0 and fh > 0 and fl <= freq_khz <= fh:
                if self.last_auto_fired_idx != i:
                    self.log_debug(f"N1MM AUTO: {freq_khz} kHz → {cfg['name']}")
                    self._on_button_click(i, source="n1mm")
                return

    # ── Firing profiles ───────────────────────────────

    def fire_profile(self, index: int, record_band=True):
        """Fire a button profile: send relay changes to all relevant ESPs."""
        if not (0 <= index < len(self.config_data)):
            return
        cfg  = self.config_data[index]
        name = cfg['name']
        role = cfg.get('role', 'tx_rx')
        band = cfg.get('band', '')
        self.log_debug(f"FIRE: {name} (role={role}, band={band})")

        relay_map = cfg.get("relay_map", {})

        # Build per-device delta
        dev_commands = {}   # ip → {relay_idx: state}
        for (esp_ref, idx), target_state in relay_map.items():
            # Resolve esp_ref (name or IP) to actual IP
            ip = self._resolve_esp(esp_ref)
            if not ip:
                continue
            if ip not in dev_commands:
                dev_commands[ip] = {}
            current = self.relay_states.get(ip, [None]*NUM_RELAYS)
            cur_state = current[idx] if idx < len(current) else None
            if cur_state != target_state:
                dev_commands[ip][idx] = target_state

        # Send via WebSocket (non-blocking)
        def _send():
            for ip, changes in dev_commands.items():
                conn = self.connections.get(ip)
                if conn and conn.online:
                    for idx, state in changes.items():
                        conn.send_relay(idx, state)
                        self.log_debug(f"WS {ip} relay{idx}→{state}")
                else:
                    self.log_debug(f"WS OFFLINE: {ip} — skipping")

        threading.Thread(target=_send, daemon=True).start()

        # Update band state
        if record_band and band:
            for b in band.split(","):
                b = b.strip()
                self.current_active_band = b
                if b not in self.band_state:
                    self.band_state[b] = {"tx_idx": None, "rx_idx": None, "rx_active": False}
                bs = self.band_state[b]
                if role == "tx_rx":
                    bs["tx_idx"]    = index
                    bs["rx_active"] = False
                    self.last_auto_fired_idx = index
                elif role == "rx_only":
                    bs["rx_idx"]    = index
                    bs["rx_active"] = True

    def _resolve_esp(self, ref: str) -> str:
        """Resolve an ESP name or IP string to its actual IP address."""
        if not ref:
            # Fall back to first ant_switch device
            for d in self.esp_devices:
                if d['type'] in ('ant_switch', 'both'):
                    return d['ip']
            return ""
        # Check if it's already an IP or exact name match
        for d in self.esp_devices:
            if d['name'] == ref or d['ip'] == ref:
                return d['ip']
        # Try connections dict to see if it's an IP we already connected to
        if ref in self.connections:
            return ref
        # No match: fall back to first ant_switch/both device rather than failing
        for d in self.esp_devices:
            if d['type'] in ('ant_switch', 'both'):
                self.log_debug(f"RESOLVE: '{ref}' not found, using fallback {d['name']} ({d['ip']})")
                return d['ip']
        return ref

    # ── UI build ──────────────────────────────────────

    def refresh_ui(self):
        self.ui_scale = self.winfo_width() / 240.0
        self.configure(fg_color="#0A0A0A")

        if self.on_top.get():
            self.overrideredirect(True)
        else:
            self.overrideredirect(False)
            self.title("Antenna Switch")

        for child in self.winfo_children():
            if isinstance(child, (tk.Toplevel, ctk.CTkToplevel)):
                continue
            child.destroy()

        # ── Header (drag handle + freq + on-top toggle) ──
        self.header_frame = ctk.CTkFrame(self, fg_color="#101012", corner_radius=0, height=28)
        self.header_frame.pack(fill="x")

        self.freq_label = ctk.CTkLabel(
            self.header_frame, text="—.— MHz",
            font=ctk.CTkFont(family="Inter", size=10, weight="bold"),
            text_color="#1E90FF")
        self.freq_label.pack(side="left", padx=10)

        ctk.CTkSwitch(self.header_frame, text="", width=40,
                      variable=self.on_top,
                      command=self.apply_mode_change,
                      progress_color="#1E90FF").pack(side="right", padx=6)

        make_draggable(self, self.header_frame)
        make_draggable(self, self.freq_label)

        # ── N1MM row ─────────────────────────────────
        n1mm_row = ctk.CTkFrame(self, fg_color="transparent")
        n1mm_row.pack(fill="x", padx=10, pady=(2, 0))
        ctk.CTkCheckBox(
            n1mm_row, text="N1MM", width=20, height=20,
            checkbox_width=16, checkbox_height=16,
            font=ctk.CTkFont(family="Inter", size=9),
            text_color="#666666", variable=self.n1mm_auto_switch,
            command=self._on_n1mm_toggle,
            fg_color="#1E90FF", hover_color="#1874CD").pack(side="left")

        # ── Button grid ───────────────────────────────
        self.main_container = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.main_container.pack(expand=True, fill="both", padx=0, pady=5)
        self.build_grid()

        # ── Bottom bar ────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=10, pady=5)

        ctk.CTkButton(bot, text="🐛", width=28, height=28, fg_color="transparent",
                      text_color="#FFFFFF", command=self.open_debug).pack(side="left")
        ctk.CTkLabel(bot, text="© YL3RZ 2026",
                     font=("Inter", 10), text_color="#FFFFFF").pack(side="left", expand=True)
        ctk.CTkButton(bot, text="⚙", width=28, height=28, fg_color="transparent",
                      text_color="#FFFFFF", command=self.open_settings).pack(side="right")

        self.apply_focus_policy()

    def build_grid(self):
        """Build button list. Groups bands with RX options automatically."""
        self.ui_buttons = []
        is_n1mm = self.n1mm_auto_switch.get()

        # Index tx_rx buttons by band so we know which have RX siblings
        band_tx  = {}  # band → idx of tx_rx button
        band_rxs = {}  # band → [list of rx_only button idxs]
        for i, cfg in enumerate(self.config_data):
            role = cfg.get("role", "tx_rx")
            for b in cfg.get("band", "").split(","):
                b = b.strip()
                if not b:
                    continue
                if role == "tx_rx":
                    band_tx[b] = i
                elif role == "rx_only":
                    band_rxs.setdefault(b, []).append(i)

        rendered_rx = set()  # rx idxs already handled by a tx button

        for i, cfg in enumerate(self.config_data):
            role = cfg.get("role", "tx_rx")

            if role == "rx_only" and i in rendered_rx:
                continue  # hidden — shown inside sub-panel

            btn = GlassButton(
                self.main_container,
                name=cfg["name"],
                scale=self.ui_scale,
                command=lambda idx=i: self._on_button_click(idx),
                rename_command=lambda idx=i: self.rename_button(idx),
            )
            btn.set_disabled(is_n1mm and role == "tx_rx")
            btn.pack(fill="x", pady=1)
            self.ui_buttons.append((i, btn))

            # Mark associated RX buttons as handled
            for b in cfg.get("band", "").split(","):
                b = b.strip()
                for rx_i in band_rxs.get(b, []):
                    rendered_rx.add(rx_i)

    def _on_button_click(self, idx: int):
        """Handle a button click — fire profile, and open sub-panel if RX options exist."""
        cfg  = self.config_data[idx]
        role = cfg.get("role", "tx_rx")

        # Fire the TX profile
        self.fire_profile(idx)

        # Check if this band has RX alternatives
        # rx_cfgs is a list of (global_index, cfg_dict)
        band_rx_info = []
        for b in cfg.get("band", "").split(","):
            b = b.strip()
            for j, other in enumerate(self.config_data):
                if other.get("role") == "rx_only" and b in [x.strip() for x in other.get("band","").split(",")]:
                    if (j, other) not in band_rx_info:
                        band_rx_info.append((j, other))

        if band_rx_info and role == "tx_rx":
            # Spawn or replace sub-panel
            if self.sub_panel and self.sub_panel.winfo_exists():
                self.sub_panel.destroy()

            band_label = cfg.get("band", "").split(",")[0].strip() or cfg["name"]

            def on_sub_select(key):
                if key == "main":
                    self.fire_profile(idx, record_band=True)
                else:
                    # key = local index into band_rx_info
                    rx_idx, _ = band_rx_info[key]
                    self.fire_profile(rx_idx, record_band=True)

            self.sub_panel = AntennaSubPanel(
                self, band_label, (idx, cfg), band_rx_info, on_sub_select)
        else:
            # No RX options — close any open sub-panel
            if self.sub_panel and self.sub_panel.winfo_exists():
                self.sub_panel.destroy()
                self.sub_panel = None

    def rename_button(self, index):
        if self.on_top.get():
            return
        new_name = simpledialog.askstring(
            "Rename", f"New name for {self.config_data[index]['name']}:")
        if new_name:
            self.config_data[index]['name'] = new_name.upper()
            self.save_config()
            self.refresh_ui()

    # ── LED updates ───────────────────────────────────

    def _update_ui_leds(self):
        """Hardware-validated LED update. Only shows colors if confirmed by ESP."""
        is_n1mm = self.n1mm_auto_switch.get()
        lock_all = self.ptt_active or is_n1mm
        
        # 1. Update main window buttons
        for (cfg_idx, btn) in self.ui_buttons:
            if not btn.winfo_exists(): continue
            cfg = self.config_data[cfg_idx]
            btn.set_disabled(lock_all)
            
            if self.ptt_active:
                btn.set_status("locked")
                continue
                
            match, role = self._check_hardware_match(cfg)
            if match:
                if role == "rx_only": btn.set_status("rx")
                elif role == "off": btn.set_status("active")
                else:
                    # For tx_rx, only green if at least one relay is active
                    has_true = any(t for (_, _), t in cfg.get("relay_map", {}).items())
                    btn.set_status("active" if has_true else "idle")
            else:
                btn.set_status("idle")

        # 2. Update sub-panel buttons if open
        if self.sub_panel and self.sub_panel.winfo_exists():
            self.sub_panel.set_locked(self.ptt_active)
            if not self.ptt_active:
                for key, btn in self.sub_panel._buttons.items():
                    cfg_idx = self.sub_panel.cfg_map.get(key)
                    if cfg_idx is not None:
                        match, role = self._check_hardware_match(self.config_data[cfg_idx])
                        if match:
                            status = "rx" if role == "rx_only" else "active"
                            # If tx_rx main button, check if any relay is actually on
                            if role == "tx_rx":
                                has_on = any(t for (_, _), t in self.config_data[cfg_idx].get("relay_map", {}).items())
                                status = "active" if has_on else "idle"
                            btn.set_status(status)
                        else:
                            btn.set_status("idle")
    def _check_hardware_match(self, cfg):
        """Returns (is_match: bool, role: str) based on real relay states."""
        relay_map = cfg.get("relay_map", {})
        role = cfg.get("role", "tx_rx")
        if not relay_map and role != "off": return False, role
        
        found_any_esp = False
        for (esp_ref, idx), target in relay_map.items():
            ip = self._resolve_esp(esp_ref)
            if not self.esp_online.get(ip, False): return False, role # Offline
            found_any_esp = True
            current = self.relay_states.get(ip, [])
            val = current[idx] if idx < len(current) else None
            if val != target: return False, role
            
        return found_any_esp or role == "off", role

    # ── Window management ─────────────────────────────

    def _on_n1mm_toggle(self):
        self.save_config()
        is_n1mm = self.n1mm_auto_switch.get()
        self.log_debug(f"N1MM Auto-Switch: {'ON' if is_n1mm else 'OFF'}")
        for _, btn in self.ui_buttons:
            btn.set_disabled(is_n1mm)
        if is_n1mm and self.current_freq_khz > 0:
            self.last_auto_fired_idx = -1
            self._auto_switch_band(self.current_freq_khz)

    def apply_mode_change(self):
        self.save_config()
        self.refresh_ui()

    def apply_focus_policy(self):
        try:
            if not self.winfo_exists():
                return
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            GWL_EXSTYLE   = -20
            WS_EX_NOACT   = 0x08000000
            WS_EX_TOPMOST = 0x00000008
            WS_EX_APPWIN  = 0x00040000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if self.on_top.get():
                style |= (WS_EX_NOACT | WS_EX_TOPMOST)
                style &= ~WS_EX_APPWIN
                self.attributes('-topmost', True)
            else:
                style &= ~(WS_EX_NOACT | WS_EX_TOPMOST)
                style |= WS_EX_APPWIN
                self.attributes('-topmost', False)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def _on_closing(self):
        res = messagebox.askyesnocancel(
            "Exit",
            "Drop all antennas before closing?\n\nYes = Drop All\nNo = Keep\nCancel = Stay")
        if res is None:
            return
        if res:
            self.log_debug("Dropping all relays on exit...")
            for ip, conn in self.connections.items():
                if conn.online:
                    for i in range(NUM_RELAYS):
                        conn.send_relay(i, False)
            time.sleep(0.3)

        self.save_config()
        self.n1mm_listener.stop()
        self._stop_connections()
        if self.sub_panel and self.sub_panel.winfo_exists():
            self.sub_panel.destroy()
        self.destroy()

    # ── Settings UI ───────────────────────────────────

    def open_settings(self):
        win = ctk.CTkToplevel(self)
        win.title("Setup")
        win.geometry("310x520")
        win.attributes('-topmost', True)
        win.overrideredirect(True)
        win.configure(fg_color="#1A1A1A")

        # ── Drag handle (header) ──────────────────────
        hdr = ctk.CTkFrame(win, fg_color="#101012", corner_radius=0, height=28)
        hdr.pack(fill="x")
        lbl = ctk.CTkLabel(hdr, text="SETUP", font=("Inter", 10, "bold"), text_color="#555")
        lbl.pack(side="left", padx=10)
        ctk.CTkButton(hdr, text="✕", width=28, height=28, fg_color="transparent",
                      text_color="#555", hover_color="#330000",
                      command=win.destroy).pack(side="right")
        make_draggable(win, hdr)
        make_draggable(win, lbl)

        def section(text):
            ctk.CTkLabel(win, text=text, font=("Inter", 10, "bold"),
                         text_color="#555").pack(pady=(14, 4))

        section("STATION CONFIGURATION")
        ctk.CTkButton(win, text="EDIT XML CONFIG", fg_color="#2B2B2B",
                      command=lambda: self.launch_xml_editor(win)).pack(pady=4, padx=30, fill="x")
        ctk.CTkButton(win, text="OPEN DEBUG CONSOLE", fg_color="#333333",
                      command=self.open_debug).pack(pady=4, padx=30, fill="x")

        section("LINKED HARDWARE")
        self.dev_frame = ctk.CTkScrollableFrame(win, fg_color="#111111", height=220)
        self.dev_frame.pack(fill="x", padx=18, pady=4)
        self._refresh_device_list()

        ctk.CTkButton(win, text="🔍 NETWORK DISCOVERY", fg_color="#1E90FF",
                      font=("Inter", 11, "bold"),
                      command=lambda: DiscoveryWindow(self, self.add_device)
                      ).pack(pady=14, padx=30, fill="x")

    def _refresh_device_list(self):
        for c in self.dev_frame.winfo_children():
            c.destroy()
        if not self.esp_devices:
            ctk.CTkLabel(self.dev_frame,
                         text="No devices.\nUse Discovery to scan.",
                         text_color="#444", font=("Inter", 9)).pack(pady=20)
            return

        for dev in self.esp_devices:
            f = ctk.CTkFrame(self.dev_frame, fg_color="#1A1A1A")
            f.pack(fill="x", pady=1)

            ip       = dev['ip']
            online   = self.esp_online.get(ip, False)
            dot_col  = "#00FF7F" if online else "#FF3131"
            type_lbl = {"ant_switch": "ANT", "ptt_reader": "PTT", "both": "A+P"}.get(
                dev.get("type", "ant_switch"), "???")

            ctk.CTkLabel(f, text="●", text_color=dot_col,
                         font=("Inter", 8)).pack(side="left", padx=(6, 0))
            ctk.CTkLabel(f, text=f"[{type_lbl}] {dev['name']} ({ip})",
                         font=("Inter", 9), text_color="#E0E0E0").pack(side="left", padx=4)

            ctk.CTkButton(f, text="✕", width=22, height=20,
                          fg_color="#FF3131", text_color="white",
                          command=lambda d=dev: self._remove_device(d)
                          ).pack(side="right", padx=4)

    def add_device(self, dev):
        if not any(d['ip'] == dev['ip'] for d in self.esp_devices):
            self.esp_devices.append(dev)
            self.save_config()
            # Start connection for new device
            ip   = dev['ip']
            conn = ESPConnection(dev,
                                 on_state_cb=self._on_esp_state,
                                 on_ptt_cb=self._on_ptt_event,
                                 log_cb=self.log_debug)
            self.connections[ip]   = conn
            self.relay_states[ip]  = [False] * NUM_RELAYS
            self.esp_online[ip]    = False
            conn.start()
            self.log_debug(f"DEVICE ADDED: {dev['name']} ({ip})")
        if hasattr(self, 'dev_frame') and self.dev_frame.winfo_exists():
            self._refresh_device_list()

    def _remove_device(self, dev):
        ip = dev['ip']
        if ip in self.connections:
            self.connections[ip].stop()
            del self.connections[ip]
        self.esp_devices  = [d for d in self.esp_devices  if d['ip'] != ip]
        self.relay_states.pop(ip, None)
        self.esp_online.pop(ip, None)
        self.log_debug(f"DEVICE REMOVED: {ip}")
        self.save_config()
        if hasattr(self, 'dev_frame') and self.dev_frame.winfo_exists():
            self._refresh_device_list()

    def launch_xml_editor(self, parent_win):
        self.save_config()
        # Create a backup before manual editing
        try:
            import shutil
            shutil.copy(CONFIG_FILE, CONFIG_FILE + ".bak")
        except: pass
        
        def run_editor():
            subprocess.run(['notepad.exe', CONFIG_FILE])
            self.after(0, self._reboot_app)
        threading.Thread(target=run_editor, daemon=True).start()

    def _reboot_app(self):
        self.log_debug("Rebooting with new config...")
        self.n1mm_listener.stop()
        self._stop_connections()
        args = list(sys.argv)
        if getattr(sys, 'frozen', False):
            subprocess.Popen([sys.executable] + args[1:])
        else:
            subprocess.Popen([sys.executable] + args)
        self.destroy()
        sys.exit(0)

    def open_debug(self):
        if not self.debug_win or not self.debug_win.winfo_exists():
            self.debug_win = DebugConsole(self)
        if self.debug_win and self.debug_win.winfo_exists():
            self.debug_win.focus()


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    mutex_name = "AntennaMini_Unique_Instance_Mutex"
    kernel32   = ctypes.windll.kernel32
    mutex      = kernel32.CreateMutexW(None, False, mutex_name)
    if kernel32.GetLastError() == 183:
        print("Application is already running.")
        sys.exit(0)

    app = AntennaMini()
    app.withdraw()   # hide until splash closes

    def _on_splash_done():
        app.deiconify()

    _splash = SplashScreen(app, _on_splash_done)
    app.mainloop()
