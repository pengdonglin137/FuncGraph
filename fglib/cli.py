"""Command line interface: argument parsing, toolchain discovery, pipeline."""

import os
import shlex
import subprocess
import argparse
import sys
import time

from .constants import ERROR_CODE_MAP, PROJECT_DIR
import configparser

import lzma

from . import config, inputs, prefilter
from .parser import parse_ftrace_file
from .report import write_html
from .util import SOURCE_BROWSERS, check_gawk_available, find_module_path, source_browser_url, verbose_print


def build_compare_data(args, raw_lines, parsed_lines, timeline_json, vmlinux_path, module_dirs):
    """Compare tab data: per-function statistics of --compare BASE vs. this trace."""
    from . import compare, timeline as tlmod
    t0 = time.time()
    base_lines, _ = inputs.read_lines([args.compare], raw_instance=args.raw_instance)
    pre = prefilter.Filter(pids=args.pid, comms=args.comm, cpus=args.cpu, time_range=args.time_range,
                           graph=args.graph, notrace=args.notrace)
    if pre.active():
        base_lines, _ = prefilter.apply(base_lines, pre)     # same filter on both sides
    rettypes = load_rettypes(parsed_lines, vmlinux_path, module_dirs, args)
    if timeline_json is not None:
        cur = timeline_json
    else:
        cur = tlmod.build_timeline([l.raw_line for l in parsed_lines], rettypes=rettypes, errno_map=ERROR_CODE_MAP,
                                   overhead=parse_overhead(args.overhead)).to_data()
    data = compare.compare(cur, base_lines, rettypes=rettypes, overhead=parse_overhead(args.overhead), base_name=os.path.basename(args.compare),
                           cur_name=os.path.basename(input_name(args)))
    print(f"Compare: {len(data['rows'])} functions vs {args.compare}, {time.time() - t0:.2f}s")
    return data


def parse_overhead(text):
    if not text:
        return None
    try:
        parts = [int(float(x)) for x in text.split(',')]
    except ValueError:
        print(f"Error: --overhead expects CHILD_NS[,SELF_NS], got {text!r}", file=sys.stderr)
        sys.exit(1)
    return (parts[0], parts[1] if len(parts) > 1 else 0)


_RETTYPES = {}


def load_rettypes(parsed_lines, vmlinux_path, module_dirs, args):
    """BTF return-type database (cached per run); None with --no-btf or when no BTF exists."""
    if args.no_btf or (not vmlinux_path and not args.btf):
        return None
    if 'db' in _RETTYPES:
        return _RETTYPES['db']
    from . import btf as btfmod
    modules = sorted({l.get('module_name') for l in parsed_lines if l.get('module_name')})
    mod_paths = []
    for m in modules:
        mp = find_module_path(m, module_dirs, args.verbose)
        if mp:
            mod_paths.append(mp)
    rettypes = btfmod.build_return_db(vmlinux_path, extra_paths=args.btf or [], module_paths=mod_paths)
    for err in rettypes.errors:
        verbose_print(f"BTF: {err}", args.verbose)
    if len(rettypes) == 0:
        print("Note: no BTF found (vmlinux lacks .BTF); error paths will be unverified", file=sys.stderr)
        rettypes = None
    else:
        verbose_print(f"BTF: {len(rettypes)} function return types from {rettypes.sources}", args.verbose)
    _RETTYPES['db'] = rettypes
    return rettypes


