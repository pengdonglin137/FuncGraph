"""Structured uftrace import, report delivery and cross-address-space navigation."""

import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import helpers
from fglib import export, inputs, timeline, uftrace
from fglib.viewer import create_server
from test_timeline import decode, invariant_errors


ORIGIN = 18446744000000000000
HEADER = dict(type='metadata', schema=uftrace.SCHEMA, order='streams', timestamp_unit='ns',
              depth_basis='address-space', clock='mono', kernel_settings={'trace_clock': 'local [mono] boot'})


def call(kind, space, timestamp, depth, name, address, tid=42, cpu=0, **fields):
    return dict(type=kind, space=space, time_ns=ORIGIN + timestamp, depth=depth, name=name,
                address=hex(address), tid=tid, cpu=cpu if space == 'kernel' else None, **fields)


def capture():
    return [call('entry', 'user', 100, 0, 'main', 1, comm='demo', source={'file': 'demo.c', 'line': 2}),
            call('entry', 'user', 200, 1, 'work<int>', 2),
            call('exit', 'user', 650, 1, 'work<int>', 2),
            call('exit', 'user', 900, 0, 'main', 1),
            call('entry', 'kernel', 240, 0, 'work<int>', 3, retaddr='0xffff0000'),
            call('entry', 'kernel', 320, 1, 'inner', 4, args=['0x0']),
            call('exit', 'kernel', 520, 1, 'inner', 4, retval='0xfffffffffffffffe'),
            call('exit', 'kernel', 620, 0, 'work<int>', 3),
            call('entry', 'kernel', 700, 0, 'second', 5),
            call('exit', 'kernel', 810, 0, 'second', 5),
            call('entry', 'kernel', 915, 0, 'outside', 6),
            call('exit', 'kernel', 950, 0, 'outside', 6)]


def lifecycle_event(phase, timestamp):
    return dict(type='event', space='kernel', time_ns=ORIGIN + timestamp, tid=42, cpu=0,
                name=f'sched:sched_process_{phase}', text='pid=42')


def stream(records, metadata=HEADER, complete=True):
    return '\n'.join(json.dumps(record) for record in [metadata, *records, {'type': 'end', 'complete': complete}]) + '\n'


def build(records, metadata=HEADER):
    return inputs.UftraceInput(io.StringIO(stream(records, metadata))).build(enable_fold=True)


def invoke(*args):
    return subprocess.run([sys.executable, str(Path(helpers.__file__).resolve().parents[1] / 'funcgraph.py'),
                           '--no-config', *map(str, args)], capture_output=True, text=True)


