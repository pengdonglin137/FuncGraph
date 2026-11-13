"""Report-bound saved views, transfer validation and browser interaction."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import unittest

import helpers
from fglib.viewer import create_server
from fglib.report import HtmlGenerator
from test_uftrace_import import capture, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ROOT = Path(helpers.ROOT)


class TimelineIdentityTest(unittest.TestCase):
    def identity(self, data):
        generator = object.__new__(HtmlGenerator)
        generator._views = lambda: [('timeline', 'Timeline', '', 'timelineView', '', 'tlData', data, '')]
        generator._attach_inline = lambda: None
        generator._attach_timeline_sources = lambda: None
        generator._attach_tail_sources = lambda: None
        section = next(value for name, value in generator._iter_view_parts() if name == 'timeline_identity')
        return helpers.json_script(section, 'fgTimelineIdentity')

    def test_only_generated_build_time_is_ignored_and_original_data_is_unchanged(self):
        data = {'n': 1, 'stats': {'build_time': 0.123}, 'record': {'build_time': 7}}
        original = json.dumps(data)
        identity = self.identity(data)
        self.assertEqual(json.dumps(data), original)
        data['stats']['build_time'] = 1.234
        self.assertEqual(self.identity(data), identity)
        data['record']['build_time'] = 8
        self.assertNotEqual(self.identity(data), identity)

    def test_actual_call_identity_time_and_arguments_change_the_fingerprint(self):
        data = {'n': 1, 'cols': {'name': [1], 'dur': [10], 'args': [2]}, 'strings': ['worker', 'value=1']}
        identity = self.identity(data)
        for field in ('name', 'dur', 'args'):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(data))
                changed['cols'][field][0] += 1
                self.assertNotEqual(self.identity(changed), identity)


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class SavedViewValidationTest(unittest.TestCase):
    def run_script(self, assertions):
        source = (ROOT / 'assets/timeline/saved-views.js').read_text()
        setup = '''
            const assert = require('node:assert/strict');
            const identity = 'a'.repeat(64);
            const N = 2, NA = 3, HAS_INL = true, sDurAdj = [1, 2];
            const TRACKS = [{id:0}, {id:1}], ORDER = [...TRACKS, {id:-1}];
            const isGhost = index => false;
            const document = {getElementById: () => ({textContent:JSON.stringify(identity)})};
            const writes = [];
            const localStorage = {getItem: () => null, setItem: (...args) => writes.push(args)};
            const sample = () => ({format:'funcgraph.saved-views',version:1,report:identity,views:[{
                id:'first',name:'My call',note:'check this',state:{view:[-20,100],selected:0,filter:'',search:'',
                hideUnmatched:false,inline:false,adjusted:false,collapsed:[-1],range:null,scroll:{track:0,offset:12}}}]});
        '''
        result = subprocess.run(['node', '-e', setup + source + assertions], capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validation_copies_and_preserves_values_without_mutating_input(self):
        self.run_script('''
            const original = sample(), encoded = JSON.stringify(original);
            const result = validateSavedViews(original, identity);
            assert.deepEqual(result, original);
            assert.notEqual(result.views[0].state, original.views[0].state);
            result.views[0].state.view[0] = 0;
            assert.equal(JSON.stringify(original), encoded);
            assert.equal(writes.length, 0);
        ''')

    def test_invalid_versions_identities_types_coordinates_and_indices_fail_closed(self):
        self.run_script('''
            const changes = [
                data => data.version = 2, data => data.format = 'other', data => data.report = 'b'.repeat(64),
                data => data.views.push(data.views[0]), data => data.views[0].name = ' ',
                data => data.views[0].note = 'x'.repeat(2001), data => data.views[0].id = '__proto__.x',
                data => data.views[0].state.view = [1,1], data => data.views[0].state.view = [NaN,2],
                data => data.views[0].state.view = [0,Infinity], data => data.views[0].state.selected = 3,
                data => data.views[0].state.selected = 0.5, data => data.views[0].state.inline = 'false',
                data => data.views[0].state.selected = 2, data => data.views[0].state.collapsed = [0,0],
                data => data.views[0].state.collapsed = [99], data => data.views[0].state.scroll.offset = -1,
                data => data.views[0].state.range = {s:2,e:1,track:null},
                data => data.views[0].state.range = {s:1,e:2,track:99},
                data => data.views[0].state.filter = 'x'.repeat(4097),
                data => data.views[0].state.search = 'x'.repeat(513)
            ];
            for (const change of changes) {
                const data = sample(); change(data);
                assert.throws(() => validateSavedViews(data, identity));
            }
            for (const value of [null, [], {}, {format:'funcgraph.saved-views',version:1,report:identity}])
                assert.throws(() => validateSavedViews(value, identity));
            assert.equal(writes.length, 0);
        ''')

    def test_view_limit_and_corrupt_storage_never_overwrite_existing_data(self):
        self.run_script('''
            const entry = sample().views[0];
            assert.throws(() => savedViewsWrite(Array.from({length:51}, (_, index) => ({...entry,id:'id'+index}))));
            assert.equal(writes.length, 0);
            savedViewsStore = null; localStorage.getItem = () => 'broken';
            const store = savedViewsRead();
            assert.match(store.error, /could not be read/);
            assert.throws(() => savedViewsWrite([]));
            assert.equal(writes.length, 0);
        ''')

    def test_unavailable_or_full_storage_keeps_exportable_session_views(self):
        self.run_script('''
            localStorage.getItem = () => {throw new Error('denied')};
            localStorage.setItem = () => {throw new Error('quota')};
            savedViewsWrite(sample().views);
            const store = savedViewsRead();
            assert.equal(store.data.views.length, 1);
            assert.match(store.warning, /export/);
            assert.equal(writes.length, 0);
        ''')

    def test_other_tabs_and_temporarily_unreadable_storage_are_never_overwritten(self):
        self.run_script('''
            savedViewsRead();
            const changed = JSON.stringify({...sample(),views:[]});
            localStorage.getItem = () => changed;
            assert.throws(() => savedViewsWrite(sample().views), /another tab/);
            assert.equal(writes.length, 0);
            savedViewsStore = null;
            localStorage.getItem = () => {throw new Error('temporarily unreadable')};
            savedViewsRead();
            localStorage.getItem = () => changed;
            savedViewsWrite(sample().views);
            assert.equal(writes.length, 0);
            assert.equal(savedViewsRead().data.views.length, 1);
        ''')


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TimelineSavedViewsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        common = ['--no-config', '--timeline', '--enable-fold', '--func-links', '--overhead', '1000,100']
        cls.html, _ = cls.kernel.run(helpers.fixture('pi4_sched'), *common, '--filter', out='report.html')
        cls.rebuilt, _ = cls.kernel.run(helpers.fixture('pi4_sched'), *common, '--filter', out='rebuilt.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *common, '--filter', '--report-mode', 'sidecar', out='sidecar')
        cls.kernel.run(helpers.fixture('pi4_sched'), *common, '--no-text-view', out='timeline-only.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *common, out='unfiltered.html')
        cls.kernel.run(helpers.fixture('pi4_default'), '--no-config', '--timeline', '--filter', out='packed.html')
        count_trace = Path(cls.kernel.dir) / 'count.txt'
        count_trace.write_text(re.sub(r'^\s*\d+\.\d+\s*\|', '',
                                     Path(helpers.fixture('pi4_nodur')).read_text(), flags=re.M))
        cls.kernel.run(str(count_trace), '--no-config', '--timeline', '--filter', out='count.html')
        event_trace = Path(cls.kernel.dir) / 'events.txt'
        event_trace.write_text('\n'.join(line for line in Path(helpers.fixture('pi4_sched')).read_text().splitlines()
                                         if 'sched_switch:' in line or 'sched_wakeup:' in line))
        cls.kernel.run(str(event_trace), '--no-config', '--timeline', '--filter', out='events.html')
        user_trace = Path(cls.kernel.dir) / 'user.jsonl'
        user_trace.write_text(stream([record for record in capture() if record['space'] == 'user']))
        cls.kernel.run(str(user_trace), '--no-config', '--timeline', '--filter', out='user.html')
        cls.kernel.run(str(ROOT / 'samples/sample_mixed.jsonl'), '--no-config', '--timeline', '--filter', out='mixed.html')
        cls.server = create_server(Path(cls.kernel.dir) / 'sidecar')
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = cls.server.url
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            cls.playwright.stop(); cls.server.shutdown(); cls.server.server_close(); cls.worker.join(); cls.kernel.close()
            raise unittest.SkipTest(str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.playwright.stop()
        cls.server.shutdown(); cls.server.server_close(); cls.worker.join(); cls.kernel.close()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1400, 'height': 860})
        self.page = self.context.new_page()
        self.page.set_default_timeout(6000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open_report(self, name='report.html', http=False):
        self.page.goto(self.base if http else (Path(self.kernel.dir) / name).as_uri())
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.locator('.tl-canvas').wait_for()

    def open_views(self):
        helpers.click_timeline_tool(self.page, '[data-act=saved-views]')
        self.page.get_by_role('dialog', name='Saved views', exact=True).wait_for()

    def save(self, name='Investigate call', note=None):
        self.open_views()
        self.page.locator('.tl-saved-views input[name=name]').fill(name)
        if note is not None:
            self.page.get_by_text('Add a note (optional)', exact=True).click()
            self.page.locator('.tl-saved-views textarea').fill(note)
        self.page.get_by_role('button', name='Save current view', exact=True).click()
        self.page.wait_for_function('() => document.querySelectorAll("[data-saved-restore]").length > 0')
        return self.payload()

    def payload(self):
        return self.page.evaluate('''() => {
            const identity = JSON.parse(document.getElementById('fgTimelineIdentity').textContent);
            return JSON.parse(localStorage.getItem('fgTl.savedViews.' + identity));
        }''')

    def close_views(self):
        self.page.get_by_role('button', name='Close saved views', exact=True).click()

    def import_payload(self, payload):
        self.page.locator('[data-saved-file]').set_input_files({
            'name': 'views.json', 'mimeType': 'application/json',
            'buffer': json.dumps(payload).encode() if not isinstance(payload, bytes) else payload})
        self.page.wait_for_function('() => document.querySelector(".tl-saved-views").getAttribute("aria-busy") !== "true"')

    def test_identity_is_stable_across_rebuild_and_inline_sidecar_delivery(self):
        identity = helpers.json_script(self.html, 'fgTimelineIdentity')
        self.assertRegex(identity, '^[a-f0-9]{64}$')
        data = re.search(r'<script[^>]+id="tlData"[^>]*>(.*?)</script>', self.html, re.S).group(1)
        normalized = re.sub(r'("build_time":)[0-9.eE+-]+', r'\g<1>0', data)
        self.assertEqual(identity, hashlib.sha256(normalized.encode()).hexdigest())
        self.assertEqual(identity, helpers.json_script(self.rebuilt, 'fgTimelineIdentity'))
        shell = (Path(self.kernel.dir) / 'sidecar/index.html').read_text()
        self.assertEqual(identity, helpers.json_script(shell, 'fgTimelineIdentity'))

    def test_menu_is_lazy_keyboard_accessible_and_does_not_resize_canvas(self):
        self.open_report()
        self.assertEqual(self.page.locator('.tl-saved-views').count(), 0)
        self.assertFalse(self.page.locator('[data-act=saved-views]').is_visible())
        canvas = self.page.locator('.tl-canvas').bounding_box()
        before = self.page.evaluate('fgView("timeline").probe().view()')
        self.open_views()
        self.assertFalse(self.page.locator('.tl-saved-note-entry').get_attribute('open'))
        self.assertFalse(self.page.locator('.tl-saved-transfer').get_attribute('open'))
        self.page.keyboard.press('ArrowLeft')
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), before)
        self.page.keyboard.press('Escape')
        self.assertFalse(self.page.locator('.tl-saved-views').is_visible())
        self.assertEqual(self.page.locator('.tl-canvas').bounding_box(), canvas)
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'options')

    def test_call_zoom_note_and_raw_data_survive_restore_and_reload(self):
        self.open_report()
        original = self.page.evaluate('''() => {
            const probe = fgView('timeline').probe(); probe.select(1); probe.zoomTo(probe.sStart[1], probe.sEnd[1], 1);
            window.savedOriginal = {start:probe.sStart,tracks:probe.TRACKS,data:JSON.stringify(fgData.read('tlData'))};
            return {view:probe.view(),selected:probe.sel()};
        }''')
        payload = self.save('Write latency', 'Compare the second write.\nKeep the original trace.')
        self.assertEqual(payload['views'][0]['note'], 'Compare the second write.\nKeep the original trace.')
        self.close_views()
        self.page.evaluate('fgView("timeline").probe().select(-1)')
        self.page.locator('[data-act=fit]').click()
        self.open_views()
        self.page.get_by_role('button', name='Write latency', exact=True).click()
        self.page.wait_for_function('() => !document.querySelector(".tl-saved-views").open')
        self.assertEqual(self.page.evaluate('''() => { const probe=fgView('timeline').probe();
            return {view:probe.view(),selected:probe.sel()}; }'''), original)
        self.assertTrue(self.page.evaluate('''() => {const probe=fgView('timeline').probe(); return probe.sStart === savedOriginal.start &&
            probe.TRACKS === savedOriginal.tracks && JSON.stringify(fgData.read('tlData')) === savedOriginal.data;}'''))
        self.page.reload(); self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.open_views()
        self.assertEqual(self.page.locator('[data-saved-restore]').count(), 1)
        self.page.get_by_role('button', name='Write latency', exact=True).click()
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), original['selected'])

    def test_applied_filter_not_unsubmitted_draft_is_saved_and_restored(self):
        self.open_report()
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('func:read')
        self.page.locator('.tl-ftext').press('Enter')
        self.page.wait_for_function('() => fgFilterState.text === "func:read" && !fgFilterProgress')
        self.page.locator('.tl-ftext').fill('func:not_applied')
        self.page.locator('[data-act=saved-views]').click()
        self.page.locator('.tl-saved-views input[name=name]').fill('Read calls')
        self.page.get_by_role('button', name='Save current view', exact=True).click()
        self.assertEqual(self.payload()['views'][0]['state']['filter'], 'func:read')
        self.close_views()
        self.page.evaluate('clearFilter()')
        self.open_views(); self.page.get_by_role('button', name='Read calls', exact=True).click()
        self.page.wait_for_function('() => !document.querySelector(".tl-saved-views").open')
        self.assertEqual(self.page.evaluate('fgFilterState.text'), 'func:read')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'func:read')

    def test_range_track_collapse_inline_and_duration_basis_restore(self):
        self.open_report()
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.page.evaluate('''() => {const probe=fgView('timeline').probe(); probe.inline(true);
            probe.TRACKS[0].collapsed=true; probe.range(probe.sStart[0],probe.sEnd[0],0);}''')
        payload = self.save('Critical range')
        self.assertTrue(payload['views'][0]['state']['adjusted'])
        self.assertTrue(payload['views'][0]['state']['inline'])
        self.close_views()
        self.page.evaluate('''() => {const probe=fgView('timeline').probe(); probe.inline(false); probe.TRACKS[0].collapsed=false; probe.select(0);}''')
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.open_views(); self.page.get_by_role('button', name='Critical range', exact=True).click()
        self.page.wait_for_function('() => !document.querySelector(".tl-saved-views").open')
        self.assertTrue(self.page.evaluate('fgView("timeline").probe().TRACKS[0].collapsed'))
        self.assertIn('subtracted', self.page.locator('[data-act=adj]').inner_text())
        self.assertIn('shown', self.page.locator('[data-act=inl]').inner_text())
        self.assertTrue(self.page.locator('.tl-range-head').is_visible())

    def test_export_and_import_preserve_notes_merge_without_overwrite_and_escape_html(self):
        self.open_report()
        note = '<img src=x onerror="window.savedViewXss=true"> & \"quoted\"'
        payload = self.save('<script>saved</script>', note)
        self.assertEqual(self.page.locator('.tl-saved-note img').count(), 0)
        self.assertEqual(self.page.locator('.tl-saved-note').inner_text(), note)
        self.page.get_by_text('Import / export', exact=True).click()
        with self.page.expect_download() as download:
            self.page.get_by_role('button', name='Export views', exact=True).click()
        exported = json.loads(Path(download.value.path()).read_text())
        self.assertEqual(exported, payload)
        self.import_payload(payload)
        self.assertEqual(len(self.payload()['views']), 1)
        payload['views'][0]['name'] = 'Different note with same ID'
        self.import_payload(payload)
        entries = self.payload()['views']
        self.assertEqual(len(entries), 2)
        self.assertEqual(len({entry['id'] for entry in entries}), 2)
        self.assertIsNone(self.page.evaluate('window.savedViewXss'))
        self.assertNotIn('cols', exported)

    def test_mismatched_malformed_oversized_and_invalid_imports_are_atomic(self):
        self.open_report(); original = self.save()
        for change in ('report', 'index', 'malformed', 'oversized'):
            with self.subTest(change=change):
                value = json.loads(json.dumps(original))
                if change == 'report': value['report'] = 'f' * 64
                elif change == 'index': value['views'][0]['state']['selected'] = 10 ** 9
                elif change == 'malformed': value = b'{bad json'
                else: value = b' ' * (1024 * 1024 + 1)
                self.import_payload(value)
                self.assertEqual(self.payload(), original)
                self.assertTrue(self.page.locator('[data-saved-status]').inner_text())

    def test_remove_returns_focus_and_preserves_trace(self):
        self.open_report(); self.save('One view')
        count = self.page.evaluate('fgView("timeline").probe().N')
        self.page.get_by_role('button', name='Remove view One view', exact=True).click()
        self.assertEqual(self.payload()['views'], [])
        self.assertTrue(self.page.locator('[data-saved-empty]').is_visible())
        self.assertTrue(self.page.locator('input[name=name]').evaluate('element => element === document.activeElement'))
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().N'), count)

    def test_repeated_save_does_not_create_duplicate_entries(self):
        self.open_report(); self.save('One view')
        self.page.get_by_role('button', name='Save current view', exact=True).click()
        self.assertEqual(len(self.payload()['views']), 1)
        self.assertIn('already saved', self.page.locator('[data-saved-status]').inner_text())

    def test_storage_denied_falls_back_to_exportable_session_state(self):
        self.page.add_init_script('''const originalGet = Storage.prototype.getItem, originalSet = Storage.prototype.setItem;
            Storage.prototype.getItem = function(key) {if(key.startsWith('fgTl.savedViews.')) throw new Error('denied'); return originalGet.call(this,key);};
            Storage.prototype.setItem = function(key,value) {if(key.startsWith('fgTl.savedViews.')) throw new Error('full'); return originalSet.call(this,key,value);};''')
        self.open_report(); self.open_views()
        self.page.get_by_role('button', name='Save current view', exact=True).click()
        self.assertEqual(self.page.locator('[data-saved-restore]').count(), 1)
        self.assertIn('export', self.page.locator('[data-saved-storage]').inner_text())
        self.page.get_by_text('Import / export', exact=True).click()
        with self.page.expect_download() as download:
            self.page.get_by_role('button', name='Export views', exact=True).click()
        self.assertEqual(len(json.loads(Path(download.value.path()).read_text())['views']), 1)

    def test_corrupt_existing_storage_is_not_overwritten(self):
        self.open_report()
        self.page.evaluate('''() => {const identity=JSON.parse(document.getElementById('fgTimelineIdentity').textContent);
            localStorage.setItem('fgTl.savedViews.'+identity,'invalid original data');}''')
        self.open_views()
        self.assertTrue(self.page.locator('[data-saved-save]').is_disabled())
        self.assertIn('not changed', self.page.locator('[data-saved-storage]').inner_text())
        self.assertIn('invalid original data', self.page.evaluate('JSON.stringify(localStorage)'))

    def test_pending_filter_cannot_be_misrepresented_as_a_saved_view(self):
        self.open_report(); self.open_views()
        self.page.evaluate('window.fgFilterProgress={text:"func:pending",message:"working"}')
        self.page.get_by_role('button', name='Save current view', exact=True).click()
        self.assertEqual(self.page.locator('[data-saved-restore]').count(), 0)
        self.assertIn('filtering', self.page.locator('[data-saved-status]').inner_text())
        self.page.evaluate('window.fgFilterProgress=null')
        self.page.evaluate('document.querySelector(".tl-search").value="x".repeat(513)')
        self.page.get_by_role('button', name='Save current view', exact=True).click()
        self.assertIn('Shorten', self.page.locator('[data-saved-status]').inner_text())
        self.assertEqual(self.page.locator('[data-saved-restore]').count(), 0)

    def test_inline_sidecar_timeline_only_packed_user_and_mixed_reports(self):
        for name, http in [('sidecar/', True), ('timeline-only.html', False), ('unfiltered.html', False),
                           ('packed.html', False), ('count.html', False), ('user.html', False), ('mixed.html', False)]:
            with self.subTest(name=name):
                self.open_report(name, http)
                self.page.evaluate('fgView("timeline").probe().select(0)')
                self.save('Round trip')
                self.close_views(); self.page.evaluate('fgView("timeline").probe().select(-1)')
                self.open_views(); self.page.get_by_role('button', name='Round trip', exact=True).click()
                self.page.wait_for_function('() => !document.querySelector(".tl-saved-views").open')
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 0)

    def test_event_only_report_saves_view_without_inventing_a_call(self):
        self.open_report('events.html')
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().N'), 0)
        original = self.page.evaluate('fgView("timeline").probe().view()')
        payload = self.save('Scheduler observations')
        self.assertEqual(payload['views'][0]['state']['selected'], -1)
        self.close_views(); self.open_views()
        self.page.get_by_role('button', name='Scheduler observations', exact=True).click()
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), original)
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), -1)

    def test_sidecar_filter_failure_retains_position_and_can_retry_without_mounting_text(self):
        self.open_report('sidecar/', True)
        original = self.page.evaluate('fgView("timeline").probe().view()')
        payload = self.save('Retry filtered view')
        payload['views'][0]['name'] = 'Needs full index'
        payload['views'][0]['state']['filter'] = 'func:read'
        self.import_payload(payload)
        self.page.route('**/text-index.json', lambda route: route.abort())
        self.page.get_by_role('button', name='Needs full index', exact=True).click()
        self.page.wait_for_function('() => document.querySelector("[data-saved-status]").textContent.includes("could not be applied")')
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), original)
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.page.unroute('**/text-index.json')
        self.page.get_by_role('button', name='Needs full index', exact=True).click()
        self.page.wait_for_function('() => !document.querySelector(".tl-saved-views").open')
        self.assertEqual(self.page.evaluate('fgFilterState.text'), 'func:read')
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)

    def test_cancelled_sidecar_restore_does_not_apply_late_results(self):
        self.open_report('sidecar/', True)
        original = self.page.evaluate('fgView("timeline").probe().view()')
        payload = self.save('Original')
        payload['views'][0]['name'] = 'Delayed'
        payload['views'][0]['state']['filter'] = 'func:read'
        self.import_payload(payload)
        pending = []
        self.page.route('**/text-index.json', lambda route: pending.append(route))
        self.page.get_by_role('button', name='Delayed', exact=True).click()
        self.page.wait_for_function('() => !!fgFilterProgress && document.querySelector(".tl-saved-views").getAttribute("aria-busy") === "true"')
        self.page.keyboard.press('Escape')
        self.page.wait_for_function('() => !document.querySelector(".tl-saved-views").open && !fgFilterProgress')
        for route in pending:
            route.abort()
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), original)
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), '')

    def test_missing_filter_controls_refuse_incompatible_restore_without_moving_view(self):
        self.open_report('unfiltered.html')
        original = self.page.evaluate('fgView("timeline").probe().view()')
        payload = self.save('Original')
        payload['views'][0]['name'] = 'Needs filter controls'
        payload['views'][0]['state']['filter'] = 'func:read'
        self.import_payload(payload)
        self.page.get_by_role('button', name='Needs filter controls', exact=True).click()
        self.page.wait_for_function('() => document.querySelector("[data-saved-status]").textContent.includes("--filter")')
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), original)

    def test_mobile_dark_mode_and_fullscreen_escape_keep_the_interface_compact(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 390, 'height': 740}, is_mobile=True, has_touch=True)
        self.page = self.context.new_page(); self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report()
        self.page.evaluate('document.documentElement.dataset.theme="dark"')
        self.page.locator('[data-act=max]').click()
        self.page.wait_for_function('() => document.getElementById("timelineView").classList.contains("tl-fullscreen")')
        self.open_views()
        box = self.page.locator('.tl-saved-views').bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], 391)
        self.assertGreaterEqual(self.page.locator('[data-saved-save]').bounding_box()['height'], 44)
        self.page.get_by_role('button', name='Save current view', exact=True).click()
        payload = self.payload()
        prototype = payload['views'][0]
        payload['views'] = [dict(prototype, id='mobile-' + str(index), name='View ' + str(index), note='A long note. ' * 20)
                            for index in range(15)]
        self.import_payload(payload)
        self.page.locator('.tl-saved-views').evaluate('element => element.scrollTop=element.scrollHeight')
        box = self.page.locator('.tl-saved-views').bounding_box()
        close = self.page.locator('[data-saved-close]').bounding_box()
        self.assertGreaterEqual(close['y'], box['y'])
        self.assertLessEqual(close['y'] + close['height'], box['y'] + box['height'])
        self.page.keyboard.press('Escape')
        self.assertFalse(self.page.locator('.tl-saved-views').is_visible())
        self.assertTrue(self.page.locator('#timelineView').evaluate('element => element.classList.contains("tl-fullscreen")'))

    def test_view_switch_dismisses_dialog_without_focus_leaking_to_hidden_timeline(self):
        self.open_report(); self.open_views()
        self.page.evaluate("async () => {await fgSwitchView('text');}")
        self.assertFalse(self.page.locator('.tl-saved-views').is_visible())
        self.assertFalse(self.page.evaluate('!!document.activeElement.closest("#timelineView")'))

    def test_hover_does_not_read_saved_views_or_rehash_report_data(self):
        self.open_report(); self.save(); self.close_views()
        self.page.evaluate('''() => {window.savedReads=0; const original=Storage.prototype.getItem;
            Storage.prototype.getItem=function(key){if(key.startsWith('fgTl.savedViews.')) window.savedReads++; return original.call(this,key);};}''')
        box = self.page.locator('.tl-canvas').bounding_box()
        self.page.mouse.move(box['x'] + 100, box['y'] + 30)
        self.page.mouse.move(box['x'] + 400, box['y'] + 70, steps=10)
        self.assertEqual(self.page.evaluate('window.savedReads'), 0)


if __name__ == '__main__':
    unittest.main()
