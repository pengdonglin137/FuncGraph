/* FuncGraph Timeline: data model + DOM skeleton.
 * model/render/interact/panels.js are concatenated (in that order) inside function __tlBoot() and share its scope. */
yield 'Reading Timeline data…';
const tlRoot = document.getElementById('timelineView');
const input = window.fgData.read('tlData');
const D = { ...input, strings: input.strings.slice(), offcpu: input.offcpu.map(record => record.slice()) };
const N = D.n, C = D.cols, S = D.strings;
const MODE = D.mode;
const TIMED = MODE === 'abs' || MODE === 'rel';
const FL = {
    INL: 1 << 23,      // synthetic inline frame (front-end only)
    GHOST: 1 << 24,    // synthetic untraced frame filling a depth gap (front-end only)
    CTX: 3, IRQOFF: 1 << 2, BHOFF: 1 << 3, RS_SHIFT: 4, PR_SHIFT: 7, MG_SHIFT: 11,
    LAT: 1 << 15, TRUNC: 1 << 16, ORPHAN: 1 << 17, EST: 1 << 18, MISMATCH: 1 << 19,
    NULLRET: 1 << 20, LEAF: 1 << 21, CTXINF: 1 << 22, USER: 1 << 25, BOUNDARY: 1 << 26
};
const HAS_USER = D.uftrace?.spaces.includes('user');
const HAS_KERNEL = !D.uftrace || D.uftrace.spaces.includes('kernel');
const MIXED = HAS_USER && HAS_KERNEL;
const CTX_NAMES = ['task', 'softirq', 'hardirq', 'NMI'];
const RESCHED_NAMES = ['', 'need-resched (n)', 'need-resched + preempt (N)', 'preempt-resched (p)',
    'lazy (l)', 'lazy + preempt (L)', 'need + lazy (b)', 'need + lazy + preempt (B)'];

yield 'Preparing call columns…';
// ---- slice columns ----
// Real slices are 0..N-1. Inline frames (see buildInline) are appended as synthetic
// slices N..NA-1; loops over i < N therefore only see traced calls.
let sStart = new Float64Array(N), sEnd = new Float64Array(N);
let sDepth = Uint16Array.from(C.depth), sTask = Int32Array.from(C.task);
let sFlags = Uint32Array.from(C.flags), sName = Int32Array.from(C.name);
let sParent = Int32Array.from(C.parent), sLine = Int32Array.from(C.line);
const sEline = C.eline ? Int32Array.from(C.eline) : null;
const sRet = C.ret, sCaller = C.caller, sArgs = C.args, sMod = C.module, sCpu = C.cpu;
(function decodeTimes() {
    let pt = -1, acc = 0;
    for (let i = 0; i < N; i++) {
        if (sTask[i] !== pt) { pt = sTask[i]; acc = 0; }
        acc += C.start[i];
        sStart[i] = acc;
        sEnd[i] = acc + C.dur[i];
    }
})();
// Durations shown in tooltips, panels and summaries. Geometry always uses the measured
// sStart/sEnd (wall clock); with an overhead estimate the user can switch to corrected values.
const sDurRaw = Float64Array.from(C.dur);
const sDurAdj = C.adj ? Float64Array.from(C.adj) : null;
let sDur = sDurRaw;
let adjOn = false;
function durOf(i) { return sDur[i]; }
const sSelf = new Float64Array(N);
let maxSelf = 1;
function computeSelf() {
    for (let i = 0; i < N; i++) sSelf[i] = sDur[i];
    for (let i = 0; i < N; i++) { const p = sParent[i]; if (p >= 0) sSelf[p] -= sDur[i]; }
    maxSelf = 1;
    for (let i = 0; i < N; i++) { if (sSelf[i] < 0) sSelf[i] = 0; if (sSelf[i] > maxSelf) maxSelf = sSelf[i]; }
}
computeSelf();
const sSelfRaw = Float64Array.from(sSelf);      // measured self time (for breakdowns)
const SECTIONS = D.sections || [];              // [task, kind, start, end, sslice, eslice, maxDepth, sline, eline]
const SEC_NAMES = ['irqs off', 'preempt off', 'BH off'];
const ATOMIC = new Map((D.atomic || []).map(a => [a[0], a[1]]));
function atomicText(bits) {
    return [[1, 'irqs off'], [2, 'preemption disabled'], [4, 'in interrupt'], [8, 'BH disabled']].filter(x => bits & x[0]).map(x => x[1]).join(', ');
}
function setAdjusted(on) {
    adjOn = !!(on && sDurAdj);
    sDur = adjOn ? sDurAdj : sDurRaw;
    computeSelf();
}

