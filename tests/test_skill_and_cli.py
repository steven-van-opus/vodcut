"""/vodcut repo checks: every script answers --help, manifests agree, the skill is self-contained, copy rules hold."""
import ast, json, os, re, shutil, subprocess, sys, unittest

import util

SKILL_DIR = os.path.join(util.ROOT, 'skills', 'vodcut')
PY_SCRIPTS = {
    'vodcut.py': [[], ['scan'], ['find'], ['verify'], ['cut'], ['prep'], ['report']],
    'ytfrag.py': [[]], 'scan_vod.py': [[]], 'scan_frags.py': [[]], 'rank_windows.py': [[]], 'framesheet.py': [[]],
    'frame.py': [[], ['probe'], ['grid'], ['plan'], ['still'], ['compose']],
    'bleep.py': [[], ['words'], ['find'], ['apply']],
    'aip_prep.py': [[], ['check'], ['sign-args'], ['record']],
}
AIP_TOOLS = {'get_pricing', 'get_editing_preset', 'sign_upload', 'start_transcribe_project', 'wait_task', 'get_task',
             'get_transcript', 'start_export', 'get_export_url', 'finish_project', 'commit_workspace', 'list_workspace',
             'get_workspace_file', 'sign_workspace_upload', 'list_sound_effects', 'place_sound_effects', 'get_view_url',
             'list_reference_presets', 'get_reference_preset', 'get_project', 'list_projects'}
BANNED_COPY = [r'up to \$\d', r'while you sleep', r'autopilot', r'get rich', r'passive income', r'guaranteed', r'millionaire']


