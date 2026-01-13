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

# ==================== 配置变量 ====================
# 修改这里来定制 HTML 的显示
APP_VERSION = "0.2"      # 应用版本号
APP_AUTHOR = "@dolinux"  # 作者信息
APP_TITLE = "FuncGraph"  # 应用标题
# ================================================

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

def highlight_ftrace_line(text):
    """
    为ftrace日志行添加语法高亮，保持原始对齐和格式
    
    仅添加HTML span标签用于高亮，保留原始字符不转义
    特殊处理：<...> 形式的标识符中的 < 和 > 替换为空格
                以避免浏览器误解为HTML标签，同时保持原始对齐和宽度
    
    高亮的元素：
    - CPU编号: <span class="hl-cpu">3)</span>
    - 时间数值: <span class="hl-time">0.208</span>
    - 时间单位: <span class="hl-unit">us</span>
    - 函数名: <span class="hl-func">mutex_unlock</span>
    - 十六进制地址: <span class="hl-addr">0xf4</span>
    - 注释: <span class="hl-comment">/* ... */</span>
    - 符号括号: <span class="hl-symbol">{}</span>
    
    返回带有HTML span标签的高亮文本，空格、缩进和所有原始字符完全保持
    """
    if not text:
        return text
    
    # 特殊处理：<...> 形式的标识符中的 < 和 > 替换为空格
    # 匹配模式：< 后跟一个或多个非 > 字符，然后是 >
    # 替换为：空格 + 内容 + 空格（保持原始宽度）
    text = re.sub(r'<([^<>]+)>', r' \1 ', text)
    
    # 1. 高亮CPU编号 (格式: 空格+数字) 在行首
    text = re.sub(r'^(\s*\d+\))', r'<span class="hl-cpu">\1</span>', text)
    
    # 2. 高亮注释部分 (/* ... */)
    text = re.sub(r'(/\*[^*]*(?:\*+[^/*][^*]*)*\*+/)', r'<span class="hl-comment">\1</span>', text)
    
    # 3. 高亮十六进制地址 (0x...)
    text = re.sub(r'(0[xX][0-9a-fA-F]+)', r'<span class="hl-addr">\1</span>', text)
    
    # 4. 高亮时间数值 (小数点格式)
    text = re.sub(r'(\d+\.\d+)', r'<span class="hl-time">\1</span>', text)
    
    # 5. 高亮时间单位 (us, ms, ns, ks)
    text = re.sub(r'\b(us|ms|ns|ks)\b', r'<span class="hl-unit">\1</span>', text)
    
    # 6. 高亮函数名 (标识符后跟( { 或 ;)
    text = re.sub(r'([a-zA-Z_]\w*)(?=\s*[\({;])', r'<span class="hl-func">\1</span>', text)
    
    # 7. 高亮括号和大括号
    text = re.sub(r'([{}()\[\];])', r'<span class="hl-symbol">\1</span>', text)
    
    return text

