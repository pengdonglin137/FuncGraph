"""Argument names and user call sites retain their original evidence and scope."""

import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import struct

import helpers
from fglib import btf, inputs, uftrace
from fglib.viewer import create_server
from test_btf import BtfBuilder, make_elf
from test_timeline import invariant_errors
from test_uftrace_import import ORIGIN, build, call, capture, invoke, stream


def arguments(database, name, fields, returned=None):
    scalar = database.int_('int', 4, signed=True)
    types = {'int': scalar, 'ptr': database.add(None, btf.KIND_PTR, 0, scalar),
             'wide': database.int_('wide', 8), 'float': database.add('float', btf.KIND_FLOAT, 0, 4),
             'struct': database.add('aggregate', btf.KIND_STRUCT, 0, 16), 'varargs': 0}
    payload = b''.join(struct.pack('<II', database.s(field) if field else 0, types[kind])
                       for field, kind in fields)
    proto = database.add(None, btf.KIND_FUNC_PROTO, len(fields), types[returned] if returned else scalar, payload)
    database.add(name, btf.KIND_FUNC, 0, proto)


def named_database(fields=(('fd', 'int'), ('buffer', 'ptr'), ('length', 'wide')), word_size=8):
    builder = BtfBuilder()
    arguments(builder, 'inner', fields)
    database = btf.ReturnTypeDB()
    database.add(btf.BtfInfo(builder.blob(), word_size=word_size))
    return database


def callsite(timestamp=199, tid=42, address='0x1234', source=None):
    site = {'retaddr': address, 'name': 'main'}
    if source is not None:
        site['source'] = source
    return dict(type='event', space='user', time_ns=ORIGIN + timestamp, tid=tid, cpu=None,
                name='callsite', payload_hex='3412000000000000', user_callsite=site)


def recorded_calls():
    records = capture()
    records[0]['source'] = {'file': 'demo.c', 'line': 2}
    records[1]['source'] = {'file': 'demo.c', 'line': 9}
    records.insert(1, callsite(source={'file': 'demo.c', 'line': 5}))
    return records


