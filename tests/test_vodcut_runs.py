"""vodcut.py, ytfrag.py and scan_frags.py against fakes, all offline.

- Which downloader a YouTube VOD goes through: ytfrag.py only while YouTube still serves it as 2-second fragments,
  yt-dlp once it is processed (the protocol is asked again before verify and cut, because it changes).
- Re-cutting a moment with new times composes the new window; reversed times are refused.
- cut passes --top-ratio, --focus-x and --divider to frame.py compose.
- report only quotes a wall time a single uninterrupted run produced.
- ytfrag.py stops on a processed VOD before the first request, and after one round if a "fragment" is a whole file;
  its track output appears only once assembled, and a failed assembly keeps the fragments.
- scan_frags.py never writes a chunk twice when the track is assembled while whisper runs.
A fake `yt-dlp` (and `whisper-cli`) on PATH stands in for the network and the model.
"""
import http.server, json, os, shutil, subprocess, sys, tempfile, threading, time, unittest
from unittest import mock

import util

yf = util.load('ytfrag')

FAKE_YTDLP = r'''#!{python} -I
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as f:
    f.write(json.dumps(args) + '\n')
def opt(name):
    return args[args.index(name) + 1] if name in args else None
if opt('--print') == 'protocol':
    print(os.environ.get('FAKE_PROTOCOL', 'https'))
    sys.exit(0)
if opt('--print') == 'duration':
    print(os.environ.get('FAKE_DURATION', '60'))
    sys.exit(0)
src, out, sec = os.environ['FAKE_SOURCE'], opt('-o'), opt('--download-sections')
if sec:  # what yt-dlp does with --download-sections: just that window of the source
    a, b = (float(x) for x in sec.lstrip('*').split('-'))
    cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-ss', str(a), '-t', str(b - a), '-i', src,
           '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', out]
else:
    cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-i', src, '-vn', '-c:a', 'aac', out.replace('%(ext)s', 'm4a')]
sys.exit(subprocess.run(cmd).returncode)
'''

FAKE_WHISPER = r'''#!{python} -I
import json, os, sys
args = sys.argv[1:]
base = args[args.index('-of') + 1]
with open(base + '.json', 'w') as f:
    json.dump({'transcription': [{'text': ' hello there', 'offsets': {'from': 1000, 'to': 2000}}]}, f)
if os.environ.get('FAKE_ASSEMBLE_DURING_WHISPER'):  # ytfrag.py finishes the track while whisper runs
    open(os.environ['FAKE_ASSEMBLE_DURING_WHISPER'], 'wb').close()
'''

YT = {'kind': 'youtube', 'id': 'qSyo5d-dVzA', 'url': 'https://www.youtube.com/watch?v=qSyo5d-dVzA'}
MOMENT = {'rank': 1, 'score': 1.0, 'in': 10.0, 'out': 40.0, 'in_hms': '0:00:10', 'out_hms': '0:00:40', 'flags': [],
          'text': 'test', 'window_start': 10, 'window_end': 40, 'cues': 0, 'words': 1, 'loud_top5_db': -20,
          'text_before': '', 'text_after': ''}


def fmp4_audio(path, seconds=4):
    """A fragmented MP4 audio track: cut into byte pieces and joined again, it reads like YouTube's fragments."""
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    f'sine=frequency=440:sample_rate=48000:duration={seconds}', '-c:a', 'aac',
                    '-movflags', 'frag_keyframe+empty_moov+default_base_moof', '-f', 'mp4', path], check=True)
    with open(path, 'rb') as f:
        return f.read()


def write_pieces(data, folder, n):
    os.makedirs(folder, exist_ok=True)
    step = -(-len(data) // n)
    for i in range(n):
        with open(os.path.join(folder, f'{i:07d}'), 'wb') as f:
            f.write(data[i * step:(i + 1) * step])


class FakeTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, 'bin')
        os.makedirs(self.bin)
        for name, body in (('yt-dlp', FAKE_YTDLP), ('whisper-cli', FAKE_WHISPER)):
            p = os.path.join(self.bin, name)
            with open(p, 'w') as f:
                f.write(body.replace('{python}', sys.executable))
            os.chmod(p, 0o755)
        self.log = os.path.join(self.tmp, 'calls.jsonl')
        self.env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ.get('PATH', ''), FAKE_LOG=self.log)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, name, *args, **env):
        return subprocess.run([sys.executable, '-I', os.path.join(util.SCRIPTS, name)] + [str(a) for a in args],
                              capture_output=True, text=True, timeout=300, env=dict(self.env, **env))

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def make_run(self, source, moments=(MOMENT,), name='run'):
        out = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(out, 'scan'))
        with open(os.path.join(out, 'vodcut.json'), 'w') as f:
            json.dump({'source': source, 'phases_s': {}}, f)
        with open(os.path.join(out, 'scan', 'moments.json'), 'w') as f:
            json.dump({'moments': list(moments)}, f)
        return out


