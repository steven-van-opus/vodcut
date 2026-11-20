# Changelog

## 1.0.0 (2026-11-19)

First release.

- `/vodcut` skill: scan a whole VOD locally, add known moments by phrase, verify on frame sheets, cut and frame
  1080x1920, open each clip as an AI Producer project (official plugin `aip@ai-producer-plugins`, declared as a
  dependency), report hours scanned, moments found and wall time.
- Works as a plugin (`/vodcut:vodcut`) or as a plain skill folder (`~/.claude/skills/vodcut` -> `/vodcut`): the scripts
  live inside the skill folder.
- Scripts: `vodcut.py`, `ytfrag.py`, `scan_vod.py`, `scan_frags.py`, `rank_windows.py` (whisper-loop cleaning,
  review flags), `framesheet.py`, `frame.py`, `bleep.py`, `aip_prep.py`.
- Tests: unittest suite, including an offline end-to-end run on a synthetic stream.
