"""Checks the remap screen, which is the one screen that writes to NVRAM.

Same shape as the other screen tests: a QApplication but no screen, and
what is checked is the state the page keeps and the bytes it would send
-- not how any of it is drawn.

Two things here are not like the other screens. The page has two staged
maps, because the keyboard holds the base keymap and the Fn layer as
separate blocks, so most of these tests are about the two staying
apart. And it saves, so every test installs a store on a temporary path
first: the real scheme file is somebody's only copy of work done by
hand, and a test suite has no business near it.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    QApplication = None

from mk_k68_lightsmart import layout as kb_layout
from mk_k68_lightsmart import protocol
from mk_k68_lightsmart import store

if QApplication is not None:
    from mk_k68_lightsmart import screens
    from mk_k68_lightsmart.screens import keys as keys_screen

app = None
tmp: tempfile.TemporaryDirectory | None = None


def setUpModule() -> None:
    global app, tmp
    if QApplication is not None:
        app = QApplication.instance() or QApplication([])
    tmp = tempfile.TemporaryDirectory()


def tearDownModule() -> None:
    # The shared store outlives this module, and whatever builds a page
    # after it would save wherever that store points. So the one left
    # in place is a temporary one, pointed at this directory even though
    # it has just gone: a stray save recreates it and harms nothing,
    # while the real scheme file stays untouched.
    tmp.cleanup()


class _Answer:
    """Stands in for ``QMessageBox`` so a write can be tested.

    The real dialog waits for a person. This answers once, which is
    enough: what the tests are after is the bytes behind the dialog,
    and the dialog itself is two lines of Qt.
    """

    Yes = "yes"
    No = "no"

    def __init__(self, reply: str):
        self.reply = reply

    def question(self, *args) -> str:
        return self.reply


@contextlib.contextmanager
def _answering(reply: str):
    """Answer the page's confirmation dialog in a person's place.

    The screen asks before every write -- that is most of the point of
    it -- so a test that goes as far as the bytes has to say yes or no
    for itself. The name in the screen's own namespace is swapped
    rather than the Qt class, which Shiboken does not let anyone patch.
    """
    real = keys_screen.QMessageBox
    keys_screen.QMessageBox = _Answer(reply)
    try:
        yield
    finally:
        keys_screen.QMessageBox = real


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class RemapScreen(unittest.TestCase):
    def setUp(self):
        self.path = Path(tmp.name) / "schemes.json"
        self.path.unlink(missing_ok=True)
        store.use(store.Store(path=self.path))
        self.page = screens.KeysPage()
        self.caps = kb_layout.BY_IDENT["Caps"]
        self.ctrl = kb_layout.BY_IDENT["RightCtrl"]
        self.c = kb_layout.BY_IDENT["C"]

    def tearDown(self):
        store.use(store.Store(path=self.path))

    # -- helpers --------------------------------------------------------
    def stage(self, key, action: int, target=None, mods=()) -> None:
        """Do by hand what a person does with four controls and a click."""
        self.page._pick(key)
        self.page.action.setCurrentIndex(action)
        if target is not None:
            self.page.target.setCurrentIndex(kb_layout.KEYS.index(target))
        for box, usage in self.page.mods:
            box.setChecked(usage in mods)
        self.page._stage()

    def record(self, key, fn_layer: bool | None = None) -> tuple:
        """One key's four bytes out of the block the page would send."""
        at = kb_layout.remap_slot(key) * protocol.REMAP_RECORD
        block = self.page.block(fn_layer)
        return tuple(block[at:at + protocol.REMAP_RECORD])

    # -- staging --------------------------------------------------------
    def test_a_fresh_page_has_nothing_staged_on_either_layer(self):
        self.assertEqual(self.page.layers, {False: {}, True: {}})
        self.assertEqual(self.page.block(), protocol.remap_defaults())

    def test_a_modifier_combination_reaches_the_record(self):
        self.stage(self.caps, 1, self.c, mods=(0xE0, 0xE1))
        held = (protocol.MODIFIER_BITS[0xE0] | protocol.MODIFIER_BITS[0xE1])
        self.assertEqual(self.record(self.caps),
                         protocol.remap_record(self.c.code, held))
        # Pinned rather than only compared with the encoder: the mask is
        # this screen's answer, the record is the encoder's.
        self.assertEqual(self.record(self.caps)[1], 0b11)

    def test_unticked_modifiers_leave_a_plain_remap(self):
        self.stage(self.caps, 1, self.c)
        self.assertEqual(self.record(self.caps),
                         protocol.remap_record(self.c.code))

    def test_leaving_one_key_as_it_is_keeps_the_others(self):
        self.stage(self.caps, 1, self.ctrl)
        self.stage(self.c, 2)
        self.stage(self.caps, 0)
        self.assertEqual(list(self.page.staged),
                         [kb_layout.remap_slot(self.c)])

    def test_forgetting_it_unstages_the_selected_key(self):
        self.stage(self.caps, 1, self.ctrl)
        self.page._unstage()
        self.assertEqual(self.page.staged, {})

    def test_forgetting_them_all_clears_both_layers_and_sends_nothing(self):
        sent = []
        self.page.write_requested.connect(lambda b, f: sent.append(f))
        said = []
        self.page.status.connect(said.append)
        self.stage(self.caps, 1, self.ctrl)
        self.page.layer.setCurrentIndex(1)
        self.stage(self.c, 2)
        self.page._clear()
        self.assertEqual(self.page.layers, {False: {}, True: {}})
        self.assertEqual(sent, [])
        self.assertEqual(len(said), 1)

    # -- the two layers -------------------------------------------------
    def test_the_fn_layer_starts_empty_with_the_base_map_staged(self):
        self.stage(self.caps, 1, self.ctrl)
        self.page.layer.setCurrentIndex(1)
        self.assertEqual(self.page.staged, {})
        self.assertEqual(self.page.block(), protocol.remap_defaults())

    def test_a_layer_keeps_its_own_record(self):
        self.stage(self.caps, 1, self.ctrl)
        self.page.layer.setCurrentIndex(1)
        self.stage(self.caps, 1, self.c)
        self.assertEqual(self.record(self.caps, False),
                         protocol.remap_record(self.ctrl.code))
        self.assertEqual(self.record(self.caps, True),
                         protocol.remap_record(self.c.code))

    def test_a_write_sends_the_layer_on_screen(self):
        sent = []
        self.page.write_requested.connect(
            lambda block, fn: sent.append((bytes(block), fn)))
        self.page.layer.setCurrentIndex(1)
        self.stage(self.caps, 1, self.ctrl)
        with _answering(_Answer.Yes):
            self.page._write()
        self.assertEqual(sent, [(self.page.block(True), True)])

    def test_a_refused_dialog_sends_nothing(self):
        sent = []
        self.page.write_requested.connect(lambda b, f: sent.append(f))
        self.stage(self.caps, 1, self.ctrl)
        with _answering(_Answer.No):
            self.page._write()
        self.assertEqual(sent, [])

    def test_a_write_with_nothing_staged_says_so_instead(self):
        said = []
        self.page.status.connect(said.append)
        self.page.write_requested.connect(lambda b, f: said.append("sent!"))
        self.page._write()
        self.assertEqual(said, ["nothing staged"])

    def test_restoring_writes_the_default_block_to_both_layers(self):
        sent = []
        self.page.write_requested.connect(
            lambda block, fn: sent.append((bytes(block), fn)))
        self.stage(self.caps, 1, self.ctrl)
        self.page.layer.setCurrentIndex(1)
        self.stage(self.c, 2)
        with _answering(_Answer.Yes):
            self.page._restore()
        defaults = protocol.remap_defaults()
        self.assertEqual(sent, [(defaults, False), (defaults, True)])
        self.assertEqual(self.page.layers, {False: {}, True: {}})

    # -- what the board shows -------------------------------------------
    def test_the_board_marks_a_remap_and_a_disabled_key_apart(self):
        self.stage(self.caps, 1, self.ctrl)
        self.stage(self.c, 2)
        self.assertEqual(self.page.board.colours[self.caps.light_index],
                         keys_screen.ACCENT)
        self.assertEqual(self.page.board.colours[self.c.light_index],
                         keys_screen.DISABLED)

    def test_the_board_only_marks_the_layer_on_screen(self):
        self.stage(self.caps, 1, self.ctrl)
        self.page.layer.setCurrentIndex(1)
        self.assertEqual(self.page.board.colours, {})

    def test_the_tooltip_says_what_a_key_became(self):
        self.stage(self.caps, 1, self.c, mods=(0xE0,))
        self.assertIn("Caps", self.page.board.toolTip())
        self.assertIn("→ Ctrl+C", self.page.board.toolTip())

    def test_picking_a_staged_key_shows_what_it_is_staged_as(self):
        self.stage(self.caps, 1, self.c, mods=(0xE1,))
        self.page._pick(self.ctrl)
        self.page._pick(self.caps)
        self.assertEqual(self.page.action.currentIndex(), 1)
        self.assertEqual(
            kb_layout.KEYS[self.page.target.currentIndex()].ident, "C")
        self.assertEqual(self.page._mask(), protocol.MODIFIER_BITS[0xE1])

    # -- the scheme file ------------------------------------------------
    def test_an_edit_asks_for_a_save(self):
        self.assertFalse(self.page._save.isActive())
        self.stage(self.caps, 1, self.ctrl)
        self.assertTrue(self.page._save.isActive())

    def test_both_staged_maps_survive_a_new_page(self):
        self.stage(self.caps, 1, self.c, mods=(0xE0,))
        self.page.layer.setCurrentIndex(1)
        self.stage(self.c, 2)
        before = {False: dict(self.page.layers[False]),
                  True: dict(self.page.layers[True])}
        self.page._save_now()

        store.use(store.Store.load(self.path))
        again = screens.KeysPage()
        self.assertEqual(again.layers, before)
        self.assertEqual(again.block(True), self.page.block(True))

    def test_the_fn_layer_is_namespaced_in_the_scheme(self):
        self.stage(self.caps, 1, self.ctrl)
        self.page.layer.setCurrentIndex(1)
        self.stage(self.caps, 1, self.c)
        slot = kb_layout.remap_slot(self.caps)
        self.assertEqual(set(store.shared().current.keymap),
                         {slot, slot + keys_screen.FN_SLOT_BASE})

    def test_a_scheme_with_no_keymap_is_simply_empty(self):
        store.use(store.Store.load(Path(tmp.name) / "nothing-here.json"))
        self.assertEqual(screens.KeysPage().layers, {False: {}, True: {}})


if __name__ == "__main__":
    unittest.main()
