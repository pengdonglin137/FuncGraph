"""HtmlGenerator: assemble the self-contained HTML report."""

import re
import os
import sys
import html
import glob
import threading
import time
import json
from collections import defaultdict

from .assets import COMPARE_CSS, COMPARE_JS, REPORT_CSS, REPORT_DATA_JS, REPORT_JS, TIMELINE_CSS, VIEWS_BOOT_JS, VIEWS_CSS, VIEWS_EARLY_JS, read_asset, timeline_script
from .constants import APP_AUTHOR, APP_TITLE, APP_VERSION, ERROR_CODE_MAP, PROJECT_DIR
from .inline import timeline_inline_map
from .output import ReportSpool
from .sources import rewrite as rewrite_sources
from .highlight import highlight_ftrace_line
from .resolver import call_faddr2line_batch, call_faddr2line_for_func_names, create_source_link, parse_list_output, parse_module_url
from .util import build_source_url, clean_file_path, escape_html_preserve_spaces, find_module_path, format_args_info, get_environment_info, get_relative_path, remove_compiler_suffix, verbose_print


class _Background:
    """Run fn(*args, **kw) in a thread; result() joins and re-raises errors."""

    def __init__(self, fn, *args, **kw):
        self._out = {}

        def run():
            try:
                self._out['value'] = fn(*args, **kw)
            except BaseException as e:      # surfaced in result()
                self._out['error'] = e
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def result(self):
        self._thread.join()
        if 'error' in self._out:
            raise self._out['error']
        return self._out['value']


class HtmlGenerator:
    """Generates self-contained interactive HTML visualization."""

    def __init__(self, parsed_lines, vmlinux_path, faddr2line_path,
                 module_dirs=None, base_url=None, module_url=None,
                 kernel_src=None, use_list=False, verbose=False,
                 fast_mode=False, highlight_code=False, path_prefix=None,
                 module_src=None, module_srcs=None, script_args=None,
                 enable_filter=False, parse_time=0, total_time=0,
                 func_links=False, entry_offset=0, enable_fold=False,
                 timeline_json=None, compare_json=None, source_provider=None):
        # Store all parameters as instance attributes
        self.parsed_lines = parsed_lines
        self.timeline_json = timeline_json
        self.compare_json = compare_json
        self.source_provider = source_provider
        self.source_note = None
        self._src_pool = []
        self._src_index = {}
        self._list_cache = {}
        self.vmlinux_path = vmlinux_path
        self.faddr2line_path = faddr2line_path
        self.module_dirs = module_dirs or []
        self.base_url = base_url
        self.module_url = module_url
        self.kernel_src = kernel_src
        self.use_list = use_list
        self.verbose = verbose
        self.fast_mode = fast_mode
        self.highlight_code = highlight_code
        self.path_prefix = path_prefix
        self.module_src = module_src
        self.module_srcs = module_srcs
        self.script_args = script_args
        self.enable_filter = enable_filter
        self.parse_time = parse_time
        self.total_time = total_time
        self.func_links = func_links
        self.entry_offset = entry_offset
        self.enable_fold = enable_fold

        # Computed state (filled during generate)
        self.vmlinux_time = 0
        self.module_time = 0
        self.filter_html = ""
        self.combined_module_url_map = {}
        self.default_module_url = base_url
        self.func_name_results = {}
        self.func_locations_map = {}
        self.vmlinux_funcs = set()
        self.module_funcs = defaultdict(set)

    def generate(self):
        """Generate the complete HTML string. Returns (html_str, vmlinux_time, module_time)."""
        html_str = ''.join(fragment for name, fragment in self.iter_sections())
        return html_str, self.vmlinux_time, self.module_time

    def iter_sections(self):
        """Yield named HTML sections without assembling the complete report in memory."""
        # 1. Pre-process data
        self._prepare_data()

        verbose_print("Generating HTML content", self.verbose)
        start_time = time.time()

        # 计算统计数据
        total_lines = len(self.parsed_lines)
        expandable_entries = sum(1 for l in self.parsed_lines if l['expandable'])

        # 生成信息面板内容
        info_items = format_args_info(self.script_args)
        env_items = get_environment_info()

        info_content_html = ""

        # 添加环境信息部分
        if env_items:
            info_content_html += '                <div class="info-section-title">Environment</div>\n'
            for label, value in env_items:
                info_content_html += f'                <div class="info-item"><div class="info-label">{label}:</div><div class="info-value">{html.escape(str(value))}</div></div>\n'
            info_content_html += '                <div class="info-section-divider"></div>\n'

        # 添加处理统计信息
        if self.parse_time > 0 or self.total_time > 0 or self.vmlinux_time > 0 or self.module_time > 0:
            info_content_html += '                <div class="info-section-title">Processing Stats</div>\n'
            if self.parse_time > 0:
                info_content_html += f'                <div class="info-item"><div class="info-label">Parse Time:</div><div class="info-value">{self.parse_time:.2f}s</div></div>\n'
            if self.vmlinux_time > 0:
                info_content_html += f'                <div class="info-item"><div class="info-label">Vmlinux Time:</div><div class="info-value">{self.vmlinux_time:.2f}s</div></div>\n'
            if self.module_time > 0:
                info_content_html += f'                <div class="info-item"><div class="info-label">Modules Time:</div><div class="info-value">{self.module_time:.2f}s</div></div>\n'
            if self.total_time > 0:
                info_content_html += f'                <div class="info-item"><div class="info-label">Total Time:</div><div class="info-value">{self.total_time:.2f}s</div></div>\n'
            info_content_html += f'                <div class="info-item"><div class="info-label">Total Lines:</div><div class="info-value">{len(self.parsed_lines)}</div></div>\n'
            info_content_html += f'                <div class="info-item"><div class="info-label">Expandable:</div><div class="info-value">{sum(1 for l in self.parsed_lines if l["expandable"])}</div></div>\n'
            if self.enable_fold:
                foldable_count = sum(1 for l in self.parsed_lines if l.get('fold_id') and l.get('fold_type') in ['entry', 'exit', 'inner'])
                info_content_html += f'                <div class="info-item"><div class="info-label">Foldable:</div><div class="info-value">{foldable_count}</div></div>\n'

        # 添加脚本参数部分
        if self.source_note:
            info_content_html += f'                <div class="info-item"><div class="info-label">Source:</div><div class="info-value">{html.escape(self.source_note)}</div></div>\n'
        if info_items:
            info_content_html += '                <div class="info-section-title">Parameters</div>\n'
            for label, value in info_items:
                info_content_html += f'                <div class="info-item"><div class="info-label">{label}:</div><div class="info-value">{html.escape(str(value))}</div></div>\n'

        if not env_items and not info_items and self.parse_time == 0:
            info_content_html = '                <div style="color: var(--summary-text); font-size: 12px;">No information available</div>'

        # 2. 构建HTML字符串
        html_str = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{APP_TITLE} v{APP_VERSION} - {APP_AUTHOR}</title>
    <style>
{self._generate_css()}{self._timeline_css()}    </style>
{self._early_view_script()}</head>
<body>
    <div class="progress-bar" id="progressBar"></div>
    <!-- 小悬浮切换按钮：用于显示/隐藏页面上的其他浮动控件 -->
    <button id="floatingToggle" class="floating-toggle" aria-label="Toggle floating controls" title="Toggle floating controls" onclick="handleFloatingToggleClick(event)">
        <span class="ft-icon" id="floatingToggleIcon">☰</span>
    </button>
    
    <div class="container">
        <div class="page-header">
            <span class="title-main">{APP_TITLE}</span>
            <span class="header-version">v{APP_VERSION}</span>
            <span class="header-sep">·</span>
            <span class="header-stats">
                <span>{total_lines} lines</span>
                <span class="header-sep">·</span>
                <span>{expandable_entries} expandable</span>
                <span class="header-sep">·</span>
                <span>{len(self.vmlinux_funcs)} kernel funcs</span>
                <span id="visibleLinesContainer" style="display: none;">
                    <span class="header-sep">·</span>
                    <span>Filtered: <span id="summaryVisibleLines">{total_lines}</span></span>
                </span>
            </span>
            <span class="header-actions">
                <button class="header-btn" onclick="toggleTheme()">Theme</button>
                <button class="header-btn" onclick="showKeyboardHint()" title="Keyboard shortcuts">?</button>
                <button class="header-btn" onclick="toggleInfoPanel()">ⓘ</button>
            </span>
        </div>

        <div class="info-panel" id="infoPanel">
            {{INFO_CONTENT_PLACEHOLDER}}
        </div>

