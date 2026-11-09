#!/bin/bash
# fgcapture.sh - capture a function_graph trace suitable for FuncGraph.
#
# Runs on the target (needs root). Sets up tracefs for the chosen scenario,
# captures, checks for lost events and restores every setting it touched.
#
#   fgcapture.sh [options] [-- command args...]
#
# Scenarios:
#   (default)      timeline: abstime + latency-format + proc + retval + retaddr + tail
#   --args         arguments too. The kernel is probed first: where args and retaddr can be
#                  printed together (BTF print optimizations) all timeline options stay on;
#                  otherwise (e.g. 6.18) retaddr is turned off, and -g/-p/a command is required
#                  because formatting arguments is slow there (~36 ms/line on a Pi 4).
#
# Options:
#   -o FILE        output file (default ./ftrace.txt)
#   -d SECONDS     capture duration when no command is given (default 1)
#   -p PID[,PID]   trace only these pids (set_ftrace_pid)
#   -g FUNC[,..]   trace only the call graphs of these functions (set_graph_function)
#   -n FUNC[,..]   do not trace these call graphs (set_graph_notrace)
#   -b KB          per-CPU buffer size in KB (default 16384)
#   --sched        also record sched_switch / sched_waking / sched_wakeup events
#   --keep         do not restore the previous tracefs settings
#   --calibrate    measure the tracing overhead with the same options first (vfs_read with and
#                  without traced children, 3000 calls) and record it in the output header;
#                  funcgraph.py then offers durations with the overhead subtracted
#   -- CMD ...     run CMD and trace only it (and its children): the command starts
#                  stopped, its pid is written to set_ftrace_pid, then it continues
#
# Examples:
#   fgcapture.sh -d 0.5 -o /tmp/all.txt
#   fgcapture.sh --sched -- cat /nonexistent
#   fgcapture.sh --args -g vfs_read,vfs_write -d 1
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -u

T=/sys/kernel/tracing
[ -d $T/options ] || T=/sys/kernel/debug/tracing
OUT=./ftrace.txt DUR=1 PIDS= GRAPH= NOTRACE= BUF=16384 ARGS=0 SCHED=0 KEEP=0 CAL=0
CMD=()

die() { echo "fgcapture: $*" >&2; exit 1; }
usage() { sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        -o) OUT=$2; shift 2 ;;
        -d) DUR=$2; shift 2 ;;
        -p) PIDS=$2; shift 2 ;;
        -g) GRAPH=$2; shift 2 ;;
        -n) NOTRACE=$2; shift 2 ;;
        -b) BUF=$2; shift 2 ;;
        --args) ARGS=1; shift ;;
        --sched) SCHED=1; shift ;;
        --keep) KEEP=1; shift ;;
        --calibrate) CAL=1; shift ;;
        -h|--help) usage 0 ;;
        --) shift; CMD=("$@"); break ;;
        *) echo "unknown option $1" >&2; usage 1 ;;
    esac
done

[ "$(id -u)" = 0 ] || die "must run as root (tracefs)"
[ -w $T/current_tracer ] || die "tracefs not found at $T"
grep -qw function_graph $T/available_tracers || die "function_graph tracer not available (CONFIG_FUNCTION_GRAPH_TRACER)"

# ---- save current state ----
SAVE=$(mktemp -d /tmp/fgcapture.XXXXXX)
OPTS="funcgraph-abstime funcgraph-proc funcgraph-retval funcgraph-retaddr funcgraph-tail funcgraph-args
      funcgraph-duration funcgraph-cpu funcgraph-overhead funcgraph-irqs latency-format"
save() {
    cat $T/current_tracer > $SAVE/tracer
    cat $T/tracing_on > $SAVE/on
    cat $T/buffer_size_kb > $SAVE/buf
    for f in set_ftrace_pid set_graph_function set_graph_notrace set_ftrace_filter; do
        [ -e $T/$f ] && grep -v '^#' $T/$f > $SAVE/$f 2>/dev/null
    done
    for o in $OPTS; do [ -e $T/options/$o ] && cat $T/options/$o > "$SAVE/opt_$o"; done
    for e in sched_switch sched_wakeup sched_waking; do
        [ -e $T/events/sched/$e/enable ] && cat $T/events/sched/$e/enable > $SAVE/ev_$e
    done
}
restore() {
    [ $KEEP = 1 ] && { echo "fgcapture: settings kept (--keep); previous state saved in $SAVE"; return; }
    echo 0 > $T/tracing_on
    echo nop > $T/current_tracer
    for o in $OPTS; do [ -e "$SAVE/opt_$o" ] && cat "$SAVE/opt_$o" > $T/options/$o 2>/dev/null; done
    for f in set_ftrace_pid set_graph_function set_graph_notrace set_ftrace_filter; do
        [ -e $T/$f ] || continue
        echo > $T/$f
        [ -s $SAVE/$f ] && while read -r v; do echo "$v" >> $T/$f; done < $SAVE/$f 2>/dev/null
    done
    for e in sched_switch sched_wakeup sched_waking; do
        [ -e $SAVE/ev_$e ] && cat $SAVE/ev_$e > $T/events/sched/$e/enable
    done
    cat $SAVE/buf > $T/buffer_size_kb 2>/dev/null
    cat $SAVE/tracer > $T/current_tracer
    cat $SAVE/on > $T/tracing_on
    rm -rf "$SAVE"
}
save
trap 'restore' EXIT
trap 'exit 130' INT TERM

