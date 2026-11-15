"""KVM tracepoint evidence, kept separate from reconstructed function calls."""
import re


EVENT = re.compile(r'^(?:/\*\s*)?(kvm:\w+|kvm_\w+|vcpu_match_mmio):\s*(.*?)\s*(?:\*/)?$', re.DOTALL)
VCPU = re.compile(r'\bvcpu(?:_id)?(?:\s*[=:]\s*|\s+)(\d+)\b')
CLOCK = re.compile(r'^#.*?\bclock=([\w_]+)(?=\s|$)')
SHARED_CLOCKS = frozenset(('global', 'mono', 'mono_raw', 'boot', 'tai', 'uptime', 'perf'))


class KvmTimeline:
    """Pair adjacent, opposite boundaries in the same recorded Host thread.

    Events: task, time, CPU, Text line, name, payload, vCPU (-1 unknown), flags.
    Intervals: task, start, end, phase (0 Guest / 1 Host), vCPU, first event,
    last event (-1 absent), open end. All times remain integer nanoseconds.
    """

    def __init__(self, intern):
        self.intern = intern
        self.clock = None
        self.events = []
        self.intervals = []
        self.issues = []
        self.losses = []
        self.pending = {}
        self.latest = {}
        self.end = None
        self.exit_name = None

    def observe(self, timestamp):
        if timestamp is not None:
            self.end = max(self.end, timestamp) if self.end is not None else timestamp

    def header(self, text):
        match = CLOCK.match(text)
        if match:
            self.clock = match[1]

    def lost(self, cpu, line):
        self.losses.append([cpu, line])
        for previous in self.pending.values():
            self.issues.append([previous, -1, 'lost_events'])
        self.pending.clear()

    def comment(self, text, task, timestamp, cpu, line, flags=0):
        match = EVENT.match(text)
        if not match:
            return False
        name, payload = match.groups()
        return self.event(name.split(':')[-1], payload, task, timestamp, cpu, line, flags)

    def event(self, name, payload, task, timestamp, cpu, line, flags=0):
        if timestamp is None:
            return False
        self.observe(timestamp)
        vcpu = VCPU.search(payload)
        vcpu = int(vcpu[1]) if vcpu else -1
        index = len(self.events)
        self.events.append([task.tid, timestamp, cpu if cpu is not None else -1, line,
                            self.intern(name), self.intern(payload), vcpu, flags])
        previous_time = self.latest.get(task.tid)
        if previous_time is not None and timestamp < previous_time:
            self.issues.append([self.pending.pop(task.tid, -1), index, 'out_of_order'])
            return True
        self.latest[task.tid] = timestamp
        if name not in ('kvm_entry', 'kvm_exit'):
            return True
        if name == 'kvm_exit':
            self.exit_name = self.events[index][4]
        if task.pid is None or task.pid <= 0:
            self.issues.append([-1, index, 'missing_host_tid'])
            return True
        previous = self.pending.pop(task.tid, None)
        if previous is not None:
            before = self.events[previous]
            if before[4] == self.events[index][4]:
                self.issues.append([previous, index, 'repeated_boundary'])
            elif before[6] >= 0 and vcpu >= 0 and before[6] != vcpu:
                self.issues.append([previous, index, 'vcpu_changed'])
            elif before[2] != self.events[index][2] and self.clock not in SHARED_CLOCKS:
                self.issues.append([previous, index, 'unverified_migration_clock'])
            else:
                self.intervals.append([task.tid, before[1], timestamp, int(name == 'kvm_entry'),
                                       before[6] if before[6] >= 0 else vcpu, previous, index, 0])
        self.pending[task.tid] = index
        return True

    def to_data(self, origin):
        intervals = list(self.intervals)
        for index in self.pending.values():
            event = self.events[index]
            end = max(event[1], self.end) if self.clock in SHARED_CLOCKS else event[1]
            intervals.append([event[0], event[1], end,
                              int(self.exit_name == event[4]), event[6], index, -1, 1])
        intervals.sort(key=lambda record: (record[0], record[1], record[5]))
        return {
            'v': 1,
            'clock': self.clock or 'unknown',
            'origin_ns': str(origin),
            'events': [[record[0], record[1] - origin] + record[2:] for record in self.events],
            'intervals': [[record[0], record[1] - origin, record[2] - origin] + record[3:]
                          for record in intervals],
            'issues': self.issues,
            'losses': self.losses,
        }
