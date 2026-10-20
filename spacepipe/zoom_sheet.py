"""Contact sheets of the first frame of every zoom clip, labelled with index, so the all-sky-start ones can be picked by eye.
py -X utf8 -m spacepipe.zoom_sheet -> out/audit/zoomsheet_<n>.png and out/audit/zoomsheet_index.json"""
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

from .lib import FFMPEG, ROOT, load_assets

OUT = ROOT / "out/audit/zf"


def grab(item):
    i, a = item
    f = OUT / f"{i:03d}.png"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", "1.5", "-i", a["url"], "-frames:v", "1", "-vf",
                    f"scale=256:144,drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{i}':x=4:y=4:fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.6", f],
                   capture_output=True, timeout=120)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    zooms = [a for a in load_assets() if a["src"] != "nasa" and a.get("kind") == "zoom"]
    items = list(enumerate(zooms))
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(grab, items))
    (ROOT / "out/audit/zoomsheet_index.json").write_text(json.dumps({i: [a["id"], a["site"], a["title"]] for i, a in items}, indent=0))
    for n in range(0, len(zooms), 56):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-start_number", str(n), "-i", str(OUT / "%03d.png"), "-frames:v", "1", "-vf", "tile=7x8",
                        str(ROOT / f"out/audit/zoomsheet_{n // 56}.png")], capture_output=True)
    print(len(zooms), "zooms")
