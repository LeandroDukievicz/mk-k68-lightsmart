"""The remap screen: point at a key, say what it does, write the map."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .. import layout as kb_layout
from .. import protocol
from .. import store
from ..gui import ACCENT, KeyboardView, caption, ghost_button, primary_button
from .common import _card, _combo, _heading, _list, _note

#: A key by the usage it sends, which is how a staged record names what
#: a key became -- the layout indexes by ident and by LED, not by usage.
BY_CODE: dict[int, kb_layout.Key] = {k.code: k for k in kb_layout.KEYS}

#: The modifiers the vendor's custom-function dialog offers, each as the
#: usage it is rather than as the bit it sets: the bit is
#: :data:`protocol.MODIFIER_BITS`' business, and naming the usage keeps
#: the two from drifting apart.
MODIFIERS = (("Ctrl", 0xE0), ("Shift", 0xE1), ("Alt", 0xE2), ("Win", 0xE3))

#: Where an Fn-layer slot goes in ``Scheme.keymap``, which has to hold
#: both layers in the one dict.
#:
#: The store reads its keys back through ``int()``, so a string
#: namespace would not survive the file; an offset will. 1000 is past
#: every slot on this board (they stop at 124) and past what
#: :func:`protocol.remap_block` will encode, so a scheme handed straight
#: to the encoder raises rather than quietly writing a wrong record.
FN_SLOT_BASE = 1000

#: A key staged as doing nothing. Marked apart from a remap because the
#: two are different kinds of change and one of them is a loss.
DISABLED = QColor("#a33b4c")


def _toggle(text: str) -> QCheckBox:
    """A modifier switch in the side card's chrome.

    The shared chrome has no checkbox because this is the only screen
    that wants one, so the style lives here next to its only use.
    """
    box = QCheckBox(text)
    box.setStyleSheet(
        "QCheckBox { color:#cfe3ff; font-size:11px; spacing:4px; }"
        "QCheckBox::indicator { width:11px; height:11px;"
        " border:1px solid #0d3a6b; border-radius:2px; background:#0a2444; }"
        "QCheckBox::indicator:checked { background:#10a9fd;"
        " border-color:#10a9fd; }"
        "QCheckBox:disabled { color:#44607f; }"
    )
    return box


# ----------------------------------------------------------------------
# 1. Keys -- the remap screen
# ----------------------------------------------------------------------
class KeysPage(QWidget):
    """Point at a key, say what it should do, then write the map.

    The vendor does this with a right-click menu over the board --
    default function, disable, custom function -- and a dialog behind
    the third. The same three choices are a combo box here, because
    there is one of them and it is always visible.

    Two maps rather than one: the base keymap and the Fn layer are
    separate blocks behind separate commands, and writing either leaves
    the other as it was. So the screen keeps one staged map per layer,
    and the layer combo says which of them every control below it
    means. Nothing is shared between them.

    Writing persists across a power cycle, which is why the write is
    two steps and why the way back has a button of its own.
    """

    #: What a key can be told to do. The first two come out of the
    #: vendor's own menu; "another key" is its custom-function dialog.
    ACTIONS = ("Leave it as it is", "Another key", "Disable it")

    #: The two blocks, in the order the layer combo offers them, worded
    #: for the dialogs rather than as a flag.
    LAYERS = ("base keymap", "Fn layer")

    write_requested = Signal(object, bool)   # block, fn_layer
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store.shared()
        #: Each layer's staged map, slot -> (usage, modifiers). Keyed by
        #: the same bool ``write_requested`` carries, so "which layer"
        #: has one answer on this screen and not two.
        self.layers: dict[bool, dict[int, tuple[int, int]]] = {
            False: {}, True: {}}
        self._selected: kb_layout.Key | None = None

        # A save rewrites the whole scheme file, and staging is one
        # click in a run of them -- remapping a row of keys is twenty.
        # So an edit asks for a save on the next trip through the event
        # loop, and twenty edits in one gesture cost one write.
        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.timeout.connect(self._save_now)

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
            "reset does — that reset is this block written to both "
            "layers, and there is no reset command behind it. 'Disable "
            "it' is the one record here that was not read out of the "
            "driver: it is a remap to usage 0."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)

        self.board = KeyboardView()
        self.board.key_clicked.connect(self._pick)
        body.addWidget(_card(self.board), 1)

        self.layer = _combo(["Base keymap", "Fn layer"])
        self.layer.currentIndexChanged.connect(self._layer_changed)

        self.chosen = QLabel("Pick a key on the board")
        self.chosen.setStyleSheet("color:#cfe3ff; font-size:12px;")
        self.action = _combo(list(self.ACTIONS))
        self.action.currentIndexChanged.connect(self._action_changed)

        self.target = _combo(
            [f"{k.ident}  (0x{k.code:02x})" for k in kb_layout.KEYS])
        self.target.setEnabled(False)

        #: The modifier boxes, each beside the usage it stands for.
        self.mods: list[tuple[QCheckBox, int]] = []
        mod_row = QHBoxLayout()
        mod_row.setSpacing(10)
        for name, usage in MODIFIERS:
            box = _toggle(name)
            box.setEnabled(False)
            self.mods.append((box, usage))
            mod_row.addWidget(box)
        mod_row.addStretch()

        self.staged_caption = caption("Staged, not written")
        self.staged_list = _list()
        self.staged_list.setFixedHeight(120)

        stage_row = QHBoxLayout()
        stage_row.addWidget(primary_button("Stage this key", self._stage))
        stage_row.addWidget(ghost_button("Forget it", self._unstage))
        stage_row.addStretch()

        clear_row = QHBoxLayout()
        self.clear_button = ghost_button("Forget them all", self._clear)
        clear_row.addWidget(self.clear_button)
        clear_row.addStretch()

        write_row = QHBoxLayout()
        self.write_button = primary_button("Write the map", self._write)
        write_row.addWidget(self.write_button)
        write_row.addWidget(
            ghost_button("Restore the default map", self._restore))
        write_row.addStretch()

        side = _card(
            caption("Layer"), self.layer,
            caption("Selected"), self.chosen,
            caption("Should do"), self.action,
            caption("Which key"), self.target,
            caption("Held with"), mod_row,
            stage_row,
            self.staged_caption, self.staged_list, clear_row,
            write_row,
        )
        side.setFixedWidth(330)
        body.addWidget(side)
        outer.addLayout(body, 1)
        outer.addStretch()

        self._load()
        self._redraw_staged()

    # -- the layer in front of you --------------------------------------
    @property
    def fn_layer(self) -> bool:
        """Whether the Fn layer is the one being edited."""
        return self.layer.currentIndex() == 1

    @property
    def staged(self) -> dict[int, tuple[int, int]]:
        """The staged map of the layer being edited.

        The screen holds both, but every control on it means the one
        the combo is showing, so the short name points there.
        """
        return self.layers[self.fn_layer]

    # -- the scheme file ------------------------------------------------
    def showEvent(self, event) -> None:
        """Re-read the scheme whenever the screen comes up.

        The profiles screen can switch scheme while this one is out of
        sight, and what is staged belongs to the scheme in use. Nothing
        is lost by re-reading: every edit here is written as it is made.
        """
        super().showEvent(event)
        self.layers[False].clear()
        self.layers[True].clear()
        self._load()
        self._redraw_staged()

    def _load(self) -> None:
        """Take both staged maps out of the scheme last saved.

        A staged map is work done by hand and the keyboard cannot be
        read back, so losing it to closing the window would mean doing
        it again off the keycaps.
        """
        for slot, pair in self.store.current.remap_changes().items():
            if slot >= FN_SLOT_BASE:
                self.layers[True][slot - FN_SLOT_BASE] = pair
            else:
                self.layers[False][slot] = pair

    def _remember(self) -> None:
        """Put both staged maps back in the scheme, and ask for a save.

        Written whole rather than patched: the scheme's keymap is this
        screen's state and nobody else's, so there is no half of it to
        preserve.
        """
        self.store.current.keymap = {
            slot + (FN_SLOT_BASE if fn else 0): [usage, modifiers]
            for fn, staged in self.layers.items()
            for slot, (usage, modifiers) in staged.items()
        }
        self._save.start()

    def _save_now(self) -> None:
        """Write the file, and survive not being able to.

        A read-only or unreachable config directory is worth saying out
        loud, but it is not worth losing the click that caused it.
        """
        try:
            self.store.save()
        except OSError as exc:
            self.status.emit(f"the scheme could not be saved: {exc}")

    # -- editing --------------------------------------------------------
    def _pick(self, key: kb_layout.Key) -> None:
        self._selected = key
        self.chosen.setText(f"{key.ident}   usage 0x{key.code:02x}")
        # The controls show this key's own staging, so that a second
        # look at a key says something true about it rather than
        # leaving the previous key's answer standing.
        staged = self.staged.get(self._slot(key))
        if staged is None:
            self.action.setCurrentIndex(0)
            self._show_mask(0)
            return
        usage, modifiers = staged
        if not usage and not modifiers:
            self.action.setCurrentIndex(2)
            self._show_mask(0)
            return
        self.action.setCurrentIndex(1)
        self._show_mask(modifiers)
        target = BY_CODE.get(usage)
        if target is not None:
            self.target.setCurrentIndex(kb_layout.KEYS.index(target))

    def _layer_changed(self, index: int) -> None:
        """Change the view, not the maps: both stay staged."""
        if self._selected is not None:
            self._pick(self._selected)
        self._redraw_staged()

    def _action_changed(self, index: int) -> None:
        enabled = index == 1
        self.target.setEnabled(enabled)
        for box, _ in self.mods:
            box.setEnabled(enabled)

    def _slot(self, key: kb_layout.Key) -> int:
        return kb_layout.remap_slot(key)

    def _mask(self) -> int:
        """The record's modifier byte, as the ticked boxes come to."""
        return sum(protocol.MODIFIER_BITS[usage]
                   for box, usage in self.mods if box.isChecked())

    def _show_mask(self, mask: int) -> None:
        for box, usage in self.mods:
            box.setChecked(bool(mask & protocol.MODIFIER_BITS[usage]))

    def _label(self, usage: int, modifiers: int) -> str:
        """What a staged record reads as: "→ Ctrl+Shift+C"."""
        if not usage and not modifiers:
            return "→ nothing"
        parts = [name for name, held in MODIFIERS
                 if modifiers & protocol.MODIFIER_BITS[held]]
        if usage:
            key = BY_CODE.get(usage)
            parts.append(key.ident if key else f"0x{usage:02x}")
        return "→ " + "+".join(parts)

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
            self.staged[slot] = (target.code, self._mask())
        else:
            self.staged[slot] = (0x00, 0)
        self._remember()
        self._redraw_staged()

    def _unstage(self) -> None:
        if self._selected is not None:
            self.staged.pop(self._slot(self._selected), None)
            self._remember()
            self._redraw_staged()

    def _clear(self) -> None:
        """Forget every staged key on both layers, sending nothing.

        No confirmation, unlike the two writes: this undoes staging
        only, and the keyboard never hears about it.
        """
        if not any(self.layers.values()):
            self.status.emit("nothing staged")
            return
        for staged in self.layers.values():
            staged.clear()
        self._remember()
        self._redraw_staged()
        self.status.emit("staged keys forgotten; the keyboard is untouched")

    def _redraw_staged(self) -> None:
        staged = self.staged
        by_slot = {self._slot(k): k for k in kb_layout.KEYS}

        self.staged_list.clear()
        lines = []
        for slot, (usage, modifiers) in sorted(staged.items()):
            key = by_slot.get(slot)
            name = key.ident if key else f"slot {slot}"
            lines.append(f"{name:<12} {self._label(usage, modifiers)}")
            self.staged_list.addItem(QListWidgetItem(lines[-1]))

        # The board shows what is staged, so the map is readable without
        # cross-checking the list against the keycaps. What it cannot
        # show is what each key became: KeyboardView draws key.name and
        # owns its painting, so the targets go in its tooltip instead.
        self.board.colours = {
            by_slot[slot].light_index:
                ACCENT if usage or modifiers else DISABLED
            for slot, (usage, modifiers) in staged.items()
            if slot in by_slot
        }
        self.board.update()
        where = self.LAYERS[self.fn_layer]
        if lines:
            self.board.setToolTip(
                f"Staged on the {where}:\n" + "\n".join(lines))
        else:
            self.board.setToolTip(f"Nothing staged on the {where}")

        # The other layer is staged and out of sight, which is exactly
        # how a write to this one gets mistaken for having done both.
        other = len(self.layers[not self.fn_layer])
        self.staged_caption.setText(
            f"Staged on the {where}, not written"
            + (f" (and {other} on the {self.LAYERS[not self.fn_layer]})"
               if other else ""))
        self.write_button.setEnabled(bool(staged))
        self.clear_button.setEnabled(any(self.layers.values()))

    # -- writing --------------------------------------------------------
    def block(self, fn_layer: bool | None = None) -> bytes:
        """The 694 bytes of one layer, by default the one on screen."""
        if fn_layer is None:
            fn_layer = self.fn_layer
        return protocol.remap_block(dict(self.layers[fn_layer]))

    def _write(self) -> None:
        staged = self.staged
        if not staged:
            self.status.emit("nothing staged")
            return
        where = self.LAYERS[self.fn_layer]
        if QMessageBox.question(
                self, f"Write the {where}",
                f"{len(staged)} key(s) on the {where} will be changed "
                "on the keyboard itself, and will stay changed after a "
                "power cycle. The other layer is left as it is.\n\n"
                "Restore the default map to undo it.",
        ) != QMessageBox.Yes:
            return
        # This map is about to become the keyboard's, so the copy of it
        # goes to disk now rather than on the next trip through the
        # event loop.
        self._save_now()
        self.write_requested.emit(self.block(), self.fn_layer)

    def _restore(self) -> None:
        if QMessageBox.question(
                self, "Restore the default map",
                "Every key goes back to what it does out of the box, on "
                "the base keymap and on the Fn layer.\n\nThis is the "
                "vendor's own reset: it writes this same block to both "
                "layers, and there is no reset command behind it.",
        ) != QMessageBox.Yes:
            return
        for staged in self.layers.values():
            staged.clear()
        self._remember()
        self._save_now()
        self._redraw_staged()
        # Both blocks, in the order the driver's own reset sends them
        # (docs/PROTOCOL.md §10, operation 4). One alone would leave
        # half a map behind, which is the state hardest to recognise
        # from the keycaps.
        defaults = protocol.remap_defaults()
        self.write_requested.emit(defaults, False)
        self.write_requested.emit(defaults, True)
