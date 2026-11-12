# Unified capture validation — 2026-11-12

## Real target and provenance

Tests use the authorized QEMU x86_64 guest running Linux `7.4.0-rc2`, with
`samples/demo_mixed.c` built using `-pg -g -O0 -fno-omit-frame-pointer`.
The guest's GNU build ID from `/sys/kernel/notes` exactly matches
`/vol_1t/Qemu/linux/x86_64/vmlinux`:

```text
0610f676d5858c29282a4a1a63a5a0aa0644f4e2
```

The kernel source is `/vol_1t/Qemu/linux` at
`7475ea8b58616b33c04afb976cf0fbaad9dd63fb` (`v7.4-rc2`). Reports pin local
source to that commit and GitHub kernel links to the matching tag. User source
uses the separate copied `demo_mixed.c`, never the kernel browser.

## Same-run process lifecycle

Artifacts are retained locally in `build/captures/20261112-demo-mixed/` and are
not added as large binary files to Git:

| Recording | Calls | Kernel records independently verified | Report |
| --- | ---: | ---: | --- |
| Default syscall-root / 8-level mixed capture | 422,590: 27 user + 422,563 kernel | 845,388: 422,563 entries + 422,559 exits + 266 events | `mixed.html`, offline Timeline + embedded source, about 68 MiB |
| Focused exec/exit + UDP / 4-level capture | 558: 27 user + 531 kernel | 1,080: 531 entries + 529 exits + 20 events | `example.html`, offline Text + Timeline |

The corresponding exports are `trace.jsonl` and `example.jsonl`; original
recording directories are `mixed.data` and `mixed-example.data`. Both contain
actual `sched_process_exec` and `sched_process_exit` for the recorded workload,
with `exec < main entry < main exit < process exit`, no clock adjustment and
no fabricated startup frames. Both use a verified shared `mono` clock and
have no explicit lost-record or nonzero graph-overrun observations. The input
and output files compare byte-for-byte.

Entry/exit counts are intentionally not forced equal. The observed process
exit path and depth boundaries leave unmatched entries; byte-level agreement
does not prove a completely recorded call tree. The reports retain these
incomplete spans instead of inventing returns.

The focused scope is explicit and reproducible:

```bash
sudo "$(command -v fgcapture)" --mixed --kernel-depth 4 \
    -g '__x64_sys_sendto,__x64_sys_recvfrom,do_execveat_common,do_exit' \
    -o mixed-example.data -- ./demo_mixed input.bin output.bin
```

Its full elapsed-process coverage does not mean that unselected kernel
functions were recorded. The larger default capture retains all its records
in JSONL; its HTML deliberately omits per-line Text DOM, not calls, and keeps
source snippets. An earlier unrestricted depth-30 diagnostic recorded millions
of kernel calls and explicit losses. It is isolated under `diagnostics/`, not
presented as a lossless example. This evidence motivates the smaller default.

## Recovery and option checks

The final wrapper is exercised on real tracefs in six additional scenarios:

| Scenario | Result | State readback |
| --- | --- | --- |
| User-only recording | success; no tracefs mutation | 81 / 81 unchanged |
| Mixed `--no-args --no-sched` | success; lifecycle remains available | 81 / 81 restored |
| Uninstrumented native workload rejected | exit 1 | 81 / 81 restored |
| Focused mixed example | success | 81 / 81 restored |
| SIGINT after instrumented `main` signals readiness | exit 130; workload PID no longer exists | 81 / 81 restored |
| Kernel-only default argument probe | args enabled automatically with return addresses | 81 / 81 restored |

Before/after snapshots and restore diagnostics remain adjacent to the original
recordings. This tests ordinary completion, native failure and interruption,
not power loss, SIGKILL, kernel crashes or external tracers bypassing the lock.

## Browser and package checks

Both actual HTML reports are checked in Chromium, not only against generated
strings. They expose both spaces, a verified clock, the two real exec/exit
milestones and all 27 recorded user calls. `dispatch_datagram` shows five
named values (`fd`, `buffer`, `length`, `peer`, `peer_size`), a bounded
`send_frame+0x…/0x…` caller site, and embedded user call-site source. Kernel
details show BTF-correct arguments and clickable pinned kernel source links.
Neither report produces a JavaScript error. PNG previews and browser evidence
JSON are saved alongside the reports.

The focused automated suite contains 44 passing tests across command presets,
bundled-recorder resolution, old/missing kernel capabilities, empty/slow
argument probes, explicit arguments, scheduling, lock contention, active-session
refusal, failure restoration, signal-during-launch ownership, process-group
cleanup, real native user names/values/returns/call sites, and legacy shell
compatibility. Linux native wheels, Python-only wheels and sdist installation
also exercise the new `fgcapture` entry point outside the checkout.

The complete native/raw/browser suite passes **1,186 tests in 746.664 seconds**,
with three optional external-data checks skipped. Twine accepts the native
wheel, Python-only wheel and sdist. Fresh isolated installations run 14 smoke
tests each: the native package passes all 14, while Python-only wheel/sdist
each skip only the native-wheel-specific case. The native installed smoke
records through both `fguftrace` and the simplified `fgcapture --user` without
a recorder-path override and verifies captured names, values and call sites.
