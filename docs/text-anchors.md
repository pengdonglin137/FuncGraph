# Native trace-line links and safe copying

## Reproduced gaps

The published sample's line numbers were clickable spans without an `href` or
keyboard focus. This prevented native link-menu copying and opening a line in a
new tab. Without `navigator.clipboard`, clicking a number updated the URL but
raised an uncaught `writeText` exception. An older denied clipboard request also
initiated a legacy copy after a newer line had already been copied, replacing
the clipboard with the previous line's URL. The baseline Chromium observations
are retained at `build/text-anchors/before.json`.

## Interaction rules

Generated line numbers are native anchors to their unchanged `#L<n>` IDs, using
the same delegated interaction handler rather than per-row listeners. Normal
appearance and geometry stay unchanged; only keyboard focus adds an inset,
theme-aware one-pixel outline. No toolbar controls, permanent status rows,
per-record metadata fields or dependencies are added.

- Plain click and Enter locate the original row and silently attempt to copy
  its complete URL. They do not expand source or fold calls. Double-clicking
  the number does not fall through to the row's source double-click handler.
- Ctrl/Cmd/Shift-modified clicks and middle-click retain browser link behavior;
  the existing capture-phase Alt-click shortcut still opens Timeline.
- Native link menus remain available for copying/opening the URL. Native line
  navigation also works in a plain inline Text report with JavaScript disabled;
  this is not a claim that all interactive viewer features work without scripts.
- Missing, throwing or denied modern clipboard support uses the existing safe
  fallback. Legacy `false`/exceptions return failure without leaving a textarea
  or disturbing selection/focus. The URL remains usable when copying fails.
- Line links and the toolbar Copy button share a request generation. A newer
  copy request, or a different current URL for a pending line link, prevents an
  older denied request from initiating a legacy copy over newer content. Both
  cross-entry directions are regression tested. Already submitted browser
  Clipboard API requests cannot be cancelled; OS completion ordering is outside
  this guarantee.

The clipboard path does not load report data or scan trace rows. Existing anchor
navigation can still reveal folded ancestors, restore hidden event evidence and
load an unmounted Text chunk when the destination requires it. This change does
not claim to remove those existing navigation costs.

## Regression scope

`tests/test_text_anchors.py` exercises native and mixed reports; plain,
standalone, sidecar and genuinely windowed Text; mouse/Enter/nested labels;
double-click isolation; missing/denied/throwing clipboard support; success and
failure of legacy copying; stale requests and intervening navigation; native
Ctrl-click tabs and the Alt-click Timeline shortcut; both themes, mobile and
keyboard focus; bounded window DOM and absence of extra data requests.

Modern and legacy paths also write Chromium's actual clipboard and compare the
read-back URL exactly. Failure and response-order cases use controlled stubs,
not claims to have tested every browser/OS permission configuration.

```bash
PYTHONPATH=tests python3 -m unittest test_text_anchors test_text_copy \
  test_textviewport -q
```

## Complete-report evidence

A Chromium probe uses the complete local 65,751,022-byte `write4.html` report,
rebasing only its line-number controls, two link CSS rules and current frontend
script/errno dictionary. This is not CLI regeneration or new capture/source-type
evidence. The source file remains unchanged, and all 38,938 row IDs, projected
trace text and return metadata remain intact. Complete embedded source bodies,
source mappings, Timeline identity and Timeline data retain their byte hashes.

All 38,938 controls are native links to their original IDs. Denied legacy
copying fails safely; intervening URL changes and a newer complete trace copy
prevent stale line-link fallbacks. No temporary textarea or page error remains,
and saved source/fold state is retained. Shortening the shared title makes the
line-control markup 88,982 bytes smaller in this report; this is a markup-size
observation, not a DOM-memory or startup-speed claim. The probe and results are
retained at `build/text-anchors/verify_real.py` and `real-report.json`.

## Validation artifacts

- The final anchors/Copy/Text-viewport run passes **55 tests**, including
  **17 new line-link tests**. A separate final copy/anchor run passes **30 tests**.
- The wider browser/Text/source/Timeline/report/sidecar/uftrace/BTF regression
  run passes **905 tests**, with matching `FG_VMLINUX` set and no skips.
- Matching-vmlinux sample/usage/BTF/clone/name/inline checks pass **77 tests**,
  without skips.
- Wheel and sdist builds succeed. Installed-wheel smoke runs **14 tests**, with
  **4 optional native/extras checks skipped**, including an assertion that the
  generated report contains native line links rather than clickable spans.
  An installed-CLI-generated report also passes the complete browser link and
  cross-action fallback probe without page errors.
- The regenerated native and mixed samples preserve all 739 rows / 10 events
  and 1,504 rows / 74 events respectively, with unchanged recorded row metadata/
  text, source pools/maps and Timeline identity/data apart from `stats.build_time`.
  Sources remain pinned to Linux v7.4-rc2 at
  `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`.
- Complete-sample browser probes verify every native link destination, denied
  copy handling and cross-action stale-fallback suppression, while preserving
  recorded rows, source/fold state and Timeline data with no page errors.
- Native/mixed previews, expanded Text, folding, Timeline detail and help
  screenshots are regenerated and remain byte-identical. No PNG churn is kept.

Logs and complete sample-fidelity results are retained under
`build/text-anchors/`.
