#!/usr/bin/env python3
"""Scan a full-stream audio track for clip-worthy moments, locally (ffmpeg + whisper.cpp, no API calls).

Usage:
  python3 -I scan_vod.py AUDIO OUT_DIR [--chunk 1200] [--model PATH] [--threads 8] [--language en]
                                       [--skip-whisper] [--skip-loudness] [--moments 20] [--min-gap 90]

Writes to OUT_DIR:
  loudness.tsv      per-second RMS / peak dBFS (ffmpeg astats), in stream time
  transcript.jsonl  {"start","end","text"} whisper.cpp segments in stream time, chunk by chunk (resumable:
                    run the same command again and finished chunks are skipped)
  windows.tsv, top_windows.md, moments.json, moments.md   ranked by rank_windows.py (see its --help)
  run.json          what was scanned and how long each phase took, for your own stats
Stream time = seconds from the start of AUDIO (for a ytfrag.py track, the VOD's first fragment is t=0).

Whisper model: --model, else $WHISPER_MODEL, else the first that exists of
  <skill folder>/models/ggml-large-v3-turbo-q5_0.bin
  ~/.cache/whisper/ggml-large-v3-turbo-q5_0.bin
Get it once from the whisper.cpp project: models/download-ggml-model.sh large-v3-turbo-q5_0
"""
import argparse, datetime, json, math, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # explicit: python3 -I does not add the script's folder
import rank_windows  # noqa: E402

MODEL_NAME = 'ggml-large-v3-turbo-q5_0.bin'


def find_model(explicit=None):
    cands = [explicit, os.environ.get('WHISPER_MODEL'),
             os.path.join(os.path.dirname(HERE), 'models', MODEL_NAME),
             os.path.expanduser(os.path.join('~', '.cache', 'whisper', MODEL_NAME))]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return explicit or os.environ.get('WHISPER_MODEL')


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=start_time,duration', '-of', 'default=nw=1', path],
                         capture_output=True, text=True).stdout
    kv = dict(l.split('=', 1) for l in out.strip().splitlines() if '=' in l)
    st = float(kv['start_time']) if kv.get('start_time') not in (None, '', 'N/A') else 0.0
    du = float(kv['duration']) if kv.get('duration') not in (None, '', 'N/A') else 0.0
    return du, st


def loudness(audio, out_dir, start_off):
    path = os.path.join(out_dir, 'loudness.tsv')
    if os.path.exists(path):
        return path
    tmp = os.path.join(out_dir, 'astats.txt')
    run(['ffmpeg', '-loglevel', 'error', '-y', '-i', audio, '-af',
         'aresample=8000,asetnsamples=n=8000:p=0,astats=metadata=1:reset=1,'
         f'ametadata=mode=print:file={tmp}', '-f', 'null', '-'])
    rows, t, rms, pk = [], None, None, None
    with open(tmp, encoding='utf-8', errors='replace') as f:
        for line in f:
            if line.startswith('frame:'):
                if t is not None:
                    rows.append((t, rms, pk))
                m = re.search(r'pts_time:([\d.]+)', line)
                t, rms, pk = (float(m.group(1)) - start_off if m else None), None, None
            elif 'Overall.RMS_level=' in line:
                v = line.split('=')[1].strip(); rms = float(v) if v not in ('-inf', 'inf', 'nan') else -90.0
            elif 'Overall.Peak_level=' in line:
                v = line.split('=')[1].strip(); pk = float(v) if v not in ('-inf', 'inf', 'nan') else -90.0
    if t is not None:
        rows.append((t, rms, pk))
    with open(path + '.part', 'w') as w:
        w.write('t\trms_db\tpeak_db\n')
        for t, r, p in rows:
            if t is not None:
                w.write(f'{t:.1f}\t{r if r is not None else -90:.1f}\t{p if p is not None else -90:.1f}\n')
    os.replace(path + '.part', path)
    os.remove(tmp)
    return path


