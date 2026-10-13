"""The macro screen: record and edit; the wire encoding is still open."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import QElapsedTimer, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
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
#: already saved.
KEY_DOWN = "Key down"
KEY_UP = "Key up"
DELAY = "Delay (ms)"
MOUSE = "Mouse button"
KINDS = (KEY_DOWN, KEY_UP, DELAY, MOUSE)

#: The three mouse buttons the vendor's mouse event can carry. Nothing
#: here records one: while the keyboard is grabbed the mouse is not, so
#: a recorded click would be the click that stopped the recording.
MOUSE_BUTTONS = ("Left", "Right", "Middle")

#: What recording does with the time between keystrokes -- the vendor's
#: three choices, which are the only three there are.
KEEP_DELAYS = "Keep the real delays"
NO_DELAY = "No delay"
ONE_DELAY = "One delay for all"
POLICIES = (KEEP_DELAYS, NO_DELAY, ONE_DELAY)

#: Stamped into an exported file so an unrelated JSON document is
#: refused rather than read as a macro with nothing in it.
FILE_KIND = "mk-k68-lightsmart-macro"


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


def _spin(value: int, top: int = 60000) -> QSpinBox:
    """A number box in the chrome the shared combo box wears."""
    box = QSpinBox()
    box.setRange(1, top)
    box.setValue(value)
    box.setStyleSheet(
        "QSpinBox { background:#0a2444; color:#cfe3ff;"
        " border:1px solid #0d3a6b; border-radius:3px; padding:3px; }")
    return box


class RecordDialog(QDialog):
    """Catch real keystrokes, with the real time between them.

    The keyboard is grabbed for as long as this is open. Without the
    grab, recording a Tab or a Space would walk and press this dialog's
    own buttons instead of being recorded, and recording an Esc would
    shut it. For the same reason recording stops on a button and never
    on a key: every key on the board has to be recordable, which rules
    out holding one back to mean "stop".
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Recording a macro")
        self.setModal(True)
        self.setMinimumWidth(380)
        #: ``(ms since recording started, kind, key name)``, in order.
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
            "other key — stop with the button.")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        self.tally = caption("nothing yet")
        outer.addWidget(self.tally)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(ghost_button("Discard", self.reject))
        row.addWidget(primary_button("Stop", self.accept))
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
        self._remember(KEY_DOWN, event)

    def keyReleaseEvent(self, event) -> None:
        if event.isAutoRepeat():
            return
        self._remember(KEY_UP, event)

    def _remember(self, kind: str, event) -> None:
        # Deliberately no call up to QDialog from either handler: the
        # base class closes on Esc and fires the default button on
        # Enter, which would make those two keys unrecordable.
        self.stamps.append((self._clock.elapsed(), kind, key_name(event)))
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

    The vendor's own screen is the model: a list of macros beside a
    list of events, its three delay policies, and insert-before and
    insert-after rather than a lone append.
    """

    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        #: name -> its events: the working copy of ``scheme.macros``,
        #: kept as objects here and as dicts in the file.
        self.macros: dict[str, list[Event]] = {}
        self._value_holds_keys = True

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

        self.names = _list()
        self.names.currentTextChanged.connect(self._show)
        made = QHBoxLayout()
        made.addWidget(primary_button("New", self._new))
        made.addWidget(ghost_button("Rename", self._rename))
        made.addStretch()
        kept = QHBoxLayout()
        kept.addWidget(ghost_button("Duplicate", self._duplicate))
        kept.addWidget(ghost_button("Delete", self._delete))
        kept.addStretch()
        shared = QHBoxLayout()
        shared.addWidget(ghost_button("Import…", self._import))
        shared.addWidget(ghost_button("Export…", self._export))
        shared.addStretch()
        left = _card(caption("Macros"), self.names, made, kept,
                     caption("As a file, to pass around"), shared)
        left.setFixedWidth(260)
        body.addWidget(left)

        self.events = _list()
        self.events.currentRowChanged.connect(self._pull)
        # The rows are a number, a word and a name in columns, and a
        # proportional font would let the columns wander.
        self.events.setFont(QFont("monospace"))

        self.kind = _combo(list(KINDS))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.value = _combo([k.ident for k in kb_layout.KEYS])
        self.value.setEditable(True)
        self.ms = _spin(50)
        event_row = QHBoxLayout()
        event_row.addWidget(self.kind)
        event_row.addWidget(self.value, 1)
        event_row.addWidget(self.ms)

        edit_row = QHBoxLayout()
        edit_row.addWidget(primary_button("Add event", self._add))
        edit_row.addWidget(
            ghost_button("Insert before", lambda: self._insert(False)))
        edit_row.addWidget(
            ghost_button("Insert after", lambda: self._insert(True)))
        edit_row.addWidget(ghost_button("Change", self._replace))
        edit_row.addStretch()

        order_row = QHBoxLayout()
        order_row.addWidget(ghost_button("Remove", self._remove))
        order_row.addWidget(ghost_button("Up", lambda: self._move(-1)))
        order_row.addWidget(ghost_button("Down", lambda: self._move(1)))
        order_row.addStretch()
        # On the screen and disabled, so the one missing piece has a
        # place rather than being absent without explanation.
        self.send = primary_button("Send to keyboard", self._send)
        self.send.setEnabled(False)
        self.send.setToolTip(
            "Not yet: an event on the wire is a 0x50 marker and two "
            "undecoded bytes. See docs/PROTOCOL.md §8.")
        order_row.addWidget(self.send)

        self.delay_mode = _combo(list(POLICIES))
        self.delay_ms = _spin(10)
        delay_row = QHBoxLayout()
        delay_row.addWidget(self.delay_mode, 1)
        delay_row.addWidget(self.delay_ms)
        record_row = QHBoxLayout()
        record_row.addWidget(primary_button("Record…", self._record))
        record_row.addStretch()

        body.addWidget(_card(
            caption("Events"), self.events,
            caption("Event"), event_row, edit_row, order_row,
            caption("Recording: the gaps between keystrokes"),
            delay_row, record_row,
        ), 1)
        outer.addLayout(body, 1)
        self._kind_changed()

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

    def _persist(self) -> None:
        """Put the macros back in the scheme and write the file.

        Every edit saves. A scheme is a couple of kilobytes and an edit
        is a click, so the write costs nothing against the alternative:
        losing a macro somebody typed out because the window closed
        without asking this screen for it.
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

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New macro", "Name")
        if not ok or not name:
            return
        name = self._free_name(name)
        self.macros[name] = []
        self.names.addItem(name)
        self._persist()
        self._select(name)

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
        copy = self._free_name(f"{name} copy")
        # A shallow copy is a whole one: an Event is immutable, so the
        # two macros can share events without sharing a future.
        self.macros[copy] = list(self.macros[name])
        self.names.addItem(copy)
        self._persist()
        self._select(copy)
        self.status.emit(f"duplicated as '{copy}'")

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
        keys = kind != MOUSE
        if keys != self._value_holds_keys:
            self._value_holds_keys = keys
            self.value.clear()
            self.value.addItems(
                [k.ident for k in kb_layout.KEYS] if keys
                else list(MOUSE_BUTTONS))
        self.value.setEnabled(kind != DELAY)
        self.ms.setEnabled(kind == DELAY)

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
        events = dialog.events(
            self.delay_mode.currentText(), self.delay_ms.value())
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
        name = self._free_name(name or Path(path).stem)
        self.macros[name] = events
        self.names.addItem(name)
        self._persist()
        self._select(name)
        self.status.emit(f"imported {len(events)} events as '{name}'")

    def _send(self) -> None:
        # Unreachable while the button is disabled, and deliberately the
        # whole of the send path: there is no encoding to send.
        self.status.emit(
            "macros cannot be sent yet: the event encoding is undecoded")
