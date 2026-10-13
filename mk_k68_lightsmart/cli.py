"""Command-line front end, used for bring-up and protocol work.

The GUI is the product; this exists so the protocol can be exercised and
confirmed one packet at a time.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import layout, matrix, protocol
from . import transport
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


def cmd_vendor(args: argparse.Namespace) -> int:
    """List or upload the stock animations from a vendor database.

    The database is not shipped with this project; this reads one you
    still have from the Windows installer. It is the only source of a
    long, multi-frame upload, so it is also how the bulk path gets
    exercised past a single frame.
    """
    animations = matrix.load_vendor_animations(args.db)
    if args.layer is None:
        print(f"{'layer':>5}  {'frames':>6}  {'blocks':>6}  name")
        for lid, (name, animation) in sorted(animations.items()):
            print(f"{lid:>5}  {len(animation.frames):>6}  "
                  f"{animation.block_count():>6}  {name}")
        return 0

    found = animations.get(args.layer)
    if found is None:
        raise ValueError(
            f"no layer {args.layer} in {args.db}; run without --layer to list"
        )
    name, animation = found
    # Never the boot slot unless it is asked for by name: that one
    # persists across a power cycle.
    animation.boot = args.boot
    if args.brightness is not None:
        animation.brightness = args.brightness

    blocks = animation.block_count()
    print(f"layer         : {args.layer} ({name})")
    print(f"frames        : {len(animation.frames)}")
    print(f"target        : {'boot animation' if args.boot else 'animation'} "
          f"(command {animation.command:#04x})")
    print(f"payload       : {blocks} blocks, {blocks * matrix.BLOCK} bytes")
    print(f"estimated     : {blocks * 0.015:.0f}s")
    if args.dry_run:
        print("dry run - nothing sent")
        return 0

    last = 0

    def report(sent: int, total: int) -> None:
        nonlocal last
        if sent - last >= 128 or sent == total:
            last = sent
            print(f"\rsending       : {sent}/{total} blocks", end="", flush=True)

    with protocol.Keyboard() as kb:
        kb.upload_animation(animation, progress=report)
    print()
    return 0


def cmd_install_udev(args: argparse.Namespace) -> int:
    """Put the udev rule in place, with one command instead of three.

    Without the rule the vendor interface belongs to root and the app
    finds nothing, which is the first thing everyone hits. This runs
    the copy and the two reloads under sudo and then checks that the
    keyboard actually became reachable.
    """
    import shutil
    import subprocess

    source = transport.rule_source()
    if not os.path.exists(source):
        raise ValueError(f"this install has no copy of the rule at {source}")

    print(f"rule      : {source}")
    print(f"target    : {transport.RULE_DIR}/{transport.RULE_NAME}")
    if transport.rule_installed() and not args.force:
        print("already installed - pass --force to put it there again")
    else:
        sudo = [] if os.geteuid() == 0 else ["sudo"]
        if sudo and shutil.which("sudo") is None:
            raise ValueError("not root, and sudo is not installed")
        for command in transport.install_rule_commands():
            print("  " + " ".join(sudo + command))
            result = subprocess.run(sudo + command)
            if result.returncode:
                print(f"error: that command failed ({result.returncode})",
                      file=sys.stderr)
                return 1

    try:
        path = transport.find_control_device()
    except DeviceNotFound as exc:
        print(f"\nthe rule is in place, but: {exc}", file=sys.stderr)
        return 1
    readable = os.access(path, os.R_OK | os.W_OK)
    print(f"\ncontrol interface: {path} "
          f"{'- reachable' if readable else '- still not readable, try replugging'}")
    return 0 if readable else 1


def cmd_meter(args: argparse.Namespace) -> int:
    """Show what the AI tools on this machine have spent.

    Reads the transcripts Claude Code and Codex keep locally -- no
    network, no login -- and puts a panel for each on the strip. With
    --watch it keeps doing it, which is the only way a strip that plays
    from its own memory can show a number that moves.
    """
    from . import patterns, usage

    def once(kb) -> None:
        readings = usage.read_all()
        for reading in readings:
            plan = ("--" if reading.plan_percent is None
                    else f"{reading.plan_percent:.0f}%")
            print(f"{reading.tool:>7}: {usage.short(reading.tokens):>6} tokens  "
                  f"context {(reading.context_percent or 0):3.0f}%  plan {plan:>4}  "
                  f"{'live' if reading.live else 'idle'}")
        animation = matrix.Animation(
            frames=patterns.meter(readings, frames_each=args.frames_each),
            brightness=args.brightness,
            speed=args.speed,
        )
        if args.dry_run:
            print(f"dry run - {animation.block_count()} blocks, nothing sent")
            return
        kb.upload_animation(animation)

    if args.dry_run:
        once(None)
        return 0

    with protocol.Keyboard() as kb:
        once(kb)
        while args.watch:
            time.sleep(args.watch)
            print()
            once(kb)
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    """Put a different effect on the strip every few minutes.

    The keyboard holds one animation at a time, and 941 frames of it --
    about ninety seconds at the usual rate. Anything longer than that
    has to come from here, which is the trade: a real five-minute
    rotation, but the strip spends the seconds of each upload showing
    the keyboard's loading state, and it stops when this does.
    """
    import random
    from . import patterns

    names = args.names or [name for name in patterns.catalogue()]
    missing = [name for name in names if name not in patterns.catalogue(True)]
    if missing:
        raise ValueError(f"no effect called {missing[0]!r}")
    if args.shuffle:
        random.shuffle(names)

    print(f"rotating {len(names)} effects, one every {args.every:g}s")
    print("ctrl-c to stop\n")
    index = 0
    with protocol.Keyboard() as kb:
        while True:
            name = names[index % len(names)]
            animation = matrix.Animation(
                frames=patterns.build(name), brightness=args.brightness,
                speed=args.speed,
            )
            blocks = animation.block_count()
            print(f"{name:<16} {len(animation.frames):>4} frames, "
                  f"{blocks:>5} blocks, {blocks * 0.015:>4.0f}s to send",
                  flush=True)
            kb.upload_animation(animation)
            index += 1
            time.sleep(max(0.0, args.every - blocks * 0.015))


def cmd_pattern(args: argparse.Namespace) -> int:
    """Upload one of the animations the program generates itself."""
    from . import patterns

    if args.name is None:
        catalogue = patterns.catalogue(with_overall=True)
        width = max(len(n) for n in catalogue)
        for name, label in catalogue.items():
            print(f"{name:<{width}}  {label}")
        return 0

    frames = patterns.build(args.name)
    animation = matrix.Animation(
        frames=frames, brightness=args.brightness, speed=args.speed
    )
    blocks = animation.block_count()
    print(f"pattern       : {args.name}")
    print(f"frames        : {len(frames)}")
    print(f"payload       : {blocks} blocks, {blocks * matrix.BLOCK} bytes")
    print(f"estimated     : {blocks * 0.015:.0f}s")
    print(f"playback      : speed {args.speed}, about "
          f"{matrix.frame_ms(args.speed):.0f} ms a frame, "
          f"{len(frames) * matrix.frame_ms(args.speed) / 1000:.0f}s a loop")
    if args.dry_run:
        print("dry run - nothing sent")
        return 0

    last = 0

    def report(sent: int, total: int) -> None:
        nonlocal last
        if sent - last >= 128 or sent == total:
            last = sent
            print(f"\rsending       : {sent}/{total} blocks", end="", flush=True)

    with protocol.Keyboard() as kb:
        kb.upload_animation(animation, progress=report)
    print()
    return 0


def cmd_image(args: argparse.Namespace) -> int:
    """Upload a picture or an animation to the strip.

    The vendor's library serves its designs as wide, short GIFs, which
    is the strip drawn at ten or more pixels per LED; this scales one
    back down to 65x5. Importing Qt here rather than at module level
    keeps the rest of the CLI usable without a desktop toolkit.
    """
    from . import image as image_module

    loaded = image_module.load(args.path, stride=args.stride, limit=args.frames)
    animation = matrix.Animation(
        frames=loaded.frames, brightness=args.brightness, speed=args.speed
    )
    blocks = animation.block_count()
    kept = (f"every {args.stride} of {loaded.available}" if args.stride > 1
            else f"all {loaded.available}")
    print(f"source        : {loaded.size[0]}x{loaded.size[1]}, "
          f"{loaded.available} frame(s)"
          + (f", {loaded.delay_ms} ms apart" if loaded.delay_ms else ""))
    print(f"importing     : {len(loaded.frames)} frames ({kept})")
    print(f"payload       : {blocks} blocks, {blocks * matrix.BLOCK} bytes")
    print(f"estimated     : {blocks * 0.015:.0f}s")
    print(f"playback      : speed {args.speed}, about "
          f"{matrix.frame_ms(args.speed):.0f} ms a frame, "
          f"{len(loaded.frames) * matrix.frame_ms(args.speed) / 1000:.0f}s a loop")
    if args.dry_run:
        print("dry run - nothing sent")
        return 0

    last = 0

    def report(sent: int, total: int) -> None:
        nonlocal last
        if sent - last >= 128 or sent == total:
            last = sent
            print(f"\rsending       : {sent}/{total} blocks", end="", flush=True)

    with protocol.Keyboard() as kb:
        kb.upload_animation(animation, progress=report)
    print()
    return 0


def cmd_effect_scan(args: argparse.Namespace) -> int:
    """Walk the effect modes one at a time, recording what each does.

    The driver's string table names 22 effects but never numbers them,
    so the mapping has to come off the hardware. This asks for what you
    saw and prints the table it builds, ready to paste.
    """
    if not args.auto and not sys.stdin.isatty():
        raise ValueError(
            "effect-scan asks what you saw, so it needs a terminal. Run it "
            "in one, or pass --auto to walk the modes on a timer instead"
        )

    seen: list[tuple[int, str]] = []
    rgb = _parse_colour(args.colour)
    if args.auto:
        print(f"Walking modes {args.first:#04x}-{args.last:#04x}, "
              f"{args.dwell:g}s each. Watch the keys and write down what "
              f"each one does, in order.\n")
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
                    if args.auto:
                        # Numbered, so a narrated list can be lined up
                        # with the modes afterwards without counting.
                        n = mode - args.first + 1
                        print(f"{n:>3}. {mode:#04x} sent", flush=True)
                        time.sleep(args.dwell)
                        note = ""
                    else:
                        known = protocol.EFFECT_BY_MODE.get(mode)
                        label = f" [{known.name}]" if known else ""
                        note = input(
                            f"{mode:#04x}{label}  what did it do? "
                        ).strip()
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
    mode = protocol.effect_mode(args.mode)
    with protocol.Keyboard() as kb:
        kb.set_effect(mode, rgb, brightness=args.brightness,
                      speed=args.speed, direction=args.direction)
    named = protocol.EFFECT_BY_MODE.get(mode)
    label = f" ({named.name})" if named else ""
    print(f"effect mode {mode:#04x}{label} -> #{args.colour.lstrip('#')}")
    return 0


def cmd_effects(args: argparse.Namespace) -> int:
    """List the effects the vendor UI offers, with their mode bytes."""
    for effect in protocol.EFFECTS:
        print(f"{effect.mode:#04x}  {effect.name}")
    return 0


def speed_help() -> str:
    """The --speed help, generated so a change of rate cannot stale it."""
    return (f"1-100; the default is {matrix.PLAYBACK_BOOST:g}x the rate the "
            f"vendor's own designs assume, about "
            f"{matrix.frame_ms(matrix.DEFAULT_PLAYBACK_SPEED):.0f} ms a frame")


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

    sub.add_parser(
        "effects", help="list the built-in animations and their modes"
    ).set_defaults(func=cmd_effects)

    e = sub.add_parser("effect", help="select a built-in animation")
    e.add_argument("mode", help="effect name (see 'effects') or a mode byte")
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
    es.add_argument("--last", type=lambda s: int(s, 0), default=0x14)
    es.add_argument("--colour", default="FF0000")
    es.add_argument("--brightness", type=int, default=10)
    es.add_argument("--speed", type=int, default=8)
    es.add_argument("--auto", action="store_true",
                    help="walk the modes on a timer instead of asking")
    es.add_argument("--dwell", type=float, default=4.0,
                    help="seconds per mode with --auto (default 4)")
    es.set_defaults(func=cmd_effect_scan)

    u = sub.add_parser(
        "install-udev", help="install the udev rule the keyboard needs")
    u.add_argument("--force", action="store_true",
                   help="install it again even if it is already there")
    u.set_defaults(func=cmd_install_udev)

    m = sub.add_parser(
        "meter", help="put this machine's AI token usage on the strip")
    m.add_argument("--watch", type=float, metavar="SECONDS",
                   help="keep refreshing every SECONDS")
    m.add_argument("--frames-each", type=int, default=1, dest="frames_each",
                   help="frames per panel (default 1)")
    m.add_argument("--brightness", type=int, default=100, help="0-100")
    m.add_argument("--speed", type=int, default=matrix.DEFAULT_PLAYBACK_SPEED,
                   help=speed_help())
    m.add_argument("--dry-run", action="store_true")
    m.set_defaults(func=cmd_meter)

    ro = sub.add_parser(
        "rotate", help="change the effect every few minutes, while this runs")
    ro.add_argument("names", nargs="*", help="which ones; default is all")
    ro.add_argument("--every", type=float, default=300,
                    metavar="SECONDS", help="seconds per effect (default 300)")
    ro.add_argument("--shuffle", action="store_true")
    ro.add_argument("--brightness", type=int, default=100, help="0-100")
    ro.add_argument("--speed", type=int,
                    default=matrix.DEFAULT_PLAYBACK_SPEED,
                    help=speed_help())
    ro.set_defaults(func=cmd_rotate)

    pt = sub.add_parser("pattern", help="upload a built-in animation")
    pt.add_argument("name", nargs="?",
                    help="which one; omit to list them")
    pt.add_argument("--brightness", type=int, default=100, help="0-100")
    pt.add_argument("--speed", type=int, default=matrix.DEFAULT_PLAYBACK_SPEED,
                   help=speed_help())
    pt.add_argument("--dry-run", action="store_true")
    pt.set_defaults(func=cmd_pattern)

    i = sub.add_parser("image", help="upload an image or GIF to the strip")
    i.add_argument("path", help="any image Qt can read; GIFs animate")
    i.add_argument("--stride", type=int, default=1,
                   help="keep every nth frame (default 1)")
    i.add_argument("--frames", type=int, help="stop after this many frames")
    i.add_argument("--brightness", type=int, default=100, help="0-100")
    i.add_argument("--speed", type=int, default=matrix.DEFAULT_PLAYBACK_SPEED,
                   help=speed_help())
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(func=cmd_image)

    v = sub.add_parser(
        "vendor", help="list or upload stock animations from a vendor database")
    v.add_argument("--db", required=True, help="path to ledlayer_v1.data")
    v.add_argument("--layer", type=int, help="layer id; omit to list them")
    v.add_argument("--brightness", type=int, help="override, 0-100")
    v.add_argument("--boot", action="store_true",
                   help="write the boot slot, which persists")
    v.add_argument("--dry-run", action="store_true")
    v.set_defaults(func=cmd_vendor)

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
