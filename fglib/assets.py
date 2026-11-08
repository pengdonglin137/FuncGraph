"""Static assets (CSS/JS) that are inlined into the generated HTML.

All front-end code lives under assets/ so it can be edited and linted as
normal files. Files are read with newline='' so the output is byte-stable.
"""
import os
from functools import lru_cache

from .constants import PROJECT_DIR

ASSET_DIR = os.path.join(PROJECT_DIR, 'assets')

REPORT_CSS = 'report/report.css'
REPORT_JS = 'report/report.js'
TIMELINE_CSS = 'timeline/timeline.css'
TIMELINE_BOOT_JS = 'timeline/boot.js'
# Concatenated in this order inside `function __tlBoot() { ... }` (shared scope)
TIMELINE_JS_PARTS = ('timeline/model.js', 'timeline/render.js', 'timeline/interact.js', 'timeline/panels.js')


@lru_cache(maxsize=None)
def read_asset(rel):
    with open(os.path.join(ASSET_DIR, rel), 'r', encoding='utf-8', newline='') as f:
        return f.read()


def timeline_script():
    """Boot code (global scope) followed by the timeline closure."""
    parts = ''.join(read_asset(p) for p in TIMELINE_JS_PARTS)
    return read_asset(TIMELINE_BOOT_JS) + '\nfunction __tlBoot() {\n' + parts + '\n}\n'


def all_asset_paths():
    return (REPORT_CSS, REPORT_JS, TIMELINE_CSS, TIMELINE_BOOT_JS) + TIMELINE_JS_PARTS
