"""Checks the five screens the rail used to open onto a placeholder.

Same shape as the strip editor's tests: a QApplication but no screen,
and what is checked is the state each page keeps -- what it stages,
what block that would produce, what it would send -- rather than how it
is drawn.

The split that matters here is which pages reach the keyboard. Keys and
Custom do, so their tests go as far as the bytes and the signals. The
other three keep state and nothing else, and are tested for that.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    QApplication = None

from mk_k68_lightsmart import layout as kb_layout
from mk_k68_lightsmart import protocol

if QApplication is not None:
    from mk_k68_lightsmart import screens

app = None


def setUpModule() -> None:
    global app
    if QApplication is not None:
        app = QApplication.instance() or QApplication([])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class EveryScreenBuilds(unittest.TestCase):
    """The cheapest regression there is: they still construct."""

    def test_all_five(self):
        pages = [
            screens.KeysPage(),
            screens.MacroPage(),
            screens.ProfilesPage(),
            screens.CustomPage(lambda: QColor("#ff0000")),
            screens.MusicPage(),
        ]
        for page in pages:
            self.assertTrue(page.layout().count() > 0, type(page).__name__)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Remapping(unittest.TestCase):
    def setUp(self):
        self.page = screens.KeysPage()
        self.caps = kb_layout.BY_IDENT["Caps"]
        self.ctrl = kb_layout.BY_IDENT["RightCtrl"]

    def stage(self, key, action: int, target=None) -> None:
        self.page._pick(key)
        self.page.action.setCurrentIndex(action)
        if target is not None:
            self.page.target.setCurrentIndex(
                [k.ident for k in kb_layout.KEYS].index(target.ident))
        self.page._stage()

    def test_nothing_is_staged_to_begin_with(self):
        self.assertEqual(self.page.staged, {})
        self.assertEqual(self.page.block(), protocol.remap_defaults())

    def test_staging_a_key_reaches_the_block(self):
        self.stage(self.caps, 1, self.ctrl)
        at = kb_layout.remap_slot(self.caps) * protocol.REMAP_RECORD
        block = self.page.block()
        self.assertEqual(
            tuple(block[at:at + 4]),
            protocol.remap_record(self.ctrl.code))

    def test_leaving_a_key_alone_unstages_it(self):
        self.stage(self.caps, 1, self.ctrl)
        self.stage(self.caps, 0)
        self.assertEqual(self.page.staged, {})

    def test_a_staged_key_is_marked_on_the_board(self):
        self.stage(self.caps, 1, self.ctrl)
        self.assertIn(self.caps.light_index, self.page.board.colours)

    def test_disabling_writes_a_remap_to_nothing(self):
        # The one record not read out of the driver, so it is pinned
        # here: if it turns out to be wrong, this is what changes.
        self.stage(self.caps, 2)
        at = kb_layout.remap_slot(self.caps) * protocol.REMAP_RECORD
        self.assertEqual(tuple(self.page.block()[at:at + 4]),
                         (protocol.REMAP_KEY, 0, 0, 0))

    def test_the_block_is_always_a_whole_one(self):
        self.stage(self.caps, 1, self.ctrl)
        self.assertEqual(len(self.page.block()), protocol.REMAP_BYTES)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class CustomLighting(unittest.TestCase):
    def setUp(self):
        self.page = screens.CustomPage(lambda: QColor("#102030"))

    def test_a_group_paints_every_key_in_it(self):
        self.page._filler("WASD")()
        painted = {
            kb_layout.BY_IDENT[i].light_index for i in ("W", "A", "S", "D")}
        self.assertEqual(set(self.page.painted), painted)

    def test_all_means_all(self):
        self.page._filler("All")()
        self.assertEqual(len(self.page.painted), len(kb_layout.KEYS))

    def test_clearing_leaves_the_board_blank(self):
        self.page._filler("All")()
        self.page._clear()
        self.assertEqual(self.page.painted, {})
        self.assertEqual(self.page.board.colours, {})

    def test_sending_emits_one_request_per_painted_key(self):
        sent = []
        self.page.apply_key.connect(lambda i, c: sent.append(i))
        self.page._filler("WASD")()
        self.page._send()
        self.assertEqual(len(sent), 4)

    def test_sending_nothing_says_so_instead(self):
        said = []
        self.page.status.connect(said.append)
        self.page.apply_key.connect(lambda i, c: said.append("sent!"))
        self.page._send()
        self.assertEqual(said, ["nothing painted yet"])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class MacrosAndProfiles(unittest.TestCase):
    """These two keep state and send nothing, which is the test."""

    def test_a_macro_holds_its_events_in_order(self):
        page = screens.MacroPage()
        page.macros["m"] = []
        page.names.addItem("m")
        page.names.setCurrentRow(0)
        page.kind.setCurrentIndex(0)
        page._add()
        page.kind.setCurrentIndex(2)
        page._add()
        self.assertEqual([k for k, _ in page.macros["m"]],
                         ["Key down", "Delay (ms)"])

    def test_moving_an_event_reorders_it(self):
        page = screens.MacroPage()
        page.macros["m"] = [("Key down", "A"), ("Key up", "A")]
        page.names.addItem("m")
        page.names.setCurrentRow(0)
        page._show("m")
        page.events.setCurrentRow(1)
        page._move(-1)
        self.assertEqual(page.macros["m"][0], ("Key up", "A"))

    def test_the_last_scheme_cannot_be_deleted(self):
        page = screens.ProfilesPage()
        said = []
        page.status.connect(said.append)
        page.list.setCurrentRow(0)
        page._delete()
        self.assertEqual(page.schemes, ["Default"])
        self.assertEqual(said, ["there has to be one scheme"])

    def test_duplicating_adds_one(self):
        page = screens.ProfilesPage()
        page.list.setCurrentRow(0)
        page._duplicate()
        self.assertEqual(len(page.schemes), 2)


if __name__ == "__main__":
    unittest.main()
