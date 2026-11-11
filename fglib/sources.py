"""Source code for the source blocks from a URL template or a git revision.

By default the source context comes from the local tree that (fast)faddr2line reads.
With a provider, the context lines of every position in the faddr2line output are
regenerated from that provider instead:

    --source-url 'https://raw.githubusercontent.com/torvalds/linux/{rev}/{path}' --source-rev v6.18
    --source-url 'https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/{path}?h={rev}'
    --source-rev v6.18          (with --kernel-src pointing at a git repository: `git show v6.18:path`)

Fetched files are cached under ~/.cache/funcgraph/src (or --source-cache).

Version check: in an inline chain, the line of an outer frame is where the inner function
is called (inlined), so the inner function's name must appear on that line (+-1, for calls
split over lines). Wrong versions shift lines and fail this check; the ratio is reported.
"""
import hashlib
import os
import re
import subprocess
import urllib.error
import urllib.request
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

RE_POS = re.compile(r'^(\s*(?:\(inlined by\)\s+)?)([A-Za-z_$][\w.$]*) at (.+):([0-9]+)(?:\s+\(discriminator [0-9]+\))?\s*$')
RE_HEAD = re.compile(r'^\S+\+0x[0-9a-fA-F]+/0x[0-9a-fA-F]+(?: \[[\w-]+\])?:$')
TOP_DIRS = ('arch', 'block', 'certs', 'crypto', 'drivers', 'fs', 'include', 'init', 'io_uring', 'ipc',
            'kernel', 'lib', 'mm', 'net', 'rust', 'samples', 'scripts', 'security', 'sound', 'tools',
            'usr', 'virt')
CONTEXT = 5


def repo_path(path, roots=()):
    """Best guess of the repository-relative path of a DWARF file name."""
    p = path
    for r in roots:
        if r and p.startswith(r.rstrip('/') + '/'):
            p = p[len(r.rstrip('/')) + 1:]
            break
    while p.startswith('./'):
        p = p[2:]
    parts = p.lstrip('/').split('/')
    if os.path.isabs(p) or parts[0] not in TOP_DIRS:
        for k, part in enumerate(parts):
            if part in TOP_DIRS and k + 1 < len(parts):
                return '/'.join(parts[k:])
    return p.lstrip('/')


class Provider:
    """Base: get(path) -> list of lines or None. Subclasses implement _fetch."""

    def __init__(self, roots=()):
        self.roots = [r for r in roots if r]
        self.files = {}
        self.errors = {}

    def describe(self):
        return ''

    def prefetch(self, paths):
        todo = [p for p in set(paths) if p not in self.files]
        with ThreadPoolExecutor(max_workers=8) as ex:
            for p, res in zip(todo, ex.map(self._safe_fetch, todo)):
                self.files[p] = res

    def _safe_fetch(self, path):
        try:
            text = self._fetch(path)
        except Exception as e:      # network/git errors are reported, not fatal
            self.errors[path] = str(e)
            return None
        if text is None:
            self.errors.setdefault(path, 'not found')
            return None
        return text.split('\n')

    def get(self, path):
        if path not in self.files:
            self.files[path] = self._safe_fetch(path)
        return self.files[path]


class GitProvider(Provider):
    def __init__(self, repo, rev, roots=()):
        super().__init__(roots)
        self.repo, self.rev = repo, rev
        p = subprocess.run(['git', '-C', repo, 'rev-parse', '--verify', rev + '^{commit}'], capture_output=True, text=True)
        if p.returncode != 0:
            raise ValueError(f"--source-rev: '{rev}' is not a commit in {repo}: {p.stderr.strip()}")
        self.commit = p.stdout.strip()

    def describe(self):
        return f'git {self.repo} @ {self.rev} ({self.commit[:12]})'

    def _fetch(self, path):
        p = subprocess.run(['git', '-C', self.repo, 'show', f'{self.commit}:{path}'], capture_output=True)
        return p.stdout.decode('utf-8', 'replace') if p.returncode == 0 else None


