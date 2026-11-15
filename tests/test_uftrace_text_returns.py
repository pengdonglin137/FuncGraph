"""Text return summaries and errno filters must retain structured trace authority."""
import io
import json
from html.parser import HTMLParser
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urljoin

import helpers
from fglib import btf, inputs, report, timeline
from fglib.viewer import create_server
from test_btf import BtfBuilder, make_elf
from test_uftrace_details import arguments
from test_uftrace_import import HEADER, call, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


CASES = [('signed8', 's8', '0xdeadbeefffffffea', '-22', -22),
         ('signed16', 's16', '0xdeadbeefffffffea', '-22', -22),
         ('signed32', 's32', '0xdeadbeefffffffea', '-22', -22),
         ('signed64', 's64', '0xfffffffffffffffe', '-2', -2),
         ('unsigned', 'u64', '0xffffffffffffffff', '0xffffffffffffffff', None),
         ('small_unsigned', 'u16', '0xdeadbeefffffffea', '0xffea', None),
         ('boolean', 'bool', '0xfffffffffffffffe', 'false', None),
         ('enumerated', 'enum', '0xdeadbeefffffffff', '4294967295', None),
         ('nothing', 'void', '0xfffffffffffffffe', None, None),
         ('pointer', 'ptr', '0xffffffffffffffea', '0xffffffffffffffea', -22),
         ('null_pointer', 'ptr', '0x0', '0x0', None),
         ('zero', 's32', '0x0', '0', None),
         ('memcmp', 's32', '0xfffffffffffffffe', '-2', None)]


def return_types():
    source = BtfBuilder()
    for name, kind, *_ in CASES:
        arguments(source, name, [], kind)
    return source.blob()


def recording():
    records = [call('entry', 'user', 0, 0, 'main', 1, comm='returns'),
               call('entry', 'user', 10, 1, 'signed32', 2),
               call('exit', 'user', 10000, 1, 'signed32', 2, user_retval='-2'),
               call('entry', 'user', 20000, 1, 'user_zero', 3),
               call('exit', 'user', 20100, 1, 'user_zero', 3, user_retval='0'),
               call('entry', 'user', 30000, 1, 'raw_only', 4),
               call('exit', 'user', 30100, 1, 'raw_only', 4, payload_hex='feffffffffffffff'),
               call('exit', 'user', 40000, 0, 'main', 1, user_retval='0')]
    for index, (name, _, raw, _, _) in enumerate(CASES):
        timestamp = 100 + index * 200
        records.extend([call('entry', 'kernel', timestamp, 0, name, 100 + index),
                        call('exit', 'kernel', timestamp + 50, 0, name, 100 + index, retval=raw)])
    records.extend([call('entry', 'kernel', 8000, 0, 'unknown', 200),
                    call('exit', 'kernel', 8100, 0, 'unknown', 200, retval='0xfffffffffffffffe')])
    return sorted(records, key=lambda record: record['time_ns'])


def build(records=None, **options):
    database = btf.ReturnTypeDB()
    database.add(btf.BtfInfo(return_types(), word_size=8))
    return inputs.UftraceInput(io.StringIO(stream(recording() if records is None else records))).build(
        rettypes=database, enable_fold=True, **options)


