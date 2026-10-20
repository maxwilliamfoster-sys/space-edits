"""Download NASA library videos' ~orig.mp4 into raw/nasa/<name>.mp4 (resumable). py -I -X utf8 spacepipe/dl_nasa.py"""
import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ITEMS = {
    "earth_exp67": "jsc2023m000111_Earth_in_4K_Space_Station_Expedition_67_68_Edition",
    "earth_views21": "jsc2021m000138_4K_Earth_Views_Extended_Cut_for_Earth_Day_ 2021_210422-4KMP4",
    "sdo_year6": "GSFC_20160212_SDO_m12144_Year6",
    "sdo_year7": "GSFC_20170211_SDO_m12500_Year7",
    "mercury_transit": "GSFC_20160601_Mercury_m12268_Transit_4K",
}
for name, nid in ITEMS.items():
    dest = ROOT / "raw/nasa" / f"{name}.mp4"
    a = json.load(urllib.request.urlopen(urllib.request.Request("https://images-api.nasa.gov/asset/" + urllib.parse.quote(nid), headers={"User-Agent": "capcut-automation"}), timeout=30))
    orig = next(i["href"] for i in a["collection"]["items"] if "~orig" in i["href"] and i["href"].endswith(".mp4"))
    orig = orig.replace("http://", "https://").replace(" ", "%20")
    print(name, orig, flush=True)
    r = subprocess.run(["curl", "-sfL", "-C", "-", "-o", str(dest), orig])
    print("  exit", r.returncode, round(dest.stat().st_size / 1e6) if dest.exists() else 0, "MB", flush=True)
