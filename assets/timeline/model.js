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
    NULLRET: 1 << 20, LEAF: 1 << 21, CTXINF: 1 << 22, USER: 1 << 25, BOUNDARY: 1 << 26,
    CTXNEST: 1 << 27, IRQUNKNOWN: 1 << 28
};
const HAS_USER = D.uftrace?.spaces.includes('user');
const HAS_KERNEL = !D.uftrace || D.uftrace.spaces.includes('kernel');
const MIXED = HAS_USER && HAS_KERNEL;
const CTX_NAMES = ['task', 'softirq', 'hardirq', 'NMI'];
const RESCHED_LABELS = [[], ['need-resched'], ['need-resched', 'preempt-resched'], ['preempt-resched'],
    ['lazy-resched'], ['lazy-resched', 'preempt-resched'], ['need-resched', 'lazy-resched'],
    ['need-resched', 'lazy-resched', 'preempt-resched']];
const RESCHED_NAMES = RESCHED_LABELS.map((labels, index) => labels.length ? labels.join(' + ') + ' (' + '.nNplLbB'[index] + ')' : '');
function contextName(flags) {
    const context = flags & FL.CTX;
    return flags & FL.CTXNEST && context >= 2 ? CTX_NAMES[context] + (context === 2 ? ' + softirq (H)' : ' + hardirq (Z)') : CTX_NAMES[context];
}

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
function computeSelf(durations) {
    const values = new Float64Array(N);
    let maximumSelf = 0, maximumDuration = 0;
    for (let index = 0; index < N; index++) {
        values[index] = durations[index];
        if (durations[index] > maximumDuration) maximumDuration = durations[index];
    }
    for (let index = 0; index < N; index++) {
        const parent = sParent[index];
        if (parent >= 0) values[parent] -= durations[index];
    }
    for (let index = 0; index < N; index++) {
        if (values[index] < 0) values[index] = 0;
        if (values[index] > maximumSelf) maximumSelf = values[index];
    }
    return {values, maxSelf: maximumSelf, maxDuration: maximumDuration,
        selfColorLog: Math.log1p(maximumSelf), durationColorLog: Math.log1p(maximumDuration)};
}
const rawSelfMetrics = computeSelf(sDurRaw);
let adjustedSelfMetrics = null;
const sSelfRaw = rawSelfMetrics.values;
let sSelf = sSelfRaw;
let {maxSelf, maxDuration, selfColorLog, durationColorLog} = rawSelfMetrics;
const SECTIONS = D.sections || [];              // [task, kind, start, end, sslice, eslice, maxDepth, sline, eline]
const SEC_NAMES = ['irqs off', 'preempt off', 'BH off'];
const ATOMIC = new Map((D.atomic || []).map(a => [a[0], a[1]]));
function atomicText(bits) {
    return [[1, 'irqs off'], [2, 'preemption disabled'], [4, 'in interrupt'], [8, 'BH disabled']].filter(x => bits & x[0]).map(x => x[1]).join(', ');
}
function setAdjusted(on) {
    const adjusted = !!(on && sDurAdj);
    if (adjusted === adjOn) return;
    const metrics = adjusted ? (adjustedSelfMetrics ||= computeSelf(sDurAdj)) : rawSelfMetrics;
    adjOn = adjusted;
    sDur = adjOn ? sDurAdj : sDurRaw;
    sSelf = metrics.values;
    ({maxSelf, maxDuration, selfColorLog, durationColorLog} = metrics);
}

