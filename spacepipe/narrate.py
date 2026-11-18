"""Narrator (2026-11-18, Max: "sound like an educational science programme"): a calm British documentary voice that says
what the edit shows (the named target, its distance, one fact, how old its light is) in short cues timed to the on-screen text.

Voice: Kokoro-82M `bm_george` (British male), British English, a little under normal speed. It runs on the CPU of a free
GitHub runner in seconds, reads exactly the words written (no generative mumbling, so no Whisper check is needed) and needs no
key. edge-tts was ruled out: it answers 403 to GitHub runners since Oct 2026 (chess bot). Chatterbox (youtube-automation) is
more expressive but costs minutes per edit on CPU and can garble words; it is the upgrade to try if narration wins its test.

Every number comes from the plan (the clip's own ESA distance, facts.ERAS) and is read as words ("about six thousand five
hundred light-years"). TARGET_FACTS holds one checked fact for the best-known targets; others get only name, distance and era.

Each cue has a time window. A cue that does not fit drops its optional sentences, then is skipped, so speech never runs over
the next cue or the end. If the voice cannot load, the edit is rendered without narration and the plan says so
(plan["narration"]["cues"] is empty), so the narrated-vs-music-only comparison only ever counts edits that really had a voice.
"""
import hashlib
import os
import re
import wave
from pathlib import Path

import numpy as np

from . import facts
from .lib import ROOT

VOICE = os.environ.get("NARRATOR_VOICE", "bm_george")
LANG = "en-gb"
SPEED = float(os.environ.get("NARRATOR_SPEED", "0.94"))   # calm, a touch slower than conversational
MODEL_DIR = Path(os.environ.get("KOKORO_DIR") or ROOT / "kokoro_models")
MODEL_FILE, VOICES_FILE = "kokoro-v1.0.onnx", "voices-v1.0.bin"
SR = 24000                  # Kokoro's sample rate
CACHE = ROOT / "out/pipe_cache/tts"
GAP = 0.3                   # silence kept between two cues
TARGET_RMS_DB = -16.0       # speech level of every clip (dBFS RMS over voiced samples); peaks capped at PEAK_DB
PEAK_DB = -1.0              # (at -19 the mix sat near -21 LUFS and the final loudnorm needed up to 7 dB of limiting)
DUCK_DB = -12.0             # music level under the voice
DUCK_RAMP = 0.3             # s for the music to dip before a cue and come back after it

# One fact per famous target, each a standard published one (ESA/NASA release pages):
TARGET_FACTS = {
    "THE SOMBRERO GALAXY": "Its bright centre hides a black hole about a billion times the mass of our Sun.",
    "THE WHIRLPOOL GALAXY": "Its spiral arms are sharpened by the pull of a smaller galaxy beside it.",
    "THE BUTTERFLY NEBULA": "Its central star is one of the hottest known in our galaxy, over two hundred thousand degrees.",
    "THE PILLARS OF CREATION": "New stars are forming inside these towers of gas and dust.",
    "THE HORSEHEAD NEBULA": "It's a dark cloud of dust, seen against glowing gas behind it.",
    "THE CIGAR GALAXY": "It's making new stars about ten times faster than our own Milky Way.",
    "THE PHANTOM GALAXY": "We see this spiral almost perfectly face-on.",
    "THE COSMIC CLIFFS": "Some of these clouds stand about seven light-years tall.",
}
# facts.ERAS lines as a narrator says them (index-matched; a test checks the two lists stay the same length)
ERA_SPOKEN = ["before Earth even existed", "before the age of the dinosaurs", "in the age of the dinosaurs",
              "before humans existed", "before the last ice age ended", "before the Great Pyramid was built",
              "before the Roman Empire began", "long before you were born"]
