"""Checks the animations the program generates.

These are art, so most of what matters cannot be asserted. What can is
that the wall actually closes, that every piece is a real tetromino
dropped onto a clear path, and that the rain's last frame leads back
into its first -- the things that would be wrong on the keyboard and
hard to see in a diff.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mk_k68_lightsmart import matrix, patterns


class TheWall(unittest.TestCase):
    def test_the_last_frame_is_every_led(self):
        last = patterns.tetris_fill()[-1]
        dark = [i for i, pixel in enumerate(last.pixels) if not pixel]
        self.assertEqual(dark, [], "the wall has holes")

    @staticmethod
    def pieces():
        return [piece for wave in patterns._wall_waves() for piece in wave]

    def test_the_pieces_cover_the_strip_exactly_once(self):
        seen: dict[tuple[int, int], str] = {}
        for name, cells in self.pieces():
            for cell in cells:
                self.assertNotIn(cell, seen, f"{name} overlaps {seen.get(cell)}")
                seen[cell] = name
        self.assertEqual(len(seen), matrix.LEDS)

    def test_every_piece_is_a_tetromino_but_the_one_that_cannot_be(self):
        # 65 x 5 is 325 cells and a tetromino is four; 325 is not a
        # multiple of 4, so exactly one cell is left for a piece the
        # game does not have.
        sizes = [len(cells) for _, cells in self.pieces()]
        self.assertEqual(sorted(sizes)[0], 1)
        self.assertEqual(sorted(sizes).count(1), 1)
        self.assertTrue(all(n == 4 for n in sorted(sizes)[1:]))

    def test_every_piece_falls_onto_a_clear_path(self):
        """Nothing lands under something already resting.

        Checked per wave, since a wave falls all at once: what has to be
        clear above a piece is whatever the earlier waves left there.
        """
        filled: set[tuple[int, int]] = set()
        for wave in patterns._wall_waves():
            columns: dict[int, str] = {}
            for name, cells in wave:
                tops: dict[int, int] = {}
                for col, row in cells:
                    tops[col] = min(tops.get(col, row), row)
                for col, top in tops.items():
                    # Two pieces in one wave may not share a column
                    # either: they would collide on the way down.
                    self.assertNotIn(col, columns,
                                     f"{name} shares column {col} with "
                                     f"{columns.get(col)} in the same wave")
                    columns[col] = name
                    for row in range(top):
                        self.assertNotIn(
                            (col, row), filled,
                            f"{name} would have to fall through ({col},{row})"
                        )
            for _, cells in wave:
                filled.update(cells)

    def test_the_wall_uses_every_tetromino(self):
        shapes = {name for name, _ in self.pieces() if name != "_"}
        self.assertEqual(shapes, set(patterns.PIECE_COLOURS),
                         "the fills between them should use all seven")

    def test_every_wave_spans_the_whole_strip(self):
        # The point of waves: pieces come down across the strip at once
        # rather than filling it left to right.
        for n, wave in enumerate(patterns._wall_waves()):
            columns = {col for _, cells in wave for col, _ in cells}
            self.assertGreater(max(columns) - min(columns), matrix.COLS - 8,
                               f"wave {n} covers only part of the strip")

    def test_a_slower_pace_is_the_same_fall_with_more_frames(self):
        quick = patterns.tetris_fill(pace=1, hold=0)
        slow = patterns.tetris_fill(pace=2, hold=0)
        self.assertEqual(len(slow), 2 * len(quick))
        self.assertEqual(list(slow[-1].pixels), list(quick[-1].pixels))

    def test_the_first_frame_is_not_the_last(self):
        frames = patterns.tetris_fill(hold=0)
        self.assertNotEqual(list(frames[0].pixels), list(frames[-1].pixels))

    def test_a_nonsense_pace_is_refused(self):
        with self.assertRaises(ValueError):
            patterns.tetris_fill(pace=0)

    def test_it_fits_what_the_keyboard_takes(self):
        frames = patterns.tetris_fill()
        self.assertLessEqual(len(frames), matrix.MAX_FRAMES_ANIMATION)
        matrix.Animation(frames=frames).payload()      # must not raise


class TheRain(unittest.TestCase):
    def test_the_loop_closes(self):
        # Frame 48 of a 49-frame run has to be frame 0 again, or the
        # strip jumps every time the animation restarts.
        short = patterns.matrix_rain("green")
        long = patterns.matrix_rain("green", frames=len(short) + 1)
        self.assertEqual(list(long[-1].pixels), list(short[0].pixels))

    def test_each_colour_stays_in_its_own_hue(self):
        for name, (base, head) in patterns.RAIN_COLOURS.items():
            frame = patterns.matrix_rain(name)[0]
            for pixel in frame.pixels:
                if not pixel:
                    continue
                for shift in (16, 8, 0):
                    if (base >> shift) & 0xFF or not (pixel >> shift) & 0xFF:
                        continue
                    # Only the near-white head may light a channel the
                    # base colour leaves dark.
                    self.assertEqual(pixel, head,
                                     f"{name} lit a channel it should not")

    def test_drops_leave_gaps(self):
        # The point of the short tail: on five rows a longer one never
        # leaves the strip dark anywhere.
        counts = [sum(1 for p in f.pixels if p)
                  for f in patterns.matrix_rain("green")]
        self.assertLess(max(counts), matrix.LEDS // 2)
        self.assertGreater(min(counts), 0)

    def test_the_head_leads_the_tail(self):
        frame = patterns.matrix_rain("green")[0]
        for col in range(matrix.COLS):
            rows = [r for r in range(matrix.ROWS) if frame[col, r]]
            if len(rows) < 2:
                continue
            brightest = max(rows, key=lambda r: sum(
                (frame[col, r] >> s) & 0xFF for s in (16, 8, 0)))
            self.assertEqual(brightest, max(rows), f"column {col}")

    def test_an_unknown_colour_names_the_ones_there_are(self):
        with self.assertRaises(ValueError) as caught:
            patterns.matrix_rain("puce")
        self.assertIn("magenta", str(caught.exception))

    def test_an_empty_animation_is_refused(self):
        with self.assertRaises(ValueError):
            patterns.matrix_rain("green", frames=0)


class TheRunner(unittest.TestCase):
    """The browser's game, at 65x5."""

    def setUp(self) -> None:
        self.frames = patterns.dino_run()
        self.body = range(patterns.RUNNER_COLUMN,
                          patterns.RUNNER_COLUMN + patterns.RUNNER_WIDTH)

    def _on_the_ground(self, frame) -> bool:
        return any(frame[col, row] == patterns.RUNNER_COLOUR
                   for col in self.body for row in (2, 3))

    def test_the_runner_never_runs_into_a_cactus(self):
        """The whole point of the jump, and the easiest thing to break.

        An earlier version scored only the nearest cactus ahead, so the
        runner landed on the one it was still standing over.
        """
        for n, frame in enumerate(self.frames):
            for col in self.body:
                cactus = any(frame[col, row] == patterns.CACTUS_COLOUR
                             for row in (2, 3))
                if cactus:
                    self.assertFalse(
                        self._on_the_ground(frame),
                        f"frame {n}: a cactus is in column {col} and the "
                        "runner is on the ground"
                    )

    def test_it_both_runs_and_jumps(self):
        airborne = sum(1 for f in self.frames if not self._on_the_ground(f))
        self.assertGreater(airborne, 5, "it never leaves the ground")
        self.assertLess(airborne, len(self.frames) - 5, "it never lands")

    def test_the_ground_is_always_underfoot(self):
        for n, frame in enumerate(self.frames):
            lit = sum(1 for col in range(matrix.COLS) if frame[col, matrix.ROWS - 1])
            self.assertEqual(lit, matrix.COLS, f"frame {n} has a gap in the ground")

    def test_the_legs_move_while_it_runs(self):
        grounded = [f for f in self.frames if self._on_the_ground(f)]
        shapes = {
            tuple(sorted((col, row) for col in self.body for row in range(matrix.ROWS)
                         if f[col, row] == patterns.RUNNER_COLOUR))
            for f in grounded
        }
        self.assertGreater(len(shapes), 1, "the runner is a statue")

    def test_the_world_loops(self):
        # One lap further on is the first frame again, or the strip
        # jumps every time the animation restarts.
        lap = patterns.dino_run(frames=len(self.frames) + 1)
        self.assertEqual(list(lap[-1].pixels), list(self.frames[0].pixels))

    def test_a_world_shorter_than_the_strip_is_refused(self):
        with self.assertRaises(ValueError):
            patterns.dino_run(world=matrix.COLS - 1)

    def test_there_are_cacti_to_jump(self):
        seen = max(sum(1 for col in range(matrix.COLS) for row in range(matrix.ROWS)
                       if f[col, row] == patterns.CACTUS_COLOUR)
                   for f in self.frames)
        self.assertGreater(seen, 2)


