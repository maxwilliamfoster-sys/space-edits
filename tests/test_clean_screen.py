"""Clean-screen A/B (autopilot review 2026-11-18, stage 2 'visual layout' swing). The two best-performing space accounts
we study (@astroplaneet, @nsn.astro) put almost no text on screen; ours stacked a top hook, a name, a small sub-line, a
distance and an era line. Since the narrator now says all of it, half of edits ('clean') drop the top hook and the small
sub-lines and keep only the big name/distance words and the credits. Arm logged as gate_log 'clean' -> analytics.json
-> tracker test 'space-clean' (views at 72 h, min 10 per arm)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics
import gate
from spacepipe import compose, render

NO_HISTORY = {"usage": {}, "edits": []}


def plan(seed, template=None):
    with mock.patch.object(compose, "history", return_value=NO_HISTORY):
        return compose.plan(seed, template)


class Draw(unittest.TestCase):
    def test_half_of_edits_are_clean(self):
        arms = [plan(f"c{s}")["clean"] for s in range(300)]
        self.assertTrue(0.4 < sum(arms) / len(arms) < 0.6)

    def test_the_draw_keeps_a_seeds_shots_and_text(self):
        with mock.patch.object(compose, "CLEAN_SHARE", 0):
            a = plan("same", "target")
        with mock.patch.object(compose, "CLEAN_SHARE", 1):
            b = plan("same", "target")
        self.assertEqual((a["shots"], a["texts"], a["hook"]), (b["shots"], b["texts"], b["hook"]))
        self.assertFalse(a["clean"])
        self.assertTrue(b["clean"])

    def test_share_zero_turns_it_off(self):
        with mock.patch.object(compose, "CLEAN_SHARE", 0):
            self.assertFalse(any(plan(f"c{s}")["clean"] for s in range(50)))


class Layers(unittest.TestCase):
    def test_clean_drops_the_hook_and_small_lines_only(self):
        for template in ("target", "flash", "timemachine", "guess", "countdown", "drift"):
            p = plan("layers", template)
            full = render.text_layers(dict(p, clean=False))
            clean = render.text_layers(dict(p, clean=True))
            self.assertTrue(any(L["hook"] for L in full), template)
            self.assertFalse(any(L["hook"] for L in clean), template)
            self.assertFalse(any(L["font"] == "arial" for L in clean), template)
            big = [L for L in full if not L["hook"] and L["font"] == "impact"]
            self.assertEqual(big, [L for L in clean], template)

    def test_target_keeps_name_and_distance(self):
        p = plan("layers", "target")
        texts = [L["text"] for L in render.text_layers(dict(p, clean=True))]
        self.assertTrue(any("LIGHT-YEARS" in t for t in texts))
        self.assertTrue(texts)

    def test_old_plans_without_the_key_are_full(self):
        p = plan("layers", "target")
        p.pop("clean", None)
        self.assertTrue(any(L["hook"] for L in render.text_layers(p)))


class Logged(unittest.TestCase):
    def test_gate_log_and_analytics_carry_the_arm(self):
        p = plan("log", "target")
        self.assertEqual(gate.entry_for(dict(p, clean=True), [], "e1")["clean"], True)
        self.assertIsNone(gate.entry_for({k: v for k, v in p.items() if k != "clean"}, [], "e1")["clean"])
        sent = [{"id": "p1", "sentAt": "2026-11-20T12:00:00Z", "metrics": {"views": 10}}]
        rows = analytics.rows(sent, {"e1": {"post": "p1"}}, [{"id": "e1", "template": "flash", "clean": True}])
        self.assertTrue(rows[0]["clean"])


if __name__ == "__main__":
    unittest.main()
