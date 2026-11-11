"""sources.py: source context from git revisions and URLs, version check."""
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

import helpers  # noqa: F401
from fglib import sources

V1 = 'int helper(int x)\n{\n\treturn x + 1;\n}\n\nint outer(int y)\n{\n\treturn helper(y);\n}\n'
V2 = '/* moved */\n\n' + V1          # same code two lines lower
OUT = """outer+0x10/0x40:
helper at ./kernel/demo.c:3
 (inlined by) outer at ./kernel/demo.c:8
"""


def have_git():
    return shutil.which('git') is not None


@unittest.skipUnless(have_git(), 'git not installed')
class GitSourceTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix='fgsrc_')
        g = lambda *a: subprocess.run(['git', '-C', self.repo] + list(a), check=True, capture_output=True)  # noqa: E731
        g('init', '-q')
        g('config', 'user.email', 't@t'); g('config', 'user.name', 't')
        os.makedirs(os.path.join(self.repo, 'kernel'))
        for text, tag in ((V1, 'v1'), (V2, 'v2')):
            with open(os.path.join(self.repo, 'kernel', 'demo.c'), 'w') as f:
                f.write(text)
            g('add', '-A'); g('commit', '-qm', tag); g('tag', tag)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_right_revision(self):
        new, st = sources.rewrite({'k': OUT}, sources.GitProvider(self.repo, 'v1'))
        self.assertIn('>8<\t\treturn helper(y);', new['k'])
        self.assertIn('>3<\t\treturn x + 1;', new['k'])
        self.assertEqual((st['checked'], st['match']), (1, 1))

    def test_wrong_revision_detected(self):
        new, st = sources.rewrite({'k': OUT}, sources.GitProvider(self.repo, 'v2'))
        self.assertEqual(st['match_ratio'], 0.0)

    def test_bad_revision(self):
        with self.assertRaises(ValueError):
            sources.GitProvider(self.repo, 'nope')


class UrlSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='fgweb_')
        os.makedirs(os.path.join(cls.root, 'v1', 'kernel'))
        with open(os.path.join(cls.root, 'v1', 'kernel', 'demo.c'), 'w') as f:
            f.write(V1)
        root = cls.root

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=root, **kw)

            def log_message(self, *a):
                pass
        cls.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.srv.server_address[1]}/{{rev}}/{{path}}'

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_fetch_cache_and_missing(self):
        cache = tempfile.mkdtemp(prefix='fgcache_')
        try:
            p = sources.UrlProvider(self.url, 'v1', cache)
            new, st = sources.rewrite({'k': OUT, 'x': 'f+0x1/0x2:\nf at kernel/missing.c:3\n'}, p)
            self.assertIn('>8<', new['k'])
            self.assertEqual(st['missing_files'], 1)
            self.assertEqual(p.fetched, 1)
            p2 = sources.UrlProvider(self.url, 'v1', cache)
            sources.rewrite({'k': OUT}, p2)
            self.assertEqual((p2.fetched, p2.cached), (0, 1))      # served from the cache
        finally:
            shutil.rmtree(cache, ignore_errors=True)

    def test_template_validation(self):
        with self.assertRaises(ValueError):
            sources.UrlProvider('http://x/{rev}/', 'v1')
        with self.assertRaises(ValueError):
            sources.UrlProvider('http://x/{rev}/{path}', None)

    def test_template_values_are_encoded_without_changing_template_syntax(self):
        revision = 'topic/fix #?&%'
        path = 'kernel/space dir:123/percent%2F-#?&"雪.c'
        provider = sources.UrlProvider('https://example.org/encoded%20root/{path}?h={rev}&view=raw%20source', revision)
        parsed = urlsplit(provider.url(path))
        self.assertEqual(unquote(parsed.path), '/encoded root/' + path)
        self.assertIn('/encoded%20root/', parsed.path)
        self.assertEqual(parse_qs(parsed.query), {'h': [revision], 'view': ['raw source']})
        self.assertEqual(parsed.fragment, '')
        self.assertIn('percent%252F-', parsed.path)
        provider = sources.UrlProvider('https://example.org/source?path={path}&rev={rev}', revision)
        self.assertEqual(parse_qs(urlsplit(provider.url(path)).query), {'path': [path], 'rev': [revision]})

    def test_special_source_names_fetch_and_reuse_cache_without_aliasing_percent_escapes(self):
        paths = ['kernel/space dir:123/percent%2F-#?&"雪.c', 'kernel/space dir:123/percent/-#?&"雪.c']
        revision = 'revision #?&%'
        for index, path in enumerate(paths):
            filename = os.path.join(self.root, revision, path)
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            with open(filename, 'w', encoding='utf-8') as stream:
                stream.write(V1 + str(index))
        with tempfile.TemporaryDirectory(prefix='fgcache_') as cache:
            provider = sources.UrlProvider(self.url, revision, cache)
            for index, path in enumerate(paths):
                self.assertEqual(provider.get(path), (V1 + str(index)).split('\n'))
            self.assertEqual((provider.fetched, provider.cached), (2, 0))
            cached = sources.UrlProvider(self.url, revision, cache)
            for index, path in enumerate(paths):
                self.assertEqual(cached.get(path), (V1 + str(index)).split('\n'))
            self.assertEqual((cached.fetched, cached.cached), (0, 2))

    def test_provider_rewrite_keeps_spaces_numeric_colons_and_inline_discriminators(self):
        path = 'kernel/space dir/version:123/demo #?%雪.c'
        output = OUT.replace('./kernel/demo.c', '/build tree/' + path)
        output = output.replace(':8\n', ':8 (discriminator 4)\n')
        provider = sources.Provider(roots=['/build tree'])
        provider.files[path] = V1.split('\n')
        rewritten, stats = sources.rewrite({'caller': output}, provider)
        self.assertEqual(stats['files'], 1)
        self.assertEqual(stats['positions'], 2)
        self.assertEqual(stats['with_source'], 2)
        self.assertEqual((stats['checked'], stats['match']), (1, 1))
        self.assertIn('>3<\t\treturn x + 1;', rewritten['caller'])
        self.assertIn('>8<\t\treturn helper(y);', rewritten['caller'])
        self.assertIn('demo #?%雪.c:8 (discriminator 4)', rewritten['caller'])

    def test_repo_path(self):
        self.assertEqual(sources.repo_path('./include/linux/mutex.h'), 'include/linux/mutex.h')
        self.assertEqual(sources.repo_path('/home/u/build/linux/fs/namei.c'), 'fs/namei.c')
        self.assertEqual(sources.repo_path('/src/k/fs/namei.c', ['/src/k']), 'fs/namei.c')


if __name__ == '__main__':
    unittest.main()
