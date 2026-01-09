#!/usr/bin/env python3
import re
import os
import subprocess
import argparse
import sys
import html
import glob
import fnmatch
from collections import defaultdict
import time

def verbose_print(message, verbose_flag, end='\n'):
    """根据verbose标志输出调试信息"""
    if verbose_flag:
        print(f"[VERBOSE] {message}", file=sys.stderr, end=end)

def escape_html_preserve_spaces(text):
    """转义HTML特殊字符，保留空格格式"""
    escaped = html.escape(text)
    escaped = escaped.replace(' ', '&nbsp;')
    escaped = escaped.replace('\t', '&nbsp;&nbsp;&nbsp;&nbsp;')
    return escaped

def escape_for_pre(text):
    """专为<pre>标签设计的转义函数，保留原始格式"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def check_gawk_available():
    """检查gawk是否可用"""
    try:
        result = subprocess.run(['gawk', '--version'], capture_output=True, text=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def find_module_path(module_name, module_dirs, verbose=False):
    """在给定目录及其子目录中递归查找模块文件"""
    if not module_dirs:
        verbose_print(f"No module directories provided for {module_name}", verbose)
        return None
        
    verbose_print(f"Searching for module '{module_name}' in {len(module_dirs)} directories", verbose)
    
    # 尝试多种可能的模块文件名模式
    patterns = [
        f"{module_name}.ko",
        f"{module_name}.ko.debug",
        f"{module_name}.ko.gz",
        f"{module_name}.ko.xz",
        f"{module_name}.ko.zst",
        f"*.ko",  # 通配符匹配
        f"*.ko.debug"
    ]
    
    # 递归搜索所有目录
    for module_dir in module_dirs:
        if not os.path.isdir(module_dir):
            verbose_print(f"Skipping non-existent directory: {module_dir}", verbose)
            continue
            
        verbose_print(f"Searching in directory: {module_dir}", verbose)
            
        # 遍历目录及其所有子目录
        for root, dirs, files in os.walk(module_dir):
            for pattern in patterns:
                # 处理通配符模式
                if '*' in pattern:
                    for filename in files:
                        if fnmatch.fnmatch(filename, pattern):
                            full_path = os.path.join(root, filename)
                            # 检查文件名是否包含模块名
                            if module_name in filename:
                                verbose_print(f"Found module match: {full_path}", verbose)
                                return os.path.abspath(full_path)
                else:
                    # 直接匹配文件名
                    candidate = os.path.join(root, pattern)
                    if os.path.exists(candidate):
                        verbose_print(f"Found exact match: {candidate}", verbose)
                        return os.path.abspath(candidate)
    
    # 尝试使用find命令进行深度搜索（如果可用）
    try:
        verbose_print("Trying find command for deeper search", verbose)
        for module_dir in module_dirs:
            if not os.path.isdir(module_dir):
                continue
                
            # 使用find命令搜索
            cmd = ['find', module_dir, '-type', 'f', 
                   '(', '-name', f'{module_name}.ko', 
                   '-o', '-name', f'{module_name}.ko.debug', 
                   '-o', '-name', f'*{module_name}*.ko', ')']
            verbose_print(f"Running find command: {' '.join(cmd)}", verbose)
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            files = result.stdout.strip().split('\n')
            if files and files[0]:
                verbose_print(f"Find command found: {files[0]}", verbose)
                return os.path.abspath(files[0])  # 返回第一个匹配项的绝对路径
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        verbose_print(f"Find command failed: {str(e)}", verbose)
        pass
    
    verbose_print(f"Module '{module_name}' not found in any directory", verbose)
    return None

def get_relative_path(full_path, base_path):
    """获取相对于基路径的相对路径"""
    if not base_path:
        return full_path
    
    # 确保路径以分隔符结尾
    base_path = os.path.normpath(base_path)
    if not base_path.endswith(os.sep):
        base_path += os.sep
    
    # 检查路径是否在基路径下
    if full_path.startswith(base_path):
        return full_path[len(base_path):]
    
    # 尝试处理常见的构建路径模式
    common_prefixes = [
        "/build/",
        "/usr/src/",
        "/lib/modules/",
        "/tmp/"
    ]
    
    for prefix in common_prefixes:
        if prefix in full_path:
            parts = full_path.split(prefix)
            if len(parts) > 1:
                return parts[-1]
    
    # 如果都不匹配，返回原始路径
    return full_path

def parse_ftrace_file(file_path, verbose=False):
    """解析ftrace文件，提取可展开的行及其函数信息"""
    verbose_print(f"Parsing ftrace file: {file_path}", verbose)
    parsed_lines = []
    expandable_count = 0
    
    try:
        with open(file_path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    line = line.rstrip('\n')
                    if '/*' in line and '<-' in line:
                        func_match = re.search(r'/\*\s*<-(.*?)\s*\*/', line)
                        if func_match:
                            full_func_info = func_match.group(1).strip()
                            
                            # 提取函数地址和模块名
                            func_addr = full_func_info.split()[0] if full_func_info else ''
                            module_name = None
                            
                            # 检查是否有模块名（在方括号中）
                            module_match = re.search(r'\[(.*?)\]', full_func_info)
                            if module_match:
                                module_name = module_match.group(1)
                            
                            parsed_lines.append({
                                'raw_line': line,
                                'expandable': True,
                                'func_info': func_addr,
                                'module_name': module_name
                            })
                            expandable_count += 1
                            continue
                    parsed_lines.append({
                        'raw_line': line,
                        'expandable': False,
                        'func_info': None,
                        'module_name': None
                    })
                except Exception as e:
                    verbose_print(f"Error parsing line {line_num}: {str(e)}", verbose)
                    # 添加为普通行继续处理
                    parsed_lines.append({
                        'raw_line': line.rstrip('\n'),
                        'expandable': False,
                        'func_info': None,
                        'module_name': None
                    })
    except Exception as e:
        print(f"Error reading file {file_path}: {str(e)}", file=sys.stderr)
        sys.exit(1)
    
    verbose_print(f"Parsed {len(parsed_lines)} lines, found {expandable_count} expandable entries", verbose)
    return parsed_lines

def adjust_function_offset(func_info):
    """
    调整函数偏移量：对于函数名+偏移/长度格式，将偏移量减1（除非偏移量为0）
    例如：el0_svc+0x34/0xf0 -> el0_svc+0x33/0xf0
    返回调整后的函数信息字符串
    """
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

def parse_list_output(output, base_url=None, kernel_src=None):
    """解析faddr2line --list的输出并转换为HTML"""
    lines = output.splitlines()
    html_output = []
    
    # 正则表达式匹配函数块头（如 "function+offset/size:"）
    func_header_re = re.compile(r'^(.*?[^+]+[\+\w]+/[0-9a-fx]+):$')
    
    # 正则表达式匹配位置行（包括内联标记行）
    loc_pattern = re.compile(r'^(?:\(inlined by\)\s+)?(.+?)\s+at\s+(.+?):(\d+)\s*$')
    
    # 处理所有行
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        
        # 1. 尝试匹配函数块头
        header_match = func_header_re.match(line)
        if header_match:
            func_name = header_match.group(1).strip()
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
                
                # a. 尝试匹配位置行（包括内联标记行）
                loc_match = loc_pattern.match(stripped)
                if loc_match:
                    func_part = loc_match.group(1).strip()
                    file_path = loc_match.group(2).strip()
                    line_no = loc_match.group(3).strip()
                    
                    # 获取相对于内核源码的路径
                    relative_path = get_relative_path(file_path, kernel_src) if kernel_src else file_path
                    
                    # 构建URL
                    if base_url:
                        if base_url.endswith('/'):
                            url = f"{base_url}{relative_path}#L{line_no}"
                        else:
                            url = f"{base_url}/{relative_path}#L{line_no}"
                        link_text = f"{func_part} at {relative_path}:{line_no}"
                        # 如果是内联行，添加标记
                        if stripped.startswith('(inlined by)'):
                            link_text = f"(inlined by) {link_text}"
                        escaped_link = escape_html_preserve_spaces(link_text)
                        escaped_url = html.escape(url)
                        html_output.append(f'<a class="location-link" href="{escaped_url}" target="_blank">{escaped_link}</a>')
                    else:
                        html_output.append(f'<div class="location-link">{escape_html_preserve_spaces(stripped)}</div>')
                    continue
                
                # b. 检查是否是当前行（格式如：>34< ...）
                if re.match(r'^\s*>(\d+)<', block_line):
                    # 直接输出整行，添加高亮样式
                    line_html = f'<div class="source-line current-line">{escape_for_pre(block_line)}</div>'
                    html_output.append(line_html)
                    continue
                
                # c. 其他行直接添加（保持原样）
                if stripped:  # 跳过空行
                    html_output.append(f'<div class="source-line">{escape_for_pre(block_line)}</div>')
            
            # 在函数块之间添加空行
            html_output.append('<div style="height: 10px;"></div>')
            continue
        
        # 2. 如果不是函数块头，直接处理该行
        if line.strip():
            html_output.append(f'<div class="source-line">{escape_for_pre(line)}</div>')
    
    return ''.join(html_output)

def call_faddr2line_batch(faddr2line_path, target, func_infos, use_list=False, kernel_src=None, verbose=False):
    """调用faddr2line获取多个函数的源代码位置信息"""
    if not func_infos:
        verbose_print("No function infos provided to faddr2line", verbose)
        return {}
    
    start_time = time.time()
    verbose_print(f"Calling faddr2line for {len(func_infos)} functions on target: {target}", verbose)
    verbose_print(f"Use list mode: {use_list}", verbose)
    
    try:
        # 检查faddr2line工具是否存在
        if not os.path.isfile(faddr2line_path):
            print(f"Error: faddr2line tool not found at {faddr2line_path}", file=sys.stderr)
            return {}
        
        # 检查目标文件是否存在
        if not os.path.isfile(target):
            print(f"Warning: Target file not found: {target}", file=sys.stderr)
            return {}
        
        # 使用绝对路径
        abs_faddr2line_path = os.path.abspath(faddr2line_path)
        abs_target = os.path.abspath(target)
        abs_kernel_src = os.path.abspath(kernel_src) if kernel_src else None
        
        # 保存当前工作目录
        original_cwd = os.getcwd()
        
        # 如果指定了内核源码目录，切换到该目录
        if abs_kernel_src and os.path.isdir(abs_kernel_src):
            verbose_print(f"Changing working directory to: {abs_kernel_src}", verbose)
            os.chdir(abs_kernel_src)
        else:
            verbose_print(f"Kernel source directory not found: {abs_kernel_src}", verbose)
        
        # 构建命令：根据是否使用--list选项
        if use_list:
            # 使用--list选项时，使用批处理模式
            cmd = [abs_faddr2line_path, '--list', abs_target] + func_infos
            verbose_print(f"Executing batch command: {' '.join(cmd)}", verbose)
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                output = result.stdout
                verbose_print(f"faddr2line --list completed successfully, output length: {len(output)} chars", verbose)
                
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
                            results[current_func] = '\n'.join(current_output)
                        
                        # 开始新的函数条目
                        current_func = header_match.group(1).strip()
                        current_output = [line]  # 包含头行
                    else:
                        # 添加到当前函数块
                        if current_func:
                            current_output.append(line)
                
                # 保存最后一个函数的信息
                if current_func:
                    results[current_func] = '\n'.join(current_output)
                
                # 检查是否有函数没有出现在输出中
                for func in func_infos:
                    if func not in results:
                        results[func] = f"Error: No output for function {func}"
                
                elapsed = time.time() - start_time
                verbose_print(f"Parsed {len(results)} function locations in {elapsed:.2f} seconds", verbose)
                return results
                
            except subprocess.CalledProcessError as e:
                verbose_print(f"Batch faddr2line --list failed with return code {e.returncode}", verbose)
                verbose_print(f"Stderr: {e.stderr}", verbose)
                # 回退到逐个处理函数
                verbose_print("Falling back to individual function processing", verbose)
                results = {}
                total_funcs = len(func_infos)
                for i, func_info in enumerate(func_infos):
                    verbose_print(f"Processing function {i+1}/{total_funcs}: {func_info}", verbose)
                    cmd = [abs_faddr2line_path, '--list', abs_target, func_info]
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                        results[func_info] = result.stdout
                    except subprocess.CalledProcessError as e2:
                        results[func_info] = f"Error: {e2.stderr}"
                return results
        else:
            # 标准批量处理模式 - 一次传递所有函数地址
            cmd = [abs_faddr2line_path, abs_target] + func_infos
            verbose_print(f"Executing batch command: {' '.join(cmd)}", verbose)
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                output = result.stdout
                verbose_print(f"faddr2line completed successfully, output length: {len(output)} chars", verbose)
                
                # 解析批量输出
                func_locations = {}
                current_func = None
                current_locations = []
                
                for line in output.splitlines():
                    line = line.rstrip()
                    if not line:
                        continue
                        
                    # 检查是否是新的函数条目
                    if line.endswith(':'):
                        # 保存前一个函数的信息
                        if current_func:
                            func_locations[current_func] = current_locations
                        
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
                        file_path = loc_match.group(2).strip()
                        line_no = loc_match.group(3).strip()
                        current_locations.append({
                            'func': func_name,
                            'file': file_path,
                            'line': line_no,
                            'inlined': inlined
                        })
                
                # 保存最后一个函数的信息
                if current_func:
                    func_locations[current_func] = current_locations
                    
                elapsed = time.time() - start_time
                verbose_print(f"Parsed {len(func_locations)} function locations in {elapsed:.2f} seconds", verbose)
                return func_locations
            except subprocess.CalledProcessError as e:
                verbose_print(f"faddr2line failed with return code {e.returncode}", verbose)
                verbose_print(f"Stderr: {e.stderr}", verbose)
                return {}
        
    except Exception as e:
        verbose_print(f"Error calling faddr2line: {str(e)}", verbose)
        return {}
    finally:
        # 恢复原始工作目录
        if 'original_cwd' in locals():
            try:
                verbose_print(f"Restoring working directory to: {original_cwd}", verbose)
                os.chdir(original_cwd)
            except Exception as e:
                verbose_print(f"Failed to restore working directory: {str(e)}", verbose)

def generate_html(parsed_lines, vmlinux_path, faddr2line_path, module_dirs=None, base_url=None, kernel_src=None, use_list=False, verbose=False, fast_mode=False):
    """生成交互式HTML页面，保留原始空格和格式"""
    if module_dirs is None:
        module_dirs = []
    
    verbose_print("Generating HTML content", verbose)
    start_time = time.time()
    
    # 使用绝对路径
    abs_vmlinux_path = os.path.abspath(vmlinux_path)
    abs_faddr2line_path = os.path.abspath(faddr2line_path)
    
    # 如果使用fast模式，优先使用fastfaddr2line.py
    if fast_mode:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        fast_faddr2line_path = os.path.join(script_dir, 'fastfaddr2line.py')
        if os.path.exists(fast_faddr2line_path):
            verbose_print(f"Using fastfaddr2line.py at {fast_faddr2line_path}", verbose)
            abs_faddr2line_path = fast_faddr2line_path
        else:
            verbose_print(f"Warning: fastfaddr2line.py not found at {fast_faddr2line_path}, using fallback", verbose)
    
    # 收集所有需要解析的函数信息（使用字典去重）
    func_info_map = defaultdict(list)  # func_info -> [line_index]
    module_funcs = defaultdict(set)   # module_name -> set(func_info)
    vmlinux_funcs = set()              # set(func_info)
    
    for idx, line_data in enumerate(parsed_lines):
        if line_data['expandable'] and line_data['func_info']:
            func_info = line_data['func_info']
            module_name = line_data['module_name']
            
            # 记录行索引
            func_info_map[func_info].append(idx)
            
            # 按目标分组
            if module_name:
                module_funcs[module_name].add(func_info)
            else:
                vmlinux_funcs.add(func_info)
    
    verbose_print(f"Found {len(vmlinux_funcs)} unique kernel functions", verbose)
    verbose_print(f"Found {len(module_funcs)} modules with functions", verbose)
    for module, funcs in module_funcs.items():
        verbose_print(f"Module {module}: {len(funcs)} functions", verbose)
    
    # 批量获取所有函数的位置信息
    func_locations_map = {}
    
    # 定义函数偏移量调整函数
    def adjust_func_info(func_info):
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
    
    # 1. 处理内核函数（去重后）
    if vmlinux_funcs:
        # 调整函数偏移量
        adjusted_vmlinux_funcs = set()
        for func in vmlinux_funcs:
            adjusted_func = adjust_func_info(func)
            adjusted_vmlinux_funcs.add(adjusted_func)
        
        vmlinux_funcs_list = list(adjusted_vmlinux_funcs)
        verbose_print(f"Processing {len(vmlinux_funcs_list)} adjusted kernel functions", verbose)
        
        # 如果是fast模式且处理的是vmlinux，使用fastfaddr2line.py
        if fast_mode and os.path.basename(abs_faddr2line_path) == 'fastfaddr2line.py':
            batch_results = call_faddr2line_batch(
                abs_faddr2line_path, 
                abs_vmlinux_path, 
                vmlinux_funcs_list, 
                use_list,
                kernel_src,
                verbose
            )
        else:
            batch_results = call_faddr2line_batch(
                abs_faddr2line_path, 
                abs_vmlinux_path, 
                vmlinux_funcs_list, 
                use_list,
                kernel_src,
                verbose
            )
            
        func_locations_map.update(batch_results)
        verbose_print(f"Resolved {len(batch_results)} kernel function locations", verbose)
    
    # 2. 处理模块函数（去重后）
    for module_name, funcs in module_funcs.items():
        verbose_print(f"Processing module {module_name} with {len(funcs)} functions", verbose)
        # 查找模块文件
        module_path = find_module_path(module_name, module_dirs, verbose)
            
        if not module_path or not os.path.isfile(module_path):
            verbose_print(f"Module file not found for {module_name}", verbose)
            continue
                
        verbose_print(f"Using module file: {module_path}", verbose)
        
        # 调整函数偏移量
        adjusted_funcs = set()
        for func in funcs:
            adjusted_func = adjust_func_info(func)
            adjusted_funcs.add(adjusted_func)
        
        funcs_list = list(adjusted_funcs)
        batch_results = call_faddr2line_batch(
            abs_faddr2line_path, 
            module_path, 
            funcs_list, 
            use_list,
            kernel_src,
            verbose
        )
        func_locations_map.update(batch_results)
        verbose_print(f"Resolved {len(batch_results)} function locations for module {module_name}", verbose)
    
    # 构建HTML字符串
    html_str = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Funcgraph Visualization</title>
    <style>
        body {{
            font-family: 'Courier New', monospace;
            background-color: #f5f5f5;
            padding: 20px;
            line-height: 1.5;
            margin: 0;
        }}
        .container {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow-x: auto;
        }}
        h1 {{
            text-align: center;
            color: #333;
            margin-bottom: 20px;
        }}
        .line-container {{
            display: flex;
            align-items: flex-start;
            padding: 2px 5px;
            border-radius: 3px;
            transition: background-color 0.2s;
            white-space: pre;
            font-family: 'Courier New', monospace;
            font-size: 14px;
        }}
        .line-container:hover {{
            background-color: #f0f0f0;
        }}
        .line-number {{
            display: inline-block;
            width: 40px;
            text-align: right;
            padding-right: 10px;
            color: #999;
            user-select: none;
            flex-shrink: 0;
        }}
        .line-content {{
            flex-grow: 1;
        }}
        .expand-btn {{
            display: inline-block;
            width: 16px;
            height: 16px;
            background-color: #4CAF50;
            color: white;
            border-radius: 50%;
            text-align: center;
            line-height: 16px;
            font-size: 12px;
            margin-left: 5px;
            cursor: pointer;
            opacity: 0.5;
            transition: opacity 0.2s;
            user-select: none;
            vertical-align: middle;
            flex-shrink: 0;
        }}
        .expand-btn:hover {{
            opacity: 1;
        }}
        .expanded-content {{
            display: none;
            margin-left: 50px;
            padding: 10px;
            background-color: #f9f9f9;
            border-left: 2px solid #4CAF50;
            border-radius: 0 4px 4px 0;
            white-space: pre;
            font-family: 'Courier New', monospace;
            font-size: 13px;
        }}
        .location-link {{
            display: block;
            padding: 3px 5px;
            color: #0366d6;
            text-decoration: none;
            margin: 2px 0;
            border-radius: 3px;
            white-space: pre;
        }}
        .location-link:hover {{
            background-color: #eaeaea;
            text-decoration: underline;
        }}
        .source-line {{
            display: block;
            padding: 2px 0;
            white-space: pre;
            font-family: 'Courier New', monospace;
            font-size: 12px;
        }}
        .current-line {{
            background-color: #fff3cd;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Funcgraph Visualization v0.1 @dolinux</h1>
        <div id="content">
"""

    # 添加行号并输出原始ftrace日志
    for idx, line_data in enumerate(parsed_lines):
        line_id = f"line_{idx}"
        line_number = idx + 1  # 行号从1开始
        escaped_line = escape_html_preserve_spaces(line_data["raw_line"])
        
        html_str += f'<div class="line-container" id="{line_id}_container">'
        html_str += f'<span class="line-number">{line_number}</span>'
        html_str += f'<span class="line-content">{escaped_line}</span>'
        
        if line_data['expandable'] and line_data['func_info']:
            html_str += f'<span class="expand-btn" onclick="toggleExpand(\'{line_id}\')">+</span>'
        
        html_str += '</div>'
        
        if line_data['expandable'] and line_data['func_info']:
            # 获取调整后的函数信息
            func_info = line_data['func_info']
            adjusted_func_info = adjust_func_info(func_info)
            
            # 从预取的数据中获取位置信息
            locations = func_locations_map.get(adjusted_func_info, {})
            
            html_str += f'<div class="expanded-content" id="{line_id}_content">'
            
            if locations:
                # 检查是结构化数据还是原始输出
                if isinstance(locations, dict) and 'func' in str(next(iter(locations.values()), {})):
                    # 结构化数据（标准模式）
                    loc_list = locations.get(adjusted_func_info, [])
                    for loc in loc_list:
                        if base_url:
                            file_path = loc['file']
                            if '[' in file_path and ']' in file_path:
                                file_path = re.sub(r'\[.*?\]', '', file_path).strip()
                            
                            # 获取相对于内核源码的路径
                            relative_path = get_relative_path(file_path, kernel_src)
                            
                            # 构建URL
                            if base_url.endswith('/'):
                                url = f"{base_url}{relative_path}#L{loc['line']}"
                            else:
                                url = f"{base_url}/{relative_path}#L{loc['line']}"
                            
                            link_text = f"{loc['func']} at {relative_path}:{loc['line']}"
                            if loc['inlined']:
                                link_text = f"(inlined) {link_text}"
                            
                            escaped_link = escape_html_preserve_spaces(link_text)
                            escaped_url = html.escape(url)
                            html_str += f'<a class="location-link" href="{escaped_url}" target="_blank">{escaped_link}</a>'
                        else:
                            file_path = loc['file']
                            if '[' in file_path and ']' in file_path:
                                file_path = re.sub(r'\[.*?\]', '', file_path).strip()
                            
                            link_text = f"{loc['func']} at {file_path}:{loc['line']}"
                            if loc['inlined']:
                                link_text = f"(inlined) {link_text}"
                            escaped_link = escape_html_preserve_spaces(link_text)
                            html_str += f'<div class="location-link">{escaped_link}</div>'
                elif isinstance(locations, str):
                    # 原始输出（--list模式）
                    html_str += parse_list_output(locations, base_url, kernel_src)
                else:
                    # 未知格式
                    html_str += f'<div class="location-link">Source information unavailable</div>'
            else:
                html_str += '<div class="location-link">Source information unavailable</div>'
            
            html_str += '</div>'
    
    html_str += """
        </div>
    </div>
    
    <script>
        function toggleExpand(lineId) {
            const content = document.getElementById(lineId + '_content');
            const btn = event.target;
            
            if (content.style.display === 'block') {
                content.style.display = 'none';
                btn.textContent = '+';
            } else {
                content.style.display = 'block';
                btn.textContent = '-';
            }
        }
    </script>
</body>
</html>
"""
    
    elapsed = time.time() - start_time
    verbose_print(f"HTML generation completed in {elapsed:.2f} seconds", verbose)
    return html_str

