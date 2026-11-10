from html.parser import HTMLParser
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
