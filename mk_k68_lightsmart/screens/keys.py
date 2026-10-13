"""The remap screen: point at a key, say what it does, write the map."""

from __future__ import annotations

from typing import NamedTuple

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
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

# -- the five key types of the custom-function dialog -------------------
#
# The vendor's key type drop-down, in its own order (manual image 11).
# These strings go in the scheme file as well as on the screen, so
# renaming one orphans every binding already saved.
KEY_FUNCTION = "Key function"
MULTI_KEY = "Multi-key combo"
MACRO = "Macro"
MOUSE = "Mouse function"
MEDIA = "Multimedia"
KINDS = (KEY_FUNCTION, MULTI_KEY, MACRO, MOUSE, MEDIA)

#: What the right-click panel's "disable key" stages. Not one of the
#: dialog's types -- the vendor keeps it as its own menu entry -- but it
#: is a kind of binding, so it is named with them.
DISABLE = "Disabled"

#: The kinds no record can carry, which is a fact about the protocol
#: and not about this screen. A macro is record type 3, an id into a
#: macro list the host cannot upload (docs/PROTOCOL.md §8); mouse and
#: multimedia are type 0xD, ``07 <a> <b> <c>``, whose three parameters
#: are not decoded (§7). A key bound to one of these is kept in the
#: scheme and written to the keyboard as itself.
HOST_ONLY = frozenset((MACRO, MOUSE, MEDIA))

#: The mouse functions the vendor's dialog offers under Mouse function.
MOUSE_FUNCTIONS = (
    "Left click", "Right click", "Middle click", "Wheel up", "Wheel down",
    "Mouse back", "Mouse forward",
)

#: The icon row the quick-select region opens with (manual image 13):
#: media transport and the system shortcuts, each as a drawn glyph --
#: the vendor's artwork does not ship here -- with its name in the
#: tooltip. The last two are the specials the manual calls out: Show Desktop
#: (image 15) and the dot-matrix effect switch (image 16), which the
#: vendor marks wired-mode only.
QUICK_ICONS = (
    ("▣", "My computer"),
    ("▤", "Calculator"),
    ("✉", "Mail"),
    ("♫", "Media player"),
    ("▶", "Play / pause"),
    ("■", "Stop"),
    ("|◀", "Previous track"),
    ("▶|", "Next track"),
    ("−", "Volume down"),
    ("+", "Volume up"),
    ("⊘", "Mute"),
    ("⌂", "Browser home"),
    ("↻", "Refresh"),
    ("✕", "Stop loading"),
    ("←", "Back"),
    ("→", "Forward"),
    ("★", "Favourites"),
    ("⌕", "Search"),
    ("⌧", "Show desktop"),
    ("✺", "Switch the dot-matrix effect (wired only)"),
)

#: Everything the icon row can bind, which is also the Multimedia list in
#: the dialog. One table, so the two regions cannot drift apart.
MEDIA_FUNCTIONS = tuple(name for _, name in QUICK_ICONS)

