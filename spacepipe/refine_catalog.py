"""Refine library/catalog_esa.json -> library/catalog.json: planets allow-list, safe distance facts (must belong to the clip's subject)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERIC = {"pan", "zoom", "video", "into", "the", "of", "in", "on", "across", "image", "views", "view", "hubble", "webb", "and", "miri", "nircam",
           "transition", "galaxy", "nebula", "cluster", "with", "for", "from", "pullout", "zooming", "new", "infrared", "optical"}
PRON = re.compile(r"^(it|this|the (?:nebula|galaxy|cluster|object|star|spiral|lens)|[A-Z][\w .+\-−]{2,30} (?:lies|is|resides|sits|is located))\b", re.I)
PLANET_OK = re.compile(r"saturn|uranus|neptune|jupiter", re.I)
PLANET_BAD = re.compile(r"hot jupiter|clash|exoplanet|transit|pluto|moving|clear to cloudy|winds", re.I)


def tokens(title):
    toks = re.findall(r"[A-Za-z]*\d[\w.+\-−]*|[A-Za-z]{4,}", title)
    return {t.lower() for t in toks if t.lower() not in GENERIC}


def safe_dist(c):
    d = c.get("dist")
    if not d:
        return None
    s = d["sentence"]
    if len(re.findall(r"light-years?", s, re.I)) != 1:
        return None
    if not (tokens(c["title"]) & {t.lower().strip(".,;()") for t in re.findall(r"[\w.+\-−]+", s)}) and not PRON.match(s):
        return None
    return d["text"]


def main():
    out = []
    for c in json.load(open(ROOT / "library/catalog_esa.json")):
        if c["tier"] == "planet" and (not PLANET_OK.search(c["title"]) or PLANET_BAD.search(c["title"])):
            continue
        c["dist_ly"] = safe_dist(c)
        out.append(c)
    (ROOT / "library/catalog.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(out), dict(Counter(c["tier"] for c in out)), "| safe distances:", sum(1 for c in out if c["dist_ly"]))
    print([c["title"][:30] + "=" + c["dist_ly"] for c in out if c["dist_ly"]][:12])


if __name__ == "__main__":
    main()
