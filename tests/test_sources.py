"""sources.py: source context from git revisions and URLs, version check."""
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import unittest

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

    def test_repo_path(self):
        self.assertEqual(sources.repo_path('./include/linux/mutex.h'), 'include/linux/mutex.h')
        self.assertEqual(sources.repo_path('/home/u/build/linux/fs/namei.c'), 'fs/namei.c')
        self.assertEqual(sources.repo_path('/src/k/fs/namei.c', ['/src/k']), 'fs/namei.c')


if __name__ == '__main__':
    unittest.main()
