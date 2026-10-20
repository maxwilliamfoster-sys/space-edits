# space-edits progress (2026-10-20)

Goal: cloud automation (GitHub Actions + Buffer) for @mcfiddler.bob space "scale" edits, running with the PC off.

## Done
- Pipeline code reviewed and fixed in `Projects/Tools/capcut-automation` (first-shot length, chart clips, closing text, QC dark-footage false fail, crash-safe batch). 6/6 test edits passed QC.
- Two edits (33, 36) sent to Telegram for Max to judge.
- Buffer: `mcfiddler.bob` connected (channel id 6ad7d7a3778b240e2f6fc860, active). API key regenerated on 2026-10-20, expires 2027-10-20, and set as `BUFFER_API_KEY` in escape-cup and youtube-automation (both use BUFFER_CHANNEL_ID, so the new channel does not confuse them).
- This repo created locally (`Projects/Content-Bots/space-edits`): pipeline ported for CI (portable ffmpeg path, Anton/Liberation fonts, NASA reels streamed over HTTP so no 4 GB download), `publish.py` (Pages media branch + Buffer GraphQL), `.github/workflows/post.yml`, music + library data. `git init` done, NOT committed or pushed, no GitHub repo yet.

## Blocked / next
- `ci_run.py` (the entry point: render -> Telegram preview -> only if repo variable AUTO_POST=true host + schedule on Buffer) was not written: the auto-mode classifier denied the Write. Needs Max's go-ahead (or a render-only first version).
- Then: create GitHub repo `space-edits` (public: Pages needs it on the free plan), push, enable Pages on branch `media`, set secrets BUFFER_API_KEY / BUFFER_CHANNEL_ID / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, run workflow once with AUTO_POST off (renders + Telegram preview), check the CI render matches local. Nothing posts until Max approves a video and AUTO_POST is set to true.
- Test CI-specific risks: Anton vs Impact text widths, apt ffmpeg 6.1 xfade/drawtext, NASA HTTP seeking speed.
