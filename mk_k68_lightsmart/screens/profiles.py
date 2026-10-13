"""The user-light screen: pick keys, pick a colour, paint them one by one."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QPainter,
    QPen,
    QRadialGradient,
)
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import layout as kb_layout
from .. import store as scheme_store
from ..gui import KeyboardView, caption, ghost_button, primary_button
from .common import _card, _heading, _list, _note


def _colour_of(rgb: int) -> QColor:
    """A stored colour. The scheme file keeps 0xRRGGBB integers because
    it is JSON and a QColor is not."""
    return QColor((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)


def _rgb_of(colour: QColor) -> int:
    return (colour.red() << 16) | (colour.green() << 8) | colour.blue()


def _toggle(text: str) -> QCheckBox:
    """One of the group selectors, in the chrome the screens wear.

    The shared chrome has no checkbox: the two screens that want one
    want it for different things, and neither is worth a third module.
    """
    box = QCheckBox(text)
    box.setStyleSheet(
        "QCheckBox { color:#cfe3ff; font-size:11px; spacing:4px; }"
        "QCheckBox::indicator { width:11px; height:11px;"
        " border:1px solid #0d3a6b; border-radius:2px; background:#0a2444; }"
        "QCheckBox::indicator:checked { background:#10a9fd;"
        " border-color:#10a9fd; }"
    )
    return box


def _filler() -> QVBoxLayout:
    """Empty space that takes whatever is left over.

    A card lays its rows out evenly, which on one holding a fixed-size
    board puts the slack in a gap between every row. This puts it all
    at the bottom instead.
    """
    space = QVBoxLayout()
    space.addStretch()
    return space


def _channel() -> QSpinBox:
    """One of the R, G and B boxes, in the shared combo box's chrome."""
    box = QSpinBox()
    box.setRange(0, 255)
    box.setFixedWidth(52)
    box.setButtonSymbols(QSpinBox.NoButtons)
    box.setStyleSheet(
        "QSpinBox { background:#0a2444; color:#cfe3ff;"
        " border:1px solid #0d3a6b; border-radius:3px; padding:3px;"
        " font-size:11px; }")
    return box


