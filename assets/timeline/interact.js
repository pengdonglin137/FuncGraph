/* FuncGraph Timeline: interaction */
function hitTest(mx, my) {
    // returns {track, slice, off, header}
    const yy = my + elScroll.scrollTop;
    let t = null;
    for (const tr of ORDER) if (!tr.hidden && yy >= tr.y && yy < tr.y + tr.h - GAP) { t = tr; break; }
    if (!t) return { track: null, slice: -1, off: null, header: false };
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
    let best = -1;
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
    }
    let off = null;
    if (best < 0 && ov.off && !t.collapsed) {
        for (const r of t.off) if (offRow(r) === d && tm >= r[1] && tm <= r[2]) { off = r; break; }
    }
    return { track: t, slice: best, off: off, header: false };
}

function zoomAt(factor, mx) {
    const span = view.e - view.s;
    let ns = span * factor;
    const minSpan = MODE === 'count' ? 2000 : 50;
    ns = Math.max(minSpan, Math.min(tMax * 1.05, ns));
    const tm = view.s + (mx / W) * span;
    view.s = tm - (mx / W) * ns;
    view.e = view.s + ns;
    clampView();
    draw();
}
function panBy(dt) { view.s += dt; view.e += dt; clampView(); draw(); }
function clampView() {
    const span = view.e - view.s, pad = tMax * 0.02;
    if (view.s < -pad) { view.s = -pad; view.e = view.s + span; }
    if (view.e > tMax + pad) { view.e = tMax + pad; view.s = view.e - span; }
}
function fitAll() { view.s = 0; view.e = tMax; draw(); }
function zoomTo(s, e, revealSlice) {
    const span = Math.max(e - s, MODE === 'count' ? 2000 : 50);
    const pad = span * 0.08;
    view.s = s - pad; view.e = s + span + pad;
    clampView();
    if (revealSlice >= 0) revealRow(revealSlice);
    draw();
}
function revealRow(i) {
    const t = TRACKS[sTask[i]];
    if (t.collapsed) { t.collapsed = false; layout(); }
    const y = t.y + HDR + rowOf(i) * ROW;
    if (y < elScroll.scrollTop + 4 || y + ROW > elScroll.scrollTop + H - 4) elScroll.scrollTop = Math.max(0, y - H / 3);
}
function select(i, opts) {
    sel = i;
    opts = opts || {};
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
    } else hideDetail();
    draw();
}

