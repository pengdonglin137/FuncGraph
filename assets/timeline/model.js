/* FuncGraph Timeline: data model + DOM skeleton.
 * model/render/interact/panels.js are concatenated (in that order) inside function __tlBoot() and share its scope. */
const tlRoot = document.getElementById('timelineView');
const D = JSON.parse(document.getElementById('tlData').textContent);
const N = D.n, C = D.cols, S = D.strings;
const MODE = D.mode;
const TIMED = MODE === 'abs' || MODE === 'rel';
const FL = {
    INL: 1 << 23,      // synthetic inline frame (front-end only)
    GHOST: 1 << 24,    // synthetic untraced frame filling a depth gap (front-end only)
    CTX: 3, IRQOFF: 1 << 2, BHOFF: 1 << 3, RS_SHIFT: 4, PR_SHIFT: 7, MG_SHIFT: 11,
    LAT: 1 << 15, TRUNC: 1 << 16, ORPHAN: 1 << 17, EST: 1 << 18, MISMATCH: 1 << 19,
    NULLRET: 1 << 20, LEAF: 1 << 21, CTXINF: 1 << 22
};
const CTX_NAMES = ['task', 'softirq', 'hardirq', 'NMI'];
const RESCHED_NAMES = ['', 'need-resched (n)', 'need-resched + preempt (N)', 'preempt-resched (p)',
    'lazy (l)', 'lazy + preempt (L)', 'need + lazy (b)', 'need + lazy + preempt (B)'];

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
(function buildInline() {
    const open = new Map();   // parent key -> [{key, sid}] per inline level (index k-1)
    const synth = [];         // [task, start, end, nameStr, depthLevel, ctxFlags, firstLine, p, chain, k]
    for (let i = 0; i < N; i++) {
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
            synth.push([sTask[i], sStart[i], sEnd[i], ch[k][0], base + k, sFlags[i] & (FL.CTX | FL.CTXINF), sLine[i], p, ch, k]);
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
// ---- untraced frames (depth gaps) ----
// The kernel's depth sometimes jumps by more than one level: a frame was entered but its entry was
// not recorded (arm64: arch_stack_walk around unwind_*, el1_interrupt around irq_enter_rcu, ...).
// The calls inside belong to the enclosing slice (parent); the missing level(s) are drawn as dashed
// "untraced" frames so the stack has no hole. The innermost one is named after the calls' return
// address (their caller), outer ones are unknown ("?"). Extent: first to last call inside.
function callerFn(i) { const c = callerOf(i); return c ? c.split('+')[0] : '?'; }
function gapOf(i) { const p = i < N ? sParent[i] : -1; return p >= 0 ? sDepth[i] - sDepth[p] - 1 : 0; }
(function buildGhosts() {
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
            gh.push([sTask[i], sStart[i], sEnd[i], name, sDepth[p] + L, sLd[p] + L, sFlags[i] & (FL.CTX | FL.CTXINF), sLine[i], p]);
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

// ---- tracks ----
const TRACKS = D.tasks.map((t, i) => ({
    id: i, pid: t.pid, comm: t.comm, comms: t.comms || [], kind: t.kind, cpu: t.cpu, runs: t.runs || [],
    lo: -1, hi: -1, maxD: -1, rows: [], rowsT: [], rowsI: [], maxDT: -1, maxDI: -1, busy: 0, off: [], rs: [], io: [], errs: [], cpus: new Set(),
    collapsed: false, hidden: false, hits: 0, y: 0, h: 0
}));
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
for (let i = 0; i < N; i++) if (sEnd[i] > tMax) tMax = sEnd[i];
for (const r of D.offcpu) if (r[2] !== null && r[2] > tMax) tMax = r[2];
const OPEN_OFF = new Set(D.offcpu.filter(record => record[2] === null));
for (const r of D.offcpu) if (r[2] === null) r[2] = tMax;
// wakeups: [waker_task, ts, target_task, ctx, waker_slice, offcpu_index]
const WAKES = D.wakeups || [];
const OFF_IDX = new Map(D.offcpu.map((r, i) => [r, i]));
const WAKE_OF_OFF = new Map();
for (const w of WAKES) if (w[5] >= 0) WAKE_OF_OFF.set(w[5], w);

const ORDER = TRACKS.filter(t => t.lo >= 0 || t.off.length).sort((a, b) => {
    if (a.kind !== b.kind) return a.kind - b.kind;
    if (a.kind === 1) return (a.cpu || 0) - (b.cpu || 0);
    return b.busy - a.busy;
});
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
    const t = D.t0 + ns;
    return Math.floor(t / 1e9) + '.' + String(Math.floor((t % 1e9) / 1e3)).padStart(6, '0');
}
function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function nameOf(i) { return S[sName[i]]; }
function retOf(i) { return sRet && sRet[i] >= 0 ? S[sRet[i]] : null; }
function callerOf(i) { return sCaller && sCaller[i] >= 0 ? S[sCaller[i]] : null; }
function argsOf(i) { return sArgs && sArgs[i] >= 0 ? S[sArgs[i]] : null; }
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

// ---- DOM skeleton ----
tlRoot.innerHTML = `
<div class="tl-toolbar">
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
  <span class="tl-group">
    <input class="tl-search" placeholder="/ search function (regex)" spellcheck="false" aria-label="Search traced function names (regular expression)" aria-describedby="tlSearchStatus" aria-invalid="false">
    <span class="tl-search-nav">
      <button class="tl-btn" data-act="searchprev" title="Previous search match (Shift+Enter / Shift+N)" aria-label="Previous search match" disabled>◀</button>
      <button class="tl-btn" data-act="searchnext" title="Next search match (Enter / N)" aria-label="Next search match" disabled>▶</button>
    </span>
    <span class="tl-count" id="tlSearchStatus" role="status" aria-live="polite" aria-atomic="true"></span>
  </span>
  <span class="tl-group tl-errnav">
    <button class="tl-btn" data-act="errprev" title="Previous error origin (Shift+E)">◀</button>
    <button class="tl-btn" data-act="errmode" title="Toggle: user-visible errors only / all errors"><span class="err-label"></span></button>
    <button class="tl-btn" data-act="errnext" title="Next error origin (E)">▶</button>
  </span>
  <span class="tl-group">
    <button class="tl-btn" data-act="fit" title="Show the whole trace (0)">Fit</button>
    <button class="tl-btn" data-act="expand" title="Expand / collapse all tracks">Tracks</button>
    <button class="tl-btn" data-act="adj" title="Subtract the estimated tracing overhead from durations (O)">Overhead: measured</button>
    <button class="tl-btn" data-act="inl" title="Show inlined functions as frames between the traced calls (I)">Inline: hidden</button>
    <button class="tl-btn" data-act="an-errors" title="Error summary by syscall / origin / errno">Errors</button>
    <button class="tl-btn" data-act="an-sec" title="Critical sections: irqs-off / preempt-off / BH-off regions">Sections</button>
    <button class="tl-btn" data-act="an-lat" title="Scheduling latency: need-resched windows and what delayed them">Sched latency</button>
    <button class="tl-btn" data-act="notes" title="Show / hide the notes about this trace">Notes</button>
    <button class="tl-btn" data-act="max" title="Maximize: hide the page header and tabs (M)">Max</button>
    <button class="tl-btn" data-act="help" title="Help (?)">?</button>
  </span>
  <span class="tl-group tl-filterbar"><span class="tl-label">Filter</span>
  <input class="tl-ftext" placeholder="same syntax as the text view, e.g. pid:1234 AND graph:vfs_read" spellcheck="false">
  <span class="tl-fcount"></span>
  <button class="tl-btn" data-act="fhide" title="Hide tracks without matches">Hide unmatched</button>
  <button class="tl-btn" data-act="fclear">Clear</button></span>
</div>
<div class="tl-notes"></div>
<div class="tl-stage">
  <canvas class="tl-head-canvas"></canvas>
  <div class="tl-scroll"><canvas class="tl-canvas" tabindex="0" aria-label="Interactive function timeline. Press question mark for keyboard shortcuts."></canvas><div class="tl-spacer"></div></div>
  <div class="tl-help"></div>
</div>
<div class="tl-resizer" title="Drag to resize the panel, double-click to reset"></div>
<div class="tl-detail"></div>
<div class="tl-tip"></div>
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
const elSearch = tlRoot.querySelector('.tl-search');
const elCount = tlRoot.querySelector('.tl-count');
const elSearchPrev = tlRoot.querySelector('[data-act=searchprev]');
const elSearchNext = tlRoot.querySelector('[data-act=searchnext]');
const elToast = tlRoot.querySelector('.tl-toast');
const elFbar = tlRoot.querySelector('.tl-filterbar');
const elResizer = tlRoot.querySelector('.tl-resizer');
const elFtext = tlRoot.querySelector('.tl-ftext');
const elFcount = tlRoot.querySelector('.tl-fcount');

let toastTimer = 0;
function toast(msg) {
    elToast.textContent = msg;
    elToast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => elToast.classList.remove('show'), 1800);
}
