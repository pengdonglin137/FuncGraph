/* FuncGraph Compare view: per-function differences between a base trace and this one. */
window.fgViews = window.fgViews || {};
window.fgViews.compare = { el: 'compareView', init: function () {
    const D = JSON.parse(document.getElementById('cmpData').textContent);
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
    const val = (col, r, side) => {
        const x = side === 'b' ? col.b : col.c;
        return typeof x === 'function' ? x(r) : r[x];
    };
    let sortKey = 'self', sortBy = 'absdiff', query = null, onlyChanged = true, limit = 400;
    let ROWS = D.rows;
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
        <input class="cmp-q" placeholder="filter function (regex)" spellcheck="false">
        <label><input type="checkbox" class="cmp-changed" checked> changed only</label>
        <span class="cmp-label">sort</span>
        <span class="tl-seg cmp-sortby">
          <button data-v="absdiff" class="on">|Δ|</button><button data-v="diff">Δ</button>
          <button data-v="pct">Δ%</button><button data-v="cur">current</button><button data-v="base">base</button>
        </span>
        <label class="cmp-adj" style="display:none"><input type="checkbox"> subtract tracing overhead</label>
        <span class="cmp-count"></span>
      </div>
      <div class="cmp-note">Durations include tracing overhead; compare traces captured with the same options. Self time = inclusive minus traced children.</div>
      <div class="cmp-empty" role="status" hidden>No functions match the current filters.</div>
      <div class="cmp-wrap" tabindex="0" role="region" aria-label="Function comparison table"><table class="cmp-table"><thead></thead><tbody></tbody></table></div>`;
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
    function resize() {
        const top = Math.max(0, wrap.getBoundingClientRect().top);
        wrap.style.maxHeight = Math.max(160, window.innerHeight - top - 12) + 'px';
    }
    function header() {
        let h = '<tr><th rowspan="2" data-k="name" class="l">function</th>';
        for (const c of COLS.slice(1)) h += `<th colspan="3" data-k="${c.k}" class="${c.k === sortKey ? 'on' : ''}">${c.t}</th>`;
        h += '</tr><tr>';
        for (const c of COLS.slice(1)) h += `<th class="sub">${esc(bn)}</th><th class="sub">${esc(cn)}</th><th class="sub">Δ</th>`;
        thead.innerHTML = h + '</tr>';
    }
    function render() {
        header();
        const col = COLS.find(c => c.k === sortKey) || COLS[2];
        let rows = ROWS;
        if (query) rows = rows.filter(r => query.test(r[0]));
        if (onlyChanged) rows = rows.filter(row => row[1] !== row[6] || row[2] !== row[7] ||
            row[3] !== row[8] || row[4] !== row[9] || row[5] !== row[10]);
        const key = r => {
            if (col.k === 'name') return r[0];
            const b = val(col, r, 'b'), c = val(col, r, 'c');
            switch (sortBy) {
                case 'diff': return c - b;
                case 'pct': { const p = pct(b, c); return p === Infinity ? 1e12 : p; }
                case 'cur': return c;
                case 'base': return b;
                default: return Math.abs(c - b);
            }
        };
        rows = rows.slice().sort((x, y) => col.k === 'name' ? (x[0] < y[0] ? -1 : 1) : key(y) - key(x));
        const maxd = Math.max(1, ...rows.slice(0, limit).map(r => Math.abs(val(col, r, 'c') - val(col, r, 'b'))));
        let h = '';
        for (const r of rows.slice(0, limit)) {
            h += `<tr><td class="l" title="${esc(r[0])}">${esc(r[0])}${!r[1] ? ' <span class="cmp-tag new">new</span>' : !r[6] ? ' <span class="cmp-tag gone">gone</span>' : ''}</td>`;
            for (const c of COLS.slice(1)) {
                const b = val(c, r, 'b'), v = val(c, r, 'c'), d = v - b;
                const p = pct(b, v);
                const cls = d === 0 ? '' : (Math.abs(p) < 5 && p !== Infinity) ? 'small' : d > 0 ? 'up' : 'down';
                const bar = c === col ? `<span class="cmp-dbar ${d > 0 ? 'up' : 'down'}" style="width:${(40 * Math.abs(d) / maxd).toFixed(1)}px"></span>` : '';
                h += `<td>${c.time ? fmt(b) : b}</td><td>${c.time ? fmt(v) : v}</td>` +
                     `<td class="d ${cls}" title="${fmtPct(p)}">${bar}${d ? sign(d, c.time) : ''}</td>`;
            }
            h += '</tr>';
        }
        if (rows.length > limit) h += `<tr><td class="l more" colspan="${1 + 3 * (COLS.length - 1)}">… ${rows.length - limit} more (click to show)</td></tr>`;
        tbody.innerHTML = h;
        root.querySelector('.cmp-count').textContent = rows.length + ' of ' + ROWS.length + ' functions';
        root.querySelector('.cmp-empty').hidden = rows.length !== 0;
        resize();
    }
    thead.addEventListener('click', ev => {
        const th = ev.target.closest('th[data-k]');
        if (!th) return;
        sortKey = th.dataset.k;
        render();
    });
    tbody.addEventListener('click', ev => { if (ev.target.closest('.more')) { limit += 1000; render(); } });
    root.querySelector('.cmp-sortby').addEventListener('click', ev => {
        const b = ev.target.closest('button');
        if (!b) return;
        sortBy = b.dataset.v;
        root.querySelectorAll('.cmp-sortby button').forEach(x => x.classList.toggle('on', x === b));
        render();
    });
    if (D.rows_adj) {
        const l = root.querySelector('.cmp-adj');
        l.style.display = '';
        l.title = 'overhead estimates: ' + D.overhead.map(o => o ? o.child + ' ns/child, ' + o.self + ' ns/call' : 'none').join(' | ');
        l.querySelector('input').addEventListener('change', ev => { ROWS = ev.target.checked ? D.rows_adj : D.rows; render(); });
    }
    root.querySelector('.cmp-changed').addEventListener('change', ev => { onlyChanged = ev.target.checked; render(); });
    let t = 0;
    root.querySelector('.cmp-q').addEventListener('input', ev => {
        clearTimeout(t);
        t = setTimeout(() => {
            try { query = ev.target.value ? new RegExp(ev.target.value, 'i') : null; ev.target.classList.remove('bad'); }
            catch (e) { ev.target.classList.add('bad'); return; }
            render();
        }, 150);
    });
    root.querySelector('.cmp-q').addEventListener('keydown', ev => ev.stopPropagation());
    window.addEventListener('resize', () => { if (window.fgCurrentView() === 'compare') resize(); });
    render();
    return { resize: resize };
} };
