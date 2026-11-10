"""Streaming UTF-8 accounting, section extraction and atomic report publication."""
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import helpers
from fglib.output import ReportSpool, SectionRange
from fglib.parser import FtraceParser
from fglib.report import TEXT_CHUNK_ROWS, HtmlGenerator, generate_html, write_html
from fglib.timeline import build_timeline


class ReportSpoolTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fgoutput_')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'report.html'

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.path.parent.glob('.funcgraph-*.tmp')), [])

    def test_utf8_ranges_and_strict_byte_threshold(self):
        parts = [('chrome', '<h>'), ('text', '中文'), ('text', '🙂'), ('chrome', '</h>')]
        expected = '<h>中文🙂</h>'.encode('utf-8')
        with ReportSpool(self.path) as spool:
            spool.write_sections(iter(parts))
            self.assertEqual(spool.byte_count, len(expected))
            self.assertGreater(spool.byte_count, len(expected.decode('utf-8')))
            self.assertEqual(spool.sections, (SectionRange('chrome', 0, 3), SectionRange('text', 3, 13),
                                              SectionRange('chrome', 13, 17)))
            self.assertTrue(spool.exceeds_threshold(len(expected) - 1))
            self.assertFalse(spool.exceeds_threshold(len(expected)))
            self.assertFalse(spool.exceeds_threshold(len(expected) + 1))
            self.assertTrue(spool.exceeds_threshold(0))
            for invalid in [-1, 1.5, True, '17', None]:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    spool.exceeds_threshold(invalid)
            text = io.BytesIO()
            position = spool.file.tell()
            spool.copy_sections('text', text)
            self.assertEqual(text.getvalue().decode('utf-8'), '中文🙂')
            self.assertEqual(spool.file.tell(), position)
            written = spool.publish()
        self.assertEqual(written.byte_count, self.path.stat().st_size)
        self.assertEqual(written.path, str(self.path))
        self.assertEqual(self.path.read_bytes(), expected)
        self.assert_no_temporary_files()

    def test_adjacent_ranges_coalesce_without_retaining_per_line_metadata(self):
        count = 50000
        with ReportSpool(self.path) as spool:
            spool.write_sections(('text', str(index) + '\n') for index in range(count))
            self.assertEqual(len(spool.sections), 1)
            self.assertEqual(spool.sections[0], SectionRange('text', 0, spool.byte_count))
            spool.publish()
        self.assertEqual(len(self.path.read_text().splitlines()), count)

    def test_empty_fragments_do_not_add_ranges_and_empty_reports_publish(self):
        with ReportSpool(self.path) as spool:
            spool.write_sections([('text', ''), ('chrome', '')])
            self.assertEqual(spool.sections, ())
            self.assertFalse(spool.exceeds_threshold(0))
            spool.publish()
        self.assertEqual(self.path.read_bytes(), b'')

    def test_single_pass_and_original_report_remains_until_publication(self):
        self.path.write_bytes(b'previous report')

        def parts():
            yield 'chrome', 'start'
            self.assertEqual(self.path.read_bytes(), b'previous report')
            yield 'text', 'body'
            self.assertEqual(self.path.read_bytes(), b'previous report')

        with ReportSpool(self.path) as spool:
            spool.write_sections(parts())
            self.assertEqual(self.path.read_bytes(), b'previous report')
            with self.assertRaises(RuntimeError):
                spool.write_sections([('text', 'duplicate')])
            spool.publish()
            with self.assertRaises(RuntimeError):
                spool.publish()
        self.assertEqual(self.path.read_bytes(), b'startbody')

    def test_render_failure_preserves_previous_output_and_cleans_partial_file(self):
        self.path.write_bytes(b'previous report')

        def broken():
            yield 'chrome', '<html>'
            raise RuntimeError('render failed')

        with self.assertRaisesRegex(RuntimeError, 'render failed'):
            with ReportSpool(self.path) as spool:
                spool.write_sections(broken())
        self.assertEqual(self.path.read_bytes(), b'previous report')
        self.assert_no_temporary_files()

    def test_unfinished_reports_cannot_publish_or_claim_a_final_size(self):
        with ReportSpool(self.path) as spool:
            with self.assertRaises(RuntimeError):
                spool.exceeds_threshold(0)
            with self.assertRaises(RuntimeError):
                spool.publish()
        self.assertFalse(self.path.exists())
        self.assert_no_temporary_files()

    def test_fsync_or_replace_failure_preserves_previous_output(self):
        for operation in ['fsync', 'replace']:
            with self.subTest(operation=operation):
                self.path.write_bytes(b'previous report')
                with self.assertRaisesRegex(OSError, 'publish failed'):
                    with ReportSpool(self.path) as spool:
                        spool.write_sections([('chrome', 'new report')])
                        with patch('fglib.output.os.' + operation, side_effect=OSError('publish failed')):
                            spool.publish()
                self.assertEqual(self.path.read_bytes(), b'previous report')
                self.assert_no_temporary_files()

    def test_invalid_sections_or_unicode_cannot_publish(self):
        for parts in [[('', 'text')], [('text', b'bytes')], [('text', '\ud800')]]:
            with self.subTest(parts=parts):
                with self.assertRaises((TypeError, UnicodeError)):
                    with ReportSpool(self.path) as spool:
                        spool.write_sections(parts)
                self.assertFalse(self.path.exists())
                self.assert_no_temporary_files()

    def test_short_spool_write_is_not_published(self):
        with ReportSpool(self.path) as spool:
            with patch.object(spool.file, 'write', return_value=0):
                with self.assertRaisesRegex(OSError, 'short write'):
                    spool.write_sections([('text', 'abc')])
            with self.assertRaises(RuntimeError):
                spool.publish()
        self.assertFalse(self.path.exists())
        self.assert_no_temporary_files()

    def test_copy_is_bounded_and_preserves_position_on_failure(self):
        payload = 'a' * (2 * 1024 * 1024 + 17)
        with ReportSpool(self.path) as spool:
            spool.write_sections([('text', payload), ('chrome', 'end'), ('text', '尾')])
            original_read = spool.file.read
            requested = []

            def bounded_read(length):
                requested.append(length)
                self.assertLessEqual(length, 1024 * 1024)
                return original_read(length)

            copied = io.BytesIO()
            with patch.object(spool.file, 'read', side_effect=bounded_read):
                spool.copy_sections('text', copied)
            self.assertGreater(len(requested), 2)
            self.assertEqual(copied.getvalue(), (payload + '尾').encode('utf-8'))
            position = spool.file.tell()
            with patch.object(copied, 'write', return_value=0):
                with self.assertRaisesRegex(OSError, 'short write'):
                    spool.copy_sections('text', copied)
            self.assertEqual(spool.file.tell(), position)
            with patch.object(spool.file, 'read', return_value=b''):
                with self.assertRaisesRegex(OSError, 'section boundary'):
                    spool.copy_sections('text', copied)
            self.assertEqual(spool.file.tell(), position)
        self.assertFalse(self.path.exists())

    def test_preserves_existing_permissions_and_new_file_umask(self):
        self.path.write_bytes(b'previous report')
        self.path.chmod(0o640)
        with ReportSpool(self.path) as spool:
            spool.write_sections([('text', 'updated')])
            spool.publish()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        control = self.path.with_name('control')
        control.write_bytes(b'control')
        self.path.unlink()
        with ReportSpool(self.path) as spool:
            spool.write_sections([('text', 'new')])
            spool.publish()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), stat.S_IMODE(control.stat().st_mode))

    def test_output_symlink_is_preserved_and_target_updated(self):
        target = self.path.with_name('target.html')
        target.write_bytes(b'previous report')
        self.path.symlink_to(target.name)
        with ReportSpool(self.path) as spool:
            spool.write_sections([('text', 'updated')])
            spool.publish()
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_bytes(), b'updated')
        self.assert_no_temporary_files()

    def test_directory_and_non_regular_destinations_are_rejected(self):
        self.path.mkdir()
        for path in [self.path, Path(os.devnull)]:
            with self.subTest(path=path), self.assertRaisesRegex(OSError, 'not a regular file'):
                with ReportSpool(path):
                    self.fail('non-regular destination was accepted')
        self.assertTrue(self.path.is_dir())
        self.assert_no_temporary_files()

    def test_new_destination_cleanup_and_missing_parent(self):
        with ReportSpool(self.path) as spool:
            spool.write_sections([('text', 'not published')])
        self.assertFalse(self.path.exists())
        self.assert_no_temporary_files()
        with self.assertRaises(FileNotFoundError):
            with ReportSpool(self.path.parent / 'missing' / 'report.html'):
                self.fail('missing parent was accepted')


class ReportSectionsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fgsections_')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'report.html'
        self.raw = ['# tracer: function_graph', '# 中文🙂', ' 0)  foo() {', ' 0)   2.000 us    |  }']
        self.lines = FtraceParser().parse('unused', lines=self.raw, enable_fold=True)
        self.timeline = build_timeline(self.raw).to_data()
        self.prepare = patch.object(HtmlGenerator, '_prepare_data')
        self.prepare.start()
        self.addCleanup(self.prepare.stop)
        self.environment = patch('fglib.report.get_environment_info', return_value=[])
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_legacy_string_api_and_streamed_file_are_byte_identical(self):
        for timeline in [None, self.timeline]:
            with self.subTest(timeline=timeline is not None):
                options = dict(timeline_json=timeline, enable_fold=True)
                text, old_vmlinux, old_modules = generate_html(self.lines, None, None, **options)
                written, new_vmlinux, new_modules = write_html(self.path, self.lines, None, None, **options)
                self.assertEqual(self.path.read_bytes(), text.encode('utf-8'))
                self.assertEqual(written.byte_count, len(text.encode('utf-8')))
                self.assertEqual((new_vmlinux, new_modules), (old_vmlinux, old_modules))

    def test_named_payloads_extract_without_markup_and_rows_are_incremental(self):
        generator = HtmlGenerator(self.lines, None, None, timeline_json=self.timeline, enable_fold=True)
        sections = list(generator.iter_sections())
        self.assertEqual(sum(name == 'text' for name, fragment in sections), len(self.lines) + 2)
        self.assertEqual(sum(name == 'source_data' for name, fragment in sections), 1)
        with ReportSpool(self.path) as spool:
            spool.write_sections(iter(sections))
            for name in ['source_data', 'source_index_data', 'view_data:tlData']:
                extracted = io.BytesIO()
                spool.copy_sections(name, extracted)
                self.assertNotIn(b'<script', extracted.getvalue())
                json.loads(extracted.getvalue())
            self.assertLess(len(spool.sections), 20)
            spool.publish()
        self.assertEqual(helpers.json_script(self.path.read_text(), 'tlData')['n'], self.timeline['n'])

    def test_text_chunks_are_bounded_balanced_and_preserve_complete_fragments(self):
        generator = HtmlGenerator(self.lines, None, None)
        opening = f'<div class="text-chunk" data-chunk-size="{TEXT_CHUNK_ROWS}">'
        for count in [0, 1, TEXT_CHUNK_ROWS, TEXT_CHUNK_ROWS + 1, TEXT_CHUNK_ROWS * 2 + 1]:
            with self.subTest(count=count):
                fragments = [f'<div class="line-container" id="L{index}">中文🙂</div>'
                             f'<div class="expanded-content" id="source{index}"></div>' for index in range(count)]
                with patch.object(generator, '_iter_content_lines', return_value=iter(fragments)):
                    result = list(generator._iter_content_chunks())
                offset = 0
                for start in range(0, count, TEXT_CHUNK_ROWS):
                    rows = fragments[start:start + TEXT_CHUNK_ROWS]
                    self.assertEqual(result[offset], opening)
                    self.assertEqual(result[offset + 1:offset + 1 + len(rows)], rows)
                    self.assertEqual(result[offset + 1 + len(rows)], '</div>')
                    offset += len(rows) + 2
                self.assertEqual(len(result), offset)

    def test_text_chunk_iterator_does_not_buffer_rows_or_repeat_iteration(self):
        generator = HtmlGenerator(self.lines, None, None)
        consumed = []

        def rows():
            for index in range(TEXT_CHUNK_ROWS + 1):
                consumed.append(index)
                yield f'row{index}'

        with patch.object(generator, '_iter_content_lines', return_value=rows()) as source:
            result = generator._iter_content_chunks()
            self.assertEqual(consumed, [])
            self.assertIn('class="text-chunk"', next(result))
            self.assertEqual(consumed, [0])
            self.assertEqual(next(result), 'row0')
            self.assertEqual(consumed, [0])
            remaining = list(result)
            self.assertEqual(consumed, list(range(TEXT_CHUNK_ROWS + 1)))
            self.assertEqual([fragment for fragment in remaining if fragment.startswith('row')],
                             [f'row{index}' for index in range(1, TEXT_CHUNK_ROWS + 1)])
            self.assertEqual(remaining[-1], '</div>')
            source.assert_called_once_with()

    def test_legacy_and_streamed_output_match_across_text_chunk_boundary(self):
        lines = FtraceParser().parse('unused', lines=['# 中文🙂'] * (TEXT_CHUNK_ROWS + 1))
        text, _, _ = generate_html(lines, None, None)
        write_html(self.path, lines, None, None)
        self.assertEqual(self.path.read_bytes(), text.encode('utf-8'))
        self.assertEqual(text.count('class="text-chunk"'), 2)

    def test_streaming_path_does_not_call_full_string_builders(self):
        with patch.object(HtmlGenerator, 'generate', side_effect=AssertionError('full document')):
            with patch.object(HtmlGenerator, '_generate_content_lines', side_effect=AssertionError('all rows')):
                with patch.object(HtmlGenerator, '_source_pool_script', side_effect=AssertionError('source wrapper')):
                    with patch.object(HtmlGenerator, '_timeline_scripts', side_effect=AssertionError('all views')):
                        write_html(self.path, self.lines, None, None, timeline_json=self.timeline)
        self.assertIn('中文🙂', self.path.read_text())

    def test_failure_after_header_preserves_old_output(self):
        self.path.write_bytes(b'previous report')

        def broken():
            yield '<div>first</div>'
            raise ValueError('bad trace row')

        with patch.object(HtmlGenerator, '_iter_content_lines', return_value=broken()):
            with self.assertRaisesRegex(ValueError, 'bad trace row'):
                write_html(self.path, self.lines, None, None)
        self.assertEqual(self.path.read_bytes(), b'previous report')
        self.assertEqual(list(self.path.parent.glob('.funcgraph-*.tmp')), [])

    def test_cli_rejects_directory_output_without_deleting_contents(self):
        self.path.mkdir()
        marker = self.path / 'keep.txt'
        marker.write_text('keep')
        result = subprocess.run([sys.executable, os.path.join(helpers.ROOT, 'funcgraph.py'),
                                 helpers.fixture('pi4_default'), '--timeline', '--output', str(self.path)],
                                capture_output=True, text=True, timeout=60,
                                env=dict(os.environ, FG_CONFIG=os.devnull))
        self.assertEqual(result.returncode, 1)
        self.assertIn('Error writing output file:', result.stderr)
        self.assertIn('not a regular file', result.stderr)
        self.assertEqual(marker.read_text(), 'keep')
