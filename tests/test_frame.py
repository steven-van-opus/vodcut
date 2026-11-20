import json, os, tempfile, unittest

import util

frame = util.load('frame')
R = frame.Rect
SPEED_FACECAM = R(0, 462, 388, 240)        # facecam of a 1920x1080 stream, left-middle
OVERLAY_FREE = R(0, 90, 1920, 990)         # gameplay below the stream's top overlay


class Geometry(unittest.TestCase):
    def test_parse_rect(self):
        self.assertEqual(frame.parse_rect('0,462,388,240'), SPEED_FACECAM)
        self.assertEqual(frame.parse_rect(' 10, 20, 300.4, 200 '), R(10, 20, 300, 200))
        for bad in ('1,2,3', 'a,b,c,d', '0,0,8,8', '-1,0,100,100', ''):
            with self.assertRaises(ValueError):
                frame.parse_rect(bad)

    def test_cover_crop_keeps_aspect_and_stays_inside(self):
        for region, (w, h) in [(R(0, 0, 1920, 1080), (1080, 1252)), (R(0, 462, 388, 240), (1080, 668)),
                               (R(100, 50, 600, 900), (1080, 700)), (R(0, 0, 1920, 1080), (1080, 1920))]:
            c = frame.cover_crop(region, w, h)
            self.assertEqual(c.w % 2, 0); self.assertEqual(c.h % 2, 0)
            self.assertEqual(c.x % 2, 0); self.assertEqual(c.y % 2, 0)
            self.assertGreaterEqual(c.x, region.x); self.assertGreaterEqual(c.y, region.y)
            self.assertLessEqual(c.x + c.w, region.x + region.w); self.assertLessEqual(c.y + c.h, region.y + region.h)
            self.assertAlmostEqual(c.w / c.h, w / h, delta=0.02)

    def test_stack_plan_for_a_left_facecam(self):
        p = frame.plan(1920, 1080, SPEED_FACECAM, 'stack', OVERLAY_FREE)
        self.assertEqual(p['top']['size'], [1080, 668])         # 1080 * 240 / 388 = 668
        self.assertEqual(p['bottom']['size'], [1080, 1252])
        self.assertEqual(p['top']['size'][1] + p['bottom']['size'][1], 1920)
        self.assertEqual(p['seam_y'], 668)
        self.assertEqual(p['bottom']['crop'], [534, 90, 852, 990])  # full overlay-free height, centred
        self.assertEqual(p['notes'], [])

    def test_top_band_is_clamped(self):
        tall = frame.plan(1920, 1080, R(0, 0, 300, 400), 'stack')    # portrait facecam would eat the frame
        self.assertEqual(tall['seam_y'], 960)
        wide = frame.plan(1920, 1080, R(0, 0, 800, 200), 'stack')    # letterbox facecam
        self.assertEqual(wide['seam_y'], frame.even_round(0.28 * 1920))
        fixed = frame.plan(1920, 1080, SPEED_FACECAM, 'stack', top_ratio=0.4)
        self.assertEqual(fixed['seam_y'], 768)
        with self.assertRaises(ValueError):
            frame.plan(1920, 1080, SPEED_FACECAM, 'stack', top_ratio=0.9)

    def test_gameplay_crop_slides_off_a_facecam_in_its_way(self):
        face = R(1000, 700, 300, 200)                      # right of centre, inside the centred crop
        p = frame.plan(1920, 1080, face, 'stack')
        crop = R(*p['bottom']['crop'])
        self.assertFalse(frame.overlaps(crop, face))
        self.assertEqual(crop.x + crop.w, 1000)            # slid left, flush against the facecam
        self.assertTrue(p['notes'] and 'slid left' in p['notes'][0])
        # an explicit focus wins, even if it overlaps
        p2 = frame.plan(1920, 1080, face, 'stack', focus_x=960)
        self.assertTrue(frame.overlaps(R(*p2['bottom']['crop']), face))
        self.assertEqual(p2['notes'], [])
        # a dead-centre facecam cannot be avoided by a full-height crop: say so instead of guessing
        p3 = frame.plan(1920, 1080, R(800, 600, 320, 200), 'stack')
        self.assertIn('still shows the facecam', p3['notes'][0])

    def test_inset_and_full(self):
        p = frame.plan(1920, 1080, SPEED_FACECAM, 'inset', OVERLAY_FREE)
        self.assertEqual(p['background']['size'], [1080, 1920])
        self.assertEqual(p['box']['size'][0], 670)
        self.assertEqual(p['box']['x'] * 2 + 670 + 2 * p['box']['border'], 1080)
        f = frame.plan(1920, 1080, None, 'full')
        self.assertEqual(f['background']['size'], [1080, 1920])
        self.assertIsNone(f['seam_y'])
        with self.assertRaises(ValueError):
            frame.plan(1920, 1080, None, 'stack')

    def test_rects_outside_the_source_are_refused(self):
        with self.assertRaises(ValueError):
            frame.plan(1920, 1080, R(1800, 900, 300, 300), 'stack')
        with self.assertRaises(ValueError):
            frame.plan(1920, 1080, SPEED_FACECAM, 'stack', R(0, 0, 2000, 1080))

    def test_filtergraph_and_command(self):
        p = frame.plan(1920, 1080, SPEED_FACECAM, 'stack', OVERLAY_FREE, divider=6)
        g = frame.filtergraph(p, fps=30)
        self.assertIn('crop=388:238:0:462,scale=1080:668', g)
        self.assertIn('crop=852:990:534:90,scale=1080:1252', g)
        self.assertIn('vstack=inputs=2', g)
        self.assertIn('drawbox=x=0:y=665:w=1080:h=6', g)
        self.assertTrue(g.endswith(',fps=30,format=yuv420p[v]'))
        cmd = frame.compose_cmd('in.mp4', 'out.mp4', p, start=10, end=40.5)
        self.assertEqual(cmd[cmd.index('-ss') + 1], '10.000')
        self.assertEqual(cmd[cmd.index('-t') + 1], '30.500')
        self.assertLess(cmd.index('-ss'), cmd.index('-i'))
        for flag in ('libx264', 'yuv420p', '+faststart', '0:a:0', '48000'):
            self.assertIn(flag, cmd)
        self.assertNotIn('0:a:0', frame.compose_cmd('in.mp4', 'o.mp4', p, audio=False))
        with self.assertRaises(ValueError):
            frame.compose_cmd('in.mp4', 'o.mp4', p, start=5, end=5)
        self.assertNotIn('format=yuv420p', frame.filtergraph(p, still=True))


