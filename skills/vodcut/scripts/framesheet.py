#!/usr/bin/env python3
"""Make a frame sheet: evenly spaced frames of a clip tiled into one image, to verify a moment by eye.

A transcript says what was said; the sheet shows what happened on screen (the chase, the crash, the face
the streamer pulled) and whether anything in the picture rules the clip out. Read the sheet before
cutting the clip in full quality.

Usage:
  python3 -I framesheet.py IN.mp4 OUT.jpg [--cols 4] [--rows 3] [--width 1600] [--start S] [--end E] [--dry-run]

Prints JSON with the time (seconds into IN) of every tile, left to right, top to bottom. ffmpeg builds the
sheet with its tile filter; nothing is drawn on the frames, so the JSON is the legend.
"""
import argparse, json, shutil, subprocess, sys


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise ValueError(f'could not read the duration of {path}')


def tile_times(start, end, n):
    """Times the fps filter keeps when it samples n frames across [start, end)."""
    span = end - start
    return [round(start + span * k / n, 3) for k in range(n)]


def sheet_cmd(inp, out, start, end, cols=4, rows=3, width=1600):
    n = cols * rows
    span = end - start
    if span <= 0:
        raise ValueError('end must be after start')
    tile_w = max(64, int(width // cols) // 2 * 2)
    vf = f'fps={n}/{span:.3f},scale={tile_w}:-2:flags=bicubic,tile={cols}x{rows}:padding=4:margin=4:color=0x0B1020'
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{start:.3f}', '-t', f'{span:.3f}', '-i', inp,
            '-vf', vf, '-frames:v', '1', '-q:v', '3', out]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input'); ap.add_argument('output')
    ap.add_argument('--cols', type=int, default=4); ap.add_argument('--rows', type=int, default=3)
    ap.add_argument('--width', type=int, default=1600, help='sheet width in px (default 1600)')
    ap.add_argument('--start', type=float, default=0.0, help='seconds into IN (default 0)')
    ap.add_argument('--end', type=float, default=None, help='seconds into IN (default: the end of the clip)')
    ap.add_argument('--dry-run', action='store_true', help='print the ffmpeg command, run nothing')
    a = ap.parse_args(argv)
    if a.cols < 1 or a.rows < 1:
        print('error: --cols and --rows must be at least 1', file=sys.stderr)
        return 2
    try:
        if not a.dry_run and not shutil.which('ffmpeg'):
            print('ffmpeg is required (brew install ffmpeg)', file=sys.stderr)
            return 1
        end = a.end if a.end is not None else (duration(a.input) if not a.dry_run else a.start + 30)
        cmd = sheet_cmd(a.input, a.output, a.start, end, a.cols, a.rows, a.width)
        times = tile_times(a.start, end, a.cols * a.rows)
        if a.dry_run:
            print(json.dumps({'cmd': cmd, 'tiles': times}))
            return 0
        subprocess.run(cmd, check=True)
    except ValueError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f'ffmpeg failed (exit {e.returncode})', file=sys.stderr)
        return 1
    legend = [{'tile': i + 1, 'row': i // a.cols + 1, 'col': i % a.cols + 1, 't': t} for i, t in enumerate(times)]
    print(json.dumps({'sheet': a.output, 'cols': a.cols, 'rows': a.rows, 'tiles': legend}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
