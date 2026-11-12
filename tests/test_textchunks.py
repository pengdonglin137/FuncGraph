"""Exact Text network ranges, publication, and deferred chunk preparation."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urljoin

import helpers
from fglib.output import ReportSpool
from fglib.sidecar import _copy, write_sidecar
from fglib import sidecar, uftrace
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
        self.assertEqual(index['version'], 4)
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

    def test_grouped_paint_reads_preserve_request_order_shared_blocks_and_partial_final_range(self):
        facts = self.page.evaluate('''async () => {
            const ordinals=[1537,1,1536,4095];
            const pending=fgData.loadTextBlocks(ordinals);
            ordinals[0]=4100;
            const blocks=await pending, last=await fgData.loadTextBlocks([4202]);
            const singles=await Promise.all([1537,1,1536,4095].map(ordinal=>fgData.loadTextBlock(ordinal)));
            return {first:blocks.map(block=>block.first),counts:blocks.map(block=>block.count),
                shared:blocks[0]===blocks[2],identical:JSON.stringify(blocks)===JSON.stringify(singles),
                last:last.map(block=>({first:block.first,count:block.count})),
                mounted:document.querySelectorAll('#content .line-container').length,deferred:fgTextDeferred()};
        }''')
        self.assertEqual(facts, dict(first=[1536, 0, 1536, 3584], counts=[512] * 4, shared=True,
                                    identical=True, last=[dict(first=4096, count=107)], mounted=0, deferred=True))
        chunks = [url for url in self.requests if '/text-' in url and url.endswith('.html')]
        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(set(chunks)), 2)

    def test_grouped_paint_reads_reject_invalid_or_cross_resource_identities_before_loading(self):
        facts = self.page.evaluate('''async () => {
            const errors=[];
            for (const ordinals of [null,{},1,[undefined],[,1],[-1],[0,4096],[4203],[Number.MAX_SAFE_INTEGER+1]]) {
                errors.push(await fgData.loadTextBlocks(ordinals).then(()=>null,error=>error.message));
            }
            const controller=new AbortController(); controller.abort();
            const canceled=await fgData.loadTextBlocks([],{signal:controller.signal}).then(()=>null,error=>error.name);
            return {errors,canceled,empty:await fgData.loadTextBlocks([])};
        }''')
        self.assertTrue(all(facts['errors']))
        self.assertEqual(facts['errors'][6], 'Text block group crosses network chunks')
        self.assertEqual(facts['canceled'], 'AbortError')
        self.assertEqual(facts['empty'], [])
        self.assertFalse(any('/text-' in url for url in self.requests))

    def test_reading_an_unmounted_line_materializes_only_its_paint_block_and_exact_source_pairs(self):
        facts = self.page.evaluate('''async () => {
            const iterator = fgTextModel.prepare(await fgData.load('fgTextIndex'));
            let result;
            do { result = iterator.next(); } while (!result.done);
            const model = result.value;
            const block = await fgLoadTextBlock(model, 4101);
            return {first:block.first, count:block.count, line:block.line.id,
                rows:block.element.querySelectorAll('.line-container').length,
                source:block.line.nextElementSibling.dataset.src,
                expected:fgTextModel.locate(model, 4101).record.source,
                connected:block.element.isConnected, mounted:document.querySelectorAll('#content .line-container').length,
                deferred:fgTextDeferred(), firstReady:fgData.ready('fgText0')};
        }''')
        self.assertEqual(facts, dict(first=4096, count=107, line='L4101', rows=107,
                                    source=str(facts['expected']), expected=facts['expected'], connected=False,
                                    mounted=0, deferred=True, firstReady=False))
        self.assertEqual(sum('/text-4096.html' in url for url in self.requests), 1)
        self.assertFalse(any('/text-0.html' in url for url in self.requests))

    def test_renderer_contract_can_reveal_an_unmounted_line_without_loading_other_text_chunks(self):
        facts = self.page.evaluate('''async () => {
            const iterator = fgTextModel.prepare(await fgData.load('fgTextIndex'));
            let result;
            do { result = iterator.next(); } while (!result.done);
            const model = result.value;
            const navigation = fgTextModel.prepareNavigation(model);
            do { result = navigation.next(); } while (!result.done);
            fgTextNavigation = result.value;
            const root = document.getElementById('content');
            fgViews.text = {el:'content', init:() => ({async ensureLine(number, options) {
                const block = await fgLoadTextBlock(model, number, options);
                if (options.signal?.aborted) throw new DOMException('Canceled', 'AbortError');
                root.replaceChildren(block.element);
                return block.line;
            }})};
            fgView('text'); fgSwitchView('text');
            await setKeyboardSelectedLine(fgTextNavigation.indexOf(4101), false);
            return {selected:keyboardSelectedLine.id, index:keyboardSelectedIndex, records:model.records.length,
                mounted:root.querySelectorAll('.line-container').length, firstReady:fgData.ready('fgText0')};
        }''')
        self.assertEqual(facts, dict(selected='L4101', index=4100, records=4203, mounted=107, firstReady=False))
        self.assertFalse(any('/text-0.html' in url for url in self.requests))

    def test_first_timeline_jump_survives_complete_text_initialization_and_stale_hash(self):
        for stale_hash in ['', '#L5']:
            with self.subTest(stale_hash=stale_hash):
                self.page.goto(self.server.url, wait_until='load')
                self.page.locator('#timelineView .tl-canvas').wait_for(state='attached')
                number = self.page.evaluate('''hash => {
                    history.replaceState(null, '', hash || location.pathname);
                    const probe = fgView('timeline').probe();
                    const index = probe.N - 1;
                    probe.select(index);
                    window.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', cancelable:true}));
                    return fgData.read('tlData').cols.line[index];
                }''', stale_hash)
                self.page.wait_for_function('number => !fgTextDeferred() && keyboardSelectedLine?.id === "L" + number', arg=number)
                self.assertGreater(number, 4096)
                self.page.locator('#L' + str(number) + '.tl-flash').wait_for(state='attached')
                self.assertEqual(self.page.locator('#content .line-container').count(), 4203)
                self.page.evaluate("fgSwitchView('timeline'); lsSet('view', 'timeline')")

    def test_cancelling_line_subscriber_does_not_cancel_shared_complete_text_initialization(self):
        facts = self.page.evaluate('''async () => {
            const opening = fgSwitchView('text');
            const controller = new AbortController();
            const line = fgEnsureTextLine(4101, {signal:controller.signal}).catch(error => error.name);
            controller.abort();
            const canceled = await line;
            const api = await opening;
            return {canceled, ready:!!api, rows:document.querySelectorAll('#content .line-container').length,
                deferred:fgTextDeferred(), initialized:fgTextInitialized};
        }''')
        self.assertEqual(facts, dict(canceled='AbortError', ready=True, rows=4203, deferred=False, initialized=True))

    def test_sparse_identity_materialization_uses_original_ordinals_for_all_index_versions(self):
        facts = self.page.evaluate('''async () => {
            const input = await fgData.load('fgTextIndex');
            const load = fgData.loadTextBlock;
            const calls = [], facts = [];
            fgData.loadTextBlock = async (ordinal, options) => {
                calls.push(ordinal);
                const block = await load(ordinal, options);
                return {...block, html:block.html.replace(/(id="L|data-line-number=")([0-9]+)"/g,
                    (_, prefix, number) => prefix + (+number*3+2) + '"')};
            };
            try {
                for (const version of [1,2,3,4]) {
                    const typed = version === 3;
                    const fields = version === 4 ? input.fields : typed ? [...fgTextModel.fields, 'space'] : [...fgTextModel.fields];
                    const rows = input.rows.map(row => version === 4 ? [row[0]*3+2, ...row.slice(1)] : [row[0]*3+2, ...row.slice(1, fgTextModel.fields.length+2),
                        ...(typed ? ['user'] : []), ...(version >= 2 ? [row.at(-1)] : [])]);
                    const iterator = fgTextModel.prepare({version, fields, rows});
                    let result;
                    do { result = iterator.next(); } while (!result.done);
                    fgTextModel.order(result.value, 'task');
                    const block = await fgLoadTextBlock(result.value, 4101*3+2);
                    facts.push({version, first:block.first, count:block.count, line:block.line.id});
                }
                return {calls, facts};
            } finally { fgData.loadTextBlock = load; }
        }''')
        self.assertEqual(facts['calls'], [4100] * 4)
        self.assertEqual(facts['facts'], [dict(version=version, first=4096, count=107, line='L12305')
                                           for version in [1, 2, 3, 4]])
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)

    def test_on_demand_rows_fail_closed_for_missing_records_wrong_counts_identities_and_sources(self):
        facts = self.page.evaluate('''async () => {
            const iterator = fgTextModel.prepare(await fgData.load('fgTextIndex'));
            let result;
            do { result = iterator.next(); } while (!result.done);
            const model = result.value;
            const original = fgData.loadTextBlock;
            const block = await original(4100);
            const messages = [];
            try {
                for (const html of [block.html.replace('class="line-container', 'class="removed'),
                    block.html.replace('id="L4097"', 'id="L999999"'),
                    block.html.replace(/data-src="[0-9]+"/, 'data-src="999999"')]) {
                    fgData.loadTextBlock = async () => ({...block, html});
                    try { await fgLoadTextBlock(model, 4101); messages.push('accepted'); }
                    catch (error) { messages.push(error.message); }
                }
                fgData.loadTextBlock = () => { throw new Error('Missing record must not load HTML'); };
                const missing = await fgLoadTextBlock(model, 999999);
                const controller = new AbortController(); controller.abort();
                try { await fgLoadTextBlock(model, 4101, {signal:controller.signal}); }
                catch (error) { messages.push(error.name); }
                return {messages, missing};
            } finally { fgData.loadTextBlock = original; }
        }''')
        self.assertEqual(facts, dict(missing=None, messages=['Text paint block row count mismatch',
                         'Text paint block line identity mismatch', 'Text paint block source identity mismatch', 'AbortError']))
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)

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

    def test_next_network_chunk_starts_before_current_paint_block_parsing(self):
        self.page.evaluate('''() => {
            const markup = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
            window.parsedTextBlocks = 0;
            Object.defineProperty(Element.prototype, 'innerHTML', {...markup, set(value) {
                if (value.startsWith('<div class="text-chunk"')) parsedTextBlocks++;
                markup.set.call(this, value);
            }});
            const load = fgData.loadTextChunk;
            window.textChunkRequests = [];
            fgData.loadTextChunk = (first, options) => {
                textChunkRequests.push({first, parsed:parsedTextBlocks});
                return load(first, options);
            };
        }''')
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        self.assertEqual(self.page.evaluate('textChunkRequests'), [dict(first=0, parsed=0), dict(first=4096, parsed=0)])
        self.assertEqual(self.page.evaluate('parsedTextBlocks'), 9)
        self.assertEqual(self.page.locator('#content .line-container').count(), 4203)

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

    def test_native_layout_skips_offscreen_blocks_without_dropping_complete_records(self):
        result = self.page.evaluate('''async () => {
            window.layoutStates = new Map();
            document.addEventListener('contentvisibilityautostatechange', event => {
                if (event.target.matches('#content > .text-chunk')) layoutStates.set(event.target, event.skipped);
            }, true);
            await fgSwitchView('text');
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            return {enabled:document.getElementById('content').classList.contains('text-layout-auto'),
                skipped:Array.from(layoutStates.values()).filter(Boolean).length,
                rows:document.querySelectorAll('#content .line-container').length,
                records:fgGetTextFolding().records.length,
                near:fgTextScrollBehavior(document.getElementById('L2'), {top:0}),
                far:fgTextScrollBehavior(document.getElementById('L4101'), {top:10000}),
                sizes:Array.from(document.querySelectorAll('#content > .text-chunk'), chunk => getComputedStyle(chunk).containIntrinsicBlockSize)};
        }''')
        self.assertTrue(result['enabled'])
        self.assertGreater(result['skipped'], 0)
        self.assertEqual(result['rows'], 4203)
        self.assertEqual(result['records'], 4203)
        self.assertEqual(result['near'], 'smooth')
        self.assertEqual(result['far'], 'instant')
        expected_sizes = ['13056px'] * 8 + ['2728.5px']
        self.assertEqual(len(result['sizes']), len(expected_sizes))
        for actual, expected in zip(result['sizes'], expected_sizes):
            self.assertIn(actual, (expected, 'auto ' + expected))

    def test_visited_offscreen_blocks_discard_stale_sizes_after_filter_and_fold(self):
        result = self.page.evaluate('''async () => {
            await fgSwitchView('text');
            const content = document.getElementById('content');
            const parent = Array.from(fgGetTextFolding().blocks.values()).find(block => block.entry?.getAttribute('data-func') === 'root');
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            async function visit() {
                for (const number of [1025, 2050, 3073, 4101, 2]) {
                    document.getElementById('L' + number).scrollIntoView({block:'center'});
                    await settle();
                }
            }
            const samples = [];
            async function compare(label) {
                await settle();
                const estimated = content.getBoundingClientRect().height;
                const baseline = document.createElement('style');
                baseline.textContent = '#content.text-layout-auto > .text-chunk {content-visibility:visible !important}';
                document.head.appendChild(baseline);
                await settle();
                samples.push({label, estimated, full:content.getBoundingClientRect().height});
                baseline.remove();
                await settle();
            }
            await visit();
            document.getElementById('filterInput').value = 'func:^root$';
            applyFilter();
            await compare('filter-after-visit');
            clearFilter();
            await compare('clear-after-visit');
            await visit();
            toggleFold(parent.id);
            await compare('fold-after-visit');
            toggleFold(parent.id);
            await compare('unfold-after-visit');
            return samples;
        }''')
        self.assertEqual(len(result), 4)
        for sample in result:
            with self.subTest(state=sample['label']):
                self.assertAlmostEqual(sample['estimated'], sample['full'], delta=1)

    def test_responsive_layout_discards_visited_block_sizes_in_both_directions(self):
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        for view, width in [('text', 390), ('text', 1500), ('timeline', 390), ('timeline', 1500)]:
            self.page.evaluate('''async view => {
                for (const number of [1025, 2050, 3073, 4101, 2]) {
                    document.getElementById('L' + number).scrollIntoView({block:'center'});
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                }
                if (view === 'timeline') await fgSwitchView('timeline');
            }''', view)
            self.page.set_viewport_size(dict(width=width, height=900))
            result = self.page.evaluate('''async view => {
                if (view === 'timeline') await fgSwitchView('text');
                const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                await settle();
                const content = document.getElementById('content');
                const estimated = content.getBoundingClientRect().height;
                const baseline = document.createElement('style');
                baseline.textContent = '#content.text-layout-auto > .text-chunk {content-visibility:visible !important}';
                document.head.appendChild(baseline);
                await settle();
                const full = content.getBoundingClientRect().height;
                baseline.remove();
                await settle();
                return {estimated, full};
            }''', view)
            with self.subTest(view=view, width=width):
                self.assertAlmostEqual(result['estimated'], result['full'], delta=1)

    def test_native_layout_extents_follow_complete_filter_fold_and_reorder_state(self):
        result = self.page.evaluate('''async () => {
            await fgSwitchView('text');
            const content = document.getElementById('content');
            const parent = Array.from(fgGetTextFolding().blocks.values()).find(block => block.entry?.getAttribute('data-func') === 'root');
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const results = [];
            async function compare(label) {
                await settle();
                const estimated = content.getBoundingClientRect().height;
                const baseline = document.createElement('style');
                baseline.textContent = '#content.text-layout-auto > .text-chunk {content-visibility:visible !important}';
                document.head.appendChild(baseline);
                await settle();
                results.push({label, estimated, full:content.getBoundingClientRect().height});
                baseline.remove();
            }
            await compare('initial');
            toggleFold(parent.id);
            await compare('folded');
            document.getElementById('filterInput').value = 'func:^leaf$';
            applyFilter();
            await compare('filtered-inside-closed-parent');
            clearFilter();
            await compare('clear-keeps-parent-closed');
            toggleFold(parent.id);
            await compare('unfolded');
            document.getElementById('filterInput').value = 'func:^leaf$ sort:desc';
            applyFilter();
            await compare('sorted');
            clearFilter();
            fgSetTextOrder('task');
            await compare('task-order');
            fgSetTextOrder('time');
            await compare('time-order');
            return results;
        }''')
        self.assertEqual(len(result), 8)
        for sample in result:
            with self.subTest(state=sample['label']):
                self.assertAlmostEqual(sample['estimated'], sample['full'], delta=1)

    def test_expanded_source_keeps_its_block_laid_out_until_closed(self):
        result = self.page.evaluate('''async () => {
            await fgSwitchView('text');
            const row = document.getElementById('L4101');
            const chunk = row.parentElement;
            const source = row.nextElementSibling;
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            toggleExpand(row.dataset.lineId);
            await fgFill(source);
            await settle();
            const open = {visibility:getComputedStyle(chunk).contentVisibility,
                display:getComputedStyle(source).display, height:source.getBoundingClientRect().height,
                layout:document.getElementById('content').getBoundingClientRect().height};
            fgToggleSrcMode();
            await settle();
            const flat = getComputedStyle(chunk).contentVisibility;
            toggleExpand(row.dataset.lineId);
            await settle();
            return {open, flat, closed:getComputedStyle(chunk).contentVisibility,
                layout:document.getElementById('content').getBoundingClientRect().height};
        }''')
        self.assertEqual(result['open']['visibility'], 'visible')
        self.assertEqual(result['open']['display'], 'block')
        self.assertGreater(result['open']['height'], 0)
        self.assertEqual(result['flat'], 'visible')
        self.assertEqual(result['closed'], 'auto')
        self.assertGreater(result['open']['layout'], result['layout'])

    def test_offscreen_anchor_and_keyboard_boundary_reveal_in_both_themes_and_widths(self):
        for width in [390, 1500]:
            for theme in ['light', 'dark']:
                with self.subTest(width=width, theme=theme):
                    self.page.set_viewport_size(dict(width=width, height=900))
                    self.page.evaluate('''async theme => {
                        document.documentElement.dataset.theme = theme;
                        await fgSwitchView('text');
                        location.hash = '#L4101';
                        handleAnchorNavigation();
                    }''', theme)
                    self.page.wait_for_function('''() => {
                        const row = document.getElementById('L4101').getBoundingClientRect();
                        return row.top >= document.querySelector('.controls').getBoundingClientRect().bottom && row.bottom <= innerHeight;
                    }''')
                    self.assertTrue(self.page.evaluate('document.getElementById("L4101").classList.contains("keyboard-selected")'))
                    self.page.evaluate('navigateToTextBoundary(false)')
                    self.page.wait_for_function('''() => {
                        const row = keyboardSelectedLine.getBoundingClientRect();
                        return row.top >= document.querySelector('.controls').getBoundingClientRect().bottom && row.bottom <= innerHeight;
                    }''')

    def test_native_layout_preserves_pointer_selection_across_a_paint_boundary(self):
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        selections = []
        for force_full in [False, True]:
            if force_full:
                self.page.add_style_tag(content='#content.text-layout-auto > .text-chunk {content-visibility:visible !important}')
            self.page.locator('#L512').evaluate('row => row.scrollIntoView({block:"center"})')
            self.page.evaluate('async () => { await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); getSelection().removeAllRanges(); }')
            points = self.page.evaluate('''() => ['L512', 'L513'].map((id, index) => {
                const walker = document.createTreeWalker(document.getElementById(id).querySelector('.line-content'), NodeFilter.SHOW_TEXT);
                const nodes = [];
                while (walker.nextNode()) nodes.push(walker.currentNode);
                const node = index ? nodes[nodes.length - 1] : nodes[0];
                const range = document.createRange();
                range.setStart(node, index ? node.textContent.length : 0);
                range.collapse(true);
                const bounds = range.getBoundingClientRect();
                return [bounds.x, bounds.y + bounds.height / 2];
            })''')
            self.page.mouse.move(*points[0])
            self.page.mouse.down()
            self.page.mouse.move(*points[1], steps=12)
            self.page.mouse.up()
            selections.append(self.page.evaluate('getSelection().toString()'))
        self.assertTrue(selections[0].strip())
        self.assertEqual(selections[0], selections[1])

    def test_print_and_unsupported_capabilities_use_full_layout(self):
        self.page.evaluate('async () => { await fgSwitchView("text"); }')
        self.page.emulate_media(media='print')
        self.assertEqual(self.page.evaluate('Array.from(document.querySelectorAll("#content > .text-chunk"), chunk => getComputedStyle(chunk).contentVisibility)'), ['visible'] * 9)
        self.assertEqual(self.page.locator('#textViewport').evaluate('node => getComputedStyle(node).overflowX'), 'visible')
        self.page.emulate_media(media='screen')
        for capability in ['content-visibility: auto',
                           'contain-intrinsic-block-size: calc(2 * (max(13px * 1.5 + 4px, 21px) + 2px))',
                           'selector(:has(*))']:
            with self.subTest(capability=capability):
                self.page.goto(self.server.url, wait_until='load')
                self.page.evaluate('''capability => {
                    const originalSupports = CSS.supports.bind(CSS);
                    CSS.supports = (...arguments) => arguments[0] === capability ? false : originalSupports(...arguments);
                }''', capability)
                self.page.evaluate('async () => { await fgSwitchView("text"); }')
                self.assertFalse(self.page.evaluate('document.getElementById("content").classList.contains("text-layout-auto")'))
                self.assertEqual(self.page.locator('#content .line-container').count(), 4203)

    def test_layout_refresh_counts_blank_hidden_and_grouped_rows_without_geometry_reads(self):
        result = self.page.evaluate('''async () => {
            await fgSwitchView('text');
            const content = document.getElementById('content');
            const chunk = content.firstElementChild;
            const rows = Array.from(chunk.querySelectorAll('.line-container'));
            rows[0].querySelector('.line-content').replaceChildren();
            rows[1].style.display = 'none';
            rows[2].classList.add('grp-hidden');
            content.classList.add('grouped');
            const original = Element.prototype.getBoundingClientRect;
            Element.prototype.getBoundingClientRect = () => { throw new Error('Layout refresh must not measure geometry'); };
            try { fgRefreshTextLayout([chunk]); }
            finally { Element.prototype.getBoundingClientRect = original; }
            return getComputedStyle(chunk).containIntrinsicBlockSize;
        }''')
        self.assertEqual(float(result[:-2]), 21.5 + 509 * 25.5)

    def test_revealed_long_rows_keep_their_horizontal_text_extent(self):
        result = self.page.evaluate('''async () => {
            await fgSwitchView('text');
            const row = document.getElementById('L4101');
            const text = row.querySelector('.line-content');
            text.append(' long_argument=' + 'abcdefgh'.repeat(128));
            row.scrollIntoView({block:'center', inline:'end'});
            row.focus({preventScroll:true});
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const chunk = row.parentElement.getBoundingClientRect();
            const bounds = text.getBoundingClientRect();
            return {chunkRight:chunk.right, textRight:bounds.right, width:bounds.width,
                scrollWidth:document.getElementById('textViewport').scrollWidth,
                rootWidth:document.documentElement.scrollWidth, viewportWidth:document.documentElement.clientWidth};
        }''')
        self.assertGreater(result['width'], 5000)
        self.assertGreater(result['scrollWidth'], 5000)
        self.assertLessEqual(result['rootWidth'], result['viewportWidth'])
        self.assertGreaterEqual(result['chunkRight'], result['textRight'])

    def test_old_anchor_scroll_cannot_override_a_new_keyboard_target(self):
        result = self.page.evaluate('''async () => {
            await fgSwitchView('text');
            history.replaceState(null, '', '#L4101');
            handleAnchorNavigation();
            navigateToTextBoundary(false);
            await new Promise(resolve => setTimeout(resolve, 200));
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const row = keyboardSelectedLine.getBoundingClientRect();
            return {selected:keyboardSelectedLine.id, top:row.top, bottom:row.bottom,
                toolbar:document.querySelector('.controls').getBoundingClientRect().bottom, height:innerHeight};
        }''')
        self.assertEqual(result['selected'], 'L2')
        self.assertGreaterEqual(result['top'], result['toolbar'])
        self.assertLessEqual(result['bottom'], result['height'])

    def test_touch_high_density_layout_keeps_fold_controls_and_far_anchors_reachable(self):
        with self.browser.new_context(viewport=dict(width=390, height=844), device_scale_factor=2,
                                      is_mobile=True, has_touch=True) as context:
            page = context.new_page()
            page.on('pageerror', lambda error: self.errors.append(str(error)))
            page.goto(self.server.url, wait_until='load')
            page.evaluate('async () => { await fgSwitchView("text"); }')
            self.assertTrue(page.evaluate('matchMedia("(any-pointer: coarse)").matches'))
            self.assertGreaterEqual(page.locator('#L2 .fold-icon').bounding_box()['width'], 44)
            page.locator('#L2 .fold-icon').tap()
            self.assertEqual(page.locator('#L4101').evaluate('row => row.style.display'), 'none')
            page.locator('#L2 .fold-icon').tap()
            self.assertEqual(page.locator('#L4101').evaluate('row => row.style.display'), '')
            page.evaluate('''() => { location.hash = '#L4101'; }''')
            page.wait_for_function('''() => {
                const row = document.getElementById('L4101').getBoundingClientRect();
                return row.top >= document.querySelector('.controls').getBoundingClientRect().bottom && row.bottom <= innerHeight;
            }''')
            self.assertEqual(page.locator('#content .line-container').count(), 4203)

    def test_real_mixed_capture_keeps_complete_spaces_flags_sources_and_extents(self):
        with (Path(helpers.ROOT) / 'samples/sample_mixed.jsonl').open() as source:
            metadata, records = uftrace.read_export(source)
        span = records[-1]['time_ns'] - records[0]['time_ns'] + 1000000
        trace = self.directory / 'mixed-layout.jsonl'
        with trace.open('w') as destination:
            destination.write(json.dumps(metadata) + '\n')
            for repetition in range(4):
                for record in records:
                    destination.write(json.dumps(dict(record, time_ns=record['time_ns'] + repetition * span)) + '\n')
            destination.write(json.dumps(dict(type='end', complete=True)) + '\n')
        self.kernel.run(str(trace), '--timeline', '--enable-fold', '--filter',
                        '--user-src', str(Path(helpers.ROOT) / 'samples'),
                        '--report-mode', 'sidecar', out='report/mixed-layout')
        self.page.goto(urljoin(self.server.url, 'mixed-layout/index.html'), wait_until='load')
        result = self.page.evaluate('''async () => {
            await fgSwitchView('text');
            const content = document.getElementById('content');
            const folding = fgGetTextFolding();
            const spaces = Array.from(new Set(folding.records.map(row => row.getAttribute('data-space')).filter(Boolean))).sort();
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            await settle();
            const estimated = content.getBoundingClientRect().height;
            const baseline = document.createElement('style');
            baseline.textContent = '#content.text-layout-auto > .text-chunk {content-visibility:visible !important}';
            document.head.appendChild(baseline);
            await settle();
            const full = content.getBoundingClientRect().height;
            baseline.remove();
            document.getElementById('filterInput').value = 'space:kernel AND irqs:^d$';
            applyFilter();
            const visible = Array.from(content.querySelectorAll('.line-container')).filter(row => row.style.display !== 'none');
            const filtered = {count:visible.length, spaces:Array.from(new Set(visible.map(row => row.dataset.space))),
                irqs:Array.from(new Set(visible.map(row => row.dataset.irqs)))};
            clearFilter();
            const user = Array.from(content.querySelectorAll('.line-container.expandable[data-space=user]')).at(-1);
            toggleExpand(user.dataset.lineId);
            await fgFill(user.nextElementSibling);
            await settle();
            return {enabled:content.classList.contains('text-layout-auto'), version:folding.records[0].version,
                rows:content.querySelectorAll('.line-container').length, records:folding.records.length, spaces,
                estimated, full, filtered, source:user.nextElementSibling.textContent,
                sourceLayout:getComputedStyle(user.parentElement).contentVisibility,
                boundaries:content.querySelectorAll('[data-boundary=user-kernel]').length};
        }''')
        self.assertTrue(result['enabled'])
        self.assertEqual(result['version'], 3)
        self.assertGreater(result['rows'], 4096)
        self.assertEqual(result['rows'], result['records'])
        self.assertEqual(result['spaces'], ['kernel', 'user'])
        self.assertAlmostEqual(result['estimated'], result['full'], delta=1)
        self.assertGreater(result['filtered']['count'], 0)
        self.assertEqual(result['filtered']['spaces'], ['kernel'])
        self.assertEqual(set(result['filtered']['irqs']), {'d', 'D'})
        self.assertIn('demo_mixed.c', result['source'])
        self.assertEqual(result['sourceLayout'], 'visible')
        self.assertGreater(result['boundaries'], 0)
