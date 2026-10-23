"""Which nebula/galaxy/deep clips would look near-black to the upload gate? Grabs 6 frames per clip (local file, else over HTTP),
crops the 9:16 window render.py would use, applies the same grade and scales to the gate's 48x85 grey thumbnail.
Writes library/luma_probe.json {id: {"y": [mean luma of each frame], "fx": crop position}}; compose.pick_asset skips clips
whose frames are too often under the gate's near-black line, so the rejection happens before the render, not after it.
    py -I -X utf8 -m spacepipe.probe_luma [--all]     (default: only clips not probed yet)"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .lib import FFMPEG, LIB, load_assets
from .render import GRADE, NET, best_fx

OUT = LIB / "luma_probe.json"
FRAMES = 6
TIERS = ("nebula", "galaxy", "deep")


def wanted(a):
    return a["src"] != "nasa" and a["tier"] in TIERS and a.get("kind") in ("pan", "transition", "zoom") and not a.get("unvetted")


def probe(a):
    src = a["path"] if Path(a["path"]).exists() and not Path(a["path"] + ".part").exists() else a["url"]
    net = NET if src.startswith("http") else []
    try:
        fx = best_fx(src, a["dur"] / 2)
        ys = []
        for i in range(FRAMES):
            t = a["dur"] * (i + 0.5) / FRAMES
            r = subprocess.run([FFMPEG, "-loglevel", "error", *net, "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-vf",
                                f"crop=w='trunc(ih*9/16/2)*2':h=ih:x='(iw-ow)*{fx}':y=0,scale=48:85,{GRADE},format=gray",
                                "-f", "rawvideo", "-"], capture_output=True, timeout=180)
            if len(r.stdout) == 48 * 85:
                ys.append(round(float(np.frombuffer(r.stdout, np.uint8).mean()), 1))
        return a["id"], (dict(y=ys, fx=fx) if len(ys) == FRAMES else None)
    except Exception as e:   # one unreachable clip must not stop the probe
        print("failed", a["id"], type(e).__name__, flush=True)
        return a["id"], None


if __name__ == "__main__":
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [a for a in load_assets() if wanted(a) and ("--all" in sys.argv or a["id"] not in done)]
    print(len(todo), "clips to probe", flush=True)
    with ThreadPoolExecutor(8) as ex:
        for n, (k, v) in enumerate(ex.map(probe, todo), 1):
            if v:
                done[k] = v
            if n % 20 == 0:
                OUT.write_text(json.dumps(done, indent=0, sort_keys=True))
                print(n, "probed", flush=True)
    OUT.write_text(json.dumps(done, indent=0, sort_keys=True))
    print(len(done), "clips in", OUT.name)
