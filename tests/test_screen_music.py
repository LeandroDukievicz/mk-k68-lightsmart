"""Checks the music screen: the band maths, the mapping, the fallback.

Same shape as ``test_screens.py`` -- a QApplication but no screen -- and
with one rule of its own: nothing here may need a working sound card.
Audio is injected as plain lists of floats, so the arithmetic that turns
sound into colours is tested on a build machine exactly as it is on a
desktop, and the capture itself is tested only for the thing that has to
work when there is no recorder at all, which is degrading quietly.

The screen carries the app's scheme list now, so it also has a store,
and every test here runs against one on a temporary path -- the real
config file is nobody's fixture.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QColor, QShowEvent
    from PySide6.QtWidgets import QApplication, QLabel
except ImportError:                                   # pragma: no cover
    QApplication = None

from mk_k68_lightsmart import layout as kb_layout
from mk_k68_lightsmart import store

if QApplication is not None:
    from mk_k68_lightsmart import protocol, screens
    from mk_k68_lightsmart.screens import music

app = None
#: Held in a module global so it outlives every test in the file and is
#: cleaned up when the interpreter exits, not between tests.
sandbox = None


def setUpModule() -> None:
    global app, sandbox
    if QApplication is not None:
        app = QApplication.instance() or QApplication([])
    sandbox = tempfile.TemporaryDirectory()
    store.use(store.Store(path=Path(sandbox.name) / "schemes.json"))


def tearDownModule() -> None:
    # One store serves the whole process, and the pages built in
    # tests/test_screens.py take it without installing one of their own.
    # What is left installed is therefore what they will edit and save,
    # so it is empty and on a temporary path -- a first run's store,
    # which is what they expect.
    store.use(store.Store(path=Path(sandbox.name) / "left-behind.json"))


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

    def test_every_ramp_in_the_dropdown_is_read_by_level(self):
        # The vendor's modes are named colour ramps, so each one has to
        # have three stops that differ and arrive in level order --
        # otherwise the name on the dropdown says nothing.
        for index, (name, _stops) in enumerate(music.RAMPS):
            seen = [music.band_colour(index, 0, 24, level, self.base)
                    for level in (0.1, 0.7, 0.95)]
            self.assertEqual(len(set(c.name() for c in seen)), 3, name)

    def test_a_ramp_holds_its_colour_between_the_stops(self):
        for index in range(len(music.RAMPS)):
            below = music.band_colour(index, 0, 24, music.RAMP_MID - 0.01,
                                      self.base)
            at = music.band_colour(index, 0, 24, music.RAMP_MID, self.base)
            just_over = music.band_colour(index, 0, 24,
                                          music.RAMP_MID + 0.01, self.base)
            self.assertNotEqual(below, at)
            self.assertEqual(at, just_over)

    def test_every_mode_the_dropdown_offers_draws_something(self):
        # Including the two that are not ramps, which is the whole list
        # the screen's combo is filled from.
        for index in range(len(music.MODES)):
            colour = music.band_colour(index, 3, 24, 0.5, self.base)
            self.assertTrue(colour.isValid(), music.MODES[index])

    def test_a_mode_index_from_nowhere_still_draws(self):
        # A stored index, or one left over from a shorter list: the
        # first ramp is what the screen opens on anyway, and refusing
        # to draw would take the screen down with it.
        self.assertEqual(music.band_colour(99, 0, 24, 0.1, self.base),
                         music.METER_GREEN)

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


class FakeCapture:
    """A recorder that hands over one frame and stays alive.

    The page asks a capture for three things, so that is all this is.
    It keeps the suite hermetic: the audio is a list of floats written
    by a test, not a sound card nobody's build machine has.
    """

    def __init__(self, block, rate: int = 48000):
        self.rate = rate
        self._block = block

    def alive(self) -> bool:
        return True

    def latest(self):
        block, self._block = self._block, None
        return block

    def stop(self) -> None:
        pass


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class ScreenFixture(unittest.TestCase):
    """A page over a store that lives in a temporary directory."""

    def setUp(self):
        self.path = Path(sandbox.name) / f"{self.id()}.json"
        store.use(store.Store(path=self.path))
        self.page = screens.MusicPage()
        self.said: list[str] = []
        self.page.status.connect(self.said.append)

    def tearDown(self):
        self.page._stop()

    def saved(self) -> store.Store:
        """The store as the next start of the app would read it."""
        return store.Store.load(self.path)

    def naming(self, name: str):
        """Answer the name dialog an add or a rename puts up."""
        return mock.patch.object(
            music.QInputDialog, "getText",
            staticmethod(lambda *a, **k: (name, True)))

    def answering(self, yes: bool):
        """Answer the confirmation a destructive action asks for."""
        reply = music.QMessageBox.Yes if yes else music.QMessageBox.No
        return mock.patch.object(
            music.QMessageBox, "question",
            staticmethod(lambda *a, **k: reply))

    def rows(self) -> list[str]:
        return [self.page.schemes.item(n).text()
                for n in range(self.page.schemes.count())]


class TheSchemeList(ScreenFixture):
    """The vendor draws its scheme list on this screen too, and the
    list is the app's one list -- so choosing here is switching."""

    def test_it_shows_every_scheme_and_marks_the_one_in_use(self):
        store.shared().add("Night")
        self.page._refresh_schemes()
        self.assertEqual(len(self.rows()), 2)
        self.assertIn("Default", self.rows()[0])
        self.assertTrue(self.rows()[0].startswith("●"))
        self.assertTrue(self.rows()[1].startswith("○"))

    def test_choosing_a_row_is_using_that_scheme(self):
        # The vendor's list has no second step: the dot marks the one in
        # use and the list is how one is chosen.
        store.shared().add("Night")
        self.page._refresh_schemes()
        switched = []
        self.page.scheme_changed.connect(switched.append)
        self.page.schemes.setCurrentRow(1)
        self.assertEqual(store.shared().active, 1)
        self.assertEqual(switched[-1].name, "Night")
        self.assertIn("Night", self.said[-1])
        self.assertEqual(self.saved().active, 1)

    def test_adding_a_scheme_makes_it_the_one_in_use(self):
        with self.naming("Night"):
            self.page._add_scheme()
        self.assertEqual(store.shared().names(), ["Default", "Night"])
        self.assertEqual(store.shared().current.name, "Night")
        self.assertEqual(self.saved().names(), ["Default", "Night"])

    def test_adding_says_so_when_the_name_was_taken(self):
        # The store renames round a collision rather than refusing one,
        # which is right for adding and the wrong thing to do quietly.
        with self.naming("Default"):
            self.page._add_scheme()
        self.assertEqual(store.shared().names(), ["Default", "Default 2"])
        self.assertIn("was taken", self.said[-1])

    def test_a_name_nobody_typed_adds_nothing(self):
        with mock.patch.object(music.QInputDialog, "getText",
                               staticmethod(lambda *a, **k: ("", False))):
            self.page._add_scheme()
        self.assertEqual(store.shared().names(), ["Default"])

    def test_renaming_renames_the_scheme_and_the_row(self):
        with self.naming("Night"):
            self.page._rename_scheme()
        self.assertEqual(store.shared().names(), ["Night"])
        self.assertIn("Night", self.rows()[0])
        self.assertEqual(self.saved().names(), ["Night"])

    def test_renaming_refuses_a_name_already_in_the_list(self):
        store.shared().add("Night")
        self.page._refresh_schemes()
        with self.naming("Night"):
            self.page._rename_scheme()
        self.assertEqual(store.shared().names(), ["Default", "Night"])
        self.assertIn("already a scheme", self.said[-1])

    def test_deleting_drops_the_scheme_and_moves_to_another(self):
        store.shared().add("Night")
        self.page._refresh_schemes()
        self.page.schemes.setCurrentRow(1)
        self.page._delete_scheme()
        self.assertEqual(store.shared().names(), ["Default"])
        self.assertEqual(store.shared().current.name, "Default")
        self.assertEqual(self.saved().names(), ["Default"])

    def test_deleting_asks_first_when_the_scheme_holds_something(self):
        # The keyboard keeps no copy of a scheme, so the file is the
        # only one there is.
        store.shared().add("Night")
        store.shared().schemes[1].lighting = {1: 0xFF0000}
        self.page._refresh_schemes()
        self.page.schemes.setCurrentRow(1)
        with self.answering(False):
            self.page._delete_scheme()
        self.assertEqual(store.shared().names(), ["Default", "Night"])
        with self.answering(True):
            self.page._delete_scheme()
        self.assertEqual(store.shared().names(), ["Default"])

    def test_the_last_scheme_cannot_be_deleted(self):
        self.page._delete_scheme()
        self.assertEqual(store.shared().names(), ["Default"])
        self.assertIn("has to be one scheme", self.said[-1])

    def test_a_scheme_added_elsewhere_shows_when_the_screen_comes_up(self):
        store.shared().add("Night")
        self.page.showEvent(QShowEvent())
        self.assertEqual(len(self.rows()), 2)

    def test_a_save_that_fails_is_said_rather_than_raised(self):
        with mock.patch.object(store.Store, "save",
                               side_effect=OSError("read-only")):
            with self.naming("Night"):
                self.page._add_scheme()
        self.assertTrue(any("could not save" in said for said in self.said))


