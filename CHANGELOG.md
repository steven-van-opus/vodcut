# Changelog

## 1.1.0 (2026-11-20)

- YouTube VODs are routed by how YouTube serves them. `vodcut.py` asks yt-dlp for the delivery protocol before every
  download (scan, verify, cut) and stores it in `vodcut.json`: post-live VODs still served as 2-second fragments go
  through `ytfrag.py`, processed VODs through yt-dlp (`139/ba/w` for the scan audio, `299+140` / `134+139` windows
  with `--download-sections`). A day after a stream YouTube ignores `&sq=N` and answers every "fragment" with the
  whole file, which sent the old fragment path into terabytes of transfer.
- `ytfrag.py` checks the protocol first and stops on a processed VOD; a reply over 16 MB (no 2-second fragment is)
  stops the download after one round. `track` writes a `.part` file, checks ffmpeg's exit code and keeps the
  fragments until the track is complete; `window` also publishes its output only when it is complete.
- `vodcut.py cut`: the downloaded window is named by its span (`mNN_src_IN-OUT.mp4`), so a re-cut with a new
  `--in-out` never composes an old window or records times the clip does not have; reversed spans are refused;
  `--top-ratio`, `--focus-x` and `--divider` are passed to `frame.py compose`.
- `vodcut.py report`: `WINDOWS_RANKED`, `MOMENTS_RANKED` and `MOMENTS_ADDED_BY_FIND` replace `MOMENTS_FOUND`, and
  `WALL_TIME` comes only from a folder's first scan that fetched the audio itself and ran start to finish (each
  scan run is recorded in `vodcut.json`); a resumed or seeded folder says why it has none.
- Twitch and Kick scan audio falls back to the smallest rendition (`ba/w`), never full-quality video.
- `aip_prep.py check`: a landscape file is an error (AI Producer keeps it 16:9) unless `--allow-landscape`.
- `scan_frags.py` drops a chunk whose transcription finishes after the track was assembled, and skips chunks
  already marked done, so no 20-minute span is transcribed twice.
- AI Producer handoff: `finish_project` when no caption or edit pass follows (and before an export), so the project
  link opens an unlocked editor.
- SKILL.md step 0 carries the install lines itself (the plain-skill install has no README).
- README: the launch numbers are filled from one fresh, uninterrupted run (`docs/launch-run.json`); one line on
  installing this or gtavi-aiproducer, not both (both ship a `vodcut` skill).
- Tests: offline routing, re-cut, layout-flag, report, ytfrag-guard, track-assembly and scan_frags tests with fake
  `yt-dlp` / `whisper-cli` on PATH and a local HTTP server.

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