def build_timeline_data(parsed_lines, vmlinux_path, module_dirs, args):
    """Build the Timeline tab data set. Returns a JSON-serializable dict or None on failure."""
    from . import timeline as tlmod
    t0 = time.time()
    rettypes = load_rettypes(parsed_lines, vmlinux_path, module_dirs, args)
    lines = [l.raw_line for l in parsed_lines]
    builder = tlmod.build_timeline(lines, rettypes=rettypes, errno_map=ERROR_CODE_MAP, verbose=args.verbose,
                                   overhead=parse_overhead(args.overhead))
    data = builder.to_data()
    if args.tail_calls:
        from . import tailcalls
        data['tail_calls'] = tailcalls.analyze(data, vmlinux_path)
        evidence = data['tail_calls']
        supported = sum(pair['status'] == 'supported' for pair in evidence['pairs'].values())
        print(f"Tail jumps: {supported}/{len(evidence['pairs'])} unique caller-mismatch relationships supported by supplied binary")
        if evidence['status'] == 'unavailable':
            print('Note: tail-jump analysis unavailable: ' + evidence['reason'], file=sys.stderr)
    st = data['stats']
    print(f"Timeline: {st['slices']} slices, {st['tasks']} tasks, mode={data['mode']}, "
          f"{sum(1 for c in data['chains'] if not c[6])} error chains (+{sum(c[6] for c in data['chains'])} minor), {st['offcpu']} off-CPU, {time.time() - t0:.2f}s")
    if data['n'] == 0:
        print("Note: no function_graph calls found; timeline tab omitted", file=sys.stderr)
        return None
    return data


