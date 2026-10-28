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

try:
    import audio_norm   # growth-lab S3 (vendored at the repo root)
except ImportError:     # imported as a package from somewhere else: put the repo root on the path
    import sys
    sys.path.insert(0, str(ROOT))
    import audio_norm

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
# opening hook (p["hook"]), burned in from frame 0 (audit 2026-10-21 #9): Impact, centred, top band y 380-520
HOOK_SIZE, HOOK_LINE, HOOK_WRAP, HOOK_Y = 80, 92, 20, 392   # 20 chars of Impact 80 ~ 800 px of the 1080 width
HOOK_BEATS, HOOK_FADE = 5, 0.3   # held until beat 5 (~2.5-3 s), then faded out
NET = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]   # for http inputs (NASA reels in CI)
WIN = 9 / 16 / (16 / 9)  # width fraction of a 16:9 frame that a 9:16 window covers
LOUDNESS_TARGET = -14    # LUFS, two-pass linear (audio_norm); qc.py accepts -17..-11


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
    grade = s.get("grade") or GRADE   # compose.STYLE: each format has its own look
    sig = hashlib.md5(json.dumps({**{k: s.get(k) for k in keys}, "wh": [W, H, FPS], "grade": grade, "src": src_sig},
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
    vf += [f"fps={FPS}", grade, "format=yuv420p"]
    run([*(NET if s["path"].startswith("http") else []), "-ss", f"{s['src_in']:.3f}", "-t", f"{(L + 0.1) * sp:.3f}", "-i", s["path"], "-vf", ",".join(vf), "-t", f"{L:.3f}", "-an",
         "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-r", FPS, tmp])
    tmp.replace(out)   # atomic: a killed encode never leaves a truncated cache file


def credit_items(p):
    """One string per credit (clip credits in order of first use, the CC BY note, the music)."""
    items = []
    for s in p["shots"]:
        if s["credit"] not in items:
            items.append(s["credit"])
    if any(s["src"] != "nasa" for s in p["shots"]):
        items.append("ESA/Webb and ESA/Hubble material: CC BY 4.0. Cropped to vertical, zooms sped up.")
    items.append("Music: " + p["music"]["credit"])
    return items


def credit_text(p):
    return "Credits\n" + "\n".join(textwrap.fill(c, 64) for c in credit_items(p))


def caption_text(p):
    """Hook, tags, a blank line, then the credits as one paragraph. Credits are joined with '; ' so neighbours never run
    together ('ESA/Hubble, NASA' + 'NASA, ESA...' used to read 'NASA NASA')."""
    return f"{p['hook']}\n{' '.join(p['hashtags'])}\n\nCredits: " + "; ".join(credit_items(p))


def _fade_out(b, d):
    """Alpha 1 until b-d, then down to 0 at b (drawtext alpha expression; only if/lt/arithmetic so tests can evaluate it)."""
    return f"if(lt(t,{b - d:.2f}),1,if(lt(t,{b:.2f}),({b:.2f}-t)/{d},0))"


def text_layers(p):
    """Every drawtext layer of the edit: {text, y, size, font, alpha, t0, hook}. The hook is burned in fully opaque from frame 0
    (no fade from black, no fade-in) in the band y 380-520, below TikTok's top UI and clear of the headline band, and fades
    out over HOOK_FADE s at beat HOOK_BEATS. Headlines of the first shot are also fully visible from frame 0; later ones fade in."""
    P, T0 = p["P"], p["T0"]
    bt = lambda k: T0 + k * P
    slot_at, k = [], 0
    for s in p["shots"]:
        slot_at.append((k, s["slot"]))
        k += s["beats"]
    out = []
    lines = textwrap.wrap(p["hook"].upper(), HOOK_WRAP) if p.get("hook") else []
    y0 = HOOK_Y + (HOOK_LINE // 2 if len(lines) == 1 else 0)
    for i, line in enumerate(lines):
        out.append(dict(text=line, y=y0 + i * HOOK_LINE, size=HOOK_SIZE, font="impact", t0=0.0, hook=True,
                        alpha=_fade_out(bt(HOOK_BEATS), HOOK_FADE)))
    for tx in p["texts"]:
        slot = next((sl for a, sl in slot_at if a == tx["k0"]), "")
        y = tx["y"]
        if y in ("y1", "y2"):
            y = TEXT_Y.get(slot, (1010, 1130))[0 if y == "y1" else 1]
        b = bt(tx["k1"]) - 0.15
        if tx["k0"] == 0:   # on screen with the first frame, like the hook
            a, al = 0.0, _fade_out(b, 0.4)
        else:
            a = bt(tx["k0"]) + 0.15
            al = f"if(lt(t,{a:.2f}),0,if(lt(t,{a + 0.4:.2f}),(t-{a:.2f})/0.4,{_fade_out(b, 0.4)}))"
        out.append(dict(text=tx["text"], y=y, size=tx["size"], font=tx["font"], t0=a, hook=False, alpha=al,
                        color=tx.get("color", "white")))
    return out


def post_chain(p, last, t_end, txt):
    """Final video filter: no fade-in (the hook must be readable at t=0), fade out at the end, then the text layers."""
    return f"[{last}]fade=t=out:st={t_end - 1.0:.2f}:d=1.0," + ",".join(txt) + ",format=yuv420p[v]"


def audio_filter(p, t_end):
    """Music from plan['music']['start'] (its best section, compose/music.pick_start) for t_end seconds. A 0.05 s fade-in only
    removes the click of starting mid-track; the first second must already be at full level. No loudnorm here: single-pass
    loudnorm is dynamic and flattened the build into the reveal, so normalize_audio() runs a two-pass linear one afterwards."""
    if p.get("silent"):
        return "aresample=48000"
    st = float(p["music"].get("start") or 0.0)
    return (f"atrim=start={st:.2f}:end={st + t_end:.2f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.05,"
            f"afade=t=out:st={t_end - 1.8:.2f}:d=1.8,aresample=48000")


def normalize_audio(final, silent=False):
    """Two-pass LINEAR loudnorm of the finished mp4 to LOUDNESS_TARGET (one constant gain, so the music's own build is kept;
    a limiter runs first if more than +6 dB is needed). The video stream is copied. On any failure the un-normalised mix is
    kept and qc's loudness window decides. Returns audio_norm's result dict, or None (silent edit or failure)."""
    if silent:
        return None
    tmp = str(final) + ".norm.mp4"
    try:
        res = audio_norm.normalize(str(final), tmp, target=LOUDNESS_TARGET, keep_video=True, ffmpeg=FFMPEG, log=print)
        os.replace(tmp, str(final))
        print(f"[audio] {res.get('input_i')} -> {res.get('output_i')} LUFS ({res.get('normalization_type')}"
              f"{', limited' if res.get('limited') else ''})")
        return res
    except Exception as e:
        print(f"[audio] two-pass loudnorm failed, keeping the raw mix: {type(e).__name__}: {str(e)[:200]}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return None


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
    txt = []
    for L in text_layers(p):
        safe = L["text"].replace("\\", "").replace(":", "\\:").replace("'", "’").replace("%", "\\%")
        txt.append(f"drawtext=fontfile='{FONTS[L['font']]}':text='{safe}':fontsize={L['size']}:fontcolor={L.get('color', 'white')}:borderw=4:bordercolor=black@0.85"
                   f":x=(w-tw)/2:y={L['y']}:alpha='{L['alpha']}'")
    cdir = ROOT / "out/pipe_tmp"
    cdir.mkdir(parents=True, exist_ok=True)
    c0 = bt(60 - CARD_BEATS)
    cal = f"if(lt(t,{c0:.2f}),0,min(1,(t-{c0:.2f})/0.5))"
    lines = credit_text(p).split("\n")
    box_h = len(lines) * 31 + 40
    txt.append(f"drawbox=x=(iw-900)/2:y=700:w=900:h={box_h}:color=black@0.55:t=fill:enable='gte(t,{c0:.2f})'")
    for i, line in enumerate(lines):   # one drawtext per line so each is centred (text_align needs ffmpeg 7+, CI has 6.1)
        lf = cdir / f"credits_{p['seed']}_{i}.txt"
        lf.write_bytes(line.encode("utf-8"))
        txt.append(f"drawtext=fontfile='{FONTS['plain']}':textfile='{lf.relative_to(ROOT).as_posix()}':fontsize=24:fontcolor=white@0.95:"
                   f"x=(w-tw)/2:y={718 + i * 31}:alpha='{cal}'")
    post = post_chain(p, last, t_end, txt)
    script = cdir / f"filter_{p['seed']}.txt"
    script.write_text(";\n".join(chain + [post]), encoding="utf-8")
    inputs = [x for s in shots for x in ("-i", s["file"])]
    if p.get("silent"):
        src = ["-f", "lavfi", "-t", f"{t_end:.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
    else:
        src = ["-i", ROOT / p["music"]["file"]]
    af = audio_filter(p, t_end)
    run([*inputs, *src, "-filter_complex_script", script, "-map", "[v]", "-map", f"{len(shots)}:a",
         "-af", af,
         "-t", f"{t_end:.2f}", "-c:v", "libx264", "-profile:v", "high", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", final])
    normalize_audio(final, silent=bool(p.get("silent")))
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