// ---- tooltip ----
function inlineFacts(i) {
    // facts about synthetic inline frame i
    const y = SYN[i - N], ch = y.chain, k = y.k;
    const kids = y.kids;
    let covered = 0;
    for (const c of kids) covered += sEnd[c] - sStart[c];
    const span = sEnd[i] - sStart[i];
    const outer = ch[k - 1];
    return { y, ch, k, kids, covered, span, gap: Math.max(0, span - covered), fn: ch[k][0], file: ch[k][1],
        intoFn: outer[0], atFile: outer[1], atLine: outer[2], mismatch: y.p >= 0 && nameOf(y.p).split('.')[0] !== ch[0][0].split('.')[0] };
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
    if (i >= N) return tipInline(i);
    const d = durOf(i), f = sFlags[i];
    let h = '<div class="t-name">' + esc(nameOf(i)) + (modOf(i) ? ' [' + esc(modOf(i)) + ']' : '') + '</div>';
    h += '<div><span class="t-dur">' + fmtDur(d) + '</span>' + (f & FL.EST ? ' <span class="t-warn">(estimated)</span>' : '') +
        ' &nbsp;self ' + fmtDur(sSelf[i]) + '</div><div class="t-sep"></div>';
    const row = (k, v, cls) => '<div class="t-row"><span class="t-k">' + k + '</span><span class="t-v' + (cls ? ' ' + cls : '') + '">' + v + '</span></div>';
    h += row('start', fmtDur(sStart[i]) + (MODE === 'abs' ? ' &nbsp;(' + fmtAbs(sStart[i]) + ')' : ''));
    const t = TRACKS[sTask[i]];
    h += row('task', esc(trackLabel(t)) + (cpuOf(i) >= 0 ? ' &nbsp;CPU ' + cpuOf(i) : ''));
    h += row('context', CTX_NAMES[f & FL.CTX] + (f & FL.CTXINF ? ' (inferred)' : '') + ctxFlagText(f));
    const r = retOf(i);
    if (r !== null) {
        if (eRole[i] >= 0) h += row('return', esc(r) + ' ' + (errName(eErr[i]) || '') + ' — ' + errRoleText(i), 't-err');
        else h += row('return', esc(r) + (f & FL.NULLRET ? ' (NULL)' : ''));
    }
    const c = callerOf(i);
    if (c) h += row('caller', esc(c) + (f & FL.MISMATCH ? ' <span class="t-warn">(untraced frame between)</span>' : ''));
    const csp = c ? callSitePos(i) : null;
    if (csp) h += row('source', esc(csp.at));
    const il = inlineLine(i);
    if (il) h += row('inlined in', esc(il));
    const a = argsOf(i);
    if (a) h += row('args', esc(a.length > 180 ? a.slice(0, 180) + '…' : a));
    if (f & FL.TRUNC) h += '<div class="t-warn">exit not captured (trace window ended or events lost)</div>';
    if (f & FL.ORPHAN) h += '<div class="t-warn">entry not captured (started before trace window)</div>';
    return h;
}
function ctxFlagText(f) {
    if (!(f & FL.LAT)) return '';
    const parts = [];
    if (f & FL.IRQOFF) parts.push('irqs off');
    if (f & FL.BHOFF) parts.push('BH off');
    const rs = (f >> FL.RS_SHIFT) & 7;
    if (rs) parts.push(RESCHED_NAMES[rs]);
    const p = (f >> FL.PR_SHIFT) & 15;
    if (p) parts.push('preempt ' + p);
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
function openTaskAt(tid, tm) {
    const t = TRACKS[tid];
    t.hidden = false;
    if (t.collapsed) t.collapsed = false;
    layout();
    elScroll.scrollTop = Math.max(0, t.y - 4);
    if (t.rowsT.length) {
        const row = t.rowsT[0], j = lbEnd(row, tm);
        if (j < row.length && sStart[row[j]] <= tm) { select(row[j]); return; }
    }
    draw();
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
function hideTip() { elTip.style.display = 'none'; }

// ---- mouse ----
let drag = null;   // {mode:'pan'|'range'|'ov', x0, s0, e0, moved, track}
function localXY(ev, el) { const b = el.getBoundingClientRect(); return [ev.clientX - b.left, ev.clientY - b.top]; }
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
            centerOn(mx / W * tMax);
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
    if (nh !== hov || h.off !== hovOff) { hov = nh; hovOff = h.off; }
    hovCpu = h.cpuSeg || null;
    elCv.classList.toggle('ptr', nh >= 0 || h.header || !!hovCpu);
    if (hovCpu) showTip(tipCpu(hovCpu), ev);
    else if (nh >= 0) showTip(tipSlice(nh), ev);
    else if (h.off) showTip(tipOff(h.off), ev);
    else hideTip();
    draw();
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
    const [mx, my] = localXY(ev, elCv);
    const h = hitTest(mx, my);
    if (h.header && h.track) { h.track.collapsed = !h.track.collapsed; layout(); draw(); return; }
    if (h.cpuSeg) { openTaskAt(h.cpuSeg.t, h.tm); return; }
    if (h.slice >= 0) select(h.slice);
    else if (h.off) { sel = -1; showWaitChain(h.off); draw(); }
    else if (range) { range = null; hideDetail(); draw(); }
    else select(-1);
});
elCv.addEventListener('mouseleave', () => { cursorX = -1; hov = -1; hovOff = null; hovCpu = null; hideTip(); draw(); });
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

