"""Where in a TikTok sound should the edit start so the track's build lands on the video's reveal?

TikTok Commercial Music Library sounds (from tiktok_music_trending) are attached in the TikTok publish form, which can trim the start.
Epic/cinematic tracks have no steady beat, so instead of locking cuts to a beat grid this finds the start offset whose energy
rises into the climax (the final "deep" shot) and is already audible in the first seconds.

    py sound_sync.py <track.mp3|m4a|wav> <video_seconds> <climax_seconds>
Prints the three best trim-start offsets (seconds)."""
import subprocess
import sys

import numpy as np

from spacepipe.lib import FFMPEG

HOP = 0.25   # seconds per energy frame / candidate offset


def envelope(path):
    """Loudness per HOP (RMS, smoothed, scaled 0..1). ffmpeg decodes first: TikTok previews are AAC in m4a, which soundfile cannot open."""
    raw = subprocess.run([FFMPEG, "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    y = np.frombuffer(raw, dtype=np.int16).astype(float) / 32768
    n = int(16000 * HOP)
    e = np.sqrt((y[: len(y) // n * n].reshape(-1, n) ** 2).mean(axis=1))
    e = np.convolve(e, np.ones(3) / 3, mode="same")
    return e / (e.max() or 1)


def best_starts(e, video_s, climax_s, top=3):
    need, c, scored = int(video_s / HOP), int(climax_s / HOP), []
    for s in range(0, len(e) - need + 1):
        w = e[s:s + need]
        before, after = w[max(0, c - 24):c].mean(), w[c:c + 16].mean()    # 6 s before, 4 s after the reveal
        score = (after - before) + 0.3 * after + 0.2 * w[:8].mean() - 0.5 * w[:8].std()   # rise into it, loud at the reveal, no dead or lurching intro
        scored.append((score, s * HOP))
    out, taken = [], []
    for sc, t in sorted(scored, reverse=True):
        if all(abs(t - u) > 8 for u in taken):   # distinct sections, not neighbouring offsets
            out.append((round(t, 2), round(sc, 3)))
            taken.append(t)
        if len(out) == top:
            break
    return out


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    env = envelope(sys.argv[1])
    video_s, climax_s = float(sys.argv[2]), float(sys.argv[3])
    if len(env) * HOP < video_s:
        sys.exit(f"track is {len(env) * HOP:.0f}s, shorter than the {video_s:.0f}s edit: skip it")
    for t, sc in best_starts(env, video_s, climax_s):
        print(f"trim start {t:6.2f}s  (score {sc})")
