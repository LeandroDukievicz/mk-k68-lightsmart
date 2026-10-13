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
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
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

    def test_a_click_reads_apart_from_the_two_halves_of_one(self):
        # The recorder writes the pair, because a button held down
        # across a delay is something a macro can mean and one event
        # could not say it.
        self.assertEqual(macro.Event(macro.MOUSE, "Left").label(),
                         "mouse    Left")
        self.assertIn("Left down",
                      macro.Event(macro.MOUSE_DOWN, "Left").label())
        self.assertIn("Left up",
                      macro.Event(macro.MOUSE_UP, "Left").label())
        self.assertTrue(
            macro.Event(macro.WHEEL, "Up").label().startswith("wheel"))

    def test_each_kind_offers_only_the_values_it_can_take(self):
        self.assertEqual(macro.value_choices(macro.MOUSE_DOWN),
                         list(macro.MOUSE_BUTTONS))
        self.assertEqual(macro.value_choices(macro.WHEEL),
                         list(macro.WHEEL_WAYS))
        self.assertIn("A", macro.value_choices(macro.KEY_DOWN))

    def test_a_mouse_button_is_named_as_the_value_list_names_it(self):
        self.assertEqual(macro.mouse_name(Qt.LeftButton), "Left")
        self.assertEqual(macro.mouse_name(Qt.MiddleButton), "Middle")
        # The names the recorder writes are the names the editor
        # offers, or an event would be recorded that nobody could edit.
        self.assertEqual([macro.mouse_name(b) for b in macro.MOUSE_BY_BUTTON],
                         list(macro.MOUSE_BUTTONS))
        self.assertEqual(macro.mouse_name(Qt.BackButton), "")

    def test_the_kinds_already_saved_keep_the_places_they_had(self):
        # The scheme file holds these strings, and a screen reads an
        # event back by looking its kind up in this tuple, so a kind
        # added anywhere but the end would orphan saved macros.
        self.assertEqual(macro.KINDS[:4],
                         ("Key down", "Key up", "Delay (ms)",
                          "Mouse button"))


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

    def click(self, button, release=False):
        """A click on the dialog's own body, which Qt hands to it.

        A click on Stop or Discard never arrives here: the button under
        the pointer takes it, which is what leaves the pair of them
        working while the rest of the dialog records the mouse.
        """
        kind = (QEvent.MouseButtonRelease if release
                else QEvent.MouseButtonPress)
        event = QMouseEvent(kind, QPointF(8, 8), QPointF(8, 8), button,
                            Qt.NoButton if release else button,
                            Qt.NoModifier)
        if release:
            self.dialog.mouseReleaseEvent(event)
        else:
            self.dialog.mousePressEvent(event)

    def wheel(self, notches):
        self.dialog.wheelEvent(QWheelEvent(
            QPointF(8, 8), QPointF(8, 8), QPoint(0, 0),
            QPoint(0, 120 * notches), Qt.NoButton, Qt.NoModifier,
            Qt.NoScrollPhase, False))

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

    def test_a_mouse_button_is_recorded_as_a_press_and_a_release(self):
        self.click(Qt.LeftButton)
        self.click(Qt.LeftButton, release=True)
        self.assertEqual([(kind, value)
                          for _, kind, value in self.dialog.stamps],
                         [("Mouse down", "Left"), ("Mouse up", "Left")])

    def test_a_button_this_screen_cannot_name_is_not_recorded(self):
        # Back, forward, the ones down the side of a gaming mouse: the
        # value list cannot offer them, so nobody could edit one.
        self.click(Qt.BackButton)
        self.assertEqual(self.dialog.stamps, [])

    def test_the_wheel_is_recorded_with_the_way_it_turned(self):
        self.wheel(1)
        self.wheel(-1)
        self.assertEqual([value for _, _, value in self.dialog.stamps],
                         ["Up", "Down"])
        self.assertEqual({kind for _, kind, _ in self.dialog.stamps},
                         {macro.WHEEL})

    def test_the_pointer_moving_about_is_not_an_event(self):
        # A macro of this board's carries no coordinates, and the
        # clicks would be buried under the travel between them.
        self.dialog.mouseMoveEvent(QMouseEvent(
            QEvent.MouseMove, QPointF(9, 9), QPointF(9, 9), Qt.NoButton,
            Qt.LeftButton, Qt.NoModifier))
        self.assertEqual(self.dialog.stamps, [])

    def test_the_keyboard_and_the_mouse_are_one_recording(self):
        self.press(Qt.Key_A, "a")
        self.click(Qt.LeftButton)
        self.press(Qt.Key_A, "a", release=True)
        self.assertEqual([kind for _, kind, _ in self.dialog.stamps],
                         ["Key down", "Mouse down", "Key up"])
        # And a mouse event is spaced by the delay policy like any
        # other: the policies look at the stamps, not at the kinds.
        kinds = [e.kind for e in self.dialog.events(macro.ONE_DELAY, 7)]
        self.assertEqual(kinds.count(macro.DELAY), 2)


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

    def offered(self) -> list[str]:
        return [self.page.value.itemText(i)
                for i in range(self.page.value.count())]

    def test_a_mouse_event_is_offered_buttons_and_not_keys(self):
        self.page.kind.setCurrentIndex(macro.KINDS.index(macro.MOUSE))
        self.assertEqual(self.offered(), list(macro.MOUSE_BUTTONS))

    def test_a_wheel_event_is_offered_the_two_ways_round(self):
        self.page.kind.setCurrentIndex(macro.KINDS.index(macro.WHEEL))
        self.assertEqual(self.offered(), list(macro.WHEEL_WAYS))

    def test_every_kind_of_event_can_be_staged(self):
        self.assertEqual([self.page.kind.itemText(i)
                          for i in range(self.page.kind.count())],
                         list(macro.KINDS))

    def test_a_delay_greys_the_value_and_a_key_greys_the_number(self):
        self.page.kind.setCurrentIndex(macro.KINDS.index(macro.DELAY))
        self.assertFalse(self.page.value.isEnabled())
        self.assertTrue(self.page.ms.isEnabled())
        self.page.kind.setCurrentIndex(macro.KINDS.index(macro.KEY_DOWN))
        self.assertTrue(self.page.value.isEnabled())
        self.assertFalse(self.page.ms.isEnabled())

    def test_a_mouse_event_is_inserted_and_kept_like_any_other(self):
        self.page.events.setCurrentRow(0)
        self.stage("Right", macro.MOUSE_DOWN)
        self.page._insert(True)
        self.assertEqual(self.saved("Login")[1],
                         {"kind": "Mouse down", "value": "Right"})

    def test_the_panel_opens_on_insert_before(self):
        # Which is where the vendor's dropdown opens, and the button
        # under it means whatever the dropdown last said.
        self.assertEqual(self.page.where.currentText(), macro.INSERT_BEFORE)
        self.page.events.setCurrentRow(1)
        self.stage("B")
        self.page._insert_here()
        self.assertEqual(self.page.macros["Login"][1].value, "B")

    def test_the_panel_inserts_after_when_the_dropdown_says_so(self):
        self.page.where.setCurrentIndex(
            macro.PLACES.index(macro.INSERT_AFTER))
        self.page.events.setCurrentRow(1)
        self.stage("B")
        self.page._insert_here()
        self.assertEqual(self.page.macros["Login"][2],
                         macro.Event(macro.KEY_DOWN, "B"))

    def test_the_dropdown_is_still_saying_it_on_the_next_insert(self):
        # The point of a dropdown rather than a button each: a run of
        # events is one choice made once, then Insert, Insert, Insert.
        self.page.where.setCurrentIndex(
            macro.PLACES.index(macro.INSERT_AFTER))
        self.page.events.setCurrentRow(0)
        self.stage("B")
        self.page._insert_here()
        self.stage("C")
        self.page._insert_here()
        self.assertEqual([e.value for e in self.page.macros["Login"]],
                         ["A", "B", "C", "10", "A"])

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
class CopyAndPaste(ThroughTheSchemeFile):
    """The pair behind the vendor's copy button and its list's menu."""

    def setUp(self):
        super().setUp()
        # The copy is kept on the module so that it outlives a screen
        # and crosses between schemes, which also means a test that
        # left one behind would be felt by the next.
        self.previous_copy = macro._copied
        macro._copied = None
        self.said: list[str] = []
        self.page.status.connect(self.said.append)
        self.make("Login", [macro.Event(macro.KEY_DOWN, "A"),
                            macro.Event.delay(10)])

    def tearDown(self):
        macro._copied = self.previous_copy
        super().tearDown()

    def test_a_copied_macro_pastes_in_under_a_free_name(self):
        self.page._copy()
        self.page._paste()
        self.assertEqual(list(self.page.macros), ["Login", "Login 2"])
        self.assertEqual(self.page.macros["Login 2"],
                         self.page.macros["Login"])
        self.assertIn("Login 2", store.Store.load(self.path).current.macros)

    def test_pasting_twice_makes_two_macros_and_not_one(self):
        self.page._copy()
        self.page._paste()
        self.page._paste()
        self.assertEqual(list(self.page.macros),
                         ["Login", "Login 2", "Login 3"])

    def test_a_pasted_macro_has_a_list_of_its_own(self):
        self.page._copy()
        self.page._paste()
        self.page.macros["Login 2"].append(macro.Event.delay(1))
        self.assertEqual(len(self.page.macros["Login"]), 2)

    def test_what_is_pasted_is_what_was_copied_at_the_time(self):
        # The copy is taken by Copy, not read out of the macro again at
        # Paste: the one it came from can have been edited since, or
        # renamed, or deleted.
        self.page._copy()
        self.page.macros["Login"].append(macro.Event.delay(99))
        self.page._paste()
        self.assertEqual(len(self.page.macros["Login 2"]), 2)

    def test_a_copy_crosses_from_one_scheme_to_another(self):
        # Which is the whole of what Paste can do that Duplicate
        # cannot: Duplicate is the copy and the paste in one step, and
        # both of them land in the scheme being looked at.
        self.page._copy()
        store.shared().add("Work")
        store.shared().active = 1
        self.page._load()
        self.page._paste()
        self.assertEqual(list(self.page.macros), ["Login"])
        self.assertEqual(self.page.macros["Login"][0].value, "A")

    def test_pasting_with_nothing_copied_says_so(self):
        self.page._paste()
        self.assertEqual(self.said, ["nothing has been copied"])
        self.assertEqual(list(self.page.macros), ["Login"])

    def test_copying_nothing_says_so_rather_than_failing(self):
        self.page.names.setCurrentRow(-1)
        self.page.names.clearSelection()
        self.page._copy()
        self.assertEqual(self.said, ["pick a macro to copy"])

    def test_the_blank_space_menu_is_the_vendors_three(self):
        self.assertEqual([a.text() for a in self.page._macro_menu(False)
                          .actions()],
                         ["New", "Import…", "Paste"])

    def test_the_menu_over_a_macro_carries_its_own_commands_too(self):
        texts = [a.text() for a in self.page._macro_menu(True).actions()]
        self.assertEqual(texts[:3], ["New", "Import…", "Paste"])
        for command in ("Copy", "Duplicate", "Rename", "Export…", "Delete"):
            self.assertIn(command, texts)

    def test_paste_is_greyed_until_something_has_been_copied(self):
        def paste_of(menu):
            return next(a for a in menu.actions() if a.text() == "Paste")

        self.assertFalse(paste_of(self.page._macro_menu(False)).isEnabled())
        self.page._copy()
        self.assertTrue(paste_of(self.page._macro_menu(False)).isEnabled())


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TheRecordPolicy(ThroughTheSchemeFile):
    """The three radios, the fixed delay beside them, and Record."""

    class FakeRecorder:
        """Stands in for the dialog, which a test cannot drive.

        The real one is modal and holds the keyboard; what matters on
        this side of it is that the screen asks it for the policy the
        radios are showing and keeps what comes back.
        """

        made: list["TheRecordPolicy.FakeRecorder"] = []
        answer = True

        def __init__(self, parent=None):
            TheRecordPolicy.FakeRecorder.made.append(self)
            self.asked = None

        def exec(self):
            return TheRecordPolicy.FakeRecorder.answer

        def events(self, policy, fixed_ms):
            self.asked = (policy, fixed_ms)
            return [macro.Event(macro.KEY_DOWN, "Z")]

    def setUp(self):
        super().setUp()
        self.said: list[str] = []
        self.page.status.connect(self.said.append)
        self.FakeRecorder.made = []
        self.FakeRecorder.answer = True
        self.make("Login", [macro.Event(macro.KEY_DOWN, "A")])

    def recording(self):
        """Put the stand-in dialog in the real one's place."""
        return unittest.mock.patch.object(
            macro, "RecordDialog", self.FakeRecorder)

    def test_recording_keeps_the_real_delays_until_told_otherwise(self):
        self.assertEqual(self.page._policy(), macro.KEEP_DELAYS)
        self.assertTrue(self.page.policies[macro.KEEP_DELAYS].isChecked())

    def test_the_fixed_delay_starts_at_the_vendors_ten_milliseconds(self):
        self.assertEqual(self.page.delay_ms.value(), 10)

    def test_the_fixed_delay_is_greyed_until_it_is_the_one_in_use(self):
        self.assertFalse(self.page.delay_ms.isEnabled())
        self.page.policies[macro.ONE_DELAY].setChecked(True)
        self.assertTrue(self.page.delay_ms.isEnabled())
        self.page.policies[macro.NO_DELAY].setChecked(True)
        self.assertFalse(self.page.delay_ms.isEnabled())

    def test_the_radios_are_one_choice_and_not_three(self):
        for name in macro.POLICIES:
            self.page.policies[name].setChecked(True)
            self.assertEqual(self.page._policy(), name)
            self.assertEqual(
                [n for n in macro.POLICIES
                 if self.page.policies[n].isChecked()], [name])

    def test_recording_is_told_what_the_radios_say(self):
        self.page.policies[macro.ONE_DELAY].setChecked(True)
        self.page.delay_ms.setValue(25)
        with self.recording():
            self.page._record()
        self.assertEqual(self.FakeRecorder.made[0].asked,
                         (macro.ONE_DELAY, 25))

    def test_a_recording_replaces_the_macro_and_is_saved(self):
        with self.recording():
            self.page._record()
        self.assertEqual(self.saved("Login"),
                         [{"kind": "Key down", "value": "Z"}])
        self.assertEqual(self.page.events.count(), 1)

    def test_a_discarded_recording_leaves_the_macro_alone(self):
        self.FakeRecorder.answer = False
        with self.recording():
            self.page._record()
        self.assertEqual(self.page.macros["Login"],
                         [macro.Event(macro.KEY_DOWN, "A")])
        self.assertEqual(self.said, ["recording discarded"])

    def test_recording_into_no_macro_says_so_rather_than_failing(self):
        self.page.names.setCurrentRow(-1)
        self.page.names.clearSelection()
        with self.recording():
            self.page._record()
        self.assertEqual(self.said, ["make a macro first"])
        self.assertEqual(self.FakeRecorder.made, [])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Saving(ThroughTheSchemeFile):
    """The vendor's save button, over a screen that saves anyway."""

    def setUp(self):
        super().setUp()
        self.said: list[str] = []
        self.page.status.connect(self.said.append)

    def test_saving_says_what_was_kept(self):
        self.make("Login", [macro.Event(macro.KEY_DOWN, "A")])
        self.said.clear()
        self.page._save()
        self.assertEqual(self.said, ["1 macro saved"])
        self.assertIn("Login", store.Store.load(self.path).current.macros)

    def test_saving_counts_them_all(self):
        self.make("Login")
        self.make("Spare")
        self.page._save()
        self.assertEqual(self.said[-1], "2 macros saved")

    def test_a_file_that_cannot_be_written_is_said_and_not_swallowed(self):
        def refuse():
            raise OSError("read-only file system")

        self.make("Login")
        store.shared().save = refuse
        self.said.clear()
        self.page._save()
        self.assertEqual(len(self.said), 1)
        self.assertIn("macros not saved", self.said[0])

    def test_an_edit_that_cannot_be_saved_still_says_so(self):
        # Every edit saves, so the warning belongs to the edit as well
        # as to the button: the editing goes on, the file does not.
        def refuse():
            raise OSError("read-only file system")

        self.make("Login", [])
        store.shared().save = refuse
        self.said.clear()
        self.page._add()
        self.assertEqual(len(self.page.macros["Login"]), 1)
        self.assertIn("macros not saved", self.said[0])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class FilesOnTheScreen(ThroughTheSchemeFile):
    """Import and export as the buttons do them, dialog and all."""

    def setUp(self):
        super().setUp()
        self.said: list[str] = []
        self.page.status.connect(self.said.append)
        self.file = Path(self.dir.name) / "shared.json"

    def choosing(self, which: str):
        """Answer the file dialog named, with this test's own path."""
        return unittest.mock.patch.object(
            macro.QFileDialog, which,
            staticmethod(lambda *a, **k: (str(self.file), "")))

    def test_an_exported_macro_can_be_imported_back(self):
        self.make("Login", [macro.Event(macro.KEY_DOWN, "A"),
                            macro.Event(macro.MOUSE_DOWN, "Left")])
        with self.choosing("getSaveFileName"):
            self.page._export()
        with self.choosing("getOpenFileName"):
            self.page._import()
        # Back under a free name rather than over the one it came from.
        self.assertEqual(self.page.macros["Login 2"],
                         self.page.macros["Login"])
        self.assertIn("Login 2", store.Store.load(self.path).current.macros)

    def test_an_imported_macro_is_the_one_being_looked_at(self):
        macro.write_macro(self.file, "Shared", [macro.Event.delay(40)])
        with self.choosing("getOpenFileName"):
            self.page._import()
        self.assertEqual(self.page._current(), "Shared")
        self.assertEqual(self.page.events.count(), 1)

    def test_a_file_that_is_not_a_macro_is_refused_and_said(self):
        self.file.write_text(json.dumps({"schemes": []}))
        shown: list[str] = []
        with self.choosing("getOpenFileName"), unittest.mock.patch.object(
                macro.QMessageBox, "warning",
                staticmethod(lambda _p, title, *a: shown.append(title))):
            self.page._import()
        self.assertEqual(shown, ["Import"])
        self.assertEqual(self.page.macros, {})

    def test_exporting_nothing_says_so(self):
        self.page._export()
        self.assertEqual(self.said, ["pick a macro to export"])


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

    def test_a_mouse_event_goes_out_and_comes_back_as_one(self):
        events = [macro.Event(macro.MOUSE_DOWN, "Left"),
                  macro.Event(macro.MOUSE_UP, "Left"),
                  macro.Event(macro.WHEEL, "Down")]
        macro.write_macro(self.path, "Click", events)
        self.assertEqual(macro.read_macro(self.path), ("Click", events))

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
