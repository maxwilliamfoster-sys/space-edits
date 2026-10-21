"""Space 'scale' edit factory.
  py -I -X utf8 spacepipe_make.py new [-n 3] [--seed S] [--template classic|tour|deep|nebulae] [--track "Epic Unease"]
  py -I -X utf8 spacepipe_make.py status        # footage/music capacity report
  py -I -X utf8 spacepipe_make.py prefetch [-n 6]  # download N ESA clips per tier ahead of time
Each edit lands in out/queue/<id>/ : video.mp4, caption.txt, plan.json, qc.json.
"""
import argparse
import datetime
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from spacepipe import compose, music, qc, render  # noqa: E402
from spacepipe.lib import ROOT, ensure_local, history, load_assets, save_history  # noqa: E402


class PlanRejected(Exception):
    """The plan breaks a novelty rule, so it is not worth rendering."""


def make_one(seed, template, track, silent=False, precheck=None):
    """Plan, render and QC one edit. Returns (edit id, plan, qc result, folder).
    precheck(plan) -> list of reasons; a non-empty list skips the (minutes-long) render with PlanRejected."""
    p = compose.plan(seed, template, track)
    reasons = precheck(p) if precheck else []
    if reasons:
        raise PlanRejected("; ".join(reasons))
    p["silent"] = silent   # silent: audio track is silence (a TikTok sound is added in the app); beats still set the cuts
    eid = f"{datetime.date.today():%Y%m%d}_{seed:04d}"
    tmp = ROOT / "out/work" / eid
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    print(f"[{eid}] template={p['template']} music={p['music']['title']} ({p['music']['mode']}) "
          f"clips={[s['asset'] for s in p['shots']]}", flush=True)
    render.render(p, str(tmp / "video.mp4"))
    res = qc.check(tmp / "video.mp4", silent=silent)
    (tmp / "caption.txt").write_text(render.caption_text(p), encoding="utf-8")
    (tmp / "plan.json").write_text(json.dumps(p, indent=1, default=str), encoding="utf-8")
    (tmp / "qc.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return eid, p, res, tmp


def run_batch(n, template=None, track=None, base=None, silent=False, precheck=None):
    """Make n edits. Passing ones go to out/queue/, QC failures to out/rejected/ (not counted as clip usage); a crash skips that
    seed only. Returns [(edit id, plan, folder)] for the edits that passed."""
    if base is None:
        base = max([e["seed"] for e in history()["edits"]] + [0]) + 1
    passed = []
    for i in range(n):
        seed = base + i
        try:
            eid, p, res, tmp = make_one(seed, template, track, silent, precheck)
        except PlanRejected as e:
            print(f"   seed {seed} skipped before rendering: {e}", flush=True)
            continue
        except Exception as e:
            print(f"   seed {seed} CRASHED: {str(e)[-300:]}", flush=True)
            continue
        dest = ROOT / "out" / ("queue" if res["ok"] else "rejected") / eid
        shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), str(dest))
        print("   QC", "PASS" if res["ok"] else "FAIL " + str([k for k, v in res["checks"].items() if not v]),
              f"{res['dur']}s {res['size_mb']}MB {res['lufs']} LUFS", flush=True)
        h = history()
        h["edits"] = [e for e in h["edits"] if e["id"] != eid]
        h["edits"].append(dict(id=eid, seed=seed, template=p["template"], music=p["music"]["title"], qc=res["ok"],
                               assets=[s["asset"] for s in p["shots"]]))
        if res["ok"]:
            passed.append((eid, p, dest))
            for s in p["shots"]:
                h["usage"][s["asset"]] = h["usage"].get(s["asset"], 0) + 1
        save_history(h)
    print(f"{len(passed)}/{n} edits passed QC")
    return passed


def new(a):
    run_batch(a.n, a.template, a.track, a.seed)


def status(_):
    assets = load_assets()
    by = Counter(x["tier"] for x in assets)
    print("assets per tier:", dict(by))
    zoom = [x for x in assets if x.get("kind") == "zoom" and x["tier"] in ("galaxy", "nebula")]
    print("zoom clips (Milky Way + galaxy slots):", len(zoom))
    pans = {t: len([x for x in assets if x["tier"] == t and x.get("kind") in ("pan", "transition")]) for t in ("nebula", "galaxy", "deep")}
    print("pan/transition clips:", pans)
    lim = {"nebula pans": pans["nebula"] // 2, "galaxy pans": pans["galaxy"], "deep": by["deep"], "zooms": len(zoom),
           "planets": by["planet"]}
    print("clips available per slot (Earth and Sun come from long reels, so are not the limit):", lim)
    on_disk = sum(1 for x in assets if Path(x["path"]).exists())
    print("clips downloaded:", on_disk, "of", len(assets))
    tr = music.usable(music.pool())
    print("music:", Counter(t["mode"] for t in tr), [t["title"] for t in tr])
    hh = history()
    print("edits made:", len(hh["edits"]))


def prefetch(a):
    assets = [x for x in load_assets() if x["src"] != "nasa"]
    per = Counter()
    for x in assets:
        if per[x["tier"]] >= a.n or Path(x["path"]).exists():
            continue
        ensure_local(x)
        per[x["tier"]] += 1
        print("got", x["id"], x["tier"], flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("-n", type=int, default=1)
    n.add_argument("--seed", type=int)
    n.add_argument("--template", choices=list(compose.TEMPLATES))
    n.add_argument("--track")
    n.set_defaults(f=new)
    sub.add_parser("status").set_defaults(f=status)
    pf = sub.add_parser("prefetch")
    pf.add_argument("-n", type=int, default=6)
    pf.set_defaults(f=prefetch)
    args = ap.parse_args()
    args.f(args)
