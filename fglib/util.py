"""Path, URL, module lookup and report-info helpers."""

import re
import os
import subprocess
import sys
import html
import fnmatch
from urllib.parse import quote


SOURCE_BROWSERS = {
    'github': ('https://github.com/torvalds/linux/blob/{rev}', 'master'),
    'elixir': ('https://elixir.bootlin.com/linux/{rev}/source', 'latest'),
}


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


def check_gawk_available():
    """检查gawk是否可用"""
    try:
        subprocess.run(['gawk', '--version'], capture_output=True, text=True, check=True)
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
        "*.ko",  # 通配符匹配
        "*.ko.debug"
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


def source_browser_url(browser, revision=None):
    """Resolve a built-in browser without changing the embedded source provider."""
    if browser not in SOURCE_BROWSERS:
        raise ValueError('--source-browser must be github or elixir')
    template, default_revision = SOURCE_BROWSERS[browser]
    revision = default_revision if revision is None else revision
    if not revision.strip():
        raise ValueError('--source-browser-rev must not be empty')
    return template.format(rev=quote(revision, safe=''))


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


def remove_compiler_suffix(func_name):
    """去除编译器优化后缀，支持 GCC 和 LLVM/Clang"""
    # 提取函数名（去掉偏移和长度部分）
    match = re.match(r'^(.*?)([+][0-9a-fA-FxX]+)(/[0-9a-fA-FxX]+)$', func_name)
    if match:
        base_func = match.group(1)
        offset = match.group(2)
        length = match.group(3)
    else:
        base_func = func_name
        offset = ""
        length = ""

    # GCC 和 LLVM/Clang 常见的优化后缀
    # 包括：.isra.N, .constprop.N, .lto.N, .part.N, .cold.N, .clone.N, .llvm.N, .unk.N
    # 以及：.plt, .ifunc, .const, .pure, .cold (无数字)

    # 第一步：去除带数字的后缀
    cleaned = re.sub(r'\.(isra|constprop|lto|part|cold|clone|llvm|unk)\.\d+', '', base_func)

    # 第二步：去除无数字的后缀
    cleaned = re.sub(r'\.(plt|ifunc|const|pure|cold)\b', '', cleaned)

    # 第三步：处理可能残留的多个后缀（如 .isra.0.constprop.1）
    # 再次清理，确保完全去除
    cleaned = re.sub(r'\.(isra|constprop|lto|part|cold|clone|llvm|unk)\.\d+', '', cleaned)
    cleaned = re.sub(r'\.(plt|ifunc|const|pure|cold)\b', '', cleaned)

    # 如果有偏移和长度，重新组合
    if offset and length:
        return f"{cleaned}{offset}{length}"
    else:
        return cleaned


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
        if (key == 'input_format' and value == 'auto') or (key == 'raw_instance' and not value):
            continue
        if key == 'report_mode' and value == 'single':
            continue
        if key == 'sidecar_threshold' and value is None:
            continue
        if key in ('source_browser', 'source_browser_rev') and not value:
            continue
        # Timeline options are only listed when used (keeps output identical otherwise)
        if key in ('timeline', 'tail_calls', 'btf', 'no_btf', 'pid', 'comm', 'cpu', 'time_range', 'graph', 'notrace', 'compare', 'overhead', 'no_text_view', 'keep_source', 'config', 'preset', 'no_config', 'source_url', 'source_rev', 'source_cache', 'export') and not value:
            continue

        # 格式化参数显示
        display_key = key.replace('_', ' ').title()
        if key == 'ftrace_file' and isinstance(value, list) and len(value) == 1:
            value = value[0]          # one input: shown as before (basename when long)

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