class TheSettingsPanel(ScreenFixture):
    """The vendor's bottom panel: two dropdowns and two sliders."""

    def test_the_mode_dropdown_offers_every_mode(self):
        self.assertEqual(
            [self.page.mode.itemText(n)
             for n in range(self.page.mode.count())], list(music.MODES))
        # The vendor's own visible value is the first of them.
        self.assertEqual(self.page.mode.itemText(music.GREEN_AMBER_RED),
                         "Green / yellow / red")

    def test_most_of_the_modes_are_named_colour_ramps(self):
        self.assertGreater(len(music.RAMPS), 1)
        for index, (name, _stops) in enumerate(music.RAMPS):
            self.assertEqual(self.page.mode.itemText(index), name)

    def test_choosing_a_mode_recolours_the_meter_as_well(self):
        # The meter and the board are driven from the same numbers, so
        # they must not disagree about what a level looks like.
        self.page.mode.setCurrentIndex(music.SPECTRUM)
        self.assertEqual(self.page.meter.scheme, music.SPECTRUM)

    def test_the_background_dropdown_offers_none_first(self):
        self.assertEqual(
            [self.page.background.itemText(n)
             for n in range(self.page.background.count())],
            list(music.BACKGROUNDS))
        self.assertEqual(self.page.background.itemText(music.NOTHING),
                         "None")

    def test_the_amplitude_reads_out_as_a_plain_number(self):
        self.page.amplitude.setValue(38)
        self.assertEqual(self.page.amplitude_value.text(), "38")

    def test_the_background_brightness_reads_out_as_a_percentage(self):
        self.page.back_bright.setValue(100)
        self.assertEqual(self.page.back_bright_value.text(), "100%")

    def test_the_amplitude_slider_is_the_gain_on_the_levels(self):
        quiet = self._level_at(10)
        loud = self._level_at(100)
        self.assertGreater(loud, quiet)
        self.assertAlmostEqual(music.gain_from_amplitude(50), 1.0)

    def _level_at(self, amplitude: int) -> float:
        """The loudest band of one quiet frame, at that amplitude."""
        self.page.amplitude.setValue(amplitude)
        self.page._smooth.clear()
        self.page._capture = FakeCapture(tone(1000.0, 0.05))
        levels = self.page._listen()
        self.page._capture = None
        return max(levels)

    def test_the_background_brightness_dims_the_unlit_keys(self):
        self.page.background.setCurrentIndex(music.DIM_STATIC)
        self.page.back_bright.setValue(100)
        self.page._paint([0.0] * music.BANDS)
        bright = max(c.value() for c in self.page._wire.values())
        self.page.back_bright.setValue(20)
        self.page._paint([0.0] * music.BANDS)
        dim = max(c.value() for c in self.page._wire.values())
        self.assertGreater(bright, dim)

    def test_the_preview_is_the_board_the_music_lights(self):
        levels = [0.0] * music.BANDS
        levels[0] = 1.0
        self.page._paint(levels)
        self.assertTrue(self.page.board.colours)
        self.assertEqual(set(self.page.board.colours), self.page._lit)

    def test_a_key_on_the_preview_says_which_band_it_stands_in(self):
        said = self.page.board.hover_text(kb_layout.BY_IDENT["G"])
        self.assertIn("band", said)
        self.assertIn("Hz", said)

    def test_the_screen_states_the_manual_s_conditions(self):
        # Wired, playing, and not at the same time as the strip: all
        # three are the manual's, and all three are invisible otherwise.
        words = " ".join(
            label.text() for label in self.page.findChildren(QLabel)).lower()
        self.assertIn("wired mode", words)
        self.assertIn("playing", words)
        self.assertIn("mutually exclusive", words)

    def test_starting_says_the_strip_gives_way(self):
        with mock.patch.object(music.Capture, "start", lambda self: True):
            self.page._start()
        self.assertIn("strip", self.said[-1])


