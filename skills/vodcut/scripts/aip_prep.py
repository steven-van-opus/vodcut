#!/usr/bin/env python3
"""Get a finished 9:16 clip ready to open as an AI Producer project (Path A: cut locally, then upload).

This script never talks to AI Producer. It checks the file against AI Producer's source limits, prints the
exact arguments sign_upload needs, and writes a provenance record you keep next to the clip.

Usage:
  python3 -I aip_prep.py check CLIP.mp4 [--max-minutes 30]
  python3 -I aip_prep.py sign-args CLIP.mp4
  python3 -I aip_prep.py record CLIP.mp4 --streamer NAME --platform youtube --source-url URL --start S --end E
                                        [--handle @name] [--title "..."] [--out CLIP.clip.json]

check      container .mp4/.mov/.webm, at most 3584 MiB, at most --max-minutes long (30 for most accounts over
           MCP; the web app guide says 15), portrait 9:16 (AI Producer keeps the source orientation and
           never reframes 16:9), frame rate (exports cap at 30 fps), H.264 video, an audio track (without
           speech there are no captions). Exit 1 on an error, 0 otherwise; warnings never fail.
sign-args  {"filename","content_type","size","sha256"}: pass these to
           mcp__plugin_aip_ai-producer__sign_upload, then PUT the bytes with every header it returns.
record     provenance + credit line for the post (keep it: campaigns and takedown requests ask for it).
"""
import argparse, datetime, hashlib, json, os, subprocess, sys

MAX_BYTES = 3584 * 1024 * 1024
TYPES = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm'}


def sha256_file(path, block=1024 * 1024):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(block)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                          'stream=codec_type,codec_name,width,height,r_frame_rate:format=duration',
                          '-of', 'json', path], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    d = json.loads(out.stdout or '{}')
    v = next((s for s in d.get('streams', []) if s.get('codec_type') == 'video'), None)
    a = next((s for s in d.get('streams', []) if s.get('codec_type') == 'audio'), None)
    fps = None
    if v and v.get('r_frame_rate') not in (None, '0/0'):
        n, _, den = v['r_frame_rate'].partition('/')
        fps = float(n) / float(den or 1)
    dur = d.get('format', {}).get('duration')
    return {'width': v and v.get('width'), 'height': v and v.get('height'), 'fps': fps,
            'vcodec': v and v.get('codec_name'), 'has_audio': a is not None,
            'duration': float(dur) if dur not in (None, 'N/A') else None}


def check(path, max_minutes=30, info=None):
    errors, warnings = [], []
    ext = os.path.splitext(path)[1].lower()
    if ext not in TYPES:
        errors.append(f'{ext or "no extension"}: AI Producer takes .mp4, .mov or .webm')
    if not os.path.exists(path):
        return {'ok': False, 'errors': errors + ['file not found'], 'warnings': warnings, 'facts': {}}
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        errors.append(f'{size / 1048576:.0f} MiB is over the 3584 MiB upload limit: trim or compress')
    info = info if info is not None else probe(path)
    facts = {'size': size}
    if not info:
        errors.append('ffprobe could not read the file')
        return {'ok': False, 'errors': errors, 'warnings': warnings, 'facts': facts}
    facts.update(info)
    dur, w, h, fps = info.get('duration'), info.get('width'), info.get('height'), info.get('fps')
    if dur is not None and dur > max_minutes * 60:
        errors.append(f'{dur / 60:.1f} min is over {max_minutes} min, the source limit for most accounts: cut it shorter')
    elif dur is not None and dur > 15 * 60:
        warnings.append('longer than 15 min: fine over MCP for most accounts, but the web app guide says 15 min')
    if not w or not h:
        errors.append('no video stream')
    else:
        if w > h:
            warnings.append(f'{w}x{h} is landscape: AI Producer keeps the source orientation and will not make it '
                            'vertical. Compose 9:16 first (frame.py compose).')
        elif w == h:
            warnings.append('square source: the project will be 1:1, not 9:16')
        elif abs(w / float(h) - 9 / 16.0) > 0.01:
            warnings.append(f'{w}x{h} is portrait but not 9:16')
        if max(w, h) > 1920 or min(w, h) > 1080:
            warnings.append(f'{w}x{h} is larger than 1080p: exports are at most 1080p, so this only costs upload time')
        elif (w, h) != (1080, 1920) and w < h:
            warnings.append(f'{w}x{h}: 1080x1920 gives the sharpest 1080p export')
    if fps and fps > 30.5:
        warnings.append(f'{fps:.0f} fps: exports cap at 30 fps; compose at 30 to save upload time')
    if info.get('vcodec') and info['vcodec'] not in ('h264', 'hevc', 'vp9', 'av1'):
        warnings.append(f"video codec {info['vcodec']}: H.264 is the safe choice")
    if not info.get('has_audio'):
        warnings.append('no audio track: the project starts as a video without speech (no captions, no music)')
    return {'ok': not errors, 'errors': errors, 'warnings': warnings, 'facts': facts}


