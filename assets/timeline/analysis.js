/* FuncGraph Timeline: analysis panels (error summary, duration distribution, scheduling latency).
 * Shares the __tlBoot() scope; rendered into the detail area via openDetail(). */
const AN = { kind: null, func: -1, off: null, errSort: 'count', latSort: 'dur' };

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
    let minor = 0;
    for (let c = 0; c < CHAINS.length; c++) {
        const ch = CHAINS[c], o = ch[0];
        if (!matchOk(o)) continue;
        if (!chainShown(c)) { minor++; continue; }
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
        (textHits ? pill('warn', 'filtered') : '') + (D.btf || D.caps.retval_btf ? '' : pill('warn', 'no BTF: unverified')) +
        (minor || showMinor ? '<label class="an-minor"><input type="checkbox" data-act="minor"' + (showMinor ? ' checked' : '') + '> show ' +
            (showMinor ? '' : minor + ' ') + 'minor (handled at once, never propagated)</label>' : '') +
        '<span class="sp"></span><button class="tl-close" title="Close (Esc)">×</button></div>';
    if (!rows.length) {
        if (ATOMIC.size) h += '<div style="padding:12px" class="err">' + ATOMIC.size + ' sleeping function call(s) in atomic context, see the slice markers.</div>';
        h += '<div style="padding:12px">' + (D.caps.retval ? 'No error returns found.' : 'Error paths need <code>options/funcgraph-retval</code>.') + '</div>';
        openDetail(h); return;
    }
    if (ATOMIC.size) {
        const am = new Map();
        for (const [i, bits] of ATOMIC) { if (!matchOk(i)) continue; const k = nameOf(i) + '\u0000' + bits; const a = am.get(k) || [i, bits, 0]; a[2]++; am.set(k, a); }
        if (am.size) {
            h += '<div class="tl-sec" style="padding:8px 12px 2px;color:#c62828">Sleeping functions called in atomic context</div><table class="tl-sum an-tbl"><tbody>';
            for (const [i, bits, n] of am.values())
                h += `<tr data-slice="${i}" class="an-atomic"><td style="text-align:left">${esc(nameOf(i))}</td><td style="text-align:left" class="err">${esc(atomicText(bits))}</td>` +
                     `<td style="text-align:left">${esc(sParent[i] >= 0 ? 'in ' + nameOf(sParent[i]) : '')}</td><td>${n}×</td></tr>`;
            h += '</tbody></table>';
        }
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
let distCache = null, distView = null, distFrame = 0;
const DIST_PAGE = 50, DIST_SITE_PAGE = 12;
function distQuantile(ids, fraction) {
    return ids.length ? durOf(ids[ids.length - 1 - Math.round(fraction * (ids.length - 1))]) : 0;
}
function distributionData(name) {
    if (distCache && distCache.name === name && distCache.hits === textHits && distCache.durations === sDur) return distCache;
    const ids = [];
    for (let index = 0; index < N; index++) if (sName[index] === name && matchOk(index)) ids.push(index);
    ids.sort((left, right) => durOf(right) - durOf(left) || left - right);
    const sites = new Map();
    let total = 0;
    for (const index of ids) {
        const site = callerOf(index) || '?';
        let group = sites.get(site);
        if (!group) { group = { site, ids: [], total: 0 }; sites.set(site, group); }
        group.ids.push(index);
        group.total += durOf(index);
        total += durOf(index);
    }
    const siteRows = Array.from(sites.values()).sort((left, right) => right.ids.length - left.ids.length ||
        (left.site < right.site ? -1 : left.site > right.site ? 1 : 0));
    distCache = { name, hits: textHits, durations: sDur, ids, total, sites, siteRows };
    return distCache;
}
function distributionHistogram(ids) {
    const low = Math.log10(Math.max(1, distQuantile(ids, 0)));
    const high = Math.log10(Math.max(2, distQuantile(ids, 1))) + 1e-9;
    const count = Math.max(8, Math.min(40, Math.ceil((high - low) * 10)));
    const width = (high - low) / count;
    const buckets = Array.from({ length: count }, () => ({ ids: [], error: false }));
    let maxCount = 1;
    for (const index of ids) {
        const bucket = buckets[Math.max(0, Math.min(count - 1, Math.floor((Math.log10(Math.max(1, durOf(index))) - low) / width)))];
        bucket.ids.push(index);
        if (eRole[index] === 0) bucket.error = true;
        maxCount = Math.max(maxCount, bucket.ids.length);
    }
    return { low, high, width, buckets, maxCount };
}
function distCallIds() {
    return distView.bucket < 0 ? distView.population.ids : distView.histogram.buckets[distView.bucket].ids;
}
function distBucketBounds(index) {
    const histogram = distView.histogram;
    return [index ? Math.pow(10, histogram.low + index * histogram.width) : 0,
        Math.pow(10, histogram.low + (index + 1) * histogram.width)];
}
function distBucketLabel(index) {
    const bounds = distBucketBounds(index);
    return (index ? fmtDur(bounds[0]) + ' – < ' : '< ') + fmtDur(bounds[1]) + ' (' + distView.histogram.buckets[index].ids.length + ' calls)';
}
function showDist(nameIdx) {
    AN.kind = 'dist';
    if (AN.func !== nameIdx) AN.site = null;
    AN.func = nameIdx;
    const data = distributionData(nameIdx);
    const population = AN.site !== null ? data.sites.get(AN.site) || { ids: [], total: 0 } : data;
    const changedPopulation = !distView || distView.data !== data || distView.site !== AN.site;
    if (changedPopulation) {
        const siteVisible = distView && distView.data === data ? distView.siteVisible : DIST_SITE_PAGE;
        distView = { data, site: AN.site, population, histogram: distributionHistogram(population.ids),
            bucket: -1, visible: Math.min(15, population.ids.length), siteVisible };
    }
    const ids = population.ids;
    AN.distIds = ids;
    const quantile = fraction => distQuantile(ids, fraction);
    const statistic = value => ids.length ? fmtDur(value) : '—';
    let h = '<div class="tl-detail-head an-dist-head"><span class="tl-detail-title" title="' + esc(S[nameIdx]) + '">' + esc(S[nameIdx]) + '</span>' +
        pill('mute', ids.length + ' calls') + pill('mute', 'total ' + fmtDur(population.total)) + (textHits ? pill('warn', 'filtered') : '') +
        (adjOn ? pill('warn', 'overhead subtracted') : '') + (AN.site !== null ? '<span class="tl-pill warn" title="' + esc(AN.site) + '">from ' + esc(AN.site) + '</span>' : '') +
        '<span class="an-dist-actions">' + (AN.site !== null ? '<button class="tl-btn" data-act="dist-reset-site">All call sites</button>' : '') +
        '<button class="tl-close" title="Close (Esc)">×</button></span></div>';
    h += '<div class="an-dist"><div class="tl-kv">' +
        kv('min', statistic(quantile(0))) + kv('p50', statistic(quantile(0.5))) + kv('p90', statistic(quantile(0.9))) + kv('p99', statistic(quantile(0.99))) +
        kv('max', statistic(quantile(1))) + kv('mean', statistic(ids.length ? population.total / ids.length : 0)) +
        '</div><div class="an-hist-wrap"><canvas class="an-hist" height="120" role="img" aria-label="Duration histogram; use the duration bucket menu to filter calls"></canvas>' +
        '<div class="an-hint">Log-scale buckets; click a bar or choose a duration bucket to filter the call list and export, not these statistics.</div></div></div>';
    if (!ids.length) h += '<div class="an-hint">No calls match this function' + (AN.site !== null ? ' and call site' : '') + ' with the current filter.</div>';
    h += '<div class="an-dist-tools"><label>Duration bucket <select data-act="dist-bucket"' + (ids.length ? '' : ' disabled') + '><option value="-1">All durations</option>';
    distView.histogram.buckets.forEach((bucket, index) => {
        if (bucket.ids.length) h += '<option value="' + index + '">' + esc(distBucketLabel(index)) + '</option>';
    });
    h += '</select></label><button class="tl-btn" data-act="dist-reset-bucket">All durations</button>' +
        '<span class="an-dist-count" role="status"></span><button class="tl-btn" data-act="dist-more">Load more</button>' +
        '<button class="tl-btn" data-act="dist-csv">Export calls CSV</button></div>';
    h += '<table class="tl-sum an-tbl"><thead><tr><th>duration</th><th>self</th><th style="text-align:left">task</th><th>start</th><th style="text-align:left">caller</th><th>×p50</th></tr></thead><tbody class="an-list">' +
        slowRows(distCallIds().slice(0, distView.visible), quantile(0.5)) + '</tbody></table>' +
        '<div style="padding:6px 12px"><button class="tl-btn" data-act="slowfast">Slow vs fast: what do the slow calls do differently?</button></div>' +
        callSiteTable(data);
    openDetail(h);
    if (changedPopulation) elDetail.scrollTop = 0;
    updateDistControls();
}
function slowRows(list, p50) {
    return list.map(i => `<tr data-slice="${i}"><td>${fmtDur(durOf(i))}</td><td>${fmtDur(sSelf[i])}</td>` +
        `<td style="text-align:left">${esc(trackLabel(TRACKS[sTask[i]]))}</td><td>${fmtDur(sStart[i])}</td>` +
        `<td style="text-align:left">${esc(callerOf(i) || (sParent[i] >= 0 ? nameOf(sParent[i]) : ''))}</td>` +
        `<td>${p50 ? (durOf(i) / p50).toFixed(1) : ''}</td></tr>`).join('');
}
function updateDistControls() {
    const ids = distCallIds();
    elDetail.querySelector('.an-dist-count').textContent = 'Showing ' + distView.visible + ' of ' + ids.length + ' calls (slowest first)';
    elDetail.querySelector('[data-act=dist-more]').disabled = distView.visible >= ids.length;
    elDetail.querySelector('[data-act=dist-csv]').disabled = !ids.length;
    elDetail.querySelector('[data-act=dist-reset-bucket]').hidden = distView.bucket < 0;
    elDetail.querySelector('[data-act=dist-bucket]').value = distView.bucket;
}
function selectDistBucket(index) {
    if (!distView || !Number.isInteger(index) || index < -1 || index >= distView.histogram.buckets.length ||
        (index >= 0 && !distView.histogram.buckets[index].ids.length)) return;
    distView.bucket = index;
    distView.visible = Math.min(index < 0 ? 15 : DIST_PAGE, distCallIds().length);
    elDetail.querySelector('.an-list').innerHTML = slowRows(distCallIds().slice(0, distView.visible), distQuantile(distView.population.ids, 0.5));
    updateDistControls();
    scheduleDistHistogram();
}
function appendDistCalls() {
    const ids = distCallIds(), start = distView.visible;
    distView.visible = Math.min(start + DIST_PAGE, ids.length);
    elDetail.querySelector('.an-list').insertAdjacentHTML('beforeend', slowRows(ids.slice(start, distView.visible), distQuantile(distView.population.ids, 0.5)));
    updateDistControls();
}
function scheduleDistHistogram() {
    if (distFrame || AN.kind !== 'dist' || !elDetail.classList.contains('show') || window.fgCurrentView() !== 'timeline') return;
    distFrame = requestAnimationFrame(() => {
        distFrame = 0;
        if (AN.kind === 'dist' && elDetail.classList.contains('show') && window.fgCurrentView() === 'timeline') drawDistHistogram();
    });
}
function drawDistHistogram() {
    const canvas = elDetail.querySelector('.an-hist');
    if (!canvas || !distView) return;
    const width = canvas.parentElement.clientWidth, height = 120, ratio = window.devicePixelRatio || 1;
    if (width <= 0) return;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    canvas.style.height = height + 'px';
    const context = canvas.getContext('2d'), histogram = distView.histogram;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    if (!distView.population.ids.length) {
        context.fillStyle = P.sub; context.font = FONT_S;
        context.fillText('No matching calls', 12, 24);
        canvas.onclick = null;
        return;
    }
    const left = 40, plotWidth = width - left - 8, plotHeight = height - 28, count = histogram.buckets.length;
    const position = value => left + (Math.log10(Math.max(1, value)) - histogram.low) / (histogram.high - histogram.low) * plotWidth;
    context.fillStyle = P.sub; context.font = FONT_S; context.textBaseline = 'top';
    histogram.buckets.forEach((bucket, index) => {
        if (!bucket.ids.length) return;
        const barHeight = Math.max(1, plotHeight * Math.log1p(bucket.ids.length) / Math.log1p(histogram.maxCount));
        context.fillStyle = bucket.error ? P.errO : P.ovBar;
        context.globalAlpha = distView.bucket < 0 || distView.bucket === index ? 0.85 : 0.3;
        context.fillRect(left + index * plotWidth / count + 1, 4 + plotHeight - barHeight, plotWidth / count - 2, barHeight);
    });
    context.globalAlpha = 1;
    context.fillStyle = P.axis;
    for (let exponent = Math.ceil(histogram.low); exponent <= histogram.high; exponent++) {
        const coordinate = position(Math.pow(10, exponent));
        context.fillRect(coordinate, 4 + plotHeight, 1, 4);
        context.fillText(fmtDur(Math.pow(10, exponent)), coordinate + 2, 6 + plotHeight);
    }
    context.fillText(String(histogram.maxCount), 2, 4);
    for (const [fraction, label, color] of [[0.5, 'p50', P.rangeLine], [0.99, 'p99', P.rs]]) {
        const coordinate = position(distQuantile(distView.population.ids, fraction)), textWidth = context.measureText(label).width;
        const labelLeft = coordinate + 3 + textWidth > width ? coordinate - textWidth - 3 : coordinate + 3;
        const labelTop = label === 'p50' ? 4 : 18;
        context.fillStyle = color; context.fillRect(coordinate, 4, 1.5, plotHeight);
        context.fillStyle = P.bg; context.fillRect(labelLeft - 2, labelTop - 1, textWidth + 4, 13);
        context.fillStyle = color; context.fillText(label, labelLeft, labelTop);
    }
    canvas.onclick = event => {
        const bounds = canvas.getBoundingClientRect();
        const index = Math.floor((event.clientX - bounds.left - left) / (plotWidth / count));
        if (index >= 0 && index < count) selectDistBucket(index);
    };
}
function exportDistCsv() {
    const ids = distCallIds();
    if (!ids.length) return;
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const basis = MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'measured';
    const bounds = distView.bucket < 0 ? ['', ''] : distBucketBounds(distView.bucket).map(value => value / scale);
    const columns = ['function', 'duration_' + unit, 'self_' + unit, 'start_' + unit, 'end_' + unit,
        'task', 'caller', 'trace_line', 'error_origin', 'time_mode', 'duration_basis', 'filter', 'call_site_filter',
        'bucket_lower_' + unit, 'bucket_upper_' + unit];
    function* rows() {
        for (const index of ids) yield [nameOf(index), durOf(index) / scale, sSelf[index] / scale, sStart[index] / scale,
            sEnd[index] / scale, trackLabel(TRACKS[sTask[index]]), callerOf(index) || '', sLine[index], eRole[index] === 0 ? 1 : 0,
            MODE, basis, (window.fgFilterState || {}).text || '', AN.site || '', bounds[0], bounds[1]];
    }
    downloadTimelineCsv('funcgraph-calls-' + (MODE === 'count' ? 'count' : adjOn ? 'adjusted' : 'raw') + '.csv', columns, rows());
}
elDetail.addEventListener('change', event => {
    if (event.target.matches('[data-act=dist-bucket]')) selectDistBucket(+event.target.value);
});
elDetail.addEventListener('click', event => {
    const button = event.target.closest('[data-act]');
    if (!button || AN.kind !== 'dist') return;
    const action = button.dataset.act;
    if (action === 'dist-more') appendDistCalls();
    else if (action === 'dist-reset-bucket') selectDistBucket(-1);
    else if (action === 'dist-reset-site') { AN.site = null; showDist(AN.func); }
    else if (action === 'dist-csv') exportDistCsv();
    else if (action === 'dist-sites-more') {
        const start = distView.siteVisible;
        distView.siteVisible = Math.min(start + DIST_SITE_PAGE, distView.data.siteRows.length);
        elDetail.querySelector('.an-sites tbody').insertAdjacentHTML('beforeend', distSiteRows(distView.data.siteRows.slice(start, distView.siteVisible)));
        button.disabled = distView.siteVisible >= distView.data.siteRows.length;
        elDetail.querySelector('.an-site-count').textContent = 'Showing ' + distView.siteVisible + ' of ' + distView.data.siteRows.length + ' call sites';
    } else return;
    event.stopImmediatePropagation();
}, true);

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
            const g = sEnd[i] - sStart[i];
            const part = g > 0 ? sSelf[i] * (Math.min(sEnd[i], r[2]) - Math.max(sStart[i], r[1])) / g : 0;
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
        ['pre', 'max preempt off'], ['irq', 'irqs-off'], ['top', 'top self time inside', 1], ['end', 'ended by']], k, 'lsort') + '</thead><tbody>';
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

