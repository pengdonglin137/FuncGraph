# Structured returns in Text

## Reproduced gap

The uftrace importer displayed an exit's return token in its Text row but left
`FtraceLine.ret_value` empty. Consequently, known kernel errors were absent from
the `ret` dropdown and shared filter, and collapsed calls lost their return
summary. The published mixed report had zero `data-ret` or `data-fold-ret`
attributes despite five BTF-classified Timeline error observations.

## Evidence and presentation

- `UftraceLine.ret_value` carries the displayed scalar for exits only. Kernel
  values use the existing BTF width/sign formatter. User values require a saved
  `user_retval`; opaque byte payloads are never decoded by this projection.
  Zero and false remain meaningful values. Void returns have no scalar.
- `UftraceLine.ret_errno` is explicitly an integer or `None`, taken from the
  existing Timeline error rows. Text projection does not reclassify values or
  add BTF lookups. User negatives, unknown types, unsigned/Boolean/enum values
  and signed comparison results do not become errors merely by looking negative.
- The report's return-option enumeration and `data-ret` attributes use one helper.
  Its native function_graph fallback preserves the existing number interpretation;
  the structured `None` override prevents that fallback from guessing an errno.
  `ret:all` still means known error codes, not every scalar return.
- `data-fold-ret` retains the displayed scalar independently of errno classification,
  so user returns and non-error kernel values get neutral summaries. Error color
  depends on `data-ret`, not the leading sign or raw hexadecimal bit pattern.
- Original JSONL, return register bits, user raw payloads, source mappings,
  timestamps and Timeline data are unchanged. This adds Text metadata, not a
  capture option or stream-schema revision. Existing recordings with saved
  returns can be re-rendered; absent observations cannot be recovered.

## Regression scope

`tests/test_uftrace_text_returns.py` exercises signed widths, unsigned and enum
returns, Boolean/void results, target-width pointers, comparison functions,
unknown BTF, disabled returns, missing exits and orphan exits. It also checks
user/kernel name collisions, user-only and kernel-only recordings, user scalars
versus raw bytes, original-bit preservation and no repeated classification.

CLI cases cover plain Text, standalone and sidecar output. Browser cases cover
fold summaries/error color, zero/false/user negatives, `ret:all` and exact errno
queries, shared Timeline scope and immutable Timeline payloads in standalone,
sidecar and experimental windowed Text.

```bash
PYTHONPATH=tests python3 -m unittest test_uftrace_text_returns -q
```

## Complete sample checks

Both reports use matching Linux v7.4-rc2 sources at
`7475ea8b58616b33c04afb976cf0fbaad9dd63fb`.
The native sample retains all 739 rows / 10 events and all row attributes.
The mixed sample retains all 1,504 rows / 74 events, now with 321 return summaries
and five errno rows matching its Timeline observations. All recorded row text,
source pools/maps and Timeline identity/data remain identical except regenerated
`stats.build_time`. Only mixed Text return attributes are allowed to differ.
The local full-payload check and its results are retained at
`build/uftrace-text-returns/verify_samples.py` and `sample-fidelity.json`.

Browser checks on both complete published samples additionally exercise the
event toggle, return filtering, clearing and Text/Timeline round trips. Each
matches exactly five errno observations without mutating Timeline data; the
mixed report also verifies neutral user-zero and error-colored kernel summaries.
Results are in `build/uftrace-text-returns/rendered-samples.json`.

The final focused run passes **62 tests**, including all **10 structured Text
return tests**. The matching-vmlinux sample/usage/BTF/clone/name/inline run passes
**77 tests**. Wheel/sdist builds succeed; installed-wheel smoke runs **14 tests**
with **4 optional native/extras checks skipped**. Regenerated default sample,
Text, Timeline detail and help screenshots remain byte-identical.

The wider browser/Text/source/Timeline/report/sidecar/prefilter/uftrace/BTF run
finishes **868 tests** with no failures and one default skip: the optional
`test_btf.BtfTest.test_real_vmlinux` check requires `FG_VMLINUX`. That exact check
is then run separately against the matching sample vmlinux and passes.
Logs are retained under `build/uftrace-text-returns/`.
