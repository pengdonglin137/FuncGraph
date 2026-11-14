/* FuncGraph Timeline: detail panel, range summary, wiring, init */
function pill(cls, txt, title) { return '<span class="tl-pill ' + cls + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(txt) + '</span>'; }
function kv(k, v, cls) { return '<span class="k">' + k + '</span><span class="v' + (cls ? ' ' + cls : '') + '">' + v + '</span>'; }
function lineLink(n, label) { return '<a class="tl-link" data-line="' + n + '">' + (label || 'line ' + n) + '</a>'; }
function detailToken(kind, value) { return '<span class="tl-code-' + kind + '">' + esc(value) + '</span>'; }
function detailSymbolHtml(value) {
    const site = value.match(/^(.+?)(\+0[xX][\da-fA-F]+\/0[xX][\da-fA-F]+)(.*)$/);
    if (site) return detailToken('symbol', site[1]) + detailToken('offset', site[2]) + esc(site[3]);
    const address = value.match(/^(0[xX][\da-fA-F]+)(.*)$/);
    if (address) return detailToken('raw', address[1]) + esc(address[2]);
    return detailToken('symbol', value);
}
function detailValueHtml(value, parameters = false) {
    const tokens = /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b[A-Za-z_]\w*(?=\s*=(?!=))|(?<![\w.])[-+]?(?:0[xX][\da-fA-F]+|\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)(?![\w.])|\b(?:NULL|nullptr|true|false|void)\b/g;
    let html = '', offset = 0;
    for (const match of value.matchAll(tokens)) {
        const token = match[0], end = match.index + token.length;
        let kind = null;
        if (token[0] === '"' || token[0] === "'") kind = 'string';
        else if (/^[A-Za-z_]/.test(token) && /^\s*=/.test(value.slice(end))) kind = parameters ? 'parameter' : null;
        else if (/^[A-Za-z_]/.test(token)) kind = 'keyword';
        else kind = 'number';
        html += esc(value.slice(offset, match.index)) + (kind ? detailToken(kind, token) : esc(token));
        offset = end;
    }
    return html + esc(value.slice(offset));
}
function detailArgumentUnit(html) { return '<span class="tl-argument">' + html + '</span>'; }
function detailNamedArgumentsHtml(value, names) {
    if (!Array.isArray(names) || !names.length) return null;
    const parts = [];
    let offset = 0;
    for (let position = 0; position < names.length; position++) {
        const name = names[position] === null ? 'slot' + (position + 1) : names[position];
        if (typeof name !== 'string' || !value.startsWith(name + '=', offset)) return null;
        const match = value.slice(offset + name.length + 1).match(/^("(?:\\.|[^"\\])*"|-?\d+|0x[\da-fA-F]+)(?=, |$)/);
        if (!match) return null;
        const literal = match[0], end = offset + name.length + 1 + literal.length;
        const separator = end < value.length ? ',' : '';
        parts.push(detailArgumentUnit(detailToken('parameter', name) + '=' + detailValueHtml(literal) + separator));
        offset = end + (separator ? 2 : 0);
    }
    return offset === value.length ? parts.join(' ') : null;
}
function detailArgumentsHtml(value, names) {
    const named = detailNamedArgumentsHtml(value, names);
    if (named !== null) return named;
    const parts = [], closing = [];
    let quote = '', escaped = false, offset = 0;
    for (let position = 0; position < value.length; position++) {
        const character = value[position];
        if (quote) {
            if (escaped) escaped = false;
            else if (character === '\\') escaped = true;
            else if (character === quote) quote = '';
        } else if (character === '"' || character === "'") quote = character;
        else if ('([{'.includes(character)) closing.push({ '(': ')', '[': ']', '{': '}' }[character]);
        else if (')]}'.includes(character)) {
            if (closing.pop() !== character) return detailArgumentUnit(detailValueHtml(value, true));
        } else if (character === ',' && !closing.length) {
            parts.push(value.slice(offset, position + 1));
            offset = position + 1;
        }
    }
    if (quote || closing.length) return detailArgumentUnit(detailValueHtml(value, true));
    parts.push(value.slice(offset));
    return parts.map(part => {
        const leading = part.match(/^\s*/)[0], trailing = part.slice(leading.length).match(/\s*$/)[0];
        const body = part.slice(leading.length, part.length - trailing.length);
        return esc(leading) + (body ? detailArgumentUnit(detailValueHtml(body, true)) : '') + esc(trailing);
    }).join('');
}
const PROCESS_PAGE = 100;
const processView = { page: 0 };
function showProcessEvents(reset = true) {
    const reuse = AN.kind === 'process' && elDetail.classList.contains('show') && elDetail.querySelector('.tl-process-table');
    AN.kind = 'process';
    range = null;
    if (reset) processView.page = 0;
    if (!reuse) openDetail('<div class="tl-detail-head tl-process-head"><span class="tl-detail-title">Recorded exec / exit</span>' +
        '<span class="sp"></span><button class="tl-btn" data-process-export>Export CSV</button>' +
        '<button class="tl-close" title="Close (Esc)">×</button></div>' +
        '<div class="tl-process-tools"><span role="status" aria-live="polite" aria-atomic="true"></span>' +
        '<span role="group" aria-label="Process event pages"><button class="tl-btn" data-process-page="-1">Previous page</button> ' +
        '<button class="tl-btn" data-process-page="1">Next page</button></span></div>' +
        '<div class="tl-process-note tl-context-note">Unfiltered recorded kernel milestones. CSV includes every event.' +
        '<details><summary>Timing and event meanings</summary>These milestones are not inferred task lifetimes. ' +
        'Exec marks the installed image; exit marks a task exiting, not its final reap. ' +
        'Click a trace time to center the timeline without changing zoom or filters.</details></div>' +
        '<table class="tl-process-table"><thead><tr><th scope="col">Event</th><th scope="col">Task</th>' +
        '<th scope="col">Trace time</th><th scope="col">Text</th></tr></thead><tbody></tbody></table>');
    renderProcessEvents();
    if (reset) elDetail.scrollTop = 0;
    draw();
}
function renderProcessEvents() {
    const count = PROCESS_EVENTS.length;
    processView.page = Math.max(0, Math.min(processView.page, Math.ceil(count / PROCESS_PAGE) - 1));
    const start = processView.page * PROCESS_PAGE, end = Math.min(start + PROCESS_PAGE, count), rows = [];
    for (let index = start; index < end; index++) {
        const [task, time, phase, line] = PROCESS_EVENTS[index];
        rows.push('<tr data-process-index="' + index + '"><td>' +
            (phase === 'exec' ? 'exec (new image installed)' : 'exit (task exiting)') + '</td><td>' +
            esc(trackLabel(TRACKS[task])) + '</td><td><button class="tl-link tl-process-link" data-process-time="' + index +
            '" title="Center timeline at this recorded event">+' + fmtDur(time) + '</button></td><td>' +
            '<button class="tl-link tl-process-link" data-line="' + line + '">line ' + line + '</button></td></tr>');
    }
    elDetail.querySelector('.tl-process-table tbody').innerHTML = rows.join('');
    elDetail.querySelector('.tl-process-tools [role=status]').textContent = count ? 'Events ' + (start + 1) + '–' + end + ' of ' + count : 'No recorded events';
    elDetail.querySelector('[data-process-page="-1"]').disabled = start === 0;
    elDetail.querySelector('[data-process-page="1"]').disabled = end >= count;
}
function focusProcessEvent(index) {
    const event = PROCESS_EVENTS[index];
    if (!event) return;
    range = null;
    setSelection(-1);
    const task = TRACKS[event[0]];
    if (!task.hidden && task.h > 0) elScroll.scrollTop = Math.max(0, task.y - H / 3);
    panBy(event[1] - (view.s + view.e) / 2);
}
function exportProcessEvents() {
    function* rows() {
        for (let index = 0; index < PROCESS_EVENTS.length; index++) {
            const [task, time, phase, line] = PROCESS_EVENTS[index], identity = TRACKS[task];
            const timestamp = Number.isSafeInteger(time) ? String(BigInt(D.uftrace.origin_ns) + BigInt(time)) : '';
            yield [index, phase, task, identity.pid, identity.comm, time, timestamp, line, D.uftrace.clock || '', D.uftrace.aligned];
        }
    }
    downloadTimelineCsv('funcgraph-process-events.csv', ['event_index', 'event', 'task_index', 'pid', 'comm',
        'trace_time_ns', 'timestamp_ns', 'trace_line', 'clock', 'clock_aligned'], rows());
}
function safeSourceUrl(href) {
    if (!href) return '';
    try {
        const url = new URL(href, document.baseURI);
        return ['http:', 'https:', 'file:', 'vscode:', 'vscodium:'].includes(url.protocol) ? url.href : '';
    } catch (error) { return ''; }
}

function callNavigationHtml(index) {
    const controls = [['parent', '↑ Parent', 'Recorded parent', '↑'],
        ['child', isSyn(index) ? '↓ First recorded call' : '↓ First child', 'First recorded child', '↓'],
        ['previous', '‹ Previous', 'Previous recorded sibling / task root', '['],
        ['next', 'Next ›', 'Next recorded sibling / task root', ']']];
    return '<div class="tl-call-nav" role="group" aria-label="Recorded call navigation">' + controls.map(([action, label, title, key]) => {
        const target = callTarget(index, action), description = title + (key ? ' (' + key + ')' : '') + ': ' + (target >= 0 ? nameOf(target) : 'none');
        return '<button class="tl-btn" data-call-nav="' + action + '" title="' + esc(description) + '" aria-label="' + esc(description) + '"' +
            (target < 0 ? ' disabled' : '') + '>' + label + '</button>';
    }).join('') + '</div>';
}

