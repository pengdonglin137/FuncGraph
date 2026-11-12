/* FuncGraph Timeline: interaction */
function hitTest(mx, my) {
    // returns {track, slice, off, header}
    const yy = my + elScroll.scrollTop;
    const t = visibleTracks[firstTrackEndingAfter(yy)];
    if (!t || yy < t.y || yy >= t.y + t.h - GAP) return { track: null, slice: -1, off: null, header: false };
    if (yy < t.y + HDR) return { track: t, slice: -1, off: null, header: true };
    const k = W / (view.e - view.s), tm = view.s + mx / k;
    const d = t.collapsed ? 0 : Math.floor((yy - t.y - HDR) / ROW);
    if (t.kind === 9) {
        if (t.collapsed || d < 0 || d >= CPU_LIST.length) return { track: t, slice: -1, off: null, header: false };
        const cpu = CPU_LIST[d], sg = CPU_SEGS.get(cpu);
        let lo = 0, hi = sg.e.length;
        while (lo < hi) { const m = (lo + hi) >> 1; if (sg.e[m] > tm) hi = m; else lo = m + 1; }
        const seg = lo < sg.s.length && sg.s[lo] <= tm ? { cpu: cpu, j: lo, t: sg.t[lo], s: sg.s[lo], e: sg.e[lo] } : null;
        return { track: t, slice: -1, off: null, header: false, cpuSeg: seg, tm: tm };
    }
    let best = -1, dense = null;
    if (d >= 0 && d < t.rows.length) {
        const row = t.rows[d];
        const j = lbEnd(row, tm);
        let bestDx = 4;   // px tolerance for very thin slices
        for (const jj of [j - 1, j, j + 1]) {
            if (jj < 0 || jj >= row.length) continue;
            const i = row[jj];
            const x0 = (sStart[i] - view.s) * k, x1 = (sEnd[i] - view.s) * k;
            const dx = mx < x0 ? x0 - mx : mx > x1 ? mx - x1 : 0;
            if (dx < bestDx || (dx === 0 && bestDx === 0 && sEnd[i] - sStart[i] < sEnd[best] - sStart[best])) { bestDx = dx; best = i; }
        }
        const exactWide = best >= 0 && (sEnd[best] - sStart[best]) * k >= 1.5 &&
            mx >= (sStart[best] - view.s) * k && mx < (sEnd[best] - view.s) * k;
        if (!exactWide) {
            const summary = densePixelAt(row, Math.floor(mx), view.s, k);
            if (summary) { best = summary.index; if (summary.count > 1) dense = summary; }
        }
    }
    let off = null;
    if (best < 0 && ov.off && !t.collapsed && t.off.length) {
        off = visitSchedulingIntervals(t.off, t.offIndex, tm, tm, record => offRow(record) === d);
    }
    return { track: t, slice: best, off: off, header: false, dense };
}

function zoomFrom(span, anchor, mx) {
    const minSpan = MODE === 'count' ? 2000 : 50;
    const nextSpan = Math.max(minSpan, Math.min(tMax * 1.05, span));
    view.s = anchor - (mx / W) * nextSpan;
    view.e = view.s + nextSpan;
    clampView();
    draw();
}
function zoomAt(factor, mx) {
    const span = view.e - view.s;
    zoomFrom(span * factor, view.s + (mx / W) * span, mx);
}
function panBy(dt) { view.s += dt; view.e += dt; clampView(); draw(); }
function clampView() {
    const span = view.e - view.s, pad = tMax * 0.02;
    if (view.s < -pad) { view.s = -pad; view.e = view.s + span; }
    if (view.e > tMax + pad) { view.e = tMax + pad; view.s = view.e - span; }
}
function fitAll() { view.s = tCall; view.e = tMax; draw(); }
function zoomTo(s, e, revealSlice) {
    const span = Math.max(e - s, MODE === 'count' ? 2000 : 50);
    const pad = span * 0.08;
    view.s = s - pad; view.e = s + span + pad;
    clampView();
    if (revealSlice >= 0) revealRow(revealSlice);
    draw();
}
function revealRow(i, expand = true) {
    const t = TRACKS[sTask[i]];
    if (t.collapsed && expand) { t.collapsed = false; layout(); }
    const y = t.y + HDR + (t.collapsed ? 0 : rowOf(i)) * ROW;
    if (y < elScroll.scrollTop + 4 || y + ROW > elScroll.scrollTop + H - 4) elScroll.scrollTop = Math.max(0, y - H / 3);
}
function setSelection(index) {
    sel = index;
    updateSearchLabel();
}
function select(i, opts) {
    opts = opts || {};
    setSelection(i);
    if (i >= 0) {
        if (opts.zoom) zoomTo(sStart[i], sEnd[i], i);
        else if (opts.reveal) {
            const span = view.e - view.s;
            if (sEnd[i] < view.s || sStart[i] > view.e || sEnd[i] - sStart[i] > span) {
                if (sEnd[i] - sStart[i] > span) zoomTo(sStart[i], sEnd[i], i);
                else { const c = (sStart[i] + sEnd[i]) / 2; view.s = c - span / 2; view.e = c + span / 2; clampView(); }
            }
            revealRow(i);
        }
        showDetail(i);
        if (compactToolbar.matches) revealRow(i, false);
    } else hideDetail();
    draw();
}

