"""Render a plan (compose.plan) to a TikTok-ready 1080x1920 mp4: per-shot cache, xfade chain, text, credit card, music."""
import hashlib
import json
import os
import subprocess
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .lib import FFMPEG, ROOT, ensure_local

W, H, FPS = 1080, 1920, 30
CACHE = ROOT / "out/pipe_cache"
if os.name == "nt":
    FONTS = {"impact": "C\\:/Windows/Fonts/impact.ttf", "arial": "C\\:/Windows/Fonts/arialbd.ttf", "plain": "C\\:/Windows/Fonts/arial.ttf"}
else:   # CI (ubuntu): Anton is the free Impact look-alike (committed in fonts/), Liberation Sans stands in for Arial
    _LIB = "/usr/share/fonts/truetype/liberation/LiberationSans-"
    FONTS = {"impact": (ROOT / "fonts/Anton-Regular.ttf").as_posix(), "arial": _LIB + "Bold.ttf", "plain": _LIB + "Regular.ttf"}
GRADE = "eq=contrast=1.06:saturation=1.1:gamma=0.97,vignette=PI/6"
TEXT_Y = {"earth": (1290, 1410), "planet": (1180, 1300)}
CARD_BEATS = 5          # credit card shows for the last N beats
NET = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]   # for http inputs (NASA reels in CI)
WIN = 9 / 16 / (16 / 9)  # width fraction of a 16:9 frame that a 9:16 window covers


def run(args, **kw):
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", *map(str, args)], capture_output=True, text=True, cwd=ROOT, **kw)
    if r.returncode:
        raise RuntimeError(r.stderr[-1500:])


