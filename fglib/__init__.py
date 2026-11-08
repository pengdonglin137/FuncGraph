"""FuncGraph: interactive HTML reports for Linux ftrace function_graph output.

Pipeline (see docs/design.md):
    parser.FtraceParser -> resolver.AddressResolver -> report.HtmlGenerator
    (optional) timeline.TimelineBuilder + btf.build_return_db -> Timeline tab
"""
from .constants import APP_VERSION  # noqa: F401
