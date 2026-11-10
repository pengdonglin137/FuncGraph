import contextlib
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit

import helpers
from fglib import viewer

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


SUPPORTED = os.open in os.supports_dir_fd and all(hasattr(os, name) for name in ['O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK'])


@unittest.skipUnless(SUPPORTED, 'directory-relative no-follow access is unavailable')
class ViewerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fgviewer_')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / 'report'
        self.directory.mkdir()
        self.html = b'<!doctype html><title>Report</title><p>trace</p>'
        (self.directory / 'index.html').write_bytes(self.html)
        self.secret = Path(self.temporary.name) / 'secret.txt'
        self.secret.write_text('private', encoding='utf-8')

    def start(self, path=None):
        server = viewer.create_server(path or self.directory)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.addCleanup(stop)
        self.server = server
        return server

    def request(self, path=None, method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path or urlsplit(self.server.url).path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_directory_entry_and_nested_resources_are_read_only(self):
        (self.directory / 'data').mkdir()
        (self.directory / 'data' / 'timeline.json').write_text('{"n":1}', encoding='utf-8')
        server = self.start()
        self.assertEqual(server.server_address[0], '127.0.0.1')
        self.assertGreaterEqual(len(server.token), 32)
        self.assertEqual(self.request()[::2], (200, self.html))
        status, headers, body = self.request('/' + server.token + '/data/timeline.json')
        self.assertEqual((status, body), (200, b'{"n":1}'))
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(self.request('/' + server.token + '/')[::2], (200, self.html))
        self.assertEqual(self.request('/' + server.token + '/data/')[0], 404)

    def test_single_file_does_not_serve_siblings_and_handles_unicode_names(self):
        entry = self.directory / '报告 trace.html'
        entry.write_bytes(self.html)
        server = self.start(entry)
        self.assertIn('%20', server.url)
        self.assertEqual(self.request()[::2], (200, self.html))
        self.assertEqual(self.request('/' + server.token + '/index.html')[0], 404)
        self.assertEqual(self.request('/' + server.token + '/secret.txt')[0], 404)

    def test_missing_files_and_hidden_paths_do_not_leak_directory_contents(self):
        (self.directory / '.git').mkdir()
        (self.directory / '.git' / 'config').write_text('private repository', encoding='utf-8')
        self.start()
        for relative in ['missing.json', '.git/config', '../secret.txt', '%2e%2e/secret.txt',
                         'data/%2e%2e/%2e%2e/secret.txt', '%2Fetc/passwd', '%5csecret.txt',
                         'index.html%00', '%FF', 'data//index.html']:
            with self.subTest(relative=relative):
                status, headers, body = self.request('/' + self.server.token + '/' + relative)
                self.assertEqual(status, 404)
                self.assertNotIn(str(self.directory).encode(), body)
                self.assertNotIn(b'private', body)

    def test_token_and_local_host_are_required(self):
        self.start()
        for path in ['/', '/index.html', '/wrong/index.html']:
            self.assertEqual(self.request(path)[0], 404)
        for host in ['attacker.invalid', '127.0.0.1.attacker.invalid', 'localhost.attacker.invalid']:
            self.assertEqual(self.request(headers={'Host': host})[0], 403)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.putrequest('GET', urlsplit(self.server.url).path, skip_host=True)
            connection.putheader('Host', '127.0.0.1:' + str(self.server.server_port))
            connection.putheader('Host', 'attacker.invalid')
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 403)
        finally:
            connection.close()

    def test_cross_origin_requests_are_rejected_without_cors(self):
        self.start()
        origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.assertEqual(self.request(headers={'Origin': origin})[0], 200)
        for origin in ['null', 'https://attacker.invalid', origin + '.attacker.invalid']:
            status, headers, body = self.request(headers={'Origin': origin})
            self.assertEqual(status, 403)
            self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_head_and_security_headers(self):
        self.start()
        status, headers, body = self.request(method='HEAD')
        self.assertEqual((status, body), (200, b''))
        self.assertEqual(int(headers['Content-Length']), len(self.html))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertIn("connect-src 'self'", headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertNotIn("'unsafe-eval'", headers['Content-Security-Policy'])
        self.assertNotIn('Python', headers['Server'])

    def test_write_methods_are_rejected(self):
        self.start()
        for method in ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']:
            status, headers, body = self.request(method=method)
            self.assertEqual(status, 405)
            self.assertEqual(headers['Allow'], 'GET, HEAD')
            self.assertEqual(body, b'')
        self.assertEqual((self.directory / 'index.html').read_bytes(), self.html)

    def test_symlink_files_and_directories_are_not_followed(self):
        (self.directory / 'linked.txt').symlink_to(self.secret)
        (self.directory / 'linked').symlink_to(self.secret.parent, target_is_directory=True)
        self.start()
        for relative in ['linked.txt', 'linked/secret.txt']:
            self.assertEqual(self.request('/' + self.server.token + '/' + relative)[0], 404)

    def test_report_root_stays_pinned_after_directory_replacement(self):
        self.start()
        self.directory.rename(self.directory.with_name('old-report'))
        self.directory.symlink_to(self.secret.parent, target_is_directory=True)
        self.assertEqual(self.request()[::2], (200, self.html))
        self.assertEqual(self.request('/' + self.server.token + '/secret.txt')[0], 404)

    def test_parent_replacement_during_open_cannot_redirect_to_a_symlink(self):
        data = self.directory / 'data'
        data.mkdir()
        (data / 'secret.txt').write_text('report resource', encoding='utf-8')
        files = viewer.ReportFiles(self.directory)
        self.addCleanup(files.close)
        original_open = os.open

        def replace_parent(path, flags, *arguments, **keywords):
            descriptor = original_open(path, flags, *arguments, **keywords)
            if path == 'data':
                data.rename(self.directory / 'old-data')
                data.symlink_to(self.secret.parent, target_is_directory=True)
            return descriptor

        with mock.patch.object(viewer.os, 'open', side_effect=replace_parent):
            stream, metadata = files.open('data/secret.txt')
            with stream:
                self.assertEqual(stream.read(), b'report resource')

    def test_parent_replacement_while_selecting_the_root_is_rejected(self):
        parent = self.directory / 'parent'
        parent.mkdir()
        report = parent / 'bundle'
        report.mkdir()
        (report / 'index.html').write_bytes(self.html)
        original_open = os.open

        def replace_parent(path, flags, *arguments, **keywords):
            if path == 'parent':
                parent.rename(self.directory / 'old-parent')
                parent.symlink_to(self.secret.parent, target_is_directory=True)
            return original_open(path, flags, *arguments, **keywords)

        with mock.patch.object(viewer.os, 'open', new=replace_parent), \
                mock.patch.object(viewer.os, 'supports_dir_fd', os.supports_dir_fd | {replace_parent}):
            with self.assertRaises(OSError):
                files = viewer.ReportFiles(report)
                files.close()

    def test_special_files_are_never_served(self):
        os.mkfifo(self.directory / 'pipe')
        self.start()
        self.assertEqual(self.request('/' + self.server.token + '/pipe')[0], 404)
        with self.assertRaises(OSError):
            viewer.create_server(self.directory / 'pipe')

    def test_large_files_are_streamed_with_bounded_reads_and_head_reads_nothing(self):
        self.start()
        payload = b'trace\n' * 700000
        (self.directory / 'large.txt').write_bytes(payload)
        sizes = []
        original_open = self.server.files.open

        class ObservedStream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *arguments):
                self.stream.close()

            def read(self, amount):
                sizes.append(amount)
                return self.stream.read(amount)

        def observed_open(relative):
            stream, metadata = original_open(relative)
            return ObservedStream(stream), metadata

        with mock.patch.object(self.server.files, 'open', side_effect=observed_open):
            path = '/' + self.server.token + '/large.txt'
            self.assertEqual(self.request(path, method='HEAD')[0], 200)
            self.assertEqual(sizes, [])
            self.assertEqual(self.request(path)[::2], (200, payload))
        self.assertEqual(len(sizes), (len(payload) + 1024 * 1024 - 1) // (1024 * 1024))
        self.assertLessEqual(max(sizes), 1024 * 1024)

    def test_failed_bind_closes_owned_directory_handles(self):
        original_files = viewer.ReportFiles
        created = []

        def capture_files(path):
            files = original_files(path)
            created.append(files)
            return files

        with mock.patch.object(viewer, 'ReportFiles', side_effect=capture_files), \
                mock.patch.object(viewer.ReportServer, 'server_bind', side_effect=OSError('occupied')):
            with self.assertRaises(OSError):
                viewer.create_server(self.directory)
        self.assertEqual(len(created), 1)
        self.assertIsNone(created[0].descriptor)

    def test_cli_is_explicit_about_browser_opening_and_closes_on_interrupt(self):
        for open_browser in [False, True]:
            server = viewer.create_server(self.directory)
            output = io.StringIO()
            with mock.patch.object(viewer, 'create_server', return_value=server), \
                    mock.patch.object(server, 'serve_forever', side_effect=KeyboardInterrupt), \
                    mock.patch.object(viewer.webbrowser, 'open', return_value=True) as browser, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(viewer.main([str(self.directory)] + (['--open'] if open_browser else [])), 0)
            self.assertIn(server.url, output.getvalue())
            self.assertEqual(browser.call_count, int(open_browser))
            self.assertIsNone(server.files.descriptor)
            self.assertEqual(server.socket.fileno(), -1)

    def test_invalid_paths_ports_and_missing_indexes_fail_without_starting_a_server(self):
        for port in [-1, 65536, True, '8000']:
            with self.assertRaises(ValueError):
                viewer.create_server(self.directory, port)
        with self.assertRaises(FileNotFoundError):
            viewer.create_server(self.directory / 'missing.html')
        (self.directory / 'index.html').unlink()
        with self.assertRaises(FileNotFoundError):
            viewer.create_server(self.directory)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                viewer.main(['--port', '65536', str(self.directory)])
        self.assertEqual(error.exception.code, 2)

    def test_missing_safe_access_capabilities_fail_closed(self):
        with mock.patch.object(viewer.os, 'supports_dir_fd', set()):
            with self.assertRaisesRegex(OSError, 'no-follow'):
                viewer.create_server(self.directory)

    def test_cyclic_selected_symlinks_report_a_cli_error_without_a_traceback(self):
        cycle = self.directory / 'cycle.html'
        cycle.symlink_to(cycle.name)
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as error:
                viewer.main([str(cycle)])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('fgview: error:', errors.getvalue())
        self.assertNotIn('Traceback', errors.getvalue())

    def test_browser_launch_failure_keeps_the_server_available(self):
        server = viewer.create_server(self.directory)
        errors = io.StringIO()
        with mock.patch.object(viewer, 'create_server', return_value=server), \
                mock.patch.object(server, 'serve_forever', side_effect=KeyboardInterrupt) as serve, \
                mock.patch.object(viewer.webbrowser, 'open', return_value=False), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            self.assertEqual(viewer.main([str(self.directory), '--open']), 0)
        self.assertIn('open the printed URL manually', errors.getvalue())
        self.assertEqual(serve.call_count, 1)
        self.assertIsNone(server.files.descriptor)


@unittest.skipUnless(SUPPORTED and sync_playwright is not None, 'viewer capabilities or Playwright unavailable')
class ViewerBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.html, unused = cls.kernel.run(helpers.fixture('pi4_sched'), '--enable-fold', '--filter', '--timeline')
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            cls.playwright.stop()
            cls.kernel.close()
            raise unittest.SkipTest('Chromium not available: ' + str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.kernel.close()

    def setUp(self):
        self.root = Path(self.kernel.dir) / self._testMethodName
        self.root.mkdir()
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
        self.page.add_init_script("window.viewerViolations = []; document.addEventListener('securitypolicyviolation', event => viewerViolations.push(event.violatedDirective));")
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.server = None
        self.thread = None

    def start(self, html):
        (self.root / 'index.html').write_text(html, encoding='utf-8')
        self.server = viewer.create_server(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.page.goto(self.server.url, wait_until='load')

    def tearDown(self):
        try:
            violations = self.page.evaluate('window.viewerViolations')
        finally:
            self.page.close()
            if self.server is not None:
                self.server.shutdown()
                self.thread.join(timeout=5)
                self.server.server_close()
        self.assertEqual(violations, [])
        self.assertEqual(self.errors, [])

    def test_generated_report_controls_work_with_the_viewer_policy(self):
        self.start(self.html)
        icon = self.page.locator('.fold-icon').first
        icon.press('Enter')
        self.assertEqual(icon.get_attribute('aria-expanded'), 'false')
        icon.press('Enter')
        self.assertEqual(icon.get_attribute('aria-expanded'), 'true')
        row = self.page.locator('.line-container.expandable').first
        row.evaluate('row => row.click()')
        source = row.locator('xpath=following-sibling::*[1]')
        source.wait_for()
        self.assertTrue(source.locator('.location-link').count())
        self.page.fill('#filterInput', 'duration:>1')
        self.page.locator('#filterInput').press('Enter')
        self.assertTrue(self.page.locator('#content .line-container[style*="display: none"]').count())
        self.page.evaluate('fgSwitchView("timeline")')
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().N'), helpers.json_script(self.html, 'tlData')['n'])
        self.page.locator('#viewHelpBtn').click()
        self.assertTrue(self.page.locator('.tl-help').is_visible())

    def test_external_data_loads_on_demand_and_retries_missing_resources(self):
        payload = json.dumps(helpers.json_script(self.html, 'tlData'), ensure_ascii=False).encode('utf-8')
        data = self.root / 'data'
        data.mkdir()

        def descriptor(filename, content):
            return dict(file=filename, bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), format='json')

        manifest = json.dumps(dict(version=1, report_id='a' * 32,
                                   resources={'tlData': descriptor('timeline.json', payload)})).encode('utf-8')
        (data / 'manifest.json').write_bytes(manifest)
        config = dict(version=1, report_id='a' * 32, manifest=descriptor('data/manifest.json', manifest))
        report = re.sub(r'    <script type="application/json" id="tlData">.*?</script>\n', '', self.html, flags=re.S)
        report = report.replace('</head>', '<script type="application/json" id="fgDataManifest">' + json.dumps(config) + '</script></head>')
        requests = []
        self.page.on('request', lambda request: requests.append(request.url))
        self.start(report)
        self.assertFalse(any('manifest.json' in request for request in requests))
        self.page.evaluate('fgSwitchView("timeline")')
        self.page.locator('#timelineView [role=alert]').wait_for()
        self.assertIn('HTTP 404', self.page.locator('#timelineView [role=alert]').inner_text())
        (data / 'timeline.json').write_bytes(payload)
        self.page.locator('#timelineView button', has_text='Retry').click()
        self.page.locator('#timelineView .tl-canvas').wait_for(state='attached')
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().N'), helpers.json_script(self.html, 'tlData')['n'])
        self.assertTrue(any('/data/timeline.json' in request for request in requests))

    def test_report_style_blob_downloads_remain_allowed(self):
        self.start('''<!doctype html><button id="save" onclick="
            const resource = window.URL.createObjectURL(new Blob(['function,count\\nworker,1\\n'], {type:'text/csv'}));
            const link = document.createElement('a'); link.href = resource;
            link.download = 'trace.csv'; link.click(); window.URL.revokeObjectURL(resource);
        ">Export</button>''')
        with self.page.expect_download() as pending:
            self.page.locator('#save').click()
        download = pending.value
        self.assertEqual(download.suggested_filename, 'trace.csv')
        self.assertEqual(Path(download.path()).read_text(), 'function,count\nworker,1\n')
