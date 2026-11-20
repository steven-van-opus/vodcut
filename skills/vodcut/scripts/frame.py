#!/usr/bin/env python3
"""Compose a 1080x1920 (9:16) clip from a 16:9 stream recording, with ffmpeg.

AI Producer keeps a source's orientation: it never reframes 16:9 into 9:16. So the vertical frame
is built here, on your machine, before the clip is uploaded.

Layouts:
  stack  facecam band on top, gameplay band below (default). The split follows the facecam's shape.
  inset  gameplay fills the frame; the facecam rides in a bordered box near the top.
  full   gameplay only (centre crop): for moments where the reaction is heard, not seen.

Usage:
  python3 -I frame.py probe IN.mp4
  python3 -I frame.py grid IN.mp4 OUT.png [--at 5] [--zoom X,Y,W,H]
  python3 -I frame.py plan IN.mp4 --facecam X,Y,W,H [layout options]
  python3 -I frame.py still IN.mp4 OUT.png --facecam X,Y,W,H [--at 5] [layout options]
  python3 -I frame.py compose IN.mp4 OUT.mp4 --facecam X,Y,W,H [--start S] [--end E] [layout options] [--dry-run]

Rects are source pixels: X,Y of the top-left corner, then width and height. Read them off `grid`
(yellow lines every 120 source px from the top-left corner, faint lines every 40 px; with --zoom,
every 50 px and 10 px inside the zoomed region), then check the result with `still` before `compose`.

Layout options:
  --layout stack|inset|full   default stack
  --gameplay X,Y,W,H          where gameplay may be taken from (default: the whole frame). Use it to keep
                              stream overlays (chat, alerts, logos, counters) out of the crop.
  --focus-x PX                horizontal centre of the gameplay crop (default: centre of --gameplay; the
                              crop slides off the facecam by itself when the two would overlap)
  --top-ratio R               stack: facecam band height / 1920 (default: from the facecam's shape, 0.28-0.50)
  --divider PX                stack: dark seam between the bands (default 0)
  --fps N                     output frame rate (default 30: AI Producer exports at most 30 fps)
  --crf N / --preset P        x264 quality (default 18 / medium)
"""
import argparse, json, math, os, shutil, subprocess, sys
from collections import namedtuple

OUT_W, OUT_H = 1080, 1920
Rect = namedtuple('Rect', 'x y w h')
INK = '0x0B1020'


def even_floor(v):
    return int(math.floor(v / 2.0)) * 2


def even_ceil(v):
    return int(math.ceil(v / 2.0)) * 2


def even_round(v):
    return int(round(v / 2.0)) * 2


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def parse_rect(text):
    try:
        parts = [int(round(float(p))) for p in str(text).replace(' ', '').split(',')]
    except ValueError:
        raise ValueError(f'bad rect {text!r}: expected X,Y,W,H in pixels')
    if len(parts) != 4:
        raise ValueError(f'bad rect {text!r}: expected 4 numbers X,Y,W,H')
    x, y, w, h = parts
    if w < 16 or h < 16 or x < 0 or y < 0:
        raise ValueError(f'bad rect {text!r}: X,Y must be >= 0 and W,H at least 16 px')
    return Rect(x, y, w, h)


def check_inside(rect, src_w, src_h, name):
    if rect.x + rect.w > src_w or rect.y + rect.h > src_h:
        raise ValueError(f'{name} {tuple(rect)} reaches outside the {src_w}x{src_h} source')


def overlaps(a, b):
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def cover_crop(region, out_w, out_h, focus_x=None, focus_y=None, bias_y=0.5):
    """Largest even-sized sub-rect of `region` with the aspect out_w:out_h, centred on the focus point
    (clamped so it stays inside the region). Scaling it to out_w x out_h then fills the band without bars."""
    target = out_w / float(out_h)
    if region.w / float(region.h) > target:   # region is wider than the band: keep full height
        ch = region.h
        cw = ch * target
    else:                                       # region is taller: keep full width
        cw = region.w
        ch = cw / target
    cw, ch = max(2, even_floor(cw)), max(2, even_floor(ch))
    fx = region.x + region.w / 2.0 if focus_x is None else float(focus_x)
    fy = region.y + region.h * bias_y if focus_y is None else float(focus_y)
    x = clamp(even_round(fx - cw / 2.0), even_ceil(region.x), even_floor(region.x + region.w - cw))
    y = clamp(even_round(fy - ch / 2.0), even_ceil(region.y), even_floor(region.y + region.h - ch))
    return Rect(int(x), int(y), int(cw), int(ch))


