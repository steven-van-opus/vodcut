---
name: vodcut
description: Watch an entire stream VOD on the user's machine (YouTube, Twitch, Kick or a local file), find the best moments from the audio (whisper.cpp transcript, loudness, reaction cues, ranked 30-second windows, frame-sheet verification), cut and frame each winner to 1080x1920, and open every one as an AI Producer project so captions and export happen in AI Producer. Use when the user says /vodcut, "scan this stream", "find the best moments in this VOD" or "clip this stream into AI Producer".
argument-hint: "<VOD URL, YouTube id or local file> [how many clips]"
---

# /vodcut

One command, one VOD in, a set of editable AI Producer projects out. Everything up to the upload runs locally
(ffmpeg, yt-dlp, whisper.cpp): no API calls, no cost beyond the user's own machine. AI Producer then hosts each
clip: transcript, captions, timeline, export and an editable project link.

Scripts: `${CLAUDE_SKILL_DIR}/scripts/` (run with `python3 -I`). Work in `vodcut/<slug>/` in the user's current
folder (`<slug>` = streamer + date). Details: [rubric](references/rubric.md), [AI Producer handoff](references/aip-handoff.md).

## 0. Check the tools (once)

`ffmpeg`, `ffprobe`, `yt-dlp`, `whisper-cli` and a whisper.cpp model (`--model`, `$WHISPER_MODEL`,
`${CLAUDE_SKILL_DIR}/models/` or `~/.cache/whisper/ggml-large-v3-turbo-q5_0.bin`). If one is missing, show the
user these lines and stop; run them only with the user's go-ahead (never download a model or binary on your own):

```bash
brew install ffmpeg yt-dlp whisper-cpp
mkdir -p ~/.cache/whisper && curl -L -o ~/.cache/whisper/ggml-large-v3-turbo-q5_0.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
```

The AI Producer steps need the official plugin `aip@ai-producer-plugins`, signed in through `/mcp`.

## 1. Scan the whole VOD

```bash
python3 -I "${CLAUDE_SKILL_DIR}/scripts/vodcut.py" scan "<VOD>" vodcut/<slug> --jobs 16
```

A day-long stream takes a while: run it in the background, tell the user it started, and report progress.
Re-running resumes (finished transcript chunks are skipped). Before each download `vodcut.py` asks yt-dlp how
YouTube serves the VOD and prints the answer: post-live VODs still served as 2-second fragments go through
`ytfrag.py`; processed VODs (YouTube processes them later; the launch-day VODs took about a day) through yt-dlp,
like Twitch and Kick; a local file is read as is.

## 2. Find the winners

1. Read `vodcut/<slug>/scan/moments.md` (distinct moments, best first) and skim `scan/top_windows.md`.
2. Moments fans already clipped, or a phrase the user remembers: `vodcut.py find vodcut/<slug> "<phrase|phrase>" --add`.
3. Verify on frame sheets: `vodcut.py verify vodcut/<slug> --top 8` (add `--rank N,M` for found ones), then open every
   `verify/mNN_sheet.jpg` with Read. Keep a moment only if the picture shows the payoff the words promise.
4. Score with the [rubric](references/rubric.md) and drop anything it rules out. Default: the 3-5 best, or the
   number the user asked for.

## 3. Cut and frame 9:16

AI Producer keeps the source's orientation, so the vertical frame is made here.

```bash
python3 -I "${CLAUDE_SKILL_DIR}/scripts/frame.py" grid vodcut/<slug>/verify/m01.mp4 vodcut/<slug>/grid.png --at 5
python3 -I "${CLAUDE_SKILL_DIR}/scripts/vodcut.py" cut vodcut/<slug> --pick 1,3,4 --facecam X,Y,W,H \
  --gameplay X,Y,W,H --streamer "<name>" --handle @<handle> --platform youtube
```

Read the grid (yellow lines every 120 source px; the 360p preview is a third of 1080p, so multiply by 3) and write
the facecam rect inside the camera's border. Check one composed frame with `frame.py still` before cutting.
Override a cut with `--in-out 3=1:02:03-1:02:41` (each span downloads its own window, so a re-cut never reuses an
old one). `cut` passes `--top-ratio`, `--focus-x` and `--divider` on to `frame.py` (same `--top-ratio` for clips
that are joined later). Clips: 1080x1920, 30 fps, H.264/AAC, provenance in `.clip.json`.
Bleep words a campaign wants bleeped with `bleep.py` (words, find, apply) before upload.

## 4. Open each one in AI Producer

```bash
python3 -I "${CLAUDE_SKILL_DIR}/scripts/vodcut.py" prep vodcut/<slug>
```

`prep` checks every clip against AI Producer's limits and writes its `sign_upload` arguments. Then, per clip,
follow [the handoff](references/aip-handoff.md): `get_pricing` (once), `get_editing_preset`, `sign_upload`, PUT
the bytes with every returned header, `start_transcribe_project` (`aspect_ratio: "9:16"`, `authoring: true`), and
give the user each `page_url` as soon as its project starts. Start all uploads, then wait on the tasks.

Captions, sound effects and the export happen in AI Producer: hand each project to the official `aip:aip` skill
(captions on by default, in its plain look or the look the user names; the MP4 only when the user asks).
If no caption or edit pass follows in this session, call `finish_project` on each project once its `wait_task`
succeeds, then give the `page_url`: `finish_project` ends the `authoring: true` status that locks the editor.

## 5. Report

```bash
python3 -I "${CLAUDE_SKILL_DIR}/scripts/vodcut.py" report vodcut/<slug>
```

Tell the user: hours scanned, 30-second windows ranked, distinct moments (and how many `find --add` added), wall
time, API cost $0 (local), and one line per clip with its moment, length and AI Producer project link. Quote the
numbers as `report` prints them: it gives a wall time only when the folder's first scan fetched the audio itself
and ran start to finish, and says why when it does not. Offer to delete the audio and previews (`audio.m4a`,
`verify/*.mp4`): a day of audio is hundreds of MB.

## Rules

- Credit the streamer on every clip and post. Credit is courtesy, not a license.
- No footage from before a game's release, no endings or major story reveals, no cutscene on its own, no sexual
  content or slurs; never imply the streamer endorses anything.
- Never show signed upload URLs, export links, tool names or ids to the user; call the product "AI Producer".
- AI Producer does not find moments in long VODs and does not reframe 16:9 to 9:16: that is what this skill does
  before the upload. Say so if asked.
