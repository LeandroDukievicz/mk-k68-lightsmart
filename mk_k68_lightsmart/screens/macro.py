"""The macro screen: record and edit; the wire encoding is still open."""

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

from .. import layout as kb_layout
from .. import protocol
from ..gui import ACCENT, MUTED, Card, KeyboardView, caption, ghost_button, primary_button
from .common import _card, _combo, _heading, _list, _note


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
