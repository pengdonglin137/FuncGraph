/* FuncGraph Timeline: detail panel, range summary, wiring, init */
function pill(cls, txt, title) { return '<span class="tl-pill ' + cls + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(txt) + '</span>'; }
function kv(k, v, cls) { return '<span class="k">' + k + '</span><span class="v' + (cls ? ' ' + cls : '') + '">' + v + '</span>'; }
function lineLink(n, label) { return '<a class="tl-link" data-line="' + n + '">' + (label || 'line ' + n) + '</a>'; }
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
    return D.sources && D.sources.callers && D.sources.callers[sCaller && sCaller[index]] || inlChain(index);
}
function detailSourceFields(position, basis) {
    return position ? { file: position[0], line: position[1], href: position[2] || '', sourceKind: basis } : {};
}
function detailRealRow(index, level, site) {
    const position = D.sources && D.sources.functions && D.sources.functions[sName[index] + ':' + (sMod ? sMod[index] : -1)];
    return { kind: 'traced', name: nameOf(index), index, owner: index, depth: sDepth[index], level,
        ...detailSourceFields(site ? site.slice(1) : position, site ? 'call_site' : 'function_entry') };
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
                for (let offset = 0; offset < gap; offset++) yield { kind: 'untraced', name: offset === gap - 1 ? callerFn(index) : '?',
                    owner: index, depth: sDepth[index] - gap + offset, level: level++,
                    ...detailSourceFields(offset === gap - 1 && chain && chain[0][0] === callerFn(index) ? chain[0].slice(1) : null, 'call_site') };
                if (chain) for (let frameIndex = 1; frameIndex < chain.length; frameIndex++) {
                    const frame = chain[frameIndex];
                    yield { kind: 'inline', name: frame[0], owner: index, level: level++, ...detailSourceFields(frame.slice(1), 'call_site') };
                }
                const child = path.ids[position + 1], childChain = child === undefined || gapOf(child) ? null : detailSourceChain(child);
                const site = childChain && childChain[0][0] === nameOf(index) ? childChain[0] : null;
                yield detailRealRow(index, level++, site);
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
        const title = row.name + (suffix ? ' — ' + suffix : '') + (row.kind === 'untraced' ? ' (not traced: skipped depth)' : '');
        const main = '<' + (traced ? 'button type="button"' : 'div') + ' class="tl-crumb tl-context-select' + (current ? ' cur' : '') + '"' + (traced ? ' data-slice="' + row.index + '"' : '') +
            ' title="' + esc(title) + '"><span class="tl-context-value">' + esc(label) + '</span><span class="tl-context-text">' + esc(row.name) +
            (traced && tailJumpOf(row.index) ? ' <span class="tl-tail-tag" title="' + esc(callerRelationText(row.index)) + '">⇢ tail jump</span>' : '') +
            (suffix ? ' <span class="d">' + esc(suffix) + '</span>' : '') + '</span></' + (traced ? 'button' : 'div') + '>';
        let source = '';
        if (row.kind !== 'outside') {
            const at = row.file ? row.file + ':' + row.line : 'Source unavailable';
            const basis = row.file ? row.sourceKind === 'function_entry' ? 'Resolved function entry, not the calling line' : 'Call site inside this frame' : '';
            const href = safeSourceUrl(row.href);
            source = '<div class="tl-context-source">' +
                '<' + (href ? 'a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer"' : 'span') +
                ' title="' + esc(at + (basis ? ' — ' + basis : '') + (row.file && !href ? ' — no source URL configured' : '')) + '">' +
                (row.file ? '<span class="tl-source-file">' + esc(row.file) + '</span><span class="tl-source-line">:' + esc(row.line) + '</span>' : esc(at)) +
                '</' + (href ? 'a' : 'span') + '></div>';
        }
        return '<div class="tl-context-row' + (traced ? '' : ' inl') + '" data-context-kind="' + row.kind + '">' + main + source + '</div>';
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
                evidence ? 'false' : ''];
        }
    }
    downloadTimelineCsv('funcgraph-context-' + kind + '-' + (MODE === 'count' ? 'count' : adjOn ? 'adjusted' : 'raw') + '.csv', columns, rows());
}

