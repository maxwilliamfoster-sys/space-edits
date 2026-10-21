# space-edits

Automatic "scale of the universe" TikTok edits for **@mcfiddler.bob**, built from real NASA and ESA/Webb/Hubble footage
(CC BY credits are burned into every edit and the caption). Runs on GitHub Actions, so it works with the PC off.

- `spacepipe_make.py` renders edits (`new -n N`, `status`, `prefetch`); `spacepipe/` is the pipeline (compose, render, QC, facts).
- `ci_run.py` is the cloud entry point: render, preview on Telegram and, when repo variable `AUTO_POST=true`, host on the `media`
  branch (GitHub Pages) and schedule on Buffer (`publish.py`). 5 edits a day in slots 08:00, 11:00, 14:00, 17:00 and 20:00 UTC, at most 10 posts waiting.
- NASA reels are streamed from nasa.gov (ffmpeg seeks into them over HTTP); ESA clips are downloaded per edit.
- Secrets: `BUFFER_API_KEY` (expires 2027-10-20), `BUFFER_CHANNEL_ID` (mcfiddler.bob), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Nothing is posted until `AUTO_POST` is `true`. Fonts: Anton (OFL) and Liberation Sans in CI.
- Music: Kevin MacLeod (incompetech.com), CC BY 4.0, credited per edit (unattended Buffer path only).
- TikTok sounds: for real uploads, render silent (`SOUND_MODE=tiktok`) and publish through the Higgsfield TikTok tools (`DIRECT_POST`), picking a
  trending `EPIC` sound from `tiktok_music_trending` and trimming it in the publish form. `py sound_sync.py <track> <video_s> <climax_s>`
  suggests the trim start whose energy build lands on the final reveal.
- All on-screen text is centred; the credit card is one `drawtext` per line (ffmpeg 6.1 in CI has no `text_align`).