def whisper(audio, out_dir, total, chunk, model, start_off, threads=8, language='en'):
    """Transcribe chunk by chunk; a chunk is only marked done once all its segments are written."""
    tj = os.path.join(out_dir, 'transcript.jsonl')
    done = set()
    if os.path.exists(tj):
        rows = []
        with open(tj, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                rows.append(d)
                if d.get('marker') == 'chunk_done':
                    done.add(d.get('chunk'))
        partial = [d for d in rows if d.get('chunk') not in done]
        if partial:  # an interrupted chunk: drop its lines so the re-run does not duplicate them
            with open(tj + '.part', 'w', encoding='utf-8') as w:
                for d in rows:
                    if d.get('chunk') in done:
                        w.write(json.dumps(d) + '\n')
            os.replace(tj + '.part', tj)
    n = int(math.ceil(total / chunk))
    ran = 0
    for i in range(n):
        if i in done:
            continue
        s = i * chunk
        wav = os.path.join(out_dir, f'_chunk{i}.wav')
        run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', str(s + start_off), '-t', str(chunk), '-i', audio,
             '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', wav])
        base = os.path.join(out_dir, f'_chunk{i}')
        run(['whisper-cli', '-m', model, '-f', wav, '-l', language, '-t', str(threads), '-np', '-oj', '-bs', '1', '-bo', '1', '-of', base],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(base + '.json', 'rb') as f:
            segs = json.loads(f.read().decode('utf-8', 'replace')).get('transcription', [])
        with open(tj, 'a', encoding='utf-8') as w:
            for sg in segs:
                txt = sg['text'].strip()
                if not txt:
                    continue
                w.write(json.dumps({'chunk': i, 'start': round(s + sg['offsets']['from'] / 1000, 2),
                                    'end': round(s + sg['offsets']['to'] / 1000, 2), 'text': txt}) + '\n')
            w.write(json.dumps({'chunk': i, 'start': s, 'end': s, 'text': '', 'marker': 'chunk_done'}) + '\n')
        os.remove(wav)
        os.remove(base + '.json')
        ran += 1
        print(f'chunk {i + 1}/{n} done ({s / 3600:.2f}h)', flush=True)
    return tj, n, ran


def update_run(out_dir, **kw):
    path = os.path.join(out_dir, 'run.json')
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except ValueError:
            data = {}
    phases = data.get('phases_s', {})
    for k, v in kw.pop('phases_s', {}).items():
        phases[k] = round(phases.get(k, 0) + v, 1)  # accumulate across resumed runs
    data.update(kw)
    data['phases_s'] = phases
    data['wall_time_s'] = round(sum(phases.values()), 1)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1)
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('audio', help='audio (or video) file of the whole stream')
    ap.add_argument('out_dir')
    ap.add_argument('--chunk', type=int, default=1200, help='seconds per whisper chunk (default 1200)')
    ap.add_argument('--model', default=None, help='whisper.cpp ggml model (default: see above)')
    ap.add_argument('--threads', type=int, default=8)
    ap.add_argument('--language', default='en')
    ap.add_argument('--skip-whisper', action='store_true', help='loudness only')
    ap.add_argument('--skip-loudness', action='store_true', help='transcript only')
    ap.add_argument('--moments', type=int, default=20, help='distinct moments to keep (default 20)')
    ap.add_argument('--min-gap', type=float, default=90, help='seconds between kept moments (default 90)')
    a = ap.parse_args(argv)
    if not os.path.exists(a.audio):
        print(f'no such file: {a.audio}', file=sys.stderr)
        return 1
    model = None
    if not a.skip_whisper:
        model = find_model(a.model)
        if not model or not os.path.exists(model):
            print('whisper.cpp model not found. Pass --model, set WHISPER_MODEL, or put '
                  f'{MODEL_NAME} in <skill folder>/models/ (whisper.cpp: models/download-ggml-model.sh large-v3-turbo-q5_0).',
                  file=sys.stderr)
            return 1
    os.makedirs(a.out_dir, exist_ok=True)
    dur, st = duration(a.audio)
    print(f'audio {dur / 3600:.2f} h, start offset {st:.2f}s', flush=True)
    update_run(a.out_dir, audio=os.path.abspath(a.audio), audio_seconds=round(dur, 1), audio_hours=round(dur / 3600, 2),
               chunk_s=a.chunk, model=os.path.basename(model) if model else None,
               started_at=datetime.datetime.now().isoformat(timespec='seconds'),
               compute='local: ffmpeg + whisper.cpp', api_cost_usd=0)

    def rank_now():
        t = time.time()
        rank_windows.main([a.out_dir, '--moments', str(a.moments), '--min-gap', str(a.min_gap), '--total', str(dur)])
        return time.time() - t

    if not a.skip_loudness:
        t = time.time()
        loudness(a.audio, a.out_dir, st)
        update_run(a.out_dir, phases_s={'loudness': time.time() - t})
        print('loudness done', flush=True)
        update_run(a.out_dir, phases_s={'rank': rank_now()})
    if not a.skip_whisper:
        t = time.time()
        _, n, ran = whisper(a.audio, a.out_dir, dur, a.chunk, model, st, a.threads, a.language)
        update_run(a.out_dir, phases_s={'whisper': time.time() - t}, chunks_total=n)
        update_run(a.out_dir, phases_s={'rank': rank_now()})
    data = update_run(a.out_dir, finished_at=datetime.datetime.now().isoformat(timespec='seconds'))
    print(f"ranked windows written; wall time so far {data['wall_time_s']:.0f}s", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
