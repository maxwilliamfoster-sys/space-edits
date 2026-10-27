"""Buffer calls go through growth-lab S7 buffer_client, and each run records what happened to past-due posts in posted.json
(sent / error), so a failed post no longer vanishes from the queue count silently (audit 2026-10-21 #7, review space-11).
Fake transport only: no Buffer call, no quota. Run: py -m unittest discover -s tests -v."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import buffer_client  # noqa: E402
import ci_run  # noqa: E402
import publish  # noqa: E402

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)
RL = [("RateLimit", '"100-in-15min";r=97;t=800'), ("RateLimit-Policy", '"100-in-15min";q=100;w=900'),
      ("RateLimit", '"250-in-24hr";r=240;t=80000'), ("RateLimit-Policy", '"250-in-24hr";q=250;w=86400')]


class Fake:
    """Buffer transport stand-in: `reply(body) -> (status, data_or_errors)`; records every request body."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, url, body, headers, timeout):
        self.calls.append(body)
        status, payload = self.reply(body)
        return status, RL, json.dumps(payload)


def post_reply(states):
    """Answer aliased post(input:) queries from {post id: node or None (not found)}."""
    def reply(body):
        data, errors = {}, []
        for name, val in body["variables"].items():
            node = states.get(val["id"])
            alias = "post" if name == "i" else "p" + name[1:]
            if node is None:
                errors.append({"message": f"Post {val['id']} not found"})
            data[alias] = node
        return 200, ({"data": data, "errors": errors} if errors else {"data": data})
    return reply


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"BUFFER_API_KEY": "test-key"})
        self.env.start()
        publish.reset_client()

    def tearDown(self):
        publish.reset_client()
        self.env.stop()

    def use(self, fake):
        publish._client = buffer_client.BufferClient(api_key="test-key", transport=fake, log=lambda *_: None)
        return fake


class Client(Base):
    def test_gql_goes_through_buffer_client_and_reads_the_quota(self):
        fake = self.use(Fake(lambda b: (200, {"data": {"ok": 1}})))
        self.assertEqual(publish._gql("query { ok }"), {"ok": 1})
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(publish.client().remaining()["15m"]["remaining"], 97)

    def test_rate_limit_is_its_own_error_never_an_empty_answer(self):
        self.use(Fake(lambda b: (429, {"errors": [{"message": "slow down", "extensions": {"code": "RATE_LIMIT_EXCEEDED", "window": "24h"}}]})))
        with self.assertRaises(buffer_client.BufferRateLimited):
            publish._gql("query { ok }")

    def test_other_buffer_errors_become_publish_errors(self):
        self.use(Fake(lambda b: (200, {"errors": [{"message": "bad input"}]})))
        with self.assertRaises(publish.PublishError):
            publish._gql("query { ok }")

    def test_missing_key_is_a_publish_error(self):
        with mock.patch.dict(os.environ, {"BUFFER_API_KEY": ""}):
            publish.reset_client()
            with self.assertRaises(publish.PublishError):
                publish._gql("query { ok }")

    def test_schedule_tiktok_sends_the_same_create_post(self):
        fake = self.use(Fake(lambda b: (200, {"data": {"createPost": {"post": {"id": "p1", "status": "scheduled", "dueAt": "x"}}}})))
        due = datetime.now(timezone.utc) + timedelta(hours=3)
        post = publish.schedule_tiktok("chan", "https://x/v.mp4", "cap", due)
        self.assertEqual(post["id"], "p1")
        inp = fake.calls[0]["variables"]["i"]
        self.assertEqual((inp["channelId"], inp["mode"], inp["schedulingType"]), ("chan", "customScheduled", "automatic"))
        self.assertIn("createPost", fake.calls[0]["query"])


class Statuses(Base):
    def test_one_call_for_many_posts(self):
        states = {f"id{k}": {"id": f"id{k}", "status": "sent"} for k in range(5)}
        fake = self.use(Fake(post_reply(states)))
        got = publish.post_statuses(list(states))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(set(got), set(states))
        self.assertIn("p4: post(input: $i4)", fake.calls[0]["query"])

    def test_chunks_of_twenty(self):
        states = {f"id{k}": {"id": f"id{k}", "status": "sent"} for k in range(25)}
        fake = self.use(Fake(post_reply(states)))
        self.assertEqual(len(publish.post_statuses(list(states))), 25)
        self.assertEqual(len(fake.calls), 2)

    def test_a_deleted_post_does_not_hide_the_others(self):
        states = {"a": {"id": "a", "status": "sent"}, "gone": None, "b": {"id": "b", "status": "error", "error": {"message": "x"}}}
        self.use(Fake(post_reply(states)))
        got = publish.post_statuses(["a", "gone", "b"])
        self.assertEqual(got["a"]["status"], "sent")
        self.assertEqual(got["b"]["status"], "error")
        self.assertIsNone(got["gone"])


