#!/usr/bin/env python3
"""Transcribe a stream while ytfrag.py is still downloading it.

`ytfrag.py track` keeps 2-second fragments in OUT.m4a.raw.frags<FORMAT>/ until the whole track is in.
This script turns every complete 20-minute run of fragments (600 fragments) into whisper.cpp segments
as soon as it lands, in the same transcript.jsonl format scan_vod.py writes, so scan_vod.py later
skips those chunks. It stops when the assembled track file appears.

Usage:
  python3 -I scan_frags.py FRAG_DIR OUT_DIR ASSEMBLED_PATH [--chunks 72] [--model PATH] [--threads 8]

Example (Speed's 23h45m VOD = 72 chunks):
  python3 -I ytfrag.py track qSyo5d-dVzA 139 vod.m4a --jobs 24 &
  python3 -I scan_frags.py vod.m4a.raw.frags139 scan/ vod.m4a --chunks 72
  python3 -I scan_vod.py vod.m4a scan/          # loudness + the chunks still missing + ranking
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # explicit: python3 -I does not add the script's folder
from scan_vod import find_model  # noqa: E402

FRAGS_PER_CHUNK = 600  # 600 x 2 s = 1200 s, the chunk size scan_vod.py uses by default


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('frag_dir')
    ap.add_argument('out_dir')
    ap.add_argument('assembled', help='the track file ytfrag.py writes when it is done; its appearance stops this script')
    ap.add_argument('--chunks', type=int, default=72, help='number of 20-minute chunks in the stream')
    ap.add_argument('--model', default=None)
    ap.add_argument('--threads', type=int, default=8)
    a = ap.parse_args(argv)
    model = find_model(a.model)
    if not model or not os.path.exists(model):
        print('whisper.cpp model not found (see scan_vod.py --help)', file=sys.stderr)
        return 1
    os.makedirs(a.out_dir, exist_ok=True)
    tj = os.path.join(a.out_dir, 'transcript.jsonl')
    done = set()
    if os.path.exists(tj):
        with open(tj, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get('marker') == 'chunk_done':
                    done.add(d['chunk'])
    for i in range(a.chunks):
        if i in done:
            continue
        sqs = range(i * FRAGS_PER_CHUNK, (i + 1) * FRAGS_PER_CHUNK)
        while True:
            if os.path.exists(a.assembled) or not os.path.isdir(a.frag_dir):
                print('assembled track exists; leaving the rest to scan_vod.py', flush=True)
                return 0
            have = sum(os.path.exists(os.path.join(a.frag_dir, f'{sq:07d}')) for sq in sqs)
            if have >= FRAGS_PER_CHUNK - 2:
                break
            time.sleep(10)
        wav = os.path.join(a.out_dir, f'_f{i}.wav')
        ff = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'mp4', '-i', 'pipe:0', '-vn', '-af',
                               'aresample=async=1:first_pts=0', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', wav],
                              stdin=subprocess.PIPE)
        ok = True
        try:
            for sq in sqs:
                p = os.path.join(a.frag_dir, f'{sq:07d}')
                if os.path.exists(p):
                    with open(p, 'rb') as r:
                        ff.stdin.write(r.read())
        except (FileNotFoundError, BrokenPipeError):
            ok = False
        ff.stdin.close()
        ff.wait()
        if not ok:
            if os.path.exists(wav):
                os.remove(wav)
            print('fragments vanished (assembly started); stopping', flush=True)
            return 0
        base = os.path.join(a.out_dir, f'_f{i}')
        subprocess.run(['whisper-cli', '-m', model, '-f', wav, '-l', 'en', '-t', str(a.threads), '-np', '-oj', '-bs', '1', '-bo', '1',
                        '-of', base], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        with open(base + '.json', 'rb') as f:
            segs = json.loads(f.read().decode('utf-8', 'replace')).get('transcription', [])
        s0 = i * FRAGS_PER_CHUNK * 2
        with open(tj, 'a', encoding='utf-8') as w:
            for sg in segs:
                txt = sg['text'].strip()
                if txt:
                    w.write(json.dumps({'chunk': i, 'start': round(s0 + sg['offsets']['from'] / 1000, 2),
                                        'end': round(s0 + sg['offsets']['to'] / 1000, 2), 'text': txt}) + '\n')
            w.write(json.dumps({'chunk': i, 'start': s0, 'end': s0, 'text': '', 'marker': 'chunk_done'}) + '\n')
        os.remove(wav)
        os.remove(base + '.json')
        print(f'chunk {i + 1}/{a.chunks} transcribed ({s0 / 3600:.2f} h)', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