def build_parser():
    """Command line options."""
    parser = argparse.ArgumentParser(description='Convert ftrace output to interactive HTML')
    parser.add_argument('ftrace_file', nargs='+',
                        help="ftrace output file(s); '-' reads stdin, .gz/.xz/.bz2 are decompressed, "
                             'several files are merged by timestamp')
    parser.add_argument('--input-format', choices=('auto', 'text', 'raw'), default='auto',
                        help='Primary input format: auto-detect text or binary trace-cmd trace.dat (default: auto)')
    parser.add_argument('--raw-instance', metavar='NAME',
                        help='Select this named trace-cmd buffer instead of the top-level buffer (also for --compare)')
    parser.add_argument('--config', metavar='FILE',
                        help='INI file with option defaults (default: ./funcgraph.ini or ~/.config/funcgraph/config.ini)')
    parser.add_argument('--preset', metavar='NAME', help='use the [preset NAME] section of the config file')
    parser.add_argument('--no-config', action='store_true', help='ignore config files')
    parser.add_argument('--vmlinux', help='Path to vmlinux (for source locations, links and BTF). Optional: without it '
                        'the report has no source information but the trace views, timeline and analyses work')
    parser.add_argument('--kernel-src', type=str,
                        help='Path to kernel source root')
    parser.add_argument('--module-dirs', nargs='*', default=[],
                        help='Directories to search for kernel modules')
    parser.add_argument('--module-srcs', nargs='*', default=[],
                        help='Module source code root directories (can specify multiple paths)')
    parser.add_argument('--source-browser', choices=tuple(SOURCE_BROWSERS),
                        help='Online kernel source browser (default: github, torvalds/linux); '
                             'overrides a base-url inherited from config')
    parser.add_argument('--source-browser-rev', metavar='REV',
                        help='Online source tag/branch/commit (default: master for github, latest for elixir); '
                             'independent of --source-rev, which controls embedded source')
    parser.add_argument('--base-url', help='Custom base URL for source code links; overrides built-in browser settings')
    parser.add_argument('--module-url', action='append', help='Module URL mapping (can be specified multiple times, format: url:mod1,mod2)')
    parser.add_argument('--output', default='ftrace_viz.html', help='Output HTML file path or sidecar report directory')
    parser.add_argument('--report-mode', choices=('single', 'sidecar'), default='single',
                        help='single: offline HTML (default); sidecar: HTTP report directory, requires --timeline; Text loads in full on first use')
    parser.add_argument('--auto-search', action='store_true',
                        help='Automatically search common module directories')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose output for debugging')
    parser.add_argument('--fast', action='store_true',
                        help='Use fastfaddr2line.py for vmlinux processing')
    parser.add_argument('--use-external', action='store_true',
                        help='Force using external faddr2line')
    parser.add_argument('--highlight-code', action='store_true',
                        help='Enable syntax highlighting for C source code (requires Pygments)')
    parser.add_argument('--path-prefix', nargs='*', default=[],
                        help='Alternative path prefixes to strip from file paths (can specify multiple paths)')
    parser.add_argument('--filter', action='store_true',
                        help='Enable filter box in HTML for CPU/PID/Comm filtering')
    parser.add_argument('--func-links', action='store_true',
                        help='Enable source links for function names (adds some overhead)')
    parser.add_argument('--entry-offset', type=int, default=0,
                        help='Offset to add to function entry addresses (for -fpatchable-function-entry)')
    parser.add_argument('--enable-fold', action='store_true',
                        help='Enable function call folding (adds some overhead)')
    flt = parser.add_argument_group('pre-filter (applied before conversion; keeps whole call trees)')
    def csv(conv):
        def f(text):
            try:
                return [conv(x) for x in text.split(',') if x]
            except ValueError:
                raise argparse.ArgumentTypeError(f'bad list {text!r}')
        return f
    flt.add_argument('--pid', type=csv(int), metavar='PID[,PID]', help='keep only these tasks')
    flt.add_argument('--comm', type=csv(str), metavar='RE[,RE]', help='keep tasks whose comm matches')
    flt.add_argument('--cpu', type=csv(int), metavar='N[,N]', help='keep only these CPUs')
    flt.add_argument('--time-range', metavar='START:END',
                     help='keep calls starting in this window; absolute seconds, or +offset from the first '
                          'timestamp with units, e.g. +2ms:+5ms')
    flt.add_argument('--graph', type=csv(str), metavar='RE[,RE]', help='keep only the call trees of matching functions')
    flt.add_argument('--notrace', type=csv(str), metavar='RE[,RE]', help='drop the call trees of matching functions')
    parser.add_argument('--overhead', metavar='CHILD_NS[,SELF_NS]',
                        help='tracing overhead per traced child call (and per call) to offer corrected durations; '
                             "default: read from a '# fgcapture: overhead' header (fgcapture.sh --calibrate)")
    src = parser.add_argument_group('source code for the source blocks (default: the local tree faddr2line reads)')
    src.add_argument('--source-url', metavar='TEMPLATE',
                     help="fetch files from a URL with {path} and optional {rev}, e.g. "
                          "'https://raw.githubusercontent.com/torvalds/linux/{rev}/{path}'")
    src.add_argument('--source-rev', metavar='REV',
                     help='revision for {rev}; without --source-url: read files with `git show REV:path` from --kernel-src')
    src.add_argument('--source-cache', metavar='DIR', help='cache for fetched files (default ~/.cache/funcgraph/src)')
    parser.add_argument('--export', metavar='FILE',
                        help='also write the analysis results: FILE.json (functions, errors, critical sections, '
                             'scheduling latency, atomic sleeps, off-CPU) or FILE.csv (per-function statistics)')
    parser.add_argument('--compare', metavar='BASE_TRACE',
                        help='Add a Compare tab: per-function calls/self/inclusive time vs. this base trace')
    parser.add_argument('--no-text-view', action='store_true',
                        help='Only build the Timeline/Compare tabs (no per-line text view; '
                             'skip address resolution unless --keep-source is used); '
                             'for traces too large for the text view (more than ~100k lines)')
    parser.add_argument('--keep-source', action='store_true',
                        help='With --timeline --no-text-view, retain resolved source blocks and inline frames '
                             'without per-line Text DOM; adds normal source-resolution generation cost')
    parser.add_argument('--timeline', action='store_true',
                        help='Add a Timeline tab: per-task flame chart over time with context/error-path coloring')
    parser.add_argument('--tail-calls', action='store_true',
                        help='With --timeline and --vmlinux, inspect caller/parent mismatches for static tail-jump evidence '
                             '(ELF64 x86-64/arm64; needs pyelftools and objdump; adds generation work)')
    parser.add_argument('--btf', nargs='*', default=None, metavar='FILE',
                        help='BTF sources for return-type checks (raw /sys/kernel/btf/* copies or ELF files). '
                             'Default: the .BTF section of --vmlinux and of resolved modules')
    parser.add_argument('--no-btf', action='store_true',
                        help='Do not use BTF; error paths are guessed from return values only')
    return parser


