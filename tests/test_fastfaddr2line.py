"""fastfaddr2line.py: symbol matching (pure) and, optionally, agreement with the kernel's faddr2line."""
import os
import subprocess
import sys
import unittest
from collections import defaultdict

import helpers

sys.path.insert(0, helpers.ROOT)
try:
    import fastfaddr2line as ff
except ImportError as e:      # pyelftools missing
    ff = None
    IMPORT_ERROR = str(e)


def table(entries, sec_end=0x2000):
    """entries: [(name, addr)] in one .text section."""
    sec = ff.Section('.text', 0x1000, sec_end)
    by_name = defaultdict(list)
    for name, addr in sorted(entries, key=lambda x: x[1]):
        s = ff.Symbol(name, addr, 0, '.text')
        sec.symbols.append(s)
        by_name[name].append(s)
    sec.addr_list = [s.addr for s in sec.symbols]
    return by_name, {'.text': sec}


@unittest.skipIf(ff is None, 'fastfaddr2line not importable (pyelftools?)')
class MatchTest(unittest.TestCase):
    def setUp(self):
        # foo: 0x1000-0x1100, foo_alias at same addr, bar.isra.0: 0x1100-0x1180, dup twice with different sizes
        self.by_name, self.secs = table([
            ('foo', 0x1000), ('foo_alias', 0x1000), ('bar.isra.0', 0x1100),
            ('dup', 0x1180), ('mid', 0x11c0), ('dup', 0x1200), ('tail', 0x1300)])

    def find(self, name, off, length, entry_offset=0):
        return ff.find_matching_symbol(name, off, length, self.by_name, self.secs, entry_offset=entry_offset)

    def test_exact_length(self):
        self.assertEqual(self.find('foo', 0x10, 0x100), (0x1000, '.text', 0x1010))

    def test_alias_length_uses_next_distinct_address(self):
        self.assertEqual(self.find('foo_alias', 0x20, 0x100)[2], 0x1020)

    def test_compiler_suffix_fuzzy(self):
        self.assertEqual(self.find('bar', 0x8, 0x80)[2], 0x1108)

    def test_duplicate_names_disambiguated_by_length(self):
        self.assertEqual(self.find('dup', 0x4, 0x40)[0], 0x1180)
        self.assertEqual(self.find('dup', 0x4, 0x100)[0], 0x1200)

    def test_entry_offset_only_at_entry(self):
        self.assertEqual(self.find('foo', 0, 0x100, entry_offset=8)[2], 0x1008)
        self.assertEqual(self.find('foo', 0x10, 0x100, entry_offset=8)[2], 0x1010)

    def test_last_symbol_length_from_section_end(self):
        self.assertEqual(self.find('tail', 0x4, 0xd00)[2], 0x1304)

    def test_unknown(self):
        self.assertIsNone(self.find('nope', 0, 0x10))


@unittest.skipIf(ff is None or not os.environ.get('FG_VMLINUX'), 'set FG_VMLINUX (and FG_KERNEL_SRC) to compare with scripts/faddr2line')
class AgainstKernelTest(unittest.TestCase):
    ADDRS = ['vfs_read+0x10/0x328', 'mutex_unlock+0x20/0x38', 'path_lookupat+0x84/0x180']

    def test_same_file_line_as_kernel_tool(self):
        vml = os.environ['FG_VMLINUX']
        src = os.environ.get('FG_KERNEL_SRC', os.path.dirname(vml))
        kernel_tool = os.path.join(src, 'scripts', 'faddr2line')
        if not os.path.exists(kernel_tool):
            self.skipTest('no scripts/faddr2line')
        for a in self.ADDRS:
            with self.subTest(addr=a):
                fast = subprocess.run([sys.executable, os.path.join(helpers.ROOT, 'fastfaddr2line.py'), vml, a],
                                      capture_output=True, text=True).stdout
                ref = subprocess.run([kernel_tool, vml, a], capture_output=True, text=True).stdout
                last = lambda out: [l.rsplit(' at ', 1)[-1].split('/')[-1].split()[0] for l in out.splitlines() if ' at ' in l][-1:]  # noqa: E731
                self.assertTrue(last(ref), f'kernel faddr2line resolved nothing: {ref!r}')
                self.assertEqual(last(fast), last(ref), f'fast={fast!r} ref={ref!r}')


if __name__ == '__main__':
    unittest.main()
