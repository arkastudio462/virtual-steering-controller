#!/usr/bin/env python3
"""
Virtual Steering Controller — server PC.

Menyajikan halaman kontrol untuk ponsel (HTTPS + WebSocket) di jaringan lokal,
lalu menerjemahkan input ponsel menjadi gamepad Xbox virtual (ViGEmBus) yang
dibaca oleh game seperti ETS2 dan MudRunner.

Jalankan:  python server.py
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import aiohttp
from aiohttp import web

import gamepad as gpad

try:  # tkinter tidak selalu tersedia; server tetap jalan tanpa GUI
    import tkinter as tk
    from tkinter import messagebox, ttk

    TK_OK = True
except Exception:  # pragma: no cover
    TK_OK = False

VERSION = "1.0.0"
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
CERT_DIR = BASE_DIR / "certs"

DEFAULT_CONFIG: dict = {
    "host": "0.0.0.0",
    "port": 8000,
    "ssl": True,
    "gui": True,
    "open_browser": False,
    "log_level": "INFO",
}

log = logging.getLogger("vsc")


# ===========================================================================
# Konfigurasi & utilitas jaringan
# ===========================================================================

def load_config(path: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in DEFAULT_CONFIG:
                if key in data:
                    cfg[key] = data[key]
    except FileNotFoundError:
        log.debug("config.json tidak ditemukan — memakai default")
    except Exception as exc:
        log.warning("config.json tidak terbaca (%s) — memakai default", exc)
    return cfg


def lan_ipv4s() -> list[str]:
    """Alamat IPv4 lokal (non-loopback), yang utama di urutan pertama."""
    found: list[str] = []

    def add(ip: str) -> None:
        try:
            obj = ipaddress.ip_address(ip)
        except ValueError:
            return
        if obj.version != 4 or obj.is_loopback or obj.is_link_local:
            return
        if ip not in found:
            found.append(ip)

    try:  # jalur 1: lewat soket UDP (tanpa benar-benar mengirim paket)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:  # jalur 2: resolusi hostname
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return found


def build_urls(port: int, use_ssl: bool, hosts: list[str]) -> list[str]:
    scheme = "https" if use_ssl else "http"
    urls = [f"{scheme}://{h}:{port}" for h in hosts]
    urls.append(f"{scheme}://localhost:{port}")
    # unik, pertahankan urutan
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


# ===========================================================================
# Sertifikat HTTPS self-signed (wajib agar sensor ponsel diizinkan browser)
# ===========================================================================

def _gen_with_cryptography(cert: Path, key: Path, hosts: list[str]) -> None:
    import datetime

    import ipaddress as _ip
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "virtual-steering.local")])
    san: list = [x509.IPAddress(_ip.ip_address("127.0.0.1")), x509.DNSName("localhost")]
    for h in hosts:
        try:
            san.append(x509.IPAddress(_ip.ip_address(h)))
        except ValueError:
            san.append(x509.DNSName(h))
    now = datetime.datetime.now(datetime.timezone.utc)
    cert_obj = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    key.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert.write_bytes(cert_obj.public_bytes(serialization.Encoding.PEM))


def _gen_with_openssl(cert: Path, key: Path, san_entries: list[str]) -> None:
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "3650",
        "-subj", "/CN=virtual-steering.local",
        "-addext", "subjectAltName=" + ",".join(san_entries),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)


def ensure_certificate(hosts: list[str]) -> tuple[Path, Path] | None:
    """Buat (sekali) sertifikat self-signed. Hapus folder certs/ untuk membuat ulang."""
    cert = CERT_DIR / "cert.pem"
    key = CERT_DIR / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    try:
        CERT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Tidak bisa membuat folder certs/: %s", exc)
        return None

    san = ["IP:127.0.0.1", "DNS:localhost"]
    for h in hosts:
        try:
            ipaddress.ip_address(h)
            san.append(f"IP:{h}")
        except ValueError:
            san.append(f"DNS:{h}")

    try:
        _gen_with_cryptography(cert, key, hosts)
        log.info("Sertifikat HTTPS dibuat di %s", CERT_DIR)
        return cert, key
    except ImportError:
        log.debug("pustaka cryptography tidak tersedia — mencoba openssl")
    except Exception as exc:
        log.warning("cryptography gagal membuat sertifikat (%s) — mencoba openssl", exc)

    if shutil.which("openssl"):
        try:
            _gen_with_openssl(cert, key, san)
            log.info("Sertifikat HTTPS dibuat di %s (via openssl)", CERT_DIR)
            return cert, key
        except Exception as exc:
            log.warning("openssl gagal membuat sertifikat: %s", exc)
    return None


def make_ssl_context(cert: Path, key: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert), str(key))
    return ctx


# ===========================================================================
# Bridge: state input yang aman dipanggil dari lintas thread
# ===========================================================================

class Bridge:
    def __init__(self, pad) -> None:
        self.pad = pad
        self.lock = threading.Lock()
        self.state = gpad.InputState()
        self.ws: web.WebSocketResponse | None = None
        self.clients = 0
        self.updates = 0
        self.last_msg = 0.0
        self.started = time.monotonic()

    def apply(self, state: gpad.InputState) -> None:
        with self.lock:
            self.state = state
            self.last_msg = time.monotonic()
            self.updates += 1
            self.pad.apply(state)

    def reset(self, reason: str = "") -> bool:
        with self.lock:
            if self.state.neutral:
                return False
            self.state = gpad.InputState()
            self.pad.apply(self.state)
        if reason:
            log.info("Input direset (%s)", reason)
        return True

    def check_timeout(self, limit: float = 1.5) -> None:
        """Jaga-jaga: bila HP berhenti mengirim (layar mati/sinyal putus), netralkan."""
        with self.lock:
            if self.clients == 0 or self.state.neutral:
                return
            if time.monotonic() - self.last_msg > limit:
                self.state = gpad.InputState()
                self.pad.apply(self.state)
                log.warning("Data dari HP hilang > %.1fs — input direset", limit)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "clients": self.clients,
                "updates": self.updates,
                "state": self.state.as_dict(),
                "pad": self.pad.name,
                "pad_label": self.pad.label,
                "pad_ok": self.pad.name != "mock",
                "reason": getattr(self.pad, "reason", ""),
                "uptime": round(time.monotonic() - self.started, 1),
            }


def parse_input_message(data: dict) -> gpad.InputState:
    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            value = float(data.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    try:
        buttons = int(data.get("p", 0)) & 0xFFFF
    except (TypeError, ValueError):
        buttons = 0
    return gpad.InputState(
        steer=num("s", 0.0, -1.0, 1.0),
        gas=num("g", 0.0, 0.0, 1.0),
        brake=num("b", 0.0, 0.0, 1.0),
        buttons=buttons,
    )


# ===========================================================================
# Aplikasi aiohttp (halaman + API + WebSocket)
# ===========================================================================

def make_app(bridge: Bridge) -> web.Application:
    app = web.Application()
    app["bridge"] = bridge

    async def index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB_DIR / "index.html")

    async def favicon(_request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def status(_request: web.Request) -> web.Response:
        snap = bridge.snapshot()
        snap.update({
            "version": VERSION,
            "urls": app.get("urls", []),
            "ssl": app.get("ssl_on", False),
        })
        return web.json_response(snap)

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)

        # Satu sesi kendali: klien baru mengambil alih klien lama.
        old = bridge.ws
        bridge.ws = ws
        with bridge.lock:
            bridge.clients += 1
        log.info("Klien terhubung (total %d)", bridge.clients)
        if old is not None and not old.closed:
            try:
                await old.close(code=aiohttp.WSCloseCode.POLICY_VIOLATION,
                                message=b"sesi diambil alih")
            except Exception as exc:
                log.warning("Gagal menutup sesi lama: %s", exc)

        try:
            await ws.send_json({
                "t": "hello",
                "pad": bridge.pad.name,
                "label": bridge.pad.label,
                "version": VERSION,
            })
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except ValueError:
                        continue
                    kind = data.get("t") if isinstance(data, dict) else None
                    if kind == "i":
                        bridge.apply(parse_input_message(data))
                    elif kind == "ping":
                        try:
                            await ws.send_json({"t": "pong", "ts": data.get("ts")})
                        except Exception:
                            pass
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("WebSocket error: %s", exc)
        finally:
            with bridge.lock:
                bridge.clients = max(0, bridge.clients - 1)
            if bridge.ws is ws:
                bridge.ws = None
                bridge.reset("klien terputus")
            log.info("Klien terputus (sisa %d)", bridge.clients)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_get("/api/status", status)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static", str(WEB_DIR), show_index=False)
    return app


class ServerThread:
    """Menjalankan siklus event asyncio di thread terpisah."""

    def __init__(self, app: web.Application, host: str, port: int, ssl_ctx: ssl.SSLContext | None) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.ssl_ctx = ssl_ctx
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = threading.Event()
        self.error: str | None = None
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="vsc-server", daemon=True)

    def start(self) -> "ServerThread":
        self._thread.start()
        return self

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as exc:
            self.error = str(exc)
            log.exception("Server gagal berjalan")
        finally:
            self.ready.set()
            try:
                self.loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port, ssl_context=self.ssl_ctx)
        await site.start()  # bila gagal (port terpakai) akan dilempar ke _run
        watchdog = asyncio.create_task(self._watchdog())
        log.info("Server siap di %s:%s (ssl=%s)", self.host, self.port, bool(self.ssl_ctx))
        try:
            while not self._stopping:
                await asyncio.sleep(0.1)
        finally:
            watchdog.cancel()
            await runner.cleanup()

    async def _watchdog(self) -> None:
        bridge: Bridge = self.app["bridge"]
        while True:
            await asyncio.sleep(0.5)
            try:
                bridge.check_timeout()
            except Exception:
                log.exception("watchdog bermasalah")

    def stop(self, timeout: float = 6.0) -> None:
        if not self._thread.is_alive():
            return
        if self.loop is not None and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(setattr, self, "_stopping", True)
            except RuntimeError:
                pass
        self._thread.join(timeout)


# ===========================================================================
# GUI Tkinter (status + URL + tombol bantuan)
# ===========================================================================

class Gui:
    def __init__(self, bridge: Bridge, urls: list[str], ssl_on: bool, actions: dict) -> None:
        self.bridge = bridge
        self.urls = urls
        self.ssl_on = ssl_on
        self.actions = actions
        self.root = tk.Tk()
        self.root.title(f"Virtual Steering Controller v{VERSION}")
        self.root.geometry("470x600")
        self.root.minsize(430, 560)
        self._build()

    def _build(self) -> None:
        root = self.root
        frm = ttk.Frame(root, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Virtual Steering Controller",
                  font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(frm, foreground="#555",
                  text="Buka alamat ini di ponsel (harus satu jaringan Wi-Fi dengan PC):"
                  ).pack(anchor="w", pady=(8, 3))

        self.url_var = tk.StringVar(value=self.urls[0] if self.urls else "")
        entry = ttk.Entry(frm, textvariable=self.url_var, font=("Consolas", 13))
        entry.pack(fill="x", ipady=6)
        entry.select_range(0, "end")

        if len(self.urls) > 1:
            ttk.Label(frm, foreground="#777", font=("Segoe UI", 8),
                      text="Alternatif: " + "   ".join(self.urls[1:])).pack(anchor="w", pady=(3, 0))

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x", pady=(12, 4))
        ttk.Button(btn_row, text="Buka di browser PC",
                   command=self.actions["open"]).pack(side="left", expand=True, fill="x")
        ttk.Button(btn_row, text="Salin URL",
                   command=self.actions["copy"]).pack(side="left", expand=True, fill="x", padx=6)
        ttk.Button(btn_row, text="Reset input",
                   command=self.actions["reset"]).pack(side="left", expand=True, fill="x")

        box = ttk.LabelFrame(frm, text="Status", padding=10)
        box.pack(fill="x", pady=(10, 8))
        self.lbl_conn = self._row(box, "Koneksi ponsel:")
        self.lbl_pad = self._row(box, "Gamepad:")
        self.lbl_mode = self._row(box, "Mode aman:")
        self.lbl_input = self._row(box, "Input terakhir:")

        self.canvas = tk.Canvas(frm, height=118, highlightthickness=0,
                                background="#10151c", borderwidth=0)
        self.canvas.pack(fill="x", pady=(2, 8))

        ttk.Button(frm, text="Petunjuk singkat",
                   command=self.actions["help"]).pack(fill="x")
        ttk.Label(frm, foreground="#888", font=("Segoe UI", 8),
                  text="Menutup jendela ini = server ikut berhenti."
                  ).pack(anchor="w", pady=(8, 0))

    @staticmethod
    def _row(parent, label: str):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=16, foreground="#555").pack(side="left")
        var = tk.StringVar(value="…")
        ttk.Label(row, textvariable=var, font=("Consolas", 10)).pack(side="left")
        return var

    def _tick(self) -> None:
        snap = self.bridge.snapshot()
        self.lbl_conn.set(
            f"TERSAMBUNG ({snap['clients']})" if snap["clients"] else "menunggu ponsel…"
        )
        pad_txt = snap["pad_label"]
        if not snap["pad_ok"]:
            pad_txt += "  [mode demo]"
        self.lbl_pad.set(pad_txt)
        self.lbl_mode.set("watchdog aktif (reset otomatis bila sinyal putus)")
        st = snap["state"]
        self.lbl_input.set(
            f"setir {st['steer']:+.2f}  gas {st['gas']:.2f}  rem {st['brake']:.2f}  "
            f"tombol 0x{st['buttons']:04X}"
        )
        self._draw_bars(st)
        self.root.after(100, self._tick)

    def _draw_bars(self, st: dict) -> None:
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 380)
        h = 118
        rows = [
            ("SETIR", st["steer"], -1.0, 1.0, "#4da3ff"),
            ("GAS", st["gas"], 0.0, 1.0, "#3ddc84"),
            ("REM", st["brake"], 0.0, 1.0, "#ff5d5d"),
        ]
        y = 8
        for name, value, lo, hi, color in rows:
            c.create_text(6, y + 12, anchor="w", text=name, fill="#8fa3b8",
                          font=("Segoe UI", 8))
            bar_x0, bar_x1 = 60, w - 10
            c.create_rectangle(bar_x0, y, bar_x1, y + 22, fill="#1b2430", outline="")
            if lo < 0:  # setir: titik nol di tengah
                mid = (bar_x0 + bar_x1) / 2
                c.create_line(mid, y, mid, y + 22, fill="#33445a")
                span = (bar_x1 - bar_x0) / 2
                x = mid + value * span
                x0, x1 = (mid, x) if x >= mid else (x, mid)
                c.create_rectangle(x0, y, x1, y + 22, fill=color, outline="")
            else:
                x = bar_x0 + value * (bar_x1 - bar_x0)
                c.create_rectangle(bar_x0, y, x, y + 22, fill=color, outline="")
            y += 36
        # indikator tombol ditekan
        pressed = [name for name, bit in gpad.BUTTONS.items() if st["buttons"] & bit]
        c.create_text(6, h - 8, anchor="sw", fill="#3ddc84", font=("Segoe UI", 8),
                      text=("tombol aktif: " + ", ".join(pressed)) if pressed else "")

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.actions["quit"])
        self.root.after(100, self._tick)
        self.root.mainloop()


# ===========================================================================
# Mode konsol (tanpa GUI)
# ===========================================================================

def print_banner(urls: list[str], pad) -> None:
    line = "=" * 62
    print(line)
    print(" Virtual Steering Controller  —  server PC")
    print("-" * 62)
    print(" Buka alamat ini di ponsel (satu Wi-Fi dengan PC):")
    for u in urls:
        print(f"   {u}")
    print("-" * 62)
    print(f" Gamepad : {pad.label}")
    if pad.name == "mock":
        print(f"           ({pad.reason})")
    print(" Catatan : browser ponsel akan menampilkan peringatan")
    print("           sertifikat (self-signed) — itu normal, pilih")
    print("           Advanced → Proceed (Android) atau")
    print("           Show Details → visit this website (iPhone).")
    print(" Tekan Ctrl+C untuk berhenti.")
    print(line, flush=True)


def run_console(bridge: Bridge, pad, urls: list[str]) -> None:
    last = ""
    try:
        while True:
            time.sleep(2)
            snap = bridge.snapshot()
            st = snap["state"]
            line = (
                f"[HP {'TERHUBUNG' if snap['clients'] else 'menunggu'}] "
                f"setir {st['steer']:+.2f} gas {st['gas']:.2f} rem {st['brake']:.2f} "
                f"tombol 0x{st['buttons']:04X}  |  {urls[0]}"
            )
            if line != last:
                print(line, flush=True)
                last = line
    except KeyboardInterrupt:
        print("\nBerhenti.", flush=True)


# ===========================================================================
# Main
# ===========================================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Virtual Steering Controller — server PC (ponsel sebagai setir).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--config", default=str(BASE_DIR / "config.json"), help="file konfigurasi JSON")
    p.add_argument("--host", default=None, help="alamat bind (default dari config)")
    p.add_argument("--port", type=int, default=None, help="port (default dari config)")
    p.add_argument("--no-ssl", action="store_true", help="HTTP biasa tanpa HTTPS (sensor ponsel mungkin diblokir browser)")
    p.add_argument("--no-gui", action="store_true", help="tanpa jendela GUI (mode konsol)")
    p.add_argument("--no-gamepad", action="store_true", help="mode demo tanpa gamepad virtual")
    p.add_argument("--open", action="store_true", help="buka browser otomatis saat mulai")
    p.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(Path(args.config))

    host = args.host or str(cfg["host"])
    port = int(args.port or cfg["port"])
    use_ssl = bool(cfg["ssl"]) and not args.no_ssl
    gui_on = bool(cfg["gui"]) and not args.no_gui
    log_level = (args.log_level or str(cfg["log_level"])).upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    pad = gpad.create_pad(force_mock=args.no_gamepad)
    if pad.name == "mock":
        log.warning("Gamepad virtual tidak aktif: %s", pad.reason)
    bridge = Bridge(pad)

    lan = lan_ipv4s()
    ssl_ctx: ssl.SSLContext | None = None
    if use_ssl:
        pair = ensure_certificate(lan)
        if pair is not None:
            ssl_ctx = make_ssl_context(*pair)
        else:
            log.error(
                "Sertifikat HTTPS gagal dibuat (butuh pustaka 'cryptography' atau openssl). "
                "Menjalankan HTTP biasa — sensor ponsel kemungkinan besar DIBLOKIR browser."
            )
            use_ssl = False
    urls = build_urls(port, use_ssl, lan)

    app = make_app(bridge)
    app["urls"] = urls
    app["ssl_on"] = use_ssl

    server = ServerThread(app, host, port, ssl_ctx).start()
    server.ready.wait(15)
    if server.error:
        print(f"ERROR: server gagal berjalan: {server.error}", file=sys.stderr)
        pad.close()
        return 1

    print_banner(urls, pad)

    if args.open or bool(cfg["open_browser"]):
        try:
            webbrowser.open(urls[0])
        except Exception:
            pass

    quit_evt = threading.Event()
    actions = {
        "open": lambda: _try(webbrowser.open, urls[0]),
        "copy": lambda: _copy_to_clipboard(urls[0]),
        "reset": lambda: bridge.reset("tombol reset GUI"),
        "help": _show_help,
        "quit": quit_evt.set,
    }

    try:
        if gui_on and TK_OK:
            try:
                gui = Gui(bridge, urls, use_ssl, actions)
                gui.root.after(200, _watch_quit, quit_evt, gui)
                gui.run()
            except Exception as exc:  # mis. tidak ada display (TclError)
                log.warning("GUI tidak dapat dibuka (%s) — memakai mode konsol.", exc)
                run_console(bridge, pad, urls)
        else:
            run_console(bridge, pad, urls)
    except KeyboardInterrupt:
        pass
    finally:
        quit_evt.set()
        server.stop()
        bridge.reset("server berhenti")
        pad.close()
        log.info("Server berhenti.")
    return 0


def _watch_quit(quit_evt: threading.Event, gui: "Gui") -> None:
    if quit_evt.is_set():
        gui.root.destroy()
        return
    gui.root.after(200, _watch_quit, quit_evt, gui)


def _try(fn, *a) -> None:
    try:
        fn(*a)
    except Exception:
        pass


def _copy_to_clipboard(text: str) -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
    except Exception:
        pass


def _show_help() -> None:
    if not TK_OK:
        return
    messagebox.showinfo(
        "Petunjuk singkat",
        "1. Pastikan ponsel & PC satu jaringan Wi-Fi.\n"
        "2. Buka alamat di atas dari browser ponsel.\n"
        "3. Jika muncul peringatan sertifikat, pilih\n"
        "   Advanced → Proceed (Android) atau\n"
        "   Show Details → visit this website (iPhone).\n"
        "4. Tekan ▶ Mulai, izinkan akses sensor gerak.\n"
        "5. Pegang ponsel seperti setir, tekan Kalibrasi.\n"
        "6. Di game: pilih controller Xbox 360, lalu\n"
        "   pasang sumbu setir = stik kiri, gas = RT,\n"
        "   rem = LT (lihat README untuk tabel lengkap).",
    )


if __name__ == "__main__":
    sys.exit(main())
