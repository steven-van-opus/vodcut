#!/usr/bin/env python3
"""vodcut: watch a whole stream VOD on your machine, find the moments, cut and frame them 9:16, and get each
winner ready to open as an AI Producer project (upload + transcribe happen through the AI Producer plugin).

Usage:
  python3 -I vodcut.py scan SOURCE OUT [--track 139] [--jobs 16] [--chunk 1200] [--model PATH] [--moments 20] [--min-gap 90]
  python3 -I vodcut.py find OUT "is that hulk|red hulk" [--add] [--pad-before 8] [--pad-after 20]
  python3 -I vodcut.py verify OUT [--top 8] [--rank 3,21] [--video 134] [--audio 139]
  python3 -I vodcut.py cut OUT --pick 1,3 --facecam X,Y,W,H [--gameplay X,Y,W,H] [--layout stack|inset|full]
                           [--top-ratio R] [--focus-x PX] [--divider PX]
                           [--in-out 3=1:02:03-1:02:41] [--streamer NAME] [--handle @name] [--platform youtube]
                           [--video 299] [--audio 140] [--keep-src]
  python3 -I vodcut.py prep OUT
  python3 -I vodcut.py report OUT [--json]
Add --dry-run to scan, verify or cut to print the commands without running them.

find   searches the transcript for words you already know matter (a fan clip's title, a phrase chat spammed): the
       loudness/cue ranking misses quiet or short moments. --add appends each hit to scan/moments.json as a new
       moment (ranks after the scanned ones), so verify --rank and cut --pick can use it.
cut    --top-ratio, --focus-x and --divider go to frame.py compose (see its --help): give every item of a
       compilation the same --top-ratio so the facecam/gameplay seam sits on the same line in all of them.

SOURCE: a YouTube video id or URL, any other URL yt-dlp understands (Twitch or Kick VODs), or a local audio/video
file. Before each download vodcut asks yt-dlp how YouTube serves the VOD: right after a stream it is often still
2-second fragments (protocol http_dash_segments), which ytfrag.py fetches; later YouTube processes it into
whole files (https; the launch-day VODs took about a day), which go through yt-dlp like Twitch and Kick.

OUT/
  vodcut.json               the source, its delivery protocol, the scan runs and what every step produced
  audio.m4a                 mono 16 kHz track the scan reads (downloaded or extracted)
  scan/                     loudness.tsv, transcript.jsonl, windows.tsv, top_windows.md, moments.json, moments.md, run.json
  verify/mNN.mp4            360p preview of moment NN, mNN_sheet.jpg its frame sheet, index.md the list to read
  clips/mNN_9x16.mp4        the finished 1080x1920 30 fps clip, .clip.json its provenance + credit, .sign.json the
                            sign_upload arguments (filename, content_type, size, sha256)
  clips/mNN_src_IN-OUT.mp4  the downloaded window, named by its span (kept with --keep-src or after a failed compose)
Everything runs locally (ffmpeg, whisper.cpp, yt-dlp): no API calls, nothing uploaded by this script.
"""
import argparse, datetime, glob, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = [sys.executable, '-I']
YT_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
FRAGMENTED = 'http_dash_segments'  # yt-dlp's protocol while a YouTube VOD is still served as 2-second fragments


def script(name):
    return os.path.join(HERE, name)


def part_path(out):
    """Where an output is written until it is complete (same folder and extension, so ffmpeg keeps the muxer):
    a step that is interrupted never leaves a truncated file that a later run would take for a finished one."""
    root, ext = os.path.splitext(out)
    return root + '.part' + ext


def hms_to_s(v):
    v = str(v).strip()
    if ':' not in v:
        return float(v)
    s = 0.0
    for p in v.split(':'):
        s = s * 60 + float(p)
    return s


def s_to_hms(s):
    s = int(round(float(s)))
    return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}'


