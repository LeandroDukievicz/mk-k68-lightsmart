"""The custom-lighting screen: an effect mode, on the keys you pick."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import layout as kb_layout
from .. import protocol
from .. import store
from ..gui import KeyboardView, caption, ghost_button, primary_button
from .common import _card, _heading, _list, _note

#: The row Esc sits in. This board has no function row of its own --
#: F1 to F12 are on this row under Fn -- so the nearest thing to the
#: vendor's F-row group is the row itself, and the ``y`` is read off
#: the layout so a layout revision moves the group with it.
TOP_ROW_Y = kb_layout.BY_IDENT["Esc"].y

#: The arrow cluster, by HID usage: right, left, down, up. The idents
#: are the unicode arrows the vendor file prints, which an editor or a
#: bad copy-paste can mangle into a group that silently matches nothing.
ARROW_USAGES = frozenset((0x4F, 0x50, 0x51, 0x52))

#: The HID modifier block -- both ctrls, shifts, alts and the Win key --
#: plus Fn, which is not a modifier to the host (0xAF is the vendor's
#: own usage) but is one to anybody looking at the keycaps.
MODIFIER_USAGES = (
    frozenset(range(0xE0, 0xE8)) | {kb_layout.BY_IDENT["Fn"].code})


def _colour_of(rgb: int) -> QColor:
    """A stored colour. The scheme file keeps 0xRRGGBB integers because
    it is JSON and a QColor is not."""
    return QColor((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)


def _rgb_of(colour: QColor) -> int:
    return (colour.red() << 16) | (colour.green() << 8) | colour.blue()


def _colour_from_file(value: object) -> QColor | None:
    """One colour out of an imported layout, or None if it is not one.

    Exports write "#rrggbb" so a shared file reads like something a
    person could edit; imports also take the plain integer, which is
    what a layout copied out of the scheme file looks like.
    """
    if isinstance(value, int):
        return _colour_of(value)
    if isinstance(value, str):
        colour = QColor(value)
        return colour if colour.isValid() else None
    return None


# ----------------------------------------------------------------------
# Where the keys are, as fractions of the board
# ----------------------------------------------------------------------
#
# The animations below are the host's own arithmetic, so they are
# written against the board's extent rather than the layout's pixels:
# a key is at 0.0 on the left edge and 1.0 on the right, whatever the
# layout file says those are in millimetres.


def _spread(values: dict[int, float]) -> dict[int, float]:
    """Rescale a per-key measurement to run 0 to 1."""
    low, high = min(values.values()), max(values.values())
    span = (high - low) or 1.0
    return {index: (value - low) / span for index, value in values.items()}


#: Across the board, 0 at the left edge and 1 at the right.
ACROSS = _spread({k.light_index: k.x + k.w / 2 for k in kb_layout.KEYS})

#: Down the board, 0 at the top row and 1 at the bottom.
DOWN = _spread({k.light_index: k.y + k.h / 2 for k in kb_layout.KEYS})

#: Out from the middle, 0 in the centre and 1 at the furthest corner.
RADIUS = _spread({
    index: math.hypot(ACROSS[index] - 0.5, DOWN[index] - 0.5)
    for index in ACROSS
})

#: Round the middle, as a fraction of a turn anticlockwise from the
#: right-hand side.
ANGLE = {
    index: (math.atan2(0.5 - DOWN[index], ACROSS[index] - 0.5)
            / (2 * math.pi)) % 1.0
    for index in ACROSS
}

#: A fixed offset per key, so the twinkling effects do not blink as one
#: board. Taken from the light index by the golden ratio rather than
#: randomised: a frame has to come out the same every time it is drawn,
#: which is what makes an animation testable at all.
SPARK = {index: (index * 0.6180339887) % 1.0 for index in ACROSS}


# ----------------------------------------------------------------------
# The animations, which are the host's
# ----------------------------------------------------------------------
#
# There is no confirmed command that puts a built-in effect on *part*
# of the board: the effect commands (docs/PROTOCOL.md §5) light the
# whole keyboard and take one colour for it. So the vendor's screen is
# reproduced the only honest way -- the host works out what each picked
# key should be showing this instant and sends it down the per-key
# colour path. The shapes below are this app's reading of the vendor's
# names, not the firmware's own arithmetic, and they stop when the app
# does.


def _dim(colour: QColor, amount: float) -> QColor:
    """A colour at a fraction of its brightness."""
    amount = max(0.0, min(1.0, amount))
    return QColor(int(colour.red() * amount),
                  int(colour.green() * amount),
                  int(colour.blue() * amount))


def _swell(turn: float) -> float:
    """A smooth 0 to 1 to 0 over one turn."""
    return (1.0 - math.cos(2 * math.pi * turn)) / 2.0


def _band(position: float, phase: float, width: float = 0.3) -> float:
    """How bright a key is as a band sweeps past it.

    The band wraps, so the distance is measured the short way round --
    otherwise every pass would end with the board going dark while the
    front travelled back from one edge to the other.
    """
    gap = (position - phase) % 1.0
    gap = min(gap, 1.0 - gap)
    return max(0.0, 1.0 - gap / width)


def _rainbow(turn: float, colour: QColor) -> QColor:
    """A fully saturated hue, at the brightness of the colour in hand.

    The spectrum effects are the ones the vendor's own screen draws in
    every colour at once, so the picked colour cannot set the hue. It
    sets how bright the thing is, which is the only part of it left.
    """
    value = max(colour.valueF(), 0.25)
    return QColor.fromHsvF(turn % 1.0, 1.0, value)


def _static(index: int, phase: float, colour: QColor) -> QColor:
    return QColor(colour)


def _closed(index: int, phase: float, colour: QColor) -> QColor:
    return QColor(0, 0, 0)


def _glittering(index: int, phase: float, colour: QColor) -> QColor:
    # The fifth power leaves most keys near dark and a few at a peak,
    # which is what "Glittering" -- a scatter of stars -- looks like.
    return _dim(colour, _swell((phase + SPARK[index]) % 1.0) ** 5)


def _falling(index: int, phase: float, colour: QColor) -> QColor:
    return _dim(colour, _band(DOWN[index], phase))


def _launch(index: int, phase: float, colour: QColor) -> QColor:
    return _dim(colour, _band(1.0 - DOWN[index], phase))


def _rolling(index: int, phase: float, colour: QColor) -> QColor:
    return _dim(colour, _band(ACROSS[index], phase))


def _shuttle(index: int, phase: float, colour: QColor) -> QColor:
    # There and back inside one turn, rather than wrapping: a shuttle
    # returns the way it came.
    there = phase * 2 if phase < 0.5 else (1.0 - phase) * 2
    return _dim(colour, _band(ACROSS[index], there))


def _tilt(index: int, phase: float, colour: QColor) -> QColor:
    return _dim(colour, _band((ACROSS[index] + DOWN[index]) / 2, phase))


def _breath(index: int, phase: float, colour: QColor) -> QColor:
    return _dim(colour, 0.12 + 0.88 * _swell(phase))


def _pulsating(index: int, phase: float, colour: QColor) -> QColor:
    # A breath that reaches the rows one after another, so the board
    # beats rather than flashing.
    return _dim(colour, 0.12 + 0.88 * _swell(phase + DOWN[index] / 4))


def _flowing(index: int, phase: float, colour: QColor) -> QColor:
    return _dim(colour, 0.2 + 0.8 * _swell((ACROSS[index] - phase) % 1.0))


def _explode(index: int, phase: float, colour: QColor) -> QColor:
    return _dim(colour, _band(RADIUS[index], phase, 0.18))


def _ripples(index: int, phase: float, colour: QColor) -> QColor:
    return _dim(colour, _swell((RADIUS[index] * 3 - phase) % 1.0))


def _rotating(index: int, phase: float, colour: QColor) -> QColor:
    return _dim(colour, _band(ANGLE[index], phase, 0.22))


def _colourful(index: int, phase: float, colour: QColor) -> QColor:
    return _rainbow(phase + (ACROSS[index] + DOWN[index]) / 2, colour)


def _spectrum(index: int, phase: float, colour: QColor) -> QColor:
    return _rainbow(phase, colour)


def _outward(index: int, phase: float, colour: QColor) -> QColor:
    return _rainbow(phase + RADIUS[index], colour)


def _scrolling(index: int, phase: float, colour: QColor) -> QColor:
    return _rainbow(phase + ACROSS[index], colour)


def _mode(name: str) -> int:
    """The mode byte of an effect, by the protocol's own name for it.

    Looked up rather than written out, so a table of magic numbers
    cannot drift away from ``protocol.EFFECTS`` -- and a name that
    stops existing fails at import rather than at a click.
    """
    return protocol.EFFECT_BY_NAME[name.lower()].mode


#: What the host does with each mode. A mode missing from here is one
#: the host cannot play at all; see :data:`PRESS_REACTIVE`.
PLAYERS = {
    _mode("Close Backlight"): _closed,
    _mode("Static"): _static,
    _mode("Glittering"): _glittering,
    _mode("Falling"): _falling,
    _mode("Colourful"): _colourful,
    _mode("Breath"): _breath,
    _mode("Spectrum"): _spectrum,
    _mode("Outward"): _outward,
    _mode("Scrolling"): _scrolling,
    _mode("Rolling"): _rolling,
    _mode("Rotating"): _rotating,
    _mode("Explode"): _explode,
    _mode("Launch"): _launch,
    _mode("Ripples"): _ripples,
    _mode("Flowing"): _flowing,
    _mode("Pulsating"): _pulsating,
    _mode("Tilt"): _tilt,
    _mode("Shuttle"): _shuttle,
}

#: The two the host cannot play. Both light a key *as it is pressed*,
#: and the presses go to whatever has focus -- the vendor interface
#: reports no keystrokes, so there is nothing here to react to. Offered
#: in the list all the same, because a missing entry would read as an
#: oversight rather than as the limit it is.
PRESS_REACTIVE = frozenset((_mode("SingleOn"), _mode("SingleOff")))

#: Modes whose frame never changes, so they are sent once instead of
#: being animated: a steady colour, and the off switch.
STEADY = frozenset((_mode("Static"), _mode("Close Backlight")))

#: Modes that run through every hue, so the picked colour can only set
#: how bright they are.
SPECTRUM = frozenset((_mode("Colourful"), _mode("Spectrum"),
                      _mode("Outward"), _mode("Scrolling")))

#: The effects the vendor's own custom screen lists, in its order: the
#: ten-entry list at 0x43D800 (docs/PROTOCOL.md §5), which is the short
#: form of the effect table. Named rather than sliced out by row, so a
#: re-ordering of ``protocol.EFFECTS`` cannot quietly change which
#: effects this screen puts first.
VENDOR_ORDER = ("Static", "Glittering", "Falling", "Colourful", "Breath",
                "Spectrum", "Outward", "Scrolling", "Rolling", "Rotating")

#: What this screen offers: the vendor's ten first, then the rest of
#: the table. The animations are the host's own, so there is no reason
#: to withhold the modes the vendor's custom screen leaves out -- but
#: somebody coming from that screen looks for its ten.
CUSTOM_EFFECTS = tuple(
    [protocol.EFFECT_BY_NAME[name.lower()] for name in VENDOR_ORDER]
    + [e for e in protocol.EFFECTS if e.name not in VENDOR_ORDER]
)

#: The fourteen swatches under the vendor's wheel, read off the
#: manual's picker: a row of hues, a row running green round to red,
#: and white and black to finish.
PRESETS = (
    "#ff0000", "#ff00ff", "#8000ff", "#0000ff", "#1e90ff", "#00e5ff",
    "#2e8b57", "#00e000", "#9acd32", "#ffe000", "#ffa500", "#ff6a00",
    "#ffffff", "#000000",
)


def _spin() -> QSpinBox:
    """One of the vendor's R, G and B boxes."""
    box = QSpinBox()
    box.setRange(0, 255)
    box.setFixedWidth(58)
    box.setStyleSheet(
        "QSpinBox { background:#0a2444; color:#cfe3ff; border:1px solid"
        " #0d3a6b; border-radius:3px; padding:2px 4px; font-size:11px; }")
    return box


