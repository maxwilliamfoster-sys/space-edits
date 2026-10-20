"""Plan one edit: pick template, assets (rotating by usage), windows, music, text. Returns a plain dict (JSON-serialisable)."""
import functools
import json
import random
import statistics

from . import facts, music
from .lib import history, load_assets

# (slot, beats). Totals must be 60 beats.
TEMPLATES = {
    "classic": [("earth", 6), ("earth", 4), ("sun", 8), ("planet", 4), ("mw", 8), ("galaxy_zoom", 10), ("deep", 20)],
    "tour":    [("earth", 6), ("planet", 6), ("nebula", 6), ("nebula", 6), ("mw", 8), ("galaxy_zoom", 10), ("deep", 18)],
    "deep":    [("earth", 8), ("sun", 6), ("nebula", 8), ("galaxy", 6), ("mw", 6), ("galaxy_zoom", 10), ("deep", 16)],
    "nebulae": [("earth", 6), ("earth", 4), ("nebula", 8), ("nebula", 6), ("galaxy", 6), ("mw", 6), ("galaxy_zoom", 10), ("deep", 14)],
}
SLOT_TIERS = {"earth": ["earth"], "sun": ["sun"], "planet": ["planet"], "nebula": ["nebula"], "galaxy": ["galaxy"],
              "mw": ["galaxy", "nebula"], "galaxy_zoom": ["galaxy", "nebula"], "deep": ["deep"]}
# eyeballed planet clips (id -> how to frame): box = planet on black background fitted full-width; src_in = allowed start range (s)
PLANET_CFG = {"weic2332a": dict(mode="box", rng=(21, 25)), "weic2214b": dict(mode="box", rng=(17, 25)), "heic2312b": dict(mode="box", rng=(0, 24)),
              "weic2606a": dict(mode="box", rng=(0, 10)), "heic1914b": dict(mode="crop", rng=(0, 25))}
CLOSER_BEATS = 8   # the last N beats carry the closing line and the credit card


def pick_asset(slot, assets, used_ids, usage, rng):
    pool = [a for a in assets if a["tier"] in SLOT_TIERS[slot] and a["id"] not in used_ids and not a.get("unvetted")]
    if slot in ("mw", "galaxy_zoom"):
        # one all-sky zoom serves both slots, so it must end on something we can caption truthfully
        pool = [a for a in pool if a.get("kind") == "zoom" and a.get("allsky") and (a["tier"] == "galaxy" or a.get("dist_ly"))]
    elif slot in ("nebula", "galaxy"):
        pool = [a for a in pool if a.get("kind") in ("pan", "transition")]
    elif slot == "deep":
        pool = [a for a in pool if a.get("kind") in ("pan", "zoom", "transition")]
    elif slot == "planet":
        pool = [a for a in pool if a["id"] in PLANET_CFG]
    if not pool:
        return None
    w = [1.0 / (1 + usage.get(a["id"], 0)) ** 2 for a in pool]
    return rng.choices(pool, weights=w)[0]


@functools.lru_cache(maxsize=None)
def _scan(path):
    with open(path) as f:
        return json.load(f)


def reel_window(a, need, rng):
    """Start time in a long NASA reel: avoids scene cuts/dissolves, dark seconds and manually skipped ranges."""
    dur = a["dur"]
    bad = [tuple(x) for x in a.get("skip", [])]
    ok = lambda s: True
    if a.get("scan"):
        sc = _scan(a["scan"])
        st = sc["stats"]
        ds = [x.get("d", 0) for x in st]
        for i, x in enumerate(st):
            med = statistics.median(ds[max(0, i - 15):i + 15] or [0])
            if x.get("d", 0) > max(25, 2.4 * med):
                bad.append((x["t"] - 2.0, x["t"] + 2.0))
        bad += [(c - 1.5, c + 1.5) for c in sc["cuts"]]
        ys = {int(x["t"]): x.get("y", 100) for x in st}
        thr = 40 if a["tier"] == "earth" else 12
        ok = lambda s: min(ys.get(int(s) + k, 100) for k in range(0, int(need) + 1)) >= thr and statistics.mean(ys.get(int(s) + k, 100) for k in range(0, int(need) + 1)) >= (55 if a["tier"] == "earth" else 12)
    cands = [s for s in range(6, int(dur - need - 3), 2) if not any(b0 < s + need and b1 > s for b0, b1 in bad) and ok(s)]
    if not cands:   # loosen scan/luma limits but still honour the manual skip ranges
        manual = [tuple(x) for x in a.get("skip", [])]
        cands = [s for s in range(6, max(7, int(dur - need - 3)), 2) if not any(b0 < s + need and b1 > s for b0, b1 in manual)]
        cands = cands or list(range(6, max(7, int(dur - need - 3)), 2))
    return rng.choice(cands)


