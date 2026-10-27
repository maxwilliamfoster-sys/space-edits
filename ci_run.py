"""Cloud entry point (GitHub Actions). Makes edits, runs each through the upload gate (quality + novelty), previews the ones that
pass on Telegram and, only when AUTO_POST=true, hosts them and schedules them on Buffer (mcfiddler.bob).
With AUTO_POST off nothing is ever posted: it renders, gates and previews.

SOUND_MODE=own     (default) our own music is mixed in and Buffer publishes automatically.
SOUND_MODE=tiktok  the edit is rendered with a silent audio track and scheduled as a Buffer "notification": at the slot time
                   Buffer pings the phone and you add a TikTok sound in the TikTok app (the API cannot attach TikTok sounds)."""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import app_events
import gate
import publish
import spacepipe_make
from spacepipe.lib import FFMPEG, LIB, ROOT, history

AUTO_POST = os.environ.get("AUTO_POST", "").strip().lower() == "true"
SOUND_MODE = os.environ.get("SOUND_MODE", "own").strip().lower()
EDITS_PER_RUN = int(os.environ.get("EDITS_PER_RUN") or 3)
MAX_TRIES = 120                   # safety cap on renders per run; the real limit is RUN_BUDGET_S, so a rejected edit is replaced until one passes
RUN_BUDGET_S = 200 * 60           # stop starting renders after this (job timeout is 240 min); the next cron tops the queue up
MAX_PLANS = 3000                  # plans per run (~20/s); a plan that breaks a novelty rule is dropped before rendering. At 3 a day
                                  # with 3-day windows a passing plan can take a few hundred draws (tests/test_gate.py Capacity)
QUEUE_TARGET = 6                  # never keep more than this many posts waiting in Buffer (two days at 3 a day)
# 3 a day (audit 2026-10-21: 10 near-identical template posts a day is the policy risk, not volume itself). 12/17/22 UTC =
# UK lunch / evening / late evening and US East 8am / 1pm / 6pm. Raise only after ~2 weeks of analytics show no suppression.
SLOT_HOURS_UTC = (12, 17, 22)
MIN_GAP_S = 5400                  # a slot must be this far from every post already waiting (old 2-hourly posts still in Buffer too)
POSTED = LIB / "posted.json"      # {edit id: {post, due, file, mode, status[, sent_at, link, error]}} (committed by the workflow)
FINAL = ("sent", "error", "missing")   # statuses that are never asked again
RECONCILE_GRACE_S = 1800          # ask about a post this long after its due time (TikTok takes 1-3 min to take it)
RECONCILE_MAX = 40                # posts asked per run: 2 calls of the shared Buffer quota at most (20 per call)


def telegram_text(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text[:3500]}, timeout=30)


def emit(kind, severity, title, body="", data=None):
    app_events.emit("space", kind, severity, title, body, data=data, fallback=telegram_text)


def load_posted():
    return json.loads(POSTED.read_text(encoding="utf-8")) if POSTED.exists() else {}


def save_posted(posted):
    POSTED.write_text(json.dumps(posted, indent=1), encoding="utf-8")


def posted_entry(post, due, fname):
    return dict(post=post["id"], due=due.isoformat().replace("+00:00", "Z"), file=fname, mode=SOUND_MODE, status="scheduled")


def reconcile(posted, now=None, emit=None):
    """Ask Buffer what happened to every post whose due time passed (plus a grace) and that has no final status yet, and
    record it in `posted` (status sent / error / missing, plus sent_at, link or error). A failed post is reported to Bot HQ
    as post_failed (once), a sent one as posted. Never raises: a Buffer problem here must not stop the run, it just leaves
    the entries to the next run. Returns True if `posted` changed."""
    now = now or datetime.now(timezone.utc)
    emit = emit or globals()["emit"]
    due = [(eid, v) for eid, v in posted.items()
           if v.get("post") and v.get("status") not in FINAL
           and datetime.fromisoformat(v["due"].replace("Z", "+00:00")) + timedelta(seconds=RECONCILE_GRACE_S) < now]
    due.sort(key=lambda kv: kv[1]["due"])
    due = due[:RECONCILE_MAX]
    if not due:
        return False
    try:
        got = publish.post_statuses([v["post"] for _, v in due])
    except Exception as e:   # BufferRateLimited, network, ...: try again next run
        print(f"[buffer] status check skipped: {type(e).__name__}: {str(e)[:200]}")
        return False
    changed = False
    for eid, v in due:
        node = got.get(v["post"], False)
        if node is False:
            continue
        status = "missing" if node is None else node.get("status") or "unknown"
        if status == v.get("status"):
            continue
        v["status"] = status
        changed = True
        if status == "sent":
            v["sent_at"] = node.get("sentAt")
            if node.get("externalLink"):
                v["link"] = node["externalLink"]
            emit("posted", "info", f"Space: {eid} posted on TikTok", node.get("externalLink") or f"Buffer post {v['post']}",
                 data={"edit": eid, "post": v["post"], "link": node.get("externalLink")})
        elif status == "error":
            v["error"] = ((node.get("error") or {}).get("message") or "unknown error")[:300]
            emit("post_failed", "error", f"Space: Buffer failed to post {eid}", f"Due {v['due']}: {v['error']}",
                 data={"edit": eid, "post": v["post"], "due": v["due"]})
        elif status == "missing":
            emit("post_failed", "warning", f"Space: Buffer post for {eid} no longer exists",
                 f"Due {v['due']}, post {v['post']} (deleted in Buffer?)", data={"edit": eid, "post": v["post"]})
    print("[buffer] post status: " + ", ".join(f"{eid}={v.get('status')}" for eid, v in due))
    return changed


