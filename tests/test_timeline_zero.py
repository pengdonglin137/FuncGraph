"""Zero is a valid string-table index and CPU, not an absent sparse value."""
from pathlib import Path
import threading
import unittest

import helpers
from fglib.timeline import TimelineBuilder, build_timeline
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def trace(body, cpu=0):
    return ('# tracer: function_graph\n'
            f'1.000000 |   {cpu})   task-42   |  ...1. |   0.100 us   |  {body}\n')


class TimelineZeroTest(unittest.TestCase):
    def test_first_interned_call_site_is_preserved(self):
        data = build_timeline(trace('leaf(); /* <-caller+0x1/0x20 */').splitlines()).to_data()
        self.assertEqual(data['strings'][0], 'caller+0x1/0x20')
        self.assertEqual(data['cols']['caller'], [0])

    def test_first_interned_arguments_are_preserved(self):
        data = build_timeline(trace('leaf(value=7);').splitlines()).to_data()
        self.assertEqual(data['strings'][0], 'value=7')
        self.assertEqual(data['cols']['args'], [0])

    def test_module_reusing_the_first_function_name_is_preserved(self):
        data = build_timeline(trace('same [same]();').splitlines()).to_data()
        self.assertEqual(data['strings'], ['same'])
        self.assertEqual(data['cols']['module'], [0])

    def test_return_value_reusing_the_first_string_is_preserved(self):
        builder = TimelineBuilder(trace('leaf(); /* ret=0x0 */').splitlines())
        self.assertEqual(builder._s('0'), 0)
        builder.build()
        self.assertEqual(builder.to_data()['cols']['ret'], [0])

    def test_cpu_zero_is_preserved(self):
        data = build_timeline(trace('leaf();').splitlines()).to_data()
        self.assertEqual(data['cols']['cpu'], [0])

    def test_absent_values_do_not_hide_a_valid_zero_in_the_same_column(self):
        text = trace('leaf(); /* <-caller+0x1/0x20 */') + ' 0)   0.100 us   |  other();\n'
        data = build_timeline(text.splitlines()).to_data()
        self.assertEqual(data['cols']['caller'], [0, -1])

    def test_truly_absent_columns_remain_compact(self):
        for text in ['', 'leaf();\n']:
            with self.subTest(text=text):
                data = build_timeline(text.splitlines()).to_data()
                for column in ['ret', 'caller', 'args', 'module', 'cpu', 'eline']:
                    self.assertIsNone(data['cols'][column], column)


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TimelineZeroBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def test_zero_values_reach_details_in_single_and_sidecar_reports(self):
        cases = [('caller', 'leaf(); /* <-caller+0x1/0x20 */', 'caller+0x1/0x20'),
                 ('args', 'leaf(value=7);', 'value=7'),
                 ('module', 'same [same]();', '[same]')]
        source = Path(self.kernel.dir) / 'zero.txt'
        for mode in ['single', 'sidecar']:
            for column, body, expected in cases:
                with self.subTest(mode=mode, column=column):
                    source.write_text(trace(body))
                    output = mode + '-' + column + ('.html' if mode == 'single' else '')
                    options = ['--no-config', '--timeline', '--func-links', '--report-mode', mode]
                    if mode == 'sidecar':
                        options += ['--no-text-view', '--keep-source']
                    self.kernel.run(str(source), *options, out=output)
                    report = Path(self.kernel.dir) / output
                    server = None
                    page = self.browser.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    try:
                        if mode == 'sidecar':
                            server = create_server(report)
                            thread = threading.Thread(target=server.serve_forever,
                                                      kwargs={'poll_interval': 0.02}, daemon=True)
                            thread.start()
                            url = server.url
                        else:
                            url = report.as_uri()
                        page.goto(url, wait_until='load')
                        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
                        data = page.evaluate("fgData.read('tlData')")
                        self.assertEqual(data['cols'][column], [0])
                        self.assertEqual(data['cols']['cpu'], [0])
                        detail = page.locator('.tl-detail').inner_text()
                        self.assertIn(expected, detail)
                        self.assertIn('on CPU 0', detail)
                        if column == 'caller':
                            self.assertTrue(data['sources']['callers']['0'])
                            self.assertIn('kernel/fake.c:2', detail)
                        if mode == 'sidecar':
                            self.assertTrue(data['no_text'])
                            self.assertEqual(page.locator('#content #L2').count(), 0)
                        self.assertEqual(errors, [])
                    finally:
                        page.close()
                        if server is not None:
                            server.shutdown()
                            thread.join(timeout=5)
                            server.server_close()


if __name__ == '__main__':
    unittest.main()
