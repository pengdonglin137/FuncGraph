"""Lazy, bounded function-search preparation and complete indexed navigation."""

from pathlib import Path
import shutil
import subprocess
import threading
import unittest

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class SearchIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(helpers.ROOT, 'assets/timeline/search.js').read_text()

    def check(self, script):
        prefix = self.source + '''
            const assert = require('node:assert/strict');
            function drain(steps) {
                let result;
                do { result = steps.next(); } while (!result.done);
                return result.value;
            }
        '''
        result = subprocess.run(['node', '-e', prefix + script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_index_preserves_zero_name_id_and_ignores_synthetic_frames(self):
        self.check('''
            const names = Int32Array.from([3, 0, 3, 7, 0, 999]);
            const index = drain(buildSearchIndex(names, 5));
            assert.deepEqual([...index.names], [3, 0, 7]);
            assert.deepEqual([...index.offsets], [0, 2, 4, 5]);
            assert.deepEqual([...index.calls], [0, 2, 1, 4, 3]);
            assert.deepEqual([...names], [3, 0, 3, 7, 0, 999]);
            const empty = drain(buildSearchIndex(names, 0));
            assert.equal(empty.calls.length, 0);
            assert.deepEqual([...empty.offsets], [0]);
        ''')

    def test_regex_only_visits_recorded_names_once(self):
        self.check('''
            const names = Int32Array.from([0, 2, 0, 4, 2]);
            const strings = ['Root<int>', 'argument text', 'child+symbol', 'unused', 'CHILD<int>'];
            const index = drain(buildSearchIndex(names, names.length));
            const tested = [];
            const hits = drain(matchSearchNames(index, strings, {test(value) {
                tested.push(value); return /child/i.test(value);
            }}));
            assert.deepEqual(tested, ['Root<int>', 'child+symbol', 'CHILD<int>']);
            assert.deepEqual([...hits], [0, 0, 1, 0, 1]);
            const matches = drain(collectSearchCalls(index, names, names.length, hits));
            assert.deepEqual([...matches], [1, 3, 4]);
        ''')

    def test_sparse_queries_merge_postings_without_reading_every_call(self):
        self.check('''
            const names = new Int32Array(50000).fill(4);
            for (const position of [0, 27, 299, 19001]) names[position] = 0;
            for (const position of [7, 30, 322, 17000, 49000]) names[position] = 2;
            const index = drain(buildSearchIndex(names, names.length));
            const forbidden = new Proxy(names, {get() {throw new Error('Full call-name scan');}});
            const merged = drain(collectSearchCalls(index, forbidden, names.length, Uint8Array.from([1, 0, 1, 0, 0])));
            assert.deepEqual([...merged], [0, 7, 27, 30, 299, 322, 17000, 19001, 49000]);
            const single = drain(collectSearchCalls(index, forbidden, names.length, Uint8Array.from([1, 0, 0, 0, 0])));
            assert.deepEqual([...single], [0, 27, 299, 19001]);
            assert.equal(single.buffer, index.calls.buffer);
            const absent = drain(collectSearchCalls(index, forbidden, names.length, new Uint8Array(5)));
            assert.equal(absent.length, 0);
        ''')

    def test_dense_queries_and_filters_are_exact_and_do_not_reorder_calls(self):
        self.check('''
            const names = Int32Array.from({length:20000}, (unused, position) => position % 17);
            const index = drain(buildSearchIndex(names, names.length));
            const hits = Uint8Array.from({length:17}, (unused, position) => position % 2);
            const matches = drain(collectSearchCalls(index, names, names.length, hits));
            const expected = [...names].flatMap((name, position) => hits[name] ? [position] : []);
            assert.deepEqual([...matches], expected);
            const accepted = Uint8Array.from({length:names.length}, (unused, position) => position % 7 === 0);
            assert.deepEqual([...drain(filterSearchCalls(matches, accepted))], expected.filter(position => accepted[position]));
            assert.equal(drain(filterSearchCalls(matches, null)), matches);
            assert.equal(drain(filterSearchCalls(matches, new Uint8Array(names.length).fill(1))), matches);
            assert.equal(drain(filterSearchCalls(matches, new Uint8Array(names.length))).length, 0);
            assert.deepEqual([...matches], expected);
        ''')

    def test_bounded_steps_yield_before_publishing_large_results(self):
        self.check('''
            const names = Int32Array.from({length:90000}, (unused, position) => position % 97);
            let reads = 0;
            const observed = new Proxy(names, {get(target, key) {
                if (Number.isInteger(Number(key))) reads++;
                return Reflect.get(target, key, target);
            }});
            const steps = buildSearchIndex(observed, names.length);
            let result, yields = 0;
            do {
                reads = 0;
                result = steps.next();
                assert.ok(reads <= SEARCH_BATCH * 2);
                if (!result.done) { assert.equal(typeof result.value, 'string'); yields++; }
            } while (!result.done);
            assert.ok(yields >= 40);
            const dense = collectSearchCalls(result.value, names, names.length, new Uint8Array(97).fill(1));
            assert.equal(dense.next().done, false);
            const matches = drain(dense);
            assert.equal(matches.length, names.length);
            assert.equal(matches[0], 0);
            assert.equal(matches.at(-1), names.length - 1);
            const filtered = filterSearchCalls(matches, new Uint8Array(names.length).fill(1));
            assert.equal(filtered.next().done, false);
            assert.equal(drain(filtered), matches);
        ''')

    def test_randomized_sparse_dense_and_filtered_queries_match_full_scan(self):
        self.check('''
            let seed = 314159;
            function random(maximum) {
                seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
                return seed % maximum;
            }
            for (const count of [0, 1, 8, 31, 4097, 20003]) {
                const names = Int32Array.from({length:count}, () => random(137));
                const index = drain(buildSearchIndex(names, count));
                for (let trial = 0; trial < 20; trial++) {
                    const hits = Uint8Array.from({length:137}, () => random(20) < trial);
                    const accepted = Uint8Array.from({length:count}, () => random(4) === 0);
                    const matches = drain(collectSearchCalls(index, names, count, hits));
                    const expected = [...names].flatMap((name, position) => hits[name] ? [position] : []);
                    assert.deepEqual([...matches], expected);
                    assert.deepEqual([...drain(filterSearchCalls(matches, accepted))], expected.filter(position => accepted[position]));
                }
            }
        ''')


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class SearchPreparationBrowserTest(unittest.TestCase):
    count = 20000
    function_count = 80

    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        trace = Path(cls.kernel.dir, 'search.txt')
        trace.write_text('# tracer: function_graph\n' + ''.join(
            f'1.{position:06d} |   0)   task-42   |  ...1. |   0.100 us   |  function_{position % cls.function_count}();\n'
            for position in range(cls.count)))
        cls.kernel.run(str(trace), '--timeline', '--filter', out='single.html')
        cls.kernel.run(str(trace), '--timeline', '--filter', '--report-mode', 'sidecar', out='sidecar')
        cls.server = create_server(Path(cls.kernel.dir, 'sidecar'))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
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

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 900})
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.errors, self.requests = [], []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda request: self.requests.append(request.url))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open_report(self, mode='single'):
        url = Path(self.kernel.dir, 'single.html').as_uri() if mode == 'single' else self.server.url
        self.page.goto(url)
        self.page.evaluate('async () => { await fgSwitchView("timeline"); }')
        self.page.locator('.tl-canvas').wait_for()
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().N'), self.count)
        self.page.evaluate('''() => {
            const now = performance.now.bind(performance);
            let tick = 0;
            performance.now = () => now() + (tick += 10);
        }''')

    def start(self, query, previous=False):
        return self.page.evaluate('''({query, previous}) => {
            const input = document.querySelector('.tl-search');
            input.value = query;
            input.dispatchEvent(new Event('input', {bubbles:true}));
            input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', shiftKey:previous, bubbles:true}));
            return {busy:document.querySelector('.tl-count').getAttribute('aria-busy'),
                disabled:document.querySelector('[data-act=searchnext]').disabled};
        }''', dict(query=query, previous=previous))

    def ready(self):
        self.page.wait_for_function('() => document.querySelector(".tl-count").getAttribute("aria-busy") === "false"')

    def selected(self):
        return self.page.evaluate('fgView("timeline").probe().sel()')

    def test_large_search_yields_and_queues_navigation_in_both_delivery_modes(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open_report(mode)
                self.assertEqual(self.start('^function_1$'), dict(busy='true', disabled=True))
                heartbeat = self.page.evaluate('''async () => {
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return document.querySelector('.tl-count').getAttribute('aria-busy');
                }''')
                self.assertEqual(heartbeat, 'true')
                self.ready()
                self.assertEqual(self.selected(), 1)
                self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 250 calls')
                self.page.locator('.tl-search').press('Shift+Enter')
                self.assertEqual(self.selected(), self.count - self.function_count + 1)
                self.assertEqual(self.page.locator('.tl-count').inner_text(), '250 / 250 calls')
                if mode == 'sidecar':
                    self.assertFalse(any(url.endswith(('text.html', 'text-index.json')) for url in self.requests))
                    self.assertEqual(self.page.locator('#content .line-container').count(), 0)

    def test_replacing_and_restarting_an_unfinished_query_never_reuses_empty_cache(self):
        self.open_report()
        self.start('^function_1$')
        self.assertEqual(self.start('^function_2$')['busy'], 'true')
        self.start('^function_2$')
        self.ready()
        self.assertEqual(self.selected(), 2)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 250 calls')

    def test_escape_cancels_preparation_without_late_selection_or_status(self):
        self.open_report()
        self.start('.*')
        self.page.locator('.tl-search').press('Escape')
        self.page.wait_for_timeout(180)
        self.assertEqual(self.selected(), -1)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '')
        self.assertEqual(self.page.locator('.tl-count').get_attribute('aria-busy'), 'false')
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())
        self.start('^function_3$')
        self.ready()
        self.assertEqual(self.selected(), 3)

    def test_filter_change_during_preparation_uses_only_the_latest_scope(self):
        self.open_report()
        self.start('.*')
        self.page.evaluate('''() => {
            const lines = fgData.read('tlData').cols.line;
            fgNotifyFilter('last only', new Set([lines.at(-1)]));
        }''')
        self.ready()
        self.assertEqual(self.selected(), self.count - 1)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 1 calls')
        self.page.evaluate('fgNotifyFilter("", null)')
        self.ready()
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{self.count} / {self.count} calls')

    def test_pending_typing_uses_new_filter_without_automatic_navigation(self):
        self.open_report()
        self.page.locator('.tl-search').fill('^function_79$')
        self.page.evaluate('''() => {
            const lines = fgData.read('tlData').cols.line;
            fgNotifyFilter('last only', new Set([lines.at(-1)]));
        }''')
        self.ready()
        self.assertEqual(self.selected(), -1)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 calls')

    def test_unavailable_preparation_is_not_an_invalid_regex_and_can_retry(self):
        self.open_report()
        self.page.evaluate('''() => {
            window.failSearch = true;
            const test = RegExp.prototype.test;
            RegExp.prototype.test = function(value) {
                if (window.failSearch && this.source === '^function_1$') throw new Error('injected search failure');
                return test.call(this, value);
            };
        }''')
        self.start('^function_1$')
        self.ready()
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Search unavailable')
        self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'false')
        self.assertIn('Press Enter to retry', self.page.locator('.tl-search').get_attribute('title'))
        self.page.evaluate('window.failSearch = false')
        self.page.locator('.tl-search').press('Enter')
        self.ready()
        self.assertEqual(self.selected(), 1)

    def test_view_change_cancels_queued_jump_and_resume_only_refreshes_matches(self):
        self.open_report('sidecar')
        self.start('.*')
        self.page.route('**/text.html', lambda route: route.abort())
        self.page.evaluate('() => { fgSwitchView("text"); }')
        self.assertEqual(self.page.locator('.tl-count').text_content(), 'Search paused')
        self.page.evaluate('async () => { await fgSwitchView("timeline"); }')
        self.ready()
        self.assertEqual(self.selected(), -1)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{self.count} calls')

    def test_zero_results_explain_whether_the_shared_filter_excludes_matches(self):
        self.open_report()
        self.start('^function_1$')
        self.ready()
        self.page.evaluate('fgNotifyFilter("nothing", new Set())')
        self.ready()
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '0 calls')
        self.assertIn('shared filter excludes', self.page.locator('.tl-count').get_attribute('title'))
        self.start('^missing_recorded_function$')
        self.ready()
        self.assertIn('No recorded function names match', self.page.locator('.tl-count').get_attribute('title'))

    def test_busy_and_ready_search_status_fit_touch_viewports_and_themes(self):
        screenshots = Path('/tmp/fg-search-review')
        screenshots.mkdir(exist_ok=True)
        self.context.close()
        for theme in ('light', 'dark'):
            for width, height in ((390, 844), (844, 390)):
                with self.subTest(theme=theme, viewport=(width, height)):
                    self.context = self.browser.new_context(viewport={'width': width, 'height': height}, has_touch=True)
                    self.page = self.context.new_page()
                    self.page.set_default_timeout(15000)
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.open_report('sidecar')
                    self.page.evaluate('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                    self.start('.*')
                    for stage in ('busy', 'ready'):
                        if stage == 'ready':
                            self.ready()
                        facts = self.page.evaluate('''() => {
                            const toolbar = document.querySelector('.tl-toolbar');
                            const input = document.querySelector('.tl-search').getBoundingClientRect();
                            return {overflow:toolbar.scrollWidth - toolbar.clientWidth,
                                inputWidth:input.width, inputHeight:input.height};
                        }''')
                        self.assertLessEqual(facts['overflow'], 1)
                        self.assertGreater(facts['inputWidth'], 100)
                        self.assertGreaterEqual(facts['inputHeight'], 44)
                    self.page.wait_for_timeout(400)
                    self.page.screenshot(path=str(screenshots / f'search-{theme}-{width}x{height}.png'))
                    self.context.close()

    def test_full_search_position_stays_together_without_growing_phone_toolbar(self):
        self.context.close()
        for mode in ('single', 'sidecar'):
            for theme in ('light', 'dark'):
                with self.subTest(mode=mode, theme=theme):
                    self.context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True)
                    self.page = self.context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.open_report(mode)
                    self.page.evaluate('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                    self.start('.*', previous=True)
                    self.ready()
                    self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{self.count} / {self.count} calls')
                    facts = self.page.evaluate('''() => {
                        const value = document.querySelector('.tl-count-value');
                        const status = value.parentElement;
                        const range = document.createRange();
                        range.selectNodeContents(value);
                        return {lines:range.getClientRects().length,
                            numberStyle:getComputedStyle(value).fontVariantNumeric,
                            statusHeight:status.getBoundingClientRect().height,
                            lineHeight:parseFloat(getComputedStyle(status).lineHeight),
                            inputWidth:document.querySelector('.tl-search').getBoundingClientRect().width,
                            toolbarHeight:document.querySelector('.tl-primary').getBoundingClientRect().height};
                    }''')
                    self.assertEqual(facts['lines'], 1)
                    self.assertEqual(facts['numberStyle'], 'tabular-nums')
                    self.assertLessEqual(facts['statusHeight'], 2 * facts['lineHeight'] + 1)
                    self.assertGreaterEqual(facts['inputWidth'], 100)
                    self.assertLessEqual(facts['toolbarHeight'], 92)
                    self.assertEqual(self.selected(), self.count - 1)
                    self.page.locator('.tl-search').press('Escape')
                    self.assertEqual(self.page.locator('.tl-count-value').count(), 0)
                    self.context.close()

    def test_long_counter_layout_keeps_exact_digits_and_touch_targets_without_overflow(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report()
        self.start('.*')
        self.ready()
        for width, height in ((320, 844), (360, 844), (390, 844), (844, 390), (1440, 900)):
            self.page.set_viewport_size({'width': width, 'height': height})
            self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            for text in ('1 / 20000', '20000 / 20000', '1000000 / 1000000', '4294967295 / 4294967295'):
                with self.subTest(viewport=(width, height), text=text):
                    self.page.locator('.tl-count-value').evaluate('(element, text) => element.textContent = text', text)
                    facts = self.page.evaluate('''() => {
                        const toolbar = document.querySelector('.tl-toolbar');
                        const input = document.querySelector('.tl-search').getBoundingClientRect();
                        const controls = [...toolbar.querySelectorAll('.tl-search-nav button, .tl-fullscreen-button')];
                        return {overflow:toolbar.scrollWidth - toolbar.clientWidth,
                            inputWidth:input.width,
                            compact:document.querySelector('#timelineView').classList.contains('tl-compact'),
                            controls:controls.map(element => {
                                const box = element.getBoundingClientRect();
                                return {left:box.left, right:box.right, width:box.width, height:box.height};
                            })};
                    }''')
                    self.assertEqual(self.page.locator('.tl-count').text_content(), text + ' calls')
                    self.assertLessEqual(facts['overflow'], 1)
                    self.assertGreaterEqual(facts['inputWidth'], 100)
                    for box in facts['controls']:
                        self.assertGreaterEqual(box['left'], 0)
                        self.assertLessEqual(box['right'], width)
                        if facts['compact']:
                            self.assertGreaterEqual(box['width'], 44)
                            self.assertGreaterEqual(box['height'], 44)
        self.assertEqual(self.selected(), 0)
