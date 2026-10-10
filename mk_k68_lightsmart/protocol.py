"""Command layer for the 68-key board's vendor HID channel.

Everything here was recovered from the Windows driver's own code paths, so
the framing and the transaction envelope are exactly what the firmware is
used to seeing.  Where a field's *meaning* is still an inference rather
than something the disassembly spelled out, the docstring says so.

Framing
-------
Every payload is a 64-byte feature report.  Two shapes occur:

``04 <sub> ...``
    Short control commands.  Byte 1 selects the operation and byte 8 is
    usually a count or selector.

``<mode> <r> <g> <b> 00 00 00 00 <p8> <p9> <p10> <p11> 00 00 AA 55``
    A 16-byte data frame terminated by the magic ``AA 55``.  A 64-byte
    report can carry four of these back to back.

Transaction envelope
--------------------
The driver never sends a data frame on its own.  It wraps it::

    04 18                       begin
    04 13 .. .. .. .. .. .. 01  select the lighting target
    <data frame>                (sent without waiting for an ack)
    04 02                       apply
    04 F0                       end

Each framed step waits for the keyboard to acknowledge, by reading a
feature report back and checking byte 3 (see :mod:`.transport`).
"""

from __future__ import annotations

from dataclasses import dataclass

from .transport import (
    ACK_OFFSET,
    ACK_OK,
    ACK_RETRY,
    REPORT_SIZE,
    KT68Transport,
    TransportError,
)

# -- control commands (byte 0 == 0x04, byte 1 == the sub-command) --------
CMD_PREFIX = 0x04

SUB_APPLY = 0x02         #: flush what was just written
SUB_KEYMAP = 0x11        #: key remap block follows (byte 8 = 0x09)
SUB_SELECT_LIGHT = 0x13  #: select the lighting target (byte 8 = 0x01)
SUB_UNKNOWN_15 = 0x15
SUB_UNKNOWN_17 = 0x17
SUB_BEGIN = 0x18         #: open a configuration transaction
SUB_UNKNOWN_19 = 0x19
SUB_UNKNOWN_20 = 0x20
SUB_UNKNOWN_23 = 0x23
SUB_FNLAYER = 0x27       #: Fn-layer remap block (byte 8 = 0x09)
SUB_MACRO_A = 0x31
SUB_MACRO_B = 0x33
SUB_UNKNOWN_35 = 0x35
SUB_UNKNOWN_61 = 0x61
SUB_END = 0xF0           #: close the transaction
SUB_UNKNOWN_F5 = 0xF5

#: Byte 0 of a data frame when the frame sets one key's colour outright
#: instead of selecting a built-in animation.
MODE_SINGLE_KEY = 0x80

FRAME_MAGIC = (0xAA, 0x55)
FRAME_SIZE = 16


def control(sub: int, **fields: int) -> bytes:
    """Build a ``04 <sub>`` control payload.

    ``fields`` are passed as ``b<offset>=value``, e.g. ``b8=1`` to set
    byte 8, matching how the driver pokes individual bytes. Offsets 0 and
    1 carry the command itself and are not writable this way.
    """
    buf = bytearray(REPORT_SIZE)
    buf[0] = CMD_PREFIX
    buf[1] = sub
    for key, value in fields.items():
        digits = key[1:]
        # isascii() matters: "１".isdigit() is True and int("１") == 1
        if not (key[:1] == "b" and digits.isascii() and digits.isdigit()):
            raise TypeError(f"expected b<offset>=..., got {key!r}")
        offset = int(digits)
        if not 2 <= offset < REPORT_SIZE:
            raise ValueError(
                f"byte offset {offset} is outside 2..{REPORT_SIZE - 1}"
            )
        if not 0 <= value <= 0xFF:
            raise ValueError(f"{key}={value} is not a byte")
        buf[offset] = value
    return bytes(buf)


def data_frame(mode: int, rgb: tuple[int, int, int] = (0, 0, 0),
               p8: int = 0, p9: int = 0, p10: int = 0, p11: int = 0) -> bytes:
    """Build one 16-byte data frame, padded out to a full report."""
    buf = bytearray(REPORT_SIZE)
    buf[0] = mode
    buf[1], buf[2], buf[3] = rgb
    buf[8], buf[9], buf[10], buf[11] = p8, p9, p10, p11
    buf[14], buf[15] = FRAME_MAGIC
    return bytes(buf)


