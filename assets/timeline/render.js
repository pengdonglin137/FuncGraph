/* FuncGraph Timeline: canvas rendering */
let W = 0, H = 0, DPR = 1, dark = false, P = null;
const ctx = elCv.getContext('2d'), hctx = elHead.getContext('2d');
const FONT = '11px ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
const FONT_B = '600 11.5px system-ui, -apple-system, "Segoe UI", sans-serif';
const FONT_S = '11px system-ui, -apple-system, "Segoe UI", sans-serif';
let charW = 6.6;
const CTX_HUE = [214, 152, 30, 282];

function palette() {
    dark = document.documentElement.getAttribute('data-theme') === 'dark';
    P = dark ? {
        bg: '#1f2126', hdr: '#272a31', hdrText: '#e6e9ef', sub: '#8d95a3', grid: 'rgba(255,255,255,.05)',
        text: '#f3f5f8', textDim: '#c9ced6', sel: '#ffffff', hov: 'rgba(255,255,255,.7)',
        off: 'rgba(150,158,170,.16)', offLine: 'rgba(170,178,190,.55)', offPre: 'rgba(245,158,11,.20)',
        ovBar: '#4f7fd9', ovWin: 'rgba(255,255,255,.10)', ovMask: 'rgba(0,0,0,.35)', axis: '#a7afbc',
        range: 'rgba(80,140,255,.16)', rangeLine: '#6ea0ff', cursor: 'rgba(255,255,255,.35)', selBand: 'rgba(110,160,255,.07)', selLine: 'rgba(130,175,255,.8)', selTag: '#3b6fd8',
        errO: '#e53935', errOh: '#d9822b', errP: '#7d3434', errPh: '#6b4a26', errLine: '#ff6b6b', errLineH: '#f0a050',
        rs: '#f59e0b', io: 'rgba(225,230,240,.75)', mis: '#c18cf0', dense: 0.75
    } : {
        bg: '#ffffff', hdr: '#f3f5f8', hdrText: '#1d2433', sub: '#7a8494', grid: 'rgba(0,0,0,.045)',
        text: '#16202e', textDim: '#3b4555', sel: '#111827', hov: 'rgba(17,24,39,.55)',
        off: 'rgba(120,128,140,.10)', offLine: 'rgba(110,118,130,.55)', offPre: 'rgba(245,158,11,.16)',
        ovBar: '#3b6fd8', ovWin: 'rgba(59,111,216,.10)', ovMask: 'rgba(255,255,255,.55)', axis: '#5b6573',
        range: 'rgba(59,111,216,.12)', rangeLine: '#3b6fd8', cursor: 'rgba(0,0,0,.30)', selBand: 'rgba(59,111,216,.05)', selLine: 'rgba(59,111,216,.75)', selTag: '#3b6fd8',
        errO: '#d32f2f', errOh: '#e8873a', errP: '#f6b8b8', errPh: '#f8d6b3', errLine: '#c62828', errLineH: '#d9822b',
        rs: '#f59e0b', io: '#4b5563', mis: '#8e44ad', dense: 0.85
    };
    colorCache.clear();
    hatch = makeHatch(dark ? 'rgba(0,0,0,.30)' : 'rgba(0,0,0,.20)');
    offHatch = makeHatch(dark ? 'rgba(200,205,215,.14)' : 'rgba(90,98,110,.14)');
}
function makeHatch(color) {
    const c = document.createElement('canvas');
    c.width = c.height = 6;
    const g = c.getContext('2d');
    g.strokeStyle = color;
    g.lineWidth = 1.2;
    g.beginPath(); g.moveTo(0, 6); g.lineTo(6, 0); g.moveTo(-3, 3); g.lineTo(3, -3); g.moveTo(3, 9); g.lineTo(9, 3); g.stroke();
    return ctx.createPattern(c, 'repeat');
}
let hatch = null, offHatch = null;

