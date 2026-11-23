"""Range pages stay bounded without changing aggregation, filtering or full CSV."""
import csv
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import helpers
from benchmark_range import fixture
from benchmark_range_offcpu import COMMIT_RANGE
from fglib.viewer import create_server
from test_uftrace_import import call, stream


class RangePagingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest(str(error))
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        trace = Path(cls.kernel.dir) / 'template.jsonl'
        trace.write_text(stream([call('entry', 'user', 10, 0, 'work', 1), call('exit', 'user', 100, 0, 'work', 1)]))
        cls.kernel.run(str(trace), '--no-config', '--timeline')
        cls.template = Path(cls.kernel.dir) / 'out.html'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-range-page-')
        self.addCleanup(self.directory.cleanup)
        self.serial = 0
        self.errors = []
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, count=650, update=None, sidecar=False, select_range=True):
        self.serial += 1
        if sidecar:
            records = []
            for index in range(count):
                name, start = f'function_{index:06d}', index * 1000
                records.extend([call('entry', 'user', start, 0, name, 1),
                                call('entry', 'kernel', start + 10, 0, name, 2),
                                call('exit', 'kernel', start + 20, 0, name, 2),
                                call('exit', 'user', start + 30, 0, name, 1)])
            trace = Path(self.directory.name) / f'mixed-{self.serial}.jsonl'
            trace.write_text(stream(records), encoding='utf-8')
            destination = Path(self.directory.name) / f'sidecar-{self.serial}'
            self.kernel.run(str(trace), '--no-config', '--timeline', '--no-text-view', '--report-mode', 'sidecar', out=str(destination))
            server = create_server(destination)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = server.url
        else:
            report = Path(self.directory.name) / f'range-{self.serial}.html'
            fixture(self.template, report, count)
            if update:
                html = report.read_text(encoding='utf-8')
                match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
                data = json.loads(match[2])
                update(data)
                report.write_text(html[:match.start(2)] + json.dumps(data).replace('<', r'\u003c') + html[match.end(2):], encoding='utf-8')
            url = report.as_uri()
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        if select_range:
            self.page.evaluate("end => fgView('timeline').probe().range(0, end)", count * 1000)

    def names(self):
        return self.page.locator('.tl-range-table tbody tr[data-name]').evaluate_all('(rows) => rows.map(row => row.dataset.name)')

    def status(self):
        return self.page.locator('.tl-range-footer [role=status]').inner_text()

    def next(self):
        self.page.locator('[data-act=range-next]').click()

    def export(self):
        with self.page.expect_download() as download:
            self.page.locator('[data-act=range-csv]').click()
        return Path(download.value.path()).read_bytes()

    def rows(self):
        return list(csv.DictReader(io.StringIO(self.export().decode('utf-8-sig'))))

    def test_large_interactive_range_yields_and_preserves_complete_csv(self):
        self.check_large_interactive_range_yields_and_preserves_complete_csv()

    def test_large_interactive_range_falls_back_without_message_channel(self):
        self.page.add_init_script('window.MessageChannel = undefined;')
        self.check_large_interactive_range_yields_and_preserves_complete_csv()

    def test_wait_heavy_call_breakdown_yields_pauses_and_discards_replaced_work(self):
        start, count = 1000, 32000
        end = start + count * 1000
        waits = [[0, start + ordinal * 1000, start + ordinal * 1000 + 400, 1, 'sleep', 'S', 0, 0, ordinal % 2, -1]
                 for ordinal in range(count)]
        for ordinal in (6, 248, 15013):
            waits[ordinal], waits[ordinal + 7] = waits[ordinal + 7], waits[ordinal]
        waits.extend([[0, 0, start, 1, 'sleep', 'S', 0, 0, 0, -1],
                      [0, start, end, 0, 'sleep', 'S', 0, 0, 0, -1],
                      [0, end, end + 1000, 1, 'sleep', 'S', 0, 0, 1, -1]])

        def update(data):
            data['cols'].update(start=[start, end + 1000], dur=[end - start, 500], adj=None)
            data['offcpu'] = waits

        self.open_report(2, update=update, select_range=False)
        state = self.page.evaluate('''async () => {
            const probe = fgView('timeline').probe();
            probe.select(1);
            window.selectWaitHeavyCall = () => {
                const now = performance.now;
                let ticks = 0;
                try {
                    performance.now = () => 4 * ++ticks;
                    probe.select(0);
                } finally { performance.now = now; }
                return document.querySelector('[data-detail-breakdown]')?.textContent;
            };
            const pending = selectWaitHeavyCall();
            await fgSwitchView('text');
            return pending;
        }''')
        self.assertEqual(state, 'Calculating time breakdown…')
        pending = self.page.locator('[data-detail-breakdown]')
        self.page.evaluate('''async () => {
            for (let frame = 0; frame < 3; frame++) await new Promise(requestAnimationFrame);
        }''')
        self.assertEqual(pending.count(), 1)
        self.page.locator('.view-tab[data-view=timeline]').click()
        chart = self.page.locator('.an-bd')
        chart.wait_for()
        self.assertEqual(chart.locator('span').evaluate_all('(parts) => parts.map(part => part.title)'),
                         ['own code 19.20 ms', 'blocked 6.400 ms', 'preempted 6.400 ms'])
        self.assertEqual(self.page.evaluate('''async () => {
            const probe = fgView('timeline').probe();
            probe.select(1);
            probe.select(0);
            return document.querySelector('[data-detail-breakdown]') === null;
        }'''), True)

        self.open_report(2, update=update, select_range=False)
        self.assertEqual(self.page.evaluate('''async () => {
            const probe = fgView('timeline').probe(), now = performance.now;
            let ticks = 0;
            probe.select(1);
            try {
                performance.now = () => 4 * ++ticks;
                probe.select(0);
            } finally { performance.now = now; }
            const queued = !!document.querySelector('[data-detail-breakdown]');
            probe.select(1);
            for (let frame = 0; frame < 3; frame++) await new Promise(requestAnimationFrame);
            return [queued, probe.sel(), document.querySelector('.an-bd span')?.title];
        }'''), [True, 1, 'own code 500 ns'])

    def test_wait_heavy_range_defers_and_preserves_scope_during_navigation(self):
        end = 40000000
        waits = [[0, ordinal * 1000, ordinal * 1000 + 550.25, 1, 'sleep', 'S', 0, 0, 0, -1]
                 for ordinal in range(40000)]
        for ordinal in (6, 248, 15013, 39700):
            waits[ordinal], waits[ordinal + 7] = waits[ordinal + 7], waits[ordinal]
        waits.extend([[0, 1000, 2500.75, 0, 'sleep', 'S', 0, 0, 0, -1],
                      [0, end - 120000, None, 0, 'sleep', 'S', 0, 0, 0, -1],
                      [1, 2000, end - 1000, 0, 'sleep', 'S', 0, 0, 0, -1]])

        def update(data):
            data['tasks'].append(dict(data['tasks'][0], pid=2, comm='other'))
            data['cols'].update(task=[0, 1], start=[0, 0], dur=[end, end - 10], adj=[end / 2, end / 4])
            data['offcpu'] = waits

        self.open_report(2, update=update, select_range=False)
        self.page.evaluate('''() => {
            window.commitWaitRange = ''' + COMMIT_RANGE + ''';
            fgView('timeline').probe().zoomTo(0, 40000000);
            document.querySelector('[data-ov=off]').click();
        }''')
        pending = self.page.locator('.tl-range-pending')
        table = self.page.locator('.tl-range-table')
        self.assertEqual(self.page.evaluate('''() => {
            commitWaitRange({track: true});
            const status = document.querySelector('.tl-range-pending')?.textContent;
            fgSwitchView('text');
            return status;
        }'''), 'Summarizing off-CPU time…')
        self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))''')
        self.assertEqual(pending.text_content(), 'Summarizing off-CPU time…')
        self.assertEqual(table.count(), 0)
        self.page.locator('.view-tab[data-view=timeline]').click()
        table.wait_for()
        single = self.export()
        row, = list(csv.DictReader(io.StringIO(single.decode('utf-8-sig'))))
        self.assertEqual(row['function'], 'function_000000')
        endpoints = [float(row[key]) for key in ('range_start_ns', 'range_end_ns')]

        def expected_wait(track=None):
            left, right = endpoints
            return sum(max(0, min(end if stop is None else stop, right) - max(begin, left))
                       for task, begin, stop, *_ in waits if track is None or task == track)

        self.assertEqual(float(row['scope_off_cpu_ns']), expected_wait(track=0))
        self.page.evaluate('bounds => fgView("timeline").probe().range(...bounds, 0)', endpoints)
        self.assertEqual(self.export(), single)
        self.page.evaluate('''() => {
            commitWaitRange();
            if (!document.querySelector('.tl-range-pending')) throw Error('Missing pending range');
            fgNotifyFilter('one call', new Set([2]));
            document.querySelector('[data-act=adj]').click();
        }''')
        table.wait_for()
        row, = self.rows()
        self.assertEqual(row['function'], 'function_000001')
        self.assertEqual(row['duration_basis'], 'overhead_subtracted_estimate')
        self.assertEqual([float(row[key]) for key in ('range_start_ns', 'range_end_ns')], endpoints)
        self.assertEqual(float(row['scope_off_cpu_ns']), expected_wait())

        replaced = self.page.evaluate('''() => {
            commitWaitRange({width: .9});
            if (!document.querySelector('.tl-range-pending')) throw Error('Missing pending range');
            fgView('timeline').probe().zoomTo(0, 80000);
            commitWaitRange();
            return !!document.querySelector('.tl-range-table');
        }''')
        self.assertTrue(replaced)
        self.assertLess(float(self.rows()[0]['range_end_ns']), 100000)

        self.page.evaluate('''() => {
            fgView('timeline').probe().zoomTo(0, 40000000);
            commitWaitRange();
            const pending = document.querySelector('.tl-range-pending');
            if (!pending) throw Error('Missing pending range');
            pending.parentElement.querySelector('.tl-close').click();
        }''')
        self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))''')
        self.assertFalse(self.page.locator('#tlDetail').is_visible())

    def check_large_interactive_range_yields_and_preserves_complete_csv(self):
        self.open_report(40000, select_range=False)
        self.page.evaluate('''() => { window.commitFullRange = () => {
            const canvas = document.querySelector('.tl-canvas'), bounds = canvas.getBoundingClientRect();
            const left = Math.ceil(bounds.left) + 1, right = Math.floor(bounds.right) - 1;
            const send = (target, type, clientX) => target.dispatchEvent(new MouseEvent(type, {
                button:0, buttons:type === 'mouseup' ? 0 : 1, shiftKey:true, altKey:true,
                bubbles:true, cancelable:true, clientX, clientY:bounds.top+12,
            }));
            send(canvas, 'mousedown', left);
            send(window, 'mousemove', right);
            send(window, 'mouseup', right);
        }; }''')
        partial = self.page.evaluate('''() => {
            commitFullRange();
            const pane = document.querySelector('#tlDetail');
            return {status:pane.querySelector('.tl-range-pending')?.textContent,
                rows:pane.querySelectorAll('tbody tr').length};
        }''')
        self.assertEqual(partial, dict(status='Summarizing calls…', rows=0))
        self.page.locator('.tl-range-table').wait_for(state='visible')
        self.assertEqual(len(self.names()), 100)
        deferred = self.export()
        rows = list(csv.DictReader(io.StringIO(deferred.decode('utf-8-sig'))))
        self.assertGreater(len(rows), 32768)
        endpoints = [float(rows[0][name]) for name in ('range_start_ns', 'range_end_ns')]

        self.page.evaluate('''() => {
            fgView('timeline').probe().range(0, 2000);
            commitFullRange();
            if (!document.querySelector('.tl-range-pending')) throw Error('Missing pending range');
            document.querySelector('#tlDetail .tl-close').click();
        }''')
        self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))''')
        self.assertFalse(self.page.locator('#tlDetail').is_visible())

        self.page.evaluate('''() => {
            commitFullRange();
            if (!document.querySelector('.tl-range-pending')) throw Error('Missing pending range');
            fgSwitchView('text');
        }''')
        self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))''')
        self.assertEqual(self.page.locator('.tl-range-pending').text_content(), 'Summarizing calls…')
        self.page.locator('.view-tab[data-view=timeline]').click()
        self.page.locator('.tl-range-table').wait_for(state='visible')
        self.assertEqual(self.export(), deferred)

        self.page.evaluate('''endpoints => {
            const model = fgView('timeline').probe();
            model.range(0, 2000);
            model.range(...endpoints);
        }''', endpoints)
        self.assertEqual(self.export(), deferred)

    def test_large_sort_keeps_the_table_and_latest_choice_wins(self):
        self.open_report(40000)
        self.next()
        original = self.names()
        pending = self.page.evaluate('''() => {
            const table = window.originalRangeTable = document.querySelector('.tl-range-table');
            const body = table.tBodies[0], first = body.firstElementChild;
            table.querySelector('th[data-sort=max] button').click();
            table.querySelector('th[data-sort=name] button').click();
            return {busy:table.getAttribute('aria-busy'), sameFirst:body.firstElementChild === first,
                state:table.querySelector('th[data-sort=self]').getAttribute('aria-sort'),
                label:document.querySelector('.tl-range-work-status').textContent,
                paused:[...document.querySelectorAll('[data-act=range-csv], .tl-range-pages :is(button, input)')].every(control => control.disabled)};
        }''')
        self.assertEqual(pending, dict(busy='true', sameFirst=True, state='descending', label='Ordering by function…', paused=True))
        table = self.page.locator('.tl-range-table')
        self.page.wait_for_function('() => document.querySelector("th[data-sort=name]").getAttribute("aria-sort") === "ascending"')
        self.assertEqual(self.names(), [f'function_{index:06d}' for index in range(100)])
        self.assertNotEqual(self.names(), original)
        self.assertIn('Showing 1–100 of 40000', self.status())
        self.assertTrue(table.evaluate('element => element === originalRangeTable'))
        self.assertTrue(table.locator('th[data-sort=name] button').evaluate('element => element === document.activeElement'))
        self.assertIsNone(table.get_attribute('aria-busy'))
        self.assertFalse(self.page.locator('.tl-range-work-status').is_visible())
        exported = self.export()
        rows = list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))
        self.assertEqual([row['function'] for row in rows], [f'function_{index:06d}' for index in range(40000)])

        self.next()
        original = self.names(), self.status()
        self.page.evaluate('''() => {
            originalRangeTable.querySelector('th[data-sort=max] button').click();
            originalRangeTable.querySelector('th[data-sort=name] button').click();
        }''')
        self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))''')
        self.assertEqual((self.names(), self.status()), original)
        self.assertIsNone(table.get_attribute('aria-busy'))
        self.assertEqual(self.export(), exported)

    def test_large_csv_can_cancel_and_pause_without_downloading_partial_bytes(self):
        unsafe = ['=SUM(1,2)', 'line,"quoted"\n函数']
        def update(data):
            data['strings'][:len(unsafe)] = unsafe
        self.open_report(40000, update=update)
        self.page.set_viewport_size({'width': 320, 'height': 700})
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        button = self.page.locator('[data-act=range-csv]')
        status = self.page.locator('.tl-range-work-status')

        pending = button.evaluate('''button => {
            button.focus(); button.click();
            const status = document.querySelector('.tl-range-work-status');
            const snapshot = {label:button.textContent, status:status.textContent,
                focused:document.activeElement === button, paging:!document.querySelector('[data-act=range-next]').disabled,
                inViewport:[button, status, document.querySelector('#tlDetail .tl-close')].every(control => {
                    const bounds = control.getBoundingClientRect();
                    return bounds.left >= 0 && bounds.right <= innerWidth && bounds.width > 0;
                })};
            button.click();
            return snapshot;
        }''')
        self.assertEqual(pending, dict(label='Cancel CSV', status='Preparing CSV…', focused=True, paging=True, inViewport=True))
        self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))''')
        self.assertEqual(downloads, [])
        self.assertEqual(button.inner_text(), 'Export CSV')
        self.assertFalse(status.is_visible())

        button.evaluate('''button => { button.click(); fgSwitchView('text'); }''')
        self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))''')
        self.assertEqual(status.text_content(), 'Preparing CSV…')
        self.assertEqual(downloads, [])
        with self.page.expect_download() as download:
            self.page.locator('.view-tab[data-view=timeline]').click()
        exported = Path(download.value.path()).read_bytes()
        self.assertTrue(exported.startswith(b'\xef\xbb\xbffunction,calls,'))
        self.assertTrue(exported.endswith(b'\r\n'))
        records = list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))
        self.assertEqual(len(records), 40000)
        self.assertEqual({row['function'] for row in records},
                         {"'" + unsafe[0], unsafe[1]} | {f'function_{index:06d}' for index in range(2, 40000)})
        self.assertEqual(button.inner_text(), 'Export CSV')
        self.assertFalse(status.is_visible())
        self.next()
        self.assertEqual(self.export(), exported)

    def test_large_csv_discards_stale_downloads_when_range_context_changes(self):
        self.page.add_init_script('window.MessageChannel = undefined;')
        self.open_report(40000)
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        button = self.page.locator('[data-act=range-csv]')
        state = button.evaluate('''button => {
            button.click();
            document.querySelector('[data-act=range-next]').click();
            const whilePaging = [button.textContent, document.querySelector('.tl-range-footer [role=status]').textContent];
            document.querySelector('th[data-sort=name] button').click();
            return [whilePaging, button.textContent, button.disabled];
        }''')
        self.assertEqual(state, [['Cancel CSV', 'Showing 101–200 of 40000 functions'], 'Export CSV', True])
        self.page.wait_for_function("document.querySelector('th[data-sort=name]').getAttribute('aria-sort') === 'ascending'")
        self.assertEqual(downloads, [])

        for change in ("document.querySelector('#tlDetail .tl-close').click()",
                       "fgNotifyFilter('first 301', new Set(Array.from({length: 301}, (_, index) => index + 1)))",
                       "fgNotifyFilter('all calls', new Set(fgData.read('tlData').cols.line))",
                       "fgView('timeline').probe().range(0, 5000)"):
            with self.subTest(change=change):
                self.page.evaluate("fgNotifyFilter('', null); fgView('timeline').probe().range(0, 40000000)")
                self.assertEqual(button.evaluate(f'''button => {{
                    button.click();
                    const label = button.textContent;
                    {change};
                    return label;
                }}'''), 'Cancel CSV')
                self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))''')
                self.assertEqual(downloads, [])

        self.page.evaluate("fgView('timeline').probe().range(0, 40000000)")
        rows = self.rows()
        self.assertEqual(len(rows), 40000)
        self.assertEqual([rows[0]['function'], rows[-1]['function']], ['function_000000', 'function_039999'])
        self.assertEqual(len(downloads), 1)

    def test_boundaries_reversible_native_keyboard_and_endpoint_focus(self):
        for count in (1, 100, 101, 200, 201):
            with self.subTest(count=count):
                self.open_report(count)
                original = self.names()
                self.assertEqual(len(original), min(100, count))
                self.assertIn(f'1–{min(100, count)} of {count}', self.status())
                if count <= 100:
                    self.assertEqual(self.page.locator('[data-range-page]').count(), 0)
                    continue
                self.assertTrue(self.page.locator('[data-act=range-prev]').is_disabled())
                first = self.page.locator('.tl-range-table tbody tr').first.element_handle()
                self.page.locator('[data-act=range-next]').press('Enter')
                self.assertFalse(first.evaluate('element => element.isConnected'))
                self.assertEqual(len(self.names()), min(100, count - 100))
                if count <= 200:
                    self.assertTrue(self.page.locator('[data-act=range-next]').is_disabled())
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'range-prev')
                self.page.locator('[data-act=range-prev]').press('Space')
                self.assertEqual(self.names(), original)
                self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'range-next')

    def test_all_functions_reachable_without_reaggregation_resorting_or_dom_growth(self):
        self.open_report(10003)
        result = self.page.evaluate('''() => {
            const originalSort = Array.prototype.sort, originalSet = Map.prototype.set;
            Array.prototype.sort = () => { throw new Error('Paging must not sort'); };
            Map.prototype.set = function(key, value) {
                if (value && typeof value.calls === 'number' && typeof value.incl === 'number') throw new Error('Paging must not aggregate');
                return originalSet.call(this, key, value);
            };
            try {
                const names = [], sizes = [];
                while (true) {
                    const rows = Array.from(document.querySelectorAll('.tl-range-table tbody tr[data-name]'));
                    sizes.push(rows.length);
                    names.push(...rows.map(row => row.dataset.name));
                    const next = document.querySelector('[data-act=range-next]');
                    if (next.disabled) break;
                    next.click();
                }
                return {names, sizes};
            } finally { Array.prototype.sort = originalSort; Map.prototype.set = originalSet; }
        }''')
        expected = sorted(range(10003), key=lambda index: (-(1 + index % 999), index))
        self.assertEqual(result['names'], [f'function_{index:06d}' for index in expected])
        self.assertEqual(result['sizes'], [100] * 100 + [3])
        self.assertIn('10001–10003 of 10003', self.status())

    def test_page_entry_validates_without_losing_focus_or_changing_csv(self):
        self.open_report()
        exported = self.export()
        page = self.page.get_by_role('textbox', name='Page', exact=True)
        self.assertEqual(page.input_value(), '1')
        self.assertEqual(self.page.locator('#tl-range-page-total').inner_text(), 'of 7')
        for invalid in ('0', '8', '1.5', ''):
            with self.subTest(page=invalid):
                page.fill(invalid)
                page.press('Enter')
                self.assertFalse(page.evaluate('input => input.validity.valid'))
                self.assertEqual(self.status(), 'Showing 1–100 of 650 functions')
        page.fill('7')
        page.press('Enter')
        self.assertTrue(page.evaluate('input => input === document.activeElement && input.validity.valid'))
        self.assertEqual(self.status(), 'Showing 601–650 of 650 functions')
        self.assertTrue(self.page.locator('[data-act=range-next]').is_disabled())
        self.assertEqual(self.export(), exported)
        page.fill('1')
        self.page.get_by_role('button', name='Go to page of functions').click()
        self.assertEqual(self.status(), 'Showing 1–100 of 650 functions')

    def test_csv_stays_complete_and_byte_identical_on_every_page_with_safe_names(self):
        names = ['=SUM(1,2)', '+cmd', '-cmd', '@cmd', '\tcmd', ' quoted,"\n函数']
        def update(data):
            data['strings'][:len(names)] = names
        self.open_report(update=update)
        self.page.locator('th[data-sort=name] button').click()
        before = self.export()
        expected = sorted(names + [f'function_{index:06d}' for index in range(6, 650)])
        self.assertEqual([row['function'] for row in self.rows()], ["'" + name if name in names[:5] else name for name in expected])
        self.next()
        self.assertEqual(self.export(), before)
        page = self.page.locator('[data-act=range-page]')
        page.fill('7')
        page.press('Enter')
        self.assertEqual(self.export(), before)
        self.assertEqual(len(self.names()), 50)
        self.page.locator('[data-act=range-prev]').click()
        self.assertEqual(self.export(), before)

    def test_reopening_refresh_and_text_roundtrip_preserve_page_but_sort_resets(self):
        self.open_report()
        self.next()
        original = self.names(), self.status(), self.export()
        self.page.evaluate("fgView('timeline').probe().range(0, 650000)")
        self.assertEqual((self.names(), self.status(), self.export()), original)
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.assertEqual(self.page.evaluate('document.body.dataset.view'), 'text')
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.assertEqual((self.names(), self.status(), self.export()), original)
        self.page.locator('.tl-close').click()
        self.page.evaluate("fgView('timeline').probe().range(0, 650000)")
        self.assertEqual((self.names(), self.status(), self.export()), original)
        self.page.locator('th[data-sort=name] button').press('Enter')
        self.assertEqual(self.names()[0], 'function_000000')
        self.assertIn('1–100 of 650', self.status())
        self.assertTrue(self.page.locator('th[data-sort=name] button').evaluate('element => element === document.activeElement'))

    def test_changed_range_track_filter_and_overhead_reset_pages_not_export_scope(self):
        def update(data):
            data['cols']['adj'] = [duration / 2 for duration in data['cols']['dur']]
        self.open_report(update=update)
        self.next()
        self.page.evaluate("fgView('timeline').probe().range(0, 649000)")
        self.assertIn('1–100 of 649', self.status())
        self.next()
        self.page.evaluate("fgView('timeline').probe().range(0, 649000, 0)")
        self.assertIn('1–100 of 649', self.status())
        self.next()
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.assertIn('1–100 of 649', self.status())
        self.assertEqual({row['duration_basis'] for row in self.rows()}, {'overhead_subtracted_estimate'})
        self.next()
        self.page.evaluate("fgNotifyFilter('first 101', new Set(Array.from({length: 101}, (_, index) => index + 1)))")
        self.assertIn('1–100 of 101', self.status())
        self.next()
        self.assertEqual(len(self.names()), 1)
        self.assertEqual(len(self.rows()), 101)
        self.page.evaluate("fgNotifyFilter('empty', new Set())")
        self.assertEqual(self.names(), [])
        self.assertEqual(self.status(), 'Showing 0 of 0 functions')
        self.assertTrue(self.page.locator('[data-act=range-csv]').is_disabled())
        self.assertEqual(self.page.locator('[data-range-page]').count(), 0)
        self.page.evaluate("fgNotifyFilter('', null)")
        self.assertIn('1–100 of 649', self.status())

    def test_all_call_filter_and_clear_keep_range_table_page_and_focus(self):
        self.open_report()
        self.next()
        self.page.locator('[data-act=range-next]').focus()
        expected = self.names(), self.status()
        before = self.rows()
        facts = self.page.evaluate('''async () => {
            const table = document.querySelector('.tl-range-table'), row = table.tBodies[0].rows[0];
            const button = document.activeElement, head = document.querySelector('.tl-range-head');
            const lines = fgData.read('tlData').cols.line;
            fgNotifyFilter('all recorded calls', new Set(lines));
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            return {retained: row.isConnected && button === document.activeElement,
                filtered: !head.querySelector('.tl-range-filtered').hidden,
                aligned: parseFloat(getComputedStyle(table.tHead.rows[0].cells[0]).top) === head.offsetHeight};
        }''')
        self.assertEqual(facts, dict(retained=True, filtered=True, aligned=True))
        self.assertEqual((self.names(), self.status()), expected)
        self.assertEqual(self.rows(), [dict(row, filter='all recorded calls') for row in before])

        self.page.evaluate('''() => {
            window.retainedRangeRow = document.querySelector('.tl-range-table tbody tr');
            fgNotifyFilter('', null);
        }''')
        self.assertTrue(self.page.evaluate('retainedRangeRow.isConnected'))
        self.assertTrue(self.page.locator('.tl-range-filtered').is_hidden())
        self.assertEqual((self.names(), self.status()), expected)
        self.assertEqual(self.rows(), before)

    def test_empty_range_keeps_its_explanation_after_sort_filter_and_clear(self):
        self.open_report(select_range=False)
        self.page.evaluate("fgView('timeline').probe().range(651000, 652000)")
        self.page.locator('th[data-sort=name] button').click()
        row = self.page.locator('.tl-range-table tbody tr')
        self.assertEqual(row.inner_text(), 'No calls overlap this range.')
        self.page.evaluate("fgNotifyFilter('all recorded calls', new Set(fgData.read('tlData').cols.line))")
        self.assertEqual(row.inner_text(), 'No calls overlap this range with the current filter.')
        self.page.evaluate("fgNotifyFilter('', null)")
        self.assertEqual(row.inner_text(), 'No calls overlap this range.')
        self.assertTrue(self.page.locator('[data-act=range-csv]').is_disabled())

    def test_changed_filter_reuses_range_controls_and_recovers_removed_focus(self):
        self.open_report()
        self.next()
        self.page.locator('[data-act=range-next]').focus()
        facts = self.page.evaluate('''async () => {
            const pane = document.querySelector('.tl-detail');
            const table = pane.querySelector('.tl-range-table'), head = pane.querySelector('.tl-range-head');
            const exportButton = head.querySelector('[data-act=range-csv]');
            const header = table.querySelector('th[aria-sort=descending] button');
            fgNotifyFilter('first five', new Set([1, 2, 3, 4, 5]));
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const retained = table.isConnected && head.isConnected && exportButton.isConnected;
            const focused = document.activeElement === header;
            const inset = parseFloat(getComputedStyle(header.parentElement).top);
            window.reusedRangeTable = table;
            return {retained, focused, inset, height: head.offsetHeight};
        }''')
        self.assertTrue(facts['retained'])
        self.assertTrue(facts['focused'])
        self.assertEqual(facts['inset'], facts['height'])
        self.assertEqual(self.status(), 'Showing 1–5 of 5 functions')
        self.assertEqual(self.page.locator('[data-range-page]').count(), 0)
        self.assertEqual({row['filter'] for row in self.rows()}, {'first five'})

        self.page.locator('[data-act=range-csv]').focus()
        self.page.evaluate("fgNotifyFilter('empty result', new Set())")
        self.assertTrue(self.page.locator('[data-act=range-csv]').is_disabled())
        self.assertTrue(self.page.locator('th[aria-sort=descending] button').evaluate('button => button === document.activeElement'))
        self.page.evaluate("fgNotifyFilter('', null)")
        self.assertTrue(self.page.evaluate('reusedRangeTable.isConnected'))
        self.assertIn('1–100 of 650', self.status())
        self.next()
        self.assertIn('101–200 of 650', self.status())

    def test_short_range_across_call_blocks_matches_full_scan(self):
        per_track = 650
        starts = [position * 1000 + offset for offset in (0, 500) for position in range(per_track)]
        misplaced, actual = per_track + 512, per_track + 640
        starts[misplaced], starts[actual] = starts[actual], starts[misplaced]
        durations = [1 + index % per_track for index in range(2 * per_track)]
        durations[0], durations[511], durations[512], durations[514] = 640000, 1000, 0, 0
        names = list(range(2 * per_track))
        names[actual] = 513
        off = [[track, 509000 + ordinal * 30, 509020 + ordinal * 30, 0, 'sleep', 'S', 0, 0, 0, -1]
               for track in (0, 1) for ordinal in range(160)]
        off[6], off[120] = off[120], off[6]
        off.extend([[0, 500000, 513700, 0, 'sleep', 'S', 0, 0, 0, -1],
                    [1, 513900, None, 0, 'sleep', 'S', 0, 0, 0, -1]])
        origins = {0, 511, 512, 514}

        def update(data):
            data['tasks'].append(dict(data['tasks'][0], pid=2, comm='second-track'))
            deltas = [begin if index % per_track == 0 else begin - starts[index - 1]
                      for index, begin in enumerate(starts)]
            data['cols'].update(task=[0] * per_track + [1] * per_track,
                                start=deltas,
                                dur=durations, name=names, depth=[0] + [1] * 639 + [0] * 660,
                                parent=[-1] + [0] * 639 + [-1] * 660)
            data['errs'] = [[index, -1, 0, 0, 0, chain] for chain, index in enumerate(sorted(origins))]
            data['chains'] = [[index, index, 1, 0, 1, 0, 0] for index in sorted(origins)]
            data['offcpu'] = off

        self.open_report(count=len(starts), update=update)
        trace_end = max(begin + duration for begin, duration in zip(starts, durations))
        self_time = [durations[0] - sum(durations[1:640]), *durations[1:]]
        left, right = 512000, 514000
        for selected_track, lines in ((None, None), (0, None), (1, None), (None, [1, 513, 515])):
            with self.subTest(track=selected_track, lines=lines):
                self.page.evaluate('''([left, right, track, lines]) => {
                    if (lines) fgNotifyFilter('selected calls', new Set(lines));
                    fgView('timeline').probe().range(left, right, track === null ? undefined : track);
                }''', [left, right, selected_track, lines])
                expected = {}
                for index, (begin, duration) in enumerate(zip(starts, durations)):
                    end = begin + duration
                    if selected_track is not None and index // per_track != selected_track:
                        continue
                    if lines is not None and index + 1 not in lines:
                        continue
                    if begin >= right or (end <= left if duration else begin < left):
                        continue
                    fraction = (min(end, right) - max(begin, left)) / duration if duration else 0
                    stats = expected.setdefault(f'function_{names[index]:06d}', [0, 0, 0, 0, 0])
                    stats[0] += begin >= left
                    stats[1] += duration * fraction
                    stats[2] += self_time[index] * fraction
                    stats[3] = max(stats[3], duration)
                    stats[4] += index in origins
                off_time = sum(max(0, min(end if end is not None else trace_end, right) - max(begin, left))
                               for track, begin, end, *_ in off if selected_track is None or track == selected_track)
                actual = {row['function']: row for row in self.rows()}
                self.assertEqual(set(actual), set(expected))
                for name, stats in expected.items():
                    row = actual[name]
                    for column, value in zip(('calls', 'inclusive_estimate_ns', 'self_estimate_ns',
                                              'max_call_ns', 'error_origins_overlapping'), stats):
                        self.assertAlmostEqual(float(row[column]), value)
                    self.assertEqual(int(row['scope_off_cpu_ns']), off_time)

    def test_all_four_time_modes_keep_complete_csv_bytes_during_paging(self):
        for mode in ('abs', 'rel', 'compressed', 'count'):
            with self.subTest(mode=mode):
                self.page.close()
                self.page = self.context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.open_report(update=lambda data: data.update(mode=mode))
                before = self.export()
                self.next()
                self.assertEqual(self.export(), before)
                rows = self.rows()
                self.assertEqual(len(rows), 650)
                self.assertEqual({row['time_mode'] for row in rows}, {mode})
                unit = 'units' if mode == 'count' else 'ns'
                self.assertEqual(rows[0]['range_end_' + unit], '650' if mode == 'count' else '650000')

    def test_paging_aligns_first_row_above_the_pinned_footer(self):
        self.open_report()
        self.next()
        geometry = self.page.evaluate('''() => {
            const panel = document.querySelector('.tl-detail').getBoundingClientRect();
            const head = document.querySelector('.tl-range-head').getBoundingClientRect();
            const footer = document.querySelector('.tl-range-footer').getBoundingClientRect();
            const row = document.querySelector('.tl-range-table tbody tr').getBoundingClientRect();
            const next = document.querySelector('[data-act=range-next]'), button = next.getBoundingClientRect();
            return {panel: panel.toJSON(), head: head.toJSON(), footer: footer.toJSON(), row: row.toJSON(),
                position: getComputedStyle(document.querySelector('.tl-range-footer')).position,
                hit: next.contains(document.elementFromPoint(button.x + button.width / 2, button.y + button.height / 2))};
        }''')
        self.assertEqual(geometry['position'], 'sticky')
        self.assertTrue(geometry['hit'])
        self.assertGreaterEqual(geometry['row']['top'], geometry['head']['bottom'])
        self.assertLessEqual(geometry['row']['bottom'], geometry['footer']['top'])
        self.assertLessEqual(geometry['footer']['bottom'], geometry['panel']['bottom'])

    def test_row_search_keeps_page_and_range_without_selecting_a_call(self):
        self.open_report()
        self.next()
        before = self.names(), self.status(), self.export()
        row = self.page.locator('.tl-range-table tbody tr[data-name]').first
        name = row.get_attribute('data-name')
        row.click()
        self.assertEqual(self.page.locator('.tl-search').input_value(), '^' + name + '$')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.assertEqual((self.names(), self.status(), self.export()), before)
        row = self.page.locator('.tl-range-table tbody tr[data-name]').nth(1)
        name = row.get_attribute('data-name')
        action = row.get_by_role('button', name=name, exact=True)
        action.press('Enter', timeout=1000)
        self.assertEqual(self.page.locator('.tl-search').input_value(), '^' + name + '$')
        self.assertTrue(action.evaluate('element => element === document.activeElement'))
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.assertEqual((self.names(), self.status(), self.export()), before)

    def test_same_name_user_and_kernel_functions_stay_separate_in_real_sidecar(self):
        self.open_report(301, sidecar=True)
        before = self.export()
        rows = self.rows()
        self.assertEqual(len(rows), 602)
        self.assertEqual({(row['function'], row['space']) for row in rows},
                         {(f'function_{index:06d}', space) for index in range(301) for space in ('user', 'kernel')})
        self.next()
        self.assertEqual(self.export(), before)
        page = self.page.locator('[data-act=range-page]')
        page.fill('7')
        page.press('Enter')
        self.assertEqual(len(self.names()), 2)
        self.assertEqual(self.export(), before)
        self.assertEqual(self.page.locator('.tl-stack-space').all_text_contents(), ['K', 'K'])

    def test_touch_themes_orientations_sticky_fallback_and_reachable_controls(self):
        self.context.close()
        for theme in ('light', 'dark'):
            context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
            self.addCleanup(context.close)
            self.page = context.new_page()
            self.page.on('pageerror', lambda error: self.errors.append(str(error)))
            self.page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
            self.open_report(301, sidecar=True)
            for width, height in ((390, 844), (320, 700), (844, 390)):
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                    self.assertEqual(self.page.locator('.tl-range-head').evaluate('element => getComputedStyle(element).top'), 'auto')
                    self.page.evaluate("document.querySelector('.tl-detail').scrollLeft = 1e6")
                    for selector in ('[data-act=range-csv]', '.tl-close'):
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.page.locator('[data-act=range-next]').tap()
                    for selector in ('[data-act=range-prev]', '[data-act=range-next]', '[data-act=range-page]', '.tl-range-jump button'):
                        control = self.page.locator(selector)
                        control.scroll_into_view_if_needed()
                        bounds = control.bounding_box()
                        self.assertGreaterEqual(bounds['height'], 44)
                        self.assertGreaterEqual(bounds['width'], 44)
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.page.locator('[data-act=range-prev]').tap()
                    self.assertIn('1–100 of 602', self.status())
                    self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                        self.assertEqual(self.page.locator('.tl-range-footer').evaluate('element => getComputedStyle(element).bottom'), 'auto')


if __name__ == '__main__':
    unittest.main()