class TextReturnModelTest(unittest.TestCase):
    def test_kernel_scalars_use_btf_width_without_changing_original_bits(self):
        builder = build()
        data = builder.to_data()
        for name, _, raw, displayed, errno in CASES:
            with self.subTest(name=name):
                index = next(index for index, identifier in enumerate(builder.s_name)
                             if builder.strings[identifier] == name and not builder.s_flags[index] & timeline.F_USER)
                entry = builder.parsed[builder.s_line[index] - 1]
                exited = builder.parsed[builder.s_eline[index] - 1]
                self.assertIsNone(entry.ret_value)
                self.assertEqual(exited.ret_value, displayed)
                self.assertEqual(exited.get('ret_errno'), errno)
                self.assertEqual(builder.s_ret[index], raw)
                self.assertEqual(builder.records[builder.s_eline[index] - 3]['retval'], raw)
                self.assertIn(raw, [data['strings'][value] for value in data['cols']['ret'] if value >= 0])

    def test_user_scalar_returns_do_not_borrow_kernel_errno_semantics(self):
        builder = build()
        users = {row.display_func_name: row for row in builder.parsed
                 if row.get('space') == 'user' and row.get('record_type') == 'exit'}
        for name, value in [('signed32', '-2'), ('user_zero', '0'), ('main', '0'), ('raw_only', None)]:
            self.assertEqual(users[name].ret_value, value)
            self.assertIsNone(users[name].get('ret_errno'))
        self.assertIn('raw return bytes: feffffffffffffff', users['raw_only'].raw_line)
        orphan = build([call('exit', 'user', 1, 0, 'signed32', 1, user_retval='-2')]).parsed[-1]
        self.assertEqual(orphan.ret_value, '-2')
        self.assertIsNone(orphan.get('ret_errno'))

    def test_unknown_missing_and_disabled_returns_do_not_invent_errno(self):
        builder = build()
        unknown = next(row for row in builder.parsed if row.display_func_name == 'unknown' and row.get('record_type') == 'exit')
        self.assertEqual(unknown.ret_value, '0xfffffffffffffffe')
        self.assertIsNone(unknown.get('ret_errno'))
        untyped = inputs.UftraceInput(io.StringIO(stream(recording()))).build(enable_fold=True)
        self.assertTrue(all(row.get('ret_errno') is None for row in untyped.parsed))
        metadata = dict(HEADER, kernel_settings={'options/funcgraph-retval': '0'})
        disabled = inputs.UftraceInput(io.StringIO(stream(recording(), metadata))).build(enable_fold=True)
        self.assertTrue(all(row.ret_value is None for row in disabled.parsed if row.get('space') == 'kernel'))
        self.assertIn('0', [row.ret_value for row in disabled.parsed if row.get('space') == 'user'])
        missing = build([call('entry', 'kernel', 1, 0, 'signed32', 1)])
        self.assertTrue(all(row.ret_value is None for row in missing.parsed))

    def test_orphan_and_32_bit_pointer_returns_reuse_timeline_classification(self):
        source = BtfBuilder()
        arguments(source, 'inner', [], 'ptr')
        database = btf.ReturnTypeDB()
        database.add(btf.BtfInfo(source.blob(), word_size=4))
        records = [call('exit', 'kernel', 1, 0, 'inner', 1, retval='0xdeadbeefffffffea')]
        builder = inputs.UftraceInput(io.StringIO(stream(records))).build(rettypes=database, enable_fold=True)
        row = builder.parsed[-1]
        self.assertEqual(row.ret_value, '0xffffffea')
        self.assertEqual(row.get('ret_errno'), -22)
        self.assertIsNone(row.fold_id)
        self.assertEqual(builder.s_ret, ['0xdeadbeefffffffea'])

    def test_text_projection_does_not_repeat_return_type_classification(self):
        builder = build()
        with patch.object(builder, '_err_value', side_effect=AssertionError('repeated classification')):
            projected = builder._text_lines(True, True)
        self.assertEqual([row.get('ret_errno') for row in projected], [row.get('ret_errno') for row in builder.parsed])
        self.assertIn(-22, [row.get('ret_errno') for row in projected])


class ReturnMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.options = None
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == 'div' and 'line-container' in attributes.get('class', '').split():
            self.rows.append(attributes)
        if tag == 'button' and attributes.get('data-field') == 'ret':
            self.options = json.loads(attributes['data-values'])