class UrlProvider(Provider):
    def __init__(self, template, rev=None, cache_dir=None, roots=(), timeout=20):
        super().__init__(roots)
        if '{path}' not in template:
            raise ValueError('--source-url must contain {path}')
        if '{rev}' in template and not rev:
            raise ValueError('--source-url uses {rev}: give --source-rev')
        self.template, self.rev, self.timeout = template, rev, timeout
        self.cache_dir = cache_dir or os.path.join(os.path.expanduser('~'), '.cache', 'funcgraph', 'src')
        self.fetched = self.cached = 0

    def describe(self):
        return self.template.replace('{rev}', self.rev or '{rev}')

    def url(self, path):
        return self.template.replace('{rev}', quote(self.rev or '', safe='')).replace('{path}', quote(path, safe='/'))

    def _fetch(self, path):
        url = self.url(path)
        key = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest()[:20])
        if os.path.exists(key):
            self.cached += 1
            with open(key, encoding='utf-8', errors='replace') as f:
                return f.read()
        req = urllib.request.Request(url, headers={'User-Agent': 'funcgraph'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        os.makedirs(self.cache_dir, exist_ok=True)
        tmp = key + '.tmp%d' % os.getpid()
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(data)
        os.replace(tmp, key)
        self.fetched += 1
        return data


def _positions(text):
    for line in text.split('\n'):
        m = RE_POS.match(line)
        if m:
            yield m


def rewrite(text_map, provider):
    """Regenerate source context lines in faddr2line outputs. Returns (new_map, stats)."""
    paths = set()
    for text in text_map.values():
        if isinstance(text, str):
            for m in _positions(text):
                paths.add(repo_path(m.group(3), provider.roots))
    provider.prefetch(paths)
    st = {'positions': 0, 'with_source': 0, 'checked': 0, 'match': 0, 'files': len(paths),
          'missing_files': sum(1 for p in paths if provider.files.get(p) is None)}
    out = {}
    for key, text in text_map.items():
        if not isinstance(text, str):
            out[key] = text
            continue
        res = []
        prev_func = None        # inner frame of the previous position in the same block
        for line in text.split('\n'):
            m = RE_POS.match(line)
            if RE_HEAD.match(line.strip()):
                res.append(line)
                prev_func = None
                continue
            if not m:
                continue            # old context lines / "not found" notes: regenerated below
            res.append(line)
            st['positions'] += 1
            path = repo_path(m.group(3), provider.roots)
            src = provider.get(path)
            n = int(m.group(4))
            if not src or n < 1 or n > len(src):
                continue
            st['with_source'] += 1
            inlined_here = prev_func if m.group(1).strip() else None
            prev_func = m.group(2)
            if inlined_here:
                # an outer frame: its line calls (inlines) the inner function, so the name is there
                st['checked'] += 1
                name = re.compile(r'\b' + re.escape(inlined_here.split('.')[0]) + r'\b')
                if any(name.search(src[j]) for j in range(max(0, n - 2), min(len(src), n + 1))):
                    st['match'] += 1
            for j in range(max(0, n - 1 - CONTEXT), min(len(src), n + CONTEXT)):
                code = src[j].rstrip()
                res.append(f'>{j + 1}<\t{code}' if j + 1 == n else f' {j + 1} \t{code}')
        out[key] = '\n'.join(res) + '\n'
    st['match_ratio'] = (st['match'] / st['checked']) if st['checked'] else None
    return out, st


def make_provider(args, kernel_src, roots):
    if not (args.source_url or args.source_rev):
        return None
    if args.source_url:
        return UrlProvider(args.source_url, args.source_rev, args.source_cache, roots)
    if not kernel_src or not os.path.isdir(os.path.join(kernel_src, '.git')) and \
            subprocess.run(['git', '-C', kernel_src, 'rev-parse'], capture_output=True).returncode != 0:
        raise ValueError('--source-rev without --source-url needs --kernel-src to be a git repository')
    return GitProvider(kernel_src, args.source_rev, roots)