const DETAIL_PAGE = 100;
let detailContext = null;
function detailParents(start, stop) {
    const ids = [], seen = new Set(), task = sTask[start];
    let current = start, status = 'complete';
    while (current >= 0) {
        if (current >= N || sTask[current] !== task) { status = 'invalid_parent'; break; }
        if (seen.has(current)) { status = 'cycle'; break; }
        seen.add(current); ids.push(current);
        if (current === stop) break;
        const parent = sParent[current];
        if (parent >= 0 && !seen.has(parent) && sDepth[parent] >= sDepth[current]) { status = 'invalid_parent'; break; }
        current = parent;
    }
    if (status === 'complete' && stop !== undefined && ids[ids.length - 1] !== stop) status = 'missing_chain_top';
    return { ids: ids.reverse(), status };
}
function detailSourceChain(index) {
    const chain = D.sources && D.sources.callers && D.sources.callers[sCaller && sCaller[index]] || inlChain(index);
    return chain && chain.length ? chain : null;
}
function detailSourceFields(position, basis) {
    return position ? { file: position[0], line: position[1], href: position[2] || '', sourceKind: basis } : {};
}
function detailRealRow(index, level, site) {
    const position = sFlags[index] & FL.USER ? userPosition(index) : D.sources?.functions?.[sName[index] + ':' + (sMod ? sMod[index] : -1)];
    return { kind: 'traced', name: nameOf(index), index, owner: index, depth: sDepth[index], level,
        ...detailSourceFields(site ? site.slice(1) : position, site ? 'call_site' : sFlags[index] & FL.USER ? 'recorded_user_source' : 'function_entry') };
}
function detailList(kind, title, count, createRows, status) {
    const list = { kind, title, count, createRows, status: status || 'complete', rows: [], visible: Math.min(DETAIL_PAGE, count) };
    list.iterator = createRows();
    return list;
}
function detailContextData(root) {
    if (detailContext && detailContext.root === root) return detailContext;
    const data = { root, lists: new Map(), facts: null };
    if (root >= N) {
        data.facts = isGhost(root) ? ghostFacts(root) : inlineFacts(root);
        const facts = data.facts;
        data.lists.set('children', detailList('children', 'Traced calls inside (' + facts.kids.length + ')', facts.kids.length, function* () {
            for (const index of facts.kids) yield detailRealRow(index, 0);
        }));
        if (!isGhost(root)) data.lists.set('inline', detailList('inline', 'Inline chain at the call site', facts.ch.length, function* () {
            for (let position = 0; position < facts.ch.length; position++) {
                const frame = (detailSourceChain(facts.kids[0]) || facts.ch)[position];
                yield { kind: position ? 'inline' : 'call_site', name: frame[0], owner: facts.kids[0], level: position,
                    ...detailSourceFields(frame.slice(1), 'call_site'), current: position === facts.k };
            }
        }));
    } else {
        const path = detailParents(root), first = path.ids[0];
        const outside = path.status === 'complete' && sDepth[first] > 0 ? sDepth[first] : 0;
        let count = outside ? 1 : 0;
        for (const index of path.ids) count += 1 + Math.max(0, gapOf(index)) + Math.max(0, (detailSourceChain(index) || []).length - 1);
        data.lists.set('stack', detailList('stack', 'Call stack (' + path.ids.length + ' recorded frames)', count, function* () {
            let level = 0;
            if (outside) yield { kind: 'outside', name: '… ' + outside + ' frame(s) outside trace window', owner: first, level: 0, missing: outside };
            for (let position = 0; position < path.ids.length; position++) {
                const index = path.ids[position];
                const gap = Math.max(0, gapOf(index));
                const chain = detailSourceChain(index);
                let boundary = !!(sFlags[index] & FL.BOUNDARY);
                const withBoundary = row => { row.boundary = boundary; boundary = false; return row; };
                for (let offset = 0; offset < gap; offset++) yield withBoundary({ kind: 'untraced', name: offset === gap - 1 ? callerFn(index) : '?',
                    owner: index, depth: sDepth[index] - gap + offset, level: level++,
                    ...detailSourceFields(offset === gap - 1 && chain && chain[0][0] === callerFn(index) ? chain[0].slice(1) : null, 'call_site') });
                if (chain) for (let frameIndex = 1; frameIndex < chain.length; frameIndex++) {
                    const frame = chain[frameIndex];
                    yield withBoundary({ kind: 'inline', name: frame[0], owner: index, level: level++, ...detailSourceFields(frame.slice(1), 'call_site') });
                }
                const child = path.ids[position + 1], childChain = child === undefined || gapOf(child) ? null : detailSourceChain(child);
                const site = childChain && childChain[0][0] === nameOf(index) ? childChain[0] : null;
                const row = detailRealRow(index, level++, site);
                const userSite = child !== undefined && sFlags[index] & FL.USER && sFlags[child] & FL.USER &&
                    sParent[child] === index && !gapOf(child) &&
                    D.uftrace?.user_details?.[sLine[child]]?.callsite?.name === nameOf(index) ? userCallSite(child) : null;
                if (userSite) Object.assign(row, detailSourceFields(userSite, 'call_site'), { sourceOwner: child });
                yield withBoundary(row);
            }
        }, path.status));
        if (eChain[root] >= 0) {
            const chain = CHAINS[eChain[root]], errorPath = detailParents(chain[0], chain[1]);
            const status = errorPath.status === 'complete' && errorPath.ids.length !== chain[4] ? 'length_mismatch' : errorPath.status;
            data.lists.set('error', detailList('error', 'Error chain (' + errorPath.ids.length + ' recorded frames, ' + (errName(chain[2]) || chain[2]) + ')',
                errorPath.ids.length, function* () {
                    for (let position = 0; position < errorPath.ids.length; position++) yield detailRealRow(errorPath.ids[position], position);
                }, status));
        }
    }
    detailContext = data;
    return data;
}
function prepareDetailRows(list, limit) {
    while (list.rows.length < Math.min(limit, list.count)) {
        const next = list.iterator.next();
        if (next.done) break;
        list.rows.push(next.value);
    }
}
function detailRowsHtml(list, rows) {
    return rows.map(row => {
        const traced = row.kind === 'traced', current = traced ? row.index === detailContext.root : row.current;
        const label = traced ? list.kind === 'error' ? String(row.depth) : fmtDur(durOf(row.index)) : row.kind === 'outside' ? '' : row.kind === 'call_site' ? 'call site' : row.kind;
        const suffix = list.kind === 'error' ? retOf(row.index) || '' : '';
        const rowSpace = MIXED && row.owner >= 0 && row.kind !== 'outside' ? spaceOf(row.owner) : null;
        const title = row.name + (suffix ? ' — ' + suffix : '') + (row.kind === 'untraced' ? ' (not traced: skipped depth)' : '');
        const main = '<' + (traced ? 'button type="button"' : 'div') + ' class="tl-crumb tl-context-select' + (current ? ' cur' : '') + '"' + (traced ? ' data-slice="' + row.index + '"' : '') +
            ' title="' + esc(title) + '"><span class="tl-context-value">' + esc(label) + '</span><span class="tl-context-text">' +
            (rowSpace ? '<span class="tl-stack-space">' + (rowSpace === 'user' ? 'U' : 'K') + '</span>' : '') + detailSymbolHtml(row.name) +
            (traced && tailJumpOf(row.index) ? ' <span class="tl-tail-tag" title="' + esc(callerRelationText(row.index)) + '">⇢ tail jump</span>' : '') +
            (suffix ? ' <span class="' + (eRole[row.index] >= 0 ? 'tl-code-error' : 'd') + '">' + detailValueHtml(suffix) + '</span>' : '') + '</span></' + (traced ? 'button' : 'div') + '>';
        let source = '';
        if (row.kind !== 'outside') {
            const at = row.file ? row.file + ':' + row.line : 'Source unavailable';
            const basis = row.file ? row.sourceKind === 'function_entry' ? 'Resolved function entry, not the calling line' :
                row.sourceKind === 'recorded_user_source' ? 'Recorded user function entry, not a call site' : 'Call site inside this frame' : '';
            const href = safeSourceUrl(row.href);
            const sourceOwner = row.sourceOwner === undefined ? row.index : row.sourceOwner;
            const embedded = traced && (row.sourceKind === 'recorded_user_source' && !userCallSite(row.index) || row.sourceOwner !== undefined) &&
                callSiteBlock(sourceOwner) !== null;
            const tag = href ? 'a' : embedded ? 'button' : 'span';
            source = '<div class="tl-context-source">' +
                '<' + tag + (href ? ' href="' + esc(href) + '" target="_blank" rel="noopener noreferrer"' :
                    embedded ? ' type="button" class="tl-source-open" data-slice="' + sourceOwner + '"' : '') +
                ' title="' + esc(at + (basis ? ' — ' + basis : '') + (row.file && !href && !embedded ? ' — no source URL configured' : '')) + '">' +
                (row.file ? '<span class="tl-source-file">' + esc(row.file) + '</span><span class="tl-source-line">:' + esc(row.line) + '</span>' : esc(at)) +
                '</' + tag + '></div>';
        }
        const boundary = list.kind === 'stack' && row.boundary;
        return '<div class="tl-context-row' + (traced ? '' : ' inl') + '" data-context-kind="' + row.kind + '"' +
            (boundary ? ' data-space-boundary="user-kernel" title="User → kernel"' : '') + '>' + main + source + '</div>';
    }).join('');
}
function detailListHtml(kind) {
    const list = detailContext.lists.get(kind);
    prepareDetailRows(list, list.visible);
    return '<section class="tl-context-list" data-context-list="' + kind + '"><div class="tl-context-head"><span class="tl-sec">' + esc(list.title) +
        '</span><button class="tl-btn" data-context-export="' + kind + '">Export ' + (kind === 'children' ? 'calls' : kind === 'inline' ? 'inline chain' : kind === 'error' ? 'error chain' : 'stack') +
        ' CSV</button></div>' + (list.status === 'complete' ? '' : '<div class="tl-context-note">Incomplete context: ' + list.status.replace(/_/g, ' ') +
            '. Showing only the observed parent path; no missing frames are invented.</div>') +
        '<div class="tl-crumbs">' + detailRowsHtml(list, list.rows.slice(0, list.visible)) + '</div>' +
        (list.count > DETAIL_PAGE ? '<div class="tl-context-footer"><span role="status">Showing ' + list.visible + ' of ' + list.count +
            ' context rows</span><button class="tl-btn" data-context-more="' + kind + '"' + (list.visible >= list.count ? ' disabled' : '') + '>Load more context</button></div>' : '') + '</section>';
}
function appendDetailRows(kind) {
    const list = detailContext.lists.get(kind), start = list.visible;
    list.visible = Math.min(start + DETAIL_PAGE, list.count);
    prepareDetailRows(list, list.visible);
    const section = elDetail.querySelector('[data-context-list="' + kind + '"]');
    section.querySelector('.tl-crumbs').insertAdjacentHTML('beforeend', detailRowsHtml(list, list.rows.slice(start, list.visible)));
    section.querySelector('[role=status]').textContent = 'Showing ' + list.visible + ' of ' + list.count + ' context rows';
    section.querySelector('[data-context-more]').disabled = list.visible >= list.count;
}
function detailContextHint() {
    return '<div class="tl-context-note">Context is unfiltered. Durations follow Overhead; frame durations overlap and must not be summed. ' +
        'Inline, untraced and outside-window rows are context, not extra measured calls. ' +
        'Source positions are on the right; hover for the full path and call-site / function-entry basis. Unresolved locations are not guessed.</div>';
}
function exportDetailCsv(kind) {
    const data = detailContext, list = data.lists.get(kind), unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const columns = ['list', 'position', 'kind', 'function', 'recorded_slice_index', 'owner_slice_index', 'depth', 'display_level',
        'trace_line', 'exit_trace_line', 'task_index', 'pid', 'comm', 'cpu', 'return', 'call_site', 'args', 'module', 'source_file', 'source_line',
        'outside_frame_count', 'duration_' + unit, 'self_' + unit, 'start_' + unit, 'end_' + unit, 'time_mode', 'duration_basis', 'position_basis',
        'selected_slice_index', 'chain_index', 'chain_origin_slice', 'chain_top_slice', 'chain_reported_frames', 'errno', 'fate', 'path_status',
        'source_kind', 'source_url', 'relationship', 'relationship_basis', 'tail_jump_sites', 'trace_binary_version_verified'];
    if (D.uftrace) columns.push('space', 'address_space_boundary');
    function* rows() {
        let position = 0;
        for (const row of list.createRows()) {
            const traced = row.kind === 'traced', index = row.index, owner = row.owner, task = TRACKS[sTask[owner]];
            const chainIndex = kind === 'error' ? eChain[data.root] : -1;
            const chain = chainIndex >= 0 ? CHAINS[chainIndex] : null;
            const evidence = traced ? tailJumpOf(index) : null;
            yield [kind, position++, row.kind, row.name, traced ? index : '', owner, row.depth === undefined ? '' : row.depth, row.level,
                traced ? sLine[index] : '', traced && sEline ? sEline[index] || '' : '', sTask[owner], task.pid, task.comm,
                traced && cpuOf(index) >= 0 ? cpuOf(index) : '', traced ? retOf(index) || '' : '', traced ? callerOf(index) || '' : '',
                traced ? argsOf(index) || '' : '', traced ? modOf(index) || '' : '', row.file || '', row.line || '', row.missing || '',
                traced ? durOf(index) / scale : '', traced ? sSelf[index] / scale : '', traced ? sStart[index] / scale : '', traced ? sEnd[index] / scale : '', MODE,
                traced ? MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'measured_duration' : '',
                traced ? TIMED ? 'relative_timestamp_span' : MODE === 'count' ? 'call_order_geometry' : 'packed_geometry' : '',
                data.root, chainIndex >= 0 ? chainIndex : '', chain ? chain[0] : '', chain ? chain[1] : '', chain ? chain[4] : '',
                chain ? chain[2] : '', chain ? ERROR_FATES[chain[3]] : '', list.status, row.sourceKind || '', row.href || '',
                evidence ? 'tail_jump_evidence' : traced && sFlags[index] & FL.MISMATCH ? 'caller_mismatch_unresolved' : '',
                evidence ? 'supplied_binary_static' : '',
                evidence ? evidence.sites.map(site => site.symbol || site.address).join('; ') : '',
                evidence ? 'false' : '', ...(D.uftrace ? [traced ? spaceOf(index) : '', traced && (sFlags[index] & FL.BOUNDARY) ? 'user_to_kernel' : ''] : [])];
        }
    }
    downloadTimelineCsv('funcgraph-context-' + kind + '-' + (MODE === 'count' ? 'count' : adjOn ? 'adjusted' : 'raw') + '.csv', columns, rows());
}