class KernelArgumentNamesTest(unittest.TestCase):
    def test_named_arguments_keep_raw_model_values_and_unused_slots(self):
        records = capture()
        records[5]['args'] = ['0x3', '0xffff1234', '0x200', '0x55']
        builder = inputs.UftraceInput(io.StringIO(stream(records))).build(
            enable_fold=True, rettypes=named_database())
        data = builder.to_data()
        index = next(index for index, name in enumerate(data['cols']['name']) if data['strings'][name] == 'inner')
        line = data['cols']['line'][index]
        named = 'fd=0x3, buffer=0xffff1234, length=0x200, slot4=0x55'
        self.assertEqual(data['uftrace']['kernel_arg_names'][data['cols']['name'][index]], ('fd', 'buffer', 'length'))
        self.assertNotIn(line, data['uftrace']['kernel_details'])
        self.assertEqual(data['strings'][data['cols']['args'][index]], '0x3, 0xffff1234, 0x200, 0x55')
        self.assertEqual(builder.parsed[line - 1].params, named)
        self.assertIn(named, builder.parsed[line - 1].raw_line)
        self.assertEqual(invariant_errors(data), [])

    def test_unknown_abi_aggregate_float_varargs_and_clone_do_not_shift_names(self):
        for fields, word_size, expected in [
                ((('fd', 'int'), ('length', 'wide'), ('flags', 'int')), None, ('fd',)),
                ((('fd', 'int'), ('length', 'wide'), ('flags', 'int')), 4, ('fd',)),
                ((('fd', 'int'), ('value', 'struct'), ('flags', 'int')), 8, ('fd',)),
                ((('fd', 'int'), ('value', 'float'), ('flags', 'int')), 8, ('fd',)),
                ((('fd', 'int'), ('', 'varargs')), 8, ('fd',)),
                ((('', 'int'), ('buffer', 'ptr')), None, ('', 'buffer'))]:
            with self.subTest(fields=fields, word_size=word_size):
                database = named_database(fields, word_size)
                self.assertEqual(database.argument_names('inner', 6), expected)
                self.assertEqual(database.argument_names('inner.isra.0', 6), ())
                self.assertEqual(database.argument_names('inner.constprop.0', 6), ())
                self.assertEqual(database.argument_names('inner.part.0', 6), ())
                self.assertEqual(database.argument_names('inner.llvm.123', 6), ())
                self.assertEqual(database.argument_names('missing', 6), ())

    def test_aggregate_return_and_conflicting_module_prototypes_remain_unknown(self):
        builder = BtfBuilder()
        arguments(builder, 'aggregate', [('fd', 'int')], 'struct')
        database = btf.ReturnTypeDB()
        database.add(btf.BtfInfo(builder.blob(), word_size=8))
        self.assertEqual(database.argument_names('aggregate', 1), ())
        first, second = BtfBuilder(), BtfBuilder()
        arguments(first, 'shared', [('fd', 'int')])
        arguments(second, 'shared', [('size', 'int')])
        for blob in (first, second, first):
            database.add(btf.BtfInfo(blob.blob(), word_size=8))
        self.assertEqual(database.argument_names('shared', 1), ())
        duplicate = BtfBuilder()
        for field in ('fd', 'size', 'fd'):
            arguments(duplicate, 'duplicate_static', [(field, 'int')])
        database.add(btf.BtfInfo(duplicate.blob(), word_size=8))
        self.assertEqual(database.argument_names('duplicate_static', 1), ())

    def test_elf_word_size_split_btf_and_absent_btf(self):
        with tempfile.TemporaryDirectory() as directory:
            base = BtfBuilder()
            scalar = base.int_('int', 4, signed=True)
            base_info = btf.BtfInfo(base.blob(), word_size=8)
            split = BtfBuilder(base_info)
            proto = split.add(None, btf.KIND_FUNC_PROTO, 1, scalar,
                              struct.pack('<II', split.s('module_arg'), scalar))
            split.add('module_fn', btf.KIND_FUNC, 0, proto)
            info = btf.BtfInfo(split.blob(), base=base_info)
            database = btf.ReturnTypeDB()
            database.add(info)
            self.assertEqual(database.argument_names('module_fn', 1), ('module_arg',))
            image = Path(directory) / 'vmlinux'
            image.write_bytes(make_elf({'.BTF': base.blob()}))
            self.assertEqual(btf.load_btf(image).word_size, 8)
        self.assertNotIn('kernel_arg_names', build(capture()).to_data()['uftrace'])


