import hashlib
import io
import json
import unittest
from unittest import mock

from fglib.report import HtmlGenerator
from fglib.textindex import FIELDS, PRESENTATION_FIELDS, TextIndexWriter


class TextIndexTest(unittest.TestCase):
    def test_raw_symbol_index_keeps_display_name_and_exact_compiler_identity_separate(self):
        stream = io.BytesIO()
        writer = TextIndexWriter(stream, include_raw_functions=True)
        writer.append(7, {'func': 'work', 'raw-func': 'work.isra.0'}, 'work();', source=0)
        writer.finish('text-index.json')
        data = json.loads(stream.getvalue())
        self.assertEqual(data['version'], 5)
        self.assertEqual(data['fields'], list(FIELDS + ('space',) + PRESENTATION_FIELDS + ('raw-func',)))
        attributes = dict(zip(data['fields'], data['rows'][0][2:-1]))
        self.assertEqual(attributes['func'], 'work')
        self.assertEqual(attributes['raw-func'], 'work.isra.0')
        self.assertEqual(data['rows'][0][-1], 0)

    def test_presentation_index_retains_exact_duration_zero_return_depth_and_space(self):
        stream = io.BytesIO()
        writer = TextIndexWriter(stream, include_presentation=True)
        writer.append(7, {'space': 'kernel', 'duration': '0', 'sort-duration': '3.125',
                          'fold-ret': '0', 'call-depth': '12'}, 'exit', source=0)
        writer.append(9, {}, 'plain')
        writer.finish('text-index.json')
        data = json.loads(stream.getvalue())
        self.assertEqual(data['version'], 4)
        self.assertEqual(data['fields'], list(FIELDS + ('space',) + PRESENTATION_FIELDS))
        attributes = dict(zip(data['fields'], data['rows'][0][2:-1]))
        self.assertEqual([attributes[field] for field in ['space', 'duration', *PRESENTATION_FIELDS]],
                         ['kernel', '0', '3.125', '0', '12'])
        self.assertEqual(data['rows'][0][-1], 0)
        self.assertEqual(data['rows'][1], [9, 'plain'] + [None] * (len(data['fields']) + 1))

    def test_empty_index_and_exact_utf8_digest(self):
        stream = io.BytesIO()
        writer = TextIndexWriter(stream)
        resource = writer.finish('text-index.json')
        payload = stream.getvalue()
        self.assertEqual(json.loads(payload), dict(version=2, fields=list(FIELDS), rows=[]))
        self.assertEqual(resource, dict(file='text-index.json', bytes=len(payload),
                                       sha256=hashlib.sha256(payload).hexdigest(), format='json'))

    def test_rows_preserve_text_and_normalize_html_parser_characters(self):
        stream = io.BytesIO()
        writer = TextIndexWriter(stream)
        writer.append(2, {'cpu': '0', 'comm': '任务 "&<>\r\nnext\0', 'params': '', 'preempt': '0'},
                      '<span title="&lt;tag&gt;">任务 &amp; &lt; &#13; &#0;</span>\r\n\r\0')
        writer.append(4, {}, '<span></span>')
        resource = writer.finish('text-index.json')
        payload = stream.getvalue()
        rows = json.loads(payload)['rows']
        self.assertEqual(rows[0][:2], [2, '任务 & < \r \ufffd\n\n\ufffd'])
        attributes = dict(zip(FIELDS, rows[0][2:]))
        self.assertEqual(attributes['comm'], '任务 "&<>\nnext\ufffd')
        self.assertEqual(attributes['cpu'], '0')
        self.assertEqual(attributes['preempt'], '0')
        self.assertEqual(attributes['params'], '')
        self.assertIsNone(attributes['ret'])
        self.assertEqual(rows[1], [4, ''] + [None] * (len(FIELDS) + 1))
        self.assertEqual(writer.rows, 2)
        self.assertEqual(resource['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(resource['bytes'], len(payload))

    def test_short_writes_are_not_published_as_valid_indexes(self):
        stream = mock.Mock()
        stream.write.return_value = 0
        with self.assertRaisesRegex(OSError, 'short write'):
            TextIndexWriter(stream)
        stream = io.BytesIO()
        writer = TextIndexWriter(stream)
        with mock.patch.object(writer, 'stream') as broken:
            broken.write.return_value = 0
            with self.assertRaisesRegex(OSError, 'short write'):
                writer.append(1, {}, 'row')
            with self.assertRaisesRegex(OSError, 'short write'):
                writer.finish('text-index.json')

    def test_literal_caller_arrows_are_text_not_html_tags(self):
        stream = io.BytesIO()
        writer = TextIndexWriter(stream)
        writer.append(1, {}, '<span>worker</span> /* <-caller+0x1/0x20 */ <span>ret=0</span>')
        writer.finish('text-index.json')
        self.assertEqual(json.loads(stream.getvalue())['rows'][0][1], 'worker /* <-caller+0x1/0x20 */ ret=0')

    def test_source_ids_include_zero_reuse_and_unexpandable_rows(self):
        stream = io.BytesIO()
        writer = TextIndexWriter(stream)
        for number, source in enumerate([None, 0, 2, 0], 1):
            writer.append(number, {'func': 'worker'}, 'row', source=source)
        writer.finish('text-index.json')
        data = json.loads(stream.getvalue())
        self.assertEqual(data['version'], 2)
        self.assertEqual(data['fields'], list(FIELDS))
        self.assertEqual([row[-1] for row in data['rows']], [None, 0, 2, 0])
        self.assertTrue(all(len(row) == len(FIELDS) + 3 for row in data['rows']))

    def test_invalid_source_ids_fail_before_writing_a_row(self):
        stream = io.BytesIO()
        writer = TextIndexWriter(stream)
        original = stream.getvalue()
        for source in [-1, True, False, '0', 1.5, float('nan'), 2 ** 53]:
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'source'):
                writer.append(1, {}, 'row', source=source)
            self.assertEqual(stream.getvalue(), original)
            self.assertEqual(writer.rows, 0)

    def test_shared_row_attributes_preserve_folds_errno_and_boolean_semantics(self):
        generator = HtmlGenerator.__new__(HtmlGenerator)
        generator.enable_fold = True
        record = dict(cpu=0, pid=42, comm='任务 "&<>', fold_id='fold1', fold_type='exit',
                      call_depth=2, fold_ancestors='outer inner', display_func_name='finish',
                      raw_func_name='finish.isra.0', params='arg="&<>',
                      duration=0, sort_duration=3, duration_raw='+ 0', ret_value='0xfffffffffffffffe',
                      irqs_off='d', need_resched='N', hardirq_softirq='h', preempt_depth=0)
        attributes = generator._text_attributes(record, 'ret=0xfffffffffffffffe', {}, {'fold1': 10})
        self.assertEqual(attributes['fold-start'], 'L10')
        self.assertEqual(attributes['fold-ret'], '0xfffffffffffffffe')
        self.assertEqual(attributes['ret'], '-2')
        self.assertEqual(attributes['comm'], record['comm'])
        self.assertEqual(attributes['params'], record['params'])
        self.assertEqual(attributes['func'], 'finish')
        self.assertEqual(attributes['raw-func'], 'finish.isra.0')
        self.assertEqual(attributes['duration'], '0')
        self.assertEqual(attributes['sort-duration'], '3')
        self.assertEqual(attributes['preempt'], '0')
        self.assertEqual(attributes['fold-ancestors'], 'outer inner')
        record.update(fold_type='entry', ret_value='true')
        attributes = generator._text_attributes(record, '', {'fold1': 20}, {})
        self.assertEqual(attributes['fold-end'], 'L20')
        self.assertEqual(attributes['ret-bool'], 'true')
        self.assertNotIn('ret', attributes)
        generator.enable_fold = False
        attributes = generator._text_attributes(record, '', {}, {})
        self.assertNotIn('fold-id', attributes)


if __name__ == '__main__':
    unittest.main()
