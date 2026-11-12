"""Explicit shared-filter submission, draft feedback and compact Timeline tools."""

from itertools import product
from pathlib import Path
import unittest

import helpers
from test_uftrace_import import capture, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TimelineFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.kernel.run(helpers.fixture('pi4_sched'), '--timeline', '--filter', out='report.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), '--timeline', out='unfiltered.html')
        for mode in ('user', 'mixed'):
            records = [record for record in capture() if mode == 'mixed' or record['space'] == 'user']
            trace = Path(cls.kernel.dir, mode + '.jsonl')
            trace.write_text(stream(records))
            cls.kernel.run(str(trace), '--timeline', '--filter', out=mode + '.html')
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            cls.playwright.stop()
            cls.kernel.close()
            raise unittest.SkipTest(str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.kernel.close()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 960})
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open_report(self, filename='report.html'):
        self.page.goto(Path(self.kernel.dir, filename).as_uri())
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.locator('.tl-canvas').wait_for()
        helpers.open_timeline_tools(self.page)

    def apply_query(self, query):
        self.page.locator('.tl-ftext').fill(query)
        self.page.locator('[data-act=fapply]').click()
        self.page.wait_for_function('query => fgFilterState.text === query && !fgFilterProgress', arg=query)

    def active_query(self):
        return self.page.evaluate('fgFilterState.text')

    def test_find_and_filter_have_distinct_scopes_and_discoverable_apply(self):
        self.assertIn('Find function', self.page.locator('.tl-search').get_attribute('placeholder'))
        self.assertIn('does not filter', self.page.locator('#tlSearchHelp').text_content())
        self.assertTrue(self.page.get_by_role('group', name='Filter calls', exact=True).is_visible())
        self.assertTrue(self.page.locator('.tl-ftext').evaluate('element => element === document.activeElement'))
        self.assertTrue(self.page.get_by_role('button', name='Apply', exact=True).is_disabled())
        self.assertIn('All ', self.page.locator('.tl-fcount').inner_text())
        self.assertFalse(self.page.locator('.tl-fdraft').is_visible())
        self.assertFalse(self.page.locator('.tl-fcurrent').is_visible())
        self.assertFalse(self.page.locator('.tl-filter-help').evaluate('element => element.open'))

    def test_typing_only_updates_draft_without_filtering_or_loading(self):
        self.page.evaluate('''() => {
            window.filterCalls = 0;
            const notify = fgOnFilter;
            fgOnFilter = () => { filterCalls++; notify(); };
        }''')
        self.page.locator('.tl-ftext').fill('comm:sh')
        self.assertEqual(self.active_query(), '')
        self.assertEqual(self.page.evaluate('filterCalls'), 0)
        self.assertTrue(self.page.locator('.tl-fdraft').is_visible())
        self.assertTrue(self.page.locator('[data-act=fapply]').is_enabled())
        self.assertIn('All ', self.page.locator('.tl-fcount').inner_text())

    def test_apply_updates_shared_state_count_and_applied_query(self):
        self.apply_query('comm:sh')
        self.assertEqual(self.page.locator('#filterInput').input_value(), 'comm:sh')
        self.assertIn(' of ', self.page.locator('.tl-fcount').inner_text())
        self.assertEqual(self.page.locator('.tl-fcurrent code').inner_text(), 'comm:sh')
        self.assertFalse(self.page.locator('.tl-fdraft').is_visible())
        self.assertTrue(self.page.locator('[data-act=fapply]').is_disabled())
        self.assertEqual(self.page.locator('.tl-more-filter').inner_text().strip(), '· Filter on')

    def test_escape_discards_draft_and_restores_active_query(self):
        self.apply_query('comm:sh')
        self.page.locator('.tl-ftext').fill('pid:99999999')
        self.page.locator('.tl-ftext').press('Escape')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'comm:sh')
        self.assertEqual(self.active_query(), 'comm:sh')
        self.assertFalse(self.page.locator('.tl-fdraft').is_visible())
        self.assertTrue(self.page.locator('[data-act=fapply]').is_disabled())

    def test_draft_survives_menu_close_and_track_visibility_toggle(self):
        self.apply_query('comm:sh')
        self.page.locator('.tl-ftext').fill('func:mutex')
        self.page.locator('[data-act=options]').click()
        helpers.open_timeline_tools(self.page)
        self.page.locator('[data-act=fhide]').click()
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'func:mutex')
        self.assertEqual(self.active_query(), 'comm:sh')
        self.assertTrue(self.page.locator('.tl-fdraft').is_visible())
        self.assertEqual(self.page.locator('[data-act=fhide]').get_attribute('aria-pressed'), 'true')

    def test_clear_removes_draft_and_applied_query_but_not_function_search(self):
        self.apply_query('comm:sh')
        self.page.locator('.tl-search').fill('mutex')
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('func:missing')
        self.page.locator('[data-act=fclear]').click()
        self.assertEqual(self.active_query(), '')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), '')
        self.assertEqual(self.page.locator('.tl-search').input_value(), 'mutex')
        self.assertFalse(self.page.locator('.tl-more-filter').is_visible())
        self.assertFalse(self.page.locator('.tl-fdraft').is_visible())
        self.assertIn('All ', self.page.locator('.tl-fcount').inner_text())

    def test_no_matches_has_recovery_hint_and_track_hiding_is_explicit(self):
        self.apply_query('func:fg_no_such_function_987654')
        self.assertIn('0 of ', self.page.locator('.tl-fcount').inner_text())
        self.assertIn('Adjust the filter or Clear', self.page.locator('.tl-fcount').inner_text())
        self.page.locator('[data-act=fhide]').click()
        self.assertTrue(self.page.evaluate('''() => {
            const tracks = Object.values(fgView('timeline').probe().TRACKS);
            return tracks.some(track => track.hidden) && tracks.every(track => track.lo < 0 || track.hidden);
        }'''))
        self.assertTrue(self.page.locator('.tl-canvas').is_visible())

    def test_shortcut_characters_in_filter_edit_do_not_change_view(self):
        self.page.keyboard.type('m?')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'm?')
        self.assertFalse(self.page.locator('#timelineView').evaluate('element => element.classList.contains("tl-fullscreen")'))
        self.assertFalse(self.page.locator('.tl-help').is_visible())

    def test_space_examples_follow_user_only_and_mixed_recordings(self):
        for mode in ('user', 'mixed'):
            with self.subTest(mode=mode):
                self.open_report(mode + '.html')
                self.page.locator('.tl-filter-help summary').click()
                self.assertEqual(self.page.locator('#tlFilterExample option[value="space:user"]').count(), 1)
                self.assertEqual(self.page.locator('#tlFilterExample option[value="space:kernel"]').count(), int(mode == 'mixed'))
                self.assertEqual(self.page.locator('#tlFilterExample option[value="graph:main"]').count(), int(mode == 'user'))
                self.page.locator('#tlFilterExample').select_option('space:user')
                self.assertEqual(self.active_query(), '')
                self.page.locator('.tl-ftext').press('Enter')
                self.assertEqual(self.active_query(), 'space:user')
                self.assertIn(' of ', self.page.locator('.tl-fcount').inner_text())
                self.assertFalse(self.page.locator('.tl-fdraft').is_visible())

    def test_examples_are_drafts_and_apply_requires_explicit_action(self):
        self.page.locator('.tl-filter-help summary').press('Enter')
        self.page.locator('#tlFilterExample').select_option('pid:1234')
        self.assertEqual(self.active_query(), '')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'pid:1234')
        self.assertTrue(self.page.locator('.tl-fdraft').is_visible())
        self.assertTrue(self.page.locator('.tl-ftext').evaluate('element => element === document.activeElement'))
        self.page.locator('.tl-ftext').press('Tab')
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'fapply')
        self.page.keyboard.press('Enter')
        self.assertEqual(self.active_query(), 'pid:1234')
        self.assertEqual(self.page.locator('#tlFilterExample option[value="space:user"]').count(), 0)
        self.assertEqual(self.page.locator('#tlFilterExample option[value="space:kernel"]').count(), 1)

    def test_external_shared_filter_sync_keeps_newer_draft_and_escape_restores_it(self):
        self.page.evaluate("fgNotifyFilter('cpu:0', new Set([1]))")
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'cpu:0')
        self.page.locator('.tl-ftext').fill('func:read')
        self.page.evaluate("fgNotifyFilter('comm:sh', new Set([2]))")
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'func:read')
        self.assertEqual(self.page.locator('.tl-fcurrent code').inner_text(), 'comm:sh')
        self.page.locator('.tl-ftext').press('Escape')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'comm:sh')

    def test_search_navigates_without_changing_applied_filter(self):
        self.apply_query('comm:sh')
        before = self.page.evaluate('Array.from(fgFilterState.lines)')
        self.page.locator('.tl-search').fill('mutex')
        self.page.locator('.tl-search').press('Enter')
        self.assertEqual(self.active_query(), 'comm:sh')
        self.assertEqual(self.page.evaluate('Array.from(fgFilterState.lines)'), before)
        self.page.locator('.tl-search').press('Escape')
        self.assertEqual(self.active_query(), 'comm:sh')

    def test_applied_expression_is_escaped_and_preserved_verbatim(self):
        query = 'func:<img src=x onerror="window.filterInjected=1">' + 'x' * 1000
        self.page.evaluate('query => fgNotifyFilter(query, new Set())', query)
        self.assertEqual(self.page.locator('.tl-fcurrent code').inner_text(), query)
        self.assertEqual(self.page.locator('.tl-fcurrent img').count(), 0)
        self.assertIsNone(self.page.evaluate('window.filterInjected'))

    def test_filter_controls_fit_themes_and_touch_viewports(self):
        screenshots = Path('/tmp/fg-filter-review')
        screenshots.mkdir(exist_ok=True)
        self.context.close()
        for theme, (width, height) in product(('light', 'dark'), ((1440, 960), (390, 844), (844, 390))):
            with self.subTest(theme=theme, viewport=(width, height)):
                self.context = self.browser.new_context(viewport={'width': width, 'height': height}, has_touch=True)
                self.page = self.context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.open_report()
                self.page.evaluate('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                self.apply_query('func:' + 'x' * 180)
                self.page.locator('.tl-filter-help summary').click()
                self.page.locator('.tl-ftext').fill('duration:>10 AND comm:sh')
                facts = self.page.evaluate('''() => {
                    const menu = document.querySelector('.tl-options');
                    const input = document.querySelector('.tl-ftext').getBoundingClientRect();
                    const apply = document.querySelector('[data-act=fapply]').getBoundingClientRect();
                    return {overflow:menu.scrollWidth - menu.clientWidth, input:input.width,
                        inputHeight:input.height, applyHeight:apply.height,
                        compact:document.getElementById('timelineView').classList.contains('tl-compact')};
                }''')
                self.assertLessEqual(facts['overflow'], 1)
                self.assertGreater(facts['input'], 170)
                if facts['compact']:
                    self.assertGreaterEqual(facts['inputHeight'], 44)
                    self.assertGreaterEqual(facts['applyHeight'], 44)
                self.page.screenshot(path=str(screenshots / f'filter-{theme}-{width}x{height}.png'))
                self.context.close()

    def test_report_without_filter_keeps_find_and_hides_shared_filter(self):
        self.open_report('unfiltered.html')
        self.assertFalse(self.page.locator('.tl-filterbar').is_visible())
        self.assertTrue(self.page.locator('.tl-search').is_visible())
        self.assertFalse(self.page.locator('.tl-more-filter').is_visible())
        self.assertTrue(self.page.evaluate('''() => {
            const active = document.activeElement;
            return document.querySelector('.tl-options').contains(active) && active.getClientRects().length > 0;
        }'''))
