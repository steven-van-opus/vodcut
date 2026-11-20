#!/usr/bin/env python3
"""Rank a scanned stream into 30-second "excitement" windows and distinct moments.

Reads what scan_vod.py writes into a scan folder:
  transcript.jsonl   {"start","end","text"} whisper segments in stream seconds (marker rows are skipped)
  loudness.tsv       per-second RMS / peak dBFS in stream seconds (optional: without it, speech cues rank alone)
Writes into the same folder (or --out-dir):
  windows.tsv        every window, best first: score, start, loudness, cue count, words, review flags, text
  top_windows.md     the best --top windows with their transcript, for a person or an agent to read
  moments.json       the best --moments distinct moments (at least --min-gap seconds apart) with suggested in/out
  moments.md         the same moments, readable

Usage:
  python3 -I rank_windows.py SCAN_DIR [--window 30] [--top 150] [--moments 20] [--min-gap 90]
                                       [--pad-before 6] [--pad-after 8] [--blocklist FILE] [--out-dir DIR]

Score = z-score of the window's 5 loudest seconds (against the whole stream)
        + 0.35 x reaction cues (capped at 12) + 0.01 x words (capped at 150).
Review flags never change the score. They tell whoever picks what to check before cutting:
  profanity  a campaign or platform may want it bleeped
  sexual     sexual content: skip it for brand-safe pages
  spoiler    story-ending talk: Rockstar's policy asks people not to post endings or major story reveals
  blocklist  a line from your own --blocklist file (one regular expression per line) matched
  loop       whisper repeated a line over and over here (it does that over music and crowd noise): listen first

Whisper loops are cleaned before scoring, so a hallucinated line cannot win on repetition: a run of identical
consecutive segments keeps its first --max-run (2), and a line of 4+ words repeated --max-line-repeats (30)
times across the whole stream is dropped everywhere (0 turns that off).
"""
import argparse, json, math, os, re, sys
from collections import Counter

CUES = re.compile(r"\b(chat|bro+|yo+|no way|what the|oh my god|omg|hell no|let'?s go+|bruh|nah+|wait|look|run|stop|help|cops?|police|wanted|crash|dead|died|wasted|busted|sorry|hulk|monster|gun|shoot|car|dog|crazy|insane|why|how|ha(ha)+|aah+|ah+)\b", re.I)
FLAGS = {
    'profanity': re.compile(r"\b(f+u+c+k\w*|motherf\w*|sh+i+t+\w*|bitch\w*|asshole\w*|dick\w*|pussy)\b", re.I),
    'sexual': re.compile(r"\b(strip ?club\w*|stripper\w*|naked|nude\w*|boob\w*|titt(y|ies)|porn\w*|horny|twerk\w*|lap ?dance\w*|sex(y|ual)?)\b", re.I),
    'spoiler': re.compile(r"\b(the ending|final mission|last mission|end credits|credits roll\w*|spoilers?|beat the game|finished the story)\b", re.I),
}


def hms(s):
    s = int(s)
    return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}'


def load_transcript(path):
    segs = []
    if not os.path.exists(path):
        return segs
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get('marker') or not str(d.get('text', '')).strip():
                continue
            segs.append((float(d['start']), float(d.get('end', d['start'])), str(d['text']).strip()))
    segs.sort(key=lambda s: s[0])
    return segs


def _norm(text):
    return re.sub(r'[^a-z0-9 ]+', '', text.lower()).strip()


def clean_loops(segments, max_run=2, max_line_repeats=30, min_words=4):
    """Drop whisper repetition loops. Returns (kept segments, dropped count, times where a loop was cut)."""
    counts = Counter(_norm(t) for _, _, t in segments)
    kept, dropped, loops = [], 0, []
    prev, run = None, 0
    for st, en, tx in segments:
        n = _norm(tx)
        if max_line_repeats and counts[n] >= max_line_repeats and len(n.split()) >= min_words:
            dropped += 1
            loops.append(st)
            continue
        run = run + 1 if n == prev else 1
        prev = n
        if run > max_run:
            dropped += 1
            if run == max_run + 2:
                loops.append(st)
            continue
        kept.append((st, en, tx))
    return kept, dropped, loops


