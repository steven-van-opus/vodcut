"""Shared helpers for the test suite (stdlib only). Run the suite from the repo root:

    python3 -I -m unittest discover -s tests -v
"""
import importlib.util, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, 'skills', 'vodcut', 'scripts')
SKILLS = os.path.join(ROOT, 'skills')


def load(name):
    """Import scripts/<name>.py as a module without touching sys.path for the caller."""
    path = os.path.join(SCRIPTS, name + '.py')
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)  # scan_vod imports rank_windows, as it does when run as a script
    spec = importlib.util.spec_from_file_location(f'vodcut_{name}', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def have(*tools):
    return all(shutil.which(t) for t in tools)


def run_script(name, *args, timeout=300):
    """Run scripts/<name> the way the skills do (python3 -I) and return the CompletedProcess."""
    return subprocess.run([sys.executable, '-I', os.path.join(SCRIPTS, name)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=timeout)


def make_video(path, w=1920, h=1080, seconds=2.0, fps=30, audio=True):
    """A small synthetic test clip (ffmpeg lavfi testsrc2 + a sine tone)."""
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
           f'testsrc2=size={w}x{h}:rate={fps}:duration={seconds}']
    if audio:
        cmd += ['-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=48000:duration={seconds}']
    cmd += ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p']
    if audio:
        cmd += ['-c:a', 'aac', '-shortest']
    cmd.append(path)
    subprocess.run(cmd, check=True)
    return path


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height,r_frame_rate:format=duration',
                          '-of', 'json', path], capture_output=True, text=True, check=True).stdout
    import json
    return json.loads(out)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()
