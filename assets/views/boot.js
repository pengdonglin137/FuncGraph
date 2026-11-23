/* FuncGraph views, boot (global scope).
 * The text view is the page itself; other views register in window.fgViews:
 *   fgViews[name] = { el: <container id>, init: () => api }   api: { resize(), revealLine?(n) }
 * __tlBoot() (timeline) is defined right after this file when --timeline is used. */
(function () {
    const views = window.fgViews = window.fgViews || {};
    const apis = {};
    const pending = {};
    const pausedSources = {};
    const textApi = { resize: () => fgTextWindow?.resize(), suspend: () => fgTextWindow?.suspend() };
    let current = 'text';
    let textScroll = 0;
    let textScrollLeft = 0;
    let textSelection = null;
    let textFailed = false;
    let useTextWindow = false;
    let returnJump = null;
    let textJumpLine = null;
    const jumpButton = document.getElementById('textTimelineJump');
    const jumpTarget = jumpButton?.querySelector('.text-jump-target');
    const jumpNumber = jumpTarget?.querySelector('.text-jump-number');
    const returnButton = document.getElementById('fgJumpReturn');
    const jumpStatus = document.getElementById('fgJumpStatus');
    const returnHome = returnButton?.parentElement;
    const get = k => (typeof lsGet === 'function' ? lsGet(k) : null);
    const set = (k, v) => { if (typeof lsSet === 'function') lsSet(k, v); };
    if (typeof __tlBoot === 'function' || document.getElementById('tlData')) {
        views.timeline = { el: 'timelineView', data: ['tlData'], init: () => __tlBoot(), steps: () => __tlSteps(),
            staged: window.fgData.external('tlData') || window.fgData.length('tlData') >= 1024 * 1024 };
    }
    if (fgTextDeferred()) {
        const textCatalog = document.getElementById('fgTextChunks');
        const chunked = !!textCatalog;
        const renderer = new URL(location.href).searchParams.get('text');
        useTextWindow = chunked && (renderer === 'windowed' ||
            (renderer !== 'full' && textCatalog.dataset.defaultRenderer === 'windowed'));
        function* textSteps() {
            if (useTextWindow) {
                try { yield* fgWindowedTextSteps(); return textApi; }
                catch (error) { if (fgTextInitialized) textFailed = true; throw error; }
            }
            const content = document.getElementById('content');
            const fragment = document.createDocumentFragment();
            const parser = document.createElement('div');
            try {
                yield 'Preparing Text…';
                function* append(html, expected, paintRows, first, total) {
                    const marker = html.match(/^<div class="text-chunk" data-chunk-size="[1-9][0-9]*">/)?.[0];
                    if (html && !marker || expected && !html || paintRows && marker !== '<div class="text-chunk" data-chunk-size="' + paintRows + '">') {
                        throw new Error('Unrecognized Text chunk format');
                    }
                    let position = 0, count = 0;
                    while (position < html.length) {
                        const next = html.indexOf(marker, position + marker.length);
                        const end = next < 0 ? html.length : next;
                        parser.innerHTML = html.slice(position, end);
                        if (parser.childNodes.length !== 1 || !parser.firstElementChild?.classList.contains('text-chunk')) {
                            throw new Error('Invalid Text chunk boundary');
                        }
                        if (expected !== null) {
                            const rows = parser.firstElementChild.querySelectorAll('.line-container').length;
                            if (rows !== Math.min(paintRows, expected - count)) throw new Error('Text chunk row count mismatch');
                            count += rows;
                        }
                        fragment.appendChild(parser.firstElementChild);
                        position = end;
                        yield 'Preparing Text… ' + Math.floor(total ? (first + count) / total * 100 : position / html.length * 100) + '%';
                    }
                    if (expected !== null && count !== expected) throw new Error('Text chunk row count mismatch');
                }
                if (chunked) {
                    const catalog = window.fgData.textChunks();
                    const loadChunk = (first, signal) => window.fgData.loadTextChunk(first, { signal }).then(
                        html => ({ html }), error => ({ error }));
                    let prefetched = null;
                    for (let first = 0; first < catalog.count; first += catalog.chunk_size) {
                        const count = Math.min(catalog.chunk_size, catalog.count - first);
                        const result = yield { label: 'Loading Text rows: ' + (first + 1) + '–' + (first + count) + ' / ' + catalog.count + '…',
                            load: signal => {
                                const current = prefetched || loadChunk(first, signal);
                                const next = first + catalog.chunk_size;
                                prefetched = next < catalog.count ? loadChunk(next, signal) : null;
                                return current;
                            } };
                        if (result.error) throw result.error;
                        yield* append(result.html, count, catalog.paint_rows, first, catalog.count);
                    }
                } else {
                    yield* append(window.fgData.read('fgText'), null, null, 0, 0);
                }
                const rows = fragment.querySelectorAll('.line-container');
                const model = yield* fgPrepareTextModel(rows);
                const navigation = yield* fgPrepareTextNavigation(rows, model);
                if (fgTextOrder === 'task') yield* fgTextModel.prepareOrder(model, 'task');
                yield { label: 'Finishing Text layout…', paint: true };
                content.classList.toggle('text-layout-auto', chunked && fgSupportsTextLayout());
                content.replaceChildren(fragment);
                content.removeAttribute('data-fg-text');
                try { fgInitText(true, model, navigation); }
                catch (error) {
                    textFailed = true;
                    content.setAttribute('data-fg-text', 'true');
                    throw error;
                }
                window.fgData.release('fgText');
                window.fgData.release('fgTextIndex');
                window.fgData.releaseTextChunks();
                document.querySelector('.controls').inert = false;
                return textApi;
            } finally {
                parser.replaceChildren();
                fragment.replaceChildren();
            }
        }
        views.text = { el: 'content', data: chunked ? [] : ['fgText'], asynchronous: chunked, staged: true, steps: textSteps, init: () => {
            const steps = textSteps();
            while (!steps.next().done) {}
            return textApi;
        } };
        document.querySelector('.controls').inert = true;
        const windowButton = document.getElementById('textWindowBtn');
        if (windowButton) windowButton.hidden = !chunked;
    }
    function stopScheduled(state) {
        cancelAnimationFrame(state.frame);
        clearTimeout(state.timer);
    }
    function complete(state, api) {
        stopScheduled(state);
        apis[state.name] = api || {};
        delete pending[state.name];
        state.el.removeAttribute('aria-busy');
        if (current === state.name && apis[state.name].resize) apis[state.name].resize();
        console.log('[' + state.name + '] init ' + (performance.now() - state.start).toFixed(0) + ' ms (staged)');
        state.resolve(apis[state.name]);
        return apis[state.name];
    }
    function backToText() {
        if (returnJump?.to === current) { returnFromJump(); return; }
        const next = current === 'text' && views.text ? 'timeline' : 'text';
        window.fgSwitchView(next);
        const tab = pageChrome().tabs.find(button => button.dataset.view === next);
        if (tab) tab.focus({ preventScroll: true });
    }
    function loadingContent(el, message, failed) {
        el.textContent = '';
        const box = document.createElement('div');
        box.className = 'fg-loading';
        const status = document.createElement('div');
        status.className = 'fg-loading-status';
        status.setAttribute('role', failed ? 'alert' : 'status');
        status.setAttribute('aria-live', failed ? 'assertive' : 'polite');
        status.setAttribute('aria-atomic', 'true');
        status.textContent = message;
        const back = document.createElement('button');
        back.type = 'button';
        back.textContent = el.id === 'content' ? 'Back to Timeline' : 'Back to Text';
        back.addEventListener('click', backToText);
        box.append(status, back);
        el.appendChild(box);
        return status;
    }
    function fail(state, error) {
        stopScheduled(state);
        if (state.steps) state.steps.return();
        delete apis[state.name];
        delete pending[state.name];
        state.el.removeAttribute('aria-busy');
        loadingContent(state.el, 'Unable to open ' + state.name + ': ' + error.message, true);
        if (state.loadingData || state.name === 'text') {
            const retry = document.createElement('button');
            retry.type = 'button';
            const reload = textFailed && state.name === 'text';
            retry.textContent = reload ? 'Reload report' : 'Retry';
            retry.addEventListener('click', () => reload ? window.location.reload() : window.fgSwitchView(state.name));
            state.el.querySelector('.fg-loading').appendChild(retry);
        }
        if (state.name === 'text' && useTextWindow) {
            const full = document.createElement('button');
            full.type = 'button';
            full.textContent = 'Open Full Text';
            full.addEventListener('click', () => fgOpenTextRenderer('full'));
            state.el.querySelector('.fg-loading').appendChild(full);
        }
        state.controller.abort();
        state.resolve(null);
        console.error('[' + state.name + '] startup failed:', error);
    }
    function cancel(name) {
        const state = pending[name];
        if (!state) return;
        stopScheduled(state);
        delete pending[name];
        state.controller.abort();
        if (state.steps) state.steps.return();
        state.el.removeAttribute('aria-busy');
        state.el.textContent = '';
        state.resolve(null);
    }
    function updateStatus(state, step) {
        const label = typeof step === 'string' ? step : step.label;
        if (state.label !== label) {
            state.label = label;
            state.status.textContent = label;
        }
        return typeof step !== 'string' && step.paint === true;
    }
    function waitForStep(state, step) {
        if (typeof step !== 'object' || typeof step.load !== 'function') return false;
        Promise.resolve().then(() => step.load(state.controller.signal)).then(value => {
            if (pending[state.name] !== state) return;
            state.input = value;
            schedule(state);
        }, error => { if (pending[state.name] === state) fail(state, error); });
        return true;
    }
    function schedule(state, first) {
        const run = () => {
            if (pending[state.name] !== state) return;
            const start = performance.now();
            try {
                let result;
                do {
                    const input = state.input;
                    state.input = undefined;
                    result = state.steps.next(input);
                    if (result.done) { complete(state, result.value); return; }
                    const paint = updateStatus(state, result.value);
                    if (waitForStep(state, result.value)) return;
                    if (paint) { schedule(state, true); return; }
                } while (performance.now() - start < 8);
                schedule(state);
            } catch (error) { fail(state, error); }
        };
        const next = () => {
            if (state.name === 'text' && useTextWindow && typeof window.scheduler?.postTask === 'function') {
                window.scheduler.postTask(run, { signal: state.controller.signal }).catch(error => {
                    if (pending[state.name] === state) fail(state, error);
                });
            } else state.timer = setTimeout(run, 0);
        };
        if (first) state.frame = requestAnimationFrame(() => {
            state.frame = requestAnimationFrame(next);
        });
        else next();
    }
    function prepare(state) {
        if (pending[state.name] !== state) return;
        try {
            if (!views[state.name].steps) { complete(state, views[state.name].init()); return; }
            state.steps = views[state.name].steps();
            const first = state.steps.next();
            if (first.done) complete(state, first.value);
            else {
                updateStatus(state, first.value);
                if (!waitForStep(state, first.value)) schedule(state, true);
            }
        } catch (error) { fail(state, error); }
    }
    function missingData(name) {
        return (views[name].data || []).filter(resource => !window.fgData.ready(resource));
    }
    function open(name) {
        if (name === 'text' && textFailed) return null;
        if (apis[name]) return apis[name];
        if (pending[name]) return pending[name].promise;
        const missing = missingData(name);
        if (!missing.length && (!views[name].staged || !views[name].steps)) return ensure(name);
        const state = { name, steps: null, controller: new AbortController(), loadingData: missing.length > 0,
            start: performance.now(), frame: 0, timer: 0, el: document.getElementById(views[name].el),
            resolve: null, promise: null, status: null, label: null };
        state.promise = new Promise(resolve => { state.resolve = resolve; });
        pending[name] = state;
        state.el.setAttribute('aria-busy', 'true');
        state.status = loadingContent(state.el, (missing.length ? 'Loading ' : 'Preparing ') + name + '…', false);
        if (missing.length) {
            Promise.all(missing.map(resource => window.fgData.load(resource, { signal: state.controller.signal }))).then(() => {
                prepare(state);
            }, error => { if (pending[name] === state) fail(state, error); });
        } else prepare(state);
        return state.promise;
    }
    function ensure(name) {
        if (name === 'text' && textFailed) return null;
        if (views[name].asynchronous) return apis[name] || open(name);
        const state = pending[name];
        if (state) {
            if (!state.steps) return state.promise;
            stopScheduled(state);
            try {
                let result;
                do { result = state.steps.next(); } while (!result.done);
                return complete(state, result.value);
            } catch (error) { fail(state, error); throw error; }
        }
        if (missingData(name).length) return open(name);
        if (!apis[name]) {
            const t0 = performance.now();
            apis[name] = views[name].init() || {};
            console.log('[' + name + '] init ' + (performance.now() - t0).toFixed(0) + ' ms');
        }
        return apis[name];
    }
    window.fgView = ensure;
    window.fgEnsureText = () => views.text ? open('text') : {};
    window.fgEnsureTextLine = function (number, options = {}) {
        const signal = options.signal;
        const check = () => fgValidateTextLine(number, null, signal);
        const validate = element => fgValidateTextLine(number, element, signal);
        const obtain = () => {
            check();
            const read = () => {
                check();
                const renderer = fgTextWindow || apis.text;
                return renderer?.ensureLine ? renderer.ensureLine(number, options) :
                    fgTextDeferred() ? null : document.getElementById('L' + number);
            };
            const reveal = fgRevealTextEvent(number);
            return reveal?.then ? reveal.then(read) : read();
        };
        check();
        const canRead = !fgTextDeferred() || apis.text?.ensureLine;
        const value = canRead ? obtain() : Promise.resolve(open('text')).then(obtain);
        if (!value?.then) return validate(value);
        return new Promise((resolve, reject) => {
            const cleanup = () => signal?.removeEventListener('abort', cancel);
            const cancel = () => { cleanup(); reject(new DOMException('Text navigation canceled', 'AbortError')); };
            signal?.addEventListener('abort', cancel, { once: true });
            Promise.resolve(value).then(element => {
                cleanup();
                try { resolve(validate(element)); } catch (error) { reject(error); }
            }, error => { cleanup(); reject(error); });
            if (signal?.aborted) cancel();
        });
    };
    // Page chrome, looked up once: querySelectorAll over a 1M-element text view costs ~40 ms per call.
    let chrome = null;
    function pageChrome() {
        if (chrome) return chrome;
        const one = s => document.querySelector(s);
        const tabs = one('.view-tabs');
        chrome = {
            hide: [one('.controls'), document.getElementById('content'), document.getElementById('progressBar'),
                   document.getElementById('textWindowLoading'),
                   document.getElementById('floatingToggle')].filter(Boolean),
            wide: [one('.container')].filter(Boolean),
            top: [one('.page-header'), tabs].filter(Boolean),
            tabs: tabs ? Array.from(tabs.querySelectorAll('.view-tab')) : [],
            textViewport: one('#textViewport'),
        };
        return chrome;
    }
    window.fgPageChrome = pageChrome;
    function clearCaretSelection() {
        const selection = window.getSelection();
        const active = document.activeElement;
        if (selection?.isCollapsed && !active?.matches('input, textarea') && !active?.isContentEditable) selection.removeAllRanges();
    }
    function suspendTextSelection() {
        const selection = window.getSelection();
        textSelection = null;
        if (selection?.rangeCount && !selection.isCollapsed) {
            const ranges = Array.from({length: selection.rangeCount}, (unused, index) => selection.getRangeAt(index).cloneRange());
            const first = ranges[0];
            textSelection = {ranges, backward: selection.anchorNode === first.endContainer && selection.anchorOffset === first.endOffset};
            selection.removeAllRanges();
        } else {
            clearCaretSelection();
        }
    }
    function restoreTextSelection() {
        const saved = textSelection;
        textSelection = null;
        if (!saved) return;
        const ranges = saved.ranges.filter(range => !range.collapsed && range.startContainer.isConnected && range.endContainer.isConnected);
        const selection = window.getSelection();
        if (!ranges.length || !selection) return;
        selection.removeAllRanges();
        if (ranges.length === 1 && saved.backward && selection.setBaseAndExtent) {
            const range = ranges[0];
            selection.setBaseAndExtent(range.endContainer, range.endOffset, range.startContainer, range.startOffset);
        } else {
            for (const range of ranges) selection.addRange(range);
        }
    }
    function switchView(which, nextReturn = null) {
        if (which !== 'text' && !views[which]) return;
        if (which !== 'text' && current !== 'text') clearCaretSelection();
        if (which === current) {
            if (which === 'text' && !views.text) return;
            const api = open(which);
            if (apis[which] && apis[which].resize) apis[which].resize();
            return api;
        }
        if (current === 'text') {
            suspendTextSelection();
            fgCancelTextRowGestures();
            fgCancelWindowHandoff();
            fgCancelTextNavigation();
            textScroll = window.scrollY;
            textScrollLeft = pageChrome().textViewport?.scrollLeft || 0;
            fgCloseTextControls();
        }
        if (typeof fgCloseHelp === 'function') fgCloseHelp(false);
        pausedSources[current] = fgCancelSources(document.getElementById(current === 'text' ? 'content' : views[current]?.el));
        apis[current]?.suspend?.();
        cancel(current);
        setReturnJump(nextReturn);
        for (const [name, v] of Object.entries(views)) {
            const el = document.getElementById(v.el);
            if (el) el.classList.toggle('fg-view-on', name === which);
        }
        const off = which !== 'text';
        const pc = pageChrome();
        pc.hide.forEach(e => e.classList.toggle('fg-off', off));
        if (pc.textViewport) {
            pc.textViewport.style.height = off ? '0px' : '';
            pc.textViewport.style.overflow = off ? 'hidden' : '';
            pc.textViewport.setAttribute('aria-hidden', String(off));
        }
        pc.wide.forEach(e => e.classList.toggle('fg-wide', off));
        // body class only for body's own rules (no descendant selectors on it)
        document.body.classList.toggle('tl-active', off);
        const max = off && document.body.classList.contains('tl-max');
        pc.top.forEach(e => e.classList.toggle('fg-off', max));
        document.body.dataset.view = which;
        pc.tabs.forEach(tab => {
            const active = tab.dataset.view === which;
            tab.classList.toggle('active', active);
            if (active) tab.setAttribute('aria-current', 'true');
            else tab.removeAttribute('aria-current');
        });
        current = which;
        set('view', which);
        const sourceRoot = document.getElementById(which === 'text' ? 'content' : views[which]?.el);
        fgResumeSourceLayout(sourceRoot);
        for (const source of pausedSources[which] || []) {
            if (source.isConnected && sourceRoot?.contains(source)) fgFill(source);
        }
        delete pausedSources[which];
        if (which === 'text') {
            fgPrepareTextLayout();
            restoreTextSelection();
            if (pc.textViewport) pc.textViewport.scrollLeft = textScrollLeft;
            window.scrollTo(0, textScroll);
            if (views.text) {
                const result = open('text');
                apis.text?.resize?.();
                return result;
            }
        } else {
            window.scrollTo(0, 0);
            const api = open(which);
            if (apis[which] && apis[which].resize) apis[which].resize();
            return api;
        }
    }
    function setReturnJump(jump) {
        returnJump = jump;
        if (jumpStatus?.textContent) jumpStatus.textContent = '';
        jumpStatus?.classList.remove('fg-jump-pending');
        if (!returnButton) return;
        returnButton.hidden = !jump;
        if (jump) {
            const viewLabel = jump.from === 'text' ? 'Text' : 'Timeline';
            const label = 'Return to ' + viewLabel;
            returnButton.firstElementChild.textContent = label;
            returnButton.lastElementChild.textContent = '← ' + viewLabel;
            returnButton.setAttribute('aria-label', label);
            returnButton.title = label;
        }
    }
    function beginTraceJump(which, line = null, origin = null) {
        const jump = which !== current && returnButton ? { from: current, to: which, line, origin,
            scroll: window.scrollY, fullscreen: apis[current]?.traceJumpFullscreen?.(), filter: window.fgFilterState.text,
            viewport: current === 'timeline' && origin ? [innerWidth, innerHeight] : null } : null;
        return switchView(which, jump);
    }
    function isJumpOriginVisible(origin, minimumTop = 0) {
        if (!origin?.isConnected || origin.matches(':disabled')) return false;
        const bounds = origin.getBoundingClientRect(), viewport = window.visualViewport;
        const viewportLeft = viewport?.offsetLeft ?? 0, viewportTop = viewport?.offsetTop ?? 0;
        const left = Math.max(bounds.left, viewportLeft), top = Math.max(bounds.top, viewportTop, minimumTop);
        const right = Math.min(bounds.right, viewportLeft + (viewport?.width ?? innerWidth));
        const bottom = Math.min(bounds.bottom, viewportTop + (viewport?.height ?? innerHeight));
        return left < right && top < bottom && origin.contains(document.elementFromPoint((left + right) / 2, (top + bottom) / 2));
    }
    function restoreJumpFocus(origin, minimumTop = 0) {
        if (!isJumpOriginVisible(origin, minimumTop)) return false;
        origin.focus({ preventScroll: true });
        return document.activeElement === origin;
    }
    function restoreTimelineJumpFocus(jump) {
        const { origin, viewport } = jump;
        if (restoreJumpFocus(origin)) return true;
        if (!viewport || (viewport[0] === innerWidth && viewport[1] === innerHeight) ||
            !origin?.isConnected || origin.matches(':disabled') || !origin.getClientRects().length ||
            getComputedStyle(origin).visibility !== 'visible') return false;
        origin.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
        return restoreJumpFocus(origin);
    }
    function textJumpInset() {
        const inset = Math.max(0, document.querySelector('.controls')?.getBoundingClientRect().bottom || 0);
        return fgTextWindow?.taskContextBottom(inset) ?? inset;
    }
    function placeTraceReturn(fullscreenToolbar) {
        if (!returnButton) return;
        if (fullscreenToolbar && returnJump?.to === 'timeline') {
            if (returnButton.parentElement !== fullscreenToolbar) {
                fullscreenToolbar.prepend(returnButton);
                fullscreenToolbar.append(jumpStatus);
            }
        } else if (returnButton.parentElement !== returnHome) {
            returnHome.append(returnButton, jumpStatus);
        }
    }
    async function returnFromJump() {
        const jump = returnJump;
        if (!jump || current !== jump.to) return;
        if (document.fullscreenElement?.contains(returnButton)) {
            try { await document.exitFullscreen(); }
            catch (_) { return; }
            if (returnJump !== jump || current !== jump.to) return;
        }
        const previousTextJob = jump.from === 'text' ? fgTextWindow?.settled : null;
        switchView(jump.from);
        if (jump.from === 'text') {
            setTextJumpLine(jump.line);
            const textJob = fgTextWindow?.settled;
            if (textJob && textJob !== previousTextJob) {
                const textTab = pageChrome().tabs.find(tab => tab.dataset.view === 'text');
                textTab?.focus({ preventScroll: true });
                try { await textJob; } catch (_) { return; }
                if (current !== 'text' || document.activeElement !== textTab || fgTextWindow?.settled !== textJob) return;
            }
            const row = jump.line === null ? null : document.getElementById('L' + jump.line);
            if (row?.hasAttribute('tabindex') && !fgTextDeferred() && restoreJumpFocus(row, textJumpInset())) {
                if (fgTextInitialized && keyboardSelectedLine !== row) fgSelectMountedTextLine(row, false, false);
                return;
            }
        } else if (jump.from === 'timeline') {
            if (jump.scroll) window.scrollTo({ top: jump.scroll, behavior: 'instant' });
            const fullscreenExit = jump.fullscreen && apis.timeline?.restoreTraceJumpFullscreen?.(jump.fullscreen);
            if (restoreTimelineJumpFocus(jump)) return;
            if (fullscreenExit) { fullscreenExit.focus(); return; }
        }
        pageChrome().tabs.find(tab => tab.dataset.view === jump.from)?.focus({ preventScroll: true });
    }
    window.fgSwitchView = which => switchView(which);
    window.fgBeginTraceJump = beginTraceJump;
    window.fgReturnFromPendingTextJump = () => {
        if (current !== 'text' || returnJump?.from !== 'timeline' || returnJump.to !== current ||
            !fgTextWindow?.jumpPending || !fgTextLineRequest?.current()) return false;
        returnFromJump();
        return true;
    };
    window.fgReturnToTextOrigin = number => {
        const jump = returnJump;
        if (current !== 'timeline' || jump?.from !== 'text' || jump.line !== number ||
            !jump.origin || document.getElementById('L' + number) !== jump.origin ||
            jump.filter !== window.fgFilterState.text) return false;
        returnFromJump();
        return true;
    };
    window.fgPlaceTraceReturn = placeTraceReturn;
    window.fgSetTraceJumpStatus = (message, pending = false) => {
        if (!jumpStatus || returnJump?.to !== current ||
            jumpStatus.textContent === message && jumpStatus.classList.contains('fg-jump-pending') === pending) return false;
        jumpStatus.classList.toggle('fg-jump-pending', pending);
        jumpStatus.textContent = message;
        return true;
    };
    function setTextJumpLine(number) {
        if (!jumpButton || number === textJumpLine) return;
        textJumpLine = number;
        jumpButton.disabled = number === null;
        const suffix = number === null ? '' : ' · L' + number;
        jumpNumber.textContent = number === null ? '' : 'L' + number;
        jumpTarget.hidden = number === null;
        jumpButton.setAttribute('aria-label', 'In Timeline' + suffix);
        jumpButton.title = number === null ? 'Select a trace row first' : 'Locate trace line ' + number + ' in Timeline';
    }
    window.fgSetTextJumpLine = setTextJumpLine;
    function textLineAt(target) {
        const line = target.closest?.('#content .line-container[data-line-number]');
        const number = Number(line?.dataset.lineNumber);
        return Number.isSafeInteger(number) && number > 0 ? number : null;
    }
    function jumpToTimeline(number, keyboard = false) {
        if (number === null) return;
        setTextJumpLine(number);
        const row = document.getElementById('L' + number);
        const origin = row && isJumpOriginVisible(row, textJumpInset()) ? row : null;
        const ready = beginTraceJump('timeline', number, origin);
        const jump = returnJump;
        const tab = pageChrome().tabs.find(tab => tab.dataset.view === 'timeline');
        tab?.focus({ preventScroll: true });
        Promise.resolve(ready).then(api => {
            if (api && current === 'timeline' && returnJump === jump)
                api.revealLine(number, keyboard && document.activeElement === tab);
        });
    }
    jumpButton?.addEventListener('click', event => {
        if (current === 'text') jumpToTimeline(textJumpLine, event.detail === 0);
    });
    returnButton?.addEventListener('click', returnFromJump);
    if (jumpButton) document.addEventListener('focusin', event => {
        if (current !== 'text') return;
        const number = textLineAt(event.target);
        if (number !== null) setTextJumpLine(number);
    });
    window.fgCurrentView = () => current;
    document.addEventListener('click', ev => {
        if (current !== 'text' || !jumpButton) return;
        const number = textLineAt(ev.target);
        if (number === null) return;
        if (!ev.altKey) {
            if (!ev.ctrlKey && !ev.metaKey && !ev.shiftKey && window.getSelection()?.isCollapsed !== false) setTextJumpLine(number);
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        jumpToTimeline(number);
    }, true);
    if (jumpButton) document.addEventListener('keydown', event => {
        if (event.key !== 'Enter' || !event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
            event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.repeat || current !== 'text') return;
        const row = event.target;
        if (!row.matches?.('#content .line-container[data-line-number]') || row.isContentEditable ||
            fgTextDeferred() || window.getSelection()?.isCollapsed === false) return;
        const number = textLineAt(row);
        if (number === null) return;
        event.preventDefault();
        jumpToTimeline(number, true);
    });
    document.addEventListener('keydown', ev => {
        if (ev.key === 'Escape' && current === 'text' && fgWindowHandoff && !fgWindowHandoff.signal.aborted) {
            fgCancelWindowHandoff();
            ev.preventDefault();
            ev.stopImmediatePropagation();
            return;
        }
        if (ev.key !== 'Escape' || !pending[current]) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        backToText();
    }, true);
    window.addEventListener('DOMContentLoaded', () => {
        const initial = window.fgInitialView;
        try {
            const name = initial ? initial.name : (!location.hash && get('view'));
            if (name && name !== 'text' && views[name]) window.fgSwitchView(name);
            else if (views.text) window.fgSwitchView('text');
        } finally {
            if (initial && initial.style) initial.style.remove();
            delete window.fgInitialView;
        }
    });
})();
