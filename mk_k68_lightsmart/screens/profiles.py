"""The configuration-scheme screen: a bundle the host replays."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .. import store as scheme_store
from ..gui import caption, ghost_button, primary_button
from .common import _card, _heading, _list, _note


# ----------------------------------------------------------------------
# 3. User -- configuration schemes
# ----------------------------------------------------------------------
class ProfilesPage(QWidget):
    """The vendor calls these configuration schemes, and so does this.

    Its own screen offers a list with new, rename, delete, duplicate,
    import and export, and that is what is here. What is not here, and
    cannot be, is a switch command: the driver has none. Reading its
    idle-tick dispatcher, the cases that change scheme send the
    keyboard nothing at all and re-send the keymap and the lighting
    instead (docs/PROTOCOL.md §10). So switching *is* replaying, which
    is why this screen asks the window to send and sends nothing
    itself.

    The schemes live in ``store``, the one the whole app shares,
    because their contents are what the other screens edit. This screen
    owns the list, the names and which one is in use; it owns none of
    the three things inside a scheme.
    """

    status = Signal(str)
    #: The scheme that has just become the active one. Every screen
    #: draws some part of a scheme, and they share one store object, so
    #: a switch changes what they are all looking at -- this is how
    #: they are told to look again.
    scheme_changed = Signal(object)
    #: Ask the window to put the active scheme back on the keyboard.
    #: The flag is whether the keymap goes with it, because the two
    #: halves differ in what being wrong costs: the lighting is gone at
    #: the next effect, a keymap outlives the power cycle.
    replay_requested = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = scheme_store.shared()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("User profiles"))
        outer.addWidget(_note(
            "No profile command exists. The vendor's own dispatcher "
            "sends the keyboard nothing when a scheme changes — it "
            "re-sends the contents instead, and so does this screen: "
            "using a scheme replays its keymap and its painted layout "
            "from the host. Nothing of a scheme is kept on the "
            "keyboard, so a scheme is this file, and deleting one here "
            "is the only copy gone."
        ))

        body = QHBoxLayout()
        body.setSpacing(14)

        self.list = _list()
        self.list.currentRowChanged.connect(self._selected)
        # Double-click as a second way in: it is how the vendor's list
        # behaves, and it is the one action worth not walking to a
        # button for.
        self.list.itemDoubleClicked.connect(lambda _item: self._use())

        edit_row = QHBoxLayout()
        edit_row.addWidget(primary_button("New", self._new))
        edit_row.addWidget(ghost_button("Rename", self._rename))
        edit_row.addWidget(ghost_button("Delete", self._delete))
        edit_row.addWidget(ghost_button("Duplicate", self._duplicate))
        edit_row.addStretch()

        file_row = QHBoxLayout()
        file_row.addWidget(ghost_button("Import…", self._import))
        file_row.addWidget(ghost_button("Export…", self._export))
        file_row.addStretch()

        left = _card(caption("Configuration schemes"), self.list,
                     edit_row, file_row)
        left.setFixedWidth(430)
        body.addWidget(left)

        self.chosen = QLabel()
        self.chosen.setStyleSheet("color:#cfe3ff; font-size:12px;")
        self.contents = QLabel()
        self.contents.setWordWrap(True)
        self.contents.setStyleSheet("color:#9fb3d0; font-size:11px;")

        self.with_keymap = QCheckBox("Write the keymap too")
        self.with_keymap.setStyleSheet("color:#9fb3d0; font-size:11px;")
        self.with_keymap.setChecked(True)
        self.with_keymap.setToolTip(
            "A keymap is written to the keyboard and outlives a power "
            "cycle; the lighting is gone at the next effect.")

        use_row = QHBoxLayout()
        use_row.addWidget(primary_button("Use this scheme", self._use))
        use_row.addWidget(
            ghost_button("Replay the active one", self._replay))
        use_row.addStretch()

        right = _card(
            caption("Selected"), self.chosen,
            caption("What it holds"), self.contents,
            caption("When a scheme is replayed"), self.with_keymap,
            use_row,
        )
        right.setFixedWidth(330)
        body.addWidget(right)
        body.addStretch()
        outer.addLayout(body, 1)
        outer.addStretch()

        self._refresh(self.store.active)

    # -- the list -------------------------------------------------------
    @property
    def schemes(self) -> list[str]:
        """The names, in the order the list draws them.

        The bundles themselves belong to the store and are shared; what
        this screen has of its own is the view of them.
        """
        return self.store.names()

    def showEvent(self, event) -> None:
        # The counts are the reason to open this screen, and another
        # screen may have remapped or painted something since it was
        # last looked at. Cheap enough to re-read every time.
        super().showEvent(event)
        self._refresh()

    def _refresh(self, select: int | None = None) -> None:
        """Redraw the list, keeping or moving the selection.

        Rebuilt whole rather than patched: the rows carry counts that
        any other screen can have changed, and a row-by-row update
        would be a cache to keep honest for no gain on a list of five.
        """
        row = self.list.currentRow() if select is None else select
        last = len(self.store.schemes) - 1
        # The rebuild moves the current row through 0 and -1 on its
        # way, and each of those would redraw the side panel from a
        # selection nobody made.
        self.list.blockSignals(True)
        self.list.clear()
        for index, scheme in enumerate(self.store.schemes):
            mark = "●" if index == self.store.active else "○"
            self.list.addItem(
                f"{mark}  {scheme.name}   —   {self._summary(scheme)}")
        self.list.setCurrentRow(max(0, min(row, last)))
        self.list.blockSignals(False)
        self._selected(self.list.currentRow())

    @staticmethod
    def _summary(scheme: scheme_store.Scheme) -> str:
        """The three things a scheme can hold, counted.

        Names alone tell nobody which scheme is which, least of all a
        week later, and these three numbers are the whole of what a
        scheme is.
        """
        return (f"{len(scheme.keymap)} remapped, "
                f"{len(scheme.lighting)} lit, "
                f"{len(scheme.macros)} macros")

    def _selected(self, row: int) -> None:
        if not 0 <= row < len(self.store.schemes):
            self.chosen.setText("No scheme selected")
            self.contents.setText("")
            return
        scheme = self.store.schemes[row]
        in_use = "   (in use)" if row == self.store.active else ""
        self.chosen.setText(f"{scheme.name}{in_use}")
        self.contents.setText(
            f"{len(scheme.keymap)} keys remapped\n"
            f"{len(scheme.lighting)} keys painted\n"
            f"{len(scheme.macros)} macros, which are kept here and not "
            f"sent — the event encoding is still open"
        )

    # -- new, rename, delete, duplicate ---------------------------------
    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New scheme", "Name")
        name = name.strip() if ok else ""
        if not name:
            return
        scheme = self.store.add(name)
        self._persist()
        self._refresh(len(self.store.schemes) - 1)
        self._announce("added", scheme.name, name)

    def _rename(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        scheme = self.store.schemes[row]
        name, ok = QInputDialog.getText(
            self, "Rename", "Name", text=scheme.name)
        name = name.strip() if ok else ""
        if not name or name == scheme.name:
            return
        if name in [s.name for i, s in enumerate(self.store.schemes)
                    if i != row]:
            # Refused rather than renamed round the collision, which is
            # what adding does: somebody typed this name on purpose, so
            # quietly storing a different one answers a question they
            # did not ask.
            QMessageBox.warning(
                self, "That name is taken",
                f"There is already a scheme called {name}.")
            return
        scheme.name = name
        self._persist()
        self._refresh(row)
        self.status.emit(f"renamed to {name}")

    def _delete(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        if len(self.store.schemes) <= 1:
            # The store refuses this as well; refusing it here keeps
            # the answer in the status bar instead of a dialog nobody
            # needs to dismiss.
            self.status.emit("there has to be one scheme")
            return
        scheme = self.store.schemes[row]
        if self._holds_anything(scheme) and not self._confirm(
                "Delete this scheme?",
                f"{scheme.name} holds {self._summary(scheme)}.\n\n"
                f"The keyboard keeps no copy of a scheme, so this is "
                f"the only one."):
            return
        gone = scheme.name
        was = self.store.current
        self.store.remove(row)
        switched = self._follow(was)
        self._persist()
        self._refresh(row)
        if switched:
            self.status.emit(
                f"deleted {gone} — now using {self.store.current.name}")
            self._switched()
        else:
            self.status.emit(f"deleted {gone}")

    def _duplicate(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        copy = self.store.duplicate(row)
        self._persist()
        self._refresh(len(self.store.schemes) - 1)
        self.status.emit(f"copied to {copy.name}")

    def _follow(self, was: scheme_store.Scheme) -> bool:
        """Keep using the scheme that was in use, or say it is gone.

        ``Store.remove`` clamps the active index, which is right when
        the last row goes and wrong for every row above the active one:
        the index would then land on a neighbour and the keyboard would
        be carrying a scheme nobody chose. Identity, not equality --
        two schemes can hold exactly the same thing and still be two
        schemes.
        """
        for index, scheme in enumerate(self.store.schemes):
            if scheme is was:
                self.store.active = index
                return False
        return True

    # -- switching, which means replaying -------------------------------
    def _use(self) -> None:
        """Make the selected scheme the active one and put it on.

        Re-using the scheme already in use is not refused: sending it
        again is the whole of what switching does, and the keyboard may
        have been unplugged, re-lit or reset since.
        """
        row = self.list.currentRow()
        if row < 0:
            return
        self.store.active = row
        self._persist()
        self._refresh(row)
        self.status.emit(f"using {self.store.current.name}")
        self._switched()

    def _replay(self) -> None:
        """Send the active scheme again without changing which it is.

        Worth its own button because the keyboard forgets: any effect
        started on another screen overwrites the painted layout, so a
        scheme is only ever as applied as the last thing sent.
        """
        self.status.emit(f"replaying {self.store.current.name}")
        self.replay_requested.emit(self.with_keymap.isChecked())

    def _switched(self) -> None:
        """Tell the rest of the app the active scheme moved.

        Two signals because two different things have to happen, and
        only one of them touches the keyboard: the screens re-read the
        scheme they draw, and the window replays it.
        """
        self.scheme_changed.emit(self.store.current)
        self.replay_requested.emit(self.with_keymap.isChecked())

    # -- one scheme as a file -------------------------------------------
    def _export(self) -> None:
        """Write the selected scheme out on its own.

        One scheme rather than the store's file: the point is to hand
        somebody a layout, not this machine's whole configuration and
        which of it was in use.
        """
        row = self.list.currentRow()
        if row < 0:
            return
        scheme = self.store.schemes[row]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export a scheme", f"{scheme.name}.json",
            "Scheme files (*.json);;All files (*)")
        if not path:
            return
        payload = {"version": scheme_store.VERSION,
                   "scheme": asdict(scheme)}
        try:
            Path(path).write_text(
                json.dumps(payload, indent=1, sort_keys=True))
        except OSError as exc:
            QMessageBox.warning(
                self, "Could not write that file", str(exc))
            return
        self.status.emit(f"exported {scheme.name} to {path}")

    def _import(self) -> None:
        """Read a scheme out of a file, as a scheme of its own.

        It is always added, never merged into the one that shares its
        name: an import that replaced a scheme would destroy somebody's
        work with nothing to undo it with. The store's own collision
        rule picks the free name, and the status line says which,
        because a rename nobody is told about is the same surprise in
        slower motion.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Import a scheme", "",
            "Scheme files (*.json);;All files (*)")
        if not path:
            return
        try:
            incoming = self._read_scheme(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Could not read that file", str(exc))
            return
        scheme = self.store.add(incoming.name)
        scheme.keymap = incoming.keymap
        scheme.lighting = incoming.lighting
        scheme.macros = incoming.macros
        self._persist()
        self._refresh(len(self.store.schemes) - 1)
        self._announce("imported", scheme.name, incoming.name)

    @staticmethod
    def _read_scheme(path: Path) -> scheme_store.Scheme:
        """Parse an exported scheme, or say why it is not one.

        Every field is coerced rather than trusted: this file was
        written somewhere else, and JSON has no integer keys, so the
        two maps come back keyed by strings whatever wrote them. A
        version this code does not know is refused rather than guessed
        at, which is what the store does with its own file.
        """
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError("That file does not hold a scheme.")
        if raw.get("version") != scheme_store.VERSION:
            raise ValueError(
                f"That file says version {raw.get('version')}, and "
                f"this app reads version {scheme_store.VERSION}.")
        body = raw.get("scheme")
        if body is None:
            # Somebody handed over the app's own schemes.json, meaning
            # the scheme in it. Taking the first is kinder than a
            # lecture about the difference between the two files.
            schemes = raw.get("schemes") or []
            body = schemes[0] if schemes else None
        if not isinstance(body, dict):
            raise ValueError("That file holds no scheme.")
        try:
            return scheme_store.Scheme(
                name=str(body.get("name") or "Imported"),
                keymap={int(k): [int(n) for n in v] for k, v
                        in (body.get("keymap") or {}).items()},
                lighting={int(k): int(v) for k, v
                          in (body.get("lighting") or {}).items()},
                macros={str(k): list(v) for k, v
                        in (body.get("macros") or {}).items()},
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"That scheme is malformed: {exc}") from exc

    # -- the small shared bits ------------------------------------------
    @staticmethod
    def _holds_anything(scheme: scheme_store.Scheme) -> bool:
        return bool(scheme.keymap or scheme.lighting or scheme.macros)

    def _confirm(self, title: str, question: str) -> bool:
        """Ask before something there is no way back from.

        A method of its own rather than the call written inline, so a
        test can answer it: an unanswered modal in a test run is a
        hang, which is worse to read than a failure.
        """
        return QMessageBox.question(
            self, title, question,
            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

    def _announce(self, verb: str, got: str, asked: str) -> None:
        """Say what happened, and say more when the name moved.

        The store renames round a collision instead of refusing one,
        which is the right behaviour for adding and the wrong thing to
        do without mentioning it.
        """
        if got == asked:
            self.status.emit(f"{verb} {got}")
        else:
            self.status.emit(f"{verb} {got} — {asked} was taken")

    def _persist(self) -> None:
        """Write the file now, and admit it in the status bar if it
        will not be written.

        Every edit here saves at once: the file is a couple of
        kilobytes, and the alternative is an unsaved-changes dialog
        over work nobody thinks of as a document. A failed save leaves
        the change on screen, so the message matters -- it is the only
        warning that the next start will not have it.
        """
        try:
            self.store.save()
        except OSError as exc:
            self.status.emit(f"could not save the schemes: {exc}")
