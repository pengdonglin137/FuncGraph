/* FuncGraph Timeline, part 4: detail panel, range summary, wiring, init */
function pill(cls, txt) { return '<span class="tl-pill ' + cls + '">' + esc(txt) + '</span>'; }
function kv(k, v, cls) { return '<span class="k">' + k + '</span><span class="v' + (cls ? ' ' + cls : '') + '">' + v + '</span>'; }
function lineLink(n, label) { return '<a class="tl-link" data-line="' + n + '">' + (label || 'line ' + n) + '</a>'; }

function showDetail(i) {
    range = null;
    const f = sFlags[i], d = sEnd[i] - sStart[i], t = TRACKS[sTask[i]];
    let pills = pill('ctx' + (f & FL.CTX), CTX_NAMES[f & FL.CTX] + (f & FL.CTXINF ? '?' : ''));
    if (f & FL.IRQOFF) pills += pill('warn', 'irqs off');
    if (f & FL.BHOFF) pills += pill('warn', 'BH off');
    if ((f >> FL.RS_SHIFT) & 7) pills += pill('warn', 'need-resched');
    if (eRole[i] >= 0) pills += pill('err', eRole[i] === 0 ? 'error origin' : 'error propagated');
    if (f & FL.TRUNC) pills += pill('mute', 'no exit');
    if (f & FL.ORPHAN) pills += pill('mute', 'no entry');
    if (f & FL.EST) pills += pill('mute', 'estimated');
    let h = '<div class="tl-detail-head"><span class="tl-detail-title">' + esc(nameOf(i)) +
        (modOf(i) ? ' [' + esc(modOf(i)) + ']' : '') + '</span>' + pills + '<span class="sp"></span>' +
        lineLink(sLine[i], 'Open in text view ↵') + '<button class="tl-close" title="Close (Esc)">×</button></div>';
    h += '<div class="tl-detail-body"><div class="tl-kv">';
    h += kv('duration', fmtDur(d) + (f & FL.EST ? ' (estimated)' : ''));
    h += kv('self', fmtDur(sSelf[i]) + (d > 0 ? ' (' + (100 * sSelf[i] / d).toFixed(1) + '%)' : ''));
    h += kv('start', '+' + fmtDur(sStart[i]) + (MODE === 'abs' ? ' &nbsp;' + fmtAbs(sStart[i]) + ' s' : ''));
    h += kv('task', esc(trackLabel(t)) + (cpuOf(i) >= 0 ? ' &nbsp;on CPU ' + cpuOf(i) : ''));
    h += kv('depth', String(sDepth[i]));
    if (f & FL.LAT) h += kv('flags', esc(ctxFlagText(f).replace(/^ · /, '') || 'none'));
    const r = retOf(i);
    if (r !== null) {
        let rv = esc(r);
        if (eRole[i] >= 0) rv += ' ' + esc(errName(eErr[i])) + ' · ' + esc(errRoleText(i));
        else if (f & FL.NULLRET) rv += ' (NULL)';
        h += kv('return', rv, eRole[i] >= 0 ? 'err' : '');
    }
    const c = callerOf(i);
    if (c) h += kv('call site', esc(c) + (f & FL.MISMATCH ? '<br><span class="tl-pill warn">caller differs from parent slice: untraced/inlined frame in between</span>' : ''));
    const a = argsOf(i);
    if (a) h += kv('args', esc(a));
    h += kv('trace lines', lineLink(sLine[i]) + (sEline && sEline[i] && sEline[i] !== sLine[i] ? ' → ' + lineLink(sEline[i]) : ''));
    h += '</div><div>';
    // error chain
    if (eChain[i] >= 0) {
        const ch = CHAINS[eChain[i]];
        h += '<div class="tl-sec">Error chain (' + ch[4] + ' frames, ' + esc(errName(ch[2]) || ch[2]) + ')</div><div class="tl-crumbs">';
        let k = ch[0], guard = 0;
        const items = [];
        while (k >= 0 && guard++ < 64) { items.push(k); if (k === ch[1]) break; k = sParent[k]; }
        for (const m of items.reverse()) h += '<div class="tl-crumb' + (m === i ? ' cur' : '') + '" data-slice="' + m + '"><span class="d">' + sDepth[m] + '</span> ' + esc(nameOf(m)) + ' <span class="d">' + esc(retOf(m) || '') + '</span></div>';
        h += '</div><div style="height:8px"></div>';
    }
    // call stack
    h += '<div class="tl-sec">Call stack</div><div class="tl-crumbs">';
    const stack = [];
    for (let k = i, g = 0; k >= 0 && g < 128; k = sParent[k], g++) stack.push(k);
    stack.reverse();
    if (stack.length && sDepth[stack[0]] > 0) h += '<div class="tl-crumb ghost">… ' + sDepth[stack[0]] + ' frame(s) outside trace window</div>';
    for (const m of stack) h += '<div class="tl-crumb' + (m === i ? ' cur' : '') + '" data-slice="' + m + '"><span class="d">' + fmtDur(sEnd[m] - sStart[m]).padStart(10, '\u00a0') + '</span>&nbsp; ' + '\u00a0'.repeat(Math.min(sDepth[m], 30)) + esc(nameOf(m)) + '</div>';
    h += '</div>';
    // heaviest children
    const kids = [];
    const tr = TRACKS[sTask[i]];
    if (sDepth[i] + 1 < tr.rows.length) {
        const row = tr.rows[sDepth[i] + 1];
        for (let q = lbEnd(row, sStart[i]); q < row.length && sStart[row[q]] <= sEnd[i]; q++) if (sParent[row[q]] === i) kids.push(row[q]);
    }
    if (kids.length) {
        const agg = new Map();
        for (const k of kids) { const a = agg.get(sName[k]) || [0, 0, k]; a[0]++; a[1] += sEnd[k] - sStart[k]; agg.set(sName[k], a); }
        const top = Array.from(agg.entries()).sort((x, y) => y[1][1] - x[1][1]).slice(0, 8);
        h += '<div style="height:8px"></div><div class="tl-sec">Children (' + kids.length + ' calls)</div><div class="tl-crumbs">';
        for (const [nm, a] of top) h += '<div class="tl-crumb" data-slice="' + a[2] + '"><span class="d">' + fmtDur(a[1]).padStart(10, '\u00a0') + '</span>&nbsp; ' + esc(S[nm]) + (a[0] > 1 ? ' <span class="d">×' + a[0] + '</span>' : '') + '</div>';
        h += '</div>';
    }
    h += '</div></div>';
    openDetail(h);
}
function openDetail(html) {
    const was = elDetail.classList.contains('show');
    elDetail.innerHTML = html;
    elDetail.classList.add('show');
    if (!was) resize();
}
function hideDetail() {
    if (!elDetail.classList.contains('show')) return;
    elDetail.classList.remove('show');
    resize();
}
elDetail.addEventListener('click', ev => {
    const a = ev.target.closest('[data-line]');
    if (a) { gotoText(+a.dataset.line); return; }
    const s = ev.target.closest('[data-slice]');
    if (s) { select(+s.dataset.slice, { reveal: true }); return; }
    if (ev.target.closest('.tl-close')) { range = null; select(-1); return; }
    const th = ev.target.closest('th[data-sort]');
    if (th) { sumSort = th.dataset.sort; showRangeSummary(); return; }
    const tr = ev.target.closest('tr[data-name]');
    if (tr) { elSearch.value = '^' + tr.dataset.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$'; applySearch(elSearch.value); }
});

// ---- range summary ----
let sumSort = 'self';
function showRangeSummary() {
    if (!range) return;
    const rs = range.s, re = range.e, span = re - rs;
    const agg = new Map();
    const ctxSelf = [0, 0, 0, 0];
    let offT = 0, nCalls = 0;
    const tracks = range.track !== null ? [TRACKS[range.track]] : ORDER;
    for (const t of tracks) {
        for (let i = t.lo; i >= 0 && i < t.hi; i++) {
            const s = sStart[i], e = sEnd[i];
            if (e <= rs || s >= re) continue;
            if (textHits && !textHits[i]) continue;
            const d = e - s, ovl = Math.min(e, re) - Math.max(s, rs);
            const selfPart = d > 0 ? sSelf[i] * ovl / d : 0;
            let a = agg.get(sName[i]);
            if (!a) { a = { calls: 0, incl: 0, self: 0, max: 0, err: 0, ex: i }; agg.set(sName[i], a); }
            if (s >= rs) { a.calls++; nCalls++; }
            a.incl += ovl;
            a.self += selfPart;
            if (d > a.max) { a.max = d; a.ex = i; }
            if (eRole[i] === 0) a.err++;
            ctxSelf[sFlags[i] & FL.CTX] += selfPart;
        }
        for (const r of t.off) if (r[2] > rs && r[1] < re) offT += Math.min(r[2], re) - Math.max(r[1], rs);
    }
    const rows = Array.from(agg.entries());
    const key = sumSort;
    rows.sort((x, y) => key === 'name' ? (S[x[0]] < S[y[0]] ? -1 : 1) : y[1][key] - x[1][key]);
    const maxv = rows.length ? Math.max(...rows.map(r => r[1].self)) || 1 : 1;
    let h = '<div class="tl-detail-head"><span class="tl-detail-title">Range ' + fmtDur(span) + '</span>';
    h += pill('mute', range.track !== null ? trackLabel(TRACKS[range.track]) : 'all tracks');
    h += pill('mute', nCalls + ' calls');
    if (textHits) h += pill('warn', 'filtered');
    for (let c = 0; c < 4; c++) if (ctxSelf[c] > 0) h += pill('ctx' + c, CTX_NAMES[c] + ' ' + fmtDur(ctxSelf[c]));
    if (offT > 0) h += pill('mute', 'off-CPU ' + fmtDur(offT));
    h += '<span class="sp"></span><button class="tl-close" title="Close (Esc)">×</button></div>';
    h += '<table class="tl-sum"><thead><tr>' +
        '<th data-sort="name">function</th><th data-sort="calls">calls</th><th data-sort="self">self</th>' +
        '<th data-sort="incl">inclusive</th><th data-sort="max">max</th><th data-sort="err">errors</th></tr></thead><tbody>';
    for (const [nm, a] of rows.slice(0, 300)) {
        h += '<tr data-name="' + esc(S[nm]) + '"><td><span class="bar" style="width:' + (60 * a.self / maxv).toFixed(1) + 'px"></span>' + esc(S[nm]) + '</td>' +
            '<td>' + a.calls + '</td><td>' + fmtDur(a.self) + '</td><td>' + fmtDur(a.incl) + '</td><td>' + fmtDur(a.max) + '</td>' +
            '<td class="' + (a.err ? 'err' : '') + '">' + (a.err || '') + '</td></tr>';
    }
    if (rows.length > 300) h += '<tr><td colspan="6">… ' + (rows.length - 300) + ' more</td></tr>';
    h += '</tbody></table>';
    sel = -1;
    openDetail(h);
    draw();
}

// ---- jump to the text view ----
function gotoText(n) {
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
function applyTextFilter() {
    const st = window.fgFilterState || { text: '', lines: null };
    if (document.activeElement !== elFtext) elFtext.value = st.text || '';
    if (!st.lines) {
        textHits = null;
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
        elFcount.textContent = cnt + ' of ' + N + ' calls';
        elFbar.classList.add('on');
    }
    tlRoot.querySelector('[data-act=fhide]').classList.toggle('on', hideUnmatched);
    if (sel >= 0 && textHits && !textHits[sel]) select(-1);
    rebuildErrList();
    layout();
    draw();
}
window.fgOnFilter = applyTextFilter;
elFtext.addEventListener('keydown', ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter') {
        const fi = document.getElementById('filterInput');
        fi.value = elFtext.value;
        elFtext.blur();
        if (elFtext.value.trim()) applyFilter(); else clearFilter();
    } else if (ev.key === 'Escape') { elFtext.value = (window.fgFilterState || {}).text || ''; elFtext.blur(); }
});
elFbar.addEventListener('click', ev => {
    const b = ev.target.closest('[data-act]');
    if (!b) return;
    if (b.dataset.act === 'fclear') { elFtext.value = ''; clearFilter(); }
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
        case 'errprev': gotoErr(-1); break;
        case 'errnext': gotoErr(1); break;
        case 'errmode': errFilter = errFilter ? 0 : 1; rebuildErrList(); draw(); break;
        case 'fit': fitAll(); break;
        case 'help': elHelp.classList.toggle('show'); break;
        case 'expand': {
            const any = ORDER.some(t => t.collapsed);
            for (const t of ORDER) t.collapsed = !any;
            layout(); draw(); break;
        }
    }
});