def plan(seed, template=None, track=None):
    rng = random.Random(seed)
    assets = load_assets()
    usage = history()["usage"]
    tname = template or rng.choice(list(TEMPLATES))
    slots = TEMPLATES[tname]
    assert sum(b for _, b in slots) == 60, tname
    tracks = music.usable(music.pool())
    pick = rng.choice(tracks)   # always drawn so a seed gives the same shots with or without --track
    tr = next((t for t in tracks if t["title"].lower() == track.lower()), None) if track else pick
    if tr is None:
        raise ValueError(f"unknown or unusable track {track!r}; usable: {[t['title'] for t in tracks]}")
    P, T0 = tr["P"], tr["T0"]
    shots, used, zoom_asset = [], set(), None
    for si, (slot, beats) in enumerate(slots):
        L = beats * P + 0.6 + (T0 if si == 0 else 0.0)   # shot 0 also covers the intro before the first beat
        a = zoom_asset if (slot == "galaxy_zoom" and zoom_asset is not None) else pick_asset(slot, assets, used, usage, rng)
        if a is None:
            raise RuntimeError(f"no asset for slot {slot}")
        if a["src"] != "nasa":
            used.add(a["id"])
        s = dict(slot=slot, asset=a["id"], beats=beats, tier=a["tier"], title=a["title"], credit=a["credit"], src=a["src"], path=a["path"],
                 url=a.get("url"), kind=a.get("kind"), len=round(L, 3), speed=1.0, mode="crop", z=[1.0, 1.06], fx=0.5, dur=a["dur"],
                 dist_ly=a.get("dist_ly"))
        if a["src"] == "nasa":
            for _ in range(8):   # long reels may repeat within an edit, but never overlapping windows
                s["src_in"] = reel_window(a, L, rng)
                if all(o["asset"] != a["id"] or abs(o["src_in"] - s["src_in"]) > L + 1 for o in shots):
                    break
            else:
                raise RuntimeError(f"no free window in {a['id']} for slot {slot}")
            if slot == "earth" and rng.random() < 0.5:
                s["z"] = [1.0, 1.1]
        elif slot == "mw":
            s.update(speed=3.0, src_in=0.0, fx="auto@3", z=[1.0, 1.0])
            zoom_asset = a
        elif slot == "galaxy_zoom":
            s.update(src_in=round(0.4 * a["dur"], 2), speed=round(max(1.0, (0.4 * a["dur"]) / L), 2), fx=0.5, z=[1.0, 1.0])
        elif slot == "planet":
            cfg = PLANET_CFG[a["id"]]
            s.update(mode=cfg["mode"], src_in=round(rng.uniform(*cfg["rng"]), 2), box=[720, 380, 2400, 1350], z=[1.0, 1.08])
        else:
            s.update(src_in=round(rng.uniform(0, max(0.0, a["dur"] - L - 0.5)), 2),
                     speed=1.0 if a["dur"] >= L + 1 else round((a["dur"] - 0.5) / L, 2), fx="auto@mid")
            if slot == "deep":
                s["z"] = [1.0, 1.12]
        s["tin"] = ["fadeblack", 0.6] if si == len(slots) - 1 else ["fade", 0.25]
        shots.append(s)
    texts, k = [], 0
    for si, (slot, beats) in enumerate(slots):
        a = next(x for x in assets if x["id"] == shots[si]["asset"])
        head, sub = facts.lines_for(slot, a, rng)
        k_end = min(k + beats, 60 - CLOSER_BEATS)
        if head and k < k_end:
            texts.append(dict(text=head, k0=k, k1=k_end, size=96 if len(head) < 15 else 74 if len(head) < 24 else 62, y="y1", font="impact"))
            texts.append(dict(text=sub, k0=k, k1=k_end, size=42 if len(sub) < 30 else 36, y="y2", font="arial"))
        k += beats
    closer = rng.choice(facts.CLOSERS)
    texts.append(dict(text=closer, k0=60 - CLOSER_BEATS, k1=60, size=60 if len(closer) <= 30 else 48, y=560, font="impact"))
    return dict(seed=seed, template=tname, music=tr, shots=shots, texts=texts, hook=rng.choice(facts.HOOKS),
                hashtags=rng.choice(facts.HASHTAGS), P=P, T0=T0)
