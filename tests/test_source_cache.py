"""Optional source caching must not discard successfully downloaded code."""
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import http.server
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import helpers
from fglib import sources


SOURCE = 'int remote_target(int value)\n{\n    return value + 17;\n}\n'


class SourceCacheTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='fg-source-cache-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.requests = []
        self.status = 200
        self.partial_response = False
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                if owner.status != 200:
                    self.send_error(owner.status)
                    return
                content = SOURCE.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content[:8] if owner.partial_response else content)
                self.close_connection = True

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                      kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.template = 'http://127.0.0.1:' + str(self.server.server_address[1]) + '/{rev}/{path}'
        self.cache = self.root / 'cache'
        self.provider = sources.UrlProvider(self.template, 'pinned-revision', str(self.cache))
        self.path = 'kernel/demo.c'
        self.key = self.cache / hashlib.sha1(self.provider.url(self.path).encode()).hexdigest()[:20]

    def assert_download_preserved(self, provider=None):
        provider = provider or self.provider
        self.assertEqual(provider.get(self.path), SOURCE.split('\n'))
        self.assertEqual(provider.errors, {})
        self.assertEqual((provider.fetched, provider.cached), (1, 0))

    def test_cache_directory_is_a_file_does_not_discard_source(self):
        self.cache.write_text('unrelated data')
        self.assert_download_preserved()
        self.assertEqual(self.cache.read_text(), 'unrelated data')
        self.assertEqual(len(self.provider.cache_errors), 1)
        self.assertEqual(self.provider.get(self.path), SOURCE.split('\n'))
        self.assertEqual(self.requests, ['/pinned-revision/kernel/demo.c'])

    def test_unreadable_entry_falls_back_to_download_without_removing_other_data(self):
        self.key.mkdir(parents=True)
        sentinel = self.key / 'unrelated'
        sentinel.write_text('keep')
        self.assert_download_preserved()
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertEqual(set(self.cache.iterdir()), {self.key})
        self.assertEqual(len(self.provider.cache_errors), 1)

    def test_failed_cache_read_can_be_repaired_and_reused_offline(self):
        self.cache.mkdir()
        self.key.write_text(SOURCE)
        original_open = open

        def cache_read_error(filename, *args, **kwargs):
            if str(filename) == str(self.key):
                raise PermissionError(errno.EACCES, 'cache entry unreadable')
            return original_open(filename, *args, **kwargs)

        with mock.patch('builtins.open', side_effect=cache_read_error):
            self.assert_download_preserved()
        self.assertEqual(self.key.read_text(), SOURCE)
        self.assertEqual(len(self.provider.cache_errors), 1)
        self.status = 503
        cached = sources.UrlProvider(self.template, 'pinned-revision', str(self.cache))
        self.assertEqual(cached.get(self.path), SOURCE.split('\n'))
        self.assertEqual((cached.fetched, cached.cached), (0, 1))
        self.assertEqual(cached.cache_errors, {})
        self.assertEqual(len(self.requests), 1)

    def test_publication_failure_keeps_download_and_removes_temporary_file(self):
        with mock.patch.object(sources.os, 'replace', side_effect=OSError(errno.ENOSPC, 'cache full')):
            self.assert_download_preserved()
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(len(self.provider.cache_errors), 1)

    def test_partial_temporary_write_is_never_published_or_reused(self):
        original_temporary = tempfile.NamedTemporaryFile

        def partial_write(*args, **kwargs):
            stream = original_temporary(*args, **kwargs)
            original_write = stream.write

            def fail_after_prefix(data):
                original_write(data[:8])
                raise OSError(errno.ENOSPC, 'cache full during write')

            stream.write = fail_after_prefix
            return stream

        with mock.patch.object(sources.tempfile, 'NamedTemporaryFile', side_effect=partial_write):
            self.assert_download_preserved()
        self.assertEqual(list(self.cache.iterdir()), [])
        fresh = sources.UrlProvider(self.template, 'pinned-revision', str(self.cache))
        self.assert_download_preserved(fresh)
        self.assertEqual(self.key.read_text(), SOURCE)
        self.assertEqual(len(self.requests), 2)

    def test_cleanup_failure_does_not_hide_download_or_original_warning(self):
        with mock.patch.object(sources.os, 'replace', side_effect=OSError(errno.ENOSPC, 'cache full')):
            with mock.patch.object(sources.os, 'unlink', side_effect=PermissionError(errno.EACCES, 'cleanup denied')):
                self.assert_download_preserved()
        self.assertIn('cache full', self.provider.cache_errors[self.path])
        self.assertFalse(self.key.exists())
        self.assertEqual(len(list(self.cache.iterdir())), 1)

    def test_concurrent_providers_do_not_share_a_temporary_filename(self):
        providers = [sources.UrlProvider(self.template, 'pinned-revision', str(self.cache)) for index in range(2)]
        barrier = threading.Barrier(2)
        original_replace = sources.os.replace
        temporary_names = []

        def publish_after_both_writes(temporary, target):
            temporary_names.append(str(temporary))
            self.assertEqual(Path(temporary).read_text(), SOURCE)
            barrier.wait(timeout=5)
            return original_replace(temporary, target)

        with mock.patch.object(sources.os, 'replace', side_effect=publish_after_both_writes):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda provider: provider.get(self.path), providers))
        self.assertEqual(results, [SOURCE.split('\n')] * 2)
        self.assertEqual(len(set(temporary_names)), 2)
        self.assertEqual(self.key.read_text(), SOURCE)
        self.assertEqual(set(self.cache.iterdir()), {self.key})
        for provider in providers:
            self.assertEqual(provider.errors, {})
            self.assertEqual(provider.cache_errors, {})
            self.assertEqual((provider.fetched, provider.cached), (1, 0))

    def test_network_errors_do_not_create_cache_or_invent_source(self):
        for status in (404, 503):
            with self.subTest(status=status):
                self.status = status
                provider = sources.UrlProvider(self.template, 'pinned-revision', str(self.cache))
                self.assertIsNone(provider.get(self.path))
                self.assertIn(self.path, provider.errors)
                self.assertEqual(provider.cache_errors, {})
                self.assertEqual((provider.fetched, provider.cached), (0, 0))
                self.assertFalse(self.cache.exists())

    def test_incomplete_download_is_not_published_and_can_be_retried(self):
        self.partial_response = True
        self.assertIsNone(self.provider.get(self.path))
        self.assertIn(self.path, self.provider.errors)
        self.assertEqual(self.provider.cache_errors, {})
        self.assertEqual((self.provider.fetched, self.provider.cached), (0, 0))
        self.assertFalse(self.cache.exists())
        self.partial_response = False
        fresh = sources.UrlProvider(self.template, 'pinned-revision', str(self.cache))
        self.assert_download_preserved(fresh)
        self.assertEqual(self.key.read_text(), SOURCE)
        self.assertEqual(len(self.requests), 2)

    def test_existing_cache_key_needs_no_network_or_cache_write(self):
        self.cache.mkdir()
        self.key.write_text(SOURCE)
        cached = sources.UrlProvider(self.template, 'pinned-revision', str(self.cache))
        with mock.patch.object(sources.urllib.request, 'urlopen', side_effect=AssertionError('unexpected download')):
            with mock.patch.object(sources.os, 'replace', side_effect=AssertionError('unexpected cache write')):
                self.assertEqual(cached.get(self.path), SOURCE.split('\n'))
        self.assertEqual((cached.fetched, cached.cached), (0, 1))
        self.assertEqual(cached.cache_errors, {})
        self.assertEqual(self.requests, [])
        self.assertEqual(set(self.cache.iterdir()), {self.key})


if __name__ == '__main__':
    unittest.main()
