import numpy
_orig_fromstring = numpy.fromstring
def _fix_fromstring(buf, dtype=float, count=-1, sep=''):
    if sep == '':
        if not isinstance(buf, (bytes, bytearray)):
            try:
                buf = memoryview(buf).tobytes()
            except Exception:
                try:
                    buf = bytes(buf)
                except Exception:
                    pass
        return numpy.frombuffer(buf, dtype=dtype, count=count)
    return _orig_fromstring(buf, dtype=dtype, count=count, sep=sep)
numpy.fromstring = _fix_fromstring

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import socket
import threading
import subprocess
import struct
import time
import queue
import sys
import json

try:
    import soundcard as sc
    SOUNDCARD_AVAILABLE = True
except ImportError:
    SOUNDCARD_AVAILABLE = False

DEFAULT_SAMPLE_RATE = 44100
DEFAULT_CHANNELS = 2
DEFAULT_SAMPLE_WIDTH = 2  # 16-bit
DEFAULT_CHUNK_SIZE = 2048
DEFAULT_PORT = 9876
DEFAULT_BUFFER = 16

BG_COLOR       = "#191A1B"
SURFACE_COLOR  = "#232424"
OVERLAY_COLOR  = "#191A1B"
TEXT_COLOR      = "#FFFFFF"
SUBTEXT_COLOR  = "#FFFFFF"
ACCENT_COLOR   = "#FFFFFF"
GREEN_COLOR    = "#73C76B"
RED_COLOR      = "#C72150"
YELLOW_COLOR   = "#F9E2AF"
BLUE_BTN       = "#064E18"
BLUE_BTN_HOVER = "#357AF5"
STOP_BTN       = "#D20F39"
STOP_BTN_HOVER = "#E33E5C"


class ADBManager:
    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self.connected = False
        self.device_serial = None
        self.adb_available = self._check_adb()

    def _check_adb(self):
        try:
            r = subprocess.run(['adb', 'version'], capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_devices(self):
        if not self.adb_available: return []
        try:
            r = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=5)
            devices = []
            for line in r.stdout.strip().split('\n')[1:]:
                parts = line.strip().split('\t')
                if len(parts) == 2 and parts[1] == 'device':
                    devices.append(parts[0])
            return devices
        except: return []

    def setup_port_forward(self, serial=None):
        cmd = ['adb'] + (['-s', serial] if serial else []) + ['forward', f'tcp:{self.port}', f'tcp:{self.port}']
        try:
            if subprocess.run(cmd, capture_output=True, text=True, timeout=10).returncode == 0:
                self.connected, self.device_serial = True, serial
                return True
            return False
        except subprocess.TimeoutExpired: return False

    def remove_port_forward(self):
        cmd = ['adb'] + (['-s', self.device_serial] if self.device_serial else []) + ['forward', '--remove', f'tcp:{self.port}']
        try: subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except: pass
        self.connected, self.device_serial = False, None


class AudioCapture:
    def __init__(self, sample_rate=DEFAULT_SAMPLE_RATE, channels=DEFAULT_CHANNELS, chunk_size=DEFAULT_CHUNK_SIZE, buffer_size=DEFAULT_BUFFER):
        self.sample_rate, self.channels, self.chunk_size, self.sample_width = sample_rate, channels, chunk_size, DEFAULT_SAMPLE_WIDTH
        self.audio_queue = queue.Queue(maxsize=buffer_size)
        self.capturing = False; self._thread = None; self._source = None

    def set_source(self, device, is_loopback=True): self._source = device
    def start(self):
        if self.capturing: return
        self.capturing = True; self._thread = threading.Thread(target=self._loop, daemon=True); self._thread.start()
    def stop(self):
        self.capturing = False
        if self._thread: self._thread.join(timeout=3)
        while not self.audio_queue.empty():
            try: self.audio_queue.get_nowait()
            except: break
    def get_audio(self, timeout=0.05):
        try: return self.audio_queue.get(timeout=timeout)
        except queue.Empty: return None

    def _loop(self):
        try:
            with self._source.recorder(samplerate=self.sample_rate, channels=self.channels) as recorder:
                while self.capturing:
                    try:
                        data = recorder.record(numframes=self.chunk_size)
                        pcm = (data * 32767).astype(numpy.int16)
                        try: self.audio_queue.put(pcm.tobytes(), timeout=0.1)
                        except queue.Full: pass
                    except: 
                        if self.capturing: time.sleep(0.01)
        except Exception as e:
            if self.capturing: self.capturing = False


