"""Keep the bilingual release gates executable without publishing anything."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import unquote, urlsplit

import helpers
from test_capture_examples import shell_blocks
from test_usage_examples import heading_anchor


ROOT = Path(helpers.ROOT)
DOCUMENTS = (ROOT / 'docs/releasing.cn.md', ROOT / 'docs/releasing.md')


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, message, *arguments):
        pass


class ReleaseDocumentationTest(unittest.TestCase):
    def test_bilingual_shell_recipes_are_identical(self):
        self.assertEqual(shell_blocks(DOCUMENTS[0]), shell_blocks(DOCUMENTS[1]))

    def test_shell_recipes_have_valid_syntax(self):
        for document in DOCUMENTS:
            for index, block in enumerate(shell_blocks(document)):
                with self.subTest(document=document.name, block=index):
                    result = subprocess.run(['bash', '-n'], input=block, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_tag_recipe_follows_master_and_pages_acceptance(self):
        for document in DOCUMENTS:
            with self.subTest(document=document.name):
                recipes = '\n'.join(shell_blocks(document))
                master = recipes.index('git push origin master')
                pages = recipes.index('bash site/build.sh')
                tag = recipes.index('git tag -a "$release_tag" "$release_commit"')
                publication = recipes.index('git push origin "refs/tags/$release_tag"')
                self.assertLess(master, pages)
                self.assertLess(pages, tag)
                self.assertLess(tag, publication)
                self.assertNotIn('--atomic', recipes)
                self.assertIn('"docs/releases/$release_tag.md"', recipes)
                self.assertIn('git commit -v', recipes)

    def test_document_links_and_anchors_resolve(self):
        for document in DOCUMENTS:
            for target in re.findall(r'\[[^\]\n]+\]\(([^)\s]+)\)', document.read_text(encoding='utf-8')):
                parsed = urlsplit(target)
                if parsed.scheme:
                    continue
                destination = document.parent / unquote(parsed.path) if parsed.path else document
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(destination.is_file())
                    if parsed.fragment:
                        headings = re.findall(r'^#{1,6} (.+)$', destination.read_text(encoding='utf-8'), re.M)
                        self.assertIn(unquote(parsed.fragment), [heading_anchor(heading) for heading in headings])

    def test_pages_recipe_verifies_all_files_and_rejects_stale_or_missing_content(self):
        block = next(recipe for recipe in shell_blocks(DOCUMENTS[0]) if 'site_url=' in recipe)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        with tempfile.TemporaryDirectory(prefix='fg-release-docs-') as directory:
            root = Path(directory)
            remote = root / 'deployed site'
            subprocess.run(['bash', 'site/build.sh', str(remote)], cwd=ROOT, check=True, capture_output=True)
            originals = {path.name: path.read_bytes() for path in remote.iterdir()}
            listed = re.search(r'^for page_file in (.*); do$', block, re.M).group(1)
            self.assertEqual(set(shlex.split(listed)), set(originals))
            server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(remote)))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                for scenario in ('match', 'changed', 'missing', 'wrong-commit'):
                    with self.subTest(scenario=scenario):
                        for name, content in originals.items():
                            (remote / name).write_bytes(content)
                        if scenario == 'changed':
                            (remote / 'sample_mixed.html').write_bytes(originals['sample_mixed.html'] + b'\n')
                        elif scenario == 'missing':
                            (remote / 'sample_text.png').unlink()
                        destination = root / (scenario + ' with spaces')
                        recipe = block.replace('site_dir="$(mktemp -d)"', 'site_dir=' + shlex.quote(str(destination)))
                        recipe = recipe.replace('https://pengdonglin137.github.io/FuncGraph',
                                                'http://127.0.0.1:' + str(server.server_port))
                        environment = dict(os.environ, release_commit='0' * 40 if scenario == 'wrong-commit' else commit)
                        result = subprocess.run(['bash', '-c', recipe], cwd=ROOT, env=environment,
                                                text=True, capture_output=True, timeout=45)
                        if scenario == 'match':
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                            self.assertEqual({path.name for path in (destination / 'online').iterdir()}, set(originals))
                            self.assertEqual(len(result.stdout.splitlines()), len(originals))
                        else:
                            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == '__main__':
    unittest.main()
