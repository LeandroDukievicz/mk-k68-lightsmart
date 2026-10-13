"""Checks the music screen: the band maths, the mapping, the fallback.

Same shape as ``test_screens.py`` -- a QApplication but no screen -- and
with one rule of its own: nothing here may need a working sound card.
Audio is injected as plain lists of floats, so the arithmetic that turns
sound into colours is tested on a build machine exactly as it is on a
desktop, and the capture itself is tested only for the thing that has to
work when there is no recorder at all, which is degrading quietly.
"""

from __future__ import annotations

import math
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    QApplication = None

from mk_k68_lightsmart import layout as kb_layout

if QApplication is not None:
    from mk_k68_lightsmart import screens
    from mk_k68_lightsmart.screens import music

app = None


def setUpModule() -> None:
    global app
    if QApplication is not None:
        app = QApplication.instance() or QApplication([])


def tone(hz: float, amplitude: float = 1.0,
         rate: int = 48000, size: int = 1024) -> list[float]:
    """One frame of a pure tone, as the recorder would have decoded it."""
    return [amplitude * math.sin(2 * math.pi * hz * i / rate)
            for i in range(size)]


def loudest(values: list[float]) -> int:
    return values.index(max(values))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class BandMaths(unittest.TestCase):
    def setUp(self):
        self.edges = music.band_edges()

    def test_the_edges_are_log_spaced(self):
        # Every band the same ratio wide, which is what makes a bar per
        # third of an octave rather than a bar per 500 Hz.
        ratios = [b / a for a, b in zip(self.edges, self.edges[1:])]
        for ratio in ratios:
            self.assertAlmostEqual(ratio, ratios[0], places=9)

    def test_the_edges_span_the_range_asked_for(self):
        self.assertEqual(len(self.edges), music.BANDS + 1)
        self.assertAlmostEqual(self.edges[0], music.LOW_HZ)
        self.assertAlmostEqual(self.edges[-1], music.HIGH_HZ)

    def test_a_tone_lands_in_the_band_that_contains_it(self):
        for hz in (250.0, 500.0, 1000.0, 2500.0, 5000.0):
            want = max(i for i, low in enumerate(self.edges[:-1])
                       if low <= hz)
            got = loudest(music.spectrum_bands(tone(hz), 48000, self.edges))
            self.assertEqual(got, want, f"{hz} Hz")

    def test_a_full_scale_tone_reads_about_full_scale(self):
        # The whole decibel stage downstream assumes this normalisation,
        # so it is pinned here rather than left to drift with the window.
        bands = music.spectrum_bands(tone(1000.0), 48000, self.edges)
        self.assertAlmostEqual(max(bands), 1.0, delta=0.1)

    def test_a_quiet_tone_reads_quiet(self):
        loud = max(music.spectrum_bands(tone(1000.0), 48000, self.edges))
        soft = max(music.spectrum_bands(tone(1000.0, 0.1), 48000,
                                        self.edges))
        self.assertAlmostEqual(soft / loud, 0.1, delta=0.02)

    def test_silence_is_silent(self):
        bands = music.spectrum_bands([0.0] * 1024, 48000, self.edges)
        self.assertEqual(max(bands), 0.0)

    def test_an_empty_frame_still_gives_a_band_per_band(self):
        self.assertEqual(len(music.spectrum_bands([], 48000, self.edges)),
                         music.BANDS)

    def test_the_bottom_bands_are_not_dead(self):
        # They are narrower than one FFT bin, so the obvious
        # "which bins fall inside this band" gives them nothing at all
        # and the bass never lights. They read the nearest bin instead.
        bands = music.spectrum_bands(tone(45.0), 48000, self.edges)
        self.assertGreater(bands[0], 0.5)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TheFallbackFilterBank(unittest.TestCase):
    """The path taken where numpy is not installed.

    It is exercised directly rather than by hiding numpy, because what
    matters is that it agrees with the FFT about where a note is, not
    how the import failed.
    """

    def setUp(self):
        self.edges = music.band_edges()

    def test_it_finds_a_tone_in_the_same_band_the_fft_does(self):
        for hz in (100.0, 500.0, 1000.0, 2500.0, 5000.0, 9000.0):
            fft = loudest(music._fft_bands(tone(hz), 48000, self.edges))
            bank = loudest(music._goertzel_bands(tone(hz), 48000,
                                                 self.edges))
            # Within one band: the two disagree only at the bottom,
            # where neither can resolve a third of an octave anyway.
            self.assertLessEqual(abs(fft - bank), 1, f"{hz} Hz")

    def test_it_reads_a_full_scale_tone_as_about_full_scale(self):
        for hz in (500.0, 5000.0):
            bands = music._goertzel_bands(tone(hz), 48000, self.edges)
            self.assertAlmostEqual(max(bands), 1.0, delta=0.3, msg=f"{hz}")

    def test_silence_is_silent(self):
        bands = music._goertzel_bands([0.0] * 1024, 48000, self.edges)
        self.assertAlmostEqual(max(bands), 0.0, places=9)

    def test_an_empty_frame_still_gives_a_band_per_band(self):
        self.assertEqual(
            len(music._goertzel_bands([], 48000, self.edges)), music.BANDS)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Levels(unittest.TestCase):
    def test_full_scale_is_the_top_of_the_meter(self):
        self.assertAlmostEqual(music.levels_from_bands([1.0])[0], 1.0,
                               places=6)

    def test_silence_is_the_bottom_of_the_meter(self):
        self.assertEqual(music.levels_from_bands([0.0])[0], 0.0)

    def test_the_floor_is_where_the_meter_starts(self):
        at_floor = 10.0 ** (music.FLOOR_DB / 20.0)
        self.assertAlmostEqual(music.levels_from_bands([at_floor])[0], 0.0,
                               places=6)

    def test_it_is_decibels_not_magnitude(self):
        # Half the amplitude is -6 dB, which is a tenth of the meter,
        # not half of it. A linear meter would give 0.5 here and show
        # everything but the loudest band as nothing.
        self.assertAlmostEqual(music.levels_from_bands([0.5])[0],
                               1.0 - 6.02 / -music.FLOOR_DB, places=2)

    def test_amplitude_is_a_gain_centred_on_fifty(self):
        self.assertAlmostEqual(music.gain_from_amplitude(50), 1.0)
        self.assertGreater(music.gain_from_amplitude(100), 1.0)
        self.assertLess(music.gain_from_amplitude(1), 1.0)

    def test_gain_cannot_push_a_level_past_the_top(self):
        self.assertEqual(music.levels_from_bands([1.0], gain=50.0)[0], 1.0)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Smoothing(unittest.TestCase):
    def test_it_rises_faster_than_it_falls(self):
        # The whole point: a beat has to arrive on time, but the fall is
        # what stops the board strobing between frames.
        up = music.Smoother(1)
        up.push([1.0])
        down = music.Smoother(1)
        down.values = [1.0]
        down.push([0.0])
        self.assertGreater(up.values[0], 1.0 - down.values[0])

    def test_it_converges_on_what_it_is_fed(self):
        smooth = music.Smoother(1)
        for _ in range(60):
            smooth.push([0.75])
        self.assertAlmostEqual(smooth.values[0], 0.75, places=3)

    def test_it_stays_between_nothing_and_everything(self):
        smooth = music.Smoother(2)
        smooth.push([5.0, -5.0])
        self.assertEqual([round(v, 6) for v in smooth.values], [1.0, 0.0])

    def test_clearing_puts_every_band_back_to_nothing(self):
        smooth = music.Smoother(3)
        smooth.push([1.0, 1.0, 1.0])
        smooth.clear()
        self.assertEqual(smooth.values, [0.0, 0.0, 0.0])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class MappingOntoTheBoard(unittest.TestCase):
    def test_every_key_lands_in_a_band_that_exists(self):
        for key in kb_layout.KEYS:
            band = music.key_band(key, music.BANDS)
            self.assertTrue(0 <= band < music.BANDS, key.ident)

    def test_bands_run_left_to_right(self):
        # By centre, not by x: keys share an x but not a width, so the
        # space bar's left edge sits with the Z row's.
        ordered = sorted(kb_layout.KEYS, key=lambda k: k.x + k.w / 2.0)
        bands = [music.key_band(k, music.BANDS) for k in ordered]
        self.assertEqual(bands, sorted(bands))

    def test_every_band_reaches_at_least_one_key(self):
        # Spread across the artwork canvas instead of across the keys,
        # the outermost bands land in the margins and the bass band
        # never lights anything.
        used = {music.key_band(k, music.BANDS) for k in kb_layout.KEYS}
        self.assertEqual(used, set(range(music.BANDS)))

    def test_the_ends_of_the_board_are_the_ends_of_the_spectrum(self):
        left = min(kb_layout.KEYS, key=lambda k: k.x + k.w / 2.0)
        right = max(kb_layout.KEYS, key=lambda k: k.x + k.w / 2.0)
        self.assertEqual(music.key_band(left, music.BANDS), 0)
        self.assertEqual(music.key_band(right, music.BANDS),
                         music.BANDS - 1)

    def test_the_rows_come_off_the_geometry_bottom_first(self):
        rows = music.key_rows()
        self.assertEqual(rows, sorted(rows, reverse=True))
        self.assertEqual(set(rows), {k.y for k in kb_layout.KEYS})
        # SWITCH sits alone above the number row, so the board has one
        # more row than the five everybody counts.
        self.assertEqual(len(rows), 6)

    def test_a_bar_fills_its_column_from_the_bottom(self):
        rows = 6
        self.assertEqual(music.key_brightness(1.0, 0, rows), 1.0)
        self.assertEqual(music.key_brightness(1.0, rows - 1, rows), 1.0)
        # Half way up: the bottom rows are full, the top rows are dark.
        self.assertEqual(music.key_brightness(0.5, 0, rows), 1.0)
        self.assertEqual(music.key_brightness(0.5, rows - 1, rows), 0.0)

    def test_the_top_of_a_bar_is_part_lit(self):
        # Without this a six-row board can only show six loudnesses.
        self.assertAlmostEqual(music.key_brightness(0.55, 3, 6), 0.3,
                               places=6)

    def test_silence_lights_nothing(self):
        for row in range(6):
            self.assertEqual(music.key_brightness(0.0, row, 6), 0.0)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Colours(unittest.TestCase):
    def setUp(self):
        self.base = QColor("#102030")

    def test_the_meter_scheme_runs_green_amber_red_with_level(self):
        scheme = music.GREEN_AMBER_RED
        self.assertEqual(music.band_colour(scheme, 0, 24, 0.1, self.base),
                         music.METER_GREEN)
        self.assertEqual(music.band_colour(scheme, 0, 24, 0.7, self.base),
                         music.METER_AMBER)
        self.assertEqual(music.band_colour(scheme, 0, 24, 0.95, self.base),
                         music.METER_RED)

    def test_the_single_colour_scheme_ignores_band_and_level(self):
        first = music.band_colour(music.SINGLE_COLOUR, 0, 24, 0.1,
                                  self.base)
        second = music.band_colour(music.SINGLE_COLOUR, 23, 24, 0.9,
                                   self.base)
        self.assertEqual(first, self.base)
        self.assertEqual(second, self.base)

    def test_the_spectrum_scheme_follows_the_band_not_the_level(self):
        quiet = music.band_colour(music.SPECTRUM, 5, 24, 0.1, self.base)
        loud = music.band_colour(music.SPECTRUM, 5, 24, 0.9, self.base)
        other = music.band_colour(music.SPECTRUM, 18, 24, 0.1, self.base)
        self.assertEqual(quiet, loud)
        self.assertNotEqual(quiet, other)

    def test_dimming_scales_towards_black(self):
        self.assertEqual(music.dim(QColor(200, 100, 50), 0.5),
                         QColor(100, 50, 25))
        self.assertEqual(music.dim(QColor(200, 100, 50), 0.0),
                         QColor(0, 0, 0))

    def test_dimming_clamps_rather_than_overflowing(self):
        self.assertEqual(music.dim(QColor(200, 100, 50), 9.0),
                         QColor(200, 100, 50))

    def test_the_gap_is_the_worst_channel(self):
        self.assertEqual(
            music.colour_gap(QColor(0, 0, 0), QColor(10, 200, 5)), 200)
        self.assertEqual(
            music.colour_gap(QColor(7, 7, 7), QColor(7, 7, 7)), 0)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class CaptureWithoutASoundCard(unittest.TestCase):
    """What has to hold when there is nothing to record."""

    def test_no_recorder_on_path_is_reported_not_raised(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(music.capture_command())

    def test_starting_without_a_recorder_fails_quietly(self):
        capture = music.Capture()
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(capture.start())
        self.assertIn("recorder", capture.reason)
        self.assertFalse(capture.alive())

    def test_stopping_something_never_started_is_harmless(self):
        music.Capture().stop()

    def test_the_chosen_command_records_a_monitor_not_a_microphone(self):
        # The one thing that makes this screen follow the music rather
        # than the room. Easy to break by tidying the arguments.
        with mock.patch("shutil.which",
                        side_effect=lambda n: "/usr/bin/" + n
                        if n == "parec" else None):
            name, argv = music.capture_command()
        self.assertEqual(name, "parec")
        self.assertIn("--device=@DEFAULT_SINK@.monitor", argv)

        with mock.patch("shutil.which",
                        side_effect=lambda n: "/usr/bin/" + n
                        if n == "pw-record" else None):
            name, argv = music.capture_command()
        self.assertEqual(name, "pw-record")
        self.assertIn("stream.capture.sink=true", argv)

    def test_the_queue_keeps_the_newest_frame_and_drops_the_rest(self):
        # A visualiser behind by half a second is worse than one that
        # skipped: the backlog is stale by definition.
        capture = music.Capture()
        for n in range(capture.BACKLOG + 5):
            capture._offer([float(n)])
        self.assertEqual(capture.latest(), [float(capture.BACKLOG + 4)])
        self.assertIsNone(capture.latest())

    def test_frames_decode_to_floats_either_side_of_zero(self):
        raw = (0).to_bytes(2, "little", signed=True) \
            + (16384).to_bytes(2, "little", signed=True) \
            + (-32768).to_bytes(2, "little", signed=True)
        got = [round(float(v), 4) for v in music.decode(raw)]
        self.assertEqual(got, [0.0, 0.5, -1.0])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TheScreen(unittest.TestCase):
    def setUp(self):
        self.page = screens.MusicPage()

    def tearDown(self):
        self.page._stop()

    def test_it_builds_with_no_arguments(self):
        self.assertTrue(self.page.layout().count() > 0)

    def test_it_offers_the_signals_the_window_wires(self):
        self.assertTrue(hasattr(self.page, "apply_key"))
        self.assertTrue(hasattr(self.page, "status"))

    def test_with_no_recorder_it_runs_a_stand_in_and_says_so(self):
        said = []
        self.page.status.connect(said.append)
        with mock.patch("shutil.which", return_value=None):
            self.page._start()
        self.assertIsNone(self.page._capture)
        self.assertTrue(self.page._timer.isActive())
        self.assertIn("stand-in", said[-1])

    def test_the_stand_in_never_lights_a_real_key(self):
        # It would look exactly like working audio, which is the one
        # impression this screen must not give.
        sent = []
        self.page.apply_key.connect(lambda i, c: sent.append(i))
        with mock.patch("shutil.which", return_value=None):
            self.page._start()
        self.page._tick()
        self.page._send()
        self.assertEqual(sent, [])

    def test_a_loud_frame_lights_keys_across_the_board(self):
        self.page._paint([0.9] * music.BANDS)
        self.assertEqual(len(self.page.board.colours), len(kb_layout.KEYS))

    def test_a_silent_frame_lights_nothing(self):
        self.page.background.setCurrentIndex(music.NOTHING)
        self.page._paint([0.0] * music.BANDS)
        self.assertEqual(self.page.board.colours, {})

    def test_only_the_keys_under_a_band_light_up(self):
        levels = [0.0] * music.BANDS
        levels[0] = 1.0
        self.page._paint(levels)
        lit = set(self.page.board.colours)
        self.assertTrue(lit)
        for key in kb_layout.KEYS:
            if key.light_index in lit:
                self.assertEqual(music.key_band(key, music.BANDS), 0,
                                 key.ident)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class WhatReachesTheKeyboard(unittest.TestCase):
    """The per-key path is slow, so what is sent matters more than how
    much. These pin the rationing rather than the colours."""

    def setUp(self):
        self.page = screens.MusicPage()
        # A stand-in for a running recorder: the sender refuses to run
        # without one, and these tests are about the sender.
        self.page._capture = object()
        self.sent = []
        self.page.apply_key.connect(
            lambda i, c: self.sent.append((i, QColor(c))))

    def tearDown(self):
        self.page._capture = None
        self.page._stop()

    def test_a_frame_never_sends_more_than_the_wire_can_take(self):
        self.page._paint([0.9] * music.BANDS)
        self.page._send()
        self.assertEqual(len(self.sent), music.KEY_BUDGET)

    def test_lit_keys_are_sent_before_background_ones(self):
        # Every key of a fresh board differs by the maximum, and there
        # are far more dark keys than bright ones, so ranking on the
        # difference alone would spend the first second painting black.
        levels = [0.0] * music.BANDS
        levels[10] = 1.0
        self.page.background.setCurrentIndex(music.NOTHING)
        self.page._paint(levels)
        self.page._send()
        self.assertTrue(self.sent)
        for index, colour in self.sent:
            self.assertIn(index, self.page._lit)
            self.assertNotEqual(colour, QColor(0, 0, 0))

    def test_a_key_is_not_sent_twice_for_the_same_colour(self):
        self.page._paint([0.9] * music.BANDS)
        for _ in range(40):
            self.page._send()
        once = [i for i, _ in self.sent]
        self.assertEqual(len(once), len(set(once)))

    def test_nothing_behind_the_bars_blacks_the_rest_out(self):
        # Otherwise a key drops out of a bar and stays lit at whatever
        # it was last told.
        levels = [0.0] * music.BANDS
        levels[10] = 1.0
        self.page.background.setCurrentIndex(music.NOTHING)
        self.page._paint(levels)
        blacks = [i for i, c in self.page._wire.items()
                  if c == QColor(0, 0, 0)]
        self.assertTrue(blacks)

    def test_the_running_effect_is_left_alone_behind_the_bars(self):
        levels = [0.0] * music.BANDS
        levels[10] = 1.0
        self.page.background.setCurrentIndex(music.RUNNING_EFFECT)
        self.page._paint(levels)
        # Unlit keys are not in the wire at all, so whatever effect the
        # keyboard is running stays visible behind the bars.
        self.assertEqual(set(self.page._wire), self.page._lit)

    def test_a_dim_background_is_sent_rather_than_left_dark(self):
        levels = [0.0] * music.BANDS
        self.page.background.setCurrentIndex(music.DIM_STATIC)
        self.page.back_bright.setValue(40)
        self.page._paint(levels)
        self.assertEqual(len(self.page._wire), len(kb_layout.KEYS))
        self.assertEqual(self.page._lit, set())

    def test_stopping_forgets_what_the_keyboard_was_told(self):
        self.page._paint([0.9] * music.BANDS)
        self.page._send()
        self.page._capture = None
        self.page._stop()
        self.assertEqual(self.page._sent, {})
        self.assertEqual(self.page._wire, {})
        self.assertEqual(self.page.board.colours, {})
        self.assertFalse(self.page._timer.isActive())
        self.assertFalse(self.page._sender.isActive())


if __name__ == "__main__":
    unittest.main()
