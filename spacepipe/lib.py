"""Asset library: ESA catalog + hand-vetted NASA reels, local paths, downloads, usage history."""
import json
import re
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
POOL = ROOT / "raw/pool"
_WIN_BIN = "C:/ffmpeg/ffmpeg-8.1.1-essentials_build/bin/"
FFMPEG = os.environ.get("FFMPEG") or (_WIN_BIN + "ffmpeg.exe" if os.path.exists(_WIN_BIN + "ffmpeg.exe") else shutil.which("ffmpeg"))
FFPROBE = os.environ.get("FFPROBE") or (_WIN_BIN + "ffprobe.exe" if os.path.exists(_WIN_BIN + "ffprobe.exe") else shutil.which("ffprobe"))

ALLSKY_DENY = {"heic1615a", "heic1711a", "heic1805b", "heic1712a"}
# clips with a chart/graph burned into the picture (checked by eye): never use them in any slot
CHART_DENY = {"heic2018b"}
CHART_TITLE = re.compile(r"brightness|light[- ]?curve|cepheid|chart|graph|spectr", re.I)
NASA_CREDIT_ISS = "NASA, International Space Station (Earth in 4K)"
NASA_CREDIT_SDO = "NASA's Solar Dynamics Observatory / NASA Goddard Space Flight Center"
# Long NASA reels. `scan` = library/scan_<name>.json (cuts/stats) used to find clean windows.
NASA = [
    dict(id="earth_exp65", url="https://images-assets.nasa.gov/video/jsc2022m000172_Earth_in_4K_Expedition_65_Edition/jsc2022m000172_Earth_in_4K_Expedition_65_Edition~orig.mp4", tier="earth", kind="reel", file="raw/nasa/earth4k.mp4", credit=NASA_CREDIT_ISS + ", Expedition 65 Edition", dur=3000),
    dict(id="earth_exp67", tier="earth", kind="reel", file="raw/nasa/earth_exp67.mp4", credit=NASA_CREDIT_ISS + ", Expedition 67-68 Edition", unvetted=True, dur=3151),
    dict(id="earth_views21", tier="earth", kind="reel", file="raw/nasa/earth_views21.mp4", credit=NASA_CREDIT_ISS + ", Earth Views extended cut", unvetted=True, dur=3154),
    dict(id="sdo_flare", url="https://images-assets.nasa.gov/video/GSFC_20160426_SDO_m12224_SolarFlare_4K/GSFC_20160426_SDO_m12224_SolarFlare_4K~orig.mp4", tier="sun", kind="reel", file="raw/nasa/sdo_flare.mp4", credit=NASA_CREDIT_SDO, dur=79, skip=[(0, 12), (54, 80)]),
    dict(id="sdo_year6", url="https://images-assets.nasa.gov/video/GSFC_20160212_SDO_m12144_Year6/GSFC_20160212_SDO_m12144_Year6~orig.mp4", tier="sun", kind="reel", file="raw/nasa/sdo_year6.mp4", credit=NASA_CREDIT_SDO, dur=391, skip=[(0, 14), (160, 176), (190, 210), (310, 391)]),
    dict(id="sdo_year7", tier="sun", kind="reel", file="raw/nasa/sdo_year7.mp4", credit=NASA_CREDIT_SDO, unvetted=True, dur=201),
    dict(id="mercury_transit", url="https://images-assets.nasa.gov/video/GSFC_20160601_Mercury_m12268_Transit_4K/GSFC_20160601_Mercury_m12268_Transit_4K~orig.mp4", tier="sun", kind="reel", file="raw/nasa/mercury_transit.mp4", credit="NASA's Goddard Space Flight Center / SDO", dur=84, skip=[(0, 5), (22, 34), (56, 84)]),
]


def load_assets():
    assets = []
    for a in NASA:
        a = dict(a, src="nasa", title=a["id"], path=str(ROOT / a["file"]))
        scan = LIB / f"scan_{a['id']}.json"
        a["scan"] = str(scan) if scan.exists() else None
        assets.append(a)
    cat = LIB / "catalog.json"
    probe = json.loads((LIB / "zoom_probe.json").read_text()) if (LIB / "zoom_probe.json").exists() else {}
    luma = json.loads((LIB / "luma_probe.json").read_text()) if (LIB / "luma_probe.json").exists() else {}   # spacepipe/probe_luma.py
    for c in json.loads(cat.read_text(encoding="utf-8")) if cat.exists() else []:
        c = dict(c, src=c["site"], path=str(POOL / f"{c['id']}.mp4"))
        if c["id"] in luma:
            c["luma"] = luma[c["id"]]["y"]
        pr = probe.get(c["id"])
        if c["id"] in CHART_DENY or CHART_TITLE.search(c["title"]):
            c["unvetted"] = True
        # zoom clips that open on the all-sky Milky Way (checked by eye on a first-frame sheet; DENY = chart/caption starts)
        c["allsky"] = bool(pr and 14 <= pr["y"] <= 50 and 0.03 <= pr["hi"] <= 0.26 and c["id"] not in ALLSKY_DENY
                           and not re.search(r"pullout", c["title"], re.I))
        assets.append(c)
    return assets


def ensure_local(a):
    """Download an ESA clip into raw/pool if missing (resumable). NASA reels must already exist."""
    p = Path(a["path"])
    if p.exists() and not Path(str(p) + ".part").exists():
        return p
    if a["src"] == "nasa":
        raise FileNotFoundError(f"{p} missing (NASA reels are streamed from their URL, see render.render)")
    POOL.mkdir(parents=True, exist_ok=True)
    part = Path(str(p) + ".part")
    r = subprocess.run(["curl", "-sfL", "-C", "-", "-o", str(part), a["url"]])
    if r.returncode == 22 and part.exists():   # maybe 416 = the .part was already complete: accept only if it decodes to the end
        pr = subprocess.run([str(FFMPEG).replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(part)],
                            capture_output=True, text=True)
        if pr.returncode == 0 and pr.stdout.strip() and abs(float(pr.stdout) - a.get("dur", float(pr.stdout))) < 1.5:
            r.returncode = 0
    if r.returncode:
        raise RuntimeError(f"download failed {a['id']} exit {r.returncode}")
    part.replace(p)
    return p


def history():
    f = LIB / "history.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"edits": [], "usage": {}}


def save_history(h):
    (LIB / "history.json").write_text(json.dumps(h, indent=1), encoding="utf-8")
