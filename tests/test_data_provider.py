"""Verified lazy report resources and cancellable view startup over real loopback HTTP."""
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import unittest

import helpers

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def resource(filename, body, format='json'):
    return dict(file=filename, bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), format=format)


class ResourceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with self.server.lock:
            self.server.counts[self.path] += 1
            response = self.server.responses.get(self.path, (404, b'not found', None))
            self.server.events.setdefault(self.path, threading.Event()).set()
        status, body, gate = response
        if gate is not None:
            gate.wait(10)
        try:
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *arguments):
        pass


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class DataProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.html, unused = cls.kernel.run(helpers.fixture('pi4_sched'), '--enable-fold', '--filter',
                                         '--func-links', '--timeline', '--compare', helpers.fixture('pi4_default'))
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            cls.playwright.stop()
            cls.kernel.close()
            raise unittest.SkipTest('chromium not available: ' + str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.kernel.close()

    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ResourceHandler)
        self.server.responses = {'/favicon.ico': (204, b'', None)}
        self.server.counts = Counter()
        self.server.events = {}
        self.server.lock = threading.Lock()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
        self.errors = []
        self.console_errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('console', lambda message: self.console_errors.append(message.text) if message.type == 'error' else None)
        self.payloads = {name: encode(helpers.json_script(self.html, name)) for name in ['tlData', 'cmpData']}
        self.payloads.update(notes=encode(dict(text='中文🙂')), raw='原始文本🙂'.encode('utf-8'))

    def tearDown(self):
        self.page.close()
        with self.server.lock:
            for status, body, gate in self.server.responses.values():
                if gate is not None:
                    gate.set()
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.assertEqual(self.errors, [])

    def publish(self, manifest_change=None, descriptor_change=None, embedded=False):
        resources = {}
        for name, body in self.payloads.items():
            filename = name + ('.txt' if name == 'raw' else '.json')
            resources[name] = resource(filename, body, 'text' if name == 'raw' else 'json')
            self.server.responses['/report/data/' + filename] = (200, body, None)
        manifest = dict(version=1, report_id='f' * 32, resources=resources)
        if manifest_change:
            manifest_change(manifest)
        manifest_body = encode(manifest)
        descriptor = dict(version=1, report_id='f' * 32, manifest=resource('data/manifest.json', manifest_body))
        if descriptor_change:
            descriptor_change(descriptor)
        self.server.responses['/report/data/manifest.json'] = (200, manifest_body, None)
        report = self.html
        if not embedded:
            report = re.sub(r'    <script type="application/json" id="(?:tlData|cmpData)">.*?</script>\n', '', report, flags=re.S)
        report = report.replace('</head>', '<script type="application/json" id="fgDataManifest">' +
                                encode(descriptor).decode('utf-8') + '</script></head>', 1)
        self.external_html = report
        self.server.responses['/report/index.html'] = (200, report.encode('utf-8'), None)
        if self.page.url.startswith(self.origin):
            self.page.evaluate('localStorage.clear()')
        self.page.goto(self.origin + '/report/index.html', wait_until='load')

    def count(self, filename):
        with self.server.lock:
            return self.server.counts['/report/data/' + filename]

    def gate(self, filename):
        path = '/report/data/' + filename
        with self.server.lock:
            status, body, unused = self.server.responses[path]
            gate = threading.Event()
            self.server.responses[path] = (status, body, gate)
            event = self.server.events.setdefault(path, threading.Event())
        return gate, event

    def model(self):
        return self.page.evaluate('''() => {
            const probe = fgView('timeline').probe();
            return { N: probe.N, NA: probe.NA, SYN: probe.SYN, S: probe.S,
                columns: ['sName', 'sStart', 'sEnd', 'sDepth', 'sParent', 'sTask'].map(name => Array.from(probe[name])),
                tracks: probe.TRACKS.map(track => [track.rowsT, track.rowsI, track.off, track.rs, track.io]) };
        }''')

    def test_external_views_are_loaded_only_on_demand_and_reused(self):
        self.publish()
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
        self.assertEqual(self.count('manifest.json'), 0)
        self.assertTrue(self.page.evaluate("async () => !!(await fgSwitchView('timeline'))"))
        self.assertEqual(self.count('manifest.json'), 1)
        self.assertEqual(self.count('tlData.json'), 1)
        self.assertEqual(self.count('cmpData.json'), 0)
        self.assertGreater(self.page.locator('.tl-toolbar').count(), 0)
        self.assertTrue(self.page.evaluate("async () => !!(await fgSwitchView('compare'))"))
        self.assertGreater(self.page.locator('.cmp-table tbody tr').count(), 0)
        self.page.evaluate("fgSwitchView('timeline')")
        self.assertEqual(self.count('tlData.json'), 1)
        self.assertEqual(self.count('cmpData.json'), 1)
        self.assertEqual(self.count('manifest.json'), 1)
        self.assertEqual(self.console_errors, [])

    def test_external_and_embedded_complete_models_match(self):
        self.publish(embedded=True)
        self.assertEqual(self.page.evaluate("typeof fgSwitchView('timeline').probe"), 'function')
        expected = self.model()
        self.assertEqual(self.count('manifest.json'), 0)
        self.publish()
        self.page.evaluate("async () => await fgSwitchView('timeline')")
        self.assertEqual(self.model(), expected)
        self.assertEqual(self.console_errors, [])

    def test_cached_input_survives_partial_and_complete_model_builds(self):
        timeline = json.loads(self.payloads['tlData'])
        caller = next(value for value in timeline['cols']['caller'] if value >= 0)
        timeline['inline'] = {str(caller): [['outer', 'test.c', 1], ['inner', 'test.c', 2]]}
        open_wait = timeline['offcpu'][0].copy()
        open_wait[2] = None
        timeline['offcpu'].append(open_wait)
        self.payloads['tlData'] = encode(timeline)
        self.publish()
        self.page.evaluate("async () => { await fgData.load('tlData'); window.originalInput = JSON.stringify(fgData.read('tlData')); }")
        facts = self.page.evaluate('''() => {
            const steps = __tlSteps();
            for (let index = 0; index < 5; index++) steps.next();
            steps.return();
            const afterCancel = JSON.stringify(fgData.read('tlData'));
            const first = __tlBoot().probe();
            const firstNames = first.S.slice();
            const second = __tlBoot().probe();
            return { afterCancel, afterBoot: JSON.stringify(fgData.read('tlData')),
                firstNames, secondNames: second.S, count: second.NA,
                cleanWaits: fgData.read('tlData').offcpu.every(record => !Object.prototype.hasOwnProperty.call(record, 'ldI')) };
        }''')
        original = self.page.evaluate('originalInput')
        self.assertEqual(facts['afterCancel'], original)
        self.assertEqual(facts['afterBoot'], original)
        self.assertEqual(facts['firstNames'], facts['secondNames'])
        self.assertTrue(facts['cleanWaits'])
        self.assertGreater(facts['count'], timeline['n'])

    def test_cancel_during_fetch_settles_readiness_and_reopen_retries(self):
        self.publish()
        gate, event = self.gate('tlData.json')
        self.page.evaluate("() => { window.pendingView = fgSwitchView('timeline'); window.sameView = fgSwitchView('timeline'); }")
        self.assertTrue(event.wait(5))
        self.assertTrue(self.page.evaluate('pendingView === sameView'))
        self.assertTrue(self.page.evaluate("fgView('timeline') === pendingView"))
        self.page.locator('.fg-loading button').click()
        self.assertTrue(self.page.evaluate('async () => await pendingView === null'))
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
        gate.set()
        self.assertTrue(self.page.evaluate("async () => !!(await fgSwitchView('timeline'))"))
        self.assertEqual(self.count('tlData.json'), 2)
        self.assertEqual(self.count('manifest.json'), 1)
        self.assertEqual(self.console_errors, [])

    def test_shared_manifest_survives_one_subscriber_canceling(self):
        self.publish()
        gate, event = self.gate('manifest.json')
        self.page.evaluate('''() => {
            window.canceled = new AbortController();
            window.first = fgData.load('tlData', {signal:canceled.signal}).catch(error => error.name);
            window.second = fgData.load('cmpData');
        }''')
        self.assertTrue(event.wait(5))
        self.page.evaluate('canceled.abort()')
        self.assertEqual(self.page.evaluate('async () => await first'), 'AbortError')
        gate.set()
        self.assertTrue(self.page.evaluate('async () => !!(await second).rows.length'))
        self.assertEqual(self.count('manifest.json'), 1)
        self.assertEqual(self.count('tlData.json'), 0)
        self.assertEqual(self.count('cmpData.json'), 1)

    def test_last_manifest_subscriber_cancel_does_not_poison_a_new_request(self):
        self.publish()
        gate, event = self.gate('manifest.json')
        self.page.evaluate('''() => {
            window.canceled = new AbortController();
            window.first = fgData.load('notes', {signal:canceled.signal}).catch(error => error.name);
        }''')
        self.assertTrue(event.wait(5))
        self.page.evaluate('canceled.abort()')
        self.assertEqual(self.page.evaluate('async () => await first'), 'AbortError')
        self.page.evaluate("() => { window.second = fgData.load('notes'); }")
        gate.set()
        self.assertEqual(self.page.evaluate('async () => await second'), dict(text='中文🙂'))
        self.assertEqual(self.count('manifest.json'), 2)
        self.assertEqual(self.count('notes.json'), 1)
        self.assertEqual(self.console_errors, [])

    def test_shared_resource_survives_one_subscriber_canceling(self):
        self.publish()
        gate, event = self.gate('notes.json')
        self.page.evaluate('''() => {
            window.canceled = new AbortController();
            window.first = fgData.load('notes', {signal:canceled.signal}).catch(error => error.name);
            window.second = fgData.load('notes');
        }''')
        self.assertTrue(event.wait(5))
        self.page.evaluate('canceled.abort()')
        self.assertEqual(self.page.evaluate('async () => await first'), 'AbortError')
        gate.set()
        self.assertEqual(self.page.evaluate('async () => await second'), dict(text='中文🙂'))
        self.assertEqual(self.count('notes.json'), 1)
        self.assertEqual(self.page.evaluate("fgData.read('notes')"), dict(text='中文🙂'))

    def test_escape_and_compare_cancel_fetch_without_late_activation(self):
        self.publish()
        gate, event = self.gate('tlData.json')
        self.page.evaluate("() => { window.pendingView = fgSwitchView('timeline'); }")
        self.assertTrue(event.wait(5))
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.evaluate('async () => await pendingView === null'))
        self.page.evaluate("() => { window.pendingView = fgSwitchView('timeline'); }")
        self.page.evaluate("async () => await fgSwitchView('compare')")
        self.assertTrue(self.page.evaluate('async () => await pendingView === null'))
        gate.set()
        self.page.wait_for_timeout(80)
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'compare')
        self.assertEqual(self.page.locator('#timelineView canvas').count(), 0)
        self.assertEqual(self.console_errors, [])

    def test_failed_fetch_has_safe_retry_and_preserves_manifest_cache(self):
        self.publish()
        response = self.server.responses.pop('/report/data/tlData.json')
        self.assertFalse(self.page.evaluate("async () => !!(await fgSwitchView('timeline'))"))
        self.assertIn('HTTP 404', self.page.locator('.fg-loading-status[role=alert]').inner_text())
        self.assertFalse(self.page.locator('#timelineView').get_attribute('aria-busy'))
        self.server.responses['/report/data/tlData.json'] = response
        self.page.get_by_role('button', name='Retry', exact=True).focus()
        self.page.keyboard.press('Enter')
        self.page.wait_for_selector('.tl-toolbar')
        self.assertEqual(self.count('tlData.json'), 2)
        self.assertEqual(self.count('manifest.json'), 1)

    def test_compact_error_keeps_back_and_retry_keyboard_accessible(self):
        self.page.set_viewport_size(dict(width=360, height=320))
        self.publish()
        self.server.responses.pop('/report/data/tlData.json')
        self.assertFalse(self.page.evaluate("async () => !!(await fgSwitchView('timeline'))"))
        for label in ['Back to Text', 'Retry']:
            button = self.page.get_by_role('button', name=label, exact=True)
            button.focus()
            bounds = button.bounding_box()
            self.assertGreaterEqual(bounds['x'], 0)
            self.assertLessEqual(bounds['x'] + bounds['width'], 360)
            self.assertGreaterEqual(bounds['y'], 0)
            self.assertLessEqual(bounds['y'] + bounds['height'], 320)
        self.page.get_by_role('button', name='Back to Text', exact=True).focus()
        self.page.keyboard.press('Enter')
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
        self.assertTrue(self.page.evaluate("document.activeElement.matches('.view-tab[data-view=text]')"))

    def test_size_checksum_utf8_and_json_errors_never_cache_data(self):
        for kind, body in [('size', b'x'), ('oversize', b'x' * 65536), ('checksum', b'[]'), ('utf8', b'\xff'), ('json', b'{')]:
            with self.subTest(kind=kind):
                self.payloads['notes'] = body if kind in ['utf8', 'json'] else encode(dict(text='中文🙂'))
                self.publish()
                if kind in ['size', 'oversize', 'checksum']:
                    if kind == 'checksum':
                        body = self.payloads['notes'].replace(b'\xf0', b'\xf1', 1)
                    self.server.responses['/report/data/notes.json'] = (200, body, None)
                errors = self.page.evaluate('''async () => {
                    const errors = [];
                    for (let attempt = 0; attempt < 2; attempt++) {
                        try { await fgData.load('notes'); } catch (error) { errors.push(error.message); }
                    }
                    return errors;
                }''')
                self.assertEqual(len(errors), 2)
                if kind in ['size', 'oversize', 'checksum']:
                    self.assertIn(('size' if kind == 'oversize' else kind) + ' mismatch', errors[0])
                self.assertFalse(self.page.evaluate("fgData.ready('notes')"))
                self.assertEqual(self.count('notes.json'), 2)
                self.server.counts.clear()

    def test_manifest_identity_and_checksum_are_required(self):
        self.publish(manifest_change=lambda value: value.update(report_id='0' * 32))
        self.assertFalse(self.page.evaluate("async () => !!(await fgSwitchView('timeline'))"))
        self.assertIn('identity or version mismatch', self.page.locator('.fg-loading-status').inner_text())
        self.assertEqual(self.count('tlData.json'), 0)
        self.publish(descriptor_change=lambda value: value['manifest'].update(sha256='0' * 64))
        self.assertFalse(self.page.evaluate("async () => !!(await fgSwitchView('compare'))"))
        self.assertIn('checksum mismatch', self.page.locator('#compareView .fg-loading-status').inner_text())
        self.assertEqual(self.count('cmpData.json'), 0)

    def test_resource_paths_and_descriptor_types_fail_before_asset_fetch(self):
        for filename in ['../escape.json', '/outside.json', '//example.org/data', 'https://example.org/data',
                         'nested/../escape.json', 'file.json?key=value', '%2e%2e/escape.json', 'dir\\file.json']:
            with self.subTest(filename=filename):
                self.publish(manifest_change=lambda value: value['resources']['notes'].update(file=filename))
                message = self.page.evaluate("async () => { try { await fgData.load('notes'); } catch (error) { return error.message; } }")
                self.assertIn('inside the report directory', message)
                self.assertEqual(self.count('notes.json'), 0)
        for invalid in [True, -1, 1.5, '10']:
            with self.subTest(bytes=invalid):
                self.publish(descriptor_change=lambda value: value['manifest'].update(bytes=invalid))
                message = self.page.evaluate("async () => { try { await fgData.load('notes'); } catch (error) { return error.message; } }")
                self.assertIn('Invalid report resource descriptor', message)

    def test_text_resources_missing_names_and_preaborted_requests(self):
        self.publish()
        self.assertEqual(self.page.evaluate("async () => await fgData.load('raw')"), '原始文本🙂')
        self.assertEqual(self.page.evaluate("fgData.read('raw')"), '原始文本🙂')
        message = self.page.evaluate("async () => { try { await fgData.load('unknown'); } catch (error) { return error.message; } }")
        self.assertEqual(message, 'Report resource not found: unknown')
        self.assertEqual(self.page.evaluate('''async () => {
            const controller = new AbortController(); controller.abort();
            try { await fgData.load('notes', {signal:controller.signal}); } catch (error) { return error.name; }
        }'''), 'AbortError')
        self.assertEqual(self.count('notes.json'), 0)

    def test_file_opening_shows_http_instructions_without_fetching(self):
        self.publish()
        path = Path(self.kernel.dir) / 'external.html'
        path.write_text(self.external_html, encoding='utf-8')
        self.page.goto(path.as_uri(), wait_until='load')
        self.assertFalse(self.page.evaluate("async () => !!(await fgSwitchView('timeline'))"))
        self.assertIn('Sidecar data needs HTTP', self.page.locator('.fg-loading-status').inner_text())
        self.assertEqual(self.count('manifest.json'), 0)


if __name__ == '__main__':
    unittest.main()