yield 'Building inline frames…';
// ---- inline frames ----
// D.inline: caller string index -> chain outer->inner [[function, file, line], ...];
// chain[0] is the traced function containing the call site, chain[1..] are inlined into it.
const INL = D.inline || {};
const HAS_INL = Object.keys(INL).length > 0;
function inlChain(i) { return i < N && sCaller && sCaller[i] >= 0 ? (INL[sCaller[i]] || null) : null; }
const SYN = [];               // per synthetic slice: {k, chain, kids: [real child indices], p}
let NA = N;
const sLd = new Int32Array(N);  // depth in the inline view (traced frames + inline frames above)
let sLdAll = sLd;
yield* (function* buildInline() {
    const open = new Map();   // parent key -> [{key, sid}] per inline level (index k-1)
    const synth = [];         // [task, start, end, nameStr, depthLevel, ctxFlags, firstLine, p, chain, k]
    for (let i = 0; i < N; i++) {
        if (i > 0 && (i & 8191) === 0) yield 'Building inline frames…';
        const p = sParent[i];
        const ch = inlChain(i);
        const n = ch ? ch.length - 1 : 0;
        // levels the kernel skipped (untraced frames, see buildGhosts) sit between parent and call site
        const base = p >= 0 ? sLd[p] + (sDepth[i] - sDepth[p] - 1) : sDepth[i] - 1;
        sLd[i] = base + 1 + n;
        const pk = p >= 0 ? p : 'r' + sTask[i] + ':' + sDepth[i];
        let runs = open.get(pk);
        if (!runs) { runs = []; open.set(pk, runs); }
        let key = '';
        for (let k = 1; k <= n; k++) {
            key += ch[k - 1][0] + ':' + ch[k - 1][2] + '>' + ch[k][0] + '|';
            const r = runs[k - 1];
            if (r && r.key === key) {
                const sy = synth[r.sid];
                sy[2] = Math.max(sy[2], sEnd[i]);
                SYN[r.sid].kids.push(i);
                continue;
            }
            runs.length = k - 1;
            const sid = synth.length;
            synth.push([sTask[i], sStart[i], sEnd[i], ch[k][0], base + k, sFlags[i] & (FL.CTX | FL.CTXINF | FL.USER), sLine[i], p, ch, k]);
            SYN.push({ k: k, chain: ch, kids: [i], p: p });
            runs.push({ key: key, sid: sid });
        }
        runs.length = n;
    }
    if (!synth.length) return;
    NA = N + synth.length;
    const grow = (a, T) => { const b = new T(NA); b.set(a); return b; };
    sStart = grow(sStart, Float64Array); sEnd = grow(sEnd, Float64Array);
    sDepth = grow(sDepth, Uint16Array); sTask = grow(sTask, Int32Array);
    sFlags = grow(sFlags, Uint32Array); sName = grow(sName, Int32Array);
    sParent = grow(sParent, Int32Array); sLine = grow(sLine, Int32Array);
    const ld = new Int32Array(NA); ld.set(sLd);
    const nameIdx = new Map();
    synth.forEach((y, j) => {
        const i = N + j;
        sTask[i] = y[0]; sStart[i] = y[1]; sEnd[i] = y[2];
        let si = nameIdx.get(y[3]);
        if (si === undefined) { si = S.length; S.push(y[3]); nameIdx.set(y[3], si); }
        sName[i] = si; sFlags[i] = FL.INL | y[5]; sLine[i] = y[6]; sParent[i] = y[7];
        sDepth[i] = y[7] >= 0 ? sDepth[y[7]] : 0;
        ld[i] = y[4];
    });
    sLdAll = ld;
})();
yield 'Building untraced frames…';
// ---- untraced frames (depth gaps) ----
// The kernel's depth sometimes jumps by more than one level: a frame was entered but its entry was
// not recorded (arm64: arch_stack_walk around unwind_*, el1_interrupt around irq_enter_rcu, ...).
// The calls inside belong to the enclosing slice (parent); the missing level(s) are drawn as dashed
// "untraced" frames so the stack has no hole. The innermost one is named after the calls' return
// address (their caller), outer ones are unknown ("?"). Extent: first to last call inside.
function callerFn(i) { const c = callerOf(i); return c ? c.split('+')[0] : '?'; }
function tailJumpOf(index) {
    if (D.tail_calls?.version !== 1 || !D.tail_calls.pairs) return null;
    const parent = sParent[index];
    if (index >= N || parent < 0 || !(sFlags[index] & FL.MISMATCH) || sDepth[index] !== sDepth[parent] + 1) return null;
    const key = [sName[parent], sMod ? sMod[parent] : -1, sName[index], sMod ? sMod[index] : -1].join(':');
    const evidence = D.tail_calls.pairs[key];
    return evidence?.status === 'supported' && evidence.sites?.length ? evidence : null;
}
function callerRelationText(index) {
    const evidence = tailJumpOf(index);
    if (evidence) return 'Supplied binary has a direct tail jump from ' + nameOf(sParent[index]) + ' to ' + nameOf(index) +
        '. Static evidence, not a captured branch; trace/binary version is not verified. Recorded nesting and durations are unchanged.';
    let text = 'Caller differs from recorded parent; tail calls, inlining or untraced code may explain the difference. Cause unresolved.';
    if (!D.tail_calls) text += ' Use --tail-calls with the matching --vmlinux for static evidence.';
    else {
        const parent = sParent[index];
        const key = [sName[parent], sMod ? sMod[parent] : -1, sName[index], sMod ? sMod[index] : -1].join(':');
        const reason = D.tail_calls.version !== 1 ? 'unsupported_evidence_version' : gapOf(index) > 0 ? 'recorded_depth_gap' :
            D.tail_calls.pairs?.[key]?.reason || D.tail_calls.reason || 'analysis_unavailable';
        text += ' Analysis: ' + reason.replace(/_/g, ' ') + '.';
    }
    return text;
}
function gapOf(i) { const p = i < N ? sParent[i] : -1; return p >= 0 ? sDepth[i] - sDepth[p] - 1 : 0; }
(function buildGhosts() {
    if (D.uftrace) return;
    const runs = new Map();   // parent -> open runs per missing level
    const gh = [];            // [task, start, end, name, depth, ld, ctxFlags, line, p]
    const meta = [];
    for (let i = 0; i < N; i++) {
        const p = sParent[i];
        if (p < 0) continue;
        const gap = sDepth[i] - sDepth[p] - 1;
        if (gap <= 0) { runs.delete(p); continue; }    // a direct child ends the runs
        let r = runs.get(p);
        if (!r) { r = []; runs.set(p, r); }
        const fn = callerFn(i);
        for (let L = 1; L <= gap; L++) {
            const name = L === gap ? fn : '?';
            const cur = r[L - 1];
            if (cur && cur.name === name) {
                const y = gh[cur.sid];
                y[2] = Math.max(y[2], sEnd[i]);
                meta[cur.sid].kids.push(i);
                continue;
            }
            r.length = L - 1;
            const sid = gh.length;
            gh.push([sTask[i], sStart[i], sEnd[i], name, sDepth[p] + L, sLd[p] + L, sFlags[i] & (FL.CTX | FL.CTXINF | FL.USER), sLine[i], p]);
            meta.push({ ghost: true, kids: [i], p: p, level: L, gap: gap, name: name });
            r.push({ name: name, sid: sid });
        }
        r.length = gap;
    }
    if (!gh.length) return;
    const base = NA;
    NA += gh.length;
    const grow = (a, T) => { const b = new T(NA); b.set(a); return b; };
    sStart = grow(sStart, Float64Array); sEnd = grow(sEnd, Float64Array);
    sDepth = grow(sDepth, Uint16Array); sTask = grow(sTask, Int32Array);
    sFlags = grow(sFlags, Uint32Array); sName = grow(sName, Int32Array);
    sParent = grow(sParent, Int32Array); sLine = grow(sLine, Int32Array);
    const ld = new Int32Array(NA); ld.set(sLdAll);
    const nameIdx = new Map();
    gh.forEach((y, j) => {
        const i = base + j;
        sTask[i] = y[0]; sStart[i] = y[1]; sEnd[i] = y[2];
        let si = nameIdx.get(y[3]);
        if (si === undefined) { si = S.length; S.push(y[3]); nameIdx.set(y[3], si); }
        sName[i] = si; sDepth[i] = y[4]; ld[i] = y[5]; sFlags[i] = FL.GHOST | y[6]; sLine[i] = y[7]; sParent[i] = y[8];
        SYN.push(meta[j]);
    });
    sLdAll = ld;
})();
function isGhost(i) { return i >= N && !!SYN[i - N].ghost; }
let inlineView = false;
function rowOf(i) { return inlineView ? sLdAll[i] : sDepth[i]; }
function isSyn(i) { return i >= N; }