elDetail.addEventListener('change', ev => {
    if (ev.target.dataset && ev.target.dataset.act === 'minor') {
        showMinor = ev.target.checked; rebuildErrList(); draw(); showErrors();
    }
});
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
        setSelection(ch[0]); revealRow(ch[0]); draw();
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
    const sl = ev.target.closest('.an-list tr[data-slice], tr.an-atomic');
    if (sl) { const i = +sl.dataset.slice; setSelection(i); zoomTo(sStart[i], sEnd[i], i); draw(); ev.stopImmediatePropagation(); }
}, true);

// ---- 4. wait chain: off-CPU segment -> who woke it -> what the waker was waiting for ----
function offBefore(tid, t) {
    // latest off-CPU segment of task tid that ended at or before t
    let best = null;
    for (const r of TRACKS[tid].off) if (r[2] <= t && (!best || r[2] > best[2])) best = r;
    return best;
}
const RE_WAKE_INTERNAL = /^(?:try_to_wake_up|ttwu_\w+|wake_up\w*|__wake_up\w*|autoremove_wake_function|default_wake_function|woken_wake_function|pollwake|ep_poll_callback|complete\w*|swake_up\w*|wake_q_\w+|signal_wake_up\w*|_raw_spin_\w+|raw_spin_rq_\w+|preempt_count_\w+|select_task_rq\w*)$/;
function wakeSite(i) {
    // the first frame above the wakeup machinery: what the waker was actually doing
    let k = i;
    while (k >= 0 && RE_WAKE_INTERNAL.test(nameOf(k).split('.')[0])) k = sParent[k];
    return k >= 0 ? nameOf(k) : nameOf(i);
}
function showWaitChain(off) {
    AN.kind = 'wait';
    AN.off = off;
    const steps = [];
    let cur = off, guard = 0;
    while (cur && guard++ < 10) {
        const idx = OFF_IDX.get(cur);
        const w = WAKE_OF_OFF.get(idx);
        steps.push({ off: cur, w: w });
        if (!w) break;
        const waker = TRACKS[w[0]];
        if (waker.kind !== 0 || w[3] !== 0) break;          // woken from idle / irq context: chain ends
        cur = offBefore(w[0], w[1]);
        if (cur && w[1] - cur[2] > 50e6) break;            // waker ran long before waking: not a chain
    }
    const t = TRACKS[off[0]];
    let h = '<div class="tl-detail-head"><span class="tl-detail-title">Wait chain · ' + esc(trackLabel(t)) + '</span>' +
        pill(off[8] ? 'warn' : 'mute', (off[8] ? 'preempted ' : 'blocked ') + fmtDur(off[2] - off[1])) +
        (off[4] >= 0 ? pill('mute', 'in ' + S[off[4]]) : '') + (off[5] >= 0 ? pill('mute', 'state ' + S[off[5]]) : '') +
        '<span class="sp"></span><button class="tl-close" title="Close (Esc)">×</button></div>';
    if (!D.caps.sched_events) {
        h += '<div style="padding:12px">Who woke the task is unknown: enable <code>events/sched/sched_wakeup</code> ' +
            '(and <code>sched_switch</code>), e.g. <code>fgcapture.sh --sched</code>.</div>';
        openDetail(h); return;
    }
    h += '<table class="tl-sum an-tbl"><thead><tr><th style="text-align:left">task</th><th>waited</th><th style="text-align:left">waiting in</th>' +
        '<th style="text-align:left">woken by</th><th style="text-align:left">waker was in</th><th>wake latency</th><th>run again on</th></tr></thead><tbody>';
    for (const st of steps) {
        const r = st.off, w = st.w;
        const who = w ? trackLabel(TRACKS[w[0]]) + (w[3] ? ' <span class="tl-pill ctx' + w[3] + '">' + CTX_NAMES[w[3]] + '</span>' : '') : '<span class="d">not recorded</span>';
        const inFn = w && w[4] >= 0 ? esc(wakeSite(w[4])) : '';
        const lat = w ? r[2] - w[1] : 0;
        h += `<tr data-wait="${OFF_IDX.get(r)}"><td style="text-align:left">${esc(trackLabel(TRACKS[r[0]]))}</td><td>${fmtDur(r[2] - r[1])}</td>` +
            `<td style="text-align:left">${r[4] >= 0 ? esc(S[r[4]]) : ''}${r[5] >= 0 ? ' <span class="d">[' + esc(S[r[5]]) + ']</span>' : ''}</td>` +
            `<td style="text-align:left">${who}</td><td style="text-align:left">${inFn}</td>` +
            `<td class="${lat > 1e6 ? 'err' : ''}">${w ? fmtDur(lat) : ''}</td><td>${r[7] >= 0 ? 'CPU ' + r[7] : ''}</td></tr>`;
    }
    h += '</tbody></table><div class="an-hint">Wake latency = time from the wakeup to running again. ' +
        'Each further row is what the waker itself was waiting for before it woke this task. Click a row to show it.</div>';
    openDetail(h);
}
elDetail.addEventListener('click', ev => {
    const tr = ev.target.closest('tr[data-wait]');
    if (!tr) return;
    const r = D.offcpu[+tr.dataset.wait];
    const w = WAKE_OF_OFF.get(+tr.dataset.wait);
    const t = TRACKS[r[0]];
    t.hidden = false; t.collapsed = false;
    if (w) { TRACKS[w[0]].hidden = false; }
    layout();
    elScroll.scrollTop = Math.max(0, t.y - 4);
    zoomTo(w ? Math.min(w[1], r[1]) : r[1], r[2], -1);
    hovOff = r;
    draw();
    ev.stopImmediatePropagation();
}, true);

