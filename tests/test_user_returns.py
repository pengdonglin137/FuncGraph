"""User API failure hints retain returns without borrowing kernel errno semantics."""

import copy
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urljoin

import helpers
from fglib import btf, export, inputs, timeline
from fglib.constants import ERROR_CODE_MAP
from fglib.textindex import FIELDS, PRESENTATION_FIELDS, TextIndexWriter
from fglib.user_returns import DIRECT_ERRNO_APIS, MINUS_ONE_APIS, NULL_APIS, classify_user_return
from fglib.viewer import create_server
from test_btf import make_elf
from test_uftrace_export import AVAILABLE, COMPILE_FLAGS, EXECUTABLE
from test_uftrace_import import call, stream
from test_uftrace_text_returns import ReturnMarkup, return_types


CASES = [('open', '-1'), ('fopen', '0x0'), ('pthread_create', '22'), ('posix_memalign', '9999'),
         ('read', '0'), ('write', '7'), ('close', '0'), ('malloc', '0'), ('strcmp', '-1'),
         ('application_work', '-22')]


def recording(groups=0, repeats=0):
    records = [call('entry', 'user', 0, 0, 'main', 1, comm='user-errors')]
    cases = [('pthread_create', str(number)) for number in range(1, groups + 1)] if groups else CASES
    if repeats:
        cases = cases + [('pthread_create', '1')] * repeats
    for position, (name, value) in enumerate(cases):
        start, address = 100 + position * 1000, 100 + position
        records.extend([call('entry', 'user', start, 1, name, address),
                        call('exit', 'user', start + 900, 1, name, address, user_retval=value,
                             payload_hex='aabbccdd')])
    finish = 2000 + len(cases) * 1000
    if not groups:
        records.extend([call('entry', 'kernel', 200, 0, 'signed32', 999),
                        call('exit', 'kernel', 800, 0, 'signed32', 999, retval='0xffffffffffffffea'),
                        call('entry', 'user', finish - 900, 1, 'open', 500),
                        call('exit', 'user', finish - 100, 1, 'open', 500, payload_hex='ffffffff')])
    records.append(call('exit', 'user', finish, 0, 'main', 1, user_retval='1'))
    return sorted(records, key=lambda record: record['time_ns'])


def build(records=None, typed=True, **options):
    database = None
    if typed:
        database = btf.ReturnTypeDB()
        database.add(btf.BtfInfo(return_types(), word_size=8))
    return inputs.UftraceInput(io.StringIO(stream(recording() if records is None else records))).build(
        rettypes=database, errno_map=ERROR_CODE_MAP, enable_fold=True, **options)


class UserReturnConventionTest(unittest.TestCase):
    def classify(self, name, value):
        return classify_user_return(name, value, ERROR_CODE_MAP)

    def test_minus_one_rules_never_turn_minus_one_into_eperm(self):
        for name in MINUS_ONE_APIS:
            with self.subTest(name=name):
                failure = self.classify(name, '-1')
                self.assertEqual(failure['rule'], 'minus_one')
                self.assertNotIn('errno', failure)
                self.assertNotIn('EPERM', failure['summary'])
                self.assertIn('errno not recorded', failure['summary'])
                for value in ('-2', '0', '1', '100', '0xffffffff', '0xffffffffffffffff'):
                    self.assertIsNone(self.classify(name, value))

    def test_null_rules_and_nonnull_results(self):
        for name in NULL_APIS:
            with self.subTest(name=name):
                for value in ('0', '0x0', '000'):
                    failure = self.classify(name, value)
                    self.assertEqual(failure['rule'], 'null')
                    self.assertNotIn('errno', failure)
                    self.assertIn('NULL', failure['summary'])
                for value in ('1', '0x1234', '-1'):
                    self.assertIsNone(self.classify(name, value))

    def test_direct_errors_preserve_positive_numbers_and_optional_names(self):
        for name in DIRECT_ERRNO_APIS:
            with self.subTest(name=name):
                for value in ('22', '0x16'):
                    failure = self.classify(name, value)
                    self.assertEqual(failure['errno'], 22)
                    self.assertIn('EINVAL (22)', failure['summary'])
                    self.assertIn('not a captured errno', failure['note'])
                for value in ('0', '-1', '-22', '2147483648', '0xffffffffffffffff'):
                    self.assertIsNone(self.classify(name, value))
        failure = self.classify('pthread_create', '9999')
        self.assertEqual(failure['summary'], 'API error · 9999')
        self.assertEqual(classify_user_return('pthread_create', '22', {})['summary'], 'API error · 22')

    def test_comparisons_allocators_conditionals_and_unknown_apis_stay_neutral(self):
        for name in ('strcmp', 'memcmp', 'strcasecmp', 'application_work', 'main', 'atoi',
                     'malloc', 'calloc', 'realloc', 'readdir', 'getpriority', 'pthread_mutex_lock', 'custom_open'):
            for value in ('-1', '-22', '0', '22'):
                with self.subTest(name=name, value=value):
                    self.assertIsNone(self.classify(name, value))

    def test_only_explicit_glibc_and_plt_symbol_versions_are_normalized(self):
        for name in ('open', 'open@plt', 'open@GLIBC_2.2.5', 'open@@GLIBC_2.34'):
            self.assertIsNotNone(self.classify(name, '-1'))
        for name in ('Open', '__custom_open', 'open.isra.0', 'open@CUSTOM_1', 'open@@plt', 'open@GLIBC_',
                     'std::open', 'open(int)', 'open<script>', 'open\n', '', None):
            with self.subTest(name=name):
                self.assertIsNone(self.classify(name, '-1'))

    def test_invalid_and_opaque_values_are_not_decoded(self):
        for value in (None, -1, True, 'ffffffff', 'raw bytes: ffffffff', 'NULL', 'true', '+1',
                      '1.5', ' 0', '0\n', '1' * 100):
            self.assertIsNone(self.classify('fopen', value))