// ---- capability notes ----
(function notes() {
    const c = D.caps, out = [];
    const note = (html, info) => out.push('<span class="tl-note' + (info ? ' info' : '') + '">' + html + '</span>');
    if (MODE === 'compressed') note('No timestamps: slices are packed by duration and gaps are lost. Enable <code>options/funcgraph-abstime</code>.');
    else if (MODE === 'count') note('No timestamps or durations: the x axis shows call order only. Enable <code>funcgraph-abstime</code> and <code>funcgraph-duration</code>.');
    if (!c.latency) note('No latency columns: context is inferred from IRQ/softirq entry function names. Enable <code>options/latency-format</code> for exact irqs-off / need-resched / hardirq / softirq / preempt flags.');
    if (!c.proc && !c.switch_markers) note('No task info: tracks are per CPU. Enable <code>options/funcgraph-proc</code>.');
    if (!c.retval) note('No return values: error paths need <code>options/funcgraph-retval</code>.');
    else if (!D.btf) note('No BTF: errors are guessed from the value alone (void functions may show false positives). Use a vmlinux with <code>CONFIG_DEBUG_INFO_BTF</code> or pass <code>--btf</code>.');
    if (!c.retaddr) note('No call sites: enable <code>options/funcgraph-retaddr</code> to see where each call came from.', true);
    else if (!c.args) note('Arguments are not printed while <code>funcgraph-retaddr</code> is on (kernel limitation).', true);
    if (TIMED && !c.switch_markers && c.proc) note('No context-switch markers seen: off-CPU periods cannot be shown.', true);
    if (D.lost.length) note(D.lost.length + ' "LOST EVENTS" record(s): some slices are incomplete (marked with dotted edges). Increase <code>buffer_size_kb</code>.');
    const st = D.stats;
    if (st.truncated || st.orphans) note(st.truncated + ' call(s) without exit, ' + st.orphans + ' without entry (trace window edges).', true);
    elNotes.innerHTML = out.join('');
})();