// ---- subtree helpers ----
function subtree(i) {
    // all descendants of slice i (same task, nested in time, parent chain reaches i)
    const t = TRACKS[sTask[i]], d0 = sDepth[i], s = sStart[i], e = sEnd[i], out = [];
    for (let d = d0 + 1; d < t.rowsT.length; d++) {
        const row = t.rowsT[d];
        let any = false;
        for (let q = lbEnd(row, s); q < row.length && sStart[row[q]] <= e; q++) {
            let k = row[q], g = 0;
            while (k >= 0 && sDepth[k] > d0 && g++ < 256) k = sParent[k];
            if (k === i) { out.push(row[q]); any = true; }
        }
        if (!any && d > d0 + 1) break;
    }
    return out;
}
// Partition of the measured wall time of slice i:
// own self | traced callees (on-CPU self) | hardirq/softirq that interrupted it | off-CPU (blocked / preempted)
function breakdown(i) {
    const ctx0 = sFlags[i] & FL.CTX, sub = subtree(i);
    const inIrq = new Set();
    let irq = 0, callees = 0, nCalls = sub.length;
    const subSet = new Set(sub);
    for (const k of sub) {           // sub is ordered by depth, so parents come first
        const p = sParent[k], c = sFlags[k] & FL.CTX;
        if (inIrq.has(p)) { inIrq.add(k); continue; }
        if (c > ctx0 && (p < 0 || (sFlags[p] & FL.CTX) < c)) { irq += sEnd[k] - sStart[k]; inIrq.add(k); continue; }
        callees += sSelfRaw[k];
    }
    let own = sSelfRaw[i], offB = 0, offP = 0;
    for (const r of TRACKS[sTask[i]].off) {
        if (r[2] <= sStart[i] || r[1] >= sEnd[i] || r[3] <= sDepth[i]) continue;
        const ov = Math.min(r[2], sEnd[i]) - Math.max(r[1], sStart[i]);
        if (r[8]) offP += ov; else offB += ov;
        // the off-CPU time sits in the self time of the frame that was open (depth r[3]-1)
        if (r[3] - 1 === sDepth[i]) own -= ov; else callees -= ov;
    }
    own = Math.max(0, own); callees = Math.max(0, callees);
    const ovh = D.overhead ? Math.min(own + callees, D.overhead.child * (nCalls - [...inIrq].length) + D.overhead.self) : 0;
    return { wall: sEnd[i] - sStart[i], own, callees, irq, offB, offP, ovh, nCalls };
}
function breakdownHtml(i) {
    const b = breakdown(i);
    if (b.wall <= 0) return '';
    const parts = [['own code', b.own, '#3b6fd8'], ['traced callees', b.callees, '#8fb3ef'], ['interrupts / softirq', b.irq, '#f0a35a'],
        ['blocked', b.offB, '#9aa3ad'], ['preempted', b.offP, '#f2c14e']].filter(p => p[1] > 0);
    let h = '<div class="tl-sec">Where the time went (' + fmtDur(b.wall) + ' measured)</div><div class="an-bd">';
    for (const p of parts) h += `<span style="width:${(100 * p[1] / b.wall).toFixed(2)}%;background:${p[2]}" title="${p[0]} ${fmtDur(p[1])}"></span>`;
    h += '</div><div class="an-bd-legend">';
    for (const p of parts) h += `<span><i style="background:${p[2]}"></i>${p[0]} <b>${fmtDur(p[1])}</b> ${(100 * p[1] / b.wall).toFixed(0)}%</span>`;
    if (b.ovh > 0) h += `<span class="d">of own + callees, ≈${fmtDur(b.ovh)} is tracing overhead (${b.nCalls} traced calls inside)</span>`;
    else if (b.nCalls > 0 && TIMED) h += `<span class="d">${b.nCalls} traced calls inside add tracing overhead (no estimate)</span>`;
    return h + '</div><div style="height:8px"></div>';
}

