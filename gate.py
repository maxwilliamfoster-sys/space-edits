"""Upload gate: nothing is hosted or scheduled unless it passes here. Two jobs:

QUALITY  - format/loudness QC passed, no frozen stretch, no dark/black stretch, no silent stretch, and (when GROQ_API_KEY is
           set) a vision model looks at a contact sheet and rejects burned-in charts/captions/watermarks, blur and glitches.
NOVELTY  - the edit must not repeat recent ones: no recently used clip or reel window, no recent template+music or hook+closer
           pair, and no frames that look like a recent edit (perceptual hash).

check() writes gate.json next to the video (with the video's sha256); approved() re-checks that file, so a video that was not
approved in this run, or was changed afterwards, can never be published. record() stores a posted edit for future novelty checks.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

import hook_lint   # growth-lab S5 (vendored)
from spacepipe.lib import FFMPEG, LIB

LOG = LIB / "gate_log.json"
# Sized for 3 edits a day (ci_run.SLOT_HOURS_UTC), checked by tests/test_gate.py Capacity with the real planner. The limits are
# the only vetted Earth reel (3000 s), the three Sun reels (79-391 s), the 12 all-sky zooms that pass compose's brightness and
# scale checks (one per edit, so NO_REPEAT_CLIPS must stay well under 12) and 5 hooks x 5 closers. 12/12/9/9 ran dry on day 5.
NO_REPEAT_CLIPS = 9        # an ESA clip may not appear in any of the last N posted edits (3 days; was 10 = 1 day at 10 a day)
NO_REPEAT_WINDOW_S = 20    # same NASA reel: new windows must start this far from recently used ones (shots are 3-6 s long)
NO_REPEAT_REELS = 9        # ... looking back this many edits (3 days)
NO_REPEAT_COMBO = 9        # template+music (3 days)
NO_REPEAT_TEXT = 6         # hook+closer (2 days; only 25 pairs until the text pools grow)
HASH_FRAMES = 6
HASH_NEAR = 8              # max hamming distance (of 64 bits) for two frames to count as "the same picture"
HASH_MATCHES = 3           # this many near-identical frames = a repeat
GROQ_MODEL = os.environ.get("GATE_VISION_MODEL", "qwen/qwen3.8-27b")
HOOK_MAX_WORDS = 7         # on-screen hook (audit 2026-10-21 #2: 7 words or fewer, burned in at Impact 80)
# names/places that count as the hook's concrete specific (hook_lint also takes numbers and capitalised names)
HOOK_SPECIFICS = ("universe", "galaxy", "galaxies", "milky way", "solar system", "earth", "sun", "light-year", "light-years")
AI_UNAVAILABLE = "AI review could not run"
AI_PROMPT = (
    "You are the quality gate for a TikTok space-footage edit (vertical video of real telescope / ISS footage with white text "
    "overlays). These are 12 frames in time order. Expected and fine: a large bold white hook line near the top of the picture in "
    "the first frames (it is the video's opening hook, burned in on purpose), clean white headline text with a small subline, and "
    "in the LAST frame a small semi-transparent black box listing credits. Reject (ok=false) ONLY for clear problems: a chart, graph, "
    "axis, scale bar or data/caption box that is part of the source footage; an agency/telescope logo or watermark stamped on the "
    "footage itself; heavy blur or compression blocks; headline text cut off by the frame edge or overlapping other text; a "
    "glitch or solid black/green frame in the middle; imagery that is clearly not real space or Earth footage. Do not flag stars "
    "or diffraction spikes. When unsure, accept. Answer ONLY JSON: {\"ok\": true|false, \"problems\": [\"short reason\", ...]}.")


def _frames(video, times, w, h, gray=True):
    out = []
    for t in times:
        r = subprocess.run([FFMPEG, "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-vf",
                            f"scale={w}:{h}," + ("format=gray" if gray else "format=rgb24"), "-f", "rawvideo", "-"], capture_output=True)
        n = w * h * (1 if gray else 3)
        if len(r.stdout) == n:
            out.append(np.frombuffer(r.stdout, np.uint8).reshape((h, w) if gray else (h, w, 3)))
    return out


def _duration(video):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def dhash(img):
    """64-bit difference hash of a 9x8 grayscale frame, as an int."""
    bits = (img[:, 1:] > img[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a, b):
    return bin(a ^ b).count("1")


def frame_hashes(video, dur):
    ts = [dur * (i + 0.5) / HASH_FRAMES for i in range(HASH_FRAMES)]
    fr = _frames(video, ts, 9, 8)
    return [dhash(f) for f in fr] if len(fr) == HASH_FRAMES else []


# ------------------------------------------------------------------ quality --

def quality(video, folder, dur, silent=False):
    reasons = []
    qc = json.loads((folder / "qc.json").read_text(encoding="utf-8"))
    if not qc.get("ok"):
        reasons.append("format/loudness QC failed: " + ", ".join(k for k, v in qc["checks"].items() if not v))
    ts = np.arange(1.0, dur - 1.5, 0.75)
    small = _frames(video, ts, 48, 85)
    if len(small) < len(ts) * 0.9:
        reasons.append("could not read frames from the video")
    else:
        lum = np.array([f.mean() for f in small])
        dark = float((lum < 6).mean())
        if dark > 0.2:
            reasons.append(f"{dark:.0%} of the video is near-black")
        run = best = 0
        for a, b in zip(small, small[1:]):
            run = run + 1 if np.abs(a.astype(int) - b.astype(int)).mean() < 0.4 else 0
            best = max(best, run)
        if best >= 3:   # 3 samples in a row ~2.25 s without any change (space footage always drifts a little)
            reasons.append(f"frozen/static stretch of ~{best * 0.75:.1f}s")
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video), "-af", "silencedetect=n=-45dB:d=2", "-f", "null", "-"],
                       capture_output=True, text=True)
    if not silent and "silence_start" in r.stderr:
        reasons.append("silent stretch of 2s or more in the audio")
    return reasons


def ai_review(video, dur):
    """None = skipped (no key), else a list of problems (empty = looks fine)."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return [AI_UNAVAILABLE] if os.environ.get("REQUIRE_AI") == "1" else None
    ts = [dur * (i + 0.5) / 12 for i in range(12)]
    fr = _frames(video, ts, 270, 480, gray=False)
    if len(fr) < 12:
        return ["could not read frames for the AI review"]
    rows = [np.concatenate(fr[i * 6:(i + 1) * 6], axis=1) for i in range(2)]
    sheet = np.concatenate(rows, axis=0)
    tmp = Path(video).with_name("gate_sheet.jpg")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{sheet.shape[1]}x{sheet.shape[0]}",
                    "-i", "-", "-q:v", "4", str(tmp)], input=sheet.tobytes(), check=True)
    b64 = base64.b64encode(tmp.read_bytes()).decode()
    body = {"model": GROQ_MODEL, "temperature": 0, "reasoning_effort": "none", "max_tokens": 400,
            "messages": [{"role": "user", "content": [{"type": "text", "text": AI_PROMPT},
                                                      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]}
    try:
        for wait in (0, 20, 45):   # free tier rate limits: back off and retry
            time.sleep(wait)
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=90)
            if r.status_code != 429:
                break
        txt = r.json()["choices"][0]["message"]["content"]
        v = json.loads(re.search(r"{.*}", txt, re.S).group(0))
    except Exception as e:      # a gate that cannot run must not wave things through when it is required
        print(f"[gate] AI review failed: {type(e).__name__}")
        return [AI_UNAVAILABLE] if os.environ.get("REQUIRE_AI") == "1" else None
    return [] if v.get("ok") else [f"AI review: {p}" for p in v.get("problems", ["rejected"])] or ["AI review rejected it"]


# ------------------------------------------------------------------ novelty --

def load_log():
    return json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []


def entry_for(plan, hashes, eid):
    closer = next((t["text"] for t in plan["texts"] if t["k1"] == 60), "")
    return dict(id=eid, t=datetime.now(timezone.utc).isoformat(timespec="seconds"), template=plan["template"], music=plan["music"]["title"],
                hook=plan["hook"], closer=closer, assets=[s["asset"] for s in plan["shots"] if s["src"] != "nasa"],
                windows=[[s["asset"], s["src_in"]] for s in plan["shots"] if s["src"] == "nasa"], hashes=hashes)


def novelty(plan, hashes, log):
    reasons = []
    e = entry_for(plan, hashes, "")
    for a in e["assets"]:
        if any(a in p["assets"] for p in log[-NO_REPEAT_CLIPS:]):
            reasons.append(f"clip {a} was used in one of the last {NO_REPEAT_CLIPS} edits")
    for asset, t in e["windows"]:
        for p in log[-NO_REPEAT_REELS:]:
            if any(a == asset and abs(t - t2) < NO_REPEAT_WINDOW_S for a, t2 in p["windows"]):
                reasons.append(f"{asset} window near {t:.0f}s was already used")
                break
    # Max, 2026-10-28: posts all told the same zoom-out story. Back-to-back edits must be different formats.
    from spacepipe.compose import FAMILY
    if log and FAMILY.get(log[-1]["template"], log[-1]["template"]) == FAMILY.get(e["template"], e["template"]):
        reasons.append(f"same format ({FAMILY.get(e['template'], e['template'])}) as the previous edit")
    if any(p["template"] == e["template"] and p["music"] == e["music"] for p in log[-NO_REPEAT_COMBO:]):
        reasons.append(f"template+music ({e['template']} / {e['music']}) used in the last {NO_REPEAT_COMBO} edits")
    if any(p["hook"] == e["hook"] and p["closer"] == e["closer"] for p in log[-NO_REPEAT_TEXT:]):
        reasons.append("same hook + closing line as a recent edit")
    if hashes:
        for p in log[-NO_REPEAT_CLIPS:]:
            if p.get("hashes") and sum(hamming(a, b) <= HASH_NEAR for a, b in zip(hashes, p["hashes"])) >= HASH_MATCHES:
                reasons.append(f"looks like edit {p['id']} (matching frames)")
                break
    return reasons


# --------------------------------------------------------------------- text --

def text_lint(plan, caption=None):
    """hook_lint on the on-screen hook (<= HOOK_MAX_WORDS, a concrete specific, no greeting/'...'/doubled word), the plan's
    hashtags (2-4, no #fyp/#foryou) and the caption (doubled words). Fails become gate reasons; warnings are only printed."""
    res = hook_lint.lint(hook=plan.get("hook") or "", caption=caption, tags=plan.get("hashtags"),
                         max_words=HOOK_MAX_WORDS, specifics=HOOK_SPECIFICS)
    for w in res.warns:
        print(f"[gate] text lint warning: {w.code}: {w.message}")
    return [f"text lint: {i.code}: {i.message}" for i in res.fails]


def plan_check(plan, log):
    """Everything that can be judged from the plan alone (text lint + plan novelty), so a bad plan is dropped before the
    minutes-long render."""
    return text_lint(plan) + novelty(plan, [], log)


# --------------------------------------------------------------------- API --

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(folder, plan, eid=None, extra_log=()):
    """Run the whole gate on out/queue/<id>/. Returns {ok, reasons, ai}. Writes gate.json (the approval) only when ok.
    extra_log = pending_entry() of edits already approved in this run but not posted yet, so one batch cannot repeat itself."""
    folder = Path(folder)
    video = folder / "video.mp4"
    eid = eid or folder.name
    dur = _duration(video)
    hashes = frame_hashes(video, dur)
    reasons = quality(video, folder, dur, silent=bool(plan.get("silent")))
    cap = folder / "caption.txt"
    reasons += text_lint(plan, cap.read_text(encoding="utf-8") if cap.exists() else None)
    ai = ai_review(video, dur)   # frames only, so works for silent edits too
    if ai:
        reasons += ai
    reasons += novelty(plan, hashes, load_log() + list(extra_log))
    res = dict(ok=not reasons, reasons=reasons, ai="skipped" if ai is None else "ran")
    gj = folder / "gate.json"
    if res["ok"]:
        gj.write_text(json.dumps(dict(res, sha256=sha256(video), hashes=hashes, id=eid)), encoding="utf-8")
    elif gj.exists():
        gj.unlink()
    print(f"[gate] {eid}: {'PASS' if res['ok'] else 'REJECT ' + '; '.join(reasons)} (AI review {res['ai']})")
    return res


def approved(folder):
    """True only if this exact video passed check()."""
    folder = Path(folder)
    gj = folder / "gate.json"
    if not gj.exists():
        return False
    return json.loads(gj.read_text(encoding="utf-8")).get("sha256") == sha256(folder / "video.mp4")


def require(folder):
    if not approved(folder):
        raise RuntimeError(f"upload gate has not approved {folder}")


def pending_entry(plan, folder, eid):
    """Novelty-log entry for an approved edit (hashes come from its gate.json)."""
    hashes = json.loads((Path(folder) / "gate.json").read_text(encoding="utf-8")).get("hashes", [])
    return entry_for(plan, hashes, eid)


def record(plan, folder, eid):
    """Add a published edit to the novelty log."""
    log = load_log()
    log = [p for p in log if p["id"] != eid] + [pending_entry(plan, folder, eid)]
    LOG.write_text(json.dumps(log[-200:], indent=1), encoding="utf-8")