def validate_args(args):
    """Check paths/URLs; exits on fatal errors, warns otherwise."""
    if args.report_mode not in ('single', 'sidecar'):
        print('Error: report mode must be single or sidecar', file=sys.stderr)
        sys.exit(1)
    if args.report_mode == 'sidecar' and not args.timeline:
        print('Error: --report-mode sidecar requires --timeline', file=sys.stderr)
        sys.exit(1)
    if args.tail_calls and not (args.timeline and args.vmlinux):
        print('Error: --tail-calls needs --timeline and --vmlinux', file=sys.stderr)
        sys.exit(1)
    if args.keep_source and not (args.timeline and args.no_text_view):
        print('Error: --keep-source needs --timeline and --no-text-view', file=sys.stderr)
        sys.exit(1)
    if args.time_range:
        try:
            prefilter.parse_range(args.time_range)
        except ValueError as e:
            print(f"Error: --time-range: {e}", file=sys.stderr)
            sys.exit(1)
    # 参数健壮性检查
    # 检查ftrace文件是否存在
    if args.compare and not os.path.isfile(args.compare):
        print(f"Error: --compare file '{args.compare}' does not exist", file=sys.stderr)
        sys.exit(1)
    for p in args.ftrace_file:
        if p == '-':
            continue
        if not os.path.exists(p):
            print(f"Error: ftrace file '{p}' does not exist", file=sys.stderr)
            sys.exit(1)
        if not os.path.isfile(p):
            print(f"Error: '{p}' is not a regular file", file=sys.stderr)
            sys.exit(1)
    if args.ftrace_file.count('-') > 1:
        print("Error: '-' (stdin) can be given only once", file=sys.stderr)
        sys.exit(1)

    if args.vmlinux and not os.path.exists(args.vmlinux):
        print(f"Error: vmlinux file '{args.vmlinux}' does not exist", file=sys.stderr)
        sys.exit(1)

    if args.vmlinux and not os.path.isfile(args.vmlinux):
        print(f"Error: '{args.vmlinux}' is not a regular file", file=sys.stderr)
        sys.exit(1)

    # 检查kernel-src路径（如果提供）
    if args.kernel_src:
        if not os.path.exists(args.kernel_src):
            print(f"Error: kernel source directory '{args.kernel_src}' does not exist", file=sys.stderr)
            sys.exit(1)
        if not os.path.isdir(args.kernel_src):
            print(f"Error: '{args.kernel_src}' is not a directory", file=sys.stderr)
            sys.exit(1)

    # 检查module-dirs路径
    for module_dir in args.module_dirs:
        if not os.path.exists(module_dir):
            print(f"Warning: module directory '{module_dir}' does not exist", file=sys.stderr)
        elif not os.path.isdir(module_dir):
            print(f"Warning: '{module_dir}' is not a directory", file=sys.stderr)

    # 检查module-srcs路径
    for module_src in args.module_srcs:
        if not os.path.exists(module_src):
            print(f"Warning: module source directory '{module_src}' does not exist", file=sys.stderr)
        elif not os.path.isdir(module_src):
            print(f"Warning: '{module_src}' is not a directory", file=sys.stderr)

    # 检查URL格式（如果提供）
    if args.base_url:
        if not args.base_url.startswith(('http://', 'https://')):
            print(f"Warning: base-url '{args.base_url}' does not start with http:// or https://", file=sys.stderr)

    if args.module_url:
        # 检查module_url格式（支持多个--module-url参数）
        for module_url_str in args.module_url:
            # 使用与parse_module_url相同的逻辑来验证
            # 首先检查是否有冒号
            if ':' not in module_url_str:
                # 没有冒号，只有URL
                if not module_url_str.startswith(('http://', 'https://')):
                    print(f"Warning: module-url '{module_url_str}' does not start with http:// or https://", file=sys.stderr)
                continue

            # 有冒号，需要解析
            import re
            # 使用与parse_module_url相同的正则表达式
            url_pattern = r'https?://[^:,]+(?=:[^,]*,|$)'
            urls = re.findall(url_pattern, module_url_str)

            if not urls:
                # 尝试更宽松的模式
                url_pattern = r'https?://[^:,]+'
                urls = re.findall(url_pattern, module_url_str)

            for url in urls:
                url = url.strip()
                # 检查URL格式
                if not url.startswith(('http://', 'https://')):
                    print(f"Warning: URL '{url}' in module-url does not start with http:// or https://", file=sys.stderr)

    # 检查输出目录是否可写
    output_dir = os.path.dirname(args.output) or '.'
    if not os.path.exists(output_dir):
        print(f"Error: output directory '{output_dir}' does not exist", file=sys.stderr)
        sys.exit(1)
    if not os.access(output_dir, os.W_OK):
        print(f"Error: output directory '{output_dir}' is not writable", file=sys.stderr)
        sys.exit(1)

    # 检查fast和use-external互斥
    if args.fast and args.use_external:
        print("Warning: --fast and --use-external are both specified, --use-external will be ignored", file=sys.stderr)