class Reconcile(Base):
    def posted(self):
        return {
            "e_sent": dict(post="a", due="2026-10-23T12:00:00Z", file="space_e_sent.mp4", mode="own"),
            "e_err": dict(post="b", due="2026-10-22T22:00:00Z", file="space_e_err.mp4", mode="own", status="scheduled"),
            "e_wait": dict(post="c", due="2026-10-23T17:50:00Z", file="space_e_wait.mp4", mode="own"),   # inside the grace
            "e_future": dict(post="d", due="2026-10-24T12:00:00Z", file="space_e_future.mp4", mode="own"),
            "e_done": dict(post="e", due="2026-10-21T21:00:00Z", file="space_e_done.mp4", mode="own", status="sent"),
            "e_gone": dict(post="f", due="2026-10-21T23:00:00Z", file="space_e_gone.mp4", mode="own"),
        }

    def states(self):
        return {"a": {"id": "a", "status": "sent", "sentAt": "2026-10-23T12:00:05Z", "externalLink": "https://www.tiktok.com/@mcfiddler.bob/video/1"},
                "b": {"id": "b", "status": "error", "error": {"message": "TikTok could not fetch the video"}},
                "f": None}

    def test_past_due_posts_get_sent_or_error(self):
        fake = self.use(Fake(post_reply(self.states())))
        posted, events = self.posted(), []
        changed = ci_run.reconcile(posted, now=NOW, emit=lambda *a, **k: events.append(a))
        self.assertTrue(changed)
        self.assertEqual(posted["e_sent"]["status"], "sent")
        self.assertEqual(posted["e_sent"]["sent_at"], "2026-10-23T12:00:05Z")
        self.assertEqual(posted["e_sent"]["link"], "https://www.tiktok.com/@mcfiddler.bob/video/1")
        self.assertEqual(posted["e_err"]["status"], "error")
        self.assertEqual(posted["e_err"]["error"], "TikTok could not fetch the video")
        self.assertEqual(posted["e_gone"]["status"], "missing")
        for k in ("e_wait", "e_future"):
            self.assertNotIn("status", posted[k])
        asked = {v["id"] for c in fake.calls for v in c["variables"].values()}
        self.assertEqual(asked, {"a", "b", "f"})          # never the future, in-grace or already-final ones
        kinds = [e[0] for e in events]
        self.assertEqual(sorted(kinds), ["post_failed", "post_failed", "posted"])
        failed = {e[2]: e[1] for e in events if e[0] == "post_failed"}
        self.assertEqual(failed, {"Space: Buffer failed to post e_err": "error",
                                  "Space: Buffer post for e_gone no longer exists": "warning"})

    def test_second_run_asks_nothing_and_does_not_re_alert(self):
        self.use(Fake(post_reply(self.states())))
        posted = self.posted()
        ci_run.reconcile(posted, now=NOW, emit=lambda *a, **k: None)
        fake = self.use(Fake(post_reply(self.states())))
        events = []
        self.assertFalse(ci_run.reconcile(posted, now=NOW, emit=lambda *a, **k: events.append(a)))
        self.assertEqual(fake.calls, [])
        self.assertEqual(events, [])

    def test_still_scheduled_after_due_is_recorded_and_asked_again_later(self):
        self.use(Fake(post_reply({"a": {"id": "a", "status": "sending"}})))
        posted = {"x": dict(post="a", due="2026-10-23T12:00:00Z", file="f.mp4", mode="own")}
        ci_run.reconcile(posted, now=NOW, emit=lambda *a, **k: None)
        self.assertEqual(posted["x"]["status"], "sending")
        fake = self.use(Fake(post_reply({"a": {"id": "a", "status": "sent"}})))
        ci_run.reconcile(posted, now=NOW, emit=lambda *a, **k: None)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(posted["x"]["status"], "sent")

    def test_rate_limit_changes_nothing_and_does_not_raise(self):
        self.use(Fake(lambda b: (429, {"errors": [{"message": "x", "extensions": {"code": "RATE_LIMIT_EXCEEDED", "window": "15m"}}]})))
        posted = self.posted()
        before = json.dumps(posted, sort_keys=True)
        self.assertFalse(ci_run.reconcile(posted, now=NOW, emit=lambda *a, **k: None))
        self.assertEqual(json.dumps(posted, sort_keys=True), before)

    def test_buffer_outage_marks_nothing_missing(self):
        # review 2026-10-27: an HTTP 5xx (or 401) used to fall through to the one-by-one retry, every id failed the same
        # way and was stored as None -> status "missing" (final, never asked again) plus a post_failed alert per post.
        for status in (502, 503, 401):
            self.use(Fake(lambda b, s=status: (s, {"message": "upstream unavailable"})))
            posted, events = self.posted(), []
            before = json.dumps(posted, sort_keys=True)
            self.assertFalse(ci_run.reconcile(posted, now=NOW, emit=lambda *a, **k: events.append(a)))
            self.assertEqual(json.dumps(posted, sort_keys=True), before, status)
            self.assertEqual(events, [], status)

    def test_at_most_max_posts_per_run(self):
        states = {f"p{k}": {"id": f"p{k}", "status": "sent"} for k in range(60)}
        fake = self.use(Fake(post_reply(states)))
        posted = {f"e{k}": dict(post=f"p{k}", due="2026-10-20T12:00:00Z", file="f", mode="own") for k in range(60)}
        ci_run.reconcile(posted, now=NOW, emit=lambda *a, **k: None)
        asked = sum(len(c["variables"]) for c in fake.calls)
        self.assertEqual(asked, ci_run.RECONCILE_MAX)

    def test_new_posts_are_recorded_as_scheduled(self):
        due = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        e = ci_run.posted_entry({"id": "p9"}, due, "space_x.mp4")
        self.assertEqual(e, dict(post="p9", due="2026-10-24T12:00:00Z", file="space_x.mp4", mode=ci_run.SOUND_MODE, status="scheduled"))

    def test_main_reconciles_before_making_edits(self):
        src = (Path(ci_run.__file__)).read_text(encoding="utf-8")
        body = src[src.index("def main"):]
        self.assertLess(body.index("reconcile(posted"), body.index("pending_posts("))


