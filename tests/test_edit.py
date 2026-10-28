"""Tests for what an edit looks and sounds like: hook on screen from frame 0, music from its best section, scale order,
near-black clips skipped before rendering. Run: py -m unittest discover -s tests -v (no network, no rendering)."""
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate  # noqa: E402
from spacepipe import compose, music, render  # noqa: E402

SEEDS = range(9000, 9040)


def alpha_at(expr, t):
    """Evaluate a drawtext alpha expression (only if/lt/arithmetic are used) at time t.
    eval is safe here: the input is render.py's own generated expression (no user data) and builtins are removed."""
    return eval(expr.replace("if(", "_if("), {"__builtins__": {}}, {"_if": lambda c, a, b: a if c else b, "lt": lambda a, b: a < b, "t": t})


def plan(seed=9001, **kw):
    with mock.patch.object(compose, "history", return_value={"usage": {}, "edits": []}):
        return compose.plan(seed, **kw)


class HookOnScreen(unittest.TestCase):
    def test_hook_is_fully_visible_at_frame_zero_in_the_top_band(self):
        for seed in (9001, 9002, 9003):
            p = plan(seed)
            hook = [L for L in render.text_layers(p) if L.get("hook")]
            self.assertTrue(hook, "the hook must be drawn on screen")
            words = " ".join(L["text"] for L in hook)
            self.assertEqual(words.split(), p["hook"].upper().split())
            for L in hook:
                self.assertTrue(380 <= L["y"] <= 520, L)
                self.assertGreaterEqual(L["size"], 64)
                self.assertEqual(alpha_at(L["alpha"], 0.0), 1)
                self.assertEqual(alpha_at(L["alpha"], 0.1), 1)
                self.assertEqual(alpha_at(L["alpha"], p["T0"] + 20 * p["P"]), 0)   # gone long before the closer

    def test_first_headline_needs_no_fade_in(self):
        p = plan()
        first = [L for L in render.text_layers(p) if not L.get("hook") and L["t0"] == 0]
        self.assertTrue(first)
        for L in first:
            self.assertEqual(alpha_at(L["alpha"], 0.1), 1)

    def test_later_texts_still_fade_in(self):
        p = plan()
        later = [L for L in render.text_layers(p) if L["t0"] > 0]
        self.assertTrue(later)
        for L in later:
            self.assertEqual(alpha_at(L["alpha"], L["t0"] - 0.01), 0)

    def test_video_does_not_fade_in_from_black(self):
        p = plan()
        self.assertNotIn("fade=t=in", render.post_chain(p, "x6", 30.0, ["null"]))

    def test_ai_review_is_told_the_hook_is_expected(self):
        self.assertIn("hook", gate.AI_PROMPT.lower())


class MusicStart(unittest.TestCase):
    def test_every_plan_starts_the_music_inside_the_track(self):
        starts = []
        for seed in SEEDS:
            p = plan(seed)
            m = p["music"]
            t_end = p["T0"] + 60 * p["P"]
            self.assertIn("start", m)
            self.assertLess(m["start"] + t_end + 2, m["dur"], m["title"])
            if m["mode"] == "beat" and m["start"]:
                self.assertAlmostEqual(m["start"] / m["P"], round(m["start"] / m["P"]), places=6)   # cuts stay on the beat
            starts.append(m["start"])
        self.assertGreater(sum(1 for s in starts if s > 0), len(starts) * 0.8, "most edits must skip the quiet intro")

    def test_offsets_vary_between_seeds_for_the_same_track(self):
        got = {plan(seed, track="Mesmerize")["music"]["start"] for seed in SEEDS}
        self.assertGreater(len(got), 1)

    def test_same_seed_same_start(self):
        self.assertEqual(plan(9005)["music"]["start"], plan(9005)["music"]["start"])

    def test_plans_use_the_cached_envelopes_not_a_decode(self):
        music.envelope.cache_clear()
        with mock.patch("sound_sync.envelope", side_effect=AssertionError("decoded a track in the planner")):
            for seed in SEEDS:
                plan(seed)

    def test_render_trims_the_music_from_the_chosen_start(self):
        p = plan()
        p["music"]["start"] = 42.5
        af = render.audio_filter(p, 30.0)
        self.assertIn("atrim=start=42.50:end=72.50", af)
        self.assertIn("asetpts=PTS-STARTPTS", af)
        self.assertLess(af.index("asetpts"), af.index("afade"))

    def test_best_section_beats_the_intro_on_a_track_with_a_quiet_start(self):
        env = [0.05] * 40 + [0.3] * 200 + [1.0] * 100 + [0.2] * 100       # 10 s near-silent intro, a build, a loud section
        t_end, climax = 30.0, 22.0
        start = music.pick_start(dict(file="x.mp3", title="x", mode="ambient", P=0.5, dur=len(env) * 0.25), t_end, climax,
                                 random.Random(1), env=env)
        self.assertGreater(start, 10)


