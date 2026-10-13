"""The music screen: the host listens, the keyboard is told colours.

No new keyboard command appears here, and that is the point. The
keyboard has no microphone and no opinion about sound; it takes per-key
colours, the same ones the Home screen sends. So everything between the
speakers and those colours is the host's work, and all of it is in this
module: a loopback recorder, an FFT, and a mapping from bands to keys.

The recorder is a subprocess rather than a Python audio binding. This
machine has neither ``sounddevice`` nor ``pyaudio``, and asking the user
to install a wheel to see a light show is a poor trade when PipeWire and
PulseAudio both ship a command that writes raw frames to a pipe. It also
keeps the failure mode simple: a missing recorder is a missing
executable, not an import that takes the whole app down with it.
"""

from __future__ import annotations

import array
import atexit
import functools
import math
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .. import layout as kb_layout
from .. import store as scheme_store
from ..gui import ACCENT, KeyboardView, caption, ghost_button, primary_button
from .common import _card, _combo, _heading, _list, _note

try:                                                  # pragma: no cover
    import numpy as _np
except ImportError:                                   # pragma: no cover
    # Not a dependency of this app, so the band maths has a pure-Python
    # path. See ``_goertzel_bands``.
    _np = None


# ----------------------------------------------------------------------
# 5. Music -- the listening
# ----------------------------------------------------------------------

#: 48 kHz is what PipeWire runs natively, so asking for it keeps a
#: resampler out of the path for the sake of a visualiser.
SAMPLE_RATE = 48000

#: 1024 samples is 21 ms, chosen for latency: much longer and the
#: lights are visibly behind the music.
#:
#: The price is paid at the bottom of the range. 21 ms resolves about
#: 47 Hz, and the bands below 175 Hz are narrower than that, so a bass
#: note leaks across all of them and lights the left end of the board as
#: a block rather than one column. That is spectral leakage, not a bug
#: in the mapping, and it cannot be fixed without a longer window --
#: which would cost exactly the responsiveness the screen is for.
FRAME = 1024

#: Bands, and the range they cover. The top is 12 kHz rather than
#: Nyquist because the two octaves above it hold cymbals and nothing
#: else, and they would waste a third of the board.
BANDS = 24
LOW_HZ, HIGH_HZ = 40.0, 12000.0

#: Anything quieter than this counts as silence. Music mastered for
#: listening sits around -20 dBFS, so a floor much lower than this
#: spends most of the meter on room tone and hiss.
FLOOR_DB = -62.0

#: The meter's own colours, lifted out so the board and the bars cannot
#: disagree about what a level looks like.
METER_GREEN = QColor("#3ddc84")
METER_AMBER = QColor("#ffc46b")
METER_RED = QColor("#ff6b6b")

#: The visualisation modes that are ramps, and the three stops of each.
#: The vendor's visualisation mode dropdown names its modes after their colours
#: -- the one its manual shows is green/yellow/red, "green/yellow/red" --
#: so a mode here is a ramp read by level: the colour of a band says how
#: loud that band is, which is what makes the board a meter rather than
#: a light show.
RAMPS = (
    ("Green / yellow / red", (METER_GREEN, METER_AMBER, METER_RED)),
    ("Blue / cyan / white",
     (QColor("#2b6bff"), QColor("#3ddcff"), QColor("#eaf7ff"))),
    ("Violet / pink / white",
     (QColor("#7a3dff"), QColor("#ff6bd6"), QColor("#ffe8ff"))),
    ("Amber / orange / red",
     (QColor("#ffd166"), QColor("#ff9f1c"), QColor("#ff4d4d"))),
)

#: Where a ramp leaves one stop for the next. Two thirds of the way up
#: and then nearly at the top: a band that is merely playing stays on
#: the first colour, and the last is kept for peaks, which is the only
#: thing that makes the top of a ramp mean anything.
RAMP_MID, RAMP_TOP = 0.6, 0.85

#: The dropdown's order: every ramp, and then the two modes that are not
#: ramps -- one colour whatever the level, and hue by band.
MODES = tuple(name for name, _ in RAMPS) + ("Single colour", "Spectrum")
GREEN_AMBER_RED = 0
SINGLE_COLOUR, SPECTRUM = len(RAMPS), len(RAMPS) + 1

#: What the keys that are not standing in a bar show. "None" is the
#: vendor's own default (none) and is the honest one on this board: the
#: per-key path is slow enough that painting a background costs bars.
BACKGROUNDS = ("None", "Dim static", "The current effect")
NOTHING, DIM_STATIC, RUNNING_EFFECT = 0, 1, 2

#: One key colour is nine 15 ms transfers -- about 140 ms measured on
#: hardware -- so the per-key path carries roughly seven keys a second
#: and the board is 69 of them.
#:
#: These two put the send rate at about thirteen keys a second: a little
#: over what the wire does, so it never sits idle, and nowhere near the
#: display rate. Emitting at the display rate instead would be worse
#: than useless -- the worker coalesces by key, so all it would achieve
#: is a permanently full queue of colours that were stale before they
#: were sent.
SEND_INTERVAL = 150
KEY_BUDGET = 2

#: How far a channel has to move before a key is worth re-sending. A
#: band wobbling by one or two counts would otherwise eat the budget and
#: leave no room for a key that actually changed colour.
COLOUR_STEP = 28


