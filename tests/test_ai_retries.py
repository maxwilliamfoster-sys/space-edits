"""gate._ask_ai: the AI review survives a busy minute and never crashes on an odd reply."""
import json
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gate  # noqa: E402


def reply(status, payload=None, text="", headers=None):
    body = payload if payload is not None else {}
    return types.SimpleNamespace(status_code=status, text=text or json.dumps(body), headers=headers or {},
                                 json=lambda: body)


def verdict(ok=True):
    return reply(200, {"choices": [{"message": {"content": json.dumps({"ok": ok, "problems": []})}}]})


class Poster:
    def __init__(self, replies):
        self.replies, self.calls, self.slept = list(replies), 0, []

    def __call__(self, *a, **k):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def ask(self):
        return gate._ask_ai({}, "k", post=self, sleep=self.slept.append)


class AskAi(unittest.TestCase):
    def test_clean_reply(self):
        p = Poster([verdict()])
        self.assertEqual(p.ask()["ok"], True)
        self.assertEqual((p.calls, p.slept), (1, []))

    def test_rate_limit_waits_as_long_as_groq_says(self):
        p = Poster([reply(429, text="rate limit, per minute", headers={"retry-after": "41"}), verdict()])
        self.assertTrue(p.ask()["ok"])
        self.assertEqual(p.slept, [41.0])

    def test_wait_is_capped(self):
        p = Poster([reply(429, text="slow down", headers={"retry-after": "900"}), verdict()])
        p.ask()
        self.assertEqual(p.slept, [gate.AI_WAIT_CAP_S])

    def test_daily_quota_is_not_retried(self):
        p = Poster([reply(429, text="Rate limit reached ... tokens per day (TPD): Limit 200000")])
        self.assertIsNone(p.ask())
        self.assertEqual((p.calls, p.slept), (1, []))

    def test_reply_without_choices_is_asked_again_not_a_crash(self):
        p = Poster([reply(200, {"error": {"message": "odd"}}), verdict(False)])
        self.assertEqual(p.ask()["ok"], False)
        self.assertEqual(p.calls, 2)

    def test_dropped_connection_is_asked_again(self):
        p = Poster([ConnectionError("reset"), verdict()])
        self.assertTrue(p.ask()["ok"])

    def test_gives_up_after_every_try(self):
        p = Poster([reply(503, text="busy")] * gate.AI_TRIES)
        self.assertIsNone(p.ask())
        self.assertEqual(p.calls, gate.AI_TRIES)

    def test_a_bad_key_is_not_retried(self):
        p = Poster([reply(401, text="invalid api key")])
        self.assertIsNone(p.ask())
        self.assertEqual(p.calls, 1)


if __name__ == "__main__":
    unittest.main()


class Distances(unittest.TestCase):
    def test_thousands_always_get_commas(self):
        from spacepipe import facts
        self.assertEqual(facts.ly_text("7500"), "7,500")
        self.assertEqual(facts.ly_text("4,500"), "4,500")
        self.assertEqual(facts.ly_text("960"), "960")
        self.assertEqual(facts.ly_text("2.8  billion"), "2.8 BILLION")
        self.assertEqual(facts.ly_text("200,000"), "200,000")


class Hooks(unittest.TestCase):
    """Opening lines: enough of them, all pass the wording check, and none repeats within 20 posts."""

    def pools(self):
        from spacepipe import facts
        return {"zoom": facts.HOOKS, **facts.FORMAT_HOOKS,
                "target": [h.format(name="the Orion Nebula") for h in facts.TARGET_HOOKS]}

    def test_every_hook_passes_the_lint_and_no_pool_repeats_itself(self):
        for name, hooks in self.pools().items():
            self.assertEqual(len(set(hooks)), len(hooks), name)
            for h in hooks:
                self.assertEqual(gate.text_lint({"hook": h, "hashtags": ["#space", "#universe"]}), [], h)

    def test_pools_are_big_enough_for_the_no_repeat_window(self):
        # back-to-back edits are never the same format, so one format fills at most half the window
        for name, hooks in self.pools().items():
            if name != "target":                      # target hooks carry the object's name, so they differ anyway
                self.assertGreater(len(hooks), gate.NO_REPEAT_HOOK // 2, name)

    def test_a_hook_used_in_the_last_twenty_edits_is_refused(self):
        from unittest import mock
        plan = {"template": "t9", "music": {"title": "m"}, "hook": "Earth is not the main character.", "texts": [],
                "shots": [], "total_beats": 60}
        old = {"id": "x", "template": "other", "music": "n", "hook": plan["hook"], "closer": "c", "assets": [],
               "windows": [], "hashes": []}
        filler = [dict(old, id=str(i), hook=f"h{i}") for i in range(25)]
        with mock.patch("spacepipe.compose.FAMILY", {}):
            near = gate.novelty(plan, [], filler[:10] + [old] + filler[10:20])
            far = gate.novelty(plan, [], [old] + filler[:20])
        self.assertTrue(any("hook was used" in r for r in near))
        self.assertFalse(any("hook was used" in r for r in far))