def escape_for_pre(text):
    """专为<pre>标签设计的转义函数，保留原始格式"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def check_pygments_available():
    """检查Pygments库是否可用"""
    try:
        import pygments
        from pygments.lexers import CLexer
        from pygments.formatters import HtmlFormatter
        return True
    except ImportError:
        return False

def highlight_c_code(text):
    """
    轻量级C代码语法高亮，使用简单的正则替换
    
    仅高亮关键字、字符串、注释和Linux内核特定类型，保持简洁
    """
    try:
        # 先对HTML特殊字符进行转义，避免破坏HTML结构
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        # 使用占位符临时保存注释，避免冲突
        comments = []
        def save_comment(match):
            idx = len(comments)
            comments.append(match.group(0))
            return f'___COMMENT_{idx}___'
        
        # 保存所有注释（转义后的文本）
        text = re.sub(r'//[^\n]*', save_comment, text)
        
        # 字符串（双引号）
        text = re.sub(r'("(?:\\.|[^"\\])*")', r'<span class="c-string">\1</span>', text)
        
        # 字符（单引号）
        text = re.sub(r"('(?:\\.|[^'\\])*')", r'<span class="c-string">\1</span>', text)
        
        # C语言关键字
        keywords = r'\b(void|int|char|float|double|struct|union|enum|typedef|return|if|else|for|while|do|switch|case|break|continue|default|static|extern|const|volatile|auto|register|goto|sizeof|restrict|inline|_Bool|_Complex|_Imaginary)\b'
        text = re.sub(keywords, r'<span class="c-keyword">\1</span>', text)
        
        # Linux内核特定的整数类型 (u8, u16, u32, u64, s8, s16, s32, s64)
        kernel_int_types = r'\b(u8|u16|u32|u64|s8|s16|s32|s64|__u8|__u16|__u32|__u64|__s8|__s16|__s32|__s64)\b'
        text = re.sub(kernel_int_types, r'<span class="kernel-type">\1</span>', text)
        
        # Linux内核特定的typedef类型
        kernel_typedef_types = r'\b(pid_t|uid_t|gid_t|dev_t|ino_t|off_t|size_t|ssize_t|atomic_t|spinlock_t|rwlock_t|mutex_t|sector_t|loff_t|umode_t|mode_t|nlink_t|dma_addr_t|gfp_t|fmode_t)\b'
        text = re.sub(kernel_typedef_types, r'<span class="kernel-type">\1</span>', text)
        
        # Linux内核常见结构体和类型
        kernel_structs = r'\b(list_head|hlist_head|hlist_node|rbtree_node|rb_node|dentry|inode|file|super_block|vfsmount|task_struct|mm_struct|page|zone|vm_area_struct)\b'
        text = re.sub(kernel_structs, r'<span class="kernel-type">\1</span>', text)
        
        # Linux内核属性修饰符(__user, __kernel, __iomem 等)
        kernel_attributes = r'\b(__user|__kernel|__iomem|__percpu|__rcu|__acquire|__release|__must_hold|__acquires|__releases)\b'
        text = re.sub(kernel_attributes, r'<span class="kernel-attr">\1</span>', text)
        
        # Linux内核常用宏常量
        kernel_macros = r'\b(GFP_KERNEL|GFP_ATOMIC|GFP_NOFS|GFP_NOIO|GFP_NOWAIT|GFP_TEMPORARY|NULL|true|false|PAGE_SIZE|TASK_RUNNING|TASK_INTERRUPTIBLE|TASK_UNINTERRUPTIBLE|ENOMEM|EINVAL|ENOENT|EAGAIN|EBUSY)\b'
        text = re.sub(kernel_macros, r'<span class="kernel-macro">\1</span>', text)
        
        # 十六进制数和整数
        text = re.sub(r'\b(0x[0-9a-fA-F]+|0[0-7]+|[0-9]+)\b', r'<span class="c-number">\1</span>', text)
        
        # 恢复注释，添加高亮
        for idx, comment in enumerate(comments):
            text = text.replace(f'___COMMENT_{idx}___', f'<span class="c-comment">{comment}</span>')
        
        return text
    except Exception as e:
        verbose_print(f"Failed to highlight C code: {str(e)}", False)
        return text

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

    # 尝试处理模块名中下划线和中划线的转换
    # 内核模块加载时会将中划线(-)替换为下划线(_)
    # 所以需要尝试反向转换
    if '_' in module_name:
        module_name_hyphen = module_name.replace('_', '-')
        verbose_print(f"Trying with hyphenated name: {module_name_hyphen}", verbose)

        # 递归调用查找函数
        result = find_module_path(module_name_hyphen, module_dirs, verbose)
        if result:
            return result

    if '-' in module_name:
        module_name_underscore = module_name.replace('-', '_')
        verbose_print(f"Trying with underscored name: {module_name_underscore}", verbose)

        # 递归调用查找函数
        result = find_module_path(module_name_underscore, module_dirs, verbose)
        if result:
            return result

    verbose_print(f"Module '{module_name}' not found in any directory", verbose)
    return None

def build_source_url(base_url, relative_path, line_no):
    """构建源代码链接URL，根据base_url决定使用#L还是#格式

    参数：
        base_url: 基础URL
        relative_path: 相对路径
        line_no: 行号

    返回：
        完整的URL字符串
    """
    if not base_url:
        return None

    # 检查是否是OpenGrok URL（包含opengrok字符串）
    is_opengrok = 'opengrok' in base_url.lower()

    # 使用#还是#L
    line_anchor = '#' if is_opengrok else '#L'

    if base_url.endswith('/'):
        return f"{base_url}{relative_path}{line_anchor}{line_no}"
    else:
        return f"{base_url}/{relative_path}{line_anchor}{line_no}"

def clean_file_path(file_path, kernel_src=None, path_prefix=None, module_src=None):
    """清理文件路径，去除内核源码根目录和其他冗余信息

    参数：
        file_path: 文件路径
        kernel_src: 内核源码根目录（单个路径）
        path_prefix: 备选的路径前缀（可以是列表）
        module_src: 模块源码根目录（可以是列表）
    """
    if not file_path:
        return file_path

    # 确保path_prefix和module_src是列表
    if path_prefix and not isinstance(path_prefix, list):
        path_prefix = [path_prefix]
    if module_src and not isinstance(module_src, list):
        module_src = [module_src]

    # 尝试所有可能的源码路径（按优先级顺序）
    all_src_paths = []

    # 1. 内核源码路径（单个）
    if kernel_src:
        all_src_paths.append(kernel_src)

    # 2. 模块源码路径
    if module_src:
        all_src_paths.extend(module_src)

    # 3. Path prefix路径
    if path_prefix:
        all_src_paths.extend(path_prefix)

    # 尝试匹配每个源码路径
    for src_path in all_src_paths:
        if not src_path:
            continue

        # 规范化路径
        src_path_norm = os.path.normpath(src_path)
        file_path_norm = os.path.normpath(file_path)

        # 处理相对路径和绝对路径
        if os.path.isabs(src_path):
            # 绝对路径：直接比较
            if file_path_norm.startswith(src_path_norm):
                # 计算相对路径
                relative_path = file_path_norm[len(src_path_norm):].lstrip(os.sep)
                return relative_path
        else:
            # 相对路径：转换为绝对路径进行比较
            abs_src_path = os.path.abspath(src_path)
            abs_src_path_norm = os.path.normpath(abs_src_path)
            if file_path_norm.startswith(abs_src_path_norm):
                # 计算相对路径
                relative_path = file_path_norm[len(abs_src_path_norm):].lstrip(os.sep)
                return relative_path

    # 去除内核模块标记，如[kernel]
    if '[' in file_path and ']' in file_path:
        file_path = re.sub(r'\[.*?\]', '', file_path).strip()

    # 去除discriminator信息，如(discriminator 9)
    file_path = re.sub(r'\s*\(discriminator\s+\d+\)\s*$', '', file_path)
    file_path = re.sub(r'\s*\(inlined\)\s*$', '', file_path)
    file_path = re.sub(r'\s*\(.*?\)\s*$', '', file_path)

    return file_path.strip()

def get_relative_path(full_path, base_path):
    """获取相对于基路径的相对路径"""
    if not base_path or not full_path:
        return full_path
    
    # 规范化路径
    base_path = os.path.normpath(base_path)
    full_path = os.path.normpath(full_path)
    
    # 如果路径已经是相对路径，直接返回
    if not os.path.isabs(full_path):
        return full_path
    
    # 确保基路径以分隔符结尾
    if not base_path.endswith(os.sep):
        base_path += os.sep
    
    # 检查完整路径是否在基路径下
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

def parse_list_output(output, base_url=None, kernel_src=None, highlight_code=False, path_prefix=None, module_src=None):
    """解析faddr2line --list的输出并转换为HTML

    参数：
        output: faddr2line --list 的输出
        base_url: 源代码链接的基础URL
        kernel_src: 内核源码根目录（单个路径）
        highlight_code: 是否对C源代码进行语法高亮
        path_prefix: 备选的路径前缀（可以是列表）
        module_src: 模块源码根目录（可以是列表）
    """
    lines = output.splitlines()
    html_output = []
    
    # 正则表达式匹配函数块头（如 "function+offset/size:"）
    func_header_re = re.compile(r'^(.*?[^+]+[\+\w]+/[0-9a-fx]+):$')
    
    # 修复正则表达式以匹配包含discriminator信息的行
    # 修改正则表达式，使其能够匹配包含discriminator的行
    loc_pattern = re.compile(r'^(?:\(inlined by\)\s+)?(.+?)\s+at\s+(.+?):(\d+).*$')
    
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

def call_faddr2line_batch(faddr2line_path, target, func_infos, use_list=False, kernel_src=None, verbose=False, path_prefix=None, module_src=None, module_srcs=None):
    """调用faddr2line获取多个函数的源代码位置信息

    参数：
        faddr2line_path: faddr2line工具路径
        target: 目标文件（vmlinux等）
        func_infos: 函数信息列表
        use_list: 是否使用--list模式
        kernel_src: 内核源码根目录（单个路径）
        verbose: 是否输出详细信息
        path_prefix: 备选的路径前缀（可以是列表，用于fastfaddr2line）
        module_src: 模块源码根目录（可以是列表，用于路径清理）
        module_srcs: 模块源码根目录（可以是列表，用于fastfaddr2line查找源码）
    """
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
            cmd = [abs_faddr2line_path, '--list']

            # 如果是fastfaddr2line，添加额外参数
            if os.path.basename(abs_faddr2line_path) == 'fastfaddr2line.py':
                # 添加path_prefix参数（保持原样）
                if path_prefix:
                    # 确保path_prefix是列表
                    if not isinstance(path_prefix, list):
                        path_prefix = [path_prefix]
                    # 为每个path_prefix添加参数（保持原样）
                    for prefix in path_prefix:
                        if prefix:
                            cmd.extend(['--path-prefix', prefix])
                            verbose_print(f"Adding --path-prefix parameter: {prefix}", verbose)

                # 添加module_srcs参数
                if module_srcs:
                    # 确保module_srcs是列表
                    if not isinstance(module_srcs, list):
                        module_srcs = [module_srcs]
                    # 为每个module_src添加参数
                    for module_src in module_srcs:
                        if module_src:
                            cmd.extend(['--module-srcs', os.path.abspath(module_src)])
                            verbose_print(f"Adding --module-srcs parameter: {os.path.abspath(module_src)}", verbose)

            cmd.extend([abs_target] + func_infos)
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
                    cmd = [abs_faddr2line_path, '--list', abs_target]

                    # 如果是fastfaddr2line，添加额外参数
                    if os.path.basename(abs_faddr2line_path) == 'fastfaddr2line.py':
                        # 添加path_prefix参数（保持原样）
                        if path_prefix:
                            # 确保path_prefix是列表
                            if not isinstance(path_prefix, list):
                                path_prefix = [path_prefix]
                            # 为每个path_prefix添加参数（保持原样）
                            for prefix in path_prefix:
                                if prefix:
                                    cmd.extend(['--path-prefix', prefix])

                        # 添加module_srcs参数
                        if module_srcs:
                            # 确保module_srcs是列表
                            if not isinstance(module_srcs, list):
                                module_srcs = [module_srcs]
                            # 为每个module_src添加参数
                            for module_src in module_srcs:
                                if module_src:
                                    cmd.extend(['--module-srcs', os.path.abspath(module_src)])

                    cmd.append(func_info)
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                        results[func_info] = result.stdout
                    except subprocess.CalledProcessError as e2:
                        results[func_info] = f"Error: {e2.stderr}"
                return results
        else:
            # 标准批量处理模式 - 一次传递所有函数地址
            cmd = [abs_faddr2line_path, abs_target]

            # 如果是fastfaddr2line，添加额外参数
            if os.path.basename(abs_faddr2line_path) == 'fastfaddr2line.py':
                # 添加path_prefix参数（保持原样）
                if path_prefix:
                    # 确保path_prefix是列表
                    if not isinstance(path_prefix, list):
                        path_prefix = [path_prefix]
                    # 为每个path_prefix添加参数（保持原样）
                    for prefix in path_prefix:
                        if prefix:
                            cmd.extend(['--path-prefix', prefix])
                            verbose_print(f"Adding --path-prefix parameter: {prefix}", verbose)

                # 添加module_srcs参数
                if module_srcs:
                    # 确保module_srcs是列表
                    if not isinstance(module_srcs, list):
                        module_srcs = [module_srcs]
                    # 为每个module_src添加参数
                    for module_src in module_srcs:
                        if module_src:
                            cmd.extend(['--module-srcs', os.path.abspath(module_src)])
                            verbose_print(f"Adding --module-srcs parameter: {os.path.abspath(module_src)}", verbose)

            cmd.extend(func_infos)
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

def get_environment_info():
    """收集运行环境信息"""
    import platform
    from datetime import datetime
    
    env_info = []
    
    # Python 版本
    python_version = f"{platform.python_version()}"
    env_info.append(("Python", python_version))
    
    # 操作系统
    system = platform.system()
    release = platform.release()
    env_info.append(("OS", f"{system} {release}"))
    
    # 主机名
    hostname = platform.node()
    env_info.append(("Hostname", hostname))
    
    # 处理器信息
    processor = platform.processor()
    if processor:
        env_info.append(("Processor", processor))
    
    # 生成时间
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    env_info.append(("Generated At", current_time))
    
    return env_info

def format_args_info(args):
    """格式化脚本接收到的实际参数"""
    if not args:
        return []

    # 获取所有参数
    args_dict = vars(args)

    # 定义要隐藏的内部参数
    hidden_params = {'verbose'}  # 不需要在HTML中显示的参数

    info_items = []
    for key, value in args_dict.items():
        if key in hidden_params:
            continue

        # 格式化参数显示
        display_key = key.replace('_', ' ').title()

        # 处理值的显示
        if isinstance(value, bool):
            display_value = "Yes" if value else "No"
        elif isinstance(value, list):
            if value:
                # 显示完整的列表内容，用逗号分隔
                display_value = ", ".join(str(v) for v in value)
            else:
                display_value = "(empty)"
        elif isinstance(value, str) and len(value) > 60:
            # 长字符串只显示文件名或后面部分
            if '/' in value:
                display_value = os.path.basename(value)
            else:
                display_value = value[-50:]
        else:
            display_value = str(value) if value is not None else "(none)"

        info_items.append((display_key, display_value))

    return info_items

def parse_module_url(module_url_str, base_url):
    """
    解析module_url参数，返回模块名到URL的映射

    参数格式：
    - None: 返回空字典，所有模块使用base_url
    - "http://example.com": 返回空字典，所有模块使用这个URL
    - "http://example.com,mod1,mod2,http://example.com/other,mod3,mod4":
      - 'http://example.com' 后面有 ',mod1,mod2'，所以 mod1 和 mod2 使用 http://example.com
      - 'http://example.com/other' 后面有 ',mod3,mod4'，所以 mod3 和 mod4 使用 http://example.com/other
      - 其他模块使用 base_url
    - "http://example.com,http://example.com/other,mod1,mod2":
      - 'http://example.com' 后面没有逗号连接的模块
      - 'http://example.com/other' 后面有 ',mod1,mod2'，所以 mod1 和 mod2 使用 http://example.com/other
      - 其他模块使用 http://example.com

    返回值：
    - module_url_map: 模块名 -> URL 的映射
    - default_module_url: 默认模块URL（未在映射中的模块使用）
    """
    if not module_url_str:
        # 没有提供module_url，使用base_url
        return {}, base_url

    # 按逗号分割
    parts = [part.strip() for part in module_url_str.split(',')]

    # 解析URL和模块名的映射关系
    module_url_map = {}

    # 收集所有URL和它们对应的模块名
    url_to_modules = {}  # URL -> [module1, module2, ...]
    urls_without_modules = []  # 没有模块名的URL

    current_url = None
    pending_modules = []

    for part in parts:
        if part.startswith(('http://', 'https://')):
            # 如果之前有URL和模块名，保存映射
            if current_url and pending_modules:
                url_to_modules[current_url] = pending_modules
                pending_modules = []
            elif current_url and not pending_modules:
                # 这个URL后面没有模块名
                urls_without_modules.append(current_url)

            # 设置当前URL
            current_url = part
        else:
            # 模块名
            pending_modules.append(part)

    # 处理最后的URL和模块名
    if current_url:
        if pending_modules:
            url_to_modules[current_url] = pending_modules
        else:
            urls_without_modules.append(current_url)

    # 确定默认URL
    default_url = base_url

    # 如果有URL没有模块名，使用第一个这样的URL作为默认
    if urls_without_modules:
        default_url = urls_without_modules[0]
    # 如果所有URL都有模块名，使用base_url作为默认
    # （因为没有指定默认URL，所以使用base_url）

    # 构建模块到URL的映射
    for url, modules in url_to_modules.items():
        for module_name in modules:
            module_url_map[module_name] = url

    return module_url_map, default_url


def generate_html(parsed_lines, vmlinux_path, faddr2line_path, module_dirs=None, base_url=None, module_url=None, kernel_src=None, use_list=False, verbose=False, fast_mode=False, highlight_code=False, path_prefix=None, module_src=None, module_srcs=None, script_args=None):
    """生成交互式HTML页面，保留原始空格和格式"""
    if module_dirs is None:
        module_dirs = []

    # 解析module_url参数
    module_url_map, default_module_url = parse_module_url(module_url, base_url)

    verbose_print(f"Module URL map: {module_url_map}", verbose)
    verbose_print(f"Default module URL: {default_module_url}", verbose)

    # 确保path_prefix、module_src和module_srcs是列表
    if path_prefix and not isinstance(path_prefix, list):
        path_prefix = [path_prefix]
    if module_src and not isinstance(module_src, list):
        module_src = [module_src]
    if module_srcs and not isinstance(module_srcs, list):
        module_srcs = [module_srcs]

    # kernel_src保持单个路径（可以是None）

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
                verbose,
                path_prefix,
                module_src,
                module_srcs
            )
        else:
            batch_results = call_faddr2line_batch(
                abs_faddr2line_path,
                abs_vmlinux_path,
                vmlinux_funcs_list,
                use_list,
                kernel_src,
                verbose,
                None,  # 原生faddr2line不支持path_prefix
                module_src,
                None   # 原生faddr2line不支持module_srcs
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
        # 如果使用fastfaddr2line.py，传递path_prefix和module_srcs；否则传递None
        should_pass_fast_args = fast_mode and os.path.basename(abs_faddr2line_path) == 'fastfaddr2line.py'
        batch_results = call_faddr2line_batch(
            abs_faddr2line_path,
            module_path,
            funcs_list,
            use_list,
            kernel_src,
            verbose,
            path_prefix if should_pass_fast_args else None,
            module_src,
            module_srcs if should_pass_fast_args else None
        )
        func_locations_map.update(batch_results)
        verbose_print(f"Resolved {len(batch_results)} function locations for module {module_name}", verbose)
    
    # 计算统计数据
    total_lines = len(parsed_lines)
    expandable_entries = sum(1 for l in parsed_lines if l['expandable'])
    
    # 定义提取行号的函数
    def extract_line_number(location_str, kernel_src, path_prefix=None, module_src=None):
        """从位置字符串中提取行号"""
        if not location_str:
            return None

        # 尝试匹配各种行号格式
        # 1. 标准格式: file.c:1234
        # 2. 带discriminator: file.c:1234 (discriminator 9)
        # 3. 带inlined: file.c:1234 (inlined)
        # 4. 其他格式

        # 首先清理字符串
        clean_str = clean_file_path(location_str, kernel_src, path_prefix, module_src)

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

    # 定义从位置字符串提取文件路径的函数
    def extract_file_path(location_str, kernel_src, path_prefix=None, module_src=None):
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
    
    # 生成信息面板内容
    info_items = format_args_info(script_args)
    env_items = get_environment_info()
    
    info_content_html = ""
    
    # 添加环境信息部分
    if env_items:
        info_content_html += '                <div style="font-weight: 600; color: var(--text-color); margin-bottom: 8px; font-size: 11px;">Environment</div>\n'
        for label, value in env_items:
            info_content_html += f'                <div class="info-item"><div class="info-label">{label}:</div><div class="info-value">{html.escape(str(value))}</div></div>\n'
        info_content_html += '                <div style="border-top: 1px solid var(--border-color); margin: 8px 0;"></div>\n'
    
    # 添加脚本参数部分
    if info_items:
        info_content_html += '                <div style="font-weight: 600; color: var(--text-color); margin-bottom: 8px; font-size: 11px;">Parameters</div>\n'
        for label, value in info_items:
            info_content_html += f'                <div class="info-item"><div class="info-label">{label}:</div><div class="info-value">{html.escape(str(value))}</div></div>\n'
    
    if not env_items and not info_items:
        info_content_html = '                <div style="color: var(--summary-text); font-size: 12px;">No information available</div>'
    
    # 构建HTML字符串
    html_str = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Funcgraph Visualization</title>
    <style>
        :root {{
            --bg-color: #f5f5f5;
            --container-bg: white;
            --text-color: #333;
            --line-hover: #f0f0f0;
            --line-selected: #e3f2fd;
            --line-highlighted: #fff8e1;
            --line-keyboard-select: #e1f5fe;
            --border-color: #ddd;
            --summary-bg: #f8f9fa;
            --summary-text: #6c757d;
            --btn-primary: #4CAF50;
            --btn-primary-hover: #45a049;
            --btn-danger: #f44336;
            --btn-danger-hover: #d32f2f;
            --link-color: #0366d6;
            --expanded-bg: #f9f9f9;
        }}
        [data-theme="dark"] {{
            --bg-color: #1a1a1a;
            --container-bg: #2d2d2d;
            --text-color: #e0e0e0;
            --line-hover: #3a3a3a;
            --line-selected: #2c3e50;
            --line-highlighted: #5d4037;
            --line-keyboard-select: #01579b;
            --border-color: #444;
            --summary-bg: #2c2c2c;
            --summary-text: #aaa;
            --btn-primary: #4CAF50;
            --btn-primary-hover: #45a049;
            --btn-danger: #f44336;
            --btn-danger-hover: #d32f2f;
            --link-color: #64b5f6;
            --expanded-bg: #363636;
        }}
        body {{
            font-family: 'Courier New', monospace;
            background-color: var(--bg-color);
            padding: 20px;
            line-height: 1.5;
            margin: 0;
            color: var(--text-color);
            transition: background-color 0.3s, color 0.3s;
        }}
        .container {{
            background: var(--container-bg);
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow-x: auto;
            transition: background-color 0.3s;
        }}
        h1 {{
            text-align: center;
            color: var(--text-color);
            margin-bottom: 15px;
            font-size: 26px;
            font-weight: 600;
            letter-spacing: 1px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .title-main {{
            display: inline;
        }}
        .title-meta {{
            display: flex;
            align-items: center;
            gap: 8px;
            white-space: nowrap;
        }}
        .version-badge {{
            display: inline-block;
            color: var(--text-color);
            opacity: 0.6;
            padding: 0 6px;
            font-size: 11px;
            font-weight: 500;
        }}
        [data-theme="dark"] .version-badge {{
            opacity: 0.6;
        }}
        .author-badge {{
            display: inline-block;
            color: var(--text-color);
            font-size: 11px;
            padding: 0 6px;
            border-left: 1px solid var(--border-color);
            opacity: 0.5;
            padding-left: 10px;
        }}
        .info-panel {{
            margin-top: 12px;
            padding: 12px;
            background-color: rgba(0, 0, 0, 0.02);
            border-radius: 6px;
            border-left: 3px solid var(--btn-primary);
        }}
        [data-theme="dark"] .info-panel {{
            background-color: rgba(255, 255, 255, 0.03);
        }}
        .info-toggle {{
            display: flex;
            align-items: center;
            cursor: pointer;
            user-select: none;
            padding: 8px;
            margin: -8px;
            font-size: 13px;
            font-weight: 500;
            color: var(--text-color);
            transition: opacity 0.2s;
        }}
        .info-toggle:hover {{
            opacity: 0.8;
        }}
        .info-toggle-icon {{
            display: inline-block;
            margin-right: 8px;
            transition: transform 0.3s;
            font-size: 14px;
        }}
        .info-toggle-icon.expanded {{
            transform: rotate(90deg);
        }}
        .info-panel {{
            display: none;
            margin-top: 8px;
            padding: 12px;
            background-color: rgba(0, 0, 0, 0.02);
            border-radius: 4px;
            border-left: 3px solid var(--btn-primary);
        }}
        .info-panel.show {{
            display: block;
        }}
        [data-theme="dark"] .info-panel {{
            background-color: rgba(255, 255, 255, 0.03);
        }}
        .info-item {{
            display: flex;
            margin-bottom: 6px;
            font-size: 11px;
            line-height: 1.5;
        }}
        .info-item:last-child {{
            margin-bottom: 0;
        }}
        .info-label {{
            min-width: 100px;
            font-weight: 600;
            color: var(--summary-text);
            margin-right: 12px;
        }}
        .info-value {{
            flex: 1;
            color: var(--text-color);
            word-break: break-all;
        }}
        .header-controls {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .theme-toggle {{
            padding: 8px 12px;
            background-color: var(--btn-primary);
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            transition: background-color 0.2s;
        }}
        .theme-toggle:hover {{
            background-color: var(--btn-primary-hover);
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
            cursor: default;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
            user-select: none;
        }}
        .line-container.expandable {{
            cursor: pointer;
        }}
        .line-container.expandable:hover {{
            background-color: var(--line-hover);
        }}
        .line-container.expandable.selected {{
            background-color: var(--line-selected);
        }}
        .line-container.expandable.highlighted {{
            background-color: var(--line-highlighted);
        }}
        .line-container.expandable.keyboard-selected {{
            background-color: var(--line-keyboard-select);
            outline: 2px solid var(--btn-primary);
        }}
        .line-number {{
            display: inline-block;
            width: 40px;
            text-align: right;
            padding-right: 10px;
            color: var(--summary-text);
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
            user-select: none;
            flex-shrink: 0;
            cursor: pointer;
            border-radius: 3px;
            padding-left: 5px;
            transition: background-color 0.2s, color 0.2s;
        }}
        .line-number:hover {{
            background-color: var(--line-hover);
        }}
        .line-content {{
            flex-grow: 1;
            padding: 2px 0;
            -webkit-user-select: text;
            -moz-user-select: text;
            -ms-user-select: text;
            user-select: text;
            white-space: pre;
            font-family: 'Courier New', monospace;
        }}
        .expand-btn {{
            display: inline-block;
            width: 16px;
            height: 16px;
            background-color: var(--btn-primary);
            color: white;
            border-radius: 50%;
            text-align: center;
            line-height: 16px;
            font-size: 12px;
            margin-left: 5px;
            cursor: pointer;
            opacity: 0.5;
            transition: opacity 0.2s, background-color 0.2s;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
            user-select: none;
            vertical-align: middle;
            flex-shrink: 0;
        }}
        .expand-btn:hover {{
            opacity: 1;
            background-color: var(--btn-primary-hover);
        }}
        .expanded-content {{
            display: none;
            margin-left: 50px;
            padding: 10px;
            background-color: var(--expanded-bg);
            border-left: 2px solid var(--btn-primary);
            border-radius: 0 4px 4px 0;
            white-space: pre;
            font-family: 'Courier New', monospace;
            font-size: 13px;
        }}
        .location-link {{
            display: block;
            padding: 3px 5px;
            color: var(--link-color);
            text-decoration: none;
            margin: 2px 0;
            border-radius: 3px;
            white-space: pre;
        }}
        .location-link:hover {{
            background-color: var(--line-hover);
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
            background-color: var(--line-highlighted);
        }}
        .summary {{
            background-color: var(--summary-bg);
            border: 1px solid var(--border-color);
            border-radius: 5px;
            padding: 8px 12px;
            margin-bottom: 0;
            font-size: 12px;
            color: var(--summary-text);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 15px;
        }}
        .summary > div:first-child {{
            flex: 1;
        }}
        .summary span {{
            margin: 0 5px;
        }}
        .info-toggle-btn {{
            display: flex;
            align-items: center;
            cursor: pointer;
            user-select: none;
            padding: 4px 8px;
            border-radius: 4px;
            transition: background-color 0.2s;
            font-size: 12px;
            color: var(--text-color);
            white-space: nowrap;
        }}
        .info-toggle-btn:hover {{
            background-color: rgba(0, 0, 0, 0.05);
        }}
        [data-theme="dark"] .info-toggle-btn:hover {{
            background-color: rgba(255, 255, 255, 0.05);
        }}
        .info-toggle-icon {{
            display: inline-block;
            margin-right: 6px;
            transition: transform 0.3s;
            font-size: 12px;
        }}
        .info-toggle-icon.expanded {{
            transform: rotate(90deg);
        }}
        .info-panel {{
            display: none;
            margin: 0;
            padding: 12px;
            background-color: rgba(0, 0, 0, 0.02);
            border-radius: 0 0 5px 5px;
            border: 1px solid var(--border-color);
            border-top: none;
            border-left: 3px solid var(--btn-primary);
            margin-bottom: 15px;
            font-size: 11px;
        }}
        .info-panel.show {{
            display: block;
        }}
        [data-theme="dark"] .info-panel {{
            background-color: rgba(255, 255, 255, 0.03);
        }}
        .info-item {{
            display: flex;
            margin-bottom: 6px;
            font-size: 11px;
            line-height: 1.5;
        }}
        .info-item:last-child {{
            margin-bottom: 0;
        }}
        .info-label {{
            min-width: 100px;
            font-weight: 600;
            color: var(--summary-text);
            margin-right: 12px;
        }}
        .info-value {{
            flex: 1;
            color: var(--text-color);
            word-break: break-all;
        }}
        .controls {{
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 1000;
            display: flex;
            gap: 5px;
        }}
        .control-btn {{
            padding: 8px 12px;
            background-color: var(--btn-primary);
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            transition: background-color 0.2s;
        }}
        .control-btn:hover {{
            background-color: var(--btn-primary-hover);
        }}
        .control-btn.collapse {{
            background-color: var(--btn-danger);
        }}
        .control-btn.collapse:hover {{
            background-color: var(--btn-danger-hover);
        }}
        .copy-btn {{
            position: fixed;
            bottom: 60px;
            right: 20px;
            padding: 8px 12px;
            background-color: #2196F3;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            transition: background-color 0.2s;
            z-index: 1000;
        }}
        .copy-btn:hover {{
            background-color: #1976D2;
        }}
        .jump-to-top {{
            position: fixed;
            bottom: 100px;
            right: 20px;
            padding: 8px 12px;
            background-color: #9C27B0;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            transition: background-color 0.2s;
            z-index: 1000;
        }}
        .jump-to-top:hover {{
            background-color: #7B1FA2;
        }}
        .progress-bar {{
            position: fixed;
            top: 0;
            left: 0;
            width: 0%;
            height: 3px;
            background-color: var(--btn-primary);
            z-index: 2000;
            transition: width 0.3s ease;
        }}
        .keyboard-hint {{
            position: fixed;
            bottom: 20px;
            left: 20px;
            background-color: var(--container-bg);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            padding: 8px 12px;
            font-size: 12px;
            color: var(--text-color);
            z-index: 1000;
            max-width: 300px;
            opacity: 0.9;
            display: none;
        }}
        .keyboard-hint h3 {{
            margin: 0 0 5px 0;
            font-size: 12px;
        }}
        .keyboard-hint ul {{
            margin: 0;
            padding-left: 15px;
        }}
        .keyboard-hint li {{
            margin: 2px 0;
        }}
        .keyboard-hint kbd {{
            background-color: var(--bg-color);
            border: 1px solid var(--border-color);
            border-radius: 3px;
            padding: 1px 4px;
            font-family: monospace;
            font-size: 11px;
        }}
        .keyboard-hint-close {{
            position: absolute;
            top: 5px;
            right: 5px;
            background: none;
            border: none;
            color: var(--text-color);
            cursor: pointer;
            font-size: 14px;
            padding: 0;
            width: 20px;
            height: 20px;
            line-height: 20px;
            text-align: center;
        }}
        .keyboard-hint-close:hover {{
            color: var(--btn-danger);
        }}
        /* 锚点目标行高亮样式 */
        .line-container:target {{
            background-color: var(--line-highlighted);
            animation: pulse-highlight 1s ease-out;
        }}
        @keyframes pulse-highlight {{
            0% {{
                background-color: var(--line-highlighted);
                box-shadow: 0 0 10px rgba(255, 193, 7, 0.5);
            }}
            100% {{
                background-color: transparent;
                box-shadow: none;
            }}
        }}
        /* Ftrace语法高亮样式 */
        .hl-cpu {{
            color: #d73a49;
            font-weight: bold;
        }}
        [data-theme="dark"] .hl-cpu {{
            color: #f97583;
            font-weight: bold;
        }}
        
        .hl-time {{
            color: #005cc5;
            font-weight: normal;
        }}
        [data-theme="dark"] .hl-time {{
            color: #79b8ff;
            font-weight: normal;
        }}
        
        .hl-unit {{
            color: #6f42c1;
            font-weight: bold;
        }}
        [data-theme="dark"] .hl-unit {{
            color: #b392f0;
            font-weight: bold;
        }}
        
        .hl-func {{
            color: #6f42c1;
            font-weight: bold;
        }}
        [data-theme="dark"] .hl-func {{
            color: #b392f0;
            font-weight: bold;
        }}
        
        .hl-addr {{
            color: #032f62;
            font-family: 'Courier New', monospace;
        }}
        [data-theme="dark"] .hl-addr {{
            color: #79b8ff;
            font-family: 'Courier New', monospace;
        }}
        
        .hl-comment {{
            color: #6a737d;
            font-style: italic;
        }}
        [data-theme="dark"] .hl-comment {{
            color: #8b949e;
            font-style: italic;
        }}
        
        .hl-symbol {{
            color: #24292e;
            font-weight: bold;
        }}
        [data-theme="dark"] .hl-symbol {{
            color: #c9d1d9;
            font-weight: bold;
        }}
        
        /* C代码简洁语法高亮 */
        .c-keyword {{
            color: #d73a49;  /* 红色用于关键字 */
            font-weight: bold;
        }}
        [data-theme="dark"] .c-keyword {{
            color: #f97583;
            font-weight: bold;
        }}
        
        .c-string {{
            color: #032f62;  /* 蓝色用于字符串 */
        }}
        [data-theme="dark"] .c-string {{
            color: #79b8ff;
        }}
        
        .c-comment {{
            color: #6a737d;  /* 灰色用于注释 */
            font-style: italic;
        }}
        [data-theme="dark"] .c-comment {{
            color: #8b949e;
            font-style: italic;
        }}
        
        .c-number {{
            color: #6f42c1;  /* 紫色用于数字 */
        }}
        [data-theme="dark"] .c-number {{
            color: #b392f0;
        }}
        
        /* Linux内核特定类型 */
        .kernel-type {{
            color: #0184bc;  /* 青色用于内核类型 */
            font-weight: bold;
        }}
        [data-theme="dark"] .kernel-type {{
            color: #58a6ff;
            font-weight: bold;
        }}
        
        /* Linux内核属性修饰符 */
        .kernel-attr {{
            color: #9e1a1a;  /* 深红色用于属性 */
        }}
        [data-theme="dark"] .kernel-attr {{
            color: #ff7b72;
        }}
        
        /* Linux内核常用宏常量 */
        .kernel-macro {{
            color: #e36209;  /* 橙色用于宏常量 */
            font-weight: bold;
        }}
        [data-theme="dark"] .kernel-macro {{
            color: #fb8500;
            font-weight: bold;
        }}
        
        .line-number {{
            color: #6a737d;
            margin-right: 8px;
        }}
        [data-theme="dark"] .line-number {{
            color: #8b949e;
        }}
        
        @media (max-width: 768px) {{
            body {{
                padding: 10px;
            }}
            .container {{
                padding: 10px;
            }}
            .line-container {{
                font-size: 12px;
            }}
            .expanded-content {{
                margin-left: 30px;
                padding: 5px;
                font-size: 11px;
            }}
            .controls {{
                bottom: 10px;
                right: 10px;
            }}
            .copy-btn {{
                bottom: 50px;
                right: 10px;
            }}
            .jump-to-top {{
                bottom: 90px;
                right: 10px;
            }}
            .keyboard-hint {{
                bottom: 10px;
                left: 10px;
                font-size: 10px;
                max-width: 200px;
            }}
        }}
    </style>
</head>
<body>
    <div class="progress-bar" id="progressBar"></div>
    
    <div class="container">
        <h1>
            <span class="title-main">{APP_TITLE}</span>
            <span class="title-meta">
                <span class="version-badge">v{APP_VERSION}</span>
                <span class="author-badge">{APP_AUTHOR}</span>
            </span>
        </h1>
        
        <div class="header-controls">
            <div></div> <!-- 占位元素 -->
            <button class="theme-toggle" onclick="toggleTheme()">Theme</button>
        </div>
        
        <div class="summary">
            <div>
                <span>Lines: {total_lines}</span>
                <span>•</span>
                <span>Expandable: {expandable_entries}</span>
                <span>•</span>
                <span>Kernel Functions: {len(vmlinux_funcs)}</span>
                <span>•</span>
                <span>Modules: {len(module_funcs)}</span>
            </div>
            <div class="info-toggle-btn" onclick="toggleInfoPanel()">
                <span class="info-toggle-icon">▶</span>
                <span>⚙️ 详情</span>
            </div>
        </div>
        
        <div class="info-panel" id="infoPanel">
            {{INFO_CONTENT_PLACEHOLDER}}
        </div>
        
        <div id="content">
"""

    # 添加行号并输出原始ftrace日志
    for idx, line_data in enumerate(parsed_lines):
        line_number = idx + 1  # 行号从1开始
        line_anchor_id = f"L{line_number}"  # 使用L{行号}格式作为锚点
        line_id = f"line_{idx}"  # 保留用于JavaScript引用
        
        # 先应用语法高亮，保持原始空格和对齐
        escaped_line = highlight_ftrace_line(line_data["raw_line"])
        
        # 检查是否可展开
        is_expandable = line_data['expandable'] and line_data['func_info']
        expandable_class = "expandable" if is_expandable else ""
        
        html_str += f'<div class="line-container {expandable_class}" id="{line_anchor_id}" data-line-number="{line_number}" data-line-id="{line_id}"'
        if is_expandable:
            html_str += f' onclick="handleLineClick(event, \'{line_id}\')" ondblclick="handleDoubleClick(event, \'{line_id}\')"'
        html_str += '>'
        html_str += f'<span class="line-number" onclick="updateAnchor(\'{line_anchor_id}\', event)" title="Click to copy anchor link">{line_number}</span>'
        html_str += f'<span class="line-content">{escaped_line}</span>'
        
        if is_expandable:
            html_str += f'<span class="expand-btn">+</span>'
        
        html_str += '</div>'
        
        if is_expandable:
            # 获取调整后的函数信息
            func_info = line_data['func_info']
            adjusted_func_info = adjust_func_info(func_info)
            
            # 从预取的数据中获取位置信息
            locations = func_locations_map.get(adjusted_func_info, {})
            
            # 为expanded-content添加内联样式style="display: none;"，确保初始状态
            html_str += f'<div class="expanded-content" id="{line_id}_content" style="display: none;">' 
            
            if locations:
                # 确定使用哪个URL
                is_module = line_data.get('module_name') is not None
                module_name = line_data.get('module_name')

                if is_module:
                    # 模块函数：优先使用module_url_map，然后是default_module_url，最后是base_url
                    if module_name and module_name in module_url_map:
                        current_base_url = module_url_map[module_name]
                    else:
                        current_base_url = default_module_url if default_module_url else base_url
                else:
                    # 内核函数：使用base_url
                    current_base_url = base_url

                # 检查是结构化数据还是原始输出
                if isinstance(locations, dict) and 'func' in str(next(iter(locations.values()), {})):
                    # 结构化数据（标准模式）
                    loc_list = locations.get(adjusted_func_info, [])
                    for loc in loc_list:
                        if current_base_url:
                            file_path = loc['file']
                            # 清理文件路径
                            file_path = clean_file_path(file_path, kernel_src, path_prefix)

                            # 提取文件路径和行号
                            clean_path = extract_file_path(file_path, kernel_src, path_prefix)
                            line_num = loc.get('line')

                            # 如果行号不在loc中，尝试从file_path中提取
                            if line_num is None:
                                line_num = extract_line_number(file_path, kernel_src, path_prefix)

                            if clean_path and line_num is not None:
                                # 获取相对于内核源码的路径
                                relative_path = get_relative_path(clean_path, kernel_src)

                                # 构建URL
                                url = build_source_url(current_base_url, relative_path, line_num)

                                link_text = f"{loc['func']} at {relative_path}:{line_num}"
                                if loc.get('inlined'):
                                    link_text = f"(inlined) {link_text}"

                                escaped_link = escape_html_preserve_spaces(link_text)
                                escaped_url = html.escape(url)
                                html_str += f'<a class="location-link" href="{escaped_url}" target="_blank">{escaped_link}</a>'
                            else:
                                # 无法提取有效路径或行号
                                link_text = f"{loc['func']} at {file_path}"
                                if loc.get('inlined'):
                                    link_text = f"(inlined) {link_text}"
                                escaped_link = escape_html_preserve_spaces(link_text)
                                html_str += f'<div class="location-link">{escaped_link}</div>'
                        else:
                            # 没有base_url，只显示文本
                            file_path = loc['file']
                            # 清理文件路径
                            file_path = clean_file_path(file_path, kernel_src, path_prefix)
                            print("file_path: " + file_path)

                            # 提取文件路径
                            clean_path = extract_file_path(file_path, kernel_src, path_prefix)
                            print("clean_path: " + clean_path)
                            line_num = loc.get('line')

                            # 如果行号不在loc中，尝试从file_path中提取
                            if line_num is None:
                                line_num = extract_line_number(file_path, kernel_src, path_prefix)

                            if clean_path and line_num is not None:
                                link_text = f"{loc['func']} at {clean_path}:{line_num}"
                            else:
                                link_text = f"{loc['func']} at {file_path}"

                            if loc.get('inlined'):
                                link_text = f"(inlined) {link_text}"
                            escaped_link = escape_html_preserve_spaces(link_text)
                            html_str += f'<div class="location-link">{escaped_link}</div>'
                elif isinstance(locations, str):
                    # 原始输出（--list模式）
                    html_str += parse_list_output(locations, current_base_url, kernel_src, highlight_code, path_prefix, module_src)
                else:
                    # 未知格式
                    html_str += f'<div class="location-link">Source information unavailable for: {escape_html_preserve_spaces(adjusted_func_info)}</div>'
            else:
                html_str += f'<div class="location-link">Source information unavailable for: {escape_html_preserve_spaces(adjusted_func_info)}</div>'
            
            html_str += '</div>'
    
    html_str += """
        </div>
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
    </div>
    
    <div class="controls">
        <button class="jump-to-top" onclick="scrollToTop()">Top</button>
        <button class="copy-btn" onclick="copyVisibleContent()">Copy</button>
        <button class="control-btn" onclick="expandAll()">Expand All</button>
        <button class="control-btn collapse" onclick="collapseAll()">Collapse All</button>
    </div>
    
    <script>
        // 标记是否正在进行文本选择
        let isSelectingText = false;
        // 记录鼠标按下时的位置和时间
        let mouseDownInfo = { x: 0, y: 0, time: 0 };
        // 记录当前高亮行
        let highlightedLine = null;
        // 当前主题
        let currentTheme = localStorage.getItem('theme') || 'light';
        // 记录展开状态
        let expandedLines = JSON.parse(localStorage.getItem('expandedLines')) || {};
        // 双击选择相关变量
        let lastClickTime = 0;
        let lastClickTarget = null;
        let doubleClickTimer = null;
        let clickTimer = null;
        let doubleClickState = 0; // 0: 初始状态, 1: 第一次双击选中单词, 2: 第二次双击选中整行
        // 键盘导航相关变量
        let keyboardSelectedLine = null;
        let keyboardSelectedIndex = -1;
        let allLines = [];
        let expandableLines = [];
        let expandableLineIndices = [];
        let keyboardHintTimer = null;
        // 记录当前滚动位置
        let scrollPositionBeforeClick = { x: 0, y: 0 };
        // 是否显示过键盘提示
        let keyboardHintShown = localStorage.getItem('keyboardHintShown') === 'true';
        
        // 初始化主题
        document.documentElement.setAttribute('data-theme', currentTheme);
        
        // 初始化展开状态
        function restoreExpandedState() {
            for (const [lineId, isExpanded] of Object.entries(expandedLines)) {
                if (isExpanded) {
                    const content = document.getElementById(lineId + '_content');
                    const btn = document.querySelector('#' + lineId + '_container .expand-btn');
                    const container = document.getElementById(lineId + '_container');
                    
                    if (content && btn && container) {
                        content.style.display = 'block';
                        if (btn) btn.textContent = '-';
                        container.classList.add('selected');
                    }
                }
            }
        }
        
        // 保存展开状态
        function saveExpandedState(lineId, isExpanded) {
            expandedLines[lineId] = isExpanded;
            localStorage.setItem('expandedLines', JSON.stringify(expandedLines));
        }
        
        // 初始化键盘导航
        function initKeyboardNavigation() {
            allLines = Array.from(document.querySelectorAll('.line-container'));
            expandableLines = Array.from(document.querySelectorAll('.line-container.expandable'));
            
            // 收集所有可展开行的索引
            expandableLineIndices = [];
            allLines.forEach((line, index) => {
                if (line.classList.contains('expandable')) {
                    expandableLineIndices.push(index);
                }
            });
            
            // 如果有可展开行，设置默认选中第一个可展开行
            if (expandableLineIndices.length > 0) {
                setKeyboardSelectedLine(expandableLineIndices[0], false);
            } else if (allLines.length > 0) {
                // 如果没有可展开行，选中第一行
                setKeyboardSelectedLine(0, false);
            }
            
            // 初始显示键盘提示，但只在第一次加载页面时
            if (!keyboardHintShown) {
                showKeyboardHint();
                localStorage.setItem('keyboardHintShown', 'true');
                keyboardHintShown = true;
            }
        }
        
        // 显示键盘提示
        function showKeyboardHint() {
            const hint = document.getElementById('keyboardHint');
            hint.style.display = 'block';
            
            // 8秒后自动隐藏
            if (keyboardHintTimer) {
                clearTimeout(keyboardHintTimer);
            }
            keyboardHintTimer = setTimeout(() => {
                hint.style.display = 'none';
            }, 8000);
        }
        
        // 关闭键盘提示
        function closeKeyboardHint() {
            const hint = document.getElementById('keyboardHint');
            hint.style.display = 'none';
            if (keyboardHintTimer) {
                clearTimeout(keyboardHintTimer);
                keyboardHintTimer = null;
            }
        }
        
        // 设置键盘选中的行
        function setKeyboardSelectedLine(index, scrollIntoView = true) {
            // 清除之前的选择
            if (keyboardSelectedLine) {
                keyboardSelectedLine.classList.remove('keyboard-selected');
            }
            
            if (index < 0) index = 0;
            if (index >= allLines.length) index = allLines.length - 1;
            
            keyboardSelectedIndex = index;
            keyboardSelectedLine = allLines[index];
            
            // 添加新的选择样式
            if (keyboardSelectedLine) {
                keyboardSelectedLine.classList.add('keyboard-selected');
                
                // 只有在需要滚动时才滚动
                if (scrollIntoView) {
                    // 获取当前行的位置
                    const rect = keyboardSelectedLine.getBoundingClientRect();
                    const windowHeight = window.innerHeight;
                    
                    // 计算行在视口中的位置
                    const lineTop = rect.top;
                    const lineBottom = rect.bottom;
                    const lineHeight = rect.height;
                    
                    // 判断是否需要滚动到中间
                    // 如果当前行接近视口底部（距离底部小于2行高度），则滚动到中间
                    // 如果当前行接近视口顶部（距离顶部小于1行高度），也滚动到中间
                    const nearBottom = windowHeight - lineBottom < lineHeight * 2;
                    const nearTop = lineTop < lineHeight;
                    
                    if (nearBottom || nearTop || lineTop < 0 || lineBottom > windowHeight) {
                        // 如果接近边界或不在视口中，滚动到中间
                        keyboardSelectedLine.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    } else {
                        // 否则，使用nearest，尽可能少滚动
                        keyboardSelectedLine.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    }
                }
            }
        }
        
        // 导航到下一个可展开行
        function navigateToExpandableLine(direction) {
            if (expandableLineIndices.length === 0) {
                return; // 没有可展开行
            }
            
            let currentExpandableIndex = -1;
            
            // 找到当前行在可展开行列表中的位置
            for (let i = 0; i < expandableLineIndices.length; i++) {
                if (expandableLineIndices[i] === keyboardSelectedIndex) {
                    currentExpandableIndex = i;
                    break;
                }
            }
            
            // 如果当前行不是可展开行，找到最近的可展开行
            if (currentExpandableIndex === -1) {
                if (direction > 0) {
                    // 向下查找
                    for (let i = 0; i < expandableLineIndices.length; i++) {
                        if (expandableLineIndices[i] > keyboardSelectedIndex) {
                            currentExpandableIndex = i;
                            break;
                        }
                    }
                    // 如果没找到，从第一个开始
                    if (currentExpandableIndex === -1) {
                        currentExpandableIndex = 0;
                    }
                } else {
                    // 向上查找
                    for (let i = expandableLineIndices.length - 1; i >= 0; i--) {
                        if (expandableLineIndices[i] < keyboardSelectedIndex) {
                            currentExpandableIndex = i;
                            break;
                        }
                    }
                    // 如果没找到，从最后一个开始
                    if (currentExpandableIndex === -1) {
                        currentExpandableIndex = expandableLineIndices.length - 1;
                    }
                }
            }
            
            // 计算下一个索引
            let nextIndex = currentExpandableIndex + direction;
            
            // 处理循环
            if (nextIndex < 0) {
                nextIndex = expandableLineIndices.length - 1; // 循环到最后一个
            } else if (nextIndex >= expandableLineIndices.length) {
                nextIndex = 0; // 循环到第一个
            }
            
            // 设置新的选中行
            setKeyboardSelectedLine(expandableLineIndices[nextIndex], true);
        }
        
        // 处理行点击事件 - 现在只处理单击
        function handleLineClick(event, lineId) {
            // 记录点击前的滚动位置
            scrollPositionBeforeClick = {
                x: window.scrollX,
                y: window.scrollY
            };
            
            // 阻止默认行为，防止可能的滚动
            event.preventDefault();
            
            // 检查是否正在进行文本选择
            const selection = window.getSelection();
            const hasSelection = selection.toString().trim().length > 0;
            
            // 检查鼠标移动距离，判断是否是拖动选择
            const mouseUpInfo = { x: event.clientX, y: event.clientY, time: Date.now() };
            const distance = Math.sqrt(
                Math.pow(mouseUpInfo.x - mouseDownInfo.x, 2) + 
                Math.pow(mouseUpInfo.y - mouseDownInfo.y, 2)
            );
            const timeDiff = mouseUpInfo.time - mouseDownInfo.time;
            
            // 如果存在文本选择，并且是拖动操作，则不触发展开/收起
            if (hasSelection && (distance > 5 || timeDiff > 200)) {
                // 高亮被选中的行
                highlightSelectedLines();
                return;
            }
            
            // 清除之前的点击定时器
            if (clickTimer) {
                clearTimeout(clickTimer);
                clickTimer = null;
            }
            
            // 如果是双击事件的一部分，则不执行单击
            if (doubleClickTimer) {
                clearTimeout(doubleClickTimer);
                doubleClickTimer = null;
                return;
            }
            
            // 设置键盘选中行，但不滚动
            // 根据 data-line-id 查找对应的行元素
            let clickedLine = null;
            const allLineElements = Array.from(document.querySelectorAll('.line-container'));
            for (let line of allLineElements) {
                if (line.getAttribute('data-line-id') === lineId) {
                    clickedLine = line;
                    break;
                }
            }
            
            if (clickedLine) {
                const lineIndex = allLineElements.findIndex(line => line === clickedLine);
                if (lineIndex >= 0) {
                    setKeyboardSelectedLine(lineIndex, false);
                }
            }
            
            // 设置延迟执行单击事件，以便在双击时可以取消
            clickTimer = setTimeout(() => {
                toggleExpand(lineId, event);
                clickTimer = null;
            }, 200);
        }
        
        // 处理双击事件
        function handleDoubleClick(event, lineId) {
            // 阻止事件冒泡
            event.stopPropagation();
            
            // 清除单击定时器
            if (clickTimer) {
                clearTimeout(clickTimer);
                clickTimer = null;
            }
            
            // 检查是否点击的是.line-content
            const lineContent = event.target.closest('.line-content');
            if (!lineContent) return;
            
            // 检查是否在可展开行内
            const lineContainer = event.target.closest('.line-container');
            if (!lineContainer) return;
            
            const now = Date.now();
            const timeDiff = now - lastClickTime;
            const target = event.target;
            
            if (timeDiff < 500 && target === lastClickTarget) {
                // 快速双击相同目标
                doubleClickState = (doubleClickState + 1) % 3;
                
                if (doubleClickState === 1) {
                    // 第一次双击：选中单词（浏览器默认行为）
                    // 我们让浏览器处理默认的双击选中单词行为
                    
                    // 设置定时器，在一定时间后重置状态
                    if (doubleClickTimer) {
                        clearTimeout(doubleClickTimer);
                    }
                    doubleClickTimer = setTimeout(() => {
                        doubleClickState = 0;
                        doubleClickTimer = null;
                    }, 500);
                    
                    return;
                } else if (doubleClickState === 2) {
                    // 第二次双击：选中整行文本
                    event.preventDefault();
                    
                    // 获取行内容
                    const lineText = lineContent.textContent;
                    
                    // 创建选择范围
                    const range = document.createRange();
                    range.selectNodeContents(lineContent);
                    
                    // 清除当前选择
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(range);
                    
                    // 高亮被选中的行
                    highlightSelectedLines();
                    
                    // 重置双击状态
                    doubleClickState = 0;
                    if (doubleClickTimer) {
                        clearTimeout(doubleClickTimer);
                        doubleClickTimer = null;
                    }
                }
            } else {
                // 新的双击序列
                doubleClickState = 1;
                
                // 第一次双击：让浏览器默认选中单词
                // 我们只需要记录时间和目标
                
                // 设置定时器，在一定时间后重置状态
                if (doubleClickTimer) {
                    clearTimeout(doubleClickTimer);
                }
                doubleClickTimer = setTimeout(() => {
                    doubleClickState = 0;
                    doubleClickTimer = null;
                }, 500);
            }
            
            lastClickTime = now;
            lastClickTarget = target;
        }
        
        // 切换展开/收起状态 - 只影响当前行，不滚动
        function toggleExpand(lineId, event) {
            // 保存当前滚动位置
            const scrollX = window.scrollX;
            const scrollY = window.scrollY;
            
            const content = document.getElementById(lineId + '_content');
            const btn = document.querySelector('[data-line-id="' + lineId + '"] .expand-btn');
            const container = document.querySelector('[data-line-id="' + lineId + '"]');
            
            // 使用getComputedStyle获取计算后的显示状态
            const computedStyle = window.getComputedStyle(content);
            const isCurrentlyVisible = computedStyle.display === 'block';
            
            if (isCurrentlyVisible) {
                // 收起当前行
                content.style.display = 'none';
                if (btn) btn.textContent = '+';
                container.classList.remove('selected');
                saveExpandedState(lineId, false);
            } else {
                // 展开当前行
                content.style.display = 'block';
                if (btn) btn.textContent = '-';
                container.classList.add('selected');
                saveExpandedState(lineId, true);
            }
            
            // 恢复滚动位置
            setTimeout(() => {
                window.scrollTo(scrollX, scrollY);
            }, 0);
        }
        
        // 切换主题
        function toggleTheme() {
            currentTheme = currentTheme === 'light' ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', currentTheme);
            localStorage.setItem('theme', currentTheme);
        }
        
        // 切换信息面板展开/折叠
        function toggleInfoPanel() {
            const panel = document.getElementById('infoPanel');
            const toggleBtn = document.querySelector('.info-toggle-btn');
            const icon = toggleBtn.querySelector('.info-toggle-icon');
            const isExpanded = panel.classList.contains('show');
            
            if (isExpanded) {
                panel.classList.remove('show');
                icon.classList.remove('expanded');
                localStorage.setItem('infoPanelExpanded', 'false');
            } else {
                panel.classList.add('show');
                icon.classList.add('expanded');
                localStorage.setItem('infoPanelExpanded', 'true');
            }
        }
        
        // 初始化信息面板状态
        function initInfoPanel() {
            const expanded = localStorage.getItem('infoPanelExpanded') === 'true';
            if (expanded) {
                const panel = document.getElementById('infoPanel');
                const toggleBtn = document.querySelector('.info-toggle-btn');
                const icon = toggleBtn.querySelector('.info-toggle-icon');
                panel.classList.add('show');
                icon.classList.add('expanded');
            }
        }
        
        // 高亮被选中的行
        function highlightSelectedLines() {
            // 清除之前的高亮
            if (highlightedLine) {
                highlightedLine.classList.remove('highlighted');
            }
            
            // 获取当前选择
            const selection = window.getSelection();
            if (selection.rangeCount > 0 && selection.toString().trim().length > 0) {
                const range = selection.getRangeAt(0);
                const container = range.commonAncestorContainer;
                
                // 向上查找包含的行容器
                let current = container;
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                    if (current.classList && current.classList.contains('line-container')) {
                        current.classList.add('highlighted');
                        highlightedLine = current;
                        break;
                    }
                    current = current.parentElement;
                }
            }
        }
        
        // 复制可见内容
        function copyVisibleContent() {
            const visibleLines = [];
            const lines = document.querySelectorAll('.line-container');
            
            lines.forEach(line => {
                const lineNumber = line.querySelector('.line-number').textContent;
                const lineContent = line.querySelector('.line-content').textContent;
                visibleLines.push(`${lineNumber} ${lineContent}`);
            });
            
            const textToCopy = visibleLines.join('\\n');
            
            navigator.clipboard.writeText(textToCopy).then(() => {
                // 显示简单的复制成功提示
                const originalText = event.target.textContent;
                event.target.textContent = 'Copied!';
                setTimeout(() => {
                    event.target.textContent = originalText;
                }, 1000);
            }).catch(err => {
                console.error('Failed to copy: ', err);
                // 降级方案
                const textArea = document.createElement('textarea');
                textArea.value = textToCopy;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);
                const originalText = event.target.textContent;
                event.target.textContent = 'Copied!';
                setTimeout(() => {
                    event.target.textContent = originalText;
                }, 1000);
            });
        }
        
        // 滚动到顶部
        function scrollToTop() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
        
        // 监听滚动，更新进度条
        function updateProgressBar() {
            const winScroll = document.body.scrollTop || document.documentElement.scrollTop;
            const height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
            const scrolled = (winScroll / height) * 100;
            document.getElementById("progressBar").style.width = scrolled + "%";
        }
        
        // 记录鼠标按下事件
        document.addEventListener('mousedown', function(event) {
            mouseDownInfo = {
                x: event.clientX,
                y: event.clientY,
                time: Date.now()
            };
        });
        
        // 监听文本选择变化
        document.addEventListener('selectionchange', function() {
            const selection = window.getSelection();
            isSelectingText = selection.toString().trim().length > 0;
            
            if (isSelectingText) {
                highlightSelectedLines();
            } else {
                // 清除高亮
                if (highlightedLine) {
                    highlightedLine.classList.remove('highlighted');
                    highlightedLine = null;
                }
            }
        });
        
        // 添加键盘快捷键支持
        document.addEventListener('keydown', function(event) {
            console.log('🔑 Keydown event:', 'key=', event.key, 'keyCode=', event.keyCode, 'code=', event.code);
            
            // 检查是否在输入框中
            if (event.target.matches('input, textarea, select')) {
                console.log('⏸ 在输入框中，跳过处理');
                return;
            }
            
            // DEBUG: 记录所有按键（可以注释掉）
            // console.log('Keydown:', event.key, 'keyCode:', event.keyCode);

            
            // Enter键特殊处理：在链接上按Enter时，用来展开/收起，而不是打开链接
            if (event.key === 'Enter' || event.keyCode === 13) {
                let handled = false;
                
                // 检查当前焦点是否在可展开行内（包括链接）
                let focusedLine = null;
                let element = event.target;
                
                // 向上查找包含 expandable 类的行容器
                while (element && element !== document.body) {
                    if (element.classList && element.classList.contains('line-container') && element.classList.contains('expandable')) {
                        focusedLine = element;
                        break;
                    }
                    element = element.parentElement;
                }
                
                // 如果找到可展开行，触发展开/收起
                if (focusedLine) {
                    event.preventDefault();
                    const lineId = focusedLine.getAttribute('data-line-id');
                    if (lineId) {
                        toggleExpand(lineId, event);
                        handled = true;
                    }
                }
                
                // 如果当前没有焦点在链接上，使用键盘选中的行
                if (!handled && keyboardSelectedLine && keyboardSelectedLine.classList.contains('expandable')) {
                    event.preventDefault();
                    const lineId = keyboardSelectedLine.getAttribute('data-line-id');
                    if (lineId) {
                        toggleExpand(lineId, event);
                        handled = true;
                    }
                }
                
                // 如果没有可展开行被处理，但 keyboardSelectedLine 不可展开，尝试展开当前行
                if (!handled && keyboardSelectedLine) {
                    event.preventDefault();
                    const lineId = keyboardSelectedLine.getAttribute('data-line-id');
                    if (lineId) {
                        toggleExpand(lineId, event);
                    }
                }
                
                if (handled) {
                    return;
                }
            }
            
            // 按下ESC键取消选择
            if (event.key === 'Escape' || event.keyCode === 27) {
                // 清除高亮
                if (highlightedLine) {
                    highlightedLine.classList.remove('highlighted');
                    highlightedLine = null;
                }
                // 清除文本选择
                window.getSelection().removeAllRanges();
            }
            
            // VIM风格导航键和方向键
            let handled = false;
            const keyLower = event.key.toLowerCase();
            
            // 处理垂直移动（j/k 和上下箭头键）- 只导航到可展开行
            if (keyLower === 'j' || event.key === 'ArrowDown') {
                event.preventDefault();
                handled = true;
                if (expandableLineIndices.length > 0) {
                    navigateToExpandableLine(1); // 向下导航
                }
            } else if (keyLower === 'k' || event.key === 'ArrowUp') {
                event.preventDefault();
                handled = true;
                if (expandableLineIndices.length > 0) {
                    navigateToExpandableLine(-1); // 向上导航
                }
            }
            
            // 处理水平滚动（h/l 键）
            // 支持大小写，Shift+h 和 Shift+l 也有效
            if (keyLower === 'h') {
                console.log('👈 检测到 h 键，执行左滚动');
                event.preventDefault();
                handled = true;
                // 向左滚动容器
                var container = document.querySelector('.container');
                if (container) {
                    container.scrollBy({ left: -100, behavior: 'smooth' });
                    console.log('✓ 容器已左滚，当前 scrollLeft:', container.scrollLeft);
                }
            } else if (keyLower === 'l') {
                console.log('👉 检测到 l 键，执行右滚动');
                event.preventDefault();
                handled = true;
                // 向右滚动容器
                var container = document.querySelector('.container');
                if (container) {
                    container.scrollBy({ left: 100, behavior: 'smooth' });
                    console.log('✓ 容器已右滚，当前 scrollLeft:', container.scrollLeft);
                }
            }
            
            // 处理其他功能键
            if (!handled) {
                switch (event.key) {
                        
                    case ' ': // 空格键向下滚动
                        if (!event.target.matches('input, textarea')) {
                            event.preventDefault();
                            window.scrollBy({ top: window.innerHeight * 0.8, behavior: 'smooth' });
                        }
                        break;
                        
                    case 'g': // gg: 跳转到顶部
                        if (!event.shiftKey) {
                            event.preventDefault();
                            if (event.repeat) break;
                            if (expandableLineIndices.length > 0) {
                                setKeyboardSelectedLine(expandableLineIndices[0], true);
                            } else if (allLines.length > 0) {
                                setKeyboardSelectedLine(0, true);
                            }
                        }
                        break;
                        
                    case 'G': // Shift+G: 跳转到底部
                        if (event.shiftKey) {
                            event.preventDefault();
                            if (event.repeat) break;
                            if (expandableLineIndices.length > 0) {
                                setKeyboardSelectedLine(expandableLineIndices[expandableLineIndices.length - 1], true);
                            } else if (allLines.length > 0) {
                                setKeyboardSelectedLine(allLines.length - 1, true);
                            }
                        }
                        break;
                        
                    case 'Home': // Home键跳转到顶部
                        event.preventDefault();
                        if (expandableLineIndices.length > 0) {
                            setKeyboardSelectedLine(expandableLineIndices[0], true);
                        } else if (allLines.length > 0) {
                            setKeyboardSelectedLine(0, true);
                        }
                        break;
                        
                    case 'End': // End键跳转到底部
                        event.preventDefault();
                        if (expandableLineIndices.length > 0) {
                            setKeyboardSelectedLine(expandableLineIndices[expandableLineIndices.length - 1], true);
                        } else if (allLines.length > 0) {
                            setKeyboardSelectedLine(allLines.length - 1, true);
                        }
                        break;
                        
                    case 'PageDown': // PageDown向下翻页
                        event.preventDefault();
                        window.scrollBy({ top: window.innerHeight * 0.9, behavior: 'smooth' });
                        break;
                        
                    case 'PageUp': // PageUp向上翻页
                        event.preventDefault();
                        window.scrollBy({ top: -window.innerHeight * 0.9, behavior: 'smooth' });
                        break;
                        
                    case '?': // 问号键显示键盘提示
                        event.preventDefault();
                        showKeyboardHint();
                        break;
                }
            }
            
            
            // Ctrl+A 选中所有文本
            if ((event.ctrlKey || event.metaKey) && event.key === 'a') {
                // 防止默认的全选行为，因为我们需要选择特定的内容
                event.preventDefault();
                
                // 选中所有行内容
                const allLineContents = document.querySelectorAll('.line-content');
                if (allLineContents.length > 0) {
                    const range = document.createRange();
                    const selection = window.getSelection();
                    
                    // 清除当前选择
                    selection.removeAllRanges();
                    
                    // 从第一个行内容开始
                    range.setStart(allLineContents[0], 0);
                    // 到最后一个行内容结束
                    range.setEnd(allLineContents[allLineContents.length - 1], allLineContents[allLines.length - 1].childNodes.length);
                    
                    selection.addRange(range);
                    highlightSelectedLines();
                }
            }
        });
        
        // 获取当前可见的行
        function getVisibleLines() {
            const viewportTop = window.scrollY;
            const viewportBottom = viewportTop + window.innerHeight;
            const visibleLines = [];
            
            allLines.forEach(line => {
                const rect = line.getBoundingClientRect();
                const lineTop = rect.top + window.scrollY;
                const lineBottom = rect.bottom + window.scrollY;
                
                // 检查行是否在视口中
                if (lineBottom > viewportTop && lineTop < viewportBottom) {
                    visibleLines.push(line);
                }
            });
            
            return visibleLines;
        }
        
        
        // 恢复视图状态
        function restoreViewState() {
            const savedState = localStorage.getItem('viewState');
            if (savedState) {
                const state = JSON.parse(savedState);
                window.scrollTo(0, state.scrollPosition);
            }
        }
        
        // 展开所有可展开行
        function expandAll() {
            const expandableLines = document.querySelectorAll('.line-container.expandable');
            expandableLines.forEach(container => {
                const lineId = container.id.replace('_container', '');
                const content = document.getElementById(lineId + '_content');
                const btn = container.querySelector('.expand-btn');
                
                if (content) {
                    content.style.display = 'block';
                    if (btn) btn.textContent = '-';
                    container.classList.add('selected');
                    saveExpandedState(lineId, true);
                }
            });
        }
        
        // 收起所有可展开行
        function collapseAll() {
            const expandableLines = document.querySelectorAll('.line-container.expandable');
            expandableLines.forEach(container => {
                const lineId = container.id.replace('_container', '');
                const content = document.getElementById(lineId + '_content');
                const btn = container.querySelector('.expand-btn');
                
                if (content) {
                    content.style.display = 'none';
                    if (btn) btn.textContent = '+';
                    container.classList.remove('selected');
                    saveExpandedState(lineId, false);
                }
            });
            
            // 清除高亮
            if (highlightedLine) {
                highlightedLine.classList.remove('highlighted');
                highlightedLine = null;
            }
        }
        
        // 更新锚点并复制到剪贴板
        function updateAnchor(anchorId, event) {
            event.stopPropagation();
            
            // 更新URL hash
            window.location.hash = anchorId;
            
            // 获取当前完整URL
            const fullUrl = window.location.href;
            
            // 尝试复制到剪贴板（静默操作，不显示任何反馈）
            navigator.clipboard.writeText(fullUrl).catch(() => {
                // 降级方案
                const textArea = document.createElement('textarea');
                textArea.value = fullUrl;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);
            });
        }
        
        // 处理锚点导航
        function handleAnchorNavigation() {
            const hash = window.location.hash.substring(1);
            if (hash) {
                // 尝试查找锚点元素
                const element = document.getElementById(hash);
                if (element && element.classList.contains('line-container')) {
                    // 如果是行容器，清除其他高亮，然后高亮此行
                    const allLines = document.querySelectorAll('.line-container');
                    allLines.forEach(line => line.classList.remove('highlighted'));
                    element.classList.add('highlighted');
                    
                    // 平滑滚动到该行
                    setTimeout(() => {
                        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }, 100);
                    
                    // 设置键盘选中
                    const lineId = element.getAttribute('data-line-id');
                    const lineIndex = allLines.length > 0 ? Array.from(allLines).findIndex(line => line.getAttribute('data-line-id') === lineId) : -1;
                    if (lineIndex >= 0) {
                        setKeyboardSelectedLine(lineIndex, false);
                    }
                }
            }
        }
        
        // 初始加载时
        window.addEventListener('load', function() {
            // 恢复主题
            document.documentElement.setAttribute('data-theme', currentTheme);
            
            // 恢复展开状态
            restoreExpandedState();
            
            // 恢复视图状态
            restoreViewState();
            
            // 初始化信息面板
            initInfoPanel();
            
            // 监听滚动事件
            window.addEventListener('scroll', updateProgressBar);
            
            // 初始化键盘导航
            initKeyboardNavigation();
            
            // 处理锚点导航
            handleAnchorNavigation();
        });
        
        // 监听hash变化，支持动态锚点导航
        window.addEventListener('hashchange', function() {
            handleAnchorNavigation();
        });
    </script>
</body>
</html>
"""
    
    elapsed = time.time() - start_time
    verbose_print(f"HTML generation completed in {elapsed:.2f} seconds", verbose)
    
    # 替换信息面板占位符
    html_str = html_str.replace('{INFO_CONTENT_PLACEHOLDER}', info_content_html)
    
    return html_str

def main():
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
    parser.add_argument('--module-url', help='Base URL for module source code links (if different from base-url)')
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

    # 处理内核源码路径（单个）
    kernel_src_abs = os.path.abspath(args.kernel_src) if args.kernel_src else None

    # 处理多个path_prefix路径（保持原样，不转换为绝对路径）
    path_prefix = args.path_prefix if args.path_prefix else []

    # 处理多个module_srcs路径
    module_srcs_abs = []
    if args.module_srcs:
        for src in args.module_srcs:
            module_srcs_abs.append(os.path.abspath(src))

    verbose_print(f"Using faddr2line: {faddr2line_path}", args.verbose)
    verbose_print(f"Using vmlinux: {vmlinux_path}", args.verbose)
    if kernel_src_abs:
        verbose_print(f"Using kernel source: {kernel_src_abs}", args.verbose)
    if path_prefix:
        verbose_print(f"Using path prefix paths: {path_prefix}", args.verbose)
    if module_srcs_abs:
        verbose_print(f"Using module source paths: {module_srcs_abs}", args.verbose)
    
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
        script_args=args  # 传递命令行参数用于显示
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
