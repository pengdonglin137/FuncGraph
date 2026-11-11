"""Captured kernel callers preserve raw PCs and reuse call-site/inline resolution."""
import io
from pathlib import Path
import tempfile
import unittest

import helpers
from fglib import timeline, uftrace
from fglib.report import HtmlGenerator
from test_timeline import invariant_errors
from test_uftrace_import import build, capture, invoke, stream


SYMBOL = {'name': 'traced_outer', 'address': '0xfffefff8', 'offset': 8, 'size': 256,
          'size_basis': 'next_symbol', 'source': 'saved_kallsyms'}


def records_with_caller(module=None):
    records = capture()
    records[4]['retaddr_symbol'] = dict(SYMBOL)
    if module:
        records[4]['retaddr_symbol']['module'] = module
    return records


class UftraceCallersTest(unittest.TestCase):
    def test_verified_spec_keeps_return_offset_and_original_address(self):
        for module in (None, 'caller_mod'):
            with self.subTest(module=module):
                builder = build(records_with_caller(module))
                data = builder.to_data()
                cols = data['cols']
                index = next(index for index, caller in enumerate(cols['caller']) if caller >= 0)
                caller = cols['caller'][index]
                suffix = ' [caller_mod]' if module else ''
                self.assertEqual(data['strings'][caller], 'traced_outer+0x8/0x100' + suffix)
                self.assertEqual(data['uftrace']['kernel_details'][cols['line'][index]]['retaddr'], '0xffff0000')
                self.assertEqual(data['uftrace']['caller_symbols'][caller]['offset'], 8)
                row = builder.parsed[cols['line'][index] - 1]
                self.assertTrue(row.expandable)
                self.assertEqual(row.func_info, 'traced_outer+0x8/0x100' + suffix)
                self.assertEqual(HtmlGenerator._adjust_func_info(row.func_info, remove_suffix=False),
                                 'traced_outer+0x7/0x100' + suffix)
                self.assertIn(row.func_info, row.raw_line)
                self.assertFalse(cols['flags'][index] & timeline.F_USER)
                self.assertEqual(invariant_errors(data), [])

    def test_legacy_raw_only_export_is_not_guessed(self):
        builder = build(capture())
        data = builder.to_data()
        self.assertNotIn('caller_symbols', data['uftrace'])
        self.assertTrue(all(not row.expandable for row in builder.parsed))
        self.assertIn('0xffff0000', data['strings'])

    def test_kernel_only_callers_preserve_pool_index_zero(self):
        data = build([record for record in records_with_caller() if record['space'] == 'kernel']).to_data()
        self.assertEqual(data['cols']['caller'][0], 0)
        self.assertEqual(data['strings'][0], 'traced_outer+0x8/0x100')
        self.assertEqual(data['uftrace']['caller_symbols'][0], SYMBOL)
        self.assertEqual(data['uftrace']['spaces'], ['kernel'])
        self.assertEqual(invariant_errors(data), [])

    def test_symbol_arithmetic_provenance_and_names_are_validated(self):
        invalid = [{'offset': 0}, {'offset': 9}, {'offset': True}, {'size': 7}, {'size': -1},
                   {'size': 1 << 64}, {'address': '0x0'}, {'address': '0xfffefff9'},
                   {'name': ''}, {'name': 'bad+name'}, {'name': '../bad'},
                   {'module': 'bad/module'}, {'module': ''}, {'module': None},
                   {'source': 'live_kallsyms'}, {'size_basis': 'elf'}, {'offset': None}]
        for updates in invalid:
            with self.subTest(updates=updates):
                records = records_with_caller()
                records[4]['retaddr_symbol'].update(updates)
                with self.assertRaisesRegex(uftrace.UftraceInputError, 'return-address symbol'):
                    uftrace.read_export(io.StringIO(stream(records)))

    def test_symbols_only_describe_kernel_entries_with_a_raw_pc(self):
        for update in [{'space': 'user', 'cpu': None}, {'type': 'exit'}, {'type': 'event'}]:
            with self.subTest(update=update):
                records = records_with_caller()
                records[4].update(update)
                with self.assertRaises(uftrace.UftraceInputError):
                    uftrace.read_export(io.StringIO(stream(records)))
        for symbol in [None, [], 'root', {'name': 'root'}]:
            with self.subTest(symbol=symbol):
                records = records_with_caller()
                records[4]['retaddr_symbol'] = symbol
                with self.assertRaises(uftrace.UftraceInputError):
                    uftrace.read_export(io.StringIO(stream(records)))
        records = records_with_caller()
        del records[4]['retaddr']
        with self.assertRaises(uftrace.UftraceInputError):
            uftrace.read_export(io.StringIO(stream(records)))

    def test_return_at_end_of_captured_range_is_valid(self):
        records = records_with_caller()
        records[4]['retaddr_symbol']['size'] = 8
        self.assertIn('traced_outer+0x8/0x8', build(records).to_data()['strings'])

    def test_real_callsite_sources_and_inline_frames_replace_entry_fallback(self):
        kernel = helpers.FakeKernel(inline_source=True)
        self.addCleanup(kernel.close)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'capture.jsonl'
            trace.write_text(stream(records_with_caller()), encoding='utf-8')
            baseline = None
            for options in ([], ['--no-text-view', '--keep-source']):
                with self.subTest(options=options):
                    html, _ = kernel.run(str(trace), '--no-config', '--timeline', '--enable-fold',
                                         '--func-links', '--base-url', 'https://kernel.invalid/src', *options)
                    data = helpers.json_script(html, 'tlData')
                    cols = data['cols']
                    index = next(index for index, caller in enumerate(cols['caller']) if caller >= 0)
                    caller = str(cols['caller'][index])
                    self.assertEqual([frame[0] for frame in data['sources']['callers'][caller]],
                                     ['traced_outer', 'inline_helper'])
                    self.assertEqual([frame[0] for frame in data['inline'][caller]],
                                     ['traced_outer', 'inline_helper'])
                    self.assertEqual(data['sources']['callers'][caller][-1][1:],
                                     ['kernel/fake.c', 2, 'https://kernel.invalid/src/kernel/fake.c#L2'])
                    pool = helpers.json_script(html, 'fgSrc')
                    lines = helpers.json_script(html, 'fgSrcLines')
                    excerpt = pool[lines[str(cols['line'][index])]]
                    self.assertIn('inline_helper', excerpt)
                    self.assertNotIn('kernel function entry', excerpt)
                    self.assertIn('https://kernel.invalid/src/kernel/fake.c#L2', excerpt)
                    if baseline is None:
                        baseline = pool, lines
                    else:
                        self.assertEqual((pool, lines), baseline)

    def test_symbols_without_debug_image_preserve_model_without_inventing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / 'capture.jsonl'
            trace.write_text(stream(records_with_caller()), encoding='utf-8')
            result = invoke('--timeline', '--output', root / 'report.html', trace)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = helpers.json_script((root / 'report.html').read_text(encoding='utf-8'), 'tlData')
            self.assertTrue(data['uftrace']['caller_symbols'])
            self.assertTrue(all(not chain for chain in data.get('sources', {}).get('callers', {}).values()))
            self.assertFalse(data.get('inline', {}))
            self.assertEqual(invariant_errors(data), [])


if __name__ == '__main__':
    unittest.main()
