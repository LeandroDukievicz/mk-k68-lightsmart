"""Checks the scheme file, which is the only copy of hand-made work."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mk_k68_lightsmart import store


class SchemeBookkeeping(unittest.TestCase):
    def setUp(self):
        self.s = store.Store()

    def test_a_fresh_store_has_exactly_one_scheme(self):
        self.assertEqual(self.s.names(), ["Default"])
        self.assertIs(self.s.current, self.s.schemes[0])

    def test_names_do_not_collide(self):
        self.s.add("Work")
        self.s.add("Work")
        self.assertEqual(self.s.names(), ["Default", "Work", "Work 2"])

    def test_the_last_scheme_cannot_be_dropped(self):
        self.assertFalse(self.s.remove(0))
        self.assertEqual(len(self.s.schemes), 1)

    def test_dropping_keeps_the_active_index_inside(self):
        self.s.add("Work")
        self.s.active = 1
        self.assertTrue(self.s.remove(1))
        self.assertEqual(self.s.active, 0)
        self.assertEqual(self.s.current.name, "Default")

    def test_a_duplicate_does_not_share_its_parents_dicts(self):
        self.s.current.lighting[5] = 0xFF0000
        copy = self.s.duplicate(0)
        copy.lighting[6] = 0x00FF00
        self.assertNotIn(6, self.s.schemes[0].lighting)

    def test_a_silly_active_index_is_clamped_not_raised(self):
        self.s.active = 99
        self.assertIs(self.s.current, self.s.schemes[0])

    def test_the_keymap_comes_back_in_the_shape_the_encoder_wants(self):
        self.s.current.keymap = {55: [0xE0, 0]}
        self.assertEqual(self.s.current.remap_changes(), {55: (0xE0, 0)})

    def test_a_keymap_entry_without_modifiers_still_works(self):
        # What an older file, or a hand-edited one, might hold.
        self.s.current.keymap = {55: [0xE0]}
        self.assertEqual(self.s.current.remap_changes(), {55: (0xE0, 0)})


class TheFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "schemes.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_round_trip_keeps_everything(self):
        s = store.Store(path=self.path)
        s.current.name = "Gaming"
        s.current.keymap = {55: [0xE0, 0]}
        s.current.lighting = {1: 0xFF0000}
        s.current.macros = {"m": [{"kind": "Key down", "value": "A"}]}
        s.add("Work")
        s.active = 1
        s.save()

        back = store.Store.load(self.path)
        self.assertEqual(back.names(), ["Gaming", "Work"])
        self.assertEqual(back.active, 1)
        self.assertEqual(back.schemes[0].remap_changes(), {55: (0xE0, 0)})
        self.assertEqual(back.schemes[0].lighting, {1: 0xFF0000})
        self.assertEqual(back.schemes[0].macros["m"][0]["value"], "A")

    def test_no_file_is_an_ordinary_first_run(self):
        back = store.Store.load(self.path)
        self.assertEqual(back.names(), ["Default"])

    def test_a_corrupt_file_does_not_take_the_app_with_it(self):
        self.path.write_text("{ not json")
        self.assertEqual(store.Store.load(self.path).names(), ["Default"])

    def test_a_file_from_a_later_version_is_refused_not_guessed(self):
        self.path.write_text(json.dumps({"version": VERSION_AHEAD,
                                         "schemes": [{"name": "X"}]}))
        self.assertEqual(store.Store.load(self.path).names(), ["Default"])

    def test_saving_leaves_no_temporary_behind(self):
        store.Store(path=self.path).save()
        self.assertEqual([p.name for p in self.path.parent.iterdir()],
                         ["schemes.json"])


VERSION_AHEAD = store.VERSION + 1


if __name__ == "__main__":
    unittest.main()
