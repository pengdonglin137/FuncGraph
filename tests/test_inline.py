"""inline.py: faddr2line inline chains and the timeline inline map."""
import copy
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
STRUCTURED = [
    {'func': 'kunwind_next_frame_record', 'file': 'arch/arm64/kernel/stacktrace.c',
     'line': '227', 'inlined': False},
    {'func': 'kunwind_next', 'file': 'arch/arm64/kernel/stacktrace.c',
     'line': '266', 'inlined': True},
    {'func': 'arch_stack_walk', 'file': 'arch/arm64/kernel/stacktrace.c',
     'line': 390, 'inlined': True},
]


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

    def test_structured_positions_match_text_without_mutating_input(self):
        original = copy.deepcopy(STRUCTURED)
        self.assertEqual(inline.parse_chain(STRUCTURED), inline.parse_chain(PLAIN))
        self.assertEqual(STRUCTURED, original)

    def test_structured_positions_stop_at_unrelated_candidate(self):
        positions = copy.deepcopy(STRUCTURED)
        positions.append({'func': 'unrelated', 'file': 'other.c', 'line': '20', 'inlined': False})
        self.assertEqual(inline.parse_chain(positions), inline.parse_chain(PLAIN))

    def test_paths_with_spaces_colons_and_discriminators_remain_exact(self):
        path = '/build with spaces/version:123/include/helper.c'
        result = inline.parse_chain('helper at ' + path + ':27 (discriminator 4)\n'
                                    ' (inlined by) parent at /other tree/main.c:18\n')
        self.assertEqual(result, [['parent', '/other tree/main.c', 18], ['helper', path, 27]])

    def test_relative_paths_apply_identically_to_structured_and_text_positions(self):
        def relative(path):
            return 'tree/' + path
        self.assertEqual(inline.parse_chain(STRUCTURED, relative), inline.parse_chain(PLAIN, relative))
        self.assertTrue(all(frame[1].startswith('tree/') for frame in inline.parse_chain(STRUCTURED, relative)))

    def test_malformed_structured_positions_do_not_invent_a_chain(self):
        for invalid in [None, 'unparsed', {}, {'func': 'bad', 'file': 'bad.c', 'line': True},
                        {'func': 'bad', 'file': 'bad.c', 'line': '2.5'},
                        {'func': 'bad', 'file': 'bad.c', 'line': 0}]:
            with self.subTest(invalid=invalid):
                positions = copy.deepcopy(STRUCTURED)
                if isinstance(invalid, dict):
                    invalid = dict(invalid, inlined=True)
                positions.insert(1, invalid)
                self.assertEqual(inline.parse_chain(positions), [])

    def test_no_output(self):
        self.assertEqual(inline.parse_chain(None), [])
        self.assertEqual(inline.parse_chain('Error: No output for function x'), [])

    def test_map_only_inlined_sites(self):
        strings = ['arch_stack_walk+0x2d0/0x490', 'f+0x1/0x2']
        m = inline.timeline_inline_map(strings, [0, 1, 0, -1], lambda s: PLAIN if s.startswith('arch') else 'f+0x0/0x2:\nf at x.c:3\n')
        self.assertEqual(list(m), ['0'])
        self.assertEqual(len(m['0']), 3)

    def test_map_accepts_structured_positions_without_additional_lookups(self):
        lookups = []
        def lookup(caller):
            lookups.append(caller)
            return STRUCTURED
        result = inline.timeline_inline_map(['caller'], [0, 0], lookup)
        self.assertEqual(result, {'0': inline.parse_chain(PLAIN)})
        self.assertEqual(lookups, ['caller'])


if __name__ == '__main__':
    unittest.main()
