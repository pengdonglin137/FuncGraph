/* FuncGraph Timeline: interaction */
function trackAtY(position) {
    const track = visibleTracks[firstTrackEndingAfter(position)];
    return track && position >= track.y && position < track.y + track.h - GAP ? track : null;
}
function rangeTrackAtY(position) {
    const track = trackAtY(position);
    return track && TRACKS[track.id] === track ? track : null;
}
function hitTest(mx, my) {
    // returns {track, slice, off, header}
    const yy = my + elScroll.scrollTop;
    const t = trackAtY(yy);
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
    if (t.kvmEvents.length && (t.collapsed || d === t.maxD + 1)) {
        const eventBand = (yy - t.y - HDR) % ROW < 3;
        return { track: t, slice: -1, off: null, header: false, kvm: hitKvm(t, tm, k, eventBand) };
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
        const exactWide = best >= 0 && (sEnd[best] - sStart[best]) * k >= DENSE_SLICE_WIDTH &&
            mx >= (sStart[best] - view.s) * k && mx < (sEnd[best] - view.s) * k;
        if (!exactWide) {
            const summary = denseHitAt(row, Math.floor(mx), view.s, k);
            if (summary) { best = summary.index; if (summary.count > 1) dense = summary; }
        }
    }
    let off = null, denseOff = 0;
    if (best < 0 && ov.off && !t.collapsed && t.off.length) {
        off = visitOffIntervals(t, tm, tm, d, d, () => true);
        if (!off && groupsDenseOff(t) && mx >= 0 && mx < W) {
            const group = denseOffAt(t, Math.floor(mx), d, k);
            if (group) { off = group.record; denseOff = group.count; }
        }
    }
    return { track: t, slice: best, off: off, header: false, dense, denseOff };
}
const unfilteredDenseHit = {};
function denseHitAt(row, pixel, start, scale) {
    const matching = matchingState();
    const hit = matching ? (matching.hit ||= {}) : unfilteredDenseHit;
    if (hit.row === row && hit.pixel === pixel && hit.start === start && hit.scale === scale) return hit.summary;
    const summary = densePixelAt(row, pixel, start, scale);
    hit.row = row;
    hit.pixel = pixel;
    hit.start = start;
    hit.scale = scale;
    hit.summary = summary;
    return summary;
}
let lastDenseOff = null;
function denseOffAt(track, pixel, depth, scale) {
    const start = view.s, records = track.off;
    if (lastDenseOff?.track === track && lastDenseOff.records === records &&
            lastDenseOff.pixel === pixel && lastDenseOff.depth === depth && lastDenseOff.inline === inlineView &&
            lastDenseOff.start === start && lastDenseOff.scale === scale && lastDenseOff.width === W) return lastDenseOff.group;
    const left = start + (pixel - 1) / scale, right = start + (pixel + 2) / scale;
    let first = null, count = 0;
    visitOffIntervals(track, left, right, depth, depth, record => {
        if (offStartPixel(record, start, scale) !== pixel) {
            if (count >= DENSE_OFF_LIMIT) return true;
            first = null;
            count = 0;
        } else {
            first ??= record;
            count++;
        }
        return false;
    });
    const group = count >= DENSE_OFF_LIMIT ? {record: first, count} : null;
    lastDenseOff = {track, records, pixel, depth, inline: inlineView, start, scale, width: W, group};
    return group;
}
function denseOffMessage(count) {
    return count + ' waits start in this pixel. Showing the first; zoom in for individual waits.';
}

