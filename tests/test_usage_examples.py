"""Execute the bilingual offline recipes without a recorder or kernel image."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from urllib.parse import unquote, urlsplit

import helpers
from test_capture_examples import shell_blocks
from test_timeline import invariant_errors


ROOT = Path(helpers.ROOT)
GUIDES = (ROOT / 'docs/examples.cn.md', ROOT / 'docs/examples.md')


def heading_anchor(heading):
    return ''.join(character for character in heading.lower()
                   if character in '-_ ' or unicodedata.category(character)[0] not in 'PS').replace(' ', '-')


class UsageExamplesTest(unittest.TestCase):
    def test_bilingual_shell_recipes_stay_identical(self):
        self.assertEqual(shell_blocks(GUIDES[0]), shell_blocks(GUIDES[1]))

    def test_shell_blocks_have_valid_syntax(self):
        for document in GUIDES:
            for index, block in enumerate(shell_blocks(document)):
                with self.subTest(document=document.name, block=index):
                    result = subprocess.run(['bash', '-n'], input=block, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_links_and_section_anchors_resolve(self):
        for document in GUIDES:
            targets = re.findall(r'\[[^\]\n]+\]\(([^)\s]+)\)', document.read_text(encoding='utf-8'))
            for target in targets:
                parsed = urlsplit(target)
                if parsed.scheme:
                    continue
                destination = document.parent / unquote(parsed.path) if parsed.path else document
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(destination.is_file())
                    if parsed.fragment:
                        headings = re.findall(r'^#{1,6} (.+)$', destination.read_text(encoding='utf-8'), re.M)
                        self.assertIn(unquote(parsed.fragment), [heading_anchor(heading) for heading in headings])

    def test_offline_recipes_preserve_models_sources_and_sidecar_resources(self):
        with tempfile.TemporaryDirectory(prefix='fg-usage-examples-') as directory:
            root = Path(directory) / 'checkout with spaces'
            samples = root / 'samples'
            samples.mkdir(parents=True)
            for filename in ('sample_io.txt', 'sample_io_slow.txt', 'sample_mixed.jsonl', 'demo_mixed.c'):
                shutil.copyfile(ROOT / 'samples' / filename, samples / filename)
            commands = root / 'bin'
            commands.mkdir()
            for name, script in (('funcgraph', 'funcgraph.py'), ('fgreport', 'fgreport')):
                launcher = commands / name
                launcher.write_text('#!/bin/sh\nexec {} {} "$@"\n'.format(
                    shlex.quote(sys.executable), shlex.quote(str(ROOT / script))), encoding='utf-8')
                launcher.chmod(0o755)
            config = root / 'config.ini'
            config.write_text('[default]\nvmlinux = /missing/unrelated-vmlinux\n'
                              '[fgreport]\npreset = unrelated-kernel\n', encoding='utf-8')
            environment = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ.get('PATH', ''),
                               FG_CONFIG=str(config), FG_PRESET='unrelated-kernel')
            recipes = [line for block in shell_blocks(GUIDES[1])
                       for line in block.replace('\\\n', '').splitlines()
                       if line.startswith(('mkdir ', 'funcgraph ', 'fgreport ')) and 'build/examples' in line]
            self.assertEqual(len(recipes), 7)
            completed = subprocess.run(['bash', '-eu', '-c', '\n'.join(recipes)], cwd=root,
                                       env=environment, text=True, capture_output=True, timeout=90)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            output = root / 'build/examples'
            kernel = helpers.json_script((output / 'kernel.html').read_text(encoding='utf-8'), 'tlData')
            mixed_html = (output / 'mixed.html').read_text(encoding='utf-8')
            mixed = helpers.json_script(mixed_html, 'tlData')
            wrapped = helpers.json_script((output / 'mixed-wrapper.html').read_text(encoding='utf-8'), 'tlData')
            self.assert_models_equal(mixed, wrapped)
            for model in (kernel, mixed):
                self.assertGreater(model['n'], 0)
                self.assertEqual(invariant_errors(model), [])
            self.assertEqual(mixed['uftrace']['spaces'], ['kernel', 'user'])
            self.assertNotIn('kernel_types', mixed['uftrace'])
            names = [mixed['strings'][index] for index in mixed['cols']['name']]
            line = str(mixed['cols']['line'][names.index('dispatch_datagram')])
            detail = mixed['uftrace']['user_details'][line]
            self.assertEqual(detail['arg_names'], ['fd', 'buffer', 'length', 'peer', 'peer_size'])
            self.assertIn('length=512', detail['args'])
            self.assertEqual(detail['callsite']['name'], 'send_frame')
            symbol = detail['callsite']['symbol']
            self.assertGreaterEqual(symbol['offset'], 0)
            self.assertLess(symbol['offset'], symbol['size'])
            self.assertEqual(int(symbol['address'], 16) + symbol['offset'],
                             int(detail['callsite']['retaddr'], 16) - 1)
            sources = mixed['sources']
            receives = [str(mixed['cols']['line'][index]) for index, name in enumerate(names)
                        if name == 'receive_frame']
            self.assertEqual([sources['user_positions'][sources['user_callsites'][line]][1]
                              for line in receives], [47, 49])
            pool = helpers.json_script(mixed_html, 'fgSrc')
            source_lines = helpers.json_script(mixed_html, 'fgSrcLines')
            for line, number in zip(receives, (47, 49)):
                self.assertIn('demo_mixed.c:' + str(number), pool[source_lines[line]])
                self.assertNotIn('github.com/torvalds/linux', pool[source_lines[line]])
            self.assertEqual(len(re.findall(r'<div class="line-container [^>]+data-boundary="user-kernel"',
                                            mixed_html)), 5)
            comparison = helpers.json_script((output / 'compare.html').read_text(encoding='utf-8'), 'cmpData')
            self.assertTrue(comparison['rows'])
            sidecar = output / 'mixed.report'
            index = (sidecar / 'index.html').read_text(encoding='utf-8')
            descriptor = helpers.json_script(index, 'fgDataManifest')
            metadata = descriptor['manifest']
            manifest_path = sidecar / metadata['file']
            payload = manifest_path.read_bytes()
            self.assertEqual(len(payload), metadata['bytes'])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), metadata['sha256'])
            manifest = json.loads(payload)
            resources = {}
            for name, resource in manifest['resources'].items():
                payload = (manifest_path.parent / resource['file']).read_bytes()
                self.assertEqual(len(payload), resource['bytes'])
                self.assertEqual(hashlib.sha256(payload).hexdigest(), resource['sha256'])
                resources[name] = payload
            self.assert_models_equal(json.loads(resources['tlData']), mixed)
            sidecar_pool = [source for name, payload in resources.items()
                            if re.fullmatch(r'fgSrc\d+', name) for source in json.loads(payload)]
            self.assertEqual(sidecar_pool, pool)
            automatic = (output / 'kernel-auto.report/index.html').read_text(encoding='utf-8')
            self.assertIsNone(helpers.json_script(automatic, 'fgDataManifest'))
            self.assert_models_equal(helpers.json_script(automatic, 'tlData'), kernel)

    def assert_models_equal(self, before, after):
        self.assertEqual(before.keys(), after.keys())
        for name in before:
            with self.subTest(field=name):
                if name == 'stats':
                    self.assertEqual({key: value for key, value in before[name].items() if key != 'build_time'},
                                     {key: value for key, value in after[name].items() if key != 'build_time'})
                else:
                    self.assertEqual(before[name], after[name])

    def test_preset_recipe_keeps_kernel_and_user_sources_separate(self):
        with tempfile.TemporaryDirectory(prefix='fg-preset-example-') as directory:
            root = Path(directory) / 'kernel with spaces'
            root.mkdir()
            image = root / 'vmlinux'
            image.touch()
            config = root / 'config.ini'
            environment = dict(os.environ, FG_CONFIG=str(config), VMLINUX=str(image), KERNEL_SRC=str(root),
                               KERNEL_REV='recorded-kernel-commit', KERNEL_WEB_REV='upstream-baseline')
            environment.pop('FG_PRESET', None)
            block = next(block for block in shell_blocks(GUIDES[1]) if block.startswith('fgreport init '))
            recipes = [line for line in block.replace('\\\n', '').splitlines()
                       if line.startswith(('fgreport init ', 'fgreport presets', 'fgreport -n '))]
            self.assertEqual(len(recipes), 3)
            launcher = shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'fgreport'))
            result = subprocess.run(['bash', '-eu', '-c', '\n'.join(
                line.replace('fgreport', launcher, 1) for line in recipes)], cwd=root,
                env=environment, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('source-rev = recorded-kernel-commit', result.stdout)
            self.assertIn('source-browser-rev = upstream-baseline', result.stdout)
            self.assertNotIn('* target-kernel', result.stdout)
            command = shlex.split(next(line[len('fgreport: '):] for line in result.stderr.splitlines()
                                      if line.startswith('fgreport: ')))
            self.assertEqual(command[command.index('--preset') + 1], 'target-kernel')
            self.assertEqual(command[command.index('--uftrace') + 1], 'mixed.data')
            self.assertEqual(command[command.index('--user-src') + 1], str(root))
            self.assertIn('--func-links', command)


if __name__ == '__main__':
    unittest.main()
