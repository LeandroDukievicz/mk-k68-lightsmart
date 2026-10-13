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
import math
import queue
import shutil
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .. import layout as kb_layout
from ..gui import ACCENT, KeyboardView, caption, ghost_button, primary_button
from .common import _card, _combo, _heading, _note

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

#: Scheme and background orders, as the combo boxes list them.
GREEN_AMBER_RED, SINGLE_COLOUR, SPECTRUM = 0, 1, 2
NOTHING, DIM_STATIC, RUNNING_EFFECT = 0, 1, 2

#: The meter's own colours, lifted out so the board and the bars cannot
#: disagree about what a level looks like.
METER_GREEN = QColor("#3ddc84")
METER_AMBER = QColor("#ffc46b")
METER_RED = QColor("#ff6b6b")

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


def _fft_bands(samples, rate: int, edges: list[float]) -> list[float]:
    block = _np.asarray(samples, dtype=float)
    window = _np.hanning(block.size)
    # Divided by the window's own coherent gain rather than by the frame
    # size, so a full-scale sinusoid reads 1.0 whatever the frame length
    # and whatever window is used. That is what lets the next stage talk
    # in dBFS instead of in arbitrary units.
    scale = max(window.sum() / 2.0, 1e-9)
    spectrum = _np.abs(_np.fft.rfft(block * window)) / scale
    freqs = _np.fft.rfftfreq(block.size, 1.0 / rate)
    out = []
    for low, high in zip(edges, edges[1:]):
        taken = spectrum[(freqs >= low) & (freqs < high)]
        if taken.size:
            # The peak, not the mean: the top bands are a third of an
            # octave wide, and averaging would bury a loud snare in the
            # empty bins beside it.
            out.append(float(taken.max()))
        else:
            # The bottom few bands are narrower than one bin, so no bin
            # centre falls inside them. Reading the nearest bin instead
            # of zero is the difference between a bass line that lights
            # the left of the board and one that never lights at all.
            nearest = int(_np.argmin(_np.abs(freqs - math.sqrt(low * high))))
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
    """The hue for one band, under the chosen visualisation scheme."""
    if scheme == SINGLE_COLOUR:
        return QColor(base)
    if scheme == SPECTRUM:
        # Hue follows the band, not the level. The point of this scheme
        # is to show *where* the energy is; a level-driven hue would say
        # the same thing as the bar height and hide the spectrum.
        colour = QColor()
        colour.setHsv(int(300 * band / max(count - 1, 1)), 255, 255)
        return colour
    if level < 0.6:
        return QColor(METER_GREEN)
    return QColor(METER_AMBER) if level < 0.85 else QColor(METER_RED)


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

    The controls are the vendor's: a colour scheme for the bars, how
    much of the range to use, and what the unlit keys do behind them.
    """

    apply_key = Signal(int, object)          # light_index, QColor
    status = Signal(str)

    SCHEMES = ("Green / yellow / red", "Single colour", "Spectrum")
    BACKGROUNDS = ("Nothing", "Dim static", "The current effect")

    #: 25 ms is a touch faster than the 21 ms frames the recorder
    #: produces, so the drain is always a little ahead of the queue and
    #: the backlog stays at nothing.
    INTERVAL = 25

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
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

        rows = key_rows()
        self._rows = len(rows)
        self._row_of = {k.light_index: rows.index(k.y)
                        for k in kb_layout.KEYS}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("Music mode"))
        outer.addWidget(_note(
            "The listening is local and needs no new command: the host "
            "records its own output and sends colours through the "
            "per-key path the Home screen uses. That path is the limit "
            "— one key is about 140 ms on the wire — so the keyboard "
            "follows the music in broad strokes while the meter and the "
            "preview follow it exactly."
        ))

        self.meter = LevelBar()
        self.board = KeyboardView()

        self.scheme = _combo(list(self.SCHEMES))
        self.scheme.currentIndexChanged.connect(self._scheme_changed)
        self.background = _combo(list(self.BACKGROUNDS))

        self.amplitude = QSlider(Qt.Horizontal)
        self.amplitude.setRange(1, 100)
        self.amplitude.setValue(60)
        self.back_bright = QSlider(Qt.Horizontal)
        self.back_bright.setRange(0, 100)
        self.back_bright.setValue(20)

        self.swatch = QLabel()
        self.swatch.setFixedHeight(22)
        self._show_swatch()

        self.route = caption("")
        self.route.setWordWrap(True)
        self._describe_route()

        actions = QHBoxLayout()
        self.start = primary_button("Start listening", self._start)
        actions.addWidget(self.start)
        actions.addWidget(ghost_button("Stop", self._stop))
        actions.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # A clock of its own, because the screen can show forty frames a
        # second and the keyboard cannot take them.
        self._sender = QTimer(self)
        self._sender.timeout.connect(self._send)

        side = _card(
            caption("Visualisation"), self.scheme,
            caption("Colour"), self.swatch,
            ghost_button("Pick a colour…", self._pick_colour),
            caption("Amplitude"), self.amplitude,
            caption("Behind it"), self.background,
            caption("Background brightness"), self.back_bright,
            actions, self.route,
        )
        side.setFixedWidth(330)

        body = QHBoxLayout()
        body.setSpacing(14)
        # Pinned to the top: the board is a fixed size, so without this
        # the card is stretched to the full height and the caption --
        # the only thing in it that can grow -- takes the slack and
        # floats in the middle of an empty panel.
        body.addWidget(_card(caption("Preview"), self.board), 1, Qt.AlignTop)
        body.addWidget(side, 0, Qt.AlignTop)

        outer.addWidget(_card(caption("Levels"), self.meter))
        outer.addLayout(body, 1)
        outer.addStretch()

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

    def _scheme_changed(self, index: int) -> None:
        self.meter.scheme = index
        self.meter.update()

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
            self.status.emit(f"listening on {capture.route}")
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
        scheme = self.scheme.currentIndex()
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
