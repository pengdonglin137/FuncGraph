let fgTextWindow = null;
let fgWindowHandoff = null;

function fgSetTextModeButton(windowed) {
    const button = document.getElementById('textWindowBtn');
    const label = windowed ? 'Full Text' : 'Windowed Text';
    button.setAttribute('aria-label', label);
    button.firstElementChild.textContent = label;
    button.lastElementChild.textContent = windowed ? label : 'Windowed';
    button.title = windowed ? 'Mount all rows for browser Find, selection across the whole trace and printing' :
        'Render only the visible Text window';
}

function fgToggleTextRenderer() {
    const button = document.getElementById('textWindowBtn');
    if (fgTextCompactMedia.matches && button.closest('.controls').dataset.panel) fgSetTextControlPanel('');
    if (fgTextWindow) return fgTextWindow.fullText().catch(fgReportFullTextError);
    return fgOpenTextRenderer('windowed');
}

function fgTextModeNotice(message, controller = null, label = '', total = null) {
    const notice = document.getElementById('textWindowLoading');
    const status = document.getElementById('textWindowStatus');
    const progress = document.getElementById('textWindowProgress');
    const action = document.getElementById('textWindowCancel');
    const button = document.getElementById('textWindowBtn');
    const previousFocus = document.activeElement;
    const hide = () => { notice.hidden = true; };
    notice.hidden = false;
    status.textContent = message;
    if (total !== null) { progress.max = total || 1; progress.value = 0; }
    progress.hidden = total === null;
    action.textContent = controller ? 'Cancel ' + label : 'Dismiss';
    action.onclick = controller ? () => controller.abort() : hide;
    if (controller) {
        button.disabled = true;
        if (previousFocus === button) action.focus({ preventScroll: true });
    }
    return {
        update(value, message) {
            progress.value = value;
            if (message) status.textContent = message;
        },
        finish(message = '') {
            const restoreFocus = document.activeElement === action;
            button.disabled = false;
            if (message && fgCurrentView() === 'text') {
                status.textContent = message;
                progress.hidden = true;
                action.textContent = 'Dismiss';
                action.onclick = hide;
            } else { status.textContent = ''; hide(); action.onclick = null; }
            if (restoreFocus && !message && fgCurrentView() === 'text') {
                const target = previousFocus.isConnected && previousFocus.getClientRects().length ? previousFocus : button;
                target.focus({ preventScroll: true });
            }
        },
    };
}

function fgReportFullTextError(error) { if (error.name !== 'AbortError') console.warn('Full Text:', error); }

function fgYieldFullText(signal) {
    if (typeof globalThis.scheduler?.postTask === 'function') {
        return globalThis.scheduler.postTask(() => {}, { priority: 'background', signal });
    }
    return new Promise(resolve => {
        if (typeof MessageChannel !== 'function') { setTimeout(resolve, 0); return; }
        const channel = new MessageChannel();
        channel.port1.onmessage = () => {
            channel.port1.close();
            channel.port2.close();
            resolve();
        };
        channel.port2.postMessage(null);
    });
}

function fgOpenTextRenderer(renderer) {
    const root = document.getElementById('content');
    const model = fgDomTextModels.get(root);
    if (renderer === 'windowed' && !fgTextDeferred() && !fgTextWindow && fgTextNavigation &&
        [4, 5, 6].includes(model?.version) && fgData.textChunks()?.count === model.records.length) {
        return fgReturnToTextWindow(root, model);
    }
    const url = new URL(location.href);
    url.searchParams.set('text', renderer);
    lsSet('view', 'text');
    location.assign(url.href);
}

function fgCancelWindowHandoff() { fgWindowHandoff?.abort(); }

function fgTextViewportAnchor(root, viewport) {
    const bounds = viewport.getBoundingClientRect();
    const inset = Math.max(0, document.querySelector('.controls')?.getBoundingClientRect().bottom || 0);
    const top = Math.max(inset, bounds.top);
    const left = Math.min(innerWidth - 1, Math.max(0, bounds.left + 12));
    for (let vertical = top + 1; vertical < Math.min(top + 180, bounds.bottom, innerHeight); vertical += 10) {
        const element = document.elementFromPoint(left, vertical);
        const row = element?.closest('.line-container') || element?.closest('.expanded-content')?.previousElementSibling;
        if (row?.classList.contains('line-container') && root.contains(row)) {
            const number = Number(row.dataset.lineNumber);
            if (Number.isSafeInteger(number) && number > 0) {
                const relativeTop = row.getBoundingClientRect().top - inset;
                return { number, relativeTop, gap: Math.max(0, relativeTop) };
            }
        }
    }
    return null;
}

function fgRestoreTextViewport(viewport, anchor, atTop, scrollLeft) {
    if (atTop) scrollTo({ top: 0, behavior: 'instant' });
    else if (anchor) {
        const row = document.getElementById('L' + anchor.number);
        const inset = Math.max(0, document.querySelector('.controls')?.getBoundingClientRect().bottom || 0);
        if (row?.getClientRects().length) scrollBy({ top: row.getBoundingClientRect().top - inset - anchor.gap, behavior: 'instant' });
    }
    viewport.scrollLeft = scrollLeft;
}

