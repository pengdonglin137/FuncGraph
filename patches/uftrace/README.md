# Maintained uftrace patches

Base: upstream **v0.20**, `842ed731248a` (full pin in the Git submodule).
`third_party/uftrace` stays pristine. `series` is authoritative; the patches
are generated from committed changes in `build/uftrace-dev`, never hand-edited.

| Patch | Purpose | Upstream status / removal condition |
| --- | --- | --- |
| 0001 | Probe optional function-graph args/retval/retaddr and scheduling events; save effective kernel options/version/clock. | Not submitted. Remove after equivalent capability/readback support lands upstream. |
| 0002 | Decode `fgraph_retaddr_entry`; retain format-driven raw context without replay PID reassignment. | Not submitted. Remove after the complete raw export context is available upstream. |
| 0003 | Versioned `dump --fgraph` JSONL export of original streams, recorded clock, sparse CPU identity, perf events and explicit completion. | Not submitted. Remove after an upstream export preserves this contract (or migrate the importer explicitly). |
| 0004 | Fix sorted kernel symbol ranges on Pi; final native validation fixes declaration diagnostics and rejects malformed/trailing user records instead of reporting completion. | Not submitted. Remove after equivalent upstream fixes land and the regression tests pass. |
| 0005 | Export supported user scalar arguments/returns from saved specifications while preserving raw payload bytes. | Not submitted. Remove when an upstream export offers both forms. |
| 0006 | Decode relative argument locations with older libtraceevent headers without requiring its newer flag enum. | Not submitted. Remove when upstream handles both supported metadata forms. |
| 0007 | Export bounded code-symbol/module metadata for original return PCs using only saved kallsyms; preserve raw addresses. | Not submitted. Remove when upstream offers the same captured-symbol provenance and boundary semantics. |
| 0008 | Preserve complete executable/library paths through recording, build-ID annotation and replay, including whitespace, long paths and explicit empty build IDs. | Not submitted. Remove when upstream preserves the same map-path and build-ID boundaries and the recording/offline-export regressions pass. |
| 0009 | Enable ARM64 two-NOP patchable entries without Capstone, reject ordinary-instruction relocation without a disassembler, and report the limited capability as `patchable`. | Not submitted. Remove when upstream offers the same bounded support and the native entry/argument regressions pass. |
| 0010 | Export native user CALLSITE events with original bytes/return PCs, saved-session caller symbols and exact `PC - 1` source positions; keep callee entries separate. | Not submitted. Remove when upstream's structured export preserves the same evidence and the call-site regressions pass. |
| 0011 | Decode saved automatic scalar argument/return specifications using native replay's integer/hexadecimal formatting, while retaining original bytes and unsupported-format fallbacks. | Not submitted. Remove when upstream's structured export supports automatic scalar values and the native regressions pass. |

The full base SHA is `842ed731248a9daded38774d8fb26c7b09474461`, locked in both
the gitlink and `third_party/versions.json`. Build scripts reject mismatches;
normal builds never track an upstream branch. See [dependency pins](../../third_party/README.md).

Linux platform wheels include the native executable and recording libraries as
separate programs, together with GPL-2.0 `COPYING`, the fully patched corresponding
source, base pin and patch hashes. Python-only wheels and sdists contain no native
binary. `fguftrace` supplies the relocatable library path; report directory imports
select this bundled executable automatically. This integration does not select
a project-wide license.

See [the integration guide](../../docs/uftrace.md),
[中文说明](../../docs/uftrace.cn.md), and the
[implementation plan](../../docs/plans/2026-11-09-user-kernel-tracing-plan.md).
