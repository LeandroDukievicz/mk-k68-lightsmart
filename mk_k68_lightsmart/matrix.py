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
import sys
from array import array
from collections.abc import Sequence
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


_LITTLE_ENDIAN = sys.byteorder == "little"

#: Brightness lookup tables, built once per level and shared. A full
#: animation is 918 KB of pixels, so scaling a byte at a time in Python
#: is the difference between a snappy upload and a visible stall.
_LUT_CACHE: dict[int, bytes] = {}


def _brightness_lut(brightness: int) -> bytes:
    lut = _LUT_CACHE.get(brightness)
    if lut is None:
        lut = bytes(i * brightness // 255 for i in range(256))
        _LUT_CACHE[brightness] = lut
    return lut


class Frame:
    """One still image on the strip, as 0x00RRGGBB values per LED.

    Pixels live in an ``array("I")`` rather than a list: 1.3 KB a frame
    instead of roughly 12 KB, which matters at 941 frames.
    """

    __slots__ = ("pixels",)

    def __init__(self, pixels: Sequence[int] | None = None):
        if pixels is None:
            self.pixels = array("I", bytes(LEDS * 4))
        else:
            if len(pixels) != LEDS:
                raise ValueError(f"a frame holds {LEDS} pixels, got {len(pixels)}")
            self.pixels = array("I", pixels)

    def __setitem__(self, pos: tuple[int, int], rgb: int) -> None:
        self.pixels[led_index(*pos)] = rgb & 0xFFFFFF

    def __getitem__(self, pos: tuple[int, int]) -> int:
        return self.pixels[led_index(*pos)]

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[int]]) -> "Frame":
        if len(rows) != ROWS or any(len(r) != COLS for r in rows):
            raise ValueError(f"expected {ROWS} rows of {COLS}")
        return cls([rgb for row in rows for rgb in row])

    def encode(self, brightness: int = 255) -> bytes:
        """Pack to the wire form: three bytes per LED, B then G then R.

        On a little-endian host an ``array("I")`` of 0x00RRGGBB already
        serialises as B, G, R, 0 -- so dropping every fourth byte gives
        the wire order outright, and the brightness scale is one
        ``translate`` over the whole frame. Both run in C.
        """
        if not 0 <= brightness <= 255:
            raise ValueError("brightness is 0..255")

        if _LITTLE_ENDIAN:
            raw = bytearray(self.pixels.tobytes())
            del raw[3::4]
        else:
            raw = bytearray(FRAME_BYTES)
            for i, rgb in enumerate(self.pixels):
                raw[i * 3] = rgb & 0xFF
                raw[i * 3 + 1] = (rgb >> 8) & 0xFF
                raw[i * 3 + 2] = (rgb >> 16) & 0xFF

        if brightness == 255:
            return bytes(raw)
        return bytes(raw.translate(_brightness_lut(brightness)))


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
    _payload: bytes | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def max_frames(self) -> int:
        return MAX_FRAMES_BOOT if self.boot else MAX_FRAMES_ANIMATION

    def invalidate(self) -> None:
        """Drop the cached payload after editing frames or settings."""
        self._payload = None

    def payload(self) -> bytes:
        """Build the bulk payload: header, frames, then the AA 55 magic.

        The result is padded out to whole 64-byte blocks, which is what
        the announce command counts.
        """
        if self._payload is not None:
            return self._payload

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
        end = len(data)

        blocks = self._blocks_for(end)
        buf = bytearray(blocks * BLOCK)
        buf[:end] = data
        # The driver leaves one byte after the data and puts the magic at
        # end+1 (0x429EF5: `mov word [ebx+edx+1], 0x55AA`). Indexed writes,
        # not a slice assignment -- `buf[a:a+2] = b"\xaa\x55"` would *grow*
        # a bytearray whenever only one byte is left, quietly making the
        # payload a non-multiple of 64 and truncating the terminator.
        buf[end + 1] = 0xAA
        buf[end + 2] = 0x55
        self._payload = bytes(buf)
        return self._payload

    @staticmethod
    def _blocks_for(data_len: int) -> int:
        """Blocks to announce and send for ``data_len`` bytes of data.

        The driver's own count is ``(len >> 6) + 1`` (0x429CF3). It writes
        the terminator into a buffer far larger than it sends, so for the
        few lengths where ``len % 64 >= 62`` its magic falls outside the
        transmitted range. We send one more block in that case, which keeps
        the announced and streamed counts equal and the terminator intact.
        """
        blocks = (data_len >> 6) + 1
        if data_len + 3 > blocks * BLOCK:
            blocks += 1
        return blocks

    def block_count(self) -> int:
        """How many 64-byte blocks the upload will announce and send.

        Derived arithmetically so it never re-encodes the frames.
        """
        n = min(len(self.frames), self.max_frames)
        if n == 0:
            raise ValueError("an animation needs at least one frame")
        header = 4 if self.boot else 2
        return self._blocks_for(header + n * FRAME_BYTES)

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