def sign_args(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in TYPES:
        raise ValueError(f'{ext or "no extension"}: AI Producer takes .mp4, .mov or .webm')
    return {'filename': os.path.basename(path), 'content_type': TYPES[ext],
            'size': os.path.getsize(path), 'sha256': sha256_file(path)}


def platform_name(platform):
    return {'youtube': 'YouTube', 'twitch': 'Twitch', 'kick': 'Kick', 'tiktok': 'TikTok', 'x': 'X'}.get(
        platform.lower(), platform)


def credit_line(streamer, platform, handle=None):
    """The on-post credit, e.g. '🎥 @IShowSpeed (YouTube)'. Credit is courtesy, not a license."""
    return f'\U0001F3A5 {handle or streamer} ({platform_name(platform)})'


def disclaimer_line(streamer, platform):
    return (f'Footage: {streamer} on {platform_name(platform)}. Not affiliated with or endorsed by {streamer}, '
            'Rockstar Games or Take-Two Interactive.')


def record(path, streamer, platform, source_url, start, end, handle=None, title=None):
    return {
        'clip': os.path.basename(path),
        'sha256': sha256_file(path) if os.path.exists(path) else None,
        'streamer': streamer, 'handle': handle, 'platform': platform,
        'source_url': source_url, 'source_start_s': float(start), 'source_end_s': float(end),
        'title': title,
        'credit': credit_line(streamer, platform, handle),
        'disclaimer': disclaimer_line(streamer, platform),
        'fetched_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'aip': {'project_id': None, 'page_url': None},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('check', help='check a clip against AI Producer source limits')
    s.add_argument('clip'); s.add_argument('--max-minutes', type=float, default=30)
    s = sub.add_parser('sign-args', help='print the sign_upload arguments (filename, content_type, size, sha256)')
    s.add_argument('clip')
    s = sub.add_parser('record', help='write the provenance + credit record for a clip')
    s.add_argument('clip')
    s.add_argument('--streamer', required=True); s.add_argument('--platform', required=True)
    s.add_argument('--source-url', required=True)
    s.add_argument('--start', type=float, required=True); s.add_argument('--end', type=float, required=True)
    s.add_argument('--handle', default=None); s.add_argument('--title', default=None)
    s.add_argument('--out', default=None, help='default: CLIP with .clip.json')
    a = ap.parse_args(argv)
    if a.cmd == 'check':
        r = check(a.clip, a.max_minutes)
        print(json.dumps(r, indent=1))
        return 0 if r['ok'] else 1
    if a.cmd == 'sign-args':
        try:
            print(json.dumps(sign_args(a.clip)))
        except (ValueError, OSError) as e:
            print(f'error: {e}', file=sys.stderr)
            return 2
        return 0
    rec = record(a.clip, a.streamer, a.platform, a.source_url, a.start, a.end, a.handle, a.title)
    out = a.out or os.path.splitext(a.clip)[0] + '.clip.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
    print(json.dumps({'record': out, 'credit': rec['credit']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
