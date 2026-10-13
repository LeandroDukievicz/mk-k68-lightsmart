"""Checks the scheme screen, which is the one that owns the list.

Same shape as the other screen tests: a QApplication but no screen,
and what is checked is what the page leaves behind -- the store, the
file it wrote and the signals it emitted -- rather than how it is
drawn.

Every test runs against a store on a temporary path. Nothing here puts
the real one back afterwards, on purpose: the pages in
``tests/test_screens.py`` take the shared store too, and a save from
one of them would otherwise land in the user's own config directory.
What this file leaves installed instead is an empty store on a
temporary path -- see ``tearDownModule``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    QApplication = None

from mk_k68_lightsmart import store

if QApplication is not None:
    from mk_k68_lightsmart.screens import profiles

app = None
#: Held in a module global so it outlives every test in the file and is
#: cleaned up when the interpreter exits, not between tests.
sandbox = None


def setUpModule() -> None:
    global app, sandbox
    if QApplication is not None:
        app = QApplication.instance() or QApplication([])
    sandbox = tempfile.TemporaryDirectory()


def tearDownModule() -> None:
    # One store serves the whole process, and the pages built in
    # tests/test_screens.py take it without installing one of their
    # own -- so what is left installed here is what they will edit and
    # save. It has to be on a temporary path, to keep their saves off
    # the real config file, and it has to be empty, because they expect
    # what a first run has: one scheme, called Default.
    store.use(store.Store(path=Path(sandbox.name) / "left-behind.json"))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class SchemeScreen(unittest.TestCase):
    def setUp(self):
        self.path = Path(sandbox.name) / f"{self.id()}.json"
        store.use(store.Store(path=self.path))
        self.page = profiles.ProfilesPage()
        self.said: list[str] = []
        self.page.status.connect(self.said.append)

    def saved(self) -> store.Store:
        """The store as the next start of the app would read it."""
        return store.Store.load(self.path)

    def choosing(self, which: str, path):
        """Answer the file dialog named, with that path."""
        return unittest.mock.patch.object(
            profiles.QFileDialog, which,
            staticmethod(lambda *a, **k: (str(path), "")))

    def answering(self, yes: bool):
        """Answer the confirmation a destructive action asks for."""
        reply = (profiles.QMessageBox.Yes if yes
                 else profiles.QMessageBox.No)
        return unittest.mock.patch.object(
            profiles.QMessageBox, "question",
            staticmethod(lambda *a, **k: reply))

    def warning(self):
        """Swallow a warning box, and record that it was shown."""
        shown: list[str] = []
        return shown, unittest.mock.patch.object(
            profiles.QMessageBox, "warning",
            staticmethod(lambda _p, title, text, *a: shown.append(title)))

    def naming(self, name: str):
        return unittest.mock.patch.object(
            profiles.QInputDialog, "getText",
            staticmethod(lambda *a, **k: (name, True)))

    # -- the list ------------------------------------------------------
    def test_a_fresh_screen_shows_the_one_scheme(self):
        self.assertEqual(self.page.schemes, ["Default"])
        self.assertEqual(self.page.list.count(), 1)

    def test_a_row_says_what_the_scheme_holds(self):
        self.page.store.current.keymap[4] = [5, 0]
        self.page.store.current.lighting[9] = 0xFF0000
        self.page._refresh()
        self.assertIn("1 remapped, 1 lit, 0 macros",
                      self.page.list.item(0).text())

    def test_adding_one_writes_the_file(self):
        with self.naming("Work"):
            self.page._new()
        self.assertEqual(self.page.schemes, ["Default", "Work"])
        self.assertEqual(self.saved().names(), ["Default", "Work"])

    def test_a_collision_is_renamed_and_mentioned(self):
        with self.naming("Default"):
            self.page._new()
        self.assertEqual(self.page.schemes, ["Default", "Default 2"])
        self.assertIn("Default was taken", self.said[-1])

    def test_renaming_refuses_a_name_already_in_use(self):
        self.page.store.add("Work")
        self.page._refresh(1)
        shown, box = self.warning()
        with box, self.naming("Default"):
            self.page._rename()
        self.assertEqual(shown, ["That name is taken"])
        self.assertEqual(self.page.schemes, ["Default", "Work"])

    def test_renaming_persists(self):
        with self.naming("Gaming"):
            self.page._rename()
        self.assertEqual(self.saved().names(), ["Gaming"])

    def test_duplicating_copies_the_contents(self):
        self.page.store.current.lighting[9] = 0x00FF00
        self.page._duplicate()
        self.assertEqual(self.saved().schemes[1].lighting, {9: 0x00FF00})

    # -- deleting ------------------------------------------------------
    def test_a_scheme_with_something_in_it_is_asked_about(self):
        self.page.store.add("Work").keymap[4] = [5, 0]
        self.page._refresh(1)
        with self.answering(False):
            self.page._delete()
        self.assertEqual(self.page.schemes, ["Default", "Work"])
        with self.answering(True):
            self.page._delete()
        self.assertEqual(self.page.schemes, ["Default"])

    def test_an_empty_scheme_goes_without_a_question(self):
        self.page.store.add("Work")
        self.page._refresh(1)
        self.page._delete()
        self.assertEqual(self.saved().names(), ["Default"])

    def test_deleting_above_the_active_one_keeps_it_in_use(self):
        for name in ("Work", "Play", "Games"):
            self.page.store.add(name)
        self.page.store.active = 1               # Work
        self.page._refresh(0)                    # delete Default
        self.page._delete()
        self.assertEqual(self.page.store.current.name, "Work")
        self.assertEqual(self.saved().active, 0)

    def test_deleting_the_active_one_is_a_switch(self):
        self.page.store.add("Work")
        self.page.store.active = 1
        switched: list[object] = []
        self.page.scheme_changed.connect(switched.append)
        self.page._refresh(1)
        self.page._delete()
        self.assertEqual([s.name for s in switched], ["Default"])

    # -- switching, which is replaying ---------------------------------
    def test_using_a_scheme_persists_and_asks_for_a_replay(self):
        self.page.store.add("Work")
        self.page._refresh(1)
        switched: list[object] = []
        replays: list[bool] = []
        self.page.scheme_changed.connect(switched.append)
        self.page.replay_requested.connect(replays.append)
        self.page._use()
        self.assertEqual([s.name for s in switched], ["Work"])
        self.assertEqual(replays, [True])
        self.assertEqual(self.saved().active, 1)

    def test_the_keymap_can_be_kept_out_of_a_replay(self):
        replays: list[bool] = []
        self.page.replay_requested.connect(replays.append)
        self.page.with_keymap.setChecked(False)
        self.page._replay()
        self.assertEqual(replays, [False])

    def test_replaying_does_not_claim_a_switch(self):
        switched: list[object] = []
        self.page.scheme_changed.connect(switched.append)
        self.page._replay()
        self.assertEqual(switched, [])

    # -- import and export ---------------------------------------------
    def test_a_scheme_survives_a_round_trip_through_a_file(self):
        scheme = self.page.store.current
        scheme.keymap[57] = [0xE0, 0]
        scheme.lighting[9] = 0x123456
        scheme.macros["sign"] = [{"kind": "Key down", "value": "A"}]
        out = Path(sandbox.name) / "one.json"
        with self.choosing("getSaveFileName", out):
            self.page._export()
        with self.choosing("getOpenFileName", out):
            self.page._import()
        copy = self.page.store.schemes[-1]
        self.assertEqual(copy.keymap, {57: [0xE0, 0]})
        self.assertEqual(copy.lighting, {9: 0x123456})
        self.assertEqual(copy.macros, scheme.macros)

    def test_importing_never_overwrites_the_name_it_lands_on(self):
        out = Path(sandbox.name) / "same.json"
        with self.choosing("getSaveFileName", out):
            self.page._export()
        with self.choosing("getOpenFileName", out):
            self.page._import()
        self.assertEqual(self.page.schemes, ["Default", "Default 2"])
        self.assertIn("Default was taken", self.said[-1])

    def test_a_version_from_the_future_is_refused(self):
        out = Path(sandbox.name) / "ahead.json"
        out.write_text(json.dumps(
            {"version": store.VERSION + 1, "scheme": {"name": "X"}}))
        shown, box = self.warning()
        with box, self.choosing("getOpenFileName", out):
            self.page._import()
        self.assertEqual(shown, ["Could not read that file"])
        self.assertEqual(self.page.schemes, ["Default"])

    def test_a_file_that_is_not_a_scheme_is_refused(self):
        out = Path(sandbox.name) / "junk.json"
        out.write_text("not json at all")
        shown, box = self.warning()
        with box, self.choosing("getOpenFileName", out):
            self.page._import()
        self.assertEqual(shown, ["Could not read that file"])
        self.assertEqual(self.page.schemes, ["Default"])

    def test_the_apps_own_file_imports_as_its_first_scheme(self):
        # Not the documented export shape, but the file somebody will
        # reach for first, so it is read rather than refused.
        self.page.store.current.lighting[3] = 0xABCDEF
        self.page.store.save()
        with self.choosing("getOpenFileName", self.path):
            self.page._import()
        self.assertEqual(self.page.store.schemes[-1].lighting,
                         {3: 0xABCDEF})


if __name__ == "__main__":
    unittest.main()
