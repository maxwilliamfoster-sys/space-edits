"""Flash cut (2026-11-12): the 10 s named-target edit that loops back to its first frame."""
import collections
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gate  # noqa: E402
from spacepipe import compose, qc, render  # noqa: E402

NO_HISTORY = {"usage": {}, "edits": []}


def plan(seed, template=None):
    with mock.patch.object(compose, "history", return_value=NO_HISTORY):
        return compose.plan(seed, template)


class Flash(unittest.TestCase):
    def test_flash_is_about_10_seconds_and_loops(self):
        for seed in range(6):
            p = plan(seed, "flash")
            t_end = p["T0"] + p["total_beats"] * p["P"]
            self.assertTrue(8.5 <= t_end <= 11.0, t_end)
            self.assertTrue(p["loop"])
            self.assertEqual(p["template"], "flash")
            self.assertEqual(gate.text_lint(p), [], p["hook"])
            self.assertFalse(any(t["text"] in compose.facts.TARGET_CLOSERS for t in p["texts"]))

    def test_about_a_third_of_seeds_are_flash_and_others_keep_their_plan(self):
        kinds = collections.Counter(plan(s)["template"] == "flash" for s in range(3000, 3150))
        self.assertTrue(35 <= kinds[True] <= 70, kinds)
        with mock.patch.object(compose, "FLASH_SHARE", 0):
            before = {s: plan(s)["template"] for s in range(3000, 3040)}
        after = {s: plan(s)["template"] for s in range(3000, 3040)}
        self.assertTrue(all(after[s] == before[s] for s in after if after[s] != "flash"))

    def test_loop_chain_dissolves_into_the_first_frame(self):
        p = plan(1, "flash")
        chain = render.post_chain(p, "x", 10.0, ["drawtext=text='A'"])
        self.assertIn("trim=end_frame=1", chain)
        self.assertIn(f"xfade=transition=fade:duration={render.LOOP_D}:offset=9.500", chain)
        self.assertNotIn("fade=t=out", chain)
        self.assertIn("d=0.3", render.audio_filter(p, 10.0))
        plain = plan(1, "target")
        self.assertIn("fade=t=out", render.post_chain(plain, "x", 18.0, ["drawtext=text='A'"]))

    def test_qc_allows_a_short_flash(self):
        self.assertEqual(qc.min_seconds({"template": "flash"}), 8)
        self.assertEqual(qc.min_seconds({"template": "classic"}), 20)

    def test_never_two_flashes_in_a_row(self):
        self.assertEqual(compose.FAMILY["flash"], "flash")


if __name__ == "__main__":
    unittest.main()
