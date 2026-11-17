# Recorded latency flags

Flags are not just the preempt count. FuncGraph decodes the five characters
printed by Linux `trace_print_lat_fmt`; older four-character records without
migration depth remain supported. Missing latency columns do not mean that
interrupts or preemption were enabled.

| Column | Recorded characters | Meaning |
|---|---|---|
| IRQ/BH | `.`, `d`, `b`, `D` | neither disabled, IRQs disabled, BH disabled, both disabled |
| Reschedule | `.`, `n`, `N`, `p`, `l`, `L`, `b`, `B` | combinations in the table below |
| Context | `.`, `s`, `h`, `H`, `z`, `Z` | task, softirq, hardirq, hardirq+softirq, NMI, NMI+hardirq |
| Preempt depth | `.`, `0`–`f` | low nibble of `common_preempt_count`; `.` means zero |
| Migration depth | `.`, `0`–`f` | high nibble of `common_preempt_count`; `.` means zero |

The uppercase context characters are preserved in the Timeline model and
selected-call/hover context labels; `H` is not reduced to `h`, nor `Z` to `z`.
Legacy `X` means the IRQ state was unavailable, not that IRQs were enabled.

## All eight reschedule states

The three independent recorded flags are `TRACE_FLAG_NEED_RESCHED`,
`TRACE_FLAG_NEED_RESCHED_LAZY` and `TRACE_FLAG_PREEMPT_RESCHED`:

| Character | NEED_RESCHED | NEED_RESCHED_LAZY | PREEMPT_RESCHED | Detail badges |
|---|---|---|---|---|
| `.` | no | no | no | none |
| `n` | yes | no | no | need-resched |
| `N` | yes | no | yes | need-resched / preempt-resched |
| `p` | no | no | yes | preempt-resched |
| `l` | no | yes | no | lazy-resched |
| `L` | no | yes | yes | lazy-resched / preempt-resched |
| `b` | yes | yes | no | need-resched / lazy-resched |
| `B` | yes | yes | yes | need-resched / lazy-resched / preempt-resched |

Each active request has one small badge, rather than labeling every nonzero
state `need-resched`. The flags row and hover text include the combined meaning
and original character. Text filters remain exact and case-sensitive:
`resched:="l"` is lazy-only, whereas `resched:="L"` also includes the preempt
reschedule request. The first-column `b` means BH disabled; the second-column
`b` means ordinary and lazy requests are both pending.

Numeric preempt filters decode the recorded digit as hexadecimal (`a` is 10,
`f` is 15, `.` is zero); comparison thresholds such as `preempt:>9` are decimal.
Exact filters such as `preempt:="a"` still match the original character.

`PREEMPT_RESCHED` is a **reschedule request**, not the numeric preempt-disable
depth. Its `p`/`N`/`L`/`B` characters must not create a `preempt off` or atomic-sleep
warning on their own. Numeric tracing correction remains internal; see
[preempt-baseline.md](preempt-baseline.md).

The existing amber corner/header overlays and scheduling-latency analysis cover
any nonzero request combination. They describe observed pending intervals, not
proof that a lazy-only request missed an immediate scheduling deadline. Windows
end when the request clears, the task switches out, or the capture ends. The
slice flags describe its recorded entry, not every flag change during the call.

## Inputs and kernel versions

- Text consumes the printed characters directly, including zero dots and
  hexadecimal depths. Original trace rows remain unchanged.
- Raw trace-cmd containers decode the common flag byte directly. The pinned
  libtraceevent latency formatter does not preserve all current combinations,
  so it is not used to normalize these columns. Event payload formatting still
  uses libtraceevent.
- Patched uftrace streams decode the same common fields and use the same
  five-character format, including dots for zero depth and lowercase hexadecimal
  digits for nonzero depth. User calls without recorded kernel latency data do
  not gain invented kernel flags.
- In the upstream flag layout, bit `0x02` was reused for lazy rescheduling from
  Linux 6.13; older captures use it for unavailable IRQ support. Binary inputs
  use the **recorded** kernel release, never the analysis host's version. An
  ambiguous set bit with missing/unrecognized release is rejected with an
  actionable error rather than silently dropped or guessed. Raw captures obtain
  the release from their saved uname; uftrace uses `kernel_settings.kernel_release`.
- Version detection follows upstream layout changes, not arbitrary vendor
  backports. These tests cover this documented layout, not unknown future bits
  or every historical/vendor-specific format.

## Regression coverage

- Timeline decoding: all 256 common flag bytes × all 256 depth bytes, with
  independent expectations for IRQ/BH, all eight reschedule states, nested
  contexts and both depth nibbles.
- uftrace decoding: the same full Cartesian product for both legacy and modern
  interpretations of bit `0x02`, checking the complete five-character string
  as well as the decoded Timeline flags.
- Text parsing: every flag byte, every value in each depth nibble and representative
  combined depths, with/without absolute timestamps and with four/five columns.
- Native raw containers: the same flag/nibble cases in both endiannesses and
  32/64-bit word sizes, plus kernel-version boundaries and ambiguous metadata.
- Browser: all eight request combinations in detail badges, flags and hover;
  nested IRQ contexts; unavailable IRQ state; exact Text filtering and hints;
  existing preempt correction and raw-model immutability checks.

Run from the repository root:

```bash
FG_RAW_REQUIRED=1 PYTHONPATH=tests python3 -m unittest \
  test_parser test_timeline test_uftrace_import test_raw -v
```