yield 'Building inline frames…';
// ---- inline frames ----
// D.inline: caller string index -> chain outer->inner [[function, file, line], ...];
// chain[0] is the traced function containing the call site, chain[1..] are inlined into it.
const INL = D.inline || {};
const HAS_INL = Object.keys(INL).length > 0;
function inlChain(i) { return i < N && sCaller && sCaller[i] >= 0 ? (INL[sCaller[i]] || null) : null; }
const sReturnSiteOwner = new Int32Array(N);
function returnSiteOwner(index) { return index >= 0 && index < N ? sReturnSiteOwner[index] : index; }
function sharesReturnSite(index, parent) {
    if (parent < 0 || parent >= index || !sCaller || sCaller[index] < 0 || sCaller[index] !== sCaller[parent]) return false;
    if ((sFlags[index] | sFlags[parent]) & (FL.ORPHAN | FL.BOUNDARY) || sName[index] === sName[parent]) return false;
    const chain = inlChain(index);
    if (!(sFlags[index] & FL.MISMATCH) && (!chain || chain[0][0].split('.')[0] === nameOf(parent).split('.')[0])) return false;
    return sTask[index] === sTask[parent] &&
        sDepth[index] === sDepth[parent] + 1 && (!sMod || sMod[index] === sMod[parent]) &&
        (sFlags[index] & (FL.USER | FL.CTX | FL.CTXINF)) === (sFlags[parent] & (FL.USER | FL.CTX | FL.CTXINF)) &&
        sStart[index] >= sStart[parent] && sEnd[index] <= sEnd[parent];
}
function sharedReturnSiteText(index) {
    const owner = returnSiteOwner(index);
    return owner === index ? '' : 'Same recorded return site as ' + nameOf(owner) +
        '; its source/inline context is displayed once; no additional inline entry is inferred.';
}
const SYN = [];               // per synthetic slice: {k, chain, kids: [real child indices], p}
let NA = N;
const sLd = new Int32Array(N);  // depth in the inline view (traced frames + inline frames above)
let sLdAll = sLd;
const PREPARE_BATCH = 8192;
function* growFrameColumn(column, Type, label) {
    const grown = new Type(NA);
    for (let offset = 0; offset < column.length; offset += PREPARE_BATCH) {
        if (offset) yield label;
        grown.set(column.subarray(offset, offset + PREPARE_BATCH), offset);
    }
    return grown;
}
function* growFrameColumns(label) {
    sStart = yield* growFrameColumn(sStart, Float64Array, label);
    sEnd = yield* growFrameColumn(sEnd, Float64Array, label);
    sDepth = yield* growFrameColumn(sDepth, Uint16Array, label);
    sTask = yield* growFrameColumn(sTask, Int32Array, label);
    sFlags = yield* growFrameColumn(sFlags, Uint32Array, label);
    sName = yield* growFrameColumn(sName, Int32Array, label);
    sParent = yield* growFrameColumn(sParent, Int32Array, label);
    sLine = yield* growFrameColumn(sLine, Int32Array, label);
    sLdAll = yield* growFrameColumn(sLdAll, Int32Array, label);
}
yield* (function* buildInline() {
    const open = new Map();   // parent key -> [{key, sid}] per inline level (index k-1)
    const synth = [];         // [task, start, end, nameStr, depthLevel, ctxFlags, firstLine, p, chain, k]
    let prepared = 0;
    for (let i = 0; i < N; i++) {
        if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Building inline frames…';
        const p = sParent[i];
        sReturnSiteOwner[i] = sharesReturnSite(i, p) ? sReturnSiteOwner[p] : i;
        const ch = inlChain(i);
        const n = ch && returnSiteOwner(i) === i ? ch.length - 1 : 0;
        // levels the kernel skipped (untraced frames, see buildGhosts) sit between parent and call site
        const base = p >= 0 ? sLd[p] + (sDepth[i] - sDepth[p] - 1) : sDepth[i] - 1;
        sLd[i] = base + 1 + n;
        const pk = p >= 0 ? p : 'r' + sTask[i] + ':' + sDepth[i];
        let runs = open.get(pk);
        if (!runs) { runs = []; open.set(pk, runs); }
        let key = '';
        for (let k = 1; k <= n; k++) {
            if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Building inline frames…';
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
    yield* growFrameColumns('Building inline frames…');
    const nameIdx = new Map();
    for (let offset = 0; offset < synth.length; offset++) {
        if (offset && (offset & (PREPARE_BATCH - 1)) === 0) yield 'Building inline frames…';
        const frame = synth[offset], index = N + offset;
        sTask[index] = frame[0]; sStart[index] = frame[1]; sEnd[index] = frame[2];
        let name = nameIdx.get(frame[3]);
        if (name === undefined) { name = S.length; S.push(frame[3]); nameIdx.set(frame[3], name); }
        sName[index] = name; sFlags[index] = FL.INL | frame[5]; sLine[index] = frame[6]; sParent[index] = frame[7];
        sDepth[index] = frame[7] >= 0 ? sDepth[frame[7]] : 0;
        sLdAll[index] = frame[4];
    }
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
    const shared = sharedReturnSiteText(index);
    if (evidence) return (shared ? shared + ' ' : '') + 'Supplied binary has a direct tail jump from ' + nameOf(sParent[index]) + ' to ' + nameOf(index) +
        '. Static evidence, not a captured branch; trace/binary version is not verified. Recorded nesting and durations are unchanged.';
    let text = (shared ? shared + ' ' : '') + 'Caller differs from recorded parent; tail calls, inlining or untraced code may explain the difference. Cause unresolved.';
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
yield* (function* buildGhosts() {
    if (D.uftrace) return;
    const runs = new Map();   // parent -> open runs per missing level
    const gh = [];            // [task, start, end, name, depth, ld, ctxFlags, line, p]
    const meta = [];
    let prepared = 0;
    for (let i = 0; i < N; i++) {
        if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Building untraced frames…';
        const p = sParent[i];
        if (p < 0) continue;
        const gap = sDepth[i] - sDepth[p] - 1;
        if (gap <= 0) { runs.delete(p); continue; }    // a direct child ends the runs
        let r = runs.get(p);
        if (!r) { r = []; runs.set(p, r); }
        const fn = callerFn(i);
        for (let L = 1; L <= gap; L++) {
            if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Building untraced frames…';
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
    yield* growFrameColumns('Building untraced frames…');
    const nameIdx = new Map();
    for (let offset = 0; offset < gh.length; offset++) {
        if (offset && (offset & (PREPARE_BATCH - 1)) === 0) yield 'Building untraced frames…';
        const frame = gh[offset], index = base + offset;
        sTask[index] = frame[0]; sStart[index] = frame[1]; sEnd[index] = frame[2];
        let name = nameIdx.get(frame[3]);
        if (name === undefined) { name = S.length; S.push(frame[3]); nameIdx.set(frame[3], name); }
        sName[index] = name; sDepth[index] = frame[4]; sLdAll[index] = frame[5];
        sFlags[index] = FL.GHOST | frame[6]; sLine[index] = frame[7]; sParent[index] = frame[8];
        SYN.push(meta[offset]);
    }
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
const USER_ERRORS = new Map();
if (D.uftrace?.user_details) for (let index = 0; index < N; index++) {
    if (index && index % PREPARE_BATCH === 0) yield 'Indexing user API failures…';
    const failure = sFlags[index] & FL.USER ? D.uftrace.user_details[sLine[index]]?.failure : null;
    if (failure) USER_ERRORS.set(index, failure);
}

yield 'Indexing task tracks…';
// ---- tracks ----
const TRACKS = D.tasks.map((t, i) => ({
    id: i, pid: t.pid, comm: t.comm, comms: t.comms || [], kind: t.kind, cpu: t.cpu, runs: t.runs || [],
    lo: -1, hi: -1, orderedParents: true, maxD: -1, rows: [], rowsT: [], rowsI: [], maxDT: -1, maxDI: -1, busy: 0, off: [], rs: [], io: [], errs: [], userErrors: [], cpus: new Set(),
    collapsed: false, hidden: false, hits: 0, y: 0, h: 0, kvm: [], kvmEvents: [], vcpus: new Set()
}));
const KVM = D.kvm?.v === 1 ? D.kvm : null;
const KVM_EVENTS = KVM?.events || [], KVM_INTERVALS = KVM?.intervals || [];
const KVM_EVENT_POS = new Int32Array(KVM_EVENTS.length), KVM_INTERVAL_POS = new Int32Array(KVM_INTERVALS.length);
for (let index = 0; index < KVM_EVENTS.length; index++) {
    if (index && (index & (PREPARE_BATCH - 1)) === 0) yield 'Indexing KVM events…';
    const event = KVM_EVENTS[index], track = TRACKS[event[0]];
    track.kvmEvents.push(index);
    if (event[6] >= 0) track.vcpus.add(event[6]);
}
for (const track of TRACKS) {
    track.kvmEvents.sort((left, right) => KVM_EVENTS[left][1] - KVM_EVENTS[right][1] || left - right);
    for (let position = 0; position < track.kvmEvents.length; position++) KVM_EVENT_POS[track.kvmEvents[position]] = position;
}
for (let index = 0; index < KVM_INTERVALS.length; index++) {
    if (index && (index & (PREPARE_BATCH - 1)) === 0) yield 'Indexing KVM windows…';
    const track = TRACKS[KVM_INTERVALS[index][0]];
    KVM_INTERVAL_POS[index] = track.kvm.length;
    track.kvm.push(index);
}
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
    if (i && (i & (PREPARE_BATCH - 1)) === 0) yield 'Indexing task tracks…';
    const t = TRACKS[sTask[i]];
    if (t.lo < 0) t.lo = i;
    t.hi = i + 1;
    if (sDepth[i] > t.maxD) t.maxD = sDepth[i];
    const parent = sParent[i];
    if (parent < 0) t.busy += sEnd[i] - sStart[i];
    else if (parent >= i) t.orderedParents = false;
    if (sCpu && sCpu[i] >= 0) t.cpus.add(sCpu[i]);
}
function* sortTimelineRow(row, compare = (left, right) => sStart[left] - sStart[right] || left - right,
        label = 'Indexing task tracks…', batchSize = PREPARE_BATCH) {
    const typed = row instanceof Int32Array;
    let ordered = true;
    for (let index = 1; index < row.length; index++) {
        if (index % batchSize === 0) yield label;
        if (compare(row[index - 1], row[index]) > 0) { ordered = false; break; }
    }
    if (ordered) return;
    for (let start = 0; start < row.length; start += batchSize) {
        const chunk = typed ? row.subarray(start, start + batchSize) : row.slice(start, start + batchSize);
        chunk.sort(compare);
        if (!typed) for (let offset = 0; offset < chunk.length; offset++) row[start + offset] = chunk[offset];
        yield label;
    }
    if (row.length <= batchSize) return;
    const buffer = typed ? new Int32Array(row.length) : new Array(row.length);
    let source = row, target = buffer;
    for (let width = batchSize; width < row.length; width *= 2) {
        for (let start = 0; start < row.length; start += width * 2) {
            const middle = Math.min(start + width, row.length), end = Math.min(start + width * 2, row.length);
            let left = start, right = middle;
            for (let index = start; index < end; index++) {
                if (index && index % batchSize === 0) yield label;
                target[index] = left < middle && (right >= end || compare(source[left], source[right]) <= 0)
                    ? source[left++] : source[right++];
            }
        }
        [source, target] = [target, source];
    }
    if (source !== row) for (let start = 0; start < row.length; start += batchSize) {
        if (typed) row.set(source.subarray(start, start + batchSize), start);
        else for (let index = start; index < Math.min(row.length, start + batchSize); index++) row[index] = source[index];
        yield label;
    }
}
function* buildRows(first, end, synthetic, depthOf) {
    const recorded = end - first, total = recorded + synthetic.length;
    let mx = -1;
    let prepared = 0;
    for (let ordinal = 0; ordinal < total; ordinal++) {
        const slice = ordinal < recorded ? first + ordinal : synthetic[ordinal - recorded];
        if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Indexing task tracks…';
        if (depthOf(slice) > mx) mx = depthOf(slice);
    }
    const cnt = new Int32Array(mx + 1);
    for (let ordinal = 0; ordinal < total; ordinal++) {
        const slice = ordinal < recorded ? first + ordinal : synthetic[ordinal - recorded];
        if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Indexing task tracks…';
        cnt[depthOf(slice)]++;
    }
    const rows = [];
    for (let d = 0; d <= mx; d++) {
        if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Indexing task tracks…';
        rows.push(new Int32Array(cnt[d]));
    }
    cnt.fill(0);
    for (let ordinal = 0; ordinal < total; ordinal++) {
        const slice = ordinal < recorded ? first + ordinal : synthetic[ordinal - recorded];
        if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Indexing task tracks…';
        const depth = depthOf(slice); rows[depth][cnt[depth]++] = slice;
    }
    for (const row of rows) yield* sortTimelineRow(row);
    return [rows, mx];
}
const synByTask = new Map();
const RANGE_BLOCK_SIZE = 128;
function* indexRangeBounds(first, end) {
    if (end - first < RANGE_BLOCK_SIZE) return null;
    const bounds = new Float64Array(2 * Math.ceil((end - first) / RANGE_BLOCK_SIZE));
    for (let offset = 0, start = first; start < end; offset += 2, start += RANGE_BLOCK_SIZE) {
        if (offset && (start - first) % PREPARE_BATCH === 0) yield 'Indexing call ranges…';
        const stop = Math.min(start + RANGE_BLOCK_SIZE, end);
        let earliest = Infinity, latest = -Infinity;
        for (let index = start; index < stop; index++) {
            earliest = Math.min(earliest, sStart[index]);
            latest = Math.max(latest, sEnd[index]);
        }
        bounds[offset] = earliest;
        bounds[offset + 1] = latest;
    }
    return bounds;
}
for (let i = N; i < NA; i++) {
    if ((i - N) && ((i - N) & (PREPARE_BATCH - 1)) === 0) yield 'Indexing task tracks…';
    if (!synByTask.has(sTask[i])) synByTask.set(sTask[i], []);
    synByTask.get(sTask[i]).push(i);
}
for (const t of TRACKS) {
    if (t.lo < 0) continue;
    t.rangeBounds = yield* indexRangeBounds(t.lo, t.hi);
    const syn = synByTask.get(t.id) || [];
    const ghosts = [];
    for (let ordinal = 0; ordinal < syn.length; ordinal++) {
        if (ordinal && (ordinal & (PREPARE_BATCH - 1)) === 0) yield 'Indexing task tracks…';
        if (isGhost(syn[ordinal])) ghosts.push(syn[ordinal]);
    }
    [t.rowsT, t.maxDT] = yield* buildRows(t.lo, t.hi, ghosts, slice => sDepth[slice]);
    if (HAS_INL) [t.rowsI, t.maxDI] = yield* buildRows(t.lo, t.hi, syn, slice => sLdAll[slice]);
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
for (let index = 0; index < CHAINS.length; index++) {
    if (index && index % PREPARE_BATCH === 0) yield 'Indexing error origins…';
    const chain = CHAINS[index];
    if (!chain[6]) TRACKS[sTask[chain[0]]].errs.push(index);
}
for (const index of USER_ERRORS.keys()) TRACKS[sTask[index]].userErrors.push(index);

let tMin = Infinity, tMax = -Infinity, callStart = Infinity, callEnd = -Infinity;
function observeTime(value) {
    if (!Number.isFinite(value)) return;
    tMin = Math.min(tMin, value);
    tMax = Math.max(tMax, value);
}
for (let index = 0; index < N; index++) {
    observeTime(sStart[index]); observeTime(sEnd[index]);
    callStart = Math.min(callStart, sStart[index]);
    callEnd = Math.max(callEnd, sEnd[index]);
}
for (const task of TRACKS) for (const run of task.runs) { observeTime(run[0]); observeTime(run[1]); }
for (const event of KVM_EVENTS) observeTime(event[1]);
for (const interval of KVM_INTERVALS) { observeTime(interval[1]); observeTime(interval[2]); }
for (const records of [D.offcpu, D.resched, D.irqoff]) for (const record of records) {
    observeTime(record[1]); observeTime(record[2]);
}
for (const section of D.sections || []) { observeTime(section[2]); observeTime(section[3]); }
for (const wake of D.wakeups || []) observeTime(wake[1]);
let initialStart = callStart, initialEnd = callEnd;
for (const event of PROCESS_EVENTS) {
    observeTime(event[1]);
    initialStart = Math.min(initialStart, event[1]);
    initialEnd = Math.max(initialEnd, event[1]);
}
if (!Number.isFinite(tMin)) tMin = 0;
if (!Number.isFinite(tMax) || tMax <= tMin) tMax = tMin + 1;
if (!Number.isFinite(initialStart)) { initialStart = tMin; initialEnd = tMax; }
initialEnd = Math.max(initialStart + 1, initialEnd);
const OPEN_OFF = new Set(D.offcpu.filter(record => record[2] === null));
for (const r of D.offcpu) if (r[2] === null) r[2] = tMax;
const SCHED_BLOCK_SIZE = 128;
const ERROR_BLOCK_SIZE = 128;
function* indexErrorOrigins(chains) {
    if (chains.length < ERROR_BLOCK_SIZE) return null;
    const bounds = new Float64Array(2 * Math.ceil(chains.length / ERROR_BLOCK_SIZE));
    let ordered = true, previous = -Infinity;
    for (let start = 0, offset = 0; start < chains.length; start += ERROR_BLOCK_SIZE, offset += 2) {
        if (start && start % PREPARE_BATCH === 0) yield 'Indexing error origins…';
        const stop = Math.min(chains.length, start + ERROR_BLOCK_SIZE);
        let earliest = Infinity, latest = -Infinity;
        for (let position = start; position < stop; position++) {
            const time = sStart[CHAINS[chains[position]][0]];
            if (time < previous) ordered = false;
            previous = time;
            earliest = Math.min(earliest, time);
            latest = Math.max(latest, time);
        }
        bounds[offset] = earliest;
        bounds[offset + 1] = latest;
    }
    return ordered ? true : bounds;
}
function* indexSchedulingIntervals(records, timeColumn = 1, offTrack = null) {
    if (records.length < SCHED_BLOCK_SIZE) return null;
    const bounds = new Float64Array(2 * Math.ceil(records.length / SCHED_BLOCK_SIZE));
    const depths = offTrack ? new Int32Array(bounds.length) : null;
    const inlineDepths = offTrack && HAS_INL ? new Int32Array(bounds.length) : depths;
    const waitTrack = TIMED && D.wakeups?.length ? offTrack : null;
    let minDepth = Infinity, maxDepth = -Infinity, minInlineDepth = Infinity, maxInlineDepth = -Infinity;
    let ordered = true, previousStart = -Infinity, previousEnd = -Infinity;
    let firstWait = -1, lastWait = -1, lastWaitEnd = 0;
    let ascending = true, descending = true, contiguous = true;
    for (let start = 0, block = 0; start < records.length; start += SCHED_BLOCK_SIZE, block += 2) {
        if (start && start % 16384 === 0) yield 'Indexing scheduling intervals…';
        let earliest = Infinity, latest = -Infinity;
        let shallowest = Infinity, deepest = -Infinity, shallowestInline = Infinity, deepestInline = -Infinity;
        const stop = Math.min(records.length, start + SCHED_BLOCK_SIZE);
        for (let ordinal = start; ordinal < stop; ordinal++) {
            const record = records[ordinal], begin = record[timeColumn], end = record[timeColumn + 1];
            earliest = Math.min(earliest, begin);
            latest = Math.max(latest, end);
            if (begin < previousStart || end < previousEnd) ordered = false;
            previousStart = begin;
            previousEnd = end;
            if (offTrack) {
                shallowest = Math.min(shallowest, record[3]);
                deepest = Math.max(deepest, record[3]);
                if (HAS_INL) {
                    shallowestInline = Math.min(shallowestInline, record.ldI);
                    deepestInline = Math.max(deepestInline, record.ldI);
                }
            }
            if (waitTrack && (ascending || descending) && contiguous && (!OPEN_OFF.size || !OPEN_OFF.has(record))) {
                if (firstWait < 0) firstWait = ordinal;
                else {
                    if (ordinal !== lastWait + 1) contiguous = false;
                    if (end < lastWaitEnd) ascending = false;
                    if (end > lastWaitEnd) descending = false;
                }
                lastWait = ordinal;
                lastWaitEnd = end;
            }
        }
        bounds[block] = earliest;
        bounds[block + 1] = latest;
        if (offTrack) {
            depths[block] = shallowest;
            depths[block + 1] = deepest;
            minDepth = Math.min(minDepth, shallowest);
            maxDepth = Math.max(maxDepth, deepest);
            if (HAS_INL) {
                inlineDepths[block] = shallowestInline;
                inlineDepths[block + 1] = deepestInline;
                minInlineDepth = Math.min(minInlineDepth, shallowestInline);
                maxInlineDepth = Math.max(maxInlineDepth, deepestInline);
            }
        }
    }
    if (offTrack) {
        offTrack.offDepths = { bounds: depths, min: minDepth, max: maxDepth };
        offTrack.offInlineDepths = HAS_INL ? { bounds: inlineDepths, min: minInlineDepth, max: maxInlineDepth } : offTrack.offDepths;
    }
    if (waitTrack && contiguous && (ascending || descending)) {
        waitTrack.waitOrder = { records, start: Math.max(0, firstWait), end: lastWait + 1, ascending };
    }
    return ordered ? true : bounds;
}
function schedulingLowerEnd(records, left, timeColumn) {
    let low = 0, high = records.length;
    while (low < high) {
        const middle = (low + high) >>> 1;
        if (records[middle][timeColumn + 1] < left) low = middle + 1;
        else high = middle;
    }
    return low;
}
function* offIntervalBlocks(track, startTime, endTime) {
    const records = track.off, index = track.offIndex;
    if (!records.length) return;
    if (index === true) {
        const first = schedulingLowerEnd(records, startTime, 1);
        let low = first, stop = records.length;
        while (low < stop) {
            const middle = (low + stop) >>> 1;
            if (records[middle][1] < endTime) low = middle + 1;
            else stop = middle;
        }
        for (let start = first; start < stop; start += PREPARE_BATCH)
            yield [start, Math.min(start + PREPARE_BATCH, stop)];
    } else if (index) {
        for (let block = 0, start = 0; block < index.length; block += 2, start += SCHED_BLOCK_SIZE) {
            if (index[block] < endTime && index[block + 1] > startTime)
                yield [start, Math.min(start + SCHED_BLOCK_SIZE, records.length)];
        }
    } else yield [0, records.length];
}
function visitSchedulingIntervals(records, index, left, right, visit, timeColumn = 1) {
    if (index === true) {
        const low = schedulingLowerEnd(records, left, timeColumn);
        for (let ordinal = low; ordinal < records.length; ordinal++) {
            const record = records[ordinal];
            if (record[timeColumn] > right) break;
            if (visit(record)) return record;
        }
    } else if (index) {
        for (let start = 0, block = 0; block < index.length; start += SCHED_BLOCK_SIZE, block += 2) {
            if (index[block] > right || index[block + 1] < left) continue;
            const stop = Math.min(records.length, start + SCHED_BLOCK_SIZE);
            for (let ordinal = start; ordinal < stop; ordinal++) {
                const record = records[ordinal];
                if (record[timeColumn] <= right && record[timeColumn + 1] >= left && visit(record)) return record;
            }
        }
    } else {
        for (const record of records) {
            if (record[timeColumn] <= right && record[timeColumn + 1] >= left && visit(record)) return record;
        }
    }
    return null;
}
function visitOffIntervals(track, left, right, firstRow, lastRow, visit) {
    const records = track.off, timeIndex = track.offIndex;
    const depthIndex = inlineView ? track.offInlineDepths : track.offDepths;
    if (depthIndex) {
        if (firstRow > depthIndex.max || lastRow < depthIndex.min) return null;
        if (firstRow <= depthIndex.min && lastRow >= depthIndex.max)
            return visitSchedulingIntervals(records, timeIndex, left, right, record => visit(record, offRow(record)));
    }
    const visitRow = record => {
        const depth = offRow(record);
        return depth >= firstRow && depth <= lastRow && visit(record, depth);
    };
    if (!depthIndex) return visitSchedulingIntervals(records, timeIndex, left, right, visitRow);
    const { bounds } = depthIndex;
    const ordered = timeIndex === true;
    const first = ordered ? schedulingLowerEnd(records, left, 1) : 0;
    for (let block = Math.floor(first / SCHED_BLOCK_SIZE) * 2; block < bounds.length; block += 2) {
        const start = Math.max(first, block / 2 * SCHED_BLOCK_SIZE);
        if (start >= records.length || (ordered && records[start][1] > right)) break;
        if (bounds[block] > lastRow || bounds[block + 1] < firstRow ||
                (!ordered && (timeIndex[block] > right || timeIndex[block + 1] < left))) continue;
        const stop = Math.min(records.length, (block / 2 + 1) * SCHED_BLOCK_SIZE);
        for (let ordinal = start; ordinal < stop; ordinal++) {
            const record = records[ordinal];
            if (record[1] > right) { if (ordered) return null; continue; }
            if ((ordered || record[2] >= left) && visitRow(record)) return record;
        }
    }
    return null;
}
for (const track of TRACKS) {
    track.errorIndex = yield* indexErrorOrigins(track.errs);
    track.offIndex = yield* indexSchedulingIntervals(track.off, 1, track);
    track.rsIndex = yield* indexSchedulingIntervals(track.rs);
    track.ioIndex = yield* indexSchedulingIntervals(track.io);
}
// wakeups: [waker_task, ts, target_task, ctx, waker_slice, offcpu_index]
const WAKES = D.wakeups || [];
const OFF_IDX = new Map(D.offcpu.map((r, i) => [r, i]));
const WAKE_OF_OFF = new Map();
const WAKE_BLOCK_SIZE = 128;
const WAKE_BOUNDS = new Float64Array(2 * Math.ceil(WAKES.length / WAKE_BLOCK_SIZE));
const WAKE_TRACK_BOUNDS = new Int32Array(WAKE_BOUNDS.length);
function wakeOff(wake) { return wake[5] >= 0 ? D.offcpu[wake[5]] : null; }
const EVENT_TASKS = new Set();
if (!N) {
    for (const wake of WAKES) { EVENT_TASKS.add(wake[0]); EVENT_TASKS.add(wake[2]); }
    for (const section of SECTIONS) EVENT_TASKS.add(section[0]);
}
const ORDER = TRACKS.filter(t => t.lo >= 0 || t.off.length || PROCESS_BY_TASK.has(t.id) ||
    t.kvmEvents.length ||
    (!N && (t.rs.length || t.io.length || EVENT_TASKS.has(t.id) ||
        ((D.caps.sched_events || D.caps.switch_markers) && t.runs.length)))).sort((a, b) => {
    if (a.kind !== b.kind) return a.kind - b.kind;
    if (a.kind === 1) return (a.cpu || 0) - (b.cpu || 0);
    if (a.kvmEvents.length || b.kvmEvents.length) return b.kvmEvents.length - a.kvmEvents.length || b.busy - a.busy;
    return b.busy - a.busy;
});
if (WAKES.length) {
    const trackOrdinals = new Int32Array(TRACKS.length).fill(-1);
    for (let index = 0; index < ORDER.length; index++) trackOrdinals[ORDER[index].id] = index;
    for (let start = 0, block = 0; start < WAKES.length; start += WAKE_BLOCK_SIZE, block += 2) {
        if (start && start % 16384 === 0) yield 'Indexing wakeup spans…';
        let earliest = Infinity, latest = -Infinity, firstTrack = ORDER.length, lastTrack = -1;
        const stop = Math.min(start + WAKE_BLOCK_SIZE, WAKES.length);
        for (let index = start; index < stop; index++) {
            const wake = WAKES[index], off = wakeOff(wake);
            if (wake[5] >= 0) WAKE_OF_OFF.set(wake[5], wake);
            earliest = Math.min(earliest, wake[1]);
            latest = Math.max(latest, off ? off[2] : wake[1]);
            if (firstTrack < 0) continue;
            const source = trackOrdinals[wake[0]], target = trackOrdinals[wake[2]];
            if (!(source >= 0 && target >= 0) || (wake[4] >= 0 && sTask[wake[4]] !== wake[0]) ||
                    (off && off[0] !== wake[2])) {
                firstTrack = -1;
                continue;
            }
            firstTrack = Math.min(firstTrack, source, target);
            lastTrack = Math.max(lastTrack, source, target);
        }
        WAKE_BOUNDS[block] = earliest;
        WAKE_BOUNDS[block + 1] = latest;
        WAKE_TRACK_BOUNDS[block] = firstTrack;
        WAKE_TRACK_BOUNDS[block + 1] = lastTrack;
    }
}
yield 'Preparing scheduling context…';
// ---- CPU tracks: which task ran on each CPU, plus irq/softirq strips ----
const CPU_SEGS = new Map();   // cpu -> {s: Float64Array, e: Float64Array, t: Int32Array}
const CPU_IRQ = new Map();    // cpu -> [[s, e, ctx], ...]
const CPU_IRQ_INDEX = new Map();
const CPU_HAS_SWITCHES = !!D.caps.sched_events;
yield* (function* buildCpu() {
    if (!TIMED) return;
    const tmp = new Map();
    let prepared = 0;
    for (const t of TRACKS) {
        if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Collecting CPU runs…';
        if (t.kind !== 0) continue;
        for (const r of t.runs) {
            if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Collecting CPU runs…';
            if (r[2] < 0 || r[1] <= r[0]) continue;
            if (!tmp.has(r[2])) tmp.set(r[2], []);
            tmp.get(r[2]).push([r[0], r[1], t.id]);
        }
    }
    for (const [cpu, a] of tmp) {
        yield* sortTimelineRow(a, (left, right) => left[0] - right[0], 'Sorting CPU runs…');
        for (let index = 1; index < a.length; index++) {
            if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Clipping CPU runs…';
            if (a[index][0] < a[index - 1][1]) a[index - 1][1] = a[index][0];
        }
        const segments = { s: new Float64Array(a.length), e: new Float64Array(a.length), t: new Int32Array(a.length) };
        for (let index = 0; index < a.length; index++) {
            if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Filling CPU columns…';
            segments.s[index] = a[index][0];
            segments.e[index] = a[index][1];
            segments.t[index] = a[index][2];
        }
        CPU_SEGS.set(cpu, segments);
    }
    if (sCpu) for (let i = 0; i < N; i++) {
        if ((++prepared & (PREPARE_BATCH - 1)) === 0) yield 'Collecting CPU interrupts…';
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
    for (const records of CPU_IRQ.values())
        yield* sortTimelineRow(records, (left, right) => left[0] - right[0], 'Sorting CPU interrupts…');
})();
for (const [cpu, records] of CPU_IRQ) {
    const index = yield* indexSchedulingIntervals(records, 0);
    if (index !== null) CPU_IRQ_INDEX.set(cpu, index);
}
function visitCpuInterrupts(cpu, left, right, visit) {
    const records = CPU_IRQ.get(cpu);
    if (records) visitSchedulingIntervals(records, CPU_IRQ_INDEX.get(cpu), left, right, visit, 0);
}
const CPU_LIST = Array.from(CPU_SEGS.keys()).sort((a, b) => a - b);
if (CPU_LIST.length && ORDER.some(t => t.kind === 0)) {
    const ct = { id: -1, pid: -1, comm: 'CPUs', comms: [], kind: 9, cpu: null, runs: [], lo: -1, hi: -1,
        maxD: CPU_LIST.length - 1, rows: [], busy: 0, off: [], rs: [], io: [], errs: [], cpus: new Set(),
        collapsed: !!KVM, hidden: false, hits: 1, y: 0, h: 0, kvm: [], kvmEvents: [], vcpus: new Set() };
    ORDER.unshift(ct);
}

// Idle tracks start collapsed when there is other work to look at
if (ORDER.some(t => t.kind === 0)) for (const t of ORDER) if (t.kind === 1) t.collapsed = true;

function trackLabel(t) {
    if (t.kind === 9) return 'CPUs';
    if (t.kind === 1) return '<idle>' + (t.cpu !== null && t.cpu !== undefined ? '  CPU ' + t.cpu : '');
    if (t.kind === 2) return 'CPU ' + t.cpu + '  (no funcgraph-proc)';
    if (t.kind === 3) return 'all';
    const vcpu = t.kvmEvents.length ? (t.vcpus.size ? 'vCPU ' + Array.from(t.vcpus).sort((left, right) => left - right).join(',') : 'KVM thread') + ' · ' : '';
    return vcpu + t.comm + '  ' + t.pid;
}
function trackSub(t) {
    if (t.kind === 9) return CPU_LIST.length + ' CPU(s) · ' +
        (CPU_HAS_SWITCHES ? 'recorded scheduling, irq/softirq strip' : 'observed calls · no sched_switch') +
        (t.collapsed ? ' · expand for CPU lanes' : ' · click a segment to open its task');
    const parts = [];
    if (t.kvmEvents.length) parts.push(t.kvmEvents.length + ' KVM events · Host TID ' + t.pid);
    if (t.cpus.size) parts.push('CPU ' + Array.from(t.cpus).sort((a, b) => a - b).join(','));
    if (t.lo >= 0) parts.push((t.hi - t.lo) + ' calls');
    else parts.push('no traced calls' + (t.runs.length ? ' · ' + t.runs.length + ' recorded runs' : ''));
    if (t.errs.length) parts.push(t.errs.length + ' err');
    if (t.userErrors?.length) parts.push(t.userErrors.length + ' API failures');
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
    const origin = D.uftrace?.origin_ns || D.kvm?.origin_ns;
    if (origin) {
        const time = BigInt(origin) + BigInt(Math.round(ns));
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
function btfDetailKey(index) { return (sMod?.[index] ?? -1) + ':' + sName[index]; }
function btfDetailOf(index) { return sFlags[index] & FL.USER ? null : D.btf_detail?.functions?.[btfDetailKey(index)]; }
function scalarValue(value, format, returning = false) {
    if (!Array.isArray(format)) return value;
    const [kind, bits] = format;
    if (kind === 'void') return null;
    if (!Number.isInteger(bits) || bits < 1 || bits > 64) return value;
    try {
        let number = BigInt(value) & ((1n << BigInt(bits)) - 1n);
        if (kind === 'sint' || kind === 'senum') {
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
    const site = D.uftrace?.user_details?.[sLine[index]]?.callsite;
    if (!site) return null;
    if (site.symbol) return site.name + '+0x' + site.symbol.offset.toString(16) + '/0x' + site.symbol.size.toString(16);
    if (site.name) return site.name + ' (offset unavailable)';
    try { return '0x' + (BigInt(site.retaddr) - 1n).toString(16) + ' (symbol unavailable)'; } catch (_) { return null; }
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

function createTraceLineLookup(entries, exits, count) {
    if (!count) return () => -1;
    const capacity = count * 2;
    let first = Infinity, last = -Infinity;
    for (let index = 0; index < count; index++) {
        const entry = entries[index], exit = exits?.[index];
        if (entry < first) first = entry;
        if (entry > last) last = entry;
        if (exit && exit < first) first = exit;
        if (exit && exit > last) last = exit;
        if (last - first >= capacity) break;
    }
    const span = last - first + 1;
    if (span <= capacity) {
        const byLine = new Uint32Array(span);
        for (let index = count - 1; index >= 0; index--) byLine[entries[index] - first] = index + 1;
        let preceding = 0;
        for (let offset = 0; offset < span; offset++) {
            preceding = byLine[offset] || preceding;
            byLine[offset] = preceding;
        }
        for (let index = 0; index < count; index++) {
            byLine[entries[index] - first] = index + 1;
            if (exits?.[index]) byLine[exits[index] - first] = index + 1;
        }
        return line => line < first ? -1 : (line > last ? preceding : byLine[line - first]) - 1;
    }
    const sortedEntries = sortTraceLineOrdinals(entries, count, false);
    const sortedExits = exits ? sortTraceLineOrdinals(exits, count, true) : null;
    const lower = (lines, ordinals, target) => {
        let low = 0, high = ordinals.length;
        while (low < high) {
            const middle = low + ((high - low) >>> 1);
            if (lines[ordinals[middle]] < target) low = middle + 1;
            else high = middle;
        }
        return low;
    };
    return line => {
        const end = Math.floor(line) + 1;
        const position = lower(entries, sortedEntries, end) - 1;
        const entry = sortedEntries[position];
        const exit = sortedExits?.length ? sortedExits[lower(exits, sortedExits, end) - 1] : undefined;
        const exactEntry = entry !== undefined && entries[entry] === line ? entry : -1;
        const exactExit = exit !== undefined && exits[exit] === line ? exit : -1;
        if (exactEntry >= 0 || exactExit >= 0) return Math.max(exactEntry, exactExit);
        if (position < 0) return -1;
        const previousLine = entries[entry];
        return entries[sortedEntries[position - 1]] === previousLine ?
            sortedEntries[lower(entries, sortedEntries, previousLine)] : entry;
    };
}

function sortTraceLineOrdinals(lines, count, skipZero) {
    const ordinals = new Uint32Array(count);
    let size = 0, previous = -Infinity, sorted = true;
    for (let index = 0; index < count; index++) {
        const line = lines[index];
        if (skipZero && !line) continue;
        ordinals[size++] = index;
        if (line < previous) sorted = false;
        previous = line;
    }
    const active = size === count ? ordinals : ordinals.slice(0, size);
    if (sorted) return active;
    if (size <= 2048) return active.sort((left, right) => lines[left] - lines[right] || left - right);
    const bits = 11, bins = new Uint32Array(1 << bits), mask = bins.length - 1;
    let input = active, output = new Uint32Array(size);
    for (let shift = 0; shift < 32; shift += bits) {
        bins.fill(0);
        for (let index = 0; index < size; index++)
            bins[((lines[input[index]] ^ 0x80000000) >>> shift) & mask]++;
        let remaining = 0;
        for (let bin = 0; bin < bins.length; bin++) {
            const length = bins[bin];
            bins[bin] = remaining;
            remaining += length;
        }
        for (let index = 0; index < size; index++) {
            const ordinal = input[index], bin = ((lines[ordinal] ^ 0x80000000) >>> shift) & mask;
            output[bins[bin]++] = ordinal;
        }
        [input, output] = [output, input];
    }
    return input;
}

let traceLineLookup = null;
function sliceForLine(line) {
    traceLineLookup ||= createTraceLineLookup(sLine, sEline, N);
    return traceLineLookup(line);
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

// ---- view state ----
const view = { s: initialStart, e: initialEnd };
function ovK() { return W / Math.max(1, tMax - tMin); }
function ovX(t) { return (t - tMin) * ovK(); }
function ovT(x) { return tMin + x / ovK(); }
const ov = { err: true, irq: true, rs: true, off: true, wk: true, preempt: false, mk: true };
let colorMode = 'ctx';
let sel = -1, hov = -1, hovOff = null, hovCpu = null, selKvm = null, hovKvm = null;
let savedViewsDialog = null;
let range = null;            // {s, e, track|null}
let searchHits = null;       // Uint8Array per name index
let textHits = null;         // Uint8Array per slice: passes the text-view filter
let hideUnmatched = false;
let errFilter = USER_ERRORS.size ? 0 : 1;
let cursorX = -1;

yield 'Drawing Timeline…';
// ---- DOM skeleton ----
tlRoot.innerHTML = `
<div class="tl-toolbar">
  <div class="tl-primary" role="group" aria-label="Timeline navigation and display">
  <span class="tl-group tl-search-group">
    <input class="tl-search" placeholder="/ Find function (regex)" spellcheck="false" aria-label="Search traced function names (regular expression)" aria-describedby="tlSearchStatus tlSearchHelp" aria-invalid="false" title="Highlight and navigate function-name matches. To filter calls, use Tools → Filter calls.">
    <span id="tlSearchHelp" hidden>Find highlights and navigates function names within the current filter. It does not filter calls. Use Tools → Filter calls for the shared Text and Timeline filter. Complex expressions use background search when available; Escape clears and cancels.</span>
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
      <button class="tl-btn" data-act="fit" title="Show the whole trace (0)" aria-label="Fit trace"><span class="tl-toolbar-long">Fit trace</span><span class="tl-toolbar-short" aria-hidden="true">Fit</span></button>
    </span>
    <button class="tl-btn" data-act="inl" title="Show inlined functions as frames between the traced calls (I)" aria-label="Inline frames" aria-pressed="false"><span class="tl-toolbar-long">Inline: hidden</span><span class="tl-toolbar-short" aria-hidden="true">Inline<svg class="tl-inline-mark" viewBox="0 0 12 12" focusable="false"><path d="M2 6l3 3 5-6"/></svg></span></button>
    <button class="tl-btn" data-act="notes" title="Show / hide the notes about this trace" aria-label="Notes" aria-pressed="false"><span class="tl-toolbar-long">Notes</span><span class="tl-toolbar-short" aria-hidden="true">Notes</span></button>
    <button class="tl-btn tl-more" data-act="options" title="Filters, colors, overlays and analysis tools" aria-label="Tools" aria-haspopup="dialog" aria-expanded="false" aria-controls="tlOptions">Tools<span class="tl-more-filter" hidden> · Filter on</span></button>
  </span>
  <button class="tl-btn tl-fullscreen-button" data-act="max" title="Enter full screen (M); Escape to exit" aria-label="Full screen" aria-pressed="false"><span class="tl-toolbar-long">Full screen</span><span class="tl-toolbar-short" aria-hidden="true">Full</span></button>
  <button class="tl-btn" id="timelineHelpBtn" data-act="help" title="Timeline help (?)" aria-label="Timeline help" aria-haspopup="dialog" aria-controls="timelineHelp" aria-expanded="false" hidden>?</button>
  </div>
  ${MIXED ? '<span class="tl-group tl-space-key" aria-label="Address space legend"><span class="tl-space-user">User</span><span>Kernel</span>' +
    (D.stats.user_kernel_boundaries ? '<span class="tl-space-edge" title="Boundary spans the recorded kernel call">User → kernel</span>' : '') + '</span>' : ''}
  <div class="tl-options" id="tlOptions" role="dialog" aria-label="Timeline tools" tabindex="-1">
  <div class="tl-menu-wide tl-jump-tools" role="group" aria-label="Search and display" hidden></div>
  <div class="tl-menu-wide tl-zoom-tools" hidden><span class="tl-label" aria-hidden="true">Zoom</span></div>
  <div class="tl-group tl-filterbar" role="group" aria-label="Filter calls">
    <div class="tl-filter-heading"><label class="tl-label" for="tlFilterInput">Filter calls</label><span class="tl-fdraft" role="status" aria-live="polite" hidden>Unapplied changes</span></div>
    <div class="tl-filter-entry">
      <input id="tlFilterInput" class="tl-ftext" placeholder="e.g. func:read AND pid:1234" aria-label="Filter trace lines" aria-describedby="tlFilterScope tlFilterCount tlFilterDraft" spellcheck="false">
      <button class="tl-btn" data-act="fapply" disabled>Apply</button>
    </div>
    <span id="tlFilterDraft" class="tl-filter-draft-help" hidden>Edits are not applied until Apply or Enter. Escape restores the active filter and cancels pending filtering.</span>
    <div class="tl-filter-result"><span class="tl-fcount" id="tlFilterCount"></span>
      <button class="tl-btn" data-act="fhide" aria-pressed="false" title="${N ? 'Hide tracks without matching calls; event tracks remain visible' : 'No recorded calls to match; event tracks stay visible'}"${N ? '' : ' disabled'}>Hide unmatched tracks</button>
      <button class="tl-btn" data-act="fclear">Clear</button>
      <button class="tl-btn" data-act="fundo" hidden>Undo focus</button>
    </div>
    <div class="tl-fcurrent" hidden><span class="tl-label">Applied</span><code></code></div>
    <details class="tl-filter-help"><summary>Syntax and examples</summary>
      <p id="tlFilterScope">Shared with Text and analysis. Unmatched calls are dimmed; Hide unmatched tracks hides only tracks without matches. Event tracks and call-stack context stay available. Find function only highlights and navigates within this filter.</p>
      <p>Combine conditions with <code>AND</code>, <code>OR</code>, <code>NOT</code> and parentheses. Names use regular expressions; <code>func:="exact symbol"</code> matches a complete, case-sensitive symbol. Apply with Enter; Escape cancels pending work and restores the active filter.</p>
      <label for="tlFilterExample">Start with an example</label>
      <select id="tlFilterExample" aria-label="Filter example">
        <option value="">Choose an example…</option>
        <option value="func:read|write">Function names: read or write</option>
        <option value="pid:1234">One task: PID 1234</option>
        ${HAS_KERNEL ? '<option value="graph:vfs_read">A complete call tree: vfs_read</option>' : '<option value="graph:main">A complete call tree: main</option>'}
        <option value="ret:all">Known error return codes</option>
        <option value="result:failure">Kernel errors and user API failure hints</option>
        ${TIMED ? '<option value="duration:>10">Duration greater than 10 µs</option>' : ''}
        ${HAS_USER ? '<option value="space:user">User-space calls</option>' : ''}
        ${HAS_KERNEL ? '<option value="space:kernel">Kernel-space calls</option>' : ''}
      </select>
      <p>Examples replace the draft only; edit the value and choose Apply.</p>
    </details>
  </div>
  <span class="tl-group" role="group" aria-label="Slice color settings"><label class="tl-label" for="tlColorMode">Color</label>
    <select class="tl-color-picker" id="tlColorMode" data-role="color">
      <option value="ctx">Context</option>
      <option value="name">Function</option>
      <option value="total"${N && MODE !== 'count' ? '' : ' hidden disabled'}>Total time</option>
      <option value="heat"${N && MODE !== 'count' ? '' : ' hidden disabled'}>Self time</option>
      <option value="cpu"${sCpu ? '' : ' hidden disabled'}>CPU</option>
      <option value="module"${sMod ? '' : ' hidden disabled'}>Module</option>
    </select>
    <span class="tl-heat-scale" id="tlHeatScale" hidden role="status" aria-live="polite" aria-atomic="true">
      <span class="tl-heat-ramp" aria-hidden="true"></span><span class="tl-heat-caption"></span>
      <span class="tl-heat-note"></span>
    </span>
  </span>
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
    <button class="tl-btn" data-act="errmode" title="Toggle: kernel error chains reaching user space / all error chains and user API hints"><span class="err-label"></span></button>
    <button class="tl-btn" data-act="errnext" title="Next error origin (E)">▶</button>
  </span>
  <span class="tl-group" role="group" aria-label="Timeline navigation">
    <span class="tl-label tl-menu-heading">Navigate</span>
    <button class="tl-btn" data-act="expand" title="Expand / collapse all tracks">Tracks</button>
    <button class="tl-btn" data-act="an-tasks" title="Find and navigate all recorded tasks, including scheduling-only tasks">Tasks</button>
    <button class="tl-btn" data-act="position" title="Go to a coordinate or interval without changing selection (G)">Go to…</button>
    <button class="tl-btn" data-act="saved-views" title="Save and restore a named position, selection and filter">Saved views…</button>
    <button class="tl-btn" data-act="fitcalls" aria-label="Fit recorded calls" title="${N ? 'Show all recorded calls, independent of filters' : 'No recorded calls in this report'}"${N ? '' : ' disabled'}>Fit calls</button>
  </span>
  <span class="tl-group" role="group" aria-label="Timeline display">
    <span class="tl-label tl-menu-heading">Display</span>
    <button class="tl-btn" data-act="adj" title="Subtract the estimated tracing overhead from durations (O)">Overhead: measured</button>
  </span>
  <span class="tl-group tl-menu-wide" role="group" aria-label="Timeline analysis">
    <span class="tl-label tl-menu-heading">Analyze</span>
    <button type="button" class="tl-btn" data-act="pick-range" title="Choose two points on the chart to summarize a range">Select range</button>
    <button class="tl-btn" data-act="an-errors" title="Error summary by syscall / origin / errno">Errors</button>
    <button class="tl-btn" data-act="an-sec" title="Critical sections: irqs-off / preempt-off / BH-off regions">Critical sections</button>
    <button class="tl-btn" data-act="an-lat" title="Scheduling latency: need-resched windows and what delayed them">Scheduling latency</button>
    ${PROCESS_EVENTS.length ? '<button class="tl-btn" data-act="process-events" title="Recorded kernel exec and exit events">Exec / exit</button>' : ''}
  </span>
  </div>
</div>
${N ? '' : '<div class="tl-event-only" role="note">No recorded function calls. Showing recorded ' + (KVM ? 'KVM / ' : '') + 'scheduling / lifecycle observations only.</div>'}
<div class="tl-notes"></div>
<div class="tl-range-pick" role="group" aria-label="Select range" hidden>
  <span id="tlRangePickHint" role="status"></span>
  <select aria-label="Range scope"><option value="all">All tracks</option><option value="track">Start track</option></select>
  <button type="button" class="tl-btn" data-range-pick-cancel>Cancel</button>
</div>
<div class="tl-chart-detail-split">
<div class="tl-stage">
  <canvas class="tl-head-canvas"></canvas>
  <div class="tl-scroll"><canvas class="tl-canvas" tabindex="0" aria-label="Interactive function timeline. Press question mark for keyboard shortcuts."></canvas><div class="tl-spacer"></div></div>
  <div class="tl-hover-layer" aria-hidden="true">
    <div class="tl-range-overlay" hidden><div class="tl-range-band"></div><div class="tl-range-line"></div><div class="tl-range-line"></div></div>
    <div class="tl-range-anchor" hidden></div>
    <div class="tl-hover-cursor" hidden><canvas class="tl-hover-time"></canvas></div>
  </div>
  <div class="tl-help" id="timelineHelp" role="dialog" aria-label="Timeline help"></div>
</div>
<div class="tl-resizer" role="separator" tabindex="0" aria-label="Resize details" aria-orientation="horizontal" aria-controls="tlDetail" aria-valuemin="60" title="Drag or use Up/Down to resize details; Home/End for limits; Enter or double-click to reset"></div>
<div class="tl-detail" id="tlDetail"></div>
</div>
<div class="tl-call-actions-panel" id="tlCallActions" role="menu" aria-label="Selected call actions"${D.no_text ? ' aria-describedby="tlCallActionsNote"' : ''} hidden>
  <button type="button" role="menuitem" tabindex="-1" data-call-action="distribution"><span>Duration distribution</span><kbd>H</kbd></button>
  <button type="button" role="menuitem" tabindex="-1" data-call-action="text"${D.no_text ? ' disabled' : ''}><span>Open in Text</span><kbd>Enter</kbd></button>
  ${document.getElementById('filterInput') ? '<button type="button" role="menuitem" tabindex="-1" data-call-action="focus-task">Focus this task</button><button type="button" role="menuitem" tabindex="-1" data-call-action="focus-function">Focus this function</button>' : ''}
  ${D.no_text ? '<p id="tlCallActionsNote" class="tl-context-note">Text view is not included in this report.</p>' : ''}
</div>
<div class="tl-tip"><div class="tl-tip-body"></div><div class="tl-tip-more" hidden>Select for full details</div></div>
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
const elRangeOverlay = tlRoot.querySelector('.tl-range-overlay');
const [elRangeBand, elRangeStart, elRangeEnd] = elRangeOverlay.children;
const elRangePick = tlRoot.querySelector('.tl-range-pick');
const elRangePickHint = elRangePick.firstElementChild;
const elRangeScope = elRangePick.querySelector('select');
const elRangeAnchor = tlRoot.querySelector('.tl-range-anchor');
const elHoverCursor = tlRoot.querySelector('.tl-hover-cursor');
const elHoverTime = elHoverCursor.firstElementChild;
const elSpacer = tlRoot.querySelector('.tl-spacer');
const elStage = tlRoot.querySelector('.tl-stage');
const elDetail = tlRoot.querySelector('.tl-detail');
const elCallActions = tlRoot.querySelector('.tl-call-actions-panel');
let callActionsTrigger = null, callActionsSelection = -1, callActionsLayout = null;
const elTip = tlRoot.querySelector('.tl-tip');
const elTipBody = elTip.querySelector('.tl-tip-body');
const elTipMore = elTip.querySelector('.tl-tip-more');
const elHelp = tlRoot.querySelector('.tl-help');
const elNotes = tlRoot.querySelector('.tl-notes');
const elToolbar = tlRoot.querySelector('.tl-toolbar');
const elOptions = tlRoot.querySelector('.tl-options');
const elMore = tlRoot.querySelector('.tl-more');
const elMoreFilter = tlRoot.querySelector('.tl-more-filter');
const elNotesButton = tlRoot.querySelector('[data-act=notes]');
const compactToolbar = window.matchMedia('(any-pointer: coarse) and (max-width: 640px), (any-pointer: coarse) and (max-height: 480px)');
const shortNarrowToolbar = window.matchMedia('(any-pointer: coarse) and (max-width: 640px) and (max-height: 480px) and (orientation: landscape)');
const elSearch = tlRoot.querySelector('.tl-search');
const elCount = tlRoot.querySelector('.tl-count');
const elSearchPrev = tlRoot.querySelector('[data-act=searchprev]');
const elSearchNext = tlRoot.querySelector('[data-act=searchnext]');
const elToast = tlRoot.querySelector('.tl-toast');
const elFbar = tlRoot.querySelector('.tl-filterbar');
const elResizer = tlRoot.querySelector('.tl-resizer');
const elFtext = tlRoot.querySelector('.tl-ftext');
const elFcount = tlRoot.querySelector('.tl-fcount');
const elFapply = tlRoot.querySelector('[data-act=fapply]');
const elFdraft = tlRoot.querySelector('.tl-fdraft');
const elFcurrent = tlRoot.querySelector('.tl-fcurrent');
const elFexample = tlRoot.querySelector('#tlFilterExample');
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