// ---- duration distribution: call sites and slow-vs-fast differential ----
function distSiteRows(rows) {
    return rows.map(row => '<tr data-site="' + esc(row.site) + '"><td><button class="an-site-btn" aria-pressed="' + (AN.site === row.site) + '">' +
        esc(row.site) + '</button></td><td>' + row.ids.length + '</td><td>' + fmtDur(distQuantile(row.ids, 0.5)) + '</td><td>' +
        fmtDur(distQuantile(row.ids, 0.99)) + '</td><td>' + fmtDur(distQuantile(row.ids, 1)) + '</td></tr>').join('');
}
function callSiteTable(data) {
    if (!sCaller || (data.siteRows.length < 2 && AN.site === null)) return '';
    let html = '<div class="tl-sec" style="padding:6px 12px 0">By call site</div><table class="tl-sum an-tbl an-sites"><thead><tr><th style="text-align:left">caller</th><th>calls</th><th>p50</th><th>p99</th><th>max</th></tr></thead><tbody>';
    html += distSiteRows(data.siteRows.slice(0, distView.siteVisible)) + '</tbody></table>';
    if (data.siteRows.length > DIST_SITE_PAGE) html += '<div class="an-dist-tools"><span class="an-site-count" role="status">Showing ' +
        Math.min(distView.siteVisible, data.siteRows.length) + ' of ' + data.siteRows.length + ' call sites</span><button class="tl-btn" data-act="dist-sites-more"' +
        (distView.siteVisible >= data.siteRows.length ? ' disabled' : '') + '>More call sites</button></div>';
    return html;
}
function slowFast(ids) {
    // compare the subtrees of the fastest half and the slowest 10% of the calls
    if (ids.length < 6) return '<div class="an-hint">Slow vs fast needs at least 6 calls.</div>';
    const byDur = ids.slice().sort((a, b) => durOf(a) - durOf(b));
    const fast = byDur.slice(0, Math.max(1, Math.floor(byDur.length / 2)));
    const slow = byDur.slice(Math.floor(byDur.length * 0.9));
    function profile(list) {
        const m = new Map();
        let irq = 0, offB = 0, offP = 0, own = 0;
        for (const i of list) {
            const b = breakdown(i);
            irq += b.irq; offB += b.offB; offP += b.offP; own += b.own;
            for (const k of subtree(i)) {
                const a = m.get(sName[k]) || [0, 0];
                a[0]++; a[1] += sSelfRaw[k];
                m.set(sName[k], a);
            }
        }
        const n = list.length;
        const out = new Map();
        for (const [k, a] of m) out.set(k, [a[0] / n, a[1] / n]);
        return { m: out, irq: irq / n, offB: offB / n, offP: offP / n, own: own / n, dur: list.reduce((s, i) => s + durOf(i), 0) / n };
    }
    const F = profile(fast), Sl = profile(slow);
    const rows = [];
    for (const k of new Set([...F.m.keys(), ...Sl.m.keys()])) {
        const a = F.m.get(k) || [0, 0], b = Sl.m.get(k) || [0, 0];
        rows.push([S[k], a[0], b[0], a[1], b[1]]);
    }
    rows.push(['(own code)', 1, 1, F.own, Sl.own], ['(interrupts / softirq)', 0, 0, F.irq, Sl.irq],
              ['(blocked off-CPU)', 0, 0, F.offB, Sl.offB], ['(preempted)', 0, 0, F.offP, Sl.offP]);
    rows.sort((x, y) => (y[4] - y[3]) - (x[4] - x[3]));
    let h = `<div class="tl-sec" style="padding:6px 12px 0">Slow vs fast: slowest ${slow.length} calls (avg ${fmtDur(Sl.dur)}) vs fastest ${fast.length} (avg ${fmtDur(F.dur)}), per call</div>` +
        '<table class="tl-sum an-tbl"><thead><tr><th style="text-align:left">function inside</th><th>calls fast</th><th>calls slow</th><th>self fast</th><th>self slow</th><th>Δ self</th></tr></thead><tbody>';
    for (const r of rows.filter(r => Math.abs(r[4] - r[3]) > 0).slice(0, 15)) {
        const d = r[4] - r[3];
        h += `<tr><td style="text-align:left">${esc(r[0])}</td><td>${r[1] ? r[1].toFixed(1) : ''}</td><td>${r[2] ? r[2].toFixed(1) : ''}</td>` +
            `<td>${fmtDur(r[3])}</td><td>${fmtDur(r[4])}</td><td class="${d > 0 ? 'err' : ''}">${d > 0 ? '+' : ''}${fmtDur(d)}</td></tr>`;
    }
    return h + '</tbody></table><div class="an-hint">Self time per call of each function in the subtree. Large Δ rows explain where the slow calls spend the extra time.</div>';
}

