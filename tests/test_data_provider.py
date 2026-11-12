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

    def publish(self, manifest_change=None, descriptor_change=None, embedded=False, source_catalog=None, text_catalog=None):
        resources = {}
        for name, body in self.payloads.items():
            text = re.fullmatch(r'fgText[0-9]+', name)
            filename = name + ('.html' if text else '.txt' if name == 'raw' else '.json')
            resources[name] = resource(filename, body, 'text' if text or name == 'raw' else 'json')
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
        if text_catalog is not None:
            report = report.replace('</head>', '<script type="application/json" id="fgTextChunks">' +
                                    encode(text_catalog).decode() + '</script></head>', 1)
        if source_catalog is not None:
            report = re.sub(r'    <script type="application/json" id="fgSrc">.*?</script>\n', '', report, flags=re.S)
            report = report.replace('</head>', '<script type="application/json" id="fgSourceChunks">' +
                                    encode(source_catalog).decode() + '</script></head>', 1)
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

    def observe_checksum_workers(self, hold=False):
        self.page.evaluate('''hold => {
            const NativeWorker = Worker;
            window.checksumFacts = {created:0, terminated:0, transfers:[]};
            window.checksumReplies = [];
            window.holdChecksumReplies = hold;
            window.Worker = class extends NativeWorker {
                constructor(...parameters) {
                    super(...parameters);
                    checksumFacts.created++;
                    window.latestChecksumWorker = this;
                    Object.defineProperty(this, 'onmessage', {set:handler => {
                        this.addEventListener('message', event => {
                            if (event.data.buffer && holdChecksumReplies)
                                checksumReplies.push({resume:() => handler(event), data:event.data});
                            else handler(event);
                        });
                    }});
                }
                postMessage(data, transfers) {
                    const bytes = data.buffer.byteLength;
                    super.postMessage(data, transfers);
                    checksumFacts.transfers.push({bytes, detached:data.buffer.byteLength === 0, buffers:transfers.length});
                }
                terminate() { checksumFacts.terminated++; super.terminate(); }
            };
        }''', hold)

    def test_large_text_and_json_share_one_worker_and_transfer_original_buffers(self):
        text = 'call 中文🙂 x=0xff\n' * 70000
        self.payloads.update(raw=text.encode(), notes=encode(dict(text=text)))
        self.publish()
        self.observe_checksum_workers()
        self.page.evaluate('''() => {
            const digest = crypto.subtle.digest.bind(crypto.subtle);
            crypto.subtle.digest = (algorithm, bytes) => {
                if (bytes.byteLength >= 1024 * 1024) throw new Error('Large checksum ran on the main thread');
                return digest(algorithm, bytes);
            };
        }''')
        result = self.page.evaluate('''async () => {
            const [text, json] = await Promise.all([fgData.load('raw'), fgData.load('notes')]);
            await fgData.load('raw');
            return {equal:text === json.text, length:text.length, facts:checksumFacts};
        }''')
        self.assertTrue(result['equal'])
        self.assertEqual(result['length'], len(text.encode('utf-16-le')) // 2)
        self.assertEqual(result['facts']['created'], 1)
        self.assertEqual(result['facts']['terminated'], 0)
        self.assertEqual(len(result['facts']['transfers']), 2)
        self.assertTrue(all(item['detached'] and item['buffers'] == 1 for item in result['facts']['transfers']))
        self.assertEqual(self.count('raw.txt'), 1)
        self.assertEqual(self.count('notes.json'), 1)
        self.assertEqual(self.console_errors, [])

    def test_checksum_worker_threshold_preserves_small_resource_verification(self):
        for size in [1024 * 1024 - 1, 1024 * 1024, 1024 * 1024 + 1]:
            with self.subTest(size=size):
                self.payloads['raw'] = b'x' * size
                self.publish()
                self.observe_checksum_workers()
                result = self.page.evaluate('''async () => {
                    const text = await fgData.load('raw');
                    return {length:text.length, workers:checksumFacts.created};
                }''')
                self.assertEqual(result, dict(length=size, workers=int(size >= 1024 * 1024)))

    def test_large_embedded_data_keeps_its_offline_worker_free_path(self):
        self.publish(embedded=True)
        self.page.evaluate('''() => {
            Object.defineProperty(window, 'Worker', {get() { throw new Error('Embedded data tried to start a Worker'); }});
            const embedded = document.createElement('script');
            embedded.type = 'application/json';
            embedded.id = 'notes';
            embedded.textContent = JSON.stringify({text:'x'.repeat(1024 * 1024)});
            document.body.appendChild(embedded);
        }''')
        self.assertEqual(self.page.evaluate("async () => (await fgData.load('notes')).text.length"), 1024 * 1024)
        self.assertTrue(self.page.evaluate("async () => !!(await fgSwitchView('timeline'))"))
        self.assertTrue(self.page.evaluate("async () => !!(await fgSwitchView('compare'))"))
        self.assertEqual(self.count('manifest.json'), 0)
        self.assertEqual(self.count('notes.json'), 0)

    def test_checksum_worker_accepts_array_buffer_responses_without_streaming(self):
        self.payloads['raw'] = b'x' * (1024 * 1024)
        self.publish()
        self.observe_checksum_workers()
        self.page.evaluate('''() => {
            const fetchResource = fetch;
            window.fetch = async (...parameters) => {
                const response = await fetchResource(...parameters);
                return {ok:response.ok, status:response.status, body:null, arrayBuffer:() => response.arrayBuffer()};
            };
        }''')
        self.assertEqual(self.page.evaluate("async () => (await fgData.load('raw')).length"), len(self.payloads['raw']))
        self.assertEqual(self.page.evaluate('checksumFacts.transfers'), [dict(bytes=len(self.payloads['raw']), detached=True, buffers=1)])

    def test_unavailable_or_unconstructable_worker_falls_back_without_skipping_checksums(self):
        for mode in ['missing', 'constructor']:
            with self.subTest(mode=mode):
                self.payloads['raw'] = b'x' * (1024 * 1024)
                self.publish()
                self.page.evaluate('''mode => {
                    window.Worker = mode === 'missing' ? undefined : class {
                        constructor() { throw new Error('Unavailable'); }
                    };
                }''', mode)
                self.server.responses['/report/data/raw.txt'] = (200, b'y' * len(self.payloads['raw']), None)
                result = self.page.evaluate("async () => { try { await fgData.load('raw'); } catch (error) { return error.message; } }")
                self.assertIn('checksum mismatch', result)
                self.assertFalse(self.page.evaluate("fgData.ready('raw')"))
                self.server.responses['/report/data/raw.txt'] = (200, self.payloads['raw'], None)
                self.assertEqual(self.page.evaluate("async () => (await fgData.load('raw')).length"), len(self.payloads['raw']))

    def test_worker_boot_failure_uses_verified_main_thread_fallback(self):
        self.payloads['raw'] = b'x' * (1024 * 1024)
        self.publish()
        self.page.evaluate('''() => {
            window.failedWorkers = 0;
            window.Worker = class {
                constructor() { queueMicrotask(() => this.onmessage({data:{ready:false}})); }
                terminate() { failedWorkers++; }
            };
        }''')
        self.assertEqual(self.page.evaluate("async () => (await fgData.load('raw')).length"), len(self.payloads['raw']))
        self.assertEqual(self.page.evaluate('failedWorkers'), 1)
        self.assertEqual(self.console_errors, [])

    def test_large_worker_checked_bytes_still_require_valid_utf8_and_json(self):
        for name, body in [('raw', b'x' * (1024 * 1024) + b'\xff'),
                           ('notes', b'{' + b' ' * (1024 * 1024) + b'invalid}')]:
            with self.subTest(name=name):
                self.payloads[name] = body
                self.publish()
                self.observe_checksum_workers()
                result = self.page.evaluate('''async name => {
                    try { await fgData.load(name); return {accepted:true}; }
                    catch (error) { return {accepted:false, cached:fgData.ready(name), workers:checksumFacts.created}; }
                }''', name)
                self.assertEqual(result, dict(accepted=False, cached=False, workers=1))

    def test_never_ready_checksum_worker_times_out_and_uses_native_verification(self):
        self.payloads['raw'] = b'x' * (1024 * 1024)
        self.publish()
        self.page.evaluate('''() => {
            const schedule = setTimeout;
            window.setTimeout = (callback, delay, ...parameters) => schedule(callback, delay === 5000 ? 0 : delay, ...parameters);
            window.failedWorkers = 0;
            window.Worker = class {
                terminate() { failedWorkers++; }
            };
        }''')
        self.assertEqual(self.page.evaluate("async () => (await fgData.load('raw')).length"), len(self.payloads['raw']))
        self.assertEqual(self.page.evaluate('failedWorkers'), 1)

    def test_checksum_worker_error_reply_terminates_the_worker_before_native_retry(self):
        self.payloads['raw'] = b'x' * (1024 * 1024)
        self.publish()
        self.observe_checksum_workers(hold=True)
        self.page.evaluate("() => { window.failedChecksum = fgData.load('raw').catch(error => error.message); }")
        self.page.wait_for_function('() => checksumReplies.length === 1')
        self.page.evaluate("() => { checksumReplies[0].data.error = 'Crypto failed'; checksumReplies.shift().resume(); }")
        self.assertIn('checksum worker failed: Crypto failed', self.page.evaluate('failedChecksum'))
        self.assertFalse(self.page.evaluate("fgData.ready('raw')"))
        self.assertEqual(self.page.evaluate('checksumFacts.terminated'), 1)
        self.assertEqual(self.page.evaluate("async () => (await fgData.load('raw')).length"), len(self.payloads['raw']))
        self.assertEqual(self.page.evaluate('checksumFacts.created'), 1)

    def test_canceling_one_subscriber_keeps_a_shared_checksum_worker_job_alive(self):
        self.payloads['raw'] = b'x' * (1024 * 1024)
        self.publish()
        self.observe_checksum_workers(hold=True)
        self.page.evaluate('''() => {
            window.firstConsumer = new AbortController();
            window.firstChecksum = fgData.load('raw', {signal:firstConsumer.signal}).catch(error => error.name);
            window.secondChecksum = fgData.load('raw');
        }''')
        self.page.wait_for_function('() => checksumReplies.length === 1')
        self.page.evaluate('firstConsumer.abort()')
        self.assertEqual(self.page.evaluate('firstChecksum'), 'AbortError')
        self.assertEqual(self.page.evaluate('checksumFacts.terminated'), 0)
        self.page.evaluate('checksumReplies.shift().resume()')
        self.assertEqual(self.page.evaluate('async () => (await secondChecksum).length'), len(self.payloads['raw']))
        self.assertTrue(self.page.evaluate("fgData.ready('raw')"))
        self.assertEqual(self.count('raw.txt'), 1)

    def test_last_checksum_consumer_abort_terminates_worker_and_ignores_late_reply(self):
        self.payloads['raw'] = b'x' * (1024 * 1024)
        self.publish()
        self.observe_checksum_workers(hold=True)
        self.page.evaluate('''() => {
            window.checksumConsumer = new AbortController();
            window.oldChecksum = fgData.load('raw', {signal:checksumConsumer.signal}).catch(error => error.name);
        }''')
        self.page.wait_for_function('() => checksumReplies.length === 1')
        self.page.evaluate('checksumConsumer.abort()')
        self.assertEqual(self.page.evaluate('oldChecksum'), 'AbortError')
        self.assertEqual(self.page.evaluate('checksumFacts.terminated'), 1)
        self.assertFalse(self.page.evaluate("fgData.ready('raw')"))
        self.page.evaluate('holdChecksumReplies = false')
        self.assertEqual(self.page.evaluate("async () => (await fgData.load('raw')).length"), len(self.payloads['raw']))
        self.page.evaluate('checksumReplies.shift().resume()')
        self.assertEqual(self.page.evaluate('checksumFacts.created'), 2)
        self.assertTrue(self.page.evaluate("fgData.ready('raw')"))

    def test_canceling_one_resource_preserves_another_checksum_job_on_the_same_worker(self):
        text = 'x' * (1024 * 1024)
        self.payloads.update(raw=text.encode(), notes=encode(dict(text=text)))
        self.publish()
        self.observe_checksum_workers(hold=True)
        self.page.evaluate('''() => {
            window.checksumConsumer = new AbortController();
            window.firstChecksum = fgData.load('raw', {signal:checksumConsumer.signal}).catch(error => error.name);
            window.otherChecksum = fgData.load('notes');
        }''')
        self.page.wait_for_function('() => checksumReplies.length === 2')
        self.page.evaluate('checksumConsumer.abort()')
        self.assertEqual(self.page.evaluate('firstChecksum'), 'AbortError')
        self.assertEqual(self.page.evaluate('checksumFacts.terminated'), 0)
        self.page.evaluate('checksumReplies.splice(0).forEach(reply => reply.resume())')
        self.assertEqual(self.page.evaluate('async () => (await otherChecksum).text.length'), len(text))
        self.assertTrue(self.page.evaluate("fgData.ready('notes') && !fgData.ready('raw')"))

    def test_failed_running_worker_cannot_cache_data_and_retry_uses_native_verification(self):
        self.payloads['raw'] = b'x' * (1024 * 1024)
        self.publish()
        self.observe_checksum_workers(hold=True)
        self.page.evaluate("() => { window.failedChecksum = fgData.load('raw').catch(error => error.message); }")
        self.page.wait_for_function('() => checksumReplies.length === 1')
        self.page.evaluate('latestChecksumWorker.onerror(new ErrorEvent("error", {cancelable:true}))')
        self.assertIn('checksum worker failed', self.page.evaluate('failedChecksum'))
        self.assertFalse(self.page.evaluate("fgData.ready('raw')"))
        self.assertEqual(self.page.evaluate("async () => (await fgData.load('raw')).length"), len(self.payloads['raw']))
        self.assertEqual(self.page.evaluate('checksumFacts.created'), 1)
        self.assertEqual(self.page.evaluate('checksumFacts.terminated'), 1)
        self.page.evaluate('checksumReplies.shift().resume()')

    def test_malformed_and_incorrect_worker_digests_never_cache_unverified_bytes(self):
        for size, message in [(31, 'Invalid report checksum result'), (32, 'checksum mismatch')]:
            with self.subTest(size=size):
                self.payloads['raw'] = b'x' * (1024 * 1024)
                self.publish()
                self.observe_checksum_workers(hold=True)
                self.page.evaluate("() => { window.invalidChecksum = fgData.load('raw').catch(error => error.message); }")
                self.page.wait_for_function('() => checksumReplies.length === 1')
                self.page.evaluate('size => { checksumReplies[0].data.digest = new ArrayBuffer(size); checksumReplies.shift().resume(); }', size)
                self.assertIn(message, self.page.evaluate('invalidChecksum'))
                self.assertFalse(self.page.evaluate("fgData.ready('raw')"))
                self.page.evaluate('holdChecksumReplies = false')
                self.assertEqual(self.page.evaluate("async () => (await fgData.load('raw')).length"), len(self.payloads['raw']))

    def test_text_chunks_load_by_original_row_ordinal_without_mounting_or_fetching_other_chunks(self):
        self.payloads.update(fgText0='first 中文'.encode(), fgText4096='second 🙂'.encode())
        self.publish(text_catalog=dict(version=1, count=4203, chunk_size=4096, paint_rows=512))
        result = self.page.evaluate('''async () => {
            const second = await fgData.loadTextChunk(4202);
            const repeated = await fgData.loadTextChunk(4096);
            return {second, repeated, first:fgData.ready('fgText0'), secondReady:fgData.ready('fgText4096')};
        }''')
        self.assertEqual(result, dict(second='second 🙂', repeated='second 🙂', first=False, secondReady=True))
        self.assertEqual(self.count('fgText4096.html'), 1)
        self.assertEqual(self.count('fgText0.html'), 0)
        self.assertEqual(self.count('text-index.json'), 0)

    def test_text_paint_blocks_select_exact_html_and_keep_partial_final_block_and_unicode(self):
        marker = '<div class="text-chunk" data-chunk-size="512">'
        blocks = [marker + '<div>块🙂' + str(index) + '</div></div>' for index in range(8)]
        last = marker + '<div>最后一个块</div></div>'
        self.payloads.update(fgText0=''.join(blocks).encode(), fgText4096=last.encode())
        self.publish(text_catalog=dict(version=1, count=4203, chunk_size=4096, paint_rows=512))
        result = self.page.evaluate('''async () => {
            const rows = [4095,512,511,0,4202];
            const blocks = [];
            for (const row of rows) blocks.push(await fgData.loadTextBlock(row));
            return blocks;
        }''')
        self.assertEqual(result, [dict(first=3584, count=512, html=blocks[7]),
                                  dict(first=512, count=512, html=blocks[1]),
                                  dict(first=0, count=512, html=blocks[0]),
                                  dict(first=0, count=512, html=blocks[0]),
                                  dict(first=4096, count=107, html=last)])
        self.assertEqual(self.count('fgText0.html'), 1)
        self.assertEqual(self.count('fgText4096.html'), 1)
        self.assertEqual(self.count('text-index.json'), 0)

    def test_text_paint_block_validation_rejects_wrong_boundaries_counts_and_evicted_failures(self):
        marker = '<div class="text-chunk" data-chunk-size="512">'
        good = marker + '<div>完整</div></div>'
        invalid = ['', 'prefix' + good, good + good, good[:-6], good.replace('512', '256')]
        for value in invalid:
            with self.subTest(value=value):
                self.payloads['fgText0'] = value.encode()
                self.publish(text_catalog=dict(version=1, count=10, chunk_size=4096, paint_rows=512))
                result = self.page.evaluate('''async () => {
                    try { await fgData.loadTextBlock(0); return 'accepted'; }
                    catch (error) { return error.message; }
                }''')
                self.assertIn('Text paint block', result)
                self.assertFalse(self.page.evaluate('fgData.ready("fgText0")'))

    def test_text_paint_block_cancellation_is_independent_even_inside_one_network_chunk(self):
        marker = '<div class="text-chunk" data-chunk-size="512">'
        self.payloads['fgText0'] = ((marker + '<div>块</div></div>') * 8).encode()
        self.publish(text_catalog=dict(version=1, count=4096, chunk_size=4096, paint_rows=512))
        gate, event = self.gate('fgText0.html')
        self.page.evaluate('''() => {
            window.blockController = new AbortController();
            window.firstBlock = fgData.loadTextBlock(0, {signal:blockController.signal}).catch(error => error.name);
            window.secondBlock = fgData.loadTextBlock(4095);
        }''')
        self.assertTrue(event.wait(5))
        self.page.evaluate('blockController.abort()')
        self.assertEqual(self.page.evaluate('firstBlock'), 'AbortError')
        gate.set()
        self.assertEqual(self.page.evaluate('async () => (await secondBlock).first'), 3584)
        self.assertEqual(self.count('fgText0.html'), 1)

    def test_text_chunk_lru_is_separate_from_sources_and_other_data_and_refreshes_real_reads(self):
        self.payloads.update({'fgText' + str(index): str(index).encode() for index in range(6)})
        self.payloads['fgSrc0'] = encode(['source'])
        self.publish(text_catalog=dict(version=1, count=6, chunk_size=1, paint_rows=1),
                     source_catalog=dict(version=1, count=1, chunk_size=1))
        result = self.page.evaluate('''async () => {
            const notes = await fgData.load('notes');
            await fgData.loadSource(0);
            for (let row = 0; row < 4; row++) await fgData.loadTextChunk(row);
            fgData.read('fgText0');
            fgData.ready('fgText1');
            await fgData.loadTextChunk(4);
            const ready = Array.from({length:5}, (unused, row) => fgData.ready('fgText' + row));
            fgData.releaseTextChunks();
            return {ready, released:Array.from({length:5}, (unused, row) => fgData.ready('fgText' + row)),
                source:fgData.readSource(0), notes:fgData.read('notes') === notes};
        }''')
        self.assertEqual(result, dict(ready=[True, False, True, True, True], released=[False] * 5,
                                      source='source', notes=True))

    def test_text_chunk_cache_limits_verified_bytes_and_does_not_retain_oversized_results(self):
        limit = 16 * 1024 * 1024
        self.payloads.update(fgText0=('中' * (3 * 1024 * 1024)).encode(),
                             fgText1=('🙂' * (2 * 1024 * 1024)).encode(),
                             fgText2=b'x' * (limit + 1), fgText3=b'y' * limit)
        self.publish(text_catalog=dict(version=1, count=4, chunk_size=1, paint_rows=1))
        result = self.page.evaluate('''async () => {
            await fgData.loadTextChunk(0); await fgData.loadTextChunk(1);
            const before = [fgData.ready('fgText0'), fgData.ready('fgText1')];
            const length = (await fgData.loadTextChunk(2)).length;
            const after = [fgData.ready('fgText1'), fgData.ready('fgText2')];
            await fgData.loadTextChunk(3);
            return {before, length, after, final:[fgData.ready('fgText1'), fgData.ready('fgText3')]};
        }''')
        self.assertEqual(result, dict(before=[False, True], length=limit + 1, after=[True, False], final=[False, True]))

    def test_invalid_text_catalogs_rows_and_misaligned_generic_resource_names_are_rejected(self):
        self.payloads.update(fgText0=b'first', fgText1=b'bad identity')
        catalog = dict(version=1, count=2, chunk_size=2, paint_rows=1)
        self.publish(text_catalog=catalog)
        messages = self.page.evaluate('''async () => {
            const messages = [];
            for (const row of [-1, 2, 0.5, true, '0', null, NaN, Infinity]) {
                try { await fgData.loadTextChunk(row); messages.push('accepted'); }
                catch (error) { messages.push(error.message); }
            }
            try { await fgData.load('fgText1'); } catch (error) { messages.push(error.message); }
            return messages;
        }''')
        self.assertTrue(all('Invalid Text row identity' in message for message in messages[:8]))
        self.assertIn('Invalid Text chunk identity', messages[-1])
        self.assertFalse(self.page.evaluate('fgData.ready("fgText1")'))
        for change in [dict(version=2), dict(count=-1), dict(chunk_size=0), dict(paint_rows=0),
                       dict(paint_rows=3), dict(count=True), dict(chunk_size=1.5)]:
            invalid = dict(catalog, **change)
            self.publish(text_catalog=invalid)
            message = self.page.evaluate('async () => { try { await fgData.loadTextChunk(0); } catch (error) { return error.message; } }')
            self.assertIn('Invalid Text chunk catalog', message)

    def test_text_chunk_format_and_integrity_failures_do_not_publish_cache_entries(self):
        self.payloads['fgText0'] = b'correct'
        catalog = dict(version=1, count=1, chunk_size=1, paint_rows=1)
        self.publish(text_catalog=catalog, manifest_change=lambda manifest: manifest['resources']['fgText0'].update(format='json'))
        message = self.page.evaluate('async () => { try { await fgData.loadTextChunk(0); } catch (error) { return error.message; } }')
        self.assertIn('must use text format', message)
        self.assertEqual(self.count('fgText0.html'), 0)
        self.publish(text_catalog=catalog)
        self.server.responses['/report/data/fgText0.html'] = (200, b'corrupt', None)
        message = self.page.evaluate('async () => { try { await fgData.loadTextChunk(0); } catch (error) { return error.message; } }')
        self.assertIn('checksum mismatch', message)
        self.assertFalse(self.page.evaluate('fgData.ready("fgText0")'))
        self.server.responses['/report/data/fgText0.html'] = (200, b'correct', None)
        self.assertEqual(self.page.evaluate('fgData.loadTextChunk(0)'), 'correct')

    def test_shared_text_chunk_subscribers_cancel_independently(self):
        self.payloads['fgText0'] = b'complete'
        self.publish(text_catalog=dict(version=1, count=4096, chunk_size=4096, paint_rows=512))
        gate, event = self.gate('fgText0.html')
        self.page.evaluate('''() => {
            window.textController = new AbortController();
            window.firstText = fgData.loadTextChunk(0, {signal:textController.signal}).catch(error => error.name);
            window.secondText = fgData.loadTextChunk(4095);
        }''')
        self.assertTrue(event.wait(5))
        self.page.evaluate('textController.abort()')
        self.assertEqual(self.page.evaluate('firstText'), 'AbortError')
        gate.set()
        self.assertEqual(self.page.evaluate('secondText'), 'complete')
        self.assertEqual(self.count('fgText0.html'), 1)
        self.assertTrue(self.page.evaluate('fgData.ready("fgText0")'))

    def test_text_catalog_cannot_silently_truncate_whole_chunks_or_skip_resource_groups(self):
        self.payloads.update(fgText0=b'first', fgText4096=b'second')
        for count in [0, 4096, 12288]:
            with self.subTest(count=count):
                self.publish(text_catalog=dict(version=1, count=count, chunk_size=4096, paint_rows=512))
                result = self.page.evaluate('''async count => {
                    try { await fgData.load(count ? 'fgText0' : 'fgText4096'); }
                    catch (error) { return error.message; }
                }''', count)
                self.assertTrue('do not match catalog' in result or 'Invalid Text row identity' in result, result)
                self.assertFalse(self.page.evaluate('fgData.ready("fgText0")'))

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

    def test_source_chunks_load_only_the_requested_group_and_share_cache(self):
        self.payloads.update(fgSrc0=encode(['first 中文🙂', 'second']), fgSrc2=encode(['third', 'fourth']))
        self.publish(source_catalog=dict(version=1, count=4, chunk_size=2))
        self.assertEqual(self.count('fgSrc0.json'), 0)
        self.assertEqual(self.count('fgSrc2.json'), 0)
        result = self.page.evaluate('''async () => {
            const initial = fgData.sourceReady(0);
            const values = await Promise.all([fgData.loadSource(0), fgData.loadSource(1), fgData.loadSource(0)]);
            return {initial, values, first:fgData.readSource(0), second:fgData.sourceReady(1), third:fgData.sourceReady(2)};
        }''')
        self.assertEqual(result, dict(initial=False, values=['first 中文🙂', 'second', 'first 中文🙂'],
                                      first='first 中文🙂', second=True, third=False))
        self.assertEqual(self.count('fgSrc0.json'), 1)
        self.assertEqual(self.count('fgSrc2.json'), 0)
        self.assertEqual(self.page.evaluate('async () => await fgData.loadSource(3)'), 'fourth')
        self.assertEqual(self.count('fgSrc2.json'), 1)

    def test_source_cache_evicts_old_chunks_without_evicting_other_report_data(self):
        self.payloads.update({'fgSrc' + str(index): encode(['source ' + str(index)]) for index in range(12)})
        self.publish(source_catalog=dict(version=1, count=12, chunk_size=1))
        result = self.page.evaluate('''async () => {
            const notes = await fgData.load('notes');
            const timeline = await fgData.load('tlData');
            const sources = [];
            for (let index = 0; index < 12; index++) sources.push(await fgData.loadSource(index));
            return {sources, ready:Array.from({length:12}, (unused, index) => fgData.sourceReady(index)),
                notes:fgData.read('notes') === notes, timeline:fgData.read('tlData') === timeline};
        }''')
        self.assertEqual(result, dict(sources=['source ' + str(index) for index in range(12)],
                                      ready=[False] * 4 + [True] * 8, notes=True, timeline=True))
        self.assertEqual(self.count('manifest.json'), 1)
        self.assertEqual(self.count('notes.json'), 1)
        self.assertEqual(self.count('tlData.json'), 1)

    def test_source_cache_refreshes_reads_and_loads_but_not_readiness_probes(self):
        self.payloads.update({'fgSrc' + str(index): encode(['source ' + str(index), 'next ' + str(index)])
                              for index in range(0, 18, 2)})
        for access in ['readSource', 'loadSource', 'read', 'load']:
            with self.subTest(access=access):
                self.publish(source_catalog=dict(version=1, count=18, chunk_size=2))
                result = self.page.evaluate('''async access => {
                    for (let index = 0; index < 16; index += 2) await fgData.loadSource(index);
                    await fgData[access](access.endsWith('Source') ? 1 : 'fgSrc0');
                    fgData.sourceReady(2);
                    fgData.ready('fgSrc2');
                    await fgData.loadSource(16);
                    return Array.from({length:9}, (unused, index) => fgData.sourceReady(index * 2));
                }''', access)
                self.assertEqual(result, [True, False] + [True] * 7)

    def test_source_cache_counts_verified_utf8_bytes_not_string_characters(self):
        self.payloads.update(fgSrc0=encode(['中' * (3 * 1024 * 1024)]),
                             fgSrc1=encode(['🙂' * (2 * 1024 * 1024)]))
        self.publish(source_catalog=dict(version=1, count=2, chunk_size=1))
        result = self.page.evaluate('''async () => {
            const first = (await fgData.loadSource(0)).length;
            const second = (await fgData.loadSource(1)).length;
            return {first, second, ready:[fgData.sourceReady(0), fgData.sourceReady(1)]};
        }''')
        self.assertEqual(result, dict(first=3 * 1024 * 1024, second=4 * 1024 * 1024, ready=[False, True]))

    def test_source_cache_accepts_exact_budget_and_delivers_oversized_chunks_without_retaining_them(self):
        limit = 16 * 1024 * 1024
        self.payloads.update(fgSrc0=encode(['x' * (limit - 4)]), fgSrc1=encode(['y' * (limit - 3)]))
        self.assertEqual(len(self.payloads['fgSrc0']), limit)
        self.assertEqual(len(self.payloads['fgSrc1']), limit + 1)
        self.publish(source_catalog=dict(version=1, count=2, chunk_size=1))
        result = self.page.evaluate('''async () => {
            const first = (await fgData.loadSource(0)).length;
            const second = (await fgData.loadSource(1)).length;
            const repeated = (await fgData.load('fgSrc1'))[0].length;
            return {first, second, repeated, ready:[fgData.sourceReady(0), fgData.sourceReady(1)]};
        }''')
        self.assertEqual(result, dict(first=limit - 4, second=limit - 3, repeated=limit - 3, ready=[True, False]))
        self.assertEqual(self.count('fgSrc0.json'), 1)
        self.assertEqual(self.count('fgSrc1.json'), 2)
        self.assertEqual(self.count('manifest.json'), 1)

    def test_releasing_a_source_chunk_returns_its_byte_budget(self):
        self.payloads.update(fgSrc0=encode(['中' * (3 * 1024 * 1024)]), fgSrc1=encode(['small']),
                             fgSrc2=encode(['新' * (3 * 1024 * 1024)]))
        self.publish(source_catalog=dict(version=1, count=3, chunk_size=1))
        result = self.page.evaluate('''async () => {
            await fgData.loadSource(0);
            await fgData.loadSource(1);
            fgData.release('fgSrc0');
            const length = (await fgData.loadSource(2)).length;
            return {length, ready:[fgData.sourceReady(0), fgData.sourceReady(1), fgData.sourceReady(2)]};
        }''')
        self.assertEqual(result, dict(length=3 * 1024 * 1024, ready=[False, True, True]))

    def test_embedded_and_legacy_source_pools_keep_their_existing_cache_behavior(self):
        self.payloads['fgSrc'] = encode(['legacy source'])
        self.payloads.update({'fgSrc' + str(index): encode(['chunk ' + str(index)]) for index in range(9)})
        self.publish(embedded=True)
        result = self.page.evaluate('''async () => {
            const embedded = fgData.read('fgSrc');
            const original = await fgData.loadSource(0);
            const preserved = original === embedded[0] && fgData.read('fgSrc') === embedded;
            document.getElementById('fgSrc').remove();
            fgData.release('fgSrc');
            const legacy = await fgData.loadSource(0);
            for (let index = 0; index < 9; index++) await fgData.load('fgSrc' + index);
            return {preserved, legacy, ready:fgData.sourceReady(0),
                chunks:Array.from({length:9}, (unused, index) => fgData.ready('fgSrc' + index))};
        }''')
        self.assertEqual(result, dict(preserved=True, legacy='legacy source', ready=True, chunks=[True] * 9))
        self.assertEqual(self.count('fgSrc.json'), 1)

    def test_released_and_invalid_source_chunks_do_not_consume_cache_slots(self):
        self.payloads.update({'fgSrc' + str(index): encode(['source ' + str(index)]) for index in range(11)})
        self.payloads['fgSrc0'] = encode([7])
        self.publish(source_catalog=dict(version=1, count=11, chunk_size=1))
        result = self.page.evaluate('''async () => {
            await fgData.loadSource(0).catch(() => {});
            for (let index = 1; index < 9; index++) await fgData.loadSource(index);
            const released = fgData.release('fgSrc4');
            const again = fgData.release('fgSrc4');
            await fgData.loadSource(9);
            const before = fgData.sourceReady(1);
            await fgData.loadSource(10);
            return {released, again, before,
                ready:Array.from({length:11}, (unused, index) => fgData.sourceReady(index))};
        }''')
        self.assertEqual(result, dict(released=True, again=False, before=True,
                                      ready=[False, False, True, True, False] + [True] * 6))

    def test_invalid_source_contents_do_not_displace_valid_cached_chunks(self):
        self.payloads.update({'fgSrc' + str(index): encode(['source ' + str(index)]) for index in range(8)})
        self.payloads['fgSrc8'] = encode([7])
        self.publish(source_catalog=dict(version=1, count=9, chunk_size=1))
        result = self.page.evaluate('''async () => {
            for (let index = 0; index < 8; index++) await fgData.loadSource(index);
            const error = await fgData.loadSource(8).catch(error => error.message);
            return {error, ready:Array.from({length:9}, (unused, index) => fgData.sourceReady(index))};
        }''')
        self.assertIn('Invalid source chunk contents', result['error'])
        self.assertEqual(result['ready'], [True] * 8 + [False])

    def test_direct_loads_cannot_cache_source_names_outside_catalog_chunk_boundaries(self):
        self.payloads.update(fgSrc0=encode(['first', 'second']), fgSrc1=encode(['misaligned']),
                             fgSrc9007199254740992=encode(['unsafe identity']))
        self.publish(source_catalog=dict(version=1, count=2, chunk_size=2))
        result = self.page.evaluate('''async () => {
            await fgData.loadSource(0);
            const errors = [];
            for (const name of ['fgSrc1', 'fgSrc9007199254740992']) {
                try { await fgData.load(name); } catch (error) { errors.push(error.message); }
            }
            return {errors, first:fgData.readSource(0),
                ready:['fgSrc0', 'fgSrc1', 'fgSrc9007199254740992'].map(name => fgData.ready(name))};
        }''')
        self.assertIn('Invalid source chunk identity', result['errors'][0])
        self.assertIn('Invalid source identity', result['errors'][1])
        self.assertEqual(result['first'], 'first')
        self.assertEqual(result['ready'], [True, False, False])

    def test_evicted_source_chunks_are_reverified_and_failed_reloads_preserve_other_entries(self):
        self.payloads.update({'fgSrc' + str(index): encode(['source ' + str(index)]) for index in range(9)})
        self.publish(source_catalog=dict(version=1, count=9, chunk_size=1))
        self.page.evaluate('''async () => {
            for (let index = 0; index < 9; index++) await fgData.loadSource(index);
        }''')
        self.server.responses['/report/data/fgSrc0.json'] = (200, encode(['broken 0']), None)
        error = self.page.evaluate('''async () => {
            try { await fgData.loadSource(0); } catch (error) { return error.message; }
        }''')
        self.assertIn('checksum mismatch', error)
        self.assertFalse(self.page.evaluate('fgData.sourceReady(0)'))
        self.assertTrue(self.page.evaluate('fgData.sourceReady(1)'))
        self.server.responses['/report/data/fgSrc0.json'] = (200, self.payloads['fgSrc0'], None)
        self.assertEqual(self.page.evaluate('async () => await fgData.loadSource(0)'), 'source 0')
        self.assertFalse(self.page.evaluate('fgData.sourceReady(1)'))
        self.assertEqual(self.count('fgSrc0.json'), 3)
        self.assertEqual(self.count('manifest.json'), 1)

    def test_concurrent_source_consumers_receive_values_even_when_other_completions_evict_their_chunk(self):
        self.payloads.update({'fgSrc' + str(index): encode(['source ' + str(index), 'source ' + str(index + 1)])
                              for index in range(0, 20, 2)})
        self.publish(source_catalog=dict(version=1, count=20, chunk_size=2))
        result = self.page.evaluate('''async () => {
            const values = await Promise.all(Array.from({length:20}, (unused, index) => fgData.loadSource(index)));
            const ready = Array.from({length:20}, (unused, index) => fgData.sourceReady(index));
            return {values, ready};
        }''')
        self.assertEqual(result['values'], ['source ' + str(index) for index in range(20)])
        self.assertEqual(sum(result['ready']), 16)
        self.assertEqual(result['ready'][::2], result['ready'][1::2])
        self.assertTrue(all(self.count('fgSrc' + str(index) + '.json') == 1 for index in range(0, 20, 2)))

    def test_canceled_source_loads_do_not_evict_or_reorder_cached_chunks(self):
        self.payloads.update({'fgSrc' + str(index): encode(['source ' + str(index)]) for index in range(9)})
        self.publish(source_catalog=dict(version=1, count=9, chunk_size=1))
        self.page.evaluate('''async () => {
            for (let index = 0; index < 8; index++) await fgData.loadSource(index);
        }''')
        gate, requested = self.gate('fgSrc8.json')
        self.page.evaluate('''() => {
            window.sourceController = new AbortController();
            window.pendingSource = fgData.loadSource(8, {signal:sourceController.signal}).catch(error => error.name);
        }''')
        self.assertTrue(requested.wait(10), 'source chunk was not requested')
        self.page.evaluate('sourceController.abort()')
        self.assertEqual(self.page.evaluate('async () => await pendingSource'), 'AbortError')
        gate.set()
        result = self.page.evaluate('''async () => {
            const canceled = await fgData.loadSource(0, {signal:sourceController.signal}).catch(error => error.name);
            const before = Array.from({length:9}, (unused, index) => fgData.sourceReady(index));
            const value = await fgData.loadSource(8);
            return {canceled, before, value, ready:fgData.sourceReady(0)};
        }''')
        self.assertEqual(result, dict(canceled='AbortError', before=[True] * 8 + [False], value='source 8', ready=False))
        self.assertEqual(self.count('fgSrc8.json'), 2)

    def test_source_eviction_keeps_rendered_panels_and_refetches_for_new_consumers(self):
        self.payloads.update({'fgSrc' + str(index): encode(['<div>source ' + str(index) + '</div>'])
                              for index in range(9)})
        self.publish(source_catalog=dict(version=1, count=9, chunk_size=1))
        result = self.page.evaluate('''async () => {
            const first = document.createElement('div');
            first.dataset.src = '0';
            document.body.appendChild(first);
            await fgFill(first);
            for (let index = 1; index < 9; index++) await fgData.loadSource(index);
            const evicted = !fgData.sourceReady(0);
            await fgFill(first);
            const unchanged = !fgData.sourceReady(0);
            const second = document.createElement('div');
            second.dataset.src = '0';
            document.body.appendChild(second);
            await fgFill(second);
            return {evicted, unchanged, first:first.textContent, second:second.textContent,
                ready:fgData.sourceReady(0), busy:second.hasAttribute('aria-busy')};
        }''')
        self.assertEqual(result, dict(evicted=True, unchanged=True, first='source 0', second='source 0', ready=True, busy=False))
        self.assertEqual(self.count('fgSrc0.json'), 2)

    def test_source_catalog_rejects_invalid_identities_without_fetching_chunks(self):
        self.payloads['fgSrc0'] = encode(['source'])
        for catalog, identity in [(dict(version=2, count=1, chunk_size=1), 0),
                                  (dict(version=1, count=-1, chunk_size=1), 0),
                                  (dict(version=1, count=1, chunk_size=0), 0),
                                  (dict(version=1, count=1, chunk_size=1), -1),
                                  (dict(version=1, count=1, chunk_size=1), 1),
                                  (dict(version=1, count=1, chunk_size=1), 0.5)]:
            with self.subTest(catalog=catalog, identity=identity):
                self.publish(source_catalog=catalog)
                error = self.page.evaluate('''async identity => {
                    try { await fgData.loadSource(identity); } catch (error) { return error.message; }
                }''', identity)
                self.assertIn('Invalid source', error)
                self.assertEqual(self.count('fgSrc0.json'), 0)

    def test_invalid_source_group_shapes_never_stay_cached(self):
        for contents in [[], ['one', 'extra'], [7], dict(source='not an array')]:
            with self.subTest(contents=contents):
                self.payloads['fgSrc0'] = encode(contents)
                self.publish(source_catalog=dict(version=1, count=1, chunk_size=1))
                errors = self.page.evaluate('''async () => {
                    const errors = [];
                    for (let attempt = 0; attempt < 2; attempt++) {
                        try { await fgData.loadSource(0); } catch (error) { errors.push(error.message); }
                    }
                    return {errors, ready:fgData.sourceReady(0)};
                }''')
                self.assertEqual(len(errors['errors']), 2)
                self.assertTrue(all('Invalid source chunk contents' in error for error in errors['errors']))
                self.assertFalse(errors['ready'])
                self.server.counts.clear()

    def test_source_chunk_checksum_failures_can_retry_without_reloading_manifest(self):
        original = encode(['source'])
        self.payloads['fgSrc0'] = original
        self.publish(source_catalog=dict(version=1, count=1, chunk_size=64))
        self.server.responses['/report/data/fgSrc0.json'] = (200, original.replace(b'source', b'broken'), None)
        error = self.page.evaluate('''async () => {
            try { await fgData.loadSource(0); } catch (error) { return error.message; }
        }''')
        self.assertIn('checksum mismatch', error)
        self.assertFalse(self.page.evaluate('fgData.sourceReady(0)'))
        self.server.responses['/report/data/fgSrc0.json'] = (200, original, None)
        self.assertEqual(self.page.evaluate('async () => await fgData.loadSource(0)'), 'source')
        self.assertEqual(self.count('fgSrc0.json'), 2)
        self.assertEqual(self.count('manifest.json'), 1)

    def test_canceling_one_source_consumer_preserves_another_in_same_group(self):
        self.payloads['fgSrc0'] = encode(['first', 'second'])
        self.publish(source_catalog=dict(version=1, count=2, chunk_size=2))
        gate, requested = self.gate('fgSrc0.json')
        self.page.evaluate('''() => {
            window.sourceController = new AbortController();
            window.firstSource = fgData.loadSource(0, {signal:sourceController.signal}).catch(error => error.name);
            window.secondSource = fgData.loadSource(1);
        }''')
        self.assertTrue(requested.wait(10), 'source chunk was not requested')
        self.page.evaluate('sourceController.abort()')
        self.assertEqual(self.page.evaluate('async () => await firstSource'), 'AbortError')
        gate.set()
        self.assertEqual(self.page.evaluate('async () => await secondSource'), 'second')
        self.assertEqual(self.count('fgSrc0.json'), 1)
        self.assertTrue(self.page.evaluate('fgData.sourceReady(0)'))

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
        self.assertIn('fgview REPORT_DIRECTORY', self.page.locator('.fg-loading-status').inner_text())
        self.assertEqual(self.count('manifest.json'), 0)


if __name__ == '__main__':
    unittest.main()
