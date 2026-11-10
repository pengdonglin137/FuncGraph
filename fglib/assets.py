"""Static assets (CSS/JS) that are inlined into the generated HTML.

All front-end code lives under assets/ so it can be edited and linted as
normal files. Files are read with newline='' so the output is byte-stable.
"""
import os
from functools import lru_cache

from .constants import PROJECT_DIR

ASSET_DIR = os.path.join(PROJECT_DIR, 'fglib', '_assets')
if not os.path.isdir(ASSET_DIR):
    ASSET_DIR = os.path.join(PROJECT_DIR, 'assets')

REPORT_CSS = 'report/report.css'
REPORT_JS = 'report/report.js'
REPORT_DATA_JS = 'report/data.js'
VIEWS_CSS = 'views/views.css'
VIEWS_EARLY_JS = 'views/early.js'
VIEWS_BOOT_JS = 'views/boot.js'
TIMELINE_CSS = 'timeline/timeline.css'
# Concatenated in this order inside `function __tlBoot() { ... }` (shared scope)
TIMELINE_JS_PARTS = ('timeline/model.js', 'timeline/render.js', 'timeline/interact.js', 'timeline/analysis.js', 'timeline/panels.js')
COMPARE_CSS = 'compare/compare.css'
COMPARE_JS = 'compare/compare.js'


@lru_cache(maxsize=None)
def read_asset(rel):
    with open(os.path.join(ASSET_DIR, rel), 'r', encoding='utf-8', newline='') as f:
        return f.read()


def timeline_script():
    """The timeline closure (registered as a view by views/boot.js)."""
    parts = ''.join(read_asset(p) for p in TIMELINE_JS_PARTS)
    return ('function* __tlSteps() {\n' + parts + '\n}\n'
            'function __tlBoot() {\n'
            '    const steps = __tlSteps();\n'
            '    let result;\n'
            '    do { result = steps.next(); } while (!result.done);\n'
            '    return result.value;\n'
            '}\n')


def all_asset_paths():
    return (REPORT_CSS, REPORT_JS, REPORT_DATA_JS, VIEWS_CSS, VIEWS_EARLY_JS, VIEWS_BOOT_JS, TIMELINE_CSS, COMPARE_CSS, COMPARE_JS) + TIMELINE_JS_PARTS
