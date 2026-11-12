"""Recorded mixed-stack ancestry and pixel-aligned Text hierarchy guides."""

import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from itertools import product
from pathlib import Path
import tempfile
import threading
import unittest

import helpers
from fglib import uftrace
from fglib.report import HtmlGenerator
from test_text_tree import Markup
from test_uftrace_import import HEADER, ORIGIN, build, call, capture, invoke

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


TRACE = Path(helpers.ROOT) / 'samples/sample_mixed.jsonl'


def event(space, timestamp, tid=42, cpu=0, **fields):
    return dict(type='event', space=space, time_ns=ORIGIN + timestamp, tid=tid,
                cpu=cpu if space == 'kernel' else None, name='callsite', text='', **fields)


class MixedTreeModelTest(unittest.TestCase):
    def test_real_callsite_rows_follow_the_recorded_parent(self):
        with TRACE.open(encoding='utf-8') as stream:
            metadata, records = uftrace.read_export(stream)
        builder = uftrace.UftraceTimeline(metadata, records).build(enable_fold=True)
        main = next(line for line in builder.parsed if line.raw_func_name == 'main')
        events = [line for line in builder.parsed if line.get('record_type') == 'event']
        self.assertEqual(len(events), 25)
        self.assertIsNone(events[0].fold_id)
        for line in events[1:]:
            self.assertIn(main.fold_id, line.fold_ancestors.split())
            self.assertEqual(line.fold_type, 'inner')
            self.assertEqual(line.fold_id, line.fold_ancestors.split()[-1])
            self.assertTrue(line.raw_line.endswith(' ' * (2 + 2 * line.call_depth) + '/* callsite:  */'))
        for line in builder.parsed:
            if line.fold_type == 'entry':
                column = line.raw_line.split(' | ', 4)[-1]
                self.assertEqual(len(column) - len(column.lstrip()), 1 + 2 * line.call_depth)
        self.assertEqual(builder.to_data()['n'], 714)

    def test_events_do_not_borrow_another_task_or_address_space_stack(self):
        builder = build(sorted(capture() + [event('user', 230), event('kernel', 400),
                                     event('user', 410), event('user', 420, tid=99),
                                     event('user', 905), event('kernel', 905)], key=lambda record: record['time_ns']))
        events = [line for line in builder.parsed if line.get('record_type') == 'event']
        self.assertEqual([line.call_depth for line in events], [2, 4, 2, None, None, None])
        self.assertEqual([line.fold_type for line in events], ['inner'] * 3 + [None] * 3)
        user, kernel, resumed = events[:3]
        self.assertEqual(user.fold_id, resumed.fold_id)
        self.assertIn(user.fold_id, kernel.fold_ancestors.split())
        self.assertNotEqual(user.fold_id, kernel.fold_id)
        detached = build(sorted(capture() + [event('kernel', 400), event('user', 410)],
                                key=lambda record: record['time_ns']),
                         dict(HEADER, clock='unknown'))
        kernel, user = [line for line in detached.parsed if line.get('record_type') == 'event']
        self.assertEqual([kernel.call_depth, user.call_depth], [2, 2])
        self.assertTrue(set(kernel.fold_ancestors.split()).isdisjoint(user.fold_ancestors.split()))

    def test_loss_and_incomplete_calls_do_not_invent_event_ancestry(self):
        for space in ('user', 'kernel'):
            with self.subTest(space=space):
                records = [call('entry', space, 100, 0, 'outer', 1),
                           dict(type='lost', space=space, time_ns=ORIGIN + 200, tid=42,
                                cpu=0 if space == 'kernel' else None, count=1),
                           event(space, 210), call('entry', space, 220, 0, 'next', 2),
                           event(space, 230), call('exit', space, 240, 0, 'next', 2)]
                events = [line for line in build(records).parsed if line.get('record_type') == 'event']
                self.assertIsNone(events[0].fold_id)
                self.assertEqual(events[0].fold_ancestors, '')
                self.assertEqual(events[1].fold_type, 'inner')
                self.assertEqual(len(events[1].fold_ancestors.split()), 1)

    def test_folding_disabled_keeps_events_free_of_fold_metadata(self):
        builder = build(sorted(capture() + [event('user', 230)], key=lambda record: record['time_ns']))
        rows = builder._text_lines(False, True)
        event_row = next(line for line in rows if line.get('record_type') == 'event')
        self.assertIsNone(event_row.fold_id)
        self.assertIsNone(event_row.call_depth)
        self.assertEqual(event_row.fold_ancestors, '')

    def test_event_pipes_are_not_mistaken_for_column_separators(self):
        for body in ('/* event: x|y */', '<span class="hl-comment">/* event: x|y */</span>'):
            with self.subTest(body=body):
                before = ' 0 | 42 | U |    ' + body
                after = HtmlGenerator._fold_line_html(before, 'inner', None)
                self.assertIn('<span class="call-indent">    </span>' + body, after)
                self.assertEqual(''.join(Markup(before).text), ''.join(Markup(after).text))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *arguments):
        pass


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class MixedTreeBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='fg-mixed-tree-')
        cls.root = Path(cls.directory.name)
        for mode, filename in [('single', 'mixed.html'), ('sidecar', 'sidecar')]:
            result = invoke(TRACE, '--timeline', '--enable-fold', '--filter', '--report-mode', mode,
                            '--user-src', Path(helpers.ROOT) / 'samples', '--output', cls.root / filename)
            if result.returncode:
                raise AssertionError(result.stderr)
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch()
        except Exception as error:
            cls.runtime.stop()
            cls.directory.cleanup()
            raise unittest.SkipTest(str(error))
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(cls.root)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/'

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def open_text(self, page, filename='mixed.html'):
        page.goto(self.url + filename)
        page.evaluate("fgSwitchView('text')")
        page.wait_for_selector('#L6 .call-indent')
        page.mouse.move(0, 0)

    def assert_continuous_rail(self, page, first='#L5', last='#L10', entry='#L4'):
        clip = page.evaluate('''({first, last, entry}) => {
            const icon = document.querySelector(entry + ' .fold-icon').getBoundingClientRect();
            const top = document.querySelector(first).getBoundingClientRect().top;
            const bottom = document.querySelector(last).getBoundingClientRect().bottom;
            return {x:Math.floor(icon.left + icon.width / 2) - 2, y:Math.ceil(top), width:5,
                height:Math.floor(bottom) - Math.ceil(top)};
        }''', dict(first=first, last=last, entry=entry))
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        visible = page.screenshot(clip=clip)
        page.locator('#foldGuidesBtn').click()
        hidden = page.screenshot(clip=clip)
        pixels = page.evaluate('''async images => {
            const decode = async encoded => {
                const image = new Image(); image.src = 'data:image/png;base64,' + encoded;
                await image.decode();
                const canvas = document.createElement('canvas');
                canvas.width = image.width; canvas.height = image.height;
                const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
                return {pixels:context.getImageData(0, 0, canvas.width, canvas.height).data,
                    width:canvas.width, height:canvas.height};
            };
            const [visible, hidden] = await Promise.all(images.map(decode));
            const missing = [], widths = new Set();
            for (let row = 1; row < visible.height - 1; row++) {
                let painted = 0;
                for (let column = 0; column < visible.width; column++) {
                    const offset = (row * visible.width + column) * 4;
                    if ([0, 1, 2].some(channel => Math.abs(visible.pixels[offset + channel] -
                        hidden.pixels[offset + channel]) >= 12)) painted++;
                }
                if (!painted) missing.push(row);
                widths.add(painted);
            }
            return {missing, widths:Array.from(widths), expected:Math.max(1, Math.round(devicePixelRatio))};
        }''', [base64.b64encode(image).decode() for image in (visible, hidden)])
        self.assertEqual(pixels['missing'], [], 'A recorded parent rail must continue through callsite, entry and exit rows')
        self.assertEqual(pixels['widths'], [pixels['expected']], 'The trunk must retain one uniform physical stroke')
        page.locator('#foldGuidesBtn').click()

    def test_mixed_rail_is_continuous_in_both_themes_at_fractional_density(self):
        for scale, theme in product((1, 1.25, 1.5, 2), ('light', 'dark')):
            with self.subTest(scale=scale, theme=theme):
                page = self.browser.new_page(viewport=dict(width=1500, height=900), device_scale_factor=scale)
                try:
                    self.open_text(page)
                    page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
                    icon = page.locator('#L4 .fold-icon').bounding_box()
                    indent = page.locator('#L4 .call-indent').bounding_box()
                    self.assertGreaterEqual(icon['x'], indent['x'] - 0.1)
                    self.assert_continuous_rail(page)
                finally:
                    page.close()

    def test_sidecar_events_mount_and_fold_with_their_actual_parent(self):
        page = self.browser.new_page(viewport=dict(width=1500, height=900))
        try:
            self.open_text(page, 'sidecar/index.html')
            self.assert_continuous_rail(page)
            page.locator('#L4 .fold-icon').click()
            self.assertFalse(page.locator('#L5').is_visible())
            self.assertFalse(page.locator('#L6').is_visible())
            self.assertTrue(page.locator('#L3').is_visible())
            page.locator('#L4 .fold-icon').click()
            self.assertTrue(page.locator('#L5').is_visible())
            page.mouse.move(0, 0)
            self.assert_continuous_rail(page)
        finally:
            page.close()

    def test_hover_emphasizes_the_parent_rail_through_callsite_rows(self):
        page = self.browser.new_page(viewport=dict(width=1500, height=900))
        try:
            self.open_text(page)
            indent = page.locator('#L8 .call-indent')
            normal = indent.screenshot()
            page.locator('#L4 .fold-icon').hover()
            emphasized = indent.screenshot()
            self.assertNotEqual(normal, emphasized)
            page.mouse.move(0, 0)
            self.assertEqual(indent.screenshot(), normal)
        finally:
            page.close()

    def test_odd_indent_ancestors_align_with_their_own_controls(self):
        kernel = helpers.FakeKernel()
        try:
            for padding in (' ', '   ', '\t '):
                with self.subTest(padding=repr(padding)):
                    trace = self.root / 'odd.txt'
                    trace.write_text('# tracer: function_graph\n' + ''.join(
                        ' 0)               |' + padding + '  ' * depth + body + '\n'
                        for depth, body in [(0, 'outer() { /* <-caller+0x1/0x20 */'),
                                            (1, 'inner() { /* <-caller+0x1/0x20 */'),
                                            (2, 'leaf(); /* <-caller+0x1/0x20 */'),
                                            (1, '} /* inner */'), (0, '} /* outer */')]))
                    report, _ = kernel.run(trace, '--enable-fold')
                    (self.root / 'odd.html').write_text(report, encoding='utf-8')
                    page = self.browser.new_page(viewport=dict(width=1500, height=900))
                    try:
                        page.goto(self.url + 'odd.html')
                        self.assert_continuous_rail(page, first='#L3', last='#L5', entry='#L2')
                    finally:
                        page.close()
        finally:
            kernel.close()