def classify(src):
    if os.path.exists(src):
        return {'kind': 'file', 'path': os.path.abspath(src)}
    if YT_ID.match(src):
        return {'kind': 'youtube', 'id': src, 'url': f'https://www.youtube.com/watch?v={src}'}
    m = re.search(r'(?:youtube\.com/(?:watch\?(?:.*&)?v=|live/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})', src)
    if m:
        return {'kind': 'youtube', 'id': m.group(1), 'url': f'https://www.youtube.com/watch?v={m.group(1)}'}
    if re.match(r'^https?://', src):
        return {'kind': 'url', 'url': src}
    raise ValueError(f'{src!r} is not a file, a YouTube id/URL or a URL')


def same_source(a, b):
    """Two classified sources name the same VOD (the stored one may also carry its probed protocol)."""
    return bool(a) and all(a.get(k) == b.get(k) for k in ('kind', 'id', 'url', 'path'))


def probe_protocol(url, fmt):
    """yt-dlp's delivery protocol for the format `fmt` selects: 'http_dash_segments' while a YouTube VOD is still
    served as 2-second fragments, 'https' once YouTube has processed it into whole files. Read-only, a second or two."""
    out = subprocess.run(['yt-dlp', '--no-warnings', '--no-playlist', '--skip-download', '-f', fmt, '--print', 'protocol',
                          url], capture_output=True, text=True)
    lines = out.stdout.strip().splitlines()
    if out.returncode != 0 or not lines:
        err = (out.stderr.strip().splitlines() or ['no output'])[-1]
        raise ValueError(f'yt-dlp could not read {url} (format {fmt}): {err}')
    return lines[-1].strip()


def describe(protocol):
    return ('still served as 2-second fragments: ytfrag.py' if protocol == FRAGMENTED
            else f'processed VOD (protocol {protocol}): yt-dlp')


def fresh_source(out, st, fmt, dry=False):
    """The run's source, with a YouTube VOD's protocol asked again: YouTube moves a VOD from fragments to whole files
    some time after the stream, so the answer stored at scan time can be stale by the time you verify or cut."""
    src = st['source']
    if src['kind'] == 'youtube':
        p = probe_protocol(src['url'], fmt)
        print(f"# {src['id']}: {describe(p)}", flush=True)
        if p != src.get('protocol'):
            src['protocol'] = p
            if not dry:
                save_state(out, st)
    return src


def done_chunks(tj):
    """Whisper chunks scan/transcript.jsonl marks finished."""
    done = set()
    if os.path.exists(tj):
        with open(tj, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get('marker') == 'chunk_done':
                    done.add(d.get('chunk'))
    return done


def state_path(out):
    return os.path.join(out, 'vodcut.json')


def load_state(out):
    p = state_path(out)
    if not os.path.exists(p):
        raise ValueError(f'{out} has no vodcut.json: run `vodcut.py scan SOURCE {out}` first')
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def save_state(out, st):
    os.makedirs(out, exist_ok=True)
    with open(state_path(out) + '.part', 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1, ensure_ascii=False)
    os.replace(state_path(out) + '.part', state_path(out))


def run(cmd, dry):
    if cmd[0] == '__rename__':  # [__rename__, part, final]: publish a finished output in one step
        print(f'$ mv {cmd[1]} {cmd[2]}', flush=True)
        if not dry:
            os.replace(cmd[1], cmd[2])
        return
    print('$ ' + ' '.join(cmd), flush=True)
    if not dry:
        subprocess.run(cmd, check=True)


def fragmented(src):
    return src['kind'] == 'youtube' and src.get('protocol') == FRAGMENTED


def extract_cmd(inp, dst):
    """ffmpeg: any audio/video file -> the mono 16 kHz AAC track the scan reads, written to dst's .part file."""
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', inp, '-vn', '-ac', '1', '-ar', '16000',
            '-c:a', 'aac', '-b:a', '32k', part_path(dst)]


