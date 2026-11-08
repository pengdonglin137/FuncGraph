"""Syntax highlighting for ftrace lines and C source."""

import re

from .constants import ERROR_CODE_MAP
from .util import verbose_print


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
