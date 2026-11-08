"""FtraceLine: one parsed line of function_graph output."""


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