// ---- colors ----
const nameHash = new Int32Array(S.length).fill(-1);
function hashOf(si) {
    let h = nameHash[si];
    if (h >= 0) return h;
    const s = S[si];
    h = 2166136261;
    for (let k = 0; k < s.length; k++) { h ^= s.charCodeAt(k); h = Math.imul(h, 16777619); }
    h = (h >>> 1) & 0x3fffffff;
    nameHash[si] = h;
    return h;
}
const colorCache = new Map();
function hsl(h, s, l) { return 'hsl(' + h.toFixed(0) + ',' + s.toFixed(0) + '%,' + l.toFixed(0) + '%)'; }
function baseColor(i) {
    const f = sFlags[i], c = f & FL.CTX;
    let key, h, s, l;
    switch (colorMode) {
        case 'name': key = sName[i]; break;
        case 'heat': key = 'h' + Math.min(19, Math.floor(19.99 * Math.log1p(sSelf[i]) / Math.log1p(maxSelf))); break;
        case 'cpu': key = 'c' + cpuOf(i); break;
        case 'module': key = 'm' + (sMod ? sMod[i] : -1); break;
        default: key = sName[i] * 4 + c;
    }
    let col = colorCache.get(key);
    if (col) return col;
    const hh = colorMode === 'module' ? (sMod && sMod[i] >= 0 ? hashOf(sMod[i]) : 0) : hashOf(sName[i]);
    switch (colorMode) {
        case 'name': h = hh % 360; s = 48 + hh % 20; l = dark ? 38 + (hh >> 9) % 10 : 68 + (hh >> 9) % 12; break;
        case 'heat': {
            const r = Math.min(19, Math.floor(19.99 * Math.log1p(sSelf[i]) / Math.log1p(maxSelf))) / 19;
            h = 220 - 220 * r; s = 30 + 50 * r; l = dark ? 30 + 18 * r : 82 - 22 * r; break;
        }
        case 'cpu': {
            const cp = cpuOf(i);
            h = cp < 0 ? 210 : (cp * 137.508) % 360; s = cp < 0 ? 8 : 52; l = dark ? 40 : 70; break;
        }
        case 'module':
            if (sMod && sMod[i] >= 0) { h = hh % 360; s = 62; l = dark ? 40 : 66; }
            else { h = 214; s = 14; l = dark ? 34 : 80; }
            break;
        default:
            h = CTX_HUE[c] + ((hh % 13) - 6) * 2.4;
            s = (c === 0 ? 46 : 58) + hh % 16;
            l = dark ? 33 + (hh >> 7) % 11 : 67 + (hh >> 7) % 13;
    }
    col = hsl(h, s, l);
    colorCache.set(key, col);
    return col;
}
function sliceStyle(i) {
    // returns [fill, textColor, borderColor|null]
    if (i >= N) return [dark ? 'rgba(120,130,150,.22)' : 'rgba(110,120,140,.13)', P.textDim, dark ? 'rgba(170,180,200,.7)' : 'rgba(90,100,120,.6)'];
    if (ov.err && eRole[i] >= 0 && (showMinor || eChain[i] < 0 || !CHAINS[eChain[i]][6])) {
        const handled = eFate[i] === 0;
        if (eRole[i] === 0) return [handled ? P.errOh : P.errO, '#ffffff', null];
        return [handled ? P.errPh : P.errP, dark ? '#ffe3e3' : '#5a1010', handled ? P.errLineH : P.errLine];
    }
    return [baseColor(i), P.text, null];
}
let matchingCache = null;
function matchingState() {
    if (!searchHits && !textHits) { matchingCache = null; return null; }
    if (!matchingCache || matchingCache.search !== searchHits || matchingCache.text !== textHits) {
        matchingCache = { search: searchHits, text: textHits, rows: new WeakMap(), synthetic: new Int8Array(NA - N).fill(-1) };
    }
    return matchingCache;
}
function isDim(i) {
    if (i >= N) {
        const state = matchingState();
        if (!state) return false;
        const index = i - N;
        if (state.synthetic[index] < 0) state.synthetic[index] = SYN[index].kids.some(child => !isDim(child)) ? 0 : 1;
        return !!state.synthetic[index];
    }
    if (searchHits && !searchHits[sName[i]]) return true;
    if (textHits && !textHits[i]) return true;
    if (range && range.track !== null && range.track !== sTask[i]) return false;
    return false;
}
function denseSummary(row, first, end) {
    const count = end - first, state = matchingState();
    if (!state) return { index: row[first], count, matches: count };
    if (count === 1) return { index: row[first], count, matches: isDim(row[first]) ? 0 : 1 };
    let prefix = state.rows.get(row);
    if (!prefix) {
        prefix = new Uint32Array(row.length + 1);
        for (let position = 0; position < row.length; position++) prefix[position + 1] = prefix[position] + (isDim(row[position]) ? 0 : 1);
        state.rows.set(row, prefix);
    }
    const matches = prefix[end] - prefix[first];
    let low = first, high = end;
    if (matches) {
        while (low < high) {
            const middle = (low + high) >>> 1;
            if (prefix[middle + 1] > prefix[first]) high = middle;
            else low = middle + 1;
        }
    }
    return { index: row[low], count, matches };
}
function lbPixel(row, pixel, start, scale, low = 0) {
    let high = row.length;
    while (low < high) {
        const middle = (low + high) >>> 1;
        if ((sStart[row[middle]] - start) * scale < pixel) low = middle + 1;
        else high = middle;
    }
    return low;
}
function densePixelAt(row, pixel, start, scale) {
    const first = lbPixel(row, pixel, start, scale);
    let end = lbPixel(row, pixel + 1, start, scale, first);
    if (end > first && (sEnd[row[end - 1]] - sStart[row[end - 1]]) * scale >= 1.5) end--;
    return end > first ? denseSummary(row, first, end) : null;
}

