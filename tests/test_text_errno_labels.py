"""Readable fold error labels use classified metadata without loading Timeline."""
import json
from pathlib import Path
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib.constants import ERROR_CODE_MAP
from fglib.viewer import create_server
from test_btf import make_elf
from test_uftrace_import import stream
from test_uftrace_text_returns import recording, return_types

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


NATIVE = '# tracer: function_graph\n' + ''.join(
    f' 0)               |  {name}() {{ /* <-caller+0x1/0x20 */\n'
    f' 0)   1.000 us    |  }} /* {name} ret={value} */\n'
    for name, value in [('failed', '-2'), ('retry', '-11'), ('pointer', '0xfffffffffffffffe'),
                        ('zero', '0'), ('unknown', '-9999'), ('boolean', 'false')])


class ErrorNameMetadataTest(unittest.TestCase):
    def test_fold_reports_embed_a_small_standalone_errno_dictionary(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        trace = Path(kernel.dir, 'returns.txt')
        trace.write_text(NATIVE)
        expected = {str(key): value for key, value in ERROR_CODE_MAP.items()}
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                options = ['--timeline'] if mode == 'sidecar' else []
                content, _ = kernel.run(str(trace), '--no-config', '--enable-fold', '--report-mode', mode, *options,
                                         out='sidecar' if mode == 'sidecar' else 'report.html')
                values = helpers.json_script(content, 'fgErrnoNames')
                self.assertEqual(values, expected)
                self.assertLess(len(json.dumps(values, separators=(',', ':')).encode()), 3072)
                self.assertIsNone(helpers.json_script(content, 'tlData'))

    def test_no_fold_and_timeline_only_reports_do_not_embed_the_dictionary(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        trace = Path(kernel.dir, 'returns.txt')
        trace.write_text(NATIVE)
        for options in [[], ['--timeline', '--enable-fold', '--no-text-view'],
                        ['--timeline', '--enable-fold', '--no-text-view', '--keep-source']]:
            with self.subTest(options=options):
                content, _ = kernel.run(str(trace), '--no-config', *options)
                self.assertIsNone(helpers.json_script(content, 'fgErrnoNames'))


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class ErrorNameBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        Path(cls.kernel.vmlinux).write_bytes(make_elf({'.BTF': return_types()}))
        native = Path(cls.kernel.dir, 'native.txt')
        native.write_text(NATIVE)
        mixed = Path(cls.kernel.dir, 'mixed.jsonl')
        mixed.write_text(stream(recording()))
        for mode in ('single', 'sidecar'):
            for name, trace in [('native', native), ('mixed', mixed)]:
                cls.kernel.run(str(trace), '--no-config', '--enable-fold', '--timeline', '--report-mode', mode,
                               out=name + ('-sidecar' if mode == 'sidecar' else '.html'))
        cls.kernel.run(str(native), '--no-config', '--enable-fold', out='plain.html')
        Path(cls.kernel.dir, 'index.html').write_text(Path(cls.kernel.dir, 'native.html').read_text())
        cls.server = create_server(Path(cls.kernel.dir))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def open_report(self, filename, theme='light'):
        context = self.browser.new_context(viewport=dict(width=1400, height=900))
        self.addCleanup(context.close)
        context.add_init_script('localStorage.clear()')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, filename) + '#L3', wait_until='load')
        page.evaluate("async theme => { if (typeof fgSwitchView === 'function') await fgSwitchView('text'); document.documentElement.dataset.theme=theme; }", theme)
        return page

    def summary(self, page, name, space=None):
        selector = f'#content [data-func={name}][data-fold-type=entry]'
        if space:
            selector += f'[data-space={space}]'
        row = page.locator(selector)
        row.evaluate("row => _setFolded(row.dataset.foldId, true)")
        return row.locator('.summary-ret')

    def test_native_error_names_keep_original_tokens_and_neutral_returns(self):
        for filename in ('plain.html', 'native.html', 'native-sidecar/index.html', 'native-sidecar/index.html?text=windowed'):
            for theme in ('light', 'dark'):
                with self.subTest(filename=filename, theme=theme):
                    page = self.open_report(filename, theme)
                    for name, expected, error in [('failed', 'ret=-2 (ENOENT)', True),
                                                  ('retry', 'ret=-11 (EAGAIN)', True),
                                                  ('pointer', 'ret=0xfffffffffffffffe (ENOENT)', True),
                                                  ('zero', 'ret=0', False),
                                                  ('unknown', 'ret=-9999', False),
                                                  ('boolean', 'ret=false', False)]:
                        returned = self.summary(page, name)
                        self.assertEqual(returned.inner_text(), expected)
                        self.assertEqual(returned.evaluate("node => node.classList.contains('summary-error')"), error)
                        self.assertEqual(returned.evaluate('''node => getComputedStyle(node).color ===
                            getComputedStyle(node.closest('.fold-summary')).color'''), not error)

    def test_structured_labels_require_kernel_errno_authority_not_just_negative_values(self):
        for filename in ('mixed.html', 'mixed-sidecar/index.html', 'mixed-sidecar/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                for name, space, expected in [('signed32', 'kernel', 'ret=-22 (EINVAL)'),
                                               ('pointer', 'kernel', 'ret=0xffffffffffffffea (EINVAL)'),
                                               ('unsigned', 'kernel', 'ret=0xffffffffffffffff'),
                                               ('memcmp', 'kernel', 'ret=-2'),
                                               ('unknown', 'kernel', 'ret=0xfffffffffffffffe'),
                                               ('signed32', 'user', 'ret=-2'),
                                               ('user_zero', 'user', 'ret=0')]:
                    self.assertEqual(self.summary(page, name, space).inner_text(), expected)

    def test_labels_parse_the_tiny_map_once_and_do_not_load_timeline_or_sources(self):
        for filename in ('native-sidecar/index.html', 'native-sidecar/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                self.assertFalse(page.evaluate("fgData.ready('tlData')"))
                requests = []
                page.on('request', lambda request: requests.append(request.url))
                page.evaluate('''() => {
                    const encoded = document.getElementById('fgErrnoNames').textContent;
                    const parse = JSON.parse;
                    window.errnoParses = 0;
                    JSON.parse = function(value, ...parameters) {
                        if (value === encoded) window.errnoParses++;
                        return parse(value, ...parameters);
                    };
                }''')
                for name in ('failed', 'retry', 'pointer', 'failed'):
                    self.summary(page, name)
                self.assertEqual(page.evaluate('errnoParses'), 1)
                self.assertFalse(page.evaluate("fgData.ready('tlData')"))
                self.assertFalse(page.evaluate("fgData.ready('fgSrc')"))
                self.assertEqual(requests, [])

    def test_legacy_missing_metadata_and_unsafe_names_remain_plain_text(self):
        page = self.open_report('native.html')
        page.locator('#fgErrnoNames').evaluate('node => node.remove()')
        self.assertEqual(self.summary(page, 'failed').inner_text(), 'ret=-2')
        page = self.open_report('native.html')
        unsafe = '<img src=x onerror="window.errnoInjected=1">'
        page.locator('#fgErrnoNames').evaluate('(node, name) => { node.textContent=JSON.stringify({"-2":name}); }', unsafe)
        returned = self.summary(page, 'failed')
        self.assertEqual(returned.inner_text(), 'ret=-2 (' + unsafe + ')')
        self.assertEqual(returned.locator('img').count(), 0)
        self.assertIsNone(page.evaluate('window.errnoInjected'))

    def test_comment_text_cannot_override_the_return_or_errno_authority(self):
        page = self.open_report('native.html')
        page.locator('[data-ret="-2"]').first.locator('.line-content').evaluate(
            "node => { node.textContent += ' /* -11=EAGAIN */'; }")
        self.assertEqual(self.summary(page, 'failed').inner_text(), 'ret=-2 (ENOENT)')
        page.locator('[data-func=failed][data-fold-type=entry]').evaluate("row => _setFolded(row.dataset.foldId, false)")
        page.evaluate("() => { fgData.release('fgErrnoNames'); document.getElementById('fgErrnoNames').remove(); }")
        self.assertEqual(self.summary(page, 'failed').inner_text(), 'ret=-2')
        page = self.open_report('mixed.html')
        page.locator('[data-space=user][data-func=signed32][data-fold-type=exit] .line-content').evaluate(
            "node => { node.textContent += ' /* -2=ENOENT */'; }")
        self.assertEqual(self.summary(page, 'signed32', 'user').inner_text(), 'ret=-2')
