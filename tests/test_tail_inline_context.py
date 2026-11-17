"""Repeated return sites share source context, not extra inline invocations."""

import csv
import io
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server


TRACE = ''' 0)               |  vfs_read() {
 0)               |    ext4_file_read_iter() { /* <-vfs_read+0x208/0x330 */
 0)               |      generic_file_read_iter() { /* <-vfs_read+0x208/0x330 */
 0)   0.001 us    |        filemap_read(); /* <-vfs_read+0x208/0x330 */
 0)   0.002 us    |      } /* generic_file_read_iter */
 0)   0.003 us    |    } /* ext4_file_read_iter */
 0)   0.004 us    |  } /* vfs_read */
'''


class TailInlineContextBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest(str(error))
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        root = Path(cls.kernel.dir)
        trace = root / 'tail-chain.txt'
        trace.write_text(TRACE)
        resolver = helpers.FAKE_INLINE_FADDR2LINE.replace('inline_helper', 'new_sync_read').replace('traced_outer', 'vfs_read')
        Path(cls.kernel.ksrc, 'scripts/faddr2line').write_text(resolver)
        cls.urls = {}
        for mode in ('single', 'sidecar'):
            destination = 'report.html' if mode == 'single' else 'report'
            cls.kernel.run(str(trace), '--no-config', '--no-btf', '--timeline', '--enable-fold',
                           '--report-mode', mode, '--base-url', 'https://source.invalid/src', out=destination)
            path = root / destination
            if mode == 'sidecar':
                server = create_server(path)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                cls.addClassCleanup(server.server_close)
                cls.addClassCleanup(server.shutdown)
                cls.urls[mode] = server.url
            else:
                cls.urls[mode] = path.as_uri()

    def open_report(self, mode='single', mutate=None):
        page = self.browser.new_page(viewport={'width': 1500, 'height': 950})
        self.addCleanup(page.close)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(self.urls[mode])
        page.evaluate("async () => { await fgData.load('tlData'); }")
        if mutate:
            page.evaluate('mutate => (' + mutate + ')(fgData.read("tlData"))', None)
        snapshot = page.evaluate("JSON.stringify(fgData.read('tlData'))")
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().inline(true); }")
        return page, snapshot

    def synthetic_names(self, page):
        return page.evaluate('''() => {
            const model = fgView('timeline').probe();
            return model.SYN.filter(frame => frame.chain).map(frame => frame.chain[frame.k][0]);
        }''')

    def export_stack(self, page):
        with page.expect_download() as pending:
            page.locator('[data-context-export=stack]').click()
        return list(csv.DictReader(io.StringIO(Path(pending.value.path()).read_text(encoding='utf-8-sig'))))

    def test_continuous_tail_chain_shows_one_inline_context_with_or_without_static_evidence(self):
        for mode in ('single', 'sidecar'):
            for supported in (False, True):
                with self.subTest(mode=mode, supported=supported):
                    mutate = None if not supported else '''data => {
                        data.tail_calls = {version:1, pairs:{}};
                        for (const index of [2,3]) {
                            const parent = data.cols.parent[index];
                            const key = [data.cols.name[parent], -1, data.cols.name[index], -1].join(':');
                            data.tail_calls.pairs[key] = {status:'supported', sites:[{
                                mnemonic:'jmp', symbol:data.strings[data.cols.name[parent]] + '+0x1/0x10', address:'0x1001'
                            }]};
                        }
                    }'''
                    page, snapshot = self.open_report(mode, mutate)
                    self.assertEqual(self.synthetic_names(page), ['new_sync_read'])
                    page.evaluate("fgView('timeline').probe().select(3)")
                    rows = self.export_stack(page)
                    self.assertEqual([row['function'] for row in rows],
                                     ['vfs_read', 'new_sync_read', 'ext4_file_read_iter', 'generic_file_read_iter', 'filemap_read'])
                    real = [row for row in rows if row['kind'] == 'traced']
                    self.assertEqual([row['recorded_slice_index'] for row in real], ['0', '1', '2', '3'])
                    self.assertEqual([row['source_context_owner_slice_index'] for row in real], ['', '1', '1', '1'])
                    self.assertEqual([row['source_context_basis'] for row in real],
                                     ['', 'recorded_return_site', 'shared_recorded_return_site', 'shared_recorded_return_site'])
                    self.assertEqual(page.locator('.tl-tail-tag').count(), 2 if supported else 0)
                    self.assertIn('Same recorded return site as ext4_file_read_iter', page.locator('.tl-detail').inner_text())
                    if not supported:
                        self.assertIn('Cause unresolved', page.locator('.tl-detail').inner_text())
                    self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), snapshot)
                    page.close()

    def test_shared_context_preserves_source_links_and_original_recorded_calls(self):
        page, snapshot = self.open_report()
        page.evaluate("fgView('timeline').probe().select(3)")
        self.assertIn('Shared return-site source', page.locator('.tl-cs > summary').text_content())
        self.assertEqual(page.locator('.tl-cs .source-line').count(), 5)
        self.assertEqual(page.locator('.tl-cs summary a').count(), 2)
        point = page.evaluate("fgView('timeline').probe().xyOf(3)")
        page.mouse.move(*point)
        self.assertIn('return-site inline context', page.locator('.tl-tip').inner_text())
        original = page.evaluate("JSON.parse(document.getElementById('tlData').textContent)")
        facts = page.evaluate('''() => {
            const model = fgView('timeline').probe();
            return {n:model.N, parent:Array.from(model.sParent).slice(0,model.N),
                depth:Array.from(model.sDepth).slice(0,model.N), duration:Array.from(model.sEnd).slice(0,model.N).map((end,index) => end-model.sStart[index])};
        }''')
        self.assertEqual(facts, dict(n=original['n'], duration=original['cols']['dur'],
                                    **{field: original['cols'][field] for field in ('parent', 'depth')}))
        page.evaluate("fgView('timeline').probe().inline(false)")
        self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 3)
        page.evaluate("fgView('timeline').probe().inline(true)")
        self.assertEqual(self.synthetic_names(page), ['new_sync_read'])
        self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), snapshot)

    def test_source_chain_identifies_shared_context_when_mismatch_flags_are_absent(self):
        page, snapshot = self.open_report(mutate='data => { data.cols.flags.fill(0); }')
        self.assertEqual(self.synthetic_names(page), ['new_sync_read'])
        page.evaluate("fgView('timeline').probe().select(3)")
        rows = self.export_stack(page)
        self.assertEqual([row['source_context_owner_slice_index'] for row in rows if row['kind'] == 'traced'], ['', '1', '1', '1'])
        self.assertEqual(page.locator('.tl-tail-tag').count(), 0)
        self.assertIn('Shared return-site source', page.locator('.tl-cs > summary').text_content())
        self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), snapshot)

    def test_recursion_different_return_sites_gaps_contexts_modules_and_invalid_extents_are_not_deduplicated(self):
        cases = {
            'recursion': 'data.cols.flags.fill(0); data.cols.name[1] = data.cols.name[0]; data.cols.name[2] = data.cols.name[0]',
            'recursive_mismatch': 'data.cols.name[2] = data.cols.name[1]',
            'missing_caller': 'data.cols.caller[2] = -1',
            'other_return_site': "data.strings.push('vfs_read+0x210/0x330'); const caller = data.strings.length - 1; data.inline[caller] = data.inline[data.cols.caller[2]]; data.cols.caller[2] = caller",
            'depth_gap': 'data.cols.depth[2] += 1; data.cols.depth[3] += 1',
            'irq_context': 'data.cols.flags[2] |= 2; data.cols.flags[3] |= 2',
            'user_kernel': 'data.cols.flags[2] |= 1 << 25; data.cols.flags[3] |= 1 << 25',
            'boundary': 'data.cols.flags[2] |= 1 << 26',
            'orphan': 'data.cols.flags[2] |= 1 << 17',
            'module': 'data.cols.module = [-1,-1,0,0]',
            'different_task': 'data.tasks.push({...data.tasks[0], pid:99}); data.cols.task[2] = 1; data.cols.task[3] = 1',
            'outliving_parent': 'data.cols.dur[2] = 100000',
        }
        for name, mutation in cases.items():
            with self.subTest(name=name):
                page, snapshot = self.open_report(mutate='data => {' + mutation + '}')
                self.assertGreaterEqual(len(self.synthetic_names(page)), 2)
                self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), snapshot)
                page.close()

    def test_siblings_remain_independent_source_owners_under_existing_inline_grouping(self):
        page, snapshot = self.open_report(mutate='''data => {
            data.cols.parent[2] = 0; data.cols.parent[3] = 0;
            data.cols.depth = [0,1,1,1]; data.cols.flags.fill(0);
            data.cols.start = [0,1000,5000,5000]; data.cols.dur = [15000,1000,1000,1000];
        }''')
        self.assertEqual(self.synthetic_names(page), ['new_sync_read'])
        for index in (1, 2, 3):
            page.evaluate("index => fgView('timeline').probe().select(index)", index)
            rows = self.export_stack(page)
            self.assertEqual(rows[-1]['source_context_owner_slice_index'], str(index))
            self.assertEqual(rows[-1]['source_context_basis'], 'recorded_return_site')
        self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), snapshot)

    def test_long_tail_chains_keep_one_context_and_export_every_recorded_call(self):
        page, snapshot = self.open_report(mutate='''data => {
            const count = 2000, caller = data.cols.caller[1], root = data.cols.name[0];
            const first = data.cols.name[1], second = data.cols.name[2];
            data.n = count;
            data.mode = 'abs';
            data.cols = {name:Array.from({length:count}, (_,index) => index ? index % 2 ? first : second : root),
                task:Array(count).fill(0), depth:Array.from({length:count}, (_,index) => index),
                start:Array.from({length:count}, (_,index) => index ? 1 : 0), dur:Array.from({length:count}, (_,index) => count * 2-index * 2),
                parent:Array.from({length:count}, (_,index) => index-1), line:Array.from({length:count}, (_,index) => index+1),
                flags:Array.from({length:count}, (_,index) => index > 1 ? 1 << 19 : 0),
                caller:Array.from({length:count}, (_,index) => index ? caller : -1),
                ret:null, args:null, module:null, cpu:null, adj:null};
        }''')
        self.assertEqual(self.synthetic_names(page), ['new_sync_read'])
        page.evaluate("fgView('timeline').probe().select(1999)")
        self.assertEqual(page.locator('[data-context-list=stack] .tl-context-row').count(), 100)
        rows = self.export_stack(page)
        self.assertEqual(len(rows), 2001)
        self.assertEqual(sum(row['kind'] == 'traced' for row in rows), 2000)
        self.assertEqual(rows[-1]['source_context_owner_slice_index'], '1')
        self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), snapshot)


if __name__ == '__main__':
    unittest.main()
