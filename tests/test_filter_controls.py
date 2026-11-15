"""Field filters are native, non-modal controls independent of trace navigation."""
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


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class FilterControlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        options = ['--no-config', '--enable-fold', '--filter', '--func-links']
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, '--timeline',
                       '--compare', helpers.fixture('pi4_default'), out='index.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, out='plain.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, '--timeline',
                       '--report-mode', 'sidecar', out='sidecar')
        cls.server = create_server(Path(cls.kernel.dir))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def open_report(self, report='index.html', width=1500, height=900, touch=False, theme='light'):
        context = self.browser.new_context(viewport=dict(width=width, height=height),
                                           is_mobile=touch, has_touch=touch)
        self.addCleanup(context.close)
        context.add_init_script('localStorage.clear()')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, report), wait_until='load')
        page.evaluate('''async theme => {
            document.documentElement.dataset.theme = theme;
            if (typeof fgSwitchView === 'function') await fgSwitchView('text');
        }''', theme)
        if not page.locator('#filterBox').is_visible():
            page.locator('#textFilterToggle').press('Enter')
        return page

    def open_field(self, page, field='cpu', activation='Enter'):
        owner = page.locator('.field-btn[data-field=' + field + ']')
        owner.press(activation)
        popup = page.locator('.field-dropdown')
        popup.wait_for(state='visible')
        return owner, popup

    def assert_owner_focused(self, owner):
        self.assertTrue(owner.evaluate('owner => owner === document.activeElement'))
        self.assertEqual(owner.get_attribute('aria-expanded'), 'false')

    def trace_state(self, page):
        return page.evaluate('''() => JSON.stringify({selected:keyboardSelectedLine?.id,
            folds:foldedBlocks, expanded:expandedLines, events:fgTextEventsVisible,
            rows:document.querySelectorAll('#content .line-container').length,
            timeline:fgData.ready('tlData') ? fgData.read('tlData') : null})''')

    def test_enum_keyboard_entry_focuses_native_checkbox_and_exposes_state(self):
        page = self.open_report()
        before = self.trace_state(page)
        for activation in ('Enter', 'Space'):
            with self.subTest(activation=activation):
                owner, popup = self.open_field(page, activation=activation)
                checkbox = popup.locator('input[type=checkbox]').first
                self.assertTrue(checkbox.evaluate('checkbox => checkbox === document.activeElement'))
                self.assertEqual(popup.get_attribute('role'), 'dialog')
                self.assertEqual(popup.get_attribute('aria-label'), 'Filter by cpu')
                self.assertEqual(owner.get_attribute('aria-haspopup'), 'dialog')
                self.assertEqual(owner.get_attribute('aria-controls'), popup.get_attribute('id'))
                self.assertEqual(owner.get_attribute('aria-expanded'), 'true')
                checkbox.press('Space')
                self.assertTrue(checkbox.is_checked())
                self.assertEqual(self.trace_state(page), before)
                page.keyboard.press('Escape')
                self.assertFalse(popup.is_visible())
                self.assert_owner_focused(owner)

    def test_tab_stays_native_and_escape_does_not_steal_outside_focus(self):
        page = self.open_report()
        owner, popup = self.open_field(page)
        popup.get_by_role('button', name='Cancel', exact=True).focus()
        page.keyboard.press('Tab')
        self.assertTrue(popup.get_by_role('button', name='OK', exact=True).evaluate(
            'button => button === document.activeElement'))
        page.keyboard.press('Tab')
        self.assertFalse(popup.evaluate('popup => popup.contains(document.activeElement)'))
        page.locator('#filterInput').focus()
        page.keyboard.press('Escape')
        self.assertFalse(popup.is_visible())
        self.assertTrue(page.locator('#filterInput').evaluate('input => input === document.activeElement'))
        self.assertEqual(owner.get_attribute('aria-expanded'), 'false')

    def test_freeform_cancel_and_enter_restore_owner_and_preserve_data(self):
        page = self.open_report()
        before = self.trace_state(page)
        owner, popup = self.open_field(page, 'func')
        query = popup.get_by_role('textbox', name='Filter by func', exact=True)
        self.assertTrue(query.evaluate('input => input === document.activeElement'))
        query.fill('do_sys_openat2')
        popup.get_by_role('button', name='Cancel', exact=True).press('Enter')
        self.assert_owner_focused(owner)
        self.assertEqual(page.locator('#filterInput').input_value(), '')
        self.assertEqual(self.trace_state(page), before)
        owner, popup = self.open_field(page, 'func')
        popup.get_by_role('textbox', name='Filter by func', exact=True).fill('do_sys_openat2')
        page.keyboard.press('Enter')
        self.assertFalse(popup.is_visible())
        self.assert_owner_focused(owner)
        self.assertEqual(page.locator('#filterInput').input_value(), 'func:do_sys_openat2')
        self.assertEqual(json.loads(self.trace_state(page))['rows'], json.loads(before)['rows'])
        self.assertEqual(json.loads(self.trace_state(page))['timeline'], json.loads(before)['timeline'])

    def test_enum_confirm_keeps_existing_expression_and_returns_to_owner(self):
        page = self.open_report()
        page.locator('#filterInput').fill('duration:>0')
        owner, popup = self.open_field(page)
        checkboxes = popup.locator('input[type=checkbox]')
        chosen = [checkboxes.nth(index).get_attribute('value') for index in range(min(2, checkboxes.count()))]
        for index in range(len(chosen)):
            checkboxes.nth(index).check()
        popup.get_by_role('button', name='OK', exact=True).press('Enter')
        expected = 'cpu:' + chosen[0] if len(chosen) == 1 else '(' + ' OR '.join('cpu:' + value for value in chosen) + ')'
        self.assertEqual(page.locator('#filterInput').input_value(), 'duration:>0 AND ' + expected)
        self.assert_owner_focused(owner)

    def test_repeated_entry_toggles_and_other_field_replaces_popup(self):
        page = self.open_report()
        owner, popup = self.open_field(page)
        owner.click()
        self.assertFalse(popup.is_visible())
        self.assert_owner_focused(owner)
        self.open_field(page)
        other, popup = self.open_field(page, 'func')
        self.assertEqual(page.locator('.field-dropdown').count(), 1)
        self.assertEqual(owner.get_attribute('aria-expanded'), 'false')
        self.assertEqual(other.get_attribute('aria-expanded'), 'true')
        popup.get_by_role('button', name='Cancel', exact=True).click()
        self.assert_owner_focused(other)

    def test_outside_click_keeps_new_focus_and_discards_only_unapplied_draft(self):
        page = self.open_report()
        before = self.trace_state(page)
        owner, popup = self.open_field(page, 'func')
        popup.locator('input').fill('unapplied')
        target = page.get_by_role('button', name='Theme', exact=True)
        target.click()
        self.assertFalse(popup.is_visible())
        self.assertTrue(target.evaluate('button => button === document.activeElement'))
        self.assertEqual(owner.get_attribute('aria-expanded'), 'false')
        self.assertEqual(page.locator('#filterInput').input_value(), '')
        self.assertEqual(self.trace_state(page), before)

    def test_view_changes_close_body_popup_without_preparing_full_text_model(self):
        for view in ('timeline', 'compare'):
            with self.subTest(view=view):
                page = self.open_report()
                self.assertFalse(page.evaluate('fgDomTextModels.has(document.getElementById("content"))'))
                owner, popup = self.open_field(page, 'func')
                page.evaluate('async view => { await fgSwitchView(view); }', view)
                self.assertFalse(popup.is_visible())
                self.assertEqual(owner.get_attribute('aria-expanded'), 'false')
                self.assertFalse(page.evaluate('fgDomTextModels.has(document.getElementById("content"))'))
                page.evaluate("async () => { await fgSwitchView('text'); }")
                self.assertFalse(popup.is_visible())

    def test_popup_cleanup_does_not_search_the_complete_trace_dom(self):
        page = self.open_report()
        page.evaluate('''() => {
            window.fieldDocumentQueries = [];
            for (const name of ['querySelector', 'querySelectorAll']) {
                const original = document[name].bind(document);
                document[name] = selector => { fieldDocumentQueries.push(selector); return original(selector); };
            }
        }''')
        for field in ('cpu', 'func'):
            self.open_field(page, field)
            page.keyboard.press('Escape')
        page.locator('#filterInput').click()
        selectors = page.evaluate('fieldDocumentQueries')
        self.assertFalse(any('.field-dropdown' in selector or '.field-btn.active' in selector or
                             '.line-container' in selector for selector in selectors), selectors)

    def test_compact_bounds_and_two_step_escape_preserve_filter_and_trace(self):
        for width, height, theme in ((320, 700, 'dark'), (390, 844, 'light'), (844, 390, 'dark')):
            with self.subTest(width=width, height=height):
                page = self.open_report(width=width, height=height, touch=True, theme=theme)
                before = self.trace_state(page)
                for field in ('cpu', 'func'):
                    owner, popup = self.open_field(page, field)
                    bounds = popup.bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertGreaterEqual(bounds['y'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.assertLessEqual(bounds['y'] + bounds['height'], height)
                    for target in popup.locator('label, button').all():
                        self.assertGreaterEqual(target.bounding_box()['height'], 44)
                    cancel = popup.get_by_role('button', name='Cancel', exact=True)
                    self.assertTrue(cancel.evaluate('button => getComputedStyle(button).color === getComputedStyle(document.body).color'))
                    if field == 'func':
                        self.assertEqual(popup.locator('input').evaluate('input => getComputedStyle(input).fontSize'), '16px')
                        self.assertGreaterEqual(popup.locator('input').bounding_box()['height'], 44)
                    page.keyboard.press('Escape')
                    self.assertFalse(popup.is_visible())
                    self.assertTrue(page.locator('#filterBox').is_visible())
                    self.assert_owner_focused(owner)
                page.keyboard.press('Escape')
                self.assertFalse(page.locator('#filterBox').is_visible())
                self.assertTrue(page.locator('#textFilterToggle').evaluate('button => button === document.activeElement'))
                self.assertEqual(self.trace_state(page), before)

    def test_resize_and_switching_compact_panels_close_child_popup(self):
        page = self.open_report(width=390, height=844, touch=True)
        owner, popup = self.open_field(page)
        page.locator('#textMoreToggle').click()
        self.assertFalse(popup.is_visible())
        self.assertEqual(owner.get_attribute('aria-expanded'), 'false')
        page.locator('#textFilterToggle').press('Enter')
        owner, popup = self.open_field(page, 'func')
        page.set_viewport_size(dict(width=1100, height=800))
        page.locator('#textMoreToggle').wait_for(state='hidden')
        self.assertFalse(popup.is_visible())
        self.assertEqual(owner.get_attribute('aria-expanded'), 'false')

    def test_composition_modifiers_and_consumed_keys_do_not_dismiss_or_apply(self):
        page = self.open_report()
        self.open_field(page, 'func')
        facts = page.evaluate('''() => {
            const input = document.querySelector('.field-dropdown input'), results = [];
            input.value = 'pending';
            for (const key of ['Escape', 'Enter']) for (const options of [
                {isComposing:true}, {keyCode:229}, {ctrlKey:true}, {metaKey:true}, {altKey:true}, {consumed:true}
            ]) {
                const event = new KeyboardEvent('keydown', {key, bubbles:true, cancelable:true, ...options});
                if (options.consumed) event.preventDefault();
                input.dispatchEvent(event);
                results.push(input.isConnected && document.getElementById('filterInput').value === '');
            }
            return results;
        }''')
        self.assertEqual(facts, [True] * 12)
        page.keyboard.press('Escape')
        self.assertEqual(page.locator('.field-dropdown').count(), 0)

    def test_plain_sidecar_and_windowed_reports_share_keyboard_lifecycle(self):
        for report in ('plain.html', 'sidecar/index.html', 'sidecar/index.html?text=windowed'):
            with self.subTest(report=report):
                page = self.open_report(report)
                before = self.trace_state(page)
                for field in ('cpu', 'func'):
                    owner, popup = self.open_field(page, field)
                    self.assertTrue(popup.evaluate('popup => popup.contains(document.activeElement)'))
                    page.keyboard.press('Escape')
                    self.assertFalse(popup.is_visible())
                    self.assert_owner_focused(owner)
                self.assertEqual(self.trace_state(page), before)

    def test_event_visibility_open_source_and_folding_survive_popup_cancel(self):
        page = self.open_report()
        page.locator('.expand-btn').first.click()
        page.locator('.expanded-content:visible').wait_for()
        page.locator('#textEventsBtn').click()
        before = self.trace_state(page)
        page.evaluate('window.filterSourceNode = document.querySelector(".expanded-content[style*=block]")')
        owner, popup = self.open_field(page)
        popup.get_by_role('button', name='Cancel', exact=True).click()
        self.assert_owner_focused(owner)
        self.assertEqual(self.trace_state(page), before)
        self.assertTrue(page.evaluate('filterSourceNode.isConnected && filterSourceNode.style.display === "block"'))
        self.assertEqual(page.locator('[data-trace-event="1"]:visible').count(), 0)
