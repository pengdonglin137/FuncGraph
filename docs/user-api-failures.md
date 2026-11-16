# User API failure hints

User return conventions differ from Linux kernel `-errno`. Reports now distinguish
**an API failure hint**, **a directly returned error number**, and **an unknown
outcome**, without changing capture behavior or adding toolbar buttons.

## Reading a report

- A decoded `open()` return of `-1` gets `API failure · errno not recorded`, not
  `EPERM`. The recorded `-1` stays intact. A kernel child returning `-ENOENT`
  does not prove that this user call's errno was ENOENT.
- A decoded NULL from `fopen()` or `opendir()` gets `API failure · NULL`, with
  the same explicit missing-errno explanation in details.
- `pthread_create/join/detach` and `posix_memalign` return error numbers directly.
  A recorded `22` can therefore show `API error · EINVAL (22)`. This is not a
  captured thread-local errno. Unknown positive codes retain their number alone.
- Details, hover, orange Errors overlays and Text return/fold summaries reuse the
  existing visual language. Each hint is derived once during import. A details
  note identifies the symbol-matched API rule and its evidence limits.
- **Errors → User API failure hints** groups by original function identity and
  rule/result, retaining every occurrence. Pages contain at most 200 groups;
  Previous / Next replaces rows, preserves occurrence positions, and does not
  rescan/sort the trace. The user API CSV exports every occurrence in the current
  shared-filter scope, including unloaded groups. Formula-like string cells use
  the existing spreadsheet-safety quote prefix; JSON retains exact return strings.
- Error navigation defaults to **all** when user hints exist. **to user** continues
  to select only kernel chains that reach the syscall boundary. User hints do not
  acquire an origin/propagation role, handled fate or kernel chain membership.
- User-only reports keep the existing Errors overlay and navigation available,
  without showing empty kernel-chain counters or an inapplicable kernel CSV button.

Use the existing shared Filter in either view:

```text
result:failure
space:user AND result:failure
space:kernel AND result:failure
```

`result:failure` matches classified failure **return records**, not every row in
their call trees. It does not implement a `result:success` classifier. Keep
`ret:all` or `ret:="-22"` for the existing kernel errno classification; user `-1`
and direct positive error numbers never enter `data-ret` or the kernel errno menu.
Old folding preferences still apply: unfold a parent to inspect its matching
return rows. Filtering, sorting or changing views does not reinterpret returns.

## Short runnable example

On Linux x86_64, build the [small failure workload](../samples/demo_user_errors.c)
with instrumentation and disable builtin replacements so `strcmp` stays observable:

```bash
cc -g -O0 -pg -fno-builtin samples/demo_user_errors.c -o /tmp/demo_user_errors
GMON_OUT_PREFIX=/tmp/funcgraph-user-errors-gmon fgcapture --user --no-sched \
    -o /tmp/user-errors.data \
    -R 'open@retval/i32' -R 'fopen@retval/x64' -R 'read@retval/i64' \
    -R 'posix_memalign@retval/i32' -R 'strcmp@retval/i32' \
    -R 'application_result@retval/i32' -- /tmp/demo_user_errors
funcgraph --no-config --uftrace /tmp/user-errors.data --user-src "$PWD" \
    --timeline --enable-fold --filter --export /tmp/user-errors.json \
    --output /tmp/user-errors.html
```

Use a fresh recording directory. Installed native wheels use bundled uftrace;
`--uftrace-bin PATH` is only an override. No vmlinux or tracefs access is needed
for this user-only example. On AArch64, replace `-pg` with
`-fpatchable-function-entry=2`; fgcapture adds the corresponding dynamic patch mode.
The expected hints are `open`, `fopen`, `read` and `posix_memalign`; `strcmp` and
the application-specific negative result remain neutral, and `main` exits zero.
The `-R` options are explicit return-spec overrides, not new classifier options.

## Rules and conservative exclusions

The explicit allowlists live in `fglib/user_returns.py`. The initial `-1` rules
cover common file, descriptor, metadata and socket APIs, including `open/read/write`,
`pread/pwrite`, `lseek`, `stat`, `unlink`, `dup`, `pipe`, `socket/bind/connect`,
`accept`, `send/recv` and `sendto/recvfrom`. NULL rules cover `fopen/fdopen/freopen`
and `opendir/fdopendir`, including listed large-file variants. Only the four APIs
listed above use the direct-error-number rule; there is no blanket `pthread_*`
or negative-value rule. Exact `@plt` and numeric `@GLIBC_*` symbol suffixes may be
normalized, but arbitrary suffixes, C++ names and application wrappers are not.

These are **symbol-name convention matches**, not verified shared-library
identities. A custom function named `open` can follow another contract. The UI
calls these hints and states that identity and error propagation are not verified.
The importer uses the paired entry's symbol, not an inconsistent exit name.

The following stay unclassified:

- Unknown functions, `main()` exit status and application-specific wrappers.
- Comparison results such as `strcmp() == -1` or `memcmp() < 0`.
- NULL from `malloc/calloc/realloc`, which needs size/contract context; NULL from
  `readdir`, which can mean end of directory; ambiguous `getpriority() == -1`.
- Robust mutex return conventions such as EOWNERDEAD, which can mean the lock was
  acquired. No prefix-based mutex failure inference is performed.
- Missing exits, raw-byte-only returns and raw register bit patterns. Only saved
  decoded `user_retval` is used. Hexadecimal all-ones values are not reinterpreted
  as signed `-1`; choose a suitable saved return specification when recording.
- An unknown outcome is **not** success. BTF/DWARF types alone do not supply an
  arbitrary function's success/failure contract.

The rules follow the Linux manuals for [errno](https://man7.org/linux/man-pages/man3/errno.3.html),
[open](https://man7.org/linux/man-pages/man2/open.2.html),
[fopen](https://man7.org/linux/man-pages/man3/fopen.3.html),
[pthread_create](https://man7.org/linux/man-pages/man3/pthread_create.3.html),
[posix_memalign](https://man7.org/linux/man-pages/man3/posix_memalign.3.html),
[malloc/realloc](https://man7.org/linux/man-pages/man3/malloc.3.html), and
[pthread_mutex_lock](https://man7.org/linux/man-pages/man3/pthread_mutex_lock.3p.html).

## Compatibility and evidence

No new uftrace probe, errno read, process-memory access, CLI option or stream
schema is introduced. Capture timestamps, addresses, source positions, payloads
and decoded values are unchanged. The stream remains `funcgraph.uftrace.v1`.
Derived hints are stored in `uftrace.user_details[entry_line].failure`, separately
from `errs` and `chains`. `--export analysis.json` adds `user_api_failures` when
nonempty. Existing function-statistics/Compare `errors` counts remain kernel
chain statistics; user hints are not silently added to those metrics.

Text uses `data-user-error`, independently of `data-ret`. Reports with user hints
use Text index version 6, extending the complete version-5 fields with `user-error`.
Reports without hints retain version 5; the viewer accepts all earlier supported
versions. Full Text, deferred Text and experimental Windowed Text use the same
classification and validate the new field against fetched paint blocks. Timeline
only reports retain hints without loading Text. Native kernel reports are unchanged.

Regenerate HTML to update old reports that already saved decoded returns. No
renderer can recover a return or errno that was never captured. In particular,
the published mixed sample's failed `receive_frame()` is an application wrapper,
and its nested `recvfrom()` has no decoded return; neither is guessed from the
other or relabeled as a proven user API failure.

`tests/test_user_returns.py` covers conventions and exclusions, aliases, missing
and raw-only returns, orphans, loss/overrun, cross-thread isolation, paired symbol
authority, original-record fidelity, JSON/CSV separation, Full/deferred/Windowed
Text, filtering, folding, details/hover, navigation and complete paginated export.
Run it together with the existing kernel-return, report, sidecar and filter tests.

## Validation on 2026-11-16

- The 70 focused return/index/inline-style regressions pass, including real
  uftrace captures and browser checks. Both menu/button interactions and shortcuts
  are exercised; user-only hints do not depend on kernel return capabilities.
- The full `tests/run.sh` run completes 1772 tests with no failures and three
  optional skips, with raw, native uftrace and sample-browser checks required.
  The real-vmlinux and real-sample-clone BTF checks were then enabled explicitly
  against the matching kernel and both pass. The remaining optional old-size
  kernel-tool check is described below, not presented as a passing test.
- The sdist, pure wheel and bundled-recorder x86_64 wheel build and pass twine
  checks. Each isolated wheel install runs 15 package smoke tests, including raw;
  the pure wheel has its one expected native-payload skip, and the native wheel
  has no skips. Package assets match the checked-out implementation exactly.
- The committed C workload was also recorded with an installed Linux x86_64
  wheel, without `--uftrace-bin`, vmlinux or tracefs. Its bundled recorder produced
  the four expected hints. `strcmp` and the application-specific negative return
  remain neutral; JSON has no fabricated errno or kernel propagation chain.
- Both sample HTML reports were regenerated against Linux source commit
  `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` (`v7.4-rc2`). Every Text row/attribute,
  event, source payload, report identity and Timeline model matches the previous
  report, except the non-evidence `stats.build_time`. Native tail-call evidence is
  retained. The five regenerated screenshots are byte-identical.
- The optional kernel-tool comparison has an existing older-build fixture-size
  mismatch for `vfs_read`, `mutex_unlock` and `path_lookupat`. The kernel reference
  rejects those stale lengths. Using selectors from the current kernel gives
  matching source locations for all three. The unrelated test and resolver were
  not changed or relaxed.

Local captures, HTML, exports, build/install logs and sample-fidelity checks are
under the ignored `build/user-api-failures/` directory. `installed.data`,
`installed.html` and `installed-analysis.json` form the installed-recorder example;
the source and reproducible recording commands are included above.