async function fgReturnToTextWindow(root, model) {
    if (fgWindowHandoff) { fgCancelWindowHandoff(); return false; }
    if (root.hasAttribute('aria-busy') || window.fgFilterProgress && !window.fgFilterProgress.error) {
        fgTextModeNotice('Wait for the current Text operation.');
        return false;
    }
    const viewport = root.closest('#textViewport');
    const anchor = fgTextViewportAnchor(root, viewport);
    const selected = keyboardSelectedLine;
    const initialScroll = scrollY;
    let scrollLeft;
    const state = { filter: window.fgFilterState, generation: fgFilterGeneration, order: fgTextOrder,
        sort: fgTextSort, events: fgTextEventsVisible, revision: textGuideRevision, request: fgTextLineRequest };
    const pending = fgWindowHandoff = new AbortController();
    const notice = fgTextModeNotice('Preparing Windowed Text…', pending, 'Windowed Text');
    let renderer, feedback;
    try {
        renderer = fgCreateTextWindow(model, fgTextNavigation, root, () => {
            if (pending.signal.aborted || fgCurrentView() !== 'text' || fgWindowHandoff !== pending) {
                throw new DOMException('Text window handoff canceled', 'AbortError');
            }
            let moved = Math.abs(scrollY - initialScroll) > innerHeight / 2;
            if (moved && anchor) {
                const row = document.getElementById('L' + anchor.number);
                const inset = Math.max(0, document.querySelector('.controls')?.getBoundingClientRect().bottom || 0);
                moved = !row || Math.abs(row.getBoundingClientRect().top - inset - anchor.relativeTop) > innerHeight / 2;
            }
            if (state.filter !== window.fgFilterState || state.generation !== fgFilterGeneration ||
                state.order !== fgTextOrder || state.sort !== fgTextSort || state.events !== fgTextEventsVisible ||
                state.revision !== textGuideRevision || state.request !== fgTextLineRequest || moved) {
                throw new Error('Text changed; try Windowed Text again');
            }
            scrollLeft = viewport.scrollLeft;
            fgCancelTextNavigation();
            fgCancelTextRowGestures();
            fgCancelSources(root);
            fgTextWindow = renderer;
        });
        await renderer.rebuild(anchor?.number, { signal: pending.signal, reveal: () => false,
            durationBase: state.sort ? fgTextNavigation.order : null });
        fgRestoreTextViewport(viewport, anchor, initialScroll === 0, scrollLeft);
        if (keyboardSelectedLine === selected && !selected?.isConnected) keyboardSelectedLine = null;
        return true;
    } catch (error) {
        if (fgTextWindow !== renderer) renderer?.dispose();
        if (error.name !== 'AbortError') feedback = 'Windowed Text not opened: ' + error.message;
        return false;
    } finally {
        if (fgWindowHandoff === pending) fgWindowHandoff = null;
        notice.finish(feedback);
    }
}

function fgRefreshTextWindow(options = {}) {
    return fgTextWindow?.rebuild(undefined, options).catch(error => {
        if (error.name !== 'AbortError') console.warn('Text window:', error);
    });
}

function fgWaitTextWindow(pending, signal) {
    return new Promise((resolve, reject) => {
        const cleanup = () => signal?.removeEventListener('abort', cancel);
        const cancel = () => { cleanup(); reject(new DOMException('Text operation cancelled', 'AbortError')); };
        signal?.addEventListener('abort', cancel, { once: true });
        Promise.resolve(pending).then(value => { cleanup(); resolve(value); }, error => { cleanup(); reject(error); });
        if (signal?.aborted) cancel();
    });
}

function fgInitialTextDestination(model, matches = null) {
    const hash = location.hash, request = fgTextLineRequest;
    const number = request ? (request.current() ? request.number : null) : fgTextAnchorNumber(hash);
    if (number === null || matches?.has(number) === false) return null;
    const reveal = () => request ? request.current() : !fgTextLineRequest && location.hash === hash;
    const blockers = request?.revealFolds && fgTextFoldBlockers(fgTextModel.locate(model, number)?.record);
    return { number, reveal, unfold: blockers?.length ? blockers : undefined, navigationSignal: request?.controller.signal };
}