def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def capture_command(rate: int = SAMPLE_RATE) -> tuple[str, list[str]] | None:
    """The first loopback recorder on PATH, and how to ask it for one.

    Both of these record a sink *monitor* rather than a microphone,
    which is the whole point of the screen: it should follow what the
    machine is playing, not what the room sounds like.

    ``parec`` is tried first because it is the one that exists on plain
    PulseAudio hosts as well as on PipeWire's compatibility layer, and
    it names the monitor outright. PipeWire's own recorder takes a
    property instead and resolves the default sink itself, which has the
    nicer property of following the default if it moves mid-song.

    ``None`` means neither is installed, which is not an error: the
    screen degrades to a stand-in rather than refusing to open.
    """
    if shutil.which("parec"):
        return "parec", [
            "parec", "--device=@DEFAULT_SINK@.monitor",
            "--format=s16le", f"--rate={rate}", "--channels=1",
            "--latency-msec=20",
        ]
    if shutil.which("pw-record"):
        return "pw-record", [
            "pw-record", "--properties", "stream.capture.sink=true",
            f"--rate={rate}", "--channels=1", "--format", "s16",
            "--latency", "20ms", "--raw", "-",
        ]
    return None


def decode(raw: bytes):
    """Signed 16-bit little-endian frames, as floats in -1..1.

    Both recorders are asked for ``s16le`` explicitly, so the byte order
    is known rather than the host's; ``array`` reads native order, hence
    the swap on the machines where those differ.
    """
    if _np is not None:
        return _np.frombuffer(raw, dtype="<i2").astype(float) / 32768.0
    block = array.array("h")
    block.frombytes(raw)
    if sys.byteorder == "big":
        block.byteswap()
    return [value / 32768.0 for value in block]


def band_edges(count: int = BANDS, low: float = LOW_HZ,
               high: float = HIGH_HZ) -> list[float]:
    """``count`` + 1 band edges, spaced by octave rather than by hertz.

    Pitch is logarithmic and a linear split does not survive contact
    with music: it puts three quarters of the bars above 5 kHz, where
    there is almost nothing, and squeezes every bass note into the
    first bar.
    """
    step = (math.log(high) - math.log(low)) / count
    return [math.exp(math.log(low) + step * i) for i in range(count + 1)]


@functools.lru_cache(maxsize=16)
def _fft_plan(size: int, rate: int, edges: tuple[float, ...]):
    """Precompute the fixed work for a given audio format and band map."""
    freqs = _np.fft.rfftfreq(size, 1.0 / rate)
    bands = []
    for low, high in zip(edges, edges[1:]):
        start = int(_np.searchsorted(freqs, low, side="left"))
        stop = int(_np.searchsorted(freqs, high, side="left"))
        nearest = int(_np.argmin(_np.abs(freqs - math.sqrt(low * high))))
        bands.append((start, stop, nearest))
    window = _np.hanning(size)
    return window, max(window.sum() / 2.0, 1e-9), tuple(bands)


def _fft_bands(samples, rate: int, edges: list[float]) -> list[float]:
    block = _np.asarray(samples, dtype=float)
    window, scale, bands = _fft_plan(
        block.size, rate, tuple(float(edge) for edge in edges)
    )
    # Divided by the window's own coherent gain rather than by the frame
    # size, so a full-scale sinusoid reads 1.0 whatever the frame length
    # and whatever window is used. That is what lets the next stage talk
    # in dBFS instead of in arbitrary units.
    spectrum = _np.abs(_np.fft.rfft(block * window)) / scale
    out = []
    for start, stop, nearest in bands:
        if start < stop:
            # The peak, not the mean: the top bands are a third of an
            # octave wide, and averaging would bury a loud snare in the
            # empty bins beside it.
            out.append(float(spectrum[start:stop].max()))
        else:
            # The bottom few bands are narrower than one bin, so no bin
            # centre falls inside them. Reading the nearest bin instead
            # of zero is the difference between a bass line that lights
            # the left of the board and one that never lights at all.
            out.append(float(spectrum[nearest]))
    return out


#: The shortest window the fallback filter bank will use. Below this the
#: estimate is all variance and no signal, so the top bands settle for
#: being wider than their filter rather than shorter than this.
MIN_WINDOW = 48


def _goertzel_bands(samples, rate: int, edges: list[float]) -> list[float]:
    """A filter bank, one Goertzel per band, for hosts without numpy.

    A full DFT in Python cannot be run forty times a second, but a
    Goertzel is a two-tap recurrence evaluated at one frequency, so a
    bank of them costs only the frequencies actually wanted -- the
    geometric centre of each band.

    The subtlety is that a Goertzel's passband is as narrow as its
    window is long, which is the opposite of what a log-spaced bank
    needs: a 1024-sample window is 47 Hz wide and would read almost
    nothing from a band a thousand hertz across unless the note sat
    exactly on the centre. So each band gets the window its own width
    asks for -- long and sharp at the bottom, short and broad at the
    top -- which also makes the bank cheap, because it is the wide bands
    that would otherwise dominate the cost.
    """
    block = list(samples)
    size = len(block)
    if size == 0:
        return [0.0] * (len(edges) - 1)
    out = []
    for low, high in zip(edges, edges[1:]):
        width = max(high - low, 1e-9)
        taken = min(size, max(MIN_WINDOW, int(rate / width)))
        # The tail of the frame, so every band describes the same
        # instant -- the newest audio -- rather than the bass lagging
        # the treble by the length of the frame.
        window = block[size - taken:]
        coeff = 2.0 * math.cos(
            2.0 * math.pi * math.sqrt(low * high) / rate)
        first = second = 0.0
        for value in window:
            first, second = coeff * first - second + value, first
        power = first * first + second * second - coeff * first * second
        out.append(2.0 * math.sqrt(max(power, 0.0)) / taken)
    return out


