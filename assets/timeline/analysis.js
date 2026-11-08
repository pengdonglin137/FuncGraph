/* FuncGraph Timeline: analysis panels (error summary, duration distribution, scheduling latency).
 * Shares the __tlBoot() scope; rendered into the detail area via openDetail(). */
const AN = { kind: null, func: -1, errSort: 'count', latSort: 'dur' };

function matchOk(i) { return !textHits || textHits[i]; }
function syscallOf(i) {
    // nearest ancestor (or self) that is a syscall entry, else the top frame
    let k = i, top = i;
    while (k >= 0) {
        if (/^(?:__(?:arm64|x64|ia32|x32|riscv|s390x?|powerpc|loongarch|se)_)?(?:compat_)?sys_\w+$|^__do_sys_/.test(nameOf(k).split('.')[0])) return k;
        top = k;
        k = sParent[k];
    }
    return top;
}
function sysLabel(i) { return nameOf(i).replace(/^__(?:arm64|x64|ia32|x32|riscv|s390x?|powerpc|loongarch)_sys_/, '').replace(/^__do_sys_/, ''); }
function tblHead(cols, sortKey, attr) {
    return '<tr>' + cols.map(c => `<th data-${attr}="${c[0]}" class="${c[0] === sortKey ? 'on' : ''}"${c[2] ? ' style="text-align:left"' : ''}>${c[1]}</th>`).join('') + '</tr>';
}

// ---- 1. error summary: syscall -> origin -> errno ----
function showErrors() {
    AN.kind = 'errors';
    const groups = new Map();
    let total = 0;
    for (let c = 0; c < CHAINS.length; c++) {
        const ch = CHAINS[c], o = ch[0];
        if (!matchOk(o)) continue;
        total++;
        const sc = syscallOf(ch[1]);
        const key = sysLabel(sc) + '\u0000' + nameOf(o) + '\u0000' + ch[2];
        let g = groups.get(key);
        if (!g) {
            g = { sys: sysLabel(sc), isSys: sc !== ch[1] || /sys_/.test(nameOf(sc)), origin: nameOf(o), errno: ch[2], fate: [0, 0, 0],
                  count: 0, tasks: new Set(), first: c, ver: 1, len: 0, ex: [] };
            groups.set(key, g);
        }
        g.count++;
        g.fate[ch[3]]++;
        g.tasks.add(sTask[o]);
        g.ver &= ch[5];
        g.len = Math.max(g.len, ch[4]);
        if (g.ex.length < 50) g.ex.push(c);
    }
    const rows = Array.from(groups.values());
    const k = AN.errSort;
    rows.sort((a, b) => k === 'sys' ? (a.sys < b.sys ? -1 : 1) : k === 'origin' ? (a.origin < b.origin ? -1 : 1) :
        k === 'errno' ? a.errno - b.errno : k === 'user' ? b.fate[1] - a.fate[1] : b.count - a.count);
    let h = '<div class="tl-detail-head"><span class="tl-detail-title">Errors</span>' +
        pill('err', total + ' error chains') + pill('mute', rows.length + ' groups') +
        (textHits ? pill('warn', 'filtered') : '') + (D.btf ? '' : pill('warn', 'no BTF: unverified')) +
        '<span class="sp"></span><button class="tl-close" title="Close (Esc)">×</button></div>';
    if (!rows.length) {
        h += '<div style="padding:12px">' + (D.caps.retval ? 'No error returns found.' : 'Error paths need <code>options/funcgraph-retval</code>.') + '</div>';
        openDetail(h); return;
    }
    h += '<table class="tl-sum an-tbl"><thead>' + tblHead([['sys', 'syscall / entry', 1], ['origin', 'origin', 1], ['errno', 'errno'],
        ['count', 'count'], ['user', 'to user'], ['h', 'handled'], ['t', 'tasks'], ['x', 'chain']], k, 'esort') + '</thead><tbody>';
    for (const g of rows) {
        const en = errName(g.errno);
        h += `<tr data-chain="${g.first}" data-chains="${g.ex.join(',')}"><td style="text-align:left">${g.isSys ? '' : '<span class="tl-pill mute">no syscall</span>'}${esc(g.sys)}</td>` +
            `<td style="text-align:left">${esc(g.origin)}</td><td class="err">${g.errno}${en ? ' ' + en : ''}</td>` +
            `<td>${g.count}</td><td class="${g.fate[1] ? 'err' : ''}">${g.fate[1] || ''}</td><td>${g.fate[0] || ''}</td>` +
            `<td>${g.tasks.size}</td><td>${g.len} frames${g.ver ? '' : ' ?'}</td></tr>`;
    }
    h += '</tbody></table><div class="an-hint">Click a row to jump to the first occurrence; click again for the next one.</div>';
    openDetail(h);
}