class UserReturnModelTest(unittest.TestCase):
    def test_hints_are_separate_from_kernel_errors_and_do_not_infer_propagation(self):
        builder = build()
        data = builder.to_data()
        failures = {builder.strings[builder.s_name[index]]: detail['failure']
                    for index, line in enumerate(builder.s_line)
                    if (detail := builder.user_details.get(line, {})).get('failure')}
        self.assertEqual(set(failures), {'open', 'fopen', 'pthread_create', 'posix_memalign'})
        self.assertEqual([row[1] for row in builder.err_rows], [-22])
        self.assertEqual(len(builder.chains), 1)
        self.assertEqual(builder.chains[0][4], 1)
        self.assertEqual(len(data['errs']), 1)
        self.assertEqual(len(data['chains']), 1)
        self.assertTrue(all(row.get('ret_errno') is None for row in builder.parsed if row.get('space') == 'user'))
        self.assertEqual(sum(row.get('ret_failure') is not None for row in builder.parsed), 4)

    def test_original_records_and_payloads_are_unchanged(self):
        original = recording()
        copied = copy.deepcopy(original)
        builder = build(original)
        self.assertEqual(original, copied)
        self.assertEqual(builder.records, copied)
        for index, line in enumerate(builder.s_line):
            detail = builder.user_details.get(line)
            if detail is None or 'retval' not in detail:
                continue
            record = builder.records[builder.s_eline[index] - 3]
            self.assertEqual(detail['retval'], record['user_retval'])
            if 'payload_hex' in record:
                self.assertEqual(detail['return_payload'], record['payload_hex'])
            self.assertIsNone(builder.s_ret[index])

    def test_user_only_without_btf_and_text_projection_reuse_hints(self):
        records = [record for record in recording() if record['space'] == 'user']
        builder = build(records, typed=False)
        with patch('fglib.uftrace.classify_user_return', side_effect=AssertionError('reclassification')):
            lines = builder._text_lines(True, True)
        self.assertEqual(sum(row.get('ret_failure') is not None for row in lines), 4)
        self.assertEqual(builder.to_data()['btf'], 0)
        self.assertEqual(builder.err_rows, [])

    def test_missing_returns_raw_bytes_and_truncated_entries_are_not_failures(self):
        for fields in ({}, {'payload_hex': 'ffffffff'}, {'retval': '0xffffffffffffffff'}):
            builder = build([call('entry', 'user', 0, 0, 'open', 1), call('exit', 'user', 1, 0, 'open', 1, **fields)])
            self.assertNotIn('failure', builder.user_details[3])
        builder = build([call('entry', 'user', 0, 0, 'open', 1)])
        self.assertTrue(builder.s_flags[0] & timeline.F_TRUNCATED)
        self.assertNotIn('failure', builder.user_details[3])

    def test_orphan_failure_and_entry_symbol_authority(self):
        builder = build([call('exit', 'user', 1, 0, 'open', 1, user_retval='-1')])
        self.assertTrue(builder.s_flags[0] & timeline.F_ORPHAN)
        self.assertEqual(builder.user_details[3]['failure']['rule'], 'minus_one')
        self.assertIsNotNone(builder.parsed[-1].get('ret_failure'))
        builder = build([call('entry', 'user', 0, 0, 'custom_open', 1),
                         call('exit', 'user', 1, 0, 'open', 1, user_retval='-1')])
        self.assertNotIn('failure', builder.user_details[3])

    def test_optional_version_six_index_preserves_old_fields_and_failures(self):
        output = io.BytesIO()
        writer = TextIndexWriter(output, include_user_errors=True)
        writer.append(3, {'space': 'user', 'fold-ret': '-1', 'user-error': 'API failure · errno not recorded'}, 'return -1')
        writer.finish('index.json')
        data = json.loads(output.getvalue())
        self.assertEqual(data['version'], 6)
        self.assertEqual(data['fields'], list(FIELDS + ('space',) + PRESENTATION_FIELDS + ('raw-func', 'user-error')))
        values = dict(zip(data['fields'], data['rows'][0][2:-1]))
        self.assertEqual(values['user-error'], 'API failure · errno not recorded')
        self.assertEqual(values['fold-ret'], '-1')
        self.assertIsNone(values['ret'])

    def test_multiple_threads_loss_and_overrun_do_not_transfer_failure_hints(self):
        records = [call('entry', 'user', 0, 0, 'open', 1, tid=10),
                   call('entry', 'user', 1, 0, 'open', 1, tid=20),
                   call('exit', 'user', 2, 0, 'open', 1, tid=20, user_retval='3'),
                   call('exit', 'user', 3, 0, 'open', 1, tid=10, user_retval='-1')]
        builder = build(records)
        self.assertEqual([builder.task_list[builder.s_task[index]].pid for index, line in enumerate(builder.s_line)
                          if builder.user_details[line].get('failure')], [10])
        for interruption in (call('lost', 'user', 1, 0, 'lost', 1, count=1),
                             call('exit', 'user', 1, 0, 'open', 1, user_retval='-1', overrun=1)):
            records = [call('entry', 'user', 0, 0, 'open', 1), interruption]
            builder = build(records)
            self.assertNotIn('failure', builder.user_details[3])
            self.assertTrue(builder.s_flags[0] & timeline.F_TRUNCATED)
            if interruption['type'] == 'exit':
                self.assertEqual(sum('failure' in detail for detail in builder.user_details.values()), 1)

    def test_json_export_preserves_separate_hints_before_and_after_serialization(self):
        data = build().to_data()
        for model in (data, json.loads(json.dumps(data))):
            result = export.build(model, ERROR_CODE_MAP)
            self.assertEqual(len(result['user_api_failures']), 4)
            self.assertEqual([row['errno'] for row in result['errors']], [-22])
            failed_open = next(row for row in result['user_api_failures'] if row['function'] == 'open')
            self.assertEqual(failed_open['recorded_return'], '-1')
            self.assertNotIn('errno', failed_open)
            self.assertNotIn('fate', failed_open)


