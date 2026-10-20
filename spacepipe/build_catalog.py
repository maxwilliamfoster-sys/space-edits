"""Build library/catalog.json: every ESA/Webb + ESA/Hubble 4K video that is real imagery (pan/zoom/transition/planet), with its
exact credit line, duration, subject, tier and any sourced distance. py -I spacepipe/build_catalog.py  (needs out/audit/esa_titles.json)."""
import html
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 capcut-automation/0.2 (personal)"}
BASE = {"webb": "https://esawebb.org/videos/", "hubble": "https://esahubble.org/videos/"}
CDN = {"webb": "https://cdn.esawebb.org/archives/videos/ultra_hd/%s.mp4", "hubble": "https://cdn.esahubble.org/archives/videos/ultra_hd/%s.mp4"}
DROP = re.compile(r"artist|animation|animated|simulat|schematic|illustrat|concept|impression|diagram|hubblecast|space sparks|spacecraft|instrument|"
                  r"interview|explain|\bhow\b|\bwhy\b|\bwhat\b|mirror|launch|deploy|team|trailer|logo|credit|annivers|celebrat|news|live|talk|"
                  r"flight to|fly[- ]?through|flight through|tour|journey|expansion|merging|collision|model|timeline|spectr|graph|chart|compar|"
                  r"orbit|simulation|visuali[sz]ation|evolution|formation|rotating globe|earth|moon|mars", re.I)
PLANETS = re.compile(r"saturn|uranus|neptune|jupiter|titan|pluto", re.I)
DEEP = re.compile(r"deep field|ultra deep|xdf|smacs|pandora|abell|macs j|sdss j|lensed|lensing|cluster of galax|galaxy cluster|el gordo|jades|ceers", re.I)
GAL = re.compile(r"galax|arp \d|m ?\d\d|messier|andromeda|triangulum|spiral|cartwheel|whirlpool|sombrero|antennae|centaurus a|ngc ?(?:\d{3,4})\b.*galax|"
                 r"stephan|seyfert|ic ?\d{3,4}.*galax|ngc 3256|ngc 7469|ngc 5068|ngc 628|ngc 1365|ngc 4535|ngc 1566|ngc 2090|ngc 253|ngc 1672|ngc 3627|ngc 1433|"
                 r"ngc 4258|ngc 5866|ngc 7496|ngc 1300|ngc 3351|ngc 4254|ngc 4321|ngc 4303|ngc 1087|ngc 1385|ngc 3059", re.I)
NEB = re.compile(r"nebula|pillars|carina|orion|crab|cassiopeia|cas a|supernova|protostar|stellar|jet|ngc 346|ngc 602|ngc 1333|ic 348|cluster|rho oph|"
                 r"hh ?\d|l1527|wolf-rayet|ring|cat.s paw|lynds|sh2|pismis|omc|nircam image|miri image|horsehead|butterfly|tarantula|carina|veil|eagle|"
                 r"lagoon|trifid|helix|cosmic cliffs|southern ring|ngc 7129|ngc 6302|ngc 3324|sagittarius|digel|bar region|dust", re.I)


def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40).read().decode("utf8", "ignore")


def classify(title):
    t = title.strip()
    if DROP.search(t):
        return None, None
    low = t.lower()
    kind = ("zoom" if re.match(r"^(zoom|zooming|pullout)", low) else "pan" if re.match(r"^pan\b", low) else
            "transition" if "transition" in low else "still-video")
    if kind == "still-video" and not PLANETS.search(t):
        return None, None
    if PLANETS.search(t):
        tier = "planet"
    elif DEEP.search(t):
        tier = "deep"
    elif GAL.search(t) and not re.search(r"nebula|supernova|protostar|jet\b", low):
        tier = "galaxy"
    elif NEB.search(t):
        tier = "nebula"
    else:
        tier = "nebula" if kind != "still-video" else None
    return kind, tier


def page_info(row):
    try:
        h = get(BASE[row["site"]] + row["id"] + "/")
    except Exception:
        return None
    m = re.search(r'<div class="credit">(.*?)</div>', h, re.S)
    credit = ""
    if m:
        c = re.sub(r"<br\s*/?>", "\n", m.group(1))
        c = html.unescape(re.sub(r"<[^>]+>", "", c))
        credit = " ".join(l.strip() for l in c.split("\n") if l.strip() and not l.strip().lower().startswith("music"))
    d = re.search(r"Duration:</th><td[^>]*>([^<]+)", h)
    dur = 0
    if d:
        for n, u in re.findall(r"(\d+)\s*(m|s)\b", d.group(1)):
            dur += int(n) * (60 if u == "m" else 1)
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)))
    text = re.sub(r"\s+", " ", text)
    dist = None
    for sent in re.split(r"(?<=[.!?]) ", text):
        mm = re.search(r"(?:about|around|approximately|roughly|some|nearly)?\s*([\d,.]+(?:\s*(?:million|billion))?)\s+light-years? (?:away|from (?:Earth|us))", sent, re.I)
        if mm and len(sent) < 400:
            dist = dict(text=mm.group(1).strip(), sentence=sent.strip())
            break
    return dict(credit=credit, dur=dur, dist=dist)


if __name__ == "__main__":
    rows = json.load(open(ROOT / "out/audit/esa_titles.json"))
    cands = []
    for r in rows:
        title = re.sub(r"&#39;|&amp;", "'", r["title"]).replace("�", "'")
        kind, tier = classify(title)
        if tier:
            cands.append(dict(id=r["id"], site=r["site"], title=title, mb=r["mb"], kind=kind, tier=tier))
    print(len(cands), "candidates after classification", flush=True)
    with ThreadPoolExecutor(16) as ex:
        infos = list(ex.map(page_info, cands))
    cat = []
    for c, i in zip(cands, infos):
        if not i or not i["credit"] or i["dur"] < 8:
            continue
        c.update(i, url=CDN[c["site"]] % c["id"], page=BASE[c["site"]] + c["id"] + "/", licence="CC BY 4.0")
        cat.append(c)
    (ROOT / "library/catalog_esa.json").write_text(json.dumps(cat, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(cat), "usable;", dict(Counter((c["tier"], c["kind"]) for c in cat)))
    print("with sourced distance:", sum(1 for c in cat if c["dist"]), "| GB:", round(sum(c["mb"] for c in cat) / 1000, 1))
