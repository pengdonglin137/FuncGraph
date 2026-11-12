let fgTextWindow = null;

function fgOpenWindowedText() {
    const url = new URL(location.href);
    url.searchParams.set('text', 'windowed');
    lsSet('view', 'text');
    location.assign(url.href);
}

function fgRefreshTextWindow() {
    return fgTextWindow?.rebuild().catch(error => {
        if (error.name !== 'AbortError') console.warn('Text window:', error);
    });
}

async function fgApplyWindowFilter(text, generation) {
    const renderer = fgTextWindow;
    const controller = fgFilterController = new AbortController();
    const previous = { state: window.fgFilterState, sort: fgTextSort, graph: _graphFoldIds,
        anchor: renderer.anchor(), suppressed: new Set(renderer.suppressedSources) };
    renderer.reportFilterError('');
    updateFilterTags(text);
    try {
        const result = await fgIndexedFilter(fgData.read('fgTextIndex'), text, controller.signal, message => {
            if (generation === fgFilterGeneration) fgSetFilterProgress({ text, message, error: false });
        });
        if (controller.signal.aborted || generation !== fgFilterGeneration || renderer !== fgTextWindow) return null;
        _graphFoldIds = result.graphSets || {};
        fgTextSort = result.sortDir || '';
        window.fgFilterProgress = null;
        fgNotifyFilter(result.text, result.lines);
        fgShowFilterCount(result.visible, result.total);
        renderer.suppressedSources.clear();
        if (result.lines) for (const [key, expanded] of Object.entries(expandedLines)) if (expanded) renderer.suppressedSources.add(key);
        renderer.invalidateHeights();
        await renderer.rebuild();
        return true;
    } catch (error) {
        if (error.name === 'AbortError' || generation !== fgFilterGeneration) return null;
        fgTextSort = previous.sort; _graphFoldIds = previous.graph;
        fgNotifyFilter(previous.state.text, previous.state.lines);
        renderer.suppressedSources.clear();
        for (const key of previous.suppressed) renderer.suppressedSources.add(key);
        try { await renderer.rebuild(previous.anchor); } catch (restoreError) {
            if (restoreError.name !== 'AbortError') console.warn('Previous Text window:', restoreError);
        }
        fgSetFilterProgress({ text, error: true, message: 'Filter not applied: ' + error.message });
        renderer.reportFilterError('Filter not applied: ' + error.message + '. Previous result retained; Apply again to retry.');
        return false;
    } finally { if (fgFilterController === controller) fgFilterController = null; }
}

function* fgWindowedTextSteps() {
    let renderer = null, ready = false;
    try {
        const data = yield { label: 'Loading complete Text metadata…', load: signal => fgData.load('fgTextIndex', { signal }) };
        const model = yield* fgTextModel.prepare(data);
        yield* fgTextModel.prepareFolding(model);
        const navigation = yield* fgTextModel.prepareNavigation(model);
        if (fgTextOrder === 'task') yield* fgTextModel.prepareOrder(model, 'task');
        const root = document.getElementById('content');
        fgDomTextModels.set(root, model);
        fgTextNavigation = navigation;
        renderer = fgTextWindow = fgCreateTextWindow(model, navigation, root);
        yield { label: 'Loading the first Text window…', load: () => renderer.rebuild() };
        const filterText = window.fgFilterProgress?.text ?? window.fgFilterState.text;
        if (filterText) {
            const input = document.getElementById('filterInput');
            if (input) input.value = filterText;
            yield { label: 'Applying the full-trace filter…', load: () => fgApplyWindowFilter(filterText, fgFilterGeneration) };
        }
        root.removeAttribute('data-fg-text');
        fgInitText(true, model, navigation, renderer);
        document.querySelector('.controls').inert = false;
        ready = true;
        return renderer;
    } finally {
        if (renderer && !ready) {
            renderer.dispose(); fgTextWindow = null; fgTextNavigation = null;
            fgDomTextModels.delete(renderer.root);
            renderer.root.setAttribute('data-fg-text', 'true');
        }
    }
}

