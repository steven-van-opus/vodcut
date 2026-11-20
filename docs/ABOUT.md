# GitHub "About" box

**Description** (350 characters max):

> /vodcut, an open-source Claude Code skill: watches an entire stream VOD on your machine (whisper.cpp + loudness + reaction cues), verifies the best moments on frame sheets, cuts them 1080x1920 and opens each one as an AI Producer project for captions and export.

**Website:** https://producer.opus.pro

**Topics:** `claude-code` `claude-code-skill` `agent-skills` `ai-producer` `opusclip` `vod` `livestream` `clipping` `whisper-cpp` `ffmpeg` `yt-dlp` `vertical-video` `gta6` `youtube-shorts` `tiktok`

## Placeholders to replace before publishing

| Placeholder | Where | Fill from |
|---|---|---|
| `[OWNER]` | README.md, .claude-plugin/plugin.json, .claude-plugin/marketplace.json, LICENSE | the GitHub owner |

## The launch numbers

The README's top line and "The numbers" table are filled from one fresh, uninterrupted run, saved as
`docs/launch-run.json` (its `vodcut.py report --json`, without the transcript lines). If you re-run for new numbers,
use a new, empty run folder, let it finish in one go, and replace all of them together from the new report:
`HOURS_SCANNED`, `WINDOWS_RANKED`, `MOMENTS_RANKED` and `WALL_TIME`. A resumed or seeded folder reports no
`WALL_TIME` (its `wall_time_note` says why), and the tests fail if the README and the file disagree.
