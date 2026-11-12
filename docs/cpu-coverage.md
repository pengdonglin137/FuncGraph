# CPU coverage and function lifetime / CPU 覆盖与函数生命周期

The CPU overview and a function slice answer different questions. A function
slice uses its recorded entry/exit timestamps. A CPU segment uses CPU-tagged
kernel records and, when present, scheduler events. A user function recorded
without a CPU number cannot establish which CPU ran its earlier instructions.

CPU 概览和函数区间的含义不同：函数按真实 entry/exit 时间绘制；CPU 行按带 CPU 编号的
内核记录及实际采集的调度事件绘制。用户态记录没有 CPU 编号时，不能根据后续一次
系统调用的 CPU，反推整个用户态调用此前都在同一 CPU 上运行。

## Mixed sample

In `samples/sample_mixed.jsonl` / `sample_mixed.html`:

| Observation | Relative time | Absolute monotonic time (ns) |
| --- | --- | --- |
| First retained event / report origin | 0 ns | 88350812558146 |
| Recorded switch-in of demo_mixed on CPU4 | 430,595,341 ns | 88351243153487 |
| `main` entry | 671,208,190 ns | 88351483766336 |
| First kernel function record on CPU4 | 676,983,649 ns | 88351489541795 |
| Last kernel function record on CPU4 | 677,350,965 ns | 88351489909111 |
| `main` return | 677,397,510 ns | 88351489955656 |

The sample now retains all 14 switches, 28 waking events and seven wakeups from
its original saved capture, alongside 25 user call-site events. Its 714 calls,
absolute function timestamps, arguments, addresses and cross-space ancestry are
unchanged. Added scheduling records move the report origin earlier; this is not
a clock correction. The recorded CPU4 switch-in precedes `main` by 240.612849 ms:
the task can execute before `main`. User records still have `cpu=null`; do not
fill their CPU field or extend functions using scheduling boundaries.

样例现在保留原始采集中的全部 14 条切换、28 条 waking、7 条 wakeup，另有 25 条
用户调用点事件。714 个调用、绝对时间、参数、地址和跨态父子关系不变；报告原点因
保留更早调度记录而前移，不是校正时钟。CPU4 上的真实 switch-in 比 `main` 早
240.612849 ms，任务可在 `main` 之前执行。用户态记录仍保持 `cpu=null`，不能填入
推断的 CPU，也不能把函数入口移到调度边界。

The full scheduling context spans about 705 ms, compared with 6.189320 ms for
`main`. The initial view focuses on calls; the overview and **Fit trace** / `0` /
`Home` retain the full context. **Tools → Navigate → Fit calls** returns to the
calls without filtering the underlying evidence. Readback `enable=1` alone is
still not proof of retained events. `--no-sched` controls perf scheduling;
patched `--kernel-full-info` enables kernel scheduler tracepoints independently.
See [record task switches](uftrace.md#record-task-switches) for verification.

完整调度上下文约 705 ms，而 `main` 只有 6.189320 ms。初始视野聚焦调用，概览与
**Fit trace** / `0` / `Home` 仍覆盖完整记录，**Tools → Navigate → Fit calls**
返回调用范围，不过滤底层证据。enable=1 仍不能单独证明事件被保留。`--no-sched`
只控制 perf 来源，补丁版 `--kernel-full-info` 独立开启内核调度 tracepoint，详见
[记录任务切换](uftrace.cn.md#记录任务切换)。

## Reading the overview

- Without actual scheduler-switch records, CPU segments have dashed edges,
  an **observed** label and an explicit coverage explanation in their tooltip.
  Their duration is an observation span, **not continuous CPU running time**.
- **First observed / last observed** do not mean task creation/exit, a function
  entry/return, or complete scheduling coverage. Unrecorded gaps, migrations and
  earlier CPU placement remain unknown; they are not filled with guessed runs.
- Reports containing actual scheduler-switch events retain the recorded
  scheduling presentation. Their boundaries still refer to captured CPU
  segments, not necessarily the lifetime of `main` or of the process.
- This presentation changes neither task runs nor function timestamps, and
  adds no trace-wide scan to cursor or hover handling. Old reports must be
  regenerated to use it.

没有实际调度切换事件时，CPU 段以虚线和 **observed** 区分，提示框明确写出首末观测时间
及证据局限；不能把这段长度当成连续 CPU 运行时间。缺失的间隔、迁移及更早的 CPU
归属保持未知，不向前延伸到 `main`，也不改写原始时间。已有真实调度事件的报告继续
展示记录的调度区间，仍不把它等同于进程或 `main` 的完整生命周期。
