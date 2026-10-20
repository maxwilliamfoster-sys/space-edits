"""Which zoom clips start on the all-sky Milky Way image? Grabs one frame (t=1.5s) from each zoom over HTTP and measures it.
Writes library/zoom_probe.json {id: {"y": mean luma, "hi": fraction of bright pixels}}.  py -I -X utf8 -m spacepipe.probe_zooms"""
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .lib import FFMPEG, LIB, load_assets


def grab(a):
    r = subprocess.run([FFMPEG, "-loglevel", "error", "-ss", "1.5", "-i", a["url"], "-frames:v", "1", "-vf", "scale=192:108,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, timeout=120)
    if len(r.stdout) != 192 * 108:
        return a["id"], None
    f = np.frombuffer(r.stdout, np.uint8).astype(float)
    return a["id"], dict(y=round(float(f.mean()), 1), hi=round(float((f > 60).mean()), 3), site=a["site"], title=a["title"])


if __name__ == "__main__":
    zooms = [a for a in load_assets() if a["src"] != "nasa" and a.get("kind") == "zoom"]
    with ThreadPoolExecutor(8) as ex:
        res = dict(ex.map(grab, zooms))
    (LIB / "zoom_probe.json").write_text(json.dumps(res, indent=1))
    ok = {k: v for k, v in res.items() if v}
    print(len(zooms), "zooms probed,", len(ok), "ok")
    for k, v in sorted(ok.items(), key=lambda kv: -kv[1]["y"])[:200]:
        print(f"{v['y']:6} {v['hi']:6} {v['site'][:2]} {k} {v['title'][:40]}")
