"""Tests for the upload gate, QC, slot scheduling and media pruning. Run: py -m unittest discover -s tests -v
Videos are tiny synthetic ffmpeg clips (moving / black / frozen / silent); nothing touches the real library or the network."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ci_run  # noqa: E402
import gate  # noqa: E402
import publish  # noqa: E402
from spacepipe import qc  # noqa: E402
from spacepipe.lib import FFMPEG  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="gate_tests_"))
SOURCES = {
    "moving": "testsrc2=s=270x480:r=30:d=20",
    "black": "color=c=black:s=270x480:r=30:d=20",
    "frozen": "color=c=0x50607a:s=270x480:r=30:d=20",
}


def make_video(kind, audio=True, name=None):
    """A 20 s clip in its own folder with a passing qc.json. audio: True = a tone, False = digital silence."""
    folder = TMP / (name or f"{kind}_{'tone' if audio else 'silent'}")
    folder.mkdir(parents=True, exist_ok=True)
    src = ["-f", "lavfi", "-i", "sine=f=330:r=48000:d=20" if audio else "anullsrc=r=48000:cl=stereo"]
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", SOURCES[kind], *src, "-t", "20", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", str(folder / "video.mp4")]
    subprocess.run(cmd, check=True)
    (folder / "qc.json").write_text(json.dumps({"ok": True, "checks": {}}), encoding="utf-8")
    return folder


def plan(assets=("new1", "new2"), windows=(), template="timemachine", music="New song", hook="New hook about Earth", closer="NEW CLOSER"):
    # the hook must pass gate.text_lint (hook_lint: a concrete specific such as Earth), as every real hook does
    """Defaults are all different from entry()'s, so a test only trips the one rule it overrides."""
    shots = [dict(asset=a, src="esa", src_in=0) for a in assets] + [dict(asset=a, src="nasa", src_in=t) for a, t in windows]
    return dict(template=template, music=dict(title=music), hook=hook, shots=shots, texts=[dict(text=closer, k1=60)])


def entry(eid="old", hashes=(), assets=("old1", "old2"), windows=(), template="classic", music="Old song", hook="Old hook", closer="OLD CLOSER"):
    return gate.entry_for(plan(assets, windows, template, music, hook, closer), list(hashes), eid)


class NoveltyRules(unittest.TestCase):
    def test_fresh_edit_passes(self):
        self.assertEqual(gate.novelty(plan(), [], [entry()]), [])

    def test_clip_reuse_within_the_last_n_edits_is_rejected_and_the_next_one_back_is_allowed(self):
        n = gate.NO_REPEAT_CLIPS
        old = [entry(f"e{i}", assets=(f"c{i}",)) for i in range(n + 5)]
        newest, oldest_banned, first_free = len(old) - 1, len(old) - n, len(old) - n - 1
        self.assertTrue(gate.novelty(plan(assets=(f"c{newest}",)), [], old))
        self.assertTrue(gate.novelty(plan(assets=(f"c{oldest_banned}",)), [], old))
        self.assertFalse([r for r in gate.novelty(plan(assets=(f"c{first_free}",)), [], old) if r.startswith("clip")])

    def test_same_nasa_reel_window_too_close_is_rejected(self):
        log = [entry(windows=[("reel", 100)])]
        self.assertTrue(gate.novelty(plan(windows=[("reel", 110)]), [], log))        # 10 s apart
        self.assertFalse(gate.novelty(plan(windows=[("reel", 130)]), [], log))       # 30 s apart
        self.assertFalse(gate.novelty(plan(windows=[("other_reel", 100)]), [], log))

    def test_template_and_music_combo_repeat_is_rejected_but_either_alone_is_fine(self):
        log = [entry(template="classic", music="Song A")]
        self.assertTrue(gate.novelty(plan(template="classic", music="Song A"), [], log))
        combo = lambda p: [r for r in gate.novelty(p, [], log) if r.startswith("template+music")]
        self.assertFalse(combo(plan(template="classic", music="Song B")))
        self.assertFalse(combo(plan(template="tour", music="Song A")))

    def test_two_edits_in_a_row_never_share_a_format(self):
        log = [entry(template="classic")]
        same = lambda t: [r for r in gate.novelty(plan(template=t), [], log) if r.startswith("same format")]
        self.assertTrue(same("tour"))            # both zoom-outs
        self.assertFalse(same("countdown"))
        log.append(entry("e2", template="countdown"))
        self.assertFalse(same("classic"))        # only the previous edit counts

    def test_hook_and_closer_pair_repeat_is_rejected(self):
        log = [entry(hook="Hook A", closer="CLOSER A")]
        self.assertTrue(gate.novelty(plan(hook="Hook A", closer="CLOSER A"), [], log))
        self.assertFalse(gate.novelty(plan(hook="Hook A", closer="CLOSER B"), [], log))

    def test_look_alike_frames_are_rejected(self):
        h = [0, 1 << 63, 12345, 99999, 2 ** 40, 7]
        near = [x ^ 0b11 for x in h]                                                  # 2 bits different each
        self.assertTrue(gate.novelty(plan(), near, [entry(hashes=h)]))
        self.assertFalse(gate.novelty(plan(), [x ^ (2 ** 64 - 1) for x in h], [entry(hashes=h)]))   # all 64 bits different

    def test_batch_cannot_repeat_itself(self):
        """The 5 edits of one run are gated before any is posted, so earlier ones in the run are passed in as extra_log."""
        first = entry("first", assets=("shared",))
        p = plan(assets=("shared",))
        self.assertTrue(gate.novelty(p, [], [first]))
        self.assertEqual(gate.novelty(p, [], []), [])                                 # without extra_log it would have slipped through


