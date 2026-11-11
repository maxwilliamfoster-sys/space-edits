"""On-screen text. Only claims that are standard, checkable facts, or numbers parsed from the clip's own ESA page (dist_ly)."""
import re

EARTH = [("THIS IS EARTH", "SEEN FROM 400 KM UP"), ("HOME", "12,742 KM ACROSS"), ("EVERYONE YOU KNOW", "LIVES ON THIS"),
         ("EVERY WAR. EVERY SONG.", "ALL OF IT HAPPENED HERE"), ("THIS IS WHERE YOU LIVE", "SEEN FROM 400 KM UP"),
         ("8 BILLION PEOPLE", "ONE PLANET"), ("EARTH", "THE ONLY PLANET KNOWN TO HOST LIFE"),
         ("THE SPACE STATION", "CIRCLES EARTH ABOUT EVERY 90 MINUTES")]
SUN = [("THE SUN", "1.3 MILLION EARTHS FIT INSIDE"), ("OUR STAR", "99.8% OF THE SOLAR SYSTEM'S MASS"),
       ("THE SUN", "ONE STAR OF HUNDREDS OF BILLIONS"), ("THE SUN", "ABOUT 150 MILLION KM AWAY"),
       ("SUNLIGHT", "TAKES ABOUT 8 MINUTES TO REACH US")]
PLANET = {"saturn": ("SATURN", "ABOUT 9 EARTHS WIDE"), "uranus": ("URANUS", "ABOUT 4 EARTHS WIDE"),
          "neptune": ("NEPTUNE", "ABOUT 4 EARTHS WIDE"), "jupiter": ("JUPITER", "OVER 1,300 EARTHS FIT INSIDE")}
MW = [("OUR GALAXY", "100+ BILLION STARS"), ("THE MILKY WAY", "ABOUT 100,000 LIGHT-YEARS ACROSS"), ("OUR GALAXY", "THE SUN IS 1 STAR OF 100+ BILLION"),
      ("THE MILKY WAY", "WE ARE ABOUT 26,000 LIGHT-YEARS FROM ITS CENTRE")]
CLOSERS = ["AND WE ARE ONE TINY ROCK", "YOU ARE SMALLER THAN YOU THINK", "ALL OF HISTORY HAPPENED ON ONE SPECK",
           "EVERYTHING YOU LOVE IS ON ONE SPECK", "AND YOU THOUGHT YOUR PROBLEMS WERE BIG", "NOW LOOK UP TONIGHT",
           "ONE PLANET. ONE CHANCE."]
# 2-4 niche tags, never #fyp/#foryou (hook_lint fails them; research-algorithm-policy: hashtags barely matter)
HASHTAGS = [["#space", "#universe", "#nasa", "#webb"], ["#astronomy", "#cosmos", "#jameswebb", "#perspective"],
            ["#space", "#galaxy", "#hubble", "#deepspace"], ["#universe", "#scale", "#earth", "#nasa"]]
# on-screen hook: 7 words or fewer with a concrete specific (a name or place: Earth, the universe...), checked by gate.text_lint
HOOKS = ["You vs the universe. Watch this.", "How small is Earth? Watch this.", "Zoom out from Earth. Keep going.",
         "This is how big the universe is.", "Earth is not the main character.", "From Earth to the edge. Watch.",
         "Not CGI. This is the real universe.", "Earth to the edge. All real.", "Real footage. Earth to deep space.",
         # 2026-11-11: the pools were so small that one hook had opened 10 of 79 posts. The same words on post after
         # post is what TikTok stops recommending, so every pool is now big enough for gate.NO_REPEAT_HOOK (20 posts).
         "Earth, then everything else. Watch.", "How far from Earth can you go?", "Leaving Earth. Do not blink.",
         "Earth is smaller than you think.", "One planet. Then the whole universe.", "Start at Earth. End at the edge.",
         "Zoom out until Earth disappears.", "Everything you know is on Earth.", "Earth from orbit. Then keep going.",
         "The Milky Way is just the start.", "Past the Sun. Past the galaxy.", "Real images. Earth to distant galaxies.",
         "This zoom starts on Earth.", "The solar system is tiny. Watch.", "The universe, from your street outwards."]

