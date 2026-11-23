/* FuncGraph Timeline: analysis panels (error summary, duration distribution, scheduling latency).
 * Shares the __tlBoot() scope; rendered into the detail area via openDetail(). */
const AN = { kind: null, func: -1, off: null, errSort: 'count', latSort: 'dur' };

function matchOk(i) { return !textHits || textHits[i]; }
const TASK_PAGE = 100;
const TASK_CSV_DEFER_ROWS = 2048;
const TASK_KINDS = ['task', 'idle', 'cpu_fallback', 'all_fallback'];
const taskView = { query: '', kind: 'all', matching: false, only: null, sort: 'calls' };
let taskFacts = null, taskMatches = null, taskMatchBasis = null, taskSortCache = null, taskOrderCache = null;
let taskCsvJob = null;
function taskFactLabel(fact) { return fact.label ??= trackLabel(fact.task); }
function taskFactCpus(fact) { return fact.cpuList ??= Array.from(fact.cpus || fact.task.cpus).sort((left, right) => left - right); }
function taskFactCpuLabel(fact) { return fact.cpuLabel ??= taskFactCpus(fact).join(','); }
function taskFactSearch(fact) {
    return fact.search ??= [taskFactLabel(fact), ...fact.task.comms, ...taskFactCpus(fact).map(cpu => 'CPU ' + cpu)].join(' ').toLowerCase();
}
function taskData() {
    if (!taskFacts) {
        taskFacts = TRACKS.map(task => ({ task, calls: task.lo < 0 ? 0 : task.hi - task.lo, errors: 0, open: 0, start: null, end: null,
            cpus: null, cpuList: null, label: null, search: null, cpuLabel: null }));
        const observeTime = (fact, value) => {
            if (Number.isFinite(value)) {
                fact.start = fact.start === null ? value : Math.min(fact.start, value);
                fact.end = fact.end === null ? value : Math.max(fact.end, value);
            }
        };
        const observeCpu = (fact, cpu) => {
            if (!Number.isInteger(cpu) || cpu < 0 || fact.task.cpus.has(cpu)) return;
            (fact.cpus ??= new Set(fact.task.cpus)).add(cpu);
        };
        const observe = (id, start, end, cpu) => {
            const fact = taskFacts[id];
            if (!fact) return;
            observeTime(fact, start);
            if (end !== start) observeTime(fact, end);
            observeCpu(fact, cpu);
        };
        for (const fact of taskFacts) {
            const { task } = fact, bounds = task.rangeBounds;
            if (bounds) {
                for (let index = 0; index < bounds.length; index += 2) {
                    observeTime(fact, bounds[index]);
                    observeTime(fact, bounds[index + 1]);
                }
            } else if (fact.calls) {
                for (let index = task.lo; index < task.hi; index++) {
                    observeTime(fact, sStart[index]);
                    observeTime(fact, sEnd[index]);
                }
            }
            observeCpu(fact, task.cpu);
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
    }
    if (!taskMatches || taskMatchBasis !== textHits) {
        const countFor = fact => textHits ? fact.task.hits : fact.calls;
        if (!taskMatches || taskFacts.some((fact, index) => taskMatches.counts[index] !== countFor(fact))) {
            taskMatches = { counts: Uint32Array.from(taskFacts, countFor) };
        }
        taskMatchBasis = textHits;
    }
    return taskMatches;
}
function sortedTaskFacts(sort, data) {
    const basis = sort === 'matches' ? data : null;
    if (taskSortCache?.sort === sort && taskSortCache.basis === basis) return taskSortCache.rows;
    const value = fact => {
        switch (sort) {
            case 'name': return taskFactLabel(fact);
            case 'matches': return -data.counts[fact.task.id];
            case 'errors': return -fact.errors;
            case 'runs': return -fact.task.runs.length;
            case 'span': return fact.start === null ? Infinity : -(fact.end - fact.start);
            default: return -fact.calls;
        }
    };
    const rows = taskFacts.slice().sort((left, right) => {
        const first = value(left), second = value(right);
        return (typeof first === 'string' ? first.localeCompare(second) : first - second) || left.task.id - right.task.id;
    });
    taskSortCache = { sort, basis, rows };
    return rows;
}
function taskOrder() {
    const data = taskData(), { kind, matching, only, sort } = taskView;
    const query = taskView.query.trim().toLowerCase(), key = JSON.stringify([query, kind, matching, only, sort]);
    if (taskOrderCache && taskOrderCache.data === data && taskOrderCache.key === key) return taskOrderCache;
    const single = only === null ? null : taskFacts[only];
    let rows = only === null ? sortedTaskFacts(sort, data) : single ? [single] : [];
    if (query || kind !== 'all' || matching) {
        rows = rows.filter(fact => (kind === 'all' || fact.task.kind === +kind) &&
            (!matching || data.counts[fact.task.id] > 0) && (!query || taskFactSearch(fact).includes(query)));
    }
    taskOrderCache = { data, key, rows, page: 0 };
    return taskOrderCache;
}
function taskRows(rows) {
    return rows.map(fact => {
        const task = fact.task, label = taskFactLabel(fact), aliases = task.comms.join(', '), cpuLabel = taskFactCpuLabel(fact);
        return '<tr data-task-row="' + task.id + '"><td><span class="an-task-name" title="' + esc(label + (aliases ? ' · names: ' + aliases : '')) + '">' +
            esc(label) + '</span></td><td>' + esc(TASK_KINDS[task.kind] || 'unknown') + '</td><td><span class="an-task-name" title="' +
            esc(cpuLabel) + '">' + esc(cpuLabel || '—') + '</span></td><td>' + fact.calls + '</td><td>' + taskMatches.counts[task.id] +
            '</td><td>' + fact.errors + '</td><td>' + task.runs.length + '</td><td title="' + (fact.open ? 'Includes open waits to the report boundary; ' : '') +
            'span includes gaps, not CPU time">' + (fact.start === null ? '—' : (fact.open ? '≥ ' : '') + fmtDur(fact.end - fact.start)) +
            '</td><td><button class="tl-btn" data-task-show="' + task.id + '"' + (fact.start === null ? ' disabled' : '') +
            ' aria-label="Show ' + esc(label) + '">Show</button></td></tr>';
    }).join('');
}
function updateTaskFooter(order, syncPage = true) {
    const start = order.page * TASK_PAGE, end = Math.min(start + TASK_PAGE, order.rows.length);
    const pages = Math.ceil(order.rows.length / TASK_PAGE);
    const previous = elDetail.querySelector('[data-task-page="-1"]');
    const next = elDetail.querySelector('[data-task-page="1"]');
    const controls = elDetail.querySelector('.an-task-pages'), jump = controls.querySelector('.an-task-jump'), field = jump.elements.page;
    const csv = elDetail.querySelector('[data-act=tasks-csv]');
    const focused = document.activeElement;
    const status = elDetail.querySelector('.an-task-footer [role=status]');
    const text = order.rows.length ? 'Tasks ' + (start + 1) + '–' + end + ' of ' + order.rows.length : 'No matching tasks';
    if (status.textContent !== text) status.textContent = text;
    previous.disabled = order.page === 0;
    next.disabled = end >= order.rows.length;
    if (focused === next && next.disabled && !previous.disabled) previous.focus({ preventScroll: true });
    else if (focused === previous && previous.disabled && !next.disabled) next.focus({ preventScroll: true });
    controls.hidden = pages < 2;
    if (syncPage) {
        field.setCustomValidity('');
        field.value = order.page + 1;
        jump.querySelector('#tl-task-page-total').textContent = 'of ' + pages;
    }
    csv.disabled = !order.rows.length;
    if (controls.hidden && controls.contains(focused)) focusOverlayControl(elDetail, csv.disabled ? elDetail.querySelector('.tl-close') : csv);
    const filters = elDetail.querySelector('[data-task-filters]');
    const active = !!taskView.query.trim() || taskView.kind !== 'all' || taskView.matching || taskView.only !== null;
    filters.textContent = active ? 'Filters · on' : 'Filters';
    filters.classList.toggle('on', active);
}
function positionTaskPager() {
    const table = elDetail.querySelector('.an-task-table'), pager = elDetail.querySelector('.an-task-footer');
    if (!table || !pager) return;
    const flow = elDetail.classList.contains('tl-detail-short') || (compactToolbar.matches && !!elDetail.querySelector('.an-task-tools.open'));
    pager.classList.toggle('an-task-pager-flow', flow);
    positionTablePager(table, pager, flow && !pager.querySelector('.an-task-pages').hidden);
}
function changeTaskPage(direction, field = null) {
    if (AN.kind !== 'tasks') return;
    const order = taskOrder(), last = Math.max(0, Math.ceil(order.rows.length / TASK_PAGE) - 1);
    const entered = field?.value.trim(), target = field ? Number(entered) - 1 : order.page + direction;
    if (field && (!/^\d+$/.test(entered) || target < 0 || target > last)) {
        field.setCustomValidity('Enter a whole page number from 1 to ' + (last + 1) + '.');
        field.reportValidity();
        return;
    }
    const page = Math.max(0, Math.min(last, target));
    if (order.page === page) {
        if (field) field.value = page + 1;
        return;
    }
    order.page = page;
    const start = order.page * TASK_PAGE;
    const table = elDetail.querySelector('.an-task-table');
    const keepRightEdge = summaryPageAtRightEdge();
    table.querySelector('tbody').innerHTML = taskRows(order.rows.slice(start, start + TASK_PAGE));
    updateTaskFooter(order);
    if (keepRightEdge) elDetail.scrollLeft = elDetail.scrollWidth - elDetail.clientWidth;
    if (table.previousElementSibling?.classList.contains('an-task-pager-flow')) revealPagedTable(table, '.an-task-pages', true);
    else elDetail.scrollTop = 0;
}
function cancelTaskCsv() {
    const job = taskCsvJob;
    if (!job) return;
    taskCsvJob = null;
    job.button.textContent = 'Export CSV';
    job.status.hidden = true;
    job.status.textContent = '';
}
function queueTaskCsv(afterPaint = false) {
    const job = taskCsvJob;
    if (job) queueTimelineCsv(job, () => taskCsvJob === job, cancelTaskCsv, afterPaint);
}
function exportTasksCsv(event) {
    if (taskCsvJob) { cancelTaskCsv(); return; }
    const order = taskOrder(), unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const { query, kind, matching, only } = taskView, filter = window.fgFilterState?.text || '';
    const filename = 'funcgraph-tasks.csv';
    const columns = ['task_index', 'task', 'pid', 'kind', 'names', 'cpus', 'calls', 'matched_calls', 'error_origins_including_minor',
        'recorded_runs', 'open_waits', 'start_' + unit, 'end_' + unit, 'span_' + unit, 'span_is_lower_bound', 'mode', 'position_basis',
        'span_basis', 'query', 'kind_filter', 'matching_only', 'task_scope', 'shared_filter'];
    function* rows() {
        for (const fact of order.rows) {
            const task = fact.task;
            yield [task.id, task.comm, task.pid, TASK_KINDS[task.kind] || 'unknown', task.comms.join('; '), taskFactCpuLabel(fact),
                fact.calls, order.data.counts[task.id], fact.errors, task.runs.length, fact.open,
                fact.start === null ? '' : fact.start / scale, fact.end === null ? '' : fact.end / scale,
                fact.start === null ? '' : (fact.end - fact.start) / scale, fact.open ? 1 : 0, MODE,
                MODE === 'count' ? 'call_order' : TIMED ? 'recorded_time' : 'packed_duration', 'record_extent_including_gaps_not_cpu_time',
                query, kind, matching ? 1 : 0, only === null ? 'all' : only, filter];
        }
    }
    if (order.rows.length < TASK_CSV_DEFER_ROWS) { downloadTimelineCsv(filename, columns, rows()); return; }
    const button = event.currentTarget, status = elDetail.querySelector('.an-task-csv-status');
    taskCsvJob = { button, status, filename, order, query, filter, rows: rows(), total: order.rows.length, batchRows: 128,
        written: 0, progress: 0, parts: ['\ufeff' + columns.join(',') + '\r\n'], scheduled: false };
    button.textContent = 'Cancel CSV';
    status.hidden = false;
    status.textContent = timelineCsvProgressText(0);
    queueTaskCsv(true);
}
function navigateTask(id) {
    taskData();
    const fact = taskFacts[id];
    if (!fact || fact.start === null) return;
    setSelection(-1);
    range = null;
    const restoreChart = detailExpanded;
    if (restoreChart) setDetailExpanded(false);
    revealTaskTrack(fact.task);
    zoomTo(fact.start, fact.end, -1);
    if (restoreChart) focusTimelineControl();
}
function setTaskFiltersOpen(open) {
    const tools = elDetail.querySelector('.an-task-tools');
    const toggle = elDetail.querySelector('[data-task-filters]');
    tools.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
    positionTaskPager();
    elDetail.scrollTop = 0;
    if (open) focusOverlayControl(elDetail, tools.querySelector('[data-task-query]'));
    else toggle.focus({ preventScroll: true });
}
function taskFiltersKey(event) {
    if (event.key !== 'Escape' || AN.kind !== 'tasks' || !compactToolbar.matches) return false;
    const tools = elDetail.querySelector('.an-task-tools.open');
    if (!tools || !(event.target instanceof Element) ||
            !(tools.contains(event.target) || event.target.matches('[data-task-filters]'))) return false;
    setTaskFiltersOpen(false);
    return true;
}
function showTasks(only) {
    if (only !== undefined) {
        taskView.only = only;
        if (only !== null) { taskView.query = ''; taskView.kind = 'all'; taskView.matching = false; }
    }
    const reuse = AN.kind === 'tasks' && elDetail.classList.contains('show') && elDetail.querySelector('.an-task-table');
    AN.kind = 'tasks';
    const order = taskOrder();
    if (taskCsvJob && (taskCsvJob.order !== order || taskCsvJob.query !== taskView.query ||
        taskCsvJob.filter !== (window.fgFilterState?.text || ''))) cancelTaskCsv();
    if (!reuse) {
        openDetail('<div class="tl-detail-head an-task-head"><span class="tl-detail-title">Tasks</span>' +
            '<span class="an-task-scope"></span><span class="an-task-actions">' +
            '<button class="tl-btn an-task-filter-toggle" type="button" data-task-filters aria-controls="tlTaskFilters" aria-expanded="false">Filters</button>' +
            '<button type="button" class="tl-btn" data-act="tasks-csv">Export CSV</button>' +
            '<button class="tl-close" title="Close (Esc)">×</button></span>' +
            '<span class="an-task-csv-status" role="status" hidden></span></div>' +
            '<div class="an-task-tools" id="tlTaskFilters"><label>Find <input data-task-query type="search" placeholder="Name, old name, PID or CPU" spellcheck="false"></label>' +
            '<label>Kind <select data-task-kind><option value="all">All</option>' + TASK_KINDS.map((name, kind) =>
                '<option value="' + kind + '">' + name + '</option>').join('') + '</select></label>' +
            '<label><input type="checkbox" data-task-matching>Matching calls only</label><button class="tl-btn" data-act="tasks-reset">Reset</button></div>' +
            '<table class="tl-sum an-task-table"></table><div class="an-task-footer"><span role="status" aria-live="polite" aria-atomic="true"></span>' +
            '<div class="an-task-pages" role="group" aria-label="Task browsing controls" hidden>' +
            '<button class="tl-btn" type="button" data-task-page="-1" aria-label="Previous page of tasks">' +
            '<span class="an-window-page-label">Previous</span><span class="an-window-page-arrow" aria-hidden="true">‹</span></button>' +
            '<form class="an-task-jump"><label>Page <input type="text" name="page" inputmode="numeric" autocomplete="off"' +
            ' aria-describedby="tl-task-page-total"></label><span id="tl-task-page-total"></span>' +
            '<button type="submit" class="tl-btn" aria-label="Go to page">Go</button></form>' +
            '<button class="tl-btn" type="button" data-task-page="1" aria-label="Next page of tasks">' +
            '<span class="an-window-page-label">Next</span><span class="an-window-page-arrow" aria-hidden="true">›</span></button></div></div>' +
            '<div class="an-hint">All recorded task identities, including scheduler-only tasks. Calls and errors exclude synthetic frames; errors include minor origins. ' +
            'The shared filter changes matches, not raw counts. Trace span includes gaps: not CPU time, running duration or task lifetime. ' +
            'Open waits extend to the report boundary (≥, lower bound), not a known resume. ' +
            (TIMED ? 'Positions use recorded time.' : MODE === 'count' ? 'Positions use call-order units, not elapsed time.' : 'Positions use packed durations; timing gaps are unknown.') +
            ' Show fits the task extent; CPU clicks preserve the current time range. Empty spans have no recorded time boundary. Pages contain at most 100 tasks; CSV includes every matching task, not just this page.</div>');
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
        elDetail.querySelector('[data-task-filters]').onclick = event =>
            setTaskFiltersOpen(event.currentTarget.getAttribute('aria-expanded') !== 'true');
        for (const button of elDetail.querySelectorAll('[data-task-page]')) {
            button.onclick = () => changeTaskPage(+button.dataset.taskPage);
        }
        const jump = elDetail.querySelector('.an-task-jump');
        jump.onsubmit = event => { event.preventDefault(); changeTaskPage(0, jump.elements.page); };
        jump.elements.page.oninput = event => event.target.setCustomValidity('');
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
    const newOrder = !reuse || table._taskOrder !== order;
    if (newOrder) {
        const columns = [['name', 'task / PID'], ['', 'kind'], ['', 'CPUs'], ['calls', 'calls'], ['matches', 'matches'],
            ['errors', 'errors'], ['runs', 'runs'], ['span', 'trace span']];
        table.innerHTML = '<thead><tr>' + columns.map(([key, label]) => '<th' + (key ? ' aria-sort="' + (taskView.sort === key ?
            (key === 'name' ? 'ascending' : 'descending') : 'none') + '"><button class="tl-sum-sort" data-task-sort="' + key + '">' + label + '</button>' : '>' + label) +
            '</th>').join('') + '<th>navigate</th></tr></thead><tbody>' +
            taskRows(order.rows.slice(order.page * TASK_PAGE, (order.page + 1) * TASK_PAGE)) + '</tbody>';
        table._taskOrder = order;
        if (reuse && !elDetail.querySelector('.an-task-tools').contains(document.activeElement)) elDetail.scrollTop = 0;
    }
    updateTaskFooter(order, newOrder);
    positionTaskPager();
    sizeDetailHead();
}
// ---- 1. error summary: syscall -> origin -> errno ----
const ERROR_PAGE = 200, ATOMIC_PAGE = 100;
const ERROR_FATES = ['handled', 'to_user', 'unknown'];
const ERROR_ENTRIES = new Map();
const SYSCALL_ENTRY = /^(?:__(?:arm64|x64|ia32|x32|riscv|s390x?|powerpc|loongarch|se)_)?(?:compat_)?sys_(\w+)$|^__do_sys_(\w+)$/;
let errorCache = null, atomicCache = null, errorView = null;
let userErrorCache = null;
function userErrorData() {
    if (userErrorCache?.hits === textHits) return userErrorCache;
    const groups = [], grouped = new Map();
    let total = 0;
    for (const [index, failure] of USER_ERRORS) {
        if (textHits && !textHits[index]) continue;
        const key = JSON.stringify([sName[index], failure.rule, failure.summary]);
        let group = grouped.get(key);
        if (!group) {
            group = { id: groups.length, name: sliceLabel(index), failure, first: index, ids: [], position: -1 };
            grouped.set(key, group); groups.push(group);
        }
        group.ids.push(index); total++;
    }
    for (const group of groups) group.ids.sort((left, right) => sStart[left] - sStart[right] || left - right);
    const rows = groups.slice().sort((left, right) => right.ids.length - left.ids.length || left.first - right.first);
    userErrorCache = { hits: textHits, groups, rows, total, page: 0, pageSize: ERROR_PAGE };
    return userErrorCache;
}
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
    errorCache = { hits: textHits, minors: showMinor, groups, total, minor, order: { key: null, rows: [], page: 0, pageSize: ERROR_PAGE } };
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
    atomicCache = { hits: textHits, groups, rows, total, page: 0, pageSize: ATOMIC_PAGE };
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
        order.page = 0;
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
function userErrorRows(rows) {
    return rows.map(group => '<tr data-user-error-group="' + group.id + '" data-slice="' + group.ids[Math.max(0, group.position)] + '">' +
        '<td style="text-align:left">' + esc(group.name) + '</td><td style="text-align:left" class="err" title="' + esc(group.failure.note) + '">' +
        esc(group.failure.summary) + '</td><td>' + group.ids.length + '</td>' + errorNavigation(group) + '</tr>').join('');
}
function errorFooter(atomic, user = false) {
    const data = user ? userErrorData() : atomic ? atomicData() : errorOrder(), kind = user ? 'user-errors' : atomic ? 'atomic' : 'errors';
    return '<div class="an-error-footer" data-error-footer="' + kind + '"><span role="status" aria-live="polite" aria-atomic="true">' +
        errorPageStatus(data, atomic, user) + '</span>' + (data.rows.length > data.pageSize ?
        '<span class="an-error-pages" role="group" aria-label="' + (user ? 'User API' : atomic ? 'Atomic' : 'Error') + ' group pages">' +
        '<button class="tl-btn" type="button" data-act="' + kind + '-prev"' + (data.page ? '' : ' disabled') + '>Previous</button>' +
        '<button class="tl-btn" type="button" data-act="' + kind + '-next"' +
        ((data.page + 1) * data.pageSize >= data.rows.length ? ' disabled' : '') + '>Next</button></span>' : '') + '</div>';
}
function errorPageStatus(data, atomic, user = false) {
    const start = data.page * data.pageSize, end = Math.min(start + data.pageSize, data.rows.length);
    return 'Showing ' + (data.rows.length ? start + 1 + '–' + end : '0') + ' of ' + data.rows.length + (user ? ' user API groups' : atomic ? ' atomic groups' : ' error groups');
}
function errorPageRows(data) {
    const start = data.page * data.pageSize;
    return data.rows.slice(start, start + data.pageSize);
}
function positionErrorPagers() {
    for (const footer of elDetail.querySelectorAll('.an-error-footer:has(.an-error-pages)'))
        positionTablePager(footer.parentElement.querySelector('table'), footer);
}
function changeErrorPage(atomic, direction, user = false) {
    const data = user ? userErrorData() : atomic ? atomicData() : errorOrder(), last = Math.max(0, Math.ceil(data.rows.length / data.pageSize) - 1);
    data.page = Math.max(0, Math.min(last, data.page + direction));
    const table = elDetail.querySelector(user ? '.an-user-error-table' : atomic ? '.an-atomic-table' : '.an-error-table');
    table.querySelector('tbody').innerHTML = (user ? userErrorRows : atomic ? atomicRows : errorRows)(errorPageRows(data));
    const kind = user ? 'user-errors' : atomic ? 'atomic' : 'errors', footer = elDetail.querySelector('[data-error-footer="' + kind + '"]');
    footer.querySelector('[role=status]').textContent = errorPageStatus(data, atomic, user);
    const previous = footer.querySelector('[data-act="' + kind + '-prev"]'), next = footer.querySelector('[data-act="' + kind + '-next"]');
    const focused = document.activeElement;
    previous.disabled = data.page === 0;
    next.disabled = data.page === last;
    if (focused === next && next.disabled && !previous.disabled) previous.focus({ preventScroll: true });
    else if (focused === previous && previous.disabled && !next.disabled) next.focus({ preventScroll: true });
    if (!elDetail.classList.contains('tl-detail-short')) revealDetailTable(table);
}
function jumpErrorOccurrence(row, direction) {
    const atomic = row.hasAttribute('data-atomic-group');
    const user = row.hasAttribute('data-user-error-group'), single = atomic || user;
    const group = user ? userErrorData().groups[+row.dataset.userErrorGroup] : atomic ? atomicData().groups[+row.dataset.atomicGroup] : errorData().groups[+row.dataset.chains];
    const count = group.ids.length;
    group.position = group.position < 0 ? (direction > 0 ? 0 : count - 1) : (group.position + direction + count) % count;
    const occurrence = group.ids[group.position], chain = single ? null : CHAINS[occurrence];
    const origin = single ? occurrence : chain[0], top = single ? origin : chain[1], track = TRACKS[sTask[origin]];
    if (track.hidden) { track.hidden = false; layout(); }
    zoomTo(sStart[top], sEnd[top], origin);
    const errorCode = atomic ? undefined : user ? -occurrence - 1 : occurrence;
    setSelection(origin, errorCode); revealRow(origin); draw();
    row.querySelector('[data-error-position]').textContent = group.position + 1 + ' / ' + count;
    if (single) row.dataset.slice = origin;
    else row.dataset.chain = occurrence;
    toast('occurrence ' + (group.position + 1) + ' / ' + count);
}
const ERROR_CSV_DEFER_ROWS = 2048;
const ERROR_CSV_KINDS = new Map([
    ['errors-csv', 'groups'], ['error-occurrences-csv', 'occurrences'], ['atomic-csv', 'atomic'], ['user-errors-csv', 'user']
]);
let errorCsvJob = null;
function cancelErrorCsv() {
    const job = errorCsvJob;
    if (!job) return;
    errorCsvJob = null;
    job.button.textContent = job.label;
    job.other.forEach(button => { button.disabled = false; });
    job.status.hidden = true;
    job.status.textContent = '';
}
function queueErrorCsv(afterPaint = false) {
    const job = errorCsvJob;
    if (job) queueTimelineCsv(job, () => errorCsvJob === job, cancelErrorCsv, afterPaint);
}
function downloadErrorCsv(button, filename, columns, rows, total) {
    if (total < ERROR_CSV_DEFER_ROWS) { downloadTimelineCsv(filename, columns, rows); return; }
    const status = button.nextElementSibling;
    const other = Array.from(elDetail.querySelectorAll('button[data-act$="-csv"]:enabled')).filter(control => control !== button);
    errorCsvJob = { button, status, label: button.textContent, other, filename, rows, total, batchRows: 128,
        written: 0, progress: 0, parts: ['\ufeff' + columns.join(',') + '\r\n'], scheduled: false };
    button.textContent = 'Cancel CSV';
    other.forEach(control => { control.disabled = true; });
    status.hidden = false;
    status.textContent = timelineCsvProgressText(0);
    queueErrorCsv(true);
}
function exportUserErrorsCsv(button) {
    const { rows: groups, total } = userErrorData();
    const filter = (window.fgFilterState || {}).text || '';
    function* rows() {
        for (const group of groups) for (const index of group.ids) {
            const task = TRACKS[sTask[index]], detail = D.uftrace.user_details[sLine[index]], failure = group.failure;
            yield [group.id, index, nameOf(index), detail.retval, failure.rule, failure.errno ?? '', failure.summary, failure.note,
                trackLabel(task), task.pid, sLine[index], sEline?.[index] || '', filter];
        }
    }
    downloadErrorCsv(button, 'funcgraph-user-api-failures.csv', ['group_id', 'slice_index', 'function', 'recorded_return', 'symbol_matched_rule',
        'direct_error_number', 'summary', 'evidence_limitations', 'task', 'pid', 'entry_line', 'exit_line', 'filter'], rows(), total);
}
function exportErrorsCsv(button, kind) {
    if (errorCsvJob) {
        if (errorCsvJob.button === button) cancelErrorCsv();
        return;
    }
    if (kind === 'user') { exportUserErrorsCsv(button); return; }
    const data = kind === 'atomic' ? atomicData() : errorData();
    const groups = kind === 'atomic' ? data.rows : errorOrder().rows;
    const total = kind === 'groups' ? groups.length : data.total;
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
        for (const group of groups) {
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
    downloadErrorCsv(button, 'funcgraph-errors-' + kind + '.csv', columns, rows(), total);
}
function errorCsvControl(action, label, disabled = false) {
    return '<span class="an-error-export"><button class="tl-btn" type="button" data-act="' + action + '"' +
        (disabled ? ' disabled' : '') + '>' + label + '</button><span class="an-error-csv-status" role="status" hidden></span></span>';
}
function showErrors() {
    const wasOpen = AN.kind === 'errors' && elDetail.classList.contains('show');
    const focusedAction = wasOpen && elDetail.contains(document.activeElement) ? document.activeElement.dataset.act : null;
    AN.kind = 'errors';
    const data = errorData(), order = errorOrder(), atomic = atomicData(), user = userErrorData();
    const resetScroll = !wasOpen || !errorView || errorView.data !== data;
    const sorted = errorView && errorView.sort !== AN.errSort;
    let html = '<div class="tl-detail-head an-error-head"><span class="tl-detail-title">Errors</span>' +
        (HAS_KERNEL ? pill('err', data.total + ' error chains') + pill('mute', data.groups.length + ' groups') : '') +
        (user.total ? pill('warn', user.total + ' user API failure hints') : '') +
        (atomic.total ? pill('warn', atomic.total + ' atomic calls') : '') + (textHits ? pill('warn', 'filtered') : '') +
        (!HAS_KERNEL || D.btf || D.caps.retval_btf ? '' : pill('warn', 'no BTF: unverified')) +
        (data.minor || showMinor ? '<label class="an-minor" title="Handled at once; this function never propagated a negative return in the trace">' +
            '<input type="checkbox" data-act="minor"' + (showMinor ? ' checked' : '') + '>Show ' + data.minor + ' minor</label>' : '') +
        '<span class="an-error-actions">' + (HAS_KERNEL ? errorCsvControl('errors-csv', 'Export groups CSV', !data.groups.length) : '') +
        '<button class="tl-close" title="Close (Esc)">×</button></span></div><div class="an-error-groups">';
    if (atomic.total) {
        html += '<div class="an-error-group"><div class="an-error-section"><span class="tl-sec">Sleeping functions called in atomic context</span>' +
            errorCsvControl('atomic-csv', 'Export atomic CSV') + '</div>' +
            '<table class="tl-sum an-tbl an-atomic-table"><thead><tr><th>function</th><th>reason</th><th>caller</th><th>calls</th><th>occurrence</th></tr></thead><tbody>' +
            atomicRows(errorPageRows(atomic)) + '</tbody></table>' + errorFooter(true) + '</div>';
    }
    if (user.total) {
        html += '<div class="an-error-group"><div class="an-error-section"><span class="tl-sec">User API failure hints</span>' +
            errorCsvControl('user-errors-csv', 'Export user API CSV') + '</div>' +
            '<div class="an-hint an-error-hint">Symbol-matched API conventions, not captured errno or verified error propagation. ' +
            'A return of -1 is not -EPERM. Use <code>space:user AND result:failure</code> in the shared filter.</div>' +
            '<table class="tl-sum an-tbl an-user-error-table"><thead><tr><th>function</th><th>failure hint</th><th>calls</th><th>occurrence</th></tr></thead><tbody>' +
            userErrorRows(errorPageRows(user)) + '</tbody></table>' + errorFooter(false, true) + '</div>';
    }
    if (data.groups.length) {
        const columns = [['sys', 'syscall / entry', 1], ['origin', 'origin', 1], ['errno', 'errno'], ['count', 'count'],
            ['user', 'to user'], ['h', 'handled'], ['unknown', 'unknown'], ['t', 'tasks'], ['x', 'chain']];
        html += '<div class="an-error-group"><div class="an-error-section"><span class="tl-sec">Kernel error chains</span>' +
            errorCsvControl('error-occurrences-csv', 'Export occurrences CSV') + '</div>' +
            '<table class="tl-sum an-tbl an-error-table"><thead><tr>' + columns.map(([key, label, left]) => '<th data-esort="' + key +
            '" aria-sort="' + (key === AN.errSort ? (errorSortAscending(key) ? 'ascending' : 'descending') : 'none') + '"' +
            (left ? ' style="text-align:left"' : '') + '><button class="tl-sum-sort">' + label + '</button></th>').join('') +
            '<th>occurrence</th></tr></thead><tbody>' + errorRows(errorPageRows(order)) + '</tbody></table>' + errorFooter(false) + '</div>';
    }
    html += '</div>';
    if (!data.groups.length && !user.total) {
        html += '<div class="an-hint an-error-hint">' + (textHits ? 'No visible error chains match the current filter.' :
            !HAS_KERNEL ? 'No recognized user API failures. Unknown API conventions and missing return values remain unclassified.' :
            D.caps.retval || CHAINS.length ? 'No visible error returns found.' : 'Error paths need <code>options/funcgraph-retval</code>.') + '</div>';
    }
    html += '<div class="an-hint an-error-hint">Click a row for the next occurrence; Shift-click or use ◀ for the previous one. ' +
        'Every occurrence is accessible, not just the first 50. Unknown means the trace cannot establish the final outcome. ' +
        'The shared filter matches error origins, user API hints or atomic calls; exports include unloaded rows. Durations follow Overhead, positions stay as recorded.</div>';
    openDetail(html);
    positionErrorPagers();
    if (resetScroll) elDetail.scrollTop = 0;
    else if (sorted && order.rows.length) revealDetailTable(elDetail.querySelector('.an-error-table'));
    if (focusedAction && /^(?:(?:errors|atomic|user-errors)-(?:prev|next|csv)|error-occurrences-csv)$/.test(focusedAction)) {
        const control = elDetail.querySelector('[data-act="' + focusedAction + '"]');
        (control && !control.disabled ? control : elDetail.querySelector('.tl-close'))?.focus({ preventScroll: true });
    }
    errorView = { data, sort: AN.errSort };
}

// ---- 2. duration distribution of one function ----
let distCache = null, distView = null, distFrame = 0, distHistogramOpen = true;
const DIST_PAGE = 50, DIST_SITE_PAGE = 12, DIST_SITE_HISTOGRAM_LIMIT = 4;
function distQuantile(ids, fraction) {
    return ids.length ? durOf(ids[ids.length - 1 - Math.round(fraction * (ids.length - 1))]) : 0;
}
function* distributionData(name, hits, durations) {
    if (distCache && distCache.name === name && distCache.hits === hits && distCache.durations === durations) return distCache;
    const ids = yield* collectNamedCallsSteps(searchPostingIndex, sName, N, name, hits);
    const compare = (left, right) => durations[right] - durations[left] || left - right;
    if (ids.length <= PREPARE_BATCH) ids.sort(compare);
    else yield* sortTimelineRow(ids, compare);
    const sites = new Map(), siteRows = [];
    let total = 0;
    for (let position = 0; position < ids.length; position++) {
        if (position && position % PREPARE_BATCH === 0) yield;
        const index = ids[position], duration = durations[index];
        const site = callerOf(index) || '?';
        let group = sites.get(site);
        if (!group) { group = { site, ids: [], total: 0 }; sites.set(site, group); siteRows.push(group); }
        group.ids.push(index);
        group.total += duration;
        total += duration;
    }
    const compareSites = (left, right) => right.ids.length - left.ids.length ||
        (left.site < right.site ? -1 : left.site > right.site ? 1 : 0);
    if (siteRows.length <= PREPARE_BATCH) siteRows.sort(compareSites);
    else yield* sortTimelineRow(siteRows, compareSites);
    distCache = { name, hits, durations, ids, total, sites, siteRows, histogram: null, siteHistograms: new Map() };
    return distCache;
}
function* distributionHistogram(ids, durations) {
    const low = Math.log10(Math.max(1, ids.length ? durations[ids[ids.length - 1]] : 0));
    const high = Math.log10(Math.max(2, ids.length ? durations[ids[0]] : 0)) + 1e-9;
    const count = Math.max(8, Math.min(40, Math.ceil((high - low) * 10)));
    const width = (high - low) / count;
    const buckets = Array.from({ length: count }, () => ({ start: 0, end: 0, error: false }));
    const bucketAt = offset => Math.max(0, Math.min(count - 1, Math.floor((Math.log10(Math.max(1, durations[ids[offset]])) - low) / width)));
    let maxCount = 1;
    for (let start = 0; start < ids.length;) {
        const index = bucketAt(start);
        let end = ids.length;
        if (index > 0) {
            let first = start + 1;
            while (first < end) {
                const middle = first + Math.floor((end - first) / 2);
                if (bucketAt(middle) === index) first = middle + 1;
                else end = middle;
            }
        }
        const bucket = buckets[index];
        bucket.start = start;
        bucket.end = end;
        if (D.errs.length) for (let offset = start; offset < end && !bucket.error; offset++) {
            if (offset !== start && (offset - start) % PREPARE_BATCH === 0) yield;
            bucket.error = eRole[ids[offset]] === 0;
        }
        maxCount = Math.max(maxCount, end - start);
        start = end;
    }
    return { low, high, width, buckets, maxCount };
}
function* distributionHistogramFor(data, population) {
    if (population === data) {
        if (!data.histogram) data.histogram = yield* distributionHistogram(data.ids, data.durations);
        return data.histogram;
    }
    if (!population.ids.length) return yield* distributionHistogram(population.ids, data.durations);
    const cache = data.siteHistograms;
    const cached = cache.get(population);
    const histogram = cached || (yield* distributionHistogram(population.ids, data.durations));
    if (cached) cache.delete(population);
    cache.set(population, histogram);
    if (cache.size > DIST_SITE_HISTOGRAM_LIMIT) cache.delete(cache.keys().next().value);
    return histogram;
}
function distCallRange() {
    return distView.bucket < 0 ? { start: 0, end: distView.population.ids.length } : distView.histogram.buckets[distView.bucket];
}
function distPageIds() {
    const { start, end } = distCallRange(), first = start + distView.page * DIST_PAGE;
    return distView.population.ids.slice(first, Math.min(first + DIST_PAGE, end));
}
function distBucketBounds(index) {
    const histogram = distView.histogram;
    return [index ? Math.pow(10, histogram.low + index * histogram.width) : 0,
        Math.pow(10, histogram.low + (index + 1) * histogram.width)];
}
function distBucketLabel(index) {
    const bounds = distBucketBounds(index);
    const { start, end } = distView.histogram.buckets[index];
    return (index ? fmtDur(bounds[0]) + ' – < ' : '< ') + fmtDur(bounds[1]) + ' (' + (end - start) + ' calls)';
}
function distHasSites(data) {
    return !!sCaller && (data.siteRows.length >= 2 || AN.site !== null);
}
function distJumpMenu(data, count) {
    const sites = distHasSites(data), slowFast = count >= 6 || distView.slowFastOpen;
    if (!sites && !slowFast) return '';
    return '<select class="an-dist-jump" data-act="dist-jump" aria-label="Jump to distribution section"><option value="">Jump to…</option>' +
        (count ? '<option value="calls">Slowest calls</option>' : '') +
        (slowFast ? '<option value="slowfast">Slow vs fast</option>' : '') +
        (sites ? '<option value="sites">By call site</option>' : '') + '</select>';
}
function jumpDistSection(menu) {
    const selector = { calls: '[data-act=dist-bucket]', slowfast: '[data-act=slowfast]', sites: '.an-site-heading' }[menu.value];
    menu.value = '';
    if (!selector) return;
    const target = elDetail.querySelector(selector);
    if (!target) return;
    revealDetailTable(target);
    target.focus({ preventScroll: true });
}
function showDist(nameIdx) {
    AN.kind = 'dist';
    if (AN.func !== nameIdx) AN.site = null;
    AN.func = nameIdx;
    const {site} = AN, hits = textHits, durations = sDur;
    const steps = (function* () {
        const data = yield* distributionData(nameIdx, hits, durations);
        const population = site !== null ? data.sites.get(site) || { ids: [], total: 0 } : data;
        const histogram = yield* distributionHistogramFor(data, population);
        return {data, population, histogram};
    })();
    const initial = advanceDetailWork(steps, 3);
    if (initial.done) { paintDistribution(nameIdx, initial.value); return; }
    const focused = elDetail.contains(document.activeElement);
    openDetail('<div class="tl-detail-head an-dist-head"><div class="an-dist-heading"><span class="tl-detail-title" title="' +
        esc(S[nameIdx]) + '">' + esc(symbolLabel(S[nameIdx])) + '</span></div><span class="an-dist-actions">' +
        '<button class="tl-close" title="Close (Esc)">×</button></span></div>' +
        '<div class="an-hint an-dist-pending" role="status">Preparing duration distribution…</div>');
    elDetail.scrollTop = 0;
    if (focused) elDetail.querySelector('.an-dist-head .tl-close').focus({preventScroll: true});
    deferDetailWork('distribution', steps, result => paintDistribution(nameIdx, result));
}
function paintDistribution(nameIdx, {data, population, histogram}) {
    const focusedAction = elDetail.contains(document.activeElement) ? document.activeElement.dataset.act : null;
    const replacementControl = elDetail.querySelector('.an-dist-pending') && elDetail.contains(document.activeElement) ?
        (document.activeElement.matches('[data-detail-expand]') ? '[data-detail-expand]' : '.an-dist-head .tl-close') : null;
    const keepSlowFast = distView && distView.data.name === nameIdx && distView.slowFastOpen;
    const changedPopulation = !distView || distView.data !== data || distView.site !== AN.site;
    if (changedPopulation) {
        if (slowFastView) slowFastView.page = 0;
        const sitePage = distView?.data === data ? distView.sitePage :
            AN.site === null ? 0 : Math.floor(Math.max(0, data.siteRows.indexOf(population)) / DIST_SITE_PAGE);
        distView = { data, site: AN.site, population, histogram,
            bucket: -1, page: 0, sitePage, slowFastOpen: !!keepSlowFast };
    }
    const ids = population.ids;
    AN.distIds = ids;
    const quantile = fraction => distQuantile(ids, fraction);
    const statistic = value => ids.length ? fmtDur(value) : '—';
    let h = '<div class="tl-detail-head an-dist-head"><div class="an-dist-heading"><span class="tl-detail-title" title="' + esc(S[nameIdx]) + '">' + esc(symbolLabel(S[nameIdx])) + '</span>' +
        pill('mute', ids.length + ' calls') + pill('mute', 'total ' + fmtDur(population.total)) + (textHits ? pill('warn', 'filtered') : '') +
        (adjOn ? pill('warn', 'overhead subtracted') : '') + (AN.site !== null ? '<span class="tl-pill warn" title="' + esc(AN.site) + '">from ' + esc(AN.site) + '</span>' : '') +
        '</div><span class="an-dist-actions">' + distJumpMenu(data, ids.length) + (AN.site !== null ? '<button class="tl-btn" data-act="dist-reset-site">All call sites</button>' : '') +
        '<button class="tl-btn" type="button" data-act="dist-histogram" aria-label="Histogram" aria-controls="tl-dist-histogram" aria-expanded="' + distHistogramOpen +
        '" title="' + (distHistogramOpen ? 'Hide histogram; keep all statistics and calls' : 'Show histogram for the full statistical population') + '">Histogram</button>' +
        '<button class="tl-close" title="Close (Esc)">×</button></span></div>';
    h += '<div class="an-dist-main"><div class="an-dist' + (distHistogramOpen ? '' : ' an-dist-compact') + '"><div class="tl-kv">' +
        kv('min', statistic(quantile(0))) + kv('p50', statistic(quantile(0.5))) + kv('p90', statistic(quantile(0.9))) + kv('p99', statistic(quantile(0.99))) +
        kv('max', statistic(quantile(1))) + kv('mean', statistic(ids.length ? population.total / ids.length : 0)) +
        '</div><div class="an-hist-wrap" id="tl-dist-histogram"' + (distHistogramOpen ? '' : ' hidden') + '><canvas class="an-hist" height="120" role="img" aria-label="Duration histogram; use the duration bucket menu to filter calls"></canvas>' +
        '<div class="an-hint">Log-scale buckets; click a bar or choose a duration bucket to filter the call list and export, not these statistics.</div></div></div>';
    h += '<div class="an-dist-results">';
    if (!ids.length) h += '<div class="an-hint">No calls match this function' + (AN.site !== null ? ' and call site' : '') + ' with the current filter.</div>';
    h += '<div class="an-dist-tools"><label><span>Duration bucket</span> <select data-act="dist-bucket" aria-label="Duration bucket"' + (ids.length ? '' : ' disabled') + '><option value="-1">All durations</option>';
    distView.histogram.buckets.forEach((bucket, index) => {
        if (bucket.start !== bucket.end) h += '<option value="' + index + '">' + esc(distBucketLabel(index)) + '</option>';
    });
    h += '</select></label><button class="tl-btn" data-act="dist-reset-bucket">All durations</button>' +
        '<button class="tl-btn" data-act="dist-csv" type="button">Export calls CSV</button>' +
        '<span class="an-dist-csv-status" role="status" aria-atomic="true" hidden></span></div>';
    h += '<div class="an-dist-call-browser"><table class="tl-sum an-tbl" aria-label="Duration calls"><thead><tr><th>duration</th><th>self</th><th style="text-align:left">task</th><th>start</th><th style="text-align:left">caller</th><th>×p50</th></tr></thead><tbody class="an-list">' +
        slowRows(distPageIds(), quantile(0.5)) + '</tbody></table>' +
        '<div class="an-dist-pager"><span class="an-dist-count" role="status" aria-live="polite" aria-atomic="true"></span>' +
        '<span class="an-dist-pages" role="group" aria-label="Duration call pages">' +
        '<button class="tl-btn" type="button" data-act="dist-prev" aria-label="Previous page of calls">Previous</button>' +
        '<button class="tl-btn" type="button" data-act="dist-next" aria-label="Next page of calls">Next</button></span></div></div></div></div>' +
        '<div class="an-slowfast">' + slowFastHtml() + '</div>' +
        callSiteTable(data);
    openDetail(h);
    if (changedPopulation) elDetail.scrollTop = 0;
    positionDistPager();
    updateDistControls();
    if (prepareSlowFast()) renderSlowFast();
    if (replacementControl) elDetail.querySelector(replacementControl)?.focus({preventScroll: true});
    else if (!changedPopulation && focusedAction) {
        const control = Array.from(elDetail.querySelectorAll('[data-act]')).find(button => button.dataset.act === focusedAction);
        if (control && !control.disabled) control.focus({ preventScroll: true });
    }
}
function slowRows(list, p50) {
    return list.map(i => `<tr data-slice="${i}"><td>${fmtDur(durOf(i))}</td><td>${fmtDur(sSelf[i])}</td>` +
        `<td style="text-align:left">${esc(trackLabel(TRACKS[sTask[i]]))}</td><td>${fmtDur(sStart[i])}</td>` +
        `<td style="text-align:left">${esc(callerOf(i) || (sParent[i] >= 0 ? nameOf(sParent[i]) : ''))}</td>` +
        `<td>${p50 ? (durOf(i) / p50).toFixed(1) : ''}</td></tr>`).join('');
}
function toggleDistHistogram() {
    distHistogramOpen = !distHistogramOpen;
    elDetail.querySelector('.an-dist').classList.toggle('an-dist-compact', !distHistogramOpen);
    elDetail.querySelector('.an-hist-wrap').hidden = !distHistogramOpen;
    const button = elDetail.querySelector('[data-act=dist-histogram]');
    button.setAttribute('aria-expanded', String(distHistogramOpen));
    button.title = distHistogramOpen ? 'Hide histogram; keep all statistics and calls' : 'Show histogram for the full statistical population';
    elDetail.scrollTop = 0;
    scheduleDistHistogram();
}
function updateDistControls() {
    const scope = distCallRange(), length = scope.end - scope.start;
    const start = distView.page * DIST_PAGE, end = Math.min(start + DIST_PAGE, length);
    const previous = elDetail.querySelector('[data-act=dist-prev]'), next = elDetail.querySelector('[data-act=dist-next]');
    const focused = document.activeElement;
    const count = elDetail.querySelector('.an-dist-count');
    count.textContent = length ? 'Calls ' + (start + 1) + '–' + end + ' of ' + length : 'No matching calls';
    if (length) count.insertAdjacentHTML('beforeend', '<span class="an-dist-order"> (slowest first)</span>');
    previous.disabled = distView.page === 0;
    next.disabled = end >= length;
    if (focused === next && next.disabled && !previous.disabled) previous.focus({ preventScroll: true });
    else if (focused === previous && previous.disabled && !next.disabled) next.focus({ preventScroll: true });
    elDetail.querySelector('[data-act=dist-csv]').disabled = !length;
    elDetail.querySelector('[data-act=dist-reset-bucket]').hidden = distView.bucket < 0;
    elDetail.querySelector('[data-act=dist-bucket]').value = distView.bucket;
}
function selectDistBucket(index) {
    if (!distView || !Number.isInteger(index) || index < -1 || index >= distView.histogram.buckets.length) return;
    const bucket = distView.histogram.buckets[index];
    if (bucket && bucket.start === bucket.end) return;
    if (distView.bucket !== index) cancelDistCsv();
    distView.bucket = index;
    distView.page = 0;
    elDetail.querySelector('.an-list').innerHTML = slowRows(distPageIds(), distQuantile(distView.population.ids, 0.5));
    updateDistControls();
    scheduleDistHistogram();
}
function positionDistPager() {
    const browser = elDetail.querySelector('.an-dist-call-browser');
    if (browser) positionTablePager(browser.querySelector('table'), browser.querySelector('.an-dist-pager'));
}
function changeDistPage(direction) {
    if (!distView || AN.kind !== 'dist') return;
    const { start, end } = distCallRange(), last = Math.max(0, Math.ceil((end - start) / DIST_PAGE) - 1);
    distView.page = Math.max(0, Math.min(last, distView.page + direction));
    const table = elDetail.querySelector('.an-dist-call-browser table');
    table.tBodies[0].innerHTML = slowRows(distPageIds(), distQuantile(distView.population.ids, 0.5));
    updateDistControls();
    revealPagedTable(table, '.an-dist-pages');
}
function changeDistSite(site) {
    AN.site = site;
    showDist(AN.func);
    (elDetail.querySelector(site === null ? '[data-act=dist-histogram]' : '[data-act=dist-reset-site]') ||
        elDetail.querySelector('.an-dist-head .tl-close'))?.focus({ preventScroll: true });
}
function scheduleDistHistogram() {
    if (distFrame || !distHistogramOpen || AN.kind !== 'dist' || !elDetail.classList.contains('show') || window.fgCurrentView() !== 'timeline') return;
    distFrame = requestAnimationFrame(() => {
        distFrame = 0;
        if (distHistogramOpen && AN.kind === 'dist' && elDetail.classList.contains('show') && window.fgCurrentView() === 'timeline') drawDistHistogram();
    });
}
function drawDistHistogram() {
    const canvas = elDetail.querySelector('.an-hist');
    if (!canvas || !distView) return;
    const width = canvas.clientWidth, height = canvas.clientHeight, ratio = window.devicePixelRatio || 1;
    if (width <= 0 || height <= 0) return;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
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
        const length = bucket.end - bucket.start;
        if (!length) return;
        const barHeight = Math.max(1, plotHeight * Math.log1p(length) / Math.log1p(histogram.maxCount));
        context.fillStyle = bucket.error ? P.errO : P.ovBar;
        context.globalAlpha = distView.bucket < 0 || distView.bucket === index ? 0.85 : 0.3;
        context.fillRect(left + index * plotWidth / count + 1, 4 + plotHeight - barHeight, plotWidth / count - 2, barHeight);
    });
    context.globalAlpha = 1;
    context.fillStyle = P.axis;
    let labelEdge = left;
    for (let exponent = Math.ceil(histogram.low); exponent <= histogram.high; exponent++) {
        const value = Math.pow(10, exponent), coordinate = position(value);
        context.fillRect(coordinate, 4 + plotHeight, 1, 4);
        const text = fmtDur(value), textWidth = context.measureText(text).width;
        const labelLeft = Math.min(coordinate + 2, width - textWidth - 2);
        if (labelLeft >= labelEdge) {
            context.fillText(text, labelLeft, 6 + plotHeight);
            labelEdge = labelLeft + textWidth + 6;
        }
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
const DIST_CSV_DEFER_ROWS = 4096;
let distCsvJob = null;
function cancelDistCsv() {
    const job = distCsvJob;
    if (!job) return;
    distCsvJob = null;
    job.button.textContent = 'Export calls CSV';
    job.status.hidden = true;
    job.status.textContent = '';
}
function queueDistCsv(afterPaint = false) {
    const job = distCsvJob;
    if (job) queueTimelineCsv(job, () => distCsvJob === job, cancelDistCsv, afterPaint);
}
function exportDistCsv(button) {
    if (distCsvJob) { cancelDistCsv(); return; }
    const { start, end } = distCallRange();
    if (start === end) return;
    const ids = distView.population.ids;
    const durations = sDur, selfTimes = sSelf, filter = window.fgFilterState?.text || '', site = AN.site || '';
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const basis = MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'measured';
    const bounds = distView.bucket < 0 ? ['', ''] : distBucketBounds(distView.bucket).map(value => value / scale);
    const columns = ['function', 'duration_' + unit, 'self_' + unit, 'start_' + unit, 'end_' + unit,
        'task', 'caller', 'trace_line', 'error_origin', 'time_mode', 'duration_basis', 'filter', 'call_site_filter',
        'bucket_lower_' + unit, 'bucket_upper_' + unit];
    function* rows() {
        for (let offset = start; offset < end; offset++) {
            const index = ids[offset];
            yield [nameOf(index), durations[index] / scale, selfTimes[index] / scale, sStart[index] / scale,
                sEnd[index] / scale, trackLabel(TRACKS[sTask[index]]), callerOf(index) || '', sLine[index], eRole[index] === 0 ? 1 : 0,
                MODE, basis, filter, site, bounds[0], bounds[1]];
        }
    }
    const filename = 'funcgraph-calls-' + (MODE === 'count' ? 'count' : adjOn ? 'adjusted' : 'raw') + '.csv';
    const total = end - start;
    if (total < DIST_CSV_DEFER_ROWS) { downloadTimelineCsv(filename, columns, rows()); return; }
    const status = elDetail.querySelector('.an-dist-csv-status');
    distCsvJob = {button, status, filename, rows: rows(), total, written: 0, progress: 0, batchRows: 128,
        parts: ['\ufeff' + columns.join(',') + '\r\n'], scheduled: false};
    button.textContent = 'Cancel CSV';
    status.textContent = timelineCsvProgressText(0);
    status.hidden = false;
    queueDistCsv(true);
}
elDetail.addEventListener('change', event => {
    if (event.target.matches('[data-act=dist-bucket]')) selectDistBucket(+event.target.value);
    else if (event.target.matches('[data-act=dist-jump]')) jumpDistSection(event.target);
});
elDetail.addEventListener('click', event => {
    const button = event.target.closest('[data-act]');
    if (!button || AN.kind !== 'dist') return;
    const action = button.dataset.act;
    if (action === 'dist-next' || action === 'dist-prev') changeDistPage(action === 'dist-next' ? 1 : -1);
    else if (action === 'dist-histogram') toggleDistHistogram();
    else if (action === 'dist-reset-bucket') selectDistBucket(-1);
    else if (action === 'dist-reset-site') changeDistSite(null);
    else if (action === 'dist-csv') exportDistCsv(button);
    else if (action === 'dist-sites-prev' || action === 'dist-sites-next') changeDistSitePage(action === 'dist-sites-next' ? 1 : -1);
    else return;
    event.stopImmediatePropagation();
}, true);

// ---- 3. scheduling latency: need-resched windows and who delayed them ----
const WINDOW_INDEX = new Map();
const WINDOW_ENDS = ['flag cleared', 'switched out', 'trace end'];
const WINDOW_PAGE_SIZE = 100;
const WINDOW_CSV_DEFER_ROWS = 2048;
let latencyCache = null, sectionCache = null, windowCsvJob = null;
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
function topWindowContributors(totals, limit) {
    const top = [];
    totals.forEach((value, name) => {
        const last = top.length === limit ? top[limit - 1] : null;
        if (last && (last[1] - value || name - last[0]) >= 0) return;
        const entry = last ? top.pop() : [name, value];
        entry[0] = name;
        entry[1] = value;
        let position = top.length;
        while (position > 0 && (top[position - 1][1] - value || name - top[position - 1][0]) < 0) {
            top[position] = top[position - 1];
            position--;
        }
        top[position] = entry;
    });
    return top;
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
    const top = topWindowContributors(blame, 3);
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
    const top = topWindowContributors(aggregate, 8);
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
function windowOrder(data, kind, key) {
    let order = data.orders[kind];
    if (!order) { order = { rows: data.groups[kind].slice(), key: null, page: 0 }; data.orders[kind] = order; }
    if (order.key !== key) {
        const direction = windowSortAscending(key) ? 1 : -1;
        order.rows.sort((left, right) => {
            const leftValue = windowSortValue(left, key), rightValue = windowSortValue(right, key);
            return (leftValue < rightValue ? -direction : leftValue > rightValue ? direction : 0) || left.index - right.index;
        });
        order.key = key;
        order.page = 0;
    }
    return order;
}
function windowTableHead(columns, key, attr) {
    return '<tr>' + columns.map(([field, label, left]) => '<th' + (left ? ' style="text-align:left"' : '') +
        (field ? ' data-' + attr + '="' + field + '" aria-sort="' + (field === key ? (windowSortAscending(key) ? 'ascending' : 'descending') : 'none') +
            '"><button class="tl-sum-sort">' + label + '</button>' : '>' + label) + '</th>').join('') + '</tr>';
}
function windowActions(enabled) {
    return '<span class="an-window-actions"><button class="tl-btn" type="button" data-act="window-csv"' + (enabled ? '' : ' disabled') +
        '>Export CSV</button><button class="tl-close" title="Close (Esc)">×</button></span>' +
        '<span class="an-window-csv-status" role="status" hidden></span></div>';
}
function windowFooter(order, section) {
    const pages = Math.ceil(order.rows.length / WINDOW_PAGE_SIZE);
    return '<div class="an-window-footer"><span role="status" aria-live="polite" aria-atomic="true">' + windowPageStatus(order, section) + '</span>' +
        (pages > 1 ? '<div class="an-window-pages"><button class="tl-btn" type="button" data-act="window-prev" ' +
            'aria-label="Previous page of ' + (section ? 'sections' : 'windows') + '"' + (order.page ? '' : ' disabled') +
            '><span class="an-window-page-label">Previous</span><span class="an-window-page-arrow" aria-hidden="true">‹</span></button>' +
            '<form class="an-window-jump" data-window-page><label>Page <input type="text" name="page" data-act="window-page" ' +
            'inputmode="numeric" autocomplete="off" aria-describedby="tl-window-page-total" value="' + (order.page + 1) + '"></label>' +
            '<span id="tl-window-page-total">of ' + pages + '</span><button type="submit" class="tl-btn" data-act="window-go" aria-label="Go to page">Go</button></form>' +
            '<button class="tl-btn" type="button" data-act="window-next" aria-label="Next page of ' + (section ? 'sections' : 'windows') + '"' +
            (order.page + 1 === pages ? ' disabled' : '') +
            '><span class="an-window-page-label">Next</span><span class="an-window-page-arrow" aria-hidden="true">›</span></button></div>' : '') + '</div>';
}
function windowPageStatus(order, section) {
    const start = order.page * WINDOW_PAGE_SIZE, end = Math.min(start + WINDOW_PAGE_SIZE, order.rows.length);
    return 'Showing ' + (order.rows.length ? start + 1 + '–' + end : '0') + ' of ' + order.rows.length + (section ? ' sections' : ' windows');
}
function visibleWindowRows(order) {
    const start = order.page * WINDOW_PAGE_SIZE;
    return order.rows.slice(start, start + WINDOW_PAGE_SIZE);
}
const windowTableStart = () => '<div class="an-hint" id="tl-window-keys">Activate a duration to zoom the chart. ' +
    'On a result, use ↑ / ↓ or Home / End to browse this page, then Enter or Space to zoom.</div>' +
    '<table class="tl-sum an-tbl an-window-table" data-window-kind="' + AN.kind + '" aria-describedby="tl-window-keys"><thead>';
function windowJumpCell(record, task, first, kind) {
    const duration = fmtDur(record.duration), start = fmtDur(record.start);
    const label = duration + ' — zoom to ' + kind + ' for ' + task + ' at ' + start;
    return '<td' + (TIMED && record.duration > 100000 ? ' class="err"' : '') + '><button type="button" ' +
        'class="tl-link tl-detail-link" data-window-jump="' + record.index + '" tabindex="' + (first ? '0' : '-1') + '" ' +
        'aria-label="' + esc(label) + '">' + duration + '</button></td><td>' + start + '</td>';
}
function windowStickySpace(table) {
    const head = elDetail.querySelector('.an-window-head');
    const footer = elDetail.querySelector('.an-window-footer:has(.an-window-pages)');
    let bottom = footer?.offsetHeight || 0;
    if (footer && elDetail.classList.contains('tl-detail-short')) bottom += parseFloat(getComputedStyle(footer).paddingBlockStart);
    return { top: (compactToolbar.matches ? 0 : head.offsetHeight) + table.tHead.offsetHeight, bottom };
}
function windowPaneNeedsFlow(height) {
    const table = elDetail.querySelector('.an-window-table'), first = table?.tBodies[0]?.rows[0];
    if (!first) return false;
    const space = windowStickySpace(table);
    return space.top + space.bottom + first.offsetHeight + 4 > height;
}
function revealWindowResult(button, table) {
    const pane = elDetail.getBoundingClientRect(), bounds = button.getBoundingClientRect();
    const left = pane.left + elDetail.clientLeft, right = left + elDetail.clientWidth;
    let top = pane.top + elDetail.clientTop, bottom = top + elDetail.clientHeight;
    if (!elDetail.classList.contains('tl-detail-short')) {
        const space = windowStickySpace(table);
        top += space.top;
        bottom -= space.bottom;
    }
    if (bounds.top < top + 2) elDetail.scrollTop += bounds.top - top - 2;
    else if (bounds.bottom > bottom - 2) elDetail.scrollTop += bounds.bottom - bottom + 2;
    if (bounds.left < left + 2) elDetail.scrollLeft += bounds.left - left - 2;
    else if (bounds.right > right - 2) elDetail.scrollLeft += bounds.right - right + 2;
}
elDetail.addEventListener('focusin', event => {
    const button = event.target;
    if (!button.matches('[data-window-jump]')) return;
    if (button.tabIndex !== 0) {
        const previous = button.closest('tbody').querySelector('[data-window-jump][tabindex="0"]');
        if (previous) previous.tabIndex = -1;
        button.tabIndex = 0;
    }
    revealWindowResult(button, button.closest('table'));
});
function windowResultKey(event) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
        !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || !event.target.matches?.('[data-window-jump]')) return false;
    const row = event.target.closest('tr');
    const target = event.key === 'Home' ? row.parentElement.firstElementChild :
        event.key === 'End' ? row.parentElement.lastElementChild :
        event.key === 'ArrowUp' ? row.previousElementSibling : row.nextElementSibling;
    target?.querySelector('[data-window-jump]')?.focus({ preventScroll: true });
    event.preventDefault();
    return true;
}
function openWindowPanel(html) {
    const focused = document.activeElement;
    const action = elDetail.contains(focused) ? focused.dataset.act : null;
    const jump = focused?.closest('.an-window-table')?.dataset.windowKind === AN.kind ? focused.dataset.windowJump : undefined;
    openDetail(html);
    positionSummaryPager();
    if (jump !== undefined) {
        const button = elDetail.querySelector('[data-window-jump="' + jump + '"]') || elDetail.querySelector('[data-window-jump]');
        (button || elDetail.querySelector('.tl-close'))?.focus({ preventScroll: true });
        return;
    }
    if (!['window-prev', 'window-next', 'window-page', 'window-go', 'window-csv'].includes(action)) return;
    const control = elDetail.querySelector('[data-act="' + action + '"]');
    (control && !control.disabled ? control : elDetail.querySelector('.tl-close'))?.focus({ preventScroll: true });
}
function windowTimeHint() {
    return TIMED ? '' : '<div class="an-hint">No timestamps: ' + (MODE === 'count' ? 'call-order units' : 'packed coordinates') +
        ' describe trace order, not measured scheduling latency or wall-clock section lengths.</div>';
}
function windowTopHtml(profile) {
    return profile.top.map(([name, value]) => esc(S[name]) + ' <span class="d">' + fmtDur(value) + '</span>').join(', ') || '—';
}
function latencyRows(rows) {
    return rows.map((record, index) => {
        const profile = windowProfile(record), task = trackLabel(TRACKS[record.track]);
        return '<tr data-win="' + record.start + ',' + record.end + ',' + record.track + '">' +
            windowJumpCell(record, task, index === 0, 'latency window') +
            '<td style="text-align:left">' + esc(task) + '</td><td>' + (record.depth || '') + '</td>' +
            '<td>' + (profile.irq ? fmtDur(profile.irq) : '') + '</td><td style="text-align:left">' + windowTopHtml(profile) + '</td>' +
            '<td>' + (WINDOW_ENDS[record.raw[5]] || '') + '</td></tr>';
    }).join('');
}
function jumpToWindow(start, end, track) {
    const task = TRACKS[track];
    task.hidden = false; task.collapsed = false; layout();
    elScroll.scrollTop = Math.max(0, task.y - 4);
    range = { s: start, e: end, track };
    zoomTo(start, end, -1);
}
function activeWindowOrder(section) {
    return section ? windowOrder(sectionWindows(), AN.secKind, AN.secSort) : windowOrder(latencyWindows(), 0, AN.latSort);
}
function syncWindowPager(order, section) {
    const footer = elDetail.querySelector('.an-window-footer');
    const input = footer.querySelector('[data-act=window-page]');
    if (input) {
        input.setCustomValidity('');
        input.value = order.page + 1;
        const previous = footer.querySelector('[data-act=window-prev]'), next = footer.querySelector('[data-act=window-next]');
        const focused = document.activeElement;
        previous.disabled = order.page === 0;
        next.disabled = (order.page + 1) * WINDOW_PAGE_SIZE >= order.rows.length;
        if (focused === next && next.disabled) previous.focus({ preventScroll: true });
        else if (focused === previous && previous.disabled) next.focus({ preventScroll: true });
    }
    const status = footer.querySelector('[role=status]'), text = windowPageStatus(order, section);
    if (status.textContent !== text) status.textContent = text;
}
function replaceWindowPage(table, order, section) {
    const rows = visibleWindowRows(order);
    table.tBodies[0].innerHTML = section ? sectionRows(rows) : latencyRows(rows);
    syncWindowPager(order, section);
    updateShortDetail();
}
function sortWindowTable(th, section) {
    if (AN.kind !== (section ? 'sec' : 'lat')) return;
    const property = section ? 'secSort' : 'latSort', key = section ? th.dataset.ssort : th.dataset.lsort;
    const button = th.querySelector('button');
    button.focus({ preventScroll: true });
    if (key === AN[property]) return;
    cancelWindowCsv(false);
    const table = th.closest('.an-window-table'), keepRightEdge = summaryPageAtRightEdge();
    AN[property] = key;
    table.tHead.querySelector('[aria-sort=ascending], [aria-sort=descending]')?.setAttribute('aria-sort', 'none');
    th.setAttribute('aria-sort', windowSortAscending(key) ? 'ascending' : 'descending');
    replaceWindowPage(table, activeWindowOrder(section), section);
    if (keepRightEdge) elDetail.scrollLeft = elDetail.scrollWidth - elDetail.clientWidth;
    revealDetailTable(table);
    const pane = elDetail.getBoundingClientRect(), bounds = button.getBoundingClientRect();
    const left = pane.left + elDetail.clientLeft, right = left + elDetail.clientWidth;
    if (bounds.left < left) elDetail.scrollLeft += bounds.left - left;
    else if (bounds.right > right) elDetail.scrollLeft += bounds.right - right;
}
function changeWindowPage(direction, field = null) {
    if (AN.kind !== 'lat' && AN.kind !== 'sec') return;
    const section = AN.kind === 'sec';
    const order = activeWindowOrder(section);
    const pages = Math.ceil(order.rows.length / WINDOW_PAGE_SIZE);
    if (pages < 2) return;
    const input = elDetail.querySelector('[data-act=window-page]');
    const entered = field?.value.trim();
    const page = field ? Number(entered) - 1 : order.page + direction;
    if (field && (!/^\d+$/.test(entered) || page < 0 || page >= pages)) {
        input.setCustomValidity('Enter a whole page number from 1 to ' + pages + '.');
        input.reportValidity();
        return;
    }
    const target = Math.max(0, Math.min(pages - 1, page));
    if (order.page === target) {
        input.setCustomValidity('');
        input.value = target + 1;
        return;
    }
    order.page = target;
    const table = elDetail.querySelector('.an-window-table');
    const keepRightEdge = summaryPageAtRightEdge();
    replaceWindowPage(table, order, section);
    revealSummaryPage(table, keepRightEdge);
}
function cancelWindowCsv(refresh = true) {
    const job = windowCsvJob;
    if (!job) return;
    windowCsvJob = null;
    job.button.textContent = 'Export CSV';
    job.status.hidden = true;
    job.status.textContent = '';
    if (refresh && window.fgCurrentView() === 'timeline') updateShortDetail();
}
function queueWindowCsv(afterPaint = false) {
    const job = windowCsvJob;
    if (job) queueTimelineCsv(job, () => windowCsvJob === job, cancelWindowCsv, afterPaint);
}
function exportWindowCsv(button) {
    if (windowCsvJob) { cancelWindowCsv(); return; }
    const section = AN.kind === 'sec';
    const order = activeWindowOrder(section);
    if (!order.rows.length) return;
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const basis = MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'measured';
    const windowBasis = TIMED ? 'timestamp_span' : MODE === 'count' ? 'call_order' : 'packed_estimate';
    const filter = window.fgFilterState?.text || '';
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
            values.push(MODE, basis, windowBasis, filter);
            yield values;
        }
    }
    const filename = 'funcgraph-' + (section ? 'sections' : 'latency') + '-' + (MODE === 'count' ? 'count' : adjOn ? 'adjusted' : 'raw') + '.csv';
    if (order.rows.length < WINDOW_CSV_DEFER_ROWS) { downloadTimelineCsv(filename, columns, rows()); return; }
    const status = elDetail.querySelector('.an-window-csv-status');
    button.textContent = 'Cancel CSV';
    status.hidden = false;
    status.textContent = timelineCsvProgressText(0);
    windowCsvJob = { button, status, filename, rows: rows(), total: order.rows.length, batchRows: 128,
        written: 0, progress: 0, parts: ['\ufeff' + columns.join(',') + '\r\n'], scheduled: false };
    updateShortDetail();
    queueWindowCsv(true);
}
function showLatency() {
    AN.kind = 'lat';
    const data = latencyWindows(), order = windowOrder(data, 0, AN.latSort);
    let h = '<div class="tl-detail-head an-window-head"><span class="tl-detail-title">Scheduling latency</span>' +
        pill('warn', order.rows.length + ' need-resched windows') + pill('mute', 'total ' + fmtDur(data.total)) +
        (order.rows.length ? pill('mute', 'max ' + fmtDur(data.maximum)) : '') + (textHits ? pill('warn', 'filtered') : '') +
        (adjOn ? pill('warn', 'self: overhead subtracted') : '') + windowActions(order.rows.length);
    if (!order.rows.length) {
        h += '<div class="an-hint">' + (!D.caps.latency ? 'Needs <code>options/latency-format</code> (need-resched flag) and timestamps.' :
            textHits ? 'No need-resched windows overlap calls matching the current filter.' : 'No pending need-resched windows in this trace.') + '</div>';
        openWindowPanel(h); return;
    }
    h += windowTimeHint() + '<div class="an-hint" style="padding:6px 12px 0">Window = any recorded NEED_RESCHED, NEED_RESCHED_LAZY or PREEMPT_RESCHED request until cleared or switched out; lazy-only is not an immediate scheduling deadline. ' +
        'Self times are proportional overlap estimates from matching calls, not exact on-CPU or causal attribution. ' +
        'Overhead changes self estimates, not window positions or lengths. Overview sums every matching function across windows.</div>' +
        '<div class="an-blame">' + data.top.map(([name, value]) => '<span class="an-chip"><span class="k">' + esc(S[name]) + '</span>' + fmtDur(value) + '</span>').join('') + '</div>';
    h += windowTableStart() + windowTableHead([['dur', 'latency'], ['start', 'start'], ['task', 'task', 1],
        ['pre', 'max preempt off'], ['irq', 'irqs-off self (est.)'], ['top', 'top self (est.)', 1], ['end', 'ended by']], AN.latSort, 'lsort') +
        '</thead><tbody>' + latencyRows(visibleWindowRows(order)) + '</tbody></table>' + windowFooter(order, false);
    openWindowPanel(h);
}