def collect_module_dirs(args):
    """--module-dirs plus common system locations with --auto-search."""
    # 自动添加常见模块目录
    module_dirs = args.module_dirs.copy()
    if args.auto_search:
        try:
            uname_output = subprocess.check_output(['uname', '-r']).decode().strip()
            verbose_print(f"Detected kernel version: {uname_output}", args.verbose)
        except Exception as e:
            verbose_print(f"Failed to detect kernel version: {str(e)}", args.verbose)
            uname_output = ""
        
        common_dirs = [
            '/lib/modules',
            '/usr/lib/modules',
            f'/lib/modules/{uname_output}/kernel',
            f'/lib/modules/{uname_output}/extra',
            f'/lib/modules/{uname_output}/updates',
        ]
        module_dirs.extend(common_dirs)
        verbose_print(f"Added {len(common_dirs)} common module directories", args.verbose)
    return module_dirs


def find_faddr2line(args):
    """Locate faddr2line: kernel scripts/, then PATH, then bundled fastfaddr2line.py (--fast).

    Returns (faddr2line_path, cross_compile, llvm, util_prefix, util_suffix).
    """
    # 处理工具链前缀（CROSS_COMPILE 和 LLVM）
    # 借鉴内核 faddr2line 的逻辑
    # 注意：faddr2line 只存在于内核源码的 scripts 目录下，LLVM 没有这个工具
    faddr2line_path = None
    util_suffix = ""
    util_prefix = ""

    # 从环境变量读取
    cross_compile = os.environ.get('CROSS_COMPILE', '')
    llvm = os.environ.get('LLVM', '')

    if llvm == "":
        util_prefix = cross_compile
    else:
        util_prefix = "llvm-"

        if llvm.endswith("/"):
            util_prefix = llvm + util_prefix
        elif llvm.startswith("-"):
            util_suffix = llvm

    # faddr2line 只存在于内核源码中，不使用工具链前缀
    # 但我们需要记录这些信息，因为后续可能需要调用其他工具（如 readelf、addr2line）

    # 1. 尝试在 kernel source 中查找原生 faddr2line
    if args.kernel_src:
        potential_path = os.path.join(args.kernel_src, 'scripts', 'faddr2line')
        if os.path.isfile(potential_path):
            faddr2line_path = potential_path
            verbose_print(f"Found faddr2line in kernel source: {potential_path}", args.verbose)

    # 2. 尝试在 PATH 中查找原生 faddr2line
    if not faddr2line_path:
        try:
            result = subprocess.run(['which', 'faddr2line'], capture_output=True, text=True, check=True)
            faddr2line_path = result.stdout.strip()
            verbose_print(f"Found faddr2line in PATH: {faddr2line_path}", args.verbose)
        except Exception:
            pass

    # 3. 如果还是找不到，检查是否使用 fast 模式
    if not faddr2line_path:
        if args.fast:
            # fast 模式下，使用 fastfaddr2line.py
            script_dir = PROJECT_DIR
            fast_faddr2line_path = os.path.join(script_dir, 'fastfaddr2line.py')
            if os.path.exists(fast_faddr2line_path):
                faddr2line_path = fast_faddr2line_path
                verbose_print(f"Using fastfaddr2line.py at {fast_faddr2line_path}", args.verbose)
            else:
                print(f"Error: Cannot locate fastfaddr2line.py at {fast_faddr2line_path}", file=sys.stderr)
                sys.exit(1)
        else:
            print("Error: Cannot locate faddr2line tool (only available in kernel source scripts/)", file=sys.stderr)
            sys.exit(1)
    return faddr2line_path, cross_compile, llvm, util_prefix, util_suffix


