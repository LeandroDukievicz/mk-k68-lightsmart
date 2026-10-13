"""Checks the macro editor, which is finished and sends nothing.

There are no bytes to check here, because the event encoding is open --
so what is checked is that nothing anybody typed is lost on the way
between the event list, the scheme file it is kept in, and the file it
can be exported to. The recorder is checked through the handlers Qt
would call rather than through a real keypress: a modal dialog with the
keyboard grabbed cannot be driven from a test.

The store is swapped for one on a temporary path throughout. Every edit
on this screen saves, so a test that forgot would write over whatever
schemes the person running it had.
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
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    QApplication = None

from mk_k68_lightsmart import store

if QApplication is not None:
    from mk_k68_lightsmart.screens import macro

app = None


def setUpModule() -> None:
    global app
    if QApplication is not None:
        app = QApplication.instance() or QApplication([])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TheEventModel(unittest.TestCase):
    """The one piece of this screen with no Qt in it."""

    def test_a_delay_carries_its_milliseconds(self):
        event = macro.Event.delay(120)
        self.assertEqual(event.kind, macro.DELAY)
        self.assertEqual(event.ms, 120)

    def test_only_a_delay_has_milliseconds(self):
        self.assertEqual(macro.Event(macro.KEY_DOWN, "A").ms, 0)

    def test_an_unreadable_delay_reads_as_no_wait(self):
        # What a hand-edited file might hold. One bad delay should not
        # cost the macro it is in.
        self.assertEqual(macro.Event(macro.DELAY, "soon").ms, 0)

    def test_an_event_goes_through_the_stores_dict_shape_unchanged(self):
        event = macro.Event(macro.KEY_UP, "Space")
        self.assertEqual(macro.as_event(event.to_dict()), event)

    def test_a_bare_pair_is_still_an_event(self):
        self.assertEqual(macro.as_event(("Key up", "A")),
                         macro.Event(macro.KEY_UP, "A"))

    def test_an_event_reads_as_a_pair_itself(self):
        # Two fields is the contract with the store and with the older
        # callers, both of which unpack an event into kind and value.
        kind, value = macro.Event(macro.KEY_DOWN, "A")
        self.assertEqual((kind, value), ("Key down", "A"))

    def test_the_list_says_what_each_event_does(self):
        labels = [
            macro.Event(macro.KEY_DOWN, "A").label(),
            macro.Event(macro.KEY_UP, "A").label(),
            macro.Event.delay(40).label(),
            macro.Event(macro.MOUSE, "Left").label(),
        ]
        self.assertEqual([text.split()[0] for text in labels],
                         ["press", "release", "wait", "mouse"])
        self.assertIn("40 ms", labels[2])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TheDelayPolicies(unittest.TestCase):
    """The vendor's three, which is the whole of what recording offers."""

    def setUp(self):
        self.stamps = [
            (0, macro.KEY_DOWN, "A"),
            (30, macro.KEY_UP, "A"),
            (200, macro.KEY_DOWN, "B"),
        ]

    def test_keeping_the_real_delays_keeps_the_real_gaps(self):
        events = macro.events_from_recording(
            self.stamps, macro.KEEP_DELAYS)
        self.assertEqual(
            [(e.kind, e.value) for e in events],
            [("Key down", "A"), ("Delay (ms)", "30"),
             ("Key up", "A"), ("Delay (ms)", "170"),
             ("Key down", "B")])

    def test_no_delay_drops_the_gaps_and_keeps_the_keys(self):
        events = macro.events_from_recording(self.stamps, macro.NO_DELAY)
        self.assertEqual([e.kind for e in events],
                         ["Key down", "Key up", "Key down"])

    def test_one_delay_for_all_replaces_every_gap(self):
        events = macro.events_from_recording(
            self.stamps, macro.ONE_DELAY, 5)
        self.assertEqual([e.ms for e in events if e.kind == macro.DELAY],
                         [5, 5])

    def test_the_gap_before_the_first_keystroke_is_not_recorded(self):
        # It is the time between clicking Record and starting to type.
        events = macro.events_from_recording(
            [(900, macro.KEY_DOWN, "A")], macro.KEEP_DELAYS)
        self.assertEqual(events, [macro.Event(macro.KEY_DOWN, "A")])

    def test_a_gap_the_clock_could_not_see_is_dropped(self):
        events = macro.events_from_recording(
            [(7, macro.KEY_DOWN, "A"), (7, macro.KEY_UP, "A")],
            macro.KEEP_DELAYS)
        self.assertEqual(len(events), 2)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TheRecorder(unittest.TestCase):
    def setUp(self):
        self.dialog = macro.RecordDialog()

    def press(self, key, text="", release=False, repeat=False):
        kind = QEvent.KeyRelease if release else QEvent.KeyPress
        event = QKeyEvent(kind, key, Qt.NoModifier, text, repeat)
        if release:
            self.dialog.keyReleaseEvent(event)
        else:
            self.dialog.keyPressEvent(event)

    def test_a_press_and_a_release_are_both_recorded(self):
        self.press(Qt.Key_A, "a")
        self.press(Qt.Key_A, "a", release=True)
        self.assertEqual([kind for _, kind, _ in self.dialog.stamps],
                         ["Key down", "Key up"])

    def test_a_key_is_named_the_way_the_board_names_it(self):
        self.press(Qt.Key_A, "a")
        self.press(Qt.Key_Escape)
        self.press(Qt.Key_Up)
        self.assertEqual([value for _, _, value in self.dialog.stamps],
                         ["A", "Esc", "↑"])

    def test_a_held_key_is_one_press_not_a_hundred(self):
        self.press(Qt.Key_A, "a")
        for _ in range(9):
            self.press(Qt.Key_A, "a", repeat=True)
        self.assertEqual(len(self.dialog.stamps), 1)

    def test_what_was_typed_comes_back_under_the_chosen_policy(self):
        self.press(Qt.Key_A, "a")
        self.press(Qt.Key_A, "a", release=True)
        events = self.dialog.events(macro.NO_DELAY, 10)
        self.assertEqual([e.kind for e in events],
                         ["Key down", "Key up"])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class ThroughTheSchemeFile(unittest.TestCase):
    """What survives the app being closed, which is the point of it."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "schemes.json"
        # Kept and put back rather than replaced with a fresh store:
        # whatever ran before this is entitled to the one it had, and
        # asking for a new one here would read the real file.
        self.previous = store._shared
        store.use(store.Store(path=self.path))
        self.page = macro.MacroPage()
        self.page._load()

    def tearDown(self):
        store._shared = self.previous
        self.dir.cleanup()

    def make(self, name: str, events=()) -> None:
        """Put a macro on the screen the way ``_new`` would."""
        self.page.macros[name] = list(events)
        self.page.names.addItem(name)
        self.page._select(name)
        self.page._persist()

    def saved(self, name: str) -> list[dict]:
        return store.Store.load(self.path).current.macros[name]

    def test_an_edit_is_in_the_file_before_anything_else_happens(self):
        self.make("Login", [macro.Event(macro.KEY_DOWN, "A")])
        self.page.kind.setCurrentIndex(macro.KINDS.index(macro.KEY_UP))
        self.page.value.setCurrentText("A")
        self.page._add()
        self.assertEqual(self.saved("Login"),
                         [{"kind": "Key down", "value": "A"},
                          {"kind": "Key up", "value": "A"}])

    def test_a_macro_comes_back_when_the_screen_is_shown_again(self):
        self.make("Login", [macro.Event.delay(25),
                            macro.Event(macro.KEY_DOWN, "A")])
        fresh = macro.MacroPage()
        fresh.show()                 # the screen reads the scheme on show
        self.assertEqual(fresh.macros["Login"],
                         [macro.Event(macro.DELAY, "25"),
                          macro.Event(macro.KEY_DOWN, "A")])
        self.assertEqual(fresh.events.count(), 2)

    def test_a_delay_survives_as_a_number_not_as_a_label(self):
        self.make("Wait", [macro.Event.delay(1500)])
        fresh = macro.MacroPage()
        fresh._load()
        self.assertEqual(fresh.macros["Wait"][0].ms, 1500)

    def test_each_scheme_keeps_its_own_macros(self):
        self.make("Login", [macro.Event(macro.KEY_DOWN, "A")])
        store.shared().add("Work")
        store.shared().active = 1
        self.page._load()
        self.assertEqual(self.page.macros, {})
        self.make("Other", [macro.Event(macro.KEY_DOWN, "B")])
        store.shared().active = 0
        self.page._load()
        self.assertEqual(list(self.page.macros), ["Login"])

    def test_deleting_a_macro_leaves_the_file_without_it(self):
        self.make("Login")
        self.make("Spare")
        self.page._select("Spare")
        self.page._delete()
        self.assertEqual(
            list(store.Store.load(self.path).current.macros), ["Login"])

    def test_the_events_shown_are_the_ones_in_the_macro(self):
        self.make("Login", [macro.Event(macro.KEY_DOWN, "Caps"),
                            macro.Event.delay(10)])
        self.page._show("Login")
        rows = [self.page.events.item(i).text()
                for i in range(self.page.events.count())]
        self.assertIn("press    Caps", rows[0])
        self.assertIn("wait     10 ms", rows[1])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Editing(ThroughTheSchemeFile):
    """The vendor's own set of edits, over a macro of three events."""

    def setUp(self):
        super().setUp()
        self.make("Login", [macro.Event(macro.KEY_DOWN, "A"),
                            macro.Event.delay(10),
                            macro.Event(macro.KEY_UP, "A")])

    def stage(self, value: str, kind: str = macro.KEY_DOWN) -> None:
        """Describe an event in the controls.

        After the selection, always: picking an event in the list is
        what loads it into these same controls, so staging first would
        be undone by the pick.
        """
        self.page.kind.setCurrentIndex(macro.KINDS.index(kind))
        self.page.value.setCurrentText(value)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.page.macros["Login"]]

    def test_insert_before_lands_above_the_selected_event(self):
        self.page.events.setCurrentRow(1)
        self.stage("B")
        self.page._insert(False)
        self.assertEqual(self.kinds()[:2], ["Key down", "Key down"])
        self.assertEqual(self.page.macros["Login"][1].value, "B")

    def test_insert_after_lands_below_it(self):
        self.page.events.setCurrentRow(0)
        self.stage("B")
        self.page._insert(True)
        self.assertEqual(self.page.macros["Login"][1],
                         macro.Event(macro.KEY_DOWN, "B"))

    def test_an_insert_with_nothing_selected_goes_at_the_end(self):
        self.page.events.setCurrentRow(-1)
        self.stage("B")
        self.page._insert(True)
        self.assertEqual(self.page.macros["Login"][-1].value, "B")

    def test_changing_one_event_leaves_its_neighbours_alone(self):
        self.page.events.setCurrentRow(1)
        self.page.kind.setCurrentIndex(macro.KINDS.index(macro.DELAY))
        self.page.ms.setValue(250)
        self.page._replace()
        self.assertEqual(self.kinds(), ["Key down", "Delay (ms)", "Key up"])
        self.assertEqual(self.page.macros["Login"][1].ms, 250)

    def test_picking_an_event_puts_it_in_the_controls_to_edit(self):
        self.page.events.setCurrentRow(1)
        self.assertEqual(self.page.kind.currentText(), macro.DELAY)
        self.assertEqual(self.page.ms.value(), 10)

    def test_a_mouse_event_is_offered_buttons_and_not_keys(self):
        self.page.kind.setCurrentIndex(macro.KINDS.index(macro.MOUSE))
        offered = [self.page.value.itemText(i)
                   for i in range(self.page.value.count())]
        self.assertEqual(offered, list(macro.MOUSE_BUTTONS))

    def test_removing_an_event_closes_the_gap(self):
        self.page.events.setCurrentRow(1)
        self.page._remove()
        self.assertEqual(self.kinds(), ["Key down", "Key up"])

    def test_moving_an_event_moves_it_in_the_file_too(self):
        self.page.events.setCurrentRow(2)
        self.page._move(-1)
        self.assertEqual(
            [e["kind"] for e in self.saved("Login")],
            ["Key down", "Key up", "Delay (ms)"])

    def test_duplicating_a_macro_does_not_share_its_events(self):
        self.page._duplicate()
        self.page.macros["Login copy"].append(macro.Event.delay(1))
        self.assertEqual(len(self.page.macros["Login"]), 3)
        self.assertIn("Login copy", store.Store.load(self.path)
                      .current.macros)

    def test_a_name_already_taken_gets_a_number(self):
        self.assertEqual(self.page._free_name("Login"), "Login 2")
        self.page.macros["Login 2"] = []
        self.assertEqual(self.page._free_name("Login"), "Login 3")

    def test_adding_to_no_macro_says_so_rather_than_failing(self):
        said = []
        self.page.names.setCurrentRow(-1)
        self.page.names.clearSelection()
        self.page.status.connect(said.append)
        self.page._add()
        self.assertEqual(said, ["make a macro first"])

    def test_nothing_on_this_screen_can_be_sent(self):
        said = []
        self.page.status.connect(said.append)
        self.assertFalse(self.page.send.isEnabled())
        self.page._send()
        self.assertIn("undecoded", said[0])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class AsAFile(unittest.TestCase):
    """Export and import, which is how a macro reaches somebody else."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "login.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_macro_comes_back_the_way_it_went_out(self):
        events = [macro.Event(macro.KEY_DOWN, "A"), macro.Event.delay(40)]
        macro.write_macro(self.path, "Login", events)
        self.assertEqual(macro.read_macro(self.path), ("Login", events))

    def test_a_bare_list_of_events_is_accepted(self):
        # What a macro looks like inside the scheme file, so pasting one
        # out of there should work.
        self.path.write_text(json.dumps([{"kind": "Key up", "value": "A"}]))
        name, events = macro.read_macro(self.path)
        self.assertEqual((name, events), ("", [macro.Event("Key up", "A")]))

    def test_an_unrelated_json_file_is_refused(self):
        self.path.write_text(json.dumps({"schemes": []}))
        with self.assertRaises(ValueError):
            macro.read_macro(self.path)

    def test_a_file_that_is_not_json_is_refused(self):
        self.path.write_text("not json at all")
        with self.assertRaises(ValueError):
            macro.read_macro(self.path)


if __name__ == "__main__":
    unittest.main()
