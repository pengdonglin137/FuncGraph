"""Duration colors, truthful scale semantics and compact Timeline controls."""

from itertools import product
import json
from pathlib import Path
import shutil
import subprocess
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import lifecycle_event, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ROOT = Path(__file__).resolve().parents[1]
TRACE = """\
1000.000100 |   0)   demo-7     |  ...1. |               |  outer() {
1000.000101 |   0)   demo-7     |  ...1. |   9.000 us    |    child(); /* <-outer+0x4/0x40 */
1000.000110 |   0)   demo-7     |  ...1. |  10.000 us    |  } /* outer */
1000.000111 |   0)   demo-7     |  ...1. |   0.100 us    |  tiny();
1000.000112 |   0)   demo-7     |  ...1. |   0.000 us    |  zero();
1000.000113 |   0)   demo-7     |  ...1. |   1.000 us    |  __x64_sys_openat(); /* ret=-2 */
"""


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class DurationColorModelTest(unittest.TestCase):
    def run_colors(self, durations, parents, assertions, adjusted=None):
        model = (ROOT / 'assets/timeline/model.js').read_text()
        times = model[model.index('const sDurRaw ='):model.index('const SECTIONS =')]
        correction = model[model.index('function setAdjusted('):model.index("yield 'Building inline frames")]
        render = (ROOT / 'assets/timeline/render.js').read_text()
        colors = render[render.index('const colorCache ='):render.index('function sliceStyle(')]
        setup = '''
            const assert = require('node:assert/strict');
            const N = input.durations.length;
            const C = {dur:input.durations, adj:input.adjusted};
            let parentReads = 0;
            const sParent = new Proxy(input.parents, {get(target, key, receiver) {
                if (typeof key === 'string' && /^\\d+$/.test(key)) parentReads++;
                return Reflect.get(target, key, receiver);
            }});
            const sFlags = Array(N).fill(0), sName = Array(N).fill(0);
            const FL = {CTX:3, USER:1 << 25};
            const P = {text:'#16202e'};
            let colorMode = 'total', dark = false;
            function hashOf() { throw new Error('Duration colors must not hash function names'); }
            function baseColor(index) { return baseSliceStyle(index)[0]; }
        '''
        source = 'const input = ' + json.dumps({'durations': durations, 'parents': parents,
                                              'adjusted': adjusted}) + ';\n'
        result = subprocess.run(['node', '-e', source + setup + times + correction + colors + assertions],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_total_and_self_differ_for_parent_with_expensive_child(self):
        self.run_colors([10000, 9000, 100, 0], [-1, 0, -1, -1], '''
            assert.deepEqual(Array.from(sSelf), [1000, 9000, 100, 0]);
            assert.equal(maxDuration, 10000);
            assert.equal(maxSelf, 9000);
            const total = Array.from({length:N}, (_, index) => baseColor(index));
            assert.equal(total[0], heatColor(19));
            assert.equal(total[3], heatColor(0));
            colorMode = 'heat';
            assert.notEqual(baseColor(0), total[0]);
            assert.equal(baseColor(1), heatColor(19));
            assert.equal(baseColor(3), heatColor(0));
        ''')

    def test_recorded_style_tuples_are_shared_per_bin_and_recreated_on_invalidation(self):
        self.run_colors([1000, 1000, 0], [-1, -1, -1], '''
            const style = baseSliceStyle(0);
            assert.equal(baseSliceStyle(1), style);
            assert.notEqual(baseSliceStyle(2), style);
            colorCache.clear();
            assert.notEqual(baseSliceStyle(0), style);
            assert.deepEqual(baseSliceStyle(0), [heatColor(19), P.text, null]);
        ''')

    def test_legacy_self_palette_and_bounded_cache_are_preserved(self):
        durations = [0, 1, 10, 100, 1000, 1000000, 1000000000] * 100
        self.run_colors(durations, [-1] * len(durations), '''
            for (const mode of ['total', 'heat']) {
                colorMode = mode;
                const bins = input.durations.map(value => Math.min(19,
                    Math.floor(19.99 * Math.log1p(value) / Math.log1p(1000000000))));
                assert.deepEqual(bins, [...bins].map((bin, index) => heatBin(index)));
                bins.forEach((bin, index) => assert.equal(baseColor(index), heatColor(bin)));
            }
            assert.ok(colorCache.size <= 40);
        ''')

    def test_all_zero_empty_and_single_nanosecond_scales_remain_finite(self):
        for durations in [[], [0], [0, 0], [1], [0, 1], [100, 100]]:
            with self.subTest(durations=durations):
                self.run_colors(durations, [-1] * len(durations), '''
                    for (const mode of ['total', 'heat']) {
                        colorMode = mode;
                        for (let index = 0; index < N; index++) {
                            assert.ok(Number.isFinite(heatBin(index)));
                            assert.equal(heatBin(index), input.durations[index] ? 19 : 0);
                            assert.equal(baseColor(index), heatColor(input.durations[index] ? 19 : 0));
                        }
                    }
                ''')

    def test_overhead_recomputes_both_scales_and_clamps_negative_self(self):
        self.run_colors([10000, 9000, 100], [-1, 0, -1], '''
            setAdjusted(true);
            assert.equal(maxDuration, 1000);
            assert.equal(maxSelf, 1000);
            assert.deepEqual(Array.from(sSelf), [0, 1000, 0]);
            colorMode = 'total';
            assert.equal(heatBin(0), 19);
            colorMode = 'heat';
            assert.equal(heatBin(0), 0);
            setAdjusted(false);
            assert.equal(maxDuration, 10000);
            assert.equal(maxSelf, 9000);
            assert.deepEqual(Array.from(sSelf), [1000, 9000, 100]);
        ''', adjusted=[800, 1000, 0])

    def test_duration_bases_reuse_self_columns_and_skip_parent_rescans(self):
        self.run_colors([9000, 4000], [-1, 0], '''
            const measured = sSelf, original = JSON.stringify(input);
            assert.equal(measured, sSelfRaw);
            assert.equal(parentReads, N);
            for (const value of [false, null, 0]) setAdjusted(value);
            assert.equal(parentReads, N);
            assert.equal(sSelf, measured);
            setAdjusted(true);
            const adjusted = sSelf;
            assert.notEqual(adjusted, measured);
            assert.deepEqual(Array.from(adjusted), [4000, 2000]);
            assert.equal(parentReads, 2 * N);
            for (let round = 0; round < 8; round++) {
                setAdjusted(true);
                assert.equal(sSelf, adjusted);
                assert.equal(sDur, sDurAdj);
                setAdjusted(false);
                assert.equal(sSelf, measured);
                assert.equal(sDur, sDurRaw);
                assert.deepEqual(Array.from(sSelfRaw), [5000, 4000]);
            }
            assert.equal(parentReads, 2 * N);
            assert.equal(JSON.stringify(input), original);
        ''', adjusted=[6000, 2000])

    def test_missing_estimate_never_recomputes_measured_self(self):
        self.run_colors([5, 7, 0], [-1, 0, -1], '''
            const measured = sSelf, reads = parentReads;
            for (const on of [true, false, true, 1, null]) {
                setAdjusted(on);
                assert.equal(adjOn, false);
                assert.equal(sDur, sDurRaw);
                assert.equal(sSelf, measured);
                assert.equal(parentReads, reads);
                assert.deepEqual(Array.from(sSelf), [0, 7, 0]);
            }
        ''')

    def test_cached_metrics_match_ordered_reference_for_every_basis_and_shape(self):
        cases = [([], [], []), ([0], [-1], [0]), ([1], [-1], [0]),
                 ([0, 5, 4], [-1, 0, 0], [0, 2, 0]),
                 ([10, 9, 5, 2, 1], [-1, 0, 1, 1, 0], [8, 7, 4, 0, 0]),
                 ([0.3, 0.1, 0.2, 9007199254740000], [-1, 0, 0, -1], [0.2, 0.09, 0.1, 8]),
                 ([3, 10, 1, 0], [1, -1, 1, -1], [1, 5, 0, 0])]
        for durations, parents, adjusted in cases:
            with self.subTest(durations=durations, parents=parents):
                self.run_colors(durations, parents, '''
                    function reference(durations) {
                        const values = Float64Array.from(durations);
                        let maximum = 0, maximumSelf = 0;
                        for (let index = 0; index < N; index++) maximum = Math.max(maximum, durations[index]);
                        for (let index = 0; index < N; index++)
                            if (input.parents[index] >= 0) values[input.parents[index]] -= durations[index];
                        for (let index = 0; index < N; index++) {
                            if (values[index] < 0) values[index] = 0;
                            maximumSelf = Math.max(maximumSelf, values[index]);
                        }
                        return {values, maximum, maximumSelf};
                    }
                    Object.freeze(input.durations); Object.freeze(input.parents); Object.freeze(input.adjusted);
                    const original = JSON.stringify(input), raw = reference(input.durations);
                    for (const on of [false, true, false, true, true, false]) {
                        setAdjusted(on);
                        const expected = on ? reference(input.adjusted) : raw;
                        assert.deepEqual(sSelf, expected.values);
                        assert.equal(maxSelf, expected.maximumSelf);
                        assert.equal(maxDuration, expected.maximum);
                        assert.equal(selfColorLog, Math.log1p(expected.maximumSelf));
                        assert.equal(durationColorLog, Math.log1p(expected.maximum));
                        assert.deepEqual(sSelfRaw, raw.values);
                        for (const mode of ['total', 'heat']) {
                            colorMode = mode;
                            for (let index = 0; index < N; index++) assert.ok(Number.isFinite(heatBin(index)));
                        }
                    }
                    assert.equal(JSON.stringify(input), original);
                ''', adjusted=adjusted)

    def test_light_and_dark_endpoints_have_consistent_hues(self):
        self.run_colors([0, 1000], [-1, -1], '''
            assert.equal(baseColor(0), 'hsl(220,30%,82%)');
            assert.equal(baseColor(1), 'hsl(0,80%,60%)');
            dark = true;
            colorCache.clear();
            assert.equal(baseColor(0), 'hsl(220,30%,30%)');
            assert.equal(baseColor(1), 'hsl(0,80%,48%)');
        ''')


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TimelineColorsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        trace = Path(cls.kernel.dir) / 'colors.txt'
        trace.write_text(TRACE)
        for report_mode in ['single', 'sidecar']:
            cls.kernel.run(str(trace), '--no-config', '--timeline', '--enable-fold', '--filter',
                           '--func-links', '--overhead', '1000,100', '--report-mode', report_mode,
                           out='colors.html' if report_mode == 'single' else 'colors-sidecar')
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, path=None):
        if path is None:
            path = Path(self.kernel.dir) / 'colors.html'
        self.page.goto(Path(path).as_uri())
        self.boot()

    def boot(self):
        self.page.evaluate('''async () => {
            await fgData.load('tlData');
            window.originalColorsInput = JSON.stringify(fgData.read('tlData'));
            await fgSwitchView('timeline');
        }''')
        self.page.locator('.tl-canvas').wait_for()

    def choose(self, mode):
        helpers.open_timeline_tools(self.page)
        self.page.get_by_label('Color', exact=True).select_option(mode)
        self.assertEqual(self.page.get_by_label('Color', exact=True).input_value(), mode)

    def colors(self):
        return self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            return Array.from({length:model.N}, (_, index) => model.styleOf(index)[0]);
        }''')

    def test_default_is_context_and_duration_legend_only_appears_in_tools(self):
        self.open_report()
        helpers.open_timeline_tools(self.page)
        self.assertEqual(self.page.get_by_label('Color', exact=True).input_value(), 'ctx')
        self.assertFalse(self.page.locator('.tl-heat-scale').is_visible())
        self.assertIsNone(self.page.get_by_label('Color', exact=True).get_attribute('aria-describedby'))
        self.choose('total')
        self.assertTrue(self.page.locator('.tl-heat-scale').is_visible())
        self.assertEqual(self.page.get_by_label('Color', exact=True).get_attribute('aria-describedby'), 'tlHeatScale')
        self.assertIn('0–10.00 us', self.page.locator('.tl-heat-caption').inner_text())
        self.assertIn('all calls', self.page.locator('.tl-heat-caption').inner_text())
        self.assertIn('children and waits', self.page.locator('.tl-heat-note').inner_text())
        self.assertIn('not CPU time', self.page.locator('.tl-heat-note').inner_text())
        helpers.click_timeline_tool(self.page, '[data-act=options]')
        self.assertFalse(self.page.locator('.tl-heat-scale').is_visible())
        helpers.open_timeline_tools(self.page)
        self.choose('name')
        self.assertFalse(self.page.locator('.tl-heat-scale').is_visible())
        self.assertIsNone(self.page.get_by_label('Color', exact=True).get_attribute('aria-describedby'))

    def test_switching_modes_preserves_model_selection_zoom_and_raw_data(self):
        self.open_report()
        self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            model.select(0);
            model.zoomTo(model.sStart[0], model.sEnd[0], 0);
            window.originalColorView = JSON.stringify([model.view(), model.sel(), [...model.sStart], [...model.sEnd]]);
        }''')
        self.choose('total')
        total = self.colors()
        self.choose('heat')
        self.assertNotEqual(self.colors()[0], total[0])
        self.assertIn('0–9.000 us', self.page.locator('.tl-heat-caption').inner_text())
        self.assertIn('Excludes children', self.page.locator('.tl-heat-note').inner_text())
        for mode in ['name', 'cpu', 'total', 'ctx']:
            self.choose(mode)
        self.assertTrue(self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            return JSON.stringify([model.view(), model.sel(), [...model.sStart], [...model.sEnd]]) === originalColorView &&
                JSON.stringify(fgData.read('tlData')) === originalColorsInput;
        }'''))

    def test_filters_and_zoom_do_not_rescale_duration_colors(self):
        self.open_report()
        for mode in ['total', 'heat']:
            with self.subTest(mode=mode):
                self.choose(mode)
                original = self.colors()
                scale = self.page.locator('.tl-heat-caption').inner_text()
                self.page.locator('.tl-ftext').fill('func:tiny')
                self.page.locator('.tl-ftext').press('Enter')
                self.page.wait_for_function('() => !!window.fgFilterState?.lines && !window.fgFilterProgress')
                self.page.evaluate('''() => { const model = fgView('timeline').probe(); model.zoomTo(11000, 12000, 0); }''')
                self.assertEqual(self.colors(), original)
                self.assertEqual(self.page.locator('.tl-heat-caption').inner_text(), scale)
                self.page.locator('.tl-ftext').fill('')
                self.page.locator('.tl-ftext').press('Enter')
                self.page.wait_for_function('() => !window.fgFilterProgress && !window.fgFilterState?.lines')

    def test_overhead_updates_colors_legend_and_zero_scale_without_changing_geometry(self):
        self.open_report()
        self.choose('total')
        original = self.colors()
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.assertIn('0–8.900 us', self.page.locator('.tl-heat-caption').inner_text())
        self.assertIn('Overhead estimate', self.page.locator('.tl-heat-note').inner_text())
        self.assertNotEqual(original[2], self.colors()[2])
        self.choose('heat')
        self.assertIn('0–8.900 us', self.page.locator('.tl-heat-caption').inner_text())
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.choose('total')
        self.assertEqual(self.colors(), original)
        self.assertIn('Measured', self.page.locator('.tl-heat-note').inner_text())
        self.assertTrue(self.page.evaluate("JSON.stringify(fgData.read('tlData')) === originalColorsInput"))

    def test_repeated_overhead_switches_keep_detail_breakdown_geometry_and_heat_colors(self):
        self.open_report()
        self.choose('heat')
        self.page.evaluate("() => fgView('timeline').probe().select(0)")
        original = self.colors()
        breakdown = self.page.locator('.an-bd-legend').inner_html()
        geometry = self.page.evaluate('''() => { const model = fgView('timeline').probe();
            return JSON.stringify([model.sStart, model.sEnd, model.sParent, model.view(), model.sel()]); }''')
        self_field = self.page.locator('.tl-call-facts > .k').filter(has_text='self').locator('xpath=following-sibling::span[1]')
        measured_self = self_field.inner_text()
        adjusted_colors = None
        for round_number in range(3):
            with self.subTest(round=round_number):
                helpers.click_timeline_tool(self.page, '[data-act=adj]')
                self.assertNotEqual(self_field.inner_text(), measured_self)
                self.assertEqual(self_field.inner_text(), '0 ns (0.0%)')
                colors = self.colors()
                if adjusted_colors is not None:
                    self.assertEqual(colors, adjusted_colors)
                adjusted_colors = colors
                self.assertEqual(self.page.locator('.an-bd-legend').inner_html(), breakdown)
                helpers.click_timeline_tool(self.page, '[data-act=adj]')
                self.assertEqual(self_field.inner_text(), measured_self)
                self.assertEqual(self.colors(), original)
                self.assertEqual(self.page.locator('.an-bd-legend').inner_html(), breakdown)
                self.assertEqual(self.page.evaluate('''() => { const model = fgView('timeline').probe();
                    return JSON.stringify([model.sStart, model.sEnd, model.sParent, model.view(), model.sel()]); }'''), geometry)
                self.assertTrue(self.page.evaluate("JSON.stringify(fgData.read('tlData')) === originalColorsInput"))

    def test_theme_change_updates_both_ramp_and_canvas_colors(self):
        self.open_report()
        for mode in ['total', 'heat']:
            self.choose(mode)
            for theme in ['light', 'dark', 'light']:
                self.page.evaluate('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                expected = 'hsl(0,80%,48%)' if theme == 'dark' else 'hsl(0,80%,60%)'
                target = 0 if mode == 'total' else 1
                self.page.wait_for_function('''({target, expected}) => fgView('timeline').probe().styleOf(target)[0] === expected''',
                                            arg={'target': target, 'expected': expected})
                endpoint = self.page.evaluate('''color => {
                    const element = document.createElement('span');
                    element.style.color = color;
                    return element.style.color;
                }''', expected)
                self.assertIn(endpoint, self.page.locator('.tl-heat-ramp').get_attribute('style'))

    def test_errors_override_heat_and_synthetic_frames_stay_neutral(self):
        self.open_report()
        self.choose('total')
        error_color = self.colors()[-1]
        self.assertEqual(error_color, '#d32f2f')
        helpers.click_timeline_tool(self.page, '[data-ov=err]')
        self.assertNotEqual(self.colors()[-1], error_color)
        self.assertTrue(self.colors()[-1].startswith('hsl('))
        self.assertTrue(self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            return model.NA > model.N && Array.from({length:model.NA - model.N},
                (_, index) => model.styleOf(model.N + index)[0]).every(color => color.startsWith('rgba('));
        }'''))

    def test_console_style_sample_cannot_change_a_cached_paint_style(self):
        self.open_report()
        self.assertTrue(self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            const style = model.styleOf(0), original = style[0];
            style[0] = 'magenta';
            return model.styleOf(0)[0] === original;
        }'''))

    def test_packed_reports_keep_duration_colors_and_count_reports_hide_them(self):
        for name, source, has_duration in [('packed', ' 0)   0.100 us    |  leaf();\n', True),
                                           ('count', 'leaf();\n', False),
                                           ('events', stream([lifecycle_event('exec', 1000),
                                                              lifecycle_event('exit', 2000)]), False)]:
            with self.subTest(name=name):
                trace = Path(self.kernel.dir) / (name + '.txt')
                trace.write_text(source)
                self.kernel.run(str(trace), '--no-config', '--timeline', out=name + '.html')
                self.open_report(Path(self.kernel.dir) / (name + '.html'))
                helpers.open_timeline_tools(self.page)
                for mode in ['total', 'heat']:
                    option = self.page.locator('[data-role=color] option[value=' + mode + ']')
                    self.assertEqual(option.is_disabled(), not has_duration)
                    self.assertEqual(option.evaluate('option => option.hidden'), not has_duration)
                    if has_duration:
                        self.choose(mode)
                        self.assertEqual(self.colors(), ['hsl(0,80%,60%)'])
                    else:
                        self.page.locator('[data-role=color]').evaluate('''(picker, value) => {
                            picker.value = value;
                            picker.dispatchEvent(new Event('change', {bubbles:true}));
                        }''', mode)
                        self.assertEqual(self.page.get_by_label('Color', exact=True).input_value(), 'ctx')
                self.assertEqual(self.page.locator('.tl-heat-scale').is_visible(), has_duration)

    def test_sidecar_report_uses_identical_color_and_overhead_semantics(self):
        self.open_report()
        self.choose('total')
        expected = self.colors()
        server = create_server(Path(self.kernel.dir) / 'colors-sidecar')
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        thread.start()
        try:
            self.page.goto(server.url)
            self.boot()
            requests = []
            self.page.on('request', lambda request: requests.append(request.url))
            self.choose('total')
            self.assertEqual(self.colors(), expected)
            helpers.click_timeline_tool(self.page, '[data-act=adj]')
            self.assertIn('8.900 us', self.page.locator('.tl-heat-caption').inner_text())
            adjusted = self.colors()
            for round_number in range(3):
                with self.subTest(round=round_number):
                    helpers.click_timeline_tool(self.page, '[data-act=adj]')
                    self.assertEqual(self.colors(), expected)
                    helpers.click_timeline_tool(self.page, '[data-act=adj]')
                    self.assertEqual(self.colors(), adjusted)
                    self.assertTrue(self.page.evaluate("JSON.stringify(fgData.read('tlData')) === originalColorsInput"))
            self.assertEqual(requests, [])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_compact_fullscreen_and_keyboard_controls_fit_both_themes(self):
        for width, theme in product([360, 1440], ['light', 'dark']):
            with self.subTest(width=width, theme=theme):
                self.page.set_viewport_size({'width': width, 'height': 860})
                self.open_report()
                self.page.evaluate('''theme => {
                    document.documentElement.setAttribute('data-theme', theme);
                    Element.prototype.requestFullscreen = () => Promise.reject(new Error('Use viewport'));
                }''', theme)
                self.page.locator('[data-act=max]').click()
                self.page.wait_for_function('document.getElementById("timelineView").classList.contains("tl-fullscreen")')
                self.choose('total')
                self.assertTrue(self.page.locator('.tl-heat-scale').is_visible())
                for selector in ['.tl-options', '.tl-heat-scale', '[data-role=color]']:
                    box = self.page.locator(selector).bounding_box()
                    self.assertGreaterEqual(box['x'], 0)
                    self.assertLessEqual(box['x'] + box['width'], width)
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                self.page.get_by_label('Color', exact=True).focus()
                self.page.keyboard.press('ArrowDown')
                self.assertEqual(self.page.get_by_label('Color', exact=True).input_value(), 'heat')
                self.page.keyboard.press('Escape')
                self.assertFalse(self.page.locator('.tl-options').is_visible())
                self.assertTrue(self.page.locator('#timelineView').evaluate('root => root.classList.contains("tl-fullscreen")'))

    def test_mixed_space_swatch_only_describes_context_colors(self):
        self.kernel.run(str(ROOT / 'samples/sample_mixed.jsonl'), '--no-config', '--timeline', out='mixed-colors.html')
        self.open_report(Path(self.kernel.dir) / 'mixed-colors.html')
        self.assertTrue(self.page.locator('.tl-space-key').is_visible())
        self.choose('total')
        self.assertFalse(self.page.locator('.tl-space-key').is_visible())
        self.choose('heat')
        self.assertFalse(self.page.locator('.tl-space-key').is_visible())
        self.choose('ctx')
        self.assertTrue(self.page.locator('.tl-space-key').is_visible())
        self.assertTrue(self.page.evaluate("JSON.stringify(fgData.read('tlData')) === originalColorsInput"))

    def test_zero_durations_have_zero_legend_in_measured_and_adjusted_modes(self):
        trace = Path(self.kernel.dir) / 'zero-times.txt'
        trace.write_text('1000.000100 |   0)   demo-7     |   0.000 us    |  leaf();\n')
        self.kernel.run(str(trace), '--no-config', '--timeline', '--overhead', '100,100', out='zero-times.html')
        self.open_report(Path(self.kernel.dir) / 'zero-times.html')
        for adjusted, mode in product([False, True], ['total', 'heat']):
            with self.subTest(adjusted=adjusted, mode=mode):
                self.choose(mode)
                button = self.page.locator('[data-act=adj]')
                if ('subtracted' in button.inner_text()) != adjusted:
                    button.click()
                self.assertEqual(self.colors(), ['hsl(220,30%,82%)'])
                self.assertIn('0–0 ns', self.page.locator('.tl-heat-caption').inner_text())

    def test_unavailable_and_unknown_color_options_do_not_change_active_mode(self):
        self.open_report()
        helpers.open_timeline_tools(self.page)
        option = self.page.locator('[data-role=color] option[value=module]')
        self.assertTrue(option.is_disabled())
        self.assertTrue(option.evaluate('option => option.hidden'))
        self.choose('total')
        original = self.colors()
        for value in ['module', 'unknown']:
            self.page.get_by_label('Color', exact=True).evaluate('''(picker, value) => {
                picker.value = value;
                picker.dispatchEvent(new Event('change', {bubbles:true}));
            }''', value)
            self.assertEqual(self.page.get_by_label('Color', exact=True).input_value(), 'total')
            self.assertEqual(self.colors(), original)

    def test_touch_picker_has_readable_text_and_target_in_portrait_and_landscape(self):
        for width, height in [(390, 844), (844, 390)]:
            context = self.browser.new_context(viewport={'width': width, 'height': height}, is_mobile=True, has_touch=True)
            try:
                self.page = context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.open_report()
                self.choose('total')
                picker = self.page.get_by_label('Color', exact=True)
                self.assertGreaterEqual(picker.bounding_box()['height'], 44)
                self.assertGreaterEqual(float(picker.evaluate('element => getComputedStyle(element).fontSize').removesuffix('px')), 16)
                self.assertTrue(self.page.locator('.tl-heat-scale').is_visible())
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            finally:
                context.close()