# The zoom-out's fixed on-screen pairs (facts.EARTH/SUN/PLANET/MW), spoken. Distance lines are built in spoken_pair().
SPOKEN = {
    ("THIS IS EARTH", "SEEN FROM 400 KM UP"): "This is Earth, seen from four hundred kilometres up.",
    ("HOME", "12,742 KM ACROSS"): "Home. Almost thirteen thousand kilometres across.",
    ("EVERYONE YOU KNOW", "LIVES ON THIS"): "Everyone you know lives here.",
    ("EVERY WAR. EVERY SONG.", "ALL OF IT HAPPENED HERE"): "Every war, every song. All of it happened here.",
    ("THIS IS WHERE YOU LIVE", "SEEN FROM 400 KM UP"): "This is where you live, seen from four hundred kilometres up.",
    ("8 BILLION PEOPLE", "ONE PLANET"): "Eight billion people. One planet.",
    ("EARTH", "THE ONLY PLANET KNOWN TO HOST LIFE"): "Earth. The only planet known to host life.",
    ("THE SPACE STATION", "CIRCLES EARTH ABOUT EVERY 90 MINUTES"): "The space station circles Earth about every ninety minutes.",
    ("THE SUN", "1.3 MILLION EARTHS FIT INSIDE"): "The Sun. One point three million Earths would fit inside.",
    ("OUR STAR", "99.8% OF THE SOLAR SYSTEM'S MASS"): "Our star holds ninety-nine point eight per cent of the solar system's mass.",
    ("THE SUN", "ONE STAR OF HUNDREDS OF BILLIONS"): "The Sun. One star among hundreds of billions.",
    ("THE SUN", "ABOUT 150 MILLION KM AWAY"): "The Sun, about a hundred and fifty million kilometres away.",
    ("SUNLIGHT", "TAKES ABOUT 8 MINUTES TO REACH US"): "Sunlight takes about eight minutes to reach us.",
    ("SATURN", "ABOUT 9 EARTHS WIDE"): "Saturn, about nine Earths wide.",
    ("URANUS", "ABOUT 4 EARTHS WIDE"): "Uranus, about four Earths wide.",
    ("NEPTUNE", "ABOUT 4 EARTHS WIDE"): "Neptune, about four Earths wide.",
    ("JUPITER", "OVER 1,300 EARTHS FIT INSIDE"): "Jupiter. Over thirteen hundred Earths would fit inside.",
    ("A WORLD", "FAR FROM HOME"): "A world, far from home.",
    ("OUR GALAXY", "100+ BILLION STARS"): "Our galaxy. More than a hundred billion stars.",
    ("THE MILKY WAY", "ABOUT 100,000 LIGHT-YEARS ACROSS"): "The Milky Way, about a hundred thousand light-years across.",
    ("OUR GALAXY", "THE SUN IS 1 STAR OF 100+ BILLION"): "Our galaxy. The Sun is one star of more than a hundred billion.",
    ("THE MILKY WAY", "WE ARE ABOUT 26,000 LIGHT-YEARS FROM ITS CENTRE"):
        "The Milky Way. We're about twenty-six thousand light-years from its centre.",
    ("ONE GALAXY", "OF HUNDREDS OF BILLIONS"): "One galaxy, of hundreds of billions.",
    ("REAL IMAGE", "FROM A SPACE TELESCOPE"): "A real image, from a space telescope.",
    ("ONE TINY PATCH OF SKY", "THOUSANDS OF GALAXIES"): "One tiny patch of sky. Thousands of galaxies.",
    ("MOST OF THESE SMUDGES", "ARE WHOLE GALAXIES"): "Most of these smudges are whole galaxies.",
}
# The time machine says one of these per shot, in turn, so five shots never repeat one sentence
TIME_LINES = ["This light left about {d} years ago.", "This one set off about {d} years ago.",
              "Here, the light is about {d} years old.", "This light has travelled for about {d} years.",
              "And this light left about {d} years ago."]
# The zoom-out's first shots are 2-3 s, too short for most full lines: a short line said instead (by slot).
SHORT = {"earth": "This is Earth.", "sun": "Our Sun.", "mw": "Our galaxy, the Milky Way."}

