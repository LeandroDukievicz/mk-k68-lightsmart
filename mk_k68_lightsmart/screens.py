"""The five screens the rail still opened onto a placeholder.

Each one is modelled on the vendor's own screen -- the controls it
offers and the order it offers them in -- drawn in this app's chrome
rather than its artwork, which does not ship here.

They differ in how much of the keyboard they can actually reach, and
the difference is the protocol's, not the screen's:

* :class:`KeysPage` and :class:`CustomPage` write to the keyboard. The
  remap block and the per-key colour command are both decoded.
* :class:`MacroPage`, :class:`ProfilesPage` and :class:`MusicPage` edit
  and keep state here only. Each says so on its face rather than
  looking live and quietly doing nothing.

The import runs after ``gui`` is loaded -- ``MainWindow`` imports this
module from inside its constructor -- so taking the shared chrome from
``gui`` at module level is not circular.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import layout as kb_layout
from . import protocol
from .gui import (
    ACCENT,
    MUTED,
    Card,
    KeyboardView,
    caption,
    ghost_button,
    primary_button,
)

#: Shown where a screen can edit something the keyboard cannot yet be
#: told about. Better on the screen than in a commit message.
PENDING_STYLE = (
    "color:#ffc46b; font-size:11px; background: rgba(90,60,0,70);"
    " border:1px solid #6b4a10; border-radius:4px; padding:7px 10px;"
)


def _note(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet(PENDING_STYLE)
    return lb


def _heading(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet("color:#cfe3ff; font-size:12px; font-weight:bold;")
    return lb


def _card(*rows) -> Card:
    """A card holding the widgets or layouts given, in order."""
    card = Card()
    inner = QVBoxLayout(card)
    inner.setContentsMargins(16, 14, 16, 14)
    inner.setSpacing(9)
    for row in rows:
        if isinstance(row, QWidget):
            inner.addWidget(row)
        else:
            inner.addLayout(row)
    return card


def _list() -> QListWidget:
    w = QListWidget()
    w.setStyleSheet(
        "QListWidget { background:#061634; color:#cfe3ff; font-size:11px;"
        " border:1px solid #0d3a6b; border-radius:4px; }"
        "QListWidget::item { padding:4px 6px; }"
        "QListWidget::item:selected { background:#0d3a6b; }"
    )
    return w


def _combo(items: list[str] | None = None) -> QComboBox:
    c = QComboBox()
    c.setStyleSheet(
        "QComboBox { background:#0a2444; color:#cfe3ff; border:1px solid #0d3a6b;"
        " border-radius:3px; padding:4px 8px; font-size:11px; }"
    )
    for text in items or []:
        c.addItem(text)
    return c


# ----------------------------------------------------------------------
# 1. Keys -- the remap screen
# ----------------------------------------------------------------------
class KeysPage(QWidget):
    """Point at a key, say what it should do, then write the map.

    The vendor does this with a right-click menu over the board --
    default function, disable, custom function -- and a dialog behind
    the third. The same three choices are a combo box here, because
    there is one of them and it is always visible.

    Writing persists across a power cycle, which is why the write is
    two steps and why the way back has a button of its own.
    """

    #: What a key can be told to do. The first two come out of the
    #: vendor's own menu; "another key" is its custom-function dialog.
    ACTIONS = ("Leave it as it is", "Another key", "Disable it")

    write_requested = Signal(object, bool)   # block, fn_layer
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        #: slot -> (usage, label); what has been staged but not written.
        self.staged: dict[int, tuple[int, str]] = {}
        self._selected: kb_layout.Key | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("Key remapping"))
        # Full width rather than inside the side card: the card is 330px
        # and this much text wraps to a dozen lines in it, which the
        # board's height then clips.
        outer.addWidget(_note(
            "A keymap outlives a power cycle. Restoring the default map "
            "is the way back, and it is the same thing the vendor's own "
            "reset does — there is no reset command behind it. 'Disable "
            "it' is the one record here that was not read out of the "
            "driver: it is a remap to usage 0."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)

        self.board = KeyboardView()
        self.board.key_clicked.connect(self._pick)
        body.addWidget(_card(self.board), 1)

        self.chosen = QLabel("Pick a key on the board")
        self.chosen.setStyleSheet("color:#cfe3ff; font-size:12px;")
        self.action = _combo(list(self.ACTIONS))
        self.action.currentIndexChanged.connect(self._action_changed)

        self.target = _combo(
            [f"{k.ident}  (0x{k.code:02x})" for k in kb_layout.KEYS])
        self.target.setEnabled(False)

        self.staged_list = _list()
        self.staged_list.setFixedHeight(150)

        stage_row = QHBoxLayout()
        stage_row.addWidget(primary_button("Stage this key", self._stage))
        stage_row.addWidget(ghost_button("Forget it", self._unstage))
        stage_row.addStretch()

        write_row = QHBoxLayout()
        self.write_button = primary_button("Write the map", self._write)
        write_row.addWidget(self.write_button)
        write_row.addWidget(
            ghost_button("Restore the default map", self._restore))
        write_row.addStretch()

        side = _card(
            caption("Selected"), self.chosen,
            caption("Should do"), self.action,
            caption("Which key"), self.target,
            stage_row,
            caption("Staged, not written"), self.staged_list,
            write_row,
        )
        side.setFixedWidth(330)
        body.addWidget(side)
        outer.addLayout(body, 1)
        outer.addStretch()

    # -- editing --------------------------------------------------------
    def _pick(self, key: kb_layout.Key) -> None:
        self._selected = key
        self.chosen.setText(f"{key.ident}   usage 0x{key.code:02x}")

    def _action_changed(self, index: int) -> None:
        self.target.setEnabled(index == 1)

    def _slot(self, key: kb_layout.Key) -> int:
        return kb_layout.remap_slot(key)

    def _stage(self) -> None:
        if self._selected is None:
            self.status.emit("pick a key on the board first")
            return
        slot = self._slot(self._selected)
        index = self.action.currentIndex()
        if index == 0:
            self.staged.pop(slot, None)
        elif index == 1:
            target = kb_layout.KEYS[self.target.currentIndex()]
            self.staged[slot] = (target.code, f"→ {target.ident}")
        else:
            self.staged[slot] = (0x00, "→ nothing")
        self._redraw_staged()

    def _unstage(self) -> None:
        if self._selected is not None:
            self.staged.pop(self._slot(self._selected), None)
            self._redraw_staged()

    def _redraw_staged(self) -> None:
        self.staged_list.clear()
        by_slot = {self._slot(k): k for k in kb_layout.KEYS}
        for slot, (_, label) in sorted(self.staged.items()):
            key = by_slot.get(slot)
            self.staged_list.addItem(
                QListWidgetItem(f"{key.ident if key else slot:<12} {label}"))
        # The board shows what is staged, so the map is readable without
        # cross-checking the list against the keycaps.
        self.board.colours = {
            by_slot[slot].light_index: QColor("#10a9fd")
            for slot in self.staged if slot in by_slot
        }
        self.board.update()
        self.write_button.setEnabled(bool(self.staged))

    # -- writing --------------------------------------------------------
    def block(self) -> bytes:
        return protocol.remap_block(
            {slot: (usage, 0) for slot, (usage, _) in self.staged.items()})

    def _write(self) -> None:
        if not self.staged:
            self.status.emit("nothing staged")
            return
        if QMessageBox.question(
                self, "Write the keymap",
                f"{len(self.staged)} key(s) will be changed on the "
                "keyboard itself, and will stay changed after a power "
                "cycle.\n\nRestore the default map to undo it.",
        ) != QMessageBox.Yes:
            return
        self.write_requested.emit(self.block(), False)

    def _restore(self) -> None:
        if QMessageBox.question(
                self, "Restore the default map",
                "Every key goes back to what it does out of the box.",
        ) != QMessageBox.Yes:
            return
        self.staged.clear()
        self._redraw_staged()
        self.write_requested.emit(protocol.remap_defaults(), False)


# ----------------------------------------------------------------------
# 2. Macro
# ----------------------------------------------------------------------
class MacroPage(QWidget):
    """Write a macro down; the keyboard cannot be told it yet.

    The vendor records three kinds of event -- a key, a mouse action and
    a delay -- and offers to keep the real delays, drop them, or replace
    them all with one number. That shape is here. What is missing is the
    encoding of an event on the wire, so nothing is sent.
    """

    status = Signal(str)

    EVENTS = ("Key down", "Key up", "Delay (ms)", "Mouse button")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        #: name -> list of (kind, value)
        self.macros: dict[str, list[tuple[str, str]]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("Macros"))
        outer.addWidget(_note(
            "The command and its framing are known — 0x15, announced "
            "with a block count, sent in 64-byte blocks. The event "
            "encoding is not: a key emits a 0x50 marker and two bytes "
            "whose meaning is still open. So this edits and keeps "
            "macros here, and sends nothing."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)

        self.names = _list()
        self.names.currentTextChanged.connect(self._show)
        name_row = QHBoxLayout()
        name_row.addWidget(ghost_button("New", self._new))
        name_row.addWidget(ghost_button("Delete", self._delete))
        name_row.addStretch()
        left = _card(caption("Macros"), self.names, name_row)
        left.setFixedWidth(240)
        body.addWidget(left)

        self.events = _list()
        self.kind = _combo(list(self.EVENTS))
        self.value = _combo([k.ident for k in kb_layout.KEYS])
        self.value.setEditable(True)

        add_row = QHBoxLayout()
        add_row.addWidget(primary_button("Add event", self._add))
        add_row.addWidget(ghost_button("Remove", self._remove))
        add_row.addWidget(ghost_button("Up", lambda: self._move(-1)))
        add_row.addWidget(ghost_button("Down", lambda: self._move(1)))
        add_row.addStretch()

        self.delay_mode = _combo(
            ("Keep the real delays", "No delay", "One delay for all"))
        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(1, 60000)
        self.delay_ms.setValue(10)
        self.delay_ms.setStyleSheet(
            "QSpinBox { background:#0a2444; color:#cfe3ff;"
            " border:1px solid #0d3a6b; border-radius:3px; padding:3px; }")
        delay_row = QHBoxLayout()
        delay_row.addWidget(self.delay_mode, 1)
        delay_row.addWidget(self.delay_ms)

        body.addWidget(_card(
            caption("Events"), self.events,
            caption("Add"), self.kind, self.value, add_row,
            caption("When recording"), delay_row,
        ), 1)
        outer.addLayout(body, 1)

    def _current(self) -> str | None:
        item = self.names.currentItem()
        return item.text() if item else None

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New macro", "Name")
        if ok and name and name not in self.macros:
            self.macros[name] = []
            self.names.addItem(name)
            self.names.setCurrentRow(self.names.count() - 1)

    def _delete(self) -> None:
        name = self._current()
        if name:
            self.macros.pop(name, None)
            self.names.takeItem(self.names.currentRow())
            self._show(self._current() or "")

    def _show(self, name: str) -> None:
        self.events.clear()
        for kind, value in self.macros.get(name, []):
            self.events.addItem(f"{kind:<14} {value}")

    def _add(self) -> None:
        name = self._current()
        if not name:
            self.status.emit("make a macro first")
            return
        self.macros[name].append(
            (self.kind.currentText(), self.value.currentText()))
        self._show(name)

    def _remove(self) -> None:
        name, row = self._current(), self.events.currentRow()
        if name and row >= 0:
            self.macros[name].pop(row)
            self._show(name)

    def _move(self, by: int) -> None:
        name, row = self._current(), self.events.currentRow()
        if not name or row < 0:
            return
        events = self.macros[name]
        new = row + by
        if 0 <= new < len(events):
            events[row], events[new] = events[new], events[row]
            self._show(name)
            self.events.setCurrentRow(new)


# ----------------------------------------------------------------------
# 3. User -- configuration schemes
# ----------------------------------------------------------------------
class ProfilesPage(QWidget):
    """The vendor calls these configuration schemes.

    Its own list sits beside add, delete, rename, import and export,
    and that is what is here. What is *not* here is a switch command:
    the driver has none. Reading its idle-tick dispatcher, the cases
    that change scheme send nothing to the keyboard at all -- the app
    re-sends the keymap, the lighting and the rest instead. So a scheme
    is a host-side bundle, and this screen will become real when there
    is something to bundle.
    """

    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.schemes: list[str] = ["Default"]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("User profiles"))
        outer.addWidget(_note(
            "No profile command exists. The vendor's own dispatcher "
            "sends nothing to the keyboard when a scheme changes — it "
            "re-sends everything instead. So switching here will mean "
            "replaying the keymap and the lighting, once there is a "
            "screen for each to replay."
        ))

        self.list = _list()
        for name in self.schemes:
            self.list.addItem(name)
        self.list.setCurrentRow(0)

        row = QHBoxLayout()
        row.addWidget(primary_button("New", self._new))
        row.addWidget(ghost_button("Rename", self._rename))
        row.addWidget(ghost_button("Delete", self._delete))
        row.addWidget(ghost_button("Duplicate", self._duplicate))
        row.addStretch()

        card = _card(caption("Configuration schemes"), self.list, row)
        card.setFixedWidth(420)
        outer.addWidget(card)
        outer.addStretch()

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New scheme", "Name")
        if ok and name:
            self.schemes.append(name)
            self.list.addItem(name)

    def _rename(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        name, ok = QInputDialog.getText(
            self, "Rename", "Name", text=self.schemes[row])
        if ok and name:
            self.schemes[row] = name
            self.list.item(row).setText(name)

    def _delete(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        if len(self.schemes) == 1:
            self.status.emit("there has to be one scheme")
            return
        self.schemes.pop(row)
        self.list.takeItem(row)

    def _duplicate(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            name = f"{self.schemes[row]} copy"
            self.schemes.append(name)
            self.list.addItem(name)


# ----------------------------------------------------------------------
# 4. Custom -- a whole layout, painted
# ----------------------------------------------------------------------
class CustomPage(QWidget):
    """Paint the whole board, then send it key by key.

    This one reaches the keyboard: the per-key colour command is the
    same one the Home screen uses. What it cannot do is *store* the
    result, because that needs the profile commands — so a layout
    painted here lasts until something else lights the keys.

    The group buttons are the vendor's: select all, the number row,
    WASD.
    """

    #: The groups the vendor's own toolbar offers.
    GROUPS = {
        "All": lambda k: True,
        "Numbers": lambda k: k.ident.isdigit(),
        "WASD": lambda k: k.ident in ("W", "A", "S", "D"),
    }

    apply_key = Signal(int, object)          # light_index, QColor
    status = Signal(str)

    def __init__(self, current_colour, parent: QWidget | None = None):
        super().__init__(parent)
        self._colour = current_colour
        self.painted: dict[int, QColor] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("Custom lighting"))
        outer.addWidget(_note(
            "The colours go out one key at a time, which works and is "
            "what the Home screen already does. Keeping a layout on the "
            "keyboard is what needs the profile commands, so what is "
            "painted here lasts until something else lights the keys."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)
        self.board = KeyboardView()
        self.board.key_clicked.connect(self._paint)
        body.addWidget(_card(self.board), 1)

        groups = QHBoxLayout()
        for name in self.GROUPS:
            groups.addWidget(ghost_button(name, self._filler(name)))
        groups.addStretch()

        actions = QHBoxLayout()
        actions.addWidget(primary_button("Send to the keyboard", self._send))
        actions.addWidget(ghost_button("Clear", self._clear))
        actions.addStretch()

        self.swatch = QLabel()
        self.swatch.setFixedHeight(22)
        self._show_swatch()

        side = _card(
            caption("Colour"), self.swatch,
            ghost_button("Pick a colour…", self._pick_colour),
            caption("Paint a group"), groups,
            actions,
        )
        side.setFixedWidth(330)
        body.addWidget(side)
        outer.addLayout(body, 1)
        outer.addStretch()

    def _show_swatch(self) -> None:
        self.swatch.setStyleSheet(
            f"background:{self._colour().name()}; border:1px solid #0d3a6b;"
            " border-radius:3px;")

    def _pick_colour(self) -> None:
        chosen = QColorDialog.getColor(self._colour(), self, "Colour")
        if chosen.isValid():
            # The page keeps its own colour once one is picked here, so
            # painting does not change what the rest of the app is using.
            self._colour = lambda c=chosen: c
            self._show_swatch()

    def _paint(self, key: kb_layout.Key) -> None:
        self.painted[key.light_index] = QColor(self._colour())
        self.board.colours = dict(self.painted)
        self.board.update()

    def _filler(self, name: str):
        def fill() -> None:
            match = self.GROUPS[name]
            for key in kb_layout.KEYS:
                if match(key):
                    self.painted[key.light_index] = QColor(self._colour())
            self.board.colours = dict(self.painted)
            self.board.update()
        return fill

    def _clear(self) -> None:
        self.painted.clear()
        self.board.colours = {}
        self.board.update()

    def _send(self) -> None:
        if not self.painted:
            self.status.emit("nothing painted yet")
            return
        for light_index, colour in self.painted.items():
            self.apply_key.emit(light_index, colour)
        self.status.emit(f"sending {len(self.painted)} keys…")


# ----------------------------------------------------------------------
# 5. Music
# ----------------------------------------------------------------------
class LevelBar(QWidget):
    """A meter, so the screen shows what it would be driving."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self._phase = 0.0
        # A frozen frame rather than a flat line: at rest the screen
        # should still show the shape of the thing it would drive.
        self.levels = [0.0] * 24
        self._tick()
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def run(self, on: bool) -> None:
        self._timer.start(50) if on else self._timer.stop()
        if not on:
            self.levels = [0.0] * len(self.levels)
            self.update()

    def _tick(self) -> None:
        import math
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
            colour = QColor("#3ddc84") if level < 0.6 else (
                QColor("#ffc46b") if level < 0.85 else QColor("#ff6b6b"))
            p.fillRect(int(i * w + 2), int(self.height() - h - 3),
                       int(w - 3), int(h), colour)