def input_name(args):
    names = ['stdin' if p == '-' else p for p in args.ftrace_file]
    return names[0] if len(names) == 1 else names[0] + f' (+{len(names) - 1})'


def parse_args(argv=None):
    parser = build_parser()
    pre, _ = parser.parse_known_args(argv)
    if not pre.no_config:
        try:
            path = config.find_config(pre.config)
            if path:
                values, used = config.load(path, pre.preset)
                if pre.source_browser is not None and pre.base_url is None:
                    values = {key: value for key, value in values.items()
                              if key.strip().lower().replace('_', '-') != 'base-url'}
                config.apply(parser, values, path)
                if pre.verbose:
                    print(f"[INFO] config {path}: {', '.join(used)}", file=sys.stderr)
            elif pre.preset:
                raise config.ConfigError('--preset given but no config file found')
        except (config.ConfigError, OSError, configparser.Error) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    args = parser.parse_args(argv)
    try:
        browser_url = source_browser_url(args.source_browser or 'github', args.source_browser_rev)
    except ValueError as error:
        parser.error(str(error))
    if args.base_url is None:
        args.source_browser = args.source_browser or 'github'
        args.base_url = browser_url
    return args


def main():
    args = parse_args()
    validate_args(args)

    # 检查gawk可用性并决定是否使用--list选项
    gawk_available = check_gawk_available()
    use_list = gawk_available
    
    if args.verbose:
        print("[INFO] Verbose mode enabled", file=sys.stderr)
        print(f"[INFO] gawk available: {gawk_available}", file=sys.stderr)
        print(f"[INFO] Using faddr2line --list option: {use_list}", file=sys.stderr)
        print(f"[INFO] Using fast mode: {args.fast}", file=sys.stderr)

    module_dirs = collect_module_dirs(args)
    if args.vmlinux:
        faddr2line_path, cross_compile, llvm, util_prefix, util_suffix = find_faddr2line(args)
        faddr2line_path = os.path.abspath(faddr2line_path)
        vmlinux_path = os.path.abspath(args.vmlinux)
    else:
        faddr2line_path = vmlinux_path = None
        cross_compile, llvm, util_prefix, util_suffix = os.environ.get('CROSS_COMPILE', ''), os.environ.get('LLVM', ''), '', ''
        print("Note: no --vmlinux given: source locations, source links and BTF checks are skipped")

    # 处理内核源码路径（单个）
    kernel_src_abs = os.path.abspath(args.kernel_src) if args.kernel_src else None

    # 处理多个path_prefix路径（保持原样，不转换为绝对路径）
    path_prefix = args.path_prefix if args.path_prefix else []

    # 处理多个module_srcs路径
    module_srcs_abs = []
    if args.module_srcs:
        for src in args.module_srcs:
            module_srcs_abs.append(os.path.abspath(src))

    # 显示工具链信息
    if args.verbose:
        if cross_compile:
            print(f"[INFO] CROSS_COMPILE: {cross_compile}", file=sys.stderr)
        if llvm:
            print(f"[INFO] LLVM: {llvm}", file=sys.stderr)
        if util_prefix or util_suffix:
            print(f"[INFO] Tool chain prefix/suffix: {util_prefix}*{util_suffix}", file=sys.stderr)
            print("[INFO] Note: faddr2line only exists in kernel source, not affected by toolchain", file=sys.stderr)
        print(f"[INFO] Using faddr2line: {faddr2line_path}", file=sys.stderr)
        print(f"[INFO] Using vmlinux: {vmlinux_path}", file=sys.stderr)
        if kernel_src_abs:
            print(f"[INFO] Using kernel source: {kernel_src_abs}", file=sys.stderr)
        if path_prefix:
            print(f"[INFO] Using path prefix paths: {path_prefix}", file=sys.stderr)
        if module_srcs_abs:
            print(f"[INFO] Using module source paths: {module_srcs_abs}", file=sys.stderr)
    
    # 解析ftrace文件
    start_time = time.time()
    try:
        raw_lines, note = inputs.read_lines(args.ftrace_file, args.input_format, args.raw_instance)
    except (OSError, EOFError, lzma.LZMAError, inputs.raw.RawInputError) as e:
        print(f"Error: cannot read trace input: {e}", file=sys.stderr)
        sys.exit(1)
    if note:
        print(f"Input: {note}")
    pre = prefilter.Filter(pids=args.pid, comms=args.comm, cpus=args.cpu, time_range=args.time_range,
                           graph=args.graph, notrace=args.notrace)
    if pre.active():
        raw_lines, st = prefilter.apply(raw_lines, pre)
        print(f"Pre-filter ({pre.describe()}): kept {st['out']} of {st['in']} lines")
        if not any(l and not l.startswith('#') for l in raw_lines):
            print("Warning: the pre-filter kept no trace lines (check --pid/--comm/--graph/--time-range)", file=sys.stderr)
    parsed_lines = parse_ftrace_file(input_name(args), args.verbose, args.enable_fold, lines=raw_lines)
    parse_time = time.time() - start_time
    verbose_print(f"File parsing completed in {parse_time:.2f} seconds", args.verbose)

    if not vmlinux_path:
        for l in parsed_lines:       # nothing to resolve: no source blocks
            l.expandable = False

    # 构建时间轴数据（可选）
    timeline_json = None
    if args.timeline:
        timeline_json = build_timeline_data(parsed_lines, vmlinux_path, module_dirs, args)

    if args.export:
        from . import export, timeline as tlmod
        data = timeline_json or tlmod.build_timeline([l.raw_line for l in parsed_lines],
                                                     rettypes=load_rettypes(parsed_lines, vmlinux_path, module_dirs, args),
                                                     errno_map=ERROR_CODE_MAP,
                                                     overhead=parse_overhead(args.overhead)).to_data()
        ex = export.write(args.export, data, ERROR_CODE_MAP, input_name(args))
        print(f"Export: {args.export} ({len(ex['functions'])} functions, {len(ex['errors'])} error chains, "
              f"{len(ex['sections'])} critical sections)")

    compare_json = None
    if args.compare:
        try:
            compare_json = build_compare_data(args, raw_lines, parsed_lines, timeline_json, vmlinux_path, module_dirs)
        except (OSError, EOFError, lzma.LZMAError, inputs.raw.RawInputError) as error:
            print(f"Error: cannot read comparison input: {error}", file=sys.stderr)
            sys.exit(1)

    # 计算统计信息
    expandable_count = sum(1 for l in parsed_lines if l['expandable'])
    module_count = sum(1 for l in parsed_lines if l['expandable'] and l['module_name'])
    kernel_count = expandable_count - module_count

    # 生成HTML
    text_lines = parsed_lines
    if args.no_text_view:
        if not (timeline_json or compare_json):
            print("Error: --no-text-view needs --timeline or --compare", file=sys.stderr)
            sys.exit(1)
        if timeline_json is not None:
            timeline_json['no_text'] = True
        if not args.keep_source:
            text_lines = [l for l in parsed_lines if l.raw_line.startswith('#')][:200]
    elif len(parsed_lines) > 150000:
        print(f"Warning: {len(parsed_lines)} lines make a very large text view that browsers may fail to open; "
              "consider --no-text-view (with --timeline) or a pre-filter (--pid/--graph/--time-range)", file=sys.stderr)
    source_provider = None
    if (args.source_url or args.source_rev) and vmlinux_path:
        from . import sources
        try:
            source_provider = sources.make_provider(args, kernel_src_abs, [kernel_src_abs] + list(path_prefix))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    try:
        report_output, vmlinux_time, module_time = write_html(
            args.output,
            text_lines,
            vmlinux_path,
            faddr2line_path,
            report_mode=args.report_mode,
            omit_text=args.no_text_view and args.keep_source,
            module_dirs=module_dirs,  # 传递原始模块目录列表（用于auto-search）
            base_url=args.base_url,
            module_url=args.module_url,  # 传递模块URL
            kernel_src=kernel_src_abs,  # 传递单个绝对路径
            use_list=use_list,
            verbose=args.verbose,
            fast_mode=args.fast,  # 传递fast_mode参数
            highlight_code=args.highlight_code,  # 传递highlight_code参数
            path_prefix=path_prefix,  # 传递原始路径列表
            module_src=module_srcs_abs,  # 传递处理后的绝对路径列表（用于路径清理）
            module_srcs=module_srcs_abs,  # 传递处理后的绝对路径列表（用于fastfaddr2line查找源码）
            script_args=args,  # 传递命令行参数用于显示
            enable_filter=args.filter,  # 传递过滤器开关
            parse_time=parse_time,  # 传递解析时间
            total_time=0,  # 占位符，稍后计算
            func_links=args.func_links,  # 传递函数名超链接开关
            entry_offset=args.entry_offset,  # 传递函数入口地址偏移量
            enable_fold=args.enable_fold,  # 传递折叠功能开关
            timeline_json=timeline_json,
            compare_json=compare_json,
            source_provider=source_provider
        )
        verbose_print(f"Output written to: {args.output} ({report_output.byte_count} bytes)", args.verbose)
    except Exception as error:
        print(f"Error writing output file: {str(error)}", file=sys.stderr)
        sys.exit(1)

    # 计算总时间

    # 输出详细的统计信息到日志
    print("\n=== Processing Statistics ===")
    print(f"Trace file parsing: {parse_time:.2f}s")
    if vmlinux_time > 0:
        print(f"Vmlinux resolution: {vmlinux_time:.2f}s")
    if module_time > 0:
        print(f"Modules resolution: {module_time:.2f}s")
    print(f"Total processing time: {parse_time + vmlinux_time + module_time:.2f}s")
    print(f"Total lines: {len(parsed_lines)}")
    print(f"Expandable entries: {sum(1 for l in parsed_lines if l['expandable'])}")
    print("=============================\n")
    
    print(f"Generated interactive visualization: {args.output}")
    if args.report_mode == 'sidecar':
        print(f"Open with: fgview {shlex.quote(os.path.abspath(args.output))}")
        print('HTTP directory report: Timeline filtering loads an index; Text loads in full on first use; no automatic size threshold.')
    print(f"Processed {len(parsed_lines)} lines, with {expandable_count} expandable entries")
    print(f"Resolved {module_count} module functions, {kernel_count} kernel functions")
    
    if args.verbose:
        print("\n[SUMMARY]")
        print(f"Total lines processed: {len(parsed_lines)}")
        print(f"Expandable entries: {expandable_count}")
        print(f"  - Kernel functions: {kernel_count}")
        print(f"  - Module functions: {module_count}")
        print(f"Output file: {os.path.abspath(args.output)}")
