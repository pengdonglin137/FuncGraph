"""Duration-call pages stay bounded without changing statistical/export scope."""
import csv
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import helpers
from benchmark_distcalls import fixture
from fglib.viewer import create_server
from test_uftrace_import import call, stream


class DistributionPagingTest(unittest.TestCase):
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
        cls.kernel.run(str(trace), '--no-config', '--timeline', '--no-text-view')
        cls.template = Path(cls.kernel.dir) / 'out.html'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-dist-page-')
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

    def open_report(self, count=205, update=None, sidecar=False, open_dist=True, site_count=0):
        self.serial += 1
        report = Path(self.directory.name) / f'calls-{self.serial}.html'
        if sidecar:
            records = []
            space = 'kernel' if site_count else 'user'
            for index in range(count):
                fields = {'retaddr': hex(0x1000 + index % site_count)} if site_count else {}
                records.extend([call('entry', space, index * 20000, 0, 'work', 1, **fields),
                                call('exit', space, index * 20000 + 1 + index, 0, 'work', 1)])
            trace = Path(self.kernel.dir) / f'actual-{self.serial}.jsonl'
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
            fixture(self.template, report, count)
            if update:
                html = report.read_text(encoding='utf-8')
                match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
                data = json.loads(match[2])
                update(data)
                encoded = json.dumps(data).replace('<', r'\u003c')
                report.write_text(html[:match.start(2)] + encoded + html[match.end(2):], encoding='utf-8')
            url = report.as_uri()
        self.page.goto(url)
        self.page.evaluate('''async openDist => {
            await fgSwitchView('timeline');
            if (openDist) { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }
        }''', open_dist)
        if open_dist:
            self.page.locator('[data-act=dist-bucket]').wait_for()

    def ids(self):
        return self.page.locator('.an-list [data-slice]').evaluate_all('(rows) => rows.map(row => +row.dataset.slice)')

    def status(self):
        return self.page.locator('.an-dist-count').inner_text()

    def next(self):
        self.page.locator('[data-act=dist-next]').click()

    def export(self):
        with self.page.expect_download() as download:
            self.page.locator('[data-act=dist-csv]').click()
        return Path(download.value.path()).read_bytes()

    def rows(self):
        return list(csv.DictReader(io.StringIO(self.export().decode('utf-8-sig'))))

    def statistics(self):
        return self.page.locator('.an-dist .tl-kv').evaluate('''element =>
            Object.fromEntries([...element.querySelectorAll('.k')].map(label =>
                [label.textContent, label.nextElementSibling.textContent]))''')

    def visible_calls(self):
        return self.page.evaluate('''() => {
            const panel = document.querySelector('.tl-detail').getBoundingClientRect();
            const pager = document.querySelector('.an-dist-pager').getBoundingClientRect();
            const header = document.querySelector('.an-dist-head').getBoundingClientRect();
            return [...document.querySelectorAll('.an-list tr')].filter(row => {
                const bounds = row.getBoundingClientRect();
                return bounds.top >= Math.max(panel.top, header.bottom) && bounds.bottom <= Math.min(panel.bottom, pager.top);
            }).length;
        }''')

    def test_large_distribution_pauses_and_restores_the_complete_population(self):
        count = 11000
        self.page.set_viewport_size({'width': 320, 'height': 700})
        self.open_report(count, open_dist=False)
        facts = self.page.evaluate('''() => {
            const clock = performance.now.bind(performance), trace = fgView('timeline').probe();
            let elapsed = 0;
            performance.now = () => clock() + (elapsed += 4);
            try { trace.dist(0); } finally { delete performance.now; }
            const pending = document.querySelector('.an-dist-pending');
            const close = document.querySelector('.an-dist-head .tl-close');
            const bounds = close.getBoundingClientRect();
            const result = [pending?.textContent, document.querySelectorAll('.an-list tr').length,
                close.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2))];
            fgSwitchView('text');
            return result;
        }''')
        self.assertEqual(facts, ['Preparing duration distribution…', 0, True])
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
        self.assertEqual(self.page.locator('.an-dist-pending').count(), 1)
        self.page.evaluate("fgSwitchView('timeline')")
        self.page.locator('[data-act=dist-bucket]').wait_for()
        ordered = sorted(range(count), key=lambda index: (-(index % 999), index))
        self.assertEqual(self.ids(), ordered[:50])
        self.assertEqual([int(row['trace_line']) - 1 for row in self.rows()], ordered)

    def test_pending_distribution_discarded_on_scope_change_and_close(self):
        def update(data):
            data['strings'].append('other_function')
            data['cols']['name'][-1] = 2

        self.open_report(9000, update=update, open_dist=False)
        self.assertEqual(self.page.evaluate('''() => {
            const trace = fgView('timeline').probe(), clock = performance.now.bind(performance);
            let elapsed = 0;
            performance.now = () => clock() + (elapsed += 4);
            try {
                trace.dist(0);
                fgNotifyFilter('one call', new Set([9]));
            } finally { delete performance.now; }
            return !!document.querySelector('.an-dist-pending');
        }'''), True)
        self.page.locator('[data-act=dist-bucket]').wait_for()
        self.assertEqual(self.ids(), [8])
        self.assertEqual(self.status(), 'Calls 1–1 of 1 (slowest first)')
        self.assertEqual(self.page.evaluate('''() => {
            const trace = fgView('timeline').probe(), clock = performance.now.bind(performance);
            let elapsed = 0;
            performance.now = () => clock() + (elapsed += 4);
            try { fgNotifyFilter('', null); } finally { delete performance.now; }
            const close = document.querySelector('.an-dist-pending') && document.querySelector('.an-dist-head .tl-close');
            if (!close) return false;
            close.click();
            trace.dist(2);
            return true;
        }'''), True)
        self.page.locator('[data-act=dist-bucket]').wait_for()
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
        self.assertEqual(self.ids(), [8999])
        self.assertEqual(self.page.locator('.an-dist-head .tl-detail-title').inner_text(), 'other_function')

    def test_large_call_csv_keeps_scope_during_paging_and_pauses_after_progress(self):
        count = 6000
        self.page.set_viewport_size({'width': 320, 'height': 700})
        self.open_report(count)
        self.page.locator('[data-act=dist-histogram]').click()
        button = self.page.locator('[data-act=dist-csv]')
        status = self.page.locator('.an-dist-csv-status')
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        self.assertEqual(button.evaluate('''button => new Promise((resolve, reject) => {
            const status = document.querySelector('.an-dist-csv-status');
            const clock = performance.now.bind(performance);
            let elapsed = 0;
            performance.now = () => clock() + (elapsed += 7);
            button.focus();
            button.click();
            const elements = [button, status].map(node => node.getBoundingClientRect());
            const accessible = elements.every(rect => rect.left >= 0 && rect.right <= innerWidth && rect.width > 0);
            const focused = document.activeElement === button;
            document.querySelector('[data-act=dist-next]').click();
            const observer = new MutationObserver(() => {
                if (!status.textContent.includes('%')) return;
                observer.disconnect(); clearTimeout(timeout); delete performance.now;
                fgSwitchView('text');
                resolve([accessible, focused, status.textContent]);
            });
            const timeout = setTimeout(() => {
                observer.disconnect(); delete performance.now; reject(Error('No CSV progress'));
            }, 5000);
            observer.observe(status, {childList: true});
        })'''), [True, True, 'Preparing CSV… 25%'])
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
        self.assertEqual(status.text_content(), 'Preparing CSV… 25%')
        self.assertEqual(downloads, [])
        with self.page.expect_download() as download:
            self.page.evaluate("fgSwitchView('timeline')")
        self.assertEqual(download.value.suggested_filename, 'funcgraph-calls-raw.csv')
        raw = Path(download.value.path()).read_bytes()
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
        ordered = sorted(range(count), key=lambda index: (-(index % 999), index))
        self.assertEqual([int(row['trace_line']) - 1 for row in rows], ordered)
        self.assertEqual(self.ids(), ordered[50:100])
        self.assertTrue(status.is_hidden())
        self.assertEqual(button.inner_text(), 'Export calls CSV')
        self.assertEqual(self.export(), raw)

    def test_large_call_csv_cancels_when_cancelled_or_scope_changes(self):
        def update(data):
            data['strings'].append('other_site')
            data['cols']['caller'][-2:] = [2, 2]
            data['cols']['adj'] = [5] * data['n']
            data['overhead'] = {'entry': 3, 'exit': 2}

        self.open_report(4110, update=update)
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        button = self.page.locator('[data-act=dist-csv]')
        for action in (
            'button.click()',
            "const bucket = document.querySelector('[data-act=dist-bucket]'); bucket.selectedIndex = 1; "
            "bucket.dispatchEvent(new Event('change', {bubbles:true}));",
            "document.querySelector('.an-sites tbody button').click()",
            "document.querySelector('[data-act=adj]').click()",
            "fgNotifyFilter('one line', new Set([1]))",
            "document.querySelector('.an-dist-head .tl-close').click()",
        ):
            with self.subTest(action=action):
                self.assertEqual(button.evaluate(f'''button => {{
                    button.click();
                    const before = button.textContent;
                    {action};
                    return before;
                }}'''), 'Cancel CSV')
                self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
                self.assertEqual(downloads, [])
                self.assertTrue(self.page.locator('.an-dist-csv-status').is_hidden())
                self.page.evaluate('''() => {
                    fgNotifyFilter('', null);
                    const reset = document.querySelector('[data-act=dist-reset-site]');
                    if (reset) reset.click();
                    else fgView('timeline').probe().dist(0);
                    document.querySelector('[data-act=dist-reset-bucket]')?.click();
                }''')
                button.wait_for()

    def test_histogram_disclosure_keeps_page_rows_statistics_csv_and_keyboard_focus(self):
        self.open_report()
        self.next()
        before = self.ids(), self.statistics(), self.export()
        navigation = self.page.evaluate("() => { const model = fgView('timeline').probe(); return [model.sel(), model.view()]; }")
        split = self.page.evaluate("localStorage.getItem('fgTl.detailFrac')")
        first = self.page.locator('.an-list [data-slice]').first.element_handle()
        button = self.page.get_by_role('button', name='Histogram', exact=True)
        self.assertEqual(button.get_attribute('aria-controls'), 'tl-dist-histogram')
        self.assertEqual(button.get_attribute('aria-expanded'), 'true')
        button.press('Enter')
        self.assertEqual(button.get_attribute('aria-expanded'), 'false')
        self.assertFalse(self.page.locator('.an-hist').is_visible())
        self.assertTrue(first.evaluate('element => element.isConnected'))
        self.assertEqual((self.ids(), self.statistics(), self.export()), before)
        self.assertEqual(self.page.evaluate("() => { const model = fgView('timeline').probe(); return [model.sel(), model.view()]; }"), navigation)
        self.assertEqual(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"), split)
        button.press('Space')
        self.assertTrue(button.evaluate('element => element === document.activeElement'))
        self.assertTrue(self.page.locator('.an-hist').is_visible())
        self.assertEqual((self.ids(), self.statistics(), self.export()), before)
        self.assertEqual(self.page.evaluate("() => { const model = fgView('timeline').probe(); return [model.sel(), model.view()]; }"), navigation)

    def test_histogram_choice_survives_population_changes_reopening_but_not_report_reload(self):
        def update(data):
            data['strings'] += ['second_function', 'second_caller']
            data['cols']['name'][-1] = 2
            data['cols']['caller'] = [1] * 120 + [3] * 85
            data['cols']['adj'] = [duration / 2 for duration in data['cols']['dur']]
        self.open_report(update=update)
        self.page.locator('[data-act=dist-histogram]').click()
        self.page.locator('tr[data-site] button').first.click()
        self.page.locator('[data-act=dist-next]').click()
        self.page.evaluate("fgNotifyFilter('subset', new Set(Array.from({length: 100}, (_, index) => index + 1)))")
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.assertEqual(self.page.locator('[data-act=dist-histogram]').get_attribute('aria-expanded'), 'false')
        self.page.locator('.an-dist-head .tl-close').click()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")
        self.assertFalse(self.page.locator('.an-hist').is_visible())
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.S.indexOf('second_function')); }")
        self.assertFalse(self.page.locator('.an-hist').is_visible())
        self.assertEqual(self.status(), 'No matching calls')
        self.open_report()
        self.assertEqual(self.page.locator('[data-act=dist-histogram]').get_attribute('aria-expanded'), 'true')

    def test_closed_histogram_skips_canvas_work_and_redraws_at_current_size_when_reopened(self):
        self.open_report()
        self.page.locator('[data-act=dist-histogram]').click()
        self.page.evaluate('''async () => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const original = HTMLCanvasElement.prototype.getContext;
            window.distCanvasCalls = 0;
            HTMLCanvasElement.prototype.getContext = function(...parameters) {
                if (this.classList.contains('an-hist')) window.distCanvasCalls++;
                return original.apply(this, parameters);
            };
        }''')
        self.page.set_viewport_size({'width': 1260, 'height': 800})
        self.page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
        self.page.locator('[data-act=dist-bucket]').select_option(self.page.locator('[data-act=dist-bucket] option').last.get_attribute('value'))
        self.page.evaluate('async () => { await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); }')
        self.assertEqual(self.page.evaluate('window.distCanvasCalls'), 0)
        bucket = self.page.locator('[data-act=dist-bucket]').input_value()
        self.page.locator('[data-act=dist-histogram]').press('Enter')
        self.page.wait_for_function('''() => {
            const canvas = document.querySelector('.an-hist');
            return window.distCanvasCalls > 0 && canvas.width === Math.round(canvas.parentElement.clientWidth * devicePixelRatio);
        }''')
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), bucket)

    def test_compact_histogram_keeps_real_call_rows_visible_in_touch_reports(self):
        self.context.close()
        for theme in ('light', 'dark'):
            for sidecar in (False, True):
                context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
                self.addCleanup(context.close)
                self.page = context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
                self.open_report(sidecar=sidecar)
                expected = self.statistics(), self.export()
                self.page.locator('[data-act=dist-histogram]').tap()
                for width, height in ((390, 844), (844, 390)):
                    with self.subTest(theme=theme, sidecar=sidecar, width=width):
                        self.page.set_viewport_size({'width': width, 'height': height})
                        if height < 480:
                            self.page.locator('[data-detail-expand]').tap()
                        self.page.evaluate("document.querySelector('.tl-detail').scrollTop = 0")
                        self.assertGreaterEqual(self.visible_calls(), 1)
                        self.assertEqual((self.statistics(), self.export()), expected)
                        self.assertEqual(self.page.get_by_label('Duration bucket', exact=True).count(), 1)
                        self.assertGreaterEqual(self.page.locator('[data-act=dist-histogram]').bounding_box()['height'], 44)
                        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
                        if height < 480:
                            self.page.locator('[data-act=dist-histogram]').tap()
                            self.assertGreaterEqual(self.visible_calls(), 1)
                            self.assertEqual((self.statistics(), self.export()), expected)
                            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
                context.close()

    def test_expanded_landscape_histogram_resizes_and_selects_its_visible_bucket(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 844, 'height': 390}, has_touch=True, is_mobile=True,
                                                device_scale_factor=2)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

        def update(data):
            data['cols']['dur'] = [100] * len(data['cols']['dur'])

        self.open_report(update=update)
        original_rows, statistics, exported = self.ids(), self.statistics(), self.export()
        self.page.locator('[data-detail-expand]').tap()
        self.page.wait_for_function('''() => {
            const canvas = document.querySelector('.an-hist');
            return canvas.clientHeight === 76 && canvas.height === canvas.clientHeight * devicePixelRatio &&
                canvas.width === Math.round(canvas.clientWidth * devicePixelRatio);
        }''')
        canvas = self.page.locator('.an-hist')
        bounds = canvas.bounding_box()
        table = self.page.get_by_role('table', name='Duration calls', exact=True).bounding_box()
        self.assertLessEqual(bounds['x'] + bounds['width'], table['x'])
        self.assertGreaterEqual(self.visible_calls(), 1)
        self.page.touchscreen.tap(bounds['x'] + 40 + (bounds['width'] - 48) / 16, bounds['y'] + 20)
        bucket = self.page.get_by_label('Duration bucket', exact=True)
        self.assertEqual(bucket.input_value(), '0')
        self.assertEqual(self.statistics(), statistics)
        self.assertEqual(self.ids(), original_rows)

        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.wait_for_function('''() => {
            const canvas = document.querySelector('.an-hist');
            return canvas.clientHeight === 120 && canvas.height === canvas.clientHeight * devicePixelRatio &&
                canvas.width === Math.round(canvas.clientWidth * devicePixelRatio);
        }''')
        self.assertEqual(bucket.input_value(), '0')
        bucket.select_option('-1')
        self.assertEqual(self.export(), exported)

    def test_empty_compact_distribution_never_invents_statistics_or_enables_export(self):
        self.open_report()
        self.page.locator('[data-act=dist-histogram]').click()
        self.page.evaluate("fgNotifyFilter('empty', new Set())")
        self.assertEqual(set(self.statistics().values()), {'—'})
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.status(), 'No matching calls')
        self.assertTrue(self.page.locator('[data-act=dist-csv]').is_disabled())
        self.page.locator('[data-act=dist-histogram]').press('Space')
        self.assertTrue(self.page.locator('.an-hist').is_visible())
        self.assertTrue(self.page.locator('[data-act=dist-bucket]').is_disabled())

    def test_narrow_compact_statistics_and_long_names_do_not_overflow(self):
        def update(data):
            data['strings'][0] = 'very_long_function_' * 12
            data['cols']['dur'] = [10 ** 12 + index for index in range(205)]
        self.page.set_viewport_size({'width': 320, 'height': 844})
        self.open_report(update=update)
        expected = self.statistics()
        self.page.locator('[data-act=dist-histogram]').click()
        self.assertEqual(self.statistics(), expected)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 320)
        button = self.page.locator('[data-act=dist-histogram]').bounding_box()
        self.assertGreaterEqual(button['x'], 0)
        self.assertLessEqual(button['x'] + button['width'], 320)

    def test_page_boundaries_and_native_keyboard_endpoint_focus(self):
        for count in (1, 50, 51, 103):
            with self.subTest(count=count):
                self.open_report(count)
                self.assertEqual(self.ids(), list(range(count - 1, max(-1, count - 51), -1)))
                self.assertTrue(self.page.locator('[data-act=dist-prev]').is_disabled())
                self.assertEqual(self.page.locator('[data-act=dist-next]').is_disabled(), count <= 50)
                if count > 50:
                    self.page.locator('[data-act=dist-next]').press('Enter')
                    self.assertEqual(self.ids(), list(range(count - 51, max(-1, count - 101), -1)))
                    if count <= 100:
                        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'dist-prev')
                    self.page.locator('[data-act=dist-prev]').press('Space')
                    self.assertEqual(self.status(), f'Calls 1–50 of {count} (slowest first)')
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'dist-next')

    def test_all_thousands_of_calls_are_reachable_without_resorting_or_growing_rows(self):
        self.open_report(5207)
        self.page.evaluate("() => { Array.prototype.sort = () => { throw new Error('Paging must reuse prepared ordering'); }; }")
        result = self.page.evaluate("""() => {
            const ids = [], sizes = [];
            while (true) {
                const rows = Array.from(document.querySelectorAll('.an-list [data-slice]'));
                sizes.push(rows.length);
                ids.push(...rows.map(row => +row.dataset.slice));
                const next = document.querySelector('[data-act=dist-next]');
                if (next.disabled) break;
                next.click();
            }
            return {ids, sizes};
        }""")
        self.assertEqual(result['ids'], sorted(range(5207), key=lambda index: (-(1 + index % 999), index)))
        self.assertEqual(result['sizes'], [50] * 104 + [7])
        self.assertIn('5201–5207 of 5207', self.status())

    def test_complete_csv_is_independent_of_pages_and_preserves_formula_escaping(self):
        def update(data):
            data['strings'] = ['=SUM(1,2)', '\tcaller,"\n函数']
        self.open_report(320, update=update)
        before = self.export()
        self.next()
        rows = self.rows()
        self.assertEqual(len(rows), 320)
        self.assertEqual({row['function'] for row in rows}, {"'=SUM(1,2)"})
        self.assertEqual({row['caller'] for row in rows}, {"'\tcaller,\"\n函数"})
        self.assertEqual([int(row['trace_line']) for row in rows], list(range(320, 0, -1)))
        self.assertEqual(self.export(), before)
        self.assertIn('51–100 of 320', self.status())
        self.assertEqual(len(self.ids()), 50)

    def test_opening_distribution_reuses_prepared_search_postings(self):
        targets = [41, 542, 21000, 28670, 30241]

        def update(data):
            data['strings'] = ['other', 'target.constprop.1', 'target.constprop.2', 'caller']
            data['cols']['name'] = [0] * data['n']
            data['cols']['caller'] = [3] * data['n']
            for index, duration in zip(targets, [500, 500, 400, 600, 300]):
                data['cols']['name'][index] = 1
                data['cols']['dur'][index] = duration
                data['cols']['name'][index + 1] = 2

        for prepared in (False, True):
            with self.subTest(prepared=prepared):
                self.open_report(32768, update=update, open_dist=False)
                if prepared:
                    search = self.page.locator('.tl-search')
                    search.fill(r'^target\.constprop\.1$')
                    search.press('Enter')
                    self.page.wait_for_function("document.querySelector('.tl-count').textContent === '1 / 5 calls'")
                lookups = self.page.evaluate('''() => {
                    const original = Int32Array.prototype.indexOf, sizes = [];
                    Int32Array.prototype.indexOf = function (name, ...args) {
                        if (name === 1) sizes.push(this.length);
                        return original.call(this, name, ...args);
                    };
                    try { fgView('timeline').probe().dist(1); }
                    finally { delete Int32Array.prototype.indexOf; }
                    return sizes;
                }''')
                self.assertEqual(lookups, [3] if prepared else [])
                self.assertEqual(self.ids(), [28670, 41, 542, 21000, 30241])
                if prepared:
                    self.page.locator('.tl-search').press('Enter')
                    self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 542)
                    self.page.evaluate('''ids => {
                        fgView('timeline').probe().dist(1);
                        fgNotifyFilter('some calls', new Set(ids.map(index => index + 1)));
                    }''', [41, 28670, 28671])
                    self.assertEqual(self.ids(), [28670, 41])

    def test_bucket_pages_do_not_change_population_statistics_or_export_scope(self):
        def update(data):
            data['cols']['dur'] = [10 ** (index // 60 + 1) for index in range(240)]
        self.open_report(240, update=update)
        statistics = self.page.locator('.an-dist .tl-kv').inner_text()
        last = self.page.locator('[data-act=dist-bucket] option').last.get_attribute('value')
        self.page.locator('[data-act=dist-bucket]').select_option(last)
        self.next()
        self.assertEqual(len(self.ids()), 10)
        self.assertIn('51–60 of 60', self.status())
        rows = self.rows()
        self.assertEqual(len(rows), 60)
        self.assertEqual({float(row['duration_ns']) for row in rows}, {10000})
        self.assertEqual(self.page.locator('.an-dist .tl-kv').inner_text(), statistics)
        self.page.locator('[data-act=dist-reset-bucket]').click()
        self.assertIn('1–50 of 240', self.status())
        self.assertEqual(len(self.rows()), 240)

    def test_bucket_boundaries_reuse_sorted_calls_without_logarithms_per_call(self):
        durations = [0, 1, 2, 9, 10, 11, 99, 100, 101, 999, 1000, 1001, 9999, 10000, 10001]
        self.open_report(20001, update=lambda data: data['cols'].update(
            dur=[durations[index % len(durations)] for index in range(data['n'])]), open_dist=False)
        result = self.page.evaluate('''async () => {
            const original = Math.log10;
            let logCalls = 0;
            Math.log10 = value => { logCalls++; return original(value); };
            try {
                const model = fgView('timeline').probe();
                model.dist(model.sName[0]);
                const deadline = performance.now() + 5000;
                while (!document.querySelector('[data-act=dist-bucket]')) {
                    if (performance.now() > deadline) throw Error('Distribution did not finish');
                    await new Promise(requestAnimationFrame);
                }
            } finally { Math.log10 = original; }
            const durations = fgData.read('tlData').cols.dur;
            const low = 0, high = Math.log10(10001) + 1e-9;
            const count = Math.max(8, Math.min(40, Math.ceil((high - low) * 10)));
            const width = (high - low) / count;
            const expected = Array.from({length: count}, () => []);
            for (let slice = 0; slice < durations.length; slice++) {
                const bucket = Math.max(0, Math.min(count - 1, Math.floor(Math.log10(Math.max(1, durations[slice])) / width)));
                expected[bucket].push(slice);
            }
            const select = document.querySelector('[data-act=dist-bucket]');
            const actualBuckets = Array.from(select.options).slice(1).map(option => Number(option.value));
            const expectedBuckets = expected.flatMap((ids, index) => ids.length ? [index] : []);
            const matches = JSON.stringify(actualBuckets) === JSON.stringify(expectedBuckets) && actualBuckets.every(index => {
                const ids = expected[index].sort((left, right) => durations[right] - durations[left] || left - right);
                select.value = index;
                select.dispatchEvent(new Event('change', {bubbles: true}));
                const next = document.querySelector('[data-act=dist-next]');
                let cursor = 0;
                while (true) {
                    for (const row of document.querySelectorAll('.an-list tr')) {
                        if (Number(row.dataset.slice) !== ids[cursor++]) return false;
                    }
                    if (next.disabled) return cursor === ids.length;
                    next.click();
                }
            });
            return {matches, logCalls};
        }''')
        self.assertTrue(result['matches'])
        self.assertLess(result['logCalls'], 650)

    def test_histogram_marks_origin_buckets_but_not_propagation_buckets(self):
        def update(data):
            data['cols']['dur'] = [1, 1, 100, 100, 10000, 10000]
            data['errs'] = [[1, 12, 1, 0, 1, 0], [3, 12, 0, 0, 1, 0]]
            data['chains'] = [[3, 1, 12, 0, 2, 1, 0]]
        self.open_report(6, update=update, open_dist=False)
        colors = self.page.evaluate('''async () => {
            const canvas = CanvasRenderingContext2D.prototype, original = canvas.fillRect, bars = [];
            canvas.fillRect = function (...args) {
                if (this.canvas.matches('.an-hist') && this.globalAlpha === 0.85) bars.push(this.fillStyle);
                return original.apply(this, args);
            };
            try {
                const model = fgView('timeline').probe();
                model.dist(model.sName[0]);
                await new Promise(resolve => requestAnimationFrame(resolve));
            } finally { canvas.fillRect = original; }
            return bars;
        }''')
        self.assertEqual(len(colors), 3)
        self.assertEqual(colors[0], colors[2])
        self.assertNotEqual(colors[0], colors[1])
        select = self.page.locator('[data-act=dist-bucket]')
        select.select_option(index=2)
        self.assertEqual([row['error_origin'] for row in self.rows()], ['0', '1'])

    def test_site_and_shared_filter_changes_reset_pages_and_keep_empty_scope_reversible(self):
        def update(data):
            data['strings'] += ['caller_b']
            data['cols']['caller'] = [1] * 120 + [2] * 120
        self.open_report(240, update=update)
        self.page.locator('tr[data-site="caller_b"] button').click()
        self.next()
        self.assertIn('51–100 of 120', self.status())
        self.page.evaluate("fgNotifyFilter('first site', new Set(Array.from({length: 120}, (_, index) => index + 1)))")
        self.assertEqual(self.status(), 'No matching calls')
        self.assertEqual(self.ids(), [])
        for action in ('dist-prev', 'dist-next', 'dist-csv'):
            self.assertTrue(self.page.locator(f'[data-act={action}]').is_disabled())
        self.page.locator('[data-act=dist-reset-site]').click()
        self.assertIn('1–50 of 120', self.status())
        self.assertEqual({row['caller'] for row in self.rows()}, {'caller+0x10/0x20'})

    def test_histograms_reuse_recent_sites_but_invalidate_on_filter_changes(self):
        def update(data):
            data['strings'] = ['target'] + [f'caller_{index}' for index in range(5)]
            data['cols']['caller'] = [1 + index // 120 for index in range(data['n'])]
        self.open_report(600, update=update)

        def measure(selector):
            return self.page.evaluate('''selector => {
                const original = Math.log10;
                let calls = 0;
                Math.log10 = value => { calls++; return original(value); };
                try { document.querySelector(selector).click(); }
                finally { Math.log10 = original; }
                return calls;
            }''', selector)

        def site(index):
            return measure(f'[data-site="caller_{index}"] button')

        cold = site(0)
        self.assertGreater(cold, 2)
        self.next()
        self.assertIn('51–100 of 120', self.status())
        self.assertEqual(measure('[data-act=dist-reset-site]'), 0)
        self.assertEqual(site(0), 0)
        self.assertIn('1–50 of 120', self.status())
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), '-1')
        for index in (1, 2, 3):
            self.assertGreater(site(index), 2)
        self.assertEqual(site(0), 0)
        self.assertGreater(site(4), 2)
        self.assertEqual(site(0), 0)
        self.assertGreater(site(1), 2)
        self.assertEqual(measure('[data-act=dist-reset-site]'), 0)

        self.page.evaluate('''fgNotifyFilter('half of two sites', new Set(
            Array.from({length: 120}, (_, index) => index + (index < 60 ? 1 : 61))))''')
        self.assertGreater(site(0), 2)
        self.assertIn('1–50 of 60', self.status())
        self.assertEqual([int(row['trace_line']) for row in self.rows()], list(range(60, 0, -1)))

    def test_refresh_reopening_and_row_selection_preserve_page_and_keyboard_focus(self):
        self.open_report()
        self.page.locator('[data-act=dist-next]').press('Enter')
        before = self.ids()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")
        self.assertEqual(self.ids(), before)
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'dist-next')
        self.page.locator('.an-list [data-slice]').first.click()
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), before[0])
        self.assertEqual(self.ids(), before)
        self.page.locator('.an-dist-head .tl-close').click()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")
        self.assertEqual(self.ids(), before)

    def test_overhead_refresh_resets_page_but_retains_measured_positions(self):
        def update(data):
            data['cols']['adj'] = [duration / 2 for duration in data['cols']['dur']]
        self.open_report(update=update)
        self.next()
        before = self.rows()
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        after = self.rows()
        self.assertIn('1–50 of 205', self.status())
        self.assertEqual([(row['start_ns'], row['end_ns']) for row in after], [(row['start_ns'], row['end_ns']) for row in before])
        self.assertEqual([float(row['duration_ns']) for row in after], [float(row['duration_ns']) / 2 for row in before])

    def test_csv_time_bases_remain_truthful_in_each_mode(self):
        for mode in ('abs', 'rel', 'compressed', 'count'):
            with self.subTest(mode=mode):
                def update(data):
                    data['mode'] = mode
                    data['cols']['dur'] = [1000] * 205
                self.open_report(update=update)
                self.next()
                rows = self.rows()
                self.assertEqual(len(rows), 205)
                self.assertEqual({row['time_mode'] for row in rows}, {mode})
                if mode == 'count':
                    self.assertEqual({row['duration_units'] for row in rows}, {'1'})
                    self.assertNotIn('duration_ns', rows[0])
                else:
                    self.assertEqual({row['duration_ns'] for row in rows}, {'1000'})

    def test_touch_paging_and_expansion_both_themes_orientations_and_sidecar(self):
        self.context.close()
        for theme in ('light', 'dark'):
            context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
            self.addCleanup(context.close)
            self.page = context.new_page()
            self.page.on('pageerror', lambda error: self.errors.append(str(error)))
            self.page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
            self.open_report(sidecar=True, site_count=14)
            for width, height in ((390, 844), (320, 700), (844, 390)):
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                    for action in ('dist-prev', 'dist-next', 'dist-csv'):
                        self.assertGreaterEqual(self.page.locator(f'[data-act={action}]').bounding_box()['height'], 44)
                    self.page.locator('[data-act=dist-next]').tap()
                    self.assertIn('51–100 of 205', self.status())
                    self.page.locator('[data-act=dist-prev]').tap()
                    self.assertIn('1–50 of 205', self.status())
                    previous = self.page.locator('[data-act=dist-sites-prev]')
                    next_page = self.page.locator('[data-act=dist-sites-next]')
                    for button in (previous, next_page):
                        self.assertGreaterEqual(button.bounding_box()['height'], 44)
                    next_page.tap()
                    self.assertIn('13–14 of 14', self.page.locator('.an-site-count').inner_text())
                    self.assertEqual(self.page.locator('.an-sites tr[data-site]').count(), 2)
                    previous.tap()
                    self.assertIn('1–12 of 14', self.page.locator('.an-site-count').inner_text())
                    self.assertIn('1–50 of 205', self.status())
                    self.assertEqual(len(self.rows()), 205)
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
            context.close()

    def test_section_jumps_in_short_touch_pane_keep_pages_and_chart_position(self):
        self.context.close()
        context = self.browser.new_context(viewport={'width': 320, 'height': 700}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.add_init_script("localStorage.setItem('fgTl.detailFrac', '0.18')")
        self.open_report(sidecar=True, site_count=14)
        pane = self.page.locator('.tl-detail.tl-detail-short')
        jump = pane.get_by_role('combobox', name='Jump to distribution section')
        self.assertEqual(jump.count(), 1)
        self.next()
        self.page.locator('[data-act=dist-sites-next]').click()
        snapshot = (self.ids(), self.status(), self.page.locator('.an-site-count').inner_text(), self.export(),
                    self.page.evaluate('fgView("timeline").probe().view()'))
        for option, target in (('calls', '[data-act=dist-bucket]'), ('slowfast', '[data-act=slowfast]'), ('sites', '.an-site-heading')):
            with self.subTest(section=option):
                pane.evaluate('pane => { pane.scrollTop = 0; pane.scrollLeft = pane.scrollWidth; }')
                self.assertTrue(jump.evaluate('''node => {
                    const {left, top, width, height} = node.getBoundingClientRect();
                    return height >= 44 && document.elementFromPoint(left + width / 2, top + height / 2) === node;
                }'''))
                jump.select_option(option)
                self.assertTrue(pane.locator(target).evaluate('''node => {
                    const rect = node.getBoundingClientRect(), x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
                    return document.activeElement === node && node.contains(document.elementFromPoint(x, y));
                }'''))
                self.assertEqual(jump.input_value(), '')
                if option == 'sites':
                    self.assertGreater(pane.evaluate('pane => pane.scrollTop'), 1000)
                self.assertEqual(self.page.locator('.an-slowfast-table').count(), 0)
        self.assertEqual((self.ids(), self.status(), self.page.locator('.an-site-count').inner_text(), self.export(),
                          self.page.evaluate('fgView("timeline").probe().view()')), snapshot)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 320)

    def test_pager_stays_inside_the_call_browser_not_over_the_following_sections(self):
        def update(data):
            data['strings'] += ['other_caller']
            data['cols']['caller'] = [1] * 100 + [2] * 105
        self.open_report(update=update)
        self.next()
        pane = self.page.locator('.tl-detail').bounding_box()
        pager = self.page.locator('.an-dist-pager').bounding_box()
        self.assertGreaterEqual(pager['y'], pane['y'])
        self.assertLessEqual(pager['y'] + pager['height'], pane['y'] + pane['height'])
        self.page.locator('.an-sites').scroll_into_view_if_needed()
        sites = self.page.locator('.an-sites').bounding_box()
        pager = self.page.locator('.an-dist-pager').bounding_box()
        self.assertLessEqual(pager['y'] + pager['height'], sites['y'])

    def test_short_call_pager_precedes_rows_and_stays_reachable_while_paging(self):
        self.context.close()
        context = self.browser.new_context(viewport={'width': 320, 'height': 700}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.add_init_script("localStorage.setItem('fgTl.detailFrac', '0.18')")
        self.open_report(101, update=lambda data: data['strings'].__setitem__(1, 'long_recorded_caller_' * 10))
        pane = self.page.locator('.tl-detail')
        pager = self.page.locator('.an-dist-pager')
        previous = pager.get_by_role('button', name='Previous page of calls')
        next_page = pager.get_by_role('button', name='Next page of calls')
        self.assertTrue(pager.evaluate('''node => node.nextElementSibling.matches('table') &&
            node.closest('.tl-detail').classList.contains('tl-detail-short')'''))

        self.page.locator('[data-act=dist-histogram]').tap()
        self.page.locator('[data-act=dist-jump]').select_option('calls')
        scroll = pane.evaluate('node => node.scrollTop')
        pane.evaluate('node => { node.scrollLeft = node.scrollWidth; }')
        next_page.scroll_into_view_if_needed()
        self.assertLess(pane.evaluate('node => node.scrollTop') - scroll, 200)
        hit = '''node => {
            const rect = node.getBoundingClientRect();
            return rect.height >= 44 && node.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
        }'''
        self.assertTrue(next_page.evaluate(hit))
        next_page.tap()
        self.assertIn('51–100 of 101', self.status())
        self.assertTrue(next_page.evaluate(hit))
        next_page.press('Enter')
        self.assertEqual(self.ids(), [0])
        self.assertTrue(previous.evaluate('node => document.activeElement === node'))
        self.assertTrue(previous.evaluate(hit))
        previous.press('Space')
        self.assertEqual(self.ids(), list(range(50, 0, -1)))
        self.assertTrue(previous.evaluate(hit))

        self.page.locator('[data-detail-expand]').tap()
        self.assertTrue(pager.evaluate('''node => node.previousElementSibling.matches('table') &&
            !node.closest('.tl-detail').classList.contains('tl-detail-short')'''))
        self.assertIn('51–100 of 101', self.status())
        self.page.locator('[data-detail-expand]').tap()
        self.assertTrue(pager.evaluate("node => node.nextElementSibling.matches('table')"))
        self.assertIn('51–100 of 101', self.status())