function showInlineDetail(i) {
    const f = detailContextData(i).facts;
    let h = '<div class="tl-detail-head tl-slice-head"><span class="tl-detail-title" title="' + esc(f.fn) + '">' + esc(f.fn) + '</span>' + pill('mute', 'inlined') +
        pill('mute', 'into ' + f.intoFn) + '<span class="sp"></span>' + lineLink(sLine[f.kids[0]], 'Open first call in text view ↵') +
        '<button class="tl-close" title="Close (Esc)">×</button></div>' + callNavigationHtml(i) + '<div class="tl-detail-body"><div class="tl-kv">';
    h += kv('traced extent', fmtDur(f.span) + ' &nbsp;<span class="d">from the first traced call inside to the end of the last one</span>');
    h += kv('start / end', fmtDur(sStart[i]) + ' → ' + fmtDur(sEnd[i]));
    h += kv('inlined into', esc(f.intoFn) + ' at ' + esc(f.atFile) + ':' + f.atLine);
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
    let h = '<div class="tl-detail-head tl-slice-head"><span class="tl-detail-title" title="' + esc(nm) + '">' + esc(nm) + '</span>' + pill('mute', 'not traced') +
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
    let pills = pill('ctx' + (f & FL.CTX), CTX_NAMES[f & FL.CTX] + (f & FL.CTXINF ? '?' : ''));
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
    let h = '<div class="tl-detail-head tl-slice-head"><span class="tl-detail-title" title="' + esc(nameOf(i)) + '">' + esc(nameOf(i)) +
        (modOf(i) ? ' [' + esc(modOf(i)) + ']' : '') + '</span>' + pills + '<span class="sp"></span>' +
        '<a class="tl-link" data-dist="' + sName[i] + '">Duration distribution (H)</a>&nbsp;&nbsp;' +
        lineLink(sLine[i], 'Open in text view ↵') + '<button class="tl-close" title="Close (Esc)">×</button></div>';
    h += callNavigationHtml(i) + '<div class="tl-detail-body"><div class="tl-left"><div class="tl-kv">';
    h += kv('duration', fmtDur(d) + (f & FL.EST ? ' (estimated)' : ''));
    h += kv('self', fmtDur(sSelf[i]) + (d > 0 ? ' (' + (100 * sSelf[i] / d).toFixed(1) + '%)' : ''));
    h += kv('start', '+' + fmtDur(sStart[i]) + (MODE === 'abs' ? ' &nbsp;' + fmtAbs(sStart[i]) + ' s' : ''));
    h += kv('task', esc(trackLabel(t)) + (cpuOf(i) >= 0 ? ' &nbsp;on CPU ' + cpuOf(i) : ''));
    h += kv('depth', String(sDepth[i]));
    if (f & FL.LAT) h += kv('flags', esc(ctxFlagText(f).replace(/^ · /, '').replace(' (preempt off)', '') || 'none') +
        ((f >> FL.PR_SHIFT) & 15 ? ' <span class="d">(baseline ' + PBASE + ': ' + (pd > 0 ? 'preemption disabled ' + pd + ' level' + (pd > 1 ? 's' : '') : 'preemptible') + ')</span>' : ''));
    const r = retOf(i);
    if (r !== null) {
        let rv = esc(r);
        if (eRole[i] >= 0) rv += ' ' + esc(errName(eErr[i])) + ' · ' + esc(errRoleText(i));
        else if (f & FL.NULLRET) rv += ' (NULL)';
        h += kv('return', rv, eRole[i] >= 0 ? 'err' : '');
    }
    const c = callerOf(i);
    if (c) h += kv('call site', esc(c) + '<span class="tl-cs-at"></span>' + (f & FL.MISMATCH ? '<div class="tl-context-note tl-tail-note">' + esc(callerRelationText(i)) + '</div>' : ''));
    const tailJump = tailJumpOf(i);
    if (tailJump) h += kv('tail jump', tailJump.sites.map(site => {
        const source = site.source;
        const url = source && safeSourceUrl(source[2]);
        const position = source ? esc(source[0] + ':' + source[1]) : 'Source unavailable';
        return '<div>' + esc(site.symbol || site.address) + ' · ' + esc(site.mnemonic) + ' → ' + esc(nameOf(i)) +
            ' · ' + (url ? '<a class="tl-link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + position + '</a>' : position) + '</div>';
    }).join('') + '<div class="tl-context-note tl-tail-note">Static sites in supplied binary; actual branch site not captured.</div>');
    const a = argsOf(i);
    if (a) h += kv('args', esc(a));
    if (ATOMIC.has(i)) h += kv('context', 'called with ' + esc(atomicText(ATOMIC.get(i))) + ' (sleeping function in atomic context)', 'err');
    h += kv('trace lines', lineLink(sLine[i]) + (sEline && sEline[i] && sEline[i] !== sLine[i] ? ' → ' + lineLink(sEline[i]) : ''));
    h += '</div><details class="tl-cs" open><summary class="tl-sec">Call site source</summary><div class="tl-cs-src"></div></details></div><div>';
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
// {func, at: 'file:line', href, inl} of the call itself (innermost frame of the block), cached per block
const csPosCache = new Map();
function callSitePos(i) {
    const k = callSiteBlock(i);
    if (k === null) return null;
    if (csPosCache.has(k)) return csPosCache.get(k);
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
    fgFill(el, true);
    // innermost position (the line of the call) next to the call-site address
    const pos = callSitePos(i);
    const at = elDetail.querySelector('.tl-cs-at');
    const hasCode = !!el.querySelector('.source-line');
    if (!hasCode) box.remove();          // no --kernel-src / source provider: position link only
    if (!pos) return;
    if (at) {
        const link = document.createElement(pos.href ? 'a' : 'span');
        link.className = 'tl-link';
        if (pos.href) { link.href = pos.href; link.target = '_blank'; }
        link.textContent = pos.at;
        at.append(document.createElement('br'), link);
        if (pos.inl) { const s = document.createElement('span'); s.className = 'tl-muted'; s.textContent = '  in ' + pos.func + ' (inlined)'; at.append(s); }
    }
    if (!hasCode) return;
    const mode = document.createElement('button');
    mode.type = 'button';
    mode.className = 'tl-btn';
    mode.dataset.fgSourceMode = '';
    mode.textContent = 'Source: ' + fgSourceMode();
    mode.title = 'Switch source layout between inline chain and flat frames';
    mode.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        fgToggleSrcMode();
    });
    box.querySelector('summary').append(' ', mode);
    fgTrackSource(mode);
    if (prefGet('csOpen') === '0') box.open = false;
    box.addEventListener('toggle', () => prefSet('csOpen', box.open ? '1' : '0'));
}
function sizeDetailHead() {
    const head = elDetail.querySelector('.tl-detail-head');
    const style = head ? getComputedStyle(head) : null;
    const height = style && style.position === 'sticky' && style.top !== 'auto' ? head.offsetHeight : 0;
    elDetail.querySelectorAll('table.tl-sum th').forEach(header => { header.style.top = height + 'px'; });
    scheduleDistHistogram();
}
const detailHeadObserver = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(sizeDetailHead) : null;
function openDetail(html) {
    const was = elDetail.classList.contains('show');
    elDetail.innerHTML = html;
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
    if (!elDetail.classList.contains('show')) return;
    elDetail.classList.remove('show');
    elResizer.classList.remove('show');
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
        elResizer.classList.remove('drag');
        document.body.classList.remove('tl-resizing');
        resize();
    };
    elResizer.addEventListener('pointermove', move);
    elResizer.addEventListener('pointerup', up, { once: true });
    elResizer.addEventListener('pointercancel', up, { once: true });
});
elResizer.addEventListener('dblclick', () => { prefSet('detailFrac', null); resize(); });

