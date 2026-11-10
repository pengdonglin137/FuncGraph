        // Global keys (shared across files): theme
        // Per-file keys: expandedLines, foldedBlocks, filterBoxVisible, keyboardHintShown,
        //   infoPanelExpanded, floatingControlsHidden, floatingTogglePos, viewState, heatmapEnabled
        function lsGet(key) { return localStorage.getItem(_lsPrefix + key); }
        // Source blocks are de-duplicated in #fgSrc and filled in on first expand
        // Filter state shared with the Timeline tab (line numbers that pass the filter)
        window.fgFilterState = { text: '', lines: null };
        window.fgFilterProgress = null;
        function fgNotifyFilter(text, lines) {
            window.fgFilterState = { text: text, lines: lines };
            if (typeof window.fgOnFilter === 'function') window.fgOnFilter();
        }
        function fgSourceHtml(source) {
            return fgData.read('fgSrc')[source] || '';
        }
        function fgSourceForLine(line) {
            const index = fgData.read('fgSrcLines');
            return Object.prototype.hasOwnProperty.call(index, line) ? index[line] : null;
        }
        // Two layouts for source blocks (toolbar "Source" button):
        //   chain: inline frames outer -> inner, each collapsible, innermost (the call) open
        //   flat : faddr2line order (innermost first), everything expanded
        let fgSrcMode = lsGet('srcMode') || 'chain';
        const fgSourceNodes = typeof WeakRef === 'function' && typeof FinalizationRegistry === 'function' ? new Set() : null;
        const fgSourceSeen = new WeakSet();
        const fgSourceCleanup = fgSourceNodes && new FinalizationRegistry(reference => fgSourceNodes.delete(reference));
        function fgTrackSource(element) {
            if (!fgSourceNodes || fgSourceSeen.has(element)) return;
            const reference = new WeakRef(element);
            fgSourceSeen.add(element);
            fgSourceNodes.add(reference);
            fgSourceCleanup.register(element, reference);
        }
        function fgSourceMode() { return fgSrcMode; }
        function fgFill(el, force) {
            if (!el || el.dataset.src === undefined || (el.firstChild && !force)) return;
            el.innerHTML = fgSourceHtml(+el.dataset.src);
            if (fgSrcMode === 'chain') fgChainView(el);
            fgTrackSource(el);
        }
        function fgChainView(el) {
            // split into frames: a location line starts a frame, source lines belong to it
            const frames = [];
            let head = null;
            const tail = [];
            for (const node of Array.from(el.childNodes)) {
                const isLoc = node.classList && node.classList.contains('location-link');
                if (isLoc && node.querySelector && node.querySelector('strong')) { head = node; continue; }
                if (isLoc) { frames.push({ loc: node, lines: [] }); continue; }
                if (frames.length && node.classList && node.classList.contains('source-line')) { frames[frames.length - 1].lines.push(node); continue; }
                tail.push(node);
            }
            if (frames.length < 2) return;           // no inlining: keep as is
            el.innerHTML = '';
            el.classList.add('src-chain');
            if (head) el.appendChild(head);
            const n = frames.length;
            frames.reverse().forEach((f, k) => {     // outer (traced function) first
                const d = document.createElement('details');
                d.className = 'src-frame' + (k === n - 1 ? ' src-call' : '');
                d.style.marginLeft = (k * 14) + 'px';
                if (k === n - 1) d.open = true;
                const sm = document.createElement('summary');
                const txt = f.loc.textContent.replace(/^\(inlined\s+by\)\s*/, '');
                const m = txt.match(/^(\S+)\s+at\s+(.*)$/);
                const tag = k === 0 ? 'traced' : 'inline';
                sm.innerHTML = '<span class="src-tag ' + tag + '">' + tag + '</span> <b>' +
                    (m ? escapeHtml(m[1]) : escapeHtml(txt)) + '</b> <span class="src-at">' +
                    (m ? escapeHtml(m[2]) : '') + '</span>' +
                    (k < n - 1 ? ' <span class="src-note">→ inlines ' + escapeHtml((frames[k + 1].loc.textContent.replace(/^\(inlined\s+by\)\s*/, '').split(/\s+at\s+/)[0]) || '') + ' here</span>'
                               : ' <span class="src-note">← the traced call happens here</span>');
                const link = f.loc.querySelector('a');
                if (link) { const a = link.cloneNode(true); a.textContent = '↗'; a.className = 'src-open'; sm.appendChild(a); }
                d.appendChild(sm);
                for (const ln of f.lines) d.appendChild(ln);
                el.appendChild(d);
            });
            for (const t of tail) el.appendChild(t);
        }
        // ---- text order: by time (as captured) or grouped by task ----
        // ftrace interleaves all CPUs by time; with several busy tasks a task's calls are scattered.
        // Grouping moves each task's lines together (original order inside the task) and replaces the
        // context switches by a summary line. Folding/filters work on attributes, not on order.
        let fgTextOrder = lsGet('textOrder') || 'time';
        const RE_SWITCH_LINE = /^\s*(\d+)\)\s+(.+?)-(\d+)\s+=>\s+(.+?)-(\d+)\s*$/;
        const RE_TS_LINE = /^\s*(\d+\.\d+)\s*\|/;
        function fgTaskKey(line) {
            const pid = line.getAttribute('data-pid'), cpu = line.getAttribute('data-cpu');
            if (pid === '' || pid === null) return cpu ? 'cpu' + cpu : '';
            return pid === '0' ? 'idle' + cpu : 'p' + pid;
        }
        function fgBlocks(container) {
            // [line, expandedContentOrNull] in DOM order
            const out = [];
            for (const child of container.children) {
                for (const element of child.classList.contains('text-chunk') ? child.children : [child]) {
                    if (element.classList.contains('line-container')) out.push([element, null]);
                    else if (element.classList.contains('expanded-content') && out.length) out[out.length - 1][1] = element;
                }
            }
            return out;
        }
        function fgSetTextOrder(mode) {
            const container = document.getElementById('content');
            if (!container) return;
            // reorder off-document: moving tens of thousands of live nodes is slow otherwise
            const parent = container.parentNode, marker = document.createComment('content');
            parent.replaceChild(marker, container);
            try { fgReorder(container, mode); } finally { parent.replaceChild(container, marker); }
            const ctl = document.querySelector('.controls');
            if (ctl) document.documentElement.style.setProperty('--ctl-h', ctl.offsetHeight + 'px');
            updateExpandableLines();
        }
        function fgReorder(container, mode, orderedBlocks) {
            container.querySelectorAll('.task-group-head, .task-gap').forEach(e => e.remove());
            const blocks = orderedBlocks || fgBlocks(container).sort((a, b) => +a[0].dataset.lineNumber - +b[0].dataset.lineNumber);
            const frag = document.createDocumentFragment();
            const chunkSize = Number(container.querySelector('.text-chunk')?.dataset.chunkSize) || 512;
            let chunk = null, chunkRows = 0;
            const boundary = element => { chunk = null; chunkRows = 0; frag.appendChild(element); };
            const put = block => {
                if (!chunk || chunkRows >= chunkSize) {
                    chunk = document.createElement('div'); chunk.className = 'text-chunk';
                    chunk.dataset.chunkSize = chunkSize; frag.appendChild(chunk); chunkRows = 0;
                }
                chunk.appendChild(block[0]); if (block[1]) chunk.appendChild(block[1]); chunkRows++;
            };
            fgTextOrder = mode;
            lsSet('textOrder', mode);
            const btn = document.getElementById('textOrderBtn');
            if (btn) btn.textContent = 'Order: ' + (mode === 'task' ? 'by task' : 'time');
            if (mode !== 'task') {
                blocks.forEach(put);
                container.replaceChildren(frag);
                container.classList.remove('grouped');
                return;
            }
            // header comments stay first; untasked lines (separators) follow the previous line's task
            const groups = new Map(), head = [];
            const realBefore = new Map();         // line number -> number of real lines before it
            let nReal = 0;
            let cur = '';
            const txt = b => (b[0].querySelector('.line-content') || b[0]).textContent;
            for (const b of blocks) {
                realBefore.set(+b[0].dataset.lineNumber, nReal);
                if (!/^\s*(-{5,})?\s*$/.test(txt(b))) nReal++;
                let k = fgTaskKey(b[0]);
                if (!k) {
                    if (!cur) { head.push(b); continue; }
                    if (/^\s*(-{5,})?\s*$/.test(txt(b))) { b[0].classList.add('grp-hidden'); }   // separators, blank lines
                    k = cur;
                }
                cur = k;
                if (!groups.has(k)) groups.set(k, []);
                groups.get(k).push(b);
            }
            head.forEach(put);
            // busiest tasks first; idle and per-CPU (no funcgraph-proc) groups after them
            const order = Array.from(groups.entries()).sort((a, b) => {
                const ia = a[0].startsWith('idle'), ib = b[0].startsWith('idle');
                if (ia !== ib) return ia ? 1 : -1;
                return b[1].length - a[1].length;
            });
            for (const [k, list] of order) {
                const real = list.filter(b => !b[0].classList.contains('grp-hidden'));
                const first = real[0] ? real[0][0] : list[0][0];
                const cpus = new Set(real.map(b => b[0].getAttribute('data-cpu')).filter(Boolean));
                const h = document.createElement('div');
                h.className = 'task-group-head';
                h.textContent = (first.getAttribute('data-comm') || k) + '  ' + (first.getAttribute('data-pid') || '') +
                    '   ·  ' + real.length + ' lines  ·  CPU ' + Array.from(cpus).sort((a, b) => a - b).join(',');
                boundary(h);
                let pendingSwitch = null, prevLn = -1, prevTs = null;
                for (const b of list) {
                    const t = txt(b), ln = +b[0].dataset.lineNumber;
                    const sw = t.match(RE_SWITCH_LINE);
                    const tm = t.match(RE_TS_LINE);
                    if (pendingSwitch && !sw && !b[0].classList.contains('grp-hidden')) {
                        const gap = document.createElement('div');
                        gap.className = 'task-gap';
                        const dt = (tm && prevTs !== null) ? ' after ' + ((parseFloat(tm[1]) - prevTs) * 1e3).toFixed(3) + ' ms' : '';
                        gap.textContent = '⇣ switched out on CPU ' + pendingSwitch[1] + ' → ' + pendingSwitch[4].trim() + '-' + pendingSwitch[5] +
                            '; resumes at line ' + ln + dt + ' (' + (realBefore.get(ln) - realBefore.get(prevLn) - 1) + ' lines of other tasks in between)';
                        boundary(gap);
                        pendingSwitch = null;
                    }
                    if (sw) { pendingSwitch = sw; b[0].classList.add('task-switch'); }
                    if (tm) prevTs = parseFloat(tm[1]);
                    if (!b[0].classList.contains('grp-hidden')) prevLn = ln;
                    put(b);
                }
            }
            container.replaceChildren(frag);
            container.classList.add('grouped');
        }
        function fgToggleTextOrder() { fgSetTextOrder(fgTextOrder === 'task' ? 'time' : 'task'); }
        function fgRestoreTextOrder() {
            if (fgTextDeferred()) return;
            const b = document.getElementById('textOrderBtn');
            if (b) b.textContent = 'Order: ' + (fgTextOrder === 'task' ? 'by task' : 'time');
            if (fgTextOrder === 'task') fgSetTextOrder('task');
        }
        document.addEventListener('DOMContentLoaded', fgRestoreTextOrder);
        function escapeHtml(s) {
            return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
        }
        function fgToggleSrcMode() {
            fgSrcMode = fgSrcMode === 'chain' ? 'flat' : 'chain';
            lsSet('srcMode', fgSrcMode);
            const b = document.getElementById('srcModeBtn');
            if (b) b.textContent = 'Source: ' + fgSrcMode;
            const update = element => {
                if (element.hasAttribute('data-fg-source-mode')) element.textContent = 'Source: ' + fgSrcMode;
                else if (element.firstChild) { element.classList.remove('src-chain'); fgFill(element, true); }
            };
            if (fgSourceNodes) {
                for (const reference of fgSourceNodes) {
                    const element = reference.deref();
                    if (!element) fgSourceNodes.delete(reference);
                    else if (element.isConnected) update(element);
                }
            } else document.querySelectorAll('[data-fg-source-mode], .expanded-content[data-src]').forEach(update);
        }
        document.addEventListener('DOMContentLoaded', () => {
            const b = document.getElementById('srcModeBtn');
            if (b) b.textContent = 'Source: ' + fgSrcMode;
        });
        function lsSet(key, val) { localStorage.setItem(_lsPrefix + key, val); }
        function lsRemove(key) { localStorage.removeItem(_lsPrefix + key); }

        // 标记是否正在进行文本选择
        let isSelectingText = false;
        // 记录鼠标按下时的位置和时间
        let mouseDownInfo = { x: 0, y: 0, time: 0 };
        // 记录当前高亮行
        let highlightedLine = null;
        // 记录展开状态
        let expandedLines = JSON.parse(lsGet('expandedLines')) || {};
        // 记录折叠状态
        let foldedBlocks = JSON.parse(lsGet('foldedBlocks')) || {};
        // 双击选择相关变量
        let lastClickTime = 0;
        let lastClickTarget = null;
        let doubleClickTimer = null;
        let clickTimer = null;
        let doubleClickState = 0; // 0: 初始状态, 1: 第一次双击选中单词, 2: 第二次双击选中整行
        // 键盘导航相关变量
        let keyboardSelectedLine = null;
        let keyboardSelectedIndex = -1;
        let allLines = [];
        let fgTextInitialized = false;
        let fgFilterGeneration = 0;
        let fgFilterController = null;
        function fgTextDeferred() { return document.getElementById('content').hasAttribute('data-fg-text'); }
        let textGuideRevision = 0;
        let expandableLines = [];
        let expandableLineIndices = [];
        let keyboardHintTimer = null;
        // 记录当前滚动位置
        let scrollPositionBeforeClick = { x: 0, y: 0 };
        // 是否显示过键盘提示
        let keyboardHintShown = lsGet('keyboardHintShown') === 'true';
        // 总行数（从summary中获取）
        let total_lines = 0;

        // 过滤功能相关变量
        let currentFilter = { cpu: [], pid: [], comm: [] };

        // 折叠图标键盘事件处理
                // 折叠图标点击事件处理
                function handleFoldIconClick(event, icon) {
                    event.stopPropagation();
                    const lineContainer = icon.closest('.line-container');
                    if (lineContainer) {
                        const foldId = lineContainer.getAttribute('data-fold-id');
                        if (foldId) {
                            // F1: Shift+Click for recursive fold/unfold
                            if (event.shiftKey) {
                                toggleFoldRecursive(foldId);
                            } else {
                                toggleFold(foldId, 'entry');
                            }
                        }
                    }
                }
        function handleFoldIconKeydown(event, icon) {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                handleFoldIconClick(event, icon);
            }
        }

        function initTextFolds() {
            const content = document.getElementById('content');
            if (!content) return;
            const guideStyle = document.getElementById('fgTreeGuides');
            const guideButton = document.getElementById('foldGuidesBtn');
            if (guideStyle && guideButton) {
                const updateGuideButton = () => {
                    const enabled = !guideStyle.sheet.disabled;
                    guideButton.textContent = 'Tree guides: ' + (enabled ? 'on' : 'off');
                    guideButton.setAttribute('aria-pressed', String(enabled));
                };
                updateGuideButton();
                guideButton.addEventListener('click', () => {
                    guideStyle.sheet.disabled = !guideStyle.sheet.disabled;
                    lsSet('foldGuides', String(!guideStyle.sheet.disabled));
                    updateGuideButton();
                    scheduleGuideScope();
                });
            }
            let scopeId = null;
            let scopeRows = [];
            let guideLayer = null;
            let guideFrame = null;
            let guideGroups = [];
            let guideRevision = -1;
            const guideMeasure = content.querySelector('.call-indent');
            const foldControl = target => target?.closest?.('.fold-icon, .fold-brace');
            const scopeControl = target => target?.closest?.('.fold-icon, .fold-brace, .fold-marker, .fold-close');
            function drawGuideScope() {
                guideFrame = null;
                if (guideLayer) guideLayer.getContext('2d').clearRect(0, 0, guideLayer.width, guideLayer.height);
                if (!guideMeasure || !guideStyle || guideStyle.sheet.disabled) return;
                const contentRect = content.getBoundingClientRect();
                if (contentRect.height === 0) return;
                const viewportWidth = document.documentElement.clientWidth;
                const viewportHeight = window.innerHeight;
                const step = parseFloat(getComputedStyle(guideMeasure, '::before').width);
                if (!(step > 0)) return;
                const unit = step / 2;
                const entry = scopeRows[0];
                const activeOffset = entry && !entry.classList.contains('folded')
                    ? entry.querySelector('.call-indent').getBoundingClientRect().width - unit : null;
                if (!guideLayer) {
                    guideLayer = document.createElement('canvas');
                    guideLayer.className = 'fold-guide-layer';
                    guideLayer.setAttribute('aria-hidden', 'true');
                    document.body.appendChild(guideLayer);
                }
                const scale = window.devicePixelRatio || 1;
                const width = Math.round(viewportWidth * scale);
                const height = Math.round(viewportHeight * scale);
                if (guideLayer.width !== width || guideLayer.height !== height) {
                    guideLayer.width = width;
                    guideLayer.height = height;
                }
                const context = guideLayer.getContext('2d');
                context.setTransform(scale, 0, 0, scale, 0, 0);
                const strokePixels = Math.max(1, Math.round(scale));
                const stroke = strokePixels / scale;
                const snap = value => Math.round(value * scale) / scale;
                const strokeOrigin = axis => Math.round(axis * scale - strokePixels / 2) / scale;
                const palette = getComputedStyle(content);
                const baseColor = palette.getPropertyValue('--tree-guide').trim();
                const activeColor = palette.getPropertyValue('--tree-guide-active').trim();
                if (guideRevision !== textGuideRevision) {
                    guideGroups = Array.from(content.children).filter(group => group.matches('.text-chunk, .line-container'));
                    guideRevision = textGuideRevision;
                }
                const toolbarBottom = content.previousElementSibling?.getBoundingClientRect().bottom || 0;
                const viewportTop = Math.max(0, contentRect.top, toolbarBottom);
                const bottom = Math.min(viewportHeight, contentRect.bottom);
                if (bottom <= viewportTop) return;
                context.save();
                context.beginPath();
                context.rect(0, viewportTop, viewportWidth, bottom - viewportTop);
                context.clip();
                let lower = 0;
                let upper = guideGroups.length;
                while (lower < upper) {
                    const middle = Math.floor((lower + upper) / 2);
                    if (guideGroups[middle].getBoundingClientRect().bottom <= viewportTop) lower = middle + 1;
                    else upper = middle;
                }
                for (let groupIndex = lower; groupIndex < guideGroups.length; groupIndex++) {
                    const group = guideGroups[groupIndex];
                    if (group.getBoundingClientRect().top >= bottom) break;
                    const rows = (group.matches('.line-container') ? [group] : Array.from(group.querySelectorAll('.line-container')))
                        .filter(row => row.style.display !== 'none' && !row.classList.contains('grp-hidden'));
                    let first = 0;
                    let last = rows.length;
                    while (first < last) {
                        const middle = Math.floor((first + last) / 2);
                        if (rows[middle].getBoundingClientRect().bottom <= viewportTop) first = middle + 1;
                        else last = middle;
                    }
                    for (let rowIndex = first; rowIndex < rows.length; rowIndex++) {
                        const row = rows[rowIndex];
                        const rect = row.getBoundingClientRect();
                        if (rect.top >= bottom) break;
                        const rowIndent = row.querySelector('.call-indent');
                        if (!rowIndent) continue;
                        const indentRect = rowIndent.getBoundingClientRect();
                        const top = snap(Math.max(viewportTop, rect.top));
                        const end = snap(Math.min(bottom, rect.bottom));
                        const ancestors = (row.getAttribute('data-fold-ancestors') || '').split(' ');
                        const related = activeOffset !== null && ancestors.includes(scopeId);
                        const start = Math.max(0, Math.ceil((-indentRect.left - unit) / step));
                        const limit = Math.min(indentRect.right - step, viewportWidth);
                        for (let column = start; indentRect.left + unit + column * step < limit; column++) {
                            const offset = unit + column * step;
                            context.fillStyle = related && Math.abs(offset - activeOffset) < unit / 2 ? activeColor : baseColor;
                            context.fillRect(strokeOrigin(indentRect.left + offset), top, stroke, end - top);
                        }
                        const own = activeOffset !== null && row.getAttribute('data-fold-id') === scopeId;
                        context.fillStyle = own ? activeColor : baseColor;
                        const axis = indentRect.right - unit;
                        if (axis < 0 || axis > viewportWidth) continue;
                        if (row.classList.contains('fold-entry') && !row.classList.contains('folded')) {
                            const from = snap(rect.top + rect.height / 2 + 6);
                            if (end > from) context.fillRect(strokeOrigin(axis), from, stroke, end - from);
                        } else if (row.getAttribute('data-fold-type') === 'exit') {
                            const center = strokeOrigin(axis) + stroke / 2;
                            const turn = strokeOrigin(rect.top + rect.height / 2) + stroke / 2;
                            const finish = snap(indentRect.right - 2);
                            const radius = Math.min(3, Math.max(0, finish - center));
                            context.strokeStyle = context.fillStyle;
                            context.lineWidth = stroke;
                            context.beginPath();
                            context.moveTo(center, top);
                            context.lineTo(center, turn - radius);
                            context.arcTo(center, turn, center + radius, turn, radius);
                            context.lineTo(finish, turn);
                            context.stroke();
                        }
                    }
                }
                context.restore();
            }
            function scheduleGuideScope() {
                if (guideFrame === null) guideFrame = requestAnimationFrame(drawGuideScope);
            }
            function showScope(control) {
                const row = control?.closest('.line-container');
                const entry = row?.getAttribute('data-fold-type') === 'exit'
                    ? document.getElementById(row.getAttribute('data-fold-start')) : row;
                const foldId = entry?.getAttribute('data-fold-id') || null;
                if (control?.matches('.fold-icon, .fold-brace')) {
                    const action = foldedBlocks[foldId] ? 'Expand' : 'Collapse';
                    control.title = action + ' call; Shift-click: entire subtree';
                }
                if (foldId === scopeId) return;
                scopeRows.forEach(row => row.classList.remove('fold-scope'));
                scopeId = foldId;
                scopeRows = [];
                if (entry) {
                    const exit = document.getElementById(entry.getAttribute('data-fold-end'));
                    scopeRows = exit ? [entry, exit] : [entry];
                    scopeRows.forEach(row => row.classList.add('fold-scope'));
                }
                scheduleGuideScope();
            }
            content.addEventListener('click', event => {
                const control = foldControl(event.target);
                if (control) {
                    handleFoldIconClick(event, control);
                    scheduleGuideScope();
                }
            });
            window.addEventListener('scroll', scheduleGuideScope, {passive:true});
            window.addEventListener('resize', scheduleGuideScope);
            content.addEventListener('fg-guides-change', scheduleGuideScope);
            if (window.ResizeObserver) new ResizeObserver(scheduleGuideScope).observe(content);
            new MutationObserver(scheduleGuideScope).observe(document.documentElement,
                {attributes:true, attributeFilter:['data-theme']});
            scheduleGuideScope();
            for (const type of ['pointerover', 'focusin']) {
                content.addEventListener(type, event => {
                    const control = scopeControl(event.target);
                    if (control) showScope(control);
                });
            }
            for (const type of ['pointerout', 'focusout']) {
                content.addEventListener(type, event => {
                    if (scopeControl(event.target)) {
                        showScope(scopeControl(event.relatedTarget) ||
                            (type === 'pointerout' ? foldControl(document.activeElement) : null));
                    }
                });
            }
        }

        // 初始化展开状态
        function restoreExpandedState() {
            for (const [lineId, isExpanded] of Object.entries(expandedLines)) {
                if (isExpanded) {
                    const content = document.getElementById(lineId + '_content');
                    const container = content && content.previousElementSibling;
                    const btn = container && container.querySelector('.expand-btn');

                    if (content && btn && container) {
                        if (container.style.display !== 'none') {
                            fgFill(content); content.style.display = 'block';
                        }
                        if (btn) btn.textContent = '-';
                        container.classList.add('selected');
                    }
                }
            }
        }

        // 恢复折叠状态
        // Restore all folded states on page load — uses _setFolded for consistency
        function restoreFoldedState() {
            for (const [foldId, isFolded] of Object.entries(foldedBlocks)) {
                if (isFolded) _setFolded(foldId, true, true);
            }
        }

        // 保存展开状态
        let _expandedStateDirty = false;
        let _expandedFlushTimer = null;
        function saveExpandedState(lineId, isExpanded) {
            expandedLines[lineId] = isExpanded;
            // Batch writes: defer localStorage flush to avoid O(n) serialization per line
            if (!_expandedStateDirty) {
                _expandedStateDirty = true;
                if (_expandedFlushTimer) cancelAnimationFrame(_expandedFlushTimer);
                _expandedFlushTimer = requestAnimationFrame(flushExpandedState);
            }
        }
        function flushExpandedState() {
            if (_expandedStateDirty) {
                lsSet('expandedLines', JSON.stringify(expandedLines));
                _expandedStateDirty = false;
                _expandedFlushTimer = null;
            }
        }
        
        // 初始化键盘导航
        function initKeyboardNavigation() {
            // 使用updateExpandableLines来初始化所有变量
            updateExpandableLines();
            
            // 如果有可展开行，设置默认选中第一个可展开行
            if (expandableLineIndices.length > 0) {
                setKeyboardSelectedLine(expandableLineIndices[0], false);
            } else if (allLines.length > 0) {
                // 如果没有可展开行，选中第一行
                setKeyboardSelectedLine(0, false);
            }
            
            // 初始显示键盘提示，但只在第一次加载页面时
            // shortcuts are shown on demand (header "?" button) instead of popping over the trace
        }
        
        // 显示键盘提示
        function showKeyboardHint() {
            const view = typeof fgCurrentView === 'function' ? fgCurrentView() : 'text';
            if (view === 'timeline') {
                Promise.resolve(fgSwitchView(view)).then(api => {
                    if (fgCurrentView() === view && api && api.showHelp) api.showHelp();
                });
                return;
            }
            const hint = document.getElementById('keyboardHint');
            hint.querySelectorAll('[data-help-view]').forEach(section => { section.hidden = section.dataset.helpView !== view; });
            hint.setAttribute('aria-label', view === 'compare' ? 'Compare help' : 'Text view help');
            hint.style.display = 'block';
            
            // 8秒后自动隐藏
            if (keyboardHintTimer) {
                clearTimeout(keyboardHintTimer);
            }
            keyboardHintTimer = setTimeout(() => {
                hint.style.display = 'none';
            }, 8000);
        }
        
        // 关闭键盘提示
        function closeKeyboardHint() {
            const hint = document.getElementById('keyboardHint');
            hint.style.display = 'none';
            if (keyboardHintTimer) {
                clearTimeout(keyboardHintTimer);
                keyboardHintTimer = null;
            }
        }

        // 切换过滤语法帮助面板
        function toggleFilterHelp() {
            const help = document.getElementById('filterSyntaxHelp');
            if (help) help.classList.toggle('visible');
        }

        // 显示过滤提示
        function showHint(event, text) {
            const hint = document.getElementById('filterHint');
            if (hint) {
                hint.textContent = text;
                hint.style.display = 'block';

                // 计算位置：在输入框上方
                const rect = event.target.getBoundingClientRect();
                const hintHeight = hint.offsetHeight || 40; // 默认高度
                hint.style.left = rect.left + 'px';
                hint.style.top = (rect.top - hintHeight - 8) + 'px';
            }
        }

        // 隐藏过滤提示
        function hideHint() {
            const hint = document.getElementById('filterHint');
            if (hint) {
                hint.style.display = 'none';
            }
        }

        // 处理回车键过滤
        function handleFilterKeypress(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                applyFilter();
            }
        }

        // 切换过滤器窗口的显示/隐藏
        function toggleFilterBox() {
            const filterBox = document.getElementById('filterBox');
            if (filterBox) {
                if (filterBox.style.display === 'none') {
                    filterBox.style.display = 'flex';
                    lsSet('filterBoxVisible', 'true');
                } else {
                    filterBox.style.display = 'none';
                    lsSet('filterBoxVisible', 'false');
                }
            }
        }

        // ====== Unified Filter Expression Parser ======

        // Tokenize filter expression into tokens
        function tokenizeFilter(text) {
            const tokens = [];
            let i = 0;
            while (i < text.length) {
                // Skip whitespace
                if (text[i] === ' ' || text[i] === '\t') { i++; continue; }
                // Parentheses
                if (text[i] === '(') { tokens.push({ type: 'LPAREN' }); i++; continue; }
                if (text[i] === ')') { tokens.push({ type: 'RPAREN' }); i++; continue; }
                // Keywords: AND, OR, NOT (case-insensitive)
                const upper3 = text.slice(i, i + 3).toUpperCase();
                const upper2 = text.slice(i, i + 2).toUpperCase();
                if (upper3 === 'AND' && (i + 3 >= text.length || /[\s()]/.test(text[i + 3]))) {
                    tokens.push({ type: 'AND' }); i += 3; continue;
                }
                if (upper2 === 'OR' && (i + 2 >= text.length || /[\s()]/.test(text[i + 2]))) {
                    tokens.push({ type: 'OR' }); i += 2; continue;
                }
                if (upper3 === 'NOT' && (i + 3 >= text.length || /[\s()]/.test(text[i + 3]))) {
                    tokens.push({ type: 'NOT' }); i += 3; continue;
                }
                // && and ||
                if (text[i] === '&' && text[i + 1] === '&') {
                    tokens.push({ type: 'AND' }); i += 2; continue;
                }
                if (text[i] === '|' && text[i + 1] === '|') {
                    tokens.push({ type: 'OR' }); i += 2; continue;
                }
                if (text[i] === '!') {
                    tokens.push({ type: 'NOT' }); i++; continue;
                }
                // field:value condition (field may contain letters/digits/underscore, value may contain anything except spaces and parens)
                const condMatch = text.slice(i).match(/^([a-zA-Z_][a-zA-Z0-9_]*):(>=|<=|>|<|=)?([^\s()]*)/);
                if (condMatch) {
                    const field = condMatch[1];
                    const op = condMatch[2] || '=';
                    const value = condMatch[3];
                    tokens.push({ type: 'COND', field: field.toLowerCase(), op, value });
                    i += condMatch[0].length;
                    continue;
                }
                // Sort directive: sort:desc or sort:asc
                const sortMatch = text.slice(i).match(/^sort:(desc|asc)/i);
                if (sortMatch) {
                    tokens.push({ type: 'SORT', value: sortMatch[1].toLowerCase() });
                    i += sortMatch[0].length;
                    continue;
                }
                // Unknown character, skip
                i++;
            }
            return tokens;
        }

        // Recursive descent parser: expr → orExpr
        // orExpr → andExpr (OR andExpr)*
        // andExpr → notExpr (AND notExpr)*
        // notExpr → NOT notExpr | atom
        // atom → COND | LPAREN expr RPAREN
        function parseFilterExpr(text) {
            const tokens = tokenizeFilter(text);
            let sortDir = '';
            // Extract sort directive
            const remaining = tokens.filter(t => {
                if (t.type === 'SORT') { sortDir = t.value; return false; }
                return true;
            });
            let pos = 0;

            function peek() { return remaining[pos] || null; }
            function advance() { return remaining[pos++]; }

            function parseOr() {
                let left = parseAnd();
                while (peek() && peek().type === 'OR') {
                    advance();
                    const right = parseAnd();
                    left = { type: 'OR', left, right };
                }
                return left;
            }

            function parseAnd() {
                let left = parseNot();
                while (peek() && peek().type === 'AND') {
                    advance();
                    const right = parseNot();
                    left = { type: 'AND', left, right };
                }
                // Implicit AND: two conditions without operator
                while (peek() && (peek().type === 'COND' || peek().type === 'NOT' || peek().type === 'LPAREN')) {
                    const right = parseNot();
                    left = { type: 'AND', left, right };
                }
                return left;
            }

            function parseNot() {
                if (peek() && peek().type === 'NOT') {
                    advance();
                    const operand = parseNot();
                    return { type: 'NOT', operand };
                }
                return parseAtom();
            }

            function parseAtom() {
                const t = peek();
                if (!t) return null;
                if (t.type === 'LPAREN') {
                    advance();
                    const expr = parseOr();
                    if (peek() && peek().type === 'RPAREN') advance();
                    return expr;
                }
                if (t.type === 'COND') {
                    advance();
                    return t;
                }
                // Skip unexpected token
                advance();
                return parseAtom();
            }

            const ast = remaining.length > 0 ? parseOr() : null;
            return { ast, sortDir };
        }

        // Evaluate a single condition against a line element
        function evalCondition(line, cond, graphSets = _graphFoldIds) {
            if (!cond || cond.type !== 'COND') return true;
            const { field, op, value } = cond;

            // Map field names to data attributes
            const fieldAttrMap = {
                'cpu': 'data-cpu', 'pid': 'data-pid', 'comm': 'data-comm',
                'ret': 'data-ret', 'params': 'data-params',
                'duration': 'data-duration',
                'irqs': 'data-irqs', 'resched': 'data-resched',
                'hirq': 'data-hirq', 'preempt': 'data-preempt'
            };

            // func: simple function name match
            if (field === 'func') {
                const funcAttr = line.getAttribute('data-func');
                if (!funcAttr) return false;
                try { return new RegExp(value, 'i').test(funcAttr); }
                catch(e) { return funcAttr.includes(value); }
            }

            // graph: show function's complete call tree
            if (field === 'graph') {
                const key = 'graph:' + value;
                const targetIds = graphSets[key];
                if (!targetIds || targetIds.size === 0) return false;

                const foldId = line.getAttribute('data-fold-id');
                const ancestorsStr = line.getAttribute('data-fold-ancestors') || '';

                // Line's own fold-id matches, or an ancestor matches
                if (foldId && targetIds.has(foldId)) return true;
                if (ancestorsStr) {
                    const ancestors = ancestorsStr.split(' ');
                    for (let i = 0; i < ancestors.length; i++) {
                        if (targetIds.has(ancestors[i])) return true;
                    }
                }
                return false;
            }

            // notrace: hide function's complete call tree
            if (field === 'notrace') {
                const key = 'notrace:' + value;
                const targetIds = graphSets[key];
                if (!targetIds || targetIds.size === 0) return true; // no match = show all

                const foldId = line.getAttribute('data-fold-id');
                const ancestorsStr = line.getAttribute('data-fold-ancestors') || '';

                if (foldId && targetIds.has(foldId)) return false;
                if (ancestorsStr) {
                    const ancestors = ancestorsStr.split(' ');
                    for (let i = 0; i < ancestors.length; i++) {
                        if (targetIds.has(ancestors[i])) return false;
                    }
                }
                return true;
            }

            const attrName = fieldAttrMap[field];
            if (!attrName) return true; // Unknown field, pass through

            // Special handling for ret:all
            if (field === 'ret' && value.toLowerCase() === 'all') {
                const retAttr = line.getAttribute('data-ret');
                return retAttr !== null && retAttr !== '';
            }

            // Special handling for duration (comparison operators)
            if (field === 'duration') {
                const durationAttr = line.getAttribute('data-duration');
                if (!durationAttr) return false;
                const dur = parseFloat(durationAttr);
                const val = parseFloat(value);
                if (isNaN(val)) return false;
                switch (op) {
                    case '>': return dur > val;
                    case '>=': return dur >= val;
                    case '<': return dur < val;
                    case '<=': return dur <= val;
                    case '=': return dur === val;
                    default: return dur === val;
                }
            }

            // Special handling for preempt (numeric comparison)
            if (field === 'preempt' && op !== '=') {
                const pAttr = line.getAttribute('data-preempt');
                if (!pAttr) return false;
                const pVal = parseInt(pAttr);
                const cVal = parseInt(value);
                if (isNaN(cVal)) return false;
                switch (op) {
                    case '>': return pVal > cVal;
                    case '>=': return pVal >= cVal;
                    case '<': return pVal < cVal;
                    case '<=': return pVal <= cVal;
                    default: return pVal === cVal;
                }
            }

            // Default: regex match (op '=')
            const attrVal = line.getAttribute(attrName);
            if (attrVal === null || attrVal === '') return false;

            // Special handling for ret: match against data-ret numeric value or error name in raw line
            if (field === 'ret') {
                // Try matching numeric value
                try {
                    const regex = new RegExp(value, 'i');
                    if (regex.test(attrVal)) return true;
                    // Also try matching error code name from raw line
                    const rawLine = line.querySelector('.line-content')?.textContent || '';
                    const retMatch = rawLine.match(/ret\s*=\s*([0-9a-fA-FxX-]+)/);
                    if (retMatch && regex.test(retMatch[1])) return true;
                } catch (e) {
                    return attrVal === value;
                }
                return false;
            }

            // For params, also check raw line if no data-params
            if (field === 'params' && !attrVal) {
                const rawLine = line.querySelector('.line-content')?.textContent || '';
                const paramsMatch = rawLine.match(/\(([^)]*)\)/);
                if (paramsMatch) {
                    try {
                        return new RegExp(value, 'i').test(paramsMatch[1]);
                    } catch (e) {
                        return paramsMatch[1].includes(value);
                    }
                }
                return false;
            }

            // General regex match
            try {
                return new RegExp(value, 'i').test(attrVal);
            } catch (e) {
                return attrVal === value;
            }
        }

        // Evaluate AST against a line element
        function evalExpr(line, ast, graphSets = _graphFoldIds) {
            if (!ast) return true;
            switch (ast.type) {
                case 'AND': return evalExpr(line, ast.left, graphSets) && evalExpr(line, ast.right, graphSets);
                case 'OR': return evalExpr(line, ast.left, graphSets) || evalExpr(line, ast.right, graphSets);
                case 'NOT': return !evalExpr(line, ast.operand, graphSets);
                case 'COND': return evalCondition(line, ast, graphSets);
                default: return true;
            }
        }

        // Serialize AST back to text
        function astToText(ast) {
            if (!ast) return '';
            switch (ast.type) {
                case 'AND': {
                    const l = astToText(ast.left);
                    const r = astToText(ast.right);
                    return l && r ? l + ' AND ' + r : l || r;
                }
                case 'OR': {
                    const l = astToText(ast.left);
                    const r = astToText(ast.right);
                    return l && r ? l + ' OR ' + r : l || r;
                }
                case 'NOT':
                    return 'NOT ' + astToText(ast.operand);
                case 'COND':
                    return ast.field + ':' + (ast.op !== '=' ? ast.op : '') + ast.value;
                default:
                    return '';
            }
        }

        // ====== Filter Tags Management ======
        function updateFilterTags(text) {
            const tagsDiv = document.getElementById('filterTags');
            if (!tagsDiv) return;
            tagsDiv.innerHTML = '';

            const { ast } = parseFilterExpr(text);
            if (!ast) return;

            // Flatten top-level AND/OR into tag list
            function flattenExpr(node, items, logicType) {
                if (!node) return;
                if (node.type === logicType) {
                    flattenExpr(node.left, items, logicType);
                    flattenExpr(node.right, items, logicType);
                } else {
                    items.push(node);
                }
            }

            // Determine top-level logic
            let topLogic = 'AND';
            let parts = [];
            if (ast.type === 'AND' || ast.type === 'OR') {
                topLogic = ast.type;
                flattenExpr(ast, parts, topLogic);
            } else {
                parts = [ast];
            }

            parts.forEach((part, idx) => {
                if (idx > 0) {
                    const logicTag = document.createElement('span');
                    logicTag.className = 'filter-tag-logic';
                    logicTag.textContent = topLogic;
                    logicTag.title = 'Click to toggle AND/OR';
                    logicTag.addEventListener('click', () => {
                        // Toggle logic in text
                        const input = document.getElementById('filterInput');
                        if (input) {
                            const newLogic = topLogic === 'AND' ? 'OR' : 'AND';
                            input.value = input.value.replace(new RegExp('\\b' + topLogic + '\\b', 'gi'), newLogic);
                            updateFilterTags(input.value);
                            applyFilter();
                        }
                    });
                    tagsDiv.appendChild(logicTag);
                }

                const tag = document.createElement('span');
                tag.className = 'filter-tag';
                const tagText = astToText(part);
                tag.innerHTML = tagText + ' <span class="tag-remove" title="Remove">&times;</span>';
                tag.querySelector('.tag-remove').addEventListener('click', () => {
                    // Remove this condition from the input
                    const input = document.getElementById('filterInput');
                    if (input) {
                        // Remove the condition text and surrounding logic operator
                        let newText = input.value;
                        // Try to remove "COND AND " or " AND COND" or " OR COND" or "COND OR "
                        const escaped = tagText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                        newText = newText.replace(new RegExp('\\s*' + topLogic + '\\s+' + escaped, 'i'), '');
                        newText = newText.replace(new RegExp(escaped + '\\s+' + topLogic + '\\s*', 'i'), '');
                        newText = newText.replace(new RegExp(escaped, 'i'), '');
                        newText = newText.replace(/^\s*(AND|OR)\s+/i, '').replace(/\s+(AND|OR)\s*$/i, '').trim();
                        input.value = newText;
                        updateFilterTags(newText);
                        applyFilter();
                    }
                });
                tagsDiv.appendChild(tag);
            });
        }

        // ====== Graph/Notrace Precomputation ======

        // Module-level cache for graph/notrace fold-id sets
        let _graphFoldIds = {};

        // Collect all graph:/notrace: conditions from AST
        function collectGraphConditions(ast) {
            if (!ast) return [];
            if (ast.type === 'COND' && (ast.field === 'graph' || ast.field === 'notrace')) return [ast];
            const results = [];
            if (ast.left) results.push(...collectGraphConditions(ast.left));
            if (ast.right) results.push(...collectGraphConditions(ast.right));
            if (ast.operand) results.push(...collectGraphConditions(ast.operand));
            return results;
        }

        // Build funcName→[foldId] map and resolve graph/notrace targets
        function precomputeGraphSets(graphConds, allLines) {
            const steps = fgGraphSetsSteps(graphConds, allLines);
            let result;
            do { result = steps.next(); } while (!result.done);
            return result.value;
        }

        function* fgGraphSetsSteps(graphConds, allLines) {
            const result = {};
            if (graphConds.length === 0) return result;

            // Build entry map: funcName → [foldId, ...]
            const entryMap = new Map();
            let scanned = 0;
            for (const line of allLines) {
                if (line.getAttribute('data-fold-type') === 'entry') {
                    const func = line.getAttribute('data-func');
                    const foldId = line.getAttribute('data-fold-id');
                    if (func && foldId) {
                        let arr = entryMap.get(func);
                        if (!arr) { arr = []; entryMap.set(func, arr); }
                        arr.push(foldId);
                    }
                }
                if (++scanned % 2048 === 0) yield 'Preparing full-trace call-tree filter…';
            }

            // For each condition, find matching fold-ids
            graphConds.forEach(cond => {
                const key = cond.field + ':' + cond.value;
                if (result[key]) return; // already computed
                const matchedIds = new Set();
                let regex;
                try { regex = new RegExp(cond.value, 'i'); } catch(e) { regex = null; }
                entryMap.forEach((ids, func) => {
                    const match = regex ? regex.test(func) : func.includes(cond.value);
                    if (match) ids.forEach(id => matchedIds.add(id));
                });
                result[key] = matchedIds;
            });

            return result;
        }

        // ====== Main Filter Functions ======

        function fgSetFilterProgress(status) {
            window.fgFilterProgress = status;
            if (typeof window.fgOnFilterProgress === 'function') window.fgOnFilterProgress();
        }

        function fgCancelPendingFilter(notify = true) {
            fgFilterGeneration++;
            if (fgFilterController) fgFilterController.abort();
            fgFilterController = null;
            if (window.fgFilterProgress) {
                window.fgFilterProgress = null;
                if (notify && typeof window.fgOnFilterProgress === 'function') window.fgOnFilterProgress();
            }
        }

        function fgShowFilterCount(visible, total) {
            const container = document.getElementById('visibleLinesContainer');
            const count = document.getElementById('summaryVisibleLines');
            if (container && count) {
                container.style.display = visible < total ? 'inline' : 'none';
                count.textContent = visible;
            }
        }

        async function fgApplyIndexedFilter(text, generation) {
            if (!parseFilterExpr(text).ast) {
                fgNotifyFilter('', null);
                fgShowFilterCount(total_lines, total_lines);
                return true;
            }
            const controller = fgFilterController = new AbortController();
            const progress = message => {
                if (generation === fgFilterGeneration) fgSetFilterProgress({ text, message, error: false });
            };
            progress('Loading full-trace filter index…');
            try {
                const data = await fgData.load('fgTextIndex', { signal: controller.signal });
                const result = await fgIndexedFilter(data, text, controller.signal, progress);
                if (generation !== fgFilterGeneration) return null;
                total_lines = result.total;
                window.fgFilterProgress = null;
                fgNotifyFilter(result.text, result.lines);
                fgShowFilterCount(result.visible, result.total);
                return true;
            } catch (error) {
                if (error.name === 'AbortError' || generation !== fgFilterGeneration) return null;
                fgData.release('fgTextIndex');
                fgSetFilterProgress({ text, error: true,
                    message: 'Filter not applied: ' + error.message + '. Previous result retained.' });
                return false;
            } finally {
                if (fgFilterController === controller) fgFilterController = null;
            }
        }

        function applyFilter() {
            fgCancelPendingFilter(false);
            const generation = fgFilterGeneration;
            const filterInput = document.getElementById('filterInput');
            const text = filterInput ? filterInput.value.trim() : '';
            if (fgTextDeferred() && document.getElementById('content').hasAttribute('data-fg-text-index')) {
                updateFilterTags(text);
                return fgApplyIndexedFilter(text, generation);
            }
            if (fgTextDeferred()) return Promise.resolve(fgEnsureText()).then(api => {
                if (generation !== fgFilterGeneration) return null;
                if (!api) return false;
                applyFilter();
                return true;
            });

            // Update tags display
            updateFilterTags(text);

            // Parse the expression
            const { ast, sortDir } = parseFilterExpr(text);
            const hasFilter = ast !== null;

            // Get all lines
            const allLineElems = document.querySelectorAll('.line-container');
            let visibleCount = 0;

            // Precompute graph/notrace fold-id sets
            if (hasFilter) {
                const graphConds = collectGraphConditions(ast);
                _graphFoldIds = precomputeGraphSets(graphConds, allLineElems);
            } else {
                _graphFoldIds = {};
            }

            const passLines = hasFilter ? new Set() : null;
            // Single-pass: filter lines + collapse expanded content
            allLineElems.forEach(line => {
                let show = true;

                if (hasFilter) {
                    const rawLine = line.querySelector('.line-content')?.textContent || '';
                    const trimmed = rawLine.trim();
                    if (trimmed === '' || /^[-]+$/.test(trimmed)) {
                        show = false;
                    }
                }

                if (show && hasFilter) {
                    show = evalExpr(line, ast);
                }

                line.style.display = show ? '' : 'none';
                if (show) visibleCount++;
                if (show && passLines) passLines.add(+line.getAttribute('data-line-number'));

                // Collapse expanded content for hidden or all filtered lines (single pass)
                if (hasFilter && line.classList.contains('expandable')) {
                    const nextSib = line.nextElementSibling;
                    const content = nextSib && nextSib.classList.contains('expanded-content') ? nextSib : null;
                    if (content && content.style.display === 'block') {
                        content.style.display = 'none';
                        const btn = line.querySelector('.expand-btn');
                        if (btn) btn.textContent = '+';
                        line.classList.remove('selected');
                    }
                }
            });

            // Apply duration sort if requested
            if (sortDir && visibleCount > 0) {
                const container = document.getElementById('content');
                if (container) {
                    const blocks = fgBlocks(container);
                    blocks.sort((left, right) => {
                        const leftHidden = left[0].style.display === 'none', rightHidden = right[0].style.display === 'none';
                        if (leftHidden !== rightHidden) return leftHidden ? 1 : -1;
                        if (leftHidden) return 0;
                        const leftDuration = parseFloat(left[0].dataset.sortDuration || left[0].dataset.duration || '0');
                        const rightDuration = parseFloat(right[0].dataset.sortDuration || right[0].dataset.duration || '0');
                        return sortDir === 'desc' ? rightDuration - leftDuration : leftDuration - rightDuration;
                    });
                    fgReorder(container, 'time', blocks);
                    const button = document.getElementById('textOrderBtn');
                    if (button) button.textContent = 'Order: duration ' + (sortDir === 'desc' ? '↓' : '↑');
                }
            }

            updateExpandableLines();
            fgNotifyFilter(hasFilter ? text : '', passLines);

            // Update visible lines count
            fgShowFilterCount(visibleCount, total_lines);
        }

        // Clear filter
        function clearFilter() {
            fgCancelPendingFilter(false);
            const filterInput = document.getElementById('filterInput');
            if (filterInput) filterInput.value = '';

            const tagsDiv = document.getElementById('filterTags');
            if (tagsDiv) tagsDiv.innerHTML = '';

            // Close any open dropdowns
            document.querySelectorAll('.field-dropdown').forEach(d => d.remove());
            document.querySelectorAll('.field-btn.active').forEach(b => b.classList.remove('active'));

            const allLineElems = document.querySelectorAll('.line-container');
            allLineElems.forEach(line => {
                line.style.display = '';
                const lineId = line.getAttribute('data-line-id');
                if (lineId) {
                    const content = document.getElementById(lineId + '_content');
                    const btn = line.querySelector('.expand-btn');
                    if (content && btn) {
                        const isExpanded = localStorage.getItem(`expanded_${lineId}`) === 'true';
                        if (isExpanded) {
                            fgFill(content); content.style.display = 'block';
                            btn.textContent = '-';
                            line.classList.add('selected');
                        } else {
                            content.style.display = 'none';
                            btn.textContent = '+';
                            line.classList.remove('selected');
                        }
                    }
                }
            });

            updateExpandableLines();
            fgNotifyFilter('', null);

            const visibleLinesContainer = document.getElementById('visibleLinesContainer');
            if (visibleLinesContainer) visibleLinesContainer.style.display = 'none';
        }

        // 更新展开行列表（只包含可见的）
        function updateExpandableLines() {
            textGuideRevision++;
            // 重新获取所有行（包括隐藏的）
            allLines = Array.from(document.querySelectorAll('.line-container'));
            expandableLines = Array.from(document.querySelectorAll('.line-container.expandable'))
                .filter(line => line.style.display !== 'none');
            expandableLineIndices = [];
            allLines.forEach((line, index) => {
                if (line.classList.contains('expandable') && line.style.display !== 'none') {
                    expandableLineIndices.push(index);
                }
            });
            document.getElementById('content')?.dispatchEvent(new Event('fg-guides-change'));
        }

        // 自动补全功能
        // Initialize unified filter field buttons and input
        function initFilterFields() {
            const filterInput = document.getElementById('filterInput');
            if (!filterInput) return;

            // Enter key triggers filter
            filterInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    applyFilter();
                }
            });

            // Helper: position dropdown near a button using fixed positioning, clamped to viewport
            function positionDropdown(dropdown, btn) {
                const rect = btn.getBoundingClientRect();
                // Temporarily show off-screen to measure dimensions
                dropdown.style.visibility = 'hidden';
                dropdown.style.left = '0px';
                dropdown.style.top = '0px';
                document.body.appendChild(dropdown);
                const dw = dropdown.offsetWidth;
                const dh = dropdown.offsetHeight;
                dropdown.style.visibility = '';

                const vw = window.innerWidth;
                const vh = window.innerHeight;
                const margin = 4;

                // Horizontal: align left edge to button, clamp to viewport
                let left = rect.left;
                if (left + dw > vw - margin) {
                    left = vw - dw - margin;
                }
                if (left < margin) left = margin;

                // Vertical: prefer above button, fall back to below if no room
                let top = rect.top - dh - margin;
                if (top < margin) {
                    top = rect.bottom + margin;
                }
                // Clamp bottom edge
                if (top + dh > vh - margin) {
                    top = vh - dh - margin;
                }
                if (top < margin) top = margin;

                dropdown.style.left = left + 'px';
                dropdown.style.top = top + 'px';
            }

            // Helper: close all field dropdowns
            function closeFieldDropdowns() {
                document.querySelectorAll('.field-dropdown').forEach(d => d.remove());
                document.querySelectorAll('.field-btn.active').forEach(b => b.classList.remove('active'));
            }

            // Field button click handlers
            document.querySelectorAll('.field-btn').forEach(btn => {
                btn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    const field = this.dataset.field;
                    const values = this.dataset.values ? this.dataset.values.split(',').filter(Boolean) : [];
                    const hints = {};
                    if (this.dataset.hints) {
                        this.dataset.hints.split(',').forEach(h => {
                            const [k, v] = h.split('=');
                            if (k && v) hints[k] = v;
                        });
                    }

                    // Close any existing dropdown
                    closeFieldDropdowns();

                    // Toggle active state
                    this.classList.add('active');

                    // Create dropdown (will be appended to document.body, not inside button)
                    const dropdown = document.createElement('div');
                    dropdown.className = 'field-dropdown';

                    if (field === 'params' || field === 'duration' || values.length === 0) {
                        // Text input for free-form fields
                        const inputDiv = document.createElement('div');
                        inputDiv.className = 'field-dropdown-input';
                        const placeholder = field === 'duration' ? 'e.g. >10 or <5' : 'regex pattern';
                        inputDiv.innerHTML = '<input type="text" placeholder="' + placeholder + '">';
                        dropdown.appendChild(inputDiv);

                        const actionsDiv = document.createElement('div');
                        actionsDiv.className = 'field-dropdown-actions';
                        actionsDiv.innerHTML = '<button class="cancel-btn">Cancel</button><button>Add</button>';
                        dropdown.appendChild(actionsDiv);

                        actionsDiv.querySelector('.cancel-btn').addEventListener('click', () => {
                            dropdown.remove();
                            btn.classList.remove('active');
                        });

                        actionsDiv.querySelector('button:last-child').addEventListener('click', () => {
                            const val = inputDiv.querySelector('input').value.trim();
                            if (val) {
                                const op = val.match(/^(>=|<=|>|<)/) ? val.match(/^(>=|<=|>|<)/)[1] : '';
                                const numVal = op ? val.slice(op.length) : val;
                                appendToFilter(field + ':' + (op || '') + numVal);
                            }
                            dropdown.remove();
                            btn.classList.remove('active');
                        });

                        // Enter key in text input
                        inputDiv.querySelector('input').addEventListener('keydown', (ev) => {
                            if (ev.key === 'Enter') {
                                ev.preventDefault();
                                actionsDiv.querySelector('button:last-child').click();
                            } else if (ev.key === 'Escape') {
                                dropdown.remove();
                                btn.classList.remove('active');
                            }
                        });

                        positionDropdown(dropdown, this);
                        setTimeout(() => inputDiv.querySelector('input').focus(), 50);
                    } else {
                        // Checkbox multi-select for enumerable fields
                        values.forEach(val => {
                            const item = document.createElement('label');
                            item.className = 'field-dropdown-item';
                            const hintText = hints[val] ? '<span class="hint-text">' + hints[val] + '</span>' : '';
                            const displayVal = val.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                            item.innerHTML = '<input type="checkbox" value="' + displayVal + '"> <span>' + displayVal + '</span>' + hintText;
                            dropdown.appendChild(item);
                        });

                        const actionsDiv = document.createElement('div');
                        actionsDiv.className = 'field-dropdown-actions';
                        actionsDiv.innerHTML = '<button class="cancel-btn">Cancel</button><button>OK</button>';
                        dropdown.appendChild(actionsDiv);

                        actionsDiv.querySelector('.cancel-btn').addEventListener('click', () => {
                            dropdown.remove();
                            btn.classList.remove('active');
                        });

                        actionsDiv.querySelector('button:last-child').addEventListener('click', () => {
                            const checked = Array.from(dropdown.querySelectorAll('input[type="checkbox"]:checked'))
                                .map(cb => cb.value);
                            if (checked.length > 0) {
                                if (checked.length === 1) {
                                    appendToFilter(field + ':' + checked[0]);
                                } else {
                                    const expr = '(' + checked.map(v => field + ':' + v).join(' OR ') + ')';
                                    appendToFilter(expr);
                                }
                            }
                            dropdown.remove();
                            btn.classList.remove('active');
                        });

                        positionDropdown(dropdown, this);
                    }
                });
            });

            // Helper: append condition to filter input
            function appendToFilter(expr) {
                const input = document.getElementById('filterInput');
                if (!input) return;
                const current = input.value.trim();
                if (current) {
                    input.value = current + ' AND ' + expr;
                } else {
                    input.value = expr;
                }
                updateFilterTags(input.value);
                applyFilter();
            }

            // Global click to close dropdowns
            document.addEventListener('click', function(e) {
                if (!e.target.closest('.field-btn') && !e.target.closest('.field-dropdown')) {
                    document.querySelectorAll('.field-dropdown').forEach(d => d.remove());
                    document.querySelectorAll('.field-btn.active').forEach(b => b.classList.remove('active'));
                }
            });
        }

        // 设置键盘选中的行
        function setKeyboardSelectedLine(index, scrollIntoView = true) {
            // 清除之前的选择
            if (keyboardSelectedLine) {
                keyboardSelectedLine.classList.remove('keyboard-selected');
            }

            if (index < 0) index = 0;
            if (index >= allLines.length) index = allLines.length - 1;

            keyboardSelectedIndex = index;
            keyboardSelectedLine = allLines[index];

            // 添加新的选择样式
            if (keyboardSelectedLine) {
                keyboardSelectedLine.classList.add('keyboard-selected');

                // 移动焦点到该行，这样event.target会更新
                // 这样Enter键就能正确处理
                if (typeof fgCurrentView !== 'function' || fgCurrentView() === 'text') keyboardSelectedLine.focus();

                // 只有在需要滚动时才滚动
                if (scrollIntoView) {
                    // 获取当前行的位置
                    const rect = keyboardSelectedLine.getBoundingClientRect();
                    const windowHeight = window.innerHeight;

                    // 计算行在视口中的位置
                    const lineTop = rect.top;
                    const lineBottom = rect.bottom;
                    const lineHeight = rect.height;

                    // 判断是否需要滚动到中间
                    // 如果当前行接近视口底部（距离底部小于2行高度），则滚动到中间
                    // 如果当前行接近视口顶部（距离顶部小于1行高度），也滚动到中间
                    const nearBottom = windowHeight - lineBottom < lineHeight * 2;
                    const nearTop = lineTop < lineHeight;

                    if (nearBottom || nearTop || lineTop < 0 || lineBottom > windowHeight) {
                        // 如果接近边界或不在视口中，滚动到中间
                        keyboardSelectedLine.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    } else {
                        // 否则，使用nearest，尽可能少滚动
                        keyboardSelectedLine.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    }
                }
            }
        }
        
        // 导航到下一个可展开行
        function navigateToExpandableLine(direction) {
            if (expandableLineIndices.length === 0) {
                return; // 没有可展开行
            }
            
            let currentExpandableIndex = -1;
            
            // 找到当前行在可展开行列表中的位置
            for (let i = 0; i < expandableLineIndices.length; i++) {
                if (expandableLineIndices[i] === keyboardSelectedIndex) {
                    currentExpandableIndex = i;
                    break;
                }
            }
            
            // 如果当前行不是可展开行，找到最近的可展开行
            if (currentExpandableIndex === -1) {
                if (direction > 0) {
                    // 向下查找
                    for (let i = 0; i < expandableLineIndices.length; i++) {
                        if (expandableLineIndices[i] > keyboardSelectedIndex) {
                            currentExpandableIndex = i;
                            break;
                        }
                    }
                    // 如果没找到，从第一个开始
                    if (currentExpandableIndex === -1) {
                        currentExpandableIndex = 0;
                    }
                } else {
                    // 向上查找
                    for (let i = expandableLineIndices.length - 1; i >= 0; i--) {
                        if (expandableLineIndices[i] < keyboardSelectedIndex) {
                            currentExpandableIndex = i;
                            break;
                        }
                    }
                    // 如果没找到，从最后一个开始
                    if (currentExpandableIndex === -1) {
                        currentExpandableIndex = expandableLineIndices.length - 1;
                    }
                }
            }
            
            // 计算下一个索引
            let nextIndex = currentExpandableIndex + direction;
            
            // 处理循环
            if (nextIndex < 0) {
                nextIndex = expandableLineIndices.length - 1; // 循环到最后一个
            } else if (nextIndex >= expandableLineIndices.length) {
                nextIndex = 0; // 循环到第一个
            }
            
            // 设置新的选中行
            setKeyboardSelectedLine(expandableLineIndices[nextIndex], true);
        }
        
        // 处理行点击事件 - 现在只处理单击
        function handleLineClick(event, lineId) {
            if (event.target.closest('.fold-icon, .fold-brace')) return;
            // 记录点击前的滚动位置
            scrollPositionBeforeClick = {
                x: window.scrollX,
                y: window.scrollY
            };

            // 阻止默认行为，防止可能的滚动
            event.preventDefault();

            // 检查是否点击的是折叠标记
            const foldMarker = event.target.closest('.fold-marker');
            if (foldMarker) {
                // 处理折叠/展开
                const lineContainer = event.target.closest('.line-container');
                if (lineContainer) {
                    const foldId = lineContainer.getAttribute('data-fold-id');
                    const foldType = lineContainer.getAttribute('data-fold-type');
                    if (foldId && foldType) {
                        toggleFold(foldId, foldType);
                    }
                }
                return;
            }

            // 检查是否正在进行文本选择
            const selection = window.getSelection();
            const hasSelection = selection.toString().trim().length > 0;

            // 检查鼠标移动距离，判断是否是拖动选择
            const mouseUpInfo = { x: event.clientX, y: event.clientY, time: Date.now() };
            const distance = Math.sqrt(
                Math.pow(mouseUpInfo.x - mouseDownInfo.x, 2) +
                Math.pow(mouseUpInfo.y - mouseDownInfo.y, 2)
            );
            const timeDiff = mouseUpInfo.time - mouseDownInfo.time;

            // 如果存在文本选择，并且是拖动操作，则不触发展开/收起
            if (hasSelection && (distance > 5 || timeDiff > 200)) {
                // 高亮被选中的行
                highlightSelectedLines();
                return;
            }

            // 清除之前的点击定时器
            if (clickTimer) {
                clearTimeout(clickTimer);
                clickTimer = null;
            }

            // 如果是双击事件的一部分，则不执行单击
            if (doubleClickTimer) {
                clearTimeout(doubleClickTimer);
                doubleClickTimer = null;
                return;
            }

            // 设置键盘选中行，但不滚动
            // 根据 data-line-id 查找对应的行元素
            let clickedLine = null;
            const allLineElements = Array.from(document.querySelectorAll('.line-container'));
            for (let line of allLineElements) {
                if (line.getAttribute('data-line-id') === lineId) {
                    clickedLine = line;
                    break;
                }
            }

            if (clickedLine) {
                const lineIndex = allLineElements.findIndex(line => line === clickedLine);
                if (lineIndex >= 0) {
                    setKeyboardSelectedLine(lineIndex, false);
                }
            }

            // 设置延迟执行单击事件，以便在双击时可以取消
            clickTimer = setTimeout(() => {
                toggleExpand(lineId, event);
                clickTimer = null;
            }, 200);
        }
        
        // 处理双击事件
        function handleDoubleClick(event, lineId) {
            // 阻止事件冒泡
            event.stopPropagation();
            if (event.target.closest('.fold-icon, .fold-brace')) return;
            
            // 清除单击定时器
            if (clickTimer) {
                clearTimeout(clickTimer);
                clickTimer = null;
            }
            
            // 检查是否点击的是.line-content
            const lineContent = event.target.closest('.line-content');
            if (!lineContent) return;
            
            // 检查是否在可展开行内
            const lineContainer = event.target.closest('.line-container');
            if (!lineContainer) return;
            
            const now = Date.now();
            const timeDiff = now - lastClickTime;
            const target = event.target;
            
            if (timeDiff < 500 && target === lastClickTarget) {
                // 快速双击相同目标
                doubleClickState = (doubleClickState + 1) % 3;
                
                if (doubleClickState === 1) {
                    // 第一次双击：选中单词（浏览器默认行为）
                    // 我们让浏览器处理默认的双击选中单词行为
                    
                    // 设置定时器，在一定时间后重置状态
                    if (doubleClickTimer) {
                        clearTimeout(doubleClickTimer);
                    }
                    doubleClickTimer = setTimeout(() => {
                        doubleClickState = 0;
                        doubleClickTimer = null;
                    }, 500);
                    
                    return;
                } else if (doubleClickState === 2) {
                    // 第二次双击：选中整行文本
                    event.preventDefault();
                    
                    // 获取行内容
                    const lineText = lineContent.textContent;
                    
                    // 创建选择范围
                    const range = document.createRange();
                    range.selectNodeContents(lineContent);
                    
                    // 清除当前选择
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(range);
                    
                    // 高亮被选中的行
                    highlightSelectedLines();
                    
                    // 重置双击状态
                    doubleClickState = 0;
                    if (doubleClickTimer) {
                        clearTimeout(doubleClickTimer);
                        doubleClickTimer = null;
                    }
                }
            } else {
                // 新的双击序列
                doubleClickState = 1;
                
                // 第一次双击：让浏览器默认选中单词
                // 我们只需要记录时间和目标
                
                // 设置定时器，在一定时间后重置状态
                if (doubleClickTimer) {
                    clearTimeout(doubleClickTimer);
                }
                doubleClickTimer = setTimeout(() => {
                    doubleClickState = 0;
                    doubleClickTimer = null;
                }, 500);
            }
            
            lastClickTime = now;
            lastClickTarget = target;
        }
        
        // 切换展开/收起状态 - 只影响当前行，不滚动
        // 切换折叠状态
        // Core fold/unfold logic — does NOT write localStorage (P3 optimization)
        function _setFolded(foldId, shouldFold, restore) {
            const isFolded = foldedBlocks[foldId] === true;
            if (shouldFold === isFolded && !restore) return; // no-op
            textGuideRevision++;

            // P1: single DOM query for all related lines
            const lines = document.querySelectorAll('[data-fold-id="' + foldId + '"], [data-fold-ancestors~="' + foldId + '"]');
            // Find entry element from the query results (avoid extra querySelector)
            let entryEl = null;
            for (let i = 0; i < lines.length; i++) {
                if (lines[i].getAttribute('data-fold-type') === 'entry' && lines[i].getAttribute('data-fold-id') === foldId) {
                    entryEl = lines[i]; break;
                }
            }

            if (shouldFold) {
                // Collapse
                foldedBlocks[foldId] = true;
                if (entryEl) {
                    entryEl.classList.add('folded');
                    const icon = entryEl.querySelector('.fold-icon');
                    if (icon) icon.classList.remove('expanded');
                }
                addFoldSummary(foldId);
            } else {
                // Expand
                delete foldedBlocks[foldId];
                removeFoldSummary(foldId);
                if (entryEl) {
                    entryEl.classList.remove('folded');
                    const icon = entryEl.querySelector('.fold-icon');
                    if (icon) icon.classList.add('expanded');
                }
            }
            if (entryEl) {
                const action = shouldFold ? 'Expand' : 'Collapse';
                const name = entryEl.getAttribute('data-func') || 'function';
                entryEl.querySelectorAll('.fold-icon, .fold-brace').forEach(control => {
                    control.setAttribute('aria-expanded', String(!shouldFold));
                    control.setAttribute('aria-label', action + ' call to ' + name);
                    control.title = action + ' call; Shift-click: entire subtree';
                });
            }

            lines.forEach(line => {
                const ancestors = (line.getAttribute('data-fold-ancestors') || '').split(' ');
                const ownFold = line.getAttribute('data-fold-id');
                const hidden = (line.getAttribute('data-fold-type') !== 'entry' && foldedBlocks[ownFold] === true) ||
                    ancestors.some(ancestor => foldedBlocks[ancestor] === true);
                line.style.display = hidden ? 'none' : '';
                const lineId = line.getAttribute('data-line-id');
                const content = document.getElementById(lineId + '_content');
                if (content) {
                    if (!hidden && expandedLines[lineId]) {
                        fgFill(content);
                        content.style.display = 'block';
                    } else {
                        content.style.display = 'none';
                    }
                }
            });
            entryEl?.closest('#content')?.dispatchEvent(new Event('fg-guides-change'));
        }

        // Public API: toggle a single fold block + save state
        function toggleFold(foldId, foldType) {
            const shouldFold = !foldedBlocks[foldId];
            _setFolded(foldId, shouldFold);
            lsSet('foldedBlocks', JSON.stringify(foldedBlocks));
        }

        function toggleExpand(lineId, event) {
            // 保存当前滚动位置
            const scrollX = window.scrollX;
            const scrollY = window.scrollY;

            const content = document.getElementById(lineId + '_content');
            const btn = document.querySelector('[data-line-id="' + lineId + '"] .expand-btn');
            const container = document.querySelector('[data-line-id="' + lineId + '"]');
            if (container.style.display === 'none') return;

            // 使用getComputedStyle获取计算后的显示状态
            const computedStyle = window.getComputedStyle(content);
            const isCurrentlyVisible = computedStyle.display === 'block';

            if (isCurrentlyVisible) {
                // 收起当前行
                content.style.display = 'none';
                if (btn) btn.textContent = '+';
                container.classList.remove('selected');
                saveExpandedState(lineId, false);
            } else {
                // 展开当前行
                fgFill(content); content.style.display = 'block';
                if (btn) btn.textContent = '-';
                container.classList.add('selected');
                saveExpandedState(lineId, true);
            }

            // 恢复滚动位置
            setTimeout(() => {
                window.scrollTo(scrollX, scrollY);
            }, 0);
        }
        
        // 切换主题
        function toggleTheme() {
            currentTheme = currentTheme === 'light' ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', currentTheme);
            localStorage.setItem('theme', currentTheme);
        }
        
        // 切换信息面板展开/折叠
        function toggleInfoPanel() {
            const panel = document.getElementById('infoPanel');
            const isExpanded = panel.classList.contains('show');
            if (isExpanded) {
                panel.classList.remove('show');
                lsSet('infoPanelExpanded', 'false');
            } else {
                panel.classList.add('show');
                lsSet('infoPanelExpanded', 'true');
            }
        }

        // 初始化信息面板状态
        function initInfoPanel() {
            const expanded = lsGet('infoPanelExpanded') === 'true';
            if (expanded) {
                const panel = document.getElementById('infoPanel');
                panel.classList.add('show');
            }
        }

        // 切换浮动控件（显示/隐藏），并保存状态到 localStorage
        function toggleFloatingControls() {
            const hidden = document.body.classList.toggle('hide-floating-controls');
            const icon = document.getElementById('floatingToggleIcon');
            if (hidden) {
                icon.textContent = '✕';
                lsSet('floatingControlsHidden', 'true');
            } else {
                icon.textContent = '☰';
                lsSet('floatingControlsHidden', 'false');
            }
        }

        // 初始化浮动控件的显示状态（从 localStorage 读取）
        function initFloatingControls() {
            const hidden = lsGet('floatingControlsHidden') === 'true';
            const icon = document.getElementById('floatingToggleIcon');
            if (hidden) {
                document.body.classList.add('hide-floating-controls');
                if (icon) icon.textContent = '✕';
            } else {
                document.body.classList.remove('hide-floating-controls');
                if (icon) icon.textContent = '☰';
            }
        }

        // 拖动相关的全局变量
        let isDraggingToggle = false;
        let dragStartX = 0;
        let dragStartY = 0;
        let dragStartLeft = 0;
        let dragStartTop = 0;
        let wasDragged = false;  // 标记本次操作是否有移动（拖动而非点击）

        // 点击处理器：检查是否为拖动，只在非拖动时执行 toggle
        function handleFloatingToggleClick(event) {
            // 如果拖动过程中产生了移动，则忽略本次点击
            if (wasDragged) {
                wasDragged = false;  // 重置标志
                return;
            }
            toggleFloatingControls();
        }

        // 初始化悬浮按钮的拖动功能
        function initFloatingToggleDrag() {
            const toggle = document.getElementById('floatingToggle');
            if (!toggle) return;

            // 从 localStorage 恢复之前保存的位置
            const savedPos = lsGet('floatingTogglePos');
            if (savedPos) {
                try {
                    const pos = JSON.parse(savedPos);
                    toggle.style.position = 'fixed';
                    toggle.style.top = pos.top + 'px';
                    toggle.style.left = pos.left + 'px';
                    toggle.style.right = 'auto';
                    toggle.style.bottom = 'auto';
                    toggle.style.transform = 'none';
                } catch (e) {
                    console.warn('Failed to restore toggle position:', e);
                }
            }

            // 鼠标按下：开始拖动
            toggle.addEventListener('mousedown', function(e) {
                e.preventDefault();
                isDraggingToggle = true;
                wasDragged = false;  // 初始化拖动标志
                toggle.classList.add('dragging');
                dragStartX = e.clientX;
                dragStartY = e.clientY;
                dragStartLeft = toggle.offsetLeft;
                dragStartTop = toggle.offsetTop;
                e.stopPropagation();
            });

            // 鼠标移动：更新位置
            document.addEventListener('mousemove', function(e) {
                if (!isDraggingToggle) return;
                
                const deltaX = e.clientX - dragStartX;
                const deltaY = e.clientY - dragStartY;
                
                // 移动距离超过 3px 时才认为是拖动（避免误触发）
                if (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3) {
                    wasDragged = true;
                }
                
                const newLeft = dragStartLeft + deltaX;
                const newTop = dragStartTop + deltaY;
                
                // 边界约束：防止拖出窗口
                const maxLeft = window.innerWidth - toggle.offsetWidth;
                const maxTop = window.innerHeight - toggle.offsetHeight;
                
                toggle.style.left = Math.max(0, Math.min(newLeft, maxLeft)) + 'px';
                toggle.style.top = Math.max(0, Math.min(newTop, maxTop)) + 'px';
            });

            // 鼠标释放：停止拖动，保存位置
            document.addEventListener('mouseup', function(e) {
                if (!isDraggingToggle) return;
                
                isDraggingToggle = false;
                toggle.classList.remove('dragging');
                
                // 保存位置到 localStorage
                const pos = {
                    left: toggle.offsetLeft,
                    top: toggle.offsetTop
                };
                lsSet('floatingTogglePos', JSON.stringify(pos));
            });

            // 防止文本选中干扰拖动
            toggle.addEventListener('selectstart', function(e) {
                e.preventDefault();
            });
        }
        
        // 高亮被选中的行
        function highlightSelectedLines() {
            // 清除之前的高亮
            if (highlightedLine) {
                highlightedLine.classList.remove('highlighted');
            }
            
            // 获取当前选择
            const selection = window.getSelection();
            if (selection.rangeCount > 0 && selection.toString().trim().length > 0) {
                const range = selection.getRangeAt(0);
                const container = range.commonAncestorContainer;
                
                // 向上查找包含的行容器
                let current = container;
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                    if (current.classList && current.classList.contains('line-container')) {
                        current.classList.add('highlighted');
                        highlightedLine = current;
                        break;
                    }
                    current = current.parentElement;
                }
            }
        }
        
        // 复制可见内容（纯文本模式，只复制 trace 内容）
        function copyVisibleContent(event) {
            // 获取所有可见的行（未被过滤器隐藏的）
            const allLines = document.querySelectorAll('.line-container');
            const visibleLines = Array.from(allLines).filter(line => {
                return line.style.display !== 'none' && !line.classList.contains('hidden');
            });

            // 构建文本，只复制 trace 内容（不包含行号）
            const textLines = [];
            visibleLines.forEach(line => {
                const lineContent = line.querySelector('.line-content').textContent;
                textLines.push(lineContent);
            });
            const textToCopy = textLines.join('\n');

            // 如果没有可见行，显示提示
            if (textLines.length === 0) {
                const originalText = event.target.textContent;
                event.target.textContent = 'No visible lines';
                event.target.style.background = 'rgba(239, 68, 68, 0.8)';
                setTimeout(() => {
                    event.target.textContent = originalText;
                    event.target.style.background = '';
                }, 1000);
                return;
            }

            // 复制到剪贴板
            if (navigator.clipboard) {
                navigator.clipboard.writeText(textToCopy).then(() => {
                    // 显示成功提示
                    const originalText = event.target.textContent;
                    event.target.textContent = `Copied ${textLines.length} lines`;
                    event.target.style.background = 'rgba(34, 197, 94, 0.8)';
                    setTimeout(() => {
                        event.target.textContent = originalText;
                        event.target.style.background = '';
                    }, 1000);
                }).catch(err => {
                    console.error('Failed to copy: ', err);
                    // 降级方案
                    fallbackCopy(textToCopy, event.target, textLines.length);
                });
            } else {
                // 老浏览器降级
                fallbackCopy(textToCopy, event.target, textLines.length);
            }
        }

        // 降级复制方案
        function fallbackCopy(text, button, lineCount) {
            const textArea = document.createElement('textarea');
            textArea.value = text;
            textArea.style.position = 'fixed';
            textArea.style.top = '-1000px';
            document.body.appendChild(textArea);
            textArea.select();
            try {
                document.execCommand('copy');
                // 显示成功提示
                const originalText = button.textContent;
                button.textContent = `Copied ${lineCount} lines`;
                button.style.background = 'rgba(34, 197, 94, 0.8)';
                setTimeout(() => {
                    button.textContent = originalText;
                    button.style.background = '';
                }, 1000);
            } catch (err) {
                console.error('Fallback copy failed: ', err);
                button.textContent = 'Failed';
                setTimeout(() => {
                    button.textContent = 'Copy';
                }, 1000);
            }
            document.body.removeChild(textArea);
        }
        
        // 滚动到顶部
        function scrollToTop() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
        
        // 监听滚动，更新进度条
        function updateProgressBar() {
            const winScroll = document.body.scrollTop || document.documentElement.scrollTop;
            const height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
            const scrolled = (winScroll / height) * 100;
            document.getElementById("progressBar").style.width = scrolled + "%";
        }
        
        // 记录鼠标按下事件
        document.addEventListener('mousedown', function(event) {
            mouseDownInfo = {
                x: event.clientX,
                y: event.clientY,
                time: Date.now()
            };
        });
        
        // 监听文本选择变化
        document.addEventListener('selectionchange', function() {
            const selection = window.getSelection();
            isSelectingText = selection.toString().trim().length > 0;
            
            if (isSelectingText) {
                highlightSelectedLines();
            } else {
                // 清除高亮
                if (highlightedLine) {
                    highlightedLine.classList.remove('highlighted');
                    highlightedLine = null;
                }
            }
        });

        // 页面初始化：恢复filter窗口状态
        document.addEventListener('DOMContentLoaded', function() {
            // 从localStorage读取filter窗口的显示状态
            const filterBoxVisible = lsGet('filterBoxVisible');
            const filterBox = document.getElementById('filterBox');

            if (filterBox) {
                // 如果有保存的状态，按状态显示
                if (filterBoxVisible === 'false') {
                    filterBox.style.display = 'none';
                } else {
                    // 默认显示（包括filterBoxVisible为'true'或未设置的情况）
                    filterBox.style.display = 'flex';
                }
            }

            // 恢复折叠状态
            if (fgTextDeferred()) return;
            initTextFolds();
            restoreFoldedState();
        });

        // 添加键盘快捷键支持
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape' && !event.target.matches('input, textarea, select') &&
                document.getElementById('keyboardHint').style.display === 'block') {
                event.preventDefault();
                closeKeyboardHint();
                return;
            }
            if (typeof fgCurrentView === 'function' && fgCurrentView() !== 'text') return;
            if (fgTextDeferred()) return;
            if ((event.key === 'Enter' || event.key === ' ') &&
                event.target.closest('.fold-icon, .fold-brace, #foldGuidesBtn, #viewHelpBtn, .keyboard-hint-close')) return;
            
            // 检查是否在输入框中
            if (event.target.matches('input, textarea, select')) {
                return;
            }
            

            
            // Enter键特殊处理：在链接上按Enter时，允许打开链接；在行上按Enter时，展开/收起
            if (event.key === 'Enter' || event.keyCode === 13) {
                // 检查当前焦点是否在链接上
                const target = event.target;
                const isLink = target.tagName === 'A' || target.closest('a');

                // 如果焦点在链接上，让链接正常跳转（不阻止默认行为）
                if (isLink) {
                    // 链接会正常跳转，不需要额外处理
                    return;
                }

                // 如果焦点不在链接上，检查是否在可展开行内
                let focusedLine = null;
                let element = target;

                // 向上查找包含 expandable 类的行容器
                while (element && element !== document.body) {
                    if (element.classList && element.classList.contains('line-container') && element.classList.contains('expandable')) {
                        focusedLine = element;
                        break;
                    }
                    element = element.parentElement;
                }

                // 如果找到可展开行，触发展开/收起
                if (focusedLine) {
                    event.preventDefault();
                    const lineId = focusedLine.getAttribute('data-line-id');
                    if (lineId) {
                        toggleExpand(lineId, event);
                    }
                    return;
                }

                // 如果当前焦点不在行内，使用键盘选中的行
                // 但只有在有可见的可展开行时才处理
                if (keyboardSelectedLine && keyboardSelectedLine.classList.contains('expandable') &&
                    keyboardSelectedLine.style.display !== 'none') {
                    event.preventDefault();
                    const lineId = keyboardSelectedLine.getAttribute('data-line-id');
                    if (lineId) {
                        toggleExpand(lineId, event);
                    }
                    return;
                }

                // 如果没有可展开行被处理，但 keyboardSelectedLine 不可展开，尝试展开当前行
                // 但只有在该行可见时才处理
                if (keyboardSelectedLine && keyboardSelectedLine.style.display !== 'none') {
                    event.preventDefault();
                    const lineId = keyboardSelectedLine.getAttribute('data-line-id');
                    if (lineId) {
                        toggleExpand(lineId, event);
                    }
                }
            }
            
            // 按下ESC键取消选择
            if (event.key === 'Escape' || event.keyCode === 27) {
                // 1. 清除键盘选中（键盘导航）
                if (keyboardSelectedLine) {
                    keyboardSelectedLine.classList.remove('keyboard-selected');
                    keyboardSelectedLine = null;
                    keyboardSelectedIndex = -1;
                }

                // 2. 清除文本选择高亮
                if (highlightedLine) {
                    highlightedLine.classList.remove('highlighted');
                    highlightedLine = null;
                }

                // 3. 清除文本选择
                const selection = window.getSelection();
                if (selection.toString().length > 0) {
                    selection.removeAllRanges();
                }

                // 4. 移除焦点（清除Tab选中）
                if (document.activeElement && document.activeElement !== document.body) {
                    document.activeElement.blur();
                }

                // 阻止默认行为
                event.preventDefault();
            }
            
            // VIM风格导航键和方向键
            let handled = false;
            const keyLower = event.key.toLowerCase();
            
            // 处理垂直移动（j/k 和上下箭头键）- 只导航到可展开行
            // 只有在有可见的可展开行时才处理
            if (keyLower === 'j' || event.key === 'ArrowDown') {
                event.preventDefault();
                handled = true;
                if (expandableLineIndices.length > 0 && expandableLines.length > 0) {
                    navigateToExpandableLine(1); // 向下导航
                }
            } else if (keyLower === 'k' || event.key === 'ArrowUp') {
                event.preventDefault();
                handled = true;
                if (expandableLineIndices.length > 0 && expandableLines.length > 0) {
                    navigateToExpandableLine(-1); // 向上导航
                }
            }
            
            // 处理水平滚动（h/l 键）
            // 支持大小写，Shift+h 和 Shift+l 也有效
            if (keyLower === 'h') {
                event.preventDefault();
                handled = true;
                // 向左滚动容器
                var container = document.querySelector('.container');
                if (container) {
                    container.scrollBy({ left: -100, behavior: 'smooth' });
                }
            } else if (keyLower === 'l') {
                event.preventDefault();
                handled = true;
                // 向右滚动容器
                var container = document.querySelector('.container');
                if (container) {
                    container.scrollBy({ left: 100, behavior: 'smooth' });
                }
            }
            
            // 处理其他功能键
            if (!handled) {
                switch (event.key) {
                        
                    case ' ': // 空格键向下滚动
                        if (!event.target.matches('input, textarea')) {
                            event.preventDefault();
                            window.scrollBy({ top: window.innerHeight * 0.8, behavior: 'smooth' });
                        }
                        break;
                        
                    case 'g': // gg: 跳转到顶部
                        if (!event.shiftKey) {
                            event.preventDefault();
                            if (event.repeat) break;
                            if (expandableLineIndices.length > 0) {
                                setKeyboardSelectedLine(expandableLineIndices[0], true);
                            } else if (allLines.length > 0) {
                                setKeyboardSelectedLine(0, true);
                            }
                        }
                        break;
                        
                    case 'G': // Shift+G: 跳转到底部
                        if (event.shiftKey) {
                            event.preventDefault();
                            if (event.repeat) break;
                            if (expandableLineIndices.length > 0) {
                                setKeyboardSelectedLine(expandableLineIndices[expandableLineIndices.length - 1], true);
                            } else if (allLines.length > 0) {
                                setKeyboardSelectedLine(allLines.length - 1, true);
                            }
                        }
                        break;
                        
                    case 'Home': // Home键跳转到顶部
                        event.preventDefault();
                        if (expandableLineIndices.length > 0) {
                            setKeyboardSelectedLine(expandableLineIndices[0], true);
                        } else if (allLines.length > 0) {
                            setKeyboardSelectedLine(0, true);
                        }
                        break;
                        
                    case 'End': // End键跳转到底部
                        event.preventDefault();
                        if (expandableLineIndices.length > 0) {
                            setKeyboardSelectedLine(expandableLineIndices[expandableLineIndices.length - 1], true);
                        } else if (allLines.length > 0) {
                            setKeyboardSelectedLine(allLines.length - 1, true);
                        }
                        break;
                        
                    case 'PageDown': // PageDown向下翻页
                        event.preventDefault();
                        window.scrollBy({ top: window.innerHeight * 0.9, behavior: 'smooth' });
                        break;
                        
                    case 'PageUp': // PageUp向上翻页
                        event.preventDefault();
                        window.scrollBy({ top: -window.innerHeight * 0.9, behavior: 'smooth' });
                        break;
                        
                    case '?': // 问号键显示键盘提示
                        event.preventDefault();
                        showKeyboardHint();
                        break;
                }

                // P1: Number keys 0-9 for fold depth control
                if (event.key >= '0' && event.key <= '9' && !event.ctrlKey && !event.metaKey && !event.altKey) {
                    event.preventDefault();
                    const depth = parseInt(event.key);
                    if (depth === 0) {
                        unfoldAllBlocks();
                    } else {
                        foldToDepth(depth);
                    }
                }
            }
            
            
            // Ctrl+A 选中所有文本
            if ((event.ctrlKey || event.metaKey) && event.key === 'a') {
                // 防止默认的全选行为，因为我们需要选择特定的内容
                event.preventDefault();
                
                // 选中所有行内容
                const allLineContents = document.querySelectorAll('.line-content');
                if (allLineContents.length > 0) {
                    const range = document.createRange();
                    const selection = window.getSelection();
                    
                    // 清除当前选择
                    selection.removeAllRanges();
                    
                    // 从第一个行内容开始
                    range.setStart(allLineContents[0], 0);
                    // 到最后一个行内容结束
                    range.setEnd(allLineContents[allLineContents.length - 1], allLineContents[allLines.length - 1].childNodes.length);
                    
                    selection.addRange(range);
                    highlightSelectedLines();
                }
            }
        });
        
        // 获取当前可见的行
        function getVisibleLines() {
            const viewportTop = window.scrollY;
            const viewportBottom = viewportTop + window.innerHeight;
            const visibleLines = [];
            
            allLines.forEach(line => {
                const rect = line.getBoundingClientRect();
                const lineTop = rect.top + window.scrollY;
                const lineBottom = rect.bottom + window.scrollY;
                
                // 检查行是否在视口中
                if (lineBottom > viewportTop && lineTop < viewportBottom) {
                    visibleLines.push(line);
                }
            });
            
            return visibleLines;
        }
        
        
        // 恢复视图状态
        function restoreViewState() {
            const savedState = lsGet('viewState');
            if (savedState) {
                const state = JSON.parse(savedState);
                window.scrollTo(0, state.scrollPosition);
            }
        }
        
        // 全局变量跟踪异步任务
        let expandCollapseTask = null;
        let isTaskRunning = false;
        let isWaitingForStop = false;

        // 禁用/启用两个按钮
        function setButtonsDisabled(disabled) {
            const expandBtn = document.querySelector('button[onclick="expandAll()"]');
            const collapseBtn = document.querySelector('button[onclick="collapseAll()"]');

            if (expandBtn) expandBtn.disabled = disabled;
            if (collapseBtn) collapseBtn.disabled = disabled;
        }

        // 等待任务完全停止
        function waitForTaskStop() {
            return new Promise((resolve) => {
                if (!isTaskRunning && !expandCollapseTask) {
                    resolve();
                    return;
                }

                const checkInterval = setInterval(() => {
                    if (!isTaskRunning && !expandCollapseTask) {
                        clearInterval(checkInterval);
                        resolve();
                    }
                }, 10);
            });
        }

        // 展开所有可展开行（异步执行，支持取消）
        async function expandAll() {
            const btn = event.target;

            // 如果正在等待停止，忽略点击
            if (isWaitingForStop) {
                return;
            }

            // 保存原始文本
            let originalText = btn.getAttribute('data-original-text');
            if (!originalText) {
                originalText = btn.textContent;
                btn.setAttribute('data-original-text', originalText);
            }

            // 如果有任务在运行，停止它并等待，然后返回（不发起新任务）
            if (expandCollapseTask) {
                isWaitingForStop = true;
                setButtonsDisabled(true);
                expandCollapseTask.cancel();
                await waitForTaskStop();
                isWaitingForStop = false;
                setButtonsDisabled(false);

                // 恢复按钮文本
                btn.textContent = originalText;
                return;
            }

            // 没有任务在运行，开始新的 Expand 任务 - 只对可见的行操作
            const expandableLines = Array.from(document.querySelectorAll('.line-container.expandable'))
                .filter(container => container.style.display !== 'none');
            const total = expandableLines.length;

            if (total === 0) return;

            // 显示进度
            btn.textContent = `Expanding... 000%`;
            isTaskRunning = true;

            let processed = 0;
            const batchSize = 50;
            let cancelled = false;

            // 创建任务对象
            expandCollapseTask = {
                type: 'expand',
                cancel: function() {
                    cancelled = true;
                    btn.textContent = originalText;
                    isTaskRunning = false;
                    expandCollapseTask = null;
                }
            };

            function processBatch() {
                if (cancelled) {
                    isTaskRunning = false;
                    expandCollapseTask = null;
                    return;
                }

                const batch = expandableLines.slice(processed, processed + batchSize);

                batch.forEach(container => {
                    if (cancelled) return;

                    const lineId = container.getAttribute('data-line-id');
                    const content = document.getElementById(lineId + '_content');
                    const expandBtn = container.querySelector('.expand-btn');
                    const isVisible = container.style.display !== 'none';

                    if (content && isVisible) {
                        fgFill(content); content.style.display = 'block';
                        if (expandBtn) expandBtn.textContent = '-';
                        container.classList.add('selected');
                        saveExpandedState(lineId, true);
                    }
                });

                processed += batch.length;

                // 更新进度（固定长度百分比）
                const percent = Math.round((processed / total) * 100);
                const percentStr = percent.toString().padStart(3, ' ');
                btn.textContent = `Expanding... ${percentStr}%`;

                if (processed < total && !cancelled) {
                    requestAnimationFrame(processBatch);
                } else {
                    if (!cancelled) {
                        btn.textContent = originalText;
                        isTaskRunning = false;
                        expandCollapseTask = null;
                    }
                }
            }

            requestAnimationFrame(processBatch);
        }

        // 收起所有可展开行（异步执行，支持取消）
        async function collapseAll() {
            const btn = event.target;

            // 如果正在等待停止，忽略点击
            if (isWaitingForStop) {
                return;
            }

            // 保存原始文本
            let originalText = btn.getAttribute('data-original-text');
            if (!originalText) {
                originalText = btn.textContent;
                btn.setAttribute('data-original-text', originalText);
            }

            // 如果有任务在运行，停止它并等待，然后返回（不发起新任务）
            if (expandCollapseTask) {
                isWaitingForStop = true;
                setButtonsDisabled(true);
                expandCollapseTask.cancel();
                await waitForTaskStop();
                isWaitingForStop = false;
                setButtonsDisabled(false);

                // 恢复按钮文本
                btn.textContent = originalText;

                // 清除高亮
                if (highlightedLine) {
                    highlightedLine.classList.remove('highlighted');
                    highlightedLine = null;
                }
                return;
            }

            // 没有任务在运行，开始新的 Collapse 任务 - 只对可见的行操作
            const expandableLines = Array.from(document.querySelectorAll('.line-container.expandable'))
                .filter(container => container.style.display !== 'none');
            const total = expandableLines.length;

            if (total === 0) {
                // 清除高亮
                if (highlightedLine) {
                    highlightedLine.classList.remove('highlighted');
                    highlightedLine = null;
                }
                return;
            }

            // 显示进度
            btn.textContent = `Collapsing... 000%`;
            isTaskRunning = true;

            let processed = 0;
            const batchSize = 50;
            let cancelled = false;

            // 创建任务对象
            expandCollapseTask = {
                type: 'collapse',
                cancel: function() {
                    cancelled = true;
                    btn.textContent = originalText;
                    isTaskRunning = false;
                    expandCollapseTask = null;
                }
            };

            function processBatch() {
                if (cancelled) {
                    isTaskRunning = false;
                    expandCollapseTask = null;
                    return;
                }

                const batch = expandableLines.slice(processed, processed + batchSize);

                batch.forEach(container => {
                    if (cancelled) return;

                    const lineId = container.getAttribute('data-line-id');
                    const content = document.getElementById(lineId + '_content');
                    const expandBtn = container.querySelector('.expand-btn');
                    const isVisible = container.style.display !== 'none';

                    if (content && isVisible) {
                        content.style.display = 'none';
                        if (expandBtn) expandBtn.textContent = '+';
                        container.classList.remove('selected');
                        saveExpandedState(lineId, false);
                    }
                });

                processed += batch.length;

                // 更新进度（固定长度百分比）
                const percent = Math.round((processed / total) * 100);
                const percentStr = percent.toString().padStart(3, ' ');
                btn.textContent = `Collapsing... ${percentStr}%`;

                if (processed < total && !cancelled) {
                    requestAnimationFrame(processBatch);
                } else {
                    if (!cancelled) {
                        btn.textContent = originalText;
                        isTaskRunning = false;
                        expandCollapseTask = null;

                        // 清除高亮
                        if (highlightedLine) {
                            highlightedLine.classList.remove('highlighted');
                            highlightedLine = null;
                        }
                    }
                }
            }

            requestAnimationFrame(processBatch);
        }
        
        // 更新锚点并复制到剪贴板
        function updateAnchor(anchorId, event) {
            event.stopPropagation();
            
            // 更新URL hash
            window.location.hash = anchorId;
            
            // 获取当前完整URL
            const fullUrl = window.location.href;
            
            // 尝试复制到剪贴板（静默操作，不显示任何反馈）
            navigator.clipboard.writeText(fullUrl).catch(() => {
                // 降级方案
                const textArea = document.createElement('textarea');
                textArea.value = fullUrl;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);
            });
        }
        
        // 处理锚点导航
        function handleAnchorNavigation() {
            if (fgTextDeferred() && location.hash) {
                const anchor = location.hash;
                Promise.resolve(fgSwitchView('text')).then(api => {
                    if (api && fgCurrentView() === 'text' && location.hash === anchor) handleAnchorNavigation();
                });
                return;
            }
            const hash = window.location.hash.substring(1);
            if (hash) {
                // 尝试查找锚点元素
                const element = document.getElementById(hash);
                if (element && element.classList.contains('line-container')) {
                    // 如果是行容器，清除其他高亮，然后高亮此行
                    const allLines = document.querySelectorAll('.line-container');
                    allLines.forEach(line => line.classList.remove('highlighted'));
                    element.classList.add('highlighted');
                    
                    // 平滑滚动到该行
                    setTimeout(() => {
                        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }, 100);
                    
                    // 设置键盘选中
                    const lineId = element.getAttribute('data-line-id');
                    const lineIndex = allLines.length > 0 ? Array.from(allLines).findIndex(line => line.getAttribute('data-line-id') === lineId) : -1;
                    if (lineIndex >= 0) {
                        setKeyboardSelectedLine(lineIndex, false);
                    }
                }
            }
        }
        
        // 初始加载时
        // ============================================================
        // P0: Duration Heatmap
        // ============================================================
        let heatmapEnabled = lsGet('heatmapEnabled') !== 'false';

        // Heatmap: data-heat is pre-computed in Python based on ftrace prefix markers.
        // This function only builds the legend and applies visibility.
        function applyDurationHeatmap() {
            const legend = document.getElementById('heatmapLegend');
            if (legend) {
                const levels = [
                    { color: 'rgba(76,175,80,0.35)',  label: '≤10us' },
                    { color: 'rgba(139,195,74,0.4)',  label: '>10us' },
                    { color: 'rgba(255,235,59,0.5)',  label: '>100us' },
                    { color: 'rgba(255,193,7,0.55)',  label: '>1ms' },
                    { color: 'rgba(255,152,0,0.6)',   label: '>10ms' },
                    { color: 'rgba(244,67,54,0.65)',  label: '>100ms' },
                    { color: 'rgba(183,28,28,0.7)',   label: '>1s' }
                ];
                legend.innerHTML = levels.map(l =>
                    '<span class="heatmap-legend-item" style="background:' + l.color + '"></span>' +
                    '<span class="heatmap-legend-label">' + l.label + '</span>'
                ).join('');
            }
            updateHeatmapVisibility();
        }

        function updateHeatmapVisibility() {
            const btn = document.getElementById('heatmapBtn');
            if (btn) btn.textContent = heatmapEnabled ? 'Heatmap ●' : 'Heatmap ○';
            const legend = document.getElementById('heatmapLegend');
            if (legend) legend.classList.toggle('visible', heatmapEnabled);
            // Toggle a class on body to control heatmap visibility via CSS
            if (heatmapEnabled) {
                document.body.classList.add('heatmap-on');
                document.body.classList.remove('heatmap-off');
            } else {
                document.body.classList.remove('heatmap-on');
                document.body.classList.add('heatmap-off');
            }
        }

        function toggleHeatmap() {
            heatmapEnabled = !heatmapEnabled;
            lsSet('heatmapEnabled', heatmapEnabled);
            updateHeatmapVisibility();
        }

        // ============================================================
        // P1: Fold to Depth
        // ============================================================
        // P3: batch operations — write localStorage once at the end
        function foldToDepth(maxDepth) {
            const entries = document.querySelectorAll('[data-fold-type="entry"]');
            entries.forEach(entry => {
                const depth = parseInt(entry.getAttribute('data-call-depth'));
                if (isNaN(depth)) return;
                const foldId = entry.getAttribute('data-fold-id');
                if (!foldId) return;
                _setFolded(foldId, depth >= maxDepth);
            });
            lsSet('foldedBlocks', JSON.stringify(foldedBlocks));
        }

        function unfoldAllBlocks() {
            const foldIds = Object.keys(foldedBlocks);
            foldIds.forEach(foldId => _setFolded(foldId, false));
            lsSet('foldedBlocks', JSON.stringify(foldedBlocks));
        }

        // F1: Recursive fold — fold/unfold block and all its child blocks
        function toggleFoldRecursive(foldId) {
            const shouldFold = !foldedBlocks[foldId];
            _setFolded(foldId, shouldFold);
            // Find all child entry blocks whose ancestors include this foldId
            const childEntries = document.querySelectorAll('[data-fold-type="entry"][data-fold-ancestors~="' + foldId + '"]');
            childEntries.forEach(child => {
                const childId = child.getAttribute('data-fold-id');
                if (childId) _setFolded(childId, shouldFold);
            });
            lsSet('foldedBlocks', JSON.stringify(foldedBlocks));
        }

        // ============================================================
        // P2: Fold Summary
        // ============================================================
        function addFoldSummary(foldId) {
            let entryEl = null;
            try {
                entryEl = document.querySelector('[data-fold-id="' + foldId + '"][data-fold-type="entry"]');
            } catch(e) { return; }
            if (!entryEl) return;

            // Remove existing summary if any
            const existing = entryEl.querySelector('.fold-summary');
            if (existing) existing.remove();

            // Find all inner/exit lines belonging to this fold
            const innerLines = document.querySelectorAll('[data-fold-id="' + foldId + '"], [data-fold-ancestors~="' + foldId + '"]');

            let childCalls = 0;
            let totalDuration = 0;
            let hasDuration = false;
            let retValue = null;
            let hasErrorReturn = false;

            innerLines.forEach(line => {
                const thisId = line.getAttribute('data-fold-id');
                const thisType = line.getAttribute('data-fold-type');

                if ((thisType === 'entry' && thisId !== foldId) ||
                    (thisType === 'inner' && line.hasAttribute('data-func'))) {
                    childCalls++;
                }

                // Get return value from exit line of this fold
                if (thisType === 'exit' && thisId === foldId) {
                    const ret = line.getAttribute('data-fold-ret') || line.getAttribute('data-ret');
                    if (ret) retValue = ret;
                    hasErrorReturn = line.hasAttribute('data-ret');
                    // Get duration from exit line
                    const dur = line.getAttribute('data-sort-duration') || line.getAttribute('data-duration');
                    if (dur) {
                        totalDuration = parseFloat(dur);
                        hasDuration = true;
                    }
                }
            });

            // If no duration from exit, try from entry itself
            if (!hasDuration) {
                const dur = entryEl.getAttribute('data-sort-duration') || entryEl.getAttribute('data-duration');
                if (dur) {
                    totalDuration = parseFloat(dur);
                    hasDuration = true;
                }
            }

            // Build summary text
            const parts = [];
            if (childCalls > 0) {
                parts.push('<span class="summary-count">' + childCalls + ' call' + (childCalls > 1 ? 's' : '') + '</span>');
            }
            if (hasDuration && totalDuration > 0) {
                let timeStr;
                if (totalDuration >= 1000000) timeStr = (totalDuration / 1000000).toFixed(1) + ' s';
                else if (totalDuration >= 1000) timeStr = (totalDuration / 1000).toFixed(1) + ' ms';
                else timeStr = totalDuration.toFixed(1) + ' us';
                parts.push('<span class="summary-time">' + timeStr + '</span>');
            }
            if (retValue) {
                // Look up error code name from the ERROR_CODE_MAP if embedded
                let retDisplay = 'ret=' + retValue;
                const errSpan = entryEl.closest('.line-container');
                // Try to find error name from sibling exit line
                const exitEl = document.querySelector('[data-fold-id="' + foldId + '"][data-fold-type="exit"]');
                if (exitEl) {
                    const exitContent = exitEl.querySelector('.line-content');
                    if (exitContent) {
                        const errMatch = exitContent.textContent.match(/\/\*\s*(-?\d+\s*=\s*\w+)\s*\*\//);
                        if (errMatch) retDisplay = errMatch[1].trim();
                    }
                }
                parts.push('<span class="summary-ret' + (hasErrorReturn ? ' summary-error' : '') + '">' + escapeHtml(retDisplay) + '</span>');
            }

            if (parts.length > 0) {
                const summary = document.createElement('span');
                summary.className = 'fold-summary';
                summary.innerHTML = ' [' + parts.join(', ') + ']';
                const brace = entryEl.querySelector('.fold-brace');
                const lineContent = entryEl.querySelector('.line-content');
                if (brace) brace.after(summary);
                else if (lineContent) lineContent.appendChild(summary);
            }
        }

        function removeFoldSummary(foldId) {
            let entryEl = null;
            try {
                entryEl = document.querySelector('[data-fold-id="' + foldId + '"][data-fold-type="entry"]');
            } catch(e) { return; }
            if (!entryEl) return;
            const summary = entryEl.querySelector('.fold-summary');
            if (summary) summary.remove();
        }

        function fgInitText(mounted = false) {
            if (fgTextInitialized || fgTextDeferred()) return;
            fgTextInitialized = true;
            if (mounted) {
                fgRestoreTextOrder();
                initTextFolds();
                restoreFoldedState();
            }
            // 初始化总行数
            // (the old .summary element no longer exists; count the rendered lines)
            total_lines = document.querySelectorAll('.line-container').length;

            // 恢复展开状态
            restoreExpandedState();

            if (mounted) {
                const filterText = window.fgFilterProgress?.text ?? window.fgFilterState.text;
                if (filterText) {
                    const input = document.getElementById('filterInput');
                    if (input) input.value = filterText;
                    applyFilter();
                }
            }

            // 移除自动滚动，保持当前视图位置
            // restoreViewState(); // 注释掉，避免自动滚动

            // 初始化键盘导航
            initKeyboardNavigation();

            // 初始化统一过滤器（如果过滤框存在）
            if (document.getElementById('filterInput')) {
                initFilterFields();
            }

            // 处理锚点导航
            handleAnchorNavigation();

            // P0: Apply duration heatmap
            applyDurationHeatmap();

            // P2: Add summaries for initially folded blocks
            Object.keys(foldedBlocks).forEach(foldId => {
                addFoldSummary(foldId);
            });
        }

        window.addEventListener('load', function() {
            initInfoPanel();
            if (typeof initFloatingControls === 'function') {
                try { initFloatingControls(); } catch (error) { console.warn('initFloatingControls failed', error); }
            }
            if (typeof initFloatingToggleDrag === 'function') {
                try { initFloatingToggleDrag(); } catch (error) { console.warn('initFloatingToggleDrag failed', error); }
            }
            window.addEventListener('scroll', updateProgressBar);
            fgInitText();
        });
        
        // 监听hash变化，支持动态锚点导航
        window.addEventListener('hashchange', function() {
            handleAnchorNavigation();
        });
