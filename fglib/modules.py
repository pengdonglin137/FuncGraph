"""Report-scoped, exact-name module discovery without repeated directory scans."""
import os

from .util import verbose_print


SUFFIXES = ('.ko', '.ko.debug', '.ko.gz', '.ko.xz', '.ko.zst')


class ModuleIndex:
    """Index supplied module trees lazily and reuse positive and negative lookups."""

    def __init__(self, directories=None, verbose=False):
        self.directories = tuple(os.path.abspath(os.fspath(path)) for path in directories or ())
        self.verbose = verbose
        self._entries = None
        self._resolved = {}

    def _build(self):
        entries = {}
        scanned = set()
        for root_order, directory in enumerate(self.directories):
            if not os.path.isdir(directory):
                verbose_print(f"Skipping non-existent module directory: {directory}", self.verbose)
                continue
            if os.path.realpath(directory) in scanned:
                continue
            for root, directories, filenames in os.walk(directory, onerror=self._walk_error):
                identity = os.path.realpath(root)
                if identity in scanned:
                    directories.clear()
                    continue
                scanned.add(identity)
                directories.sort()
                for filename in sorted(filenames):
                    for suffix_order, suffix in enumerate(SUFFIXES):
                        if not filename.endswith(suffix):
                            continue
                        name = filename[:-len(suffix)]
                        path = os.path.join(root, filename)
                        if name and os.path.isfile(path):
                            relative = os.path.relpath(path, directory)
                            entries.setdefault(name.replace('-', '_'), []).append(
                                (root_order, name, suffix_order, relative, path))
                        break
        self._entries = entries

    def _walk_error(self, error):
        verbose_print(f"Module search: {error}", self.verbose)

    def find(self, module_name):
        if not isinstance(module_name, str) or not module_name or os.path.basename(module_name) != module_name:
            return None
        if module_name in self._resolved:
            return self._resolved[module_name]
        if self._entries is None:
            for directory in self.directories:
                if not os.path.isdir(directory):
                    continue
                direct = os.path.join(directory, module_name + '.ko')
                if os.path.isfile(direct):
                    self._resolved[module_name] = direct
                    verbose_print(f"Module '{module_name}': {direct}", self.verbose)
                    return direct
                break
            self._build()
        candidates = self._entries.get(module_name.replace('-', '_'), ())

        def priority(candidate):
            root_order, name, suffix_order, relative, path = candidate
            return root_order, name != module_name, suffix_order, relative.count(os.sep), relative

        path = min(candidates, key=priority)[-1] if candidates else None
        self._resolved[module_name] = path
        verbose_print(f"Module '{module_name}': {path or 'not found in supplied directories'}", self.verbose)
        return path
