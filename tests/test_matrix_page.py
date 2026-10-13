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
        self.assertGreater(len(self.page.frames), 1, "the default should load")
        # The rest of these are about editing, so start from a blank.
        self.page.frames = [matrix.Frame()]
        self.page.current = 0
        self.page._refresh_timeline()

    def test_it_opens_on_the_default_pattern(self):
        fresh = gui.MatrixPage(lambda: self.colour)
        self.assertEqual(
            [list(f.pixels) for f in fresh.frames],
            [list(f.pixels) for f in gui.patterns.build(fresh.DEFAULT_PATTERN)],
        )
        self.assertEqual(fresh.timeline.count(), len(fresh.frames))

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
        self.page.frames = [matrix.Frame()]
        self.page.current = 0
        self.page._refresh_timeline()

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
        self.page.frames = [matrix.Frame()]
        self.page.current = 0
        self.page._refresh_timeline()

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


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class ImportingAPicture(unittest.TestCase):
    """The file dialog's end of the import, with the dialogs stubbed."""

    #: the same two-frame GIF the image tests use
    import base64 as _base64
    TINY_GIF = _base64.b64decode(
        "R0lGODlhAgABAPAAAP8AAAAA/yH5BAAKAAAALAAAAAACAAEAAAICBAoAIfkEAAoAAAAsAAAA"
        "AAIAAQAAAgJMCgA7"
    )

    def setUp(self) -> None:
        import tempfile
        self.page = gui.MatrixPage(lambda: QColor("#ffffff"))
        self.page.frames = [matrix.Frame()]
        self.page.current = 0
        self.page._refresh_timeline()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "tiny.gif")
        with open(self.path, "wb") as fh:
            fh.write(self.TINY_GIF)

    def _choose(self, path):
        return unittest.mock.patch.object(
            gui.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (path, ""))
        )

    def test_a_chosen_file_becomes_the_frames(self):
        notes = []
        self.page.status.connect(notes.append)
        with self._choose(self.path):
            self.page.import_image()
        self.assertEqual(len(self.page.frames), 2)
        self.assertEqual(self.page.timeline.count(), 2)
        self.assertEqual(self.page.current, 0)
        self.assertIn("imported 2 frames", notes[-1])

    def test_cancelling_the_dialog_changes_nothing(self):
        before = list(self.page.frames)
        with self._choose(""):
            self.page.import_image()
        self.assertEqual(self.page.frames, before)

    def test_a_file_that_is_not_an_image_is_reported_not_raised(self):
        bad = os.path.join(self.dir.name, "notes.txt")
        with open(bad, "w") as fh:
            fh.write("not a picture")
        with self._choose(bad), unittest.mock.patch.object(
            gui.QMessageBox, "warning", staticmethod(lambda *a, **k: None)
        ) as warned:
            self.page.import_image()
        self.assertEqual(len(self.page.frames), 1)     # the blank it started with

    def test_a_long_animation_asks_how_many_frames_to_keep(self):
        # Pretend the file holds more than the page imports without asking.
        peek = gui.image.Imported(frames=[matrix.Frame()], available=312,
                                  delay_ms=100, size=(700, 70))
        asked = {}

        def fake_getInt(parent, title, label, value, lo, hi):
            asked.update(default=value, low=lo, high=hi, label=label)
            return value, True

        with self._choose(self.path), \
             unittest.mock.patch.object(gui.image, "load",
                                        side_effect=[peek, peek]), \
             unittest.mock.patch.object(gui.QInputDialog, "getInt",
                                        staticmethod(fake_getInt)):
            self.page.import_image()

        self.assertEqual(asked["default"], 3)          # 312 / 120, rounded up
        self.assertEqual(asked["high"], 312)
        self.assertIn("312 frames", asked["label"])

    def test_the_time_estimate_matches_the_payload(self):
        blocks = matrix.Animation(frames=[matrix.Frame()] * 10).block_count()
        self.assertEqual(self.page._seconds_for(10), round(blocks * 0.015))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class LightScreen(unittest.TestCase):
    """The screen that picks a built-in animation."""

    def setUp(self) -> None:
        from PySide6.QtGui import QColor
        self.page = gui.LightPage(lambda: QColor("#102030"))

    def test_it_lists_every_effect_the_driver_has(self):
        from mk_k68_lightsmart import protocol
        self.assertEqual(self.page.effects.count(), len(protocol.EFFECTS))
        # The mode is shown, since it is the thing the protocol uses.
        self.assertIn("0x00", self.page.effects.item(
            len(protocol.EFFECTS) - 1).text())

    def test_the_selection_is_the_effect_not_the_row(self):
        from mk_k68_lightsmart import protocol
        self.page.effects.setCurrentRow(len(protocol.EFFECTS) - 1)
        chosen = self.page.selected()
        self.assertEqual(chosen.name, "Close Backlight")
        self.assertEqual(chosen.mode, 0x00)

    def test_apply_carries_the_mode_colour_and_parameters(self):
        sent = []
        self.page.apply_requested.connect(lambda *args: sent.append(args))
        self.page.effects.setCurrentRow(6)          # Breath, 0x07
        self.page.sliders["brightness"].setValue(12)
        self.page.sliders["speed"].setValue(3)
        self.page.sliders["direction"].setValue(1)
        self.page.apply()
        self.assertEqual(sent, [(0x07, 0x102030, 12, 3, 1)])

    def test_the_note_admits_what_is_not_known(self):
        # The parameter bytes are inferred; the screen should not imply
        # otherwise by offering three confident sliders alone.
        notes = [w.text() for w in self.page.findChildren(gui.QLabel)]
        self.assertTrue(any("inferred" in text for text in notes))
