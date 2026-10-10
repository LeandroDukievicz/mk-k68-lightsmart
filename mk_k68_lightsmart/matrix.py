"""The 65x5 dot-matrix strip above the function row.

The strip is 325 LEDs addressed row by row, which is how the vendor's own
SQLite animation store numbers them, and how the upload wants them.

Frame upload, as the Windows driver does it::

    04 18                                     begin
    04 31|33 .. .. .. .. .. .. <lo> <hi>      announce <blocks> 64-byte blocks
    <header><frames...>AA 55                  the bulk payload
    04 02                                     apply
    04 F0                                     end

``0x31`` is the boot animation, capped at 134 frames and carrying a
four-byte header; ``0x33`` is an ordinary animation, up to 941 frames,
with a two-byte header. Pixels are three bytes each in **B, G, R** order,
pre-scaled by the layer's brightness.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

COLS = 65
ROWS = 5
LEDS = COLS * ROWS            #: 325
FRAME_BYTES = LEDS * 3        #: 975

MAX_FRAMES_BOOT = 134
MAX_FRAMES_ANIMATION = 941

#: Byte 1 of the announce command.
CMD_BOOT_ANIMATION = 0x31
CMD_ANIMATION = 0x33

#: What the driver sends when a layer carries no explicit speed.
DEFAULT_SPEED = 0x50
#: A layer's 1..100 speed setting is sent as this minus the setting.
SPEED_BASE = 0x66

BLOCK = 64


def led_index(col: int, row: int) -> int:
    """Address of the LED at ``(col, row)``, counting rows first."""
    if not 0 <= col < COLS or not 0 <= row < ROWS:
        raise ValueError(f"({col},{row}) is outside the {COLS}x{ROWS} strip")
    return row * COLS + col


def led_position(index: int) -> tuple[int, int]:
    """The ``(col, row)`` an LED address refers to."""
    if not 0 <= index < LEDS:
        raise ValueError(f"LED index {index} is outside 0..{LEDS - 1}")
    return index % COLS, index // COLS


@dataclass
class Frame:
    """One still image on the strip, as 0x00RRGGBB values per LED."""

    pixels: list[int] = field(default_factory=lambda: [0] * LEDS)

    def __post_init__(self) -> None:
        if len(self.pixels) != LEDS:
            raise ValueError(f"a frame holds {LEDS} pixels, got {len(self.pixels)}")

    def __setitem__(self, pos: tuple[int, int], rgb: int) -> None:
        self.pixels[led_index(*pos)] = rgb

    def __getitem__(self, pos: tuple[int, int]) -> int:
        return self.pixels[led_index(*pos)]

    @classmethod
    def from_rows(cls, rows: list[list[int]]) -> "Frame":
        if len(rows) != ROWS or any(len(r) != COLS for r in rows):
            raise ValueError(f"expected {ROWS} rows of {COLS}")
        return cls([rgb for row in rows for rgb in row])

    def encode(self, brightness: int = 255) -> bytes:
        """Pack to the wire form: three bytes per LED, B then G then R.

        ``brightness`` is the 0-255 scale the driver derives from the
        layer's 0-100 setting; every channel is multiplied by it.
        """
        if not 0 <= brightness <= 255:
            raise ValueError("brightness is 0..255")
        out = bytearray(FRAME_BYTES)
        for i, rgb in enumerate(self.pixels):
            r = ((rgb >> 16) & 0xFF) * brightness // 255
            g = ((rgb >> 8) & 0xFF) * brightness // 255
            b = (rgb & 0xFF) * brightness // 255
            out[i * 3 : i * 3 + 3] = bytes((b, g, r))
        return bytes(out)


def brightness_to_byte(percent: int) -> int:
    """Convert the UI's 0-100 brightness to the 0-255 the encoder wants."""
    return max(0, min(100, percent)) * 255 // 100


def speed_to_byte(setting: int | None) -> int:
    """Convert a 1-100 speed setting to the wire value."""
    if setting is None:
        return DEFAULT_SPEED
    return SPEED_BASE - max(0, min(100, setting))


@dataclass
class Animation:
    """A sequence of frames destined for the strip."""

    frames: list[Frame] = field(default_factory=list)
    speed: int | None = None
    brightness: int = 100
    boot: bool = False

    @property
    def max_frames(self) -> int:
        return MAX_FRAMES_BOOT if self.boot else MAX_FRAMES_ANIMATION

    def payload(self) -> bytes:
        """Build the bulk payload: header, frames, then the AA 55 magic.

        The result is padded out to whole 64-byte blocks, which is what
        the announce command counts.
        """
        frames = self.frames[: self.max_frames]
        if not frames:
            raise ValueError("an animation needs at least one frame")

        count = len(frames)
        if self.boot:
            header = struct.pack("<HH", count, speed_to_byte(self.speed))
        else:
            # The driver reserves two bytes here and fills in the frame
            # count; the speed travels with the layer's own settings.
            header = struct.pack("<H", count)

        bright = brightness_to_byte(self.brightness)
        body = b"".join(f.encode(bright) for f in frames)
        data = header + body

        blocks = (len(data) >> 6) + 1
        buf = bytearray(blocks * BLOCK)
        buf[: len(data)] = data
        buf[len(data) : len(data) + 2] = b"\xaa\x55"
        return bytes(buf)

    def block_count(self) -> int:
        return len(self.payload()) // BLOCK

    @property
    def command(self) -> int:
        return CMD_BOOT_ANIMATION if self.boot else CMD_ANIMATION


def load_vendor_animations(db_path: str) -> dict[int, tuple[str, Animation]]:
    """Read the stock animations out of the vendor's SQLite store.

    Only useful if you still have `ledlayer_v1.data` from the Windows
    installer; it is not shipped with this project.
    """
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out: dict[int, tuple[str, Animation]] = {}
    layers = con.execute(
        "SELECT led_id, name, speed, brightness FROM t_ledlayer_data"
    ).fetchall()
    for led_id, name, speed, brightness in layers:
        for table in ("t_default_led_frames", "t_led_frames"):
            rows = con.execute(
                f"SELECT frameIndex, led_index, rgb_value FROM {table} "
                "WHERE layer_id=? ORDER BY frameIndex, led_index",
                (led_id,),
            ).fetchall()
            if not rows:
                continue
            by_frame: dict[int, Frame] = {}
            for frame_index, index, rgb in rows:
                by_frame.setdefault(frame_index, Frame())
                by_frame[frame_index].pixels[index] = rgb
            anim = Animation(
                frames=[by_frame[k] for k in sorted(by_frame)],
                speed=speed,
                brightness=brightness,
                boot=(led_id == 1),
            )
            out[led_id] = (name, anim)
            break
    con.close()
    return out