// ---- detail panel height (drag handle between canvas and panel) ----
function prefGet(k) { try { return localStorage.getItem('fgTl.' + k); } catch (e) { return null; } }
function prefSet(k, v) { try { if (v === null) localStorage.removeItem('fgTl.' + k); else localStorage.setItem('fgTl.' + k, v); } catch (e) { /* private mode */ } }
function detailDefault() { return Math.round(Math.min(400, Math.max(180, window.innerHeight * 0.32))); }
// stored as a fraction of the window height, so the split looks the same on other screens
function detailPx() { const v = +prefGet('detailFrac'); return v > 0 && v < 1 ? Math.round(v * window.innerHeight) : detailDefault(); }
function clampDetailH(h) {
    const notesMin = elNotes.classList.contains('hide') ? 0 : 28;
    return Math.round(Math.max(60, Math.min(h, tlRoot.clientHeight - 160 - 32 - notesMin - elResizer.offsetHeight)));
}

// ---- main canvas ----
function resize() {
    DPR = window.devicePixelRatio || 1;
    const top = Math.max(0, tlRoot.getBoundingClientRect().top);
    tlRoot.style.height = Math.max(288, window.innerHeight - top - 12) + 'px';
    if (elDetail.classList.contains('show')) {
        elDetail.style.height = clampDetailH(detailPx()) + 'px';
    }
    W = elScroll.clientWidth;
    H = elScroll.clientHeight;
    for (const [cv, w, h] of [[elCv, W, H], [elHead, W, HEAD_H]]) {
        cv.width = Math.max(1, Math.round(w * DPR));
        cv.height = Math.max(1, Math.round(h * DPR));
        cv.style.height = h + 'px';
        // both canvases are drawn for W = width of the track area without its scrollbar; the ruler
        // is wider in CSS (no scrollbar above it) and would be stretched: x positions drift apart
        cv.style.width = w + 'px';
    }
    ctx.font = FONT;
    charW = ctx.measureText('abcdefghijklmnopqrstuvwxyz_0123').width / 31;
    ovDirty = true;
    layout();
    draw();
    scheduleDistHistogram();
}
function layout() {
    let y = 0;
    for (const t of ORDER) {
        t.y = y;
        if (t.hidden) { t.h = 0; continue; }
        t.h = HDR + (t.collapsed ? MINI : (t.maxD + 1) * ROW) + GAP;
        y += t.h;
    }
    totalH = y;
    elSpacer.style.height = Math.max(0, totalH - H) + 'px';
}

let rafPending = false;
function draw() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => { rafPending = false; render(); renderHead(); });
}

function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const m = raw / p;
    return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}

function render() {
    matchingState();
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.fillStyle = P.bg;
    ctx.fillRect(0, 0, W, H);
    const vs = view.s, ve = view.e, k = W / (ve - vs), sy = elScroll.scrollTop;
    // grid
    const step = niceStep((ve - vs) / Math.max(1, W / 110));
    ctx.fillStyle = P.grid;
    for (let t = Math.ceil(vs / step) * step; t < ve; t += step) ctx.fillRect(Math.round((t - vs) * k), 0, 1, H);
    for (const t of ORDER) {
        if (t.hidden) continue;
        const y = t.y - sy;
        if (y + t.h < 0 || y > H) continue;
        drawTrack(t, y, vs, ve, k);
    }
    if (ov.wk && WAKES.length) drawWakeups(vs, ve, k, sy);
    if (range) {
        const x0 = (range.s - vs) * k, x1 = (range.e - vs) * k;
        ctx.fillStyle = P.range;
        if (range.track !== null) {
            const t = TRACKS[range.track];
            ctx.fillRect(x0, t.y - sy, x1 - x0, t.h - GAP);
        } else ctx.fillRect(x0, 0, x1 - x0, H);
        ctx.fillStyle = P.rangeLine;
        ctx.fillRect(Math.round(x0), 0, 1, H);
        ctx.fillRect(Math.round(x1), 0, 1, H);
    }
    if (sel >= 0 && !range) {
        // selected slice: start and end lines over all tracks (as in Perfetto), the ruler shows its span
        const x0 = (sStart[sel] - vs) * k, x1 = (sEnd[sel] - vs) * k;
        if (x1 >= 0 && x0 <= W) {
            ctx.fillStyle = P.selBand;
            ctx.fillRect(Math.max(0, x0), 0, Math.min(W, x1) - Math.max(0, x0), H);
            ctx.fillStyle = P.selLine;
            if (x0 >= 0) ctx.fillRect(Math.round(x0), 0, 1, H);
            if (x1 <= W && x1 - x0 >= 1) ctx.fillRect(Math.round(x1), 0, 1, H);
        }
    }
    if (cursorX >= 0) { ctx.fillStyle = P.cursor; ctx.fillRect(Math.round(cursorX), 0, 1, H); }
}
function drawSelSpan(vs, k) {
    // ruler: bracket from start to end of the selected slice with its duration (and position in the overview)
    if (sel < 0 || range) return;
    const s = sStart[sel], e = sEnd[sel];
    hctx.fillStyle = P.selTag;
    hctx.fillRect(Math.round(ovX(s)), 0, Math.max(1.5, (e - s) * ovK()), OV_H);
    const x0 = (s - vs) * k, x1 = (e - vs) * k;
    if (x1 < 0 || x0 > W) return;
    const y = OV_H + 2, hh = HEAD_H - OV_H - 3;
    const a = Math.max(0, x0), b = Math.min(W, x1);
    hctx.globalAlpha = 0.14;
    hctx.fillRect(a, y, Math.max(1, b - a), hh);
    hctx.globalAlpha = 1;
    if (x0 >= 0) hctx.fillRect(Math.round(x0), y, 1, hh);
    if (x1 <= W) hctx.fillRect(Math.round(x1), y, 1, hh);
    hctx.fillRect(a, HEAD_H - 3, Math.max(1, b - a), 2);
    const isS = sel >= N;
    const lab = fmtDur(isS ? e - s : durOf(sel));
    hctx.font = FONT_S;
    const tw = hctx.measureText(lab).width + 10;
    let lx = (a + b) / 2 - tw / 2;
    if (b - a < tw + 4) lx = x1 + 4 + tw <= W ? x1 + 4 : x0 - 4 - tw;
    lx = Math.max(0, Math.min(W - tw, lx));
    const ly = HEAD_H - 17;
    hctx.fillStyle = P.selTag;
    hctx.beginPath();
    if (hctx.roundRect) hctx.roundRect(lx, ly, tw, 14, 3); else hctx.rect(lx, ly, tw, 14);
    hctx.fill();
    hctx.fillStyle = '#fff';
    hctx.textBaseline = 'middle';
    hctx.fillText(lab, lx + 5, ly + 7.5);
    hctx.textBaseline = 'top';
}

