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
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtWidgets import QApplication, QDialog, QPushButton
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
        #: Every question asked, so a test can read what the person was
        #: told before they said yes.
        self.asked: list[tuple] = []

    def question(self, *args) -> str:
        self.asked.append(args)
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
    fake = _Answer(reply)
    keys_screen.QMessageBox = fake
    try:
        yield fake
    finally:
        keys_screen.QMessageBox = real


class _Panel:
    """Stands in for ``QMenu``, which is the right-click panel.

    A real menu's ``exec`` waits for a person and, under the offscreen
    platform, waits for one who will never come. This remembers the
    entries instead, which is what the panel is: three names and what
    each of them does.
    """

    def __init__(self, parent=None):
        self.entries: list[tuple] = []

    def addAction(self, text, slot):
        self.entries.append((text, slot))

    def exec(self, where) -> None:
        self.where = where

    def texts(self) -> list[str]:
        return [text for text, _ in self.entries]

    def choose(self, text: str) -> None:
        """Click one of the entries."""
        dict(self.entries)[text]()


@contextlib.contextmanager
def _panelling():
    """Open the right-click panel without anyone to close it again."""
    real = keys_screen.QMenu
    panels: list[_Panel] = []

    def build(parent=None):
        panels.append(_Panel(parent))
        return panels[-1]

    keys_screen.QMenu = build
    try:
        yield panels
    finally:
        keys_screen.QMenu = real


class _Custom:
    """Stands in for the custom-function dialog, for the same reason.

    The dialog itself is checked below against the real thing; what
    this is for is the other half -- that the screen opens it for the
    key in hand and stages exactly what it hands back.
    """

    opened: "list[_Custom]" = []
    reply = None
    answer = None

    def __init__(self, key, macros, binding=None, parent=None):
        self.key, self.macros, self.opened_on = key, macros, binding
        type(self).opened.append(self)

    def exec(self):
        return type(self).reply

    def binding(self):
        return type(self).answer


