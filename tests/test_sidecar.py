import gzip
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

    def test_auto_threshold_uses_strict_complete_utf8_bytes_and_renders_once(self):
        class CountedRender(Render):
            def iter_sections(self):
                self.iterations = getattr(self, 'iterations', 0) + 1
                yield from super().iter_sections()

        full = ''.join(fragment for name, fragment in Render().iter_sections()).encode('utf-8')
        self.assertGreater(len(full), len(full.decode('utf-8')))
        for threshold, mode in [(len(full) + 1, 'single'), (len(full), 'single'),
                                (len(full) - 1, 'sidecar'), (0, 'sidecar')]:
            with self.subTest(threshold=threshold):
                render = CountedRender()
                destination = self.root / str(threshold)
                written = write_sidecar(destination, render, byte_limit=threshold)
                self.assertEqual(render.iterations, 1)
                self.assertEqual(written.mode, mode)
                self.assertEqual(written.single_file_bytes, len(full))
                self.assertEqual(written.byte_count, sum(path.stat().st_size for path in destination.rglob('*') if path.is_file()))
                html = (destination / 'index.html').read_bytes()
                if mode == 'single':
                    self.assertEqual(html, full)
                    self.assertEqual(written.resource_bytes, 0)
                    self.assertEqual({path.name for path in destination.iterdir()}, {'index.html', '.funcgraph-sidecar'})
                else:
                    self.assertIn(b'id="fgDataManifest"', html)
                    self.assertGreater(written.resource_bytes, 0)
        self.assertEqual(list(self.root.glob('.funcgraph-*')), [])

    def test_auto_switches_in_both_directions_without_removing_open_page_resources(self):
        write_sidecar(self.output, Render('old'), byte_limit=0)
        old = (self.output / 'index.html').read_text(encoding='utf-8')
        (self.output / 'index.html').chmod(0o600)
        write_sidecar(self.output, Render('offline'), byte_limit=10**9)
        offline = (self.output / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('fgDataManifest', offline)
        self.assertIn(b'old', self.resources(old)['fgText'])
        self.assertEqual(stat.S_IMODE((self.output / 'index.html').stat().st_mode), 0o600)
        write_sidecar(self.output, Render('new'), byte_limit=0)
        self.assertIn(b'new', self.resources()['fgText'])
        self.assertIn(b'old', self.resources(old)['fgText'])
        self.assertEqual(stat.S_IMODE((self.output / 'index.html').stat().st_mode), 0o600)

    def test_auto_entry_failure_preserves_either_previous_mode(self):
        replace = os.replace

        def fail_entry(source, destination):
            if Path(destination) == self.output / 'index.html':
                raise OSError('entry failed')
            replace(source, destination)

        for previous_limit, next_limit in [(0, 10**9), (10**9, 0), (10**9, 10**9)]:
            with self.subTest(previous_limit=previous_limit, next_limit=next_limit):
                write_sidecar(self.output, Render('previous'), byte_limit=previous_limit)
                previous = (self.output / 'index.html').read_bytes()
                entries = set(self.output.iterdir())
                with mock.patch('fglib.sidecar.os.replace', side_effect=fail_entry):
                    with self.assertRaisesRegex(OSError, 'entry failed'):
                        write_sidecar(self.output, Render('failed'), byte_limit=next_limit)
                self.assertEqual((self.output / 'index.html').read_bytes(), previous)
                self.assertEqual(set(self.output.iterdir()), entries)
                self.assertEqual(list(self.root.glob('.funcgraph-*')), [])

    def test_auto_no_text_does_not_restore_omitted_rows_or_filter_index(self):
        for threshold in [0, 10**9]:
            with self.subTest(threshold=threshold):
                written = write_sidecar(self.output, Render(no_text=True), byte_limit=threshold)
                if written.mode == 'sidecar':
                    self.assertEqual(set(self.resources()), {'tlData', 'cmpData'})
                else:
                    html = (self.output / 'index.html').read_text(encoding='utf-8')
                    self.assertIsNotNone(helpers.json_script(html, 'tlData'))
                    self.assertNotIn('data-fg-text=', html)

    def test_invalid_auto_thresholds_do_not_create_a_destination(self):
        for threshold in [-1, True, 1.5, '100']:
            with self.subTest(threshold=threshold), self.assertRaisesRegex(ValueError, 'threshold'):
                write_sidecar(self.output, Render(), byte_limit=threshold)
        self.assertEqual(list(self.root.iterdir()), [])

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

        for threshold in [None, 0, 10**9]:
            with self.subTest(threshold=threshold):
                with self.assertRaisesRegex(ValueError, 'render failed'):
                    write_sidecar(self.output, Broken(), byte_limit=threshold)
                with mock.patch('fglib.sidecar.os.fsync', side_effect=OSError('sync failed')):
                    with self.assertRaisesRegex(OSError, 'sync failed'):
                        write_sidecar(self.output, Render(), byte_limit=threshold)
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

    def test_cli_auto_requires_an_explicit_threshold_and_timeline(self):
        self.output.write_text('previous report')
        config = self.root / 'negative.ini'
        config.write_text('[default]\nreport-mode = auto\ntimeline = true\nsidecar-threshold = -1\n')
        for options in [('--no-config', '--timeline', '--report-mode', 'auto'),
                        ('--no-config', '--report-mode', 'auto', '--sidecar-threshold', '0'),
                        ('--no-config', '--timeline', '--report-mode', 'auto', '--sidecar-threshold', '-1'),
                        ('--config', str(config))]:
            with self.subTest(options=options):
                result = subprocess.run([sys.executable, str(Path(helpers.ROOT) / 'funcgraph.py'),
                                         *options, '--output', str(self.output), helpers.fixture('pi4_sched')],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(self.output.read_text(), 'previous report')
                self.assertFalse(list(self.root.glob('.funcgraph-*')))

    def test_cli_auto_accepts_compressed_and_standard_input_without_line_counting(self):
        trace = Path(helpers.fixture('pi4_sched')).read_text(encoding='utf-8')
        compressed = self.root / 'trace.txt.gz'
        with gzip.open(compressed, 'wt', encoding='utf-8') as stream:
            stream.write(trace)
        for source in ['-', str(compressed)]:
            for threshold, mode in [(0, 'sidecar'), (10**9, 'single')]:
                with self.subTest(source=source, threshold=threshold):
                    output = self.root / ('stdin' if source == '-' else 'gzip') / mode
                    output.parent.mkdir(exist_ok=True)
                    result = subprocess.run([sys.executable, str(Path(helpers.ROOT) / 'funcgraph.py'),
                                             '--no-config', '--timeline', '--report-mode', 'auto',
                                             '--sidecar-threshold', str(threshold), '--output', str(output), source],
                                            input=trace if source == '-' else None, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('Automatic report mode: ' + mode, result.stdout)
                    html = (output / 'index.html').read_text(encoding='utf-8')
                    self.assertEqual('id="fgDataManifest"' in html, mode == 'sidecar')
                    self.assertEqual('id="tlData"' in html, mode == 'single')

    def test_cli_auto_reports_actual_mode_and_obeys_explicit_single_override(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        config = self.root / 'auto.ini'
        config.write_text('[default]\nreport-mode = auto\ntimeline = true\nsidecar-threshold = 0\n')
        for options, mode in [(('--config', str(config)), 'sidecar'),
                              (('--config', str(config), '--sidecar-threshold', str(10**9)), 'single')]:
            with self.subTest(mode=mode):
                html, result = kernel.run(helpers.fixture('pi4_sched'), *options, out='auto-' + mode)
                self.assertIn('Automatic report mode: ' + mode, result.stdout)
                self.assertIn('switches only above threshold', result.stdout)
                self.assertEqual('id="fgDataManifest"' in html, mode == 'sidecar')
                self.assertEqual('Open with: fgview ' in result.stdout, mode == 'sidecar')
                self.assertTrue((Path(kernel.dir) / ('auto-' + mode) / 'index.html').is_file())
        html, result = kernel.run(helpers.fixture('pi4_sched'), '--config', str(config),
                                  '--report-mode', 'single', out='single.html')
        self.assertNotIn('id="fgDataManifest"', html)
        self.assertNotIn('Automatic report mode:', result.stdout)
        self.assertTrue((Path(kernel.dir) / 'single.html').is_file())
        html, result = kernel.run(helpers.fixture('pi4_sched'), '--no-config', '--timeline', out='default.html')
        self.assertNotIn('Sidecar Threshold:', html)
        self.assertNotIn('Report Mode:', html)
        self.assertTrue((Path(kernel.dir) / 'default.html').is_file())

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
                       '--compare', helpers.fixture('pi4_default'), '--report-mode', 'auto',
                       '--sidecar-threshold', '0', out='report')
        cls.kernel.run(helpers.fixture('pi4_sched'), '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--report-mode', 'auto', '--sidecar-threshold', str(10**9), out='offline')
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
        self.assertFalse(any(url.endswith(('text.html', 'compare.json', 'text-index.json')) for url in self.requests))
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

    def test_auto_small_report_remains_offline_and_keeps_full_text(self):
        self.requests.clear()
        self.page.goto((Path(self.kernel.dir) / 'offline' / 'index.html').as_uri(), wait_until='load')
        self.page.locator('#content .fold-icon').first.wait_for(state='attached')
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)
        self.assertFalse(self.page.evaluate('fgTextDeferred()'))
        self.page.locator('[data-view=timeline]').click()
        self.page.locator('#timelineView .tl-canvas').wait_for(state='attached')
        self.assertGreater(self.page.evaluate('fgView("timeline").probe().N'), 0)
        self.assertFalse(any(url.endswith(('.json', 'text.html')) for url in self.requests))
        self.assertEqual(self.page.locator('#fgDataManifest').count(), 0)

    def test_text_initializes_once_and_preserves_sources_folds_filters_and_reentry(self):
        self.page.locator('[data-view=text]').click()
        self.page.locator('#content .fold-icon').first.wait_for(state='attached')
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

    def test_timeline_unified_filter_uses_all_rows_without_loading_text_or_switching_views(self):
        self.page.locator('.tl-ftext').fill('duration:>1')
        self.page.locator('.tl-ftext').press('Enter')
        self.page.locator('.tl-fcount').filter(has_text='of').wait_for()
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertIn('of', self.page.locator('.tl-fcount').inner_text())
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 0)
        self.assertEqual(sum(url.endswith('text-index.json') for url in self.requests), 1)
        self.assertGreater(self.page.evaluate('fgFilterState.lines.size'), 0)

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
        self.page.route('**/text-index.json', lambda route: routes.append(route))
        self.page.locator('.tl-ftext').fill('duration:>1')
        with self.page.expect_request('**/text-index.json'):
            self.page.locator('.tl-ftext').press('Enter')
        self.page.locator('[data-act=fclear]').click()
        payload = (self.manifest.parent / self.resources['fgTextIndex']['file']).read_bytes()
        routes[0].fulfill(status=200, body=payload)
        self.page.evaluate('async () => { await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); }')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
        self.assertNotIn('not applied', self.page.locator('body').inner_text())

    def apply_query(self, query):
        return self.page.evaluate('''async query => {
            document.getElementById('filterInput').value = query;
            await applyFilter();
            return fgFilterState.lines === null ? null : Array.from(fgFilterState.lines).sort((left, right) => left - right);
        }''', query)

    def test_index_matches_native_text_attributes_content_and_all_filter_fields(self):
        index = self.page.evaluate('async () => await fgData.load("fgTextIndex")')
        queries = ['', 'cpu:0', 'pid:131108', 'comm:sh', 'func:mutex', 'duration:>1',
                   'duration:<=0.5', 'ret:all', 'ret:-2', 'params:0x', 'irqs:d', 'resched:N',
                   'hirq:h', 'preempt:>1', 'preempt:1', 'graph:do_sys_openat2',
                   'notrace:getname_flags', 'graph:missing', 'notrace:missing', 'func:[',
                   '(comm:sh OR cpu:1) AND NOT duration:>10', 'graph:do_sys_openat2 AND ret:all',
                   'NOT graph:getname_flags OR duration:>10', 'unknown:value', 'NOT unknown:value',
                   'duration:>1 sort:desc']
        indexed = {}
        for query in queries:
            with self.subTest(query=query):
                indexed[query] = self.apply_query(query)
                self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        saved = indexed[queries[-1]]
        self.page.locator('[data-view=text]').click()
        self.page.locator('#content .fold-icon').first.wait_for(state='attached')
        self.assertEqual(self.page.evaluate('Array.from(fgFilterState.lines).sort((left, right) => left - right)'), saved)
        self.assertFalse(self.page.evaluate('fgData.ready("fgTextIndex")'))
        actual = self.page.evaluate('''fields => Array.from(document.querySelectorAll('#content .line-container'), line => [
            +line.getAttribute('data-line-number'), line.querySelector('.line-content').textContent,
            ...fields.map(field => line.getAttribute('data-' + field))])''', index['fields'])
        self.assertEqual(index['rows'], actual)
        for query in queries:
            with self.subTest(native_query=query):
                self.assertEqual(self.apply_query(query), indexed[query])

    def test_filter_index_failure_retains_previous_result_and_retries_without_text(self):
        previous = self.apply_query('duration:>1')
        self.page.evaluate('fgData.release("fgTextIndex")')
        self.page.route('**/text-index.json', lambda route: route.fulfill(status=404, body='missing'))
        self.page.locator('.tl-ftext').fill('graph:do_sys_openat2')
        self.page.locator('.tl-ftext').press('Enter')
        status = self.page.locator('.tl-fcount[role=alert]')
        status.wait_for()
        self.assertIn('HTTP 404', status.inner_text())
        self.assertIn('Previous result retained', status.inner_text())
        self.assertEqual(self.page.evaluate('Array.from(fgFilterState.lines).sort((left, right) => left - right)'), previous)
        self.page.unroute('**/text-index.json')
        self.page.locator('[data-act=fretry]').press('Enter')
        self.page.locator('.tl-fcount[role=status]').filter(has_text='of').wait_for()
        self.assertTrue(self.page.locator('.tl-ftext').evaluate('element => element === document.activeElement'))
        self.assertEqual(self.page.evaluate('fgFilterState.text'), 'graph:do_sys_openat2')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 0)

    def test_escape_cancels_a_pending_index_filter_without_clearing_the_previous_result(self):
        previous = self.apply_query('duration:>1')
        self.page.evaluate('fgData.release("fgTextIndex")')
        routes = []
        self.page.route('**/text-index.json', lambda route: routes.append(route))
        self.page.locator('.tl-ftext').fill('graph:do_sys_openat2')
        with self.page.expect_request('**/text-index.json'):
            self.page.locator('.tl-ftext').press('Enter')
        self.page.locator('.tl-ftext').press('Escape')
        payload = (self.manifest.parent / self.resources['fgTextIndex']['file']).read_bytes()
        routes[0].fulfill(status=200, body=payload)
        self.page.evaluate('async () => { await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); }')
        self.assertEqual(self.page.evaluate('Array.from(fgFilterState.lines).sort((left, right) => left - right)'), previous)
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'duration:>1')
        self.assertIsNone(self.page.evaluate('fgFilterProgress'))
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)

    def test_superseded_index_filter_cannot_replace_the_newer_result(self):
        expected = self.apply_query('duration:>1')
        self.page.evaluate('fgData.release("fgTextIndex"); clearFilter()')
        routes = []
        self.page.route('**/text-index.json', lambda route: routes.append(route))
        for query in ['graph:do_sys_openat2', 'duration:>1']:
            self.page.locator('.tl-ftext').fill(query)
            with self.page.expect_request('**/text-index.json'):
                self.page.locator('.tl-ftext').press('Enter')
        payload = (self.manifest.parent / self.resources['fgTextIndex']['file']).read_bytes()
        routes[1].fulfill(status=200, body=payload)
        self.page.wait_for_function('() => fgFilterState.text === "duration:>1" && fgFilterProgress === null')
        routes[0].fulfill(status=200, body=payload)
        self.page.evaluate('async () => { await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); }')
        self.assertEqual(self.page.evaluate('Array.from(fgFilterState.lines).sort((left, right) => left - right)'), expected)
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)

    def test_mounting_text_cancels_pending_index_and_reapplies_the_query_natively(self):
        expected = self.apply_query('duration:>1')
        self.page.evaluate('fgData.release("fgTextIndex"); clearFilter()')
        routes = []
        self.page.route('**/text-index.json', lambda route: routes.append(route))
        self.page.locator('.tl-ftext').fill('duration:>1')
        with self.page.expect_request('**/text-index.json'):
            self.page.locator('.tl-ftext').press('Enter')
        self.page.locator('[data-view=text]').click()
        self.page.wait_for_function('() => !fgTextDeferred() && fgFilterState.text === "duration:>1"')
        payload = (self.manifest.parent / self.resources['fgTextIndex']['file']).read_bytes()
        routes[0].fulfill(status=200, body=payload)
        self.page.evaluate('async () => { await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); }')
        self.assertEqual(self.page.evaluate('Array.from(fgFilterState.lines).sort((left, right) => left - right)'), expected)
        self.assertFalse(self.page.evaluate('fgData.ready("fgTextIndex")'))
        self.assertGreater(self.page.locator('#content .line-container').count(), 0)

    def test_index_schema_validation_rejects_invalid_records(self):
        errors = self.page.evaluate('''async () => {
            const original = await fgData.load('fgTextIndex');
            const mutations = [data => { data.version = 2; }, data => { data.fields.reverse(); },
                data => { data.rows[0][0] = -1; }, data => { data.rows[1][0] = data.rows[0][0]; },
                data => { data.rows[0][2] = {}; }, data => { data.rows[0].pop(); }];
            const results = [];
            for (const mutate of mutations) {
                const data = JSON.parse(JSON.stringify(original));
                mutate(data);
                try {
                    await fgIndexedFilter(data, 'func:mutex', new AbortController().signal, () => {});
                    results.push(null);
                } catch (error) { results.push(error.message); }
            }
            return results;
        }''')
        self.assertEqual(len(errors), 6)
        for error in errors:
            self.assertRegex(error, r'(Unsupported|Invalid) Text filter index')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)

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