function drawTrack(t, y, vs, ve, k) {
    ctx.fillStyle = P.hdr;
    ctx.fillRect(0, y, W, HDR);
    // track-level windows in the header band
    if (ov.rs) for (const r of t.rs) bandRect(r[1], r[2], y + HDR - 4, 3, P.rs, vs, k);
    // irqs-off periods: dark grey like the hatching (red is reserved for errors)
    if (ov.irq) for (const r of t.io) bandRect(r[1], r[2], y + HDR - 2, 2, P.io, vs, k);
    ctx.font = FONT_B;
    ctx.fillStyle = P.hdrText;
    ctx.textBaseline = 'middle';
    const caret = t.collapsed ? '▸ ' : '▾ ';
    const label = caret + trackLabel(t);
    ctx.fillText(label, 8, y + 9.5);
    const lw = ctx.measureText(label).width;
    ctx.font = FONT_S;
    ctx.fillStyle = P.sub;
    ctx.fillText(trackSub(t), 18 + lw, y + 9.5);
    ctx.font = FONT;
    const top = y + HDR;
    if (t.kind === 9) { if (!t.collapsed) drawCpuTrack(top, vs, ve, k); return; }
    if (t.collapsed) {
        if (t.rows.length) drawRow(t.rows[0], top + 1, MINI - 2, vs, ve, k, false);
        return;
    }
    for (let d = 0; d < t.rows.length; d++) {
        const ry = top + d * ROW;
        if (ry > H || ry + ROW < 0) continue;
        drawRow(t.rows[d], ry, ROW - 1, vs, ve, k, true);
    }
    if (ov.off) for (const r of t.off) drawOff(r, top + offRow(r) * ROW, vs, k);
    if (ov.err) {
        // origins are always marked, even when merged into dense blocks
        for (const c of t.errs) {
            const ch = CHAINS[c];
            if (errFilter && ch[3] !== 1) continue;
            const i = ch[0];
            const x = (sStart[i] - vs) * k;
            if (x < -6 || x > W + 6) continue;
            const ry = top + rowOf(i) * ROW;
            if (ry < -ROW || ry > H) continue;
            if ((sEnd[i] - sStart[i]) * k >= 6) continue;
            ctx.fillStyle = ch[3] === 0 ? P.errOh : P.errO;
            ctx.beginPath(); ctx.moveTo(x, ry - 1); ctx.lineTo(x + 4.5, ry + 7); ctx.lineTo(x - 4.5, ry + 7); ctx.closePath(); ctx.fill();
        }
    }
}
function taskColor(tid) {
    const t = TRACKS[tid];
    const key = 'task' + tid;
    let c = colorCache.get(key);
    if (!c) {
        let h = 2166136261;
        const s = t.comm;
        for (let q = 0; q < s.length; q++) { h ^= s.charCodeAt(q); h = Math.imul(h, 16777619); }
        h = (h >>> 1) % 360;
        c = hsl(h, 50, dark ? 42 : 70);
        colorCache.set(key, c);
    }
    return c;
}
function drawCpuTrack(top, vs, ve, k) {
    for (let d = 0; d < CPU_LIST.length; d++) {
        const cpu = CPU_LIST[d], ry = top + d * ROW;
        if (ry > H || ry + ROW < 0) continue;
        const sg = CPU_SEGS.get(cpu);
        ctx.font = FONT_S;
        const labW = ctx.measureText('CPU ' + cpu).width + 10;
        ctx.font = FONT;
        let lo = 0, hi = sg.e.length;
        while (lo < hi) { const m = (lo + hi) >> 1; if (sg.e[m] > vs) hi = m; else lo = m + 1; }
        for (let j = lo; j < sg.s.length && sg.s[j] < ve; j++) {
            const x0 = (sg.s[j] - vs) * k, x1 = (sg.e[j] - vs) * k;
            const cx0 = Math.max(x0, -2), w = Math.max(1, Math.min(x1, W + 2) - cx0);
            const tid = sg.t[j];
            ctx.globalAlpha = textHits && !TRACKS[tid].hits ? 0.25 : 1;
            ctx.fillStyle = taskColor(tid);
            ctx.fillRect(cx0, ry, w > 3 ? w - 1 : w, ROW - 1);
            const tx = Math.max(cx0, labW) + 4, tw = Math.min(x1, W) - tx - 4;
            if (tw > 30) {
                let txt = trackLabel(TRACKS[tid]);
                const maxc = Math.floor(tw / charW);
                if (txt.length > maxc) txt = txt.slice(0, Math.max(1, maxc - 1)) + '…';
                ctx.fillStyle = P.text;
                ctx.textBaseline = 'middle';
                ctx.fillText(txt, tx, ry + ROW / 2);
            }
            if (hovCpu && hovCpu.cpu === cpu && hovCpu.j === j) { ctx.strokeStyle = P.hov; ctx.lineWidth = 1; ctx.strokeRect(cx0 + 0.5, ry + 0.5, w - 1, ROW - 2); }
        }
        ctx.globalAlpha = 1;
        const irq = CPU_IRQ.get(cpu);
        if (irq) for (const r of irq) {
            if (r[1] < vs || r[0] > ve) continue;
            const x0 = (r[0] - vs) * k, x1 = (r[1] - vs) * k;
            ctx.fillStyle = r[2] === 1 ? 'hsl(152,60%,40%)' : r[2] === 2 ? 'hsl(30,85%,50%)' : 'hsl(282,60%,50%)';
            ctx.fillRect(Math.max(0, x0), ry + ROW - 5, Math.max(1.5, x1 - Math.max(0, x0)), 3);
        }
        // CPU label
        const lab = 'CPU ' + cpu;
        ctx.font = FONT_S;
        const lw = ctx.measureText(lab).width + 10;
        ctx.fillStyle = P.hdr;
        ctx.globalAlpha = 0.9;
        ctx.fillRect(0, ry, lw, ROW - 1);
        ctx.globalAlpha = 1;
        ctx.fillStyle = P.sub;
        ctx.fillText(lab, 5, ry + ROW / 2);
        ctx.font = FONT;
    }
}
function trackAnchorY(tid, slice, sy) {
    const t = TRACKS[tid];
    if (!t || t.hidden || t.h === 0) return null;
    if (slice >= 0 && !t.collapsed) return t.y - sy + HDR + rowOf(slice) * ROW + ROW / 2;
    return t.y - sy + HDR / 2;
}
const WAKE_MAX = 15;   // above this many visible arrows only the focused ones get their full curve
function drawWakeups(vs, ve, k, sy) {
    const vis = [];
    for (const w of WAKES) {
        const off = w[5] >= 0 ? D.offcpu[w[5]] : null;
        const t1 = off ? off[2] : w[1];              // target resumes here
        if (t1 < vs || w[1] > ve) continue;
        const y0 = trackAnchorY(w[0], w[4], sy);
        const tt = TRACKS[w[2]];
        if (y0 === null || !tt || tt.hidden || tt.h === 0) continue;
        const y1 = off && !tt.collapsed ? tt.y - sy + HDR + offRow(off) * ROW + ROW / 2 : tt.y - sy + HDR / 2;
        if ((y0 < 0 && y1 < 0) || (y0 > H && y1 > H)) continue;
        vis.push([w, off, (w[1] - vs) * k, y0, (t1 - vs) * k, y1]);
    }
    if (!vis.length) return;
    // crowded: another arrow ends within 6 px (bursts of wakeups); or simply too many in view
    const xs = vis.map(v => v[4]).sort((a, b) => a - b);
    const near = x => {
        let lo = 0, hi = xs.length;
        while (lo < hi) { const m = (lo + hi) >> 1; if (xs[m] < x - 12) lo = m + 1; else hi = m; }
        let n = 0;
        for (let j = lo; j < xs.length && xs[j] <= x + 12; j++) n++;
        return n - 1;
    };
    const crowded = vis.map(v => vis.length > WAKE_MAX || near(v[4]) >= 1);
    const dense = crowded.some(Boolean);
    const focus = new Set();
    if (sel >= 0) focus.add(sTask[sel]);
    if (hov >= 0) focus.add(sTask[hov]);
    if (hovOff) focus.add(hovOff[0]);
    if (range && range.track !== null) focus.add(range.track);
    const col = dark ? 'rgba(52,211,190,.75)' : 'rgba(15,157,138,.7)';
    let drawn = 0;
    for (let q = 0; q < vis.length; q++) {
        const [w, off, x0, y0, x1, y1] = vis[q];
        const hot = hovOff && off === hovOff;
        const full = !crowded[q] || hot || focus.has(w[0]) || focus.has(w[2]);
        ctx.fillStyle = ctx.strokeStyle = hot ? '#0b7a6b' : col;
        const dir = y1 >= y0 ? 1 : -1;
        if (!full) {
            // dense: only a small head where the woken task resumes
            ctx.globalAlpha = 0.55;
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x1 - 3, y1 - 4 * dir); ctx.lineTo(x1 + 3, y1 - 4 * dir); ctx.closePath(); ctx.fill();
            ctx.globalAlpha = 1;
            continue;
        }
        if (++drawn > 400) break;
        ctx.lineWidth = hot ? 2 : 1.3;
        ctx.beginPath(); ctx.arc(x0, y0, 2.5, 0, 6.3); ctx.fill();
        ctx.beginPath(); ctx.moveTo(x0, y0);
        const mx = (x0 + x1) / 2;
        ctx.bezierCurveTo(mx, y0, mx, y1, x1, y1);
        ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x1 - 5, y1 - 4 * dir); ctx.lineTo(x1 + 1, y1 - 6 * dir); ctx.closePath(); ctx.fill();
    }
    if (dense) {
        const nc = crowded.filter(Boolean).length;
        const msg = nc + ' of ' + vis.length + ' wakeups bunched' + (focus.size ? ' · arrows for the focused task' : ' · hover or select a task to see its arrows, zoom in for all');
        ctx.font = FONT_S;
        const tw = ctx.measureText(msg).width;
        ctx.fillStyle = dark ? 'rgba(31,33,38,.85)' : 'rgba(255,255,255,.85)';
        ctx.fillRect(W - tw - 16, 4, tw + 12, 16);
        ctx.fillStyle = col;
        ctx.textBaseline = 'middle';
        ctx.fillText(msg, W - tw - 10, 12);
        ctx.font = FONT;
    }
}
function bandRect(s, e, y, h, color, vs, k) {
    const x0 = (s - vs) * k, x1 = (e - vs) * k;
    if (x1 < 0 || x0 > W) return;
    ctx.fillStyle = color;
    ctx.fillRect(Math.max(0, x0), y, Math.max(1.5, Math.min(W, x1) - Math.max(0, x0)), h);
}