class AudioStreamer:
    def __init__(self, port=DEFAULT_PORT, sample_rate=DEFAULT_SAMPLE_RATE, channels=DEFAULT_CHANNELS):
        self.port, self.sample_rate, self.channels, self.sample_width = port, sample_rate, channels, DEFAULT_SAMPLE_WIDTH
        self.streaming = self.connected = False; self._sock = self._thread = None
        self.bytes_sent = 0; self.start_time = 0; self.volume = 1.0

    def connect(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(8); self._sock.connect(('127.0.0.1', self.port)); self._sock.settimeout(None)
            cfg = json.dumps({'sample_rate': self.sample_rate, 'channels': self.channels, 'sample_width': self.sample_width}).encode('utf-8')
            self._sock.sendall(struct.pack('!I', len(cfg))); self._sock.sendall(cfg)
            if self._sock.recv(2) != b'OK': self._sock.close(); return False
            self.connected = True; return True
        except: self._sock = None; return False

    def start(self, capture):
        if self.streaming: return
        self.streaming, self.bytes_sent, self.start_time = True, 0, time.time()
        self._thread = threading.Thread(target=self._loop, args=(capture,), daemon=True); self._thread.start()

    def stop(self):
        self.streaming = False
        if self._thread: self._thread.join(timeout=2)
        self.disconnect()

    def disconnect(self):
        self.connected = False
        if self._sock:
            try: self._sock.shutdown(socket.SHUT_RDWR)
            except: pass
            try: self._sock.close()
            except: pass
            self._sock = None

    def set_volume(self, v): self.volume = max(0.0, min(1.0, v))

    def _loop(self, capture):
        while self.streaming and self.connected:
            data = capture.get_audio(timeout=0.1)
            if not data: continue
            if self.volume != 1.0:
                arr = numpy.frombuffer(data, dtype=numpy.int16).copy()
                data = (arr * self.volume).astype(numpy.int16).tobytes()
            try:
                self._sock.sendall(data); self.bytes_sent += len(data)
            except: self.connected = self.streaming = False; break

    def get_stats(self):
        elapsed = time.time() - self.start_time if self.start_time else 0
        return {'bytes_sent': self.bytes_sent, 'elapsed': elapsed, 'bitrate': (self.bytes_sent * 8 / elapsed) if elapsed > 0 else 0}



class AndroidSpeakerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Copeline")
        self.root.geometry("580x740")
        self.root.minsize(520, 680)
        self.root.configure(bg=BG_COLOR)

        self.adb = ADBManager()
        self.capture = None
        self.streamer = None
        self._audio_devices = [] 
        self._stats_thread = None

        self._build_gui()
        self._check_adb()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _make_card(self, parent, title=""):
        frame = tk.Frame(parent, bg=SURFACE_COLOR, highlightthickness=0, padx=16, pady=9)
        frame.pack(fill=tk.X, pady=(0, 12))
        if title:
            tk.Label(frame, text=title, bg=SURFACE_COLOR, fg=ACCENT_COLOR, 
                     font=('Segoe UI', 11, 'bold'), anchor='w').pack(fill=tk.X, pady=(0, 8))
        inner = tk.Frame(frame, bg=SURFACE_COLOR)
        inner.pack(fill=tk.X)
        return inner

    def _make_label(self, parent, text, color=SUBTEXT_COLOR, size=10, bold=False):
        weight = 'bold' if bold else 'normal'
        return tk.Label(parent, text=text, bg=SURFACE_COLOR, fg=color, font=('Segoe UI', size, weight), anchor='w')

    def _make_button(self, parent, text, command, color=OVERLAY_COLOR, text_color=TEXT_COLOR, hover_color=None, width=None):
        btn = tk.Button(parent, text=text, command=command, bg=color, fg=text_color,
                        activebackground=hover_color or color, activeforeground=text_color,
                        font=('Segoe UI', 10), relief='flat', cursor='hand2', pady=6,
                        width=width, bd=0)
        return btn
    
    def _build_gui(self):
        main = tk.Frame(self.root, bg=BG_COLOR, padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)

        tk.Label(main, text="🔊  Copeline ", bg=BG_COLOR, fg=TEXT_COLOR,
                 font=('Segoe UI', 20, 'bold')).pack(anchor='w', pady=(0, 2))
        tk.Label(main, text="Stream system audio to your phone via USB", bg=BG_COLOR, fg=SUBTEXT_COLOR,
                 font=('Segoe UI', 10)).pack(anchor='w', pady=(0, 16))

        c1 = self._make_card(main, "Connection")
        row1 = tk.Frame(c1, bg=SURFACE_COLOR); row1.pack(fill=tk.X)
        self._make_button(row1, "🔄 Refresh", self._refresh_devices, OVERLAY_COLOR, TEXT_COLOR).pack(side=tk.LEFT)
        self.lbl_adb = self._make_label(row1, "", GREEN_COLOR, 10, True)
        self.lbl_adb.pack(side=tk.LEFT, padx=12)

        self.cmb_dev = ttk.Combobox(c1, state='readonly', font=('Segoe UI', 10))
        self.cmb_dev.pack(fill=tk.X, pady=(8, 8))
        self.cmb_dev.set("No devices found")

        self.btn_conn = self._make_button(c1, "Connect", self._connect, BLUE_BTN, "#000000", BLUE_BTN_HOVER)
        self.btn_conn.pack(fill=tk.X)
        self.btn_conn.configure(state=tk.DISABLED)
        
        self.lbl_conn = self._make_label(c1, "", SUBTEXT_COLOR, 9)
        self.lbl_conn.pack(anchor='w', pady=(6, 0))

        # ── Audio Source Card ──
        c2 = self._make_card(main, "Audio Source")
        row2 = tk.Frame(c2, bg=SURFACE_COLOR); row2.pack(fill=tk.X)
        self._make_label(row2, "Type:", size=10).pack(side=tk.LEFT)
        self.cmb_type = ttk.Combobox(row2, state='readonly', width=22, font=('Segoe UI', 10),
                                     values=["System Audio (Loopback)", "Microphone"])
        self.cmb_type.set("System Audio (Loopback)")
        self.cmb_type.pack(side=tk.LEFT, padx=8)
        self.cmb_type.bind('<<ComboboxSelected>>', lambda e: self._refresh_audio())
        self._make_button(row2, "🔄", self._refresh_audio, OVERLAY_COLOR, TEXT_COLOR, width=3).pack(side=tk.LEFT)

        self.cmb_audio = ttk.Combobox(c2, state='readonly', font=('Segoe UI', 10))
        self.cmb_audio.pack(fill=tk.X, pady=8)
        

        c3 = self._make_card(main, "Controls")
        
        # Volume Slider
        vr = tk.Frame(c3, bg=SURFACE_COLOR); vr.pack(fill=tk.X, pady=(0, 10))
        self._make_label(vr, "🔊 Volume", size=10).pack(side=tk.LEFT)
        self.lbl_vol = self._make_label(vr, "100%", TEXT_COLOR, 10, True)
        self.lbl_vol.pack(side=tk.RIGHT)
        self.var_vol = tk.DoubleVar(value=1.0)
        tk.Scale(vr, from_=0, to=1, resolution=0.05, orient=tk.HORIZONTAL, variable=self.var_vol,
                 command=self._vol_changed, bg=SURFACE_COLOR, fg=TEXT_COLOR, troughcolor=OVERLAY_COLOR,
                 highlightthickness=0, sliderrelief='flat', showvalue=False).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)

        # Buffer Slider
        br = tk.Frame(c3, bg=SURFACE_COLOR); br.pack(fill=tk.X, pady=(0, 12))
        self._make_label(br, "📦 Buffer", size=10).pack(side=tk.LEFT)
        self.lbl_buf = self._make_label(br, f"{DEFAULT_BUFFER}", TEXT_COLOR, 10, True)
        self.lbl_buf.pack(side=tk.RIGHT)
        self.var_buf = tk.IntVar(value=DEFAULT_BUFFER)
        tk.Scale(br, from_=4, to=48, orient=tk.HORIZONTAL, variable=self.var_buf,
                 command=self._buf_changed, bg=SURFACE_COLOR, fg=TEXT_COLOR, troughcolor=OVERLAY_COLOR,
                 highlightthickness=0, sliderrelief='flat', showvalue=False).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)

        # Big Action Button
        self.btn_start = tk.Button(c3, text="Start", command=self._start, 
                                   bg=GREEN_COLOR, fg=BG_COLOR, activebackground="#94D89A",
                                   font=('Segoe UI', 13, 'bold'), relief='flat', cursor='hand2', pady=10)
        self.btn_start.pack(fill=tk.X)
        self.btn_start.configure(state=tk.DISABLED)

        c4 = self._make_card(main, "Status")
        grid = tk.Frame(c4, bg=SURFACE_COLOR); grid.pack(fill=tk.X)
        grid.columnconfigure(1, weight=1)
        
        labels = ['Status']
        self.stat_vals = []
        for i, lbl in enumerate(labels):
            self._make_label(grid, lbl + ":", SUBTEXT_COLOR, 9).grid(row=i, column=0, sticky=tk.W, pady=2)
            v = self._make_label(grid, "—", TEXT_COLOR, 10, True)
            v.grid(row=i, column=1, sticky=tk.W, padx=(12, 0), pady=2)
            self.stat_vals.append(v)

        self.txt_log = scrolledtext.ScrolledText(
            insertbackground=SUBTEXT_COLOR, selectbackground=OVERLAY_COLOR, 
            borderwidth=0, wrap=tk.WORD, relief='flat')
        self.txt_log.pack(fill=tk.BOTH, expand=True)

       

        if SOUNDCARD_AVAILABLE: self._refresh_audio()
        else: self._log("ERROR: soundcard library required.", "ERR")

    def _log(self, msg, lvl="INFO"):
        icons = {"INFO": "•", "ERR": "✗", "OK": "✓", "WARN": "!"}
        color = SUBTEXT_COLOR
        if lvl == "ERR": color = RED_COLOR
        elif lvl == "OK": color = GREEN_COLOR
        elif lvl == "WARN": color = YELLOW_COLOR
        
        self.txt_log.insert(tk.END, f"{icons.get(lvl,'•')} {msg}\n")
        self.txt_log.see(tk.END)

    def _check_adb(self):
        if self.adb.adb_available:
            self.lbl_adb.configure(text="Device Ready", fg=GREEN_COLOR)
            self._refresh_devices()
           

    def _refresh_devices(self):
        devs = self.adb.get_devices()
        if devs:
            self.cmb_dev['values'] = devs; self.cmb_dev.set(devs[0])
            self.btn_conn.configure(state=tk.NORMAL)
            self._log(f"Found {len(devs)} device(s)", "OK")
        else:
            self.cmb_dev.set("No devices found"); self.btn_conn.configure(state=tk.DISABLED)
            self._log("No devices. Check USB cable and debugging.", "WARN")

    def _connect(self):
        dev = self.cmb_dev.get()
        if not dev or dev == "No devices found": return
        self.lbl_conn.configure(text="Connecting...", fg=YELLOW_COLOR)
        if self.adb.setup_port_forward(serial=dev):
            self.lbl_conn.configure(text=f"✓ Port {DEFAULT_PORT} forwarded", fg=GREEN_COLOR)
            self._log("Port forwarded successfully", "OK")
            self.btn_start.configure(state=tk.NORMAL)
            self.btn_conn.configure(text="✕  Disconnect", command=self._disconnect, bg=OVERLAY_COLOR, fg=RED_COLOR, activebackground=RED_COLOR)
            self.cmb_dev.configure(state=tk.DISABLED)
        else:
            self.lbl_conn.configure(text="✗ Failed to forward port", fg=RED_COLOR)
            self._log("Port forward failed", "ERR")

    def _disconnect(self):
        self._stop()
        self.adb.remove_port_forward()
        self.lbl_conn.configure(text="Disconnected", fg=SUBTEXT_COLOR)
        self.btn_start.configure(state=tk.DISABLED)
        self.btn_conn.configure(text="🔗  Connect & Forward Port", command=self._connect, bg=BLUE_BTN, fg="#FFFFFF", activebackground=BLUE_BTN_HOVER)
        self.cmb_dev.configure(state='readonly')

    def _refresh_audio(self):
        if not SOUNDCARD_AVAILABLE: return
        self._audio_devices = []
        names = []
        # Always check microphones (Windows hides loopbacks here)
        for i, mic in enumerate(sc.all_microphones()):
            self._audio_devices.append(('mic', i, mic))
            names.append(f"{i}: {mic.name}")
        if names:
            self.cmb_audio['values'] = names; self.cmb_audio.set(names[0])
        else:
            self.cmb_audio.set("No devices found")

    def _vol_changed(self, *_):
        v = self.var_vol.get()
        self.lbl_vol.configure(text=f"{int(v*100)}%")
        if self.streamer: self.streamer.set_volume(v)

    def _buf_changed(self, *_):
        b = int(self.var_buf.get())
        self.lbl_buf.configure(text=f"{b}")
        if self.capture:
            new_q = queue.Queue(maxsize=b)
            while not self.capture.audio_queue.empty():
                try: new_q.put(self.capture.audio_queue.get_nowait())
                except: break
            self.capture.audio_queue = new_q

    def _start(self):
        sel = self.cmb_audio.get()
        if not sel or "No devices" in sel: return
        try: idx = int(sel.split(":")[0])
        except: return

        device = None
        for _, didx, dobj in self._audio_devices:
            if didx == idx: device = dobj; break
        if not device: return

        buf = int(self.var_buf.get())
        try:
            self.capture = AudioCapture(sample_rate=DEFAULT_SAMPLE_RATE, channels=DEFAULT_CHANNELS, chunk_size=DEFAULT_CHUNK_SIZE, buffer_size=buf)
            self.capture.set_source(device)
        except Exception as e:
            messagebox.showerror("Error", f"Audio init failed:\n{e}"); return

        self.stat_vals[0].configure(text="Connecting...", fg=YELLOW_COLOR)
        self.streamer = AudioStreamer(port=DEFAULT_PORT, sample_rate=DEFAULT_SAMPLE_RATE, channels=DEFAULT_CHANNELS)
        self.streamer.set_volume(self.var_vol.get())

        if not self.streamer.connect():
            messagebox.showerror("Failed", "Cannot reach Android.\nEnsure receiver.py is running in Termux.")
            self.stat_vals[0].configure(text="Failed", fg=RED_COLOR)
            self._log("Connection failed", "ERR"); self.streamer = None; return

        try:
            self.capture.start(); self.streamer.start(self.capture)
        except Exception as e:
            messagebox.showerror("Error", f"Stream failed:\n{e}"); self._stop(); return

        self.stat_vals[0].configure(text="● Streaming", fg=GREEN_COLOR)
        self._log("Streaming started", "OK")
        
        # UI Update for streaming state
        self.btn_start.configure(text="⏹   Stop Streaming", command=self._stop, bg=STOP_BTN, fg="#FFFFFF", activebackground=STOP_BTN_HOVER)
        self.cmb_type.configure(state=tk.DISABLED)
        self.cmb_audio.configure(state=tk.DISABLED)
        self._stats_thread = threading.Thread(target=self._update_stats, daemon=True); self._stats_thread.start()

    def _stop(self):
        if self.streamer: self.streamer.stop(); self.streamer = None
        if self.capture: self.capture.stop(); self.capture = None
        self.stat_vals[0].configure(text="Stopped", fg=SUBTEXT_COLOR)
        self._log("Stopped")
        self.btn_start.configure(text="▶   Start Streaming", command=self._start, bg=GREEN_COLOR, fg=BG_COLOR, activebackground="#94D89A")
        self.cmb_type.configure(state='readonly'); self.cmb_audio.configure(state='readonly')
        for v in self.stat_vals[1:]: v.configure(text="—")

    def _update_stats(self):
        while self.streamer and self.streamer.streaming:
            try:
                st = self.streamer.get_stats()
                br = st['bitrate']
                self.stat_vals[1].configure(text=f"{br/1e6:.1f} Mbps" if br > 1e6 else f"{br/1000:.0f} Kbps")
                b = st['bytes_sent']
                self.stat_vals[2].configure(text=f"{b/1e6:.1f} MB" if b > 1e6 else f"{b/1000:.0f} KB")
                m, s = divmod(int(st['elapsed']), 60)
                self.stat_vals[3].configure(text=f"{m:02d}:{s:02d}")
                time.sleep(0.5)
            except: break

    def _show_help(self):
    
        win = tk.Toplevel(self.root); win.title("Setup"); win.geometry("450x450")
        win.configure(bg=BG_COLOR); win.transient(self.root)

        t = scrolledtext.ScrolledText(win, font=('Segoe UI', 10), bg=SURFACE_COLOR, fg=TEXT_COLOR,
                                      insertbackground=TEXT_COLOR, borderwidth=0, wrap=tk.WORD, padx=15, pady=15)
        t.pack(fill=tk.BOTH, expand=True, padx=15, pady=(15, 5))
        t.insert('1.0'); t.configure(state=tk.DISABLED)

    def _on_close(self):
        self._stop()
        if self.adb.connected: self.adb.remove_port_forward()
        self.root.destroy()

if __name__ == '__main__':
    root = tk.Tk()
    app = AndroidSpeakerApp(root)
    root.mainloop()