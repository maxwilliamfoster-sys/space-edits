"""Automatic checks on a finished edit: TikTok spec, black frames, loudness."""
import json
import re
from fractions import Fraction
import subprocess

from .lib import FFMPEG, FFPROBE


def probe(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout)


def check(path, silent=False):
    j = probe(path)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    dur = float(j["format"]["duration"])
    fps = float(Fraction(v["r_frame_rate"]))
    size_mb = int(j["format"]["size"]) / 1e6
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=0.4:pix_th=0.02", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    err = r.stderr
    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
    mid_black = sum(min(b, dur - 1.5) - max(a, 1.0) for a, b in blacks if b > 1.0 and a < dur - 1.5)
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    lufs = float(lufs[-1]) if lufs else None
    checks = {
        "1080x1920": (v["width"], v["height"]) == (1080, 1920),
        "30fps": abs(fps - 30) < 0.01,
        "h264 high yuv420p": v["codec_name"] == "h264" and v["profile"] == "High" and v["pix_fmt"] == "yuv420p",
        "aac 48k": bool(a) and a["codec_name"] == "aac" and a["sample_rate"] == "48000",
        "length 20-60s": 20 <= dur <= 60,
        "size < 200MB": size_mb < 200,
        "no mid black (<0.8s)": mid_black < 0.8,
        "loudness -17..-11 LUFS": silent or (lufs is not None and -17 <= lufs <= -11),
    }
    return dict(ok=all(checks.values()), checks=checks, dur=round(dur, 2), size_mb=round(size_mb, 1), lufs=lufs, black=blacks)