// ---- critical sections: irqs-off / preempt-off / BH-off ----
AN.secKind = 0;
function showSections() {
    AN.kind = 'sec';
    const counts = [0, 0, 0], tot = [0, 0, 0], mx = [0, 0, 0];
    for (const r of SECTIONS) { const d = r[3] - r[2]; counts[r[1]]++; tot[r[1]] += d; if (d > mx[r[1]]) mx[r[1]] = d; }
    let h = '<div class="tl-detail-head"><span class="tl-detail-title">Critical sections</span><span class="tl-seg an-seckind">';
    for (let k = 0; k < 3; k++) h += `<button data-sk="${k}" class="${k === AN.secKind ? 'on' : ''}">${SEC_NAMES[k]} (${counts[k]})</button>`;
    h += '</span>' + pill('mute', 'total ' + fmtDur(tot[AN.secKind])) + pill('warn', 'max ' + fmtDur(mx[AN.secKind])) +
        (textHits ? pill('warn', 'filtered') : '') + '<span class="sp"></span><button class="tl-close" title="Close (Esc)">×</button></div>';
    if (!D.caps.latency) {
        openDetail(h + '<div style="padding:12px">Needs <code>options/latency-format</code> (irqs-off / preempt-depth / BH flags).</div>'); return;
    }
    const list = SECTIONS.map((r, k) => k).filter(k => SECTIONS[k][1] === AN.secKind && (!textHits || TRACKS[SECTIONS[k][0]].hits))
        .sort((a, b) => (SECTIONS[b][3] - SECTIONS[b][2]) - (SECTIONS[a][3] - SECTIONS[a][2]));
    h += '<div class="an-hint" style="padding:6px 12px 0">Regions where a task ran with ' + SEC_NAMES[AN.secKind] + ' (task context; preempt depth above the trace baseline ' + (D.preempt_base || 0) + '). ' +
        'The flag changes inside untraced code, so start/end are the traced frames around the change.</div>';
    h += '<table class="tl-sum an-tbl"><thead><tr><th>duration</th><th>start</th><th style="text-align:left">task</th><th style="text-align:left">started in</th><th style="text-align:left">ended in</th>' +
        (AN.secKind === 1 ? '<th>max depth</th>' : '') + '<th style="text-align:left">top self time inside</th></tr></thead><tbody>';
    for (const k of list.slice(0, 200)) {
        const r = SECTIONS[k], t = TRACKS[r[0]];
        const blame = new Map();
        for (let i = t.lo; i >= 0 && i < t.hi; i++) {
            if (sEnd[i] <= r[2] || sStart[i] >= r[3]) continue;
            const g = sEnd[i] - sStart[i];
            if (g > 0) blame.set(sName[i], (blame.get(sName[i]) || 0) + sSelfRaw[i] * (Math.min(sEnd[i], r[3]) - Math.max(sStart[i], r[2])) / g);
        }
        const top = Array.from(blame.entries()).sort((a, b) => b[1] - a[1]).slice(0, 3);
        h += `<tr data-sec="${k}"><td class="${r[3] - r[2] > 100000 ? 'err' : ''}">${fmtDur(r[3] - r[2])}</td><td>${fmtDur(r[2])}</td>` +
            `<td style="text-align:left">${esc(trackLabel(t))}</td><td style="text-align:left">${r[4] >= 0 ? esc(nameOf(r[4])) : '<span class="d">top level</span>'}</td>` +
            `<td style="text-align:left">${r[5] >= 0 ? esc(nameOf(r[5])) : '<span class="d">' + (r[8] < 0 ? 'switched out / end' : 'interrupt') + '</span>'}</td>` +
            (AN.secKind === 1 ? `<td>${r[6]}</td>` : '') +
            `<td style="text-align:left">${top.map(([nm, v]) => esc(S[nm]) + ' <span class="d">' + fmtDur(v) + '</span>').join(', ')}</td></tr>`;
    }
    if (list.length > 200) h += `<tr><td colspan="7">… ${list.length - 200} more</td></tr>`;
    openDetail(h + '</tbody></table>');
}
elDetail.addEventListener('click', ev => {
    const sk = ev.target.closest('[data-sk]');
    if (sk) { AN.secKind = +sk.dataset.sk; showSections(); ev.stopImmediatePropagation(); return; }
    const sr = ev.target.closest('tr[data-sec]');
    if (sr) {
        const r = SECTIONS[+sr.dataset.sec], t = TRACKS[r[0]];
        t.hidden = false; t.collapsed = false; layout();
        elScroll.scrollTop = Math.max(0, t.y - 4);
        zoomTo(r[2], r[3], -1);
        range = { s: r[2], e: r[3], track: r[0] };
        draw();
        ev.stopImmediatePropagation(); return;
    }
    const st = ev.target.closest('tr[data-site]');
    if (st && AN.kind === 'dist') { AN.site = AN.site === st.dataset.site ? null : st.dataset.site; showDist(AN.func); ev.stopImmediatePropagation(); return; }
    const sfb = ev.target.closest('[data-act=slowfast]');
    if (sfb) { sfb.outerHTML = slowFast(AN.distIds); ev.stopImmediatePropagation(); }
}, true);
