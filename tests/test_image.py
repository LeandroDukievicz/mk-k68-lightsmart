"""Checks that a picture becomes the strip frames it looks like.

The fixture is a two-frame GIF written out byte by byte below rather
than a file from the vendor's library: it keeps the suite self-contained
and ships none of the manufacturer's artwork.
"""

from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QColor, QImage
except ImportError:                                   # pragma: no cover
    QImage = None

from mk_k68_lightsmart import matrix

if QImage is not None:
    from mk_k68_lightsmart import image

#: 2x1 pixels, two frames 100 ms apart: one red, one blue.
TINY_GIF = base64.b64decode(
    "R0lGODlhAgABAPAAAP8AAAAA/yH5BAAKAAAALAAAAAACAAEAAAICBAoAIfkEAAoAAAAsAAAA"
    "AAIAAQAAAgJMCgA7"
)

requires_qt = unittest.skipIf(QImage is None, "PySide6 is not installed")


@requires_qt
class Scaling(unittest.TestCase):
    def test_a_source_drawn_ten_pixels_per_led_comes_back_unchanged(self):
        # What the vendor's manual asks for: 650x50, ten pixels a dot.
        source = QImage(650, 50, QImage.Format_RGB32)
        source.fill(QColor("#000000"))
        for col in range(matrix.COLS):
            colour = QColor(col * 3, 255 - col * 3, 128)
            for x in range(col * 10, col * 10 + 10):
                for y in range(10):
                    source.setPixelColor(x, y, colour)

        frame = image.frame_from_image(source)
        for col in range(matrix.COLS):
            self.assertEqual(frame[col, 0], QColor(col * 3, 255 - col * 3, 128).rgb()
                             & 0xFFFFFF, f"column {col}")

    def test_any_size_is_squashed_to_the_strip(self):
        source = QImage(1, 1, QImage.Format_RGB32)
        source.fill(QColor("#123456"))
        frame = image.frame_from_image(source)
        self.assertEqual(len(frame.pixels), matrix.LEDS)
        self.assertEqual(set(frame.pixels), {0x123456})


@requires_qt
class Loading(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(self.enterContext(
            __import__("tempfile").TemporaryDirectory())) / "tiny.gif"
        self.path.write_bytes(TINY_GIF)

    def test_an_animation_becomes_one_frame_per_image(self):
        got = image.load(str(self.path))
        self.assertEqual(len(got.frames), 2)
        self.assertEqual(got.available, 2)
        self.assertEqual(got.size, (2, 1))
        self.assertEqual(got.delay_ms, 100)
        self.assertTrue(got.animated)

    def test_the_colours_are_the_ones_in_the_file(self):
        red, blue = image.load(str(self.path)).frames
        self.assertEqual(set(red.pixels), {0xFF0000})
        self.assertEqual(set(blue.pixels), {0x0000FF})

    def test_stride_keeps_every_nth_frame_starting_at_the_first(self):
        got = image.load(str(self.path), stride=2)
        self.assertEqual(len(got.frames), 1)
        # still reports what the file holds, not what was kept
        self.assertEqual(got.available, 2)
        self.assertEqual(set(got.frames[0].pixels), {0xFF0000})

    def test_a_limit_caps_the_frames(self):
        got = image.load(str(self.path), limit=1)
        self.assertEqual(len(got.frames), 1)
        self.assertEqual(got.available, 2)

    def test_the_keyboards_cap_is_never_exceeded(self):
        got = image.load(str(self.path), limit=10_000)
        self.assertLessEqual(len(got.frames), matrix.MAX_FRAMES_ANIMATION)

    def test_a_nonsense_stride_is_refused(self):
        with self.assertRaises(ValueError):
            image.load(str(self.path), stride=0)

    def test_something_that_is_not_an_image_is_refused(self):
        bad = self.path.with_name("notes.txt")
        bad.write_text("this is not a picture")
        with self.assertRaises(ValueError):
            image.load(str(bad))

    def test_frames_go_to_the_wire_as_a_normal_animation(self):
        got = image.load(str(self.path))
        payload = matrix.Animation(frames=got.frames).payload()
        self.assertEqual(payload[:2], b"\x02\x00")        # two frames
        self.assertEqual(payload[4:7], bytes((0xFF, 0x00, 0x00)))


if __name__ == "__main__":
    unittest.main()
