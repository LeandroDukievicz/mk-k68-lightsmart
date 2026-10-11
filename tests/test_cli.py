"""Checks the bring-up commands, which are how the hardware gets asked.

These exercise the paths that build a payload, never one that opens the
device: a test that needs a keyboard plugged in is a test nobody runs.
"""

from __future__ import annotations

import io
import sys
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mk_k68_lightsmart import cli, matrix


class DiagnosticPattern(unittest.TestCase):
    def test_marks_the_first_led_white(self):
        # The one pixel that says where the firmware thinks (0,0) is.
        self.assertEqual(cli.test_frame()[0, 0], 0xFFFFFF)

    def test_each_row_carries_its_own_colour(self):
        frame = cli.test_frame()
        for row, base in enumerate(cli.ROW_COLOURS):
            # Read the brightest column, where the ramp is at full scale.
            self.assertEqual(frame[matrix.COLS - 1, row], base,
                             f"row {row} is not {base:#08x} at full scale")

    def test_brightness_ramps_along_the_columns(self):
        frame = cli.test_frame()
        values = [frame[col, 1] >> 8 & 0xFF for col in range(matrix.COLS)]
        self.assertEqual(values, sorted(values))
        self.assertLess(values[0], values[-1])

    def test_fill_covers_every_led(self):
        frame = cli.fill_frame(0x102030)
        self.assertEqual(set(frame.pixels), {0x102030})
        self.assertEqual(len(frame.pixels), matrix.LEDS)


class WirePayload(unittest.TestCase):
    def test_a_filled_frame_goes_out_red_green_blue(self):
        payload = matrix.Animation(frames=[cli.fill_frame(0x102030)]).payload()
        # Four header bytes -- count then speed -- and then the pixels.
        self.assertEqual(payload[:4], b"\x01\x00\x50\x00")
        self.assertEqual(payload[4:10], bytes([0x10, 0x20, 0x30] * 2))

    def test_one_frame_is_sixteen_blocks(self):
        animation = matrix.Animation(frames=[cli.test_frame()])
        self.assertEqual(animation.block_count(), 16)


class CommandLine(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_a_dry_run_describes_the_upload_without_a_device(self):
        code, out = self.run_cli(["strip", "test", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("16 blocks", out)
        self.assertIn("nothing sent", out)
        # The white corner LED leads the payload, B G R.
        self.assertIn("FF FF FF", out)

    def test_a_dry_run_names_the_boot_slot_and_its_command(self):
        code, out = self.run_cli(["strip", "clear", "--boot", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("boot animation", out)
        self.assertIn("0x31", out)

    def test_fill_rejects_a_colour_that_is_not_a_colour(self):
        code, _ = self.run_cli(["strip", "fill", "nope", "--dry-run"])
        self.assertEqual(code, 2)

    def test_effect_scan_refuses_when_nothing_can_answer_it(self):
        with unittest.mock.patch.object(sys.stdin, "isatty", lambda: False):
            code, _ = self.run_cli(["effect-scan"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()


class ProbePattern(unittest.TestCase):
    """The pattern that separates a row swap from a channel swap."""

    def test_left_half_alternates_colours_a_channel_swap_cannot_touch(self):
        frame = cli.probe_frame()
        rows = [frame[0, row] for row in range(matrix.ROWS)]
        self.assertEqual(rows, [0xFF0000, 0xFFFFFF, 0xFF0000, 0xFFFFFF, 0xFF0000])
        for colour in rows:
            swapped = (colour & 0xFF0000) | (colour & 0xFF) << 8 | (colour >> 8) & 0xFF
            self.assertEqual(colour, swapped, "a G/B swap would change this row")

    def test_right_half_is_green_ramping_to_the_right_edge(self):
        frame = cli.probe_frame()
        self.assertEqual(frame[matrix.COLS - 1, 0], 0x00FF00)
        values = [frame[col, 0] >> 8 & 0xFF for col in range(33, matrix.COLS)]
        self.assertEqual(values, sorted(values))
        self.assertLess(values[0], values[-1])

    def test_the_halves_are_split_by_a_dark_column(self):
        self.assertEqual(cli.probe_frame()[32, 0], 0x000000)
