"""Checks the user-light screen: picking keys, picking a colour, and
the scheme list it keeps on the side.

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
import math
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QShowEvent
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    QApplication = None

from mk_k68_lightsmart import layout as kb_layout
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
class UserLightScreen(unittest.TestCase):
    """What every test here needs: a page over a store of its own."""

    def setUp(self):
        self.path = Path(sandbox.name) / f"{self.id()}.json"
        store.use(store.Store(path=self.path))
        self.page = profiles.ProfilesPage()
        self.said: list[str] = []
        self.sent: list[tuple[int, str]] = []
        self.page.status.connect(self.said.append)
        self.page.apply_key.connect(
            lambda index, colour: self.sent.append((index, colour.name())))

    def saved(self) -> store.Store:
        """The store as the next start of the app would read it."""
        return store.Store.load(self.path)

    def light(self, ident: str) -> int:
        return kb_layout.BY_IDENT[ident].light_index

    def click(self, *idents: str) -> None:
        """Click keys on the board, by the name on the keycap."""
        for ident in idents:
            self.page._pick(kb_layout.BY_IDENT[ident])

    def answering(self, yes: bool):
        """Answer the confirmation a destructive action asks for."""
        reply = (profiles.QMessageBox.Yes if yes
                 else profiles.QMessageBox.No)
        return unittest.mock.patch.object(
            profiles.QMessageBox, "question",
            staticmethod(lambda *a, **k: reply))


class PickingKeys(UserLightScreen):
    """The board, the three group boxes, and the ring they draw."""

    def test_a_click_picks_a_key_and_a_second_lets_it_go(self):
        self.click("W")
        self.assertEqual(self.page.chosen, {self.light("W")})
        self.click("W")
        self.assertEqual(self.page.chosen, set())

    def test_the_board_is_handed_the_very_set_that_is_picked(self):
        # The view draws the ring from this set, so it has to be the
        # one the page edits and not a copy of it.
        self.click("A")
        self.assertIs(self.page.board.chosen, self.page.chosen)
        self.assertIn(self.light("A"), self.page.board.chosen)

    def test_many_keys_stay_picked_at_once(self):
        self.click("A", "S", "D")
        self.assertEqual(len(self.page.chosen), 3)

    def test_the_count_says_how_many_are_picked(self):
        self.assertEqual(self.page.picked.text(), "none picked")
        self.click("A", "S")
        self.assertEqual(self.page.picked.text(), "2 picked")

    def test_select_all_takes_the_whole_board(self):
        box, _ = self.page.groups[0]
        box.setChecked(True)
        self.assertEqual(len(self.page.chosen), len(kb_layout.KEYS))
        box.setChecked(False)
        self.assertEqual(self.page.chosen, set())

    def test_numbers_takes_the_number_row(self):
        box, _ = self.page.groups[1]
        box.setChecked(True)
        self.assertEqual(
            self.page.chosen,
            {self.light(str(n)) for n in range(10)})

    def test_wasd_takes_the_four(self):
        box, _ = self.page.groups[2]
        box.setChecked(True)
        self.assertEqual(
            self.page.chosen,
            {self.light(n) for n in ("W", "A", "S", "D")})

    def test_a_group_box_ticks_itself_once_its_group_is_picked(self):
        # The boxes are a shortcut, not the state: picking the same
        # keys by hand has to leave them saying the same thing.
        self.click("W", "A", "S", "D")
        self.assertTrue(self.page.groups[2][0].isChecked())
        self.click("W")
        self.assertFalse(self.page.groups[2][0].isChecked())

    def test_a_group_leaves_what_was_picked_outside_it_alone(self):
        self.click("Esc")
        self.page.groups[2][0].setChecked(True)
        self.assertIn(self.light("Esc"), self.page.chosen)
        self.page.groups[2][0].setChecked(False)
        self.assertEqual(self.page.chosen, {self.light("Esc")})

    def test_a_key_says_what_it_is_carrying(self):
        self.page.painted[self.light("W")] = QColor("#ff0000")
        self.click("W")
        said = self.page._hover_text(kb_layout.BY_IDENT["W"])
        self.assertIn("#ff0000", said)
        self.assertIn("picked", said)
        self.assertIn("unpainted",
                      self.page._hover_text(kb_layout.BY_IDENT["Q"]))


class ChoosingAColour(UserLightScreen):
    """The wheel, the number boxes and the vendor's fourteen presets."""

    def ring(self, turn: float, reach: float = 0.5) -> QPointF:
        """A point on the wheel, as a turn round it and a reach out
        from the hole.

        Both ends are nudged half a pixel inwards: the wheel ignores
        anything off the ring, and the rounding in a sine is enough to
        put a point meant for the very edge on the wrong side of that.
        """
        wheel = self.page.wheel
        outer = wheel.width() / 2
        inner = outer * wheel.HOLE
        radius = min(max(inner + reach * (outer - inner), inner + 0.5),
                     outer - 0.5)
        angle = turn * 2 * math.pi
        return QPointF(outer + radius * math.cos(angle),
                       outer - radius * math.sin(angle))

    def test_the_wheel_hands_back_the_hue_under_the_point(self):
        picked: list[QColor] = []
        self.page.wheel.picked.connect(picked.append)
        self.page.wheel._take(self.ring(0.0, reach=0.0))
        self.assertEqual(picked[-1].hue(), 0)
        self.page.wheel._take(self.ring(1 / 3, reach=0.0))
        self.assertEqual(picked[-1].hue(), 120)

    def test_the_rim_is_paler_than_the_hole(self):
        wheel = self.page.wheel
        wheel._take(self.ring(0.0, reach=0.0))
        inner = wheel.colour().saturationF()
        wheel._take(self.ring(0.0, reach=1.0))
        self.assertLess(wheel.colour().saturationF(), inner)
        self.assertAlmostEqual(
            wheel.colour().saturationF(), wheel.RIM_SATURATION, delta=0.02)

    def test_the_hole_is_not_a_colour(self):
        picked: list[QColor] = []
        self.page.wheel.picked.connect(picked.append)
        self.page.wheel._take(QPointF(self.page.wheel.width() / 2,
                                      self.page.wheel.height() / 2))
        self.assertEqual(picked, [])

    def test_a_colour_off_the_wheel_fills_the_number_boxes(self):
        self.page.wheel._take(self.ring(0.0, reach=0.0))
        colour = self.page._colour
        self.assertEqual(colour.hue(), 0)
        self.assertEqual([b.value() for b in self.page.channels],
                         [colour.red(), colour.green(), colour.blue()])

    def test_typing_a_number_is_the_colour_the_apply_uses(self):
        red, green, blue = self.page.channels
        red.setValue(0)
        green.setValue(128)
        blue.setValue(64)
        self.assertEqual(self.page._colour.name(), "#008040")
        # ...and the wheel follows it rather than arguing with it.
        self.assertEqual(self.page.wheel.colour().hue(),
                         QColor("#008040").hue())

    def test_a_preset_is_one_of_the_vendors_fourteen(self):
        self.assertEqual(len(self.page.PRESETS), 14)
        self.page._use_colour(QColor(self.page.PRESETS[5]))
        self.assertEqual(self.page._colour.name(), "#00fcff")
        self.assertEqual([b.value() for b in self.page.channels],
                         [0, 252, 255])

    def test_the_dialog_is_how_a_colour_the_ring_cannot_reach_arrives(self):
        # The ring is full value by construction, so a dim red has to
        # come from somewhere else.
        with unittest.mock.patch.object(
                profiles.QColorDialog, "getColor",
                staticmethod(lambda *a, **k: QColor("#400000"))):
            self.page._pick_colour()
        self.assertEqual(self.page._colour.name(), "#400000")

    def test_a_cancelled_dialog_changes_nothing(self):
        before = self.page._colour.name()
        with unittest.mock.patch.object(
                profiles.QColorDialog, "getColor",
                staticmethod(lambda *a, **k: QColor())):
            self.page._pick_colour()
        self.assertEqual(self.page._colour.name(), before)


