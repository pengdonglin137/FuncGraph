"""Command line interface: argument parsing, toolchain discovery, pipeline."""

import os
import subprocess
import argparse
import sys
import time

from .constants import ERROR_CODE_MAP, PROJECT_DIR
from . import prefilter
from .parser import parse_ftrace_file
from .report import generate_html
from .util import check_gawk_available, find_module_path, verbose_print


def build_compare_data(args, raw_lines, parsed_lines, timeline_json, vmlinux_path, module_dirs):
    """Compare tab data: per-function statistics of --compare BASE vs. this trace."""
    from . import compare, timeline as tlmod
    t0 = time.time()
    with open(args.compare, 'r', errors='replace') as f:
        base_lines = f.read().split('\n')
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
                           cur_name=os.path.basename(args.ftrace_file))
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
    if args.no_btf:
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
    st = data['stats']
    print(f"Timeline: {st['slices']} slices, {st['tasks']} tasks, mode={data['mode']}, "
          f"{len(data['chains'])} error chains, {st['offcpu']} off-CPU, {time.time() - t0:.2f}s")
    if data['n'] == 0:
        print("Note: no function_graph calls found; timeline tab omitted", file=sys.stderr)
        return None
    return data


def build_parser():
    """Command line options."""
    parser = argparse.ArgumentParser(description='Convert ftrace output to interactive HTML')
    parser.add_argument('ftrace_file', help='Path to ftrace output file')
    parser.add_argument('--vmlinux', required=True, help='Path to vmlinux file')
    parser.add_argument('--kernel-src', type=str,
                        help='Path to kernel source root')
    parser.add_argument('--module-dirs', nargs='*', default=[],
                        help='Directories to search for kernel modules')
    parser.add_argument('--module-srcs', nargs='*', default=[],
                        help='Module source code root directories (can specify multiple paths)')
    parser.add_argument('--base-url', help='Base URL for source code links')
    parser.add_argument('--module-url', action='append', help='Module URL mapping (can be specified multiple times, format: url:mod1,mod2)')
    parser.add_argument('--output', default='ftrace_viz.html', help='Output HTML file path')
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
    parser.add_argument('--compare', metavar='BASE_TRACE',
                        help='Add a Compare tab: per-function calls/self/inclusive time vs. this base trace')
    parser.add_argument('--no-text-view', action='store_true',
                        help='Only build the Timeline/Compare tabs (no per-line text view, no address resolution); '
                             'for traces too large for the text view (more than ~100k lines)')
    parser.add_argument('--timeline', action='store_true',
                        help='Add a Timeline tab: per-task flame chart over time with context/error-path coloring')
    parser.add_argument('--btf', nargs='*', default=None, metavar='FILE',
                        help='BTF sources for return-type checks (raw /sys/kernel/btf/* copies or ELF files). '
                             'Default: the .BTF section of --vmlinux and of resolved modules')
    parser.add_argument('--no-btf', action='store_true',
                        help='Do not use BTF; error paths are guessed from return values only')
    return parser


def validate_args(args):
    """Check paths/URLs; exits on fatal errors, warns otherwise."""
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
    if not os.path.exists(args.ftrace_file):
        print(f"Error: ftrace file '{args.ftrace_file}' does not exist", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.ftrace_file):
        print(f"Error: '{args.ftrace_file}' is not a regular file", file=sys.stderr)
        sys.exit(1)

    # 检查vmlinux文件是否存在
    if not os.path.exists(args.vmlinux):
        print(f"Error: vmlinux file '{args.vmlinux}' does not exist", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.vmlinux):
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


def main():
    args = build_parser().parse_args()
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
    faddr2line_path, cross_compile, llvm, util_prefix, util_suffix = find_faddr2line(args)

    # 使用绝对路径
    faddr2line_path = os.path.abspath(faddr2line_path)
    vmlinux_path = os.path.abspath(args.vmlinux)

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
    raw_lines = None
    pre = prefilter.Filter(pids=args.pid, comms=args.comm, cpus=args.cpu, time_range=args.time_range,
                           graph=args.graph, notrace=args.notrace)
    if pre.active():
        with open(args.ftrace_file, 'r', errors='replace') as f:
            raw_lines = f.read().split('\n')
        if raw_lines and raw_lines[-1] == '':
            raw_lines.pop()
        raw_lines, st = prefilter.apply(raw_lines, pre)
        print(f"Pre-filter ({pre.describe()}): kept {st['out']} of {st['in']} lines")
        if not any(l and not l.startswith('#') for l in raw_lines):
            print("Warning: the pre-filter kept no trace lines (check --pid/--comm/--graph/--time-range)", file=sys.stderr)
    parsed_lines = parse_ftrace_file(args.ftrace_file, args.verbose, args.enable_fold, lines=raw_lines)
    parse_time = time.time() - start_time
    verbose_print(f"File parsing completed in {parse_time:.2f} seconds", args.verbose)

    # 构建时间轴数据（可选）
    timeline_json = None
    if args.timeline:
        timeline_json = build_timeline_data(parsed_lines, vmlinux_path, module_dirs, args)

    compare_json = None
    if args.compare:
        compare_json = build_compare_data(args, raw_lines, parsed_lines, timeline_json, vmlinux_path, module_dirs)

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
        text_lines = [l for l in parsed_lines if l.raw_line.startswith('#')][:200]
    elif len(parsed_lines) > 150000:
        print(f"Warning: {len(parsed_lines)} lines make a very large text view that browsers may fail to open; "
              "consider --no-text-view (with --timeline) or a pre-filter (--pid/--graph/--time-range)", file=sys.stderr)
    html_content, vmlinux_time, module_time = generate_html(
        text_lines,
        vmlinux_path,
        faddr2line_path,
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
        compare_json=compare_json
    )

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
    
    # 写入输出文件
    try:
        with open(args.output, 'w') as f:
            f.write(html_content)
        verbose_print(f"Output written to: {args.output}", args.verbose)
    except Exception as e:
        print(f"Error writing output file: {str(e)}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Generated interactive visualization: {args.output}")
    print(f"Processed {len(parsed_lines)} lines, with {expandable_count} expandable entries")
    print(f"Resolved {module_count} module functions, {kernel_count} kernel functions")
    
    if args.verbose:
        print("\n[SUMMARY]")
        print(f"Total lines processed: {len(parsed_lines)}")
        print(f"Expandable entries: {expandable_count}")
        print(f"  - Kernel functions: {kernel_count}")
        print(f"  - Module functions: {module_count}")
        print(f"Output file: {os.path.abspath(args.output)}")
