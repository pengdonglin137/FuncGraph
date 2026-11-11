"""AddressResolver: func+off/len -> source locations via (fast)faddr2line."""

import re
import os
import subprocess
import sys
import html
import time

from .constants import PROJECT_DIR
from .highlight import escape_for_pre, highlight_c_code
from .util import build_source_url, clean_file_path, escape_html_preserve_spaces, get_relative_path, verbose_print


class AddressResolver:
    """Resolves function addresses to source code locations via faddr2line."""

    def __init__(self, faddr2line_path, vmlinux_path, kernel_src=None,
                 verbose=False, fast_mode=False, path_prefix=None,
                 module_src=None, module_srcs=None, entry_offset=0):
        self.faddr2line_path = os.path.abspath(faddr2line_path) if faddr2line_path else None
        self.vmlinux_path = os.path.abspath(vmlinux_path) if vmlinux_path else None
        self.kernel_src = os.path.abspath(kernel_src) if kernel_src else None
        self.verbose = verbose
        self.fast_mode = fast_mode
        self.path_prefix = path_prefix
        self.module_src = module_src
        self.module_srcs = module_srcs
        self.entry_offset = entry_offset

        # If fast mode, try to find fastfaddr2line.py
        if fast_mode and faddr2line_path:
            script_dir = PROJECT_DIR
            fast_path = os.path.join(script_dir, 'fastfaddr2line.py')
            if os.path.exists(fast_path):
                self.faddr2line_path = fast_path

    def _build_cmd(self, target, use_list=False, extra_args=None):
        """Build faddr2line command with common parameters.

        This is the key deduplication - replaces 4 separate command builders.
        """
        cmd = [self.faddr2line_path]
        if use_list:
            cmd.append('--list')

        # Add fastfaddr2line-specific parameters
        if os.path.basename(self.faddr2line_path) == 'fastfaddr2line.py':
            cmd.insert(0, sys.executable)
            if self.path_prefix:
                prefixes = self.path_prefix if isinstance(self.path_prefix, list) else [self.path_prefix]
                for prefix in prefixes:
                    if prefix:
                        cmd.extend(['--path-prefix', prefix])
                        verbose_print(f"Adding --path-prefix parameter: {prefix}", self.verbose)
            if self.module_srcs:
                srcs = self.module_srcs if isinstance(self.module_srcs, list) else [self.module_srcs]
                for src in srcs:
                    if src:
                        cmd.extend(['--module-srcs', os.path.abspath(src)])
                        verbose_print(f"Adding --module-srcs parameter: {os.path.abspath(src)}", self.verbose)
            if extra_args:
                cmd.extend(extra_args)

        cmd.append(target)
        return cmd

    def resolve_batch(self, func_infos, target=None, use_list=False):
        """Batch resolve function addresses. Replaces call_faddr2line_batch()."""
        if target is None:
            target = self.vmlinux_path

        if not func_infos:
            verbose_print("No function infos provided to faddr2line", self.verbose)
            return {}

        start_time = time.time()
        verbose_print(f"Calling faddr2line for {len(func_infos)} functions on target: {target}", self.verbose)
        verbose_print(f"Use list mode: {use_list}", self.verbose)

        try:
            # 检查faddr2line工具是否存在
            if not os.path.isfile(self.faddr2line_path):
                print(f"Error: faddr2line tool not found at {self.faddr2line_path}", file=sys.stderr)
                return {}

            # 检查目标文件是否存在
            abs_target = os.path.abspath(target)
            if not os.path.isfile(abs_target):
                print(f"Warning: Target file not found: {abs_target}", file=sys.stderr)
                return {}

            # 保存当前工作目录
            original_cwd = os.getcwd()

            # 如果指定了内核源码目录，切换到该目录
            if self.kernel_src and os.path.isdir(self.kernel_src):
                verbose_print(f"Changing working directory to: {self.kernel_src}", self.verbose)
                os.chdir(self.kernel_src)
            else:
                verbose_print(f"Kernel source directory not found: {self.kernel_src}", self.verbose)

            # 清理func_infos：移除[module]部分，因为fastfaddr2line不需要
            # 但需要建立映射关系，以便返回时使用原始键
            func_info_map = {}
            cleaned_func_infos = []
            for func in func_infos:
                cleaned = re.sub(r'\s*\[.*?\]', '', func)
                func_info_map[cleaned] = func  # 清理后 -> 原始
                cleaned_func_infos.append(cleaned)

            # 构建命令：根据是否使用--list选项
            if use_list:
                cmd = self._build_cmd(abs_target, use_list=True)
                cmd.extend(cleaned_func_infos)
                verbose_print(f"Executing batch command: {' '.join(cmd)}", self.verbose)
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    output = result.stdout
                    verbose_print(f"faddr2line --list completed successfully, output length: {len(output)} chars", self.verbose)

                    # 解析批处理输出
                    results = {}
                    current_func = None
                    current_output = []

                    # 正则表达式匹配函数块头
                    func_header_re = re.compile(r'^(.*?[^+]+[\+\w]+/[0-9a-fx]+):$')

                    for line in output.splitlines():
                        line = line.rstrip()
                        # 检查是否是新的函数块头
                        header_match = func_header_re.match(line)
                        if header_match:
                            # 保存前一个函数的信息
                            if current_func:
                                # 使用原始键存储
                                original_func = func_info_map.get(current_func, current_func)
                                results.setdefault(original_func, '\n'.join(current_output))

                            # 开始新的函数条目
                            current_func = header_match.group(1).strip()
                            current_output = [line]  # 包含头行
                        else:
                            # 添加到当前函数块
                            if current_func:
                                current_output.append(line)

                    # 保存最后一个函数的信息
                    if current_func:
                        original_func = func_info_map.get(current_func, current_func)
                        results.setdefault(original_func, '\n'.join(current_output))

                    # 检查是否有函数没有出现在输出中
                    # 清理results的键进行比较（只算一次）
                    cleaned_result_keys = {re.sub(r'\s*\[.*?\]', '', k) for k in results.keys()}
                    for func in func_infos:
                        cleaned_func = re.sub(r'\s*\[.*?\]', '', func)
                        if cleaned_func not in cleaned_result_keys:
                            results[func] = f"Error: No output for function {func}"

                    elapsed = time.time() - start_time
                    verbose_print(f"Parsed {len(results)} function locations in {elapsed:.2f} seconds", self.verbose)
                    return results

                except subprocess.CalledProcessError as e:
                    verbose_print(f"Batch faddr2line --list failed with return code {e.returncode}", self.verbose)
                    verbose_print(f"Stderr: {e.stderr}", self.verbose)
                    # 回退到逐个处理函数
                    verbose_print("Falling back to individual function processing", self.verbose)
                    results = {}
                    total_funcs = len(func_infos)
                    for i, func_info in enumerate(func_infos):
                        verbose_print(f"Processing function {i+1}/{total_funcs}: {func_info}", self.verbose)
                        fallback_cmd = self._build_cmd(abs_target, use_list=True)
                        # 清理func_info：移除[module]部分
                        cleaned_func = re.sub(r'\s*\[.*?\]', '', func_info)
                        fallback_cmd.append(cleaned_func)
                        try:
                            result = subprocess.run(fallback_cmd, capture_output=True, text=True, check=True)
                            results[func_info] = result.stdout
                        except subprocess.CalledProcessError as e2:
                            results[func_info] = f"Error: {e2.stderr}"
                    return results
            else:
                # 标准批量处理模式 - 一次传递所有函数地址
                cmd = self._build_cmd(abs_target, use_list=False)
                cmd.extend(cleaned_func_infos)
                verbose_print(f"Executing batch command: {' '.join(cmd)}", self.verbose)
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    output = result.stdout
                    verbose_print(f"faddr2line completed successfully, output length: {len(output)} chars", self.verbose)

                    # 解析批量输出
                    func_locations = {}
                    current_func = None
                    current_locations = []

                    for line in output.splitlines():
                        line = line.strip()
                        if not line:
                            continue

                        # 检查是否是新的函数条目
                        if line.endswith(':'):
                            # 保存前一个函数的信息
                            if current_func:
                                # 使用原始键存储
                                original_func = func_info_map.get(current_func, current_func)
                                func_locations.setdefault(original_func, current_locations)

                            # 开始新的函数条目
                            current_func = line[:-1]  # 去掉末尾的冒号
                            current_locations = []
                            continue

                        # 处理内联函数标记
                        inlined = False
                        if line.startswith('(inlined by)'):
                            line = line[len('(inlined by)'):].strip()
                            inlined = True

                        # 解析位置和行号
                        loc_match = re.match(r'(.*)\s+at\s+(.*):(\d+)', line)
                        if loc_match:
                            func_name = loc_match.group(1).strip()
                            file_path = loc_match.group(2)
                            line_no = loc_match.group(3).strip()
                            current_locations.append({
                                'func': func_name,
                                'file': file_path,
                                'line': line_no,
                                'inlined': inlined
                            })

                    # 保存最后一个函数的信息
                    if current_func:
                        original_func = func_info_map.get(current_func, current_func)
                        func_locations.setdefault(original_func, current_locations)

                    elapsed = time.time() - start_time
                    verbose_print(f"Parsed {len(func_locations)} function locations in {elapsed:.2f} seconds", self.verbose)
                    return func_locations
                except subprocess.CalledProcessError as e:
                    verbose_print(f"faddr2line failed with return code {e.returncode}", self.verbose)
                    verbose_print(f"Stderr: {e.stderr}", self.verbose)
                    return {}

        except Exception as e:
            verbose_print(f"Error calling faddr2line: {str(e)}", self.verbose)
            return {}
        finally:
            # 恢复原始工作目录
            if 'original_cwd' in locals():
                try:
                    verbose_print(f"Restoring working directory to: {original_cwd}", self.verbose)
                    os.chdir(original_cwd)
                except Exception as e:
                    verbose_print(f"Failed to restore working directory: {str(e)}", self.verbose)

    def resolve_func_names(self, func_names, func_module_map=None, module_paths=None):
        """Resolve function names to source locations. Replaces call_faddr2line_for_func_names()."""
        if not func_names:
            return {}

        # 调用 faddr2line
        results = {}

        # 分组函数：内核函数 vs 模块函数
        kernel_funcs = []
        module_funcs = {}  # 模块名 -> 函数列表

        for func_name in func_names:
            if func_module_map and func_name in func_module_map:
                module_name = func_module_map[func_name]
                if module_name not in module_funcs:
                    module_funcs[module_name] = []
                module_funcs[module_name].append(func_name)
            else:
                kernel_funcs.append(func_name)

        # 处理内核函数：不需要 module_srcs
        if kernel_funcs:
            kernel_results = self._resolve_func_list(
                self.vmlinux_path, kernel_funcs, include_module_srcs=False
            )
            results.update(kernel_results)

        # 处理模块函数：需要 module_srcs 和模块文件路径
        for module_name, module_func_list in module_funcs.items():
            module_path = None
            if module_paths:
                module_path = module_paths.get(module_name)

            # 如果没有找到模块路径，跳过这些函数
            if not module_path or not os.path.exists(module_path):
                verbose_print(f"警告: 无法找到模块 {module_name} 的文件路径，跳过函数: {module_func_list}", self.verbose)
                continue

            abs_module_path = os.path.abspath(module_path)
            module_results = self._resolve_func_list(
                abs_module_path, module_func_list, include_module_srcs=True
            )
            results.update(module_results)

        return results

    def _resolve_func_list(self, target_path, func_names, include_module_srcs=False):
        """Resolve a list of function names against a specific target. Replaces _call_faddr2line_for_functions()."""
        if not func_names:
            return {}

        # 为每个函数名添加 +0x0/0x1 格式
        func_specs = []
        for func_name in func_names:
            func_specs.append(f"{func_name}+0x0/0x1")

        # 构建命令
        cmd = [self.faddr2line_path, target_path] + func_specs

        # 如果是fastfaddr2line，添加额外参数
        if os.path.basename(self.faddr2line_path) == 'fastfaddr2line.py':
            cmd.insert(0, sys.executable)
            if self.path_prefix:
                prefixes = self.path_prefix if isinstance(self.path_prefix, list) else [self.path_prefix]
                for prefix in prefixes:
                    cmd.extend(['--path-prefix', prefix])
            if include_module_srcs and self.module_srcs:
                srcs = self.module_srcs if isinstance(self.module_srcs, list) else [self.module_srcs]
                for src in srcs:
                    cmd.extend(['--module-src', src])
            # 总是传递entry-offset参数，即使为0
            cmd.extend(['--entry-offset', str(self.entry_offset)])

        verbose_print(f"调用 faddr2line 解析函数名: {' '.join(cmd)}", self.verbose)

        try:
            # 执行命令
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                verbose_print(f"faddr2line 错误: {result.stderr}", self.verbose)
                return {}

            # 解析输出
            lines = result.stdout.strip().split('\n')
            results = {}

            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue

                # 匹配函数名格式: func_name+0x0/0x1:
                if '+0x0/0x1:' in line and i + 1 < len(lines):
                    # 提取函数名，去掉 +0x0/0x1: 部分
                    func_name = line.replace('+0x0/0x1:', '').strip()
                    source_line = lines[i + 1].strip()

                    # 匹配格式: func_name at source_file:line_num
                    if ' at ' in source_line and ':' in source_line:
                        # 提取源码文件和行号
                        # 格式: func_name at source_file:line_num
                        at_pos = source_line.find(' at ')
                        if at_pos != -1:
                            rest = source_line[at_pos + 4:]  # 跳过 " at "
                            if ':' in rest:
                                source_file, line_num = rest.rsplit(':', 1)
                                results[func_name] = (source_file, line_num)

                    i += 2
                else:
                    i += 1

            verbose_print(f"函数名解析结果: {len(results)} 个", self.verbose)
            return results

        except subprocess.TimeoutExpired:
            verbose_print("faddr2line 超时", self.verbose)
            return {}
        except Exception as e:
            verbose_print(f"调用 faddr2line 失败: {str(e)}", self.verbose)
            return {}

    @staticmethod
    def adjust_offset(func_info):
        """Adjust function offset by -1 for non-zero offsets. Replaces adjust_function_offset()."""
        # 匹配函数名+偏移/长度格式
        match = re.match(r'^(.*?)([+][0-9a-fA-FxX]+)(/[0-9a-fA-FxX]+)$', func_info)
        if not match:
            return func_info  # 不符合格式，直接返回

        func_name = match.group(1)
        offset_str = match.group(2)[1:]  # 去掉前面的+号
        length_str = match.group(3)

        try:
            # 解析十六进制偏移量
            offset_val = int(offset_str, 16)
            if offset_val == 0:
                return func_info  # 偏移量为0，不需要调整

            # 计算新偏移量（减1）
            new_offset_val = offset_val - 1
            new_offset_str = hex(new_offset_val)

            # 重构函数信息字符串
            return f"{func_name}+{new_offset_str}{length_str}"
        except ValueError:
            return func_info  # 解析失败，返回原始字符串

    @staticmethod
    def parse_list_output(output, base_url=None, kernel_src=None, highlight_code=False, path_prefix=None, module_src=None):
        """Parse faddr2line --list output to HTML. Replaces parse_list_output()."""
        lines = output.splitlines()
        html_output = []

        # 正则表达式匹配函数块头（如 "function+offset/size:"）
        func_header_re = re.compile(r'^(.*?[^+]+[\+\w]+/[0-9a-fx]+):$')

        # 修复正则表达式以匹配包含discriminator信息的行
        # 修改正则表达式，使其能够匹配包含discriminator的行
        loc_pattern = re.compile(r'^(?:\(inlined by\)\s+)?(.+?)\s+at\s+(.+):([0-9]+)(?:\s+\(discriminator [0-9]+\))?\s*$')

        # 处理所有行
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            i += 1

            # 1. 尝试匹配函数块头
            header_match = func_header_re.match(line)
            if header_match:
                html_output.append(f'<div class="location-link"><strong>{escape_html_preserve_spaces(line)}</strong></div>')

                # 收集函数块的所有行，直到下一个函数块头或结束
                block_lines = []
                while i < len(lines):
                    next_line = lines[i].rstrip()
                    # 检查是否是下一个函数块头
                    if func_header_re.match(next_line):
                        break
                    block_lines.append(next_line)
                    i += 1

                # 处理当前函数块的行
                for block_line in block_lines:
                    stripped = block_line.strip()

                    # a. 尝试匹配位置行（包括内联标记行和包含discriminator的行）
                    loc_match = loc_pattern.match(stripped)
                    if loc_match:
                        func_part = loc_match.group(1).strip()
                        file_path = loc_match.group(2).strip()
                        line_no = loc_match.group(3).strip()

                        # 清理文件路径，去除内核源码根目录、模块源码根目录或备选路径前缀
                        clean_path = clean_file_path(file_path, kernel_src, path_prefix, module_src)

                        # 获取相对于内核源码的路径
                        relative_path = get_relative_path(clean_path, kernel_src) if kernel_src else clean_path

                        # 进一步清理路径，确保显示相对路径
                        relative_path = clean_file_path(relative_path, kernel_src, path_prefix, module_src)

                        # 构建URL
                        if base_url:
                            url = build_source_url(base_url, relative_path, line_no)

                            # 使用清理后的相对路径构建链接文本
                            link_text = f"{func_part} at {relative_path}:{line_no}"
                            # 如果是内联行，添加标记
                            if stripped.startswith('(inlined by)'):
                                link_text = f"(inlined by) {link_text}"

                            escaped_link = escape_html_preserve_spaces(link_text)
                            escaped_url = html.escape(url)
                            html_output.append(f'<a class="location-link" href="{escaped_url}" target="_blank">{escaped_link}</a>')
                        else:
                            # 没有base_url，只显示文本
                            link_text = f"{func_part} at {relative_path}:{line_no}"
                            if stripped.startswith('(inlined by)'):
                                link_text = f"(inlined by) {link_text}"
                            escaped_link = escape_html_preserve_spaces(link_text)
                            html_output.append(f'<div class="location-link">{escaped_link}</div>')
                        continue

                    # 其他处理逻辑保持不变...
                    # b. 检查是否是当前行（格式如：>34< ...）
                    if re.match(r'^\s*>(\d+)<', block_line):
                        if highlight_code:
                            # 提取行号和源代码
                            match = re.match(r'^(\s*>)(\d+)(<\s*)(.*)', block_line)
                            if match:
                                prefix = match.group(1)  # 前面的空格和 >
                                line_no = match.group(2)
                                bracket = match.group(3)  # < 和后面的空格
                                code_line = match.group(4)
                                # 只对代码部分高亮，保持行号格式不变
                                highlighted_code = highlight_c_code(code_line)
                                # 使用原始的前缀和括号，确保对齐
                                line_html = f'<div class="source-line current-line">{escape_html_preserve_spaces(prefix + line_no + bracket)}{highlighted_code}</div>'
                            else:
                                line_html = f'<div class="source-line current-line">{escape_for_pre(block_line)}</div>'
                        else:
                            line_html = f'<div class="source-line current-line">{escape_for_pre(block_line)}</div>'
                        html_output.append(line_html)
                        continue

                    # c. 其他行直接添加（保持原样）
                    if stripped:  # 跳过空行
                        if highlight_code:
                            # 对其他行，需要保持行号和代码部分的对齐
                            # 格式通常是: " 行号  代码" 或 " 行号 代码"
                            match = re.match(r'^(\s+)(\d+)(\s+)(.*)', block_line)
                            if match:
                                prefix = match.group(1)  # 前面的空格
                                line_no = match.group(2)
                                separator = match.group(3)  # 行号后的空格
                                code_line = match.group(4)
                                # 只对代码部分高亮
                                highlighted_code = highlight_c_code(code_line)
                                line_html = f'<div class="source-line">{escape_html_preserve_spaces(prefix + line_no + separator)}{highlighted_code}</div>'
                            else:
                                # 无法解析，使用原始行
                                line_html = f'<div class="source-line">{escape_for_pre(block_line)}</div>'
                            html_output.append(line_html)
                        else:
                            html_output.append(f'<div class="source-line">{escape_for_pre(block_line)}</div>')

                # 在函数块之间添加空行
                html_output.append('<div style="height: 10px;"></div>')
                continue

            # 2. 如果不是函数块头，直接处理该行
            if line.strip():
                html_output.append(f'<div class="source-line">{escape_for_pre(line)}</div>')

        return ''.join(html_output)


