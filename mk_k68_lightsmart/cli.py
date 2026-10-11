"""Command-line front end, used for bring-up and protocol work.

The GUI is the product; this exists so the protocol can be exercised and
confirmed one packet at a time.
"""

from __future__ import annotations

import argparse
import sys

from . import layout, matrix, protocol
from .transport import DeviceNotFound, KT68Transport, TransportError

#: One colour per strip row, for the diagnostic pattern. Chosen so that
#: a channel-order mistake is unmistakable: if the wire took R where it
#: wanted B, the top row comes back blue and the middle row red.
ROW_COLOURS = (0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0xFF00FF)


def _hex(data: bytes, n: int = 16) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


def _parse_colour(text: str) -> tuple[int, int, int]:
    """Parse RRGGBB, with or without a leading #."""
    value = text.lstrip("#")
    if len(value) != 6 or not all(c in "0123456789abcdefABCDEF" for c in value):
        raise ValueError(f"{text!r} is not a RRGGBB colour")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _resolve_key(text: str) -> int:
    """Turn a key identifier or a raw LED index into an LED index."""
    key = layout.BY_IDENT.get(text)
    if key is not None:
        return key.light_index
    if text in layout.AMBIGUOUS:
        options = " or ".join(layout.AMBIGUOUS[text])
        raise ValueError(f"{text!r} matches two keys - use {options}")
    try:
        index = int(text, 0)
    except ValueError:
        raise ValueError(
            f"no key called {text!r}; run 'keys' for the list, or pass an index"
        ) from None
    if index not in layout.BY_LIGHT_INDEX:
        raise ValueError(f"no key uses LED index {index}")
    return index


def cmd_info(args: argparse.Namespace) -> int:
    """Report what was found, touching the device read-only."""
    with KT68Transport() as t:
        print(f"control interface : {t.path}")
        print(f"usb id            : 05AC:024F (SONiX / KT68 RGB)")
        print(f"keys              : {len(layout.KEYS)}")
        print(f"dot-matrix        : 65 x 5 = 325 LEDs")
        print(f"feature read      : {_hex(t.get_feature())}")
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    for k in sorted(layout.KEYS, key=lambda k: k.light_index):
        label = "" if k.ident == k.name else f'  (printed "{k.name}")'
        print(f"{k.light_index:4d}  {k.ident:<11s} hid={k.code:#04x}{label}")
    return 0


def cmd_raw(args: argparse.Namespace) -> int:
    """Send one raw payload. The blunt instrument for protocol work."""
    try:
        payload = bytes.fromhex(args.hex.replace(",", " ").replace("-", " "))
    except ValueError as exc:
        raise ValueError(f"{args.hex!r} is not hex bytes: {exc}") from None
    if not payload:
        raise ValueError("nothing to send")
    print(f"-> {_hex(payload, 64)}")
    with KT68Transport() as t:
        t.set_feature(payload)
        if args.read:
            print(f"<- {_hex(t.get_feature(), 64)}")
    return 0


def cmd_key_colour(args: argparse.Namespace) -> int:
    rgb = _parse_colour(args.colour)
    index = _resolve_key(args.key)
    with protocol.Keyboard() as kb:
        kb.set_key_colour(index, rgb)
    print(f"key {args.key} (LED index {index}) -> #{args.colour.lstrip('#')}")
    return 0


def fill_frame(rgb: int) -> matrix.Frame:
    return matrix.Frame([rgb] * matrix.LEDS)


def test_frame() -> matrix.Frame:
    """A pattern built so each possible mistake looks different.

    * One colour per row: wrong channel order recolours the rows.
    * Brightness ramps left to right: a column/row mix-up turns the
      horizontal ramp into a vertical one, and a wrong starting corner
      runs it backwards.
    * The first LED is white: it marks where the firmware thinks (0,0)
      is, which is the one thing a ramp alone cannot show.
    """
    frame = matrix.Frame()
    for row in range(matrix.ROWS):
        base = ROW_COLOURS[row]
        for col in range(matrix.COLS):
            scale = (col + 1) / matrix.COLS
            frame[col, row] = (
                (int(((base >> 16) & 0xFF) * scale) << 16)
                | (int(((base >> 8) & 0xFF) * scale) << 8)
                | int((base & 0xFF) * scale)
            )
    frame[0, 0] = 0xFFFFFF
    return frame


def probe_frame() -> matrix.Frame:
    """One upload that separates a row order from a channel order.

    The first diagnostic pattern could not tell them apart: its row
    colours were green/blue and yellow/magenta, which are exactly the
    two pairs a green-blue swap maps onto each other, so a channel swap
    and a 1<->2, 3<->4 row swap look identical.

    Left half: rows alternate red and white, both unchanged by any
    green/blue swap. Whatever order they come back in is the row order.

    Right half: green, ramping to full at the right edge. Green is the
    primary that tells a B,G,R wire order from a G,B,R one, and the ramp
    says which end the firmware counts columns from.
    """
    frame = matrix.Frame()
    gap = matrix.COLS // 2          # one dark column, so the halves read apart
    for row in range(matrix.ROWS):
        left = 0xFF0000 if row % 2 == 0 else 0xFFFFFF
        for col in range(gap):
            frame[col, row] = left
        for col in range(gap + 1, matrix.COLS):
            scale = (col - gap) / (matrix.COLS - 1 - gap)
            frame[col, row] = int(0xFF * scale) << 8
    return frame