class MusicPage(QWidget):
    """What the keyboard would do with sound, and who would do it.

    The listening is the host's: the keyboard is told colours, the same
    way every other lighting path tells it colours. So this needs an
    audio capture, not a new command -- which is why the controls are
    real and the meter is a stand-in.

    The controls are the vendor's: a colour scheme for the bars, how
    much of the range to use, and what the unlit keys do behind them.
    """

    status = Signal(str)

    SCHEMES = ("Green / yellow / red", "Single colour", "Spectrum")
    BACKGROUNDS = ("Nothing", "Dim static", "The current effect")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("Music mode"))
        outer.addWidget(_note(
            "No new command is needed for this — the host listens and "
            "sends colours, through the same per-key path the Home "
            "screen uses. What is missing is the audio capture, so the "
            "meter below is a stand-in and nothing reaches the keyboard."
        ))

        self.meter = LevelBar()

        self.scheme = _combo(list(self.SCHEMES))
        self.background = _combo(list(self.BACKGROUNDS))

        self.amplitude = QSlider(Qt.Horizontal)
        self.amplitude.setRange(1, 100)
        self.amplitude.setValue(60)
        self.back_bright = QSlider(Qt.Horizontal)
        self.back_bright.setRange(0, 100)
        self.back_bright.setValue(20)

        self.preview = QRadioButton("Animate the meter")
        self.preview.setStyleSheet("color:#9fb3d0; font-size:11px;")
        self.preview.toggled.connect(self.meter.run)
        group = QButtonGroup(self)
        group.setExclusive(False)
        group.addButton(self.preview)

        row = QHBoxLayout()
        row.addWidget(_card(
            caption("Visualisation"), self.scheme,
            caption("Amplitude"), self.amplitude,
            caption("Behind it"), self.background,
            caption("Background brightness"), self.back_bright,
            self.preview,
        ), 1)
        outer.addWidget(_card(caption("Levels"), self.meter))
        outer.addLayout(row)
        outer.addStretch()
