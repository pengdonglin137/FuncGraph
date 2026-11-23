"""BTF enum aliases and exact, independently inspectable function declarations."""

import io
from pathlib import Path
import struct
import threading
import unittest
from unittest.mock import patch

import helpers
from fglib import btf, inputs, timeline
from fglib.viewer import create_server
from test_btf import BtfBuilder, make_elf
from test_uftrace_import import call, stream


WIDE_VALUE = 2**63 + 37
LARGE_ALIAS_GROUP = [f'LONG_BTF_ALIAS_NAME_{index:02d}_FOR_NARROW_VIEWPORT' for index in range(33)]
COMPACT_FTRACE_ARGS = 'cpu=1, flags=2, mode=1, tag="a,b", r=0, n=1'
COMPACT_UFTRACE_SLOTS = ['0x1', '0x2', '0x1', '0x0', '0x0', '0x1']
FTRACE_ENUM64_ARGS = f'mode=(ENUM64)[{hex(WIDE_VALUE)}], label=0x0, ...'
FTRACE_PARTIAL_ARGS = f'mode=(ENUM64)[0x25:...], peer=(ENUM64)[{hex(WIDE_VALUE)}]'
FTRACE_LABELLED_ARGS = 'cpu=1, mode=0x1 [QUICK], flags=1 [QUICK], ...'
FTRACE_LONG_ENUM_ARGS = f'label="{"segment/" * 28}", mode={hex(WIDE_VALUE)}, peer={hex(WIDE_VALUE)}'


def enumeration(builder, name, size, fields, signed=False):
    entries = []
    for label, value in fields:
        if size == 8:
            entries.append(struct.pack('<III', builder.s(label), value & 0xffffffff, (value >> 32) & 0xffffffff))
        else:
            entries.append(struct.pack('<II', builder.s(label), value & 0xffffffff))
    return builder.add(name, btf.KIND_ENUM64 if size == 8 else btf.KIND_ENUM, len(fields), size,
                       b''.join(entries), signed=signed)


def function(builder, name, result, parameters):
    fields = b''.join(struct.pack('<II', builder.s(label) if label else 0, type_id)
                      for label, type_id in parameters)
    proto = builder.add(None, btf.KIND_FUNC_PROTO, len(parameters), result, fields)
    if name:
        builder.add(name, btf.KIND_FUNC, 0, proto)
    return proto


def definitions():
    builder = BtfBuilder()
    mode = enumeration(builder, 'tx_mode', 8, [('TX_FAST', WIDE_VALUE), ('TX_LOW_LATENCY', WIDE_VALUE)])
    status = enumeration(builder, 'tx_status', 4, [('TX_BUSY', -1)], signed=True)
    handle = builder.add('mode_t', btf.KIND_TYPEDEF, 0, mode)
    function(builder, 'submit', status, [('mode', handle)])
    function(builder, 'unused', 0, [('mode', handle)])
    peer = enumeration(builder, 'peer_mode', 8, [('PEER_FAST', WIDE_VALUE)])
    char = builder.int_('char', 1)
    const_char = builder.add(None, btf.KIND_CONST, 0, char)
    string = builder.add(None, btf.KIND_PTR, 0, const_char)
    function(builder, 'mixed', 0, [('label', string), ('mode', handle), ('peer', peer)])
    aliases = enumeration(builder, 'aliased_mode', 4, [(name, 1) for name in LARGE_ALIAS_GROUP])
    function(builder, 'many_aliases', status, [('mode', aliases)])
    count = builder.int_('int', 4, signed=True)
    speed = enumeration(builder, 'speed', 4, [('FAST', 1), ('QUICK', 1), ('SLOW', 2)])
    function(builder, 'compact', 0, [('cpu', count), ('flags', count), ('mode', speed), ('tag', string),
                                     ('r', count), ('n', count)])
    return builder


class BtfDetailTest(unittest.TestCase):
    def test_prototype_only_traces_do_not_decode_unused_enumerators(self):
        database = btf.ReturnTypeDB()
        database.add(btf.BtfInfo(definitions().blob(), word_size=8))
        lines = ['# tracer: function_graph\n',
                 '100.000001 |   0)  demo-1    |   1.000 us    |  submit();\n']
        with patch.object(btf.BtfInfo, 'enum_values', side_effect=AssertionError('unobserved enum')):
            data = timeline.build_timeline(lines, rettypes=database).to_data()
        detail = data['btf_detail']
        self.assertEqual(detail['enums'], [])
        self.assertEqual(list(detail['functions'].values()), [{'prototype': 'enum tx_status submit(mode_t mode);'}])

    def test_only_kernel_symbols_with_values_keep_enum_references(self):
        database = btf.ReturnTypeDB()
        database.add(btf.BtfInfo(definitions().blob(), word_size=8))
        records = [call('entry', 'kernel', 100, 0, 'submit', 1),
                   call('exit', 'kernel', 125, 0, 'submit', 1, retval='0xffffffff'),
                   call('entry', 'kernel', 150, 0, 'submit', 1, args=[hex(WIDE_VALUE)]),
                   call('exit', 'kernel', 175, 0, 'submit', 1),
                   call('entry', 'kernel', 200, 0, 'unused', 2),
                   call('exit', 'kernel', 225, 0, 'unused', 2),
                   call('entry', 'user', 250, 0, 'unused', 3, user_args=['1']),
                   call('exit', 'user', 275, 0, 'unused', 3)]
        report = inputs.UftraceInput(io.StringIO(stream(records))).build(rettypes=database).to_data()
        detail = report['btf_detail']
        functions = detail['functions']
        submit = functions[f"-1:{report['strings'].index('submit')}"]
        unused = functions[f"-1:{report['strings'].index('unused')}"]
        self.assertIn('args', submit)
        self.assertIn('ret', submit)
        self.assertEqual(unused, {'prototype': 'void unused(mode_t mode);'})
        self.assertEqual(len(detail['enums']), 2)

    def test_aliases_and_raw_values_are_distinct_in_uftrace(self):
        database = btf.ReturnTypeDB()
        database.add(btf.BtfInfo(definitions().blob(), word_size=8))
        records = [call('entry', 'kernel', 100, 0, 'submit', 1, args=[hex(WIDE_VALUE)]),
                   call('exit', 'kernel', 200, 0, 'submit', 1, retval='0xffffffff')]
        report = inputs.UftraceInput(io.StringIO(stream(records))).build(rettypes=database).to_data()
        detail = report['btf_detail']
        symbol = detail['functions'][f"-1:{report['cols']['name'][0]}"]
        self.assertEqual(len(detail['functions']), 1)
        self.assertEqual(symbol['prototype'], 'enum tx_status submit(mode_t mode);')
        self.assertEqual(detail['enums'][symbol['args'][0][1]]['values'], {str(WIDE_VALUE): ['TX_FAST', 'TX_LOW_LATENCY']})
        self.assertEqual(detail['enums'][symbol['ret']]['values'], {'-1': ['TX_BUSY']})
        self.assertEqual(report['strings'][report['cols']['ret'][0]], '0xffffffff')
        self.assertEqual(report['chains'], [])

    def test_split_objects_do_not_merge_same_type_id_or_function_name(self):
        builder = definitions()
        base = btf.BtfInfo(builder.blob(), source='vmlinux', word_size=8)
        database = btf.ReturnTypeDB()
        database.add(base)
        for module, label in [('alpha.ko', 'ALPHA'), ('beta.ko', 'BETA')]:
            split = BtfBuilder(base)
            member = enumeration(split, 'result', 4, [(label, 42)])
            function(split, 'same', member, [('base_arg', 1)])
            database.add(btf.BtfInfo(split.blob(), base=base, source=module))
        detail = database.detail_data([('unknown', 'same', None, True, True),
                                       ('alpha', 'same', 'alpha', True, True), ('beta', 'same', 'beta', True, True)])
        functions, enums = detail['functions'], detail['enums']
        self.assertNotIn('unknown', functions)
        self.assertEqual(enums[functions['alpha']['ret']]['values'], {'42': ['ALPHA']})
        self.assertEqual(enums[functions['beta']['ret']]['values'], {'42': ['BETA']})
        self.assertEqual(functions['alpha']['args'][0][1], functions['beta']['args'][0][1])

    def test_module_observations_keep_first_call_order_and_independent_values(self):
        base = btf.BtfInfo(definitions().blob(), source='vmlinux', word_size=8)
        database = btf.ReturnTypeDB()
        database.add(base)
        for module in ('alpha', 'beta'):
            split = BtfBuilder(base)
            member = enumeration(split, 'result', 4, [(module.upper(), 42)])
            function(split, 'same', member, [('mode', member)])
            database.add(btf.BtfInfo(split.blob(), base=base, source=module + '.ko'))
        lines = [
            '# tracer: function_graph\n',
            '100.000001 |   0)  demo-1    |   1.000 us    |  same [beta]();\n',
            '100.000003 |   0)  demo-1    |   1.000 us    |  same [alpha](); /* ret=42 */\n',
            '100.000005 |   0)  demo-1    |   1.000 us    |  same [beta](mode=42);\n',
            '100.000007 |   0)  demo-1    |   1.000 us    |  same [missing](mode=42); /* ret=42 */\n',
        ]
        report = timeline.build_timeline(lines, rettypes=database).to_data()
        detail = report['btf_detail']
        beta, alpha = detail['functions'].values()
        self.assertEqual([beta['source'], alpha['source']], ['beta.ko', 'alpha.ko'])
        self.assertNotIn('ret', beta)
        self.assertEqual(detail['enums'][beta['args'][0][1]]['values'], {'42': ['BETA']})
        self.assertNotIn('args', alpha)
        self.assertEqual(detail['enums'][alpha['ret']]['values'], {'42': ['ALPHA']})

    def test_prototype_owner_index_rejects_conflicts_even_with_module_name(self):
        first = BtfBuilder()
        function(first, 'shared', 0, [])
        function(first, 'local_conflict', 0, [])
        integer = first.int_('int', 4, signed=True)
        function(first, 'local_conflict', integer, [])
        database = btf.ReturnTypeDB()
        owner = btf.BtfInfo(first.blob(), source='first/virt-io.ko.xz', word_size=8)
        database.add(owner)
        self.assertIs(database.display_prototype('shared', 'virt_io')[0], owner)
        self.assertIsNone(database.display_prototype('shared', 'other'))
        self.assertIsNone(database.display_prototype('local_conflict', 'virt_io'))
        second = BtfBuilder()
        function(second, 'shared', 0, [])
        database.add(btf.BtfInfo(second.blob(), source='second/virt_io.ko', word_size=8))
        self.assertIsNone(database.display_prototype('shared', 'virt-io'))
        self.assertIsNone(database.display_prototype('shared', None))

    def test_declarator_keeps_qualifiers_function_pointers_and_varargs(self):
        builder = BtfBuilder()
        integer = builder.int_('int', 4, signed=True)
        char = builder.int_('char', 1)
        const_char = builder.add(None, btf.KIND_CONST, 0, char)
        string = builder.add(None, btf.KIND_PTR, 0, const_char)
        callback = builder.add(None, btf.KIND_PTR, 0, function(builder, None, integer, [('', string)]))
        proto = function(builder, 'dispatch', 0, [('callback', callback), ('', 0)])
        info = btf.BtfInfo(builder.blob(), word_size=8)
        self.assertEqual(info.prototype(proto, 'dispatch'), 'void dispatch(\n    int (*callback)(const char *),\n    ...\n);')

    def test_projection_reuses_parameter_lists_without_mixing_split_objects(self):
        base = btf.BtfInfo(definitions().blob(), word_size=8)
        database = btf.ReturnTypeDB()
        database.add(base)
        proto_ids = []
        for module in ('alpha', 'beta'):
            builder = BtfBuilder(base)
            member = enumeration(builder, module + '_type', 4, [(module.upper(), 1)])
            proto = function(builder, None, 0, [('value', member)])
            proto_ids.append(proto)
            for label in ('first', 'second'):
                builder.add(label, btf.KIND_FUNC, 0, proto)
            database.add(btf.BtfInfo(builder.blob(), source=module + '.ko', base=base))
        self.assertEqual(*proto_ids)
        symbols = [(module + ':' + label, label, module, True, False)
                   for module in ('alpha', 'beta') for label in ('first', 'second')]
        with patch.object(btf.BtfInfo, '_prototype_parts', autospec=True,
                          side_effect=btf.BtfInfo._prototype_parts) as parts:
            detail = database.detail_data(symbols)
            self.assertEqual(parts.call_count, 2)
            self.assertEqual(database.detail_data(symbols), detail)
            self.assertEqual(parts.call_count, 4)
        self.assertEqual([entry['prototype'] for entry in detail['functions'].values()],
                         [f'void {label}(enum {module}_type value);' for _, label, module, _, _ in symbols])
        self.assertEqual([entry['values'] for entry in detail['enums']],
                         [{'1': ['ALPHA']}, {'1': ['BETA']}])


