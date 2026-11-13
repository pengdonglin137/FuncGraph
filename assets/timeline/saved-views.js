const SAVED_VIEWS_LIMIT = 50, SAVED_VIEWS_BYTES = 1024 * 1024;
let savedViewsStore = null, savedViewsGeneration = 0, savedViewsBusy = false;
let savedViewsReturnFocus = null;

function savedViewsIdentity() {
    const element = document.getElementById('fgTimelineIdentity');
    const identity = element && JSON.parse(element.textContent);
    if (typeof identity !== 'string' || !/^[a-f0-9]{64}$/.test(identity))
        throw new Error('This report has no data fingerprint. Regenerate it before saving views.');
    return identity;
}
function validateSavedViews(value, identity) {
    const invalid = () => { throw new Error('Invalid saved-view file. No views were changed.'); };
    const object = item => item !== null && typeof item === 'object' && !Array.isArray(item);
    const coordinate = number => typeof number === 'number' && Number.isFinite(number) && Math.abs(number) <= Number.MAX_SAFE_INTEGER;
    const interval = item => Array.isArray(item) && item.length === 2 && item.every(coordinate) && item[1] > item[0];
    const text = (item, limit) => typeof item === 'string' && item.length <= limit;
    const trackIds = new Set(ORDER.map(track => track.id));
    const task = index => Number.isInteger(index) && trackIds.has(index);
    if (!object(value) || value.format !== 'funcgraph.saved-views' || value.version !== 1) invalid();
    if (value.report !== identity) throw new Error('These views belong to different report data. Open the matching report; no views were changed.');
    if (!Array.isArray(value.views)) invalid();
    if (value.views.length > SAVED_VIEWS_LIMIT) throw new Error('A report can keep at most 50 saved views. No views were changed.');
    const ids = new Set();
    const views = value.views.map(entry => {
        if (!object(entry) || !text(entry.id, 80) || !/^[\w-]+$/.test(entry.id) || ids.has(entry.id) ||
            !text(entry.name, 100) || !entry.name.trim() || !text(entry.note, 2000)) invalid();
        ids.add(entry.id);
        const state = entry.state;
        if (!object(state) || !interval(state.view) || !Number.isInteger(state.selected) || state.selected < -1 || state.selected >= NA ||
            !text(state.filter, 4096) || !text(state.search, 512) || typeof state.hideUnmatched !== 'boolean' ||
            typeof state.inline !== 'boolean' || typeof state.adjusted !== 'boolean' || state.inline && !HAS_INL || state.adjusted && !sDurAdj ||
            state.selected >= N && !state.inline && !isGhost(state.selected) ||
            !Array.isArray(state.collapsed) || state.collapsed.length > ORDER.length || !state.collapsed.every(task) ||
            new Set(state.collapsed).size !== state.collapsed.length) invalid();
        if (state.range !== null && (!object(state.range) || !interval([state.range.s, state.range.e]) ||
            state.range.track !== null && (!task(state.range.track) || state.range.track === -1))) invalid();
        if (state.scroll !== null && (!object(state.scroll) || !task(state.scroll.track) || !coordinate(state.scroll.offset) || state.scroll.offset < 0)) invalid();
        return { id: entry.id, name: entry.name.trim(), note: entry.note,
            state: { view: state.view.slice(), selected: state.selected, filter: state.filter, search: state.search,
                hideUnmatched: state.hideUnmatched, inline: state.inline, adjusted: state.adjusted,
                collapsed: state.collapsed.slice(), range: state.range && { s: state.range.s, e: state.range.e, track: state.range.track },
                scroll: state.scroll && { track: state.scroll.track, offset: state.scroll.offset } } };
    });
    return { format: 'funcgraph.saved-views', version: 1, report: identity, views };
}
function savedViewId() { return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2); }
function savedViewsRead() {
    if (savedViewsStore) return savedViewsStore;
    const identity = savedViewsIdentity();
    savedViewsStore = { key: 'fgTl.savedViews.' + identity, data: { format: 'funcgraph.saved-views', version: 1, report: identity, views: [] }, raw: null, readable: true, warning: '', error: '' };
    let stored;
    try { stored = localStorage.getItem(savedViewsStore.key); }
    catch (error) { savedViewsStore.readable = false; savedViewsStore.warning = 'Browser storage is unavailable. Views stay in this tab only; export them to keep a copy.'; }
    if (stored !== null && stored !== undefined) {
        try {
            if (stored.length > SAVED_VIEWS_BYTES) throw new Error('Stored views exceed the size limit. Existing storage was not changed.');
            savedViewsStore.data = validateSavedViews(JSON.parse(stored), identity);
            savedViewsStore.raw = stored;
        } catch (error) { savedViewsStore.error = 'Stored views could not be read. Existing storage was not changed. ' + error.message; }
    }
    return savedViewsStore;
}
function savedViewsWrite(views) {
    const store = savedViewsRead();
    if (store.error) throw new Error(store.error);
    if (views.length > SAVED_VIEWS_LIMIT) throw new Error('Keep up to 50 views per report. Export or remove a view first.');
    const next = validateSavedViews({ ...store.data, views }, store.data.report), encoded = JSON.stringify(next);
    if (new Blob([encoded]).size > SAVED_VIEWS_BYTES) throw new Error('Saved views exceed 1 MiB. Export or remove a view first.');
    if (store.readable) {
        let current;
        try { current = localStorage.getItem(store.key); }
        catch (error) { store.readable = false; }
        if (store.readable && current !== store.raw)
            throw new Error('Saved views changed in another tab. Export this tab’s views, then reload the report before editing. No stored views were overwritten.');
    }
    try {
        if (!store.readable) throw new Error('unavailable');
        localStorage.setItem(store.key, encoded); store.raw = encoded; store.warning = '';
    } catch (error) { store.warning = 'Browser storage is unavailable or full. Views stay in this tab only; export them to keep a copy.'; }
    store.data = next;
}
function captureSavedView() {
    if (window.fgFilterProgress && !window.fgFilterProgress.error) throw new Error('Wait for filtering to finish, or cancel it, before saving a view.');
    if ((window.fgFilterState?.text || '').length > 4096 || elSearch.value.length > 512)
        throw new Error('Shorten the applied filter (4096 characters) or Find expression (512 characters) before saving this view.');
    const track = visibleTracks[firstTrackEndingAfter(elScroll.scrollTop)];
    return { view: [view.s, view.e], selected: sel, filter: window.fgFilterState?.text || '', search: elSearch.value,
        hideUnmatched, inline: inlineView, adjusted: adjOn, collapsed: ORDER.filter(task => task.collapsed).map(task => task.id),
        range: range && { s: range.s, e: range.e, track: range.track },
        scroll: track ? { track: track.id, offset: Math.max(0, elScroll.scrollTop - track.y) } : null };
}
function savedViewsMessage(message) { savedViewsDialog.querySelector('[data-saved-status]').textContent = message; }
function savedViewsSetBusy(busy) {
    savedViewsBusy = busy;
    savedViewsDialog.setAttribute('aria-busy', String(busy));
    for (const control of savedViewsDialog.querySelectorAll('button:not([data-saved-close]), input, textarea')) control.disabled = busy;
    if (!busy) renderSavedViews();
}
function renderSavedViews() {
    const store = savedViewsRead(), list = savedViewsDialog.querySelector('[data-saved-list]');
    list.replaceChildren();
    savedViewsDialog.querySelector('[data-saved-empty]').hidden = !!store.data.views.length;
    savedViewsDialog.querySelector('[data-saved-export]').disabled = !store.data.views.length || savedViewsBusy;
    savedViewsDialog.querySelector('[data-saved-save]').disabled = !!store.error || savedViewsBusy;
    savedViewsDialog.querySelector('[data-saved-import]').disabled = !!store.error || savedViewsBusy;
    savedViewsDialog.querySelector('[data-saved-storage]').textContent = store.error || store.warning;
    for (const entry of store.data.views) {
        const row = document.createElement('li'), heading = document.createElement('div');
        row.className = 'tl-saved-row'; heading.className = 'tl-saved-row-head';
        const restore = document.createElement('button');
        restore.type = 'button'; restore.className = 'tl-saved-name'; restore.dataset.savedRestore = entry.id;
        restore.textContent = entry.name; restore.title = 'Restore this view'; restore.disabled = savedViewsBusy;
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'tl-btn'; remove.dataset.savedRemove = entry.id;
        remove.textContent = 'Remove'; remove.setAttribute('aria-label', 'Remove view ' + entry.name); remove.disabled = savedViewsBusy;
        heading.append(restore, remove); row.append(heading);
        const context = document.createElement('p');
        context.className = 'tl-saved-context';
        context.textContent = (MODE === 'count' ? 'Call order · ' : MODE === 'compressed' ? 'Packed position · ' : 'Time · ') +
            fmtDur(entry.state.view[0]) + ' → ' + fmtDur(entry.state.view[1]) +
            (entry.state.range ? ' · Selected range' : entry.state.selected >= 0 ? ' · ' + nameOf(entry.state.selected) : '') +
            (entry.state.filter ? ' · Filter: ' + entry.state.filter : '') +
            (entry.state.adjusted ? ' · Overhead-subtracted estimate' : '');
        row.append(context);
        if (entry.note) {
            const note = document.createElement('p'); note.className = 'tl-saved-note'; note.textContent = entry.note; row.append(note);
        }
        list.append(row);
    }
}
async function restoreSavedView(entry) {
    if (window.fgFilterProgress && !window.fgFilterProgress.error) throw new Error('Wait for filtering to finish, or cancel it, before restoring a view.');
    const state = entry.state, input = document.getElementById('filterInput');
    if (state.filter && !input) throw new Error('This report has no filtering controls. Regenerate it with --filter to restore this view.');
    const generation = ++savedViewsGeneration;
    savedViewsSetBusy(true);
    savedViewsMessage('Restoring view…');
    try {
        const expected = state.filter.trim();
        if ((window.fgFilterState?.text || '').trim() !== expected) {
            input.value = expected;
            const result = await (expected ? applyFilter() : clearFilter());
            if (generation !== savedViewsGeneration || !savedViewsDialog.open) return;
            if (result === false || window.fgFilterProgress || (window.fgFilterState?.text || '').trim() !== expected)
                throw new Error('The saved filter could not be applied. Retry after the report data is available.');
        }
        if (generation !== savedViewsGeneration || !savedViewsDialog.open) return;
        if (hideUnmatched !== state.hideUnmatched) { hideUnmatched = state.hideUnmatched; applyTextFilter(); }
        if (adjOn !== state.adjusted) {
            setAdjusted(state.adjusted);
            const button = tlRoot.querySelector('[data-act=adj]');
            button.textContent = 'Overhead: ' + (adjOn ? 'subtracted' : 'measured'); button.classList.toggle('on', adjOn);
            colorCache.clear(); updateHeatLegend();
        }
        inlineView = state.inline; applyView();
        const inlineButton = tlRoot.querySelector('[data-act=inl]');
        inlineButton.textContent = 'Inline: ' + (inlineView ? 'shown' : 'hidden'); inlineButton.classList.toggle('on', inlineView);
        const collapsed = new Set(state.collapsed);
        for (const task of ORDER) task.collapsed = collapsed.has(task.id);
        AN.kind = null; range = null;
        select(state.selected);
        if (state.range) { range = { ...state.range }; showRangeSummary(); }
        view.s = state.view[0]; view.e = state.view[1];
        layout();
        const track = state.scroll && ORDER.find(task => task.id === state.scroll.track && !task.hidden);
        elScroll.scrollTop = track ? track.y + Math.min(state.scroll.offset, track.h) : 0;
        if (elSearch.value !== state.search) { elSearch.value = state.search; applySearch(state.search); }
        elFtext.value = state.filter; renderFilterControls();
        hideTip(); draw(); savedViewsDialog.close();
        toast('Restored “' + entry.name + '”');
    } finally {
        if (generation === savedViewsGeneration) savedViewsSetBusy(false);
    }
}
function exportSavedViews() {
    const store = savedViewsRead(), url = URL.createObjectURL(new Blob([JSON.stringify(store.data, null, 2) + '\n'], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url; link.download = 'funcgraph-views-' + store.data.report.slice(0, 12) + '.json';
    tlRoot.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    savedViewsMessage('Exported view positions, filters and notes. Share the matching report separately; notes may contain private information.');
}
function closeSavedViews() { if (savedViewsDialog?.open) savedViewsDialog.close(); }
function showSavedViews() {
    if (savedViewsDialog?.open) return;
    try { savedViewsRead(); } catch (error) { toast(error.message); return; }
    savedViewsReturnFocus = document.activeElement;
    if (!savedViewsDialog) {
        savedViewsDialog = document.createElement('dialog');
        savedViewsDialog.className = 'tl-saved-views';
        savedViewsDialog.setAttribute('aria-labelledby', 'tlSavedViewsTitle');
        savedViewsDialog.setAttribute('aria-describedby', 'tlSavedViewsHint');
        savedViewsDialog.innerHTML = `<header><h3 id="tlSavedViewsTitle">Saved views</h3><button class="tl-close" type="button" data-saved-close aria-label="Close saved views">×</button></header>
            <p id="tlSavedViewsHint">Keep a position, selected call or range, and applied filter. Views are saved in this browser for this report only.</p>
            <form><label>Name<input name="name" maxlength="100" required autocomplete="off"></label>
            <details class="tl-saved-note-entry"><summary>Add a note (optional)</summary><label>Note<textarea name="note" maxlength="2000" rows="3"></textarea></label></details>
            <button class="tl-btn" type="submit" data-saved-save>Save current view</button></form>
            <p data-saved-status role="status" aria-live="polite"></p><p data-saved-storage role="note"></p>
            <p data-saved-empty>No saved views yet. Save a useful call or time range so you can return to it later.</p>
            <ul data-saved-list aria-label="Saved views"></ul>
            <details class="tl-saved-transfer"><summary>Import / export</summary><p>Transfer views and notes, not trace data. Import requires matching report data.</p>
            <button class="tl-btn" type="button" data-saved-export>Export views</button><button class="tl-btn" type="button" data-saved-import>Import views…</button>
            <input type="file" accept=".json,application/json" data-saved-file hidden></details>`;
        tlRoot.append(savedViewsDialog);
        savedViewsDialog.querySelector('form').addEventListener('submit', event => {
            event.preventDefault();
            if (savedViewsBusy) return;
            try {
                const form = event.currentTarget, name = form.elements.name.value.trim();
                if (!name) throw new Error('Give this view a name.');
                const entry = { id: savedViewId(), name, note: form.elements.note.value, state: captureSavedView() };
                const existing = savedViewsRead().data.views;
                if (existing.some(view => view.name === entry.name && view.note === entry.note && JSON.stringify(view.state) === JSON.stringify(entry.state))) {
                    savedViewsMessage('This view is already saved.'); return;
                }
                savedViewsWrite([...existing, entry]);
                renderSavedViews(); savedViewsMessage('Saved “' + name + '”.');
                form.elements.name.select();
            } catch (error) { savedViewsMessage(error.message); }
        });
        savedViewsDialog.addEventListener('click', event => {
            const control = event.target.closest('button');
            if (!control || control.disabled) return;
            if (control.hasAttribute('data-saved-close')) { savedViewsDialog.close(); return; }
            if (savedViewsBusy) return;
            const store = savedViewsRead();
            if (control.dataset.savedRestore) {
                const entry = store.data.views.find(view => view.id === control.dataset.savedRestore);
                if (entry) restoreSavedView(entry).catch(error => savedViewsMessage(error.message));
            } else if (control.dataset.savedRemove) {
                try {
                    const index = store.data.views.findIndex(view => view.id === control.dataset.savedRemove);
                    savedViewsWrite(store.data.views.filter(view => view.id !== control.dataset.savedRemove));
                    renderSavedViews(); savedViewsMessage('Removed the saved view. Trace data was not changed.');
                    const remaining = savedViewsDialog.querySelectorAll('[data-saved-restore]');
                    (remaining[Math.min(index, remaining.length - 1)] || savedViewsDialog.querySelector('input[name=name]')).focus();
                } catch (error) { savedViewsMessage(error.message); }
            } else if (control.hasAttribute('data-saved-export')) exportSavedViews();
            else if (control.hasAttribute('data-saved-import')) savedViewsDialog.querySelector('[data-saved-file]').click();
        });
        savedViewsDialog.querySelector('[data-saved-file]').addEventListener('change', async event => {
            const file = event.target.files[0]; event.target.value = '';
            if (!file || savedViewsBusy) return;
            const generation = ++savedViewsGeneration;
            savedViewsSetBusy(true);
            try {
                if (file.size > SAVED_VIEWS_BYTES) throw new Error('View files must not exceed 1 MiB. No views were changed.');
                const store = savedViewsRead(), imported = validateSavedViews(JSON.parse(await file.text()), store.data.report);
                if (generation !== savedViewsGeneration || !savedViewsDialog.open) return;
                const views = store.data.views.slice(), same = entry => JSON.stringify({ name: entry.name, note: entry.note, state: entry.state });
                const content = new Set(views.map(same)), ids = new Set(views.map(entry => entry.id));
                for (const entry of imported.views) {
                    const signature = same(entry);
                    if (content.has(signature)) continue;
                    if (ids.has(entry.id)) entry.id = savedViewId();
                    ids.add(entry.id); content.add(signature); views.push(entry);
                }
                const added = views.length - store.data.views.length;
                savedViewsWrite(views); savedViewsMessage('Imported ' + added + ' view' + (added === 1 ? '' : 's') + '; existing views were kept.');
            } catch (error) {
                if (generation === savedViewsGeneration) savedViewsMessage('Import failed: ' + error.message + ' Existing views were not changed.');
            } finally {
                if (generation === savedViewsGeneration) savedViewsSetBusy(false);
            }
        });
        savedViewsDialog.addEventListener('close', () => {
            ++savedViewsGeneration;
            if (savedViewsBusy) {
                if (window.fgFilterProgress) fgCancelPendingFilter();
                const filter = window.fgFilterState?.text || '', input = document.getElementById('filterInput');
                if (input) input.value = filter;
                elFtext.value = filter; renderFilterControls();
            }
            savedViewsSetBusy(false);
            if (window.fgCurrentView() !== 'timeline') return;
            if (savedViewsReturnFocus?.isConnected && savedViewsReturnFocus.getClientRects().length) savedViewsReturnFocus.focus({ preventScroll: true });
            else focusTimelineControl();
        });
    }
    setToolbarOptions(false); closeCallActions(); hideTip();
    renderSavedViews(); savedViewsMessage('');
    const name = savedViewsDialog.querySelector('input[name=name]');
    name.value = sel >= 0 ? nameOf(sel).slice(0, 100) : range ? 'Selected range' : 'Current view';
    savedViewsDialog.querySelector('textarea').value = '';
    savedViewsDialog.querySelector('.tl-saved-note-entry').open = false;
    savedViewsDialog.querySelector('.tl-saved-transfer').open = false;
    savedViewsDialog.showModal(); name.focus(); name.select();
}