# ------------------------------------------------------------------------------------------- numbers as words --
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _under_100(n):
    return _ONES[n] if n < 20 else _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")


def _under_1000(n):
    h, r = divmod(n, 100)
    if not h:
        return _under_100(r)
    return f"{_ONES[h]} hundred" + (f" and {_under_100(r)}" if r else "")


def int_words(n):
    """British: 6500 -> 'six thousand five hundred', 250 -> 'two hundred and fifty', 1050 -> 'one thousand and fifty'."""
    n = int(n)
    if n < 1000:
        return _under_1000(n)
    parts = []
    for size, name in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            parts.append(f"{_under_1000(n // size)} {name}")
            n %= size
    if n:
        parts.append(("and " if n < 100 else "") + _under_1000(n))
    return " ".join(parts)


def num_words(x):
    """2.8 -> 'two point eight', 30 -> 'thirty', 12.5 -> 'twelve point five'."""
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    whole, _, frac = s.partition(".")
    return int_words(int(whole)) + ("" if not frac else " point " + " ".join(_ONES[int(c)] for c in frac))


def distance_words(dist_ly):
    """The catalogue's distance string as a narrator reads it: '6,500' -> 'six thousand five hundred', '2.8 billion' ->
    'two point eight billion', '30 million' -> 'thirty million'. None if it does not parse or is under 1 (a scraped '000')."""
    from .compose import ly_value
    v = ly_value(dist_ly)
    if not v or v < 1:
        return None
    for size, name in ((1e9, "billion"), (1e6, "million")):
        if v >= size:
            return _a(f"{num_words(round(v / size, 2))} {name}")
    return _a(int_words(round(v)))


def _a(words):
    """'one hundred million' -> 'a hundred million', as people say it after 'about'."""
    return re.sub(r"^one (hundred|thousand)\b", r"a \1", words)


def place_fact(asset):
    """A line that is true of any target from its tier and distance alone, for targets without a TARGET_FACTS entry."""
    from .compose import IN_OUR_GALAXY_LY, OTHER_GALAXY_LY, ly_value
    v = ly_value(asset.get("dist_ly")) or 0
    if 1 <= v < IN_OUR_GALAXY_LY:
        return "It's inside our own galaxy, the Milky Way."
    if v >= OTHER_GALAXY_LY:
        return "A whole galaxy, far beyond our own." if asset.get("tier") == "galaxy" else "It lies far beyond our own galaxy."
    return None


def era_spoken(years):
    i = facts.era_index(years)
    return ERA_SPOKEN[i] if i is not None else ""


_CAT = re.compile(r"^(NGC|Messier|MACS|M(?=\d)|Arp|Abell|Terzan|Westerlund|Pismis|Lynds)\s*(.*)$")
_CAT_SAY = {"NGC": "N G C", "M": "Messier", "MACS": "Macs"}


def _cat_number(s):
    """Catalogue numbers the way astronomers say them: 3507 -> 'thirty-five oh seven', 1300 -> 'thirteen hundred',
    253 -> 'two fifty-three', 82 -> 'eighty-two'. Anything with letters is spelt out ('S1063' -> 'S one oh six three')."""
    if not s.isdigit() or s.startswith("0"):
        return " ".join(c if c.isalpha() else ("oh" if c == "0" else _ONES[int(c)]) for c in s if c.isalnum())
    n = int(s)
    if len(s) == 4:
        hi, lo = divmod(n, 100)
        return f"{_under_100(hi)} hundred" if lo == 0 else f"{_under_100(hi)} {'oh ' + _ONES[lo] if lo < 10 else _under_100(lo)}"
    if len(s) == 3:
        hi, lo = divmod(n, 100)
        return f"{_ONES[hi]} {'oh ' + _ONES[lo] if lo < 10 else _under_100(lo)}" if lo else f"{_ONES[hi]} hundred"
    return int_words(n)