def audio_cmds(src, out, track='139', jobs=16):
    """Commands that leave OUT/audio.m4a: the mono 16 kHz track the scan reads. ['__extract__', IN, DST, MODE] runs
    extract_cmd and renames the result into place; MODE 'download' (IN is a glob of what yt-dlp wrote, deleted
    afterwards) or 'file' (IN is the user's own file, left alone)."""
    dst = os.path.join(out, 'audio.m4a')
    if fragmented(src):
        return dst, [PY + [script('ytfrag.py'), 'track', src['id'], str(track), dst, '--jobs', str(jobs)]]
    if src['kind'] in ('youtube', 'url'):
        # audio-only when the site has it, else the smallest full rendition (never full-quality video for a scan)
        fmt = f'{track}/ba/w' if src['kind'] == 'youtube' else 'ba/w'
        tmpl = os.path.join(out, 'audio_dl.%(ext)s')
        return dst, [['yt-dlp', '--no-warnings', '--no-playlist', '-f', fmt, '-o', tmpl, src['url']],
                     ['__extract__', os.path.join(glob.escape(out), 'audio_dl.*'), dst, 'download']]
    return dst, [['__extract__', src['path'], dst, 'file']]


def run_audio(cmds, dry):
    for c in cmds:
        if c[0] != '__extract__':
            run(c, dry)
            continue
        _, inp, dst, mode = c
        if mode == 'download':
            if dry:
                print(f'$ ffmpeg -i {inp} -vn -ac 1 -ar 16000 -c:a aac -b:a 32k {dst}', flush=True)
                continue
            got = sorted(p for p in glob.glob(inp) if not re.search(r'\.(part|ytdl)$|\.part-Frag', p))
            if not got:
                raise ValueError('yt-dlp wrote no audio file')
            inp = got[0]
        run(extract_cmd(inp, dst), dry)
        run(['__rename__', part_path(dst), dst], dry)
        if mode == 'download' and not dry:
            os.remove(inp)


def window_cmd(src, a, b, dst, video, audio, preview=False):
    """Commands that write the stream window [a, b] to dst (mp4). Each one leaves dst only once it is complete
    (ytfrag.py and yt-dlp write a .part file first; the local-file branch renames its own)."""
    if fragmented(src):
        return [PY + [script('ytfrag.py'), 'window', src['id'], f'{a:.2f}', f'{b:.2f}', dst, '--video', str(video), '--audio', str(audio)]]
    if src['kind'] in ('youtube', 'url'):
        fmt = 'bv*[height<=360]+ba/b[height<=360]/w' if preview else 'bv*[height<=1080]+ba/b'
        if src['kind'] == 'youtube':  # a processed YouTube VOD: the same format ids ytfrag.py would use, then any
            fmt = f'{video}+{audio}/{fmt}'
        return [['yt-dlp', '--no-warnings', '--no-playlist', '-f', fmt, '--download-sections', f'*{a:.2f}-{b:.2f}',
                 '--merge-output-format', 'mp4', '-o', dst, src['url']]]
    vf = ['-vf', 'scale=-2:360'] if preview else []
    return [['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{a:.3f}', '-t', f'{b - a:.3f}', '-i', src['path']]
            + vf + ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28' if preview else '16', '-c:a', 'aac', part_path(dst)],
            ['__rename__', part_path(dst), dst]]


def has_video(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=codec_type',
                          '-of', 'csv=p=0', path], capture_output=True, text=True).stdout
    return 'video' in out