def _check(text: str) -> QCheckBox:
    """One of the quick selectors above the board."""
    box = QCheckBox(text)
    box.setCursor(Qt.PointingHandCursor)
    box.setStyleSheet("QCheckBox { color:#9fb3d0; font-size:11px; }")
    return box


class ColourWheel(QWidget):
    """The vendor's colour ring: hue round the rim, the colour in hand
    in the middle.

    Hue only. Saturation and brightness come from the R, G and B boxes
    beside it, which is how the vendor's picker is arranged -- the ring
    is for reaching a colour quickly, the boxes for saying exactly
    which one.
    """

    picked = Signal(object)                   # QColor

    #: How much of the radius the hue ring takes.
    RIM = 0.34

    def __init__(self, size: int = 116, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._colour = QColor("#ff0000")

    def set_colour(self, colour: QColor) -> None:
        self._colour = QColor(colour)
        self.update()

    def hue_at(self, pos: QPointF) -> int | None:
        """The hue the ring carries under a point.

        None off the ring: the middle is a swatch showing the colour in
        hand, not a control, and a click that lands there should not
        swing the hue to wherever the pointer happened to be.
        """
        middle = self.width() / 2.0
        dx, dy = pos.x() - middle, pos.y() - middle
        distance = math.hypot(dx, dy)
        if not middle * (1.0 - self.RIM) <= distance <= middle:
            return None
        return int(math.degrees(math.atan2(-dy, dx)) % 360.0)

    def _pick(self, pos: QPointF) -> None:
        hue = self.hue_at(pos)
        if hue is None:
            return
        # A grey in hand has no saturation to keep and would come back
        # grey whatever was clicked, which looks like a broken ring.
        self.picked.emit(QColor.fromHsv(
            hue, self._colour.saturation() or 255,
            self._colour.value() or 255))

    def mousePressEvent(self, event) -> None:
        self._pick(event.position())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons():
            self._pick(event.position())

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rim = QRectF(1, 1, self.width() - 2, self.height() - 2)
        middle = rim.center()

        # Twelve stops and a repeat of the first, so the gradient closes
        # on itself instead of running the long way back to red.
        wheel = QConicalGradient(middle, 0.0)
        for step in range(13):
            wheel.setColorAt(step / 12.0,
                             QColor.fromHsv((step * 30) % 360, 255, 255))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(wheel))
        p.drawEllipse(rim)

        inset = rim.width() * self.RIM / 2.0
        hole = rim.adjusted(inset, inset, -inset, -inset)
        p.setBrush(QColor("#0a1024"))
        p.drawEllipse(hole)
        p.setBrush(self._colour)
        p.drawEllipse(hole.adjusted(5, 5, -5, -5))

        hue = self._colour.hue()
        if hue >= 0:
            reach = rim.width() / 2.0 * (1.0 - self.RIM / 2.0)
            at = QPointF(middle.x() + math.cos(math.radians(hue)) * reach,
                         middle.y() - math.sin(math.radians(hue)) * reach)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            p.drawEllipse(at, 4, 4)


