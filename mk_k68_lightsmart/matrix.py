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

import contextlib
import os
import struct
import urllib.parse
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

# The fast encode path reads an array("I") as four bytes per pixel.
assert array("I").itemsize == 4, "this build's array('I') is not 32-bit"



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
            self.pixels = array("I", [p & 0xFFFFFF for p in pixels])

    def __setitem__(self, pos: tuple[int, int], rgb: int) -> None:
        self.pixels[led_index(*pos)] = rgb & 0xFFFFFF

    def __getitem__(self, pos: tuple[int, int]) -> int:
        return self.pixels[led_index(*pos)]

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[int]]) -> "Frame":
        if len(rows) != ROWS or any(len(r) != COLS for r in rows):
            raise ValueError(f"expected {ROWS} rows of {COLS}")
        return cls([rgb for row in rows for rgb in row])

    @classmethod
    def decode(cls, data: bytes) -> "Frame":
        """Rebuild a frame from :meth:`encode`'s wire bytes.

        What the keyboard is sent is also how the bundled effects are
        stored, so this is the only reader they need.
        """
        if len(data) != FRAME_BYTES:
            raise ValueError(
                f"a frame is {FRAME_BYTES} bytes on the wire, got {len(data)}"
            )
        frame = cls()
        pixels = frame.pixels
        for i in range(LEDS):
            r, g, b = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
            pixels[i] = (r << 16) | (g << 8) | b
        return frame

    def encode(self, brightness: int = 255) -> bytes:
        """Pack to the wire form: three bytes per LED, R then G then B.

        The driver writes its pixel out low byte first (0x42A27C,
        0x42A284, 0x42A290), and the pixel it holds is a Win32
        ``COLORREF`` -- 0x00BBGGRR -- so what lands on the wire is R, G,
        B. Confirmed on hardware: sending B, G, R put every green pixel
        on the blue channel.

        Byte-swapping an ``array("I")`` of 0x00RRGGBB makes it serialise
        as 00, R, G, B, so dropping every fourth byte leaves the wire
        order outright. That and the brightness ``translate`` both run
        in C, which is what keeps a 941-frame encode off the stopwatch.
        """
        if not 0 <= brightness <= 255:
            raise ValueError("brightness is 0..255")

        values = self.pixels
        if _LITTLE_ENDIAN:
            values = array("I", values)   # byteswap is in place; copy first
            values.byteswap()
        raw = bytearray(values.tobytes())
        del raw[0::4]

        if brightness == 255:
            return bytes(raw)
        # bytes().translate returns bytes directly; bytearray().translate
        # would hand back a bytearray we would then have to copy again.
        return bytes(raw).translate(_brightness_lut(brightness))


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
        # Both commands take the same 4-byte header -- frame count then
        # speed, each little-endian 16-bit. The animation path sets its
        # first pixel offset to 4 (0x42A190) exactly as the boot path
        # does, after writing the count at 0x42A11C and the speed at
        # 0x42A187. Reading this as a 2-byte header shifted every pixel
        # two bytes early, which the strip showed as a green/blue swap:
        # the firmware had taken the first pixel's B and G for the speed.
        header = struct.pack("<HH", count, speed_to_byte(self.speed))

        bright = brightness_to_byte(self.brightness)
        body = b"".join(f.encode(bright) for f in frames)
        if len(body) != count * FRAME_BYTES:
            raise ValueError(
                f"frames encoded to {len(body)} bytes, expected "
                f"{count * FRAME_BYTES} -- a Frame's pixel array is the "
                "wrong length"
            )
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
        return bytes(buf)

    @staticmethod
    def _blocks_for(data_len: int) -> int:
        """Blocks to announce and send for ``data_len`` bytes of data.

        The driver counts twice and can disagree with itself. It
        announces ``((frames * 975 + 2) >> 6) + 1`` (0x42A0A0-0x42A0B1)
        but hands the sender ``frames * 975 + 4`` (0x42A2B5), whose own
        count is ``(len >> 6) + 1`` -- so at 4 frames, and every 64
        frames after, it announces one block fewer than it streams. We
        use the transmitted length for both, so the keyboard is told
        exactly what it then receives.

        The driver also writes the terminator into a buffer far larger
        than it sends, so for the few lengths where ``len % 64 >= 62``
        its magic falls outside the transmitted range. We send one more
        block in that case, which keeps the terminator intact.
        """
        blocks = (data_len >> 6) + 1
        if data_len + 3 > blocks * BLOCK:
            blocks += 1
        return blocks

    def block_count(self) -> int:
        """How many 64-byte blocks the upload will announce and send.

        Deliberately derived from the real payload rather than recomputed,
        so this can never disagree with what actually goes on the wire.
        """
        return len(self.payload()) // BLOCK

    @property
    def command(self) -> int:
        return CMD_BOOT_ANIMATION if self.boot else CMD_ANIMATION


# Table names are fixed here rather than interpolated, so the query text is
# never built from anything outside this module.
_FRAMES_LIVE = (
    "SELECT frameIndex, led_index, rgb_value FROM t_led_frames "
    "WHERE layer_id=? ORDER BY frameIndex, led_index"
)
_FRAMES_DEFAULT = (
    "SELECT frameIndex, led_index, rgb_value FROM t_default_led_frames "
    "WHERE layer_id=? ORDER BY frameIndex, led_index"
)


def load_vendor_animations(db_path: str) -> dict[int, tuple[str, Animation]]:
    """Read the stock animations out of the vendor's SQLite store.

    Only useful if you still have `ledlayer_v1.data` from the Windows
    installer; it is not shipped with this project.
    """
    import sqlite3

    # A path is not a URI: "?" or "#" in it would otherwise be read as URI
    # syntax and could override mode=ro.
    uri = "file:" + urllib.parse.quote(os.path.abspath(db_path)) + "?mode=ro"
    out: dict[int, tuple[str, Animation]] = {}
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as con:
        layers = con.execute(
            "SELECT led_id, name, speed, brightness FROM t_ledlayer_data"
        ).fetchall()
        for led_id, name, speed, brightness in layers:
            # Live frames win over the shipped defaults; a layer can appear
            # in both. (The vendor never states the precedence -- this is
            # what the table names imply.)
            for query in (_FRAMES_LIVE, _FRAMES_DEFAULT):
                rows = con.execute(query, (led_id,)).fetchall()
                if not rows:
                    continue
                by_frame: dict[int, Frame] = {}
                for frame_index, index, rgb in rows:
                    try:
                        frame_index = int(frame_index)
                        index = int(index)
                        rgb = int(rgb)
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"layer {led_id}: {db_path} is not a valid "
                            "animation store (non-numeric frame data)"
                        ) from None
                    if not 0 <= index < LEDS:
                        raise ValueError(
                            f"layer {led_id} frame {frame_index}: led_index "
                            f"{index} is outside 0..{LEDS - 1}"
                        )
                    frame = by_frame.get(frame_index)
                    if frame is None:
                        # Anything past the cap is discarded by payload()
                        # anyway; refusing here stops a malformed file from
                        # making us allocate gigabytes first.
                        if len(by_frame) >= MAX_FRAMES_ANIMATION:
                            raise ValueError(
                                f"layer {led_id} has more than "
                                f"{MAX_FRAMES_ANIMATION} frames"
                            )
                        frame = by_frame[frame_index] = Frame()
                    frame.pixels[index] = rgb & 0xFFFFFF
                out[led_id] = (name, Animation(
                    frames=[by_frame[k] for k in sorted(by_frame)],
                    speed=speed,
                    brightness=brightness,
                    boot=(led_id == 1),
                ))
                break
    return out
