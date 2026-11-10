"""Structured uftrace import, report delivery and cross-address-space navigation."""

import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import helpers
from fglib import export, inputs, timeline, uftrace
from fglib.viewer import create_server
from test_timeline import decode, invariant_errors


ORIGIN = 18446744000000000000
HEADER = dict(type='metadata', schema=uftrace.SCHEMA, order='streams', timestamp_unit='ns',
              depth_basis='address-space', clock='mono', kernel_settings={'trace_clock': 'local [mono] boot'})


def call(kind, space, timestamp, depth, name, address, tid=42, cpu=0, **fields):
    return dict(type=kind, space=space, time_ns=ORIGIN + timestamp, depth=depth, name=name,
                address=hex(address), tid=tid, cpu=cpu if space == 'kernel' else None, **fields)


def capture():
    return [call('entry', 'user', 100, 0, 'main', 1, comm='demo', source={'file': 'demo.c', 'line': 2}),
            call('entry', 'user', 200, 1, 'work<int>', 2),
            call('exit', 'user', 650, 1, 'work<int>', 2),
            call('exit', 'user', 900, 0, 'main', 1),
            call('entry', 'kernel', 240, 0, 'work<int>', 3, retaddr='0xffff0000'),
            call('entry', 'kernel', 320, 1, 'inner', 4, args=['0x0']),
            call('exit', 'kernel', 520, 1, 'inner', 4, retval='0xfffffffffffffffe'),
            call('exit', 'kernel', 620, 0, 'work<int>', 3),
            call('entry', 'kernel', 700, 0, 'second', 5),
            call('exit', 'kernel', 810, 0, 'second', 5),
            call('entry', 'kernel', 915, 0, 'outside', 6),
            call('exit', 'kernel', 950, 0, 'outside', 6)]


def stream(records, metadata=HEADER, complete=True):
    return '\n'.join(json.dumps(record) for record in [metadata, *records, {'type': 'end', 'complete': complete}]) + '\n'


def build(records, metadata=HEADER):
    return inputs.UftraceInput(io.StringIO(stream(records, metadata))).build(enable_fold=True)


def invoke(*args):
    return subprocess.run([sys.executable, str(Path(helpers.__file__).resolve().parents[1] / 'funcgraph.py'),
                           '--no-config', *map(str, args)], capture_output=True, text=True)


class UftraceImportTest(unittest.TestCase):
    def test_shared_clock_joins_at_two_actual_depths_and_preserves_ns(self):
        builder = build(capture())
        data = builder.to_data()
        self.assertEqual(invariant_errors(data), [])
        columns = data['cols']
        self.assertEqual(data['uftrace']['origin_ns'], str(ORIGIN + 100))
        self.assertEqual(decode(data), ([0, 100, 140, 220, 600, 815], [800, 550, 520, 420, 710, 850]))
        self.assertEqual(columns['parent'], [-1, 0, 1, 2, 0, -1])
        self.assertEqual(columns['depth'], [0, 1, 2, 3, 1, 0])
        self.assertEqual(columns['cpu'], [-1, -1, 0, 0, 0, 0])
        self.assertEqual([bool(flag & timeline.F_BOUNDARY) for flag in columns['flags']], [False, False, True, False, True, False])
        self.assertNotEqual(columns['name'][1], columns['name'][2])
        self.assertEqual(data['chains'], [])
        self.assertEqual(data['strings'][columns['ret'][3]], '0xfffffffffffffffe')
        self.assertEqual(data['strings'][columns['caller'][2]], '0xffff0000')
        self.assertEqual(data['strings'][columns['args'][3]], '0x0')
        for index in [2, 4]:
            row = builder.parsed[columns['line'][index] - 1]
            parent = builder.parsed[columns['line'][columns['parent'][index]] - 1]
            self.assertTrue(row.boundary)
            self.assertIn(parent.fold_id, row.fold_ancestors.split())
        by_space = {(row['name'], row['space']) for row in export.build(data)['functions']}
        self.assertTrue({('work<int>', 'user'), ('work<int>', 'kernel')} <= by_space)

    def test_only_containment_on_same_task_and_verified_clock_is_linked(self):
        records = capture() + [call('entry', 'kernel', 300, 0, 'other', 7, tid=7, cpu=1),
                               call('exit', 'kernel', 400, 0, 'other', 7, tid=7, cpu=1)]
        for metadata, count in ((HEADER, 2), (dict(HEADER, clock=None), 0),
                                (dict(HEADER, kernel_settings={'trace_clock': '[local] mono'}), 0)):
            with self.subTest(metadata=metadata):
                data = build(records, metadata).to_data()
                self.assertEqual(data['stats']['user_kernel_boundaries'], count)
                self.assertEqual(invariant_errors(data), [])
        records[7]['address'] = '0x55'
        data = build(records).to_data()
        self.assertEqual(data['stats']['user_kernel_boundaries'], 1)
        self.assertGreater(data['stats']['truncated'], 0)
        self.assertGreater(data['stats']['detached_kernel_roots'], 0)
        self.assertEqual(invariant_errors(data), [])

    def test_zero_timestamp_user_loss_breaks_pairing_at_native_stream_position(self):
        records = capture()
        records.insert(2, dict(type='lost', space='user', tid=42, cpu=None, time_ns=0, count=2))
        builder = build(records)
        data = builder.to_data()
        self.assertEqual(data['stats']['lost'], 1)
        self.assertEqual(data['stats']['truncated'], 2)
        self.assertEqual(data['stats']['orphans'], 2)
        self.assertEqual(data['stats']['user_kernel_boundaries'], 0)
        self.assertIn('timestamp ?', builder.parsed[4].raw_line)

    def test_rejects_incomplete_and_invalid_streams(self):
        for text in (stream(capture(), complete=False), stream(capture()) + '{}',
                     stream(capture()).rsplit('{"type": "end"', 1)[0],
                     stream([dict(capture()[0], space='unknown')]),
                     stream([capture()[1], capture()[0]])):
            with self.subTest(text=text[:200]), self.assertRaises(uftrace.UftraceInputError):
                uftrace.read_export(io.StringIO(text))

    def test_cli_three_populations_without_vmlinux_and_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for space in ('user', 'kernel', 'mixed'):
                records = [record for record in capture() if record['space'] == space or space == 'mixed']
                with gzip.open(root / 'capture.jsonl.gz', 'wt', encoding='utf-8') as output:
                    output.write(stream(records))
                html = root / (space + '.html')
                result = invoke(root / 'capture.jsonl.gz', '--timeline', '--enable-fold', '--filter', '--output', html)
                self.assertEqual(result.returncode, 0, result.stderr)
                text = html.read_text(encoding='utf-8')
                self.assertEqual(' data-boundary="user-kernel"' in text, space == 'mixed')
                data = helpers.json_script(text, 'tlData')
                self.assertEqual(data['uftrace']['spaces'], ['kernel', 'user'] if space == 'mixed' else [space])
                self.assertEqual(invariant_errors(data), [])
            (root / 'baseline.jsonl').write_text(stream(capture()[:4]), encoding='utf-8')
            result = invoke(root / 'capture.jsonl.gz', '--compare', root / 'baseline.jsonl', '--output', root / 'compare.html')
            self.assertEqual(result.returncode, 0, result.stderr)
            comparison = helpers.json_script((root / 'compare.html').read_text(encoding='utf-8'), 'cmpData')
            self.assertEqual({row[11] for row in comparison['rows'] if row[0] == 'work<int>'}, {'user', 'kernel'})