class TheCatalogue(unittest.TestCase):
    def test_every_name_builds(self):
        for name in patterns.BUILT_INS:
            frames = patterns.build(name)
            self.assertTrue(frames)
            self.assertLessEqual(len(frames), matrix.MAX_FRAMES_ANIMATION)

    def test_an_unknown_name_lists_the_known_ones(self):
        with self.assertRaises(ValueError) as caught:
            patterns.build("pong")
        self.assertIn("tetris", str(caught.exception))

    def test_the_rain_colours_are_all_offered(self):
        for colour in patterns.RAIN_COLOURS:
            self.assertIn(f"matrix-{colour}", patterns.BUILT_INS)


if __name__ == "__main__":
    unittest.main()


class TheMeter(unittest.TestCase):
    """The panel that shows what the AI tools have spent."""

    def reading(self, tool="claude", **kw):
        from mk_k68_lightsmart import usage
        import time
        fields = dict(tool=tool, tokens=2_500_000, context=600_000,
                      context_window=1_000_000, plan_percent=None,
                      plan_window_minutes=None, plan_long_percent=None,
                      updated=time.time(), source=Path("x"))
        fields.update(kw)
        return usage.Reading(**fields)

    def test_a_panel_each(self):
        frames = patterns.meter([self.reading("claude"),
                                 self.reading("codex", plan_percent=64.0)])
        self.assertEqual(len(frames), 2)
        self.assertNotEqual(list(frames[0].pixels), list(frames[1].pixels))

    def test_frames_each_stretches_a_panel(self):
        frames = patterns.meter([self.reading()], frames_each=30)
        self.assertEqual(len(frames), 30)
        self.assertEqual(list(frames[0].pixels), list(frames[-1].pixels))

    def test_the_name_fills_with_the_context(self):
        from mk_k68_lightsmart import font
        empty = patterns.meter_screen(self.reading(context=0))
        full = patterns.meter_screen(self.reading(context=1_000_000))
        bright = patterns.METER_COLOURS["claude"][0]
        lit = lambda f: sum(1 for p in f.pixels if p == bright)
        self.assertGreater(lit(full), lit(empty),
                           "the name should fill as the window does")

    def test_each_tool_has_its_own_colour(self):
        claude = patterns.meter_screen(self.reading("claude"))
        codex = patterns.meter_screen(self.reading("codex"))
        self.assertIn(patterns.METER_COLOURS["claude"][0], set(claude.pixels))
        self.assertIn(patterns.METER_COLOURS["codex"][0], set(codex.pixels))

    def test_a_session_that_is_over_is_drawn_grey(self):
        stale = patterns.meter_screen(self.reading(updated=0.0))
        self.assertIn(patterns.METER_IDLE[0], set(stale.pixels))
        self.assertNotIn(patterns.METER_COLOURS["claude"][0], set(stale.pixels))

    def test_a_tool_with_no_session_still_shows_its_name(self):
        from mk_k68_lightsmart import usage
        screen = patterns.meter_screen(usage.empty("codex"))
        self.assertTrue(any(screen.pixels))

    def test_nothing_to_show_is_refused(self):
        with self.assertRaises(ValueError):
            patterns.meter([])

    def test_a_nonsense_frame_count_is_refused(self):
        with self.assertRaises(ValueError):
            patterns.meter([self.reading()], frames_each=0)


