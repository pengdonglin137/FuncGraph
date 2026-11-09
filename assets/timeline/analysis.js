/* FuncGraph Timeline: analysis panels (error summary, duration distribution, scheduling latency).
 * Shares the __tlBoot() scope; rendered into the detail area via openDetail(). */
const AN = { kind: null, func: -1, off: null, errSort: 'count', latSort: 'dur' };

function matchOk(i) { return !textHits || textHits[i]; }
const TASK_PAGE = 100;
const TASK_KINDS = ['task', 'idle', 'cpu_fallback', 'all_fallback'];
const taskView = { query: '', kind: 'all', matching: false, only: null, sort: 'calls' };
let taskFacts = null, taskMatches = null, taskOrderCache = null;
function taskData() {
    if (!taskFacts) {
        taskFacts = TRACKS.map(task => ({ task, calls: 0, errors: 0, open: 0, start: null, end: null,
            cpus: new Set(task.cpus), search: '', cpuLabel: '' }));
        const observe = (id, start, end, cpu) => {
            const fact = taskFacts[id];
            if (!fact) return;
            for (const value of [start, end]) if (Number.isFinite(value)) {
                fact.start = fact.start === null ? value : Math.min(fact.start, value);
                fact.end = fact.end === null ? value : Math.max(fact.end, value);
            }
            if (Number.isInteger(cpu) && cpu >= 0) fact.cpus.add(cpu);
        };
        for (let index = 0; index < N; index++) {
            taskFacts[sTask[index]].calls++;
            observe(sTask[index], sStart[index], sEnd[index], sCpu ? sCpu[index] : null);
        }
        for (const task of TRACKS) {
            observe(task.id, null, null, task.cpu);
            for (const run of task.runs) observe(task.id, run[0], run[1], run[2]);
        }
        for (const record of D.offcpu) {
            observe(record[0], record[1], record[2], record[6]);
            if (OPEN_OFF.has(record)) taskFacts[record[0]].open++;
            else observe(record[0], null, null, record[7]);
        }
        for (const records of [D.resched, D.irqoff]) for (const record of records) observe(record[0], record[1], record[2], null);
        for (const section of D.sections || []) observe(section[0], section[2], section[3], null);
        for (const wake of WAKES) { observe(wake[0], wake[1], wake[1], null); observe(wake[2], wake[1], wake[1], null); }
        for (const chain of CHAINS) if (chain[0] >= 0 && chain[0] < N) taskFacts[sTask[chain[0]]].errors++;
        for (const fact of taskFacts) {
            const cpus = Array.from(fact.cpus).sort((left, right) => left - right);
            fact.cpuLabel = cpus.join(',');
            fact.search = [trackLabel(fact.task), ...fact.task.comms, ...cpus.map(cpu => 'CPU ' + cpu)].join(' ').toLowerCase();
        }
    }
    if (!taskMatches || taskMatches.hits !== textHits) {
        const counts = new Uint32Array(TRACKS.length);
        if (textHits) {
            for (let index = 0; index < N; index++) if (textHits[index]) counts[sTask[index]]++;
        } else for (const fact of taskFacts) counts[fact.task.id] = fact.calls;
        taskMatches = { hits: textHits, counts };
    }
    return taskMatches;
}
function taskOrder() {
    const data = taskData(), key = JSON.stringify(taskView);
    if (taskOrderCache && taskOrderCache.data === data && taskOrderCache.key === key) return taskOrderCache;
    const query = taskView.query.trim().toLowerCase();
    const rows = taskFacts.filter(fact => (taskView.only === null || fact.task.id === taskView.only) &&
        (taskView.kind === 'all' || fact.task.kind === +taskView.kind) && (!taskView.matching || data.counts[fact.task.id] > 0) &&
        (!query || fact.search.includes(query)));
    const value = fact => {
        switch (taskView.sort) {
            case 'name': return trackLabel(fact.task);
            case 'matches': return -data.counts[fact.task.id];
            case 'errors': return -fact.errors;
            case 'runs': return -fact.task.runs.length;
            case 'span': return fact.start === null ? Infinity : -(fact.end - fact.start);
            default: return -fact.calls;
        }
    };
    rows.sort((left, right) => {
        const first = value(left), second = value(right);
        return (typeof first === 'string' ? first.localeCompare(second) : first - second) || left.task.id - right.task.id;
    });
    taskOrderCache = { data, key, rows, visible: Math.min(TASK_PAGE, rows.length) };
    return taskOrderCache;
}
function taskRows(rows) {
    return rows.map(fact => {
        const task = fact.task, label = trackLabel(task), aliases = task.comms.join(', ');
        return '<tr data-task-row="' + task.id + '"><td><span class="an-task-name" title="' + esc(label + (aliases ? ' · names: ' + aliases : '')) + '">' +
            esc(label) + '</span></td><td>' + esc(TASK_KINDS[task.kind] || 'unknown') + '</td><td><span class="an-task-name" title="' +
            esc(fact.cpuLabel) + '">' + esc(fact.cpuLabel || '—') + '</span></td><td>' + fact.calls + '</td><td>' + taskMatches.counts[task.id] +
            '</td><td>' + fact.errors + '</td><td>' + task.runs.length + '</td><td title="' + (fact.open ? 'Includes open waits to the report boundary; ' : '') +
            'span includes gaps, not CPU time">' + (fact.start === null ? '—' : (fact.open ? '≥ ' : '') + fmtDur(fact.end - fact.start)) +
            '</td><td><button class="tl-btn" data-task-show="' + task.id + '"' + (fact.start === null ? ' disabled' : '') +
            ' aria-label="Show ' + esc(label) + '">Show</button></td></tr>';
    }).join('');
}
function updateTaskFooter(order) {
    elDetail.querySelector('.an-task-footer [role=status]').textContent = 'Showing ' + order.visible + ' of ' + order.rows.length + ' tasks';
    elDetail.querySelector('[data-act=tasks-more]').disabled = order.visible >= order.rows.length;
    elDetail.querySelector('[data-act=tasks-csv]').disabled = !order.rows.length;
}
function appendTaskRows() {
    const order = taskOrder(), start = order.visible;
    order.visible = Math.min(start + TASK_PAGE, order.rows.length);
    elDetail.querySelector('.an-task-table tbody').insertAdjacentHTML('beforeend', taskRows(order.rows.slice(start, order.visible)));
    updateTaskFooter(order);
}
function exportTasksCsv() {
    const order = taskOrder(), unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const columns = ['task_index', 'task', 'pid', 'kind', 'names', 'cpus', 'calls', 'matched_calls', 'error_origins_including_minor',
        'recorded_runs', 'open_waits', 'start_' + unit, 'end_' + unit, 'span_' + unit, 'span_is_lower_bound', 'mode', 'position_basis',
        'span_basis', 'query', 'kind_filter', 'matching_only', 'task_scope', 'shared_filter'];
    function* rows() {
        for (const fact of order.rows) {
            const task = fact.task;
            yield [task.id, task.comm, task.pid, TASK_KINDS[task.kind] || 'unknown', task.comms.join('; '), fact.cpuLabel,
                fact.calls, order.data.counts[task.id], fact.errors, task.runs.length, fact.open,
                fact.start === null ? '' : fact.start / scale, fact.end === null ? '' : fact.end / scale,
                fact.start === null ? '' : (fact.end - fact.start) / scale, fact.open ? 1 : 0, MODE,
                MODE === 'count' ? 'call_order' : TIMED ? 'recorded_time' : 'packed_duration', 'record_extent_including_gaps_not_cpu_time',
                taskView.query, taskView.kind, taskView.matching ? 1 : 0, taskView.only === null ? 'all' : taskView.only,
                window.fgFilterState && window.fgFilterState.text || ''];
        }
    }
    downloadTimelineCsv('funcgraph-tasks.csv', columns, rows());
}
function navigateTask(id) {
    taskData();
    const fact = taskFacts[id];
    if (!fact || fact.start === null) return;
    setSelection(-1);
    range = null;
    revealTaskTrack(fact.task);
    zoomTo(fact.start, fact.end, -1);
}
function showTasks(only) {
    if (only !== undefined) {
        taskView.only = only;
        if (only !== null) { taskView.query = ''; taskView.kind = 'all'; taskView.matching = false; }
    }
    const reuse = AN.kind === 'tasks' && elDetail.classList.contains('show') && elDetail.querySelector('.an-task-table');
    AN.kind = 'tasks';
    const order = taskOrder();
    if (!reuse) {
        openDetail('<div class="tl-detail-head an-task-head"><span class="tl-detail-title">Tasks</span>' +
            '<span class="an-task-scope"></span><span class="an-task-actions"><button class="tl-btn" data-act="tasks-csv">Export CSV</button>' +
            '<button class="tl-close" title="Close (Esc)">×</button></span></div>' +
            '<div class="an-task-tools"><label>Find <input data-task-query type="search" placeholder="Name, old name, PID or CPU" spellcheck="false"></label>' +
            '<label>Kind <select data-task-kind><option value="all">All</option>' + TASK_KINDS.map((name, kind) =>
                '<option value="' + kind + '">' + name + '</option>').join('') + '</select></label>' +
            '<label><input type="checkbox" data-task-matching>Matching calls only</label><button class="tl-btn" data-act="tasks-reset">Reset</button></div>' +
            '<table class="tl-sum an-task-table"></table><div class="an-task-footer"><span role="status" aria-live="polite"></span>' +
            '<button class="tl-btn" data-act="tasks-more">Load more</button></div>' +
            '<div class="an-hint">All recorded task identities, including scheduler-only tasks. Calls and errors exclude synthetic frames; errors include minor origins. ' +
            'The shared filter changes matches, not raw counts. Trace span includes gaps: not CPU time, running duration or task lifetime. ' +
            'Open waits extend to the report boundary (≥, lower bound), not a known resume. ' +
            (TIMED ? 'Positions use recorded time.' : MODE === 'count' ? 'Positions use call-order units, not elapsed time.' : 'Positions use packed durations; timing gaps are unknown.') +
            ' Show fits the task extent; CPU clicks preserve the current time range. Empty spans have no recorded time boundary. CSV includes unloaded rows.</div>');
        elDetail.querySelector('[data-task-query]').addEventListener('input', event => {
            taskView.query = event.target.value; taskView.only = null; showTasks();
        });
        elDetail.querySelector('[data-task-kind]').addEventListener('change', event => {
            taskView.kind = event.target.value; taskView.only = null; showTasks();
        });
        elDetail.querySelector('[data-task-matching]').addEventListener('change', event => {
            taskView.matching = event.target.checked; taskView.only = null; showTasks();
        });
        elDetail.querySelector('[data-act=tasks-reset]').onclick = () => {
            Object.assign(taskView, { query: '', kind: 'all', matching: false, only: null, sort: 'calls' }); showTasks();
        };
        elDetail.querySelector('[data-act=tasks-more]').onclick = appendTaskRows;
        elDetail.querySelector('[data-act=tasks-csv]').onclick = exportTasksCsv;
        elDetail.querySelector('.an-task-table').onclick = event => {
            const sort = event.target.closest('[data-task-sort]'), show = event.target.closest('[data-task-show]');
            if (sort) {
                taskView.sort = sort.dataset.taskSort; showTasks();
                elDetail.querySelector('[data-task-sort="' + taskView.sort + '"]').focus();
            } else if (show) navigateTask(+show.dataset.taskShow);
        };
        elDetail.scrollTop = 0;
    }
    elDetail.querySelector('[data-task-query]').value = taskView.query;
    elDetail.querySelector('[data-task-kind]').value = taskView.kind;
    elDetail.querySelector('[data-task-matching]').checked = taskView.matching;
    elDetail.querySelector('.an-task-scope').textContent = taskView.only === null ? TRACKS.length + ' identities' : 'Task #' + taskView.only + ' · Reset for all';
    const table = elDetail.querySelector('.an-task-table');
    if (!reuse || table._taskOrder !== order) {
        const columns = [['name', 'task / PID'], ['', 'kind'], ['', 'CPUs'], ['calls', 'calls'], ['matches', 'matches'],
            ['errors', 'errors'], ['runs', 'runs'], ['span', 'trace span']];
        table.innerHTML = '<thead><tr>' + columns.map(([key, label]) => '<th' + (key ? ' aria-sort="' + (taskView.sort === key ?
            (key === 'name' ? 'ascending' : 'descending') : 'none') + '"><button class="tl-sum-sort" data-task-sort="' + key + '">' + label + '</button>' : '>' + label) +
            '</th>').join('') + '<th>navigate</th></tr></thead><tbody>' + taskRows(order.rows.slice(0, order.visible)) + '</tbody>';
        table._taskOrder = order;
        if (reuse) elDetail.scrollTop = 0;
    }
    updateTaskFooter(order);
    sizeDetailHead();
}
// ---- 1. error summary: syscall -> origin -> errno ----
const ERROR_PAGE = 200, ATOMIC_PAGE = 100;
const ERROR_FATES = ['handled', 'to_user', 'unknown'];
const ERROR_ENTRIES = new Map();
const SYSCALL_ENTRY = /^(?:__(?:arm64|x64|ia32|x32|riscv|s390x?|powerpc|loongarch|se)_)?(?:compat_)?sys_(\w+)$|^__do_sys_(\w+)$/;
let errorCache = null, atomicCache = null, errorView = null;
function errorEntry(index) {
    const path = [];
    let current = index, entry;
    while (current >= 0) {
        entry = ERROR_ENTRIES.get(current);
        if (entry) break;
        path.push(current);
        const name = nameOf(current), base = name.split('.')[0], syscall = SYSCALL_ENTRY.exec(base);
        if (syscall || sParent[current] < 0) {
            entry = { label: syscall ? (syscall[1] || syscall[2]) + name.slice(base.length) : name, isSys: !!syscall };
            break;
        }
        current = sParent[current];
    }
    for (const visited of path) ERROR_ENTRIES.set(visited, entry);
    return entry;
}
function errorData() {
    if (errorCache && errorCache.hits === textHits && errorCache.minors === showMinor) return errorCache;
    const grouped = new Map(), groups = [];
    let total = 0, minor = 0;
    CHAINS.forEach((chain, index) => {
        if (!matchOk(chain[0])) return;
        if (chain[6]) minor++;
        if (!chainShown(index)) return;
        total++;
        const entry = errorEntry(chain[1]), origin = nameOf(chain[0]);
        const key = JSON.stringify([entry.label, entry.isSys, origin, chain[2]]);
        let group = grouped.get(key);
        if (!group) {
            group = { id: groups.length, entry, origin, errno: chain[2], fate: [0, 0, 0], tasks: new Set(),
                first: index, verified: 0, minor: 0, length: 0, ids: [], position: -1 };
            grouped.set(key, group); groups.push(group);
        }
        group.fate[chain[3]]++;
        group.tasks.add(sTask[chain[0]]);
        group.verified += chain[5] ? 1 : 0;
        group.minor += chain[6] ? 1 : 0;
        group.length = Math.max(group.length, chain[4]);
        group.ids.push(index);
    });
    for (const group of groups) group.ids.sort((left, right) => sStart[CHAINS[left][0]] - sStart[CHAINS[right][0]] || left - right);
    errorCache = { hits: textHits, minors: showMinor, groups, total, minor, order: { key: null, rows: [], visible: 0 } };
    return errorCache;
}
function atomicData() {
    if (atomicCache && atomicCache.hits === textHits) return atomicCache;
    const grouped = new Map(), groups = [];
    let total = 0;
    for (const [index, bits] of ATOMIC) {
        if (index >= N || !matchOk(index)) continue;
        total++;
        const caller = sParent[index] >= 0 ? nameOf(sParent[index]) : '';
        const key = JSON.stringify([sName[index], bits, caller]);
        let group = grouped.get(key);
        if (!group) {
            group = { id: groups.length, name: nameOf(index), caller, bits, first: index, ids: [], position: -1 };
            grouped.set(key, group); groups.push(group);
        }
        group.ids.push(index);
    }
    for (const group of groups) group.ids.sort((left, right) => sStart[left] - sStart[right] || left - right);
    const rows = groups.slice().sort((left, right) => right.ids.length - left.ids.length || left.first - right.first);
    atomicCache = { hits: textHits, groups, rows, total, visible: Math.min(ATOMIC_PAGE, rows.length) };
    return atomicCache;
}
function errorSortAscending(key) { return key === 'sys' || key === 'origin' || key === 'errno'; }
function errorSortValue(group, key) {
    if (key === 'sys') return group.entry.label;
    if (key === 'origin') return group.origin;
    if (key === 'errno') return group.errno;
    if (key === 'user') return group.fate[1];
    if (key === 'h') return group.fate[0];
    if (key === 'unknown') return group.fate[2];
    if (key === 't') return group.tasks.size;
    return key === 'x' ? group.length : group.ids.length;
}
function errorOrder() {
    const data = errorData(), order = data.order;
    if (order.key !== AN.errSort) {
        const direction = errorSortAscending(AN.errSort) ? 1 : -1;
        order.rows = data.groups.slice().sort((left, right) => {
            const leftValue = errorSortValue(left, AN.errSort), rightValue = errorSortValue(right, AN.errSort);
            return (leftValue < rightValue ? -direction : leftValue > rightValue ? direction : 0) || left.first - right.first;
        });
        order.key = AN.errSort;
        order.visible = Math.min(ERROR_PAGE, order.rows.length);
    }
    return order;
}
function errorNavigation(group) {
    return '<td class="an-occurrence"><button class="tl-btn" data-error-step="-1" aria-label="Previous occurrence">◀</button>' +
        '<span role="status" data-error-position>' + (group.position < 0 ? '—' : group.position + 1) + ' / ' + group.ids.length + '</span>' +
        '<button class="tl-btn" data-error-step="1" aria-label="Next occurrence">▶</button></td>';
}
function errorRows(rows) {
    return rows.map(group => '<tr data-chain="' + group.ids[Math.max(0, group.position)] + '" data-chains="' + group.id + '">' +
        '<td style="text-align:left">' + (group.entry.isSys ? '' : pill('mute', 'no syscall')) + esc(group.entry.label) + '</td>' +
        '<td style="text-align:left">' + esc(group.origin) + '</td><td class="err">' + group.errno + ' ' + esc(errName(group.errno)) + '</td>' +
        '<td>' + group.ids.length + '</td><td class="' + (group.fate[1] ? 'err' : '') + '">' + group.fate[1] + '</td>' +
        '<td>' + group.fate[0] + '</td><td>' + group.fate[2] + '</td><td>' + group.tasks.size + '</td>' +
        '<td title="' + (group.ids.length - group.verified) + ' of ' + group.ids.length + ' chains have unverified frames">' +
        group.length + ' frames' + (group.verified === group.ids.length ? '' : ' ?') + '</td>' + errorNavigation(group) + '</tr>').join('');
}
function atomicRows(rows) {
    return rows.map(group => '<tr class="an-atomic" data-atomic-group="' + group.id + '" data-slice="' + group.ids[Math.max(0, group.position)] + '">' +
        '<td style="text-align:left">' + esc(group.name) + '</td><td style="text-align:left" class="err">' + esc(atomicText(group.bits)) + '</td>' +
        '<td style="text-align:left">' + esc(group.caller || 'top level') + '</td><td>' + group.ids.length + '</td>' + errorNavigation(group) + '</tr>').join('');
}
function errorFooter(atomic) {
    const data = atomic ? atomicData() : errorOrder(), kind = atomic ? 'atomic' : 'errors';
    return '<div class="an-error-footer" data-error-footer="' + kind + '"><span role="status">Showing ' + data.visible + ' of ' + data.rows.length +
        (atomic ? ' atomic groups' : ' error groups') + '</span><button class="tl-btn" data-act="' + kind + '-more"' +
        (data.visible >= data.rows.length ? ' disabled' : '') + '>Load more</button>' +
        (atomic ? '' : '<button class="tl-btn" data-act="error-occurrences-csv">Export occurrences CSV</button>') + '</div>';
}
function appendErrorRows(atomic) {
    const data = atomic ? atomicData() : errorOrder(), start = data.visible;
    data.visible = Math.min(start + (atomic ? ATOMIC_PAGE : ERROR_PAGE), data.rows.length);
    elDetail.querySelector(atomic ? '.an-atomic-table tbody' : '.an-error-table tbody').insertAdjacentHTML('beforeend',
        (atomic ? atomicRows : errorRows)(data.rows.slice(start, data.visible)));
    const kind = atomic ? 'atomic' : 'errors', footer = elDetail.querySelector('[data-error-footer="' + kind + '"]');
    footer.querySelector('[role=status]').textContent = 'Showing ' + data.visible + ' of ' + data.rows.length + (atomic ? ' atomic groups' : ' error groups');
    footer.querySelector('[data-act="' + kind + '-more"]').disabled = data.visible >= data.rows.length;
}
function jumpErrorOccurrence(row, direction) {
    const atomic = row.hasAttribute('data-atomic-group');
    const group = atomic ? atomicData().groups[+row.dataset.atomicGroup] : errorData().groups[+row.dataset.chains];
    const count = group.ids.length;
    group.position = group.position < 0 ? (direction > 0 ? 0 : count - 1) : (group.position + direction + count) % count;
    const occurrence = group.ids[group.position], chain = atomic ? null : CHAINS[occurrence];
    const origin = atomic ? occurrence : chain[0], top = atomic ? origin : chain[1], track = TRACKS[sTask[origin]];
    if (track.hidden) { track.hidden = false; layout(); }
    zoomTo(sStart[top], sEnd[top], origin);
    setSelection(origin); revealRow(origin); draw();
    row.querySelector('[data-error-position]').textContent = group.position + 1 + ' / ' + count;
    if (atomic) row.dataset.slice = origin;
    else row.dataset.chain = occurrence;
    toast('occurrence ' + (group.position + 1) + ' / ' + count);
}
function exportErrorsCsv(kind) {
    const filter = (window.fgFilterState || {}).text || '', includeMinor = showMinor ? 1 : 0;
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const basis = MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'measured';
    let columns;
    if (kind === 'groups') columns = ['group_id', 'entry', 'entry_is_syscall', 'origin', 'errno', 'errno_name', 'chains', 'to_user', 'handled', 'unknown',
        'tasks', 'task_labels', 'max_chain_frames', 'verified_chains', 'unverified_chains', 'minor_chains', 'filter', 'include_minor'];
    else if (kind === 'occurrences') columns = ['group_id', 'chain_index', 'entry', 'entry_is_syscall', 'origin', 'errno', 'errno_name', 'fate', 'chain_frames',
        'verified', 'minor', 'task', 'pid', 'origin_slice', 'top_slice', 'origin_line', 'top_line', 'origin_start_' + unit, 'origin_end_' + unit,
        'origin_duration_' + unit, 'top_start_' + unit, 'top_end_' + unit, 'top_duration_' + unit, 'time_mode', 'duration_basis', 'filter', 'include_minor'];
    else columns = ['group_id', 'slice_index', 'function', 'caller', 'reasons', 'reason_bits', 'task', 'pid', 'line', 'start_' + unit, 'end_' + unit,
        'duration_' + unit, 'self_' + unit, 'time_mode', 'duration_basis', 'filter'];
    function* rows() {
        for (const group of kind === 'atomic' ? atomicData().rows : errorOrder().rows) {
            if (kind === 'groups') {
                yield [group.id, group.entry.label, group.entry.isSys ? 1 : 0, group.origin, group.errno, errName(group.errno), group.ids.length,
                    group.fate[1], group.fate[0], group.fate[2], group.tasks.size, Array.from(group.tasks, task => trackLabel(TRACKS[task])).join('; '),
                    group.length, group.verified, group.ids.length - group.verified, group.minor, filter, includeMinor];
                continue;
            }
            for (const index of group.ids) {
                if (kind === 'atomic') {
                    const task = TRACKS[sTask[index]];
                    yield [group.id, index, group.name, group.caller, atomicText(group.bits), group.bits, trackLabel(task), task.pid, sLine[index],
                        sStart[index] / scale, sEnd[index] / scale, durOf(index) / scale, sSelf[index] / scale, MODE, basis, filter];
                } else {
                    const chain = CHAINS[index], origin = chain[0], top = chain[1], task = TRACKS[sTask[origin]];
                    yield [group.id, index, group.entry.label, group.entry.isSys ? 1 : 0, group.origin, group.errno, errName(group.errno), ERROR_FATES[chain[3]],
                        chain[4], chain[5] ? 1 : 0, chain[6] ? 1 : 0, trackLabel(task), task.pid, origin, top, sLine[origin], sLine[top],
                        sStart[origin] / scale, sEnd[origin] / scale, durOf(origin) / scale, sStart[top] / scale, sEnd[top] / scale, durOf(top) / scale,
                        MODE, basis, filter, includeMinor];
                }
            }
        }
    }
    downloadTimelineCsv('funcgraph-errors-' + kind + '.csv', columns, rows());
}
function showErrors() {
    const wasOpen = AN.kind === 'errors' && elDetail.classList.contains('show');
    AN.kind = 'errors';
    const data = errorData(), order = errorOrder(), atomic = atomicData();
    const resetScroll = !wasOpen || !errorView || errorView.data !== data;
    const sorted = errorView && errorView.sort !== AN.errSort;
    let html = '<div class="tl-detail-head an-error-head"><span class="tl-detail-title">Errors</span>' +
        pill('err', data.total + ' error chains') + pill('mute', data.groups.length + ' groups') +
        (atomic.total ? pill('warn', atomic.total + ' atomic calls') : '') + (textHits ? pill('warn', 'filtered') : '') +
        (D.btf || D.caps.retval_btf ? '' : pill('warn', 'no BTF: unverified')) +
        (data.minor || showMinor ? '<label class="an-minor" title="Handled at once; this function never propagated a negative return in the trace">' +
            '<input type="checkbox" data-act="minor"' + (showMinor ? ' checked' : '') + '>Show ' + data.minor + ' minor</label>' : '') +
        '<span class="an-error-actions"><button class="tl-btn" data-act="errors-csv"' + (!data.groups.length ? ' disabled' : '') +
        '>Export groups CSV</button><button class="tl-close" title="Close (Esc)">×</button></span></div>';
    if (atomic.total) {
        html += '<div class="an-error-section"><span class="tl-sec">Sleeping functions called in atomic context</span>' +
            '<button class="tl-btn" data-act="atomic-csv">Export atomic CSV</button></div>' +
            '<table class="tl-sum an-tbl an-atomic-table"><thead><tr><th>function</th><th>reason</th><th>caller</th><th>calls</th><th>occurrence</th></tr></thead><tbody>' +
            atomicRows(atomic.rows.slice(0, atomic.visible)) + '</tbody></table>' + errorFooter(true);
    }
    if (!data.groups.length) {
        html += '<div class="an-hint">' + (textHits ? 'No visible error chains match the current filter.' :
            D.caps.retval || CHAINS.length ? 'No visible error returns found.' : 'Error paths need <code>options/funcgraph-retval</code>.') + '</div>';
    } else {
        const columns = [['sys', 'syscall / entry', 1], ['origin', 'origin', 1], ['errno', 'errno'], ['count', 'count'],
            ['user', 'to user'], ['h', 'handled'], ['unknown', 'unknown'], ['t', 'tasks'], ['x', 'chain']];
        html += '<table class="tl-sum an-tbl an-error-table"><thead><tr>' + columns.map(([key, label, left]) => '<th data-esort="' + key +
            '" aria-sort="' + (key === AN.errSort ? (errorSortAscending(key) ? 'ascending' : 'descending') : 'none') + '"' +
            (left ? ' style="text-align:left"' : '') + '><button class="tl-sum-sort">' + label + '</button></th>').join('') +
            '<th>occurrence</th></tr></thead><tbody>' + errorRows(order.rows.slice(0, order.visible)) + '</tbody></table>' + errorFooter(false);
    }
    html += '<div class="an-hint">Click a row for the next occurrence; Shift-click or use ◀ for the previous one. ' +
        'Every occurrence is accessible, not just the first 50. Unknown means the trace cannot establish the final outcome. ' +
        'The shared filter matches error origins or atomic calls; exports include unloaded rows. Durations follow Overhead, positions stay as recorded.</div>';
    openDetail(html);
    if (resetScroll) elDetail.scrollTop = 0;
    else if (sorted && order.rows.length) {
        const table = elDetail.querySelector('.an-error-table'), head = elDetail.querySelector('.an-error-head');
        const pinned = getComputedStyle(head).top !== 'auto' ? head.offsetHeight : 0;
        elDetail.scrollTop += table.getBoundingClientRect().top - elDetail.getBoundingClientRect().top - pinned;
    }
    errorView = { data, sort: AN.errSort };
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
    const keepSlowFast = distView && distView.data.name === nameIdx && distView.slowFastOpen;
    AN.kind = 'dist';
    if (AN.func !== nameIdx) AN.site = null;
    AN.func = nameIdx;
    const data = distributionData(nameIdx);
    const population = AN.site !== null ? data.sites.get(AN.site) || { ids: [], total: 0 } : data;
    const changedPopulation = !distView || distView.data !== data || distView.site !== AN.site;
    if (changedPopulation) {
        const siteVisible = distView && distView.data === data ? distView.siteVisible : DIST_SITE_PAGE;
        distView = { data, site: AN.site, population, histogram: distributionHistogram(population.ids),
            bucket: -1, visible: Math.min(15, population.ids.length), siteVisible, slowFastOpen: !!keepSlowFast };
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
        '<div class="an-slowfast">' + slowFastHtml() + '</div>' +
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
const WINDOW_INDEX = new Map();
const WINDOW_ENDS = ['flag cleared', 'switched out', 'trace end'];
let latencyCache = null, sectionCache = null;
function windowRows(track) {
    let indexed = WINDOW_INDEX.get(track);
    if (indexed) return indexed;
    indexed = [];
    for (const row of TRACKS[track].rowsT) {
        const ids = row.filter(index => index < N);
        if (!ids.length) continue;
        const ends = new Float64Array(ids.length);
        let maximum = -Infinity;
        for (let position = 0; position < ids.length; position++) {
            maximum = Math.max(maximum, sEnd[ids[position]]);
            ends[position] = maximum;
        }
        indexed.push({ ids, ends });
    }
    WINDOW_INDEX.set(track, indexed);
    return indexed;
}
function visitWindow(track, start, end, visit) {
    if (end <= start) return;
    for (const row of windowRows(track)) {
        let low = 0, high = row.ids.length;
        while (low < high) {
            const middle = (low + high) >>> 1;
            if (row.ends[middle] < start) low = middle + 1;
            else high = middle;
        }
        for (let position = low; position < row.ids.length; position++) {
            const index = row.ids[position], begin = sStart[index], finish = sEnd[index];
            if (begin >= end) break;
            if (finish <= start && !(begin === finish && begin >= start)) continue;
            if (visit(index) === false) return;
        }
    }
}
function windowMatches(track, start, end) {
    if (!textHits) return true;
    let matched = false;
    visitWindow(track, start, end, index => {
        if (textHits[index]) { matched = true; return false; }
    });
    return matched;
}
function windowProfile(record, aggregate) {
    if (record.profile) return record.profile;
    const blame = new Map();
    let matched = false, irq = 0;
    visitWindow(record.track, record.start, record.end, index => {
        if (!matchOk(index)) return;
        matched = true;
        const geometry = sEnd[index] - sStart[index];
        const part = geometry > 0 ? sSelf[index] * (Math.min(sEnd[index], record.end) - Math.max(sStart[index], record.start)) / geometry : 0;
        if (part <= 0) return;
        blame.set(sName[index], (blame.get(sName[index]) || 0) + part);
        if (sFlags[index] & FL.IRQOFF) irq += part;
    });
    if (aggregate) for (const [name, value] of blame) aggregate.set(name, (aggregate.get(name) || 0) + value);
    const top = Array.from(blame.entries()).sort((left, right) => right[1] - left[1] || left[0] - right[0]).slice(0, 3);
    record.profile = { top, irq, matched };
    return record.profile;
}
function windowRecord(raw, index, section) {
    const offset = section ? 2 : 1, start = raw[offset], end = raw[offset + 1];
    return { raw, index, track: raw[0], start, end, duration: end - start, depth: raw[section ? 6 : 3], profile: null };
}
function latencyWindows() {
    if (latencyCache && latencyCache.hits === textHits && latencyCache.durations === sDur) return latencyCache;
    const rows = [], aggregate = new Map();
    let total = 0, maximum = 0;
    D.resched.forEach((raw, index) => {
        const record = windowRecord(raw, index, false);
        if (record.duration <= 0) return;
        const profile = windowProfile(record, aggregate);
        if (textHits && !profile.matched) return;
        rows.push(record);
        total += record.duration;
        maximum = Math.max(maximum, record.duration);
    });
    const top = Array.from(aggregate.entries()).sort((left, right) => right[1] - left[1] || left[0] - right[0]).slice(0, 8);
    latencyCache = { hits: textHits, durations: sDur, groups: [rows], orders: [], total, maximum, top };
    return latencyCache;
}
function sectionWindows() {
    if (sectionCache && sectionCache.hits === textHits && sectionCache.durations === sDur) return sectionCache;
    const groups = [[], [], []], totals = [0, 0, 0], maxima = [0, 0, 0];
    SECTIONS.forEach((raw, index) => {
        const record = windowRecord(raw, index, true), kind = raw[1];
        if (record.duration < 0 || !windowMatches(record.track, record.start, record.end)) return;
        groups[kind].push(record);
        totals[kind] += record.duration;
        maxima[kind] = Math.max(maxima[kind], record.duration);
    });
    sectionCache = { hits: textHits, durations: sDur, groups, orders: [], totals, maxima };
    return sectionCache;
}
function windowSortAscending(key) { return key === 'start' || key === 'task' || key === 'end'; }
function windowSortValue(record, key) {
    if (key === 'dur') return record.duration;
    if (key === 'start') return record.start;
    if (key === 'task') return trackLabel(TRACKS[record.track]);
    if (key === 'pre') return record.depth;
    if (key === 'end') return record.raw[5];
    const profile = windowProfile(record);
    return key === 'irq' ? profile.irq : profile.top.length ? profile.top[0][1] : 0;
}
function windowOrder(data, kind, key, page) {
    let order = data.orders[kind];
    if (!order) { order = { rows: data.groups[kind].slice(), key: null, visible: 0 }; data.orders[kind] = order; }
    if (order.key !== key) {
        const direction = windowSortAscending(key) ? 1 : -1;
        order.rows.sort((left, right) => {
            const leftValue = windowSortValue(left, key), rightValue = windowSortValue(right, key);
            return (leftValue < rightValue ? -direction : leftValue > rightValue ? direction : 0) || left.index - right.index;
        });
        order.key = key;
        order.visible = Math.min(page, order.rows.length);
    }
    return order;
}
function windowTableHead(columns, key, attr) {
    return '<tr>' + columns.map(([field, label, left]) => '<th' + (left ? ' style="text-align:left"' : '') +
        (field ? ' data-' + attr + '="' + field + '" aria-sort="' + (field === key ? (windowSortAscending(key) ? 'ascending' : 'descending') : 'none') +
            '"><button class="tl-sum-sort">' + label + '</button>' : '>' + label) + '</th>').join('') + '</tr>';
}
function windowActions(enabled) {
    return '<span class="an-window-actions"><button class="tl-btn" data-act="window-csv"' + (enabled ? '' : ' disabled') +
        '>Export CSV</button><button class="tl-close" title="Close (Esc)">×</button></span></div>';
}
function windowFooter(order, section) {
    return '<div class="an-window-footer"><span role="status">Showing ' + order.visible + ' of ' + order.rows.length +
        (section ? ' sections' : ' windows') + '</span><button class="tl-btn" data-act="window-more"' +
        (order.visible >= order.rows.length ? ' disabled' : '') + '>Load more</button></div>';
}
function windowTimeHint() {
    return TIMED ? '' : '<div class="an-hint">No timestamps: ' + (MODE === 'count' ? 'call-order units' : 'packed coordinates') +
        ' describe trace order, not measured scheduling latency or wall-clock section lengths.</div>';
}
function windowTopHtml(profile) {
    return profile.top.map(([name, value]) => esc(S[name]) + ' <span class="d">' + fmtDur(value) + '</span>').join(', ') || '—';
}
function latencyRows(rows) {
    return rows.map(record => {
        const profile = windowProfile(record);
        return '<tr data-win="' + record.start + ',' + record.end + ',' + record.track + '"><td class="' +
            (TIMED && record.duration > 100000 ? 'err' : '') + '">' + fmtDur(record.duration) + '</td><td>' + fmtDur(record.start) + '</td>' +
            '<td style="text-align:left">' + esc(trackLabel(TRACKS[record.track])) + '</td><td>' + (record.depth || '') + '</td>' +
            '<td>' + (profile.irq ? fmtDur(profile.irq) : '') + '</td><td style="text-align:left">' + windowTopHtml(profile) + '</td>' +
            '<td>' + (WINDOW_ENDS[record.raw[5]] || '') + '</td></tr>';
    }).join('');
}
function appendWindowRows() {
    const section = AN.kind === 'sec';
    const data = section ? sectionWindows() : latencyWindows();
    const order = windowOrder(data, section ? AN.secKind : 0, section ? AN.secSort : AN.latSort, section ? 200 : 300);
    const start = order.visible;
    order.visible = Math.min(start + (section ? 200 : 300), order.rows.length);
    const rows = order.rows.slice(start, order.visible);
    elDetail.querySelector('.an-window-table tbody').insertAdjacentHTML('beforeend', section ? sectionRows(rows) : latencyRows(rows));
    elDetail.querySelector('.an-window-footer [role=status]').textContent = 'Showing ' + order.visible + ' of ' + order.rows.length + (section ? ' sections' : ' windows');
    elDetail.querySelector('[data-act=window-more]').disabled = order.visible >= order.rows.length;
}
function exportWindowCsv() {
    const section = AN.kind === 'sec';
    const data = section ? sectionWindows() : latencyWindows();
    const order = windowOrder(data, section ? AN.secKind : 0, section ? AN.secSort : AN.latSort, section ? 200 : 300);
    if (!order.rows.length) return;
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const basis = MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'measured';
    const columns = [section ? 'section_index' : 'window_index', 'task', 'pid', 'start_' + unit, 'end_' + unit, 'window_duration_' + unit, 'max_preempt_depth'];
    columns.push(...(section ? ['kind', 'started_in', 'ended_in', 'start_line', 'end_line'] : ['irqoff_seen', 'irqoff_self_estimate_' + unit, 'ended_by']));
    for (let rank = 1; rank <= 3; rank++) columns.push('top_' + rank + '_function', 'top_' + rank + '_self_estimate_' + unit);
    columns.push('time_mode', 'self_duration_basis', 'window_duration_basis', 'filter');
    function* rows() {
        for (const record of order.rows) {
            const raw = record.raw, task = TRACKS[record.track], profile = windowProfile(record);
            const values = [record.index, trackLabel(task), task.pid, record.start / scale, record.end / scale, record.duration / scale, record.depth];
            values.push(...(section ? [SEC_NAMES[raw[1]], raw[4] >= 0 ? nameOf(raw[4]) : 'top level',
                raw[5] >= 0 ? nameOf(raw[5]) : raw[8] < 0 ? 'switched out / end' : 'interrupt', raw[7], raw[8]] :
                [raw[4], profile.irq / scale, WINDOW_ENDS[raw[5]] || '']));
            for (let rank = 0; rank < 3; rank++) values.push(...(profile.top[rank] ? [S[profile.top[rank][0]], profile.top[rank][1] / scale] : ['', '']));
            values.push(MODE, basis, TIMED ? 'timestamp_span' : MODE === 'count' ? 'call_order' : 'packed_estimate', (window.fgFilterState || {}).text || '');
            yield values;
        }
    }
    downloadTimelineCsv('funcgraph-' + (section ? 'sections' : 'latency') + '-' + (MODE === 'count' ? 'count' : adjOn ? 'adjusted' : 'raw') + '.csv', columns, rows());
}
function showLatency() {
    AN.kind = 'lat';
    const data = latencyWindows(), order = windowOrder(data, 0, AN.latSort, 300);
    let h = '<div class="tl-detail-head an-window-head"><span class="tl-detail-title">Scheduling latency</span>' +
        pill('warn', order.rows.length + ' need-resched windows') + pill('mute', 'total ' + fmtDur(data.total)) +
        (order.rows.length ? pill('mute', 'max ' + fmtDur(data.maximum)) : '') + (textHits ? pill('warn', 'filtered') : '') +
        (adjOn ? pill('warn', 'self: overhead subtracted') : '') + windowActions(order.rows.length);
    if (!order.rows.length) {
        h += '<div class="an-hint">' + (!D.caps.latency ? 'Needs <code>options/latency-format</code> (need-resched flag) and timestamps.' :
            textHits ? 'No need-resched windows overlap calls matching the current filter.' : 'No pending need-resched windows in this trace.') + '</div>';
        openDetail(h); return;
    }
    h += windowTimeHint() + '<div class="an-hint" style="padding:6px 12px 0">Window = need-resched set until the task schedules (or is switched out). ' +
        'Self times are proportional overlap estimates from matching calls, not exact on-CPU or causal attribution. ' +
        'Overhead changes self estimates, not window positions or lengths. Overview sums every matching function across windows.</div>' +
        '<div class="an-blame">' + data.top.map(([name, value]) => '<span class="an-chip"><span class="k">' + esc(S[name]) + '</span>' + fmtDur(value) + '</span>').join('') + '</div>';
    h += '<table class="tl-sum an-tbl an-window-table"><thead>' + windowTableHead([['dur', 'latency'], ['start', 'start'], ['task', 'task', 1],
        ['pre', 'max preempt off'], ['irq', 'irqs-off self (est.)'], ['top', 'top self (est.)', 1], ['end', 'ended by']], AN.latSort, 'lsort') +
        '</thead><tbody>' + latencyRows(order.rows.slice(0, order.visible)) + '</tbody></table>' + windowFooter(order, false);
    openDetail(h);
}

elDetail.addEventListener('change', ev => {
    if (ev.target.dataset && ev.target.dataset.act === 'minor') {
        showMinor = ev.target.checked; rebuildErrList(); draw(); showErrors();
        elDetail.querySelector('[data-act=minor]').focus({ preventScroll: true });
    }
});
elDetail.addEventListener('click', ev => {
    const action = ev.target.closest('[data-act]');
    if (action && AN.kind === 'errors') {
        const act = action.dataset.act;
        if (act === 'errors-more' || act === 'atomic-more') { appendErrorRows(act === 'atomic-more'); ev.stopImmediatePropagation(); return; }
        if (act === 'errors-csv' || act === 'error-occurrences-csv' || act === 'atomic-csv') {
            exportErrorsCsv(act === 'errors-csv' ? 'groups' : act === 'atomic-csv' ? 'atomic' : 'occurrences');
            ev.stopImmediatePropagation(); return;
        }
    }
    if (action && (AN.kind === 'lat' || AN.kind === 'sec')) {
        if (action.dataset.act === 'window-more') { appendWindowRows(); ev.stopImmediatePropagation(); return; }
        if (action.dataset.act === 'window-csv') { exportWindowCsv(); ev.stopImmediatePropagation(); return; }
    }
    const th = ev.target.closest('th[data-esort], th[data-lsort]');
    if (th) {
        if (th.dataset.esort) {
            AN.errSort = th.dataset.esort; showErrors();
            elDetail.querySelector('th[data-esort="' + AN.errSort + '"] button').focus({ preventScroll: true });
        }
        else {
            AN.latSort = th.dataset.lsort; showLatency();
            elDetail.querySelector('th[data-lsort="' + AN.latSort + '"] button').focus({ preventScroll: true });
        }
        ev.stopImmediatePropagation(); return;
    }
    const ec = ev.target.closest('tr[data-chains], tr[data-atomic-group]');
    if (ec) {
        const step = ev.target.closest('[data-error-step]');
        jumpErrorOccurrence(ec, step ? +step.dataset.errorStep : ev.shiftKey ? -1 : 1);
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
const WAIT_PAGE = 100, WAIT_MAX_GAP = 50e6;
const waitOffIndexes = new Map();
let waitCache = null;
function offBefore(task, time) {
    let records = waitOffIndexes.get(task);
    if (!records) {
        records = TRACKS[task].off.filter(record => !OPEN_OFF.has(record));
        records.sort((left, right) => left[2] - right[2] || OFF_IDX.get(right) - OFF_IDX.get(left));
        waitOffIndexes.set(task, records);
    }
    let lower = 0, upper = records.length;
    while (lower < upper) {
        const middle = (lower + upper) >>> 1;
        if (records[middle][2] <= time) lower = middle + 1;
        else upper = middle;
    }
    return lower ? records[lower - 1] : null;
}
const RE_WAKE_INTERNAL = /^(?:try_to_wake_up|ttwu_\w+|wake_up\w*|__wake_up\w*|autoremove_wake_function|default_wake_function|woken_wake_function|pollwake|ep_poll_callback|complete\w*|swake_up\w*|wake_q_\w+|signal_wake_up\w*|_raw_spin_\w+|raw_spin_rq_\w+|preempt_count_\w+|select_task_rq\w*)$/;
function wakeSite(i) {
    // the first frame above the wakeup machinery: what the waker was actually doing
    let k = i;
    while (k >= 0 && RE_WAKE_INTERNAL.test(nameOf(k).split('.')[0])) k = sParent[k];
    return k >= 0 ? nameOf(k) : nameOf(i);
}
function extendWaitChain(data, limit) {
    while (data.next && data.steps.length < limit) {
        const record = data.next, index = OFF_IDX.get(record), wake = WAKE_OF_OFF.get(index);
        data.steps.push({ off: record, wake: wake, gap: data.gap });
        data.seen.add(index);
        data.next = null;
        if (!TIMED) { data.stop = ['no-timestamps', 'Timestamps are unavailable; temporal links cannot be inferred.']; break; }
        if (!wake) { data.stop = ['wakeup-not-recorded', 'Wakeup not recorded for the last wait.']; break; }
        if (wake[3] !== 0) { data.stop = ['interrupt-context', 'The last wakeup came from interrupt context.']; break; }
        if (TRACKS[wake[0]].kind !== 0) { data.stop = ['non-task-context', 'The last waker was idle or a CPU-only track.']; break; }
        const previous = offBefore(wake[0], wake[1]);
        if (!previous) { data.stop = ['no-prior-wait', 'No earlier completed wait was recorded for the last waker.']; break; }
        const gap = wake[1] - previous[2];
        if (gap > WAIT_MAX_GAP) { data.stop = ['gap-exceeded', 'The last waker resumed ' + fmtDur(gap) + ' before waking the task (limit 50 ms).']; break; }
        if (data.seen.has(OFF_IDX.get(previous))) { data.stop = ['cycle', 'A repeated wait was reached; temporal traversal stopped.']; break; }
        data.next = previous;
        data.gap = gap;
    }
}
function waitRows(data, start, end) {
    let html = '';
    for (let index = start; index < end; index++) {
        const step = data.steps[index], record = step.off, wake = step.wake;
        const open = OPEN_OFF.has(record), label = trackLabel(TRACKS[record[0]]);
        const who = wake ? esc(trackLabel(TRACKS[wake[0]])) + (wake[3] ? ' <span class="tl-pill ctx' + wake[3] + '">' + CTX_NAMES[wake[3]] + '</span>' : '') : '<span class="d">not recorded</span>';
        const latency = wake && !open ? record[2] - wake[1] : null;
        html += '<tr data-wait="' + OFF_IDX.get(record) + '"><td><button class="an-site-btn" title="' + esc(label) + '" aria-label="Show wait ' + (index + 1) + ': ' + esc(label) + '">' +
            (index + 1) + '. ' + esc(label) + '</button></td><td>' + (open ? '≥ ' : '') + fmtDur(record[2] - record[1]) + '</td><td>' +
            (record[4] >= 0 ? esc(S[record[4]]) : '') + (record[5] >= 0 ? ' <span class="d">[' + esc(S[record[5]]) + ']</span>' : '') + '</td><td>' +
            who + '</td><td>' + (wake && wake[4] >= 0 ? esc(wakeSite(wake[4])) : '') + '</td><td class="' +
            (TIMED && latency > 1e6 ? 'err' : '') + '">' + (latency === null ? 'not recorded' : fmtDur(latency)) + '</td><td>' +
            (!open && record[7] >= 0 ? 'CPU ' + record[7] : 'not recorded') + '</td><td>' + (step.gap === null ? 'selected wait' : fmtDur(step.gap)) + '</td></tr>';
    }
    return html;
}
function waitStatus(data) {
    return 'Showing ' + Math.min(data.visible, data.steps.length) + (data.next ? '+' : ' of ' + data.steps.length) + ' waits · ' +
        (data.next ? 'More temporal links available.' : 'Stopped: ' + data.stop[1]);
}
function updateWaitFooter(data) {
    elDetail.querySelector('.an-wait-footer [role=status]').textContent = waitStatus(data);
    elDetail.querySelector('[data-act=wait-more]').disabled = data.visible >= data.steps.length && !data.next;
}
function showWaitChain(off) {
    AN.kind = 'wait';
    AN.off = off;
    if (!waitCache || waitCache.off !== off) {
        waitCache = { off: off, steps: [], next: off, gap: null, seen: new Set(), visible: WAIT_PAGE, stop: null };
        extendWaitChain(waitCache, WAIT_PAGE);
    }
    const data = waitCache, label = trackLabel(TRACKS[off[0]]);
    let html = '<div class="tl-detail-head an-wait-head"><span class="tl-detail-title" title="' + esc(label) + '">Wait chain · ' + esc(label) + '</span>' +
        pill(off[8] ? 'warn' : 'mute', (off[8] ? 'preempted ' : 'blocked ') + (OPEN_OFF.has(off) ? '≥ ' : '') + fmtDur(off[2] - off[1])) +
        (off[4] >= 0 ? pill('mute', 'in ' + S[off[4]]) : '') + (off[5] >= 0 ? pill('mute', 'state ' + S[off[5]]) : '') +
        '<span class="an-wait-actions"><button class="tl-btn" data-act="wait-csv">Export chain CSV</button>' +
        '<button class="tl-close" title="Close (Esc)">×</button></span></div>';
    html += '<div class="an-hint an-wait-hint">' +
        (!D.caps.sched_events ? '<div>Capture sched_switch and sched_waking (fgcapture.sh --sched) to record wakeups.</div>' : '') +
        '<details><summary>' + (TIMED ? 'Further rows: temporal candidates, not proven dependencies · ≤ 50 ms · all tasks' :
            'No timestamps: temporal links unavailable · all tasks') + '</summary>' +
        '<div>Recorded wakeups identify who woke each task. Further rows are temporal candidates, not proven causal dependencies: ' +
        'the waker’s latest completed wait ending at most 50 ms before it woke the preceding task. “Link gap” is the time from that prior resume to the wakeup in the preceding row. ' +
        'Wake latency is from wakeup to the recorded resume; ≥ marks a wait still open at trace end. ' +
        'All tasks are included, independently of the text filter and Overhead. Click a row or activate its task button to show it.</div></details></div>';
    html += '<table class="tl-sum an-tbl an-wait-table"><thead><tr><th>task</th><th>waited</th><th>waiting in</th>' +
        '<th>woken by</th><th>waker was in</th><th>wake latency</th><th>run again on</th><th>link gap</th></tr></thead><tbody>' +
        waitRows(data, 0, Math.min(data.visible, data.steps.length)) + '</tbody></table>' +
        '<div class="an-wait-footer"><span role="status">' + esc(waitStatus(data)) + '</span><button class="tl-btn" data-act="wait-more"' +
        (data.visible >= data.steps.length && !data.next ? ' disabled' : '') + '>Load more waits</button></div>';
    openDetail(html);
}
function appendWaitRows() {
    if (AN.kind !== 'wait' || !waitCache) return;
    const data = waitCache, start = Math.min(data.visible, data.steps.length);
    data.visible += WAIT_PAGE;
    extendWaitChain(data, data.visible);
    elDetail.querySelector('.an-wait-table tbody').insertAdjacentHTML('beforeend', waitRows(data, start, Math.min(data.visible, data.steps.length)));
    updateWaitFooter(data);
}
function exportWaitCsv() {
    if (AN.kind !== 'wait' || !waitCache) return;
    const data = waitCache, unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    extendWaitChain(data, Infinity);
    const columns = ['step', 'offcpu_index', 'task_index', 'pid', 'comm', 'wait_start_' + unit, 'wait_end_' + unit, 'waited_' + unit,
        'end_status', 'waiting_function', 'state', 'preempted', 'waker_task_index', 'waker_pid', 'waker_comm', 'wake_time_' + unit,
        'waker_context', 'waker_function', 'wake_latency_' + unit, 'resume_cpu', 'link_kind', 'prior_resume_to_wake_' + unit,
        'max_link_gap_' + unit, 'terminal_reason', 'time_mode', 'duration_basis', 'filter_scope'];
    function* rows() {
        for (let index = 0; index < data.steps.length; index++) {
            const step = data.steps[index], record = step.off, wake = step.wake, task = TRACKS[record[0]];
            const waker = wake ? TRACKS[wake[0]] : null, open = OPEN_OFF.has(record);
            yield [index + 1, OFF_IDX.get(record), record[0], task.pid, task.comm, record[1] / scale, open ? '' : record[2] / scale,
                (record[2] - record[1]) / scale, open ? 'trace_end_lower_bound' : 'recorded_resume', record[4] >= 0 ? S[record[4]] : '',
                record[5] >= 0 ? S[record[5]] : '', record[8], wake ? wake[0] : '', waker ? waker.pid : '', waker ? waker.comm : '',
                wake ? wake[1] / scale : '', wake ? CTX_NAMES[wake[3]] : '', wake && wake[4] >= 0 ? wakeSite(wake[4]) : '',
                wake && !open ? (record[2] - wake[1]) / scale : '', !open && record[7] >= 0 ? record[7] : '', index ? 'temporal' : 'selected',
                step.gap === null ? '' : step.gap / scale, TIMED ? WAIT_MAX_GAP : '', index === data.steps.length - 1 ? data.stop[0] : '',
                MODE, MODE === 'count' ? 'call_order' : 'recorded_timestamps', 'all_tasks'];
        }
    }
    downloadTimelineCsv('funcgraph-wait-chain-' + (MODE === 'count' ? 'count' : 'raw') + '.csv', columns, rows());
    updateWaitFooter(data);
}
elDetail.addEventListener('click', ev => {
    const action = ev.target.closest('[data-act]');
    if (action && AN.kind === 'wait') {
        if (action.dataset.act === 'wait-more') { appendWaitRows(); ev.stopImmediatePropagation(); return; }
        if (action.dataset.act === 'wait-csv') { exportWaitCsv(); ev.stopImmediatePropagation(); return; }
    }
    const tr = ev.target.closest('tr[data-wait]');
    if (!tr) return;
    const r = D.offcpu[+tr.dataset.wait];
    const w = WAKE_OF_OFF.get(+tr.dataset.wait);
    const t = TRACKS[r[0]];
    t.hidden = false; t.collapsed = false;
    if (w) { TRACKS[w[0]].hidden = false; TRACKS[w[0]].collapsed = false; }
    layout();
    elScroll.scrollTop = Math.max(0, t.y - 4);
    zoomTo(w ? Math.min(w[1], r[1]) : r[1], r[2], -1);
    hovOff = r;
    draw();
    ev.stopImmediatePropagation();
}, true);

// ---- subtree helpers ----
let realCallTree = null;
const breakdownCache = new Map();
function callTreeIndex() {
    if (realCallTree) return realCallTree;
    const first = new Int32Array(N).fill(-1), next = new Int32Array(N).fill(-1), previous = new Int32Array(N).fill(-1);
    const roots = new Int32Array(TRACKS.length).fill(-1);
    for (let index = N - 1; index >= 0; index--) {
        const parent = sParent[index];
        if (parent >= 0 && (parent >= N || sTask[parent] !== sTask[index] || sDepth[parent] >= sDepth[index])) continue;
        const heads = parent < 0 ? roots : first, owner = parent < 0 ? sTask[index] : parent;
        next[index] = heads[owner];
        if (next[index] >= 0) previous[next[index]] = index;
        heads[owner] = index;
    }
    realCallTree = { first, next, previous };
    return realCallTree;
}
function directChildren(root) {
    const tree = callTreeIndex(), children = [];
    for (let child = tree.first[root]; child >= 0; child = tree.next[child]) children.push(child);
    return children;
}
const CHILD_PAGE = 100;
let childFacts = null, childFiltered = null;
const childOrders = { groups: null, calls: null };
const childView = { root: -1, mode: 'groups', name: null, query: '', groupSort: 'inclusive', callSort: 'inclusive' };
function childData(root, hits) {
    if (!childFacts || childFacts.root !== root) {
        childFacts = { root, ids: directChildren(root), bases: new Map() };
        childFiltered = null; childOrders.groups = null; childOrders.calls = null;
    }
    const raw = childFacts;
    if (!hits && raw.bases.has(sDur)) return raw.bases.get(sDur);
    if (hits && childFiltered && childFiltered.raw === raw && childFiltered.hits === hits && childFiltered.durations === sDur) return childFiltered;
    const grouped = new Map(), ids = [];
    for (const index of raw.ids) {
        if (hits && !hits[index]) continue;
        ids.push(index);
        let group = grouped.get(sName[index]);
        if (!group) {
            group = { name: sName[index], ids: [], inclusive: 0, self: 0, max: -Infinity, longest: index };
            grouped.set(group.name, group);
        }
        const duration = durOf(index);
        group.ids.push(index); group.inclusive += duration; group.self += sSelf[index];
        if (duration > group.max) { group.max = duration; group.longest = index; }
    }
    const groups = Array.from(grouped.values());
    for (const group of groups) group.mean = group.inclusive / group.ids.length;
    const data = { raw, hits, durations: sDur, ids, groups, preview: null };
    if (hits) childFiltered = data;
    else raw.bases.set(sDur, data);
    return data;
}
function childPreviewHtml(root) {
    const data = childData(root, null);
    if (!data.ids.length) return '';
    if (!data.preview) data.preview = data.groups.slice().sort((left, right) => right.inclusive - left.inclusive || left.ids[0] - right.ids[0]).slice(0, 8);
    return '<div class="tl-child-summary"><span class="tl-sec">Children (' + data.ids.length + ' call' + (data.ids.length === 1 ? '' : 's') +
        (data.groups.length > 8 ? '; top 8 of ' + data.groups.length + ' functions' : '') + ')</span>' +
        '<button class="tl-btn" data-child-open="' + root + '">Explore all children</button></div><div class="tl-crumbs">' +
        data.preview.map(group => '<button class="tl-crumb tl-child-preview" data-slice="' + group.longest +
            '" title="Open the longest recorded call of ' + esc(S[group.name]) + '"><span class="d">' + fmtDur(group.inclusive) +
            '</span><span class="tl-child-preview-name">' + esc(S[group.name]) + '</span>' + (group.ids.length > 1 ?
                '<span class="d">×' + group.ids.length + '</span>' : '') + '</button>').join('') + '</div>';
}
function childOrder(mode) {
    const data = childData(childView.root, textHits), sort = mode === 'groups' ? childView.groupSort : childView.callSort;
    const key = JSON.stringify([childView.name, childView.query, sort]), cached = childOrders[mode];
    if (cached && cached.data === data && cached.key === key) return cached;
    const query = childView.query.trim().toLowerCase();
    const groups = data.groups.filter(group => (childView.name === null || group.name === childView.name) && (!query || S[group.name].toLowerCase().includes(query)));
    const rows = mode === 'groups' ? groups : groups.flatMap(group => group.ids);
    const value = row => {
        if (sort === 'name') return S[mode === 'groups' ? row.name : sName[row]];
        if (mode === 'groups') return sort === 'calls' ? row.ids.length : row[sort];
        return sort === 'start' ? sStart[row] : sort === 'self' ? sSelf[row] : durOf(row);
    };
    rows.sort((left, right) => {
        const first = value(left), second = value(right);
        return (typeof first === 'string' ? first.localeCompare(second) : (sort === 'start' ? first - second : second - first)) ||
            (mode === 'groups' ? left.ids[0] - right.ids[0] : left - right);
    });
    const retain = cached && cached.key === key && cached.data.raw === data.raw && cached.data.hits === data.hits;
    childOrders[mode] = { data, key, rows, visible: Math.min(rows.length, retain ? cached.visible : CHILD_PAGE) };
    return childOrders[mode];
}
function childRows(rows, mode) {
    const name = value => '<span class="an-child-name" title="' + esc(value) + '">' + esc(value) + '</span>';
    if (mode === 'groups') return rows.map(group => '<tr><td>' + name(S[group.name]) + '</td><td>' + group.ids.length +
        '</td><td>' + fmtDur(group.inclusive) + '</td><td>' + fmtDur(group.self) + '</td><td>' + fmtDur(group.mean) + '</td><td>' + fmtDur(group.max) +
        '</td><td><button class="tl-btn" data-child-show="' + group.longest + '" aria-label="Show longest ' + esc(S[group.name]) +
        '">Longest</button> <button class="tl-btn" data-child-function="' + group.name + '" aria-label="List calls of ' + esc(S[group.name]) + '">Calls</button></td></tr>').join('');
    return rows.map(index => '<tr data-child-call="' + index + '"' + (index === sel ? ' class="an-child-selected"' : '') + '><td>' + name(nameOf(index)) +
        '</td><td>' + fmtDur(durOf(index)) + '</td><td>' + fmtDur(sSelf[index]) + '</td><td>' + fmtDur(sStart[index]) + '</td><td>' +
        (cpuOf(index) >= 0 ? cpuOf(index) : '—') + '</td><td>' + name(callerOf(index) || '—') + '</td><td><button class="tl-btn" data-child-show="' + index +
        '">Show</button> <button class="tl-btn" data-child-details="' + index + '">Details</button></td></tr>').join('');
}
function updateChildFooter(order) {
    elDetail.querySelector('.an-child-footer [role=status]').textContent = 'Showing ' + order.visible + ' of ' + order.rows.length +
        (childView.mode === 'groups' ? ' functions' : ' calls');
    elDetail.querySelector('[data-child-more]').disabled = order.visible >= order.rows.length;
}
function appendChildRows() {
    const order = childOrder(childView.mode), start = order.visible;
    order.visible = Math.min(start + CHILD_PAGE, order.rows.length);
    elDetail.querySelector('.an-child-table tbody').insertAdjacentHTML('beforeend', childRows(order.rows.slice(start, order.visible), childView.mode));
    updateChildFooter(order);
}
function showChildCall(index) {
    setSelection(index);
    revealTaskTrack(TRACKS[sTask[index]]);
    zoomTo(sStart[index], sEnd[index], index);
    elDetail.querySelectorAll('[data-child-call]').forEach(row => row.classList.toggle('an-child-selected', +row.dataset.childCall === index));
}
function exportChildrenCsv(mode) {
    const order = childOrder(mode), root = childView.root, task = TRACKS[sTask[root]];
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const prefix = ['parent_slice', 'parent_function', 'parent_task_index', 'parent_pid', 'parent_depth'];
    const suffix = ['mode', 'duration_basis', 'position_basis', 'relationship_basis', 'shared_filter', 'function_query', 'function_scope'];
    const columns = prefix.concat(mode === 'groups' ? ['function', 'calls', 'inclusive_' + unit, 'self_' + unit, 'mean_' + unit, 'max_' + unit,
        'first_slice', 'longest_slice'] : ['slice_index', 'function', 'depth', 'depth_gap', 'inclusive_' + unit, 'self_' + unit, 'start_' + unit, 'end_' + unit,
        'cpu', 'trace_line', 'exit_line', 'call_site', 'module', 'return_value', 'arguments'], suffix);
    function* rows() {
        for (const row of order.rows) {
            const values = mode === 'groups' ? [S[row.name], row.ids.length, row.inclusive / scale, row.self / scale, row.mean / scale, row.max / scale,
                row.ids[0], row.longest] : [row, nameOf(row), sDepth[row], sDepth[row] - sDepth[root] - 1, durOf(row) / scale, sSelf[row] / scale,
                sStart[row] / scale, sEnd[row] / scale, cpuOf(row), sLine[row], sEline && sEline[row] || '', callerOf(row) || '', modOf(row) || '',
                retOf(row) === null ? '' : retOf(row), argsOf(row) || ''];
            yield [root, nameOf(root), sTask[root], task.pid, sDepth[root]].concat(values, [MODE,
                MODE === 'count' ? 'call_order' : adjOn ? 'adjusted_estimate' : 'measured', MODE === 'count' ? 'call_order' : TIMED ? 'recorded_time' : 'packed_duration',
                'nearest_recorded_parent_not_complete_runtime_callees', window.fgFilterState && window.fgFilterState.text || '',
                childView.query, childView.name === null ? 'all' : S[childView.name]]);
        }
    }
    downloadTimelineCsv('funcgraph-children-' + mode + '.csv', columns, rows());
}
function showChildren(root) {
    if (root !== undefined && childView.root !== root) Object.assign(childView, { root, mode: 'groups', name: null, query: '', groupSort: 'inclusive', callSort: 'inclusive' });
    if (childView.root < 0 || childView.root >= N) return;
    const reuse = AN.kind === 'children' && elDetail.classList.contains('show') && elDetail.querySelector('.an-child-table');
    AN.kind = 'children';
    const order = childOrder(childView.mode), data = order.data;
    if (!reuse) {
        openDetail('<div class="tl-detail-head an-child-head"><span class="tl-detail-title">Direct recorded children</span>' +
            '<button class="tl-btn an-child-parent" data-child-parent title="' + esc(nameOf(childView.root)) + '">' + esc(nameOf(childView.root)) +
            '</button><span class="an-child-actions"><button class="tl-btn" data-child-csv="groups">Export groups CSV</button>' +
            '<button class="tl-btn" data-child-csv="calls">Export calls CSV</button><button class="tl-close" title="Close (Esc)">×</button></span></div>' +
            '<div class="an-child-tools"><button class="tl-btn" data-child-mode="groups">Groups</button><button class="tl-btn" data-child-mode="calls">Calls</button>' +
            '<label>Find <input type="search" data-child-query placeholder="Literal function name" spellcheck="false"></label>' +
            '<span class="an-child-scope"></span><button class="tl-btn" data-child-reset>All functions / reset</button></div>' +
            '<div class="an-child-status"></div><table class="tl-sum an-child-table"></table><div class="an-child-footer"><span role="status" aria-live="polite"></span>' +
            '<button class="tl-btn" data-child-more>Load more</button></div><div class="an-hint">Children use the nearest recorded real parent, including depth gaps, ' +
            'not a complete runtime callee inventory. Synthetic frames and grandchildren are excluded. The shared filter matches child calls, not the parent. ' +
            'Groups use function names; inclusive sums can overlap and are not a partition of the parent. Self excludes each child\'s own recorded descendants. ' +
            'Longest/Show highlights a call without leaving this panel; Details or the parent button opens slice details. ' +
            'Overhead updates duration/self estimates, never recorded positions. ' + (MODE === 'count' ? 'Values use call-order units, not nanoseconds. ' :
                TIMED ? '' : 'Positions use packed durations, not elapsed time. ') + 'CSV includes all sorted scoped rows, including unloaded pages.</div>');
        elDetail.querySelector('[data-child-parent]').onclick = () => select(childView.root, { reveal: true });
        elDetail.querySelector('[data-child-query]').addEventListener('input', event => { childView.query = event.target.value; showChildren(); });
        elDetail.querySelector('[data-child-reset]').onclick = () => { childView.query = ''; childView.name = null; showChildren(); };
        elDetail.querySelectorAll('[data-child-mode]').forEach(button => { button.onclick = () => { childView.mode = button.dataset.childMode; showChildren(); }; });
        elDetail.querySelectorAll('[data-child-csv]').forEach(button => { button.onclick = () => exportChildrenCsv(button.dataset.childCsv); });
        elDetail.querySelector('[data-child-more]').onclick = appendChildRows;
        elDetail.querySelector('.an-child-table').onclick = event => {
            const sort = event.target.closest('[data-child-sort]'), scope = event.target.closest('[data-child-function]');
            const show = event.target.closest('[data-child-show]'), details = event.target.closest('[data-child-details]');
            if (sort) {
                childView[childView.mode === 'groups' ? 'groupSort' : 'callSort'] = sort.dataset.childSort; showChildren();
                elDetail.querySelector('[data-child-sort="' + sort.dataset.childSort + '"]').focus({ preventScroll: true });
            } else if (scope) {
                childView.name = +scope.dataset.childFunction; childView.mode = 'calls'; showChildren();
                elDetail.querySelector('[data-child-mode=calls]').focus({ preventScroll: true });
            }
            else if (show) showChildCall(+show.dataset.childShow);
            else if (details) select(+details.dataset.childDetails, { reveal: true });
        };
        elDetail.scrollTop = 0;
    }
    elDetail.querySelector('[data-child-query]').value = childView.query;
    elDetail.querySelector('.an-child-scope').textContent = childView.name === null ? '' : S[childView.name];
    elDetail.querySelector('.an-child-scope').title = childView.name === null ? '' : S[childView.name];
    elDetail.querySelector('.an-child-status').textContent = data.ids.length + ' of ' + data.raw.ids.length + ' direct recorded calls match the shared filter · ' + data.groups.length + ' functions';
    elDetail.querySelectorAll('[data-child-mode]').forEach(button => {
        const selected = button.dataset.childMode === childView.mode;
        button.classList.toggle('on', selected); button.setAttribute('aria-pressed', String(selected));
    });
    const table = elDetail.querySelector('.an-child-table');
    if (!reuse || table._childOrder !== order) {
        const columns = childView.mode === 'groups' ? [['name', 'function'], ['calls', 'calls'], ['inclusive', 'inclusive sum'], ['self', 'self sum'],
            ['mean', 'mean'], ['max', 'max']] : [['name', 'function'], ['inclusive', 'inclusive'], ['self', 'self'], ['start', 'start'], ['', 'CPU'], ['', 'call site']];
        const sort = childView.mode === 'groups' ? childView.groupSort : childView.callSort;
        table.innerHTML = '<thead><tr>' + columns.map(([key, label]) => '<th' + (key ? ' aria-sort="' + (key === sort ?
            (key === 'name' || key === 'start' ? 'ascending' : 'descending') : 'none') + '"><button class="tl-sum-sort" data-child-sort="' + key + '">' + label + '</button>' : '>' + label) +
            '</th>').join('') + '<th>navigate</th></tr></thead><tbody>' + childRows(order.rows.slice(0, order.visible), childView.mode) + '</tbody>';
        table._childOrder = order;
        if (reuse) elDetail.scrollTop = 0;
    }
    let empty = elDetail.querySelector('.an-child-empty');
    if (!empty) { empty = document.createElement('div'); empty.className = 'an-hint an-child-empty'; table.after(empty); }
    empty.textContent = order.rows.length ? '' : 'No direct recorded children match the current scope. Reset or change the shared filter.';
    updateChildFooter(order);
    sizeDetailHead();
}
function subtree(root) {
    const tree = callTreeIndex(), descendants = [];
    let current = tree.first[root];
    while (current >= 0) {
        descendants.push(current);
        if (tree.first[current] >= 0) { current = tree.first[current]; continue; }
        while (current !== root && tree.next[current] < 0) current = sParent[current];
        if (current === root) break;
        current = tree.next[current];
    }
    return descendants;
}
// Partition of the measured wall time of slice i:
// own self | traced callees (on-CPU self) | hardirq/softirq that interrupted it | off-CPU (blocked / preempted)
function breakdown(i, descendants) {
    const cached = breakdownCache.get(i);
    if (cached) return cached;
    const ctx0 = sFlags[i] & FL.CTX, sub = descendants || subtree(i);
    const inIrq = new Set();
    let irq = 0, callees = 0, nCalls = sub.length;
    for (const k of sub) {
        const p = sParent[k], c = sFlags[k] & FL.CTX;
        if (inIrq.has(p)) { inIrq.add(k); continue; }
        if (c > ctx0) { irq += sEnd[k] - sStart[k]; inIrq.add(k); continue; }
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
    const ovh = D.overhead ? Math.min(own + callees, D.overhead.child * (nCalls - inIrq.size) + D.overhead.self) : 0;
    const result = { wall: sEnd[i] - sStart[i], own, callees, irq, offB, offP, ovh, nCalls };
    if (breakdownCache.size >= 256) breakdownCache.delete(breakdownCache.keys().next().value);
    breakdownCache.set(i, result);
    return result;
}
function breakdownHtml(i) {
    const b = breakdown(i);
    if (b.wall <= 0) return '';
    const parts = [['own code', b.own, '#3b6fd8'], ['traced callees', b.callees, '#8fb3ef'], ['interrupts / softirq', b.irq, '#f0a35a'],
        ['blocked', b.offB, '#9aa3ad'], ['preempted', b.offP, '#f2c14e']].filter(p => p[1] > 0);
    const accounted = parts.reduce((total, part) => total + part[1], 0);
    if (accounted < b.wall) parts.push(['unattributed', b.wall - accounted, '#b39ac9']);
    const total = Math.max(b.wall, accounted);
    let h = '<div class="tl-sec">Where the time went (' + fmtDur(b.wall) + (TIMED ? ' measured' : MODE === 'count' ? ' call-order units' : ' packed geometry, not elapsed time') + ')</div><div class="an-bd">';
    for (const p of parts) h += `<span style="width:${(100 * p[1] / total).toFixed(2)}%;background:${p[2]}" title="${p[0]} ${fmtDur(p[1])}"></span>`;
    h += '</div><div class="an-bd-legend">';
    for (const p of parts) h += `<span><i style="background:${p[2]}"></i>${p[0]} <b>${fmtDur(p[1])}</b> ${(100 * p[1] / total).toFixed(0)}%</span>`;
    if (accounted > b.wall) h += '<span class="d">Trace intervals overlap or are inconsistent: parts exceed the recorded interval by ' + fmtDur(accounted - b.wall) +
        '. Percentages use the category total, not the recorded duration.</span>';
    if (b.ovh > 0) h += `<span class="d">of own + callees, ≈${fmtDur(b.ovh)} is tracing overhead (${b.nCalls} traced call${b.nCalls === 1 ? '' : 's'} inside)</span>`;
    else if (b.nCalls > 0 && TIMED) h += `<span class="d">${b.nCalls} traced call${b.nCalls === 1 ? '' : 's'} inside add tracing overhead (no estimate)</span>`;
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
const SLOW_FAST_PAGE = 100;
let slowFastCache = null, slowFastView = null;
function cohortProfile(ids) {
    const functions = new Map();
    let irq = 0, offB = 0, offP = 0, own = 0, duration = 0;
    for (const root of ids) {
        const descendants = subtree(root), parts = breakdown(root, descendants);
        irq += parts.irq; offB += parts.offB; offP += parts.offP;
        own += Math.max(0, parts.own - (sSelfRaw[root] - sSelf[root]));
        duration += durOf(root);
        for (const index of descendants) {
            let entry = functions.get(sName[index]);
            if (!entry) { entry = { calls: 0, self: 0, example: index }; functions.set(sName[index], entry); }
            entry.calls++;
            entry.self += sSelf[index];
        }
    }
    return { ids, functions, irq: irq / ids.length, offB: offB / ids.length, offP: offP / ids.length,
        own: own / ids.length, duration: duration / ids.length };
}
function slowFastData() {
    const ids = distView.population.ids;
    if (slowFastCache && slowFastCache.ids === ids && slowFastCache.durations === sDur) return slowFastCache;
    if (ids.length < 6) return null;
    const fast = cohortProfile(ids.slice(-Math.floor(ids.length / 2)));
    const slow = cohortProfile(ids.slice(0, Math.ceil(ids.length / 10)));
    const functions = new Map();
    for (const [cohort, profile] of [['fast', fast], ['slow', slow]]) {
        for (const [name, entry] of profile.functions) {
            let row = functions.get(name);
            if (!row) {
                row = { kind: 'function', name: S[name], nameIndex: name, fastCalls: 0, slowCalls: 0,
                    fastSelf: 0, slowSelf: 0, fastExample: -1, slowExample: -1 };
                functions.set(name, row);
            }
            row[cohort + 'Calls'] = entry.calls / profile.ids.length;
            row[cohort + 'Self'] = entry.self / profile.ids.length;
            row[cohort + 'Example'] = entry.example;
        }
    }
    const rows = Array.from(functions.values());
    for (const [kind, name, field] of [['own', '(own code)', 'own'], ['irq', '(interrupts / softirq)', 'irq'],
        ['blocked', '(blocked off-CPU)', 'offB'], ['preempted', '(preempted)', 'offP']]) {
        rows.push({ kind, name, nameIndex: -1, fastCalls: kind === 'own' ? 1 : null, slowCalls: kind === 'own' ? 1 : null,
            fastSelf: fast[field], slowSelf: slow[field], fastExample: -1, slowExample: -1 });
    }
    for (const row of rows) row.delta = row.slowSelf - row.fastSelf;
    slowFastCache = { ids, durations: sDur, fast, slow, rows };
    slowFastView = { data: slowFastCache, sort: slowFastView ? slowFastView.sort : 'delta',
        ascending: slowFastView ? slowFastView.ascending : false, visible: SLOW_FAST_PAGE, ordered: null };
    return slowFastCache;
}
function slowFastOrder() {
    const data = slowFastData();
    if (!data) return [];
    if (slowFastView.ordered) return slowFastView.ordered;
    const key = slowFastView.sort, direction = slowFastView.ascending ? 1 : -1;
    slowFastView.ordered = data.rows.slice().sort((left, right) => {
        const order = key === 'name' ? (left.name < right.name ? -1 : left.name > right.name ? 1 : 0) : (left[key] || 0) - (right[key] || 0);
        return direction * order || (left.name < right.name ? -1 : left.name > right.name ? 1 : 0) || left.kind.localeCompare(right.kind);
    });
    return slowFastView.ordered;
}
function slowFastRows(rows) {
    return rows.map(row => '<tr data-sf-kind="' + row.kind + '" data-sf-name="' + row.nameIndex + '"><td><span class="an-slowfast-name" title="' + esc(row.name) + '">' + esc(row.name) + '</span></td><td>' +
        (row.fastCalls === null ? '' : row.fastCalls.toFixed(2)) + '</td><td>' + (row.slowCalls === null ? '' : row.slowCalls.toFixed(2)) +
        '</td><td>' + fmtDur(row.fastSelf) + '</td><td>' + fmtDur(row.slowSelf) + '</td><td class="' + (row.delta > 0 ? 'err' : '') + '">' +
        (row.delta > 0 ? '+' : '') + fmtDur(row.delta) + '</td><td>' +
        [['fast', row.fastExample], ['slow', row.slowExample]].filter(entry => entry[1] >= 0).map(([cohort, index]) =>
            '<button class="tl-btn" data-sf-example="' + index + '" aria-label="Show ' + cohort + ' example of ' + esc(row.name) + '">' + cohort + '</button>').join(' ') + '</td></tr>').join('');
}
function slowFastHtml() {
    if (!distView.slowFastOpen) return '<div class="an-slowfast-tools"><button class="tl-btn" data-act="slowfast">Slow vs fast: what do the slow calls do differently?</button></div>';
    const data = slowFastData();
    let html = '<div class="an-slowfast-tools"><span class="tl-sec">Slow vs fast</span><button class="tl-btn" data-act="slowfast">Hide comparison</button>';
    if (!data) return html + '</div><div class="an-hint">Slow vs fast needs at least 6 matching calls. Change the filter or call site to restore the population.</div>';
    html += '<button class="tl-btn" data-act="slowfast-csv">Export profile CSV</button><button class="tl-btn" data-act="slowfast-cohorts-csv">Export cohorts CSV</button></div>' +
        '<div class="an-hint an-slowfast-hint">Per selected call: slowest ' + data.slow.ids.length + ' (avg ' + fmtDur(data.slow.duration) + ') vs fastest ' +
        data.fast.ids.length + ' (avg ' + fmtDur(data.fast.duration) + '). Self follows Overhead; scheduling-context rows use recorded geometry.' +
        '<details><summary>Scope and interpretation</summary>Roots follow the function, shared filter and call site, not the duration bucket. Descendants include all real traced calls, even outside the shared filter; synthetic inline/untraced frames are excluded. ' +
        'Cohorts use descending duration, then original slice index; the fastest half and slowest ceil(10%) are disjoint, including ties. Recursive descendants contribute once per selected ancestor. ' +
        'Function self may include off-CPU time; context rows are separate observations, not additive with function rows. Own code is on-CPU self minus the estimated self overhead, clamped at zero. ' +
        'Differences are associations, not proof of a cause. Count/packed geometry is not elapsed scheduling time.</details></div>';
    const rows = slowFastOrder(), columns = [['name', 'function inside'], ['fastCalls', 'calls fast'], ['slowCalls', 'calls slow'],
        ['fastSelf', 'self / context fast'], ['slowSelf', 'self / context slow'], ['delta', 'Δ value']];
    html += '<table class="tl-sum an-tbl an-slowfast-table"><thead><tr>' + columns.map(([key, label]) => '<th data-sf-sort="' + key +
        '" aria-sort="' + (slowFastView.sort === key ? slowFastView.ascending ? 'ascending' : 'descending' : 'none') + '"><button class="tl-sum-sort">' + label +
        '</button></th>').join('') + '<th>examples</th></tr></thead><tbody>' + slowFastRows(rows.slice(0, slowFastView.visible)) + '</tbody></table>' +
        '<div class="an-slowfast-footer"><span role="status">Showing ' + Math.min(slowFastView.visible, rows.length) + ' of ' + rows.length +
        ' profile rows, including unchanged functions</span><button class="tl-btn" data-act="slowfast-more"' +
        (slowFastView.visible >= rows.length ? ' disabled' : '') + '>Load more profile rows</button></div>';
    return html;
}
function renderSlowFast() { elDetail.querySelector('.an-slowfast').innerHTML = slowFastHtml(); sizeDetailHead(); }
function appendSlowFastRows() {
    const rows = slowFastOrder(), start = slowFastView.visible;
    slowFastView.visible = Math.min(start + SLOW_FAST_PAGE, rows.length);
    elDetail.querySelector('.an-slowfast-table tbody').insertAdjacentHTML('beforeend', slowFastRows(rows.slice(start, slowFastView.visible)));
    elDetail.querySelector('.an-slowfast-footer [role=status]').textContent = 'Showing ' + slowFastView.visible + ' of ' + rows.length + ' profile rows, including unchanged functions';
    elDetail.querySelector('[data-act=slowfast-more]').disabled = slowFastView.visible >= rows.length;
}
function slowFastBasis(kind) {
    if (kind === 'function' || kind === 'own') return MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'raw_self';
    return TIMED ? 'recorded_scheduling_geometry' : MODE === 'count' ? 'call_order_geometry' : 'packed_geometry';
}
function exportSlowFastCsv(cohorts) {
    const data = slowFastData();
    if (!data) return;
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const filter = (window.fgFilterState || {}).text || '', site = AN.site || '';
    const selectionBasis = MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'measured_duration';
    const columns = cohorts ? ['cohort', 'slice_index', 'function', 'trace_line', 'task_index', 'pid', 'comm', 'caller',
        'duration_' + unit, 'self_' + unit, 'start_' + unit, 'end_' + unit, 'time_mode', 'duration_basis', 'filter', 'call_site_filter'] :
        ['kind', 'function', 'name_index', 'fast_calls_per_root', 'slow_calls_per_root', 'fast_self_per_root_' + unit, 'slow_self_per_root_' + unit,
        'delta_self_per_root_' + unit, 'fast_example_slice', 'slow_example_slice', 'fast_roots', 'slow_roots', 'population_calls',
        'mean_fast_duration_' + unit, 'mean_slow_duration_' + unit, 'time_mode', 'selection_basis', 'value_basis', 'filter', 'call_site_filter'];
    function* rows() {
        if (cohorts) {
            for (const [cohort, profile] of [['fast', data.fast], ['slow', data.slow]]) for (const index of profile.ids) {
                const task = TRACKS[sTask[index]];
                yield [cohort, index, nameOf(index), sLine[index], sTask[index], task.pid, task.comm, callerOf(index) || '',
                    durOf(index) / scale, sSelf[index] / scale, sStart[index] / scale, sEnd[index] / scale, MODE, selectionBasis, filter, site];
            }
        } else for (const row of slowFastOrder()) yield [row.kind, row.name, row.nameIndex < 0 ? '' : row.nameIndex,
            row.fastCalls === null ? '' : row.fastCalls, row.slowCalls === null ? '' : row.slowCalls, row.fastSelf / scale, row.slowSelf / scale,
            row.delta / scale, row.fastExample < 0 ? '' : row.fastExample, row.slowExample < 0 ? '' : row.slowExample,
            data.fast.ids.length, data.slow.ids.length, data.ids.length, data.fast.duration / scale, data.slow.duration / scale,
            MODE, selectionBasis, slowFastBasis(row.kind), filter, site];
    }
    downloadTimelineCsv('funcgraph-slowfast-' + (cohorts ? 'cohorts' : 'profile') + '-' + (MODE === 'count' ? 'count' : adjOn ? 'adjusted' : 'raw') + '.csv', columns, rows());
}

// ---- critical sections: irqs-off / preempt-off / BH-off ----
AN.secKind = 0;
AN.secSort = 'dur';
function sectionRows(rows) {
    return rows.map(record => {
        const raw = record.raw;
        return '<tr data-sec="' + record.index + '"><td class="' + (TIMED && record.duration > 100000 ? 'err' : '') + '">' + fmtDur(record.duration) + '</td><td>' + fmtDur(record.start) + '</td>' +
            '<td style="text-align:left">' + esc(trackLabel(TRACKS[record.track])) + '</td><td style="text-align:left">' +
            (raw[4] >= 0 ? esc(nameOf(raw[4])) : '<span class="d">top level</span>') + '</td><td style="text-align:left">' +
            (raw[5] >= 0 ? esc(nameOf(raw[5])) : '<span class="d">' + (raw[8] < 0 ? 'switched out / end' : 'interrupt') + '</span>') + '</td>' +
            (AN.secKind === 1 ? '<td>' + record.depth + '</td>' : '') +
            '<td style="text-align:left">' + windowTopHtml(windowProfile(record)) + '</td></tr>';
    }).join('');
}
function showSections() {
    AN.kind = 'sec';
    const data = sectionWindows(), order = windowOrder(data, AN.secKind, AN.secSort, 200);
    let h = '<div class="tl-detail-head an-window-head"><span class="tl-detail-title">Critical sections</span><span class="tl-seg an-seckind">';
    for (let kind = 0; kind < 3; kind++) h += '<button data-sk="' + kind + '" class="' + (kind === AN.secKind ? 'on' : '') + '">' + SEC_NAMES[kind] + ' (' + data.groups[kind].length + ')</button>';
    h += '</span>' + pill('mute', 'total ' + fmtDur(data.totals[AN.secKind])) + pill('warn', 'max ' + fmtDur(data.maxima[AN.secKind])) +
        (textHits ? pill('warn', 'filtered') : '') + (adjOn ? pill('warn', 'self: overhead subtracted') : '') + windowActions(order.rows.length);
    if (!D.caps.latency) {
        openDetail(h + '<div class="an-hint">Needs <code>options/latency-format</code> (irqs-off / preempt-depth / BH flags).</div>'); return;
    }
    h += windowTimeHint() + '<div class="an-hint" style="padding:6px 12px 0">Regions where a task ran with ' + SEC_NAMES[AN.secKind] + ' (task context; preempt depth above the trace baseline ' + (D.preempt_base || 0) + '). ' +
        'The flag changes inside untraced code, so start/end are the traced frames around the change. ' +
        'Self times are proportional overlap estimates from matching calls, not exact on-CPU or causal attribution. Overhead changes self estimates, not section positions or lengths.</div>';
    if (!order.rows.length) {
        openDetail(h + '<div class="an-hint">' + (textHits ? 'No sections overlap calls matching the current filter.' : 'No sections of this kind in this trace.') + '</div>'); return;
    }
    const columns = [['dur', 'duration'], ['start', 'start'], ['task', 'task', 1], [null, 'started in', 1], [null, 'ended in', 1]];
    if (AN.secKind === 1) columns.push(['pre', 'max depth']);
    columns.push(['top', 'top self (est.)', 1]);
    h += '<table class="tl-sum an-tbl an-window-table"><thead>' + windowTableHead(columns, AN.secSort, 'ssort') +
        '</thead><tbody>' + sectionRows(order.rows.slice(0, order.visible)) + '</tbody></table>' + windowFooter(order, true);
    openDetail(h);
}
elDetail.addEventListener('click', ev => {
    const sk = ev.target.closest('[data-sk]');
    if (sk) {
        AN.secKind = +sk.dataset.sk; showSections();
        elDetail.querySelector('[data-sk="' + AN.secKind + '"]').focus({ preventScroll: true });
        ev.stopImmediatePropagation(); return;
    }
    const sort = ev.target.closest('th[data-ssort]');
    if (sort) {
        AN.secSort = sort.dataset.ssort; showSections();
        elDetail.querySelector('th[data-ssort="' + AN.secSort + '"] button').focus({ preventScroll: true });
        ev.stopImmediatePropagation(); return;
    }
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
    const slowFastAction = ev.target.closest('.an-slowfast [data-act]');
    if (slowFastAction) {
        const action = slowFastAction.dataset.act;
        if (action === 'slowfast') {
            distView.slowFastOpen = !distView.slowFastOpen; renderSlowFast();
            elDetail.querySelector('[data-act=slowfast]').focus({ preventScroll: true });
        } else if (action === 'slowfast-more') appendSlowFastRows();
        else if (action === 'slowfast-csv' || action === 'slowfast-cohorts-csv') exportSlowFastCsv(action === 'slowfast-cohorts-csv');
        ev.stopImmediatePropagation(); return;
    }
    const slowFastSort = ev.target.closest('[data-sf-sort]');
    if (slowFastSort) {
        const key = slowFastSort.dataset.sfSort;
        slowFastView.ascending = slowFastView.sort === key ? !slowFastView.ascending : key === 'name';
        slowFastView.sort = key; slowFastView.visible = SLOW_FAST_PAGE; slowFastView.ordered = null;
        renderSlowFast(); elDetail.querySelector('[data-sf-sort="' + key + '"] button').focus({ preventScroll: true });
        ev.stopImmediatePropagation(); return;
    }
    const example = ev.target.closest('[data-sf-example]');
    if (example) {
        const index = +example.dataset.sfExample;
        const track = TRACKS[sTask[index]];
        if (track.hidden) { track.hidden = false; layout(); }
        setSelection(index); zoomTo(sStart[index], sEnd[index], index); draw();
        ev.stopImmediatePropagation();
    }
}, true);
