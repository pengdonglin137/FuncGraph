"""Validate the published mixed sample and its no-debug-image fallback."""

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
COUNTS = {'user': 25, 'kernel': 689}
PAGE_FILES = {'index.html', 'sample.html', 'sample.png', 'sample_text.png',
              'sample_mixed.html', 'sample_mixed.png', '.nojekyll'}


def generate(trace, report):
    result = invoke('--timeline', '--enable-fold', '--filter', '--highlight-code', '--user-src', ROOT / 'samples',
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
    def test_real_capture_and_no_debug_rebuild_preserve_recorded_model(self):
        with TRACE.open(encoding='utf-8') as source:
            metadata, records = uftrace.read_export(source)
        self.assertEqual(metadata['kernel_settings']['kernel_release'], '7.4.0-rc2')
        for space, count in COUNTS.items():
            for kind in ('entry', 'exit'):
                self.assertEqual(sum(record['space'] == space and record['type'] == kind for record in records), count)
        self.assertEqual({record['type'] for record in records}, {'entry', 'exit', 'event'})
        events = [record for record in records if record['type'] == 'event']
        self.assertEqual(len(events), 25)
        self.assertTrue(all(record['space'] == 'user' and record['name'] == 'callsite' for record in events))
        sendto = next(record for record in records if record['name'] == '__x64_sys_sendto' and record['type'] == 'entry')
        self.assertTrue(sendto['args'])
        self.assertIn('retaddr', sendto)
        self.assertIn('0xfffffffffffffff5', [record.get('retval') for record in records
                                             if record['name'] == '__x64_sys_recvfrom'])
        dispatch = next(record for record in records if record['name'] == 'dispatch_datagram' and record['type'] == 'entry')
        self.assertEqual(dispatch['user_args'][2], '512')
        self.assertIn('payload_hex', dispatch)
        self.assertEqual([record['user_retval'] for record in records
                          if record['name'] == 'receive_frame' and record['type'] == 'exit'], ['512', '-1'])

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
        kernel_flags = [flags for flags in cols['flags'] if not flags & timeline.F_USER]
        self.assertEqual(sum(bool(flags & timeline.F_IRQOFF) for flags in kernel_flags), 51)
        self.assertEqual(sum(bool(flags & timeline.F_BHOFF) for flags in kernel_flags), 51)
        self.assertEqual(sum(flags & timeline.F_CTX_MASK == timeline.CTX_HARDIRQ for flags in kernel_flags), 0)
        self.assertEqual(sum(flags & timeline.F_CTX_MASK == timeline.CTX_SOFTIRQ for flags in kernel_flags), 45)
        self.assertTrue(all(flags & timeline.F_HAS_LAT for flags in kernel_flags))
        self.assertFalse(any(flags & timeline.F_HAS_LAT for flags in cols['flags'] if flags & timeline.F_USER))
        boundaries = [index for index, flags in enumerate(cols['flags']) if flags & timeline.F_BOUNDARY]
        self.assertEqual([(names[cols['parent'][index]], names[index]) for index in boundaries], [
            ('read', '__x64_sys_read'), ('write', '__x64_sys_write'), ('sendto', '__x64_sys_sendto'),
            ('recvfrom', '__x64_sys_recvfrom'), ('recvfrom', '__x64_sys_recvfrom')])
        index = names.index('loopback_xmit')
        ancestry = []
        while index >= 0:
            ancestry.append(names[index])
            index = cols['parent'][index]
        self.assertEqual(ancestry[-7:], ['__x64_sys_sendto', 'sendto', 'dispatch_datagram', 'send_frame',
                                         'exchange_loopback', 'run_round', 'main'])
        detail = data['uftrace']['user_details'][str(cols['line'][names.index('dispatch_datagram')])]
        self.assertEqual(detail['address'], dispatch['address'])
        self.assertEqual(detail['arg_names'], ['fd', 'buffer', 'length', 'peer', 'peer_size'])
        self.assertEqual(detail['args'], ', '.join(f'{name}={value}'
                                                 for name, value in zip(dispatch['user_arg_names'], dispatch['user_args'])))
        self.assertEqual(detail['retval'], '512')
        self.assertGreater(data['btf'], 0)
        self.assertEqual(len(data['chains']), 1)
        self.assertEqual(data['chains'][0][2], -11)
        self.assertTrue(data['chains'][0][3])
        self.assertEqual(len(re.findall(r'<div class="line-container [^>]+data-boundary="user-kernel"', html)), 5)
        with tempfile.TemporaryDirectory() as directory:
            rebuilt = Path(directory) / REPORT.name
            generate(TRACE, rebuilt)
            rebuilt_html = rebuilt.read_text(encoding='utf-8')
            rebuilt_data = helpers.json_script(rebuilt_html, 'tlData')
            for key in data.keys() - {'btf', 'cols', 'errs', 'chains', 'errnames', 'sources', 'stats', 'inline', 'uftrace'}:
                self.assertEqual(data[key], rebuilt_data[key], key)
            self.assertTrue(data['uftrace']['kernel_arg_names'])
            self.assertTrue(data['uftrace']['kernel_types'])
            self.assertNotIn('kernel_arg_names', rebuilt_data['uftrace'])
            self.assertNotIn('kernel_types', rebuilt_data['uftrace'])
            expected_uftrace = {key: value for key, value in data['uftrace'].items()
                               if key not in ('kernel_arg_names', 'kernel_types')}
            expected_details = {}
            for line, detail in data['uftrace']['user_details'].items():
                expected_detail = dict(detail)
                values = detail.get('args', '').split(', ')
                if 'args' in detail and not detail.get('arg_names') and all(uftrace.SCALAR.fullmatch(value) for value in values):
                    expected_detail['args'] = ', '.join(f'slot{position + 1}={value}' for position, value in enumerate(values))
                expected_details[line] = expected_detail
            expected_uftrace['user_details'] = expected_details
            self.assertEqual(expected_uftrace, rebuilt_data['uftrace'])
            for key in cols:
                if key == 'flags':
                    self.assertEqual([flags & ~timeline.F_NULL_RET for flags in cols[key]], rebuilt_data['cols'][key])
                else:
                    self.assertEqual(cols[key], rebuilt_data['cols'][key], key)
            for key in data['stats'].keys() - {'build_time'}:
                self.assertEqual(data['stats'][key], rebuilt_data['stats'][key], key)
            for key in ('user_lines', 'user_positions', 'user_callsites'):
                self.assertEqual(data['sources'][key], rebuilt_data['sources'][key])
            self.assertEqual(rebuilt_data['sources']['functions'], {})
            original_lines, rebuilt_lines = (helpers.json_script(content, 'fgSrcLines')
                                             for content in (html, rebuilt_html))
            for line in data['sources']['user_lines']:
                self.assertEqual(line in original_lines, line in rebuilt_lines)
                if line not in original_lines:
                    continue
                original_source = helpers.json_script(html, 'fgSrc')[original_lines[line]]
                rebuilt_source = helpers.json_script(rebuilt_html, 'fgSrc')[rebuilt_lines[line]]
                self.assertEqual(original_source, rebuilt_source)
            generate(TRACE, rebuilt)
            self.assertEqual(stable_html(rebuilt_html), stable_html(rebuilt.read_text(encoding='utf-8')))

    def test_published_kernel_entries_have_pinned_links_and_embedded_excerpts(self):
        html = REPORT.read_text(encoding='utf-8')
        data, pool, source_lines = (helpers.json_script(html, key) for key in ('tlData', 'fgSrc', 'fgSrcLines'))
        functions, cols = data['sources']['functions'], data['cols']
        self.assertEqual(len(functions), 306)
        self.assertEqual(len(data['sources']['callers']), 465)
        self.assertTrue(all(data['sources']['callers'].values()))
        self.assertEqual(len(data['inline']), 282)
        self.assertEqual(len(data['uftrace']['kernel_details']), COUNTS['kernel'])
        for index, flags in enumerate(cols['flags']):
            if flags & timeline.F_USER:
                continue
            position = functions[f'{cols["name"][index]}:-1']
            self.assertTrue(position[0] and position[1] > 0)
            self.assertEqual(position[2], f'https://github.com/torvalds/linux/blob/v7.4-rc2/{position[0]}#L{position[1]}')
            excerpt = pool[source_lines[str(cols['line'][index])]]
            self.assertNotIn('kernel function entry', excerpt)
            self.assertIn('source-line current-line', excerpt)
            chain = data['sources']['callers'][str(cols['caller'][index])]
            self.assertTrue(chain)
            self.assertTrue(all(frame[3] in excerpt for frame in chain))
            symbol = data['uftrace']['caller_symbols'][str(cols['caller'][index])]
            raw = data['uftrace']['kernel_details'][str(cols['line'][index])]['retaddr']
            self.assertEqual(int(symbol['address'], 16) + symbol['offset'], int(raw, 16))
        self.assertTrue(all(not position[2] for position in data['sources']['user_positions']))

    def test_published_argument_names_and_user_sites_match_original_records(self):
        with TRACE.open(encoding='utf-8') as source:
            _, records = uftrace.read_export(source)
        html = REPORT.read_text(encoding='utf-8')
        data, pool, source_lines = (helpers.json_script(html, key) for key in ('tlData', 'fgSrc', 'fgSrcLines'))
        cols, sources = data['cols'], data['sources']
        names = [data['strings'][index] for index in cols['name']]
        index = names.index('__sys_sendto')
        self.assertEqual(data['uftrace']['kernel_arg_names'][str(cols['name'][index])],
                         ['fd', 'buff', 'len', 'flags', 'addr', 'addr_len'])
        self.assertEqual(len(data['uftrace']['kernel_arg_names']), 280)
        signature = data['uftrace']['kernel_types'][str(cols['name'][index])]
        self.assertEqual(signature['argc'], 6)
        self.assertEqual(signature['args'], [['sint', 32], ['ptr', 64], ['uint', 64],
                                            ['uint', 32], ['ptr', 64], ['sint', 32]])
        entries = {line: record for line, record in enumerate(records, 3) if record['type'] == 'entry'}
        raw = entries[cols['line'][index]]['args']
        self.assertEqual(data['strings'][cols['args'][index]], ', '.join(raw))
        self.assertEqual(raw[2], '0x200')
        expected_counts = {'main': 2, 'run_round': 4, 'transfer_file': 4, 'read_block': 3,
                           'write_block': 3, 'exchange_loopback': 4, 'send_frame': 4,
                           'dispatch_datagram': 5, 'receive_frame': 4}
        for record in entries.values():
            if record['space'] == 'user' and record['name'] in expected_counts:
                self.assertEqual(len(record['user_args']), expected_counts[record['name']], record['name'])
                self.assertIn('payload_hex', record)
        self.assertEqual(sum('args' in detail for detail in data['uftrace']['user_details'].values()), 19)
        self.assertEqual(sum(bool(detail.get('arg_names')) for detail in data['uftrace']['user_details'].values()), 19)
        self.assertEqual(sum('retval' in detail for detail in data['uftrace']['user_details'].values()), 19)
        sites = {(record['tid'], record['time_ns'] + 1): record for record in records
                 if record['type'] == 'event'}
        self.assertEqual(len(sources['user_callsites']), len(sites))
        for line, record in entries.items():
            if record['space'] != 'user':
                continue
            detail = data['uftrace']['user_details'][str(line)]
            event = sites.get((record['tid'], record['time_ns']))
            if event is None:
                self.assertNotIn('callsite', detail)
                continue
            site = event['user_callsite']
            self.assertIn('symbol', site)
            self.assertEqual(int(site['symbol']['address'], 16) + site['symbol']['offset'], int(site['retaddr'], 16) - 1)
            self.assertLess(site['symbol']['offset'], site['symbol']['size'])
            self.assertEqual(detail['callsite'], {key: value for key, value in site.items() if key != 'source'})
            self.assertEqual(int.from_bytes(bytes.fromhex(event['payload_hex']), 'little'), int(site['retaddr'], 16))
            position = sources['user_positions'][sources['user_callsites'][str(line)]]
            self.assertEqual(position, [site['source']['file'], site['source']['line'], ''])
            if 'source' in record:
                entry_position = sources['user_positions'][sources['user_lines'][str(line)]]
                self.assertEqual(entry_position, [record['source']['file'], record['source']['line'], ''])
        receives = [str(cols['line'][index]) for index, name in enumerate(names) if name == 'receive_frame']
        self.assertEqual([sources['user_positions'][sources['user_callsites'][line]][1] for line in receives], [47, 49])
        self.assertEqual([sources['user_positions'][sources['user_lines'][line]][1] for line in receives], [36, 36])
        for line, number in zip(receives, (47, 49)):
            excerpt = pool[source_lines[line]]
            self.assertIn(f'demo_mixed.c:{number} · user call site (recorded return address - 1)', excerpt)
            self.assertNotIn('function entry (not call site)', excerpt)

    def test_open_has_named_string_arguments_and_real_bounded_call_sites(self):
        with TRACE.open(encoding='utf-8') as source:
            _, records = uftrace.read_export(source)
        data = helpers.json_script(REPORT.read_text(encoding='utf-8'), 'tlData')
        opens = [(line, record) for line, record in enumerate(records, 3)
                 if record['type'] == 'entry' and record['space'] == 'user' and record['name'] == 'open']
        self.assertEqual(len(opens), 2)
        for (line, record), filename, flags, offset in zip(opens, ('input.bin', 'output.bin'), ('0', '1'), (0x73, 0x8c)):
            detail = data['uftrace']['user_details'][str(line)]
            self.assertEqual(record['user_arg_names'], ['pathname', 'flags'])
            self.assertEqual(record['user_arg_name_sources'], ['prototype', 'prototype'])
            self.assertEqual(record['user_args'], [f'"/tmp/funcgraph-btf-values.OBCDn1/{filename}"', flags])
            self.assertEqual(detail['args'], f'pathname={record["user_args"][0]}, flags={flags}')
            self.assertEqual(detail['arg_names'], record['user_arg_names'])
            self.assertEqual(detail['arg_name_sources'], record['user_arg_name_sources'])
            self.assertEqual(detail['callsite']['name'], 'main')
            self.assertEqual(detail['callsite']['symbol'], {'address': '0x55d10752f616', 'offset': offset, 'size': 0x196})
            index = data['cols']['line'].index(line)
            self.assertEqual(data['strings'][data['cols']['args'][index]], record['payload_hex'])

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
        divider = stack.locator('[data-space-boundary=user-kernel]', has_text='do_syscall_x64')
        divider.wait_for(state='visible')
        self.assertEqual(divider.evaluate('row => getComputedStyle(row).borderTopWidth'), '1px')
        self.assertEqual(divider.locator('.tl-stack-space').inner_text(), 'K')
        self.assertEqual(divider.get_attribute('data-context-kind'), 'inline')
        self.assertEqual(divider.evaluate('row => row.previousElementSibling.querySelector(".tl-stack-space").textContent'), 'U')
        arguments = page.locator('.k:text-is("args (BTF)") + .v')
        self.assertEqual(arguments.inner_text().splitlines()[0].count('='), 1)
        self.assertTrue(arguments.inner_text().startswith('__unused=0x'))
        raw_slots = arguments.locator('details:has(summary:text-is("Raw argument slots"))')
        self.assertEqual(raw_slots.get_attribute('open'), None)
        self.assertEqual(raw_slots.text_content().count(', '), 5)
        self.assertEqual(page.locator('.k:text-is("return (BTF)") + .v').inner_text(), '512')
        self.assertIn('0x200', page.locator('.k:text-is("raw return") + .v').text_content())
        stack.locator('.tl-source-open', has_text='demo_mixed.c:33').click()
        page.locator('.tl-cs-src .current-line', has_text='return dispatch_datagram').wait_for(state='visible')
        page.evaluate('''() => {
            const model = fgView('timeline').probe();
            const index = Array.from({length: model.N}, (unused, candidate) => candidate)
                .find(candidate => model.nameOf(candidate) === 'dispatch_datagram');
            model.select(index);
        }''')
        page.locator('.k', has_text='user args').wait_for(state='visible')
        self.assertIn('512', page.locator('.k:has-text("user args") + .v').inner_text())
        self.assertEqual(page.locator('.k:has-text("user return") + .v').inner_text(), '512')
        self.assertEqual(page.get_by_text('BH off', exact=True).count(), 0)
        data = helpers.json_script(REPORT.read_text(encoding='utf-8'), 'tlData')
        irq_bh = next(index for index, flags in enumerate(data['cols']['flags'])
                      if flags & timeline.F_IRQOFF and flags & timeline.F_BHOFF)
        page.evaluate('index => fgView("timeline").probe().select(index)', irq_bh)
        page.locator('.tl-pill').get_by_text('BH off', exact=True).wait_for(state='visible')
        page.locator('.tl-pill').get_by_text('irqs off', exact=True).wait_for(state='visible')

        page.get_by_role('button', name='Text', exact=True).click()
        marker = page.locator('#content [data-boundary=user-kernel][data-func=__x64_sys_sendto] .trace-space')
        dispatch = page.locator('#content .fold-entry[data-func=dispatch_datagram]')
        self.assertIn('512', dispatch.inner_text())
        self.assertIn('recorded address 0x', dispatch.inner_text())
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
        page.locator('#filterInput').fill('space:kernel AND irqs:^d$')
        page.locator('#filterInput').press('Enter')
        irq_rows = page.locator('#content .line-container:is([data-irqs=d],[data-irqs=D]):visible')
        irq_rows.first.wait_for(state='visible')
        self.assertEqual(page.locator('.line-container[data-space=user]:visible').count(), 0)
        self.assertEqual(page.locator('.line-container[data-space=kernel]:visible').count(), irq_rows.count())
        self.assertEqual(errors, [])
        self.assertEqual(requests, [REPORT.as_uri()])

    def test_offline_kernel_entry_links_and_excerpts_in_both_views(self):
        page, errors, requests = self.open_page(REPORT.as_uri())
        page.get_by_role('button', name='Timeline', exact=True).click()
        line = page.evaluate('''() => {
            const model = fgView('timeline').probe();
            const index = Array.from({length: model.N}, (unused, candidate) => candidate)
                .find(candidate => model.nameOf(candidate) === '__x64_sys_sendto');
            model.select(index);
            return fgData.read('tlData').cols.line[index];
        }''')
        summary = page.locator('.tl-cs > summary', has_text='Call site source')
        summary.wait_for(state='visible')
        page.locator('.tl-cs-src .current-line', has_text='regs->ax = x64_sys_call').wait_for(state='visible')
        source = page.locator('[data-context-list=stack] [data-context-kind=traced]', has_text='__x64_sys_sendto').locator('.tl-context-source a')
        self.assertEqual(source.get_attribute('href'), 'https://github.com/torvalds/linux/blob/v7.4-rc2/net/socket.c#L2286')
        self.assertIn('function entry, not the calling line', source.get_attribute('title'))
        callsite_url = 'https://github.com/torvalds/linux/blob/v7.4-rc2/arch/x86/entry/syscall_64.c#L61'
        excerpt_link = page.locator('.tl-cs-src .src-call summary a.src-at')
        self.assertEqual(excerpt_link.get_attribute('href'), callsite_url)
        callsite_link = page.locator('.tl-cs-at [data-callsite-position]')
        self.assertEqual(callsite_link.get_attribute('href'), callsite_url)
        self.assertIn('do_syscall_64+0xb7/0x580', page.locator('.k:text-is("caller site") + .v').inner_text())
        self.assertEqual(page.locator('.k:has-text("return address")').count(), 0)
        self.assertEqual(page.evaluate('line => fgData.read("tlData").uftrace.kernel_details[String(line)].retaddr', line),
                         '0xffffffff82129117')
        page.context.route('https://github.com/torvalds/linux/**', lambda route: route.fulfill(
            status=200, content_type='text/html', body='<title>Kernel source</title>'))
        for link in (source, excerpt_link, callsite_link):
            expected = link.get_attribute('href')
            with page.expect_popup() as opened:
                link.click()
            popup = opened.value
            popup.wait_for_load_state()
            self.assertEqual(popup.url, expected)
            popup.close()
        self.assertEqual(page.locator('.tl-cs-at [data-callsite-position]').count(), 1)
        page.get_by_role('button', name='Text', exact=True).click()
        entry = page.locator(f'#L{line}')
        self.assertEqual(entry.locator('a.func-name-link').get_attribute('href'), source.get_attribute('href'))
        entry.click()
        self.assertIn('do_syscall_64+0xb7/0x580', entry.inner_text())
        page.locator('.expanded-content:visible .current-line', has_text='regs->ax = x64_sys_call').wait_for(state='visible')
        self.assertEqual(errors, [])
        self.assertEqual(requests, [REPORT.as_uri()])

    def test_offline_open_arguments_and_caller_offsets_are_readable(self):
        page, errors, requests = self.open_page(REPORT.as_uri())
        page.get_by_role('button', name='Timeline', exact=True).click()
        for occurrence, filename, flags, offset in ((0, 'input.bin', '0', '73'), (1, 'output.bin', '1', '8c')):
            page.evaluate('''occurrence => {
                const model = fgView('timeline').probe();
                const indices = Array.from({length: model.N}, (unused, index) => index)
                    .filter(index => model.nameOf(index) === 'open');
                model.select(indices[occurrence]);
            }''', occurrence)
            detail = page.locator('.tl-detail')
            self.assertEqual(detail.locator('.tl-argument').all_text_contents(), [
                f'pathname="/tmp/funcgraph-btf-values.OBCDn1/{filename}",', f'flags={flags}'])
            self.assertIn(f'main+0x{offset}/0x196', detail.locator('.k:text-is("caller site") + .v').inner_text())
            self.assertNotIn('offset unavailable', detail.inner_text())
            self.assertEqual(detail.locator('.k:text-is("raw argument bytes")').count(), 0)
            self.assertEqual(detail.locator('.k:text-is("caller return")').count(), 0)
            raw = detail.get_by_text('Original argument bytes', exact=True)
            self.assertFalse(raw.locator('..').evaluate('element => element.open'))
            raw.click()
            self.assertIn('uftrace built-in prototypes', detail.inner_text())
        self.assertEqual(errors, [])
        self.assertEqual(requests, [REPORT.as_uri()])

    def test_offline_named_kernel_arguments_and_distinct_user_call_sites(self):
        page, errors, requests = self.open_page(REPORT.as_uri())
        page.get_by_role('button', name='Timeline', exact=True).click()
        for occurrence, number in enumerate((47, 49)):
            line = page.evaluate('''occurrence => {
                const model = fgView('timeline').probe();
                const indices = Array.from({length: model.N}, (unused, candidate) => candidate)
                    .filter(candidate => model.nameOf(candidate) === 'receive_frame');
                model.select(indices[occurrence]);
                return fgData.read('tlData').cols.line[indices[occurrence]];
            }''', occurrence)
            page.locator('.tl-cs > summary', has_text='User call site source').wait_for(state='visible')
            self.assertIn(f'demo_mixed.c:{number}', page.locator('.tl-cs-src [data-source-position]').inner_text())
            stack = page.locator('[data-context-list=stack]')
            caller = stack.locator('[data-context-kind=traced]', has_text='exchange_loopback')
            self.assertIn(f'demo_mixed.c:{number}', caller.locator('.tl-source-open').text_content())
            callee = stack.locator('[data-context-kind=traced]', has_text='receive_frame')
            self.assertIn('demo_mixed.c:36', callee.text_content())
            self.assertEqual(callee.locator('.tl-source-open').count(), 0)
            self.assertEqual(page.locator('.k:has-text("user return") + .v').inner_text(), ('512', '-1')[occurrence])
            page.get_by_role('button', name='Text', exact=True).click()
            entry = page.locator(f'#L{line}')
            entry.click()
            position = page.locator('.expanded-content:visible [data-source-position]',
                                    has_text=f'demo_mixed.c:{number}')
            position.wait_for(state='visible')
            self.assertIn('user call site', position.inner_text())
            page.get_by_role('button', name='Timeline', exact=True).click()
        index = page.evaluate('''() => {
            const model = fgView('timeline').probe();
            const index = Array.from({length: model.N}, (unused, candidate) => candidate)
                .find(candidate => model.nameOf(candidate) === '__sys_sendto');
            model.select(index);
            return index;
        }''')
        arguments = page.locator('.k:has-text("args (BTF)") + .v')
        self.assertIn('len=0x200', arguments.inner_text())
        for name in ('fd', 'buff', 'len', 'flags', 'addr', 'addr_len'):
            self.assertIn(name + '=', arguments.inner_text())
        arguments.get_by_text('Raw argument slots', exact=True).click()
        data = helpers.json_script(REPORT.read_text(encoding='utf-8'), 'tlData')
        raw = data['strings'][data['cols']['args'][index]]
        self.assertIn(raw, arguments.locator('details').inner_text())
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
