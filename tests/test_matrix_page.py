"""Checks the strip editor's bookkeeping.

Widgets need a QApplication but not a screen, so these run offscreen.
What is tested is the model the page keeps -- which frame is current,
what painting writes into, what an upload would carry -- rather than
how any of it is drawn.
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

from mk_k68_lightsmart import matrix

if QApplication is not None:
    from mk_k68_lightsmart import gui

app = None


def setUpModule() -> None:
    global app
    if QApplication is not None:
        app = QApplication.instance() or QApplication([])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class EditorModel(unittest.TestCase):
    def setUp(self) -> None:
        self.colour = QColor("#ff0000")
        self.page = gui.MatrixPage(lambda: self.colour)

    def test_starts_with_one_blank_frame(self):
        self.assertEqual(len(self.page.frames), 1)
        self.assertEqual(self.page.timeline.count(), 1)

    def test_painting_writes_into_the_current_frame(self):
        self.page.add_frame()
        self.page.on_pixel(4, 3)
        self.assertEqual(self.page.frames[1][4, 3], 0xFF0000)
        # and only into that one
        self.assertEqual(self.page.frames[0][4, 3], 0)

    def test_add_inserts_after_the_current_frame_and_follows_it(self):
        self.page.on_pixel(0, 0)
        self.page.add_frame()
        self.assertEqual(self.page.current, 1)
        self.assertEqual(self.page.frames[0][0, 0], 0xFF0000)
        self.assertEqual(self.page.frames[1][0, 0], 0)

    def test_duplicate_copies_the_pixels_without_sharing_them(self):
        self.page.on_pixel(2, 1)
        self.page.duplicate_frame()
        self.assertEqual(self.page.frames[1][2, 1], 0xFF0000)
        self.page.on_pixel(5, 0)                  # edits the copy
        self.assertEqual(self.page.frames[0][5, 0], 0)

    def test_deleting_the_last_frame_clears_it_instead(self):
        # An animation needs a frame, so there is nothing to delete to.
        self.page.on_pixel(1, 1)
        self.page.delete_frame()
        self.assertEqual(len(self.page.frames), 1)
        self.assertEqual(self.page.frames[0][1, 1], 0)

    def test_delete_keeps_the_selection_in_range(self):
        self.page.add_frame()
        self.page.add_frame()
        self.assertEqual(self.page.current, 2)
        self.page.delete_frame()
        self.assertEqual(len(self.page.frames), 2)
        self.assertEqual(self.page.current, 1)
        self.assertEqual(self.page.timeline.currentRow(), 1)

    def test_rebuilding_the_timeline_keeps_the_current_frame(self):
        # Regression: clearing a QListWidget emits currentRowChanged,
        # which selected frame 0 behind the caller's back.
        self.page.add_frame()
        self.page.add_frame()
        self.page.current = 1
        self.page._refresh_timeline()
        self.assertEqual(self.page.current, 1)

    def test_the_frame_cap_is_the_keyboards(self):
        self.page.frames = [matrix.Frame()] * matrix.MAX_FRAMES_ANIMATION
        self.page.current = 0
        notes = []
        self.page.status.connect(notes.append)
        self.page.add_frame()
        self.assertEqual(len(self.page.frames), matrix.MAX_FRAMES_ANIMATION)
        self.assertIn(str(matrix.MAX_FRAMES_ANIMATION), notes[0])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Playback(unittest.TestCase):
    def setUp(self) -> None:
        self.page = gui.MatrixPage(lambda: QColor("#00ff00"))

    def test_a_single_frame_is_not_an_animation(self):
        notes = []
        self.page.status.connect(notes.append)
        self.page.toggle_play()
        self.assertFalse(self.page._playing)
        self.assertTrue(notes)

    def test_playback_wraps(self):
        self.page.add_frame()
        self.page.toggle_play()
        self.assertTrue(self.page._playing)
        self.page._tick()
        self.assertEqual(self.page.current, 0)
        self.page.stop()
        self.assertFalse(self.page._playing)

    def test_faster_means_a_shorter_interval(self):
        self.assertLess(self.page._interval_ms(100), self.page._interval_ms(1))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Uploading(unittest.TestCase):
    def setUp(self) -> None:
        self.page = gui.MatrixPage(lambda: QColor("#0000ff"))

    def test_upload_hands_over_what_the_editor_holds(self):
        self.page.add_frame()
        self.page.on_pixel(0, 0)
        self.page.speed.setValue(30)
        self.page.brightness.setValue(80)

        sent = []
        self.page.upload_requested.connect(sent.append)
        self.page.upload()

        self.assertEqual(len(sent), 1)
        animation = sent[0]
        self.assertEqual(len(animation.frames), 2)
        self.assertEqual(animation.speed, 30)
        self.assertEqual(animation.brightness, 80)
        self.assertEqual(animation.payload()[:2], b"\x02\x00")

    def test_upload_stops_playback_first(self):
        # Letting the preview run would keep repainting the strip while
        # the keyboard is being written to.
        self.page.add_frame()
        self.page.toggle_play()
        self.page.upload()
        self.assertFalse(self.page._playing)

    def test_cancel_asks_the_window_and_disables_itself(self):
        asked = []
        self.page.cancel_requested.connect(lambda: asked.append(True))
        self.page.set_uploading(True)
        self.page.cancel()
        self.assertEqual(asked, [True])
        self.assertFalse(self.page.cancel_button.isEnabled())

    def test_the_upload_button_follows_the_connection(self):
        self.page.set_connected(False)
        self.assertFalse(self.page.upload_button.isEnabled())
        self.page.set_connected(True)
        self.assertTrue(self.page.upload_button.isEnabled())
        self.page.set_uploading(True, connected=True)
        self.assertFalse(self.page.upload_button.isEnabled())


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class SetupScreen(unittest.TestCase):
    """What the screen claims has to be what it checked."""

    def setUp(self) -> None:
        self.page = gui.SetupPage()

    def test_the_commands_name_this_installs_own_rule(self):
        text = self.page.command_text()
        self.assertIn(gui.SetupPage.RULE_NAME, text)
        self.assertIn(gui.SetupPage.RULE_DIR, text)
        self.assertEqual(len(text.splitlines()), 3)

    def test_a_snap_install_points_at_the_snap(self):
        # $SNAP is read-only and mounted elsewhere, so the path printed
        # has to be the one the running process can see.
        with unittest.mock.patch.dict(os.environ, {"SNAP": "/snap/x/current"}):
            self.assertTrue(
                gui._installed_rule_source().as_posix().startswith("/snap/x/current")
            )

    def test_every_row_reports_a_checked_condition(self):
        self.page.refresh("/dev/hidraw9", True)
        html = self.page.checks.text()
        self.assertEqual(html.count("<br>"), 2)
        self.assertIn("/dev/hidraw9", html)
        # The rule row has to agree with the filesystem, not a constant.
        marker = "✓" if self.page.rule_installed() else "•"
        rule_row = [r for r in html.split("<br>") if "udev rule" in r][0]
        self.assertIn(marker, rule_row)

    def test_a_disconnected_device_is_not_drawn_as_connected(self):
        self.page.refresh("no keyboard", False)
        rows = self.page.checks.text().split("<br>")
        self.assertIn("•", rows[-1])