async function fgApplyWindowFilter(text, generation, startupSignal = null) {
    const renderer = fgTextWindow;
    fgFilterController?.abort();
    const controller = fgFilterController = new AbortController();
    const abortStartup = () => controller.abort();
    startupSignal?.addEventListener('abort', abortStartup, { once: true });
    if (startupSignal?.aborted) abortStartup();
    const previous = { state: window.fgFilterState, sort: fgTextSort, graph: _graphFoldIds,
        anchor: renderer.anchor(), suppressed: new Set(renderer.suppressedSources) };
    let committed = false;
    const progress = message => {
        if (generation !== fgFilterGeneration || fgFilterController !== controller || controller.signal.aborted) return;
        fgSetFilterProgress({ text, message, error: false });
        renderer.reportFilterProgress(message);
    };
    const cancelled = () => {
        if (renderer === fgTextWindow && fgFilterController === controller) renderer.reportFilterProgress('');
    };
    controller.signal.addEventListener('abort', cancelled, { once: true });
    renderer.reportFilterError('');
    updateFilterTags(text);
    try {
        const data = fgData.read('fgTextIndex');
        const reused = startupSignal && previous.state.text === text && fgIndexedFilterProjections.get(previous.state)?.get(data);
        if (!reused) progress('Preparing full-trace filter… Previous result retained.');
        const result = reused || await fgIndexedFilter(data, text, controller.signal, progress);
        if (controller.signal.aborted || generation !== fgFilterGeneration || fgFilterController !== controller || renderer !== fgTextWindow) return null;
        progress('Preparing filtered Text rows… Previous result retained.');
        const preparation = {
            signal: controller.signal, filter: result, resetHeights: true,
            commit() {
                _graphFoldIds = result.graphSets || {};
                fgTextSort = result.sortDir || '';
                renderer.suppressedSources.clear();
                if (result.lines) for (const [key, expanded] of Object.entries(expandedLines)) {
                    if (expanded) renderer.suppressedSources.add(key);
                }
                committed = true;
            }
        };
        while (true) {
            const events = renderer.eventRevision, interactions = renderer.interactionRevision;
            const destination = startupSignal && fgInitialTextDestination(renderer.model, result.lines);
            try {
                if (destination) {
                    const { number, ...target } = destination;
                    if (await renderer.rebuild(number, { ...preparation, ...target }) === false) {
                        await renderer.rebuild(undefined, preparation);
                    }
                } else await renderer.rebuild(previous.anchor, preparation);
                break;
            } catch (error) {
                const retained = () => !controller.signal.aborted && generation === fgFilterGeneration &&
                    fgFilterController === controller && renderer === fgTextWindow &&
                    interactions === renderer.interactionRevision;
                if (error.name === 'AbortError' && destination?.navigationSignal?.aborted && retained()) continue;
                if (error.name !== 'AbortError' || events === renderer.eventRevision || !retained()) throw error;
                let pending;
                do {
                    pending = renderer.settled;
                    try { await fgWaitTextWindow(pending, controller.signal); }
                    catch (refreshError) {
                        if (refreshError.name !== 'AbortError' || !retained()) throw refreshError;
                    }
                    if (!retained()) throw error;
                } while (pending !== renderer.settled);
                progress('Preparing filtered Text rows… Previous result retained.');
            }
        }
        if (controller.signal.aborted || generation !== fgFilterGeneration || fgFilterController !== controller || renderer !== fgTextWindow) return null;
        if (reused && window.fgFilterState === previous.state) fgSetFilterProgress(null);
        else {
            window.fgFilterProgress = null;
            fgNotifyFilter(result.text, result.lines);
        }
        fgShowFilterCount(result.visible, result.total);
        return true;
    } catch (error) {
        if (generation !== fgFilterGeneration || fgFilterController !== controller) return null;
        if (error.name === 'AbortError') { renderer.reportFilterProgress(''); fgSetFilterProgress(null); return null; }
        if (committed) {
            fgTextSort = previous.sort; _graphFoldIds = previous.graph;
            fgNotifyFilter(previous.state.text, previous.state.lines);
            renderer.suppressedSources.clear();
            for (const key of previous.suppressed) renderer.suppressedSources.add(key);
            try { await renderer.rebuild(previous.anchor); } catch (restoreError) {
                if (restoreError.name !== 'AbortError') console.warn('Previous Text window:', restoreError);
            }
        }
        fgSetFilterProgress({ text, error: true, message: 'Filter not applied: ' + error.message });
        renderer.reportFilterError('Filter not applied: ' + error.message + '. Previous result retained; Apply again to retry.');
        return false;
    } finally {
        startupSignal?.removeEventListener('abort', abortStartup);
        controller.signal.removeEventListener('abort', cancelled);
        if (fgFilterController === controller) fgFilterController = null;
    }
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
        const startupFilter = !window.fgFilterProgress && window.fgFilterState.text;
        let projected = false;
        if (startupFilter) {
            const input = document.getElementById('filterInput');
            if (input) input.value = startupFilter;
            projected = yield { label: 'Preparing the filtered Text window…',
                load: signal => fgApplyWindowFilter(startupFilter, fgFilterGeneration, signal) };
        }
        if (!projected) yield { label: 'Loading the first Text window…', load: async signal => {
            for (;;) {
                const filter = window.fgFilterProgress?.text ?? window.fgFilterState.text;
                const destination = !filter && fgInitialTextDestination(model);
                if (!destination) return renderer.rebuild(undefined, { signal });
                const { number, reveal, unfold, navigationSignal } = destination;
                if (unfold) {
                    try {
                        if (await renderer.rebuild(number, { signal, navigationSignal, reveal, unfold })) return;
                    } catch (error) {
                        if (signal.aborted || !navigationSignal.aborted || error.name !== 'AbortError') throw error;
                        continue;
                    }
                }
                return renderer.rebuild(number, { signal, reveal });
            }
        } };
        const filterText = window.fgFilterProgress?.text ?? window.fgFilterState.text;
        if (filterText && filterText !== startupFilter) {
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

function fgCreateTextWindow(model, navigation, root, beforeMount = null) {
    if (![4, 5, 6].includes(model.version) || fgData.textChunks()?.count !== model.records.length) {
        throw new Error('Windowed Text requires a complete presentation index');
    }
    const limit = 160, overscan = 32;
    let heights = new Float64Array(model.records.length);
    let positions = new Map();
    const timePositions = {
        size: model.records.length,
        get(number) {
            if (Number.isSafeInteger(number) && number > 0) return fgTextModel.locate(model, number)?.ordinal;
        },
        has(number) { return this.get(number) !== undefined; },
        keys() { return fgTextModel.order(model, 'time').order.values(); },
    };
    const suppressedSources = new Set();
    let sequence = [], tree = new Float64Array(1), sizes = [], plan = null, taskGroups = [], rankedOrder = null, preparedRank = null;
    let copyVisibility = null;
    let first = 0, end = 0, generation = 0, controller = null, frame = null, pendingStart = null;
    let destroyed = false, suspended = false, fullJob = null, fullController = null, currentJob = null, navigating = 0, rebuilding = 0;
    let measuredWidth = innerWidth, widest = 0, filterError = '', suspendedReading = null;
    let rebuildController = null, rankingJob = null, needsRebuild = false;
    let eventRevision = 0, interactionRevision = 0;
    let jumpProgress = null;
    let mounted = false;
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
    const expandButton = document.querySelector('.btn-expand');
    notice.append(status, retry);
    const context = document.createElement('div'), contextHeading = document.createElement('div');
    context.className = 'fg-window-context'; context.hidden = true;
    context.setAttribute('role', 'note'); context.setAttribute('aria-label', 'Current task');
    contextHeading.className = 'task-group-head'; context.appendChild(contextHeading);
    function mount() {
        if (mounted) return;
        beforeMount?.();
        beforeMount = null;
        mounted = true;
        if (expandButton) expandButton.title = 'Expand source for all matching rows; load each block as you scroll';
        fgSetTextModeButton(true);
        root.before(notice);
        (root.closest('#textViewport') || root).before(context);
        root.replaceChildren(top, body, bottom);
        root.classList.remove('text-layout-auto', 'text-layout-measured');
        root.classList.add('fg-windowed');
        if (controls) controlsObserver.observe(controls);
        addEventListener('scroll', schedule, { passive: true });
        addEventListener('resize', resize);
        document.addEventListener('selectionchange', schedule);
        document.addEventListener('keydown', wholeDocumentAction, true);
    }

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
    function retainRank(result, candidate, direction) {
        const count = model.records.length;
        if (!Array.isArray(result?.order) || result.order.length !== count ||
                !(result.ordinals instanceof Uint32Array) || result.ordinals.length !== count) {
            throw new Error('Invalid complete Text duration order');
        }
        const seen = new Uint8Array(count);
        for (let index = 0; index < count; index++) {
            const ordinal = result.ordinals[index];
            if (ordinal >= count || seen[ordinal] || model.records[ordinal].row[0] !== result.order[index]) {
                throw new Error('Invalid complete Text duration order');
            }
            seen[ordinal] = 1;
        }
        return preparedRank = { plan: candidate, direction, order: result.order, ordinals: result.ordinals };
    }
    function updateTaskContext(inset, viewportY) {
        if (destroyed || suspended || fgTextOrder !== 'task' || fgTextSort || !taskGroups.length) {
            if (!context.hidden) context.hidden = true;
            return;
        }
        const toolbarInset = inset ?? readingInset();
        const position = (viewportY ?? -root.getBoundingClientRect().top) + toolbarInset;
        if (position < 0 || position >= prefix(sequence.length)) {
            if (!context.hidden) context.hidden = true;
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
        if (!group || position <= prefix(group.index) + 0.5) {
            if (!context.hidden) context.hidden = true;
            return;
        }
        if (contextHeading.textContent !== group.text) {
            contextHeading.textContent = group.text;
            contextHeading.title = group.text;
        }
        context.style.top = toolbarInset + 'px';
        if (context.hidden) context.hidden = false;
    }
    function estimateRowHeight() {
        const style = getComputedStyle(root);
        return Math.max(parseFloat(style.getPropertyValue('--fg-text-source-row-min')),
            parseFloat(style.getPropertyValue('--fg-text-font-size')) * 1.5 +
            2 * parseFloat(style.getPropertyValue('--fg-text-pad-y')));
    }
    function setRowHeight(index, height) {
        const delta = height - sizes[index];
        if (!delta) return;
        sizes[index] = height;
        for (let position = index + 1; position < tree.length; position += position & -position) tree[position] += delta;
    }
    function measureWidth() {
        widest = Math.max(widest, body.scrollWidth);
        const width = Math.max(root.parentElement.clientWidth, widest) + 'px';
        if (root.style.minWidth !== width) root.style.minWidth = width;
    }
    function recordRowHeight(index, height) {
        if (!(height > 0) || !Number.isFinite(height)) return;
        setRowHeight(index, height);
        const item = sequence[index];
        if (typeof item === 'number') heights[fgTextModel.locate(model, item).ordinal] = height;
    }
    function measure(keptViewport = null, changes = null, elements = body.children) {
        if (destroyed || suspended || !body.childElementCount) return;
        const viewportY = Math.max(0, scrollY - offset());
        const anchor = keptViewport?.anchor ?? indexAt(viewportY);
        const within = keptViewport?.within ?? viewportY - prefix(anchor);
        if (changes) {
            for (const [index, height] of changes) recordRowHeight(index, height);
        } else {
            for (const element of elements) {
                const index = +element.dataset.windowIndex;
                if (index >= first && index < end) recordRowHeight(index, element.getBoundingClientRect().height);
            }
        }
        measureWidth();
        padding();
        const intraRow = keptViewport ? Math.min(within, Math.max(0, sizes[anchor] - 1)) : within;
        const target = offset() + prefix(anchor) + intraRow;
        if ((keptViewport || scrollY >= offset()) && Math.abs(target - scrollY) > 0.5) scrollTo({ top: target, behavior: 'instant' });
        updateTaskContext();
    }
    const observer = new ResizeObserver(entries => {
        if (destroyed || suspended) return;
        const changes = [];
        for (const { target, borderBoxSize } of entries) {
            if (target.parentElement !== body) continue;
            const index = +target.dataset.windowIndex;
            if (index < first || index >= end) continue;
            const height = borderBoxSize?.[0]?.blockSize;
            if (!Number.isFinite(height)) { measure(); schedule(); return; }
            if (height > 0 && height !== sizes[index]) changes.push([index, height]);
        }
        if (changes.length) measure(null, changes);
        else measureWidth();
        schedule();
    });
    const controlsObserver = new ResizeObserver(schedule);
    const controls = document.querySelector('.controls');
    function setJumpPending(pending, progress = null) {
        jumpProgress = pending ? progress : null;
        if (body.classList.contains('fg-window-jump-pending') === pending) return;
        if (pending) body.style.setProperty('--fg-window-pending-height', Math.max(0, prefix(end) - prefix(first)) + 'px');
        else body.style.removeProperty('--fg-window-pending-height');
        body.classList.toggle('fg-window-jump-pending', pending);
        context.classList.toggle('fg-window-jump-pending', pending);
        root.dispatchEvent(new Event('fg-guides-change'));
    }
    async function paint(start, signal, { candidate = null, commit = null, revealJump = false } = {}) {
        check(signal);
        const paintSequence = candidate ? candidate.sequence : sequence;
        const stop = Math.min(paintSequence.length, start + limit);
        const id = ++generation;
        controller?.abort();
        const own = controller = new AbortController();
        pendingStart = start;
        const abort = () => own.abort();
        signal?.addEventListener('abort', abort, { once: true });
        let progressTimer, progressMessage;
        const stopProgress = () => {
            clearTimeout(progressTimer);
            if (progressMessage && status.textContent === progressMessage) showStatus();
            progressMessage = undefined;
        };
        own.signal.addEventListener('abort', stopProgress, { once: true });
        retry.hidden = true;
        if (mounted) root.setAttribute('aria-busy', 'true');
        try {
            const retained = new Map(Array.from(body.children).filter(element => element.dataset.windowLine)
                .map(element => [+element.dataset.windowLine, element]));
            const missing = new Map();
            const catalog = fgData.textChunks();
            const required = new Set(paintSequence.slice(start, stop).filter(item => typeof item === 'number' && !retained.has(item)));
            for (const number of required) {
                const ordinal = fgTextModel.locate(model, number).ordinal;
                const chunk = Math.floor(ordinal / catalog.chunk_size);
                if (!missing.has(chunk)) missing.set(chunk, new Map());
                const block = Math.floor(ordinal / catalog.paint_rows);
                if (!missing.get(chunk).has(block)) missing.get(chunk).set(block, { number, ordinal });
            }
            const groups = Array.from(missing.values(), group => Array.from(group.values()));
            const retainedCount = retained.size;
            let lastLoaded = -1;
            const reportProgress = () => {
                const loaded = retained.size - retainedCount;
                if (own.signal.aborted || id !== generation || loaded >= required.size) return;
                if (lastLoaded !== loaded) {
                    lastLoaded = loaded;
                    if (!(revealJump && jumpProgress?.(loaded, required.size))) {
                        progressMessage = 'Loading Text rows: ' + loaded + ' / ' + required.size + '…';
                        showStatus(progressMessage);
                    }
                }
                progressTimer = setTimeout(reportProgress, 500);
            };
            if (required.size) progressTimer = setTimeout(reportProgress, 300);
            let next = 0;
            const collect = async () => {
                while (next < groups.length) {
                    const group = groups[next++];
                    const blocks = await fgData.loadTextBlocks(group.map(item => item.ordinal), { signal: own.signal });
                    check(own.signal);
                    for (let index = 0; index < group.length; index++) {
                        check(own.signal);
                        const loaded = await fgLoadTextBlock(model, group[index].number, { signal: own.signal, block: blocks[index] });
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
                }
            };
            await Promise.all(Array.from({ length: Math.min(4, groups.length) }, collect));
            check(own.signal);
            if (id !== generation) throw new DOMException('Text window replaced', 'AbortError');
            for (const number of required) if (!retained.has(number)) throw new Error('Text window is missing line ' + number);
            if (!mounted) mount();
            if (candidate) {
                if (commit) commit();
                navigation.reorder(candidate.order);
                navigation.setVisibility(candidate.visibility);
                ({ sequence, positions, taskGroups, sizes, tree, plan, rankedOrder } = candidate);
                copyVisibility = sequence.length * 8 >= model.records.length ? candidate.visibility : null;
                if (preparedRank === rankedOrder) preparedRank = null;
                if (candidate.resetHeights) heights.fill(0);
                root.classList.toggle('grouped', candidate.mode === 'task' && !candidate.direction);
                const button = document.getElementById('textOrderBtn');
                if (button) button.textContent = 'Order: ' + (candidate.direction ? 'duration ' +
                    (candidate.direction === 'desc' ? '↓' : '↑') : candidate.mode === 'task' ? 'by task' : 'time');
                needsRebuild = false;
            }
            const sharedFirst = Math.max(start, first), sharedEnd = Math.min(stop, end);
            const slides = !candidate && sharedFirst < sharedEnd;
            const leading = document.createDocumentFragment(), trailing = document.createDocumentFragment();
            const entering = slides ? [] : null;
            for (let index = start; index < stop; index++) {
                if (slides && index >= sharedFirst && index < sharedEnd) continue;
                const item = paintSequence[index];
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
                entering?.push(element);
                (slides && index >= sharedEnd ? trailing : leading).appendChild(element);
            }
            const evictedSources = fgPendingSources(body);
            const presented = entering ?? body.children;
            try {
                if (slides) {
                    for (let index = first; index < sharedFirst; index++) {
                        const element = body.firstElementChild;
                        observer.unobserve(element); element.remove();
                    }
                    for (let index = end; index > sharedEnd; index--) {
                        const element = body.lastElementChild;
                        observer.unobserve(element); element.remove();
                    }
                    if (leading.childElementCount) body.prepend(leading);
                    if (trailing.childElementCount) body.append(trailing);
                } else {
                    observer.disconnect();
                    body.replaceChildren(leading);
                }
                first = start; end = stop;
                padding();
                for (const element of presented) {
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
                    observer.observe(element, { box: 'border-box' });
                }
            } finally {
                for (const source of evictedSources) if (!body.contains(source)) fgCancelSource(source);
            }
            if (revealJump) setJumpPending(false);
            updateExpandableLines();
            measure(null, null, presented); showStatus();
        } catch (error) {
            own.abort();
            if (error.name !== 'AbortError' && id === generation) {
                showStatus('Text rows not loaded: ' + error.message + '. Previous window retained.');
                retry.hidden = false;
            }
            throw error;
        } finally {
            stopProgress();
            own.signal.removeEventListener('abort', stopProgress);
            signal?.removeEventListener('abort', abort);
            if (id === generation) {
                if (mounted) root.removeAttribute('aria-busy');
                pendingStart = null;
            }
        }
    }
    function schedule() {
        if (!mounted || frame !== null || destroyed || suspended) return;
        frame = requestAnimationFrame(() => {
            frame = null;
            if (destroyed || suspended) return;
            const viewportY = -root.getBoundingClientRect().top;
            updateTaskContext(undefined, viewportY);
            if (navigating || rebuilding || fullJob || needsRebuild) return;
            if (selected()) { showStatus('Selection pinned · clear selection to scroll, or choose Full Text'); return; }
            const at = indexAt(Math.max(0, viewportY));
            const last = indexAt(Math.max(0, viewportY + innerHeight));
            if (at >= first + 8 && last < end - 8 || first === 0 && at < overscan && last < end - 8 || end === sequence.length && at >= first + 8) return;
            const start = Math.max(0, Math.min(sequence.length - limit, at - overscan));
            if (pendingStart === start) return;
            if (first === start && end === Math.min(sequence.length, start + limit)) return;
            currentJob = paint(start).catch(error => { if (error.name !== 'AbortError') console.warn('Text window:', error); });
        });
    }
    function anchor() { return sequence[indexAt(Math.max(0, scrollY - offset()))]; }
    const readingInset = () => Math.max(0, controls?.getBoundingClientRect().bottom || 0);
    function captureReadingPosition() {
        if (!mounted || !sequence.length) return null;
        const position = scrollY + readingInset() - offset();
        if (position < 0 || position >= prefix(sequence.length)) return null;
        const index = indexAt(position), number = sequence[index];
        return typeof number === 'number' ? { number, within: position - prefix(index) } : null;
    }
    function rebuild(preferredNumber, options = {}) {
        if (options.eventRefresh) eventRevision++;
        else if (!options.filter) interactionRevision++;
        const previous = preferredNumber ?? options.reading?.number ?? anchor();
        const previousNeedsRebuild = needsRebuild;
        rebuildController?.abort();
        const own = rebuildController = new AbortController();
        const abort = () => own.abort();
        const signals = [options.signal, options.navigationSignal].filter(Boolean);
        for (const signal of signals) {
            signal.addEventListener('abort', abort, { once: true });
            if (signal.aborted) own.abort();
        }
        controller?.abort(); generation++;
        rankingJob = null; needsRebuild = true; rebuilding++;
        const mode = fgTextOrder, direction = options.filter ? options.filter.sortDir : fgTextSort;
        const matches = options.filter ? options.filter.lines : window.fgFilterState.lines;
        const unfold = options.unfold;
        const folds = unfold ? { ...foldedBlocks } : foldedBlocks;
        const commit = unfold ? () => {
            options.commit?.();
            for (const id of unfold) delete foldedBlocks[id];
        } : options.commit;
        if (unfold) for (const id of unfold) delete folds[id];
        const job = (async () => {
            if (suspended && options.filter) {
                if (own.signal.aborted || destroyed) throw new DOMException('Text window canceled', 'AbortError');
                options.commit?.();
                return;
            }
            check(own.signal);
            const candidate = fgTextModel.order(model, mode);
            let ranked = null;
            if (direction) {
                const compatible = value => value?.plan === candidate && value.direction === direction;
                ranked = compatible(rankedOrder) ? rankedOrder : compatible(preparedRank) ? preparedRank : null;
                if (!ranked) {
                    const base = options.durationBase ? {
                        order: options.durationBase,
                        ordinals: Uint32Array.from(options.durationBase, number => fgTextModel.locate(model, number).ordinal),
                    } : candidate;
                    let result;
                    if (candidate.order.length > 16384) {
                        if (mounted) root.setAttribute('aria-busy', 'true');
                        retry.hidden = true;
                        showStatus('Preparing complete duration order… Previous window retained.');
                        rankingJob = fgTextModel.rankDurationAsync(model, base, direction, own.signal, message => {
                            if (rebuildController === own && !own.signal.aborted) showStatus(message);
                        });
                        result = await rankingJob;
                        check(own.signal);
                        rankingJob = null;
                        showStatus('Loading sorted Text rows…');
                    } else result = fgTextModel.rankDuration(model, base, direction);
                    check(own.signal);
                    ranked = retainRank(result, candidate, direction);
                }
            }
            check(own.signal);
            const order = ranked ? ranked.order : candidate.order, ordinals = ranked ? ranked.ordinals : candidate.ordinals;
            const folding = fgTextModel.folding(model);
            const hidden = matches === null ? folding.hiddenMask(folds) : null;
            const visibleRecord = matches === null ? null : folding.visibility(folds, matches);
            const visibility = new Uint8Array(order.length);
            const estimate = estimateRowHeight();
            const prepared = { sequence: [], taskGroups: [], sizes: [],
                plan: candidate, rankedOrder: ranked, order, visibility, mode, direction,
                resetHeights: options.resetHeights };
            if (!plan && mode === 'time' && !direction && matches === null && hidden === null && fgTextEventsVisible) {
                prepared.sequence = order;
                prepared.positions = timePositions;
                prepared.sizes = new Array(order.length).fill(estimate);
                visibility.fill(1);
                prepared.tree = new Float64Array(order.length + 1);
                for (let index = 1; index < prepared.tree.length; index++) prepared.tree[index] = estimate * (index & -index);
            } else {
                const boundaries = direction ? [] : candidate.boundaries;
                let boundaryIndex = 0;
                const pendingBoundaries = [];
                for (let index = 0; index < order.length; index++) {
                    const number = order[index], ordinal = ordinals ? ordinals[index] : index;
                    while (boundaryIndex < boundaries.length && boundaries[boundaryIndex].at === index) {
                        const boundary = boundaries[boundaryIndex++];
                        if (boundary.kind === 'task-group-head') pendingBoundaries.length = 0;
                        pendingBoundaries.push(boundary);
                    }
                    const record = model.records[ordinal];
                    const visible = (visibleRecord ? visibleRecord(record) : hidden === null || !hidden[ordinal]) &&
                        (direction || mode !== 'task' || !candidate.hidden.has(number)) && fgTextEventVisible(record);
                    visibility[index] = visible ? 1 : 0;
                    if (!visible) continue;
                    if (pendingBoundaries.length) {
                        for (const boundary of pendingBoundaries) {
                            if (boundary.kind === 'task-group-head') prepared.taskGroups.push({ index: prepared.sequence.length, text: boundary.text });
                            prepared.sequence.push(boundary);
                            prepared.sizes.push(30);
                        }
                        pendingBoundaries.length = 0;
                    }
                    prepared.sequence.push(number);
                    prepared.sizes.push(options.resetHeights ? estimate : heights[ordinal] || estimate);
                }
                const sameSequence = prepared.sequence.length === sequence.length &&
                    prepared.sequence.every((item, index) => item === sequence[index]);
                prepared.positions = sameSequence ? positions : new Map();
                if (!sameSequence) for (let index = 0; index < prepared.sequence.length; index++) {
                    const number = prepared.sequence[index];
                    if (typeof number === 'number') prepared.positions.set(number, index);
                }
                prepared.tree = new Float64Array(prepared.sequence.length + 1);
                for (let index = 1; index < prepared.tree.length; index++) {
                    prepared.tree[index] += prepared.sizes[index - 1];
                    const parent = index + (index & -index);
                    if (parent < prepared.tree.length) prepared.tree[parent] += prepared.tree[index];
                }
            }
            if (unfold && !prepared.positions.has(previous)) { needsRebuild = previousNeedsRebuild; return false; }
            const anchorIndex = prepared.positions.get(previous) || 0;
            await paint(Math.max(0, Math.min(prepared.sequence.length - limit, anchorIndex - overscan)),
                own.signal, { candidate: prepared, commit, revealJump: options.revealJump });
            check(own.signal);
            if (options.reveal) {
                const row = prepared.positions.has(previous) && options.reveal() && document.getElementById('L' + previous);
                if (row) fgScrollTextLine(row, { block: 'center', behavior: 'instant', centerAfterSticky: true });
            } else if (options.reading && prepared.positions.has(previous)) {
                const within = Math.min(options.reading.within, Math.max(0, sizes[anchorIndex] - 1));
                scrollTo({ top: offset() + prefix(anchorIndex) + within - readingInset(), behavior: 'instant' });
            } else if (scrollY > offset()) scrollTo({ top: offset() + prefix(anchorIndex), behavior: 'instant' });
            return true;
        })();
        currentJob = job.catch(error => {
            if (rebuildController === own && unfold && needsRebuild) needsRebuild = previousNeedsRebuild;
            if (rebuildController === own && options.filter && needsRebuild) {
                needsRebuild = false;
                if (error.name === 'AbortError') showStatus();
            }
            if (rebuildController === own && needsRebuild && error.name !== 'AbortError') {
                showStatus('Text order not prepared: ' + error.message + '. Previous window retained; Retry.');
                retry.hidden = false;
            }
            throw error;
        }).finally(() => {
            for (const signal of signals) signal.removeEventListener('abort', abort);
            if (rebuildController === own) {
                rebuildController = null; rankingJob = null;
                if (mounted && !fullJob) root.removeAttribute('aria-busy');
            }
            rebuilding--; schedule();
        });
        return currentJob;
    }
    async function ensureLine(number, options = {}) {
        if (destroyed) return document.getElementById('L' + number);
        check(options.signal);
        while (rankingJob || options.revealFolds && rebuildController) {
            const pending = options.revealFolds && rebuildController ? currentJob : rankingJob;
            try {
                await fgWaitTextWindow(pending, options.signal);
            } catch (error) {
                check(options.signal);
                if (error.name !== 'AbortError' || rankingJob === pending) throw error;
            }
            check(options.signal);
        }
        const position = positions.get(number);
        if (position === undefined) {
            if (!options.revealFolds || window.fgFilterState.lines?.has(number) === false) return null;
            const unfold = fgTextFoldBlockers(fgTextModel.locate(model, number)?.record);
            if (!unfold.length) return null;
            await rebuild(number, { signal: options.signal, unfold, reveal: () => !options.deferScroll,
                revealJump: options.deferScroll });
            check(options.signal);
            return document.getElementById('L' + number);
        }
        const mounted = document.getElementById('L' + number);
        if (mounted) return mounted;
        navigating++;
        try {
            await paint(Math.max(0, Math.min(sequence.length - limit, position - overscan)), options.signal,
                { revealJump: options.deferScroll });
            check(options.signal);
            const row = document.getElementById('L' + number);
            if (row && !options.deferScroll) fgScrollTextLine(row, { block: 'center', behavior: 'instant' });
            return row;
        } finally { navigating--; }
    }
    async function expandSources() {
        while (rebuildController) {
            await currentJob.catch(() => {});
            check();
        }
        check();
        const viewportY = scrollY - offset();
        const anchor = indexAt(Math.max(0, viewportY));
        const keptViewport = viewportY >= 0 ? { anchor, within: viewportY - prefix(anchor) } : null;
        for (const number of positions.keys()) {
            if (fgTextModel.locate(model, number).record.source === null) continue;
            const lineId = 'line_' + (number - 1);
            if (expandedLines[lineId] !== true) saveExpandedState(lineId, true);
            suppressedSources.delete(lineId);
        }
        for (const wrapper of body.children) {
            const row = wrapper.querySelector('.line-container.expandable');
            if (row) fgProjectFoldLine(row, true);
        }
        measure(keptViewport); schedule();
    }
    async function collapseSources() {
        while (rebuildController) {
            await currentJob.catch(() => {});
            check();
        }
        check();
        const viewportY = scrollY - offset();
        const anchor = indexAt(Math.max(0, viewportY));
        const keptViewport = viewportY >= 0 ? { anchor, within: viewportY - prefix(anchor) } : null;
        const collapsed = new Set();
        let estimate;
        for (const lineId of Object.keys(expandedLines)) {
            if (expandedLines[lineId] !== true || !/^line_(0|[1-9][0-9]*)$/.test(lineId)) continue;
            const number = Number(lineId.slice(5)) + 1, index = positions.get(number);
            if (index === undefined) continue;
            const location = fgTextModel.locate(model, number);
            if (!location || location.record.source === null) continue;
            saveExpandedState(lineId, false);
            suppressedSources.delete(lineId);
            heights[location.ordinal] = 0;
            if (index < first || index >= end) setRowHeight(index, estimate ??= estimateRowHeight());
            else collapsed.add(lineId);
        }
        for (const wrapper of body.children) {
            const row = wrapper.querySelector('.line-container');
            if (collapsed.has(row?.dataset.lineId)) fgProjectFoldLine(row, true);
        }
        if (estimate !== undefined || collapsed.size) { measure(keptViewport); schedule(); }
    }
    function copyLines() {
        const lines = [], records = model.records;
        if (copyVisibility) {
            const ordinals = (rankedOrder || plan).ordinals;
            for (let index = 0; index < copyVisibility.length; index++) {
                if (copyVisibility[index]) lines.push(records[ordinals ? ordinals[index] : index].textContent);
            }
        } else {
            for (const number of sequence) {
                if (typeof number === 'number') lines.push(fgTextModel.locate(model, number).record.textContent);
            }
        }
        return lines;
    }
    function dispose() {
        interactionRevision++;
        destroyed = true; generation++; controller?.abort(); rebuildController?.abort(); fullController?.abort();
        rebuildController = null; rankingJob = null;
        observer.disconnect(); controlsObserver.disconnect(); cancelAnimationFrame(frame);
        fgCancelSources(body); body.replaceChildren();
        positions = new Map(); sequence = []; sizes = []; plan = null; taskGroups = []; rankedOrder = null; preparedRank = null;
        copyVisibility = null;
        heights = new Float64Array(0); tree = new Float64Array(1);
        removeEventListener('scroll', schedule); removeEventListener('resize', resize);
        document.removeEventListener('selectionchange', schedule);
        document.removeEventListener('keydown', wholeDocumentAction, true);
        if (!mounted) return;
        root.classList.remove('fg-windowed'); root.style.minWidth = '';
        root.removeAttribute('aria-busy');
        expandButton?.removeAttribute('title');
        fgSetTextModeButton(false);
        notice.remove(); context.remove();
    }
    async function fullText() {
        if (destroyed) return;
        if (fullJob) return fullJob;
        check();
        interactionRevision++;
        const loading = fullController = new AbortController();
        const catalog = fgData.textChunks();
        const progress = fgTextModeNotice('Preparing Full Text…', loading, 'Full Text', catalog.count);
        let feedback;
        fullJob = (async () => {
            controller?.abort(); rebuildController?.abort(); generation++;
            rebuildController = null; rankingJob = null;
            root.setAttribute('aria-busy', 'true');
            const fragment = document.createDocumentFragment();
            const filterState = window.fgFilterState, input = document.getElementById('filterInput');
            const filterGeneration = fgFilterGeneration, eventAtStart = eventRevision, interactionAtStart = interactionRevision;
            const canProjectFilter = () => !needsRebuild && !window.fgFilterProgress && fgFilterGeneration === filterGeneration &&
                window.fgFilterState === filterState && input?.value.trim() === filterState.text &&
                fgTextOrder === 'time' && !fgTextSort && eventRevision === eventAtStart && interactionRevision === interactionAtStart &&
                !Object.values(foldedBlocks).includes(true);
            const projectedVisibility = filterState.lines !== null && model.version >= 4 && canProjectFilter() ?
                { positions, hidden: [] } : null;
            let authoredBlocks = projectedVisibility || (fgTextOrder === 'time' && fgTextEventsVisible && !filterState.text &&
                !Object.values(foldedBlocks).includes(true)) ? new Map() : null;
            let sliceStart = performance.now();
            for (let firstOrdinal = 0; firstOrdinal < catalog.count; firstOrdinal += catalog.chunk_size) {
                const stop = Math.min(firstOrdinal + catalog.chunk_size, catalog.count);
                const ordinals = [];
                for (let ordinal = firstOrdinal; ordinal < stop; ordinal += catalog.paint_rows) ordinals.push(ordinal);
                const blocks = await fgData.loadTextBlocks(ordinals, { signal: loading.signal });
                for (let index = 0; index < ordinals.length; index++) {
                    check(loading.signal);
                    const ordinal = ordinals[index];
                    const block = await fgLoadTextBlock(model, model.records[ordinal].row[0],
                        { signal: loading.signal, block: blocks[index], countAuthoredRows: authoredBlocks !== null, projectedVisibility });
                    check(loading.signal);
                    if (block.authoredCounts) authoredBlocks?.set(block.element, block.authoredCounts);
                    else authoredBlocks = null;
                    fragment.appendChild(block.element);
                    progress.update(ordinal + block.count,
                        ordinal + block.count === catalog.count ? 'Finishing Full Text layout…' : null);
                    if (performance.now() - sliceStart >= 8 || ordinal + block.count === catalog.count) {
                        await fgYieldFullText(loading.signal);
                        check(loading.signal);
                        sliceStart = performance.now();
                    }
                }
            }
            const filterProjected = projectedVisibility && authoredBlocks && canProjectFilter();
            if (projectedVisibility && !filterProjected) {
                authoredBlocks = null;
                for (const row of projectedVisibility.hidden) {
                    row.style.removeProperty('display');
                    row.removeAttribute('style');
                    if (performance.now() - sliceStart >= 8) {
                        await fgYieldFullText(loading.signal);
                        check(loading.signal);
                        sliceStart = performance.now();
                    }
                }
            }
            const viewport = root.closest('#textViewport');
            const reading = fgTextViewportAnchor(root, viewport), atTop = scrollY === 0, scrollLeft = viewport.scrollLeft;
            const selectedNumber = keyboardSelectedLine && +keyboardSelectedLine.dataset.lineNumber;
            const selectFocus = root.contains(document.activeElement);
            fgCancelTextNavigation();
            fgCancelTextRowGestures();
            fgCancelSources(body);
            fullController = null;
            dispose(); fgTextWindow = null;
            root.classList.toggle('text-layout-auto', catalog.count >= 4096 && fgSupportsTextLayout());
            root.replaceChildren(fragment);
            if (fgTextOrder === 'task') fgReorder(root, 'task');
            else {
                root.classList.remove('grouped');
                navigation.reorder(fgTextModel.order(model, 'time').order);
                const button = document.getElementById('textOrderBtn');
                if (button) button.textContent = 'Order: time';
            }
            fgTextSort = '';
            if (filterProjected) {
                fgMountedFilterProjection = null;
                fgRefreshTextLayout(null, false, filterState.lines, null, false, authoredBlocks);
                updateExpandableLines();
            } else {
                restoreFoldedState(); restoreExpandedState(); applyFilter({ initializeLayout: true, authoredBlocks });
            }
            if (selectedNumber) fgSelectMountedTextLine(document.getElementById('L' + selectedNumber), false, selectFocus);
            fgRestoreTextViewport(viewport, reading, atTop, scrollLeft);
        })().catch(error => {
            if (!rebuildController && !destroyed && (!suspended || error.name === 'AbortError')) {
                if (error.name === 'AbortError') showStatus('Full Text loading canceled. Windowed Text retained.' +
                    (needsRebuild ? ' Retry the order if needed.' : ''));
                else {
                    feedback = 'Complete Text not loaded: ' + error.message + '. Windowed Text retained; ' +
                        (needsRebuild ? 'Retry the order or retry Full Text.' : 'retry Full Text.');
                    showStatus(feedback);
                }
                retry.hidden = !needsRebuild;
            }
            throw error;
        }).finally(() => {
            fullJob = null; fullController = null;
            progress.finish(feedback);
            if (!rebuildController) root.removeAttribute('aria-busy');
            schedule();
        });
        return fullJob;
    }
    function resize(event) {
        if (destroyed || suspended && event?.type === 'resize') return;
        const widthChanged = innerWidth !== measuredWidth;
        const reading = widthChanged && !needsRebuild ? suspendedReading : null;
        suspendedReading = null;
        suspended = false;
        if (widthChanged || needsRebuild) {
            heights.fill(0); measuredWidth = innerWidth; widest = 0;
            rebuild(undefined, { reading }).catch(error => { if (error.name !== 'AbortError') console.warn('Text resize:', error); });
            return;
        }
        schedule();
    }
    function wholeDocumentAction(event) {
        if (typeof fgCurrentView === 'function' && fgCurrentView() !== 'text') return;
        if (event.key === 'Escape' && fullController && !fullController.signal.aborted &&
            !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey) {
            event.preventDefault(); event.stopImmediatePropagation();
            fullController.abort();
            return;
        }
        if (!(event.ctrlKey || event.metaKey) || event.altKey || !['a', 'f', 'p'].includes(event.key.toLowerCase()) ||
            event.target.closest?.('input, textarea, [contenteditable=true]')) return;
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
        }).catch(fgReportFullTextError);
    }
    retry.addEventListener('click', () => {
        if (needsRebuild) rebuild().catch(error => { if (error.name !== 'AbortError') console.warn('Text order:', error); });
        else { first = end = 0; schedule(); }
    });
    if (!beforeMount) mount();
    showStatus();
    return { model, navigation, root, rebuild, ensureLine, expandSources, collapseSources, fullText, resize, dispose, anchor, suppressedSources, copyLines,
        setJumpPending,
        taskContextBottom(inset) {
            updateTaskContext(inset);
            return context.hidden ? inset : Math.max(inset, contextHeading.getBoundingClientRect().bottom);
        },
        reportFilterError(message) { filterError = message; showStatus(); },
        reportFilterProgress(message) { showStatus(message); },
        invalidateHeights() { heights.fill(0); },
        suspend() {
            suspendedReading = captureReadingPosition();
            interactionRevision++; suspended = true; context.hidden = true;
            controller?.abort(); rebuildController?.abort(); fullController?.abort();
        },
        get visibleNumbers() { return sequence.filter(item => typeof item === 'number'); },
        get jumpPending() { return body.classList.contains('fg-window-jump-pending'); },
        get mountedLimit() { return limit; },
        get eventRevision() { return eventRevision; },
        get interactionRevision() { return interactionRevision; },
        get settled() { return currentJob; } };
}
