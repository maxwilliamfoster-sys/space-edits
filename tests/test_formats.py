"""The non-zoom formats (2026-10-28): different stories, true numbers, and a mix across seeds."""
import collections
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gate
from spacepipe import compose, facts, render

NO_HISTORY = {"usage": {}, "edits": []}


def plan(seed, template=None):
    with mock.patch.object(compose, "history", return_value=NO_HISTORY):
        return compose.plan(seed, template)


class Formats(unittest.TestCase):
    def test_seeds_spread_over_every_format(self):
        fams = collections.Counter(compose.FAMILY[plan(s)["template"]] for s in range(9000, 9100))
        self.assertEqual(set(fams), set(compose.FAMILIES))
        self.assertLess(max(fams.values()), 40)          # ~20 each, no format dominates

    def test_every_format_fills_60_beats_and_lints_clean(self):
        for t in compose.FORMATS:
            for seed in range(5):
                p = plan(seed, t)
                self.assertEqual(sum(s["beats"] for s in p["shots"]), 60, t)
                self.assertEqual(gate.text_lint(p), [], (t, p["hook"]))
                self.assertTrue(render.text_layers(p))

    def test_every_hook_in_every_pool_lints_clean(self):
        for t, hooks in facts.FORMAT_HOOKS.items():
            for h in hooks + facts.HOOKS:
                self.assertEqual(gate.text_lint(dict(hook=h, hashtags=facts.FORMAT_HASHTAGS[t][0])), [], h)

    def test_on_screen_distances_are_the_clips_own(self):
        for t in ("timemachine", "guess", "countdown"):
            p = plan(3, t)
            shown = " ".join(x["text"] for x in p["texts"])
            for s in p["shots"]:
                self.assertIn(facts.ly_text(s["dist_ly"]), shown, (t, s["asset"]))

    def test_time_machine_and_countdown_run_nearest_to_farthest(self):
        for t in ("timemachine", "countdown"):
            d = [compose.ly_value(s["dist_ly"]) for s in plan(11, t)["shots"]]
            self.assertEqual(d, sorted(d), t)

    def test_eras_are_true_for_their_distance(self):
        self.assertEqual(facts.era_line(5e9), "BEFORE EARTH EXISTED")
        self.assertEqual(facts.era_line(1e8), "IN THE AGE OF THE DINOSAURS")
        self.assertEqual(facts.era_line(3e7), "BEFORE HUMANS EXISTED")
        self.assertEqual(facts.era_line(6500), "BEFORE THE GREAT PYRAMID WAS BUILT")
        self.assertEqual(facts.era_line(400), "LONG BEFORE YOU WERE BORN")
        self.assertEqual(facts.era_line(50), "")

    def test_guess_reveals_after_the_question(self):
        p = plan(5, "guess")
        q = [x for x in p["texts"] if x["text"] == "HOW FAR AWAY IS THIS?"]
        a = [x for x in p["texts"] if x["text"].endswith("LIGHT-YEARS")]
        self.assertEqual(len(q), len(a))
        for qi, ai in zip(q, a):
            self.assertEqual(qi["k1"], ai["k0"])

    def test_each_format_has_its_own_look(self):
        grades = {t: plan(2, t)["shots"][0].get("grade") for t in compose.FORMATS}
        self.assertEqual(len(set(grades.values())), len(grades))
        cuts = {plan(2, t)["shots"][1]["tin"][0] for t in compose.FORMATS}
        self.assertGreaterEqual(len(cuts), 3)


if __name__ == "__main__":
    unittest.main()