class UftraceImportTest(unittest.TestCase):
    def test_kernel_context_bits_are_preserved_when_joined_under_user(self):
        records = capture()
        records[4].update(common_flags=0x85, common_preempt_count=0x23)
        records[5].update(common_flags=0x48, common_preempt_count=0)
        records[6].update(common_flags=0x11, common_preempt_count=1)
        records[7].update(common_flags=0, common_preempt_count=1)
        builder = build(records)
        data = builder.to_data()
        cols = data['cols']
        self.assertEqual(cols['parent'][2:4], [1, 2])
        self.assertEqual(data['preempt_base'], 1)
        self.assertEqual(cols['flags'][2], timeline.F_BOUNDARY | timeline.F_HAS_LAT | timeline.F_IRQOFF |
                         timeline.F_BHOFF | 1 << timeline.F_RESCHED_SHIFT | 3 << timeline.F_PREEMPT_SHIFT |
                         2 << timeline.F_MIGRATE_SHIFT)
        self.assertEqual(cols['flags'][3], timeline.F_HAS_LAT | timeline.CTX_NMI)
        for index in (0, 1):
            self.assertFalse(cols['flags'][index] & timeline.F_HAS_LAT)
            self.assertIsNone(builder.parsed[cols['line'][index] - 1].preempt_depth)
        for lineno, expected in ((cols['line'][2], ('D', 'n', '.', '3')),
                                 (cols['line'][3], ('.', '.', 'Z', '0')),
                                 (cols['eline'][3], ('d', '.', 's', '1'))):
            row = builder.parsed[lineno - 1]
            self.assertEqual((row.irqs_off, row.need_resched, row.hardirq_softirq, row.preempt_depth), expected)
        records[6]['common_flags'] = 0x81
        sections = build(records).to_data()['sections']
        self.assertEqual({section[1] for section in sections},
                         {timeline.SEC_IRQ, timeline.SEC_BH, timeline.SEC_PREEMPT})

    def test_kernel_context_matches_linux_flag_combinations(self):
        for raw, expected in ((0, '....'), (0x01, 'd...'), (0x80, 'b...'), (0x81, 'D...'),
                              (0x04, '.n..'), (0x24, '.N..'), (0x20, '.p..'), (0x02, '.l..'),
                              (0x22, '.L..'), (0x06, '.b..'), (0x26, '.B..'),
                              (0x08, '..h.'), (0x10, '..s.'), (0x18, '..H.'),
                              (0x40, '..z.'), (0x58, '..Z.')):
            with self.subTest(raw=raw):
                raw_record = dict(common_flags=raw, common_preempt_count=0x42)
                flags, context, latency = uftrace._latency(raw_record, True)
                self.assertEqual(latency, expected[:3] + '24')
                self.assertEqual((flags, context), timeline.TimelineBuilder._lat_flags(expected[:3] + '24'))

    def test_kernel_release_disambiguates_reused_flag_bit_and_missing_data(self):
        records = capture()
        records[4].update(common_flags=0x22, common_preempt_count=2)
        records[5].update(common_flags=0x80)
        records[8].update(common_flags=0, common_preempt_count=256)
        for release, expected in (('6.12.99', ('X', 'p')), ('6.13.0-rc1', ('.', 'L')),
                                  ('6.18.56-v8-MY_CUSTOM_KERNEL+', ('.', 'L')),
                                  ('unknown', ('.', 'p'))):
            with self.subTest(release=release):
                metadata = dict(HEADER, kernel_settings=dict(HEADER['kernel_settings'], kernel_release=release))
                builder = build(records, metadata)
                data = builder.to_data()
                cols = data['cols']
                row = builder.parsed[cols['line'][2] - 1]
                self.assertEqual((row.irqs_off, row.need_resched), expected)
                self.assertEqual(data['preempt_base'], 1)
                for index in (3, 4):
                    self.assertFalse(cols['flags'][index] & timeline.F_HAS_LAT)
                    self.assertIsNone(builder.parsed[cols['line'][index] - 1].irqs_off)

    def test_lifecycle_keeps_exact_kernel_milestones_and_clock_separation(self):
        records = capture()[:4] + [lifecycle_event('exec', 70), lifecycle_event('exit', 980)]
        for metadata, shared in ((HEADER, True), (dict(HEADER, clock=None), False)):
            with self.subTest(shared=shared):
                builder = build(records, metadata)
                data = builder.to_data()
                events = data['uftrace']['lifecycle']
                self.assertEqual(data['uftrace']['origin_ns'], str(ORIGIN + 70))
                self.assertEqual([(timestamp, phase) for _, timestamp, phase, _ in events],
                                 [(0, 'exec'), (910, 'exit')])
                self.assertEqual(events[0][0] == data['cols']['task'][0], shared)
                self.assertEqual(events[0][0], events[1][0])
                self.assertEqual(data['n'], 2)
                self.assertEqual(data['stats']['user_kernel_boundaries'], 0)
                for _, _, phase, line in events:
                    self.assertIn(f'sched:sched_process_{phase}', builder.parsed[line - 1].raw_line)
        only_exec = build(capture()[:4] + [lifecycle_event('exec', 70)]).to_data()
        self.assertEqual([event[2] for event in only_exec['uftrace']['lifecycle']], ['exec'])
        self.assertNotIn('lifecycle', build(capture()).to_data()['uftrace'])

    def test_shared_clock_joins_at_two_actual_depths_and_preserves_ns(self):
        builder = build(capture())
        data = builder.to_data()
        self.assertEqual(invariant_errors(data), [])
        columns = data['cols']
        self.assertEqual(data['uftrace']['origin_ns'], str(ORIGIN + 100))
        self.assertEqual(decode(data), ([0, 100, 140, 220, 600, 815], [800, 550, 520, 420, 710, 850]))
        self.assertEqual(columns['parent'], [-1, 0, 1, 2, 0, -1])
        self.assertEqual(columns['depth'], [0, 1, 2, 3, 1, 0])
        self.assertEqual(columns['cpu'], [-1, -1, 0, 0, 0, 0])
        self.assertEqual([bool(flag & timeline.F_BOUNDARY) for flag in columns['flags']], [False, False, True, False, True, False])
        self.assertNotEqual(columns['name'][1], columns['name'][2])
        self.assertEqual(data['chains'], [])
        self.assertEqual(data['strings'][columns['ret'][3]], '0xfffffffffffffffe')
        self.assertEqual(data['strings'][columns['caller'][2]], '0xffff0000')
        self.assertEqual(data['strings'][columns['args'][3]], '0x0')
        for index in [2, 4]:
            row = builder.parsed[columns['line'][index] - 1]
            parent = builder.parsed[columns['line'][columns['parent'][index]] - 1]
            self.assertTrue(row.boundary)
            self.assertIn(parent.fold_id, row.fold_ancestors.split())
        by_space = {(row['name'], row['space']) for row in export.build(data)['functions']}
        self.assertTrue({('work<int>', 'user'), ('work<int>', 'kernel')} <= by_space)

    def test_only_containment_on_same_task_and_verified_clock_is_linked(self):
        records = capture() + [call('entry', 'kernel', 300, 0, 'other', 7, tid=7, cpu=1),
                               call('exit', 'kernel', 400, 0, 'other', 7, tid=7, cpu=1)]
        for metadata, count in ((HEADER, 2), (dict(HEADER, clock=None), 0),
                                (dict(HEADER, kernel_settings={'trace_clock': '[local] mono'}), 0)):
            with self.subTest(metadata=metadata):
                data = build(records, metadata).to_data()
                self.assertEqual(data['stats']['user_kernel_boundaries'], count)
                self.assertEqual(invariant_errors(data), [])
        records[7]['address'] = '0x55'
        data = build(records).to_data()
        self.assertEqual(data['stats']['user_kernel_boundaries'], 1)
        self.assertGreater(data['stats']['truncated'], 0)
        self.assertGreater(data['stats']['detached_kernel_roots'], 0)
        self.assertEqual(invariant_errors(data), [])

    def test_zero_timestamp_user_loss_breaks_pairing_at_native_stream_position(self):
        records = capture()
        records.insert(2, dict(type='lost', space='user', tid=42, cpu=None, time_ns=0, count=2))
        builder = build(records)
        data = builder.to_data()
        self.assertEqual(data['stats']['lost'], 1)
        self.assertEqual(data['stats']['truncated'], 2)
        self.assertEqual(data['stats']['orphans'], 2)
        self.assertEqual(data['stats']['user_kernel_boundaries'], 0)
        self.assertIn('timestamp ?', builder.parsed[4].raw_line)

    def test_rejects_incomplete_and_invalid_streams(self):
        for text in (stream(capture(), complete=False), stream(capture()) + '{}',
                     stream(capture()).rsplit('{"type": "end"', 1)[0],
                     stream([dict(capture()[0], space='unknown')]),
                     stream([capture()[1], capture()[0]])):
            with self.subTest(text=text[:200]), self.assertRaises(uftrace.UftraceInputError):
                uftrace.read_export(io.StringIO(text))

    def test_cli_three_populations_without_vmlinux_and_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for space in ('user', 'kernel', 'mixed'):
                records = [record for record in capture() if record['space'] == space or space == 'mixed']
                with gzip.open(root / 'capture.jsonl.gz', 'wt', encoding='utf-8') as output:
                    output.write(stream(records))
                html = root / (space + '.html')
                result = invoke(root / 'capture.jsonl.gz', '--timeline', '--enable-fold', '--filter', '--output', html)
                self.assertEqual(result.returncode, 0, result.stderr)
                text = html.read_text(encoding='utf-8')
                self.assertEqual(' data-boundary="user-kernel"' in text, space == 'mixed')
                data = helpers.json_script(text, 'tlData')
                self.assertEqual(data['uftrace']['spaces'], ['kernel', 'user'] if space == 'mixed' else [space])
                self.assertEqual(invariant_errors(data), [])
            (root / 'baseline.jsonl').write_text(stream(capture()[:4]), encoding='utf-8')
            result = invoke(root / 'capture.jsonl.gz', '--compare', root / 'baseline.jsonl', '--output', root / 'compare.html')
            self.assertEqual(result.returncode, 0, result.stderr)
            comparison = helpers.json_script((root / 'compare.html').read_text(encoding='utf-8'), 'cmpData')
            self.assertEqual({row[11] for row in comparison['rows'] if row[0] == 'work<int>'}, {'user', 'kernel'})

    def test_kernel_entry_sources_are_separate_from_recorded_user_and_raw_callers(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'kernel').mkdir()
            (root / 'kernel/fake.c').write_text('/* user file */\nint user_only;\n', encoding='utf-8')
            records = capture()
            records[1]['source'] = {'file': 'kernel/fake.c', 'line': 2}
            trace = root / 'capture.jsonl'
            trace.write_text(stream(records), encoding='utf-8')
            baseline = None
            for extra in ([], ['--no-text-view', '--keep-source'], ['--no-text-view']):
                with self.subTest(options=extra):
                    html, _ = kernel.run(str(trace), '--no-config', '--timeline', '--enable-fold',
                                         '--func-links', '--base-url', 'https://kernel.invalid/src',
                                         '--user-src', str(root), *extra)
                    data = helpers.json_script(html, 'tlData')
                    pool, lines = (helpers.json_script(html, key) for key in ('fgSrc', 'fgSrcLines'))
                    self.assertEqual(data['sources']['callers'], {})
                    self.assertEqual(data['inline'], {})
                    cols = data['cols']
                    names = [data['strings'][name] for name in cols['name']]
                    kernel_index = next(index for index, name in enumerate(names)
                                        if name == 'work<int>' and not cols['flags'][index] & timeline.F_USER)
                    user_index = names.index('work<int>')
                    if extra == ['--no-text-view']:
                        self.assertEqual(pool, [])
                        self.assertEqual(lines, {})
                        continue
                    self.assertEqual(data['sources']['functions'][f'{cols["name"][kernel_index]}:-1'],
                                     ['kernel/fake.c', 2, 'https://kernel.invalid/src/kernel/fake.c#L2'])
                    kernel_block = pool[lines[str(cols['line'][kernel_index])]]
                    user_block = pool[lines[str(cols['line'][user_index])]]
                    self.assertIn('kernel function entry', kernel_block)
                    self.assertIn('int b;', kernel_block)
                    self.assertNotIn('user_only', kernel_block)
                    self.assertIn('int user_only;', user_block)
                    self.assertIn('recorded user source', user_block)
                    self.assertNotIn('https://kernel.invalid', user_block)
                    if baseline is None:
                        baseline = pool, lines
                        self.assertIn('class="func-name-link" href="https://kernel.invalid/src/kernel/fake.c#L2"', html)
                    else:
                        self.assertEqual((pool, lines), baseline)
            html, _ = kernel.run(str(trace), '--no-config', '--timeline', '--user-src', str(root))
            self.assertFalse(helpers.json_script(html, 'tlData')['sources']['functions'])
            self.assertTrue(all('kernel function entry' not in block for block in helpers.json_script(html, 'fgSrc')))

    def test_entry_excerpts_work_in_text_only_for_each_address_space(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'demo.c').write_text('/* user source */\nint user_main;\n', encoding='utf-8')
            trace = root / 'capture.jsonl'
            for space in ('user', 'kernel', 'mixed'):
                with self.subTest(space=space):
                    records = [record for record in capture() if space == 'mixed' or record['space'] == space]
                    trace.write_text(stream(records), encoding='utf-8')
                    html, _ = kernel.run(str(trace), '--no-config', '--func-links', '--user-src', str(root),
                                         '--base-url', 'https://kernel.invalid/src')
                    pool = helpers.json_script(html, 'fgSrc')
                    self.assertIsNone(helpers.json_script(html, 'tlData'))
                    self.assertEqual(any('kernel function entry' in block for block in pool), space != 'user')
                    self.assertEqual(any('recorded user source' in block for block in pool), space != 'kernel')
                    self.assertEqual(len(pool), 2 if space == 'mixed' else 1)


