import hashlib
import io
import json
import os
import shlex
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import helpers
from fglib.output import ReportSpool
from fglib.sidecar import MARKER, _copy, _replace, write_sidecar
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class Render:
    def __init__(self, label='报告', no_text=False, compare=True):
        self.label = label
        self.timeline_json = dict(n=2, no_text=no_text)
        self.compare = compare

    def iter_sections(self):
        yield 'chrome', '<!doctype html><script data-default-view="text"></script><div id="content">'
        yield 'text', '<p>' + self.label + '</p>'
        yield 'chrome', '</div>'
        yield 'source_open', '<script type="application/json" id="fgSrc">'
        yield 'source_data', '["source"]'
        yield 'source_close', '</script>'
        yield 'report_script', '<script>const report = true;</script>'
        for name in ['tlData', 'cmpData'] if self.compare else ['tlData']:
            yield 'view_open:' + name, '<script type="application/json" id="' + name + '">'
            yield 'view_data:' + name, json.dumps(dict(name=self.label, count=2), ensure_ascii=False)
            yield 'view_close:' + name, '</script>'
        yield 'end', '<footer>end</footer>'


class SidecarTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fgsidecar_')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'report'

    def resources(self, html=None):
        html = html or (self.output / 'index.html').read_text(encoding='utf-8')
        descriptor = helpers.json_script(html, 'fgDataManifest')
        manifest_path = self.output / descriptor['manifest']['file']
        payload = manifest_path.read_bytes()
        self.assertEqual(len(payload), descriptor['manifest']['bytes'])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), descriptor['manifest']['sha256'])
        manifest = json.loads(payload)
        self.assertEqual(manifest['report_id'], descriptor['report_id'])
        result = {}
        for name, resource in manifest['resources'].items():
            payload = (manifest_path.parent / resource['file']).read_bytes()
            self.assertEqual(len(payload), resource['bytes'])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), resource['sha256'])
            result[name] = payload
        return result

    def test_bundle_has_exact_utf8_resources_and_preserves_embedded_sources(self):
        render = Render()
        full = ''.join(fragment for name, fragment in render.iter_sections()).encode('utf-8')
        written = write_sidecar(self.output, render)
        html = (self.output / 'index.html').read_text(encoding='utf-8')
        resources = self.resources(html)
        self.assertEqual(resources['fgText'], '<p>报告</p>'.encode('utf-8'))
        self.assertEqual(json.loads(resources['tlData']), dict(name='报告', count=2))
        self.assertEqual(json.loads(resources['cmpData']), dict(name='报告', count=2))
        self.assertEqual(helpers.json_script(html, 'fgSrc'), ['source'])
        self.assertIsNone(helpers.json_script(html, 'tlData'))
        self.assertNotIn('<p>报告</p>', html)
        self.assertIn('data-fg-text="true"', html)
        self.assertIn('data-default-view="timeline" data-deferred-text="true"', html)
        self.assertLess(html.index('id="fgDataManifest"'), html.index('const report'))
        self.assertEqual(written.single_file_bytes, len(full))
        self.assertEqual(written.byte_count, sum(path.stat().st_size for path in self.output.rglob('*') if path.is_file()))
        self.assertEqual((self.output / '.funcgraph-sidecar').read_bytes(), MARKER)
        self.assertEqual(list(self.root.glob('.funcgraph-*')), [])

    def test_omitted_text_is_not_exported_or_enabled_by_sidecar(self):
        write_sidecar(self.output, Render(no_text=True, compare=False))
        html = (self.output / 'index.html').read_text(encoding='utf-8')
        self.assertEqual(set(self.resources(html)), {'tlData'})
        self.assertNotIn('data-fg-text=', html)
        self.assertIn('<p>报告</p>', html)

    def test_existing_empty_directory_is_published_as_a_complete_report(self):
        self.output.mkdir()
        write_sidecar(self.output, Render())
        self.assertIn('fgText', self.resources())

    def test_update_keeps_old_resources_available_and_preserves_entry_mode(self):
        write_sidecar(self.output, Render('old'))
        old = (self.output / 'index.html').read_text(encoding='utf-8')
        (self.output / 'index.html').chmod(0o600)
        write_sidecar(self.output, Render('new'))
        self.assertIn(b'new', self.resources()['fgText'])
        self.assertIn(b'old', self.resources(old)['fgText'])
        self.assertEqual(stat.S_IMODE((self.output / 'index.html').stat().st_mode), 0o600)
        self.assertEqual(list(self.root.glob('.funcgraph-*')), [])

    def test_unmanaged_nonempty_directories_files_and_symlinks_are_never_replaced(self):
        self.output.mkdir()
        (self.output / 'private.txt').write_text('secret')
        with self.assertRaisesRegex(OSError, 'unmanaged'):
            write_sidecar(self.output, Render())
        alias = self.root / 'alias'
        alias.symlink_to(self.output)
        with self.assertRaisesRegex(OSError, 'symlink'):
            write_sidecar(alias, Render())
        with self.assertRaisesRegex(OSError, 'not a directory'):
            write_sidecar(self.output / 'private.txt', Render())
        self.assertEqual((self.output / 'private.txt').read_text(), 'secret')

    def test_render_and_sync_failures_leave_no_new_report(self):
        class Broken(Render):
            def iter_sections(self):
                yield 'chrome', 'partial'
                raise ValueError('render failed')

        with self.assertRaisesRegex(ValueError, 'render failed'):
            write_sidecar(self.output, Broken())
        with mock.patch('fglib.sidecar.os.fsync', side_effect=OSError('sync failed')):
            with self.assertRaisesRegex(OSError, 'sync failed'):
                write_sidecar(self.output, Render())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_entry_publication_preserves_the_previous_complete_bundle(self):
        write_sidecar(self.output, Render('old'))
        previous = (self.output / 'index.html').read_bytes()
        entries = set(self.output.iterdir())
        replace = os.replace

        def fail_entry(source, destination):
            if Path(destination) == self.output / 'index.html':
                raise OSError('entry failed')
            replace(source, destination)

        with mock.patch('fglib.sidecar.os.replace', side_effect=fail_entry):
            with self.assertRaisesRegex(OSError, 'entry failed'):
                write_sidecar(self.output, Render('new'))
        self.assertEqual((self.output / 'index.html').read_bytes(), previous)
        self.assertEqual(set(self.output.iterdir()), entries)
        self.assertIn(b'old', self.resources()['fgText'])
        self.assertEqual(list(self.root.glob('.funcgraph-*')), [])

    def test_interruption_after_entry_rename_does_not_delete_committed_resources(self):
        write_sidecar(self.output, Render('old'))
        replace = os.replace

        def interrupt_after_entry(source, destination):
            replace(source, destination)
            if Path(destination) == self.output / 'index.html':
                raise KeyboardInterrupt

        with mock.patch('fglib.sidecar.os.replace', side_effect=interrupt_after_entry):
            with self.assertRaises(KeyboardInterrupt):
                write_sidecar(self.output, Render('new'))
        self.assertIn(b'new', self.resources()['fgText'])

    def test_sidecar_requires_a_timeline(self):
        render = Render()
        render.timeline_json = None
        with self.assertRaisesRegex(ValueError, '--timeline'):
            write_sidecar(self.output, render)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_rejects_invalid_modes_without_changing_the_destination(self):
        self.output.write_text('previous report')
        config = self.root / 'invalid.ini'
        config.write_text('[default]\nreport-mode = invalid\n')
        for options in [('--no-config', '--report-mode', 'sidecar'), ('--config', str(config))]:
            with self.subTest(options=options):
                result = subprocess.run([sys.executable, str(Path(helpers.ROOT) / 'funcgraph.py'),
                                         *options, '--output', str(self.output), helpers.fixture('pi4_sched')],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('report', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(self.output.read_text(), 'previous report')

    def test_cli_prints_shell_safe_viewer_command_and_preserves_unmanaged_files(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        destination = 'report \'"$HOME'
        _, result = kernel.run(helpers.fixture('pi4_sched'), '--timeline', '--report-mode', 'sidecar', out=destination)
        command = next(line[len('Open with: '):] for line in result.stdout.splitlines() if line.startswith('Open with: '))
        self.assertEqual(shlex.split(command), ['fgview', str(Path(kernel.dir) / destination)])
        protected = self.root / 'unmanaged'
        protected.mkdir()
        (protected / 'private.txt').write_text('private')
        result = subprocess.run([sys.executable, str(Path(helpers.ROOT) / 'funcgraph.py'), '--no-config',
                                 '--timeline', '--report-mode', 'sidecar', '--output', str(protected),
                                 helpers.fixture('pi4_sched')], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unmanaged', result.stderr)
        self.assertEqual((protected / 'private.txt').read_text(), 'private')

    def test_streaming_replacements_match_bytes_replace_at_every_boundary(self):
        for payload in [b'ababa', b'content <div id="content"> suffix', b'aaaaabaaaa', b'', b'ab']:
            for before, after in [(b'aba', b'long replacement'), (b'a', b''), (b'<div id="content">', b'<div id="content" data-fg-text="true">')]:
                for size in range(1, len(payload) + 2):
                    with self.subTest(payload=payload, before=before, size=size):
                        blocks = [payload[index:index + size] for index in range(0, len(payload), size)]
                        self.assertEqual(b''.join(_replace(blocks, before, after)), payload.replace(before, after))

    def test_large_section_copies_use_bounded_reads_and_detect_short_writes(self):
        with ReportSpool(self.root / 'spool.html') as spool:
            spool.write_sections([('text', '中文' * 400000)])
            spool.file.flush()
            original = spool.file
            observed = []

            class Reader:
                def seek(self, position):
                    return original.seek(position)

                def read(self, length):
                    observed.append(length)
                    return original.read(length)

            spool.file = Reader()
            try:
                output = io.BytesIO()
                _copy(spool, spool.sections[0], output)
                self.assertEqual(len(output.getvalue()), spool.byte_count)
                self.assertLessEqual(max(observed), 1024 * 1024)
                short = mock.Mock()
                short.write.return_value = 0
                with self.assertRaisesRegex(OSError, 'short write'):
                    _copy(spool, spool.sections[0], short)
            finally:
                spool.file = original


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class SidecarBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.kernel.run(helpers.fixture('pi4_sched'), '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--compare', helpers.fixture('pi4_default'), '--report-mode', 'sidecar', out='report')
        cls.directory = Path(cls.kernel.dir) / 'report'
        cls.server = create_server(cls.directory)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        except Exception as error:
            cls.server.shutdown()
            cls.thread.join(timeout=5)
            cls.server.server_close()
            cls.playwright.stop()
            cls.kernel.close()
            raise unittest.SkipTest(str(error))
        descriptor = helpers.json_script((cls.directory / 'index.html').read_text(), 'fgDataManifest')
        cls.manifest = cls.directory / descriptor['manifest']['file']
        cls.resources = json.loads(cls.manifest.read_text())['resources']

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
        cls.kernel.close()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
        self.errors = []
        self.requests = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda request: self.requests.append(request.url))
        self.page.add_init_script("window.violations = []; document.addEventListener('securitypolicyviolation', event => violations.push(event.violatedDirective))")
        self.page.goto(self.server.url, wait_until='load')
        self.page.locator('#timelineView .tl-canvas').wait_for(state='attached')

    def tearDown(self):
        try:
            self.assertEqual(self.errors, [])
            self.assertEqual(self.page.evaluate('window.violations'), [])
        finally:
            self.page.close()

    def test_initial_timeline_does_not_fetch_text_or_compare_or_create_text_rows(self):
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertFalse(any(url.endswith(('text.html', 'compare.json')) for url in self.requests))
        self.assertGreater(self.page.evaluate('fgView("timeline").probe().N'), 0)
        self.assertTrue(self.page.evaluate('fgTextDeferred()'))
        self.page.evaluate('''() => {
            const data = fgData.read('tlData');
            fgView('timeline').probe().select(data.cols.parent.findIndex(parent => parent >= 0));
        }''')
        locations = self.page.locator('[data-context-list=stack] .tl-context-source').all_text_contents()
        self.assertIn('kernel/fake.c:2', ''.join(locations))
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertFalse(any(url.endswith(('text.html', 'compare.json')) for url in self.requests))

    def test_text_initializes_once_and_preserves_sources_folds_filters_and_reentry(self):
        self.page.locator('[data-view=text]').click()
        self.page.locator('#content .fold-icon').first.wait_for()
        count = self.page.locator('#content .line-container').count()
        self.assertGreater(count, 100)
        self.assertFalse(self.page.evaluate('fgData.ready("fgText")'))
        icon = self.page.locator('.fold-icon').first
        icon.press('Enter')
        self.assertEqual(icon.get_attribute('aria-expanded'), 'false')
        icon.press('Enter')
        self.assertEqual(icon.get_attribute('aria-expanded'), 'true')
        self.page.locator('.line-container.expandable .line-content').first.click()
        self.page.locator('.expanded-content .location-link').first.wait_for()
        self.page.locator('#filterInput').fill('duration:>1')
        self.page.locator('#filterInput').press('Enter')
        self.assertEqual(self.page.evaluate('fgFilterState.text'), 'duration:>1')
        for name in ['timeline', 'text', 'compare', 'text']:
            self.page.locator('[data-view=' + name + ']').click()
        self.assertEqual(self.page.locator('#content .line-container').count(), count)
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 1)

    def test_timeline_unified_filter_loads_the_complete_text_without_switching_views(self):
        self.page.locator('.tl-ftext').fill('duration:>1')
        self.page.locator('.tl-ftext').press('Enter')
        self.page.locator('.tl-fcount').filter(has_text='of').wait_for()
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)
        self.assertIn('of', self.page.locator('.tl-fcount').inner_text())
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 1)

    def test_hash_navigation_loads_text_before_locating_a_real_line(self):
        self.page.goto(self.server.url + '#L25', wait_until='load')
        self.page.locator('#L25.keyboard-selected').wait_for()
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)

    def test_text_failure_is_retryable_without_partial_rows(self):
        self.page.route('**/text.html', lambda route: route.fulfill(status=404, body='missing'))
        self.page.locator('[data-view=text]').click()
        self.page.locator('#content [role=alert]').wait_for()
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertIn('HTTP 404', self.page.locator('#content [role=alert]').inner_text())
        self.page.unroute('**/text.html')
        self.page.locator('#content button', has_text='Retry').click()
        self.page.locator('#content .fold-icon').first.wait_for()
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)

    def test_text_initialization_failure_requires_reload_instead_of_reusing_partial_state(self):
        self.page.evaluate('''() => {
            initKeyboardNavigation = () => { throw new Error('injected Text initialization failure'); };
        }''')
        self.page.locator('[data-view=text]').click()
        self.page.locator('#content [role=alert]').wait_for()
        self.assertIn('initialization failure', self.page.locator('#content [role=alert]').inner_text())
        self.assertTrue(self.page.evaluate('fgTextDeferred()'))
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertIsNone(self.page.evaluate('fgEnsureText()'))
        for view in ['timeline', 'text']:
            self.page.locator('[data-view=' + view + ']').click()
        self.page.locator('#content button', has_text='Reload report').click()
        self.page.locator('#content .fold-icon').first.wait_for()
        icon = self.page.locator('.fold-icon').first
        icon.press('Enter')
        self.assertEqual(icon.get_attribute('aria-expanded'), 'false')

    def test_clearing_a_pending_filter_does_not_apply_or_report_a_cancelled_filter(self):
        routes = []
        self.page.route('**/text.html', lambda route: routes.append(route))
        self.page.locator('.tl-ftext').fill('duration:>1')
        with self.page.expect_request('**/text.html'):
            self.page.locator('.tl-ftext').press('Enter')
        self.page.locator('[data-act=fclear]').click()
        payload = (self.manifest.parent / self.resources['fgText']['file']).read_bytes()
        routes[0].fulfill(status=200, body=payload)
        self.page.locator('#content .line-container').first.wait_for(state='attached')
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
        self.assertNotIn('not applied', self.page.locator('body').inner_text())

    def test_cancelled_text_fetch_cannot_mount_after_returning_to_timeline(self):
        routes = []
        self.page.route('**/text.html', lambda route: routes.append(route))
        with self.page.expect_request('**/text.html'):
            self.page.locator('[data-view=text]').click()
        self.page.keyboard.press('Escape')
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
        self.assertTrue(routes)
        payload = (self.manifest.parent / self.resources['fgText']['file']).read_bytes()
        routes[0].fulfill(status=200, body=payload)
        self.page.unroute('**/text.html')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertTrue(self.page.evaluate('fgTextDeferred()'))
        self.page.locator('[data-view=text]').click()
        self.page.locator('#content .fold-icon').first.wait_for()

    def test_timeline_jump_loads_text_and_reveals_the_original_trace_line(self):
        self.page.evaluate('fgView("timeline").probe().select(0)')
        link = self.page.locator('.tl-detail [data-line]').first
        number = link.get_attribute('data-line')
        link.click()
        self.page.locator('#L' + number + '.tl-flash').wait_for()
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)

    def test_repeated_text_open_shares_the_request_and_restores_saved_text_on_reload(self):
        self.assertTrue(self.page.evaluate('fgSwitchView("text") === fgSwitchView("text")'))
        self.page.locator('#content .fold-icon').first.wait_for()
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 1)
        self.page.locator('#textOrderBtn').click()
        self.page.reload(wait_until='load')
        self.page.locator('#content .task-group-head').first.wait_for()
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
        self.assertEqual(self.page.locator('#textOrderBtn').inner_text(), 'Order: by task')