#: The quick-select grid (manual images 13 and 14), row by row, as
#: ``(label, usage)``.
#:
#: It is wider than this board on purpose: it is where a key is bound to
#: something the board does not have, which is most of the point of
#: remapping. Keys the board *does* have are labelled with their
#: ``layout`` ident rather than the vendor's wording, so one usage has
#: one name wherever it is shown. The vendor's K14, k42, K45, K56, K150
#: and K151 cells are left out -- they are matrix positions of the
#: bigger boards this grid is shared with, and this one has no usage for
#: them.
QUICK_ROWS: tuple[tuple[tuple[str, int], ...], ...] = (
    (("Esc", 0x29),
     ("F1", 0x3A), ("F2", 0x3B), ("F3", 0x3C), ("F4", 0x3D),
     ("F5", 0x3E), ("F6", 0x3F), ("F7", 0x40), ("F8", 0x41),
     ("F9", 0x42), ("F10", 0x43), ("F11", 0x44), ("F12", 0x45),
     ("Print", 0x46), ("Scroll", 0x47), ("Pause", 0x48)),
    (("~", 0x35),
     ("1", 0x1E), ("2", 0x1F), ("3", 0x20), ("4", 0x21), ("5", 0x22),
     ("6", 0x23), ("7", 0x24), ("8", 0x25), ("9", 0x26), ("0", 0x27),
     ("-", 0x2D), ("=", 0x2E), ("Backspace", 0x2A),
     ("Ins", 0x49), ("Home", 0x4A), ("PgUp", 0x4B),
     ("NumLk", 0x53), ("Num /", 0x54), ("Num *", 0x55), ("Num -", 0x56)),
    (("Tab", 0x2B),
     ("Q", 0x14), ("W", 0x1A), ("E", 0x08), ("R", 0x15), ("T", 0x17),
     ("Y", 0x1C), ("U", 0x18), ("I", 0x0C), ("O", 0x12), ("P", 0x13),
     ("[", 0x2F), ("]", 0x30), ("\\|", 0x31),
     ("Del", 0x4C), ("End", 0x4D), ("PgDn", 0x4E),
     ("Num 7", 0x5F), ("Num 8", 0x60), ("Num 9", 0x61), ("Num +", 0x57)),
    (("Caps", 0x39),
     ("A", 0x04), ("S", 0x16), ("D", 0x07), ("F", 0x09), ("G", 0x0A),
     ("H", 0x0B), ("J", 0x0D), ("K", 0x0E), ("L", 0x0F),
     (";", 0x33), ('"', 0x34), ("Enter", 0x28),
     ("Num 4", 0x5C), ("Num 5", 0x5D), ("Num 6", 0x5E)),
    (("LeftShift", 0xE1),
     ("Z", 0x1D), ("X", 0x1B), ("C", 0x06), ("V", 0x19), ("B", 0x05),
     ("N", 0x11), ("M", 0x10), ("<", 0x36), (">", 0x37), ("?", 0x38),
     ("RightShift", 0xE5), ("↑", 0x52),
     ("Num 1", 0x59), ("Num 2", 0x5A), ("Num 3", 0x5B), ("Num Enter", 0x58)),
    (("Control", 0xE0), ("Win", 0xE3), ("LeftAlt", 0xE2), ("Space", 0x2C),
     ("RightAlt", 0xE6), ("RightWin", 0xE7), ("Fn", 0xAF), ("App", 0x65),
     ("RightCtrl", 0xE4),
     ("←", 0x50), ("↓", 0x51), ("→", 0x4F),
     ("Num 0", 0x62), ("Num .", 0x63)),
)

#: What a usage is called, for the hover panel and the staged list. The
#: board's own keys name themselves -- their ident is unique where the
#: printed label is not -- and the quick-select grid names the rest.
NAMES: dict[int, str] = {
    usage: label for row in QUICK_ROWS for label, usage in row}
NAMES.update({k.code: k.ident for k in kb_layout.KEYS})


def _usage_name(usage: int) -> str:
    """What a usage reads as, or its number where nothing names it."""
    if not usage:
        return "nothing"
    return NAMES.get(usage, f"0x{usage:02x}")


class Binding(NamedTuple):
    """What one key was told to do.

    ``usage`` and ``modifiers`` are the record
    :func:`protocol.remap_record` builds, so a binding is still the pair
    the encoder wants. ``kind`` is which of the vendor's key types it
    came from and ``detail`` names the function for the kinds whose name
    is not a key -- a macro's name, a mouse button, a media function.

    A binding of a kind in :data:`HOST_ONLY` carries the key's *own*
    usage: the keyboard cannot be told about a macro or a media key, and
    a record that maps the key to itself is the one record that changes
    nothing on hardware. So the scheme remembers the binding, the
    keyboard keeps typing what the keycap says, and nothing in between
    has to special-case a slot with no record.
    """

    usage: int
    modifiers: int = 0
    kind: str = KEY_FUNCTION
    detail: str = ""

    @classmethod
    def host_only(cls, key: kb_layout.Key, kind: str, detail: str) -> "Binding":
        """A binding the wire cannot carry, staged as a self-map."""
        return cls(key.code, 0, kind, detail)

    @classmethod
    def restored(cls, raw) -> "Binding":
        """One binding out of the scheme file.

        A two-element record is a file written before the kinds existed:
        it is still a usage and a mask, and which kind it was is plain
        from what is in it.
        """
        raw = list(raw)
        usage = int(raw[0]) if raw else 0
        modifiers = int(raw[1]) if len(raw) > 1 else 0
        if len(raw) > 2:
            return cls(usage, modifiers, str(raw[2]),
                       str(raw[3]) if len(raw) > 3 else "")
        if not usage and not modifiers:
            return cls(0, 0, DISABLE, "")
        return cls(usage, modifiers,
                   MULTI_KEY if modifiers else KEY_FUNCTION, "")

    @property
    def carried(self) -> bool:
        """Whether the keyboard is told this, or only the scheme file."""
        return self.kind not in HOST_ONLY

    @property
    def pair(self) -> tuple[int, int]:
        """The ``(usage, modifiers)`` :func:`protocol.remap_block` takes."""
        return (self.usage, self.modifiers)

    def stored(self) -> list:
        """This binding as the scheme file keeps it.

        The first two elements stay the pair, because
        ``Scheme.remap_changes`` reads exactly those and nothing else
        should have to know about the kinds.
        """
        return [self.usage, self.modifiers, self.kind, self.detail]


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


