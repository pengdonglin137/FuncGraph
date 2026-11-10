"""Built-in online browsers, revision selection, precedence and report links."""
import contextlib
import io
import os
import tempfile
import unittest

import helpers
from fglib.cli import parse_args
from fglib.util import build_source_url, source_browser_url


class SourceBrowserTest(unittest.TestCase):
    def test_built_in_urls_and_line_anchors(self):
        cases = (
            ('github', None, 'https://github.com/torvalds/linux/blob/master'),
            ('github', 'v7.2', 'https://github.com/torvalds/linux/blob/v7.2'),
            ('elixir', None, 'https://elixir.bootlin.com/linux/latest/source'),
            ('elixir', 'v7.2', 'https://elixir.bootlin.com/linux/v7.2/source'),
        )
        for browser, revision, expected in cases:
            with self.subTest(browser=browser, revision=revision):
                base_url = source_browser_url(browser, revision)
                self.assertEqual(base_url, expected)
                self.assertEqual(build_source_url(base_url, 'kernel/sched/core.c', 42),
                                 expected + '/kernel/sched/core.c#L42')

    def test_revision_is_one_url_component(self):
        self.assertEqual(source_browser_url('github', 'topic/fix#test?x=1'),
                         'https://github.com/torvalds/linux/blob/topic%2Ffix%23test%3Fx%3D1')
        self.assertEqual(source_browser_url('github', '0123456789abcdef'),
                         'https://github.com/torvalds/linux/blob/0123456789abcdef')

    def test_invalid_browser_or_empty_revision(self):
        with self.assertRaisesRegex(ValueError, '--source-browser must be'):
            source_browser_url('invalid')
        for revision in ('', '   '):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, 'must not be empty'):
                source_browser_url('github', revision)

    def test_custom_opengrok_anchor_is_unchanged(self):
        self.assertEqual(build_source_url('https://opengrok.test/xref/linux/', 'kernel/fake.c', 2),
                         'https://opengrok.test/xref/linux/kernel/fake.c#2')


class SourceBrowserCliTest(unittest.TestCase):
    def parse(self, *options):
        return parse_args(['--no-config', *options, 'trace.txt'])

    def test_default_and_explicit_selection(self):
        args = self.parse()
        self.assertEqual(args.source_browser, 'github')
        self.assertEqual(args.base_url, source_browser_url('github'))
        args = self.parse('--source-browser', 'elixir', '--source-browser-rev', 'v7.2')
        self.assertEqual(args.base_url, source_browser_url('elixir', 'v7.2'))

    def test_web_revision_does_not_select_embedded_source(self):
        args = self.parse('--source-browser-rev', 'v7.2')
        self.assertIsNone(args.source_rev)
        self.assertEqual(args.base_url, source_browser_url('github', 'v7.2'))
        args = self.parse('--source-rev', 'local-custom-commit')
        self.assertEqual(args.base_url, source_browser_url('github'))
        self.assertEqual(args.source_rev, 'local-custom-commit')

    def test_explicit_custom_url_wins(self):
        args = self.parse('--source-browser', 'elixir', '--source-browser-rev', 'v7.2',
                          '--base-url', 'https://opengrok.test/xref/custom')
        self.assertEqual(args.base_url, 'https://opengrok.test/xref/custom')

    def test_config_and_command_line_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, 'config.ini')
            with open(config_path, 'w', encoding='utf-8') as output:
                output.write('[default]\nsource-browser = elixir\nsource-browser-rev = v6.18\n'
                             '[preset legacy]\nbase_url = https://custom.test/src\n')
            args = parse_args(['--config', config_path, 'trace.txt'])
            self.assertEqual(args.base_url, source_browser_url('elixir', 'v6.18'))
            args = parse_args(['--config', config_path, '--preset', 'legacy', 'trace.txt'])
            self.assertEqual(args.base_url, 'https://custom.test/src')
            args = parse_args(['--config', config_path, '--preset', 'legacy',
                               '--source-browser', 'github', '--source-browser-rev', 'v7.2', 'trace.txt'])
            self.assertEqual(args.base_url, source_browser_url('github', 'v7.2'))
            args = parse_args(['--config', config_path, '--preset', 'legacy',
                               '--source-browser', 'github', '--base-url', 'https://cli.test/src', 'trace.txt'])
            self.assertEqual(args.base_url, 'https://cli.test/src')

    def test_invalid_selection_from_cli_and_config(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse('--source-browser', 'invalid')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse('--source-browser-rev', '')
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, 'config.ini')
            with open(config_path, 'w', encoding='utf-8') as output:
                output.write('[default]\nsource-browser = invalid\n')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(['--config', config_path, 'trace.txt'])


class SourceBrowserReportTest(unittest.TestCase):
    def test_text_and_timeline_share_selected_links(self):
        kernel = helpers.FakeKernel(inline_source=True)
        self.addCleanup(kernel.close)
        cases = (
            ((), source_browser_url('github')),
            (('--source-browser', 'elixir', '--source-browser-rev', 'v7.2'),
             source_browser_url('elixir', 'v7.2')),
            (('--base-url', 'https://opengrok.test/xref/linux'), 'https://opengrok.test/xref/linux'),
        )
        for options, base_url in cases:
            with self.subTest(options=options):
                report, _ = kernel.run(helpers.fixture('vm72_full'), '--no-config', '--no-btf',
                                       '--timeline', '--enable-fold', *options)
                expected = build_source_url(base_url, 'kernel/fake.c', 3)
                self.assertIn(expected, ''.join(helpers.json_script(report, 'fgSrc')))
                timeline = helpers.json_script(report, 'tlData')
                locations = [frame for chain in timeline['sources']['callers'].values() for frame in chain]
                self.assertTrue(locations)
                self.assertTrue(all(frame[3].startswith(base_url + '/') for frame in locations))

    def test_timeline_only_retains_selected_links(self):
        kernel = helpers.FakeKernel(inline_source=True)
        self.addCleanup(kernel.close)
        report, _ = kernel.run(helpers.fixture('vm72_full'), '--no-config', '--no-btf', '--timeline',
                               '--no-text-view', '--keep-source', '--source-browser-rev', 'v7.2')
        timeline = helpers.json_script(report, 'tlData')
        locations = [frame for chain in timeline['sources']['callers'].values() for frame in chain]
        self.assertTrue(locations)
        self.assertTrue(all(frame[3].startswith(source_browser_url('github', 'v7.2') + '/')
                            for frame in locations))


if __name__ == '__main__':
    unittest.main()
