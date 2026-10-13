"""Checks the custom-lighting screen: what it paints, keeps and sends.

Same shape as ``test_screens.py``: a QApplication but no screen, and
what is checked is the state the page keeps and the signals it emits
rather than how any of it is drawn.

Two things are deliberate here. The store is a temporary one in every
test, because this screen saves a painted layout and the real config
file is nobody's fixture. And the strokes are sent as real mouse events
through the board rather than by calling the handlers, because the
whole point of the event filter is what the view would otherwise do
with those events.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QColor, QMouseEvent
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    QApplication = None

from mk_k68_lightsmart import layout as kb_layout
from mk_k68_lightsmart import store

if QApplication is not None:
    from mk_k68_lightsmart import screens

app = None

PAINT = "#102030"


def setUpModule() -> None:
    global app
    if QApplication is not None:
        app = QApplication.instance() or QApplication([])


class Fixture(unittest.TestCase):
    """A page over a store that lives in a temporary directory."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "schemes.json"
        # Read the installed store rather than calling shared(), which
        # would load the real file just to find out what was there.
        self._was = store._shared
        store.use(store.Store(path=self.path))
        self.page = screens.CustomPage(lambda: QColor(PAINT))
        self.said: list[str] = []
        self.page.status.connect(self.said.append)

    def tearDown(self):
        # None is what "nothing loaded yet" looks like, so putting it
        # back leaves the next test to load whatever it wants.
        store.use(self._was)
        self._dir.cleanup()

    # -- helpers --------------------------------------------------------
    def key(self, ident: str) -> kb_layout.Key:
        return kb_layout.BY_IDENT[ident]

    def centre(self, ident: str):
        # The view holds the only mapping from a key to where it was
        # drawn; working the scale out again here would just be testing
        # this file's arithmetic against the view's.
        return self.page.board._rects[self.key(ident).light_index].center()

    def press(self, ident: str, button=None, modifiers=None) -> None:
        button = button if button is not None else Qt.LeftButton
        modifiers = modifiers if modifiers is not None else Qt.NoModifier
        at = self.centre(ident)
        QApplication.sendEvent(self.page.board, QMouseEvent(
            QEvent.MouseButtonPress, at, at, button, button, modifiers))

    def move(self, ident: str, button=None, modifiers=None) -> None:
        button = button if button is not None else Qt.LeftButton
        modifiers = modifiers if modifiers is not None else Qt.NoModifier
        at = self.centre(ident)
        QApplication.sendEvent(self.page.board, QMouseEvent(
            QEvent.MouseMove, at, at, Qt.NoButton, button, modifiers))

    def release(self, ident: str) -> None:
        at = self.centre(ident)
        QApplication.sendEvent(self.page.board, QMouseEvent(
            QEvent.MouseButtonRelease, at, at,
            Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

    def stroke(self, *idents: str, button=None, modifiers=None) -> None:
        self.press(idents[0], button, modifiers)
        for ident in idents[1:]:
            self.move(ident, button, modifiers)
        self.release(idents[-1])

    def lit(self, *idents: str) -> set[int]:
        return {self.key(i).light_index for i in idents}


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Painting(Fixture):
    def test_a_press_paints_the_key_under_it(self):
        self.press("A")
        self.assertEqual(set(self.page.painted), self.lit("A"))
        self.assertEqual(self.page.painted[self.key("A").light_index].name(),
                         PAINT)

    def test_the_board_shows_what_has_been_painted(self):
        self.press("A")
        self.assertEqual(set(self.page.board.colours), self.lit("A"))

    def test_a_stroke_paints_every_key_it_crosses(self):
        self.stroke("A", "S", "D", "F")
        self.assertEqual(set(self.page.painted), self.lit("A", "S", "D", "F"))

    def test_a_stroke_stops_at_the_release(self):
        self.stroke("A", "S")
        self.move("D")
        self.assertEqual(set(self.page.painted), self.lit("A", "S"))

    def test_moving_with_no_button_down_paints_nothing(self):
        at = self.centre("A")
        QApplication.sendEvent(self.page.board, QMouseEvent(
            QEvent.MouseMove, at, at,
            Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        self.assertEqual(self.page.painted, {})

    def test_a_centre_is_where_the_key_is(self):
        # Guards the helper the rest of these tests lean on.
        self.assertIs(self.page.board.key_at(self.centre("Space")),
                      self.key("Space"))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Erasing(Fixture):
    def test_a_right_click_rubs_a_key_out(self):
        self.press("A")
        self.press("A", Qt.RightButton)
        # Not just absent: the view emits its click for any button, so
        # a press that reached it would have painted the key again.
        self.assertEqual(self.page.painted, {})
        self.assertEqual(self.page.stale, self.lit("A"))

    def test_a_ctrl_click_rubs_a_key_out_too(self):
        self.press("A")
        self.press("A", Qt.LeftButton, Qt.ControlModifier)
        self.assertEqual(self.page.painted, {})

    def test_a_right_drag_rubs_out_a_run(self):
        self.page._filler("All")()
        self.stroke("A", "S", "D", button=Qt.RightButton)
        self.assertEqual(self.lit("A", "S", "D"), self.page.stale)
        self.assertEqual(len(self.page.painted), len(kb_layout.KEYS) - 3)

    def test_erasing_a_blank_key_owes_it_nothing(self):
        self.press("A", Qt.RightButton)
        self.assertEqual(self.page.stale, set())

    def test_clearing_owes_every_painted_key_a_blanking(self):
        self.page._filler("WASD")()
        self.page._clear()
        self.assertEqual(self.page.painted, {})
        self.assertEqual(self.page.stale, self.lit("W", "A", "S", "D"))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Sending(Fixture):
    def sent(self) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        self.page.apply_key.connect(
            lambda index, colour: out.append((index, colour.name())))
        return out

    def test_a_rubbed_out_key_is_sent_black(self):
        out = self.sent()
        self.page._filler("WASD")()
        self.press("W", Qt.RightButton)
        self.page._send()
        black = [index for index, name in out if name == "#000000"]
        self.assertEqual(black, [self.key("W").light_index])
        self.assertEqual(len(out), 4)

    def test_a_blanking_is_owed_only_once(self):
        out = self.sent()
        self.page._filler("WASD")()
        self.page._clear()
        self.page._send()
        self.assertEqual(len(out), 4)
        self.assertEqual(self.page.stale, set())
        del out[:]
        self.page._send()
        self.assertEqual(out, [])

    def test_an_empty_board_with_nothing_owed_says_so(self):
        out = self.sent()
        self.page._send()
        self.assertEqual(out, [])
        self.assertEqual(self.said, ["nothing painted yet"])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Groups(Fixture):
    def idents(self, name: str) -> set[str]:
        self.page._clear()
        self.page._filler(name)()
        by_index = {k.light_index: k.ident for k in kb_layout.KEYS}
        return {by_index[i] for i in self.page.painted}

    def test_every_group_matches_something(self):
        # A group derived from the layout can go quietly empty if what
        # it looks for is not on this board -- which is what happened
        # to the function row, and why there is no group for one.
        for name in self.page.GROUPS:
            self.assertTrue(self.idents(name), name)

    def test_the_arrows_are_the_four_arrows(self):
        self.assertEqual(self.idents("Arrows"), {"←", "↑", "→", "↓"})

    def test_the_modifiers_are_the_modifier_block_and_fn(self):
        self.assertEqual(
            self.idents("Modifiers"),
            {"Control", "Win", "LeftAlt", "RightAlt", "RightCtrl",
             "LeftShift", "RightShift", "Fn"})

    def test_the_top_row_is_the_row_esc_sits_in(self):
        idents = self.idents("Top row")
        self.assertLessEqual({"Esc", "1", "0", "=", "~"}, idents)
        # The SWITCH key is drawn above the board, beside the strip.
        self.assertNotIn("SWITCH", idents)
        self.assertNotIn("Tab", idents)

    def test_the_letters_are_the_alphabet_on_the_board(self):
        self.assertEqual(len(self.idents("Letters")), 26)

    def test_all_still_means_all(self):
        self.assertEqual(len(self.idents("All")), len(kb_layout.KEYS))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Persistence(Fixture):
    def reopen(self) -> "screens.CustomPage":
        """A second page over the file the first one wrote."""
        store.use(store.Store.load(self.path))
        return screens.CustomPage(lambda: QColor("#ffffff"))

    def test_a_change_waits_for_the_timer_rather_than_writing_at_once(self):
        self.press("A")
        self.assertTrue(self.page._save.isActive())
        self.assertFalse(self.path.exists())

    def test_a_painted_layout_survives_the_file(self):
        self.stroke("A", "S", "D")
        self.page._save_now()
        again = self.reopen()
        self.assertEqual(set(again.painted), self.lit("A", "S", "D"))
        self.assertEqual(again.painted[self.key("A").light_index].name(),
                         PAINT)

    def test_an_erase_survives_the_file_too(self):
        self.stroke("A", "S")
        self.press("A", Qt.RightButton)
        self.page._save_now()
        self.assertEqual(set(self.reopen().painted), self.lit("S"))

    def test_the_palette_comes_back_with_the_layout(self):
        self.press("A")
        self.page._save_now()
        self.assertEqual([c.name() for c in self.reopen().recent], [PAINT])

    def test_the_layout_lands_in_the_active_scheme(self):
        self.press("A")
        self.page._save_now()
        self.assertEqual(self.page.store.current.lighting,
                         {self.key("A").light_index: 0x102030})

    def test_a_save_that_cannot_be_written_says_so(self):
        # A file where the directory should be: the save has to come
        # back as a message, not as an exception out of a timer.
        blocked = Path(self._dir.name) / "blocked"
        blocked.write_text("")
        self.page.store.path = blocked / "schemes.json"
        self.press("A")
        self.page._save_now()
        self.assertEqual(len(self.said), 1)
        self.assertTrue(self.said[0].startswith("could not save"))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Files(Fixture):
    def setUp(self):
        super().setUp()
        self.file = Path(self._dir.name) / "layout.json"

    def test_an_export_names_the_keys_the_way_the_keycaps_do(self):
        self.press("A")
        self.page._export_to(self.file)
        self.assertEqual(json.loads(self.file.read_text()),
                         {"version": 1, "keys": {"A": PAINT}})

    def test_an_exported_layout_imports_back(self):
        self.stroke("A", "S")
        self.page._use(QColor("#ff8800"))
        self.press("Space")
        before = {i: c.name() for i, c in self.page.painted.items()}
        self.page._export_to(self.file)
        self.page._clear()
        self.page._import_from(self.file)
        self.assertEqual({i: c.name() for i, c in self.page.painted.items()},
                         before)

    def test_an_import_replaces_what_was_there(self):
        self.page._filler("WASD")()
        self.file.write_text(json.dumps({"keys": {"W": "#00ff00"}}))
        self.page._import_from(self.file)
        self.assertEqual(set(self.page.painted), self.lit("W"))
        # The three it dropped are still lit on the keyboard.
        self.assertEqual(self.page.stale, self.lit("A", "S", "D"))

    def test_a_key_this_board_has_not_got_is_skipped(self):
        self.file.write_text(
            json.dumps({"keys": {"W": "#00ff00", "F13": "#00ff00"}}))
        self.page._import_from(self.file)
        self.assertEqual(set(self.page.painted), self.lit("W"))
        self.assertIn("1 skipped", self.said[-1])

    def test_a_plain_integer_colour_reads_as_one(self):
        self.file.write_text(json.dumps({"keys": {"W": 0x00FF00}}))
        self.page._import_from(self.file)
        self.assertEqual(self.page.painted[self.key("W").light_index].name(),
                         "#00ff00")

    def test_rubbish_is_refused_and_the_layout_left_alone(self):
        self.press("A")
        self.file.write_text("not a layout at all")
        self.page._import_from(self.file)
        self.assertEqual(set(self.page.painted), self.lit("A"))
        self.assertIn("not a layout", self.said[-1])

    def test_a_layout_for_another_board_is_refused_whole(self):
        self.file.write_text(json.dumps({"keys": {"F13": "#00ff00"}}))
        self.page._import_from(self.file)
        self.assertEqual(self.page.painted, {})
        self.assertIn("belong to this board", self.said[-1])

    def test_an_export_of_nothing_says_so(self):
        self.page._export()
        self.assertEqual(self.said, ["nothing painted to export"])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Palette(Fixture):
    def names(self) -> list[str]:
        return [c.name() for c in self.page.recent]

    def test_painting_remembers_the_colour_it_used(self):
        self.press("A")
        self.assertEqual(self.names(), [PAINT])

    def test_a_colour_used_again_moves_to_the_front_without_repeating(self):
        for colour in ("#ff0000", "#00ff00", "#ff0000"):
            self.page._use(QColor(colour))
        self.assertEqual(self.names(), ["#ff0000", "#00ff00"])

    def test_the_palette_stops_at_its_cap(self):
        for n in range(self.page.RECENT_COLOURS + 4):
            self.page._use(QColor(n * 8, 0, 0))
        self.assertEqual(len(self.page.recent), self.page.RECENT_COLOURS)

    def test_a_swatch_takes_its_colour_back_in_hand(self):
        self.page._use(QColor("#ff0000"))
        self.page._use(QColor("#00ff00"))
        self.page._palette_row.itemAt(1).widget().click()
        self.assertEqual(self.page._colour().name(), "#ff0000")

    def test_the_colour_in_hand_starts_as_the_app_s(self):
        # Nothing picked yet, so the page follows the rest of the app.
        self.assertEqual(self.page._colour().name(), PAINT)


if __name__ == "__main__":
    unittest.main()
