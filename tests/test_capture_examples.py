"""Check published capture recipes and execute the shipped user demo without root."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

import helpers
from fglib import capture
from test_timeline import invariant_errors
from test_uftrace_export import AVAILABLE, COMPILE_FLAGS, EXECUTABLE


ROOT = Path(helpers.ROOT)
GUIDES = ('docs/capture.cn.md', 'docs/capture.md', 'docs/pigz.cn.md', 'docs/pigz.md')


def shell_blocks(path):
    return re.findall(r'^```bash\n(.*?)^```', path.read_text(encoding='utf-8'), re.M | re.S)


class CaptureDocumentationTest(unittest.TestCase):
    def test_all_capture_case_shell_blocks_have_valid_syntax(self):
        for filename in GUIDES:
            blocks = shell_blocks(ROOT / filename)
            self.assertTrue(blocks, filename)
            for index, block in enumerate(blocks):
                with self.subTest(file=filename, block=index):
                    result = subprocess.run(['bash', '-n'], input=block, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_user_mixed_recipes_are_accepted_by_the_actual_argument_parser(self):
        for filename in GUIDES:
            examples = 0
            for block in shell_blocks(ROOT / filename):
                for line in block.replace('\\\n', '').splitlines():
                    prefix = re.match(r'^(?:fgcapture|sudo "\$\(command -v fgcapture\)") (.*)$', line)
                    if not prefix:
                        continue
                    arguments = shlex.split(prefix[1])
                    if '--user' not in arguments and '--mixed' not in arguments:
                        continue
                    with self.subTest(file=filename, command=line):
                        parsed = capture.parser().parse_args(arguments)
                        self.assertEqual(parsed.command[0], '--')
                        self.assertTrue(parsed.command[1])
                        self.assertTrue(parsed.output)
                        self.assertTrue(parsed.args)
                        examples += 1
            self.assertGreaterEqual(examples, 2, filename)

    def test_capture_case_local_links_resolve(self):
        for filename in GUIDES:
            document = ROOT / filename
            targets = re.findall(r'\[[^\]\n]+\]\(([^)\s]+)\)', document.read_text(encoding='utf-8'))
            for target in targets:
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                with self.subTest(file=filename, target=target):
                    self.assertTrue((document.parent / unquote(parsed.path)).is_file())


@unittest.skipUnless(AVAILABLE, 'patched uftrace and gcc are required')
class NativeCaptureExampleTest(unittest.TestCase):
    def test_demo_roundtrip_named_arguments_callers_and_both_import_paths(self):
        with tempfile.TemporaryDirectory(prefix='fg-capture-example-') as directory:
            root = Path(directory) / 'case with spaces'
            root.mkdir()
            source_directory = root / 'samples'
            source_directory.mkdir()
            source = source_directory / 'demo_mixed.c'
            shutil.copyfile(ROOT / 'samples/demo_mixed.c', source)
            binary = root / 'demo_mixed'
            environment = dict(os.environ, FG_CONFIG=str(root / 'absent.ini'))
            compiler = next(line for block in shell_blocks(ROOT / 'docs/capture.md')
                            for line in block.splitlines() if line.startswith('cc '))
            compiler = compiler.replace('-pg', ' '.join(COMPILE_FLAGS))
            subprocess.run(['bash', '-eu', '-c', compiler], cwd=root, check=True, capture_output=True)
            original = root / 'input.bin'
            original.write_bytes(bytes(range(256)) * 4)
            output = root / 'output.bin'
            output.touch()
            data = root / 'user.data'
            recorded = subprocess.run([sys.executable, str(ROOT / 'fgcapture'), '--user',
                '--uftrace-bin', str(EXECUTABLE.resolve()), '-o', str(data), '--', str(binary),
                str(original), str(output)], cwd=root, env=environment, text=True, capture_output=True, timeout=30)
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            self.assertEqual(output.read_bytes(), original.read_bytes())
            evidence = root / 'user.data.fgcapture'
            invocation = json.loads((evidence / 'command.json').read_text())
            self.assertIn('--auto-args', invocation)
            self.assertIn('.@callsite', invocation)
            self.assertFalse((evidence / 'tracefs-before.json').exists())
            exported = subprocess.run([str(EXECUTABLE.resolve()), 'dump', '--fgraph', '--srcline',
                '-d', str(data)], cwd=root, env=environment, text=True, capture_output=True, timeout=30)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            records = [json.loads(line) for line in exported.stdout.splitlines()]
            self.assertEqual(records[-1], {'type': 'end', 'complete': True})
            self.assertFalse(any(record['type'] == 'lost' for record in records))
            dispatch = next(record for record in records if
                            record.get('name') == 'dispatch_datagram' and record['type'] == 'entry')
            self.assertEqual(dispatch['user_arg_names'], ['fd', 'buffer', 'length', 'peer', 'peer_size'])
            self.assertEqual(dispatch['user_args'][2], '512')
            self.assertEqual(len(dispatch['user_args']), 5)
            self.assertEqual([record['user_retval'] for record in records if
                              record.get('name') == 'receive_frame' and record['type'] == 'exit'], ['512', '-1'])
            trace = root / 'user.jsonl'
            trace.write_text(exported.stdout, encoding='utf-8')
            reports = []
            for mode, arguments in [('directory', ['--uftrace', str(data), '--uftrace-bin', str(EXECUTABLE.resolve())]),
                                    ('jsonl', [str(trace)])]:
                report = root / (mode + '.html')
                rendered = subprocess.run([sys.executable, str(ROOT / 'funcgraph.py'), '--no-config',
                    '--timeline', '--enable-fold', '--filter', '--user-src', str(root),
                    '--output', str(report), *arguments], cwd=root, env=environment, text=True,
                    capture_output=True, timeout=30)
                self.assertEqual(rendered.returncode, 0, rendered.stderr)
                html = report.read_text(encoding='utf-8')
                model = helpers.json_script(html, 'tlData')
                sources = model['sources']
                self.assertEqual(invariant_errors(model), [])
                self.assertEqual(model['uftrace']['spaces'], ['user'])
                names = [model['strings'][name] for name in model['cols']['name']]
                detail = model['uftrace']['user_details'][str(model['cols']['line'][names.index('dispatch_datagram')])]
                self.assertEqual(detail['callsite']['name'], 'send_frame')
                symbol = detail['callsite']['symbol']
                self.assertGreaterEqual(symbol['offset'], 0)
                self.assertLess(symbol['offset'], symbol['size'])
                self.assertEqual(int(symbol['address'], 16) + symbol['offset'],
                                 int(detail['callsite']['retaddr'], 16) - 1)
                receives = [str(model['cols']['line'][index]) for index, name in enumerate(names)
                            if name == 'receive_frame']
                positions = [sources['user_positions'][sources['user_callsites'][line]] for line in receives]
                self.assertEqual([position[1] for position in positions], [47, 49])
                self.assertTrue(all('demo_mixed.c' in position[0] for position in positions))
                pool = helpers.json_script(html, 'fgSrc')
                source_lines = helpers.json_script(html, 'fgSrcLines')
                for line, number in zip(receives, (47, 49)):
                    excerpt = pool[source_lines[line]]
                    self.assertIn(f'demo_mixed.c:{number} · user call site', excerpt)
                    self.assertIn('source-line current-line', excerpt)
                    self.assertIn('receive_frame', excerpt)
                    self.assertNotIn('github.com/torvalds/linux', excerpt)
                reports.append(model)
            for field in ('n', 'cols', 'strings', 'uftrace', 'sources'):
                self.assertEqual(reports[0][field], reports[1][field], field)


if __name__ == '__main__':
    unittest.main()
