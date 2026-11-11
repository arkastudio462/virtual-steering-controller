#!/usr/bin/env python3
"""Simulasi klien ponsel untuk menguji server tanpa ponsel sungguhan.

Menguji: sambungan WebSocket, pesan input, ping/pong, reset saat putus,
dan watchdog (reset otomatis bila ponsel berhenti mengirim).

Contoh:
    python tools/test_client.py --host 127.0.0.1 --port 8000 --ssl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import aiohttp

GAS_BIT = 0x1000 | 0x0001  # A (klakson) + D-pad atas (lampu)


async def get_status(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url) as resp:
        return await resp.json()


async def run(args: argparse.Namespace) -> int:
    scheme_ws = "wss" if args.ssl else "ws"
    scheme_http = "https" if args.ssl else "http"
    ws_url = f"{scheme_ws}://{args.host}:{args.port}/ws"
    status_url = f"{scheme_http}://{args.host}:{args.port}/api/status"
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        mark = "OK  " if ok else "GAGAL"
        if not ok:
            failures += 1
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))

    connector = aiohttp.TCPConnector(ssl=False)  # sertifikat self-signed
    async with aiohttp.ClientSession(connector=connector) as session:
        print(f"1. Menghubungkan {ws_url}")
        async with session.ws_connect(ws_url, heartbeat=20) as ws:
            hello = await ws.receive_json()
            check("menerima hello", hello.get("t") == "hello", json.dumps(hello))

            print("2. Mengirim rangkaian input (setir menyapu + gas + tombol)")
            buttons = GAS_BIT
            for i in range(50):  # ~1 detik @ 50 Hz
                steer = round(-1 + 2 * (i / 49), 3)
                await ws.send_json({"t": "i", "s": steer, "g": 1.0, "b": 0.25, "p": buttons})
                await asyncio.sleep(0.02)

            print("3. Cek status server (harus mencerminkan input terakhir)")
            st = await get_status(session, status_url)
            state = st.get("state", {})
            check("steer terkirim", abs(state.get("steer", 0) - 1.0) < 0.01, str(state.get("steer")))
            check("gas = 1", abs(state.get("gas", 0) - 1.0) < 0.01, str(state.get("gas")))
            check("rem = 0.25", abs(state.get("brake", 0) - 0.25) < 0.01, str(state.get("brake")))
            check("tombol = 0x%04X" % buttons, state.get("buttons") == buttons,
                  hex(state.get("buttons", -1)))
            check("klien terhitung", st.get("clients") == 1, str(st.get("clients")))

            print("4. Ping/pong (ukur latensi)")
            t0 = time.time()
            await ws.send_json({"t": "ping", "ts": int(t0 * 1000)})
            pong = await ws.receive_json()
            rtt = int(time.time() * 1000) - pong.get("ts", int(t0 * 1000))
            check("menerima pong", pong.get("t") == "pong", f"RTT ~{max(rtt, 0)} ms")

            print("5. Watchdog: berhenti kirim selama ~2 detik (gas harus dinetralkan)")
            await ws.send_json({"t": "i", "s": 0, "g": 1, "b": 0, "p": 0})
            await asyncio.sleep(0.4)
            st = await get_status(session, status_url)
            check("gas masih 1 sebelum timeout", abs(st["state"]["gas"] - 1.0) < 0.01)
            await asyncio.sleep(1.6)
            st = await get_status(session, status_url)
            check("watchdog menetralkan input", st["state"]["gas"] == 0 and st["state"]["steer"] == 0,
                  json.dumps(st["state"]))

        print("6. Memutus sambungan (state harus netral & klien 0)")
        await asyncio.sleep(0.6)
        st = await get_status(session, status_url)
        check("klien = 0", st.get("clients") == 0, str(st.get("clients")))
        check("state netral", st["state"]["buttons"] == 0 and st["state"]["gas"] == 0,
              json.dumps(st["state"]))

    print()
    if failures:
        print(f"HASIL: {failures} pemeriksaan GAGAL")
        return 1
    print("HASIL: semua pemeriksaan LULUS ✔")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Simulasi klien ponsel untuk pengujian server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--ssl", action="store_true", default=True, help="pakai wss:// (default)")
    p.add_argument("--no-ssl", dest="ssl", action="store_false", help="pakai ws://")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_args())))
