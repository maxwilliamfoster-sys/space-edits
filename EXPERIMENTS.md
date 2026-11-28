# Space Edits experiments (pre-registered)

One main test at a time. Write the test here before it starts; judge it only by its own rule.

## Running: space-clean (clean screen, since 2026-11-19)
- Variable: CLEAN_SHARE=0.5 in spacepipe/compose.py. Clean edits drop the top hook and the small lines.
- Control: full-text edits. Primary metric: views at 72 h (support: average watch seconds).
- Min 10 videos per arm past 72 h, max 30. Arm = gate_log/analytics `clean`.
- 2026-11-28 check: 8 clean and 7 full-text videos past 72 h. Not judged yet.

## Queued next (stage 3 new format, autopilot review 2026-11-28): space-top7, a 61-second ranked list
- Why: views have sat at 555-890 (median ~700) for 4 weeks with 4 tests in a row and no winner (stage 3).
  Viewers watch about 3 s of 10-30 s edits. Competitor evidence (2026-11-28):
  - @astronomy_hq: its best recent post (394K views, 17 Oct 2026, account median ~14.5K) is a 61 s numbered
    list. A title stating the promise stays on screen the whole video, and each item has a numbered label
    with a known name ("1. The Big Bang", "5. Whirlpool Galaxy M51"), ~8 s per item.
  - @astroospace (7.2M followers): its biggest space edits are exactly 1:00 long (95.2M, 9.3M, 4.4M views).
  - TikTok's Creator Rewards only pays on videos of 1 minute or more, and every edit we make is shorter.
- Variable: format. About a third of new posts are "top7" (replacing the same share of other formats):
  - 61-63 s, 7 named objects from the existing library (TARGET_NAMES first, then single catalogue names,
    said in plain English: "a galaxy 30 million light-years away", never a bare "NGC 1309").
  - A promise title held on screen from 0 s to the end, e.g. "7 REAL PLACES THAT LOOK FAKE",
    "7 THINGS FARTHER AWAY THAN YOU THINK", ranked so #1 is the biggest or farthest.
  - A numbered label per item (#7 down to #1) and the narrator says one line per item.
  - Open on the subject already moving (never on near-black sky); no closing card before the credits.
  - Keeps everything proven or locked: narrator on (Max's call), credits, wrapped lines, 3 posts a day.
- Control: all other formats in the same weeks. Primary metric: views at 72 h. Support: average watch
  seconds, shares, follows if Buffer gives them.
- Min 10 top7 videos past 72 h, max 30 (about 2-3 weeks at a third of 3 a day).
- Win rule: top7 median views at least 20% above the other formats' median over the same dates.
  Otherwise it goes under "Didn't work here".
- Starts only after space-clean is judged (never during it: the clean arms depend on the text layout).
- Not copied: no rival footage, sounds, titles or lists. Only the structure (promise title, numbered items,
  ~60 s) is borrowed. Footage stays NASA/ESA with credits.

## Queued after that
- Mission edits (rockets, astronauts, Moon landings) from NASA public-domain footage on a third of posts
  (@nsn.astro, @astro_asteria). Needs a footage source checked as free to use first.