def spectrum_bands(samples, rate: int = SAMPLE_RATE,
                   edges: list[float] | None = None) -> list[float]:
    """Band magnitudes for one frame, 0.0 to 1.0 of full scale."""
    edges = band_edges() if edges is None else edges
    if len(samples) == 0:
        return [0.0] * (len(edges) - 1)
    if _np is not None:
        return _fft_bands(samples, rate, edges)
    return _goertzel_bands(samples, rate, edges)


def gain_from_amplitude(amplitude: int) -> float:
    """The vendor's 1..100 amplitude slider, as an input gain.

    Centred so that 50 is unity: the control is a gain on a signal whose
    loudness the app does not get to choose, and the useful range turns
    out to be "a bit less" to "a lot more".
    """
    return amplitude / 50.0


def levels_from_bands(bands: list[float], gain: float = 1.0,
                      floor_db: float = FLOOR_DB) -> list[float]:
    """Band magnitudes as 0..1 display levels, on a decibel scale.

    Linear magnitude is unusable here. Hearing is logarithmic, so a
    linear meter spends its whole top half on the one loudest band and
    shows everything else as flat -- which on this screen means one key
    column moving and sixty-eight keys dark.
    """
    out = []
    for value in bands:
        db = 20.0 * math.log10(max(value, 1e-9))
        out.append(_clamp((db - floor_db) / -floor_db * gain))
    return out


class Smoother:
    """Per-band levels that rise quickly and fall slowly.

    Following the frames exactly strobes: consecutive FFT frames of the
    same note differ enough that the board flickers rather than pulses.
    Asymmetric smoothing is what a hardware VU meter does and it is what
    makes a beat read as a beat -- the attack has to arrive on time, but
    the fall is what the eye actually sees, so it is given room.
    """

    def __init__(self, count: int = BANDS, attack: float = 0.55,
                 decay: float = 0.14):
        self.values = [0.0] * count
        self.attack = attack
        self.decay = decay

    def push(self, levels: list[float]) -> list[float]:
        for i, target in enumerate(levels[:len(self.values)]):
            held = self.values[i]
            rate = self.attack if target > held else self.decay
            self.values[i] = _clamp(held + (target - held) * rate)
        return self.values

    def clear(self) -> None:
        self.values = [0.0] * len(self.values)


class Capture:
    """A loopback recorder, read on a thread of its own.

    The recorder writes raw frames to a pipe and a reader thread turns
    them into blocks on a bounded queue, which the UI timer drains. The
    bound matters: if the UI falls behind, the frames waiting in the
    queue are already stale, so the oldest is dropped rather than the
    recorder blocked. A visualiser wants the newest frame, not all of
    them.

    Stopping has to kill the subprocess. It is a child of the app, so it
    would otherwise outlive it and sit there recording the speakers
    forever -- hence the ``atexit`` registration, which covers the case
    where the window goes away without anyone pressing Stop.
    """

    #: How many frames may wait before the oldest is dropped. Eight is
    #: about 170 ms, enough to ride out a slow repaint and short enough
    #: that nothing stale ever reaches the board.
    BACKLOG = 8

    def __init__(self, rate: int = SAMPLE_RATE, frame: int = FRAME):
        self.rate = rate
        self.frame = frame
        self.route = ""
        self.reason = ""
        self.frames: queue.Queue = queue.Queue(maxsize=self.BACKLOG)
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> bool:
        """Spawn the recorder. False if there is nothing to spawn."""
        found = capture_command(self.rate)
        if found is None:
            self.reason = "no loopback recorder (parec or pw-record)"
            return False
        self.route, argv = found
        try:
            self._proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE,
                # Discarded rather than piped: nothing reads a second
                # pipe while the first is being drained, and a recorder
                # that chattered would eventually fill it and wedge.
                # The exit code is diagnostic enough.
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.reason = f"{self.route} would not start: {exc}"
            return False
        self._stopping.clear()
        self._reader = threading.Thread(
            target=self._read, name="music-capture", daemon=True)
        self._reader.start()
        atexit.register(self.stop)
        return True

    def _read(self) -> None:
        width = 2 * self.frame
        stream = self._proc.stdout
        while not self._stopping.is_set():
            try:
                raw = stream.read(width)
            except (OSError, ValueError):
                break
            if raw is None or len(raw) < width:
                break
            self._offer(decode(raw))

    def _offer(self, block) -> None:
        try:
            self.frames.put_nowait(block)
            return
        except queue.Full:
            pass
        try:
            self.frames.get_nowait()
            self.frames.put_nowait(block)
        except (queue.Empty, queue.Full):        # pragma: no cover
            pass

    def latest(self):
        """The newest frame waiting, or None. Drops the backlog."""
        block = None
        while True:
            try:
                block = self.frames.get_nowait()
            except queue.Empty:
                return block

    def alive(self) -> bool:
        """False once the recorder has ended, however it ended."""
        return self._reader is not None and self._reader.is_alive()

    def ended(self) -> str:
        """Why the recorder stopped, as far as the exit code says."""
        if self._proc is None or self._proc.poll() is None:
            return ""
        return f"{self.route} ended (exit {self._proc.returncode})"

    def stop(self) -> None:
        self._stopping.set()
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:    # pragma: no cover
                proc.kill()
                proc.wait(timeout=1.0)
        if proc is not None and proc.stdout is not None:
            # Closed after the wait, so the reader's blocking read ends
            # on the recorder's own EOF rather than on a closed handle.
            proc.stdout.close()
        self._reader = None
        atexit.unregister(self.stop)