@unittest.skipIf(' ' in sys.executable, 'the fake tools need a shebang-safe python path')
class Routing(FakeTools):
    def test_scan_asks_how_youtube_serves_the_vod(self):
        r = self.run_script('vodcut.py', 'scan', 'qSyo5d-dVzA', os.path.join(self.tmp, 'a'), '--dry-run', FAKE_PROTOCOL='https')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('processed VOD (protocol https)', r.stdout)
        self.assertIn('$ yt-dlp --no-warnings --no-playlist -f 139/ba/w -o', r.stdout)
        self.assertNotIn('ytfrag.py', r.stdout)
        r = self.run_script('vodcut.py', 'scan', 'qSyo5d-dVzA', os.path.join(self.tmp, 'b'), '--dry-run',
                            FAKE_PROTOCOL='http_dash_segments')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('ytfrag.py track qSyo5d-dVzA 139', r.stdout)
        self.assertEqual([c for c in self.calls() if '--print' in c][0][-3:],
                         ['--print', 'protocol', 'https://www.youtube.com/watch?v=qSyo5d-dVzA'])

    def test_verify_and_cut_ask_again(self):
        # scanned while still fragments; processed by the time the moments are verified and cut
        out = self.make_run(dict(YT, protocol='http_dash_segments'))
        r = self.run_script('vodcut.py', 'verify', out, '--top', '1', '--dry-run', FAKE_PROTOCOL='https')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('-f 134+139/bv*[height<=360]+ba/b[height<=360]/w --download-sections *10.00-40.00', r.stdout)
        self.assertNotIn('ytfrag.py', r.stdout)
        r = self.run_script('vodcut.py', 'cut', out, '--pick', '1', '--facecam', '0,462,388,240', '--dry-run',
                            FAKE_PROTOCOL='https')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('-f 299+140/bv*[height<=1080]+ba/b --download-sections *10.00-40.00', r.stdout)
        self.assertIn('m01_src_10.00-40.00.mp4', r.stdout)
        r = self.run_script('vodcut.py', 'verify', out, '--top', '1', '--dry-run', FAKE_PROTOCOL='http_dash_segments')
        self.assertIn('ytfrag.py window qSyo5d-dVzA 10.00 40.00', r.stdout)
        self.assertNotIn('--download-sections', r.stdout)

    def test_cut_passes_the_layout_flags_to_compose(self):
        for source in ({'kind': 'file', 'path': os.path.join(self.tmp, 'stream.mp4')}, YT):
            out = self.make_run(source, name=source['kind'])
            r = self.run_script('vodcut.py', 'cut', out, '--pick', '1', '--facecam', '0,462,388,240', '--top-ratio', '0.35',
                                '--focus-x', '1200', '--divider', '6', '--gameplay', '0,90,1920,990', '--dry-run')
            self.assertEqual(r.returncode, 0, r.stderr)
            compose = next(l for l in r.stdout.splitlines() if 'frame.py compose' in l)
            for flag in ('--layout stack', '--facecam 0,462,388,240', '--gameplay 0,90,1920,990', '--top-ratio 0.35',
                         '--focus-x 1200', '--divider 6'):
                self.assertIn(flag, compose, source['kind'])

    def test_reversed_times_are_refused_before_any_download(self):
        out = self.make_run(YT)
        r = self.run_script('vodcut.py', 'cut', out, '--pick', '1', '--in-out', '1=1:02:41-1:02:03', '--facecam', '0,462,388,240',
                            '--dry-run')
        self.assertEqual(r.returncode, 2)
        self.assertIn('END must be after START', r.stderr)
        self.assertEqual(self.calls(), [])