// ---- error path data ----
// eRole: -1 none, 0 origin, 1 propagation.  eFate: 0 handled by caller, 1 reaches user space, 2 leaves window
const eErr = new Int32Array(N), eRole = new Int8Array(N).fill(-1), eFate = new Int8Array(N).fill(-1);
const eChain = new Int32Array(N).fill(-1), eVer = new Uint8Array(N);
for (const r of D.errs) {
    const i = r[0];
    eErr[i] = r[1]; eRole[i] = r[2]; eFate[i] = r[3]; eVer[i] = r[4]; eChain[i] = r[5];
}
const CHAINS = D.chains;   // [origin, top, errno, fate, length, verified, minor]
let showMinor = false;     // minor: handled at once by a function whose negative returns never propagate
function chainShown(c) { return showMinor || !CHAINS[c][6]; }
function errName(e) { return D.errnames[String(e)] || ''; }

yield 'Indexing task tracks…';
// ---- tracks ----
const TRACKS = D.tasks.map((t, i) => ({
    id: i, pid: t.pid, comm: t.comm, comms: t.comms || [], kind: t.kind, cpu: t.cpu, runs: t.runs || [],
    lo: -1, hi: -1, maxD: -1, rows: [], rowsT: [], rowsI: [], maxDT: -1, maxDI: -1, busy: 0, off: [], rs: [], io: [], errs: [], cpus: new Set(),
    collapsed: false, hidden: false, hits: 0, y: 0, h: 0
}));
const PROCESS_EVENTS = D.uftrace?.lifecycle || [];
const PROCESS_BY_TASK = new Map();
const unorderedProcessTasks = new Set();
let processedProcessEvents = 0;
for (const event of PROCESS_EVENTS) {
    const task = event[0];
    if (!PROCESS_BY_TASK.has(task)) PROCESS_BY_TASK.set(task, []);
    const events = PROCESS_BY_TASK.get(task);
    if (events.length && events[events.length - 1][1] > event[1]) unorderedProcessTasks.add(task);
    events.push(event);
    if ((++processedProcessEvents & 16383) === 0) yield 'Indexing process milestones…';
}
for (const task of unorderedProcessTasks) PROCESS_BY_TASK.get(task).sort((left, right) => left[1] - right[1]);
function firstProcessEventAt(events, time) {
    let low = 0, high = events.length;
    while (low < high) {
        const middle = (low + high) >>> 1;
        if (events[middle][1] < time) low = middle + 1;
        else high = middle;
    }
    return low;
}
for (let i = 0; i < N; i++) {
    const t = TRACKS[sTask[i]];
    if (t.lo < 0) t.lo = i;
    t.hi = i + 1;
    if (sDepth[i] > t.maxD) t.maxD = sDepth[i];
    if (sParent[i] < 0) t.busy += sEnd[i] - sStart[i];
    if (sCpu && sCpu[i] >= 0) t.cpus.add(sCpu[i]);
}
function buildRows(ids, depthOf) {
    let mx = -1;
    for (const i of ids) if (depthOf(i) > mx) mx = depthOf(i);
    const cnt = new Int32Array(mx + 1);
    for (const i of ids) cnt[depthOf(i)]++;
    const rows = [];
    for (let d = 0; d <= mx; d++) rows.push(new Int32Array(cnt[d]));
    cnt.fill(0);
    for (const i of ids) { const d = depthOf(i); rows[d][cnt[d]++] = i; }
    for (const r of rows) r.sort((a, b) => sStart[a] - sStart[b] || a - b);
    return [rows, mx];
}
const synByTask = new Map();
for (let i = N; i < NA; i++) { if (!synByTask.has(sTask[i])) synByTask.set(sTask[i], []); synByTask.get(sTask[i]).push(i); }
for (const t of TRACKS) {
    if (t.lo < 0) continue;
    const real = [];
    for (let i = t.lo; i < t.hi; i++) real.push(i);
    const syn = synByTask.get(t.id) || [];
    [t.rowsT, t.maxDT] = buildRows(real.concat(syn.filter(isGhost)), i => sDepth[i]);
    if (HAS_INL) [t.rowsI, t.maxDI] = buildRows(real.concat(syn), i => sLdAll[i]);
    else { t.rowsI = t.rowsT; t.maxDI = t.maxDT; }
}
for (const r of D.offcpu) {   // [task, start, end, depth, reason, state, cpuOut, cpuIn, preempted, waker]
    const t = TRACKS[r[0]];
    t.off.push(r);
    // inline view: below the frame that was open when the task left the CPU
    r.ldI = r[3];
    if (HAS_INL && r[3] > 0 && t.rowsT[r[3] - 1]) {
        const row = t.rowsT[r[3] - 1];
        const j = lbEnd(row, r[1]);
        if (j < row.length && sStart[row[j]] <= r[1]) r.ldI = sLdAll[row[j]] + 1;
    }
    if (r[3] > t.maxDT) t.maxDT = r[3];
    if (r.ldI > t.maxDI) t.maxDI = r.ldI;
}
function offRow(r) { return inlineView ? r.ldI : r[3]; }
function applyView() {
    for (const t of TRACKS) { t.rows = inlineView ? t.rowsI : t.rowsT; t.maxD = inlineView ? t.maxDI : t.maxDT; }
}
applyView();
for (const r of D.resched) TRACKS[r[0]].rs.push(r);   // [task, start, end, maxPreempt, irqoff, closedBy]
for (const r of D.irqoff) TRACKS[r[0]].io.push(r);    // [task, start, end]
for (let c = 0; c < CHAINS.length; c++) if (!CHAINS[c][6]) TRACKS[sTask[CHAINS[c][0]]].errs.push(c);