class TextReturnReportTest(unittest.TestCase):
    def test_standalone_plain_and_sidecar_keep_error_options_and_return_metadata(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        Path(kernel.vmlinux).write_bytes(make_elf({'.BTF': return_types()}))
        trace = Path(kernel.dir, 'returns.jsonl')
        trace.write_text(stream(recording()))
        for mode, timeline_enabled in [('single', True), ('single', False), ('sidecar', True)]:
            with self.subTest(mode=mode, timeline=timeline_enabled):
                options = ['--no-config', '--enable-fold', '--filter', '--report-mode', mode]
                if timeline_enabled:
                    options.append('--timeline')
                content, _ = kernel.run(str(trace), *options, out='report' if mode == 'sidecar' else 'report.html')
                markup = ReturnMarkup(content)
                self.assertEqual(markup.options, ['all', '-22', '-2'])
                if mode == 'sidecar':
                    descriptor = helpers.json_script(content, 'fgDataManifest')
                    manifest_path = Path(kernel.dir, 'report', descriptor['manifest']['file'])
                    resources = json.loads(manifest_path.read_text())['resources']
                    markup = ReturnMarkup((manifest_path.parent / resources['fgText']['file']).read_text())
                exits = {row['data-func']: row for row in markup.rows if row.get('data-space') == 'kernel'
                         and row.get('data-fold-type') == 'exit'}
                for name, _, _, displayed, errno in CASES:
                    self.assertEqual(exits[name].get('data-fold-ret'), displayed)
                    self.assertEqual(exits[name].get('data-ret'), str(errno) if errno is not None else None)
                self.assertFalse(any('data-ret' in row for row in markup.rows if row.get('data-space') == 'user'))

    def test_single_address_space_reports_keep_scalar_summaries_and_scoped_errno(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        Path(kernel.vmlinux).write_bytes(make_elf({'.BTF': return_types()}))
        for space in ('user', 'kernel'):
            with self.subTest(space=space):
                trace = Path(kernel.dir, space + '.jsonl')
                trace.write_text(stream([record for record in recording() if record['space'] == space]))
                content, _ = kernel.run(str(trace), '--no-config', '--enable-fold', '--filter', '--timeline')
                markup = ReturnMarkup(content)
                self.assertEqual(markup.options, ['all', '-22', '-2'] if space == 'kernel' else None)
                self.assertTrue(any('data-fold-ret' in row for row in markup.rows))
                self.assertTrue(all(row.get('data-space') == space for row in markup.rows if row.get('data-func')))

    def test_native_return_metadata_keeps_existing_semantics(self):
        generator = report.HtmlGenerator.__new__(report.HtmlGenerator)
        generator.enable_fold = True
        for value, errno in [('0xfffffffffffffffe', '-2'), ('-22', '-22'), ('0XFFFFFFFFFFFFFFEA', '-22'),
                             ('-9999', None), ('false', None), ('0', None)]:
            with self.subTest(value=value):
                row = {'ret_value': value, 'fold_id': 'a', 'fold_type': 'exit'}
                attributes = generator._text_attributes(row, '', {}, {})
                self.assertEqual(attributes.get('ret'), errno)
                self.assertEqual(attributes.get('fold-ret'), value)


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TextReturnBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        Path(cls.kernel.vmlinux).write_bytes(make_elf({'.BTF': return_types()}))
        trace = Path(cls.kernel.dir, 'returns.jsonl')
        trace.write_text(stream(recording()))
        for mode in ('single', 'sidecar'):
            cls.kernel.run(str(trace), '--no-config', '--enable-fold', '--filter', '--timeline', '--report-mode', mode,
                           out='sidecar' if mode == 'sidecar' else 'index.html')
        cls.server = create_server(Path(cls.kernel.dir))
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

    def open_report(self, filename='index.html'):
        context = self.browser.new_context(viewport=dict(width=1400, height=900))
        self.addCleanup(context.close)
        context.add_init_script('localStorage.clear()')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, filename), wait_until='load')
        page.evaluate("async () => { await fgSwitchView('text'); }")
        return page

    def test_folded_returns_keep_zero_bool_and_user_values_without_false_error_color(self):
        for filename in ('index.html', 'sidecar/index.html', 'sidecar/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                for space, name, value, error in [('kernel', 'signed32', '-22', True),
                                                  ('kernel', 'unsigned', '0xffffffffffffffff', False),
                                                  ('kernel', 'boolean', 'false', False),
                                                  ('kernel', 'zero', '0', False),
                                                  ('user', 'signed32', '-2', False),
                                                  ('user', 'user_zero', '0', False)]:
                    row = page.locator(f'[data-space={space}][data-func={name}][data-fold-type=entry]')
                    row.evaluate("row => _setFolded(row.getAttribute('data-fold-id'), true)")
                    returned = row.locator('.fold-summary .summary-ret')
                    self.assertEqual(returned.inner_text(), 'ret=' + value)
                    self.assertEqual(returned.evaluate("node => node.classList.contains('summary-error')"), error)

    def test_errno_filter_matches_timeline_and_never_labels_user_returns(self):
        for filename in ('index.html', 'sidecar/index.html', 'sidecar/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                data = page.evaluate("async () => JSON.stringify(await fgData.load('tlData'))")
                page.evaluate("() => { document.getElementById('filterInput').value='ret:all'; applyFilter(); }")
                page.wait_for_function('() => !fgFilterProgress')
                actual = page.locator('#content [data-ret]:visible').evaluate_all('rows => rows.map(row => +row.dataset.lineNumber).sort((left,right)=>left-right)')
                self.assertTrue(actual)
                expected = page.evaluate("() => { const data=fgData.read('tlData'); return data.errs.map(error=>data.cols.eline[error[0]]).sort((left,right)=>left-right); }")
                self.assertEqual(actual, expected)
                self.assertEqual(page.locator('#content [data-space=user]:visible').count(), 0)
                page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), data)
                page.evaluate("async () => { clearFilter(); await fgSwitchView('text'); }")
                self.assertTrue(page.locator('#content [data-space=user]:visible').count())
                page.evaluate('''() => { document.getElementById('filterInput').value='ret:="-22"'; applyFilter(); }''')
                page.wait_for_function('() => !fgFilterProgress')
                values = page.locator('#content [data-ret]:visible').evaluate_all('rows => rows.map(row=>row.dataset.ret)')
                self.assertTrue(values)
                self.assertEqual(set(values), {'-22'})
                self.assertEqual(len(values), sum(error[1] == -22 for error in json.loads(data)['errs']))


if __name__ == '__main__':
    unittest.main()