function showInlineDetail(i) {
    const f = detailContextData(i).facts;
    let h = '<div class="tl-detail-head tl-slice-head"><span class="tl-detail-title" title="' + esc(f.fn) + '">' + detailSymbolHtml(f.fn) + '</span>' + pill('mute', 'inlined') +
        pill('mute', 'into ' + f.intoFn) + '<span class="sp"></span>' + lineLink(sLine[f.kids[0]], 'Open first call in text view ↵') +
        '<button class="tl-close" title="Close (Esc)">×</button></div>' + callNavigationHtml(i) + '<div class="tl-detail-body"><div class="tl-kv">';
    h += kv('traced extent', fmtDur(f.span) + ' &nbsp;<span class="d">from the first traced call inside to the end of the last one</span>');
    h += kv('start / end', fmtDur(sStart[i]) + ' → ' + fmtDur(sEnd[i]));
    h += kv('inlined into', detailSymbolHtml(f.intoFn) + ' at ' + esc(f.atFile) + ':' + f.atLine);
    h += kv('defined in', esc(f.file));
    h += kv('traced calls', f.kids.length + ', together ' + fmtDur(f.covered));
    h += kv('between them', fmtDur(f.gap) + ' <span class="d">own inlined code, untraced callees, interrupts</span>');
    if (f.mismatch) h += kv('note', 'containing function ' + esc(f.ch[0][0]) + ' ≠ recorded parent ' + esc(nameOf(f.y.p)) + ' (tail call, inline or untraced code; cause unresolved)');
    h += '<span class="k">limits</span><span class="v d">Inlined code has no entry/exit events: only the traced calls inside it are visible, so the real start may be earlier and the real end later.</span>';
    h += '</div><div>' + detailListHtml('inline') + detailListHtml('children') + detailContextHint() + '</div></div>';
    openDetail(h);
}
function showGhostDetail(i) {
    const f = detailContextData(i).facts, y = f.y;
    const nm = y.name === '?' ? 'unknown frame' : y.name;
    let h = '<div class="tl-detail-head tl-slice-head"><span class="tl-detail-title" title="' + esc(nm) + '">' + detailSymbolHtml(nm) + '</span>' + pill('mute', 'not traced') +
        '<span class="sp"></span>' + lineLink(sLine[f.kids[0]], 'Open first call in text view ↵') +
        '<button class="tl-close" title="Close (Esc)">×</button></div>' + callNavigationHtml(i) + '<div class="tl-detail-body"><div class="tl-kv">';
    h += kv('extent', fmtDur(f.span) + ' &nbsp;<span class="d">from the first call inside to the end of the last one</span>');
    h += kv('start / end', fmtDur(sStart[i]) + ' → ' + fmtDur(sEnd[i]));
    h += kv('inside', '<a class="tl-link" data-slice="' + y.p + '">' + esc(nameOf(y.p)) + '</a>');
    h += kv('traced calls', f.kids.length + ', together ' + fmtDur(f.covered));
    h += kv('between them', fmtDur(f.gap) + ' <span class="d">untraced code</span>');
    h += '<span class="k">why</span><span class="v d">The kernel\'s call depth jumps by ' + (y.gap + 1) + ' levels from ' + esc(nameOf(y.p)) +
        ' to the calls inside: ' + (y.gap > 1 ? y.gap + ' frames were' : 'a frame was') + ' entered without a recorded entry ' +
        '(not traceable, e.g. noinstr entry code or the unwinder, or dropped). ' +
        (y.name === '?' ? 'Its name is unknown.' : 'The name comes from the return address of the calls inside (their caller).') +
        ' Its real start and end are not known.</span>';
    h += '</div><div>' + detailListHtml('children') + detailContextHint() + '</div></div>';
    openDetail(h);
}
function showDetail(i) {
    range = null;
    AN.kind = null;
    if (i >= N) { if (isGhost(i)) showGhostDetail(i); else showInlineDetail(i); return; }
    const context = detailContextData(i);
    const f = sFlags[i], d = durOf(i), t = TRACKS[sTask[i]];
    let pills = D.uftrace ? pill('mute', f & FL.USER ? 'User space' : 'Kernel space') : '';
    if (!D.uftrace || f & FL.LAT) pills += pill('ctx' + (f & FL.CTX), CTX_NAMES[f & FL.CTX] + (f & FL.CTXINF ? '?' : ''));
    if (f & FL.BOUNDARY) pills += pill('mute', 'User → kernel', 'Same thread and contained recorded interval; exact user call site is not known');
    if (f & FL.IRQOFF) pills += pill('warn', 'irqs off');
    if (f & FL.BHOFF) pills += pill('warn', 'BH off');
    if ((f >> FL.RS_SHIFT) & 7) pills += pill('warn', 'need-resched');
    // preemption: the preempt-count digit above the trace's baseline (the tracer itself may add one)
    const pd = preemptOff(f);
    if (pd > 0) pills += pill('warn', 'preempt off' + (pd > 1 ? ' ×' + pd : ''), 'preempt count ' + ((f >> FL.PR_SHIFT) & 15) + ', baseline of this trace ' + PBASE);
    if ((f >> FL.MG_SHIFT) & 15) pills += pill('warn', 'migrate off', 'migrate_disable() depth ' + ((f >> FL.MG_SHIFT) & 15));
    if (eRole[i] >= 0) pills += pill('err', eRole[i] === 0 ? 'error origin' : 'error propagated');
    if (ATOMIC.has(i)) pills += pill('err', 'may sleep in atomic context');
    if (f & FL.TRUNC) pills += pill('mute', 'no exit');
    if (f & FL.ORPHAN) pills += pill('mute', 'no entry');
    if (f & FL.EST) pills += pill('mute', 'estimated');
    let h = '<div class="tl-detail-head tl-slice-head"><span class="tl-detail-title" title="' + esc(nameOf(i)) + '">' + detailSymbolHtml(nameOf(i)) +
        (modOf(i) ? ' [' + esc(modOf(i)) + ']' : '') + '</span>' + pills + '<span class="sp"></span>' +
        '<button class="tl-btn tl-call-actions-button" type="button" data-call-actions aria-haspopup="menu" ' +
        'aria-controls="tlCallActions" aria-expanded="false" aria-label="Selected call actions">Actions ▾</button>' +
        '<button class="tl-close" title="Close (Esc)">×</button></div>';
    h += callNavigationHtml(i) + '<div class="tl-detail-body"><div class="tl-left"><div class="tl-kv tl-call-facts">';
    h += kv('duration', f & FL.ORPHAN && D.uftrace ? 'unknown · entry not recorded' :
        fmtDur(d) + (f & FL.EST ? ' (estimated)' : f & FL.TRUNC && D.uftrace ? ' (incomplete span)' : ''));
    h += kv('self', fmtDur(sSelf[i]) + (d > 0 ? ' (' + (100 * sSelf[i] / d).toFixed(1) + '%)' : ''));
    h += kv('start', '+' + fmtDur(sStart[i]) + (MODE === 'abs' ? ' &nbsp;' + fmtAbs(sStart[i]) + ' s' : ''));
    h += kv('task', esc(trackLabel(t)) + (cpuOf(i) >= 0 ? ' &nbsp;on CPU ' + cpuOf(i) : ''));
    if (f & FL.LAT) h += kv('flags', esc(ctxFlagText(f).replace(/^ · /, '').replace(' (preempt off)', '') || 'none') +
        ((f >> FL.PR_SHIFT) & 15 ? ' <span class="d">(baseline ' + PBASE + ': ' + (pd > 0 ? 'preemption disabled ' + pd + ' level' + (pd > 1 ? 's' : '') : 'preemptible') + ')</span>' : ''));
    if (ATOMIC.has(i)) h += kv('context', 'called with ' + esc(atomicText(ATOMIC.get(i))) + ' (sleeping function in atomic context)', 'err');
    const r = retOf(i), rawReturn = rawRetOf(i), typedReturn = kernelTypesOf(i)?.ret;
    const c = callerOf(i);
    if (c) h += kv('caller site', detailSymbolHtml(c) + '<span class="tl-cs-at"></span>' + (f & FL.MISMATCH ? '<div class="tl-context-note tl-tail-note">' + esc(callerRelationText(i)) + '</div>' : ''));
    const tailJump = tailJumpOf(i);
    if (tailJump) h += kv('tail jump', tailJump.sites.map(site => {
        const source = site.source;
        const url = source && safeSourceUrl(source[2]);
        const position = source ? esc(source[0] + ':' + source[1]) : 'Source unavailable';
        return '<div>' + detailSymbolHtml(site.symbol || site.address) + ' · ' + esc(site.mnemonic) + ' → ' + detailSymbolHtml(nameOf(i)) +
            ' · ' + (url ? '<a class="tl-link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + position + '</a>' : position) + '</div>';
    }).join('') + '<div class="tl-context-note tl-tail-note">Static sites in supplied binary; actual branch site not captured.</div>');
    const a = argsOf(i);
    const userDetail = f & FL.USER ? D.uftrace?.user_details?.[sLine[i]] : null;
    if (userDetail) {
        const callAddress = userCallAddress(i);
        const userSite = userCallSite(i);
        if (callAddress) h += kv('caller site', detailSymbolHtml(callAddress) +
            '<span class="tl-cs-at">' + (userSite ? '<br><span data-callsite-position>' + esc(userSite[0] + ':' + userSite[1]) + '</span>' : '') + '</span>' +
            (!userSite ? '<div class="tl-context-note">Call-site source unavailable; function entry is a fallback.</div>' : ''));
        else h += kv('caller site', '<span class="tl-muted">Not recorded · capture with -T &apos;.@callsite&apos;</span>');
        if (userDetail.args !== undefined) {
            const provenance = '<div class="tl-context-note">' + (userDetail.arg_name_sources?.includes('prototype') ?
                'Names from uftrace built-in prototypes matched to the saved capture specification; recorded user DWARF takes precedence. Unnamed values use slotN placeholders.' : userDetail.arg_names ?
                'Names from recorded user DWARF; values follow the saved capture specification. Unnamed values use slotN placeholders.' :
                'Parameter names were not recorded; slotN identifies a captured value, not a source parameter name.') + '</div>';
            h += kv('user args', detailArgumentsHtml(userDetail.args, userDetail.arg_names) +
                (a || provenance ? '<details class="tl-context-note"><summary>' + (a ? 'Original argument bytes' : 'Argument provenance') +
                    '</summary>' + provenance + (a ? detailToken('raw', a) : '') + '</details>' : ''), 'tl-arguments');
        }
        else if (a) h += kv('raw argument bytes', detailToken('raw', a));
        if (userDetail.retval !== undefined) h += kv('user return', detailValueHtml(userDetail.retval));
        else if (userDetail.return_payload) h += kv('raw return bytes', detailToken('raw', userDetail.return_payload));
    } else if (a) {
        const namedArgs = namedArgsOf(i);
        if (namedArgs) h += kv('args (BTF)', detailArgumentsHtml(namedArgs) + '<details class="tl-context-note"><summary>Raw argument slots</summary>' + detailToken('raw', a) + '</details>', 'tl-arguments');
        else h += kv(D.uftrace ? (f & FL.USER ? 'raw payload' : 'raw args') : 'args',
            D.uftrace ? detailToken('raw', a) : detailArgumentsHtml(a), D.uftrace ? '' : 'tl-arguments');
    }
    if (r !== null) {
        let rv = detailValueHtml(r);
        if (eRole[i] >= 0) rv += ' ' + esc(errName(eErr[i])) + ' · ' + esc(errRoleText(i));
        else if (f & FL.NULLRET) rv += ' (' + detailToken('keyword', 'NULL') + ')';
        h += kv(typedReturn ? 'return (BTF)' : D.uftrace ? 'raw return' : 'return', rv, eRole[i] >= 0 ? 'err' : '');
    }
    if (typedReturn && rawReturn !== null) h += kv('raw return', detailToken('raw', rawReturn) +
        (typedReturn[0] === 'void' ? '<div class="tl-context-note">void (BTF) · these bits are not a return value.</div>' : ''));
    h += kv('depth', String(sDepth[i]));
    if (userDetail) h += kv('recorded address', detailToken('raw', userDetail.address) + '<div class="tl-context-note">Function entry address, not the call site.</div>');
    h += kv('trace lines', lineLink(sLine[i]) + (sEline && sEline[i] && sEline[i] !== sLine[i] ? ' → ' + lineLink(sEline[i]) : ''));
    h += '</div>' +
        '<details class="tl-cs" open><summary class="tl-sec">' + (f & FL.USER ? userCallSite(i) ? 'User call site source' : 'User function source (entry)' : D.uftrace && !D.sources?.callers?.[sCaller?.[i]]?.length ? 'Kernel function source' : 'Call site source') + '</summary><div class="tl-cs-src"></div></details></div><div>';
    h += breakdownHtml(i);
    if (context.lists.has('error')) h += detailListHtml('error');
    h += detailListHtml('stack') + detailContextHint();
    h += childPreviewHtml(i);
    h += '</div></div>';
    openDetail(h);
    fillCallSite(i);
}
function callSiteBlock(i) {
    if (typeof fgSourceForLine !== 'function' || isSyn(i)) return null;
    return fgSourceForLine(sLine[i]);
}
const csPosCache = new Map();
function callSitePos(i) {
    if (isSyn(i)) return null;
    const userSite = userCallSite(i);
    if (userSite) return { func: '', at: userSite[0] + ':' + userSite[1], href: null, inl: false };
    if (D.uftrace && !D.uftrace.caller_symbols?.[sCaller?.[i]]) return null;
    const chain = D.sources?.callers?.[sCaller?.[i]];
    if (Array.isArray(chain)) {
        if (!chain.length) return null;
        const frame = chain[chain.length - 1];
        if (Array.isArray(frame) && typeof frame[0] === 'string' && frame[0] &&
            typeof frame[1] === 'string' && frame[1] && Number.isSafeInteger(frame[2]) && frame[2] > 0) {
            if (!csPosCache.has(chain)) csPosCache.set(chain, {func:frame[0], at:frame[1] + ':' + frame[2],
                href:safeSourceUrl(frame[3]) || null, inl:chain.length > 1});
            return csPosCache.get(chain);
        }
    }
    const k = callSiteBlock(i);
    if (k === null) return null;
    if (csPosCache.has(k)) return csPosCache.get(k);
    try {
        if (typeof fgData !== 'undefined' && fgData.sourceReady && !fgData.sourceReady(k)) {
            if (typeof loadCallSitePosition === 'function') loadCallSitePosition(i, k);
            return null;
        }
    } catch (error) { return null; }
    let r = null;
    if (typeof fgSourceHtml === 'function') {
        const tmp = document.createElement('div');
        tmp.innerHTML = fgSourceHtml(k);
        const locs = tmp.querySelectorAll('a.location-link, div.location-link:not(:first-child)');
        const a = locs[0];
        if (a) {
            const txt = a.textContent.replace(/^\(inlined\s+by\)\s*/, '');
            const m = txt.match(/^(\S+)\s+at\s+(.*)$/);
            if (m) r = { func: m[1], at: m[2], href: a.getAttribute && a.getAttribute('href'), inl: locs.length > 1 };
        }
    }
    csPosCache.set(k, r);
    return r;
}
function fillCallSite(i) {
    const box = elDetail.querySelector('.tl-cs');
    if (!box) return;
    const k = callSiteBlock(i);
    if (k === null) { box.remove(); return; }
    const el = box.querySelector('.tl-cs-src');
    el.className = 'tl-cs-src expanded-content';
    el.dataset.src = k;
    const location = () => {
        const pos = callSitePos(i);
        const at = elDetail.querySelector('.tl-cs-at');
        if (!pos || !at || at.querySelector('[data-callsite-position]')) return;
        const link = document.createElement(pos.href ? 'a' : 'span');
        link.className = 'tl-link';
        link.dataset.callsitePosition = '';
        if (pos.href) { link.href = pos.href; link.target = '_blank'; }
        link.textContent = pos.at;
        at.append(document.createElement('br'), link);
        if (pos.inl) { const label = document.createElement('span'); label.className = 'tl-muted'; label.textContent = '  in ' + pos.func + ' (inlined)'; at.append(label); }
    };
    location();
    el.addEventListener('fg-source-ready', () => {
        if (!box.isConnected) return;
        location();
        if (!el.querySelector('.source-line')) { box.remove(); return; }
        if (box.querySelector('[data-fg-source-mode]')) return;
        const mode = document.createElement('button');
        mode.type = 'button';
        mode.className = 'tl-btn';
        mode.dataset.fgSourceMode = '';
        mode.textContent = 'Source: ' + fgSourceMode();
        mode.title = 'Switch source layout between inline chain and flat frames';
        mode.addEventListener('click', event => {
            event.preventDefault(); event.stopPropagation(); fgToggleSrcMode();
        });
        box.querySelector('summary').append(' ', mode);
        fgTrackSource(mode);
        sizeDetailHead();
    });
    if (prefGet('csOpen') === '0') box.open = false;
    box.addEventListener('toggle', () => {
        prefSet('csOpen', box.open ? '1' : '0');
        if (box.open) fgFill(el);
        else fgCancelSource(el);
    });
    if (box.open) fgFill(el, true);
}
function sizeDetailHead() {
    const head = elDetail.querySelector('.tl-detail-head');
    const style = head ? getComputedStyle(head) : null;
    const height = style && style.position === 'sticky' && style.top !== 'auto' ? head.offsetHeight : 0;
    elDetail.querySelectorAll('table.tl-sum th').forEach(header => { header.style.top = height + 'px'; });
    scheduleDistHistogram();
}
const detailHeadObserver = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(sizeDetailHead) : null;
function updateDetailExpand() {
    const button = elDetail.querySelector('[data-detail-expand]');
    if (!button) return;
    const label = detailExpanded ? 'Restore chart and detail split' : 'Expand details into the chart area';
    button.title = label;
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-pressed', String(detailExpanded));
}
function focusTimelineControl() {
    const target = detailExpanded ? elDetail.querySelector('[data-detail-expand]') : elCv;
    if (target) target.focus({ preventScroll: true });
}
let pendingTextFocus = null;
function restoreTextFocus() {
    const request = pendingTextFocus;
    if (!request) return;
    const tab = document.querySelector('.view-tab[data-view="text"]'), active = document.activeElement;
    if (!request.current() || active !== document.body && active !== tab && !tlRoot.contains(active)) {
        pendingTextFocus = null;
        return;
    }
    if (!document.fullscreenElement) tab?.focus({ preventScroll: true });
    if (!fullscreenPending && !nativeFullscreen && document.fullscreenElement !== tlRoot) pendingTextFocus = null;
}
for (const kind of ['pointerdown', 'keydown', 'focusin']) document.addEventListener(kind, event => {
    if (!pendingTextFocus) return;
    if (kind === 'focusin' && (event.target === document.body || tlRoot.contains(event.target) ||
        event.target?.matches?.('.view-tab[data-view="text"]'))) return;
    pendingTextFocus = null;
}, true);
function setDetailExpanded(on) {
    detailExpanded = on && elDetail.classList.contains('show');
    tlRoot.classList.toggle('tl-detail-expanded', detailExpanded);
    updateDetailExpand();
    resize();
    sizeDetailHead();
    const button = elDetail.querySelector('[data-detail-expand]');
    if (button === document.activeElement && !button.getClientRects().length) {
        elDetail.querySelector('.tl-close')?.focus({ preventScroll: true });
    }
}
function closeCallActions(restoreFocus = false) {
    if (elCallActions.hidden) return;
    const trigger = callActionsTrigger;
    elCallActions.hidden = true;
    trigger?.setAttribute('aria-expanded', 'false');
    callActionsTrigger = null;
    callActionsSelection = -1;
    callActionsLayout = null;
    if (restoreFocus && trigger?.isConnected && trigger.getClientRects().length && window.fgCurrentView() === 'timeline')
        trigger.focus({ preventScroll: true });
}
function callActionItems() {
    return Array.from(elCallActions.querySelectorAll('[role=menuitem]:not(:disabled)'));
}
function openCallActions(trigger, last = false) {
    if (sel < 0 || sel >= N) return;
    closeCallActions();
    setToolbarOptions(false);
    callActionsTrigger = trigger;
    callActionsSelection = sel;
    const pendingFilter = window.fgFilterProgress && !window.fgFilterProgress.error;
    for (const button of elCallActions.querySelectorAll('[data-call-action^="focus-"]')) {
        const available = button.dataset.callAction !== 'focus-task' || callTaskFilter(sel) !== null;
        button.disabled = !!pendingFilter || !available;
        button.title = pendingFilter ? 'Wait for filtering to finish, or cancel it first' : !available ?
            'No task identity available to the shared filter' : 'Add an exact condition to the filter draft; Apply to confirm';
    }
    trigger.setAttribute('aria-expanded', 'true');
    elCallActions.hidden = false;
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
    const edge = 8, gap = 4;
    elCallActions.style.width = Math.max(1, Math.min(248, width - 2 * edge)) + 'px';
    elCallActions.style.maxHeight = Math.max(1, height - 2 * edge) + 'px';
    const anchor = trigger.getBoundingClientRect(), menu = elCallActions.getBoundingClientRect();
    callActionsLayout = { x: anchor.x, y: anchor.y, width: anchor.width, height: anchor.height,
        scrollTop: elDetail.scrollTop, scrollX, scrollY, viewportLeft: left, viewportTop: top,
        viewportWidth: width, viewportHeight: height };
    const below = anchor.bottom + gap;
    const target = below + menu.height <= top + height - edge ? below : anchor.top - gap - menu.height;
    elCallActions.style.left = Math.max(left + edge, Math.min(anchor.right - menu.width, left + width - menu.width - edge)) + 'px';
    elCallActions.style.top = Math.max(top + edge, Math.min(target, top + height - menu.height - edge)) + 'px';
    elCallActions.scrollTop = 0;
    const items = callActionItems();
    items[last ? items.length - 1 : 0]?.focus({ preventScroll: true });
}
function callActionsKey(event) {
    const trigger = event.target.closest?.('[data-call-actions]');
    if (elCallActions.hidden) {
        if (trigger && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
            event.preventDefault();
            openCallActions(trigger, event.key === 'ArrowUp');
            return true;
        }
        return false;
    }
    if (event.key === 'Escape' || event.key === 'Tab') {
        if (event.key === 'Escape') event.preventDefault();
        closeCallActions(true);
        return true;
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const items = callActionItems(), current = items.indexOf(document.activeElement);
        const index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 :
            (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items[index]?.focus({ preventScroll: true });
        return true;
    }
    if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') &&
            !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        return true;
    }
    if (event.key.length === 1 && event.key !== ' ' && !event.ctrlKey && !event.metaKey && !event.altKey)
        closeCallActions(true);
    return false;
}
function dismissCallActions(event) {
    if (!elCallActions.hidden && !elCallActions.contains(event.target) && !callActionsTrigger?.contains(event.target))
        closeCallActions();
}
function closeMovedCallActions() {
    if (elCallActions.hidden) return;
    const anchor = callActionsTrigger.getBoundingClientRect(), previous = callActionsLayout;
    const viewport = window.visualViewport;
    if (anchor.x !== previous.x || anchor.y !== previous.y || anchor.width !== previous.width ||
        anchor.height !== previous.height || elDetail.scrollTop !== previous.scrollTop ||
        scrollX !== previous.scrollX || scrollY !== previous.scrollY ||
        (viewport?.offsetLeft || 0) !== previous.viewportLeft || (viewport?.offsetTop || 0) !== previous.viewportTop ||
        (viewport?.width || innerWidth) !== previous.viewportWidth || (viewport?.height || innerHeight) !== previous.viewportHeight)
        closeCallActions(elCallActions.contains(document.activeElement));
}
document.addEventListener('pointerdown', dismissCallActions);
document.addEventListener('focusin', dismissCallActions);
elDetail.addEventListener('scroll', closeMovedCallActions, { passive: true });
window.addEventListener('scroll', closeMovedCallActions, { passive: true });
window.visualViewport?.addEventListener('scroll', closeMovedCallActions, { passive: true });
window.visualViewport?.addEventListener('resize', closeMovedCallActions, { passive: true });
elCallActions.addEventListener('click', event => {
    const action = event.target.closest('[data-call-action]');
    if (!action || action.disabled) return;
    const index = callActionsSelection;
    closeCallActions();
    if (index < 0 || index >= N || index !== sel) return;
    if (action.dataset.callAction === 'distribution') {
        showDist(sName[index]);
        focusTimelineControl();
    } else if (action.dataset.callAction === 'text') {
        gotoText(sLine[index]);
        if (window.fgCurrentView() !== 'text') focusTimelineControl();
    } else if (action.dataset.callAction.startsWith('focus-')) {
        stageCallFilter(index, action.dataset.callAction);
    }
});
function openDetail(html) {
    closeCallActions();
    const was = elDetail.classList.contains('show');
    fgCancelSources(elDetail);
    elDetail.innerHTML = html;
    const head = elDetail.querySelector('.tl-detail-head');
    const close = head && head.querySelector('.tl-close');
    if (close) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'tl-btn tl-detail-expand';
        button.dataset.detailExpand = '';
        button.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path class="tl-expand-icon" d="M6 2H2v4m0-4 5 5m3 7h4v-4m0 4-5-5"/><path class="tl-restore-icon" d="M2 6h4V2m0 4L1 1m13 9h-4v4m0-4 5 5"/></svg>';
        close.before(button);
        updateDetailExpand();
    }
    elDetail.classList.add('show');
    elResizer.classList.add('show');
    // sticky table headers sit right below the (sticky) panel head
    sizeDetailHead();
    if (detailHeadObserver) {
        detailHeadObserver.disconnect();
        const head = elDetail.querySelector('.tl-detail-head');
        if (head) detailHeadObserver.observe(head);
    }
    if (!was) resize();
}
function hideDetail() {
    closeCallActions();
    if (!elDetail.classList.contains('show')) return;
    fgCancelSources(elDetail);
    elDetail.classList.remove('show');
    elResizer.classList.remove('show');
    detailExpanded = false;
    tlRoot.classList.remove('tl-detail-expanded');
    resize();
}
// Drag the handle above the panel to resize it (height kept across reports); double-click resets.
elResizer.addEventListener('pointerdown', ev => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    const y0 = ev.clientY, h0 = elDetail.offsetHeight;
    elResizer.setPointerCapture(ev.pointerId);
    elResizer.classList.add('drag');
    document.body.classList.add('tl-resizing');
    let pend = 0;
    const move = e => {
        const h = clampDetailH(h0 + (y0 - e.clientY));
        prefSet('detailFrac', (h / window.innerHeight).toFixed(4));
        if (!pend) pend = requestAnimationFrame(() => { pend = 0; resize(); });
    };
    const up = () => {
        elResizer.removeEventListener('pointermove', move);
        elResizer.removeEventListener('pointerup', up);
        elResizer.removeEventListener('pointercancel', up);
        elResizer.removeEventListener('lostpointercapture', up);
        if (pend) { cancelAnimationFrame(pend); pend = 0; }
        elResizer.classList.remove('drag');
        document.body.classList.remove('tl-resizing');
        if (elResizer.hasPointerCapture(ev.pointerId)) elResizer.releasePointerCapture(ev.pointerId);
        resize();
    };
    elResizer.addEventListener('pointermove', move);
    elResizer.addEventListener('pointerup', up, { once: true });
    elResizer.addEventListener('pointercancel', up, { once: true });
    elResizer.addEventListener('lostpointercapture', up, { once: true });
});
elResizer.addEventListener('dblclick', () => { prefSet('detailFrac', null); resize(); });

