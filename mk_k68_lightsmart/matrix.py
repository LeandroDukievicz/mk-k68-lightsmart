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
import json
import os
import struct
import urllib.parse
import sys
from array import array
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

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

#: How long the firmware holds a frame, per unit of the wire value, on
#: the one keyboard this has been measured on.
#:
#: Measured 2026-10-14: ten frames at wire 66 flashed once every 8 s,
#: so 800 ms a frame. It replaces a figure of 100/66 -- about 1.5 ms --
#: that was never measured at all: it came from watching a library GIF
#: and deciding wire 66 looked like the 100 ms the GIF was authored at.
#: Wire 66 is 800 ms. Everything the program said about rate was
#: interpolated from that point, including the timer the preview paced
#: itself with, which is how the two could disagree while both claiming
#: 64 ms a frame.
#:
#: The reading is consistent with the other three things ever written
#: down about this byte: wire 80 "reads slow" is 970 ms, wire 2 is "a
#: blur" at 24 ms, and the old default stepping visibly is 509 ms.
#:
#: Still a fallback, not a fact about every unit: it is one reading
#: from one keyboard running re-flashed V1.1 firmware. Read it through
#: :func:`ms_per_speed_unit`, which prefers this machine's own
#: measurement when there is one.
FALLBACK_MS_PER_SPEED_UNIT = 12.121

#: Where a measurement is kept, next to the schemes file.
CALIBRATION_FILE = "calibration.json"

_measured_unit: float | None = None
_measured_loaded = False


def _calibration_path() -> "Path":
    from .store import config_dir
    return config_dir() / CALIBRATION_FILE


def load_calibration() -> float | None:
    """The measured ms-per-unit on this machine, if one was taken.

    Missing, unreadable and nonsensical files all mean "not measured"
    rather than an error: a bad calibration must not stop the program
    lighting a keyboard.
    """
    try:
        raw = json.loads(_calibration_path().read_text(encoding="utf-8"))
        value = float(raw["ms_per_speed_unit"])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return value if 0.05 <= value <= 200 else None


def ms_per_speed_unit() -> float:
    """How long the firmware holds a frame per unit of the wire value.

    A measurement if there is one, the guess otherwise. Cached, because
    every label and every upload asks.
    """
    global _measured_unit, _measured_loaded
    if not _measured_loaded:
        _measured_unit = load_calibration()
        _measured_loaded = True
    return (FALLBACK_MS_PER_SPEED_UNIT if _measured_unit is None
            else _measured_unit)


def reload_calibration() -> None:
    """Drop the cached reading so the next ask looks at the file again.

    The cache exists because every label and every upload asks for the
    unit; this is how a fresh measurement, or a test's temporary config
    directory, gets seen without restarting.
    """
    global _measured_loaded
    _measured_loaded = False


def is_calibrated() -> bool:
    """Whether the rate this program reports was ever measured."""
    ms_per_speed_unit()
    return _measured_unit is not None


def set_ms_per_speed_unit(value: float | None) -> None:
    """Use ``value`` from now on; ``None`` goes back to the guess.

    In memory only -- :func:`save_calibration` is what outlives the
    process. Tests use this to pin the scale.
    """
    global _measured_unit, _measured_loaded
    if value is not None and not 0.05 <= value <= 200:
        raise ValueError("a frame unit outside 0.05..200 ms is not a reading")
    _measured_unit = value
    _measured_loaded = True


def save_calibration(value: float) -> "Path":
    """Write a measurement where the next run will find it."""
    set_ms_per_speed_unit(value)
    path = _calibration_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ms_per_speed_unit": round(value, 4)}, indent=1) + "\n",
        encoding="utf-8",
    )
    return path

#: The rate the vendor's dot-matrix designs are drawn at. This one is
#: not a guess: every GIF in the shipped set carries a 100 ms frame
#: delay, and index.json records it per effect.
#:
#: There used to be a NATIVE_WIRE_SPEED of 66 here, described as "the
#: wire value that plays at the rate the vendor's designs assume". It
#: was the eyeballed anchor, and the measurement says wire 66 is 800 ms
#: a frame, not 100. The artwork's rate is the durable fact; which wire
#: value delivers it depends on a measurement, so it is derived now and
#: no constant claims to know it.
NATIVE_FRAME_MS = 100

#: Where calibrate-speed measures, unless told otherwise. Mid-scale on
#: purpose: ten frames at this wire value is a loop of several seconds,
#: long enough to time by eye and short enough not to take a minute.
MEASURING_WIRE_SPEED = 66
MEASURING_SPEED = SPEED_BASE - MEASURING_WIRE_SPEED


def default_playback_speed() -> int:
    """The setting to use when the source names no rate of its own.

    The vendor's artwork rate, turned into whatever setting delivers it
    on this keyboard. A function and not a constant because the
    conversion depends on the measurement, and a constant read at
    import would freeze whatever the scale looked like then.

    This used to be a fixed wire value times a PLAYBACK_BOOST of
    1.5625, on the reasoning that the native pace crawls across
    sixty-five columns. Both halves were wrong: the boost made the
    strip disagree with the program's own preview of the same frames,
    and the wire value it boosted away from was itself eight times off.
    """
    return speed_for_ms(NATIVE_FRAME_MS)

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


def speed_for_ms(milliseconds: float) -> int:
    """The 1-100 setting that holds a frame for about that long.

    The scale is inverted and short -- the whole usable range is about
    3 ms to 150 ms a frame -- so this clamps rather than pretending.
    """
    wire = max(1, min(SPEED_BASE - 1,
                      round(milliseconds / ms_per_speed_unit())))
    return SPEED_BASE - wire


def frame_ms(setting: int | None) -> float:
    """About how long a frame lasts at ``setting``."""
    return speed_to_byte(setting) * ms_per_speed_unit()


def speed_for_source(delay_ms: float, stride: int = 1) -> int:
    """The setting that replays a source at the pace it was drawn at.

    ``stride`` is how many source frames each kept frame stands in for,
    so a 100 ms GIF thinned to every third frame wants each survivor
    held for 300 ms -- otherwise thinning a GIF also speeds it up.

    Sources that carry no delay of their own fall back to the default,
    which is that same native rate.

    The result is clamped, and for a thinned GIF it will be: the whole
    scale is one byte of delay units, so :func:`slowest_frame_ms` is as
    long as a frame can be held. Callers that promise a source's own
    pace should say so when :func:`source_rate_is_reachable` is false
    rather than claim a rate the strip cannot keep.
    """
    if delay_ms <= 0:
        return default_playback_speed()
    return speed_for_ms(delay_ms * max(1, stride))


def source_ms(delay_ms: float, stride: int = 1) -> float:
    """How long a kept frame lasted in the source it came from."""
    return max(0.0, delay_ms) * max(1, stride)


def slowest_frame_ms() -> float:
    """The longest a frame can be held: the bottom of the scale.

    A function and not a constant because calibration moves it, and a
    constant read at import would be the old guess for the rest of the
    process.
    """
    return frame_ms(1)


def fastest_frame_ms() -> float:
    """The shortest a frame can be held: the top of the scale."""
    return frame_ms(100)


def source_rate_is_reachable(delay_ms: float, stride: int = 1) -> bool:
    """Whether the strip can hold a frame for that long at all."""
    wanted = source_ms(delay_ms, stride)
    return not wanted or fastest_frame_ms() <= wanted <= slowest_frame_ms()




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
