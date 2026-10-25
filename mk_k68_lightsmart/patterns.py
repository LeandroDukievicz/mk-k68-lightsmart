"""Animations the program ships with.

Generated rather than drawn, so they fit the strip exactly and carry no
artwork from anywhere. Everything here is deterministic: the same call
returns the same frames, which is what lets the tests check them.
"""

from __future__ import annotations

import functools
import gzip
import json
import random
from pathlib import Path

from .matrix import (
    COLS,
    FRAME_BYTES,
    LEDS,
    MAX_FRAMES_ANIMATION,
    ROWS,
    Animation,
    Frame,
    default_playback_speed,
    frame_ms,
    speed_for_ms,
    speed_for_source,
)

#: Where the shipped animations live, as the bytes the keyboard is sent.
EFFECTS_DIR = Path(__file__).resolve().parent / "effects"

#: The tetrominoes, in their usual colours, turned up.
PIECE_COLOURS = {
    "I": 0x00FFFF,   # cyan
    "O": 0xFFE000,   # yellow
    "T": 0xB000FF,   # purple
    "S": 0x00FF40,   # green
    "Z": 0xFF2020,   # red
    "J": 0x2040FF,   # blue
    "L": 0xFF9000,   # orange
}

#: The colour of the one cell no tetromino can reach -- see `tetris_fill`.
LAST_CELL_COLOUR = 0xFFFFFF

#: Ways to fill a four-column slice of the strip, each as five pieces in
#: the order they are dropped.
#:
#: Found by searching the drops themselves rather than the tilings: at
#: every step the piece falls straight down and may leave no cell
#: covered. 1777 such fills exist for a 4x5 slice; these five were
#: picked to use all seven tetrominoes between them, so the wall comes
#: out in seven colours rather than four.
SLICE_FILLS: tuple[tuple[tuple[str, tuple[tuple[int, int], ...]], ...], ...] = (
    (   # ZZLL / IZZL / IJJL / IJOO / IJOO
        ("I", ((0, 1), (0, 2), (0, 3), (0, 4))),
        ("O", ((2, 3), (2, 4), (3, 3), (3, 4))),
        ("J", ((1, 2), (1, 3), (1, 4), (2, 2))),
        ("Z", ((0, 0), (1, 0), (1, 1), (2, 1))),
        ("L", ((2, 0), (3, 0), (3, 1), (3, 2))),
    ),
    (   # TTTT / ITTT / ILST / ILSS / ILLS
        ("I", ((0, 1), (0, 2), (0, 3), (0, 4))),
        ("L", ((1, 2), (1, 3), (1, 4), (2, 4))),
        ("S", ((2, 2), (2, 3), (3, 3), (3, 4))),
        ("T", ((2, 1), (3, 0), (3, 1), (3, 2))),
        ("T", ((0, 0), (1, 0), (1, 1), (2, 0))),
    ),
    (   # LLOO / ILOO / ILZJ / IZZJ / IZJJ
        ("I", ((0, 1), (0, 2), (0, 3), (0, 4))),
        ("J", ((2, 4), (3, 2), (3, 3), (3, 4))),
        ("Z", ((1, 3), (1, 4), (2, 2), (2, 3))),
        ("O", ((2, 0), (2, 1), (3, 0), (3, 1))),
        ("L", ((0, 0), (1, 0), (1, 1), (1, 2))),
    ),
    (   # JJSS / JSSI / JLLI / OOLI / OOLI
        ("I", ((3, 1), (3, 2), (3, 3), (3, 4))),
        ("O", ((0, 3), (0, 4), (1, 3), (1, 4))),
        ("L", ((1, 2), (2, 2), (2, 3), (2, 4))),
        ("S", ((1, 1), (2, 0), (2, 1), (3, 0))),
        ("J", ((0, 0), (0, 1), (0, 2), (1, 0))),
    ),
    (   # OOJJ / OOJI / LSJI / LSSI / LLSI
        ("I", ((3, 1), (3, 2), (3, 3), (3, 4))),
        ("L", ((0, 2), (0, 3), (0, 4), (1, 4))),
        ("S", ((1, 2), (1, 3), (2, 3), (2, 4))),
        ("O", ((0, 0), (0, 1), (1, 0), (1, 1))),
        ("J", ((2, 0), (2, 1), (2, 2), (3, 0))),
    ),
)