class Applying(UserLightScreen):
    """One transaction per key, and the scheme that remembers them."""

    def test_apply_sends_one_command_for_every_picked_key(self):
        self.page._use_colour(QColor("#00ff00"))
        self.click("W", "A")
        self.page._apply()
        self.assertEqual(
            sorted(self.sent),
            sorted([(self.light("W"), "#00ff00"),
                    (self.light("A"), "#00ff00")]))

    def test_apply_puts_the_colours_in_the_scheme_and_the_file(self):
        self.page._use_colour(QColor("#123456"))
        self.click("Esc")
        self.page._apply()
        self.assertEqual(self.page.store.current.lighting,
                         {self.light("Esc"): 0x123456})
        self.assertEqual(self.saved().current.lighting,
                         {self.light("Esc"): 0x123456})

    def test_apply_leaves_the_keys_picked(self):
        # The vendor's own screen does: one selection, several colours
        # tried on it, is how a layout gets chosen.
        self.click("W")
        self.page._apply()
        self.assertEqual(self.page.chosen, {self.light("W")})

    def test_apply_with_nothing_picked_sends_nothing(self):
        self.page._apply()
        self.assertEqual(self.sent, [])
        self.assertIn("pick a key", self.said[-1])

    def test_a_second_colour_replaces_the_first_on_a_key(self):
        self.click("W")
        self.page._use_colour(QColor("#ff0000"))
        self.page._apply()
        self.page._use_colour(QColor("#0000ff"))
        self.page._apply()
        self.assertEqual(self.page.store.current.lighting,
                         {self.light("W"): 0x0000FF})

    def test_the_row_counts_the_keys_that_are_lit(self):
        self.click("W", "A")
        self.page._apply()
        self.assertIn("2 lit", self.page.list.item(0).text())

    def test_a_group_and_an_apply_paints_the_group(self):
        self.page.groups[1][0].setChecked(True)       # Numbers
        self.page._apply()
        self.assertEqual(len(self.sent), 10)
        self.assertEqual(len(self.page.store.current.lighting), 10)