@unittest.skipUnless(util.have('ffmpeg', 'ffprobe'), 'ffmpeg not installed')
class Render(unittest.TestCase):
    def test_compose_still_grid_on_a_synthetic_stream(self):
        with tempfile.TemporaryDirectory() as d:
            src = util.make_video(os.path.join(d, 'stream.mp4'), 1920, 1080, 2.0, fps=60)
            out = os.path.join(d, 'v.mp4')
            r = util.run_script('frame.py', 'compose', src, out, '--facecam', '0,462,388,240', '--preset', 'ultrafast')
            self.assertEqual(r.returncode, 0, r.stderr)
            info = json.loads(r.stdout)
            self.assertEqual((info['width'], info['height'], info['fps']), (1080, 1920, 30.0))
            self.assertTrue(info['has_audio'])
            for mode, extra in (('still', ['--facecam', '0,462,388,240', '--at', '1']), ('grid', ['--at', '1'])):
                png = os.path.join(d, mode + '.png')
                r = util.run_script('frame.py', mode, src, png, *extra)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertGreater(os.path.getsize(png), 1000)
            r = util.run_script('frame.py', 'grid', src, os.path.join(d, 'z.png'), '--zoom', '0,420,480,340')
            self.assertEqual(json.loads(r.stdout)['major_px'], 50)

    def test_bad_rect_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as d:
            src = util.make_video(os.path.join(d, 's.mp4'), 640, 360, 1.0, audio=False)
            r = util.run_script('frame.py', 'plan', src, '--facecam', '600,300,200,200')
            self.assertEqual(r.returncode, 2)
            self.assertIn('outside', r.stderr)


if __name__ == '__main__':
    unittest.main()