// ---- tooltip ----
let syntheticGeometryCache = null;
function syntheticGeometry(i) {
    if (syntheticGeometryCache && syntheticGeometryCache.index === i) return syntheticGeometryCache;
    const y = SYN[i - N], kids = y.kids;
    let covered = 0;
    for (const child of kids) covered += sEnd[child] - sStart[child];
    const span = sEnd[i] - sStart[i];
    syntheticGeometryCache = { index: i, y, kids, covered, span, gap: Math.max(0, span - covered) };
    return syntheticGeometryCache;
}
function inlineFacts(i) {
    // facts about synthetic inline frame i
    const { y, kids, covered, span, gap } = syntheticGeometry(i), ch = y.chain, k = y.k;
    const outer = ch[k - 1];
    return { y, ch, k, kids, covered, span, gap, fn: ch[k][0], file: ch[k][1],
        intoFn: outer[0], atFile: outer[1], atLine: outer[2], mismatch: y.p >= 0 && nameOf(y.p).split('.')[0] !== ch[0][0].split('.')[0] };
}
function ghostFacts(i) {
    return syntheticGeometry(i);
}
function tipDense(summary) {
    return '<div class="t-sep"></div><div class="t-warn">Dense pixel: ' + summary.count + ' frames in this task/depth row; ' + summary.matches +
        ' match the current search/filter. Showing the ' + (summary.matches ? 'first matching' : 'first') + ' representative frame, not every call. Zoom in to separate them.</div>';
}
function tipGhost(i) {
    const f = ghostFacts(i);
    const row = (k, v) => '<div class="t-row"><span class="t-k">' + k + '</span><span class="t-v">' + v + '</span></div>';
    let h = '<div class="t-name">' + esc(f.y.name === '?' ? 'unknown frame' : f.y.name) + ' <span class="t-warn">(not traced)</span></div>';
    h += '<div>extent of the calls inside <span class="t-dur">' + fmtDur(f.span) + '</span></div><div class="t-sep"></div>';
    h += row('inside', esc(nameOf(f.y.p)));
    h += row('traced calls', f.kids.length + ' (' + fmtDur(f.covered) + ')');
    h += '<div class="t-warn">The trace skips ' + f.y.gap + ' call level' + (f.y.gap > 1 ? 's' : '') + ' here: the entry was not recorded. ' +
        (f.y.name === '?' ? 'Name unknown.' : 'Name from the return address of the calls inside.') + '</div>';
    return h;
}
function tipInline(i) {
    const f = inlineFacts(i);
    const row = (k, v) => '<div class="t-row"><span class="t-k">' + k + '</span><span class="t-v">' + v + '</span></div>';
    let h = '<div class="t-name">' + esc(f.fn) + ' <span class="t-warn">(inlined)</span></div>';
    h += '<div>traced extent <span class="t-dur">' + fmtDur(f.span) + '</span></div><div class="t-sep"></div>';
    h += row('inlined into', esc(f.intoFn) + ' at ' + esc(f.atFile) + ':' + f.atLine);
    h += row('defined in', esc(f.file));
    h += row('traced calls', f.kids.length + ' (' + fmtDur(f.covered) + ')');
    h += row('between them', fmtDur(f.gap) + ' (own inlined code or untraced work)');
    h += '<div class="t-warn">Start/end = first/last traced call inside: the inlined code itself may begin earlier and end later.</div>';
    if (f.mismatch) h += '<div class="t-warn">Containing function ' + esc(f.ch[0][0]) + ' differs from the parent slice ' + esc(nameOf(f.y.p)) + ' (untraced frame in between).</div>';
    return h;
}
function inlineLine(i) {
    // "a > b > c" path of inline frames around the call site of real slice i
    const ch = inlChain(i);
    return ch && ch.length > 1 ? ch.slice(1).map(f => f[0]).join(' › ') : '';
}
function tipSlice(i) {
    if (i >= N) return isGhost(i) ? tipGhost(i) : tipInline(i);
    const d = durOf(i), f = sFlags[i];
    let h = '<div class="t-name">' + esc(nameOf(i)) + (modOf(i) ? ' [' + esc(modOf(i)) + ']' : '') + '</div>';
    h += '<div><span class="t-dur">' + fmtDur(d) + '</span>' + (f & FL.EST ? ' <span class="t-warn">(estimated)</span>' : '') +
        ' &nbsp;self ' + fmtDur(sSelf[i]) + '</div><div class="t-sep"></div>';
    const row = (k, v, cls) => '<div class="t-row"><span class="t-k">' + k + '</span><span class="t-v' + (cls ? ' ' + cls : '') + '">' + v + '</span></div>';
    h += row('start', fmtDur(sStart[i]) + (MODE === 'abs' ? ' &nbsp;(' + fmtAbs(sStart[i]) + ')' : ''));
    const t = TRACKS[sTask[i]];
    h += row('task', esc(trackLabel(t)) + (cpuOf(i) >= 0 ? ' &nbsp;CPU ' + cpuOf(i) : ''));
    if (D.uftrace) h += row('space', f & FL.BOUNDARY ? 'Kernel · entered from user' : f & FL.USER ? 'User' : 'Kernel');
    if (!D.uftrace || f & FL.LAT) h += row('context', CTX_NAMES[f & FL.CTX] + (f & FL.CTXINF ? ' (inferred)' : '') + ctxFlagText(f));
    const r = retOf(i);
    if (r !== null) {
        const field = kernelTypesOf(i)?.ret ? 'return (BTF)' : D.uftrace ? 'raw return' : 'return';
        if (eRole[i] >= 0) h += row(field, esc(r) + ' ' + (errName(eErr[i]) || '') + ' — ' + errRoleText(i), 't-err');
        else h += row(field, esc(r) + (f & FL.NULLRET ? ' (NULL)' : ''));
    }
    const c = callerOf(i);
    if (c) h += row('caller', esc(c) + (f & FL.MISMATCH ? '<div class="tl-tail-note">' + esc(callerRelationText(i)) + '</div>' : ''));
    const csp = c ? callSitePos(i) : null;
    if (csp) h += row('source', esc(csp.at));
    const userSite = f & FL.USER ? userCallSite(i) : null;
    const userSource = f & FL.USER ? userSite || userPosition(i) : null;
    if (userSource) h += row(userSite ? 'call site' : 'function entry', esc(userSource[0] + ':' + userSource[1]));
    const il = inlineLine(i);
    if (il) h += row('inlined in', esc(il));
    const a = argsOf(i);
    const namedArgs = namedArgsOf(i) || a;
    if (namedArgs) h += row('args', esc(namedArgs.length > 180 ? namedArgs.slice(0, 180) + '…' : namedArgs));
    if (f & FL.TRUNC) h += '<div class="t-warn">exit not captured (trace window ended or events lost)</div>';
    if (f & FL.ORPHAN) h += '<div class="t-warn">entry not captured (started before trace window)</div>';
    return h;
}
// preempt depth above the baseline of the trace (task context; in IRQ context preemption is off anyway)
const PBASE = D.preempt_base || 0;
function preemptOff(f) { return (f & FL.LAT) ? Math.max(0, ((f >> FL.PR_SHIFT) & 15) - PBASE) : 0; }
function ctxFlagText(f) {
    if (!(f & FL.LAT)) return '';
    const parts = [];
    if (f & FL.IRQOFF) parts.push('irqs off');
    if (f & FL.BHOFF) parts.push('BH off');
    const rs = (f >> FL.RS_SHIFT) & 7;
    if (rs) parts.push(RESCHED_NAMES[rs]);
    const p = (f >> FL.PR_SHIFT) & 15;
    if (p) parts.push('preempt ' + p + (p > PBASE ? ' (preempt off)' : ''));
    const m = (f >> FL.MG_SHIFT) & 15;
    if (m) parts.push('migrate-off ' + m);
    return parts.length ? ' · ' + parts.join(', ') : '';
}
function errRoleText(i) {
    const fate = ['handled by caller', 'returned to user space', 'propagates beyond trace'][eFate[i]] || '';
    return (eRole[i] === 0 ? 'error origin' : 'propagated') + ', ' + fate + (eVer[i] ? '' : ' (unverified: no BTF type)');
}
function tipOff(r) {
    let h = '<div class="t-name">' + (r[8] ? 'Preempted' : 'Off-CPU (blocked)') + '</div>';
    h += '<div><span class="t-dur">' + fmtDur(r[2] - r[1]) + '</span></div><div class="t-sep"></div>';
    const row = (k, v) => '<div class="t-row"><span class="t-k">' + k + '</span><span class="t-v">' + v + '</span></div>';
    if (r[4] >= 0) h += row('waiting in', esc(S[r[4]]));
    if (r[5] >= 0) h += row('state', esc(S[r[5]]));
    h += row('switch', 'CPU ' + (r[6] >= 0 ? r[6] : '?') + ' → ' + (r[7] >= 0 ? 'CPU ' + r[7] : 'not back in window'));
    if (r[9] >= 0) h += row('woken by', esc(trackLabel(TRACKS[r[9]])));
    return h;
}
function tipCpu(c) {
    const t = TRACKS[c.t];
    let h = '<div class="t-name">' + esc(trackLabel(t)) + '</div><div><span class="t-dur">' + fmtDur(c.e - c.s) + '</span> on CPU ' + c.cpu + '</div><div class="t-sep"></div>';
    h += '<div class="t-row"><span class="t-k">start</span><span class="t-v">' + fmtDur(c.s) + (MODE === 'abs' ? ' (' + fmtAbs(c.s) + ')' : '') + '</span></div>';
    const irq = CPU_IRQ.get(c.cpu);
    if (irq) {
        const sum = [0, 0, 0, 0];
        for (const r of irq) if (r[1] > c.s && r[0] < c.e) sum[r[2]] += Math.min(r[1], c.e) - Math.max(r[0], c.s);
        for (let q = 1; q < 4; q++) if (sum[q]) h += '<div class="t-row"><span class="t-k">' + CTX_NAMES[q] + '</span><span class="t-v">' + fmtDur(sum[q]) + '</span></div>';
    }
    return h + '<div class="t-warn">click to open this task</div>';
}
function revealTaskTrack(task) {
    if (!ORDER.includes(task)) ORDER.push(task);
    task.hidden = false;
    task.collapsed = false;
    layout();
    elScroll.scrollTop = Math.max(0, task.y - 4);
}
function openTaskAt(tid, tm) {
    const task = TRACKS[tid];
    if (!task) return;
    revealTaskTrack(task);
    for (let depth = task.rowsT.length - 1; depth >= 0; depth--) {
        const row = task.rowsT[depth], position = lbEnd(row, tm);
        if (position < row.length) {
            const index = row[position];
            if (index < N && sStart[index] <= tm && sEnd[index] > tm) { select(index); return; }
        }
    }
    setSelection(-1);
    range = null;
    showTasks(tid);
    revealTaskTrack(task);
    draw();
}
let hoverSourceRequest = null;
let hoverSourcePointer = null;
function loadCallSitePosition(index, source) {
    if (index !== hov || !hoverSourcePointer) return;
    if (hoverSourceRequest?.index === index && hoverSourceRequest.source === source) return;
    hoverSourceRequest?.controller.abort();
    const request = { index, source, controller: new AbortController() };
    hoverSourceRequest = request;
    fgData.loadSource(source, { signal: request.controller.signal }).then(() => {
        if (hoverSourceRequest !== request || hov !== index || !hoverSourcePointer || fgCurrentView() !== 'timeline') return;
        const { event, dense } = hoverSourcePointer;
        showTip(tipSlice(index) + (dense ? tipDense(dense) : ''), event);
    }).catch(error => {
        if (hoverSourceRequest !== request || error.name === 'AbortError' || hov !== index) return;
        elTip.insertAdjacentHTML('beforeend', '<div class="t-warn">Source not loaded: ' + esc(error.message) + '. Select the call to retry.</div>');
    });
}
function showTip(html, ev) {
    elTip.innerHTML = html;
    elTip.style.display = 'block';
    const r = elTip.getBoundingClientRect();
    let x = ev.clientX + 14, y = ev.clientY + 16;
    if (x + r.width > window.innerWidth - 6) x = ev.clientX - r.width - 14;
    if (y + r.height > window.innerHeight - 6) y = ev.clientY - r.height - 12;
    elTip.style.left = Math.max(4, x) + 'px';
    elTip.style.top = Math.max(4, y) + 'px';
}
function hideTip() {
    elTip.style.display = 'none';
    hoverSourceRequest?.controller.abort();
    hoverSourceRequest = null;
    hoverSourcePointer = null;
}

