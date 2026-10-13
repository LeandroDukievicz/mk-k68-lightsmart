"""The macro screen: record and edit; the wire encoding is still open."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import QElapsedTimer, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QMessageBox,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import layout as kb_layout
from .. import store as scheme_store
from ..gui import caption, ghost_button, primary_button
from .common import _card, _combo, _heading, _list, _note

#: The kinds of event the vendor's recorder produces, in the order its
#: own list offers them. These strings are what the scheme file holds as
#: well as what the screen shows, so renaming one orphans every macro
#: already saved -- which is also why a kind is only ever added to the
#: end of this tuple.
KEY_DOWN = "Key down"
KEY_UP = "Key up"
DELAY = "Delay (ms)"
MOUSE = "Mouse button"
MOUSE_DOWN = "Mouse down"
MOUSE_UP = "Mouse up"
WHEEL = "Mouse wheel"
KINDS = (KEY_DOWN, KEY_UP, DELAY, MOUSE, MOUSE_DOWN, MOUSE_UP, WHEEL)

#: The kinds whose value names a mouse button. ``MOUSE`` is the whole
#: click the insert panel stages; the recorder splits a real click into
#: the pair, because a button held down across a delay is a thing a
#: macro can mean and one event could not say it.
MOUSE_KINDS = (MOUSE, MOUSE_DOWN, MOUSE_UP)

#: Which mouse button a Qt event is about. Back and forward are absent
#: on purpose: the vendor's own event offers three buttons, and a name
#: this screen cannot offer in its own list is one nobody could edit.
MOUSE_BY_BUTTON = {
    Qt.LeftButton: "Left",
    Qt.RightButton: "Right",
    Qt.MiddleButton: "Middle",
}

#: The three mouse buttons a mouse event can carry, named as the value
#: list offers them. Derived from the lookup above so that the names the
#: recorder writes and the names the editor offers cannot drift apart.
MOUSE_BUTTONS = tuple(MOUSE_BY_BUTTON.values())

#: Which way the wheel turned. A notch is one event and not a pair:
#: there is nothing to hold down.
WHEEL_WAYS = ("Up", "Down")

#: What recording does with the time between keystrokes -- the vendor's
#: three choices, which are the only three there are.
KEEP_DELAYS = "Keep the real delays"
NO_DELAY = "No delay"
ONE_DELAY = "One delay for all"
POLICIES = (KEEP_DELAYS, NO_DELAY, ONE_DELAY)

#: The vendor's insert panel is a dropdown and one button rather than a
#: button each, and these are the two things it can say.
INSERT_BEFORE = "Insert before the selected event"
INSERT_AFTER = "Insert after the selected event"
PLACES = (INSERT_BEFORE, INSERT_AFTER)

#: Stamped into an exported file so an unrelated JSON document is
#: refused rather than read as a macro with nothing in it.
FILE_KIND = "mk-k68-lightsmart-macro"

#: The macro Copy last took, waiting for a Paste: its name and its
#: events. On the module rather than on the screen so that a copy
#: outlives the screen being rebuilt and crosses between schemes --
#: copy in one scheme, switch, paste into the other -- which is the
#: only thing Paste can do that Duplicate cannot.
_copied: tuple[str, list["Event"]] | None = None


class Event(NamedTuple):
    """One step of a macro: what happens, and what it happens to.

    Two fields and no more, deliberately. The scheme file keeps an
    event as ``{"kind", "value"}`` and a delay's milliseconds are a
    number in that same ``value``, so a third field would be a second
    home for them and the two could drift apart. :attr:`ms` derives it
    instead, which also leaves an event immutable -- copying a macro is
    then a copy of its list and nothing deeper.
    """

    kind: str
    value: str

    @classmethod
    def delay(cls, ms: int) -> "Event":
        return cls(DELAY, str(int(ms)))

    @property
    def ms(self) -> int:
        """The wait in milliseconds; 0 for anything that is not a wait.

        Tolerant on purpose. The value can have come from a file
        somebody edited by hand, and one unreadable delay should cost
        that delay rather than the whole macro.
        """
        if self.kind != DELAY:
            return 0
        try:
            return int(float(self.value))
        except ValueError:
            return 0

    def label(self) -> str:
        """How the event reads in the list.

        Press and release are spelled out rather than drawn as arrows.
        The vendor uses arrows, but this board's own cursor keys are
        *named* ``↑`` and ``↓`` in ``layout.KEYS``, so an arrow beside a
        key name would read as a second key.
        """
        if self.kind == DELAY:
            return f"wait     {self.ms} ms"
        if self.kind == KEY_DOWN:
            return f"press    {self.value}"
        if self.kind == KEY_UP:
            return f"release  {self.value}"
        if self.kind == MOUSE:
            return f"mouse    {self.value}"
        if self.kind == MOUSE_DOWN:
            return f"mouse    {self.value} down"
        if self.kind == MOUSE_UP:
            return f"mouse    {self.value} up"
        if self.kind == WHEEL:
            return f"wheel    {self.value}"
        return f"{self.kind}  {self.value}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value}


def as_event(raw) -> Event:
    """Coerce whatever is holding an event into an :class:`Event`.

    Events arrive as dicts from the scheme file and from an imported
    one, and as bare pairs from this screen's older callers, so the
    coercion sits here rather than at every edge that reads one.
    """
    if isinstance(raw, Event):
        return raw
    if isinstance(raw, dict):
        return Event(str(raw.get("kind", KEY_DOWN)),
                     str(raw.get("value", "")))
    kind, value = (list(raw) + ["", ""])[:2]
    return Event(str(kind), str(value))


def value_choices(kind: str) -> list[str]:
    """What the value control can offer for a kind of event.

    One list per kind rather than one list with everything in it: the
    vendor's panel swaps its field with the radio above it, and a key
    name in a mouse event is not an event anybody meant.
    """
    if kind == WHEEL:
        return list(WHEEL_WAYS)
    if kind in MOUSE_KINDS:
        return list(MOUSE_BUTTONS)
    return [k.ident for k in kb_layout.KEYS]


def events_from_recording(
        stamps, policy: str = KEEP_DELAYS, fixed_ms: int = 10,
) -> list[Event]:
    """Turn a recording into events, under one of the delay policies.

    ``stamps`` is what :class:`RecordDialog` collects: ``(at_ms, kind,
    value)`` in the order the keys moved. The gap before the first
    event is never kept -- that is the time between clicking Record and
    starting to type, which belongs to the operator, not the macro.
    """
    events: list[Event] = []
    previous: int | None = None
    for at, kind, value in stamps:
        if previous is not None:
            if policy == ONE_DELAY:
                events.append(Event.delay(fixed_ms))
            elif policy == KEEP_DELAYS and at > previous:
                # A gap the clock rounded away is not worth an event of
                # its own; it would only pad the list out.
                events.append(Event.delay(at - previous))
        events.append(Event(kind, str(value)))
        previous = at
    return events


def write_macro(path, name: str, events) -> None:
    """Write one macro out as its own file, to pass to somebody else."""
    Path(path).write_text(json.dumps({
        "kind": FILE_KIND,
        "name": name,
        "events": [as_event(e).to_dict() for e in events],
    }, indent=1))


def read_macro(path) -> tuple[str, list[Event]]:
    """Read an exported macro back, name and all.

    Raises ``ValueError`` on anything that is not one, so the caller can
    say so plainly: a macro half-read out of somebody else's file would
    type something nobody asked for.
    """
    raw = json.loads(Path(path).read_text())
    # A bare list is taken as well as a whole file: that is what a macro
    # looks like inside the scheme file, so somebody who pasted one out
    # of there should not be told it is the wrong shape.
    if isinstance(raw, list):
        return "", [as_event(e) for e in raw]
    if not isinstance(raw, dict) or not isinstance(raw.get("events"), list):
        raise ValueError("no events in it")
    return str(raw.get("name") or ""), [as_event(e) for e in raw["events"]]


#: Qt key codes whose name on this board is not the text they type.
#:
#: Qt reports one code for both shifts, and on X11 one for both control
#: keys, so a recorded modifier lands on the left-hand one. Telling
#: them apart means a native scan code, which is right more often and
#: differently wrong on every platform; the event can be edited in
#: place instead, which is cheaper than being subtly wrong.
SPECIAL_KEYS = {
    Qt.Key_Escape: "Esc",
    Qt.Key_Tab: "Tab",
    Qt.Key_Backtab: "Tab",
    Qt.Key_Backspace: "Backspace",
    Qt.Key_Return: "Enter",
    Qt.Key_Enter: "Enter",
    Qt.Key_CapsLock: "Caps",
    Qt.Key_Space: "Space",
    Qt.Key_Delete: "Del",
    Qt.Key_PageUp: "PgUp",
    Qt.Key_PageDown: "PgDn",
    Qt.Key_Up: "↑",
    Qt.Key_Down: "↓",
    Qt.Key_Left: "←",
    Qt.Key_Right: "→",
    Qt.Key_Shift: "LeftShift",
    Qt.Key_Control: "Control",
    Qt.Key_Alt: "LeftAlt",
    Qt.Key_AltGr: "RightAlt",
    Qt.Key_Meta: "Win",
}


def key_name(event) -> str:
    """The board's name for the key a Qt event is about.

    Falls back to whatever Qt calls it. Somebody recording on a
    keyboard that has a key this 68-key board does not should get a
    recognisable event they can edit, not a silent nothing.
    """
    code = event.key()
    if code in SPECIAL_KEYS:
        return SPECIAL_KEYS[code]
    typed = (event.text() or "").strip().upper()
    if typed in kb_layout.BY_IDENT:
        return typed
    return QKeySequence(code).toString() or typed or f"0x{code:02x}"


def mouse_name(button) -> str:
    """The name of a mouse button, or ``""`` for one with no name here.

    Empty rather than a made-up name: a button this screen cannot offer
    in its value list is one nobody could edit afterwards, so the
    recorder drops it instead of recording something uneditable.
    """
    return MOUSE_BY_BUTTON.get(button, "")


def _spin(value: int, top: int = 60000) -> QSpinBox:
    """A number box in the chrome the shared combo box wears."""
    box = QSpinBox()
    box.setRange(1, top)
    box.setValue(value)
    box.setStyleSheet(
        "QSpinBox { background:#0a2444; color:#cfe3ff;"
        " border:1px solid #0d3a6b; border-radius:3px; padding:3px; }"
        "QSpinBox:disabled { color:#44607f; border-color:#0a2748; }")
    return box


def _radio(text: str) -> QRadioButton:
    """One of a set of choices, in the same chrome as everything else.

    The shared chrome has no radio button because this is the only
    screen with a set of them, so the style lives beside its only use.
    """
    button = QRadioButton(text)
    button.setStyleSheet(
        "QRadioButton { color:#cfe3ff; font-size:11px; spacing:5px; }"
        "QRadioButton::indicator { width:11px; height:11px;"
        " border:1px solid #0d3a6b; border-radius:6px; background:#0a2444; }"
        "QRadioButton::indicator:checked { background:#10a9fd;"
        " border-color:#10a9fd; }"
    )
    return button


def _menu(parent: QWidget) -> QMenu:
    """A popup menu wearing the chrome the lists and combos wear."""
    menu = QMenu(parent)
    menu.setStyleSheet(
        "QMenu { background:#0a2444; color:#cfe3ff; font-size:11px;"
        " border:1px solid #0d3a6b; }"
        "QMenu::item { padding:5px 18px; }"
        "QMenu::item:selected { background:#0d3a6b; }"
        "QMenu::item:disabled { color:#44607f; }"
    )
    return menu


class RecordDialog(QDialog):
    """Catch real keystrokes, with the real time between them.

    The keyboard is grabbed for as long as this is open. Without the
    grab, recording a Tab or a Space would walk and press this dialog's
    own buttons instead of being recorded, and recording an Esc would
    shut it. For the same reason recording stops on a button and never
    on a key: every key on the board has to be recordable, which rules
    out holding one back to mean "stop".

    The mouse is deliberately *not* grabbed, which is what decides how
    much of it is recorded. A button press and a wheel notch over the
    dialog's own body are recorded, because Qt hands those to the
    dialog; a click on Stop or Discard is not, because the button under
    the pointer takes it first -- so the pair of buttons still works
    while the rest of the dialog is a mouse-recording surface. Pointer
    movement is recorded by neither: a macro of this board's carries no
    coordinates (docs/PROTOCOL.md §8), and tracking it would bury the
    clicks under hundreds of events nobody asked for.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Recording a macro")
        self.setModal(True)
        self.setMinimumWidth(380)
        #: ``(ms since recording started, kind, name)``, in order.
        self.stamps: list[tuple[int, str, str]] = []
        # Started here as well as on show: a stamp taken before the
        # dialog is up would otherwise be read off an unstarted clock.
        self._clock = QElapsedTimer()
        self._clock.start()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)
        outer.addWidget(_heading("Recording"))
        hint = caption(
            "Type the macro now. Every press and every release is kept, "
            "with the time between them. Nothing reaches the rest of the "
            "app while this is open, so Esc and Enter record like any "
            "other key — stop with the button. Mouse buttons and the "
            "wheel record too, over this panel; the two buttons below "
            "take their own clicks, and the pointer's travels are not "
            "recorded at all.")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        self.tally = caption("nothing yet")
        outer.addWidget(self.tally)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(ghost_button("Discard", self.reject))
        row.addWidget(primary_button("Stop recording", self.accept))
        outer.addLayout(row)
        self.setStyleSheet("QDialog { background:#050b24; }")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.grabKeyboard()
        self._clock.restart()

    def hideEvent(self, event) -> None:
        # Handed back whichever way the dialog closed. A grab left in
        # place would deafen the whole application.
        self.releaseKeyboard()
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.isAutoRepeat():
            # A held key repeats at the host's rate. A macro wants the
            # one press and the wait, not a hundred presses.
            return
        self._stamp(KEY_DOWN, key_name(event))

    def keyReleaseEvent(self, event) -> None:
        if event.isAutoRepeat():
            return
        self._stamp(KEY_UP, key_name(event))

    def mousePressEvent(self, event) -> None:
        self._mouse(MOUSE_DOWN, event)

    def mouseReleaseEvent(self, event) -> None:
        self._mouse(MOUSE_UP, event)

    def _mouse(self, kind: str, event) -> None:
        # A nameless button -- back, forward, the ones down the side of
        # a gaming mouse -- is dropped rather than guessed at.
        name = mouse_name(event.button())
        if name:
            self._stamp(kind, name)

    def wheelEvent(self, event) -> None:
        # Only the vertical half: a wheel that tilts sideways is not
        # something this board's own event can say.
        turned = event.angleDelta().y()
        if turned:
            self._stamp(WHEEL, WHEEL_WAYS[0] if turned > 0 else WHEEL_WAYS[1])

    def _stamp(self, kind: str, value: str) -> None:
        # Deliberately no call up to QDialog from any of the handlers:
        # the base class closes on Esc and fires the default button on
        # Enter, which would make those two keys unrecordable.
        self.stamps.append((self._clock.elapsed(), kind, value))
        self.tally.setText(f"{len(self.stamps)} events so far")

    def events(self, policy: str, fixed_ms: int) -> list[Event]:
        """What was typed, under the policy chosen on the screen."""
        return events_from_recording(self.stamps, policy, fixed_ms)


