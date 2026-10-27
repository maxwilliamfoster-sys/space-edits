# vendored from growth-lab/hook_lint.py @ a0fbaa7 - do not edit here; change the master and run sync_vendored.py
"""hook_lint: deterministic hook, tag and true-crime wording checks (growth-lab S5, master copy).

Edit it HERE, then roll it out with sync_vendored.py. Stdlib only, zero tokens.

    import hook_lint
    r = hook_lint.lint(hook=cold_open, caption=tiktok_caption, tags=tags, true_crime=True)
    if not r.ok:
        print("\\n".join(r.messages()))      # rewrite or regenerate before rendering
    for w in r.warns: log(w.message)

Checks (level "fail" unless marked warn):
  hook_empty       no hook text at all
  hook_too_long    first line over `max_words` words (default 16; BCF cold open uses 10)
  hook_greeting    opens with a greeting ("hey guys", "welcome back"...) or says
                   "in this video" / "in today's video" anywhere in the first line
  hook_no_specific first line has no concrete specific: a number (digits or a
                   number word), a name or a place (a capitalised word that is not
                   a common sentence opener), or one of the caller's `specifics`
  hook_ellipsis    first line ends in "..." or the ellipsis character
  doubled_word     "case case" style template bug ("had had", "that that" allowed)
  tags_count       fewer than 2 or more than 4 distinct tags
  tags_spam        #fyp / #foryou / #foryoupage / #fy and variants
  tags_generic     (warn) #viral, #trending, #tiktok, #shorts... say nothing specific
  tags_duplicate   (warn) the same tag twice
  legal_guilt_unqualified
                   (warn, true_crime=True only) a line naming someone states guilt
                   ("X murdered...", "X is the killer", "X did it", "...by X")
                   without accused / alleged / convicted / court-outcome wording.
                   A heuristic safety net, not legal review: the rule stays "never
                   imply guilt of anyone acquitted, uncharged or not convicted".

Tag checks run when `tags` is given, or when the caption contains at least one
hashtag (a caption with no hashtags at all is not failed).

CLI (exit 1 when any check fails, warnings never fail):
    py hook_lint.py --hook "In 1983 a farmer in Norfolk found a box." --tags "#coldcase,#norfolk"
    py hook_lint.py --caption-file caption.txt --script-file script.txt --true-crime --json

Lint ideas (opening length, no greeting or "in this video", concrete specifics)
come from Chamanrajragu/purffle-shorts, purffle_shorts/script.py::lint_script and
RUBRIC, under the MIT licence:

    MIT License

    Copyright (c) 2025 Purffle

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""
import argparse
import json
import re
import sys
from collections import namedtuple

Issue = namedtuple("Issue", "code level message")

MAX_WORDS = 16
MIN_TAGS, MAX_TAGS = 2, 4

# purffle _GREETING, plus a few more openers seen in the bots' own drafts.
_GREETING = re.compile(
    r"^\W*(hi|hey|hello|hiya|welcome|what'?s up|good (morning|evening|afternoon)|"
    r"today (we|i|i'm|we're|we'll|i'll)|let'?s (talk|dive|get into)|in this video|"
    r"in today'?s video)\b", re.I)
_THIS_VIDEO = re.compile(r"\bin (this|today'?s) (video|short|episode|clip)\b", re.I)
# purffle _NUMBER
_NUMBER = re.compile(
    r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"(thir|four|fif|six|seven|eigh|nine)teen|(twen|thir|for|fif|six|seven|eigh|nine)ty|"
    r"hundred|thousand|million|billion|trillion|half|twice|dozen|percent)\b", re.I)
_ELLIPSIS = re.compile(r"(\.\.\.|…)$")
_CLOSERS = "\"')]}’” \t"
# Same line only (a caption's line break is not a doubled word); a hyphen after the repeat is
# a compound name ("Boutros Boutros-Ghali"). Kept in step with upload_gate.DOUBLED_RE.
_DOUBLED = re.compile(r"\b(\w+)[ \t]+\1\b(?!-)", re.I)
DOUBLE_OK = {"had", "that"}
# Real doubled names, allowed only when both words are capitalised ("Sirhan Sirhan").
DOUBLED_NAMES = {"sirhan", "sing", "bora", "walla", "pago", "duran", "mahi", "baden", "tora"}
_WORD = re.compile(r"[\w'’-]+", re.U)

# Capitalised words that open ordinary sentences: not names or places.
COMMON = set("""
a an the this that these those he she it they we you i his her its their our my your
what why how when where who whom whose which there here then now so but and or if in on
at after before during while until since as of for from with without by to into onto
nobody no not never none every everyone everybody someone somebody something nothing
everything all some most many few each both either neither any anyone anything one
just only even still yet also again imagine watch look listen guess meet can could would
should will did do does is was are were be been has have had may might must let lets
police officer officers detective detectives man woman men women boy girl boys girls
people family mother father mum mom dad son daughter wife husband friend neighbour
neighbor victim body bodies killer murderer suspect court judge jury today yesterday
tonight tomorrow last next first second third final part chapter episode story case
she's he's it's i'm i'll i've i'd you're we're they're don't didn't can't won't isn't
wasn't aren't weren't hasn't haven't wouldn't couldn't shouldn't that's there's what's
who's here's let's oh wow yes ok okay hey hi hello welcome good bad new old big small
""".split())
_PLACE_PREP = {"in", "at", "from", "near", "to", "outside", "inside", "across", "around"}

SPAM_PREFIXES = ("fyp", "foryou")
SPAM_EXACT = {"fy", "xyzbca", "fypage"}
GENERIC_TAGS = {"viral", "viralvideo", "trending", "trend", "explore", "explorepage", "tiktok",
                "shorts", "short", "youtubeshorts", "reels", "video", "follow", "like",
                "subscribe", "funny", "fun", "love", "instagood"}

# -- legal guard --
_GUILT_VERB = (r"(murdered|killed|stabbed|strangled|shot|poisoned|raped|abducted|kidnapped|"
               r"assaulted|attacked|robbed|smothered|bludgeoned|dismembered|slaughtered|"
               r"butchered|tortured|drowned|molested)")
_PASSIVE_AUX = {"was", "were", "been", "being", "is", "are", "be", "got", "get", "gets", "getting"}
_UNKNOWN_SUBJECT = {"who", "whoever", "someone", "somebody", "nobody", "no-one", "noone",
                    "something", "what", "which", "unknown", "stranger", "intruder", "gunman",
                    "attacker", "killer", "man", "men", "woman", "gang"}
_VERB_RE = re.compile(r"\b" + _GUILT_VERB + r"\b", re.I)
_BY_AGENT = re.compile(r"^\s+by\s+([A-Z][\w'’-]+)")
_GUILT_PHRASE = re.compile(
    r"\b((is|was|'s|’s|as|became)\s+(the|a|an)\s+(real\s+)?"
    r"(killer|murderer|rapist|kidnapper|abductor|culprit|perpetrator|attacker|serial killer)|"
    r",\s*the\s+(killer|murderer|rapist|culprit|perpetrator)|"
    r"did it|is guilty|was guilty|"
    r"got away with (it|murder|the murder|killing)|"
    r"responsible for (the|her|his|their|its) (death|deaths|murder|murders|killing|killings)|"
    r"committed the (murder|murders|crime|crimes|killing|killings|attack)|"
    r"behind the (murder|murders|killing|killings|attack|disappearance))\b", re.I)
_QUALIFIER = re.compile(
    r"\b(accus\w*|alleg\w*|convict\w*|charged|charges?|found guilty|pleaded|pled|admitted|"
    r"sentenced|jury|juror\w*|court|trial|suspect\w*|acquit\w*|cleared|exonerat\w*|"
    r"den(y|ies|ied|ying)|not guilty|prosecut\w*|verdict|jailed|imprisoned|confess\w*|"
    r"inquest|unlawfully)\b", re.I)
_TWO_CAPS = re.compile(r"\b([A-Z][\w'’-]+)(\s+[A-Z][\w'’-]+)+")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


# ------------------------------------------------------------------- helpers --

def first_line(text):
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def word_count(text):
    return len(_WORD.findall(text or ""))


def _tokens(line):
    """[(word, at_sentence_start, previous_word_lower)]"""
    out = []
    start, prev = True, ""
    for m in re.finditer(r"\S+", line):
        raw = m.group(0)
        word = raw.strip("\"'()[]{}‘’“”.,;:!?*")
        if word:
            out.append((word, start, prev))
            prev = word.lower()
        start = bool(re.search(r"[.!?:]['\")\]’”]*$", raw))
    return out


def _is_cap(word):
    if word in ("I", "I'm", "I'll", "I've", "I'd"):
        return False
    if len(word) >= 2 and word.isupper() and any(c.isalpha() for c in word):
        return True  # FBI, UK, NYPD
    return word[:1].isupper() and len(word) > 1


def has_specific(line, specifics=()):
    if _NUMBER.search(line):
        return True
    low = line.lower()
    for s in specifics or ():
        if s and re.search(r"(?<!\w)" + re.escape(s.lower()) + r"(?!\w)", low):
            return True
    for word, at_start, _prev in _tokens(line):
        if not _is_cap(word):
            continue
        if word.lower() in COMMON and not word.isupper():
            continue
        return True
    return False


# ---------------------------------------------------------------------- hook --

def lint_hook(text, max_words=MAX_WORDS, specifics=()):
    """Checks on the first non-empty line of `text`. Returns [Issue]."""
    line = first_line(text)
    if not line:
        return [Issue("hook_empty", "fail", "No hook text: the first line is empty.")]
    out = []
    n = word_count(line)
    if n > max_words:
        out.append(Issue("hook_too_long", "fail",
                         f"First line is {n} words; keep the hook to {max_words} or fewer."))
    if _GREETING.search(line) or _THIS_VIDEO.search(line):
        out.append(Issue("hook_greeting", "fail",
                         "Hook opens with a greeting or 'in this video' instead of the most "
                         "interesting line."))
    if not has_specific(line, specifics):
        out.append(Issue("hook_no_specific", "fail",
                         "Hook has no concrete specific (a number, a name or a place)."))
    if _ELLIPSIS.search(line.rstrip(_CLOSERS)):
        out.append(Issue("hook_ellipsis", "fail",
                         "Hook ends in '...': write a complete line, never a truncated one."))
    out += _doubled(line)
    return out


def _doubled(text):
    out = []
    for m in _DOUBLED.finditer(text or ""):
        w = m.group(1).lower()
        if w in DOUBLE_OK or w.isdigit():
            continue
        second = m.group(0).split()[-1]
        if w in DOUBLED_NAMES and m.group(1)[:1].isupper() and second[:1].isupper():
            continue
        out.append(Issue("doubled_word", "fail", f"Doubled word: '{m.group(0)}'."))
    return out


# ---------------------------------------------------------------------- tags --

def extract_tags(text):
    return [t.rstrip(".,;:!?") for t in re.findall(r"#[^\s#]+", text or "")]


def _norm_tag(t):
    t = str(t).strip()
    return t if t.startswith("#") else "#" + t


def lint_tags(tags, min_tags=MIN_TAGS, max_tags=MAX_TAGS):
    """`tags`: a list of tags (with or without '#') or a caption string."""
    if isinstance(tags, str):
        tags = extract_tags(tags)
    tags = [_norm_tag(t) for t in (tags or []) if str(t).strip().lstrip("#")]
    out = []
    seen, uniq = set(), []
    for t in tags:
        key = t.lower()
        if key in seen:
            out.append(Issue("tags_duplicate", "warn", f"Tag {t} appears twice."))
            continue
        seen.add(key)
        uniq.append(t)
    for t in uniq:
        bare = t.lower().lstrip("#")
        if bare.startswith(SPAM_PREFIXES) or bare in SPAM_EXACT:
            out.append(Issue("tags_spam", "fail",
                             f"{t}: no #fyp/#foryou tags (they add nothing and read as spam)."))
        elif bare in GENERIC_TAGS:
            out.append(Issue("tags_generic", "warn", f"{t} is generic; use a specific tag."))
    if not (min_tags <= len(uniq) <= max_tags):
        out.append(Issue("tags_count", "fail",
                         f"{len(uniq)} tags; use {min_tags}-{max_tags} specific tags."))
    return out


# ---------------------------------------------------------------- legal guard --

def _lines(text):
    for ln in (text or "").splitlines():
        for s in _SENT_SPLIT.split(ln.strip()):
            if s.strip():
                yield s.strip()


def has_name(line, names=()):
    low = line.lower()
    for n in names or ():
        if n and re.search(r"(?<!\w)" + re.escape(n.lower()) + r"(?!\w)", low):
            return True
    for m in _TWO_CAPS.finditer(line):
        words = m.group(0).split()
        if words[0].lower() in COMMON:
            words = words[1:]
        if words and all(w.lower() not in COMMON for w in words) and len(words) >= 1:
            if len(words) >= 2 or not _at_start(line, m.start()):
                return True
    for word, at_start, prev in _tokens(line):
        if (not at_start and _is_cap(word) and word.lower() not in COMMON
                and prev not in _PLACE_PREP):
            return True
    return False


def _at_start(line, idx):
    return not line[:idx].strip() or bool(re.search(r"[.!?:]\W*$", line[:idx]))


def _states_guilt(line):
    if _GUILT_PHRASE.search(line):
        return True
    for m in _VERB_RE.finditer(line):
        before = re.findall(r"[\w']+", line[:m.start()].lower())
        # skip one adverb: "was brutally murdered"
        aux = before[-1] if before else ""
        if aux.endswith("ly") and len(before) >= 2:
            aux = before[-2]
        if aux in _PASSIVE_AUX:
            if _BY_AGENT.match(line[m.end():]):   # "...was murdered by Peter Brown"
                return True
            continue                               # victim in passive voice
        if aux in ("of", "for", "after"):          # "accused of", noun-ish uses
            continue
        if aux in _UNKNOWN_SUBJECT:                # "Who killed Jill Dando?": no one named
            continue
        return True
    return False


def legal_guard(text, names=()):
    """Warn (never fail) for each line that names someone and states guilt
    without accused / alleged / convicted / court-outcome wording."""
    out = []
    for line in _lines(text):
        if not _states_guilt(line) or _QUALIFIER.search(line):
            continue
        if not has_name(line, names):
            continue
        out.append(Issue("legal_guilt_unqualified", "warn",
                         f"'{line[:90]}' states a named person's guilt without accused/alleged/"
                         "convicted wording; use the court outcome or 'accused'/'alleged'."))
    return out


# ------------------------------------------------------------------ combined --

class LintResult:
    def __init__(self, issues):
        self.issues = list(issues)

    @property
    def fails(self):
        return [i for i in self.issues if i.level == "fail"]

    @property
    def warns(self):
        return [i for i in self.issues if i.level == "warn"]

    @property
    def ok(self):
        return not self.fails

    def messages(self):
        return [f"{i.level.upper()} {i.code}: {i.message}" for i in self.issues]

    def to_dict(self):
        return {"ok": self.ok, "issues": [i._asdict() for i in self.issues]}


def lint(hook=None, caption=None, tags=None, script=None, true_crime=False,
         max_words=MAX_WORDS, specifics=(), names=()):
    """Run every check. `hook` defaults to the caption's first line; `tags`
    default to the caption's hashtags (checked only if it has any)."""
    issues = []
    hook_text = hook if hook is not None else first_line(caption or "")
    if hook is not None or caption is not None:
        issues += lint_hook(hook_text, max_words=max_words, specifics=specifics)
    if caption and hook is not None:
        issues += [i for i in _doubled(caption) if i not in issues]
    if tags is not None:
        issues += lint_tags(tags)
    elif caption and extract_tags(caption):
        issues += lint_tags(caption)
    if true_crime:
        seen, lines = set(), []
        for block in (hook if hook is not None else None, caption, script):
            for ln in _lines(block or ""):
                if ln not in seen:
                    seen.add(ln)
                    lines.append(ln)
        issues += legal_guard("\n".join(lines), names=names)
    return LintResult(issues)


# ----------------------------------------------------------------------- CLI --

def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hook / tag / true-crime wording lint "
                                             "(exit 1 when a check fails)")
    ap.add_argument("--hook")
    ap.add_argument("--caption")
    ap.add_argument("--caption-file")
    ap.add_argument("--script")
    ap.add_argument("--script-file")
    ap.add_argument("--tags", help="comma- or space-separated")
    ap.add_argument("--true-crime", action="store_true")
    ap.add_argument("--max-words", type=int, default=MAX_WORDS)
    ap.add_argument("--specifics", default="", help="comma-separated known names/places")
    ap.add_argument("--names", default="", help="comma-separated people named in the story")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    caption = _read(a.caption_file) if a.caption_file else a.caption
    script = _read(a.script_file) if a.script_file else a.script
    tags = [t for t in re.split(r"[,\s]+", a.tags) if t] if a.tags is not None else None
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
    r = lint(hook=a.hook, caption=caption, tags=tags, script=script, true_crime=a.true_crime,
             max_words=a.max_words, specifics=split(a.specifics), names=split(a.names))
    if a.json:
        print(json.dumps(r.to_dict(), ensure_ascii=False))
    else:
        for m in r.messages() or ["OK: no issues"]:
            print(f"[hook_lint] {m}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