def main():
    parser = argparse.ArgumentParser(description='Convert ftrace output to interactive HTML')
    parser.add_argument('ftrace_file', help='Path to ftrace output file')
    parser.add_argument('--vmlinux', required=True, help='Path to vmlinux file')
    parser.add_argument('--kernel-src', 
                        help='Path to kernel source root (e.g., /path/to/linux-source)')
    parser.add_argument('--module-dirs', nargs='*', default=[],
                        help='Directories to search for kernel modules')
    parser.add_argument('--base-url', help='Base URL for source code links')
    parser.add_argument('--output', default='ftrace_viz.html', help='Output HTML file path')
    parser.add_argument('--auto-search', action='store_true', 
                        help='Automatically search common module directories')
    parser.add_argument('--verbose', action='store_true', 
                        help='Enable verbose output for debugging')
    parser.add_argument('--fast', action='store_true', 
                        help='Use fastfaddr2line.py for vmlinux processing')
    parser.add_argument('--use-external', action='store_true', 
                        help='Force using external faddr2line')
    args = parser.parse_args()

    # 检查gawk可用性并决定是否使用--list选项
    gawk_available = check_gawk_available()
    use_list = gawk_available
    
    if args.verbose:
        print("[INFO] Verbose mode enabled", file=sys.stderr)
        print(f"[INFO] gawk available: {gawk_available}", file=sys.stderr)
        print(f"[INFO] Using faddr2line --list option: {use_list}", file=sys.stderr)
        print(f"[INFO] Using fast mode: {args.fast}", file=sys.stderr)
    
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
    
    # 确定faddr2line工具路径
    faddr2line_path = None
    if args.kernel_src:
        potential_path = os.path.join(args.kernel_src, 'scripts', 'faddr2line')
        if os.path.isfile(potential_path):
            faddr2line_path = potential_path
            verbose_print(f"Found faddr2line in kernel source: {potential_path}", args.verbose)
    
    if not faddr2line_path or args.use_external:
        try:
            result = subprocess.run(['which', 'faddr2line'], capture_output=True, text=True, check=True)
            faddr2line_path = result.stdout.strip()
            verbose_print(f"Found faddr2line in PATH: {faddr2line_path}", args.verbose)
        except Exception as e:
            verbose_print(f"faddr2line not in PATH: {str(e)}", args.verbose)
            faddr2line_path = './scripts/faddr2line'
            if os.path.isfile(faddr2line_path):
                verbose_print(f"Using local faddr2line: {faddr2line_path}", args.verbose)
            else:
                print(f"Error: Cannot locate faddr2line tool", file=sys.stderr)
                sys.exit(1)
    
    # 使用绝对路径
    faddr2line_path = os.path.abspath(faddr2line_path)
    vmlinux_path = os.path.abspath(args.vmlinux)
    kernel_src = os.path.abspath(args.kernel_src) if args.kernel_src else None
    
    verbose_print(f"Using faddr2line: {faddr2line_path}", args.verbose)
    verbose_print(f"Using vmlinux: {vmlinux_path}", args.verbose)
    if kernel_src:
        verbose_print(f"Using kernel source: {kernel_src}", args.verbose)
    
    # 解析ftrace文件
    start_time = time.time()
    parsed_lines = parse_ftrace_file(args.ftrace_file, args.verbose)
    parse_time = time.time() - start_time
    verbose_print(f"File parsing completed in {parse_time:.2f} seconds", args.verbose)
    
    # 计算统计信息
    expandable_count = sum(1 for l in parsed_lines if l['expandable'])
    module_count = sum(1 for l in parsed_lines if l['expandable'] and l['module_name'])
    kernel_count = expandable_count - module_count
    
    # 生成HTML
    html_content = generate_html(
        parsed_lines, 
        vmlinux_path, 
        faddr2line_path,
        module_dirs=module_dirs,
        base_url=args.base_url,
        kernel_src=kernel_src,
        use_list=use_list,
        verbose=args.verbose,
        fast_mode=args.fast  # 传递fast_mode参数
    )
    
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
        print(f"\n[SUMMARY]")
        print(f"Total lines processed: {len(parsed_lines)}")
        print(f"Expandable entries: {expandable_count}")
        print(f"  - Kernel functions: {kernel_count}")
        print(f"  - Module functions: {module_count}")
        print(f"Output file: {os.path.abspath(args.output)}")

if __name__ == '__main__':
    main()