def load_loudness(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as f:
        next(f, None)  # header
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 2:
                continue
            try:
                rows.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return rows


def load_blocklist(path):
    if not path:
        return []
    pats = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                pats.append(re.compile(line, re.I))
    return pats


def review_flags(text, blocklist=()):
    out = [name for name, rx in FLAGS.items() if rx.search(text)]
    if any(rx.search(text) for rx in blocklist):
        out.append('blocklist')
    return out


def rank(segments, loudness, window=30, total=None, blocklist=(), loops=()):
    """Return one dict per window, best score first. `loops` are times where clean_loops cut a whisper loop."""
    if total is None:
        ends = [s[1] for s in segments] + [t for t, _ in loudness]
        total = max(ends) if ends else 0.0
    nw = int(total // window) + 1
    loud = [[] for _ in range(nw)]
    for t, r in loudness:
        k = int(t // window)
        if 0 <= k < nw:
            loud[k].append(r)
    looped = set()
    for t in loops:
        k = int(t // window)
        if 0 <= k < nw:
            looped.add(k)
    text = [[] for _ in range(nw)]
    for st, _, tx in segments:
        k = int(st // window)
        if 0 <= k < nw:
            text[k].append(tx)
    feats = []
    for k in range(nw):
        top = sorted(loud[k])[-5:] if loud[k] else [-90.0]
        feats.append((sum(top) / len(top), ' '.join(text[k])))
    vals = [lv for lv, _ in feats if lv > -80]
    if vals:
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
    rows = []
    for k, (lv, tx) in enumerate(feats):
        z = (lv - mu) / sd if vals else 0.0
        cues = len(CUES.findall(tx))
        words = len(tx.split())
        score = z + 0.35 * min(cues, 12) + 0.01 * min(words, 150)
        flags = review_flags(tx, blocklist) + (['loop'] if k in looped else [])
        rows.append({'score': round(score, 3), 'start': k * window, 'end': min((k + 1) * window, total),
                     'loud_top5_db': round(lv, 1), 'cues': cues, 'words': words,
                     'flags': flags, 'text': tx, 'index': k})
    rows.sort(key=lambda r: (-r['score'], r['start']))
    return rows


def pick_moments(rows, n=20, min_gap=90, window=30, pad_before=6, pad_after=8, total=None):
    """Greedy non-maximum suppression: the best window wins, nothing within min_gap seconds of it is kept."""
    by_index = {r['index']: r for r in rows}
    if total is None:
        total = max((r['end'] for r in rows), default=0)
    picked = []
    for r in rows:
        if r['words'] == 0 and (r['loud_top5_db'] <= -80 or r['score'] < 1.5):
            continue  # nobody talking: only a very loud window (z >= 1.5) can still be a moment
        if any(abs(r['start'] - p['start']) < min_gap for p in picked):
            continue
        picked.append(r)
        if len(picked) >= n:
            break
    out = []
    for i, r in enumerate(picked, 1):
        prev, nxt = by_index.get(r['index'] - 1), by_index.get(r['index'] + 1)
        cut_in = max(0.0, r['start'] - pad_before)
        cut_out = min(float(total), r['start'] + window + pad_after) if total else r['start'] + window + pad_after
        out.append({
            'rank': i, 'score': r['score'],
            'window_start': r['start'], 'window_end': r['start'] + window,
            'in': round(cut_in, 2), 'out': round(cut_out, 2),
            'in_hms': hms(cut_in), 'out_hms': hms(cut_out),
            'loud_top5_db': r['loud_top5_db'], 'cues': r['cues'], 'words': r['words'], 'flags': r['flags'],
            'text': r['text'],
            'text_before': (prev['text'][-240:] if prev else ''),
            'text_after': (nxt['text'][:240] if nxt else ''),
        })
    return out


def write_outputs(rows, moments, out_dir, top=150):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'windows.tsv'), 'w', encoding='utf-8') as w:
        w.write('score\tstart_s\tstart_hms\tloud_top5_db\tcues\twords\tflags\ttext\n')
        for r in rows:
            tx = r['text'].replace('\t', ' ')[:400]
            w.write(f"{r['score']:.2f}\t{r['start']}\t{hms(r['start'])}\t{r['loud_top5_db']:.1f}\t{r['cues']}\t"
                    f"{r['words']}\t{','.join(r['flags'])}\t{tx}\n")
    with open(os.path.join(out_dir, 'top_windows.md'), 'w', encoding='utf-8') as w:
        w.write(f'# Top {min(top, len(rows))} windows by excitement score\n\n')
        for r in rows[:top]:
            fl = f", review: {', '.join(r['flags'])}" if r['flags'] else ''
            w.write(f"## {hms(r['start'])} (score {r['score']:.2f}, loud {r['loud_top5_db']:.1f} dB, cues {r['cues']}{fl})\n"
                    f"{r['text']}\n\n")
    with open(os.path.join(out_dir, 'moments.json'), 'w', encoding='utf-8') as w:
        json.dump({'moments': moments}, w, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, 'moments.md'), 'w', encoding='utf-8') as w:
        w.write(f'# {len(moments)} distinct moments, best first\n\n')
        w.write('Suggested cut = window start - pad-before .. window end + pad-after. Verify every one on a frame sheet.\n\n')
        for m in moments:
            fl = f" | review: {', '.join(m['flags'])}" if m['flags'] else ''
            w.write(f"## #{m['rank']}  {m['in_hms']} -> {m['out_hms']}  (score {m['score']:.2f}, cues {m['cues']}{fl})\n")
            if m['text_before']:
                w.write(f"> before: ...{m['text_before']}\n\n")
            w.write(f"{m['text']}\n\n")
            if m['text_after']:
                w.write(f"> after: {m['text_after']}...\n\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scan_dir', help='folder holding transcript.jsonl (and loudness.tsv)')
    ap.add_argument('--window', type=float, default=30, help='window length in seconds (default 30)')
    ap.add_argument('--top', type=int, default=150, help='windows listed in top_windows.md (default 150)')
    ap.add_argument('--moments', type=int, default=20, help='distinct moments to keep (default 20)')
    ap.add_argument('--min-gap', type=float, default=90, help='seconds between two kept moments (default 90)')
    ap.add_argument('--pad-before', type=float, default=6, help='seconds of lead-in before a window (default 6)')
    ap.add_argument('--pad-after', type=float, default=8, help='seconds after a window (default 8)')
    ap.add_argument('--total', type=float, default=None, help='stream length in seconds (default: from the data)')
    ap.add_argument('--blocklist', default=None, help='file with one regular expression per line to flag')
    ap.add_argument('--max-run', type=int, default=2, help='identical consecutive segments kept (default 2)')
    ap.add_argument('--max-line-repeats', type=int, default=30,
                    help='drop a 4+ word line repeated this often across the stream (default 30, 0 = off)')
    ap.add_argument('--out-dir', default=None, help='write outputs here instead of SCAN_DIR')
    a = ap.parse_args(argv)
    segs = load_transcript(os.path.join(a.scan_dir, 'transcript.jsonl'))
    loud = load_loudness(os.path.join(a.scan_dir, 'loudness.tsv'))
    if not segs and not loud:
        print(f'nothing to rank: no transcript.jsonl or loudness.tsv in {a.scan_dir}', file=sys.stderr)
        return 1
    segs, dropped, loops = clean_loops(segs, a.max_run, a.max_line_repeats)
    rows = rank(segs, loud, a.window, a.total, load_blocklist(a.blocklist), loops)
    moments = pick_moments(rows, a.moments, a.min_gap, a.window, a.pad_before, a.pad_after, a.total)
    out = a.out_dir or a.scan_dir
    write_outputs(rows, moments, out, a.top)
    hours = (a.total or max((r['end'] for r in rows), default=0)) / 3600
    print(json.dumps({'ok': True, 'windows': len(rows), 'moments': len(moments), 'hours': round(hours, 2),
                      'segments': len(segs), 'loop_segments_dropped': dropped, 'loudness_seconds': len(loud),
                      'out_dir': out}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