class UserCallSiteTest(unittest.TestCase):
    def test_real_site_is_separate_from_entry_and_paired_only_with_its_thread(self):
        builder = build(recorded_calls())
        data = builder.to_data()
        index = 1
        line = data['cols']['line'][index]
        sources = data['sources']
        self.assertEqual(sources['user_positions'][sources['user_lines'][line]][:2], ('demo.c', 9))
        self.assertEqual(sources['user_positions'][sources['user_callsites'][line]][:2], ('demo.c', 5))
        self.assertEqual(data['uftrace']['user_details'][line]['address'], '0x2')
        self.assertEqual(data['uftrace']['user_details'][line]['callsite'], {'retaddr': '0x1234', 'name': 'main'})
        self.assertEqual(builder.parsed[line - 1].source, ('demo.c', 5))
        self.assertIn('user call site', builder.parsed[line - 1].source_kind)
        self.assertEqual(data['cols']['caller'][index], -1)
        self.assertEqual(invariant_errors(data), [])

    def test_old_records_remain_entries_and_never_infer_sites_from_callee_addresses(self):
        builder = build(capture())
        self.assertNotIn('user_callsites', builder.to_data()['sources'])
        self.assertEqual(builder.parsed[2].source_kind, 'user function entry')

    def test_unresolved_site_keeps_original_pc_and_entry_fallback(self):
        records = recorded_calls()
        del records[1]['user_callsite']['source']
        builder = build(records)
        data = builder.to_data()
        line = data['cols']['line'][1]
        self.assertEqual(data['uftrace']['user_details'][line]['callsite']['retaddr'], '0x1234')
        self.assertNotIn('user_callsites', data['sources'])
        self.assertEqual(builder.parsed[line - 1].source, ('demo.c', 9))

    def test_interleaved_threads_keep_independent_sites(self):
        records = [call('entry', 'user', 100, 0, 'main', 1),
                   callsite(timestamp=198, tid=43, address='0x4567', source={'file': 'thread.c', 'line': 7}),
                   callsite(timestamp=199, source={'file': 'demo.c', 'line': 5}),
                   call('entry', 'user', 199, 0, 'worker', 2, tid=43),
                   call('entry', 'user', 200, 1, 'child', 3),
                   call('exit', 'user', 300, 0, 'worker', 2, tid=43),
                   call('exit', 'user', 400, 1, 'child', 3),
                   call('exit', 'user', 500, 0, 'main', 1)]
        data = build(records).to_data()
        details = {detail['address']: detail for detail in data['uftrace']['user_details'].values()}
        self.assertEqual(details['0x2']['callsite']['retaddr'], '0x4567')
        self.assertEqual(details['0x3']['callsite']['retaddr'], '0x1234')
        self.assertEqual(invariant_errors(data), [])

    def test_wrong_time_thread_intervening_record_or_loss_never_reuses_site(self):
        for site, interruption in [
                (callsite(timestamp=198), []), (callsite(tid=43), []),
                (callsite(), [dict(type='lost', space='user', time_ns=ORIGIN + 199, tid=42, cpu=None, count=1)]),
                (callsite(), [dict(type='event', space='user', time_ns=ORIGIN + 199, tid=42, cpu=None, name='other')])]:
            with self.subTest(site=site, interruption=interruption):
                records = capture()
                records[1:1] = [site, *interruption]
                data = build(records).to_data()
                self.assertTrue(all('callsite' not in detail for detail in data['uftrace']['user_details'].values()))

    def test_callsite_validation_rejects_wrong_scope_and_malformed_positions(self):
        for change in [{'user_callsite': None}, {'name': 'other'}, {'space': 'kernel', 'cpu': 0},
                       {'user_callsite': {'retaddr': '0x0'}},
                       {'user_callsite': {'retaddr': '0x1', 'name': True}},
                       {'space': None, 'origin': 'perf'},
                       {'user_callsite': {'retaddr': '0x1', 'source': {'file': 'x.c', 'line': True}}}]:
            record = callsite()
            record.update(change)
            with self.subTest(change=change), self.assertRaises(uftrace.UftraceInputError):
                uftrace.read_export(io.StringIO(stream([record])))

    def test_source_delivery_single_sidecar_and_timeline_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'demo.c').write_text('/* user source */\n' * 12)
            trace = root / 'trace.jsonl'
            trace.write_text(stream(recorded_calls()))
            for mode in ('single', 'sidecar'):
                for timeline_only in (False, True):
                    with self.subTest(mode=mode, timeline_only=timeline_only):
                        output = root / ('report.html' if mode == 'single' else 'report')
                        extra = ['--no-text-view', '--keep-source'] if timeline_only else []
                        result = invoke(trace, '--timeline', '--enable-fold', '--user-src', root,
                                        '--base-url', 'https://kernel.invalid', '--report-mode', mode,
                                        '--output', output, *extra)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        if mode == 'single':
                            html = output.read_text()
                            pool = helpers.json_script(html, 'fgSrc')
                        else:
                            descriptor = helpers.json_script((output / 'index.html').read_text(), 'fgDataManifest')
                            manifest_path = output / descriptor['manifest']['file']
                            manifest = json.loads(manifest_path.read_text())
                            pool = []
                            for name, resource in manifest['resources'].items():
                                if name.startswith('fgSrc'):
                                    pool.extend(json.loads((manifest_path.parent / resource['file']).read_text()))
                        block = next(block for block in pool if 'user call site' in block)
                        self.assertIn('demo.c:5', block)
                        self.assertNotIn('kernel.invalid', block)


class UftraceDetailBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest(str(error))
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def test_named_args_and_exact_user_sources_in_both_delivery_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'demo.c').write_text('/* user source */\n' * 12)
            records = recorded_calls()
            next(record for record in records if record.get('name') == 'inner')['args'] = ['0x3', '0xffff1234', '0x200']
            trace = root / 'trace.jsonl'
            trace.write_text(stream(records))
            builder = BtfBuilder()
            arguments(builder, 'inner', [('fd', 'int'), ('buffer', 'ptr'), ('length', 'wide')])
            image = root / 'debug.elf'
            image.write_bytes(make_elf({'.BTF': builder.blob()}))
            for mode in ('single', 'sidecar'):
                for no_text in (False, True):
                    with self.subTest(mode=mode, no_text=no_text):
                        output = root / f'{mode}-{no_text}'
                        extra = ['--no-text-view', '--keep-source'] if no_text else []
                        result = invoke(trace, '--timeline', '--enable-fold', '--filter', '--btf', image,
                                        '--user-src', root, '--report-mode', mode, '--output', output, *extra)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        with create_server(output) as server:
                            thread = threading.Thread(target=server.serve_forever, daemon=True)
                            thread.start()
                            page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
                            page.set_default_timeout(5000)
                            errors = []
                            page.on('pageerror', lambda error: errors.append(str(error)))
                            try:
                                page.goto(server.url)
                                page.evaluate('() => fgSwitchView("timeline")')
                                page.evaluate('() => fgView("timeline").probe().select(1)')
                                panel = page.locator('.tl-detail')
                                panel.locator('.tl-cs > summary', has_text='User call site source').wait_for()
                                self.assertIn('demo.c:5', panel.locator('.tl-cs-src').inner_text())
                                self.assertIn('user call site', panel.locator('.tl-cs-src').inner_text())
                                self.assertIn('0x1234', panel.locator('.k:has-text("caller return address") + .v').inner_text())
                                self.assertEqual(panel.locator('[data-callsite-position]').count(), 1)
                                stack = panel.locator('[data-context-list=stack]')
                                main_source = stack.locator('.tl-context-source', has_text='demo.c:5')
                                self.assertEqual(main_source.count(), 1)
                                self.assertEqual(main_source.locator('button').get_attribute('data-slice'), '1')
                                self.assertIn('demo.c:9', stack.text_content())
                                self.assertEqual(stack.locator('.tl-context-source', has_text='demo.c:9').locator('button').count(), 0)
                                self.assertEqual(panel.locator('a[href^="https://github.com/torvalds/linux"]').count(), 0)
                                page.evaluate('''() => {
                                    const model = fgView('timeline').probe();
                                    model.select(Array.from({length:model.N}, (_, index) => index)
                                        .find(index => model.nameOf(index) === 'inner'));
                                }''')
                                panel.get_by_text('args (BTF)', exact=True).wait_for()
                                self.assertIn('fd=0x3, buffer=0xffff1234, length=0x200', panel.inner_text())
                                self.assertEqual(panel.locator('details:has(summary:text-is("Raw argument slots"))').get_attribute('open'), None)
                                page.set_viewport_size({'width': 390, 'height': 844})
                                self.assertEqual(page.get_by_text('args (BTF)', exact=True).count(), 1)
                                page.evaluate('() => fgView("timeline").probe().select(0)')
                                panel.get_by_text('Not recorded · capture with -T', exact=False).wait_for()
                                self.assertIn('User function source (entry)', panel.locator('.tl-cs > summary').text_content())
                                self.assertIn('not call site', panel.locator('.tl-cs-src').inner_text())
                                self.assertEqual(errors, [])
                            finally:
                                page.close()
                                server.shutdown()
                                thread.join(timeout=5)

    def test_caller_position_survives_missing_source_files_and_untraced_wrappers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = recorded_calls()
            records[1]['user_callsite']['name'] = 'untraced_wrapper'
            trace = root / 'trace.jsonl'
            trace.write_text(stream(records))
            report = root / 'report.html'
            result = invoke(trace, '--timeline', '--output', report)
            self.assertEqual(result.returncode, 0, result.stderr)
            page = self.browser.new_page()
            page.set_default_timeout(5000)
            try:
                page.goto(report.as_uri())
                page.evaluate('() => fgSwitchView("timeline")')
                page.evaluate('() => fgView("timeline").probe().select(1)')
                panel = page.locator('.tl-detail')
                self.assertIn('demo.c:5', panel.locator('.k:has-text("caller return address") + .v').text_content())
                self.assertEqual(panel.locator('.tl-cs').count(), 0)
                stack = panel.locator('[data-context-list=stack]')
                self.assertIn('demo.c:2', stack.text_content())
                self.assertNotIn('demo.c:5', stack.text_content())
            finally:
                page.close()


if __name__ == '__main__':
    unittest.main()