class QuotaWarningOnce(unittest.TestCase):
    """A standing low-quota condition warns once a day, not on every 3-a-day run (review 2026-10-27)."""

    def setUp(self):
        import tempfile
        self.path = Path(tempfile.mkdtemp()) / "alerts.json"
        patcher = mock.patch.object(ci_run, "ALERTS", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.t0 = NOW.timestamp()

    def test_first_low_warns_then_quiet_for_a_day(self):
        self.assertTrue(ci_run.alert_once("buffer_quota_low", "30d", now=self.t0))
        self.assertFalse(ci_run.alert_once("buffer_quota_low", "30d", now=self.t0 + 5 * 3600))
        self.assertTrue(ci_run.alert_once("buffer_quota_low", "30d", now=self.t0 + 86400))

    def test_a_newly_low_window_warns_at_once(self):
        ci_run.alert_once("buffer_quota_low", "30d", now=self.t0)
        self.assertTrue(ci_run.alert_once("buffer_quota_low", "24h,30d", now=self.t0 + 60))

    def test_recovery_clears_so_the_next_low_warns(self):
        ci_run.alert_once("buffer_quota_low", "30d", now=self.t0)
        self.assertFalse(ci_run.alert_once("buffer_quota_low", None, now=self.t0 + 60))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {})
        self.assertTrue(ci_run.alert_once("buffer_quota_low", "30d", now=self.t0 + 120))

    def test_no_condition_and_no_file_writes_nothing(self):
        self.assertFalse(ci_run.alert_once("buffer_quota_low", None, now=self.t0))
        self.assertFalse(self.path.exists())

    def test_the_workflow_commits_the_alert_state(self):
        wf = (Path(ci_run.__file__).parent / ".github/workflows/post.yml").read_text(encoding="utf-8")
        self.assertIn("library/alerts.json", wf)
        self.assertTrue((Path(ci_run.__file__).parent / "library/alerts.json").exists())   # git add -A fails on a missing path


if __name__ == "__main__":
    unittest.main()