// ---- mouse ----
let drag = null;   // {mode:'pan'|'range'|'ov', x0, s0, e0, moved, track}
function localXY(ev, el) { const b = el.getBoundingClientRect(); return [ev.clientX - b.left, ev.clientY - b.top]; }
function activateCanvas(mx, my) {
    const h = hitTest(mx, my);
    if (h.header && h.track) { h.track.collapsed = !h.track.collapsed; layout(); draw(); return; }
    if (h.cpuSeg) { openTaskAt(h.cpuSeg.t, h.tm); return; }
    if (h.slice >= 0) select(h.slice);
    else if (h.off) { setSelection(-1); showWaitChain(h.off); draw(); }
    else if (range) { range = null; hideDetail(); draw(); }
    else select(-1);
}
elCv.addEventListener('mousedown', ev => {
    if (ev.button !== 0) return;
    const [mx, my] = localXY(ev, elCv);
    const h = hitTest(mx, my);
    drag = { mode: ev.shiftKey ? 'range' : 'pan', x0: mx, s0: view.s, e0: view.e, moved: false,
        track: ev.shiftKey && !ev.altKey && h.track ? h.track.id : null };
    ev.preventDefault();
});
window.addEventListener('mousemove', ev => {
    if (window.fgCurrentView() !== 'timeline') return;
    if (drag) {
        const [mx] = localXY(ev, drag.mode === 'ov' ? elHead : elCv);
        if (Math.abs(mx - drag.x0) > 3) drag.moved = true;
        if (drag.mode === 'pan' && drag.moved) {
            elCv.classList.add('grab');
            const dt = (mx - drag.x0) / W * (drag.e0 - drag.s0);
            view.s = drag.s0 - dt; view.e = drag.e0 - dt; clampView();
        } else if (drag.mode === 'range' && drag.moved) {
            const k = W / (view.e - view.s);
            const a = view.s + drag.x0 / k, b = view.s + mx / k;
            range = { s: Math.max(0, Math.min(a, b)), e: Math.min(tMax, Math.max(a, b)), track: drag.track };
        } else if (drag.mode === 'ov') {
            centerOn(ovT(mx));
        }
        hideTip();
        draw();
        return;
    }
    if (ev.target !== elCv) return;
    const [mx, my] = localXY(ev, elCv);
    cursorX = mx;
    const h = hitTest(mx, my);
    const nh = h.slice;
    hoverSourcePointer = { event: ev, dense: h.dense };
    if (hoverSourceRequest && hoverSourceRequest.index !== nh) {
        hoverSourceRequest.controller.abort(); hoverSourceRequest = null;
    }
    const nextCpu = h.cpuSeg || null;
    const changed = nh !== hov || h.off !== hovOff || nextCpu?.cpu !== hovCpu?.cpu || nextCpu?.j !== hovCpu?.j;
    hov = nh; hovOff = h.off;
    hovCpu = nextCpu;
    elCv.classList.toggle('ptr', nh >= 0 || h.header || !!hovCpu);
    if (hovCpu) showTip(tipCpu(hovCpu), ev);
    else if (nh >= 0) showTip(tipSlice(nh) + (h.dense ? tipDense(h.dense) : ''), ev);
    else if (h.off) showTip(tipOff(h.off), ev);
    else hideTip();
    if (changed) draw();
    else drawCursor();
});
window.addEventListener('mouseup', ev => {
    if (!drag) return;
    const d = drag;
    drag = null;
    elCv.classList.remove('grab');
    if (d.mode === 'range') {
        if (d.moved && range) showRangeSummary();
        else { range = null; draw(); }
        return;
    }
    if (d.mode !== 'pan' || d.moved || ev.target !== elCv) return;
    activateCanvas(...localXY(ev, elCv));
});
elCv.addEventListener('mouseleave', () => {
    const changed = hov >= 0 || hovOff !== null || hovCpu !== null;
    cursorX = -1; hov = -1; hovOff = null; hovCpu = null; hideTip();
    if (changed) draw();
    else drawCursor();
});
elCv.addEventListener('dblclick', ev => {
    const [mx, my] = localXY(ev, elCv);
    const h = hitTest(mx, my);
    if (h.slice >= 0) select(h.slice, { zoom: true });
    else if (h.off) zoomTo(h.off[1], h.off[2], -1);
});
elCv.addEventListener('wheel', ev => {
    const [mx] = localXY(ev, elCv);
    if (ev.ctrlKey || ev.metaKey || ev.altKey) {
        ev.preventDefault();
        zoomAt(Math.exp(ev.deltaY * (ev.deltaMode === 1 ? 0.05 : 0.0022)), mx);
    } else if (ev.shiftKey || Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) {
        ev.preventDefault();
        const dd = ev.shiftKey && !ev.deltaX ? ev.deltaY : ev.deltaX;
        panBy(dd / W * (view.e - view.s));
    }
}, { passive: false });
elScroll.addEventListener('scroll', () => { hideTip(); draw(); });