class UserReturnReportTest(unittest.TestCase):
    def test_reports_keep_errno_separate_with_or_without_timeline_and_folding(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        Path(kernel.vmlinux).write_bytes(make_elf({'.BTF': return_types()}))
        trace = Path(kernel.dir, 'returns.jsonl')
        trace.write_text(stream(recording()))
        for options in ([], ['--timeline'], ['--enable-fold'], ['--timeline', '--enable-fold']):
            with self.subTest(options=options):
                content, _ = kernel.run(str(trace), '--no-config', '--filter', *options)
                markup = ReturnMarkup(content)
                failures = [row for row in markup.rows if row.get('data-user-error')]
                self.assertEqual(len(failures), 4)
                self.assertTrue(all(row.get('data-space') == 'user' and 'data-ret' not in row for row in failures))
                self.assertEqual(markup.options, ['all', '-22'])
                self.assertIn('errno was not recorded', content)


class UserReturnRealCaptureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not AVAILABLE:
            if os.environ.get('FG_UFTRACE_REQUIRED'):
                raise AssertionError('patched uftrace and gcc are required')
            raise unittest.SkipTest('optional patched uftrace build not available')
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        cls.root = Path(cls.kernel.dir)
        cls.environment = dict(os.environ, GMON_OUT_PREFIX=str(cls.root / 'gmon'))
        cls.binary, cls.capture = cls.root / 'api_failures', cls.root / 'capture.data'
        subprocess.run(['gcc', *COMPILE_FLAGS, '-O0', '-g', '-fno-builtin',
                        str(Path(helpers.ROOT, 'samples/demo_user_errors.c')), '-o', str(cls.binary)],
                       check=True, capture_output=True, env=cls.environment)
        command = [str(Path(helpers.ROOT, 'fgcapture')), '--user', '--no-sched', '--uftrace-bin', str(EXECUTABLE),
                   '-o', str(cls.capture)]
        for specification in ('open@retval/i32', 'fopen@retval/x64', 'read@retval/i64',
                              'posix_memalign@retval/i32', 'strcmp@retval/i32', 'application_result@retval/i32'):
            command += ['-R', specification]
        command += ['--', str(cls.binary)]
        subprocess.run(command, check=True, capture_output=True, env=cls.environment)

    def test_real_capture_keeps_failure_conventions_and_normal_negative_values_separate(self):
        source = inputs.read_uftrace_directory(self.capture, EXECUTABLE)
        original = copy.deepcopy(source.records)
        builder = source.build(enable_fold=True, errno_map=ERROR_CODE_MAP)
        self.assertEqual(builder.records, original)
        failures = {builder.strings[builder.s_name[index]]: detail['failure']
                    for index, line in enumerate(builder.s_line)
                    if (detail := builder.user_details.get(line, {})).get('failure')}
        self.assertEqual(set(failures), {'open', 'fopen', 'read', 'posix_memalign'})
        self.assertEqual(failures['posix_memalign']['errno'], 22)
        self.assertTrue(all('errno' not in failures[name] for name in ('open', 'fopen', 'read')))
        self.assertEqual(builder.err_rows, [])
        self.assertEqual(builder.chains, [])
        for index, line in enumerate(builder.s_line):
            name = builder.strings[builder.s_name[index]]
            if name in ('strcmp', 'application_result'):
                self.assertLess(int(builder.user_details[line]['retval']), 0)
                self.assertNotIn('failure', builder.user_details[line])

    def test_real_recording_directory_renders_without_vmlinux_and_exports_all_hints(self):
        output, exported = self.root / 'real.html', self.root / 'analysis.json'
        subprocess.run([str(Path(helpers.ROOT, 'funcgraph.py')), '--no-config', '--uftrace', str(self.capture),
                        '--uftrace-bin', str(EXECUTABLE), '--timeline', '--enable-fold', '--filter',
                        '--user-src', helpers.ROOT, '--output', str(output), '--export', str(exported)],
                       check=True, capture_output=True, env=self.environment)
        markup = ReturnMarkup(output.read_text())
        self.assertEqual(sum('data-user-error' in row for row in markup.rows), 4)
        result = json.loads(exported.read_text())
        self.assertEqual(len(result['user_api_failures']), 4)
        self.assertEqual(result['errors'], [])


class UserReturnBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest(str(error))
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        Path(cls.kernel.vmlinux).write_bytes(make_elf({'.BTF': return_types()}))
        trace = Path(cls.kernel.dir, 'returns.jsonl')
        for mode in ('single', 'sidecar'):
            records = recording()
            if mode == 'sidecar':
                records.pop()
                for position in range(2200):
                    start, address = 20000 + position * 1000, 10000 + position
                    records.extend([call('entry', 'user', start, 1, 'padding', address),
                                    call('exit', 'user', start + 900, 1, 'padding', address, user_retval='0')])
                records.append(call('exit', 'user', 2220000, 0, 'main', 1, user_retval='1'))
            trace.write_text(stream(records))
            cls.kernel.run(str(trace), '--no-config', '--filter', '--enable-fold', '--timeline', '--report-mode', mode,
                           out='sidecar' if mode == 'sidecar' else 'index.html')
        cls.kernel.run(str(trace), '--no-config', '--timeline', '--no-text-view', out='timeline-only.html')
        trace.write_text(stream(recording(215, repeats=75)))
        cls.kernel.run(str(trace), '--no-config', '--filter', '--enable-fold', '--timeline', out='many.html')
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

    def open_report(self, filename='index.html', width=1400):
        context = self.browser.new_context(viewport=dict(width=width, height=900))
        self.addCleanup(context.close)
        context.add_init_script('localStorage.clear()')
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, filename), wait_until='load')
        page.evaluate("async () => { await fgSwitchView('text'); }")
        if '?text=windowed' in filename:
            page.wait_for_function('() => fgTextWindow && !document.getElementById("content").hasAttribute("aria-busy")')
            self.assertEqual(page.evaluate('fgTextWindow.model.version'), 6)
        return page

    def apply(self, page, expression):
        page.evaluate('async expression => { document.getElementById("filterInput").value = expression; await applyFilter(); }', expression)
        self.assertIsNone(page.evaluate('fgFilterProgress'))
        self.assertEqual(page.evaluate('fgFilterState.text'), expression)

    def test_folding_and_shared_filter_in_all_delivery_modes(self):
        for filename in ('index.html', 'sidecar/index.html', 'sidecar/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                for name, label in [('open', 'errno not recorded'), ('fopen', 'NULL'), ('pthread_create', 'EINVAL (22)'),
                                    ('posix_memalign', '9999'), ('strcmp', None), ('malloc', None), ('main', None)]:
                    row = page.locator(f'[data-space=user][data-func="{name}"][data-fold-type=entry]').first
                    row.evaluate('row => _setFolded(row.getAttribute("data-fold-id"), true)')
                    summary = row.locator('.summary-ret')
                    self.assertEqual(summary.evaluate('node => node.classList.contains("summary-error")'), label is not None)
                    if label:
                        self.assertIn(label, summary.inner_text())
                    self.assertNotIn('EPERM', summary.inner_text())
                page.evaluate('unfoldAllBlocks()')
                self.apply(page, 'space:user AND result:failure')
                self.assertEqual(page.locator('#content [data-user-error]:visible').count(), 4)
                self.assertEqual(page.locator('#content [data-ret]:visible').count(), 0)
                page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.assertEqual(page.evaluate('() => fgView("timeline").probe().TRACKS.reduce((count, track) => count + track.hits, 0)'), 4)
                page.evaluate("async () => { await fgSwitchView('text'); }")
                self.apply(page, 'result:failure')
                self.assertEqual(page.locator('#content [data-user-error]:visible').count(), 4)
                self.assertEqual(page.locator('#content [data-ret]:visible').count(), 1)
                self.apply(page, 'ret:all')
                self.assertEqual(page.locator('#content [data-user-error]:visible').count(), 0)
                self.assertEqual(page.locator('#content [data-ret]:visible').count(), 1)
                for expression in ('result:success', 'result:>failure', 'result:="failure', 'result:fail'):
                    self.apply(page, expression)
                    self.assertEqual(page.evaluate('fgFilterState.lines.size'), 0)

    def test_details_tooltips_and_error_overlay_do_not_claim_user_errno(self):
        page = self.open_report()
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        for name, expected in [('open', 'errno not recorded'), ('fopen', 'NULL'), ('pthread_create', 'EINVAL (22)')]:
            index = page.evaluate('name => { const probe=fgView("timeline").probe(); return [...probe.sName].findIndex((value,index)=>probe.nameOf(index)===name); }', name)
            page.evaluate('index => fgView("timeline").probe().select(index)', index)
            self.assertIn('API failure hint', page.locator('.tl-slice-head').inner_text())
            self.assertIn(expected, page.locator('.tl-call-facts').inner_text())
            self.assertIn('not verified', page.locator('.tl-call-facts').inner_text())
            self.assertEqual(page.locator('[data-context-kind="error"]').count(), 0)
            point = page.evaluate('index => { const probe=fgView("timeline").probe(); probe.zoomTo(probe.sStart[index],probe.sEnd[index],index); return probe.xyOf(index); }', index)
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            page.mouse.move(5, 5)
            page.mouse.move(*point)
            page.wait_for_function('text => document.querySelector(".tl-tip")?.innerText.includes(text)', arg=expected)
            self.assertNotIn('EPERM', page.locator('.tl-tip').inner_text())
        styles = page.evaluate('''() => { const probe=fgView("timeline").probe();
            return ["open", "strcmp"].map(name=>probe.styleOf([...probe.sName].findIndex((value,index)=>probe.nameOf(index)===name))); }''')
        self.assertNotEqual(styles[0], styles[1])

    def test_errors_summary_csv_navigation_and_filter_scope(self):
        page = self.open_report('sidecar/index.html?text=windowed')
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().errors(); }")
        self.assertEqual(page.locator('tr[data-user-error-group]').count(), 4)
        self.assertEqual(page.locator('tr[data-chains]').count(), 1)
        with page.expect_download() as download:
            page.locator('[data-act=user-errors-csv]').click()
        rows = list(csv.DictReader(io.StringIO(Path(download.value.path()).read_text(encoding='utf-8-sig'))))
        self.assertEqual(len(rows), 4)
        expected = {name: "'" + value if value.startswith('-') else value for name, value in CASES[:4]}
        self.assertEqual({row['function']: row['recorded_return'] for row in rows}, expected)
        self.assertEqual({row['direct_error_number'] for row in rows if row['function'] == 'open'}, {''})
        self.assertEqual({row['direct_error_number'] for row in rows if row['function'] == 'pthread_create'}, {'22'})
        page.locator('tr[data-user-error-group]').first.locator('[data-error-step="1"]').click()
        self.assertEqual(page.locator('tr[data-user-error-group]').first.locator('[data-error-position]').inner_text(), '1 / 1')
        self.assertTrue(page.locator('tr[data-chains]').is_visible())
        self.apply(page, 'space:user AND result:failure')
        page.evaluate('() => fgView("timeline").probe().errors()')
        self.assertEqual(page.locator('tr[data-user-error-group]').count(), 4)
        self.assertEqual(page.locator('tr[data-chains]').count(), 0)
        page.evaluate('async () => { await fgSwitchView("text"); await fgTextWindow.settled; }')
        self.assertEqual(page.evaluate('fgTextWindow.visibleNumbers.length'), 4)
        self.assertEqual(page.locator('#content [data-user-error]:visible').count(), 4)

    def test_background_window_filter_sort_and_clear_rebuild_on_return(self):
        page = self.open_report('sidecar/index.html?text=windowed')
        original = page.evaluate('fgTextWindow.visibleNumbers.length')
        mounted = page.locator('#content .line-container').count()
        page.evaluate('async () => { await fgSwitchView("timeline"); }')
        self.apply(page, 'result:failure sort:desc')
        self.assertEqual(page.locator('#content .line-container').count(), mounted)
        self.assertEqual(page.evaluate('fgTextSort'), 'desc')
        page.evaluate('async () => { await fgSwitchView("text"); await fgTextWindow.settled; }')
        self.assertEqual(page.evaluate('fgTextWindow.visibleNumbers.length'), 5)
        self.assertEqual(page.locator('#content [data-user-error]:visible').count(), 4)
        self.assertEqual(page.locator('#content [data-ret]:visible').count(), 1)
        page.evaluate('async () => { await fgSwitchView("timeline"); await clearFilter(); }')
        self.assertEqual(page.evaluate('fgFilterState.text'), '')
        self.assertIsNone(page.evaluate('fgFilterState.lines'))
        self.assertEqual(page.evaluate('fgTextSort'), '')
        page.evaluate('async () => { await fgSwitchView("text"); await fgTextWindow.settled; }')
        self.assertEqual(page.evaluate('fgTextWindow.visibleNumbers.length'), original)
        self.assertLessEqual(page.locator('#content .line-container').count(), page.evaluate('fgTextWindow.mountedLimit'))

    def test_error_shortcut_includes_hints_only_in_all_scope(self):
        page = self.open_report()
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.assertIn('all', page.locator('.err-label').inner_text())
        selected = []
        for position in range(5):
            page.keyboard.press('e')
            selected.append(page.evaluate('() => { const probe=fgView("timeline").probe(); return probe.nameOf(probe.sel()); }'))
        self.assertEqual(set(selected), {'open', 'signed32', 'fopen', 'pthread_create', 'posix_memalign'})
        page.locator('[data-act=errmode]').evaluate('button => button.click()')
        self.assertIn('to user', page.locator('.err-label').inner_text())
        self.assertTrue(page.locator('[data-act=errnext]').is_disabled())

    def test_timeline_only_retains_failures_without_text_or_btf_dependency(self):
        page = self.open_report('timeline-only.html')
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().errors(); }")
        self.assertEqual(page.locator('tr[data-user-error-group]').count(), 4)
        self.assertEqual(page.locator('tr[data-chains]').count(), 1)

    def test_user_only_pages_and_complete_csv_include_unmounted_groups(self):
        page = self.open_report('many.html', width=390)
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().errors(); }")
        self.assertEqual(page.locator('tr[data-user-error-group]').count(), 200)
        self.assertNotIn('no BTF', page.locator('.an-error-head').inner_text())
        self.assertNotIn('error chains', page.locator('.an-error-head').inner_text())
        self.assertEqual(page.locator('[data-act=errors-csv]').count(), 0)
        self.assertNotIn('funcgraph-retval', page.locator('.tl-detail').inner_text())
        first = page.locator('tr[data-user-error-group]').first
        first.locator('[data-error-step="-1"]').click()
        self.assertEqual(first.locator('[data-error-position]').inner_text(), '76 / 76')
        page.locator('[data-act=user-errors-next]').click()
        self.assertEqual(page.locator('tr[data-user-error-group]').count(), 15)
        self.assertIn('201–215 of 215 user API groups', page.locator('[data-error-footer=user-errors] [role=status]').inner_text())
        with page.expect_download() as download:
            page.locator('[data-act=user-errors-csv]').click()
        rows = list(csv.DictReader(io.StringIO(Path(download.value.path()).read_text(encoding='utf-8-sig'))))
        self.assertEqual(len(rows), 290)
        self.assertEqual({int(row['recorded_return']) for row in rows}, set(range(1, 216)))
        self.assertEqual(sum(row['recorded_return'] == '1' for row in rows), 76)
        page.locator('[data-act=user-errors-prev]').press('Space')
        self.assertEqual(page.locator('tr[data-user-error-group]').count(), 200)
        self.assertEqual(page.locator('tr[data-user-error-group]').first.locator('[data-error-position]').inner_text(), '76 / 76')

    def test_user_only_tools_enable_overlay_navigation_and_summary(self):
        page = self.open_report('many.html')
        page.evaluate('async () => { await fgSwitchView("timeline"); }')
        page.locator('[data-act=options]').click()
        self.assertTrue(page.locator('.tl-errnav').is_visible())
        self.assertTrue(page.locator('[data-ov=err]').is_visible())
        self.assertFalse(page.locator('[data-act=errnext]').is_disabled())
        style = page.evaluate('fgView("timeline").probe().styleOf(1)')
        helpers.click_timeline_tool(page, '[data-ov=err]')
        self.assertNotEqual(page.evaluate('fgView("timeline").probe().styleOf(1)'), style)
        helpers.click_timeline_tool(page, '[data-ov=err]')
        self.assertEqual(page.evaluate('fgView("timeline").probe().styleOf(1)'), style)
        helpers.click_timeline_tool(page, '[data-act=errnext]')
        self.assertGreater(page.evaluate('fgView("timeline").probe().sel()'), 0)
        helpers.click_timeline_tool(page, '[data-act=errmode]')
        self.assertTrue(page.locator('[data-act=errnext]').is_disabled())
        page.keyboard.press('e')
        self.assertIn('Use all to include user API failure hints', page.locator('.tl-toast').inner_text())
        helpers.click_timeline_tool(page, '[data-act=errmode]')
        self.assertFalse(page.locator('[data-act=errnext]').is_disabled())
        helpers.click_timeline_tool(page, '[data-act=an-errors]')
        self.assertEqual(page.locator('tr[data-user-error-group]').count(), 200)
        self.assertEqual(page.locator('[data-act=errors-csv]').count(), 0)

    def test_windowed_paint_validation_rejects_changed_user_error_metadata(self):
        page = self.open_report('sidecar/index.html?text=windowed')
        message = page.evaluate('''async () => {
            const data=JSON.parse(JSON.stringify(await fgData.load('fgTextIndex')));
            const column=data.fields.indexOf('user-error')+2;
            const row=data.rows.find(row=>row[column]);
            row[column]='API error · invented';
            const steps=fgTextModel.prepare(data); let result;
            do {result=steps.next();} while(!result.done);
            return fgLoadTextBlock(result.value,row[0]).then(()=>null,error=>error.message);
        }''')
        self.assertEqual(message, 'Text paint block presentation metadata mismatch')


if __name__ == '__main__':
    unittest.main()