class TheFont(unittest.TestCase):
    def test_a_glyph_is_three_by_five(self):
        from mk_k68_lightsmart import font
        for character, rows in font._GLYPHS.items():
            self.assertEqual(len(rows), font.GLYPH_HEIGHT, character)
            for row in rows:
                self.assertEqual(len(row), font.GLYPH_WIDTH, character)

    def test_the_strip_holds_sixteen_characters(self):
        from mk_k68_lightsmart import font
        self.assertLessEqual(font.width("X" * 16), matrix.COLS)
        self.assertGreater(font.width("X" * 17), matrix.COLS)

    def test_text_past_the_edge_is_clipped_not_wrapped(self):
        from mk_k68_lightsmart import font
        frame = matrix.Frame()
        font.draw(frame, "WWWW", matrix.COLS - 4, 0xFFFFFF)
        # nothing may appear back at the left
        self.assertFalse(any(frame[col, row] for col in range(4)
                             for row in range(matrix.ROWS)))

    def test_an_unknown_character_still_draws_something(self):
        from mk_k68_lightsmart import font
        frame = matrix.Frame()
        font.draw(frame, "ç", 0, 0xFFFFFF)
        self.assertTrue(any(frame.pixels))


class TheShippedEffects(unittest.TestCase):
    """The animations converted from the vendor's library.

    They are data files, so what matters is that every one of them
    still decodes into frames the keyboard will take -- a truncated or
    half-written file would otherwise only show up on the strip.
    """

    def test_the_index_and_the_files_agree(self):
        for key, entry in patterns.bundled().items():
            path = patterns.EFFECTS_DIR / entry["file"]
            self.assertTrue(path.exists(), key)
            self.assertEqual(path.stat().st_size, entry["bytes"], key)

    def test_every_one_decodes_to_the_frames_it_claims(self):
        for key, entry in patterns.bundled().items():
            frames = patterns.load_bundled(key)
            self.assertEqual(len(frames), entry["frames"], key)
            self.assertTrue(frames, key)

    def test_every_one_is_uploadable(self):
        for key in patterns.bundled():
            frames = patterns.build(key)
            self.assertLessEqual(len(frames), matrix.MAX_FRAMES_ANIMATION, key)
            matrix.Animation(frames=frames).payload()

    def test_they_are_pictures_rather_than_blank(self):
        for key in patterns.bundled():
            frames = patterns.load_bundled(key)
            lit = sum(1 for frame in frames for pixel in frame.pixels if pixel)
            self.assertGreater(lit, 0, f"{key} is entirely dark")

    def test_the_catalogue_is_the_generated_ones_and_these(self):
        catalogue = patterns.catalogue()
        for key in patterns.GENERATED:
            self.assertIn(key, catalogue)
        for key in patterns.bundled():
            self.assertIn(key, catalogue)

    def test_a_missing_folder_is_not_fatal(self):
        # A checkout that has not been converted still runs; only the
        # generated animations are offered.
        with unittest.mock.patch.object(patterns, "EFFECTS_DIR",
                                        Path("/nonexistent")):
            patterns.bundled.cache_clear()
            try:
                self.assertEqual(patterns.bundled(), {})
                self.assertEqual(set(patterns.catalogue()),
                                 set(patterns.GENERATED))
                self.assertTrue(patterns.build("tetris"))
            finally:
                patterns.bundled.cache_clear()

    def test_a_truncated_file_says_so(self):
        import gzip
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "x.strip.gz").write_bytes(gzip.compress(b"\x00" * 100))
            (folder / "index.json").write_text(json.dumps(
                {"effects": [{"key": "x", "title": "X", "file": "x.strip.gz",
                              "frames": 1, "bytes": 0}]}))
            with unittest.mock.patch.object(patterns, "EFFECTS_DIR", folder):
                patterns.bundled.cache_clear()
                try:
                    with self.assertRaises(ValueError):
                        patterns.load_bundled("x")
                finally:
                    patterns.bundled.cache_clear()

    def test_an_unknown_name_lists_what_there_is(self):
        with self.assertRaises(ValueError) as caught:
            patterns.build("does-not-exist")
        self.assertIn("aquarium", str(caught.exception))