const MIN_ZOOM_SPAN = MODE === 'count' ? 2000 : 50;
const MAX_ZOOM_SPAN = Math.max(MIN_ZOOM_SPAN, tMax * 1.05);
function zoomFrom(span, anchor, horizontal) {
    const nextSpan = Math.max(MIN_ZOOM_SPAN, Math.min(MAX_ZOOM_SPAN, span));
    if (panTo(anchor - horizontal / W * nextSpan, nextSpan)) draw();
}
function zoomAt(factor, horizontal, surface = null) {
    const span = view.e - view.s, limit = factor < 1 ? MIN_ZOOM_SPAN : MAX_ZOOM_SPAN;
    const spanPrecision = Number.EPSILON * Math.max(Math.abs(view.s), Math.abs(view.e), limit);
    if (factor === 1 || Math.abs(span - limit) <= spanPrecision) return;
    const position = surface ? horizontal - surface.getBoundingClientRect().left : horizontal;
    zoomFrom(span * factor, view.s + position / W * span, position);
}
function panBy(offset) {
    const start = view.s, end = view.e, pad = tMax * 0.02;
    const minimumOffset = -pad - start, maximumOffset = tMax + pad - end;
    const bounded = Math.min(maximumOffset, Math.max(minimumOffset, offset));
    if (!bounded) return false;
    const nextStart = bounded === minimumOffset ? -pad : start + bounded;
    const nextEnd = bounded === maximumOffset ? tMax + pad : end + bounded;
    if (nextStart === start && nextEnd === end) return false;
    view.s = nextStart;
    view.e = nextEnd;
    draw();
    return true;
}
function panTo(start, span) {
    const pad = tMax * 0.02, maximum = tMax + pad;
    let nextStart = Math.max(-pad, start), nextEnd = nextStart + span;
    if (nextEnd > maximum) { nextEnd = maximum; nextStart = maximum - span; }
    if (nextStart === view.s && nextEnd === view.e) return false;
    view.s = nextStart;
    view.e = nextEnd;
    return true;
}
function clampView() {
    const span = view.e - view.s, pad = tMax * 0.02;
    if (view.s < -pad) { view.s = -pad; view.e = view.s + span; }
    if (view.e > tMax + pad) { view.e = tMax + pad; view.s = view.e - span; }
}
function fitAll() { view.s = tMin; view.e = tMax; draw(); }
function fitCalls() {
    if (!N) return;
    view.s = callStart; view.e = Math.max(callStart + 1, callEnd); draw();
}
function zoomTo(s, e, revealSlice) {
    const span = Math.max(e - s, MIN_ZOOM_SPAN);
    const pad = span * 0.08;
    view.s = s - pad; view.e = s + span + pad;
    clampView();
    if (revealSlice >= 0) revealRow(revealSlice);
    draw();
}
function frameTraceSlice(index) {
    const start = sStart[index], end = sEnd[index], duration = end - start;
    const span = view.e - view.s;
    if (duration > span) { zoomTo(start, end, -1); return; }
    const nextSpan = duration > 0 && W > 0 && duration * W < span * 48
        ? Math.min(span, Math.max(MIN_ZOOM_SPAN, duration * Math.max(2, W / 56))) : span;
    if (nextSpan === span && start >= view.s && end <= view.e && start < view.e && end > view.s) return;
    view.s = start + (duration - nextSpan) / 2;
    view.e = view.s + nextSpan;
    clampView();
}
function revealSliceControls(rowOffset, keepSearch = false) {
    const controls = elDetail.querySelector('.tl-call-controls') || elDetail.querySelector('.tl-close');
    if (!controls) return;
    const viewport = window.visualViewport;
    const viewportTop = viewport?.offsetTop ?? 0;
    const viewportBottom = viewportTop + (viewport?.height ?? innerHeight);
    const delta = Math.ceil(controls.getBoundingClientRect().bottom - viewportBottom);
    if (delta <= 0) return;
    const fullscreen = tlRoot.classList.contains('tl-fullscreen');
    const stickyInset = fullscreen ? detailViewportTop(viewportTop) : window.fgPageChrome().tabs[0]?.parentElement.offsetHeight || 0;
    const rowTop = elCv.getBoundingClientRect().top + Math.max(0, rowOffset);
    const topLimit = Math.max(viewportTop, stickyInset);
    if (rowTop - delta < topLimit) return;
    if (keepSearch && elSearch.parentElement.getBoundingClientRect().top - delta < topLimit) return;
    (fullscreen ? tlRoot : window).scrollBy({ top: delta, behavior: 'instant' });
}
function revealRow(i, { expand = true, page = false, nearest = false, openedDetail = false, keepSearch = false } = {}) {
    const t = TRACKS[sTask[i]];
    if (t.collapsed && expand) { t.collapsed = false; layout(); }
    const y = t.y + HDR + (t.collapsed ? 0 : rowOf(i)) * ROW;
    const scrollTop = elScroll.scrollTop;
    const maximumScroll = y - 4, minimumScroll = y + (nearest && t.collapsed ? MINI : ROW) + 4 - H;
    if (scrollTop > maximumScroll || scrollTop < minimumScroll) {
        elScroll.scrollTop = Math.max(0, nearest ? Math.min(maximumScroll, Math.max(minimumScroll, scrollTop)) : y - H / 3);
    }
    if ((openedDetail || page || keepSearch) && H && !t.hidden) revealSliceControls(y - elScroll.scrollTop, keepSearch);
    if (page && H && !t.hidden) {
        const viewport = window.visualViewport;
        const viewportBottom = (viewport?.offsetTop ?? 0) + (viewport?.height ?? innerHeight);
        const rowBottom = elScroll.getBoundingClientRect().top + Math.min(H, y + ROW - elScroll.scrollTop);
        if (rowBottom > viewportBottom) window.scrollBy({ top: rowBottom - viewportBottom + 4, behavior: 'instant' });
    }
}
function setSelection(index, errorCode) {
    closeCallActions();
    selKvm = null;
    const changed = sel !== index;
    sel = index;
    syncErrorPosition(index, errorCode);
    if (changed) updateSearchSelection();
}
function showsSelectedSlice(index) {
    return index === sel && index === detailSliceIndex && !range && AN.kind === null && elDetail.classList.contains('show');
}
function select(i, opts) {
    opts = opts || {};
    const reuseDetail = i >= 0 && showsSelectedSlice(i);
    setSelection(i);
    if (i >= 0) {
        const previousChartHeight = H;
        let revealedOnOpen = false;
        const revealBeforeMount = !opts.zoom && opts.reveal && !opts.revealPage && (!opts.keepSearch || landscapeDetails.matches);
        const firstCanvasClick = opts.canvasDetailOpen === false && !opts.zoom && !opts.reveal;
        const revealOnOpen = revealBeforeMount || firstCanvasClick ? () => {
            revealRow(i, revealBeforeMount ? undefined : { expand: false, nearest: true });
            revealedOnOpen = true;
        } : null;
        if (opts.zoom) zoomTo(sStart[i], sEnd[i], -1);
        else if (opts.frameTrace) frameTraceSlice(i);
        else if (opts.reveal) {
            const start = sStart[i], end = sEnd[i], span = view.e - view.s;
            const outside = start >= view.e || end < view.s || (end === view.s && start < end);
            if (end - start > span) zoomTo(start, end, -1);
            else if (outside) {
                const center = start + (end - start) / 2;
                view.s = center - span / 2;
                view.e = center + span / 2;
                clampView();
            }
        }
        if (reuseDetail) updateDenseSelection(elDetail.firstElementChild, opts.dense);
        else showDetail(i, opts.dense, revealOnOpen);
        if (opts.zoom || opts.reveal) {
            if (!revealedOnOpen) revealRow(i, { page: opts.revealPage, keepSearch: opts.keepSearch });
        } else if (opts.canvasDetailOpen !== undefined) {
            if (!revealedOnOpen && (!opts.canvasDetailOpen || H < previousChartHeight))
                revealRow(i, { expand: false, nearest: true, openedDetail: !opts.canvasDetailOpen });
        } else if (compactToolbar.matches) revealRow(i, { expand: false });
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
    const filtered = !!(searchHits || textHits);
    const matching = filtered ? '; ' + summary.matches + ' match the current search/filter' : '';
    return '<div class="t-warn">Dense pixel: ' + summary.count + ' frames share this pixel' + matching +
        '. Zoom in to separate them. Details show the ' + (filtered && summary.matches ? 'first matching' : 'first') + ' frame.</div><div class="t-sep"></div>';
}
function tipSymbol(value) { return '<span title="' + esc(value) + '">' + esc(symbolSiteLabel(value)) + '</span>'; }
function tipGhost(i) {
    const f = ghostFacts(i);
    const row = (k, v) => '<div class="t-row"><span class="t-k">' + k + '</span><span class="t-v">' + v + '</span></div>';
    const name = f.y.name === '?' ? 'unknown frame' : f.y.name;
    let h = '<div class="t-name" title="' + esc(name) + '">' + esc(symbolLabel(name)) + ' <span class="t-warn">(not traced)</span></div>';
    h += '<div>extent of the calls inside <span class="t-dur">' + fmtDur(f.span) + '</span></div><div class="t-sep"></div>';
    h += row('inside', tipSymbol(nameOf(f.y.p)));
    h += row('traced calls', f.kids.length + ' (' + fmtDur(f.covered) + ')');
    h += '<div class="t-warn">The trace skips ' + f.y.gap + ' call level' + (f.y.gap > 1 ? 's' : '') + ' here: the entry was not recorded. ' +
        (f.y.name === '?' ? 'Name unknown.' : 'Name from the return address of the calls inside.') + '</div>';
    return h;
}
function tipInline(i) {
    const f = inlineFacts(i);
    const row = (k, v) => '<div class="t-row"><span class="t-k">' + k + '</span><span class="t-v">' + v + '</span></div>';
    let h = '<div class="t-name" title="' + esc(f.fn) + '">' + esc(symbolLabel(f.fn)) + ' <span class="t-warn">(inlined)</span></div>';
    h += '<div>traced extent <span class="t-dur">' + fmtDur(f.span) + '</span></div><div class="t-sep"></div>';
    h += row('inlined into', tipSymbol(f.intoFn) + ' at ' + esc(f.atFile) + ':' + f.atLine);
    h += row('defined in', esc(f.file));
    h += row('traced calls', f.kids.length + ' (' + fmtDur(f.covered) + ')');
    h += row('between them', fmtDur(f.gap) + ' (own inlined code or untraced work)');
    h += '<div class="t-warn">Start/end = first/last traced call inside: the inlined code itself may begin earlier and end later.</div>';
    if (f.mismatch) h += '<div class="t-warn">' + esc(callerRelationText(f.kids[0])) + '</div>';
    return h;
}
function inlineLine(i) {
    // "a > b > c" path of inline frames around the call site of real slice i
    const ch = inlChain(i);
    return ch && ch.length > 1 ? ch.slice(1).map(frame => symbolLabel(frame[0])).join(' › ') : '';
}
function tipEnumText(match) {
    const {name, value, names, recordedMatch, labelMismatch} = match;
    return (name ? name + ' (' + value + ')' : value + ' · No BTF match') +
        (names.length > 1 ? (recordedMatch ? ' · ' : ' · first of ') + names.length + ' names' : '') +
        (recordedMatch ? ' · Recorded label' : labelMismatch ? ' · Label mismatch' : '');
}
function tipEnumArguments(index, raw, signature) {
    if (!signature?.args || !raw || raw.length > 4096) return null;
    const args = btfEnumArguments(index, raw, signature);
    if (!args) return null;
    const limit = 180, omitted = ', …';
    const preview = [], enums = [];
    for (const {name, match, field, value} of args.parts) {
        const text = match ? name + '=' + tipEnumText(match) : field || name + '=' + value;
        preview.push(text);
        if (match) enums.push(text);
    }
    if (!enums.length) return null;
    if (args.truncation) preview.push(args.truncation);
    const complete = preview.join(', ');
    if (complete.length <= limit) return complete;
    let compact = '';
    for (const text of enums) {
        const separator = compact ? ', ' : '';
        if (compact.length + separator.length + text.length + omitted.length <= limit) compact += separator + text;
    }
    return compact ? compact + omitted : null;
}
function tipSlice(i) {
    if (i >= N) return isGhost(i) ? tipGhost(i) : tipInline(i);
    const d = durOf(i), f = sFlags[i];
    let h = '<div class="t-name" title="' + esc(nameOf(i)) + '">' + esc(sliceLabel(i)) + (modOf(i) ? ' [' + esc(modOf(i)) + ']' : '') + '</div>';
    h += '<div><span class="t-dur">' + fmtDur(d) + '</span>' + (f & FL.EST ? ' <span class="t-warn">(estimated)</span>' : '') +
        ' &nbsp;self ' + fmtDur(sSelf[i]) + '</div><div class="t-sep"></div>';
    const row = (k, v, cls, title) => '<div class="t-row"><span class="t-k">' + k + '</span><span class="t-v' + (cls ? ' ' + cls : '') + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + v + '</span></div>';
    h += row('start', fmtDur(sStart[i]) + (MODE === 'abs' ? ' &nbsp;(' + fmtAbs(sStart[i]) + ')' : ''));
    const t = TRACKS[sTask[i]];
    h += row('task', esc(trackLabel(t)) + (cpuOf(i) >= 0 ? ' &nbsp;CPU ' + cpuOf(i) : ''));
    if (D.uftrace) h += row('space', f & FL.BOUNDARY ? 'Kernel · entered from user' : f & FL.USER ? 'User' : 'Kernel');
    if (!D.uftrace || f & FL.LAT) h += row('context', contextName(f) + (f & FL.CTXINF ? ' (inferred)' : '') + ctxFlagText(f));
    const btf = btfDetailOf(i);
    const enumReturn = btf?.ret != null ? btfEnumMatch(rawRetOf(i), btf.ret) : null;
    const r = enumReturn ? tipEnumText(enumReturn) : retOf(i);
    const userDetail = f & FL.USER ? D.uftrace?.user_details?.[sLine[i]] : null;
    if (userDetail?.retval !== undefined) {
        const failure = USER_ERRORS.get(i);
        h += row('user return', esc(userDetail.retval) + (failure ? ' · ' + esc(failure.summary) : ''), failure ? 't-err' : '');
    }
    if (r !== null) {
        const field = enumReturn || kernelTypesOf(i)?.ret ? 'return (BTF)' : D.uftrace ? 'raw return' : 'return';
        if (eRole[i] >= 0) h += row(field, esc(r) + ' ' + (errName(eErr[i]) || '') + ' — ' + errRoleText(i), 't-err');
        else h += row(field, esc(r) + (f & FL.NULLRET ? ' (NULL)' : ''));
    }
    const c = callerOf(i);
    if (c) h += row('caller', esc(symbolSiteLabel(c)) + (f & FL.MISMATCH ? '<div class="tl-tail-note">' + esc(callerRelationText(i)) + '</div>' : ''), '', c);
    const csp = c ? callSitePos(i) : null;
    if (csp) h += row('source', esc(csp.at));
    const userSite = f & FL.USER ? userCallSite(i) : null;
    const userSource = f & FL.USER ? userSite || userPosition(i) : null;
    if (userSource) h += row(userSite ? 'call site' : 'function entry', esc(userSource[0] + ':' + userSource[1]));
    const il = inlineLine(i);
    if (il) h += row(returnSiteOwner(i) === i ? 'inlined in' : 'return-site inline context', esc(il));
    const a = argsOf(i);
    const enumArgs = tipEnumArguments(i, a, btf);
    const namedArgs = enumArgs || namedArgsOf(i) || a;
    if (namedArgs) h += row(enumArgs ? 'args (BTF)' : 'args',
        esc(!enumArgs && namedArgs.length > 180 ? namedArgs.slice(0, 180) + '…' : namedArgs));
    if (f & FL.TRUNC) h += '<div class="t-warn">exit not captured (trace window ended or events lost)</div>';
    if (f & FL.ORPHAN) h += '<div class="t-warn">entry not captured (started before trace window)</div>';
    return h;
}
// preempt depth above the corresponding graph-record baseline (task context; IRQ context is already atomic)
const PBASE = D.preempt_base || 0;
function preemptBase(flags) { return flags & FL.ORPHAN ? D.preempt_exit_base ?? PBASE : PBASE; }
function preemptOff(f) { return (f & FL.LAT) ? Math.max(0, ((f >> FL.PR_SHIFT) & 15) - preemptBase(f)) : 0; }
function ctxFlagText(f) {
    if (!(f & FL.LAT)) return '';
    const parts = [];
    if (f & FL.IRQUNKNOWN) parts.push('IRQ state unavailable (X)');
    if (f & FL.IRQOFF) parts.push('irqs off');
    if (f & FL.BHOFF) parts.push('BH off');
    const rs = (f >> FL.RS_SHIFT) & 7;
    if (rs) parts.push(RESCHED_NAMES[rs]);
    const p = (f >> FL.PR_SHIFT) & 15;
    if (p) parts.push('preempt ' + p + (p > preemptBase(f) ? ' (preempt off)' : ''));
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
    if (r[10] >= 0) h += row('blocked at (recorded)', esc(S[r[10]]));
    if (r[11] !== undefined) h += row('I/O wait (recorded)', r[11] ? 'yes' : 'no');
    if (r[5] >= 0) h += row('state', esc(S[r[5]]));
    h += row('switch', 'CPU ' + (r[6] >= 0 ? r[6] : '?') + ' → ' + (r[7] >= 0 ? 'CPU ' + r[7] : 'not back in window'));
    if (r[9] >= 0) h += row('woken by', esc(trackLabel(TRACKS[r[9]])));
    return h;
}
let cpuIrqSummary = null;
function cpuIrqTimes(segment) {
    if (cpuIrqSummary && cpuIrqSummary.cpu === segment.cpu &&
            cpuIrqSummary.start === segment.s && cpuIrqSummary.end === segment.e) return cpuIrqSummary.totals;
    const totals = [0, 0, 0, 0];
    visitCpuInterrupts(segment.cpu, segment.s, segment.e, record => {
        if (record[1] > segment.s && record[0] < segment.e)
            totals[record[2]] += Math.min(record[1], segment.e) - Math.max(record[0], segment.s);
    });
    cpuIrqSummary = { cpu: segment.cpu, start: segment.s, end: segment.e, totals };
    return totals;
}
function tipCpu(c) {
    const t = TRACKS[c.t];
    let h = '<div class="t-name">' + esc(trackLabel(t)) + '</div><div><span class="t-dur">' + fmtDur(c.e - c.s) +
        '</span>' + (CPU_HAS_SWITCHES ? ' recorded on CPU ' : ' observation span on CPU ') + c.cpu + '</div><div class="t-sep"></div>';
    h += '<div class="t-row"><span class="t-k">' + (CPU_HAS_SWITCHES ? 'start' : 'first observed') + '</span><span class="t-v">' + fmtDur(c.s) + (MODE === 'abs' ? ' (' + fmtAbs(c.s) + ')' : '') + '</span></div>';
    h += '<div class="t-row"><span class="t-k">' + (CPU_HAS_SWITCHES ? 'end' : 'last observed') + '</span><span class="t-v">' + fmtDur(c.e) + (MODE === 'abs' ? ' (' + fmtAbs(c.e) + ')' : '') + '</span></div>';
    if (!CPU_HAS_SWITCHES) h += '<div class="t-warn">No sched_switch records. This spans CPU-tagged observations, not continuous running time. Its edges are not task start/exit or main entry/return; CPU placement outside these observations is unknown.</div>';
    const totals = cpuIrqTimes(c);
    for (let context = 1; context < 4; context++) if (totals[context])
        h += '<div class="t-row"><span class="t-k">' + CTX_NAMES[context] + '</span><span class="t-v">' + fmtDur(totals[context]) + '</span></div>';
    return h + '<div class="t-warn">click to open this task</div>';
}
function revealTaskTrack(task) {
    if (!task.h || task.hidden || task.collapsed) {
        if (!task.h && !ORDER.includes(task)) ORDER.push(task);
        task.hidden = false;
        task.collapsed = false;
        layout();
    }
    elScroll.scrollTop = Math.max(0, task.y - 4);
}
function openTaskAt(tid, tm) {
    const task = TRACKS[tid];
    if (!task) return;
    for (let depth = task.rowsT.length - 1; depth >= 0; depth--) {
        const row = task.rowsT[depth], position = lbEnd(row, tm);
        if (position < row.length) {
            const index = row[position];
            if (index < N && sStart[index] <= tm && sEnd[index] > tm) {
                revealTaskTrack(task);
                select(index);
                return;
            }
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
let sliceTip = null;
function showSliceTip(index, dense, event) {
    if (hoverSourceRequest?.warning && fgData.sourceReady(hoverSourceRequest.source)) {
        hoverSourceRequest = null;
        sliceTip = null;
    }
    if (sliceTip?.index !== index || sliceTip.adjusted !== adjOn) {
        sliceTip = { index, adjusted: adjOn, html: tipSlice(index) };
    }
    showTip((dense ? tipDense(dense) : '') + sliceTip.html + (hoverSourceRequest?.warning || ''), event);
}
function loadCallSitePosition(index, source) {
    if (index !== hov || !hoverSourcePointer) return;
    if (hoverSourceRequest?.index === index && hoverSourceRequest.source === source) return;
    hoverSourceRequest?.controller.abort();
    const request = { index, source, controller: new AbortController() };
    hoverSourceRequest = request;
    fgData.loadSource(source, { signal: request.controller.signal }).then(() => {
        if (hoverSourceRequest !== request || hov !== index || !hoverSourcePointer || fgCurrentView() !== 'timeline') return;
        const { event, dense } = hoverSourcePointer;
        sliceTip = null;
        showSliceTip(index, dense, event);
    }).catch(error => {
        if (hoverSourceRequest !== request || error.name === 'AbortError' || hov !== index) return;
        request.warning = '<div class="t-warn">Source not loaded: ' + esc(error.message) + '. Select the call to retry.</div>';
        if (hoverSourcePointer) showSliceTip(index, hoverSourcePointer.dense, hoverSourcePointer.event);
    });
}
let tipHtml = null, tipSize = null, tipPointer = null, pendingHover = null;
let pendingHoverNeedsTargetCheck = false;
function showTip(html, ev) {
    if (tipHtml !== html) {
        elTipBody.innerHTML = html;
        tipHtml = html;
        tipSize = null;
    }
    if (elTip.style.display !== 'flex') elTip.style.display = 'flex';
    tipPointer = ev;
    positionTip(ev);
}
function positionTip(ev) {
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
    const edge = 4;
    if (!tipSize || tipSize.viewportWidth !== width || tipSize.viewportHeight !== height) {
        elTip.style.maxWidth = Math.max(1, Math.min(480, width - edge * 2)) + 'px';
        elTip.style.maxHeight = Math.max(1, height - edge * 2) + 'px';
        elTipMore.hidden = true;
        elTipMore.hidden = elTipBody.scrollHeight <= elTipBody.clientHeight &&
            elTipBody.scrollWidth <= elTipBody.clientWidth;
        const bounds = elTip.getBoundingClientRect();
        tipSize = { width: bounds.width, height: bounds.height, viewportWidth: width, viewportHeight: height };
    }
    let x = ev.clientX + 14, y = ev.clientY + 16;
    if (x + tipSize.width > left + width - edge) x = ev.clientX - tipSize.width - 14;
    if (y + tipSize.height > top + height - edge) y = ev.clientY - tipSize.height - 12;
    x = Math.max(left + edge, Math.min(x, left + width - tipSize.width - edge));
    y = Math.max(top + edge, Math.min(y, top + height - tipSize.height - edge));
    const transform = 'translate(' + x + 'px, ' + y + 'px)';
    if (elTip.style.transform !== transform) elTip.style.transform = transform;
}
function hideTip() {
    pendingHover = null;
    pendingHoverNeedsTargetCheck = false;
    elTip.style.display = 'none';
    tipHtml = null;
    tipSize = null;
    tipPointer = null;
    sliceTip = null;
    hoverSourceRequest?.controller.abort();
    hoverSourceRequest = null;
    hoverSourcePointer = null;
}
if (typeof ResizeObserver !== 'undefined') {
    const tipObserver = new ResizeObserver(() => {
        if (!tipPointer) return;
        tipSize = null;
        positionTip(tipPointer);
    });
    tipObserver.observe(elTip);
}
window.addEventListener('resize', resizeTimelineHover, { passive: true });
window.addEventListener('scroll', dismissTimelineHover, { passive: true });
window.visualViewport?.addEventListener('resize', resizeTimelineHover, { passive: true });
window.visualViewport?.addEventListener('scroll', dismissTimelineHover, { passive: true });
document.fonts?.addEventListener('loadingdone', () => {
    if (!tipPointer) return;
    tipSize = null;
    positionTip(tipPointer);
});

// ---- mouse ----
let drag = null;
const MOUSE_DRAG_SLOP = 3;
let rangePick = null;
let firstCanvasZoomHit = null, pendingCanvasZoomHit = null;
function rememberCanvasZoomHit(hit = null, clickCount = 0) {
    const previous = firstCanvasZoomHit;
    firstCanvasZoomHit = null;
    pendingCanvasZoomHit = null;
    if (!hit || (hit.slice < 0 && hit.kvm?.kind !== 'interval' && !hit.off)) return;
    if (clickCount === 1) firstCanvasZoomHit = hit;
    else if (clickCount === 2 && previous) {
        const same = hit.slice >= 0 ? hit.slice === previous.slice : hit.kvm?.kind === 'interval' ?
            previous.kvm?.kind === 'interval' && previous.kvm.index === hit.kvm.index : hit.off === previous.off;
        if (same) pendingCanvasZoomHit = hit;
    }
}
function paintedRange() { return drag?.rangePreview ?? range; }
function takeTimelineMouseDrag() {
    const gesture = drag;
    drag = null;
    if (gesture?.mode === 'pan' && gesture.moved) elCv.classList.remove('grab');
    return gesture;
}
function cancelTimelineMouseDrag() {
    rememberCanvasZoomHit();
    if (takeTimelineMouseDrag()?.rangePreview) draw();
}
function localXY(ev, el) { const b = el.getBoundingClientRect(); return [ev.clientX - b.left, ev.clientY - b.top]; }
function beginRangePick() {
    rangePick = { start: null, track: null };
    elRangeScope.value = 'all';
    elRangePickHint.textContent = 'Pick range start';
    elRangePick.hidden = false;
    elCv.setAttribute('aria-describedby', 'tlRangePickHint');
    elCv.dataset.rangePick = '';
    if (clearTimelineHover()) drawTracks();
    if (detailExpanded) setDetailExpanded(false);
    else resize();
    focusTimelineControl();
}
function cancelRangePick(restoreFocus = false, updateLayout = true) {
    if (!rangePick) return;
    rangePick = null;
    elRangeAnchor.hidden = true;
    elRangePick.hidden = true;
    elCv.removeAttribute('aria-describedby');
    delete elCv.dataset.rangePick;
    if (updateLayout) resize();
    if (restoreFocus) focusTimelineControl();
}
function pickRangePoint(horizontal, vertical) {
    const point = Math.max(0, Math.min(tMax, view.s + horizontal / W * (view.e - view.s)));
    if (rangePick.start === null) {
        const track = elRangeScope.value === 'track' ? rangeTrackAtY(vertical + elScroll.scrollTop) : null;
        if (elRangeScope.value === 'track' && !track) {
            elRangePickHint.textContent = 'Pick a task or CPU trace track';
            return;
        }
        rangePick = { start: point, track: track?.id ?? null };
        elRangePickHint.textContent = 'Pick range end';
        drawCursor();
        return;
    }
    if (point === rangePick.start) {
        elRangePickHint.textContent = 'Pick a different end';
        return;
    }
    range = { s: Math.min(rangePick.start, point), e: Math.max(rangePick.start, point), track: rangePick.track };
    cancelRangePick(true);
    requestRangeSummary();
}
elRangeScope.addEventListener('change', () => {
    if (!rangePick) return;
    rangePick = { start: null, track: null };
    elRangePickHint.textContent = 'Pick range start';
    elRangeAnchor.hidden = true;
});
elRangePick.querySelector('[data-range-pick-cancel]').addEventListener('click', () => cancelRangePick(true));
function activateCanvas(mx, my, clickCount = 0) {
    if (rangePick) { rememberCanvasZoomHit(); pickRangePoint(mx, my); return; }
    const hit = hitTest(mx, my);
    rememberCanvasZoomHit(hit, clickCount);
    if (hit.header && hit.track) { hit.track.collapsed = !hit.track.collapsed; layout(); draw(); return; }
    if (hit.cpuSeg) { openTaskAt(hit.cpuSeg.t, hit.tm); return; }
    if (hit.kvm) { showKvmDetail(hit.kvm); return; }
    if (hit.slice >= 0) {
        if (document.activeElement !== elCv) focusTimelineControl();
        let dense = null;
        if (hit.dense) {
            const pixelWidth = (view.e - view.s) / W;
            const start = view.s + Math.floor(mx) * pixelWidth;
            dense = { ...hit.dense, start, end: start + pixelWidth, filtered: !!(searchHits || textHits) };
        }
        if (showsSelectedSlice(hit.slice)) {
            updateDenseSelection(elDetail.firstElementChild, dense);
            return;
        }
        select(hit.slice, { canvasDetailOpen: elDetail.classList.contains('show'), dense });
    } else if (hit.off) { setSelection(-1); showWaitChain(hit.off, hit.denseOff); draw(); }
    else if (range) { range = null; hideDetail(); draw(); }
    else if (sel >= 0 || selKvm || elDetail.classList.contains('show')) select(-1);
}
elCv.addEventListener('mousedown', ev => {
    if (ev.button !== 0) return;
    if (drag) cancelTimelineMouseDrag();
    if (clearTimelineHover()) drawTracks();
    const [mx, my] = localXY(ev, elCv);
    const pickingByDrag = ev.shiftKey && !rangePick;
    const track = pickingByDrag && !ev.altKey ? rangeTrackAtY(my + elScroll.scrollTop) : null;
    drag = { mode: pickingByDrag ? 'range' : 'pan', x0: mx, start: view.s, span: view.e - view.s, moved: false,
        clientX: ev.clientX, clientY: ev.clientY, verticalMotion: false, track: track?.id ?? null };
    ev.preventDefault();
});
window.addEventListener('mousemove', ev => {
    if (window.fgCurrentView() !== 'timeline') return;
    if (drag && !(ev.buttons & 1)) cancelTimelineMouseDrag();
    if (drag) {
        if (!drag.moved) {
            if (Math.abs(ev.clientY - drag.clientY) > MOUSE_DRAG_SLOP) drag.verticalMotion = true;
            if (Math.abs(ev.clientX - drag.clientX) <= MOUSE_DRAG_SLOP) return;
            drag.moved = true;
            if (drag.mode === 'pan') elCv.classList.add('grab');
        }
        const [mx] = localXY(ev, drag.mode === 'ov' ? elHead : elCv);
        if (drag.mode === 'pan') {
            if (!panTo(drag.start - (mx - drag.x0) / W * drag.span, drag.span)) return;
        } else if (drag.mode === 'range') {
            const k = W / (view.e - view.s);
            const a = view.s + drag.x0 / k, b = view.s + mx / k;
            const visible = paintedRange();
            const repaintTracks = !visible || visible.track !== drag.track;
            drag.rangePreview = { s: Math.max(0, Math.min(a, b)), e: Math.min(tMax, Math.max(a, b)), track: drag.track };
            hideTip();
            if (repaintTracks) draw();
            else drawRuler();
            return;
        } else if (drag.mode === 'ov') {
            if (!panTo(ovT(mx) - drag.span / 2, drag.span)) return;
        }
        hideTip();
        draw();
        return;
    }
    if (ev.target !== elCv || rangePick) return;
    pendingHover = ev;
    pendingHoverNeedsTargetCheck = false;
    drawCursor();
});
function paintedHoverSlice(index, scale) {
    if (index < 0 || index >= N) return index;
    if (index === sel) return -1;
    const width = (sEnd[index] - view.s) * scale - (sStart[index] - view.s) * scale;
    return width < DENSE_SLICE_WIDTH ? -1 : index;
}
function additionalWakeHoverTask(index) {
    if (index < 0) return -1;
    const task = sTask[index];
    return (sel >= 0 && sTask[sel] === task) || range?.track === task ? -1 : task;
}
function hoverSliceChangesPaint(next, previous) {
    if (next === previous) return false;
    const scale = W / (view.e - view.s);
    return paintedHoverSlice(next, scale) !== paintedHoverSlice(previous, scale) ||
        (ov.wk && WAKES.length > 0 && additionalWakeHoverTask(next) !== additionalWakeHoverTask(previous));
}
function flushHover() {
    const event = pendingHover;
    const checkTarget = pendingHoverNeedsTargetCheck;
    pendingHover = null;
    pendingHoverNeedsTargetCheck = false;
    if (!event || drag || rangePick || window.fgCurrentView() !== 'timeline') return null;
    if (checkTarget && document.elementFromPoint(event.clientX, event.clientY) !== elCv) return null;
    const [horizontal, vertical] = localXY(event, elCv);
    cursorX = horizontal;
    const hit = hitTest(horizontal, vertical);
    const slice = hit.slice;
    if (slice < 0) sliceTip = null;
    hoverSourcePointer = { event, dense: hit.dense };
    if (hoverSourceRequest && hoverSourceRequest.index !== slice) {
        hoverSourceRequest.controller.abort(); hoverSourceRequest = null;
    }
    const nextCpu = hit.cpuSeg || null;
    const changed = hoverSliceChangesPaint(slice, hov) ||
        hit.off !== hovOff || nextCpu?.cpu !== hovCpu?.cpu || nextCpu?.j !== hovCpu?.j ||
        hit.kvm?.kind !== hovKvm?.kind || hit.kvm?.index !== hovKvm?.index;
    hov = slice; hovOff = hit.off;
    hovCpu = nextCpu;
    hovKvm = hit.kvm || null;
    elCv.classList.toggle('ptr', slice >= 0 || hit.header || !!hit.off || !!hovCpu || !!hovKvm);
    return {changed, event, hit};
}
function renderHoverTip({event, hit}) {
    if (hovKvm) showTip(tipKvm(hovKvm), event);
    else if (hovCpu) showTip(tipCpu(hovCpu), event);
    else if (hov >= 0) showSliceTip(hov, hit.dense, event);
    else if (hit.off) showTip(tipOff(hit.off) + (hit.denseOff ? '<div>' + denseOffMessage(hit.denseOff) + '</div>' : ''), event);
    else hideTip();
}
window.addEventListener('mouseup', ev => {
    if (ev.button !== 0) return;
    const gesture = takeTimelineMouseDrag();
    if (!gesture) { rememberCanvasZoomHit(); return; }
    if (gesture.mode === 'range') {
        rememberCanvasZoomHit();
        if (gesture.rangePreview) {
            range = gesture.rangePreview;
            requestRangeSummary();
        }
        else { if (rangeJob) hideDetail(); range = null; draw(); }
        return;
    }
    if (gesture.mode !== 'pan' || gesture.moved || gesture.verticalMotion || ev.target !== elCv ||
            Math.max(Math.abs(ev.clientX - gesture.clientX), Math.abs(ev.clientY - gesture.clientY)) > MOUSE_DRAG_SLOP) {
        rememberCanvasZoomHit();
        return;
    }
    activateCanvas(...localXY(ev, elCv), ev.detail);
});
function clearTimelineHover() {
    const changed = hoverSliceChangesPaint(-1, hov) || hovOff !== null || hovCpu !== null || hovKvm !== null;
    cursorX = -1; hov = -1; hovOff = null; hovCpu = null;
    hovKvm = null;
    elCv.classList.remove('ptr');
    elHoverCursor.hidden = true;
    hideTip();
    return changed;
}
function dismissTimelineHover() {
    if (window.fgCurrentView() === 'timeline' && clearTimelineHover()) drawTracks();
}
function resizeTimelineHover() {
    const pointer = pendingHover;
    dismissTimelineHover();
    if (pointer) {
        pendingHover = pointer;
        pendingHoverNeedsTargetCheck = true;
    }
}
elCv.addEventListener('mouseleave', dismissTimelineHover);
elCv.addEventListener('dblclick', () => {
    const hit = pendingCanvasZoomHit;
    rememberCanvasZoomHit();
    if (!hit || rangePick) return;
    if (hit.slice >= 0) select(hit.slice, { zoom: true });
    else if (hit.kvm?.kind === 'interval') {
        const interval = KVM_INTERVALS[hit.kvm.index];
        showKvmDetail(hit.kvm); zoomTo(interval[1], interval[2], -1);
    }
    else if (hit.off) zoomTo(hit.off[1], hit.off[2], -1);
});
function timelineWheelPixels(delta, mode, pageExtent) {
    return delta * (mode === WheelEvent.DOM_DELTA_LINE ? 24 : mode === WheelEvent.DOM_DELTA_PAGE ? pageExtent : 1);
}
function zoomWithTimelineWheel(event, canvas) {
    const mode = event.deltaMode;
    const pixels = timelineWheelPixels(event.deltaY, mode, H);
    if (!W || !pixels) return;
    event.preventDefault();
    zoomAt(Math.exp(pixels * 0.0022), event.clientX, canvas);
}
elCv.addEventListener('wheel', event => {
    if (event.ctrlKey || event.metaKey || event.altKey) { zoomWithTimelineWheel(event, elCv); return; }
    const mode = event.deltaMode;
    const { deltaX, deltaY } = event;
    if (!W || (!event.shiftKey && Math.abs(deltaX) <= Math.abs(deltaY))) return;
    const pixels = timelineWheelPixels(deltaX || deltaY, mode, W);
    if (!pixels) return;
    event.preventDefault();
    panBy(pixels / W * (view.e - view.s));
}, { passive: false });
elScroll.addEventListener('scroll', () => { clearTimelineHover(); drawTracks(); });

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
    pendingTouchTap = null;
    if (ev.pointerType !== 'touch' || !W) return;
    ev.preventDefault();
    elCv.setPointerCapture(ev.pointerId);
    const [x, y] = localXY(ev, elCv);
    touchPoints.set(ev.pointerId, { x, y });
    const span = view.e - view.s;
    if (touchPoints.size === 1) {
        touchGesture = { mode: 'tap', x, y, start: view.s, span };
        if (clearTimelineHover()) drawTracks();
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
    if (gesture.mode === 'pan' && panTo(gesture.start - dx / W * gesture.span, gesture.span)) draw();
});
let pendingTouchTap = null;
function cancelTimelineGestures() {
    cancelTimelineMouseDrag();
    pendingTouchTap = null;
    touchGesture = null;
    for (const pointerId of touchPoints.keys()) {
        touchPoints.delete(pointerId);
        if (elCv.hasPointerCapture(pointerId)) elCv.releasePointerCapture(pointerId);
    }
}
window.addEventListener('blur', cancelTimelineGestures);
document.addEventListener('visibilitychange', () => {
    if (document.hidden) cancelTimelineGestures();
});
function finishTouch(ev) {
    if (!touchPoints.delete(ev.pointerId)) return;
    const gesture = touchGesture;
    if (touchPoints.size) gesture.mode = 'blocked';
    else touchGesture = null;
    if (ev.type !== 'pointerup' || gesture.mode !== 'tap' || window.fgCurrentView() !== 'timeline') return;
    const [x, y] = localXY(ev, elCv);
    if (x >= 0 && x < W && y >= 0 && y < H &&
            Math.max(Math.abs(x - gesture.x), Math.abs(y - gesture.y)) < TOUCH_SLOP) {
        if (rangePick) activateCanvas(x, y);
        else pendingTouchTap = { pointerId: ev.pointerId, x, y };
    }
}
for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) elCv.addEventListener(event, finishTouch);
elCv.addEventListener('click', ev => {
    const tap = pendingTouchTap;
    pendingTouchTap = null;
    if (!tap || window.fgCurrentView() !== 'timeline') return;
    if (ev.pointerType ? ev.pointerType !== 'touch' || ev.pointerId !== tap.pointerId :
        !ev.detail && !ev.sourceCapabilities?.firesTouchEvents) return;
    activateCanvas(tap.x, tap.y, ev.detail);
});

elHead.addEventListener('mousedown', ev => {
    if (ev.button !== 0) return;
    const [mx, my] = localXY(ev, elHead);
    if (my > OV_H) return;
    if (drag) cancelTimelineMouseDrag();
    const hoverChanged = clearTimelineHover(), span = view.e - view.s;
    drag = { mode: 'ov', span, moved: true };
    if (panTo(ovT(mx) - span / 2, span)) draw();
    else if (hoverChanged) drawTracks();
    ev.preventDefault();
});
elHead.addEventListener('wheel', event => zoomWithTimelineWheel(event, elHead), { passive: false });

// ---- error navigation ----
let errList = [], errPos = -1;
function errorOrigin(code) { return code < 0 ? -code - 1 : CHAINS[code][0]; }
function errorTime(code) { return sStart[errorOrigin(code)]; }
function errorTieOrder(code) { return code < 0 ? CHAINS.length - code - 1 : code; }
function errorLowerBound(time, tieOrder) {
    let low = 0, high = errList.length;
    while (low < high) {
        const middle = (low + high) >>> 1, code = errList[middle], start = errorTime(code);
        if (start < time || (start === time && errorTieOrder(code) < tieOrder)) low = middle + 1;
        else high = middle;
    }
    return low;
}
function syncErrorPosition(index, code) {
    let position = errPos;
    if (code !== undefined) {
        position = errorLowerBound(sStart[index], errorTieOrder(code));
        if (errList[position] !== code) position = -1;
    } else if (position >= 0 && errorOrigin(errList[position]) !== index) position = -1;
    if (position !== errPos) {
        errPos = position;
        updateErrLabel();
    }
}
function rebuildErrList() {
    errList = [];
    for (let c = 0; c < CHAINS.length; c++) if (chainShown(c) && (!errFilter || CHAINS[c][3] === 1) && (!textHits || textHits[CHAINS[c][0]])) errList.push(c);
    if (!errFilter) for (const index of USER_ERRORS.keys()) if (!textHits || textHits[index]) errList.push(-index - 1);
    errList.sort((left, right) => errorTime(left) - errorTime(right) || errorTieOrder(left) - errorTieOrder(right));
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
    if (!errList.length) {
        const message = errFilter && USER_ERRORS.size ?
            'No kernel error chains reach user space in the current filter. Use all to include user API failure hints.' :
            !HAS_KERNEL ? 'No recognized user API failures in the current filter; returns or API conventions may be missing.' :
            D.caps.retval ? 'No error paths found' : 'Error paths need options/funcgraph-retval';
        toast(message);
        return;
    }
    if (errPos < 0) {
        const time = sel >= 0 ? sStart[sel] : view.s;
        const boundary = errorLowerBound(time, dir > 0 ? Infinity : -Infinity);
        errPos = (boundary + (dir < 0 ? errList.length - 1 : 0)) % errList.length;
    } else errPos = (errPos + dir + errList.length) % errList.length;
    const code = errList[errPos], origin = errorOrigin(code), top = code < 0 ? origin : CHAINS[code][1];
    // frame the whole chain, select the origin
    zoomTo(sStart[top], sEnd[top], origin);
    select(origin, { reveal: true });
    updateErrLabel();
}

// ---- search ----
let searchQuery = '', searchError = '', searchErrorKind = '', searchTimer = 0;
let searchPostingIndex = null, searchMatchQuery = null, searchNameHits = null;
let searchMatches = new Uint32Array(0), searchVisibleMatches = searchMatches;
let searchFilterBasis = null, searchWork = null, searchProgress = '', searchPaused = false;
let searchReadyQuery = '', searchComposing = false, searchWorkerUnavailable = false;
let searchRefreshStatus = null;
const searchNavButtons = [elSearchPrev, elSearchNext];
const searchDefaultTitle = 'Case-insensitive regular expression; highlights and navigates recorded function names. ' +
    'Enter jumps to the next match; Shift+Enter jumps to the previous match. Escape clears and cancels. ' +
    'Complex expressions use cancellable background search when available. ' +
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
function renderSearchStatus(message, value, refreshing) {
    if (refreshing) {
        if (!searchRefreshStatus) {
            searchRefreshStatus = document.createElement('span');
            searchRefreshStatus.className = 'tl-count-refresh';
            searchRefreshStatus.innerHTML = '<span class="tl-count-refresh-mark" aria-hidden="true"></span><span class="tl-count-refresh-text"></span>';
            elCount.append(searchRefreshStatus);
            elCount.setAttribute('data-refreshing', '');
        }
        const label = searchRefreshStatus.lastElementChild;
        if (label.textContent !== message) label.textContent = message;
        return;
    }
    if (searchRefreshStatus) {
        searchRefreshStatus.remove();
        searchRefreshStatus = null;
        elCount.removeAttribute('data-refreshing');
    }
    if (value === null) {
        if (elCount.firstElementChild || elCount.textContent !== message) elCount.textContent = message;
        return;
    }
    if (!elCount.firstElementChild)
        elCount.innerHTML = '<span class="tl-count-result"><span class="tl-count-value"></span> calls</span>';
    const digits = elCount.firstElementChild.firstElementChild;
    if (digits.textContent !== value) digits.textContent = value;
}
function searchInProgress() {
    return searchComposing || !!searchTimer || !!searchWork;
}
function renderSearchPosition() {
    const count = searchVisibleMatches.length, position = searchIndex(sel);
    const atMatch = position < count && searchVisibleMatches[position] === sel;
    renderSearchStatus('', (atMatch ? (position + 1) + ' / ' : '') + count, false);
}
function updateSearchSelection() {
    if (searchQuery && searchVisibleMatches.length && !searchInProgress() && !searchPaused && !searchError)
        renderSearchPosition();
}
function updateSearchLabel() {
    const busy = searchInProgress();
    const invalid = !busy && searchErrorKind === 'regex';
    elSearch.classList.toggle('bad', invalid);
    elSearch.setAttribute('aria-invalid', String(invalid));
    elSearch.title = !busy && searchError ? searchError : searchDefaultTitle +
        (searchWorkerUnavailable && searchNeedsWorker(searchQuery) ? ' Background search is unavailable; this expression uses the page thread and may block it.' : '');
    elCount.setAttribute('aria-busy', String(busy));
    elCount.title = searchComposing ? 'Finish text composition to search.' : busy ? searchProgress || 'Searching…' : searchError;
    const count = searchVisibleMatches.length;
    const refreshing = busy && !searchPaused && !elSearchNext.disabled;
    const disable = !refreshing && (busy || searchPaused || !!searchError || !count);
    if (disable && !searchPaused && (document.activeElement === elSearchPrev || document.activeElement === elSearchNext))
        elSearch.focus({ preventScroll: true });
    const tabIndex = refreshing ? -1 : 0;
    for (const button of searchNavButtons) {
        if (button.disabled !== disable) button.disabled = disable;
        if (button.tabIndex !== tabIndex) button.tabIndex = tabIndex;
        if (refreshing && !button.hasAttribute('aria-disabled')) button.setAttribute('aria-disabled', 'true');
        else if (!refreshing && button.hasAttribute('aria-disabled')) button.removeAttribute('aria-disabled');
    }
    let message = '';
    if (searchComposing) message = 'Composing…';
    else if (busy) message = searchProgress || 'Searching…';
    else if (searchPaused) message = 'Search paused';
    else if (invalid) message = 'Invalid regex';
    else if (searchError) message = 'Search unavailable';
    else if (searchQuery) {
        if (!count) elCount.title = textHits && searchMatches.length ?
            'The shared filter excludes all matching calls. Adjust or clear it in Tools → Filter calls.' :
            'No recorded function names match this expression. Try a different case-insensitive regular expression.';
        renderSearchPosition();
        return;
    }
    renderSearchStatus(message, null, refreshing);
}
function cancelSearchWork() {
    const work = searchWork;
    searchWork = null;
    if (work) { clearTimeout(work.timer); work.controller.abort(); work.steps.return(); }
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
    const keepSearch = compactToolbar.matches && innerHeight <= 480 && innerWidth > innerHeight;
    select(searchVisibleMatches[position], { reveal: true, keepSearch });
}
function* prepareSearchMatches(query, matcher, accepted, signal) {
    if (!searchPostingIndex) searchPostingIndex = yield* buildSearchIndex(sName, N, input.strings.length);
    if (searchMatchQuery !== query) {
        let hits = null;
        if (!matcher && !searchWorkerUnavailable) {
            const pending = matchSearchNamesWorker(searchPostingIndex, S, query, signal);
            if (pending) {
                try { hits = yield { pending, status: 'Searching function names…' }; }
                catch (error) {
                    if (error.searchKind !== 'unavailable') throw error;
                    searchWorkerUnavailable = true;
                }
            } else searchWorkerUnavailable = true;
        }
        if (!hits) {
            if (!matcher) {
                yield { pending: new Promise(resolve => setTimeout(resolve, 0)), status: 'Searching in page…' };
                try { matcher = new RegExp(query, 'i'); }
                catch (error) { error.searchKind = 'regex'; throw error; }
            }
            hits = yield* matchSearchNames(searchPostingIndex, S, matcher);
        }
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
    const controller = new AbortController();
    const work = { steps: prepareSearchMatches(query, matcher, textHits, controller.signal), accepted: textHits, controller, timer: 0, direction: dir };
    searchWork = work;
    function advance(value, rejected = false) {
        if (searchWork !== work) return;
        try {
            const deadline = performance.now() + 6;
            do {
                const result = rejected ? work.steps.throw(value) : work.steps.next(value);
                value = undefined;
                rejected = false;
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
                if (result.value?.pending) {
                    searchProgress = result.value.status;
                    updateSearchLabel();
                    result.value.pending.then(value => advance(value), error => advance(error, true));
                    return;
                }
                searchProgress = result.value;
            } while (performance.now() < deadline);
            updateSearchLabel();
            work.timer = setTimeout(advance, 0);
        } catch (error) {
            cancelSearchWork();
            searchErrorKind = error.searchKind === 'regex' ? 'regex' : 'work';
            searchError = searchErrorKind === 'regex' ? error.message : 'Could not prepare function search: ' + error.message + '. Press Enter to retry.';
            searchHits = null;
            searchVisibleMatches = new Uint32Array(0);
            updateSearchLabel();
            draw();
        }
    }
    advance();
}
function rebuildSearchMatches() {
    if (searchComposing || searchTimer || searchPaused || !searchQuery || searchErrorKind === 'regex') { updateSearchLabel(); return; }
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
    if (searchComposing) { updateSearchLabel(); return false; }
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
    try { if (!searchNeedsWorker(query)) matcher = new RegExp(query, 'i'); }
    catch (error) {
        searchError = error.message;
        searchErrorKind = 'regex';
        updateSearchLabel(); draw(); return false;
    }
    startSearchWork(query, matcher, dir);
    return !searchWork && !searchError;
}
function searchNext(dir) { applySearch(elSearch.value, dir); }
function scheduleSearchInput(event) {
    clearTimeout(searchTimer);
    searchTimer = 0;
    cancelSearchWork();
    searchPaused = window.fgCurrentView() !== 'timeline';
    if (searchComposing || event?.isComposing || searchPaused) { updateSearchLabel(); return; }
    const query = elSearch.value.trim();
    if (!query) { applySearch(''); return; }
    searchTimer = setTimeout(() => applySearch(query), searchNeedsWorker(query) ? 150 : 90);
    updateSearchLabel();
}
elSearch.addEventListener('input', scheduleSearchInput);
elSearch.addEventListener('compositionstart', () => {
    searchComposing = true;
    scheduleSearchInput();
});
elSearch.addEventListener('compositionend', () => {
    searchComposing = false;
    scheduleSearchInput();
});
function timelineShortcutBlocked(event) {
    return event.defaultPrevented || event.isComposing || event.keyCode === 229 ||
        event.ctrlKey || event.metaKey || event.altKey || event.target === elSearch && searchComposing;
}
elSearch.addEventListener('keydown', ev => {
    if (timelineShortcutBlocked(ev)) return;
    if (ev.key === 'Enter') { searchNext(ev.shiftKey ? -1 : 1); ev.preventDefault(); }
    else if (ev.key === 'Escape') {
        if (jumpTools.contains(elSearch) && elOptions.classList.contains('open')) {
            ev.preventDefault();
            setToolbarOptions(false, true);
        } else {
            elSearch.value = ''; applySearch(''); elSearch.blur();
        }
    }
    ev.stopPropagation();
});

let positionBasis = 'offset', positionReturnFocus = null;
function positionOrigin() {
    if (MODE !== 'abs' || MIXED && !D.uftrace.aligned) return null;
    const origin = D.uftrace?.origin_ns || D.kvm?.origin_ns;
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
const callNavigationKeys = new Map([['ArrowUp', 'parent'], ['ArrowDown', 'child'], ['[', 'previous'], [']', 'next']]);
window.addEventListener('keydown', ev => {
    if (window.fgCurrentView() !== 'timeline' || timelineShortcutBlocked(ev)) return;
    if (elHelp.classList.contains('show') && elHelp.contains(ev.target instanceof Node ? ev.target : document.activeElement)) return;
    if (savedViewsDialog?.open) { ev.stopPropagation(); return; }
    if (callActionsKey(ev)) { ev.stopPropagation(); return; }
    if (resizeDetailKey(ev)) { ev.stopPropagation(); return; }
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
    if (taskFiltersKey(ev)) { ev.stopPropagation(); ev.preventDefault(); return; }
    if (windowResultKey(ev)) { ev.stopPropagation(); return; }
    if (ev.key === 'Escape' && rangePick && !elHelp.classList.contains('show')) {
        ev.stopPropagation(); ev.preventDefault();
        cancelRangePick(true);
        return;
    }
    if (ev.key === 'Escape' && !elHelp.classList.contains('show') &&
            ev.target.closest?.('.tl-detail') === elDetail &&
            !ev.target.closest('input, textarea, select, [contenteditable]') && closeReadingDisclosure()) {
        ev.stopPropagation(); ev.preventDefault();
        return;
    }
    if (ev.key === 'Escape' && tlRoot.classList.contains('tl-fullscreen') && !elHelp.classList.contains('show')) {
        ev.stopPropagation(); ev.preventDefault();
        leaveFullscreen();
        return;
    }
    if (ev.target.matches && ev.target.matches('input, textarea, select')) return;
    const focused = ev.target instanceof Node ? ev.target : document.activeElement;
    if ((elOptions.contains(focused) || focused?.matches?.('.tl-source-code')) &&
            ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(ev.key)) {
        ev.stopPropagation();
        return;
    }
    ev.stopPropagation();
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.closest && ev.target.closest('button, a[href], summary')) return;
    const navigation = callNavigationKeys.get(ev.key);
    if (navigation) {
        ev.preventDefault();
        const fromDetailNav = !!focused?.closest?.('[data-call-nav]');
        navigateCall(navigation, fromDetailNav);
        if (!fromDetailNav) focusTimelineControl();
        return;
    }
    const span = view.e - view.s, cx = cursorX >= 0 ? cursorX : W / 2;
    let handled = true;
    switch (ev.key) {
        case 'w': case 'W': case '+': case '=': zoomAt(0.8, cx); break;
        case 's': case 'S': case '-': zoomAt(1.25, cx); break;
        case 'a': case 'A': case 'ArrowLeft': panBy(-span * 0.15); break;
        case 'd': case 'D': case 'ArrowRight': panBy(span * 0.15); break;
        case '0': case 'Home': fitAll(); break;
        case 'f': case 'F':
            if (sel >= 0) zoomTo(sStart[sel], sEnd[sel], sel);
            else if (selKvm?.kind === 'interval') {
                const record = KVM_INTERVALS[selKvm.index]; zoomTo(record[1], record[2], -1);
            } else if (range) zoomTo(range.s, range.e, -1);
            break;
        case 'e': gotoErr(1); break;
        case 'E': gotoErr(-1); break;
        case 'n': searchNext(1); break;
        case 'N': searchNext(-1); break;
        case 'Enter':
            if (rangePick && focused === elCv) pickRangePoint(W / 2, H / 2);
            else if (sel >= 0) {
                const exit = sEline?.[sel];
                if (focused !== elCv || !exit || !window.fgReturnToTextOrigin(exit))
                    gotoText(sLine[sel], focused === elCv ? elCv : null);
            }
            break;
        case '/':
            if (jumpTools.contains(elSearch) && !elOptions.classList.contains('open')) setToolbarOptions(true);
            elSearch.focus(); elSearch.select(); break;
        case 'g': case 'G': showPosition(); break;
        case 'h': case 'H': if (sel >= 0) showDist(sName[sel]); break;
        case 'o': case 'O':
            toggleAdjusted();
            if (elDetail.contains(document.activeElement)) {
                ev.preventDefault();
                return;
            }
            break;
        case 'i': case 'I': toggleInlineView(); break;
        case 'm': case 'M': toggleFullscreen(); break;
        case '?': setToolbarOptions(false); showKeyboardHint(); break;
        case 'Escape':
            if (elHelp.classList.contains('show')) closeTimelineHelp();
            else { range = null; select(-1); }
            break;
        default: handled = false;
    }
    if (handled) {
        ev.preventDefault();
        if (ev.key !== '/' && ev.key !== '?' && ev.key.toLowerCase() !== 'g' && window.fgCurrentView() === 'timeline') focusTimelineControl();
    }
}, true);

const CALL_NAV_SCAN_LIMIT = 256;
function nearbyCallSibling(index, step) {
    const track = TRACKS[sTask[index]], parent = sParent[index];
    if (parent >= 0 && (parent < track.lo || parent >= track.hi || sTask[parent] !== track.id || sDepth[parent] >= sDepth[index])) return -1;
    let candidate = index + step;
    for (let scanned = 0; scanned < CALL_NAV_SCAN_LIMIT && candidate >= track.lo && candidate < track.hi; scanned++, candidate += step) {
        if (sTask[candidate] !== track.id) continue;
        if (parent < 0 ? sParent[candidate] < 0 : sParent[candidate] === parent && sDepth[parent] < sDepth[candidate]) return candidate;
    }
    return candidate < track.lo || candidate >= track.hi ? -1 : null;
}
function nearbyCallChild(index) {
    if (sFlags[index] & FL.LEAF) return -1;
    const track = TRACKS[sTask[index]];
    const start = track.orderedParents ? index + 1 : track.lo;
    const end = Math.min(track.hi, start + CALL_NAV_SCAN_LIMIT);
    const depth = sDepth[index];
    for (let candidate = start; candidate < end; candidate++) {
        if (sParent[candidate] === index && sTask[candidate] === track.id && sDepth[candidate] > depth) return candidate;
    }
    return end === track.hi ? -1 : null;
}
function callTarget(index, action) {
    if (index < 0 || index >= NA) return -1;
    if (action === 'parent') {
        const parent = sParent[index];
        return parent >= 0 && parent < N && sTask[parent] === sTask[index] &&
            (isSyn(index) || sDepth[parent] < sDepth[index]) ? parent : -1;
    }
    if (isSyn(index)) return action === 'child' ? SYN[index - N].kids[0] ?? -1 : -1;
    if (action !== 'child' && action !== 'previous' && action !== 'next') return -1;
    const tree = callTreeFor(index);
    if (!tree) {
        return action === 'child' ? nearbyCallChild(index) : nearbyCallSibling(index, action === 'previous' ? -1 : 1);
    }
    const position = index - tree.offset;
    return action === 'child' ? tree.first[position] : action === 'previous' ? tree.previous[position] : action === 'next' ? tree.next[position] : -1;
}
const reverseCallNavigation = { parent: 'child', child: 'parent', previous: 'next', next: 'previous' };
function navigateCall(action, focusControl) {
    const target = callTarget(sel, action);
    if (target === null) {
        toast('Preparing recorded call navigation…');
        return;
    }
    if (target < 0) return;
    select(target, { reveal: true });
    const fromActions = focusControl === 'actions';
    const button = fromActions ? elDetail.querySelector('[data-call-actions]') : focusControl ?
        elDetail.querySelector('[data-call-nav="' + action + '"]:enabled') ||
        elDetail.querySelector('[data-call-nav="' + reverseCallNavigation[action] + '"]:enabled') : null;
    resetDetailScroll(fromActions ? null : button);
    if (button) button.focus({ preventScroll: true });
    else if (focusControl) focusTimelineControl();
}