function drawRow(row, ry, h, vs, ve, k, labels) {
    const n = row.length;
    const filtered = !!(searchHits || textHits);
    let j = Math.min(lbEnd(row, vs), lbStart(row, vs, 0));
    let dx0 = -1, dx1 = -1, dcol = null, ddim = true;
    const flush = () => {
        if (dx0 < 0) return;
        ctx.globalAlpha = ddim ? 0.2 : P.dense;
        ctx.fillStyle = dcol;
        ctx.fillRect(dx0, ry, Math.max(1, dx1 - dx0), h);
        ctx.globalAlpha = 1;
        dx0 = -1;
    };
    while (j < n) {
        const i = row[j];
        const s = sStart[i];
        if (s > ve) break;
        const x0 = (s - vs) * k, x1 = (sEnd[i] - vs) * k;
        if (x1 - x0 < 1.5) {
            const px = Math.floor(x0);
            // skip the other thin slices that start in this pixel. Slices of a row do not overlap,
            // so only the last one starting here can be wide: never skip that one.
            let jj = lbPixel(row, px + 1, vs, k, j + 1);
            if (jj - 1 > j && (sEnd[row[jj - 1]] - sStart[row[jj - 1]]) * k >= 1.5) jj = jj - 1;
            const summary = filtered ? denseSummary(row, j, jj) : null, dim = summary ? !summary.matches : false;
            if (dx0 >= 0 && px <= dx1 + 1 && ddim === dim) { if (px + 1 > dx1) dx1 = px + 1; }
            else { flush(); dx0 = px; dx1 = px + 1; dcol = sliceStyle(summary ? summary.index : i)[0]; ddim = dim; }
            j = jj > j ? jj : j + 1;
            continue;
        }
        flush();
        drawSlice(i, x0, x1, ry, h, labels);
        j++;
    }
    flush();
}