let tMax = 1;
function observeEnd(value) { if (Number.isFinite(value) && value > tMax) tMax = value; }
for (let index = 0; index < N; index++) { observeEnd(sStart[index]); observeEnd(sEnd[index]); }
for (const task of TRACKS) for (const run of task.runs) { observeEnd(run[0]); observeEnd(run[1]); }
for (const records of [D.offcpu, D.resched, D.irqoff]) for (const record of records) {
    observeEnd(record[1]); observeEnd(record[2]);
}
for (const section of D.sections || []) { observeEnd(section[2]); observeEnd(section[3]); }
for (const wake of D.wakeups || []) observeEnd(wake[1]);
for (const event of PROCESS_EVENTS) observeEnd(event[1]);
const OPEN_OFF = new Set(D.offcpu.filter(record => record[2] === null));
for (const r of D.offcpu) if (r[2] === null) r[2] = tMax;
const SCHED_BLOCK_SIZE = 128;
function* indexSchedulingIntervals(records) {
    if (records.length < SCHED_BLOCK_SIZE) return null;
    const bounds = new Float64Array(2 * Math.ceil(records.length / SCHED_BLOCK_SIZE));
    let ordered = true, previousStart = -Infinity, previousEnd = -Infinity;
    for (let start = 0, block = 0; start < records.length; start += SCHED_BLOCK_SIZE, block += 2) {
        if (start && start % 16384 === 0) yield 'Indexing scheduling intervals…';
        let earliest = Infinity, latest = -Infinity;
        const stop = Math.min(records.length, start + SCHED_BLOCK_SIZE);
        for (let ordinal = start; ordinal < stop; ordinal++) {
            const record = records[ordinal], begin = record[1], end = record[2];
            earliest = Math.min(earliest, begin);
            latest = Math.max(latest, end);
            if (begin < previousStart || end < previousEnd) ordered = false;
            previousStart = begin;
            previousEnd = end;
        }
        bounds[block] = earliest;
        bounds[block + 1] = latest;
    }
    return ordered ? true : bounds;
}
function visitSchedulingIntervals(records, index, left, right, visit) {
    if (index === true) {
        let low = 0, high = records.length;
        while (low < high) {
            const middle = (low + high) >>> 1;
            if (records[middle][2] < left) low = middle + 1;
            else high = middle;
        }
        for (let ordinal = low; ordinal < records.length && records[ordinal][1] <= right; ordinal++) {
            const record = records[ordinal];
            if (visit(record)) return record;
        }
    } else if (index) {
        for (let start = 0, block = 0; block < index.length; start += SCHED_BLOCK_SIZE, block += 2) {
            if (index[block] > right || index[block + 1] < left) continue;
            const stop = Math.min(records.length, start + SCHED_BLOCK_SIZE);
            for (let ordinal = start; ordinal < stop; ordinal++) {
                const record = records[ordinal];
                if (record[1] <= right && record[2] >= left && visit(record)) return record;
            }
        }
    } else {
        for (const record of records) {
            if (record[1] <= right && record[2] >= left && visit(record)) return record;
        }
    }
    return null;
}
for (const track of TRACKS) {
    track.offIndex = yield* indexSchedulingIntervals(track.off);
    track.rsIndex = yield* indexSchedulingIntervals(track.rs);
    track.ioIndex = yield* indexSchedulingIntervals(track.io);
}
// wakeups: [waker_task, ts, target_task, ctx, waker_slice, offcpu_index]
const WAKES = D.wakeups || [];
const OFF_IDX = new Map(D.offcpu.map((r, i) => [r, i]));
const WAKE_OF_OFF = new Map();
const WAKE_BLOCK_SIZE = 128;
const WAKE_BOUNDS = new Float64Array(2 * Math.ceil(WAKES.length / WAKE_BLOCK_SIZE));
function wakeOff(wake) { return wake[5] >= 0 ? D.offcpu[wake[5]] : null; }
for (let start = 0, block = 0; start < WAKES.length; start += WAKE_BLOCK_SIZE, block += 2) {
    if (start && start % 16384 === 0) yield 'Indexing wakeup spans…';
    let earliest = Infinity, latest = -Infinity;
    const stop = Math.min(start + WAKE_BLOCK_SIZE, WAKES.length);
    for (let index = start; index < stop; index++) {
        const wake = WAKES[index], off = wakeOff(wake);
        if (wake[5] >= 0) WAKE_OF_OFF.set(wake[5], wake);
        earliest = Math.min(earliest, wake[1]);
        latest = Math.max(latest, off ? off[2] : wake[1]);
    }
    WAKE_BOUNDS[block] = earliest;
    WAKE_BOUNDS[block + 1] = latest;
}

