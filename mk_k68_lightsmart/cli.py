"""Command-line front end, used for bring-up and protocol work.

The GUI is the product; this exists so the protocol can be exercised and
confirmed one packet at a time.
"""

from __future__ import annotations

import argparse
import sys

from . import layout, protocol
from .transport import DeviceNotFound, KT68Transport, TransportError


def _hex(data: bytes, n: int = 16) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


def _parse_colour(text: str) -> tuple[int, int, int]:
    """Parse RRGGBB, with or without a leading #."""
    value = text.lstrip("#")
    if len(value) != 6 or not all(c in "0123456789abcdefABCDEF" for c in value):
        raise ValueError(f"{text!r} is not a RRGGBB colour")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _resolve_key(text: str) -> int:
    """Turn a key name or a raw LED index into an index."""
    key = layout.BY_NAME.get(text)
    if key is not None:
        return key.light_index
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
        print(f"{k.light_index:4d}  {k.name:<10s} hid={k.code:#04x}")
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
    k.add_argument("key", help="key name (see 'keys') or a raw light index")
    k.add_argument("colour", help="RRGGBB")
    k.set_defaults(func=cmd_key_colour)

    e = sub.add_parser("effect", help="select a built-in animation")
    e.add_argument("mode", type=lambda s: int(s, 0), help="effect mode byte")
    e.add_argument("colour", help="RRGGBB")
    e.add_argument("--brightness", type=int, default=0)
    e.add_argument("--speed", type=int, default=0)
    e.add_argument("--direction", type=int, default=0)
    e.set_defaults(func=cmd_effect)

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