function drawSlice(i, x0, x1, y, h, labels) {
    const cx0 = Math.max(x0, -2), cx1 = Math.min(x1, W + 2);
    const w = cx1 - cx0;
    const ww = w > 3 ? w - 1 : w;
    const st = sliceStyle(i);
    const dim = isDim(i);
    const f = sFlags[i];
    if (dim) ctx.globalAlpha = 0.18;
    ctx.fillStyle = st[0];
    ctx.fillRect(cx0, y, ww, h);
    if (ov.irq && (f & FL.IRQOFF) && w > 2) { ctx.fillStyle = hatch; ctx.fillRect(cx0, y, ww, h); }
    if (ov.preempt && w > 2) {
        const p = preemptOff(f);   // above the tracer's own level
        if (p) { ctx.fillStyle = dark ? 'rgba(255,255,255,' + (0.1 + 0.08 * p) + ')' : 'rgba(0,0,0,' + (0.08 + 0.07 * p) + ')'; ctx.fillRect(cx0, y + h - 3, ww, 3); }
    }
    if (i >= N) {
        // inline frame: dashed outline; the dashed ends say "extent known only from the traced calls inside"
        ctx.strokeStyle = st[2]; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
        ctx.strokeRect(cx0 + 0.5, y + 0.5, ww - 1, h - 1);
        ctx.setLineDash([]);
    } else if (st[2]) { ctx.strokeStyle = st[2]; ctx.lineWidth = 1.5; ctx.strokeRect(cx0 + 0.75, y + 0.75, ww - 1.5, h - 1.5); }
    if (ov.mk && w > 5) {
        if (f & FL.TRUNC) { ctx.fillStyle = P.sub; for (let yy = y; yy < y + h; yy += 4) ctx.fillRect(cx0 + ww - 2, yy, 2, 2); }
        if (f & FL.ORPHAN) { ctx.fillStyle = P.sub; for (let yy = y; yy < y + h; yy += 4) ctx.fillRect(cx0, yy, 2, 2); }
        if (f & FL.MISMATCH) {
            if (tailJumpOf(i)) {
                if (w > 8) {
                    ctx.strokeStyle = P.sub; ctx.lineWidth = 1; ctx.beginPath();
                    ctx.moveTo(cx0 + 1, y + 3.5); ctx.lineTo(cx0 + 6, y + 3.5);
                    ctx.moveTo(cx0 + 4, y + 1.5); ctx.lineTo(cx0 + 6, y + 3.5); ctx.lineTo(cx0 + 4, y + 5.5); ctx.stroke();
                }
            } else { ctx.fillStyle = P.mis; ctx.fillRect(cx0, y, 3, 3); }
        }
        if (ATOMIC.has(i)) { ctx.strokeStyle = P.errLine; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx0, y + h - 1); ctx.lineTo(cx0 + ww, y + h - 1); ctx.stroke(); }
    }
    if (ov.rs && w > 6 && ((f >> FL.RS_SHIFT) & 7)) {
        ctx.fillStyle = P.rs;
        ctx.beginPath(); ctx.moveTo(cx0 + ww - 6, y); ctx.lineTo(cx0 + ww, y); ctx.lineTo(cx0 + ww, y + 6); ctx.closePath(); ctx.fill();
    }
    if (labels && w > 26) {
        const nm = nameOf(i);
        let txt = i >= N ? (isGhost(i) ? (nm === '?' ? 'untraced frame' : 'untraced ' + nm) : 'inline ' + nm) : nm;
        if (ov.err && eRole[i] === 0 && (showMinor || !CHAINS[eChain[i]][6])) { const en = errName(eErr[i]); txt += ' = ' + (en || eErr[i]); }
        const maxc = Math.floor((w - 8) / charW);
        if (maxc >= 3) {
            if (txt.length > maxc) txt = txt.slice(0, maxc - 1) + '…';
            ctx.fillStyle = st[1];
            ctx.textBaseline = 'middle';
            ctx.fillText(txt, Math.max(cx0, 0) + 4, y + h / 2 + 0.5);
        }
    }
    ctx.globalAlpha = 1;
    if (i === sel) { ctx.strokeStyle = P.sel; ctx.lineWidth = 2; ctx.strokeRect(cx0 + 1, y + 1, Math.max(1, ww - 2), h - 2); }
    else if (i === hov) { ctx.strokeStyle = P.hov; ctx.lineWidth = 1; ctx.strokeRect(cx0 + 0.5, y + 0.5, Math.max(1, ww - 1), h - 1); }
}