const EVENT_TASKS = new Set();
if (!N) {
    for (const wake of WAKES) { EVENT_TASKS.add(wake[0]); EVENT_TASKS.add(wake[2]); }
    for (const section of SECTIONS) EVENT_TASKS.add(section[0]);
}
const ORDER = TRACKS.filter(t => t.lo >= 0 || t.off.length || PROCESS_BY_TASK.has(t.id) ||
    (!N && (t.rs.length || t.io.length || EVENT_TASKS.has(t.id) ||
        ((D.caps.sched_events || D.caps.switch_markers) && t.runs.length)))).sort((a, b) => {
    if (a.kind !== b.kind) return a.kind - b.kind;
    if (a.kind === 1) return (a.cpu || 0) - (b.cpu || 0);
    return b.busy - a.busy;
});
yield 'Preparing scheduling context…';
// ---- CPU tracks: which task ran on each CPU, plus irq/softirq strips ----
const CPU_SEGS = new Map();   // cpu -> {s: Float64Array, e: Float64Array, t: Int32Array}
const CPU_IRQ = new Map();    // cpu -> [[s, e, ctx], ...]
(function buildCpu() {
    if (!TIMED) return;
    const tmp = new Map();
    for (const t of TRACKS) {
        if (t.kind !== 0) continue;
        for (const r of t.runs) {
            if (r[2] < 0 || r[1] <= r[0]) continue;
            if (!tmp.has(r[2])) tmp.set(r[2], []);
            tmp.get(r[2]).push([r[0], r[1], t.id]);
        }
    }
    for (const [cpu, a] of tmp) {
        a.sort((x, y) => x[0] - y[0]);
        for (let k = 1; k < a.length; k++) if (a[k][0] < a[k - 1][1]) a[k - 1][1] = a[k][0];
        CPU_SEGS.set(cpu, { s: Float64Array.from(a, x => x[0]), e: Float64Array.from(a, x => x[1]), t: Int32Array.from(a, x => x[2]) });
    }
    if (sCpu) for (let i = 0; i < N; i++) {
        const c = sFlags[i] & FL.CTX;
        if (!c) continue;
        const p = sParent[i];
        if (p >= 0 && (sFlags[p] & FL.CTX) === c) continue;
        const cpu = sCpu[i];
        if (cpu < 0) continue;
        if (!CPU_IRQ.has(cpu)) CPU_IRQ.set(cpu, []);
        CPU_IRQ.get(cpu).push([sStart[i], sEnd[i], c]);
        if (!CPU_SEGS.has(cpu)) CPU_SEGS.set(cpu, { s: new Float64Array(0), e: new Float64Array(0), t: new Int32Array(0) });
    }
    for (const a of CPU_IRQ.values()) a.sort((x, y) => x[0] - y[0]);
})();
const CPU_LIST = Array.from(CPU_SEGS.keys()).sort((a, b) => a - b);
if (CPU_LIST.length && ORDER.some(t => t.kind === 0)) {
    const ct = { id: -1, pid: -1, comm: 'CPUs', comms: [], kind: 9, cpu: null, runs: [], lo: -1, hi: -1,
        maxD: CPU_LIST.length - 1, rows: [], busy: 0, off: [], rs: [], io: [], errs: [], cpus: new Set(),
        collapsed: false, hidden: false, hits: 1, y: 0, h: 0 };
    ORDER.unshift(ct);
}

// Idle tracks start collapsed when there is other work to look at
if (ORDER.some(t => t.kind === 0)) for (const t of ORDER) if (t.kind === 1) t.collapsed = true;