class Help(unittest.TestCase):
    def test_every_script_has_help(self):
        listed = sorted(f for f in os.listdir(util.SCRIPTS) if f.endswith('.py'))
        self.assertEqual(listed, sorted(PY_SCRIPTS))
        for name, variants in PY_SCRIPTS.items():
            for sub in variants:
                r = util.run_script(name, *(sub + ['--help']), timeout=60)
                self.assertEqual(r.returncode, 0, f'{name} {sub}: {r.stderr[:300]}')
                self.assertIn('usage', r.stdout.lower(), f'{name} {sub}')

    def test_stdlib_only(self):
        std = set(sys.stdlib_module_names) | {'rank_windows', 'scan_vod'}
        for name in PY_SCRIPTS:
            tree = ast.parse(util.read(os.path.join(util.SCRIPTS, name)))
            for node in ast.walk(tree):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                       [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
                for m in mods:
                    self.assertIn(m.split('.')[0], std, f'{name} imports {m}')

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_render_help(self):
        r = subprocess.run(['node', os.path.join(util.ROOT, 'docs', 'render.mjs'), '--help'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)


class Repo(unittest.TestCase):
    def test_manifests(self):
        plugin = json.loads(util.read(os.path.join(util.ROOT, '.claude-plugin', 'plugin.json')))
        market = json.loads(util.read(os.path.join(util.ROOT, '.claude-plugin', 'marketplace.json')))
        self.assertEqual((plugin['name'], market['name'], market['plugins'][0]['name']), ('vodcut', 'vodcut', 'vodcut'))
        self.assertEqual(plugin['version'], market['plugins'][0]['version'])
        self.assertIn('aip@ai-producer-plugins', plugin['dependencies'])
        self.assertIn('ai-producer-plugins', market['allowCrossMarketplaceDependenciesOn'])
        self.assertTrue(os.path.exists(os.path.join(util.ROOT, plugin['icon'])))

    def test_skill_is_self_contained(self):
        """Copied alone to ~/.claude/skills/vodcut, the skill must still find everything it names."""
        text = util.read(os.path.join(SKILL_DIR, 'SKILL.md'))
        m = re.match(r'^---\n(.*?)\n---\n', text, re.S)
        self.assertIsNotNone(m)
        self.assertIn('name: vodcut', m.group(1))
        self.assertNotIn('../', re.sub(r'\[[^\]]*\]\(references/[^)]+\)', '', text))
        for script in re.findall(r'\$\{CLAUDE_SKILL_DIR\}/scripts/([\w.]+)', text):
            self.assertTrue(os.path.exists(os.path.join(util.SCRIPTS, script)), script)
        for ref in re.findall(r'\((references/[\w.-]+)\)', text):
            self.assertTrue(os.path.exists(os.path.join(SKILL_DIR, ref)), ref)
        for script in re.findall(r'\b([a-z_]+\.py)\b', text):
            self.assertTrue(script in ('upload_batch.py',) or os.path.exists(os.path.join(util.SCRIPTS, script)), script)

    def test_aip_tool_names(self):
        for root, _, files in os.walk(util.ROOT):
            if '.git' in root:
                continue
            for f in files:
                if f.endswith(('.md', '.py')):
                    text = util.read(os.path.join(root, f))
                    for tool in re.findall(r'mcp__plugin_aip_ai-producer__(\w+)', text):
                        if tool != 'tool':
                            self.assertIn(tool, AIP_TOOLS, f'{f}: {tool}')
                    for tool in re.findall(r'`(\w+)`', text):
                        if tool in ('build_captions', 'add_sound_effects', 'save_editing_preset'):
                            self.fail(f'{f} names {tool}, which the official skill forbids')

    def test_launch_numbers_come_from_the_recorded_run(self):
        """The launch line quotes one real, uninterrupted run: docs/launch-run.json is its `report --json`."""
        rec = json.loads(util.read(os.path.join(util.ROOT, 'docs', 'launch-run.json')))
        rep = rec['report']
        self.assertIsNone(rep['wall_time_note'])
        self.assertIsNotNone(rep['WALL_TIME'])
        self.assertEqual(rep['API_COST_USD'], 0)
        self.assertEqual(rep['source']['id'], 'qSyo5d-dVzA')
        self.assertNotIn('top', rep)   # no transcript lines in the repo
        readme = util.read(os.path.join(util.ROOT, 'README.md'))
        hero = readme.split('\n---\n', 1)[0]
        for value in (f"**{rep['HOURS_SCANNED']} hours**", f"**{rep['WINDOWS_RANKED']:,} thirty-second windows**",
                      f"**{rep['MOMENTS_RANKED']} moments**", f"**{rep['WALL_TIME']}**"):
            self.assertIn(value, hero)
        numbers = readme[readme.index('## The numbers'):readme.index('## FAQ')]
        for value in (f"| {rep['HOURS_SCANNED']} |", f"| {rep['WINDOWS_RANKED']:,} |", f"| {rep['MOMENTS_RANKED']} ",
                      f"| {rep['WALL_TIME']}"):
            self.assertIn(value, numbers)

    def test_readme_placeholders_and_copy(self):
        readme = util.read(os.path.join(util.ROOT, 'README.md'))
        self.assertIn('[OWNER]', readme)
        for ph in ('[HOURS_SCANNED]', '[MOMENTS_FOUND]', '[WALL_TIME]'):
            self.assertNotIn(ph, readme)
        for cmd in ('claude plugin install aip@ai-producer-plugins', '/plugin install vodcut@vodcut',
                    'cp -R vodcut/skills/vodcut ~/.claude/skills/vodcut'):
            self.assertIn(cmd, readme)
        self.assertIn('Not affiliated with or endorsed by Rockstar Games', readme)
        for img in re.findall(r'src="(docs/[^"]+)"', readme):
            self.assertTrue(os.path.exists(os.path.join(util.ROOT, img)), img)
        for root, _, files in os.walk(util.ROOT):
            if '.git' in root:
                continue
            for f in files:
                if f.endswith('.md'):
                    text = util.read(os.path.join(root, f))
                    for pat in BANNED_COPY:
                        self.assertIsNone(re.search(pat, text, re.I), f'{f}: {pat}')


if __name__ == '__main__':
    unittest.main()