# ----------------------------------------------------------------------
# 2. Macro
# ----------------------------------------------------------------------
class MacroPage(QWidget):
    """A finished macro editor for a keyboard that cannot be told one.

    Recording, editing, keeping, importing and exporting all work, and
    a macro is saved with the scheme the moment it changes. What is
    missing is one thing, and it is not on this screen: the encoding of
    an event on the wire. The command and its framing are decoded --
    ``0x15``, announced with a block count, then 64-byte blocks -- but
    a key event is a ``0x50`` marker and two bytes nobody has pinned
    down (docs/PROTOCOL.md §8), so there is nothing to fill the blocks
    with. When those two bytes are known, the macro to send is already
    here.

    The vendor's own screen is the model, down to its three columns:
    the macro list on the left, the insert-event panel over the basic
    commands and the recorder in the middle, and the macro record on
    the right with its own small toolbar and a save. Its right-click
    menu over the list is here too, Paste and all, and so are its three
    delay policies.
    """

    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        #: name -> its events: the working copy of ``scheme.macros``,
        #: kept as objects here and as dicts in the file.
        self.macros: dict[str, list[Event]] = {}
        #: What the value control is offering, so that a kind change
        #: that does not change the list leaves what was typed alone.
        self._offered: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("Macros"))
        outer.addWidget(_note(
            "Complete as an editor, and it sends nothing. Recording, "
            "editing, import and export all work, and a macro is saved "
            "with the scheme as soon as it changes. The command and its "
            "framing are known — 0x15, announced with a block count, "
            "then 64-byte blocks — but an event on the wire is not: a "
            "key emits a 0x50 marker and two bytes whose meaning is "
            "still open (PROTOCOL.md §8). Once those two bytes are "
            "decoded, the send path is all that is left to add."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)

        # -- the macro list, the vendor's far-left column ---------------
        self.names = _list()
        self.names.currentTextChanged.connect(self._show)
        # The vendor puts new, import and paste on the blank space of
        # this list and nowhere else, so the menu is not a duplicate of
        # the buttons: Paste has no button.
        self.names.setContextMenuPolicy(Qt.CustomContextMenu)
        self.names.customContextMenuRequested.connect(self._names_menu)
        left = _card(caption("Macro list"), self.names)
        left.setFixedWidth(210)
        body.addWidget(left)

        # -- insert, the basic commands, and recording ------------------
        self.where = _combo(list(PLACES))
        self.kind = _combo(list(KINDS))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.value = _combo()
        self.value.setEditable(True)
        self.ms = _spin(50)
        value_row = QHBoxLayout()
        value_row.addWidget(self.value, 1)
        value_row.addWidget(self.ms)

        insert_row = QHBoxLayout()
        insert_row.addWidget(primary_button("Insert", self._insert_here))
        insert_row.addWidget(ghost_button("Append", self._add))
        insert_row.addWidget(ghost_button("Change", self._replace))
        insert_row.addStretch()

        made = QHBoxLayout()
        made.addWidget(primary_button("New", self._new))
        made.addWidget(ghost_button("Rename", self._rename))
        made.addWidget(ghost_button("Delete", self._delete))
        made.addStretch()
        kept = QHBoxLayout()
        kept.addWidget(ghost_button("Copy", self._copy))
        kept.addWidget(ghost_button("Paste", self._paste))
        kept.addWidget(ghost_button("Duplicate", self._duplicate))
        kept.addStretch()
        shared = QHBoxLayout()
        shared.addWidget(ghost_button("Import…", self._import))
        shared.addWidget(ghost_button("Export…", self._export))
        shared.addStretch()

        # Radio buttons rather than a combo box, as the vendor draws
        # them: all three policies are worth seeing at once, because
        # choosing one is part of setting up the recording.
        # The number box first: checking a radio below tells the policy
        # handler to enable or grey it, and it has to exist by then.
        self.delay_ms = _spin(10)
        self.policies = {name: _radio(name) for name in POLICIES}
        self._policy_group = QButtonGroup(self)
        for radio in self.policies.values():
            self._policy_group.addButton(radio)
            radio.toggled.connect(self._policy_changed)
        self.policies[KEEP_DELAYS].setChecked(True)
        fixed_row = QHBoxLayout()
        fixed_row.addWidget(self.policies[ONE_DELAY])
        fixed_row.addWidget(self.delay_ms)
        fixed_row.addWidget(caption("ms"))
        fixed_row.addStretch()
        record_row = QHBoxLayout()
        record_row.addWidget(primary_button("Record…", self._record))
        record_row.addStretch()

        middle = _card(
            caption("Insert event"), self.where, self.kind, value_row,
            insert_row,
            caption("Basic commands"), made, kept, shared,
            caption("Recording: the gaps between keystrokes"),
            self.policies[KEEP_DELAYS], self.policies[NO_DELAY], fixed_row,
            record_row,
        )
        middle.setFixedWidth(340)
        body.addWidget(middle)

        # -- the macro record, and the one thing it cannot do -----------
        self.events = _list()
        self.events.currentRowChanged.connect(self._pull)
        # The rows are a number, a word and a name in columns, and a
        # proportional font would let the columns wander.
        self.events.setFont(QFont("monospace"))

        # Above the list and to the right, where the vendor's three
        # icons sit: they act on the row selected in the list below.
        # Worded rather than drawn, and told apart from the commands in
        # the middle column -- which delete and reorder macros -- by a
        # tooltip each, because the words alone are the same words.
        order_row = QHBoxLayout()
        order_row.addStretch()
        for text, what, says in (
                ("Delete", self._remove, "Delete the selected event"),
                ("Up", lambda: self._move(-1), "Move it one row up"),
                ("Down", lambda: self._move(1), "Move it one row down")):
            button = ghost_button(text, what)
            button.setToolTip(says)
            order_row.addWidget(button)

        # On the screen and disabled, so the one missing piece has a
        # place rather than being absent without explanation.
        self.send = primary_button("Send to keyboard", self._send)
        self.send.setEnabled(False)
        self.send.setToolTip(
            "Not yet: an event on the wire is a 0x50 marker and two "
            "undecoded bytes. See docs/PROTOCOL.md §8.")
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_row.addWidget(self.send)
        save_row.addWidget(primary_button("Save", self._save))

        body.addWidget(_card(
            caption("Macro record"), order_row, self.events, save_row,
        ), 1)
        outer.addLayout(body, 1)
        self._kind_changed()
        self._policy_changed()

    # -- the scheme file ------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._load()

    def _load(self) -> None:
        """Take the macros from whichever scheme is current.

        Read when the screen comes up rather than once at construction:
        the profiles screen can switch scheme while this one is out of
        sight, and the macros on show belong to the scheme in use.
        Nothing is lost by re-reading, because every edit here has
        already been written.
        """
        chosen = self._current()
        self.macros = {
            str(name): [as_event(e) for e in events]
            for name, events
            in scheme_store.shared().current.macros.items()
        }
        self.names.clear()
        self.names.addItems(list(self.macros))
        if chosen in self.macros:
            self._select(chosen)
        elif self.macros:
            self.names.setCurrentRow(0)
        else:
            self._show("")

    def _persist(self) -> bool:
        """Put the macros back in the scheme and write the file.

        Every edit saves. A scheme is a couple of kilobytes and an edit
        is a click, so the write costs nothing against the alternative:
        losing a macro somebody typed out because the window closed
        without asking this screen for it. Says whether it got there,
        which is what the Save button reports.
        """
        store = scheme_store.shared()
        store.current.macros = {
            name: [as_event(e).to_dict() for e in events]
            for name, events in self.macros.items()
        }
        try:
            store.save()
        except OSError as exc:
            # A config directory that cannot be written to should cost
            # the save, not the editing.
            self.status.emit(f"macros not saved: {exc}")
            return False
        return True

    def _save(self) -> None:
        """Write the macros out and say so, as the vendor's save does.

        Every edit here has already saved, so this changes nothing on
        disk -- but somebody who has just recorded a macro wants to be
        told it is kept rather than to take it on trust, and if the
        config directory has gone away this is where they find out.
        """
        if self._persist():
            kept = len(self.macros)
            self.status.emit(
                f"{kept} macro{'' if kept == 1 else 's'} saved")

    # -- the list of macros ---------------------------------------------
    def _current(self) -> str | None:
        item = self.names.currentItem()
        return item.text() if item else None

    def _select(self, name: str) -> None:
        for row in range(self.names.count()):
            if self.names.item(row).text() == name:
                self.names.setCurrentRow(row)
                return

    def _free_name(self, wanted: str) -> str:
        """``wanted``, or the first numbered variant nobody has taken.

        Macros are keyed by name, so a collision on import or duplicate
        would quietly overwrite the one already there.
        """
        if wanted and wanted not in self.macros:
            return wanted
        stem = wanted or "Macro"
        n = 2
        while f"{stem} {n}" in self.macros:
            n += 1
        return f"{stem} {n}"

    def _adopt(self, name: str, events: list[Event]) -> str:
        """Put a macro from somewhere else in the list, and keep it.

        Import, duplicate and paste all arrive at this: a free name, a
        row, a save and the selection. Said once so the three cannot
        come to disagree about what arriving means.
        """
        name = self._free_name(name)
        self.macros[name] = list(events)
        self.names.addItem(name)
        self._persist()
        self._select(name)
        return name

    def _names_menu(self, pos) -> None:
        """The right-click menu, where the vendor's own list has one."""
        item = self.names.itemAt(pos)
        if item is not None:
            # The lower half of the menu is about the macro under the
            # pointer, which need not be the one already selected.
            self.names.setCurrentItem(item)
        self._macro_menu(item is not None).exec(
            self.names.viewport().mapToGlobal(pos))

    def _macro_menu(self, over_macro: bool) -> QMenu:
        """What the right-click menu offers; built apart from showing it.

        Blank space gets the vendor's three -- new, import, paste --
        which are also the only three a list with nothing in it can
        offer. Over a macro the menu carries that macro's own commands
        as well, so the buttons in the middle column are a second way
        rather than the only one.
        """
        menu = _menu(self)
        menu.addAction("New", self._new)
        menu.addAction("Import…", self._import)
        paste = menu.addAction("Paste", self._paste)
        # Disabled rather than hidden: a menu that changes shape is one
        # nobody learns the shape of.
        paste.setEnabled(_copied is not None)
        if over_macro:
            menu.addSeparator()
            menu.addAction("Copy", self._copy)
            menu.addAction("Duplicate", self._duplicate)
            menu.addAction("Rename", self._rename)
            menu.addAction("Export…", self._export)
            menu.addAction("Delete", self._delete)
        return menu

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New macro", "Name")
        if not ok or not name:
            return
        self._adopt(name, [])

    def _rename(self) -> None:
        old = self._current()
        if not old:
            return
        new, ok = QInputDialog.getText(
            self, "Rename macro", "Name", text=old)
        if not ok or not new or new == old:
            return
        if new in self.macros:
            self.status.emit(f"there is already a macro called '{new}'")
            return
        # Rebuilt rather than popped and re-added: the dict's order is
        # the order the list shows, and renaming should not reshuffle it.
        self.macros = {(new if k == old else k): v
                       for k, v in self.macros.items()}
        self.names.currentItem().setText(new)
        self._persist()

    def _duplicate(self) -> None:
        name = self._current()
        if not name:
            return
        # A shallow copy is a whole one: an Event is immutable, so the
        # two macros can share events without sharing a future.
        copy = self._adopt(f"{name} copy", self.macros[name])
        self.status.emit(f"duplicated as '{copy}'")

    def _copy(self) -> None:
        """Take a copy of the selected macro, for a later Paste.

        The vendor's basic commands have a copy and its list's menu has
        a paste, which is the pair: Duplicate is the two in one step
        and cannot leave the scheme it is in, while this can.
        """
        global _copied
        name = self._current()
        if not name:
            self.status.emit("pick a macro to copy")
            return
        _copied = (name, list(self.macros[name]))
        self.status.emit(f"copied '{name}'")

    def _paste(self) -> None:
        """Put the copied macro in this list, under a free name."""
        if _copied is None:
            self.status.emit("nothing has been copied")
            return
        name, events = _copied
        self.status.emit(
            f"pasted {len(events)} events as '{self._adopt(name, events)}'")

    def _delete(self) -> None:
        name = self._current()
        if not name:
            return
        self.macros.pop(name, None)
        self.names.takeItem(self.names.currentRow())
        self._persist()
        self._show(self._current() or "")

    # -- the list of events ---------------------------------------------
    def _show(self, name: str) -> None:
        self.events.clear()
        for n, raw in enumerate(self.macros.get(name, []), 1):
            self.events.addItem(f"{n:>3}  {as_event(raw).label()}")

    def _kind_changed(self) -> None:
        """Offer what the chosen kind can take, and nothing else."""
        kind = self.kind.currentText()
        offered = value_choices(kind)
        if offered != self._offered:
            self._offered = offered
            self.value.clear()
            self.value.addItems(offered)
        self.value.setEnabled(kind != DELAY)
        self.ms.setEnabled(kind == DELAY)

    def _policy_changed(self) -> None:
        """Only one of the three policies has a number to set."""
        self.delay_ms.setEnabled(self.policies[ONE_DELAY].isChecked())

    def _policy(self) -> str:
        """Which of the three the radio buttons are saying."""
        for name, radio in self.policies.items():
            if radio.isChecked():
                return name
        return KEEP_DELAYS

    def _staged(self) -> Event:
        """The event the controls are describing."""
        kind = self.kind.currentText()
        if kind == DELAY:
            return Event.delay(self.ms.value())
        return Event(kind, self.value.currentText())

    def _pull(self, row: int) -> None:
        """Load the selected event into the controls.

        This is what makes editing one in place a matter of picking it,
        changing a control and pressing Change. The alternative is
        remove, retype, insert, which loses the place in the list.
        """
        name = self._current()
        if not name or not 0 <= row < len(self.macros.get(name, [])):
            return
        event = as_event(self.macros[name][row])
        if event.kind in KINDS:
            self.kind.setCurrentIndex(KINDS.index(event.kind))
        if event.kind == DELAY:
            self.ms.setValue(max(1, event.ms))
        else:
            self.value.setCurrentText(event.value)

    def _add(self) -> None:
        name = self._current()
        if not name:
            self.status.emit("make a macro first")
            return
        self.macros[name].append(self._staged())
        self._persist()
        self._show(name)
        self.events.setCurrentRow(len(self.macros[name]) - 1)

    def _insert_here(self) -> None:
        """Insert where the panel's dropdown says, which is its point.

        The vendor's panel is a dropdown and one button rather than a
        button each, and the dropdown is the thing that remembers: a
        run of events inserted after one another is one choice made
        once, then Insert, Insert, Insert.
        """
        self._insert(self.where.currentText() == INSERT_AFTER)

    def _insert(self, after: bool) -> None:
        """Put the staged event either side of the selected one.

        The vendor offers exactly this pair and no plain insert, and it
        is the right pair: with only an append, adding an event in the
        middle means adding it at the end and walking it up.
        """
        name, row = self._current(), self.events.currentRow()
        if not name:
            self.status.emit("make a macro first")
            return
        events = self.macros[name]
        at = len(events) if row < 0 else row + (1 if after else 0)
        events.insert(at, self._staged())
        self._persist()
        self._show(name)
        self.events.setCurrentRow(at)

    def _replace(self) -> None:
        """Overwrite the selected event with what the controls say."""
        name, row = self._current(), self.events.currentRow()
        if not name or row < 0:
            self.status.emit("pick an event to change")
            return
        self.macros[name][row] = self._staged()
        self._persist()
        self._show(name)
        self.events.setCurrentRow(row)

    def _remove(self) -> None:
        name, row = self._current(), self.events.currentRow()
        if name and row >= 0:
            self.macros[name].pop(row)
            self._persist()
            self._show(name)

    def _move(self, by: int) -> None:
        name, row = self._current(), self.events.currentRow()
        if not name or row < 0:
            return
        events = self.macros[name]
        new = row + by
        if 0 <= new < len(events):
            events[row], events[new] = events[new], events[row]
            self._persist()
            self._show(name)
            self.events.setCurrentRow(new)

    # -- recording, files, and the one thing that cannot be done --------
    def _record(self) -> None:
        name = self._current()
        if not name:
            self.status.emit("make a macro first")
            return
        dialog = RecordDialog(self)
        if not dialog.exec():
            self.status.emit("recording discarded")
            return
        events = dialog.events(self._policy(), self.delay_ms.value())
        if not events:
            self.status.emit("nothing was typed")
            return
        # A recording replaces the macro, as the vendor's does. Adding
        # to the end of an existing one is what the insert buttons are
        # for, and they need no keyboard grab to do it.
        self.macros[name] = events
        self._persist()
        self._show(name)
        self.status.emit(f"recorded {len(events)} events into '{name}'")

    def _export(self) -> None:
        name = self._current()
        if not name:
            self.status.emit("pick a macro to export")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export macro", f"{name}.json",
            "Macro (*.json);;All files (*)")
        if not path:
            return
        try:
            write_macro(path, name, self.macros[name])
        except OSError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        self.status.emit(f"exported '{name}'")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import macro", "", "Macro (*.json);;All files (*)")
        if not path:
            return
        try:
            name, events = read_macro(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Import", f"That is not a macro file: {exc}")
            return
        name = self._adopt(name or Path(path).stem, events)
        self.status.emit(f"imported {len(events)} events as '{name}'")

    def _send(self) -> None:
        # Unreachable while the button is disabled, and deliberately the
        # whole of the send path: there is no encoding to send.
        self.status.emit(
            "macros cannot be sent yet: the event encoding is undecoded")