function trackLabel(t) {
    if (t.kind === 9) return 'CPUs';
    if (t.kind === 1) return '<idle>' + (t.cpu !== null && t.cpu !== undefined ? '  CPU ' + t.cpu : '');
    if (t.kind === 2) return 'CPU ' + t.cpu + '  (no funcgraph-proc)';
    if (t.kind === 3) return 'all';
    return t.comm + '  ' + t.pid;
}
function trackSub(t) {
    if (t.kind === 9) return CPU_LIST.length + ' CPU(s) · task on CPU, irq/softirq strip · click a segment to open its task';
    const parts = [];
    if (t.cpus.size) parts.push('CPU ' + Array.from(t.cpus).sort((a, b) => a - b).join(','));
    if (t.lo >= 0) parts.push((t.hi - t.lo) + ' calls');
    else parts.push('no traced calls' + (t.runs.length ? ' · ' + t.runs.length + ' recorded runs' : ''));
    if (t.errs.length) parts.push(t.errs.length + ' err');
    if (textHits) parts.push(t.hits + ' matched');
    if (t.comms.length > 1) parts.push('was: ' + t.comms.slice(0, -1).join(', '));
    return parts.join('  ·  ');
}

// ---- layout constants ----
const HDR = 20, ROW = 17, GAP = 6, MINI = 9, HEAD_H = 62, OV_H = 30;
let totalH = 0;

// ---- formatting ----
function fmtDur(ns) {
    if (MODE === 'count') return (ns / 1000).toFixed(ns % 1000 ? 1 : 0) + ' u';
    const a = Math.abs(ns);
    if (a < 1000) return ns.toFixed(0) + ' ns';
    if (a < 1e6) return (ns / 1e3).toFixed(a < 1e4 ? 3 : a < 1e5 ? 2 : 1) + ' us';
    if (a < 1e9) return (ns / 1e6).toFixed(a < 1e7 ? 3 : a < 1e8 ? 2 : 1) + ' ms';
    return (ns / 1e9).toFixed(3) + ' s';
}
function fmtAbs(ns) {
    if (MODE !== 'abs') return '';
    if (D.uftrace?.origin_ns) {
        const time = BigInt(D.uftrace.origin_ns) + BigInt(Math.round(ns));
        return (time / 1000000000n) + '.' + String(time % 1000000000n).padStart(9, '0');
    }
    const t = D.t0 + ns;
    return Math.floor(t / 1e9) + '.' + String(Math.floor((t % 1e9) / 1e3)).padStart(6, '0');
}
function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function nameOf(i) { return S[sName[i]]; }
function spaceOf(index) { return sFlags[index] & FL.USER ? 'user' : 'kernel'; }
function userPosition(index) { return D.sources?.user_positions?.[D.sources?.user_lines?.[sLine[index]]]; }
function userCallSite(index) { return D.sources?.user_positions?.[D.sources?.user_callsites?.[sLine[index]]]; }
function kernelTypesOf(index) { return sFlags[index] & FL.USER ? null : D.uftrace?.kernel_types?.[sName[index]]; }
function scalarValue(value, format, returning = false) {
    if (!Array.isArray(format)) return value;
    const [kind, bits] = format;
    if (kind === 'void') return null;
    if (!Number.isInteger(bits) || bits < 1 || bits > 64) return value;
    try {
        let number = BigInt(value) & ((1n << BigInt(bits)) - 1n);
        if (kind === 'sint') {
            if (number & (1n << BigInt(bits - 1))) number -= 1n << BigInt(bits);
            return String(number);
        }
        if (kind === 'enum') return String(number);
        if (kind === 'bool' && returning) return number ? 'true' : 'false';
        return '0x' + number.toString(16);
    } catch (_) { return value; }
}
function rawRetOf(index) { return sRet && sRet[index] >= 0 ? S[sRet[index]] : null; }
function retOf(index) {
    const raw = rawRetOf(index), format = kernelTypesOf(index)?.ret;
    return raw === null || !format ? raw : scalarValue(raw, format, true);
}
function userCallAddress(index) {
    const address = D.uftrace?.user_details?.[sLine[index]]?.callsite?.retaddr;
    try { return address ? '0x' + (BigInt(address) - 1n).toString(16) : null; } catch (_) { return null; }
}
function callerOf(i) { return sCaller && sCaller[i] >= 0 ? S[sCaller[i]] : null; }
function argsOf(i) { return sArgs && sArgs[i] >= 0 ? S[sArgs[i]] : null; }
function namedArgsOf(index) {
    const raw = argsOf(index), names = D.uftrace?.kernel_arg_names?.[sName[index]];
    const signature = kernelTypesOf(index), formats = signature?.args || [];
    if (!raw || raw === '[]' || sFlags[index] & FL.USER ||
        (!names?.some(Boolean) && !formats.length && signature?.argc === undefined)) return null;
    const values = raw.split(', '), count = signature?.argc ?? values.length;
    if (!count) return 'no parameters';
    const result = values.slice(0, count).map((value, position) =>
        (names?.[position] || 'slot' + (position + 1)) + '=' + scalarValue(value, formats[position]));
    if (count > values.length) result.push('…');
    return result.join(', ');
}
function modOf(i) { return sMod && sMod[i] >= 0 ? S[sMod[i]] : null; }
function cpuOf(i) { return sCpu ? sCpu[i] : -1; }

// line number -> slice (lazy)
let lineMap = null;
function sliceForLine(n) {
    if (!lineMap) {
        lineMap = new Map();
        for (let i = 0; i < N; i++) {
            lineMap.set(sLine[i], i);
            if (sEline && sEline[i]) lineMap.set(sEline[i], i);
        }
    }
    return lineMap.has(n) ? lineMap.get(n) : -1;
}

// ---- binary search helpers on a depth row (slices in a row never overlap) ----
function lbEnd(row, t) {        // first j with end > t
    let lo = 0, hi = row.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (sEnd[row[m]] > t) hi = m; else lo = m + 1; }
    return lo;
}
function lbStart(row, t, lo) {  // first j >= lo with start >= t
    let hi = row.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (sStart[row[m]] >= t) hi = m; else lo = m + 1; }
    return lo;
}

