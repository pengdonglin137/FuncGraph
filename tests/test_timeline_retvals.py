"""Native return tokens must survive parsing before type-aware classification."""
import os
from pathlib import Path
import unittest

import helpers
from fglib import btf, raw, timeline
from raw_fixture import container
from test_btf import BtfBuilder, make_elf


def build(value, form='leaf', database=None, separator=' '):
    prefix = '1.000000 |   0)   task-42   |  ...1. |   1.000 us   |  '
    comment = 'ret=' + value + separator + '*/'
    if form == 'leaf':
        body = prefix + 'target(); /* ' + comment
    else:
        body = prefix + '} /* target ' + comment
        if form == 'paired':
            body = '0.999999 |   0)   task-42   |  ...1. |               |  target() {\n' + body
    return timeline.build_timeline(('# tracer: function_graph\n' + body).splitlines(), rettypes=database).to_data()


class NativeReturnValueTest(unittest.TestCase):
    def test_literal_values_are_not_reduced_to_numeric_prefixes(self):
        for value in ['0x0', '0x12345678', '0xffffffffffffffea', '0xffffffea', '0X1234', '0XFFFFFFFFFFFFFFEA', '-22', '4294967295',
                      'true', 'false', '1764235(trunc)', '0x1234(trunc)']:
            with self.subTest(value=value):
                data = build(value)
                self.assertEqual(data['strings'][data['cols']['ret'][0]], value)

    def test_hex_returns_are_preserved_for_leaves_paired_exits_and_orphans(self):
        for form in ['leaf', 'paired', 'orphan']:
            with self.subTest(form=form):
                data = build('0xffffffffffffffea', form=form)
                self.assertEqual(data['strings'][data['cols']['ret'][0]], '0xffffffffffffffea')
                self.assertEqual(data['chains'][0][2], -22)

    def test_malformed_tokens_are_not_accepted_as_valid_prefixes(self):
        for value in ['0x', '0xzz', '0x1oops', 'falsehood', '-22junk', '1.5', '0x1(trunc)junk']:
            with self.subTest(value=value):
                data = build(value)
                self.assertIsNone(data['cols']['ret'])
                self.assertFalse(data['caps']['retval'])
                self.assertEqual(data['chains'], [])

    def test_value_immediately_before_comment_close_is_still_valid(self):
        data = build('0xffffffffffffffea', separator='')
        self.assertEqual(data['strings'][data['cols']['ret'][0]], '0xffffffffffffffea')

    def test_signed_width_and_pointer_error_classification_use_the_whole_value(self):
        for category, width, value, expected in [('sint', 4, '0xffffffea', -22),
                                                  ('sint', 8, '0xffffffffffffffea', -22),
                                                  ('ptr', 8, '0xfffffffffffffffe', -2),
                                                  ('void', 0, '0xffffffffffffffea', None),
                                                  ('uint', 4, '0xffffffea', None),
                                                  ('ptr', 8, '0xffff000000001000', None)]:
            with self.subTest(category=category, width=width, value=value):
                database = btf.ReturnTypeDB()
                database.funcs['target'] = (category, width)
                data = build(value, database=database)
                self.assertEqual([chain[2] for chain in data['chains']], [] if expected is None else [expected])
                self.assertFalse(data['cols']['flags'][0] & timeline.F_NULL_RET)

    def test_only_zero_pointers_receive_the_null_marker(self):
        database = btf.ReturnTypeDB()
        database.funcs['target'] = ('ptr', 8)
        for value, expected in [('0x0', True), ('0xffff000000001000', False)]:
            with self.subTest(value=value):
                data = build(value, database=database)
                self.assertEqual(bool(data['cols']['flags'][0] & timeline.F_NULL_RET), expected)


class NativeRawReturnValueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            raw._helper()
        except raw.RawInputError as error:
            if os.environ.get('FG_RAW_REQUIRED'):
                raise
            raise unittest.SkipTest(str(error))

    def test_raw_bit_patterns_survive_decode_report_and_split_module_btf(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        root = Path(kernel.dir)
        modules = root / 'modules'
        modules.mkdir()
        base = BtfBuilder()
        integer = base.int_('int', 4, signed=True)
        base.func('root', integer)
        Path(kernel.vmlinux).write_bytes(make_elf({'.BTF': base.blob()}))
        for endian, width in [('<', 8), ('>', 4)]:
            source = root / 'trace.dat'
            source.write_bytes(container(endian=endian, width=width, module=True))
            for kind, return_type in [('void', 0), ('signed', integer)]:
                with self.subTest(endian=endian, width=width, kind=kind):
                    module = BtfBuilder(btf.BtfInfo(base.blob()))
                    module.func('child', return_type)
                    (modules / 'demo-mod.ko').write_bytes(make_elf({'.BTF': module.blob()}))
                    report, _ = kernel.run(str(source), '--no-config', '--module-dirs', str(modules), '--timeline')
                    data = helpers.json_script(report, 'tlData')
                    self.assertEqual(data['btf'], 2)
                    child = next(index for index, name in enumerate(data['cols']['name'])
                                 if data['strings'][name] == 'child')
                    returned = data['strings'][data['cols']['ret'][child]]
                    self.assertEqual(returned, hex((1 << (width * 8)) - 2))
                    self.assertEqual([chain[2] for chain in data['chains']], [-2] if kind == 'signed' else [])


if __name__ == '__main__':
    unittest.main()
