"""Music pool: analyse beat grid (period P, phase T0, alignment R) for every track, cache in music/pool_beats.json."""
import json
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "music/pool_beats.json"
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


if __name__ == "__main__":
    c = pool()
    for v in usable(c):
        print(v["title"], v["bpm"], "bpm R", v["R"], "T0", v["T0"], "60 beats =", round(60 * v["P"], 1), "s")
