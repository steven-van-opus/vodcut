import hashlib, json, os, tempfile, unittest

import util

prep = util.load('aip_prep')


class Check(unittest.TestCase):
    """check() is pure once it has the probe facts, so most cases need no ffmpeg."""

    def facts(self, **kw):
        base = {'width': 1080, 'height': 1920, 'fps': 30.0, 'vcodec': 'h264', 'has_audio': True, 'duration': 31.0}
        base.update(kw)
        return base

    def run_check(self, name='clip.mp4', size=1024, **kw):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, name)
            with open(path, 'wb') as f:
                f.write(b'\0' * size)
            return prep.check(path, 30, info=self.facts(**kw))

    def test_a_composed_clip_passes_clean(self):
        r = self.run_check()
        self.assertTrue(r['ok'])
        self.assertEqual(r['errors'], [])
        self.assertEqual(r['warnings'], [])

    def test_landscape_is_flagged(self):
        # AI Producer never reframes: a 16:9 upload stays 16:9, so it is an error unless it is meant to stay 16:9
        r = self.run_check(width=1920, height=1080, fps=60.0)
        self.assertFalse(r['ok'])
        text = ' '.join(r['errors'])
        self.assertIn('landscape', text)
        self.assertIn('frame.py compose', text)
        self.assertIn('--allow-landscape', text)
        self.assertIn('30 fps', ' '.join(r['warnings']))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'talk.mp4')
            with open(path, 'wb') as f:
                f.write(b'\0' * 1024)
            r = prep.check(path, 30, info=self.facts(width=1920, height=1080), allow_landscape=True)
        self.assertTrue(r['ok'])
        self.assertIn('landscape', ' '.join(r['warnings']))

    def test_limits(self):
        self.assertFalse(self.run_check(duration=31 * 60.0)['ok'])
        self.assertIn('15 min', ' '.join(self.run_check(duration=20 * 60.0)['warnings']))
        self.assertFalse(self.run_check(name='clip.avi')['ok'])
        self.assertIn('no audio', ' '.join(self.run_check(has_audio=False)['warnings']))
        self.assertIn('not 9:16', ' '.join(self.run_check(width=1080, height=1350)['warnings']))
        self.assertIn('1:1', ' '.join(self.run_check(width=1080, height=1080)['warnings']))

    def test_missing_file(self):
        r = prep.check('/nonexistent/clip.mp4')
        self.assertFalse(r['ok'])


class SignArgs(unittest.TestCase):
    def test_sign_args_match_the_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            for ext, ctype in (('.mp4', 'video/mp4'), ('.mov', 'video/quicktime'), ('.webm', 'video/webm')):
                path = os.path.join(d, 'm01_9x16' + ext)
                data = os.urandom(3 * 1024 * 1024 + 17)
                with open(path, 'wb') as f:
                    f.write(data)
                r = util.run_script('aip_prep.py', 'sign-args', path)
                self.assertEqual(r.returncode, 0, r.stderr)
                args = json.loads(r.stdout)
                self.assertEqual(args, {'filename': 'm01_9x16' + ext, 'content_type': ctype, 'size': len(data),
                                        'sha256': hashlib.sha256(data).hexdigest()})
            bad = os.path.join(d, 'x.mkv')
            open(bad, 'wb').close()
            self.assertEqual(util.run_script('aip_prep.py', 'sign-args', bad).returncode, 2)

    def test_record_has_credit_and_disclaimer(self):
        with tempfile.TemporaryDirectory() as d:
            clip = os.path.join(d, 'm02_9x16.mp4')
            with open(clip, 'wb') as f:
                f.write(b'clip')
            r = util.run_script('aip_prep.py', 'record', clip, '--streamer', 'Kai Cenat', '--handle', '@KaiCenat',
                                '--platform', 'twitch', '--source-url', 'https://www.twitch.tv/videos/1', '--start', '61.5',
                                '--end', '92')
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = json.loads(util.read(os.path.join(d, 'm02_9x16.clip.json')))
            self.assertEqual(rec['credit'], '\U0001F3A5 @KaiCenat (Twitch)')
            self.assertIn('Not affiliated with or endorsed by Kai Cenat, Rockstar Games', rec['disclaimer'])
            self.assertEqual((rec['source_start_s'], rec['source_end_s']), (61.5, 92.0))
            self.assertEqual(rec['sha256'], hashlib.sha256(b'clip').hexdigest())
            self.assertIsNone(rec['aip']['page_url'])


@unittest.skipUnless(util.have('ffmpeg', 'ffprobe'), 'ffmpeg not installed')
class RealFile(unittest.TestCase):
    def test_check_reads_a_real_file(self):
        with tempfile.TemporaryDirectory() as d:
            clip = util.make_video(os.path.join(d, 'v.mp4'), 1080, 1920, 1.0)
            r = util.run_script('aip_prep.py', 'check', clip)
            self.assertEqual(r.returncode, 0, r.stdout)
            out = json.loads(r.stdout)
            self.assertEqual((out['facts']['width'], out['facts']['height']), (1080, 1920))
            self.assertTrue(out['facts']['has_audio'])
            wide = util.make_video(os.path.join(d, 'wide.mp4'), 1920, 1080, 1.0)
            r = util.run_script('aip_prep.py', 'check', wide)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertFalse(json.loads(r.stdout)['ok'])
            self.assertEqual(util.run_script('aip_prep.py', 'check', wide, '--allow-landscape').returncode, 0)


if __name__ == '__main__':
    unittest.main()