class MusicSource(unittest.TestCase):
    def test_only_cc_by_kevin_macleod_tracks_no_trending_or_commercial_sounds(self):
        for t in music.usable(music.pool()):
            self.assertIn("Kevin MacLeod (incompetech.com), CC BY 4.0", t["credit"], t["title"])

    def test_scheduled_runs_mix_our_own_music(self):
        wf = (Path(__file__).resolve().parent.parent / ".github/workflows/post.yml").read_text(encoding="utf-8")
        self.assertNotIn("SOUND_MODE", wf)   # default 'own'; the TikTok-sound slot (audit #10) is opt-in only


class ScaleOrder(unittest.TestCase):
    def test_templates_only_ever_zoom_out(self):
        for name, slots in compose.TEMPLATES.items():
            names = [s for s, _ in slots]
            if "planet" in names and "sun" in names:
                self.assertLess(names.index("planet"), names.index("sun"), f"{name}: Jupiter/Saturn after the Sun")
            if "galaxy" in names:
                self.assertLess(names.index("mw"), names.index("galaxy"), f"{name}: another galaxy before our own")
            ranks = [compose.SCALE_RANK[s] for s in names]
            self.assertEqual(ranks, sorted(ranks), name)

    def test_the_milky_way_zoom_never_ends_on_something_inside_it(self):
        for seed in SEEDS:
            p = plan(seed)
            z = next((s for s in p["shots"] if s["slot"] == "galaxy_zoom"), None)
            if z is None:   # one of the other formats (compose.FORMATS)
                continue
            if z["tier"] != "galaxy":
                self.assertGreaterEqual(compose.ly_value(z["dist_ly"]), 1e6, (z["asset"], z["dist_ly"]))

    def test_nebula_slots_are_inside_our_galaxy(self):
        for seed in SEEDS:
            for s in plan(seed)["shots"]:
                if s["slot"] == "nebula" and s["dist_ly"]:
                    self.assertLess(compose.ly_value(s["dist_ly"]), 1e5, (s["asset"], s["dist_ly"]))

    def test_ly_value(self):
        self.assertEqual(compose.ly_value("650"), 650)
        self.assertEqual(compose.ly_value("5,500"), 5500)
        self.assertEqual(compose.ly_value("30 million"), 30e6)
        self.assertEqual(compose.ly_value("6.3 billion"), 6.3e9)
        self.assertIsNone(compose.ly_value(None))


class NearBlackClips(unittest.TestCase):
    def asset(self, i, ys):
        return dict(id=f"d{i}", tier="deep", kind="pan", src="esa", luma=ys)

    def test_dark_clips_are_never_picked(self):
        dark = self.asset(1, [2, 3, 2, 30, 3, 2])
        bright = self.asset(2, [30, 28, 31, 30, 27, 29])
        for seed in range(200):
            a = compose.pick_asset("deep", [dark, bright], set(), {}, random.Random(seed))
            self.assertEqual(a["id"], "d2")

    def test_unprobed_clips_are_still_allowed(self):
        a = dict(id="u", tier="deep", kind="pan", src="esa")
        self.assertEqual(compose.pick_asset("deep", [a], set(), {}, random.Random(0))["id"], "u")

    def test_one_dim_frame_is_tolerated(self):
        self.assertTrue(compose.bright_enough(dict(luma=[4, 30, 31, 30, 29, 30])))
        self.assertFalse(compose.bright_enough(dict(luma=[4, 5, 31, 30, 29, 30])))

    def test_probe_results_reach_the_assets(self):
        from spacepipe.lib import load_assets
        probed = [a for a in load_assets() if a.get("luma")]
        self.assertGreater(len(probed), 100)


if __name__ == "__main__":
    unittest.main()
