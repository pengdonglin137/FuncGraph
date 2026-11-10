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

    def test_timeline_hover_reads_compact_positions_without_source_or_text_resources(self):
        target = self.page.evaluate('''() => {
            const data = fgData.read('tlData'), probe = fgView('timeline').probe();
            const index = data.cols.caller.findIndex(caller => data.sources.callers[caller]?.length);
            const frame = data.sources.callers[data.cols.caller[index]].slice(-1)[0];
            const read = fgData.read;
            fgData.read = name => {
                if (name === 'fgSrc' || name === 'fgSrcLines') throw new Error('Hover read source HTML');
                return read(name);
            };
            probe.zoomTo(probe.sStart[index], probe.sEnd[index], index);
            return {point:probe.xyOf(index), source:frame[1] + ':' + frame[2]};
        }''')
        self.page.mouse.move(*target['point'])
        self.page.locator('.tl-tip').wait_for(state='visible')
        self.assertIn(target['source'], self.page.locator('.tl-tip').inner_text())
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertFalse(any(url.endswith(('text.html', 'text-index.json', 'compare.json')) for url in self.requests))

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

    def stage_text(self):
        self.page.evaluate('''async () => {
            await fgData.load('fgText');
            window.textChunks = 0;
            window.textClosed = 0;
            window.holdText = true;
            const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
            Object.defineProperty(Element.prototype, 'innerHTML', {...descriptor, set(value) {
                descriptor.set.call(this, value);
                if (this.tagName === 'DIV' && value.startsWith('<div class="text-chunk"')) textChunks++;
            }});
            const original = fgViews.text.steps;
            fgViews.text.steps = function* () {
                const steps = original();
                try {
                    while (true) {
                        const result = steps.next();
                        if (result.done) return result.value;
                        yield result.value;
                        while (holdText && textChunks) yield result.value;
                    }
                } finally { steps.return(); textClosed++; }
            };
        }''')

    def test_text_parses_complete_detached_chunks_without_exposing_partial_rows(self):
        self.page.evaluate('''async () => {
            const html = await fgData.load('fgText');
            const expected = document.createElement('template');
            expected.innerHTML = html;
            window.expectedText = expected.innerHTML;
            window.chunkFacts = [];
            const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
            Object.defineProperty(Element.prototype, 'innerHTML', {...descriptor, set(value) {
                descriptor.set.call(this, value);
                if (this.tagName === 'DIV' && value.startsWith('<div class="text-chunk"')) chunkFacts.push({
                    rows:this.querySelectorAll('.line-container').length,
                    blocks:this.children.length,
                    mounted:document.querySelectorAll('#content .line-container').length,
                    deferred:fgTextDeferred(), inert:document.querySelector('.controls').inert});
            }});
            const original = fgInitText;
            fgInitText = (...args) => {
                window.sameText = document.getElementById('content').innerHTML === expectedText;
                return original(...args);
            };
            await fgEnsureText();
        }''')
        chunks = self.page.evaluate('chunkFacts')
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < chunk['rows'] <= 512 and chunk['blocks'] == 1
                            and chunk['mounted'] == 0 and chunk['deferred'] and chunk['inert'] for chunk in chunks))
        self.assertEqual(sum(chunk['rows'] for chunk in chunks), self.page.locator('#content .line-container').count())
        self.assertTrue(self.page.evaluate('sameText'))
        self.assertFalse(self.page.evaluate('document.querySelector(".controls").inert'))

    def test_text_preparation_cancels_safely_and_reuses_verified_data_on_reentry(self):
        self.stage_text()
        self.page.evaluate('''() => {
            window.textOpening = fgSwitchView('text');
            window.textShared = fgEnsureText() === textOpening;
        }''')
        self.page.wait_for_function('() => textChunks > 0')
        self.assertTrue(self.page.evaluate('textShared'))
        self.assertIn('Preparing Text', self.page.locator('#content [role=status]').inner_text())
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.page.locator('#content button', has_text='Back to Timeline').click()
        self.assertIsNone(self.page.evaluate('textOpening'))
        self.assertEqual(self.page.evaluate('textClosed'), 1)
        self.assertTrue(self.page.evaluate('fgTextDeferred() && fgData.ready("fgText")'))
        self.page.wait_for_timeout(80)
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
        self.page.evaluate('async () => { holdText = false; await fgEnsureText(); }')
        self.assertEqual(self.page.evaluate('textClosed'), 2)
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)
        self.assertFalse(self.page.evaluate('fgData.ready("fgText")'))
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 1)

    def test_text_final_layout_status_has_a_paint_opportunity_before_initialization(self):
        self.page.evaluate('''async () => {
            window.finishPainted = false;
            const original = fgViews.text.steps;
            fgViews.text.steps = function* () {
                const steps = original();
                try {
                    while (true) {
                        const result = steps.next();
                        if (result.done) return result.value;
                        if (result.value.paint) requestAnimationFrame(() => requestAnimationFrame(() => {
                            window.finishPainted = document.querySelector('#content [role=status]')?.textContent === 'Finishing Text layout…'
                                && !document.querySelector('#content .line-container');
                        }));
                        yield result.value;
                    }
                } finally { steps.return(); }
            };
            const initialize = fgInitText;
            fgInitText = (...args) => {
                window.finishedBeforeInit = finishPainted;
                return initialize(...args);
            };
            await fgEnsureText();
        }''')
        self.assertTrue(self.page.evaluate('finishedBeforeInit'))
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)

    def test_text_fold_metadata_is_prepared_before_mount_and_reused_on_first_click(self):
        self.assertEqual(self.page.evaluate('typeof fgTextModel.prepareFolding'), 'function')
        facts = self.page.evaluate('''async () => {
            const prepare = fgTextModel.prepareFolding;
            const checkpoints = [];
            fgTextModel.prepareFolding = function* (model) {
                const steps = prepare(model);
                try {
                    while (true) {
                        const result = steps.next();
                        if (result.done) return result.value;
                        checkpoints.push({mounted:document.querySelectorAll('#content .line-container').length,
                            deferred:fgTextDeferred(), inert:document.querySelector('.controls').inert,
                            published:fgDomTextModels.has(document.getElementById('content'))});
                        yield result.value;
                    }
                } finally { steps.return(); }
            };
            await fgEnsureText();
            const model = fgDomTextModels.get(document.getElementById('content'));
            const folding = prepare(model).next();
            fgGetTextModel = () => { throw new Error('First fold rebuilt Text rows'); };
            const target = Array.from(folding.value.blocks.values()).find(block => block.entry && block.calls);
            _setFolded(target.id, true);
            _setFolded(target.id, false);
            return {checkpoints, cached:folding.done, records:model.records.length,
                rows:document.querySelectorAll('#content .line-container').length};
        }''')
        self.assertTrue(facts['checkpoints'])
        self.assertTrue(all(checkpoint == dict(mounted=0, deferred=True, inert=True, published=False)
                            for checkpoint in facts['checkpoints']))
        self.assertTrue(facts['cached'])
        self.assertEqual(facts['records'], facts['rows'])
        self.assertFalse(any(url.endswith('text-index.json') for url in self.requests))

    def test_cancelling_fold_metadata_discards_partial_work_and_retries_without_refetch(self):
        self.assertEqual(self.page.evaluate('typeof fgTextModel.prepareFolding'), 'function')
        self.page.evaluate('''() => {
            window.holdFolds = true;
            window.foldStarted = false;
            window.foldClosed = 0;
            window.prepareFolds = fgTextModel.prepareFolding;
            fgTextModel.prepareFolding = function* (model) {
                const steps = prepareFolds(model);
                try {
                    window.cancelledFoldModel = model;
                    const first = steps.next();
                    window.foldStarted = true;
                    while (holdFolds) yield first.value;
                    if (!first.done) yield first.value;
                    return first.done ? first.value : yield* steps;
                } finally { steps.return(); foldClosed++; }
            };
            window.foldOpening = fgSwitchView('text');
        }''')
        self.page.wait_for_function('() => foldStarted')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertFalse(self.page.evaluate('fgDomTextModels.has(document.getElementById("content"))'))
        self.page.locator('#content button', has_text='Back to Timeline').click()
        self.assertIsNone(self.page.evaluate('foldOpening'))
        self.assertEqual(self.page.evaluate('foldClosed'), 1)
        self.assertTrue(self.page.evaluate('''() => {
            const retry = prepareFolds(cancelledFoldModel);
            const partial = !retry.next().done;
            retry.return();
            return partial && fgTextDeferred() && fgData.ready('fgText');
        }'''))
        self.page.evaluate('async () => { holdFolds = false; await fgEnsureText(); }')
        self.assertEqual(self.page.evaluate('foldClosed'), 2)
        self.assertTrue(self.page.evaluate('fgDomTextModels.has(document.getElementById("content"))'))
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 1)

    def test_fold_metadata_failure_retries_before_text_initialization(self):
        self.page.evaluate('''() => {
            const prepare = fgTextModel.prepareFolding;
            let fail = true;
            fgTextModel.prepareFolding = function* (model) {
                yield 'Preparing Text fold metadata…';
                if (fail) {
                    fail = false;
                    throw new Error('fold metadata <failed>');
                }
                return yield* prepare(model);
            };
        }''')
        self.assertIsNone(self.page.evaluate('async () => await fgSwitchView("text")'))
        self.assertIn('fold metadata <failed>', self.page.locator('#content [role=alert]').inner_text())
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertFalse(self.page.evaluate('fgDomTextModels.has(document.getElementById("content"))'))
        self.assertFalse(self.page.evaluate('fgTextInitialized'))
        self.page.locator('#content').get_by_role('button', name='Retry', exact=True).click()
        self.page.wait_for_function('() => !fgTextDeferred()')
        self.assertTrue(self.page.evaluate('fgTextInitialized'))
        self.assertTrue(self.page.evaluate('fgDomTextModels.has(document.getElementById("content"))'))
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 1)

    def test_navigation_preparation_cancels_without_publishing_partial_order(self):
        self.page.set_viewport_size({'width': 360, 'height': 640})
        self.page.evaluate('''() => {
            const prepare = fgPrepareTextNavigation;
            window.holdNavigation = true;
            window.navigationStarted = false;
            window.navigationClosed = 0;
            fgPrepareTextNavigation = function* (lines, model) {
                const steps = prepare(lines, model);
                try {
                    const first = steps.next();
                    window.navigationStarted = true;
                    while (holdNavigation) yield first.value;
                    if (!first.done) yield first.value;
                    return first.done ? first.value : yield* steps;
                } finally { steps.return(); navigationClosed++; }
            };
            window.navigationOpening = fgSwitchView('text');
        }''')
        self.page.wait_for_function('() => navigationStarted')
        for theme in ['light', 'dark']:
            self.page.evaluate('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
            for selector in ['#content [role=status]', '#content .fg-loading button']:
                bounds = self.page.locator(selector).bounding_box()
                self.assertIsNotNone(bounds)
                self.assertGreaterEqual(bounds['x'], 0)
                self.assertLessEqual(bounds['x'] + bounds['width'], 360)
        self.assertTrue(self.page.evaluate('fgTextNavigation === null'))
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.page.locator('#content button', has_text='Back to Timeline').click()
        self.assertIsNone(self.page.evaluate('navigationOpening'))
        self.assertEqual(self.page.evaluate('navigationClosed'), 1)
        self.assertTrue(self.page.evaluate('fgTextNavigation === null && fgData.ready("fgText")'))
        self.page.evaluate('async () => { holdNavigation = false; await fgEnsureText(); }')
        self.assertEqual(self.page.evaluate('navigationClosed'), 2)
        self.assertEqual(self.page.evaluate('fgTextNavigation.order.length'), self.page.locator('#content .line-container').count())
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 1)
        self.assertFalse(any(url.endswith('text-index.json') for url in self.requests))

    def test_source_navigation_uses_complete_records_without_inspecting_dom_rows(self):
        index = json.loads((self.manifest.parent / self.resources['fgTextIndex']['file']).read_text())
        expected = [[row[0], row[-1]] for row in index['rows']]
        for preload in [False, True]:
            with self.subTest(preload=preload):
                if preload:
                    self.page.reload(wait_until='load')
                    self.page.locator('#timelineView .tl-canvas').wait_for(state='attached')
                facts = self.page.evaluate('''async preload => {
                    if (preload) await fgData.load('fgTextIndex');
                    const prepare = fgPrepareTextNavigation;
                    let calls = 0;
                    fgPrepareTextNavigation = function* (rows, model) {
                        calls++;
                        if (model.version !== 2) throw new Error('Missing structured source metadata');
                        const noDOM = new Proxy({}, {get() {throw new Error('Navigation inspected DOM rows');}});
                        return yield* prepare(noDOM, model);
                    };
                    await fgEnsureText();
                    const model = fgDomTextModels.get(document.getElementById('content'));
                    const mapping = model.records.map(record => [record.row[0], record.source]);
                    const pool = fgData.read('fgSrc');
                    const sourceLines = fgData.read('fgSrcLines');
                    return {calls, mapping, candidates:fgTextNavigation.expandable.map(position => fgTextNavigation.order[position]),
                        exact:model.records.every(record => record.source === null ? !(record.row[0] in sourceLines) :
                            record.source === sourceLines[record.row[0]] && typeof pool[record.source] === 'string'),
                        mounted:document.querySelectorAll('#content .line-container').length};
                }''', preload)
                self.assertEqual(facts['calls'], 1)
                self.assertTrue(facts['exact'])
                self.assertEqual(facts['mapping'], expected)
                self.assertEqual(facts['mounted'], len(expected))
                self.assertEqual(facts['candidates'], [number for number, source in expected if source is not None])
                self.assertTrue(any(source == 0 for number, source in expected))
                self.assertEqual(sum(url.endswith('text-index.json') for url in self.requests), int(preload))

    def test_legacy_index_keeps_full_navigation_with_dom_capability_fallback(self):
        facts = self.page.evaluate('''async () => {
            const legacy = JSON.parse(JSON.stringify(await fgData.load('fgTextIndex')));
            legacy.version = 1;
            legacy.rows.forEach(row => row.pop());
            const read = fgData.read;
            fgData.read = id => id === 'fgTextIndex' ? legacy : read(id);
            fgTextModel.prepareNavigation = function* () { throw new Error('Legacy source capability was inferred'); };
            await fgEnsureText();
            const model = fgDomTextModels.get(document.getElementById('content'));
            const expected = Array.from(document.querySelectorAll('#content .line-container.expandable'), row => +row.dataset.lineNumber);
            return {version:model.version, unknown:model.records.every(record => record.source === undefined),
                expected, actual:fgTextNavigation.expandable.map(position => fgTextNavigation.order[position])};
        }''')
        self.assertEqual(facts['version'], 1)
        self.assertTrue(facts['unknown'])
        self.assertTrue(facts['expected'])
        self.assertEqual(facts['actual'], facts['expected'])

    def test_invalid_snapshot_source_does_not_publish_partial_model_and_can_retry(self):
        self.page.evaluate('''async () => {
            await fgData.load('fgText');
            const read = fgData.read;
            window.badSource = true;
            fgData.read = id => {
                const value = read(id);
                return badSource && id === 'fgText' ? value.replace(/data-src="[0-9]+"/, 'data-src="-1"') : value;
            };
            await fgSwitchView('text');
        }''')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertIn('Missing Text source metadata', self.page.locator('#content').inner_text())
        self.assertTrue(self.page.evaluate('!fgTextInitialized && fgTextNavigation === null && !fgDomTextModels.has(document.getElementById("content"))'))
        self.page.evaluate('badSource = false')
        self.page.locator('#content').get_by_role('button', name='Retry', exact=True).click()
        self.page.wait_for_function('() => !fgTextDeferred()')
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 1)
        self.assertFalse(any(url.endswith('text-index.json') for url in self.requests))

    def test_text_preparation_status_fits_compact_light_and_dark_viewports(self):
        self.stage_text()
        self.page.set_viewport_size({'width': 360, 'height': 640})
        for theme in ['light', 'dark']:
            with self.subTest(theme=theme):
                self.page.evaluate('''theme => {
                    document.documentElement.setAttribute('data-theme', theme);
                    textChunks = 0;
                    fgSwitchView('text');
                }''', theme)
                self.page.wait_for_function('() => textChunks > 0')
                for selector in ['#content [role=status]', '#content .fg-loading button']:
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertIsNotNone(bounds)
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], 360)
                self.page.locator('#content .fg-loading button').focus()
                self.page.keyboard.press('Enter')
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
                self.assertEqual(self.page.locator('#content .line-container').count(), 0)

    def test_escape_and_compare_switch_discard_detached_text_preparation(self):
        self.stage_text()
        for cancel in ['escape', 'compare']:
            with self.subTest(cancel=cancel):
                self.page.evaluate('() => { textChunks = 0; window.textOpening = fgSwitchView("text"); }')
                self.page.wait_for_function('() => textChunks > 0')
                if cancel == 'escape':
                    self.page.keyboard.press('Escape')
                else:
                    self.page.locator('[data-view=compare]').click()
                self.assertIsNone(self.page.evaluate('textOpening'))
                self.assertEqual(self.page.locator('#content .line-container').count(), 0)
                self.assertTrue(self.page.evaluate('fgTextDeferred()'))
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline' if cancel == 'escape' else 'compare')
        self.assertEqual(self.page.evaluate('textClosed'), 2)

    def test_synchronous_text_probe_drains_the_pending_iterator_once(self):
        self.stage_text()
        self.page.evaluate('() => { window.textOpening = fgSwitchView("text"); }')
        self.page.wait_for_function('() => textChunks > 0')
        self.page.evaluate('''() => {
            holdText = false;
            window.textAPI = fgView('text');
            window.textCached = fgView('text') === textAPI;
        }''')
        self.assertTrue(self.page.evaluate('textCached'))
        self.assertTrue(self.page.evaluate('textOpening.then(api => api === textAPI)'))
        self.assertEqual(self.page.evaluate('textClosed'), 1)
        self.assertFalse(self.page.locator('#content').get_attribute('aria-busy'))
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)

    def test_detached_text_parse_failure_can_retry_without_initializing_partial_state(self):
        self.page.evaluate('''async () => {
            await fgData.load('fgText');
            const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
            let chunks = 0;
            Object.defineProperty(Element.prototype, 'innerHTML', {...descriptor, set(value) {
                if (this.tagName === 'DIV' && value.startsWith('<div class="text-chunk"') && ++chunks === 2) throw new Error('injected chunk failure');
                descriptor.set.call(this, value);
            }});
        }''')
        self.page.locator('[data-view=text]').click()
        self.page.locator('#content [role=alert]').wait_for()
        self.assertIn('injected chunk failure', self.page.locator('#content [role=alert]').inner_text())
        self.assertTrue(self.page.evaluate('fgTextDeferred() && !fgTextInitialized'))
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.page.locator('#content button', has_text='Retry').click()
        self.page.locator('#content .fold-icon').first.wait_for()
        self.assertGreater(self.page.locator('#content .line-container').count(), 100)
        icon = self.page.locator('#content .fold-icon').first
        icon.press('Enter')
        self.assertEqual(icon.get_attribute('aria-expanded'), 'false')

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
            ...fields.map(field => line.getAttribute('data-' + field)),
            line.classList.contains('expandable') ? +line.nextElementSibling.dataset.src : null
            ]).sort((left, right) => left[0] - right[0])''', index['fields'])
        self.assertEqual(index['rows'], actual)
        for query in queries:
            with self.subTest(native_query=query):
                self.assertEqual(self.apply_query(query), indexed[query])

    def test_full_text_reuses_prepared_index_model_after_resource_cache_release(self):
        self.apply_query('graph:do_sys_openat2')
        facts = self.page.evaluate('''async () => {
            const iterator = fgTextModel.prepare(fgData.read('fgTextIndex'));
            let result;
            do { result = iterator.next(); } while (!result.done);
            const model = result.value;
            await fgEnsureText();
            return {same:fgDomTextModels.get(document.getElementById('content')) === model,
                records:model.records.length, rows:document.querySelectorAll('#content .line-container').length,
                cached:fgData.ready('fgTextIndex'),
                stable:model.records[0].querySelector('.line-content') === model.records[0],
                nodes:model.records.some(record => record instanceof Node)};
        }''')
        self.assertTrue(facts['same'])
        self.assertTrue(facts['stable'])
        self.assertFalse(facts['nodes'])
        self.assertFalse(facts['cached'])
        self.assertEqual(facts['records'], facts['rows'])

    def test_dom_snapshot_is_canonical_and_filters_without_any_mounted_rows(self):
        index = json.loads((self.manifest.parent / self.resources['fgTextIndex']['file']).read_text())
        records = self.page.evaluate('''async () => {
            await fgEnsureText();
            fgSetTextOrder('task');
            const entry = document.querySelector('#content [data-fold-type=entry]');
            _setFolded(entry.getAttribute('data-fold-id'), true);
            if (!entry.querySelector('.fold-summary')) throw new Error('Expected generated summary');
            window.detachedTextModel = fgGetTextModel(document.querySelectorAll('#content .line-container'));
            return detachedTextModel.records.map(record => [+record.getAttribute('data-line-number'),
                record.textContent, ...fgTextModel.fields.map(field => record.getAttribute('data-' + field)), record.source]);
        }''')
        self.assertEqual(records, index['rows'])
        facts = self.page.evaluate('''() => {
            const evaluate = () => {
                const iterator = fgTextModel.filter(detachedTextModel, 'duration:>1 OR func:mutex');
                let result;
                do { result = iterator.next(); } while (!result.done);
                return Array.from(result.value.lines);
            };
            const before = evaluate();
            document.getElementById('content').replaceChildren();
            return {before, after:evaluate(), rows:document.querySelectorAll('#content .line-container').length};
        }''')
        self.assertTrue(facts['before'])
        self.assertEqual(facts['after'], facts['before'])
        self.assertEqual(facts['rows'], 0)

    def test_native_queries_reuse_the_snapshot_after_reordering_without_dom_field_reads(self):
        self.page.evaluate('async () => { await fgEnsureText(); }')
        self.apply_query('comm:sh')
        self.page.evaluate('''() => {
            window.originalTextModel = fgDomTextModels.get(document.getElementById('content'));
            fgSetTextOrder('task');
            window.modelAttributeReads = 0;
            const original = Element.prototype.getAttribute;
            Element.prototype.getAttribute = function(name) {
                if ((name === 'data-comm' || name === 'data-params') && this.matches('.line-container')) modelAttributeReads++;
                return original.call(this, name);
            };
        }''')
        for query in ['comm:sh', 'params:0x', 'graph:do_sys_openat2', 'duration:>1 sort:desc']:
            self.apply_query(query)
        self.assertEqual(self.page.evaluate('modelAttributeReads'), 0)
        self.assertTrue(self.page.evaluate('fgDomTextModels.get(document.getElementById("content")) === originalTextModel'))

    def test_duration_sort_directives_are_not_filter_conditions(self):
        parsed = self.page.evaluate('''() => ['duration:>1 sort:desc', 'sort:ASC', 'sort:invalid',
            '(func:mutex) sort:asc sort:desc'].map(text => parseFilterExpr(text))''')
        self.assertEqual(parsed[0]['sortDir'], 'desc')
        self.assertEqual(parsed[0]['ast'], dict(type='COND', field='duration', op='>', value='1'))
        self.assertEqual(parsed[1], dict(ast=None, sortDir='asc'))
        self.assertEqual(parsed[2]['sortDir'], '')
        self.assertEqual(parsed[2]['ast']['field'], 'sort')
        self.assertEqual(parsed[3]['sortDir'], 'desc')
        self.assertEqual(parsed[3]['ast']['field'], 'func')

    def test_duration_sort_keeps_full_matches_and_orders_only_presentation(self):
        expected = self.apply_query('duration:>1')
        self.assertTrue(expected)
        self.assertEqual(self.apply_query('duration:>1 sort:desc'), expected)
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.page.evaluate('async () => { await fgEnsureText(); }')
        for direction in ['desc', 'asc']:
            with self.subTest(direction=direction):
                self.assertEqual(self.apply_query('duration:>1 sort:' + direction), expected)
                facts = self.page.evaluate('''() => {
                    const rows = Array.from(document.querySelectorAll('#content .line-container'));
                    return {durations:rows.filter(row => row.style.display !== 'none').map(row =>
                        Number(row.dataset.sortDuration || row.dataset.duration || 0)),
                        adjacent:rows.filter(row => row.classList.contains('expandable')).every(row =>
                            row.nextElementSibling?.id === row.dataset.lineId + '_content')};
                }''')
                self.assertTrue(facts['adjacent'])
                self.assertEqual(facts['durations'], sorted(facts['durations'], reverse=direction == 'desc'))

    def test_clearing_or_replacing_duration_sort_restores_preferred_task_order(self):
        self.page.evaluate('async () => { await fgEnsureText(); fgSetTextOrder("task"); }')
        order = self.page.evaluate('Array.from(document.querySelectorAll("#content .line-container"), row => row.id)')
        for direction, clear in [('desc', True), ('asc', False)]:
            with self.subTest(direction=direction, clear=clear):
                self.apply_query('duration:>1 sort:' + direction)
                self.assertEqual(self.page.evaluate('fgTextOrder'), 'task')
                self.assertEqual(self.page.evaluate('fgTextSort'), direction)
                self.assertEqual(self.page.evaluate('lsGet("textOrder")'), 'task')
                if clear:
                    self.page.evaluate('clearFilter()')
                else:
                    self.apply_query('func:mutex')
                self.assertEqual(self.page.evaluate('fgTextSort'), '')
                self.assertEqual(self.page.locator('#textOrderBtn').inner_text(), 'Order: by task')
                self.assertEqual(self.page.evaluate('Array.from(document.querySelectorAll("#content .line-container"), row => row.id)'), order)

    def test_standalone_duration_sort_and_manual_order_switch_preserve_all_records(self):
        self.page.evaluate('async () => { await fgEnsureText(); }')
        before = self.page.evaluate('Array.from(document.querySelectorAll("#content .line-container"), row => row.id)')
        self.assertIsNone(self.apply_query('sort:desc'))
        facts = self.page.evaluate('''() => {
            const rows = Array.from(document.querySelectorAll('#content .line-container'));
            return {ids:rows.map(row => row.id), durations:rows.map(row =>
                Number(row.dataset.sortDuration || row.dataset.duration || 0))};
        }''')
        self.assertEqual(sorted(facts['ids']), sorted(before))
        self.assertEqual(facts['durations'], sorted(facts['durations'], reverse=True))
        self.page.evaluate('fgSetTextOrder("time")')
        self.assertEqual(self.page.evaluate('fgTextSort'), '')
        self.assertEqual(self.page.evaluate('Array.from(document.querySelectorAll("#content .line-container"), row => row.id)'), before)

    def test_standalone_duration_sort_survives_deferred_text_entry_without_index_loading(self):
        self.assertIsNone(self.apply_query('sort:desc'))
        self.assertEqual(self.page.evaluate('fgFilterState.text'), 'sort:desc')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertFalse(any(url.endswith(('text-index.json', 'text.html')) for url in self.requests))
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        facts = self.page.evaluate('''() => {
            const rows = Array.from(document.querySelectorAll('#content .line-container'));
            return {rows:rows.length, durations:rows.map(row =>
                Number(row.dataset.sortDuration || row.dataset.duration || 0)),
                query:document.getElementById('filterInput').value, text:fgFilterState.text,
                unfiltered:fgFilterState.lines === null, sort:fgTextSort};
        }''')
        self.assertGreater(facts['rows'], 100)
        self.assertEqual(facts['durations'], sorted(facts['durations'], reverse=True))
        self.assertEqual(facts['query'], 'sort:desc')
        self.assertEqual(facts['text'], 'sort:desc')
        self.assertTrue(facts['unfiltered'])
        self.assertEqual(facts['sort'], 'desc')
        self.assertFalse(any(url.endswith('text-index.json') for url in self.requests))
        self.page.evaluate('clearFilter()')
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')

    def test_folded_filter_projection_survives_sidecar_mount_and_view_reentry(self):
        expected = self.apply_query('comm:.*')
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        facts = self.page.evaluate('''async () => {
            const entry = document.querySelector('#content .fold-entry');
            const foldId = entry.dataset.foldId;
            toggleFold(foldId);
            applyFilter();
            await fgSwitchView('timeline');
            await fgSwitchView('text');
            const folding = fgGetTextFolding();
            const members = Array.from(folding.members(foldId)).filter(record => record !== folding.blocks.get(foldId).entry);
            return {hidden:members.every(record => document.getElementById('L' + record.row[0]).style.display === 'none'),
                members:members.length, cached:fgData.ready('fgTextIndex'), rows:folding.records.length,
                lines:Array.from(fgFilterState.lines).sort((left, right) => left - right)};
        }''')
        self.assertTrue(facts['hidden'])
        self.assertGreater(facts['members'], 0)
        self.assertEqual(facts['lines'], expected)
        self.assertFalse(facts['cached'])
        self.assertEqual(facts['rows'], self.page.locator('#content .line-container').count())
        self.assertEqual(sum(url.endswith('text.html') for url in self.requests), 1)

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
            const mutations = [data => { data.version = 3; }, data => { data.fields.reverse(); },
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