setopt() { [ -e $T/options/$1 ] && echo "$2" > $T/options/$1 || echo "fgcapture: option $1 not supported by this kernel" >&2; }
setlist() {   # file comma-list
    local f=$1 v
    echo > $T/$f
    [ -z "$2" ] && return
    for v in ${2//,/ }; do
        echo "$v" >> $T/$f 2>/dev/null || die "cannot add '$v' to $f (unknown function or pid?)"
    done
}

# ---- configure ----
echo 0 > $T/tracing_on
echo nop > $T/current_tracer
echo > $T/trace
echo "$BUF" > $T/buffer_size_kb
setlist set_graph_function "$GRAPH"
[ -e $T/set_graph_notrace ] && setlist set_graph_notrace "$NOTRACE"
setlist set_ftrace_pid "$PIDS"
for o in funcgraph-duration funcgraph-cpu funcgraph-overhead funcgraph-irqs; do setopt $o 1; done
# Probe what this kernel can do with funcgraph-args: on kernels without the BTF print
# optimizations (e.g. 6.18) reading args is ~36 ms/line and args vanish when retaddr is on.
ARGS_RETADDR=0 ARGS_SLOW=1
if [ $ARGS = 1 ]; then
    echo nop > $T/current_tracer; echo > $T/trace
    # the probe must not be limited by -g/-n (vfs_read would only be seen inside the graph functions)
    echo > $T/set_graph_function; [ -e $T/set_graph_notrace ] && echo > $T/set_graph_notrace
    echo vfs_read > $T/set_ftrace_filter          # one function only: cheap even where args are slow
    for o in funcgraph-args funcgraph-retaddr funcgraph-proc; do setopt $o 1; done
    echo function_graph > $T/current_tracer
    echo 1 > $T/tracing_on; cat /proc/version > /dev/null; head -c 1 /etc/hostname > /dev/null; echo 0 > $T/tracing_on
    t0=$(date +%s%N); cat $T/trace > $SAVE/probe; t1=$(date +%s%N)
    n=$(grep -vc '^#' $SAVE/probe)
    if [ "$n" -gt 0 ]; then
        per_us=$(( (t1 - t0) / 1000 / n ))
        grep -q 'vfs_read(file=.*<-' $SAVE/probe && ARGS_RETADDR=1
        [ $per_us -lt 1000 ] && ARGS_SLOW=0
        echo "fgcapture: funcgraph-args probe: ${per_us} us/line, args with retaddr: $([ $ARGS_RETADDR = 1 ] && echo yes || echo no)"
    else
        per_us=unknown
        echo "fgcapture: funcgraph-args probe saw no vfs_read call; assuming slow args without retaddr" >&2
    fi
    echo nop > $T/current_tracer; echo > $T/trace; echo > $T/set_ftrace_filter
    setlist set_graph_function "$GRAPH"
    [ -e $T/set_graph_notrace ] && setlist set_graph_notrace "$NOTRACE"
    if [ $ARGS_SLOW = 1 ] && [ -z "$GRAPH$PIDS" ] && [ ${#CMD[@]} = 0 ]; then
        die "--args needs -g, -p or a command on this kernel: reading the trace would take ${per_us} us per line"
    fi
fi
if [ $ARGS = 1 ]; then
    for o in funcgraph-abstime funcgraph-proc funcgraph-retval funcgraph-args funcgraph-tail; do setopt $o 1; done
    setopt funcgraph-retaddr $ARGS_RETADDR
    if [ $ARGS_RETADDR = 1 ]; then setopt latency-format 1; else setopt latency-format 0; fi
else
    for o in funcgraph-abstime funcgraph-proc funcgraph-retval funcgraph-retaddr funcgraph-tail latency-format; do setopt $o 1; done
    setopt funcgraph-args 0
fi
for e in sched_switch sched_wakeup sched_waking; do
    [ -e $T/events/sched/$e/enable ] && echo $SCHED > $T/events/sched/$e/enable
done

# ---- optional overhead calibration (same options as the capture) ----
CALLINE=
calib_run() {   # $1 = set_graph_function | set_ftrace_filter ; prints "<median top duration ns> <entries per call>"
    echo nop > $T/current_tracer; echo > $T/trace
    echo > $T/set_graph_function; echo > $T/set_ftrace_filter; echo > $T/set_ftrace_pid
    echo vfs_read > $T/$1
    echo function_graph > $T/current_tracer
    ( kill -STOP $BASHPID; exec dd if=/dev/zero of=/dev/null bs=1 count=3000 status=none ) &
    local p=$!
    while [ "$(awk '{print $3}' /proc/$p/stat 2>/dev/null)" != "T" ]; do sleep 0.01; done
    echo $p > $T/set_ftrace_pid
    echo 1 > $T/tracing_on; kill -CONT $p; wait $p; echo 0 > $T/tracing_on
    # one line per vfs_read: "<duration ns>"; plus a trailing "calls <entries>" line
    awk -F'|' '
        /^#/ || NF < 4 { next }
        { body = $NF; dur = "" ; for (i = 2; i < NF; i++) if ($i ~ /[0-9.]+ us/) { d = $i; gsub(/[^0-9.]/, "", d); dur = d } }
        body ~ /^  vfs_read\(/ && body ~ /\);/ { print dur * 1000; n++ }
        body ~ /^  } \/\* vfs_read/ { print dur * 1000; n++ }
        body ~ /[(].*[)] *[{;]/ { calls++ }
        END { printf "calls %.3f\n", n ? calls / n : 0 }
    ' $T/trace > $SAVE/cal_$1
    local per; per=$(awk '/^calls/{print $2}' $SAVE/cal_$1)
    echo "$(grep -v '^calls' $SAVE/cal_$1 | sort -n | awk '{v[NR]=$1} END{print NR ? int(v[int((NR+1)/2)]) : 0}') $per"
}
leaf_p1() {
    awk -F'|' '/^#/ || NF < 4 { next } $NF ~ /\);/ { for (i = 2; i < NF; i++) if ($i ~ /[0-9.]+ us/) { d=$i; gsub(/[^0-9.]/,"",d); print int(d*1000) } }' $T/trace |
        sort -n | awk '{v[NR]=$1} END{print NR ? v[int(NR/100)+1] : 0}'
}
if [ $CAL = 1 ]; then
    echo "fgcapture: calibrating tracing overhead..."
    read -r A_MED A_CALLS < <(calib_run set_graph_function)
    SELF=$(leaf_p1)
    read -r B_MED B_CALLS < <(calib_run set_ftrace_filter)
    DESC=$(awk "BEGIN{print $A_CALLS - 1}")
    if awk "BEGIN{exit !($DESC > 0.5 && $A_MED > $B_MED)}"; then
        CHILD=$(awk "BEGIN{printf \"%d\", ($A_MED - $B_MED) / $DESC}")
        CALLINE="# fgcapture: overhead child=${CHILD}ns self=${SELF}ns (vfs_read: ${A_MED}ns with ${DESC} traced descendants, ${B_MED}ns without)"
        echo "fgcapture: overhead per traced child ${CHILD} ns, per call ${SELF} ns"
    else
        echo "fgcapture: calibration inconclusive (A=${A_MED}ns B=${B_MED}ns desc=${DESC}), skipped" >&2
    fi
    echo nop > $T/current_tracer; echo > $T/trace; echo > $T/set_ftrace_filter
    setlist set_graph_function "$GRAPH"
    setlist set_ftrace_pid "$PIDS"
fi
echo function_graph > $T/current_tracer

# ---- capture ----
START=$(date +%s.%N)
if [ ${#CMD[@]} -gt 0 ]; then
    # start the command stopped so tracing begins exactly with it
    ( kill -STOP $BASHPID; exec "${CMD[@]}" ) &
    CPID=$!
    while [ "$(awk '{print $3}' /proc/$CPID/stat 2>/dev/null)" != "T" ]; do sleep 0.01; done
    echo $CPID >> $T/set_ftrace_pid
    [ -e $T/options/function-fork ] && echo 1 > $T/options/function-fork
    echo 1 > $T/tracing_on
    kill -CONT $CPID
    wait $CPID; RC=$?
    echo 0 > $T/tracing_on
    [ -e $T/options/function-fork ] && echo 0 > $T/options/function-fork
    echo "fgcapture: command exited with $RC"
else
    echo 1 > $T/tracing_on
    sleep "$DUR"
    echo 0 > $T/tracing_on
fi

# ---- save output ----
echo "fgcapture: reading trace buffer..."
{ [ -n "$CALLINE" ] && echo "$CALLINE"; cat $T/trace; } > "$OUT" || die "cannot write $OUT"
END=$(date +%s.%N)
LINES=$(grep -vc '^#' "$OUT")
LOST=$(grep -c 'LOST.*EVENTS' "$OUT")
OVR=$(awk '/overrun:/{s+=$2} END{print s+0}' $T/per_cpu/cpu*/stats 2>/dev/null)
echo "fgcapture: $LINES lines -> $OUT ($(awk "BEGIN{printf \"%.1f\", $END-$START}")s)"
if [ "$LOST" != 0 ] || [ "${OVR:-0}" != 0 ]; then
    echo "fgcapture: WARNING: events were lost (LOST markers: $LOST, ring buffer overruns: $OVR)." >&2
    echo "           Increase -b (now $BUF KB per CPU), shorten -d, or narrow with -p/-g." >&2
fi
[ "$LINES" = 0 ] && echo "fgcapture: WARNING: the trace is empty (did the filter match anything?)" >&2
echo "fgcapture: convert with: python3 funcgraph.py --timeline --fast --filter --enable-fold --vmlinux <vmlinux> $OUT"
exit 0
