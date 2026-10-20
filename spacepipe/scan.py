"""One decode pass over a long NASA reel -> library/scan_<name>.json: scene cuts + per-second stats (luma, saturation, motion).
py -I -X utf8 spacepipe/scan.py raw/nasa/earth4k.mp4 earth4k"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = "C:/ffmpeg/ffmpeg-8.1.1-essentials_build/bin/ffmpeg.exe"


def scan(src, name):
    tmp = ROOT / "out/scan_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    cuts_f, stats_f = f"scan_{name}_cuts.txt", f"scan_{name}_stats.txt"
    fc = (f"[0:v]scale=192:108,split[a][b];[a]select='gt(scene,0.28)',metadata=print:file={cuts_f}[o1];"
          f"[b]fps=1,signalstats,metadata=print:file={stats_f}[o2]")
    for hw in (["-hwaccel", "auto"], []):   # hardware decode chokes on some reels; fall back to software
        r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", *hw, "-i", str(Path(src).resolve()), "-filter_complex", fc,
                            "-map", "[o1]", "-f", "null", "-", "-map", "[o2]", "-f", "null", "-"], cwd=tmp, capture_output=True, text=True)
        if not r.returncode:
            break
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])
    cuts = sorted({round(float(m), 2) for m in re.findall(r"pts_time:([\d.]+)", (tmp / cuts_f).read_text())})
    stats, cur = [], {}
    for line in (tmp / stats_f).read_text().splitlines():
        if line.startswith("frame:"):
            if cur:
                stats.append(cur)
            cur = {"t": float(re.search(r"pts_time:([\d.]+)", line).group(1))}
        elif "YAVG=" in line:
            cur["y"] = float(line.split("=")[1])
        elif "SATAVG=" in line:
            cur["s"] = float(line.split("=")[1])
        elif "YDIF=" in line:
            cur["d"] = float(line.split("=")[1])
    if cur:
        stats.append(cur)
    (ROOT / f"library/scan_{name}.json").write_text(json.dumps({"cuts": cuts, "stats": stats}))
    print(name, "cuts", len(cuts), "stat-seconds", len(stats))


if __name__ == "__main__":
    scan(sys.argv[1], sys.argv[2])
