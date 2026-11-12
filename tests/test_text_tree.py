from html.parser import HTMLParser
import re
import unittest

import helpers
from fglib.highlight import highlight_ftrace_line
from fglib.report import HtmlGenerator


class Markup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.text = []
        self.tags = []
        self.feed(markup)

    def handle_starttag(self, tag, attributes):
        self.tags.append((tag, dict(attributes)))

    def handle_data(self, text):
        self.text.append(text)


class TextTreeMarkupTest(unittest.TestCase):
    def test_adjacent_nonstructural_punctuation_shares_one_style_span(self):
        for punctuation in ['()', '();', '[]', '[[]]();', ';(())', '())];;']:
            with self.subTest(punctuation=punctuation):
                result = highlight_ftrace_line(punctuation)
                self.assertEqual(result, '<span class="hl-symbol">' + punctuation + '</span>')
                self.assertEqual(''.join(Markup(result).text), punctuation)

    def test_symbol_runs_keep_structural_braces_separate_and_foldable(self):
        result = highlight_ftrace_line('call(){}[];')
        self.assertEqual(re.findall(r'<span class="hl-symbol">([^<]*)</span>', result),
                         ['()', '{', '}', '[];'])
        entry = HtmlGenerator._fold_line_html(result, 'entry', 'call')
        controls = [attributes for tag, attributes in Markup(entry).tags if tag == 'button']
        self.assertEqual(len(controls), 2)
        self.assertEqual(''.join(Markup(entry).text), 'call(){}[];')
        closing = HtmlGenerator._fold_line_html(highlight_ftrace_line('}[];'), 'exit', 'call')
        self.assertIn('<span class="hl-symbol fold-close">}</span>', closing)

    def test_runs_do_not_join_across_whitespace_functions_values_or_comments(self):
        cases = {
            '( )': ['(', ')'],
            '(\t);': ['(', ');'],
            'outer(inner());': ['(', '());'],
            'leaf(value=0x42);': ['(', ');'],
            'leaf(); /* ()[]{;} */': ['();'],
            '() /* unchanged */ [];': ['()', '[];'],
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                result = highlight_ftrace_line(raw)
                self.assertEqual(re.findall(r'<span class="hl-symbol">([^<]*)</span>', result), expected)
                self.assertEqual(''.join(Markup(result).text), raw)

    def test_compact_symbols_preserve_the_complete_existing_character_style_stream(self):
        class StyledMarkup(HTMLParser):
            def __init__(self, markup):
                super().__init__()
                self.stack = []
                self.runs = []
                self.feed(markup)

            def handle_starttag(self, tag, attributes):
                self.stack.append((tag, tuple(attributes)))

            def handle_endtag(self, tag):
                self.stack.pop()

            def handle_data(self, value):
                self.runs.extend((character, tuple(self.stack)) for character in value)

        cases = [' 3) 1.25 us | outer(inner()); /* caller+0x4/0x80 */',
                 ' 3) | leaf(array[0], ptr=0xf0);',
                 ' 0) | parent() {', ' 0) | } /* parent */',
                 ' 3) | leaf [my_module]();', ' 3) | leaf(); /* 中文🙂 */']
        for raw in cases:
            with self.subTest(raw=raw):
                compact = highlight_ftrace_line(raw)
                expanded = re.sub(r'<span class="hl-symbol">([()\[\];]+)</span>',
                                  lambda match: ''.join('<span class="hl-symbol">' + character + '</span>'
                                                        for character in match[1]), compact)
                self.assertEqual(StyledMarkup(compact).runs, StyledMarkup(expanded).runs)

    def test_large_symbol_run_is_one_text_run_without_dropping_characters(self):
        raw = '([]);' * 20000
        result = highlight_ftrace_line(raw)
        self.assertEqual(len(Markup(result).tags), 1)
        self.assertEqual(''.join(Markup(result).text), raw)
        self.assertEqual(len(result), len(raw) + len('<span class="hl-symbol"></span>'))

    def test_preserves_trace_columns_spaces_and_symbols(self):
        cases = [
            ('entry', ' 3)               |  parent() { /* caller+0x4/0x8 */'),
            ('inner', ' 3)   0.5 us      |      leaf(value=0x42);'),
            ('exit', ' 3) + 25.0 us     |  } /* parent ret=-22 */'),
            ('entry', ' 3)               |\t  parent() {'),
            ('entry', '    parent() {'),
            ('entry', ' 3)               |  parent(value="x|y") {'),
        ]
        for kind, raw in cases:
            with self.subTest(kind=kind, raw=raw):
                before = highlight_ftrace_line(raw)
                after = HtmlGenerator._fold_line_html(before, kind, 'parent')
                self.assertEqual(''.join(Markup(before).text), ''.join(Markup(after).text))
                controls = [attributes for tag, attributes in Markup(after).tags if tag == 'button']
                self.assertEqual(len(controls), 2 if kind == 'entry' else 0)

    def test_native_controls_preserve_links_and_escape_accessible_names(self):
        before = (' 3) |  <a class="func-name-link" href="https://example.org/file|part">parent</a>'
                  '() <span class="hl-symbol">{</span>')
        after = HtmlGenerator._fold_line_html(before, 'entry', 'parent "quoted" <name>')
        buttons = [attributes for tag, attributes in Markup(after).tags if tag == 'button']
        self.assertEqual(len(buttons), 2)
        for attributes in buttons:
            self.assertEqual(attributes['type'], 'button')
            self.assertEqual(attributes['aria-expanded'], 'true')
            self.assertEqual(attributes['aria-label'], 'Collapse call to parent "quoted" <name>')
            self.assertNotIn('onclick', attributes)
            self.assertNotIn('onkeydown', attributes)
        self.assertEqual([attributes['href'] for tag, attributes in Markup(after).tags if tag == 'a'],
                         ['https://example.org/file|part'])
        self.assertEqual(''.join(Markup(before).text), ''.join(Markup(after).text))

    def test_guide_node_count_is_independent_of_depth(self):
        counts = []
        for depth in [1, 10, 1000]:
            before = highlight_ftrace_line(' 3) |' + '  ' * depth + 'parent() {')
            after = HtmlGenerator._fold_line_html(before, 'entry', 'parent')
            counts.append(len(Markup(after).tags))
            self.assertEqual(''.join(Markup(before).text), ''.join(Markup(after).text))
        self.assertEqual(counts, [counts[0]] * 3)

    def test_exit_marks_only_the_real_brace(self):
        before = highlight_ftrace_line(' 3) 1.0 us |  } /* parent { } */')
        after = HtmlGenerator._fold_line_html(before, 'exit', 'parent')
        closings = [attributes for tag, attributes in Markup(after).tags
                    if 'fold-close' in attributes.get('class', '').split()]
        self.assertEqual(len(closings), 1)
        self.assertEqual(''.join(Markup(before).text), ''.join(Markup(after).text))