elDetail.addEventListener('input', event => {
    if (event.target.matches('[data-act=window-page]')) event.target.setCustomValidity('');
});
elDetail.addEventListener('submit', event => {
    if (!event.target.matches('[data-window-page]')) return;
    event.preventDefault();
    changeWindowPage(0, event.target.elements.page);
});
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
        if (/^(?:errors|atomic|user-errors)-(?:prev|next)$/.test(act)) {
            changeErrorPage(act.startsWith('atomic'), act.endsWith('next') ? 1 : -1, act.startsWith('user-errors')); ev.stopImmediatePropagation(); return;
        }
        if (ERROR_CSV_KINDS.has(act)) {
            exportErrorsCsv(action, ERROR_CSV_KINDS.get(act));
            ev.stopImmediatePropagation(); return;
        }
    }
    if (action && (AN.kind === 'lat' || AN.kind === 'sec')) {
        if (action.dataset.act === 'window-prev' || action.dataset.act === 'window-next') {
            changeWindowPage(action.dataset.act === 'window-next' ? 1 : -1); ev.stopImmediatePropagation(); return;
        }
        if (action.dataset.act === 'window-csv') { exportWindowCsv(action); ev.stopImmediatePropagation(); return; }
    }
    const th = ev.target.closest('th[data-esort], th[data-lsort]');
    if (th) {
        if (th.dataset.esort) {
            AN.errSort = th.dataset.esort; showErrors();
            elDetail.querySelector('th[data-esort="' + AN.errSort + '"] button').focus({ preventScroll: true });
        }
        else sortWindowTable(th, false);
        ev.stopImmediatePropagation(); return;
    }
    const ec = ev.target.closest('tr[data-chains], tr[data-atomic-group], tr[data-user-error-group]');
    if (ec) {
        const step = ev.target.closest('[data-error-step]');
        jumpErrorOccurrence(ec, step ? +step.dataset.errorStep : ev.shiftKey ? -1 : 1);
        ev.stopImmediatePropagation(); return;
    }
    const wr = ev.target.closest('tr[data-win]');
    if (wr) {
        jumpToWindow(...wr.dataset.win.split(',').map(Number));
        ev.stopImmediatePropagation(); return;
    }
    const sl = ev.target.closest('.an-list tr[data-slice], tr.an-atomic');
    if (sl) { const i = +sl.dataset.slice; setSelection(i); zoomTo(sStart[i], sEnd[i], i); draw(); ev.stopImmediatePropagation(); }
}, true);

