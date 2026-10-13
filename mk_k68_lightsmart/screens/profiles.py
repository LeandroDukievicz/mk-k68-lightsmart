"""The configuration-scheme screen, which bundles host-side state."""

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