def _modifier_row() -> tuple[QHBoxLayout, list[tuple[QCheckBox, int]]]:
    """The "held with" switches, and the boxes to read them back.

    Built here rather than inline because the side card and the
    custom-function dialog both offer them, and two rows that drifted
    apart would stage two different masks for the same gesture.
    """
    boxes: list[tuple[QCheckBox, int]] = []
    row = QHBoxLayout()
    row.setSpacing(10)
    for name, usage in MODIFIERS:
        box = _toggle(name)
        boxes.append((box, usage))
        row.addWidget(box)
    row.addStretch()
    return row, boxes


def _mask_of(boxes: list[tuple[QCheckBox, int]]) -> int:
    """The record's modifier byte, as the ticked boxes come to."""
    return sum(protocol.MODIFIER_BITS[usage]
               for box, usage in boxes if box.isChecked())


def _tick(boxes: list[tuple[QCheckBox, int]], mask: int) -> None:
    for box, usage in boxes:
        box.setChecked(bool(mask & protocol.MODIFIER_BITS[usage]))


def _tile(text: str, tip: str, on_click) -> QPushButton:
    """One cell of the quick-select region.

    The shared chrome's buttons are padded for a sentence; this grid is
    a hundred of them in six rows, so it has a button of its own.

    The font is set on the widget rather than in the style sheet so the
    width below is measured in the font the cell is actually drawn in.
    Without a minimum of its own a cell is squeezed by its column until
    "Backspace" reads "3ackspac".
    """
    button = QPushButton(text)
    button.setCursor(Qt.PointingHandCursor)
    button.setToolTip(tip)
    font = QFont()
    font.setPointSizeF(7.0)
    button.setFont(font)
    button.setFixedHeight(19)
    button.setMinimumWidth(button.fontMetrics().horizontalAdvance(text) + 12)
    button.setStyleSheet(
        "QPushButton { background:#0a2444; color:#cfe3ff;"
        " border:1px solid #0d3a6b; border-radius:3px; padding:0px 4px; }"
        "QPushButton:hover { color:#ffffff; border-color:#10a9fd; }"
    )
    button.clicked.connect(on_click)
    return button


