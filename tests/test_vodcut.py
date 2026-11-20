import json, os, shutil, tempfile, unittest

import util

vc = util.load('vodcut')
MODEL = os.environ.get('WHISPER_MODEL')


class Sources(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(vc.classify('qSyo5d-dVzA')['kind'], 'youtube')
        for url in ('https://www.youtube.com/watch?v=qSyo5d-dVzA', 'https://youtu.be/qSyo5d-dVzA?t=10',
                    'https://www.youtube.com/live/qSyo5d-dVzA', 'https://www.youtube.com/watch?t=5&v=qSyo5d-dVzA'):
            self.assertEqual(vc.classify(url)['id'], 'qSyo5d-dVzA', url)
        self.assertEqual(vc.classify('https://www.twitch.tv/videos/123456789')['kind'], 'url')
        with tempfile.NamedTemporaryFile(suffix='.m4a') as f:
            self.assertEqual(vc.classify(f.name)['kind'], 'file')
        with self.assertRaises(ValueError):
            vc.classify('not a source')

    def test_times_and_overrides(self):
        self.assertEqual(vc.hms_to_s('1:02:03.5'), 3723.5)
        self.assertEqual(vc.s_to_hms(3723.5), '1:02:04')
        self.assertEqual(vc.parse_overrides(['3=1:02:03-1:02:41']), {3: (3723.0, 3761.0)})
        with self.assertRaises(ValueError):
            vc.parse_overrides(['three=1-2'])

    def test_commands_per_source(self):
        # still served as 2-second fragments (right after the stream): ytfrag.py
        frags = dict(vc.classify('qSyo5d-dVzA'), protocol='http_dash_segments')
        dst, cmds = vc.audio_cmds(frags, '/tmp/out', '139', 24)
        self.assertEqual(cmds[0][-6:], ['track', 'qSyo5d-dVzA', '139', dst, '--jobs', '24'])
        w = vc.window_cmd(frags, 100.0, 140.5, 'o.mp4', '134', '139', preview=True)[0]
        self.assertEqual(w[-9:], ['window', 'qSyo5d-dVzA', '100.00', '140.50', 'o.mp4', '--video', '134', '--audio', '139'])
        # processed into whole files (https), or never probed: yt-dlp, never ytfrag.py (the server would answer every
        # "fragment" with the whole file)
        for proto in ('https', None):
            yt = dict(vc.classify('qSyo5d-dVzA'), protocol=proto)
            dst, cmds = vc.audio_cmds(yt, '/tmp/out', '139', 24)
            self.assertEqual(cmds[0][:5], ['yt-dlp', '--no-warnings', '--no-playlist', '-f', '139/ba/w'])
            self.assertEqual(cmds[0][-1], 'https://www.youtube.com/watch?v=qSyo5d-dVzA')
            self.assertEqual(cmds[1][0], '__extract__')
            prev = vc.window_cmd(yt, 100.0, 140.5, 'o.mp4', '134', '139', preview=True)[0]
            full = vc.window_cmd(yt, 100.0, 140.5, 'o.mp4', '299', '140')[0]
            self.assertEqual(prev[prev.index('-f') + 1], '134+139/bv*[height<=360]+ba/b[height<=360]/w')
            self.assertEqual(full[full.index('-f') + 1], '299+140/bv*[height<=1080]+ba/b')
            for c in (prev, full):
                self.assertEqual(c[c.index('--download-sections') + 1], '*100.00-140.50')
                self.assertEqual(c[c.index('-o') + 1], 'o.mp4')
            self.assertFalse(any('ytfrag.py' in part for c in cmds + [prev, full] for part in c))
        tw = vc.classify('https://www.twitch.tv/videos/1')
        w = vc.window_cmd(tw, 100, 140, 'o.mp4', '299', '140')[0]
        self.assertIn('--download-sections', w)
        self.assertIn('*100.00-140.00', w)
        dst, cmds = vc.audio_cmds(tw, '/tmp/out')
        self.assertEqual(cmds[0][cmds[0].index('-f') + 1], 'ba/w')   # never full-quality video just for the audio
        self.assertEqual(cmds[1][0], '__extract__')
        # a local file is extracted in place, never deleted; its windows are published by a rename
        f = {'kind': 'file', 'path': '/tmp/stream.mp4'}
        self.assertEqual(vc.audio_cmds(f, '/tmp/out')[1], [['__extract__', '/tmp/stream.mp4', '/tmp/out/audio.m4a', 'file']])
        w = vc.window_cmd(f, 1, 5, '/tmp/out/verify/m01.mp4', '134', '139', preview=True)
        self.assertEqual(w[0][-1], '/tmp/out/verify/m01.part.mp4')
        self.assertEqual(w[1], ['__rename__', '/tmp/out/verify/m01.part.mp4', '/tmp/out/verify/m01.mp4'])

    def test_same_source_ignores_the_probed_protocol(self):
        yt = vc.classify('https://youtu.be/qSyo5d-dVzA')
        self.assertTrue(vc.same_source(dict(yt, protocol='http_dash_segments'), vc.classify('qSyo5d-dVzA')))
        self.assertFalse(vc.same_source(yt, vc.classify('VZqAPdUvqnQ')))
        self.assertFalse(vc.same_source(None, yt))


@unittest.skipUnless(util.have('ffmpeg', 'ffprobe', 'whisper-cli') and MODEL and os.path.exists(MODEL),
                     'needs ffmpeg, whisper-cli and $WHISPER_MODEL')
class EndToEnd(unittest.TestCase):
    """scan -> find -> verify -> cut -> prep -> report on a synthetic 'stream' file, all local."""

    def test_pipeline_on_a_local_file(self):
        with tempfile.TemporaryDirectory() as d:
            src = util.make_video(os.path.join(d, 'stream.mp4'), 1920, 1080, 8.0)
            out = os.path.join(d, 'run')
            r = util.run_script('vodcut.py', 'scan', src, out, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for name in ('loudness.tsv', 'transcript.jsonl', 'windows.tsv', 'moments.json', 'run.json'):
                self.assertTrue(os.path.exists(os.path.join(out, 'scan', name)), name)
            rep = json.loads(util.run_script('vodcut.py', 'report', out, '--json').stdout)
            self.assertEqual(rep['API_COST_USD'], 0)
            self.assertIsNotNone(rep['WALL_TIME'])
            # a pure tone has no speech: the scan may legitimately find no moment, so cut a manual window
            with open(os.path.join(out, 'scan', 'moments.json')) as f:
                data = json.load(f)
            data['moments'] = [{'rank': 1, 'score': 1, 'in': 1.0, 'out': 6.0, 'in_hms': '0:00:01', 'out_hms': '0:00:06',
                                'flags': [], 'text': 'test', 'window_start': 0, 'window_end': 30, 'cues': 0, 'words': 1,
                                'loud_top5_db': -20, 'text_before': '', 'text_after': ''}]
            with open(os.path.join(out, 'scan', 'moments.json'), 'w') as f:
                json.dump(data, f)
            r = util.run_script('vodcut.py', 'verify', out, '--top', '1')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.exists(os.path.join(out, 'verify', 'm01_sheet.jpg')))
            r = util.run_script('vodcut.py', 'cut', out, '--pick', '1', '--facecam', '0,462,388,240', '--streamer', 'Test',
                                '--platform', 'youtube')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            clip = os.path.join(out, 'clips', 'm01_9x16.mp4')
            v = next(s for s in util.probe(clip)['streams'] if s['codec_type'] == 'video')
            self.assertEqual((v['width'], v['height']), (1080, 1920))
            r = util.run_script('vodcut.py', 'prep', out)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(util.read(os.path.join(out, 'clips', 'm01_9x16.sign.json')))['content_type'], 'video/mp4')


if __name__ == '__main__':
    unittest.main()