SLICE_WIDTH = 4
#: Pieces in a slice, which is also how many waves the wall takes.
WAVES = len(SLICE_FILLS[0])


def _wall_waves():
    """The wall as waves of pieces, each wave falling all at once.

    One piece per four-column slice per wave, so every wave spans the
    whole strip: sixteen pieces coming down together rather than one
    after another. Neighbouring slices take different fills, so the same
    shape rarely lands twice in a row.

    The last column is its own case. 65 x 5 is 325 cells and a tetromino
    is four, so no arrangement of them fills the strip: a vertical I
    takes four of that column's five, and the cell left under it is lit
    white -- the piece the game does not have.
    """
    slices = COLS // SLICE_WIDTH
    waves: list[list[tuple[str, tuple[tuple[int, int], ...]]]] = [
        [] for _ in range(WAVES)
    ]
    for index in range(slices):
        fill = SLICE_FILLS[index % len(SLICE_FILLS)]
        for wave, (name, cells) in enumerate(fill):
            waves[wave].append((
                name,
                tuple((col + index * SLICE_WIDTH, row) for col, row in cells),
            ))

    last = COLS - 1
    waves[0].append(("_", ((last, ROWS - 1),)))
    waves[1].append(("I", tuple((last, row) for row in range(ROWS - 1))))
    return waves


def tetris_fill(pace: int = 2, hold: int = 12) -> list[Frame]:
    """Tetrominoes falling in waves until the strip is a solid wall.

    Every wave drops across the whole strip at once, each piece entering
    from above and stopping where it lands -- so the ones with furthest
    to fall are still moving after the others have settled.

    ``pace`` frames are spent on each row of the fall: 1 is a drop, 2
    is a fall you can follow on a strip only five LEDs tall. ``hold``
    frames keep the finished wall up before the loop restarts.
    """
    if pace < 1:
        raise ValueError("pace is 1 or more")

    board = [0] * LEDS
    frames: list[Frame] = []

    for wave in _wall_waves():
        # A piece enters with its top cell just above the strip, so the
        # distance it falls is that row plus one.
        travel = max(min(row for _, row in cells) + 1 for _, cells in wave)
        for tick in range(1, travel * pace + 1):
            elapsed = tick // pace
            frame = Frame(board)
            for name, cells in wave:
                colour = (LAST_CELL_COLOUR if name == "_"
                          else PIECE_COLOURS[name])
                top = min(row for _, row in cells)
                offset = max(0, top + 1 - elapsed)
                for col, row in cells:
                    if row - offset >= 0:
                        frame[col, row - offset] = colour
            frames.append(frame)
        for name, cells in wave:
            colour = LAST_CELL_COLOUR if name == "_" else PIECE_COLOURS[name]
            for col, row in cells:
                board[row * COLS + col] = colour

    frames.extend(Frame(board) for _ in range(hold))
    return frames


#: Rain colours, by the name the UI offers.
RAIN_COLOURS = {
    "green": (0x00FF41, 0xD6FFE0),
    "cyan": (0x00E5FF, 0xD6F8FF),
    "magenta": (0xFF00E5, 0xFFD6F8),
}

#: Frames in a rain loop.
RAIN_FRAMES = 48

#: How long a drop takes to come round again, per frames-per-row speed.
#: Only cycles that divide the loop at that speed are offered, which is
#: what lets the last frame lead back into the first.
RAIN_CYCLES = {1: (12, 16, 24), 2: (12, 24)}

#: Rows lit behind the head. Two, not four: on a five-row strip a longer
#: tail never leaves a gap, and the rain reads as a curtain scrolling.
RAIN_TAIL = 2