class BtfDetailBrowserTest(unittest.TestCase):
    def test_paged_aliases_keep_original_order_and_copy_all_names(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            self.skipTest(str(error))
        names = [f'LONG_BTF_ALIAS_NAME_{index:03d}_FOR_NARROW_VIEWPORT_{"ODD" if index % 2 else "EVEN"}'
                 for index in range(73)]
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        directory = Path(kernel.dir)
        types = directory / 'enum-vmlinux'
        builder = BtfBuilder()
        aliases = enumeration(builder, 'aliased_mode', 8, [(name, WIDE_VALUE) for name in names])
        function(builder, 'many_aliases', aliases, [('mode', aliases)])
        short_aliases = enumeration(builder, 'short_mode', 4, [(name, 1) for name in names[:17]])
        function(builder, 'short_aliases', short_aliases, [('mode', short_aliases)])
        types.write_bytes(make_elf({'.BTF': builder.blob()}))
        trace = directory / 'many-aliases.txt'
        trace.write_text('# tracer: function_graph\n'
                         f'100.000001 |   0)  demo-1    |   1.000 us    |  many_aliases(mode={WIDE_VALUE}); /* ret={WIDE_VALUE} */\n'
                         f'100.000003 |   0)  demo-1    |   1.000 us    |  many_aliases(mode={WIDE_VALUE} [{names[-1]}]); /* ret={WIDE_VALUE} */\n'
                         f'100.000005 |   0)  demo-1    |   1.000 us    |  short_aliases(mode=1 [{names[16]}]); /* ret=1 */\n')
        kernel.run(str(trace), '--no-config', '--timeline', '--btf', str(types), '--')
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:
                self.skipTest(str(error))
            page = browser.new_page(viewport={'width': 320, 'height': 770}, is_mobile=True, has_touch=True)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            try:
                page.goto((directory / 'out.html').as_uri())
                page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
                argument = page.locator('.tl-enum-arg .tl-enum')
                summary = argument.locator('summary')
                self.assertIn(f'{names[0]} ({WIDE_VALUE})', summary.inner_text())
                self.assert_compact_enum_summary(summary, f'({WIDE_VALUE})', ['first of 73 names'])
                summary.press('Space')
                browse = argument.get_by_role('button', name='Browse all 73 names')
                browse.wait_for(state='visible')
                rows = argument.locator('li')
                displayed_names = rows.locator('.tl-code-symbol')
                self.assertEqual(rows.first.get_attribute('aria-current'), 'true')
                self.assertEqual(rows.first.locator('.tl-enum-name-basis').inner_text(), 'BTF first match')
                locate = argument.get_by_role('button', name='Locate recorded label')
                self.assertEqual(locate.count(), 0)
                self.assertEqual(displayed_names.all_text_contents(), names[:8])
                self.assert_alias_browse_reveals_filter(page, argument, browse)
                self.assert_alias_filter_reveals_feedback(page, argument, names)
                self.assert_alias_feedback_coalesces(page, argument, browse, names)
                browse.press('Enter')
                previous, next_page = (argument.get_by_role('button', name=name, exact=True)
                                       for name in ('Previous', 'Next'))
                copy_button = argument.get_by_role('button', name='Copy all names')
                name_filter = argument.get_by_role('searchbox', name='Filter alias names')
                status = argument.locator('.tl-enum-page-status')
                rows.first.evaluate('row => { window.firstAliasRow = row; }')
                name_filter.fill('lOnG')
                self.assertEqual(status.inner_text(), 'Names 1–24 of 73 matches (73 names total)')
                self.assertTrue(rows.first.evaluate('row => row === window.firstAliasRow'))
                status.evaluate('node => { window.aliasStatusText = node.firstChild; }')
                normalized = name_filter.evaluate('''input => {
                    const lower = String.prototype.toLowerCase;
                    let aliases = 0;
                    String.prototype.toLowerCase = function() {
                        if (String(this).startsWith('LONG_BTF_ALIAS_NAME_')) aliases++;
                        return lower.call(this);
                    };
                    try {
                        input.value = 'long_btf';
                        input.dispatchEvent(new InputEvent('input', {bubbles:true}));
                        return aliases;
                    } finally {
                        String.prototype.toLowerCase = lower;
                    }
                }''')
                self.assertEqual(normalized, 0)
                self.assertTrue(status.evaluate('node => node.firstChild === window.aliasStatusText'))
                self.assertTrue(rows.first.evaluate('row => row === window.firstAliasRow'))
                name_filter.fill('')
                self.assertEqual(status.inner_text(), 'Names 1–24 of 73')
                self.assertTrue(rows.first.evaluate('row => row === window.firstAliasRow'))
                self.assertTrue(previous.is_disabled())
                visited = []
                for start in range(0, len(names), 24):
                    end = min(start + 24, len(names))
                    self.assertEqual(status.inner_text(), f'Names {start + 1}–{end} of 73')
                    self.assertEqual(displayed_names.all_text_contents(), names[start:end])
                    self.assertEqual(argument.locator('li[aria-current="true"]').count(), int(start == 0))
                    visited.extend(displayed_names.all_text_contents())
                    if end < len(names):
                        next_page.press('Space')
                self.assertEqual(visited, names)
                self.assertTrue(next_page.is_disabled())
                self.assertTrue(previous.evaluate('button => button === document.activeElement'))
                self.assert_enum_body_geometry(argument, wide_body=True)
                for control in (previous, next_page, copy_button, name_filter):
                    bounds = control.bounding_box()
                    self.assertGreaterEqual(bounds['height'], 44)
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], 320)
                page.evaluate('''() => {
                    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
                        writeText: async text => { window.copiedEnumNames = text; }
                    }});
                }''')
                self.assert_copy_feedback_at_pane_edge(copy_button, argument.locator('.tl-enum-copy-status'),
                                                       '73 names copied')
                self.assertEqual(page.evaluate('copiedEnumNames'), '\n'.join(names))
                self.assertEqual(status.inner_text(), 'Names 73–73 of 73')
                name_filter.fill('  _eVeN  ')
                self.assertEqual(displayed_names.all_text_contents(), names[::2][:24])
                self.assertEqual(status.inner_text(), 'Names 1–24 of 37 matches (73 names total)')
                self.assertTrue(previous.is_disabled())
                self.assertTrue(name_filter.evaluate('input => input === document.activeElement'))
                next_page.press('Enter')
                self.assertEqual(displayed_names.all_text_contents(), names[::2][24:])
                self.assertEqual(status.inner_text(), 'Names 25–37 of 37 matches (73 names total)')
                page.evaluate('window.copiedEnumNames = null')
                copy_button.press('Enter')
                self.assertEqual(page.evaluate('copiedEnumNames'), '\n'.join(names))
                name_filter.evaluate('''input => {
                    input.value = 'NAME_072';
                    input.dispatchEvent(new InputEvent('input', {bubbles:true, isComposing:true}));
                }''')
                self.assertEqual(displayed_names.all_text_contents(), names[::2][24:])
                name_filter.dispatch_event('compositionend')
                self.assertEqual(displayed_names.all_text_contents(), names[-1:])
                name_filter.fill('not present')
                self.assertEqual(status.inner_text(), 'No matching names')
                self.assertEqual(rows.count(), 0)
                self.assertTrue(previous.is_disabled())
                self.assertTrue(next_page.is_disabled())
                name_filter.fill('')
                self.assertEqual(displayed_names.all_text_contents(), names[:24])
                next_page.press('Enter')
                previous.press('Enter')
                self.assertTrue(previous.is_disabled())
                self.assertTrue(next_page.evaluate('button => button === document.activeElement'))
                name_filter.fill('NAME_072')
                argument.get_by_role('button', name='Show fewer names').press('Space')
                self.assertEqual(displayed_names.all_text_contents(), names[:8])
                self.assertFalse(status.is_visible())
                browse.press('Enter')
                self.assertEqual(name_filter.input_value(), '')
                self.assertEqual(displayed_names.all_text_contents(), names[:24])
                self.assertEqual(argument.locator('.tl-enum-copy-status').inner_text(), '')
                self.assertIn(f'Recorded: {WIDE_VALUE}', argument.inner_text())
                self.assertEqual(page.locator('[data-recorded-field=return] .tl-enum li').count(), 0)
                self.assert_alias_footer_navigation(page, argument, names)
                self.assert_aliases_adapt_to_the_landscape_split(page, argument, names)
                self.assert_alias_footer_avoids_sticky_tabs(page, argument, names)
                page.set_viewport_size({'width': 844, 'height': 390})
                page.evaluate("fgView('timeline').probe().select(1)")
                divider = page.get_by_role('separator', name='Resize details')
                divider.press('Home')
                self.assertEqual(divider.get_attribute('aria-valuenow'), divider.get_attribute('aria-valuemin'))
                self.assert_compact_enum_summary(summary, f'({WIDE_VALUE})', ['73 names', 'Recorded label'])
                summary.tap()
                locate.wait_for(state='visible')
                self.assert_enum_body_geometry(argument, wide_body=True)
                summary.press('Space')
                page.set_viewport_size({'width': 320, 'height': 770})
                page.evaluate("fgView('timeline').probe().select(1)")
                self.assertIn(names[-1], summary.inner_text())
                summary.press('Enter')
                locate.wait_for(timeout=2000)
                self.assertEqual(argument.locator('li[aria-current="true"]').count(), 0)
                self.assertEqual(displayed_names.all_text_contents(), names[:8])
                self.assertGreaterEqual(locate.bounding_box()['height'], 44)
                locate.focus()
                outside_detail = '''() => [scrollX, scrollY, document.querySelector('.tl-scroll').scrollTop,
                    fgView('timeline').probe().view()]'''
                before = page.evaluate(outside_detail)
                locate.press('Enter')
                self.assertEqual(page.evaluate(outside_detail), before)
                self.assertEqual(displayed_names.all_text_contents(), names[-1:])
                self.assertEqual(status.inner_text(), 'Names 73–73 of 73')
                promoted = argument.locator('li[aria-current="true"]')
                self.assertEqual(promoted.locator('.tl-enum-name-basis').inner_text(), 'Recorded label')
                self.assertTrue(promoted.evaluate('''row => {
                    const box = row.getBoundingClientRect();
                    return row === document.activeElement &&
                        row.contains(document.elementFromPoint((box.left + box.right)/2, (box.top + box.bottom)/2));
                }'''))
                excluded = argument.get_by_role('status').filter(has_text='hidden by the current filter')
                self.assert_filtered_label_notice(page, argument, locate, excluded, names, outside_detail)
                name_filter.fill('_EVEN')
                self.assertFalse(excluded.is_visible())
                locate.tap()
                self.assertEqual(name_filter.input_value(), '_EVEN')
                self.assertEqual(displayed_names.all_text_contents(), names[::2][24:])
                self.assertEqual(status.inner_text(), 'Names 25–37 of 37 matches (73 names total)')
                self.assertTrue(promoted.evaluate('row => row === document.activeElement'))
                page.evaluate('window.copiedEnumNames = null')
                copy_button.press('Enter')
                self.assertEqual(page.evaluate('copiedEnumNames'), '\n'.join(names))
                self.assert_enum_body_geometry(argument, wide_body=True)

                page.evaluate("fgView('timeline').probe().select(2)")
                summary.press('Space')
                locate.wait_for(timeout=2000)
                self.assertEqual(displayed_names.all_text_contents(), names[:8])
                locate.press('Enter')
                self.assertEqual(displayed_names.all_text_contents(), names[:17])
                self.assertEqual(promoted.locator('.tl-code-symbol').inner_text(), names[16])
                self.assertTrue(promoted.evaluate('row => row === document.activeElement'))
                argument.get_by_role('button', name='Show fewer names').press('Enter')
                self.assertEqual(displayed_names.all_text_contents(), names[:8])
                self.assert_enum_body_geometry(argument, wide_body=True)
                self.assertEqual(errors, [])
            finally:
                page.close()
                browser.close()

    def test_prototypes_aliases_and_compact_parameters_preserve_raw_records(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            self.skipTest(str(error))
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        directory = Path(kernel.dir)
        types = directory / 'enum-vmlinux'
        types.write_bytes(make_elf({'.BTF': definitions().blob()}))
        trace = directory / 'enum-trace.txt'
        trace.write_text('# tracer: function_graph\n'
                         f'100.000001 |   0)  demo-1    |   1.000 us    |  submit(mode={hex(WIDE_VALUE)}); /* ret=0xffffffff */\n'
                         '100.000003 |   0)  demo-1    |   1.000 us    |  submit(mode=7); /* ret=0xffffffff */\n'
                         '100.000005 |   0)  demo-1    |   1.000 us    |  unused();\n'
                         f'100.000007 |   0)  demo-1    |   1.000 us    |  mixed(peer={hex(WIDE_VALUE)}, '
                         f'label="test, mode=7", mode={hex(WIDE_VALUE)});\n'
                         f'100.000009 |   0)  demo-1    |   1.000 us    |  mixed(mode=7, mode={hex(WIDE_VALUE)}, '
                         f'peer={hex(WIDE_VALUE)});\n'
                         '100.000011 |   0)  demo-1    |   1.000 us    |  many_aliases(mode=1); /* ret=0xffffffff */\n'
                         f'100.000013 |   0)  demo-1    |   1.000 us    |  compact({COMPACT_FTRACE_ARGS});\n'
                         f'100.000015 |   0)  demo-1    |   1.000 us    |  mixed({FTRACE_ENUM64_ARGS});\n'
                         f'100.000017 |   0)  demo-1    |   1.000 us    |  mixed({FTRACE_PARTIAL_ARGS});\n'
                         '100.000019 |   0)  demo-1    |   1.000 us    |  compact(mode=(ENUM64)[0x1]);\n'
                         '100.000021 |   0)  demo-1    |   1.000 us    |  compact(mode=1, ...);\n'
                         '100.000023 |   0)  demo-1    |   1.000 us    |  compact(mode=1, ..., n=1);\n'
                         f'100.000025 |   0)  demo-1    |   1.000 us    |  compact({FTRACE_LABELLED_ARGS});\n'
                         '100.000027 |   0)  demo-1    |   1.000 us    |  compact(mode=1 [SLOW]);\n'
                         '100.000029 |   0)  demo-1    |   1.000 us    |  compact(mode=7 [QUICK]);\n'
                         '100.000031 |   0)  demo-1    |   1.000 us    |  compact(mode=1 [QUICK], mode=2 [SLOW]);\n'
                         f'100.000033 |   0)  demo-1    |   1.000 us    |  mixed({FTRACE_LONG_ENUM_ARGS});\n')
        capture = directory / 'args.jsonl'
        capture.write_text(stream([call('entry', 'kernel', 100, 0, 'compact', 1, args=COMPACT_UFTRACE_SLOTS),
                                   call('exit', 'kernel', 200, 0, 'compact', 1)]))
        kernel.run(str(trace), '--no-config', '--timeline', '--overhead', '300,150', '--btf', str(types), '--')
        kernel.run(str(trace), '--no-config', '--timeline', '--overhead', '300,150', '--report-mode', 'sidecar',
                   '--btf', str(types), '--', out='sidecar')
        kernel.run(str(capture), '--no-config', '--timeline', '--no-text-view', '--btf', str(types), '--', out='uftrace.html')
        server = create_server(directory / 'sidecar')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:
                self.skipTest(str(error))
            for mode, url in (('single', (directory / 'out.html').as_uri()), ('sidecar', server.url),
                              ('uftrace', (directory / 'uftrace.html').as_uri())):
                with self.subTest(mode=mode):
                    page = browser.new_page(viewport={'width': 320, 'height': 770}, is_mobile=True, has_touch=True)
                    page.set_default_timeout(5000)
                    try:
                        if mode == 'uftrace':
                            page.goto(url)
                            page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
                            self.assert_compact_enum_layout(page, ', '.join(COMPACT_UFTRACE_SLOTS), 'tag=0x0')
                            rows = self.hover_enum_slice(page, 0)
                            self.assertIn('mode=FAST (1) · first of 2 names', rows['args (BTF)'])
                            self.assert_copy_prototype(page, 0, 'void compact(\n    int cpu,\n    int flags,\n    enum speed mode,\n'
                                                       '    const char *tag,\n    int r,\n    int n\n);')
                        else:
                            self.assert_tablet_call_layout(page, url)
                            self.assert_short_detail_value_actions(page, url)
                            self.assert_direct_disclosure_in_short_split(page, url)
                            self.assert_fullscreen_direct_prototype(page, url)
                            self.assert_actions_return_with_auto_expanded_prototype(page, url)
                            self.assert_narrow_detail(page, url)
                            self.assert_whole_prototype_reveal(page)
                            self.assert_disclosure_reveal(page)
                            self.assert_report(page, url)
                            page.evaluate("fgView('timeline').probe().select(6)")
                            self.assert_compact_enum_layout(page, COMPACT_FTRACE_ARGS, 'tag="a,b"')
                            self.assert_native_enum_formats(page)
                            self.assert_recorded_enum_labels(page)
                            self.assert_enum_hover(page)
                            self.assert_copy_prototype(page, 3, 'void mixed(\n    const char *label,\n'
                                                       '    mode_t mode,\n    enum peer_mode peer\n);')
                    finally:
                        page.close()
            browser.close()

    def assert_tablet_call_layout(self, page, url):
        viewport = page.viewport_size
        try:
            page.set_viewport_size({'width': 641, 'height': 900})
            page.goto(url)
            page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
            pane = page.locator('.tl-detail')
            prototype = pane.locator('.tl-btf-prototype')
            stack = pane.locator('[data-context-list=stack]')
            self.assertFalse(prototype.evaluate('node => node.open'))
            self.assertEqual(stack.locator('[data-stack-toggle]').get_attribute('aria-expanded'), 'false')
            self.assertEqual(stack.locator('[data-context-kind]').count(), 0)
            pane.get_by_role('button', name='Selected call actions').click()
            page.get_by_role('menuitem', name='Show function prototype').click()
            self.assertEqual(prototype.locator('code').inner_text(), 'enum tx_status submit(mode_t mode);')
            end = pane.locator('[data-detail-return=end]')
            self.assertTrue(end.evaluate('node => node.hidden'))
            outer = page.evaluate('[scrollX, scrollY, document.querySelector(".tl-scroll").scrollTop]')
            pane_size = pane.evaluate('node => [node.clientHeight, node.clientWidth]')
            pane.evaluate('''node => {
                const edge = node.getBoundingClientRect().top + node.clientTop;
                const head = node.firstElementChild.getBoundingClientRect().height;
                const declaration = node.querySelector('.tl-btf-prototype').getBoundingClientRect().height;
                const viewportBottom = Math.ceil(edge + head + declaration / 2);
                Object.defineProperty(visualViewport, 'height', {configurable: true, value: viewportBottom});
                visualViewport.dispatchEvent(new Event('resize'));
            }''')
            end.wait_for(state='visible', timeout=1500)
            self.assertEqual(pane.evaluate('node => [node.clientHeight, node.clientWidth]'), pane_size)
            end.evaluate('node => node.focus({preventScroll: true})')
            page.evaluate('''() => {
                delete visualViewport.height;
                visualViewport.dispatchEvent(new Event('resize'));
            }''')
            end.wait_for(state='hidden', timeout=1500)
            self.assertTrue(pane.locator('[data-detail-return=""]').evaluate('node => node === document.activeElement'))
            self.assertEqual(page.evaluate('[scrollX, scrollY, document.querySelector(".tl-scroll").scrollTop]'), outer)
            geometry = '''node => {
                const body = node.closest('.tl-detail-body'), [facts, context] = body.children;
                const pane = body.closest('.tl-detail');
                return {above: facts.getBoundingClientRect().bottom <= context.getBoundingClientRect().top,
                        width: node.getBoundingClientRect().width, overflow: pane.scrollWidth > pane.clientWidth};
            }'''
            narrow_width = None
            for width in (640, 641, 820, 960):
                with self.subTest(width=width):
                    page.set_viewport_size({'width': width, 'height': 900})
                    layout = prototype.evaluate(geometry)
                    self.assertTrue(layout['above'], layout)
                    self.assertFalse(layout['overflow'], layout)
                    if narrow_width is not None:
                        self.assertGreaterEqual(layout['width'], narrow_width - 1)
                    narrow_width = layout['width']
                    self.assertFalse(end.is_visible())
            page.set_viewport_size({'width': 1280, 'height': 900})
            self.assertFalse(prototype.evaluate(geometry)['above'])
            self.assertTrue(prototype.evaluate('node => node.open'))
            self.assertEqual(stack.locator('[data-context-kind]').count(), 0)
            page.set_viewport_size({'width': 820, 'height': 900})
            page.get_by_role('button', name='Return to call details', exact=True).click()
            pane.get_by_role('button', name='Selected call actions').click()
            page.get_by_role('menuitem', name='Show call stack').click()
            self.assertEqual(stack.locator('[data-stack-toggle]').get_attribute('aria-expanded'), 'true')
            self.assertGreater(stack.locator('[data-context-kind]').count(), 0)
            self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 0)
        finally:
            page.set_viewport_size(viewport)

    def hover_enum_slice(self, page, index):
        return page.evaluate('''async index => {
            const model = fgView('timeline').probe();
            const canvas = document.querySelector('.tl-canvas');
            const tip = document.querySelector('.tl-tip');
            const settle = () => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
            model.zoomTo(model.sStart[index], model.sEnd[index], index);
            await settle();
            const [clientX, clientY] = model.xyOf(index);
            if (document.elementFromPoint(clientX, clientY) !== canvas) throw Error('Call is outside the canvas');
            canvas.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX, clientY}));
            await settle();
            if (tip.style.display !== 'flex' || tip.querySelector('.t-name').title !== model.nameOf(index))
                throw Error('Incorrect hover target');
            if (tip.querySelector('details, li, button')) throw Error('Unexpected interactive preview');
            return Object.fromEntries([...tip.querySelectorAll('.t-row')].map(row =>
                [row.querySelector('.t-k').textContent, row.querySelector('.t-v').textContent]));
        }''', index)

    def assert_enum_hover(self, page):
        rows = self.hover_enum_slice(page, 0)
        self.assertIn(f'mode=TX_FAST ({WIDE_VALUE}) · first of 2 names', rows['args (BTF)'])
        self.assertEqual(rows['return (BTF)'], 'TX_BUSY (-1)')
        cases = (
            (3, 'args (BTF)', '"test, mode=7"'),
            (7, 'args (BTF)', f'mode=TX_FAST ({WIDE_VALUE})'),
            (8, 'args (BTF)', 'mode=(ENUM64)[0x25:...]'),
            (9, 'args', 'mode=(ENUM64)[0x1]'),
            (12, 'args (BTF)', 'mode=QUICK (1) · 2 names · Recorded label'),
            (13, 'args (BTF)', 'mode=FAST (1) · first of 2 names · Label mismatch'),
            (14, 'args (BTF)', 'mode=7 · No BTF match · Label mismatch'),
            (15, 'args', 'mode=1 [QUICK], mode=2 [SLOW]'),
        )
        for index, field, text in cases:
            with self.subTest(index=index):
                self.assertIn(text, self.hover_enum_slice(page, index)[field])
        preview = self.hover_enum_slice(page, 16)['args (BTF)']
        self.assertEqual(preview, f'mode=TX_FAST ({WIDE_VALUE}) · first of 2 names, peer=PEER_FAST ({WIDE_VALUE}), …')
        self.assertLessEqual(len(preview), 180)
        page.evaluate("fgView('timeline').probe().select(16)")
        evidence = page.locator('.tl-arguments > .tl-value-evidence')
        evidence.locator('summary').press('Enter')
        self.assertEqual(evidence.locator('.tl-code-raw').inner_text(), FTRACE_LONG_ENUM_ARGS)

    def assert_direct_disclosure_in_short_split(self, page, url):
        viewport = page.viewport_size
        page.set_viewport_size({'width': 240, 'height': 390})
        restore = page.get_by_role('button', name='Restore chart and detail split')
        timeline_state = '''() => {
            const timeline = fgView('timeline').probe();
            return [timeline.sel(), timeline.view(), document.querySelector('.tl-detail').clientHeight];
        }'''
        try:
            for selector, keyboard in (('.tl-btf-prototype', False), ('.tl-enum-arg .tl-enum', True)):
                with self.subTest(selector=selector, keyboard=keyboard):
                    page.goto(url)
                    page.evaluate("async () => (await fgSwitchView('timeline')).probe().select(0)")
                    disclosure = page.locator(selector).first
                    summary = disclosure.locator(':scope > summary')
                    activate = (lambda: summary.press('Enter')) if keyboard else summary.tap
                    summary.evaluate("node => node.scrollIntoView({block:'center', inline:'nearest'})")
                    before = page.evaluate(timeline_state)
                    self.assertFalse(disclosure.evaluate('node => node.open'))
                    if keyboard:
                        summary.focus()
                        page.keyboard.press('Enter')
                        self.assertTrue(summary.evaluate('node => node === document.activeElement'))
                    else:
                        summary.tap()
                    self.assertTrue(disclosure.evaluate('''node => {
                        const text = node.querySelector('pre code, .tl-enum-body .tl-code-raw');
                        const range = document.createRange();
                        range.selectNodeContents(text);
                        const firstLine = range.getClientRects()[0];
                        return node.open && !!firstLine && text.contains(document.elementFromPoint(
                            firstLine.x + firstLine.width / 2, firstLine.y + firstLine.height / 2));
                    }'''))
                    point = restore.evaluate('''button => {
                        const bounds = button.getBoundingClientRect();
                        const x = bounds.x + bounds.width / 2, y = bounds.y + bounds.height / 2;
                        const hit = document.elementFromPoint(x, y);
                        const pane = button.closest('.tl-detail');
                        return {point: [x, y], hit: button.contains(hit), target: hit?.outerHTML.slice(0, 145),
                            pane: [pane.getBoundingClientRect().top, pane.clientHeight],
                            tabs: document.querySelector('.view-tabs')?.getBoundingClientRect().bottom, windowY: scrollY};
                    }''')
                    self.assertTrue(point['hit'], point)
                    page.touchscreen.tap(*point['point'])
                    self.assertEqual(page.locator('.tl-detail-reading').count(), 0)
                    self.assertEqual(page.evaluate(timeline_state), before)
                    self.assertTrue(disclosure.evaluate('node => node.open'))

                    summary.press('Enter')
                    summary.evaluate("node => node.scrollIntoView({block:'center', inline:'nearest'})")
                    scroll = page.evaluate("[scrollY, document.querySelector('.tl-detail').scrollTop]")
                    activate()
                    self.assertTrue(restore.is_visible())
                    activate()
                    self.assertFalse(disclosure.evaluate('node => node.open'))
                    self.assertEqual(page.locator('.tl-detail-reading').count(), 0)
                    self.assertEqual(page.evaluate(timeline_state), before)
                    self.assertEqual(page.evaluate("[scrollY, document.querySelector('.tl-detail').scrollTop]"), scroll)
                    if keyboard:
                        self.assertTrue(summary.evaluate('node => node === document.activeElement'))

                    activate()
                    self.assertEqual(page.locator('.tl-detail-reading').count(), 1)
                    if not keyboard:
                        disclosure.get_by_role('button', name='Copy declaration').focus()
                    page.keyboard.press('Escape')
                    self.assertEqual(disclosure.count(), 1)
                    self.assertFalse(disclosure.evaluate('node => node.open'))
                    self.assertEqual(page.locator('.tl-detail-reading').count(), 0)
                    self.assertEqual(page.evaluate(timeline_state), before)
                    self.assertEqual(page.evaluate("[scrollY, document.querySelector('.tl-detail').scrollTop]"), scroll)
                    self.assertTrue(summary.evaluate('node => node === document.activeElement'))

                    page.get_by_role('button', name='Expand details into the chart area').tap()
                    summary.press('Enter')
                    self.assertTrue(disclosure.evaluate('node => node.open'))
                    summary.press('Enter')
                    self.assertTrue(restore.is_visible())

                    restore.tap()
                    summary.evaluate("node => node.scrollIntoView({block:'center', inline:'nearest'})")
                    activate()
                    self.assertEqual(page.locator('.tl-detail-reading').count(), 1)
                    page.evaluate("fgView('timeline').probe().select(1)")
                    self.assertEqual(page.locator('.tl-detail-reading').count(), 0)
                    self.assertTrue(page.locator('.tl-canvas').is_visible())
                    selected, view, height = page.evaluate(timeline_state)
                    self.assertEqual([selected, view, height], [1, *before[1:]])
        finally:
            page.set_viewport_size(viewport)

    def assert_fullscreen_direct_prototype(self, page, url):
        viewport = page.viewport_size
        page.set_viewport_size({'width': 240, 'height': 240})
        state = '''() => {
            const root = document.getElementById('timelineView'), pane = root.querySelector('.tl-detail');
            const timeline = fgView('timeline').probe();
            return [root.scrollTop, pane.scrollTop, pane.clientHeight, timeline.sel(), timeline.view()];
        }'''
        try:
            for native in (False, True):
                with self.subTest(native=native):
                    page.goto(url)
                    page.evaluate("async () => (await fgSwitchView('timeline')).probe().select(0)")
                    if not native:
                        page.evaluate("document.getElementById('timelineView').requestFullscreen = undefined")
                    page.get_by_role('button', name='Full screen', exact=True).tap()
                    page.wait_for_function('''native => !!document.fullscreenElement === native &&
                        document.querySelector('.tl-fullscreen') && !document.querySelector('[data-act=max]').disabled''', arg=native)
                    summary = page.locator('.tl-btf-prototype > summary')
                    summary.evaluate("node => node.scrollIntoView({block:'center', inline:'nearest'})")
                    before = page.evaluate(state)
                    summary.tap()
                    observed = summary.evaluate('''node => {
                        const details = node.parentElement;
                        const pane = node.closest('.tl-detail');
                        const code = details.querySelector('code');
                        const range = document.createRange();
                        range.selectNodeContents(code);
                        const line = range.getClientRects()[0];
                        const hits = (element, rect = element.getBoundingClientRect()) =>
                            [rect.top + 1, rect.bottom - 1].every(y =>
                                element.contains(document.elementFromPoint(rect.left + rect.width / 2, y)));
                        return { title: hits(node), line: !!line && hits(code, line),
                            copy: hits(details.querySelector('[data-copy-prototype]')),
                            restore: hits(pane.querySelector('[data-detail-expand]')),
                            exit: hits(document.querySelector('[data-act=max]')) };
                    }''')
                    self.assertTrue(all(observed.values()), observed)
                    summary.press('Enter')
                    self.assertFalse(summary.evaluate('node => node.parentElement.open'))
                    self.assertEqual(page.locator('.tl-detail-reading').count(), 0)
                    self.assertEqual(page.evaluate(state), before)
                    summary.press('Enter')
                    self.assertEqual(page.locator('.tl-detail-reading').count(), 1)
                    page.keyboard.press('Escape')
                    self.assertEqual(page.locator('.tl-fullscreen').count(), 1)
                    self.assertEqual(page.evaluate('!!document.fullscreenElement'), native)
                    self.assertTrue(summary.evaluate('node => !node.parentElement.open && node === document.activeElement'))
                    self.assertEqual(page.evaluate(state), before)
                    page.get_by_role('button', name='Exit full screen', exact=True).tap()
                    page.locator('.tl-fullscreen').wait_for(state='detached')
        finally:
            if page.evaluate('!!document.fullscreenElement'):
                page.evaluate('document.exitFullscreen()')
                page.locator('.tl-fullscreen').wait_for(state='detached')
            page.set_viewport_size(viewport)

    def assert_actions_return_with_auto_expanded_prototype(self, page, url):
        viewport = page.viewport_size
        page.set_viewport_size({'width': 240, 'height': 390})
        try:
            page.goto(url)
            page.evaluate("async () => (await fgSwitchView('timeline')).probe().select(0)")
            disclosure = page.locator('.tl-btf-prototype')
            summary = disclosure.locator(':scope > summary')
            trigger = page.get_by_role('button', name='Selected call actions')
            back = page.get_by_role('button', name='Return to call details', exact=True)
            summary.evaluate("node => node.scrollIntoView({block:'center', inline:'nearest'})")
            state = '''() => [scrollY, document.querySelector('.tl-detail').clientHeight,
                fgView('timeline').probe().sel(), fgView('timeline').probe().view()]'''
            before = page.evaluate(state)
            summary.tap()
            self.assertTrue(page.locator('.tl-detail-reading').count())

            trigger.tap()
            page.get_by_role('menuitem', name='Show function prototype').tap()
            back.tap()
            self.assertTrue(page.locator('.tl-detail-reading').count())
            self.assertTrue(disclosure.evaluate('node => node.open'))

            trigger.tap()
            page.get_by_role('menuitem', name='Show function prototype').tap()
            summary.press('Enter')
            self.assertFalse(disclosure.evaluate('node => node.open'))
            self.assertTrue(back.is_visible())
            page.keyboard.press('Escape')
            self.assertFalse(back.is_visible())
            self.assertTrue(page.locator('.tl-canvas').is_visible())
            self.assertEqual(page.evaluate(state), before)
            focused = '''button => {
                const bounds = button.getBoundingClientRect();
                return button === document.activeElement && button.contains(document.elementFromPoint(
                    bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
            }'''
            self.assertTrue(trigger.evaluate(focused))

            trigger.tap()
            before = page.evaluate(state)
            page.get_by_role('menuitem', name='Show call stack').tap()
            self.assertTrue(page.locator('.tl-canvas').is_visible())
            summary.evaluate("node => node.scrollIntoView({block:'center', inline:'nearest'})")
            summary.tap()
            self.assertTrue(page.locator('.tl-detail-reading').count())
            back.tap()
            self.assertFalse(back.is_visible())
            self.assertEqual(page.evaluate(state), before)
            self.assertTrue(disclosure.evaluate('node => node.open'))
            self.assertTrue(trigger.evaluate(focused))
        finally:
            page.set_viewport_size(viewport)

    def assert_whole_prototype_reveal(self, page):
        viewport = page.viewport_size
        page.set_viewport_size({'width': 844, 'height': 470})
        try:
            page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
            page.evaluate("fgView('timeline').probe().select(3)")
            summary = page.locator('.tl-btf-prototype > summary')
            position = '''node => {
                const pane = node.closest('.tl-detail');
                node.scrollIntoView({block:'end'});
                const bottom = Math.min(innerHeight, pane.getBoundingClientRect().top + pane.clientTop + pane.clientHeight);
                pane.scrollTop += node.getBoundingClientRect().bottom - bottom + 4;
                node.focus({preventScroll:true});
                return [scrollY, fgView('timeline').probe().view(), document.querySelector('.tl-scroll').scrollTop];
            }'''
            coverage = '''node => {
                const disclosure = node.parentElement, code = disclosure.querySelector('code');
                const range = document.createRange();
                range.selectNodeContents(code);
                const lines = [...range.getClientRects()].filter(rect => rect.width > 0);
                const hit = (el, rect) => [rect.top + 1, rect.bottom - 1].every(y =>
                    el.contains(document.elementFromPoint(rect.left + rect.width / 2, y)));
                const copy = disclosure.querySelector('[data-copy-prototype]');
                const pane = node.closest('.tl-detail');
                const vertical = rect => [rect.top, rect.bottom];
                const hitTargets = rect => [rect.top + 1, rect.bottom - 1].map(y =>
                    document.elementFromPoint(rect.left + rect.width / 2, y)?.className ?? null);
                return {lines:lines.length, visible:lines.filter(line => hit(code, line)).length,
                    summary:hit(node, node.getBoundingClientRect()), copy:hit(copy, copy.getBoundingClientRect()),
                    focused:node === document.activeElement, expanded:!!node.closest('.tl-detail-expanded'),
                    geometry:{heading:vertical(node.getBoundingClientRect()), pane:vertical(pane.getBoundingClientRect()),
                        scroll:pane.scrollTop, clip:[pane.clientTop, pane.clientHeight, innerHeight, visualViewport.height],
                        first:vertical(lines[0]), last:vertical(lines.at(-1)), lastHits:hitTargets(lines.at(-1))},
                    unchanged:[scrollY, fgView('timeline').probe().view(), document.querySelector('.tl-scroll').scrollTop]};
            }'''
            summary.evaluate(f'''node => addEventListener('resize', () => {{
                window.prototypePositionAtResize = ({position})(node);
                node.click();
            }}, {{once:true, capture:true}})''')
            page.set_viewport_size({'width': 844, 'height': 390})
            page.wait_for_function('() => window.prototypePositionAtResize !== undefined')
            observed = summary.evaluate(coverage)
            self.assertGreater(observed['lines'], 1)
            self.assertEqual(observed['visible'], observed['lines'], observed)
            self.assertTrue(observed['summary'] and observed['copy'] and observed['focused'], observed)
            self.assertFalse(observed['expanded'], observed)
            self.assertEqual(observed['unchanged'], page.evaluate('window.prototypePositionAtResize'))

            pane_height = observed['geometry']['last'][1] - observed['geometry']['heading'][0] + 5
            summary.evaluate('''(node, height) => {
                node.parentElement.open = false;
                node.closest('.tl-detail').style.maxHeight = Math.ceil(height) + 'px';
            }''', pane_height)
            before = summary.evaluate(position)
            page.keyboard.press('Enter')
            tight = summary.evaluate(coverage)
            self.assertEqual(tight['visible'], tight['lines'], tight)
            self.assertTrue(tight['summary'] and tight['copy'] and tight['focused'], tight)
            self.assertFalse(tight['expanded'], tight)
            self.assertEqual(tight['unchanged'], before)
        finally:
            page.set_viewport_size(viewport)

    def assert_disclosure_reveal(self, page):
        viewport = page.viewport_size
        page.goto(page.url)
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
        cases = (('.tl-btf-prototype', 'code'), ('.tl-enum-arg .tl-enum', '.tl-code-raw'),
                 ('.tl-arguments > .tl-value-evidence', '.tl-code-raw'), ('.tl-return-evidence', '.tl-code-raw'))
        state = '''() => [scrollY, fgView('timeline').probe().sel(), ...fgView('timeline').probe().view(),
            document.querySelector('.tl-scroll').scrollTop]'''
        for size, keyboard in ((viewport, False), ({'width': 844, 'height': 390}, True)):
            page.set_viewport_size(size)
            page.evaluate('async () => { await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))); }')
            for selector, contents in cases:
                with self.subTest(size=size, selector=selector):
                    summary = page.locator(selector).locator(':scope > summary')
                    point = summary.evaluate('''node => {
                        const pane = node.closest('.tl-detail');
                        node.parentElement.open = false;
                        node.scrollIntoView({block:'end'});
                        const bottom = Math.min(innerHeight, pane.getBoundingClientRect().top + pane.clientTop + pane.clientHeight);
                        pane.scrollTop += node.getBoundingClientRect().bottom - bottom + 4;
                        const rect = node.getBoundingClientRect();
                        return {x:Math.round(rect.left + 12), y:Math.round((rect.top + rect.bottom) / 2),
                            scroll:pane.scrollTop};
                    }''')
                    if keyboard:
                        summary.evaluate('node => node.focus({preventScroll:true})')
                    before = page.evaluate(state)
                    if keyboard:
                        page.keyboard.press('Enter')
                    else:
                        page.touchscreen.tap(point['x'], point['y'])
                    observed = summary.evaluate('''(node, selector) => {
                        const hit = rect => {
                            const x = Math.ceil(rect.left + Math.min(4, rect.width / 2));
                            return [rect.top + 1, rect.bottom - 1].every(y => node.parentElement.contains(document.elementFromPoint(x, y)));
                        };
                        const first = node.parentElement.querySelector(selector)?.getClientRects()[0];
                        const copy = node.parentElement.querySelector('[data-copy-prototype]')?.getBoundingClientRect();
                        return {open:node.parentElement.open, summary:hit(node.getBoundingClientRect()),
                            copy:!copy || hit(copy), first:!!first && hit(first), focused:node === document.activeElement,
                            scroll:node.closest('.tl-detail').scrollTop};
                    }''', contents)
                    self.assertGreater(observed.pop('scroll'), point['scroll'])
                    self.assertTrue(all(observed.values()), observed)
                    self.assertEqual(page.evaluate(state), before)
                    scroll_stays = summary.evaluate('''node => {
                        const pane = node.closest('.tl-detail');
                        node.parentElement.open = false;
                        const before = pane.scrollTop;
                        node.click();
                        return pane.scrollTop === before;
                    }''')
                    self.assertTrue(scroll_stays)
                    summary.evaluate('node => { node.parentElement.open = false; }')
        page.set_viewport_size(viewport)

    def assert_copy_feedback_at_pane_edge(self, button, status, message):
        button.scroll_into_view_if_needed()
        geometry = '''(button, status) => {
            const pane = button.closest('.tl-detail');
            const paneRect = pane.getBoundingClientRect();
            const top = Math.max(paneRect.top + pane.clientTop, document.querySelector('.view-tabs').getBoundingClientRect().bottom);
            const bottom = Math.min(paneRect.top + pane.clientTop + pane.clientHeight, innerHeight);
            if (!status) pane.scrollTop += Math.ceil(button.getBoundingClientRect().bottom - bottom + 2);
            const control = button.getBoundingClientRect();
            const feedback = status?.getBoundingClientRect();
            const model = fgView('timeline').probe();
            return {top, bottom, controlTop:control.top, controlBottom:control.bottom, statusBottom:feedback?.bottom,
                paneScroll:pane.scrollTop, focused:button === document.activeElement,
                outer:[scrollY, model.sel(), model.view()]};
        }'''
        before = button.evaluate(geometry, None)
        self.assertGreaterEqual(before['controlTop'], before['top'])
        self.assertLessEqual(before['controlBottom'], before['bottom'])
        self.assertLessEqual(before['bottom'] - before['controlBottom'], 3)
        button.press('Enter')
        status.get_by_text(message, exact=True).wait_for()
        after = button.evaluate(geometry, status.element_handle())
        self.assertGreater(after['paneScroll'], before['paneScroll'])
        self.assertLessEqual(after['statusBottom'], after['bottom'])
        self.assertGreaterEqual(after['controlTop'], after['top'])
        self.assertTrue(after['focused'])
        self.assertEqual(after['outer'], before['outer'])

    def assert_copy_prototype(self, page, index, expected):
        page.evaluate("index => fgView('timeline').probe().select(index)", index)
        declaration = page.locator('.tl-btf-prototype')
        button = declaration.get_by_role('button', name='Copy declaration')
        self.assertEqual(declaration.locator('[data-copy-prototype]').count(), 0)
        declaration.locator('summary').press('Enter')
        self.assertEqual(declaration.locator('code').inner_text(), expected)
        self.assertGreaterEqual(button.bounding_box()['height'], 44)
        status = declaration.get_by_role('status')
        page.evaluate('''() => {
            window.prototypeWrites = [];
            window.prototypeFallbacks = [];
            window.prototypeCopyMode = 'success';
            Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
                writeText: async text => {
                    prototypeWrites.push(text);
                    if (prototypeCopyMode === 'pending')
                        return new Promise((resolve, reject) => { window.rejectPrototypeCopy = reject; });
                    if (prototypeCopyMode !== 'success') throw new Error('denied');
                }
            }});
            document.execCommand = command => {
                if (command !== 'copy') throw new Error(command);
                prototypeFallbacks.push(document.querySelector('textarea[aria-hidden=true]').value);
                return prototypeCopyMode !== 'denied';
            };
        }''')
        for mode, feedback in (('success', 'Declaration copied'), ('fallback', 'Declaration copied'),
                               ('denied', 'Copy failed. Select the declaration to copy it.')):
            page.evaluate('mode => { window.prototypeCopyMode = mode; }', mode)
            if mode == 'success':
                self.assert_copy_feedback_at_pane_edge(button, status, feedback)
            else:
                button.press('Enter')
                status.get_by_text(feedback, exact=True).wait_for()
            self.assertTrue(button.evaluate('element => element === document.activeElement'))
            self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), index)
        self.assertEqual(page.evaluate('prototypeWrites'), [expected] * 3)
        self.assertEqual(page.evaluate('prototypeFallbacks'), [expected] * 2)

        page.evaluate("window.prototypeCopyMode = 'pending'")
        button.press('Space')
        page.wait_for_function("() => typeof window.rejectPrototypeCopy === 'function'")
        declaration.locator('summary').press('Enter')
        self.assertFalse(button.is_visible())
        page.evaluate('''async () => {
            rejectPrototypeCopy(new Error('late denial'));
            await new Promise(resolve => requestAnimationFrame(resolve));
        }''')
        self.assertEqual(page.evaluate('prototypeFallbacks'), [expected] * 2)
        declaration.locator('summary').press('Enter')
        self.assertEqual(status.inner_text(), '')
        page.evaluate('delete window.rejectPrototypeCopy')
        button.press('Enter')
        page.wait_for_function("() => typeof window.rejectPrototypeCopy === 'function'")
        page.evaluate('''async index => {
            fgView('timeline').probe().select(index);
            rejectPrototypeCopy(new Error('call changed'));
            await new Promise(resolve => requestAnimationFrame(resolve));
        }''', (index - 1) if index else -1)
        self.assertEqual(page.evaluate('prototypeFallbacks'), [expected] * 2)
        self.assertFalse(button.is_visible())

    def assert_short_detail_value_actions(self, page, url):
        viewport = page.viewport_size
        page.goto(url)
        page.evaluate("async () => (await fgSwitchView('timeline')).probe().select(0)")
        trigger = page.get_by_role('button', name='Selected call actions')
        back = page.get_by_role('button', name='Return to call details', exact=True)
        prototype = page.locator('.tl-btf-prototype')
        arguments = page.locator('[data-recorded-field=arguments]')
        result = page.locator('[data-recorded-field=return]')
        state = '''() => {
            const detail = document.querySelector('.tl-detail');
            const timeline = fgView('timeline').probe();
            return [detail.clientHeight, detail.clientWidth, detail.scrollTop, detail.scrollLeft,
                scrollY, timeline.view(), timeline.sel()];
        }'''
        hit = '''element => {
            const box = element.getBoundingClientRect();
            return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
        }'''
        for width, height, prototype_only in ((240, 240, True), (240, 390, True), (844, 240, True),
                                              (280, 390, False), (320, 480, False), (568, 320, False),
                                              (844, 390, False)):
            with self.subTest(width=width, height=height):
                page.set_viewport_size({'width': width, 'height': height})
                page.evaluate("() => { fgView('timeline').probe().select(2); fgView('timeline').probe().select(0); }")
                trigger.scroll_into_view_if_needed()
                trigger.tap()
                before = page.evaluate(state)
                self.assertEqual(prototype.locator('code').count(), 0)
                page.get_by_role('menuitem', name='Show function prototype').tap()
                prototype.locator('code').wait_for(state='attached')
                self.assertTrue(prototype.get_by_role('button', name='Copy declaration').evaluate(hit))
                self.assertTrue(prototype.locator('code').evaluate('''code => {
                    const range = document.createRange();
                    range.selectNodeContents(code);
                    const line = range.getClientRects()[0];
                    return !!line && code.contains(document.elementFromPoint(line.x + line.width / 2, line.y + line.height / 2));
                }'''))
                self.assertTrue(back.evaluate(hit))
                if (width, height) != (844, 240):
                    self.assertEqual(page.locator('.tl-detail-expanded').count(), int(before[0] < 140))
                back.tap()
                self.assertEqual(page.evaluate(state), before)
                self.assertTrue(prototype.evaluate('node => node.open'))
                self.assertTrue(trigger.evaluate('node => node === document.activeElement'))
                if prototype_only:
                    continue

                for name, field, value in (('Show recorded arguments', arguments, 'TX_FAST'),
                                           ('Show recorded return value', result, 'TX_BUSY')):
                    trigger.tap()
                    before = page.evaluate(state)
                    page.get_by_role('menuitem', name=name).tap()
                    summary = field.locator('.tl-enum > summary')
                    self.assertTrue(summary.evaluate(hit))
                    self.assertIn(value, summary.inner_text())
                    self.assertFalse(summary.evaluate('node => node.parentElement.open'))
                    self.assertTrue(field.locator('.k').evaluate('node => node === document.activeElement'))
                    page.keyboard.press('Tab')
                    self.assertTrue(summary.evaluate('node => node === document.activeElement'))
                    page.keyboard.press('Shift+Tab')
                    self.assertTrue(back.evaluate('node => node === document.activeElement'))
                    page.keyboard.press('Enter')
                    self.assertEqual(page.evaluate(state), before)
                    self.assertTrue(trigger.evaluate('node => node === document.activeElement'))

        page.set_viewport_size({'width': 280, 'height': 390})
        page.evaluate("() => { fgView('timeline').probe().select(2); fgView('timeline').probe().select(5); }")
        trigger.tap()
        before = page.evaluate(state)
        page.get_by_role('menuitem', name='Show recorded arguments').tap()
        end = page.get_by_role('button', name='Return to call details from end of section', include_hidden=True)
        self.assertFalse(end.is_visible())
        enum = arguments.locator('.tl-enum')
        enum.locator('summary').tap()
        enum.get_by_role('button', name='Browse all 33 names').tap()
        names = enum.locator('li')
        self.assertEqual(names.count(), 24)
        end.wait_for(state='visible')
        end.focus()
        enum.evaluate('node => { node.open = false; }')
        end.wait_for(state='hidden')
        self.assertTrue(back.evaluate('button => button === document.activeElement'))
        enum.locator('summary').tap()
        self.assertEqual(names.count(), 24)
        end.wait_for(state='visible')
        end.evaluate('''node => {
            const pane = node.closest('.tl-detail');
            pane.scrollTop += node.getBoundingClientRect().bottom - pane.getBoundingClientRect().bottom + 6;
        }''')
        self.assertFalse(back.evaluate(hit))
        self.assertTrue(end.evaluate(hit))
        self.assertGreaterEqual(end.bounding_box()['height'], 44)
        end.tap()
        self.assertEqual(page.evaluate(state), before)
        self.assertTrue(trigger.evaluate('node => node === document.activeElement'))
        self.assertTrue(enum.evaluate('node => node.open'))
        self.assertEqual(names.count(), 24)

        trigger.tap()
        before = page.evaluate(state)
        page.get_by_role('menuitem', name='Show function prototype').tap()
        prototype.get_by_role('button', name='Copy declaration').focus()
        page.keyboard.press('Escape')
        self.assertFalse(back.is_visible())
        self.assertTrue(trigger.evaluate('node => node === document.activeElement'))
        self.assertEqual(page.evaluate(state), before)

        page.set_viewport_size({'width': 320, 'height': 480})
        trigger.tap()
        page.get_by_role('menuitem', name='Show recorded arguments').tap()
        self.assertTrue(page.locator('.tl-detail-expanded').count())
        page.evaluate("fgView('timeline').probe().select(2)")
        self.assertFalse(page.locator('.tl-detail-expanded').count())
        self.assertFalse(back.is_visible())
        trigger.tap()
        self.assertEqual(page.get_by_role('menuitem', name='Show recorded arguments').count(), 0)
        self.assertEqual(page.get_by_role('menuitem', name='Show recorded return value').count(), 0)
        page.set_viewport_size(viewport)

    def assert_narrow_detail(self, page, url):
        page.goto(url)
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
        pane = page.locator('.tl-detail')
        heading = pane.locator('.tl-slice-head')
        title = heading.locator('.tl-detail-title').bounding_box()
        actions = heading.get_by_role('button', name='Selected call actions').bounding_box()
        close = heading.locator('.tl-close').bounding_box()
        self.assertLessEqual(title['x'] + title['width'], actions['x'])
        self.assertLessEqual(actions['x'] + actions['width'], close['x'])
        self.assertAlmostEqual(actions['y'], close['y'], delta=1)
        self.assertGreaterEqual(actions['height'], 44)
        navigation = pane.get_by_role('group', name='Recorded call navigation')
        self.assertEqual(navigation.get_by_role('button', name='First recorded child', exact=False).inner_text(), '↓ Child')
        buttons = navigation.get_by_role('button').evaluate_all('''buttons => buttons.map(button => {
            const rect = button.getBoundingClientRect();
            return [rect.top, rect.height];
        })''')
        self.assertEqual(len(buttons), 4)
        self.assertEqual(len({top for top, height in buttons}), 1)
        self.assertTrue(all(height >= 44 for top, height in buttons))
        self.assertLessEqual(navigation.bounding_box()['height'], 54)

        for selector in ('.tl-btf-prototype', '.tl-arguments .tl-enum', '[data-recorded-field=return] .tl-enum'):
            target = pane.locator(selector)
            summary = target.locator(':scope > summary')
            self.assertFalse(target.evaluate('node => node.open'))
            for opened in (True, False):
                area = summary.evaluate('''node => {
                    node.scrollIntoView({block:'center', inline:'nearest'});
                    const bounds = node.getBoundingClientRect();
                    const column = node.closest('.v').getBoundingClientRect();
                    const x = column.right - 5, y = bounds.bottom - 5;
                    return {x, y, height:bounds.height, hit:node.contains(document.elementFromPoint(x, y))};
                }''')
                self.assertGreaterEqual(area['height'], 44, area)
                self.assertTrue(area['hit'], area)
                page.touchscreen.tap(area['x'], area['y'])
                self.assertEqual(target.evaluate('node => node.open'), opened)

        disclosure = pane.locator('.tl-btf-prototype > summary')
        result = disclosure.evaluate('''summary => {
            const pane = summary.closest('.tl-detail');
            const hit = () => {
                const rect = summary.getBoundingClientRect();
                return summary.contains(document.elementFromPoint(rect.left + 4, rect.top + rect.height / 2));
            };
            pane.scrollTop += summary.getBoundingClientRect().top - pane.getBoundingClientRect().top - 18;
            const obscured = !hit();
            summary.focus();
            return [obscured, hit(), summary === document.activeElement];
        }''')
        self.assertEqual(result, [True, True, True])
        page.keyboard.press('Enter')
        disclosure.locator('..').locator('code').wait_for(state='visible')
        viewport = page.viewport_size
        for width in (320, 390, 640, 1280):
            page.set_viewport_size({'width': width, 'height': viewport['height']})
            geometry = pane.locator('.tl-prototype-body pre').evaluate('''pre => {
                const grid = pre.closest('.tl-call-facts');
                const copy = pre.parentElement.querySelector('[data-copy-prototype]').getBoundingClientRect();
                const bounds = pre.getBoundingClientRect();
                const fields = grid.getBoundingClientRect();
                return {left: bounds.left, right: bounds.right, gridLeft: fields.left, gridRight: fields.right,
                    copyAboveCode: copy.bottom <= bounds.top && copy.left >= fields.left && copy.right <= fields.right,
                    overflow: pre.scrollWidth > pre.clientWidth};
            }''')
            with self.subTest(width=width):
                self.assertAlmostEqual(geometry['left'], geometry['gridLeft'], delta=1)
                self.assertAlmostEqual(geometry['right'], geometry['gridRight'], delta=1)
                self.assertTrue(geometry['copyAboveCode'], geometry)
                self.assertFalse(geometry['overflow'])
                self.assertTrue(disclosure.locator('..').evaluate('node => node.open'))
        page.set_viewport_size(viewport)
        disclosure.evaluate('summary => { summary.parentElement.open = false; }')
        heading.get_by_role('button', name='Selected call actions').tap()
        self.assertFalse(disclosure.evaluate('summary => summary.parentElement.open'))
        page.get_by_role('menuitem', name='Show function prototype').tap()
        self.assertTrue(disclosure.evaluate('summary => summary.parentElement.open && summary === document.activeElement'))
        self.assertIn('submit', disclosure.locator('..').locator('code').inner_text())
        back = page.get_by_role('button', name='Return to call details', exact=True)
        back.tap()
        self.assertFalse(back.is_visible())
        self.assertTrue(heading.get_by_role('button', name='Selected call actions').evaluate(
            'button => button === document.activeElement'))
        self.assertTrue(disclosure.evaluate('summary => summary.parentElement.open'))

    def assert_report(self, page, url):
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
        pane = page.locator('.tl-detail')
        container_name = 'pane => getComputedStyle(pane).containerName'
        self.assertEqual(pane.evaluate(container_name), 'none')
        prototype = page.locator('.tl-btf-prototype')
        self.assertFalse(prototype.evaluate('node => node.open'))
        self.assertEqual(prototype.locator('code').count(), 0)
        prototype.locator('summary').press('Enter')
        self.assertEqual(prototype.locator('code').inner_text(), 'enum tx_status submit(mode_t mode);')
        self.assertEqual(pane.evaluate(container_name), 'none')
        argument = page.locator('.tl-enum-arg .tl-enum')
        self.assertIn(f'TX_FAST ({WIDE_VALUE})', argument.locator('summary').inner_text())
        self.assertEqual(argument.locator('li').count(), 0)
        argument.locator('summary').press('Space')
        argument.locator('li').last.wait_for(state='visible')
        self.assertEqual(argument.locator('li > .tl-code-symbol').all_text_contents(), ['TX_FAST', 'TX_LOW_LATENCY'])
        self.assertEqual(argument.locator('[data-enum-show-all]').count(), 0)
        self.assertIn(hex(WIDE_VALUE), argument.inner_text())
        return_value = page.locator('.tl-enum').last
        self.assertIn('TX_BUSY (-1)', return_value.locator('summary').inner_text())
        return_value.locator('summary').press('Enter')
        self.assertEqual(return_value.locator('.tl-enum-body .tl-code-raw').inner_text(), '0xffffffff')
        self.assert_enum_body_geometry(return_value, wide_body=True)
        argument.locator('summary').press('Space')
        self.assertEqual(pane.evaluate(container_name), 'tl-call-detail')
        return_value.locator('summary').press('Enter')
        self.assertEqual(pane.evaluate(container_name), 'none')
        evidence = page.locator('.tl-return-evidence')
        evidence.locator('summary').press('Enter')
        self.assert_enum_body_geometry(evidence, wide_body=True, body_selector='.tl-value-evidence-body')
        self.assertEqual(page.locator('.tl-slice-head .pill.err').count(), 0)
        page.evaluate("fgView('timeline').probe().select(1)")
        self.assertEqual(pane.evaluate(container_name), 'none')
        self.assertTrue(prototype.evaluate('node => node.open'))
        self.assertEqual(prototype.locator('code').count(), 1)
        self.assert_compact_enum_summary(page.locator('.tl-enum-arg summary'), '7', ['No BTF match'])
        page.evaluate("fgView('timeline').probe().select(2)")
        self.assertFalse(prototype.evaluate('node => node.open'))
        self.assertEqual(prototype.locator('code').count(), 0)
        prototype.locator('summary').press('Enter')
        self.assertEqual(prototype.locator('code').text_content(), 'void unused(mode_t mode);')
        page.evaluate("fgView('timeline').probe().select(3)")
        self.assertFalse(prototype.evaluate('node => node.open'))
        prototype.locator('summary').press('Enter')
        self.assertEqual(prototype.locator('code').text_content(),
                         'void mixed(\n    const char *label,\n    mode_t mode,\n    enum peer_mode peer\n);')
        self.assertEqual(page.locator('.tl-enum-arg .tl-enum summary .tl-code-symbol').all_text_contents(),
                         ['PEER_FAST', 'TX_FAST'])
        self.assertEqual(page.locator('.tl-enum-arg .tl-code-string').text_content(), '"test, mode=7"')
        page.evaluate("fgView('timeline').probe().select(4)")
        self.assertEqual(page.locator('.tl-enum-arg .tl-enum summary .tl-code-symbol').all_text_contents(), ['PEER_FAST'])
        self.assertIn('mode=7', page.locator('.tl-enum-arg').first.text_content())
        self.assert_large_alias_group(page)
        self.assertEqual(errors, [])

    def assert_large_alias_group(self, page):
        page.evaluate("fgView('timeline').probe().select(5)")
        argument = page.locator('.tl-enum-arg .tl-enum')
        summary = argument.locator('summary')
        self.assertIn('33 names', summary.inner_text())
        self.assertEqual(argument.locator('li').count(), 0)
        summary.press('Space')
        toggle = argument.get_by_role('button', name='Browse all 33 names')
        toggle.wait_for(state='visible')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        self.assertEqual(argument.locator('li > .tl-code-symbol').all_text_contents(), LARGE_ALIAS_GROUP[:8])
        self.assertGreaterEqual(toggle.bounding_box()['height'], 44)
        self.assertIn('Recorded: 1', argument.inner_text())
        body = argument.locator('.tl-enum-body')
        preview_height = body.evaluate('node => node.offsetHeight')
        page.keyboard.press('Tab')
        self.assertTrue(toggle.evaluate('node => node === document.activeElement'))
        page.keyboard.press('Enter')
        toggle = argument.get_by_role('button', name='Show fewer names')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        name_filter = argument.get_by_role('searchbox', name='Filter alias names')
        self.assertTrue(name_filter.evaluate('node => node === document.activeElement'))
        self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 5)
        aliases = argument.locator('li > .tl-code-symbol')
        self.assertEqual(aliases.all_text_contents(), LARGE_ALIAS_GROUP[:24])
        self.assertLess(preview_height, body.evaluate('node => node.offsetHeight'))
        argument.get_by_role('button', name='Next', exact=True).press('Enter')
        self.assertEqual(aliases.all_text_contents(), LARGE_ALIAS_GROUP[24:])
        self.assert_refresh_preserves_evidence(page, toggle, LARGE_ALIAS_GROUP[24:])
        name_filter.fill('_32_')
        self.assertEqual(aliases.all_text_contents(), LARGE_ALIAS_GROUP[-1:])
        name_filter.fill('')
        self.assertEqual(aliases.all_text_contents(), LARGE_ALIAS_GROUP[:24])
        viewport = page.viewport_size
        for width in (320, 390, 640, 1280):
            with self.subTest(enum_width=width):
                page.set_viewport_size({'width': width, 'height': viewport['height']})
                self.assert_enum_body_geometry(argument, wide_body=width <= 640)
                self.assertEqual(argument.locator('li').count(), 24)
        page.set_viewport_size(viewport)
        toggle.press('Space')
        self.assertEqual(argument.locator('li > .tl-code-symbol').all_text_contents(), LARGE_ALIAS_GROUP[:8])
        self.assertTrue(argument.locator('[data-enum-show-all]').evaluate('node => node === document.activeElement'))
        summary.press('Space')
        self.assertFalse(argument.evaluate('node => node.open'))
        summary.press('Space')
        self.assertEqual(argument.locator('ul').count(), 1)
        self.assertEqual(argument.locator('li > .tl-code-symbol').all_text_contents(), LARGE_ALIAS_GROUP[:8])
        argument.get_by_role('button', name='Browse all 33 names').press('Space')
        self.assertEqual(aliases.all_text_contents(), LARGE_ALIAS_GROUP[:24])
        page.evaluate("fgView('timeline').probe().select(0); fgView('timeline').probe().select(5)")
        self.assertFalse(argument.evaluate('node => node.open'))
        self.assertEqual(argument.locator('li').count(), 0)

    def assert_refresh_preserves_evidence(self, page, toggle, expected_aliases):
        pane = page.locator('.tl-detail')
        facts = pane.locator('.tl-call-facts')
        duration = facts.locator('.v').first
        before = duration.inner_text()
        for selector in ('.tl-btf-prototype', '.tl-arguments > details', '.tl-return-evidence'):
            facts.locator(selector + ' > summary').press('Enter')
        facts.locator('.tl-btf-prototype code').wait_for(state='visible')
        toggle.focus()
        toggle.evaluate('node => { window.activeEvidenceControl = node; }')
        snapshot = '''pane => ({scroll: pane.scrollTop, focus: pane.contains(document.activeElement),
            evidence: Array.from(pane.querySelectorAll('.tl-call-facts details[open]'), node => node.textContent),
            selected: fgView('timeline').probe().sel()})'''
        original = pane.evaluate(snapshot)
        self.assertGreater(original['scroll'], 0)
        self.assertEqual(len(original['evidence']), 4)
        page.keyboard.press('o')
        self.assertIn('subtracted', page.locator('[data-act=adj]').text_content())
        self.assertNotEqual(duration.inner_text(), before)
        self.assertTrue(toggle.evaluate('node => node === document.activeElement && node === window.activeEvidenceControl'))
        self.assertEqual(pane.evaluate(snapshot), original)
        self.assertEqual(facts.locator('.tl-enum-arg li > .tl-code-symbol').all_text_contents(), expected_aliases)
        page.keyboard.press('o')
        self.assertEqual(duration.inner_text(), before)
        self.assertEqual(pane.evaluate(snapshot), original)
        page.evaluate('window.fgOnFilter()')
        self.assertEqual(pane.evaluate(snapshot), original)

    def assert_filtered_label_notice(self, page, argument, locate, excluded, names, outside_detail):
        name_filter = argument.get_by_role('searchbox', name='Filter alias names')
        for width, height in ((320, 770), (844, 240)):
            with self.subTest(notice_viewport=(width, height)):
                page.set_viewport_size({'width': width, 'height': height})
                page.wait_for_function('wide => Boolean(document.querySelector(".tl-landscape-details")) === wide',
                                       arg=width > height)
                name_filter.fill('_ODD')
                locate.focus()
                locate.scroll_into_view_if_needed()
                page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
                before = locate.evaluate('''button => {
                    const pane = button.closest('.tl-detail'), rect = pane.getBoundingClientRect();
                    const bottom = Math.min(innerHeight, rect.top + pane.clientTop + pane.clientHeight);
                    pane.scrollTop += button.getBoundingClientRect().bottom - bottom + 4;
                    const bounds = button.getBoundingClientRect();
                    return {detail:pane.scrollTop, room:bottom - bounds.bottom,
                        x:bounds.left + bounds.width/2, y:bounds.top + bounds.height/2,
                        hit:button.contains(document.elementFromPoint(bounds.left + bounds.width/2, bounds.top + bounds.height/2))};
                }''')
                self.assertTrue(before['hit'], before)
                self.assertAlmostEqual(before['room'], 4, delta=1)
                state = page.evaluate(outside_detail)
                if width == 320:
                    page.keyboard.press('Space')
                else:
                    page.touchscreen.tap(before['x'], before['y'])
                excluded.wait_for()
                geometry = excluded.evaluate('''notice => {
                    const pane = notice.closest('.tl-detail'), rect = pane.getBoundingClientRect();
                    const bottom = Math.min(innerHeight, rect.top + pane.clientTop + pane.clientHeight);
                    const bounds = notice.getBoundingClientRect(), center = (bounds.left + bounds.right) / 2;
                    return {detail:pane.scrollTop, bottom, noticeBottom:bounds.bottom,
                        visibleTop:notice.contains(document.elementFromPoint(center, bounds.top + 1)),
                        visibleBottom:notice.contains(document.elementFromPoint(center, bounds.bottom - 1))};
                }''')
                self.assertLessEqual(geometry['noticeBottom'], geometry['bottom'], geometry)
                self.assertTrue(geometry['visibleTop'] and geometry['visibleBottom'], geometry)
                self.assertGreater(geometry['detail'], before['detail'])
                self.assertTrue(locate.evaluate('button => button === document.activeElement'))
                self.assertEqual(page.evaluate(outside_detail), state)
                self.assertEqual(name_filter.input_value(), '_ODD')
                self.assertEqual(argument.locator('li > .tl-code-symbol').all_text_contents(), names[1::2][:24])
                page.keyboard.press('Space')
                self.assertEqual(excluded.evaluate('notice => notice.closest(".tl-detail").scrollTop'), geometry['detail'])
                name_filter.fill('')
                self.assertFalse(excluded.is_visible())
        page.set_viewport_size({'width': 320, 'height': 770})

    def assert_alias_footer_navigation(self, page, argument, names):
        footer = argument.locator('.tl-enum-page-bottom')
        position = footer.locator('.tl-enum-page-position')
        previous = footer.get_by_role('button', name='Previous alias page')
        next_page = footer.get_by_role('button', name='Next alias page')
        rows = argument.locator('li')
        displayed_names = rows.locator('.tl-code-symbol')
        pane = page.locator('.tl-detail')
        name_filter = argument.get_by_role('searchbox', name='Filter alias names')
        self.assertEqual(footer.count(), 1)
        self.assertEqual(position.inner_text(), '1–24 / 73')
        self.assertEqual(position.get_attribute('aria-hidden'), 'true')
        self.assertTrue(previous.is_disabled())
        self.assertGreaterEqual(next_page.bounding_box()['height'], 44)
        next_page.focus()
        page.wait_for_function('document.querySelector(".tl-detail").scrollTop > 0')
        self.assertGreater(next_page.bounding_box()['y'], rows.last.bounding_box()['y'])
        before = page.evaluate('[scrollX, scrollY]')
        next_page.press('Enter')
        self.assertEqual(displayed_names.all_text_contents(), names[24:48])
        self.assertEqual(position.inner_text(), '25–48 / 73')
        self.assertTrue(rows.first.evaluate('row => row === document.activeElement'))
        visible = rows.first.evaluate('''row => {
            const bounds = row.getBoundingClientRect();
            const detail = row.closest('.tl-detail').getBoundingClientRect();
            return bounds.top >= detail.top && bounds.bottom <= detail.bottom;
        }''')
        self.assertTrue(visible)
        self.assertEqual(page.evaluate('[scrollX, scrollY]'), before)
        self.assertLess(pane.evaluate('node => node.scrollTop'), next_page.evaluate('''button =>
            button.getBoundingClientRect().top - button.closest('.tl-detail').getBoundingClientRect().top +
            button.closest('.tl-detail').scrollTop'''))

        name_filter.fill('_ODD')
        next_page.tap()
        self.assertEqual(displayed_names.all_text_contents(), names[1::2][24:])
        self.assertEqual(position.inner_text(), '25–36 / 36')
        self.assertTrue(next_page.is_disabled())
        self.assertTrue(rows.first.evaluate('row => row === document.activeElement'))
        previous.press('Space')
        self.assertEqual(displayed_names.all_text_contents(), names[1::2][:24])
        self.assertTrue(previous.is_disabled())
        self.assertEqual(name_filter.input_value(), '_ODD')
        name_filter.fill('NAME_072')
        self.assertFalse(footer.is_visible())
        self.assertEqual(displayed_names.all_text_contents(), names[-1:])
        name_filter.fill('')
        self.assertTrue(footer.is_visible())
        self.assertEqual(position.inner_text(), '1–24 / 73')

    def assert_aliases_adapt_to_the_landscape_split(self, page, argument, names):
        name_filter = argument.get_by_role('searchbox', name='Filter alias names')
        name_filter.fill('_EVEN')
        status = argument.locator('.tl-enum-page-status')
        expected_status = 'Names 1–24 of 37 matches (73 names total)'
        raw = page.locator('.tl-return-evidence')
        raw.locator('summary').press('Space')
        page.set_viewport_size({'width': 844, 'height': 390})
        divider = page.get_by_role('separator', name='Resize details')
        page.locator('.tl-landscape-details').wait_for()
        for key in ('Home', 'End'):
            with self.subTest(side_pane_limit=key):
                divider.press(key)
                self.assert_enum_body_geometry(argument, wide_body=True)
                self.assert_enum_body_geometry(raw, wide_body=True, body_selector='.tl-value-evidence-body')
                self.assertEqual(status.inner_text(), expected_status)
                self.assertEqual(argument.locator('li > .tl-code-symbol').all_text_contents(), names[::2][:24])

        expand = page.get_by_role('button', name='Expand details into the chart area')
        expand.press('Enter')
        self.assert_enum_body_geometry(argument, wide_body=False)
        self.assert_enum_body_geometry(raw, wide_body=False, body_selector='.tl-value-evidence-body')
        self.assertEqual(name_filter.input_value(), '_EVEN')
        restore = page.get_by_role('button', name='Restore chart and detail split')
        restore.press('Enter')
        self.assert_enum_body_geometry(argument, wide_body=True)
        self.assertEqual(status.inner_text(), expected_status)
        self.assertTrue(raw.evaluate('node => node.open'))
        self.assertFalse(page.locator('.tl-btf-prototype').evaluate('node => node.open'))
        self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 0)

    def assert_alias_browse_reveals_filter(self, page, argument, browse):
        original_size = page.viewport_size
        state = '''() => [scrollX, scrollY, document.querySelector('.tl-scroll').scrollTop,
            fgView('timeline').probe().sel(), ...fgView('timeline').probe().view()]'''
        try:
            for width, height, keyboard, short in ((320, 770, False, False), (844, 390, True, False),
                                                    (280, 390, False, True), (320, 480, True, True),
                                                    (568, 320, False, True)):
                with self.subTest(width=width, height=height):
                    page.set_viewport_size({'width': width, 'height': height})
                    page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
                    placement = browse.evaluate('''node => {
                        node.scrollIntoView({block:'end'});
                        const pane = node.closest('.tl-detail'), bounds = pane.getBoundingClientRect();
                        const bottom = Math.min(innerHeight, bounds.top + pane.clientTop + pane.clientHeight);
                        pane.scrollTop += node.getBoundingClientRect().bottom - bottom + 4;
                        const button = node.getBoundingClientRect();
                        const left = (button.left + button.right) / 2, top = (button.top + button.bottom) / 2;
                        return {left, top, scroll: pane.scrollTop, hit: node.contains(document.elementFromPoint(left, top))};
                    }''')
                    self.assertTrue(placement['hit'], placement)
                    before = page.evaluate(state)
                    if keyboard:
                        browse.evaluate('node => node.focus({preventScroll:true})')
                        page.keyboard.press('Enter')
                    else:
                        page.touchscreen.tap(placement['left'], placement['top'])
                    field = argument.get_by_role('searchbox', name='Filter alias names')
                    observed = field.evaluate('''(input, keyboardToFilter) => {
                        const pane = input.closest('.tl-detail');
                        const button = input.closest('.tl-enum-body').querySelector('[data-enum-show-all]');
                        const hit = node => {
                            const bounds = node.getBoundingClientRect(), left = (bounds.left + bounds.right) / 2;
                            return [bounds.top + 2, bounds.bottom - 2].every(top => node.contains(document.elementFromPoint(left, top)));
                        };
                        return {filter:hit(input), button:hit(button),
                            focused:(keyboardToFilter ? input : button) === document.activeElement,
                            scroll:pane.scrollTop};
                    }''', keyboard)
                    after = page.evaluate(state)
                    count = argument.locator('li').count()
                    collapse = argument.get_by_role('button', name='Show fewer names')
                    if short and observed['filter']:
                        if not keyboard:
                            bounds = field.bounding_box()
                            page.touchscreen.tap(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
                        self.assertTrue(field.evaluate('input => input === document.activeElement'))
                        page.keyboard.type('NAME_072')
                        self.assertEqual(argument.locator('li > .tl-code-symbol').count(), 1)
                    stable = collapse.evaluate('''(button, testStable) => {
                        const pane = button.closest('.tl-detail');
                        button.click();
                        if (!testStable) return true;
                        const before = pane.scrollTop;
                        button.click();
                        const stays = pane.scrollTop === before;
                        button.click();
                        return stays;
                    }''', not short)
                    self.assertGreater(observed.pop('scroll'), placement['scroll'], observed)
                    self.assertTrue(observed['filter'] and observed['focused'], observed)
                    if not short:
                        self.assertTrue(observed['button'], observed)
                    self.assertEqual(after, before)
                    self.assertEqual(count, 24)
                    self.assertTrue(stable)
        finally:
            page.set_viewport_size(original_size)

    def assert_alias_filter_reveals_feedback(self, page, argument, names):
        original_size = page.viewport_size
        search = argument.get_by_role('searchbox', name='Filter alias names')
        clear = argument.get_by_role('button', name='Clear alias filter')
        status = argument.locator('.tl-enum-page-status')
        row = argument.locator('li').first
        hit = '''node => {
            const box = node.getBoundingClientRect();
            return node.contains(document.elementFromPoint((box.left + box.right) / 2, (box.top + box.bottom) / 2));
        }'''
        bottom_align = '''node => {
            const pane = node.closest('.tl-detail');
            const bottom = Math.min(innerHeight, pane.getBoundingClientRect().top + pane.clientTop + pane.clientHeight);
            pane.scrollTop += node.getBoundingClientRect().bottom - bottom + 4;
        }'''
        view = '''() => [scrollX, scrollY, document.querySelector('.tl-scroll').scrollTop,
            fgView('timeline').probe().sel(), ...fgView('timeline').probe().view()]'''
        try:
            for width, height in ((280, 390), (568, 320), (844, 390)):
                with self.subTest(filter_feedback=(width, height)):
                    page.set_viewport_size({'width': width, 'height': height})
                    page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
                    collapse = argument.get_by_role('button', name='Show fewer names')
                    if collapse.count():
                        collapse.evaluate('button => button.click()')
                    argument.get_by_role('button', name='Browse all 73 names').tap()
                    search.tap()
                    search.evaluate(bottom_align)
                    self.assertTrue(search.evaluate(hit))
                    self.assertTrue(clear.is_disabled())
                    before = page.evaluate(view)
                    page.keyboard.type('NAME_072')
                    page.wait_for_function('() => (' + hit + ')(document.querySelector(".tl-enum-arg .tl-enum-page-status"))')
                    self.assertEqual(row.locator('.tl-code-symbol').inner_text(), names[-1])
                    self.assertTrue(search.evaluate('input => input === document.activeElement'))
                    self.assertTrue(search.evaluate(hit))
                    self.assertTrue(status.evaluate(hit), search.evaluate('''input => {
                        const pane = input.closest('.tl-detail'), status = input.parentElement.nextElementSibling;
                        return {input: input.getBoundingClientRect().toJSON(), status: status.getBoundingClientRect().toJSON(),
                            pane: pane.getBoundingClientRect().toJSON(), scroll: [scrollY, pane.scrollTop], text: status.textContent};
                    }'''))
                    if width == 844:
                        self.assertTrue(row.locator('.tl-code-symbol').evaluate('''name => {
                            const text = document.createRange();
                            text.selectNodeContents(name);
                            const line = text.getClientRects()[0];
                            return name.contains(document.elementFromPoint((line.left + line.right) / 2,
                                (line.top + line.bottom) / 2));
                        }'''))
                    page.keyboard.press('Enter')
                    self.assertTrue(row.evaluate('name => name === document.activeElement'))
                    self.assertTrue(row.evaluate(hit))
                    self.assertEqual(page.evaluate(view), before)
                    search.fill('no match here')
                    self.assertEqual(status.inner_text(), 'No matching names')
                    page.wait_for_function('() => (' + hit + ')(document.querySelector(".tl-enum-arg .tl-enum-page-status"))')
                    self.assertTrue(status.evaluate(hit))
                    page.keyboard.press('Enter')
                    self.assertTrue(search.evaluate('input => input === document.activeElement'))
                    self.assertTrue(clear.evaluate(hit))
                    self.assertGreaterEqual(clear.bounding_box()['width'], 44)
                    self.assertGreaterEqual(clear.bounding_box()['height'], 44)
                    clear.tap()
                    self.assertEqual(search.input_value(), '')
                    self.assertTrue(search.evaluate('input => input === document.activeElement'))
                    self.assertTrue(clear.is_disabled())
                    self.assertEqual(argument.locator('li > .tl-code-symbol').all_text_contents(), names[:24])
                    self.assertEqual(page.evaluate(view), before)
                    search.fill('NAME_072')
                    search.press('Escape')
                    self.assertEqual(search.input_value(), '')
                    self.assertTrue(search.evaluate('input => input === document.activeElement'))
                    self.assertEqual(argument.locator('li > .tl-code-symbol').all_text_contents(), names[:24])
                    next_page = argument.get_by_role('button', name='Next', exact=True)
                    next_page.scroll_into_view_if_needed()
                    next_page.evaluate(bottom_align)
                    self.assertTrue(next_page.evaluate(hit))
                    before = page.evaluate(view)
                    next_page.tap()
                    self.assertEqual(row.locator('.tl-code-symbol').inner_text(), names[24])
                    self.assertTrue(row.evaluate(hit))
                    if width < 844:
                        self.assertTrue(row.evaluate('name => name === document.activeElement'))
                    self.assertEqual(page.evaluate(view), before)
        finally:
            collapse = argument.get_by_role('button', name='Show fewer names')
            if collapse.count():
                collapse.evaluate('button => button.click()')
            page.set_viewport_size(original_size)

    def assert_alias_feedback_coalesces(self, page, argument, browse, names):
        browse.press('Enter')
        search = argument.get_by_role('searchbox', name='Filter alias names')
        search.focus()
        observed = search.evaluate('''async input => {
            const nativeRects = Range.prototype.getClientRects;
            const nextFrame = () => new Promise(requestAnimationFrame);
            const pane = input.closest('.tl-detail');
            const type = value => {
                input.value = value;
                input.dispatchEvent(new InputEvent('input', {bubbles:true}));
            };
            let reads = 0;
            Range.prototype.getClientRects = function() {
                if (this.commonAncestorContainer.closest?.('.tl-enum-body li')) reads++;
                return nativeRects.call(this);
            };
            try {
                for (const query of ['_ODD', '_EVEN', 'NAME_071', 'NAME_072']) type(query);
                const duringInput = reads;
                await nextFrame();
                const atFrame = reads;
                const name = pane.querySelector('.tl-enum-arg li > .tl-code-symbol').textContent;
                type('NAME_071');
                input.blur();
                const scrollBefore = pane.scrollTop;
                await nextFrame();
                return {duringInput, atFrame, afterBlur:reads, name, scrollStayed:pane.scrollTop === scrollBefore};
            } finally { Range.prototype.getClientRects = nativeRects; }
        }''')
        self.assertEqual(observed, {'duringInput': 0, 'atFrame': 1, 'afterBlur': 1,
                                    'name': names[-1], 'scrollStayed': True})
        argument.get_by_role('button', name='Show fewer names').press('Enter')

    def assert_alias_footer_avoids_sticky_tabs(self, page, argument, names):
        page.set_viewport_size({'width': 844, 'height': 240})
        page.locator('.tl-landscape-details').wait_for()
        argument.get_by_role('searchbox', name='Filter alias names').fill('')
        next_page = argument.get_by_role('button', name='Next alias page')
        before = next_page.evaluate('''button => {
            const pane = button.closest('.tl-detail'), tabs = document.querySelector('.view-tabs');
            const scrollRoom = document.createElement('div');
            scrollRoom.style.height = '100vh';
            pane.closest('.container').append(scrollRoom);
            scrollTo(0, scrollY + pane.getBoundingClientRect().top - 24);
            const bounds = pane.getBoundingClientRect(), tabsBottom = tabs.getBoundingClientRect().bottom;
            const center = (tabsBottom + Math.min(innerHeight, bounds.bottom)) / 2;
            const control = button.getBoundingClientRect();
            pane.scrollTop += control.top + control.height / 2 - center;
            const placed = button.getBoundingClientRect();
            const hit = button.contains(document.elementFromPoint(placed.left + placed.width / 2, placed.top + placed.height / 2));
            return {scroll:scrollY, paneTop:bounds.top, tabsBottom, hit};
        }''')
        self.assertTrue(before['hit'], before)
        self.assertLess(before['paneTop'], before['tabsBottom'], before)
        next_page.tap()
        self.assertEqual(argument.locator('li > .tl-code-symbol').all_text_contents(), names[24:48])
        state = argument.locator('li').first.evaluate('''row => {
            const box = row.getBoundingClientRect();
            const target = document.elementFromPoint((box.left + box.right)/2, (box.top + box.bottom)/2);
            return {focused:document.activeElement === row, reached:row.contains(target), scroll:scrollY};
        }''')
        self.assertEqual(state, {'focused': True, 'reached': True, 'scroll': before['scroll']})

    def assert_compact_enum_summary(self, summary, number, badges):
        self.assertEqual(summary.locator('.tl-enum-number').inner_text(), number)
        self.assertEqual(summary.locator('.tl-enum-status').all_inner_texts(), badges)
        geometry = summary.evaluate('''summary => {
            const bounds = summary.getBoundingClientRect();
            const parts = summary.querySelectorAll('.tl-enum-number, .tl-enum-status');
            const range = document.createRange();
            return Array.from(parts, part => {
                range.selectNodeContents(part);
                const lines = Array.from(range.getClientRects(), rect => rect.top);
                const rect = part.getBoundingClientRect();
                return {text: part.textContent, oneLine: new Set(lines).size === 1,
                    fits: rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1};
            });
        }''')
        self.assertTrue(all(part['oneLine'] and part['fits'] for part in geometry), geometry)

    def assert_enum_body_geometry(self, disclosure, wide_body, body_selector='.tl-enum-body'):
        geometry = disclosure.evaluate('''(node, selector) => {
            const body = node.querySelector(selector);
            const fields = node.closest('.tl-call-facts').getBoundingClientRect();
            return {left: body.getBoundingClientRect().left, right: body.getBoundingClientRect().right,
                summaryLeft: node.querySelector('summary').getBoundingClientRect().left,
                valueLeft: node.closest('.v').getBoundingClientRect().left,
                fieldsLeft: fields.left, fieldsRight: fields.right, overflow: body.scrollWidth - body.clientWidth};
        }''', body_selector)
        self.assertAlmostEqual(geometry['summaryLeft'], geometry['valueLeft'], delta=1)
        self.assertAlmostEqual(geometry['left'], geometry['fieldsLeft' if wide_body else 'valueLeft'], delta=1)
        self.assertAlmostEqual(geometry['right'], geometry['fieldsRight'], delta=1)
        self.assertLessEqual(geometry['overflow'], 1)

    def assert_compact_enum_layout(self, page, raw, tag):
        args = page.locator('.tl-call-facts .tl-arguments')
        fields = args.locator('.tl-enum-arg')
        positions = fields.evaluate_all('nodes => nodes.map(node => node.getBoundingClientRect().top)')
        self.assertEqual(len(positions), 6)
        self.assertAlmostEqual(positions[0], positions[1], delta=1)
        self.assertAlmostEqual(positions[3], positions[4], delta=1)
        self.assertAlmostEqual(positions[4], positions[5], delta=1)
        self.assertLess(positions[0], positions[2])
        self.assertLess(positions[2], positions[3])
        self.assertEqual(fields.all_inner_texts(), ['cpu=1,', 'flags=2', 'mode=FAST (1) first of 2 names',
                                                    tag + ',', 'r=0,', 'n=1'])
        self.assertTrue(args.evaluate('node => node.scrollWidth <= node.clientWidth + 1'))
        enum_summary = args.locator('.tl-enum > summary')
        enum_summary.press('Space')
        args.locator('.tl-enum li').last.wait_for(state='visible')
        self.assertEqual(args.locator('.tl-enum li > .tl-code-symbol').all_inner_texts(), ['FAST', 'QUICK'])
        evidence = args.locator(':scope > details.tl-context-note')
        evidence.locator('summary').press('Enter')
        self.assertEqual(evidence.locator('.tl-code-raw').inner_text(), raw)

    def assert_native_enum_formats(self, page):
        page.evaluate("fgView('timeline').probe().select(7)")
        args = page.locator('.tl-call-facts .tl-arguments')
        enum_summary = args.locator('.tl-enum > summary')
        self.assertEqual(enum_summary.count(), 1)
        self.assertIn(f'TX_FAST ({WIDE_VALUE})', enum_summary.inner_text())
        self.assertIn('...', args.inner_text())
        enum_summary.press('Enter')
        self.assertEqual(args.locator('.tl-enum .tl-code-raw').inner_text(), f'(ENUM64)[{hex(WIDE_VALUE)}]')
        evidence = args.locator(':scope > details.tl-context-note')
        evidence.locator('summary').press('Enter')
        self.assertEqual(evidence.locator('.tl-code-raw').inner_text(), FTRACE_ENUM64_ARGS)
        page.evaluate("fgView('timeline').probe().select(8)")
        self.assertEqual(enum_summary.count(), 1)
        self.assertIn('PEER_FAST', enum_summary.inner_text())
        self.assertIn('mode=(ENUM64)[0x25:...]', args.inner_text())
        page.evaluate("fgView('timeline').probe().select(9)")
        self.assertEqual(enum_summary.count(), 0)
        self.assertIn('mode=(ENUM64)[0x1]', args.inner_text())
        page.evaluate("fgView('timeline').probe().select(10)")
        self.assertIn('mode=FAST (1)', enum_summary.inner_text())
        self.assertIn('...', args.inner_text())
        page.evaluate("fgView('timeline').probe().select(11)")
        self.assertEqual(enum_summary.count(), 0)
        self.assertIn('mode=1, ..., n=1', args.inner_text())

    def assert_recorded_enum_labels(self, page):
        select = "index => fgView('timeline').probe().select(index)"
        args = page.locator('.tl-call-facts .tl-arguments')
        disclosure = args.locator('.tl-enum')
        summary = disclosure.locator('summary')
        page.evaluate(select, 12)
        self.assertEqual(disclosure.count(), 1)
        self.assertIn('mode=QUICK (1)', summary.inner_text())
        self.assert_compact_enum_summary(summary, '(1)', ['2 names', 'Recorded label'])
        self.assertIn('flags=1 [QUICK]', args.inner_text())
        self.assertIn('...', args.inner_text())
        self.assertEqual(disclosure.locator('li').count(), 0)
        summary.press('Enter')
        disclosure.locator('li').first.wait_for()
        promoted = disclosure.locator('li[aria-current="true"]')
        self.assertEqual(promoted.locator('.tl-code-symbol').inner_text(), 'QUICK')
        self.assertEqual(promoted.locator('.tl-enum-name-basis').inner_text(), 'Recorded label')
        self.assertEqual(disclosure.locator('li > .tl-code-symbol').all_text_contents(), ['FAST', 'QUICK'])
        self.assertIn('Recorded: 0x1 [QUICK]', disclosure.inner_text())
        self.assertNotIn('BTF first match', disclosure.inner_text())
        evidence = args.locator(':scope > details.tl-context-note')
        evidence.locator('summary').press('Enter')
        self.assertEqual(evidence.locator('.tl-code-raw').inner_text(), FTRACE_LABELLED_ARGS)

        for index, label, number, badge, raw in ((13, 'mode=FAST (1)', '(1)', 'first of 2 names', '1 [SLOW]'),
                                                 (14, 'mode=7', '7', 'No BTF match', '7 [QUICK]')):
            page.evaluate(select, index)
            self.assertIn(label, summary.inner_text())
            self.assert_compact_enum_summary(summary, number, [badge, 'Label mismatch'])
            summary.press('Enter')
            self.assertIn('Recorded: ' + raw, disclosure.inner_text())
            self.assertIn('Recorded label does not match this BTF value', disclosure.inner_text())
        page.evaluate(select, 15)
        self.assertEqual(disclosure.count(), 0)
        self.assertIn('mode=1 [QUICK], mode=2 [SLOW]', args.inner_text())