class Approval(unittest.TestCase):
    def setUp(self):
        self.folder = TMP / "approval"
        self.folder.mkdir(exist_ok=True)
        (self.folder / "video.mp4").write_bytes(b"video-bytes")
        (self.folder / "gate.json").unlink(missing_ok=True)

    def approve(self, content=b"video-bytes"):
        (self.folder / "gate.json").write_text(json.dumps({"ok": True, "sha256": gate.sha256(self.folder / "video.mp4")}))

    def test_not_approved_without_gate_json(self):
        self.assertFalse(gate.approved(self.folder))
        with self.assertRaises(RuntimeError):
            gate.require(self.folder)

    def test_approved_when_hash_matches(self):
        self.approve()
        self.assertTrue(gate.approved(self.folder))
        gate.require(self.folder)

    def test_video_changed_after_approval_is_blocked(self):
        self.approve()
        (self.folder / "video.mp4").write_bytes(b"swapped!")
        self.assertFalse(gate.approved(self.folder))
        with self.assertRaises(RuntimeError):
            gate.require(self.folder)


class Quality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.moving = make_video("moving")
        cls.black = make_video("black")
        cls.frozen = make_video("frozen")
        cls.moving_silent = make_video("moving", audio=False)

    def q(self, folder, silent=False):
        return gate.quality(folder / "video.mp4", folder, gate._duration(folder / "video.mp4"), silent=silent)

    def test_normal_video_passes(self):
        self.assertEqual(self.q(self.moving), [])

    def test_black_video_is_rejected(self):
        self.assertTrue(any("near-black" in r for r in self.q(self.black)))

    def test_frozen_video_is_rejected(self):
        self.assertTrue(any("frozen" in r for r in self.q(self.frozen)))

    def test_silent_audio_is_rejected_unless_the_edit_is_meant_to_be_silent(self):
        self.assertTrue(any("silent" in r for r in self.q(self.moving_silent)))
        self.assertEqual(self.q(self.moving_silent, silent=True), [])

    def test_failed_qc_json_is_rejected(self):
        folder = TMP / "qcfail"
        folder.mkdir(exist_ok=True)
        (folder / "video.mp4").write_bytes((self.moving / "video.mp4").read_bytes())
        (folder / "qc.json").write_text(json.dumps({"ok": False, "checks": {"1080x1920": False, "30fps": True}}))
        self.assertTrue(any("1080x1920" in r for r in self.q(folder)))

    def test_qc_check_flags_wrong_resolution(self):
        res = qc.check(self.moving / "video.mp4")
        self.assertFalse(res["ok"])
        self.assertFalse(res["checks"]["1080x1920"])
        self.assertTrue(res["checks"]["length 20-60s"])              # a 20 s clip sits on the lower boundary and must count as inside


class AiReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = make_video("moving", name="ai")
        cls.video = cls.folder / "video.mp4"
        cls.dur = gate._duration(cls.video)

    def reply(self, content=None, status=200):
        r = mock.Mock(status_code=status)
        r.json.return_value = {"choices": [{"message": {"content": content}}]}
        return r

    def run_review(self, responses, env):
        with mock.patch.dict(os.environ, env, clear=False), mock.patch("gate.time.sleep"), \
                mock.patch("gate.requests.post", side_effect=responses) as post:
            return gate.ai_review(self.video, self.dur), post

    def test_no_key_skips_unless_required(self):
        env = {"GROQ_API_KEY": ""}
        self.assertIsNone(self.run_review([], env)[0])
        self.assertEqual(self.run_review([], {**env, "REQUIRE_AI": "1"})[0], [gate.AI_UNAVAILABLE])

    def test_ok_verdict_passes(self):
        res, _ = self.run_review([self.reply('{"ok": true, "problems": []}')], {"GROQ_API_KEY": "k"})
        self.assertEqual(res, [])

    def test_rejection_carries_the_reasons(self):
        res, _ = self.run_review([self.reply('{"ok": false, "problems": ["burned-in chart"]}')], {"GROQ_API_KEY": "k"})
        self.assertEqual(res, ["AI review: burned-in chart"])

    def test_verdict_inside_extra_text_is_still_parsed(self):
        res, _ = self.run_review([self.reply('Sure! {"ok": false, "problems": ["blur"]} done')], {"GROQ_API_KEY": "k"})
        self.assertEqual(res, ["AI review: blur"])

    def test_rate_limit_is_retried(self):
        res, post = self.run_review([self.reply(status=429), self.reply('{"ok": true}')], {"GROQ_API_KEY": "k"})
        self.assertEqual(res, [])
        self.assertEqual(post.call_count, 2)

    def test_persistent_failure_fails_closed_only_when_required(self):
        bad = [self.reply(status=429)] * 3
        self.assertIsNone(self.run_review(bad, {"GROQ_API_KEY": "k", "REQUIRE_AI": ""})[0])
        self.assertEqual(self.run_review(bad, {"GROQ_API_KEY": "k", "REQUIRE_AI": "1"})[0], [gate.AI_UNAVAILABLE])

    def test_garbage_reply_fails_closed_when_required(self):
        res, _ = self.run_review([self.reply("no json here")], {"GROQ_API_KEY": "k", "REQUIRE_AI": "1"})
        self.assertEqual(res, [gate.AI_UNAVAILABLE])


class WholeGate(unittest.TestCase):
    """gate.check end to end on a real clip, with the novelty log redirected to a temp file."""

    def setUp(self):
        self.log = TMP / "gate_log.json"
        self.log.unlink(missing_ok=True)
        self.patches = [mock.patch.object(gate, "LOG", self.log), mock.patch.dict(os.environ, {"GROQ_API_KEY": "", "REQUIRE_AI": ""})]
        for p in self.patches:
            p.start()
        self.folder = make_video("moving", name="whole")
        (self.folder / "gate.json").unlink(missing_ok=True)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_pass_writes_approval_and_record_feeds_the_next_check(self):
        p = plan()
        res = gate.check(self.folder, p, "w1")
        self.assertTrue(res["ok"], res["reasons"])
        gate.require(self.folder)
        gate.record(p, self.folder, "w1")
        res2 = gate.check(self.folder, p, "w2")                          # identical plan + frames: must now be a repeat
        self.assertFalse(res2["ok"])
        self.assertFalse((self.folder / "gate.json").exists())           # a rejection revokes any earlier approval
        with self.assertRaises(RuntimeError):
            gate.require(self.folder)

    def test_extra_log_blocks_a_repeat_within_one_run(self):
        p = plan()
        first = gate.check(self.folder, p, "w1")
        self.assertTrue(first["ok"])
        pending = gate.pending_entry(p, self.folder, "w1")
        second = gate.check(self.folder, p, "w2", extra_log=[pending])
        self.assertFalse(second["ok"])

    def test_black_video_never_gets_approval(self):
        black = make_video("black", name="whole_black")
        self.assertFalse(gate.check(black, plan(), "b1")["ok"])
        self.assertFalse(gate.approved(black))


class PreRender(unittest.TestCase):
    """A plan that breaks a novelty rule must be dropped before the minutes-long render."""

    def test_rejected_plan_is_never_rendered(self):
        import spacepipe_make
        with mock.patch.object(spacepipe_make.compose, "plan", return_value=plan()),                 mock.patch.object(spacepipe_make.render, "render") as render:
            with self.assertRaises(spacepipe_make.PlanRejected):
                spacepipe_make.make_one(1, None, None, precheck=lambda p: ["clip a1 was used in one of the last 10 edits"])
            render.assert_not_called()

    def test_run_batch_skips_a_rejected_plan_without_crashing_or_touching_history(self):
        import spacepipe_make
        with mock.patch.object(spacepipe_make.compose, "plan", return_value=plan()),                 mock.patch.object(spacepipe_make, "save_history") as save:
            self.assertEqual(spacepipe_make.run_batch(2, base=1, precheck=lambda p: ["repeat"]), [])
            save.assert_not_called()