// Notes strip (hidden state remembered) and maximized layout (no page header / tabs).
function setNotes(on) {
    elNotes.classList.toggle('hide', !on);
    tlRoot.querySelector('[data-act=notes]').classList.toggle('on', on);
    prefSet('notes', on ? '1' : '0');
    resize();
}
function setMax(on) {
    // only in the timeline; the classes sit on the elements (see views.css)
    document.body.classList.toggle('tl-max', on);
    window.fgPageChrome().top.forEach(e => e.classList.toggle('fg-off', on));
    tlRoot.querySelector('[data-act=max]').classList.toggle('on', on);
    tlRoot.querySelector('[data-act=help]').hidden = !on;
    resize();
}
elDetail.addEventListener('click', ev => {
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
    if (ev.target.closest('[data-act=range-more]')) { appendRangeRows(); return; }
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
    const ctxSelf = [0, 0, 0, 0];
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
            ctxSelf[sFlags[i] & FL.CTX] += selfPart;
        }
        for (const r of t.off) if (r[2] > rs && r[1] < re) offT += Math.min(r[2], re) - Math.max(r[1], rs);
    }
    const rows = Array.from(agg.entries());
    let maxv = 0;
    for (const row of rows) maxv = Math.max(maxv, row[1].self);
    sumCache = { start: rs, end: re, track: range.track, hits: textHits, durations: sDur,
        rows, ctxSelf, offT, nCalls, maxv: maxv || 1, sort: null, visible: 0 };
    return sumCache;
}
function sortedRangeSummary() {
    const summary = rangeSummary();
    if (summary.sort !== sumSort) {
        summary.rows.sort((left, right) => sumSort === 'name' ?
            (S[left[0]] < S[right[0]] ? -1 : S[left[0]] > S[right[0]] ? 1 : 0) : right[1][sumSort] - left[1][sumSort]);
        summary.sort = sumSort;
        summary.visible = Math.min(SUM_PAGE, summary.rows.length);
    }
    return summary;
}
function rangeRowsHtml(summary, start, end) {
    let html = '';
    for (let index = start; index < end; index++) {
        const [name, stats] = summary.rows[index];
        html += '<tr data-name="' + esc(S[name]) + '"><td><span class="bar" style="width:' + (60 * stats.self / summary.maxv).toFixed(1) + 'px"></span>' + esc(S[name]) + '</td>' +
            '<td>' + stats.calls + '</td><td>' + fmtDur(stats.self) + '</td><td>' + fmtDur(stats.incl) + '</td><td>' + fmtDur(stats.max) + '</td>' +
            '<td class="' + (stats.err ? 'err' : '') + '">' + (stats.err || '') + '</td></tr>';
    }
    return html;
}
function appendRangeRows() {
    if (!range || AN.kind !== 'range') return;
    const summary = sortedRangeSummary(), start = summary.visible;
    summary.visible = Math.min(start + SUM_PAGE, summary.rows.length);
    elDetail.querySelector('.tl-range-table tbody').insertAdjacentHTML('beforeend', rangeRowsHtml(summary, start, summary.visible));
    elDetail.querySelector('.tl-range-footer [role=status]').textContent = 'Showing ' + summary.visible + ' of ' + summary.rows.length + ' functions';
    elDetail.querySelector('[data-act=range-more]').disabled = summary.visible >= summary.rows.length;
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
    function* rows() {
        for (const [name, stats] of summary.rows) yield [S[name], stats.calls, stats.self / scale, stats.incl / scale, stats.max / scale, stats.err,
            range.s / scale, range.e / scale, scope, summary.offT / scale, MODE, basis, (window.fgFilterState || {}).text || ''];
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
    for (let c = 0; c < 4; c++) if (ctxSelf[c] > 0) h += pill('ctx' + c, CTX_NAMES[c] + ' ' + fmtDur(ctxSelf[c]));
    if (offT > 0) h += pill('mute', 'off-CPU ' + fmtDur(offT));
    h += '<span class="tl-range-actions"><button class="tl-btn" data-act="range-csv"' + (rows.length ? '' : ' disabled') +
        '>Export CSV</button><button class="tl-close" title="Close (Esc)">×</button></span></div>';
    h += '<table class="tl-sum tl-range-table"><thead><tr>';
    for (const [key, label] of [['name', 'function'], ['calls', 'calls'], ['self', 'self'], ['incl', 'inclusive'], ['max', 'max'], ['err', 'errors']]) {
        h += '<th data-sort="' + key + '" aria-sort="' + (key === sumSort ? (key === 'name' ? 'ascending' : 'descending') : 'none') +
            '"><button class="tl-sum-sort">' + label + '</button></th>';
    }
    h += '</tr></thead><tbody>' + rangeRowsHtml(summary, 0, summary.visible);
    if (!rows.length) h += '<tr><td colspan="6">No calls overlap this range' + (textHits ? ' with the current filter' : '') + '.</td></tr>';
    h += '</tbody></table><div class="tl-range-footer"><span role="status">Showing ' + summary.visible + ' of ' + rows.length + ' functions</span>';
    if (rows.length > SUM_PAGE) h += '<button class="tl-btn" data-act="range-more"' + (summary.visible >= rows.length ? ' disabled' : '') + '>Load more</button>';
    h += '</div><div class="an-hint">Self and inclusive are proportional overlap estimates. Calls start in [range start, range end); ' +
        'max uses whole-call duration; errors count overlapping origins. Off-CPU covers the selected tracks, independent of the filter.</div>';
    setSelection(-1);
    openDetail(h);
    draw();
}

// ---- jump to the text view ----
function gotoText(n) {
    if (D.no_text) { toast('Report generated with --no-text-view: no text view (trace line ' + n + ')'); return; }
    if (fgTextDeferred()) return Promise.resolve(window.fgSwitchView('text')).then(api => {
        if (api && window.fgCurrentView() === 'text') gotoText(n);
    });
    const el = document.getElementById('L' + n);
    if (!el) { toast('Line ' + n + ' not found'); return; }
    // unfold folded ancestors so the line becomes visible
    if (typeof _setFolded === 'function' && typeof foldedBlocks === 'object') {
        const anc = (el.getAttribute('data-fold-ancestors') || '').split(' ').filter(Boolean);
        for (const id of anc) if (foldedBlocks[id]) _setFolded(id, false);
    }
    window.fgSwitchView('text');
    if (el.style.display === 'none') toast('Line ' + n + ' is hidden by the current filter');
    setTimeout(() => {
        el.scrollIntoView({ block: 'center' });
        el.classList.remove('tl-flash');
        void el.offsetWidth;
        el.classList.add('tl-flash');
    }, 30);
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
function renderFilterProgress() {
    const status = window.fgFilterProgress;
    elFcount.setAttribute('role', status?.error ? 'alert' : 'status');
    elFcount.setAttribute('aria-live', status?.error ? 'assertive' : 'polite');
    elFcount.setAttribute('aria-busy', String(!!status && !status.error));
    elFcount.textContent = status ? status.message : filteredCallCount === null ? '' : filteredCallCount + ' of ' + N + ' calls';
    elFcount.title = status ? status.message : '';
    if (filterRetryButton) { filterRetryButton.remove(); filterRetryButton = null; }
    if (status?.error) {
        filterRetryButton = document.createElement('button');
        filterRetryButton.type = 'button';
        filterRetryButton.className = 'tl-btn';
        filterRetryButton.dataset.act = 'fretry';
        filterRetryButton.textContent = 'Retry filter';
        elFbar.appendChild(filterRetryButton);
    }
}
window.fgOnFilterProgress = renderFilterProgress;
function applyTextFilter() {
    const st = window.fgFilterState || { text: '', lines: null };
    if (document.activeElement !== elFtext) elFtext.value = st.text || '';
    if (!st.lines) {
        textHits = null;
        filteredCallCount = null;
        for (const t of ORDER) { t.hits = 0; t.hidden = false; }
        elFcount.textContent = '';
        elFbar.classList.remove('on');
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
    tlRoot.querySelector('[data-act=fhide]').classList.toggle('on', hideUnmatched);
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
elFtext.addEventListener('keydown', ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter') {
        const fi = document.getElementById('filterInput');
        fi.value = elFtext.value;
        elFtext.blur();
        if (elFtext.value.trim()) {
            const indexed = fgTextDeferred() && document.getElementById('content').hasAttribute('data-fg-text-index');
            const pending = applyFilter();
            if (!indexed && pending && typeof pending.then === 'function') {
                elFcount.textContent = 'Loading Text for full-trace filtering…';
                pending.then(applied => { if (applied === false) toast('Text filter not applied. Open Text to retry loading its data.'); });
            }
        } else clearFilter();
    } else if (ev.key === 'Escape') {
        if (window.fgFilterProgress) fgCancelPendingFilter();
        elFtext.value = (window.fgFilterState || {}).text || '';
        elFtext.blur();
    }
});
elFbar.addEventListener('click', ev => {
    const b = ev.target.closest('[data-act]');
    if (!b) return;
    if (b.dataset.act === 'fclear') { elFtext.value = ''; clearFilter(); }
    else if (b.dataset.act === 'fretry') {
        const status = window.fgFilterProgress;
        if (status) {
            document.getElementById('filterInput').value = elFtext.value = status.text;
            elFtext.focus({ preventScroll: true });
            applyFilter();
        }
    }
    else if (b.dataset.act === 'fhide') { hideUnmatched = !hideUnmatched; applyTextFilter(); }
});
if (!document.getElementById('filterInput')) elFbar.style.display = 'none';

// ---- toolbar ----
tlRoot.querySelector('[data-role=color]').addEventListener('click', ev => {
    const b = ev.target.closest('button');
    if (!b) return;
    colorMode = b.dataset.v;
    tlRoot.querySelectorAll('[data-role=color] button').forEach(x => x.classList.toggle('on', x === b));
    colorCache.clear();
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
tlRoot.querySelector('.tl-toolbar').addEventListener('click', ev => {
    const b = ev.target.closest('[data-act]');
    if (!b) return;
    switch (b.dataset.act) {
        case 'searchprev': searchNext(-1); break;
        case 'searchnext': searchNext(1); break;
        case 'errprev': gotoErr(-1); break;
        case 'errnext': gotoErr(1); break;
        case 'errmode': errFilter = errFilter ? 0 : 1; rebuildErrList(); draw(); break;
        case 'fit': fitAll(); break;
        case 'help': elHelp.classList.toggle('show'); break;
        case 'notes': setNotes(elNotes.classList.contains('hide')); break;
        case 'max': setMax(!document.body.classList.contains('tl-max')); break;
        case 'adj': toggleAdjusted(); break;
        case 'inl': toggleInlineView(); break;
        case 'an-errors': showErrors(); break;
        case 'an-lat': showLatency(); break;
        case 'an-sec': showSections(); break;
        case 'an-tasks': showTasks(null); break;
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
    refreshPanel();
    draw();
    toast(adjOn ? 'Durations: overhead subtracted (estimate)' : 'Durations: as measured');
}
function refreshPanel() {
    if (!elDetail.classList.contains('show')) return;
    if (AN.kind === 'errors') showErrors();
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
    if (!c.latency) note('No latency columns: context is inferred from IRQ/softirq entry function names. Enable <code>options/latency-format</code> for exact irqs-off / need-resched / hardirq / softirq / preempt flags.');
    if (!c.proc && !c.switch_markers) note('No task info: tracks are per CPU. Enable <code>options/funcgraph-proc</code>.');
    if (!c.retval) note('No return values: error paths need <code>options/funcgraph-retval</code>.');
    else if (!D.btf && c.retval_btf) note('The kernel printed type-aware return values (BTF-aware funcgraph-retval): void functions have no ret=, errnos and ERR_PTRs are signed.', true);
    else if (!D.btf) note('No BTF: errors are guessed from the value alone (void functions may show false positives). Use a vmlinux with <code>CONFIG_DEBUG_INFO_BTF</code> or pass <code>--btf</code>.');
    if (!c.retaddr) note('No call sites: enable <code>options/funcgraph-retaddr</code> to see where each call came from.', true);
    else if (!c.args && !c.retval_btf) note('Arguments are not printed while <code>funcgraph-retaddr</code> is on (kernel limitation, e.g. 6.18).', true);
    if (TIMED && !c.switch_markers && c.proc) note('No context-switch markers seen: off-CPU periods cannot be shown.', true);
    if (D.overhead) note('Tracing overhead estimate (' + esc(D.overhead.source) + '): ' + fmtDur(D.overhead.child) +
        ' per traced child call, ' + fmtDur(D.overhead.self) + ' per call. Toggle <b>Overhead</b> to subtract it; slice positions stay as measured.', true);
    else if (TIMED) note('Durations include tracing overhead (measured 0.2 us per traced call on a QEMU x86_64 VM, 1.1 us on a Raspberry Pi 4). Capture with <code>fgcapture.sh --calibrate</code> to get a correction.', true);
    if (c.latency && PBASE) note('Every line shows preempt depth ' + PBASE + ' or more: function_graph itself disables preemption while recording. ' +
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
<li><kbd>W</kbd>/<kbd>S</kbd> or <kbd>Ctrl</kbd>+wheel: zoom at cursor</li>
<li><kbd>A</kbd>/<kbd>D</kbd>, drag, or <kbd>Shift</kbd>+wheel: pan</li>
<li>Wheel: scroll tracks · click track header: collapse</li>
<li><kbd>0</kbd> fit all · <kbd>F</kbd> zoom to selection · double-click: zoom to slice</li>
<li><kbd>Shift</kbd>+drag: summarize a range in this track (<kbd>Shift</kbd>+<kbd>Alt</kbd>: all tracks)</li></ul>
<h4>Search and filter</h4><ul>
<li><b>Search</b>: function-name regex; highlights matches and navigates between calls with Enter / Shift+Enter.</li>
<li><b>Filter</b>: conditions such as <code>pid:1234 AND graph:vfs_read</code>, applied with Enter and shared with Text. Analysis panels use this filter, not the search expression.</li>
<li>Search navigation stays within the filtered calls. <b>Hide unmatched</b> hides tracks without filter matches.</li></ul>
<h4>Select</h4><ul>
<li>Call details have native navigation buttons: <kbd>↑</kbd> recorded parent · <kbd>↓</kbd> first recorded child · <kbd>[</kbd>/<kbd>]</kbd> recorded siblings (task roots stay within the task); synthetic frames are skipped</li>
<li><kbd>E</kbd>/<kbd>Shift+E</kbd> next/prev error · <kbd>/</kbd> search, <kbd>N</kbd>/<kbd>Shift+N</kbd> next/prev match</li>
<li>Click an off-CPU segment: wait chain (who woke the task, wake latency, and who woke the waker)</li>
<li><b>Tasks</b>: find recorded tasks by name / PID / CPU, including tasks without traced calls; export all matching rows</li>
<li><b>Explore all children</b> in call details: complete parent-scoped groups/calls, longest-call navigation and full CSV</li>
<li><kbd>I</kbd>: show / hide inlined functions as dashed frames (extent = first to last traced call inside)</li>
<li><kbd>M</kbd> or <b>Max</b>: maximize (hide the page header and tabs); drag the bar above the bottom panel to resize it, double-click it to reset</li>
<li><kbd>O</kbd>: durations as measured / with the tracing overhead subtracted</li>
<li><kbd>H</kbd>: duration distribution of the selected function · toolbar <b>Errors</b> / <b>Sched latency</b>: summaries</li>
<li><kbd>Enter</kbd>: open in text view · <kbd>Alt</kbd>+click a text line: show it here</li></ul>
<h4>Legend</h4><div class="tl-legend">
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
    elCv.focus({ preventScroll: true });
});

// ---- init ----
palette();
applyTextFilter();
if (!CHAINS.length) tlRoot.querySelector('.tl-errnav').style.display = 'none';
if (!sMod) tlRoot.querySelector('[data-v=module]').style.display = 'none';
if (!sCpu) tlRoot.querySelector('[data-v=cpu]').style.display = 'none';
if (!D.caps.latency) { tlRoot.querySelector('[data-ov=irq]').classList.add('na'); tlRoot.querySelector('[data-ov=preempt]').classList.add('na'); }
if (!D.resched.length && !D.caps.latency) tlRoot.querySelector('[data-ov=rs]').classList.add('na');
if (!D.offcpu.length) tlRoot.querySelector('[data-ov=off]').classList.add('na');
if (!sDurAdj) tlRoot.querySelector('[data-act=adj]').style.display = 'none';
if (!HAS_INL) tlRoot.querySelector('[data-act=inl]').style.display = 'none';
else if (typeof lsGet === 'function' && lsGet('tlInline') === '1') toggleInlineView(true);
{
    const nn = elNotes.children.length, nb = tlRoot.querySelector('[data-act=notes]');
    if (!nn) nb.style.display = 'none';
    else {
        nb.textContent = 'Notes ' + nn;
        const on = prefGet('notes') !== '0';
        elNotes.classList.toggle('hide', !on); nb.classList.toggle('on', on);
    }
}
if (!WAKES.length) tlRoot.querySelector('[data-ov=wk]').classList.add('na');
if (!D.caps.retval) tlRoot.querySelector('[data-ov=err]').classList.add('na');
new MutationObserver(() => { palette(); ovDirty = true; draw(); scheduleDistHistogram(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
window.addEventListener('resize', () => { if (window.fgCurrentView() === 'timeline') { resize(); sizeDetailHead(); } });
if (typeof ResizeObserver !== 'undefined') {
    let controlsFrame = 0;
    const controlsObserver = new ResizeObserver(() => {
        if (controlsFrame || window.fgCurrentView() !== 'timeline') return;
        controlsFrame = requestAnimationFrame(() => {
            controlsFrame = 0;
            if (window.fgCurrentView() === 'timeline') resize();
        });
    });
    controlsObserver.observe(tlRoot.querySelector('.tl-toolbar'));
    controlsObserver.observe(elNotes);
}

// probe: read access to the model and a few actions, for tests and the browser console
const probe = () => ({ N, NA, SYN, S, sName, sStart, sEnd, sDepth, sParent, sTask, nameOf, isGhost, gapOf, TRACKS,
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
return { resize: resize, revealLine: revealLine, draw: draw, probe: probe, showHelp: () => elHelp.classList.add('show') };
