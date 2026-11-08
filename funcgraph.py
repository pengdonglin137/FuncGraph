#!/usr/bin/env python3
"""FuncGraph command line entry point.

The implementation lives in the fglib package next to this file. The names
below are re-exported so that existing `import funcgraph` users keep working.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fglib.constants import *  # noqa: E402,F401,F403
from fglib.model import FtraceLine  # noqa: E402,F401
from fglib.util import *  # noqa: E402,F401,F403
from fglib.highlight import *  # noqa: E402,F401,F403
from fglib.parser import FtraceParser, parse_ftrace_file, identify_fold_blocks  # noqa: E402,F401
from fglib.resolver import *  # noqa: E402,F401,F403
from fglib.report import HtmlGenerator, generate_html  # noqa: E402,F401
from fglib.cli import main, build_timeline_data  # noqa: E402,F401

if __name__ == '__main__':
    main()
