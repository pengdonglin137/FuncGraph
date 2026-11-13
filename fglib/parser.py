"""FtraceParser: function_graph text -> list[FtraceLine] (+ fold blocks)."""

import re
import sys

from .model import FtraceLine
from .util import remove_compiler_suffix, verbose_print


class FtraceParser:
    """Parses ftrace function_graph output files."""

    def __init__(self, verbose=False):
        self.verbose = verbose

    def parse(self, file_path, enable_fold=False, lines=None):
        """Parse ftrace file and return list of FtraceLine objects.

        This is the main entry point, equivalent to the old parse_ftrace_file().

        参数:
            file_path: ftrace输出文件路径（lines 给出时只用于日志）
            enable_fold: 是否启用函数调用折叠功能
            lines: 可选，已读入（可能已预过滤）的行列表，不含换行符

        返回:
            parsed_lines: 包含所有行信息的列表
        """
        verbose_print(f"Parsing ftrace file: {file_path}", self.verbose)
        parsed_lines = []
        expandable_count = 0

        try:
            with (open(file_path, 'r') if lines is None else _NullCtx(lines)) as f:
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

                        function_match = re.search(r'\|\s*([A-Za-z_$][\w.$]*)(?:\s+\[([\w-]+)\])?\(.*\)\s*[;{]', line)
                        raw_func_name = function_match.group(1) if function_match else None
                        line_data = FtraceLine(
                            raw_line=line,
                            raw_func_name=raw_func_name,
                            display_func_name=remove_compiler_suffix(raw_func_name) if raw_func_name else None,
                            module_name=function_match.group(2) if function_match else None,
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
                # 使用 line_index 作为唯一性的一部分
                fold_id = 'f' + _base36(line_index)

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


def _base36(n):
    """Compact, deterministic fold id component (the entry line index)."""
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    out = ''
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if n == 0:
            return out


class _NullCtx:
    """Context manager yielding an in-memory line list (same shape as a file)."""

    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self.lines

    def __exit__(self, *exc):
        return False


def parse_ftrace_file(file_path, verbose=False, enable_fold=False, lines=None):
    """Parse ftrace file. Wrapper for backward compatibility."""
    parser = FtraceParser(verbose=verbose)
    return parser.parse(file_path, enable_fold=enable_fold, lines=lines)


def identify_fold_blocks(parsed_lines, verbose=False):
    """Identify fold blocks. Wrapper for backward compatibility."""
    parser = FtraceParser(verbose=verbose)
    parser._identify_fold_blocks(parsed_lines)
