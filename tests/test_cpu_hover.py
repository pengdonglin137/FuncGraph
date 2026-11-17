"""CPU hover summaries retain clipped IRQ totals without rescanning each move."""

from itertools import product
from pathlib import Path
import threading
import unittest
from urllib.parse import urljoin

import helpers
from benchmark_cpu_hover import fixture, instrument
from fglib.viewer import create_server
from test_uftrace_import import capture, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


INTERRUPTS = '''\
1000.000000 |   0) alpha-42 |  ...1. |               |  root() {
1000.000001 |   0) alpha-42 |  ..s1. |               |    soft() {
1000.000002 |   0) alpha-42 |  ..h1. |   0.200 us    |      hard();
1000.000003 |   0) alpha-42 |  ..s1. |   2.000 us    |    } /* soft */
1000.000004 |   0) alpha-42 |  ..z1. |   0.100 us    |    nmi();
1000.000010 |   0) alpha-42 |  ...1. |  10.000 us    |  } /* root */
1000.000010 |   0) beta-43 |  ...1. |  10.000 us    |  next();
1000.000000 |   1) gamma-44 |  ...1. |               |  root() {
1000.000002 |   1) gamma-44 |  ..Z1. |   1.500 us    |    nmi();
1000.000010 |   1) gamma-44 |  ...1. |  10.000 us    |  } /* root */
1000.000000 |   2) clear-45 |  ...1. |  10.000 us    |  quiet();
'''