// Notes strip (hidden state remembered) and maximized layout (no page header / tabs).
function updateNotes(on) {
    on = on && elNotes.children.length > 0;
    elNotes.classList.toggle('hide', !on);
    elNotesButton.classList.toggle('on', on);
    elNotesButton.setAttribute('aria-pressed', String(on));
}
function setNotes(on) {
    updateNotes(on);
    prefSet(compactToolbar.matches ? 'notesCompact' : 'notes', on ? '1' : '0');
    if (!compactToolbar.matches) resize();
}
function setToolbarOptions(on, restoreFocus = false) {
    const open = !!on;
    if (open) closeCallActions();
    if (open && compactToolbar.matches && !elNotes.classList.contains('hide')) setNotes(false);
    elOptions.classList.toggle('open', open);
    elMore.setAttribute('aria-expanded', String(open));
    if (open) Array.from(elOptions.querySelectorAll('button:not([disabled]), input:not([disabled])'))
        .find(control => control.getClientRects().length)?.focus({ preventScroll: true });
    if (restoreFocus) elMore.focus({ preventScroll: true });
}
function syncToolbar() {
    const compact = compactToolbar.matches;
    const focusInOptions = elOptions.contains(document.activeElement);
    tlRoot.classList.toggle('tl-compact', compact);
    setToolbarOptions(false, focusInOptions);
    updateNotes(compact ? prefGet('notesCompact') === '1' : prefGet('notes') !== '0');
}
function dismissToolbarOptions(event) {
    if (elOptions.classList.contains('open') && !elOptions.contains(event.target) && !elMore.contains(event.target)) {
        setToolbarOptions(false);
    }
}
document.addEventListener('pointerdown', dismissToolbarOptions);
document.addEventListener('focusin', dismissToolbarOptions);
compactToolbar.addEventListener('change', () => {
    syncToolbar();
    if (window.fgCurrentView() === 'timeline') resize();
});
let fullscreenPending = false, nativeFullscreen = false;
const fullscreenButton = tlRoot.querySelector('[data-act=max]');
function setMax(on, restoreFocus = false) {
    if (!on) setToolbarOptions(false);
    document.body.classList.toggle('tl-max', on);
    tlRoot.classList.toggle('tl-fullscreen', on);
    window.fgPageChrome().top.forEach(e => e.classList.toggle('fg-off', on));
    fullscreenButton.setAttribute('aria-pressed', String(on));
    fullscreenButton.textContent = on ? 'Exit full screen' : 'Full screen';
    fullscreenButton.setAttribute('aria-label', fullscreenButton.textContent);
    fullscreenButton.title = on ? 'Exit full screen (M / Escape)' : 'Enter full screen (M); Escape to exit';
    tlRoot.querySelector('[data-act=help]').hidden = !on;
    resize();
    sizeDetailHead();
    if (restoreFocus && window.fgCurrentView() === 'timeline') fullscreenButton.focus({ preventScroll: true });
}
function leaveFullscreen(restoreFocus = true) {
    setMax(false, restoreFocus);
    if (document.fullscreenElement === tlRoot) {
        document.exitFullscreen().catch(() => {
            if (document.fullscreenElement === tlRoot && window.fgCurrentView() === 'timeline') {
                setMax(true);
                toast('Use Escape or the browser controls to exit full screen.');
            }
        });
    }
}
async function toggleFullscreen() {
    setToolbarOptions(false);
    if (tlRoot.classList.contains('tl-fullscreen')) { leaveFullscreen(); return; }
    if (fullscreenPending) return;
    setMax(true);
    if (!tlRoot.requestFullscreen || document.fullscreenEnabled === false) {
        toast('Using page full screen; browser full screen is unavailable.');
        return;
    }
    fullscreenPending = true;
    fullscreenButton.disabled = true;
    try {
        await tlRoot.requestFullscreen();
    } catch (_) {
        if (tlRoot.classList.contains('tl-fullscreen') && window.fgCurrentView() === 'timeline')
            toast('Using page full screen; browser full screen was not permitted.');
    } finally {
        fullscreenPending = false;
        fullscreenButton.disabled = false;
        if (!tlRoot.classList.contains('tl-fullscreen') || window.fgCurrentView() !== 'timeline') {
            leaveFullscreen(false);
        }
        restoreTextFocus();
    }
}
document.addEventListener('fullscreenchange', () => {
    if (document.fullscreenElement === tlRoot) {
        nativeFullscreen = true;
        if (window.fgCurrentView() !== 'timeline' || !tlRoot.classList.contains('tl-fullscreen')) leaveFullscreen(false);
        else { resize(); sizeDetailHead(); }
    } else if (nativeFullscreen) {
        nativeFullscreen = false;
        setMax(false, true);
    }
    restoreTextFocus();
});
elDetail.addEventListener('click', ev => {
    const actions = ev.target.closest('[data-call-actions]');
    if (actions) {
        if (elCallActions.hidden) openCallActions(actions);
        else closeCallActions(true);
        return;
    }
    if (ev.target.closest('[data-detail-expand]')) {
        setDetailExpanded(!detailExpanded);
        return;
    }
    const processPage = ev.target.closest('[data-process-page]');
    if (processPage && AN.kind === 'process') {
        processView.page += +processPage.dataset.processPage;
        renderProcessEvents(); elDetail.scrollTop = 0; return;
    }
    const processTime = ev.target.closest('[data-process-time]');
    if (processTime && AN.kind === 'process') { focusProcessEvent(+processTime.dataset.processTime); return; }
    if (ev.target.closest('[data-process-export]') && AN.kind === 'process') { exportProcessEvents(); return; }
    const navigation = ev.target.closest('[data-call-nav]');
    if (navigation) { navigateCall(navigation.dataset.callNav, true); return; }
    const children = ev.target.closest('[data-child-open]');
    if (children) { showChildren(+children.dataset.childOpen); return; }
    const contextMore = ev.target.closest('[data-context-more]');
    if (contextMore) { appendDetailRows(contextMore.dataset.contextMore); return; }
    const contextExport = ev.target.closest('[data-context-export]');
    if (contextExport) { exportDetailCsv(contextExport.dataset.contextExport); return; }
    const dl = ev.target.closest('[data-dist]');
    if (dl) { showDist(+dl.dataset.dist); return; }
    const a = ev.target.closest('[data-line]');
    if (a) { gotoText(+a.dataset.line); return; }
    const s = ev.target.closest('[data-slice]');
    if (s) { select(+s.dataset.slice, { reveal: true }); return; }
    if (ev.target.closest('.tl-close')) { range = null; select(-1); return; }
    if (ev.target.closest('[data-act=range-csv]')) { exportRangeCsv(); return; }
    const rangePage = ev.target.closest('[data-range-page]');
    if (rangePage) { changeRangePage(+rangePage.dataset.rangePage); return; }
    const th = ev.target.closest('th[data-sort]');
    if (th) {
        sumSort = th.dataset.sort; showRangeSummary();
        elDetail.querySelector('th[data-sort="' + sumSort + '"] button').focus({ preventScroll: true });
        return;
    }
    const tr = ev.target.closest('tr[data-name]');
    if (tr) { elSearch.value = '^' + tr.dataset.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$'; applySearch(elSearch.value); }
});

// ---- range summary ----
let sumSort = 'self';
let sumCache = null;
const SUM_PAGE = 300;
function rangeSummary() {
    if (sumCache && sumCache.start === range.s && sumCache.end === range.e && sumCache.track === range.track &&
        sumCache.hits === textHits && sumCache.durations === sDur) return sumCache;
    const rs = range.s, re = range.e;
    const agg = new Map();
    const ctxSelf = [0, 0, 0, 0, 0];
    let offT = 0, nCalls = 0;
    const tracks = range.track !== null ? [TRACKS[range.track]] : ORDER;
    for (const t of tracks) {
        for (let i = t.lo; i >= 0 && i < t.hi; i++) {
            const s = sStart[i], e = sEnd[i];
            if (s === e ? s < rs || s >= re : e <= rs || s >= re) continue;
            if (textHits && !textHits[i]) continue;
            const d = e - s, ovl = Math.min(e, re) - Math.max(s, rs);
            const frac = d > 0 ? ovl / d : 0;
            const selfPart = sSelf[i] * frac;
            let a = agg.get(sName[i]);
            if (!a) { a = { calls: 0, incl: 0, self: 0, max: 0, err: 0, ex: i }; agg.set(sName[i], a); }
            if (s >= rs) { a.calls++; nCalls++; }
            a.incl += durOf(i) * frac;
            a.self += selfPart;
            if (durOf(i) > a.max) { a.max = durOf(i); a.ex = i; }
            if (eRole[i] === 0) a.err++;
            ctxSelf[sFlags[i] & FL.USER ? 4 : sFlags[i] & FL.CTX] += selfPart;
        }
        for (const r of t.off) if (r[2] > rs && r[1] < re) offT += Math.min(r[2], re) - Math.max(r[1], rs);
    }
    const rows = Array.from(agg.entries());
    let maxv = 0;
    for (const row of rows) maxv = Math.max(maxv, row[1].self);
    sumCache = { start: rs, end: re, track: range.track, hits: textHits, durations: sDur,
        rows, ctxSelf, offT, nCalls, maxv: maxv || 1, sort: null, page: 0 };
    return sumCache;
}
function sortedRangeSummary() {
    const summary = rangeSummary();
    if (summary.sort !== sumSort) {
        summary.rows.sort((left, right) => sumSort === 'name' ?
            (S[left[0]] < S[right[0]] ? -1 : S[left[0]] > S[right[0]] ? 1 : 0) : right[1][sumSort] - left[1][sumSort]);
        summary.sort = sumSort;
        summary.page = 0;
    }
    return summary;
}
function rangeRowsHtml(summary, start, end) {
    let html = '';
    for (let index = start; index < end; index++) {
        const [name, stats] = summary.rows[index];
        html += '<tr data-name="' + esc(S[name]) + '"><td><span class="bar" style="width:' + (60 * stats.self / summary.maxv).toFixed(1) + 'px"></span>' +
            (MIXED ? '<span class="tl-stack-space">' + (spaceOf(stats.ex) === 'user' ? 'U' : 'K') + '</span>' : '') + esc(S[name]) + '</td>' +
            '<td>' + stats.calls + '</td><td>' + fmtDur(stats.self) + '</td><td>' + fmtDur(stats.incl) + '</td><td>' + fmtDur(stats.max) + '</td>' +
            '<td class="' + (stats.err ? 'err' : '') + '">' + (stats.err || '') + '</td></tr>';
    }
    return html;
}
function updateRangeControls(summary) {
    const start = summary.page * SUM_PAGE, end = Math.min(start + SUM_PAGE, summary.rows.length);
    elDetail.querySelector('.tl-range-footer [role=status]').textContent =
        'Showing ' + (summary.rows.length ? start + 1 + '–' + end : '0') + ' of ' + summary.rows.length + ' functions';
    const previous = elDetail.querySelector('[data-act=range-prev]'), next = elDetail.querySelector('[data-act=range-next]');
    if (!previous || !next) return;
    const focused = document.activeElement;
    previous.disabled = summary.page === 0;
    next.disabled = end >= summary.rows.length;
    if (focused === next && next.disabled && !previous.disabled) previous.focus({ preventScroll: true });
    else if (focused === previous && previous.disabled && !next.disabled) next.focus({ preventScroll: true });
}
function changeRangePage(direction) {
    if (!range || AN.kind !== 'range') return;
    const summary = sortedRangeSummary(), last = Math.max(0, Math.ceil(summary.rows.length / SUM_PAGE) - 1);
    summary.page = Math.max(0, Math.min(last, summary.page + direction));
    const table = elDetail.querySelector('.tl-range-table'), head = elDetail.querySelector('.tl-range-head');
    const style = getComputedStyle(head), pinned = style.position === 'sticky' && style.top !== 'auto' ? head.offsetHeight : 0;
    elDetail.scrollTop += table.getBoundingClientRect().top - elDetail.getBoundingClientRect().top - pinned;
    const start = summary.page * SUM_PAGE;
    table.querySelector('tbody').innerHTML = rangeRowsHtml(summary, start, Math.min(start + SUM_PAGE, summary.rows.length));
    updateRangeControls(summary);
}
function downloadTimelineCsv(filename, columns, rows) {
    const cell = value => {
        let text = String(value);
        if (typeof value === 'string' && (/^[\s]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text))) text = "'" + text;
        return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    };
    const lines = [columns.join(',')];
    for (const row of rows) lines.push(row.map(cell).join(','));
    const url = URL.createObjectURL(new Blob(['\ufeff', lines.join('\r\n'), '\r\n'], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.hidden = true;
    tlRoot.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exportRangeCsv() {
    if (!range || AN.kind !== 'range') return;
    const summary = sortedRangeSummary();
    if (!summary.rows.length) return;
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const basis = MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'measured';
    const scope = range.track !== null ? trackLabel(TRACKS[range.track]) : 'all tracks';
    const columns = ['function', 'calls', 'self_estimate_' + unit, 'inclusive_estimate_' + unit, 'max_call_' + unit,
        'error_origins_overlapping', 'range_start_' + unit, 'range_end_' + unit, 'scope', 'scope_off_cpu_' + unit,
        'time_mode', 'duration_basis', 'filter'];
    if (D.uftrace) columns.push('space');
    function* rows() {
        for (const [name, stats] of summary.rows) yield [S[name], stats.calls, stats.self / scale, stats.incl / scale, stats.max / scale, stats.err,
            range.s / scale, range.e / scale, scope, summary.offT / scale, MODE, basis, (window.fgFilterState || {}).text || '',
            ...(D.uftrace ? [spaceOf(stats.ex)] : [])];
    }
    downloadTimelineCsv('funcgraph-range-' + (MODE === 'count' ? 'count' : adjOn ? 'adjusted' : 'raw') + '.csv', columns, rows());
}
function showRangeSummary() {
    if (!range) return;
    AN.kind = 'range';
    const summary = sortedRangeSummary(), { rows, ctxSelf, offT, nCalls } = summary;
    const span = range.e - range.s;
    let h = '<div class="tl-detail-head tl-range-head"><span class="tl-detail-title">Range ' + fmtDur(span) + '</span>';
    h += pill('mute', range.track !== null ? trackLabel(TRACKS[range.track]) : 'all tracks');
    h += pill('mute', nCalls + ' calls');
    if (textHits) h += pill('warn', 'filtered');
    if (adjOn) h += pill('warn', 'overhead subtracted');
    if (ctxSelf[4] > 0) h += pill('mute', 'user ' + fmtDur(ctxSelf[4]));
    for (let c = 0; c < 4; c++) if (ctxSelf[c] > 0) h += pill('ctx' + c, CTX_NAMES[c] + ' ' + fmtDur(ctxSelf[c]));
    if (offT > 0) h += pill('mute', 'off-CPU ' + fmtDur(offT));
    h += '<span class="tl-range-actions"><button class="tl-btn" data-act="range-csv"' + (rows.length ? '' : ' disabled') +
        '>Export CSV</button><button class="tl-close" title="Close (Esc)">×</button></span></div>';
    h += '<table class="tl-sum tl-range-table"><thead><tr>';
    for (const [key, label] of [['name', 'function'], ['calls', 'calls'], ['self', 'self'], ['incl', 'inclusive'], ['max', 'max'], ['err', 'errors']]) {
        h += '<th data-sort="' + key + '" aria-sort="' + (key === sumSort ? (key === 'name' ? 'ascending' : 'descending') : 'none') +
            '"><button class="tl-sum-sort">' + label + '</button></th>';
    }
    const start = summary.page * SUM_PAGE;
    h += '</tr></thead><tbody>' + rangeRowsHtml(summary, start, Math.min(start + SUM_PAGE, rows.length));
    if (!rows.length) h += '<tr><td colspan="6">No calls overlap this range' + (textHits ? ' with the current filter' : '') + '.</td></tr>';
    h += '</tbody></table><div class="tl-range-footer"><span role="status" aria-live="polite" aria-atomic="true"></span>';
    if (rows.length > SUM_PAGE) h += '<span class="tl-range-pages"><button class="tl-btn" type="button" data-act="range-prev" data-range-page="-1" ' +
        'aria-label="Previous page of functions">Previous</button><button class="tl-btn" type="button" data-act="range-next" data-range-page="1" ' +
        'aria-label="Next page of functions">Next</button></span>';
    h += '</div><div class="an-hint">Self and inclusive are proportional overlap estimates. Calls start in [range start, range end); ' +
        'max uses whole-call duration; errors count overlapping origins. Off-CPU covers the selected tracks, independent of the filter.</div>';
    setSelection(-1);
    openDetail(h);
    updateRangeControls(summary);
    draw();
}

// ---- jump to the text view ----
function gotoText(n) {
    if (D.no_text) { toast('Report generated with --no-text-view: no text view (trace line ' + n + ')'); return; }
    window.fgSwitchView('text');
    const result = fgRequestTextLine(n, (el, request) => {
        if (typeof _setFolded === 'function' && typeof foldedBlocks === 'object') {
            const model = fgDomTextModels.get(document.getElementById('content'));
            const record = model ? fgTextModel.locate(model, n)?.record : el;
            const ancestors = (record?.getAttribute('data-fold-ancestors') || '').split(' ').filter(Boolean);
            for (const id of ancestors) if (foldedBlocks[id]) _setFolded(id, false);
        }
        fgSelectMountedTextLine(el, false);
        if (el.style.display === 'none') toast('Line ' + n + ' is hidden by the current filter');
        setTimeout(() => {
            if (!request.current() || !el.isConnected) return;
            el.scrollIntoView({ block: 'center' });
            el.classList.remove('tl-flash');
            void el.offsetWidth;
            el.classList.add('tl-flash');
        }, 30);
    }, { missing: () => toast('Line ' + n + ' not found'),
        error: error => toast('Unable to open trace line ' + n + ': ' + error.message) });
    pendingTextFocus = fgTextLineRequest;
    restoreTextFocus();
    return result;
}
function revealLine(n) {
    let i = sliceForLine(n);
    if (i < 0) {   // nearest slice line at or before n
        let best = -1;
        for (let k = 0; k < N; k++) if (sLine[k] <= n && (best < 0 || sLine[k] > sLine[best])) best = k;
        i = best;
    }
    if (i < 0) { toast('No timeline slice for line ' + n); return; }
    select(i, { reveal: true });
}

// ---- text-view filter integration ----
let filteredCallCount = null;
let filterRetryButton = null;
let filterAppliedText = '';
let focusFilterDraft = null, focusFilterUndo = null, focusFilterRequest = null;
const elFundo = elFbar.querySelector('[data-act=fundo]');
function callTaskFilter(index) {
    if (!D.caps.proc && !D.uftrace) return null;
    const task = TRACKS[sTask[index]];
    if (!task || !Number.isSafeInteger(task.pid) || task.pid < 0 || ![0, 1].includes(task.kind)) return null;
    if (task.kind === 1 && (!Number.isSafeInteger(task.cpu) || task.cpu < 0)) return null;
    return 'pid:^' + task.pid + '$' + (task.kind === 1 ? ' AND cpu:^' + task.cpu + '$' : '');
}
function stageCallFilter(index, action) {
    if (!document.getElementById('filterInput') || window.fgFilterProgress && !window.fgFilterProgress.error) return;
    const clause = action === 'focus-task' ? callTaskFilter(index) :
        'func:=' + JSON.stringify(nameOf(index)) + ' AND space:' + (sFlags[index] & FL.USER ? 'user' : 'kernel');
    if (!clause) return;
    const base = elFtext.value.trim();
    const query = base === clause || focusFilterDraft?.query === base && focusFilterDraft.clause === clause ? base :
        base ? '(' + base + ') AND (' + clause + ')' : clause;
    focusFilterDraft = { before: filterAppliedText, query, clause };
    elFtext.value = query;
    renderFilterControls();
    setToolbarOptions(true);
    elFtext.focus({ preventScroll: true });
}
function updateFocusFilter(state) {
    if (focusFilterRequest && state !== focusFilterRequest.state) {
        const request = focusFilterRequest;
        focusFilterRequest = null;
        focusFilterUndo = state.text === request.query && !request.undo ? { before: request.before, query: request.query } : null;
        focusFilterDraft = null;
    }
    if (focusFilterUndo && state.text !== focusFilterUndo.query) focusFilterUndo = null;
    if (focusFilterDraft && state.text !== focusFilterDraft.before) focusFilterDraft = null;
}
function renderFilterControls() {
    const status = window.fgFilterProgress;
    const pending = status && !status.error;
    const draft = elFtext.value.trim();
    const changed = draft !== filterAppliedText.trim();
    const requested = pending && draft === status.text.trim();
    elFdraft.hidden = pending ? requested : !changed;
    elFapply.disabled = pending ? requested : !changed && !status?.error;
    elFcurrent.hidden = !filterAppliedText;
    elFcurrent.querySelector('code').textContent = filterAppliedText;
    elMoreFilter.title = filterAppliedText;
    elFundo.hidden = !focusFilterUndo;
    elFundo.disabled = !!pending || changed;
    elFundo.title = changed ? 'Apply or discard the current draft first' : 'Restore the previous filter: ' + (focusFilterUndo?.before || 'All calls');
    if (filterRetryButton && status?.error) {
        filterRetryButton.disabled = draft !== status.text.trim();
        filterRetryButton.title = filterRetryButton.disabled ? 'Choose Apply to use the edited query' : 'Retry the failed query';
    }
}
function renderFilterProgress() {
    const status = window.fgFilterProgress;
    elFcount.setAttribute('role', status?.error ? 'alert' : 'status');
    elFcount.setAttribute('aria-live', status?.error ? 'assertive' : 'polite');
    elFcount.setAttribute('aria-busy', String(!!status && !status.error));
    elFcount.textContent = status ? status.message : !N ?
        (filteredCallCount === null ? 'No recorded calls · event tracks unfiltered' : 'Text filter only · event tracks unfiltered') :
        filteredCallCount === null ? 'All ' + N + ' calls' : filteredCallCount + ' of ' + N + ' calls' +
        (filteredCallCount === 0 ? ' · No matches. Adjust the filter or Clear.' : '');
    elFcount.title = status ? status.message : '';
    if (filterRetryButton) { filterRetryButton.remove(); filterRetryButton = null; }
    if (status?.error) {
        filterRetryButton = document.createElement('button');
        filterRetryButton.type = 'button';
        filterRetryButton.className = 'tl-btn';
        filterRetryButton.dataset.act = 'fretry';
        filterRetryButton.textContent = 'Retry filter';
        elFbar.querySelector('.tl-filter-result').appendChild(filterRetryButton);
    }
    renderFilterControls();
}
window.fgOnFilterProgress = renderFilterProgress;
function applyTextFilter() {
    const st = window.fgFilterState || { text: '', lines: null };
    updateFocusFilter(st);
    const edited = elFtext.value.trim() !== filterAppliedText.trim();
    filterAppliedText = st.text || '';
    if (!edited) elFtext.value = filterAppliedText;
    elMoreFilter.hidden = !st.lines;
    if (!st.lines) {
        textHits = null;
        filteredCallCount = null;
        for (const t of ORDER) { t.hits = 0; t.hidden = false; }
        elFcount.textContent = '';
        elFbar.classList.remove('on');
    } else if (!N) {
        textHits = null;
        filteredCallCount = 0;
        for (const task of ORDER) { task.hits = 0; task.hidden = false; }
        elFbar.classList.add('on');
    } else {
        textHits = new Uint8Array(N);
        for (const t of ORDER) t.hits = 0;
        let cnt = 0;
        for (let i = 0; i < N; i++) {
            if (st.lines.has(sLine[i]) || (sEline && sEline[i] && st.lines.has(sEline[i]))) {
                textHits[i] = 1; cnt++; TRACKS[sTask[i]].hits++;
            }
        }
        for (const t of ORDER) t.hidden = hideUnmatched && t.kind !== 9 && t.hits === 0;
        filteredCallCount = cnt;
        elFcount.textContent = cnt + ' of ' + N + ' calls';
        elFbar.classList.add('on');
    }
    const hideButton = tlRoot.querySelector('[data-act=fhide]');
    hideButton.classList.toggle('on', hideUnmatched);
    hideButton.setAttribute('aria-pressed', String(hideUnmatched));
    if (sel >= 0 && textHits && !(sel < N ? textHits[sel] : SYN[sel - N].kids.some(index => textHits[index]))) {
        if (AN.kind) setSelection(-1);
        else select(-1);
    }
    rebuildSearchMatches();
    rebuildErrList();
    layout();
    draw();
    refreshPanel();
    renderFilterProgress();
}
window.fgOnFilter = applyTextFilter;
function submitTimelineFilter(undo = false) {
    const fi = document.getElementById('filterInput');
    if (!fi) return;
    const query = elFtext.value.trim();
    focusFilterRequest = undo ? { state: window.fgFilterState, query, undo: true } :
        focusFilterDraft?.query === query && focusFilterDraft.before === filterAppliedText && query !== filterAppliedText ?
            { state: window.fgFilterState, query, before: filterAppliedText, undo: false } : null;
    fi.value = elFtext.value;
    if (elFtext.value.trim()) {
        const indexed = fgTextDeferred() && document.getElementById('content').hasAttribute('data-fg-text-index');
        const pending = applyFilter();
        if (!indexed && pending && typeof pending.then === 'function') {
            elFcount.textContent = 'Loading Text for full-trace filtering…';
            pending.then(applied => { if (applied === false) toast('Text filter not applied. Open Text to retry loading its data.'); });
        }
    } else clearFilter();
    renderFilterControls();
}
elFtext.addEventListener('input', () => { focusFilterDraft = null; renderFilterControls(); });
elFexample.addEventListener('change', () => {
    if (!elFexample.value) return;
    focusFilterDraft = null;
    elFtext.value = elFexample.value;
    elFexample.value = '';
    renderFilterControls();
    elFtext.focus({ preventScroll: true });
});
function restoreTimelineFilterDraft() {
    if (window.fgFilterProgress) fgCancelPendingFilter();
    focusFilterDraft = null; focusFilterRequest = null;
    elFtext.value = (window.fgFilterState || {}).text || '';
    renderFilterControls();
}
elFtext.addEventListener('keydown', ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter') {
        ev.preventDefault();
        elFtext.blur();
        submitTimelineFilter();
    } else if (ev.key === 'Escape') {
        ev.preventDefault();
        restoreTimelineFilterDraft();
        elFtext.blur();
    }
});
elFbar.addEventListener('click', ev => {
    const b = ev.target.closest('[data-act]');
    if (!b) return;
    if (b.dataset.act === 'fapply') submitTimelineFilter();
    else if (b.dataset.act === 'fclear') { elFtext.value = ''; clearFilter(); }
    else if (b.dataset.act === 'fundo' && focusFilterUndo && !b.disabled) {
        elFtext.value = focusFilterUndo.before;
        submitTimelineFilter(true);
    }
    else if (b.dataset.act === 'fretry') {
        const status = window.fgFilterProgress;
        if (status) {
            document.getElementById('filterInput').value = elFtext.value = status.text;
            elFtext.focus({ preventScroll: true });
            submitTimelineFilter();
        }
    }
    else if (b.dataset.act === 'fhide') { hideUnmatched = !hideUnmatched; applyTextFilter(); }
});
if (!document.getElementById('filterInput')) {
    elFbar.style.display = 'none';
    elSearch.title = 'Highlight and navigate function-name matches; shared filtering is not enabled for this report';
    tlRoot.querySelector('#tlSearchHelp').textContent = 'Find highlights and navigates function names. Shared filtering is not enabled for this report.';
}

// ---- toolbar ----
tlRoot.querySelector('[data-role=color]').addEventListener('change', ev => {
    const picker = ev.currentTarget;
    if (!picker.selectedOptions.length || picker.selectedOptions[0].disabled) {
        picker.value = colorMode;
        return;
    }
    colorMode = picker.value;
    colorCache.clear();
    updateHeatLegend();
    draw();
});
tlRoot.querySelectorAll('[data-ov]').forEach(b => {
    b.addEventListener('click', () => {
        const k = b.dataset.ov;
        ov[k] = !ov[k];
        b.classList.toggle('off', !ov[k]);
        draw();
    });
});
elToolbar.addEventListener('click', ev => {
    const b = ev.target.closest('[data-act]');
    if (!b) return;
    if (elOptions.classList.contains('open') && b.matches('[data-act^=an-], [data-act=position], [data-act=saved-views], [data-act=fitcalls], [data-act=process-events], [data-act=max], [data-act=help]')) {
        setToolbarOptions(false, true);
    }
    switch (b.dataset.act) {
        case 'options': setToolbarOptions(!elOptions.classList.contains('open')); break;
        case 'searchprev': searchNext(-1); break;
        case 'searchnext': searchNext(1); break;
        case 'errprev': gotoErr(-1); break;
        case 'errnext': gotoErr(1); break;
        case 'errmode': errFilter = errFilter ? 0 : 1; rebuildErrList(); draw(); break;
        case 'zoomout': zoomAt(1.5, W / 2); break;
        case 'zoomin': zoomAt(1 / 1.5, W / 2); break;
        case 'fit': fitAll(); break;
        case 'fitcalls': fitCalls(); break;
        case 'help': elHelp.classList.toggle('show'); break;
        case 'notes': setNotes(elNotes.classList.contains('hide')); break;
        case 'max': toggleFullscreen(); break;
        case 'adj': toggleAdjusted(); break;
        case 'inl': toggleInlineView(); break;
        case 'an-errors': showErrors(); break;
        case 'an-lat': showLatency(); break;
        case 'an-sec': showSections(); break;
        case 'an-tasks': showTasks(null); break;
        case 'position': showPosition(); break;
        case 'saved-views': showSavedViews(); break;
        case 'process-events': showProcessEvents(); break;
        case 'expand': {
            const any = ORDER.some(t => t.collapsed);
            for (const t of ORDER) t.collapsed = !any;
            layout(); draw(); break;
        }
    }
});

function toggleInlineView(force) {
    if (!HAS_INL) { toast('No inline information: needs resolved call sites (funcgraph-retaddr + vmlinux with debug info)'); return; }
    inlineView = force === undefined ? !inlineView : !!force;
    applyView();
    const b = tlRoot.querySelector('[data-act=inl]');
    b.textContent = 'Inline: ' + (inlineView ? 'shown' : 'hidden');
    b.classList.toggle('on', inlineView);
    if (typeof lsSet === 'function') lsSet('tlInline', inlineView ? '1' : '0');
    if (sel >= N && !inlineView && !isGhost(sel)) select(-1);
    layout();
    if (sel >= 0) revealRow(sel);
    draw();
}
function toggleAdjusted() {
    if (!sDurAdj) { toast('No overhead estimate: capture with fgcapture.sh --calibrate or pass --overhead'); return; }
    setAdjusted(!adjOn);
    const b = tlRoot.querySelector('[data-act=adj]');
    b.textContent = 'Overhead: ' + (adjOn ? 'subtracted' : 'measured');
    b.classList.toggle('on', adjOn);
    colorCache.clear();
    updateHeatLegend();
    refreshPanel();
    draw();
    toast(adjOn ? 'Durations: overhead subtracted (estimate)' : 'Durations: as measured');
}
function refreshPanel() {
    if (!elDetail.classList.contains('show')) return;
    if (AN.kind === 'process') showProcessEvents(false);
    else if (AN.kind === 'errors') showErrors();
    else if (AN.kind === 'lat') showLatency();
    else if (AN.kind === 'sec') showSections();
    else if (AN.kind === 'dist') showDist(AN.func);
    else if (AN.kind === 'wait') showWaitChain(AN.off);
    else if (AN.kind === 'tasks') showTasks();
    else if (AN.kind === 'children') showChildren();
    else if (range) showRangeSummary();
    else if (sel >= 0) showDetail(sel);
}

// ---- capability notes ----
(function notes() {
    const c = D.caps, out = [];
    const note = (html, info) => out.push('<span class="tl-note' + (info ? ' info' : '') + '">' + html + '</span>');
    if (MODE === 'compressed') note('No timestamps: slices are packed by duration and gaps are lost. Enable <code>options/funcgraph-abstime</code>.');
    else if (MODE === 'count') note('No timestamps or durations: the x axis shows call order only. Enable <code>funcgraph-abstime</code> and <code>funcgraph-duration</code>.');
    if (D.uftrace) {
        if (MIXED && !D.uftrace.aligned) note('Clock alignment is not verified. Address spaces are shown separately; do not compare times across them.');
        if (D.stats.detached_kernel_roots) note(D.stats.detached_kernel_roots + ' overlapping kernel roots could not be linked; shown on an unjoined lane.', true);
        if (HAS_KERNEL && !D.btf && c.retval) note('Kernel returns are raw bits; errno inference requires kernel BTF.', true);
        if (HAS_KERNEL && !c.sched_events) note('No usable kernel sched_switch records. CPU rows show observed kernel-call coverage, not task start/exit or continuous CPU time. User calls without a recorded CPU are not extended onto a CPU; gaps and off-CPU periods remain unknown.', true);
    } else {
    if (N && !c.latency) note('No latency columns: context is inferred from IRQ/softirq entry function names. Enable <code>options/latency-format</code> for exact irqs-off / need-resched / hardirq / softirq / preempt flags.');
    if (!c.proc && !c.switch_markers) note('No task info: tracks are per CPU. Enable <code>options/funcgraph-proc</code>.');
    if (N && !c.retval) note('No return values: error paths need <code>options/funcgraph-retval</code>.');
    else if (N && !D.btf && c.retval_btf) note('The kernel printed type-aware return values (BTF-aware funcgraph-retval): void functions have no ret=, errnos and ERR_PTRs are signed.', true);
    else if (N && !D.btf) note('No BTF: errors are guessed from the value alone (void functions may show false positives). Use a vmlinux with <code>CONFIG_DEBUG_INFO_BTF</code> or pass <code>--btf</code>.');
    if (N && !c.retaddr) note('No call sites: enable <code>options/funcgraph-retaddr</code> to see where each call came from.', true);
    else if (N && !c.args && !c.retval_btf) note('Arguments are not printed while <code>funcgraph-retaddr</code> is on (kernel limitation, e.g. 6.18).', true);
    if (TIMED && !c.switch_markers && c.proc) note('No context-switch markers seen: off-CPU periods cannot be shown.', true);
    }
    if (N && D.overhead) note('Tracing overhead estimate (' + esc(D.overhead.source) + '): ' + fmtDur(D.overhead.child) +
        ' per traced child call, ' + fmtDur(D.overhead.self) + ' per call. Toggle <b>Overhead</b> to subtract it; slice positions stay as measured.', true);
    else if (N && TIMED) note(D.uftrace ? 'Durations include tracing overhead; no per-address-space correction was recorded.' :
        'Durations include tracing overhead (measured 0.2 us per traced call on a QEMU x86_64 VM, 1.1 us on a Raspberry Pi 4). Capture with <code>fgcapture.sh --calibrate</code> to get a correction.', true);
    if (c.latency && PBASE && !D.uftrace) note('Every line shows preempt depth ' + PBASE + ' or more: function_graph itself disables preemption while recording. ' +
        'The timeline subtracts it (preempt off, sections, atomic sleeps, preempt overlay); the text view shows the raw digit, so use <code>preempt:&gt;' + PBASE + '</code> there.', true);
    if (D.lost.length) note(D.lost.length + ' "LOST EVENTS" record(s): some slices are incomplete (marked with dotted edges). Increase <code>buffer_size_kb</code>.');
    const st = D.stats;
    if (st.truncated || st.orphans) note(st.truncated + ' call(s) without exit, ' + st.orphans + ' without entry (trace window edges).', true);
    elNotes.innerHTML = out.join('');
})();

elHelp.innerHTML = `
<button class="tl-btn tl-help-close" type="button" aria-label="Close help">×</button>
<h4>Timeline Help</h4>
<h4>Navigate</h4><ul>
<li>CPU rows show recorded coverage, not the beginning of <code>main</code>. Without <code>sched_switch</code>, dashed segments span CPU-tagged observations only; their duration is not continuous CPU running time. Missing CPU placement remains unknown.</li>
<li><kbd>W</kbd>/<kbd>S</kbd> or <kbd>Ctrl</kbd>+wheel: zoom at cursor</li>
<li><kbd>A</kbd>/<kbd>D</kbd>, drag, or <kbd>Shift</kbd>+wheel: pan</li>
<li>Wheel: scroll tracks · click track header: collapse</li>
<li><kbd>0</kbd> fit all · <kbd>F</kbd> zoom to selection · double-click: zoom to slice</li>
<li><kbd>Shift</kbd>+drag: summarize a range in this track (<kbd>Shift</kbd>+<kbd>Alt</kbd>: all tracks)</li></ul>
<p>Range summaries use <b>Previous / Next</b> for 300-function pages. Sorting or changing the range, shared filter or Overhead resets the page; <b>Export CSV</b> includes every function in that scope.</p>
<p><b>Critical sections</b> uses 200-row pages; <b>Scheduling latency</b> uses 300-row pages. <b>Previous / Next</b> does not grow the table. Each section kind remembers its page; sort, shared-filter or Overhead changes reset it. CSV always exports the complete sorted scope, not just the page.</p>
<h4>Touch</h4><ul>
<li>Tap a call or track header. Swipe horizontally on the tracks to pan; swipe vertically to scroll.</li>
<li>Pinch the tracks to zoom around your fingers, or use <b>−</b>, <b>+</b> and <b>Fit trace</b> on touch devices.</li>
<li><b>Fit trace</b> (<kbd>0</kbd> / <kbd>Home</kbd>) and the overview include all recorded scheduling context. The initial view focuses on calls and recorded exec / exit milestones. <b>Tools → Navigate → Fit calls</b> shows all recorded calls without changing selection, filters or analysis.</li>
<li><b>Tools</b> opens grouped navigation, display, analysis and filter controls without resizing the tracks. Click outside or press <kbd>Escape</kbd> to close it. <b>Notes</b> shows capture limitations. On phones, <b>Full screen</b> stays visible beside Search.</li></ul>
<h4>Search and filter</h4><ul>
<li><b>Search</b>: function-name regex; highlights matches and navigates between calls with Enter / Shift+Enter.</li>
<li><b>Filter</b>: conditions such as <code>pid:1234 AND graph:vfs_read</code>, applied with Enter and shared with Text. Analysis panels use this filter, not the search expression.</li>
<li>Search navigation stays within the filtered calls. <b>Hide unmatched</b> hides tracks without filter matches.</li></ul>
${N ? '' : '<p>No function calls were recorded. Search has no call matches; shared filters apply to Text lines only and do not hide or filter event tracks.</p>'}
<h4>Colors</h4>
<p><b>Tools → Color → Total time</b> includes child calls and recorded waits; <b>Self time</b> excludes child calls. Both use a blue-to-red log scale from zero to the maximum across all recorded calls, independent of zoom and filters. Neither measures CPU time. The legend inside Tools follows the theme and Overhead setting; slice positions and widths stay as recorded. Error overlays take priority; turn off <b>Errors</b> to compare duration colors. Duration colors are unavailable without durations or recorded calls. <b>Context</b> remains the default.</p>
<h4>Select</h4><ul>
<li>Call details group <b>Duration distribution</b> and <b>Open in Text</b> under <b>Actions</b>. Use arrow keys / Home / End inside the menu; Escape closes it before leaving full screen. Text is disabled when it is not included in the report.</li>
<li><b>Actions → Focus this task / Focus this function</b> adds an exact task or space-qualified raw symbol to the current filter draft. Choose <b>Apply</b> to confirm; <b>Undo focus</b> restores the previously applied filter. Missing task identity disables task focus. Reports without filtering omit these actions.</li>
<li><b>Tools → Saved views…</b>: name a position, selected call or range and applied filter, with optional notes. Click a saved name to restore it. Import / export transfers views for matching report data only; share the report separately. Browser storage failures are shown in the dialog.</li>
<li>Call details have native navigation buttons: <kbd>↑</kbd> recorded parent · <kbd>↓</kbd> first recorded child · <kbd>[</kbd>/<kbd>]</kbd> recorded siblings (task roots stay within the task); synthetic frames are skipped</li>
<li><kbd>E</kbd>/<kbd>Shift+E</kbd> next/prev error · <kbd>/</kbd> search, <kbd>N</kbd>/<kbd>Shift+N</kbd> next/prev match</li>
<li>Click an off-CPU segment: wait chain (who woke the task, wake latency, and who woke the waker)</li>
<li><b>Tasks</b>: find recorded tasks by name / PID / CPU, including tasks without traced calls; browse 100 identities per page with Previous / Next and export all matching rows, not just this page</li>
<li><kbd>G</kbd> or <b>Go to…</b>: center at an offset without changing zoom, or enter start/end to show an exact interval. Timestamped reports accept ns/us/ms/s; recorded timestamps require a known exact origin. Packed and call-order coordinates are explicitly not wall-clock time. Selection, filters and analysis stay unchanged.</li>
<li><b>Errors</b>: independent pages of 200 error groups and 100 atomic groups. Previous / Next replaces rows without losing a group's occurrence position; all three CSV exports include the complete current scope, not just the page.</li>
<li><b>Duration distribution</b>: browse 50 slowest-first calls per page. Previous / Next do not change percentiles or histogram scope; CSV includes all calls in the selected function, call site and duration bucket, not just the page. Toggle <b>Histogram</b> to give calls more room while keeping all six statistics; this report-local choice does not change the saved panel split.</li>
<li><b>Explore all children</b> in call details: complete parent-scoped groups/calls, longest-call navigation and full CSV</li>
<li><kbd>I</kbd>: show / hide inlined functions as dashed frames (extent = first to last traced call inside)</li>
<li><kbd>M</kbd> or <b>Full screen</b>: give Timeline the whole screen; <b>Exit full screen</b> or <kbd>Escape</kbd> restores the page. If the browser disallows native full screen, the page viewport is used instead. Zoom, selection, filters and the panel split stay unchanged.</li>
<li>Drag the bar above the bottom panel to resize it; double-click it to reset.</li>
<li>In a very short detail panel, the expand icon next to Close borrows the chart area. Press it again to restore the split; closing the panel restores the chart too. The saved panel height, query and current task page are not changed.</li>
<li><kbd>O</kbd>: durations as measured / with the tracing overhead subtracted</li>
<li><kbd>H</kbd>: duration distribution of the selected function · toolbar <b>Errors</b> / <b>Scheduling latency</b>: summaries</li>
<li><kbd>Enter</kbd>: open in text view · <kbd>Alt</kbd>+click a text line: show it here</li></ul>
<h4>Legend</h4><div class="tl-legend">
${HAS_USER ? '<i style="background:#9bcdbf"></i><span>user space (Context color)</span>' : ''}
${MIXED ? '<i style="background:currentColor;height:1px"></i><span>user → kernel boundary, only over a linked kernel call</span>' : ''}
<i style="background:hsl(214,55%,70%)"></i><span>task context</span>
<i style="background:hsl(152,62%,68%)"></i><span>softirq</span>
<i style="background:hsl(30,62%,68%)"></i><span>hardirq</span>
<i style="background:hsl(282,62%,70%)"></i><span>NMI</span>
<i style="background:#d32f2f"></i><span>error origin (reaches user space)</span>
<i style="background:#e8873a"></i><span>error origin (handled by caller)</span>
<i style="background:#f6b8b8;outline:1.5px solid #c62828"></i><span>error propagation</span>
<i style="background:repeating-linear-gradient(135deg,#888 0 2px,#ddd 2px 4px)"></i><span>irqs disabled when the call started</span>
<i style="background:#4b5563;height:2px"></i><span>under the track header: irqs-off periods</span>
<i style="background:#f59e0b"></i><span>need-resched pending (header band / corner)</span>
<i style="background:repeating-linear-gradient(135deg,#aaa 0 2px,#eee 2px 4px)"></i><span>off-CPU (amber tint: preempted)</span>
<i style="background:#8e44ad"></i><span>caller ≠ recorded parent (cause unresolved); ⇢ = static tail-jump evidence</span>
<i style="background:#0f9d8a;height:2px"></i><span>wakeup: waker → woken task (sched_waking/wakeup); bunched: small marker only</span>
<i style="background:rgba(110,120,140,.13);outline:1px dashed #6b7280"></i><span>inline frame (Inline view)</span>
<i style="background:#8fb3ef;border-bottom:2px solid #c62828"></i><span>red underline: sleeping function called in atomic context</span>
<i style="background:#8fb3ef;border-right:2px dotted #6b7280"></i><span>dotted right / left edge: exit / entry not captured</span>
<i style="background:#d32f2f;clip-path:polygon(50% 0,100% 100%,0 100%)"></i><span>error origin too narrow to label (triangle)</span>
<i style="background:#f0a35a;height:3px"></i><span>CPU track strip: hardirq (orange) / softirq (green) / NMI (purple)</span></div>`;
elHelp.querySelector('.tl-help-close').addEventListener('click', () => {
    elHelp.classList.remove('show');
    focusTimelineControl();
});

// ---- init ----
palette();
applyTextFilter();
if (!CHAINS.length) tlRoot.querySelector('.tl-errnav').style.display = 'none';
if (!D.caps.latency) { tlRoot.querySelector('[data-ov=irq]').classList.add('na'); tlRoot.querySelector('[data-ov=preempt]').classList.add('na'); }
if (!D.resched.length && !D.caps.latency) tlRoot.querySelector('[data-ov=rs]').classList.add('na');
if (!D.offcpu.length) tlRoot.querySelector('[data-ov=off]').classList.add('na');
if (!sDurAdj) tlRoot.querySelector('[data-act=adj]').style.display = 'none';
if (!HAS_INL) tlRoot.querySelector('[data-act=inl]').style.display = 'none';
if (!sDurAdj) tlRoot.querySelector('[aria-label="Timeline display"]').style.display = 'none';
if (HAS_INL && typeof lsGet === 'function' && lsGet('tlInline') === '1') toggleInlineView(true);
const noteCount = elNotes.children.length;
if (noteCount) elNotesButton.textContent = 'Notes ' + noteCount;
else elNotesButton.hidden = true;
syncToolbar();
if (!WAKES.length) tlRoot.querySelector('[data-ov=wk]').classList.add('na');
if (!D.caps.retval) tlRoot.querySelector('[data-ov=err]').classList.add('na');
new MutationObserver(() => { palette(); ovDirty = true; draw(); scheduleDistHistogram(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
window.addEventListener('resize', () => { if (window.fgCurrentView() === 'timeline') { resize(); sizeDetailHead(); } });
if (typeof ResizeObserver !== 'undefined') {
    let controlsFrame = 0;
    const controlsObserver = new ResizeObserver(entries => {
        if (compactToolbar.matches && !entries.some(entry => entry.target === elToolbar)) return;
        if (controlsFrame || window.fgCurrentView() !== 'timeline') return;
        controlsFrame = requestAnimationFrame(() => {
            controlsFrame = 0;
            if (window.fgCurrentView() === 'timeline') resize();
        });
    });
    controlsObserver.observe(elToolbar);
    controlsObserver.observe(elNotes);
}

// probe: read access to the model and a few actions, for tests and the browser console
const probe = () => ({ N, NA, SYN, S, sName, sStart, sEnd, sDepth, sParent, sTask, nameOf, isGhost, gapOf, TRACKS, styleOf: sliceStyle,
    select: (i) => select(i, { reveal: true }), zoomTo, sel: () => sel, view: () => [view.s, view.e],
    // page coordinates of slice i (center) / of off-CPU record r (center), for scripted mouse input
    xyOf: (i) => { const r = elCv.getBoundingClientRect(), k = W / (view.e - view.s), t = TRACKS[sTask[i]];
        return [r.left + ((Math.max(sStart[i], view.s) + Math.min(sEnd[i], view.e)) / 2 - view.s) * k,
                r.top + t.y - elScroll.scrollTop + HDR + rowOf(i) * ROW + ROW / 2]; },
    offXY: (r) => { const c = elCv.getBoundingClientRect(), k = W / (view.e - view.s), t = TRACKS[r[0]];
        return [c.left + ((Math.max(r[1], view.s) + Math.min(r[2], view.e)) / 2 - view.s) * k,
                c.top + t.y - elScroll.scrollTop + HDR + offRow(r) * ROW + ROW / 2]; },
    range: (s, e, track) => { range = { s: s, e: e, track: track === undefined ? null : track }; select(-1); showRangeSummary(); draw(); },
    errors: showErrors, latency: showLatency, sections: showSections, dist: showDist, wait: showWaitChain,
    inline: (on) => { if (!!on !== inlineView) toggleInlineView(); } });
return { resize: () => { resumeSearch(); resize(); }, revealLine: revealLine, draw: draw, probe: probe,
    suspend: () => { suspendSearch(); closeSavedViews(); closeCallActions(); if (elPosition.open) elPosition.close(); if (tlRoot.classList.contains('tl-fullscreen') || document.fullscreenElement === tlRoot) leaveFullscreen(false); setToolbarOptions(false); hideTip(); discardSceneCache(); }, showHelp: () => { closeCallActions(); elHelp.classList.add('show'); } };