// ---- 2. duration distribution of one function ----
function showDist(nameIdx) {
    AN.kind = 'dist';
    AN.func = nameIdx;
    const ids = [];
    for (let i = 0; i < N; i++) if (sName[i] === nameIdx && matchOk(i)) ids.push(i);
    const durs = ids.map(i => sEnd[i] - sStart[i]).sort((a, b) => a - b);
    const q = p => durs.length ? durs[Math.min(durs.length - 1, Math.floor(p * (durs.length - 1) + 0.5))] : 0;
    const sum = durs.reduce((s, x) => s + x, 0);
    let h = '<div class="tl-detail-head"><span class="tl-detail-title">' + esc(S[nameIdx]) + '</span>' +
        pill('mute', ids.length + ' calls') + pill('mute', 'total ' + fmtDur(sum)) + (textHits ? pill('warn', 'filtered') : '') +
        '<span class="sp"></span><button class="tl-close" title="Close (Esc)">×</button></div>';
    if (!ids.length) { openDetail(h + '<div style="padding:12px">No calls.</div>'); return; }
    h += '<div class="an-dist"><div class="tl-kv">' +
        kv('min', fmtDur(durs[0])) + kv('p50', fmtDur(q(0.5))) + kv('p90', fmtDur(q(0.9))) + kv('p99', fmtDur(q(0.99))) +
        kv('max', fmtDur(durs[durs.length - 1])) + kv('mean', fmtDur(sum / durs.length)) +
        '</div><div><canvas class="an-hist" height="150"></canvas><div class="an-hint">Log-scale buckets; click a bar to list its calls. ' +
        'Slowest calls are listed below.</div></div></div>';
    const slow = ids.slice().sort((a, b) => (sEnd[b] - sStart[b]) - (sEnd[a] - sStart[a])).slice(0, 15);
    h += '<table class="tl-sum an-tbl"><thead><tr><th>duration</th><th>self</th><th style="text-align:left">task</th><th>start</th><th style="text-align:left">caller</th><th>×p50</th></tr></thead><tbody class="an-list">' +
        slowRows(slow, q(0.5)) + '</tbody></table>';
    openDetail(h);
    drawHist(elDetail.querySelector('.an-hist'), ids, durs, q(0.5), q(0.99));
}
function slowRows(list, p50) {
    return list.map(i => `<tr data-slice="${i}"><td>${fmtDur(sEnd[i] - sStart[i])}</td><td>${fmtDur(sSelf[i])}</td>` +
        `<td style="text-align:left">${esc(trackLabel(TRACKS[sTask[i]]))}</td><td>${fmtDur(sStart[i])}</td>` +
        `<td style="text-align:left">${esc(callerOf(i) || (sParent[i] >= 0 ? nameOf(sParent[i]) : ''))}</td>` +
        `<td>${p50 ? ((sEnd[i] - sStart[i]) / p50).toFixed(1) : ''}</td></tr>`).join('');
}
function drawHist(cv, ids, durs, p50, p99) {
    const w = cv.parentElement.clientWidth || 500, hh = 150, dpr = window.devicePixelRatio || 1;
    cv.width = w * dpr; cv.height = hh * dpr; cv.style.width = w + 'px'; cv.style.height = hh + 'px';
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const lo = Math.log10(Math.max(1, durs[0])), hi = Math.log10(Math.max(2, durs[durs.length - 1])) + 1e-9;
    const NB = Math.max(8, Math.min(40, Math.ceil((hi - lo) * 10)));
    const bw = (hi - lo) / NB || 1;
    const buckets = Array.from({ length: NB }, () => []);
    for (const i of ids) {
        const d = Math.max(1, sEnd[i] - sStart[i]);
        buckets[Math.min(NB - 1, Math.floor((Math.log10(d) - lo) / bw))].push(i);
    }
    const mx = Math.max(...buckets.map(b => b.length));
    const L = 40, B = 20, PW = w - L - 8, PH = hh - B - 8;
    g.fillStyle = P.sub; g.font = FONT_S; g.textBaseline = 'top';
    const x = v => L + (Math.log10(Math.max(1, v)) - lo) / (hi - lo || 1) * PW;
    buckets.forEach((b, k) => {
        if (!b.length) return;
        const bh = Math.max(1, PH * Math.log1p(b.length) / Math.log1p(mx));
        g.fillStyle = eRoleAny(b) ? P.errO : P.ovBar;
        g.globalAlpha = 0.75;
        g.fillRect(L + k * PW / NB + 1, 4 + PH - bh, PW / NB - 2, bh);
    });
    g.globalAlpha = 1;
    g.fillStyle = P.axis;
    for (let e = Math.ceil(lo); e <= hi; e++) {
        const xx = x(Math.pow(10, e));
        g.fillRect(xx, 4 + PH, 1, 4);
        g.fillText(fmtDur(Math.pow(10, e)), xx + 2, 6 + PH);
    }
    g.fillText(String(mx), 2, 4);
    for (const [v, lab, col] of [[p50, 'p50', P.rangeLine], [p99, 'p99', P.rs]]) {
        const xv = x(v), tw = g.measureText(lab).width;
        g.fillStyle = col; g.fillRect(xv, 4, 1.5, PH);
        g.fillText(lab, xv + 3 + tw > w ? xv - tw - 3 : xv + 3, lab === 'p50' ? 4 : 18);
    }
    cv.onclick = ev => {
        const r = cv.getBoundingClientRect();
        const k = Math.floor((ev.clientX - r.left - L) / (PW / NB));
        if (k < 0 || k >= NB || !buckets[k].length) return;
        const list = buckets[k].slice().sort((a, b) => (sEnd[b] - sStart[b]) - (sEnd[a] - sStart[a])).slice(0, 50);
        elDetail.querySelector('.an-list').innerHTML = slowRows(list, p50);
    };
}
function eRoleAny(list) { for (const i of list) if (eRole[i] === 0) return true; return false; }