// The time origin is the earliest event (sched events may precede the first traced call);
// Fit and the initial view start at the first call so leading idle time does not shrink the calls.
let tCall = tMax;
for (let i = 0; i < N; i++) if (sStart[i] < tCall) tCall = sStart[i];
for (const event of PROCESS_EVENTS) if (event[1] < tCall) tCall = event[1];
if (tCall >= tMax) tCall = 0;

// ---- view state ----
const view = { s: tCall, e: tMax };
// overview strip: covers the calls ([tCall, tMax]); ovX(t) / ovT(x) convert time <-> strip x
function ovK() { return W / Math.max(1, tMax - tCall); }
function ovX(t) { return (t - tCall) * ovK(); }
function ovT(x) { return tCall + x / ovK(); }
const ov = { err: true, irq: true, rs: true, off: true, wk: true, preempt: false, mk: true };
let colorMode = 'ctx';
let sel = -1, hov = -1, hovOff = null, hovCpu = null;
let range = null;            // {s, e, track|null}
let searchHits = null;       // Uint8Array per name index
let textHits = null;         // Uint8Array per slice: passes the text-view filter
let hideUnmatched = false;
let errFilter = 1;           // 1: reaches user space, 0: all
let cursorX = -1;

yield 'Drawing Timeline…';
// ---- DOM skeleton ----
tlRoot.innerHTML = `
<div class="tl-toolbar">
  <span class="tl-group tl-search-group">
    <input class="tl-search" placeholder="/ search function (regex)" spellcheck="false" aria-label="Search traced function names (regular expression)" aria-describedby="tlSearchStatus" aria-invalid="false">
    <span class="tl-search-nav">
      <button class="tl-btn" data-act="searchprev" title="Previous search match (Shift+Enter / Shift+N)" aria-label="Previous search match" disabled>◀</button>
      <button class="tl-btn" data-act="searchnext" title="Next search match (Enter / N)" aria-label="Next search match" disabled>▶</button>
    </span>
    <span class="tl-count" id="tlSearchStatus" role="status" aria-live="polite" aria-atomic="true"></span>
  </span>
  <span class="tl-group tl-quick">
    <span class="tl-group tl-zoom-group" role="group" aria-label="Timeline zoom">
      <button class="tl-btn tl-touch-zoom" data-act="zoomout" aria-label="Zoom timeline out">−</button>
      <button class="tl-btn tl-touch-zoom" data-act="zoomin" aria-label="Zoom timeline in">+</button>
      <button class="tl-btn" data-act="fit" title="Show the whole trace (0)">Fit</button>
    </span>
    <button class="tl-btn" data-act="notes" title="Show / hide the notes about this trace" aria-pressed="false">Notes</button>
    <button class="tl-btn tl-more" data-act="options" aria-expanded="false" aria-controls="tlOptions">More<span class="tl-more-filter" hidden> · Filter on</span></button>
  </span>
  ${MIXED ? '<span class="tl-group tl-space-key" aria-label="Address space legend"><span class="tl-space-user">User</span><span>Kernel</span>' +
    (D.stats.user_kernel_boundaries ? '<span class="tl-space-edge" title="Boundary spans the recorded kernel call">User → kernel</span>' : '') + '</span>' : ''}
  <div class="tl-options" id="tlOptions">
  <span class="tl-group"><span class="tl-label">Color</span>
    <span class="tl-seg" data-role="color">
      <button data-v="ctx" class="on" title="Execution context (task / softirq / hardirq / NMI)">Context</button>
      <button data-v="name" title="Hash of function name">Function</button>
      <button data-v="heat" title="Self time, log scale">Self time</button>
      <button data-v="cpu" title="CPU the call ran on">CPU</button>
      <button data-v="module" title="Kernel module">Module</button>
    </span></span>
  <span class="tl-group"><span class="tl-label">Overlay</span>
    <button class="tl-chip" data-ov="err"><i class="dot" style="background:#d32f2f"></i>Errors</button>
    <button class="tl-chip" data-ov="irq"><i class="dot" style="background:repeating-linear-gradient(135deg,#555 0 2px,transparent 2px 4px)"></i>IRQs off</button>
    <button class="tl-chip" data-ov="rs"><i class="dot" style="background:#f59e0b"></i>Resched delay</button>
    <button class="tl-chip" data-ov="off"><i class="dot" style="background:#9aa3ad"></i>Off-CPU</button>
    <button class="tl-chip" data-ov="wk"><i class="dot" style="background:#0f9d8a"></i>Wakeups</button>
    <button class="tl-chip off" data-ov="preempt"><i class="dot" style="background:#6b7280"></i>Preempt depth</button>
    <button class="tl-chip" data-ov="mk"><i class="dot" style="background:#8e44ad"></i>Markers</button>
  </span>
  <span class="tl-group tl-errnav">
    <button class="tl-btn" data-act="errprev" title="Previous error origin (Shift+E)">◀</button>
    <button class="tl-btn" data-act="errmode" title="Toggle: user-visible errors only / all errors"><span class="err-label"></span></button>
    <button class="tl-btn" data-act="errnext" title="Next error origin (E)">▶</button>
  </span>
  <span class="tl-group">
    <button class="tl-btn" data-act="expand" title="Expand / collapse all tracks">Tracks</button>
    <button class="tl-btn" data-act="an-tasks" title="Find and navigate all recorded tasks, including scheduling-only tasks">Tasks</button>
    <button class="tl-btn" data-act="position" title="Go to a coordinate or interval without changing selection (G)">Go to…</button>
    <button class="tl-btn" data-act="adj" title="Subtract the estimated tracing overhead from durations (O)">Overhead: measured</button>
    <button class="tl-btn" data-act="inl" title="Show inlined functions as frames between the traced calls (I)">Inline: hidden</button>
    <button class="tl-btn" data-act="an-errors" title="Error summary by syscall / origin / errno">Errors</button>
    <button class="tl-btn" data-act="an-sec" title="Critical sections: irqs-off / preempt-off / BH-off regions">Sections</button>
    <button class="tl-btn" data-act="an-lat" title="Scheduling latency: need-resched windows and what delayed them">Sched latency</button>
    ${PROCESS_EVENTS.length ? '<button class="tl-btn" data-act="process-events" title="Recorded kernel exec and exit events">Exec / exit</button>' : ''}
    <button class="tl-btn" data-act="max" title="Maximize: hide the page header and tabs (M)">Max</button>
    <button class="tl-btn" data-act="help" title="Timeline help (?)" aria-label="Timeline help" hidden>?</button>
  </span>
  <span class="tl-group tl-filterbar"><span class="tl-label">Filter</span>
  <input class="tl-ftext" placeholder="e.g. pid:1234" aria-label="Filter trace lines" title="Uses the same filter syntax as Text" spellcheck="false">
  <span class="tl-fcount"></span>
  <button class="tl-btn" data-act="fhide" title="${N ? 'Hide tracks without matches' : 'No recorded calls to match; event tracks stay visible'}"${N ? '' : ' disabled'}>Hide unmatched</button>
  <button class="tl-btn" data-act="fclear">Clear</button></span>
  </div>
</div>
${N ? '' : '<div class="tl-event-only" role="note">No recorded function calls. Showing recorded scheduling / lifecycle observations only.</div>'}
<div class="tl-notes"></div>
<div class="tl-stage">
  <canvas class="tl-head-canvas"></canvas>
  <div class="tl-scroll"><canvas class="tl-canvas" tabindex="0" aria-label="Interactive function timeline. Press question mark for keyboard shortcuts."></canvas><div class="tl-spacer"></div></div>
  <div class="tl-help" role="dialog" aria-label="Timeline help"></div>
</div>
<div class="tl-resizer" title="Drag to resize the panel, double-click to reset"></div>
<div class="tl-detail"></div>
<div class="tl-tip"></div>
<dialog class="tl-position" aria-labelledby="tlPositionTitle" aria-describedby="tlPositionHint">
  <form novalidate>
    <h3 id="tlPositionTitle">Go to position</h3>
    <p id="tlPositionHint"></p>
    <label data-position-basis>Coordinate basis<select id="tlPositionBasis"><option value="offset">Offset from report origin</option><option value="timestamp">Recorded timestamp</option></select></label>
    <label>Start / center<input id="tlPositionStart" type="text" maxlength="80" autocomplete="off" spellcheck="false" aria-describedby="tlPositionHint tlPositionError"></label>
    <label>End (optional)<input id="tlPositionEnd" type="text" maxlength="80" autocomplete="off" spellcheck="false" aria-describedby="tlPositionHint tlPositionError"></label>
    <p id="tlPositionError" role="status" aria-live="polite"></p>
    <div class="tl-position-actions"><button class="tl-btn" type="button" data-position-cancel>Cancel</button><button class="tl-btn" type="submit" data-position-go>Go</button></div>
  </form>
</dialog>
<div class="tl-toast"></div>`;
const elHead = tlRoot.querySelector('.tl-head-canvas');
const elScroll = tlRoot.querySelector('.tl-scroll');
const elCv = tlRoot.querySelector('.tl-canvas');
const elSpacer = tlRoot.querySelector('.tl-spacer');
const elStage = tlRoot.querySelector('.tl-stage');
const elDetail = tlRoot.querySelector('.tl-detail');
const elTip = tlRoot.querySelector('.tl-tip');
const elHelp = tlRoot.querySelector('.tl-help');
const elNotes = tlRoot.querySelector('.tl-notes');
const elToolbar = tlRoot.querySelector('.tl-toolbar');
const elOptions = tlRoot.querySelector('.tl-options');
const elMore = tlRoot.querySelector('.tl-more');
const elMoreFilter = tlRoot.querySelector('.tl-more-filter');
const elNotesButton = tlRoot.querySelector('[data-act=notes]');
const compactToolbar = window.matchMedia('(any-pointer: coarse) and (max-width: 640px), (any-pointer: coarse) and (max-height: 480px)');
const elSearch = tlRoot.querySelector('.tl-search');
const elCount = tlRoot.querySelector('.tl-count');
const elSearchPrev = tlRoot.querySelector('[data-act=searchprev]');
const elSearchNext = tlRoot.querySelector('[data-act=searchnext]');
const elToast = tlRoot.querySelector('.tl-toast');
const elFbar = tlRoot.querySelector('.tl-filterbar');
const elResizer = tlRoot.querySelector('.tl-resizer');
const elFtext = tlRoot.querySelector('.tl-ftext');
const elFcount = tlRoot.querySelector('.tl-fcount');
const elPosition = tlRoot.querySelector('.tl-position');
const elPositionStart = elPosition.querySelector('#tlPositionStart');
const elPositionEnd = elPosition.querySelector('#tlPositionEnd');
const elPositionBasis = elPosition.querySelector('#tlPositionBasis');
const elPositionError = elPosition.querySelector('#tlPositionError');

let toastTimer = 0;
function toast(msg) {
    elToast.textContent = msg;
    elToast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => elToast.classList.remove('show'), 1800);
}