# The other formats (Max, 2026-10-28: every post said the same things over the same pictures). Each has its own story,
# hooks, closers and tags; every number on screen comes from the clip's own ESA page (dist_ly).
FORMAT_HOOKS = {
    "timemachine": ["The universe is a time machine.", "You are seeing the universe's past.", "Light-years are also years. Watch.",
                    "Old light from across the universe.", "Every galaxy you see is old light.",
                    "Telescopes look into the universe's past.", "The farther the galaxy, the older.",
                    "This galaxy light is ancient. Watch.", "Far in the universe means long ago.",
                    "The universe shows us its past.", "Old light, real galaxies. Watch.",
                    "Nothing in the universe is seen live."],
    "guess": ["Guess the distance in light-years.", "Guess the light-years before they appear.", "How many light-years away? Guess.",
              "Guess how far. Light-years appear.", "Guess how many light-years. Then look.",
              "Light-years quiz: how far is this?", "How far is this galaxy? Guess.", "Four objects. Guess the light-years.",
              "Can you guess these light-years?", "Near or far? Guess the light-years.",
              "Guess first. The light-years come after.", "Guess the distance. Light-years, not miles."],
    "countdown": ["5 real telescope images, ranked by distance.", "The universe, ranked by distance. Watch.",
                  "5 views of the universe. Farthest last.", "Ranked: 5 views of the universe.",
                  "5 stops across the universe. Ranked.", "Farthest galaxy last. 5 real images.",
                  "Counting down 5 views of the universe.", "The universe in 5 images. Ranked.",
                  "5 light-year distances, smallest first.", "Near to far: 5 universe views.",
                  "5 images, each deeper into the universe.", "5 real images, nearest to farthest galaxy."],
    "drift": ["30 seconds of the real universe.", "Breathe. This is the real universe.", "Real telescope footage of the universe.",
              "Slow down. The universe is real.", "Quiet footage of the real universe.", "No talking. Just the universe.",
              "Real galaxies. Take a breath.", "Pause here. This is the universe.", "Calm, real images of the universe.",
              "Let the universe slow you down.", "Real telescope views. Just the universe.", "Watch the universe drift by."],
}
FORMAT_CLOSERS = {
    "timemachine": ["YOU ARE LOOKING BACK IN TIME", "THE NIGHT SKY IS OLD LIGHT", "EVERY STAR YOU SEE IS THE PAST"],
    "guess": ["HOW MANY DID YOU GET RIGHT?", "COMMENT YOUR SCORE OUT OF 4", "WHICH ONE SURPRISED YOU?"],
    "countdown": ["WHICH NUMBER WAS YOUR FAVOURITE?", "THE FARTHEST LIGHT CAME LAST", "COMMENT YOUR FAVOURITE NUMBER"],
    "drift": ["SAVE THIS FOR LATER", "SEND THIS TO SOMEONE WHO NEEDS IT", "ALL OF THIS IS REAL"],
}
FORMAT_HASHTAGS = {
    "timemachine": [["#space", "#universe", "#lightyears", "#history"], ["#astronomy", "#cosmos", "#jameswebb", "#time"]],
    "guess": [["#space", "#quiz", "#astronomy", "#guess"], ["#universe", "#quiz", "#hubble", "#galaxy"]],
    "countdown": [["#space", "#top5", "#universe", "#hubble"], ["#astronomy", "#ranking", "#galaxy", "#webb"]],
    "drift": [["#space", "#relaxing", "#universe", "#webb"], ["#calm", "#cosmos", "#nebula", "#hubble"]],
}
# The named-target cut (compose.plan_target). Kept apart from FORMAT_HOOKS: {name} is filled in from the clip.
TARGET_HOOKS = ["We flew to {name}.", "Next stop: {name}.", "This is {name}. It's real.", "Real footage of {name}.",
                "Destination: {name}.", "Have you seen {name}?", "A closer look at {name}.", "Zooming in on {name}."]
TARGET_CLOSERS = ["WHERE SHOULD WE GO NEXT?", "COMMENT THE NEXT STOP", "NAME OUR NEXT DESTINATION"]
TARGET_HASHTAGS = [["#space", "#universe", "#astronomy", "#jameswebb"], ["#cosmos", "#galaxy", "#hubble", "#nebula"]]
# (lower bound in years, line). Standard dates: Earth 4.54 bn years; the Mesozoic "age of dinosaurs" 252-66 million;
# Homo sapiens about 300,000; the last ice age ended about 11,700; the Great Pyramid about 4,500; the Roman Empire 27 BC.
ERAS = [(4.6e9, "BEFORE EARTH EXISTED"), (2.52e8, "BEFORE THE AGE OF DINOSAURS"), (6.6e7, "IN THE AGE OF THE DINOSAURS"),
        (3e5, "BEFORE HUMANS EXISTED"), (1.2e4, "BEFORE THE LAST ICE AGE ENDED"), (4.6e3, "BEFORE THE GREAT PYRAMID WAS BUILT"),
        (2.1e3, "BEFORE THE ROMAN EMPIRE BEGAN"), (200, "LONG BEFORE YOU WERE BORN")]


def era_index(years):
    """Index into ERAS of the first bound `years` reaches; None under 200 years (too close to say anything)."""
    return next((i for i, (lo, _) in enumerate(ERAS) if years >= lo), None)


def era_line(years):
    i = era_index(years)
    return ERAS[i][1] if i is not None else ""


def ly_text(d):
    """'2.8 billion' -> '2.8 BILLION'; '960' -> '960'; '7500' -> '7,500'.
    The catalogue's distances are scraped as written, so one video once showed '7500' next to '4,500'.
    Whole numbers of four or more digits always get thousands commas (years like 'in 1054' never reach here)."""
    d = re.sub(r"\s+", " ", d).upper()
    return re.sub(r"(?<![\d.,])\d{4,}(?![\d.,])", lambda m: f"{int(m.group()):,}", d)


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
