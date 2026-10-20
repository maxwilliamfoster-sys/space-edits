"""On-screen text. Only claims that are standard, checkable facts, or numbers parsed from the clip's own ESA page (dist_ly)."""
import re

EARTH = [("THIS IS EARTH", "SEEN FROM 400 KM UP"), ("HOME", "12,742 KM ACROSS"), ("EVERYONE YOU KNOW", "LIVES ON THIS"),
         ("EVERY WAR. EVERY SONG.", "ALL OF IT HAPPENED HERE"), ("THIS IS WHERE YOU LIVE", "SEEN FROM 400 KM UP")]
SUN = [("THE SUN", "1.3 MILLION EARTHS FIT INSIDE"), ("OUR STAR", "99.8% OF THE SOLAR SYSTEM'S MASS"),
       ("THE SUN", "ONE STAR OF HUNDREDS OF BILLIONS")]
PLANET = {"saturn": ("SATURN", "ABOUT 9 EARTHS WIDE"), "uranus": ("URANUS", "ABOUT 4 EARTHS WIDE"),
          "neptune": ("NEPTUNE", "ABOUT 4 EARTHS WIDE"), "jupiter": ("JUPITER", "OVER 1,300 EARTHS FIT INSIDE")}
MW = [("OUR GALAXY", "100+ BILLION STARS"), ("THE MILKY WAY", "ABOUT 100,000 LIGHT-YEARS ACROSS"), ("OUR GALAXY", "THE SUN IS 1 STAR OF 100+ BILLION")]
CLOSERS = ["AND WE ARE ONE TINY ROCK", "YOU ARE SMALLER THAN YOU THINK", "ALL OF HISTORY HAPPENED ON ONE SPECK",
           "EVERYTHING YOU LOVE IS ON ONE SPECK", "AND YOU THOUGHT YOUR PROBLEMS WERE BIG"]
HASHTAGS = [["#space", "#universe", "#nasa", "#webb", "#fyp"], ["#astronomy", "#cosmos", "#jameswebb", "#perspective", "#fyp"],
            ["#space", "#galaxy", "#hubble", "#deepspace", "#fyp"], ["#universe", "#scale", "#earth", "#nasa", "#fyp"]]
HOOKS = ["You are smaller than you think.", "How small are you? Watch this.", "Zoom out. Keep zooming out.",
         "This is how big the universe really is.", "Earth is not the main character."]


def ly_text(d):
    """'2.8 billion' -> '2.8 BILLION'; '960' -> '960'."""
    return re.sub(r"\s+", " ", d).upper()


def lines_for(slot, asset, rng):
    if slot == "earth":
        return rng.choice(EARTH)
    if slot == "sun":
        return rng.choice(SUN)
    if slot == "planet":
        for k, v in PLANET.items():
            if k in asset["title"].lower():
                return v
        return ("A WORLD", "FAR FROM HOME")
    if slot == "mw":
        return rng.choice(MW)
    d = asset.get("dist_ly")
    if d and d.replace(",", "").lstrip().startswith("0"):   # scraped fragment such as "000" from "200,000"
        d = None
    if slot == "galaxy_zoom" and asset["tier"] == "nebula":   # never call a nebula a galaxy
        return ("THIS ONE", f"{ly_text(d)} LIGHT-YEARS AWAY") if d else ("REAL IMAGE", "FROM A SPACE TELESCOPE")
    if slot in ("galaxy_zoom", "galaxy"):
        if d:
            return rng.choice([("THIS GALAXY", f"{ly_text(d)} LIGHT-YEARS AWAY"), ("THIS LIGHT", f"LEFT {ly_text(d)} YEARS AGO")])
        return ("ONE GALAXY", "OF HUNDREDS OF BILLIONS")
    if slot == "nebula":
        if d:
            return ("THIS ONE", f"{ly_text(d)} LIGHT-YEARS AWAY")
        return ("REAL IMAGE", "FROM A SPACE TELESCOPE")
    if slot == "deep":
        if re.search(r"ultra deep|deep field", asset["title"], re.I):
            return ("ONE TINY PATCH OF SKY", "THOUSANDS OF GALAXIES")
        if d:
            return ("A CLUSTER OF GALAXIES", f"{ly_text(d)} LIGHT-YEARS AWAY")
        return ("MOST OF THESE SMUDGES", "ARE WHOLE GALAXIES")
    return ("", "")
