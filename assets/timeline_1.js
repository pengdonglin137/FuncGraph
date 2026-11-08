/* FuncGraph Timeline, part 1: data model + DOM skeleton.
 * Parts 1-4 are concatenated inside function __tlBoot() and share its scope. */
const tlRoot = document.getElementById('timelineView');
const D = JSON.parse(document.getElementById('tlData').textContent);
const N = D.n, C = D.cols, S = D.strings;
const MODE = D.mode;
const TIMED = MODE === 'abs' || MODE === 'rel';
const FL = {
    CTX: 3, IRQOFF: 1 << 2, BHOFF: 1 << 3, RS_SHIFT: 4, PR_SHIFT: 7, MG_SHIFT: 11,
    LAT: 1 << 15, TRUNC: 1 << 16, ORPHAN: 1 << 17, EST: 1 << 18, MISMATCH: 1 << 19,
    NULLRET: 1 << 20, LEAF: 1 << 21, CTXINF: 1 << 22
};
const CTX_NAMES = ['task', 'softirq', 'hardirq', 'NMI'];
const RESCHED_NAMES = ['', 'need-resched (n)', 'need-resched + preempt (N)', 'preempt-resched (p)',
    'lazy (l)', 'lazy + preempt (L)', 'need + lazy (b)', 'need + lazy + preempt (B)'];

// ---- slice columns ----
const sStart = new Float64Array(N), sEnd = new Float64Array(N);
const sDepth = Uint16Array.from(C.depth), sTask = Int32Array.from(C.task);
const sFlags = Uint32Array.from(C.flags), sName = Int32Array.from(C.name);
const sParent = Int32Array.from(C.parent), sLine = Int32Array.from(C.line);
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
const sSelf = new Float64Array(N);
for (let i = 0; i < N; i++) sSelf[i] = sEnd[i] - sStart[i];
for (let i = 0; i < N; i++) { const p = sParent[i]; if (p >= 0) sSelf[p] -= sEnd[i] - sStart[i]; }
for (let i = 0; i < N; i++) if (sSelf[i] < 0) sSelf[i] = 0;
let maxSelf = 1;
for (let i = 0; i < N; i++) if (sSelf[i] > maxSelf) maxSelf = sSelf[i];

// ---- error path data ----
// eRole: -1 none, 0 origin, 1 propagation.  eFate: 0 handled by caller, 1 reaches user space, 2 leaves window
const eErr = new Int32Array(N), eRole = new Int8Array(N).fill(-1), eFate = new Int8Array(N).fill(-1);
const eChain = new Int32Array(N).fill(-1), eVer = new Uint8Array(N);
for (const r of D.errs) {
    const i = r[0];
    eErr[i] = r[1]; eRole[i] = r[2]; eFate[i] = r[3]; eVer[i] = r[4]; eChain[i] = r[5];
}
const CHAINS = D.chains;   // [origin, top, errno, fate, length, verified]
function errName(e) { return D.errnames[String(e)] || ''; }

// ---- tracks ----
const TRACKS = D.tasks.map((t, i) => ({
    id: i, pid: t.pid, comm: t.comm, comms: t.comms || [], kind: t.kind, cpu: t.cpu, runs: t.runs || [],
    lo: -1, hi: -1, maxD: -1, rows: [], busy: 0, off: [], rs: [], io: [], errs: [], cpus: new Set(),
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
for (const t of TRACKS) {
    if (t.lo < 0) continue;
    const cnt = new Int32Array(t.maxD + 1);
    for (let i = t.lo; i < t.hi; i++) cnt[sDepth[i]]++;
    for (let d = 0; d <= t.maxD; d++) t.rows.push(new Int32Array(cnt[d]));
    cnt.fill(0);
    for (let i = t.lo; i < t.hi; i++) { const d = sDepth[i]; t.rows[d][cnt[d]++] = i; }
}
for (const r of D.offcpu) {   // [task, start, end, depth, reason, state, cpuOut, cpuIn, preempted, waker]
    const t = TRACKS[r[0]];
    t.off.push(r);
    if (r[3] > t.maxD) t.maxD = r[3];
}
for (const r of D.resched) TRACKS[r[0]].rs.push(r);   // [task, start, end, maxPreempt, irqoff, closedBy]
for (const r of D.irqoff) TRACKS[r[0]].io.push(r);    // [task, start, end]
for (let c = 0; c < CHAINS.length; c++) TRACKS[sTask[CHAINS[c][0]]].errs.push(c);

let tMax = 1;
for (let i = 0; i < N; i++) if (sEnd[i] > tMax) tMax = sEnd[i];
for (const r of D.offcpu) if (r[2] !== null && r[2] > tMax) tMax = r[2];
for (const r of D.offcpu) if (r[2] === null) r[2] = tMax;

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

// ---- view state ----
const view = { s: 0, e: tMax };
const ov = { err: true, irq: true, rs: true, off: true, preempt: false, mk: true };
let colorMode = 'ctx';
let sel = -1, hov = -1, hovOff = null, hovCpu = null;
let range = null;            // {s, e, track|null}
let search = null;           // RegExp
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
    <button class="tl-chip off" data-ov="preempt"><i class="dot" style="background:#6b7280"></i>Preempt depth</button>
    <button class="tl-chip" data-ov="mk"><i class="dot" style="background:#8e44ad"></i>Markers</button>
  </span>
  <span class="tl-group">
    <input class="tl-search" placeholder="/ search function (regex)" spellcheck="false">
    <span class="tl-count"></span>
  </span>
  <span class="tl-group tl-errnav">
    <button class="tl-btn" data-act="errprev" title="Previous error origin (Shift+E)">◀</button>
    <button class="tl-btn" data-act="errmode" title="Toggle: user-visible errors only / all errors"><span class="err-label"></span></button>
    <button class="tl-btn" data-act="errnext" title="Next error origin (E)">▶</button>
  </span>
  <span class="tl-group">
    <button class="tl-btn" data-act="fit" title="Show the whole trace (0)">Fit</button>
    <button class="tl-btn" data-act="expand" title="Expand / collapse all tracks">Tracks</button>
    <button class="tl-btn" data-act="help" title="Help (?)">?</button>
  </span>
</div>
<div class="tl-filterbar"><span class="tl-label">Filter</span>
  <input class="tl-ftext" placeholder="same syntax as the text view, e.g. pid:1234 AND graph:vfs_read" spellcheck="false">
  <span class="tl-fcount"></span>
  <button class="tl-btn" data-act="fhide" title="Hide tracks without matches">Hide unmatched</button>
  <button class="tl-btn" data-act="fclear">Clear</button></div>
<div class="tl-notes"></div>
<div class="tl-stage">
  <canvas class="tl-head-canvas"></canvas>
  <div class="tl-scroll"><canvas class="tl-canvas"></canvas><div class="tl-spacer"></div></div>
  <div class="tl-help"></div>
</div>
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
const elToast = tlRoot.querySelector('.tl-toast');
const elFbar = tlRoot.querySelector('.tl-filterbar');
const elFtext = tlRoot.querySelector('.tl-ftext');
const elFcount = tlRoot.querySelector('.tl-fcount');

let toastTimer = 0;
function toast(msg) {
    elToast.textContent = msg;
    elToast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => elToast.classList.remove('show'), 1800);
}
