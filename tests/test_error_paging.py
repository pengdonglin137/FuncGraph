"""Bounded error/atomic pages preserve complete diagnostics and occurrence state."""
import csv
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import helpers
from benchmark_error_csv import fixture as mixed_csv_fixture
from benchmark_errors import fixture
from fglib.viewer import create_server


class ErrorPagingTest(unittest.TestCase):
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
        cls.kernel.run(helpers.fixture('pi4_sched'), '--no-config', '--timeline')
        cls.template = Path(cls.kernel.dir) / 'out.html'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-error-page-')
        self.addCleanup(self.directory.cleanup)
        self.serial = 0
        self.errors = []
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = None

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, count=650, atomic_count=None, update=None, mixed_csv=False):
        self.serial += 1
        if self.page and not self.page.is_closed():
            self.page.close()
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        report = Path(self.directory.name) / f'errors-{self.serial}.html'
        if mixed_csv:
            mixed_csv_fixture(self.template, report, count)
        else:
            fixture(self.template, report, count, atomic_count)
        if update:
            html = report.read_text(encoding='utf-8')
            match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
            data = json.loads(match[2])
            update(data)
            report.write_text(html[:match.start(2)] + json.dumps(data).replace('<', r'\u003c') + html[match.end(2):], encoding='utf-8')
        self.page.goto(report.as_uri())
        self.page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().errors(); }")

    def ids(self, atomic=False):
        return self.page.locator('tr[data-atomic-group]' if atomic else 'tr[data-chains]').evaluate_all(
            'rows => rows.map(row => row.dataset.atomicGroup ?? row.dataset.chains)')

    def status(self, atomic=False):
        return self.page.locator('[data-error-footer="' + ('atomic' if atomic else 'errors') + '"] [role=status]').inner_text()

    def button(self, atomic=False, direction='next'):
        return self.page.locator('[data-act="' + ('atomic' if atomic else 'errors') + '-' + direction + '"]')

    def export(self, kind='groups'):
        selector = {'groups': 'errors-csv', 'occurrences': 'error-occurrences-csv', 'atomic': 'atomic-csv'}[kind]
        with self.page.expect_download() as download:
            self.page.locator('[data-act="' + selector + '"]').click()
        return Path(download.value.path()).read_bytes()

    def rows(self, kind='groups'):
        return list(csv.DictReader(io.StringIO(self.export(kind).decode('utf-8-sig'))))

    def test_overview_markers_are_reused_during_zoom_and_refresh_for_scope_and_theme(self):
        def update(data):
            for index, chain in enumerate(data['chains']):
                chain[6] = int(index % 7 == 0)

        self.open_report(update=update)
        self.page.evaluate('''async () => {
            window.rulerFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            await rulerFrame();
            const fillRect = CanvasRenderingContext2D.prototype.fillRect;
            window.rulerMarks = 0;
            CanvasRenderingContext2D.prototype.fillRect = function (left, top, width, height) {
                if (top === 1 && width === 1.5 && ['#d32f2f', '#e8873a', '#e53935', '#d9822b'].includes(this.fillStyle)) rulerMarks++;
                return fillRect.call(this, left, top, width, height);
            };
            window.rulerPixels = () => document.querySelector('.tl-head-canvas').toDataURL();
        }''')
        original = self.page.evaluate('rulerPixels()')
        zoomed = self.page.evaluate('''async () => {
            fgView('timeline').probe().zoomTo(100000, 200000);
            await rulerFrame();
            return rulerPixels();
        }''')
        self.assertNotEqual(original, zoomed)
        self.assertEqual(self.page.evaluate('rulerMarks'), 0)

        def change(action):
            return self.page.evaluate('''async action => {
                rulerMarks = 0;
                if (action === 'theme') document.documentElement.setAttribute('data-theme', 'dark');
                else document.querySelector(action).click();
                await rulerFrame();
                return [rulerMarks, rulerPixels()];
            }''', action)

        marks, all_chains = change('[data-act=errmode]')
        self.assertGreater(marks, 300)
        self.assertNotEqual(all_chains, zoomed)
        marks, with_minor = change('[data-act=minor]')
        self.assertEqual(marks, 650)
        self.assertNotEqual(with_minor, all_chains)
        marks, disabled = change('[data-ov=err]')
        self.assertEqual(marks, 0)
        self.assertNotEqual(disabled, with_minor)
        marks, restored = change('[data-ov=err]')
        self.assertEqual(marks, 650)
        self.assertEqual(restored, with_minor)
        marks, dark = change('theme')
        self.assertEqual(marks, 650)
        self.assertNotEqual(dark, restored)

    def test_track_error_markers_read_nearby_origins_in_original_draw_order(self):
        for ordered in (True, False):
            with self.subTest(ordered=ordered):
                def update(data):
                    data['cols']['dur'] = [1] * len(data['chains'])
                    data['cols']['start'][2911] = 0
                    if not ordered:
                        for left, right in ((10, 2900), (2910, 2911)):
                            data['chains'][left], data['chains'][right] = data['chains'][right], data['chains'][left]

                self.open_report(count=4096, atomic_count=0, update=update)
                result = self.page.evaluate(r'''async () => {
                    const wait = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    document.querySelector('[data-act=errmode]').click();
                    await wait();
                    const model = fgView('timeline').probe(), source = fgData.read('tlData');
                    const track = model.TRACKS[0], canvas = document.querySelector('.tl-canvas');
                    const ctx = canvas.getContext('2d');
                    const actual = [];
                    let reads = 0, path = [];
                    track.errs = new Proxy(track.errs, {get(target, key) {
                        if (typeof key === 'string' && /^\d+$/.test(key)) reads++;
                        return Reflect.get(target, key);
                    }});
                    for (const method of ['beginPath', 'moveTo', 'lineTo', 'fill']) {
                        const original = ctx[method];
                        ctx[method] = function (...args) {
                            if (method === 'beginPath') path = [];
                            else if (method === 'moveTo' || method === 'lineTo') path.push(args);
                            else if (path.length === 3 && path[1][0] === path[0][0] + 4.5 &&
                                     path[2][0] === path[0][0] - 4.5 && path[1][1] === path[0][1] + 8 &&
                                     ['#e8873a', '#d32f2f'].includes(ctx.fillStyle)) actual.push([path[0][0], ctx.fillStyle]);
                            return original.apply(this, args);
                        };
                    }
                    model.zoomTo(2900000, 2920000);
                    await wait();
                    const [start, end] = model.view(), width = parseFloat(canvas.style.width), scale = width / (end - start);
                    const expected = source.chains.map(chain => [(model.sStart[chain[0]] - start) * scale,
                        chain[3] === 0 ? '#e8873a' : '#d32f2f']).filter(([x]) => x >= -6 && x <= width + 6);
                    return {actual, expected, reads};
                }''')
                self.assertGreater(len(result['expected']), 20)
                self.assertEqual(result['actual'], result['expected'])
                self.assertLess(result['reads'], 400 if not ordered else 70)

    def test_independent_pages_replace_rows_and_preserve_complete_exports(self):
        self.open_report()
        original = {kind: self.export(kind) for kind in ('groups', 'occurrences', 'atomic')}
        first = self.ids(), self.ids(True)
        first_row = self.page.locator('tr[data-chains]').first.element_handle()
        self.button().press('Enter')
        self.assertEqual(len(self.ids()), 200)
        self.assertFalse(first_row.evaluate('row => row.isConnected'))
        self.assertEqual(self.ids(True), first[1])
        self.assertIn('201–400 of 650 error groups', self.status())
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'errors-next')
        error_page = self.ids()
        self.button(True).press('Space')
        self.assertEqual(self.ids(), error_page)
        self.assertEqual(len(self.ids(True)), 100)
        self.assertIn('101–200 of 650 atomic groups', self.status(True))
        for kind, content in original.items():
            self.assertEqual(self.export(kind), content)
        self.button(direction='prev').click()
        self.button(True, 'prev').click()
        self.assertEqual((self.ids(), self.ids(True)), first)

    def test_exact_boundaries_and_keyboard_endpoint_focus(self):
        for atomic, size in ((False, 200), (True, 100)):
            for count in (1, size, size + 1, size * 2, size * 2 + 1):
                with self.subTest(atomic=atomic, count=count):
                    self.open_report(count)
                    first = self.ids(atomic)
                    self.assertEqual(len(first), min(count, size))
                    if count <= size:
                        self.assertEqual(self.button(atomic).count(), 0)
                        self.assertEqual(self.button(atomic, 'prev').count(), 0)
                        continue
                    self.assertTrue(self.button(atomic, 'prev').is_disabled())
                    while self.button(atomic).is_enabled():
                        self.button(atomic).press('Enter')
                    self.assertEqual(len(self.ids(atomic)), (count - 1) % size + 1)
                    prefix = 'atomic' if atomic else 'errors'
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), prefix + '-prev')
                    while self.button(atomic, 'prev').is_enabled():
                        self.button(atomic, 'prev').press('Space')
                    self.assertEqual(self.ids(atomic), first)
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), prefix + '-next')

    def test_thousands_of_groups_remain_reachable_without_dom_growth_or_reaggregation(self):
        self.open_report(5003)
        for atomic, size in ((False, 200), (True, 100)):
            with self.subTest(atomic=atomic):
                expected = [row['group_id'] for row in self.rows('atomic' if atomic else 'groups')]
                result = self.page.evaluate('''atomic => {
                    const original = Array.prototype.sort;
                    Array.prototype.sort = () => { throw new Error('Prepared groups and order must be reused'); };
                    try {
                        const ids = [], sizes = [], kind = atomic ? 'atomic' : 'errors';
                        while (true) {
                            const rows = [...document.querySelectorAll(atomic ? 'tr[data-atomic-group]' : 'tr[data-chains]')];
                            sizes.push(rows.length);
                            ids.push(...rows.map(row => row.dataset.atomicGroup ?? row.dataset.chains));
                            const next = document.querySelector('[data-act="' + kind + '-next"]');
                            if (next.disabled) break;
                            next.click();
                        }
                        return {ids, sizes};
                    } finally { Array.prototype.sort = original; }
                }''', atomic)
                self.assertEqual(result['ids'], expected)
                self.assertEqual(result['sizes'], [size] * (5003 // size) + [5003 % size])
                self.assertEqual(len(set(result['ids'])), 5003)

    def test_toolbar_continues_from_the_current_call_instead_of_an_old_error(self):
        self.open_report(6)
        self.page.locator('[data-act=errmode]').evaluate('button => { if (button.textContent.includes("to user")) button.click(); }')
        self.page.locator('[data-act=errnext]').evaluate('button => button.click()')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)
        self.page.evaluate("fgView('timeline').probe().select(4)")
        self.assertEqual(self.page.locator('.err-label').inner_text(), '6 errors · all')
        for action, index in (('errnext', 5), ('errnext', 0), ('errprev', 5)):
            self.page.locator(f'[data-act={action}]').evaluate('button => button.click()')
            self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), index)
            self.assertEqual(self.page.locator('.err-label').inner_text(), f'{index + 1}/6 errors · all')

    def test_occurrence_rows_anchor_toolbar_navigation_even_at_tied_times(self):
        def mixed(data):
            chains = data['chains']
            data['chains'] = [chains[index] for index in (2, 1, 0, 4, 5)]
            data['cols']['start'][2:4] = [0, 0]
            data['cols']['flags'][3] = 1 << 25
            data['uftrace'] = {'spaces': ['kernel', 'user'], 'user_details': {'4': {
                'failure': {'rule': 'minus_one', 'summary': 'API error', 'note': 'Synthetic API failure'}
            }}}

        self.open_report(6, 0, mixed)
        self.page.locator('tr[data-chain="0"] [data-error-step="1"]').evaluate('button => button.click()')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 2)
        self.assertEqual(self.page.locator('.err-label').inner_text(), '2/6 errors · all')
        for index in (1, 3):
            self.page.locator('[data-act=errnext]').evaluate('button => button.click()')
            self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), index)
        self.page.evaluate("fgView('timeline').probe().errors()")
        self.page.locator('tr[data-user-error-group] [data-error-step="1"]').evaluate('button => button.click()')
        self.assertEqual(self.page.locator('.err-label').inner_text(), '4/6 errors · all')
        self.page.locator('[data-act=errprev]').evaluate('button => button.click()')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)

    def test_unanchored_navigation_bounds_origin_reads_in_large_error_lists(self):
        self.open_report(8192, 0)
        facts = self.page.evaluate('''() => {
            const model = fgView('timeline').probe(), chains = fgData.read('tlData').chains;
            const scope = document.querySelector('[data-act=errmode]');
            if (scope.textContent.includes('to user')) scope.click();
            let reads = 0;
            for (const chain of chains) {
                const origin = chain[0];
                Object.defineProperty(chain, 0, {get() { reads++; return origin; }});
            }
            return ['errnext', 'errprev'].map(action => {
                model.select(4096);
                scope.click(); scope.click();
                reads = 0;
                document.querySelector(`[data-act=${action}]`).click();
                return {selected:model.sel(), reads};
            });
        }''')
        self.assertEqual([item['selected'] for item in facts], [4097, 4095])
        self.assertLess(max(item['reads'] for item in facts), 24, facts)

    def test_occurrence_positions_survive_page_replacement_reopen_and_text_roundtrips(self):
        def update(data):
            data['cols']['name'][:75] = [0] * 75
            for index in range(75):
                data['chains'][index][2] = 2
                data['atomic'][index][1] = 1
        self.open_report(725, update=update)
        for atomic in (False, True):
            selector = 'tr[data-atomic-group]' if atomic else 'tr[data-chains]'
            row = self.page.locator(selector).first
            row.locator('[data-error-step="-1"]').press('Enter')
            self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 74)
            self.page.evaluate('''selector => {
                const row = document.querySelector(selector);
                for (let index = 0; index < 52; index++) row.click();
            }''', selector)
            self.assertEqual(row.locator('[data-error-position]').inner_text(), '52 / 75')
            self.button(atomic).click()
            self.button(atomic, 'prev').click()
            self.assertEqual(self.page.locator(selector).first.locator('[data-error-position]').inner_text(), '52 / 75')
        self.button().click()
        self.button(True).click()
        before = self.ids(), self.ids(True), self.status(), self.status(True)
        self.button().focus()
        self.page.evaluate("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'errors-next')
        self.page.locator('.tl-close').click()
        self.page.evaluate("fgView('timeline').probe().errors()")
        self.assertEqual((self.ids(), self.ids(True), self.status(), self.status(True)), before)
        self.page.evaluate("async () => { await fgSwitchView('text'); await fgSwitchView('timeline'); }")
        self.assertEqual((self.ids(), self.ids(True), self.status(), self.status(True)), before)
        for atomic in (False, True):
            self.button(atomic, 'prev').click()
            selector = 'tr[data-atomic-group]' if atomic else 'tr[data-chains]'
            self.assertEqual(self.page.locator(selector).first.locator('[data-error-position]').inner_text(), '52 / 75')

    def test_sort_minor_and_shared_filter_invalidation_keep_independent_scope(self):
        def update(data):
            data['chains'][-1][6] = 1
        self.open_report(update=update)
        self.button().click()
        self.button(True).click()
        atomic = self.ids(True), self.status(True)
        self.page.locator('th[data-esort=origin] button').press('Enter')
        self.assertIn('1–200 of 649', self.status())
        self.assertEqual((self.ids(True), self.status(True)), atomic)
        self.assertTrue(self.page.locator('th[data-esort=origin] button').evaluate('node => node === document.activeElement'))
        self.button().click()
        self.page.locator('[data-act=minor]').press('Space')
        self.assertIn('1–200 of 650', self.status())
        self.assertEqual((self.ids(True), self.status(True)), atomic)
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'minor')
        self.page.evaluate("fgNotifyFilter('last call', new Set([650]))")
        self.assertEqual(len(self.ids()), 1)
        self.assertEqual(len(self.ids(True)), 1)
        self.assertEqual(self.button().count(), 0)
        self.assertEqual(self.button(True).count(), 0)
        self.page.evaluate("fgNotifyFilter('', null)")
        self.assertIn('1–200 of 650', self.status())
        self.assertIn('1–100 of 650', self.status(True))

    def test_overhead_retains_pages_positions_and_original_geometry(self):
        self.open_report()
        self.button().click()
        self.button(True).click()
        before = self.ids(), self.ids(True), self.status(), self.status(True)
        raw = {kind: self.rows(kind) for kind in ('groups', 'occurrences', 'atomic')}
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.assertEqual((self.ids(), self.ids(True), self.status(), self.status(True)), before)
        for kind in ('occurrences', 'atomic'):
            adjusted = self.rows(kind)
            duration = 'origin_duration_ns' if kind == 'occurrences' else 'duration_ns'
            start = 'origin_start_ns' if kind == 'occurrences' else 'start_ns'
            end = 'origin_end_ns' if kind == 'occurrences' else 'end_ns'
            self.assertEqual(len(adjusted), 650)
            for original, changed in zip(raw[kind], adjusted):
                self.assertEqual((original[start], original[end]), (changed[start], changed[end]))
                self.assertEqual(float(changed[duration]), float(original[duration]) / 2)
                self.assertEqual(changed['duration_basis'], 'overhead_subtracted_estimate')
        self.assertEqual(self.rows(), raw['groups'])

    def test_full_csv_quoting_and_canonical_modes_do_not_depend_on_page(self):
        def update(data):
            data['strings'][0] = '=SUM(1,2)'
            data['tasks'][0]['comm'] = '\tquoted,"\n任务'
        for mode in ('abs', 'rel', 'compressed', 'count'):
            with self.subTest(mode=mode):
                def configured(data):
                    update(data)
                    data['mode'] = mode
                self.open_report(405, update=configured)
                original = {kind: self.export(kind) for kind in ('groups', 'occurrences', 'atomic')}
                self.button().click()
                self.button(True).click()
                for kind, content in original.items():
                    self.assertEqual(self.export(kind), content)
                    rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
                    field = 'function' if kind == 'atomic' else 'origin'
                    self.assertEqual(rows[0][field], "'=SUM(1,2)")
                    if kind != 'groups':
                        self.assertEqual({row['time_mode'] for row in rows}, {mode})
                        unit = 'units' if mode == 'count' else 'ns'
                        self.assertIn('origin_start_' + unit if kind == 'occurrences' else 'start_' + unit, rows[0])
                    self.assertIn('任务', rows[0]['task_labels' if kind == 'groups' else 'task'])

    def test_large_csv_preserves_all_rows_and_order_through_paging_and_text(self):
        count = 2050
        self.open_report(count, mixed_csv=True)
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        for kind, action, identity in (('groups', 'errors-csv', 'group_id'),
                                       ('occurrences', 'error-occurrences-csv', 'chain_index'),
                                       ('atomic', 'atomic-csv', 'slice_index'),
                                       ('user', 'user-errors-csv', 'slice_index')):
            with self.subTest(kind=kind):
                button = self.page.locator(f'[data-act={action}]')
                status = button.locator('+ .an-error-csv-status')
                label = button.inner_text()
                self.assertEqual(button.evaluate('''button => new Promise((resolve, reject) => {
                    const status = button.nextElementSibling, clock = performance.now.bind(performance);
                    let elapsed = 0;
                    performance.now = () => clock() + (elapsed += 7);
                    button.click();
                    for (const pager of document.querySelectorAll('[data-act=errors-next], [data-act=atomic-next]')) pager.click();
                    document.querySelector('tr[data-chains] [data-error-step="1"]').click();
                    const observer = new MutationObserver(() => {
                        if (!status.textContent.includes('%')) return;
                        observer.disconnect(); clearTimeout(timeout); delete performance.now;
                        fgSwitchView('text');
                        resolve([button.textContent, status.textContent]);
                    });
                    const timeout = setTimeout(() => {
                        observer.disconnect(); delete performance.now; reject(Error('No CSV progress'));
                    }, 5000);
                    observer.observe(status, {childList: true});
                })'''), ['Cancel CSV', 'Preparing CSV… 25%'])
                self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
                self.assertEqual(downloads, [])
                self.assertEqual(status.text_content(), 'Preparing CSV… 25%')
                with self.page.expect_download() as downloaded:
                    self.page.evaluate("fgSwitchView('timeline')")
                filename = 'funcgraph-user-api-failures.csv' if kind == 'user' else f'funcgraph-errors-{kind}.csv'
                self.assertEqual(downloaded.value.suggested_filename, filename)
                self.assertEqual(len(downloads), 1)
                downloads.clear()
                raw = Path(downloaded.value.path()).read_bytes()
                self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
                self.assertTrue(raw.endswith(b'\r\n'))
                rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
                expected = list(range(count))
                if kind == 'user':
                    expected = [index for offset in range(4) for index in range(count + offset, 2 * count, 4)]
                    self.assertIn('"用户"', rows[0]['function'])
                self.assertEqual([int(row[identity]) for row in rows], expected)
                self.assertTrue(status.is_hidden())
                self.assertEqual(button.inner_text(), label)
                with self.page.expect_download() as replay:
                    button.click()
                self.assertEqual(Path(replay.value.path()).read_bytes(), raw)
                downloads.clear()

    def test_large_csv_cancel_control_fits_and_scope_changes_discard_pending_work(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 320, 'height': 700}, has_touch=True, is_mobile=True)
        self.addCleanup(self.context.close)
        self.open_report(2060, update=lambda data: data['chains'][-1].__setitem__(6, 1))
        button = self.page.locator('[data-act=errors-csv]')
        status = button.locator('+ .an-error-csv-status')
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        for change in (
            'button.click()',
            "document.querySelector('th[data-esort=origin] button').click()",
            "document.querySelector('[data-act=minor]').click()",
            "document.querySelector('[data-act=adj]').click()",
            "fgNotifyFilter('one call', new Set([1]))",
            "document.querySelector('.an-error-head .tl-close').click()",
            "fgView('timeline').probe().sections()",
        ):
            with self.subTest(change=change):
                self.assertEqual(button.evaluate(f'''button => {{
                    document.querySelector('.tl-detail').scrollLeft = 1e6;
                    button.click();
                    const visible = [button, button.nextElementSibling].every(element => {{
                        const rect = element.getBoundingClientRect();
                        return rect.left >= 0 && rect.right <= innerWidth && rect.width > 0;
                    }});
                    const result = [button.textContent, visible, button.offsetHeight >= 44,
                        document.querySelector('[data-act=atomic-csv]').disabled];
                    {change};
                    return result;
                }}'''), ['Cancel CSV', True, True, True])
                self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
                self.assertEqual(downloads, [])
                self.assertTrue(status.is_hidden())
                self.page.evaluate("fgNotifyFilter('', null); fgView('timeline').probe().errors();")
                self.assertEqual(button.inner_text(), 'Export groups CSV')
                self.assertFalse(self.page.locator('[data-act=atomic-csv]').is_disabled())

    def test_empty_single_scope_and_atomic_only_populations_have_no_paging_clutter(self):
        for count, atomic_count in ((0, 0), (1, 0), (0, 1), (200, 100)):
            with self.subTest(count=count, atomic_count=atomic_count):
                self.open_report(count, atomic_count)
                self.assertEqual(self.page.locator('.an-error-pages').count(), 0)
                self.assertEqual(self.page.locator('[data-act=errors-csv]').is_disabled(), not count)
                self.assertEqual(len(self.ids()), count)
                self.assertEqual(len(self.ids(True)), atomic_count)
        self.open_report()
        self.page.evaluate("fgNotifyFilter('nothing', new Set())")
        self.assertEqual((self.ids(), self.ids(True)), ([], []))
        self.assertEqual(self.page.locator('.an-error-pages').count(), 0)
        self.assertIn('No visible error chains match', self.page.locator('.tl-detail').inner_text())

    def test_touch_controls_in_both_themes_portrait_landscape_and_short_panes(self):
        self.context.close()
        for theme in ('light', 'dark'):
            self.context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
            self.addCleanup(self.context.close)
            self.context.add_init_script(f"localStorage.setItem('theme', '{theme}')")
            self.open_report(405)
            for width, height in ((390, 844), (320, 700), (844, 390)):
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
                    self.assertEqual(self.page.locator('.an-error-head').evaluate('node => getComputedStyle(node).top'), 'auto')
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                    self.page.evaluate("document.querySelector('.tl-detail').scrollLeft = 1e6")
                    for selector in ('[data-act=errors-csv]', '[data-act=atomic-csv]', '[data-act=error-occurrences-csv]', '.tl-close'):
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    for atomic in (False, True):
                        self.button(atomic).tap()
                        for direction in ('prev', 'next'):
                            button = self.button(atomic, direction)
                            button.scroll_into_view_if_needed()
                            bounds = button.bounding_box()
                            self.assertGreaterEqual(bounds['height'], 44)
                            self.assertGreaterEqual(bounds['width'], 44)
                            self.assertGreaterEqual(bounds['x'], 0)
                            self.assertLessEqual(bounds['x'] + bounds['width'], width)
                        self.button(atomic, 'prev').tap()
                    self.page.locator('.an-occurrence .tl-btn').first.scroll_into_view_if_needed()
                    bounds = self.page.locator('.an-occurrence .tl-btn').first.bounding_box()
                    self.assertGreaterEqual(bounds['width'], 44)
                    self.assertGreaterEqual(bounds['height'], 44)
                    self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                        self.assertEqual(self.page.locator('.an-error-table th').first.evaluate('node => getComputedStyle(node).position'), 'static')

    def test_mixed_pagers_are_reachable_at_each_table_start_without_scrolling_its_rows(self):
        def mixed(data):
            data['chains'] = data['chains'][:205]
            data['atomic'] = data['atomic'][:101]
            data['uftrace'] = {'spaces': ['kernel', 'user'], 'user_details': {}}
            for index in range(205, 406):
                data['cols']['flags'][index] = 1 << 25
                data['uftrace']['user_details'][str(index + 1)] = {
                    'failure': {'rule': 'minus_one', 'summary': 'API error', 'note': 'Synthetic user API failure'}
                }

        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 320, 'height': 700}, has_touch=True, is_mobile=True)
        self.addCleanup(self.context.close)
        self.context.add_init_script("localStorage.setItem('fgTl.detailFrac', '0.18')")
        modes = [('atomic', 'an-atomic-table', 100, 1), ('user-errors', 'an-user-error-table', 200, 1),
                 ('errors', 'an-error-table', 200, 5)]
        for short in (True, False):
            self.open_report(406, update=mixed)
            if not short:
                self.page.locator('[data-detail-expand]').tap()
            self.assertEqual(self.page.locator('.tl-detail').evaluate("pane => pane.classList.contains('tl-detail-short')"), short)
            current = [mode[2] for mode in modes]
            for slot, (kind, table_class, _, last_count) in enumerate(modes):
                with self.subTest(short=short, kind=kind):
                    table = self.page.locator('.' + table_class)
                    table.evaluate('''(table, short) => {
                        const pane = table.closest('.tl-detail');
                        pane.scrollLeft = pane.scrollWidth;
                        pane.scrollTop += table.getBoundingClientRect().top - pane.getBoundingClientRect().top
                            - pane.clientTop - (short ? 85 : 0);
                    }''', short)
                    next_button = self.page.locator(f'[data-act={kind}-next]')
                    point = self.page.evaluate('''kind => {
                        const button = document.querySelector(`[data-act=${kind}-next]`);
                        const rect = button.getBoundingClientRect(), x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
                        return button.contains(document.elementFromPoint(x, y)) ? [x, y] : null;
                    }''', kind)
                    self.assertIsNotNone(point, 'Next must be tappable at the start of its own table')
                    self.page.touchscreen.tap(*point)
                    current[slot] = last_count
                    self.assertEqual([self.page.locator(f'.{item[1]} tbody tr').count() for item in modes], current)
                    previous = self.page.locator(f'[data-act={kind}-prev]')
                    self.assertTrue(next_button.is_disabled())
                    self.assertTrue(previous.evaluate('''button => {
                        const rect = button.getBoundingClientRect();
                        return button === document.activeElement && button.contains(document.elementFromPoint(
                            rect.left + rect.width / 2, rect.top + rect.height / 2));
                    }'''), 'Focus must move to a visible previous-page button')
                    self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))

    def test_actual_parser_and_cli_sidecar_keep_every_original_error_chain(self):
        trace = Path(self.directory.name) / 'failures.txt'
        trace.write_text('# tracer: function_graph\n' + ''.join(
            f'  {0.001 + index * 0.000002:.9f} |   0) demo-1 |  ..... |   1.000 us    |  __x64_sys_failure_{index}(); /* ret=-2 */\n'
            for index in range(405)), encoding='utf-8')
        destination = Path(self.directory.name) / 'sidecar'
        self.kernel.run(str(trace), '--no-config', '--timeline', '--report-mode', 'sidecar', out=str(destination))
        server = create_server(destination)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(server.url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().errors(); }")
        original = self.export('occurrences')
        rows = list(csv.DictReader(io.StringIO(original.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 405)
        self.assertEqual({row['fate'] for row in rows}, {'to_user'})
        self.assertEqual({row['origin_line'] for row in rows}, {str(index + 2) for index in range(405)})
        first = self.ids()
        self.button().click()
        self.button().click()
        self.assertEqual(len(self.ids()), 5)
        self.assertEqual(self.export('occurrences'), original)
        self.button(direction='prev').click()
        self.button(direction='prev').click()
        self.assertEqual(self.ids(), first)


if __name__ == '__main__':
    unittest.main()