function drawOff(r, y, vs, k) {
    const x0 = (r[1] - vs) * k, x1 = (r[2] - vs) * k;
    if (x1 < 0 || x0 > W || y > H || y + ROW < 0) return;
    const cx0 = Math.max(x0, -2), cx1 = Math.min(x1, W + 2), w = Math.max(1, cx1 - cx0);
    ctx.fillStyle = r[8] ? P.offPre : P.off;
    ctx.fillRect(cx0, y, w, ROW - 1);
    ctx.fillStyle = offHatch;
    ctx.fillRect(cx0, y, w, ROW - 1);
    ctx.fillStyle = P.offLine;
    ctx.fillRect(cx0, y + ROW - 2, w, 1);
    if (w > 40) {
        let txt = (r[8] ? 'preempted ' : 'off-CPU ') + fmtDur(r[2] - r[1]);
        if (r[4] >= 0) txt += ' · ' + S[r[4]];
        if (r[5] >= 0) txt += ' [' + S[r[5]] + ']';
        const maxc = Math.floor((w - 8) / charW);
        if (txt.length > maxc) txt = txt.slice(0, Math.max(1, maxc - 1)) + '…';
        ctx.fillStyle = P.sub;
        ctx.textBaseline = 'middle';
        ctx.fillText(txt, Math.max(cx0, 0) + 4, y + ROW / 2);
    }
    if (hovOff === r) { ctx.strokeStyle = P.hov; ctx.lineWidth = 1; ctx.strokeRect(cx0 + 0.5, y + 0.5, w - 1, ROW - 2); }
}

