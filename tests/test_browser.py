"""Headless browser smoke test of a generated report (skipped without Playwright + Chromium)."""
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
                  '--base-url', 'https://example.org/src', '--timeline', out='report.html')
        cls.url = 'file://' + cls.k.dir + '/report.html'
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
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

    def test_timeline_error_navigation_and_jump_to_text(self):
        self.open_timeline()
        self.page.keyboard.press('e')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("document.querySelector('.tl-detail').classList.contains('show')"))
        self.assertIn('error', self.ev("document.querySelector('.tl-detail-head').textContent"))
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
