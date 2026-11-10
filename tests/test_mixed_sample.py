"""Rebuild and open the published demo_io sample without native dependencies."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest

import helpers
from fglib import timeline, uftrace
from test_timeline import invariant_errors
from test_uftrace_import import invoke


ROOT = Path(helpers.ROOT)
TRACE = ROOT / 'samples/sample_mixed.jsonl'
REPORT = ROOT / 'sample_mixed.html'
COUNTS = {'user': 15, 'kernel': 916}
PAGE_FILES = {'index.html', 'sample.html', 'sample.png', 'sample_text.png',
              'sample_mixed.html', 'sample_mixed.png', '.nojekyll'}


def generate(trace, report):
    result = invoke('--timeline', '--enable-fold', '--filter', '--user-src', ROOT / 'samples',
                    '--report-mode', 'single', '--output', report, trace)
    if result.returncode:
        raise AssertionError(result.stderr)


def assemble(destination):
    subprocess.run(['bash', str(ROOT / 'site/build.sh'), str(destination)],
                   cwd=destination.parent, check=True, capture_output=True)


def stable_html(html):
    html = re.sub(r'^ +<div class="info-item"><div class="info-label">'
                  r'(?:Generated At|Parse Time|Vmlinux Time|Modules Time|Total Time|Ftrace File|Output|User Src):'
                  r'</div><div class="info-value">[^\n]*</div></div>\n', '', html, flags=re.M)
    return re.sub(r'("build_time"\s*:\s*)[0-9.eE+-]+', r'\g<1>0', html)


class MixedSampleTest(unittest.TestCase):
    def test_real_capture_and_reproducible_report(self):
        with TRACE.open(encoding='utf-8') as source:
            metadata, records = uftrace.read_export(source)
        self.assertEqual(metadata['kernel_settings']['kernel_release'], '7.4.0-rc2')
        for space, count in COUNTS.items():
            for kind in ('entry', 'exit'):
                self.assertEqual(sum(record['space'] == space and record['type'] == kind for record in records), count)
        self.assertEqual({record['type'] for record in records}, {'entry', 'exit'})
        sendto = next(record for record in records if record['name'] == '__x64_sys_sendto' and record['type'] == 'entry')
        self.assertTrue(sendto['args'])
        self.assertIn('retaddr', sendto)
        self.assertIn('0xfffffffffffffff5', [record.get('retval') for record in records
                                             if record['name'] == '__x64_sys_recvfrom'])

        html = REPORT.read_text(encoding='utf-8')
        data = helpers.json_script(html, 'tlData')
        self.assertEqual(data['n'], sum(COUNTS.values()))
        self.assertEqual(invariant_errors(data), [])
        self.assertTrue(data['uftrace']['aligned'])
        self.assertEqual(data['uftrace']['spaces'], ['kernel', 'user'])
        for stat in ('lost', 'truncated', 'orphans', 'detached_kernel_roots'):
            self.assertEqual(data['stats'][stat], 0, stat)
        cols = data['cols']
        names = [data['strings'][index] for index in cols['name']]
        boundaries = [index for index, flags in enumerate(cols['flags']) if flags & timeline.F_BOUNDARY]
        self.assertEqual([(names[cols['parent'][index]], names[index]) for index in boundaries], [
            ('read', '__x64_sys_read'), ('write', '__x64_sys_write'), ('sendto', '__x64_sys_sendto'),
            ('recvfrom', '__x64_sys_recvfrom'), ('recvfrom', '__x64_sys_recvfrom')])
        index = names.index('loopback_xmit')
        ancestry = []
        while index >= 0:
            ancestry.append(names[index])
            index = cols['parent'][index]
        self.assertEqual(ancestry[-3:], ['__x64_sys_sendto', 'sendto', 'main'])
        self.assertEqual(len(re.findall(r'<div class="line-container [^>]+data-boundary="user-kernel"', html)), 5)
        with tempfile.TemporaryDirectory() as directory:
            rebuilt = Path(directory) / REPORT.name
            generate(TRACE, rebuilt)
            self.assertTrue(stable_html(html) == stable_html(rebuilt.read_text(encoding='utf-8')),
                            'Regenerate sample_mixed.html using the command in docs/uftrace.md')

    def test_pages_stage_preserves_both_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'pages output'
            assemble(output)
            self.assertEqual({path.name for path in output.iterdir()}, PAGE_FILES)
            for name in PAGE_FILES - {'.nojekyll'}:
                source = ROOT / ({'index.html': 'site/index.html', 'sample_text.png': 'samples/sample_text.png'}
                                 .get(name, name))
                self.assertTrue((output / name).read_bytes() == source.read_bytes(), name)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class MixedSampleBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
            cls.addClassCleanup(playwright.stop)
            cls.browser = playwright.chromium.launch()
            cls.addClassCleanup(cls.browser.close)
        except Exception as error:
            if os.environ.get('FG_SAMPLE_BROWSER_REQUIRED') == '1':
                raise
            raise unittest.SkipTest(str(error)) from error

    def open_page(self, url, dpr=1):
        page = self.browser.new_page(viewport={'width': 1600, 'height': 900}, device_scale_factor=dpr,
                                     locale='en-US', color_scheme='light')
        page.set_default_timeout(5000)
        self.addCleanup(page.close)
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url))
        page.goto(url)
        return page, errors, requests

    def test_offline_report_timeline_text_filter_fold_and_source(self):
        page, errors, requests = self.open_page(REPORT.as_uri(), dpr=1.25)
        page.get_by_role('button', name='Timeline', exact=True).click()
        page.locator('.tl-space-key', has_text='User → kernel').wait_for(state='visible')
        point, painted = page.evaluate('''async () => {
            const timeline = fgView('timeline'), model = timeline.probe();
            const index = Array.from({length: model.N}, (unused, candidate) => candidate)
                .find(candidate => model.nameOf(candidate) === '__x64_sys_sendto');
            const canvas = document.querySelector('.tl-canvas'), context = canvas.getContext('2d');
            const original = context.fillRect, strokes = [];
            context.fillRect = function (left, top, width, height) {
                if (this.fillStyle === '#5b6573' && height === 1 / devicePixelRatio) strokes.push([left, width]);
                return original.call(this, left, top, width, height);
            };
            try {
                timeline.draw();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            } finally { context.fillRect = original; }
            const [start, end] = model.view(), scale = canvas.clientWidth / (end - start);
            const left = (model.sStart[index] - start) * scale;
            const width = (model.sEnd[index] - model.sStart[index]) * scale;
            const painted = strokes.some(([strokeLeft, strokeWidth]) =>
                Math.abs(strokeLeft - left) < 2 && Math.abs(strokeWidth - width) < 2);
            return [model.xyOf(index), painted];
        }''')
        self.assertTrue(painted, 'Expected a one-device-pixel boundary spanning the recorded sendto root')
        page.mouse.click(*point)
        stack = page.locator('[data-context-list=stack]')
        divider = stack.locator('[data-space-boundary=user-kernel]', has_text='__x64_sys_sendto')
        divider.wait_for(state='visible')
        self.assertEqual(divider.evaluate('row => getComputedStyle(row).borderTopWidth'), '1px')
        stack.locator('.tl-source-open', has_text='demo_io.c:22').click()
        page.get_by_text('int main(int argc, char **argv)', exact=False).first.wait_for(state='visible')

        page.get_by_role('button', name='Text', exact=True).click()
        marker = page.locator('#content [data-boundary=user-kernel][data-func=__x64_sys_sendto] .trace-space')
        self.assertEqual(marker.inner_text().strip(), 'K')
        self.assertEqual(marker.evaluate('tag => getComputedStyle(tag).borderBottomWidth'), '1px')
        main = page.locator('#content .fold-entry[data-space=user][data-func=main] .fold-icon')
        main.click()
        marker.wait_for(state='hidden')
        main.click()
        marker.wait_for(state='visible')
        page.locator('#filterInput').fill('space:user')
        page.locator('#filterInput').press('Enter')
        marker.wait_for(state='hidden')
        self.assertGreater(page.locator('.line-container[data-space=user]:visible').count(), 0)
        self.assertEqual(page.locator('.line-container[data-space=kernel]:visible').count(), 0)
        self.assertEqual(errors, [])
        self.assertEqual(requests, [REPORT.as_uri()])

    def test_both_single_space_reports_have_no_dividers(self):
        with TRACE.open(encoding='utf-8') as source:
            metadata, records = uftrace.read_export(source)
        with tempfile.TemporaryDirectory() as directory:
            for space, count in COUNTS.items():
                with self.subTest(space=space):
                    trace = Path(directory) / (space + '.jsonl')
                    report = trace.with_suffix('.html')
                    rows = [metadata] + [record for record in records if record['space'] == space]
                    rows.append({'type': 'end', 'complete': True})
                    trace.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
                    generate(trace, report)
                    data = helpers.json_script(report.read_text(encoding='utf-8'), 'tlData')
                    self.assertEqual(data['uftrace']['spaces'], [space])
                    self.assertEqual(data['n'], count)
                    self.assertEqual(invariant_errors(data), [])
                    self.assertFalse(any(flags & timeline.F_BOUNDARY for flags in data['cols']['flags']))
                    page, errors, _requests = self.open_page(report.as_uri())
                    self.assertGreater(page.locator(f'#content [data-space={space}]').count(), 0)
                    self.assertEqual(page.locator('#content [data-boundary]').count(), 0)
                    page.get_by_role('button', name='Timeline', exact=True).click()
                    page.locator('.tl-canvas').wait_for(state='visible')
                    self.assertEqual(page.locator('.tl-space-key').count(), 0)
                    self.assertEqual(errors, [])

    def test_bilingual_pages_links_under_project_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            assemble(Path(directory) / 'FuncGraph')
            with ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=directory)) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f'http://127.0.0.1:{server.server_port}/FuncGraph/'
                    page, errors, _requests = self.open_page(base)
                    self.assertEqual(page.get_by_role('link', name='Open the mixed sample').get_attribute('href'),
                                     'sample_mixed.html')
                    self.assertTrue(page.locator('#mixed-sample img').evaluate('img => img.complete && img.naturalWidth > 0'))
                    self.assertTrue(page.locator('#kernel-sample img').evaluate('img => img.complete && img.naturalWidth > 0'))
                    page.get_by_role('link', name='中文', exact=True).click()
                    self.assertEqual(page.locator('html').get_attribute('lang'), 'zh-CN')
                    page.get_by_role('link', name='打开纯内核态示例', exact=True).click()
                    page.wait_for_url(base + 'sample.html')
                    page.get_by_role('button', name='Timeline', exact=True).click()
                    page.locator('.tl-canvas').wait_for(state='visible')
                    page.go_back()
                    page.get_by_role('link', name='打开用户态 + 内核态示例', exact=True).click()
                    page.wait_for_url(base + 'sample_mixed.html')
                    page.get_by_role('button', name='Timeline', exact=True).click()
                    page.locator('.tl-space-key', has_text='User → kernel').wait_for(state='visible')
                    self.assertEqual(errors, [])
                finally:
                    server.shutdown()
                    thread.join()
