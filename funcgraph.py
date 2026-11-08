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
import json

# ============================================================
# Constants and Configuration
# ============================================================
APP_VERSION = "0.7"      # 应用版本号
APP_AUTHOR = "@dolinux"  # 作者信息
APP_TITLE = "FuncGraph"  # 应用标题

# Linux内核常见错误码映射（负值表示错误）
# 注意：0不包含在内，因为0是成功返回值，不是错误码
ERROR_CODE_MAP = {
    # 负值（错误码）
    -1: "EPERM",
    -2: "ENOENT",
    -3: "ESRCH",
    -4: "EINTR",
    -5: "EIO",
    -6: "ENXIO",
    -7: "E2BIG",
    -8: "ENOEXEC",
    -9: "EBADF",
    -10: "ECHILD",
    -11: "EAGAIN",
    -12: "ENOMEM",
    -13: "EACCES",
    -14: "EFAULT",
    -15: "ENOTBLK",
    -16: "EBUSY",
    -17: "EEXIST",
    -18: "EXDEV",
    -19: "ENODEV",
    -20: "ENOTDIR",
    -21: "EISDIR",
    -22: "EINVAL",
    -23: "ENFILE",
    -24: "EMFILE",
    -25: "ENOTTY",
    -26: "ETXTBSY",
    -27: "EFBIG",
    -28: "ENOSPC",
    -29: "ESPIPE",
    -30: "EROFS",
    -31: "EMLINK",
    -32: "EPIPE",
    -33: "EDOM",
    -34: "ERANGE",
    -35: "EDEADLK",
    -36: "ENAMETOOLONG",
    -37: "ENOLCK",
    -38: "ENOSYS",
    -39: "ENOTEMPTY",
    -40: "ELOOP",
    -42: "ENOMSG",
    -43: "EIDRM",
    -44: "ECHRNG",
    -45: "EL2NSYNC",
    -46: "EL3HLT",
    -47: "EL3RST",
    -48: "ELNRNG",
    -49: "EUNATCH",
    -50: "ENOCSI",
    -51: "EL2HLT",
    -52: "EBADE",
    -53: "EBADR",
    -54: "EXFULL",
    -55: "ENOANO",
    -56: "EBADRQC",
    -57: "EBADSLT",
    -59: "EBFONT",
    -60: "ENOSTR",
    -61: "ENODATA",
    -62: "ETIME",
    -63: "ENOSR",
    -64: "ENONET",
    -65: "ENOPKG",
    -66: "EREMOTE",
    -67: "ENOLINK",
    -68: "EADV",
    -69: "ESRMNT",
    -70: "ECOMM",
    -71: "EPROTO",
    -72: "EMULTIHOP",
    -73: "EDOTDOT",
    -74: "EBADMSG",
    -75: "EOVERFLOW",
    -76: "ENOTUNIQ",
    -77: "EBADFD",
    -78: "EREMCHG",
    -79: "ELIBACC",
    -80: "ELIBBAD",
    -81: "ELIBSCN",
    -82: "ELIBMAX",
    -83: "ELIBEXEC",
    -84: "EILSEQ",
    -85: "ERESTART",
    -86: "ESTRPIPE",
    -87: "EUSERS",
    -88: "ENOTSOCK",
    -89: "EDESTADDRREQ",
    -90: "EMSGSIZE",
    -91: "EPROTOTYPE",
    -92: "ENOPROTOOPT",
    -93: "EPROTONOSUPPORT",
    -94: "ESOCKTNOSUPPORT",
    -95: "EOPNOTSUPP",
    -96: "EPFNOSUPPORT",
    -97: "EAFNOSUPPORT",
    -98: "EADDRINUSE",
    -99: "EADDRNOTAVAIL",
    -100: "ENETDOWN",
    -101: "ENETUNREACH",
    -102: "ENETRESET",
    -103: "ECONNABORTED",
    -104: "ECONNRESET",
    -105: "ENOBUFS",
    -106: "EISCONN",
    -107: "ENOTCONN",
    -108: "ESHUTDOWN",
    -109: "ETOOMANYREFS",
    -110: "ETIMEDOUT",
    -111: "ECONNREFUSED",
    -112: "EHOSTDOWN",
    -113: "EHOSTUNREACH",
    -114: "EALREADY",
    -115: "EINPROGRESS",
    -116: "ESTALE",
    -117: "EUCLEAN",
    -118: "ENOTNAM",
    -119: "ENAVAIL",
    -120: "EISNAM",
    -121: "EREMOTEIO",
    -122: "EDQUOT",
    -123: "ENOMEDIUM",
    -124: "EMEDIUMTYPE",
    -125: "ECANCELED",
    -126: "ENOKEY",
    -127: "EKEYEXPIRED",
    -128: "EKEYREVOKED",
    -129: "EKEYREJECTED",
    -130: "EOWNERDEAD",
    -131: "ENOTRECOVERABLE",
    -132: "ERFKILL",
    -133: "EHWPOISON",
    # 内核重启和NFS相关错误码
    -512: "ERESTARTSYS",
    -513: "ERESTARTNOINTR",
    -514: "ERESTARTNOHAND",
    -515: "ENOIOCTLCMD",
    -516: "ERESTART_RESTARTBLOCK",
    -517: "EPROBE_DEFER",
    -518: "EOPENSTALE",
    -519: "ENOPARAM",
    -521: "EBADHANDLE",
    -522: "ENOTSYNC",
    -523: "EBADCOOKIE",
    -524: "ENOTSUPP",
    -525: "ETOOSMALL",
    -526: "ESERVERFAULT",
    -527: "EBADTYPE",
    -528: "EJUKEBOX",
    -529: "EIOCBQUEUED",
    -530: "ERECALLCONFLICT",
    -531: "ENOGRACE",
}

# 反向映射：从宏名到数字
ERROR_CODE_NAME_MAP = {v: k for k, v in ERROR_CODE_MAP.items()}

# ============================================================
# Data Model
# ============================================================
class FtraceLine:
    """Parsed ftrace line data. Uses __slots__ for memory efficiency."""
    __slots__ = [
        'raw_line', 'expandable', 'func_info', 'raw_func_name',
        'display_func_name', 'module_name', 'cpu', 'pid', 'comm',
        'func_name_info', 'ret_value', 'params', 'duration',
        'sort_duration', 'duration_raw', 'fold_id', 'fold_type',
        'fold_ancestors', 'call_depth',
        'irqs_off', 'need_resched', 'hardirq_softirq', 'preempt_depth',
    ]

    def __init__(self, raw_line='', expandable=False, func_info=None,
                 raw_func_name=None, display_func_name=None, module_name=None,
                 cpu=None, pid=None, comm=None, func_name_info=None,
                 ret_value=None, params=None, duration=None,
                 sort_duration=None, duration_raw=None, fold_id=None,
                 fold_type=None, fold_ancestors='', call_depth=None,
                 irqs_off=None, need_resched=None, hardirq_softirq=None,
                 preempt_depth=None):
        self.raw_line = raw_line
        self.expandable = expandable
        self.func_info = func_info
        self.raw_func_name = raw_func_name
        self.display_func_name = display_func_name
        self.module_name = module_name
        self.cpu = cpu
        self.pid = pid
        self.comm = comm
        self.func_name_info = func_name_info
        self.ret_value = ret_value
        self.params = params
        self.duration = duration
        self.sort_duration = sort_duration
        self.duration_raw = duration_raw
        self.fold_id = fold_id
        self.fold_type = fold_type
        self.fold_ancestors = fold_ancestors
        self.call_depth = call_depth
        self.irqs_off = irqs_off
        self.need_resched = need_resched
        self.hardirq_softirq = hardirq_softirq
        self.preempt_depth = preempt_depth

    def get(self, key, default=None):
        """Dict-compatible get() for gradual migration."""
        return getattr(self, key, default)

    def __setitem__(self, key, value):
        """Dict-compatible __setitem__ for gradual migration."""
        setattr(self, key, value)

    def __getitem__(self, key):
        """Dict-compatible __getitem__ for gradual migration."""
        return getattr(self, key)

    def __contains__(self, key):
        """Dict-compatible 'in' operator for gradual migration."""
        return hasattr(self, key) and getattr(self, key) is not None

# ============================================================
# Utility Functions
# ============================================================

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

# ============================================================
# Syntax Highlighting
# ============================================================

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
    - 函数名: <span class="hl-func">func_name</span> (内核函数和模块函数使用相同颜色)
    - 十六进制地址: <span class="hl-addr">0xf4</span>
    - 注释: <span class="hl-comment">/* ... */</span>
    - 符号括号: <span class="hl-symbol">{}</span>
    - 返回值: <span class="hl-ret-error">ret=-22</span> (错误码) 或 <span class="hl-ret-success">ret=0</span> (成功)

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

    # 2. 提取并临时替换注释，防止后续高亮影响注释内容
    # 使用唯一标记来标识注释位置
    comment_placeholders = []
    def save_comment(match):
        comment = match.group(0)
        placeholder = f"__COMMENT_{len(comment_placeholders)}__"
        comment_placeholders.append((placeholder, comment))
        return placeholder

    text = re.sub(r'(/\*[^*]*(?:\*+[^/*][^*]*)*\*+/)', save_comment, text)

    # 3. 高亮十六进制地址 (0x...)
    text = re.sub(r'(0[xX][0-9a-fA-F]+)', r'<span class="hl-addr">\1</span>', text)

    # 4. 高亮时间数值 (小数点格式)
    text = re.sub(r'(\d+\.\d+)', r'<span class="hl-time">\1</span>', text)

    # 5. 高亮时间单位 (us, ms, ns, ks)
    text = re.sub(r'\b(us|ms|ns|ks)\b', r'<span class="hl-unit">\1</span>', text)

    # 6. 高亮返回值 (ret=xxx)
    def highlight_ret_value(match):
        ret_str = match.group(0)  # 完整的 ret=xxx
        ret_val_str = match.group(1)  # 只有 xxx 部分

        # 尝试解析为整数（支持10进制和16进制）
        ret_val = None
        try:
            if ret_val_str.startswith('0x') or ret_val_str.startswith('0X'):
                ret_val = int(ret_val_str, 16)
                # 处理64位无符号整数转换为有符号整数
                # 但只对明显是负数的值进行转换
                if ret_val >= 0x8000000000000000:
                    converted = ret_val - 0x10000000000000000
                    if converted < 0:
                        ret_val = converted
            else:
                ret_val = int(ret_val_str)
        except ValueError:
            # 无法解析，返回原始格式
            return f'<span class="hl-ret-normal">{ret_str}</span>'

        # 只对合理的错误码范围进行高亮
        if ret_val < -1000000 or ret_val > 1000000:
            # 值太大，可能是误判，只做普通高亮
            return f'<span class="hl-ret-normal">{ret_str}</span>'

        # 查找错误码映射
        error_name = ERROR_CODE_MAP.get(ret_val)

        if error_name:
            # 是已知错误码（只能是负数）
            return f'<span class="hl-ret-error">{ret_str} /* {error_name} */</span>'
        else:
            # 不是已知错误码
            if ret_val == 0:
                # 0，普通显示（不特殊高亮）
                return f'<span class="hl-ret-normal">{ret_str}</span>'
            elif ret_val < 0:
                # 负数但不在映射中（如-515），只做普通高亮
                return f'<span class="hl-ret-normal">{ret_str}</span>'
            else:
                # 正数，普通返回值
                return f'<span class="hl-ret-normal">{ret_str}</span>'

    text = re.sub(r'\bret\s*=\s*([0-9a-fA-FxX-]+)', highlight_ret_value, text)

    # 7. 高亮函数名 - 内核函数和模块函数使用相同颜色
    # 匹配所有函数名：func_name() 或 func_name [module]()
    text = re.sub(r'([a-zA-Z_]\w*)(?=\s*(?:\[.*\])?\s*\()', r'<span class="hl-func">\1</span>', text)

    # 8. 高亮括号和大括号
    text = re.sub(r'([{}()\[\];])', r'<span class="hl-symbol">\1</span>', text)

    # 9. 恢复注释，并高亮
    for placeholder, comment in comment_placeholders:
        # 高亮注释
        highlighted_comment = f'<span class="hl-comment">{comment}</span>'
        text = text.replace(placeholder, highlighted_comment)

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

# ============================================================
# Ftrace Parser
# ============================================================

