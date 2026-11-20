#!/usr/bin/env python3
"""Bleep (or mute) words in a clip before it goes to AI Producer, with exact word times from whisper.cpp.

Some campaigns and pages want profanity bleeped. AI Producer keeps the voice as recorded, so the bleep is
baked in here, on the composed 9:16 clip, before upload. The video stream is copied untouched.

Usage:
  python3 -I bleep.py words CLIP.mp4 WORDS.json [--model PATH] [--language en]
  python3 -I bleep.py find WORDS.json [--pattern REGEX] [--pad 0.04]
  python3 -I bleep.py apply CLIP.mp4 OUT.mp4 --spans 3.20-3.62,7.90-8.31 [--mute] [--tone 1000] [--dry-run]

words  word-level transcript of the clip (whisper.cpp -ml 1), seconds from the clip's start
find   the spans to bleep: strong profanity by default, or your own --pattern; prints JSON with a ready --spans
apply  replaces each span with a tone (or silence with --mute); everything else is untouched
Model lookup is the same as scan_vod.py (--model, $WHISPER_MODEL, <skill folder>/models/, ~/.cache/whisper/).
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # explicit: python3 -I does not add the script's folder
from scan_vod import find_model  # noqa: E402

PROFANITY = r"\b(f+u+c+k\w*|motherf\w*|sh+i+t+\w*|bitch\w*|asshole\w*|dick\w*|pussy)\b"


def parse_whisper_words(data):
    """whisper.cpp -ml 1 -oj output -> [{text, start, end}] (seconds), empty tokens dropped."""
    words = []
    for tok in data.get('transcription', []):
        text = (tok.get('text') or '').strip()
        if not text or text.startswith('['):
            continue
        off = tok.get('offsets') or {}
        words.append({'text': text, 'start': round(off.get('from', 0) / 1000.0, 3), 'end': round(off.get('to', 0) / 1000.0, 3)})
    return words


def find_spans(words, pattern=PROFANITY, pad=0.04):
    rx = re.compile(pattern, re.I)
    spans = []
    for w in words:
        if rx.search(w['text']):
            a, b = max(0.0, w['start'] - pad), w['end'] + pad
            if spans and a <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], b))
            else:
                spans.append((a, b))
    return [(round(a, 3), round(b, 3)) for a, b in spans]


def parse_spans(text):
    spans = []
    for item in filter(None, str(text).split(',')):
        a, sep, b = item.strip().partition('-')
        try:
            a, b = float(a), float(b)
        except ValueError:
            raise ValueError(f'bad span {item!r}: use START-END in seconds, e.g. 3.20-3.62')
        if not sep or b <= a or a < 0:
            raise ValueError(f'bad span {item!r}')
        spans.append((a, b))
    if not spans:
        raise ValueError('no spans given')
    return sorted(spans)


def enable_expr(spans):
    return '+'.join(f'between(t,{a:.3f},{b:.3f})' for a, b in spans)


def apply_cmd(inp, out, spans, duration, mute=False, tone=1000, level=0.25):
    expr = enable_expr(spans)
    if mute:
        graph = f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume=volume=0:enable='{expr}'[a]"
    else:
        wave = f'{level}*sin(2*PI*{int(tone)}*t)*({expr})'
        graph = (f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume=volume=0:enable='{expr}'[m];"
                 f"aevalsrc=exprs='{wave}|{wave}':s=48000:c=stereo:d={duration:.3f}[b];"
                 f"[m][b]amix=inputs=2:duration=first:normalize=0[a]")
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', inp, '-filter_complex', graph,
            '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', out]


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        raise ValueError(f'could not read the duration of {path}')


def cmd_words(a):
    model = find_model(a.model)
    if not model or not os.path.exists(model):
        raise ValueError('whisper.cpp model not found (see scan_vod.py --help)')
    with tempfile.TemporaryDirectory() as d:
        wav, base = os.path.join(d, 'a.wav'), os.path.join(d, 'w')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', a.clip, '-vn', '-ar', '16000', '-ac', '1',
                        '-c:a', 'pcm_s16le', wav], check=True)
        subprocess.run(['whisper-cli', '-m', model, '-f', wav, '-l', a.language, '-ml', '1', '-sow', '-oj', '-of', base],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        with open(base + '.json', 'rb') as f:
            data = json.loads(f.read().decode('utf-8', 'replace'))
    words = parse_whisper_words(data)
    with open(a.words_json, 'w', encoding='utf-8') as f:
        json.dump({'clip': os.path.basename(a.clip), 'words': words}, f, indent=1, ensure_ascii=False)
    print(json.dumps({'words': len(words), 'out': a.words_json}))
    return 0


def cmd_find(a):
    with open(a.words_json, encoding='utf-8') as f:
        data = json.load(f)
    words = data['words'] if isinstance(data, dict) else data
    spans = find_spans(words, a.pattern or PROFANITY, a.pad)
    hits = [w for w in words if re.search(a.pattern or PROFANITY, w['text'], re.I)]
    print(json.dumps({'hits': hits, 'spans': spans, 'spans_arg': ','.join(f'{x:.2f}-{y:.2f}' for x, y in spans)},
                     indent=1, ensure_ascii=False))
    return 0


def cmd_apply(a):
    spans = parse_spans(a.spans)
    if not a.dry_run and not shutil.which('ffmpeg'):
        raise ValueError('ffmpeg is required (brew install ffmpeg)')
    dur = duration(a.clip) if not a.dry_run else spans[-1][1] + 1
    if spans[-1][1] > dur + 0.05:
        raise ValueError(f'span {spans[-1]} runs past the clip ({dur:.2f} s)')
    cmd = apply_cmd(a.clip, a.out, spans, dur, a.mute, a.tone)
    if a.dry_run:
        print(json.dumps({'cmd': cmd}))
        return 0
    subprocess.run(cmd, check=True)
    print(json.dumps({'out': a.out, 'spans': spans, 'mode': 'mute' if a.mute else f'{a.tone} Hz tone'}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('words', help='word-level transcript of a clip (whisper.cpp)')
    s.add_argument('clip'); s.add_argument('words_json')
    s.add_argument('--model', default=None); s.add_argument('--language', default='en')
    s = sub.add_parser('find', help='spans to bleep from a words file')
    s.add_argument('words_json'); s.add_argument('--pattern', default=None); s.add_argument('--pad', type=float, default=0.04)
    s = sub.add_parser('apply', help='bake the bleeps (or silence) into a copy of the clip')
    s.add_argument('clip'); s.add_argument('out'); s.add_argument('--spans', required=True)
    s.add_argument('--mute', action='store_true'); s.add_argument('--tone', type=int, default=1000)
    s.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    try:
        return {'words': cmd_words, 'find': cmd_find, 'apply': cmd_apply}[a.cmd](a)
    except (ValueError, OSError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f'error: a step failed (exit {e.returncode})', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
