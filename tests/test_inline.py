"""inline.py: faddr2line inline chains and the timeline inline map."""
import unittest

import helpers  # noqa: F401
from fglib import inline

PLAIN = """arch_stack_walk+0x2cf/0x490:
kunwind_next_frame_record at arch/arm64/kernel/stacktrace.c:227
 (inlined by) kunwind_next at arch/arm64/kernel/stacktrace.c:266
 (inlined by) arch_stack_walk at arch/arm64/kernel/stacktrace.c:390
"""
LISTED = """rb_simple_write+0x127/0x170:
class_mutex_destructor at include/linux/mutex.h:228
 227
>228<\tDEFINE_GUARD(mutex, struct mutex *, mutex_lock(_T), mutex_unlock(_T))
 229 at foo:12 is not a position
(inlined by) rb_simple_write at kernel/trace/trace.c:9642
 9641\t\tif (buffer) {
>9642<\t\t\tguard(mutex)(&trace_types_lock);
"""
TWO = """dup+0x10/0x40:
dup at a.c:1
dup+0x10/0x40:
dup at b.c:2
"""


class InlineTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(inline.parse_chain(PLAIN), [
            ['arch_stack_walk', 'arch/arm64/kernel/stacktrace.c', 390],
            ['kunwind_next', 'arch/arm64/kernel/stacktrace.c', 266],
            ['kunwind_next_frame_record', 'arch/arm64/kernel/stacktrace.c', 227]])

    def test_list_mode_ignores_source_lines(self):
        self.assertEqual([f[0] for f in inline.parse_chain(LISTED)], ['rb_simple_write', 'class_mutex_destructor'])

    def test_first_candidate_only(self):
        self.assertEqual(inline.parse_chain(TWO), [['dup', 'a.c', 1]])

    def test_no_output(self):
        self.assertEqual(inline.parse_chain(None), [])
        self.assertEqual(inline.parse_chain('Error: No output for function x'), [])

    def test_map_only_inlined_sites(self):
        strings = ['arch_stack_walk+0x2d0/0x490', 'f+0x1/0x2']
        m = inline.timeline_inline_map(strings, [0, 1, 0, -1], lambda s: PLAIN if s.startswith('arch') else 'f+0x0/0x2:\nf at x.c:3\n')
        self.assertEqual(list(m), ['0'])
        self.assertEqual(len(m['0']), 3)


if __name__ == '__main__':
    unittest.main()