@contextlib.contextmanager
def _dialogue(reply, answer=None):
    """Answer the custom-function dialog in a person's place."""
    real = keys_screen.CustomFunctionDialog
    _Custom.opened = []
    _Custom.reply, _Custom.answer = reply, answer
    keys_screen.CustomFunctionDialog = _Custom
    try:
        yield _Custom
    finally:
        keys_screen.CustomFunctionDialog = real


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

    def centre(self, key) -> QPoint:
        """Where on the board a key is drawn, for a right-click.

        Read off the layout and the scale the page draws at rather than
        pinned, and checked against the view: a geometry that moved
        should fail here rather than quietly right-click empty space.
        """
        scale = self.page.BOARD_SCALE
        top = kb_layout.KEYS_TOP - 8
        point = QPoint(int((key.x + key.w / 2) * scale),
                       int((key.y + key.h / 2 - top) * scale))
        self.assertIs(self.page.board.key_at(QPointF(point)), key)
        return point

    def tile(self, text: str) -> QPushButton:
        """One cell of the quick-select region, by what it reads."""
        found = [b for b in self.page.findChildren(QPushButton)
                 if b.text() == text]
        self.assertEqual(len(found), 1, text)
        return found[0]

    def staged_of(self, key):
        return self.page.staged.get(kb_layout.remap_slot(key))

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

    def test_the_picked_key_is_ringed_for_the_quick_grid(self):
        self.page._pick(self.caps)
        self.assertEqual(self.page.board.chosen, {self.caps.light_index})
        self.page._pick(self.c)
        self.assertEqual(self.page.board.chosen, {self.c.light_index})

    # -- the hover panel ------------------------------------------------
    def test_the_board_asks_this_screen_what_a_key_is_doing(self):
        self.assertEqual(self.page.board.hover_text, self.page._describe)

    def test_an_untouched_key_hovers_as_its_own_function(self):
        self.assertEqual(self.page._describe(self.caps).splitlines(),
                         ["Default function: Caps",
                          "Current function: Caps"])

    def test_a_remapped_key_hovers_as_both_functions(self):
        self.stage(self.caps, 1, self.c, mods=(0xE0,))
        self.assertEqual(self.page._describe(self.caps).splitlines(),
                         ["Default function: Caps",
                          "Current function: Ctrl+C"])

    def test_a_disabled_key_hovers_as_doing_nothing(self):
        self.stage(self.caps, 2)
        self.assertIn("Current function: nothing",
                      self.page._describe(self.caps))

    def test_the_hover_panel_follows_the_layer_on_screen(self):
        self.stage(self.caps, 1, self.c)
        self.page.layer.setCurrentIndex(1)
        self.assertIn("Current function: Caps",
                      self.page._describe(self.caps))

    # -- the right-click panel ------------------------------------------
    def test_a_right_click_picks_the_key_under_it(self):
        with _panelling() as panels:
            self.page._panel(self.centre(self.c))
        self.assertIs(self.page._selected, self.c)
        self.assertEqual(len(panels), 1)

    def test_the_panel_offers_the_vendors_three_entries(self):
        with _panelling() as panels:
            self.page._panel(self.centre(self.caps))
        self.assertEqual(panels[0].texts(),
                         ["Default function", "Disable key",
                          "Custom function…"])

    def test_a_right_click_off_every_key_opens_nothing(self):
        with _panelling() as panels:
            self.page._panel(QPoint(2, 2))
        self.assertEqual(panels, [])

    def test_the_panel_disables_the_key_it_was_opened_on(self):
        with _panelling() as panels:
            self.page._panel(self.centre(self.caps))
            panels[0].choose("Disable key")
        self.assertEqual(self.staged_of(self.caps),
                         keys_screen.Binding(0, 0, keys_screen.DISABLE, ""))
        self.assertEqual(self.record(self.caps), protocol.remap_record(0))

    def test_the_panels_default_function_takes_the_key_back(self):
        self.stage(self.caps, 1, self.c)
        with _panelling() as panels:
            self.page._panel(self.centre(self.caps))
            panels[0].choose("Default function")
        self.assertEqual(self.page.staged, {})
        self.assertEqual(self.page.action.currentIndex(), 0)

    # -- the custom-function dialog, as the screen uses it ---------------
    def test_the_dialog_opens_on_the_picked_key_and_its_binding(self):
        self.stage(self.caps, 1, self.c)
        with _dialogue(QDialog.Rejected) as dialog:
            self.page._custom_function()
        self.assertIs(dialog.opened[0].key, self.caps)
        self.assertEqual(dialog.opened[0].opened_on,
                         self.staged_of(self.caps))

    def test_a_cancelled_dialog_leaves_the_key_as_it_was(self):
        self.stage(self.caps, 1, self.c)
        before = self.staged_of(self.caps)
        with _dialogue(QDialog.Rejected,
                       keys_screen.Binding(0x05, 0)):
            self.page._custom_function()
        self.assertEqual(self.staged_of(self.caps), before)

    def test_an_applied_dialog_stages_what_it_hands_back(self):
        self.page._pick(self.caps)
        wanted = keys_screen.Binding(self.c.code, 0, keys_screen.KEY_FUNCTION)
        with _dialogue(QDialog.Accepted, wanted):
            self.page._custom_function()
        self.assertEqual(self.staged_of(self.caps), wanted)
        self.assertEqual(self.record(self.caps),
                         protocol.remap_record(self.c.code))

    def test_the_fourth_action_is_the_dialog(self):
        self.page._pick(self.caps)
        self.page.action.setCurrentIndex(3)
        with _dialogue(QDialog.Rejected) as dialog:
            self.page._stage()
        self.assertEqual(len(dialog.opened), 1)

    def test_the_dialog_is_handed_this_schemes_macros(self):
        store.shared().current.macros = {"Greeting": []}
        self.page._pick(self.caps)
        with _dialogue(QDialog.Rejected) as dialog:
            self.page._custom_function()
        self.assertEqual(dialog.opened[0].macros, {"Greeting": []})

    def test_asking_for_a_custom_function_with_no_key_says_so(self):
        said = []
        self.page.status.connect(said.append)
        with _dialogue(QDialog.Accepted) as dialog:
            self.page._custom_function()
        self.assertEqual(dialog.opened, [])
        self.assertEqual(said, ["pick a key on the board first"])

    # -- the kinds the keyboard cannot be told --------------------------
    def macro_on(self, key, name: str = "Greeting") -> None:
        """Bind a key to a macro the way the dialog would."""
        self.page._pick(key)
        with _dialogue(QDialog.Accepted, keys_screen.Binding.host_only(
                key, keys_screen.MACRO, name)):
            self.page._custom_function()

    def test_a_macro_is_staged_as_the_key_mapping_to_itself(self):
        self.macro_on(self.caps)
        binding = self.staged_of(self.caps)
        self.assertEqual(binding.kind, keys_screen.MACRO)
        self.assertEqual(binding.detail, "Greeting")
        self.assertFalse(binding.carried)
        # The record that goes out changes nothing on the keyboard,
        # which is the honest thing to send for a binding it cannot be
        # told about.
        self.assertEqual(self.record(self.caps),
                         protocol.remap_record(self.caps.code))

    def test_a_macro_binding_says_it_was_not_sent(self):
        self.macro_on(self.caps)
        self.assertIn('macro "Greeting" (not sent)',
                      self.page.board.toolTip())
        self.assertIn('Current function: macro "Greeting" (not sent)',
                      self.page._describe(self.caps))

    def test_a_macro_binding_is_blue_like_any_other_change(self):
        self.macro_on(self.caps)
        self.assertEqual(self.page.board.colours[self.caps.light_index],
                         keys_screen.ACCENT)

    def test_picking_a_host_only_key_points_at_the_dialog(self):
        self.macro_on(self.caps)
        self.page._pick(self.c)
        self.page._pick(self.caps)
        self.assertEqual(self.page.action.currentIndex(), 3)

    def test_a_macro_binding_survives_a_new_page(self):
        self.macro_on(self.caps)
        self.page._save_now()
        store.use(store.Store.load(self.path))
        again = screens.KeysPage()
        self.assertEqual(again.layers[False][kb_layout.remap_slot(self.caps)],
                         self.staged_of(self.caps))

    def test_the_write_dialog_owns_up_to_what_it_cannot_send(self):
        self.macro_on(self.caps)
        self.stage(self.c, 1, self.ctrl)
        with _answering(_Answer.Yes) as asked:
            self.page._write()
        said = " ".join(str(part) for part in asked.asked[0])
        self.assertIn("2 key(s)", said)
        self.assertIn("1 of them is a macro, mouse or multimedia", said)

    def test_the_write_dialog_stays_quiet_when_everything_can_be_sent(self):
        self.stage(self.caps, 1, self.ctrl)
        with _answering(_Answer.Yes) as asked:
            self.page._write()
        said = " ".join(str(part) for part in asked.asked[0])
        self.assertNotIn("macro, mouse or multimedia", said)

    # -- the quick function select --------------------------------------
    def test_the_quick_grid_reaches_past_this_board(self):
        offered = {usage for row in keys_screen.QUICK_ROWS
                   for _, usage in row}
        # F-keys, the numpad and the editing cluster: the board has none
        # of them, and binding one of them is the point of the grid.
        self.assertTrue({0x3A, 0x45, 0x49, 0x4D, 0x62}.issubset(offered))
        self.assertTrue({k.code for k in kb_layout.KEYS}
                        - {0x9D} <= offered)

    def test_a_quick_key_binds_the_key_picked_on_the_board(self):
        self.page._pick(self.caps)
        self.page._quick_key("F5", 0x3E)
        self.assertEqual(self.record(self.caps), protocol.remap_record(0x3E))
        self.assertEqual(self.staged_of(self.caps).kind,
                         keys_screen.KEY_FUNCTION)

    def test_a_quick_cell_is_wired_to_the_key_it_reads_as(self):
        self.page._pick(self.caps)
        self.tile("F5").click()
        self.assertEqual(self.record(self.caps), protocol.remap_record(0x3E))

    def test_a_quick_icon_is_kept_but_not_sent(self):
        self.page._pick(self.caps)
        self.tile("▶").click()
        binding = self.staged_of(self.caps)
        self.assertEqual(binding.kind, keys_screen.MEDIA)
        self.assertEqual(binding.detail, "Play / pause")
        self.assertFalse(binding.carried)
        self.assertEqual(self.record(self.caps),
                         protocol.remap_record(self.caps.code))

    def test_the_quick_grid_needs_a_key_before_it_does_anything(self):
        said = []
        self.page.status.connect(said.append)
        self.page._quick_key("F5", 0x3E)
        self.page._quick_media("Mute")
        self.assertEqual(self.page.layers, {False: {}, True: {}})
        self.assertEqual(said, ["pick a key on the board first"] * 2)

    def test_a_quick_key_lands_on_the_layer_on_screen(self):
        self.page.layer.setCurrentIndex(1)
        self.page._pick(self.caps)
        self.page._quick_key("F5", 0x3E)
        self.assertEqual(self.page.layers[False], {})
        self.assertEqual(self.record(self.caps, True),
                         protocol.remap_record(0x3E))

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
