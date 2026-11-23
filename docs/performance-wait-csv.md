# Wait-chain CSV responsiveness

For an already resolved chain below 2,048 waits, **Export chain CSV** downloads
immediately. An unresolved chain, or a larger known chain, uses the shared
Timeline CSV scheduler. Each batch resolves only the waits it needs to write.
An unknown total displays processed rows, not an estimated percentage.

Paging and row navigation remain available, and the shared filter does not
change the wait scope. Switching to Text pauses work; returning to Timeline
resumes it. **Cancel CSV**, closing the panel, or selecting another chain
discards the pending file. Already discovered links for the same root remain
available for browsing. A download starts only once the terminal reason and
all rows are known.

## Measurements

Three interleaved A/B runs in headless Chromium 145 with 4× CPU throttling.
Time is measured after the panel opens and first page is resolved. Figures are
medians in ms; file ready means the download trigger, not a disk write.

| Fixture | Click handler | Maximum frame gap | File ready |
| --- | ---: | ---: | ---: |
| 50,000 temporal links in a synthetic chain | 507.6 → 1.7 | 516.5 → 22.4 | 506.9 → 619.3 |
| One wait in real `ping3` | 3.4 → 3.1 | 16.7 → 16.4 | 3.3 → 3.1 |

Both fixtures matched in length and SHA-256 across versions. The large export
takes longer to finish but leaves the interface responsive. The benchmark
does not measure Timeline startup or initial lookup-index construction.
Reproduce with `python3 tests/benchmark_wait_csv.py before.html after.html`
(use `--rows 0` for the real capture). Local results:
`build/benchmarks/20261120-wait-csv/`.

## Cold waker lookup

Timeline detects ascending and descending completed waits during the existing
interval-index pass, without copying or sorting the track. Either order supports
binary search. For a dense unordered track, the first query scans in bounded
batches and caches its result. A different query on the same track prepares a
reusable end-time index in batches. Neither changes the original record order
or IDs. The lookup excludes open waits and breaks end-time ties by the lowest
recorded index, including exact matches at the wakeup timestamp.

Wait pages and CSV share pending work. The panel shows **Preparing wait links…**
only when a page needs a cold lookup, keeps the current rows visible while
changing pages, and does not replace already displayed rows on initial load.
Switching to Text pauses work; closing or replacing the panel discards pending
work. The wait selection, terminal reason, and exported bytes do not change.

Five interleaved A/B runs in headless Chromium 145 at 4× CPU throttling. A
synthetic track contains 200,000 waits in a seeded random order. The CSV starts
from the already opened first page and reaches that track during export.
Figures are medians in ms; page ready measures installed rows, and CSV ready is
the download trigger.

| Interaction | Handler | Maximum frame gap | Ready |
| --- | ---: | ---: | ---: |
| Open Wait chain, cold unordered waker | 327.9 → 14.0 | 345.2 → 29.5 | 327.9 → 46.8 |
| Next page, cold unordered waker | 302.3 → 2.4 | 313.2 → 22.0 | 302.4 → 35.1 |
| CSV, cold unordered waker | 2.2 → 2.1 | 287.8 → 21.3 | 309.9 → 30.3 |
| Open one real `ping3` wait | 15.3 → 14.7 | 38.1 → 36.9 | 15.3 → 14.7 |

Timeline preparation from Text measured 306.1 → 313.1 ms in the synthetic
first-page fixture and 393.0 → 388.3 ms in the real capture. Displayed row IDs,
terminal status, and CSV SHA-256 matched across versions. In a separate export
of 50,000 ordered links, the maximum frame gap stayed near 25 ms and CSV bytes
also matched. Reproduce the cold test with
`python3 tests/benchmark_wait_open.py before.html after.html --rounds 5`
(use deferred-Text single files; `--rows 0` uses their original data).
Local results: `build/benchmarks/20261120-wait-lookup/`.
