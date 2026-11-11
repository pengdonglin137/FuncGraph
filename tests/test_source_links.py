"""Built-in online browsers, revision selection, precedence and report links."""
import contextlib
from html.parser import HTMLParser
import io
import os
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

import helpers
from fglib.cli import parse_args
from fglib.resolver import create_source_link
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

    def test_source_names_are_literal_url_paths_not_queries_fragments_or_escapes(self):
        path = 'drivers/space dir:123/percent%2F-#?&"雪.c'
        encoded = 'drivers/space%20dir%3A123/percent%252F-%23%3F%26%22%E9%9B%AA.c'
        for base, anchor in [(source_browser_url('github', 'topic/fix'), 'L42'),
                             (source_browser_url('elixir', 'v7.2'), 'L42'),
                             ('https://opengrok.test/xref/custom/', '42')]:
            with self.subTest(base=base):
                result = build_source_url(base, path, 42)
                self.assertEqual(result, base.rstrip('/') + '/' + encoded + '#' + anchor)
                parsed = urlsplit(result)
                self.assertEqual(parsed.query, '')
                self.assertEqual(parsed.fragment, anchor)
                self.assertTrue(unquote(parsed.path).endswith('/' + path))

    def test_function_link_title_cannot_turn_source_names_into_attributes(self):
        class Links(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    self.attributes = dict(attrs)

        path = 'drivers/a" onmouseover="bad()" data-other="file.c'
        parser = Links()
        parser.feed(create_source_link('/tree/' + path, 42, 'work',
                                       'https://example.org/src', '/tree', None))
        self.assertEqual(set(parser.attributes), {'class', 'href', 'target', 'title', 'onclick'})
        self.assertEqual(parser.attributes['title'], 'Click to open ' + path + ':42')
        self.assertEqual(urlsplit(parser.attributes['href']).fragment, 'L42')
        self.assertEqual(unquote(urlsplit(parser.attributes['href']).path), '/src/' + path)


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
    def test_literal_source_names_match_across_function_links_source_blocks_and_timeline(self):
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []

            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    self.links.append(dict(attrs))

        path = 'kernel/space dir/version:123/percent%2F-#?&"雪.c'
        for listing in [False, True]:
            with self.subTest(listing=listing):
                kernel = helpers.FakeKernel(inline_source=True, listing=listing, source_file=path)
                self.addCleanup(kernel.close)
                report, _ = kernel.run(helpers.fixture('vm72_full'), '--no-config', '--no-btf',
                                       '--timeline', '--enable-fold', '--func-links',
                                       '--base-url', 'https://example.org/src')
                functions = Links()
                functions.feed(report)
                links = [link for link in functions.links if link.get('class') == 'func-name-link']
                self.assertGreater(len(links), 0)
                pool = Links()
                pool.feed(''.join(helpers.json_script(report, 'fgSrc')))
                self.assertGreater(len(pool.links), 0)
                for link in links + pool.links:
                    parsed = urlsplit(link['href'])
                    self.assertEqual(unquote(parsed.path), '/src/' + path)
                    self.assertEqual(parsed.query, '')
                    self.assertIn(parsed.fragment, ['L2', 'L3'])
                timeline = helpers.json_script(report, 'tlData')
                positions = list(timeline['sources']['functions'].values())
                positions += [frame[1:] for chain in timeline['sources']['callers'].values() for frame in chain]
                self.assertGreater(len(positions), 0)
                for source_path, number, url in positions:
                    self.assertEqual(source_path, path)
                    self.assertEqual(unquote(urlsplit(url).path), '/src/' + path)
                    self.assertEqual(urlsplit(url).fragment, 'L' + str(number))

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