{self._timeline_tabs()}        <!-- 控制按钮 - 水平工具栏 -->
        <div class="controls">
            <div id="filterPlaceholder"></div>
            <div class="toolbar">
                <div class="btn-group">
                    <button class="control-btn btn-nav" onclick="toggleFilterBox()">&#x1F50D; Filter</button>
                    <button class="control-btn btn-nav" onclick="scrollToTop()">Top</button>
                </div>
                <div class="btn-group">
                    <button class="control-btn btn-expand" onclick="expandAll()">Expand All</button>
                    <button class="control-btn btn-collapse" onclick="collapseAll()">Collapse All</button>
                </div>
                <div class="btn-group">
                    <button class="control-btn btn-tool" onclick="copyVisibleContent(event)">Copy</button>
                    <button class="control-btn btn-tool" id="heatmapBtn" onclick="toggleHeatmap()">Heatmap</button>
                    <button class="control-btn btn-tool" id="textOrderBtn" onclick="fgToggleTextOrder()" title="Line order: as captured (all CPUs interleaved by time) or grouped by task with context switches summarized">Order: time</button>
                    <button class="control-btn btn-tool" id="srcModeBtn" onclick="fgToggleSrcMode()" title="Source blocks: inline chain (outer to inner, collapsible) or flat faddr2line order">Source: chain</button>
                </div>
                <span class="heatmap-legend" id="heatmapLegend"></span>
            </div>
        </div>

        <div id="content">
"""

        def section(name, fragment):
            fragment = fragment.replace('{INFO_CONTENT_PLACEHOLDER}', info_content_html)
            if self.filter_html:
                fragment = fragment.replace('<div id="filterPlaceholder"></div>', self.filter_html)
            return name, fragment

        yield section('chrome', html_str)

        # 3. Generate content lines
        for fragment in self._iter_content_lines():
            yield section('text', fragment)

        yield section('chrome', """
        </div>""" + self._view_containers() + """
    </div>

    <div class="keyboard-hint" id="keyboardHint">
        <button class="keyboard-hint-close" onclick="closeKeyboardHint()">×</button>
        <h3>Keyboard Shortcuts</h3>
        <ul>
            <li><kbd>j</kbd>/<kbd>k</kbd> or <kbd>↓</kbd>/<kbd>↑</kbd>: Move to next/prev expandable line</li>
            <li><kbd>h</kbd>/<kbd>l</kbd>: Scroll left/right</li>
            <li><kbd>Enter</kbd>: Expand/collapse</li>
            <li><kbd>Space</kbd>: Scroll down</li>
            <li><kbd>Esc</kbd>: Clear selection</li>
        </ul>
        <h3>Function Call Folding</h3>
        <ul>
            <li>Click <strong>▶</strong> to fold/unfold; <kbd>Shift</kbd>+Click for recursive fold/unfold</li>
            <li><kbd>1</kbd>-<kbd>9</kbd>: Fold to depth N (hide calls deeper than N)</li>
            <li><kbd>0</kbd>: Unfold all fold blocks</li>
            <li>Folded blocks show summary: call count, total time, return value</li>
            <li>Folded blocks are saved in browser storage</li>
        </ul>
    </div>
""")
        for name, fragment in self._iter_source_parts():
            yield section(name, fragment)
        yield section('report_script', self._generate_javascript())
        for name, fragment in self._iter_view_parts():
            yield section(name, fragment)
        yield section('end', """</body>