// ---- 3. scheduling latency: need-resched windows and who delayed them ----
function showLatency() {
    AN.kind = 'lat';
    const wins = [];
    for (const r of D.resched) {   // [task, start, end, maxPreempt, irqoff, closedBy]
        const t = TRACKS[r[0]];
        if (textHits && !t.hits) continue;
        const dur = r[2] - r[1];
        if (dur <= 0) continue;
        // the deepest-in-time culprit: largest self time inside the window on this task
        const blame = new Map();
        let irqT = 0;
        for (let i = t.lo; i >= 0 && i < t.hi; i++) {
            if (sEnd[i] <= r[1] || sStart[i] >= r[2]) continue;
            const d = sEnd[i] - sStart[i];
            const part = d > 0 ? sSelf[i] * (Math.min(sEnd[i], r[2]) - Math.max(sStart[i], r[1])) / d : 0;
            blame.set(sName[i], (blame.get(sName[i]) || 0) + part);
            if (sFlags[i] & FL.IRQOFF) irqT += part;
        }
        const top = Array.from(blame.entries()).sort((a, b) => b[1] - a[1]).slice(0, 3);
        wins.push({ r, dur, top, irqT });
    }
    const agg = new Map();
    for (const w of wins) for (const [nm, v] of w.top) agg.set(nm, (agg.get(nm) || 0) + v);
    const k = AN.latSort;
    wins.sort((a, b) => k === 'start' ? a.r[1] - b.r[1] : k === 'pre' ? b.r[3] - a.r[3] : b.dur - a.dur);
    const totalLat = wins.reduce((s, w) => s + w.dur, 0);
    let h = '<div class="tl-detail-head"><span class="tl-detail-title">Scheduling latency</span>' +
        pill('warn', wins.length + ' need-resched windows') + pill('mute', 'total ' + fmtDur(totalLat)) +
        (wins.length ? pill('mute', 'max ' + fmtDur(Math.max(...wins.map(w => w.dur)))) : '') +
        '<span class="sp"></span><button class="tl-close" title="Close (Esc)">×</button></div>';
    if (!wins.length) {
        h += '<div style="padding:12px">' + (D.caps.latency ? 'No pending need-resched windows in this trace.' :
            'Needs <code>options/latency-format</code> (need-resched flag) and timestamps.') + '</div>';
        openDetail(h); return;
    }
    const topAgg = Array.from(agg.entries()).sort((a, b) => b[1] - a[1]).slice(0, 8);
    h += '<div class="an-hint" style="padding:6px 12px 0">Window = need-resched set until the task schedules (or is switched out). ' +
        'Blame = self time spent inside the window; irqs-off/preempt-disabled code delays the reschedule.</div>' +
        '<div class="an-blame">' + topAgg.map(([nm, v]) => `<span class="an-chip"><span class="k">${esc(S[nm])}</span>${fmtDur(v)}</span>`).join('') + '</div>';
    h += '<table class="tl-sum an-tbl"><thead>' + tblHead([['dur', 'latency'], ['start', 'start'], ['task', 'task', 1],
        ['pre', 'max preempt'], ['irq', 'irqs-off'], ['top', 'top self time inside', 1], ['end', 'ended by']], k, 'lsort') + '</thead><tbody>';
    for (const w of wins.slice(0, 300)) {
        const r = w.r;
        h += `<tr data-win="${r[1]},${r[2]},${r[0]}"><td class="${w.dur > 100000 ? 'err' : ''}">${fmtDur(w.dur)}</td><td>${fmtDur(r[1])}</td>` +
            `<td style="text-align:left">${esc(trackLabel(TRACKS[r[0]]))}</td><td>${r[3] || ''}</td><td>${w.irqT ? fmtDur(w.irqT) : ''}</td>` +
            `<td style="text-align:left">${w.top.map(([nm, v]) => esc(S[nm]) + ' <span class="d">' + fmtDur(v) + '</span>').join(', ')}</td>` +
            `<td>${['flag cleared', 'switched out', 'trace end'][r[5]] || ''}</td></tr>`;
    }
    h += '</tbody></table>';
    openDetail(h);
}

