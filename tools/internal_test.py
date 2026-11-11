#!/usr/bin/env python3
"""Uji internal: mapping gamepad XInput (dengan modul vgamepad palsu),
pengambilalihan sesi WebSocket, dan konsistensi ID elemen HTML <-> app.js.

Membutuhkan server yang sedang berjalan, mis.:
    python server.py --no-gui --port 8000 &
    PORT=8000 python tools/internal_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'GAGAL'}] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. XInputPad dengan vgamepad tiruan
# ---------------------------------------------------------------------------
print("1. XInputPad (modul vgamepad tiruan)")

calls: list[tuple] = []


class FakeXUSB:
    XUSB_GAMEPAD_DPAD_UP = 1
    XUSB_GAMEPAD_DPAD_DOWN = 2
    XUSB_GAMEPAD_DPAD_LEFT = 3
    XUSB_GAMEPAD_DPAD_RIGHT = 4
    XUSB_GAMEPAD_START = 5
    XUSB_GAMEPAD_BACK = 6
    XUSB_GAMEPAD_LEFT_SHOULDER = 7
    XUSB_GAMEPAD_RIGHT_SHOULDER = 8
    XUSB_GAMEPAD_A = 9
    XUSB_GAMEPAD_B = 10
    XUSB_GAMEPAD_X = 11
    XUSB_GAMEPAD_Y = 12


class FakePad:
    # Signature disamakan persis dengan vgamepad asli agar pemanggilan
    # dengan nama argumen yang salah ikut gagal diuji.
    def left_joystick(self, x_value, y_value):
        calls.append(("stick", x_value, y_value))

    def right_trigger(self, value):
        calls.append(("rt", value))

    def left_trigger(self, value):
        calls.append(("lt", value))

    def press_button(self, button=0):
        calls.append(("press", button))

    def release_button(self, button=0):
        calls.append(("release", button))

    def update(self):
        calls.append(("update",))

    def reset(self):
        calls.append(("reset",))

    def unplug(self):
        calls.append(("unplug",))


fake_vg = types.ModuleType("vgamepad")
fake_vg.VX360Gamepad = FakePad
fake_vg.XUSB_BUTTON = FakeXUSB
sys.modules["vgamepad"] = fake_vg

import gamepad as gpad  # noqa: E402

pad = gpad.XInputPad()
check("nama pad = xinput", pad.name == "xinput")

calls.clear()
pad.apply(gpad.InputState(steer=0.5, gas=1.0, brake=0.5, buttons=0x1001))
check("stik kiri x = int(0.5*32767)", ("stick", int(0.5 * 32767), 0) in calls, str(calls[:3]))
check("RT = 255", ("rt", 255) in calls)
check("LT = 127", ("lt", 127) in calls)
check("tekan A (9)", ("press", 9) in calls)
check("tekan DPAD_UP (1)", ("press", 1) in calls)
check("update() dipanggil", ("update",) in calls)

calls.clear()
pad.apply(gpad.InputState())  # semua dilepas
check("lepas A", ("release", 9) in calls)
check("lepas DPAD_UP", ("release", 1) in calls)
check("stik kembali ke 0", ("stick", 0, 0) in calls)
check("trigger kembali ke 0", ("rt", 0) in calls and ("lt", 0) in calls)

# clamping ekstrem
calls.clear()
pad.apply(gpad.InputState(steer=5.0, gas=-3.0, brake=9.0, buttons=0))
check("steer di-clamp ke 32767", ("stick", 32767, 0) in calls)
check("gas di-clamp ke 0", ("rt", 0) in calls)
check("rem di-clamp ke 255", ("lt", 255) in calls)

# reset() memakai reset() vgamepad, bukan apply()
calls.clear()
pad.reset()
check("reset() memakai vgamepad.reset()+update()",
      ("reset",) in calls and ("update",) in calls and ("stick", 0, 0) not in calls,
      str(calls))

calls.clear()
pad.close()
check("close() me-reset lalu melepas pad", ("reset",) in calls and pad._pad is None,
      str(calls))
pad.apply(gpad.InputState(steer=1.0))
check("apply() setelah close() aman (tanpa error)", pad.updates >= 1)

# ---------------------------------------------------------------------------
# 2. Konsistensi ID elemen HTML <-> app.js
# ---------------------------------------------------------------------------
print("2. Konsistensi elemen HTML <-> app.js")
html = (BASE / "web" / "index.html").read_text(encoding="utf-8")
js = (BASE / "web" / "app.js").read_text(encoding="utf-8")

html_ids = set(re.findall(r'id="([^"]+)"', html))
js_ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
missing = sorted(js_ids - html_ids)
check("semua #id di app.js ada di index.html", not missing, f"hilang: {missing}")

js_querysel = set(re.findall(r'querySelector\(.#([A-Za-z0-9_-]+).\)', js))
missing2 = sorted(js_querysel - html_ids)
check("querySelector(#id) terpenuhi", not missing2, f"hilang: {missing2}")

# tombol data-btn di HTML harus berupa bilangan yang sama dengan yang dipakai gamepad.py
html_bits = sorted({int(x) for x in re.findall(r'data-btn="(\d+)"', html)})
py_bits = sorted(gpad.BUTTONS.values())
check("bit data-btn HTML == BUTTONS gamepad.py", html_bits == py_bits,
      f"html={html_bits} py={py_bits}")

# nilai bit di KEY_MASK app.js juga harus cocok
js_mask_pairs = dict(re.findall(r"(\w+):\s*(\d+),", js.split("const KEY_MASK")[1].split("};")[0]))
js_bits = sorted({int(v) for v in js_mask_pairs.values()})
check("bit KEY_MASK app.js ⊆ BUTTONS gamepad.py", set(js_bits) <= set(py_bits),
      f"js={js_bits}")

# ---------------------------------------------------------------------------
# 3. Pengambilalihan sesi: klien baru menutup klien lama
# ---------------------------------------------------------------------------
print("3. Pengambilalihan sesi WebSocket")


async def takeover_test() -> None:
    import aiohttp

    port = int(os.environ.get("PORT", "8000"))
    url = f"wss://127.0.0.1:{port}/ws"
    conn = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=conn) as s:
        ws1 = await s.ws_connect(url)
        h1 = await ws1.receive_json()
        check("klien 1 hello", h1.get("t") == "hello")

        ws2 = await s.ws_connect(url)
        h2 = await ws2.receive_json()
        check("klien 2 hello", h2.get("t") == "hello")

        # klien 1 harus ditutup server
        try:
            msg = await asyncio.wait_for(ws1.receive(), timeout=5)
            closed_by_server = msg.type.name in ("CLOSE", "CLOSED", "ERROR")
        except asyncio.TimeoutError:
            closed_by_server = False
        check("klien 1 ditutup server (sesi diambil alih)", closed_by_server)

        # klien 2 tetap hidup & bisa mengirim
        await ws2.send_json({"t": "i", "s": 0.25, "g": 0, "b": 0, "p": 0})
        await asyncio.sleep(0.3)
        async with s.get(f"https://127.0.0.1:{port}/api/status") as r:
            st = await r.json()
        check("klien aktif = 1", st["clients"] == 1, str(st["clients"]))
        check("input klien 2 diterima", abs(st["state"]["steer"] - 0.25) < 0.01,
              str(st["state"]["steer"]))
        await ws2.close()


asyncio.run(takeover_test())

print()
if failures:
    print(f"HASIL: {failures} pemeriksaan GAGAL")
    sys.exit(1)
print("HASIL: semua pemeriksaan LULUS ✔")