const touchPoints = new Map();
const TOUCH_SLOP = 7;
let touchGesture = null;
function pinchGeometry() {
    const [first, second] = touchPoints.values();
    return {
        center: (first.x + second.x) / 2,
        distance: Math.max(1, Math.hypot(second.x - first.x, second.y - first.y)),
    };
}
elCv.addEventListener('pointerdown', ev => {
    if (ev.pointerType !== 'touch' || !W) return;
    ev.preventDefault();
    elCv.setPointerCapture(ev.pointerId);
    const [x, y] = localXY(ev, elCv);
    touchPoints.set(ev.pointerId, { x, y });
    const span = view.e - view.s;
    if (touchPoints.size === 1) {
        touchGesture = { mode: 'tap', x, y, start: view.s, span };
        cursorX = -1; hov = -1; hovOff = null; hovCpu = null;
        hideTip();
        draw();
    } else if (touchPoints.size === 2 && touchGesture.mode !== 'blocked') {
        const { center, distance } = pinchGeometry();
        touchGesture = { mode: 'pinch', distance, span, anchor: view.s + center / W * span };
    } else {
        touchGesture.mode = 'blocked';
    }
});
elCv.addEventListener('pointermove', ev => {
    const point = touchPoints.get(ev.pointerId);
    if (!point || window.fgCurrentView() !== 'timeline' || !W) return;
    [point.x, point.y] = localXY(ev, elCv);
    const gesture = touchGesture;
    if (gesture.mode === 'blocked') return;
    if (gesture.mode === 'pinch') {
        const { center, distance } = pinchGeometry();
        zoomFrom(gesture.span * gesture.distance / distance, gesture.anchor, center);
        return;
    }
    const dx = point.x - gesture.x, dy = point.y - gesture.y;
    if (gesture.mode === 'tap' && Math.max(Math.abs(dx), Math.abs(dy)) >= TOUCH_SLOP) {
        gesture.mode = Math.abs(dx) > Math.abs(dy) ? 'pan' : 'blocked';
    }
    if (gesture.mode === 'pan') {
        view.s = gesture.start - dx / W * gesture.span;
        view.e = view.s + gesture.span;
        clampView();
        draw();
    }
});
function finishTouch(ev) {
    if (!touchPoints.delete(ev.pointerId)) return;
    const gesture = touchGesture;
    if (touchPoints.size) gesture.mode = 'blocked';
    else touchGesture = null;
    if (ev.type !== 'pointerup' || gesture.mode !== 'tap' || window.fgCurrentView() !== 'timeline') return;
    const [x, y] = localXY(ev, elCv);
    if (x >= 0 && x < W && y >= 0 && y < H &&
            Math.max(Math.abs(x - gesture.x), Math.abs(y - gesture.y)) < TOUCH_SLOP) activateCanvas(x, y);
}
for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) elCv.addEventListener(event, finishTouch);