elDetail.addEventListener('click', ev => {
    const th = ev.target.closest('th[data-esort], th[data-lsort]');
    if (th) {
        if (th.dataset.esort) { AN.errSort = th.dataset.esort; showErrors(); } else { AN.latSort = th.dataset.lsort; showLatency(); }
        ev.stopImmediatePropagation(); return;
    }
    const ec = ev.target.closest('tr[data-chains]');
    if (ec) {
        const list = ec.dataset.chains.split(',').map(Number);
        const pos = (+ec.dataset.pos || 0) % list.length;
        ec.dataset.pos = pos + 1;
        const ch = CHAINS[list[pos]];
        zoomTo(sStart[ch[1]], sEnd[ch[1]], ch[0]);
        sel = ch[0]; revealRow(ch[0]); draw();
        toast('occurrence ' + (pos + 1) + ' / ' + list.length);
        ev.stopImmediatePropagation(); return;
    }
    const wr = ev.target.closest('tr[data-win]');
    if (wr) {
        const [s, e, t] = wr.dataset.win.split(',').map(Number);
        const tr = TRACKS[t];
        tr.hidden = false; tr.collapsed = false; layout();
        elScroll.scrollTop = Math.max(0, tr.y - 4);
        zoomTo(s, e, -1);
        range = { s: s, e: e, track: t };
        draw();
        ev.stopImmediatePropagation(); return;
    }
    const sl = ev.target.closest('.an-list tr[data-slice]');
    if (sl) { const i = +sl.dataset.slice; sel = i; zoomTo(sStart[i], sEnd[i], i); draw(); ev.stopImmediatePropagation(); }
}, true);
