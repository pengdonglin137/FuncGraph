/* FuncGraph Timeline: Host KVM tracepoint windows and original event evidence. */
const KVM_PAGE = 50;
const kvmView = { item: null, page: 0, task: null, first: 0, end: 0 };
const kvmReasons = new Map();
function kvmReason(eventIndex) {
    if (eventIndex < 0) return '';
    if (!kvmReasons.has(eventIndex)) {
        const event = KVM_EVENTS[eventIndex];
        const match = S[event[5]].match(/\breason(?:\s*[=:]\s*|\s+)([\w-]+)/);
        kvmReasons.set(eventIndex, match ? match[1] : '');
    }
    return kvmReasons.get(eventIndex);
}
function kvmWindowTitle(index) {
    const record = KVM_INTERVALS[index], reason = kvmReason(record[3] ? record[5] : record[6]);
    return (record[3] ? 'Host' : 'Guest') + ' window' + (reason ? ' · ' + reason : '');
}
function firstKvmEventAt(track, timestamp) {
    let low = 0, high = track.kvmEvents.length;
    while (low < high) {
        const middle = (low + high) >>> 1;
        if (KVM_EVENTS[track.kvmEvents[middle]][1] < timestamp) low = middle + 1;
        else high = middle;
    }
    return low;
}
function firstKvmIntervalEndingAfter(track, timestamp) {
    let low = 0, high = track.kvm.length;
    while (low < high) {
        const middle = (low + high) >>> 1;
        if (KVM_INTERVALS[track.kvm[middle]][2] <= timestamp) low = middle + 1;
        else high = middle;
    }
    return low;
}
function hitKvm(track, timestamp, scale, eventBand) {
    const position = firstKvmEventAt(track, timestamp);
    let nearest = -1, distance = 4;
    for (const candidate of [position - 1, position]) {
        if (candidate < 0 || candidate >= track.kvmEvents.length) continue;
        const index = track.kvmEvents[candidate], delta = Math.abs(KVM_EVENTS[index][1] - timestamp) * scale;
        if (delta < distance) { nearest = index; distance = delta; }
    }
    if (eventBand && nearest >= 0) return { kind: 'event', index: nearest };
    const offset = firstKvmIntervalEndingAfter(track, timestamp);
    if (offset < track.kvm.length) {
        const index = track.kvm[offset], record = KVM_INTERVALS[index];
        if (record[1] <= timestamp && record[2] > timestamp) return { kind: 'interval', index };
    }
    return nearest >= 0 ? { kind: 'event', index: nearest } : null;
}
function kvmIs(item, kind, index) { return item?.kind === kind && item.index === index; }
function kvmCoordinates(item) {
    const record = item.kind === 'interval' ? KVM_INTERVALS[item.index] : KVM_EVENTS[item.index];
    const track = TRACKS[record[0]], box = elCv.getBoundingClientRect();
    const time = item.kind === 'interval' ? (record[1] + record[2]) / 2 : record[1];
    return { x: box.left + Math.max(1, Math.min(W - 1, (time - view.s) * W / (view.e - view.s))),
        y: box.top + track.y + HDR + (track.collapsed ? MINI / 2 : (track.maxD + 1.5) * ROW) - elScroll.scrollTop };
}
function drawKvmTrack(track, top, height, start, end, scale) {
    if (top > H || top + height < 0) return;
    let previousPixel = -Infinity;
    for (let offset = firstKvmIntervalEndingAfter(track, start); offset < track.kvm.length; offset++) {
        const index = track.kvm[offset], record = KVM_INTERVALS[index];
        if (record[1] > end) break;
        const left = Math.max(0, (record[1] - start) * scale), right = Math.min(W, (record[2] - start) * scale);
        const width = Math.max(1, right - left), pixel = Math.floor(left);
        const selected = kvmIs(selKvm, 'interval', index), hovered = kvmIs(hovKvm, 'interval', index);
        if (width < 1.5 && pixel === previousPixel && !selected && !hovered) continue;
        previousPixel = pixel;
        ctx.fillStyle = dark ? record[3] ? '#3c4860' : '#34594f' : record[3] ? '#dce3ee' : '#d5e9e1';
        ctx.fillRect(left, top, width, height);
        if (record[7]) {
            ctx.strokeStyle = P.sub; ctx.setLineDash([2, 2]);
            ctx.beginPath(); ctx.moveTo(right - 0.5, top); ctx.lineTo(right - 0.5, top + height); ctx.stroke();
            ctx.setLineDash([]);
        }
        if (width > 60 && height > MINI) {
            const label = kvmWindowTitle(index) + (record[7] ? ' · open end' : '');
            const characters = Math.max(1, Math.floor((width - 8) / charW));
            ctx.fillStyle = P.text; ctx.textBaseline = 'middle';
            ctx.fillText(label.length > characters ? label.slice(0, characters - 1) + '…' : label, left + 4, top + height / 2);
        }
        if (selected || hovered) {
            ctx.strokeStyle = selected ? P.sel : P.hov; ctx.lineWidth = 1;
            ctx.strokeRect(left + 0.5, top + 0.5, Math.max(0, width - 1), height - 1);
        }
    }
    ctx.fillStyle = P.sub;
    for (let position = firstKvmEventAt(track, start); position < track.kvmEvents.length;) {
        const event = KVM_EVENTS[track.kvmEvents[position]];
        if (event[1] > end) break;
        const pixel = Math.floor((event[1] - start) * scale);
        ctx.fillRect(pixel, top, 1, 2);
        position = Math.max(position + 1, firstKvmEventAt(track, start + (pixel + 1) / scale));
    }
    for (const item of [selKvm, hovKvm]) if (item?.kind === 'event') {
        const event = KVM_EVENTS[item.index];
        if (event[0] !== track.id || event[1] < start || event[1] > end) continue;
        ctx.fillStyle = item === selKvm ? P.sel : P.hov;
        ctx.fillRect(Math.floor((event[1] - start) * scale), top, 2, height);
    }
}
function tipKvm(item) {
    if (item.kind === 'event') {
        const event = KVM_EVENTS[item.index];
        return '<div class="t-name">' + esc(S[event[4]]) + '</div><div class="t-v">' + esc(S[event[5]]) +
            '</div><div>Host TID ' + (TRACKS[event[0]].pid ?? 'not recorded') + ' · ' +
            (event[2] >= 0 ? 'CPU ' + event[2] : 'CPU not recorded') + ' · +' + fmtDur(event[1]) +
            '</div><div class="t-warn">Recorded KVM event · click for original evidence.</div>';
    }
    const record = KVM_INTERVALS[item.index];
    return '<div class="t-name">' + esc(kvmWindowTitle(item.index)) + '</div><div>' +
        (record[4] >= 0 ? 'vCPU ' + record[4] + ' · ' : '') + 'Host TID ' + TRACKS[record[0]].pid +
        ' · ' + fmtDur(record[2] - record[1]) + (record[7] ? ' observed extent · end not recorded' : ' wall time') +
        '</div><div class="t-warn">Boundary interval, not a function or CPU time. May include scheduling waits.' +
        (record[3] ? ' Not necessarily a return to QEMU userspace.' : ' Guest internal calls are not shown.') + '</div>';
}
function kvmLine(line) {
    return D.no_text ? 'line ' + line : '<button type="button" class="tl-link" data-line="' + line + '">line ' + line + '</button>';
}
function kvmTimestamp(timestamp) { return String(BigInt(KVM.origin_ns) + BigInt(timestamp)); }
function showKvmDetail(item, page = 0) {
    setSelection(-1);
    selKvm = item; range = null; AN.kind = 'kvm';
    const record = item.kind === 'interval' ? KVM_INTERVALS[item.index] : null;
    const event = KVM_EVENTS[record ? record[5] : item.index], task = TRACKS[event[0]];
    const first = record ? KVM_EVENT_POS[record[5]] : firstKvmEventAt(task, event[1]);
    const end = record ? record[6] >= 0 ? KVM_EVENT_POS[record[6]] + 1 : task.kvmEvents.length : firstKvmEventAt(task, event[1] + 1);
    kvmView.item = item; kvmView.task = task; kvmView.first = first; kvmView.end = end;
    kvmView.page = Math.min(Math.max(0, page), Math.max(0, Math.ceil((end - first) / KVM_PAGE) - 1));
    const title = record ? kvmWindowTitle(item.index) : S[event[4]];
    let html = '<div class="tl-detail-head tl-slice-head tl-kvm-head"><span class="tl-detail-title">' + esc(title) + '</span>' +
        pill('mute', record ? 'KVM boundary' : 'Recorded KVM event') +
        (record?.[7] ? pill('warn', 'end not recorded') : '') + '<span class="sp"></span>' +
        '<button class="tl-btn" data-kvm-export>Export events CSV</button><button class="tl-close" title="Close (Esc)">×</button></div>';
    if (record) {
        const position = KVM_INTERVAL_POS[item.index];
        html += '<div class="tl-call-nav" role="group" aria-label="KVM window navigation">' +
            '<button class="tl-btn" data-kvm-step="-1"' + (position ? '' : ' disabled') + '>Previous window</button>' +
            '<button class="tl-btn" data-kvm-step="1"' + (position + 1 < task.kvm.length ? '' : ' disabled') + '>Next window</button>' +
            '<button class="tl-btn" data-kvm-fit>Fit window</button></div>';
    }
    html += '<div class="tl-detail-body tl-kvm-facts"><div class="tl-kv">';
    if (record) html += kv(record[7] ? 'observed extent' : 'wall duration', fmtDur(record[2] - record[1])) +
        kv('boundaries', (record[3] ? 'kvm_exit → ' : 'kvm_entry → ') +
            (record[7] ? 'end not recorded' : record[3] ? 'kvm_entry' : 'kvm_exit'));
    html += kv('Host thread', task.pid === null ? 'not recorded · CPU-scoped event' : esc(task.comm) + ' · TID ' + task.pid) +
        kv('vCPU', event[6] >= 0 ? String(event[6]) : record && record[4] >= 0 ? String(record[4]) : 'not recorded') +
        kv('start', '+' + fmtDur(event[1]) + ' · ' + kvmTimestamp(event[1]) + ' ns') +
        kv('Host CPU', event[2] >= 0 ? String(event[2]) : 'not recorded') + kv('clock', esc(KVM.clock));
    if (record && record[6] >= 0) html += kv('end', '+' + fmtDur(record[2]) + ' · ' + kvmTimestamp(record[2]) + ' ns');
    const reason = record ? kvmReason(record[3] ? record[5] : record[6]) : kvmReason(item.index);
    if (reason) html += kv('exit reason', esc(reason));
    html += '</div><div class="tl-context-note">' + (record ?
        'Guest / Host windows are derived from adjacent boundary events in this Host thread. They are not function calls, exact guest CPU time, or proof of QEMU userspace execution. Wall time can include preemption and sleep. ' +
        (record[7] ? 'The final boundary is missing; the dotted extent stops at the last observed trace timestamp, not a measured completion. ' : '') : 'This is an original tracepoint, not a reconstructed function call. ') +
        'Guest source and internal call stacks require a separate Guest recording and verified clock synchronization. Event tracks are not filtered by the function-call filter.</div></div>';
    const start = first + kvmView.page * KVM_PAGE, stop = Math.min(end, start + KVM_PAGE);
    html += '<div class="tl-process-tools"><span role="status" aria-live="polite">Events ' + (start - first + 1) + '–' + (stop - first) + ' of ' + (end - first) +
        '</span><span><button class="tl-btn" data-kvm-page="-1"' + (start > first ? '' : ' disabled') + '>Previous page</button> ' +
        '<button class="tl-btn" data-kvm-page="1"' + (stop < end ? '' : ' disabled') + '>Next page</button></span></div>';
    html += '<table class="tl-process-table tl-kvm-table"><thead><tr><th scope="col">Offset / CPU</th><th scope="col">Original event and payload</th><th scope="col">Trace line</th></tr></thead><tbody>';
    for (let position = start; position < stop; position++) {
        const source = KVM_EVENTS[task.kvmEvents[position]];
        html += '<tr><td>+' + fmtDur(source[1]) + '<br>' + (source[2] >= 0 ? 'CPU ' + source[2] : 'CPU not recorded') + '</td><td><strong>' + esc(S[source[4]]) +
            '</strong><div class="tl-kvm-payload">' + esc(S[source[5]]) + '</div>' +
            (source[7] & FL.LAT ? '<div class="tl-context-note">Flags: ' + esc(ctxFlagText(source[7]).replace(/^ · /, '') || 'none') + '</div>' : '') +
            '</td><td>' + kvmLine(source[3]) + '</td></tr>';
    }
    html += '</tbody></table>';
    openDetail(html); draw();
}
function exportKvmEvents() {
    function* rows() {
        for (let position = kvmView.first; position < kvmView.end; position++) {
            const index = kvmView.task.kvmEvents[position], event = KVM_EVENTS[index];
            yield [index, event[0], kvmView.task.pid, kvmView.task.comm, event[6] >= 0 ? event[6] : '',
                event[1], kvmTimestamp(event[1]), event[2] >= 0 ? event[2] : '', S[event[4]], S[event[5]], event[3], event[7], KVM.clock];
        }
    }
    downloadTimelineCsv('funcgraph-kvm-events.csv', ['event_index', 'task_index', 'host_tid', 'comm', 'vcpu',
        'trace_time_ns', 'timestamp_ns', 'host_cpu', 'event', 'payload', 'trace_line', 'flags', 'clock'], rows());
}
elDetail.addEventListener('click', event => {
    if (AN.kind !== 'kvm' || !kvmView.item) return;
    const page = event.target.closest('[data-kvm-page]'), step = event.target.closest('[data-kvm-step]');
    if (page) showKvmDetail(kvmView.item, kvmView.page + Number(page.dataset.kvmPage));
    if (step) {
        const position = KVM_INTERVAL_POS[kvmView.item.index] + Number(step.dataset.kvmStep);
        const index = kvmView.task.kvm[position];
        if (index !== undefined) {
            showKvmDetail({ kind: 'interval', index });
            const record = KVM_INTERVALS[index];
            if (record[2] < view.s || record[1] > view.e) zoomTo(record[1], record[2], -1);
        }
    }
    if (event.target.closest('[data-kvm-fit]') && kvmView.item.kind === 'interval') {
        const record = KVM_INTERVALS[kvmView.item.index]; zoomTo(record[1], record[2], -1);
    }
    if (event.target.closest('[data-kvm-export]')) exportKvmEvents();
});