class ColourWheel(QWidget):
    """The vendor's colour ring, which Qt has nothing of its own for.

    Hue runs round the ring and saturation out from the hole, which is
    the pair the vendor's own wheel offers; the hole holds the colour in
    hand. It is here rather than behind a dialog for the reason the
    vendor put it on the page: painting a board a key at a time is
    already tiring, and a modal between every key is what makes it
    unbearable.

    The ring is two fills rather than a pixel loop. White at alpha *a*
    over a full-value colour *is* that colour at saturation ``1 - a``,
    so a hue gradient under a white wash draws the whole wheel in two
    passes and stays smooth at any size.
    """

    picked = Signal(QColor)

    #: The hole in the middle, as a fraction of the outer radius.
    HOLE = 0.46

    #: How saturated the rim is. Not zero: a ring that fades to white
    #: at the edge spends its outer third on colours a backlight cannot
    #: tell apart.
    RIM_SATURATION = 0.45

    #: Wedges in the hue gradient. A conical gradient interpolates in
    #: RGB, so too few stops cut the corners off the hue circle and
    #: lose the colours between them.
    STOPS = 24

    def __init__(self, size: int = 132, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._colour = QColor("#ff0000")

    # -- the colour it is showing ---------------------------------------
    def colour(self) -> QColor:
        return QColor(self._colour)

    def show_colour(self, colour: QColor) -> None:
        """Move the marker without emitting anything.

        For a colour chosen elsewhere on the page -- a preset, the
        number boxes, the dialog -- which the wheel has to agree with
        and must not then re-announce.
        """
        self._colour = QColor(colour)
        self.update()

    # -- geometry --------------------------------------------------------
    def _centre(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def _outer(self) -> float:
        return min(self.width(), self.height()) / 2

    def _spot(self) -> QPointF:
        """Where the marker sits for the colour in hand."""
        outer = self._outer()
        inner = outer * self.HOLE
        hue, saturation, _value, _alpha = self._colour.getHsvF()
        # A grey has no hue of its own and reports -1. Red is where its
        # saturation ran out, so that is where the marker waits.
        angle = math.radians(max(hue, 0.0) * 360)
        reach = (1.0 - max(saturation, self.RIM_SATURATION)) / (
            1.0 - self.RIM_SATURATION)
        radius = inner + reach * (outer - inner)
        centre = self._centre()
        return QPointF(centre.x() + radius * math.cos(angle),
                       centre.y() - radius * math.sin(angle))

    # -- drawing ---------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        centre, outer = self._centre(), self._outer()
        p.setPen(Qt.NoPen)

        hue = QConicalGradient(centre, 0)
        for n in range(self.STOPS + 1):
            step = n / self.STOPS
            hue.setColorAt(step, QColor.fromHsvF(min(step, 0.9999), 1.0, 1.0))
        p.setBrush(hue)
        p.drawEllipse(centre, outer, outer)

        wash = QRadialGradient(centre, outer)
        wash.setColorAt(0.0, QColor(255, 255, 255, 0))
        wash.setColorAt(self.HOLE, QColor(255, 255, 255, 0))
        wash.setColorAt(1.0, QColor(
            255, 255, 255, int((1.0 - self.RIM_SATURATION) * 255)))
        p.setBrush(wash)
        p.drawEllipse(centre, outer, outer)

        # The hole, and the colour in hand sitting in it, which is what
        # the vendor's wheel shows there.
        inner = outer * self.HOLE
        p.setBrush(QColor("#0a1024"))
        p.drawEllipse(centre, inner, inner)
        p.setBrush(self._colour)
        p.drawEllipse(centre, inner * 0.62, inner * 0.62)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawEllipse(self._spot(), 4, 4)

    # -- picking ---------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        self._take(event.position())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons():
            self._take(event.position())

    def _take(self, pos: QPointF) -> None:
        """Read the colour under a point, if the point is on the ring.

        The hole and the corners outside the circle are not colours. A
        drag that wanders off the ring holds the last one rather than
        jumping, which is the behaviour of every wheel anybody has used.
        """
        centre, outer = self._centre(), self._outer()
        inner = outer * self.HOLE
        across = pos.x() - centre.x()
        up = centre.y() - pos.y()
        distance = math.hypot(across, up)
        if not inner <= distance <= outer:
            return
        hue = (math.degrees(math.atan2(up, across)) % 360) / 360
        reach = (distance - inner) / (outer - inner)
        saturation = 1.0 - reach * (1.0 - self.RIM_SATURATION)
        self._colour = QColor.fromHsvF(min(hue, 0.9999), saturation, 1.0)
        self.update()
        self.picked.emit(self.colour())


# ----------------------------------------------------------------------
# 3. User -- user light, a colour per key
# ----------------------------------------------------------------------
class UserLightPage(QWidget):
    """Pick keys, pick a colour, apply it to the lot.

    The vendor's User Light. Its screen is a scheme list, the board with
    three group shortcuts over it, and a colour panel: wheel, R/G/B
    boxes and a row of presets, with reset and apply. The same controls
    are here, rearranged for a window that is wider than it is tall.

    The apply goes out a key at a time, because that is all the
    firmware takes -- mode ``0x80`` carries one light index
    (docs/PROTOCOL.md §5), and the vendor's own manual says a single
    apply can only set one key. So a selection of thirty is thirty
    transactions, which the window's handler and the worker coalesce by
    key.

    The keyboard keeps none of it. There is no command to store a
    user-light scheme on the device, so the colours live in the scheme
    file and are sent again; on the keyboard they last until something
    else lights the keys.

    The scheme list is the vendor's own on this screen, and it carries
    the rest of a scheme with it. What it cannot carry is a switch
    command: the driver has none. Reading its idle-tick dispatcher, the
    cases that change scheme send the keyboard nothing at all and
    re-send the keymap and the lighting instead (docs/PROTOCOL.md §10).
    So switching *is* replaying, which is why this screen asks the
    window to send and sends nothing itself.
    """

    #: The three group shortcuts the vendor's own screen offers, as
    #: predicates over ``kb_layout`` rather than lists of LED indices,
    #: so they stay right if the layout is ever re-transcribed.
    GROUPS = (
        ("Select all", lambda k: True),
        ("Numbers", lambda k: k.ident.isdigit()),
        ("WASD", lambda k: k.ident in ("W", "A", "S", "D")),
    )

    #: The vendor's fourteen presets, sampled off the colour panel in
    #: the manual rather than guessed at from their names -- its purple
    #: is not Qt's, and its last two are the two a backlight uses most.
    PRESETS = (
        "#ff0000", "#ff00fc", "#7e00ff", "#0000ff", "#0089fd", "#00fcff",
        "#339933", "#00ff00", "#b4ff00", "#fff600", "#ffa800", "#ea5413",
        "#ffffff", "#000000",
    )

    #: How many presets fit across the colour card.
    PRESETS_PER_ROW = 7

    status = Signal(str)
    #: The scheme that has just become the active one. Every screen
    #: draws some part of a scheme, and they share one store object, so
    #: a switch changes what they are all looking at -- this is how
    #: they are told to look again.
    scheme_changed = Signal(object)
    #: Ask the window to put the active scheme back on the keyboard.
    #: The flag is whether the keymap goes with it, because the two
    #: halves differ in what being wrong costs: the lighting is gone at
    #: the next effect, a keymap outlives the power cycle.
    replay_requested = Signal(bool)
    apply_key = Signal(int, object)          # light_index, QColor

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = scheme_store.shared()
        self._colour = QColor("#ff0000")
        #: light_index -> QColor, the layout as it stands. Seeded from
        #: the scheme, which is the only place one survives.
        self.painted: dict[int, QColor] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("User light"))
        outer.addWidget(_note(
            "A colour goes out one key at a time — the firmware takes "
            "one light index per command, and the vendor's manual says "
            "the same. So applying to thirty keys is thirty "
            "transactions. The keyboard remembers none of it: there is "
            "no command to store a user-light scheme on the device, so "
            "the colours are kept in the scheme file and sent again, "
            "and on the keyboard they last until something else lights "
            "the keys."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_schemes())
        body.addWidget(self._build_board())
        body.addWidget(self._build_colours())
        body.addStretch()
        outer.addLayout(body, 1)
        outer.addStretch()

        self._load_painted()
        self._use_colour(self._colour)
        self._refresh(self.store.active)

    # -- the three cards -------------------------------------------------
    def _build_schemes(self) -> QWidget:
        """The scheme list, which the vendor puts on this screen too."""
        self.list = _list()
        # Wrapped rather than scrolled sideways: the card is narrow
        # enough that every row would otherwise end mid-count, and a
        # horizontal scrollbar costs more height than the second line.
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._selected)
        # Double-click as a second way in: it is how the vendor's list
        # behaves, and it is the one action worth not walking to a
        # button for.
        self.list.itemDoubleClicked.connect(lambda _item: self._use())

        # A grid rather than two rows: the vendor's own toolbar is five
        # icons in the width of the list, and spelling them out in
        # English needs the second column to stay that narrow.
        buttons = QGridLayout()
        buttons.setSpacing(6)
        for n, (text, slot) in enumerate((
                ("New", self._new), ("Rename", self._rename),
                ("Delete", self._delete), ("Duplicate", self._duplicate),
                ("Import…", self._import), ("Export…", self._export))):
            buttons.addWidget(ghost_button(text, slot), n // 2, n % 2)

        self.summary = QLabel()
        self.summary.setStyleSheet("color:#cfe3ff; font-size:12px;")
        self.contents = QLabel()
        self.contents.setWordWrap(True)
        self.contents.setStyleSheet("color:#9fb3d0; font-size:11px;")
        self.contents.setToolTip(
            "Macros are kept in the scheme and never sent: the event "
            "encoding is still open.")

        self.with_keymap = QCheckBox("Write the keymap too")
        self.with_keymap.setStyleSheet("color:#9fb3d0; font-size:11px;")
        self.with_keymap.setChecked(True)
        self.with_keymap.setToolTip(
            "A keymap is written to the keyboard and outlives a power "
            "cycle; the lighting is gone at the next effect.")

        use_row = QHBoxLayout()
        use_row.addWidget(primary_button("Use this scheme", self._use))
        use_row.addWidget(ghost_button("Replay", self._replay))
        use_row.addStretch()

        card = _card(
            caption("Configuration schemes"), self.list, buttons,
            self.summary, self.contents, self.with_keymap, use_row,
        )
        card.setFixedWidth(270)
        return card

    def _build_board(self) -> QWidget:
        """The board, with the vendor's three group shortcuts over it."""
        self.board = KeyboardView(scale=0.68)
        self.board.key_clicked.connect(self._pick)
        self.board.hover_text = self._hover_text
        # The view only reads these, so it is handed the page's own set
        # and dict rather than copies of them.
        self.chosen: set[int] = self.board.chosen

        group_row = QHBoxLayout()
        group_row.setSpacing(12)
        #: Each group box beside the predicate that fills it.
        self.groups: list[tuple[QCheckBox, object]] = []
        for name, match in self.GROUPS:
            box = _toggle(name)
            box.toggled.connect(self._group(match))
            self.groups.append((box, match))
            group_row.addWidget(box)
        group_row.addStretch()
        self.picked = caption("")
        group_row.addWidget(self.picked)

        return _card(
            group_row, self.board,
            caption("Click a key to pick it, and again to let it go. A "
                    "picked key wears a ring."),
            _filler(),
        )

    def _build_colours(self) -> QWidget:
        """Wheel, numbers and presets -- the vendor's colour panel."""
        self.wheel = ColourWheel()
        self.wheel.picked.connect(self._use_colour)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch()
        wheel_row.addWidget(self.wheel)
        wheel_row.addStretch()

        #: R, G and B, in that order.
        self.channels: list[QSpinBox] = []
        number_row = QHBoxLayout()
        number_row.setSpacing(4)
        for name in ("R", "G", "B"):
            box = _channel()
            box.valueChanged.connect(self._typed)
            self.channels.append(box)
            number_row.addWidget(caption(f"{name}:"))
            number_row.addWidget(box)
        number_row.addStretch()

        presets = QGridLayout()
        presets.setSpacing(5)
        for n, value in enumerate(self.PRESETS):
            presets.addWidget(self._swatch(QColor(value)),
                              n // self.PRESETS_PER_ROW,
                              n % self.PRESETS_PER_ROW)
        presets.setColumnStretch(self.PRESETS_PER_ROW, 1)

        actions = QHBoxLayout()
        actions.addWidget(primary_button("Apply", self._apply))
        actions.addWidget(ghost_button("Reset", self._reset))
        actions.addWidget(ghost_button("Pick…", self._pick_colour))
        actions.addStretch()

        card = _card(
            caption("Colour"), wheel_row, number_row,
            caption("Presets"), presets, actions, _filler(),
        )
        card.setFixedWidth(300)
        return card

    def _swatch(self, colour: QColor) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(30, 24)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(colour.name())
        button.setStyleSheet(
            f"QPushButton {{ background:{colour.name()};"
            " border:1px solid #0d3a6b; border-radius:3px; }"
            "QPushButton:hover { border:1px solid #10a9fd; }")
        button.clicked.connect(lambda: self._use_colour(colour))
        return button

    # -- the colour in hand ----------------------------------------------
    def _use_colour(self, colour: QColor) -> None:
        """One colour for the whole panel, however it was chosen.

        Every control here shows the same colour, so each of them hands
        it through this and none of them writes to the others: the
        number boxes would otherwise feed the wheel feeding the number
        boxes.
        """
        self._colour = QColor(colour)
        self.wheel.show_colour(self._colour)
        for box, value in zip(self.channels, (
                self._colour.red(), self._colour.green(),
                self._colour.blue())):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)

    def _typed(self, _value: int) -> None:
        self._use_colour(QColor(*[box.value() for box in self.channels]))

    def _pick_colour(self) -> None:
        """The system picker, for a colour the ring cannot reach.

        The wheel is full value by construction, so anything darker
        than the rim -- a dim red, a grey -- has to come from here.
        """
        chosen = QColorDialog.getColor(self._colour, self, "Colour")
        if chosen.isValid():
            self._use_colour(chosen)

    # -- picking keys ----------------------------------------------------
    def _pick(self, key: kb_layout.Key) -> None:
        """Take a key into the selection, or let it go again.

        A toggle rather than a modifier-held click: the vendor's screen
        has no modifiers, and the three group boxes are the only other
        way to pick more than one.
        """
        if key.light_index in self.chosen:
            self.chosen.discard(key.light_index)
        else:
            self.chosen.add(key.light_index)
        # The view rings the key it was last clicked on as well, which
        # would leave a ring on a key just let go of.
        self.board.selected = None
        self._redraw_board()

    def _group(self, match):
        def pick(on: bool) -> None:
            for key in kb_layout.KEYS:
                if match(key):
                    if on:
                        self.chosen.add(key.light_index)
                    else:
                        self.chosen.discard(key.light_index)
            self.board.selected = None
            self._redraw_board()
        return pick

    def _hover_text(self, key: kb_layout.Key) -> str:
        """What a key says about itself, which at this size is worth
        saying: the board is drawn small to leave room for the panel."""
        colour = self.painted.get(key.light_index)
        said = f"{key.ident} — {colour.name() if colour else 'unpainted'}"
        return said + (", picked" if key.light_index in self.chosen else "")

    def _redraw_board(self) -> None:
        self.board.colours = self.painted
        self.board.update()
        self.picked.setText(
            f"{len(self.chosen)} picked" if self.chosen else "none picked")
        self._show_groups()

    def _show_groups(self) -> None:
        """Tick a group's box when the whole group is picked.

        The boxes are a shortcut, not the state -- keys are picked on
        the board as well -- so a box left ticked over a group that is
        now half picked would be the one control here saying something
        untrue.
        """
        for box, match in self.groups:
            whole = {k.light_index for k in kb_layout.KEYS if match(k)}
            box.blockSignals(True)
            box.setChecked(bool(whole) and whole <= self.chosen)
            box.blockSignals(False)

    # -- painting --------------------------------------------------------
    def _apply(self) -> None:
        """Put the colour on every picked key, one transaction each.

        The loop is the firmware's, not a choice: mode ``0x80`` carries
        a single light index, and the vendor's manual says as much.
        """
        if not self.chosen:
            self.status.emit("pick a key on the board first")
            return
        for light_index in sorted(self.chosen):
            self.painted[light_index] = QColor(self._colour)
            self.apply_key.emit(light_index, QColor(self._colour))
        self._remember()
        self.status.emit(
            f"applying {self._colour.name()} to "
            f"{len(self.chosen)} key(s)…")

    def _reset(self) -> None:
        """Unpaint the board, which is two things and not one.

        The scheme forgets the colours, and every key carrying one is
        sent black. Forgetting without sending would leave the keyboard
        lit in a layout no longer written down anywhere, which is the
        state that is hardest to get back out of.
        """
        self.chosen.clear()
        if not self.painted:
            self._redraw_board()
            self.status.emit("nothing painted")
            return
        if not self._confirm(
                "Reset the lighting?",
                f"{len(self.painted)} painted key(s) go back to black, "
                f"on the keyboard and in {self.store.current.name}.\n\n"
                f"The keyboard keeps no copy of a layout, so this is "
                f"the only one."):
            return
        dark = QColor(0, 0, 0)
        for light_index in sorted(self.painted):
            self.apply_key.emit(light_index, dark)
        gone = len(self.painted)
        self.painted.clear()
        self._remember()
        self.status.emit(f"reset {gone} key(s) to black")

    def _remember(self) -> None:
        """Put the layout in the scheme, write it, and redraw.

        Every apply saves: the file is a couple of kilobytes, and what
        is in it is the only copy of work done by hand.
        """
        self.store.current.lighting = {
            index: _rgb_of(colour)
            for index, colour in self.painted.items()
        }
        self._persist()
        self._redraw_board()
        # The rows count the lit keys, and one of them just changed.
        self._refresh()

    def _load_painted(self) -> None:
        """Take the painted layout out of the scheme in use.

        Rebound rather than patched, and the selection dropped with it:
        a ring round a key of the scheme being left says nothing about
        the one being arrived at.
        """
        self.painted = {
            index: _colour_of(rgb)
            for index, rgb in self.store.current.lighting.items()
        }
        self.chosen.clear()
        self._redraw_board()

    # -- the list --------------------------------------------------------
    @property
    def schemes(self) -> list[str]:
        """The names, in the order the list draws them.

        The bundles themselves belong to the store and are shared; what
        this screen has of its own is the view of them.
        """
        return self.store.names()

    def showEvent(self, event) -> None:
        # Another screen may have remapped or painted something since
        # this one was last looked at, and the custom screen edits the
        # very layout drawn here. Cheap enough to re-read every time.
        super().showEvent(event)
        self._load_painted()
        self._refresh()

    def _refresh(self, select: int | None = None) -> None:
        """Redraw the list, keeping or moving the selection.

        Rebuilt whole rather than patched: the rows carry counts that
        any other screen can have changed, and a row-by-row update
        would be a cache to keep honest for no gain on a list of five.
        """
        row = self.list.currentRow() if select is None else select
        last = len(self.store.schemes) - 1
        # The rebuild moves the current row through 0 and -1 on its
        # way, and each of those would redraw the side panel from a
        # selection nobody made.
        self.list.blockSignals(True)
        self.list.clear()
        for index, scheme in enumerate(self.store.schemes):
            mark = "●" if index == self.store.active else "○"
            self.list.addItem(
                f"{mark}  {scheme.name}   —   {self._summary(scheme)}")
        self.list.setCurrentRow(max(0, min(row, last)))
        self.list.blockSignals(False)
        self._selected(self.list.currentRow())

    @staticmethod
    def _summary(scheme: scheme_store.Scheme) -> str:
        """The three things a scheme can hold, counted.

        Names alone tell nobody which scheme is which, least of all a
        week later, and these three numbers are the whole of what a
        scheme is.
        """
        return (f"{len(scheme.keymap)} remapped, "
                f"{len(scheme.lighting)} lit, "
                f"{len(scheme.macros)} macros")

    def _selected(self, row: int) -> None:
        if not 0 <= row < len(self.store.schemes):
            self.summary.setText("No scheme selected")
            self.contents.setText("")
            return
        scheme = self.store.schemes[row]
        in_use = "   (in use)" if row == self.store.active else ""
        self.summary.setText(f"{scheme.name}{in_use}")
        self.contents.setText(
            f"{len(scheme.keymap)} keys remapped\n"
            f"{len(scheme.lighting)} keys painted\n"
            f"{len(scheme.macros)} macros, kept here and not sent"
        )

    # -- new, rename, delete, duplicate ---------------------------------
    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New scheme", "Name")
        name = name.strip() if ok else ""
        if not name:
            return
        scheme = self.store.add(name)
        self._persist()
        self._refresh(len(self.store.schemes) - 1)
        self._announce("added", scheme.name, name)

    def _rename(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        scheme = self.store.schemes[row]
        name, ok = QInputDialog.getText(
            self, "Rename", "Name", text=scheme.name)
        name = name.strip() if ok else ""
        if not name or name == scheme.name:
            return
        if name in [s.name for i, s in enumerate(self.store.schemes)
                    if i != row]:
            # Refused rather than renamed round the collision, which is
            # what adding does: somebody typed this name on purpose, so
            # quietly storing a different one answers a question they
            # did not ask.
            QMessageBox.warning(
                self, "That name is taken",
                f"There is already a scheme called {name}.")
            return
        scheme.name = name
        self._persist()
        self._refresh(row)
        self.status.emit(f"renamed to {name}")

    def _delete(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        if len(self.store.schemes) <= 1:
            # The store refuses this as well; refusing it here keeps
            # the answer in the status bar instead of a dialog nobody
            # needs to dismiss.
            self.status.emit("there has to be one scheme")
            return
        scheme = self.store.schemes[row]
        if self._holds_anything(scheme) and not self._confirm(
                "Delete this scheme?",
                f"{scheme.name} holds {self._summary(scheme)}.\n\n"
                f"The keyboard keeps no copy of a scheme, so this is "
                f"the only one."):
            return
        gone = scheme.name
        was = self.store.current
        self.store.remove(row)
        switched = self._follow(was)
        self._persist()
        self._refresh(row)
        if switched:
            self.status.emit(
                f"deleted {gone} — now using {self.store.current.name}")
            self._switched()
        else:
            self.status.emit(f"deleted {gone}")

    def _duplicate(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        copy = self.store.duplicate(row)
        self._persist()
        self._refresh(len(self.store.schemes) - 1)
        self.status.emit(f"copied to {copy.name}")

    def _follow(self, was: scheme_store.Scheme) -> bool:
        """Keep using the scheme that was in use, or say it is gone.

        ``Store.remove`` clamps the active index, which is right when
        the last row goes and wrong for every row above the active one:
        the index would then land on a neighbour and the keyboard would
        be carrying a scheme nobody chose. Identity, not equality --
        two schemes can hold exactly the same thing and still be two
        schemes.
        """
        for index, scheme in enumerate(self.store.schemes):
            if scheme is was:
                self.store.active = index
                return False
        return True

    # -- switching, which means replaying -------------------------------
    def _use(self) -> None:
        """Make the selected scheme the active one and put it on.

        Re-using the scheme already in use is not refused: sending it
        again is the whole of what switching does, and the keyboard may
        have been unplugged, re-lit or reset since.
        """
        row = self.list.currentRow()
        if row < 0:
            return
        self.store.active = row
        self._persist()
        self._refresh(row)
        self.status.emit(f"using {self.store.current.name}")
        self._switched()

    def _replay(self) -> None:
        """Send the active scheme again without changing which it is.

        Worth its own button because the keyboard forgets: any effect
        started on another screen overwrites the painted layout, so a
        scheme is only ever as applied as the last thing sent.
        """
        self.status.emit(f"replaying {self.store.current.name}")
        self.replay_requested.emit(self.with_keymap.isChecked())

    def _switched(self) -> None:
        """Tell the rest of the app the active scheme moved.

        Two signals because two different things have to happen, and
        only one of them touches the keyboard: the screens re-read the
        scheme they draw, and the window replays it. This screen draws
        one too, so it re-reads here rather than waiting to be shown.
        """
        self._load_painted()
        self.scheme_changed.emit(self.store.current)
        self.replay_requested.emit(self.with_keymap.isChecked())

    # -- one scheme as a file -------------------------------------------
    def _export(self) -> None:
        """Write the selected scheme out on its own.

        One scheme rather than the store's file: the point is to hand
        somebody a layout, not this machine's whole configuration and
        which of it was in use.
        """
        row = self.list.currentRow()
        if row < 0:
            return
        scheme = self.store.schemes[row]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export a scheme", f"{scheme.name}.json",
            "Scheme files (*.json);;All files (*)")
        if not path:
            return
        payload = {"version": scheme_store.VERSION,
                   "scheme": asdict(scheme)}
        try:
            Path(path).write_text(
                json.dumps(payload, indent=1, sort_keys=True))
        except OSError as exc:
            QMessageBox.warning(
                self, "Could not write that file", str(exc))
            return
        self.status.emit(f"exported {scheme.name} to {path}")

    def _import(self) -> None:
        """Read a scheme out of a file, as a scheme of its own.

        It is always added, never merged into the one that shares its
        name: an import that replaced a scheme would destroy somebody's
        work with nothing to undo it with. The store's own collision
        rule picks the free name, and the status line says which,
        because a rename nobody is told about is the same surprise in
        slower motion.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Import a scheme", "",
            "Scheme files (*.json);;All files (*)")
        if not path:
            return
        try:
            incoming = self._read_scheme(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Could not read that file", str(exc))
            return
        scheme = self.store.add(incoming.name)
        scheme.keymap = incoming.keymap
        scheme.lighting = incoming.lighting
        scheme.macros = incoming.macros
        self._persist()
        self._refresh(len(self.store.schemes) - 1)
        self._announce("imported", scheme.name, incoming.name)

    @staticmethod
    def _read_scheme(path: Path) -> scheme_store.Scheme:
        """Parse an exported scheme, or say why it is not one.

        Every field is coerced rather than trusted: this file was
        written somewhere else, and JSON has no integer keys, so the
        two maps come back keyed by strings whatever wrote them. A
        version this code does not know is refused rather than guessed
        at, which is what the store does with its own file.
        """
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError("That file does not hold a scheme.")
        if raw.get("version") != scheme_store.VERSION:
            raise ValueError(
                f"That file says version {raw.get('version')}, and "
                f"this app reads version {scheme_store.VERSION}.")
        body = raw.get("scheme")
        if body is None:
            # Somebody handed over the app's own schemes.json, meaning
            # the scheme in it. Taking the first is kinder than a
            # lecture about the difference between the two files.
            schemes = raw.get("schemes") or []
            body = schemes[0] if schemes else None
        if not isinstance(body, dict):
            raise ValueError("That file holds no scheme.")
        try:
            return scheme_store.Scheme(
                name=str(body.get("name") or "Imported"),
                keymap={int(k): [int(n) for n in v] for k, v
                        in (body.get("keymap") or {}).items()},
                lighting={int(k): int(v) for k, v
                          in (body.get("lighting") or {}).items()},
                macros={str(k): list(v) for k, v
                        in (body.get("macros") or {}).items()},
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"That scheme is malformed: {exc}") from exc

    # -- the small shared bits ------------------------------------------
    @staticmethod
    def _holds_anything(scheme: scheme_store.Scheme) -> bool:
        return bool(scheme.keymap or scheme.lighting or scheme.macros)

    def _confirm(self, title: str, question: str) -> bool:
        """Ask before something there is no way back from.

        A method of its own rather than the call written inline, so a
        test can answer it: an unanswered modal in a test run is a
        hang, which is worse to read than a failure.
        """
        return QMessageBox.question(
            self, title, question,
            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

    def _announce(self, verb: str, got: str, asked: str) -> None:
        """Say what happened, and say more when the name moved.

        The store renames round a collision instead of refusing one,
        which is the right behaviour for adding and the wrong thing to
        do without mentioning it.
        """
        if got == asked:
            self.status.emit(f"{verb} {got}")
        else:
            self.status.emit(f"{verb} {got} — {asked} was taken")

    def _persist(self) -> None:
        """Write the file now, and admit it in the status bar if it
        will not be written.

        Every edit here saves at once: the file is a couple of
        kilobytes, and the alternative is an unsaved-changes dialog
        over work nobody thinks of as a document. A failed save leaves
        the change on screen, so the message matters -- it is the only
        warning that the next start will not have it.
        """
        try:
            self.store.save()
        except OSError as exc:
            self.status.emit(f"could not save the schemes: {exc}")


#: What the window still calls this screen. The rail slot was a profile
#: manager before it was the vendor's user light, and the name outlived
#: the screen.
ProfilesPage = UserLightPage
