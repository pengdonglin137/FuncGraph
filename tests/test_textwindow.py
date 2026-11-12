"""Opt-in Text windows retain complete trace semantics with bounded row DOM."""
from pathlib import Path
import json
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
        cls.kernel.run(str(trace), '--no-config', '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--report-mode', 'sidecar', out='report')
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
            const load=fgData.loadTextBlock;
            fgData.loadTextBlock=async ordinal=>{throw new Error('injected task window failure')};
            let failed=false;
            try { await fgEnsureTextLine(number); } catch(error) {failed=error.message.includes('injected task window failure')}
            finally {fgData.loadTextBlock=load}
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
        self.assertEqual(facts['version'], 4)
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

    def test_default_text_remains_complete_and_has_explicit_experimental_entry(self):
        self.open_report(windowed=False)
        self.assertGreater(self.mounted(), 9000)
        self.assertTrue(self.page.locator('#textWindowBtn').is_visible())
        self.assertTrue(self.page.evaluate('fgTextWindow === null'))

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

    def test_cancelled_far_navigation_cannot_replace_newer_window(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const original = fgData.loadTextBlock;
            let release;
            fgData.loadTextBlock = async (ordinal, options) => {
                if (ordinal > 8000) await new Promise(resolve => release = resolve);
                return original(ordinal, options);
            };
            const controller = new AbortController();
            const obsolete = fgEnsureTextLine(8500, {signal:controller.signal}).catch(error => error.name);
            while (!release) await new Promise(resolve => setTimeout(resolve, 0));
            controller.abort();
            const row = await fgEnsureTextLine(5001);
            release();
            const canceled = await obsolete;
            fgData.loadTextBlock = original;
            return {canceled, id:row.id, connected:row.isConnected, stale:!!document.getElementById('L8500')};
        }''')
        self.assertEqual(facts, dict(canceled='AbortError', id='L5001', connected=True, stale=False))

    def test_failed_block_keeps_previous_window_and_navigation_can_retry(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const original = fgData.loadTextBlock;
            const before = [...document.querySelectorAll('#content .line-container')].map(row => row.id);
            fgData.loadTextBlock = () => Promise.reject(new Error('injected network failure'));
            const error = await fgEnsureTextLine(8500).catch(error => error.message);
            const after = [...document.querySelectorAll('#content .line-container')].map(row => row.id);
            const retry = !document.querySelector('.fg-window-notice button').hidden;
            fgData.loadTextBlock = original;
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
            const original = fgData.loadTextBlock;
            const before = fgTextWindow.visibleNumbers;
            fgData.loadTextBlock = () => Promise.reject(new Error('filter block unavailable'));
            document.getElementById('filterInput').value = 'params:arg=8999';
            const applied = await applyFilter();
            const after = fgTextWindow.visibleNumbers, state = fgFilterState, error = fgFilterProgress.message;
            const notice = document.querySelector('.fg-window-notice').textContent;
            fgData.loadTextBlock = original;
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

    def test_failed_full_text_keeps_windowed_rows_and_can_retry(self):
        self.open_report()
        facts = self.page.evaluate('''async () => {
            const renderer = fgTextWindow, original = fgData.loadTextBlock;
            fgData.loadTextBlock = (ordinal, options) => ordinal>4000 ? Promise.reject(new Error('full block unavailable')) : original(ordinal, options);
            const error = await renderer.fullText().catch(error=>error.message);
            const retained = renderer === fgTextWindow, mounted = document.querySelectorAll('#content .line-container').length;
            fgData.loadTextBlock = original;
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
            const data=JSON.parse(JSON.stringify(fgData.read('fgTextIndex')));
            const column=data.fields.indexOf('sort-duration')+2;
            data.rows[5000][column]='999999';
            const steps=fgTextModel.prepare(data); let result;
            do {result=steps.next();} while(!result.done);
            return fgLoadTextBlock(result.value,data.rows[5000][0]).then(()=>null,error=>error.message);
        }''')
        self.assertEqual(facts, 'Text paint block presentation metadata mismatch')

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

    def test_leaving_text_cancels_reads_and_reentry_resumes_scrolling(self):
        self.open_report()
        self.page.evaluate('async () => { await fgSwitchView("timeline"); await fgSwitchView("text"); scrollTo({top:150000,behavior:"instant"}); }')
        self.page.wait_for_function('''() => [...document.querySelectorAll('#content .line-container')]
            .some(row => +row.dataset.lineNumber>5000)''')
        self.assertLessEqual(self.mounted(), 160)

    def test_full_document_shortcut_mounts_all_rows_before_selecting(self):
        self.open_report()
        self.page.keyboard.press('Control+a')
        self.page.wait_for_function('() => fgTextWindow === null && !getSelection().isCollapsed')
        self.assertGreater(self.mounted(), 9000)
        self.assertIn('arg=8999', self.page.evaluate('getSelection().toString()'))

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