elHelp.innerHTML = `
<h4>Navigate</h4><ul>
<li><kbd>W</kbd>/<kbd>S</kbd> or <kbd>Ctrl</kbd>+wheel: zoom at cursor</li>
<li><kbd>A</kbd>/<kbd>D</kbd>, drag, or <kbd>Shift</kbd>+wheel: pan</li>
<li>Wheel: scroll tracks · click track header: collapse</li>
<li><kbd>0</kbd> fit all · <kbd>F</kbd> zoom to selection · double-click: zoom to slice</li>
<li><kbd>Shift</kbd>+drag: summarize a range in this track (<kbd>Shift</kbd>+<kbd>Alt</kbd>: all tracks)</li></ul>
<h4>Select</h4><ul>
<li><kbd>↑</kbd> parent · <kbd>↓</kbd> first child · <kbd>[</kbd>/<kbd>]</kbd> siblings</li>
<li><kbd>E</kbd>/<kbd>Shift+E</kbd> next/prev error · <kbd>/</kbd> search, <kbd>N</kbd> next match</li>
<li><kbd>Enter</kbd>: open in text view · <kbd>Alt</kbd>+click a text line: show it here</li></ul>
<h4>Legend</h4><div class="tl-legend">
<i style="background:hsl(214,55%,70%)"></i><span>task context</span>
<i style="background:hsl(152,62%,68%)"></i><span>softirq</span>
<i style="background:hsl(30,62%,68%)"></i><span>hardirq</span>
<i style="background:hsl(282,62%,70%)"></i><span>NMI</span>
<i style="background:#d32f2f"></i><span>error origin (reaches user space)</span>
<i style="background:#e8873a"></i><span>error origin (handled by caller)</span>
<i style="background:#f6b8b8;outline:1.5px solid #c62828"></i><span>error propagation</span>
<i style="background:repeating-linear-gradient(135deg,#888 0 2px,#ddd 2px 4px)"></i><span>irqs disabled</span>
<i style="background:#f59e0b"></i><span>need-resched pending (header band / corner)</span>
<i style="background:repeating-linear-gradient(135deg,#aaa 0 2px,#eee 2px 4px)"></i><span>off-CPU (amber tint: preempted)</span>
<i style="background:#8e44ad"></i><span>caller ≠ parent (untraced frame)</span></div>`;

// ---- init ----
palette();
applyTextFilter();
if (!CHAINS.length) tlRoot.querySelector('.tl-errnav').style.display = 'none';
if (!sMod) tlRoot.querySelector('[data-v=module]').style.display = 'none';
if (!sCpu) tlRoot.querySelector('[data-v=cpu]').style.display = 'none';
if (!D.caps.latency) { tlRoot.querySelector('[data-ov=irq]').classList.add('na'); tlRoot.querySelector('[data-ov=preempt]').classList.add('na'); }
if (!D.resched.length && !D.caps.latency) tlRoot.querySelector('[data-ov=rs]').classList.add('na');
if (!D.offcpu.length) tlRoot.querySelector('[data-ov=off]').classList.add('na');
if (!D.caps.retval) tlRoot.querySelector('[data-ov=err]').classList.add('na');
new MutationObserver(() => { palette(); ovDirty = true; draw(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
window.addEventListener('resize', () => { if (document.body.classList.contains('tl-active')) resize(); });

return { resize: resize, revealLine: revealLine, draw: draw };
