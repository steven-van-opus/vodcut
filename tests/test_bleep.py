import json, os, re, subprocess, tempfile, unittest

import util

bl = util.load('bleep')

WHISPER_WORDS = {'transcription': [
    {'text': '', 'offsets': {'from': 0, 'to': 0}},
    {'text': ' Bro,', 'offsets': {'from': 0, 'to': 400}},
    {'text': ' what', 'offsets': {'from': 400, 'to': 600}},
    {'text': ' the', 'offsets': {'from': 600, 'to': 700}},
    {'text': ' fuck', 'offsets': {'from': 700, 'to': 1100}},
    {'text': ' [MUSIC]', 'offsets': {'from': 1100, 'to': 1500}},
    {'text': ' shit,', 'offsets': {'from': 1120, 'to': 1400}},
    {'text': ' run!', 'offsets': {'from': 3000, 'to': 3400}}]}


class Words(unittest.TestCase):
    def test_parse_and_find(self):
        words = bl.parse_whisper_words(WHISPER_WORDS)
        self.assertEqual([w['text'] for w in words], ['Bro,', 'what', 'the', 'fuck', 'shit,', 'run!'])
        self.assertEqual(words[3], {'text': 'fuck', 'start': 0.7, 'end': 1.1})
        spans = bl.find_spans(words, pad=0.04)
        self.assertEqual(spans, [(0.66, 1.44)])                 # two hits close together merge into one bleep
        self.assertEqual(bl.find_spans(words, r'\brun\b', pad=0), [(3.0, 3.4)])

    def test_spans_and_command(self):
        self.assertEqual(bl.parse_spans('7.9-8.3, 3.2-3.62'), [(3.2, 3.62), (7.9, 8.3)])
        for bad in ('3.2', '5-4', 'a-b', ''):
            with self.assertRaises(ValueError):
                bl.parse_spans(bad)
        cmd = bl.apply_cmd('in.mp4', 'out.mp4', [(3.2, 3.62)], 20.0)
        g = cmd[cmd.index('-filter_complex') + 1]
        self.assertIn("volume=volume=0:enable='between(t,3.200,3.620)'", g)
        self.assertIn('aevalsrc', g)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'copy')        # the picture is never re-encoded
        self.assertNotIn('aevalsrc', bl.apply_cmd('in.mp4', 'o.mp4', [(1, 2)], 5.0, mute=True)[8])


@unittest.skipUnless(util.have('ffmpeg', 'ffprobe'), 'ffmpeg not installed')
class Apply(unittest.TestCase):
    def mean_volume(self, path, start, length):
        out = subprocess.run(['ffmpeg', '-hide_banner', '-ss', str(start), '-t', str(length), '-i', path, '-af', 'volumedetect',
                              '-f', 'null', '-'], capture_output=True, text=True).stderr
        return float(re.search(r'mean_volume: (-?[\d.]+) dB', out).group(1))

    def test_mute_and_tone(self):
        with tempfile.TemporaryDirectory() as d:
            src = util.make_video(os.path.join(d, 'c.mp4'), 320, 568, 3.0)
            muted = os.path.join(d, 'm.mp4')
            r = util.run_script('bleep.py', 'apply', src, muted, '--spans', '1.0-1.6', '--mute')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(self.mean_volume(muted, 1.1, 0.4), -60)
            self.assertGreater(self.mean_volume(muted, 2.0, 0.5), -40)
            toned = os.path.join(d, 't.mp4')
            r = util.run_script('bleep.py', 'apply', src, toned, '--spans', '1.0-1.6')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertGreater(self.mean_volume(toned, 1.1, 0.4), -40)
            self.assertEqual(util.run_script('bleep.py', 'apply', src, toned, '--spans', '2.5-9').returncode, 2)


if __name__ == '__main__':
    unittest.main()
