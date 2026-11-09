/* FuncGraph views, boot (global scope).
 * The text view is the page itself; other views register in window.fgViews:
 *   fgViews[name] = { el: <container id>, init: () => api }   api: { resize(), revealLine?(n) }
 * __tlBoot() (timeline) is defined right after this file when --timeline is used. */
(function () {
    const views = window.fgViews = window.fgViews || {};
    const apis = {};
    const pending = {};
    let current = 'text';
    let textScroll = 0;
    const get = k => (typeof lsGet === 'function' ? lsGet(k) : null);
    const set = (k, v) => { if (typeof lsSet === 'function') lsSet(k, v); };
    if (typeof __tlBoot === 'function' || document.getElementById('tlData')) {
        const data = document.getElementById('tlData');
        const first = data && data.firstChild;
        const length = first && first === data.lastChild ? first.length : (data ? data.textContent.length : 0);
        views.timeline = { el: 'timelineView', init: () => __tlBoot(), steps: () => __tlSteps(), staged: length >= 1024 * 1024 };
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
        window.fgSwitchView('text');
        const tab = pageChrome().tabs.find(button => button.dataset.view === 'text');
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
        back.textContent = 'Back to Text';
        back.addEventListener('click', backToText);
        box.append(status, back);
        el.appendChild(box);
        return status;
    }
    function fail(state, error) {
        stopScheduled(state);
        delete apis[state.name];
        delete pending[state.name];
        state.el.removeAttribute('aria-busy');
        loadingContent(state.el, 'Unable to open ' + state.name + ': ' + error.message, true);
        state.resolve(null);
        console.error('[' + state.name + '] startup failed:', error);
    }
    function cancel(name) {
        const state = pending[name];
        if (!state) return;
        stopScheduled(state);
        state.steps.return();
        delete pending[name];
        state.el.removeAttribute('aria-busy');
        state.el.textContent = '';
        state.resolve(null);
    }
    function schedule(state, first) {
        const next = () => {
            state.timer = setTimeout(() => {
                if (pending[state.name] !== state) return;
                const start = performance.now();
                try {
                    let result;
                    do {
                        result = state.steps.next();
                        if (result.done) { complete(state, result.value); return; }
                        if (state.label !== result.value) {
                            state.label = result.value;
                            state.status.textContent = result.value;
                        }
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
    function open(name) {
        if (apis[name]) return apis[name];
        if (pending[name]) return pending[name].promise;
        if (!views[name].staged || !views[name].steps) return ensure(name);
        const state = { name, steps: views[name].steps(), start: performance.now(), frame: 0, timer: 0,
            el: document.getElementById(views[name].el), resolve: null, promise: null, status: null, label: null };
        state.promise = new Promise(resolve => { state.resolve = resolve; });
        pending[name] = state;
        state.el.setAttribute('aria-busy', 'true');
        state.status = loadingContent(state.el, 'Preparing ' + name + '…', false);
        try {
            const first = state.steps.next();
            if (first.done) complete(state, first.value);
            else { state.label = first.value; state.status.textContent = first.value; schedule(state, true); }
        } catch (error) { fail(state, error); }
        return state.promise;
    }
    function ensure(name) {
        const state = pending[name];
        if (state) {
            stopScheduled(state);
            try {
                let result;
                do { result = state.steps.next(); } while (!result.done);
                return complete(state, result.value);
            } catch (error) { fail(state, error); throw error; }
        }
        if (!apis[name]) {
            const t0 = performance.now();
            apis[name] = views[name].init() || {};
            console.log('[' + name + '] init ' + (performance.now() - t0).toFixed(0) + ' ms');
        }
        return apis[name];
    }
    window.fgView = ensure;
    // Page chrome, looked up once: querySelectorAll over a 1M-element text view costs ~40 ms per call.
    let chrome = null;
    function pageChrome() {
        if (chrome) return chrome;
        const one = s => document.querySelector(s);
        const tabs = one('.view-tabs');
        chrome = {
            hide: [one('.controls'), document.getElementById('content'), document.getElementById('progressBar'),
                   document.getElementById('floatingToggle'), document.getElementById('keyboardHint')].filter(Boolean),
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
            if (which === 'text') return;
            const api = open(which);
            if (apis[which] && apis[which].resize) apis[which].resize();
            return api;
        }
        if (current === 'text') textScroll = window.scrollY;
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
        if (which === 'text') {
            window.scrollTo(0, textScroll);
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
        } finally {
            if (initial && initial.style) initial.style.remove();
            delete window.fgInitialView;
        }
    });
})();