class UftraceBrowserTest(unittest.TestCase):
    def test_timeline_boundary_and_deferred_text_filter(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:
                self.skipTest(str(error))
            root = Path(directory)
            kernel = helpers.FakeKernel()
            self.addCleanup(kernel.close)
            records = capture() + [lifecycle_event('exit', 980)]
            records[0]['source'] = {'file': str(Path(kernel.ksrc) / 'kernel' / 'fake.c'), 'line': 2}
            records[1]['source'] = {'file': 'demo.c', 'line': 3}
            trace = root / 'capture.jsonl'
            trace.write_text(stream(records), encoding='utf-8')
            (root / 'demo.c').write_text('/* recorded user source */\nint main(void) {}\nint user_work(void) {}\n',
                                         encoding='utf-8')
            kernel_url = 'https://kernel.invalid/source'
            kernel.run(str(trace), '--no-config', '--timeline', '--enable-fold', '--filter', '--user-src', str(root),
                       '--func-links', '--base-url', kernel_url, '--report-mode', 'sidecar', out='report')
            with create_server(Path(kernel.dir) / 'report') as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    page = browser.new_page(viewport={'width': 1450, 'height': 900}, device_scale_factor=1.25)
                    errors, requests = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: requests.append(request.url))
                    page.goto(server.url)
                    page.locator('.tl-space-key').wait_for()
                    page.get_by_role('button', name='Exec / exit').click()
                    page.get_by_text('exit (task exiting)', exact=True).wait_for(state='visible')
                    self.assertEqual(page.get_by_text('exec (new image installed)', exact=True).count(), 0)
                    page.evaluate('''() => {
                        const model = fgView('timeline').probe();
                        model.select(2);
                    }''')
                    boundary = page.locator('[data-context-list=stack] [data-space-boundary]')
                    self.assertEqual(boundary.count(), 1)
                    self.assertIn('work<int>', boundary.inner_text())
                    kernel_link = boundary.locator('.tl-context-source a').get_attribute('href')
                    self.assertTrue(kernel_link.startswith(kernel_url + '/'), kernel_link)
                    stack = page.locator('[data-context-list=stack]')
                    user_twin = stack.locator('.tl-source-open', has_text='demo.c:3')
                    self.assertIsNone(user_twin.get_attribute('href'))
                    outside = stack.locator('.tl-context-source', has_text='fake.c:2').first
                    self.assertEqual(outside.locator('a, button').count(), 0)
                    self.assertEqual(stack.locator('a[href^="' + kernel_url + '"]').count(), 1)
                    self.assertTrue(page.evaluate('''async () => {
                        const api = fgView('timeline'), model = api.probe();
                        const canvas = document.querySelector('.tl-canvas');
                        const context = canvas.getContext('2d'), original = context.fillRect, lines = [];
                        context.fillRect = function (x, y, w, h) {
                            if (this.fillStyle === '#5b6573' && h === 0.8) lines.push([x, w]);
                            return original.call(this, x, y, w, h);
                        };
                        try {
                            api.draw();
                            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        } finally { context.fillRect = original; }
                        const [start, end] = model.view(), scale = canvas.clientWidth / (end - start);
                        const left = (model.sStart[2] - start) * scale;
                        const width = (model.sEnd[2] - model.sStart[2]) * scale;
                        return lines.some(([x, w]) => Math.abs(x - left) < 2 && Math.abs(w - width) < 2);
                    }'''))
                    user_twin.click()
                    page.locator('.tl-cs-src .current-line', has_text='int user_work(void) {}').wait_for()
                    page.locator('.tl-ftext').fill('space:user')
                    page.locator('.tl-ftext').press('Enter')
                    page.locator('.tl-fcount', has_text='2 of 6 calls').wait_for()
                    self.assertFalse(any(url.endswith('text.html') for url in requests))
                    page.locator('[data-act=fclear]').click()
                    page.evaluate("fgSwitchView('text')")
                    boundary = page.locator('#content [data-boundary]')
                    self.assertEqual(boundary.count(), 2)
                    self.assertEqual(page.locator('#content [data-space=user] a[href^="' + kernel_url + '"]').count(), 0)
                    kernel_entry = page.locator('#content .fold-entry[data-space=kernel][data-func="work<int>"]')
                    self.assertEqual(kernel_entry.locator('a.func-name-link').get_attribute('href'),
                                     kernel_url + '/kernel/fake.c#L2')
                    kernel_entry.click()
                    page.locator('.expanded-content:visible .current-line', has_text='int b;').wait_for()
                    page.evaluate('fgView("timeline").probe().select(2)')
                    page.get_by_role('button', name='Timeline', exact=True).click()
                    page.locator('.tl-cs summary', has_text='Kernel function source').wait_for(state='visible')
                    page.locator('.tl-cs-src .current-line', has_text='int b;').wait_for(state='visible')
                    self.assertEqual(page.locator('.tl-cs-at [data-callsite-position]').count(), 0)
                    page.get_by_role('button', name='Text', exact=True).click()
                    page.locator('#content .fold-entry[data-space=user] .fold-icon').first.click()
                    self.assertFalse(boundary.first.is_visible())
                    self.assertEqual(errors, [])
                finally:
                    server.shutdown()
                    thread.join()
                    browser.close()