class SettingsFollowTheScheme(ScreenFixture):
    """A scheme is what the vendor hangs these settings off, and the
    list switches between them -- for as long as the app is open."""

    def test_each_scheme_keeps_its_own_settings(self):
        self.page.amplitude.setValue(80)
        self.page.mode.setCurrentIndex(music.SPECTRUM)
        with self.naming("Night"):
            self.page._add_scheme()
        # A new scheme opens on the defaults, not on the last one's.
        self.assertEqual(self.page.amplitude.value(),
                         music.MusicSettings().amplitude)
        self.assertEqual(self.page.mode.currentIndex(),
                         music.MusicSettings().mode)
        self.page.schemes.setCurrentRow(0)
        self.assertEqual(self.page.amplitude.value(), 80)
        self.assertEqual(self.page.mode.currentIndex(), music.SPECTRUM)

    def test_loading_a_scheme_does_not_write_to_the_one_left_behind(self):
        self.page.back_bright.setValue(70)
        with self.naming("Night"):
            self.page._add_scheme()
        self.page.back_bright.setValue(5)
        self.page.schemes.setCurrentRow(0)
        self.assertEqual(self.page.back_bright.value(), 70)

    def test_the_settings_move_with_a_renamed_scheme(self):
        self.page.amplitude.setValue(33)
        with self.naming("Night"):
            self.page._rename_scheme()
        self.assertEqual(self.page.amplitude.value(), 33)
        self.assertIn("Night", self.page._settings)

    def test_a_deleted_scheme_takes_its_settings_with_it(self):
        with self.naming("Night"):
            self.page._add_scheme()
        self.page.amplitude.setValue(21)
        self.page._delete_scheme()
        self.assertNotIn("Night", self.page._settings)

    def test_the_colour_in_hand_belongs_to_the_scheme_too(self):
        self.page._colour = QColor("#123456")
        self.page._keep_settings()
        with self.naming("Night"):
            self.page._add_scheme()
        self.assertEqual(self.page._colour.name(),
                         music.MusicSettings().colour.name())
        self.page.schemes.setCurrentRow(0)
        self.assertEqual(self.page._colour.name(), "#123456")


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class ApplyHasToOutlastTheNextTick(unittest.TestCase):
    """Apply lights the whole board, so the bars have to give way.

    Resting lit is one effect command; the bars are per-key writes, and
    a per-key write drops the board off the effect layer. So while the
    listener ran, Apply used to light the board and the next tick with
    sound in it took it straight back about 1.2 seconds later -- a
    blink, and a button that read as broken. Seen on the wire as one
    mode-0x01 envelope followed by per-key 0x80 envelopes 1.576s later.
    """

    def setUp(self):
        self.page = screens.MusicPage()
        self.effects: list[tuple] = []
        self.keys: list[int] = []
        self.page.apply_effect.connect(lambda *a: self.effects.append(a))
        self.page.apply_key.connect(lambda i, c: self.keys.append(i))

    def tearDown(self):
        self.page._capture = None
        self.page._stop()

    def listening(self) -> None:
        """The screen as it stands while the listener runs."""
        self.page._capture = FakeCapture([0.9] * 64)
        self.page._timer.start(self.page.INTERVAL)
        self.page._sender.start(music.SEND_INTERVAL)

    def test_apply_lights_the_board_with_one_effect(self):
        self.page._apply()
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(self.effects[0][0], protocol.effect_mode("Static"))

    def test_apply_stops_the_listener_that_would_paint_over_it(self):
        self.listening()
        self.page._apply()
        self.assertFalse(self.page._timer.isActive())
        self.assertFalse(self.page._sender.isActive())
        self.assertIsNone(self.page._capture)

    def test_no_per_key_write_survives_an_apply(self):
        self.listening()
        self.page._apply()
        # What used to undo it: a tick that finds sound clears the
        # resting flag, and the sender then paints the bars per key.
        self.page._tick()
        self.page._send()
        self.assertEqual(self.keys, [])
        self.assertTrue(self.page._resting)


if __name__ == "__main__":
    unittest.main()