class Resetting(UserLightScreen):
    """The way back, which has to reach the keyboard as well."""

    def painted(self, *idents: str) -> None:
        self.click(*idents)
        self.page._apply()
        self.sent.clear()
        self.said.clear()

    def test_reset_blacks_out_every_painted_key(self):
        self.painted("W", "A")
        with self.answering(True):
            self.page._reset()
        self.assertEqual(
            sorted(self.sent),
            sorted([(self.light("W"), "#000000"),
                    (self.light("A"), "#000000")]))

    def test_reset_empties_the_scheme_and_the_file(self):
        self.painted("W")
        with self.answering(True):
            self.page._reset()
        self.assertEqual(self.page.store.current.lighting, {})
        self.assertEqual(self.saved().current.lighting, {})

    def test_a_refused_reset_changes_nothing(self):
        self.painted("W")
        with self.answering(False):
            self.page._reset()
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.page.store.current.lighting), 1)

    def test_reset_with_nothing_painted_asks_nothing_and_sends_nothing(self):
        # No question either: there is nothing to lose, and a modal
        # over an empty board is a dialog nobody needs to dismiss.
        self.page._reset()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.said[-1], "nothing painted")

    def test_reset_lets_every_key_go(self):
        self.painted("W", "A")
        with self.answering(True):
            self.page._reset()
        self.assertEqual(self.page.chosen, set())


class TheSchemeList(UserLightScreen):
    """The list the vendor keeps on this screen, which still works."""

    def choosing(self, which: str, path):
        """Answer the file dialog named, with that path."""
        return unittest.mock.patch.object(
            profiles.QFileDialog, which,
            staticmethod(lambda *a, **k: (str(path), "")))

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

    # -- the layout follows the scheme ---------------------------------
    def test_switching_scheme_redraws_the_board_from_the_new_one(self):
        self.page.store.add("Work").lighting[self.light("Q")] = 0xABCDEF
        self.page._refresh(1)
        self.page._use()
        self.assertEqual(self.page.painted[self.light("Q")],
                         QColor("#abcdef"))
        self.assertIs(self.page.board.colours, self.page.painted)

    def test_switching_scheme_lets_the_picked_keys_go(self):
        # A ring round a key of the scheme being left says nothing
        # about the one being arrived at.
        self.page.store.add("Work")
        self.click("W")
        self.page._refresh(1)
        self.page._use()
        self.assertEqual(self.page.chosen, set())

    def test_coming_back_to_the_screen_re_reads_the_layout(self):
        # The custom screen paints the very same field, so the board
        # has to be re-read rather than remembered.
        self.page.store.current.lighting[self.light("Z")] = 0x010203
        self.page.showEvent(QShowEvent())
        self.assertEqual(self.page.painted[self.light("Z")],
                         QColor("#010203"))

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


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class WhatTheWindowExpects(unittest.TestCase):
    """The name and the signals ``gui`` connects, which the rename of
    this screen must not have moved."""

    def test_the_old_name_still_points_at_the_screen(self):
        self.assertIs(profiles.ProfilesPage, profiles.UserLightPage)

    def test_it_still_takes_no_arguments(self):
        store.use(store.Store(path=Path(sandbox.name) / "args.json"))
        self.assertTrue(profiles.ProfilesPage().layout().count() > 0)

    def test_every_signal_the_window_connects_is_there(self):
        store.use(store.Store(path=Path(sandbox.name) / "signals.json"))
        page = profiles.ProfilesPage()
        for name in ("status", "scheme_changed", "replay_requested",
                     "apply_key"):
            getattr(page, name).connect(lambda *_: None)

    def test_a_key_goes_out_the_way_the_other_painting_screens_send_one(self):
        # The window wires this to the same handler as Custom and
        # Music, so it has to carry the same two things.
        store.use(store.Store(path=Path(sandbox.name) / "shape.json"))
        page = profiles.ProfilesPage()
        got: list[tuple] = []
        page.apply_key.connect(lambda index, colour: got.append(
            (index, colour.name())))
        page.apply_key.emit(9, QColor("#ff0000"))
        self.assertEqual(got, [(9, "#ff0000")])


if __name__ == "__main__":
    unittest.main()
