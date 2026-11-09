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

    def load_startup_report(self, saved='text', theme='light', fragment='', filename='report.html'):
        self.ev("({saved, theme}) => { lsSet('view', saved); localStorage.setItem('theme', theme); }",
                dict(saved=saved, theme=theme))
        with open(os.path.join(self.k.dir, filename), encoding='utf-8') as stream:
            report = stream.read()
        instrument = '''<script>
            window.startupFacts = {
                visibility: getComputedStyle(document.getElementById('content')).contentVisibility,
                theme: document.documentElement.getAttribute('data-theme'),
                textFocus: 0, themeChanges: 0
            };
            const startupFocus = HTMLElement.prototype.focus;
            HTMLElement.prototype.focus = function (...args) {
                if (this.matches('.line-container')) startupFacts.textFocus++;
                return startupFocus.apply(this, args);
            };
            new MutationObserver(records => startupFacts.themeChanges += records.length)
                .observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
        </script>'''
        report = report.replace('<div id="content">', '<div id="content">' + instrument, 1)
        path = os.path.join(self.k.dir, 'startup.html')
        with open(path, 'w', encoding='utf-8') as stream:
            stream.write(report)
        self.page.goto('file://' + path + fragment, wait_until='load')

    def test_saved_timeline_skips_initial_text_render_and_focus(self):
        self.load_startup_report(saved='timeline', theme='dark')
        facts = self.ev('startupFacts')
        self.assertEqual(facts, dict(visibility='hidden', theme='dark', textFocus=0, themeChanges=0))
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.assertTrue(self.ev("document.getElementById('content').classList.contains('fg-off')"))
        self.assertTrue(self.ev("typeof fgInitialView === 'undefined'"))
        self.ev("fgSwitchView('text')")
        self.assertEqual(self.ev("getComputedStyle(document.getElementById('content')).contentVisibility"), 'visible')
        self.page.fill('#filterInput', 'comm:sh')
        self.page.press('#filterInput', 'Enter')
        self.assertEqual(self.ev('fgFilterState.text'), 'comm:sh')
        self.ev("fgSwitchView('timeline')")
        self.assertIn('of', self.page.locator('.tl-fcount').inner_text())
        self.ev('toggleTheme()')
        self.assertEqual(self.ev("document.documentElement.getAttribute('data-theme')"), 'light')

    def test_saved_compare_skips_initial_text_render_and_focus(self):
        self.load_startup_report(saved='compare')
        self.assertEqual(self.ev('startupFacts.visibility'), 'hidden')
        self.assertEqual(self.ev('startupFacts.textFocus'), 0)
        self.assertEqual(self.ev('fgCurrentView()'), 'compare')
        self.assertGreater(self.page.locator('.cmp-table tbody tr').count(), 0)
        self.ev("fgSwitchView('text')")
        self.assertEqual(self.ev("getComputedStyle(document.getElementById('content')).contentVisibility"), 'visible')

    def test_initial_text_and_anchors_override_unavailable_or_saved_views(self):
        for saved, fragment in [('text', ''), ('missing', ''), ('timeline', '#L10')]:
            with self.subTest(saved=saved, fragment=fragment):
                self.load_startup_report(saved=saved, fragment=fragment)
                self.assertEqual(self.ev('startupFacts.visibility'), 'visible')
                self.assertEqual(self.ev('fgCurrentView()'), 'text')
                self.assertGreater(self.ev('startupFacts.textFocus'), 0)
                self.assertEqual(self.ev('startupFacts.themeChanges'), 0)
                self.assertTrue(self.ev("typeof fgInitialView === 'undefined'"))

    def test_timeline_only_default_precedes_saved_view_and_anchor(self):
        self.k.run(helpers.fixture('pi4_sched'), '--timeline', '--no-text-view',
                   '--compare', helpers.fixture('pi4_default'), out='timeline_only.html')
        self.load_startup_report(saved='compare', fragment='#L1', filename='timeline_only.html')
        self.assertEqual(self.ev('startupFacts.visibility'), 'hidden')
        self.assertEqual(self.ev('startupFacts.textFocus'), 0)
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.assertGreater(self.ev("fgView('timeline').probe().N"), 100)

    def test_plain_report_has_early_theme_and_text_keyboard_focus(self):
        self.k.run(helpers.fixture('pi4_default'), out='plain.html')
        self.load_startup_report(saved='timeline', theme='dark', filename='plain.html')
        self.assertEqual(self.ev('startupFacts.visibility'), 'visible')
        self.assertEqual(self.ev('startupFacts.theme'), 'dark')
        self.assertEqual(self.ev('startupFacts.themeChanges'), 0)
        self.assertGreater(self.ev('startupFacts.textFocus'), 0)
        self.assertTrue(self.ev("typeof fgInitialView === 'undefined'"))
        self.ev('toggleTheme()')
        self.assertEqual(self.ev("document.documentElement.getAttribute('data-theme')"), 'light')

    def stage_timeline(self, slow=False):
        self.ev('''slow => {
            const original = fgViews.timeline.steps;
            fgViews.timeline.staged = true;
            window.stageFactories = 0; window.stageClosed = 0;
            fgViews.timeline.steps = function* () {
                stageFactories++;
                try {
                    if (slow) for (let phase = 0; phase < 20; phase++) {
                        yield 'Test preparation phase ' + phase;
                        const end = performance.now() + 10;
                        while (performance.now() < end) {}
                    }
                    return yield* original();
                } finally { stageClosed++; }
            };
        }''', slow)

    def test_large_saved_timeline_automatically_stages_before_initial_paint(self):
        with open(os.path.join(self.k.dir, 'report.html'), encoding='utf-8') as stream:
            report = stream.read()
        marker = '<script type="application/json" id="tlData">'
        self.assertEqual(report.count(marker), 1)
        report = report.replace(marker, marker + ' ' * (1024 * 1024), 1)
        report = report.replace('</body>', '''<script>
            window.addEventListener('DOMContentLoaded', () => {
                window.largeStartup = {staged:fgViews.timeline.staged,
                    busy:document.getElementById('timelineView').getAttribute('aria-busy'),
                    canvas:!!document.querySelector('.tl-canvas')};
            });
        </script></body>''', 1)
        with open(os.path.join(self.k.dir, 'large.html'), 'w', encoding='utf-8') as stream:
            stream.write(report)
        self.load_startup_report(saved='timeline', filename='large.html')
        self.assertEqual(self.ev('largeStartup'), dict(staged=True, busy='true', canvas=False))
        self.assertEqual(self.ev('startupFacts.textFocus'), 0)
        self.page.wait_for_selector('.tl-canvas')
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.assertFalse(self.page.locator('#timelineView').get_attribute('aria-busy'))

    def test_staged_status_wraps_in_compact_light_and_dark_viewports(self):
        self.stage_timeline(slow=True)
        for width in [320, 390, 800, 1500]:
            for theme in ['light', 'dark']:
                with self.subTest(width=width, theme=theme):
                    self.page.set_viewport_size({'width': width, 'height': 700})
                    self.ev('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                    self.ev("() => { fgSwitchView('timeline'); }")
                    self.assertEqual(self.page.locator('#timelineView').get_attribute('aria-busy'), 'true')
                    self.assertTrue(self.page.locator('.fg-loading-status[role=status]').is_visible())
                    bounds = self.page.locator('.fg-loading button').bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.assertLessEqual(bounds['y'] + bounds['height'], 700)
                    self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                    self.page.locator('.fg-loading button').focus()
                    self.page.keyboard.press('Space')
                    self.assertEqual(self.ev('fgCurrentView()'), 'text')

    def test_staged_timeline_has_paintable_status_and_awaitable_cached_api(self):
        self.stage_timeline()
        initial = self.ev('''() => {
            window.stageReady = fgSwitchView('timeline');
            const repeated = fgSwitchView('timeline');
            requestAnimationFrame(() => requestAnimationFrame(() => {
                window.stagePaint = {
                    busy:document.getElementById('timelineView').getAttribute('aria-busy'),
                    status:document.querySelector('.fg-loading-status').textContent,
                    canvas:!!document.querySelector('.tl-canvas')
                };
            }));
            return {same:repeated === stageReady, promise:typeof stageReady.then === 'function',
                busy:document.getElementById('timelineView').getAttribute('aria-busy')};
        }''')
        self.assertEqual(initial, dict(same=True, promise=True, busy='true'))
        self.assertTrue(self.ev('async () => (await stageReady) === fgView("timeline")'))
        self.assertEqual(self.ev('stagePaint'), dict(busy='true', status='Reading Timeline data…', canvas=False))
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [1, 1])
        self.assertFalse(self.page.locator('#timelineView').get_attribute('aria-busy'))
        self.assertEqual(self.page.locator('.fg-loading').count(), 0)
        self.assertGreater(self.ev('fgView("timeline").probe().N'), 100)
        self.ev("fgSwitchView('text')")
        self.assertTrue(self.ev('''() => {
            const api = fgSwitchView('timeline');
            return !api.then && api === fgView('timeline');
        }'''))
        self.assertEqual(self.ev('stageFactories'), 1)

    def test_staged_cancel_button_restores_text_scroll_and_discards_pending_model(self):
        self.stage_timeline(slow=True)
        initial_scroll = self.ev("() => { window.scrollTo(0, 450); return scrollY; }")
        self.ev("() => { window.cancelledStage = fgSwitchView('timeline'); }")
        self.page.locator('.fg-loading button').click()
        self.assertTrue(self.ev('async () => await cancelledStage === null'))
        self.page.wait_for_timeout(100)
        self.assertEqual(self.ev('fgCurrentView()'), 'text')
        self.assertAlmostEqual(self.ev('scrollY'), initial_scroll, delta=2)
        self.assertTrue(self.ev("document.activeElement.matches('.view-tab[data-view=text]')"))
        self.assertEqual(self.page.locator('.tl-canvas').count(), 0)
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [1, 1])
        self.assertTrue(self.ev("typeof fgOnFilter === 'undefined'"))
        self.ev("fgViews.timeline.steps = () => __tlSteps()")
        self.ev("fgSwitchView('timeline')")
        self.assertEqual(self.page.locator('.tl-toolbar').count(), 1)

    def test_staged_escape_and_compare_switch_cancel_without_stale_activation(self):
        self.stage_timeline(slow=True)
        self.ev("() => { window.escapeStage = fgSwitchView('timeline'); }")
        self.page.keyboard.press('Escape')
        self.assertTrue(self.ev('async () => await escapeStage === null'))
        self.assertEqual(self.ev('fgCurrentView()'), 'text')
        self.ev("() => { window.compareStage = fgSwitchView('timeline'); fgSwitchView('compare'); }")
        self.assertTrue(self.ev('async () => await compareStage === null'))
        self.page.wait_for_timeout(100)
        self.assertEqual(self.ev('fgCurrentView()'), 'compare')
        self.assertGreater(self.page.locator('.cmp-table tbody tr').count(), 0)
        self.assertEqual(self.page.locator('.tl-canvas').count(), 0)
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [2, 2])

    def test_staged_direct_probe_finishes_same_iterator_exactly_once(self):
        self.stage_timeline()
        self.assertTrue(self.ev('''async () => {
            const ready = fgSwitchView('timeline');
            const api = fgView('timeline');
            return api.probe().N > 100 && await ready === api;
        }'''))
        self.page.wait_for_timeout(100)
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [1, 1])
        self.assertEqual(self.page.locator('.tl-toolbar').count(), 1)
        self.assertFalse(self.page.locator('#timelineView').get_attribute('aria-busy'))

    def test_staged_timeline_uses_filter_updated_during_preparation(self):
        self.stage_timeline()
        expected = self.ev('''() => {
            const data = JSON.parse(document.getElementById('tlData').textContent);
            const lines = new Set(data.cols.line.slice(0, 3));
            window.filterStage = fgSwitchView('timeline');
            fgNotifyFilter('func:pending', lines);
            return data.cols.line.filter((line, index) => lines.has(line) ||
                (data.cols.eline && lines.has(data.cols.eline[index]))).length;
        }''')
        self.assertTrue(self.ev('async () => !!(await filterStage)'))
        count = self.ev("fgView('timeline').probe().N")
        self.assertEqual(self.page.locator('.tl-fcount').inner_text(),
                         f'{expected} of {count} calls')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'func:pending')

    def test_staged_alt_click_reveals_requested_line_after_ready(self):
        self.stage_timeline()
        line = self.page.locator('.line-container.expandable').first
        number = int(line.get_attribute('data-line-number'))
        expected = self.ev("line => JSON.parse(document.getElementById('tlData').textContent).cols.line.indexOf(line)", number)
        self.assertGreaterEqual(expected, 0)
        line.locator('.line-content').click(modifiers=['Alt'])
        self.page.wait_for_selector('.tl-detail.show')
        self.assertEqual(self.ev('fgView("timeline").probe().sel()'), expected)
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [1, 1])

    def test_staged_failure_is_escaped_and_returns_to_text(self):
        unsafe = '<img src=x onerror=alert(1)>'
        self.ev('''message => {
            fgViews.timeline.staged = true;
            fgViews.timeline.steps = function* () { yield 'Starting…'; throw new Error(message); };
            window.failedStage = fgSwitchView('timeline');
        }''', unsafe)
        self.page.wait_for_selector('.fg-loading-status[role=alert]')
        self.assertTrue(self.ev('async () => await failedStage === null'))
        self.assertIn(unsafe, self.page.locator('.fg-loading-status').inner_text())
        self.assertEqual(self.page.locator('.fg-loading img').count(), 0)
        self.assertFalse(self.page.locator('#timelineView').get_attribute('aria-busy'))
        self.assertEqual(len(self.errors), 1)
        self.assertIn('[timeline] startup failed:', self.errors.pop())
        self.page.locator('.fg-loading button').focus()
        self.page.keyboard.press('Enter')
        self.assertEqual(self.ev('fgCurrentView()'), 'text')
        self.assertTrue(self.ev("document.activeElement.matches('.view-tab[data-view=text]')"))
        self.ev("fgViews.timeline.steps = () => __tlSteps()")
        self.ev("fgSwitchView('timeline')")
        self.assertEqual(self.page.locator('.tl-toolbar').count(), 1)

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

    def test_timeline_range_includes_all_recorded_event_ends(self):
        cases = [('runs', {'tasks': [dict(pid=1, comm='late', kind=0, cpu=0, runs=[[0, 9000, 0]])]}),
                 ('wait', {'offcpu': [[0, 8000, None, 0, 0, 1, 0, -1, 0, -1]]}),
                 ('resched', {'resched': [[0, 1000, 9000, 1, 0, 0]]}),
                 ('irqoff', {'irqoff': [[0, 1000, 9000]]}),
                 ('section', {'sections': [[0, 0, 1000, 9000, 0, 0, 0, 1, 1]]}),
                 ('wake', {'wakeups': [[0, 9000, 0, 0, -1, -1]]})]
        for label, extra in cases:
            with self.subTest(event=label):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_range_data(['early'], starts=[1000], durations=[1000], extra=extra)
                self.page.click('[data-act=fit]')
                expected = 8000 if label == 'wait' else 9000
                self.assertEqual(self.ev("fgView('timeline').probe().view()"), [1000, expected])

    def click_cpu_at(self, timestamp):
        self.page.click('[data-act=fit]')
        self.ev("document.querySelector('.tl-scroll').scrollTop = 0")
        self.page.wait_for_timeout(60)
        start, end = self.ev("fgView('timeline').probe().view()")
        box = self.page.locator('.tl-canvas').bounding_box()
        self.page.mouse.click(box['x'] + box['width'] * (timestamp - start) / (end - start), box['y'] + 28)

    def test_cpu_navigation_reveals_scheduler_only_task_and_clears_stale_selection(self):
        tasks = [dict(pid=1, comm='calls', kind=0, cpu=0, runs=[[0, 3000, 0]]),
                 dict(pid=2, comm='scheduler-only', kind=0, cpu=0, runs=[[3000, 9000, 0]])]
        self.open_range_data(['early'], starts=[0], durations=[2000], extra={'tasks': tasks})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertEqual(self.ev("fgView('timeline').probe().TRACKS[1].h"), 0)
        self.click_cpu_at(6000)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), -1)
        self.assertGreater(self.ev("fgView('timeline').probe().TRACKS[1].h"), 0)
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 1)
        self.assertIn('scheduler-only', self.page.locator('.an-task-table').inner_text())
        self.assertNotIn('early', self.page.locator('.tl-detail').inner_text())
        self.assertEqual(self.ev("fgView('timeline').probe().view()"), [0, 9000])

    def test_cpu_navigation_selects_innermost_real_nonzero_depth_call(self):
        tasks = [dict(pid=1, comm='nested', kind=0, cpu=0, runs=[[0, 9000, 0]])]
        self.open_range_data(['root', 'leaf'], starts=[0, 1000], durations=[9000, 2000], parents=[-1, 0],
                             columns={'depth': [3, 4]}, extra={'tasks': tasks})
        self.click_cpu_at(2000)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        self.click_cpu_at(3100)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)

    def open_tasks_data(self, count=230, mode='abs'):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        tasks = [dict(pid=index + 1, comm=('=unsafe' if index == 0 else 'task_' + str(index).zfill(4)),
                      comms=['old_' + str(index)], kind=0, cpu=0,
                      runs=[[index * 1000, index * 1000 + 500, index % 4]]) for index in range(count)]
        tasks += [dict(pid=-1, comm='<identity only>', kind=3, cpu=None),
                  dict(pid=0, comm='idle', kind=1, cpu=0), dict(pid=-1, comm='cpu fallback', kind=2, cpu=0)]
        self.open_range_data(['root', 'child'], starts=[0, 100], durations=[1000, 200], parents=[-1, 0],
                             columns={'depth': [0, 1]}, mode=mode,
                             extra={'tasks': tasks, 'offcpu': [[1, 100, None, 0, 0, 1, 0, -1, 0, -1]],
                                    'wakeups': [[-1, 250000, 2, 0, -1, -1]],
                                    'chains': [[1, 0, 12, 0, 2, 1, 1]], 'errnames': {'12': 'ENOMEM'},
                                    'overhead': {'child': 10, 'self': 5, 'source': 'test'}})
        self.page.click('[data-act=an-tasks]')

    def test_tasks_paginate_export_all_and_preserve_focused_more_on_refresh(self):
        self.open_tasks_data()
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 100)
        self.page.locator('[data-act=tasks-more]').press('Enter')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 200)
        self.assertEqual(self.ev("document.activeElement.dataset.act"), 'tasks-more')
        rows = self.download_csv('[data-act=tasks-csv]')
        self.assertEqual(len(rows), 233)
        self.assertEqual(rows[0]['task'], "'=unsafe")
        self.assertEqual(rows[0]['calls'], '2')
        self.assertEqual(rows[0]['error_origins_including_minor'], '1')
        self.assertEqual(rows[1]['open_waits'], '1')
        self.assertEqual(rows[2]['end_ns'], '250000')
        self.assertEqual(rows[-3]['start_ns'], '')
        self.assertEqual(rows[-2]['cpus'], '0')
        self.page.click('[data-act=adj]')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 200)
        self.assertEqual(self.download_csv('[data-act=tasks-csv]'), rows)
        self.page.locator('[data-act=tasks-more]').press('Space')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 233)
        self.assertTrue(self.page.locator('[data-act=tasks-more]').is_disabled())

    def test_tasks_literal_query_kind_shared_matches_and_navigation(self):
        self.open_tasks_data(count=12)
        self.page.fill('[data-task-query]', 'old_2')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 1)
        self.assertIn('task_0002', self.page.locator('.an-task-table').inner_text())
        self.page.fill('[data-task-query]', 'CPU 3')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 3)
        self.page.fill('[data-task-query]', '[')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 0)
        self.page.click('[data-act=tasks-reset]')
        self.page.select_option('[data-task-kind]', '1')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 1)
        self.assertTrue(self.page.locator('[data-task-show]').is_disabled())
        self.page.click('[data-act=tasks-reset]')
        self.ev("fgNotifyFilter('child only', new Set([2]))")
        self.page.check('[data-task-matching]')
        rows = self.download_csv('[data-act=tasks-csv]')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['matched_calls'], '1')
        self.assertEqual(rows[0]['calls'], '2')
        self.page.click('[data-act=tasks-reset]')
        self.page.locator('[data-task-show="2"]').click()
        self.assertGreater(self.ev("fgView('timeline').probe().TRACKS[2].h"), 0)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), -1)
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Tasks')

    def test_tasks_count_and_packed_exports_do_not_claim_nanosecond_measurements(self):
        for mode in ['count', 'compressed']:
            with self.subTest(mode=mode):
                self.open_tasks_data(count=4, mode=mode)
                rows = self.download_csv('[data-act=tasks-csv]')
                suffix = 'units' if mode == 'count' else 'ns'
                self.assertIn('span_' + suffix, rows[0])
                self.assertEqual(rows[0]['position_basis'], 'call_order' if mode == 'count' else 'packed_duration')
                self.assertIn('not CPU time', self.page.locator('.tl-detail').inner_text())

    def test_tasks_reuse_prepared_facts_and_keep_sort_keyboard_focus(self):
        self.open_tasks_data()
        self.ev("""() => {
            for (const task of fgView('timeline').probe().TRACKS) {
                task.runs[Symbol.iterator] = function* () { throw new Error('Task facts rescanned'); };
            }
        }""")
        self.page.locator('[data-task-sort=name]').press('Enter')
        self.assertEqual(self.ev("document.activeElement.dataset.taskSort"), 'name')
        self.assertEqual(self.page.locator('th[aria-sort=ascending]').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=tasks-csv]')), 233)
        self.page.fill('[data-task-query]', 'task_')
        self.page.type('[data-task-query]', '0')
        self.assertEqual(self.ev("document.activeElement.hasAttribute('data-task-query')"), True)
        self.assertEqual(len(self.download_csv('[data-act=tasks-csv]')), 229)
        self.page.click('[data-act=adj]')
        self.ev("fgNotifyFilter('child', new Set([2]))")
        self.page.check('[data-task-matching]')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 0)
        self.page.fill('[data-task-query]', '')
        self.assertEqual(self.download_csv('[data-act=tasks-csv]')[0]['matched_calls'], '1')

    def test_tasks_compact_layout_preserves_controls_during_horizontal_scroll(self):
        tasks = [dict(pid=1, comm='<script>long_name_' + 'x' * 600, kind=0, cpu=0)]
        self.open_range_data(['root'], extra={'tasks': tasks})
        self.page.click('[data-act=an-tasks]')
        for theme in ['light', 'dark']:
            for width, height in [(320, 700), (390, 844), (800, 420), (1500, 900)]:
                with self.subTest(theme=theme, width=width, height=height):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_timeout(60)
                    self.ev("document.querySelector('.tl-detail').scrollLeft = 5000")
                    metrics = self.ev("""() => {
                        const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                        const head = document.querySelector('.an-task-head');
                        const tools = document.querySelector('.an-task-tools').getBoundingClientRect();
                        const footer = document.querySelector('.an-task-footer').getBoundingClientRect();
                        return [document.documentElement.scrollWidth, tools.left - panel.left, footer.left - panel.left,
                            head.getBoundingClientRect().left - panel.left, document.querySelector('.an-task-name').offsetHeight,
                            getComputedStyle(head).top, document.querySelector('.an-task-table th').style.top];
                    }""")
                    self.assertLessEqual(metrics[0], width)
                    self.assertLessEqual(abs(metrics[1]), 2)
                    self.assertLessEqual(abs(metrics[2]), 2)
                    self.assertLessEqual(abs(metrics[3]), 2)
                    self.assertLessEqual(metrics[4], 40)
                    if width <= 640 or height <= 600:
                        self.assertEqual(metrics[5:], ['auto', '0px'])
        self.assertEqual(self.page.locator('.an-task-table script').count(), 0)

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

    def open_call_navigation(self, mode='abs'):
        self.open_range_data(['root_a', 'deep_first', 'grandchild', 'shallow_second', 'deep_last',
                              'root_b', 'other_parent_child', 'root_c', 'other_task', 'other_task_child'],
                             mode=mode, starts=[0, 1000, 1200, 10000, 20000, 200000, 201000, 300000, 0, 1000],
                             durations=[100000, 5000, 1000, 1000, 1000, 50000, 1000, 1000, 100000, 1000],
                             parents=[-1, 0, 1, 0, 0, -1, 5, -1, -1, 8],
                             columns={'depth': [2, 5, 6, 3, 5, 0, 3, 4, 0, 5], 'task': [0] * 8 + [1, 1],
                                      'start': [0, 1000, 200, 8800, 10000, 180000, 1000, 99000, 0, 1000]},
                             extra={'tasks': [dict(pid=1, comm='first', kind=0, cpu=0),
                                              dict(pid=2, comm='second', kind=0, cpu=1)]})
        self.ev("fgView('timeline').probe().select(0)")

    def test_recorded_navigation_real_trace_does_not_cross_parent(self):
        self.open_timeline()
        target = self.ev("""() => {
            const probe = fgView('timeline').probe();
            for (const task of probe.TRACKS) for (const row of task.rowsT) {
                for (let position = 1; position < row.length; position++) {
                    const before = row[position - 1], after = row[position];
                    if (before >= probe.N || after >= probe.N || probe.sParent[before] < 0 ||
                        probe.sParent[before] === probe.sParent[after]) continue;
                    const siblings = [];
                    for (let index = 0; index < probe.N; index++) {
                        if (probe.sTask[index] === probe.sTask[before] && probe.sParent[index] === probe.sParent[before]) siblings.push(index);
                    }
                    const next = siblings[siblings.indexOf(before) + 1];
                    return [before, next === undefined ? before : next];
                }
            }
        }""")
        self.assertIsNotNone(target)
        self.ev("index => fgView('timeline').probe().select(index)", target[0])
        self.page.locator('.tl-canvas').focus()
        self.page.keyboard.press(']')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), target[1])

    def test_recorded_navigation_follows_parent_links_across_depths_and_roots(self):
        for mode in ['abs', 'rel', 'compressed', 'count']:
            with self.subTest(mode=mode):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_call_navigation(mode)
                if mode in ['abs', 'rel']:
                    self.assertIn('4 traced calls inside', self.page.locator('.an-bd-legend').inner_text())
                self.page.locator('.tl-canvas').focus()
                for key, expected in [('ArrowDown', 1), (']', 3), (']', 4), (']', 4), ('[', 3),
                                      ('ArrowUp', 0), (']', 5), (']', 7), (']', 7), ('[', 5),
                                      ('ArrowDown', 6), (']', 6), ('ArrowUp', 5), ('[', 0), ('[', 0)]:
                    self.page.keyboard.press(key)
                    self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected, key)
                self.ev("fgView('timeline').probe().select(9)")
                for key in ['[', ']']:
                    self.page.keyboard.press(key)
                    self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 9)

    def test_recorded_navigation_native_controls_focus_filters_and_layout(self):
        self.open_call_navigation()
        self.assertTrue(self.page.locator('[data-call-nav=parent]').is_disabled())
        self.assertTrue(self.page.locator('[data-call-nav=previous]').is_disabled())
        self.page.locator('[data-call-nav=child]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        self.assertTrue(self.page.locator('[data-call-nav=child]').evaluate('(node) => node === document.activeElement'))
        self.page.locator('[data-call-nav=next]').press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        self.assertTrue(self.page.locator('[data-call-nav=next]').evaluate('(node) => node === document.activeElement'))
        self.page.locator('[data-call-nav=next]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 4)
        self.assertTrue(self.page.locator('[data-call-nav=next]').is_disabled())
        self.assertTrue(self.page.locator('.tl-canvas').evaluate('(node) => node === document.activeElement'))
        self.ev("fgView('timeline').probe().select(0)")
        self.ev("fgNotifyFilter('root only', new Set([1]))")
        self.page.locator('[data-call-nav=child]').click()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        self.page.click('[data-act=adj]')
        self.ev("fgView('timeline').probe().inline(true)")
        self.page.locator('[data-call-nav=next]').click()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        for theme in ['light', 'dark']:
            self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
            for width, height in [(1500, 900), (800, 420), (390, 844), (320, 700)]:
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_timeout(60)
                    self.ev("document.querySelector('.tl-detail').scrollTop = 0")
                    bounds = self.ev("""() => {
                        const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                        const nav = document.querySelector('.tl-call-nav').getBoundingClientRect();
                        return [nav.left - panel.left, nav.right - panel.right,
                            document.documentElement.scrollWidth, nav.height];
                    }""")
                    self.assertGreaterEqual(bounds[0], 0)
                    self.assertLessEqual(bounds[1], 1)
                    self.assertLessEqual(bounds[2], width)
                    self.assertLessEqual(bounds[3], 100)

    def test_recorded_navigation_zero_duration_ties_and_safe_target_names(self):
        unsafe = '<img src=x onerror=alert(1)>'
        self.open_range_data(['root', unsafe, 'second', 'last'], starts=[0, 0, 0, 1000],
                             durations=[10000, 0, 0, 1000], parents=[-1, 0, 0, 0], columns={'depth': [0, 3, 1, 2]})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertIn(unsafe, self.page.locator('[data-call-nav=child]').get_attribute('title'))
        self.assertEqual(self.page.locator('.tl-call-nav img').count(), 0)
        self.page.locator('.tl-canvas').focus()
        for key, expected in [('ArrowDown', 1), (']', 2), (']', 3), ('[', 2), ('[', 1)]:
            self.page.keyboard.press(key)
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected)

    def test_recorded_navigation_reuses_links_without_reading_visual_rows(self):
        count = 10000
        self.open_range_data(['root'] + ['zero_call'] * count, starts=[0] * (count + 1),
                             durations=[10000] + [0] * count, parents=[-1] + [0] * count,
                             columns={'depth': [0] + [1] * count})
        self.ev("index => fgView('timeline').probe().select(index)", count - 2)
        result = self.ev("""() => {
            const probe = fgView('timeline').probe(), saved = probe.TRACKS.map(task => task.rowsT);
            let reads = 0;
            for (const task of probe.TRACKS) task.rowsT = new Proxy(task.rowsT, {
                get(target, key) { if (typeof key === 'string' && /^[0-9]+$/.test(key)) reads++; return Reflect.get(target, key); }
            });
            try {
                const canvas = document.querySelector('.tl-canvas');
                for (let iteration = 0; iteration < 30; iteration++) {
                    canvas.dispatchEvent(new KeyboardEvent('keydown', {key: ']', bubbles: true}));
                    canvas.dispatchEvent(new KeyboardEvent('keydown', {key: '[', bubbles: true}));
                }
                return [reads, probe.sel()];
            } finally { probe.TRACKS.forEach((task, index) => { task.rowsT = saved[index]; }); }
        }""")
        self.assertEqual(result, [0, count - 2])

    def test_recorded_navigation_synthetic_frames_reach_actual_recorded_calls(self):
        self.open_range_data(['root', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [3, 6]},
                             extra={'inline': {'2': [['root', 'a.c', 10], ['inline_one', 'b.c', 20], ['inline_two', 'c.c', 30]]}})
        synthetic = self.ev("() => { const probe = fgView('timeline').probe(); return Array.from({length: probe.NA - probe.N}, (_, index) => probe.N + index); }")
        self.assertEqual(len(synthetic), 4)
        self.ev("fgView('timeline').probe().inline(true)")
        for index in synthetic:
            with self.subTest(index=index):
                self.ev("index => fgView('timeline').probe().select(index)", index)
                self.assertTrue(self.page.locator('[data-call-nav=previous]').is_disabled())
                self.assertTrue(self.page.locator('[data-call-nav=next]').is_disabled())
                self.page.locator('[data-call-nav=child]').press('Enter')
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
                self.ev("index => fgView('timeline').probe().select(index)", index)
                self.page.locator('.tl-canvas').focus()
                self.page.keyboard.press('ArrowDown')
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
                self.ev("index => fgView('timeline').probe().select(index)", index)
                self.page.keyboard.press('ArrowUp')
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)

    def open_detail_stack(self, count=320, errors=False, mode='abs', prefix='frame_'):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        names = [prefix + str(index).zfill(4) for index in range(count)]
        extra = {'strings': names + ['-12'], 'errnames': {'12': 'ENOMEM'}}
        if errors:
            extra.update(errs=[[index, 12, 0 if index == count - 1 else 1, 2, 1, 0] for index in range(count)],
                         chains=[[count - 1, 0, 12, 2, count, 1, 0]])
        self.open_range_data(names, mode=mode, starts=list(range(count)), durations=[10000 - index * 2 for index in range(count)],
                             parents=[-1] + list(range(count - 1)), columns={'depth': list(range(count)), 'ret': [count] * count}, extra=extra)
        self.ev('index => fgView("timeline").probe().select(index)', count - 1)

    def open_child_groups(self, count=225, mode='abs', prefix='child_', parent_name='parent'):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        names, starts, durations, parents, depths = [parent_name], [0], [count * 10000 + 1000], [-1], [3]
        for group in range(count):
            first = len(names)
            names += [prefix + str(group).zfill(4), 'grandchild', prefix + str(group).zfill(4), 'grandchild']
            starts += [group * 10000 + 100, group * 10000 + 200, group * 10000 + 2000, group * 10000 + 2100]
            durations += [1000, 300, 4000, 500]
            parents += [0, first, 0, first + 2]
            depths += [5, 6, 5, 6]
        self.open_range_data(names, starts=starts, durations=durations, parents=parents, mode=mode,
                             columns={'depth': depths}, extra={'overhead': {'child': 10, 'self': 5, 'source': 'test'}})
        self.ev("fgView('timeline').probe().select(0)")

    def test_child_explorer_real_trace_reaches_functions_beyond_top_eight(self):
        self.open_timeline()
        parent = self.ev("""() => { const probe = fgView('timeline').probe();
            for (let index = 0; index < probe.N; index++) if (probe.nameOf(index) === 'path_openat') return index;
        }""")
        self.ev("index => fgView('timeline').probe().select(index)", parent)
        self.assertIn('top 8 of 10 functions', self.page.locator('.tl-detail').text_content())
        self.page.locator('[data-child-open]').press('Enter')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 10)
        groups = self.download_csv('[data-child-csv=groups]')
        calls = self.download_csv('[data-child-csv=calls]')
        self.assertEqual(len(groups), 10)
        self.assertEqual(len(calls), 10)
        self.assertEqual({int(row['parent_slice']) for row in calls}, {parent})

    def test_child_explorer_complete_groups_calls_and_longest_navigation(self):
        self.open_child_groups()
        self.assertIn('top 8 of 225 functions', self.page.locator('.tl-detail').text_content())
        self.assertEqual(self.page.locator('.tl-child-preview').first.get_attribute('data-slice'), '3')
        self.page.locator('[data-child-open]').press('Space')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 100)
        groups = self.download_csv('[data-child-csv=groups]')
        calls = self.download_csv('[data-child-csv=calls]')
        self.assertEqual(len(groups), 225)
        self.assertEqual(len(calls), 450)
        self.assertEqual(groups[0]['calls'], '2')
        self.assertEqual(float(groups[0]['inclusive_ns']), 5000)
        self.assertEqual(float(groups[0]['self_ns']), 4200)
        self.assertEqual(float(groups[0]['mean_ns']), 2500)
        self.assertEqual(float(groups[0]['max_ns']), 4000)
        self.assertEqual(groups[0]['longest_slice'], '3')
        self.assertEqual({row['function'] for row in calls}, {row['function'] for row in groups})
        self.assertTrue(all(row['depth_gap'] == '1' for row in calls))
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 100)
        self.ev("window.firstChildExplorerRow = document.querySelector('.an-child-table tbody tr')")
        self.page.locator('[data-child-more]').press('Enter')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 200)
        self.assertTrue(self.ev("window.firstChildExplorerRow === document.querySelector('.an-child-table tbody tr')"))
        self.assertTrue(self.ev("document.activeElement.hasAttribute('data-child-more')"))
        self.page.locator('[data-child-show="3"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 200)
        self.assertIn('Direct recorded children', self.page.locator('.tl-detail-title').inner_text())

    def test_child_explorer_scope_query_sort_and_filter_children_not_parent(self):
        self.open_child_groups(count=12)
        self.page.click('[data-child-open]')
        self.page.locator('[data-child-function="1"]').press('Enter')
        self.assertEqual(self.ev("document.activeElement.dataset.childMode"), 'calls')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 2)
        self.assertEqual(len(self.download_csv('[data-child-csv=calls]')), 2)
        self.page.locator('[data-child-sort=start]').press('Enter')
        self.assertEqual(self.ev("document.activeElement.dataset.childSort"), 'start')
        self.assertEqual([row['slice_index'] for row in self.download_csv('[data-child-csv=calls]')], ['1', '3'])
        self.page.click('[data-child-reset]')
        self.page.fill('[data-child-query]', 'child_0009')
        self.assertEqual(len(self.download_csv('[data-child-csv=groups]')), 1)
        self.assertEqual(len(self.download_csv('[data-child-csv=calls]')), 2)
        self.page.fill('[data-child-query]', '[')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 0)
        self.page.click('[data-child-reset]')
        self.ev("fgNotifyFilter('only one direct child', new Set([2]))")
        rows = self.download_csv('[data-child-csv=groups]')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['calls'], '1')
        self.assertEqual(float(rows[0]['inclusive_ns']), 1000)
        self.assertEqual(float(rows[0]['self_ns']), 700)
        self.ev("fgNotifyFilter('only grandchild', new Set([3]))")
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 0)
        self.assertIn('No direct recorded children', self.page.locator('.tl-detail').inner_text())
        self.assertTrue(self.page.locator('[data-child-reset]').is_visible())
        self.ev("fgNotifyFilter('', null)")
        self.assertEqual(len(self.download_csv('[data-child-csv=calls]')), 24)

    def test_child_explorer_adjustment_cache_and_loaded_pages(self):
        self.open_child_groups()
        self.page.click('[data-child-open]')
        self.page.click('[data-child-more]')
        self.page.click('[data-act=adj]')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 200)
        rows = self.download_csv('[data-child-csv=groups]')
        self.assertEqual(float(rows[0]['inclusive_ns']), 2500)
        self.assertEqual(float(rows[0]['self_ns']), 2100)
        self.assertEqual(float(rows[0]['max_ns']), 2000)
        self.assertEqual(rows[0]['duration_basis'], 'adjusted_estimate')
        writes = self.ev("""() => {
            const original = Map.prototype.set; let writes = 0;
            Map.prototype.set = function(key, value) {
                if (value && typeof value.inclusive === 'number' && Array.isArray(value.ids)) writes++;
                return original.call(this, key, value);
            };
            document.querySelector('[data-child-sort=name]').click();
            document.querySelector('[data-child-mode=calls]').click();
            document.querySelector('[data-child-mode=groups]').click();
            Map.prototype.set = original; return writes;
        }""")
        self.assertEqual(writes, 0)

    def test_child_explorer_count_basis_safe_labels_and_compact_layout(self):
        for mode in ['count', 'compressed']:
            with self.subTest(mode=mode):
                self.open_child_groups(count=2, mode=mode, prefix='=unsafe<script>')
                self.page.click('[data-child-open]')
                rows = self.download_csv('[data-child-csv=groups]')
                self.assertTrue(rows[0]['function'].startswith("'=unsafe<script>"))
                suffix = 'units' if mode == 'count' else 'ns'
                self.assertEqual(float(rows[0]['inclusive_' + suffix]), 5 if mode == 'count' else 5000)
                self.assertEqual(rows[0]['position_basis'], 'call_order' if mode == 'count' else 'packed_duration')
                self.assertEqual(self.page.locator('.an-child-table script').count(), 0)
                self.assertIn('not a partition', self.page.locator('.tl-detail').inner_text())
        for theme in ['light', 'dark']:
            for width, height in [(320, 700), (390, 844), (800, 420), (1500, 900)]:
                with self.subTest(theme=theme, width=width):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_timeout(60)
                    self.ev("document.querySelector('.tl-detail').scrollLeft = 5000")
                    offsets = self.ev("""() => {
                        const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                        return ['.an-child-head', '.an-child-tools', '.an-child-footer'].map(selector =>
                            document.querySelector(selector).getBoundingClientRect().left - panel.left);
                    }""")
                    self.assertTrue(all(abs(offset) <= 2 for offset in offsets))
                    self.assertLessEqual(self.ev("document.documentElement.scrollWidth"), width)
                    if width <= 640 or height <= 600:
                        self.assertEqual(self.ev("getComputedStyle(document.querySelector('.an-child-head')).top"), 'auto')
                        self.assertEqual(self.ev("document.querySelector('.an-child-table th').style.top"), '0px')

    def test_child_explorer_long_parent_and_function_scope_stay_compact(self):
        label = '<b>very_long_function_name</b>' * 30
        self.open_child_groups(count=2, prefix=label, parent_name=label + '_parent')
        self.page.click('[data-child-open]')
        self.page.click('[data-child-function="1"]')
        for width, height in [(320, 700), (390, 844), (800, 420), (1500, 900)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_timeout(60)
                bounds = self.ev("""() => {
                    const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                    const parent = document.querySelector('.an-child-parent').getBoundingClientRect();
                    const scope = document.querySelector('.an-child-scope');
                    return [parent.left - panel.left, parent.right - panel.right, scope.offsetHeight,
                        scope.title, document.querySelector('.an-child-name').offsetHeight];
                }""")
                self.assertGreaterEqual(bounds[0], 0)
                self.assertLessEqual(bounds[1], 0)
                self.assertLessEqual(bounds[2], 40)
                self.assertEqual(bounds[3], label + '0000')
                self.assertLessEqual(bounds[4], 40)
        self.assertEqual(self.page.locator('.an-child-parent b').count(), 0)

    def test_detail_stack_pages_exports_and_preserves_expansion_on_refresh(self):
        self.open_detail_stack()
        section = self.page.locator('[data-context-list=stack]')
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        self.assertEqual(section.locator('[data-slice]').first.get_attribute('data-slice'), '0')
        self.assertNotIn('outside trace window', section.inner_text())
        first = section.locator('[data-slice]').first.element_handle()
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual(len(rows), 320)
        self.assertEqual([int(row['recorded_slice_index']) for row in rows], list(range(320)))
        self.assertTrue(all(row['path_status'] == 'complete' and row['selected_slice_index'] == '319' for row in rows))
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        self.page.locator('[data-context-more=stack]').press('Enter')
        self.assertEqual(section.locator('[data-slice]').count(), 200)
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertTrue(self.page.locator('[data-context-more=stack]').evaluate('(node) => node === document.activeElement'))
        walks = self.ev("""() => {
            const original = Array.prototype.reverse;
            let walks = 0;
            Array.prototype.reverse = function() { if (this.length === 320) walks++; return original.call(this); };
            try {
                fgNotifyFilter('leaf only', new Set([320]));
                document.querySelector('[data-act=adj]').click();
                fgView('timeline').probe().inline(true);
            } finally { Array.prototype.reverse = original; }
            return walks;
        }""")
        self.assertEqual(walks, 0)
        self.assertEqual(section.locator('[data-slice]').count(), 200)
        adjusted = self.download_csv('[data-context-export=stack]')
        self.assertEqual(float(adjusted[0]['duration_ns']), float(rows[0]['duration_ns']) / 2)
        self.assertEqual([row['start_ns'] for row in adjusted], [row['start_ns'] for row in rows])
        self.assertTrue(all(row['duration_basis'] == 'overhead_subtracted_estimate' for row in adjusted))
        self.page.locator('[data-context-more=stack]').press('Space')
        self.page.locator('[data-context-more=stack]').press('Enter')
        self.assertEqual(section.locator('[data-slice]').count(), 320)
        self.assertTrue(self.page.locator('[data-context-more=stack]').is_disabled())

    def test_detail_error_chain_is_complete_beyond_sixty_four_frames(self):
        self.open_detail_stack(errors=True)
        section = self.page.locator('[data-context-list=error]')
        self.assertIn('320 recorded frames', section.locator('.tl-sec').text_content())
        self.assertIn('ENOMEM', section.inner_text())
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        rows = self.download_csv('[data-context-export=error]')
        self.assertEqual(len(rows), 320)
        self.assertEqual([int(row['recorded_slice_index']) for row in rows], list(range(320)))
        self.assertTrue(all(row['chain_index'] == '0' and row['errno'] == '12' and row['fate'] == 'unknown' for row in rows))
        self.assertTrue(all(row['return'] == "'-12" for row in rows))
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        for expected in [200, 300, 320]:
            self.page.locator('[data-context-more=error]').press('Enter')
            self.assertEqual(section.locator('[data-slice]').count(), expected)

    def test_detail_stack_exports_typed_inline_gaps_and_actual_window_boundary(self):
        self.open_range_data(['root', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [3, 6]},
                             extra={'inline': {'2': [['root', 'a.c', 10], ['inline_one', 'b.c', 20], ['inline_two', 'c.c', 30]]}})
        self.ev("fgView('timeline').probe().select(1)")
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual([row['kind'] for row in rows], ['outside', 'traced', 'untraced', 'untraced', 'inline', 'inline', 'traced'])
        self.assertEqual(rows[0]['outside_frame_count'], '3')
        self.assertEqual([row['depth'] for row in rows if row['kind'] == 'untraced'], ['4', '5'])
        self.assertEqual([row['source_file'] for row in rows if row['kind'] == 'inline'], ['b.c', 'c.c'])
        for row in rows:
            if row['kind'] != 'traced':
                self.assertTrue(all(row[field] == '' for field in ['recorded_slice_index', 'trace_line', 'duration_ns', 'self_ns', 'start_ns', 'end_ns', 'duration_basis']))
        before = self.page.locator('[data-context-list=stack]').inner_text()
        self.ev("fgView('timeline').probe().inline(true)")
        self.assertEqual(self.page.locator('[data-context-list=stack]').inner_text(), before)

    def open_detail_children(self, count=225, inline=False):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        extra = {'inline': {'2': [['root', 'a.c', 1], ['inline_frame', 'b.c', 2]]}} if inline else None
        self.open_range_data(['root'] + ['child'] * count, starts=[0] + [1000 + index * 1000 for index in range(count)],
                             durations=[(count + 2) * 1000] + [500] * count, parents=[-1] + [0] * count,
                             callers=[None] + ['root+0x1/0x20'] * count if inline else None,
                             columns={'depth': [0] + [1 if inline else 2] * count}, extra=extra)
        self.ev("fgView('timeline').probe().select(fgView('timeline').probe().N)")

    def test_detail_ghost_children_are_complete_cached_and_incremental(self):
        self.open_detail_children()
        section = self.page.locator('[data-context-list=children]')
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        rows = self.download_csv('[data-context-export=children]')
        self.assertEqual(len(rows), 225)
        self.assertEqual([int(row['recorded_slice_index']) for row in rows], list(range(1, 226)))
        self.page.locator('[data-context-more=children]').press('Enter')
        self.assertEqual(section.locator('[data-slice]').count(), 200)
        scans = self.ev("""() => {
            const probe = fgView('timeline').probe(), kids = probe.SYN[probe.NA - probe.N - 1].kids;
            const original = Array.prototype[Symbol.iterator];
            let scans = 0;
            Array.prototype[Symbol.iterator] = function() { if (this === kids) scans++; return original.call(this); };
            try {
                fgNotifyFilter('one child only', new Set([2]));
                document.querySelector('[data-act=adj]').click();
            } finally { Array.prototype[Symbol.iterator] = original; }
            return scans;
        }""")
        self.assertEqual(scans, 0)
        self.assertEqual(section.locator('[data-slice]').count(), 200)
        adjusted = self.download_csv('[data-context-export=children]')
        self.assertEqual(float(adjusted[0]['duration_ns']), float(rows[0]['duration_ns']) / 2)
        self.assertEqual([row['start_ns'] for row in adjusted], [row['start_ns'] for row in rows])
        self.page.locator('[data-context-more=children]').press('Space')
        self.assertEqual(section.locator('[data-slice]').count(), 225)
        self.ev("fgNotifyFilter('no matching children', new Set())")
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), -1)
        self.assertFalse(self.page.locator('.tl-detail').is_visible())

    def test_detail_inline_exports_callsite_chain_without_fabricated_timings(self):
        self.open_detail_children(inline=True)
        rows = self.download_csv('[data-context-export=inline]')
        self.assertEqual([row['kind'] for row in rows], ['call_site', 'inline'])
        self.assertEqual([row['source_file'] for row in rows], ['a.c', 'b.c'])
        self.assertTrue(all(row['recorded_slice_index'] == '' and row['duration_ns'] == '' for row in rows))
        self.assertEqual(len(self.download_csv('[data-context-export=children]')), 225)
        self.assertEqual(self.page.locator('[data-context-list=children] [data-slice]').count(), 100)
        self.page.locator('[data-context-more=children]').press('Enter')
        self.page.locator('[data-context-list=children] [data-slice="150"]').press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 150)
        self.assertEqual(self.page.locator('[data-context-list=stack] [data-context-kind=inline]').count(), 1)

    def test_detail_large_synthetic_fanout_only_renders_requested_rows(self):
        self.open_detail_children(count=150000)
        self.assertEqual(self.page.locator('[data-context-list=children] [data-slice]').count(), 100)
        self.assertIn('of 150000 context rows', self.page.locator('.tl-context-footer').inner_text())
        self.page.locator('[data-context-more=children]').press('Enter')
        self.assertEqual(self.page.locator('[data-context-list=children] [data-slice]').count(), 200)

    def test_detail_long_inline_chain_is_lazy_and_fully_exportable(self):
        chain = [['root', 'root.c', 1]] + [['inline_' + str(index), 'inline.c', index + 2] for index in range(1000)]
        self.open_range_data(['root', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [0, 1]}, extra={'inline': {'2': chain}})
        self.ev("fgView('timeline').probe().select(fgView('timeline').probe().N)")
        section = self.page.locator('[data-context-list=inline]')
        self.assertEqual(section.locator('.tl-context-row').count(), 100)
        self.assertIn('of 1001 context rows', section.inner_text())
        rows = self.download_csv('[data-context-export=inline]')
        self.assertEqual(len(rows), 1001)
        self.assertEqual(rows[-1]['function'], 'inline_999')
        self.assertEqual(rows[-1]['source_line'], '1001')
        self.assertTrue(all(row['duration_ns'] == '' and row['recorded_slice_index'] == '' for row in rows))
        self.assertEqual(section.locator('.tl-context-row').count(), 100)
        self.page.locator('[data-context-more=inline]').press('Enter')
        self.assertEqual(section.locator('.tl-context-row').count(), 200)

    def test_detail_context_native_navigation(self):
        self.open_detail_stack(count=3, errors=True)
        self.page.locator('[data-context-list=error] [data-slice="1"]').press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        self.assertEqual(self.page.locator('[data-context-list=error] .cur').get_attribute('data-slice'), '1')
        self.page.locator('[data-context-list=stack] [data-slice="0"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)
        self.assertEqual(self.page.locator('[data-context-list=stack] [data-slice]').count(), 1)

    def test_detail_csv_units_and_formula_protection(self):
        for mode in ['abs', 'dur', 'count']:
            with self.subTest(mode=mode):
                self.open_detail_stack(count=3, mode=mode, prefix='=SUM(1,2)')
                rows = self.download_csv('[data-context-export=stack]')
                self.assertEqual(rows[0]['function'], "'=SUM(1,2)0000")
                unit = 'units' if mode == 'count' else 'ns'
                self.assertEqual(float(rows[0]['duration_' + unit]), 10 if mode == 'count' else 10000)
                self.assertEqual(rows[0]['position_basis'], 'relative_timestamp_span' if mode == 'abs' else 'packed_geometry' if mode == 'dur' else 'call_order_geometry')
                self.assertEqual(rows[0]['time_mode'], mode)
                if mode == 'count':
                    self.assertFalse(any(column.endswith('_ns') for column in rows[0]))

    def test_detail_context_reports_inconsistent_chain_metadata(self):
        self.open_range_data(['root', 'parent', 'origin', 'unrelated'], starts=[0, 1, 2, 20000], durations=[10000, 9000, 1000, 1000],
                             parents=[-1, 0, 1, -1], columns={'depth': [0, 1, 2, 0]},
                             extra={'errs': [[2, 12, 0, 2, 0, 0]], 'chains': [[2, 3, 12, 2, 999, 0, 0]]})
        self.ev("fgView('timeline').probe().select(2)")
        section = self.page.locator('[data-context-list=error]')
        self.assertIn('missing chain top', section.inner_text())
        self.assertIn('3 recorded frames', section.locator('.tl-sec').text_content())
        rows = self.download_csv('[data-context-export=error]')
        self.assertEqual([row['recorded_slice_index'] for row in rows], ['0', '1', '2'])
        self.assertTrue(all(row['path_status'] == 'missing_chain_top' for row in rows))
        self.assertTrue(all(row['chain_top_slice'] == '3' and row['chain_reported_frames'] == '999' for row in rows))

    def test_detail_context_detects_parent_cycles_without_a_depth_guard(self):
        self.open_range_data(['root', 'parent', 'leaf'], starts=[0, 1, 2], durations=[10000, 9000, 1000],
                             parents=[-1, 0, 1], columns={'depth': [0, 1, 2]})
        self.ev("() => { const probe = fgView('timeline').probe(); probe.sParent[0] = 2; probe.select(2); }")
        self.assertIn('cycle', self.page.locator('[data-context-list=stack]').inner_text())
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['path_status'] == 'cycle' for row in rows))

    def test_detail_context_long_names_fit_compact_windows(self):
        self.open_detail_stack(count=160, prefix='<b>long_function_name</b>' * 15)
        for theme in ['light', 'dark']:
            for width, height in [(1500, 900), (320, 700), (390, 844), (800, 420)]:
                with self.subTest(theme=theme, width=width):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.ev("document.querySelector('.tl-detail').scrollTop = 0")
                    self.page.wait_for_timeout(100)
                    close = self.page.locator('.tl-slice-head .tl-close').bounding_box()
                    self.assertGreaterEqual(close['x'], 0)
                    self.assertLessEqual(close['x'] + close['width'], width)
                    self.assertEqual(self.ev("getComputedStyle(document.querySelector('.tl-slice-head')).top"), 'auto' if width <= 640 or height <= 600 else '0px')
                    self.assertEqual(self.page.locator('.tl-context-text b').count(), 0)
                    self.assertLessEqual(self.page.locator('.tl-context-row').first.evaluate('(node) => node.offsetHeight'), 40)
                    self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                    self.assertTrue(self.page.locator('[data-context-export=stack]').is_enabled())

    def test_detail_breakdown_uses_real_parent_links_across_depth_gaps(self):
        self.open_range_data(['parent', 'real_child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             columns={'depth': [0, 3]})
        self.ev("fgView('timeline').probe().select(0)")
        legend = self.page.locator('.an-bd-legend').inner_text()
        self.assertIn('traced callees', legend)
        self.assertIn('1 traced call inside', legend)
        self.assertNotIn('NaN', self.page.locator('.tl-detail').inner_text())
        self.assertIn('real_child', self.page.locator('.tl-detail').inner_text())
        self.assertEqual(self.page.locator('.tl-detail .tl-crumb[data-slice="1"]').count(), 1)

    def test_detail_breakdown_keeps_descendants_beyond_256_levels(self):
        count = 320
        self.open_range_data(['deep_call'] * count, starts=list(range(count)),
                             durations=[10000 - index * 2 for index in range(count)],
                             parents=[-1] + list(range(count - 1)), columns={'depth': list(range(count))})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertIn('319 traced calls inside', self.page.locator('.an-bd-legend').inner_text())

    def test_detail_breakdown_discloses_overlapping_recordings_instead_of_160_percent(self):
        self.open_range_data(['parent', 'first', 'overlapping'], starts=[0, 0, 100], durations=[1000, 800, 800],
                             parents=[-1, 0, 0], columns={'depth': [0, 1, 1]})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertIn('parts exceed the recorded interval by 600 ns', self.page.locator('.an-bd-legend').inner_text())
        self.assertNotIn('160%', self.page.locator('.an-bd-legend').inner_text())
        self.assertAlmostEqual(self.ev("Array.from(document.querySelectorAll('.an-bd span')).reduce((total, part) => total + parseFloat(part.style.width), 0)"), 100)

    def open_slowfast_data(self, functions=225, depth=1, extra=None, root_sites=None, child_prefix='child_'):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        names, starts, durations, parents, depths, callers = [], [], [], [], [], []
        for root in range(10):
            parent = len(names)
            names.append('target')
            starts.append(root * 1000000)
            durations.append(800000 if root == 9 else 600000 + root * 1000)
            parents.append(-1)
            depths.append(0)
            callers.append(root_sites[root] if root_sites else None)
            for child in range(functions):
                names.append(child_prefix + str(child).zfill(4))
                starts.append(root * 1000000 + child * 1000)
                durations.append(200 + child if root == 9 else 100)
                parents.append(parent)
                depths.append(depth)
                callers.append(None)
        self.open_range_data(names, starts=starts, durations=durations, parents=parents, columns={'depth': depths}, extra=extra,
                             callers=callers if root_sites else None)
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.locator('[data-act=slowfast]').press('Enter')

    def test_detail_breakdown_partitions_irq_and_waits_without_synthetic_calls(self):
        self.open_range_data(['root', 'helper', 'irq', 'irq_child', 'child'], starts=[0, 100, 200, 300, 700],
                             durations=[10000, 1000, 400, 100, 300], parents=[-1, 0, 1, 2, 1],
                             callers=[None, 'caller_helper', None, None, None],
                             columns={'depth': [0, 3, 5, 6, 4], 'flags': [0, 0, 2, 2, 0]},
                             extra={'offcpu': [[0, 3000, 5000, 1, -1, -1, 0, 0, 0, -1],
                                               [0, 1010, 1060, 4, -1, -1, 0, 0, 1, -1]],
                                    'inline': {'5': [['caller_helper', 'a.c', 1], ['inlined', 'a.c', 2]]}})
        self.ev("fgView('timeline').probe().select(0)")
        values = self.page.locator('.an-bd span').evaluate_all('(parts) => parts.map(part => part.title)')
        self.assertEqual(values, ['own code 7.000 us', 'traced callees 550 ns', 'interrupts / softirq 400 ns', 'blocked 2.000 us', 'preempted 50 ns'])
        self.assertIn('4 traced calls inside', self.page.locator('.an-bd-legend').inner_text())
        writes = self.ev("""() => {
            const original = Map.prototype.set;
            let writes = 0;
            Map.prototype.set = function(key, value) { if (value && typeof value.wall === 'number' && typeof value.nCalls === 'number') writes++; return original.call(this, key, value); };
            document.querySelector('[data-act=adj]').click();
            fgNotifyFilter('child only', new Set([5]));
            fgView('timeline').probe().inline(true);
            Map.prototype.set = original;
            return writes;
        }""")
        self.assertEqual(writes, 0)
        self.assertEqual(self.page.locator('.an-bd span').evaluate_all('(parts) => parts.map(part => part.title)'), values)

    def test_slowfast_refresh_adjustment_bucket_scope_and_example_navigation(self):
        self.open_slowfast_data(functions=2, depth=3,
                                extra={'offcpu': [[0, 9700000, 9710000, 1, -1, -1, 0, 0, 0, -1]]})
        raw = self.download_csv('[data-act=slowfast-csv]')
        child = next(row for row in raw if row['function'] == 'child_0001')
        self.assertEqual(float(child['slow_self_per_root_ns']), 201)
        blocked = next(row for row in raw if row['kind'] == 'blocked')
        self.assertEqual(float(blocked['slow_self_per_root_ns']), 10000)
        self.assertTrue(all(row['name_index'] == '' or int(row['name_index']) < 3 for row in raw))
        self.page.locator('[data-sf-example]').first.press('Enter')
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'target')
        self.assertGreater(self.ev("fgView('timeline').probe().sel()"), 0)
        self.page.locator('[data-act=adj]').click()
        adjusted = self.download_csv('[data-act=slowfast-csv]')
        child = next(row for row in adjusted if row['function'] == 'child_0001')
        self.assertEqual(float(child['slow_self_per_root_ns']), 100.5)
        self.assertEqual(child['value_basis'], 'overhead_subtracted_estimate')
        blocked_adjusted = next(row for row in adjusted if row['kind'] == 'blocked')
        self.assertEqual(blocked_adjusted['slow_self_per_root_ns'], blocked['slow_self_per_root_ns'])
        self.assertEqual(blocked_adjusted['value_basis'], 'recorded_scheduling_geometry')
        option = self.page.locator('[data-act=dist-bucket] option').nth(1).get_attribute('value')
        self.page.locator('[data-act=dist-bucket]').select_option(option)
        self.assertEqual(self.download_csv('[data-act=slowfast-csv]'), adjusted)
        self.ev("fgNotifyFilter('roots only', new Set([1,4,7,10,13,16,19,22,25,28]))")
        self.assertEqual(self.page.locator('.an-slowfast-table').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 6)
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertIn('at least 6 matching calls', self.page.locator('.an-slowfast').inner_text())
        self.ev("fgNotifyFilter('', null)")
        self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 6)

    def test_slowfast_cache_survives_sort_export_collapse_and_site_changes(self):
        self.open_slowfast_data(functions=30, root_sites=['site_a'] * 7 + ['site_b'] * 3)
        writes = self.ev("""() => {
            const original = Map.prototype.set;
            let writes = 0;
            Map.prototype.set = function(key, value) { if (value && typeof value.calls === 'number' && typeof value.self === 'number' && 'example' in value) writes++; return original.call(this, key, value); };
            document.querySelector('[data-sf-sort=name] button').click();
            document.querySelector('[data-act=slowfast]').click();
            document.querySelector('[data-act=slowfast]').click();
            Map.prototype.set = original;
            return writes;
        }""")
        self.assertEqual(writes, 0)
        self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 34)
        self.page.locator('tr[data-site=site_a] button').press('Enter')
        rows = self.download_csv('[data-act=slowfast-csv]')
        self.assertTrue(all(row['population_calls'] == '7' and row['call_site_filter'] == 'site_a' for row in rows))
        self.page.locator('[data-act=dist-reset-site]').press('Enter')
        self.assertTrue(all(row['population_calls'] == '10' for row in self.download_csv('[data-act=slowfast-csv]')))

    def test_slowfast_ties_recursion_and_zero_time_count_differences(self):
        self.open_range_data(['target'] * 10, starts=[0] * 10, durations=[1000] * 10,
                             parents=[-1] + list(range(9)), columns={'depth': list(range(10))})
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.locator('[data-act=slowfast]').press('Enter')
        cohorts = self.download_csv('[data-act=slowfast-cohorts-csv]')
        fast = {int(row['slice_index']) for row in cohorts if row['cohort'] == 'fast'}
        slow = {int(row['slice_index']) for row in cohorts if row['cohort'] == 'slow'}
        self.assertEqual(fast, {5, 6, 7, 8, 9})
        self.assertEqual(slow, {0})
        self.assertFalse(fast & slow)
        function = next(row for row in self.download_csv('[data-act=slowfast-csv]') if row['kind'] == 'function')
        self.assertEqual(float(function['fast_calls_per_root']), 2)
        self.assertEqual(float(function['slow_calls_per_root']), 9)
        self.assertEqual(float(function['fast_self_per_root_ns']), 800)
        self.assertEqual(float(function['slow_self_per_root_ns']), 1000)
        self.ev("fgSwitchView('text')")
        self.page.reload()
        self.open_range_data(['target', 'zero_time'] + ['target'] * 9, starts=[0, 0] + list(range(1000, 10000, 1000)),
                             durations=[1000, 0] + [1000] * 9, parents=[-1, 0] + [-1] * 9,
                             columns={'depth': [0, 1] + [0] * 9})
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.locator('[data-act=slowfast]').press('Enter')
        row = next(row for row in self.download_csv('[data-act=slowfast-csv]') if row['kind'] == 'function')
        self.assertEqual(row['function'], 'zero_time')
        self.assertEqual(float(row['delta_self_per_root_ns']), 0)
        self.assertEqual(float(row['slow_calls_per_root']), 1)

    def test_slowfast_export_formula_protection_and_units(self):
        for mode in ['abs', 'count', 'dur']:
            with self.subTest(mode=mode):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_range_data(['target', '=SUM(1,2)'] * 10, mode=mode, starts=[index * 3000 for index in range(20)],
                                     durations=[6000, 1000] * 10, parents=[parent for index in range(10) for parent in [-1, index * 2]],
                                     columns={'depth': [0, 1] * 10}, extra={'tasks': [dict(pid=1, comm='@worker', kind=0, cpu=0)]})
                self.ev("fgView('timeline').probe().dist(0)")
                self.page.locator('[data-act=slowfast]').press('Enter')
                rows = self.download_csv('[data-act=slowfast-csv]')
                row = next(row for row in rows if row['kind'] == 'function')
                self.assertEqual(row['function'], "'=SUM(1,2)")
                unit = 'units' if mode == 'count' else 'ns'
                self.assertEqual(float(row['fast_self_per_root_' + unit]), 1 if mode == 'count' else 1000)
                cohort = self.download_csv('[data-act=slowfast-cohorts-csv]')[0]
                self.assertEqual(cohort['comm'], "'@worker")
                self.assertEqual(cohort['time_mode'], mode)
                if mode == 'count':
                    self.assertFalse(any(column.endswith('_ns') for column in row))
                elif mode == 'dur':
                    self.assertEqual(next(row for row in rows if row['kind'] == 'irq')['value_basis'], 'packed_geometry')

    def test_slowfast_compact_layout_sorts_and_examples_remain_reachable(self):
        self.open_slowfast_data(functions=5, child_prefix='very_long_function_' * 20)
        for theme in ['light', 'dark']:
            for width, height in [(1500, 900), (320, 700), (800, 420)]:
                with self.subTest(theme=theme, width=width):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.ev("() => { const panel = document.querySelector('.tl-detail'); panel.scrollTop = 0; panel.scrollLeft = panel.scrollWidth; }")
                    self.page.wait_for_timeout(100)
                    close = self.page.locator('.an-dist-head .tl-close').bounding_box()
                    self.assertGreaterEqual(close['x'], 0)
                    self.assertLessEqual(close['x'] + close['width'], width)
                    for selector in ['.an-slowfast-tools', '.an-slowfast-footer', '.an-slowfast-hint']:
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.page.locator('[data-sf-sort=name] button').press('Enter')
                    self.page.locator('[data-sf-example]').first.press('Space')
                    self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'target')
                    self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                    self.assertEqual(self.ev("getComputedStyle(document.querySelector('.an-dist-head')).top"),
                                     'auto' if width <= 640 or height <= 600 else '0px')
                    self.assertEqual(self.ev("document.querySelector('.an-slowfast-table th').style.top"),
                                     '0px' if width <= 640 or height <= 600 else str(self.ev("document.querySelector('.an-dist-head').offsetHeight")) + 'px')
                    self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 9)
                    self.assertLessEqual(self.page.locator('.an-slowfast-table tr[data-sf-kind=function]').first.evaluate('(node) => node.offsetHeight'), 48)

    def test_slowfast_large_fanout_paginates_without_spreading_arguments(self):
        count = 150000
        self.open_range_data(['target'] * 6 + ['child_' + str(index) for index in range(count)],
                             starts=[index * 2000 for index in range(6)] + [11000 + index * 1000 for index in range(count)],
                             durations=[1000] * 5 + [(count + 1) * 1000] + [500] * count,
                             parents=[-1] * 6 + [5] * count, columns={'depth': [0] * 6 + [1] * count})
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.locator('[data-act=slowfast]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 100)
        self.assertIn('of 150004 profile rows', self.page.locator('.an-slowfast-footer').inner_text())
        self.page.locator('[data-act=slowfast-more]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 200)

    def test_slowfast_paginates_sorts_exports_and_retains_population(self):
        self.open_slowfast_data()
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 100)
        first = self.page.locator('.an-slowfast-table tbody tr').first.element_handle()
        rows = self.download_csv('[data-act=slowfast-csv]')
        self.assertEqual(len(rows), 229)
        child = next(row for row in rows if row['function'] == 'child_0224')
        self.assertEqual(float(child['fast_calls_per_root']), 1)
        self.assertEqual(float(child['slow_calls_per_root']), 1)
        self.assertEqual(float(child['fast_self_per_root_ns']), 100)
        self.assertEqual(float(child['slow_self_per_root_ns']), 424)
        self.assertEqual(float(child['delta_self_per_root_ns']), 324)
        self.page.locator('[data-act=slowfast-more]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 200)
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertTrue(self.page.locator('[data-act=slowfast-more]').evaluate('(node) => node === document.activeElement'))
        self.page.locator('[data-act=slowfast-more]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 229)
        cohorts = self.download_csv('[data-act=slowfast-cohorts-csv]')
        self.assertEqual(len(cohorts), 6)
        self.assertEqual(sum(row['cohort'] == 'fast' for row in cohorts), 5)
        self.assertEqual(sum(row['cohort'] == 'slow' for row in cohorts), 1)
        self.page.locator('.an-slowfast-table th[data-sf-sort=name] button').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 100)
        self.assertEqual(self.page.locator('.an-slowfast-table th[data-sf-sort=name]').get_attribute('aria-sort'), 'ascending')
        self.page.locator('[data-act=slowfast]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table').count(), 0)
        self.page.locator('[data-act=slowfast]').press('Enter')
        self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 229)

    def test_slowfast_numeric_sorts_export_complete_profiles(self):
        for key, field in [('fastCalls', 'fast_calls_per_root'), ('slowCalls', 'slow_calls_per_root'),
                           ('fastSelf', 'fast_self_per_root_ns'), ('slowSelf', 'slow_self_per_root_ns'),
                           ('delta', 'delta_self_per_root_ns')]:
            with self.subTest(sort=key):
                self.open_slowfast_data()
                self.page.locator('.an-slowfast-table th[data-sf-sort=name] button').press('Enter')
                button = self.page.locator(f'.an-slowfast-table th[data-sf-sort={key}] button')
                button.press('Enter')
                values = [float(row[field] or 0) for row in self.download_csv('[data-act=slowfast-csv]')]
                self.assertEqual(len(values), 229)
                self.assertEqual(values, sorted(values, reverse=True))
                button.press('Enter')
                values = [float(row[field] or 0) for row in self.download_csv('[data-act=slowfast-csv]')]
                self.assertEqual(len(values), 229)
                self.assertEqual(values, sorted(values))

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

    def open_dense_pixels(self, depth=1, inline=False, label='dense_child', mode='abs'):
        self.open_range_data(['root'] + [label] * 6, starts=[0, 100000, 100001, 100002, 100800, 100801, 100802],
                             durations=[1000000] + [1] * 6, parents=[-1] + [0] * 6, mode=mode,
                             callers=[None] + ['root+0x1/0x20'] * 6 if inline else None,
                             columns={'depth': [0] + [depth] * 6},
                             extra={'inline': {'2': [['root', 'a.c', 1], ['inline_frame', 'b.c', 2]]}} if inline else None)

    def canvas_pixel(self, index, at_start=False):
        return self.ev("""({index, at_start}) => {
            const probe = fgView('timeline').probe(), canvas = document.querySelector('.tl-canvas');
            const bounds = canvas.getBoundingClientRect(), point = probe.xyOf(index), ratio = window.devicePixelRatio || 1;
            if (at_start) { const view = probe.view(); point[0] = bounds.left + (probe.sStart[index] - view[0]) * bounds.width / (view[1] - view[0]); }
            const localX = Math.floor(point[0] - bounds.left) + 0.5, localY = point[1] - bounds.top;
            return {color: [...canvas.getContext('2d').getImageData(Math.floor(localX * ratio), Math.floor(localY * ratio), 1, 1).data],
                point: [bounds.left + localX, bounds.top + localY]};
        }""", {'index': index, 'at_start': at_start})

    def test_dense_pixels_keep_matching_members_bright_without_brightening_neighbors(self):
        self.open_dense_pixels()
        self.page.wait_for_timeout(80)
        bright = [self.canvas_pixel(index)['color'] for index in [1, 4]]
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.page.wait_for_timeout(80)
        dimmed = [self.canvas_pixel(index)['color'] for index in [1, 4]]
        self.assertNotEqual(bright, dimmed)
        for line in [6, 5]:
            self.ev("line => fgNotifyFilter('one member', new Set([line]))", line)
            self.page.wait_for_timeout(80)
            self.assertEqual(self.canvas_pixel(1)['color'], dimmed[0])
            self.assertEqual(self.canvas_pixel(4)['color'], bright[1])
        self.page.fill('.tl-search', '^root$')
        self.page.wait_for_timeout(220)
        self.assertEqual(self.canvas_pixel(4)['color'], dimmed[1])
        self.page.fill('.tl-search', '')
        self.page.wait_for_timeout(80)
        self.assertEqual(self.canvas_pixel(4)['color'], bright[1])

    def test_dense_pixel_hover_and_click_explain_and_select_matching_representative(self):
        label = '<img src=x onerror=alert(1)>'
        self.open_dense_pixels(label=label)
        self.ev("fgNotifyFilter('middle member', new Set([3]))")
        self.page.wait_for_timeout(80)
        point = self.canvas_pixel(1)['point']
        self.page.mouse.move(*point)
        self.assertIn('3 frames', self.page.locator('.tl-tip').inner_text())
        self.assertIn('1 match', self.page.locator('.tl-tip').inner_text())
        self.assertIn(label, self.page.locator('.tl-tip .t-name').inner_text())
        self.assertEqual(self.page.locator('.tl-tip img').count(), 0)
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 2)

    def test_dense_pixel_keeps_exact_wide_hits_and_zero_duration_boundary_calls(self):
        self.open_range_data(['root', 'thin', 'wanted', 'wide'], starts=[0, 100000, 100001, 100002],
                             durations=[1000000, 1, 1, 200000], parents=[-1, 0, 0, 0], columns={'depth': [0, 1, 1, 1]})
        self.ev("fgNotifyFilter('thin match', new Set([3]))")
        point = self.canvas_pixel(1)['point']
        self.ev("""point => {
            const event = new MouseEvent('mousemove', {clientX: point[0], clientY: point[1], bubbles: true});
            Object.defineProperty(event, 'clientX', {value: point[0]});
            document.querySelector('.tl-canvas').dispatchEvent(event);
        }""", point)
        self.assertEqual(self.page.locator('.tl-tip .t-name').inner_text(), 'wide')
        self.assertNotIn('Dense pixel', self.page.locator('.tl-tip').inner_text())
        self.page.mouse.click(point[0] + 1, point[1])
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        self.ev("fgSwitchView('text')")
        self.page.reload()
        self.open_range_data(['root', 'zero', 'wanted'], starts=[0, 0, 0], durations=[1000000, 0, 0],
                             parents=[-1, 0, 0], columns={'depth': [0, 1, 1]})
        self.ev("fgNotifyFilter('zero match', new Set([3]))")
        self.page.wait_for_timeout(80)
        point = self.canvas_pixel(1)['point']
        self.page.mouse.move(*point)
        self.assertIn('2 frames', self.page.locator('.tl-tip').inner_text())
        self.assertIn('1 match', self.page.locator('.tl-tip').inner_text())
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 2)

    def test_dense_synthetic_frames_match_any_child_with_search_filter_intersection(self):
        for inline in [False, True]:
            with self.subTest(inline=inline):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_dense_pixels(depth=1 if inline else 3, inline=inline)
                if inline:
                    self.ev("fgView('timeline').probe().inline(true)")
                synthetic = self.ev("fgView('timeline').probe().N")
                self.ev("fgNotifyFilter('all children', new Set([2, 3, 4, 5, 6, 7]))")
                self.page.wait_for_timeout(120)
                bright = self.canvas_pixel(synthetic, at_start=True)['color']
                self.ev("fgNotifyFilter('later child', new Set([6]))")
                self.page.wait_for_timeout(80)
                self.assertEqual(self.canvas_pixel(synthetic, at_start=True)['color'], bright)
                self.page.fill('.tl-search', '^root$')
                self.page.wait_for_timeout(220)
                self.assertNotEqual(self.canvas_pixel(synthetic, at_start=True)['color'], bright)
                self.page.fill('.tl-search', '')
                self.page.wait_for_timeout(80)
                self.assertEqual(self.canvas_pixel(synthetic, at_start=True)['color'], bright)
                self.page.click('[data-act=adj]')
                self.page.wait_for_timeout(80)
                self.assertEqual(self.canvas_pixel(synthetic, at_start=True)['color'], bright)

    def test_dense_synthetic_hover_and_filter_facts_are_cached_across_repaints(self):
        for inline in [False, True]:
            with self.subTest(inline=inline):
                self.open_detail_children(count=30000, inline=inline)
                if inline:
                    self.ev("fgView('timeline').probe().inline(true)")
                synthetic = self.ev("fgView('timeline').probe().N")
                self.ev("fgView('timeline').probe().select(-1)")
                self.ev("""() => {
                    const kids = fgView('timeline').probe().SYN[0].kids;
                    const iterate = kids[Symbol.iterator], some = kids.some;
                    window.syntheticGeometryWalks = 0; window.syntheticFilterWalks = 0;
                    kids[Symbol.iterator] = function() { window.syntheticGeometryWalks++; return iterate.call(this); };
                    kids.some = function(...args) { window.syntheticFilterWalks++; return some.apply(this, args); };
                    fgNotifyFilter('last child', new Set([30001]));
                }""")
                self.page.wait_for_timeout(80)
                for step in range(6):
                    point = self.canvas_pixel(synthetic)['point']
                    self.page.mouse.move(point[0] + step, point[1])
                self.page.click('[data-act=adj]')
                self.page.set_viewport_size({'width': 1400, 'height': 850})
                self.page.wait_for_timeout(80)
                point = self.canvas_pixel(synthetic)['point']
                self.page.mouse.move(*point)
                self.assertEqual(self.ev('window.syntheticGeometryWalks'), 0)
                self.assertEqual(self.ev('window.syntheticFilterWalks'), 1)
                self.page.fill('.tl-search', '^child$')
                self.page.wait_for_timeout(220)
                self.page.mouse.move(point[0] + 1, point[1])
                self.assertEqual(self.ev('window.syntheticGeometryWalks'), 0)
                self.assertEqual(self.ev('window.syntheticFilterWalks'), 2)

    def test_dense_matching_prefix_reuses_row_facts_for_hover_and_paint(self):
        count = 30000
        self.open_range_data(['root'] + ['child'] * count, starts=[0] + [100000] * count,
                             durations=[1000000] + [0] * count, parents=[-1] + [0] * count,
                             columns={'depth': [0] + [1] * count})
        self.ev("fgNotifyFilter('last child', new Set([30001]))")
        self.page.wait_for_timeout(100)
        self.ev("""() => {
            const probe = fgView('timeline').probe(), row = probe.TRACKS[0].rows[1];
            window.denseRowReads = 0;
            probe.TRACKS[0].rows[1] = new Proxy(row, {
                get(target, key) {
                    if (typeof key === 'string' && /^[0-9]+$/.test(key)) window.denseRowReads++;
                    return Reflect.get(target, key, target);
                }
            });
        }""")
        point = self.canvas_pixel(1)['point']
        self.page.mouse.move(*point)
        self.assertIn('30000 frames', self.page.locator('.tl-tip').inner_text())
        self.assertIn('1 match', self.page.locator('.tl-tip').inner_text())
        self.ev('window.denseRowReads = 0')
        for offset in [0.1, 0.2, 0.3, 0.4]:
            self.page.mouse.move(point[0] + offset, point[1])
            self.page.wait_for_timeout(25)
        self.assertLess(self.ev('window.denseRowReads'), 1000)
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), count)

    def test_dense_pixel_highlights_survive_time_modes_themes_and_device_scale(self):
        for mode in ['abs', 'rel', 'compressed', 'count']:
            with self.subTest(mode=mode):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_dense_pixels(mode=mode)
                self.page.fill('.tl-search', '^dense_child$')
                self.page.wait_for_timeout(220)
                for theme in ['light', 'dark']:
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.wait_for_timeout(80)
                    self.ev("fgNotifyFilter('all children', new Set([2, 3, 4, 5, 6, 7]))")
                    self.page.wait_for_timeout(80)
                    bright = self.canvas_pixel(4)['color']
                    self.ev("fgNotifyFilter('last child', new Set([7]))")
                    self.page.wait_for_timeout(80)
                    self.assertEqual(self.canvas_pixel(4)['color'], bright)
        context = self.browser.new_context(viewport={'width': 800, 'height': 600}, device_scale_factor=2)
        original = self.page
        try:
            self.page = context.new_page()
            self.page.on('pageerror', lambda error: self.errors.append(str(error)))
            self.page.goto(self.url)
            self.open_dense_pixels()
            self.ev("fgNotifyFilter('last child', new Set([4]))")
            self.page.wait_for_timeout(100)
            point = self.canvas_pixel(1)['point']
            self.page.mouse.move(*point)
            self.assertIn('3 frames', self.page.locator('.tl-tip').inner_text())
            self.assertIn('1 match', self.page.locator('.tl-tip').inner_text())
            self.page.mouse.click(*point)
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        finally:
            context.close()
            self.page = original

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