# Backward-compatible wrapper functions for AddressResolver
def adjust_function_offset(func_info):
    """Backward-compatible wrapper for AddressResolver.adjust_offset()."""
    return AddressResolver.adjust_offset(func_info)


def parse_list_output(output, base_url=None, kernel_src=None, highlight_code=False, path_prefix=None, module_src=None):
    """Backward-compatible wrapper for AddressResolver.parse_list_output()."""
    return AddressResolver.parse_list_output(output, base_url, kernel_src, highlight_code, path_prefix, module_src)


def call_faddr2line_batch(faddr2line_path, target, func_infos, use_list=False, kernel_src=None, verbose=False, path_prefix=None, module_src=None, module_srcs=None):
    """Backward-compatible wrapper for AddressResolver.resolve_batch()."""
    resolver = AddressResolver(faddr2line_path, target, kernel_src=kernel_src,
                               verbose=verbose, path_prefix=path_prefix,
                               module_src=module_src, module_srcs=module_srcs)
    return resolver.resolve_batch(func_infos, target, use_list=use_list)


def parse_module_url(module_url_str, base_url):
    """
    解析module_url参数，返回模块名到URL的映射

    参数格式：
    - None: 返回空字典，所有模块使用base_url
    - "http://example.com": 返回空字典，所有模块使用这个URL
    - "http://example.com:mod1,mod2": 返回 {'mod1': 'http://example.com', 'mod2': 'http://example.com'}，其他模块使用base_url
    - "http://example.com:mod1,mod2,http://example.com/other:mod3,mod4":
      - mod1,mod2使用http://example.com
      - mod3,mod4使用http://example.com/other
      - 其他模块使用base_url

    返回值：
    - module_url_map: 模块名 -> URL 的映射
    - default_module_url: 默认模块URL（未在映射中的模块使用）
    """
    if not module_url_str:
        # 没有提供module_url，使用base_url
        return {}, base_url

    import re

    # 找出所有URL:modules模式
    # 格式：url:mod1,mod2 或 url
    # 多个URL:modules对之间用逗号分隔

    # 首先检查是否有冒号
    if ':' not in module_url_str:
        # 没有冒号，说明只有URL
        # 验证URL格式
        if not module_url_str.startswith(('http://', 'https://')):
            print(f"Warning: module-url '{module_url_str}' does not start with http:// or https://", file=sys.stderr)
            return {}, base_url

        # 所有模块使用这个URL
        return {}, module_url_str

    # 有冒号，需要解析
    # 使用正则表达式找到所有 URL:modules 对

    # 方法：使用正则表达式匹配 URL 和它后面的模块列表
    # 模式：URL:modules，其中 modules 是直到下一个 URL 或结束的所有内容

    # 使用正则表达式：https?://[^:,]+:([^,]+(?:,[^,]+)*)?
    # 但这会匹配到 URL:mod1，然后 mod2 会被忽略

    # 更好的方法：找到所有 URL，然后解析它们后面的模块列表
    # URL 模式：http:// 或 https:// 开头，后面跟着非逗号字符，直到冒号

    # 使用正则表达式找到所有 URL 的位置
    # 匹配模式：https?://[^:,]+，但需要确保匹配到完整的 URL
    # 对于 https://url1.com:mod1，应该匹配 https://url1.com，而不是 https
    # 问题：https?://[^:,]+ 会匹配到 https，因为遇到冒号就停止了
    # 解决方案：使用 https?://[^:,]+ 但确保后面有冒号或结束
    # 或者更简单：找到所有以 http:// 或 https:// 开头，后面跟着非逗号字符，直到遇到冒号或逗号或结束
    # 使用模式：https?://[^:,]+(?=:[^,]*,|$)
    url_pattern = r'https?://[^:,]+(?=:[^,]*,|$)'
    urls = re.findall(url_pattern, module_url_str)

    # 如果没有匹配到，尝试更宽松的模式
    if not urls:
        url_pattern = r'https?://[^:,]+'
        urls = re.findall(url_pattern, module_url_str)

    if not urls:
        # 没有找到 URL，说明格式错误
        print(f"Warning: module-url '{module_url_str}' contains no valid URLs", file=sys.stderr)
        return {}, base_url

    # 找到每个 URL 在字符串中的位置
    url_positions = []
    for url in urls:
        pos = module_url_str.find(url)
        if pos != -1:
            url_positions.append((pos, url))

    # 按位置排序
    url_positions.sort()

    # 解析每个 URL 和它后面的模块
    module_url_map = {}
    urls_without_modules = []

    for i, (pos, url) in enumerate(url_positions):
        # 找出这个 URL 后面的内容
        if i + 1 < len(url_positions):
            next_pos = url_positions[i + 1][0]
            content_after = module_url_str[pos + len(url):next_pos]
        else:
            # 最后一个 URL
            content_after = module_url_str[pos + len(url):]

        # 去掉开头的冒号和空格
        content_after = content_after.lstrip(':').strip()

        # 验证URL格式
        if not url.startswith(('http://', 'https://')):
            print(f"Warning: URL '{url}' does not start with http:// or https://", file=sys.stderr)
            continue

        if not content_after:
            # 没有模块名，这个URL作为默认URL
            urls_without_modules.append(url)
        else:
            # 有模块名，按逗号分割
            # 但需要排除其中的 URL
            module_list = []
            for part in content_after.split(','):
                part = part.strip()
                if part and not part.startswith(('http://', 'https://')):
                    module_list.append(part)

            # 有模块名，分配给这个URL
            for module_name in module_list:
                # 检查模块名是否包含非法字符
                if not module_name.replace('-', '_').replace('.', '_').isalnum():
                    print(f"Warning: module name '{module_name}' contains potentially invalid characters", file=sys.stderr)
                module_url_map[module_name] = url

    # 确定默认URL
    default_url = base_url

    # 如果有URL没有模块名，使用第一个这样的URL作为默认
    if urls_without_modules:
        default_url = urls_without_modules[0]

    return module_url_map, default_url