def cmd_strip(args: argparse.Namespace) -> int:
    """Upload one still frame to the dot-matrix strip."""
    if args.pattern == "test":
        frame = test_frame()
    elif args.pattern == "probe":
        frame = probe_frame()
    elif args.pattern == "clear":
        frame = fill_frame(0x000000)
    else:
        r, g, b = _parse_colour(args.colour or "FFFFFF")
        frame = fill_frame(r << 16 | g << 8 | b)

    animation = matrix.Animation(
        frames=[frame], brightness=args.brightness, boot=args.boot
    )
    payload = animation.payload()
    blocks = len(payload) // matrix.BLOCK
    target = "boot animation" if args.boot else "animation"
    print(f"pattern       : {args.pattern}")
    print(f"target        : {target} (command {animation.command:#04x})")
    print(f"payload       : {len(payload)} bytes, {blocks} blocks")
    print(f"first pixels  : {_hex(payload[4:16], 12)}  (R G B per LED)")
    print(f"estimated     : {blocks * 0.015:.1f}s")
    if args.dry_run:
        print("dry run - nothing sent")
        return 0

    with protocol.Keyboard() as kb:
        sent = kb.upload_animation(animation)
    print(f"sent          : {sent} blocks")
    return 0


def cmd_effect_scan(args: argparse.Namespace) -> int:
    """Walk the effect modes one at a time, recording what each does.

    The driver's string table names 22 effects but never numbers them,
    so the mapping has to come off the hardware. This asks for what you
    saw and prints the table it builds, ready to paste.
    """
    if not sys.stdin.isatty():
        raise ValueError("effect-scan needs a terminal: it asks what you saw")

    seen: list[tuple[int, str]] = []
    rgb = _parse_colour(args.colour)
    try:
        with protocol.Keyboard() as kb:
            for mode in range(args.first, args.last + 1):
                try:
                    kb.set_effect(mode, rgb, brightness=args.brightness,
                                  speed=args.speed)
                except TransportError as exc:
                    note = f"rejected: {exc}"
                    print(f"{mode:#04x}  {note}")
                else:
                    note = input(f"{mode:#04x}  what did it do? ").strip()
                    if note.lower() in ("q", "quit"):
                        break
                seen.append((mode, note or "-"))
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        print()
        print("| mode | what it did |")
        print("|---|---|")
        for mode, note in seen:
            print(f"| `{mode:#04x}` | {note} |")
    return 0


def cmd_effect(args: argparse.Namespace) -> int:
    rgb = _parse_colour(args.colour)
    with protocol.Keyboard() as kb:
        kb.set_effect(args.mode, rgb, brightness=args.brightness,
                      speed=args.speed, direction=args.direction)
    print(f"effect mode {args.mode:#04x} -> #{args.colour}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mk-k68-lightsmart",
        description="Control a 68-key LightSmart keyboard over its vendor HID channel.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="show the detected device (read-only)").set_defaults(func=cmd_info)
    sub.add_parser("keys", help="list keys and their LED indices").set_defaults(func=cmd_keys)

    r = sub.add_parser("raw", help="send a raw payload, as hex bytes")
    r.add_argument("hex", help='e.g. "04 18" or 0418')
    r.add_argument("--read", action="store_true", help="read a feature report back")
    r.set_defaults(func=cmd_raw)

    k = sub.add_parser("key", help="set one key to a fixed colour")
    k.add_argument("key", help="key identifier (see 'keys') or a raw LED index")
    k.add_argument("colour", help="RRGGBB")
    k.set_defaults(func=cmd_key_colour)

    e = sub.add_parser("effect", help="select a built-in animation")
    e.add_argument("mode", type=lambda s: int(s, 0), help="effect mode byte")
    e.add_argument("colour", help="RRGGBB")
    e.add_argument("--brightness", type=int, default=0)
    e.add_argument("--speed", type=int, default=0)
    e.add_argument("--direction", type=int, default=0)
    e.set_defaults(func=cmd_effect)

    s = sub.add_parser("strip", help="upload one frame to the dot-matrix strip")
    s.add_argument("pattern", choices=("test", "probe", "fill", "clear"),
                   help="test = the diagnostic pattern; "
                        "probe = row order vs channel order")
    s.add_argument("colour", nargs="?", help="RRGGBB, for 'fill'")
    s.add_argument("--brightness", type=int, default=100, help="0-100, default 100")
    s.add_argument("--boot", action="store_true",
                   help="write the boot animation slot instead (persists)")
    s.add_argument("--dry-run", action="store_true",
                   help="describe the payload without sending it")
    s.set_defaults(func=cmd_strip)

    es = sub.add_parser("effect-scan",
                        help="walk the effect modes, recording what each does")
    es.add_argument("--first", type=lambda s: int(s, 0), default=0x00)
    es.add_argument("--last", type=lambda s: int(s, 0), default=0x15)
    es.add_argument("--colour", default="FF0000")
    es.add_argument("--brightness", type=int, default=10)
    es.add_argument("--speed", type=int, default=8)
    es.set_defaults(func=cmd_effect_scan)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except DeviceNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
