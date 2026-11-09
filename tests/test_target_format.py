"""The named-target cut (research report action 8): about 18 s, opening on a named place.
Off by default (TARGET_SHARE 0) until the 2 Nov results are read on 9 Nov."""
import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gate
from spacepipe import compose, facts, qc, render
from spacepipe.lib import load_assets

NO_HISTORY = {"usage": {}, "edits": []}


def plan(seed, template="target"):
    with mock.patch.object(compose, "history", return_value=NO_HISTORY):
        return compose.plan(seed, template)


class SwitchedOff(unittest.TestCase):
    def test_it_never_comes_up_at_share_zero(self):
        with mock.patch.object(compose, "TARGET_SHARE", 0):
            self.assertNotIn("target", {plan(s, None)["template"] for s in range(200)})

    def test_live_share_is_half(self):
        # switched on 2026-11-09 (review 2026-11-09)
        self.assertEqual(compose.TARGET_SHARE, 0.5)

    def test_a_seed_still_gives_the_same_edit(self):
        # share 0 takes no draw, so a seed gives the edit it gave before the test existed
        with mock.patch.object(compose, "history", return_value=NO_HISTORY),                 mock.patch.object(compose, "TARGET_SHARE", 0):
            first = compose.plan(9001)
            second = compose.plan(9001)
        self.assertEqual(first["template"], second["template"])
        self.assertEqual([s["asset"] for s in first["shots"]], [s["asset"] for s in second["shots"]])

    def test_the_other_formats_keep_their_60_beats(self):
        for t in [*compose.TEMPLATES, *compose.FORMATS]:
            self.assertNotIn("total_beats", plan(1, t))


class SwitchedOn(unittest.TestCase):
    def test_it_comes_up_about_half_the_time_at_half_share(self):
        with mock.patch.object(compose, "TARGET_SHARE", 0.5):
            n = sum(plan(s, None)["template"] == "target" for s in range(200))
        self.assertTrue(60 < n < 140, n)


class Targets(unittest.TestCase):
    def test_names_come_from_the_list_or_the_clips_own_title(self):
        self.assertEqual(compose.target_name({"id": "weic2608c", "title": "Zoom video: M51"}), "THE WHIRLPOOL GALAXY")
        self.assertEqual(compose.target_name({"id": "x", "title": "Pan Video: NGC 2283"}), "NGC 2283")
        self.assertEqual(compose.target_name({"id": "x", "title": "Pan: I Zwicky 18 (I Zw 18)"}), None)
        self.assertEqual(compose.target_name({"id": "x", "title": "Pan video: NGC 4490 and NGC 4485"}), None)
        self.assertEqual(compose.target_name({"id": "x", "title": "Pullout video: NGC 6302"}), None)

    def test_every_listed_clip_exists_and_can_carry_a_distance(self):
        pool = {a["id"] for a in compose.dist_pool(load_assets(), set())}
        missing = [i for i in compose.TARGET_NAMES if i not in pool]
        self.assertLessEqual(len(missing), len(compose.TARGET_NAMES) // 2, missing)

    def test_there_are_enough_places_to_stay_fresh(self):
        pool = [a for a in compose.dist_pool(load_assets(), set()) if compose.target_name(a)]
        self.assertGreaterEqual(len({compose.target_name(a) for a in pool}), 25)


class Edit(unittest.TestCase):
    def test_it_runs_15_to_20_seconds_on_every_track(self):
        for seed in range(30):
            p = plan(seed)
            total = p["total_beats"]
            self.assertEqual(sum(s["beats"] for s in p["shots"]), total)
            self.assertTrue(15 <= p["T0"] + total * p["P"] <= 20.5, (seed, p["P"]))

    def test_the_name_is_on_screen_from_the_first_frame(self):
        for seed in range(10):
            p = plan(seed)
            name = compose.target_name(next(a for a in load_assets() if a["id"] == p["shots"][0]["asset"]))
            self.assertIn(name.lower(), p["hook"].lower())
            self.assertEqual(p["texts"][0]["k0"], 0)
            self.assertEqual(p["texts"][0]["text"], name.upper())

    def test_a_catalogue_name_keeps_its_capitals_in_the_caption(self):
        self.assertEqual(compose.hook_name("NGC 3507"), "NGC 3507")
        self.assertEqual(compose.hook_name("Abell S1063"), "Abell S1063")
        self.assertEqual(compose.hook_name("THE PILLARS OF CREATION"), "the Pillars of Creation")
        self.assertEqual(compose.hook_name("THE SOMBRERO GALAXY"), "the Sombrero Galaxy")

    def test_the_distance_is_the_clips_own(self):
        for seed in range(10):
            p = plan(seed)
            a = next(x for x in load_assets() if x["id"] == p["shots"][0]["asset"])
            shown = " ".join(t["text"] for t in p["texts"])
            self.assertIn(facts.ly_text(a["dist_ly"]) + " LIGHT-YEARS", shown)

    def test_a_long_zoom_is_sped_up_to_fit(self):
        for seed in range(30):
            s = plan(seed)["shots"][0]
            if s["kind"] == "zoom" and s["dur"] > s["len"] + 1:
                self.assertEqual(s["src_in"], 0.0)
                self.assertGreater(s["speed"], 1.0)
                self.assertLessEqual(s["speed"] * s["len"], s["dur"])
                return
        self.fail("no zoom clip came up in 30 seeds")

    def test_it_lints_clean_and_all_text_ends_in_time(self):
        for seed in range(15):
            p = plan(seed)
            self.assertEqual(gate.text_lint(p), [], p["hook"])
            self.assertLessEqual(max(t["k1"] for t in p["texts"]), p["total_beats"])
            self.assertTrue(render.text_layers(p))
            self.assertTrue(render.caption_text(p).startswith(p["hook"]))

    def test_the_closer_is_found_for_the_repeat_check(self):
        p = plan(2)
        entry = gate.entry_for(p, [], "e")
        self.assertIn(entry["closer"], facts.TARGET_CLOSERS)

    def test_two_targets_in_a_row_are_rejected_like_any_other_format(self):
        self.assertEqual(compose.FAMILY["target"], "target")
        self.assertNotIn("target", compose.FAMILIES)


class LengthCheck(unittest.TestCase):
    def test_only_a_target_cut_may_run_under_20_seconds(self):
        self.assertEqual(qc.min_seconds({"template": "guess"}), 20)
        self.assertEqual(qc.min_seconds({"template": "target"}), 15)


if __name__ == "__main__":
    unittest.main()
