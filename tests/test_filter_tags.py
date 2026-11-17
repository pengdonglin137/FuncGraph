"""Filter chips retain complete predicates, keyboard focus and bounded layout."""
import json
from pathlib import Path
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


REPORTS = ('plain.html', 'index.html', 'sidecar/index.html', 'sidecar/index.html?text=windowed')


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class FilterTagsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        trace = Path(cls.kernel.dir, 'tags.txt')
        lines = ['# tracer: function_graph\n']
        for mask in range(8):
            cpu = 1 if mask & 1 else 10
            pid = 7 if mask & 2 else 70
            comm = 'worker' if mask & 4 else 'other'
            lines.append(f'  {mask + 1:.6f} | {cpu:3}) {comm}-{pid} |  ...1. |   {mask + 1}.000 us    |  '
                         f'probe_{mask}(value={mask}); /* <-outer+0x4/0x80 */\n')
        trace.write_text(''.join(lines))
        options = ['--no-config', '--filter', '--enable-fold', '--func-links']
        cls.kernel.run(str(trace), *options, out='plain.html')
        cls.kernel.run(str(trace), *options, '--timeline', out='index.html')
        cls.kernel.run(str(trace), *options, '--timeline', '--report-mode', 'sidecar', out='sidecar')
        cls.server = create_server(Path(cls.kernel.dir))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch()
        cls.addClassCleanup(cls.browser.close)

    def open_report(self, report, theme='light', width=1400, height=860, touch=False):
        context = self.browser.new_context(viewport=dict(width=width, height=height),
                                           is_mobile=touch, has_touch=touch)
        self.addCleanup(context.close)
        context.add_init_script('localStorage.clear()')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, report))
        page.evaluate('''async theme => {
            document.documentElement.dataset.theme = theme;
            if (typeof fgSwitchView === 'function') await fgSwitchView('text');
        }''', theme)
        if not page.locator('#filterBox').is_visible():
            page.locator('#textFilterToggle').press('Enter')
        return page

    def apply(self, page, expression):
        page.locator('#filterInput').fill(expression)
        page.locator('#filterInput').press('Enter')
        self.settle(page, expression)

    def settle(self, page, expression=None):
        page.wait_for_function('''text => !fgFilterProgress && fgFilterState.text ===
            (text === null ? document.getElementById('filterInput').value.trim() : text)''', arg=expression)

    def assert_focused(self, locator):
        self.assertTrue(locator.evaluate('element => element === document.activeElement'))

    def assert_matches(self, page, masks):
        names = page.locator('#content .line-container:visible[data-func]').evaluate_all(
            'rows => rows.map(row => row.dataset.func).sort()')
        self.assertEqual(names, ['probe_' + str(mask) for mask in masks])

    def test_tab_space_enter_remove_conditions_and_retain_sort(self):
        for report in REPORTS:
            for theme in ('light', 'dark'):
                for touch in (False, True):
                    with self.subTest(report=report, theme=theme, touch=touch):
                        page = self.open_report(report, theme, width=390 if touch else 1400, touch=touch)
                        self.apply(page, 'cpu:="1" AND pid:="7" AND comm:="worker" sort:desc')
                        self.assert_matches(page, [7])
                        controls = page.locator('#filterTags button')
                        self.assertEqual(controls.count(), 5)
                        self.assertTrue(controls.evaluate_all('''items => items.every(item =>
                            item.type === "button" && item.getAttribute("aria-label"))'''))
                        page.locator('#filterInput').press('Shift+Tab')
                        self.assert_focused(page.locator('.tag-remove').last)
                        page.keyboard.press('Space')
                        self.settle(page)
                        self.assert_matches(page, [3, 7])
                        self.assert_focused(page.locator('.tag-remove').last)
                        page.keyboard.press('Enter')
                        self.settle(page)
                        self.assert_matches(page, [1, 3, 5, 7])
                        self.assert_focused(page.locator('.tag-remove'))
                        page.keyboard.press('Space')
                        self.settle(page)
                        self.assertEqual(page.locator('#filterInput').input_value(), 'sort:desc')
                        self.assert_focused(page.locator('#filterInput'))
                        self.assertEqual(page.locator('#filterTags button').count(), 0)
                        self.assert_matches(page, list(range(8)))
                        page.context.close()

    def test_logic_toggle_preserves_groups_results_focus_and_data(self):
        for report in REPORTS:
            for theme in ('light', 'dark'):
                with self.subTest(report=report, theme=theme):
                    page = self.open_report(report, theme)
                    original = page.evaluate('async () => JSON.stringify(await fgData.load("tlData"))') if report != 'plain.html' else None
                    self.apply(page, '(cpu:="1" OR pid:="7") AND NOT comm:="other" AND func:probe sort:asc')
                    self.assert_matches(page, [5, 6, 7])
                    labels = page.locator('.filter-tag-label').all_text_contents()
                    logic = page.locator('.filter-tag-logic').first
                    logic.focus()
                    logic.press('Space')
                    self.settle(page)
                    self.assert_matches(page, list(range(8)))
                    self.assertEqual(page.locator('.filter-tag-logic').all_text_contents(), ['OR', 'OR'])
                    self.assertEqual(page.locator('.filter-tag-label').all_text_contents(), labels)
                    self.assert_focused(page.locator('.filter-tag-logic').first)
                    self.assertIn('top-level conditions to AND', page.locator('.filter-tag-logic').first.get_attribute('aria-label'))
                    page.keyboard.press('Enter')
                    self.settle(page)
                    self.assert_matches(page, [5, 6, 7])
                    self.assertEqual(page.locator('.filter-tag-logic').all_text_contents(), ['AND', 'AND'])
                    self.assert_focused(page.locator('.filter-tag-logic').first)
                    self.assertTrue(page.locator('#filterInput').input_value().endswith('sort:asc'))
                    if report != 'plain.html':
                        page.evaluate('async () => { await fgSwitchView("timeline"); }')
                        names = page.evaluate('''() => {
                            const data = fgData.read('tlData');
                            return data.cols.line.flatMap((number, index) => fgFilterState.lines.has(number)
                                ? [data.strings[data.cols.name[index]]] : []).sort();
                        }''')
                        self.assertEqual(names, ['probe_5', 'probe_6', 'probe_7'])
                        if original is not None:
                            self.assertEqual(page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)
                    page.context.close()

    def test_explicit_same_logic_group_stays_one_condition(self):
        for report in REPORTS:
            with self.subTest(report=report):
                page = self.open_report(report)
                self.apply(page, 'cpu:="1" AND (pid:="7" AND comm:="worker") sort:desc')
                self.assertEqual(page.locator('.tag-remove').count(), 2)
                self.assert_matches(page, [7])
                group = page.locator('.filter-tag-label').last.text_content()
                self.assertTrue(group.startswith('(') and group.endswith(')'))
                page.locator('.filter-tag-logic').press('Enter')
                self.settle(page)
                self.assertEqual(page.locator('.tag-remove').count(), 2)
                self.assertEqual(page.locator('.filter-tag-label').last.text_content(), group)
                self.assert_matches(page, [1, 3, 5, 6, 7])
                page.keyboard.press('Space')
                self.settle(page)
                self.assert_matches(page, [7])
                page.locator('.tag-remove').first.press('Enter')
                self.settle(page)
                self.assertEqual(page.locator('.filter-tag-label').text_content(), group)
                self.assertEqual(page.locator('.filter-tag-logic').count(), 0)
                self.assert_matches(page, [6, 7])
                self.assert_focused(page.locator('.tag-remove'))
                page.keyboard.press('Space')
                self.settle(page)
                self.assertEqual(page.locator('#filterInput').input_value(), 'sort:desc')
                self.assert_focused(page.locator('#filterInput'))
                page.context.close()

    def test_precedence_group_stays_one_condition_across_logic_toggles(self):
        for report in REPORTS:
            with self.subTest(report=report):
                page = self.open_report(report)
                self.apply(page, 'cpu:="1" OR pid:="7" AND comm:="worker" sort:asc')
                self.assertEqual(page.locator('.tag-remove').count(), 2)
                self.assert_matches(page, [1, 3, 5, 6, 7])
                labels = page.locator('.filter-tag-label').all_text_contents()
                self.assertTrue(labels[-1].startswith('(') and labels[-1].endswith(')'))
                page.locator('.filter-tag-logic').press('Enter')
                self.settle(page)
                self.assertEqual(page.locator('.filter-tag-label').all_text_contents(), labels)
                self.assert_matches(page, [7])
                self.assert_focused(page.locator('.filter-tag-logic'))
                page.keyboard.press('Space')
                self.settle(page)
                self.assertEqual(page.locator('.filter-tag-label').all_text_contents(), labels)
                self.assert_matches(page, [1, 3, 5, 6, 7])
                page.context.close()

    def test_quoted_literals_are_complete_text_not_markup(self):
        value = 'worker_"\\ <img src=x onerror="window.tagInjected=true"> & λ'
        for report in REPORTS:
            with self.subTest(report=report):
                page = self.open_report(report)
                self.apply(page, 'comm:=' + json.dumps(value) + ' AND func:probe sort:desc')
                expected = page.locator('.filter-tag-label').first.text_content()
                self.assertIn('<img', expected)
                self.assertEqual(page.locator('#filterTags img').count(), 0)
                self.assertFalse(page.evaluate('Boolean(window.tagInjected)'))
                page.locator('.tag-remove').last.focus()
                page.keyboard.press('Enter')
                self.settle(page)
                self.assertEqual(page.locator('.filter-tag-label').text_content(), expected)
                self.assertEqual(page.evaluate('parseFilterExpr(document.getElementById("filterInput").value).ast.value'), value)
                self.assert_matches(page, [])
                page.context.close()

    def test_long_labels_fit_and_scroll_without_truncation(self):
        value = 'worker_' + '0123456789abcdef' * 200
        for report in REPORTS:
            for theme in ('light', 'dark'):
                for width, height, touch in ((1400, 860, False), (800, 600, False),
                                              (390, 844, True), (844, 390, True)):
                    with self.subTest(report=report, theme=theme, width=width):
                        page = self.open_report(report, theme, width, height, touch)
                        self.apply(page, 'comm:=' + json.dumps(value) + ' AND func:probe')
                        label = page.locator('.filter-tag-label').first
                        self.assertEqual(page.evaluate('parseFilterExpr(document.getElementById("filterInput").value).ast.left.value'), value)
                        self.assertIn(value, label.text_content())
                        layout = page.evaluate('''() => {
                            const tags = document.getElementById('filterTags');
                            const panel = tags.closest('.text-compact') ? document.getElementById('filterBox') : tags;
                            panel.scrollTop = 0;
                            return {width: document.documentElement.scrollWidth, viewport: innerWidth,
                                tagWidth: tags.clientWidth, contentWidth: tags.scrollWidth,
                                height: panel.clientHeight, contentHeight: panel.scrollHeight,
                                controls: document.querySelector('.controls').getBoundingClientRect().height};
                        }''')
                        self.assertLessEqual(layout['width'], layout['viewport'])
                        self.assertLessEqual(layout['contentWidth'], layout['tagWidth'] + 1)
                        self.assertGreater(layout['contentHeight'], layout['height'])
                        self.assertLess(layout['controls'], height * 0.8)
                        panel = page.locator('#filterBox' if touch else '#filterTags')
                        bounds = panel.bounding_box()
                        page.mouse.move(bounds['x'] + bounds['width'] / 2, bounds['y'] + min(20, bounds['height'] / 2))
                        page.mouse.wheel(0, 10000)
                        page.wait_for_function('selector => document.querySelector(selector).scrollTop > 0',
                                               arg='#filterBox' if touch else '#filterTags')
                        remove = page.locator('.tag-remove').last
                        remove.focus()
                        bounds = remove.bounding_box()
                        self.assertGreaterEqual(bounds['y'], 0)
                        self.assertLessEqual(bounds['y'] + bounds['height'], height)
                        if touch:
                            self.assertGreaterEqual(bounds['width'], 44)
                            self.assertGreaterEqual(bounds['height'], 44)
                        remove.press('Enter')
                        self.settle(page)
                        self.assertIn(value, page.locator('.filter-tag-label').text_content())
                        self.assert_focused(page.locator('.tag-remove'))
                        page.context.close()

    def test_many_conditions_keep_last_removal_visible_and_focused(self):
        for report in REPORTS:
            for touch in (False, True):
                with self.subTest(report=report, touch=touch):
                    page = self.open_report(report, width=390 if touch else 800, height=600, touch=touch)
                    self.apply(page, ' AND '.join(['func:probe'] * 60))
                    self.assertEqual(page.locator('.tag-remove').count(), 60)
                    self.assert_matches(page, list(range(8)))
                    page.locator('.tag-remove').last.focus()
                    page.keyboard.press('Enter')
                    self.settle(page)
                    self.assertEqual(page.locator('.tag-remove').count(), 59)
                    self.assert_focused(page.locator('.tag-remove').last)
                    bounds = page.locator('.tag-remove').last.bounding_box()
                    self.assertGreaterEqual(bounds['y'], 0)
                    self.assertLessEqual(bounds['y'] + bounds['height'], 600)
                    self.assert_matches(page, list(range(8)))
                    page.context.close()

    def test_first_removal_focuses_next_and_pending_filter_does_not_steal_focus(self):
        for report in REPORTS:
            with self.subTest(report=report):
                page = self.open_report(report)
                self.apply(page, 'cpu:="1" AND pid:="7" AND func:probe')
                page.locator('.tag-remove').first.focus()
                page.keyboard.press('Enter')
                self.settle(page)
                self.assert_focused(page.locator('.tag-remove').first)
                self.assertIn('pid:', page.locator('.tag-remove').first.get_attribute('aria-label'))
                self.assert_matches(page, [2, 3, 6, 7])
                page.locator('.filter-tag-logic').press('Space')
                page.locator('#filterInput').focus()
                self.settle(page)
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                self.assert_focused(page.locator('#filterInput'))
                self.assert_matches(page, list(range(8)))
                self.apply(page, 'cpu:="1" AND pid:="7" AND func:probe sort:desc')
                page.locator('.tag-remove').nth(1).focus()
                page.keyboard.press('Enter')
                self.settle(page)
                self.assert_matches(page, [1, 3, 5, 7])
                self.assert_focused(page.locator('.tag-remove').last)
                self.assertIn('func:', page.locator('.tag-remove').last.get_attribute('aria-label'))
                self.assertTrue(page.locator('#filterInput').input_value().endswith('sort:desc'))
                page.context.close()


if __name__ == '__main__':
    unittest.main()
