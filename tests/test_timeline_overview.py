"""Overview density depends on recorded roots and CSS width, not panel or palette."""

from itertools import product
from pathlib import Path
import threading
import unittest

import helpers
from benchmark_overview import instrument
from fglib.viewer import create_server
from test_kvm import capture as kvm_capture
from test_uftrace_import import capture, lifecycle_event, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TimelineOverviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)
        traces = {'kernel': helpers.fixture('pi4_sched')}
        cpu = Path(cls.kernel.dir, 'cpu.txt')
        cpu.write_text('1000.000100 |   0)   1.000 us    |  root();\n'
                       '1000.000102 |   1)   1.000 us    |  worker();\n')
        traces['cpu'] = cpu
        for kind, records in [('mixed', capture()), ('kvm', kvm_capture()),
                              ('mixed-kvm', kvm_capture() + capture()),
                              ('events', [lifecycle_event('exec', 0), lifecycle_event('exit', 1000)])]:
            trace = Path(cls.kernel.dir, kind + '.jsonl')
            trace.write_text(stream(records))
            traces[kind] = trace
        cls.urls = {}
        for kind, mode in product(traces, ('single', 'sidecar')):
            destination = kind + '-' + mode + ('.html' if mode == 'single' else '')
            cls.kernel.run(str(traces[kind]), '--no-config', '--timeline', '--enable-fold', '--filter',
                           '--func-links', '--report-mode', mode, out=destination)
            report = Path(cls.kernel.dir, destination)
            if mode == 'sidecar':
                report = report / 'index.html'
            report.write_text(instrument(report.read_text()))
            if mode == 'sidecar':
                server = create_server(report.parent)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                cls.addClassCleanup(thread.join, 5)
                cls.addClassCleanup(server.server_close)
                cls.addClassCleanup(server.shutdown)
                cls.urls[kind, mode] = server.url
            else:
                cls.urls[kind, mode] = report.as_uri()

    def setUp(self):
        self.context = None
        self.errors = []

    def tearDown(self):
        if self.context:
            self.context.close()
        self.assertEqual(self.errors, [])

    def open(self, kind='kernel', mode='single', ratio=1):
        if self.context:
            self.context.close()
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900}, device_scale_factor=ratio)
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.urls[kind, mode])
        self.page.evaluate("async () => await fgSwitchView('timeline')")
        self.page.evaluate('''async () => {
            await document.fonts.ready;
            window.overviewFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            await overviewFrame();
            window.overviewOriginal = fgView('timeline').probe().overview().density;
            window.overviewSnapshot = () => {
                const model = fgView('timeline').probe();
                return JSON.stringify({data:fgData.read('tlData'),N:model.N,NA:model.NA,S:model.S,SYN:model.SYN,
                    columns:[model.sStart,model.sEnd,model.sDepth,model.sTask,model.sName,model.sParent,
                        model.sFlags,model.sLine,model.sLdAll,model.sDur,model.sSelf,model.sSelfRaw].map(column => Array.from(column)),
                    rows:model.TRACKS.map(track => [track.rowsT.map(row => Array.from(row)),track.rowsI.map(row => Array.from(row))])});
            };
            window.overviewReference = () => {
                const model=fgView('timeline').probe(), value=model.overview(), spans=[];
                const hasTasks=value.tracks.some(track => track.kind === 0);
                for (const track of value.tracks) {
                    if (hasTasks && track.kind !== 0) continue;
                    for (const index of track.kvm) spans.push(model.kvm.intervals[index].slice(1,3));
                    for (const index of track.rowsT[0] || []) spans.push([model.sStart[index],model.sEnd[index]]);
                }
                const scale=value.width/Math.max(1,value.end-value.start), expected=new Float32Array(Math.max(1,Math.ceil(value.width)));
                for (let pixel=0;pixel<expected.length;pixel++) for (const [start,end] of spans) {
                    const left=Math.max(0,(start-value.start)*scale),right=(end-value.start)*scale;
                    if (pixel >= Math.floor(left) && pixel <= Math.floor(right))
                        expected[pixel] += Math.min(right,pixel+1)-Math.max(left,pixel);
                }
                return {actual:Array.from(value.density),expected:Array.from(expected),maximum:value.maximum,
                    expectedMaximum:Math.max(...expected) || 1};
            };
            overviewBuilds.length=0;
        }''')

    def snapshot(self):
        return self.page.evaluate('overviewSnapshot()')

    def settle(self):
        self.page.evaluate('overviewFrame()')

    def assert_density(self):
        result = self.page.evaluate('overviewReference()')
        self.assertEqual(result['actual'], result['expected'])
        self.assertEqual(result['maximum'], result['expectedMaximum'])

    def test_same_size_resizes_and_theme_changes_reuse_the_recorded_density(self):
        for kind, mode, ratio in product(('kernel', 'cpu', 'mixed', 'kvm', 'mixed-kvm', 'events'), ('single', 'sidecar'), (1, 2)):
            with self.subTest(kind=kind, mode=mode, ratio=ratio):
                self.open(kind=kind, mode=mode, ratio=ratio)
                before = self.snapshot()
                pixels = self.page.locator('.tl-head-canvas').evaluate('canvas => canvas.toDataURL()')
                self.page.evaluate('''async () => {
                    for (let index=0;index<3;index++) { fgView('timeline').resize(); await overviewFrame(); }
                    for (const theme of ['dark','light']) { document.documentElement.dataset.theme=theme; await overviewFrame(); }
                }''')
                self.assertEqual(self.page.evaluate('overviewBuilds'), [])
                self.assertTrue(self.page.evaluate("fgView('timeline').probe().overview().density === overviewOriginal"))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.page.locator('.tl-head-canvas').evaluate('canvas => canvas.toDataURL()'), pixels)
                self.assert_density()

    def test_actual_width_changes_rebuild_exact_values_without_changing_the_model(self):
        for kind, mode in product(('kernel', 'cpu', 'mixed', 'kvm', 'mixed-kvm', 'events'), ('single', 'sidecar')):
            with self.subTest(kind=kind, mode=mode):
                self.open(kind=kind, mode=mode)
                before = self.snapshot()
                values = self.page.evaluate("Array.from(fgView('timeline').probe().overview().density)")
                for width in (1240, 790, 1500):
                    self.page.evaluate('overviewBuilds.length=0')
                    self.page.set_viewport_size({'width': width, 'height': 900})
                    self.settle()
                    builds = self.page.evaluate('overviewBuilds')
                    self.assertGreater(len(builds), 0)
                    for previous, current in zip(builds, builds[1:]):
                        self.assertNotEqual(previous['width'], current['width'])
                    self.assert_density()
                    self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.page.evaluate("Array.from(fgView('timeline').probe().overview().density)"), values)

    def test_detail_resize_and_view_roundtrip_do_not_rebuild_at_the_same_width(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(kind='mixed', mode=mode)
                self.page.evaluate("fgView('timeline').probe().select(0)")
                self.settle()
                before = self.snapshot()
                self.page.evaluate('overviewBuilds.length=0')
                separator = self.page.get_by_role('separator', name='Resize details')
                for key in ('ArrowUp', 'ArrowDown'):
                    separator.press(key)
                    self.settle()
                bounds = separator.bounding_box()
                center = bounds['x'] + bounds['width'] / 2
                self.page.mouse.move(center, bounds['y'] + 4)
                self.page.mouse.down()
                self.page.mouse.move(center, bounds['y'] - 28, steps=3)
                self.page.mouse.up()
                self.settle()
                self.page.evaluate("async () => { await fgSwitchView('text'); await fgSwitchView('timeline'); }")
                self.settle()
                self.assertEqual(self.page.evaluate('overviewBuilds'), [])
                self.assert_density()
                self.assertEqual(self.snapshot(), before)

    def test_inline_search_filters_and_track_collapse_preserve_the_full_overview(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode)
                before = self.snapshot()
                self.assertGreater(self.page.evaluate("fgView('timeline').probe().NA - fgView('timeline').probe().N"), 0)
                self.page.evaluate('''async () => {
                    const model=fgView('timeline').probe();
                    model.inline(true); await overviewFrame(); model.inline(false); await overviewFrame();
                    for (const track of model.TRACKS) track.collapsed=true;
                    fgView('timeline').resize(); await overviewFrame();
                    for (const track of model.TRACKS) track.collapsed=false;
                    fgView('timeline').resize(); await overviewFrame();
                }''')
                self.page.locator('.tl-search').fill('schedule')
                self.page.locator('.tl-search').press('Enter')
                helpers.open_timeline_tools(self.page)
                self.page.locator('.tl-ftext').fill('func:schedule')
                self.page.locator('[data-act=fapply]').click()
                self.page.wait_for_function("() => fgFilterState.text === 'func:schedule'")
                self.settle()
                self.assert_density()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.page.evaluate('overviewBuilds'), [])

    def test_pixel_ratio_only_changes_keep_css_pixel_density_and_repaint_the_ruler(self):
        self.open()
        before = self.snapshot()
        density = self.page.evaluate("Array.from(fgView('timeline').probe().overview().density)")
        session = self.context.new_cdp_session(self.page)
        for ratio in (1.25, 2, 1):
            session.send('Emulation.setDeviceMetricsOverride', dict(width=1500, height=900, deviceScaleFactor=ratio, mobile=False))
            self.page.evaluate("fgView('timeline').resize()")
            self.settle()
            self.assertEqual(self.page.evaluate('overviewBuilds'), [])
            self.assert_density()
            self.assertEqual(self.page.evaluate("Array.from(fgView('timeline').probe().overview().density)"), density)
            canvas = self.page.locator('.tl-head-canvas')
            self.assertAlmostEqual(canvas.evaluate('element => element.width'), canvas.bounding_box()['width'] * ratio, delta=1)
            self.assertEqual(self.snapshot(), before)
        session.detach()


if __name__ == '__main__':
    unittest.main()
