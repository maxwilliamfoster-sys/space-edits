"""Music pool: analyse beat grid (period P, phase T0, alignment R) for every track, cache in music/pool_beats.json.
Loudness envelopes (sound_sync.envelope) are cached in music/pool_env.json so the planner can pick each edit's start offset
(the track's best section, not its quiet intro) without decoding any mp3. Rebuild after adding tracks: py -X utf8 -m spacepipe.music"""
import functools
import json
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "music/pool_beats.json"
ENV_CACHE = ROOT / "music/pool_env.json"
START_CHOICES = 3   # pick one of the best N distinct sections per edit (seeded), so the same track does not always start alike
CREDIT = "{t} by Kevin MacLeod (incompetech.com), CC BY 4.0"
# CI test run 2026-10-21: both failed every time they were drawn (2 of 2), so they never reach the gate again
BAD_TRACKS = {"Lightless Dawn": "silent stretch >= 2 s (gate)", "Rising Tide": "loudness -18.2 LUFS after loudnorm (QC needs -17..-11)"}
EXTRA = {"Epic_Unease": (1.02, 0.4875), "Heroic_Age": None}  # Epic Unease grid hand-tuned earlier


def analyze(path):
    import librosa
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    _, beats = librosa.beat.beat_track(y=y, sr=sr, units="time", tightness=200)
    beats = np.asarray(beats)
    P = float(np.median(np.diff(beats)))
    while P < 0.42:
        P *= 2
    while P > 0.75:
        P /= 2
    ph = (beats % P) / P * 2 * np.pi
    R = float(np.hypot(np.cos(ph).mean(), np.sin(ph).mean()))
    phase = (np.arctan2(np.sin(ph).mean(), np.cos(ph).mean()) % (2 * np.pi)) / (2 * np.pi) * P
    T0 = phase
    while T0 < 0.6:
        T0 += P
    return dict(file=str(Path(path).relative_to(ROOT)).replace("\\", "/"), title=Path(path).stem.replace("_", " "),
                bpm=round(60 / P, 1), P=round(P, 4), T0=round(float(T0), 3), R=round(R, 3), dur=round(len(y) / sr, 1))


def pool(refresh=False):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    files = sorted((ROOT / "music/pool").glob("*.mp3")) + [ROOT / "music/Epic_Unease.mp3", ROOT / "music/Heroic_Age.mp3"]
    for f in files:
        if not f.exists():
            continue
        if f.stem in cache and not refresh:
            continue
        cache[f.stem] = analyze(f)
        print("analysed", f.stem, cache[f.stem], flush=True)
    CACHE.write_text(json.dumps(cache, indent=1))
    return cache


def usable(cache):
    """beat tracks (grid trustworthy, R>=0.6) lock cuts to the music; ambient tracks get a fixed 0.5 s rhythm instead."""
    out = []
    for k, v in cache.items():
        if v["title"] in BAD_TRACKS:
            continue
        if k == "Epic_Unease":
            v = dict(v, T0=1.02, P=0.4875, R=1.0)
        if v["dur"] < 45 and k != "Epic_Unease":
            continue
        credit = CREDIT.format(t=v["title"])
        if v["R"] >= 0.6 and 0.41 <= v["P"] <= 0.62 and 60 * v["P"] + v["T0"] + 2 < v["dur"]:
            out.append(dict(v, mode="beat", credit=credit))
        elif v["dur"] > 60 and k not in ("Epic_Unease", "Heroic_Age"):
            out.append(dict(v, mode="ambient", P=0.5, T0=0.6, credit=credit))
    return out


def _env_cache():
    return json.loads(ENV_CACHE.read_text(encoding="utf-8")) if ENV_CACHE.exists() else {}


def build_envelopes(refresh=False):
    """Decode each pool track once and store its loudness envelope (one value per sound_sync.HOP, scaled 0..1000)."""
    import sound_sync
    cache = _env_cache()
    for v in pool().values():
        f = ROOT / v["file"]
        key, size = Path(v["file"]).stem, f.stat().st_size if f.exists() else None
        if size is None or (not refresh and cache.get(key, {}).get("size") == size):
            continue
        e = sound_sync.envelope(f)
        cache[key] = dict(size=size, hop=sound_sync.HOP, env=[int(round(float(x) * 1000)) for x in e])
        print("envelope", key, len(e), flush=True)
    body = ",\n".join(f"{json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}" for k, v in sorted(cache.items()))
    ENV_CACHE.write_text("{\n" + body + "\n}\n", encoding="utf-8")
    envelope.cache_clear()
    return cache


@functools.lru_cache(maxsize=None)
def envelope(file):
    """Cached loudness envelope (tuple of 0..1) of a pool track; decodes it only if pool_env.json has no entry yet."""
    key = Path(file).stem
    c = _env_cache().get(key) or build_envelopes().get(key)
    return tuple(x / 1000 for x in c["env"])


@functools.lru_cache(maxsize=None)
def _best(file, t_end, climax):
    import sound_sync
    return tuple(sound_sync.best_starts(np.asarray(envelope(file)), t_end, climax, top=START_CHOICES))


def pick_start(tr, t_end, climax, rng, env=None):
    """Start offset (s) into the track so its build lands on the climax (start of the last deep shot) and the first seconds are
    already audible. Beat tracks snap to whole beats so the cuts stay on the beat. 0.0 if no section fits."""
    if env is not None:
        import sound_sync
        best = sound_sync.best_starts(np.asarray(env, float), t_end, climax, top=START_CHOICES)
    else:
        best = _best(tr["file"], round(t_end, 3), round(climax, 3))
    starts = []
    for t, _ in best:
        if tr["mode"] == "beat":
            t = round(t / tr["P"]) * tr["P"]
        if t >= 0 and t + t_end + 2 < tr["dur"]:
            starts.append(t)
    return rng.choice(starts) if starts else 0.0


if __name__ == "__main__":
    build_envelopes()
    c = pool()
    for v in usable(c):
        print(v["title"], v["bpm"], "bpm R", v["R"], "T0", v["T0"], "60 beats =", round(60 * v["P"], 1), "s")
