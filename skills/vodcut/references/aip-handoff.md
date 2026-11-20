# Opening a clip in AI Producer

AI Producer by OpusClip hosts each clip: word-level transcript, an editable caption workspace, timeline,
versions, export, and a durable project link (`page_url`). It is reached through the official Claude Code plugin
`aip@ai-producer-plugins`; its tools are named `mcp__plugin_aip_ai-producer__<tool>`. Read the official `aip:aip`
skill once per session: its pricing, settings, delivery and talk-to-the-user rules apply.

Every call carries `context` (15-25 words, third person, no names or personal details) and `llm_model` (your model
id, or "unknown"). Never show tool names, ids or signed URLs to the user; call the product "AI Producer".

Tools this skill calls (Claude Code names): `mcp__plugin_aip_ai-producer__get_pricing`, `mcp__plugin_aip_ai-producer__get_editing_preset`, `mcp__plugin_aip_ai-producer__sign_upload`, `mcp__plugin_aip_ai-producer__start_transcribe_project`, `mcp__plugin_aip_ai-producer__wait_task`, `mcp__plugin_aip_ai-producer__finish_project`, `mcp__plugin_aip_ai-producer__start_export`, `mcp__plugin_aip_ai-producer__get_export_url`.

## Per session

1. `get_pricing` with `{}`: the sign-in check and the price table. If preparation is free, say nothing about money;
   if a step can charge, tell the user the price before it runs. Never quote prices from memory.
   No tools, or an OAuth prompt: the user signs in with `/mcp` -> `plugin:aip:ai-producer` and starts a new session
   (the official `aip:aip-setup` skill walks them through it).

## Per clip

1. `get_editing_preset`, fresh for every new project.
2. `sign_upload` with the `filename`, `content_type`, `size` and `sha256` from `vodcut/<slug>/clips/mNN_9x16.sign.json`.
3. PUT the file with every returned header. The official helper never prints the URL: write
   `[{"path": "mNN_9x16.mp4", "upload_url": "<upload_url>", "headers": {<headers>}}]` to a temporary file outside the
   project, run `python3 "<aip scripts>/upload_batch.py" vodcut/<slug>/clips < tmp.json`, delete the file.
   `<aip scripts>` is the official aip skill's `scripts/` folder
   (`ls -d ~/.claude/plugins/cache/ai-producer-plugins/aip/*/skills/aip/scripts`). Run it with plain `python3`.
4. `start_transcribe_project`: `video_ref`, `sha256`, `filename`, `aspect_ratio: "9:16"`, `authoring: true`, and a
   `prompt` in the user's words, e.g. "Vertical stream clip of <streamer>, facecam on top and gameplay below. Keep
   the cut as uploaded and add word-by-word captions; credit <streamer>." If the reply carries `aspect_ratio_adjusted`
   the file was not portrait: re-run `vodcut.py cut`. `speechless_source` means no captions.
5. Give the user the `page_url` right away. New projects ask one question about a reference style (preset, link,
   video or skip): for a batch, ask once and apply the answer to every project.
6. `wait_task` on the `task_id` (`wait_seconds` 270, repeat until terminal; at most 8 waits at once). Write
   `project_id` and `page_url` into the clip's `.clip.json`.
7. `authoring: true` marks the project as still being edited (it lapses 30 minutes after the last call that set it),
   and `finish_project` is what ends that status and unlocks the editor. If no caption/edit pass follows in this
   session, call `finish_project` once `wait_task` succeeds, then give the `page_url`. It refuses a busy or
   unsettled project: wait for the running task first.

## After

Captions, sound effects and edits follow the official `aip:aip` skill on each project; when that pass is done, call
`finish_project` if it has not run since, then give the `page_url`. Rough cut: keep it off for gameplay clips unless
the user asks (removing "filler" from screams breaks the reaction).

Export only when the user asks for the MP4: `finish_project` (if not yet run), then `start_export`, `wait_task`,
`get_export_url`, and download at once (the link is a one-use credential; never share it). The project link is the
default delivery. Do not open or inspect the preview or the export yourself.

Limits to keep in mind: sources up to 3584 MiB and about 30 minutes for most accounts (15 in the web guide); export
one MP4 up to 1080p and 30 fps in the source orientation; one task or commit per project at a time.
