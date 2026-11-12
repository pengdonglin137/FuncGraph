"""Convenient user/mixed recording with an idle-tracefs restoration boundary."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import shlex
import signal
import stat
import subprocess
import sys

from . import uftrace_runtime


SCHED_EVENTS = ('sched_switch', 'sched_waking', 'sched_wakeup')
LIFECYCLE_EVENTS = ('sched_process_exec', 'sched_process_exit')
FILTERS = ('set_ftrace_pid', 'set_event_pid', 'set_graph_function', 'set_graph_notrace',
           'set_ftrace_filter', 'set_ftrace_notrace')
SETTINGS = ('tracing_on', 'current_tracer', 'trace_clock', 'max_graph_depth',
            'tracing_cpumask', 'set_event', 'buffer_size_kb') + FILTERS


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def normalized(name, value):
    value = value.strip()
    if name == 'trace_clock':
        match = re.search(r'\[([^\]]+)\]', value)
        return match.group(1) if match else value
    if name in FILTERS:
        return '\n'.join(line for line in value.splitlines()
                         if line and not line.startswith('#') and line != 'no pid')
    return value


class TracefsSession:
    def __init__(self, root, evidence, lock_path=Path('/run/funcgraph-capture.lock')):
        self.root = Path(root)
        self.evidence = evidence
        self.lock_path = lock_path
        self.lock = None
        self.before = {}

    def snapshot(self):
        names = list(SETTINGS)
        names += [str(path.relative_to(self.root)) for path in sorted(self.root.glob('options/*'))]
        names += [str(path.relative_to(self.root))
                  for path in sorted(self.root.glob('per_cpu/cpu*/buffer_size_kb'))]
        names += ['events/sched/' + event + '/enable' for event in SCHED_EVENTS + LIFECYCLE_EVENTS]
        return {name: normalized(name, (self.root / name).read_text())
                for name in names if (self.root / name).exists()}

    def write(self, name, value):
        (self.root / name).write_text((' ' if value == '' else str(value)) + '\n')

    def __enter__(self):
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        self.lock = os.fdopen(descriptor, 'r+')
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError('capture lock is not a regular file')
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OSError('another fgcapture session owns tracefs') from None
            self.before = self.snapshot()
            for name in ('tracing_on', 'current_tracer', 'trace_clock', 'set_event'):
                if name not in self.before:
                    raise OSError('required tracefs setting is missing: ' + name)
            if self.before['tracing_on'] != '0' or self.before['set_event']:
                raise OSError('tracing/events are active; refusing to interrupt another session')
            if (self.root / 'events/enable').read_text().strip() != '0':
                raise OSError('trace events are enabled; refusing to interrupt another session')
            if any(self.before.get(name) for name in FILTERS):
                raise OSError('mixed capture requires idle tracefs with empty PID/function filters')
            if 'function_graph' not in (self.root / 'available_tracers').read_text().split():
                raise OSError('function_graph tracer is unavailable')
            if 'mono' not in (self.root / 'trace_clock').read_text().replace('[', '').replace(']', '').split():
                raise OSError('tracefs mono clock is unavailable')
            write_json(self.evidence / 'tracefs-before.json', self.before)
            print('fgcapture: using idle tracefs; existing trace buffer contents will be consumed', file=sys.stderr)
            return self
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    def configure(self, args):
        for name, value in (('funcgraph-args', int(args.args)), ('funcgraph-retval', 1),
                            ('funcgraph-retaddr', 1)):
            path = 'options/' + name
            if path in self.before:
                self.write(path, value)
            else:
                print('fgcapture: kernel option unavailable: ' + name, file=sys.stderr)

    def __exit__(self, *exception):
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        errors = []
        try:
            ordered = ['tracing_on', 'current_tracer']
            ordered += [name for name in self.before if name not in ('tracing_on', 'current_tracer', 'buffer_size_kb')]
            if not any(name.startswith('per_cpu/') for name in self.before):
                ordered += ['buffer_size_kb']
            ordered += ['current_tracer', 'tracing_on']
            for index, name in enumerate(ordered):
                value = ('0' if name == 'tracing_on' else 'nop') if index < 2 else self.before.get(name)
                if value is None:
                    continue
                try:
                    self.write(name, value)
                except OSError as error:
                    errors.append(name + ': ' + str(error))
            try:
                after = self.snapshot()
            except OSError as error:
                after = {}
                errors.append('readback: ' + str(error))
            write_json(self.evidence / 'tracefs-after.json', after)
            differences = {name: [value, after.get(name)] for name, value in self.before.items()
                           if after.get(name) != value}
            write_json(self.evidence / 'tracefs-restore.json', {'errors': errors, 'differences': differences})
            if errors or differences:
                raise OSError('tracefs restoration failed; inspect ' + str(self.evidence / 'tracefs-restore.json'))
            print('fgcapture: restored all {} saved tracefs settings'.format(len(self.before)), file=sys.stderr)
        finally:
            self.lock.close()
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)


class CaptureInterrupted(Exception):
    def __init__(self, signum):
        self.signum = signum


@contextlib.contextmanager
def interruptible():
    received = []

    def handler(signum, frame):
        if not received:
            received.append(signum)
            raise CaptureInterrupted(signum)

    previous = {signum: signal.signal(signum, handler) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_record(command):
    process = None
    failed = False
    pending = []

    def defer(signum, frame):
        pending.append(signum)

    try:
        previous = {signum: signal.signal(signum, defer) for signum in (signal.SIGINT, signal.SIGTERM)}
        try:
            process = subprocess.Popen(command, start_new_session=True)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        if pending:
            raise CaptureInterrupted(pending[0])
        return process.wait()
    except BaseException:
        failed = True
        raise
    finally:
        if process is not None and (failed or process.poll() is None):
            previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
            try:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def parser():
    result = argparse.ArgumentParser(description='Capture kernel, user, or mixed function graphs.',
        epilog='Default kernel mode forwards legacy options to fgcapture.sh. '
               'User/mixed examples: fgcapture --user -o user.data -- ./app; '
               'sudo fgcapture --mixed -o mixed.data -- ./app. '
               'Applications need instrumentation (-pg -g on x86_64). '
               'Generate HTML: funcgraph --uftrace mixed.data --timeline --enable-fold --filter --output mixed.html')
    mode = result.add_mutually_exclusive_group()
    mode.add_argument('--kernel', action='store_true', help='kernel-only text capture (default)')
    mode.add_argument('--user', action='store_true', help='user-only uftrace capture; no tracefs access')
    mode.add_argument('--mixed', action='store_true', help='user + kernel, scheduling and exec/exit in one recording')
    result.add_argument('-o', '--output', help='new uftrace directory (default user.data / mixed.data)')
    result.add_argument('--uftrace-bin', help='optional recorder override; bundled uftrace is preferred')
    values = result.add_mutually_exclusive_group()
    values.add_argument('--args', dest='args', action='store_true', help='capture arguments/returns (default)')
    values.add_argument('--no-args', dest='args', action='store_false', help='disable argument capture')
    result.set_defaults(args=True, sched=None)
    scheduling = result.add_mutually_exclusive_group()
    scheduling.add_argument('--sched', dest='sched', action='store_true', help='capture scheduling (default in mixed mode)')
    scheduling.add_argument('--no-sched', dest='sched', action='store_false', help='omit scheduling; retain exec/exit in mixed mode')
    result.add_argument('-b', type=positive, default=16384, metavar='KB', help='kernel buffer KB per CPU (default 16384)')
    result.add_argument('-g', '--graph', help='comma-separated kernel graph roots (default architecture syscall roots)')
    result.add_argument('-n', '--notrace', help='optional comma-separated excluded kernel graph roots')
    result.add_argument('--kernel-depth', type=positive, default=8, help='kernel depth limit (default 8; increase deliberately)')
    result.add_argument('-A', '--argument', action='append', default=[], help='additional uftrace argument specification')
    result.add_argument('-R', '--retval', action='append', default=[], help='additional uftrace return specification')
    result.add_argument('command', nargs=argparse.REMAINDER, help='-- command [arguments...]')
    return result


def build_command(args, tracer, tracefs=None):
    command = tracer + ['record', '--clock=mono', '--srcline', '-T', '.@callsite']
    if args.args:
        command += ['--auto-args']
    if args.mixed:
        command += ['-K', str(args.kernel_depth), '--kernel-full', '--kernel-buffer={}k'.format(args.b)]
        events = LIFECYCLE_EVENTS + (SCHED_EVENTS if args.sched is not False else ())
        for event in events:
            if (tracefs / 'events/sched' / event / 'enable').exists():
                command += ['-E', 'sched:' + event + '@kernel']
            else:
                print('fgcapture: kernel event unavailable: ' + event, file=sys.stderr)
    if args.mixed or not args.sched:
        command += ['--no-sched']
    if platform.machine() in ('aarch64', 'arm64'):
        command += ['-P', '.']
    graph = args.graph
    if args.mixed and graph is None:
        architecture = platform.machine()
        roots = {'x86_64': '__x64_sys_.*', 'aarch64': '__arm64_sys_.*', 'arm64': '__arm64_sys_.*'}
        if architecture not in roots:
            raise OSError('choose explicit kernel graph roots with -g on ' + architecture)
        graph = roots[architecture]
    for graph, option in ((graph, '-F'), (args.notrace, '-N')):
        if graph:
            command += [option, ';'.join(name + '@kernel' for name in graph.split(','))]
    for value in args.argument:
        command += ['-A', value]
    for value in args.retval:
        command += ['-R', value]
    return command + ['-d', str(args.output)] + args.command


def kernel_command(arguments):
    source = Path(__file__).resolve().parent.parent / 'fgcapture.sh'
    script = str(source) if source.is_file() else shutil.which('fgcapture.sh')
    if not script:
        raise OSError('fgcapture.sh is unavailable; reinstall FuncGraph')
    boundary = arguments.index('--') if '--' in arguments else len(arguments)
    forwarded = [argument for argument in arguments[:boundary] if argument != '--kernel']
    return subprocess.call(['bash', script] + forwarded + arguments[boundary:])


def tracefs_root():
    for root in (Path('/sys/kernel/tracing'), Path('/sys/kernel/debug/tracing')):
        if (root / 'current_tracer').exists():
            return root
    raise OSError('tracefs is unavailable; mount tracefs before mixed capture')


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    prefix = arguments[:arguments.index('--')] if '--' in arguments else arguments
    try:
        if not any(value in prefix for value in ('--user', '--mixed', '--help', '-h')):
            return kernel_command(arguments)
        args = parser().parse_args(arguments)
        if args.command[:1] == ['--']:
            args.command = args.command[1:]
        if not args.command:
            raise OSError('user/mixed capture requires -- command [arguments...]')
        if args.user and (args.graph or args.notrace):
            raise OSError('-g/-n select kernel graphs; use --mixed')
        tracer = uftrace_runtime.command(args.uftrace_bin)
        args.output = Path(args.output or ('mixed.data' if args.mixed else 'user.data')).absolute()
        if args.output.exists() or args.output.is_symlink():
            raise OSError('refusing to overwrite an existing capture: ' + str(args.output))
        evidence = args.output.with_name(args.output.name + '.fgcapture')
        if args.mixed and os.geteuid() != 0:
            raise OSError('mixed capture requires root for tracefs; user-only capture does not')
        evidence.mkdir()
        tracefs = tracefs_root() if args.mixed else None
        session = TracefsSession(tracefs, evidence) if args.mixed else contextlib.nullcontext()
        with interruptible(), session as state:
            if state is not None:
                state.configure(args)
            invocation = build_command(args, tracer, tracefs)
            write_json(evidence / 'command.json', invocation)
            print('fgcapture: recording {} to {}'.format('user + kernel' if args.mixed else 'user', args.output), file=sys.stderr)
            result = run_record(invocation)
        if result:
            return result if result > 0 else 128 - result
        if args.mixed and (not (args.output / 'kernel_header').is_file() or
                           not (args.output / 'kernel-funcgraph.info').is_file() or
                           not (args.output / 'funcgraph-clock').is_file() or
                           not any(path.stat().st_size for path in args.output.glob('kernel-cpu*.dat'))):
            raise OSError('uftrace did not save kernel data; refusing to claim a mixed capture')
        print('fgcapture: generate HTML with: funcgraph --uftrace {} --timeline --enable-fold --filter --output report.html'.format(shlex.quote(str(args.output))), file=sys.stderr)
        return 0
    except CaptureInterrupted as error:
        print('fgcapture: interrupted; partial recording retained', file=sys.stderr)
        return 128 + error.signum
    except (OSError, ValueError) as error:
        print('fgcapture: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
