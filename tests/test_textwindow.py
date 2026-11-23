"""Windowed Text retains complete trace semantics with bounded row DOM."""
from pathlib import Path
import json
import re
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TextWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        trace = Path(cls.kernel.dir) / 'trace.txt'
        trace.write_text('# tracer: function_graph\n'
                         ' 3)               |  root() { /* <-main+0x4/0x80 */\n'
                         ' 3)               |    inner() { /* <-root+0x4/0x80 */\n' +
                         ''.join(f' 3) {number % 17 + 1:7.3f} us    |      leaf(arg={number}); /* <-inner+0x4/0x80 */\n'
                                 for number in range(9000)) +
                         ' 3)   0.000 us    |    } /* inner ret=0 */\n'
                         ' 3) 300.000 us    |  } /* root ret=0 */\n')
        entry, _ = cls.kernel.run(str(trace), '--no-config', '--timeline', '--enable-fold', '--filter', '--func-links',
                                  '--report-mode', 'sidecar', out='report')
        (Path(cls.kernel.dir) / 'report/adaptive.html').write_text(entry.replace(
            'id="fgTextChunks">', 'id="fgTextChunks" data-default-renderer="windowed">', 1), encoding='utf-8')
        mixed = [json.loads(line) for line in (Path(helpers.ROOT) / 'samples/sample_mixed.jsonl').read_text().splitlines()]
        records = [record for record in mixed if record.get('type') not in ['meta', 'end'] and 'time_ns' in record]
        span = records[-1]['time_ns'] - records[0]['time_ns'] + 1000000
        mixed_trace = Path(cls.kernel.dir) / 'mixed.jsonl'
        with mixed_trace.open('w') as stream:
            stream.write(json.dumps(mixed[0]) + '\n')
            for repetition in range(4):
                for record in records:
                    stream.write(json.dumps(dict(record, time_ns=record['time_ns'] + repetition * span)) + '\n')
            stream.write(json.dumps(dict(type='end', complete=True)) + '\n')
        cls.kernel.run(str(mixed_trace), '--no-config', '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--user-src', str(Path(helpers.ROOT) / 'samples'), '--report-mode', 'sidecar', out='report/mixed')
        task_trace = Path(cls.kernel.dir) / 'tasks.txt'
        task_trace.write_text('# tracer: function_graph\n' + ''.join(
            f'  {number / 1000000:.6f} |   {cpu}) worker-{pid} |  ...1. |   1.000 us    |  leaf(arg={number}); /* <-inner+0x4/0x80 */\n'
            for number, cpu, pid in [(number, 3, 101) for number in range(1, 4201)] +
                                    [(number, 7, 202) for number in range(4201, 7801)]))
        cls.kernel.run(str(task_trace), '--no-config', '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--report-mode', 'sidecar', out='report/tasks')
        scattered = Path(cls.kernel.dir) / 'scattered.txt'
        scattered.write_text('# tracer: function_graph\n' + ''.join(
            f' 3) {number * 7919 % 24576 + 1:7.3f} us    |  leaf(arg={number}); /* <-root+0x4/0x80 */\n'
            for number in range(24576)))
        cls.kernel.run(str(scattered), '--no-config', '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--report-mode', 'sidecar', out='report/scattered')
        scattered_events = Path(cls.kernel.dir) / 'scattered-events.txt'
        scattered_events.write_text(scattered.read_text() + ' 3)               |  /* sched_wakeup: comm=worker pid=43 target_cpu=3 */\n')
        cls.kernel.run(str(scattered_events), '--no-config', '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--report-mode', 'sidecar', out='report/scattered-events')
        cls.server = create_server(Path(cls.kernel.dir) / 'report')
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            cls.playwright.stop()
            cls.server.shutdown()
            cls.thread.join(timeout=5)
            cls.server.server_close()
            cls.kernel.close()
            raise unittest.SkipTest(str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
        cls.kernel.close()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 900})
        self.page = self.context.new_page()
        self.errors = []
        self.requests = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda request: self.requests.append(request.url))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open_report(self, windowed=True):
        self.page.goto(urljoin(self.server.url, 'index.html' + ('?text=windowed' if windowed else '')))
        result = self.page.evaluate('async () => !!(await fgSwitchView("text"))')
        self.assertTrue(result, self.page.locator('#content').inner_text())
        self.page.wait_for_function('!document.getElementById("content").hasAttribute("aria-busy")')

    def mounted(self):
        return self.page.locator('#content .line-container').count()

    def open_gated_duration_report(self):
        self.page.goto(urljoin(self.server.url, 'scattered/index.html?text=windowed'))
        self.assertTrue(self.page.evaluate('async () => !!(await fgSwitchView("text"))'))
        self.page.evaluate('''() => {
            window.durationGates=[];
            const original=fgTextModel.rankDurationAsync;
            fgTextModel.rankDurationAsync=(model,plan,direction,signal,progress)=>new Promise((resolve,reject)=>{
                durationGates.push({direction,signal,progress,fail:reject,
                    release:async()=>{try {resolve(await original(model,plan,direction,undefined,progress))} catch(error) {reject(error)}}});
            });
            window.beginDuration=direction=>{
                fgTextSort=direction;
                window.durationWork=fgTextWindow.rebuild().then(()=>true,error=>error.name);
            };
        }''')

    def open_task_report(self):
        self.page.goto(urljoin(self.server.url, 'tasks/index.html?text=windowed'))
        groups = self.page.evaluate('''async () => {
            await fgSwitchView('text'); await fgSetTextOrder('task');
            const plan=fgTextModel.order(fgTextWindow.model,'task');
            return plan.boundaries.filter(item=>item.kind==='task-group-head')
                .map(item=>({text:item.text,number:plan.order[item.at+1000],first:plan.order[item.at]}));
        }''')
        self.assertEqual(len(groups), 2)
        return groups

    def seek_task_context(self, number):
        self.page.evaluate('''async number => {
            const row=await fgEnsureTextLine(number);
            row.scrollIntoView({block:'center',behavior:'instant'});
        }''', number)
        self.page.wait_for_timeout(100)
        return self.page.evaluate('''() => {
            const context=document.querySelector('.fg-window-context'), rect=context?.getBoundingClientRect();
            return {text:context?.textContent,hidden:context?.hidden,top:rect?.top,width:rect?.width,
                controls:document.querySelector('.controls').getBoundingClientRect().bottom,
                headings:document.querySelectorAll('#content .task-group-head').length,
                contexts:document.querySelectorAll('.fg-window-context').length,
                rows:document.querySelectorAll('#content .line-container').length};
        }''')

    def test_task_context_survives_eviction_and_follows_both_group_directions(self):
        groups = self.open_task_report()
        for group in [groups[0], groups[1], groups[0]]:
            facts = self.seek_task_context(group['number'])
            self.assertEqual(facts.get('text'), group['text'])
            self.assertFalse(facts['hidden'])
            self.assertEqual(facts['contexts'], 1)
            self.assertEqual(facts['headings'], 0)
            self.assertAlmostEqual(facts['top'], facts['controls'], delta=1)
            self.assertLessEqual(facts['rows'], 160)

    def test_task_context_changes_at_viewport_boundary_not_selected_row(self):
        groups = self.open_task_report()
        self.seek_task_context(groups[1]['first'])
        self.page.evaluate('''() => {
            const heading=document.querySelector('#content .task-group-head');
            const inset=document.querySelector('.controls').getBoundingClientRect().bottom;
            scrollTo({top:scrollY+heading.getBoundingClientRect().top-inset+2,behavior:'instant'});
        }''')
        self.page.wait_for_function('text => !document.querySelector(".fg-window-context").hidden && document.querySelector(".fg-window-context").textContent===text', arg=groups[1]['text'])
        self.page.evaluate('scrollBy({top:-60,behavior:"instant"})')
        self.page.wait_for_function('text => document.querySelector(".fg-window-context").textContent===text', arg=groups[0]['text'])
        self.assertLessEqual(self.mounted(), 160)

    def test_in_window_scroll_reads_one_offset_and_keeps_task_context_stable(self):
        groups = self.open_task_report()
        for order in ('task', 'time'):
            with self.subTest(order=order):
                self.page.evaluate('async order => await fgSetTextOrder(order)', order)
                self.seek_task_context(groups[0]['number'])
                result = self.page.evaluate('''async () => {
                    const root = document.getElementById('content');
                    const toolbar = document.querySelector('.controls');
                    const caption = document.querySelector('.fg-window-context');
                    const readers = {root: 0, toolbar: 0};
                    for (const [name, element] of Object.entries({root, toolbar})) {
                        const bounds = element.getBoundingClientRect.bind(element);
                        element.getBoundingClientRect = () => { readers[name]++; return bounds(); };
                    }
                    const rows = () => [...root.querySelectorAll('.line-container')];
                    const before = rows(), position = scrollY, text = caption.textContent;
                    const observer = new MutationObserver(records => mutations += records.length);
                    let mutations = 0;
                    observer.observe(caption, {attributes: true, childList: true, subtree: true});
                    try {
                        for (let step = 0; step < 5; step++) {
                            scrollBy({top: 12, behavior: 'instant'});
                            await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
                        }
                        const after = rows();
                        return {readers, mutations, scrolled: scrollY - position,
                            hidden: caption.hidden, sameLabel: text === caption.textContent,
                            kept: before.length === after.length && before.every((row, index) => row === after[index])};
                    } finally {
                        delete root.getBoundingClientRect;
                        delete toolbar.getBoundingClientRect;
                        observer.disconnect();
                    }
                }''')
                self.assertEqual(result, dict(readers=dict(root=5, toolbar=5 if order == 'task' else 0),
                                              mutations=0, scrolled=60, hidden=order == 'time', sameLabel=True, kept=True))

    def test_task_context_does_not_cover_nearest_keyboard_navigation(self):
        groups = self.open_task_report()
        self.seek_task_context(groups[0]['number'])
        covered = self.page.evaluate('''async number => {
            const row=document.getElementById('L'+number);
            const caption=document.querySelector('.fg-window-context > .task-group-head');
            scrollBy({top:row.getBoundingClientRect().top-caption.getBoundingClientRect().bottom+1,behavior:'instant'});
            await new Promise(requestAnimationFrame);
            return row.getBoundingClientRect().top<caption.getBoundingClientRect().bottom;
        }''', groups[0]['number'])
        self.assertTrue(covered)
        self.page.evaluate('number => fgSelectMountedTextLine(document.getElementById("L"+number),true)', groups[0]['number'])
        self.page.wait_for_timeout(500)
        facts = self.page.evaluate('''number => ({top:document.getElementById('L'+number).getBoundingClientRect().top,
            caption:document.querySelector('.fg-window-context > .task-group-head').getBoundingClientRect().bottom})''', groups[0]['number'])
        self.assertGreaterEqual(facts['top'], facts['caption'])

    def test_task_context_resets_for_filter_empty_duration_and_time_order(self):
        groups = self.open_task_report()
        self.seek_task_context(groups[1]['number'])
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value='pid:202'; await applyFilter();
        }''')
        self.assertEqual(self.seek_task_context(groups[1]['number'])['text'], groups[1]['text'])
        for query in ['func:^absent$', 'func:^leaf$ sort:desc']:
            self.page.evaluate('''async query => {
                document.getElementById('filterInput').value=query; await applyFilter();
            }''', query)
            self.assertTrue(self.page.locator('.fg-window-context').evaluate('element=>element.hidden'), query)
        self.page.evaluate('async () => { await clearFilter(); await fgSetTextOrder("time"); }')
        self.assertTrue(self.page.locator('.fg-window-context').evaluate('element=>element.hidden'))
        self.page.evaluate('async () => await fgSetTextOrder("task")')
        self.assertEqual(self.seek_task_context(groups[0]['number'])['text'], groups[0]['text'])

    def test_task_context_is_not_copied_printed_or_leaked_across_views_and_full_text(self):
        groups = self.open_task_report()
        self.seek_task_context(groups[0]['number'])
        facts = self.page.evaluate('''async () => {
            const context=document.querySelector('.fg-window-context');
            Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.taskCopied=text}});
            await copyVisibleContent({target:document.createElement('button')});
            const expected=fgTextWindow.visibleNumbers.map(number=>fgTextModel.locate(fgTextWindow.model,number).record.textContent).join('\\n');
            await fgSwitchView('timeline');
            const hidden=context.hidden;
            await fgSwitchView('text');
            await new Promise(requestAnimationFrame);
            return {copied:taskCopied===expected,hidden,resumed:!context.hidden,
                contexts:document.querySelectorAll('.fg-window-context').length};
        }''')
        self.assertEqual(facts, dict(copied=True, hidden=True, resumed=True, contexts=1))
        self.page.emulate_media(media='print')
        self.assertEqual(self.page.locator('.fg-window-context').evaluate('element=>getComputedStyle(element).display'), 'none')
        self.page.emulate_media(media='screen')
        self.page.evaluate('async () => await fgTextWindow.fullText()')
        self.assertEqual(self.page.locator('.fg-window-context').count(), 0)
        self.assertGreater(self.mounted(), 7000)

    def test_task_context_retains_previous_group_when_remote_loading_fails(self):
        groups = self.open_task_report()
        self.seek_task_context(groups[0]['number'])
        facts = self.page.evaluate('''async number => {
            const load=fgData.loadTextBlocks;
            fgData.loadTextBlocks=async ()=>{throw new Error('injected task window failure')};
            let failed=false;
            try { await fgEnsureTextLine(number); } catch(error) {failed=error.message.includes('injected task window failure')}
            finally {fgData.loadTextBlocks=load}
            return {failed,text:document.querySelector('.fg-window-context').textContent,
                hidden:document.querySelector('.fg-window-context').hidden};
        }''', groups[1]['number'])
        self.assertEqual(facts, dict(failed=True, text=groups[0]['text'], hidden=False))
        self.assertEqual(self.seek_task_context(groups[1]['number'])['text'], groups[1]['text'])

    def test_task_context_stays_readable_on_narrow_dark_horizontal_scroll_and_toolbar_resize(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width':320,'height':740}, is_mobile=True, has_touch=True)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.add_init_script('localStorage.setItem("theme","dark")')
        groups = self.open_task_report()
        facts = self.seek_task_context(groups[1]['number'])
        self.assertLessEqual(facts['width'], 320)
        self.assertAlmostEqual(facts['top'], facts['controls'], delta=1)
        horizontal = self.page.evaluate('''async () => {
            const context=document.querySelector('.fg-window-context');
            const left=context.getBoundingClientRect().left;
            const viewport=document.getElementById('textViewport'); viewport.scrollLeft=10000;
            await new Promise(requestAnimationFrame);
            return {left,after:context.getBoundingClientRect().left,scroll:viewport.scrollLeft,
                overflow:document.documentElement.scrollWidth>innerWidth,
                title:context.firstElementChild.title,text:context.textContent};
        }''')
        self.assertGreater(horizontal['scroll'], 0)
        self.assertEqual(horizontal['left'], horizontal['after'])
        self.assertFalse(horizontal['overflow'])
        self.assertEqual(horizontal['title'], groups[1]['text'])
        self.assertEqual(horizontal['text'], groups[1]['text'])
        self.page.evaluate('document.querySelector(".controls").style.display="none"')
        self.page.wait_for_function('() => document.querySelector(".fg-window-context").getBoundingClientRect().top===0')
        self.page.evaluate('document.querySelector(".controls").style.display=""')
        self.page.wait_for_function('''() => Math.abs(document.querySelector('.fg-window-context').getBoundingClientRect().top-
            document.querySelector('.controls').getBoundingClientRect().bottom)<1''')
        self.assertLessEqual(self.mounted(), 160)

    def test_cold_text_mounts_only_one_bounded_window_and_one_network_chunk(self):
        self.open_report()
        facts = self.page.evaluate('''() => ({records:fgTextWindow.model.records.length,
            navigation:fgTextNavigation.order.length, version:fgTextWindow.model.version,
            sources:document.querySelectorAll('#content .expanded-content').length,
            sourceJobs:fgSourceJobs.size, count:total_lines, deferred:fgTextDeferred()})''')
        self.assertGreater(facts['records'], 9000)
        self.assertEqual(facts['records'], facts['navigation'])
        self.assertEqual(facts['records'], facts['count'])
        self.assertEqual(facts['version'], 5)
        self.assertLessEqual(self.mounted(), 160)
        self.assertLessEqual(facts['sources'], 160)
        self.assertEqual(facts['sourceJobs'], 0)
        self.assertFalse(facts['deferred'])
        chunks = {url.rsplit('/', 1)[-1] for url in self.requests if '/text-' in url and url.endswith('.html')}
        self.assertEqual(chunks, {'text-0.html'})

    def test_initial_shared_filter_keeps_saved_source_temporarily_collapsed(self):
        self.page.goto(urljoin(self.server.url, 'index.html?text=windowed'))
        facts = self.page.evaluate('''async () => {
            const steps=fgTextModel.prepare(await fgData.load('fgTextIndex')); let result;
            do {result=steps.next();} while(!result.done);
            const loaded=await fgLoadTextBlock(result.value,100), id=loaded.line.dataset.lineId;
            expandedLines[id]=true;
            fgNotifyFilter('func:^leaf$',null);
            const opened=!!await fgSwitchView('text');
            const row=await fgEnsureTextLine(100);
            return {opened,saved:expandedLines[id],display:row.nextElementSibling.style.display,query:fgFilterState.text};
        }''')
        self.assertEqual(facts, dict(opened=True, saved=True, display='none', query='func:^leaf$'))

    def test_partial_final_initialization_requires_reload_not_listener_reuse(self):
        self.page.goto(urljoin(self.server.url, 'index.html?text=windowed'))
        facts = self.page.evaluate('''async () => {
            fgInitText=()=>{fgTextInitialized=true;throw new Error('injected final init failure')};
            const opened=await fgSwitchView('text');
            return {opened,buttons:[...document.querySelectorAll('#content button')].map(button=>button.textContent),windowed:!!fgTextWindow};
        }''')
        self.assertIsNone(facts['opened'])
        self.assertIn('Reload report', facts['buttons'])
        self.assertFalse(facts['windowed'])
        self.page.reload()
        self.assertTrue(self.page.evaluate('async () => !!await fgSwitchView("text")'))
        self.assertLessEqual(self.mounted(), 160)

    def test_navigation_crosses_network_boundaries_and_preserves_original_identity(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const numbers = [5001, 8990, 33];
            const result = [];
            for (const number of numbers) {
                const row = await fgEnsureTextLine(number);
                fgSelectMountedTextLine(row, false);
                row.scrollIntoView({block:'center', behavior:'instant'});
                await new Promise(resolve => setTimeout(resolve, 80));
                result.push({id:row.id, text:row.querySelector('.line-content').textContent,
                    connected:row.isConnected, mounted:document.querySelectorAll('#content .line-container').length});
            }
            return result;
        }''')
        for number, fact in zip([5001, 8990, 33], facts):
            self.assertEqual(fact['id'], f'L{number}')
            self.assertTrue(fact['connected'])
            self.assertLessEqual(fact['mounted'], 160)
            self.assertIn('leaf(', fact['text'])

    def test_filter_matches_unmounted_rows_and_clear_restores_complete_visibility(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            document.getElementById('filterInput').value = 'params:arg=8999';
            const applied = await applyFilter();
            const matches = [...fgFilterState.lines];
            const rows = [...document.querySelectorAll('#content .line-container')].map(row => +row.dataset.lineNumber);
            await clearFilter();
            return {applied, matches, rows, visible:fgTextWindow.visibleNumbers.length, total:total_lines};
        }''')
        self.assertTrue(facts['applied'])
        self.assertEqual(len(facts['matches']), 1)
        self.assertEqual(facts['rows'], facts['matches'])
        self.assertEqual(facts['visible'], facts['total'])
        self.assertLessEqual(self.mounted(), 160)

    def test_fold_hides_complete_descendants_and_keeps_zero_return_in_summary(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const folding = fgTextModel.folding(fgTextWindow.model);
            const block = [...folding.blocks.values()].find(item => item.entry?.getAttribute('data-func') === 'inner');
            _setFolded(block.id, true);
            await fgRefreshTextWindow();
            const summary = document.getElementById('L'+block.entry.row[0]).querySelector('.fold-summary').textContent;
            const folded = fgTextWindow.visibleNumbers.length;
            _setFolded(block.id, false);
            await fgRefreshTextWindow();
            return {summary, folded, unfolded:fgTextWindow.visibleNumbers.length, total:total_lines};
        }''')
        self.assertIn('9000 calls', facts['summary'])
        self.assertIn('ret=0', facts['summary'])
        self.assertLess(facts['folded'], 20)
        self.assertEqual(facts['unfolded'], facts['total'])

    def test_full_text_fallback_restores_all_rows_and_keeps_filter_and_folds(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            document.getElementById('filterInput').value = 'params:arg=8999';
            await applyFilter();
            const expected = [...fgFilterState.lines];
            const renderer = fgTextWindow;
            const previousBody = document.querySelector('.fg-window-rows');
            document.getElementById('content').setAttribute('aria-busy','true');
            await renderer.fullText();
            return {expected, windowed:!!fgTextWindow, total:total_lines, released:previousBody.childElementCount===0,
                busy:document.getElementById('content').hasAttribute('aria-busy'),
                rows:document.querySelectorAll('#content .line-container').length,
                visible:[...document.querySelectorAll('#content .line-container')].filter(row => row.style.display !== 'none')
                    .map(row => +row.dataset.lineNumber), last:(await renderer.ensureLine(total_lines)).id};
        }''')
        self.assertFalse(facts['windowed'])
        self.assertTrue(facts['released'])
        self.assertFalse(facts['busy'])
        self.assertEqual(facts['rows'], facts['total'])
        self.assertEqual(facts['visible'], facts['expected'])
        self.assertEqual(facts['last'], f'L{facts["total"]}')

    def test_full_text_time_handoff_reuses_authored_chunks_and_resets_the_window_order(self):
        self.page.goto(urljoin(self.server.url, 'scattered/index.html?text=windowed'))
        self.assertTrue(self.page.evaluate('async () => !!(await fgSwitchView("text"))'))
        facts = self.page.evaluate('''async () => {
            const renderer = fgTextWindow, root = renderer.root;
            const expected = renderer.model.records.map(record => record.row[0]);
            fgTextSort = 'desc';
            await renderer.rebuild(expected[0]);
            const wasSorted = fgTextNavigation.order.some((number, index) => number !== expected[index]);
            const original = fgLoadTextBlock, chunks = [];
            fgLoadTextBlock = async (...args) => {
                const result = await original(...args);
                chunks.push(result.element);
                return result;
            };
            try { await renderer.fullText(); } finally { fgLoadTextBlock = original; }
            const rows = Array.from(root.querySelectorAll('.line-container'), row => +row.dataset.lineNumber);
            return {wasSorted, whole:rows.length === expected.length,
                authored:chunks.length === root.childElementCount && chunks.every((chunk, index) => root.children[index] === chunk),
                ordered:rows.every((number, index) => number === expected[index]),
                navigation:JSON.stringify(fgTextNavigation.order) === JSON.stringify(expected),
                sort:fgTextSort, label:document.getElementById('textOrderBtn').textContent};
        }''')
        self.assertEqual(facts, dict(wasSorted=True, whole=True, authored=True, ordered=True,
                                    navigation=True, sort='', label='Order: time'))

    def test_unfiltered_full_text_reuses_validated_row_counts_and_visibility(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const renderer = fgTextWindow, root = renderer.root, navigation = fgTextNavigation;
            let projectedRows = 0;
            const project = navigation.setVisible;
            navigation.setVisible = function (...args) { projectedRows++; return project.apply(this, args); };
            try { await renderer.fullText(); } finally { navigation.setVisible = project; }
            const counts = {plain: 0, empty: 0, expandable: 0};
            let chunks = 0, matched = true;
            for (const chunk of root.children) {
                if (!chunk.classList.contains('text-chunk')) continue;
                chunks++;
                const expected = {plain: 0, empty: 0, expandable: 0};
                for (const row of chunk.children) {
                    if (row.classList.contains('line-container') && row.style.display !== 'none') {
                        expected[row.classList.contains('expandable') ? 'expandable' :
                            row.querySelector('.line-content')?.hasChildNodes() ? 'plain' : 'empty']++;
                    }
                }
                for (const [kind, value] of Object.entries(expected)) {
                    counts[kind] += value;
                    matched &&= chunk.style.getPropertyValue('--fg-text-' + kind + '-rows') === String(value);
                }
            }
            return {projectedRows, chunks, matched, count: counts.plain + counts.empty + counts.expandable,
                visible: navigation.visibleCount, expected: total_lines, deferred: root.classList.contains('text-layout-auto')};
        }''')
        self.assertTrue(facts['deferred'] and facts['matched'], facts)
        self.assertGreater(facts['chunks'], 1)
        self.assertEqual(facts['projectedRows'], 0)
        self.assertEqual(facts['count'], facts['expected'])
        self.assertEqual(facts['visible'], facts['expected'])

    def test_filtered_full_text_reuses_the_committed_filter_and_chunk_counts(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const input = document.getElementById('filterInput');
            input.value = 'params:arg=8999';
            await applyFilter();
            const state = fgFilterState, renderer = fgTextWindow, root = renderer.root;
            const visibleBefore = renderer.visibleNumbers;
            const originalFilter = applyFilter;
            applyFilter = () => { throw new Error('Committed filter evaluated again'); };
            try { await renderer.fullText(); } finally { applyFilter = originalFilter; }
            const rows = Array.from(root.querySelectorAll('.line-container'));
            const visible = rows.filter(row => row.style.display !== 'none').map(row => +row.dataset.lineNumber);
            const navigation = fgTextNavigation.visible.map(index => fgTextNavigation.order[index]);
            const counted = Array.from(root.children, chunk => ['plain', 'empty', 'expandable'].reduce((sum, kind) =>
                sum + Number(chunk.style.getPropertyValue('--fg-text-' + kind + '-rows')), 0));
            const countsMatch = Array.from(root.children).every((chunk, index) => counted[index] ===
                Array.from(chunk.querySelectorAll('.line-container')).filter(row => row.style.display !== 'none').length);
            return {sameState:state === fgFilterState, all:rows.length === total_lines, countsMatch,
                visible, visibleBefore, navigation, rendererCleared:!fgTextWindow};
        }''')
        self.assertTrue(all(facts[key] for key in ('sameState', 'all', 'countsMatch', 'rendererCleared')), facts)
        self.assertEqual(len(facts['visible']), 1)
        self.assertEqual(facts['visible'], facts['visibleBefore'])
        self.assertEqual(facts['visible'], facts['navigation'])

    def test_full_text_discards_prepared_visibility_if_filter_draft_changes(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const input = document.getElementById('filterInput');
            const hidden = document.querySelector('#content .line-container.expandable');
            const sourceId = hidden.nextElementSibling.id;
            toggleExpand(hidden.dataset.lineId);
            input.value = 'params:arg=8999';
            await applyFilter();
            const renderer = fgTextWindow, original = fgData.loadTextBlocks;
            let changed = false;
            fgData.loadTextBlocks = (rows, options) => {
                if (rows[0] > 0 && !changed) { input.value = ''; changed = true; }
                return original(rows, options);
            };
            try { await renderer.fullText(); } finally { fgData.loadTextBlocks = original; }
            const rows = Array.from(renderer.root.querySelectorAll('.line-container'));
            return {changed, clear:fgFilterState.lines === null, all:rows.length === total_lines,
                visible:rows.every(row => row.style.display === ''), restored:document.getElementById(sourceId)?.style.display};
        }''')
        self.assertEqual(facts, dict(changed=True, clear=True, all=True, visible=True, restored='block'))

    def test_full_text_handoff_retains_task_groups_or_explicit_duration_sort(self):
        for query in ('', 'sort:desc'):
            with self.subTest(query=query):
                self.open_task_report()
                facts = self.page.evaluate('''async query => {
                    const input = document.getElementById('filterInput'); input.value = query;
                    await applyFilter();
                    const renderer = fgTextWindow;
                    const expected = fgTextNavigation.order.slice();
                    await renderer.fullText();
                    const rows = Array.from(renderer.root.querySelectorAll('.line-container'), row => +row.dataset.lineNumber);
                    return {order:fgTextOrder, sort:fgTextSort, filter:fgFilterState.text,
                        groups:renderer.root.querySelectorAll('.task-group-head').length,
                        rows:JSON.stringify(rows) === JSON.stringify(expected),
                        navigation:JSON.stringify(fgTextNavigation.order) === JSON.stringify(expected)};
                }''', query)
                self.assertEqual(facts, dict(order='task', sort='desc' if query else '', filter=query,
                                            groups=0 if query else 2, rows=True, navigation=True))

    def test_default_text_remains_complete_and_has_explicit_experimental_entry(self):
        self.open_report(windowed=False)
        self.assertGreater(self.mounted(), 9000)
        self.assertTrue(self.page.locator('#textWindowBtn').is_visible())
        self.assertTrue(self.page.evaluate('fgTextWindow === null'))

    def test_first_deep_full_text_jump_keeps_reading_position_in_windowed_text(self):
        for touch in (False, True):
            with self.subTest(touch=touch):
                if touch:
                    self.context.close()
                    self.context = self.browser.new_context(viewport={'width': 390, 'height': 900},
                                                            is_mobile=True, has_touch=True)
                    self.page = self.context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page.goto(urljoin(self.server.url, 'adaptive.html?text=full'))
                self.assertTrue(self.page.evaluate('async () => !!(await fgSwitchView("text"))'))
                self.page.add_style_tag(content='#content { min-width: 2400px; }')
                facts = self.page.evaluate('''async () => {
                    const content = document.getElementById('content');
                    const viewport = document.getElementById('textViewport');
                    const row = document.getElementById('L8500');
                    viewport.scrollLeft = 100;
                    fgScrollTextLine(row, {block:'center', behavior:'instant'});
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    const bounds = row.getBoundingClientRect();
                    const hit = document.elementFromPoint(viewport.getBoundingClientRect().left + 12,
                        bounds.top + bounds.height / 2)?.closest('.line-container');
                    return {deferred:!fgTextWindow && content.classList.contains('text-layout-auto'),
                        hit:hit?.id, left:viewport.scrollLeft, anchor:fgTextViewportAnchor(content, viewport)};
                }''')
                self.assertTrue(facts['deferred'], facts)
                self.assertEqual(facts['left'], 100, facts)
                self.assertEqual(facts.get('hit'), 'L8500', facts)
                self.assertIsNotNone(facts['anchor'], facts)
                self.assertGreater(facts['anchor']['number'], 8192)
                if touch:
                    self.page.get_by_role('button', name='More', exact=True).tap()
                button = self.page.get_by_role('button', name='Windowed Text', exact=True)
                (button.tap if touch else button.click)()
                self.page.wait_for_function('() => fgTextWindow !== null && !fgWindowHandoff')
                restored = self.page.evaluate('''() => {
                    const content = document.getElementById('content');
                    const viewport = document.getElementById('textViewport');
                    return {anchor:fgTextViewportAnchor(content, viewport), target:!!document.getElementById('L8500')};
                }''')
                self.assertTrue(restored['target'], restored)
                self.assertEqual(restored['anchor']['number'], facts['anchor']['number'], restored)
                self.assertLessEqual(self.mounted(), 160)

    def test_full_text_switches_to_window_in_place_at_deep_scroll(self):
        self.open_report(windowed=False)
        line = self.page.evaluate('''async () => {
            const timeline = await fgSwitchView('timeline');
            await fgSwitchView('text');
            const row = document.getElementById('L8500');
            row.scrollIntoView({block:'center'});
            const root = row.closest('#content'), viewport = root.parentElement;
            const anchor = fgTextViewportAnchor(root, viewport);
            window.handoff = {timeline, navigation: fgTextNavigation, model: fgDomTextModels.get(root),
                anchor, old: document.getElementById('L' + anchor.number), url: location.href, history: history.length};
            return anchor.number;
        }''')
        self.assertGreater(line, 8192)
        self.requests.clear()
        self.page.get_by_role('button', name='Windowed Text', exact=True).click()
        self.page.wait_for_function('() => fgTextWindow && !fgWindowHandoff')
        self.assertLessEqual(self.mounted(), 160)
        self.assertEqual(self.text_html_requests(), ['text-8192.html'])
        self.assertTrue(self.page.evaluate('''async () => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const row = document.getElementById('L' + handoff.anchor.number);
            const gap = row?.getBoundingClientRect().top - document.querySelector('.controls').getBoundingClientRect().bottom;
            return !handoff.old.isConnected && fgTextNavigation === handoff.navigation &&
                fgTextWindow.model === handoff.model && fgView('timeline') === handoff.timeline &&
                handoff.url === location.href && handoff.history === history.length &&
                Math.abs(gap - handoff.anchor.gap) <= 2 && document.activeElement.id === 'textWindowBtn';
        }'''))
        self.assertTrue(self.page.evaluate('''async () => {
            await fgSwitchView('timeline');
            await fgSwitchView('text');
            const row = await fgEnsureTextLine(100);
            return row?.id === 'L100' && row.isConnected && !document.getElementById('L8500');
        }'''))
        self.assertLessEqual(self.mounted(), 160)

    def test_pending_source_click_cannot_act_after_a_window_eviction_or_view_switch(self):
        self.page.add_init_script('localStorage.clear()')
        for action in ('evict', 'switch', 'keep'):
            with self.subTest(action=action):
                self.open_report()
                facts = self.page.evaluate('''async action => {
                    const row = document.querySelector('#content .line-container.expandable');
                    const source = row.nextElementSibling;
                    const timers = new Map(), schedule = window.setTimeout, cancel = window.clearTimeout;
                    let sequence = -1;
                    window.setTimeout = (callback, delay, ...args) => {
                        if (delay !== 200) return schedule(callback, delay, ...args);
                        timers.set(sequence, () => callback(...args));
                        return sequence--;
                    };
                    window.clearTimeout = id => timers.delete(id) || cancel(id);
                    try {
                        row.querySelector('.line-content').click();
                        const pending = timers.has(clickTimer);
                        if (action === 'evict') await fgEnsureTextLine(8500);
                        if (action === 'switch') await fgSwitchView('timeline');
                        for (const callback of timers.values()) callback();
                        return {pending, connected:row.isConnected, open:source.style.display === 'block',
                            idle:clickTimer === null};
                    } finally {
                        window.setTimeout = schedule;
                        window.clearTimeout = cancel;
                    }
                }''', action)
                self.assertEqual(facts, dict(pending=True, connected=action != 'evict', open=action == 'keep', idle=True))

    def test_tree_guides_reuse_rows_within_a_window_but_rebuild_after_eviction(self):
        self.open_report()
        guide = self.page.locator('#foldGuidesBtn')
        if guide.get_attribute('aria-pressed') != 'true':
            guide.click()
        facts = self.page.evaluate('''async () => {
            const settle = () => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
            const first = await fgEnsureTextLine(4500);
            await settle();
            await settle();
            const root = document.getElementById('content');
            const group = first.closest('.fg-window-rows');
            const queries = group.querySelectorAll.bind(group);
            const context = document.querySelector('.fold-guide-layer').getContext('2d');
            const fill = context.fillRect.bind(context);
            let scans = 0, strokes = 0, staleReads = 0;
            const geometry = {root: 0, group: 0};
            for (const [name, node] of Object.entries({root, group})) {
                const bounds = node.getBoundingClientRect.bind(node);
                node.getBoundingClientRect = () => { geometry[name]++; return bounds(); };
            }
            group.querySelectorAll = selector => {
                if (selector === '.line-container') scans++;
                return queries(selector);
            };
            context.fillRect = (...args) => { strokes++; return fill(...args); };
            const rows = [...queries('.line-container')], start = scrollY;
            try {
                const repaints = [];
                for (let step = 0; step < 4; step++) {
                    const before = strokes;
                    scrollBy({top: 12, behavior: 'instant'});
                    await settle();
                    repaints.push(strokes > before);
                }
                const warmScans = scans, scrolled = scrollY - start, warmGeometry = {...geometry};
                const kept = rows.every(row => group.contains(row));
                for (const row of rows) {
                    const bounds = row.getBoundingClientRect.bind(row);
                    row.getBoundingClientRect = () => {
                        if (!row.isConnected) staleReads++;
                        return bounds();
                    };
                }
                const before = strokes;
                const next = await fgEnsureTextLine(8500);
                await settle();
                return {warmScans, warmGeometry, repaints, kept, scrolled, staleReads,
                    evicted: !first.isConnected, mounted: group.contains(next),
                    paintedAfterEviction: strokes > before};
            } finally {
                delete root.getBoundingClientRect;
                delete group.getBoundingClientRect;
                delete group.querySelectorAll;
                delete context.fillRect;
                for (const row of rows) delete row.getBoundingClientRect;
            }
        }''')
        self.assertEqual(facts, dict(warmScans=0, warmGeometry=dict(root=4, group=4),
                                     repaints=[True] * 4, kept=True, scrolled=48,
                                     staleReads=0, evicted=True, mounted=True, paintedAfterEviction=True))

    def test_disabled_tree_guides_do_not_retain_full_text_after_switching_back(self):
        self.open_report()
        guide = self.page.locator('#foldGuidesBtn')
        session = self.context.new_cdp_session(self.page)

        def retained_nodes():
            self.page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
            session.send('HeapProfiler.collectGarbage')
            return session.send('Memory.getDOMCounters')['nodes']

        self.assertLessEqual(self.mounted(), 160)
        window_nodes = retained_nodes()
        self.assertTrue(self.page.evaluate('async () => { await fgTextWindow.fullText(); return !fgTextWindow; }'))
        if guide.get_attribute('aria-pressed') != 'true':
            guide.click()
        self.page.locator('.fold-guide-layer').wait_for(state='visible')
        full_nodes = retained_nodes()
        self.assertGreater(full_nodes, window_nodes * 4)
        guide.click()
        self.assertEqual(guide.get_attribute('aria-pressed'), 'false')
        self.assertTrue(self.page.evaluate('async () => await fgOpenTextRenderer("windowed")'))
        self.assertLessEqual(self.mounted(), 160)
        self.assertLess(retained_nodes(), full_nodes // 4)
        guide.click()
        self.assertEqual(guide.get_attribute('aria-pressed'), 'true')
        self.assertTrue(self.page.evaluate('async () => (await fgEnsureTextLine(8500))?.id === "L8500"'))

    def test_mode_switch_is_reachable_and_preserves_reading_position_in_both_directions(self):
        for touch in (False, True):
            with self.subTest(touch=touch):
                if touch:
                    self.context.close()
                    self.context = self.browser.new_context(viewport={'width': 320, 'height': 740},
                                                            is_mobile=True, has_touch=True)
                    self.page = self.context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.open_report()
                self.page.evaluate('''async () => {
                    await fgEnsureTextLine(8500);
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    const root = document.getElementById('content');
                    window.modeSwitch = {root, button:document.getElementById('textWindowBtn'),
                        anchor:fgTextViewportAnchor(root, root.parentElement), url:location.href, entries:history.length};
                }''')
                self.assertGreater(self.page.evaluate('modeSwitch.anchor.number'), 8192)
                for action, windowed in [('Full Text', False), ('Windowed Text', True)]:
                    button = self.page.get_by_role('button', name=action, exact=True)
                    self.assertEqual(button.count(), 1)
                    self.assertTrue(button.evaluate('''(button, touch) => {
                        const rect = button.getBoundingClientRect();
                        return button === modeSwitch.button && rect.top >= 0 && rect.bottom <= innerHeight &&
                            rect.left >= 0 && rect.right <= innerWidth && (!touch || rect.height >= 44) &&
                            !button.closest('.controls').dataset.panel && !button.closest('#textToolbarOptions');
                    }''', touch))
                    (button.tap if touch else button.click)()
                    self.page.wait_for_function('windowed => !!fgTextWindow === windowed && !fgWindowHandoff && !modeSwitch.button.disabled', arg=windowed)
                    facts = self.page.evaluate('''async () => {
                        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        const {root, anchor, url, entries, button} = modeSwitch;
                        const row = document.getElementById('L' + anchor.number);
                        const gap = row?.getBoundingClientRect().top - document.querySelector('.controls').getBoundingClientRect().bottom;
                        return {same:location.href === url && history.length === entries && button.isConnected,
                            present:!!row, delta:gap - anchor.gap, focus:document.activeElement.id,
                            panel:button.closest('.controls').dataset.panel};
                    }''')
                    self.assertTrue(facts['same'] and facts['present'], facts)
                    self.assertLessEqual(abs(facts['delta']), 2, facts)
                    self.assertEqual(facts['focus'], 'textWindowBtn')
                    self.assertFalse(facts['panel'])
                    if windowed:
                        self.assertLessEqual(self.mounted(), 160)
                    else:
                        self.assertEqual(self.mounted(), self.page.evaluate('total_lines'))

    def test_compact_mode_and_jump_share_one_toolbar_row_with_an_active_filter(self):
        with self.browser.new_context(viewport={'width': 320, 'height': 740}, is_mobile=True, has_touch=True) as context:
            page = context.new_page()
            page.on('pageerror', lambda error: self.errors.append(str(error)))
            page.set_default_timeout(5000)
            page.goto(urljoin(self.server.url, 'index.html?text=windowed'))
            page.evaluate('''async () => {
                await fgSwitchView('text');
                document.getElementById('filterInput').value = 'func:^leaf$';
                await applyFilter();
                fgSelectMountedTextLine(await fgEnsureTextLine(8500), false);
            }''')
            jump = page.get_by_role('button', name='In Timeline · L8500', exact=True)
            toggle = page.locator('#textWindowBtn')
            filter_button = page.get_by_role('button', name='Filter · On', exact=True)
            for label in ('Full Text', 'Windowed Text'):
                with self.subTest(label=label):
                    page.set_viewport_size({'width': 320, 'height': 740})
                    self.assertEqual(toggle.inner_text(), label)
                    self.assertEqual(jump.inner_text().split(), ['In', 'Timeline', 'L8500'])
                    self.assertTrue(jump.locator('.text-jump-target').evaluate('label => label.scrollWidth <= label.clientWidth'))
                    self.assertTrue(filter_button.is_visible())
                    facts = toggle.evaluate('''button => {
                        const toolbar = button.closest('.toolbar');
                        const rect = button.getBoundingClientRect();
                        const targets = toolbar.querySelectorAll(':scope > .btn-group > button, :scope > button');
                        return Array.from(targets, target => {
                            const bounds = target.getBoundingClientRect();
                            return bounds.top === rect.top && bounds.height >= 44 && bounds.width >= 44 &&
                                bounds.right <= toolbar.getBoundingClientRect().right;
                        });
                    }''')
                    self.assertEqual(facts, [True] * 5)
                    for width in (280, 320, 360, 361, 365, 390):
                        page.set_viewport_size({'width': width, 'height': 320})
                        page.evaluate('fgSetTextJumpLine(Number.MAX_SAFE_INTEGER)')
                        long_jump = page.get_by_role('button', name='In Timeline · L9007199254740991', exact=True)
                        self.assertEqual(long_jump.get_attribute('title'), 'Locate trace line 9007199254740991 in Timeline')
                        targets = long_jump.evaluate('''async button => {
                            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                            const toolbar = button.closest('.toolbar'), area = toolbar.getBoundingClientRect();
                            const rect = button.getBoundingClientRect();
                            const targets = toolbar.querySelectorAll(':scope > .btn-group > button, :scope > button');
                            return Array.from(targets).flatMap(target => {
                                const bounds = target.getBoundingClientRect();
                                const fits = bounds.top === rect.top && bounds.width >= 44 && bounds.height >= 44 &&
                                    bounds.left >= area.left && bounds.right <= area.right &&
                                    target.contains(document.elementFromPoint((bounds.left + bounds.right) / 2,
                                        (bounds.top + bounds.bottom) / 2));
                                return fits ? [] : [{id: target.id, bounds: bounds.toJSON()}];
                            });
                        }''')
                        self.assertEqual(targets, [], (width, label, targets))
                    page.evaluate('fgSetTextJumpLine(8500)')
                    if label == 'Full Text':
                        toggle.tap()
                        page.wait_for_function('() => !fgTextWindow && !document.getElementById("textWindowBtn").disabled')
            page.set_viewport_size({'width': 390, 'height': 740})
            self.assertEqual(jump.inner_text().split(), ['In', 'Timeline', '·', 'L8500'])

    def test_full_text_to_window_keeps_filter_task_groups_and_duration_order(self):
        for query in ('pid:^202$', 'pid:^202$ sort:desc'):
            with self.subTest(query=query):
                self.open_task_report()
                facts = self.page.evaluate('''async query => {
                    const input = document.getElementById('filterInput'); input.value = query;
                    await applyFilter(); await fgTextWindow.fullText();
                    if (query.includes('sort:desc')) {
                        input.value = 'pid:^202$ AND params:^7 sort:desc';
                        await applyFilter();
                        input.value = query;
                        await applyFilter();
                    }
                    const navigation = fgTextNavigation;
                    const visible = navigation.order.filter(number => {
                        const row = document.getElementById('L' + number);
                        return row.style.display !== 'none' && !row.classList.contains('grp-hidden');
                    });
                    const label = document.getElementById('textOrderBtn').textContent;
                    const filter = fgFilterState;
                    const switched = await fgOpenTextRenderer('windowed');
                    return {switched, keptNavigation: fgTextNavigation === navigation,
                        keptFilter: fgFilterState === filter && filter.text === query,
                        keptVisible: JSON.stringify(visible) === JSON.stringify(fgTextWindow?.visibleNumbers) && visible.length > 160,
                        keptOrder: fgTextOrder === 'task' && document.getElementById('textOrderBtn').textContent === label,
                        sort: fgTextSort};
                }''', query)
                self.assertEqual(facts, dict(switched=True, keptNavigation=True, keptFilter=True, keptVisible=True,
                                             keptOrder=True, sort='desc' if 'sort:desc' in query else ''))
                self.assertLessEqual(self.mounted(), 160)

    def test_full_text_to_window_tolerates_offset_changes_without_reading_line_change(self):
        self.open_report(windowed=False)
        facts = self.page.evaluate('''async () => {
            const root = document.getElementById('content'), viewport = root.parentElement;
            document.getElementById('L8500').scrollIntoView({block:'center', behavior:'instant'});
            const anchor = fgTextViewportAnchor(root, viewport), initialScroll = scrollY;
            const row = document.getElementById('L' + anchor.number), initialTop = row.getBoundingClientRect().top;
            const original = fgLoadTextBlock;
            let release;
            const gate = new Promise(resolve => { release = resolve; });
            fgLoadTextBlock = async (...args) => { await gate; return original(...args); };
            try {
                const job = fgOpenTextRenderer('windowed');
                const spacer = document.createElement('div');
                spacer.style.height = innerHeight * 2 + 'px';
                viewport.before(spacer);
                scrollBy({top: row.getBoundingClientRect().top - initialTop, behavior:'instant'});
                const shifted = row.getBoundingClientRect().top - initialTop, movement = scrollY - initialScroll;
                release();
                const switched = await job;
                return {switched, moved:movement > innerHeight, stable:Math.abs(shifted) <= 40,
                    replaced:!row.isConnected && !!document.getElementById(row.id),
                    anchor, shifted, movement, status:document.getElementById('textWindowStatus').textContent};
            } finally { release(); fgLoadTextBlock = original; }
        }''')
        self.assertTrue(all(facts[key] for key in ('switched', 'moved', 'stable', 'replaced')), facts)
        self.assertLessEqual(self.mounted(), 160)

    def test_full_text_to_window_cancel_or_failed_validation_keeps_complete_text(self):
        self.open_report(windowed=False)
        self.page.evaluate('''() => {
            const original = fgLoadTextBlock;
            window.handoff = {row: document.getElementById('L100'), navigation: fgTextNavigation,
                begin(failure = false) {
                    const gate = new Promise(resolve => this.release = resolve);
                    fgLoadTextBlock = async (...args) => {
                        this.loading = true;
                        await gate;
                        const block = await original(...args);
                        if (failure) for (const row of block.element.querySelectorAll('.line-container')) row.remove();
                        return block;
                    };
                    this.loading = false;
                    this.job = fgOpenTextRenderer('windowed').finally(() => { fgLoadTextBlock = original; });
                }};
        }''')
        for action in ('Escape', 'switch', 'filter', 'validation'):
            with self.subTest(action=action):
                self.page.evaluate('action => handoff.begin(action === "validation")', action)
                self.page.wait_for_function('() => handoff.loading')
                self.assertGreater(self.mounted(), 9000)
                if action == 'Escape':
                    self.page.keyboard.press('Escape')
                elif action == 'switch':
                    self.page.locator('.view-tab[data-view=timeline]').click()
                elif action == 'filter':
                    self.page.evaluate('''async () => {
                        document.getElementById('filterInput').value = 'func:=leaf';
                        await applyFilter();
                    }''')
                self.assertTrue(self.page.evaluate('''async () => {
                    handoff.release();
                    return !await handoff.job && !fgTextWindow && !fgWindowHandoff &&
                        handoff.row === document.getElementById('L100') && handoff.navigation === fgTextNavigation &&
                        !document.getElementById('content').hasAttribute('aria-busy');
                }'''))
                if action == 'switch':
                    self.page.locator('.view-tab[data-view=text]').click()
                if action == 'validation':
                    self.assertIn('missing line', self.page.locator('#textWindowStatus').inner_text())
                if action == 'filter':
                    self.assertEqual(self.page.evaluate('fgFilterState.text'), 'func:=leaf')
        self.page.get_by_role('button', name='Windowed Text', exact=True).click()
        self.page.wait_for_function('() => fgTextWindow && !fgWindowHandoff')
        self.assertLessEqual(self.mounted(), 160)

    def test_adaptive_report_opens_bounded_text_with_a_plain_url(self):
        self.page.goto(urljoin(self.server.url, 'adaptive.html'))
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
        self.assertEqual(self.mounted(), 0)
        self.assertTrue(self.page.evaluate('''async () => {
            await fgSwitchView('text');
            return fgTextWindow?.model.records.length > 9000;
        }'''))
        self.assertLessEqual(self.mounted(), 160)
        self.assertEqual(len([url for url in self.requests if '/text-0.html' in url]), 1)
        self.assertFalse(any('/text-4096.html' in url for url in self.requests))
        self.assertTrue(self.page.get_by_role('button', name='Full Text', exact=True).is_visible())
        self.assertTrue(self.page.locator('#textWindowBtn').is_visible())
        self.assertTrue(self.page.evaluate('keyboardSelectedLine?.isConnected === true'))

    def test_adaptive_report_anchors_and_explicit_full_renderer(self):
        for query, windowed in [('', True), ('?text=full', False)]:
            with self.subTest(query=query):
                self.requests.clear()
                self.page.goto(urljoin(self.server.url, f'adaptive.html{query}#L8500'))
                self.page.wait_for_function('''() => {
                    const row = document.getElementById('L8500');
                    return row && fgTextInitialized && keyboardSelectedLine === row;
                }''')
                if windowed:
                    self.assertEqual(self.text_html_requests(), ['text-8192.html'])
                    self.assertLessEqual(self.mounted(), 160)
                else:
                    self.assertGreater(self.mounted(), 9000)
                self.assertEqual(self.page.evaluate('!!fgTextWindow'), windowed)

    def text_html_requests(self):
        return [url.rsplit('/', 1)[-1] for url in self.requests if '/text-' in url and url.endswith('.html')]

    def test_first_timeline_jump_fetches_the_destination_window_only(self):
        self.page.goto(urljoin(self.server.url, 'adaptive.html'))
        self.page.wait_for_function('() => !document.getElementById("timelineView").hasAttribute("aria-busy")')
        self.page.evaluate('''() => {
            const index = fgData.read('tlData').cols.line.indexOf(8500);
            if (index < 0) throw new Error('Missing timeline test slice');
            fgView('timeline').probe().select(index);
        }''')
        self.page.locator('[data-call-actions]').click()
        self.page.locator('#tlCallActions [data-call-action=text]').click()
        self.page.locator('#L8500.tl-flash').wait_for(timeout=10000)
        self.assertTrue(self.page.evaluate('document.activeElement.id === "L8500"'))
        self.assertEqual(self.text_html_requests(), ['text-8192.html'])
        self.assertLessEqual(self.mounted(), 160)

    def test_first_timeline_jump_scrolls_to_the_final_sticky_position_once(self):
        for width, height, mode in ((1500, 900, 'time'), (844, 390, 'task')):
            with self.subTest(width=width, order=mode):
                self.page.set_viewport_size(dict(width=width, height=height))
                self.page.goto(urljoin(self.server.url, 'adaptive.html'))
                index = self.page.evaluate('''async mode => {
                    const timeline = await fgSwitchView('timeline');
                    if (fgTextWindow) throw new Error('Text already opened');
                    fgTextOrder = mode;
                    const index = fgData.read('tlData').cols.line.indexOf(8500);
                    if (index < 0) throw new Error('Missing timeline test slice');
                    timeline.probe().select(index);
                    const scroll = window.scrollBy.bind(window);
                    window.coldJumpMoves = [];
                    window.scrollBy = (options, ...rest) => {
                        const previous = scrollY;
                        scroll(options, ...rest);
                        if (fgCurrentView() === 'text' && scrollY !== previous) coldJumpMoves.push(scrollY);
                    };
                    return index;
                }''', mode)
                self.requests.clear()
                self.page.locator('.tl-canvas').press('Enter')
                self.page.locator('#L8500.keyboard-selected.tl-flash').wait_for()
                self.assertTrue(self.page.evaluate('''() => {
                    const element = document.getElementById('L8500');
                    const row = element.getBoundingClientRect();
                    const toolbar = document.querySelector('.controls').getBoundingClientRect();
                    return coldJumpMoves.length === 1 && coldJumpMoves[0] === scrollY &&
                        Math.abs(row.top + row.bottom - innerHeight - toolbar.bottom) < 2 &&
                        element === document.activeElement &&
                        element.contains(document.elementFromPoint(100, (row.top + row.bottom) / 2));
                }'''))
                self.assertEqual(self.text_html_requests(), ['text-8192.html'])
                self.assertLessEqual(self.mounted(), 160)
                self.page.get_by_role('button', name='Return to Timeline', exact=True).click()
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), index)

    def test_warm_timeline_jump_centers_an_unmounted_row_once(self):
        for width, height in ((1500, 900), (844, 390)):
            with self.subTest(width=width):
                self.page.set_viewport_size(dict(width=width, height=height))
                self.open_report()
                self.assertEqual(self.page.locator('#L8500').count(), 0)
                self.assertTrue(self.page.evaluate('fgTextWindow.navigation.indexOf(8500) >= 0'))
                index = self.page.evaluate('''async () => {
                    const scroll = fgScrollTextLine;
                    window.jumpCenters = [];
                    window.pendingAtFirstMeasurement = null;
                    const bounds = Element.prototype.getBoundingClientRect;
                    Element.prototype.getBoundingClientRect = function () {
                        if (this.dataset?.windowLine === '8500' && pendingAtFirstMeasurement === null)
                            pendingAtFirstMeasurement = this.parentElement.classList.contains('fg-window-jump-pending');
                        return bounds.call(this);
                    };
                    fgScrollTextLine = (row, ...options) => {
                        jumpCenters.push(row.id);
                        scroll(row, ...options);
                    };
                    await fgSwitchView('timeline');
                    const index = fgData.read('tlData').cols.line.indexOf(8500);
                    fgView('timeline').probe().select(index);
                    return index;
                }''')
                self.page.locator('.tl-detail button[data-line="8500"]').click()
                self.page.locator('#L8500.keyboard-selected.tl-flash').wait_for()
                self.assertIs(self.page.evaluate('pendingAtFirstMeasurement'), False)
                self.assertEqual(self.page.evaluate('jumpCenters'), ['L8500'])
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'L8500')
                self.assertTrue(self.page.evaluate('''() => {
                    const row = document.getElementById('L8500').getBoundingClientRect();
                    const toolbar = document.querySelector('.controls').getBoundingClientRect();
                    return Math.abs(row.top + row.bottom - innerHeight - toolbar.bottom) < 2;
                }'''))
                self.page.get_by_role('button', name='Return to Timeline', exact=True).click()
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), index)

    def test_escape_returns_during_warm_jump_after_compact_controls_close(self):
        self.page.set_viewport_size(dict(width=390, height=390))
        self.open_report()
        self.page.evaluate('''async () => {
            const timeline = (await fgSwitchView('timeline')).probe();
            const index = fgData.read('tlData').cols.line.indexOf(8500);
            timeline.select(index);
            const load = fgData.loadTextBlocks;
            window.escapeJump = { load, index, view: timeline.view(), gate: null };
            fgData.loadTextBlocks = async (ordinals, options) => {
                if (!escapeJump.gate) await new Promise(resolve => {
                    escapeJump.gate = { resolve, signal: options.signal };
                });
                return load(ordinals, options);
            };
        }''')
        link = self.page.locator('.tl-detail button[data-line="8500"]')
        back = self.page.get_by_role('button', name='Return to Timeline', exact=True)
        link.press('Enter')
        self.page.wait_for_function('escapeJump.gate && fgTextWindow.jumpPending')
        more = self.page.locator('#textMoreToggle')
        more.click(timeout=5000)
        self.assertEqual(more.get_attribute('aria-expanded'), 'true')
        self.page.keyboard.press('Escape')
        self.assertEqual(more.get_attribute('aria-expanded'), 'false')
        self.assertTrue(self.page.evaluate('fgCurrentView() === "text" && !escapeJump.gate.signal.aborted'))
        back.press('Escape')
        self.assertTrue(self.page.evaluate('''() => {
            const timeline = fgView('timeline').probe();
            return fgCurrentView() === 'timeline' && escapeJump.gate.signal.aborted &&
                timeline.sel() === escapeJump.index && timeline.view().toString() === escapeJump.view.toString();
        }'''))
        self.assertTrue(link.evaluate('element => document.activeElement === element'))
        self.assertEqual(self.page.locator('#fgJumpStatus').inner_text(), '')
        self.page.evaluate('''() => {
            fgData.loadTextBlocks = escapeJump.load;
            escapeJump.gate.resolve();
        }''')
        link.press('Enter')
        row = self.page.locator('#L8500.keyboard-selected.tl-flash')
        row.wait_for()
        row.press('Escape')
        self.assertTrue(self.page.evaluate('fgCurrentView() === "text" && !keyboardSelectedLine'))
        self.assertTrue(back.is_visible())

    def test_complete_time_first_window_resolves_sparse_lines_and_survives_filter_roundtrip(self):
        self.page.goto(urljoin(self.server.url, 'adaptive.html'))
        facts = self.page.evaluate('''async () => {
            await fgSwitchView('timeline');
            const sparse = number => 4294967297 + 11 * number;
            const load = fgData.load, blocks = fgData.loadTextBlocks;
            fgData.load = async (name, options) => {
                const data = await load(name, options);
                return name !== 'fgTextIndex' ? data : {...data,
                    rows: data.rows.map(row => [sparse(row[0]), ...row.slice(1)]),
                    events: data.events?.map(sparse)};
            };
            fgData.loadTextBlocks = async (ordinals, options) => (await blocks(ordinals, options)).map(block => ({...block,
                html: block.html.replace(/(id="L|data-line-number=")([0-9]+)"/g,
                    (_, prefix, number) => prefix + sparse(+number) + '"')}));
            try {
                await fgSwitchView('text');
                const renderer = fgTextWindow;
                const line = sparse(8500), first = renderer.model.records[0].row[0];
                const order = renderer.model.records.map(record => record.row[0]);
                const ready = renderer.visibleNumbers.toString() === order.toString() &&
                    document.querySelectorAll('#content .line-container').length <= renderer.mountedLimit;
                const missing = await renderer.ensureLine(line - 1) === null;
                const found = (await renderer.ensureLine(line))?.id;
                fgNotifyFilter('fixture', new Set([first, line]));
                await renderer.rebuild(line);
                const filtered = renderer.visibleNumbers.toString() === [first, line].toString() &&
                    renderer.navigation.visibleCount === 2;
                fgNotifyFilter('', null);
                await renderer.rebuild(line);
                return {ready, missing, found, filtered, restored: renderer.visibleNumbers.toString() === order.toString() &&
                    renderer.navigation.visibleCount === order.length && !!document.getElementById('L' + line)};
            } finally { fgData.load = load; fgData.loadTextBlocks = blocks; }
        }''')
        self.assertEqual(facts, dict(ready=True, missing=True, found=f'L{4294967297 + 11 * 8500}',
                                     filtered=True, restored=True))
        self.assertLessEqual(self.mounted(), 160)

    def test_cancelling_queued_windowed_start_aborts_it_and_timer_fallback_can_reopen(self):
        self.page.goto(urljoin(self.server.url, 'adaptive.html'))
        scheduled = self.page.evaluate('''async () => {
            const timeline = await fgSwitchView('timeline');
            const index = fgData.read('tlData').cols.line.indexOf(8500);
            if (index < 0) throw new Error('Missing timeline test slice');
            timeline.probe().select(index);
            const native = window.scheduler?.postTask?.bind(window.scheduler);
            if (!native) return false;
            let release;
            const gate = new Promise(resolve => { release = resolve; });
            const queued = window.queuedTextTask = { release, signal: null, ran: false, outcome: null };
            Object.defineProperty(window, 'scheduler', { configurable: true, value: {
                postTask(callback, options) {
                    if (queued.signal) return native(callback, options);
                    queued.signal = options.signal;
                    const task = gate.then(() => native(() => { queued.ran = true; callback(); }, options));
                    queued.outcome = task.then(() => 'ran', error => error.name);
                    return task;
                },
            } });
            return true;
        }''')
        if not scheduled:
            self.skipTest('Prioritized tasks are unavailable in this browser')
        self.page.locator('[data-call-actions]').click()
        self.page.locator('#tlCallActions [data-call-action=text]').click()
        self.page.wait_for_function('() => !!queuedTextTask.signal')
        self.page.get_by_role('button', name='Return to Timeline', exact=True).click()
        self.assertEqual(self.page.evaluate('''async () => {
            queuedTextTask.release();
            const outcome = await queuedTextTask.outcome;
            Object.defineProperty(window, 'scheduler', { configurable: true, value: undefined });
            return {outcome, aborted: queuedTextTask.signal.aborted, ran: queuedTextTask.ran,
                closed: fgCurrentView() === 'timeline' && !fgTextWindow};
        }'''), dict(outcome='AbortError', aborted=True, ran=False, closed=True))
        self.assertEqual(self.text_html_requests(), [])

        self.page.locator('[data-call-actions]').click()
        self.page.locator('#tlCallActions [data-call-action=text]').click()
        self.page.locator('#L8500.tl-flash').wait_for()
        self.assertTrue(self.page.evaluate('document.activeElement.id === "L8500"'))
        self.assertEqual(self.text_html_requests(), ['text-8192.html'])
        self.assertLessEqual(self.mounted(), 160)

    def open_cold_folded_timeline(self):
        self.open_report()
        saved = self.page.evaluate('''() => {
            const blocks = [...fgGetTextFolding().blocks.values()];
            const root = blocks.find(block => block.entry?.getAttribute('data-func') === 'root');
            const inner = blocks.find(block => block.entry?.getAttribute('data-func') === 'inner');
            const ids = [root.id, inner.id];
            lsSet('foldedBlocks', JSON.stringify(Object.fromEntries(ids.map(id => [id, true]))));
            lsSet('view', 'timeline');
            return {ids, root: root.entry.row[0]};
        }''')
        self.requests.clear()
        self.page.goto(urljoin(self.server.url, 'adaptive.html'))
        self.page.evaluate('''async () => {
            const model = (await fgSwitchView('timeline')).probe();
            model.select(fgData.read('tlData').cols.line.indexOf(8500));
        }''')
        self.assertTrue(self.page.evaluate('fgTextDeferred() && !fgTextWindow'))
        self.assertEqual(self.text_html_requests(), [])
        return saved

    def test_first_folded_timeline_jump_fetches_the_destination_window_only(self):
        saved = self.open_cold_folded_timeline()
        link = self.page.locator('.tl-detail button[data-line="8500"]')
        link.click()
        self.page.locator('#L8500.keyboard-selected.tl-flash').wait_for(timeout=10000)
        self.assertEqual(self.text_html_requests(), ['text-8192.html'])
        self.assertLessEqual(self.mounted(), 160)
        self.assertTrue(self.page.evaluate('''({ids}) => ids.every(id => !foldedBlocks[id] &&
            JSON.parse(lsGet('foldedBlocks'))[id])''', saved))
        self.page.get_by_role('button', name='Return to Timeline', exact=True).click()
        self.assertTrue(link.evaluate('element => element === document.activeElement'))

    def test_first_filtered_timeline_jump_fetches_only_its_visible_destination_window(self):
        saved = self.open_cold_folded_timeline()
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value = 'func:^(root|inner|leaf)$';
            if (!await applyFilter() || !fgFilterState.lines.has(8500) || fgTextWindow) {
                throw new Error('Expected an applied filter with deferred Text');
            }
            window.firstAppliedFilter = fgFilterState;
            window.firstFilterEvaluations = 0;
            const evaluate = fgIndexedFilter;
            fgIndexedFilter = (...args) => { firstFilterEvaluations++; return evaluate(...args); };
        }''')
        self.assertEqual(self.text_html_requests(), [])
        self.page.locator('.tl-detail button[data-line="8500"]').click()
        self.page.locator('#L8500.keyboard-selected.tl-flash').wait_for(timeout=10000)
        self.assertEqual(self.text_html_requests(), ['text-8192.html'])
        self.assertTrue(self.page.evaluate('firstFilterEvaluations === 0 && fgFilterState === firstAppliedFilter'))
        self.assertTrue(self.page.evaluate('''({ids}) => fgFilterState.text === 'func:^(root|inner|leaf)$' &&
            fgFilterState.lines.has(8500) && ids.every(id => !foldedBlocks[id] && JSON.parse(lsGet('foldedBlocks'))[id])''', saved))
        self.assertLessEqual(self.mounted(), 160)

    def test_first_window_reuses_applied_call_tree_filter_and_duration_order(self):
        self.page.goto(urljoin(self.server.url, 'adaptive.html'))
        self.page.evaluate('''async () => {
            await fgSwitchView('timeline');
            document.getElementById('filterInput').value = 'graph:^inner$ sort:desc';
            if (!await applyFilter() || !fgFilterState.lines?.has(8500) || fgTextWindow) throw new Error('Expected indexed call-tree filter');
            window.filteredBeforeText = fgFilterState;
            fgIndexedFilter = () => { throw Error('Duplicate first-window filter'); };
        }''')
        self.page.locator('.view-tab[data-view="text"]').click()
        self.page.wait_for_function('() => !fgTextDeferred() && fgTextWindow')
        self.assertTrue(self.page.evaluate('''() => {
            const model = fgTextWindow.model, plan = fgTextModel.order(model, 'time');
            const outer = model.records.find(record => record.getAttribute('data-func') === 'root');
            const matches = filteredBeforeText.lines, actual = fgTextWindow.visibleNumbers;
            const expected = fgTextModel.rankDuration(model, plan, 'desc').order.filter(number => matches.has(number));
            return fgFilterState === filteredBeforeText && !fgFilterProgress && fgTextSort === 'desc' &&
                !matches.has(outer.row[0]) && actual.length === matches.size &&
                expected[0] !== plan.order.find(number => matches.has(number)) &&
                actual.every((number, index) => number === expected[index]);
        }'''))
        self.assertLessEqual(self.mounted(), 160)

    def test_first_filtered_timeline_jump_does_not_load_or_unfold_excluded_target(self):
        saved = self.open_cold_folded_timeline()
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value = 'func:^root$';
            if (!await applyFilter() || fgFilterState.lines.has(8500)) throw new Error('Target was not filtered');
            fgView('timeline').probe().select(fgData.read('tlData').cols.line.indexOf(8500));
        }''')
        self.page.locator('.tl-detail button[data-line="8500"]').click()
        self.page.get_by_role('status').filter(has_text='Line 8500 is hidden by the current filter').wait_for()
        self.assertEqual(self.text_html_requests(), ['text-0.html'])
        self.assertEqual(self.page.locator('#L8500').count(), 0)
        self.assertTrue(self.page.evaluate('({ids}) => ids.every(id => foldedBlocks[id] === true)', saved))
        self.page.get_by_role('button', name='Return to Timeline', exact=True).click()

    def test_slow_sorted_jump_reports_real_window_progress_and_return_cancels_it(self):
        self.page.set_viewport_size({'width': 390, 'height': 420})
        self.page.goto(urljoin(self.server.url, 'adaptive.html?view=timeline'))
        target = self.page.evaluate('''async () => {
            const timeline = (await fgSwitchView('timeline')).probe();
            if (fgTextWindow || !fgTextDeferred()) throw Error('Expected a cold Text window');
            document.getElementById('filterInput').value = 'sort:desc';
            if (!await applyFilter()) throw Error('Sort did not apply');
            const lines = fgData.read('tlData').cols.line, index = lines.indexOf(8500);
            if (index < 0) throw Error('Missing Timeline call for L8500');
            timeline.select(index);
            window.sortedPaint = { gates: [], load: fgData.loadTextBlocks, index, view: timeline.view() };
            fgData.loadTextBlocks = (ordinals, options) => new Promise((resolve, reject) => {
                sortedPaint.gates.push({ signal: options.signal, release: () =>
                    sortedPaint.load(ordinals, options).then(resolve, reject) });
            });
            return lines[index];
        }''')
        canvas = self.page.locator('.tl-canvas')
        canvas.press('Enter')
        notice = self.page.locator('.fg-window-notice [role=status]')
        notice.get_by_text('Loading Text rows: 0 / 160…', exact=True).wait_for()
        self.page.evaluate('sortedPaint.gates[0].release()')
        notice.get_by_text(re.compile(r'^Loading Text rows: [1-9]\d* / 160…$')).wait_for()
        self.assertEqual(self.mounted(), 0)
        self.page.get_by_role('button', name='Return to Timeline', exact=True).click()
        self.assertTrue(self.page.evaluate('''() => {
            const timeline = fgView('timeline').probe();
            return fgCurrentView() === 'timeline' && !fgTextWindow && fgTextDeferred() &&
                sortedPaint.gates.every(gate => gate.signal.aborted) && timeline.sel() === sortedPaint.index &&
                timeline.view().toString() === sortedPaint.view.toString();
        }'''))
        self.page.evaluate('''async () => {
            fgData.loadTextBlocks = sortedPaint.load;
            await Promise.all(sortedPaint.gates.map(gate => gate.release()));
        }''')
        self.assertEqual(notice.count(), 0)
        canvas.press('Enter')
        self.page.locator(f'#L{target}.tl-flash').wait_for(timeout=10000)
        self.assertIn('Windowed Text', notice.inner_text())
        self.assertLessEqual(self.mounted(), 160)

    def test_failed_initial_filter_projection_keeps_the_committed_result_readable(self):
        self.open_cold_folded_timeline()
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value = 'func:^root$';
            if (!await applyFilter()) throw new Error('Filter did not apply');
            window.beforeFirstProjection = fgFilterState;
            fgData.release('fgTextIndex');
            fgIndexedFilter = async () => { throw new Error('test projection unavailable'); };
        }''')
        self.page.locator('.view-tab[data-view="text"]').click()
        self.page.get_by_role('status').filter(has_text='test projection unavailable').first.wait_for()
        self.page.wait_for_function('() => !fgTextDeferred()')
        self.assertTrue(self.page.evaluate('''() => fgFilterState === beforeFirstProjection &&
            fgTextWindow.visibleNumbers.every(number => fgFilterState.lines.has(number))'''))
        self.assertGreater(self.mounted(), 0)
        self.assertEqual(self.text_html_requests(), ['text-0.html'])

    def test_cancelled_first_folded_jump_does_not_unfold_or_replace_new_navigation(self):
        for outcome, filtered in (('return', False), ('hash', False), ('return', True), ('hash', True)):
            with self.subTest(outcome=outcome, filtered=filtered):
                saved = self.open_cold_folded_timeline()
                self.page.evaluate('''async filtered => {
                    if (filtered) {
                        document.getElementById('filterInput').value = 'func:^(root|inner|leaf)$';
                        if (!await applyFilter()) throw new Error('Filter did not apply');
                    }
                    const load = fgLoadTextBlock;
                    window.firstFoldGate = null;
                    fgLoadTextBlock = async (model, number, options) => {
                        if (!firstFoldGate && number > 8000) {
                            await new Promise(resolve => {
                                firstFoldGate = {resolve, signal: options.signal};
                            });
                        }
                        return load(model, number, options);
                    };
                }''', filtered)
                self.page.locator('.tl-detail button[data-line="8500"]').click()
                self.page.wait_for_function('() => firstFoldGate !== null', timeout=5000)
                if outcome == 'return':
                    self.page.get_by_role('button', name='Return to Timeline', exact=True).click()
                else:
                    self.page.evaluate('number => { location.hash = "#L" + number; }', saved['root'])
                self.page.wait_for_function('() => firstFoldGate.signal.aborted', timeout=5000)
                self.page.evaluate('firstFoldGate.resolve()')
                self.page.wait_for_function('() => fgFilterProgress === null', timeout=5000)
                if outcome == 'return':
                    self.assertTrue(self.page.evaluate('fgCurrentView() === "timeline" && fgTextDeferred() && !fgTextWindow'))
                else:
                    self.page.wait_for_function('number => keyboardSelectedLine?.id === "L" + number', arg=saved['root'])
                self.assertTrue(self.page.evaluate('ids => ids.every(id => foldedBlocks[id] === true)', saved['ids']))
                self.assertEqual(self.page.locator('#L8500').count(), 0)

    def test_deep_timeline_jump_commits_nested_unfolds_only_after_loading(self):
        self.open_report()
        self.page.evaluate('''async () => {
            const renderer = fgTextWindow;
            const foldIds = [...fgGetTextFolding().blocks.values()]
                .filter(block => ['root', 'inner'].includes(block.entry?.getAttribute('data-func')))
                .map(block => block.id);
            if (foldIds.length !== 2) throw new Error('Missing nested folds');
            fgSetFoldStates(foldIds.map(id => [id, true]));
            await renderer.settled;
            const scroll = fgScrollTextLine;
            const computedStyle = window.getComputedStyle;
            const rowHeight = renderer.root.querySelector('.fg-window-rows').getBoundingClientRect().height;
            window.foldJump = {renderer, foldIds, visible: renderer.visibleNumbers.toString(), load: fgLoadTextBlock,
                scrolls: [], rowHeight, guideReads: 0};
            fgScrollTextLine = (row, ...options) => {
                foldJump.scrolls.push(row.id);
                scroll(row, ...options);
            };
            window.getComputedStyle = (element, pseudo) => {
                if (pseudo === '::before' && element.classList.contains('call-indent')) foldJump.guideReads++;
                return computedStyle(element, pseudo);
            };
            await fgSwitchView('timeline');
            fgView('timeline').probe().select(fgData.read('tlData').cols.line.indexOf(8500));
        }''')
        link = self.page.locator('.tl-detail button[data-line="8500"]')
        back = self.page.get_by_role('button', name='Return to Timeline', exact=True)
        rows = self.page.locator('.fg-window-rows')
        locating = self.page.get_by_role('status').filter(has_text='Locating L8500')
        retained = '''() => foldJump.scrolls.length === 0 && foldJump.foldIds.every(id => foldedBlocks[id] === true) &&
            foldJump.renderer.visibleNumbers.toString() === foldJump.visible && !document.getElementById('L8500')'''
        for outcome in ('load failure', 'return'):
            with self.subTest(outcome=outcome):
                self.page.evaluate('''() => {
                    foldJump.gate = null;
                    foldJump.guideReads = 0;
                    fgLoadTextBlock = async function(model, number, options) {
                        if (!foldJump.gate && number > 8000) {
                            await new Promise((resolve, reject) => {
                                foldJump.gate = {resolve, reject, signal: options.signal};
                            });
                        }
                        return foldJump.load(model, number, options);
                    };
                }''')
                link.click()
                self.page.wait_for_function('() => foldJump.gate !== null', timeout=5000)
                self.assertTrue(self.page.evaluate(retained))
                self.assertTrue(locating.is_visible())
                self.assertFalse(rows.is_visible())
                self.assertTrue(back.is_visible())
                self.assertTrue(rows.evaluate('''async element => {
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    return foldJump.guideReads === 0 && Math.abs(element.getBoundingClientRect().height - foldJump.rowHeight) < 1;
                }'''))
                locating.get_by_text(re.compile(r'^Locating L8500 · \d+ / \d+ rows$')).wait_for()
                self.assertNotIn('Loading Text rows:', self.page.locator('.fg-window-notice').inner_text())
                if outcome == 'load failure':
                    self.page.evaluate('foldJump.gate.reject(new Error("destination block unavailable"))')
                    self.page.get_by_role('status').filter(
                        has_text='Unable to open trace line 8500: destination block unavailable').wait_for()
                    self.assertTrue(rows.is_visible())
                    self.assertFalse(locating.is_visible())
                    self.page.wait_for_function('() => foldJump.guideReads > 0', timeout=5000)
                    back.click()
                else:
                    back.click()
                    self.assertTrue(self.page.evaluate('foldJump.gate.signal.aborted'))
                    self.page.evaluate('foldJump.gate.resolve()')
                self.page.evaluate('''async () => {
                    await foldJump.renderer.settled.catch(() => {});
                    fgLoadTextBlock = foldJump.load;
                }''')
                self.assertTrue(self.page.evaluate(retained))
                self.assertEqual(self.page.locator('#fgJumpStatus').inner_text(), '')
                self.assertTrue(rows.evaluate('''element => !element.classList.contains('fg-window-jump-pending') &&
                    !element.style.getPropertyValue('--fg-window-pending-height')'''))

        link.click()
        self.page.locator('#L8500.keyboard-selected.tl-flash').wait_for()
        self.assertTrue(rows.is_visible())
        self.assertFalse(locating.is_visible())
        self.assertTrue(self.page.evaluate('fgTextWindow === foldJump.renderer && foldJump.foldIds.every(id => !foldedBlocks[id])'))
        self.assertEqual(self.page.evaluate('foldJump.scrolls'), ['L8500'])
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'L8500')
        self.assertTrue(self.page.evaluate('''() => {
            const row = document.getElementById('L8500').getBoundingClientRect();
            const toolbar = document.querySelector('.controls').getBoundingClientRect();
            return Math.abs(row.top + row.bottom - innerHeight - toolbar.bottom) < 2;
        }'''))
        self.assertLessEqual(self.mounted(), self.page.evaluate('fgTextWindow.mountedLimit'))
        back.click()
        self.assertTrue(link.evaluate('button => button === document.activeElement'))

    def test_return_to_windowed_text_focuses_only_a_visible_mounted_row(self):
        for evicted in (False, True):
            with self.subTest(evicted=evicted):
                self.page.goto(urljoin(self.server.url, f'adaptive.html?evicted={int(evicted)}#L8500'))
                self.page.wait_for_function('() => fgTextWindow && keyboardSelectedLine?.id === "L8500"')
                if evicted:
                    self.page.evaluate('window.scrollTo({top: 0, behavior: "instant"})')
                    self.page.wait_for_function('''() => !document.getElementById('L8500') &&
                        !!document.getElementById('L2') && !document.getElementById('content').hasAttribute('aria-busy')''')
                before = self.page.evaluate('scrollY')
                self.page.evaluate('window.beforeTextReturn = fgTextWindow.settled')
                self.page.get_by_role('button', name='In Timeline · L8500', exact=True).press('Enter')
                back = self.page.get_by_role('button', name='Return to Text', exact=True)
                self.requests.clear()
                back.press('Enter')
                self.assertFalse(back.is_visible())
                self.assertEqual(self.page.evaluate('scrollY'), before)
                self.assertTrue(self.page.evaluate('fgTextWindow.settled === beforeTextReturn'))
                self.assertEqual(self.text_html_requests(), [])
                self.assertLessEqual(self.mounted(), 160)
                if evicted:
                    self.assertEqual(self.page.locator('#L8500').count(), 0)
                    self.assertTrue(self.page.locator('.view-tab[data-view=text]').evaluate(
                        'tab => tab === document.activeElement'))
                else:
                    self.assertEqual(self.page.evaluate('document.activeElement.id'), 'L8500')
                    self.page.keyboard.press('j')
                    self.page.wait_for_function('() => document.activeElement.id === "L8501" && keyboardSelectedLine === document.activeElement')

    def test_return_to_text_resumes_keyboard_navigation_from_the_alt_clicked_row(self):
        self.page.goto(urljoin(self.server.url, 'adaptive.html#L8500'))
        self.page.wait_for_function('() => fgTextWindow && keyboardSelectedLine?.id === "L8500"')
        self.page.locator('#L8502 .line-content').click(modifiers=['Alt'])
        self.page.get_by_role('button', name='Return to Text', exact=True).press('Enter')
        self.assertEqual(self.page.evaluate('keyboardSelectedLine?.id'), 'L8502')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'L8502')
        self.page.keyboard.press('j')
        self.page.wait_for_function('() => document.activeElement.id === "L8503"')

    def test_opening_the_visible_origin_again_restores_reading_position(self):
        for route in ('same', 'different', 'evicted', 'filtered'):
            with self.subTest(route=route):
                self.page.goto(urljoin(self.server.url, f'adaptive.html?route={route}#L8500'))
                self.page.wait_for_function('() => fgTextWindow && keyboardSelectedLine?.id === "L8500"')
                if route == 'evicted':
                    self.page.evaluate('scrollTo({top: 0, behavior: "instant"})')
                    self.page.wait_for_function("() => !document.getElementById('L8500')")
                else:
                    self.page.evaluate('''() => {
                        fgScrollTextLine(keyboardSelectedLine, {block: 'center', behavior: 'instant'});
                        scrollBy({top: 120, behavior: 'instant'});
                    }''')
                position = '[scrollY, document.getElementById("textViewport").scrollLeft, history.length]'
                jump = self.page.get_by_role('button', name='In Timeline · L8500', exact=True)
                jump.scroll_into_view_if_needed()
                before = self.page.evaluate(position)
                jump.press('Enter')
                original = self.page.locator('.tl-detail button[data-line="8500"]').first
                original.wait_for()
                if route == 'filtered':
                    self.page.evaluate('''async () => {
                        document.getElementById('filterInput').value = 'func:^leaf$';
                        await applyFilter();
                    }''')
                    self.assertEqual(self.page.evaluate('fgFilterState.text'), 'func:^leaf$')
                if route == 'different':
                    self.page.evaluate('''() => fgView('timeline').probe().select(fgData.read('tlData').cols.line.indexOf(8502))''')
                    self.page.locator('.tl-detail button[data-line="8502"]').first.press('Enter')
                    self.page.locator('#L8502.tl-flash').wait_for()
                else:
                    self.requests.clear()
                    original.press('Enter')
                    if route == 'same':
                        self.assertTrue(self.page.locator('#L8500').evaluate('row => row === document.activeElement'))
                        self.assertEqual(self.page.evaluate(position), before)
                        self.assertFalse(self.page.locator('#fgJumpReturn').is_visible())
                        self.assertEqual(self.text_html_requests(), [])
                        continue
                    self.page.locator('#L8500.tl-flash').wait_for()
                self.assertTrue(self.page.get_by_role('button', name='Return to Timeline', exact=True).is_visible())

    def test_hash_change_during_initial_window_never_focuses_old_anchor(self):
        self.page.add_init_script('''(() => {
            window.textFocus = [];
            document.addEventListener('focusin', event => {
                if (event.target.matches('.line-container')) textFocus.push(event.target.id);
            });
        })()''')
        anchor_route = []
        self.page.route('**/text-8192.html', lambda route: anchor_route.append(route))
        with self.page.expect_request('**/text-8192.html', timeout=10000):
            self.page.goto(urljoin(self.server.url, 'adaptive.html#L8500'))
        self.page.evaluate('location.hash = "#L4500"')
        self.page.wait_for_function('() => fgTextLineRequest?.number === 4500', timeout=5000)
        anchor_route[0].fulfill(response=anchor_route[0].fetch())
        self.page.wait_for_function('() => keyboardSelectedLine?.id === "L4500"', timeout=10000)
        self.assertEqual(self.page.evaluate('textFocus'), ['L4500'])
        self.assertLessEqual(self.mounted(), 160)

    def test_adaptive_index_failure_can_open_full_text_without_the_index(self):
        self.page.route('**/text-index.json', lambda route: route.abort())
        self.page.goto(urljoin(self.server.url, 'adaptive.html?tag=test#L8500'))
        self.page.get_by_role('button', name='Open Full Text').click()
        self.page.wait_for_function('() => fgTextInitialized && keyboardSelectedLine?.id === "L8500"')
        self.assertTrue(self.page.url.endswith('?tag=test&text=full#L8500'))
        self.assertGreater(self.mounted(), 9000)
        self.assertFalse(self.page.evaluate('!!fgTextWindow'))

    def test_scrollbar_seek_and_repeated_scrolls_keep_dom_bounded(self):
        self.open_report()
        for fraction in [0.9, 0.2, 0.7, 0.01]:
            self.page.evaluate('fraction => scrollTo({top:document.documentElement.scrollHeight*fraction, behavior:"instant"})', fraction)
            self.page.wait_for_timeout(180)
            self.page.wait_for_function('!document.getElementById("content").hasAttribute("aria-busy")')
            facts = self.page.evaluate('''() => {
                const rows = [...document.querySelectorAll('#content .line-container')];
                return {visible:rows.filter(row => {const rect=row.getBoundingClientRect(); return rect.bottom>0 && rect.top<innerHeight}).length,
                    ids:rows.map(row => row.id), all:document.querySelectorAll('#content *').length};
            }''')
            self.assertGreater(facts['visible'], 0)
            self.assertLessEqual(len(facts['ids']), 160)
            self.assertEqual(len(set(facts['ids'])), len(facts['ids']))
            self.assertLess(facts['all'], 7000)

    def test_sliding_window_keeps_retained_rows_connected_and_focused(self):
        self.open_report()
        self.page.evaluate('''async () => {
            await fgTextWindow.ensureLine(4100);
            const body = document.querySelector('.fg-window-rows');
            const rows = [...body.children], target = rows[135].firstElementChild;
            const source = target.nextElementSibling;
            toggleExpand(target.dataset.lineId);
            await fgFill(source);
            const sourceChild = source.firstElementChild;
            target.tabIndex = -1;
            target.focus({preventScroll:true});
            const removed = new Set();
            const observer = new MutationObserver(records => {
                for (const record of records) for (const node of record.removedNodes) removed.add(node);
            });
            observer.observe(body, {childList:true});
            const destination = scrollY + rows[130].getBoundingClientRect().top;
            const measured = new Set(), rect = HTMLElement.prototype.getBoundingClientRect;
            HTMLElement.prototype.getBoundingClientRect = function () {
                if (this.parentElement === body) measured.add(this);
                return rect.call(this);
            };
            const projected = new Set(), project = fgProjectFoldLine;
            fgProjectFoldLine = (row, ...args) => {
                if (row?.parentElement.parentElement === body) projected.add(row.parentElement);
                return project(row, ...args);
            };
            window.textSlide = { body, rows, target, source, sourceChild, removed, observer, measured, rect, projected, project };
            scrollTo({top:destination, behavior:'instant'});
        }''')
        self.page.wait_for_function('''() => textSlide.body.firstElementChild !== textSlide.rows[0] &&
            !fgTextWindow.root.hasAttribute('aria-busy')''')
        facts = self.page.evaluate('''() => {
            const {body,rows,target,source,sourceChild,removed,observer,measured,rect,projected,project} = textSlide;
            observer.disconnect();
            HTMLElement.prototype.getBoundingClientRect = rect;
            fgProjectFoldLine = project;
            const mounted = [...body.children], shared = mounted.filter(node => rows.includes(node));
            return {mounted:mounted.length, shared:shared.length, detached:shared.filter(node => removed.has(node)).length,
                retainedReads:shared.filter(node => measured.has(node)).length,
                incomingReads:mounted.filter(node => !rows.includes(node) && measured.has(node)).length,
                retainedProjections:shared.filter(node => projected.has(node)).length,
                incomingProjections:mounted.filter(node => !rows.includes(node) && projected.has(node)).length,
                sourceIntact:!!sourceChild && sourceChild.isConnected && source.firstElementChild === sourceChild &&
                    source.style.display === 'block' && target.querySelector('.expand-btn').getAttribute('aria-expanded') === 'true',
                focused:document.activeElement === target,
                consecutive:mounted.every((element,index) => !index ||
                    +element.dataset.windowIndex === +mounted[index-1].dataset.windowIndex + 1)};
        }''')
        shared = facts.pop('shared')
        self.assertGreater(shared, 0)
        self.assertEqual(facts, dict(mounted=160, detached=0, retainedReads=0, incomingReads=160 - shared,
                                     retainedProjections=0, incomingProjections=160 - shared,
                                     sourceIntact=True, focused=True, consecutive=True))

    def test_source_reopens_after_eviction_and_source_jobs_do_not_accumulate(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            let row = await fgEnsureTextLine(100);
            toggleExpand(row.dataset.lineId); await fgFill(row.nextElementSibling);
            const text = row.nextElementSibling.textContent, source = row.nextElementSibling.dataset.src;
            await fgEnsureTextLine(8500);
            const evicted = !row.isConnected;
            row = await fgEnsureTextLine(100); await fgFill(row.nextElementSibling);
            return {text, again:row.nextElementSibling.textContent, source, againSource:row.nextElementSibling.dataset.src,
                expanded:row.nextElementSibling.style.display, evicted, jobs:fgSourceJobs.size};
        }''')
        self.assertTrue(facts['evicted'])
        self.assertTrue(facts['text'])
        self.assertEqual(facts['text'], facts['again'])
        self.assertEqual(facts['source'], facts['againSource'])
        self.assertEqual(facts['expanded'], 'block')
        self.assertEqual(facts['jobs'], 0)

    def test_pending_source_chunk_is_shared_between_evicted_and_incoming_rows(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const incomingNumber = 8500, renderer = fgTextWindow;
            const old = await fgEnsureTextLine(100), outgoing = old.nextElementSibling;
            const incomingIndex = fgTextModel.locate(renderer.model, incomingNumber).record.source;
            const chunkSize = fgData.read('fgSourceChunks').chunk_size;
            const shareChunk = Math.floor(+outgoing.dataset.src / chunkSize) === Math.floor(incomingIndex / chunkSize);
            const fetch = window.fetch, attempts = [];
            window.fetch = (url, options) => {
                if (!/source-[0-9]+\\.json$/.test(url)) return fetch(url, options);
                return new Promise((resolve, reject) => attempts.push({ signal: options.signal,
                    release: () => fetch(url, options).then(resolve, reject) }));
            };
            try {
                toggleExpand(old.dataset.lineId);
                const previous = fgSourceJobs.get(outgoing);
                await new Promise(requestAnimationFrame);
                saveExpandedState('line_' + (incomingNumber - 1), true);
                const incoming = (await fgEnsureTextLine(incomingNumber)).nextElementSibling;
                const current = fgSourceJobs.get(incoming);
                const stopped = previous.controller.signal.aborted && !fgSourceJobs.has(outgoing);
                const evicted = !outgoing.isConnected;
                const requestRetained = !attempts[0].signal.aborted;
                const count = attempts.length;
                attempts.forEach(attempt => attempt.release());
                return {shareChunk, evicted, stopped, requestRetained, count, ignored: !await previous.promise,
                    outgoingEmpty: outgoing.textContent === '', loaded: await current.promise,
                    currentVisible: incoming.style.display === 'block', currentText: incoming.textContent.length > 0,
                    jobs: fgSourceJobs.size};
            } finally { window.fetch = fetch; }
        }''')
        self.assertEqual(facts, dict(shareChunk=True, evicted=True, stopped=True, requestRetained=True, count=1,
                                     ignored=True, outgoingEmpty=True, loaded=True, currentVisible=True,
                                     currentText=True, jobs=0))

    def test_cancelled_far_navigation_cannot_replace_newer_window(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const original = fgData.loadTextBlocks;
            let release;
            fgData.loadTextBlocks = async (ordinals, options) => {
                if (ordinals.some(ordinal => ordinal > 8000)) await new Promise(resolve => release = resolve);
                return original(ordinals, options);
            };
            const controller = new AbortController();
            const obsolete = fgEnsureTextLine(8500, {signal:controller.signal}).catch(error => error.name);
            while (!release) await new Promise(resolve => setTimeout(resolve, 0));
            controller.abort();
            const row = await fgEnsureTextLine(5001);
            release();
            const canceled = await obsolete;
            fgData.loadTextBlocks = original;
            return {canceled, id:row.id, connected:row.isConnected, stale:!!document.getElementById('L8500')};
        }''')
        self.assertEqual(facts, dict(canceled='AbortError', id='L5001', connected=True, stale=False))

    def test_failed_block_keeps_previous_window_and_navigation_can_retry(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const original = fgData.loadTextBlocks;
            const before = [...document.querySelectorAll('#content .line-container')].map(row => row.id);
            fgData.loadTextBlocks = () => Promise.reject(new Error('injected network failure'));
            const error = await fgEnsureTextLine(8500).catch(error => error.message);
            const after = [...document.querySelectorAll('#content .line-container')].map(row => row.id);
            const retry = !document.querySelector('.fg-window-notice button').hidden;
            fgData.loadTextBlocks = original;
            const row = await fgEnsureTextLine(8500);
            return {before, after, error, retry, id:row.id};
        }''')
        self.assertEqual(facts['before'], facts['after'])
        self.assertEqual(facts['error'], 'injected network failure')
        self.assertTrue(facts['retry'])
        self.assertEqual(facts['id'], 'L8500')

    def test_failed_filter_rolls_back_full_state_and_retry_reaches_remote_match(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const original = fgData.loadTextBlocks;
            const before = fgTextWindow.visibleNumbers;
            fgData.loadTextBlocks = () => Promise.reject(new Error('filter block unavailable'));
            document.getElementById('filterInput').value = 'params:arg=8999';
            const applied = await applyFilter();
            const after = fgTextWindow.visibleNumbers, state = fgFilterState, error = fgFilterProgress.message;
            const notice = document.querySelector('.fg-window-notice').textContent;
            fgData.loadTextBlocks = original;
            const retried = await applyFilter();
            return {before, after, applied, text:state.text, lines:state.lines, error, notice, retried, matches:[...fgFilterState.lines]};
        }''')
        self.assertFalse(facts['applied'])
        self.assertEqual(facts['before'], facts['after'])
        self.assertEqual(facts['text'], '')
        self.assertIsNone(facts['lines'])
        self.assertIn('filter block unavailable', facts['error'])
        self.assertIn('Previous result retained; Apply again to retry', facts['notice'])
        self.assertTrue(facts['retried'])
        self.assertEqual(len(facts['matches']), 1)

    def test_cancel_full_text_from_deep_scroll_keeps_window_and_does_not_print(self):
        self.page.set_viewport_size({'width': 320, 'height': 680})
        self.open_report()
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value = 'func:^leaf$';
            await applyFilter();
            const row = await fgEnsureTextLine(8000);
            row.tabIndex = -1;
            row.focus({preventScroll: true});
            window.retainedFullTextWindow = {row, renderer: fgTextWindow, filter: fgFilterState, top: scrollY};
            const load = fgData.loadTextBlocks;
            fgData.loadTextBlocks = (ordinals, options) => {
                fgData.loadTextBlocks = load;
                window.fullTextSignal = options.signal;
                return new Promise((resolve, reject) => options.signal.addEventListener('abort',
                    () => reject(new DOMException('Canceled by user', 'AbortError')), {once: true}));
            };
            window.printedFullText = 0;
            window.print = () => printedFullText++;
        }''')
        self.page.keyboard.press('Control+p')
        cancel = self.page.get_by_role('button', name='Cancel Full Text')
        cancel.wait_for()
        self.page.wait_for_function('window.fullTextSignal !== undefined')
        self.assertTrue(cancel.evaluate('''button => {
            const rect = button.getBoundingClientRect();
            return rect.left >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight &&
                rect.height >= 44 && document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2) === button;
        }'''))
        cancel.click()
        cancel.wait_for(state='hidden')
        facts = self.page.evaluate('''() => {
            const {row, renderer, filter, top} = retainedFullTextWindow;
            return {aborted: fullTextSignal.aborted, printed: printedFullText, retained: fgTextWindow === renderer,
                filter: fgFilterState === filter, sameRow: document.getElementById('L8000') === row,
                focus: document.activeElement === row, scroll: Math.abs(top - scrollY) <= 1,
                busy: renderer.root.hasAttribute('aria-busy')};
        }''')
        self.assertEqual(facts, dict(aborted=True, printed=0, retained=True, filter=True, sameRow=True, focus=True, scroll=True, busy=False))
        self.assertLessEqual(self.mounted(), 160)
        self.assertIn('Windowed Text retained', self.page.locator('.fg-window-notice').inner_text())
        self.page.get_by_role('button', name='Full Text', exact=True).click()
        self.page.wait_for_function('() => fgTextWindow === null')
        self.assertEqual(self.mounted(), self.page.evaluate('total_lines'))

    def test_escape_after_last_full_text_block_still_prevents_handoff(self):
        for disabled in ([], ['scheduler'], ['scheduler', 'MessageChannel']):
            with self.subTest(disabled=disabled):
                self.check_escape_before_full_text_handoff(disabled)

    def check_escape_before_full_text_handoff(self, disabled):
        self.open_report()
        facts = self.page.evaluate('''async disabled => {
            const original = disabled.map(name => [name, globalThis[name]]);
            for (const name of disabled) globalThis[name] = undefined;
            const renderer = fgTextWindow, row = document.querySelector('#content .line-container');
            const progress = document.querySelector('.fg-window-full-loading progress');
            let atEnd = false;
            const observer = new MutationObserver(() => {
                if (progress.value !== renderer.model.records.length) return;
                atEnd = true;
                cancelDispatched = !document.activeElement.dispatchEvent(new KeyboardEvent('keydown',
                    {key: 'Escape', bubbles: true, cancelable: true}));
            });
            let cancelDispatched = false;
            observer.observe(progress, {attributeFilter: ['value']});
            try {
                const outcome = await renderer.fullText().then(() => 'mounted', error => error.name);
                return {atEnd, cancelDispatched, outcome, retained: fgTextWindow === renderer, sameRow: row.isConnected,
                    busy: renderer.root.hasAttribute('aria-busy'), completed: !progress.closest('div').hidden};
            } finally {
                observer.disconnect();
                for (const [name, value] of original) globalThis[name] = value;
            }
        }''', disabled)
        self.assertEqual(facts, dict(atEnd=True, cancelDispatched=True, outcome='AbortError', retained=True,
                                     sameRow=True, busy=False, completed=False))
        self.assertLessEqual(self.mounted(), 160)

    def test_failed_full_text_keeps_windowed_rows_and_can_retry(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const renderer = fgTextWindow, original = fgData.loadTextBlocks;
            fgData.loadTextBlocks = (ordinals, options) => ordinals[0]>4000 ? Promise.reject(new Error('full block unavailable')) : original(ordinals, options);
            const error = await renderer.fullText().catch(error=>error.message);
            const retained = renderer === fgTextWindow, mounted = document.querySelectorAll('#content .line-container').length;
            fgData.loadTextBlocks = original;
            await renderer.fullText();
            return {error, retained, mounted, all:document.querySelectorAll('#content .line-container').length, total:total_lines};
        }''')
        self.assertEqual(facts['error'], 'full block unavailable')
        self.assertTrue(facts['retained'])
        self.assertLessEqual(facts['mounted'], 160)
        self.assertEqual(facts['all'], facts['total'])

    def test_source_suppression_during_filter_does_not_erase_saved_expansion(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            let row=await fgEnsureTextLine(100);
            const id=row.dataset.lineId;
            toggleExpand(id); await fgFill(row.nextElementSibling);
            document.getElementById('filterInput').value='func:^leaf$'; await applyFilter();
            row=await fgEnsureTextLine(100);
            const filtered=row.nextElementSibling.style.display, saved=expandedLines[id];
            await clearFilter(); row=await fgEnsureTextLine(100); await fgFill(row.nextElementSibling);
            return {filtered,saved,restored:row.nextElementSibling.style.display};
        }''')
        self.assertEqual(facts, dict(filtered='none', saved=True, restored='block'))

    def test_expand_all_keeps_window_and_opens_matching_offscreen_sources_on_arrival(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const renderer = fgTextWindow;
            const near = await fgEnsureTextLine(100);
            toggleExpand(near.dataset.lineId);
            await fgFill(near.nextElementSibling);
            const hidden = fgTextModel.locate(renderer.model, 200).record.getAttribute('data-params');
            document.getElementById('filterInput').value = 'func:^leaf$ AND NOT params:^' + hidden + '$';
            await applyFilter();
            const row = await fgEnsureTextLine(100), source = row.nextElementSibling;
            const before = {offscreen: !document.getElementById('L8500'), near: source.style.display,
                far: expandedLines.line_8499 === true, records: renderer.visibleNumbers.length};
            await expandAll(document.querySelector('.btn-expand'));
            await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
            const saved = JSON.parse(lsGet('expandedLines'));
            const after = {sameRow: row === document.getElementById('L100'), near: source.style.display,
                offscreen: !document.getElementById('L8500'), far: saved.line_8499 === true,
                hidden: saved.line_199 === true, windowed: renderer === fgTextWindow,
                mounted: document.querySelectorAll('#content .line-container').length,
                records: renderer.visibleNumbers.length};
            const far = await fgEnsureTextLine(8500);
            await fgFill(far.nextElementSibling);
            const farOpen = far.nextElementSibling.style.display;
            toggleExpand(far.dataset.lineId);
            await fgEnsureTextLine(100);
            const revisited = await fgEnsureTextLine(8500);
            await clearFilter();
            const hiddenRow = await fgEnsureTextLine(200);
            return {before, after, farOpen, farClosed: revisited.nextElementSibling?.style.display ?? 'evicted',
                hiddenClosed: hiddenRow.nextElementSibling.style.display};
        }''')
        self.assertEqual(facts['before']['near'], 'none')
        self.assertTrue(facts['before']['offscreen'])
        self.assertFalse(facts['before']['far'])
        self.assertTrue(facts['after']['sameRow'])
        self.assertEqual(facts['after']['near'], 'block')
        self.assertTrue(facts['after']['offscreen'])
        self.assertTrue(facts['after']['far'])
        self.assertFalse(facts['after']['hidden'])
        self.assertTrue(facts['after']['windowed'])
        self.assertLessEqual(facts['after']['mounted'], 160)
        self.assertEqual(facts['after']['records'], facts['before']['records'])
        self.assertEqual(facts['farOpen'], 'block')
        self.assertEqual(facts['farClosed'], 'none')
        self.assertEqual(facts['hiddenClosed'], 'none')

    def test_collapse_all_clears_mounted_and_offscreen_visible_source_states_only(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const renderer = fgTextWindow;
            const rowIds = {};
            for (const number of [8500, 200, 100]) {
                const row = await fgEnsureTextLine(number);
                rowIds[number] = row.dataset.lineId;
                toggleExpand(rowIds[number]);
                await fgFill(row.nextElementSibling);
            }
            const hidden = fgTextModel.locate(renderer.model, 200).record.getAttribute('data-params');
            const filter = document.getElementById('filterInput');
            filter.value = 'func:^leaf$ AND NOT params:^' + hidden + '$';
            await applyFilter();
            await fgEnsureTextLine(100);
            const visible = [100, 8500, 200].map(number => renderer.visibleNumbers.includes(number));
            const offscreen = !document.getElementById('L8500');
            const button = document.querySelector('.btn-collapse');
            const before = performance.getEntriesByType('resource').length;
            await collapseAll(button);
            const after = performance.getEntriesByType('resource').slice(before).map(entry => entry.name);
            const bounded = renderer === fgTextWindow && document.querySelectorAll('#content .line-container').length <= 160;
            const saved = [100, 8500, 200].map(number => expandedLines[rowIds[number]]);
            await clearFilter();
            const hiddenRow = await fgEnsureTextLine(200);
            const hiddenRestored = hiddenRow.nextElementSibling.style.display;
            const farRow = await fgEnsureTextLine(8500);
            const farClosed = farRow.nextElementSibling.style.display;
            await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
            const persisted = [100, 8500, 200].map(number => JSON.parse(lsGet('expandedLines'))[rowIds[number]]);
            return {visible, offscreen, bounded, requestedText: after.some(name => /\\/text-\\d+\\.html$/.test(name)),
                saved, persisted, hiddenRestored, farClosed};
        }''')
        self.assertEqual(facts, dict(visible=[True, True, False], offscreen=True, bounded=True, requestedText=False,
                                     saved=[False, False, True], persisted=[False, False, True],
                                     hiddenRestored='block', farClosed='none'))

    def test_collapse_all_cancels_pending_mounted_source_without_replacing_its_row(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const row = await fgEnsureTextLine(100), content = row.nextElementSibling, button = document.querySelector('.btn-collapse');
            let sourceRequest;
            const loadSource = fgData.loadSource;
            fgData.loadSource = (index, options) => new Promise(resolve => { sourceRequest = {resolve, signal: options.signal}; });
            try {
                toggleExpand(row.dataset.lineId);
                const pending = fgSourceJobs.has(content) && !sourceRequest.signal.aborted;
                const loading = fgSourceJobs.get(content).promise;
                await collapseAll(button);
                sourceRequest.resolve('stale source response');
                await loading;
                return {pending, cancelled: sourceRequest.signal.aborted, stale: content.textContent,
                    display: content.style.display, sameRow: row === document.getElementById('L100'),
                    loadedJobs: fgSourceJobs.size, mounted: document.querySelectorAll('#content .line-container').length};
            } finally { fgData.loadSource = loadSource; }
        }''')
        self.assertLessEqual(facts.pop('mounted'), 160)
        self.assertEqual(facts, dict(pending=True, cancelled=True, stale='', display='none', sameRow=True, loadedJobs=0))

    def test_collapse_all_updates_evicted_source_height_without_moving_the_current_row(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const nextFrame = () => new Promise(requestAnimationFrame);
            const earlier = await fgEnsureTextLine(100);
            toggleExpand(earlier.dataset.lineId);
            const source = earlier.nextElementSibling;
            await fgFill(source);
            const extra = document.createElement('div');
            extra.style.height = '800px';
            source.append(extra);
            await nextFrame(); await nextFrame();
            const current = await fgEnsureTextLine(8500);
            await nextFrame(); await nextFrame();
            const before = { top: current.getBoundingClientRect().top, y: scrollY };
            await collapseAll(document.querySelector('.btn-collapse'));
            await nextFrame(); await nextFrame();
            return { evicted: !earlier.isConnected, retained: current === document.getElementById('L8500'),
                removedHeight: before.y - scrollY, drift: current.getBoundingClientRect().top - before.top };
        }''')
        self.assertTrue(facts['evicted'])
        self.assertTrue(facts['retained'])
        self.assertGreaterEqual(facts['removedHeight'], 780)
        self.assertAlmostEqual(facts['drift'], 0, delta=2)

    def test_native_selection_pins_current_rows_until_cleared(self):
        self.open_report()
        self.page.evaluate('''() => {
            const text=document.querySelector('#L10 .line-content'), range=document.createRange();
            range.selectNodeContents(text); getSelection().addRange(range);
            scrollTo({top:150000,behavior:'instant'});
        }''')
        self.page.wait_for_timeout(180)
        self.assertTrue(self.page.locator('#L10').count())
        self.assertIn('Selection pinned', self.page.locator('.fg-window-notice').inner_text())
        self.page.evaluate('getSelection().removeAllRanges()')
        self.page.wait_for_function('() => !document.getElementById("L10")')
        self.assertLessEqual(self.mounted(), 160)

    def test_print_does_not_silently_print_only_mounted_window(self):
        self.open_report()
        self.page.emulate_media(media='print')
        self.assertEqual(self.page.locator('.fg-window-rows').evaluate('element=>getComputedStyle(element).display'), 'none')
        self.assertIn('Choose Full Text', self.page.locator('#content').evaluate('element=>getComputedStyle(element,"::before").content'))

    def test_rich_metadata_mismatch_is_rejected_before_publishing_detached_rows(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const original = fgData.read('fgTextIndex');
            const errors = [];
            for (const column of [1, original.fields.indexOf('sort-duration') + 2, original.fields.length + 1]) {
                const data = structuredClone(original);
                data.rows[5000][column] = '999999';
                const steps = fgTextModel.prepare(data); let result;
                do { result = steps.next(); } while (!result.done);
                errors.push(await fgLoadTextBlock(result.value, data.rows[5000][0]).then(() => null, error => error.message));
            }
            return errors;
        }''')
        self.assertEqual(facts, ['Text paint block presentation metadata mismatch'] * 3)

    def test_complete_order_sort_depth_and_empty_filter_use_unmounted_metadata(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            await fgSetTextOrder('task');
            const model = fgTextWindow.model, plan = fgTextModel.order(model, 'task');
            const task = JSON.stringify(fgTextWindow.visibleNumbers) === JSON.stringify(plan.order.filter(number => !plan.hidden.has(number)));
            document.getElementById('filterInput').value = 'func:^leaf$';
            await applyFilter();
            const filteredHeading = !!document.querySelector('#content .task-group-head')?.textContent;
            document.getElementById('filterInput').value = 'func:^leaf$ sort:desc';
            await applyFilter();
            const sorted = fgTextWindow.visibleNumbers;
            const expected = model.records.filter(record => record.getAttribute('data-func') === 'leaf')
                .sort((left,right) => +(right.getAttribute('data-sort-duration') || right.getAttribute('data-duration')) -
                    +(left.getAttribute('data-sort-duration') || left.getAttribute('data-duration'))).map(record => record.row[0]);
            const correct = JSON.stringify(sorted) === JSON.stringify(expected);
            document.getElementById('filterInput').value = 'func:^not_a_symbol$';
            await applyFilter();
            const empty = fgTextWindow.visibleNumbers.length;
            await clearFilter();
            await fgSetTextOrder('time');
            foldToDepth(1); await fgRefreshTextWindow();
            const depth = fgTextWindow.visibleNumbers.length;
            unfoldAllBlocks(); await fgRefreshTextWindow();
            return {task, filteredHeading, correct, empty, depth, total:total_lines, restored:fgTextWindow.visibleNumbers.length};
        }''')
        self.assertTrue(facts['task'])
        self.assertTrue(facts['filteredHeading'])
        self.assertTrue(facts['correct'])
        self.assertEqual(facts['empty'], 0)
        self.assertLess(facts['depth'], 20)
        self.assertEqual(facts['total'], facts['restored'])

    def test_duration_order_cache_reuses_complete_plan_and_invalidates_on_direction_or_mode(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const nativeRank=fgTextModel.rankDuration;
            let sorts=0;
            fgTextModel.rankDuration=function(...args) {
                sorts++;
                return nativeRank(...args);
            };
            try {
                fgTextSort='desc'; await fgTextWindow.rebuild();
                const initial=sorts, before=fgTextNavigation.order.slice();
                await fgTextWindow.rebuild();
                const warm=sorts;
                const folding=fgTextModel.folding(fgTextWindow.model);
                const block=[...folding.blocks.values()].find(item=>item.entry?.getAttribute('data-func')==='inner');
                _setFolded(block.id,true); await fgTextWindow.rebuild();
                const folded=fgTextWindow.visibleNumbers.length, foldSorts=sorts;
                _setFolded(block.id,false); await fgTextWindow.rebuild();
                const stable=JSON.stringify(before)===JSON.stringify(fgTextNavigation.order);
                fgTextSort='asc'; await fgTextWindow.rebuild();
                const direction=sorts;
                await fgSetTextOrder('task');
                fgTextSort='asc'; await fgTextWindow.rebuild();
                const grouped=sorts;
                await fgTextWindow.rebuild();
                const groupWarm=sorts;
                const order=fgTextNavigation.order;
                const durations=order.map(number=>+(fgTextModel.locate(fgTextWindow.model,number).record.getAttribute('data-sort-duration')||0));
                return {initial,warm,foldSorts,folded,stable,direction,grouped,groupWarm,
                    sorted:durations.every((duration,index)=>!index||durations[index-1]<=duration),
                    rows:document.querySelectorAll('#content .line-container').length};
            } finally {fgTextModel.rankDuration=nativeRank}
        }''')
        self.assertEqual(facts['initial'], 1)
        self.assertEqual(facts['warm'], 1)
        self.assertEqual(facts['foldSorts'], 1)
        self.assertLess(facts['folded'], 20)
        self.assertTrue(facts['stable'])
        self.assertEqual(facts['direction'], 2)
        self.assertEqual(facts['grouped'], 3)
        self.assertEqual(facts['groupWarm'], 3)
        self.assertTrue(facts['sorted'])
        self.assertLessEqual(facts['rows'], 160)

    def test_duration_ranking_matches_finite_and_nonfinite_complete_reference_in_chromium(self):
        self.open_report()
        cases = self.page.evaluate('''() => {
            let cases=0;
            for(const tokens of [['0','-0','0.00001','12.5','2e3','',null],['NaN','Infinity','-Infinity','garbage','0','12.5']]) {
                const source=fgData.read('fgTextIndex'),data={...source,rows:source.rows.slice(0,31).map(row=>row.slice())};
                for(let index=0;index<data.rows.length;index++) {
                    data.rows[index][data.fields.indexOf('sort-duration')+2]=tokens[index%tokens.length];
                    data.rows[index][data.fields.indexOf('pid')+2]=String(index%3+1);
                    data.rows[index][data.fields.indexOf('cpu')+2]=String(index%2);
                }
                const iterator=fgTextModel.prepare(data);let prepared;
                do {prepared=iterator.next()} while(!prepared.done);
                const model=prepared.value,before=JSON.stringify(data);
                for(const mode of ['time','task']) for(const direction of ['asc','desc']) {
                    const plan=fgTextModel.order(model,mode);
                    const reference=plan.order.map((number,index)=>{
                        const ordinal=plan.ordinals?plan.ordinals[index]:index,record=model.records[ordinal];
                        return {number,ordinal,duration:parseFloat(record.getAttribute('data-sort-duration')||record.getAttribute('data-duration')||'0')};
                    }).sort((left,right)=>direction==='desc'?right.duration-left.duration:left.duration-right.duration);
                    const result=fgTextModel.rankDuration(model,plan,direction);
                    if(JSON.stringify([result.order,[...result.ordinals]])!==JSON.stringify([reference.map(item=>item.number),reference.map(item=>item.ordinal)]))
                        throw new Error('Complete duration ranking changed');
                    if(JSON.stringify(data)!==before) throw new Error('Ranking changed source records');
                    cases++;
                }
            }
            return cases;
        }''')
        self.assertEqual(cases,8)

    def test_rebuild_uses_prepared_ordinals_without_full_model_point_lookups(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const original=fgTextModel.locate, results=[];
            let reads=0, counting=false;
            fgTextModel.locate=function(...args) {if(counting) reads++; return original(...args)};
            try {
                for (const order of ['time','task']) for (const direction of ['','desc','desc','asc','']) {
                    fgTextOrder=order; fgTextSort=direction;
                    reads=0; counting=true;
                    const pending=fgTextWindow.rebuild();
                    counting=false;
                    await pending;
                    results.push({order,direction,reads,mounted:document.querySelectorAll('#content .line-container').length});
                }
                return results;
            } finally {fgTextModel.locate=original}
        }''')
        self.assertEqual(len(facts), 10)
        for result in facts:
            with self.subTest(order=result['order'], direction=result['direction']):
                self.assertLess(result['reads'], 1024)
                self.assertLessEqual(result['mounted'], 160)

    def test_rebuild_updates_complete_navigation_visibility_once_without_full_model_per_line_calls(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const navigation=fgTextNavigation,original=navigation.setVisibility,perLine=navigation.setVisible;
            const root=[...fgTextModel.folding(fgTextWindow.model).blocks.values()].find(block=>block.entry?.getAttribute('data-func')==='root').id;
            const captures=[],syncCalls=[];
            let counting=false,calls=0;
            navigation.setVisibility=function(values) {captures.push(values.slice());return original.call(this,values)};
            navigation.setVisible=function(...arguments) {if(counting) calls++;return perLine.apply(this,arguments)};
            try {
                for(const order of ['time','task']) for(const direction of ['','desc','asc']) {
                    fgTextOrder=order;fgTextSort=direction;foldedBlocks={[root]:true};
                    fgNotifyFilter('',null);calls=0;counting=true;
                    const pending=fgTextWindow.rebuild();counting=false;syncCalls.push(calls);await pending;
                    const mask=captures.at(-1),expected=navigation.order.filter((_,index)=>mask[index]===1);
                    if(mask.length!==fgTextWindow.model.records.length) throw new Error('Incomplete navigation mask');
                    if(JSON.stringify(expected)!==JSON.stringify(fgTextWindow.visibleNumbers)) throw new Error('Display/ordinal masks confused');
                    if(navigation.visibleCount!==expected.length) throw new Error('Wrong navigation count');
                }
                return {calls:captures.length,syncCalls,mounted:document.querySelectorAll('#content .line-container').length};
            } finally {navigation.setVisibility=original;navigation.setVisible=perLine}
        }''')
        self.assertEqual(facts['calls'],6)
        self.assertTrue(all(calls<=160 for calls in facts['syncCalls']),facts['syncCalls'])
        self.assertLessEqual(facts['mounted'],160)

    def test_unfiltered_rebuild_uses_range_masks_and_filtered_rebuild_keeps_selective_predicate(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const model=fgTextWindow.model,folding=fgTextModel.folding(model),original=folding.hiddenMask;
            const root=[...folding.blocks.values()].find(block=>block.entry?.getAttribute('data-func')==='root').id;
            let masks=0;
            folding.hiddenMask=function(...arguments) {masks++;return original.apply(this,arguments)};
            try {
                foldedBlocks={[root]:true};fgNotifyFilter('',null);
                await fgTextWindow.rebuild();
                const unfiltered=masks;
                for(const matches of [new Set(),new Set(model.records.filter((_,index)=>index%11===0).map(record=>record.row[0]))]) {
                    fgNotifyFilter('test',matches);await fgTextWindow.rebuild();
                    const expected=fgTextNavigation.order.filter(number=>folding.visible(fgTextModel.locate(model,number).record,foldedBlocks,matches));
                    if(JSON.stringify(expected)!==JSON.stringify(fgTextWindow.visibleNumbers)) throw new Error('Filtered result changed');
                }
                return {unfiltered,masks,mounted:document.querySelectorAll('#content .line-container').length};
            } finally {folding.hiddenMask=original}
        }''')
        self.assertEqual(facts['unfiltered'],1)
        self.assertEqual(facts['masks'],1)
        self.assertLessEqual(facts['mounted'],160)

    def test_rebuild_visibility_matches_full_reference_after_repeated_folds_filters_and_order_changes(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const model=fgTextWindow.model, folding=fgTextModel.folding(model), original=JSON.stringify(model.records.map(record=>record.row));
            const root=[...folding.blocks.values()].find(block=>block.entry?.getAttribute('data-func')==='root').id;
            const child=[...folding.blocks.values()].find(block=>block.entry?.getAttribute('data-func')==='inner').id;
            let cases=0;
            for (const order of ['time','task']) for(const direction of ['','desc','asc']) {
                fgTextOrder=order; fgTextSort=direction;
                for(const folds of [{},{[root]:true},{[child]:true},{[root]:true,[child]:true},{}]) {
                    foldedBlocks=folds;
                    for(const matches of [null,new Set(model.records.filter((_,index)=>index%7===0).map(record=>record.row[0])),new Set()]) {
                        fgNotifyFilter(matches===null?'':'test',matches);
                        await fgTextWindow.rebuild();
                        const plan=fgTextModel.order(model,order);
                        const expected=fgTextNavigation.order.filter(number=>folding.visible(fgTextModel.locate(model,number).record,folds,matches) &&
                            (direction || order!=='task' || !plan.hidden.has(number)));
                        if(JSON.stringify(expected)!==JSON.stringify(fgTextWindow.visibleNumbers)) throw new Error('Visibility changed');
                        if(fgTextNavigation.visibleCount!==expected.length) throw new Error('Navigation visibility changed');
                        if(document.querySelectorAll('#content .line-container').length>160) throw new Error('Window grew');
                        cases++;
                    }
                }
            }
            return {cases,unchanged:JSON.stringify(model.records.map(record=>record.row))===original};
        }''')
        self.assertEqual(facts['cases'], 90)
        self.assertTrue(facts['unchanged'])

    def test_task_boundary_stream_matches_complete_reference_with_hidden_groups_and_shared_positions(self):
        self.open_task_report()
        facts = self.page.evaluate('''async () => {
            const model=fgTextWindow.model,original=JSON.stringify(model.records.map(record=>record.row)),
                task=fgTextModel.order(model,'task'),nativeOrder=fgTextModel.order;
            const boundaries=[
                {at:0,kind:'task-group-head',text:'A'}, {at:0,kind:'task-gap',text:'A gap'},
                {at:1,kind:'task-gap',text:'hidden gap'},
                {at:2,kind:'task-group-head',text:'B'}, {at:2,kind:'task-gap',text:'B gap'},
                {at:4,kind:'task-gap',text:'B later gap'},
                {at:5,kind:'task-group-head',text:'C'}, {at:5,kind:'task-gap',text:'C gap'},
                {at:7,kind:'task-group-head',text:'D'}, {at:7,kind:'task-gap',text:'D gap'},
                {at:task.order.length-1,kind:'task-group-head',text:'trailing hidden group'}];
            const plan={...task,boundaries,hidden:new Set([task.order[1],task.order[6]])};
            const preserved=JSON.stringify([plan.order,[...plan.ordinals],plan.boundaries,[...plan.hidden]]);
            fgTextModel.order=(target,mode)=>target===model && mode==='task'?plan:nativeOrder(target,mode);
            let cases=0,headings=0,gaps=0;
            try {
                for(const mode of ['task','time']) for(const direction of ['','desc','asc'])
                    for(const indices of [[],[0],[1],[3],[4],[7],[8],[0,3,7],[0,4,8]]) {
                        const matches=new Set(indices.map(index=>task.order[index]));
                        fgTextOrder=mode;fgTextSort=direction;fgNotifyFilter('test',matches);
                        await fgTextWindow.rebuild();
                        const active=fgTextModel.order(model,mode),order=fgTextNavigation.order;
                        const expected=[],byNumber=new Map();
                        if(!direction) for(const boundary of active.boundaries) {
                            const number=active.order[boundary.at];
                            if(!byNumber.has(number)) byNumber.set(number,[]);
                            byNumber.get(number).push(boundary);
                        }
                        let pending=[];
                        for(const number of order) {
                            for(const boundary of byNumber.get(number)||[]) {
                                if(boundary.kind==='task-group-head') pending=[boundary];
                                else pending.push(boundary);
                            }
                            if(!matches.has(number) || (!direction && mode==='task' && active.hidden.has(number))) continue;
                            expected.push(...pending.map(boundary=>[boundary.kind,boundary.text]),number);pending=[];
                        }
                        if(mode==='task' && !direction && indices.join(',')==='0,3,7') {
                            const known=[['task-group-head','A'],['task-gap','A gap'],task.order[0],
                                ['task-group-head','B'],['task-gap','B gap'],task.order[3],
                                ['task-group-head','D'],['task-gap','D gap'],task.order[7]];
                            if(JSON.stringify(expected)!==JSON.stringify(known)) throw new Error('Reference fixture changed');
                        }
                        const actual=[...document.querySelector('.fg-window-rows').children].map(element=>
                            element.dataset.windowLine?+element.dataset.windowLine:[element.firstElementChild.className,element.textContent]);
                        if(JSON.stringify(actual)!==JSON.stringify(expected)) throw new Error('Boundary projection changed: '+JSON.stringify({mode,direction,indices,expected,actual}));
                        const numbers=expected.filter(item=>typeof item==='number');
                        if(JSON.stringify(numbers)!==JSON.stringify(fgTextWindow.visibleNumbers) || fgTextNavigation.visibleCount!==numbers.length)
                            throw new Error('Complete visible/navigation order changed');
                        headings+=expected.filter(item=>item[0]==='task-group-head').length;
                        gaps+=expected.filter(item=>item[0]==='task-gap').length;
                        cases++;
                    }
            } finally { fgTextModel.order=nativeOrder; }
            return {cases,headings,gaps,unchanged:JSON.stringify(model.records.map(record=>record.row))===original &&
                JSON.stringify([plan.order,[...plan.ordinals],plan.boundaries,[...plan.hidden]])===preserved};
        }''')
        self.assertEqual(facts['cases'], 54)
        self.assertGreater(facts['headings'], 6)
        self.assertGreater(facts['gaps'], 6)
        self.assertTrue(facts['unchanged'])

    def test_cooperative_duration_pending_retains_complete_order_dom_and_event_loop(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            const order=fgTextNavigation.order.slice(),visible=fgTextWindow.visibleNumbers,
                markup=document.querySelector('.fg-window-rows').innerHTML,count=fgTextNavigation.visibleCount;
            beginDuration('desc');
            let heartbeats=0;await new Promise(resolve=>setTimeout(()=>{heartbeats++;resolve()},0));
            dispatchEvent(new Event('scroll'));await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
            const retained=JSON.stringify(order)===JSON.stringify(fgTextNavigation.order) &&
                JSON.stringify(visible)===JSON.stringify(fgTextWindow.visibleNumbers) && count===fgTextNavigation.visibleCount &&
                markup===document.querySelector('.fg-window-rows').innerHTML;
            const busy=document.getElementById('content').getAttribute('aria-busy');
            await durationGates[0].release();await durationWork;
            const expected=fgTextModel.rankDuration(fgTextWindow.model,fgTextModel.order(fgTextWindow.model,'time'),'desc').order;
            return {retained,busy,heartbeats,sorted:JSON.stringify(expected)===JSON.stringify(fgTextNavigation.order),
                ready:!document.getElementById('content').hasAttribute('aria-busy')};
        }''')
        self.assertEqual(facts, dict(retained=True, busy='true', heartbeats=1, sorted=True, ready=True))
        self.assertLessEqual(self.mounted(), 160)

    def test_cooperative_duration_latest_direction_wins_and_stale_progress_cannot_publish(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            beginDuration('desc');const previous=durationWork;
            beginDuration('asc');const current=durationWork;
            const cancelled=durationGates[0].signal.aborted;
            await durationGates[1].release();await current;
            const order=fgTextNavigation.order.slice(),notice=document.querySelector('.fg-window-notice [role=status]').textContent;
            durationGates[0].progress('STALE');await durationGates[0].release();const old=await previous;
            const stable=JSON.stringify(order)===JSON.stringify(fgTextNavigation.order) &&
                notice===document.querySelector('.fg-window-notice [role=status]').textContent;
            await fgTextWindow.rebuild();
            const expected=fgTextModel.rankDuration(fgTextWindow.model,fgTextModel.order(fgTextWindow.model,'time'),'asc').order;
            return {cancelled,old,stable,gates:durationGates.length,sorted:JSON.stringify(expected)===JSON.stringify(order)};
        }''')
        self.assertEqual(facts, dict(cancelled=True, old='AbortError', stable=True, gates=2, sorted=True))

    def test_cooperative_duration_task_order_supersedes_pending_rank(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            beginDuration('desc');const previous=durationWork;
            await fgSetTextOrder('task');
            const order=fgTextNavigation.order.slice();
            await durationGates[0].release();
            return {old:await previous,cancelled:durationGates[0].signal.aborted,mode:fgTextOrder,sort:fgTextSort,
                stable:JSON.stringify(order)===JSON.stringify(fgTextNavigation.order),
                correct:JSON.stringify(order)===JSON.stringify(fgTextModel.order(fgTextWindow.model,'task').order)};
        }''')
        self.assertEqual(facts, dict(old='AbortError', cancelled=True, mode='task', sort='', stable=True, correct=True))

    def test_cooperative_duration_view_resume_rebuilds_at_unchanged_width(self):
        self.open_gated_duration_report()
        self.page.evaluate("() => {beginDuration('desc');window.previousDuration=durationWork}")
        self.assertTrue(self.page.evaluate('async () => !!(await fgSwitchView("timeline"))'))
        self.assertTrue(self.page.evaluate('durationGates[0].signal.aborted'))
        self.assertEqual(self.page.evaluate('''async () => {
            dispatchEvent(new Event('resize'));await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
            return durationGates.length;
        }'''), 1)
        self.assertTrue(self.page.evaluate('async () => !!(await fgSwitchView("text"))'))
        self.page.wait_for_function('durationGates.length===2')
        facts = self.page.evaluate('''async () => {
            await durationGates[0].release();const old=await previousDuration;
            await durationGates[1].release();await fgTextWindow.settled;
            const expected=fgTextModel.rankDuration(fgTextWindow.model,fgTextModel.order(fgTextWindow.model,'time'),'desc').order;
            return {old,sort:fgTextSort,sorted:JSON.stringify(expected)===JSON.stringify(fgTextNavigation.order),
                ready:!document.getElementById('content').hasAttribute('aria-busy')};
        }''')
        self.assertEqual(facts, dict(old='AbortError', sort='desc', sorted=True, ready=True))

    def test_cooperative_duration_navigation_waits_for_latest_order_and_can_cancel_independently(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            beginDuration('desc');const first=durationWork,controller=new AbortController();
            const cancelled=fgTextWindow.ensureLine(20000,{signal:controller.signal}).then(()=>false,error=>error.name);
            let finished=false;
            const navigation=fgTextWindow.ensureLine(20000).then(row=>{finished=true;return row?.id});
            controller.abort();const cancelledResult=await cancelled;
            beginDuration('asc');const latest=durationWork;
            await durationGates[0].release();await first;
            const waiting=!finished;
            await durationGates[1].release();await latest;
            return {cancelledResult,waiting,row:await navigation,rankCancelled:durationGates[1].signal.aborted};
        }''')
        self.assertEqual(facts, dict(cancelledResult='AbortError', waiting=True, row='L20000', rankCancelled=False))

    def test_cooperative_duration_invalid_complete_order_keeps_cache_and_retry_recovers(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            const originalOrder=fgTextNavigation.order.slice(),native=fgTextModel.rankDurationAsync;
            fgTextModel.rankDurationAsync=async()=>({order:[2],ordinals:new Uint32Array([0])});
            beginDuration('desc');const error=await durationWork;
            const retained=JSON.stringify(originalOrder)===JSON.stringify(fgTextNavigation.order),
                retry=document.querySelector('.fg-window-notice button'),notice=document.querySelector('.fg-window-notice [role=status]').textContent;
            fgTextModel.rankDurationAsync=native;retry.click();
            await durationGates[0].release();await fgTextWindow.settled;
            const expected=fgTextModel.rankDuration(fgTextWindow.model,fgTextModel.order(fgTextWindow.model,'time'),'desc').order;
            return {error,retained,retryOff:retry.hidden,notice,sorted:JSON.stringify(expected)===JSON.stringify(fgTextNavigation.order)};
        }''')
        self.assertEqual(facts['error'], 'Error')
        self.assertTrue(facts['retained'])
        self.assertTrue(facts['retryOff'])
        self.assertIn('Previous window retained; Retry', facts['notice'])
        self.assertTrue(facts['sorted'])

    def test_cooperative_duration_full_text_failure_preserves_notice_and_allows_order_retry(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            beginDuration('desc');const previous=durationWork,load=fgData.loadTextBlocks,renderer=fgTextWindow;
            fgData.loadTextBlocks=async()=>{throw new Error('full text unavailable')};
            const error=await renderer.fullText().catch(error=>error.message);
            const notice=document.querySelector('.fg-window-notice [role=status]').textContent,
                retained=renderer===fgTextWindow,busy=document.getElementById('content').hasAttribute('aria-busy');
            fgData.loadTextBlocks=load;await durationGates[0].release();const old=await previous;
            const stableNotice=notice===document.querySelector('.fg-window-notice [role=status]').textContent;
            document.querySelector('.fg-window-notice button').click();
            await durationGates[1].release();await renderer.settled;
            return {error,notice,retained,busy,old,stableNotice,sort:fgTextSort};
        }''')
        self.assertEqual(facts['error'], 'full text unavailable')
        self.assertIn('Complete Text not loaded', facts['notice'])
        self.assertIn('Retry the order', facts['notice'])
        self.assertTrue(facts['retained'])
        self.assertFalse(facts['busy'])
        self.assertEqual(facts['old'], 'AbortError')
        self.assertTrue(facts['stableNotice'])
        self.assertEqual(facts['sort'], 'desc')

    def test_cooperative_duration_full_text_success_cannot_be_overwritten_by_late_rank(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            beginDuration('desc');const previous=durationWork,renderer=fgTextWindow;
            await renderer.fullText();const markup=document.getElementById('content').innerHTML;
            durationGates[0].progress('STALE');await durationGates[0].release();
            return {old:await previous,cancelled:durationGates[0].signal.aborted,full:fgTextWindow===null,
                unchanged:markup===document.getElementById('content').innerHTML,
                count:document.querySelectorAll('#content .line-container').length,total:total_lines};
        }''')
        self.assertEqual(facts['old'], 'AbortError')
        self.assertTrue(facts['cancelled'])
        self.assertTrue(facts['full'])
        self.assertTrue(facts['unchanged'])
        self.assertEqual(facts['count'], facts['total'])

    def test_cooperative_duration_new_filter_cannot_be_overwritten_by_pending_sorted_filter(self):
        self.open_gated_duration_report()
        self.page.evaluate('''() => {
            document.getElementById('filterInput').value='func:leaf sort:desc';
            window.previousSortedFilter=applyFilter();
        }''')
        self.page.wait_for_function('durationGates.length===1')
        facts = self.page.evaluate('''async () => {
            document.getElementById('filterInput').value='func:^absent$';await applyFilter();
            const order=fgTextNavigation.order.slice();
            await durationGates[0].release();await previousSortedFilter;
            const retained={text:fgFilterState.text,visible:fgTextNavigation.visibleCount,sort:fgTextSort,
                stable:JSON.stringify(order)===JSON.stringify(fgTextNavigation.order)};
            await clearFilter();return {...retained,restored:fgTextNavigation.visibleCount===fgTextWindow.model.records.length};
        }''')
        self.assertEqual(facts, dict(text='func:^absent$', visible=0, sort='', stable=True, restored=True))

    def test_pending_sorted_filter_keeps_the_last_complete_applied_result(self):
        self.open_gated_duration_report()
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value='func:leaf';
            await applyFilter();
            window.previousWindowFilter = fgFilterState;
            window.previousWindowOrder = fgTextNavigation.order.slice();
            document.getElementById('filterInput').value='func:^leaf$ sort:desc';
            window.pendingSortedFilter = applyFilter();
        }''')
        self.page.wait_for_function('() => durationGates.length === 1')
        facts = self.page.evaluate('''() => ({
            applied: fgFilterState.text,
            previous: previousWindowFilter.text,
            sameLines: fgFilterState.lines === previousWindowFilter.lines,
            stableOrder: JSON.stringify(fgTextNavigation.order) === JSON.stringify(previousWindowOrder),
            pending: !!fgFilterProgress && !fgFilterProgress.error,
            requested: fgFilterProgress?.text
        })''')
        self.assertEqual(facts, dict(applied='func:leaf', previous='func:leaf', sameLines=True,
                                    stableOrder=True, pending=True, requested='func:^leaf$ sort:desc'))

    def test_cancelled_sorted_filter_aborts_ranking_and_keeps_navigation_usable(self):
        self.open_gated_duration_report()
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value='func:leaf'; await applyFilter();
            window.previousWindowFilter=fgFilterState;
            window.previousWindowOrder=fgTextNavigation.order.slice();
            document.getElementById('filterInput').value='func:^leaf$ sort:desc';
            window.pendingSortedFilter=applyFilter();
        }''')
        self.page.wait_for_function('() => durationGates.length === 1')
        facts = self.page.evaluate('''async () => {
            fgCancelPendingFilter();
            const aborted=durationGates[0].signal.aborted;
            durationGates[0].progress('STALE SORT');
            await durationGates[0].release();
            const applied=await pendingSortedFilter;
            const stable=fgFilterState===previousWindowFilter && fgTextSort==='' &&
                JSON.stringify(fgTextNavigation.order)===JSON.stringify(previousWindowOrder);
            const row=await fgTextWindow.ensureLine(20000);
            return {aborted,applied,stable,pending:fgFilterProgress,
                navigated:row.id==='L20000' && row.isConnected,
                stale:document.querySelector('.fg-window-notice').textContent.includes('STALE SORT')};
        }''')
        self.assertEqual(facts, dict(aborted=True, applied=None, stable=True, pending=None,
                                    navigated=True, stale=False))

    def test_pending_remote_filter_does_not_publish_navigation_or_replace_rows(self):
        self.open_gated_duration_report()
        self.page.evaluate('''() => {
            window.previousWindowFilter=fgFilterState;
            window.previousWindowNumbers=fgTextWindow.visibleNumbers.slice();
            window.previousWindowRows=Array.from(document.querySelectorAll('#content .line-container'));
            window.previousVisibleCount=fgTextNavigation.visibleCount;
            const original=fgData.loadTextBlocks;
            window.restoreWindowBlocks=()=>{fgData.loadTextBlocks=original};
            window.windowPaintGates=[];
            fgData.loadTextBlocks=(ordinals,options)=>new Promise((resolve,reject)=>{
                const abort=()=>reject(new DOMException('Test paint cancelled','AbortError'));
                options.signal.addEventListener('abort',abort,{once:true});
                windowPaintGates.push({signal:options.signal,fail:reject,
                    release:async()=>resolve(await original(ordinals,{...options,signal:undefined}))});
            });
            document.getElementById('filterInput').value='params:^arg=24000$';
            window.pendingRemoteFilter=applyFilter();
        }''')
        self.page.wait_for_function('() => windowPaintGates.length === 1')
        self.page.get_by_text('Loading Text rows: 0 / 1…', exact=True).wait_for()
        facts = self.page.evaluate('''async () => {
            const before={sameState:fgFilterState===previousWindowFilter,
                sameNumbers:JSON.stringify(fgTextWindow.visibleNumbers)===JSON.stringify(previousWindowNumbers),
                sameCount:fgTextNavigation.visibleCount===previousVisibleCount,
                sameRows:previousWindowRows.every(row=>row.isConnected),
                pending:!!fgFilterProgress && !fgFilterProgress.error};
            fgCancelPendingFilter();
            const result=await pendingRemoteFilter;
            restoreWindowBlocks();
            await windowPaintGates[0].release();
            const row=await fgTextWindow.ensureLine(20000);
            return {...before,result,aborted:windowPaintGates[0].signal.aborted,
                retained:fgFilterState===previousWindowFilter,
                navigated:row.id==='L20000' && row.isConnected,
                pendingAfter:fgFilterProgress};
        }''')
        self.assertEqual(facts, dict(sameState=True, sameNumbers=True, sameCount=True, sameRows=True,
                                    pending=True, result=None, aborted=True, retained=True,
                                    navigated=True, pendingAfter=None))
        self.assertNotIn('Preparing', self.page.locator('.fg-window-notice [role=status]').inner_text())

    def test_matching_progress_is_visible_and_cancel_restores_the_previous_notice(self):
        self.open_report()
        self.page.evaluate('''() => {
            window.previousWindowFilter=fgFilterState;
            window.matchingFilterGates=[];
            const original=fgIndexedFilter;
            window.restoreIndexedWindowFilter=()=>{window.fgIndexedFilter=original};
            window.fgIndexedFilter=(data,text,signal,progress)=>new Promise((resolve,reject)=>{
                signal.addEventListener('abort',()=>reject(new DOMException('Test matching cancelled','AbortError')),{once:true});
                matchingFilterGates.push({signal,progress});
            });
            document.getElementById('filterInput').value='func:leaf';
            window.pendingMatchingFilter=applyFilter();
        }''')
        self.page.wait_for_function('() => matchingFilterGates.length === 1')
        self.assertIn('Preparing full-trace filter', self.page.locator('.fg-window-notice [role=status]').inner_text())
        facts = self.page.evaluate('''async () => {
            fgCancelPendingFilter();const result=await pendingMatchingFilter;
            matchingFilterGates[0].progress('STALE MATCHING');restoreIndexedWindowFilter();
            return {result,aborted:matchingFilterGates[0].signal.aborted,
                retained:fgFilterState===previousWindowFilter,pending:fgFilterProgress,
                notice:document.querySelector('.fg-window-notice [role=status]').textContent};
        }''')
        self.assertIsNone(facts['result'])
        self.assertTrue(facts['aborted'])
        self.assertTrue(facts['retained'])
        self.assertIsNone(facts['pending'])
        self.assertNotIn('Preparing', facts['notice'])
        self.assertNotIn('STALE', facts['notice'])

    def open_pending_event_filter(self, phase='blocks', report='mixed', text='space:user sort:desc', previous=''):
        self.page.goto(urljoin(self.server.url, report + '/index.html?text=windowed'))
        self.assertTrue(self.page.evaluate('async () => !!(await fgSwitchView("text"))'))
        if previous:
            self.page.evaluate('''async text => {
                document.getElementById('filterInput').value=text;await applyFilter();
            }''', previous)
        self.page.evaluate('async () => {window.previousEventTimeline=JSON.stringify(await fgData.load("tlData"))}')
        self.page.evaluate('''({phase,text}) => {
            window.previousEventFilter=fgFilterState;
            window.previousEventModel=JSON.stringify(fgTextWindow.model.records.map(record=>record.row));
            window.eventFilterMatches=0;
            window.eventFilterRanks=0;
            const match=fgIndexedFilter,load=fgData.loadTextBlocks;
            const rank=fgTextModel.rankDuration,rankAsync=fgTextModel.rankDurationAsync;
            window.restoreEventFilter=()=>{window.fgIndexedFilter=match;fgData.loadTextBlocks=load;
                fgTextModel.rankDuration=rank;fgTextModel.rankDurationAsync=rankAsync};
            fgTextModel.rankDuration=(...args)=>{eventFilterRanks++;return rank(...args)};
            fgTextModel.rankDurationAsync=(...args)=>{eventFilterRanks++;return rankAsync(...args)};
            window.eventFilterGates=[];
            window.fgIndexedFilter=(...args)=>{
                eventFilterMatches++;
                if(phase!=='matching')return match(...args);
                return new Promise((resolve,reject)=>{
                    args[2].addEventListener('abort',()=>reject(new DOMException('Matching cancelled','AbortError')),{once:true});
                    eventFilterGates.push({signal:args[2],release:async()=>resolve(await match(...args))});
                });
            };
            fgData.loadTextBlocks=(ordinals,options)=>{
                if(phase==='matching' || eventFilterGates.length)return load(ordinals,options);
                return new Promise((resolve,reject)=>{
                    options.signal.addEventListener('abort',()=>reject(new DOMException('Event projection superseded','AbortError')),{once:true});
                    eventFilterGates.push({signal:options.signal,
                        release:async()=>resolve(await load(ordinals,{...options,signal:undefined}))});
                });
            };
            document.getElementById('filterInput').value=text;
            window.pendingEventFilter=applyFilter();
        }''', dict(phase=phase, text=text))
        self.page.wait_for_function('() => eventFilterGates.length === 1')

    def gate_event_projection(self):
        self.page.evaluate('''() => {
            const load=fgData.loadTextBlocks;
            window.restoreEventProjection=()=>{fgData.loadTextBlocks=load};
            window.eventProjectionGates=[];
            fgData.loadTextBlocks=(ordinals,options)=>new Promise((resolve,reject)=>{
                options.signal.addEventListener('abort',()=>reject(new DOMException('Event visibility cancelled','AbortError')),{once:true});
                eventProjectionGates.push({signal:options.signal,fail:reject,
                    release:async()=>resolve(await load(ordinals,{...options,signal:undefined}))});
            });
            window.eventProjectionWork=fgSetTextEventsVisible(false);
        }''')
        self.page.wait_for_function('() => eventProjectionGates.length > 0')

    def test_event_toggle_preserves_a_pending_filter_and_reuses_complete_matches(self):
        self.open_pending_event_filter()
        self.page.locator('#textEventsBtn').click()
        facts = self.page.evaluate('''async () => {
            await fgTextWindow.settled;
            const during={retained:fgFilterProgress?.text==='space:user sort:desc' && !fgFilterProgress.error ||
                    fgFilterState.text==='space:user sort:desc',
                hidden:document.querySelectorAll('#content [data-trace-event="1"]').length===0};
            await eventFilterGates[0].release();
            const applied=await pendingEventFilter;
            restoreEventFilter();
            const expected=fgTextWindow.model.records.filter(record=>record.getAttribute('data-space')==='user')
                .map(record=>record.row[0]).sort((left,right)=>left-right);
            return {during,applied,matches:eventFilterMatches,ranks:eventFilterRanks,text:fgFilterState.text,
                complete:JSON.stringify([...fgFilterState.lines||[]].sort((left,right)=>left-right))===JSON.stringify(expected),
                model:JSON.stringify(fgTextWindow.model.records.map(record=>record.row))===previousEventModel,
                timeline:JSON.stringify(fgData.read('tlData'))===previousEventTimeline,
                hidden:!fgTextEventsVisible && document.getElementById('textEventsBtn').textContent==='Events: hidden',
                finished:fgFilterProgress===null,sort:fgTextSort};
        }''')
        self.assertEqual(facts, dict(during=dict(retained=True, hidden=True), applied=True,
                                    matches=1, ranks=1, text='space:user sort:desc', complete=True, model=True,
                                    timeline=True, hidden=True, finished=True, sort='desc'))

    def test_event_toggle_reuses_a_complete_cooperative_rank_while_loading_rows(self):
        self.open_pending_event_filter(report='scattered-events', text='func:leaf sort:desc')
        self.page.locator('#textEventsBtn').click()
        facts = self.page.evaluate('''async () => {
            await fgTextWindow.settled;await eventFilterGates[0].release();
            const applied=await pendingEventFilter;
            restoreEventFilter();
            const model=fgTextWindow.model,plan=fgTextModel.order(model,'time');
            const expected=fgTextModel.rankDuration(model,plan,'desc').order;
            return {applied,matches:eventFilterMatches,ranks:eventFilterRanks,
                complete:fgFilterState.lines.size===24576,
                sorted:JSON.stringify(expected)===JSON.stringify(fgTextNavigation.order),
                visible:fgTextWindow.visibleNumbers.length===24576,
                unchanged:JSON.stringify(model.records.map(record=>record.row))===previousEventModel &&
                    JSON.stringify(fgData.read('tlData'))===previousEventTimeline,
                hidden:!fgTextEventsVisible && document.querySelectorAll('#content [data-trace-event="1"]').length===0,
                finished:fgFilterProgress===null,mounted:document.querySelectorAll('#content .line-container').length};
        }''')
        mounted = facts.pop('mounted')
        self.assertEqual(facts, dict(applied=True, matches=1, ranks=1, complete=True, sorted=True,
                                    visible=True, unchanged=True, hidden=True, finished=True))
        self.assertLessEqual(mounted, 160)

    def test_event_refresh_of_committed_opposite_sort_keeps_pending_rank(self):
        self.open_pending_event_filter(previous='space:user sort:asc')
        self.page.locator('#textEventsBtn').click()
        facts = self.page.evaluate('''async () => {
            await fgTextWindow.settled;await eventFilterGates[0].release();
            const applied=await pendingEventFilter;
            restoreEventFilter();
            const expected=fgTextModel.rankDuration(fgTextWindow.model,fgTextModel.order(fgTextWindow.model,'time'),'desc').order;
            return {applied,previous:previousEventFilter.text,text:fgFilterState.text,
                matches:eventFilterMatches,ranks:eventFilterRanks,
                sorted:JSON.stringify(expected)===JSON.stringify(fgTextNavigation.order),
                unchanged:JSON.stringify(fgData.read('tlData'))===previousEventTimeline};
        }''')
        self.assertEqual(facts, dict(applied=True, previous='space:user sort:asc', text='space:user sort:desc',
                                    matches=1, ranks=1, sorted=True, unchanged=True))

    def test_prepared_rank_is_single_entry_exact_plan_and_direction_and_renderer_local(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const model=fgTextWindow.model,nativeRank=fgTextModel.rankDuration,nativePlan=fgTextModel.order,
                nativeLoad=fgData.loadTextBlocks,original=JSON.stringify(fgTextNavigation.order),
                records=JSON.stringify(model.records.map(record=>record.row));
            let ranks=0;const counts=[],errors=[];
            fgTextModel.rankDuration=(...args)=>{ranks++;return nativeRank(...args)};
            fgData.loadTextBlocks=async()=>{throw new Error('Prepared row fetch failed')};
            const attempt=async(mode,direction)=>{
                fgTextOrder=mode;fgTextSort=direction;
                errors.push(await fgTextWindow.rebuild().then(()=>null,error=>error.name));
                counts.push(ranks);
                if(JSON.stringify(fgTextNavigation.order)!==original)throw new Error('Failed preparation replaced navigation');
            };
            try {
                await attempt('time','desc');await attempt('time','desc');
                await attempt('task','desc');await attempt('time','desc');
                await attempt('time','asc');await attempt('time','asc');
                fgTextModel.order=(...args)=>({...nativePlan(...args)});
                await attempt('time','asc');fgTextModel.order=nativePlan;
                await attempt('time','asc');await attempt('time','asc');
                const previous=fgTextWindow;previous.dispose();
                fgTextWindow=fgCreateTextWindow(model,previous.navigation,previous.root);
                await attempt('time','asc');
                fgData.loadTextBlocks=nativeLoad;await fgTextWindow.rebuild();
                const expected=nativeRank(model,nativePlan(model,'time'),'asc').order;
                return {counts,errors,ranks,sorted:JSON.stringify(expected)===JSON.stringify(fgTextNavigation.order),
                    records:JSON.stringify(model.records.map(record=>record.row))===records,
                    mounted:document.querySelectorAll('#content .line-container').length};
            } finally {fgTextModel.rankDuration=nativeRank;fgTextModel.order=nativePlan;fgData.loadTextBlocks=nativeLoad}
        }''')
        self.assertEqual(facts['counts'], [1, 1, 2, 3, 4, 4, 5, 6, 6, 7])
        self.assertEqual(facts['errors'], ['Error'] * 10)
        self.assertEqual(facts['ranks'], 7)
        self.assertTrue(facts['sorted'])
        self.assertTrue(facts['records'])
        self.assertLessEqual(facts['mounted'], 160)

    def test_invalid_prepared_rank_cannot_poison_a_complete_order_retry(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const model=fgTextWindow.model,native=fgTextModel.rankDuration;
            const errors=[],stable=[];let ranks=0;
            const corruptions=[
                result=>{result.order.pop()},
                result=>{result.ordinals[0]=result.ordinals[1];result.order[0]=result.order[1]},
                result=>{result.ordinals[0]=model.records.length},
                result=>{[result.order[0],result.order[1]]=[result.order[1],result.order[0]]},
                result=>{result.ordinals=[...result.ordinals]}
            ];
            try {
                for(const corrupt of corruptions) {
                    fgTextSort='';await fgTextWindow.rebuild();
                    const previous=JSON.stringify(fgTextNavigation.order);
                    fgTextModel.rankDuration=(...args)=>{ranks++;const result=native(...args);corrupt(result);return result};
                    fgTextSort='desc';errors.push(await fgTextWindow.rebuild().then(()=>null,error=>error.message));
                    stable.push(previous===JSON.stringify(fgTextNavigation.order));
                    fgTextModel.rankDuration=(...args)=>{ranks++;return native(...args)};
                    await fgTextWindow.rebuild();
                    const expected=native(model,fgTextModel.order(model,'time'),'desc').order;
                    if(JSON.stringify(expected)!==JSON.stringify(fgTextNavigation.order))throw new Error('Invalid order poisoned retry');
                }
                return {errors,stable,ranks};
            } finally {fgTextModel.rankDuration=native}
        }''')
        self.assertEqual(facts['errors'], ['Invalid complete Text duration order'] * 5)
        self.assertEqual(facts['stable'], [True] * 5)
        self.assertEqual(facts['ranks'], 10)

    def test_event_toggle_during_matching_keeps_the_query_and_latest_visibility(self):
        self.open_pending_event_filter('matching')
        self.page.locator('#textEventsBtn').click()
        facts = self.page.evaluate('''async () => {
            await fgTextWindow.settled;
            const pending=fgFilterState===previousEventFilter &&
                fgFilterProgress?.text==='space:user sort:desc' && !eventFilterGates[0].signal.aborted;
            await eventFilterGates[0].release();const applied=await pendingEventFilter;restoreEventFilter();
            return {pending,applied,matches:eventFilterMatches,text:fgFilterState.text,
                hidden:!fgTextEventsVisible,finished:fgFilterProgress===null};
        }''')
        self.assertEqual(facts, dict(pending=True, applied=True, matches=1,
                                    text='space:user sort:desc', hidden=True, finished=True))

    def test_event_toggle_during_duration_ranking_keeps_complete_filter_results(self):
        self.page.goto(urljoin(self.server.url, 'scattered-events/index.html?text=windowed'))
        self.assertTrue(self.page.evaluate('async () => !!(await fgSwitchView("text"))'))
        self.page.evaluate('''() => {
            window.eventRankGates=[];window.eventRankMatches=0;
            const rank=fgTextModel.rankDurationAsync,match=fgIndexedFilter;
            window.restoreEventRanking=()=>{fgTextModel.rankDurationAsync=rank;window.fgIndexedFilter=match};
            window.fgIndexedFilter=(...args)=>{eventRankMatches++;return match(...args)};
            fgTextModel.rankDurationAsync=(model,plan,direction,signal,progress)=>{
                if(eventRankGates.length)return rank(model,plan,direction,signal,progress);
                return new Promise((resolve,reject)=>{
                    signal.addEventListener('abort',()=>reject(new DOMException('Ranking superseded','AbortError')),{once:true});
                    eventRankGates.push({signal,release:async()=>resolve(await rank(model,plan,direction,undefined,progress))});
                });
            };
            document.getElementById('filterInput').value='func:leaf sort:desc';
            window.eventRankWork=applyFilter();
        }''')
        self.page.wait_for_function('() => eventRankGates.length === 1')
        self.page.locator('#textEventsBtn').click()
        facts = self.page.evaluate('''async () => {
            await eventRankGates[0].release();const applied=await eventRankWork;restoreEventRanking();
            const model=fgTextWindow.model,plan=fgTextModel.order(model,'time');
            const expected=fgTextModel.rankDuration(model,plan,'desc').order
                .filter(number=>fgTextModel.locate(model,number).record.getAttribute('data-func')==='leaf');
            return {applied,aborted:eventRankGates[0].signal.aborted,matches:eventRankMatches,
                complete:JSON.stringify(fgTextWindow.visibleNumbers)===JSON.stringify(expected),
                count:fgFilterState.lines.size,hidden:!fgTextEventsVisible,finished:fgFilterProgress===null};
        }''')
        self.assertEqual(facts, dict(applied=True, aborted=True, matches=1, complete=True,
                                    count=24576, hidden=True, finished=True))

    def test_rapid_event_toggles_keep_the_last_preference_and_the_pending_query(self):
        self.open_pending_event_filter()
        facts = self.page.evaluate('''async () => {
            const changes=[];
            for(const visible of [false,true,false,true,false])changes.push(fgSetTextEventsVisible(visible));
            await Promise.all(changes);await eventFilterGates[0].release();
            const applied=await pendingEventFilter;
            restoreEventFilter();
            return {applied,matches:eventFilterMatches,text:fgFilterState.text,finished:fgFilterProgress===null,
                hidden:!fgTextEventsVisible && fgTextWindow.visibleNumbers.every(number=>
                    fgTextModel.locate(fgTextWindow.model,number).record.getAttribute('data-trace-event')!=='1'),
                model:JSON.stringify(fgTextWindow.model.records.map(record=>record.row))===previousEventModel};
        }''')
        self.assertEqual(facts, dict(applied=True, matches=1, text='space:user sort:desc',
                                    finished=True, hidden=True, model=True))

    def test_filter_cancellation_does_not_wait_for_or_cancel_event_visibility_loading(self):
        self.open_pending_event_filter()
        self.gate_event_projection()
        self.page.evaluate('''() => {
            window.cancelledEventFilter='waiting';
            pendingEventFilter.then(result=>{window.cancelledEventFilter=result});
            fgCancelPendingFilter();
        }''')
        self.page.wait_for_function('() => cancelledEventFilter !== "waiting"')
        facts = self.page.evaluate('''async () => {
            const before={result:cancelledEventFilter,retained:fgFilterState===previousEventFilter,
                pending:fgFilterProgress,displayContinues:eventProjectionGates.every(gate=>!gate.signal.aborted)};
            restoreEventProjection();await Promise.all(eventProjectionGates.map(gate=>gate.release()));
            await eventProjectionWork;await eventFilterGates[0].release();restoreEventFilter();
            return {...before,hidden:!fgTextEventsVisible &&
                document.querySelectorAll('#content [data-trace-event="1"]').length===0,
                unchanged:fgFilterState===previousEventFilter};
        }''')
        self.assertEqual(facts, dict(result=None, retained=True, pending=None, displayContinues=True,
                                    hidden=True, unchanged=True))

    def test_event_refresh_does_not_resume_a_filter_after_explicit_order_change(self):
        self.open_pending_event_filter()
        self.gate_event_projection()
        facts = self.page.evaluate('''async () => {
            restoreEventProjection();await fgSetTextOrder('task');
            await Promise.all(eventProjectionGates.map(gate=>gate.release()));
            await eventProjectionWork;await eventFilterGates[0].release();
            const applied=await pendingEventFilter;restoreEventFilter();
            return {applied,retained:fgFilterState===previousEventFilter,order:fgTextOrder,
                sort:fgTextSort,finished:fgFilterProgress===null};
        }''')
        self.assertEqual(facts, dict(applied=None, retained=True, order='task', sort='', finished=True))

    def test_full_text_handoff_cannot_be_overwritten_by_an_event_filter_retry(self):
        self.open_pending_event_filter()
        self.gate_event_projection()
        facts = self.page.evaluate('''async () => {
            restoreEventProjection();restoreEventFilter();
            const renderer=fgTextWindow,full=renderer.fullText();
            const applied=await pendingEventFilter;
            await Promise.all(eventProjectionGates.map(gate=>gate.release()));
            await eventProjectionWork;await eventFilterGates[0].release();await full;
            return {applied,full:fgTextWindow===null,mounted:document.querySelectorAll('#content .line-container').length,
                expected:renderer.model.records.length,hidden:document.querySelectorAll('#content [data-trace-event="1"]')
                    .length>0 && Array.from(document.querySelectorAll('#content [data-trace-event="1"]'))
                        .every(row=>!row.getClientRects().length)};
        }''')
        self.assertIsNone(facts['applied'])
        self.assertTrue(facts['full'])
        self.assertEqual(facts['mounted'], facts['expected'])
        self.assertTrue(facts['hidden'])

    def test_same_generation_filter_handoff_aborts_the_previous_controller(self):
        self.open_gated_duration_report()
        self.page.evaluate('''() => {
            window.previousWindowFilter=fgFilterState;
            window.previousInternalFilter=fgApplyWindowFilter('func:leaf sort:desc',fgFilterGeneration);
        }''')
        self.page.wait_for_function('() => durationGates.length === 1')
        self.page.evaluate('''() => {
            window.nextInternalFilter=fgApplyWindowFilter('params:^arg=24000$ sort:asc',fgFilterGeneration);
        }''')
        self.page.wait_for_function('() => durationGates.length === 2')
        facts = self.page.evaluate('''async () => {
            const aborted=durationGates[0].signal.aborted,retained=fgFilterState===previousWindowFilter;
            await durationGates[0].release();const old=await previousInternalFilter;
            const pending=fgFilterProgress?.text==='params:^arg=24000$ sort:asc' && !fgFilterProgress.error;
            await durationGates[1].release();const applied=await nextInternalFilter;
            return {aborted,retained,old,pending,applied,query:fgFilterState.text,
                matches:fgFilterState.lines.size,sort:fgTextSort,finished:fgFilterProgress===null};
        }''')
        self.assertEqual(facts, dict(aborted=True, retained=True, old=None, pending=True, applied=True,
                                    query='params:^arg=24000$ sort:asc', matches=1, sort='asc', finished=True))

    def test_remote_filter_failure_retains_exact_state_and_retry_commits_once(self):
        self.open_gated_duration_report()
        self.page.evaluate('''() => {
            window.previousWindowFilter=fgFilterState;
            window.previousWindowNumbers=fgTextWindow.visibleNumbers.slice();
            window.previousWindowRows=Array.from(document.querySelectorAll('#content .line-container'));
            const original=fgData.loadTextBlocks;
            window.restoreWindowBlocks=()=>{fgData.loadTextBlocks=original};
            window.windowPaintGates=[];
            fgData.loadTextBlocks=(ordinals,options)=>new Promise((resolve,reject)=>{
                windowPaintGates.push({signal:options.signal,fail:reject});
            });
            const notify=window.fgOnFilter;
            window.appliedWindowFilters=[];
            window.fgOnFilter=()=>{appliedWindowFilters.push(fgFilterState.text);notify?.()};
            document.getElementById('filterInput').value='params:^arg=24000$';
            window.pendingRemoteFilter=applyFilter();
        }''')
        self.page.wait_for_function('() => windowPaintGates.length === 1')
        facts = self.page.evaluate('''async () => {
            windowPaintGates[0].fail(new Error('remote filtered rows unavailable'));
            const failed=await pendingRemoteFilter;
            const retained=fgFilterState===previousWindowFilter &&
                JSON.stringify(fgTextWindow.visibleNumbers)===JSON.stringify(previousWindowNumbers) &&
                previousWindowRows.every(row=>row.isConnected);
            const notifications=appliedWindowFilters.slice(),error=fgFilterProgress?.error;
            restoreWindowBlocks();
            const retried=await applyFilter();
            const rows=Array.from(document.querySelectorAll('#content .line-container'));
            return {failed,retained,notifications,error,retried,pending:fgFilterProgress,
                applied:fgFilterState.text,commits:appliedWindowFilters,
                matched:fgFilterState.lines.size===1 && fgTextNavigation.visibleCount===1 &&
                    rows.length===1 && rows[0].dataset.params==='arg=24000'};
        }''')
        self.assertEqual(facts, dict(failed=False, retained=True, notifications=[], error=True,
                                    retried=True, pending=None, applied='params:^arg=24000$',
                                    commits=['params:^arg=24000$'], matched=True))

    def test_cancelled_filter_after_zero_matches_keeps_the_complete_empty_result(self):
        self.open_gated_duration_report()
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value='func:^absent$'; await applyFilter();
            window.previousWindowFilter=fgFilterState;
            document.getElementById('filterInput').value='func:leaf sort:desc';
            window.pendingSortedFilter=applyFilter();
        }''')
        self.page.wait_for_function('() => durationGates.length === 1')
        facts = self.page.evaluate('''async () => {
            fgCancelPendingFilter(); await durationGates[0].release();
            const result=await pendingSortedFilter;
            const empty=fgFilterState===previousWindowFilter && fgTextWindow.visibleNumbers.length===0 &&
                fgTextNavigation.visibleCount===0 && document.querySelectorAll('#content .line-container').length===0;
            await clearFilter();
            return {result,empty,pending:fgFilterProgress,
                restored:fgTextNavigation.visibleCount===fgTextWindow.model.records.length};
        }''')
        self.assertEqual(facts, dict(result=None, empty=True, pending=None, restored=True))

    def test_suspending_pending_filter_keeps_applied_state_after_reentry(self):
        self.open_gated_duration_report()
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value='func:leaf'; await applyFilter();
            window.previousWindowFilter=fgFilterState;
            document.getElementById('filterInput').value='func:^leaf$ sort:desc';
            window.pendingSortedFilter=applyFilter();
        }''')
        self.page.wait_for_function('() => durationGates.length === 1')
        facts = self.page.evaluate('''async () => {
            await fgSwitchView('timeline');
            const aborted=durationGates[0].signal.aborted;
            await durationGates[0].release();
            const result=await pendingSortedFilter;
            await fgSwitchView('text');
            return {aborted,result,retained:fgFilterState===previousWindowFilter,
                sort:fgTextSort,pending:fgFilterProgress,
                navigated:(await fgTextWindow.ensureLine(20000)).id==='L20000'};
        }''')
        self.assertEqual(facts, dict(aborted=True, result=None, retained=True, sort='',
                                    pending=None, navigated=True))

    def test_unchanged_window_geometry_reuses_positions_without_a_full_map_copy(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            const NativeMap=Map, maps=[], numbers=fgTextWindow.visibleNumbers.slice();
            window.Map=new Proxy(NativeMap,{construct(target,parameters){
                const instance=Reflect.construct(target,parameters);maps.push(instance);return instance;
            }});
            try {
                await fgTextWindow.rebuild();await fgTextWindow.rebuild();
                return {complete:JSON.stringify(fgTextWindow.visibleNumbers)===JSON.stringify(numbers),
                    copied:maps.some(instance=>instance.size===fgTextWindow.model.records.length),
                    count:fgTextNavigation.visibleCount===numbers.length,
                    navigated:(await fgTextWindow.ensureLine(20000)).id==='L20000'};
            } finally {window.Map=NativeMap}
        }''')
        self.assertEqual(facts, dict(complete=True, copied=False, count=True, navigated=True))

    def test_equal_sized_filters_with_different_lines_invalidate_staged_positions(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            const results=[];
            for (const argument of [24000,23000,24000]) {
                document.getElementById('filterInput').value='params:^arg='+argument+'$';
                await applyFilter();
                const [number]=fgFilterState.lines;
                const row=await fgTextWindow.ensureLine(number);
                results.push(fgFilterState.lines.size===1 && fgTextNavigation.visibleCount===1 &&
                    fgTextWindow.visibleNumbers.length===1 && fgTextWindow.visibleNumbers[0]===number &&
                    row.isConnected && row.dataset.params==='arg='+argument);
            }
            await clearFilter();
            return {results,restored:fgTextNavigation.visibleCount===fgTextWindow.model.records.length};
        }''')
        self.assertEqual(facts, dict(results=[True, True, True], restored=True))

    def test_cooperative_duration_filter_failure_rolls_back_previous_complete_result(self):
        self.open_gated_duration_report()
        facts = self.page.evaluate('''async () => {
            const order=fgTextNavigation.order.slice(),visible=fgTextNavigation.visibleCount,text=fgFilterState.text;
            fgTextModel.rankDurationAsync=async()=>{throw new Error('duration preparation failed')};
            document.getElementById('filterInput').value='func:leaf sort:desc';await applyFilter();
            return {stable:JSON.stringify(order)===JSON.stringify(fgTextNavigation.order),
                visible:fgTextNavigation.visibleCount===visible,text:fgFilterState.text===text,sort:fgTextSort,
                notice:document.querySelector('.fg-window-notice [role=status]').textContent};
        }''')
        self.assertTrue(facts['stable'])
        self.assertTrue(facts['visible'])
        self.assertTrue(facts['text'])
        self.assertEqual(facts['sort'], '')
        self.assertIn('duration preparation failed', facts['notice'])

    def test_scattered_sort_loads_each_network_resource_once_without_changing_display_order(self):
        self.page.goto(urljoin(self.server.url, 'scattered/index.html?text=windowed'))
        self.assertTrue(self.page.evaluate('async () => !!(await fgSwitchView("text"))'))
        facts = self.page.evaluate('''async () => {
            const original=fgData.loadTextBlocks, catalog=fgData.textChunks(), results=[];
            for (const direction of ['desc','asc','desc']) {
                const groups=[];
                let active=0, maximum=0;
                fgData.loadTextBlocks=async (ordinals, options)=>{
                    groups.push(ordinals.slice()); active++; maximum=Math.max(maximum,active);
                    try {return await original(ordinals,options)} finally {active--}
                };
                try {
                    fgData.releaseTextChunks(); performance.clearResourceTimings();
                    const expected=fgTextWindow.model.records.slice().sort((left,right)=>{
                        const delta=parseFloat(left.getAttribute('data-sort-duration')||left.getAttribute('data-duration')||'0')-
                            parseFloat(right.getAttribute('data-sort-duration')||right.getAttribute('data-duration')||'0');
                        return direction==='asc' ? delta : -delta;
                    }).map(record=>record.row[0]);
                    fgTextSort=direction; await fgTextWindow.rebuild(expected[0]);
                    const rows=[...document.querySelectorAll('#content .line-container')];
                    const requests=performance.getEntriesByType('resource').filter(entry=>/\\/text-\\d+\\.html$/.test(entry.name));
                    results.push({maximum,groups:groups.length,
                        grouped:groups.every(group=>new Set(group.map(ordinal=>Math.floor(ordinal/catalog.chunk_size))).size===1 &&
                            new Set(group.map(ordinal=>Math.floor(ordinal/catalog.paint_rows))).size===group.length),
                        requests:requests.length,unique:new Set(requests.map(entry=>entry.name)).size,
                        complete:JSON.stringify(fgTextWindow.visibleNumbers)===JSON.stringify(expected),
                        display:JSON.stringify(rows.map(row=>+row.dataset.lineNumber))===JSON.stringify(expected.slice(0,rows.length)),
                        sources:rows.every(row=>row.classList.contains('expandable')===!!row.nextElementSibling?.classList.contains('expanded-content')),
                        mounted:rows.length});
                } finally {fgData.loadTextBlocks=original}
            }
            return results;
        }''')
        for result in facts:
            with self.subTest(result=result):
                self.assertEqual(result['requests'], result['unique'])
                self.assertTrue(result['complete'])
                self.assertTrue(result['display'])
                self.assertGreater(result['groups'], 4)
                self.assertLessEqual(result['maximum'], 4)
                self.assertTrue(result['grouped'])
                self.assertEqual(result['requests'], result['groups'])
                self.assertTrue(result['sources'])
                self.assertLessEqual(result['mounted'], 160)

    def test_grouped_window_reads_validate_unmounted_rows_and_block_ranges_before_publication(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const original=fgData.loadTextBlocks, results=[];
            for (const kind of ['range','identity','presentation']) {
                await fgEnsureTextLine(100);
                const before=[...document.querySelectorAll('#content .line-container')].map(row=>row.id);
                fgData.loadTextBlocks=async (...args)=>{
                    const blocks=await original(...args);
                    return blocks.map(block=>{
                        if (kind==='range') return {...block,first:block.first+512};
                        const number=fgTextWindow.model.records[block.first].row[0];
                        return {...block,html:kind==='identity' ? block.html.replace('id="L'+number+'"','id="L999999"') :
                            block.html.replace('class="line-content"','class="unexpected-content"')};
                    });
                };
                let error;
                try {await fgEnsureTextLine(8500)} catch(failure) {error=failure.message}
                finally {fgData.loadTextBlocks=original}
                const after=[...document.querySelectorAll('#content .line-container')].map(row=>row.id);
                const row=await fgEnsureTextLine(8500);
                results.push({kind,error,retained:JSON.stringify(before)===JSON.stringify(after),retry:row.id});
            }
            return results;
        }''')
        for result, message in zip(facts, ['range', 'line identity', 'presentation metadata']):
            with self.subTest(kind=result['kind']):
                self.assertEqual(result['error'], 'Text paint block ' + message + ' mismatch')
                self.assertTrue(result['retained'])
                self.assertEqual(result['retry'], 'L8500')

    def test_scroll_feedback_does_not_preempt_an_explicit_window_rebuild(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const original=fgLoadTextBlock;
            let release, entered, signal;
            const waiting=new Promise(resolve=>{release=resolve});
            const started=new Promise(resolve=>{entered=resolve});
            fgLoadTextBlock=async (...args)=>{
                signal=args[2].signal; entered();
                const result=await original(...args);
                await waiting;
                return result;
            };
            try {
                scrollTo({top:document.getElementById('content').getBoundingClientRect().top+scrollY+100,behavior:'instant'});
                const pending=fgTextWindow.rebuild(7000).then(()=>true,error=>error.name);
                await started;
                dispatchEvent(new Event('scroll'));
                await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
                const preempted=signal.aborted;
                release();
                const complete=await pending;
                return {preempted,complete,target:!!document.getElementById('L7000'),
                    busy:document.getElementById('content').hasAttribute('aria-busy'),
                    rows:document.querySelectorAll('#content .line-container').length};
            } finally {release(); fgLoadTextBlock=original}
        }''')
        self.assertFalse(facts['preempted'])
        self.assertIs(facts['complete'], True)
        self.assertTrue(facts['target'])
        self.assertFalse(facts['busy'])
        self.assertLessEqual(facts['rows'], 160)

    def test_leaving_text_cancels_reads_and_reentry_resumes_scrolling(self):
        self.open_report()
        self.page.evaluate('async () => { await fgSwitchView("timeline"); await fgSwitchView("text"); scrollTo({top:150000,behavior:"instant"}); }')
        self.page.wait_for_function('''() => [...document.querySelectorAll('#content .line-container')]
            .some(row => +row.dataset.lineNumber>5000)''')
        self.assertLessEqual(self.mounted(), 160)

    def test_trace_jump_updates_return_control_after_suspending_text(self):
        self.open_report()
        self.page.evaluate('''async () => {
            const button = document.getElementById('fgJumpReturn');
            const suspend = fgTextWindow.suspend;
            window.returnStateAtSuspend = [];
            fgTextWindow.suspend = () => {
                returnStateAtSuspend.push(button.hidden ? null : button.getAttribute('aria-label'));
                suspend();
            };
            const row = await fgTextWindow.ensureLine(4100);
            fgSelectMountedTextLine(row, false, false);
        }''')
        self.page.get_by_role('button', name='In Timeline · L4100', exact=True).press('Enter')
        self.page.wait_for_function('() => document.activeElement.matches(".tl-canvas")')
        self.page.get_by_role('button', name='Return to Text', exact=True).press('Enter')
        self.page.locator('.view-tab[data-view=timeline]').press('Enter')
        self.page.locator('.tl-canvas').press('Enter')
        self.page.get_by_role('button', name='Return to Timeline', exact=True).press('Enter')
        self.page.wait_for_function('() => fgCurrentView() === "timeline"')
        self.assertEqual(self.page.evaluate('returnStateAtSuspend'), [None, None, 'Return to Timeline'])

    def test_trace_return_keeps_the_reading_line_after_rotation(self):
        for before, after in (((844, 390), (390, 844)), ((390, 844), (844, 390))):
            with self.subTest(before=before, after=after):
                self.page.set_viewport_size(dict(width=before[0], height=before[1]))
                self.open_report()
                original = self.page.evaluate('''async () => {
                    await fgTextWindow.ensureLine(4100);
                    await fgTextWindow.settled;
                    const origin = document.getElementById('L4100');
                    const inset = () => document.querySelector('.controls').getBoundingClientRect().bottom;
                    scrollBy({top:origin.parentElement.getBoundingClientRect().top - inset() - 39, behavior:'instant'});
                    for (let frame = 0; frame < 3; frame++) await new Promise(requestAnimationFrame);
                    fgSelectMountedTextLine(origin, false, false);
                    window.reading = () => {
                        const edge = inset(), rows = [...document.querySelectorAll('.fg-window-rows [data-window-line]')];
                        const row = rows.find(item => item.getBoundingClientRect().bottom > edge);
                        return {number:row?.dataset.windowLine, hidden:edge - row?.getBoundingClientRect().top,
                            focus:document.activeElement.id, rows:rows.length};
                    };
                    return reading();
                }''')
                self.page.get_by_role('button', name='In Timeline · L4100', exact=True).press('Enter')
                self.page.wait_for_function('() => fgCurrentView() === "timeline" && !!fgView("timeline")?.probe')
                self.page.set_viewport_size(dict(width=after[0], height=after[1]))
                self.page.get_by_role('button', name='Return to Text', exact=True).press('Enter')
                returned = self.page.evaluate('''async () => {
                    await fgTextWindow.settled;
                    for (let frame = 0; frame < 3; frame++) await new Promise(requestAnimationFrame);
                    return reading();
                }''')
                self.assertEqual(returned['number'], original['number'], (original, returned))
                self.assertAlmostEqual(returned['hidden'], original['hidden'], delta=1)
                self.assertEqual(returned['focus'], 'L4100')
                self.assertEqual(returned['rows'], 160)

    def test_rotated_return_does_not_reveal_a_filtered_out_origin(self):
        self.page.set_viewport_size(dict(width=844, height=390))
        self.open_report()
        self.page.evaluate('''async () => {
            const row = await fgTextWindow.ensureLine(4100);
            fgSelectMountedTextLine(row, false, false);
        }''')
        self.page.get_by_role('button', name='In Timeline · L4100', exact=True).press('Enter')
        self.page.wait_for_function('() => fgCurrentView() === "timeline" && !!fgView("timeline")?.probe')
        self.page.evaluate('''async () => {
            document.getElementById('filterInput').value = 'params:^arg=8000$';
            await applyFilter();
        }''')
        self.page.set_viewport_size(dict(width=390, height=844))
        self.page.get_by_role('button', name='Return to Text', exact=True).press('Enter')
        self.page.locator('#content .line-container').filter(has_text='arg=8000').wait_for()
        self.assertEqual(self.page.locator('#L4100').count(), 0)
        self.assertTrue(self.page.locator('.view-tab[data-view=text]').evaluate('tab => tab === document.activeElement'))
        self.assertEqual(self.page.evaluate('fgFilterState.text'), 'params:^arg=8000$')

    def test_rotated_return_does_not_focus_a_row_covered_by_the_task_heading(self):
        self.page.set_viewport_size(dict(width=844, height=390))
        group = self.open_task_report()[0]
        number = group['number']
        self.seek_task_context(number)
        self.page.evaluate('''async number => {
            const row = document.getElementById('L' + number);
            const edge = document.querySelector('.controls').getBoundingClientRect().bottom;
            scrollBy({top:row.getBoundingClientRect().top - edge + 6, behavior:'instant'});
            for (let frame = 0; frame < 3; frame++) await new Promise(requestAnimationFrame);
            fgSelectMountedTextLine(row, false, false);
        }''', number)
        self.page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).press('Enter')
        self.page.wait_for_function('() => fgCurrentView() === "timeline" && !!fgView("timeline")?.probe')
        self.page.set_viewport_size(dict(width=390, height=844))
        self.page.get_by_role('button', name='Return to Text', exact=True).press('Enter')
        self.assertTrue(self.page.evaluate('''async number => {
            await fgTextWindow.settled;
            const heading = document.querySelector('.fg-window-context');
            const row = document.getElementById('L' + number);
            return !heading.hidden && row.getBoundingClientRect().bottom <= heading.firstElementChild.getBoundingClientRect().bottom
                && document.activeElement.matches('.view-tab[data-view=text]');
        }''', number))

    def test_text_return_uses_observed_row_heights_and_keeps_the_reading_anchor(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            await fgTextWindow.ensureLine(4100);
            const frames = async () => {
                for (let index = 0; index < 3; index++) await new Promise(requestAnimationFrame);
            };
            await frames();
            const body = document.querySelector('.fg-window-rows');
            const first = body.firstElementChild, target = document.getElementById('L4100');
            const originalTop = target.getBoundingClientRect().top, originalScroll = scrollY;
            if (first.getBoundingClientRect().bottom >= 0) throw Error('Expected a retained row above the viewport');
            const bounds = Element.prototype.getBoundingClientRect;
            let reads = 0;
            Element.prototype.getBoundingClientRect = function () {
                if (this.parentElement === body) reads++;
                return bounds.call(this);
            };
            try {
                const roundTrip = async change => {
                    await fgSwitchView('timeline');
                    await frames();
                    if (change) first.style.paddingBlockEnd = '36px';
                    reads = 0;
                    await fgSwitchView('text');
                    await frames();
                    return {reads, top:target.getBoundingClientRect().top, scroll:scrollY,
                        retained:target === document.getElementById('L4100') && first === body.firstElementChild};
                };
                return {before:{top:originalTop, scroll:originalScroll}, unchanged:await roundTrip(false),
                    grown:await roundTrip(true)};
            } finally { Element.prototype.getBoundingClientRect = bounds; }
        }''')
        self.assertEqual(facts['unchanged'], dict(**facts['before'], reads=0, retained=True))
        self.assertEqual(facts['grown'], dict(top=facts['before']['top'], scroll=facts['before']['scroll'] + 36,
                                             reads=0, retained=True))

    def test_full_document_shortcut_mounts_all_rows_before_selecting(self):
        self.open_report()
        self.page.keyboard.press('Control+a')
        self.page.wait_for_function('() => fgTextWindow === null && !getSelection().isCollapsed')
        self.assertGreater(self.mounted(), 9000)
        self.assertIn('arg=8999', self.page.evaluate('getSelection().toString()'))

    def test_full_text_uses_deferred_layout_without_losing_far_find_or_print(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const renderer = fgTextWindow, root = renderer.root;
            const before = root.classList.contains('text-layout-auto');
            await renderer.fullText();
            const chunks = Array.from(root.querySelectorAll(':scope > .text-chunk'));
            const far = document.getElementById('L9000');
            const after = root.classList.contains('text-layout-auto');
            const sized = chunks.every(chunk => {
                const rows = chunk.querySelectorAll(':scope > .line-container').length;
                const counted = ['plain', 'empty', 'expandable'].reduce((sum, kind) =>
                    sum + Number(chunk.style.getPropertyValue('--fg-text-' + kind + '-rows')), 0);
                return rows > 0 && counted === rows;
            });
            const auto = chunks.every(chunk => getComputedStyle(chunk).contentVisibility === 'auto' || chunk.matches(':focus-within'));
            const found = window.find('arg=8999');
            return {before, after, sized, auto, found, mounted:far?.isConnected, selected:getSelection().toString()};
        }''')
        self.assertEqual(facts, dict(before=False, after=True, sized=True, auto=True, found=True, mounted=True, selected='arg=8999'))
        self.page.emulate_media(media='print')
        self.assertTrue(self.page.evaluate('''() => Array.from(document.querySelectorAll('#content > .text-chunk'))
            .every(chunk => getComputedStyle(chunk).contentVisibility === 'visible')'''))

    def test_full_text_keeps_complete_dom_without_deferred_layout_support(self):
        self.page.add_init_script('''(() => {
            const supports = CSS.supports.bind(CSS);
            CSS.supports = (...args) => args[0] !== 'content-visibility: auto' && supports(...args);
        })()''')
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const renderer = fgTextWindow;
            await renderer.fullText();
            return {eager:!renderer.root.classList.contains('text-layout-auto'),
                all:renderer.root.querySelectorAll('.line-container').length === renderer.model.records.length};
        }''')
        self.assertEqual(facts, dict(eager=True, all=True))

    def test_copy_covers_all_visible_records_without_mounting_them(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            let copied;
            Object.defineProperty(navigator, 'clipboard', {value:{writeText:async text => {copied=text;}}});
            copyVisibleContent({target:document.createElement('button')});
            return {copied, total:fgTextWindow.visibleNumbers.length, mounted:document.querySelectorAll('#content .line-container').length};
        }''')
        self.assertEqual(len(facts['copied'].split('\n')), facts['total'])
        self.assertIn('arg=8999', facts['copied'])
        self.assertLessEqual(facts['mounted'], 160)

    def test_mixed_rows_keep_spaces_boundaries_flags_and_sources(self):
        self.page.goto(urljoin(self.server.url, 'mixed/index.html?text=windowed'))
        self.assertTrue(self.page.evaluate('async () => !!(await fgSwitchView("text"))'))
        facts = self.page.evaluate('''async () => {
            const model = fgTextWindow.model;
            const spaces = [...new Set(model.records.map(record => record.getAttribute('data-space')).filter(Boolean))].sort();
            const result = [];
            for (const space of ['kernel','user']) {
                const record = model.records.find(record => record.getAttribute('data-space')===space && record.source!==null);
                const row = await fgEnsureTextLine(record.row[0]);
                toggleExpand(row.dataset.lineId); await fgFill(row.nextElementSibling);
                result.push({space:row.dataset.space, source:row.nextElementSibling.dataset.src, expected:record.source,
                    text:row.nextElementSibling.textContent, context:row.getAttribute('data-irqs'), expectedContext:record.getAttribute('data-irqs')});
            }
            const boundary = model.records.find(record => record.textContent.includes('User') && record.textContent.includes('Kernel'));
            return {spaces, result, records:model.records.length, boundary:boundary?.row[0] || null};
        }''')
        self.assertEqual(facts['spaces'], ['kernel', 'user'])
        self.assertGreater(facts['records'], 4096)
        for result in facts['result']:
            self.assertEqual(int(result['source']), result['expected'])
            self.assertTrue(result['text'])
            self.assertEqual(result['context'], result['expectedContext'])

    def test_touch_dark_theme_resize_far_hash_and_fold_controls(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width':390,'height':844}, is_mobile=True,
                                               has_touch=True, device_scale_factor=2)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.add_init_script('localStorage.setItem("theme","dark")')
        self.open_report()
        self.assertGreaterEqual(self.page.locator('#content .fold-icon').first.bounding_box()['width'], 44)
        self.page.evaluate('location.hash = "#L8500"')
        self.page.wait_for_function('''() => {
            const row=document.getElementById('L8500');
            if (!row) return false;
            const rect=row.getBoundingClientRect();
            return rect.top>=document.querySelector('.controls').getBoundingClientRect().bottom && rect.bottom<=innerHeight;
        }''', timeout=5000)
        self.page.set_viewport_size({'width':900,'height':844})
        self.page.wait_for_timeout(160)
        self.assertLessEqual(self.mounted(), 160)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))


if __name__ == '__main__':
    unittest.main()
