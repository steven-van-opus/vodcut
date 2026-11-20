# Picking moments: the rubric

Read this when choosing which scanned moments become clips.

## Drop it, whatever it scores

- slurs or hate, harassment of a real person, private information, anyone who looks like a minor at risk
- sexual content, including in the background
- an ending, a final mission or a major story reveal; a cutscene shown on its own; an in-game TV show or
  performance on its own (publishers' video policies remove these; Rockstar's does for GTA)
- footage from before the game's release, leaked footage, cheats, mods or region tricks
- gambling, crypto or cannabis promotion
- real-world violence talk, medical emergencies, the streamer's private life

Scan flags help: `profanity` (bleep it if the brief asks), `sexual`, `spoiler`, `loop` (whisper hallucinated a
repeated line over music: listen before trusting the text), `found` (added by `vodcut.py find`).

## Score the rest (0-3 each)

| | 0 | 3 |
|---|---|---|
| Hook | nothing in the first 2 s | something said or done in the first second |
| Payoff | it fizzles | a clear peak: bust, crash, scare, discovery, win or fail |
| Reaction | voice only | face and voice both, on camera |
| Stands alone | needs the stream's context | a stranger gets it with captions on |
| Readable | menus, map, loading | the action reads at phone size |
| Novelty | seen a hundred times | a first, an easter egg, something only this stream has |
| Fit | wrong for the page | the page's streamer and length (20-45 s) |

Keep 12+ out of 21.

## Cut points

Start on the hook (one beat before at most), end on the reaction's last word plus a breath, never mid-sentence.
20-45 s for a single clip; hard cap 90 s. The scan's suggested in/out pads the 30 s window by 6 s before and 8 s
after: tighten it with `--in-out RANK=START-END`.