def matrix_rain(colour: str = "green", frames: int = RAIN_FRAMES,
                seed: int = 7) -> list[Frame]:
    """Drops falling down the strip, the way the film does it.

    Each column runs on its own cycle, and every cycle divides the loop
    length, so the last frame leads back into the first with no jump.
    The head is near-white and the tail fades to black behind it.

    ``colour`` is one of :data:`RAIN_COLOURS`.
    """
    try:
        base, head = RAIN_COLOURS[colour]
    except KeyError:
        raise ValueError(
            f"no rain colour called {colour!r}; try "
            + ", ".join(sorted(RAIN_COLOURS))
        ) from None
    if frames < 1:
        raise ValueError("an animation needs a frame")

    rng = random.Random(seed)
    columns = []
    for _ in range(COLS):
        speed = rng.choice((1, 1, 2))          # most fall fast, some drift
        cycle = rng.choice(RAIN_CYCLES[speed])
        columns.append((speed, cycle, rng.randrange(cycle)))
    tail = RAIN_TAIL

    out: list[Frame] = []
    for n in range(frames):
        frame = Frame()
        for col, (speed, cycle, offset) in enumerate(columns):
            # Where this column's head is, counting from above the strip
            # so a drop enters from the top rather than appearing in it.
            position = ((n // speed + offset) % cycle) - 1
            for depth in range(tail + 1):
                row = position - depth
                if not 0 <= row < ROWS:
                    continue
                if depth == 0:
                    frame[col, row] = head
                else:
                    fade = (tail - depth + 1) / (tail + 1)
                    frame[col, row] = (
                        (int(((base >> 16) & 0xFF) * fade) << 16)
                        | (int(((base >> 8) & 0xFF) * fade) << 8)
                        | int((base & 0xFF) * fade)
                    )
        out.append(frame)
    return out


# -- the runner --------------------------------------------------------
#
# The browser's offline game, at 65x5. The vertical budget is the whole
# design: the ground takes the bottom row, the runner and the cacti are
# two rows tall above it, and that leaves exactly two rows of air -- so
# a jump clears a cactus by one row and no more.

#: The runner, two rows, with its legs swapped between frames.
RUNNER_FRAMES = (
    ((1, 0), (2, 0), (3, 0), (0, 1), (1, 1), (3, 1)),
    ((1, 0), (2, 0), (3, 0), (0, 1), (2, 1), (3, 1)),
)
RUNNER_WIDTH = 4

#: Cacti: a sprout and one with arms.
CACTI = (
    ((0, 0), (0, 1)),
    ((0, 0), (1, 0), (2, 0), (1, 1)),
)

RUNNER_COLOUR = 0xE8E8E8
CACTUS_COLOUR = 0x56B056
GROUND_COLOUR = 0x303030
PEBBLE_COLOUR = 0x585858
MOON_COLOUR = 0xC8C070
STAR_COLOUR = 0x404058

#: Where the runner stands, and how long the world is before it repeats.
RUNNER_COLUMN = 6
RUNNER_FRAMES_TOTAL = 96


def _jump_height(gap: int) -> int:
    """How high the runner is when a cactus is ``gap`` columns from its nose.

    Two rows of air, so the arc is short. It has to stay up until the
    cactus has passed the whole body, not just the nose: the runner is
    four columns long, so the window runs to minus its width.
    """
    # Full height for every column the body covers -- the runner is
    # four long and its nose leads, so that is gaps 0 through -3. Half
    # height only on the way in and out, where nothing is underneath.
    arc = {5: 1, 4: 2, 3: 2, 2: 2, 1: 2, 0: 2, -1: 2, -2: 2, -3: 2, -4: 1}
    return arc.get(gap, 0)


def dino_run(frames: int | None = None, seed: int = 3,
             world: int = RUNNER_FRAMES_TOTAL) -> list[Frame]:
    """The browser's runner, dodging cacti along the strip.

    The world is a loop ``world`` columns long that scrolls one column a
    frame, so frame ``world`` is frame 0 again. The runner stays in its
    column and jumps by where the next cactus is, which depends only on
    its position in that loop -- so the jumps repeat with it.

    ``frames`` defaults to one lap. Asking for more repeats the lap,
    which is how the loop itself gets tested.
    """
    if world < COLS:
        raise ValueError(f"the world has to be at least {COLS} columns")
    if frames is None:
        frames = world
    if frames < 1:
        raise ValueError("an animation needs a frame")

    rng = random.Random(seed)
    cacti: list[tuple[int, tuple[tuple[int, int], ...]]] = []
    at = rng.randrange(12, 20)
    while at < world - 10:
        cacti.append((at, CACTI[rng.randrange(len(CACTI))]))
        at += rng.randrange(14, 26)
    # Every column a cactus occupies, not just where it starts: the one
    # with arms is three wide, and the runner has to clear all of it.
    cactus_at = {(x + dx) % world for x, shape in cacti for dx, _ in shape}

    pebbles = {rng.randrange(world) for _ in range(world // 6)}
    stars = {rng.randrange(world) for _ in range(world // 12)}
    moon = rng.randrange(world)

    def lift_at(nose: int) -> int:
        """How high the runner is, given every cactus around it.

        Each one is scored on its own and the highest wins: taking only
        the nearest ahead lands the runner on the one it is still
        standing over, which is exactly the bug this replaces.
        """
        highest = 0
        for cx in cactus_at:
            ahead = (cx - nose) % world
            gap = ahead if ahead <= world // 2 else ahead - world
            highest = max(highest, _jump_height(gap))
        return highest

    out: list[Frame] = []
    for n in range(frames):
        frame = Frame()
        for col in range(COLS):
            x = (n + col) % world
            frame[col, ROWS - 1] = (PEBBLE_COLOUR if x in pebbles
                                    else GROUND_COLOUR)
            if x in stars:
                frame[col, 0] = STAR_COLOUR
            if x == moon:
                frame[col, 0] = MOON_COLOUR

        for cx, shape in cacti:
            col = (cx - n) % world
            if col >= COLS:
                continue
            for dx, dy in shape:
                if col + dx < COLS:
                    frame[col + dx, ROWS - 3 + dy] = CACTUS_COLOUR

        nose = (n + RUNNER_COLUMN + RUNNER_WIDTH - 1) % world
        lift = lift_at(nose)
        sprite = RUNNER_FRAMES[0 if lift else (n // 2) % len(RUNNER_FRAMES)]
        for dx, dy in sprite:
            col = RUNNER_COLUMN + dx
            row = ROWS - 3 + dy - lift
            if 0 <= col < COLS and 0 <= row < ROWS:
                frame[col, row] = RUNNER_COLOUR
        out.append(frame)
    return out


# -- the token meter ---------------------------------------------------

#: Bright and dim for each tool, in its own colour.
METER_COLOURS = {
    "claude": (0xFF7A1A, 0x3A1A00),
    "codex": (0x2E8BFF, 0x001A38),
}
#: What a tool with nothing running is drawn in.
METER_IDLE = (0x606060, 0x141414)

# The four Sessions views are loaded together and repeated in the uploaded
# animation, so changing views does not trigger another transfer.
SESSION_SECONDS_EACH = 3
METER_SECONDS_EACH = SESSION_SECONDS_EACH
SESSION_COLOURS = {
    "claude": (0xFF7A1A, 0x000000),
    "codex": (0x1A5FB4, 0x000000),
}
SESSION_FRAME_TARGET_MS = 1_000
SESSION_SEQUENCE = (
    ("claude", "5h"),
    ("claude", "7d"),
    ("codex", "5h"),
    ("codex", "7d"),
)


def session_timing(speed: int | None = None) -> tuple[int, int, float]:
    """Return playback speed, frames per view, and the matching view dwell."""
    play_speed = speed if speed is not None else speed_for_ms(
        SESSION_FRAME_TARGET_MS)
    frame_duration = frame_ms(play_speed)
    per_view = max(1, round(
        SESSION_SECONDS_EACH * 1000 / frame_duration))
    per_view = min(
        per_view, MAX_FRAMES_ANIMATION // len(SESSION_SEQUENCE))
    return play_speed, per_view, per_view * frame_duration


def session_percent(reading, window: str) -> float | None:
    """Used percentage for the short or long plan window."""
    if window == "5h":
        return reading.plan_percent
    if window == "7d":
        return reading.plan_long_percent
    raise ValueError("session window is '5h' or '7d'")


def session_screen(reading, window: str) -> Frame:
    """One quota screen with text followed by its service-coloured bar."""
    from . import font

    percent = session_percent(reading, window)
    frame = Frame()
    bright, dim = SESSION_COLOURS.get(reading.tool, (0xFFFFFF, 0x000000))
    amount = "--" if percent is None else f"{percent:.0f}%"
    label = f"{reading.tool.upper()} {window.upper()} {amount}"
    font.draw(frame, label, 1, bright)

    # Keep the full 3x5 lettering intact; the meter starts after the text.
    bar_start = 2 + font.width(label)
    bar_width = max(0, COLS - bar_start)
    filled = 0 if percent is None else round(bar_width * percent / 100.0)
    for col in range(bar_start, COLS):
        colour = bright if col - bar_start < filled else dim
        for row in range(ROWS):
            frame[col, row] = colour
    return frame


def session_animation(readings, brightness: int = 100,
                      speed: int | None = None) -> Animation:
    """Build one looping upload so view changes need no further transfers.

    The keyboard only holds a frame for the time encoded in the speed byte,
    so duplicate each view enough times to approach a three-second dwell.
    Use a one-second frame pace by default to keep the animation upload small.
    """
    by_tool = ({reading.tool: reading for reading in readings}
               if not isinstance(readings, dict) else readings)
    play_speed, per_view, _dwell_ms = session_timing(speed)

    from .usage import empty

    frames: list[Frame] = []
    for tool, window in SESSION_SEQUENCE:
        reading = by_tool.get(tool) or empty(tool)
        screen = session_screen(reading, window)
        frames.extend([screen] * per_view)
    return Animation(frames=frames, brightness=brightness, speed=play_speed)


def meter_screen(reading, label: str | None = None) -> Frame:
    """One tool's panel: the name as a gauge, then its numbers.

    The name doubles as the context bar -- it fills from the left in
    proportion to how much of the window is in use -- which is the only
    way to show a bar and a word in five rows. After it come the tokens
    the session has spent and, where the tool records it, how much of
    the plan's window has gone.
    """
    from . import font

    name = (label or reading.tool).upper()
    bright, dim = (METER_COLOURS.get(reading.tool, METER_IDLE)
                   if reading.live else METER_IDLE)

    frame = Frame()
    x = 1
    share = (reading.context_percent or 0) / 100.0
    font.draw(frame, name, x, bright,
              fill_to=x + round(font.width(name) * share), dim=dim)
    x += font.width(name) + 3

    from .usage import short

    parts = [short(reading.tokens) if reading.found else "--"]
    if reading.plan_percent is not None:
        parts.append(f"{reading.plan_percent:.0f}%")
    for part in parts:
        if x + font.width(part) > COLS:
            break
        x = font.draw(frame, part, x, bright) + 4
    return frame


def meter(readings, frames_each: int = 1) -> list[Frame]:
    """The tools in turn, one panel each.

    ``frames_each`` is how many frames a panel holds. One is enough
    when the firmware is playing slowly; more is how to stretch a panel
    to a few seconds on a keyboard that plays fast.
    """
    if frames_each < 1:
        raise ValueError("frames_each is 1 or more")
    if not readings:
        raise ValueError("nothing to show")
    frames: list[Frame] = []
    for reading in readings:
        screen = meter_screen(reading)
        frames.extend(Frame(screen.pixels) for _ in range(frames_each))
    return frames


#: The name of the effect that plays all the others.
OVERALL = "overall"


def overall(limit: int = MAX_FRAMES_ANIMATION,
            names: list[str] | None = None) -> list[Frame]:
    """Every effect in turn, inside a single upload.

    The keyboard holds 941 frames *in total*, so this is a showcase and
    not a schedule: each of the 25 effects gets an equal share of them,
    37 frames, and the whole thing loops without the host being
    involved at all. How long a share lasts is whatever the speed
    setting makes of 37 frames -- a couple of seconds at the default,
    and `pattern` prints the figure before it sends.

    Where an effect has more frames than its share, they are sampled
    evenly, so what shows is the whole of it played quickly rather than
    the first few seconds of it.
    """
    chosen = [name for name in (names or catalogue()) if name != OVERALL]
    if not chosen:
        raise ValueError("nothing to show")
    if limit < len(chosen):
        raise ValueError(f"{limit} frames cannot hold {len(chosen)} effects")

    share = limit // len(chosen)
    out: list[Frame] = []
    for name in chosen:
        frames = build(name)
        take = min(share, len(frames))
        if take == len(frames):
            out.extend(frames)
            continue
        for index in range(take):
            at = min(len(frames) - 1, round(index * len(frames) / take))
            out.append(frames[at])
    return out[:limit]


#: The animations this module generates, by name.
GENERATED: dict[str, str] = {
    "tetris": "Tetris — pieces falling in until the strip is a wall",
    "runner": "Runner — the browser's dinosaur, jumping cacti",
    "matrix-green": "Matrix rain, green",
    "matrix-cyan": "Matrix rain, cyan",
    "matrix-magenta": "Matrix rain, magenta",
}


@functools.lru_cache(maxsize=1)
def bundled() -> dict[str, dict]:
    """The shipped effects, by key, from their index.

    Empty rather than fatal when the folder is missing: the app runs
    from a checkout that has not been converted, and the generated
    animations do not depend on any of this.
    """
    index = EFFECTS_DIR / "index.json"
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {entry["key"]: entry for entry in data.get("effects", [])
            if isinstance(entry, dict) and entry.get("key")}


def load_bundled(key: str) -> list[Frame]:
    """Frames of one shipped effect."""
    entry = bundled().get(key)
    if entry is None:
        raise ValueError(f"no effect called {key!r}")
    raw = gzip.decompress((EFFECTS_DIR / entry["file"]).read_bytes())
    if len(raw) % FRAME_BYTES:
        raise ValueError(
            f"{entry['file']} is {len(raw)} bytes, not whole frames of "
            f"{FRAME_BYTES}"
        )
    return [Frame.decode(raw[at:at + FRAME_BYTES])
            for at in range(0, len(raw), FRAME_BYTES)]


def source_speed(name: str) -> int:
    """The speed setting that plays ``name`` at its own pace.

    The shipped effects are GIFs scaled down to the strip, and the
    index keeps both numbers that decide their pace: the delay the GIF
    asked for, and the stride the conversion thinned it by. Playing
    them at anything else is what made the strip and the program's
    preview disagree.

    Everything generated here is drawn frame by frame with no source to
    be faithful to, so it takes the default -- which is the same native
    rate the GIFs were authored at.
    """
    entry = bundled().get(name)
    if entry is None:
        return default_playback_speed()
    return speed_for_source(entry.get("delay_ms") or 0,
                            entry.get("stride") or 1)


def catalogue(with_overall: bool = False) -> dict[str, str]:
    """Everything the UI and the CLI offer: generated first, then shipped.

    ``overall`` is left out by default because it is built *from* this
    list, and including it in its own ingredients is how you get a
    recursion rather than an animation.
    """
    out = dict(GENERATED)
    for key, entry in bundled().items():
        out[key] = f"{entry['title']} — {entry['frames']} frames"
    if with_overall:
        out[OVERALL] = "Overall — every effect in turn, in one upload"
    return out


#: Kept for callers that only want what is generated here.
BUILT_INS = GENERATED


def build(name: str) -> list[Frame]:
    """Frames for anything in :func:`catalogue`."""
    if name == OVERALL:
        return overall()
    if name == "tetris":
        return tetris_fill()
    if name == "runner":
        return dino_run()
    if name.startswith("matrix-") and name.split("-", 1)[1] in RAIN_COLOURS:
        return matrix_rain(name.split("-", 1)[1])
    if name in bundled():
        return load_bundled(name)
    raise ValueError(
        f"no effect called {name!r}; try " + ", ".join(sorted(catalogue()))
    )