# ----------------------------------------------------------------------
# 5. Music -- the mapping
# ----------------------------------------------------------------------
#: The leftmost and rightmost key centres. The bands are spread between
#: these rather than across the 800-wide artwork canvas, because the
#: keys only occupy x=40 to x=780 of it: measured against the canvas,
#: the lowest and highest bands fall in the margins and reach no key at
#: all, so the bass band and the top of the treble never light.
_CENTRES = tuple(key.x + key.w / 2.0 for key in kb_layout.KEYS)
LEFTMOST, RIGHTMOST = min(_CENTRES), max(_CENTRES)


def key_band(key: kb_layout.Key, count: int = BANDS) -> int:
    """Which band a key belongs to, by where it sits on the board.

    Left to right, by the key's centre rather than its left edge -- the
    space bar spans a quarter of the board, and its left edge would file
    the whole thing under the bass sitting over by the Z row.
    """
    span = RIGHTMOST - LEFTMOST
    across = (key.x + key.w / 2.0 - LEFTMOST) / span
    return min(count - 1, max(0, int(across * count)))


def key_rows() -> list[int]:
    """The board's rows, bottom first.

    Read off the geometry rather than written down, because the board is
    not a neat grid: the SWITCH key sits alone in a row of its own above
    the function row, so a hardcoded row count would either lose it or
    put everything above the home row in the wrong place.
    """
    return sorted({key.y for key in kb_layout.KEYS}, reverse=True)


def key_brightness(level: float, row: int, rows: int) -> float:
    """How lit a key in ``row`` is when its band stands at ``level``.

    The board is read as a bar graph standing on the space bar: a band
    fills its column from the bottom up. The key at the top of the bar
    is part-lit, which is the only thing stopping a six-row board from
    stepping between six brightnesses and nothing else.
    """
    return _clamp(level * rows - row)


def band_colour(scheme: int, band: int, count: int, level: float,
                base: QColor) -> QColor:
    """The hue for one band, under the chosen visualisation mode."""
    if scheme == SINGLE_COLOUR:
        return QColor(base)
    if scheme == SPECTRUM:
        # Hue follows the band, not the level. The point of this mode
        # is to show *where* the energy is; a level-driven hue would say
        # the same thing as the bar height and hide the spectrum.
        colour = QColor()
        colour.setHsv(int(300 * band / max(count - 1, 1)), 255, 255)
        return colour
    # An index from a stored scheme, or from a combo that has since
    # lost an entry, is no reason to refuse to draw: the first ramp is
    # the vendor's own and is what the screen opens on anyway.
    low, mid, high = RAMPS[scheme if 0 <= scheme < len(RAMPS) else 0][1]
    if level < RAMP_MID:
        return QColor(low)
    return QColor(mid) if level < RAMP_TOP else QColor(high)


def dim(colour: QColor, amount: float) -> QColor:
    """``colour`` scaled towards black, for a part-lit key."""
    amount = _clamp(amount)
    return QColor(int(colour.red() * amount),
                  int(colour.green() * amount),
                  int(colour.blue() * amount))


def colour_gap(first: QColor, second: QColor) -> int:
    """The largest channel difference between two colours."""
    return max(abs(first.red() - second.red()),
               abs(first.green() - second.green()),
               abs(first.blue() - second.blue()))