@dataclass(frozen=True)
class Effect:
    """A built-in lighting animation.

    ``mode`` is byte 0 of the data frame.  The names come from the
    driver's own English resource file (``language/1033.lan``, ids
    200-221); the numeric modes are **not** stated there and are the part
    still to be confirmed against hardware.
    """

    mode: int
    name: str


class Keyboard:
    """High-level operations on the keyboard."""

    def __init__(self, transport: KT68Transport | None = None):
        # Only close what we opened; a caller-supplied transport stays
        # the caller's to manage.
        self._owns_transport = transport is None
        self.t = transport or KT68Transport()

    def __enter__(self) -> "Keyboard":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_transport:
            self.t.close()

    # -- framed steps ---------------------------------------------------
    def _step(self, payload: bytes, expect_ack: bool = True) -> None:
        """Send one payload, optionally waiting for the keyboard's ack.

        The driver retries once when the keyboard answers ``0xFF``, so we
        do the same rather than failing on a transient busy reply.
        """
        self.t.set_feature(payload)
        if not expect_ack:
            return
        reply = self.t.get_feature()
        if reply[ACK_OFFSET] == ACK_RETRY:
            self.t.set_feature(payload)
            reply = self.t.get_feature()
        if reply[ACK_OFFSET] != ACK_OK:
            name = " ".join(f"{b:#04x}" for b in payload[:2])
            raise TransportError(
                f"keyboard rejected [{name}]: status byte "
                f"{reply[ACK_OFFSET]:#04x}"
            )

    def transaction(self, *frames: bytes) -> None:
        """Run the driver's begin/select/apply/end envelope around frames.

        The closing step is sent even when something fails partway, so a
        broken transfer does not leave the keyboard with the transaction
        still open.
        """
        self._step(control(SUB_BEGIN))
        try:
            self._step(control(SUB_SELECT_LIGHT, b8=0x01))
            for frame in frames:
                self._step(frame, expect_ack=False)
            self._step(control(SUB_APPLY))
        finally:
            self._end_quietly()

    def _end_quietly(self) -> None:
        """Close the transaction, swallowing a secondary failure."""
        try:
            self._step(control(SUB_END))
        except TransportError:
            pass

    # -- lighting -------------------------------------------------------
    def set_effect(self, mode: int, rgb: tuple[int, int, int] = (0, 0, 0),
                   p8: int = 0, brightness: int = 0, speed: int = 0,
                   direction: int = 0) -> None:
        """Select a built-in animation.

        The driver fills bytes 8-11 from a 20-byte per-effect settings
        record.  Mapping them to brightness / speed / direction matches
        the four knobs the UI offers for an effect, but which byte is
        which has not been confirmed on hardware yet.
        """
        self.transaction(data_frame(mode, rgb, p8, brightness, speed, direction))

    # -- dot-matrix strip -----------------------------------------------
    def upload_animation(self, animation, progress=None) -> int:
        """Push an animation to the 65x5 strip.

        Mirrors the driver's bulk path: announce the block count, stream
        the payload, then apply. The keyboard shows a loading screen for
        the duration -- unplugging it midway leaves the upload unfinished,
        which is what the vendor manual warns about.

        ``progress`` is called with ``(chunk, total)`` as the payload goes
        out, so a GUI can show a bar. Returns the chunks sent.
        """
        payload = animation.payload()
        if len(payload) % REPORT_SIZE:
            raise ValueError(
                f"payload is {len(payload)} bytes, not a multiple of "
                f"{REPORT_SIZE}"
            )
        blocks = len(payload) // REPORT_SIZE
        if blocks > 0xFFFF:
            raise ValueError(f"{blocks} blocks exceeds the 16-bit count")

        self._step(control(SUB_BEGIN))
        try:
            self._step(control(animation.command,
                               b8=blocks & 0xFF, b9=(blocks >> 8) & 0xFF))
            sent = 0
            for i in range(blocks):
                self.t.set_feature(
                    payload[i * REPORT_SIZE : (i + 1) * REPORT_SIZE]
                )
                sent += 1
                if progress is not None:
                    progress(sent, blocks)
            self._step(control(SUB_APPLY))
        finally:
            self._end_quietly()
        return sent

    def set_key_colour(self, light_index: int, rgb: tuple[int, int, int]) -> None:
        """Light one key a fixed colour.

        Mirrors the driver's "user light" path, which likewise sets a
        single key per transaction -- the manual spells that limitation
        out, so it is the firmware's, not an artefact of the port.
        """
        self.transaction(
            data_frame(MODE_SINGLE_KEY, rgb, p8=light_index, p9=0,
                       p10=0x0F, p11=0x0F)
        )