function centerOn(t) {
    const span = view.e - view.s;
    view.s = t - span / 2; view.e = t + span / 2; clampView();
}
elHead.addEventListener('mousedown', ev => {
    const [mx, my] = localXY(ev, elHead);
    if (my > OV_H) return;
    drag = { mode: 'ov', x0: mx, moved: true };
    centerOn(ovT(mx));
    draw();
    ev.preventDefault();
});
elHead.addEventListener('wheel', ev => {
    ev.preventDefault();
    const [mx] = localXY(ev, elHead);
    zoomAt(Math.exp(ev.deltaY * 0.0022), mx);
}, { passive: false });

// ---- error navigation ----
let errList = [], errPos = -1;
function rebuildErrList() {
    errList = [];
    for (let c = 0; c < CHAINS.length; c++) if (chainShown(c) && (!errFilter || CHAINS[c][3] === 1) && (!textHits || textHits[CHAINS[c][0]])) errList.push(c);
    errList.sort((a, b) => sStart[CHAINS[a][0]] - sStart[CHAINS[b][0]]);
    errPos = -1;
    updateErrLabel();
}
function updateErrLabel() {
    const lab = tlRoot.querySelector('.err-label');
    const kind = errFilter ? 'to user' : 'all';
    lab.textContent = (errList.length ? (errPos >= 0 ? (errPos + 1) + '/' : '') + errList.length : '0') + ' errors · ' + kind;
    tlRoot.querySelectorAll('[data-act=errprev],[data-act=errnext]').forEach(b => b.disabled = !errList.length);
}
function gotoErr(dir) {
    if (!errList.length) { toast(D.caps.retval ? 'No error paths found' : 'Error paths need options/funcgraph-retval'); return; }
    if (errPos < 0) {
        const mid = sel >= 0 ? sStart[sel] : view.s;
        errPos = dir > 0 ? errList.findIndex(c => sStart[CHAINS[c][0]] > mid) : -1;
        if (dir < 0) for (let p = errList.length - 1; p >= 0; p--) if (sStart[CHAINS[errList[p]][0]] < mid) { errPos = p; break; }
        if (errPos < 0) errPos = dir > 0 ? 0 : errList.length - 1;
    } else errPos = (errPos + dir + errList.length) % errList.length;
    const ch = CHAINS[errList[errPos]];
    // frame the whole chain, select the origin
    zoomTo(sStart[ch[1]], sEnd[ch[1]], ch[0]);
    select(ch[0], { reveal: true });
    updateErrLabel();
}

// ---- search ----
let searchQuery = '', searchError = '', searchErrorKind = '', searchTimer = 0;
let searchPostingIndex = null, searchMatchQuery = null, searchNameHits = null;
let searchMatches = new Uint32Array(0), searchVisibleMatches = searchMatches;
let searchFilterBasis = null, searchWork = null, searchProgress = '', searchPaused = false;
let searchReadyQuery = '';
const searchDefaultTitle = 'Case-insensitive regular expression; highlights and navigates recorded function names. ' +
    (document.getElementById('filterInput') ? 'Search is limited by the shared filter; it does not change that filter.' :
        'Shared filtering is not enabled for this report.');