@unittest.skipIf(' ' in sys.executable, 'the fake tools need a shebang-safe python path')
@unittest.skipUnless(util.have('ffmpeg', 'ffprobe'), 'needs ffmpeg')
class Recut(FakeTools):
    def duration(self, path):
        return float(util.probe(path)['format']['duration'])

    def test_a_new_in_out_cuts_the_new_window(self):
        src = util.make_video(os.path.join(self.tmp, 'stream.mp4'), 1920, 1080, 12.0)
        out = self.make_run(YT)
        clips = os.path.join(out, 'clips')
        base = ['cut', out, '--pick', '1', '--facecam', '0,462,388,240', '--streamer', 'Test', '--platform', 'youtube']
        r = self.run_script('vodcut.py', *base, '--in-out', '1=1-4', '--keep-src', FAKE_SOURCE=src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertAlmostEqual(self.duration(os.path.join(clips, 'm01_9x16.mp4')), 3.0, delta=0.1)
        self.assertTrue(os.path.exists(os.path.join(clips, 'm01_src_1.00-4.00.mp4')))   # kept by --keep-src
        r = self.run_script('vodcut.py', *base, '--in-out', '1=2-7.5', FAKE_SOURCE=src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertAlmostEqual(self.duration(os.path.join(clips, 'm01_9x16.mp4')), 5.5, delta=0.1)
        rec = json.loads(util.read(os.path.join(clips, 'm01_9x16.clip.json')))
        self.assertEqual((rec['source_start_s'], rec['source_end_s']), (2.0, 7.5))
        self.assertEqual(sorted(f for f in os.listdir(clips) if '_src_' in f), [])   # old and new windows removed
        sections = [c[c.index('--download-sections') + 1] for c in self.calls() if '--download-sections' in c]
        self.assertEqual(sections, ['*1.00-4.00', '*2.00-7.50'])

    def test_verify_previews_follow_the_moment_span(self):
        # verify right after the loudness pass, then again once whisper has re-ranked: rank 1 moved
        src = util.make_video(os.path.join(self.tmp, 'stream.mp4'), 1920, 1080, 12.0)
        out = self.make_run(YT, moments=[dict(MOMENT, **{'in': 1.0, 'out': 4.0})])
        preview = os.path.join(out, 'verify', 'm01.mp4')
        for _ in range(2):   # the second run reuses the preview: same span
            r = self.run_script('vodcut.py', 'verify', out, '--top', '1', FAKE_SOURCE=src)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertAlmostEqual(self.duration(preview), 3.0, delta=0.1)
        with open(os.path.join(out, 'scan', 'moments.json'), 'w') as f:
            json.dump({'moments': [dict(MOMENT, **{'in': 5.0, 'out': 10.5})]}, f)
        r = self.run_script('vodcut.py', 'verify', out, '--top', '1', FAKE_SOURCE=src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertAlmostEqual(self.duration(preview), 5.5, delta=0.1)
        self.assertEqual(util.read(os.path.join(out, 'verify', 'm01.span')).strip(), '5.00-10.50')
        sections = [c[c.index('--download-sections') + 1] for c in self.calls() if '--download-sections' in c]
        self.assertEqual(sections, ['*1.00-4.00', '*5.00-10.50'])


class Report(unittest.TestCase):
    vc = util.load('vodcut')

    def folder(self, d, runs=None):
        os.makedirs(os.path.join(d, 'scan'))
        st = {'source': YT, 'phases_s': {'audio': 60.0}}
        if runs is not None:
            st['scan_runs'] = runs
        with open(os.path.join(d, 'vodcut.json'), 'w') as f:
            json.dump(st, f)
        with open(os.path.join(d, 'scan', 'run.json'), 'w') as f:
            json.dump({'audio_hours': 23.76, 'wall_time_s': 1.0, 'phases_s': {'rank': 1.0}, 'chunks_total': 72}, f)
        with open(os.path.join(d, 'scan', 'windows.tsv'), 'w') as f:
            f.write('score\tstart_s\n1\t0\n2\t30\n3\t60\n')
        with open(os.path.join(d, 'scan', 'moments.json'), 'w') as f:
            json.dump({'moments': [MOMENT, dict(MOMENT, rank=2, flags=['found'])]}, f)

    def test_only_an_uninterrupted_first_run_has_a_wall_time(self):
        whole = {'finished': True, 'audio_fetched': True, 'chunks_done_before': 0, 'elapsed_s': 3723.0}
        cases = [(None, None), ([whole], '1:02:03'), ([whole, dict(whole, audio_fetched=False, elapsed_s=5)], '1:02:03'),
                 ([dict(whole, chunks_done_before=54)], None), ([dict(whole, finished=False)], None),
                 ([dict(whole, audio_fetched=False)], None)]
        for runs, wall in cases:
            with tempfile.TemporaryDirectory() as d:
                self.folder(d, runs)
                rep = self.vc.report_data(d)
                self.assertEqual(rep['WALL_TIME'], wall, runs)
                self.assertEqual(bool(rep['wall_time_note']), wall is None, runs)
                self.assertEqual((rep['HOURS_SCANNED'], rep['WINDOWS_RANKED'], rep['MOMENTS_RANKED'],
                                  rep['MOMENTS_ADDED_BY_FIND'], rep['API_COST_USD']), (23.76, 3, 1, 1, 0))


@unittest.skipIf(' ' in sys.executable, 'the fake tools need a shebang-safe python path')
class YtfragGuards(FakeTools):
    def test_a_processed_vod_is_refused_before_the_first_request(self):
        track, window = os.path.join(self.tmp, 'a.m4a'), os.path.join(self.tmp, 'w.mp4')
        for args, out in ((['track', 'qSyo5d-dVzA', '139', track], track),
                          (['window', 'qSyo5d-dVzA', '10', '40', window, '--video', '134', '--audio', '139'], window)):
            r = self.run_script('ytfrag.py', *args, FAKE_PROTOCOL='https')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('processed VOD (protocol https)', r.stderr)
            self.assertEqual(sorted(os.listdir(self.tmp)), ['bin', 'calls.jsonl'], out)   # nothing written
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(all('--print' in c for c in self.calls()), self.calls())   # no signed URL was even fetched

    def test_a_whole_file_reply_stops_the_download(self):
        hits = []

        class WholeFile(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header('Content-Length', str(254615289))   # Kai's whole format-139 file
                self.end_headers()
                try:
                    self.wfile.write(b'\0' * 65536)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), WholeFile)
        srv.daemon_threads = True
        srv.handle_error = lambda *a: None   # the client hangs up mid-body on purpose
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            src = yf.Source('qSyo5d-dVzA')
            src.urls['139'] = f'http://127.0.0.1:{srv.server_address[1]}/videoplayback?itag=139'
            src.when['139'] = time.time() + 3600   # no yt-dlp refresh
            with self.assertRaises(SystemExit) as cm:
                yf.download_range(src, '139', 0, 999, os.path.join(self.tmp, 'a.m4a.raw'), jobs=4, progress=False)
            self.assertIn('no longer served as 2-second fragments', str(cm.exception))
            self.assertLessEqual(len(hits), 8, 'it kept requesting after the first whole-file reply')
        finally:
            srv.shutdown()
            srv.server_close()


@unittest.skipUnless(util.have('ffmpeg', 'ffprobe'), 'needs ffmpeg')
class TrackAssembly(unittest.TestCase):
    def run_track(self, d, pieces):
        frags = os.path.join(d, 'a.m4a.raw.frags139')

        class Src(yf.Source):
            def require_fragments(self, *fids):
                pass

        with mock.patch.object(yf, 'Source', Src), mock.patch.object(yf, 'download_range', lambda *a, **k: frags):
            write_pieces(pieces, frags, 7)
            yf.cmd_track(['qSyo5d-dVzA', '139', os.path.join(d, 'a.m4a'), '--frags', '7'])
        return frags

    def test_output_appears_only_when_assembled(self):
        with tempfile.TemporaryDirectory() as d:
            frags = self.run_track(d, fmp4_audio(os.path.join(d, 'src.mp4')))
            self.assertTrue(os.path.exists(os.path.join(d, 'a.m4a')))
            self.assertFalse(os.path.exists(os.path.join(d, 'a.part.m4a')))
            self.assertFalse(os.path.exists(frags))
            self.assertAlmostEqual(float(util.probe(os.path.join(d, 'a.m4a'))['format']['duration']), 4.0, delta=0.2)

    def test_a_failed_assembly_keeps_the_fragments(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                self.run_track(d, os.urandom(70000))
            self.assertIn('assembly failed', str(cm.exception))
            self.assertFalse(os.path.exists(os.path.join(d, 'a.m4a')))
            self.assertFalse(os.path.exists(os.path.join(d, 'a.part.m4a')))
            self.assertEqual(len(os.listdir(os.path.join(d, 'a.m4a.raw.frags139'))), 7)


@unittest.skipIf(' ' in sys.executable, 'the fake tools need a shebang-safe python path')
@unittest.skipUnless(util.have('ffmpeg'), 'needs ffmpeg')
class ScanFrags(FakeTools):
    def setUp(self):
        super().setUp()
        self.frags = os.path.join(self.tmp, 'audio.m4a.raw.frags139')
        write_pieces(fmp4_audio(os.path.join(self.tmp, 'src.mp4')), self.frags, 600)
        self.model = os.path.join(self.tmp, 'model.bin')
        open(self.model, 'wb').close()
        self.scan = os.path.join(self.tmp, 'scan')
        self.assembled = os.path.join(self.tmp, 'audio.m4a')

    def transcript(self):
        p = os.path.join(self.scan, 'transcript.jsonl')
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(line) for line in f]

    def frag_scan(self, **env):
        return self.run_script('scan_frags.py', self.frags, self.scan, self.assembled, '--chunks', '1', '--model',
                               self.model, **env)

    def test_a_chunk_finished_after_assembly_is_dropped(self):
        r = self.frag_scan(FAKE_ASSEMBLE_DURING_WHISPER=self.assembled)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.transcript(), [])   # scan_vod.py transcribes chunk 0 instead
        self.assertEqual(sorted(f for f in os.listdir(self.scan) if f.startswith('_f')), [])

    def test_a_chunk_is_written_once(self):
        for _ in range(2):
            r = self.frag_scan()
            self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.transcript()
        self.assertEqual([d.get('marker') for d in rows], [None, 'chunk_done'])
        self.assertEqual((rows[0]['start'], rows[0]['text']), (1.0, 'hello there'))


if __name__ == '__main__':
    unittest.main()
