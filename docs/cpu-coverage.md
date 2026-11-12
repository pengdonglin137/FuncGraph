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
| `main` entry | 0 ns | 88351483766336 |
| First kernel record on CPU4 | 5,775,459 ns | 88351489541795 |
| Last kernel record on CPU4 | 6,142,775 ns | 88351489909111 |
| `main` return | 6,189,320 ns | 88351489955656 |

This capture has 25 user call-site events but **no `sched_switch` records**.
The saved configuration says scheduler events were enabled; configuration is
not evidence that corresponding events reached the saved export. User function
records have `cpu=null`. These timestamps share the saved monotonic clock; the
5.775459 ms difference is not an offset to correct or a delayed `main` entry.

该样例保存了 25 条用户态调用点事件，但没有实际的 `sched_switch` 事件。采集配置中的
enable=1 不等于导出文件中存在对应事件。用户态函数的 `cpu=null`，时间统一使用保存的
monotonic 时钟。因此相差的 5.775459 ms 不是应当平移修正的时钟偏移，也不是 `main`
实际从 CPU 行左边界才开始执行。

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