</html>
""")

        elapsed = time.time() - start_time
        verbose_print(f"HTML generation completed in {elapsed:.2f} seconds", self.verbose)

    def _source_pool_script(self):
        return ''.join(fragment for name, fragment in self._iter_source_parts())

    def _iter_source_parts(self):
        yield 'source_open', '    <script type="application/json" id="fgSrc">'
        data = json.dumps(self._src_pool, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
        yield 'source_data', data
        yield 'source_close', '</script>\n'

    # ---- Optional views (Timeline, Compare): tabs, containers, CSS, data, scripts ----
    def _early_view_script(self):
        names = ' '.join(view[0] for view in self._views())
        default = 'timeline' if self.timeline_json and self.timeline_json.get('no_text') else 'text'
        return (f'    <script data-views="{names}" data-default-view="{default}">\n'
                + read_asset(VIEWS_EARLY_JS) + '    </script>\n')

    def _views(self):
        """[(name, label, badge, container_id, css, data_id, data, js)] for the enabled views."""
        v = []
        if self.timeline_json:
            v.append(('timeline', 'Timeline', f"{self.timeline_json.get('n', 0)} calls", 'timelineView',
                      read_asset(TIMELINE_CSS), 'tlData', self.timeline_json, timeline_script()))
        if self.compare_json:
            v.append(('compare', 'Compare', f"vs {self.compare_json['names'][0]}", 'compareView',
                      read_asset(COMPARE_CSS), 'cmpData', self.compare_json, read_asset(COMPARE_JS)))
        return v

    def _timeline_css(self):
        views = self._views()
        if not views:
            return ''
        return read_asset(VIEWS_CSS) + ''.join(x[4] for x in views) + '\n'

    def _timeline_tabs(self):
        views = self._views()
        if not views:
            return ''
        h = ('        <div class="view-tabs">\n'
             '            <button class="view-tab active" data-view="text" onclick="fgSwitchView(\'text\')">Text</button>\n')
        for name, label, badge, *_ in views:
            h += (f'            <button class="view-tab" data-view="{name}" onclick="fgSwitchView(\'{name}\')">'
                  f'{label}<span class="tab-badge">{html.escape(badge)}</span></button>\n')
        return h + '        </div>\n'

    def _view_containers(self):
        return ''.join(f'\n        <div id="{x[3]}" class="fg-view"></div>' for x in self._views())

    def _attach_inline(self):
        """Add inline-frame chains of call sites to the timeline data (needs resolved addresses)."""
        tl = self.timeline_json
        if not tl or 'inline' in tl:
            return
        tl['inline'] = {}
        if not self.func_locations_map:
            return
        ksrc = (self.kernel_src.rstrip('/') + '/') if self.kernel_src else None

        def rel(path):
            return path[len(ksrc):] if ksrc and path.startswith(ksrc) else path

        def lookup(caller):
            return self.func_locations_map.get(self._adjust_func_info(caller, remove_suffix=False))
        tl['inline'] = timeline_inline_map(tl['strings'], tl['cols'].get('caller'),
                                           lambda c: lookup(c))
        if ksrc:
            for chain in tl['inline'].values():
                for f in chain:
                    f[1] = rel(f[1])

    def _timeline_scripts(self):
        return ''.join(fragment for name, fragment in self._iter_view_parts())

    def _iter_view_parts(self):
        views = self._views()
        if not views:
            return
        self._attach_inline()
        for x in views:
            data = json.dumps(x[6], separators=(',', ':')).replace('<', '\\u003c')
            yield 'view_open:' + x[5], f'    <script type="application/json" id="{x[5]}">'
            yield 'view_data:' + x[5], data
            yield 'view_close:' + x[5], '</script>\n'
        # view code first (function declarations / registrations), then the boot code that wires tabs
        yield 'view_script', '    <script>\n' + ''.join(x[7] for x in views) + read_asset(VIEWS_BOOT_JS) + '    </script>\n'

    def _prepare_data(self):
        """Pre-process data: resolve func names, collect filter data, parse module URLs.

        Sets self.vmlinux_time, self.module_time, self.func_name_results,
        self.filter_html, self.combined_module_url_map, self.default_module_url,
        self.func_locations_map, self.vmlinux_funcs, self.module_funcs.
        """
        module_dirs = self.module_dirs if self.module_dirs is not None else []

        # 初始化时间统计
        vmlinux_time = 0
        module_time = 0

        # 如果启用函数名超链接，收集需要解析的函数名
        func_name_results = {}
        func_name_job = None
        if self.func_links:
            # 收集所有唯一的原始函数名（去重）和模块信息
            unique_func_names = set()
            func_module_map = {}  # 函数名 -> 模块名
            module_paths = {}     # 模块名 -> 模块文件路径

            for line_data in self.parsed_lines:
                raw_func_name = line_data.get('raw_func_name')
                module_name = line_data.get('module_name')

                if raw_func_name:
                    unique_func_names.add(raw_func_name)
                    # 如果有模块名，记录映射关系
                    if module_name:
                        func_module_map[raw_func_name] = module_name

            # 如果有模块函数，需要查找模块文件路径
            if func_module_map and module_dirs:
                for module_name in set(func_module_map.values()):
                    found = False
                    # 在 module_dirs 中查找模块文件
                    for module_dir in module_dirs:
                        # 尝试多种可能的模块名格式
                        # 内核加载模块时会将 - 替换为 _，所以需要双向查找
                        module_name_variants = [module_name]

                        # 如果模块名包含下划线，尝试替换为中划线
                        if '_' in module_name:
                            module_name_variants.append(module_name.replace('_', '-'))

                        # 如果模块名包含中划线，尝试替换为下划线
                        if '-' in module_name:
                            module_name_variants.append(module_name.replace('-', '_'))

                        # 去重
                        module_name_variants = list(dict.fromkeys(module_name_variants))

                        for variant in module_name_variants:
                            # 方法1: 直接查找精确的 .ko 文件
                            module_path = os.path.join(module_dir, f"{variant}.ko")
                            if os.path.exists(module_path):
                                module_paths[module_name] = module_path
                                found = True
                                break

                            # 方法2: 查找带版本号的模块（优先匹配精确名称）
                            # 先查找精确匹配
                            exact_files = glob.glob(os.path.join(module_dir, f"{variant}.ko"))
                            if exact_files:
                                module_paths[module_name] = exact_files[0]
                                found = True
                                break

                            # 再查找带后缀的模块，但优先选择最短的（最接近精确匹配的）
                            module_files = glob.glob(os.path.join(module_dir, f"{variant}*.ko"))
                            if module_files:
                                # 按文件名长度排序，优先选择最短的
                                module_files.sort(key=lambda x: len(os.path.basename(x)))
                                module_paths[module_name] = module_files[0]
                                found = True
                                break

                            # 方法3: 递归查找子目录
                            for root, dirs, files in os.walk(module_dir):
                                # 先查找精确匹配
                                if f"{variant}.ko" in files:
                                    full_path = os.path.join(root, f"{variant}.ko")
                                    module_paths[module_name] = full_path
                                    found = True
                                    break

                                # 再查找带后缀的模块
                                matching_files = [f for f in files if f.startswith(f"{variant}") and f.endswith(".ko")]
                                if matching_files:
                                    # 按文件名长度排序
                                    matching_files.sort(key=lambda x: len(x))
                                    full_path = os.path.join(root, matching_files[0])
                                    module_paths[module_name] = full_path
                                    found = True
                                    break

                            if found:
                                break

                        if found:
                            break

            if unique_func_names and self.vmlinux_path and self.faddr2line_path:
                verbose_print(f"收集到 {len(unique_func_names)} 个唯一函数名用于解析", self.verbose)
                if func_module_map:
                    verbose_print(f"其中 {len(func_module_map)} 个是模块函数", self.verbose)

                # 调用 faddr2line 解析函数名（使用原始函数名）
                func_name_list = list(unique_func_names)
                # Runs in the background: it is an independent faddr2line process, and the
                # return-address batch below can resolve at the same time. Joined before use.
                func_name_job = _Background(
                    call_faddr2line_for_func_names,
                    self.vmlinux_path, self.faddr2line_path, func_name_list,
                    use_list=self.use_list, verbose=self.verbose, fast_mode=self.fast_mode,
                    path_prefix=self.path_prefix, module_srcs=self.module_srcs,
                    entry_offset=self.entry_offset,
                    func_module_map=func_module_map,
                    module_paths=module_paths
                )

        # 根据enable_filter参数生成统一过滤框HTML
        filter_html = ""
        if self.enable_filter:
            # 收集所有唯一的CPU、PID、进程名和错误码用于字段按钮
            unique_cpus = set()
            unique_pids = set()
            unique_comms = set()
            unique_error_codes = set()
            unique_irqs = set()
            unique_resched = set()
            unique_hirq = set()
            unique_preempt = set()

            has_params = False
            has_duration = False
            has_funcs = False

            for line_data in self.parsed_lines:
                cpu = line_data.get('cpu')
                pid = line_data.get('pid')
                comm = line_data.get('comm')
                ret_value = line_data.get('ret_value')

                if cpu is not None:
                    cpu_str = str(cpu)
                    if cpu_str.strip() and cpu_str.isdigit():
                        unique_cpus.add(cpu_str)

                if pid is not None:
                    pid_str = str(pid)
                    if pid_str.strip() and pid_str.isdigit():
                        unique_pids.add(pid_str)

                if comm:
                    comm_str = str(comm).strip()
                    if comm_str:
                        cleaned_comm = comm_str
                        if cleaned_comm.startswith('<') and cleaned_comm.endswith('>'):
                            cleaned_comm = cleaned_comm[1:-1]
                        if cleaned_comm.startswith('(') and cleaned_comm.endswith(')'):
                            cleaned_comm = cleaned_comm[1:-1]
                        if '@' in cleaned_comm:
                            cleaned_comm = cleaned_comm.split('@')[0]
                        if cleaned_comm:
                            unique_comms.add(cleaned_comm)

                if ret_value:
                    try:
                        if ret_value.startswith('0x') or ret_value.startswith('0X'):
                            ret_int = int(ret_value, 16)
                            if ret_int >= 0x8000000000000000:
                                converted = ret_int - 0x10000000000000000
                                if converted < 0:
                                    ret_int = converted
                        else:
                            ret_int = int(ret_value)
                        if ret_int in ERROR_CODE_MAP:
                            unique_error_codes.add(ret_int)
                    except ValueError:
                        pass

                if not has_params and line_data.get('params'):
                    has_params = True
                if not has_duration and line_data.get('duration') is not None:
                    has_duration = True
                if not has_funcs and line_data.get('display_func_name'):
                    has_funcs = True

                # 收集 latency 字段值
                if line_data.get('irqs_off') is not None:
                    unique_irqs.add(line_data.irqs_off)
                if line_data.get('need_resched') is not None:
                    unique_resched.add(line_data.need_resched)
                if line_data.get('hardirq_softirq') is not None:
                    unique_hirq.add(line_data.hardirq_softirq)
                if line_data.get('preempt_depth') is not None:
                    unique_preempt.add(line_data.preempt_depth)

            # 构建字段按钮 HTML
            field_buttons = []

            if unique_cpus:
                vals = ','.join(sorted(unique_cpus, key=lambda x: int(x)))
                field_buttons.append(f'<button class="field-btn" data-field="cpu" data-values="{vals}">cpu</button>')

            if unique_pids:
                sorted_pids = sorted(unique_pids, key=lambda x: int(x))
                vals = ','.join(sorted_pids)
                field_buttons.append(f'<button class="field-btn" data-field="pid" data-values="{vals}">pid</button>')

            if unique_comms:
                vals = ','.join(sorted(unique_comms))
                field_buttons.append(f'<button class="field-btn" data-field="comm" data-values="{vals}">comm</button>')

            if unique_error_codes:
                # 构建 ret 按钮：值为错误码数字，hints 为宏名映射
                error_vals = []
                error_hints = []
                for ret_int in sorted(unique_error_codes):
                    error_vals.append(str(ret_int))
                    error_name = ERROR_CODE_MAP.get(ret_int, '')
                    if error_name:
                        error_hints.append(f"{ret_int}={error_name}")
                vals = ','.join(error_vals)
                hints = ','.join(error_hints)
                field_buttons.append(f'<button class="field-btn" data-field="ret" data-values="all,{vals}" data-hints="{hints}">ret</button>')

            if has_params:
                field_buttons.append('<button class="field-btn" data-field="params" data-values="">params</button>')

            if has_duration:
                field_buttons.append('<button class="field-btn" data-field="duration" data-values="">duration</button>')

            # latency 字段按钮（仅在 latency trace 时出现）
            if unique_irqs:
                vals = ','.join(sorted(unique_irqs))
                field_buttons.append(f'<button class="field-btn" data-field="irqs" data-values="{vals}" data-hints="d=disabled,.=enabled">irqs</button>')

            if unique_resched:
                vals = ','.join(sorted(unique_resched))
                field_buttons.append(f'<button class="field-btn" data-field="resched" data-values="{vals}" data-hints="B=NEED_RESCHED+LAZY,N=NEED_RESCHED,n=NEED_RESCHED_LAZY,p=PREEMPT_NEED,.=none">resched</button>')

            if unique_hirq:
                vals = ','.join(sorted(unique_hirq))
                field_buttons.append(f'<button class="field-btn" data-field="hirq" data-values="{vals}" data-hints="H=hardirq,h=hardirq(idle),s=softirq,Z=NMI,z=NMI(idle),.=none">hirq</button>')

            if unique_preempt:
                vals = ','.join(sorted(unique_preempt))
                field_buttons.append(f'<button class="field-btn" data-field="preempt" data-values="{vals}">preempt</button>')

            # func/graph/notrace 字段按钮（文本输入模式，不带 data-values）
            if has_funcs:
                field_buttons.append('<button class="field-btn" data-field="func" data-values="">func</button>')
                field_buttons.append('<button class="field-btn" data-field="graph" data-values="">graph</button>')
                field_buttons.append('<button class="field-btn" data-field="notrace" data-values="">notrace</button>')

            if field_buttons:
                # 将所有错误码值存储在隐藏属性中（用于 ret:all 过滤）
                error_values_json = ''
                if unique_error_codes:
                    error_values_json = '[' + ','.join(str(v) for v in sorted(unique_error_codes)) + ']'

                filter_html = f'''
            <div class="filter-box" id="filterBox">
                <div class="filter-tags" id="filterTags"></div>
                <div class="filter-input-row">
                    <input type="text" id="filterInput" placeholder="field:value, e.g. cpu:3 AND duration:>10 graph:funcname sort:desc" autocomplete="off" data-error-values="{error_values_json}">
                    <button class="control-btn filter-btn" onclick="applyFilter()">Apply</button>
                    <button class="control-btn clear-btn" onclick="clearFilter()">Clear</button>
                    <button class="control-btn filter-help-btn" onclick="toggleFilterHelp()" title="Filter syntax help">?</button>
                </div>
                <div class="filter-fields" id="filterFields">
                    {''.join(field_buttons)}
                </div>
                <div class="filter-syntax-help" id="filterSyntaxHelp">
                    <div class="syntax-help-header">
                        <span>Filter Syntax</span>
                        <span class="syntax-help-close" onclick="toggleFilterHelp()">&times;</span>
                    </div>
                    <div class="syntax-help-body">
                        <div class="syntax-section">
                            <div class="syntax-title">Basic</div>
                            <div class="syntax-example">field:value</div>
                            <div class="syntax-desc">e.g. <code>cpu:3</code> <code>comm:bash</code> <code>pid:1234</code></div>
                        </div>
                        <div class="syntax-section">
                            <div class="syntax-title">Logic</div>
                            <div class="syntax-example">expr AND expr &nbsp; expr OR expr &nbsp; NOT expr &nbsp; (expr)</div>
                            <div class="syntax-desc">e.g. <code>cpu:0 AND pid:431</code> &nbsp; <code>irqs:d OR hirq:h</code> &nbsp; <code>NOT comm:idle</code></div>
                        </div>
                        <div class="syntax-section">
                            <div class="syntax-title">Comparison</div>
                            <div class="syntax-example">duration:&gt;10 &nbsp; duration:&lt;=5 &nbsp; preempt:&gt;1</div>
                            <div class="syntax-desc">Operators: <code>&gt;</code> <code>&lt;</code> <code>&gt;=</code> <code>&lt;=</code> (for duration and preempt)</div>
                        </div>
                        <div class="syntax-section">
                            <div class="syntax-title">Graph</div>
                            <div class="syntax-example">graph:func &nbsp; notrace:func &nbsp; func:name</div>
                            <div class="syntax-desc"><code>graph:</code> show function call tree; <code>notrace:</code> hide call tree; <code>func:</code> match function name only</div>
                        </div>
                        <div class="syntax-section">
                            <div class="syntax-title">Special</div>
                            <div class="syntax-example">ret:all &nbsp; sort:desc &nbsp; sort:asc</div>
                            <div class="syntax-desc"><code>ret:all</code> matches all error codes; <code>sort:desc/asc</code> sorts by duration</div>
                        </div>
                        <div class="syntax-section">
                            <div class="syntax-title">Fields</div>
                            <div class="syntax-desc syntax-fields-list">
                                <code>cpu</code> <code>pid</code> <code>comm</code> <code>ret</code> <code>params</code> <code>duration</code> <code>func</code> <code>graph</code> <code>notrace</code> <code>irqs</code> <code>resched</code> <code>hirq</code> <code>preempt</code>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="filter-hint" id="filterHint"></div>'''

        # 解析module_url参数（支持多个--module-url参数）
        if self.module_url is None:
            module_url_list = []
        elif isinstance(self.module_url, list):
            module_url_list = self.module_url
        else:
            module_url_list = [self.module_url]

        # 合并所有module_url参数的解析结果
        combined_module_url_map = {}
        default_module_url = self.base_url

        for module_url_str in module_url_list:
            url_map, default_url = parse_module_url(module_url_str, self.base_url)

            # 合并映射
            combined_module_url_map.update(url_map)

            # 如果这个参数提供了默认URL（即没有跟模块名），更新默认URL
            # （后面的参数会覆盖前面的）
            if default_url != self.base_url:
                default_module_url = default_url

        # 如果没有提供任何module_url参数，使用base_url作为默认
        if not module_url_list:
            default_module_url = self.base_url

        verbose_print(f"Module URL map: {combined_module_url_map}", self.verbose)
        verbose_print(f"Default module URL: {default_module_url}", self.verbose)

        # 验证module_url_map中的模块名是否在parsed_lines中存在
        if combined_module_url_map and self.parsed_lines:
            available_modules = set()
            for line_data in self.parsed_lines:
                if line_data.get('module_name'):
                    available_modules.add(line_data['module_name'])

            # 检查是否有未使用的模块映射
            unused_mappings = set(combined_module_url_map.keys()) - available_modules
            if unused_mappings:
                print(f"Warning: module-url specifies mappings for modules not found in trace: {', '.join(sorted(unused_mappings))}", file=sys.stderr)

            # 检查是否有模块没有URL映射但default_module_url不是base_url
            if default_module_url and default_module_url != self.base_url:
                unmapped_modules = available_modules - set(combined_module_url_map.keys())
                if unmapped_modules:
                    verbose_print(f"Modules using default URL '{default_module_url}': {', '.join(sorted(unmapped_modules))}", self.verbose)

        # 确保path_prefix、module_src和module_srcs是列表
        if self.path_prefix and not isinstance(self.path_prefix, list):
            self.path_prefix = [self.path_prefix]
        if self.module_src and not isinstance(self.module_src, list):
            self.module_src = [self.module_src]
        if self.module_srcs and not isinstance(self.module_srcs, list):
            self.module_srcs = [self.module_srcs]

        # kernel_src保持单个路径（可以是None）

        # 使用绝对路径
        abs_vmlinux_path = os.path.abspath(self.vmlinux_path) if self.vmlinux_path else None
        abs_faddr2line_path = os.path.abspath(self.faddr2line_path) if self.faddr2line_path else ''

        # 如果使用fast模式，优先使用fastfaddr2line.py
        if self.fast_mode:
            script_dir = PROJECT_DIR
            fast_faddr2line_path = os.path.join(script_dir, 'fastfaddr2line.py')
            if os.path.exists(fast_faddr2line_path):
                verbose_print(f"Using fastfaddr2line.py at {fast_faddr2line_path}", self.verbose)
                abs_faddr2line_path = fast_faddr2line_path
            else:
                verbose_print(f"Warning: fastfaddr2line.py not found at {fast_faddr2line_path}, using fallback", self.verbose)

        # 收集所有需要解析的函数信息（使用字典去重）
        func_info_map = defaultdict(list)  # func_info -> [line_index]
        module_funcs = defaultdict(set)   # module_name -> set(func_info)
        vmlinux_funcs = set()              # set(func_info)

        for idx, line_data in enumerate(self.parsed_lines):
            if line_data['expandable'] and line_data['func_info']:
                func_info = line_data['func_info']

                # 记录行索引
                func_info_map[func_info].append(idx)

                # 从func_info中提取模块名（如果存在）
                # func_info格式: func+offset/length [module]
                func_info_module_name = None
                if '[' in func_info and ']' in func_info:
                    module_match = re.search(r'\[(.*?)\]', func_info)
                    if module_match:
                        func_info_module_name = module_match.group(1)

                # 优先使用func_info中的模块名
                # 如果func_info没有模块名，则默认为内核函数（None）
                # 不使用当前函数的模块名，因为返回地址可能属于不同的模块或内核
                effective_module = func_info_module_name

                # 按目标分组
                if effective_module:
                    module_funcs[effective_module].add(func_info)
                else:
                    vmlinux_funcs.add(func_info)

        verbose_print(f"Found {len(vmlinux_funcs)} unique kernel functions", self.verbose)
        verbose_print(f"Found {len(module_funcs)} modules with functions", self.verbose)
        for module, funcs in module_funcs.items():
            verbose_print(f"Module {module}: {len(funcs)} functions", self.verbose)

        # 批量获取所有函数的位置信息
        func_locations_map = {}

        # 1. 处理内核函数（去重后）
        if vmlinux_funcs:
            # 为调用fastfaddr2line准备函数列表（只调整偏移量，保留编译器后缀）
            vmlinux_funcs_for_call = set()
            for func in vmlinux_funcs:
                # 只调整偏移量，不移除编译器后缀
                adjusted_func = self._adjust_func_info(func, remove_suffix=False)
                vmlinux_funcs_for_call.add(adjusted_func)

            vmlinux_funcs_list = list(vmlinux_funcs_for_call)
            verbose_print(f"Processing {len(vmlinux_funcs_list)} kernel functions", self.verbose)

            # 记录vmlinux解析开始时间
            vmlinux_start = time.time()

            # 如果是fast模式且处理的是vmlinux，使用fastfaddr2line.py
            # 对于内核函数，不传递模块相关的路径参数
            if self.fast_mode and os.path.basename(abs_faddr2line_path) == 'fastfaddr2line.py':
                batch_results = call_faddr2line_batch(
                    abs_faddr2line_path,
                    abs_vmlinux_path,
                    vmlinux_funcs_list,
                    self.use_list,
                    self.kernel_src,
                    self.verbose,
                    self.path_prefix,
                    None,  # 内核函数不需要模块源码路径
                    None   # 内核函数不需要模块源码路径
                )
            else:
                batch_results = call_faddr2line_batch(
                    abs_faddr2line_path,
                    abs_vmlinux_path,
                    vmlinux_funcs_list,
                    self.use_list,
                    self.kernel_src,
                    self.verbose,
                    None,  # 原生faddr2line不支持path_prefix
                    None,  # 内核函数不需要模块源码路径
                    None   # 原生faddr2line不支持module_srcs
                )

            vmlinux_time = time.time() - vmlinux_start
            func_locations_map.update(batch_results)
            verbose_print(f"Resolved {len(batch_results)} kernel function locations in {vmlinux_time:.2f}s", self.verbose)

        # 2. 处理模块函数（去重后）
        module_start = time.time()
        for module_name, funcs in module_funcs.items():
            verbose_print(f"Processing module {module_name} with {len(funcs)} functions", self.verbose)
            # 查找模块文件
            module_path = find_module_path(module_name, self.module_dirs, self.verbose)

            if not module_path or not os.path.isfile(module_path):
                verbose_print(f"Module file not found for {module_name}", self.verbose)
                continue

            verbose_print(f"Using module file: {module_path}", self.verbose)

            # 为调用fastfaddr2line准备函数列表（只调整偏移量，保留编译器后缀）
            funcs_for_call = set()
            for func in funcs:
                # 只调整偏移量，不移除编译器后缀
                adjusted_func = self._adjust_func_info(func, remove_suffix=False)
                funcs_for_call.add(adjusted_func)

            funcs_list = list(funcs_for_call)
            # 如果使用fastfaddr2line.py，传递path_prefix和module_srcs；否则传递None
            should_pass_fast_args = self.fast_mode and os.path.basename(abs_faddr2line_path) == 'fastfaddr2line.py'
            batch_results = call_faddr2line_batch(
                abs_faddr2line_path,
                module_path,
                funcs_list,
                self.use_list,
                self.kernel_src,
                self.verbose,
                self.path_prefix if should_pass_fast_args else None,
                self.module_src,
                self.module_srcs if should_pass_fast_args else None
            )
            func_locations_map.update(batch_results)
            verbose_print(f"Resolved {len(batch_results)} function locations for module {module_name}", self.verbose)

        module_time = time.time() - module_start
        if module_funcs:
            verbose_print(f"Resolved all modules in {module_time:.2f}s", self.verbose)

        # Store all computed state
        self.vmlinux_time = vmlinux_time
        self.module_time = module_time
        if func_name_job is not None:
            func_name_results = func_name_job.result()
            verbose_print(f"函数名解析完成，获取 {len(func_name_results)} 个结果", self.verbose)
            # 将解析结果存储到 parsed_lines 中
            for line_data in self.parsed_lines:
                raw_func_name = line_data.get('raw_func_name')
                if raw_func_name and raw_func_name in func_name_results:
                    line_data['func_name_info'] = func_name_results[raw_func_name]
        self.func_name_results = func_name_results
        self.filter_html = filter_html
        self.combined_module_url_map = combined_module_url_map
        self.default_module_url = default_module_url
        if self.source_provider is not None and func_locations_map:
            t0 = time.time()
            func_locations_map, st = rewrite_sources(func_locations_map, self.source_provider)
            ratio = st['match_ratio']
            self.source_note = (f"{self.source_provider.describe()}: {st['files']} files"
                                f"{' (' + str(st['missing_files']) + ' not found)' if st['missing_files'] else ''}, "
                                f"{st['with_source']}/{st['positions']} positions with source"
                                + (f", {ratio * 100:.0f}% match the function names" if ratio is not None else ''))
            print(f"Source: {self.source_note} ({time.time() - t0:.1f}s)")
            if ratio is not None and ratio < 0.75:
                print("Warning: the source does not seem to match the vmlinux (function names are not found "
                      "at the reported lines); check --source-rev", file=sys.stderr)
                self.source_note += ' - WARNING: probably a different version than the vmlinux'
        self.func_locations_map = func_locations_map
        self.vmlinux_funcs = vmlinux_funcs
        self.module_funcs = module_funcs

    @staticmethod
    def _adjust_func_info(func_info, remove_suffix=True):
        """
        调整函数信息，用于显示或调用fastfaddr2line

        参数:
            func_info: 函数信息字符串，格式: func+offset/length [module]
            remove_suffix: 是否移除编译器后缀（用于显示），默认True
                          设为False时保留后缀（用于调用fastfaddr2line）
        """
        # 先提取模块名（如果存在）
        module_part = ''
        if '[' in func_info and ']' in func_info:
            module_match = re.search(r'(\s*\[.*?\])', func_info)
            if module_match:
                module_part = module_match.group(1)
                func_info = func_info.replace(module_part, '')

        # 匹配函数名+偏移/长度格式
        match = re.match(r'^(.*?)([+][0-9a-fA-FxX]+)(/[0-9a-fA-FxX]+)$', func_info)
        if not match:
            # 如果不匹配，恢复模块名并返回
            return func_info + module_part

        func_name = match.group(1)
        offset_str = match.group(2)[1:]  # 去掉前面的+号
        length_str = match.group(3)

        try:
            # 解析十六进制偏移量
            offset_val = int(offset_str, 16)

            # 根据参数决定是否去除编译器后缀
            if remove_suffix:
                cleaned_func = remove_compiler_suffix(func_name)
            else:
                cleaned_func = func_name

            if offset_val == 0:
                # 偏移量为0，但可能仍需要去除后缀（如果remove_suffix=True）
                if remove_suffix and cleaned_func != func_name:
                    return f"{cleaned_func}+{offset_str}{length_str}{module_part}"
                return func_info + module_part  # 偏移量为0，不需要调整

            # 计算新偏移量（减1）
            new_offset_val = offset_val - 1
            new_offset_str = hex(new_offset_val)

            # 重构函数信息字符串
            return f"{cleaned_func}+{new_offset_str}{length_str}{module_part}"
        except ValueError:
            return func_info + module_part  # 解析失败，返回原始字符串

    @staticmethod
    def _extract_line_number(location_str, kernel_src, path_prefix=None, module_src=None):
        """从位置字符串中提取行号"""
        if not location_str:
            return None

        # 尝试匹配各种行号格式
        # 1. 标准格式: file.c:1234
        # 2. 带discriminator: file.c:1234 (discriminator 9)
        # 3. 带inlined: file.c:1234 (inlined)
        # 4. 其他格式

        # 首先清理字符串

        # 尝试从原始字符串提取行号
        line_match = re.search(r':(\d+)(?:\s|$)', location_str)
        if line_match:
            try:
                return int(line_match.group(1))
            except (ValueError, IndexError):
                pass

        # 如果上述方法失败，尝试其他方法
        # 查找冒号后的数字
        match = re.search(r':(\d+)', location_str)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                pass

        return None

    @staticmethod
    def _extract_file_path(location_str, kernel_src, path_prefix=None, module_src=None):
        """从位置字符串中提取文件路径"""
        if not location_str:
            return ""

        # 清理字符串
        cleaned = clean_file_path(location_str, kernel_src, path_prefix, module_src)

        # 如果还有冒号和数字，去除它们
        # 匹配文件路径:行号格式
        file_match = re.match(r'^(.*?):\d+', cleaned)
        if file_match:
            return file_match.group(1)

        # 如果没有行号，直接返回清理后的路径
        return cleaned

    def _generate_content_lines(self):
        """Generate HTML for all ftrace content lines.

        Returns a string of all line-container divs.
        """
        return ''.join(self._iter_content_lines())

    def _iter_content_lines(self):
        """Yield each trace row with its source placeholder, retaining stable source IDs."""

        # 添加行号并输出原始ftrace日志
        for idx, line_data in enumerate(self.parsed_lines):
            content_parts = []
            line_number = idx + 1  # 行号从1开始
            line_anchor_id = f"L{line_number}"  # 使用L{行号}格式作为锚点
            line_id = f"line_{idx}"  # 保留用于JavaScript引用

            # 处理原始行：去除编译器优化后缀，用于显示
            raw_line = line_data["raw_line"]

            # 优先处理函数调用名称（func_name）- 适用于可展开和不可展开的行
            if line_data.get('raw_func_name'):
                raw_func_name = line_data['raw_func_name']
                display_name = line_data.get('display_func_name', raw_func_name)

                # 检查函数名是否在注释中（在 /**/ 之间）
                # 如果是，则不添加超链接
                in_comment = False
                comment_start = raw_line.find('/*')
                comment_end = raw_line.find('*/')
                if comment_start != -1 and comment_end != -1:
                    # 检查函数名是否在注释范围内
                    func_pos = raw_line.find(raw_func_name)
                    if comment_start < func_pos < comment_end:
                        in_comment = True

                # 如果启用了函数名超链接且有解析结果且不在注释中
                if self.func_links and line_data.get('func_name_info') and not in_comment:
                    source_file, line_num = line_data['func_name_info']
                    # 获取模块信息（如果适用）
                    module_name = line_data.get('module_name')
                    # 确定使用哪个URL（模块特定URL或默认URL）
                    current_base_url = self.base_url
                    if module_name:
                        if module_name in self.combined_module_url_map:
                            current_base_url = self.combined_module_url_map[module_name]
                        elif self.default_module_url and self.default_module_url != self.base_url:
                            current_base_url = self.default_module_url
                    # 生成超链接（使用显示名）
                    link_html = create_source_link(source_file, line_num, display_name, self.base_url, self.kernel_src, self.module_srcs, module_name, current_base_url)
                    # 替换原始行中的原始函数名为显示名
                    if raw_func_name != display_name:
                        raw_line = raw_line.replace(raw_func_name, display_name)
                    # 然后将显示名替换为超链接
                    raw_line = raw_line.replace(display_name, link_html)
                else:
                    # 仅替换为显示名
                    if raw_func_name != display_name:
                        raw_line = raw_line.replace(raw_func_name, display_name)

            # 然后处理返回地址（func_info）- 仅适用于可展开的行
            elif line_data['expandable'] and line_data.get('func_info'):
                func_info = line_data['func_info']
                display_func = remove_compiler_suffix(func_info)
                if func_info != display_func:
                    raw_line = raw_line.replace(func_info, display_func)

            # 先应用语法高亮，保持原始空格和对齐
            # 但是我们需要保护已经生成的 HTML 链接不被转义
            # 使用占位符临时保存 HTML 链接
            html_links = []
            def save_html_link(match):
                idx = len(html_links)
                html_links.append(match.group(0))
                return f'___HTML_LINK_{idx}___'

            # 临时替换 HTML 链接为占位符
            raw_line_with_placeholders = re.sub(r'<a class="func-name-link"[^>]*>.*?</a>', save_html_link, raw_line)

            # 应用语法高亮（会转义 HTML）
            escaped_line = highlight_ftrace_line(raw_line_with_placeholders)

            # 恢复 HTML 链接
            for idx, link in enumerate(html_links):
                escaped_line = escaped_line.replace(f'___HTML_LINK_{idx}___', link)

            # 检查是否可展开
            is_expandable = line_data['expandable'] and line_data['func_info']
            expandable_class = "expandable" if is_expandable else ""

            # 获取折叠信息
            fold_id = line_data.get('fold_id')
            fold_type = line_data.get('fold_type')
            call_depth = line_data.get('call_depth')

            # 检查是否是可折叠的函数调用块
            is_foldable = self.enable_fold and fold_id is not None and fold_type in ['entry', 'exit', 'inner']
            fold_class = ""
            fold_marker = ""

            if is_foldable:
                if fold_type == 'entry':
                    fold_class = "fold-entry"
                    # 默认展开状态：使用向下箭头表示展开（可通过 CSS/JS 旋转和替换）
                    fold_marker = '<span class="fold-icon expanded">▶</span>'  # 折叠标记（默认展开）
                elif fold_type == 'exit':
                    fold_class = "fold-exit"
                    fold_marker = '<span class="fold-guide"></span>'
                elif fold_type == 'inner':
                    fold_class = "fold-inner"
                    fold_marker = '<span class="fold-guide"></span>'

            # 获取CPU、PID、进程名信息，用于过滤
            cpu = line_data.get('cpu')
            pid = line_data.get('pid')
            comm = line_data.get('comm')
            ret_value = line_data.get('ret_value')
            params = line_data.get('params')
            duration = line_data.get('duration')  # 耗时（微秒）- 用于过滤（原始显示值）
            sort_duration = line_data.get('sort_duration')  # 排序用的实际值
            duration_raw = line_data.get('duration_raw')  # 原始耗时字符串

            # 构建数据属性用于过滤
            data_attrs = f' data-cpu="{cpu if cpu is not None else ""}" data-pid="{pid if pid is not None else ""}" data-comm="{comm if comm else ""}"'

            # 添加折叠属性
            if is_foldable:
                # fold_ancestors 作为空格分隔的 token 列表，便于在 JS 中使用 ~= 选择器匹配
                ancestors_attr = ''
                if line_data.get('fold_ancestors'):
                    ancestors_attr = line_data.get('fold_ancestors')
                data_attrs += f' data-fold-id="{fold_id}" data-fold-type="{fold_type}"'
                if call_depth is not None:
                    data_attrs += f' data-call-depth="{call_depth}"'
                if ancestors_attr:
                    data_attrs += f' data-fold-ancestors="{ancestors_attr}"'

            # 添加函数名属性（用于 func/graph/notrace 过滤）
            display_func = line_data.get('display_func_name')
            if display_func:
                escaped_func = display_func.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
                data_attrs += f' data-func="{escaped_func}"'

            # 添加参数属性（用于参数过滤）
            if params:
                # 转义特殊字符，避免HTML属性问题
                # 使用html.escape，但只转义引号、尖括号
                escaped_params = params.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
                data_attrs += f' data-params="{escaped_params}"'

            # 添加耗时属性（用于耗时过滤和排序）
            if duration is not None:
                data_attrs += f' data-duration="{duration}"'
                if sort_duration is not None and sort_duration != duration:
                    data_attrs += f' data-sort-duration="{sort_duration}"'
                if duration_raw:
                    escaped_duration = duration_raw.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
                    data_attrs += f' data-duration-raw="{escaped_duration}"'
                    # Heatmap level based on ftrace duration prefix character
                    prefix_char = duration_raw.lstrip()[0] if duration_raw.lstrip() and duration_raw.lstrip()[0] in '$@*#!+' else ' '
                    heat_map = {'$': '7', '@': '6', '*': '5', '#': '4', '!': '3', '+': '2', ' ': '1'}
                    data_attrs += f' data-heat="{heat_map.get(prefix_char, "1")}"'

            # 添加返回值属性（用于错误码过滤）
            if ret_value:
                try:
                    ret_lower = ret_value.lower()
                    if ret_lower == 'true' or ret_lower == 'false':
                        # B6: Store boolean return values as data-ret-bool
                        data_attrs += f' data-ret-bool="{ret_lower}"'
                    elif ret_value.startswith('0x') or ret_value.startswith('0X'):
                        ret_int = int(ret_value, 16)
                        if ret_int >= 0x8000000000000000:
                            converted = ret_int - 0x10000000000000000
                            if converted < 0:
                                ret_int = converted
                        if ret_int in ERROR_CODE_MAP:
                            data_attrs += f' data-ret="{ret_int}"'
                    else:
                        ret_int = int(ret_value)
                        if ret_int in ERROR_CODE_MAP:
                            data_attrs += f' data-ret="{ret_int}"'
                except ValueError:
                    pass

            # 添加 latency 字段属性（irqs-off, need-resched, hardirq/softirq, preempt-depth）
            if line_data.get('irqs_off') is not None:
                data_attrs += f' data-irqs="{line_data.irqs_off}"'
            if line_data.get('need_resched') is not None:
                data_attrs += f' data-resched="{line_data.need_resched}"'
            if line_data.get('hardirq_softirq') is not None:
                data_attrs += f' data-hirq="{line_data.hardirq_softirq}"'
            if line_data.get('preempt_depth') is not None:
                data_attrs += f' data-preempt="{line_data.preempt_depth}"'

            # 添加耗时 tooltip
            title_attr = ''
            if duration_raw:
                escaped_title = duration_raw.strip().replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
                title_attr = f' title="{escaped_title}"'

            # 添加折叠类
            line_container_class = f"line-container {expandable_class} {fold_class}".strip()

            content_parts.append(f'<div class="{line_container_class}" id="{line_anchor_id}" data-line-number="{line_number}" data-line-id="{line_id}"{data_attrs}{title_attr}')
            if is_expandable:
                content_parts.append(f' onclick="handleLineClick(event, \'{line_id}\')" ondblclick="handleDoubleClick(event, \'{line_id}\')" tabindex="0"')
                content_parts.append('>')
            else:
                content_parts.append('>')
            content_parts.append(f'<span class="line-number" onclick="updateAnchor(\'{line_anchor_id}\', event)" title="Click to copy anchor link">{line_number}</span>')

            # 添加折叠标记
            if is_foldable:
                if fold_type == 'entry':
                    # V2: Always use ▶, CSS rotate(90deg) for expanded state
                    expanded_class = '' if 'folded' in fold_class else ' expanded'
                    fold_marker = f'<span class="fold-icon{expanded_class}" tabindex="0" role="button" aria-label="Toggle fold" aria-expanded="{"false" if "folded" in fold_class else "true"}" onkeydown="handleFoldIconKeydown(event, this)" onclick="handleFoldIconClick(event, this)">▶</span>'
                content_parts.append(f'<span class="fold-marker">{fold_marker}</span>')
            else:
                # 非可折叠行也插入空白fold-marker以保持对齐
                content_parts.append('<span class="fold-marker"><span style="display:inline-block; width:20px; height:20px;"></span></span>')

            content_parts.append(f'<span class="line-content">{escaped_line}</span>')

            if is_expandable:
                content_parts.append('<span class="expand-btn">+</span>')

            content_parts.append('</div>')

            if is_expandable:
                # 获取函数信息
                func_info = line_data['func_info']

                # 为调用fastfaddr2line准备的键（保留后缀，调整偏移）
                func_info_for_call = self._adjust_func_info(func_info, remove_suffix=False)

                # 为显示准备的键（移除后缀，调整偏移）
                func_info_for_display = self._adjust_func_info(func_info, remove_suffix=True)

                # 从预取的数据中获取位置信息（使用调用时的键）
                locations = self.func_locations_map.get(func_info_for_call, {})

                # 为expanded-content添加内联样式style="display: none;"，确保初始状态
                block_start = len(content_parts)

                if locations:
                    # 确定使用哪个URL
                    # 从func_info_for_call中提取模块名，判断返回地址的函数类型
                    # func_info_for_call格式: func+offset/length [module]
                    return_addr_module_name = None
                    if '[' in func_info_for_call and ']' in func_info_for_call:
                        module_match = re.search(r'\[(.*?)\]', func_info_for_call)
                        if module_match:
                            return_addr_module_name = module_match.group(1)

                    if return_addr_module_name:
                        # 返回地址是模块函数
                        if return_addr_module_name in self.combined_module_url_map:
                            current_base_url = self.combined_module_url_map[return_addr_module_name]
                        else:
                            current_base_url = self.default_module_url if self.default_module_url else self.base_url
                    else:
                        # 返回地址是内核函数
                        current_base_url = self.base_url

                    # 检查是结构化数据还是原始输出
                    if isinstance(locations, dict) and 'func' in str(next(iter(locations.values()), {})):
                        # 结构化数据（标准模式）- 这部分可能不会执行，保留以兼容性
                        loc_list = locations.get(func_info_for_call, [])
                        for loc in loc_list:
                            if current_base_url:
                                file_path = loc['file']
                                # 清理文件路径
                                file_path = clean_file_path(file_path, self.kernel_src, self.path_prefix)

                                # 提取文件路径和行号
                                clean_path = self._extract_file_path(file_path, self.kernel_src, self.path_prefix)
                                line_num = loc.get('line')

                                # 如果行号不在loc中，尝试从file_path中提取
                                if line_num is None:
                                    line_num = self._extract_line_number(file_path, self.kernel_src, self.path_prefix)

                                if clean_path and line_num is not None:
                                    # 获取相对于内核源码的路径
                                    relative_path = get_relative_path(clean_path, self.kernel_src)

                                    # 构建URL
                                    url = build_source_url(current_base_url, relative_path, line_num)

                                    link_text = f"{loc['func']} at {relative_path}:{line_num}"
                                    if loc.get('inlined'):
                                        link_text = f"(inlined) {link_text}"

                                    escaped_link = escape_html_preserve_spaces(link_text)
                                    escaped_url = html.escape(url)
                                    content_parts.append(f'<a class="location-link" href="{escaped_url}" target="_blank">{escaped_link}</a>')
                                else:
                                    # 无法提取有效路径或行号
                                    link_text = f"{loc['func']} at {file_path}"
                                    if loc.get('inlined'):
                                        link_text = f"(inlined) {link_text}"
                                    escaped_link = escape_html_preserve_spaces(link_text)
                                    content_parts.append(f'<div class="location-link">{escaped_link}</div>')
                            else:
                                # 没有base_url，只显示文本
                                file_path = loc['file']
                                # 清理文件路径
                                file_path = clean_file_path(file_path, self.kernel_src, self.path_prefix)

                                # 提取文件路径
                                clean_path = self._extract_file_path(file_path, self.kernel_src, self.path_prefix)
                                line_num = loc.get('line')

                                # 如果行号不在loc中，尝试从file_path中提取
                                if line_num is None:
                                    line_num = self._extract_line_number(file_path, self.kernel_src, self.path_prefix)

                                if clean_path and line_num is not None:
                                    link_text = f"{loc['func']} at {clean_path}:{line_num}"
                                else:
                                    link_text = f"{loc['func']} at {file_path}"

                                if loc.get('inlined'):
                                    link_text = f"(inlined) {link_text}"
                                escaped_link = escape_html_preserve_spaces(link_text)
                                content_parts.append(f'<div class="location-link">{escaped_link}</div>')
                    elif isinstance(locations, str):
                        # 原始输出（--list模式）
                        # Same location text + URL always renders the same block (55k calls -> ~3k distinct)
                        list_key = (locations, current_base_url)
                        rendered = self._list_cache.get(list_key)
                        if rendered is None:
                            rendered = parse_list_output(locations, current_base_url, self.kernel_src, self.highlight_code, self.path_prefix, self.module_src)
                            self._list_cache[list_key] = rendered
                        content_parts.append(rendered)
                    else:
                        # 未知格式
                        content_parts.append(f'<div class="location-link">Source information unavailable for: {escape_html_preserve_spaces(func_info_for_display)}</div>')
                else:
                    content_parts.append(f'<div class="location-link">Source information unavailable for: {escape_html_preserve_spaces(func_info_for_display)}</div>')

                # De-duplicated source block, filled into the DOM on first expand (fgFill)
                block_html = ''.join(content_parts[block_start:])
                del content_parts[block_start:]
                src_idx = self._src_index.get(block_html)
                if src_idx is None:
                    src_idx = len(self._src_pool)
                    self._src_pool.append(block_html)
                    self._src_index[block_html] = src_idx
                content_parts.append(f'<div class="expanded-content" id="{line_id}_content" data-src="{src_idx}" style="display: none;"></div>')

            yield ''.join(content_parts)

    def _generate_javascript(self):
        """Main report JavaScript (assets/report/report.js)."""
        return '\n    <script>\n' + read_asset(REPORT_DATA_JS) + read_asset(REPORT_JS) + '    </script>\n'

    def _generate_css(self):
        """Main report CSS (assets/report/report.css)."""
        return read_asset(REPORT_CSS)


def generate_html(parsed_lines, vmlinux_path, faddr2line_path, module_dirs=None, base_url=None, module_url=None, kernel_src=None, use_list=False, verbose=False, fast_mode=False, highlight_code=False, path_prefix=None, module_src=None, module_srcs=None, script_args=None, enable_filter=False, parse_time=0, total_time=0, func_links=False, entry_offset=0, enable_fold=False, timeline_json=None, compare_json=None, source_provider=None):
    """Generate interactive HTML page. Wrapper for backward compatibility."""
    generator = HtmlGenerator(
        parsed_lines, vmlinux_path, faddr2line_path,
        module_dirs=module_dirs, base_url=base_url, module_url=module_url,
        kernel_src=kernel_src, use_list=use_list, verbose=verbose,
        fast_mode=fast_mode, highlight_code=highlight_code,
        path_prefix=path_prefix, module_src=module_src, module_srcs=module_srcs,
        script_args=script_args, enable_filter=enable_filter,
        parse_time=parse_time, total_time=total_time,
        func_links=func_links, entry_offset=entry_offset,
        enable_fold=enable_fold, timeline_json=timeline_json, compare_json=compare_json,
        source_provider=source_provider
    )
    return generator.generate()


def write_html(path, parsed_lines, vmlinux_path, faddr2line_path, **options):
    """Stream a complete report atomically and return output metadata and resolver times."""
    generator = HtmlGenerator(parsed_lines, vmlinux_path, faddr2line_path, **options)
    with ReportSpool(path) as spool:
        spool.write_sections(generator.iter_sections())
        written = spool.publish()
    return written, generator.vmlinux_time, generator.module_time