def create_source_link(source_file, line_num, display_name, base_url, kernel_src, module_srcs, module_name=None, module_url=None):
    """为函数名生成源码链接

    参数:
        source_file: 源码文件路径
        line_num: 行号
        display_name: 显示名称
        base_url: 基础URL（内核函数使用）
        kernel_src: 内核源码根目录
        module_srcs: 模块源码路径列表
        module_name: 模块名（如果是模块函数）
        module_url: 模块特定的URL（如果提供）

    返回:
        HTML链接字符串
    """
    if not source_file or not line_num:
        return escape_html_preserve_spaces(display_name)

    # 确定使用哪个URL
    # 如果提供了module_url，优先使用module_url
    # 否则使用base_url
    effective_url = module_url if module_url else base_url

    if not effective_url:
        return escape_html_preserve_spaces(display_name)

    # 清理文件路径
    clean_path = clean_file_path(source_file, kernel_src, module_srcs)

    # 获取相对于内核源码的路径
    relative_path = get_relative_path(clean_path, kernel_src)

    # 构建URL
    url = build_source_url(effective_url, relative_path, line_num)

    # 生成链接
    escaped_display_name = escape_html_preserve_spaces(display_name)
    escaped_url = html.escape(url)

    # 添加 onclick="event.stopPropagation()" 防止事件冒泡到父元素
    return f'<a class="func-name-link" href="{escaped_url}" target="_blank" title="Click to open {relative_path}:{line_num}" onclick="event.stopPropagation()">{escaped_display_name}</a>'


def _call_faddr2line_for_functions(faddr2line_path, target_path, func_names, path_prefix, module_srcs, entry_offset, verbose):
    """Backward-compatible wrapper for AddressResolver._resolve_func_list()."""
    resolver = AddressResolver(faddr2line_path, target_path, verbose=verbose,
                               path_prefix=path_prefix, module_srcs=module_srcs,
                               entry_offset=entry_offset)
    return resolver._resolve_func_list(target_path, func_names,
                                       include_module_srcs=(module_srcs is not None))


def call_faddr2line_for_func_names(vmlinux_path, faddr2line_path, func_names, use_list=False, verbose=False, fast_mode=False, path_prefix=None, module_srcs=None, entry_offset=0, func_module_map=None, module_paths=None):
    """Backward-compatible wrapper for AddressResolver.resolve_func_names()."""
    resolver = AddressResolver(faddr2line_path, vmlinux_path, verbose=verbose,
                               fast_mode=fast_mode, path_prefix=path_prefix,
                               module_srcs=module_srcs, entry_offset=entry_offset)
    return resolver.resolve_func_names(func_names, func_module_map=func_module_map,
                                       module_paths=module_paths)