function searchIndex(target) {
    let low = 0, high = searchVisibleMatches.length;
    while (low < high) {
        const middle = (low + high) >>> 1;
        if (searchVisibleMatches[middle] < target) low = middle + 1;
        else high = middle;
    }
    return low;
}
function updateSearchLabel() {
    const busy = !!searchTimer || !!searchWork;
    const invalid = !busy && searchErrorKind === 'regex';
    elSearch.classList.toggle('bad', invalid);
    elSearch.setAttribute('aria-invalid', String(invalid));
    elSearch.title = !busy && searchError ? searchError : searchDefaultTitle;
    elCount.setAttribute('aria-busy', String(busy));
    elCount.title = busy ? searchProgress : searchError;
    const count = searchVisibleMatches.length;
    elSearchPrev.disabled = elSearchNext.disabled = busy || searchPaused || !!searchError || !count;
    if (busy) elCount.textContent = searchProgress || 'Searching…';
    else if (searchPaused) elCount.textContent = 'Search paused';
    else if (invalid) elCount.textContent = 'Invalid regex';
    else if (searchError) elCount.textContent = 'Search unavailable';
    else if (!searchQuery) elCount.textContent = '';
    else {
        const position = searchIndex(sel);
        const value = (position < count && searchVisibleMatches[position] === sel ? (position + 1) + ' / ' : '') + count;
        elCount.innerHTML = '<span class="tl-count-value">' + value + '</span> calls';
        if (!count) elCount.title = textHits && searchMatches.length ?
            'The shared filter excludes all matching calls. Adjust or clear it in Tools → Filter calls.' :
            'No recorded function names match this expression. Try a different case-insensitive regular expression.';
    }
}
function cancelSearchWork() {
    if (searchWork) clearTimeout(searchWork.timer);
    searchWork = null;
    searchProgress = '';
}
function navigateSearch(dir) {
    if (!searchVisibleMatches.length) return;
    const count = searchVisibleMatches.length;
    let position = dir > 0 ? 0 : count - 1;
    if (sel >= 0 && sel < N) {
        position = searchIndex(sel);
        if (dir < 0) position--;
        else if (searchVisibleMatches[position] === sel) position++;
        position = (position + count) % count;
    }
    select(searchVisibleMatches[position], { reveal: true });
}
function* prepareSearchMatches(query, matcher, accepted) {
    if (!searchPostingIndex) searchPostingIndex = yield* buildSearchIndex(sName, N);
    if (searchMatchQuery !== query) {
        const hits = yield* matchSearchNames(searchPostingIndex, S, matcher);
        const matches = yield* collectSearchCalls(searchPostingIndex, sName, N, hits);
        searchNameHits = hits;
        searchMatches = matches;
        searchMatchQuery = query;
    }
    const visible = yield* filterSearchCalls(searchMatches, accepted);
    searchHits = searchNameHits;
    searchFilterBasis = accepted;
    return visible;
}
function startSearchWork(query, matcher, dir) {
    const work = { steps: prepareSearchMatches(query, matcher, textHits), accepted: textHits, timer: 0, direction: dir };
    searchWork = work;
    function advance() {
        if (searchWork !== work) return;
        try {
            const deadline = performance.now() + 6;
            do {
                const result = work.steps.next();
                if (result.done) {
                    searchVisibleMatches = result.value;
                    searchReadyQuery = query;
                    searchWork = null;
                    searchProgress = '';
                    updateSearchLabel();
                    if (work.direction && searchVisibleMatches.length) navigateSearch(work.direction);
                    else draw();
                    return;
                }
                searchProgress = result.value;
            } while (performance.now() < deadline);
            updateSearchLabel();
            work.timer = setTimeout(advance, 0);
        } catch (error) {
            cancelSearchWork();
            searchError = 'Could not prepare function search: ' + error.message + '. Press Enter to retry.';
            searchErrorKind = 'work';
            searchHits = null;
            searchVisibleMatches = new Uint32Array(0);
            updateSearchLabel();
            draw();
        }
    }
    advance();
}
function rebuildSearchMatches() {
    if (searchTimer || searchPaused || !searchQuery || searchErrorKind === 'regex') { updateSearchLabel(); return; }
    applySearch(searchQuery, searchWork?.direction || 0);
}
function suspendSearch() {
    searchPaused = !!elSearch.value.trim() || !!searchTimer || !!searchWork;
    clearTimeout(searchTimer);
    searchTimer = 0;
    cancelSearchWork();
    updateSearchLabel();
}
function resumeSearch() {
    if (!searchPaused) return;
    searchPaused = false;
    applySearch(elSearch.value);
}
function applySearch(query, dir = 0) {
    clearTimeout(searchTimer);
    searchTimer = 0;
    query = query.trim();
    searchPaused = false;
    if (query === searchQuery && !searchError) {
        if (searchWork?.accepted === textHits) { if (dir) searchWork.direction = dir; updateSearchLabel(); return false; }
        if (searchReadyQuery === query && searchFilterBasis === textHits) {
            updateSearchLabel(); if (dir) navigateSearch(dir); return true;
        }
    }
    cancelSearchWork();
    searchQuery = query;
    searchError = '';
    searchErrorKind = '';
    searchHits = null;
    searchReadyQuery = null;
    searchVisibleMatches = new Uint32Array(0);
    if (!query) {
        searchMatches = searchVisibleMatches;
        searchNameHits = null;
        searchMatchQuery = null;
        searchFilterBasis = textHits;
        searchReadyQuery = '';
        updateSearchLabel(); draw(); return true;
    }
    let matcher;
    try { matcher = new RegExp(query, 'i'); }
    catch (error) {
        searchError = error.message;
        searchErrorKind = 'regex';
        updateSearchLabel(); draw(); return false;
    }
    startSearchWork(query, matcher, dir);
    return !searchWork && !searchError;
}
function searchNext(dir) { applySearch(elSearch.value, dir); }
elSearch.addEventListener('input', () => {
    clearTimeout(searchTimer);
    cancelSearchWork();
    searchPaused = false;
    if (!elSearch.value.trim()) { applySearch(''); return; }
    searchTimer = setTimeout(() => applySearch(elSearch.value), 150);
    updateSearchLabel();
});
elSearch.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && !ev.isComposing) { searchNext(ev.shiftKey ? -1 : 1); ev.preventDefault(); }
    else if (ev.key === 'Escape') { elSearch.value = ''; applySearch(''); elSearch.blur(); }
    ev.stopPropagation();
});