def cmd_scan(a):
    src = classify(a.source)
    os.makedirs(a.out, exist_ok=True)
    st = {'source': src, 'created_at': datetime.datetime.now().isoformat(timespec='seconds'), 'phases_s': {}}
    if os.path.exists(state_path(a.out)):
        old = load_state(a.out)
        if not same_source(old.get('source'), src):
            raise ValueError(f'{a.out} already holds a scan of another source; use a new folder')
        st = old
    dst = os.path.join(a.out, 'audio.m4a')
    scan_dir = os.path.join(a.out, 'scan')
    fetch = not os.path.exists(dst)
    if fetch:
        fresh_source(a.out, st, f'{a.track}/ba', dry=True)  # saved below, with this run's record
    _, cmds = audio_cmds(st['source'], a.out, a.track, a.jobs)
    # One record per invocation: the launch numbers (report WALL_TIME) only come from a run that fetched the
    # audio itself, started with no transcript and finished; a resumed or seeded folder has no honest wall time.
    rec = {'started_at': datetime.datetime.now().isoformat(timespec='seconds'), 'audio_fetched': fetch,
           'chunks_done_before': len(done_chunks(os.path.join(scan_dir, 'transcript.jsonl'))), 'finished': False}
    if not a.dry_run:
        st.setdefault('scan_runs', []).append(rec)
        save_state(a.out, st)
    t0 = time.time()
    if fetch:
        run_audio(cmds, a.dry_run)
    st['phases_s']['audio'] = round(st['phases_s'].get('audio', 0) + time.time() - t0, 1)
    cmd = PY + [script('scan_vod.py'), dst, scan_dir, '--chunk', str(a.chunk), '--moments', str(a.moments),
                '--min-gap', str(a.min_gap)] + (['--model', a.model] if a.model else [])
    run(cmd, a.dry_run)
    if a.dry_run:
        return 0
    rec.update(finished=True, elapsed_s=round(time.time() - t0, 1),
               finished_at=datetime.datetime.now().isoformat(timespec='seconds'))
    save_state(a.out, st)
    return cmd_report(argparse.Namespace(out=a.out, json=False))


def moments_of(out):
    p = os.path.join(out, 'scan', 'moments.json')
    if not os.path.exists(p):
        raise ValueError(f'no {p}: run scan first')
    with open(p, encoding='utf-8') as f:
        return json.load(f)['moments']