class Overall(unittest.TestCase):
    """The effect that plays all the others from one upload."""

    def test_it_fits_what_the_keyboard_holds(self):
        frames = patterns.overall()
        self.assertLessEqual(len(frames), matrix.MAX_FRAMES_ANIMATION)
        matrix.Animation(frames=frames).payload()

    def test_every_effect_gets_a_turn(self):
        """Each contributes its share, or all of it if it is shorter.

        Pac-chase is two frames and the sound bars are three, so the
        total is not simply the share times the count.
        """
        names = list(patterns.catalogue())
        share = matrix.MAX_FRAMES_ANIMATION // len(names)
        expected = sum(min(share, len(patterns.build(name))) for name in names)
        self.assertEqual(len(patterns.overall()), expected)
        self.assertGreater(expected, share)        # more than one got in

    def test_it_is_not_one_of_its_own_ingredients(self):
        # Building it from a list that contains it is a recursion, not
        # an animation.
        self.assertNotIn(patterns.OVERALL, patterns.catalogue())
        self.assertIn(patterns.OVERALL, patterns.catalogue(with_overall=True))
        frames = patterns.overall(names=list(patterns.catalogue(True)))
        self.assertTrue(frames)

    def test_a_short_effect_is_shown_whole(self):
        frames = patterns.overall(limit=200, names=["pacman", "tetris"])
        pacman = patterns.build("pacman")
        self.assertEqual([list(f.pixels) for f in frames[:len(pacman)]],
                         [list(f.pixels) for f in pacman])

    def test_a_long_one_is_sampled_rather_than_cut_short(self):
        # What shows should be the whole of it played quickly, so the
        # last frame of the share comes from near the end of the source.
        long_one = max(patterns.bundled(),
                       key=lambda key: patterns.bundled()[key]["frames"])
        source = patterns.build(long_one)
        frames = patterns.overall(limit=20, names=[long_one])
        self.assertEqual(list(frames[-1].pixels),
                         list(source[round(19 * len(source) / 20)].pixels))

    def test_too_few_frames_for_the_list_is_refused(self):
        with self.assertRaises(ValueError):
            patterns.overall(limit=3, names=list(patterns.catalogue()))

    def test_nothing_to_show_is_refused(self):
        with self.assertRaises(ValueError):
            patterns.overall(names=[patterns.OVERALL])

    def test_build_knows_it_by_name(self):
        self.assertTrue(patterns.build(patterns.OVERALL))