class UftraceBrowserTest(unittest.TestCase):
    def test_timeline_boundary_and_deferred_text_filter(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:
                self.skipTest(str(error))
            root = Path(directory)
            (root / 'capture.jsonl').write_text(stream(capture()), encoding='utf-8')
            (root / 'demo.c').write_text('/* recorded user source */\nint main(void) {}\n', encoding='utf-8')
            result = invoke(root / 'capture.jsonl', '--timeline', '--enable-fold', '--filter', '--user-src', root,
                            '--report-mode', 'sidecar', '--output', root / 'report')
            self.assertEqual(result.returncode, 0, result.stderr)
            with create_server(root / 'report') as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    page = browser.new_page(viewport={'width': 1450, 'height': 900}, device_scale_factor=1.25)
                    errors, requests = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: requests.append(request.url))
                    page.goto(server.url)
                    page.locator('.tl-space-key').wait_for()
                    page.evaluate('''() => {
                        const model = fgView('timeline').probe();
                        model.select(2);
                    }''')
                    boundary = page.locator('[data-context-list=stack] [data-space-boundary]')
                    self.assertEqual(boundary.count(), 1)
                    self.assertIn('work<int>', boundary.inner_text())
                    self.assertTrue(page.evaluate('''async () => {
                        const api = fgView('timeline'), model = api.probe();
                        const canvas = document.querySelector('.tl-canvas');
                        const context = canvas.getContext('2d'), original = context.fillRect, lines = [];
                        context.fillRect = function (x, y, w, h) {
                            if (this.fillStyle === '#5b6573' && h === 0.8) lines.push([x, w]);
                            return original.call(this, x, y, w, h);
                        };
                        try {
                            api.draw();
                            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        } finally { context.fillRect = original; }
                        const [start, end] = model.view(), scale = canvas.clientWidth / (end - start);
                        const left = (model.sStart[2] - start) * scale;
                        const width = (model.sEnd[2] - model.sStart[2]) * scale;
                        return lines.some(([x, w]) => Math.abs(x - left) < 2 && Math.abs(w - width) < 2);
                    }'''))
                    page.locator('.tl-source-open').click()
                    page.get_by_text('int main(void) {}', exact=False).wait_for()
                    page.locator('.tl-ftext').fill('space:user')
                    page.locator('.tl-ftext').press('Enter')
                    page.locator('.tl-fcount', has_text='2 of 6 calls').wait_for()
                    self.assertFalse(any(url.endswith('text.html') for url in requests))
                    page.locator('[data-act=fclear]').click()
                    page.evaluate("fgSwitchView('text')")
                    boundary = page.locator('#content [data-boundary]')
                    self.assertEqual(boundary.count(), 2)
                    page.locator('#content .fold-entry[data-space=user] .fold-icon').first.click()
                    self.assertFalse(boundary.first.is_visible())
                    self.assertEqual(errors, [])
                finally:
                    server.shutdown()
                    thread.join()
                    browser.close()