def slide_off(region, crop, face):
    """Move a gameplay crop sideways so it does not show the facecam a second time."""
    if face is None or not overlaps(crop, face):
        return crop, None
    options = []
    right = even_ceil(face.x + face.w)
    if right + crop.w <= region.x + region.w:
        options.append(right)
    left = even_floor(face.x - crop.w)
    if left >= region.x:
        options.append(left)
    if not options:
        return crop, 'gameplay crop still shows the facecam: pass --gameplay or --focus-x to choose the area'
    best = min(options, key=lambda x: abs(x - crop.x))
    side = 'right' if best >= face.x else 'left'
    return crop._replace(x=int(best)), f'gameplay crop slid {side} of the facecam'


def plan(src_w, src_h, facecam=None, layout='stack', gameplay=None, focus_x=None, top_ratio=None, divider=0):
    """Geometry for one layout. Pure: no ffmpeg involved, so it is easy to test and to print."""
    if layout not in ('stack', 'inset', 'full'):
        raise ValueError(f'unknown layout {layout!r}')
    if layout in ('stack', 'inset') and facecam is None:
        raise ValueError(f'layout {layout} needs --facecam X,Y,W,H (or use --layout full)')
    region = gameplay or Rect(0, 0, src_w, src_h)
    check_inside(region, src_w, src_h, 'gameplay')
    if facecam is not None:
        check_inside(facecam, src_w, src_h, 'facecam')
    notes = []
    p = {'layout': layout, 'source': [src_w, src_h], 'out': [OUT_W, OUT_H],
         'facecam': list(facecam) if facecam else None, 'gameplay_region': list(region)}
    if layout == 'stack':
        if top_ratio is None:
            top_h = clamp(OUT_W * facecam.h / float(facecam.w), 0.28 * OUT_H, 0.50 * OUT_H)
        else:
            if not 0.15 <= top_ratio <= 0.6:
                raise ValueError('--top-ratio must be between 0.15 and 0.6')
            top_h = top_ratio * OUT_H
        top_h = even_round(top_h)
        bot_h = OUT_H - top_h
        face_crop = cover_crop(facecam, OUT_W, top_h, bias_y=0.45)
        game_crop = cover_crop(region, OUT_W, bot_h, focus_x=focus_x)
        if focus_x is None:
            game_crop, note = slide_off(region, game_crop, facecam)
            if note:
                notes.append(note)
        p.update({'top': {'crop': list(face_crop), 'size': [OUT_W, top_h]},
                  'bottom': {'crop': list(game_crop), 'size': [OUT_W, bot_h]},
                  'seam_y': top_h, 'divider': int(divider)})
    elif layout == 'inset':
        game_crop = cover_crop(region, OUT_W, OUT_H, focus_x=focus_x)
        if focus_x is None:
            game_crop, note = slide_off(region, game_crop, facecam)
            if note:
                notes.append(note)
        box_w = even_round(OUT_W * 0.62)
        face_crop = Rect(even_ceil(facecam.x), even_ceil(facecam.y), even_floor(facecam.w), even_floor(facecam.h))
        box_h = even_round(box_w * face_crop.h / float(face_crop.w))
        border = 8
        p.update({'background': {'crop': list(game_crop), 'size': [OUT_W, OUT_H]},
                  'box': {'crop': list(face_crop), 'size': [box_w, box_h], 'border': border,
                          'x': (OUT_W - box_w) // 2 - border, 'y': even_round(OUT_H * 0.10)},
                  'seam_y': even_round(OUT_H * 0.10) + box_h + 2 * border})
    else:
        game_crop = cover_crop(region, OUT_W, OUT_H, focus_x=focus_x)
        if facecam is not None and focus_x is None:
            game_crop, note = slide_off(region, game_crop, facecam)
            if note:
                notes.append(note)
        p.update({'background': {'crop': list(game_crop), 'size': [OUT_W, OUT_H]}, 'seam_y': None})
    p['notes'] = notes
    return p


def _crop(r):
    x, y, w, h = r
    return f'crop={w}:{h}:{x}:{y}'


def filtergraph(p, fps=30, still=False):
    """The ffmpeg -filter_complex string for a plan; the output pad is [v]."""
    tail = '' if still else f',fps={fps},format=yuv420p'
    if p['layout'] == 'stack':
        tw, th = p['top']['size']
        bw, bh = p['bottom']['size']
        g = (f"[0:v]split=2[fa][ga];"
             f"[fa]{_crop(p['top']['crop'])},scale={tw}:{th}:flags=lanczos,setsar=1[top];"
             f"[ga]{_crop(p['bottom']['crop'])},scale={bw}:{bh}:flags=lanczos,setsar=1[bot];"
             f"[top][bot]vstack=inputs=2")
        d = p.get('divider') or 0
        if d:
            g += f",drawbox=x=0:y={th - d // 2}:w={OUT_W}:h={d}:color={INK}:t=fill"
        return g + tail + '[v]'
    if p['layout'] == 'inset':
        b = p['box']
        bw, bh = b['size']
        pad = b['border']
        return (f"[0:v]split=2[fa][ga];"
                f"[ga]{_crop(p['background']['crop'])},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[bg];"
                f"[fa]{_crop(b['crop'])},scale={bw}:{bh}:flags=lanczos,setsar=1,"
                f"pad={bw + 2 * pad}:{bh + 2 * pad}:{pad}:{pad}:color={INK}[pip];"
                f"[bg][pip]overlay=x={b['x']}:y={b['y']}" + tail + '[v]')
    return f"[0:v]{_crop(p['background']['crop'])},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1" + tail + '[v]'


def compose_cmd(inp, out, p, start=None, end=None, fps=30, crf=18, preset='medium', audio=True):
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y']
    if start is not None:
        cmd += ['-ss', f'{float(start):.3f}']
    if end is not None:
        if start is not None and float(end) <= float(start):
            raise ValueError('--end must be after --start')
        cmd += ['-t', f'{float(end) - float(start or 0):.3f}']
    cmd += ['-i', inp, '-filter_complex', filtergraph(p, fps), '-map', '[v]']
    if audio:
        cmd += ['-map', '0:a:0', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2']
    cmd += ['-c:v', 'libx264', '-preset', preset, '-crf', str(crf), '-profile:v', 'high', '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart', out]
    return cmd


def still_cmd(inp, out_png, p, at=5.0):
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{float(at):.3f}', '-i', inp,
            '-filter_complex', filtergraph(p, still=True), '-map', '[v]', '-frames:v', '1', out_png]


def grid_cmd(inp, out_png, at=5.0, zoom=None):
    """A frame with a coordinate grid. Lines sit on multiples of the step in SOURCE pixels."""
    if zoom is None:
        z, minor, major, ox, oy, pre = 1, 40, 120, 0, 0, ''
    else:
        z = max(1, min(4, int(1200 // max(zoom.w, 1)) or 1))
        minor, major = 10, 50
        pre = f'{_crop(zoom)},scale=iw*{z}:ih*{z}:flags=neighbor,'
        ox, oy = ((-zoom.x) % major) * z, ((-zoom.y) % major) * z
        oxm, oym = ((-zoom.x) % minor) * z, ((-zoom.y) % minor) * z
    if zoom is None:
        oxm, oym = 0, 0
    vf = (pre + f'drawgrid=x={oxm}:y={oym}:w={minor * z}:h={minor * z}:t=1:c=white@0.22,'
          f'drawgrid=x={ox}:y={oy}:w={major * z}:h={major * z}:t=2:c=yellow@0.75')
    info = {'image': out_png, 'origin': [zoom.x, zoom.y] if zoom else [0, 0], 'scale': z,
            'minor_px': minor, 'major_px': major,
            'how_to_read': f'yellow lines every {major} source px, faint lines every {minor} px, counted from the '
                           f'source top-left (0,0); with --zoom the image starts at the zoom origin and is scaled x{z}'}
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{float(at):.3f}', '-i', inp,
            '-frames:v', '1', '-vf', vf, out_png], info


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                          'stream=index,codec_type,codec_name,width,height,r_frame_rate:format=duration,size',
                          '-of', 'json', path], capture_output=True, text=True)
    if out.returncode != 0:
        raise ValueError(f'ffprobe could not read {path}: {out.stderr.strip()[:200]}')
    d = json.loads(out.stdout or '{}')
    v = next((s for s in d.get('streams', []) if s.get('codec_type') == 'video'), None)
    a = next((s for s in d.get('streams', []) if s.get('codec_type') == 'audio'), None)
    fps = None
    if v and v.get('r_frame_rate') and v['r_frame_rate'] != '0/0':
        n, _, den = v['r_frame_rate'].partition('/')
        fps = round(float(n) / float(den or 1), 3)
    fmt = d.get('format', {})
    return {'path': path, 'width': v.get('width') if v else None, 'height': v.get('height') if v else None,
            'fps': fps, 'vcodec': v.get('codec_name') if v else None, 'acodec': a.get('codec_name') if a else None,
            'has_audio': a is not None, 'duration': float(fmt['duration']) if fmt.get('duration') else None,
            'size': int(fmt['size']) if fmt.get('size') else None}


def _layout_args(ap):
    ap.add_argument('--facecam', default=None, help='facecam rect X,Y,W,H in source pixels')
    ap.add_argument('--layout', default='stack', choices=['stack', 'inset', 'full'])
    ap.add_argument('--gameplay', default=None, help='gameplay region X,Y,W,H (default: whole frame)')
    ap.add_argument('--focus-x', type=float, default=None, help='centre x of the gameplay crop, source px')
    ap.add_argument('--top-ratio', type=float, default=None, help='stack: facecam band height / 1920')
    ap.add_argument('--divider', type=int, default=0, help='stack: seam thickness in px (default 0)')


def _plan_from(a, info):
    return plan(info['width'], info['height'], parse_rect(a.facecam) if a.facecam else None, a.layout,
                parse_rect(a.gameplay) if a.gameplay else None, a.focus_x, a.top_ratio, a.divider)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('probe', help='size, fps, duration and audio of a video, as JSON')
    s.add_argument('input')
    s = sub.add_parser('grid', help='one frame with a coordinate grid, to read the facecam rect')
    s.add_argument('input'); s.add_argument('output')
    s.add_argument('--at', type=float, default=5.0)
    s.add_argument('--zoom', default=None, help='X,Y,W,H region to zoom into (finer grid)')
    s = sub.add_parser('plan', help='print the layout geometry as JSON')
    s.add_argument('input'); _layout_args(s)
    s = sub.add_parser('still', help='one composed 1080x1920 frame as PNG, to check the framing')
    s.add_argument('input'); s.add_argument('output')
    s.add_argument('--at', type=float, default=5.0); _layout_args(s)
    s = sub.add_parser('compose', help='write the 1080x1920 clip')
    s.add_argument('input'); s.add_argument('output'); _layout_args(s)
    s.add_argument('--start', type=float, default=None); s.add_argument('--end', type=float, default=None)
    s.add_argument('--fps', type=int, default=30); s.add_argument('--crf', type=int, default=18)
    s.add_argument('--preset', default='medium')
    s.add_argument('--dry-run', action='store_true', help='print the ffmpeg command and the plan, run nothing')
    a = ap.parse_args(argv)
    try:
        if a.cmd != 'compose' or not a.dry_run:
            if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
                print('ffmpeg and ffprobe are required (brew install ffmpeg)', file=sys.stderr)
                return 1
        if a.cmd == 'probe':
            print(json.dumps(probe(a.input), indent=1))
            return 0
        if a.cmd == 'grid':
            cmd, info = grid_cmd(a.input, a.output, a.at, parse_rect(a.zoom) if a.zoom else None)
            subprocess.run(cmd, check=True)
            print(json.dumps(info, indent=1))
            return 0
        info = probe(a.input) if (a.cmd != 'compose' or not a.dry_run or os.path.exists(a.input)) else None
        if info is None:
            info = {'width': 1920, 'height': 1080, 'has_audio': True}
        p = _plan_from(a, info)
        if a.cmd == 'plan':
            print(json.dumps(p, indent=1))
            return 0
        if a.cmd == 'still':
            subprocess.run(still_cmd(a.input, a.output, p, a.at), check=True)
            print(json.dumps({'still': a.output, 'plan': p}, indent=1))
            return 0
        cmd = compose_cmd(a.input, a.output, p, a.start, a.end, a.fps, a.crf, a.preset, audio=info.get('has_audio', True))
        if a.dry_run:
            print(json.dumps({'cmd': cmd, 'plan': p}, indent=1))
            return 0
        subprocess.run(cmd, check=True)
        out = probe(a.output)
        print(json.dumps({'output': a.output, 'width': out['width'], 'height': out['height'], 'fps': out['fps'],
                          'duration': out['duration'], 'has_audio': out['has_audio'], 'notes': p['notes']}, indent=1))
        return 0
    except ValueError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f'ffmpeg failed (exit {e.returncode})', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