// ---- header: overview + axis ----
let ovDirty = true, ovDens = null, ovMax = 1;
function buildOverview() {
    ovDens = new Float32Array(Math.max(1, Math.ceil(W)));
    for (const t of ORDER) {
        if (t.kind !== 0 && ORDER.some(x => x.kind === 0)) continue;
        if (!t.rowsT.length) continue;
        for (const i of t.rowsT[0]) {
            const a = Math.max(0, ovX(sStart[i])), b = ovX(sEnd[i]);
            const pa = Math.floor(a), pb = Math.min(ovDens.length - 1, Math.floor(b));
            for (let p = pa; p <= pb; p++) ovDens[p] += Math.min(b, p + 1) - Math.max(a, p);
        }
    }
    ovMax = 0;
    for (let p = 0; p < ovDens.length; p++) if (ovDens[p] > ovMax) ovMax = ovDens[p];
    ovMax = ovMax || 1;
    ovDirty = false;
}
function renderHead() {
    if (ovDirty) buildOverview();
    hctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    hctx.fillStyle = P.hdr;
    hctx.fillRect(0, 0, W, HEAD_H);
    hctx.fillStyle = P.ovBar;
    hctx.globalAlpha = 0.55;
    for (let p = 0; p < ovDens.length; p++) {
        if (!ovDens[p]) continue;
        const hh = Math.max(1, (OV_H - 6) * ovDens[p] / ovMax);
        hctx.fillRect(p, 3 + OV_H - 6 - hh, 1, hh);
    }
    hctx.globalAlpha = 1;
    if (ov.err) for (const ch of CHAINS) {
        if ((errFilter && ch[3] !== 1) || (ch[6] && !showMinor)) continue;
        hctx.fillStyle = ch[3] === 0 ? P.errOh : P.errO;
        hctx.fillRect(Math.round(ovX(sStart[ch[0]])), 1, 1.5, ch[3] === 1 ? OV_H - 4 : 8);
    }
    const wx0 = Math.max(0, ovX(view.s)), wx1 = Math.max(wx0 + 2, ovX(view.e));
    hctx.fillStyle = P.ovMask;
    hctx.fillRect(0, 0, wx0, OV_H);
    hctx.fillRect(wx1, 0, W - wx1, OV_H);
    hctx.strokeStyle = P.rangeLine;
    hctx.lineWidth = 1;
    hctx.strokeRect(wx0 + 0.5, 0.5, wx1 - wx0 - 1, OV_H - 1);
    // axis
    const vs = view.s, ve = view.e, k = W / (ve - vs);
    const step = niceStep((ve - vs) / Math.max(1, W / 110));
    hctx.font = FONT_S;
    hctx.textBaseline = 'top';
    hctx.fillStyle = P.axis;
    for (let t = Math.ceil(vs / step) * step; t < ve; t += step) {
        const x = Math.round((t - vs) * k);
        hctx.fillRect(x, OV_H + 2, 1, 6);
        hctx.fillText(fmtDur(t), x + 3, OV_H + 6);
    }
    hctx.fillStyle = P.sub;
    const span = 'span ' + fmtDur(ve - vs) + (MODE === 'abs' ? '   start ' + fmtAbs(vs) + ' s' : '') +
        (MODE === 'compressed' ? '   compressed time (no timestamps)' : MODE === 'count' ? '   call order (no timing)' : '');
    hctx.textAlign = 'right';
    hctx.fillText((range ? 'selected ' + fmtDur(range.e - range.s) + '   ' : '') + span, W - 8, OV_H + 18);
    hctx.textAlign = 'left';
    drawSelSpan(vs, k);
    if (cursorX >= 0) {
        const tc = vs + cursorX / k;
        hctx.fillStyle = P.hdrText;
        hctx.fillText(fmtDur(tc), Math.min(W - 70, cursorX + 4), OV_H + 18);
    }
}
