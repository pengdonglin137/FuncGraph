/* FuncGraph views, boot (global scope).
 * The text view is the page itself; other views register in window.fgViews:
 *   fgViews[name] = { el: <container id>, init: () => api }   api: { resize(), revealLine?(n) }
 * __tlBoot() (timeline) is defined right after this file when --timeline is used. */
(function () {
    const views = window.fgViews = window.fgViews || {};
    const apis = {};
    let current = 'text';
    let textScroll = 0;
    const get = k => (typeof lsGet === 'function' ? lsGet(k) : null);
    const set = (k, v) => { if (typeof lsSet === 'function') lsSet(k, v); };
    if (typeof __tlBoot === 'function' || document.getElementById('tlData')) {
        views.timeline = { el: 'timelineView', init: () => __tlBoot() };
    }
    function ensure(name) {
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
        if (which === current) { if (which !== 'text') (ensure(which).resize || (() => {}))(); return; }
        if (current === 'text') textScroll = window.scrollY;
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
            const api = ensure(which);
            if (api.resize) api.resize();
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
        window.fgSwitchView('timeline');
        ensure('timeline').revealLine(n);
    }, true);
    window.addEventListener('DOMContentLoaded', () => {
        const tl = document.getElementById('tlData');
        if (tl && /"no_text":true/.test(tl.textContent.slice(-200)) && views.timeline) { window.fgSwitchView('timeline'); return; }
        const v = get('view');
        if (v && v !== 'text' && views[v] && !location.hash) window.fgSwitchView(v);
    });
})();