def cmd_find(a):
    load_state(a.out)
    try:
        rx = re.compile(a.pattern, re.I)
    except re.error as e:
        raise ValueError(f'bad pattern: {e}')
    tj = os.path.join(a.out, 'scan', 'transcript.jsonl')
    if not os.path.exists(tj):
        raise ValueError('no scan/transcript.jsonl yet: run scan first')
    hits = []
    with open(tj, encoding='utf-8', errors='replace') as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not d.get('marker') and rx.search(d.get('text', '')):
                hits.append((float(d['start']), float(d.get('end', d['start'])), d['text'].strip()))
    hits.sort()
    groups = []
    for h in hits:  # hits within 30 s of each other are one moment
        if groups and h[0] - groups[-1][-1][0] <= 30:
            groups[-1].append(h)
        else:
            groups.append([h])
    path = os.path.join(a.out, 'scan', 'moments.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    next_rank = max([m['rank'] for m in data['moments']] + [0]) + 1
    found = []
    for g in groups:
        t_in, t_out = max(0.0, g[0][0] - a.pad_before), g[-1][1] + a.pad_after
        text = ' '.join(dict.fromkeys(h[2] for h in g))
        dup = next((m for m in data['moments'] if m['in'] <= g[0][0] <= m['out']), None)
        found.append({'in': round(t_in, 2), 'out': round(t_out, 2), 'in_hms': s_to_hms(t_in), 'out_hms': s_to_hms(t_out),
                      'hits': len(g), 'text': text[:400], 'already_moment': dup['rank'] if dup else None})
    if a.add:
        for m in found:
            if m['already_moment']:
                continue
            m['rank'] = next_rank
            next_rank += 1
            data['moments'].append({'rank': m['rank'], 'score': 0.0, 'window_start': m['in'], 'window_end': m['out'],
                                    'in': m['in'], 'out': m['out'], 'in_hms': m['in_hms'], 'out_hms': m['out_hms'],
                                    'loud_top5_db': None, 'cues': 0, 'words': len(m['text'].split()), 'flags': ['found'],
                                    'text': m['text'], 'text_before': '', 'text_after': '', 'pattern': a.pattern})
        with open(path + '.part', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(path + '.part', path)
    print(json.dumps({'pattern': a.pattern, 'hits': len(hits), 'moments': found, 'added': bool(a.add)}, indent=1,
                     ensure_ascii=False))
    return 0 if hits else 1


def cmd_verify(a):
    st = load_state(a.out)
    src = st['source']
    allm = moments_of(a.out)
    if a.rank:
        want = {int(x) for x in str(a.rank).split(',') if x.strip()}
        ms = [m for m in allm if m['rank'] in want]
    else:
        ms = allm[:a.top]
    vdir = os.path.join(a.out, 'verify')
    os.makedirs(vdir, exist_ok=True)
    rows = []
    probed = False
    for m in ms:
        tag = f"m{m['rank']:02d}"
        clip, sheet = os.path.join(vdir, f'{tag}.mp4'), os.path.join(vdir, f'{tag}_sheet.jpg')
        if src['kind'] == 'file' and not has_video(src['path']):
            rows.append((m, None, 'audio-only source: no frames to check'))
            continue
        if not os.path.exists(clip):
            if not probed:  # once per run, right before the first download
                src, probed = fresh_source(a.out, st, f'{a.audio}/ba', a.dry_run), True
            for c in window_cmd(src, m['in'], m['out'], clip, a.video, a.audio, preview=True):
                run(c, a.dry_run)
        run(PY + [script('framesheet.py'), clip, sheet, '--cols', '4', '--rows', '3', '--width', '1600'], a.dry_run)
        rows.append((m, sheet, ''))
    if a.dry_run:
        return 0
    with open(os.path.join(vdir, 'index.md'), 'w', encoding='utf-8') as f:
        f.write('# Moments to verify\n\nOpen each sheet (12 frames across the cut, left to right, top to bottom). Keep a moment only '
                'if the picture shows the payoff the transcript promises and nothing in it rules the clip out.\n\n')
        for m, sheet, note in rows:
            fl = f" | review: {', '.join(m['flags'])}" if m['flags'] else ''
            f.write(f"## #{m['rank']} {m['in_hms']} -> {m['out_hms']} (score {m['score']:.2f}{fl})\n")
            f.write(f"sheet: {os.path.relpath(sheet, a.out) if sheet else note}\n\n{m['text']}\n\n")
    st['verify'] = [{'rank': m['rank'], 'sheet': sheet} for m, sheet, _ in rows]
    save_state(a.out, st)
    print(f"wrote {os.path.join(vdir, 'index.md')} ({len(rows)} moments)")
    return 0


def parse_overrides(items):
    out = {}
    for it in items or []:
        rank, _, span = it.partition('=')
        a, _, b = span.partition('-')
        if not rank.isdigit() or not a or not b:
            raise ValueError(f'--in-out {it!r}: use RANK=START-END, e.g. 3=1:02:03-1:02:41')
        out[int(rank)] = (hms_to_s(a), hms_to_s(b))
    return out


def cmd_cut(a):
    st = load_state(a.out)
    src = st['source']
    ms = {m['rank']: m for m in moments_of(a.out)}
    picks = [int(x) for x in str(a.pick).split(',') if x.strip()]
    over = parse_overrides(a.in_out)
    missing = [p for p in picks if p not in ms and p not in over]
    if missing:
        raise ValueError(f'no moment(s) {missing} in scan/moments.json')
    spans = {}
    for p in picks:  # check every pick before the first download
        t_in, t_out = over[p] if p in over else (ms[p]['in'], ms[p]['out'])
        if t_out <= t_in:
            raise ValueError(f'moment {p}: END must be after START')
        if t_out - t_in > 180:
            raise ValueError(f'moment {p} is {t_out - t_in:.0f} s long: clips run 20-90 s; pass --in-out')
        spans[p] = (t_in, t_out)
    cdir = os.path.join(a.out, 'clips')
    os.makedirs(cdir, exist_ok=True)
    layout = ['--layout', a.layout] + (['--facecam', a.facecam] if a.facecam else []) + \
             (['--gameplay', a.gameplay] if a.gameplay else []) + \
             (['--top-ratio', f'{a.top_ratio:g}'] if a.top_ratio is not None else []) + \
             (['--focus-x', f'{a.focus_x:g}'] if a.focus_x is not None else []) + \
             (['--divider', str(a.divider)] if a.divider is not None else [])
    made = []
    probed = False
    for p in picks:
        t_in, t_out = spans[p]
        tag = f'm{p:02d}'
        final = os.path.join(cdir, f'{tag}_9x16.mp4')
        if src['kind'] == 'file':
            run(PY + [script('frame.py'), 'compose', src['path'], final, '--start', f'{t_in:.3f}', '--end', f'{t_out:.3f}'] + layout, a.dry_run)
        else:
            # The downloaded window is named by its span, so a re-cut with other times never composes an old window
            # (one kept by --keep-src, or left by a compose that failed on a wrong --facecam).
            raw = os.path.join(cdir, f'{tag}_src_{t_in:.2f}-{t_out:.2f}.mp4')
            if not os.path.exists(raw):
                if not probed:  # once per run, right before the first download
                    src, probed = fresh_source(a.out, st, f'{a.audio}/ba', a.dry_run), True
                for c in window_cmd(src, t_in, t_out, raw, a.video, a.audio):
                    run(c, a.dry_run)
            run(PY + [script('frame.py'), 'compose', raw, final] + layout, a.dry_run)
            if not a.keep_src and not a.dry_run:  # this window and any earlier spans of the same moment
                for old in glob.glob(os.path.join(glob.escape(cdir), f'{tag}_src_*.mp4')):
                    os.remove(old)
        if a.streamer:
            url = src.get('url') or src.get('path')
            run(PY + [script('aip_prep.py'), 'record', final, '--streamer', a.streamer, '--platform', a.platform,
                      '--source-url', url, '--start', f'{t_in:.2f}', '--end', f'{t_out:.2f}']
                + (['--handle', a.handle] if a.handle else []), a.dry_run)
        made.append({'rank': p, 'clip': os.path.relpath(final, a.out), 'in': t_in, 'out': t_out})
    if a.dry_run:
        return 0
    st.setdefault('clips', [])
    st['clips'] = [c for c in st['clips'] if c['rank'] not in picks] + made
    save_state(a.out, st)
    print(json.dumps({'ok': True, 'clips': made}, indent=1))
    return 0


def cmd_prep(a):
    st = load_state(a.out)
    clips = sorted(glob.glob(os.path.join(a.out, 'clips', '*_9x16.mp4')))
    if not clips:
        raise ValueError('no clips/*_9x16.mp4 yet: run cut first')
    rows = []
    for c in clips:
        chk = json.loads(subprocess.run(PY + [script('aip_prep.py'), 'check', c], capture_output=True, text=True).stdout)
        args = None
        if chk['ok']:
            args = json.loads(subprocess.run(PY + [script('aip_prep.py'), 'sign-args', c], capture_output=True, text=True,
                                             check=True).stdout)
            with open(os.path.splitext(c)[0] + '.sign.json', 'w', encoding='utf-8') as f:
                json.dump(args, f, indent=1)
        rows.append({'clip': os.path.relpath(c, a.out), 'ok': chk['ok'], 'errors': chk['errors'], 'warnings': chk['warnings'],
                     'seconds': round(chk['facts'].get('duration') or 0, 2), 'sign_upload': args})
    st['prep'] = rows
    save_state(a.out, st)
    print(json.dumps({'ready': [r['clip'] for r in rows if r['ok']], 'blocked': [r['clip'] for r in rows if not r['ok']],
                      'rows': rows,
                      'next': 'For each ready clip: mcp__plugin_aip_ai-producer__sign_upload with its sign_upload args, PUT the '
                              'file with every returned header, then mcp__plugin_aip_ai-producer__start_transcribe_project '
                              '(video_ref, sha256, filename, prompt, aspect_ratio "9:16", authoring true). If no caption or '
                              'edit pass follows, mcp__plugin_aip_ai-producer__finish_project once its task succeeded, so '
                              'the editor behind page_url opens unlocked.'}, indent=1))
    return 0 if all(r['ok'] for r in rows) else 1


def count_rows(path):
    """Ranked windows in scan/windows.tsv: the rows that start with a score and a start time."""
    if not os.path.exists(path):
        return None
    row = re.compile(r'^-?\d+(?:\.\d+)?\t\d')
    with open(path, encoding='utf-8', errors='replace') as f:
        return sum(1 for line in f if row.match(line))


def report_data(out):
    """What the scan measured. WALL_TIME is the elapsed time of this folder's first scan, and only when that run
    fetched the audio itself, started with no transcript and finished: a resumed or seeded folder gets None, with
    the reason in wall_time_note, so no launch post quotes a number no run produced."""
    st = load_state(out)
    run_p = os.path.join(out, 'scan', 'run.json')
    run_d = {}
    if os.path.exists(run_p):
        with open(run_p, encoding='utf-8') as f:
            run_d = json.load(f)
    try:
        ms = moments_of(out)
    except ValueError:
        ms = []
    runs = st.get('scan_runs', [])
    first = runs[0] if runs else {}
    whole = bool(first.get('finished') and first.get('audio_fetched') and not first.get('chunks_done_before'))
    if whole:
        note = None
    elif not runs:
        note = 'no scan run recorded in this folder'
    elif not first.get('finished'):
        note = 'the first scan was interrupted (or is still running): resumed runs have no single wall time'
    else:
        note = 'the first scan started from audio or transcript chunks that were already here (seeded or resumed)'
    found = [m for m in ms if 'found' in (m.get('flags') or [])]
    processing = (run_d.get('wall_time_s') or 0) + (st.get('phases_s', {}).get('audio') or 0)
    return {
        'HOURS_SCANNED': run_d.get('audio_hours'),
        'WINDOWS_RANKED': count_rows(os.path.join(out, 'scan', 'windows.tsv')),
        'MOMENTS_RANKED': len(ms) - len(found),
        'MOMENTS_ADDED_BY_FIND': len(found),
        'WALL_TIME': s_to_hms(first['elapsed_s']) if whole else None,
        'wall_time_s': first.get('elapsed_s') if whole else None,
        'wall_time_note': note,
        'API_COST_USD': 0,
        'scan_runs': len(runs),
        'processing_s': round(processing, 1) if processing else None,
        'chunks_transcribed': run_d.get('chunks_total'),
        'phases_s': dict(run_d.get('phases_s', {}), audio_fetch=st.get('phases_s', {}).get('audio')),
        'clips_cut': len(st.get('clips', [])),
        'source': st.get('source'),
        'top': [{'rank': m['rank'], 'at': m['in_hms'], 'score': m['score'], 'text': m['text'][:120]} for m in ms[:5]],
    }


def cmd_report(a):
    d = report_data(a.out)
    if a.json:
        print(json.dumps(d, indent=1, ensure_ascii=False))
        return 0
    added = f" (+{d['MOMENTS_ADDED_BY_FIND']} added by find)" if d['MOMENTS_ADDED_BY_FIND'] else ''
    wall = d['WALL_TIME'] or f"not measured ({d['wall_time_note']})"
    n = d['MOMENTS_RANKED']
    print(f"Scanned {d['HOURS_SCANNED']} h of stream, ranked {d['WINDOWS_RANKED']} 30-second windows into "
          f"{n} distinct moment{'' if n == 1 else 's'}{added}, wall time {wall}, API cost $0 (everything ran locally).")
    for t in d['top']:
        print(f"  #{t['rank']} {t['at']} (score {t['score']:.2f}) {t['text']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('scan', help='download (or read) the audio, transcribe, rank the moments')
    s.add_argument('source'); s.add_argument('out')
    s.add_argument('--track', default='139'); s.add_argument('--jobs', type=int, default=16)
    s.add_argument('--chunk', type=int, default=1200); s.add_argument('--model', default=None)
    s.add_argument('--moments', type=int, default=20); s.add_argument('--min-gap', type=float, default=90)
    s.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('find', help='search the transcript for known phrases; --add turns hits into moments')
    s.add_argument('out'); s.add_argument('pattern', help='regular expression, case-insensitive')
    s.add_argument('--add', action='store_true')
    s.add_argument('--pad-before', type=float, default=8); s.add_argument('--pad-after', type=float, default=20)
    s = sub.add_parser('verify', help='360p preview + frame sheet for the top moments')
    s.add_argument('out'); s.add_argument('--top', type=int, default=8)
    s.add_argument('--rank', default=None, help='verify these moment ranks instead of the top N, e.g. 3,21')
    s.add_argument('--video', default='134'); s.add_argument('--audio', default='139')
    s.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('cut', help='download the chosen windows in full quality and compose them 9:16')
    s.add_argument('out'); s.add_argument('--pick', required=True, help='moment ranks, e.g. 1,3,4')
    s.add_argument('--facecam', default=None); s.add_argument('--gameplay', default=None)
    s.add_argument('--layout', default='stack', choices=['stack', 'inset', 'full'])
    s.add_argument('--top-ratio', type=float, default=None,
                   help='stack: facecam band height / 1920, e.g. 0.35 (passed to frame.py compose)')
    s.add_argument('--focus-x', type=float, default=None,
                   help='centre x of the gameplay crop in source px (passed to frame.py compose)')
    s.add_argument('--divider', type=int, default=None,
                   help='stack: dark seam between the bands in px (passed to frame.py compose)')
    s.add_argument('--in-out', action='append', default=[], help='RANK=START-END to override a cut, repeatable')
    s.add_argument('--streamer', default=None); s.add_argument('--handle', default=None)
    s.add_argument('--platform', default='youtube')
    s.add_argument('--video', default='299'); s.add_argument('--audio', default='140')
    s.add_argument('--keep-src', action='store_true'); s.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('prep', help='check every clip and write its sign_upload arguments')
    s.add_argument('out')
    s = sub.add_parser('report', help='what was scanned, found and cut')
    s.add_argument('out'); s.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if a.cmd in ('scan', 'verify', 'cut') and not a.dry_run:
        for tool in ('ffmpeg', 'ffprobe'):
            if not shutil.which(tool):
                print(f'{tool} is required (brew install ffmpeg)', file=sys.stderr)
                return 1
    if a.cmd == 'cut' and a.layout in ('stack', 'inset') and not a.facecam:
        print('error: --facecam X,Y,W,H is required for the stack and inset layouts (read it with frame.py grid)', file=sys.stderr)
        return 2
    try:
        return {'scan': cmd_scan, 'find': cmd_find, 'verify': cmd_verify, 'cut': cmd_cut, 'prep': cmd_prep,
                'report': cmd_report}[a.cmd](a)
    except ValueError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f'error: a step failed (exit {e.returncode}): {" ".join(map(str, e.cmd))[:300]}', file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f'error: {e.filename or e} was not found: install it (brew install ffmpeg yt-dlp whisper-cpp) '
              'or check the path', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