class LevelBar(QWidget):
    """A meter, showing the bands the board is being driven from.

    It animates itself when there is nothing to listen to, because a
    screen whose meter is a flat line looks broken rather than idle.
    When the recorder is running the page pushes real levels in and the
    animation is switched off.
    """

    def __init__(self, bars: int = BANDS, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self._phase = 0.0
        #: Which scheme to colour the bars by, so the meter and the
        #: board never disagree about what a level looks like.
        self.scheme = GREEN_AMBER_RED
        self.base = QColor(ACCENT)
        # A frozen frame rather than a flat line: at rest the screen
        # should still show the shape of the thing it would drive.
        self.levels = [0.0] * bars
        self._tick()
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def run(self, on: bool) -> None:
        self._timer.start(50) if on else self._timer.stop()
        if not on:
            self.levels = [0.0] * len(self.levels)
            self.update()

    def show_levels(self, levels: list[float]) -> None:
        """Draw measured levels, as many as there are bars."""
        self.levels = list(levels[:len(self.levels)])
        self.update()

    def _tick(self) -> None:
        self._phase += 0.25
        self.levels = [
            abs(math.sin(self._phase + i * 0.4)) * (0.35 + 0.65 * (i % 5) / 4)
            for i in range(len(self.levels))
        ]
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#061634"))
        n = len(self.levels)
        w = self.width() / n
        for i, level in enumerate(self.levels):
            h = max(2.0, level * (self.height() - 6))
            colour = band_colour(self.scheme, i, n, level, self.base)
            p.fillRect(int(i * w + 2), int(self.height() - h - 3),
                       int(w - 3), int(h), colour)


# ----------------------------------------------------------------------
# 5. Music -- the screen
# ----------------------------------------------------------------------
@dataclass
class MusicSettings:
    """The four controls the vendor's panel carries, for one scheme.

    They live here and not in ``store`` because a stored scheme has
    nowhere to put them: ``store.Scheme`` holds a keymap, a painted
    layout and macros, and nothing a visualiser could use. So these last
    as long as the app does and no longer, which the screen says on its
    face rather than leaving someone to find out at the next start.

    The defaults are the ones the controls open on, so a scheme nobody
    has touched and a scheme that has just been made look the same.
    """

    mode: int = GREEN_AMBER_RED
    amplitude: int = 60
    background: int = NOTHING
    brightness: int = 20
    colour: QColor = field(default_factory=lambda: QColor(ACCENT))


class MusicPage(QWidget):
    """Sound into per-key colours, with the vendor's own controls.

    The listening is the host's and so is the arithmetic: a loopback
    recorder, an FFT, bands mapped across the board by key position, and
    the result sent down the same per-key colour path the Home screen
    uses. No new keyboard command is involved.

    What that path cannot do is keep up. A key colour is nine paced
    transfers, so about seven keys a second reach the keyboard, and the
    board is sixty-nine. The meter and the preview run at full rate off
    the same numbers, and only the keys whose colour has really moved
    are sent -- biggest change first. The lights therefore follow the
    music in broad strokes while the screen follows it exactly, and that
    is a property of the protocol rather than of this screen.

    The screen is laid out as the vendor's Music Rhythm is: the scheme list
    down the left, the board in the middle as a preview lit by the
    music, and the panel underneath it in two columns -- a visualisation
    mode and its amplitude, a background and its brightness.
    """

    apply_key = Signal(int, object)          # light_index, QColor
    status = Signal(str)
    #: The scheme the list here has just made the active one. The same
    #: signal the profiles screen carries, and for the same reason: the
    #: screens share one store, so a switch changes what every one of
    #: them is looking at and they have to be told to look again.
    scheme_changed = Signal(object)

    #: 25 ms is a touch faster than the 21 ms frames the recorder
    #: produces, so the drain is always a little ahead of the queue and
    #: the backlog stays at nothing.
    INTERVAL = 25

    #: The board is a preview here, not something to be painted on, and
    #: the vendor's panel sits underneath it -- at the size the Custom
    #: screen draws the board there would be no room for the panel.
    #:
    #: 0.72 rather than something roomier because this screen is the
    #: tallest of the rail: two notes, the preview, the meter and a
    #: two-column panel. At 0.85 the column wanted about 870px, more
    #: than the window's own minimum of 802, and a layout given less
    #: than it asks for does not refuse -- it lays the next widget over
    #: the one before, which put the "Levels" caption across the bottom
    #: row of keys. Everything below the board is a fixed height, so the
    #: board is the only part that can give.
    PREVIEW_SCALE = 0.72

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = scheme_store.shared()
        self._capture: Capture | None = None
        self._smooth = Smoother(BANDS)
        #: What the keyboard has been told, so only changes are re-sent.
        self._sent: dict[int, QColor] = {}
        #: The latest frame's colours, and which of them are bars. The
        #: display and the wire run at different rates, so the painting
        #: leaves its result here and the sender takes what it can.
        self._wire: dict[int, QColor] = {}
        self._lit: set[int] = set()
        self._colour = QColor(ACCENT)
        self._edges = band_edges(BANDS)
        #: Settings per scheme, by name -- see :class:`MusicSettings`.
        #: Keyed by name rather than by the scheme object because a
        #: dataclass with ``eq`` is unhashable, so a rename moves the
        #: entry and a delete drops it.
        self._settings: dict[str, MusicSettings] = {}
        #: Whose settings the controls are currently showing, so a
        #: control that moves writes into the right scheme.
        self._scheme_name = ""

        rows = key_rows()
        self._rows = len(rows)
        self._row_of = {k.light_index: rows.index(k.y)
                        for k in kb_layout.KEYS}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("Music mode"))
        # One note rather than two: this is the tallest screen on the
        # rail, and a second paragraph here pushed the column past what
        # the window's minimum grants, which a Qt layout answers by
        # drawing the next widget over the one before.
        outer.addWidget(_note(
            "The listening is local and needs no new command: the host "
            "records its own output and sends colours through the "
            "per-key path the Home screen uses. That path is the limit "
            "— one key is about 140 ms on the wire — so the keyboard "
            "follows the music in broad strokes while the meter and the "
            "preview follow it exactly. The manual's conditions hold "
            "too: wired mode, because the per-key colour command only "
            "travels over USB, and something actually playing, because "
            "what is recorded is this machine's own output. The "
            "keyboard's music rhythm and the dot-matrix strip's own are "
            "mutually exclusive: only one of the two shows at a time."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_schemes(), 0, Qt.AlignTop)

        self.meter = LevelBar()
        self.board = KeyboardView(self.PREVIEW_SCALE)
        # The board carries no labels of its own for what it is showing,
        # so the tooltip says which band a key stands in and what is in
        # that band -- the only way to read a column off the preview.
        self.board.hover_text = self._band_of

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # A clock of its own, because the screen can show forty frames a
        # second and the keyboard cannot take them.
        self._sender = QTimer(self)
        self._sender.timeout.connect(self._send)

        # The board is a fixed size and the card is not, so the two are
        # put in a column of their own between stretches: that centres
        # the board the way the vendor's preview sits centred, and it
        # takes the meter to the board's own width, which is what makes
        # a bar line up with the keys it is driving.
        stack = QVBoxLayout()
        stack.setSpacing(9)
        stack.addWidget(caption("Preview"))
        stack.addWidget(self.board)
        stack.addWidget(caption("Levels"))
        stack.addWidget(self.meter)
        middle = QHBoxLayout()
        middle.addStretch()
        middle.addLayout(stack)
        middle.addStretch()

        centre = QVBoxLayout()
        centre.setSpacing(12)
        centre.addWidget(_card(middle))
        centre.addWidget(self._build_settings())
        centre.addStretch()
        body.addLayout(centre, 1)

        outer.addLayout(body, 1)
        outer.addStretch()

        # Last, because it fills the controls built above from whichever
        # scheme the store has active.
        self._refresh_schemes()

    # -- the scheme list ------------------------------------------------
    def _build_schemes(self):
        """The list down the left, with the vendor's three buttons.

        Its list is the app's one list of configuration schemes, the
        same one the profiles screen owns: the vendor draws it on every
        lighting screen and marks the scheme in use with a dot, so
        choosing one here is using one, with no second step.

        Starting and stopping is here too, under the list, because the
        vendor's panel has no such control to copy -- its screen listens
        the whole time it is open, and a recorder that ran whenever
        somebody looked at this page is not a thing to do quietly.
        """
        self.schemes = _list()
        self.schemes.currentRowChanged.connect(self._chose)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(primary_button("New", self._add_scheme))
        row.addWidget(ghost_button("Rename", self._rename_scheme))
        row.addWidget(ghost_button("Delete", self._delete_scheme))
        row.addStretch()

        self.route = caption("")
        self.route.setWordWrap(True)
        self._describe_route()

        actions = QHBoxLayout()
        self.start = primary_button("Start listening", self._start)
        actions.addWidget(self.start)
        actions.addWidget(ghost_button("Stop", self._stop))
        actions.addStretch()

        kept = caption(
            "A scheme's name and the rest of its contents are kept in "
            "the scheme file. The settings on this screen are not: a "
            "stored scheme has nowhere to put them, so they last until "
            "the app closes.")
        # The card is a fixed width, and the shared caption does not
        # wrap on its own -- without this the sentence is simply cut.
        kept.setWordWrap(True)

        card = _card(caption("Scheme list"), self.schemes, row, kept,
                     caption("Listening"), actions, self.route)
        card.setFixedWidth(280)
        return card

    def _refresh_schemes(self, select: int | None = None) -> None:
        """Redraw the list, and show whichever scheme is now in use.

        Rebuilt whole rather than patched, as the profiles screen does
        it: the list is five rows and the marks move whenever the active
        scheme does, including when another screen moved it.
        """
        row = self.store.active if select is None else select
        # The rebuild drags the current row through 0 and -1 on its way,
        # and each of those would read as a choice nobody made.
        self.schemes.blockSignals(True)
        self.schemes.clear()
        for index, scheme in enumerate(self.store.schemes):
            mark = "●" if index == self.store.active else "○"
            self.schemes.addItem(f"{mark}  {scheme.name}")
        self.schemes.setCurrentRow(
            max(0, min(row, len(self.store.schemes) - 1)))
        self.schemes.blockSignals(False)
        self._follow_active()

    def _follow_active(self) -> None:
        """Put the active scheme's settings into the controls."""
        name = self.store.current.name
        if name == self._scheme_name:
            return
        self._scheme_name = name
        self._load_settings(self._settings_of(name))

    def _chose(self, row: int) -> None:
        """Selecting a scheme is using it, which is the vendor's list."""
        if not 0 <= row < len(self.store.schemes) or row == self.store.active:
            return
        self.store.active = row
        self._persist()
        self._refresh_schemes(row)
        self.status.emit(f"using {self.store.current.name}")
        self.scheme_changed.emit(self.store.current)

    def _add_scheme(self) -> None:
        name, ok = QInputDialog.getText(self, "New scheme", "Name")
        name = name.strip() if ok else ""
        if not name:
            return
        scheme = self.store.add(name)
        self.store.active = len(self.store.schemes) - 1
        self._persist()
        self._refresh_schemes()
        if scheme.name == name:
            self.status.emit(f"added {scheme.name}")
        else:
            # The store renames round a collision rather than refusing
            # one, which is right for adding and the wrong thing to do
            # without saying so.
            self.status.emit(f"added {scheme.name} — {name} was taken")
        self.scheme_changed.emit(self.store.current)

    def _rename_scheme(self) -> None:
        row = self.schemes.currentRow()
        if not 0 <= row < len(self.store.schemes):
            return
        scheme = self.store.schemes[row]
        name, ok = QInputDialog.getText(
            self, "Rename", "Name", text=scheme.name)
        name = name.strip() if ok else ""
        if not name or name == scheme.name:
            return
        if name in [s.name for i, s in enumerate(self.store.schemes)
                    if i != row]:
            # Refused rather than renamed round the collision: somebody
            # typed this name on purpose, so quietly storing a different
            # one answers a question they did not ask.
            self.status.emit(f"there is already a scheme called {name}")
            return
        was, scheme.name = scheme.name, name
        held = self._settings.pop(was, None)
        if held is not None:
            self._settings[name] = held
        if self._scheme_name == was:
            self._scheme_name = name
        self._persist()
        self._refresh_schemes(row)
        self.status.emit(f"renamed to {name}")

    def _delete_scheme(self) -> None:
        row = self.schemes.currentRow()
        if not 0 <= row < len(self.store.schemes):
            return
        if len(self.store.schemes) <= 1:
            # The store refuses this as well; refusing it here keeps the
            # answer in the status bar instead of a dialog to dismiss.
            self.status.emit("there has to be one scheme")
            return
        scheme = self.store.schemes[row]
        if (scheme.keymap or scheme.lighting or scheme.macros) \
                and not self._confirm(
                    "Delete this scheme?",
                    f"{scheme.name} holds {len(scheme.keymap)} remapped "
                    f"keys, {len(scheme.lighting)} painted and "
                    f"{len(scheme.macros)} macros.\n\nThe keyboard keeps "
                    f"no copy of a scheme, so this is the only one."):
            return
        gone = scheme.name
        self.store.remove(row)
        self._settings.pop(gone, None)
        self._persist()
        self._refresh_schemes()
        self.status.emit(
            f"deleted {gone} — now using {self.store.current.name}")
        self.scheme_changed.emit(self.store.current)

    def _confirm(self, title: str, question: str) -> bool:
        """Ask before something there is no way back from.

        A method of its own rather than the call written inline, so a
        test can answer it: an unanswered modal in a test run is a hang,
        which is worse to read than a failure.
        """
        return QMessageBox.question(
            self, title, question,
            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

    def _persist(self) -> None:
        """Write the scheme file, and say so if it will not be written.

        A failed save leaves the change on screen, so the message is the
        only warning that the next start will not have it.
        """
        try:
            self.store.save()
        except OSError as exc:
            self.status.emit(f"could not save the schemes: {exc}")

    def showEvent(self, event) -> None:
        # Another screen can add, rename or switch a scheme while this
        # one is out of sight, and the list is the only thing here that
        # would then be wrong.
        super().showEvent(event)
        self._refresh_schemes()

    # -- the settings panel ---------------------------------------------
    def _build_settings(self):
        """The vendor's panel: two columns under the preview.

        Left is what the music looks like -- the ramp and how hard the
        levels are driven into it. Right is what everything else looks
        like while it plays.
        """
        # The controls open on what a scheme nobody has touched holds,
        # so the two cannot drift apart.
        opening = MusicSettings()

        self.mode = _combo(list(MODES))
        self.mode.currentIndexChanged.connect(self._mode_changed)

        self.swatch = QLabel()
        self.swatch.setFixedHeight(22)
        self._show_swatch()
        colour_row = QHBoxLayout()
        colour_row.addWidget(self.swatch, 1)
        colour_row.addWidget(ghost_button("Pick…", self._pick_colour))

        self.amplitude = QSlider(Qt.Horizontal)
        self.amplitude.setRange(1, 100)
        self.amplitude.setValue(opening.amplitude)
        self.amplitude.setToolTip(
            "A gain on the levels before they are drawn, centred so "
            "that 50 is the signal as it arrived.")
        self.amplitude.valueChanged.connect(self._value_changed)
        # Under the slider and centred, where the vendor puts its own
        # readout: a gain with no number on it is a control nobody can
        # come back to.
        self.amplitude_value = caption("")
        self.amplitude_value.setAlignment(Qt.AlignCenter)

        self.background = _combo(list(BACKGROUNDS))
        self.background.currentIndexChanged.connect(self._value_changed)

        self.back_bright = QSlider(Qt.Horizontal)
        self.back_bright.setRange(0, 100)
        self.back_bright.setValue(opening.brightness)
        self.back_bright.valueChanged.connect(self._value_changed)
        self.back_bright_value = caption("")
        self.back_bright_value.setAlignment(Qt.AlignCenter)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(caption("Visualisation mode"))
        left.addWidget(self.mode)
        left.addWidget(caption("Colour, for the modes that take one"))
        left.addLayout(colour_row)
        left.addWidget(caption("Amplitude"))
        left.addWidget(self.amplitude)
        left.addWidget(self.amplitude_value)
        left.addStretch()

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(caption("Background mode"))
        right.addWidget(self.background)
        right.addWidget(caption("Background brightness"))
        right.addWidget(self.back_bright)
        right.addWidget(self.back_bright_value)
        right.addStretch()

        columns = QHBoxLayout()
        columns.setSpacing(28)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)

        self._show_values()
        return _card(columns)

    def _settings_of(self, name: str) -> MusicSettings:
        return self._settings.setdefault(name, MusicSettings())

    def _load_settings(self, held: MusicSettings) -> None:
        """Put one scheme's settings into the controls, quietly.

        Quietly because every control writes itself back into the scheme
        it belongs to as it moves, and these moves belong to the scheme
        being switched *to*.
        """
        for widget, value in ((self.mode, held.mode),
                              (self.background, held.background)):
            widget.blockSignals(True)
            widget.setCurrentIndex(value)
            widget.blockSignals(False)
        for slider, value in ((self.amplitude, held.amplitude),
                              (self.back_bright, held.brightness)):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
        self._colour = QColor(held.colour)
        self.meter.scheme = self.mode.currentIndex()
        self.meter.base = QColor(self._colour)
        self._show_swatch()
        self._show_values()
        self.meter.update()

    def _keep_settings(self) -> None:
        """Remember the controls against the scheme they belong to."""
        self._settings[self._scheme_name] = MusicSettings(
            mode=self.mode.currentIndex(),
            amplitude=self.amplitude.value(),
            background=self.background.currentIndex(),
            brightness=self.back_bright.value(),
            colour=QColor(self._colour),
        )

    def _show_values(self) -> None:
        """The two readouts under the sliders.

        Amplitude is a raw number because that is what it is -- a
        position on a gain nobody can put a unit on -- and the
        background is a percentage because that is what a brightness is.
        """
        self.amplitude_value.setText(str(self.amplitude.value()))
        self.back_bright_value.setText(f"{self.back_bright.value()}%")

    def _mode_changed(self, index: int) -> None:
        self.meter.scheme = index
        self.meter.update()
        self._keep_settings()

    def _value_changed(self, _value: int = 0) -> None:
        self._show_values()
        self._keep_settings()

    def _band_of(self, key: kb_layout.Key) -> str:
        """Which band a key stands in, and what is in that band."""
        band = key_band(key, BANDS)
        return (f"{key.name} — band {band + 1} of {BANDS}, "
                f"{self._edges[band]:.0f}–{self._edges[band + 1]:.0f} Hz")

    # -- chrome ---------------------------------------------------------
    def _show_swatch(self) -> None:
        self.swatch.setStyleSheet(
            f"background:{self._colour.name()}; border:1px solid #0d3a6b;"
            " border-radius:3px;")

    def _pick_colour(self) -> None:
        chosen = QColorDialog.getColor(self._colour, self, "Colour")
        if chosen.isValid():
            self._colour = chosen
            self.meter.base = QColor(chosen)
            self._show_swatch()
            self.meter.update()
            self._keep_settings()

    def _describe_route(self) -> None:
        """Say on the screen's face what it can and cannot listen to."""
        found = capture_command()
        if found is None:
            self.route.setText(
                "No loopback recorder found. Install pulseaudio-utils "
                "(parec) or pipewire-bin (pw-record) to listen to what "
                "is playing; until then Start shows a stand-in.")
        else:
            self.route.setText(
                f"Will record this machine's output with {found[0]}.")

    # -- running --------------------------------------------------------
    def _start(self) -> None:
        if self._timer.isActive():
            return
        capture = Capture()
        if capture.start():
            self._capture = capture
            self.meter.run(False)
            # The exclusivity is the manual's, and it is the one thing
            # somebody watching the strip would otherwise read as a
            # fault rather than as the keyboard doing what it was asked.
            self.status.emit(
                f"listening on {capture.route} — the strip's own music "
                f"effect does not show while this runs")
        else:
            # Not a failure worth refusing to run for: the controls, the
            # meter and the preview are all still worth seeing, so the
            # screen says what is missing and animates instead.
            self._capture = None
            self.meter.run(True)
            self.status.emit(f"{capture.reason} — showing a stand-in")
        self._timer.start(self.INTERVAL)
        self._sender.start(SEND_INTERVAL)

    def _stop(self) -> None:
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        self._timer.stop()
        self._sender.stop()
        self.meter.run(False)
        self._smooth.clear()
        self._sent.clear()
        self._wire, self._lit = {}, set()
        self.board.colours = {}
        self.board.update()
        # The keys keep whatever they were last told, as they do when
        # the Custom screen stops painting: blacking out the board would
        # be another sixty-nine writes, which is ten seconds of wire for
        # the sake of tidiness nobody asked for.
        self.status.emit("stopped listening")

    def _tick(self) -> None:
        levels = self._listen()
        if levels is None:
            return
        self.meter.show_levels(levels)
        self._paint(levels)

    def _listen(self) -> list[float] | None:
        """Smoothed band levels, or None if there is nothing new yet."""
        if self._capture is None:
            # The meter is animating itself; the board rides along so
            # the stand-in shows the real mapping, just not real sound.
            return list(self.meter.levels)
        if not self._capture.alive():
            reason = self._capture.ended() or "the recorder stopped"
            self._capture.stop()
            self._capture = None
            self.meter.run(True)
            self.status.emit(f"{reason} — showing a stand-in")
            return None
        block = self._capture.latest()
        if block is None or len(block) == 0:
            return None
        bands = spectrum_bands(block, self._capture.rate, self._edges)
        gain = gain_from_amplitude(self.amplitude.value())
        return list(self._smooth.push(levels_from_bands(bands, gain)))

    def _paint(self, levels: list[float]) -> None:
        """Colour every key from the bands, then send what moved."""
        scheme = self.mode.currentIndex()
        behind = self.background.currentIndex()
        back = dim(self._colour, self.back_bright.value() / 100.0)

        preview: dict[int, QColor] = {}
        wire: dict[int, QColor] = {}
        lit_keys: set[int] = set()
        count = len(levels)
        for key in kb_layout.KEYS:
            band = key_band(key, count)
            level = levels[band]
            lit = key_brightness(level, self._row_of[key.light_index],
                                 self._rows)
            if lit > 0.02:
                colour = dim(band_colour(scheme, band, count, level,
                                         self._colour), lit)
                preview[key.light_index] = colour
                wire[key.light_index] = colour
                lit_keys.add(key.light_index)
            elif behind == DIM_STATIC:
                preview[key.light_index] = back
                wire[key.light_index] = back
            elif behind == NOTHING:
                # Explicitly dark, so a key that was lit a frame ago
                # goes out instead of sticking at its last colour.
                wire[key.light_index] = QColor(0, 0, 0)
            # RUNNING_EFFECT sends nothing, which is how the effect
            # already on the keyboard stays visible behind the bars.

        self.board.colours = preview
        self.board.update()
        self._wire, self._lit = wire, lit_keys

    def _send(self) -> None:
        """Spend the wire's budget on the keys that matter most.

        Lit keys outrank background ones whatever the colour difference,
        because every key of a fresh board differs by the maximum and
        there are far more dark keys than bright ones -- sorting on the
        difference alone would spend the first second of every song
        painting the background black while the bars sat still.

        Nothing is sent while the stand-in is running. The meter and the
        preview are worth showing without a recorder, but lighting real
        keys from a sine wave would look exactly like working audio,
        which is the one impression this screen must not give.
        """
        if self._capture is None:
            return
        moved = []
        for index, colour in self._wire.items():
            before = self._sent.get(index)
            gap = 255 if before is None else colour_gap(before, colour)
            if gap >= COLOUR_STEP:
                moved.append((index in self._lit, gap, index, colour))
        moved.sort(key=lambda row: (row[0], row[1]), reverse=True)
        for _, _, index, colour in moved[:KEY_BUDGET]:
            self._sent[index] = QColor(colour)
            self.apply_key.emit(index, QColor(colour))

    def hideEvent(self, event) -> None:
        # Leaving the window is not leaving the screen -- the rail keeps
        # every page alive -- but a closed window must not leave a
        # recorder running, and this is the only notice of it there is.
        if not self.isVisible() and self.window().isHidden():
            self._stop()
        super().hideEvent(event)
