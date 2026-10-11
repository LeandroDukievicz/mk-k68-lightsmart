"""Checks the reading of what the local AI tools have spent.

The fixtures are transcripts written here, not copies of real ones:
they are small, they can carry the awkward cases on purpose, and no
session of anyone's ends up in the repository.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mk_k68_lightsmart import usage


def write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def claude_turn(input_tokens=0, cache_read=0, cache_write=0, output=0,
                model="claude-opus-5"):
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
                "output_tokens": output,
            },
        },
    }


def codex_count(total_in, cached_in, cache_write, out, window=258_400,
                primary=None, secondary=None):
    return {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": total_in,
                    "cached_input_tokens": cached_in,
                    "cache_write_input_tokens": cache_write,
                    "output_tokens": out,
                    "total_tokens": total_in + out,
                },
                "last_token_usage": {"input_tokens": 1000, "output_tokens": 50},
                "model_context_window": window,
            },
            "rate_limits": {
                "primary": primary or {"used_percent": 64.0, "window_minutes": 300},
                "secondary": secondary or {"used_percent": 10.0,
                                           "window_minutes": 10080},
            },
        },
    }


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir,
                                                            ignore_errors=True))


class ReadingClaude(Scratch):
    def test_cache_reads_are_not_spending(self):
        """The same context is re-read every turn.

        Counting those reports a quarter of a billion tokens for an
        afternoon, which is how this was first got wrong.
        """
        write(self.dir / "p" / "s.jsonl", [
            claude_turn(input_tokens=10, cache_read=50_000, cache_write=100,
                        output=5),
            claude_turn(input_tokens=20, cache_read=50_000, cache_write=0,
                        output=7),
        ])
        got = usage.claude_reading(self.dir)
        self.assertEqual(got.tokens, 10 + 100 + 5 + 20 + 0 + 7)

    def test_the_context_is_the_last_turns(self):
        write(self.dir / "p" / "s.jsonl", [
            claude_turn(input_tokens=1, cache_read=90_000, output=10),
            claude_turn(input_tokens=1, cache_read=120_000, output=20),
        ])
        got = usage.claude_reading(self.dir)
        self.assertEqual(got.context, 1 + 120_000 + 20)

    def test_the_window_is_inferred_from_the_fullest_turn(self):
        # The transcripts never say what the window is, so a session
        # that held 590K cannot be reported as 100% of 200K.
        write(self.dir / "p" / "s.jsonl",
              [claude_turn(input_tokens=1, cache_read=590_000, output=1)])
        got = usage.claude_reading(self.dir)
        self.assertEqual(got.context_window, 1_000_000)
        self.assertLess(got.context_percent, 100)

    def test_a_small_session_keeps_the_small_window(self):
        write(self.dir / "p" / "s.jsonl",
              [claude_turn(input_tokens=1, cache_read=1000, output=1)])
        self.assertEqual(usage.claude_reading(self.dir).context_window, 200_000)

    def test_no_plan_figure_is_invented(self):
        write(self.dir / "p" / "s.jsonl", [claude_turn(output=1)])
        got = usage.claude_reading(self.dir)
        self.assertIsNone(got.plan_percent)
        self.assertIsNone(got.plan_long_percent)

    def test_the_newest_transcript_wins(self):
        old = write(self.dir / "a" / "old.jsonl", [claude_turn(output=1)])
        new = write(self.dir / "b" / "new.jsonl", [claude_turn(output=99)])
        import os
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        self.assertEqual(usage.claude_reading(self.dir).source, new)

    def test_a_half_written_line_is_skipped(self):
        path = self.dir / "p" / "s.jsonl"
        write(path, [claude_turn(output=3)])
        with path.open("a") as handle:
            handle.write('{"type": "assistant", "message": {"usage": {"out')
        self.assertEqual(usage.claude_reading(self.dir).tokens, 3)

    def test_nothing_there_reads_as_nothing(self):
        got = usage.claude_reading(self.dir / "missing")
        self.assertFalse(got.found)
        self.assertFalse(got.live)
        self.assertEqual(got.tokens, 0)


class ReadingCodex(Scratch):
    def test_totals_and_limits_come_off_the_last_count(self):
        write(self.dir / "d" / "r.jsonl", [
            codex_count(1_000, 500, 0, 100),
            codex_count(1_723_983, 1_674_368, 0, 9_123),
        ])
        got = usage.codex_reading(self.dir)
        self.assertEqual(got.tokens, 1_723_983 - 1_674_368 + 9_123)
        self.assertEqual(got.context_window, 258_400)
        self.assertEqual(got.plan_percent, 64.0)
        self.assertEqual(got.plan_long_percent, 10.0)
        self.assertEqual(got.plan_window_minutes, 300)

    def test_a_percentage_out_of_range_is_clamped(self):
        write(self.dir / "d" / "r.jsonl",
              [codex_count(10, 0, 0, 1, primary={"used_percent": 140.0})])
        self.assertEqual(usage.codex_reading(self.dir).plan_percent, 100.0)

    def test_nonsense_in_a_limit_is_not_a_number(self):
        write(self.dir / "d" / "r.jsonl",
              [codex_count(10, 0, 0, 1, primary={"used_percent": None})])
        self.assertIsNone(usage.codex_reading(self.dir).plan_percent)


class Liveness(Scratch):
    def test_a_recent_transcript_is_live(self):
        write(self.dir / "p" / "s.jsonl", [claude_turn(output=1)])
        self.assertTrue(usage.claude_reading(self.dir).live)

    def test_an_old_one_is_not(self):
        import os
        path = write(self.dir / "p" / "s.jsonl", [claude_turn(output=1)])
        old = time.time() - usage.LIVE_SECONDS - 60
        os.utime(path, (old, old))
        self.assertFalse(usage.claude_reading(self.dir).live)


class Formatting(unittest.TestCase):
    def test_counts_fit_the_strip(self):
        self.assertEqual(usage.short(942), "942")
        self.assertEqual(usage.short(58_749), "58K")
        self.assertEqual(usage.short(1_733_106), "1.7M")
        self.assertEqual(usage.short(2_000_000), "2M")

    def test_nothing_is_longer_than_four_characters(self):
        for count in (0, 999, 1000, 999_999, 1_000_000, 987_654_321):
            self.assertLessEqual(len(usage.short(count)), 4, count)


if __name__ == "__main__":
    unittest.main()