# ----------------------------------------------------------------------
# 4. Custom -- an effect mode, on the keys that are picked
# ----------------------------------------------------------------------
class CustomPage(QWidget):
    """The vendor's Custom Light, as far as the protocol allows.

    Its screen is not a per-key painter: keys are *picked*, an effect
    mode is chosen from a list, a colour and an animation speed go with
    it, and Apply puts that effect on those key positions.

    No command does that. The effect commands light the whole board and
    take one colour for it; the only per-key command is a fixed colour,
    one key per transaction (docs/PROTOCOL.md §5). So the effect is run
    on the host: this screen works out what each picked key should be
    showing this instant and sends the ones that moved, which is the
    same path the Music screen drives and the same wire budget -- about
    seven keys a second. The animation therefore lives in this app and
    ends with it, and the screen says so rather than implying the
    keyboard has taken anything.

    Picking is painting, as it was before this screen grew an effect
    list: a press or a stroke gives a key the colour in hand and puts
    it in the selection, the quick selectors take whole groups, and a
    right-click rubs one out. A key picked but never painted takes the
    colour in hand when Apply reaches it.
    """

    #: The groups the toolbar offers. Predicates over ``kb_layout``
    #: rather than lists of LED indices, so they stay right if the
    #: layout is ever re-transcribed. The first three are the vendor's.
    GROUPS = {
        "All": lambda k: True,
        "Numbers": lambda k: k.ident.isdigit(),
        "WASD": lambda k: k.ident in ("W", "A", "S", "D"),
        "Top row": lambda k: k.y == TOP_ROW_Y,
        "Letters": lambda k: len(k.ident) == 1 and k.ident.isalpha(),
        "Arrows": lambda k: k.code in ARROW_USAGES,
        "Modifiers": lambda k: k.code in MODIFIER_USAGES,
    }

    #: The three the vendor puts above the board as checkboxes, and the
    #: wording it puts on the first of them.
    QUICK = (("All", "Select all"), ("Numbers", "Numbers"), ("WASD", "WASD"))

    #: How long a change waits before it reaches the file. A stroke
    #: fires a change per mouse move and a group paints up to 69 keys
    #: at once; the scheme file is written whole, so the writes coalesce
    #: here rather than rewriting it per painted key.
    SAVE_DELAY_MS = 400

    #: How many colours the palette keeps: enough for a layout with a
    #: couple of accents, few enough to stay one row in the card.
    RECENT_COLOURS = 7

    #: Bumped if an exported file stops being readable by this code.
    FILE_VERSION = 1

    #: Brightness handed to the built-in effect command. Full, because
    #: this screen has no brightness control of its own -- the vendor
    #: puts that one on its effect-mode screen, not on this one.
    BUILTIN_BRIGHTNESS = 100

    #: How often a frame is drawn. The preview can run at this rate;
    #: the keyboard cannot, which is what the sender below is for.
    FRAME_MS = 40

    #: One turn of an animation at speed 1, in milliseconds. The slider
    #: divides it, so speed 15 -- the vendor's own default -- is a turn
    #: every 600 ms.
    TURN_MS = 9000

    #: The speed the slider starts at, which is the number the vendor's
    #: screenshot shows under its own.
    DEFAULT_SPEED = 15
    SPEED_RANGE = (1, 30)

    #: How often keys go out, and how many of them. A key colour is
    #: nine paced transfers -- about 140 ms -- so this is the wire's
    #: own limit rather than a throttle chosen here. The Music screen
    #: reached the same two numbers from the same arithmetic.
    SEND_INTERVAL = 150
    KEY_BUDGET = 2

    #: How far a key's colour has to move before it is worth a
    #: transfer, as the largest change of any one channel.
    COLOUR_STEP = 28

    apply_key = Signal(int, object)          # light_index, QColor
    status = Signal(str)
    #: mode, r, g, b, brightness, speed -- the built-in effect command,
    #: which the firmware runs itself. Used when the whole board is
    #: picked, because then there is nothing a per-key animation can do
    #: that this does not do better. See :meth:`_apply`.
    apply_effect = Signal(int, int, int, int, int, int)
    #: The scheme that has just become the active one, emitted when the
    #: list on this screen switches it. Every screen draws some part of
    #: a scheme and they share one store, so a switch changes what they
    #: are all looking at -- this is how the window is told.
    scheme_changed = Signal(object)

    def __init__(self, current_colour, parent: QWidget | None = None):
        super().__init__(parent)
        self._colour = current_colour
        self.store = store.shared()

        #: light_index -> QColor, the layout as it stands. Seeded from
        #: the file: the keyboard forgets a layout as soon as anything
        #: else lights it, so the host is the only place one survives.
        self.painted: dict[int, QColor] = {
            index: _colour_of(rgb)
            for index, rgb in self.store.current.lighting.items()
        }
        #: The keys the next Apply writes. A key is picked by painting
        #: it, or by a quick selector -- which picks without painting,
        #: the way the vendor's checkboxes do.
        self.chosen: set[int] = set()
        #: Keys rubbed out since the last send. Only a colour ever goes
        #: to the keyboard, so an erased key stays lit until something
        #: sends it black -- these are what the next send blacks out.
        self.stale: set[int] = set()
        #: Colours used lately, newest first.
        self.recent: list[QColor] = []
        self._stroke: str | None = None

        #: The effect running on the host, or None. With it, the keys it
        #: is running on and the colour each of them took.
        self._running = None
        self._targets: dict[int, QColor] = {}
        self._phase = 0.0
        #: The latest frame, and what the keyboard has been told of it.
        #: The screen and the wire run at different rates, so the frame
        #: is left here and the sender takes what it can afford.
        self._wire: dict[int, QColor] = {}
        self._sent: dict[int, QColor] = {}

        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.setInterval(self.SAVE_DELAY_MS)
        self._save.timeout.connect(self._save_now)
        self._frames = QTimer(self)
        self._frames.timeout.connect(self._tick)
        # A clock of its own, because the preview can show twenty-five
        # frames a second and the keyboard takes seven keys.
        self._sender = QTimer(self)
        self._sender.timeout.connect(self._send_frame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 12)
        outer.setSpacing(9)
        outer.addWidget(_heading("Custom lighting"))
        outer.addWidget(_note(
            "No command puts a built-in effect on part of the board — "
            "the effect commands light all of it and take one colour. "
            "So the effect below is run by this app: it works out what "
            "each picked key should show and sends it down the per-key "
            "path the Home screen uses. It runs while the app does, "
            "stops when the app closes, and the keys keep whatever they "
            "were last sent. The wire takes about seven keys a second, "
            "so the preview is exact and the keyboard follows in broad "
            "strokes."
        ))

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addLayout(self._build_left())
        body.addLayout(self._build_board(), 1)
        outer.addLayout(body, 1)

        # The palette comes back with the layout: whatever is already on
        # the board is what the next edit most likely wants to match.
        self.recent = _in_use_order(self.painted)[:self.RECENT_COLOURS]
        self._show_recent()
        self._show_colour()
        self._show_speed()
        self._describe()
        self._refresh_schemes()
        self._refresh()

    # -- the screen, left to right ---------------------------------------
    def _build_left(self) -> QVBoxLayout:
        """The vendor's two left-hand lists: schemes, then effect modes."""
        self.schemes = _list()
        self.schemes.currentRowChanged.connect(self._scheme_chosen)

        scheme_edit = QHBoxLayout()
        scheme_edit.setSpacing(5)
        scheme_edit.addWidget(ghost_button("New", self._new_scheme))
        scheme_edit.addWidget(ghost_button("Rename", self._rename_scheme))
        scheme_edit.addWidget(ghost_button("Delete", self._delete_scheme))
        scheme_files = QHBoxLayout()
        scheme_files.setSpacing(5)
        scheme_files.addWidget(ghost_button("Import…", self._import_scheme))
        scheme_files.addWidget(ghost_button("Export…", self._export_scheme))

        schemes = _card(caption("Scheme list"), self.schemes,
                        scheme_edit, scheme_files)
        schemes.setFixedWidth(236)
        self.schemes.setFixedHeight(96)

        self.effects = _list()
        for effect in CUSTOM_EFFECTS:
            self.effects.addItem(effect.name)
        self.effects.setCurrentRow(0)
        self.effects.currentRowChanged.connect(lambda _row: self._describe())
        self.effects.itemDoubleClicked.connect(lambda _item: self._apply())

        self.about = caption("")
        self.about.setWordWrap(True)
        modes = _card(caption("Lighting mode"), self.effects, self.about)
        modes.setFixedWidth(236)

        left = QVBoxLayout()
        left.setSpacing(9)
        left.addWidget(schemes)
        left.addWidget(modes, 1)
        return left

    def _build_board(self) -> QVBoxLayout:
        """The quick selectors, the board, and the controls under it."""
        # 0.95 rather than the default: this screen has a column of
        # lists beside the board and a row of controls under it, which
        # is the vendor's own arrangement and does not fit otherwise.
        self.board = KeyboardView(0.95)
        self.board.key_clicked.connect(self._paint)
        self.board.hover_text = self._hover_text
        # Presses arrive as the signal above; everything else about the
        # mouse -- strokes, and the right-click that rubs a key out --
        # has to be read before the view sees it.
        self.board.installEventFilter(self)

        quick = QHBoxLayout()
        quick.setSpacing(8)
        quick.addWidget(caption("Pick the keys"))
        self.quick: dict[str, QCheckBox] = {}
        for name, label in self.QUICK:
            box = _check(label)
            box.toggled.connect(
                lambda on, group=name: self._choose(group, on))
            quick.addWidget(box)
            self.quick[name] = box
        quick.addWidget(ghost_button("None", self._choose_none))
        quick.addStretch()

        middle = QVBoxLayout()
        middle.setSpacing(9)
        middle.addLayout(quick)
        middle.addWidget(_card(self.board), 0, Qt.AlignLeft)
        middle.addWidget(self._build_controls())
        middle.addStretch()
        return middle

    def _build_controls(self) -> QWidget:
        """The vendor's bottom strip: the wheel, the speed, the buttons."""
        self.wheel = ColourWheel()
        self.wheel.picked.connect(self._use)

        self.swatch = QLabel()
        self.swatch.setFixedHeight(20)
        self.rgb: dict[str, QSpinBox] = {}
        channels = QVBoxLayout()
        channels.setSpacing(4)
        for name in ("R", "G", "B"):
            box = _spin()
            box.valueChanged.connect(self._rgb_typed)
            self.rgb[name] = box
            row = QHBoxLayout()
            row.addWidget(caption(f"{name}:"))
            row.addWidget(box)
            row.addStretch()
            channels.addLayout(row)
        channels.addWidget(self.swatch)
        channels.addWidget(ghost_button("Pick…", self._pick_colour))

        presets = QGridLayout()
        presets.setSpacing(4)
        for n, name in enumerate(PRESETS):
            presets.addWidget(self._swatch_button(QColor(name)),
                              n // 7, n % 7)

        self._palette_row = QHBoxLayout()
        self._palette_row.setSpacing(5)

        wheel_row = QHBoxLayout()
        wheel_row.setSpacing(10)
        wheel_row.addWidget(self.wheel)
        wheel_row.addLayout(channels)
        wheel_row.addLayout(presets)

        colours = QVBoxLayout()
        colours.setSpacing(6)
        colours.addWidget(caption("Colour"))
        colours.addLayout(wheel_row)
        colours.addWidget(caption("Used lately"))
        colours.addLayout(self._palette_row)

        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(*self.SPEED_RANGE)
        self.speed.setValue(self.DEFAULT_SPEED)
        self.speed.setFixedWidth(190)
        self.speed.valueChanged.connect(lambda _v: self._show_speed())
        self.speed_value = QLabel()
        self.speed_value.setStyleSheet(
            "color:#cfe3ff; font-size:12px; font-weight:bold;")

        groups = QGridLayout()
        groups.setSpacing(5)
        for n, name in enumerate(("Top row", "Letters", "Arrows",
                                  "Modifiers")):
            groups.addWidget(ghost_button(name, self._filler(name)),
                             n // 2, n % 2)

        speeds = QVBoxLayout()
        speeds.setSpacing(6)
        speeds.addWidget(caption("Animation speed"))
        speeds.addWidget(self.speed)
        speeds.addWidget(self.speed_value)
        speeds.addWidget(caption("Paint a group with the colour in hand"))
        speeds.addLayout(groups)
        speeds.addStretch()

        actions = QHBoxLayout()
        actions.addWidget(primary_button("Apply", self._apply))
        actions.addWidget(ghost_button("Stop", self._stop_clicked))
        files = QHBoxLayout()
        files.addWidget(ghost_button("Import…", self._import))
        files.addWidget(ghost_button("Export…", self._export))

        buttons = QVBoxLayout()
        buttons.setSpacing(6)
        buttons.addLayout(actions)
        buttons.addWidget(ghost_button("Reset", self._reset))
        buttons.addWidget(caption("Layout file"))
        buttons.addLayout(files)
        buttons.addWidget(caption(
            "Drag across the board to paint a run of keys. Right-click, "
            "or ctrl-click, to rub one out."))
        buttons.addStretch()

        strip = QHBoxLayout()
        strip.setSpacing(16)
        strip.addLayout(colours)
        strip.addLayout(speeds)
        strip.addLayout(buttons, 1)
        return _card(strip)

    def _hover_text(self, key: kb_layout.Key) -> str:
        """What the board says about a key under the pointer."""
        colour = self.painted.get(key.light_index)
        picked = "picked" if key.light_index in self.chosen else "not picked"
        return (f"{key.ident} — {picked}, "
                + (colour.name() if colour is not None else "unpainted"))

    # -- the colour in hand ---------------------------------------------
    def _show_colour(self) -> None:
        """Put the colour in hand on the swatch, the ring and the boxes."""
        colour = self._colour()
        self.swatch.setStyleSheet(
            f"background:{colour.name()}; border:1px solid #0d3a6b;"
            " border-radius:3px;")
        self.wheel.set_colour(colour)
        for name, value in (("R", colour.red()), ("G", colour.green()),
                            ("B", colour.blue())):
            box = self.rgb[name]
            # Blocked rather than guarded by a flag: writing the boxes
            # back is what a pick does, and an unblocked write would
            # come straight back in as three more picks.
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)

    def _rgb_typed(self, _value: int) -> None:
        self._use(QColor(self.rgb["R"].value(), self.rgb["G"].value(),
                         self.rgb["B"].value()))

    def _pick_colour(self) -> None:
        chosen = QColorDialog.getColor(self._colour(), self, "Colour")
        if chosen.isValid():
            self._use(chosen)

    def _use(self, colour: QColor) -> None:
        # The page keeps its own colour once one is chosen here, so
        # painting does not change what the rest of the app is using.
        self._colour = lambda c=QColor(colour): c
        self._show_colour()
        self._remember(colour)

    def _remember(self, colour: QColor) -> None:
        """Put a colour at the front of the palette, once.

        Reopening the picker for every second key is what makes a
        per-key editor tiring, so a colour already in use stays one
        click away.
        """
        self.recent = ([QColor(colour)]
                       + [c for c in self.recent if c != colour])
        del self.recent[self.RECENT_COLOURS:]
        self._show_recent()

    def _show_recent(self) -> None:
        while self._palette_row.count():
            widget = self._palette_row.takeAt(0).widget()
            if widget is not None:
                # deleteLater rather than a plain delete: this rebuild
                # usually runs from inside one of these buttons' click.
                widget.deleteLater()
        for colour in self.recent:
            self._palette_row.addWidget(self._swatch_button(colour))
        self._palette_row.addStretch()

    def _swatch_button(self, colour: QColor) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(26, 20)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(colour.name())
        button.setStyleSheet(
            f"QPushButton {{ background:{colour.name()};"
            " border:1px solid #0d3a6b; border-radius:3px; }"
            "QPushButton:hover { border:1px solid #10a9fd; }")
        button.clicked.connect(lambda: self._use(colour))
        return button

    # -- the effect in hand ---------------------------------------------
    def effect(self):
        """The effect the list has selected."""
        row = self.effects.currentRow()
        if 0 <= row < len(CUSTOM_EFFECTS):
            return CUSTOM_EFFECTS[row]
        return CUSTOM_EFFECTS[0]

    def _select_effect(self, effect) -> None:
        self.effects.setCurrentRow(CUSTOM_EFFECTS.index(effect))

    def _describe(self) -> None:
        """Say what the selected effect does, and what it does not.

        The list is the protocol's own, and three of its entries are
        things this screen cannot do: two react to key presses it never
        sees, and one is an off switch rather than an animation. Saying
        so under the list is cheaper than a click that does nothing.
        """
        effect = self.effect()
        if effect.mode in PRESS_REACTIVE:
            text = (f"{effect.name} lights a key as it is pressed. The "
                    "presses never reach this app, so it cannot be run "
                    "from the host.")
        elif effect.mode == _mode("Close Backlight"):
            text = "Puts the picked keys out, and leaves the rest alone."
        elif effect.mode == _mode("Static"):
            text = "A steady colour, sent once rather than animated."
        elif effect.mode in SPECTRUM:
            text = (f"{effect.name} runs through every hue, so the colour "
                    "only sets how bright it is.")
        else:
            text = f"{effect.name}, in the colour in hand."
        self.about.setText(text)

    def _show_speed(self) -> None:
        turn = self._turn_ms(self.speed.value()) / 1000.0
        self.speed_value.setText(f"{self.speed.value()}   ({turn:.1f} s a turn)")

    @classmethod
    def _turn_ms(cls, speed: int) -> int:
        """How long one turn of an animation takes at a speed setting.

        Floored: past about five turns a second the wire is sending the
        same two keys a hundred milliseconds apart and the board shows
        a flicker rather than a motion.
        """
        return max(200, int(cls.TURN_MS / max(1, speed)))

    # -- picking keys ---------------------------------------------------
    def _paint(self, key: kb_layout.Key) -> None:
        colour = QColor(self._colour())
        if (self.painted.get(key.light_index) == colour
                and key.light_index in self.chosen):
            # A stroke reports every pixel it crosses, and most of those
            # land on a key that is already this colour and picked.
            return
        self.painted[key.light_index] = colour
        self.chosen.add(key.light_index)
        self.stale.discard(key.light_index)
        self._remember(colour)
        self._refresh()

    def _erase(self, key: kb_layout.Key) -> None:
        self.chosen.discard(key.light_index)
        if self.painted.pop(key.light_index, None) is None:
            return
        self.stale.add(key.light_index)
        self._refresh()

    def _filler(self, name: str):
        """Give a whole group the colour in hand, and pick it."""
        def fill() -> None:
            match = self.GROUPS[name]
            colour = QColor(self._colour())
            for key in kb_layout.KEYS:
                if match(key):
                    self.painted[key.light_index] = colour
                    self.chosen.add(key.light_index)
                    self.stale.discard(key.light_index)
            self._remember(colour)
            self._refresh()
        return fill

    def _choose(self, name: str, on: bool) -> None:
        """Pick or drop a whole group, without painting it.

        What the vendor's three checkboxes do: they select, and the
        colour is decided afterwards by the wheel. A key picked this
        way carries no colour of its own, so Apply gives it the one in
        hand.
        """
        group = {key.light_index for key in kb_layout.KEYS
                 if self.GROUPS[name](key)}
        if on:
            self.chosen |= group
        else:
            self.chosen -= group
        self._refresh()

    def _choose_none(self) -> None:
        self.chosen.clear()
        for box in self.quick.values():
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
        self._refresh()

    def _clear(self) -> None:
        # Every key that was painted still has its LED lit, so each one
        # is owed a blanking on the next send.
        self.stale |= set(self.painted)
        self.painted.clear()
        self._refresh()

    def _targets_now(self) -> dict[int, QColor]:
        """The keys the next Apply writes, with the colour each takes.

        Picking keys and then applying is the vendor's whole flow --
        "select the keys, choose the effect, press Apply to put it on
        those key positions" -- so an explicit pick takes the colour in
        hand, including for a key that already carries one. Letting the
        old colour win there made Apply look dead: the keys were picked,
        a new colour was chosen, and what went to the keyboard was
        whatever had been painted before.

        With nothing picked there is no such instruction, so the whole
        painted layout goes as it was painted -- which keeps a layout
        built key by key from being flattened to one colour.
        """
        if self.chosen:
            colour = QColor(self._colour())
            return {index: QColor(colour) for index in sorted(self.chosen)}
        return {index: QColor(colour)
                for index, colour in sorted(self.painted.items())}

    def showEvent(self, event) -> None:
        """Re-read the layout whenever the screen comes up.

        The profiles screen can switch scheme while this one is out of
        sight, and the painted layout belongs to the scheme in use.
        Anything rubbed out but not yet sent is dropped with it, which
        is right: it was never part of the scheme being switched to.
        """
        super().showEvent(event)
        self._read_scheme()
        self._refresh_schemes()

    def _refresh(self) -> None:
        # The view only reads these, so it is handed ours rather than a
        # copy -- a stroke edits them dozens of times a second.
        self.board.colours = self.painted
        self.board.chosen = self.chosen
        self.board.update()
        self._save.start()

    # -- strokes --------------------------------------------------------
    def eventFilter(self, watched, event) -> bool:
        """Read the mouse on the board before the board does.

        The view emits its click on press only, and emits it for any
        button at all. So a stroke has to be assembled from the moves
        here, and the right-click that erases has to be swallowed --
        letting it reach the view would paint the key it just rubbed
        out.
        """
        if watched is not self.board:
            return super().eventFilter(watched, event)
        kind = event.type()
        if kind == QEvent.MouseButtonPress:
            return self._pressed(event)
        if kind == QEvent.MouseButtonRelease:
            self._stroke = None
        elif kind == QEvent.MouseMove:
            if not event.buttons():
                self._stroke = None          # a release that went astray
            elif self._stroke is not None:
                self._dragged(event)
        return False

    def _pressed(self, event) -> bool:
        erasing = (event.button() == Qt.RightButton
                   or bool(event.modifiers() & Qt.ControlModifier))
        self._stroke = "erase" if erasing else "paint"
        if not erasing:
            return False        # the view's own click signal paints it
        key = self.board.key_at(event.position())
        if key is not None:
            self._erase(key)
        return True

    def _dragged(self, event) -> None:
        key = self.board.key_at(event.position())
        if key is None:
            return
        if self._stroke == "erase":
            self._erase(key)
        else:
            self._paint(key)

    # -- the scheme list ------------------------------------------------
    def _refresh_schemes(self, select: int | None = None) -> None:
        """Redraw the list of schemes, marking the one in use.

        Rebuilt whole rather than patched: the rows carry a count that
        any other screen can have changed, and the list is five rows
        long on a bad day.
        """
        row = self.store.active if select is None else select
        last = len(self.store.schemes) - 1
        # The rebuild moves the current row through 0 and -1 on its way,
        # and each of those would read as somebody switching scheme.
        self.schemes.blockSignals(True)
        self.schemes.clear()
        for index, scheme in enumerate(self.store.schemes):
            mark = "●" if index == self.store.active else "○"
            self.schemes.addItem(
                f"{mark}  {scheme.name}   —   {len(scheme.lighting)} lit")
        self.schemes.setCurrentRow(max(0, min(row, last)))
        self.schemes.blockSignals(False)

    def _read_scheme(self) -> None:
        """Take the painted layout from the scheme now in use."""
        self.painted = {
            index: _colour_of(rgb)
            for index, rgb in self.store.current.lighting.items()
        }
        self.stale.clear()
        self._choose_none()
        self._refresh()

    def _scheme_chosen(self, row: int) -> None:
        """Switch to the scheme under the cursor.

        Switching is replaying, because the keyboard has no command for
        it (docs/PROTOCOL.md §10) -- but the replaying belongs to the
        profiles screen and the window. What happens here is that the
        work in hand is written out first, so the scheme being left
        keeps the painting that was done on it.
        """
        if not 0 <= row < len(self.store.schemes) or row == self.store.active:
            return
        self._stop()
        if self._save.isActive():
            self._save_now()
        self.store.active = row
        self._persist()
        self._read_scheme()
        self._refresh_schemes(row)
        self.status.emit(f"using {self.store.current.name}")
        self.scheme_changed.emit(self.store.current)

    def _new_scheme(self) -> None:
        name, ok = QInputDialog.getText(self, "New scheme", "Name")
        name = name.strip() if ok else ""
        if name:
            self._add_scheme(name)

    def _add_scheme(self, name: str) -> None:
        scheme = self.store.add(name)
        self._persist()
        self._refresh_schemes()
        if scheme.name == name:
            self.status.emit(f"added {scheme.name}")
        else:
            # The store renames round a collision rather than refusing
            # one, which is right for adding and the wrong thing to do
            # without saying so.
            self.status.emit(f"added {scheme.name} — {name} was taken")

    def _rename_scheme(self) -> None:
        row = self.schemes.currentRow()
        if not 0 <= row < len(self.store.schemes):
            return
        name, ok = QInputDialog.getText(
            self, "Rename", "Name", text=self.store.schemes[row].name)
        name = name.strip() if ok else ""
        if name:
            self._rename_scheme_to(row, name)

    def _rename_scheme_to(self, row: int, name: str) -> None:
        scheme = self.store.schemes[row]
        if name == scheme.name:
            return
        if name in [s.name for n, s in enumerate(self.store.schemes)
                    if n != row]:
            # Refused rather than renamed round the collision: somebody
            # typed this name on purpose, so quietly storing a different
            # one answers a question they did not ask.
            self.status.emit(f"there is already a scheme called {name}")
            return
        scheme.name = name
        self._persist()
        self._refresh_schemes(row)
        self.status.emit(f"renamed to {name}")

    def _delete_scheme(self) -> None:
        row = self.schemes.currentRow()
        if not 0 <= row < len(self.store.schemes):
            return
        if len(self.store.schemes) <= 1:
            # The store refuses this as well; refusing it here keeps the
            # answer in the status bar instead of a dialog nobody needs
            # to dismiss.
            self.status.emit("there has to be one scheme")
            return
        scheme = self.store.schemes[row]
        if scheme.lighting and not self._confirm(
                "Delete this scheme?",
                f"{scheme.name} holds {len(scheme.lighting)} painted "
                f"keys.\n\nThe keyboard keeps no copy of a scheme, so "
                f"this is the only one."):
            return
        self._drop_scheme(row)

    def _drop_scheme(self, row: int) -> None:
        gone = self.store.schemes[row].name
        was = self.store.current
        self.store.remove(row)
        # Identity, not equality: two schemes can hold exactly the same
        # thing and still be two schemes. The store clamps the active
        # index, which is right when the last row goes and wrong for
        # every row above the active one.
        switched = True
        for index, scheme in enumerate(self.store.schemes):
            if scheme is was:
                self.store.active = index
                switched = False
                break
        self._persist()
        if switched:
            self._stop()
            self._read_scheme()
        self._refresh_schemes()
        self.status.emit(
            f"deleted {gone}" + (f" — now using {self.store.current.name}"
                                 if switched else ""))

    def _export_scheme(self) -> None:
        row = self.schemes.currentRow()
        if not 0 <= row < len(self.store.schemes):
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Export a scheme", f"{self.store.schemes[row].name}.json",
            "Scheme files (*.json);;All files (*)")
        if name:
            self._export_scheme_to(row, Path(name))

    def _export_scheme_to(self, row: int, path: Path) -> None:
        """Write one scheme out, in the file the profiles screen reads.

        One scheme rather than the store's own file: the point is to
        hand somebody a configuration, not this machine's whole set and
        which of them was in use.
        """
        scheme = self.store.schemes[row]
        try:
            path.write_text(json.dumps(
                {"version": store.VERSION, "scheme": asdict(scheme)},
                indent=1, sort_keys=True))
        except OSError as exc:
            self.status.emit(f"could not write that file: {exc}")
            return
        self.status.emit(f"exported {scheme.name} to {path.name}")

    def _import_scheme(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Import a scheme", "",
            "Scheme files (*.json);;All files (*)")
        if name:
            self._import_scheme_from(Path(name))

    def _import_scheme_from(self, path: Path) -> None:
        """Read a scheme out of a file, as a scheme of its own.

        Always added, never merged into the one that shares its name: an
        import that replaced a scheme would destroy somebody's work with
        nothing to undo it with.
        """
        try:
            raw = json.loads(path.read_text())
            if raw.get("version") != store.VERSION:
                self.status.emit(
                    f"that file says version {raw.get('version')}, and "
                    f"this app reads version {store.VERSION}")
                return
            body = raw.get("scheme")
            if body is None:
                # Somebody handed over the app's own schemes.json,
                # meaning the scheme in it.
                found = raw.get("schemes") or []
                body = found[0] if found else None
            name = str(body.get("name") or "Imported")
            keymap = {int(k): [int(n) for n in v]
                      for k, v in (body.get("keymap") or {}).items()}
            lighting = {int(k): int(v)
                        for k, v in (body.get("lighting") or {}).items()}
            macros = {str(k): list(v)
                      for k, v in (body.get("macros") or {}).items()}
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            self.status.emit("that file is not a scheme this app can read")
            return
        scheme = self.store.add(name)
        scheme.keymap, scheme.lighting, scheme.macros = keymap, lighting, macros
        self._persist()
        self._refresh_schemes()
        self.status.emit(
            f"imported {scheme.name}, {len(lighting)} keys lit")

    def _confirm(self, title: str, question: str) -> bool:
        """Ask before something there is no way back from.

        A method of its own rather than the call written inline, so a
        test can answer it: an unanswered modal in a test run is a
        hang, which is worse to read than a failure.
        """
        return QMessageBox.question(
            self, title, question,
            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

    # -- the file -------------------------------------------------------
    def _persist(self) -> None:
        """Write the schemes now, and admit it if they will not write."""
        try:
            self.store.save()
        except OSError as exc:
            self.status.emit(f"could not save the schemes: {exc}")

    def _save_now(self) -> None:
        """Put the layout in the scheme file.

        A failed save must not take the screen with it: a full disk is
        a bad afternoon, not a reason to lose the work still on screen.
        """
        self._save.stop()
        self.store.current.lighting = {
            index: _rgb_of(colour)
            for index, colour in self.painted.items()
        }
        try:
            self.store.save()
        except OSError as exc:
            self.status.emit(f"could not save the layout: {exc}")

    def hideEvent(self, event) -> None:
        # Leaving the screen is a better moment to write than trusting
        # the timer to outlive the window it is parented to. Leaving the
        # window is not leaving the screen -- the rail keeps every page
        # alive -- but a closed window must not leave an animation
        # running, and this is the only notice of it there is.
        if self._save.isActive():
            self._save_now()
        if not self.isVisible() and self.window().isHidden():
            self._stop()
        super().hideEvent(event)

    def _export(self) -> None:
        if not self.painted:
            self.status.emit("nothing painted to export")
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Export the layout", "layout.json",
            "Layout (*.json);;All files (*)")
        if name:
            self._export_to(Path(name))

    def _export_to(self, path: Path) -> None:
        """Write the layout keyed by ident, with the effect on it.

        Not by LED index: a file somebody shares should name the keys
        the way the keycaps do, and the idents would survive a layout
        revision that renumbered the LEDs. The effect and the speed go
        with them because they are half of what this screen does, and a
        reader that only knows about keys ignores them.
        """
        by_index = {k.light_index: k for k in kb_layout.KEYS}
        keys = {
            by_index[index].ident: colour.name()
            for index, colour in self.painted.items()
            if index in by_index
        }
        try:
            path.write_text(json.dumps(
                {"version": self.FILE_VERSION, "keys": keys,
                 "effect": self.effect().name, "speed": self.speed.value()},
                indent=1, sort_keys=True))
        except OSError as exc:
            self.status.emit(f"could not write that file: {exc}")
            return
        self.status.emit(f"exported {len(keys)} keys to {path.name}")

    def _import(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Import a layout", "", "Layout (*.json);;All files (*)")
        if name:
            self._import_from(Path(name))

    def _import_from(self, path: Path) -> None:
        try:
            raw = json.loads(path.read_text())
            keys = raw["keys"]
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            self.status.emit("that file is not a layout this app can read")
            return
        if not isinstance(keys, dict):
            self.status.emit("that file is not a layout this app can read")
            return

        found: dict[int, QColor] = {}
        for ident, value in keys.items():
            key = kb_layout.BY_IDENT.get(str(ident))
            colour = _colour_from_file(value)
            # A file from another board, or one hand-edited past repair:
            # take the keys that do exist and say how many were left,
            # rather than refusing the whole layout over one of them.
            if key is not None and colour is not None:
                found[key.light_index] = colour
        if not found:
            self.status.emit("no keys in that file belong to this board")
            return

        # An import replaces the layout, so anything it does not mention
        # is owed a blanking the same as an erase.
        self.stale |= set(self.painted) - set(found)
        self.painted = found
        self.chosen = set(found)
        self.recent = _in_use_order(found)[:self.RECENT_COLOURS]
        self._take_effect(raw)
        self._show_recent()
        self._refresh()
        skipped = len(keys) - len(found)
        self.status.emit(
            f"imported {len(found)} keys from {path.name}"
            + (f", {skipped} skipped" if skipped else ""))

    def _take_effect(self, raw: dict) -> None:
        """Take the effect and the speed out of an imported layout.

        Both are optional: a file written before this screen had an
        effect list, or by hand, is still a layout. Anything this app
        does not recognise is left as it was rather than refused -- the
        keys are the point of the file.
        """
        name = raw.get("effect")
        if isinstance(name, str):
            found = protocol.EFFECT_BY_NAME.get(name.strip().lower())
            if found is not None and found in CUSTOM_EFFECTS:
                self._select_effect(found)
        speed = raw.get("speed")
        low, high = self.SPEED_RANGE
        if isinstance(speed, int) and low <= speed <= high:
            self.speed.setValue(speed)

    # -- the keyboard ---------------------------------------------------
    def _apply(self) -> None:
        """The vendor's Apply: this effect, this colour, these keys.

        The keys take the colour they are applied in, so the layout the
        scheme stores is what was last applied -- a replay from the
        profiles screen then puts the board back as it was left, which
        is as close to the keyboard remembering a custom layout as this
        protocol gets.
        """
        effect = self.effect()
        self._stop()
        if effect.mode in PRESS_REACTIVE:
            self.status.emit(
                f"{effect.name} lights a key as it is pressed, and the "
                f"presses never reach this app — so the host cannot play it")
            return
        targets = self._targets_now()
        if not targets and not self.stale:
            self.status.emit("nothing painted yet")
            return
        self.painted.update(targets)
        self._refresh()
        if effect.mode in STEADY:
            self._send_steady(effect, targets)
        elif self._covers_the_board(targets):
            self._send_builtin(effect, targets)
        else:
            self._start(effect, targets)

    @staticmethod
    def _covers_the_board(targets: dict[int, QColor]) -> bool:
        """Is every key on the board in this apply?"""
        return len(targets) >= len(kb_layout.KEYS)

    def _send_builtin(self, effect, targets: dict[int, QColor]) -> None:
        """Hand a whole-board effect to the firmware to run.

        The per-key path is seven keys a second. Spread over four keys
        that is a coarse animation; spread over all sixty-nine it is one
        refresh per key every ten seconds, which on the keyboard is not
        a slow effect but a still picture -- the screen looked broken
        because what it was doing could not be seen.

        The firmware has these same effects built in and runs them at
        its own speed. It lights the whole board and takes one colour,
        which is exactly the case this branch covers.
        """
        self._stop()
        colour = QColor(self._colour())
        # The vendor's wire value counts the other way: its slider is
        # the same scale this screen shows, and a bigger number there
        # means a faster turn.
        self.apply_effect.emit(
            effect.mode, colour.red(), colour.green(), colour.blue(),
            self.BUILTIN_BRIGHTNESS, self.speed.value())
        self._wire = {index: colour for index in targets}
        self.board.colours = self._wire
        self.board.update()
        self.status.emit(
            f"{effect.name} is running on the keyboard itself, over the "
            f"whole board — the firmware plays it, so it keeps going "
            f"after this app closes")

    def _send_steady(self, effect, targets: dict[int, QColor]) -> None:
        """Put a frame that never changes on the keyboard, once."""
        play = PLAYERS[effect.mode]
        for index, colour in targets.items():
            self.apply_key.emit(index, play(index, 0.0, colour))
        blanked = len(self.stale)
        self._blank_stale()
        said = f"{effect.name}: {len(targets)} keys…"
        if blanked:
            said = f"{effect.name}: {len(targets)} keys, blanking {blanked}…"
        self.status.emit(said)

    def _start(self, effect, targets: dict[int, QColor]) -> None:
        """Run an effect on the host, over the keys it was applied to."""
        self._running = effect
        self._targets = targets
        self._phase = 0.0
        self._sent.clear()
        self._blank_stale()
        self._draw()
        self._frames.start(self.FRAME_MS)
        self._sender.start(self.SEND_INTERVAL)
        self.status.emit(
            f"{effect.name} is running on {len(targets)} keys from the "
            f"host — Stop ends it, and so does closing the app")

    def _stop_clicked(self) -> None:
        if self._running is None:
            self.status.emit("nothing is running")
            return
        name = self._running.name
        self._stop()
        self.status.emit(
            f"stopped {name} — the keys keep the colour they were last sent")

    def _stop(self) -> None:
        """Halt the animation, and put the painted layout back on show.

        The keys keep whatever they were last told, as they do when the
        Music screen stops: blacking out the board would be another
        sixty-nine writes, which is ten seconds of wire for the sake of
        a tidiness nobody asked for.
        """
        self._frames.stop()
        self._sender.stop()
        self._running = None
        self._targets = {}
        self._wire = {}
        self._sent.clear()
        self.board.colours = self.painted
        self.board.update()

    def _tick(self) -> None:
        """One frame: move the phase on and redraw."""
        self._phase = (self._phase + self._step()) % 1.0
        self._draw()

    def _step(self) -> float:
        """How much of a turn one frame covers, at the speed set now."""
        return self.FRAME_MS / self._turn_ms(self.speed.value())

    def _draw(self) -> None:
        play = PLAYERS[self._running.mode]
        self._wire = {index: play(index, self._phase, colour)
                      for index, colour in self._targets.items()}
        self.board.colours = self._wire
        self.board.update()

    def _send_frame(self) -> None:
        """Spend the wire's budget on the keys that have moved most.

        The screen draws twenty-five frames a second and the keyboard
        takes about seven keys in the same second, so most of a frame
        never goes out. Sending the largest changes first is what keeps
        the board recognisably the animation rather than a lagging copy
        of one.
        """
        moved = []
        for index, colour in self._wire.items():
            before = self._sent.get(index)
            gap = 255 if before is None else _colour_gap(before, colour)
            if gap >= self.COLOUR_STEP:
                moved.append((gap, index, colour))
        moved.sort(key=lambda row: row[0], reverse=True)
        for _, index, colour in moved[:self.KEY_BUDGET]:
            self._sent[index] = QColor(colour)
            self.apply_key.emit(index, QColor(colour))

    def _blank_stale(self) -> None:
        """Black out the keys rubbed out since the last send."""
        dark = QColor(0, 0, 0)
        for light_index in sorted(self.stale):
            self.apply_key.emit(light_index, dark)
        self.stale.clear()

    def _reset(self) -> None:
        """The vendor's Reset: an empty board and the defaults back.

        The keys on the keyboard are still lit, so they are owed a
        blanking -- which the next Apply pays, the same as an erase.
        """
        self._stop()
        self._clear()
        self._choose_none()
        self.speed.setValue(self.DEFAULT_SPEED)
        self.effects.setCurrentRow(0)
        self.status.emit("reset — Apply blanks the keys that were lit")

    def _send(self) -> None:
        """Send the painted layout as it stands, with no effect on it.

        What this screen did before it had an effect list, and still
        what a scheme replay amounts to: every painted key its colour,
        every rubbed-out one black.
        """
        if not self.painted and not self.stale:
            self.status.emit("nothing painted yet")
            return
        for light_index, colour in self.painted.items():
            self.apply_key.emit(light_index, colour)
        blanked = len(self.stale)
        self._blank_stale()
        said = f"sending {len(self.painted)} keys…"
        if blanked:
            said = (f"sending {len(self.painted)} keys, "
                    f"blanking {blanked}…")
        self.status.emit(said)


def _colour_gap(first: QColor, second: QColor) -> int:
    """How far apart two colours are, as the largest channel's move."""
    return max(abs(first.red() - second.red()),
               abs(first.green() - second.green()),
               abs(first.blue() - second.blue()))


def _in_use_order(painted: dict[int, QColor]) -> list[QColor]:
    """The distinct colours of a layout, most used first.

    Which is the order a palette wants: the colour on half the board is
    the one being reached for, not the one on a single key.
    """
    counted: dict[str, tuple[QColor, int]] = {}
    for colour in painted.values():
        name = colour.name()
        held, count = counted.get(name, (colour, 0))
        counted[name] = (held, count + 1)
    return [colour for colour, _ in
            sorted(counted.values(), key=lambda pair: -pair[1])]
