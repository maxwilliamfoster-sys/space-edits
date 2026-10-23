"""On-screen hook + caption lint (growth-lab S5 hook_lint, audit 2026-10-21 #2/#4): hook of 7 words or fewer with a concrete
specific, no greeting, no '...', no doubled words; 2-4 niche tags, never #fyp/#foryou. Run: py -m unittest discover -s tests -v."""
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate  # noqa: E402
import hook_lint  # noqa: E402
from spacepipe import compose, facts, render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def plan(seed):
    with mock.patch.object(compose, "history", return_value={"usage": {}, "edits": []}):
        return compose.plan(seed)


class Pools(unittest.TestCase):
    def test_every_hook_passes_the_lint(self):
        for h in facts.HOOKS:
            self.assertEqual(gate.text_lint({"hook": h}), [], h)

    def test_hooks_are_seven_words_or_fewer(self):
        for h in facts.HOOKS:
            self.assertLessEqual(hook_lint.word_count(h), gate.HOOK_MAX_WORDS, h)
        self.assertEqual(gate.HOOK_MAX_WORDS, 7)

    def test_every_tag_set_passes_and_has_no_fyp(self):
        for tags in facts.HASHTAGS:
            self.assertEqual([i for i in hook_lint.lint_tags(tags) if i.level == "fail"], [], tags)
            self.assertTrue(2 <= len(tags) <= 4, tags)
            self.assertFalse(any(t.lower().lstrip("#").startswith(("fyp", "foryou")) for t in tags), tags)

    def test_real_plans_and_captions_pass(self):
        for seed in range(9100, 9130):
            p = plan(seed)
            self.assertEqual(gate.text_lint(p, render.caption_text(p)), [], (seed, p["hook"], p["hashtags"]))


class CaptionCredits(unittest.TestCase):
    def test_credits_are_separated_so_neighbours_do_not_run_together(self):
        p = {"hook": "How small is Earth? Watch this.", "hashtags": ["#space", "#nasa"], "music": {"credit": "Kevin MacLeod"},
             "shots": [dict(credit="ESA/Hubble, NASA", src="esa"), dict(credit="NASA, ESA, CSA, STScI", src="esa")]}
        cap = render.caption_text(p)
        self.assertNotIn("NASA NASA", cap)
        self.assertIn("ESA/Hubble, NASA; NASA, ESA, CSA, STScI", cap)
        self.assertNotIn("\n", cap.split("\n\n", 1)[1])          # credits stay one paragraph after the blank line
        self.assertEqual(gate.text_lint(p, cap), [])


class Rejections(unittest.TestCase):
    def test_vague_hook_fails(self):
        r = gate.text_lint({"hook": "you are smaller than you think."})
        self.assertTrue(any("hook_no_specific" in x for x in r), r)

    def test_long_hook_fails(self):
        r = gate.text_lint({"hook": "This is how big the whole universe really is, honestly."})
        self.assertTrue(any("hook_too_long" in x for x in r), r)

    def test_fyp_tag_fails(self):
        r = gate.text_lint({"hook": "How small is Earth? Watch this.", "hashtags": ["#space", "#nasa", "#fyp"]})
        self.assertTrue(any("tags_spam" in x for x in r), r)

    def test_doubled_word_in_caption_fails(self):
        r = gate.text_lint({"hook": "How small is Earth? Watch this."}, "How small is Earth?\n#space #nasa\n\nCredits the the end")
        self.assertTrue(any("doubled_word" in x for x in r), r)

    def test_reasons_are_prefixed_for_the_gate_log(self):
        r = gate.text_lint({"hook": "hello guys, in this video..."})
        self.assertTrue(r and all(x.startswith("text lint: ") for x in r), r)


class Wiring(unittest.TestCase):
    def test_plan_check_drops_bad_text_before_rendering(self):
        p = plan(9101)
        self.assertEqual(gate.plan_check(p, []), [])
        bad = dict(p, hook="you are smaller than you think.")
        self.assertTrue(gate.plan_check(bad, []))

    def test_ci_precheck_uses_plan_check(self):
        src = (ROOT / "ci_run.py").read_text(encoding="utf-8")
        self.assertIn("gate.plan_check(plan,", src)

    def test_gate_check_rejects_a_bad_caption(self):
        tmp = ROOT / "out" / "test_hook_lint"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            (tmp / "video.mp4").write_bytes(b"x")
            (tmp / "caption.txt").write_text("How small is Earth?\n#space #fyp\n\ncredits", encoding="utf-8")
            p = dict(plan(9102), hashtags=["#space", "#fyp"])
            with mock.patch.object(gate, "_duration", return_value=30.0), mock.patch.object(gate, "frame_hashes", return_value=[]), \
                    mock.patch.object(gate, "quality", return_value=[]), mock.patch.object(gate, "ai_review", return_value=None), \
                    mock.patch.object(gate, "load_log", return_value=[]), mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
                res = gate.check(tmp, p, "t1")
            self.assertFalse(res["ok"])
            self.assertTrue(any("tags_spam" in r for r in res["reasons"]), res["reasons"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
