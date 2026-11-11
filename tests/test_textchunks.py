"""Exact Text network ranges, publication, and deferred chunk preparation."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import helpers
from fglib.output import ReportSpool
from fglib.sidecar import _copy, write_sidecar
from fglib import sidecar
from fglib.textchunks import TEXT_NETWORK_ROWS, TextChunkRanges
from fglib.viewer import create_server

try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except ImportError:
    sync_playwright = None


class ChunkRender:
    text_chunk_rows = 512
    timeline_json = {'n': 1}

    def __init__(self, rows):
        self.rows = rows

    def iter_sections(self):
        yield 'chrome', '<!doctype html><div id="content">'
        for index in range(self.rows):
            if index % self.text_chunk_rows == 0:
                if index:
                    yield 'text', '</div>'
                yield 'text', '<div class="text-chunk" data-chunk-size="512">'
            markup = '<div class="line-container" id="L' + str(index + 1) + '">行🙂</div>'
            if getattr(self, 'text_index', None) is not None:
                attributes = dict(space='user' if index % 2 else 'kernel') if 'uftrace' in self.timeline_json else {}
                self.text_index.append(index + 1, attributes, '行🙂')
            yield 'text', markup
        if self.rows:
            yield 'text', '</div>'
        yield 'chrome', '</div>'
        yield 'report_script', '<script></script>'
        yield 'view_data:tlData', '{"n":1}'

    def iter_source_chunks(self, rows):
        return iter(())


class TextChunkRangesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-text-chunks-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_exact_utf8_ranges_cover_each_original_byte_once(self):
        for count in [0, 1, 4095, 4096, 4097, 8192, 8199]:
            with self.subTest(count=count):
                ranges = TextChunkRanges(512)
                with ReportSpool(self.root / 'spool') as spool:
                    spool.write_sections(ChunkRender(count).iter_sections(), observer=ranges.append)
                    result = ranges.finish(count)
                    spool.file.flush()
                    expected = io.BytesIO()
                    spool.copy_sections('text', expected)
                    actual = io.BytesIO()
                    for first, rows, section in result:
                        self.assertEqual(first % TEXT_NETWORK_ROWS, 0)
                        self.assertEqual(rows, min(TEXT_NETWORK_ROWS, count - first))
                        _copy(spool, section, actual)
                    self.assertEqual(actual.getvalue(), expected.getvalue())
                    self.assertEqual(sum(rows for first, rows, section in result), count)
                    self.assertEqual(len(result), (count + TEXT_NETWORK_ROWS - 1) // TEXT_NETWORK_ROWS)

    def test_invalid_paint_sizes_and_incomplete_structures_fail_closed(self):
        for size in [True, 0, -1, 3, '512', None]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                TextChunkRanges(size)
        marker = b'<div class="text-chunk" data-chunk-size="512">'
        cases = [[('text', b'</div>')], [('text', b'row')], [('text', marker)],
                 [('text', marker), ('text', b'</div>')],
                 [('text', marker), ('text', b'row'), ('text', b'</div>'), ('text', marker)],
                 [('text', marker), ('text', b'row'), ('chrome', b'gap'), ('text', b'</div>')],
                 [('text', marker)] + [('text', b'row')] * 513 + [('text', b'</div>')]]
        for parts in cases:
            with self.subTest(parts=parts[:4]), self.assertRaises(ValueError):
                ranges = TextChunkRanges(512)
                position = 0
                for name, payload in parts:
                    ranges.append(name, payload, position, position + len(payload))
                    position += len(payload)
                ranges.finish(1)

    def test_observer_failure_does_not_publish_a_partial_report(self):
        target = self.root / 'report'
        target.write_bytes(b'previous report')
        with self.assertRaisesRegex(RuntimeError, 'observer failed'):
            with ReportSpool(target) as spool:
                def observer(name, payload, start, end):
                    self.assertEqual(payload, '中文'.encode())
                    self.assertEqual((start, end), (0, 6))
                    raise RuntimeError('observer failed')
                spool.write_sections([('text', '中文')], observer=observer)
        self.assertEqual(target.read_bytes(), b'previous report')
        self.assertEqual(list(self.root.glob('.funcgraph-*.tmp')), [])

    def test_sidecar_chunks_match_original_text_and_index_without_a_monolithic_resource(self):
        rows = 8199
        render = ChunkRender(rows)
        output = self.root / 'report'
        write_sidecar(output, render)
        entry = (output / 'index.html').read_text()
        catalog = helpers.json_script(entry, 'fgTextChunks')
        self.assertEqual(catalog, dict(version=1, count=rows, chunk_size=4096, paint_rows=512))
        descriptor = helpers.json_script(entry, 'fgDataManifest')
        manifest_path = output / descriptor['manifest']['file']
        manifest_bytes = manifest_path.read_bytes()
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(), descriptor['manifest']['sha256'])
        manifest = json.loads(manifest_bytes)
        self.assertEqual(manifest['capabilities']['text'], 'chunks-v1')
        self.assertNotIn('fgText', manifest['resources'])
        parts = []
        for first in [0, 4096, 8192]:
            resource = manifest['resources']['fgText' + str(first)]
            payload = (manifest_path.parent / resource['file']).read_bytes()
            self.assertEqual(resource['bytes'], len(payload))
            self.assertEqual(resource['sha256'], hashlib.sha256(payload).hexdigest())
            parts.append(payload)
        expected = ''.join(fragment for name, fragment in ChunkRender(rows).iter_sections() if name == 'text').encode()
        self.assertEqual(b''.join(parts), expected)
        index = json.loads((manifest_path.parent / 'text-index.json').read_bytes())
        self.assertEqual([row[0] for row in index['rows']], list(range(1, rows + 1)))
        self.assertFalse((manifest_path.parent / 'text.html').exists())

    def test_small_sidecars_and_single_auto_outputs_keep_existing_delivery(self):
        for count in [0, 1, 4096]:
            with self.subTest(count=count):
                output = self.root / ('small-' + str(count))
                write_sidecar(output, ChunkRender(count))
                entry = (output / 'index.html').read_text()
                self.assertIsNone(helpers.json_script(entry, 'fgTextChunks'))
                descriptor = helpers.json_script(entry, 'fgDataManifest')
                manifest_path = output / descriptor['manifest']['file']
                manifest = json.loads(manifest_path.read_bytes())
                self.assertIn('fgText', manifest['resources'])
                self.assertEqual(manifest['capabilities']['text'], 'deferred-full')
        output = self.root / 'single.html'
        result = write_sidecar(output, ChunkRender(4097), byte_limit=10**9)
        self.assertEqual(result.mode, 'single')
        self.assertIsNone(helpers.json_script(Path(result.entry_path).read_text(), 'fgTextChunks'))
        self.assertEqual(list(self.root.glob('.funcgraph-sidecar-*')), [])

    def test_chunk_export_failure_preserves_old_entry_and_success_keeps_old_resources(self):
        output = self.root / 'report'
        write_sidecar(output, ChunkRender(4097))
        original_entry = (output / 'index.html').read_bytes()
        entries = set(output.iterdir())
        export = sidecar._resource

        def interrupted(spool, section_name, path, format, ranges=None):
            if path.name == 'text-4096.html':
                raise OSError('interrupted Text chunk')
            return export(spool, section_name, path, format, ranges)

        with mock.patch.object(sidecar, '_resource', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interrupted Text chunk'):
                write_sidecar(output, ChunkRender(8199))
        self.assertEqual((output / 'index.html').read_bytes(), original_entry)
        self.assertEqual(set(output.iterdir()), entries)
        self.assertEqual(list(self.root.glob('.funcgraph-sidecar-*')), [])
        write_sidecar(output, ChunkRender(8199))
        self.assertTrue(entries.issubset(set(output.iterdir())))
        catalog = helpers.json_script((output / 'index.html').read_text(), 'fgTextChunks')
        self.assertEqual(catalog['count'], 8199)

    def test_mixed_space_index_remains_complete_across_network_chunk_boundaries(self):
        render = ChunkRender(4203)
        render.timeline_json = dict(n=1, uftrace={})
        output = self.root / 'mixed'
        write_sidecar(output, render)
        descriptor = helpers.json_script((output / 'index.html').read_text(), 'fgDataManifest')
        directory = (output / descriptor['manifest']['file']).parent
        index = json.loads((directory / 'text-index.json').read_bytes())
        self.assertEqual(index['version'], 3)
        column = index['fields'].index('space') + 2
        self.assertEqual([row[column] for row in index['rows']],
                         ['user' if number % 2 else 'kernel' for number in range(4203)])
        self.assertTrue((directory / 'text-4096.html').is_file())


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TextChunkBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        trace = Path(cls.kernel.dir) / 'chunk-trace.txt'
        trace.write_text('# tracer: function_graph\n'
                         ' 3)               |  root() { /* <-main+0x4/0x80 */\n' +
                         ' 3)   0.050 us    |    leaf(); /* <-root+0x4/0x80 */\n' * 4200 +
                         ' 3) 300.000 us    |  } /* root */\n')
        cls.kernel.run(str(trace), '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--report-mode', 'sidecar', out='report')
        cls.directory = Path(cls.kernel.dir) / 'report'
        cls.server = create_server(cls.directory)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            cls.playwright.stop()
            cls.server.shutdown()
            cls.thread.join(timeout=5)
            cls.server.server_close()
            cls.kernel.close()
            raise unittest.SkipTest(str(error))

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
        self.page.add_init_script('localStorage.clear(); window.violations = []; document.addEventListener("securitypolicyviolation", event => violations.push(event.violatedDirective))')
        self.page.goto(self.server.url, wait_until='load')
        self.page.locator('#timelineView .tl-canvas').wait_for(state='attached')

    def tearDown(self):
        try:
            self.assertEqual(self.errors, [])
            self.assertEqual(self.page.evaluate('violations'), [])
        finally:
            self.page.close()

    def test_timeline_stays_text_free_and_arbitrary_chunk_access_does_not_mount_text(self):
        self.assertFalse(any('/text-' in url or url.endswith('text.html') for url in self.requests))
        result = self.page.evaluate('''async () => {
            const catalog = fgData.textChunks();
            const html = await fgData.loadTextChunk(4100);
            return {catalog, second:html.includes('L4101'), first:fgData.ready('fgText0'),
                rows:document.querySelectorAll('#content .line-container').length, deferred:fgTextDeferred()};
        }''')
        self.assertEqual(result['catalog']['count'], 4203)
        self.assertTrue(result['second'])
        self.assertFalse(result['first'])
        self.assertEqual(result['rows'], 0)
        self.assertTrue(result['deferred'])
        self.assertFalse(any(url.endswith('text-index.json') for url in self.requests))

    def test_complete_chunk_mount_preserves_cross_chunk_folding_filtering_sources_and_reentry(self):
        result = self.page.evaluate('''async () => {
            await fgSwitchView('text');
            const folding = fgGetTextFolding();
            const root = Array.from(folding.blocks.values()).find(block => block.entry?.getAttribute('data-func') === 'root');
            const counts = [document.querySelectorAll('#content .line-container').length];
            toggleFold(root.id);
            counts.push(document.querySelectorAll('#content .line-container:not([style*="display: none"])').length);
            document.getElementById('filterInput').value = 'func:^leaf$';
            applyFilter();
            toggleFold(root.id);
            counts.push(document.querySelectorAll('#content .line-container:not([style*="display: none"])').length);
            clearFilter();
            fgSetTextOrder('task'); fgSetTextOrder('time');
            const source = document.getElementById('L4101').nextElementSibling;
            await fgFill(source);
            await fgSwitchView('timeline'); await fgSwitchView('text');
            return {counts, total:folding.records.length, source:source.textContent.includes('fake.c'),
                cached:[fgData.ready('fgText0'), fgData.ready('fgText4096')], deferred:fgTextDeferred()};
        }''')
        self.assertEqual(result, dict(counts=[4203, 2, 4200], total=4203, source=True, cached=[False, False], deferred=False))
        self.assertEqual(sum(url.endswith('text-0.html') for url in self.requests), 1)
        self.assertEqual(sum(url.endswith('text-4096.html') for url in self.requests), 1)
        self.assertFalse(any(url.endswith('text.html') for url in self.requests))

    def test_waiting_for_a_chunk_can_cancel_and_retry_without_partial_mount_or_stale_resume(self):
        held = []
        self.page.route('**/text-4096.html', lambda route: held.append(route))
        self.page.evaluate('() => { window.openingText = fgSwitchView("text"); }')
        self.page.wait_for_function('() => document.querySelector("#content .fg-loading-status")?.textContent.includes("4097")')
        for unused in range(500):
            if held:
                break
            self.page.wait_for_timeout(10)
        self.assertTrue(held)
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertTrue(self.page.evaluate('fgView("text") instanceof Promise'))
        self.page.evaluate('fgSwitchView("timeline")')
        for route in held:
            try:
                route.abort()
            except PlaywrightError as error:
                self.assertIn('already handled', str(error))
        self.page.unroute('**/text-4096.html')
        self.page.evaluate('async () => { await openingText; await new Promise(resolve => setTimeout(resolve, 30)); }')
        self.assertTrue(self.page.evaluate('fgTextDeferred() && fgCurrentView() === "timeline"'))
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        self.assertEqual(self.page.locator('#content .line-container').count(), 4203)
        self.assertEqual(sum(url.endswith('text-0.html') for url in self.requests), 1)

    def test_invalid_prepared_chunk_retries_before_any_rows_or_listeners_are_published(self):
        self.page.evaluate('''() => {
            const load = fgData.loadTextChunk;
            window.originalTextLoader = load;
            fgData.loadTextChunk = (row, options) => row ? Promise.resolve('<div class="text-chunk" data-chunk-size="512"><div class="line-container">wrong count</div></div>') : load(row, options);
        }''')
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        self.assertIn('row count mismatch', self.page.locator('#content .fg-loading-status').inner_text())
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertTrue(self.page.evaluate('fgTextDeferred() && !fgTextInitialized'))
        self.page.evaluate('() => { fgData.loadTextChunk = originalTextLoader; }')
        self.page.get_by_role('button', name='Retry', exact=True).click()
        self.page.wait_for_function('() => !fgTextDeferred()')
        self.assertEqual(self.page.locator('#content .line-container').count(), 4203)

    def test_rejected_network_chunk_exposes_retry_and_never_mounts_partial_text(self):
        self.page.route('**/text-4096.html', lambda route: route.fulfill(status=200, body='corrupt'))
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        self.assertIn('size mismatch', self.page.locator('#content .fg-loading-status').inner_text())
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertTrue(self.page.evaluate('fgTextDeferred() && !fgTextInitialized'))
        self.page.unroute('**/text-4096.html')
        self.page.get_by_role('button', name='Retry', exact=True).click()
        self.page.wait_for_function('() => !fgTextDeferred()')
        self.assertEqual(self.page.locator('#content .line-container').count(), 4203)
        self.assertEqual(sum(url.endswith('text-0.html') for url in self.requests), 1)

    def test_late_uncancellable_chunk_result_cannot_resume_a_cancelled_text_generator(self):
        self.page.evaluate('''async () => {
            window.secondChunk = await fgData.loadTextChunk(4096);
            window.actualTextLoader = fgData.loadTextChunk;
            fgData.loadTextChunk = (row, options) => row ? new Promise(resolve => {
                window.resumeOldText = resolve;
                window.oldTextSignal = options.signal;
            }) : actualTextLoader(row, options);
            window.openingText = fgSwitchView('text');
        }''')
        self.page.wait_for_function('() => typeof resumeOldText === "function"')
        self.page.evaluate('fgSwitchView("timeline")')
        self.assertTrue(self.page.evaluate('oldTextSignal.aborted'))
        self.page.evaluate('''async () => {
            fgData.loadTextChunk = actualTextLoader;
            resumeOldText(secondChunk);
            await openingText;
            await new Promise(resolve => setTimeout(resolve, 30));
        }''')
        self.assertTrue(self.page.evaluate('fgCurrentView() === "timeline" && fgTextDeferred() && !fgTextInitialized'))
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        self.assertEqual(self.page.locator('#content .line-container').count(), 4203)

    def test_preloaded_full_trace_filter_and_narrow_anchor_entry_reach_the_last_chunk(self):
        result = self.page.evaluate('''async () => {
            document.getElementById('filterInput').value = 'func:^leaf$';
            await applyFilter();
            const before = fgFilterState.lines.size;
            await fgSwitchView('text');
            const after = document.querySelectorAll('#content .line-container:not([style*="display: none"])').length;
            return {before, after, total:fgGetTextFolding().records.length};
        }''')
        self.assertEqual(result['after'], 4200)
        self.assertEqual(result['before'], 4200)
        self.assertEqual(result['total'], 4203)
        self.assertEqual(sum(url.endswith('text-index.json') for url in self.requests), 1)
        self.page.set_viewport_size(dict(width=390, height=844))
        self.page.goto(self.server.url + '#L4101', wait_until='load')
        self.page.wait_for_function('() => !fgTextDeferred()')
        self.assertTrue(self.page.evaluate('fgCurrentView() === "text" && location.hash === "#L4101"'))
        self.assertEqual(self.page.locator('#L4101').count(), 1)