def spoken_name(name):
    """'THE PILLARS OF CREATION' -> 'the Pillars of Creation'; 'NGC 3507' -> 'N G C thirty-five oh seven'; 'M77' -> 'Messier seventy-seven'."""
    from .compose import TARGET_NAMES, hook_name
    if name in TARGET_NAMES.values():
        return hook_name(name)
    m = _CAT.match(name)
    if not m:
        return name
    return f"{_CAT_SAY.get(m.group(1), m.group(1))} {_cat_number(m.group(2).replace(' ', ''))}".strip()


def spoken_pair(head, sub):
    """The narrator's line for one on-screen headline + subline of the zoom-out (facts.lines_for). None = say nothing."""
    if (head, sub) in SPOKEN:
        return SPOKEN[(head, sub)]
    m = re.fullmatch(r"(.+?) LIGHT-YEARS AWAY", sub) or re.fullmatch(r"LEFT (.+?) YEARS AGO", sub)
    d = distance_words(m.group(1)) if m else None
    if not d:
        return None
    if sub.startswith("LEFT"):
        return f"This light set off about {d} years ago."
    return {"THIS GALAXY": f"This galaxy is about {d} light-years away.",
            "A CLUSTER OF GALAXIES": f"A whole cluster of galaxies, about {d} light-years away."}.get(head, f"This one is about {d} light-years away.")


# ---------------------------------------------------------------------------------------------------- the script --
def _cue(t0, t1, *parts, options=None):
    """A narration cue: spoken from t0, must end by t1. options = texts in order of preference (the first that fits is said);
    by default parts[0] plus as many of the later parts as fit."""
    parts = [x for x in parts if x]
    options = options or [" ".join(parts[:k]) for k in range(len(parts), 0, -1)]
    return dict(t0=round(t0, 3), t1=round(t1, 3), options=options)


def _is_are(name):
    return "are" if re.search(r"PILLARS|CLIFFS", name) else "is"