def duration(value):
    if value < 1000:
        return f'{value:.0f} ns'
    return f'{value / 1000:.3f} us'


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class CpuHoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        traces = {'kernel': helpers.fixture('pi4_sched')}
        for kind, text in [('mixed', stream(capture())), ('interrupts', INTERRUPTS)]:
            source = Path(cls.kernel.dir, kind + ('.jsonl' if kind == 'mixed' else '.txt'))
            source.write_text(text)
            traces[kind] = source
        cls.reports = {}
        for kind, mode in product(traces, ('single', 'sidecar')):
            destination = 'index.html' if (kind, mode) == ('kernel', 'single') else kind + '-' + mode + ('.html' if mode == 'single' else '')
            text, _ = cls.kernel.run(str(traces[kind]), '--no-config', '--timeline', '--enable-fold',
                                     '--filter', '--func-links', '--report-mode', mode, out=destination)
            report = Path(cls.kernel.dir, destination)
            if mode == 'sidecar':
                report = report / 'index.html'
            cls.reports[kind, mode] = report.relative_to(cls.kernel.dir).as_posix()
            report.write_text(instrument(text))
            if kind == 'kernel' and mode == 'single':
                Path(cls.kernel.dir, 'large.html').write_text(instrument(fixture(text, 25000)))
        cls.server = create_server(Path(cls.kernel.dir))
        worker = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        worker.start()
        cls.addClassCleanup(worker.join, 5)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = None
        self.errors = []

    def tearDown(self):
        if self.context:
            self.context.close()
        self.assertEqual(self.errors, [])

    def open(self, kind='kernel', mode='single', theme='light', ratio=1):
        if self.context:
            self.context.close()
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900}, device_scale_factor=ratio)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        name = 'large.html' if kind == 'large' else self.reports[kind, mode]
        self.page.goto(urljoin(self.server.url, name))
        self.page.evaluate('theme => { document.documentElement.dataset.theme=theme; }', theme)
        self.page.evaluate("async () => await fgSwitchView('timeline')")
        self.settle()

    def settle(self):
        self.page.evaluate('async () => { for (let frame=0;frame<2;frame++) await new Promise(requestAnimationFrame); }')

    def snapshot(self):
        return self.page.evaluate('''() => {
            const model=fgView('timeline').probe();
            return JSON.stringify({data:fgData.read('tlData'),selection:model.sel(),view:model.view(),
                irqs:Array.from(model.CPU_IRQ.entries()),runs:Array.from(model.CPU_SEGS.entries(),
                    ([cpu,runs])=>[cpu,Array.from(runs.s),Array.from(runs.e),Array.from(runs.t)])});
        }''')

    def hover(self):
        point = self.page.evaluate('''() => {
            const model=fgView('timeline').probe(),view=model.view();
            const canvas=document.querySelector('.tl-canvas').getBoundingClientRect();
            let best=null;
            for (const [cpu,runs] of model.CPU_SEGS) for (let index=0;index<runs.s.length;index++) {
                const left=Math.max(runs.s[index],view[0]),right=Math.min(runs.e[index],view[1]);
                if (right>left && (!best || right-left>best.right-best.left)) best={cpu,left,right};
            }
            if (!best) throw new Error('No visible CPU segment');
            return [canvas.left+(best.left+best.right-2*view[0])/2/(view[1]-view[0])*canvas.width,
                canvas.top+28+17*model.CPU_LIST.indexOf(best.cpu),
                Math.min(3,(best.right-best.left)/(view[1]-view[0])*canvas.width/4)];
        }''')
        self.page.mouse.move(point[0], point[1])
        self.page.locator('.tl-tip').wait_for(state='visible')
        self.settle()
        return point

    def reset_stats(self):
        self.page.evaluate('Object.assign(cpuHoverStats,{calls:0,lookups:0,records:0,milliseconds:0})')

    def test_pointer_moves_keep_the_preview_and_do_not_revisit_irq_history(self):
        for kind, mode, theme, ratio in product(('kernel', 'mixed', 'interrupts'), ('single', 'sidecar'),
                                                ('light', 'dark'), (1, 2)):
            with self.subTest(kind=kind, mode=mode, theme=theme, ratio=ratio):
                self.open(kind, mode, theme, ratio)
                self.assertEqual(self.page.evaluate('cpuHoverStats.calls'), 0)
                before = self.snapshot()
                point = self.hover()
                content = self.page.locator('.tl-tip-body').inner_html()
                pixels = self.page.locator('.tl-canvas').evaluate('canvas=>canvas.toDataURL()')
                self.reset_stats()
                for offset in (-1, 1, -1, 1, 0):
                    self.page.mouse.move(point[0] + point[2] * offset, point[1])
                    self.settle()
                    self.assertEqual(self.page.locator('.tl-tip-body').inner_html(), content)
                stats = self.page.evaluate('cpuHoverStats')
                self.assertGreater(stats['calls'], 0)
                self.assertEqual((stats['lookups'], stats['records']), (0, 0))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.page.locator('.tl-canvas').evaluate('canvas=>canvas.toDataURL()'), pixels)

    def test_cpu_and_both_window_edges_preserve_exact_nested_and_clipped_totals(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open('interrupts', mode)
                before = self.snapshot()
                records = dict(self.page.evaluate('Array.from(fgView("timeline").probe().CPU_IRQ.entries())'))
                cases = [(0, 0, 10000), (1, 0, 10000), (0, 1500, 10000), (0, 1500, 3000),
                         (0, 2200, 4000), (0, 4100, 10000), (2, 0, 10000), (0, 0, 10000)]
                for cpu, start, end in cases:
                    expected = {}
                    for context, label in enumerate(('task', 'softirq', 'hardirq', 'NMI')):
                        total = sum(max(0, min(stop, end) - max(begin, start))
                                    for begin, stop, kind in records.get(cpu, []) if kind == context)
                        if context and total:
                            expected[label] = duration(total)
                    self.reset_stats()
                    actual = self.page.evaluate('''segment => {
                        const model=fgView('timeline').probe(),element=document.createElement('div');
                        for (let repeat=0;repeat<3;repeat++) element.innerHTML=model.cpuTip(segment);
                        return Object.fromEntries(Array.from(element.querySelectorAll('.t-row'),row=>
                            [row.querySelector('.t-k').textContent,row.querySelector('.t-v').textContent])
                            .filter(([key])=>['softirq','hardirq','NMI'].includes(key)));
                    }''', dict(cpu=cpu, s=start, e=end, t=0))
                    self.assertEqual(actual, expected)
                    self.assertEqual(self.page.evaluate('cpuHoverStats.lookups'), 1)
                self.assertEqual(self.snapshot(), before)

    def test_summary_reuses_only_irq_totals_not_the_task_label(self):
        self.open('interrupts')
        result = self.page.evaluate('''() => {
            const model=fgView('timeline').probe(),segment={cpu:0,s:0,e:10000,t:0};
            const first=model.cpuTip(segment);
            Object.assign(cpuHoverStats,{lookups:0,records:0});
            return {first,second:model.cpuTip({...segment,t:1}),lookups:cpuHoverStats.lookups};
        }''')
        self.assertNotEqual(result['first'], result['second'])
        self.assertEqual(result['lookups'], 0)

    def test_text_source_roundtrip_keeps_summary_and_recorded_data(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode)
                before = self.snapshot()
                self.hover()
                content = self.page.locator('.tl-tip-body').inner_html()
                self.page.evaluate("async () => await fgSwitchView('text')")
                self.page.locator('.line-container.expandable').first.locator('.line-content').click()
                self.page.locator('.expanded-content:visible').first.wait_for(state='visible')
                self.page.evaluate("async () => await fgSwitchView('timeline')")
                self.settle()
                self.reset_stats()
                self.hover()
                self.assertEqual(self.page.locator('.tl-tip-body').inner_html(), content)
                self.assertEqual(self.page.evaluate('cpuHoverStats.lookups'), 0)
                self.assertEqual(self.snapshot(), before)

    def test_large_history_is_scanned_once_then_retains_only_the_last_window(self):
        self.open('large')
        self.assertEqual(self.page.evaluate('cpuHoverStats.lookups'), 0)
        self.hover()
        self.assertEqual(self.page.evaluate('cpuHoverStats.lookups'), 1)
        self.assertEqual(self.page.evaluate('cpuHoverStats.records'), 25000)
        self.reset_stats()
        self.page.evaluate('''() => {
            const model=fgView('timeline').probe(),runs=model.CPU_SEGS.get(0);
            const segment={cpu:0,s:runs.s[0],e:runs.e[0],t:runs.t[0]};
            for (let repeat=0;repeat<32;repeat++) model.cpuTip({...segment});
        }''')
        self.assertEqual(self.page.evaluate('cpuHoverStats.lookups'), 0)
        self.assertEqual(self.page.evaluate('cpuHoverStats.records'), 0)


if __name__ == '__main__':
    unittest.main()