function centerOn(t) {
    const span = view.e - view.s;
    view.s = t - span / 2; view.e = t + span / 2; clampView();
}
elHead.addEventListener('mousedown', ev => {
    const [mx, my] = localXY(ev, elHead);
    if (my > OV_H) return;
    drag = { mode: 'ov', x0: mx, moved: true };
    centerOn(mx / W * tMax);
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
function applySearch(q) {
    q = q.trim();
    elSearch.classList.remove('bad');
    if (!q) { search = null; searchHits = null; elCount.textContent = ''; draw(); return; }
    try { search = new RegExp(q, 'i'); } catch (e) { elSearch.classList.add('bad'); return; }
    searchHits = new Uint8Array(S.length);
    for (let s = 0; s < S.length; s++) if (search.test(S[s])) searchHits[s] = 1;
    let cnt = 0;
    for (let i = 0; i < N; i++) if (searchHits[sName[i]]) cnt++;
    elCount.textContent = cnt + ' calls';
    draw();
}
function searchNext(dir) {
    if (!searchHits) return;
    const from = sel >= 0 ? sel : -1;
    // slices are ordered by (task, start); walk in that order
    for (let s = 1; s <= N; s++) {
        const i = ((from + dir * s) % N + N) % N;
        if (searchHits[sName[i]]) { select(i, { reveal: true }); return; }
    }
}
let searchTimer = 0;
elSearch.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => applySearch(elSearch.value), 150); });
elSearch.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { applySearch(elSearch.value); searchNext(ev.shiftKey ? -1 : 1); ev.preventDefault(); }
    else if (ev.key === 'Escape') { elSearch.value = ''; applySearch(''); elSearch.blur(); }
    ev.stopPropagation();
});

// ---- keyboard (capture phase: keeps the text view's shortcuts from firing) ----
window.addEventListener('keydown', ev => {
    if (window.fgCurrentView() !== 'timeline') return;
    if (ev.target.matches && ev.target.matches('input, textarea, select')) return;
    ev.stopPropagation();
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
        case 'ArrowUp': if (sel >= 0 && sParent[sel] >= 0) select(sParent[sel], { reveal: true }); break;
        case 'ArrowDown': if (sel >= 0) { const c = firstChild(sel); if (c >= 0) select(c, { reveal: true }); } break;
        case '[': if (sel >= 0) { const c = sibling(sel, -1); if (c >= 0) select(c, { reveal: true }); } break;
        case ']': if (sel >= 0) { const c = sibling(sel, 1); if (c >= 0) select(c, { reveal: true }); } break;
        case 'Enter': if (sel >= 0) gotoText(sLine[sel]); break;
        case '/': elSearch.focus(); elSearch.select(); break;
        case 'h': case 'H': if (sel >= 0) showDist(sName[sel]); break;
        case 'o': case 'O': toggleAdjusted(); break;
        case 'i': case 'I': toggleInlineView(); break;
        case 'm': case 'M': setMax(!document.body.classList.contains('tl-max')); break;
        case '?': elHelp.classList.toggle('show'); break;
        case 'Escape':
            if (elHelp.classList.contains('show')) elHelp.classList.remove('show');
            else { range = null; select(-1); }
            break;
        default: handled = false;
    }
    if (handled) ev.preventDefault();
}, true);

function rowPos(i) {
    const row = TRACKS[sTask[i]].rowsT[sDepth[i]];
    const j = lbStart(row, sStart[i], 0);
    for (let q = j; q < row.length && sStart[row[q]] <= sStart[i]; q++) if (row[q] === i) return [row, q];
    for (let q = Math.max(0, j - 2); q < row.length; q++) if (row[q] === i) return [row, q];
    return [row, -1];
}
function firstChild(i) {
    const t = TRACKS[sTask[i]];
    if (isSyn(i) || sDepth[i] + 1 >= t.rowsT.length) return -1;
    const row = t.rowsT[sDepth[i] + 1];
    const j = lbEnd(row, sStart[i]);
    for (let q = j; q < row.length && sStart[row[q]] <= sEnd[i]; q++) if (sParent[row[q]] === i) return row[q];
    return -1;
}
function sibling(i, dir) {
    if (isSyn(i)) return -1;
    const [row, q] = rowPos(i);
    if (q < 0) return -1;
    const nq = q + dir;
    return nq >= 0 && nq < row.length ? row[nq] : -1;
}