let positionBasis = 'offset', positionReturnFocus = null;
function positionOrigin() {
    if (MODE !== 'abs' || MIXED && !D.uftrace.aligned) return null;
    const origin = D.uftrace?.origin_ns;
    if (origin !== undefined) return typeof origin === 'string' && /^\d{1,32}$/.test(origin) ? BigInt(origin) : null;
    return Number.isSafeInteger(D.t0) && D.t0 >= 0 ? BigInt(D.t0) : null;
}
function positionText(coordinate, basis) {
    const value = BigInt(Math.round(coordinate));
    if (basis === 'timestamp') {
        const timestamp = positionOrigin() + value;
        return timestamp / 1000000000n + '.' + String(timestamp % 1000000000n).padStart(9, '0') + ' s';
    }
    if (MODE === 'count') return value / 1000n + (value % 1000n ? '.' + String(value % 1000n).padStart(3, '0').replace(/0+$/, '') : '') + ' u';
    return value + ' ns';
}
function parsePosition(text, basis) {
    text = text.trim();
    if (!text) throw new Error('Enter a position.');
    const match = text.length <= 80 && /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))\s*(ns|us|µs|μs|ms|s|u|units?)?$/i.exec(text);
    if (!match) throw new Error(MODE === 'count' ? 'Use a call-order coordinate, for example 12 or 12.5 u.' : 'Use a decimal value with ns, us, µs, ms or s.');
    const unit = (match[5] || (MODE === 'count' ? 'u' : basis === 'timestamp' ? 's' : 'ns')).toLowerCase();
    const countUnit = ['u', 'unit', 'units'].includes(unit);
    if (MODE === 'count' ? !countUnit : countUnit) throw new Error(MODE === 'count' ? 'Call-order units are not time: use u, not ns/us/ms/s.' : 'Use time units, not call-order units.');
    const fraction = match[3] || match[4] || '', divisor = 10n ** BigInt(fraction.length);
    const factor = countUnit ? 1000n : { ns: 1n, us: 1000n, 'µs': 1000n, 'μs': 1000n, ms: 1000000n, s: 1000000000n }[unit];
    const numerator = BigInt((match[2] || '0') + fraction) * factor;
    if (numerator % divisor) throw new Error(MODE === 'count' ? 'Use at most three decimal places for call-order units.' : 'Use whole nanoseconds; sub-nanosecond coordinates are unavailable.');
    let value = (match[1] === '-' ? -1n : 1n) * (numerator / divisor);
    if (basis === 'timestamp') {
        const origin = positionOrigin();
        if (origin === null) throw new Error('Recorded timestamps are unavailable for this report.');
        value -= origin;
    }
    if (value < 0n || value > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('Position must be nonnegative and fit the exact integer coordinate range.');
    const coordinate = Number(value);
    if (coordinate > tMax) throw new Error('Position is past the recorded extent (' + positionText(tMax, 'offset') + ' from the report origin).');
    return coordinate;
}
function clearPositionError() {
    elPositionError.textContent = '';
    elPositionStart.setAttribute('aria-invalid', 'false');
    elPositionEnd.setAttribute('aria-invalid', 'false');
}
function positionHint() {
    const basis = positionBasis === 'timestamp' ? 'Recorded timestamps; default unit s. ' : 'Report-origin offsets; default unit ns. ';
    const mode = MODE === 'count' ? 'Call-order coordinates only (u); no timestamps or durations are available. No unit means u. ' :
        MODE === 'compressed' ? 'Packed coordinates, not wall-clock time: gaps are lost. ns/us/µs/ms/s; default ns. ' :
        MODE === 'rel' ? 'Duration-based coordinates, not recorded timestamps. ns/us/µs/ms/s; default ns. ' : basis + 'Units: ns/us/µs/ms/s. ';
    elPosition.querySelector('#tlPositionHint').textContent = mode + 'Blank End centers at current zoom, clamped near trace edges; Start + End shows the exact interval. ' +
        'Selection, filters and analysis remain unchanged.' + (MIXED && !D.uftrace.aligned ? ' Clocks are not verified; offsets do not establish cross-space alignment.' : '');
}
function showPosition() {
    if (elPosition.open) return;
    positionReturnFocus = document.activeElement;
    positionBasis = 'offset';
    elPositionBasis.value = positionBasis;
    elPosition.querySelector('[data-position-basis]').hidden = positionOrigin() === null;
    elPosition.querySelector('#tlPositionTitle').textContent = MODE === 'count' ? 'Go to call-order position' : MODE === 'compressed' ? 'Go to packed position' : 'Go to time';
    elPositionStart.value = positionText(Math.min(Number.MAX_SAFE_INTEGER, tMax, Math.max(0, view.s)), positionBasis);
    elPositionEnd.value = '';
    clearPositionError();
    positionHint();
    elPosition.showModal();
    elPositionStart.focus();
    elPositionStart.select();
}
elPosition.addEventListener('input', clearPositionError);
elPositionBasis.addEventListener('change', () => {
    let field = elPositionStart;
    try {
        const start = parsePosition(elPositionStart.value, positionBasis);
        field = elPositionEnd;
        const end = elPositionEnd.value.trim() ? parsePosition(elPositionEnd.value, positionBasis) : null;
        positionBasis = elPositionBasis.value;
        elPositionStart.value = positionText(start, positionBasis);
        if (end !== null) elPositionEnd.value = positionText(end, positionBasis);
        clearPositionError();
        positionHint();
    } catch (error) {
        elPositionBasis.value = positionBasis;
        field.setAttribute('aria-invalid', 'true');
        elPositionError.textContent = error.message;
        field.focus();
    }
});
elPosition.querySelector('form').addEventListener('submit', event => {
    event.preventDefault();
    let field = elPositionStart;
    try {
        const start = parsePosition(elPositionStart.value, positionBasis);
        field = elPositionEnd;
        const end = elPositionEnd.value.trim() ? parsePosition(elPositionEnd.value, positionBasis) : null;
        if (end !== null && end <= start) throw new Error('End must be greater than start.');
        if (end === null) panBy(start - (view.s + view.e) / 2);
        else { view.s = start; view.e = end; draw(); }
        elPosition.close();
    } catch (error) {
        field.setAttribute('aria-invalid', 'true');
        elPositionError.textContent = error.message;
        field.focus();
    }
});
elPosition.querySelector('[data-position-cancel]').addEventListener('click', () => elPosition.close());
elPosition.addEventListener('close', () => {
    if (window.fgCurrentView() !== 'timeline') return;
    if (positionReturnFocus?.isConnected && positionReturnFocus.getClientRects().length && !positionReturnFocus.disabled) positionReturnFocus.focus({ preventScroll: true });
    else focusTimelineControl();
});