class CustomFunctionDialog(QDialog):
    """The vendor's Custom Function dialog (manual image 11).

    The key, a key type drop-down over the five kinds, the value that
    type takes, and Apply. Two of the five reach the keyboard: a key
    function and a multi-key combo are both record type 2, which is
    decoded. The other three are not, and the dialog says so rather
    than offering a button that quietly does nothing -- the binding is
    still kept, because the scheme is the host's and a macro assigned
    today should still be there when the encoding is known.
    """

    def __init__(self, key: kb_layout.Key, macros, binding: Binding | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Custom function")
        self.setModal(True)
        self.setMinimumWidth(430)
        self.key = key
        #: The scheme's macros, by name, in the order the macro screen
        #: lists them.
        self.macros = sorted(macros or ())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)
        outer.addWidget(_heading("Custom function"))

        # The key, in the blue box the vendor's dialog opens with.
        self.cap = QLabel(key.name)
        self.cap.setAlignment(Qt.AlignCenter)
        self.cap.setFixedSize(96, 54)
        self.cap.setStyleSheet(
            "background:#10a9fd; color:#04121f; border-radius:4px;"
            " font-size:16px; font-weight:bold;")

        self.kind = _combo(list(KINDS))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.value = _combo()

        fields = QVBoxLayout()
        fields.setSpacing(6)
        fields.addWidget(caption("Key type"))
        fields.addWidget(self.kind)
        fields.addWidget(caption("Function"))
        fields.addWidget(self.value)

        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(self.cap)
        top.addLayout(fields, 1)
        outer.addLayout(top)

        mod_row, self.mods = _modifier_row()
        outer.addWidget(caption("Held with"))
        outer.addLayout(mod_row)

        self.note = _note("")
        outer.addWidget(self.note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(ghost_button("Cancel", self.reject))
        self.apply_button = primary_button("Apply", self.accept)
        buttons.addWidget(self.apply_button)
        outer.addLayout(buttons)
        self.setStyleSheet("QDialog { background:#050b24; }")

        self._show(binding)

    # -- the type in front of you ---------------------------------------
    def _kind_changed(self) -> None:
        """Refill the value field for the type now chosen.

        One field for five types, as the vendor has it: what changes is
        what can go in it.
        """
        kind = self.kind.currentText()
        self.value.clear()
        if kind in (KEY_FUNCTION, MULTI_KEY):
            for row in QUICK_ROWS:
                for label, usage in row:
                    self.value.addItem(label, usage)
        elif kind == MACRO:
            for name in self.macros:
                self.value.addItem(name, 0)
        elif kind == MOUSE:
            for name in MOUSE_FUNCTIONS:
                self.value.addItem(name, 0)
        else:
            for name in MEDIA_FUNCTIONS:
                self.value.addItem(name, 0)

        for box, _ in self.mods:
            box.setEnabled(kind == MULTI_KEY)
        if kind != MULTI_KEY:
            _tick(self.mods, 0)

        self.note.setText(self._honest(kind))
        self.note.setVisible(bool(self.note.text()))
        # A macro can only be chosen once one has been recorded, and an
        # Apply that staged an empty name would be a binding to nothing.
        self.apply_button.setEnabled(self.value.count() > 0)

    def _honest(self, kind: str) -> str:
        """What this type will and will not do, in the dialog itself."""
        if kind == MACRO:
            if not self.macros:
                return ("This scheme has no macros yet — record one on "
                        "the Macro screen first.")
            return ("A macro is record type 3, an id into a macro list "
                    "the host cannot upload: the event encoding is not "
                    "decoded (docs/PROTOCOL.md §8). The binding is kept "
                    "in the scheme, and the key is written to the "
                    "keyboard as itself.")
        if kind in HOST_ONLY:
            return ("The driver carries this as record type 0x0D, "
                    "07 <a> <b> <c>, and those three parameters are not "
                    "decoded (docs/PROTOCOL.md §7). The binding is kept "
                    "in the scheme, and the key is written to the "
                    "keyboard as itself.")
        return ""

    def _show(self, binding: Binding | None) -> None:
        """Open on what the key is already bound to, where it is bound."""
        wanted = binding.kind if binding else KEY_FUNCTION
        self.kind.setCurrentIndex(
            KINDS.index(wanted) if wanted in KINDS else 0)
        # setCurrentIndex only fires on a change, and the first type is
        # index 0 either way, so the fill is asked for by hand.
        self._kind_changed()
        if binding is None:
            return
        _tick(self.mods, binding.modifiers)
        if binding.kind in HOST_ONLY:
            at = self.value.findText(binding.detail)
        else:
            at = self.value.findData(binding.usage)
        if at >= 0:
            self.value.setCurrentIndex(at)

    # -- what it was told -----------------------------------------------
    def binding(self) -> Binding:
        """What Apply stages."""
        kind = self.kind.currentText()
        detail = self.value.currentText()
        if kind in HOST_ONLY:
            return Binding.host_only(self.key, kind, detail)
        usage = int(self.value.currentData() or 0)
        mask = _mask_of(self.mods) if kind == MULTI_KEY else 0
        return Binding(usage, mask, kind, "")


# ----------------------------------------------------------------------
# 1. Keys -- the remap screen
# ----------------------------------------------------------------------
class KeysPage(QWidget):
    """Point at a key, say what it should do, then write the map.

    The vendor's Config File screen is three regions, and so is this one.
    The board is region 2: hovering a key shows its default function and
    its current one (manual images 7-9), right-clicking it opens the
    function panel (image 10), and a key whose function changed is drawn
    in blue (image 12). Under it is region 3, the quick function select:
    click a key on the board, then a function in the grid, and that key
    becomes it. Region 1, the scheme list, is the Profiles screen here.

    The side card is this app's own: the vendor's panel is a menu over
    the board and nothing else, and a visible set of controls says what
    is staged without having to right-click a key to find out.

    Two maps rather than one: the base keymap and the Fn layer are
    separate blocks behind separate commands, and writing either leaves
    the other as it was. So the screen keeps one staged map per layer,
    and the layer combo says which of them every control below it
    means. Nothing is shared between them.

    Writing persists across a power cycle, which is why the write is
    two steps and why the way back has a button of its own.
    """

    #: What a key can be told to do. The first three are the vendor's
    #: right-click panel, in its order; the fourth is the same panel's
    #: Custom Function, which is a dialog rather than a choice.
    ACTIONS = ("Leave it as it is", "Another key", "Disable it",
               "Custom function…")

    #: The two blocks, in the order the layer combo offers them, worded
    #: for the dialogs rather than as a flag.
    LAYERS = ("base keymap", "Fn layer")

    #: How wide the board is drawn here. Smaller than the other screens'
    #: default: the quick-select grid sits under it, and both have to
    #: fit a window the rest of the app already fills.
    BOARD_SCALE = 1.0

    write_requested = Signal(object, bool)   # block, fn_layer
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store.shared()
        #: Each layer's staged map, slot -> Binding. Keyed by the same
        #: bool ``write_requested`` carries, so "which layer" has one
        #: answer on this screen and not two.
        self.layers: dict[bool, dict[int, Binding]] = {False: {}, True: {}}
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
            "driver: it is a remap to usage 0. Macros, mouse and "
            "multimedia functions are kept in the scheme but cannot be "
            "sent yet; the dialog says which is which."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)

        self.board = KeyboardView(self.BOARD_SCALE)
        self.board.key_clicked.connect(self._pick)
        # The vendor's hover panel: two lines over the key under the
        # pointer, its default function and its current one.
        self.board.hover_text = self._describe
        # The same panel the vendor opens on a right-click. The view
        # itself only reports presses, so the menu is asked for here.
        self.board.setContextMenuPolicy(Qt.CustomContextMenu)
        self.board.customContextMenuRequested.connect(self._panel)

        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(_card(self.board))
        left.addWidget(self._quick_region())
        body.addLayout(left, 1)

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
        mod_row, self.mods = _modifier_row()
        for box, _ in self.mods:
            box.setEnabled(False)

        self.staged_caption = caption("Staged, not written")
        self.staged_list = _list()
        self.staged_list.setFixedHeight(90)

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

    # -- region 3, the quick function select ----------------------------
    def _quick_region(self):
        """The grid under the board (manual images 13 and 14).

        A click here needs a key picked on the board first, which is the
        vendor's own order of operations and the reason the picked key
        is ringed.
        """
        icons = QHBoxLayout()
        icons.setSpacing(3)
        for glyph, name in QUICK_ICONS:
            icons.addWidget(_tile(
                glyph, name, lambda _=False, n=name: self._quick_media(n)))
        icons.addStretch()

        grid = QGridLayout()
        grid.setSpacing(3)
        for r, row in enumerate(QUICK_ROWS):
            for c, (label, usage) in enumerate(row):
                grid.addWidget(_tile(
                    label, f"Make the picked key {label}",
                    lambda _=False, lb=label, u=usage: self._quick_key(lb, u),
                ), r, c)

        return _card(
            caption("Quick function select — pick a key on the board, "
                    "then a function here"),
            icons, grid,
        )

    def _quick_key(self, label: str, usage: int) -> None:
        """Bind the picked key to one of the grid's keys."""
        if self._selected is None:
            self.status.emit("pick a key on the board first")
            return
        self._apply(self._selected, Binding(usage, 0, KEY_FUNCTION, ""))
        self.status.emit(
            f"{self._selected.ident} → {label}, staged on the "
            f"{self.LAYERS[self.fn_layer]}")

    def _quick_media(self, name: str) -> None:
        """Bind the picked key to one of the icon row's functions."""
        if self._selected is None:
            self.status.emit("pick a key on the board first")
            return
        self._apply(self._selected,
                    Binding.host_only(self._selected, MEDIA, name))
        self.status.emit(
            f"{self._selected.ident} → {name}, kept in the scheme — the "
            "keyboard cannot be told this one yet")

    # -- the layer in front of you --------------------------------------
    @property
    def fn_layer(self) -> bool:
        """Whether the Fn layer is the one being edited."""
        return self.layer.currentIndex() == 1

    @property
    def staged(self) -> dict[int, Binding]:
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

        Read off ``keymap`` itself rather than ``remap_changes()``: that
        one hands back the pair the encoder wants, which is all anybody
        else needs, but it drops the kind this screen stores beside it.

        A staged map is work done by hand and the keyboard cannot be
        read back, so losing it to closing the window would mean doing
        it again off the keycaps.
        """
        for slot, raw in self.store.current.keymap.items():
            slot = int(slot)
            binding = Binding.restored(raw)
            if slot >= FN_SLOT_BASE:
                self.layers[True][slot - FN_SLOT_BASE] = binding
            else:
                self.layers[False][slot] = binding

    def _remember(self) -> None:
        """Put both staged maps back in the scheme, and ask for a save.

        Written whole rather than patched: the scheme's keymap is this
        screen's state and nobody else's, so there is no half of it to
        preserve.
        """
        self.store.current.keymap = {
            slot + (FN_SLOT_BASE if fn else 0): binding.stored()
            for fn, staged in self.layers.items()
            for slot, binding in staged.items()
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
        # Ringed as well as outlined: the quick-select grid acts on this
        # key, and the grid is far enough from the board that "which
        # key" has to be answerable without looking back at the card.
        self.board.selected = key
        self.board.chosen = {key.light_index}
        self.board.update()

        # The controls show this key's own staging, so that a second
        # look at a key says something true about it rather than
        # leaving the previous key's answer standing.
        binding = self.staged.get(self._slot(key))
        if binding is None:
            self.action.setCurrentIndex(0)
            self._show_mask(0)
            return
        if binding.kind == DISABLE:
            self.action.setCurrentIndex(2)
            self._show_mask(0)
            return
        if not binding.carried:
            # Nothing in the combo says "macro" or "mute"; the dialog
            # does, and that is where the combo points.
            self.action.setCurrentIndex(3)
            self._show_mask(0)
            return
        self.action.setCurrentIndex(1)
        self._show_mask(binding.modifiers)
        target = BY_CODE.get(binding.usage)
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
        return _mask_of(self.mods)

    def _show_mask(self, mask: int) -> None:
        _tick(self.mods, mask)

    # -- what a key is doing --------------------------------------------
    def _function(self, binding: Binding) -> str:
        """What a binding reads as: "Ctrl+C", "macro "Greeting"".

        The kinds the wire cannot carry say so here rather than only in
        the dialog that staged them: the staged list and the hover panel
        are where a map is read back, and a binding that looks like
        every other one is how a key gets taken for remapped.
        """
        if binding.kind == DISABLE or not (binding.usage or binding.modifiers):
            return "nothing"
        if binding.kind == MACRO:
            return f'macro "{binding.detail}" (not sent)'
        if not binding.carried:
            return f"{binding.detail} (not sent)"
        parts = [name for name, held in MODIFIERS
                 if binding.modifiers & protocol.MODIFIER_BITS[held]]
        if binding.usage:
            parts.append(_usage_name(binding.usage))
        return "+".join(parts)

    def _label(self, binding: Binding) -> str:
        """What a staged record reads as: "→ Ctrl+Shift+C"."""
        return "→ " + self._function(binding)

    def _describe(self, key: kb_layout.Key) -> str:
        """The vendor's hover panel, as two lines (manual images 7-9).

        Default key function and Current key function: what the keycap says, and what the
        key has been told to do instead. The second line is the first
        one again where nothing is staged, which is the vendor's own
        answer and not a gap.
        """
        binding = self.staged.get(self._slot(key))
        now = _usage_name(key.code) if binding is None else self._function(binding)
        return (f"Default function: {_usage_name(key.code)}\n"
                f"Current function: {now}")

    # -- the right-click panel ------------------------------------------
    def _panel(self, pos) -> None:
        """The vendor's function panel over a key (manual image 10)."""
        key = self.board.key_at(QPointF(pos))
        if key is None:
            return
        # A right-click picks the key as well as opening the panel: the
        # three entries all act on one key, and that key is this one.
        self._pick(key)
        self.function_panel().exec(self.board.mapToGlobal(pos))

    def function_panel(self) -> QMenu:
        """The three entries the vendor's panel offers, in its order."""
        menu = QMenu(self)
        menu.addAction("Default function", self._default_function)
        menu.addAction("Disable key", self._disable_key)
        menu.addAction("Custom function…", self._custom_function)
        return menu

    def _default_function(self) -> None:
        """Default function: the key goes back to doing what it says it does."""
        if self._selected is not None:
            self._apply(self._selected, None)
            self.action.setCurrentIndex(0)

    def _disable_key(self) -> None:
        """Disable key: a remap to usage 0."""
        if self._selected is not None:
            self._apply(self._selected, Binding(0, 0, DISABLE, ""))
            self.action.setCurrentIndex(2)

    def _custom_function(self) -> None:
        """Custom Function: the dialog, and what it staged if it was applied."""
        key = self._selected
        if key is None:
            self.status.emit("pick a key on the board first")
            return
        dialog = CustomFunctionDialog(
            key, self.store.current.macros,
            self.staged.get(self._slot(key)), self)
        if dialog.exec() != QDialog.Accepted:
            return
        binding = dialog.binding()
        self._apply(key, binding)
        if binding.carried:
            self.status.emit(f"{key.ident} {self._label(binding)}, staged "
                             f"on the {self.LAYERS[self.fn_layer]}")
        else:
            self.status.emit(
                f"{key.ident} {self._label(binding)} — kept in the scheme, "
                "the keyboard cannot be told this one yet")

    # -- staging --------------------------------------------------------
    def _apply(self, key: kb_layout.Key, binding: Binding | None) -> None:
        """Stage one binding, or take the key out of the staged map."""
        slot = self._slot(key)
        if binding is None:
            self.staged.pop(slot, None)
        else:
            self.staged[slot] = binding
        self._remember()
        self._redraw_staged()

    def _stage(self) -> None:
        if self._selected is None:
            self.status.emit("pick a key on the board first")
            return
        index = self.action.currentIndex()
        if index == 3:
            # The fourth choice is the panel's dialog, not a record:
            # what it stages, it stages itself.
            self._custom_function()
            return
        if index == 0:
            self._apply(self._selected, None)
        elif index == 1:
            target = kb_layout.KEYS[self.target.currentIndex()]
            mask = self._mask()
            self._apply(self._selected, Binding(
                target.code, mask, MULTI_KEY if mask else KEY_FUNCTION, ""))
        else:
            self._apply(self._selected, Binding(0, 0, DISABLE, ""))

    def _unstage(self) -> None:
        if self._selected is not None:
            self._apply(self._selected, None)

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
        for slot, binding in sorted(staged.items()):
            key = by_slot.get(slot)
            name = key.ident if key else f"slot {slot}"
            lines.append(f"{name:<12} {self._label(binding)}")
            self.staged_list.addItem(QListWidgetItem(lines[-1]))

        # The board shows what is staged, so the map is readable without
        # cross-checking the list against the keycaps. Blue for a key
        # whose function changed, as the vendor draws it (manual image
        # 12); the one exception is a disabled key, which is a loss
        # rather than a remap and is worth telling apart.
        self.board.colours = {
            by_slot[slot].light_index:
                DISABLED if binding.kind == DISABLE else ACCENT
            for slot, binding in staged.items()
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
        return protocol.remap_block(
            {slot: binding.pair
             for slot, binding in self.layers[fn_layer].items()})

    def _write(self) -> None:
        staged = self.staged
        if not staged:
            self.status.emit("nothing staged")
            return
        where = self.LAYERS[self.fn_layer]
        kept = sum(1 for binding in staged.values() if not binding.carried)
        if QMessageBox.question(
                self, f"Write the {where}",
                f"{len(staged)} key(s) on the {where} will be changed "
                "on the keyboard itself, and will stay changed after a "
                "power cycle. The other layer is left as it is.\n\n"
                + (f"{kept} of them is a macro, mouse or multimedia "
                   "function, which no record can carry yet: those keys "
                   "go out mapped to themselves and keep their binding "
                   "in the scheme.\n\n" if kept else "")
                + "Restore the default map to undo it.",
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