class Capacity(unittest.TestCase):
    def test_a_full_day_of_edits_stays_possible_for_ten_days(self):
        """Real planner + real novelty rules, no rendering. The old thresholds (15 clips / 90 s / 30 reels) fail this on day 3 at 5 a day."""
        from spacepipe import compose
        per_day = len(ci_run.SLOT_HOURS_UTC)
        log, seed, worst = [], 7000, 0
        with mock.patch.object(compose, "history", return_value={"usage": {}, "edits": []}):
            for day in range(10):
                made, tried = [], 0
                while len(made) < per_day and tried < ci_run.MAX_PLANS:
                    seed, tried = seed + 1, tried + 1
                    p = compose.plan(seed)
                    if not gate.novelty(p, [], log + made):
                        made.append(gate.entry_for(p, [], f"s{seed}"))
                self.assertEqual(len(made), per_day, f"day {day + 1}: only {len(made)} edits possible in {tried} plans")
                log += made
                worst = max(worst, tried)
        self.assertLess(worst, ci_run.MAX_PLANS)


class Slots(unittest.TestCase):
    NOW = datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc)

    def test_three_posts_a_day_at_12_17_22_utc(self):
        taken = []
        for _ in range(6):
            taken.append(ci_run.next_slot(taken, self.NOW))
        days = {}
        for t in taken:
            days.setdefault(t.date(), []).append(t.hour)
        self.assertEqual(sorted(days), [datetime(2026, 10, 21).date(), datetime(2026, 10, 22).date()])
        self.assertTrue(all(h == [12, 17, 22] for h in days.values()), days)

    def test_slot_taken_by_a_waiting_post_is_skipped(self):
        busy = [datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)]
        self.assertEqual(ci_run.next_slot(busy, self.NOW).hour, 17)

    def test_never_schedules_in_the_past_or_within_15_minutes(self):
        now = datetime(2026, 10, 21, 11, 50, tzinfo=timezone.utc)
        self.assertEqual(ci_run.next_slot([], now).hour, 17)             # 12:00 is only 10 min away

    def test_old_two_hourly_posts_still_waiting_push_new_ones_to_free_slots(self):
        """Posts queued before the cut (2 h apart) are left alone; a new slot must keep MIN_GAP_S from each of them."""
        busy = [datetime(2026, 10, 21, h, 0, tzinfo=timezone.utc) for h in (11, 13, 15, 17, 19, 21, 23)]
        got = ci_run.next_slot(busy, self.NOW)
        self.assertEqual((got.day, got.hour), (22, 12))

    def test_queue_holds_two_days_of_posts_and_a_run_makes_one_day(self):
        self.assertEqual(ci_run.SLOT_HOURS_UTC, (12, 17, 22))
        self.assertEqual(ci_run.QUEUE_TARGET, 6)
        with mock.patch.dict(os.environ, {"EDITS_PER_RUN": ""}):
            import importlib
            self.assertEqual(importlib.reload(ci_run).EDITS_PER_RUN, 3)

    def test_cron_runs_twice_a_day(self):
        wf = (Path(__file__).resolve().parent.parent / ".github/workflows/post.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '23 6,16 * * *'", wf)
        self.assertNotIn("|| '10'", wf)


class NoveltyWindows(unittest.TestCase):
    def test_windows_cover_days_not_hours_at_three_a_day(self):
        per_day = len(ci_run.SLOT_HOURS_UTC)
        self.assertGreaterEqual(gate.NO_REPEAT_CLIPS / per_day, 3)
        self.assertGreaterEqual(gate.NO_REPEAT_REELS / per_day, 3)
        self.assertGreaterEqual(gate.NO_REPEAT_COMBO / per_day, 3)
        self.assertGreaterEqual(gate.NO_REPEAT_TEXT / per_day, 2)

    def test_planning_stays_cheap_next_to_rendering(self):
        self.assertLessEqual(ci_run.MAX_PLANS, 5000)   # ~20 plans/s, so at most a few minutes of a 200-minute budget


class MediaPruning(unittest.TestCase):
    def test_scheduled_videos_are_never_deleted(self):
        present = [f"v{i}.mp4" for i in range(12)]
        delete, order = publish.prune_plan(present, present, "new.mp4", protect=present[:11], keep=12)
        self.assertNotIn("v0.mp4", delete)
        self.assertTrue(set(delete) <= {"v11.mp4"})
        self.assertEqual(order[-1], "new.mp4")

    def test_oldest_unprotected_go_first(self):
        present = [f"v{i}.mp4" for i in range(12)]
        delete, _ = publish.prune_plan(present, present, "new.mp4", protect=[], keep=12)
        self.assertEqual(delete, ["v0.mp4"])        # 12 stored + the new one would be 13; keep=12, so exactly the oldest goes


if __name__ == "__main__":
    unittest.main()