def script(p):
    """The narration of a plan, as cues in time order."""
    from .compose import CLOSER_BEATS, ly_value
    P, T0 = p["P"], p["T0"]
    bt = lambda k: T0 + k * P
    total = p.get("total_beats", 60)
    t_end = bt(total) - (0.6 if p.get("loop") else 1.2)
    tname = p["template"]
    cues = []
    if tname in ("target", "flash"):
        s = p["shots"][0]
        mid = total // 2
        name = next(t["text"] for t in p["texts"] if t["k0"] == 0 and t["y"] == "y1")
        d, years = distance_words(s["dist_ly"]), ly_value(s["dist_ly"])
        era = era_spoken(years)
        this = "These" if _is_are(name) == "are" else "This"
        cues.append(_cue(0.3, bt(mid) - 0.2, f"{this} {_is_are(name)} {spoken_name(name)}.",
                         TARGET_FACTS.get(name) or place_fact(s)))
        if d:
            cues.append(_cue(bt(mid) + 0.15, t_end, f"It's about {d} light-years away.",
                             f"The light you're seeing left it {era}." if era else None))
        return cues
    shot_k, k = [], 0
    for s in p["shots"]:
        shot_k.append(k)
        k += s["beats"]
    n = len(p["shots"])
    if tname == "drift":   # 3 long calm shots: one unhurried line each, with room to breathe
        for i, (s, k0) in enumerate(zip(p["shots"], shot_k)):
            d, era = distance_words(s["dist_ly"]), era_spoken(ly_value(s["dist_ly"]))
            end = bt(shot_k[i + 1]) - 0.5 if i + 1 < n else t_end
            first = ["Real footage, from space telescopes."] if i == 0 else []
            say = ([f"This light left about {d} years ago."] if i == 0 else
                   [f"This one is about {d} light-years away." if i < n - 1 else f"And this one, about {d} light-years away."]) if d else []
            cues.append(_cue(0.3 if i == 0 else bt(k0) + 0.6, end, *(first + say), f"Its light left {era}." if d and era and i else None))
        return [c for c in cues if c["options"]]
    if tname == "timemachine":
        for i, (s, k0) in enumerate(zip(p["shots"], shot_k)):
            d, era = distance_words(s["dist_ly"]), era_spoken(ly_value(s["dist_ly"]))
            if not d:
                continue
            start = 0.3 if i == 0 else bt(k0) + 0.2
            end = bt(shot_k[i + 1]) + 0.1 if i + 1 < n else t_end
            core = TIME_LINES[i % len(TIME_LINES)].format(d=d)
            then = f"That was {era}." if era else ""
            opts = [f"{core} {then}".strip(), core]
            if i == 0:   # the idea first, if there is time for it
                opts = [f"Light takes time to reach us. {o}" for o in opts] + opts
            cues.append(_cue(start, end, options=opts))
        return cues
    if tname == "guess":
        for i, (s, k0) in enumerate(zip(p["shots"], shot_k)):
            k1 = min(k0 + s["beats"], 60 - CLOSER_BEATS)
            mid = k0 + max(2, (k1 - k0) // 2)
            d = distance_words(s["dist_ly"])
            cues.append(_cue(0.3 if i == 0 else bt(k0) + 0.2, bt(mid) - 0.15,
                             "How far away is this? Take a guess." if i == 0 else "And this one?"))
            if d:
                end = bt(shot_k[i + 1]) + 0.1 if i + 1 < n else t_end
                cues.append(_cue(bt(mid) + 0.15, end, f"About {d} light-years."))
        return cues
    if tname == "countdown":
        for i, (s, k0) in enumerate(zip(p["shots"], shot_k)):
            d = distance_words(s["dist_ly"])
            end = bt(shot_k[i + 1]) + 0.1 if i + 1 < n else t_end
            cues.append(_cue(0.3 if i == 0 else bt(k0) + 0.2, end, f"Number {_ONES[n - i]}.",
                             f"About {d} light-years away." if d else None))
        return cues
    # the zoom-out: speak each slot's on-screen pair while it is on screen (a short line if the full one won't fit); a line
    # already said in this edit is not said again
    heads = [t for t in p["texts"] if t["y"] == "y1"]
    slot_at = dict(zip(shot_k, (s["slot"] for s in p["shots"])))
    said = set()
    for j, h in enumerate(heads):
        sub = next((t["text"] for t in p["texts"] if t["y"] == "y2" and t["k0"] == h["k0"]), "")
        slot = slot_at.get(h["k0"], "")
        short = SHORT.get(slot) or (f"{h['text'].title()}." if slot == "planet" and h["text"] != "A WORLD" else None)
        opts = [o for o in dict.fromkeys([spoken_pair(h["text"], sub), short]) if o and o not in said]
        if not opts:
            continue
        said.update(opts)
        start = 0.3 if h["k0"] == 0 else bt(h["k0"]) + 0.2
        end = bt(heads[j + 1]["k0"]) + 0.1 if j + 1 < len(heads) else t_end
        cues.append(_cue(start, end, options=opts))
    return cues


# ------------------------------------------------------------------------------------------------- the voice --
_KOKORO = None


def available():
    return (MODEL_DIR / MODEL_FILE).exists() and (MODEL_DIR / VOICES_FILE).exists()


def _engine():
    global _KOKORO
    if _KOKORO is None:
        from kokoro_onnx import Kokoro
        _KOKORO = Kokoro(str(MODEL_DIR / MODEL_FILE), str(MODEL_DIR / VOICES_FILE))
    return _KOKORO


def _trim(x, thr=0.01):
    idx = np.flatnonzero(np.abs(x) > thr)
    if not len(idx):
        return x[:0]
    a, b = max(0, idx[0] - int(0.03 * SR)), min(len(x), idx[-1] + int(0.08 * SR))
    return x[a:b]


def _level(x):
    """Same speech level for every clip: RMS over the voiced part to TARGET_RMS_DB, peaks no higher than PEAK_DB."""
    voiced = x[np.abs(x) > 0.02]
    if not len(voiced):
        return x
    g = 10 ** (TARGET_RMS_DB / 20) / float(np.sqrt(np.mean(voiced ** 2)))
    g = min(g, 10 ** (PEAK_DB / 20) / float(np.max(np.abs(x))))
    return (x * g).astype(np.float32)


def say(text, speed=SPEED):
    """One clip of speech (float32 mono at SR), trimmed and levelled. Cached on disk by voice, speed and text."""
    key = hashlib.md5(f"{VOICE}|{LANG}|{speed}|{TARGET_RMS_DB}|{PEAK_DB}|{text}".encode()).hexdigest()[:16]
    f = CACHE / f"{key}.npy"
    if f.exists():
        return np.load(f)
    samples, sr = _engine().create(text, voice=VOICE, speed=speed, lang=LANG)
    assert sr == SR, sr
    x = _level(_trim(np.asarray(samples, dtype=np.float32)))
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(f, x)
    return x


def fit(cues, synth=say, gap=GAP):
    """Speech for each cue that fits its window: whole first, then without its optional sentences, then a little faster.
    A cue that still overruns is skipped. Returns [(t0, t1, text, samples)] in time order, never overlapping."""
    out, free_at = [], 0.0
    for c in cues:
        t0 = max(c["t0"], free_at)
        room = c["t1"] - t0
        for text in c["options"]:
            for speed in (SPEED, SPEED * 1.08):
                x = synth(text, speed)
                if 0 < len(x) / SR <= room:
                    out.append((round(t0, 3), round(t0 + len(x) / SR, 3), text, x))
                    free_at = t0 + len(x) / SR + gap
                    break
            else:
                continue
            break
    return out


def write_track(clips, t_end, path):
    """One mono wav at Kokoro's 24 kHz, as long as the edit, every clip at its time (ffmpeg resamples it to 48 kHz properly;
    repeating samples would add harsh aliasing)."""
    y = np.zeros(int(round(t_end * SR)) + 1, np.float32)
    for t0, _, _, x in clips:
        a = int(round(t0 * SR))
        b = min(len(y), a + len(x))
        y[a:b] += x[:b - a]
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def duck_expr(spans, gain_db=DUCK_DB, ramp=DUCK_RAMP):
    """ffmpeg volume expression (eval=frame): 1 outside the spoken spans, gain_db under them, with ramp-s slopes either side."""
    if not spans:
        return "1"
    g = 10 ** (gain_db / 20)
    bumps = "+".join(f"clip((t-{a - ramp:.2f})/{ramp},0,1)*clip(({b + ramp:.2f}-t)/{ramp},0,1)" for a, b in spans)
    return f"1-{1 - g:.4f}*min(1,{bumps})"


def build(p, workdir):
    """Narrate plan p into workdir/narration.wav. Sets p['narration'] = {voice, cues: [{t0, t1, text}], file}. On any failure
    the edit goes out without a voice: cues == [] and the reason is kept, so the A/B never counts it as narrated."""
    P, T0 = p["P"], p["T0"]
    t_end = T0 + p.get("total_beats", 60) * P
    info = dict(voice=f"kokoro/{VOICE}/{LANG}@{SPEED}", cues=[], file=None)
    p["narration"] = info
    try:
        if not available():
            raise FileNotFoundError(f"Kokoro model files not in {MODEL_DIR}")
        clips = fit(script(p))
        if not clips:
            raise RuntimeError("no cue fitted its window")
        path = Path(workdir) / f"narration_{p['seed']}.wav"
        write_track(clips, t_end, path)
        info.update(file=str(path), cues=[dict(t0=a, t1=b, text=t) for a, b, t, _ in clips])
        print(f"[narration] {len(clips)} cue(s): " + " | ".join(t for _, _, t, _ in clips))
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"[narration] FAILED, rendering without a voice: {info['error']}")
    return info