function fgCreateTextWindow(model, navigation, root) {
    if (model.version !== 4 || fgData.textChunks()?.count !== model.records.length) {
        throw new Error('Windowed Text requires a complete presentation index');
    }
    const limit = 160, overscan = 32;
    let heights = new Float64Array(model.records.length);
    const positions = new Map();
    const suppressedSources = new Set();
    let sequence = [], tree = new Float64Array(1), sizes = [], plan = null, taskGroups = [], rankedOrder = null;
    let first = 0, end = 0, generation = 0, controller = null, frame = null, pendingStart = null;
    let destroyed = false, suspended = false, fullJob = null, fullController = null, currentJob = null, navigating = 0, rebuilding = 0;
    let measuredWidth = innerWidth, widest = 0, filterError = '';
    const top = document.createElement('div'), body = document.createElement('div'), bottom = document.createElement('div');
    top.className = bottom.className = 'fg-window-spacer';
    top.setAttribute('aria-hidden', 'true'); bottom.setAttribute('aria-hidden', 'true');
    body.className = 'fg-window-rows';
    const notice = document.createElement('div');
    notice.className = 'fg-window-notice';
    const status = document.createElement('span');
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const retry = document.createElement('button');
    retry.type = 'button'; retry.textContent = 'Retry'; retry.hidden = true;
    const full = document.createElement('button');
    full.type = 'button'; full.textContent = 'Full Text';
    full.title = 'Mount all rows for browser Find, selection across the whole trace and printing';
    notice.append(status, retry, full);
    root.before(notice);
    const context = document.createElement('div'), contextHeading = document.createElement('div');
    context.className = 'fg-window-context'; context.hidden = true;
    context.setAttribute('role', 'note'); context.setAttribute('aria-label', 'Current task');
    contextHeading.className = 'task-group-head'; context.appendChild(contextHeading);
    (root.closest('#textViewport') || root).before(context);
    root.classList.add('fg-windowed');
    root.replaceChildren(top, body, bottom);

    const offset = () => root.getBoundingClientRect().top + scrollY;
    const prefix = count => {
        let result = 0;
        for (let index = count; index > 0; index -= index & -index) result += tree[index];
        return result;
    };
    const indexAt = value => {
        let index = 0, sum = 0;
        for (let step = 2 ** Math.floor(Math.log2(Math.max(1, sequence.length))); step >= 1; step /= 2) {
            const next = index + step;
            if (next <= sequence.length && sum + tree[next] <= value) { index = next; sum += tree[next]; }
        }
        return Math.min(index, Math.max(0, sequence.length - 1));
    };
    const padding = () => {
        top.style.height = prefix(first) + 'px';
        bottom.style.height = Math.max(0, prefix(sequence.length) - prefix(end)) + 'px';
    };
    const selected = () => {
        const selection = getSelection();
        return selection && !selection.isCollapsed && root.contains(selection.anchorNode);
    };
    const showStatus = message => {
        status.textContent = message || filterError || 'Windowed Text (experimental) · ' + positions.size.toLocaleString() + ' visible rows · up to ' + limit + ' mounted';
    };
    const check = signal => {
        if (signal?.aborted || destroyed || suspended) throw new DOMException('Text window canceled', 'AbortError');
    };
    function updateTaskContext() {
        const inset = Math.max(0, document.querySelector('.controls')?.getBoundingClientRect().bottom || 0);
        const position = scrollY + inset - offset();
        if (destroyed || suspended || fgTextOrder !== 'task' || fgTextSort || !taskGroups.length ||
            position < 0 || position >= prefix(sequence.length)) {
            context.hidden = true;
            return;
        }
        const at = indexAt(position);
        let low = 0, high = taskGroups.length;
        while (low < high) {
            const middle = Math.floor((low + high) / 2);
            if (taskGroups[middle].index <= at) low = middle + 1;
            else high = middle;
        }
        const group = taskGroups[low - 1];
        if (!group || position <= prefix(group.index) + 0.5) { context.hidden = true; return; }
        if (contextHeading.textContent !== group.text) {
            contextHeading.textContent = group.text;
            contextHeading.title = group.text;
        }
        context.style.top = inset + 'px';
        context.hidden = false;
    }
    function measure() {
        if (destroyed || suspended || !body.childElementCount) return;
        const viewportY = Math.max(0, scrollY - offset());
        const anchor = indexAt(viewportY), within = viewportY - prefix(anchor);
        for (const element of body.children) {
            const index = +element.dataset.windowIndex;
            if (index < first || index >= end) continue;
            const height = element.getBoundingClientRect().height;
            if (!(height > 0) || !Number.isFinite(height)) continue;
            const delta = height - sizes[index];
            sizes[index] = height;
            const item = sequence[index];
            if (typeof item === 'number') heights[fgTextModel.locate(model, item).ordinal] = height;
            for (let position = index + 1; position < tree.length; position += position & -position) tree[position] += delta;
        }
        widest = Math.max(widest, body.scrollWidth);
        root.style.minWidth = Math.max(root.parentElement.clientWidth, widest) + 'px';
        padding();
        const target = offset() + prefix(anchor) + within;
        if (scrollY >= offset() && Math.abs(target - scrollY) > 0.5) scrollTo({ top: target, behavior: 'instant' });
        updateTaskContext();
    }
    const observer = new ResizeObserver(() => { measure(); schedule(); });
    const controlsObserver = new ResizeObserver(schedule);
    const controls = document.querySelector('.controls');
    if (controls) controlsObserver.observe(controls);
    async function paint(start, signal) {
        check(signal);
        const stop = Math.min(sequence.length, start + limit);
        const id = ++generation;
        controller?.abort();
        const own = controller = new AbortController();
        pendingStart = start;
        const abort = () => own.abort();
        signal?.addEventListener('abort', abort, { once: true });
        retry.hidden = true;
        root.setAttribute('aria-busy', 'true');
        try {
            const retained = new Map(Array.from(body.children).filter(element => element.dataset.windowLine)
                .map(element => [+element.dataset.windowLine, element]));
            const missing = new Map();
            const required = new Set(sequence.slice(start, stop).filter(item => typeof item === 'number' && !retained.has(item)));
            for (const number of required) {
                const ordinal = fgTextModel.locate(model, number).ordinal;
                const block = Math.floor(ordinal / fgData.textChunks().paint_rows);
                if (!missing.has(block)) missing.set(block, number);
            }
            const blocks = Array.from(missing.values());
            let next = 0;
            const collect = async () => {
                while (next < blocks.length) {
                    const number = blocks[next++];
                    const loaded = await fgLoadTextBlock(model, number, { signal: own.signal });
                    check(own.signal);
                    for (const row of Array.from(loaded.element.querySelectorAll('.line-container'))) {
                        const identity = +row.dataset.lineNumber;
                        if (!required.has(identity)) continue;
                        const wrapper = document.createElement('div');
                        wrapper.dataset.windowLine = identity;
                        const source = row.nextElementSibling;
                        wrapper.appendChild(row);
                        if (source?.classList.contains('expanded-content')) wrapper.appendChild(source);
                        retained.set(identity, wrapper);
                    }
                    await new Promise(resolve => setTimeout(resolve, 0));
                }
            };
            await Promise.all(Array.from({ length: Math.min(4, blocks.length) }, collect));
            check(own.signal);
            if (id !== generation) throw new DOMException('Text window replaced', 'AbortError');
            const fragment = document.createDocumentFragment();
            for (let index = start; index < stop; index++) {
                const item = sequence[index];
                let element;
                if (typeof item === 'number') {
                    element = retained.get(item);
                    if (!element) throw new Error('Text window is missing line ' + item);
                } else {
                    element = document.createElement('div');
                    const heading = document.createElement('div');
                    heading.className = item.kind; heading.textContent = item.text;
                    element.appendChild(heading);
                }
                element.dataset.windowIndex = index;
                fragment.appendChild(element);
            }
            observer.disconnect();
            fgCancelSources(body);
            body.replaceChildren(fragment);
            first = start; end = stop;
            padding();
            for (const element of body.children) {
                const row = element.querySelector('.line-container');
                if (row) {
                    row.classList.toggle('task-switch', fgTextOrder === 'task' && plan.switches.has(+row.dataset.lineNumber));
                    const fold = row.dataset.foldId;
                    if (row.dataset.foldType === 'entry') fgUpdateFoldState(fgTextModel.folding(model), fold, foldedBlocks[fold] === true, true);
                    fgProjectFoldLine(row, true);
                    if (keyboardSelectedLine?.id === row.id) {
                        keyboardSelectedLine = row;
                        row.classList.add('keyboard-selected');
                    }
                }
                observer.observe(element);
            }
            updateExpandableLines();
            measure(); showStatus();
        } catch (error) {
            own.abort();
            if (error.name !== 'AbortError' && id === generation) {
                showStatus('Text rows not loaded: ' + error.message + '. Previous window retained.');
                retry.hidden = false;
            }
            throw error;
        } finally {
            signal?.removeEventListener('abort', abort);
            if (id === generation) { root.removeAttribute('aria-busy'); pendingStart = null; }
        }
    }
    function schedule() {
        if (frame !== null || destroyed || suspended) return;
        frame = requestAnimationFrame(() => {
            frame = null;
            updateTaskContext();
            if (navigating || rebuilding || fullJob) return;
            if (selected()) { showStatus('Selection pinned · clear selection to scroll, or choose Full Text'); return; }
            const at = indexAt(Math.max(0, scrollY - offset()));
            const last = indexAt(Math.max(0, scrollY + innerHeight - offset()));
            if (at >= first + 8 && last < end - 8 || first === 0 && at < overscan && last < end - 8 || end === sequence.length && at >= first + 8) return;
            const start = Math.max(0, Math.min(sequence.length - limit, at - overscan));
            if (pendingStart === start) return;
            if (first === start && end === Math.min(sequence.length, start + limit)) return;
            currentJob = paint(start).catch(error => { if (error.name !== 'AbortError') console.warn('Text window:', error); });
        });
    }
    function anchor() { return sequence[indexAt(Math.max(0, scrollY - offset()))]; }
    function rebuild(preferredNumber) {
        const previous = preferredNumber ?? anchor();
        controller?.abort(); generation++;
        const folding = fgTextModel.folding(model);
        plan = fgTextModel.order(model, fgTextOrder);
        let order = plan.order;
        if (fgTextSort) {
            if (!rankedOrder || rankedOrder.plan !== plan || rankedOrder.direction !== fgTextSort) {
                const ranked = order.map(number => {
                    const record = fgTextModel.locate(model, number).record;
                    return { number, duration: parseFloat(record.getAttribute('data-sort-duration') || record.getAttribute('data-duration') || '0') };
                });
                ranked.sort((left, right) => fgTextSort === 'desc' ? right.duration - left.duration : left.duration - right.duration);
                rankedOrder = { plan, direction: fgTextSort, order: ranked.map(item => item.number) };
            }
            order = rankedOrder.order;
        } else rankedOrder = null;
        navigation.reorder(order);
        positions.clear(); sequence = []; taskGroups = [];
        const boundaries = new Map();
        if (!fgTextSort) for (const boundary of plan.boundaries) {
            const number = plan.order[boundary.at];
            if (!boundaries.has(number)) boundaries.set(number, []);
            boundaries.get(number).push(boundary);
        }
        let pendingBoundaries = [];
        for (const number of order) {
            for (const boundary of boundaries.get(number) || []) {
                if (boundary.kind === 'task-group-head') pendingBoundaries = [boundary];
                else pendingBoundaries.push(boundary);
            }
            const record = fgTextModel.locate(model, number).record;
            const visible = folding.visible(record, foldedBlocks, window.fgFilterState.lines) &&
                (fgTextSort || fgTextOrder !== 'task' || !plan.hidden.has(number));
            navigation.setVisible(number, !!visible);
            if (!visible) continue;
            for (const boundary of pendingBoundaries) {
                if (boundary.kind === 'task-group-head') taskGroups.push({ index: sequence.length, text: boundary.text });
                sequence.push(boundary);
            }
            pendingBoundaries = [];
            positions.set(number, sequence.length); sequence.push(number);
        }
        const style = getComputedStyle(root);
        const estimate = Math.max(21, parseFloat(style.getPropertyValue('--fg-text-font-size')) * 1.5 +
            2 * parseFloat(style.getPropertyValue('--fg-text-pad-y')));
        sizes = sequence.map(item => typeof item === 'number' ? heights[fgTextModel.locate(model, item).ordinal] || estimate : 30);
        tree = new Float64Array(sequence.length + 1);
        for (let index = 1; index < tree.length; index++) {
            tree[index] += sizes[index - 1];
            const parent = index + (index & -index);
            if (parent < tree.length) tree[parent] += tree[index];
        }
        root.classList.toggle('grouped', fgTextOrder === 'task' && !fgTextSort);
        const button = document.getElementById('textOrderBtn');
        if (button) button.textContent = 'Order: ' + (fgTextSort ? 'duration ' + (fgTextSort === 'desc' ? '↓' : '↑') : fgTextOrder === 'task' ? 'by task' : 'time');
        const anchorIndex = positions.get(previous) || 0;
        first = end = 0;
        rebuilding++;
        return paint(Math.max(0, Math.min(sequence.length - limit, anchorIndex - overscan))).then(() => {
            if (scrollY > offset()) scrollTo({ top: offset() + prefix(anchorIndex), behavior: 'instant' });
        }).finally(() => { rebuilding--; schedule(); });
    }
    async function ensureLine(number, options = {}) {
        if (destroyed) return document.getElementById('L' + number);
        check(options.signal);
        const position = positions.get(number);
        if (position === undefined) return null;
        const mounted = document.getElementById('L' + number);
        if (mounted) return mounted;
        navigating++;
        try {
            await paint(Math.max(0, Math.min(sequence.length - limit, position - overscan)), options.signal);
            check(options.signal);
            const row = document.getElementById('L' + number);
            if (row) fgScrollTextLine(row, { block: 'center', behavior: 'instant' });
            return row;
        } finally { navigating--; }
    }
    function dispose() {
        destroyed = true; generation++; controller?.abort();
        observer.disconnect(); controlsObserver.disconnect(); cancelAnimationFrame(frame);
        fgCancelSources(body); body.replaceChildren();
        positions.clear(); sequence = []; sizes = []; plan = null; taskGroups = []; rankedOrder = null;
        heights = new Float64Array(0); tree = new Float64Array(1);
        removeEventListener('scroll', schedule); removeEventListener('resize', resize);
        document.removeEventListener('selectionchange', schedule);
        document.removeEventListener('keydown', wholeDocumentAction, true);
        root.classList.remove('fg-windowed'); root.style.minWidth = '';
        root.removeAttribute('aria-busy');
        notice.remove(); context.remove();
    }
    async function fullText() {
        if (destroyed) return;
        if (fullJob) return fullJob;
        check();
        const loading = fullController = new AbortController();
        full.disabled = true;
        fullJob = (async () => {
            controller?.abort(); generation++;
            const selectedNumber = keyboardSelectedLine && +keyboardSelectedLine.dataset.lineNumber;
            const fragment = document.createDocumentFragment();
            const catalog = fgData.textChunks();
            for (let ordinal = 0; ordinal < catalog.count; ordinal += catalog.paint_rows) {
                showStatus('Loading complete Text: ' + ordinal + ' / ' + catalog.count + '…');
                const block = await fgLoadTextBlock(model, model.records[ordinal].row[0], { signal: loading.signal });
                check(loading.signal);
                fragment.appendChild(block.element);
                await new Promise(resolve => setTimeout(resolve, 0));
            }
            fgCancelSources(body);
            dispose(); fgTextWindow = null;
            root.replaceChildren(fragment);
            fgReorder(root, fgTextOrder);
            restoreFoldedState(); restoreExpandedState(); applyFilter();
            if (selectedNumber) fgSelectMountedTextLine(document.getElementById('L' + selectedNumber), true);
        })().catch(error => {
            full.disabled = false;
            showStatus('Complete Text not loaded: ' + error.message + '. Windowed Text retained; retry Full Text.');
            throw error;
        }).finally(() => { fullJob = null; fullController = null; schedule(); });
        return fullJob;
    }
    function resize() {
        if (destroyed) return;
        suspended = false;
        if (innerWidth !== measuredWidth) {
            heights.fill(0); measuredWidth = innerWidth; widest = 0;
            rebuild().catch(error => { if (error.name !== 'AbortError') console.warn('Text resize:', error); });
            return;
        }
        schedule();
    }
    function wholeDocumentAction(event) {
        if (!(event.ctrlKey || event.metaKey) || event.altKey || !['a', 'f', 'p'].includes(event.key.toLowerCase()) ||
            event.target.closest?.('input, textarea, [contenteditable=true]') ||
            typeof fgCurrentView === 'function' && fgCurrentView() !== 'text') return;
        event.preventDefault(); event.stopImmediatePropagation();
        const key = event.key.toLowerCase();
        fullText().then(() => {
            if (key === 'p') window.print();
            else if (key === 'a') {
                const range = document.createRange(); range.selectNodeContents(root);
                getSelection().removeAllRanges(); getSelection().addRange(range);
            } else {
                const message = document.createElement('div');
                message.className = 'fg-window-notice'; message.setAttribute('role', 'status');
                message.textContent = 'Full Text ready. Press Ctrl+F / ⌘F again to search the complete trace. ';
                const close = document.createElement('button');
                close.type = 'button'; close.textContent = 'Dismiss'; close.onclick = () => message.remove();
                message.appendChild(close); root.before(message);
            }
        }).catch(error => console.warn('Full Text:', error));
    }
    retry.addEventListener('click', () => { first = end = 0; schedule(); });
    full.addEventListener('click', () => fullText().catch(error => console.warn('Full Text:', error)));
    addEventListener('scroll', schedule, { passive: true });
    addEventListener('resize', resize);
    document.addEventListener('selectionchange', schedule);
    document.addEventListener('keydown', wholeDocumentAction, true);
    showStatus();
    return { model, navigation, root, rebuild, ensureLine, fullText, resize, dispose, anchor, suppressedSources,
        reportFilterError(message) { filterError = message; showStatus(); },
        invalidateHeights() { heights.fill(0); },
        suspend() { suspended = true; context.hidden = true; controller?.abort(); fullController?.abort(); },
        get visibleNumbers() { return sequence.filter(item => typeof item === 'number'); },
        get mountedLimit() { return limit; },
        get settled() { return currentJob; } };
}
