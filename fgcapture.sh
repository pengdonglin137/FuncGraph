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
#   --args         arguments: args + retval, no retaddr (they cannot be combined on 6.18).
#                  Requires -g or -p: formatting arguments is slow (~36 ms/line on a Pi 4 / 6.18).
#
# Options:
#   -o FILE        output file (default ./ftrace.txt)
#   -d SECONDS     capture duration when no command is given (default 1)
#   -p PID[,PID]   trace only these pids (set_ftrace_pid)
#   -g FUNC[,..]   trace only the call graphs of these functions (set_graph_function)
#   -n FUNC[,..]   do not trace these call graphs (set_graph_notrace)
#   -b KB          per-CPU buffer size in KB (default 16384)
#   --sched        also record sched_switch / sched_wakeup events
#   --keep         do not restore the previous tracefs settings
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
OUT=./ftrace.txt DUR=1 PIDS= GRAPH= NOTRACE= BUF=16384 ARGS=0 SCHED=0 KEEP=0
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
        -h|--help) usage 0 ;;
        --) shift; CMD=("$@"); break ;;
        *) echo "unknown option $1" >&2; usage 1 ;;
    esac
done

[ "$(id -u)" = 0 ] || die "must run as root (tracefs)"
[ -w $T/current_tracer ] || die "tracefs not found at $T"
grep -qw function_graph $T/available_tracers || die "function_graph tracer not available (CONFIG_FUNCTION_GRAPH_TRACER)"
if [ $ARGS = 1 ] && [ -z "$GRAPH$PIDS" ] && [ ${#CMD[@]} = 0 ]; then
    die "--args needs -g, -p or a command: formatting arguments is very slow on kernels without the BTF print optimizations"
fi

# ---- save current state ----
SAVE=$(mktemp -d /tmp/fgcapture.XXXXXX)
OPTS="funcgraph-abstime funcgraph-proc funcgraph-retval funcgraph-retaddr funcgraph-tail funcgraph-args
      funcgraph-duration funcgraph-cpu funcgraph-overhead funcgraph-irqs latency-format"
save() {
    cat $T/current_tracer > $SAVE/tracer
    cat $T/tracing_on > $SAVE/on
    cat $T/buffer_size_kb > $SAVE/buf
    for f in set_ftrace_pid set_graph_function set_graph_notrace; do
        [ -e $T/$f ] && grep -v '^#' $T/$f > $SAVE/$f 2>/dev/null
    done
    for o in $OPTS; do [ -e $T/options/$o ] && cat $T/options/$o > "$SAVE/opt_$o"; done
    for e in sched_switch sched_wakeup; do
        [ -e $T/events/sched/$e/enable ] && cat $T/events/sched/$e/enable > $SAVE/ev_$e
    done
}
restore() {
    [ $KEEP = 1 ] && { echo "fgcapture: settings kept (--keep); previous state saved in $SAVE"; return; }
    echo 0 > $T/tracing_on
    echo nop > $T/current_tracer
    for o in $OPTS; do [ -e "$SAVE/opt_$o" ] && cat "$SAVE/opt_$o" > $T/options/$o 2>/dev/null; done
    for f in set_ftrace_pid set_graph_function set_graph_notrace; do
        [ -e $T/$f ] || continue
        echo > $T/$f
        [ -s $SAVE/$f ] && while read -r v; do echo "$v" >> $T/$f; done < $SAVE/$f 2>/dev/null
    done
    for e in sched_switch sched_wakeup; do
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
if [ $ARGS = 1 ]; then
    for o in funcgraph-abstime funcgraph-proc funcgraph-retval funcgraph-args funcgraph-tail; do setopt $o 1; done
    setopt funcgraph-retaddr 0; setopt latency-format 0
else
    for o in funcgraph-abstime funcgraph-proc funcgraph-retval funcgraph-retaddr funcgraph-tail latency-format; do setopt $o 1; done
    setopt funcgraph-args 0
fi
for e in sched_switch sched_wakeup; do
    [ -e $T/events/sched/$e/enable ] && echo $SCHED > $T/events/sched/$e/enable
done
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
cat $T/trace > "$OUT" || die "cannot write $OUT"
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
