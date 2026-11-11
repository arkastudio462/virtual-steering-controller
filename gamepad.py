"""
Gamepad virtual untuk PC.

Dua implementasi dengan antarmuka yang sama:

- ``XInputPad`` : gamepad Xbox 360 virtual lewat driver ViGEmBus
                  (khusus Windows, membutuhkan ``pip install vgamepad``).
                  Game seperti ETS2 & MudRunner melihatnya sebagai stik Xbox asli.
- ``MockPad``   : simulasi tanpa driver — dipakai untuk pengujian di OS lain
                  dan untuk "mode demo" bila driver belum terpasang.

Semua pemanggilan dari luar dilindungi satu lock di ``server.Bridge`` karena
objek vgamepad tidak aman dipanggil dari banyak thread.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Peta tombol. Nilainya adalah bit kode tombol XInput (XUSB) sehingga bit yang
# sama dipakai bersama oleh: halaman HP (bitmask "p"), server, dan gamepad.
# ---------------------------------------------------------------------------
BUTTONS: dict[str, int] = {
    "up": 0x0001,     # D-pad atas    -> Lampu
    "down": 0x0002,   # D-pad bawah   -> Mode lampu
    "left": 0x0004,   # D-pad kiri    -> Lihat kiri
    "right": 0x0008,  # D-pad kanan   -> Lihat kanan
    "start": 0x0010,  # START         -> Jeda / menu
    "back": 0x0020,   # BACK          -> Ganti kamera
    "lb": 0x0100,     # LB            -> Mundur
    "rb": 0x0200,     # RB            -> Lampu jauh
    "a": 0x1000,      # A             -> Klakson
    "b": 0x2000,      # B             -> Rem tangan
    "x": 0x4000,      # X             -> Gigi naik
    "y": 0x8000,      # Y             -> Gigi turun
}

# Nama anggota enum XUSB_BUTTON di pustaka vgamepad, sesuai kunci BUTTONS.
XUSB_NAMES: dict[str, str] = {
    "up": "DPAD_UP",
    "down": "DPAD_DOWN",
    "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT",
    "start": "START",
    "back": "BACK",
    "lb": "LEFT_SHOULDER",
    "rb": "RIGHT_SHOULDER",
    "a": "A",
    "b": "B",
    "x": "X",
    "y": "Y",
}

# Label Indonesia untuk tombol (dipakai jendela GUI & README).
BUTTON_LABELS: list[tuple[str, str]] = [
    ("a", "Klakson"),
    ("b", "Rem tangan"),
    ("x", "Gigi naik"),
    ("y", "Gigi turun"),
    ("lb", "Mundur"),
    ("rb", "Lampu jauh"),
    ("start", "Jeda / menu"),
    ("back", "Ganti kamera"),
    ("up", "Lampu"),
    ("down", "Mode lampu"),
    ("left", "Lihat kiri"),
    ("right", "Lihat kanan"),
]


@dataclass
class InputState:
    """Snapshot input dari HP."""

    steer: float = 0.0                  # -1..1  (sumbu X stik kiri)
    gas: float = 0.0                    # 0..1   (trigger kanan)
    brake: float = 0.0                  # 0..1   (trigger kiri)
    buttons: int = 0                    # bitmask XUSB

    @property
    def neutral(self) -> bool:
        return self.steer == 0.0 and self.gas == 0.0 and self.brake == 0.0 and self.buttons == 0

    def as_dict(self) -> dict:
        return {
            "steer": round(self.steer, 4),
            "gas": round(self.gas, 4),
            "brake": round(self.brake, 4),
            "buttons": self.buttons,
        }


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


class MockPad:
    """Gamepad simulasi: hanya menyimpan state terakhir (untuk uji & demo)."""

    name = "mock"
    label = "Mode demo (tanpa gamepad virtual)"

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        self.state = InputState()
        self.updates = 0

    def apply(self, state: InputState) -> None:
        self.state = state
        self.updates += 1

    def reset(self) -> None:
        self.apply(InputState())

    def describe(self) -> str:
        s = self.state
        return (
            f"setir={s.steer:+.2f} gas={s.gas:.2f} rem={s.brake:.2f} "
            f"tombol=0x{s.buttons:04X} (update #{self.updates})"
        )

    def close(self) -> None:  # pragma: no cover - tidak ada sumber daya
        pass


class XInputPad:
    """Gamepad Xbox 360 virtual nyata lewat ViGEmBus (Windows)."""

    name = "xinput"
    label = "Gamepad Xbox virtual (ViGEmBus)"

    def __init__(self) -> None:
        import vgamepad as vg  # impor di sini supaya OS non-Windows tetap jalan

        self._vg = vg
        self._pad = vg.VX360Gamepad()
        self._prev_buttons = 0
        self.reason = ""
        self.state = InputState()
        self.updates = 0

    def apply(self, state: InputState) -> None:
        if self._pad is None:
            return
        self.state = state
        self.updates += 1
        p = self._pad
        x = int(_clamp(state.steer, -1.0, 1.0) * 32767)
        p.left_joystick(x, 0)                       # (x_value, y_value)
        p.right_trigger(int(_clamp(state.gas, 0.0, 1.0) * 255))
        p.left_trigger(int(_clamp(state.brake, 0.0, 1.0) * 255))

        changed = state.buttons ^ self._prev_buttons
        if changed:
            enum = self._vg.XUSB_BUTTON
            for key, bit in BUTTONS.items():
                if not (changed & bit):
                    continue
                code = getattr(enum, f"XUSB_GAMEPAD_{XUSB_NAMES[key]}")
                if state.buttons & bit:
                    p.press_button(button=code)
                else:
                    p.release_button(button=code)
        self._prev_buttons = state.buttons
        p.update()

    def reset(self) -> None:
        if self._pad is None:
            return
        self.state = InputState()
        self.updates += 1
        self._prev_buttons = 0
        self._pad.reset()
        self._pad.update()

    def describe(self) -> str:
        s = self.state
        return (
            f"setir={s.steer:+.2f} gas={s.gas:.2f} rem={s.brake:.2f} "
            f"tombol=0x{s.buttons:04X} (update #{self.updates})"
        )

    def close(self) -> None:
        if getattr(self, "_pad", None) is None:
            return
        try:
            self.reset()
        except Exception:
            pass
        # Melepas referensi: vgamepad membersihkan target ViGEm di __del__.
        self._pad = None


def create_pad(force_mock: bool = False):
    """Buat gamepad terbaik yang tersedia di mesin ini."""
    if force_mock:
        return MockPad("dimatikan lewat --no-gamepad")
    if sys.platform != "win32":
        return MockPad("ViGEmBus/vgamepad hanya tersedia di Windows")
    try:
        return XInputPad()
    except Exception as exc:  # driver belum terpasang / vgamepad belum diinstal
        return MockPad(f"gagal memuat vgamepad/ViGEmBus: {exc}")
