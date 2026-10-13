"""The remap screen: point at a key, say what it does, write the map."""

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
