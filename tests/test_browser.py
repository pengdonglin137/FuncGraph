"""Headless browser smoke test of a generated report (skipped without Playwright + Chromium)."""
import csv
import os
import unittest

import helpers

try:
    from playwright.sync_api import sync_playwright
except ImportError:      # pragma: no cover
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class BrowserSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = helpers.FakeKernel()
        cls.k.run(helpers.fixture('pi4_sched'), '--enable-fold', '--filter', '--func-links',
                  '--base-url', 'https://example.org/src', '--timeline',
                  '--compare', helpers.fixture('pi4_default'), out='report.html')
        cls.url = 'file://' + cls.k.dir + '/report.html'
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        except Exception as e:      # browser binaries not installed
            cls.pw.stop()
            cls.k.close()
            raise unittest.SkipTest(f'chromium not available: {e}')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.k.close()

    def setUp(self):
        self.errors = []
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
        self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        self.page.on('console', lambda m: self.errors.append(m.text) if m.type == 'error' else None)
        self.page.goto(self.url, wait_until='load')
        self.page.evaluate('localStorage.clear()')

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [], 'JavaScript errors in the page')

    def ev(self, js, arg=None):
        return self.page.evaluate(js, arg)

    def test_text_view_lazy_expand(self):
        line = self.page.query_selector('.line-container.expandable')
        lid = line.get_attribute('data-line-id')
        self.assertEqual(self.ev("id => document.getElementById(id + '_content').childElementCount", lid), 0)
        line.query_selector('.line-content').click()
        self.page.wait_for_timeout(200)
        shown, kids = self.ev("id => { const c = document.getElementById(id + '_content'); return [getComputedStyle(c).display, c.childElementCount]; }", lid)
        self.assertEqual(shown, 'block')
        self.assertGreater(kids, 0)

    def test_top_bar_and_filtered_count(self):
        bar = self.ev("(() => { const r = document.querySelector('.controls').getBoundingClientRect(); return [r.top, getComputedStyle(document.querySelector('.controls')).position]; })()")
        self.assertEqual(bar[1], 'sticky')
        self.assertLess(bar[0], 200)
        self.assertEqual(self.ev("getComputedStyle(document.getElementById('keyboardHint')).display"), 'none')
        self.page.fill('#filterInput', 'comm:sh')
        self.page.click('.filter-btn')
        self.page.wait_for_timeout(300)
        self.assertIn('Filtered:', self.ev("document.querySelector('.header-stats').innerText"))

    def test_page_header_stays_in_viewport_with_wide_trace(self):
        for width, height in [(1500, 900), (800, 600), (390, 844), (320, 700)]:
            self.page.set_viewport_size({'width': width, 'height': height})
            for view in ['text', 'timeline', 'compare']:
                with self.subTest(width=width, view=view):
                    self.ev('view => fgSwitchView(view)', view)
                    self.ev('window.scrollTo(0, 0)')
                    self.page.wait_for_timeout(100)
                    bounds = self.ev("""() => {
                        const header = document.querySelector('.page-header').getBoundingClientRect();
                        const actions = document.querySelector('.header-actions').getBoundingClientRect();
                        return [header.left, header.right, header.height, actions.right,
                                document.documentElement.clientWidth];
                    }""")
                    self.assertGreaterEqual(bounds[0], 0)
                    self.assertLessEqual(bounds[1], bounds[4] + 1)
                    self.assertLessEqual(bounds[3], bounds[4] + 1)
                    self.assertLessEqual(bounds[2], 80)
                    if view == 'text':
                        self.assertGreater(self.ev('document.documentElement.scrollWidth'), width)
                    else:
                        self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)

    def test_timeline_compact_layout_keeps_canvas_and_detail_reachable(self):
        self.open_timeline()
        self.ev("fgView('timeline').probe().select(0)")
        for width, height in [(1500, 900), (800, 600), (390, 844), (320, 700), (800, 420)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.ev('window.scrollTo(0, 0)')
                self.page.wait_for_timeout(100)
                bounds = self.ev("""() => {
                    const stage = document.querySelector('.tl-stage').getBoundingClientRect();
                    const detail = document.querySelector('.tl-detail');
                    const toolbar = document.querySelector('.tl-toolbar');
                    const notes = document.querySelector('.tl-notes');
                    return {stageHeight: stage.height, detailBottom: detail.getBoundingClientRect().bottom,
                            detailOverflow: detail.scrollWidth - detail.clientWidth,
                            toolbarOverflow: toolbar.scrollWidth - toolbar.clientWidth,
                            notesOverflow: notes.scrollWidth - notes.clientWidth,
                            pageWidth: document.documentElement.scrollWidth,
                            pageHeight: document.documentElement.scrollHeight};
                }""")
                self.assertGreaterEqual(bounds['stageHeight'], 160)
                self.assertLessEqual(bounds['detailBottom'], height + 1)
                self.assertLessEqual(bounds['detailOverflow'], 1)
                self.assertLessEqual(bounds['toolbarOverflow'], 1)
                self.assertLessEqual(bounds['notesOverflow'], 1)
                self.assertLessEqual(bounds['pageWidth'], width)
                self.assertLessEqual(bounds['pageHeight'], height)

    def test_timeline_compact_controls_resize_and_maximize_in_both_themes(self):
        self.open_timeline()
        self.ev("fgView('timeline').probe().select(0)")
        for theme in ['light', 'dark']:
            for width, height in [(320, 700), (800, 420)]:
                with self.subTest(theme=theme, width=width, height=height):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.click('[data-act=notes]')
                    self.assertFalse(self.page.locator('.tl-notes').is_visible())
                    self.page.click('[data-act=max]')
                    self.assertFalse(self.page.locator('.view-tabs').is_visible())
                    handle = self.page.locator('.tl-resizer').bounding_box()
                    center = handle['x'] + handle['width'] / 2
                    self.page.mouse.move(center, handle['y'] + 4)
                    self.page.mouse.down()
                    self.page.mouse.move(center, 0, steps=5)
                    self.page.mouse.up()
                    self.page.wait_for_timeout(100)
                    self.assertGreaterEqual(self.page.locator('.tl-stage').bounding_box()['height'], 160)
                    detail = self.page.locator('.tl-detail').bounding_box()
                    self.assertLessEqual(detail['y'] + detail['height'], height + 1)
                    self.page.dblclick('.tl-resizer')
                    self.page.click('[data-act=max]')
                    self.page.click('[data-act=notes]')
                    self.assertTrue(self.page.locator('.view-tabs').is_visible())
                    self.assertTrue(self.page.locator('.tl-notes').is_visible())

    def test_fold_hides_children(self):
        self.page.query_selector('.fold-entry .fold-icon').click()
        self.page.wait_for_timeout(200)
        hidden = self.ev("""() => { const e = document.querySelector('.fold-entry.folded'); const id = e.getAttribute('data-fold-id');
            const n = document.querySelectorAll('[data-fold-ancestors~="' + id + '"]');
            return Array.from(n).every(x => x.style.display === 'none') && n.length > 0; }""")
        self.assertTrue(hidden)

    def open_timeline(self):
        self.page.click('.view-tab[data-view=timeline]')
        self.page.wait_for_timeout(400)
        self.assertTrue(self.ev("document.body.dataset.view === 'timeline'"))

    def open_compare(self, rows=None, adjusted=None):
        if rows is not None:
            self.ev("""({rows, adjusted}) => {
                const node = document.getElementById('cmpData');
                const data = JSON.parse(node.textContent);
                data.rows = rows;
                data.rows_adj = adjusted;
                if (adjusted) data.overhead = [{child: 10, self: 5}, {child: 10, self: 5}];
                node.textContent = JSON.stringify(data);
            }""", {'rows': rows, 'adjusted': adjusted})
        self.page.click('.view-tab[data-view=compare]')
        self.page.wait_for_selector('.cmp-table')

    def open_range_data(self, names, mode='abs', starts=None, durations=None, parents=None, callers=None, extra=None, columns=None):
        count = len(names)
        strings = list(dict.fromkeys(names + ([caller for caller in callers if caller is not None] if callers else [])))
        string_ids = {name: index for index, name in enumerate(strings)}
        data = self.ev("JSON.parse(document.getElementById('tlData').textContent)")
        starts = starts if starts is not None else [index * 1000 for index in range(count)]
        durations = durations if durations is not None else [1000] * count
        data.update(n=count, mode=mode, strings=strings,
                    tasks=[dict(pid=1, comm='range-test', kind=0, cpu=0)],
                    errs=[], chains=[], offcpu=[], resched=[], wakeups=[], irqoff=[],
                    sections=[], atomic=[], inline={}, overhead=None)
        data['cols'] = dict(task=[0] * count, depth=[0] * count,
                            start=[start - (starts[index - 1] if index else 0)
                                   for index, start in enumerate(starts)],
                            dur=durations, name=[string_ids[name] for name in names],
                            parent=parents if parents is not None else [-1] * count,
                            line=list(range(1, count + 1)), flags=[0] * count,
                            ret=None, caller=[string_ids.get(caller, -1) for caller in callers] if callers else None,
                            args=None, module=None, cpu=None,
                            adj=[duration / 2 for duration in durations])
        if columns:
            data['cols'].update(columns)
        if extra:
            data.update(extra)
        self.ev("data => { document.getElementById('tlData').textContent = JSON.stringify(data); }", data)
        self.open_timeline()

    def drag_range(self):
        box = self.page.locator('.tl-canvas').bounding_box()
        self.page.keyboard.down('Shift')
        self.page.keyboard.down('Alt')
        self.page.mouse.move(box['x'] + box['width'] * 0.1, box['y'] + 30)
        self.page.mouse.down()
        self.page.mouse.move(box['x'] + box['width'] * 0.9, box['y'] + 30, steps=4)
        self.page.mouse.up()
        self.page.keyboard.up('Alt')
        self.page.keyboard.up('Shift')

    def test_window_panels_filter_overlapping_calls_and_adjust_self_not_positions(self):
        extra = {'resched': [[0, 0, 1000, 2, 1, 0], [0, 2000, 3000, 3, 0, 0]],
                 'sections': [[0, 0, 0, 1000, 0, 1, 0, 1, 2], [0, 0, 2000, 3000, 2, 2, 0, 3, 4],
                              [0, 1, 2000, 3000, 2, 2, 3, 3, 4]]}
        self.open_range_data(['inside', 'same_window', 'elsewhere'], starts=[0, 400, 2000],
                             durations=[400, 600, 1000], columns={'flags': [4, 0, 0]}, extra=extra)
        self.ev("fgNotifyFilter('inside only', new Set([1]))")
        for action, rows in [('latency', 'tr[data-win]'), ('sections', 'tr[data-sec]')]:
            with self.subTest(action=action):
                self.ev(f"fgView('timeline').probe().{action}()")
                self.assertEqual(self.page.locator(rows).count(), 1)
                self.assertIn('inside 400 ns', self.page.locator(rows).first.inner_text())
                self.assertNotIn('same_window', self.page.locator(rows).first.locator('td').nth(5).inner_text())
                raw = self.download_csv('[data-act=window-csv]')[0]
                self.page.click('[data-act=adj]')
                adjusted = self.download_csv('[data-act=window-csv]')[0]
                self.assertEqual(float(raw['top_1_self_estimate_ns']), 400)
                self.assertEqual(float(adjusted['top_1_self_estimate_ns']), 200)
                self.assertEqual(adjusted['self_duration_basis'], 'overhead_subtracted_estimate')
                self.assertEqual(raw['start_ns'], adjusted['start_ns'])
                self.assertEqual(raw['end_ns'], adjusted['end_ns'])
                self.assertEqual(raw['window_duration_ns'], adjusted['window_duration_ns'])
                self.assertEqual(raw['window_duration_basis'], 'timestamp_span')
                self.assertEqual(raw['filter'], 'inside only')
                if action == 'latency':
                    self.assertEqual(float(adjusted['irqoff_self_estimate_ns']), 200)
                else:
                    self.assertEqual(adjusted['ended_in'], 'same_window')
                    self.assertEqual(self.page.locator('[data-sk="0"]').inner_text(), 'irqs off (1)')
                    self.assertEqual(self.page.locator('[data-sk="1"]').inner_text(), 'preempt off (0)')
                self.page.click('[data-act=adj]')
        self.ev("fgNotifyFilter('no calls', new Set())")
        for action in ['latency', 'sections']:
            self.ev(f"fgView('timeline').probe().{action}()")
            self.assertIn('current filter', self.page.locator('.tl-detail').inner_text())
            self.assertTrue(self.page.locator('[data-act=window-csv]').is_disabled())

    def test_window_latency_overview_aggregates_functions_outside_each_top_three(self):
        names = ['a1', 'b1', 'c1', 'common', 'a2', 'b2', 'c2', 'common']
        starts = [0, 1100, 2200, 3300, 20000, 21100, 22200, 23300]
        self.open_range_data(names, starts=starts, durations=[1000, 900, 800, 700] * 2,
                             extra={'resched': [[0, 0, 5000, 0, 0, 0], [0, 20000, 25000, 0, 0, 0]]})
        self.ev("fgView('timeline').probe().latency()")
        self.assertEqual(self.page.locator('.an-blame .an-chip .k').first.inner_text(), 'common')
        self.assertIn('1.400 us', self.page.locator('.an-blame .an-chip').first.inner_text())
        self.assertNotIn('common', self.page.locator('tr[data-win]').first.inner_text())

    def test_window_panels_paginate_export_and_sort_without_reaggregating(self):
        count = 650
        extra = {'resched': [[0, index * 2000, index * 2000 + 1000, index % 4, 0, index % 3] for index in range(count)],
                 'sections': [[0, 0, index * 2000, index * 2000 + 1000, index, index, 0, index + 1, index + 1]
                              for index in range(count)]}
        self.open_range_data(['function'] * count, starts=[index * 2000 for index in range(count)], extra=extra)
        for action, selector, page_size, sort_attr in [('latency', 'tr[data-win]', 300, 'lsort'),
                                                     ('sections', 'tr[data-sec]', 200, 'ssort')]:
            with self.subTest(action=action):
                self.ev(f"fgView('timeline').probe().{action}()")
                self.assertEqual(self.page.locator(selector).count(), page_size)
                first = self.page.locator(selector).first.element_handle()
                self.page.click('[data-act=window-more]')
                self.assertTrue(first.evaluate('(node) => node.isConnected'))
                self.assertEqual(self.page.locator(selector).count(), page_size * 2)
                exported = self.download_csv('[data-act=window-csv]')
                self.assertEqual(len(exported), count)
                self.ev("""() => {
                    window.windowAggregateWrites = 0;
                    const originalSet = Map.prototype.set;
                    Map.prototype.set = function(key, value) {
                        if (typeof key === 'number' && typeof value === 'number') window.windowAggregateWrites++;
                        return originalSet.call(this, key, value);
                    };
                }""")
                self.page.locator(f'th[data-{sort_attr}=start] button').press('Enter')
                self.assertEqual(self.ev('window.windowAggregateWrites'), 0)
                self.assertEqual(self.page.locator(selector).count(), page_size)
                self.assertEqual(self.page.locator(f'th[data-{sort_attr}=start]').get_attribute('aria-sort'), 'ascending')
                while self.page.locator('[data-act=window-more]').is_enabled():
                    self.page.click('[data-act=window-more]')
                self.assertEqual(self.page.locator(selector).count(), count)
        self.ev("fgNotifyFilter('last call', new Set([650]))")
        self.assertEqual(self.page.locator('tr[data-sec]').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=window-csv]')), 1)

    def test_window_panels_sort_preempt_irq_top_end_and_task_columns(self):
        windows = [[0, 0, 1000, 1, 1, 2], [0, 2000, 3000, 3, 1, 0], [0, 4000, 5000, 2, 0, 1]]
        self.open_range_data(['first', 'second', 'third'], starts=[0, 2000, 4000], durations=[100, 200, 300],
                             columns={'flags': [4, 4, 0]},
                             extra={'resched': windows,
                                    'sections': [[0, kind, raw[1], raw[2], index, index, raw[3], index + 1, index + 1]
                                                 for kind in [0, 1] for index, raw in enumerate(windows)]})
        self.ev("fgView('timeline').probe().latency()")
        for key, order in [('pre', [1, 2, 0]), ('irq', [1, 0, 2]), ('top', [2, 1, 0]), ('end', [1, 2, 0]), ('task', [0, 1, 2])]:
            with self.subTest(key=key):
                self.page.locator(f'th[data-lsort={key}] button').click()
                self.assertTrue(self.page.locator(f'th[data-lsort={key}] button').evaluate('(node) => node === document.activeElement'))
                rows = self.download_csv('[data-act=window-csv]')
                self.assertEqual([int(row['window_index']) for row in rows], order)
        self.ev("fgView('timeline').probe().sections()")
        for key, order in [('top', [2, 1, 0]), ('start', [0, 1, 2]), ('task', [0, 1, 2]), ('dur', [0, 1, 2])]:
            with self.subTest(section_sort=key):
                self.page.locator(f'th[data-ssort={key}] button').press('Enter')
                self.assertTrue(self.page.locator(f'th[data-ssort={key}] button').evaluate('(node) => node === document.activeElement'))
                rows = self.download_csv('[data-act=window-csv]')
                self.assertEqual([int(row['section_index']) for row in rows], order)
        self.page.locator('[data-sk="1"]').press('Space')
        self.page.locator('th[data-ssort=pre] button').press('Enter')
        self.assertEqual([int(row['section_index']) for row in self.download_csv('[data-act=window-csv]')], [4, 5, 3])

    def test_window_overlap_index_keeps_long_calls_zero_boundaries_and_skips_ghosts(self):
        self.open_range_data(['long', 'short', 'later', 'point'], starts=[0, 500, 2000, 5000],
                             durations=[10000, 100, 100, 0],
                             extra={'resched': [[0, 5000, 6000, 0, 0, 0]],
                                    'sections': [[0, 0, 5000, 6000, 0, 0, 0, 1, 1]]})
        for action in ['latency', 'sections']:
            self.ev(f"fgView('timeline').probe().{action}()")
            self.assertEqual(float(self.download_csv('[data-act=window-csv]')[0]['top_1_self_estimate_ns']), 1000)
        self.ev("fgNotifyFilter('point', new Set([4]))")
        self.assertEqual(len(self.download_csv('[data-act=window-csv]')), 1)
        self.assertEqual(self.download_csv('[data-act=window-csv]')[0]['top_1_function'], '')
        self.ev("fgSwitchView('text')")
        self.page.reload()
        self.open_range_data(['parent', 'child'], starts=[0, 1000], durations=[10000, 1000], parents=[-1, 0],
                             columns={'depth': [0, 3]},
                             extra={'resched': [[0, 0, 10000, 0, 0, 0]]})
        self.assertGreater(self.ev("fgView('timeline').probe().NA"), 2)
        self.ev("fgView('timeline').probe().latency()")
        row = self.download_csv('[data-act=window-csv]')[0]
        self.assertEqual(float(row['top_1_self_estimate_ns']) + float(row['top_2_self_estimate_ns']), 10000)

    def test_window_panels_preserve_untraced_intervals_until_a_call_filter_is_applied(self):
        self.open_range_data(['elsewhere'], starts=[10000], extra={'resched': [[0, 0, 1000, 0, 0, 0]],
                             'sections': [[0, 0, 0, 1000, -1, -1, 0, 1, -1]]})
        for action in ['latency', 'sections']:
            self.ev(f"fgView('timeline').probe().{action}()")
            self.assertEqual(len(self.download_csv('[data-act=window-csv]')), 1)
        self.ev("fgNotifyFilter('elsewhere', new Set([1]))")
        self.assertEqual(self.page.locator('tr[data-sec]').count(), 0)
        self.assertEqual(self.page.locator('[data-sk="0"]').inner_text(), 'irqs off (0)')
        self.ev("fgView('timeline').probe().latency()")
        self.assertEqual(self.page.locator('tr[data-win]').count(), 0)

    def test_window_csv_units_quoting_and_compact_actions(self):
        for mode in ['abs', 'rel', 'packed', 'count']:
            if mode != 'abs':
                self.ev("fgSwitchView('text')")
                self.page.reload()
            self.open_range_data(['=SUM(1,2)'], mode=mode,
                                 extra={'resched': [[0, 0, 1000, 1, 0, 0]],
                                        'sections': [[0, 0, 0, 1000, 0, 0, 0, 1, 1]]})
            for action in ['latency', 'sections']:
                self.ev(f"fgView('timeline').probe().{action}()")
                row = self.download_csv('[data-act=window-csv]')[0]
                self.assertEqual(row['top_1_function'], "'=SUM(1,2)")
                self.assertEqual(row['time_mode'], mode)
                self.assertEqual(row['self_duration_basis'], 'call_order' if mode == 'count' else 'measured')
                self.assertEqual(row['window_duration_basis'], 'call_order' if mode == 'count' else 'packed_estimate' if mode == 'packed' else 'timestamp_span')
                if mode in ['count', 'packed']:
                    self.assertIn('not measured scheduling latency', self.page.locator('.tl-detail').inner_text())
                unit = 'units' if mode == 'count' else 'ns'
                self.assertEqual(float(row['window_duration_' + unit]), 1 if mode == 'count' else 1000)
                for width, height in [(390, 844), (320, 700), (800, 420), (1500, 900)]:
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.ev("document.querySelector('.tl-detail').scrollTop = 0")
                    self.page.wait_for_timeout(100)
                    for selector in ['[data-act=window-csv]', '.tl-close']:
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                        self.assertGreaterEqual(bounds['y'], 0)
                        self.assertLessEqual(bounds['y'] + bounds['height'], height)
                    self.ev("() => { const detail = document.querySelector('.tl-detail'); detail.scrollLeft = detail.scrollWidth; }")
                    for selector in ['[data-act=window-csv]', '[data-act=window-more]', '.tl-close']:
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    if width <= 640 or height <= 600:
                        self.assertEqual(self.page.locator('.an-window-head').evaluate('(node) => getComputedStyle(node).top'), 'auto')
                        self.assertTrue(self.page.locator('.an-window-table th').evaluate_all('(nodes) => nodes.every(node => getComputedStyle(node).top === "0px")'))
                    row = self.page.locator('tr[data-win], tr[data-sec]').first
                    row.scroll_into_view_if_needed()
                    bounds, detail = row.bounding_box(), self.page.locator('.tl-detail').bounding_box()
                    self.assertGreaterEqual(bounds['y'], detail['y'])
                    self.assertLessEqual(bounds['y'] + bounds['height'], detail['y'] + detail['height'])

    def test_window_latency_many_windows_does_not_spread_arguments(self):
        count = 150000
        self.open_range_data(['parent'], durations=[count * 1000],
                             extra={'resched': [[0, index * 1000, (index + 1) * 1000, 0, 0, 0] for index in range(count)]})
        self.ev("fgView('timeline').probe().latency()")
        self.assertEqual(self.page.locator('tr[data-win]').count(), 300)
        self.assertIn('150000', self.page.locator('.tl-detail-head').inner_text())

    def test_error_atomic_only_respects_filter_and_separates_callers(self):
        self.open_range_data(['worker', 'sleeper', 'other_worker', 'sleeper'], starts=[0, 100, 3000, 3100],
                             durations=[2000, 1000, 2000, 1000], parents=[-1, 0, -1, 2],
                             columns={'depth': [0, 1, 0, 1]}, extra={'atomic': [[1, 1], [3, 1]]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 2)
        self.assertIn('2 atomic calls', self.page.locator('.tl-detail-head').inner_text())
        self.assertTrue(self.page.locator('[data-act=errors-csv]').is_disabled())
        rows = self.download_csv('[data-act=atomic-csv]')
        self.assertEqual([row['caller'] for row in rows], ['worker', 'other_worker'])
        self.page.locator('tr.an-atomic').nth(1).click()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Errors')
        self.ev("fgNotifyFilter('one sleeper', new Set([2]))")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=atomic-csv]')), 1)
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 0)
        self.assertNotIn('2 atomic calls', self.page.locator('.tl-detail').inner_text())
        self.assertIn('current filter', self.page.locator('.tl-detail').inner_text())

    def test_error_occurrences_navigate_beyond_fifty_and_preserve_position(self):
        self.open_range_data(['origin'] * 60, extra={'chains': [[index, index, 2, index % 3, 1, 1, 0] for index in range(60)]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 1)
        self.ev("() => { for (let index = 0; index < 51; index++) document.querySelector('tr[data-chains]').click(); }")
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 50)
        self.assertIn('51 / 60', self.page.locator('tr[data-chains]').inner_text())
        self.page.locator('tr[data-chains] [data-error-step="1"]').press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 51)
        self.page.locator('tr[data-chains] [data-error-step="-1"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 50)
        self.page.click('[data-act=adj]')
        self.page.locator('th[data-esort=origin] button').press('Enter')
        self.assertIn('51 / 60', self.page.locator('tr[data-chains]').inner_text())
        self.page.locator('tr[data-chains] [data-error-step="1"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 51)
        self.assertEqual(len(self.download_csv('[data-act=error-occurrences-csv]')), 60)
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Errors')

    def test_error_entry_classification_does_not_merge_syscalls_with_ordinary_roots(self):
        self.open_range_data(['open', 'origin', '__x64_sys_open', 'origin'], starts=[0, 100, 3000, 3100],
                             durations=[2000, 1000, 2000, 1000], parents=[-1, 0, -1, 2],
                             columns={'depth': [0, 1, 0, 1]},
                             extra={'chains': [[1, 1, 2, 0, 1, 1, 0], [3, 3, 2, 1, 2, 0, 0]]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 2)
        self.assertEqual(self.page.locator('tr[data-chains] .tl-pill').count(), 1)
        rows = self.download_csv('[data-act=errors-csv]')
        self.assertEqual([row['entry'] for row in rows], ['open', 'open'])
        self.assertEqual([int(row['entry_is_syscall']) for row in rows], [0, 1])
        self.assertEqual([int(row['unverified_chains']) for row in rows], [0, 1])

    def test_error_all_columns_sort_real_fates_tasks_and_chain_lengths(self):
        names = ['entryA', 'entryB', 'entryC', 'entryB', 'entryC', 'entryC']
        tasks = [dict(pid=index + 1, comm='task' + str(index), kind=0, cpu=0) for index in range(3)]
        fates, lengths, errnos = [0, 1, 2, 1, 2, 2], [4, 2, 3, 2, 3, 3], [22, 5, 2, 5, 2, 2]
        self.open_range_data(names, columns={'task': [0, 0, 0, 1, 1, 2]},
                             extra={'tasks': tasks, 'chains': [[index, index, errnos[index], fates[index], lengths[index], 1, 0] for index in range(6)]})
        self.ev("fgView('timeline').probe().errors()")
        for key, expected in [('h', ['entryA', 'entryB', 'entryC']), ('user', ['entryB', 'entryA', 'entryC']),
                              ('t', ['entryC', 'entryB', 'entryA']), ('x', ['entryA', 'entryC', 'entryB']),
                              ('unknown', ['entryC', 'entryA', 'entryB']), ('errno', ['entryC', 'entryB', 'entryA']),
                              ('sys', ['entryA', 'entryB', 'entryC']), ('origin', ['entryA', 'entryB', 'entryC'])]:
            with self.subTest(key=key):
                self.page.locator(f'th[data-esort={key}] button').press('Enter')
                self.assertTrue(self.page.locator(f'th[data-esort={key}] button').evaluate('(node) => node === document.activeElement'))
                rows = self.download_csv('[data-act=errors-csv]')
                self.assertEqual([row['origin'] for row in rows], expected)
                self.assertTrue(all(int(row['chains']) == int(row['handled']) + int(row['to_user']) + int(row['unknown']) for row in rows))
        self.ev("fgNotifyFilter('last origin', new Set([6]))")
        row = self.download_csv('[data-act=errors-csv]')[0]
        self.assertEqual((int(row['chains']), int(row['unknown']), int(row['tasks'])), (1, 1, 1))

    def test_error_groups_paginate_export_all_and_reuse_counts(self):
        count = 650
        self.open_range_data(['origin_' + str(index).zfill(4) for index in range(count)],
                             extra={'chains': [[index, index, 2, 0, 1, 1, 0] for index in range(count)]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 200)
        first = self.page.locator('tr[data-chains]').first.element_handle()
        self.page.locator('[data-act=errors-more]').press('Enter')
        self.assertTrue(self.page.locator('[data-act=errors-more]').evaluate('(node) => node === document.activeElement'))
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 400)
        self.assertEqual(len(self.download_csv('[data-act=errors-csv]')), count)
        occurrences = self.download_csv('[data-act=error-occurrences-csv]')
        self.assertEqual(len(occurrences), count)
        self.assertEqual(int(occurrences[-1]['origin_line']), count)
        writes = self.ev("""() => {
            let writes = 0;
            const original = Set.prototype.add;
            Set.prototype.add = function(value) { if (typeof value === 'number') writes++; return original.call(this, value); };
            document.querySelector('th[data-esort=origin] button').click();
            document.querySelector('[data-act=adj]').click();
            Set.prototype.add = original;
            return writes;
        }""")
        self.assertEqual(writes, 0)
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 200)
        while self.page.locator('[data-act=errors-more]').is_enabled():
            self.page.click('[data-act=errors-more]')
        self.assertEqual(self.page.locator('tr[data-chains]').count(), count)
        self.ev("fgNotifyFilter('last origin', new Set([650]))")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=errors-csv]')), 1)

    def test_error_atomic_groups_paginate_navigate_and_export_every_call(self):
        names = ['sleeper'] * 60 + ['sleeper_' + str(index) for index in range(250)]
        self.open_range_data(names, extra={'atomic': [[index, 1] for index in range(len(names))]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 100)
        first = self.page.locator('tr.an-atomic').first.element_handle()
        self.page.locator('tr.an-atomic').first.locator('[data-error-step="-1"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 59)
        self.ev("() => { for (let index = 0; index < 52; index++) document.querySelector('tr.an-atomic').click(); }")
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 51)
        self.page.locator('[data-act=atomic-more]').press('Enter')
        self.assertTrue(self.page.locator('[data-act=atomic-more]').evaluate('(node) => node === document.activeElement'))
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 200)
        rows = self.download_csv('[data-act=atomic-csv]')
        self.assertEqual(len(rows), len(names))
        self.assertEqual(len({row['slice_index'] for row in rows}), len(names))
        self.page.click('[data-act=atomic-more]')
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 251)
        self.ev("fgNotifyFilter('last atomic', new Set([310]))")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=atomic-csv]')), 1)

    def test_error_minor_toggle_is_reachable_in_filtered_empty_state(self):
        self.open_range_data(['major', 'minor', 'minor'],
                             extra={'chains': [[0, 0, 2, 1, 2, 1, 0], [1, 1, 2, 0, 1, 1, 1], [2, 2, 2, 0, 1, 0, 1]]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(len(self.download_csv('[data-act=error-occurrences-csv]')), 1)
        self.assertIn('Show 2 minor', self.page.locator('.an-minor').inner_text())
        self.ev("fgNotifyFilter('minor only', new Set([2, 3]))")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 0)
        self.assertTrue(self.page.locator('[data-act=errors-csv]').is_disabled())
        self.page.locator('[data-act=minor]').press('Space')
        self.assertTrue(self.page.locator('[data-act=minor]').is_checked())
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 1)
        rows = self.download_csv('[data-act=error-occurrences-csv]')
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['minor'] == '1' and row['include_minor'] == '1' for row in rows))
        self.page.locator('[data-act=minor]').press('Space')
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 0)

    def test_error_csv_units_adjusted_geometry_quoting_and_compact_controls(self):
        for mode in ['abs', 'count']:
            if mode != 'abs':
                self.ev("fgSwitchView('text')")
                self.page.reload()
            self.open_range_data(['=SUM(1,2)'], mode=mode,
                                 extra={'chains': [[0, 0, 2, 2, 1, 0, 0]], 'atomic': [[0, 1]]})
            self.ev("fgView('timeline').probe().errors()")
            row = self.download_csv('[data-act=error-occurrences-csv]')[0]
            unit = 'units' if mode == 'count' else 'ns'
            self.assertEqual(row['origin'], "'=SUM(1,2)")
            self.assertEqual(row['fate'], 'unknown')
            self.assertEqual(row['time_mode'], mode)
            self.assertEqual(float(row['origin_duration_' + unit]), 1 if mode == 'count' else 1000)
            if mode == 'abs':
                self.page.click('[data-act=adj]')
                adjusted = self.download_csv('[data-act=error-occurrences-csv]')[0]
                self.assertEqual(float(adjusted['origin_duration_ns']), 500)
                self.assertEqual(adjusted['duration_basis'], 'overhead_subtracted_estimate')
                self.assertEqual(row['origin_start_ns'], adjusted['origin_start_ns'])
                self.assertEqual(row['origin_end_ns'], adjusted['origin_end_ns'])
                self.assertEqual(float(self.download_csv('[data-act=atomic-csv]')[0]['duration_ns']), 500)
            else:
                self.assertEqual(row['duration_basis'], 'call_order')
            for width, height in [(320, 700), (390, 844), (800, 420), (1500, 900)]:
                self.page.set_viewport_size({'width': width, 'height': height})
                self.ev("() => { const detail = document.querySelector('.tl-detail'); detail.scrollTop = 0; detail.scrollLeft = 0; }")
                self.page.wait_for_timeout(100)
                for selector in ['[data-act=errors-csv]', '.tl-close']:
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.assertGreaterEqual(bounds['y'], 0)
                    self.assertLessEqual(bounds['y'] + bounds['height'], height)
                self.ev("() => { const detail = document.querySelector('.tl-detail'); detail.scrollLeft = detail.scrollWidth; }")
                for selector in ['[data-act=errors-csv]', '[data-act=error-occurrences-csv]', '[data-act=atomic-csv]', '.tl-close']:
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                if width <= 640 or height <= 600:
                    self.assertEqual(self.page.locator('.an-error-head').evaluate('(node) => getComputedStyle(node).top'), 'auto')
                    self.assertTrue(self.page.locator('.tl-detail table.tl-sum th').evaluate_all('(nodes) => nodes.every(node => getComputedStyle(node).top === "0px")'))

    def test_range_replaces_analysis_state_and_resets_search_position(self):
        self.open_timeline()
        total = self.ev("fgView('timeline').probe().N")
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Enter')
        for action in ('an-errors', 'an-lat', 'an-sec'):
            with self.subTest(action=action):
                self.page.click(f'[data-act={action}]')
                self.drag_range()
                self.assertTrue(self.page.locator('.tl-detail-title').inner_text().startswith('Range '))
                self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{total} calls')
                self.ev("fgNotifyFilter('', null)")
                self.assertTrue(self.page.locator('.tl-detail-title').inner_text().startswith('Range '))

    def test_analysis_highlights_sync_search_without_replacing_panel(self):
        self.open_timeline()
        total = self.ev("fgView('timeline').probe().N")
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Enter')
        self.page.click('[data-act=an-errors]')
        self.page.locator('tr[data-chains]').first.click()
        selected = self.ev("fgView('timeline').probe().sel()")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{selected + 1} / {total} calls')
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Errors')
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertTrue(self.page.locator('.tl-detail.show').is_visible())
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Errors')
        self.ev("fgNotifyFilter('', null)")
        self.ev("() => { const probe = fgView('timeline').probe(); probe.dist(probe.sName[19]); }")
        title = self.page.locator('.tl-detail-title').inner_text()
        self.page.locator('.an-list tr[data-slice]').last.click()
        selected = self.ev("fgView('timeline').probe().sel()")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{selected + 1} / {total} calls')
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), title)

    def test_wait_panel_survives_refresh_with_retained_range(self):
        self.open_timeline()
        self.ev("fgView('timeline').probe().range(0, 1000000)")
        self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS.find(track => track.off.length).off[0]); }")
        self.ev("fgNotifyFilter('', null)")
        self.assertTrue(self.page.locator('.tl-detail-title').inner_text().startswith('Wait chain'))

    def open_wait_data(self, count=125, mode='abs', offcpu=None, wakeups=None, caps=None, task_names=None, task_kinds=None):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        names = task_names or ['waiter', '<b>source</b>']
        tasks = [dict(pid=index + 1, comm=name, kind=task_kinds[index] if task_kinds else 0, cpu=index)
                 for index, name in enumerate(names)]
        records = offcpu if offcpu is not None else [
            [index % len(tasks), (count - index) * 3000, (count - index) * 3000 + 1000,
             0, 0, -1, 0, 1, 0, -1] for index in range(count)]
        wakes = wakeups if wakeups is not None else [
            [(index + 1) % len(tasks), record[2] - 100, record[0], 0, -1, index]
            for index, record in enumerate(records[:-1])]
        extra = dict(tasks=tasks, offcpu=records, wakeups=wakes)
        if caps is not None:
            extra['caps'] = caps
        self.open_range_data(['waiting_function'] * len(tasks), mode=mode,
                             durations=[max(count * 4000, 1000000)] * len(tasks),
                             columns={'task': list(range(len(tasks)))}, extra=extra)
        self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[0].off[0]); }")

    def test_wait_chain_escapes_task_names_and_keeps_observed_root_without_sched(self):
        self.open_wait_data(count=2, caps={'sched_events': False})
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 2)
        self.assertEqual(self.page.locator('tr[data-wait="0"] td').nth(3).locator('b').count(), 0)
        self.assertIn('<b>source</b>', self.page.locator('tr[data-wait="0"] td').nth(3).inner_text())
        self.assertIn('temporal', self.page.locator('.an-wait-hint').inner_text())
        self.open_wait_data(count=1, wakeups=[], caps={'sched_events': False})
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 1)
        self.assertIn('fgcapture.sh --sched', self.page.locator('.an-wait-hint').inner_text())

    def test_wait_chain_many_intervals_resolves_only_requested_pages(self):
        self.open_wait_data(count=150000)
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 100)
        self.assertIn('Showing 100+', self.page.locator('.an-wait-footer').inner_text())
        self.page.locator('[data-act=wait-more]').press('Enter')
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 200)
        self.assertIn('Showing 200+', self.page.locator('.an-wait-footer').inner_text())
        self.assertEqual(self.page.locator('tr[data-wait]').last.get_attribute('data-wait'), '199')

    def test_wait_chain_paginates_exports_and_refreshes_without_rewalking(self):
        self.open_wait_data(count=225)
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 100)
        first = self.page.locator('tr[data-wait]').first.element_handle()
        rows = self.download_csv('[data-act=wait-csv]')
        self.assertEqual(len(rows), 225)
        self.assertEqual(rows[0]['link_kind'], 'selected')
        self.assertEqual(rows[1]['link_kind'], 'temporal')
        self.assertEqual(int(rows[1]['prior_resume_to_wake_ns']), 2900)
        self.assertEqual(rows[-1]['terminal_reason'], 'wakeup-not-recorded')
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 100)
        self.page.locator('[data-act=wait-more]').press('Enter')
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 200)
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertTrue(self.page.locator('[data-act=wait-more]').evaluate('(node) => node === document.activeElement'))
        self.page.locator('[data-act=wait-more]').press('Enter')
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 225)
        self.assertTrue(self.page.locator('[data-act=wait-more]').is_disabled())
        writes = self.ev("""() => {
            let writes = 0;
            const original = Set.prototype.add;
            Set.prototype.add = function(value) { if (typeof value === 'number') writes++; return original.call(this, value); };
            fgNotifyFilter('no calls', new Set());
            document.querySelector('[data-act=adj]').click();
            Set.prototype.add = original;
            return writes;
        }""")
        self.assertEqual(writes, 0)
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 225)
        self.assertEqual(self.download_csv('[data-act=wait-csv]'), rows)

    def test_wait_chain_keyboard_navigation_preserves_panel(self):
        self.open_wait_data(count=3)
        self.page.locator('.an-wait-hint summary').press('Enter')
        self.assertTrue(self.page.locator('.an-wait-hint details').evaluate('(node) => node.open'))
        self.assertIn('Overhead', self.page.locator('.an-wait-hint').inner_text())
        self.page.locator('tr[data-wait="1"] button').press('Enter')
        self.assertTrue(self.page.locator('.tl-detail-title').inner_text().startswith('Wait chain'))
        start, end = self.ev("fgView('timeline').probe().view()")
        self.assertLessEqual(start, 6000)
        self.assertGreaterEqual(end, 7000)
        self.page.locator('tr[data-wait="2"] button').press('Space')
        self.assertTrue(self.page.locator('.tl-detail.show').is_visible())

    def test_wait_chain_reports_all_termination_reasons_and_gap_boundary(self):
        root = [0, 70000000, 80000000, 0, 0, -1, 0, 1, 0, -1]
        prior = [1, 24000000, 25000000, 0, 0, -1, 0, 1, 0, -1]
        cases = [
            ('boundary', [root, prior], [[1, 75000000, 0, 0, -1, 0]], 2, 'wakeup-not-recorded'),
            ('gap', [root, prior], [[1, 75000001, 0, 0, -1, 0]], 1, 'gap-exceeded'),
            ('interrupt', [root, prior], [[1, 75000000, 0, 2, -1, 0]], 1, 'interrupt-context'),
            ('no prior', [root], [[1, 75000000, 0, 0, -1, 0]], 1, 'no-prior-wait'),
            ('missing', [root], [], 1, 'wakeup-not-recorded'),
            ('cycle', [[0, 0, 0, 0, 0, -1, 0, 1, 0, -1]], [[0, 0, 0, 0, -1, 0]], 1, 'cycle'),
        ]
        for label, records, wakes, length, reason in cases:
            with self.subTest(label=label):
                self.open_wait_data(offcpu=records, wakeups=wakes)
                self.assertEqual(self.page.locator('tr[data-wait]').count(), length)
                self.assertIn('Stopped:', self.page.locator('.an-wait-footer').inner_text())
                self.assertEqual(self.download_csv('[data-act=wait-csv]')[-1]['terminal_reason'], reason)
        self.open_wait_data(count=2, task_kinds=[0, 1])
        self.assertEqual(self.download_csv('[data-act=wait-csv]')[0]['terminal_reason'], 'non-task-context')

    def test_wait_chain_indexes_unordered_ends_stable_ties_and_excludes_open_waits(self):
        records = [[0, 9000, 10000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, 6000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, 8000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, 7000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, 8000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, None, 0, 0, -1, 0, -1, 0, -1]]
        self.open_wait_data(offcpu=records, wakeups=[[0, 9200, 0, 0, -1, 0], [1, 9500, 0, 0, -1, 0],
                                                   [0, 900000, 1, 2, -1, 5]])
        self.assertEqual(self.page.locator('tr[data-wait]').evaluate_all('(rows) => rows.map(row => +row.dataset.wait)'), [0, 2])
        self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[1].off[4]); }")
        row = self.download_csv('[data-act=wait-csv]')[0]
        self.assertEqual(row['end_status'], 'trace_end_lower_bound')
        self.assertEqual(row['wait_end_ns'], '')
        self.assertEqual(row['wake_latency_ns'], '')
        self.assertEqual(row['resume_cpu'], '')
        self.assertIn('≥', self.page.locator('tr[data-wait]').inner_text())

    def test_wait_chain_csv_formula_protection_and_count_mode_units(self):
        for mode in ['abs', 'count']:
            with self.subTest(mode=mode):
                self.open_wait_data(count=2, mode=mode, task_names=['=SUM(1,2)', '@source'])
                rows = self.download_csv('[data-act=wait-csv]')
                self.assertEqual(rows[0]['comm'], "'=SUM(1,2)")
                self.assertEqual(rows[0]['waker_comm'], "'@source")
                self.assertEqual(rows[0]['filter_scope'], 'all_tasks')
                self.assertEqual(rows[0]['duration_basis'], 'call_order' if mode == 'count' else 'recorded_timestamps')
                if mode == 'count':
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]['waited_units'], '1')
                    self.assertEqual(rows[0]['terminal_reason'], 'no-timestamps')
                    self.assertFalse(any(name.endswith('_ns') for name in rows[0]))
                    self.assertEqual(self.page.locator('.an-wait-table .err').count(), 0)
                    self.assertNotIn('ms', self.page.locator('.an-wait-hint').inner_text())

    def test_wait_chain_compact_layout_keeps_controls_and_rows_reachable(self):
        self.open_wait_data(count=3, task_names=['very_long_task_name_' * 20, 'source'])
        for theme in ['light', 'dark']:
            for width, height in [(1500, 900), (320, 700), (800, 420)]:
                with self.subTest(theme=theme, width=width):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.ev("() => { const panel = document.querySelector('.tl-detail'); panel.scrollTop = 0; panel.scrollLeft = panel.scrollWidth; }")
                    self.page.wait_for_timeout(100)
                    bounds = self.ev("""() => {
                        const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                        const close = document.querySelector('.an-wait-head .tl-close').getBoundingClientRect();
                        const head = document.querySelector('.an-wait-head');
                        return {left: panel.left, right: panel.right, bottom: panel.bottom,
                                closeLeft: close.left, closeRight: close.right, closeBottom: close.bottom,
                                top: getComputedStyle(head).top, tableTop: document.querySelector('.an-wait-table th').style.top,
                                pageWidth: document.documentElement.scrollWidth};
                    }""")
                    self.assertGreaterEqual(bounds['closeLeft'], bounds['left'])
                    self.assertLessEqual(bounds['closeRight'], bounds['right'])
                    self.assertLessEqual(bounds['closeBottom'], bounds['bottom'])
                    self.assertLessEqual(bounds['pageWidth'], width)
                    self.assertLessEqual(self.ev("document.querySelector('tr[data-wait]').offsetHeight"), 48)
                    self.assertGreaterEqual(self.ev("document.querySelector('tr[data-wait] button').offsetWidth"), 140)
                    if width <= 640 or height <= 600:
                        self.assertEqual(bounds['top'], 'auto')
                        self.assertEqual(bounds['tableTop'], '0px')
                    self.assertFalse(self.page.locator('.an-wait-hint details').evaluate('(node) => node.open'))
                    self.page.locator('tr[data-wait="2"] button').press('Enter')
                    self.assertTrue(self.page.locator('.tl-detail.show').is_visible())
        self.page.locator('.an-wait-head .tl-close').click()
        self.assertFalse(self.page.locator('.tl-detail').is_visible())


    def test_range_pagination_sorting_and_cache(self):
        self.open_range_data([f'function_{index:04d}' for index in range(650)])
        self.ev("fgView('timeline').probe().range(0, 650000)")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 300)
        first = self.page.locator('.tl-range-table tbody tr').first.element_handle()
        self.page.locator('[data-act=range-more]').click()
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 600)
        self.ev("""() => {
            const originalSet = Map.prototype.set;
            window.rangeAggregationWrites = 0;
            Map.prototype.set = function(key, value) {
                if (value && typeof value.calls === 'number' && typeof value.incl === 'number') window.rangeAggregationWrites++;
                return originalSet.call(this, key, value);
            };
        }""")
        self.page.locator('.tl-range-table th[data-sort=name] button').click()
        self.assertEqual(self.ev('window.rangeAggregationWrites'), 0)
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 300)
        self.assertEqual(self.page.locator('.tl-range-table th[data-sort=name]').get_attribute('aria-sort'), 'ascending')
        self.page.locator('[data-act=range-more]').click()
        self.page.locator('[data-act=range-more]').click()
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 650)
        self.assertTrue(self.page.locator('[data-act=range-more]').is_disabled())
        self.ev("fgNotifyFilter('one call', new Set([650]))")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 1)
        self.assertEqual(self.page.locator('.tl-range-table tbody tr').first.get_attribute('data-name'), 'function_0649')
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertIn('No calls overlap', self.page.locator('.tl-detail').inner_text())
        self.assertTrue(self.page.locator('[data-act=range-csv]').is_disabled())

    def test_range_many_distinct_functions_does_not_spread_arguments(self):
        self.open_range_data([f'function_{index}' for index in range(150000)])
        self.ev("fgView('timeline').probe().range(0, 150000000)")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 300)
        self.assertIn('150000 functions', self.page.locator('.tl-range-footer').inner_text())

    def download_csv(self, selector):
        with self.page.expect_download() as download_info:
            self.page.click(selector)
        with open(download_info.value.path(), encoding='utf-8-sig', newline='') as source:
            return list(csv.DictReader(source))

    def download_range_csv(self):
        return self.download_csv('[data-act=range-csv]')

    def open_distribution(self):
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.wait_for_selector('.an-dist-tools')

    def test_distribution_call_sites_remain_reversible_and_page_without_resorting(self):
        self.open_range_data(['target'] * 80, callers=[f'caller_{index % 20:02d}' for index in range(80)])
        self.open_distribution()
        self.assertEqual(self.page.locator('tr[data-site]').count(), 12)
        first = self.page.locator('tr[data-site]').first.element_handle()
        self.page.click('[data-act=dist-sites-more]')
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('tr[data-site]').count(), 20)
        self.ev("""() => {
            window.distributionSortLengths = [];
            const originalSort = Array.prototype.sort;
            Array.prototype.sort = function(...args) {
                window.distributionSortLengths.push(this.length);
                return originalSort.apply(this, args);
            };
        }""")
        self.page.locator('tr[data-site] button').first.press('Enter')
        self.assertEqual(self.page.locator('tr[data-site] button[aria-pressed=true]').count(), 1)
        self.assertIn('4 of 4 calls', self.page.locator('.an-dist-count').inner_text())
        self.page.locator('tr[data-site] button[aria-pressed=true]').press('Space')
        self.assertIn('15 of 80 calls', self.page.locator('.an-dist-count').inner_text())
        self.assertEqual(self.ev('window.distributionSortLengths'), [])

    def test_distribution_empty_site_can_be_cleared_after_filtering(self):
        self.open_range_data(['target'] * 8, callers=['caller_a'] * 4 + ['caller_b'] * 4)
        self.open_distribution()
        self.page.locator('tr[data-site="caller_a"] button').click()
        self.ev("fgNotifyFilter('only b', new Set([5, 6, 7, 8]))")
        self.assertEqual(self.page.locator('.an-list tr').count(), 0)
        self.assertTrue(self.page.locator('[data-act=dist-csv]').is_disabled())
        self.assertIn('No calls', self.page.locator('.tl-detail').inner_text())
        self.assertEqual(self.page.locator('.an-dist .tl-kv .v').all_inner_texts(), ['—'] * 6)
        self.page.click('[data-act=dist-reset-site]')
        self.assertEqual(self.page.locator('.an-list tr').count(), 4)
        rows = self.download_csv('[data-act=dist-csv]')
        self.assertEqual({row['caller'] for row in rows}, {'caller_b'})
        self.assertEqual({row['filter'] for row in rows}, {'only b'})

    def test_distribution_loads_and_exports_every_call_not_just_initial_rows(self):
        self.open_range_data(['=SUM(1,2)'] * 320, durations=list(range(1000, 1320)),
                             callers=['\tcaller,"\n函数'] * 320)
        self.open_distribution()
        self.assertEqual(self.page.locator('.an-list tr').count(), 15)
        first = self.page.locator('.an-list tr').first.element_handle()
        self.page.click('[data-act=dist-more]')
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('.an-list tr').count(), 65)
        rows = self.download_csv('[data-act=dist-csv]')
        self.assertEqual(len(rows), 320)
        self.assertEqual([int(row['duration_ns']) for row in rows], list(range(1319, 999, -1)))
        self.assertEqual({row['function'] for row in rows}, {"'=SUM(1,2)"})
        self.assertEqual({row['caller'] for row in rows}, {"'\tcaller,\"\n函数"})
        self.assertEqual({row['duration_basis'] for row in rows}, {'measured'})
        self.assertEqual([int(row['trace_line']) for row in rows], list(range(320, 0, -1)))
        while self.page.locator('[data-act=dist-more]').is_enabled():
            self.page.click('[data-act=dist-more]')
        self.assertEqual(self.page.locator('.an-list tr').count(), 320)

    def test_distribution_histogram_bucket_filters_list_and_export_then_resets(self):
        self.open_range_data(['target'] * 240, durations=[10 ** (index // 60 + 1) for index in range(240)])
        self.open_distribution()
        options = self.page.locator('[data-act=dist-bucket] option').evaluate_all('(nodes) => nodes.map(node => node.value)')
        self.assertEqual(len(options), 5)
        last = options[-1]
        self.page.select_option('[data-act=dist-bucket]', last)
        self.assertIn('50 of 60 calls', self.page.locator('.an-dist-count').inner_text())
        self.page.click('[data-act=dist-more]')
        self.assertEqual(self.page.locator('.an-list tr').count(), 60)
        rows = self.download_csv('[data-act=dist-csv]')
        self.assertEqual(len(rows), 60)
        for row in rows:
            self.assertEqual(float(row['duration_ns']), 10000)
            self.assertGreaterEqual(float(row['duration_ns']), float(row['bucket_lower_ns']))
            self.assertLess(float(row['duration_ns']), float(row['bucket_upper_ns']))
        self.page.click('[data-act=dist-reset-bucket]')
        self.assertIn('15 of 240 calls', self.page.locator('.an-dist-count').inner_text())
        self.page.focus('[data-act=dist-bucket]')
        self.page.keyboard.press('End')
        self.page.keyboard.press('Enter')
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), last)
        self.page.click('[data-act=dist-reset-bucket]')
        canvas = self.page.locator('.an-hist')
        canvas.scroll_into_view_if_needed()
        bounds = canvas.bounding_box()
        bucket_count = int(last) + 1
        self.page.mouse.click(bounds['x'] + 40 + (int(last) + 0.5) * (bounds['width'] - 48) / bucket_count,
                              bounds['y'] + 50)
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), last)
        self.page.click('[data-act=adj]')
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), '-1')
        self.assertEqual(len(self.download_csv('[data-act=dist-csv]')), 240)
        self.page.select_option('[data-act=dist-bucket]', self.page.locator('[data-act=dist-bucket] option').last.get_attribute('value'))
        self.ev("fgNotifyFilter('first group', new Set(Array.from({length: 60}, (_, index) => index + 1)))")
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), '-1')
        self.assertEqual(len(self.download_csv('[data-act=dist-csv]')), 60)

    def test_distribution_quantiles_sites_filter_and_adjustment_share_one_population(self):
        self.open_range_data(['target'] * 4, durations=[1000, 2000, 5000, 10000], callers=['a', 'a', 'b', 'b'])
        self.open_distribution()
        values = self.ev("""() => Object.fromEntries([...document.querySelector('.an-dist .tl-kv').children]
            .filter(node => node.classList.contains('k')).map(node => [node.textContent, node.nextElementSibling.textContent]))""")
        self.assertEqual(values['p50'], '5.000 us')
        self.assertEqual(values['mean'], '4.500 us')
        self.assertEqual(self.page.locator('tr[data-site="a"] td').nth(2).inner_text(), '2.000 us')
        self.page.locator('tr[data-site="a"] button').click()
        self.page.click('[data-act=adj]')
        adjusted = self.download_csv('[data-act=dist-csv]')
        self.assertEqual([float(row['duration_ns']) for row in adjusted], [1000, 500])
        self.assertEqual([(float(row['start_ns']), float(row['end_ns'])) for row in adjusted], [(1000, 3000), (0, 1000)])
        self.assertEqual({row['duration_basis'] for row in adjusted}, {'overhead_subtracted_estimate'})
        self.assertEqual({row['call_site_filter'] for row in adjusted}, {'a'})
        self.ev("fgNotifyFilter('one call', new Set([1]))")
        self.assertEqual(len(self.download_csv('[data-act=dist-csv]')), 1)
        self.page.click('[data-act=adj]')
        self.assertEqual(float(self.download_csv('[data-act=dist-csv]')[0]['duration_ns']), 1000)

    def test_distribution_exports_truthful_units_in_each_time_mode(self):
        for mode in ('abs', 'rel', 'compressed', 'count'):
            with self.subTest(mode=mode):
                if mode != 'abs':
                    self.ev("fgSwitchView('text')")
                    self.page.reload()
                self.open_range_data(['target'] * 2, mode=mode)
                self.open_distribution()
                row = self.download_csv('[data-act=dist-csv]')[0]
                self.assertEqual(row['time_mode'], mode)
                if mode == 'count':
                    self.assertNotIn('duration_ns', row)
                    self.assertEqual(float(row['duration_units']), 1)
                    self.assertEqual(row['duration_basis'], 'call_order')
                else:
                    self.assertEqual(float(row['duration_ns']), 1000)
                    self.assertEqual(row['duration_basis'], 'measured')

    def test_distribution_histogram_resizes_and_repaints_after_theme_and_view_changes(self):
        self.open_range_data(['long_function_' * 15] * 4, callers=['long_caller_' * 20] * 2 + ['other'] * 2,
                             durations=[1000, 2000, 5000, 10000])
        self.open_distribution()
        self.page.locator('tr[data-site] button').first.click()
        for width, height in [(390, 844), (320, 700), (1500, 900)]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_timeout(150)
                dimensions = self.ev("""() => {
                    const canvas = document.querySelector('.an-hist');
                    const bounds = canvas.getBoundingClientRect();
                    return [bounds.width, canvas.width, bounds.right, window.devicePixelRatio];
                }""")
                self.assertLessEqual(dimensions[2], width)
                self.assertLessEqual(abs(dimensions[1] - dimensions[0] * dimensions[3]), 1)
                close = self.page.locator('.tl-close').bounding_box()
                self.assertLessEqual(close['x'] + close['width'], width)
                self.assertLessEqual(self.page.locator('.an-dist-head').bounding_box()['height'], 160)
        before = self.ev("document.querySelector('.an-hist').toDataURL()")
        self.ev('toggleTheme()')
        self.page.wait_for_timeout(100)
        after = self.ev("document.querySelector('.an-hist').toDataURL()")
        self.assertNotEqual(before, after)
        self.ev("fgSwitchView('compare')")
        self.ev('toggleTheme()')
        self.ev("fgSwitchView('timeline')")
        self.page.wait_for_timeout(100)
        self.assertEqual(before, self.ev("document.querySelector('.an-hist').toDataURL()"))

    def test_range_csv_exports_all_rows_safely_in_sort_order(self):
        names = ['=SUM(1,2)', '+cmd', '-cmd', '@cmd', '\tcmd', ' quoted,"\n函数']
        names += [f'function_{index:04d}' for index in range(650)]
        self.open_range_data(names)
        self.ev("fgView('timeline').probe().range(0, 656000)")
        self.page.locator('.tl-range-table th[data-sort=name] button').click()
        rows = self.download_range_csv()
        self.assertEqual(len(rows), len(names))
        expected = ["'" + name if name in names[:5] else name for name in sorted(names)]
        self.assertEqual([row['function'] for row in rows], expected)
        self.assertEqual(rows[0]['duration_basis'], 'measured')
        self.assertEqual(rows[0]['range_end_ns'], '656000')
        self.assertEqual(rows[0]['time_mode'], 'abs')

    def test_range_csv_statistics_adjustment_filter_and_count_units(self):
        self.open_range_data(['parent', 'child', 'zero-start', 'zero-end'],
                             starts=[0, 2000, 4000, 6000], durations=[10000, 4000, 0, 0],
                             parents=[-1, 0, -1, -1])
        self.ev("fgView('timeline').probe().range(4000, 6000, 0)")
        rows = {row['function']: row for row in self.download_range_csv()}
        self.assertEqual(set(rows), {'parent', 'child', 'zero-start'})
        self.assertEqual(float(rows['parent']['self_estimate_ns']), 1200)
        self.assertEqual(float(rows['parent']['inclusive_estimate_ns']), 2000)
        self.assertEqual(float(rows['child']['inclusive_estimate_ns']), 2000)
        self.assertEqual(rows['parent']['max_call_ns'], '10000')
        self.assertEqual(rows['parent']['calls'], '0')
        self.assertEqual(rows['zero-start']['calls'], '1')
        self.page.click('[data-act=adj]')
        adjusted = {row['function']: row for row in self.download_range_csv()}
        self.assertEqual(float(adjusted['parent']['self_estimate_ns']), 600)
        self.assertEqual(adjusted['parent']['duration_basis'], 'overhead_subtracted_estimate')
        self.ev("fgNotifyFilter('=one call', new Set([2]))")
        filtered = self.download_range_csv()
        self.assertEqual([row['function'] for row in filtered], ['child'])
        self.assertEqual(filtered[0]['filter'], "'=one call")
        self.ev("fgSwitchView('text')")
        self.page.reload()
        self.open_range_data(['ordered_call'], mode='count')
        self.ev("fgView('timeline').probe().range(0, 1000)")
        row = self.download_range_csv()[0]
        self.assertNotIn('self_estimate_ns', row)
        self.assertEqual(float(row['self_estimate_units']), 1)
        self.assertEqual(float(row['range_end_units']), 1)
        self.assertEqual(row['duration_basis'], 'call_order')

    def test_range_compact_header_keeps_export_and_close_reachable(self):
        self.open_range_data(['wide_function_name_' * 8])
        self.ev("fgView('timeline').probe().range(0, 1000)")
        for width, height in [(390, 844), (320, 700), (800, 420)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_timeout(100)
                for selector in ('[data-act=range-csv]', '.tl-close'):
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                self.assertTrue(self.ev("""() => {
                    const head = document.querySelector('.tl-range-head');
                    const header = document.querySelector('.tl-range-table th');
                    return parseInt(header.style.top, 10) === head.offsetHeight;
                }"""))
        self.page.click('.tl-close')
        self.assertFalse(self.page.locator('.tl-detail.show').count())

    def test_timeline_search_previous_starts_at_last_and_wraps(self):
        self.open_timeline()
        total = self.ev("fgView('timeline').probe().N")
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Shift+Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), total - 1)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{total} / {total} calls')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'1 / {total} calls')

    def test_timeline_search_invalid_and_empty_results_do_not_reuse_old_hits(self):
        self.open_timeline()
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Enter')
        selected = self.ev("fgView('timeline').probe().sel()")
        self.page.fill('.tl-search', '[')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selected)
        self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'true')
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Invalid regex')
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())
        self.page.fill('.tl-search', '^not_a_captured_function$')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selected)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '0 calls')
        self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'false')
        self.assertTrue(self.page.locator('[data-act=searchprev]').is_disabled())
        self.page.press('.tl-search', 'Escape')
        self.assertEqual(self.page.locator('.tl-search').input_value(), '')
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '')

    def test_timeline_search_tests_function_names_once_and_cancels_pending_work(self):
        self.open_timeline()
        names = self.ev("""() => {
            const probe = fgView('timeline').probe();
            window.testedSearchNames = [];
            const originalTest = RegExp.prototype.test;
            RegExp.prototype.test = function(text) {
                if (this.source === '^__arm64_sys_openat$') window.testedSearchNames.push(text);
                return originalTest.call(this, text);
            };
            return [...new Set([...probe.sName.slice(0, probe.N)].map(index => probe.S[index]))];
        }""")
        self.page.fill('.tl-search', '^__arm64_sys_openat$')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(sorted(self.ev('window.testedSearchNames')), sorted(names))
        self.page.wait_for_timeout(300)
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(len(self.ev('window.testedSearchNames')), len(names))
        self.ev("fgNotifyFilter('', null)")
        self.assertEqual(len(self.ev('window.testedSearchNames')), len(names))

    def test_timeline_search_tracks_shared_filter_and_refreshes_count(self):
        self.open_timeline()
        total = self.ev("fgView('timeline').probe().N")
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Enter')
        self.ev("""() => {
            const data = JSON.parse(document.getElementById('tlData').textContent);
            fgNotifyFilter('one call', new Set([data.cols.line[data.n - 1]]));
        }""")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 calls')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), total - 1)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 1 calls')
        self.ev("fgNotifyFilter('', null)")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{total} / {total} calls')
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '0 calls')
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())

    def test_timeline_search_buttons_and_selection_counter(self):
        self.open_timeline()
        self.assertEqual(self.page.locator('[data-act=searchnext]').count(), 1)
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())
        matches = self.ev("""() => {
            const probe = fgView('timeline').probe();
            return Array.from({length: probe.N}, (unused, index) => index)
                .filter(index => /^(mutex_unlock|__arm64_sys_openat)$/.test(probe.nameOf(index)));
        }""")
        self.assertGreaterEqual(len(matches), 2)
        self.page.fill('.tl-search', '^(mutex_unlock|__arm64_sys_openat)$')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[0])
        self.page.click('[data-act=searchnext]')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[1])
        self.page.press('[data-act=searchprev]', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[0])
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.ev("index => fgView('timeline').probe().select(index)", matches[-1])
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{len(matches)} / {len(matches)} calls')
        self.page.press('[data-act=searchnext]', 'Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[0])
        self.ev("fgView('timeline').probe().select(1)")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{len(matches)} calls')
        self.page.click('[data-act=searchnext]')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[1])

    def test_timeline_search_shortcuts_flush_pending_query_and_keep_input_focus(self):
        self.open_timeline()
        self.page.keyboard.press('/')
        self.assertTrue(self.ev("document.activeElement.matches('.tl-search')"))
        pending = self.ev("""() => {
            const input = document.querySelector('.tl-search');
            input.value = '  ^MUTEX_UNLOCK$  ';
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.blur();
            return document.querySelector('.tl-count').textContent;
        }""")
        self.assertEqual(pending, 'Searching…')
        self.page.keyboard.press('n')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)
        self.assertTrue(self.ev("document.activeElement.matches('.tl-canvas')"))
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 1 calls')
        self.page.keyboard.press('/')
        self.page.press('.tl-search', 'Escape')
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '')
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())

    def test_timeline_control_enter_keeps_native_button_activation(self):
        self.open_timeline()
        self.ev("fgView('timeline').probe().select(0)")
        self.page.press('[data-act=help]', 'Enter')
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.assertTrue(self.page.locator('.tl-help').is_visible())

    def test_timeline_search_status_wrap_keeps_canvas_dimensions_in_sync(self):
        self.page.set_viewport_size({'width': 1400, 'height': 900})
        self.open_timeline()
        for query in ['.*', '^not_a_captured_function$', '']:
            with self.subTest(query=query):
                self.page.fill('.tl-search', query)
                self.page.wait_for_timeout(250)
                canvas, client = self.ev("""() => [
                    document.querySelector('.tl-canvas').getBoundingClientRect().height,
                    document.querySelector('.tl-scroll').clientHeight]
                """)
                self.assertAlmostEqual(canvas, client, delta=1)

    def test_compare_changed_only_covers_every_metric(self):
        baseline = [2, 100, 50, 70, 0]
        rows = [['unchanged'] + baseline + baseline]
        metrics = ['calls', 'inclusive', 'self', 'max', 'errors']
        for index, metric in enumerate(metrics):
            current = baseline.copy()
            current[index] += 1
            rows.append([metric] + baseline + current)
        self.open_compare(rows)
        names = self.page.locator('.cmp-table tbody td.l').all_text_contents()
        self.assertEqual(set(names), set(metrics))
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '5 of 6 functions')
        self.page.uncheck('.cmp-changed')
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '6 of 6 functions')

    def test_compare_empty_filter_has_explanation(self):
        self.open_compare()
        self.page.fill('.cmp-q', '^not_a_captured_function$')
        self.page.wait_for_function("document.querySelector('.cmp-count').textContent.startsWith('0 of ')")
        self.assertEqual(self.page.locator('.cmp-empty').count(), 1)
        self.assertTrue(self.page.locator('.cmp-empty').is_visible())
        self.assertIn('No functions match', self.page.locator('.cmp-empty').inner_text())
        self.page.fill('.cmp-q', '')
        self.page.wait_for_function("!document.querySelector('.cmp-count').textContent.startsWith('0 of ')")
        self.assertFalse(self.page.locator('.cmp-empty').is_visible())

    def test_compare_responsive_height_and_sticky_function_names(self):
        self.open_compare()
        for width, height in [(1280, 720), (800, 600), (390, 844)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_timeout(100)
                bounds = self.page.locator('.cmp-wrap').bounding_box()
                self.assertLessEqual(bounds['y'] + bounds['height'], height + 1)
                positions = self.ev("""() => {
                    const wrap = document.querySelector('.cmp-wrap');
                    wrap.scrollLeft = wrap.scrollWidth - wrap.clientWidth;
                    return [wrap.getBoundingClientRect().left,
                            document.querySelector('.cmp-table tbody td.l').getBoundingClientRect().left,
                            document.querySelector('.cmp-table th.l').getBoundingClientRect().left];
                }""")
                self.assertAlmostEqual(positions[0], positions[1], delta=2)
                self.assertAlmostEqual(positions[0], positions[2], delta=2)

    def test_compare_csv_exports_all_matches_and_pending_filter(self):
        rows = [[f'worker_{index:04d}', 2, 100, 50, 70, 0,
                 2, 200 + index, 100 + index, 120 + index, 0] for index in range(605)]
        rows.append(['other_function', 2, 100, 50, 70, 0, 2, 2000, 1500, 1700, 0])
        self.open_compare(rows)
        self.assertEqual(self.page.locator('.cmp-export').count(), 1)
        with self.page.expect_download() as pending:
            self.ev("""() => {
                const input = document.querySelector('.cmp-q');
                input.value = '^worker_';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                document.querySelector('.cmp-export').click();
            }""")
        download = pending.value
        self.assertEqual(download.suggested_filename, 'funcgraph-compare-raw.csv')
        with open(download.path(), encoding='utf-8-sig', newline='') as stream:
            exported = list(csv.DictReader(stream))
        self.assertEqual(len(exported), 605)
        self.assertEqual(len(exported[0]), 19)
        self.assertEqual(exported[0]['function'], 'worker_0604')
        self.assertEqual(exported[-1]['function'], 'worker_0000')
        self.assertEqual(exported[0]['base_self_ns'], '50')
        self.assertEqual(exported[0]['current_self_ns'], '704')
        self.assertEqual(exported[0]['delta_self_ns'], '654')
        self.assertEqual(self.page.locator('.cmp-table tbody tr').count(), 401)
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '605 of 606 functions')

    def test_compare_csv_adjusted_values_and_escaping(self):
        names = ['worker,"quoted"\nnext_line', '=SUM(1,2)', '处理函数']
        rows = [[name, 2, 100, 50, 70, 0, 2, 150, 80, 90, 1] for name in names]
        adjusted = [[name, 2, 90, 40, 60, 0, 2, 130, 65, 80, 1] for name in names]
        self.open_compare(rows, adjusted)
        self.assertEqual(self.page.locator('.cmp-export').count(), 1)
        self.page.check('.cmp-adj input')
        self.assertIn('summary pills stay measured', self.page.locator('.cmp-note').inner_text())
        with self.page.expect_download() as pending:
            self.page.click('.cmp-export')
        download = pending.value
        self.assertEqual(download.suggested_filename, 'funcgraph-compare-adjusted.csv')
        with open(download.path(), encoding='utf-8-sig', newline='') as stream:
            exported = list(csv.DictReader(stream))
        self.assertEqual([row['function'] for row in exported], [names[0], "'" + names[1], names[2]])
        for row in exported:
            self.assertEqual(row['base_incl_ns'], '90')
            self.assertEqual(row['current_incl_ns'], '130')
            self.assertEqual(row['base_self_ns'], '40')
            self.assertEqual(row['delta_self_ns'], '25')
            self.assertEqual(row['delta_mean_ns'], '20')
            self.assertEqual(row['delta_err'], '1')

    def test_compare_invalid_regex_and_empty_results_disable_export(self):
        self.open_compare([['worker', 2, 100, 50, 70, 0, 2, 150, 80, 90, 1]])
        self.assertEqual(self.page.locator('.cmp-export').count(), 1)
        self.ev("""() => {
            const input = document.querySelector('.cmp-q');
            input.value = '[';
            input.dispatchEvent(new Event('input', {bubbles: true}));
            document.querySelector('.cmp-export').click();
        }""")
        self.assertTrue(self.page.locator('.cmp-export').is_disabled())
        self.assertEqual(self.page.locator('.cmp-q').get_attribute('aria-invalid'), 'true')
        self.assertTrue(self.page.locator('.cmp-filter-error').is_visible())
        self.assertIn('Invalid regular expression', self.page.locator('.cmp-filter-error').inner_text())
        self.assertEqual(self.page.locator('.cmp-table tbody tr').count(), 1)
        self.page.fill('.cmp-q', '^missing$')
        self.page.wait_for_function("document.querySelector('.cmp-count').textContent.startsWith('0 of ')")
        self.assertTrue(self.page.locator('.cmp-export').is_disabled())
        self.assertFalse(self.page.locator('.cmp-filter-error').is_visible())
        self.page.fill('.cmp-q', '^worker$')
        self.page.press('.cmp-q', 'Enter')
        self.assertFalse(self.page.locator('.cmp-export').is_disabled())

    def test_compare_load_more_reuses_sorted_rows_and_existing_nodes(self):
        rows = [[f'worker_{index:04d}', 2, 100, 50, 70, 0,
                 2, 200 + index, 100 + index, 120 + index, 0] for index in range(1205)]
        self.open_compare(rows)
        self.ev("""() => {
            window.firstCompareRow = document.querySelector('.cmp-table tbody tr');
            window.compareSortCalls = 0;
            const originalSort = Array.prototype.sort;
            Array.prototype.sort = function(...args) {
                const row = Array.isArray(this[0]) ? this[0] : this[0] && this[0].row;
                if (this.length === 1205 && row && row[0].startsWith('worker_')) window.compareSortCalls++;
                return originalSort.apply(this, args);
            };
        }""")
        self.page.click('.cmp-table .more')
        self.assertEqual(self.page.locator('.cmp-table tbody tr').count(), 1205)
        self.assertEqual(self.ev('window.compareSortCalls'), 0)
        self.assertTrue(self.ev("window.firstCompareRow === document.querySelector('.cmp-table tbody tr')"))

    def test_compare_cached_results_follow_every_sort_mode(self):
        self.open_compare([
            ['added', 0, 0, 0, 0, 0, 2, 200, 100, 150, 0],
            ['grown', 1, 100, 50, 100, 0, 5, 500, 250, 150, 0],
            ['shrunk', 10, 1000, 500, 150, 0, 4, 400, 200, 150, 0],
        ])
        self.page.click('.cmp-table th[data-k=calls]')
        expected_orders = {
            'absdiff': ['shrunk', 'grown', 'added'],
            'diff': ['grown', 'added', 'shrunk'],
            'pct': ['added', 'grown', 'shrunk'],
            'cur': ['grown', 'shrunk', 'added'],
            'base': ['shrunk', 'grown', 'added'],
        }
        for mode, expected in expected_orders.items():
            with self.subTest(mode=mode):
                self.page.click(f'.cmp-sortby button[data-v={mode}]')
                names = self.page.locator('.cmp-table tbody td.l[title]').evaluate_all(
                    'cells => cells.map(cell => cell.title)')
                self.assertEqual(names, expected)
        self.page.click('.cmp-table th[data-k=name]')
        names = self.page.locator('.cmp-table tbody td.l[title]').evaluate_all(
            'cells => cells.map(cell => cell.title)')
        self.assertEqual(names, ['added', 'grown', 'shrunk'])

    def test_compare_only_report_has_styled_controls_and_exports(self):
        self.k.run(helpers.fixture('pi4_sched'), '--compare', helpers.fixture('pi4_default'),
                   out='compare_only.html')
        self.page.goto('file://' + self.k.dir + '/compare_only.html', wait_until='load')
        self.page.click('.view-tab[data-view=compare]')
        self.assertEqual(self.page.locator('#tlData').count(), 0)
        self.assertEqual(self.ev("getComputedStyle(document.querySelector('.cmp-sortby')).display"), 'flex')
        self.assertEqual(self.ev("getComputedStyle(document.querySelector('.cmp-sortby button.on')).backgroundColor"),
                         'rgb(59, 111, 216)')
        self.assertEqual(self.ev("getComputedStyle(document.querySelector('.cmp-export')).borderRadius"), '6px')
        with self.page.expect_download() as pending:
            self.page.click('.cmp-export')
        with open(pending.value.path(), encoding='utf-8-sig', newline='') as stream:
            exported = list(csv.DictReader(stream))
        self.assertGreater(len(exported), 100)

    def test_timeline_error_navigation_and_jump_to_text(self):
        self.open_timeline()
        self.page.keyboard.press('e')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("document.querySelector('.tl-detail').classList.contains('show')"))
        self.assertIn('error', self.ev("document.querySelector('.tl-detail-head').textContent"))
        self.assertTrue(self.ev("document.activeElement.matches('.tl-canvas')"))
        self.page.keyboard.press('Enter')
        self.page.wait_for_timeout(300)
        self.assertFalse(self.ev("document.body.dataset.view === 'timeline'"))
        self.assertTrue(self.ev("!!document.querySelector('.line-container.tl-flash')"))

    def test_timeline_follows_text_filter(self):
        self.open_timeline()
        self.page.fill('.tl-ftext', 'comm:sh')
        self.page.press('.tl-ftext', 'Enter')
        self.page.wait_for_timeout(400)
        count = self.ev("document.querySelector('.tl-fcount').textContent")
        self.assertRegex(count, r'^\d+ of \d+ calls$')
        self.page.click('.tl-filterbar [data-act=fclear]')
        self.page.wait_for_timeout(300)
        self.assertEqual(self.ev("document.querySelector('.tl-fcount').textContent"), '')

    def test_detail_call_site_source(self):
        # the detail panel reuses the text view's resolved source block of the call site
        self.open_timeline()
        self.page.keyboard.press('e')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("!!document.querySelector('.tl-detail.show')"))
        # the fake kernel has no source tree: position link only, no empty source block
        self.assertFalse(self.ev("!!document.querySelector('.tl-cs')"))
        at = self.ev("(document.querySelector('.tl-cs-at .tl-link') || {}).textContent || ''")
        self.assertRegex(at, r'\.[ch]:\d+$')
        self.assertTrue(self.ev("(document.querySelector('.tl-cs-at a') || {}).href || ''").startswith('https://example.org/src/'))

    def test_untraced_frames_fill_depth_gaps(self):
        # pi4_nodur has calls two levels below their parent (kernel depth jump): the missing level is
        # drawn as an "untraced" frame named after the calls' caller, and selecting it opens its panel
        self.k.run(helpers.fixture('pi4_nodur'), '--timeline', out='gap.html')
        self.page.goto('file://' + self.k.dir + '/gap.html', wait_until='load')
        self.open_timeline()
        r = self.ev("""() => { const q = fgView('timeline').probe(); const g = [];
            for (let i = q.N; i < q.NA; i++) if (q.isGhost(i)) {
                const y = q.SYN[i - q.N];
                g.push([i, q.S[q.sName[i]], q.sDepth[i] - q.sDepth[y.p], y.kids.every(c => q.sDepth[c] === q.sDepth[i] + 1)]);
            }
            return g; }""")
        self.assertTrue(r)
        for i, name, dd, nested in r:
            self.assertEqual(dd, 1)
            self.assertTrue(nested)
            self.assertNotEqual(name, '')
        self.ev(f"fgView('timeline').probe().select({r[0][0]})")
        self.page.wait_for_timeout(300)
        self.assertIn('not traced', self.ev("document.querySelector('.tl-detail').innerText"))

    def test_ruler_aligned_with_tracks_when_scrollbar_shown(self):
        # headless Chromium hides scrollbars by default; with a real one the track area is narrower
        # than the ruler, and the ruler must not be stretched (selection marks drifted apart)
        b = self.pw.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        try:
            pg = b.new_page(viewport={'width': 1200, 'height': 420})
            pg.goto(self.url, wait_until='load')
            pg.evaluate("fgSwitchView('timeline')")
            pg.wait_for_timeout(400)
            r = pg.evaluate("""() => { const s = document.querySelector('.tl-scroll');
                return [document.querySelector('.tl-head-canvas').getBoundingClientRect().width,
                        document.querySelector('.tl-canvas').getBoundingClientRect().width,
                        s.clientWidth, s.offsetWidth]; }""")
        finally:
            b.close()
        head, main, client, offset = r
        if offset == client:
            self.skipTest('no visible scrollbar in this environment')
        self.assertEqual(head, main)
        self.assertEqual(main, client)

    def test_detail_panel_resize_and_maximize(self):
        self.open_timeline()
        self.page.click('[data-act=an-errors]')
        self.page.wait_for_timeout(200)
        box = lambda sel: self.page.locator(sel).bounding_box()
        h0, st0 = box('.tl-detail')['height'], box('.tl-stage')['height']
        r = box('.tl-resizer')
        x, y = r['x'] + r['width'] / 2, r['y'] + 4
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x, y - 120, steps=5)
        self.page.mouse.up()
        self.page.wait_for_timeout(200)
        self.assertAlmostEqual(box('.tl-detail')['height'], h0 + 120, delta=3)
        self.assertAlmostEqual(box('.tl-stage')['height'], st0 - 120, delta=3)
        self.assertLessEqual(box('.tl-detail')['y'] + box('.tl-detail')['height'], 900 + 1)
        self.page.dblclick('.tl-resizer')
        self.page.wait_for_timeout(200)
        self.assertAlmostEqual(box('.tl-detail')['height'], h0, delta=3)
        top0 = box('.tl-stage')['y']
        self.page.click('[data-act=max]')
        self.page.wait_for_timeout(200)
        self.assertFalse(self.page.locator('.view-tabs').is_visible())
        self.assertLess(box('.tl-stage')['y'], top0 - 40)
        self.page.click('[data-act=max]')
        self.page.wait_for_timeout(200)
        self.assertTrue(self.page.locator('.view-tabs').is_visible())
        self.assertEqual(self.errors, [])

    def test_analysis_panels(self):
        self.open_timeline()
        self.page.click('[data-act=an-errors]')
        self.page.wait_for_timeout(200)
        self.assertGreater(self.ev("document.querySelectorAll('.an-tbl tbody tr[data-chains]').length"), 0)
        self.page.click('.an-tbl tbody tr[data-chains]')
        self.page.wait_for_timeout(200)
        self.assertIn('occurrence 1', self.ev("document.querySelector('.tl-toast').textContent"))
        self.page.click('[data-act=an-lat]')
        self.page.wait_for_timeout(200)
        self.assertIn('Scheduling latency', self.ev("document.querySelector('.tl-detail-title').textContent"))
        self.page.keyboard.press('e')
        self.page.wait_for_timeout(200)
        self.page.keyboard.press('h')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("!!document.querySelector('.an-hist')"))
        self.assertGreater(self.ev("document.querySelectorAll('.an-list tr').length"), 0)

    def test_breakdown_sections_slowfast(self):
        self.open_timeline()
        self.page.fill('.tl-search', '^__arm64_sys_openat$')
        self.page.press('.tl-search', 'Enter')
        self.page.wait_for_timeout(200)
        self.ev('document.activeElement.blur()')
        self.assertIn('own code', self.ev("document.querySelector('.an-bd-legend').innerText"))
        self.page.keyboard.press('h')
        self.page.wait_for_timeout(200)
        self.page.click('[data-act=slowfast]')
        self.page.wait_for_timeout(300)
        self.assertRegex(self.ev("document.querySelector('.tl-detail').innerText"), r'Slow vs fast')
        self.page.click('[data-act=an-sec]')
        self.page.wait_for_timeout(200)
        self.assertGreater(self.ev("document.querySelectorAll('tr[data-sec]').length"), 0)

    def test_source_block_modes(self):
        lid = self.ev("(() => { const c = Array.from(document.querySelectorAll('.expanded-content[data-src]')); return c[0].id; })()")
        line_id = lid[:-len('_content')]
        self.page.query_selector(f'[data-line-id="{line_id}"] .line-content').click()
        self.page.wait_for_timeout(200)
        self.assertEqual(self.ev("document.getElementById('srcModeBtn').textContent"), 'Source: chain')
        self.page.click('#srcModeBtn')
        self.page.wait_for_timeout(200)
        self.assertEqual(self.ev("document.getElementById('srcModeBtn').textContent"), 'Source: flat')
        self.assertEqual(self.ev(f"document.querySelectorAll('#{lid} details').length"), 0)

    def test_inline_toggle_hidden_without_data(self):
        self.open_timeline()      # fake faddr2line has no inline chains
        self.assertEqual(self.ev("getComputedStyle(document.querySelector('[data-act=inl]')).display"), 'none')

    def test_text_order_by_task(self):
        self.page.click('#textOrderBtn')
        self.page.wait_for_timeout(300)
        runs, groups, gaps = self.ev("""() => {
            const L = Array.from(document.querySelectorAll('#content .line-container')).filter(l => l.dataset.pid && !l.classList.contains('grp-hidden'));
            let runs = 0, prev = null;
            for (const l of L) { const k = l.dataset.pid === '0' ? 'i' + l.dataset.cpu : l.dataset.pid; if (k !== prev) { runs++; prev = k; } }
            return [runs, document.querySelectorAll('.task-group-head').length, document.querySelectorAll('.task-gap').length]; }""")
        self.assertEqual(runs, groups)                     # every task's lines are contiguous
        self.assertGreater(gaps, 0)                        # context switches summarized
        self.page.query_selector('.fold-entry .fold-icon').click()
        self.page.wait_for_timeout(100)
        self.page.click('#textOrderBtn')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("""(() => { const n = Array.from(document.querySelectorAll('#content .line-container')).map(l => +l.dataset.lineNumber);
            return n.every((x, i) => i === 0 || x > n[i - 1]) && !document.querySelector('.task-gap, .task-group-head'); })()"""))

    def test_wide_slice_after_thin_one_is_drawn(self):
        # compressed time: a 1 ns call, then a 100 us call starting in the same pixel at full view
        trace = os.path.join(self.k.dir, 'thin_wide.txt')
        with open(trace, 'w') as f:
            f.write(' 0)   a-1     |   0.001 us    |  thin();\n'
                    ' 0)   a-1     | ! 100.000 us  |  wide();\n')
        self.k.run(trace, '--timeline', '--no-text-view', out='thin_wide.html')
        self.page.goto('file://' + self.k.dir + '/thin_wide.html', wait_until='load')
        self.page.wait_for_timeout(500)
        # middle of the first call row (track header 20 px + half a 17 px row)
        # compared with an empty spot of the same canvas (a deeper row without calls)
        painted = self.ev("""(() => { const cv = document.querySelector('.tl-canvas'), d = window.devicePixelRatio || 1;
            const g = cv.getContext('2d'), x = Math.round(cv.width / 2);
            const a = g.getImageData(x, Math.round(28 * d), 1, 1).data, e = g.getImageData(x, Math.round(80 * d), 1, 1).data;
            return Math.abs(a[0] - e[0]) + Math.abs(a[1] - e[1]) + Math.abs(a[2] - e[2]) > 30; })()""")
        self.assertTrue(painted, 'wide slice not drawn at full view')

    def test_wakeup_overlay_toggle(self):
        self.open_timeline()
        self.assertFalse(self.ev("document.querySelector('[data-ov=wk]').classList.contains('na')"))
        self.page.click('[data-ov=wk]')
        self.page.click('[data-ov=wk]')

    def test_range_summary_and_theme(self):
        self.open_timeline()
        box = self.page.query_selector('.tl-canvas').bounding_box()
        self.page.keyboard.down('Shift')
        self.page.keyboard.down('Alt')
        self.page.mouse.move(box['x'] + box['width'] * 0.1, box['y'] + 30)
        self.page.mouse.down()
        self.page.mouse.move(box['x'] + box['width'] * 0.9, box['y'] + 30, steps=4)
        self.page.mouse.up()
        self.page.keyboard.up('Alt')
        self.page.keyboard.up('Shift')
        self.page.wait_for_timeout(300)
        self.assertGreater(self.ev("document.querySelectorAll('table.tl-sum tbody tr').length"), 5)
        self.ev('toggleTheme()')
        for mode in ('name', 'heat', 'cpu', 'ctx'):
            self.page.click(f'[data-role=color] button[data-v={mode}]')
        self.page.wait_for_timeout(200)


if __name__ == '__main__':
    unittest.main()
