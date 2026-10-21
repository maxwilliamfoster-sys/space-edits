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
from datetime import datetime, timedelta, timezone

import requests

import app_events
import gate
import publish
import spacepipe_make
from spacepipe.lib import FFMPEG, LIB, ROOT, history

AUTO_POST = os.environ.get("AUTO_POST", "").strip().lower() == "true"
SOUND_MODE = os.environ.get("SOUND_MODE", "own").strip().lower()
EDITS_PER_RUN = int(os.environ.get("EDITS_PER_RUN") or 10)
MAX_TRIES = 32                    # renders per run (each is a few minutes of CI time); gate rejections eat into this
MAX_PLANS = 600                   # plans per run; a plan that breaks a novelty rule is dropped before rendering (costs milliseconds)
QUEUE_TARGET = 10                 # never keep more than this many posts waiting in Buffer (its free-plan cap; one day at 10 a day)
SLOT_HOURS_UTC = (5, 7, 9, 11, 13, 15, 17, 19, 21, 23)   # 10 a day, 2 h apart: 6am to midnight UK in BST (one hour earlier in GMT)
MIN_GAP_S = 5400                  # a slot must be this far from every post already waiting (slots are 2 h apart)
POSTED = LIB / "posted.json"      # {edit id: {post, due, file}} (committed by the workflow)


def telegram_text(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text[:3500]}, timeout=30)


def emit(kind, severity, title, body="", data=None):
    app_events.emit("space", kind, severity, title, body, data=data, fallback=telegram_text)


def load_posted():
    return json.loads(POSTED.read_text(encoding="utf-8")) if POSTED.exists() else {}


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
        pending = publish.pending_posts(org, chan)
        need = min(EDITS_PER_RUN, QUEUE_TARGET - len(pending))
        print(f"[buffer] {len(pending)} post(s) waiting, making {max(need, 0)}")
        if need <= 0:
            return
    else:
        pending, need = [], EDITS_PER_RUN
    good, plans, batch_log, skipped = [], 0, [], []
    base = max([e["seed"] for e in history()["edits"]] + [0]) + 1

    def precheck(plan):   # the plan-only novelty rules (clips, reel windows, template+music, hook+closer), against the log and this run's edits
        reasons = gate.novelty(plan, [], gate.load_log() + batch_log)
        if reasons:
            skipped.append(reasons)
        return reasons

    while len(good) < need and plans - len(skipped) < MAX_TRIES and plans < MAX_PLANS:
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
        posted[eid] = dict(post=post["id"], due=due.isoformat().replace("+00:00", "Z"), file=fname, mode=SOUND_MODE)
        POSTED.write_text(json.dumps(posted, indent=1), encoding="utf-8")
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