class FtraceParser:
    """Parses ftrace function_graph output files."""

    def __init__(self, verbose=False):
        self.verbose = verbose

    def parse(self, file_path, enable_fold=False):
        """Parse ftrace file and return list of FtraceLine objects.

        This is the main entry point, equivalent to the old parse_ftrace_file().

        参数:
            file_path: ftrace输出文件路径
            enable_fold: 是否启用函数调用折叠功能

        返回:
            parsed_lines: 包含所有行信息的列表
        """
        verbose_print(f"Parsing ftrace file: {file_path}", self.verbose)
        parsed_lines = []
        expandable_count = 0

        try:
            with open(file_path, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        line = line.rstrip('\n')

                        # 首先检查是否是注释行或空行（跳过）
                        if line.startswith('#') or not line.strip():
                            line_data = FtraceLine(raw_line=line)
                            parsed_lines.append(line_data)
                            continue

                        # 解析CPU、PID、进程名
                        pid = None
                        comm = None
                        ret_value = None
                        params = None

                        # 方法1: 匹配CPU编号
                        cpu = self._parse_cpu(line)

                        # 方法2: 查找 PID/Comm 格式（只在函数调用之前的部分查找）
                        # 关键：只在函数调用之前的部分查找，避免匹配函数参数
                        func_start = line.find('(')
                        if func_start == -1:
                            func_start = len(line)

                        prefix = line[:func_start]

                        # 关键：排除耗时信息和状态字符（latency 模式）
                        # 格式1（普通）：CPU)  [进程-PID]  |  [耗时]  |  函数
                        # 格式2（Latency）：CPU)  进程-PID  |  状态字符  |  [耗时]  |  函数
                        # 格式3（带时间戳）：时间戳 | CPU)  进程-PID  |  状态  |  [耗时]  |  函数

                        # 检查是否是带时间戳的格式
                        has_timestamp = bool(re.match(r'^\s*[\d.]+\s*\|\s*\d+\)', prefix))

                        # 找到第一个分隔符 | 的位置
                        pipe_pos = prefix.find('|')
                        if pipe_pos != -1:
                            # 有分隔符，耗时信息在第一个分隔符之后
                            # 但是 latency 模式有两个分隔符：状态字符和耗时
                            # 格式：CPU)  进程-PID  |  状态  |  [耗时]  |  函数

                            # 找到第二个分隔符 | 的位置（状态字符之后）
                            second_pipe = prefix.find('|', pipe_pos + 1)
                            if second_pipe != -1:
                                # 有第二个分隔符
                                if has_timestamp:
                                    # 带时间戳格式：时间戳 | CPU)  进程-PID  |  状态  |  [耗时]  |  函数
                                    # 在第一个分隔符之后查找 PID/Comm
                                    search_area = prefix[pipe_pos + 1:]
                                else:
                                    # Latency 模式：CPU)  进程-PID  |  状态  |  [耗时]  |  函数
                                    # 在第一个分隔符之前查找 PID/Comm
                                    search_area = prefix[:pipe_pos]
                            else:
                                # 只有一个分隔符
                                if has_timestamp:
                                    # 带时间戳格式：时间戳 | CPU)  进程-PID  |  ...
                                    # 在第一个分隔符之后查找 PID/Comm
                                    search_area = prefix[pipe_pos + 1:]
                                else:
                                    # 普通格式：CPU)  进程-PID  |  ...
                                    # 在分隔符之前查找 PID/Comm
                                    search_area = prefix[:pipe_pos]
                        else:
                            # 没有分隔符，耗时信息可能在 CPU 编号之后
                            # 移除耗时格式：[ $@*#!+ ]数字.us
                            timing_pattern = r'[ $@*#!+]*\d+\.us'
                            search_area = re.sub(timing_pattern, '', prefix)

                        # 如果是带时间戳的格式，需要从search_area中移除CPU部分
                        # 格式：时间戳 | CPU)  进程-PID  |  ...
                        # 现在search_area是：CPU)  进程-PID  |  ...
                        # 需要移除 CPU) 部分
                        if has_timestamp:
                            cpu_match_ts = re.match(r'^\s*\d+\)\s*', search_area)
                            if cpu_match_ts:
                                search_area = search_area[cpu_match_ts.end():]

                        # DEBUG: 打印search_area内容（可以注释掉）
                        # print(f"DEBUG: has_timestamp={has_timestamp}, search_area='{search_area}'", file=sys.stderr)

                        # 提取耗时信息
                        duration, sort_duration, duration_raw = self._parse_duration(prefix)

                        # 提取 PID/Comm
                        pid, comm = self._parse_pid_comm(search_area)

                        # 提取 latency 字段（irqs-off, need-resched, hardirq/softirq, preempt-depth）
                        # Latency 模式格式：CPU)  进程-PID  |  d.h2  |  [耗时]  |  函数
                        # 4 个字符: irqs_off(d/.), need_resched(B/N/n/p/L/b/l/.), hardirq_softirq(Z/z/H/h/s/.), preempt_depth(0-9)
                        irqs_off = None
                        need_resched = None
                        hardirq_softirq = None
                        preempt_depth = None
                        if pipe_pos != -1 and second_pipe != -1 and not has_timestamp:
                            # Latency 模式：状态字段在 pipe_pos 和 second_pipe 之间
                            latency_area = prefix[pipe_pos+1:second_pipe]
                            lat_match = re.search(r'([d.])([.BNnpLbl])([.ZzHhs])(\d)', latency_area)
                            if lat_match:
                                irqs_off = lat_match.group(1)
                                need_resched = lat_match.group(2)
                                hardirq_softirq = lat_match.group(3)
                                preempt_depth = lat_match.group(4)
                        elif has_timestamp and pipe_pos != -1:
                            # 带时间戳格式：时间戳 | CPU) 进程-PID | 状态 | [耗时] | 函数
                            # 需要找第二、三个 pipe 之间的 latency 区域
                            third_pipe = prefix.find('|', second_pipe + 1) if second_pipe != -1 else -1
                            if second_pipe != -1 and third_pipe != -1:
                                latency_area = prefix[second_pipe+1:third_pipe]
                                lat_match = re.search(r'([d.])([.BNnpLbl])([.ZzHhs])(\d)', latency_area)
                                if lat_match:
                                    irqs_off = lat_match.group(1)
                                    need_resched = lat_match.group(2)
                                    hardirq_softirq = lat_match.group(3)
                                    preempt_depth = lat_match.group(4)

                        # 检查是否包含函数信息（支持多种格式）
                        func_info = None
                        raw_func_name = None
                        display_func_name = None
                        module_name = None

                        # 格式1: 函数调用 + 返回地址
                        # 例如:
                        # - rcu_rdp_cpu_online.isra.0() { /* <-rcu_lockdep_current_cpu_online+0x48/0x70 */
                        # - preempt_count_add(val=65536); /* <-irq_enter_rcu+0x17/0x80 */
                        # - tick_irq_enter() { /* <-irq_enter_rcu+0x6a/0x80 */
                        # - mi_after_dequeue_task_hook [metis]() { /* <-__traceiter_android_rvh_after_dequeue_task+0x60/0x8c */
                        if '/*' in line and '<-' in line:
                            # 提取函数调用名称，支持多种格式：
                            # 1. func() { - 函数调用开始
                            # 2. func(args); - 带参数的函数调用
                            # 3. func() - 函数调用
                            # 4. func [module](args) { - 模块函数调用
                            func_name_match = re.search(r'([a-zA-Z_][a-zA-Z0-9_.]*)\s*(?:\[([^\]]+)\])?\s*\([^)]*\)\s*[;{]?', line)
                            if func_name_match:
                                raw_func_name = func_name_match.group(1)
                                # 如果有模块名，提取它（这是当前函数的模块名）
                                if func_name_match.group(2):
                                    module_name = func_name_match.group(2)
                                # 处理后的函数名用于显示（去除编译器后缀）
                                display_func_name = remove_compiler_suffix(raw_func_name)

                                # 提取参数（如果存在）
                                # 格式: func(arg1=val1, arg2=val2, ...)
                                params = None
                                params_match = re.search(r'\(([^)]*)\)', line)
                                if params_match and params_match.group(1).strip():
                                    params = params_match.group(1).strip()

                            # 提取返回地址（注意：返回地址的模块名不影响当前函数的module_name）
                            func_match = re.search(r'/\*\s*<-(.*?)\s*\*/', line)
                            if func_match:
                                full_func_info = func_match.group(1).strip()
                                # 从返回地址中提取函数信息，去掉 ret=xxx 部分
                                # 格式: func+offset/length [module] ret=xxx
                                # 或者: func+offset/length [module]
                                # 或者: func+offset/length ret=xxx
                                # 或者: func+offset/length

                                # 先去掉 ret=xxx 部分
                                func_info = re.sub(r'\s+ret=.*$', '', full_func_info)
                                # 保留func_info中的模块名信息，用于后续处理
                                # 格式: func+offset/length [module]
                                # 返回地址中的[module]是返回地址的模块，不覆盖当前函数的module_name
                                # 所以这里不提取module_name

                            # 提取当前函数的返回值（如果存在）
                            # 格式: func() { /* <-... ret=xxx */
                            ret_value = None
                            ret_match = re.search(r'ret=([0-9a-fA-FxX-]+|true|false)', line)
                            if ret_match:
                                ret_value = ret_match.group(1)

                        # 格式3: /* func+offset/length [module] */ (没有 <-)
                        elif '/*' in line and not '<-' in line:
                            comment_match = re.search(r'/\*\s*([a-zA-Z_][a-zA-Z0-9_.]*\+[0-9a-fA-FxX]+/[0-9a-fA-FxX]+)(?:\s*\[(.*?)\])?\s*\*/', line)
                            if comment_match:
                                func_info = comment_match.group(1)
                                if comment_match.group(2):
                                    module_name = comment_match.group(2)
                            # 提取返回值（仅当 Format 1 未解析时）
                            if ret_value is None:
                                ret_match = re.search(r'ret\s*=\s*([0-9a-fA-FxX-]+|true|false)', line)
                                if ret_match:
                                    ret_value = ret_match.group(1)

                        # 格式4: 直接在行中 func+offset/length (没有注释)
                        if func_info is None:
                            direct_match = re.search(r'([a-zA-Z_][a-zA-Z0-9_.]*\+[0-9a-fA-FxX]+/[0-9a-fA-FxX]+)', line)
                            if direct_match:
                                func_info = direct_match.group(1)
                            # 提取返回值（仅当之前未解析时）
                            if ret_value is None:
                                ret_match = re.search(r'ret\s*=\s*([0-9a-fA-FxX-]+|true|false)', line)
                                if ret_match:
                                    ret_value = ret_match.group(1)

                        # 如果找到函数信息，添加到解析结果
                        if func_info or raw_func_name:
                            line_data = FtraceLine(
                                raw_line=line, expandable=True,
                                func_info=func_info,
                                raw_func_name=raw_func_name,
                                display_func_name=display_func_name,
                                module_name=module_name,
                                cpu=cpu, pid=pid, comm=comm,
                                ret_value=ret_value,
                                params=params,
                                duration=duration,
                                sort_duration=sort_duration if sort_duration is not None else duration,
                                duration_raw=duration_raw,
                                irqs_off=irqs_off, need_resched=need_resched,
                                hardirq_softirq=hardirq_softirq, preempt_depth=preempt_depth,
                            )
                            parsed_lines.append(line_data)
                            expandable_count += 1


                            continue

                        # 检查不可展开的行中是否包含函数名（如 ret= 格式）
                        # 格式: 3)   1.175 us    |  } /* finish_task_switch.isra.0 ret=0xffffffff81381f60 */
                        ret_func_match = re.search(r'/\*\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s+ret=', line)
                        if ret_func_match:
                            raw_func_name = ret_func_match.group(1)
                            display_func_name = remove_compiler_suffix(raw_func_name)

                            # 提取返回值（支持 ret=xxx 或 ret = xxx）
                            ret_value = None
                            ret_match = re.search(r'ret\s*=\s*([0-9a-fA-FxX-]+|true|false)', line)
                            if ret_match:
                                ret_value = ret_match.group(1)

                            line_data = FtraceLine(
                                raw_line=line,
                                raw_func_name=raw_func_name,
                                display_func_name=display_func_name,
                                cpu=cpu, pid=pid, comm=comm,
                                ret_value=ret_value,
                                duration=duration,
                                sort_duration=sort_duration if sort_duration is not None else duration,
                                duration_raw=duration_raw,
                                irqs_off=irqs_off, need_resched=need_resched,
                                hardirq_softirq=hardirq_softirq, preempt_depth=preempt_depth,
                            )
                            parsed_lines.append(line_data)


                            continue

                        # 检查不可展开的行中是否只包含返回值（没有函数名）
                        # 格式: 2)    bash-509    |               |                            } /* ret=0 */
                        ret_only_match = re.search(r'/\*\s*ret=([0-9a-fA-FxX-]+|true|false)\s*\*/', line)
                        if ret_only_match:
                            ret_value = ret_only_match.group(1)
                            line_data = FtraceLine(
                                raw_line=line,
                                cpu=cpu, pid=pid, comm=comm,
                                ret_value=ret_value,
                                duration=duration,
                                sort_duration=sort_duration if sort_duration is not None else duration,
                                duration_raw=duration_raw,
                                irqs_off=irqs_off, need_resched=need_resched,
                                hardirq_softirq=hardirq_softirq, preempt_depth=preempt_depth,
                            )
                            parsed_lines.append(line_data)
                            continue

                        # 检查是否是函数出口（包含 }）
                        # 格式: } /* func */ 或 } /* func ret=xxx */
                        exit_match = re.search(r'}\s*/\*\s*([a-zA-Z_][a-zA-Z0-9_.]*)', line)
                        if exit_match:
                            raw_func_name = exit_match.group(1)
                            display_func_name = remove_compiler_suffix(raw_func_name)

                            # 提取返回值（如果存在）
                            ret_value = None
                            ret_match = re.search(r'ret\s*=\s*([0-9a-fA-FxX-]+|true|false)', line)
                            if ret_match:
                                ret_value = ret_match.group(1)

                            line_data = FtraceLine(
                                raw_line=line,
                                raw_func_name=raw_func_name,
                                display_func_name=display_func_name,
                                cpu=cpu, pid=pid, comm=comm,
                                ret_value=ret_value,
                                duration=duration,
                                sort_duration=sort_duration if sort_duration is not None else duration,
                                duration_raw=duration_raw,
                                irqs_off=irqs_off, need_resched=need_resched,
                                hardirq_softirq=hardirq_softirq, preempt_depth=preempt_depth,
                            )
                            parsed_lines.append(line_data)
                            continue

                        line_data = FtraceLine(
                            raw_line=line,
                            cpu=cpu, pid=pid, comm=comm,
                            duration=duration,
                            sort_duration=sort_duration if sort_duration is not None else duration,
                            duration_raw=duration_raw,
                            irqs_off=irqs_off, need_resched=need_resched,
                            hardirq_softirq=hardirq_softirq, preempt_depth=preempt_depth,
                        )
                        parsed_lines.append(line_data)

                    except Exception as e:
                        verbose_print(f"Error parsing line {line_num}: {str(e)}", self.verbose)
                        # 添加为普通行继续处理
                        line_data = FtraceLine(raw_line=line.rstrip('\n'))
                        parsed_lines.append(line_data)

        except Exception as e:
            print(f"Error reading file {file_path}: {str(e)}", file=sys.stderr)
            sys.exit(1)

        # 如果启用了折叠功能，识别函数调用折叠块
        if enable_fold:
            verbose_print("Identifying fold blocks...", self.verbose)
            self._identify_fold_blocks(parsed_lines)

        verbose_print(f"Parsed {len(parsed_lines)} lines, found {expandable_count} expandable entries", self.verbose)
        return parsed_lines

    def _parse_cpu(self, line):
        """Extract CPU number from an ftrace line.

        参数:
            line: 原始ftrace行

        返回:
            int or None: CPU编号
        """
        # 支持多种前缀：
        # - 直接 CPU 格式: "  3)" 或 " 0)"（行首）
        # - 带时间戳格式: "1324242.786252 |   5)"（时间戳 | CPU）
        # - 由行号前缀的格式: "84  3)"（行号 + CPU）
        cpu_match = re.match(r'^\s*(\d+)\)', line)
        if cpu_match:
            return int(cpu_match.group(1))

        # 先尝试匹配行号 + CPU 的格式，如: "84  3)"
        cpu_match_line_num = re.match(r'^\s*\d+\s+(\d+)\)', line)
        if cpu_match_line_num:
            return int(cpu_match_line_num.group(1))

        # 尝试匹配带时间戳的格式: "时间戳 | CPU)"
        cpu_match_timestamp = re.match(r'^\s*[\d.]+\s*\|\s*(\d+)\)', line)
        if cpu_match_timestamp:
            return int(cpu_match_timestamp.group(1))

        return None

    def _parse_pid_comm(self, search_area):
        """Extract PID and process name from the search area.

        参数:
            search_area: 已经计算好的搜索区域字符串

        返回:
            (pid, comm) tuple: PID(int or None) 和进程名(str or None)
        """
        pid = None
        comm = None

        # 在清理后的区域中查找 PID/Comm
        # Format 1: "comm-PID" (最常见: bash-430, <idle>-0, kworker/0:1-123)
        # 限制 comm 必须包含至少一个字母或 < 或 /（避免匹配纯数字或管道符）
        comm_pid_dash_match = re.search(r'([\w<>/:.+\[\]-]+)-(\d+)', search_area)
        if comm_pid_dash_match:
            candidate_comm = comm_pid_dash_match.group(1)
            # 确保 comm 不是纯数字（纯数字可能是 CPU 残留）
            if not candidate_comm.isdigit():
                comm = candidate_comm
                pid = int(comm_pid_dash_match.group(2))

        # Format 2: "comm/PID" (如: bash/430)
        if pid is None:
            comm_pid_match = re.search(r'([a-zA-Z_<][\w<>.-]*)/(\d+)', search_area)
            if comm_pid_match:
                comm = comm_pid_match.group(1)
                pid = int(comm_pid_match.group(2))

        # Format 3: "tgid/pid" (纯数字，两个数字用 / 分隔 — 取第二个作为 PID)
        if pid is None:
            pid_comm_match = re.search(r'\s+(\d+)/(\d+)', search_area)
            if pid_comm_match:
                pid = int(pid_comm_match.group(2))

        # 方法3: 查找 prev= 或 next= 参数中的进程信息（只在搜索区域中查找）
        if comm is None:
            prev_next_match = re.search(r'(?:prev|next)=0x[0-9a-fA-F]+(?:\s*,\s*prev=)?\s*0x[0-9a-fA-F]+(?:\s*,\s*comm=)?\s*([a-zA-Z_][a-zA-Z0-9_-]*)', search_area)
            if prev_next_match:
                comm = prev_next_match.group(1)

        return (pid, comm)

    def _parse_duration(self, prefix):
        """Extract duration info from an ftrace line prefix.

        参数:
            prefix: 函数调用之前的行前缀部分

        返回:
            (duration, sort_duration, duration_raw) tuple
        """
        duration = None
        sort_duration = None
        duration_raw = None

        # 提取耗时信息（在搜索区域之前）
        # 格式: " 3)   0.157 us    |" 或 " 3)  $1.234 us    |"
        # 耗时格式: [ $@*#!+ ]数字[.数字] us
        duration_match = re.search(r'([ $@*#!+]*)(\d+(?:\.\d+)?)\s*us', prefix)
        if duration_match:
            duration_raw = duration_match.group(0).strip()
            duration_str = duration_match.group(2)
            try:
                # 存储原始显示值，不加前缀
                # 这样用户输入 >100 && <200 时能正确过滤 !145.859 us
                duration = float(duration_str)

                # 但为了排序，需要计算实际值
                # 存储一个额外的排序值
                prefix_char = duration_match.group(1).strip()
                sort_duration = duration
                if prefix_char == '$':
                    sort_duration += 1000000  # 1秒 = 1000000微秒
                elif prefix_char == '@':
                    sort_duration += 100000  # 100毫秒 = 100000微秒
                elif prefix_char == '#':
                    sort_duration += 1000  # 1000微秒
                elif prefix_char == '!':
                    sort_duration += 100  # 100微秒
                elif prefix_char == '+':
                    sort_duration += 10  # 10微秒
                # ' ' 或没有前缀：0-10微秒，直接使用数值
            except ValueError:
                duration = None
                sort_duration = None

        return (duration, sort_duration, duration_raw)

    def _identify_fold_blocks(self, parsed_lines):
        """Identify function call fold blocks in parsed lines.

        Modifies parsed_lines in place.

        参数:
            parsed_lines: 解析后的行列表
        """
        # 使用栈来跟踪函数调用关系
        # 栈中存储: (cpu, pid, func_name, line_index, fold_id, call_depth, indent_level)
        call_stack = []

        for line_index, line_data in enumerate(parsed_lines):
            cpu = line_data.get('cpu')
            pid = line_data.get('pid')
            raw_func_name = line_data.get('raw_func_name')
            display_func_name = line_data.get('display_func_name')
            raw_line = line_data.get('raw_line', '')

            # 跳过没有CPU/函数名的行
            if cpu is None or raw_func_name is None:
                continue

            # 计算缩进级别：基于函数列（最后一个 '|' 之后）的前导空格数量
            # 这样能更准确地反映函数调用的显示缩进（原始行的行首可能是CPU/时间戳列）
            func_column = raw_line
            if '|' in raw_line:
                # 取最后一个 '|' 之后的部分作为函数显示列
                func_column = raw_line.split('|')[-1]
            indent_match = re.match(r'(\s*)', func_column)
            indent_level = len(indent_match.group(1)) if indent_match else 0

            # 记录当前行所处的折叠祖先（以空格分隔），用于在折叠父函数时也能匹配到内部行
            ancestor_ids = [entry['fold_id'] for entry in call_stack]
            line_data['fold_ancestors'] = ' '.join(ancestor_ids) if ancestor_ids else ''

            # 检查是否是函数入口（包含 {）
            # 格式: func() { 或 func(args) {
            # 注意：行尾可能有注释，所以不能使用 $ 匹配行尾
            if re.search(r'\)\s*\{', raw_line):
                # 这是一个函数入口
                # 创建唯一标识符: CPU_PID_函数名_行号_调用深度_缩进级别
                # 包含行号以确保同名连续调用（同一 depth/indent）具有不同的 fold_id
                # 如果pid为None，则使用空字符串
                pid_str = pid if pid is not None else ""
                # 使用 line_index 作为唯一性的一部分
                fold_id = f"{cpu}_{pid_str}_{raw_func_name}_{line_index}_{len(call_stack)}_{indent_level}"

                # 将入口信息推入栈
                call_stack.append({
                    'cpu': cpu,
                    'pid': pid,
                    'func_name': raw_func_name,
                    'line_index': line_index,
                    'fold_id': fold_id,
                    'call_depth': len(call_stack),
                    'indent_level': indent_level
                })

                # 标记为入口
                line_data['fold_id'] = fold_id
                line_data['fold_type'] = 'entry'
                line_data['call_depth'] = len(call_stack) - 1  # 当前调用深度

                if self.verbose:
                    print(f"Fold entry: {fold_id} at line {line_index + 1}, depth {len(call_stack) - 1}, indent {indent_level}", file=sys.stderr)

            # 检查是否是函数出口（包含 }）
            # 格式: } /* func */ 或 } /* func ret=xxx */
            elif re.search(r'}\s*/\*\s*([a-zA-Z_][a-zA-Z0-9_.]*)', raw_line):
                # 这是一个函数出口
                # 提取函数名
                exit_func_match = re.search(r'}\s*/\*\s*([a-zA-Z_][a-zA-Z0-9_.]*)', raw_line)
                if exit_func_match:
                    exit_func_name = exit_func_match.group(1)

                    # 从栈中查找匹配的入口
                    # 从栈顶开始查找，找到第一个匹配的入口
                    found_match = False
                    for i in range(len(call_stack) - 1, -1, -1):
                        entry = call_stack[i]
                        # 匹配条件：CPU、PID、函数名、缩进级别
                        # 如果entry['pid']为None，则匹配任意pid（包括None）
                        pid_match = (entry['pid'] == pid) or (entry['pid'] is None and pid is None)
                        if (entry['cpu'] == cpu and
                            pid_match and
                            entry['func_name'] == exit_func_name and
                            entry['indent_level'] == indent_level):
                            # 找到匹配的入口
                            fold_id = entry['fold_id']
                            call_depth = entry['call_depth']

                            # 标记为出口
                            line_data['fold_id'] = fold_id
                            line_data['fold_type'] = 'exit'
                            line_data['call_depth'] = call_depth

                            # 从栈中移除
                            call_stack.pop(i)

                            if self.verbose:
                                print(f"Fold exit: {fold_id} at line {line_index + 1}, depth {call_depth}, indent {indent_level}", file=sys.stderr)

                            found_match = True
                            break

                    # 如果没有找到匹配的入口，说明这是一个只有出口的行
                    if not found_match:
                        line_data['fold_id'] = None
                        line_data['fold_type'] = None
                        line_data['call_depth'] = None
            else:
                # 既不是入口也不是出口
                # 检查是否在某个函数调用块内部
                if call_stack:
                    # 当前在某个函数调用块内部
                    # 找到当前最内层的函数调用块
                    innermost_entry = call_stack[-1]
                    fold_id = innermost_entry['fold_id']
                    call_depth = innermost_entry['call_depth']

                    line_data['fold_id'] = fold_id
                    line_data['fold_type'] = 'inner'
                    line_data['call_depth'] = call_depth

                    if self.verbose:
                        print(f"Fold inner: {fold_id} at line {line_index + 1}, depth {call_depth}", file=sys.stderr)
                else:
                    line_data['fold_id'] = None
                    line_data['fold_type'] = None
                    line_data['call_depth'] = None


def parse_ftrace_file(file_path, verbose=False, enable_fold=False):
    """Parse ftrace file. Wrapper for backward compatibility."""
    parser = FtraceParser(verbose=verbose)
    return parser.parse(file_path, enable_fold=enable_fold)


def identify_fold_blocks(parsed_lines, verbose=False):
    """Identify fold blocks. Wrapper for backward compatibility."""
    parser = FtraceParser(verbose=verbose)
    parser._identify_fold_blocks(parsed_lines)

# ============================================================
# Address Resolver
# ============================================================

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
            script_dir = os.path.dirname(os.path.abspath(__file__))
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
                                results[original_func] = '\n'.join(current_output)

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
                        results[original_func] = '\n'.join(current_output)

                    # 检查是否有函数没有出现在输出中
                    for func in func_infos:
                        cleaned_func = re.sub(r'\s*\[.*?\]', '', func)
                        # 清理results的键进行比较
                        cleaned_result_keys = [re.sub(r'\s*\[.*?\]', '', k) for k in results.keys()]
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
                        line = line.rstrip()
                        if not line:
                            continue

                        # 检查是否是新的函数条目
                        if line.endswith(':'):
                            # 保存前一个函数的信息
                            if current_func:
                                # 使用原始键存储
                                original_func = func_info_map.get(current_func, current_func)
                                func_locations[original_func] = current_locations

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
                        original_func = func_info_map.get(current_func, current_func)
                        func_locations[original_func] = current_locations

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
        # Timeline options are only listed when used (keeps output identical otherwise)
        if key in ('timeline', 'btf', 'no_btf') and not value:
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


def parse_module_url_old_format(module_url_str, base_url):
    """处理旧格式的module-url解析"""
    parts = [part.strip() for part in module_url_str.split(',')]
    parts = [part for part in parts if part]

    if not parts:
        return {}, base_url

    # 找出所有URL和模块名
    urls = []
    module_names = []

    for part in parts:
        if part.startswith(('http://', 'https://')):
            urls.append(part)
        else:
            # 模块名，检查是否包含非法字符
            if not part.replace('-', '_').replace('.', '_').isalnum():
                print(f"Warning: module name '{part}' contains potentially invalid characters", file=sys.stderr)
            module_names.append(part)

    if not urls:
        print(f"Warning: module-url '{module_url_str}' contains no valid URLs", file=sys.stderr)
        return {}, base_url

    if not module_names:
        return {}, urls[0]

    if len(urls) == 1:
        return {}, urls[0]

    # 重新解析，找出URL和模块名的对应关系
    module_url_map = {}
    url_to_modules = {}
    urls_without_modules = []

    current_url = None
    pending_modules = []

    for part in parts:
        if part.startswith(('http://', 'https://')):
            if current_url and pending_modules:
                url_to_modules[current_url] = pending_modules
                pending_modules = []
            elif current_url and not pending_modules:
                urls_without_modules.append(current_url)
            current_url = part
        else:
            pending_modules.append(part)

    if current_url:
        if pending_modules:
            url_to_modules[current_url] = pending_modules
        else:
            urls_without_modules.append(current_url)

    default_url = base_url
    if urls_without_modules:
        default_url = urls_without_modules[0]

    for url, modules in url_to_modules.items():
        for module_name in modules:
            module_url_map[module_name] = url

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


# ============================================================
# HTML Generator
# ============================================================

class HtmlGenerator:
    """Generates self-contained interactive HTML visualization."""

    def __init__(self, parsed_lines, vmlinux_path, faddr2line_path,
                 module_dirs=None, base_url=None, module_url=None,
                 kernel_src=None, use_list=False, verbose=False,
                 fast_mode=False, highlight_code=False, path_prefix=None,
                 module_src=None, module_srcs=None, script_args=None,
                 enable_filter=False, parse_time=0, total_time=0,
                 func_links=False, entry_offset=0, enable_fold=False,
                 timeline_json=None):
        # Store all parameters as instance attributes
        self.parsed_lines = parsed_lines
        self.timeline_json = timeline_json
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
</head>
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
                </div>
                <span class="heatmap-legend" id="heatmapLegend"></span>
            </div>
        </div>

        <div id="content">
"""

        # 3. Generate content lines
        html_str += self._generate_content_lines()

        html_str += """
        </div>""" + ("""
        <div id="timelineView"></div>""" if self.timeline_json else "") + """
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
"""
        html_str += self._generate_javascript()
        html_str += self._timeline_scripts()
        html_str += """</body>
</html>
"""

        elapsed = time.time() - start_time
        verbose_print(f"HTML generation completed in {elapsed:.2f} seconds", self.verbose)

        # 替换信息面板占位符
        html_str = html_str.replace('{INFO_CONTENT_PLACEHOLDER}', info_content_html)

        # 将过滤窗口插入到 filterPlaceholder 位置
        if self.filter_html:
            html_str = html_str.replace('<div id="filterPlaceholder"></div>', self.filter_html)

        return html_str, self.vmlinux_time, self.module_time

    # ---- Timeline view (optional, --timeline) ----
    @staticmethod
    def _timeline_asset(name):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', name)
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def _timeline_css(self):
        if not self.timeline_json:
            return ''
        return self._timeline_asset('timeline.css') + '\n'

    def _timeline_tabs(self):
        if not self.timeline_json:
            return ''
        n = self.timeline_json.get('n', 0)
        return ('        <div class="view-tabs">\n'
                '            <button class="view-tab active" data-view="text" onclick="fgSwitchView(\'text\')">Text</button>\n'
                '            <button class="view-tab" data-view="timeline" onclick="fgSwitchView(\'timeline\')">'
                f'Timeline<span class="tab-badge">{n} calls</span></button>\n'
                '        </div>\n')

    def _timeline_scripts(self):
        if not self.timeline_json:
            return ''
        data = json.dumps(self.timeline_json, separators=(',', ':')).replace('<', '\\u003c')
        parts = ''.join(self._timeline_asset(f'timeline_{i}.js') for i in range(1, 5))
        return ('    <script type="application/json" id="tlData">' + data + '</script>\n'
                '    <script>\n' + self._timeline_asset('timeline_boot.js') +
                '\nfunction __tlBoot() {\n' + parts + '\n}\n    </script>\n')

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
                func_name_results = call_faddr2line_for_func_names(
                    self.vmlinux_path, self.faddr2line_path, func_name_list,
                    use_list=self.use_list, verbose=self.verbose, fast_mode=self.fast_mode,
                    path_prefix=self.path_prefix, module_srcs=self.module_srcs,
                    entry_offset=self.entry_offset,
                    func_module_map=func_module_map,
                    module_paths=module_paths
                )
                verbose_print(f"函数名解析完成，获取 {len(func_name_results)} 个结果", self.verbose)

                # 将解析结果存储到 parsed_lines 中
                for line_data in self.parsed_lines:
                    raw_func_name = line_data.get('raw_func_name')
                    if raw_func_name and raw_func_name in func_name_results:
                        line_data['func_name_info'] = func_name_results[raw_func_name]

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
        abs_vmlinux_path = os.path.abspath(self.vmlinux_path)
        abs_faddr2line_path = os.path.abspath(self.faddr2line_path)

        # 如果使用fast模式，优先使用fastfaddr2line.py
        if self.fast_mode:
            script_dir = os.path.dirname(os.path.abspath(__file__))
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
        self.func_name_results = func_name_results
        self.filter_html = filter_html
        self.combined_module_url_map = combined_module_url_map
        self.default_module_url = default_module_url
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
        content_parts = []

        # 添加行号并输出原始ftrace日志
        for idx, line_data in enumerate(self.parsed_lines):
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
                content_parts.append(f'<span class="expand-btn">+</span>')

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
                content_parts.append(f'<div class="expanded-content" id="{line_id}_content" style="display: none;">')

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
                        content_parts.append(parse_list_output(locations, current_base_url, self.kernel_src, self.highlight_code, self.path_prefix, self.module_src))
                    else:
                        # 未知格式
                        content_parts.append(f'<div class="location-link">Source information unavailable for: {escape_html_preserve_spaces(func_info_for_display)}</div>')
                else:
                    content_parts.append(f'<div class="location-link">Source information unavailable for: {escape_html_preserve_spaces(func_info_for_display)}</div>')

                content_parts.append('</div>')

        return ''.join(content_parts)

    def _generate_javascript(self):
        """Generate all JavaScript code."""
        return """
    <script>
        // localStorage namespace: per-file keys use a hash of the document title
        const _lsPrefix = (function() {
            let h = 0;
            const s = document.title || '';
            for (let i = 0; i < s.length; i++) { h = ((h << 5) - h + s.charCodeAt(i)) | 0; }
            return 'fg_' + (h >>> 0).toString(36) + '_';
        })();
        // Global keys (shared across files): theme
        // Per-file keys: expandedLines, foldedBlocks, filterBoxVisible, keyboardHintShown,
        //   infoPanelExpanded, floatingControlsHidden, floatingTogglePos, viewState, heatmapEnabled
        function lsGet(key) { return localStorage.getItem(_lsPrefix + key); }
        function lsSet(key, val) { localStorage.setItem(_lsPrefix + key, val); }
        function lsRemove(key) { localStorage.removeItem(_lsPrefix + key); }

        // 标记是否正在进行文本选择
        let isSelectingText = false;
        // 记录鼠标按下时的位置和时间
        let mouseDownInfo = { x: 0, y: 0, time: 0 };
        // 记录当前高亮行
        let highlightedLine = null;
        // 当前主题 (global, not per-file)
        // Auto-detect system preference for first-time visitors
        let currentTheme = localStorage.getItem('theme') ||
            (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
        // 记录展开状态
        let expandedLines = JSON.parse(lsGet('expandedLines')) || {};
        // 记录折叠状态
        let foldedBlocks = JSON.parse(lsGet('foldedBlocks')) || {};
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
        let keyboardHintShown = lsGet('keyboardHintShown') === 'true';
        // 总行数（从summary中获取）
        let total_lines = 0;

        // 过滤功能相关变量
        let currentFilter = { cpu: [], pid: [], comm: [] };

        // 折叠图标键盘事件处理
                // 折叠图标点击事件处理
                function handleFoldIconClick(event, icon) {
                    event.stopPropagation();
                    const lineContainer = icon.closest('.line-container');
                    if (lineContainer) {
                        const foldId = lineContainer.getAttribute('data-fold-id');
                        if (foldId) {
                            // F1: Shift+Click for recursive fold/unfold
                            if (event.shiftKey) {
                                toggleFoldRecursive(foldId);
                            } else {
                                toggleFold(foldId, 'entry');
                            }
                        }
                    }
                }
        function handleFoldIconKeydown(event, icon) {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                handleFoldIconClick(event, icon);
            }
        }

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

        // 恢复折叠状态
        // Restore all folded states on page load — uses _setFolded for consistency
        function restoreFoldedState() {
            for (const [foldId, isFolded] of Object.entries(foldedBlocks)) {
                if (isFolded) _setFolded(foldId, true);
            }
        }

        // 保存展开状态
        let _expandedStateDirty = false;
        let _expandedFlushTimer = null;
        function saveExpandedState(lineId, isExpanded) {
            expandedLines[lineId] = isExpanded;
            // Batch writes: defer localStorage flush to avoid O(n) serialization per line
            if (!_expandedStateDirty) {
                _expandedStateDirty = true;
                if (_expandedFlushTimer) cancelAnimationFrame(_expandedFlushTimer);
                _expandedFlushTimer = requestAnimationFrame(flushExpandedState);
            }
        }
        function flushExpandedState() {
            if (_expandedStateDirty) {
                lsSet('expandedLines', JSON.stringify(expandedLines));
                _expandedStateDirty = false;
                _expandedFlushTimer = null;
            }
        }
        
        // 初始化键盘导航
        function initKeyboardNavigation() {
            // 使用updateExpandableLines来初始化所有变量
            updateExpandableLines();
            
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
                lsSet('keyboardHintShown', 'true');
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

        // 切换过滤语法帮助面板
        function toggleFilterHelp() {
            const help = document.getElementById('filterSyntaxHelp');
            if (help) help.classList.toggle('visible');
        }

        // 显示过滤提示
        function showHint(event, text) {
            const hint = document.getElementById('filterHint');
            if (hint) {
                hint.textContent = text;
                hint.style.display = 'block';

                // 计算位置：在输入框上方
                const rect = event.target.getBoundingClientRect();
                const hintHeight = hint.offsetHeight || 40; // 默认高度
                hint.style.left = rect.left + 'px';
                hint.style.top = (rect.top - hintHeight - 8) + 'px';
            }
        }

        // 隐藏过滤提示
        function hideHint() {
            const hint = document.getElementById('filterHint');
            if (hint) {
                hint.style.display = 'none';
            }
        }

        // 处理回车键过滤
        function handleFilterKeypress(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                applyFilter();
            }
        }

        // 切换过滤器窗口的显示/隐藏
        function toggleFilterBox() {
            const filterBox = document.getElementById('filterBox');
            if (filterBox) {
                if (filterBox.style.display === 'none') {
                    filterBox.style.display = 'flex';
                    lsSet('filterBoxVisible', 'true');
                } else {
                    filterBox.style.display = 'none';
                    lsSet('filterBoxVisible', 'false');
                }
            }
        }

        // ====== Unified Filter Expression Parser ======

        // Tokenize filter expression into tokens
        function tokenizeFilter(text) {
            const tokens = [];
            let i = 0;
            while (i < text.length) {
                // Skip whitespace
                if (text[i] === ' ' || text[i] === '\\t') { i++; continue; }
                // Parentheses
                if (text[i] === '(') { tokens.push({ type: 'LPAREN' }); i++; continue; }
                if (text[i] === ')') { tokens.push({ type: 'RPAREN' }); i++; continue; }
                // Keywords: AND, OR, NOT (case-insensitive)
                const upper3 = text.slice(i, i + 3).toUpperCase();
                const upper2 = text.slice(i, i + 2).toUpperCase();
                if (upper3 === 'AND' && (i + 3 >= text.length || /[\\s()]/.test(text[i + 3]))) {
                    tokens.push({ type: 'AND' }); i += 3; continue;
                }
                if (upper2 === 'OR' && (i + 2 >= text.length || /[\\s()]/.test(text[i + 2]))) {
                    tokens.push({ type: 'OR' }); i += 2; continue;
                }
                if (upper3 === 'NOT' && (i + 3 >= text.length || /[\\s()]/.test(text[i + 3]))) {
                    tokens.push({ type: 'NOT' }); i += 3; continue;
                }
                // && and ||
                if (text[i] === '&' && text[i + 1] === '&') {
                    tokens.push({ type: 'AND' }); i += 2; continue;
                }
                if (text[i] === '|' && text[i + 1] === '|') {
                    tokens.push({ type: 'OR' }); i += 2; continue;
                }
                if (text[i] === '!') {
                    tokens.push({ type: 'NOT' }); i++; continue;
                }
                // field:value condition (field may contain letters/digits/underscore, value may contain anything except spaces and parens)
                const condMatch = text.slice(i).match(/^([a-zA-Z_][a-zA-Z0-9_]*):(>=|<=|>|<|=)?([^\\s()]*)/);
                if (condMatch) {
                    const field = condMatch[1];
                    const op = condMatch[2] || '=';
                    const value = condMatch[3];
                    tokens.push({ type: 'COND', field: field.toLowerCase(), op, value });
                    i += condMatch[0].length;
                    continue;
                }
                // Sort directive: sort:desc or sort:asc
                const sortMatch = text.slice(i).match(/^sort:(desc|asc)/i);
                if (sortMatch) {
                    tokens.push({ type: 'SORT', value: sortMatch[1].toLowerCase() });
                    i += sortMatch[0].length;
                    continue;
                }
                // Unknown character, skip
                i++;
            }
            return tokens;
        }

        // Recursive descent parser: expr → orExpr
        // orExpr → andExpr (OR andExpr)*
        // andExpr → notExpr (AND notExpr)*
        // notExpr → NOT notExpr | atom
        // atom → COND | LPAREN expr RPAREN
        function parseFilterExpr(text) {
            const tokens = tokenizeFilter(text);
            let sortDir = '';
            // Extract sort directive
            const remaining = tokens.filter(t => {
                if (t.type === 'SORT') { sortDir = t.value; return false; }
                return true;
            });
            let pos = 0;

            function peek() { return remaining[pos] || null; }
            function advance() { return remaining[pos++]; }

            function parseOr() {
                let left = parseAnd();
                while (peek() && peek().type === 'OR') {
                    advance();
                    const right = parseAnd();
                    left = { type: 'OR', left, right };
                }
                return left;
            }

            function parseAnd() {
                let left = parseNot();
                while (peek() && peek().type === 'AND') {
                    advance();
                    const right = parseNot();
                    left = { type: 'AND', left, right };
                }
                // Implicit AND: two conditions without operator
                while (peek() && (peek().type === 'COND' || peek().type === 'NOT' || peek().type === 'LPAREN')) {
                    const right = parseNot();
                    left = { type: 'AND', left, right };
                }
                return left;
            }

            function parseNot() {
                if (peek() && peek().type === 'NOT') {
                    advance();
                    const operand = parseNot();
                    return { type: 'NOT', operand };
                }
                return parseAtom();
            }

            function parseAtom() {
                const t = peek();
                if (!t) return null;
                if (t.type === 'LPAREN') {
                    advance();
                    const expr = parseOr();
                    if (peek() && peek().type === 'RPAREN') advance();
                    return expr;
                }
                if (t.type === 'COND') {
                    advance();
                    return t;
                }
                // Skip unexpected token
                advance();
                return parseAtom();
            }

            const ast = remaining.length > 0 ? parseOr() : null;
            return { ast, sortDir };
        }

        // Evaluate a single condition against a line element
        function evalCondition(line, cond) {
            if (!cond || cond.type !== 'COND') return true;
            const { field, op, value } = cond;

            // Map field names to data attributes
            const fieldAttrMap = {
                'cpu': 'data-cpu', 'pid': 'data-pid', 'comm': 'data-comm',
                'ret': 'data-ret', 'params': 'data-params',
                'duration': 'data-duration',
                'irqs': 'data-irqs', 'resched': 'data-resched',
                'hirq': 'data-hirq', 'preempt': 'data-preempt'
            };

            // func: simple function name match
            if (field === 'func') {
                const funcAttr = line.getAttribute('data-func');
                if (!funcAttr) return false;
                try { return new RegExp(value, 'i').test(funcAttr); }
                catch(e) { return funcAttr.includes(value); }
            }

            // graph: show function's complete call tree
            if (field === 'graph') {
                const key = 'graph:' + value;
                const targetIds = _graphFoldIds[key];
                if (!targetIds || targetIds.size === 0) return false;

                const foldId = line.getAttribute('data-fold-id');
                const ancestorsStr = line.getAttribute('data-fold-ancestors') || '';

                // Line's own fold-id matches, or an ancestor matches
                if (foldId && targetIds.has(foldId)) return true;
                if (ancestorsStr) {
                    const ancestors = ancestorsStr.split(' ');
                    for (let i = 0; i < ancestors.length; i++) {
                        if (targetIds.has(ancestors[i])) return true;
                    }
                }
                return false;
            }

            // notrace: hide function's complete call tree
            if (field === 'notrace') {
                const key = 'notrace:' + value;
                const targetIds = _graphFoldIds[key];
                if (!targetIds || targetIds.size === 0) return true; // no match = show all

                const foldId = line.getAttribute('data-fold-id');
                const ancestorsStr = line.getAttribute('data-fold-ancestors') || '';

                if (foldId && targetIds.has(foldId)) return false;
                if (ancestorsStr) {
                    const ancestors = ancestorsStr.split(' ');
                    for (let i = 0; i < ancestors.length; i++) {
                        if (targetIds.has(ancestors[i])) return false;
                    }
                }
                return true;
            }

            const attrName = fieldAttrMap[field];
            if (!attrName) return true; // Unknown field, pass through

            // Special handling for ret:all
            if (field === 'ret' && value.toLowerCase() === 'all') {
                const retAttr = line.getAttribute('data-ret');
                return retAttr !== null && retAttr !== '';
            }

            // Special handling for duration (comparison operators)
            if (field === 'duration') {
                const durationAttr = line.getAttribute('data-duration');
                if (!durationAttr) return false;
                const dur = parseFloat(durationAttr);
                const val = parseFloat(value);
                if (isNaN(val)) return false;
                switch (op) {
                    case '>': return dur > val;
                    case '>=': return dur >= val;
                    case '<': return dur < val;
                    case '<=': return dur <= val;
                    case '=': return dur === val;
                    default: return dur === val;
                }
            }

            // Special handling for preempt (numeric comparison)
            if (field === 'preempt' && op !== '=') {
                const pAttr = line.getAttribute('data-preempt');
                if (!pAttr) return false;
                const pVal = parseInt(pAttr);
                const cVal = parseInt(value);
                if (isNaN(cVal)) return false;
                switch (op) {
                    case '>': return pVal > cVal;
                    case '>=': return pVal >= cVal;
                    case '<': return pVal < cVal;
                    case '<=': return pVal <= cVal;
                    default: return pVal === cVal;
                }
            }

            // Default: regex match (op '=')
            const attrVal = line.getAttribute(attrName);
            if (attrVal === null || attrVal === '') return false;

            // Special handling for ret: match against data-ret numeric value or error name in raw line
            if (field === 'ret') {
                // Try matching numeric value
                try {
                    const regex = new RegExp(value, 'i');
                    if (regex.test(attrVal)) return true;
                    // Also try matching error code name from raw line
                    const rawLine = line.querySelector('.line-content')?.textContent || '';
                    const retMatch = rawLine.match(/ret\\s*=\\s*([0-9a-fA-FxX-]+)/);
                    if (retMatch && regex.test(retMatch[1])) return true;
                } catch (e) {
                    return attrVal === value;
                }
                return false;
            }

            // For params, also check raw line if no data-params
            if (field === 'params' && !attrVal) {
                const rawLine = line.querySelector('.line-content')?.textContent || '';
                const paramsMatch = rawLine.match(/\\(([^)]*)\\)/);
                if (paramsMatch) {
                    try {
                        return new RegExp(value, 'i').test(paramsMatch[1]);
                    } catch (e) {
                        return paramsMatch[1].includes(value);
                    }
                }
                return false;
            }

            // General regex match
            try {
                return new RegExp(value, 'i').test(attrVal);
            } catch (e) {
                return attrVal === value;
            }
        }

        // Evaluate AST against a line element
        function evalExpr(line, ast) {
            if (!ast) return true;
            switch (ast.type) {
                case 'AND': return evalExpr(line, ast.left) && evalExpr(line, ast.right);
                case 'OR': return evalExpr(line, ast.left) || evalExpr(line, ast.right);
                case 'NOT': return !evalExpr(line, ast.operand);
                case 'COND': return evalCondition(line, ast);
                default: return true;
            }
        }

        // Serialize AST back to text
        function astToText(ast) {
            if (!ast) return '';
            switch (ast.type) {
                case 'AND': {
                    const l = astToText(ast.left);
                    const r = astToText(ast.right);
                    return l && r ? l + ' AND ' + r : l || r;
                }
                case 'OR': {
                    const l = astToText(ast.left);
                    const r = astToText(ast.right);
                    return l && r ? l + ' OR ' + r : l || r;
                }
                case 'NOT':
                    return 'NOT ' + astToText(ast.operand);
                case 'COND':
                    return ast.field + ':' + (ast.op !== '=' ? ast.op : '') + ast.value;
                default:
                    return '';
            }
        }

        // ====== Filter Tags Management ======
        function updateFilterTags(text) {
            const tagsDiv = document.getElementById('filterTags');
            if (!tagsDiv) return;
            tagsDiv.innerHTML = '';

            const { ast } = parseFilterExpr(text);
            if (!ast) return;

            // Flatten top-level AND/OR into tag list
            function flattenExpr(node, items, logicType) {
                if (!node) return;
                if (node.type === logicType) {
                    flattenExpr(node.left, items, logicType);
                    flattenExpr(node.right, items, logicType);
                } else {
                    items.push(node);
                }
            }

            // Determine top-level logic
            let topLogic = 'AND';
            let parts = [];
            if (ast.type === 'AND' || ast.type === 'OR') {
                topLogic = ast.type;
                flattenExpr(ast, parts, topLogic);
            } else {
                parts = [ast];
            }

            parts.forEach((part, idx) => {
                if (idx > 0) {
                    const logicTag = document.createElement('span');
                    logicTag.className = 'filter-tag-logic';
                    logicTag.textContent = topLogic;
                    logicTag.title = 'Click to toggle AND/OR';
                    logicTag.addEventListener('click', () => {
                        // Toggle logic in text
                        const input = document.getElementById('filterInput');
                        if (input) {
                            const newLogic = topLogic === 'AND' ? 'OR' : 'AND';
                            input.value = input.value.replace(new RegExp('\\\\b' + topLogic + '\\\\b', 'gi'), newLogic);
                            updateFilterTags(input.value);
                            applyFilter();
                        }
                    });
                    tagsDiv.appendChild(logicTag);
                }

                const tag = document.createElement('span');
                tag.className = 'filter-tag';
                const tagText = astToText(part);
                tag.innerHTML = tagText + ' <span class="tag-remove" title="Remove">&times;</span>';
                tag.querySelector('.tag-remove').addEventListener('click', () => {
                    // Remove this condition from the input
                    const input = document.getElementById('filterInput');
                    if (input) {
                        // Remove the condition text and surrounding logic operator
                        let newText = input.value;
                        // Try to remove "COND AND " or " AND COND" or " OR COND" or "COND OR "
                        const escaped = tagText.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
                        newText = newText.replace(new RegExp('\\\\s*' + topLogic + '\\\\s+' + escaped, 'i'), '');
                        newText = newText.replace(new RegExp(escaped + '\\\\s+' + topLogic + '\\\\s*', 'i'), '');
                        newText = newText.replace(new RegExp(escaped, 'i'), '');
                        newText = newText.replace(/^\\s*(AND|OR)\\s+/i, '').replace(/\\s+(AND|OR)\\s*$/i, '').trim();
                        input.value = newText;
                        updateFilterTags(newText);
                        applyFilter();
                    }
                });
                tagsDiv.appendChild(tag);
            });
        }

        // ====== Graph/Notrace Precomputation ======

        // Module-level cache for graph/notrace fold-id sets
        let _graphFoldIds = {};

        // Collect all graph:/notrace: conditions from AST
        function collectGraphConditions(ast) {
            if (!ast) return [];
            if (ast.type === 'COND' && (ast.field === 'graph' || ast.field === 'notrace')) return [ast];
            const results = [];
            if (ast.left) results.push(...collectGraphConditions(ast.left));
            if (ast.right) results.push(...collectGraphConditions(ast.right));
            if (ast.operand) results.push(...collectGraphConditions(ast.operand));
            return results;
        }

        // Build funcName→[foldId] map and resolve graph/notrace targets
        function precomputeGraphSets(graphConds, allLines) {
            const result = {};
            if (graphConds.length === 0) return result;

            // Build entry map: funcName → [foldId, ...]
            const entryMap = new Map();
            for (let i = 0; i < allLines.length; i++) {
                const line = allLines[i];
                if (line.getAttribute('data-fold-type') === 'entry') {
                    const func = line.getAttribute('data-func');
                    const foldId = line.getAttribute('data-fold-id');
                    if (func && foldId) {
                        let arr = entryMap.get(func);
                        if (!arr) { arr = []; entryMap.set(func, arr); }
                        arr.push(foldId);
                    }
                }
            }

            // For each condition, find matching fold-ids
            graphConds.forEach(cond => {
                const key = cond.field + ':' + cond.value;
                if (result[key]) return; // already computed
                const matchedIds = new Set();
                let regex;
                try { regex = new RegExp(cond.value, 'i'); } catch(e) { regex = null; }
                entryMap.forEach((ids, func) => {
                    const match = regex ? regex.test(func) : func.includes(cond.value);
                    if (match) ids.forEach(id => matchedIds.add(id));
                });
                result[key] = matchedIds;
            });

            return result;
        }

        // ====== Main Filter Functions ======

        function applyFilter() {
            const filterInput = document.getElementById('filterInput');
            const text = filterInput ? filterInput.value.trim() : '';

            // Update tags display
            updateFilterTags(text);

            // Parse the expression
            const { ast, sortDir } = parseFilterExpr(text);
            const hasFilter = ast !== null;

            // Get all lines
            const allLineElems = document.querySelectorAll('.line-container');
            let visibleCount = 0;

            // Precompute graph/notrace fold-id sets
            if (hasFilter) {
                const graphConds = collectGraphConditions(ast);
                _graphFoldIds = precomputeGraphSets(graphConds, allLineElems);
            } else {
                _graphFoldIds = {};
            }

            // Single-pass: filter lines + collapse expanded content
            allLineElems.forEach(line => {
                let show = true;

                if (hasFilter) {
                    const rawLine = line.querySelector('.line-content')?.textContent || '';
                    const trimmed = rawLine.trim();
                    if (trimmed === '' || /^[-]+$/.test(trimmed)) {
                        show = false;
                    }
                }

                if (show && hasFilter) {
                    show = evalExpr(line, ast);
                }

                line.style.display = show ? '' : 'none';
                if (show) visibleCount++;

                // Collapse expanded content for hidden or all filtered lines (single pass)
                if (hasFilter && line.classList.contains('expandable')) {
                    const nextSib = line.nextElementSibling;
                    const content = nextSib && nextSib.classList.contains('expanded-content') ? nextSib : null;
                    if (content && content.style.display === 'block') {
                        content.style.display = 'none';
                        const btn = line.querySelector('.expand-btn');
                        if (btn) btn.textContent = '+';
                        line.classList.remove('selected');
                    }
                }
            });

            // Update expandable lines
            updateExpandableLines();

            // Apply duration sort if requested
            if (sortDir && visibleCount > 0) {
                const visibleLines = Array.from(allLineElems).filter(l => l.style.display !== 'none');
                visibleLines.sort((a, b) => {
                    const dA = parseFloat(a.getAttribute('data-sort-duration') || a.getAttribute('data-duration') || '0');
                    const dB = parseFloat(b.getAttribute('data-sort-duration') || b.getAttribute('data-duration') || '0');
                    return sortDir === 'desc' ? dB - dA : dA - dB;
                });
                const container = visibleLines[0]?.parentElement;
                if (container) {
                    visibleLines.forEach(l => l.remove());
                    visibleLines.forEach(l => {
                        const lastVis = Array.from(container.children).findLast(c =>
                            c.classList.contains('line-container') && c.style.display !== 'none'
                        );
                        if (lastVis) lastVis.after(l);
                        else container.insertBefore(l, container.firstChild);
                    });
                }
            }

            // Update visible lines count
            const visibleLinesContainer = document.getElementById('visibleLinesContainer');
            const summaryVisibleLines = document.getElementById('summaryVisibleLines');
            if (visibleLinesContainer && summaryVisibleLines) {
                if (visibleCount < total_lines) {
                    visibleLinesContainer.style.display = 'inline';
                    summaryVisibleLines.textContent = visibleCount;
                } else {
                    visibleLinesContainer.style.display = 'none';
                }
            }
        }

        // Clear filter
        function clearFilter() {
            const filterInput = document.getElementById('filterInput');
            if (filterInput) filterInput.value = '';

            const tagsDiv = document.getElementById('filterTags');
            if (tagsDiv) tagsDiv.innerHTML = '';

            // Close any open dropdowns
            document.querySelectorAll('.field-dropdown').forEach(d => d.remove());
            document.querySelectorAll('.field-btn.active').forEach(b => b.classList.remove('active'));

            const allLineElems = document.querySelectorAll('.line-container');
            allLineElems.forEach(line => {
                line.style.display = '';
                const lineId = line.getAttribute('data-line-id');
                if (lineId) {
                    const content = document.getElementById(lineId + '_content');
                    const btn = line.querySelector('.expand-btn');
                    if (content && btn) {
                        const isExpanded = localStorage.getItem(`expanded_${lineId}`) === 'true';
                        if (isExpanded) {
                            content.style.display = 'block';
                            btn.textContent = '-';
                            line.classList.add('selected');
                        } else {
                            content.style.display = 'none';
                            btn.textContent = '+';
                            line.classList.remove('selected');
                        }
                    }
                }
            });

            updateExpandableLines();

            const visibleLinesContainer = document.getElementById('visibleLinesContainer');
            if (visibleLinesContainer) visibleLinesContainer.style.display = 'none';
        }

        // 更新展开行列表（只包含可见的）
        function updateExpandableLines() {
            // 重新获取所有行（包括隐藏的）
            allLines = Array.from(document.querySelectorAll('.line-container'));
            expandableLines = Array.from(document.querySelectorAll('.line-container.expandable'))
                .filter(line => line.style.display !== 'none');
            expandableLineIndices = [];
            allLines.forEach((line, index) => {
                if (line.classList.contains('expandable') && line.style.display !== 'none') {
                    expandableLineIndices.push(index);
                }
            });
        }

        // 自动补全功能
        // Initialize unified filter field buttons and input
        function initFilterFields() {
            const filterInput = document.getElementById('filterInput');
            if (!filterInput) return;

            // Enter key triggers filter
            filterInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    applyFilter();
                }
            });

            // Helper: position dropdown near a button using fixed positioning, clamped to viewport
            function positionDropdown(dropdown, btn) {
                const rect = btn.getBoundingClientRect();
                // Temporarily show off-screen to measure dimensions
                dropdown.style.visibility = 'hidden';
                dropdown.style.left = '0px';
                dropdown.style.top = '0px';
                document.body.appendChild(dropdown);
                const dw = dropdown.offsetWidth;
                const dh = dropdown.offsetHeight;
                dropdown.style.visibility = '';

                const vw = window.innerWidth;
                const vh = window.innerHeight;
                const margin = 4;

                // Horizontal: align left edge to button, clamp to viewport
                let left = rect.left;
                if (left + dw > vw - margin) {
                    left = vw - dw - margin;
                }
                if (left < margin) left = margin;

                // Vertical: prefer above button, fall back to below if no room
                let top = rect.top - dh - margin;
                if (top < margin) {
                    top = rect.bottom + margin;
                }
                // Clamp bottom edge
                if (top + dh > vh - margin) {
                    top = vh - dh - margin;
                }
                if (top < margin) top = margin;

                dropdown.style.left = left + 'px';
                dropdown.style.top = top + 'px';
            }

            // Helper: close all field dropdowns
            function closeFieldDropdowns() {
                document.querySelectorAll('.field-dropdown').forEach(d => d.remove());
                document.querySelectorAll('.field-btn.active').forEach(b => b.classList.remove('active'));
            }

            // Field button click handlers
            document.querySelectorAll('.field-btn').forEach(btn => {
                btn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    const field = this.dataset.field;
                    const values = this.dataset.values ? this.dataset.values.split(',').filter(Boolean) : [];
                    const hints = {};
                    if (this.dataset.hints) {
                        this.dataset.hints.split(',').forEach(h => {
                            const [k, v] = h.split('=');
                            if (k && v) hints[k] = v;
                        });
                    }

                    // Close any existing dropdown
                    closeFieldDropdowns();

                    // Toggle active state
                    this.classList.add('active');

                    // Create dropdown (will be appended to document.body, not inside button)
                    const dropdown = document.createElement('div');
                    dropdown.className = 'field-dropdown';

                    if (field === 'params' || field === 'duration' || values.length === 0) {
                        // Text input for free-form fields
                        const inputDiv = document.createElement('div');
                        inputDiv.className = 'field-dropdown-input';
                        const placeholder = field === 'duration' ? 'e.g. >10 or <5' : 'regex pattern';
                        inputDiv.innerHTML = '<input type="text" placeholder="' + placeholder + '">';
                        dropdown.appendChild(inputDiv);

                        const actionsDiv = document.createElement('div');
                        actionsDiv.className = 'field-dropdown-actions';
                        actionsDiv.innerHTML = '<button class="cancel-btn">Cancel</button><button>Add</button>';
                        dropdown.appendChild(actionsDiv);

                        actionsDiv.querySelector('.cancel-btn').addEventListener('click', () => {
                            dropdown.remove();
                            btn.classList.remove('active');
                        });

                        actionsDiv.querySelector('button:last-child').addEventListener('click', () => {
                            const val = inputDiv.querySelector('input').value.trim();
                            if (val) {
                                const op = val.match(/^(>=|<=|>|<)/) ? val.match(/^(>=|<=|>|<)/)[1] : '';
                                const numVal = op ? val.slice(op.length) : val;
                                appendToFilter(field + ':' + (op || '') + numVal);
                            }
                            dropdown.remove();
                            btn.classList.remove('active');
                        });

                        // Enter key in text input
                        inputDiv.querySelector('input').addEventListener('keydown', (ev) => {
                            if (ev.key === 'Enter') {
                                ev.preventDefault();
                                actionsDiv.querySelector('button:last-child').click();
                            } else if (ev.key === 'Escape') {
                                dropdown.remove();
                                btn.classList.remove('active');
                            }
                        });

                        positionDropdown(dropdown, this);
                        setTimeout(() => inputDiv.querySelector('input').focus(), 50);
                    } else {
                        // Checkbox multi-select for enumerable fields
                        values.forEach(val => {
                            const item = document.createElement('label');
                            item.className = 'field-dropdown-item';
                            const hintText = hints[val] ? '<span class="hint-text">' + hints[val] + '</span>' : '';
                            const displayVal = val.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                            item.innerHTML = '<input type="checkbox" value="' + displayVal + '"> <span>' + displayVal + '</span>' + hintText;
                            dropdown.appendChild(item);
                        });

                        const actionsDiv = document.createElement('div');
                        actionsDiv.className = 'field-dropdown-actions';
                        actionsDiv.innerHTML = '<button class="cancel-btn">Cancel</button><button>OK</button>';
                        dropdown.appendChild(actionsDiv);

                        actionsDiv.querySelector('.cancel-btn').addEventListener('click', () => {
                            dropdown.remove();
                            btn.classList.remove('active');
                        });

                        actionsDiv.querySelector('button:last-child').addEventListener('click', () => {
                            const checked = Array.from(dropdown.querySelectorAll('input[type="checkbox"]:checked'))
                                .map(cb => cb.value);
                            if (checked.length > 0) {
                                if (checked.length === 1) {
                                    appendToFilter(field + ':' + checked[0]);
                                } else {
                                    const expr = '(' + checked.map(v => field + ':' + v).join(' OR ') + ')';
                                    appendToFilter(expr);
                                }
                            }
                            dropdown.remove();
                            btn.classList.remove('active');
                        });

                        positionDropdown(dropdown, this);
                    }
                });
            });

            // Helper: append condition to filter input
            function appendToFilter(expr) {
                const input = document.getElementById('filterInput');
                if (!input) return;
                const current = input.value.trim();
                if (current) {
                    input.value = current + ' AND ' + expr;
                } else {
                    input.value = expr;
                }
                updateFilterTags(input.value);
                applyFilter();
            }

            // Global click to close dropdowns
            document.addEventListener('click', function(e) {
                if (!e.target.closest('.field-btn') && !e.target.closest('.field-dropdown')) {
                    document.querySelectorAll('.field-dropdown').forEach(d => d.remove());
                    document.querySelectorAll('.field-btn.active').forEach(b => b.classList.remove('active'));
                }
            });
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

                // 移动焦点到该行，这样event.target会更新
                // 这样Enter键就能正确处理
                keyboardSelectedLine.focus();

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

            // 检查是否点击的是折叠标记
            const foldMarker = event.target.closest('.fold-marker');
            if (foldMarker) {
                // 处理折叠/展开
                const lineContainer = event.target.closest('.line-container');
                if (lineContainer) {
                    const foldId = lineContainer.getAttribute('data-fold-id');
                    const foldType = lineContainer.getAttribute('data-fold-type');
                    if (foldId && foldType) {
                        toggleFold(foldId, foldType);
                    }
                }
                return;
            }

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
        // 切换折叠状态
        // Core fold/unfold logic — does NOT write localStorage (P3 optimization)
        function _setFolded(foldId, shouldFold) {
            const isFolded = foldedBlocks[foldId] === true;
            if (shouldFold === isFolded) return; // no-op

            // P1: single DOM query for all related lines
            const lines = document.querySelectorAll('[data-fold-id="' + foldId + '"], [data-fold-ancestors~="' + foldId + '"]');
            // Find entry element from the query results (avoid extra querySelector)
            let entryEl = null;
            for (let i = 0; i < lines.length; i++) {
                if (lines[i].getAttribute('data-fold-type') === 'entry' && lines[i].getAttribute('data-fold-id') === foldId) {
                    entryEl = lines[i]; break;
                }
            }

            if (shouldFold) {
                // Collapse
                foldedBlocks[foldId] = true;
                lines.forEach(line => {
                    const type = line.getAttribute('data-fold-type');
                    const thisId = line.getAttribute('data-fold-id');
                    if (!(type === 'entry' && thisId === foldId)) {
                        line.style.display = 'none';
                    }
                });
                if (entryEl) {
                    entryEl.classList.add('folded');
                    const icon = entryEl.querySelector('.fold-icon');
                    if (icon) icon.classList.remove('expanded');
                }
                addFoldSummary(foldId);
            } else {
                // Expand
                delete foldedBlocks[foldId];
                removeFoldSummary(foldId);
                lines.forEach(line => { line.style.display = ''; });
                if (entryEl) {
                    entryEl.classList.remove('folded');
                    const icon = entryEl.querySelector('.fold-icon');
                    if (icon) icon.classList.add('expanded');
                }
                // P2: only restore child folds (not all foldedBlocks)
                _restoreChildFolds(foldId);
            }
        }

        // P2: restore only folds that are children of the given foldId
        function _restoreChildFolds(parentFoldId) {
            for (const childId of Object.keys(foldedBlocks)) {
                if (!foldedBlocks[childId]) continue;
                // Check if childId's entry has parentFoldId in its ancestors
                const childEntry = document.querySelector('[data-fold-id="' + childId + '"][data-fold-type="entry"]');
                if (!childEntry) continue;
                const ancestors = childEntry.getAttribute('data-fold-ancestors') || '';
                if (ancestors.split(' ').includes(parentFoldId)) {
                    _setFolded(childId, true);
                }
            }
        }

        // Public API: toggle a single fold block + save state
        function toggleFold(foldId, foldType) {
            const shouldFold = !foldedBlocks[foldId];
            _setFolded(foldId, shouldFold);
            lsSet('foldedBlocks', JSON.stringify(foldedBlocks));
        }

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
            const isExpanded = panel.classList.contains('show');
            if (isExpanded) {
                panel.classList.remove('show');
                lsSet('infoPanelExpanded', 'false');
            } else {
                panel.classList.add('show');
                lsSet('infoPanelExpanded', 'true');
            }
        }

        // 初始化信息面板状态
        function initInfoPanel() {
            const expanded = lsGet('infoPanelExpanded') === 'true';
            if (expanded) {
                const panel = document.getElementById('infoPanel');
                panel.classList.add('show');
            }
        }

        // 切换浮动控件（显示/隐藏），并保存状态到 localStorage
        function toggleFloatingControls() {
            const hidden = document.body.classList.toggle('hide-floating-controls');
            const icon = document.getElementById('floatingToggleIcon');
            if (hidden) {
                icon.textContent = '✕';
                lsSet('floatingControlsHidden', 'true');
            } else {
                icon.textContent = '☰';
                lsSet('floatingControlsHidden', 'false');
            }
        }

        // 初始化浮动控件的显示状态（从 localStorage 读取）
        function initFloatingControls() {
            const hidden = lsGet('floatingControlsHidden') === 'true';
            const icon = document.getElementById('floatingToggleIcon');
            if (hidden) {
                document.body.classList.add('hide-floating-controls');
                if (icon) icon.textContent = '✕';
            } else {
                document.body.classList.remove('hide-floating-controls');
                if (icon) icon.textContent = '☰';
            }
        }

        // 拖动相关的全局变量
        let isDraggingToggle = false;
        let dragStartX = 0;
        let dragStartY = 0;
        let dragStartLeft = 0;
        let dragStartTop = 0;
        let wasDragged = false;  // 标记本次操作是否有移动（拖动而非点击）

        // 点击处理器：检查是否为拖动，只在非拖动时执行 toggle
        function handleFloatingToggleClick(event) {
            // 如果拖动过程中产生了移动，则忽略本次点击
            if (wasDragged) {
                wasDragged = false;  // 重置标志
                return;
            }
            toggleFloatingControls();
        }

        // 初始化悬浮按钮的拖动功能
        function initFloatingToggleDrag() {
            const toggle = document.getElementById('floatingToggle');
            if (!toggle) return;

            // 从 localStorage 恢复之前保存的位置
            const savedPos = lsGet('floatingTogglePos');
            if (savedPos) {
                try {
                    const pos = JSON.parse(savedPos);
                    toggle.style.position = 'fixed';
                    toggle.style.top = pos.top + 'px';
                    toggle.style.left = pos.left + 'px';
                    toggle.style.right = 'auto';
                    toggle.style.bottom = 'auto';
                    toggle.style.transform = 'none';
                } catch (e) {
                    console.warn('Failed to restore toggle position:', e);
                }
            }

            // 鼠标按下：开始拖动
            toggle.addEventListener('mousedown', function(e) {
                e.preventDefault();
                isDraggingToggle = true;
                wasDragged = false;  // 初始化拖动标志
                toggle.classList.add('dragging');
                dragStartX = e.clientX;
                dragStartY = e.clientY;
                dragStartLeft = toggle.offsetLeft;
                dragStartTop = toggle.offsetTop;
                e.stopPropagation();
            });

            // 鼠标移动：更新位置
            document.addEventListener('mousemove', function(e) {
                if (!isDraggingToggle) return;
                
                const deltaX = e.clientX - dragStartX;
                const deltaY = e.clientY - dragStartY;
                
                // 移动距离超过 3px 时才认为是拖动（避免误触发）
                if (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3) {
                    wasDragged = true;
                }
                
                const newLeft = dragStartLeft + deltaX;
                const newTop = dragStartTop + deltaY;
                
                // 边界约束：防止拖出窗口
                const maxLeft = window.innerWidth - toggle.offsetWidth;
                const maxTop = window.innerHeight - toggle.offsetHeight;
                
                toggle.style.left = Math.max(0, Math.min(newLeft, maxLeft)) + 'px';
                toggle.style.top = Math.max(0, Math.min(newTop, maxTop)) + 'px';
            });

            // 鼠标释放：停止拖动，保存位置
            document.addEventListener('mouseup', function(e) {
                if (!isDraggingToggle) return;
                
                isDraggingToggle = false;
                toggle.classList.remove('dragging');
                
                // 保存位置到 localStorage
                const pos = {
                    left: toggle.offsetLeft,
                    top: toggle.offsetTop
                };
                lsSet('floatingTogglePos', JSON.stringify(pos));
            });

            // 防止文本选中干扰拖动
            toggle.addEventListener('selectstart', function(e) {
                e.preventDefault();
            });
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
        
        // 复制可见内容（纯文本模式，只复制 trace 内容）
        function copyVisibleContent(event) {
            // 获取所有可见的行（未被过滤器隐藏的）
            const allLines = document.querySelectorAll('.line-container');
            const visibleLines = Array.from(allLines).filter(line => {
                return line.style.display !== 'none' && !line.classList.contains('hidden');
            });

            // 构建文本，只复制 trace 内容（不包含行号）
            const textLines = [];
            visibleLines.forEach(line => {
                const lineContent = line.querySelector('.line-content').textContent;
                textLines.push(lineContent);
            });
            const textToCopy = textLines.join('\\n');

            // 如果没有可见行，显示提示
            if (textLines.length === 0) {
                const originalText = event.target.textContent;
                event.target.textContent = 'No visible lines';
                event.target.style.background = 'rgba(239, 68, 68, 0.8)';
                setTimeout(() => {
                    event.target.textContent = originalText;
                    event.target.style.background = '';
                }, 1000);
                return;
            }

            // 复制到剪贴板
            if (navigator.clipboard) {
                navigator.clipboard.writeText(textToCopy).then(() => {
                    // 显示成功提示
                    const originalText = event.target.textContent;
                    event.target.textContent = `Copied ${textLines.length} lines`;
                    event.target.style.background = 'rgba(34, 197, 94, 0.8)';
                    setTimeout(() => {
                        event.target.textContent = originalText;
                        event.target.style.background = '';
                    }, 1000);
                }).catch(err => {
                    console.error('Failed to copy: ', err);
                    // 降级方案
                    fallbackCopy(textToCopy, event.target, textLines.length);
                });
            } else {
                // 老浏览器降级
                fallbackCopy(textToCopy, event.target, textLines.length);
            }
        }

        // 降级复制方案
        function fallbackCopy(text, button, lineCount) {
            const textArea = document.createElement('textarea');
            textArea.value = text;
            textArea.style.position = 'fixed';
            textArea.style.top = '-1000px';
            document.body.appendChild(textArea);
            textArea.select();
            try {
                document.execCommand('copy');
                // 显示成功提示
                const originalText = button.textContent;
                button.textContent = `Copied ${lineCount} lines`;
                button.style.background = 'rgba(34, 197, 94, 0.8)';
                setTimeout(() => {
                    button.textContent = originalText;
                    button.style.background = '';
                }, 1000);
            } catch (err) {
                console.error('Fallback copy failed: ', err);
                button.textContent = 'Failed';
                setTimeout(() => {
                    button.textContent = 'Copy';
                }, 1000);
            }
            document.body.removeChild(textArea);
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

        // 页面初始化：恢复filter窗口状态
        document.addEventListener('DOMContentLoaded', function() {
            // 从localStorage读取filter窗口的显示状态
            const filterBoxVisible = lsGet('filterBoxVisible');
            const filterBox = document.getElementById('filterBox');

            if (filterBox) {
                // 如果有保存的状态，按状态显示
                if (filterBoxVisible === 'false') {
                    filterBox.style.display = 'none';
                } else {
                    // 默认显示（包括filterBoxVisible为'true'或未设置的情况）
                    filterBox.style.display = 'flex';
                }
            }

            // 恢复折叠状态
            restoreFoldedState();
        });

        // 添加键盘快捷键支持
        document.addEventListener('keydown', function(event) {
            
            // 检查是否在输入框中
            if (event.target.matches('input, textarea, select')) {
                return;
            }
            

            
            // Enter键特殊处理：在链接上按Enter时，允许打开链接；在行上按Enter时，展开/收起
            if (event.key === 'Enter' || event.keyCode === 13) {
                // 检查当前焦点是否在链接上
                const target = event.target;
                const isLink = target.tagName === 'A' || target.closest('a');

                // 如果焦点在链接上，让链接正常跳转（不阻止默认行为）
                if (isLink) {
                    // 链接会正常跳转，不需要额外处理
                    return;
                }

                // 如果焦点不在链接上，检查是否在可展开行内
                let focusedLine = null;
                let element = target;

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
                    }
                    return;
                }

                // 如果当前焦点不在行内，使用键盘选中的行
                // 但只有在有可见的可展开行时才处理
                if (keyboardSelectedLine && keyboardSelectedLine.classList.contains('expandable') &&
                    keyboardSelectedLine.style.display !== 'none') {
                    event.preventDefault();
                    const lineId = keyboardSelectedLine.getAttribute('data-line-id');
                    if (lineId) {
                        toggleExpand(lineId, event);
                    }
                    return;
                }

                // 如果没有可展开行被处理，但 keyboardSelectedLine 不可展开，尝试展开当前行
                // 但只有在该行可见时才处理
                if (keyboardSelectedLine && keyboardSelectedLine.style.display !== 'none') {
                    event.preventDefault();
                    const lineId = keyboardSelectedLine.getAttribute('data-line-id');
                    if (lineId) {
                        toggleExpand(lineId, event);
                    }
                }
            }
            
            // 按下ESC键取消选择
            if (event.key === 'Escape' || event.keyCode === 27) {
                // 1. 清除键盘选中（键盘导航）
                if (keyboardSelectedLine) {
                    keyboardSelectedLine.classList.remove('keyboard-selected');
                    keyboardSelectedLine = null;
                    keyboardSelectedIndex = -1;
                }

                // 2. 清除文本选择高亮
                if (highlightedLine) {
                    highlightedLine.classList.remove('highlighted');
                    highlightedLine = null;
                }

                // 3. 清除文本选择
                const selection = window.getSelection();
                if (selection.toString().length > 0) {
                    selection.removeAllRanges();
                }

                // 4. 移除焦点（清除Tab选中）
                if (document.activeElement && document.activeElement !== document.body) {
                    document.activeElement.blur();
                }

                // 阻止默认行为
                event.preventDefault();
            }
            
            // VIM风格导航键和方向键
            let handled = false;
            const keyLower = event.key.toLowerCase();
            
            // 处理垂直移动（j/k 和上下箭头键）- 只导航到可展开行
            // 只有在有可见的可展开行时才处理
            if (keyLower === 'j' || event.key === 'ArrowDown') {
                event.preventDefault();
                handled = true;
                if (expandableLineIndices.length > 0 && expandableLines.length > 0) {
                    navigateToExpandableLine(1); // 向下导航
                }
            } else if (keyLower === 'k' || event.key === 'ArrowUp') {
                event.preventDefault();
                handled = true;
                if (expandableLineIndices.length > 0 && expandableLines.length > 0) {
                    navigateToExpandableLine(-1); // 向上导航
                }
            }
            
            // 处理水平滚动（h/l 键）
            // 支持大小写，Shift+h 和 Shift+l 也有效
            if (keyLower === 'h') {
                event.preventDefault();
                handled = true;
                // 向左滚动容器
                var container = document.querySelector('.container');
                if (container) {
                    container.scrollBy({ left: -100, behavior: 'smooth' });
                }
            } else if (keyLower === 'l') {
                event.preventDefault();
                handled = true;
                // 向右滚动容器
                var container = document.querySelector('.container');
                if (container) {
                    container.scrollBy({ left: 100, behavior: 'smooth' });
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

                // P1: Number keys 0-9 for fold depth control
                if (event.key >= '0' && event.key <= '9' && !event.ctrlKey && !event.metaKey && !event.altKey) {
                    event.preventDefault();
                    const depth = parseInt(event.key);
                    if (depth === 0) {
                        unfoldAllBlocks();
                    } else {
                        foldToDepth(depth);
                    }
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
            const savedState = lsGet('viewState');
            if (savedState) {
                const state = JSON.parse(savedState);
                window.scrollTo(0, state.scrollPosition);
            }
        }
        
        // 全局变量跟踪异步任务
        let expandCollapseTask = null;
        let isTaskRunning = false;
        let isWaitingForStop = false;

        // 禁用/启用两个按钮
        function setButtonsDisabled(disabled) {
            const expandBtn = document.querySelector('button[onclick="expandAll()"]');
            const collapseBtn = document.querySelector('button[onclick="collapseAll()"]');

            if (expandBtn) expandBtn.disabled = disabled;
            if (collapseBtn) collapseBtn.disabled = disabled;
        }

        // 等待任务完全停止
        function waitForTaskStop() {
            return new Promise((resolve) => {
                if (!isTaskRunning && !expandCollapseTask) {
                    resolve();
                    return;
                }

                const checkInterval = setInterval(() => {
                    if (!isTaskRunning && !expandCollapseTask) {
                        clearInterval(checkInterval);
                        resolve();
                    }
                }, 10);
            });
        }

        // 展开所有可展开行（异步执行，支持取消）
        async function expandAll() {
            const btn = event.target;

            // 如果正在等待停止，忽略点击
            if (isWaitingForStop) {
                return;
            }

            // 保存原始文本
            let originalText = btn.getAttribute('data-original-text');
            if (!originalText) {
                originalText = btn.textContent;
                btn.setAttribute('data-original-text', originalText);
            }

            // 如果有任务在运行，停止它并等待，然后返回（不发起新任务）
            if (expandCollapseTask) {
                isWaitingForStop = true;
                setButtonsDisabled(true);
                expandCollapseTask.cancel();
                await waitForTaskStop();
                isWaitingForStop = false;
                setButtonsDisabled(false);

                // 恢复按钮文本
                btn.textContent = originalText;
                return;
            }

            // 没有任务在运行，开始新的 Expand 任务 - 只对可见的行操作
            const expandableLines = Array.from(document.querySelectorAll('.line-container.expandable'))
                .filter(container => container.style.display !== 'none');
            const total = expandableLines.length;

            if (total === 0) return;

            // 显示进度
            btn.textContent = `Expanding... 000%`;
            isTaskRunning = true;

            let processed = 0;
            const batchSize = 50;
            let cancelled = false;

            // 创建任务对象
            expandCollapseTask = {
                type: 'expand',
                cancel: function() {
                    cancelled = true;
                    btn.textContent = originalText;
                    isTaskRunning = false;
                    expandCollapseTask = null;
                }
            };

            function processBatch() {
                if (cancelled) {
                    isTaskRunning = false;
                    expandCollapseTask = null;
                    return;
                }

                const batch = expandableLines.slice(processed, processed + batchSize);

                batch.forEach(container => {
                    if (cancelled) return;

                    const lineId = container.getAttribute('data-line-id');
                    const content = document.getElementById(lineId + '_content');
                    const expandBtn = container.querySelector('.expand-btn');
                    const isVisible = container.style.display !== 'none';

                    if (content && isVisible) {
                        content.style.display = 'block';
                        if (expandBtn) expandBtn.textContent = '-';
                        container.classList.add('selected');
                        saveExpandedState(lineId, true);
                    }
                });

                processed += batch.length;

                // 更新进度（固定长度百分比）
                const percent = Math.round((processed / total) * 100);
                const percentStr = percent.toString().padStart(3, ' ');
                btn.textContent = `Expanding... ${percentStr}%`;

                if (processed < total && !cancelled) {
                    requestAnimationFrame(processBatch);
                } else {
                    if (!cancelled) {
                        btn.textContent = originalText;
                        isTaskRunning = false;
                        expandCollapseTask = null;
                    }
                }
            }

            requestAnimationFrame(processBatch);
        }

        // 收起所有可展开行（异步执行，支持取消）
        async function collapseAll() {
            const btn = event.target;

            // 如果正在等待停止，忽略点击
            if (isWaitingForStop) {
                return;
            }

            // 保存原始文本
            let originalText = btn.getAttribute('data-original-text');
            if (!originalText) {
                originalText = btn.textContent;
                btn.setAttribute('data-original-text', originalText);
            }

            // 如果有任务在运行，停止它并等待，然后返回（不发起新任务）
            if (expandCollapseTask) {
                isWaitingForStop = true;
                setButtonsDisabled(true);
                expandCollapseTask.cancel();
                await waitForTaskStop();
                isWaitingForStop = false;
                setButtonsDisabled(false);

                // 恢复按钮文本
                btn.textContent = originalText;

                // 清除高亮
                if (highlightedLine) {
                    highlightedLine.classList.remove('highlighted');
                    highlightedLine = null;
                }
                return;
            }

            // 没有任务在运行，开始新的 Collapse 任务 - 只对可见的行操作
            const expandableLines = Array.from(document.querySelectorAll('.line-container.expandable'))
                .filter(container => container.style.display !== 'none');
            const total = expandableLines.length;

            if (total === 0) {
                // 清除高亮
                if (highlightedLine) {
                    highlightedLine.classList.remove('highlighted');
                    highlightedLine = null;
                }
                return;
            }

            // 显示进度
            btn.textContent = `Collapsing... 000%`;
            isTaskRunning = true;

            let processed = 0;
            const batchSize = 50;
            let cancelled = false;

            // 创建任务对象
            expandCollapseTask = {
                type: 'collapse',
                cancel: function() {
                    cancelled = true;
                    btn.textContent = originalText;
                    isTaskRunning = false;
                    expandCollapseTask = null;
                }
            };

            function processBatch() {
                if (cancelled) {
                    isTaskRunning = false;
                    expandCollapseTask = null;
                    return;
                }

                const batch = expandableLines.slice(processed, processed + batchSize);

                batch.forEach(container => {
                    if (cancelled) return;

                    const lineId = container.getAttribute('data-line-id');
                    const content = document.getElementById(lineId + '_content');
                    const expandBtn = container.querySelector('.expand-btn');
                    const isVisible = container.style.display !== 'none';

                    if (content && isVisible) {
                        content.style.display = 'none';
                        if (expandBtn) expandBtn.textContent = '+';
                        container.classList.remove('selected');
                        saveExpandedState(lineId, false);
                    }
                });

                processed += batch.length;

                // 更新进度（固定长度百分比）
                const percent = Math.round((processed / total) * 100);
                const percentStr = percent.toString().padStart(3, ' ');
                btn.textContent = `Collapsing... ${percentStr}%`;

                if (processed < total && !cancelled) {
                    requestAnimationFrame(processBatch);
                } else {
                    if (!cancelled) {
                        btn.textContent = originalText;
                        isTaskRunning = false;
                        expandCollapseTask = null;

                        // 清除高亮
                        if (highlightedLine) {
                            highlightedLine.classList.remove('highlighted');
                            highlightedLine = null;
                        }
                    }
                }
            }

            requestAnimationFrame(processBatch);
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
        // ============================================================
        // P0: Duration Heatmap
        // ============================================================
        let heatmapEnabled = lsGet('heatmapEnabled') !== 'false';

        // Heatmap: data-heat is pre-computed in Python based on ftrace prefix markers.
        // This function only builds the legend and applies visibility.
        function applyDurationHeatmap() {
            const legend = document.getElementById('heatmapLegend');
            if (legend) {
                const levels = [
                    { color: 'rgba(76,175,80,0.35)',  label: '≤10us' },
                    { color: 'rgba(139,195,74,0.4)',  label: '>10us' },
                    { color: 'rgba(255,235,59,0.5)',  label: '>100us' },
                    { color: 'rgba(255,193,7,0.55)',  label: '>1ms' },
                    { color: 'rgba(255,152,0,0.6)',   label: '>10ms' },
                    { color: 'rgba(244,67,54,0.65)',  label: '>100ms' },
                    { color: 'rgba(183,28,28,0.7)',   label: '>1s' }
                ];
                legend.innerHTML = levels.map(l =>
                    '<span class="heatmap-legend-item" style="background:' + l.color + '"></span>' +
                    '<span class="heatmap-legend-label">' + l.label + '</span>'
                ).join('');
            }
            updateHeatmapVisibility();
        }

        function updateHeatmapVisibility() {
            const btn = document.getElementById('heatmapBtn');
            if (btn) btn.textContent = heatmapEnabled ? 'Heatmap ●' : 'Heatmap ○';
            const legend = document.getElementById('heatmapLegend');
            if (legend) legend.classList.toggle('visible', heatmapEnabled);
            // Toggle a class on body to control heatmap visibility via CSS
            if (heatmapEnabled) {
                document.body.classList.add('heatmap-on');
                document.body.classList.remove('heatmap-off');
            } else {
                document.body.classList.remove('heatmap-on');
                document.body.classList.add('heatmap-off');
            }
        }

        function toggleHeatmap() {
            heatmapEnabled = !heatmapEnabled;
            lsSet('heatmapEnabled', heatmapEnabled);
            updateHeatmapVisibility();
        }

        // ============================================================
        // P1: Fold to Depth
        // ============================================================
        // P3: batch operations — write localStorage once at the end
        function foldToDepth(maxDepth) {
            const entries = document.querySelectorAll('[data-fold-type="entry"]');
            entries.forEach(entry => {
                const depth = parseInt(entry.getAttribute('data-call-depth'));
                if (isNaN(depth)) return;
                const foldId = entry.getAttribute('data-fold-id');
                if (!foldId) return;
                _setFolded(foldId, depth >= maxDepth);
            });
            lsSet('foldedBlocks', JSON.stringify(foldedBlocks));
        }

        function unfoldAllBlocks() {
            const foldIds = Object.keys(foldedBlocks);
            foldIds.forEach(foldId => _setFolded(foldId, false));
            lsSet('foldedBlocks', JSON.stringify(foldedBlocks));
        }

        // F1: Recursive fold — fold/unfold block and all its child blocks
        function toggleFoldRecursive(foldId) {
            const shouldFold = !foldedBlocks[foldId];
            _setFolded(foldId, shouldFold);
            // Find all child entry blocks whose ancestors include this foldId
            const childEntries = document.querySelectorAll('[data-fold-type="entry"][data-fold-ancestors~="' + foldId + '"]');
            childEntries.forEach(child => {
                const childId = child.getAttribute('data-fold-id');
                if (childId) _setFolded(childId, shouldFold);
            });
            lsSet('foldedBlocks', JSON.stringify(foldedBlocks));
        }

        // ============================================================
        // P2: Fold Summary
        // ============================================================
        function addFoldSummary(foldId) {
            let entryEl = null;
            try {
                entryEl = document.querySelector('[data-fold-id="' + foldId + '"][data-fold-type="entry"]');
            } catch(e) { return; }
            if (!entryEl) return;

            // Remove existing summary if any
            const existing = entryEl.querySelector('.fold-summary');
            if (existing) existing.remove();

            // Find all inner/exit lines belonging to this fold
            const innerLines = document.querySelectorAll('[data-fold-id="' + foldId + '"], [data-fold-ancestors~="' + foldId + '"]');

            let childCalls = 0;
            let totalDuration = 0;
            let hasDuration = false;
            let retValue = null;

            innerLines.forEach(line => {
                const thisId = line.getAttribute('data-fold-id');
                const thisType = line.getAttribute('data-fold-type');

                // Count direct child entry calls (fold-ancestors contains our foldId)
                if (thisType === 'entry' && thisId !== foldId) {
                    childCalls++;
                }

                // Get return value from exit line of this fold
                if (thisType === 'exit' && thisId === foldId) {
                    const ret = line.getAttribute('data-ret');
                    if (ret) retValue = ret;
                    // Get duration from exit line
                    const dur = line.getAttribute('data-sort-duration') || line.getAttribute('data-duration');
                    if (dur) {
                        totalDuration = parseFloat(dur);
                        hasDuration = true;
                    }
                }
            });

            // If no duration from exit, try from entry itself
            if (!hasDuration) {
                const dur = entryEl.getAttribute('data-sort-duration') || entryEl.getAttribute('data-duration');
                if (dur) {
                    totalDuration = parseFloat(dur);
                    hasDuration = true;
                }
            }

            // Build summary text
            const parts = [];
            if (childCalls > 0) {
                parts.push('<span class="summary-count">' + childCalls + ' call' + (childCalls > 1 ? 's' : '') + '</span>');
            }
            if (hasDuration && totalDuration > 0) {
                let timeStr;
                if (totalDuration >= 1000000) timeStr = (totalDuration / 1000000).toFixed(1) + ' s';
                else if (totalDuration >= 1000) timeStr = (totalDuration / 1000).toFixed(1) + ' ms';
                else timeStr = totalDuration.toFixed(1) + ' us';
                parts.push('<span class="summary-time">' + timeStr + '</span>');
            }
            if (retValue) {
                // Look up error code name from the ERROR_CODE_MAP if embedded
                let retDisplay = 'ret=' + retValue;
                const errSpan = entryEl.closest('.line-container');
                // Try to find error name from sibling exit line
                const exitEl = document.querySelector('[data-fold-id="' + foldId + '"][data-fold-type="exit"]');
                if (exitEl) {
                    const exitContent = exitEl.querySelector('.line-content');
                    if (exitContent) {
                        const errMatch = exitContent.textContent.match(/\/\*\s*(-?\d+\s*=\s*\w+)\s*\*\//);
                        if (errMatch) retDisplay = errMatch[1].trim();
                    }
                }
                parts.push('<span class="summary-ret">' + retDisplay + '</span>');
            }

            if (parts.length > 0) {
                const summary = document.createElement('span');
                summary.className = 'fold-summary';
                summary.innerHTML = ' [' + parts.join(', ') + ']';
                const lineContent = entryEl.querySelector('.line-content');
                if (lineContent) lineContent.appendChild(summary);
            }
        }

        function removeFoldSummary(foldId) {
            let entryEl = null;
            try {
                entryEl = document.querySelector('[data-fold-id="' + foldId + '"][data-fold-type="entry"]');
            } catch(e) { return; }
            if (!entryEl) return;
            const summary = entryEl.querySelector('.fold-summary');
            if (summary) summary.remove();
        }

        window.addEventListener('load', function() {
            // 恢复主题
            document.documentElement.setAttribute('data-theme', currentTheme);

            // 初始化总行数
            const linesSpan = document.querySelector('.summary span');
            if (linesSpan) {
                const linesText = linesSpan.textContent;
                const match = linesText.match(/Lines: (\d+)/);
                if (match) {
                    total_lines = parseInt(match[1]);
                }
            }

            // 恢复展开状态
            restoreExpandedState();

            // 移除自动滚动，保持当前视图位置
            // restoreViewState(); // 注释掉，避免自动滚动

            // 初始化信息面板
            initInfoPanel();

            // 初始化浮动控件显示状态
            if (typeof initFloatingControls === 'function') {
                try { initFloatingControls(); } catch (e) { console.warn('initFloatingControls failed', e); }
            }

            // 初始化浮动按钮的拖动功能
            if (typeof initFloatingToggleDrag === 'function') {
                try { initFloatingToggleDrag(); } catch (e) { console.warn('initFloatingToggleDrag failed', e); }
            }

            // 监听滚动事件
            window.addEventListener('scroll', updateProgressBar);

            // 初始化键盘导航
            initKeyboardNavigation();

            // 初始化统一过滤器（如果过滤框存在）
            if (document.getElementById('filterInput')) {
                initFilterFields();
            }

            // 处理锚点导航
            handleAnchorNavigation();

            // P0: Apply duration heatmap
            applyDurationHeatmap();

            // P2: Add summaries for initially folded blocks
            Object.keys(foldedBlocks).forEach(foldId => {
                addFoldSummary(foldId);
            });
        });
        
        // 监听hash变化，支持动态锚点导航
        window.addEventListener('hashchange', function() {
            handleAnchorNavigation();
        });
    </script>
"""

    def _generate_css(self):
        """Generate all CSS styles."""
        return '''\
        :root {
            --bg-color: #f5f5f5;
            --container-bg: white;
            --container-bg-hover: #f3f4f6;
            --text-color: #333;
            --text-primary: #333;
            --text-secondary: #999;
            --text-muted: #888;
            --bg-secondary: #f0f0f0;
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
            --guide-color: rgba(76, 175, 80, 0.35);
        }
        [data-theme="dark"] {
            --bg-color: #1a1a1a;
            --container-bg: #2d2d2d;
            --container-bg-hover: #3a3a3a;
            --text-color: #e0e0e0;
            --text-primary: #e0e0e0;
            --text-secondary: #777;
            --text-muted: #888;
            --bg-secondary: #363636;
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
            --guide-color: rgba(102, 187, 106, 0.35);
        }
        body {
            font-family: 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', 'Courier New', monospace;
            background-color: var(--bg-color);
            padding: 8px;
            line-height: 1.5;
            margin: 0;
            color: var(--text-color);
            transition: background-color 0.3s, color 0.3s;
        }
        .container {
            background: var(--container-bg);
            padding: 4px 4px 4px 4px;
            border-radius: 4px;
            box-shadow: none;
            transition: background-color 0.3s;
            width: fit-content;
            min-width: 100%;
        }
        /* Compact single-line page header */
        .page-header {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 5px 8px;
            margin-bottom: 4px;
            border-bottom: 1px solid var(--border-color);
            font-size: 12px;
            color: var(--summary-text);
            flex-wrap: wrap;
        }
        .page-header .title-main {
            font-size: 13px;
            font-weight: 700;
            background: linear-gradient(135deg, var(--btn-primary), var(--link-color));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: 0.5px;
            margin-right: 2px;
        }
        .header-version {
            font-size: 11px;
            color: var(--summary-text);
            opacity: 0.7;
        }
        .header-sep {
            color: var(--border-color);
            margin: 0 1px;
        }
        .header-stats {
            flex: 1;
            display: flex;
            align-items: center;
            gap: 4px;
            flex-wrap: wrap;
        }
        .header-actions {
            display: flex;
            gap: 4px;
            margin-left: auto;
        }
        .header-btn {
            padding: 2px 8px;
            background: transparent;
            color: var(--summary-text);
            border: 1px solid var(--border-color);
            border-radius: 3px;
            cursor: pointer;
            font-size: 11px;
            transition: background-color 0.15s, color 0.15s;
        }
        .header-btn:hover {
            background: var(--line-hover);
            color: var(--text-color);
        }
        .line-container {
            display: flex;
            align-items: center;
            padding: 1px 2px;
            border-radius: 2px;
            white-space: pre;
            font-family: 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', 'Courier New', monospace;
            font-size: 13px;
            cursor: default;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
            user-select: none;
        }
        .line-container.expandable {
            cursor: pointer;
        }
        .line-container.expandable:hover {
            background-color: var(--line-hover);
        }
        .line-container.expandable.selected {
            background-color: var(--line-selected);
        }
        .line-container.expandable.highlighted {
            background-color: var(--line-highlighted);
        }
        .line-container.expandable.keyboard-selected {
            background-color: var(--line-keyboard-select);
            outline: 2px solid var(--btn-primary);
        }
        .line-number {
            display: inline-block;
            min-width: 3ch;
            width: auto;
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
        }
        .line-number:hover {
            background-color: var(--line-hover);
        }
        .line-content {
            flex-grow: 1;
            padding: 2px 0;
            -webkit-user-select: text;
            -moz-user-select: text;
            -ms-user-select: text;
            user-select: text;
            white-space: pre;
            font-family: 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', 'Courier New', monospace;
        }
        .fold-marker {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            position: relative;
            width: 20px;
            height: 20px;
            color: var(--btn-primary);
            font-weight: bold;
            cursor: pointer;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
            user-select: none;
            flex-shrink: 0;
        }
        .fold-marker:hover {
            color: var(--btn-primary-hover);
        }
        .fold-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 20px;
            height: 20px;
            font-size: 11px;
            border-radius: 3px;
            transition: transform 0.15s ease, background-color 0.1s;
            transform: rotate(0deg);
        }
        .fold-icon:hover {
            background-color: rgba(0, 0, 0, 0.08);
        }
        [data-theme="dark"] .fold-icon:hover {
            background-color: rgba(255, 255, 255, 0.1);
        }
        .fold-icon.expanded {
            transform: rotate(90deg);
        }
        /* Fold guide lines — use align-self:stretch so fold-marker spans full line height */
        .fold-guide {
            display: inline-block;
            width: 20px;
            height: 20px;
        }
        /* Fold-marker stretches to full line-container height for continuous guide lines */
        .fold-inner .fold-marker,
        .fold-exit .fold-marker {
            align-self: stretch;
            height: auto;
        }
        /* Inner lines: full-height vertical bar */
        .fold-inner .fold-guide::after {
            content: '';
            position: absolute;
            left: 50%;
            top: 0;
            bottom: 0;
            width: 2px;
            transform: translateX(-50%);
            background: var(--guide-color);
            pointer-events: none;
        }
        /* Exit line: top-half bar + horizontal tick (└ shape) */
        .fold-exit .fold-guide::after {
            content: '';
            position: absolute;
            left: 50%;
            top: 0;
            height: 50%;
            width: 2px;
            transform: translateX(-50%);
            background: var(--guide-color);
            pointer-events: none;
        }
        .fold-exit .fold-guide::before {
            content: '';
            position: absolute;
            left: 50%;
            top: 50%;
            width: 8px;
            height: 2px;
            background: var(--guide-color);
            pointer-events: none;
        }
        /* Entry line (expanded): downward connector from icon center to bottom edge */
        .fold-entry:not(.folded) .fold-marker {
            align-self: stretch;
            height: auto;
        }
        .fold-entry:not(.folded) .fold-marker::after {
            content: '';
            position: absolute;
            left: 50%;
            top: 65%;
            bottom: 0;
            width: 2px;
            transform: translateX(-50%);
            background: var(--guide-color);
            pointer-events: none;
        }
        .fold-entry {
            background-color: rgba(76, 175, 80, 0.05);
        }
        [data-theme="dark"] .fold-entry {
            background-color: rgba(102, 187, 106, 0.08);
        }
        /* 折叠时的高亮样式：更醒目的背景与图标颜色 */
        .fold-entry.folded {
            background-color: rgba(244, 67, 54, 0.12);
            color: var(--text-muted);
        }
        [data-theme="dark"] .fold-entry.folded {
            background-color: rgba(211, 47, 47, 0.18);
            color: var(--text-muted);
        }
        .fold-entry.folded .fold-icon {
            color: var(--btn-danger);
        }
        [data-theme="dark"] .fold-entry.folded .fold-icon {
            color: var(--btn-danger);
        }
        .fold-exit {
            background-color: rgba(244, 67, 54, 0.05);
        }
        [data-theme="dark"] .fold-exit {
            background-color: rgba(239, 83, 80, 0.08);
        }
        .fold-inner {
            background-color: rgba(33, 150, 243, 0.03);
        }
        [data-theme="dark"] .fold-inner {
            background-color: rgba(33, 150, 243, 0.05);
        }
        /* Duration heatmap colors — based on ftrace prefix markers */
        /* 1: ≤10us(space)  2: >10us(+)  3: >100us(!)  4: >1ms(#)  5: >10ms(*)  6: >100ms(@)  7: >1s($) */
        .line-container[data-heat="1"] { background-color: rgba(76, 175, 80, 0.06); }   /* green - fast */
        .line-container[data-heat="2"] { background-color: rgba(139, 195, 74, 0.08); }  /* light green */
        .line-container[data-heat="3"] { background-color: rgba(255, 235, 59, 0.10); }  /* yellow */
        .line-container[data-heat="4"] { background-color: rgba(255, 193, 7, 0.12); }   /* amber */
        .line-container[data-heat="5"] { background-color: rgba(255, 152, 0, 0.15); }   /* orange */
        .line-container[data-heat="6"] { background-color: rgba(244, 67, 54, 0.18); }   /* red */
        .line-container[data-heat="7"] { background-color: rgba(183, 28, 28, 0.22); }   /* dark red - slowest */
        [data-theme="dark"] .line-container[data-heat="1"] { background-color: rgba(76, 175, 80, 0.10); }
        [data-theme="dark"] .line-container[data-heat="2"] { background-color: rgba(139, 195, 74, 0.12); }
        [data-theme="dark"] .line-container[data-heat="3"] { background-color: rgba(255, 235, 59, 0.14); }
        [data-theme="dark"] .line-container[data-heat="4"] { background-color: rgba(255, 193, 7, 0.16); }
        [data-theme="dark"] .line-container[data-heat="5"] { background-color: rgba(255, 152, 0, 0.20); }
        [data-theme="dark"] .line-container[data-heat="6"] { background-color: rgba(244, 67, 54, 0.24); }
        [data-theme="dark"] .line-container[data-heat="7"] { background-color: rgba(183, 28, 28, 0.28); }
        /* Hide heatmap colors when disabled */
        .heatmap-off .line-container[data-heat] {
            background-color: transparent !important;
        }
        /* Restore fold entry/exit/inner colors even when heatmap is off */
        .heatmap-off .fold-entry[data-heat] { background-color: rgba(76, 175, 80, 0.05) !important; }
        .heatmap-off .fold-exit[data-heat] { background-color: rgba(244, 67, 54, 0.05) !important; }
        .heatmap-off .fold-inner[data-heat] { background-color: rgba(33, 150, 243, 0.03) !important; }
        .heatmap-off .fold-entry.folded[data-heat] { background-color: rgba(244, 67, 54, 0.12) !important; }
        [data-theme="dark"] .heatmap-off .fold-entry[data-heat] { background-color: rgba(102, 187, 106, 0.08) !important; }
        [data-theme="dark"] .heatmap-off .fold-exit[data-heat] { background-color: rgba(239, 83, 80, 0.08) !important; }
        [data-theme="dark"] .heatmap-off .fold-inner[data-heat] { background-color: rgba(33, 150, 243, 0.05) !important; }
        [data-theme="dark"] .heatmap-off .fold-entry.folded[data-heat] { background-color: rgba(211, 47, 47, 0.18) !important; }
        /* Fold summary (P2) */
        .fold-summary {
            color: var(--summary-text);
            font-size: 0.85em;
            margin-left: 8px;
            opacity: 0.8;
        }
        .fold-summary .summary-count { color: #2196F3; }
        .fold-summary .summary-time { color: #FF9800; }
        .fold-summary .summary-ret { color: #f44336; }
        [data-theme="dark"] .fold-summary .summary-count { color: #64B5F6; }
        [data-theme="dark"] .fold-summary .summary-time { color: #FFB74D; }
        [data-theme="dark"] .fold-summary .summary-ret { color: #EF5350; }
        /* Heatmap legend */
        .heatmap-legend {
            display: none;
            font-size: 11px;
            gap: 2px;
            align-items: center;
            color: var(--text-color);
        }
        .heatmap-legend.visible { display: inline-flex; }
        .heatmap-legend-item {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 2px;
            vertical-align: middle;
        }
        .heatmap-legend-label {
            margin: 0 4px 0 1px;
            white-space: nowrap;
        }
        .func-name-link {
            color: var(--link-color);
            text-decoration: none;
            cursor: pointer;
        }
        .func-name-link:hover {
            text-decoration: underline;
        }
        .expand-btn {
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
            margin-top: 5px;  /* Center align with line content */
        }
        .expand-btn:hover {
            opacity: 1;
            background-color: var(--btn-primary-hover);
        }
        .expanded-content {
            display: none;
            margin-left: 40px;
            padding: 6px 8px;
            background-color: var(--expanded-bg);
            border-left: 2px solid var(--btn-primary);
            border-radius: 0;
            white-space: pre;
            font-family: 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', 'Courier New', monospace;
            font-size: 12px;
        }
        .location-link {
            display: block;
            padding: 3px 5px;
            color: var(--link-color);
            text-decoration: none;
            margin: 2px 0;
            border-radius: 3px;
            white-space: pre;
        }
        .location-link:hover {
            background-color: var(--line-hover);
            text-decoration: underline;
        }
        .source-line {
            display: block;
            padding: 2px 0;
            white-space: pre;
            font-family: 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', 'Courier New', monospace;
            font-size: 12px;
        }
        .current-line {
            background-color: var(--line-highlighted);
        }
        .info-panel {
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
        }
        .info-panel.show {
            display: block;
        }
        [data-theme="dark"] .info-panel {
            background-color: rgba(255, 255, 255, 0.03);
        }
        .info-section-title {
            font-weight: 600;
            color: var(--text-color);
            margin-bottom: 8px;
            font-size: 11px;
        }
        .info-section-divider {
            border-top: 1px solid var(--border-color);
            margin: 8px 0;
        }
        .info-item {
            display: flex;
            margin-bottom: 6px;
            font-size: 11px;
            line-height: 1.5;
        }
        .info-item:last-child {
            margin-bottom: 0;
        }
        .info-label {
            min-width: 100px;
            font-weight: 600;
            color: var(--summary-text);
            margin-right: 12px;
        }
        .info-value {
            flex: 1;
            color: var(--text-color);
            word-break: break-all;
        }
        /* Clear button - gray with trash icon */
        .clear-btn {
            background-color: rgba(107, 114, 128, 0.7);
            color: white;
            padding: 8px 10px;
            font-size: 14px;
        }
        .clear-btn:hover {
            background-color: rgba(75, 85, 99, 0.8);
        }

        /* Control bar - horizontal toolbar */
        /* .controls 保持悬浮在右下角，内含水平工具栏 */
        .controls {
            position: fixed;
            bottom: 16px;
            right: 16px;
            z-index: 1000;
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 6px;
        }
        .toolbar {
            display: flex;
            gap: 4px;
            align-items: center;
            background: var(--container-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 5px 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
            flex-wrap: wrap;
        }
        [data-theme="dark"] .toolbar {
            box-shadow: 0 2px 8px rgba(0,0,0,0.4);
        }
        .btn-group {
            display: inline-flex;
            gap: 3px;
        }
        .btn-group + .btn-group {
            margin-left: 3px;
            padding-left: 6px;
            border-left: 1px solid var(--border-color);
        }

        /* Filter box styles */
        .filter-box {
            display: flex;
            flex-direction: column;
            gap: 6px;
            padding: 8px;
            background: var(--container-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        }
        [data-theme="dark"] .filter-box {
            box-shadow: 0 2px 8px rgba(0,0,0,0.4);
        }
        .filter-input-row {
            display: flex;
            gap: 6px;
            align-items: center;
        }
        .filter-input-row input {
            flex: 1;
            padding: 6px 8px;
            border: 1px solid var(--border-color);
            border-radius: 3px;
            background: rgba(200, 200, 200, 0.3);
            color: var(--text-primary);
            font-size: 12px;
            min-width: 200px;
        }
        [data-theme="dark"] .filter-input-row input {
            background: rgba(100, 100, 100, 0.3);
        }
        .filter-input-row input:focus {
            outline: none;
            border-color: var(--btn-primary);
        }
        .filter-input-row input::placeholder {
            color: var(--text-secondary);
        }
        /* Filter tags */
        .filter-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            min-height: 0;
        }
        .filter-tags:empty {
            display: none;
        }
        .filter-tag {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            background: var(--btn-primary);
            color: white;
            cursor: default;
            white-space: nowrap;
        }
        .filter-tag .tag-remove {
            cursor: pointer;
            font-size: 13px;
            line-height: 1;
            opacity: 0.8;
        }
        .filter-tag .tag-remove:hover {
            opacity: 1;
        }
        .filter-tag-logic {
            background: transparent;
            color: var(--text-secondary);
            font-weight: bold;
            font-size: 11px;
            padding: 2px 4px;
            cursor: pointer;
            border: 1px dashed var(--border-color);
            border-radius: 4px;
        }
        .filter-tag-logic:hover {
            background: var(--border-color);
            color: var(--text-primary);
        }
        /* Field selector buttons */
        .filter-fields {
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
        }
        .field-btn {
            padding: 2px 8px;
            font-size: 11px;
            border: 1px solid var(--border-color);
            border-radius: 3px;
            background: transparent;
            color: var(--text-secondary);
            cursor: pointer;
            position: relative;
        }
        .field-btn:hover {
            background: var(--border-color);
            color: var(--text-primary);
        }
        .field-btn.active {
            background: var(--btn-primary);
            color: white;
            border-color: var(--btn-primary);
        }
        /* Field dropdown */
        .field-dropdown {
            position: fixed;
            min-width: 140px;
            max-height: 200px;
            overflow-y: auto;
            background: var(--container-bg);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            box-shadow: 0 -2px 8px rgba(0,0,0,0.2);
            z-index: 3000;
            padding: 4px 0;
        }
        [data-theme="dark"] .field-dropdown {
            box-shadow: 0 -2px 8px rgba(0,0,0,0.5);
        }
        .field-dropdown-item {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 4px 8px;
            font-size: 11px;
            cursor: pointer;
            color: var(--text-primary);
        }
        .field-dropdown-item:hover {
            background: rgba(180, 180, 180, 0.3);
        }
        .field-dropdown-item input[type="checkbox"] {
            margin: 0;
            cursor: pointer;
        }
        .field-dropdown-item .hint-text {
            color: var(--text-secondary);
            font-size: 10px;
            margin-left: auto;
        }
        .field-dropdown-actions {
            display: flex;
            gap: 4px;
            padding: 4px 8px;
            border-top: 1px solid var(--border-color);
            margin-top: 2px;
        }
        .field-dropdown-actions button {
            flex: 1;
            padding: 2px 6px;
            font-size: 10px;
            border: 1px solid var(--border-color);
            border-radius: 3px;
            background: var(--btn-primary);
            color: white;
            cursor: pointer;
        }
        .field-dropdown-actions button.cancel-btn {
            background: transparent;
            color: var(--text-secondary);
        }
        /* Text input for dropdown (params, duration) */
        .field-dropdown-input {
            display: flex;
            gap: 4px;
            padding: 4px 8px;
        }
        .field-dropdown-input input {
            flex: 1;
            padding: 3px 6px;
            font-size: 11px;
            border: 1px solid var(--border-color);
            border-radius: 3px;
            background: rgba(200, 200, 200, 0.3);
            color: var(--text-primary);
        }
        [data-theme="dark"] .field-dropdown-input input {
            background: rgba(100, 100, 100, 0.3);
        }

        /* Filter help button */
        .filter-help-btn {
            width: 26px;
            height: 26px;
            padding: 0;
            font-size: 14px;
            font-weight: bold;
            line-height: 26px;
            text-align: center;
            border-radius: 50%;
            background: var(--border-color);
            color: var(--text-secondary);
            flex-shrink: 0;
        }
        .filter-help-btn:hover {
            background: var(--btn-primary);
            color: white;
        }
        /* Filter syntax help panel */
        .filter-syntax-help {
            display: none;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            background: var(--container-bg);
            overflow: hidden;
        }
        .filter-syntax-help.visible {
            display: block;
        }
        .syntax-help-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 4px 10px;
            background: var(--border-color);
            font-size: 12px;
            font-weight: bold;
            color: var(--text-primary);
        }
        .syntax-help-close {
            cursor: pointer;
            font-size: 16px;
            line-height: 1;
            opacity: 0.6;
        }
        .syntax-help-close:hover {
            opacity: 1;
        }
        .syntax-help-body {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 6px;
            padding: 8px 10px;
        }
        .syntax-section {
            font-size: 11px;
            line-height: 1.5;
        }
        .syntax-title {
            font-weight: bold;
            color: var(--text-primary);
            margin-bottom: 1px;
        }
        .syntax-example {
            color: var(--btn-primary);
            font-family: monospace;
            font-size: 11px;
        }
        .syntax-desc {
            color: var(--text-secondary);
        }
        .syntax-desc code {
            background: rgba(128, 128, 128, 0.15);
            padding: 0 3px;
            border-radius: 2px;
            font-size: 10px;
        }
        .syntax-fields-list code {
            margin-right: 2px;
        }

        /* Hint tooltip */
        .filter-hint {
            position: fixed;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 12px;
            max-width: 300px;
            z-index: 2000;
            pointer-events: none;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
            line-height: 1.4;
            display: none;
        }
        [data-theme="dark"] .filter-hint {
            background: rgba(255, 255, 255, 0.95);
            color: #000;
        }

        /* Control buttons - base style */
        .control-btn {
            padding: 6px 12px;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 500;
            transition: background-color 0.15s, opacity 0.15s;
            white-space: nowrap;
            text-align: center;
            line-height: 1.4;
        }
        .control-btn:hover { opacity: 0.85; }
        .control-btn:active { opacity: 0.7; }
        /* Navigation buttons - blue */
        .btn-nav { background: #2563eb; }
        .btn-nav:hover { background: #1d4ed8; }
        /* Expand - green */
        .btn-expand { background: #2f9e58; }
        .btn-expand:hover { background: #22834a; }
        /* Collapse - warm red */
        .btn-collapse { background: #dc2626; }
        .btn-collapse:hover { background: #b91c1c; }
        /* Tool buttons - indigo */
        .btn-tool { background: #4f46e5; }
        .btn-tool:hover { background: #4338ca; }
        /* Filter-box apply button - blue, same family as btn-nav */
        .filter-btn { background: #2563eb; }
        .filter-btn:hover { background: #1d4ed8; }

        /* 当 body 带有 hide-floating-controls 时隐藏所有浮动按钮 */
        body.hide-floating-controls .floating-buttons,
        body.hide-floating-controls .controls {
            display: none !important;
        }

        /* 小型悬浮切换按钮（不会遮挡正文，默认靠右中间，支持鼠标拖动） */
        .floating-toggle {
            position: fixed;
            top: 50%;
            right: 12px;
            width: 44px;
            height: 44px;
            border-radius: 22px;
            background: var(--container-bg);
            border: 1px solid var(--border-color);
            box-shadow: 0 2px 6px rgba(0,0,0,0.12);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: grab;
            z-index: 3000;
            opacity: 0.95;
            user-select: none;
            transition: background-color 0.15s, opacity 0.15s;
            /* 仅在非拖动状态下使用 transform 和 transition 动画 */
            transform: translateY(-50%);
        }
        .floating-toggle:hover:not(.dragging) {
            background: var(--container-bg-hover, #f3f4f6);
            transform: translateY(-50%) scale(1.05);
            box-shadow: 0 4px 8px rgba(0,0,0,0.15);
        }
        .floating-toggle.dragging {
            cursor: grabbing;
            opacity: 0.98;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            transition: none;
            transform: none !important;
        }
        .floating-toggle .ft-icon {
            font-size: 18px;
            color: var(--text-color);
            user-select: none;
            pointer-events: none;
        }
        /* 在移动端靠底部放置，避免遮挡阅读区 */
        @media (max-width: 768px) {
            .floating-toggle {
                top: auto;
                bottom: 18px;
                right: 12px;
            }
        }
        .progress-bar {
            position: fixed;
            top: 0;
            left: 0;
            width: 0%;
            height: 3px;
            background-color: var(--btn-primary);
            z-index: 2000;
            transition: width 0.3s ease;
        }
        .keyboard-hint {
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
        }
        .keyboard-hint h3 {
            margin: 0 0 5px 0;
            font-size: 12px;
        }
        .keyboard-hint ul {
            margin: 0;
            padding-left: 15px;
        }
        .keyboard-hint li {
            margin: 2px 0;
        }
        .keyboard-hint strong {
            color: var(--btn-primary);
        }
        .keyboard-hint kbd {
            background-color: var(--bg-color);
            border: 1px solid var(--border-color);
            border-radius: 3px;
            padding: 1px 4px;
            font-family: monospace;
            font-size: 11px;
        }
        .keyboard-hint-close {
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
        }
        .keyboard-hint-close:hover {
            color: var(--btn-danger);
        }
        /* 锚点目标行高亮样式 */
        .line-container:target {
            background-color: var(--line-highlighted);
            animation: pulse-highlight 1s ease-out;
        }
        @keyframes pulse-highlight {
            0% {
                background-color: var(--line-highlighted);
                box-shadow: 0 0 10px rgba(255, 193, 7, 0.5);
            }
            100% {
                background-color: transparent;
                box-shadow: none;
            }
        }
        /* Ftrace语法高亮样式 */
        .hl-cpu {
            color: #d73a49;
            font-weight: bold;
        }
        [data-theme="dark"] .hl-cpu {
            color: #f97583;
            font-weight: bold;
        }
        
        .hl-time {
            color: #005cc5;
            font-weight: normal;
        }
        [data-theme="dark"] .hl-time {
            color: #79b8ff;
            font-weight: normal;
        }
        
        .hl-unit {
            color: #6f42c1;
            font-weight: bold;
        }
        [data-theme="dark"] .hl-unit {
            color: #b392f0;
            font-weight: bold;
        }
        
        .hl-func {
            color: #6f42c1;
            font-weight: bold;
        }
        [data-theme="dark"] .hl-func {
            color: #b392f0;
            font-weight: bold;
        }

        .hl-module-func {
            color: #0369a1;
            font-weight: bold;
        }
        [data-theme="dark"] .hl-module-func {
            color: #79c0ff;
            font-weight: bold;
        }

        .hl-addr {
            color: #032f62;
            font-family: 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', 'Courier New', monospace;
        }
        [data-theme="dark"] .hl-addr {
            color: #79b8ff;
            font-family: 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', 'Courier New', monospace;
        }
        
        .hl-comment {
            color: #6a737d;
            font-style: italic;
        }
        [data-theme="dark"] .hl-comment {
            color: #8b949e;
            font-style: italic;
        }
        
        .hl-symbol {
            color: #24292e;
            font-weight: bold;
        }
        [data-theme="dark"] .hl-symbol {
            color: #c9d1d9;
            font-weight: bold;
        }

        .hl-ret-error {
            color: #d73a49;
            font-weight: bold;
            background-color: rgba(215, 58, 73, 0.1);
            padding: 1px 3px;
            border-radius: 2px;
        }
        [data-theme="dark"] .hl-ret-error {
            color: #f97583;
            background-color: rgba(249, 117, 131, 0.15);
        }

        .hl-ret-success {
            color: #28a745;
            font-weight: bold;
            background-color: rgba(40, 167, 69, 0.1);
            padding: 1px 3px;
            border-radius: 2px;
        }
        [data-theme="dark"] .hl-ret-success {
            color: #3fb950;
            background-color: rgba(63, 185, 80, 0.15);
        }

        .hl-ret-normal {
            color: #586069;
            font-weight: bold;
        }
        [data-theme="dark"] .hl-ret-normal {
            color: #8b949e;
        }

        /* C代码简洁语法高亮 */
        .c-keyword {
            color: #d73a49;  /* 红色用于关键字 */
            font-weight: bold;
        }
        [data-theme="dark"] .c-keyword {
            color: #f97583;
            font-weight: bold;
        }
        
        .c-string {
            color: #032f62;  /* 蓝色用于字符串 */
        }
        [data-theme="dark"] .c-string {
            color: #79b8ff;
        }
        
        .c-comment {
            color: #6a737d;  /* 灰色用于注释 */
            font-style: italic;
        }
        [data-theme="dark"] .c-comment {
            color: #8b949e;
            font-style: italic;
        }
        
        .c-number {
            color: #6f42c1;  /* 紫色用于数字 */
        }
        [data-theme="dark"] .c-number {
            color: #b392f0;
        }
        
        /* Linux内核特定类型 */
        .kernel-type {
            color: #0184bc;  /* 青色用于内核类型 */
            font-weight: bold;
        }
        [data-theme="dark"] .kernel-type {
            color: #58a6ff;
            font-weight: bold;
        }
        
        /* Linux内核属性修饰符 */
        .kernel-attr {
            color: #9e1a1a;  /* 深红色用于属性 */
        }
        [data-theme="dark"] .kernel-attr {
            color: #ff7b72;
        }
        
        /* Linux内核常用宏常量 */
        .kernel-macro {
            color: #e36209;  /* 橙色用于宏常量 */
            font-weight: bold;
        }
        [data-theme="dark"] .kernel-macro {
            color: #fb8500;
            font-weight: bold;
        }
        
        @media (max-width: 768px) {
            body {
                padding: 4px;
            }
            .container {
                padding: 6px 4px;
            }
            .page-header {
                font-size: 11px;
                padding: 4px 6px;
            }
            .line-container {
                font-size: 11px;
                padding: 0.5px 1px;
            }
            .expanded-content {
                margin-left: 25px;
                padding: 4px 6px;
                font-size: 10px;
            }
            .controls {
                bottom: 10px;
                right: 10px;
            }
            .toolbar {
                padding: 4px 6px;
                gap: 3px;
            }
            .btn-group + .btn-group {
                margin-left: 2px;
                padding-left: 5px;
            }
            .control-btn {
                padding: 5px 8px;
                font-size: 11px;
            }
            .keyboard-hint {
                bottom: 10px;
                left: 10px;
                font-size: 10px;
                max-width: 200px;
            }
        }
'''


def generate_html(parsed_lines, vmlinux_path, faddr2line_path, module_dirs=None, base_url=None, module_url=None, kernel_src=None, use_list=False, verbose=False, fast_mode=False, highlight_code=False, path_prefix=None, module_src=None, module_srcs=None, script_args=None, enable_filter=False, parse_time=0, total_time=0, func_links=False, entry_offset=0, enable_fold=False, timeline_json=None):
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
        enable_fold=enable_fold, timeline_json=timeline_json
    )
    return generator.generate()

# ============================================================
# Main Entry Point
# ============================================================

def build_timeline_data(parsed_lines, vmlinux_path, module_dirs, args):
    """Build the Timeline tab data set. Returns a JSON-serializable dict or None on failure."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        import timeline as tlmod
        import btf as btfmod
    except ImportError as e:
        print(f"Warning: timeline disabled, cannot import helper modules: {e}", file=sys.stderr)
        return None
    t0 = time.time()
    rettypes = None
    if not args.no_btf:
        modules = sorted({l.get('module_name') for l in parsed_lines if l.get('module_name')})
        mod_paths = []
        for m in modules:
            mp = find_module_path(m, module_dirs, args.verbose)
            if mp:
                mod_paths.append(mp)
        extra = args.btf or []
        rettypes = btfmod.build_return_db(vmlinux_path,
                                          extra_paths=extra, module_paths=mod_paths)
        for err in rettypes.errors:
            verbose_print(f"BTF: {err}", args.verbose)
        if len(rettypes) == 0:
            print("Note: no BTF found (vmlinux lacks .BTF); timeline error paths will be unverified", file=sys.stderr)
            rettypes = None
        else:
            verbose_print(f"BTF: {len(rettypes)} function return types from {rettypes.sources}", args.verbose)
    lines = [l.raw_line for l in parsed_lines]
    builder = tlmod.build_timeline(lines, rettypes=rettypes, errno_map=ERROR_CODE_MAP, verbose=args.verbose)
    data = builder.to_data()
    st = data['stats']
    print(f"Timeline: {st['slices']} slices, {st['tasks']} tasks, mode={data['mode']}, "
          f"{len(data['chains'])} error chains, {st['offcpu']} off-CPU, {time.time() - t0:.2f}s")
    if data['n'] == 0:
        print("Note: no function_graph calls found; timeline tab omitted", file=sys.stderr)
        return None
    return data


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
    parser.add_argument('--timeline', action='store_true',
                        help='Add a Timeline tab: per-task flame chart over time with context/error-path coloring')
    parser.add_argument('--btf', nargs='*', default=None, metavar='FILE',
                        help='BTF sources for return-type checks (raw /sys/kernel/btf/* copies or ELF files). '
                             'Default: the .BTF section of --vmlinux and of resolved modules')
    parser.add_argument('--no-btf', action='store_true',
                        help='Do not use BTF; error paths are guessed from return values only')
    args = parser.parse_args()

    # 参数健壮性检查
    # 检查ftrace文件是否存在
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
        print(f"Warning: --fast and --use-external are both specified, --use-external will be ignored", file=sys.stderr)

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
            script_dir = os.path.dirname(os.path.abspath(__file__))
            fast_faddr2line_path = os.path.join(script_dir, 'fastfaddr2line.py')
            if os.path.exists(fast_faddr2line_path):
                faddr2line_path = fast_faddr2line_path
                verbose_print(f"Using fastfaddr2line.py at {fast_faddr2line_path}", args.verbose)
            else:
                print(f"Error: Cannot locate fastfaddr2line.py at {fast_faddr2line_path}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Error: Cannot locate faddr2line tool (only available in kernel source scripts/)", file=sys.stderr)
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

    # 显示工具链信息
    if args.verbose:
        if cross_compile:
            print(f"[INFO] CROSS_COMPILE: {cross_compile}", file=sys.stderr)
        if llvm:
            print(f"[INFO] LLVM: {llvm}", file=sys.stderr)
        if util_prefix or util_suffix:
            print(f"[INFO] Tool chain prefix/suffix: {util_prefix}*{util_suffix}", file=sys.stderr)
            print(f"[INFO] Note: faddr2line only exists in kernel source, not affected by toolchain", file=sys.stderr)
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
    parsed_lines = parse_ftrace_file(args.ftrace_file, args.verbose, args.enable_fold)
    parse_time = time.time() - start_time
    verbose_print(f"File parsing completed in {parse_time:.2f} seconds", args.verbose)

    # 构建时间轴数据（可选）
    timeline_json = None
    if args.timeline:
        timeline_json = build_timeline_data(parsed_lines, vmlinux_path, module_dirs, args)

    # 计算统计信息
    expandable_count = sum(1 for l in parsed_lines if l['expandable'])
    module_count = sum(1 for l in parsed_lines if l['expandable'] and l['module_name'])
    kernel_count = expandable_count - module_count

    # 生成HTML
    html_content, vmlinux_time, module_time = generate_html(
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
        script_args=args,  # 传递命令行参数用于显示
        enable_filter=args.filter,  # 传递过滤器开关
        parse_time=parse_time,  # 传递解析时间
        total_time=0,  # 占位符，稍后计算
        func_links=args.func_links,  # 传递函数名超链接开关
        entry_offset=args.entry_offset,  # 传递函数入口地址偏移量
        enable_fold=args.enable_fold,  # 传递折叠功能开关
        timeline_json=timeline_json
    )

    # 计算总时间
    total_time = parse_time + vmlinux_time + module_time

    # 输出详细的统计信息到日志
    print(f"\n=== Processing Statistics ===")
    print(f"Trace file parsing: {parse_time:.2f}s")
    if vmlinux_time > 0:
        print(f"Vmlinux resolution: {vmlinux_time:.2f}s")
    if module_time > 0:
        print(f"Modules resolution: {module_time:.2f}s")
    print(f"Total processing time: {parse_time + vmlinux_time + module_time:.2f}s")
    print(f"Total lines: {len(parsed_lines)}")
    print(f"Expandable entries: {sum(1 for l in parsed_lines if l['expandable'])}")
    print(f"=============================\n")
    
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
