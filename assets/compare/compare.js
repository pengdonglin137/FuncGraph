/* FuncGraph Compare view: per-function differences between a base trace and this one. */
window.fgViews = window.fgViews || {};
window.fgViews.compare = { el: 'compareView', data: ['cmpData'], init: function () {
    const D = window.fgData.read('cmpData');
    const root = document.getElementById('compareView');
    // row: 0 name | base 1 calls 2 incl 3 self 4 max 5 err | cur 6 calls 7 incl 8 self 9 max 10 err
    const COLS = [
        { k: 'name', t: 'function', f: r => r[0] },
        { k: 'calls', t: 'calls', b: 1, c: 6, num: true },
        { k: 'self', t: 'self time', b: 3, c: 8, time: true },
        { k: 'incl', t: 'inclusive', b: 2, c: 7, time: true },
        { k: 'mean', t: 'mean incl', b: r => r[1] ? r[2] / r[1] : 0, c: r => r[6] ? r[7] / r[6] : 0, time: true },
        { k: 'max', t: 'max', b: 4, c: 9, time: true },
        { k: 'err', t: 'errors', b: 5, c: 10, num: true },
    ];
    const SORTS = [
        ['absdiff', '|Δ|', 'Absolute Δ', 'absolute difference'],
        ['diff', 'Δ', 'Signed Δ', 'signed difference'],
        ['pct', 'Δ%', 'Percent Δ', 'percentage difference'],
        ['cur', 'current', 'Current', 'current value'],
        ['base', 'base', 'Base', 'base value'],
    ];
    const val = (col, r, side) => {
        const x = side === 'b' ? col.b : col.c;
        return typeof x === 'function' ? x(r) : r[x];
    };
    const PAGE_SIZE = 400, CSV_CHUNK_ROWS = 1024;
    let sortKey = 'self', sortBy = 'absdiff', query = null, onlyChanged = true, page = 0;
    let ROWS = D.rows;
    let matchingRows = [], population = null, maxDelta = 1, adjustedMode = false;
    let filterTimer = 0, queryText = '', queryNames = null, pendingQuery = null, nameIndex = null, composing = false, controlsOpen = false;
    let exportRequest = null;
    const fmt = ns => {
        const a = Math.abs(ns);
        if (a < 1000) return ns.toFixed(0) + ' ns';
        if (a < 1e6) return (ns / 1e3).toFixed(a < 1e4 ? 2 : 1) + ' us';
        if (a < 1e9) return (ns / 1e6).toFixed(a < 1e7 ? 2 : 1) + ' ms';
        return (ns / 1e9).toFixed(3) + ' s';
    };
    const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const pct = (b, c) => b ? ((c - b) / b * 100) : (c ? Infinity : 0);
    const fmtPct = p => p === Infinity ? 'new' : (p > 0 ? '+' : '') + p.toFixed(p > -10 && p < 10 ? 1 : 0) + '%';
    const sign = (d, time) => (d > 0 ? '+' : d < 0 ? '−' : '') + (time ? fmt(Math.abs(d)) : Math.abs(d));

    const [bn, cn] = D.names;
    root.innerHTML = `
      <div class="cmp-head">
        <div class="cmp-title">Compare <b>${esc(bn)}</b> → <b>${esc(cn)}</b></div>
        <div class="cmp-sum"></div>
      </div>
      <div class="cmp-bar">
        <button class="cmp-toggle" type="button" aria-controls="cmpControls" aria-expanded="false" hidden>Filters</button>
        <select class="cmp-metric" aria-label="Comparison metric" hidden>
          ${COLS.map(column => `<option value="${column.k}">${column.t === 'function' ? 'Function name' : column.t[0].toUpperCase() + column.t.slice(1)}</option>`).join('')}
        </select>
        <div class="cmp-controls" id="cmpControls">
        <input class="cmp-q" placeholder="filter function (regex)" spellcheck="false" aria-label="Filter function names by regular expression" aria-describedby="cmpFilterError">
        <select class="cmp-order" aria-label="Sort order" title="Sort order" hidden>
          ${SORTS.map(([value, , label]) => `<option value="${value}">${label}</option>`).join('')}
        </select>
        <label class="cmp-scope"><input type="checkbox" class="cmp-changed" checked> changed only</label>
        <span class="cmp-label cmp-sort-label">sort</span>
        <span class="cmp-sortby">
          ${SORTS.map(([value, label, , description]) => `<button type="button" data-v="${value}" class="${value === sortBy ? 'on' : ''}" aria-pressed="${value === sortBy}" aria-label="Sort by ${description}">${label}</button>`).join('')}
        </span>
        <label class="cmp-scope cmp-adj" style="display:none"><input type="checkbox"> subtract tracing overhead</label>
        </div>
        <button class="cmp-export" type="button"><span>Export CSV</span><span>Cancel CSV</span></button>
        <span class="cmp-compact-range" aria-hidden="true" hidden></span>
        <span class="cmp-count"></span>
      </div>
      <div class="cmp-note">Durations include tracing overhead; compare traces captured with the same options. Self time = inclusive minus traced children.</div>
      <div class="cmp-filter-status" hidden><span role="status"></span> <button class="cmp-cancel" type="button">Cancel</button></div>
      <div id="cmpFilterError" class="cmp-filter-error" role="alert" hidden></div>
      <div class="cmp-export-status" role="status"></div>
      <div class="cmp-empty" role="status" hidden>No functions match the current filters.</div>
      <div class="cmp-wrap" tabindex="0" role="region" aria-label="Function comparison table"><table class="cmp-table"><thead></thead><tbody></tbody></table></div>
      <div class="cmp-pages" hidden><span class="cmp-page-status" role="status" aria-live="polite" aria-atomic="true"></span>
        <div class="cmp-page-actions" role="group" aria-label="Comparison result pages">
          <span class="cmp-page-steppers"><button class="cmp-prev" type="button" aria-label="Previous comparison page">Previous</button>
          <button class="cmp-next" type="button" aria-label="Next comparison page">Next</button></span>
          <form class="cmp-page-jump">
            <label>Page <input class="cmp-page-number" type="number" inputmode="numeric" min="1" step="1" required aria-describedby="cmpPageTotal"></label>
            <span id="cmpPageTotal" class="cmp-page-total"></span><button type="submit" aria-label="Go to comparison page">Go</button>
          </form>
        </div></div>`;
    const tot = (i) => D.rows.reduce((s, r) => s + r[i], 0);
    const pill = (label, b, c, time) => {
        const d = c - b, p = pct(b, c);
        const cls = Math.abs(p) < 5 ? 'same' : d > 0 ? 'up' : 'down';
        return `<span class="cmp-pill ${cls}"><span class="k">${label}</span> ${time ? fmt(b) : b} → ${time ? fmt(c) : c} <b>${fmtPct(p)}</b></span>`;
    };
    root.querySelector('.cmp-sum').innerHTML =
        pill('traced time', D.window[0], D.window[1], true) +
        pill('calls', D.calls[0], D.calls[1], false) +
        pill('error chains', D.errors[0], D.errors[1], false) +
        (D.modes[0] !== D.modes[1] ? `<span class="cmp-pill warn">time modes differ: ${D.modes[0]} vs ${D.modes[1]}</span>` : '');

    const thead = root.querySelector('thead'), tbody = root.querySelector('tbody');
    const wrap = root.querySelector('.cmp-wrap');
    const pages = root.querySelector('.cmp-pages'), previousPage = root.querySelector('.cmp-prev'), nextPage = root.querySelector('.cmp-next');
    const pageStatus = pages.querySelector('.cmp-page-status'), pageJump = pages.querySelector('.cmp-page-jump');
    const pageNumber = pageJump.querySelector('.cmp-page-number'), pageTotal = pageJump.querySelector('.cmp-page-total');
    const resultCount = root.querySelector('.cmp-count'), compactRange = root.querySelector('.cmp-compact-range');
    const controls = root.querySelector('.cmp-controls'), controlsToggle = root.querySelector('.cmp-toggle');
    const sortButtons = root.querySelector('.cmp-sortby'), sortLabel = root.querySelector('.cmp-sort-label');
    const orderSelect = root.querySelector('.cmp-order');
    const compactControls = window.matchMedia('(max-width: 700px), (pointer: coarse)');
    const compactMetrics = window.matchMedia('(max-width: 700px), (max-height: 500px) and (max-width: 950px)');
    const metricSelect = root.querySelector('.cmp-metric');
    let projectedCompact = compactMetrics.matches, wideScrollLeft = 0, renderedHeader = null, headerSortKey = null;
    let readingPosition = null, tableWidth = 0, renderedBody = null;
    const filterInput = root.querySelector('.cmp-q'), filterError = root.querySelector('.cmp-filter-error');
    const exportButton = root.querySelector('.cmp-export');
    const exportStatus = root.querySelector('.cmp-export-status');
    const filterStatus = root.querySelector('.cmp-filter-status');
    const note = root.querySelector('.cmp-note'), measuredNote = note.textContent;
    function captureReadingPosition(scrollTop = wrap.scrollTop) {
        if (!scrollTop || !wrap.clientHeight) return null;
        const rows = tbody.rows, edge = thead.getBoundingClientRect().bottom;
        let first = 0, end = rows.length;
        while (first < end) {
            const middle = first + Math.floor((end - first) / 2);
            if (rows[middle].getBoundingClientRect().bottom <= edge + 0.5) first = middle + 1;
            else end = middle;
        }
        if (first === rows.length) return null;
        return { ordinal: first, inset: Math.max(0, edge - rows[first].getBoundingClientRect().top), scrollTop };
    }
    function restoreReadingPosition(position) {
        const row = position && tbody.rows[position.ordinal];
        if (!row) { wrap.scrollTop = 0; return; }
        const bounds = row.getBoundingClientRect();
        const inset = Math.min(position.inset, Math.max(0, bounds.height - 1));
        wrap.scrollTop += bounds.top + inset - thead.getBoundingClientRect().bottom;
    }
    function resize() {
        const compact = compactMetrics.matches;
        const projectionChanged = compact !== projectedCompact;
        const useToggle = compactControls.matches || compact;
        const hideControls = useToggle && !controlsOpen;
        const active = document.activeElement;
        const focusedControl = hideControls && controls.contains(active);
        const focusedToggle = !useToggle && active === controlsToggle;
        const switchSortFocus = useToggle ? sortButtons.contains(active) : active === orderSelect;
        if (hideControls && !controls.hidden) composing = false;
        controlsToggle.hidden = !useToggle;
        controlsToggle.setAttribute('aria-expanded', String(!hideControls));
        const filterActive = !!(queryText || filterInput.value || !onlyChanged || adjustedMode || sortKey !== 'self' || sortBy !== 'absdiff');
        controlsToggle.toggleAttribute('data-active', filterActive);
        controlsToggle.setAttribute('aria-label', filterActive ? 'Filters, active' : 'Filters');
        controlsToggle.title = filterActive ? 'Filters or sorting settings are active' : '';
        controls.hidden = hideControls;
        orderSelect.hidden = !useToggle;
        sortButtons.hidden = sortLabel.hidden = useToggle;
        const placeholder = useToggle ? 'name regex' : 'filter function (regex)';
        if (filterInput.placeholder !== placeholder) filterInput.placeholder = placeholder;
        if (window.fgCurrentView() === 'compare') {
            if (focusedControl) controlsToggle.focus({preventScroll:true});
            else if (focusedToggle) filterInput.focus({preventScroll:true});
            else if (switchSortFocus) (useToggle ? orderSelect : sortButtons.querySelector('.on')).focus({preventScroll:true});
        }
        if (projectionChanged) {
            if (!projectedCompact) wideScrollLeft = wrap.scrollLeft;
            if (!compact && document.activeElement === metricSelect) wrap.focus({preventScroll:true});
            projectedCompact = compact;
            renderTable();
            if (!compact) wrap.scrollLeft = wideScrollLeft;
        }
        metricSelect.hidden = !compact;
        const top = Math.max(0, wrap.getBoundingClientRect().top);
        const footer = pages.hidden ? 0 : pages.getBoundingClientRect().height;
        wrap.style.maxHeight = Math.max(160, window.innerHeight - top - footer - 12) + 'px';
        const width = wrap.clientWidth;
        if (projectionChanged || width !== tableWidth) {
            tableWidth = width;
            if (readingPosition) restoreReadingPosition(readingPosition);
            readingPosition = captureReadingPosition();
        }
    }
    function header(metrics) {
        const identity = projectedCompact ? metrics[0].k : 'wide';
        if (renderedHeader !== identity) {
            const focusedKey = thead.contains(document.activeElement) ? document.activeElement.closest('th[data-k]')?.dataset.k : null;
            renderedHeader = identity;
            headerSortKey = null;
            let markup = '<tr><th rowspan="2" data-k="name" class="l"><button type="button">function</button></th>';
            for (const column of metrics) markup += `<th colspan="3" data-k="${column.k}"><button type="button">${column.t}</button></th>`;
            const labels = projectedCompact ? `<th class="sub" title="${esc(bn)}">Base</th><th class="sub" title="${esc(cn)}">Current</th><th class="sub">Δ</th>` :
                `<th class="sub">${esc(bn)}</th><th class="sub">${esc(cn)}</th><th class="sub">Δ</th>`;
            thead.innerHTML = markup + '</tr><tr>' + labels.repeat(metrics.length) + '</tr>';
            if (focusedKey) (thead.querySelector(`th[data-k="${focusedKey}"] button`) || wrap).focus({preventScroll:true});
        }
        if (headerSortKey === sortKey) return;
        headerSortKey = sortKey;
        for (const heading of thead.querySelectorAll('th[data-k]')) {
            const active = heading.dataset.k === sortKey;
            heading.classList.toggle('on', active);
            if (active) heading.setAttribute('aria-sort', sortKey === 'name' ? 'ascending' : 'descending');
            else heading.removeAttribute('aria-sort');
        }
    }
    function scopedRows() {
        const matcher = queryNames || query;
        if (population?.source === ROWS && population.matcher === matcher && population.onlyChanged === onlyChanged) return population.rows;
        const filtered = matcher || onlyChanged ? ROWS.filter(row => {
            if (queryNames ? !queryNames.has(row[0]) : query && !query.test(row[0])) return false;
            return !onlyChanged || row[1] !== row[6] || row[2] !== row[7] || row[3] !== row[8] ||
                row[4] !== row[9] || row[5] !== row[10];
        }) : ROWS;
        const rows = filtered.length === ROWS.length ? ROWS : filtered;
        population = {source: ROWS, matcher, onlyChanged, rows};
        return rows;
    }
    function updateRows() {
        const col = COLS.find(c => c.k === sortKey) || COLS[2];
        const byName = col.k === 'name';
        const ranked = [];
        maxDelta = 1;
        for (const row of scopedRows()) {
            let key = row[0];
            if (!byName) {
                const base = val(col, row, 'b'), current = val(col, row, 'c'), delta = current - base;
                maxDelta = Math.max(maxDelta, Math.abs(delta));
                switch (sortBy) {
                    case 'diff': key = delta; break;
                    case 'pct': key = pct(base, current); break;
                    case 'cur': key = current; break;
                    case 'base': key = base; break;
                    default: key = Math.abs(delta);
                }
            }
            ranked.push({row, key});
        }
        ranked.sort(byName ? (left, right) => left.key < right.key ? -1 : left.key > right.key ? 1 : 0 :
            (left, right) => right.key - left.key);
        matchingRows = ranked.map(entry => entry.row);
        page = 0;
        render();
    }
    const barClass = delta => 'cmp-dbar ' + (delta > 0 ? 'up' : 'down');
    const barStyle = delta => 'width:' + (40 * Math.abs(delta) / maxDelta).toFixed(1) + 'px';
    function rowMarkup(row, metrics, barMetric) {
        let markup = `<tr><td class="l" title="${esc(row[0])}">${esc(row[0])}${row[11] ? ' <span class="cmp-tag">' + esc(row[11]) + '</span>' : ''}${!row[1] ? ' <span class="cmp-tag new">new</span>' : !row[6] ? ' <span class="cmp-tag gone">gone</span>' : ''}</td>`;
        for (const column of metrics) {
            const base = val(column, row, 'b'), current = val(column, row, 'c'), delta = current - base;
            const percent = pct(base, current);
            const cls = delta === 0 ? '' : (Math.abs(percent) < 5 && percent !== Infinity) ? 'small' : delta > 0 ? 'up' : 'down';
            const bar = column === barMetric ? `<span class="${barClass(delta)}" style="${barStyle(delta)}"></span>` : '';
            markup += `<td>${column.time ? fmt(base) : base}</td><td>${column.time ? fmt(current) : current}</td>` +
                      `<td class="d ${cls}" title="${fmtPct(percent)}">${bar}${delta ? sign(delta, column.time) : ''}</td>`;
        }
        return markup + '</tr>';
    }
    function updateRowBar(node, row, metrics, oldIndex, newIndex) {
        const bar = oldIndex < 0 ? null : node.cells[3 * (oldIndex + 1)].firstElementChild;
        if (newIndex < 0) { bar?.remove(); return; }
        const column = metrics[newIndex], delta = val(column, row, 'c') - val(column, row, 'b');
        const marker = bar || document.createElement('span');
        marker.className = barClass(delta);
        marker.setAttribute('style', barStyle(delta));
        if (oldIndex !== newIndex) node.cells[3 * (newIndex + 1)].prepend(marker);
    }
    function renderTable() {
        const first = page * PAGE_SIZE, rows = matchingRows.slice(first, first + PAGE_SIZE);
        const col = COLS.find(column => column.k === sortKey) || COLS[2];
        const metrics = projectedCompact ? [col.k === 'name' ? COLS[2] : col] : COLS.slice(1);
        const projection = projectedCompact ? metrics[0].k : 'wide';
        const barIndex = projectedCompact ? -1 : metrics.indexOf(col);
        wrap.classList.toggle('cmp-compact-metrics', projectedCompact);
        header(metrics);
        metricSelect.value = sortKey;

        const previous = renderedBody?.projection === projection ? renderedBody : null;
        const nodes = rows.map(row => {
            const node = previous?.nodes.get(row);
            if (node) previous.nodes.delete(row);
            return node;
        });
        const markup = entries => entries.map(row => rowMarkup(row, metrics, metrics[barIndex])).join('');
        if (!nodes.some(Boolean)) {
            tbody.innerHTML = markup(rows);
        } else {
            const missing = rows.filter((_, index) => !nodes[index]);
            const parsed = missing.length ? document.createElement('tbody') : null;
            if (parsed) parsed.innerHTML = markup(missing);
            const newNodes = parsed ? Array.from(parsed.rows) : [];
            const updateBars = previous.barIndex !== barIndex || barIndex >= 0 && previous.scale !== maxDelta;
            const current = tbody.rows;
            let nextMissing = 0, reordered = nodes.length !== current.length;
            for (const [index, row] of rows.entries()) {
                if (!nodes[index]) nodes[index] = newNodes[nextMissing++];
                else if (updateBars) updateRowBar(nodes[index], row, metrics, previous.barIndex, barIndex);
                if (nodes[index] !== current[index]) reordered = true;
            }
            if (reordered) tbody.replaceChildren(...nodes);
        }
        renderedBody = {projection, barIndex, scale: maxDelta, nodes: new Map(rows.map((row, index) => [row, tbody.rows[index]]))};
    }
    function render() {
        const pageCount = Math.max(1, Math.ceil(matchingRows.length / PAGE_SIZE));
        page = Math.max(0, Math.min(page, pageCount - 1));
        const first = page * PAGE_SIZE, last = Math.min(first + PAGE_SIZE, matchingRows.length);
        renderTable();
        wrap.scrollTop = 0;
        readingPosition = null;
        const focused = document.activeElement;
        const rowRange = 'Rows ' + (matchingRows.length ? first + 1 : 0) + '–' + last + ' of ' + matchingRows.length;
        pageStatus.textContent = rowRange;
        compactRange.hidden = pageCount === 1;
        if (!compactRange.hidden) {
            compactRange.textContent = rowRange + (matchingRows.length < ROWS.length ? ' · ' + ROWS.length + ' total' : '');
        }
        if (pageNumber.max !== String(pageCount)) {
            pageNumber.max = pageCount;
            pageNumber.style.setProperty('--cmp-page-digits', String(pageCount).length);
            pageTotal.textContent = 'of ' + pageCount;
        }
        pageNumber.value = page + 1;
        pages.hidden = pageCount === 1;
        previousPage.disabled = first === 0;
        nextPage.disabled = last >= matchingRows.length;
        if (pages.hidden && pages.contains(focused)) wrap.focus({preventScroll:true});
        else if ((focused === previousPage || focused === nextPage) && focused.disabled) {
            (!previousPage.disabled ? previousPage : !nextPage.disabled ? nextPage : wrap).focus({preventScroll:true});
        }
        const countText = matchingRows.length + ' of ' + ROWS.length + ' functions';
        if (resultCount.textContent !== countText) resultCount.textContent = countText;
        root.querySelector('.cmp-empty').hidden = matchingRows.length !== 0;
        updateExportState();
        resize();
    }
    function goToPage(target) {
        if (!Number.isInteger(target) || target < 0 || target >= Math.ceil(matchingRows.length / PAGE_SIZE)) return;
        if (target === page) {
            pageNumber.value = page + 1;
            return;
        }
        page = target;
        render();
    }
    function updateExportState() {
        const building = !!exportRequest?.building;
        exportButton.toggleAttribute('data-exporting', building);
        exportButton.disabled = !building && (!matchingRows.length || filterInput.classList.contains('bad') || !!pendingQuery);
        const title = building ? 'Cancel CSV export in progress' :
            'Export all ' + matchingRows.length + ' matching functions in the current order; durations are in ns';
        if (exportButton.title !== title) exportButton.title = title;
    }
    function stopFilter(restore = false) {
        clearTimeout(filterTimer);
        const request = pendingQuery;
        pendingQuery = null;
        request?.controller.abort();
        filterStatus.hidden = true;
        wrap.removeAttribute('aria-busy');
        if (restore) {
            filterInput.value = queryText;
            clearFilterError();
        }
        updateExportState();
        resize();
    }
    function clearFilterError() {
        filterInput.classList.remove('bad');
        filterInput.removeAttribute('aria-invalid');
        filterError.hidden = true;
    }
    function filterFailed(message, invalid = false) {
        filterInput.classList.add('bad');
        if (invalid) filterInput.setAttribute('aria-invalid', 'true');
        filterError.textContent = message;
        filterError.hidden = false;
        updateExportState();
        resize();
    }
    function indexedNames() {
        if (!nameIndex) {
            const unique = new Set();
            for (const rows of [D.rows, D.rows_adj || []]) {
                for (const row of rows) unique.add(row[0]);
            }
            const strings = Array.from(unique);
            nameIndex = {strings, names: Int32Array.from(strings, (name, index) => index)};
        }
        return nameIndex;
    }
    async function runQuery(request, matcher) {
        try {
            const index = indexedNames(), signal = request.controller.signal;
            let hits = null;
            try {
                const background = matchSearchNamesWorker(index, index.strings, request.text, signal);
                if (background) hits = await background;
            } catch (error) {
                if (error.searchKind !== 'unavailable') throw error;
            }
            if (!hits) {
                if (signal.aborted) return false;
                filterStatus.querySelector('span').textContent = 'Filtering in page… Complex expressions may block; previous results are shown.';
                hits = new Uint8Array(index.strings.length);
                for (let position = 0; position < index.strings.length; position++) {
                    if (position % 256 === 0) {
                        await new Promise(resolve => setTimeout(resolve, 0));
                        if (signal.aborted) return false;
                    }
                    hits[position] = matcher.test(index.strings[position]) ? 1 : 0;
                }
            }
            if (pendingQuery !== request || signal.aborted) return false;
            const names = new Set();
            for (let position = 0; position < hits.length; position++) {
                if (hits[position]) names.add(index.strings[position]);
            }
            query = null;
            queryNames = names;
            queryText = request.text;
            stopFilter();
            updateRows();
            return true;
        } catch (error) {
            if (pendingQuery !== request || request.controller.signal.aborted) return false;
            stopFilter();
            filterFailed('Background filter failed. Press Enter to retry or edit the expression; previous results are still shown.');
            return false;
        }
    }
    function applyQuery() {
        clearTimeout(filterTimer);
        if (pendingQuery?.text === filterInput.value) return pendingQuery.promise;
        stopFilter();
        clearFilterError();
        if (filterInput.value === queryText) { updateExportState(); resize(); return true; }
        let nextQuery;
        try { nextQuery = filterInput.value ? new RegExp(filterInput.value, 'i') : null; }
        catch (error) {
            filterFailed('Invalid regular expression. Fix the filter before exporting; the last valid results are still shown.', true);
            return false;
        }
        if (nextQuery && searchNeedsWorker(filterInput.value)) {
            const request = {text: filterInput.value, controller: new AbortController(), promise: null};
            pendingQuery = request;
            filterStatus.querySelector('span').textContent = 'Filtering function names… Previous results are shown.';
            filterStatus.hidden = false;
            wrap.setAttribute('aria-busy', 'true');
            updateExportState();
            resize();
            request.promise = runQuery(request, nextQuery);
            return request.promise;
        }
        query = nextQuery;
        queryNames = null;
        queryText = filterInput.value;
        updateRows();
        return true;
    }
    function csvCell(value) {
        if (typeof value === 'number') return String(value);
        const text = String(value);
        return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    }
    function setExportStatus(message, failed = false) {
        const layoutChanged = exportStatus.classList.contains('bad') !== failed;
        exportStatus.classList.toggle('bad', failed);
        exportStatus.textContent = message;
        if (layoutChanged) resize();
    }
    function finishExport(request, message = '', failed = false) {
        if (!request || exportRequest !== request) return;
        const focused = document.activeElement === exportButton;
        exportRequest = null;
        updateExportState();
        if (focused && exportButton.disabled) (controls.hidden ? controlsToggle : filterInput).focus({preventScroll:true});
        setExportStatus(message, failed);
    }
    async function buildCsv(request, rows) {
        const columns = COLS.slice(1), headings = ['function'];
        const bySpace = D.rows[0]?.length > 11;
        if (bySpace) headings.push('space');
        for (const column of columns) {
            const suffix = column.k + (column.time ? '_ns' : '');
            headings.push('base_' + suffix, 'current_' + suffix, 'delta_' + suffix);
        }
        const encoder = new TextEncoder(), parts = [encoder.encode('\ufeff' + headings.join(',') + '\r\n')];
        let sliceStart = performance.now();
        for (let first = 0; first < rows.length; first += CSV_CHUNK_ROWS) {
            if (request.building && (first === 0 || performance.now() - sliceStart >= 6)) {
                await new Promise(resolve => setTimeout(resolve, 0));
                if (exportRequest !== request) return null;
                sliceStart = performance.now();
            }
            const lines = [], last = Math.min(first + CSV_CHUNK_ROWS, rows.length);
            for (let index = first; index < last; index++) {
                const row = rows[index];
                const name = /^[\s]*[=+\-@]|^[\t\r\n]/.test(row[0]) ? "'" + row[0] : row[0];
                const cells = [name];
                if (bySpace) cells.push(row[11]);
                for (const column of columns) {
                    const base = val(column, row, 'b'), current = val(column, row, 'c');
                    cells.push(base, current, current - base);
                }
                lines.push(cells.map(csvCell).join(','));
            }
            parts.push(encoder.encode(lines.join('\r\n') + '\r\n'));
        }
        return new Blob(parts, {type: 'text/csv;charset=utf-8'});
    }
    function downloadCsv(blob, adjusted) {
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url;
        link.download = 'funcgraph-compare-' + (adjusted ? 'adjusted' : 'raw') + '.csv';
        link.hidden = true;
        root.appendChild(link);
        try { link.click(); }
        finally {
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
    }
    async function exportCsv() {
        if (exportRequest) {
            if (exportRequest.building) finishExport(exportRequest, 'CSV export cancelled.');
            return;
        }
        const request = {building: false};
        exportRequest = request;
        setExportStatus('');
        try {
            if (!await applyQuery() || exportRequest !== request || !matchingRows.length) return;
            const rows = matchingRows, adjusted = adjustedMode;
            if (rows.length > CSV_CHUNK_ROWS) {
                request.building = true;
                updateExportState();
                setExportStatus('Preparing CSV for ' + rows.length + ' functions.');
            }
            const blob = await buildCsv(request, rows);
            if (!blob || exportRequest !== request) return;
            downloadCsv(blob, adjusted);
            finishExport(request, request.building ? 'CSV download started.' : '');
        } catch (error) {
            finishExport(request, 'Could not prepare the CSV. Try exporting again.', true);
        } finally {
            finishExport(request);
        }
    }
    function selectMetric(key) {
        if (sortKey === key) return;
        sortKey = key;
        updateRows();
    }
    thead.addEventListener('click', ev => {
        const heading = ev.target.closest('th[data-k]');
        if (heading) selectMetric(heading.dataset.k);
    });
    metricSelect.addEventListener('change', () => selectMetric(metricSelect.value));
    previousPage.addEventListener('click', () => goToPage(page - 1));
    nextPage.addEventListener('click', () => goToPage(page + 1));
    pageJump.addEventListener('submit', event => {
        event.preventDefault();
        goToPage(pageNumber.valueAsNumber - 1);
    });
    pageNumber.addEventListener('keydown', event => {
        event.stopPropagation();
        if (event.key === 'Escape') {
            event.preventDefault();
            pageNumber.value = page + 1;
        }
    });
    controlsToggle.addEventListener('click', () => { controlsOpen = !controlsOpen; resize(); });
    function selectSortMode(value) {
        if (sortBy === value) return;
        sortBy = value;
        orderSelect.value = value;
        sortButtons.querySelectorAll('button').forEach(button => {
            const selected = button.dataset.v === value;
            button.classList.toggle('on', selected);
            button.setAttribute('aria-pressed', String(selected));
        });
        updateRows();
    }
    orderSelect.addEventListener('change', () => selectSortMode(orderSelect.value));
    sortButtons.addEventListener('click', event => {
        const button = event.target.closest('button');
        if (button) selectSortMode(button.dataset.v);
    });
    if (D.rows_adj) {
        const l = root.querySelector('.cmp-adj');
        l.style.display = '';
        l.title = 'overhead estimates: ' + D.overhead.map(o => o ? o.child + ' ns/child, ' + o.self + ' ns/call' : 'none').join(' | ');
        l.querySelector('input').addEventListener('change', ev => {
            adjustedMode = ev.target.checked;
            ROWS = adjustedMode ? D.rows_adj : D.rows;
            note.textContent = adjustedMode ? 'Table/CSV durations have tracing overhead estimates subtracted; summary pills stay measured. Self time = inclusive minus traced children.' : measuredNote;
            updateRows();
        });
    }
    root.querySelector('.cmp-changed').addEventListener('change', ev => { onlyChanged = ev.target.checked; updateRows(); });
    filterInput.addEventListener('input', event => {
        stopFilter();
        if (composing || event.isComposing) return;
        filterTimer = setTimeout(applyQuery, 150);
    });
    filterInput.addEventListener('compositionstart', () => { composing = true; stopFilter(); });
    filterInput.addEventListener('compositionend', () => { composing = false; filterTimer = setTimeout(applyQuery, 150); });
    filterInput.addEventListener('keydown', ev => {
        ev.stopPropagation();
        if (composing || ev.isComposing) return;
        if (ev.key === 'Enter') { ev.preventDefault(); applyQuery(); }
        else if (ev.key === 'Escape') { ev.preventDefault(); stopFilter(true); }
    });
    root.querySelector('.cmp-cancel').addEventListener('click', () => { stopFilter(true); (controls.hidden ? controlsToggle : filterInput).focus(); });
    exportButton.addEventListener('click', exportCsv);
    wrap.addEventListener('scroll', () => {
        const top = wrap.scrollTop;
        if (top !== (readingPosition?.scrollTop || 0) && wrap.clientWidth === tableWidth) {
            readingPosition = captureReadingPosition(top);
        }
    }, {passive:true});
    window.addEventListener('resize', () => { if (window.fgCurrentView() === 'compare') resize(); });
    compactControls.addEventListener('change', () => { if (window.fgCurrentView() === 'compare') resize(); });
    updateRows();
    return { resize: resize, suspend: () => {
        composing = false;
        pageNumber.value = page + 1;
        stopFilter(true);
        if (exportRequest) finishExport(exportRequest);
        else setExportStatus('');
    } };
} };