def next_slot(taken, now=None):
    now = now or datetime.now(timezone.utc)
    for day in range(0, 30):
        for h in SLOT_HOURS_UTC:
            dt = (now + timedelta(days=day)).replace(hour=h, minute=0, second=0, microsecond=0)
            if dt > now + timedelta(minutes=15) and all(abs((dt - t).total_seconds()) > MIN_GAP_S for t in taken):
                return dt
    raise RuntimeError("no free slot in 30 days")


def telegram_preview(eid, folder, note):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[telegram] no token, skipping preview")
        return
    small = folder / "preview.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(folder / "video.mp4"), "-vf", "scale=540:960", "-c:v", "libx264",
                    "-crf", "30", "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k", str(small)], check=True)
    with open(small, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendVideo", data={"chat_id": chat, "caption": f"{eid}: {note}"[:1000],
                          "supports_streaming": "true", "width": 540, "height": 960}, files={"video": f}, timeout=300)
    print("[telegram]", r.status_code, r.json().get("ok"))


def main():
    org = chan = None
    if AUTO_POST:
        org, chan = publish.tiktok_channel()
        posted = load_posted()
        if reconcile(posted):   # sent / error for past-due posts, so a failed post is seen instead of silently refilled
            save_posted(posted)
        pending = publish.pending_posts(org, chan)
        low = publish.client().quota_low()
        if low:
            emit("buffer_quota_low", "warning", "Space: Buffer API quota is running low",
                 f"Under 15% left in: {', '.join(low)} (the key may be shared with other bots)", data=publish.client().remaining())
        need = min(EDITS_PER_RUN, QUEUE_TARGET - len(pending))
        print(f"[buffer] {len(pending)} post(s) waiting, making {max(need, 0)}")
        if need <= 0:
            return
    else:
        pending, need = [], EDITS_PER_RUN
    good, plans, batch_log, skipped = [], 0, [], []
    base = max([e["seed"] for e in history()["edits"]] + [0]) + 1

    def precheck(plan):   # plan-only rules (text lint; clips, reel windows, template+music, hook+closer novelty) against the log and this run's edits
        reasons = gate.plan_check(plan, gate.load_log() + batch_log)
        if reasons:
            skipped.append(reasons)
        return reasons

    started = time.monotonic()
    while (len(good) < need and plans - len(skipped) < MAX_TRIES and plans < MAX_PLANS
           and time.monotonic() - started < RUN_BUDGET_S):
        plans += 1
        for eid, plan, folder in spacepipe_make.run_batch(1, base=base + plans, silent=(SOUND_MODE == "tiktok"), precheck=precheck):
            res = gate.check(folder, plan, eid, extra_log=batch_log)
            if res["ok"]:
                good.append((eid, plan, folder))
                batch_log.append(gate.pending_entry(plan, folder, eid))
            elif gate.AI_UNAVAILABLE in res["reasons"]:   # every further edit would be rejected the same way: stop instead of burning renders
                emit("gate_unavailable", "error", "Space: the AI quality review could not run", "Stopped without posting. Check GROQ_API_KEY / Groq limits.")
                sys.exit("AI review unavailable and required")
            else:
                rej = ROOT / "out/rejected" / eid
                shutil.rmtree(rej, ignore_errors=True)
                shutil.move(str(folder), str(rej))
                emit("gate_rejected", "info", f"Space: edit {eid} rejected by the gate", "\n".join(res["reasons"]))
    if len(good) < need:
        emit("render_failed", "warning", "Space: could not make enough edits that pass the gate",
             f"{len(good)}/{need} passed after {plans - len(skipped)} renders ({len(skipped)} plans dropped before rendering)")
    if not good:
        sys.exit("no edit passed the gate")
    posted = load_posted()
    now = datetime.now(timezone.utc)
    taken = [datetime.fromisoformat(p["dueAt"].replace("Z", "+00:00")) for p in pending]
    protect = [v["file"] for v in posted.values() if datetime.fromisoformat(v["due"].replace("Z", "+00:00")) > now]
    for eid, plan, folder in good:
        if not AUTO_POST:
            telegram_preview(eid, folder, "passed the gate; preview only (AUTO_POST is off, not posted)")
            continue
        gate.require(folder)       # only a video this run's gate approved can be hosted
        caption = (folder / "caption.txt").read_text(encoding="utf-8")
        if len(caption) > 2200:
            caption = caption[:2190].rsplit(" ", 1)[0] + "..."
        due = datetime.now(timezone.utc) if os.environ.get("POST_NOW") == "true" else next_slot(taken)   # POST_NOW: publish immediately
        fname = f"space_{eid}.mp4"
        url = publish.host_video(folder / "video.mp4", fname, protect)
        post = publish.schedule_tiktok(chan, url, caption, due, notify=(SOUND_MODE == "tiktok"))
        taken.append(due)
        protect.append(fname)
        posted[eid] = posted_entry(post, due, fname)
        save_posted(posted)
        gate.record(plan, folder, eid)
        what = "notification (add a TikTok sound in the app)" if SOUND_MODE == "tiktok" else "auto-post"
        emit("scheduled", "info", f"Space: {eid} scheduled", f"{due:%a %d %b %H:%M} UTC on mcfiddler.bob, {what}",
             data={"edit": eid, "due": due.isoformat(), "mode": SOUND_MODE})
        telegram_preview(eid, folder, f"SCHEDULED {due:%a %d %b %H:%M} UTC on mcfiddler.bob ({what})")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        emit("run_failed", "error", "Space: run failed", f"{type(e).__name__}: {str(e)[:500]}")
        raise
