# Timeline input-method and keyboard safety

## Reproduced behavior

The previous Timeline handlers treated some input-method and browser editing
keys as viewer commands. Chromium checks on a generated scheduling report
reproduced four separate problems:

- Escape with `isComposing` cleared Find and moved focus out of the field.
- Enter with legacy IME `keyCode=229` selected the next call.
- Enter with `isComposing` in the filter field submitted the unfinished draft
  and removed focus.
- A composing `n` delivered to the canvas selected the next search result.

The before-state observations are retained locally in
`build/timeline-input/before.json`. Composing input also used the normal
150 ms debounce, allowing incomplete text to start regex matching or show an
invalid-regex warning before the user had committed the text.

## Interaction contract

Timeline's capture-phase shortcuts and the Find/filter field handlers share
one guard for already-consumed events, `isComposing`, legacy IME key code 229
and Ctrl/Cmd/Alt combinations. Shift remains available for normal previous-hit
navigation and native backwards focus traversal. The guard precedes menu and
full-screen Escape handling, so composing or modified Escape does not become
a request to close either. Browser-owned shortcuts remain browser-owned.

Find tracks composition start/end. Starting composition cancels its debounce
and outstanding cooperative search work, including a queued navigation.
The existing status reads **Composing…**, with busy semantics and disabled
previous/next buttons; no new toolbar button or permanent status row is added.
Existing committed highlighting and the selected call remain unchanged while
text is being composed. Intermediate empty or invalid expressions are not
applied. After composition ends, the normal debounce applies only the latest
text and shared-filter scope, without navigating automatically. The final
`input` event coalesces with `compositionend` rather than repeating a search.

If Text is opened before composition ends, the completed input remains paused
until Timeline returns. Ordinary Enter/Shift+Enter still navigate, ordinary
Escape still clears Find or restores the filter draft, and native buttons keep
Enter/Space activation. No trace records, parameters, source locations, regex
matching rules, indexes or shared-filter semantics are changed.

## Verification and limits

```bash
PYTHONPATH=tests python3 -m unittest \
  test_timeline_keyboard test_timeline_filters test_filter_controls test_help -v
```

Browser regressions cover single-file reports with/without shared filtering,
full/windowed sidecar Text, Find/filter/canvas keys, Tools and page full screen,
normal navigation and button activation, invalid/empty/intermediate Unicode
composition, shared-filter updates, view suspension, narrow/desktop layouts
and both themes. A 5,000-call report with a controlled clock forces an actual
cooperative-index yield before cancelling it, then verifies that the old
navigation never selects a call and a new committed query can finish normally.

Composition and legacy-key scenarios dispatch DOM composition/input/keyboard
events in Chromium. An additional CDP `Input.imeSetComposition` / `Input.insertText`
check drives Chromium's native composition path, observes trusted composition
start/input events and verifies commitment without automatic navigation. These
are not a claim of manual coverage of every OS IME or all browser engines.
Page-full-screen handling is verified separately from
the browser's own full-screen Escape behavior, which a page cannot override.
Tests bound unnecessary search work before commitment, not overall startup,
memory use, regex worst-case latency or a measured end-to-end speedup. Large
reports still retain their original data and existing main-thread limits.

Old HTML embeds its previous handlers. Regenerate it with current FuncGraph;
there is no need to recapture the trace.