// ---- keyboard (capture phase: keeps the text view's shortcuts from firing) ----
window.addEventListener('keydown', ev => {
    if (window.fgCurrentView() !== 'timeline') return;
    if (elPosition.open) {
        ev.stopPropagation();
        if (ev.key === 'Tab') {
            const controls = Array.from(elPosition.querySelectorAll('input, select, button')).filter(control => !control.disabled && control.getClientRects().length);
            const index = controls.indexOf(document.activeElement);
            if (controls.length && (index < 0 || ev.shiftKey && index === 0 || !ev.shiftKey && index === controls.length - 1)) {
                ev.preventDefault();
                controls[ev.shiftKey ? controls.length - 1 : 0].focus();
            }
        }
        return;
    }
    if (ev.key === 'Escape' && elOptions.classList.contains('open')) {
        ev.stopPropagation(); ev.preventDefault();
        if (ev.target === elFtext) {
            restoreTimelineFilterDraft();
        }
        setToolbarOptions(false, true);
        return;
    }
    if (ev.key === 'Escape' && tlRoot.classList.contains('tl-fullscreen') && !elHelp.classList.contains('show')) {
        ev.stopPropagation(); ev.preventDefault();
        leaveFullscreen();
        return;
    }
    if (ev.target.matches && ev.target.matches('input, textarea, select')) return;
    ev.stopPropagation();
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.closest && ev.target.closest('button, a[href], summary')) return;
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const span = view.e - view.s, cx = cursorX >= 0 ? cursorX : W / 2;
    let handled = true;
    switch (ev.key) {
        case 'w': case 'W': case '+': case '=': zoomAt(0.8, cx); break;
        case 's': case 'S': case '-': zoomAt(1.25, cx); break;
        case 'a': case 'A': case 'ArrowLeft': panBy(-span * 0.15); break;
        case 'd': case 'D': case 'ArrowRight': panBy(span * 0.15); break;
        case '0': case 'Home': fitAll(); break;
        case 'f': case 'F': if (sel >= 0) zoomTo(sStart[sel], sEnd[sel], sel); else if (range) zoomTo(range.s, range.e, -1); break;
        case 'e': gotoErr(1); break;
        case 'E': gotoErr(-1); break;
        case 'n': searchNext(1); break;
        case 'N': searchNext(-1); break;
        case 'ArrowUp': navigateCall('parent'); break;
        case 'ArrowDown': navigateCall('child'); break;
        case '[': navigateCall('previous'); break;
        case ']': navigateCall('next'); break;
        case 'Enter': if (sel >= 0) gotoText(sLine[sel]); break;
        case '/': elSearch.focus(); elSearch.select(); break;
        case 'g': case 'G': showPosition(); break;
        case 'h': case 'H': if (sel >= 0) showDist(sName[sel]); break;
        case 'o': case 'O': toggleAdjusted(); break;
        case 'i': case 'I': toggleInlineView(); break;
        case 'm': case 'M': toggleFullscreen(); break;
        case '?': setToolbarOptions(false); elHelp.classList.toggle('show'); break;
        case 'Escape':
            if (elHelp.classList.contains('show')) elHelp.classList.remove('show');
            else { range = null; select(-1); }
            break;
        default: handled = false;
    }
    if (handled) {
        ev.preventDefault();
        if (ev.key !== '/' && ev.key.toLowerCase() !== 'g' && window.fgCurrentView() === 'timeline') focusTimelineControl();
    }
}, true);

function callTarget(index, action) {
    if (index < 0 || index >= NA) return -1;
    if (action === 'parent') {
        const parent = sParent[index];
        return parent >= 0 && parent < N && sTask[parent] === sTask[index] &&
            (isSyn(index) || sDepth[parent] < sDepth[index]) ? parent : -1;
    }
    const tree = callTreeIndex();
    if (isSyn(index)) return action === 'child' ? SYN[index - N].kids[0] ?? -1 : -1;
    return action === 'child' ? tree.first[index] : action === 'previous' ? tree.previous[index] : action === 'next' ? tree.next[index] : -1;
}
function navigateCall(action, focusControl) {
    const target = callTarget(sel, action);
    if (target < 0) return;
    select(target, { reveal: true });
    elDetail.scrollTop = 0; elDetail.scrollLeft = 0;
    if (focusControl) {
        const button = elDetail.querySelector('[data-call-nav="' + action + '"]');
        (button && !button.disabled ? button : elCv).focus({ preventScroll: true });
    }
}