def best_fx(path, t):
    """Horizontal crop position (0..1) of the 9:16 window with the most light/detail at time t."""
    r = subprocess.run([FFMPEG, "-loglevel", "error", *(NET if path.startswith("http") else []), "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-vf", "scale=192:108,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    if len(r.stdout) != 192 * 108:
        return 0.5
    f = np.frombuffer(r.stdout, np.uint8).reshape(108, 192).astype(float)
    ww = int(192 * WIN)
    scores = [f[:, x:x + ww].mean() + 0.5 * f[:, x:x + ww].std() for x in range(0, 192 - ww + 1)]
    return round(float(np.argmax(scores)) / max(1, 192 - ww), 3)


def resolve_fx(s):
    if isinstance(s["fx"], str):
        t = s["src_in"] + (1.0 if s["fx"] == "auto@3" else s["len"] * s["speed"] / 2)
        s["fx"] = best_fx(s["path"], t)
    return s["fx"]


def render_shot(s):
    resolve_fx(s)
    keys = ("path", "src_in", "len", "fx", "z", "speed", "mode", "box")
    src_sig = [0, 0] if s["path"].startswith("http") else [Path(s["path"]).stat().st_size, int(Path(s["path"]).stat().st_mtime)]
    sig = hashlib.md5(json.dumps({**{k: s.get(k) for k in keys}, "wh": [W, H, FPS], "grade": GRADE, "src": src_sig},
                                 sort_keys=True).encode()).hexdigest()[:12]
    out = CACHE / f"{s['asset']}_{sig}.mp4"
    s["file"] = str(out)
    if out.exists():
        return
    tmp = out.with_suffix(".tmp.mp4")
    L, sp = s["len"], s["speed"]
    z0, z1 = s["z"]
    zexpr = f"({z0}+({z1}-{z0})*t/{L:.3f})"
    if s["mode"] == "box":
        x, y, w, h = s["box"]
        hh = int(W * h / w) // 2 * 2
        vf = [f"crop={w}:{h}:{x}:{y}", f"scale={W}:{hh}:flags=lanczos", f"pad={W}:{H}:0:({H}-{hh})/2-60:black"]
    else:
        vf = [f"crop=w='trunc(ih*9/16/2)*2':h=ih:x='(iw-ow)*{s['fx']}':y=0",
              f"scale=w='2*ceil({W}*{zexpr}/2)':h='2*ceil({H}*{zexpr}/2)':eval=frame:flags=lanczos", f"crop={W}:{H}"]
    if sp != 1.0:
        vf.insert(0, f"setpts=PTS/{sp}")
    vf += [f"fps={FPS}", GRADE, "format=yuv420p"]
    run([*(NET if s["path"].startswith("http") else []), "-ss", f"{s['src_in']:.3f}", "-t", f"{(L + 0.1) * sp:.3f}", "-i", s["path"], "-vf", ",".join(vf), "-t", f"{L:.3f}", "-an",
         "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-r", FPS, tmp])
    tmp.replace(out)   # atomic: a killed encode never leaves a truncated cache file


def credit_text(p):
    seen, lines = [], []
    for s in p["shots"]:
        if s["credit"] not in seen:
            seen.append(s["credit"])
    for c in seen:
        lines.append(textwrap.fill(c, 64))
    if any(s["src"] != "nasa" for s in p["shots"]):
        lines.append(textwrap.fill("ESA/Webb and ESA/Hubble material: CC BY 4.0. Cropped to vertical, zooms sped up.", 64))
    lines.append(textwrap.fill("Music: " + p["music"]["credit"], 64))
    return "Credits\n" + "\n".join(lines)


def caption_text(p):
    credits = credit_text(p).replace("\n", " ")
    return f"{p['hook']}\n{' '.join(p['hashtags'])}\n\n{credits}"


def assemble(p, final):
    shots = p["shots"]
    P, T0 = p["P"], p["T0"]
    bt = lambda k: T0 + k * P
    cuts, k = [0.0], 0
    for s in shots:
        k += s["beats"]
        cuts.append(bt(k))
    t_end = bt(60)
    for i, s in enumerate(shots):
        s["c0"] = cuts[i]
    chain, last = [f"[{i}:v]tpad=stop_mode=clone:stop_duration=0.3[p{i}]" for i in range(len(shots))], "p0"
    for i in range(1, len(shots)):
        typ, d = shots[i]["tin"]
        chain.append(f"[{last}][p{i}]xfade=transition={typ}:duration={d}:offset={shots[i]['c0'] - d / 2:.3f}[x{i}]")
        last = f"x{i}"
    slot_at = []
    k = 0
    for s in shots:
        slot_at.append((k, k + s["beats"], s["slot"]))
        k += s["beats"]
    txt = []
    for tx in p["texts"]:
        slot = next((sl for a, b, sl in slot_at if a == tx["k0"]), "")
        y = tx["y"]
        if y in ("y1", "y2"):
            y = TEXT_Y.get(slot, (1010, 1130))[0 if y == "y1" else 1]
        a, b = bt(tx["k0"]) + 0.15, bt(tx["k1"]) - 0.15
        al = f"if(lt(t,{a:.2f}),0,if(lt(t,{a + 0.4:.2f}),(t-{a:.2f})/0.4,if(lt(t,{b - 0.4:.2f}),1,if(lt(t,{b:.2f}),({b:.2f}-t)/0.4,0))))"
        safe = tx["text"].replace("\\", "").replace(":", "\\:").replace("'", "’").replace("%", "\\%")
        txt.append(f"drawtext=fontfile='{FONTS[tx['font']]}':text='{safe}':fontsize={tx['size']}:fontcolor=white:borderw=4:bordercolor=black@0.85"
                   f":x=(w-tw)/2-60:y={y}:alpha='{al}'")
    cdir = ROOT / "out/pipe_tmp"
    cdir.mkdir(parents=True, exist_ok=True)
    ct = cdir / f"credits_{p['seed']}.txt"
    ct.write_bytes(credit_text(p).encode("utf-8"))
    c0 = bt(60 - CARD_BEATS)
    cal = f"if(lt(t,{c0:.2f}),0,min(1,(t-{c0:.2f})/0.5))"
    nlines = credit_text(p).count("\n") + 1
    box_h = nlines * 31 + 40
    txt.append(f"drawbox=x=36:y=700:w=900:h={box_h}:color=black@0.55:t=fill:enable='gte(t,{c0:.2f})'")
    txt.append(f"drawtext=fontfile='{FONTS['plain']}':textfile='{ct.relative_to(ROOT).as_posix()}':fontsize=24:line_spacing=7:fontcolor=white@0.95:"
               f"x=56:y=718:alpha='{cal}'")
    post = f"[{last}]fade=t=in:st=0:d=0.5,fade=t=out:st={t_end - 1.0:.2f}:d=1.0," + ",".join(txt) + ",format=yuv420p[v]"
    script = cdir / f"filter_{p['seed']}.txt"
    script.write_text(";\n".join(chain + [post]), encoding="utf-8")
    inputs = [x for s in shots for x in ("-i", s["file"])]
    m = p["music"]
    if p.get("silent"):
        src, af = ["-f", "lavfi", "-t", f"{t_end:.2f}", "-i", "anullsrc=r=48000:cl=stereo"], "aresample=48000"
    else:
        src = ["-i", ROOT / m["file"]]
        af = f"atrim=0:{t_end:.2f},afade=t=in:st=0:d=0.5,afade=t=out:st={t_end - 1.8:.2f}:d=1.8,loudnorm=I=-14:TP=-1,aresample=48000"
    run([*inputs, *src, "-filter_complex_script", script, "-map", "[v]", "-map", f"{len(shots)}:a",
         "-af", af,
         "-t", f"{t_end:.2f}", "-c:v", "libx264", "-profile:v", "high", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", final])
    return t_end


def render(p, final):
    CACHE.mkdir(parents=True, exist_ok=True)
    for s in p["shots"]:
        if s["src"] != "nasa":
            s["path"] = str(ensure_local(dict(path=s["path"], src=s["src"], url=s["url"], id=s["asset"])))
        elif not Path(s["path"]).exists():
            s["path"] = s["url"]   # no local copy (CI): ffmpeg seeks straight into the NASA reel over HTTP
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(render_shot, p["shots"]))
    return assemble(p, final)