// ---- 4. wait chain: off-CPU segment -> who woke it -> what the waker was waiting for ----
const WAIT_PAGE = 100, WAIT_MAX_GAP = 50e6, WAIT_CSV_DEFER_ROWS = 2048;
const WAIT_LOOKUP_BATCH = 2048;
let waitCache = null, waitCsvJob = null;
function* prepareWaitOrder(track) {
    const records = [];
    for (let start = 0; start < track.off.length; start += WAIT_LOOKUP_BATCH) {
        yield;
        const end = Math.min(start + WAIT_LOOKUP_BATCH, track.off.length);
        for (let index = start; index < end; index++) {
            const record = track.off[index];
            if (!OPEN_OFF.has(record)) records.push(record);
        }
    }
    yield* sortTimelineRow(records, (left, right) => left[2] - right[2], '', WAIT_LOOKUP_BATCH);
    return { records, start: 0, end: records.length, ascending: true };
}
function scanWaitBefore(records, time, start, end, best = null) {
    for (let index = start; index < end; index++) {
        const record = records[index];
        if (record[2] <= time && (!best || record[2] > best[2]) && !OPEN_OFF.has(record)) best = record;
    }
    return best;
}
function* scanWaitTrack(track, time) {
    let best = null;
    for (let start = 0; start < track.off.length; start += PREPARE_BATCH) {
        yield;
        best = scanWaitBefore(track.off, time, start, Math.min(start + PREPARE_BATCH, track.off.length), best);
    }
    return best;
}
function offBefore(track, time) {
    const order = track.waitOrder;
    if (!order) return scanWaitBefore(track.off, time, 0, track.off.length);
    const { records, start, end, ascending } = order;
    let lower = start, upper = end;
    while (lower < upper) {
        const middle = (lower + upper) >>> 1;
        if (ascending ? records[middle][2] <= time : records[middle][2] > time) lower = middle + 1;
        else upper = middle;
    }
    if (!ascending) return lower < end ? records[lower] : null;
    if (lower === start) return null;
    const latest = records[lower - 1][2];
    if (lower === start + 1 || records[lower - 2][2] < latest) return records[lower - 1];
    upper = lower - 1;
    lower = start;
    while (lower < upper) {
        const middle = (lower + upper) >>> 1;
        if (records[middle][2] < latest) lower = middle + 1;
        else upper = middle;
    }
    return records[lower];
}
const RE_WAKE_INTERNAL = /^(?:try_to_wake_up|ttwu_\w+|wake_up\w*|__wake_up\w*|autoremove_wake_function|default_wake_function|woken_wake_function|pollwake|ep_poll_callback|complete\w*|swake_up\w*|wake_q_\w+|signal_wake_up\w*|_raw_spin_\w+|raw_spin_rq_\w+|preempt_count_\w+|select_task_rq\w*)$/;
function wakeSite(i) {
    // the first frame above the wakeup machinery: what the waker was actually doing
    let k = i;
    while (k >= 0 && RE_WAKE_INTERNAL.test(nameOf(k).split('.')[0])) k = sParent[k];
    return k >= 0 ? nameOf(k) : nameOf(i);
}
function* extendWaitChain(data, limit) {
    while (data.next && data.steps.length < limit) {
        const record = data.next, index = OFF_IDX.get(record), wake = WAKE_OF_OFF.get(index);
        const waker = TIMED && wake && wake[3] === 0 ? TRACKS[wake[0]] : null;
        let previous;
        if (waker?.kind === 0 && waker.off.length >= SCHED_BLOCK_SIZE && !waker.waitOrder) {
            const probe = waker.waitProbe;
            if (probe?.time === wake[1]) previous = probe.record;
            else {
                data.lookup ??= probe ? prepareWaitOrder(waker) : scanWaitTrack(waker, wake[1]);
                const progress = data.lookup.next();
                if (!progress.done) { yield; continue; }
                if (probe) {
                    waker.waitOrder = progress.value;
                    waker.waitProbe = null;
                } else {
                    previous = progress.value;
                    waker.waitProbe = { time: wake[1], record: previous };
                }
                data.lookup = null;
            }
        }
        data.steps.push({ off: record, wake: wake, gap: data.gap });
        data.seen.add(index);
        data.next = null;
        if (!TIMED) { data.stop = ['no-timestamps', 'Timestamps are unavailable; temporal links cannot be inferred.']; break; }
        if (!wake) { data.stop = ['wakeup-not-recorded', 'Wakeup not recorded for the last wait.']; break; }
        if (wake[3] !== 0) { data.stop = ['interrupt-context', 'The last wakeup came from interrupt context.']; break; }
        if (waker.kind !== 0) { data.stop = ['non-task-context', 'The last waker was idle or a CPU-only track.']; break; }
        if (previous === undefined) previous = offBefore(waker, wake[1]);
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
function waitStatus(data, start, end) {
    if (!end) return 'No waits resolved yet';
    return (start + 1) + '–' + end + ' of ' + data.steps.length + (data.next ? '+' : '') + ' waits';
}
function waitStopStatus(data, busy = false) {
    if (busy) return 'Preparing wait links…';
    return data.next ? 'More temporal links available.' : 'Stopped: ' + data.stop[1];
}
function updateWaitPager(data) {
    const busy = elDetail.querySelector('.an-wait-table').hasAttribute('aria-busy');
    const stop = elDetail.querySelector('.an-wait-stop'), stopText = waitStopStatus(data, busy);
    if (stop.textContent !== stopText) stop.textContent = stopText;
    if (busy) return;
    const pager = elDetail.querySelector('.an-wait-pager');
    if (!pager) return;
    const start = data.page * WAIT_PAGE, end = Math.min(start + WAIT_PAGE, data.steps.length);
    const count = pager.querySelector('[role=status]'), status = waitStatus(data, start, end);
    if (count.textContent !== status) count.textContent = status;
    const previous = pager.querySelector('[data-act=wait-prev]'), next = pager.querySelector('[data-act=wait-next]');
    const focused = document.activeElement;
    previous.disabled = start === 0;
    next.disabled = end === data.steps.length && !data.next;
    if (focused === next && next.disabled) previous.focus({ preventScroll: true });
    else if (focused === previous && previous.disabled) next.focus({ preventScroll: true });
}
let waitAllColumns = false;
function showWaitChain(off, denseOff = AN.kind === 'wait' && AN.off === off ? AN.denseOff : 0) {
    const sameWait = waitCache?.off === off;
    if (sameWait && AN.kind === 'wait' && AN.denseOff === denseOff && elDetail.classList.contains('show')) return;
    AN.kind = 'wait';
    AN.off = off;
    AN.denseOff = denseOff;
    if (!sameWait) waitCache = { off: off, steps: [], next: off, gap: null, seen: new Set(), page: 0, stop: null };
    const data = waitCache, label = trackLabel(TRACKS[off[0]]);
    const work = extendWaitChain(data, (data.page + 1) * WAIT_PAGE), busy = !work.next().done;
    let html = '<div class="tl-detail-head an-wait-head"><span class="tl-detail-title" title="' + esc(label) + '">Wait chain · ' + esc(label) + '</span>' +
        '<span class="an-wait-context">' +
        pill(off[8] ? 'warn' : 'mute', (off[8] ? 'preempted ' : 'blocked ') + (OPEN_OFF.has(off) ? '≥ ' : '') + fmtDur(off[2] - off[1])) +
        (off[4] >= 0 ? pill('mute an-wait-site', 'in ' + S[off[4]], S[off[4]]) : '') +
        (off[5] >= 0 ? pill('mute an-wait-state', 'state ' + S[off[5]], S[off[5]]) : '') + '</span>' +
        '<span class="an-wait-actions"><button class="tl-btn" type="button" data-act="wait-csv">Export chain CSV</button>' +
        '<button class="tl-close" title="Close (Esc)">×</button></span>' +
        '<span class="an-wait-csv-status" role="status" hidden></span></div>';
    html += '<div class="an-hint an-wait-hint">' +
        (denseOff ? '<div class="an-wait-dense">' + denseOffMessage(denseOff) + '</div>' : '') +
        (!D.caps.sched_events ? '<div>Capture sched_switch and sched_waking (fgcapture.sh --sched) to record wakeups.</div>' : '') +
        '<details><summary>' + (TIMED ? 'Further rows: temporal candidates, not proven dependencies · ≤ 50 ms · all tasks' :
            'No timestamps: temporal links unavailable · all tasks') + '</summary>' +
        '<div>Recorded wakeups identify who woke each task. Further rows are temporal candidates, not proven causal dependencies: ' +
        'the waker’s latest completed wait ending at most 50 ms before it woke the preceding task. “Link gap” is the time from that prior resume to the wakeup in the preceding row. ' +
        'Wake latency is from wakeup to the recorded resume; ≥ marks a wait still open at trace end. ' +
        'All tasks are included, independently of the text filter and Overhead. Click a row or activate its task button to show it.</div></details></div>';
    const start = data.page * WAIT_PAGE, end = Math.min(start + WAIT_PAGE, data.steps.length);
    html += '<div class="an-wait-meta"><div class="an-wait-stop" role="status">' + esc(waitStopStatus(data, busy)) + '</div>' +
        '<button class="tl-btn an-wait-columns" type="button" data-act="wait-columns" aria-controls="tlWaitTable" aria-pressed="' +
        waitAllColumns + '">All columns</button></div>';
    if (data.next || data.steps.length > WAIT_PAGE) {
        html += '<div class="an-wait-pager"><span role="status" aria-atomic="true">' + waitStatus(data, start, end) +
            '</span><span class="an-wait-pages" role="group" aria-label="Wait-chain pages">' +
            '<button class="tl-btn" type="button" data-act="wait-prev" aria-label="Previous page of waits"' + (start ? '' : ' disabled') + '>Previous</button>' +
            '<button class="tl-btn" type="button" data-act="wait-next" aria-label="Next page of waits"' + (end && (data.next || end < data.steps.length) ? '' : ' disabled') + '>Next</button></span></div>';
    }
    html += '<table id="tlWaitTable" class="tl-sum an-tbl an-wait-table' + (waitAllColumns ? ' an-wait-all' : '') +
        '" aria-label="Wait chain"' + (busy ? ' aria-busy="true"' : '') +
        '><thead><tr><th>task</th><th>waited</th><th>waiting in</th>' +
        '<th>woken by</th><th>waker was in</th><th>wake latency</th><th>run again on</th><th>link gap</th></tr></thead><tbody>' +
        waitRows(data, start, end) + '</tbody></table>';
    openDetail(html);
    if (!sameWait) elDetail.scrollTop = 0;
    if (busy) deferDetailWork('wait-page', work, () => paintWaitPage(data, data.page, false));
}
function paintWaitPage(data, page, reveal) {
    const table = elDetail.querySelector('.an-wait-table');
    table.removeAttribute('aria-busy');
    let start = page * WAIT_PAGE;
    if (start >= data.steps.length) { page = data.page; start = page * WAIT_PAGE; }
    const end = Math.min(start + WAIT_PAGE, data.steps.length), body = table.tBodies[0];
    if (page === data.page) body.insertAdjacentHTML('beforeend', waitRows(data, start + body.rows.length, end));
    else body.innerHTML = waitRows(data, start, end);
    data.page = page;
    updateWaitPager(data);
    if (reveal) revealDetailTable(elDetail.querySelector('.an-wait-pager'));
}
function changeWaitPage(direction) {
    const data = waitCache, page = data.page + direction, start = page * WAIT_PAGE;
    if (start < 0 || (start >= data.steps.length && !data.next) ||
            (direction > 0 && detailWork.has('wait-page'))) return;
    if (detailWork.delete('wait-page') && !waitCsvJob) data.lookup = null;
    const work = extendWaitChain(data, start + WAIT_PAGE);
    if (work.next().done) paintWaitPage(data, page, true);
    else {
        elDetail.querySelector('.an-wait-table').setAttribute('aria-busy', 'true');
        updateWaitPager(data);
        deferDetailWork('wait-page', work, () => paintWaitPage(data, page, true));
    }
}
function resetWaitWork() {
    cancelWaitCsv();
    if (waitCache) waitCache.lookup = null;
}
function cancelWaitCsv(refresh = false) {
    const job = waitCsvJob;
    if (!job) return;
    waitCsvJob = null;
    job.button.textContent = 'Export chain CSV';
    job.status.hidden = true;
    job.status.textContent = '';
    if (refresh) {
        if (!detailWork.has('wait-page')) waitCache.lookup = null;
        updateWaitPager(waitCache);
    }
}
function queueWaitCsv(afterPaint = false) {
    const job = waitCsvJob;
    if (job) queueTimelineCsv(job, () => waitCsvJob === job, () => cancelWaitCsv(true), afterPaint);
}
function exportWaitCsv(button) {
    if (AN.kind !== 'wait' || !waitCache) return;
    if (waitCsvJob) { cancelWaitCsv(true); return; }
    const data = waitCache, unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const filename = 'funcgraph-wait-chain-' + (MODE === 'count' ? 'count' : 'raw') + '.csv';
    const total = data.next ? null : data.steps.length;
    const columns = ['step', 'offcpu_index', 'task_index', 'pid', 'comm', 'wait_start_' + unit, 'wait_end_' + unit, 'waited_' + unit,
        'end_status', 'waiting_function', 'state', 'preempted', 'waker_task_index', 'waker_pid', 'waker_comm', 'wake_time_' + unit,
        'waker_context', 'waker_function', 'wake_latency_' + unit, 'resume_cpu', 'link_kind', 'prior_resume_to_wake_' + unit,
        'max_link_gap_' + unit, 'terminal_reason', 'time_mode', 'duration_basis', 'filter_scope'];
    function* rows() {
        for (let index = 0; index < data.steps.length || data.next; index++) {
            if (index === data.steps.length) yield* extendWaitChain(data, index + 1);
            const step = data.steps[index], record = step.off, wake = step.wake, task = TRACKS[record[0]];
            const waker = wake ? TRACKS[wake[0]] : null, open = OPEN_OFF.has(record);
            yield [index + 1, OFF_IDX.get(record), record[0], task.pid, task.comm, record[1] / scale, open ? '' : record[2] / scale,
                (record[2] - record[1]) / scale, open ? 'trace_end_lower_bound' : 'recorded_resume', record[4] >= 0 ? S[record[4]] : '',
                record[5] >= 0 ? S[record[5]] : '', record[8], wake ? wake[0] : '', waker ? waker.pid : '', waker ? waker.comm : '',
                wake ? wake[1] / scale : '', wake ? CTX_NAMES[wake[3]] : '', wake && wake[4] >= 0 ? wakeSite(wake[4]) : '',
                wake && !open ? (record[2] - wake[1]) / scale : '', !open && record[7] >= 0 ? record[7] : '', index ? 'temporal' : 'selected',
                step.gap === null ? '' : step.gap / scale, TIMED ? WAIT_MAX_GAP : '', !data.next && index === data.steps.length - 1 ? data.stop[0] : '',
                MODE, MODE === 'count' ? 'call_order' : 'recorded_timestamps', 'all_tasks'];
        }
    }
    if (total !== null && total < WAIT_CSV_DEFER_ROWS) {
        downloadTimelineCsv(filename, columns, rows());
        return;
    }
    const status = elDetail.querySelector('.an-wait-csv-status');
    waitCsvJob = { button, status, filename, rows: rows(), total, batchRows: 128,
        written: 0, progress: 0, parts: ['\ufeff' + columns.join(',') + '\r\n'], scheduled: false };
    button.textContent = 'Cancel CSV';
    status.hidden = false;
    status.textContent = timelineCsvProgressText(0);
    queueWaitCsv(true);
}
elDetail.addEventListener('click', ev => {
    const action = ev.target.closest('[data-act]');
    if (action && AN.kind === 'wait') {
        if (action.dataset.act === 'wait-prev' || action.dataset.act === 'wait-next') {
            changeWaitPage(action.dataset.act === 'wait-next' ? 1 : -1);
            ev.stopImmediatePropagation(); return;
        }
        if (action.dataset.act === 'wait-csv') { exportWaitCsv(action); ev.stopImmediatePropagation(); return; }
        if (action.dataset.act === 'wait-columns') {
            waitAllColumns = !waitAllColumns;
            action.setAttribute('aria-pressed', String(waitAllColumns));
            elDetail.querySelector('.an-wait-table').classList.toggle('an-wait-all', waitAllColumns);
            if (!waitAllColumns) elDetail.scrollLeft = 0;
            ev.stopImmediatePropagation(); return;
        }
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
const callTreesByTrack = new WeakMap();
const callTreeBuilders = new WeakMap();
const breakdownCache = new Map();
const CALL_TREE_BATCH = 8192;
function callTreeFor(slice) {
    return callTreesByTrack.get(TRACKS[sTask[slice]]);
}
function* callTreeSteps(slice) {
    const track = TRACKS[sTask[slice]];
    let tree = callTreesByTrack.get(track);
    while (!tree) {
        let builder = callTreeBuilders.get(track);
        if (!builder) callTreeBuilders.set(track, builder = buildCallTree(track));
        const step = builder.next();
        if (step.done) {
            callTreeBuilders.delete(track);
            callTreesByTrack.set(track, step.value);
            return step.value;
        }
        yield;
        tree = callTreesByTrack.get(track);
    }
    return tree;
}
function* buildCallTree(track) {
    const { lo: offset, hi: end, id: task } = track;
    const count = end - offset;
    const first = new Int32Array(count).fill(-1), next = new Int32Array(count).fill(-1), previous = new Int32Array(count).fill(-1);
    let root = -1;
    for (let index = end - 1; index >= offset; index--) {
        if (index < end - 1 && (end - 1 - index) % CALL_TREE_BATCH === 0) yield;
        if (sTask[index] !== task) continue;
        const parent = sParent[index];
        if (parent >= 0 && (parent < offset || parent >= end || sTask[parent] !== task || sDepth[parent] >= sDepth[index])) continue;
        const sibling = parent < 0 ? root : first[parent - offset];
        next[index - offset] = sibling;
        if (sibling >= 0) previous[sibling - offset] = index;
        if (parent < 0) root = index;
        else first[parent - offset] = index;
    }
    return { offset, first, next, previous };
}
const CHILD_WORK_BATCH = 1024;
function* directChildren(root) {
    const { offset, first, next } = yield* callTreeSteps(root), children = [];
    for (let child = first[root - offset]; child >= 0; child = next[child - offset]) {
        if (children.length && children.length % CHILD_WORK_BATCH === 0) yield;
        children.push(child);
    }
    return children;
}
const CHILD_PAGE = 100;
const CHILD_PREVIEW_SIZE = 8;
const CHILD_PREVIEW_CACHE_LIMIT = 128;
const childPreviews = new Map();
const emptyChildPreview = { callCount: 0 };
let childFacts = null, childFiltered = null, childScope = null;
const childOrders = { groups: null, calls: null }, childRankings = { groups: null, calls: null };
const childView = { root: -1, mode: 'groups', name: null, query: '', groupSort: 'inclusive', callSort: 'inclusive', pages: { groups: 0, calls: 0 }, allColumns: false };
let childListReturn = null;
function resetChildPages() { childView.pages.groups = 0; childView.pages.calls = 0; }
function normalizedChildQuery(value) { return value.trim().toLowerCase(); }
function* childDataSteps(root, hits) {
    const durations = sDur, selfTimes = sSelf;
    if (!childFacts || childFacts.root !== root) {
        childFacts = null;
        childFiltered = null; childOrders.groups = null; childOrders.calls = null;
        childRankings.groups = null; childRankings.calls = null;
        childFacts = { root, ids: yield* directChildren(root), bases: new Map() };
    }
    const raw = childFacts;
    if (!hits && raw.bases.has(durations)) return raw.bases.get(durations);
    if (hits && childFiltered && childFiltered.raw === raw && childFiltered.hits === hits && childFiltered.durations === durations) return childFiltered;
    const grouped = new Map(), groups = [], ids = hits ? [] : raw.ids;
    let scanned = 0;
    for (const index of raw.ids) {
        if (++scanned === CHILD_WORK_BATCH) { scanned = 0; yield; }
        if (hits && !hits[index]) continue;
        if (hits) ids.push(index);
        const name = sName[index], duration = durations[index];
        let group = grouped.get(name);
        if (!group) {
            group = { name, count: 0, first: index, inclusive: 0, self: 0, max: -Infinity, longest: index };
            grouped.set(name, group);
            groups.push(group);
        }
        group.count++; group.inclusive += duration; group.self += selfTimes[index];
        if (duration > group.max) { group.max = duration; group.longest = index; }
    }
    const data = { raw, hits, durations, selfTimes, ids, groups, members: null };
    if (hits) childFiltered = data;
    else raw.bases.set(durations, data);
    return data;
}
function childGroupMean(group) { return group.inclusive / group.count; }
function compareChildPreviewGroups(left, right) {
    return right.inclusive - left.inclusive || left.first - right.first;
}
function* selectChildPreview(groups) {
    const preview = [];
    for (let position = 0; position < groups.length; position++) {
        if (position && position % CHILD_WORK_BATCH === 0) yield;
        const group = groups[position];
        const full = preview.length === CHILD_PREVIEW_SIZE;
        if (full && compareChildPreviewGroups(group, preview[CHILD_PREVIEW_SIZE - 1]) >= 0) continue;
        let rank = full ? CHILD_PREVIEW_SIZE - 1 : preview.length;
        while (rank > 0 && compareChildPreviewGroups(group, preview[rank - 1]) < 0) {
            preview[rank] = preview[rank - 1];
            rank--;
        }
        preview[rank] = group;
    }
    return preview;
}
function childPreviewRows(data) {
    return data.preview.map(group => '<button class="tl-crumb tl-child-preview" data-child-preview-group="' + group.name + '" data-slice="' + group.longest +
            '" title="Open the longest recorded call of ' + esc(S[group.name]) + '"><span class="d">' + fmtDur(group.inclusive) +
            '</span><span class="tl-child-preview-name">' + esc(S[group.name]) + '</span>' + (group.count > 1 ?
                '<span class="d">×' + group.count + '</span>' : '') + '</button>').join('');
}
function* childPreviewSteps(root) {
    if (sFlags[root] & FL.LEAF) return emptyChildPreview;
    const cached = childPreviews.get(root);
    if (cached?.durations === sDur) {
        childPreviews.delete(root);
        childPreviews.set(root, cached);
        return cached;
    }
    const tree = yield* callTreeSteps(root);
    if (tree.first[root - tree.offset] < 0) return emptyChildPreview;
    const data = yield* childDataSteps(root, null);
    const preview = yield* selectChildPreview(data.groups);
    const summary = { callCount: data.ids.length, groupCount: data.groups.length, durations: data.durations, preview };
    childPreviews.delete(root);
    if (childPreviews.size >= CHILD_PREVIEW_CACHE_LIMIT) childPreviews.delete(childPreviews.keys().next().value);
    childPreviews.set(root, summary);
    return summary;
}
function childPreviewTitle(data) {
    return data ? 'Children (' + data.callCount + ' call' + (data.callCount === 1 ? '' : 's') +
        (data.groupCount > CHILD_PREVIEW_SIZE ? '; top ' + CHILD_PREVIEW_SIZE + ' of ' + data.groupCount + ' functions' : '') + ')' : 'Children';
}
function childPreviewContent(data) {
    return data ? childPreviewRows(data) : '<span role="status" class="tl-context-note">Preparing child summary…</span>';
}
function childPreviewHtml(root, data) {
    if (data && !data.callCount) return '';
    return '<div class="tl-child-summary"><span class="tl-sec" data-child-title>' + childPreviewTitle(data) + '</span>' +
        '<button type="button" class="tl-btn" data-child-open="' + root + '"' + (data ? '' : ' aria-disabled="true"') +
        '>Explore all children</button></div><div class="tl-crumbs" data-child-preview>' + childPreviewContent(data) + '</div>';
}
function* childMembers(data) {
    if (data.members) return data.members;
    const members = new Map();
    let scanned = 0;
    for (const index of data.ids) {
        if (++scanned === CHILD_WORK_BATCH) { scanned = 0; yield; }
        const name = sName[index], group = members.get(name);
        if (group) group.push(index);
        else members.set(name, [index]);
    }
    return data.members = members;
}
function* selectChildRows(rows, accept = null) {
    const result = [];
    for (let position = 0; position < rows.length; position++) {
        if (position && position % CHILD_WORK_BATCH === 0) yield;
        const row = rows[position];
        if (!accept || accept(row)) result.push(row);
    }
    return result;
}
function* rankChildRowsSteps(data, mode, sort, name, query) {
    const unscoped = name === null && !query, ranking = childRankings[mode];
    const prepared = ranking?.data === data && ranking.sort === sort ? ranking.rows : null;
    if (prepared && unscoped) return prepared;
    const matches = group => (name === null || group.name === name) &&
        (!query || (group.search ??= S[group.name].toLowerCase()).includes(query));
    if (prepared && mode === 'groups') return yield* selectChildRows(prepared, matches);
    const groups = unscoped ? data.groups : yield* selectChildRows(data.groups, matches);
    if (prepared && groups.length === data.groups.length) return prepared;
    if (prepared && name === null) {
        let matching = 0;
        for (let position = 0; position < groups.length; position++) {
            if (position && position % CHILD_WORK_BATCH === 0) yield;
            matching += groups[position].count;
        }
        if (matching * 4 >= prepared.length) {
            const names = new Set();
            for (let position = 0; position < groups.length; position++) {
                if (position && position % CHILD_WORK_BATCH === 0) yield;
                names.add(groups[position].name);
            }
            return yield* selectChildRows(prepared, index => names.has(sName[index]));
        }
    }
    const rows = mode === 'groups' ? (unscoped ? yield* selectChildRows(groups) : groups) :
        groups.length === data.groups.length ? yield* selectChildRows(data.ids) : [];
    if (mode === 'calls' && groups.length > 0 && groups.length < data.groups.length) {
        const members = yield* childMembers(data);
        for (const group of groups) for (const index of members.get(group.name)) {
            if (rows.length && rows.length % CHILD_WORK_BATCH === 0) yield;
            rows.push(index);
        }
    }
    const value = row => {
        if (sort === 'name') return S[mode === 'groups' ? row.name : sName[row]];
        if (mode === 'groups') return sort === 'calls' ? row.count : sort === 'mean' ? childGroupMean(row) : row[sort];
        return sort === 'start' ? sStart[row] : sort === 'self' ? data.selfTimes[row] : data.durations[row];
    };
    const compare = (left, right) => {
        const first = value(left), second = value(right);
        return (typeof first === 'string' ? first.localeCompare(second) : (sort === 'start' ? first - second : second - first)) ||
            (mode === 'groups' ? left.first - right.first : left - right);
    };
    if (rows.length <= CHILD_WORK_BATCH) rows.sort(compare);
    else yield* sortTimelineRow(rows, compare, undefined, CHILD_WORK_BATCH);
    if (unscoped) childRankings[mode] = { data, sort, rows };
    return rows;
}
function* childOrderSteps(mode) {
    const {root, name} = childView, query = normalizedChildQuery(childView.query);
    const sort = mode === 'groups' ? childView.groupSort : childView.callSort;
    const data = yield* childDataSteps(root, textHits);
    const key = JSON.stringify([name, query, sort]), cached = childOrders[mode];
    const order = cached?.data === data && cached.key === key ? cached : { data, key, rows: yield* rankChildRowsSteps(data, mode, sort, name, query) };
    if (childScope?.root === root && childScope.ids !== data.ids) {
        const previous = childScope.ids;
        let same = previous.length === data.ids.length;
        for (let position = 0; same && position < previous.length; position++) {
            if (position && position % CHILD_WORK_BATCH === 0) yield;
            same = previous[position] === data.ids[position];
        }
        if (!same) resetChildPages();
    }
    childScope = { root, ids: data.ids };
    return childOrders[mode] = order;
}
function childRows(rows, mode, selection = sel) {
    const name = value => '<span class="an-child-name" title="' + esc(value) + '">' + esc(value) + '</span>';
    const selectedRow = index => index === selection ? ' class="an-child-selected"' : '';
    const showAttributes = index => ' data-child-show="' + index + '"' + (index === selection ? ' aria-current="true"' : '');
    if (mode === 'groups') return rows.map(group => '<tr' + selectedRow(group.longest) + '><td>' + name(S[group.name]) + '</td><td>' + group.count +
        '</td><td>' + fmtDur(group.inclusive) + '</td><td>' + fmtDur(group.self) + '</td><td>' + fmtDur(childGroupMean(group)) + '</td><td>' + fmtDur(group.max) +
        '</td><td><button class="tl-btn"' + showAttributes(group.longest) + ' aria-label="Show longest ' + esc(S[group.name]) +
        '">Longest</button> <button class="tl-btn" data-child-function="' + group.name + '" aria-label="List calls of ' + esc(S[group.name]) + '">Calls</button></td></tr>').join('');
    return rows.map(index => '<tr data-child-call="' + index + '"' + selectedRow(index) + '><td>' + name(nameOf(index)) +
        '</td><td>' + fmtDur(durOf(index)) + '</td><td>' + fmtDur(sSelf[index]) + '</td><td>' + fmtDur(sStart[index]) + '</td><td>' +
        (cpuOf(index) >= 0 ? cpuOf(index) : '—') + '</td><td>' + name(callerOf(index) || '—') + '</td><td><button class="tl-btn"' + showAttributes(index) +
        '>Show</button> <button class="tl-btn" data-child-details="' + index + '">Details</button></td></tr>').join('');
}
function syncChildRowSelection(table) {
    const previous = table.querySelector('[data-child-show][aria-current=true]');
    if (+previous?.dataset.childShow === sel) return;
    if (previous) {
        previous.removeAttribute('aria-current');
        previous.closest('tr').classList.remove('an-child-selected');
    }
    const current = table.querySelector('[data-child-show="' + sel + '"]');
    if (current) {
        current.setAttribute('aria-current', 'true');
        current.closest('tr').classList.add('an-child-selected');
    }
}
function patchChildPage(body, rows, previous, previousStart) {
    const wanted = new Set(rows), retained = new Map(), removed = [];
    for (const [position, node] of Array.from(body.rows).entries()) {
        const row = previous.rows[previousStart + position];
        if (wanted.has(row)) retained.set(row, node);
        else removed.push(node);
    }
    if (!retained.size) return false;
    const missing = rows.filter(row => !retained.has(row));
    const additions = document.createElement('tbody');
    if (missing.length) additions.innerHTML = childRows(missing, childView.mode, -1);
    for (const node of removed) node.remove();
    let next = body.firstElementChild;
    for (const row of rows) {
        const node = retained.get(row) ?? additions.firstElementChild;
        if (node === next) next = next.nextElementSibling;
        else body.insertBefore(node, next);
    }
    return true;
}
function renderChildPage(table, order, page) {
    const body = table.tBodies[0], previous = table._childOrder, previousStart = table._childPage * CHILD_PAGE;
    const start = page * CHILD_PAGE, rows = order?.rows.slice(start, start + CHILD_PAGE) ?? [];
    const sameData = order && previous?.data === order.data;
    const sameRows = rows.length === body.rows.length && (!rows.length || (sameData &&
        rows.every((row, position) => previous.rows[previousStart + position] === row)));
    if (sameRows || (sameData && rows.length && patchChildPage(body, rows, previous, previousStart))) {
        if (rows.length) syncChildRowSelection(table);
    } else body.innerHTML = childRows(rows, childView.mode);
    table._childOrder = order;
    table._childPage = page;
    return !sameRows;
}
function updateChildFooter(order) {
    const mode = childView.mode, page = childView.pages[mode], start = page * CHILD_PAGE;
    const count = order?.rows.length ?? 0, end = Math.min(start + CHILD_PAGE, count), kind = mode === 'groups' ? 'functions' : 'calls';
    const footer = elDetail.querySelector('.an-child-footer'), controls = footer.querySelector('.an-child-pages');
    const previous = controls.firstElementChild, next = controls.lastElementChild;
    const input = controls.querySelector('[name=page]'), pages = Math.ceil(count / CHILD_PAGE);
    const focused = document.activeElement;
    footer.querySelector('[role=status]').textContent = !order ? 'Preparing child list…' : count ?
        'Showing ' + (start + 1) + '–' + end + ' of ' + count + ' ' + kind : 'No matching ' + kind;
    previous.disabled = !order || page === 0;
    next.disabled = !order || end >= count;
    controls.querySelector('#tlChildPageTotal').textContent = 'of ' + pages;
    input.setCustomValidity('');
    input.value = page + 1;
    controls.hidden = pages <= 1;
    if (controls.hidden && controls.contains(focused) || focused === previous && previous.disabled || focused === next && next.disabled) {
        (!next.disabled ? next : !previous.disabled ? previous : elDetail.querySelector('[data-child-mode="' + mode + '"]')).focus({ preventScroll: true });
    }
}
function positionChildPager(table = elDetail.querySelector('.an-child-table'), height = elDetail.clientHeight) {
    if (!table) return;
    const controls = elDetail.querySelector('.an-child-controls'), footer = elDetail.querySelector('.an-child-footer');
    const short = elDetail.classList.contains('tl-detail-short');
    const inline = !short && height <= 250 && elDetail.clientWidth >= 720 && !footer.querySelector('.an-child-pages').hidden;
    const inlineChanged = controls.classList.contains('an-child-pager-inline') !== inline;
    if (inlineChanged) controls.classList.toggle('an-child-pager-inline', inline);
    const anchor = inline ? controls.querySelector('.an-child-status') : short ? table : elDetail.querySelector('.an-child-hint');
    if (footer.nextElementSibling === anchor) return;
    const focused = document.activeElement;
    anchor.before(footer);
    if (inlineChanged) sizeDetailHead();
    if (footer.contains(focused)) focused.focus({ preventScroll: !short });
}
function changeChildPage(direction, input = null) {
    if (AN.kind !== 'children') return;
    const table = elDetail.querySelector('.an-child-table'), order = table._childOrder;
    if (!order) return;
    const mode = childView.mode, last = Math.max(0, Math.ceil(order.rows.length / CHILD_PAGE) - 1);
    const current = childView.pages[mode];
    let target = Math.max(0, Math.min(last, current + direction));
    if (input) {
        const value = input.value.trim(), number = Number(value);
        if (!/^\d+$/.test(value) || number < 1 || number > last + 1) {
            input.setCustomValidity('Enter a page from 1 to ' + (last + 1) + '.');
            input.reportValidity();
            return;
        }
        target = number - 1;
        input.value = number;
    }
    if (target === current) return;
    childView.pages[mode] = target;
    renderChildPage(table, order, target);
    updateChildFooter(order);
    if (!elDetail.classList.contains('tl-detail-short')) {
        const controls = elDetail.querySelector('.an-child-pager-inline');
        const inset = controls ? elDetail.getBoundingClientRect().top + elDetail.clientTop + controls.offsetHeight : undefined;
        revealDetailTable(table, inset);
    }
}
function showChildCall(index) {
    setSelection(index);
    revealTaskTrack(TRACKS[sTask[index]]);
    zoomTo(sStart[index], sEnd[index], index);
    syncChildRowSelection(elDetail.querySelector('.an-child-table'));
}
const CHILD_CSV_DEFER_ROWS = 4096;
let childCsvJob = null;
function cancelChildrenCsv() {
    const job = childCsvJob;
    if (!job) return;
    childCsvJob = null;
    detailWork.delete('child-csv-order');
    job.button.textContent = job.label;
    job.status.hidden = true;
    job.status.textContent = '';
}
function queueChildrenCsv(afterPaint = false) {
    const job = childCsvJob;
    if (job?.rows) queueTimelineCsv(job, () => childCsvJob === job, cancelChildrenCsv, afterPaint);
}
function childrenCsv(order, scope) {
    const { root, mode, adjusted, filter, query, name } = scope, task = TRACKS[sTask[root]];
    const { durations, selfTimes } = order.data;
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const prefix = ['parent_slice', 'parent_function', 'parent_task_index', 'parent_pid', 'parent_depth'];
    const suffix = ['mode', 'duration_basis', 'position_basis', 'relationship_basis', 'shared_filter', 'function_query', 'function_scope'];
    const columns = prefix.concat(mode === 'groups' ? ['function', 'calls', 'inclusive_' + unit, 'self_' + unit, 'mean_' + unit, 'max_' + unit,
        'first_slice', 'longest_slice'] : ['slice_index', 'function', 'depth', 'depth_gap', 'inclusive_' + unit, 'self_' + unit, 'start_' + unit, 'end_' + unit,
        'cpu', 'trace_line', 'exit_line', 'call_site', 'module', 'return_value', 'arguments'], suffix);
    function* rows() {
        for (const row of order.rows) {
            const values = mode === 'groups' ? [S[row.name], row.count, row.inclusive / scale, row.self / scale, childGroupMean(row) / scale, row.max / scale,
                row.first, row.longest] : [row, nameOf(row), sDepth[row], sDepth[row] - sDepth[root] - 1, durations[row] / scale, selfTimes[row] / scale,
                sStart[row] / scale, sEnd[row] / scale, cpuOf(row), sLine[row], sEline && sEline[row] || '', callerOf(row) || '', modOf(row) || '',
                retOf(row) === null ? '' : retOf(row), argsOf(row) || ''];
            yield [root, nameOf(root), sTask[root], task.pid, sDepth[root]].concat(values, [MODE,
                MODE === 'count' ? 'call_order' : adjusted ? 'adjusted_estimate' : 'measured', MODE === 'count' ? 'call_order' : TIMED ? 'recorded_time' : 'packed_duration',
                'nearest_recorded_parent_not_complete_runtime_callees', filter, query, name === null ? 'all' : S[name]]);
        }
    }
    return { filename: 'funcgraph-children-' + mode + '.csv', columns, rows: rows() };
}
function writeChildrenCsv(job, order, scope, afterPaint = false) {
    if (!order || childCsvJob !== job) return;
    const { filename, columns, rows } = childrenCsv(order, scope);
    Object.assign(job, { filename, rows, total: order.rows.length, written: 0, progress: 0, batchRows: 128,
        parts: ['\ufeff' + columns.join(',') + '\r\n'], scheduled: false });
    job.status.textContent = timelineCsvProgressText(0);
    queueChildrenCsv(afterPaint);
}
function exportChildrenCsv(button) {
    if (AN.kind !== 'children' || detailWork.has('child-order')) return;
    const wasRunning = childCsvJob?.button === button;
    cancelChildrenCsv();
    if (wasRunning) return;
    const mode = button.dataset.childCsv, { root, query, name } = childView;
    const scope = { mode, root, query, name, adjusted: adjOn, filter: window.fgFilterState?.text || '' };
    const steps = (function* () {
        try { return yield* childOrderSteps(mode); }
        catch (error) { cancelChildrenCsv(); toast('Could not export CSV: ' + error.message); return null; }
    })();
    const initial = advanceDetailWork(steps, 3);
    if (initial.done && !initial.value) return;
    if (initial.done && initial.value.rows.length < CHILD_CSV_DEFER_ROWS) {
        const { filename, columns, rows } = childrenCsv(initial.value, scope);
        downloadTimelineCsv(filename, columns, rows);
        return;
    }
    const status = elDetail.querySelector('[data-child-csv-status]');
    const job = childCsvJob = { button, status, label: button.textContent };
    button.textContent = 'Cancel CSV';
    status.hidden = false;
    if (initial.done) writeChildrenCsv(job, initial.value, scope, true);
    else {
        status.textContent = 'Ordering CSV…';
        deferDetailWork('child-csv-order', steps, order => writeChildrenCsv(job, order, scope));
    }
}
function openChildDetails(index, actionsOrigin, expandedForList) {
    childListReturn = { index, root: childView.root, actionsOrigin, expandedForList, expanded: detailExpanded,
        top: elDetail.scrollTop, left: elDetail.scrollLeft };
    select(index, { reveal: true });
    const back = elDetail.querySelector('[data-child-list-return]');
    if (back) {
        resetDetailScroll(back);
        back.focus({ preventScroll: true });
    }
}
function restoreChildList(focus = true) {
    const origin = childListReturn;
    if (!origin || origin.index !== detailSliceIndex) return;
    childListReturn = null;
    showChildren(origin.root, origin.actionsOrigin, { ...origin, focus });
}
function restoreChildListOpener(origin) {
    const opener = origin.focus && elDetail.querySelector('[data-child-details="' + origin.index + '"]');
    if (opener) restoreDetailOpener(opener, origin);
    else {
        elDetail.scrollTo({ top: origin.top, left: origin.left, behavior: 'instant' });
        if (origin.focus) elDetail.querySelector('[data-child-mode="' + childView.mode + '"]')?.focus();
    }
}
function showChildren(root, actionsOrigin = null, returning = null) {
    if (root !== undefined && childView.root !== root) Object.assign(childView, { root, mode: 'groups', name: null, query: '', groupSort: 'inclusive', callSort: 'inclusive', pages: { groups: 0, calls: 0 } });
    if (childView.root < 0 || childView.root >= N) return;
    const reuse = AN.kind === 'children' && elDetail.classList.contains('show') && elDetail.querySelector('.an-child-table');
    cancelChildrenCsv();
    detailWork.delete('child-order');
    AN.kind = 'children';
    const steps = childOrderSteps(childView.mode), initial = advanceDetailWork(steps, 3);
    if (!reuse) {
        const parent = esc(nameOf(childView.root));
        openDetail('<div class="tl-detail-head an-child-head"><div class="an-child-heading"><span class="tl-detail-title">Direct recorded children</span>' +
            '<button class="tl-btn an-child-parent" data-child-parent title="' + parent + '" aria-label="Return to parent call: ' + parent + '">' +
            '<span aria-hidden="true">←</span><span class="an-child-parent-name">' + parent + '</span></button></div>' +
            '<span class="an-child-actions"><button class="tl-btn" data-child-csv="groups">Groups CSV</button>' +
            '<button class="tl-btn" data-child-csv="calls">Calls CSV</button><button class="tl-close" title="Close (Esc)">×</button></span>' +
            '<span class="an-child-csv-status" data-child-csv-status role="status" hidden></span></div>' +
            '<div class="an-child-controls"><div class="an-child-tools"><button class="tl-btn" data-child-mode="groups">Groups</button><button class="tl-btn" data-child-mode="calls">Calls</button>' +
            '<button class="tl-btn an-child-columns" data-child-columns aria-controls="tlChildTable">All columns</button>' +
            '<label>Find <input type="search" data-child-query placeholder="Literal function name" spellcheck="false"></label>' +
            '<span class="an-child-scope"></span><button class="tl-btn" data-child-reset>All functions / reset</button></div>' +
            '<div class="an-child-status"></div></div><table class="tl-sum an-child-table" id="tlChildTable"></table><div class="an-child-footer"><span role="status" aria-live="polite"></span>' +
            '<div class="an-child-pages" hidden><button class="tl-btn" data-child-page="-1" aria-label="Previous child page">' +
            '<span class="an-child-page-label">Previous</span><span class="an-child-page-arrow" aria-hidden="true">‹</span></button>' +
            '<form class="an-child-jump"><label>Page <input type="text" name="page" inputmode="numeric" autocomplete="off" aria-describedby="tlChildPageTotal"></label>' +
            '<span id="tlChildPageTotal"></span><button class="tl-btn" type="submit" aria-label="Go to child page">Go</button></form>' +
            '<button class="tl-btn" data-child-page="1" aria-label="Next child page"><span class="an-child-page-label">Next</span>' +
            '<span class="an-child-page-arrow" aria-hidden="true">›</span></button></div></div><div class="an-hint an-child-hint">Children use the nearest recorded real parent, including depth gaps, ' +
            'not a complete runtime callee inventory. Synthetic frames and grandchildren are excluded. The shared filter matches child calls, not the parent. ' +
            'Groups use function names; inclusive sums can overlap and are not a partition of the parent. Self excludes each child\'s own recorded descendants. ' +
            'Longest/Show highlights a call without leaving this panel; Details or the parent button opens slice details. ' +
            'Overhead updates duration/self estimates, never recorded positions. ' + (MODE === 'count' ? 'Values use call-order units, not nanoseconds. ' :
                TIMED ? '' : 'Positions use packed durations, not elapsed time. ') + 'CSV includes all sorted scoped rows, including unloaded pages.</div>');
        const expandedForList = returning?.expandedForList ?? !!(actionsOrigin && !detailExpanded && elDetail.classList.contains('tl-detail-short'));
        elDetail.querySelector('[data-child-parent]').onclick = () => {
            select(childView.root, { reveal: true });
            if (!actionsOrigin) return;
            if (expandedForList && detailExpanded) setDetailExpanded(false, true);
            const actions = elDetail.querySelector('[data-call-actions]');
            if (!actions) return;
            detailScrollResetPending = false;
            restoreDetailOpener(actions, actionsOrigin);
        };
        elDetail.querySelector('[data-child-query]').addEventListener('input', event => {
            const previous = normalizedChildQuery(childView.query);
            childView.query = event.target.value;
            if (normalizedChildQuery(childView.query) !== previous) { resetChildPages(); showChildren(); }
        });
        elDetail.querySelector('[data-child-reset]').onclick = () => { childView.query = ''; childView.name = null; resetChildPages(); showChildren(); };
        elDetail.querySelectorAll('[data-child-mode]').forEach(button => { button.onclick = () => { childView.mode = button.dataset.childMode; showChildren(); }; });
        elDetail.querySelectorAll('[data-child-csv]').forEach(button => { button.onclick = () => exportChildrenCsv(button); });
        elDetail.querySelectorAll('[data-child-page]').forEach(button => { button.onclick = () => changeChildPage(+button.dataset.childPage); });
        const jump = elDetail.querySelector('.an-child-jump'), pageInput = jump.elements.page;
        pageInput.oninput = () => pageInput.setCustomValidity('');
        jump.onsubmit = event => { event.preventDefault(); changeChildPage(0, pageInput); };
        const table = elDetail.querySelector('.an-child-table'), columns = elDetail.querySelector('[data-child-columns]');
        const syncColumns = () => {
            columns.setAttribute('aria-pressed', String(childView.allColumns));
            table.classList.toggle('an-child-all', childView.allColumns);
        };
        columns.onclick = () => { childView.allColumns = !childView.allColumns; syncColumns(); };
        syncColumns();
        table.onclick = event => {
            const sort = event.target.closest('[data-child-sort]'), scope = event.target.closest('[data-child-function]');
            const show = event.target.closest('[data-child-show]'), details = event.target.closest('[data-child-details]');
            if (sort) {
                childView.pages[childView.mode] = 0;
                childView[childView.mode === 'groups' ? 'groupSort' : 'callSort'] = sort.dataset.childSort; showChildren();
            } else if (scope) {
                childView.name = +scope.dataset.childFunction; childView.mode = 'calls'; resetChildPages(); showChildren();
                elDetail.querySelector('[data-child-mode=calls]').focus({ preventScroll: true });
            }
            else if (show) showChildCall(+show.dataset.childShow);
            else if (details) openChildDetails(+details.dataset.childDetails, actionsOrigin, expandedForList);
        };
        const expanded = returning?.expanded ?? (detailExpanded || expandedForList);
        if (expanded !== detailExpanded) setDetailExpanded(expanded, true);
        elDetail.scrollTop = 0;
    }
    const input = elDetail.querySelector('[data-child-query]');
    if (input.value !== childView.query) input.value = childView.query;
    elDetail.querySelector('.an-child-scope').textContent = childView.name === null ? '' : S[childView.name];
    elDetail.querySelector('.an-child-scope').title = childView.name === null ? '' : S[childView.name];
    elDetail.querySelectorAll('[data-child-mode]').forEach(button => {
        const selected = button.dataset.childMode === childView.mode;
        button.classList.toggle('on', selected); button.setAttribute('aria-pressed', String(selected));
    });
    paintChildren(initial.done ? initial.value : null, !!reuse);
    if (initial.done) {
        if (returning) restoreChildListOpener(returning);
    } else {
        const fallback = returning?.focus ? elDetail.querySelector('[data-child-mode="' + childView.mode + '"]') : null;
        fallback?.focus({ preventScroll: true });
        deferDetailWork('child-order', steps, order => {
            paintChildren(order, false);
            if (returning && (!returning.focus || document.activeElement === fallback)) restoreChildListOpener(returning);
        });
    }
}
function paintChildren(order, resetScroll) {
    const mode = childView.mode, table = elDetail.querySelector('.an-child-table');
    if (order) childView.pages[mode] = Math.min(childView.pages[mode], Math.max(0, Math.ceil(order.rows.length / CHILD_PAGE) - 1));
    const page = childView.pages[mode], sort = mode === 'groups' ? childView.groupSort : childView.callSort;
    table.setAttribute('aria-busy', String(!order));
    elDetail.querySelectorAll('[data-child-csv]').forEach(button => {
        if (order) button.removeAttribute('aria-disabled');
        else button.setAttribute('aria-disabled', 'true');
    });
    const status = elDetail.querySelector('.an-child-status'), data = order?.data;
    status.hidden = !data;
    status.textContent = data ? data.ids.length + ' of ' + data.raw.ids.length + ' direct recorded calls match the shared filter · ' + data.groups.length + ' functions' : '';
    const heading = mode + ':' + sort;
    if (table._childHeading !== heading) {
        table.dataset.childTableMode = mode;
        const focusedSort = table.contains(document.activeElement) ? document.activeElement.dataset.childSort : undefined;
        const columns = mode === 'groups' ? [['name', 'function'], ['calls', 'calls'], ['inclusive', 'inclusive sum'], ['self', 'self sum'],
            ['mean', 'mean'], ['max', 'max']] : [['name', 'function'], ['inclusive', 'inclusive'], ['self', 'self'], ['start', 'start'], ['', 'CPU'], ['', 'call site']];
        table.innerHTML = '<thead><tr>' + columns.map(([key, label]) => '<th' + (key ? ' aria-sort="' + (key === sort ?
            (key === 'name' || key === 'start' ? 'ascending' : 'descending') : 'none') + '"><button class="tl-sum-sort" data-child-sort="' + key + '">' + label + '</button>' : '>' + label) +
            '</th>').join('') + '<th>navigate</th></tr></thead><tbody></tbody>';
        table._childHeading = heading;
        table._childOrder = undefined;
        if (focusedSort) table.querySelector('[data-child-sort="' + focusedSort + '"]')?.focus({preventScroll: true});
    }
    if (table._childOrder !== order || order && table._childPage !== page) {
        const previousPage = table._childPage, changed = renderChildPage(table, order, page);
        if (resetScroll && (changed || page !== previousPage)) elDetail.scrollTop = 0;
    }
    let empty = elDetail.querySelector('.an-child-empty');
    if (!empty) { empty = document.createElement('div'); empty.className = 'an-hint an-child-empty'; table.after(empty); }
    empty.textContent = !order || order.rows.length ? '' : 'No direct recorded children match the current scope. Reset or change the shared filter.';
    updateChildFooter(order);
    positionChildPager(table);
}
function* scanCallSubtreeSteps(root, visit, measure) {
    if (sFlags[root] & FL.LEAF) return { nCalls: 0, interrupted: 0, irq: 0, callees: 0 };
    const { offset, first, next } = yield* callTreeSteps(root);
    const context = measure ? sFlags[root] & FL.CTX : 0;
    let nCalls = 0, interrupted = 0, irq = 0, callees = 0, interruptRoot = -1;
    let current = first[root - offset], work = 0;
    while (current >= 0) {
        if ((++work & 4095) === 0) yield;
        if (visit) visit(current);
        if (measure) {
            nCalls++;
            if (interruptRoot < 0 && (sFlags[current] & FL.CTX) > context) {
                interruptRoot = current;
                irq += sEnd[current] - sStart[current];
            }
            if (interruptRoot >= 0) interrupted++;
            else callees += sSelfRaw[current];
        }
        if (first[current - offset] >= 0) { current = first[current - offset]; continue; }
        while (current !== root) {
            if (current === interruptRoot) interruptRoot = -1;
            if (next[current - offset] >= 0) break;
            current = sParent[current];
            if ((++work & 4095) === 0) yield;
        }
        if (current === root) break;
        current = next[current - offset];
    }
    return { nCalls, interrupted, irq, callees };
}
// Partition of the measured wall time of slice i:
// own self | traced callees (on-CPU self) | hardirq/softirq that interrupted it | off-CPU (blocked / preempted)
function* breakdownSteps(index, visit) {
    const cached = breakdownCache.get(index);
    if (cached && !visit) return cached;
    const tree = yield* scanCallSubtreeSteps(index, visit, !cached);
    if (cached) return cached;
    const { irq, nCalls, interrupted } = tree;
    let callees = tree.callees;
    let own = sSelfRaw[index], offB = 0, offP = 0;
    const track = TRACKS[sTask[index]], start = sStart[index], end = sEnd[index], depth = sDepth[index];
    let prepared = 0;
    for (const [first, stop] of offIntervalBlocks(track, start, end)) {
        for (let ordinal = first; ordinal < stop; ordinal++) {
            const record = track.off[ordinal];
            if (record[2] <= start || record[1] >= end || record[3] <= depth) continue;
            const overlap = Math.min(record[2], end) - Math.max(record[1], start);
            if (record[8]) offP += overlap; else offB += overlap;
            if (record[3] - 1 === depth) own -= overlap; else callees -= overlap;
        }
        prepared += stop - first;
        if (prepared >= PREPARE_BATCH) { prepared = 0; yield; }
    }
    own = Math.max(0, own); callees = Math.max(0, callees);
    const ovh = D.overhead ? Math.min(own + callees, D.overhead.child * (nCalls - interrupted) + D.overhead.self) : 0;
    const result = { wall: end - start, own, callees, irq, offB, offP, ovh, nCalls };
    if (breakdownCache.size >= 256) breakdownCache.delete(breakdownCache.keys().next().value);
    breakdownCache.set(index, result);
    return result;
}
function breakdownHtml(b) {
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
    return rows.map(row => '<tr data-site="' + esc(row.site) + '"><td><button type="button" class="an-site-btn" aria-pressed="' + (AN.site === row.site) + '">' +
        esc(row.site) + '</button></td><td>' + row.ids.length + '</td><td>' + fmtDur(distQuantile(row.ids, 0.5)) + '</td><td>' +
        fmtDur(distQuantile(row.ids, 0.99)) + '</td><td>' + fmtDur(distQuantile(row.ids, 1)) + '</td></tr>').join('');
}
function distSiteStatus(start, end, count) {
    return (start + 1) + '–' + end + ' of ' + count + ' call sites';
}
function callSiteTable(data) {
    const rows = data.siteRows;
    if (!distHasSites(data)) return '';
    const start = distView.sitePage * DIST_SITE_PAGE, end = Math.min(start + DIST_SITE_PAGE, rows.length);
    let html = '<div class="tl-sec an-site-heading" tabindex="-1" role="heading" aria-level="3">By call site</div>';
    if (rows.length > DIST_SITE_PAGE) html += '<div class="an-dist-tools an-site-pager"><span class="an-site-count" role="status" aria-atomic="true">' + distSiteStatus(start, end, rows.length) +
        '</span><span class="an-site-pages" role="group" aria-label="Call-site pages">' +
        '<button class="tl-btn" type="button" data-act="dist-sites-prev" aria-label="Previous call-site page"' + (start ? '' : ' disabled') + '>Previous</button>' +
        '<button class="tl-btn" type="button" data-act="dist-sites-next" aria-label="Next call-site page"' + (end < rows.length ? '' : ' disabled') + '>Next</button></span></div>';
    return html + '<table class="tl-sum an-tbl an-sites" aria-label="By call site"><thead><tr><th>caller</th><th>calls</th><th>p50</th><th>p99</th><th>max</th></tr></thead><tbody>' +
        (rows.length ? distSiteRows(rows.slice(start, end)) : '<tr><td colspan="5">No matching call sites</td></tr>') + '</tbody></table>';
}
function changeDistSitePage(direction) {
    const rows = distView.data.siteRows, page = distView.sitePage + direction;
    const start = page * DIST_SITE_PAGE, end = Math.min(start + DIST_SITE_PAGE, rows.length);
    if (start < 0 || start >= rows.length) return;
    distView.sitePage = page;
    elDetail.querySelector('.an-sites tbody').innerHTML = distSiteRows(rows.slice(start, end));
    const pager = elDetail.querySelector('.an-site-pager');
    pager.querySelector('.an-site-count').textContent = distSiteStatus(start, end, rows.length);
    const previous = pager.querySelector('[data-act=dist-sites-prev]'), next = pager.querySelector('[data-act=dist-sites-next]');
    const focused = document.activeElement;
    previous.disabled = start === 0;
    next.disabled = end === rows.length;
    if (focused === next && next.disabled) previous.focus({ preventScroll: true });
    else if (focused === previous && previous.disabled) next.focus({ preventScroll: true });
    revealDetailTable(pager);
}
const SLOW_FAST_PAGE = 100;
let slowFastCache = null, slowFastView = null;
function* cohortProfile(ids, durations, selfTimes) {
    const functions = new Map();
    let irq = 0, offB = 0, offP = 0, own = 0, duration = 0;
    const collect = index => {
        let entry = functions.get(sName[index]);
        if (!entry) { entry = { calls: 0, self: 0, example: index }; functions.set(sName[index], entry); }
        entry.calls++;
        entry.self += selfTimes[index];
    };
    for (let ordinal = 0; ordinal < ids.length; ordinal++) {
        if (ordinal && ordinal % 256 === 0) yield;
        const root = ids[ordinal], parts = yield* breakdownSteps(root, collect);
        irq += parts.irq; offB += parts.offB; offP += parts.offP;
        own += Math.max(0, parts.own - (sSelfRaw[root] - selfTimes[root]));
        duration += durations[root];
    }
    return { ids, functions, irq: irq / ids.length, offB: offB / ids.length, offP: offP / ids.length,
        own: own / ids.length, duration: duration / ids.length };
}
function slowFastData() {
    const ids = distView.population.ids;
    return slowFastCache?.ids === ids && slowFastCache.durations === sDur ? slowFastCache : null;
}
function* slowFastDataSteps(ids, durations, selfTimes) {
    const fast = yield* cohortProfile(ids.slice(-Math.floor(ids.length / 2)), durations, selfTimes);
    const slow = yield* cohortProfile(ids.slice(0, Math.ceil(ids.length / 10)), durations, selfTimes);
    const functions = new Map(), rows = [];
    let merged = 0;
    for (const [cohort, profile] of [['fast', fast], ['slow', slow]]) {
        for (const [name, entry] of profile.functions) {
            if (merged && merged % PREPARE_BATCH === 0) yield;
            merged++;
            let row = functions.get(name);
            if (!row) {
                row = { kind: 'function', name: S[name], nameIndex: name, fastCalls: 0, slowCalls: 0,
                    fastSelf: 0, slowSelf: 0, fastExample: -1, slowExample: -1 };
                functions.set(name, row);
                rows.push(row);
            }
            row[cohort + 'Calls'] = entry.calls / profile.ids.length;
            row[cohort + 'Self'] = entry.self / profile.ids.length;
            row[cohort + 'Example'] = entry.example;
        }
    }
    for (const [kind, name, field] of [['own', '(own code)', 'own'], ['irq', '(interrupts / softirq)', 'irq'],
        ['blocked', '(blocked off-CPU)', 'offB'], ['preempted', '(preempted)', 'offP']]) {
        rows.push({ kind, name, nameIndex: -1, fastCalls: kind === 'own' ? 1 : null, slowCalls: kind === 'own' ? 1 : null,
            fastSelf: fast[field], slowSelf: slow[field], fastExample: -1, slowExample: -1 });
    }
    for (let ordinal = 0; ordinal < rows.length; ordinal++) {
        if (ordinal && ordinal % PREPARE_BATCH === 0) yield;
        const row = rows[ordinal];
        row.delta = row.slowSelf - row.fastSelf;
    }
    return { ids, durations, fast, slow, rows };
}
function* slowFastOrderSteps(data, key, ascending) {
    const direction = ascending ? 1 : -1;
    const compare = (left, right) => {
        const order = key === 'name' ? (left.name < right.name ? -1 : left.name > right.name ? 1 : 0) : (left[key] || 0) - (right[key] || 0);
        return direction * order || (left.name < right.name ? -1 : left.name > right.name ? 1 : 0) || left.kind.localeCompare(right.kind);
    };
    const ordered = data.rows.slice();
    if (ordered.length <= PREPARE_BATCH) ordered.sort(compare);
    else yield* sortTimelineRow(ordered, compare);
    return ordered;
}
function prepareSlowFast(focusAfterOrdering) {
    if (!distView.slowFastOpen || distView.population.ids.length < 6) return false;
    const cached = slowFastData();
    if (cached && slowFastView.ordered) return false;
    const ids = distView.population.ids, durations = sDur, selfTimes = sSelf;
    const { sort = 'delta', ascending = false, page = 0 } = slowFastView || {};
    const steps = (function* () {
        const data = cached || (yield* slowFastDataSteps(ids, durations, selfTimes));
        const ordered = yield* slowFastOrderSteps(data, sort, ascending);
        return { data, sort, ascending, page: cached ? page : 0, ordered };
    })();
    const store = prepared => { slowFastView = prepared; slowFastCache = prepared.data; };
    const initial = advanceDetailWork(steps, 3);
    if (initial.done) { store(initial.value); return true; }
    deferDetailWork('slowfast', steps, prepared => {
        const keepFocus = document.activeElement.matches('[data-act=slowfast]');
        store(prepared);
        renderSlowFast();
        if (keepFocus) elDetail.querySelector(focusAfterOrdering || '[data-act=slowfast]')?.focus({preventScroll: true});
    });
    return false;
}
function slowFastRows(rows) {
    return rows.map(row => '<tr data-sf-kind="' + row.kind + '" data-sf-name="' + row.nameIndex + '"><td><span class="an-slowfast-name" title="' + esc(row.name) + '">' + esc(row.name) + '</span></td><td>' +
        (row.fastCalls === null ? '' : row.fastCalls.toFixed(2)) + '</td><td>' + (row.slowCalls === null ? '' : row.slowCalls.toFixed(2)) +
        '</td><td>' + fmtDur(row.fastSelf) + '</td><td>' + fmtDur(row.slowSelf) + '</td><td class="' + (row.delta > 0 ? 'err' : '') + '">' +
        (row.delta > 0 ? '+' : '') + fmtDur(row.delta) + '</td><td>' +
        [['fast', row.fastExample], ['slow', row.slowExample]].filter(entry => entry[1] >= 0).map(([cohort, index]) =>
            '<button class="tl-btn" data-sf-example="' + index + '" aria-label="Show ' + cohort + ' example of ' + esc(row.name) + '">' + cohort + '</button>').join(' ') + '</td></tr>').join('');
}
function slowFastPageStatus(start, end, count) {
    return (start + 1) + '–' + end + ' of ' + count + ' profile rows';
}
function slowFastHtml() {
    if (!distView.slowFastOpen) return '<div class="an-slowfast-tools"><button class="tl-btn" type="button" data-act="slowfast" aria-expanded="false">Slow vs fast: what do the slow calls do differently?</button></div>';
    const data = slowFastData();
    let html = '<div class="an-slowfast-tools"><span class="tl-sec">Slow vs fast</span><button class="tl-btn" type="button" data-act="slowfast" aria-expanded="true">Hide comparison</button>';
    if (distView.population.ids.length < 6) return html + '</div><div class="an-hint">Slow vs fast needs at least 6 matching calls. Change the filter or call site to restore the population.</div>';
    if (!data || !slowFastView.ordered) return html + '</div><div class="an-hint an-slowfast-pending" role="status">' +
        (data ? 'Ordering comparison…' : 'Preparing comparison…') + '</div>';
    html += '<button class="tl-btn" type="button" data-act="slowfast-csv">Export profile CSV</button>' +
        '<button class="tl-btn" type="button" data-act="slowfast-cohorts-csv">Export cohorts CSV</button>' +
        '<span class="an-slowfast-csv-status" role="status" aria-atomic="true" hidden></span></div>' +
        '<div class="an-hint an-slowfast-hint">Per selected call: slowest ' + data.slow.ids.length + ' (avg ' + fmtDur(data.slow.duration) + ') vs fastest ' +
        data.fast.ids.length + ' (avg ' + fmtDur(data.fast.duration) + '). Self follows Overhead; scheduling-context rows use recorded geometry.' +
        '<details><summary>Scope and interpretation</summary>Roots follow the function, shared filter and call site, not the duration bucket. Descendants include all real traced calls, even outside the shared filter; synthetic inline/untraced frames are excluded. ' +
        'Cohorts use descending duration, then original slice index; the fastest half and slowest ceil(10%) are disjoint, including ties. Recursive descendants contribute once per selected ancestor. ' +
        'Function self may include off-CPU time; context rows are separate observations, not additive with function rows. Own code is on-CPU self minus the estimated self overhead, clamped at zero. ' +
        'Differences are associations, not proof of a cause. Count/packed geometry is not elapsed scheduling time.</details></div>';
    const rows = slowFastView.ordered, columns = [['name', 'function inside'], ['fastCalls', 'calls fast'], ['slowCalls', 'calls slow'],
        ['fastSelf', 'self / context fast'], ['slowSelf', 'self / context slow'], ['delta', 'Δ value']];
    const start = slowFastView.page * SLOW_FAST_PAGE, end = Math.min(start + SLOW_FAST_PAGE, rows.length);
    html += '<div class="an-slowfast-pager"><span role="status" aria-atomic="true">' + slowFastPageStatus(start, end, rows.length) + '</span>';
    if (rows.length > SLOW_FAST_PAGE) html += '<span class="an-slowfast-pages" role="group" aria-label="Slow vs fast profile pages">' +
        '<button class="tl-btn" type="button" data-act="slowfast-prev" aria-label="Previous profile page"' + (start ? '' : ' disabled') + '>Previous</button>' +
        '<button class="tl-btn" type="button" data-act="slowfast-next" aria-label="Next profile page"' + (end < rows.length ? '' : ' disabled') + '>Next</button></span>';
    return html + '</div><table class="tl-sum an-tbl an-slowfast-table" aria-label="Slow vs fast profile"><thead><tr>' + columns.map(([key, label]) => '<th data-sf-sort="' + key +
        '" aria-sort="' + (slowFastView.sort === key ? slowFastView.ascending ? 'ascending' : 'descending' : 'none') + '"><button class="tl-sum-sort">' + label +
        '</button></th>').join('') + '<th>examples</th></tr></thead><tbody>' + slowFastRows(rows.slice(start, end)) + '</tbody></table>';
}
function renderSlowFast() {
    cancelSlowFastCsv();
    elDetail.querySelector('.an-slowfast').innerHTML = slowFastHtml();
    sizeDetailHead();
}
function changeSlowFastPage(direction) {
    const rows = slowFastView.ordered, page = slowFastView.page + direction;
    const start = page * SLOW_FAST_PAGE, end = Math.min(start + SLOW_FAST_PAGE, rows.length);
    if (start < 0 || start >= rows.length) return;
    slowFastView.page = page;
    const table = elDetail.querySelector('.an-slowfast-table');
    table.tBodies[0].innerHTML = slowFastRows(rows.slice(start, end));
    const pager = elDetail.querySelector('.an-slowfast-pager');
    pager.querySelector('[role=status]').textContent = slowFastPageStatus(start, end, rows.length);
    const previous = pager.querySelector('[data-act=slowfast-prev]'), next = pager.querySelector('[data-act=slowfast-next]');
    const focused = document.activeElement;
    previous.disabled = start === 0;
    next.disabled = end === rows.length;
    if (focused === next && next.disabled) previous.focus({ preventScroll: true });
    else if (focused === previous && previous.disabled) next.focus({ preventScroll: true });
    revealDetailTable(pager);
}
const SLOW_FAST_CSV_DEFER_ROWS = 4096;
let slowFastCsvJob = null;
function cancelSlowFastCsv() {
    const job = slowFastCsvJob;
    if (!job) return;
    slowFastCsvJob = null;
    job.button.textContent = job.label;
    job.other.disabled = false;
    job.status.hidden = true;
    job.status.textContent = '';
}
function queueSlowFastCsv(afterPaint = false) {
    const job = slowFastCsvJob;
    if (job) queueTimelineCsv(job, () => slowFastCsvJob === job, cancelSlowFastCsv, afterPaint);
}
function slowFastBasis(kind, adjusted) {
    if (kind === 'function' || kind === 'own') return MODE === 'count' ? 'call_order' : adjusted ? 'overhead_subtracted_estimate' : 'raw_self';
    return TIMED ? 'recorded_scheduling_geometry' : MODE === 'count' ? 'call_order_geometry' : 'packed_geometry';
}
function exportSlowFastCsv(button) {
    if (slowFastCsvJob) {
        if (slowFastCsvJob.button === button) cancelSlowFastCsv();
        return;
    }
    const data = slowFastData();
    if (!data || !slowFastView.ordered) return;
    const cohorts = button.dataset.act === 'slowfast-cohorts-csv', ordered = slowFastView.ordered;
    const { durations, fast, slow } = data, selfTimes = sSelf, adjusted = adjOn;
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const filter = window.fgFilterState?.text || '', site = AN.site || '';
    const selectionBasis = MODE === 'count' ? 'call_order' : adjusted ? 'overhead_subtracted_estimate' : 'measured_duration';
    const columns = cohorts ? ['cohort', 'slice_index', 'function', 'trace_line', 'task_index', 'pid', 'comm', 'caller',
        'duration_' + unit, 'self_' + unit, 'start_' + unit, 'end_' + unit, 'time_mode', 'duration_basis', 'filter', 'call_site_filter'] :
        ['kind', 'function', 'name_index', 'fast_calls_per_root', 'slow_calls_per_root', 'fast_self_per_root_' + unit, 'slow_self_per_root_' + unit,
        'delta_self_per_root_' + unit, 'fast_example_slice', 'slow_example_slice', 'fast_roots', 'slow_roots', 'population_calls',
        'mean_fast_duration_' + unit, 'mean_slow_duration_' + unit, 'time_mode', 'selection_basis', 'value_basis', 'filter', 'call_site_filter'];
    function* rows() {
        if (cohorts) {
            for (const [cohort, profile] of [['fast', fast], ['slow', slow]]) for (const index of profile.ids) {
                const task = TRACKS[sTask[index]];
                yield [cohort, index, nameOf(index), sLine[index], sTask[index], task.pid, task.comm, callerOf(index) || '',
                    durations[index] / scale, selfTimes[index] / scale, sStart[index] / scale, sEnd[index] / scale, MODE, selectionBasis, filter, site];
            }
        } else for (const row of ordered) yield [row.kind, row.name, row.nameIndex < 0 ? '' : row.nameIndex,
            row.fastCalls === null ? '' : row.fastCalls, row.slowCalls === null ? '' : row.slowCalls, row.fastSelf / scale, row.slowSelf / scale,
            row.delta / scale, row.fastExample < 0 ? '' : row.fastExample, row.slowExample < 0 ? '' : row.slowExample,
            fast.ids.length, slow.ids.length, data.ids.length, fast.duration / scale, slow.duration / scale,
            MODE, selectionBasis, slowFastBasis(row.kind, adjusted), filter, site];
    }
    const filename = 'funcgraph-slowfast-' + (cohorts ? 'cohorts' : 'profile') + '-' + (MODE === 'count' ? 'count' : adjusted ? 'adjusted' : 'raw') + '.csv';
    const total = cohorts ? fast.ids.length + slow.ids.length : ordered.length;
    if (total < SLOW_FAST_CSV_DEFER_ROWS) { downloadTimelineCsv(filename, columns, rows()); return; }
    const tools = button.closest('.an-slowfast-tools');
    const status = tools.querySelector('.an-slowfast-csv-status');
    const other = tools.querySelector(cohorts ? '[data-act=slowfast-csv]' : '[data-act=slowfast-cohorts-csv]');
    slowFastCsvJob = {button, other, status, label: button.textContent, filename, rows: rows(), total, written: 0, progress: 0, batchRows: 128,
        parts: ['\ufeff' + columns.join(',') + '\r\n'], scheduled: false};
    button.textContent = 'Cancel CSV';
    other.disabled = true;
    status.textContent = timelineCsvProgressText(0);
    status.hidden = false;
    queueSlowFastCsv(true);
}

// ---- critical sections: irqs-off / preempt-off / BH-off ----
AN.secKind = 0;
AN.secSort = 'dur';
function sectionRows(rows) {
    return rows.map((record, index) => {
        const raw = record.raw, task = trackLabel(TRACKS[record.track]);
        return '<tr data-sec="' + record.index + '">' + windowJumpCell(record, task, index === 0, 'section') +
            '<td style="text-align:left">' + esc(task) + '</td><td style="text-align:left">' +
            (raw[4] >= 0 ? esc(nameOf(raw[4])) : '<span class="d">top level</span>') + '</td><td style="text-align:left">' +
            (raw[5] >= 0 ? esc(nameOf(raw[5])) : '<span class="d">' + (raw[8] < 0 ? 'switched out / end' : 'interrupt') + '</span>') + '</td>' +
            (AN.secKind === 1 ? '<td>' + record.depth + '</td>' : '') +
            '<td style="text-align:left">' + windowTopHtml(windowProfile(record)) + '</td></tr>';
    }).join('');
}
function showSections() {
    AN.kind = 'sec';
    const data = sectionWindows(), order = windowOrder(data, AN.secKind, AN.secSort);
    let h = '<div class="tl-detail-head an-window-head"><span class="tl-detail-title">Critical sections</span><span class="tl-seg an-seckind">';
    for (let kind = 0; kind < 3; kind++) h += '<button data-sk="' + kind + '" class="' + (kind === AN.secKind ? 'on' : '') + '">' + SEC_NAMES[kind] + ' (' + data.groups[kind].length + ')</button>';
    h += '</span>' + pill('mute', 'total ' + fmtDur(data.totals[AN.secKind])) + pill('warn', 'max ' + fmtDur(data.maxima[AN.secKind])) +
        (textHits ? pill('warn', 'filtered') : '') + (adjOn ? pill('warn', 'self: overhead subtracted') : '') + windowActions(order.rows.length);
    if (!D.caps.latency) {
        openWindowPanel(h + '<div class="an-hint">Needs <code>options/latency-format</code> (irqs-off / preempt-depth / BH flags).</div>'); return;
    }
    h += windowTimeHint() + '<div class="an-hint" style="padding:6px 12px 0">Regions where a task ran with ' + SEC_NAMES[AN.secKind] + ' (task context; preempt depth excludes inferred tracing overhead). ' +
        'The flag changes inside untraced code, so start/end are the traced frames around the change. ' +
        'Self times are proportional overlap estimates from matching calls, not exact on-CPU or causal attribution. Overhead changes self estimates, not section positions or lengths.</div>';
    if (!order.rows.length) {
        openWindowPanel(h + '<div class="an-hint">' + (textHits ? 'No sections overlap calls matching the current filter.' : 'No sections of this kind in this trace.') + '</div>'); return;
    }
    const columns = [['dur', 'duration'], ['start', 'start'], ['task', 'task', 1], [null, 'started in', 1], [null, 'ended in', 1]];
    if (AN.secKind === 1) columns.push(['pre', 'max depth']);
    columns.push(['top', 'top self (est.)', 1]);
    h += windowTableStart() + windowTableHead(columns, AN.secSort, 'ssort') +
        '</thead><tbody>' + sectionRows(visibleWindowRows(order)) + '</tbody></table>' + windowFooter(order, true);
    openWindowPanel(h);
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
        sortWindowTable(sort, true);
        ev.stopImmediatePropagation(); return;
    }
    const sr = ev.target.closest('tr[data-sec]');
    if (sr) {
        const record = SECTIONS[+sr.dataset.sec];
        jumpToWindow(record[2], record[3], record[0]);
        ev.stopImmediatePropagation(); return;
    }
    const st = ev.target.closest('tr[data-site]');
    if (st && AN.kind === 'dist') {
        changeDistSite(AN.site === st.dataset.site ? null : st.dataset.site);
        ev.stopImmediatePropagation(); return;
    }
    const slowFastAction = ev.target.closest('.an-slowfast [data-act]');
    if (slowFastAction) {
        const action = slowFastAction.dataset.act;
        if (action === 'slowfast') {
            distView.slowFastOpen = !distView.slowFastOpen;
            if (distView.slowFastOpen) prepareSlowFast();
            else detailWork.delete('slowfast');
            renderSlowFast();
            const toggle = elDetail.querySelector('[data-act=slowfast]');
            if (distView.slowFastOpen) revealDetailTable(toggle.closest('.an-slowfast-tools'));
            toggle.focus({ preventScroll: true });
        } else if (action === 'slowfast-prev' || action === 'slowfast-next') changeSlowFastPage(action === 'slowfast-next' ? 1 : -1);
        else if (action === 'slowfast-csv' || action === 'slowfast-cohorts-csv') exportSlowFastCsv(slowFastAction);
        ev.stopImmediatePropagation(); return;
    }
    const slowFastSort = ev.target.closest('[data-sf-sort]');
    if (slowFastSort) {
        const key = slowFastSort.dataset.sfSort;
        slowFastView.ascending = slowFastView.sort === key ? !slowFastView.ascending : key === 'name';
        slowFastView.sort = key; slowFastView.page = 0; slowFastView.ordered = null;
        const selector = '[data-sf-sort="' + key + '"] button';
        prepareSlowFast(selector);
        renderSlowFast();
        (elDetail.querySelector(selector) || elDetail.querySelector('[data-act=slowfast]')).focus({ preventScroll: true });
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
