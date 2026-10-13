"""The custom-lighting screen: paint a whole board, send it key by key."""

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