class SpeedMapping(unittest.TestCase):
    """Calibrated by eye against a library GIF, which is 100 ms a frame."""

    def test_the_native_setting_is_about_a_hundred_milliseconds(self):
        self.assertAlmostEqual(matrix.frame_ms(matrix.NATIVE_SPEED), 100,
                               delta=2)

    def test_the_scale_runs_backwards(self):
        self.assertLess(matrix.frame_ms(100), matrix.frame_ms(1))

    def test_a_duration_round_trips(self):
        for ms in (20, 50, 100, 140):
            self.assertAlmostEqual(
                matrix.frame_ms(matrix.speed_for_ms(ms)), ms, delta=2)

    def test_asking_for_the_impossible_clamps(self):
        self.assertGreaterEqual(matrix.speed_for_ms(0), 1)
        self.assertLessEqual(matrix.speed_for_ms(10_000), 100)

    def test_the_default_is_boosted_past_the_native_rate(self):
        # The wire value is a whole number of delay units, so the boost
        # lands near 1.25 rather than on it -- the next step down the
        # scale would be 1.27.
        boost = (matrix.frame_ms(matrix.NATIVE_SPEED)
                 / matrix.frame_ms(matrix.DEFAULT_PLAYBACK_SPEED))
        self.assertAlmostEqual(boost, matrix.PLAYBACK_BOOST, delta=0.02)

    def test_the_default_is_a_setting_the_keyboard_accepts(self):
        self.assertTrue(1 <= matrix.DEFAULT_PLAYBACK_SPEED <= 100)
        # Nothing was clamped on the way to the wire.
        self.assertEqual(
            matrix.speed_to_byte(matrix.DEFAULT_PLAYBACK_SPEED),
            matrix.SPEED_BASE - matrix.DEFAULT_PLAYBACK_SPEED)
