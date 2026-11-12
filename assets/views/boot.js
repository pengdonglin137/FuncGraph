/* FuncGraph views, boot (global scope).
 * The text view is the page itself; other views register in window.fgViews:
 *   fgViews[name] = { el: <container id>, init: () => api }   api: { resize(), revealLine?(n) }
 * __tlBoot() (timeline) is defined right after this file when --timeline is used. */
(function () {
    const views = window.fgViews = window.fgViews || {};
    const apis = {};
    const pending = {};
    const pausedSources = {};
    let current = 'text';
    let textScroll = 0;
    let textFailed = false;
    const get = k => (typeof lsGet === 'function' ? lsGet(k) : null);
    const set = (k, v) => { if (typeof lsSet === 'function') lsSet(k, v); };
    if (typeof __tlBoot === 'function' || document.getElementById('tlData')) {
        views.timeline = { el: 'timelineView', data: ['tlData'], init: () => __tlBoot(), steps: () => __tlSteps(),
            staged: window.fgData.external('tlData') || window.fgData.length('tlData') >= 1024 * 1024 };
    }
    if (fgTextDeferred()) {
        const chunked = !!document.getElementById('fgTextChunks');
        function* textSteps() {
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
                    for (let first = 0; first < catalog.count; first += catalog.chunk_size) {
                        const count = Math.min(catalog.chunk_size, catalog.count - first);
                        const html = yield { label: 'Loading Text rows: ' + (first + 1) + '–' + (first + count) + ' / ' + catalog.count + '…',
                            load: signal => window.fgData.loadTextChunk(first, { signal }) };
                        yield* append(html, count, catalog.paint_rows, first, catalog.count);
                    }
                } else {
                    yield* append(window.fgData.read('fgText'), null, null, 0, 0);
                }
                const rows = fragment.querySelectorAll('.line-container');
                const model = yield* fgPrepareTextModel(rows);
                const navigation = yield* fgPrepareTextNavigation(rows, model);
                yield { label: 'Finishing Text layout…', paint: true };
                content.classList.toggle('text-layout-auto', chunked && window.CSS?.supports('content-visibility: auto') &&
                    CSS.supports('contain-intrinsic-block-size: calc(2 * (max(13px * 1.5 + 4px, 21px) + 2px))') &&
                    CSS.supports('selector(:has(*))'));
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
                return {};
            } finally {
                parser.replaceChildren();
                fragment.replaceChildren();
            }
        }
        views.text = { el: 'content', data: chunked ? [] : ['fgText'], asynchronous: chunked, staged: true, steps: textSteps, init: () => {
            const steps = textSteps();
            while (!steps.next().done) {}
            return {};
        } };
        document.querySelector('.controls').inert = true;
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
        const next = () => {
            state.timer = setTimeout(() => {
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
            }, 0);
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
    // Page chrome, looked up once: querySelectorAll over a 1M-element text view costs ~40 ms per call.
    let chrome = null;
    function pageChrome() {
        if (chrome) return chrome;
        const one = s => document.querySelector(s);
        const tabs = one('.view-tabs');
        chrome = {
            hide: [one('.controls'), document.getElementById('content'), document.getElementById('progressBar'),
                   document.getElementById('floatingToggle')].filter(Boolean),
            wide: [one('.container')].filter(Boolean),
            top: [one('.page-header'), tabs].filter(Boolean),
            tabs: tabs ? Array.from(tabs.querySelectorAll('.view-tab')) : [],
        };
        return chrome;
    }
    window.fgPageChrome = pageChrome;
    window.fgSwitchView = function (which) {
        if (which !== 'text' && !views[which]) return;
        if (which === current) {
            if (which === 'text' && !views.text) return;
            const api = open(which);
            if (apis[which] && apis[which].resize) apis[which].resize();
            return api;
        }
        if (current === 'text') {
            textScroll = window.scrollY;
            fgCloseTextControls();
        }
        if (typeof closeKeyboardHint === 'function') closeKeyboardHint();
        pausedSources[current] = fgCancelSources(document.getElementById(current === 'text' ? 'content' : views[current]?.el));
        apis[current]?.suspend?.();
        cancel(current);
        for (const [name, v] of Object.entries(views)) {
            const el = document.getElementById(v.el);
            if (el) el.classList.toggle('fg-view-on', name === which);
        }
        const off = which !== 'text';
        const pc = pageChrome();
        pc.hide.forEach(e => e.classList.toggle('fg-off', off));
        pc.wide.forEach(e => e.classList.toggle('fg-wide', off));
        // body class only for body's own rules (no descendant selectors on it)
        document.body.classList.toggle('tl-active', off);
        const max = off && document.body.classList.contains('tl-max');
        pc.top.forEach(e => e.classList.toggle('fg-off', max));
        document.body.dataset.view = which;
        pc.tabs.forEach(b => b.classList.toggle('active', b.dataset.view === which));
        current = which;
        set('view', which);
        const sourceRoot = document.getElementById(which === 'text' ? 'content' : views[which]?.el);
        for (const source of pausedSources[which] || []) {
            if (source.isConnected && sourceRoot?.contains(source)) fgFill(source);
        }
        delete pausedSources[which];
        if (which === 'text') {
            window.scrollTo(0, textScroll);
            if (views.text) return open('text');
        } else {
            window.scrollTo(0, 0);
            const api = open(which);
            if (apis[which] && apis[which].resize) apis[which].resize();
            return api;
        }
    };
    window.fgCurrentView = () => current;
    // Alt+click on a text line opens it in the timeline
    document.addEventListener('click', ev => {
        if (!ev.altKey || current !== 'text' || !views.timeline) return;
        const line = ev.target.closest && ev.target.closest('.line-container');
        if (!line) return;
        ev.preventDefault();
        ev.stopPropagation();
        const n = parseInt(line.getAttribute('data-line-number'), 10);
        Promise.resolve(window.fgSwitchView('timeline')).then(api => {
            if (api && current === 'timeline') api.revealLine(n);
        });
    }, true);
    document.addEventListener('keydown', ev => {
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
