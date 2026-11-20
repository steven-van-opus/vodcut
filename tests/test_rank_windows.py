import json, os, tempfile, unittest

import util

rw = util.load('rank_windows')


def write_scan(d, segments, loud=None, markers=True):
    with open(os.path.join(d, 'transcript.jsonl'), 'w') as f:
        for st, en, tx in segments:
            f.write(json.dumps({'chunk': 0, 'start': st, 'end': en, 'text': tx}) + '\n')
        if markers:
            f.write(json.dumps({'chunk': 0, 'start': 0, 'end': 0, 'text': '', 'marker': 'chunk_done'}) + '\n')
    if loud is not None:
        with open(os.path.join(d, 'loudness.tsv'), 'w') as f:
            f.write('t\trms_db\tpeak_db\n')
            for t, r in loud:
                f.write(f'{t:.1f}\t{r:.1f}\t{r + 6:.1f}\n')


def quiet_stream(seconds=600, loud_at=(), level=-30.0, peak=-8.0):
    rows = []
    for t in range(seconds):
        rows.append((float(t), peak if any(a <= t < a + 10 for a in loud_at) else level))
    return rows


class Ranking(unittest.TestCase):
    def test_loud_window_with_reaction_cues_wins(self):
        segs = [(5, 8, 'okay so we are driving to the store'),
                (300, 305, 'NO WAY bro the cops are here, run, RUN, chat look'),
                (310, 314, 'oh my god we got busted'),
                (500, 503, 'alright let me check the map')]
        rows = rw.rank(segs, quiet_stream(600, loud_at=(301,)), window=30, total=600)
        self.assertEqual(rows[0]['start'], 300)
        self.assertGreater(rows[0]['cues'], 5)
        self.assertEqual(len(rows), 21)
        self.assertEqual(rows, sorted(rows, key=lambda r: (-r['score'], r['start'])))

    def test_without_loudness_cues_alone_rank(self):
        segs = [(10, 12, 'hello'), (95, 99, 'wait wait no way chat look at this crazy car')]
        rows = rw.rank(segs, [], window=30)
        self.assertEqual(rows[0]['start'], 90)
        self.assertGreater(rows[0]['score'], rows[1]['score'])

    def test_review_flags(self):
        self.assertEqual(rw.review_flags('what the fuck bro'), ['profanity'])
        self.assertIn('sexual', rw.review_flags('we are going to the strip club'))
        self.assertIn('spoiler', rw.review_flags('chat this is the final mission'))
        self.assertEqual(rw.review_flags('look at this car'), [])
        import re
        self.assertIn('blocklist', rw.review_flags('my secret word', [re.compile('secret', re.I)]))

    def test_whisper_loops_are_cleaned_and_flagged(self):
        loop = [(100 + i, 101 + i, 'Man, they just got a gun.') for i in range(25)]
        real = [(400, 402, 'no way chat'), (401, 403, 'no way chat'), (402, 404, 'look at this')]
        kept, dropped, loops = rw.clean_loops(loop + real, max_run=2, max_line_repeats=0)
        self.assertEqual(dropped, 23)
        self.assertEqual(sum(1 for s in kept if 'gun' in s[2]), 2)
        self.assertEqual(len([s for s in kept if 'no way' in s[2]]), 2)
        self.assertTrue(any(100 <= t < 130 for t in loops))
        rows = rw.rank(kept, [], window=30, loops=loops)
        looped = next(r for r in rows if r['start'] == 90)
        self.assertIn('loop', looped['flags'])
        # a line repeated all over the stream is dropped everywhere
        spam = [(i * 50.0, i * 50.0 + 1, 'Who told me not to get the shotgun?') for i in range(40)]
        kept, dropped, _ = rw.clean_loops(spam + [(5, 6, 'yeah.')] * 1, max_line_repeats=30)
        self.assertEqual(dropped, 40)
        self.assertEqual(kept, [(5, 6, 'yeah.')])

    def test_moments_are_distinct_and_padded(self):
        segs = [(t, t + 2, 'no way bro run run chat') for t in (100, 130, 160, 400, 700)]
        rows = rw.rank(segs, quiet_stream(900, loud_at=(100, 130, 160, 400, 700)), total=900)
        ms = rw.pick_moments(rows, n=10, min_gap=90, window=30, pad_before=6, pad_after=8, total=900)
        starts = [m['window_start'] for m in ms]
        for i, a in enumerate(starts):
            for b in starts[i + 1:]:
                self.assertGreaterEqual(abs(a - b), 90)
        m = ms[0]
        self.assertEqual(m['in'], m['window_start'] - 6)
        self.assertEqual(m['out'], m['window_start'] + 30 + 8)
        self.assertEqual([x['rank'] for x in ms], list(range(1, len(ms) + 1)))
        self.assertEqual(ms[0]['in_hms'], rw.hms(ms[0]['in']))

    def test_silence_is_never_a_moment(self):
        rows = rw.rank([], [(float(t), -90.0) for t in range(120)], total=120)
        self.assertEqual(rw.pick_moments(rows, total=120), [])

    def test_cli_writes_all_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            write_scan(d, [(60, 63, 'no way bro the cops'), (61, 62, 'no way bro the cops'),
                           (62, 63, 'no way bro the cops'), (200, 203, 'what the fuck bro')],
                       quiet_stream(300, loud_at=(60, 200)))
            r = util.run_script('rank_windows.py', d, '--moments', '5', '--min-gap', '60')
            self.assertEqual(r.returncode, 0, r.stderr)
            info = json.loads(r.stdout)
            self.assertEqual(info['loop_segments_dropped'], 1)
            for name in ('windows.tsv', 'top_windows.md', 'moments.json', 'moments.md'):
                self.assertTrue(os.path.exists(os.path.join(d, name)), name)
            with open(os.path.join(d, 'windows.tsv')) as f:
                self.assertEqual(f.readline().strip().split('\t'),
                                 ['score', 'start_s', 'start_hms', 'loud_top5_db', 'cues', 'words', 'flags', 'text'])
            with open(os.path.join(d, 'moments.json')) as f:
                ms = json.load(f)['moments']
            self.assertEqual(len(ms), 2)
            self.assertIn('profanity', next(m for m in ms if m['window_start'] == 180)['flags'])

    def test_cli_refuses_an_empty_folder(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(util.run_script('rank_windows.py', d).returncode, 1)


if __name__ == '__main__':
    unittest.main()
