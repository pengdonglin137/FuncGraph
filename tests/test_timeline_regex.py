"""Background regex matching retains exact results and cancellable viewer input."""

from pathlib import Path
import shutil
import subprocess
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import call, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class RegexWorkerModelTest(unittest.TestCase):
    def check(self, assertions):
        source = (Path(helpers.ROOT, 'assets/report/function-search.js').read_text() + '\n' +
                  Path(helpers.ROOT, 'assets/timeline/search.js').read_text())
        result = subprocess.run(['node', '-e', source + "\nconst assert = require('node:assert/strict');\n" + assertions],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_literal_atoms_anchors_and_universal_match_stay_in_page(self):
        self.check(r'''
            for (const query of ['', '.*', '^', '$', '^$', 'mutex_unlock', '^CHILD$',
                '中文🙂', 'first\nsecond', '^a\\+b\\.c\\[d\\]$', '\\\\', 'foo\\$', 'a\\-b'])
                assert.equal(searchNeedsWorker(query), false, query);
            for (const query of ['[', '\\', '(a+)+$', 'a.*b', 'a|b', '(?:literal)', '\\d',
                '^a$'.repeat(2), '\\u0061', '[a-z]', 'a{2}', 'a'.repeat(513)]) {
                assert.equal(searchNeedsWorker(query), true, query);
            }
        ''')

    def test_worker_codec_matches_native_regex_for_every_name_and_batch(self):
        self.check(r'''
            const messages = [];
            globalThis.self = {postMessage:data => messages.push(data)};
            searchRegexWorkerMain();
            assert.deepEqual(messages.shift(), {type:'started'});
            const names = ['Root<int>', 'child+symbol', 'CHILD<int>', '中文🙂', 'K', 'ſ',
                'İ', 'quote"\\path', 'first\nsecond', '', 'z'.repeat(70000)];
            for (const query of ['child', '^(Root|CHILD)', '[a-z]+', '^.*$', '^$', '(?<=child)\\+',
                '(child)\\1|中文', '\\p{Letter}', '\\u212a', 'a{1,3}', '\\s', 'z+$']) {
                self.onmessage({data:{type:'init',query}});
                assert.equal(messages.shift().type, 'ready');
                const matcher = new RegExp(query, 'i');
                for (let position = 0; position < names.length; position += 3) {
                    const batch = names.slice(position,position + 3);
                    self.onmessage({data:{type:'batch',id:position,names:batch}});
                    const result = messages.shift();
                    assert.equal(result.id,position);
                    assert.deepEqual([...new Uint8Array(result.buffer)],batch.map(name => Number(matcher.test(name))));
                }
            }
            self.onmessage({data:{type:'init',query:'['}});
            assert.equal(messages.shift().kind,'regex');
            assert.equal(messages.length,0);
        ''')

    def test_missing_worker_support_returns_an_explicit_fallback_signal(self):
        self.check('''
            delete globalThis.Worker;
            assert.equal(matchSearchNamesWorker({names:new Int32Array()}, [], 'a|b', new AbortController().signal), null);
        ''')


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class RegexWorkerBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        cls.source = (Path(helpers.ROOT, 'assets/report/function-search.js').read_text() + '\n' +
                      Path(helpers.ROOT, 'assets/timeline/search.js').read_text())
        cls.names = ['a' * 28 + 'b', 'root', 'CHILD<int>', 'child+symbol', '中文🙂', 'first\nsecond',
                     'quote"\\path', 'z' * 70000] + [f'function_{index}' for index in range(650)]
        records = []
        for repeat in range(2):
            for index, name in enumerate(cls.names):
                timestamp = (repeat * len(cls.names) + index) * 100
                records.extend([call('entry', 'user', timestamp, 0, name, index + 1),
                                call('exit', 'user', timestamp + 50, 0, name, index + 1)])
        trace = Path(cls.kernel.dir, 'regex.jsonl')
        trace.write_text(stream(records))
        common = ['--no-config', '--timeline', '--filter', '--enable-fold']
        cls.kernel.run(str(trace), *common, out='single.html')
        cls.kernel.run(str(trace), *common, '--report-mode', 'sidecar', out='sidecar')
        cls.server = create_server(Path(cls.kernel.dir, 'sidecar'))
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join, 5)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.add_init_script('''
            window.regexWorkers = [];
            const OriginalWorker = window.Worker;
            window.Worker = function(url, options) {
                const worker = new OriginalWorker(url,options);
                if (options?.name === 'FuncGraph function search') {
                    const entry = {terminated:0,batches:[],messages:[]};
                    regexWorkers.push(entry);
                    const terminate = worker.terminate.bind(worker), post = worker.postMessage.bind(worker);
                    worker.terminate = () => { entry.terminated++; return terminate(); };
                    worker.postMessage = (data,...rest) => {
                        if (data.type === 'batch') entry.batches.push(data.names.map(name => name.length));
                        return post(data,...rest);
                    };
                    worker.addEventListener('message', event => entry.messages.push(event.data.type));
                }
                return worker;
            };
            window.Worker.prototype = OriginalWorker.prototype;
        ''')
        self.open_report()

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, mode='single'):
        url = Path(self.kernel.dir, 'single.html').as_uri() if mode == 'single' else self.server.url
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.locator('.tl-canvas').wait_for()
        self.page.evaluate('''source => {
            window.regexHelpers = new Function(source + ';return {matchSearchNamesWorker,buildSearchIndex};')();
        }''', self.source)

    def start(self, query):
        return self.page.evaluate('''query => {
            const input = document.querySelector('.tl-search');
            input.value = query;
            input.dispatchEvent(new Event('input', {bubbles:true}));
            input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',bubbles:true,cancelable:true}));
            return document.querySelector('.tl-count').getAttribute('aria-busy');
        }''', query)

    def ready(self):
        self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")

    def test_complex_results_and_every_worker_bit_match_the_native_reference(self):
        facts = self.page.evaluate(r'''async () => {
            const probe = fgView('timeline').probe(), names = probe.S;
            const steps = regexHelpers.buildSearchIndex(probe.sName,probe.N);
            let result;
            do { result = steps.next(); } while (!result.done);
            const index = result.value, facts = [];
            for (const query of ['^(root|CHILD)', 'child\\+', '^function_(1|30|649)$', '^.*$',
                '(?<=child)\\+', '(function)_(?:1|20)$', '\\s', 'z+$']) {
                const hits = await regexHelpers.matchSearchNamesWorker(index,names,query,new AbortController().signal);
                const matcher = new RegExp(query,'i'), expected = new Uint8Array(names.length);
                for (const name of index.names) expected[name] = Number(matcher.test(names[name]));
                facts.push(Array.from(hits).every((value,position) => value === expected[position]));
            }
            return {facts,workers:regexWorkers};
        }''')
        self.assertEqual(facts['facts'], [True] * 8)
        for worker in facts['workers']:
            self.assertEqual(worker['terminated'], 1)
            for batch in worker['batches']:
                self.assertLessEqual(len(batch), 256)
                self.assertTrue(sum(batch) <= 65536 or len(batch) == 1)
        self.assertTrue(any(70000 in batch for worker in facts['workers'] for batch in worker['batches']))

    def test_expensive_regex_keeps_input_responsive_and_escape_terminates_worker(self):
        self.assertEqual(self.start('^(a+)+$'), 'true')
        self.page.wait_for_function("() => regexWorkers[0].messages.includes('ready')")
        state = self.page.evaluate('''async () => {
            await new Promise(resolve => setTimeout(resolve,50));
            return {busy:document.querySelector('.tl-count').getAttribute('aria-busy'),
                selected:fgView('timeline').probe().sel(),workers:regexWorkers.length};
        }''')
        self.assertEqual(state, dict(busy='true', selected=-1, workers=1))
        self.page.locator('.tl-search').press('Escape')
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '')
        self.assertEqual(self.page.evaluate('regexWorkers[0].terminated'), 1)
        self.start('^root$')
        self.ready()
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().nameOf(fgView('timeline').probe().sel())"), 'root')

    def test_replacing_a_running_regex_does_not_publish_partial_or_stale_hits(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open_report(mode)
                before = self.page.evaluate("JSON.stringify(fgData.read('tlData'))")
                self.start('^(a+)+$')
                self.start('^function_(1|649)$')
                self.ready()
                self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 4 calls')
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().nameOf(fgView('timeline').probe().sel())"), 'function_1')
                self.assertEqual(self.page.evaluate("JSON.stringify(fgData.read('tlData'))"), before)
                self.assertTrue(self.page.evaluate('regexWorkers.every(worker => worker.terminated === 1)'))
                if mode == 'sidecar':
                    self.assertEqual(self.page.locator('#content .line-container').count(), 0)

    def test_worker_query_cache_and_shared_filter_keep_exact_navigation_scope(self):
        self.start('^function_(1|649)$')
        self.ready()
        workers = self.page.evaluate('regexWorkers.length')
        self.page.locator('.tl-search').press('Enter')
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '2 / 4 calls')
        self.page.evaluate("fgNotifyFilter('none',new Set())")
        self.ready()
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '0 calls')
        self.assertIn('shared filter excludes', self.page.locator('.tl-count').get_attribute('title'))
        self.page.evaluate("fgNotifyFilter('',null)")
        self.ready()
        self.assertEqual(self.page.evaluate('regexWorkers.length'), workers)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '4 calls')

    def test_composition_and_view_suspension_cancel_workers_and_queued_navigation(self):
        self.start('^(a+)+$')
        self.page.evaluate('''() => {
            const input = document.querySelector('.tl-search');
            input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
            input.value = '^function_(1|20)$';
            input.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));
        }''')
        self.assertEqual(self.page.evaluate('regexWorkers[0].terminated'), 1)
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.page.evaluate("document.querySelector('.tl-search').dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}))")
        self.page.wait_for_timeout(180)
        self.assertEqual(self.page.evaluate('regexWorkers.length'), 1)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.ready()
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '4 calls')

    def test_invalid_worker_regex_keeps_syntax_errors_distinct_from_unavailability(self):
        self.start('[')
        self.ready()
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Invalid regex')
        self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'true')
        self.assertTrue(self.page.evaluate('regexWorkers.every(worker => worker.terminated === 1)'))
        self.start('^function_(1|20)$')
        self.ready()
        self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'false')
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 4 calls')

    def test_missing_or_blocked_workers_preserve_explicit_main_thread_fallback(self):
        for constructor in ('undefined', "function() { throw new Error('blocked worker'); }"):
            with self.subTest(constructor=constructor):
                self.open_report()
                self.page.evaluate('constructor => { window.Worker = new Function("return " + constructor)(); }', constructor)
                self.start('^function_(1|20)$')
                self.ready()
                self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 4 calls')
                self.assertIn('page thread', self.page.locator('.tl-search').get_attribute('title'))
                self.start('[')
                self.ready()
                self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Invalid regex')

    def test_worker_runtime_failure_never_falls_back_to_the_page_or_partial_results(self):
        self.page.evaluate('''() => {
            const OriginalWorker = window.Worker;
            window.Worker = function(...args) {
                const worker = new OriginalWorker(...args), post = worker.postMessage.bind(worker);
                worker.postMessage = (data,...rest) => {
                    if (data.type === 'batch') throw new Error('injected batch failure');
                    return post(data,...rest);
                };
                return worker;
            };
        }''')
        self.start('^function_(1|20)$')
        self.ready()
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Search unavailable')
        self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'false')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.assertEqual(self.page.evaluate('regexWorkers[0].terminated'), 1)
        self.start('^root$')
        self.ready()
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 2 calls')

    def test_literal_navigation_keeps_its_synchronous_path_without_workers(self):
        for query in ('^root$', '^CHILD<int>$', r'^child\+symbol$', '.*'):
            with self.subTest(query=query):
                self.assertEqual(self.start(query), 'false')
                self.assertTrue(self.page.locator('[data-act=searchnext]').is_enabled())
        self.assertEqual(self.page.evaluate('regexWorkers.length'), 0)

    def test_abort_before_start_and_during_start_release_all_owned_resources(self):
        result = self.page.evaluate('''async () => {
            const probe = fgView('timeline').probe(), steps = regexHelpers.buildSearchIndex(probe.sName,probe.N);
            let result;
            do { result = steps.next(); } while (!result.done);
            const before = new AbortController(); before.abort();
            const rejected = await regexHelpers.matchSearchNamesWorker(result.value,probe.S,'a|b',before.signal)
                .then(() => 'unexpected success',error => error.searchKind);
            const created = [], revoked = [], create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
            URL.createObjectURL = blob => { const url = create(blob); created.push(url); return url; };
            URL.revokeObjectURL = url => { revoked.push(url); return revoke(url); };
            const controller = new AbortController();
            const pending = regexHelpers.matchSearchNamesWorker(result.value,probe.S,'a|b',controller.signal)
                .then(() => 'unexpected success',error => error.searchKind);
            controller.abort();
            return {before:rejected,during:await pending,created,revoked,workers:regexWorkers};
        }''')
        self.assertEqual(result['before'], 'abort')
        self.assertEqual(result['during'], 'abort')
        self.assertEqual(len(result['created']), 1)
        self.assertEqual(result['created'], result['revoked'])
        self.assertEqual(len(result['workers']), 1)
        self.assertEqual(result['workers'][0]['terminated'], 1)

    def test_no_recorded_names_still_validates_regex_and_returns_only_zero_bits(self):
        result = self.page.evaluate('''async () => {
            const index = {names:new Int32Array()}, strings = ['unrecorded name'];
            const hits = await regexHelpers.matchSearchNamesWorker(index,strings,'a|b',new AbortController().signal);
            const invalid = await regexHelpers.matchSearchNamesWorker(index,strings,'[',new AbortController().signal)
                .then(() => 'unexpected success',error => error.searchKind);
            return {hits:Array.from(hits),invalid,workers:regexWorkers};
        }''')
        self.assertEqual(result['hits'], [0])
        self.assertEqual(result['invalid'], 'regex')
        self.assertTrue(all(worker['terminated'] == 1 for worker in result['workers']))

    def test_malformed_or_failed_later_batches_publish_no_partial_matches_and_retry(self):
        for fault in ('size', 'boolean', 'sequence', 'messageerror', 'runtime'):
            with self.subTest(fault=fault):
                self.open_report()
                self.page.evaluate('''fault => {
                    const OriginalWorker = window.Worker;
                    window.restoreRegexWorker = () => { window.Worker = OriginalWorker; };
                    window.fakeRegexTerminated = 0;
                    window.Worker = class {
                        constructor() { queueMicrotask(() => this.onmessage?.({data:{type:'started'}})); }
                        terminate() { fakeRegexTerminated++; }
                        postMessage(data) {
                            if (data.type === 'init') {
                                queueMicrotask(() => this.onmessage({data:{type:'ready'}})); return;
                            }
                            const values = new Uint8Array(data.names.length).fill(1);
                            let id = data.id, buffer = values.buffer;
                            if (data.id > 1) {
                                if (fault === 'size') buffer = new ArrayBuffer(1);
                                else if (fault === 'boolean') values[0] = 2;
                                else if (fault === 'sequence') id++;
                                else if (fault === 'messageerror') { queueMicrotask(() => this.onmessageerror()); return; }
                                else if (fault === 'runtime') { queueMicrotask(() => this.onerror({preventDefault(){}})); return; }
                            }
                            queueMicrotask(() => this.onmessage({data:{type:'hits',id,buffer}}));
                        }
                    };
                }''', fault)
                self.start('^function_(1|20)$')
                self.ready()
                self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Search unavailable')
                self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'false')
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
                self.assertEqual(self.page.evaluate('fakeRegexTerminated'), 1)
                self.page.evaluate('restoreRegexWorker()')
                self.page.locator('.tl-search').press('Enter')
                self.ready()
                self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 4 calls')

    def test_loader_error_falls_back_before_matching_without_repeated_startups(self):
        self.page.evaluate('''() => {
            window.failedRegexStarts = 0;
            window.Worker = class {
                constructor() { failedRegexStarts++; queueMicrotask(() => this.onerror({preventDefault(){}})); }
                terminate() {}
            };
        }''')
        for query, status in (('^function_(1|20)$', '1 / 4 calls'), ('^function_(1|649)$', '2 / 4 calls')):
            self.start(query)
            self.ready()
            self.assertEqual(self.page.locator('.tl-count').inner_text(), status)
            self.assertIn('page thread', self.page.locator('.tl-search').get_attribute('title'))
        self.assertEqual(self.page.evaluate('failedRegexStarts'), 1)

    def test_startup_deadline_falls_back_without_leaving_a_worker_alive(self):
        self.page.evaluate('''() => {
            window.hangingRegexTerminated = 0;
            const schedule = window.setTimeout.bind(window);
            window.setTimeout = (callback,delay,...rest) => schedule(callback,delay === 5000 ? 1 : delay,...rest);
            window.Worker = class { terminate() { hangingRegexTerminated++; } };
        }''')
        self.start('^function_(1|20)$')
        self.ready()
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 4 calls')
        self.assertEqual(self.page.evaluate('hangingRegexTerminated'), 1)
        self.assertIn('page thread', self.page.locator('.tl-search').get_attribute('title'))

    def test_filter_change_during_background_matching_uses_only_the_new_scope(self):
        self.page.evaluate('''() => {
            const OriginalWorker = window.Worker;
            window.heldRegexBatches = [];
            window.releaseRegexBatches = false;
            window.Worker = function(...args) {
                const worker = new OriginalWorker(...args), post = worker.postMessage.bind(worker), stop = worker.terminate.bind(worker);
                let active = true;
                worker.terminate = () => { active = false; return stop(); };
                worker.postMessage = (data,...rest) => {
                    if (data.type === 'batch' && !releaseRegexBatches) heldRegexBatches.push(() => { if (active) post(data,...rest); });
                    else post(data,...rest);
                };
                return worker;
            };
        }''')
        self.start('^function_(1|20)$')
        self.page.wait_for_function("() => regexWorkers[0].messages.includes('ready')")
        selected = self.page.evaluate('''() => {
            const probe = fgView('timeline').probe();
            const matches = Array.from({length:probe.N},(unused,index) => index).filter(index => probe.nameOf(index) === 'function_20');
            const target = matches.at(-1), line = fgData.read('tlData').cols.line[target];
            fgNotifyFilter('one call only',new Set([line]));
            releaseRegexBatches = true;
            for (const release of heldRegexBatches) release();
            return target;
        }''')
        self.ready()
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 1 calls')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected)
        self.assertEqual(self.page.evaluate('regexWorkers.length'), 2)
        self.assertTrue(self.page.evaluate('regexWorkers.every(worker => worker.terminated === 1)'))

    def test_busy_worker_status_keeps_compact_canvas_geometry_and_both_themes(self):
        self.page.set_viewport_size({'width': 320, 'height': 800})
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                self.page.evaluate("theme => document.documentElement.setAttribute('data-theme',theme)", theme)
                self.start('^(a+)+$')
                self.page.wait_for_function('''() => Math.abs(document.querySelector('.tl-canvas').getBoundingClientRect().height -
                    document.querySelector('.tl-scroll').clientHeight) <= 1''')
                self.assertTrue(self.page.locator('.tl-search').is_visible())
                self.assertTrue(self.page.locator('.tl-count').is_visible())
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth === document.documentElement.clientWidth'))
                self.page.locator('.tl-search').press('Escape')
        self.assertTrue(self.page.evaluate('regexWorkers.every(worker => worker.terminated === 1)'))
