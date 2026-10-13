"""The custom-lighting screen: paint a whole board, send it key by key."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import layout as kb_layout
from .. import store
from ..gui import KeyboardView, caption, ghost_button, primary_button
from .common import _card, _heading, _note

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
# 4. Custom -- a whole layout, painted
# ----------------------------------------------------------------------
class CustomPage(QWidget):
    """Paint the whole board, then send it key by key.

    This one reaches the keyboard: the per-key colour command is the
    same one the Home screen uses. What it cannot do is *store* the
    result on the keyboard, because that needs the profile commands —
    so the layout is kept in the scheme file instead, and the app
    re-sends it.

    Painting is a stroke rather than a click per key: 69 keys one
    dialog at a time is why the vendor's own editor is tiring. The
    board widget only reports presses, so the drag is read here, off
    an event filter on the view.
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

    #: How long a change waits before it reaches the file. A stroke
    #: fires a change per mouse move and a group paints up to 69 keys
    #: at once; the scheme file is written whole, so the writes coalesce
    #: here rather than rewriting it per painted key.
    SAVE_DELAY_MS = 400

    #: How many colours the palette keeps: enough for a layout with a
    #: couple of accents, few enough to stay one row in a 330px card.
    RECENT_COLOURS = 7

    #: Bumped if an exported file stops being readable by this code.
    FILE_VERSION = 1

    apply_key = Signal(int, object)          # light_index, QColor
    status = Signal(str)

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
        #: Keys rubbed out since the last send. Only a colour ever goes
        #: to the keyboard, so an erased key stays lit until something
        #: sends it black -- these are what the next send blacks out.
        self.stale: set[int] = set()
        #: Colours used lately, newest first.
        self.recent: list[QColor] = []
        self._stroke: str | None = None

        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.setInterval(self.SAVE_DELAY_MS)
        self._save.timeout.connect(self._save_now)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("Custom lighting"))
        outer.addWidget(_note(
            "The colours go out one key at a time, which works and is "
            "what the Home screen already does. What this cannot do is "
            "make the keyboard remember the layout: storing one needs "
            "the profile commands, which the driver does not expose. So "
            "the layout is kept in the scheme file and sent again, and "
            "on the keyboard it lasts until something else lights the "
            "keys."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)
        self.board = KeyboardView()
        self.board.key_clicked.connect(self._paint)
        # Presses arrive as the signal above; everything else about the
        # mouse -- strokes, and the right-click that rubs a key out --
        # has to be read before the view sees it.
        self.board.installEventFilter(self)
        body.addWidget(_card(self.board), 1)

        self.swatch = QLabel()
        self.swatch.setFixedHeight(22)
        self._show_swatch()
        colour_row = QHBoxLayout()
        colour_row.addWidget(self.swatch, 1)
        colour_row.addWidget(ghost_button("Pick…", self._pick_colour))

        self._palette_row = QHBoxLayout()
        self._palette_row.setSpacing(5)

        groups = QGridLayout()
        groups.setSpacing(6)
        for n, name in enumerate(self.GROUPS):
            groups.addWidget(ghost_button(name, self._filler(name)),
                             n // 2, n % 2)

        actions = QHBoxLayout()
        actions.addWidget(primary_button("Send to the keyboard", self._send))
        actions.addWidget(ghost_button("Clear", self._clear))
        actions.addStretch()

        files = QHBoxLayout()
        files.addWidget(ghost_button("Import…", self._import))
        files.addWidget(ghost_button("Export…", self._export))
        files.addStretch()

        side = _card(
            caption("Colour"), colour_row,
            caption("Used lately"), self._palette_row,
            caption("Paint a group"), groups,
            caption("Drag across the board to paint a run of keys. "
                    "Right-click, or ctrl-click, to rub one out."),
            actions, files,
        )
        side.setFixedWidth(330)
        body.addWidget(side)
        outer.addLayout(body, 1)
        outer.addStretch()

        # The palette comes back with the layout: whatever is already on
        # the board is what the next edit most likely wants to match.
        self.recent = _in_use_order(self.painted)[:self.RECENT_COLOURS]
        self._show_recent()
        self._refresh()

    # -- the colour in hand ---------------------------------------------
    def _show_swatch(self) -> None:
        self.swatch.setStyleSheet(
            f"background:{self._colour().name()}; border:1px solid #0d3a6b;"
            " border-radius:3px;")

    def _pick_colour(self) -> None:
        chosen = QColorDialog.getColor(self._colour(), self, "Colour")
        if chosen.isValid():
            self._use(chosen)

    def _use(self, colour: QColor) -> None:
        # The page keeps its own colour once one is chosen here, so
        # painting does not change what the rest of the app is using.
        self._colour = lambda c=QColor(colour): c
        self._show_swatch()
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
        button.setFixedSize(26, 22)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(colour.name())
        button.setStyleSheet(
            f"QPushButton {{ background:{colour.name()};"
            " border:1px solid #0d3a6b; border-radius:3px; }"
            "QPushButton:hover { border:1px solid #10a9fd; }")
        button.clicked.connect(lambda: self._use(colour))
        return button

    # -- painting -------------------------------------------------------
    def _paint(self, key: kb_layout.Key) -> None:
        colour = QColor(self._colour())
        if self.painted.get(key.light_index) == colour:
            # A stroke reports every pixel it crosses, and most of those
            # land on a key that is already this colour.
            return
        self.painted[key.light_index] = colour
        self.stale.discard(key.light_index)
        self._remember(colour)
        self._refresh()

    def _erase(self, key: kb_layout.Key) -> None:
        if self.painted.pop(key.light_index, None) is None:
            return
        self.stale.add(key.light_index)
        self._refresh()

    def _filler(self, name: str):
        def fill() -> None:
            match = self.GROUPS[name]
            colour = QColor(self._colour())
            for key in kb_layout.KEYS:
                if match(key):
                    self.painted[key.light_index] = colour
                    self.stale.discard(key.light_index)
            self._remember(colour)
            self._refresh()
        return fill

    def _clear(self) -> None:
        # Every key that was painted still has its LED lit, so each one
        # is owed a blanking on the next send.
        self.stale |= set(self.painted)
        self.painted.clear()
        self._refresh()

    def showEvent(self, event) -> None:
        """Re-read the layout whenever the screen comes up.

        The profiles screen can switch scheme while this one is out of
        sight, and the painted layout belongs to the scheme in use.
        Anything rubbed out but not yet sent is dropped with it, which
        is right: it was never part of the scheme being switched to.
        """
        super().showEvent(event)
        self.painted = {
            index: _colour_of(rgb)
            for index, rgb in self.store.current.lighting.items()
        }
        self.stale.clear()
        self._refresh()

    def _refresh(self) -> None:
        # The view only reads this dict, so it is handed ours rather
        # than a copy -- a stroke edits it dozens of times a second.
        self.board.colours = self.painted
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

    # -- the file -------------------------------------------------------
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
        # the timer to outlive the window it is parented to.
        if self._save.isActive():
            self._save_now()
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
        """Write the layout keyed by ident.

        Not by LED index: a file somebody shares should name the keys
        the way the keycaps do, and the idents would survive a layout
        revision that renumbered the LEDs.
        """
        by_index = {k.light_index: k for k in kb_layout.KEYS}
        keys = {
            by_index[index].ident: colour.name()
            for index, colour in self.painted.items()
            if index in by_index
        }
        try:
            path.write_text(json.dumps(
                {"version": self.FILE_VERSION, "keys": keys},
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
            keys = json.loads(path.read_text())["keys"]
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
        self.recent = _in_use_order(found)[:self.RECENT_COLOURS]
        self._show_recent()
        self._refresh()
        skipped = len(keys) - len(found)
        self.status.emit(
            f"imported {len(found)} keys from {path.name}"
            + (f", {skipped} skipped" if skipped else ""))

    # -- the keyboard ---------------------------------------------------
    def _send(self) -> None:
        if not self.painted and not self.stale:
            self.status.emit("nothing painted yet")
            return
        for light_index, colour in self.painted.items():
            self.apply_key.emit(light_index, colour)
        dark = QColor(0, 0, 0)
        for light_index in sorted(self.stale):
            self.apply_key.emit(light_index, dark)
        said = f"sending {len(self.painted)} keys…"
        if self.stale:
            said = (f"sending {len(self.painted)} keys, "
                    f"blanking {len(self.stale)}…")
        self.stale.clear()
        self.status.emit(said)


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
