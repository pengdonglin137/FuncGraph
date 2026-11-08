/* FuncGraph Timeline, boot (global scope). __tlBoot() is defined right after this file. */
(function () {
    let tl = null;
    let textScroll = 0;
    const get = k => (typeof lsGet === 'function' ? lsGet(k) : null);
    const set = (k, v) => { if (typeof lsSet === 'function') lsSet(k, v); };
    function ensure() {
        if (!tl) {
            const t0 = performance.now();
            tl = __tlBoot();
            console.log('[timeline] init ' + (performance.now() - t0).toFixed(0) + ' ms');
        }
        return tl;
    }
    window.fgSwitchView = function (which) {
        const toTl = which === 'timeline';
        const active = document.body.classList.contains('tl-active');
        if (toTl === active) { if (toTl) ensure().resize(); return; }
        if (toTl) textScroll = window.scrollY;
        document.body.classList.toggle('tl-active', toTl);
        document.querySelectorAll('.view-tab').forEach(b => b.classList.toggle('active', b.dataset.view === which));
        set('view', which);
        if (toTl) {
            window.scrollTo(0, 0);
            ensure().resize();
        } else {
            window.scrollTo(0, textScroll);
        }
    };
    // Alt+click on a text line opens it in the timeline
    document.addEventListener('click', ev => {
        if (!ev.altKey || document.body.classList.contains('tl-active')) return;
        const line = ev.target.closest && ev.target.closest('.line-container');
        if (!line) return;
        ev.preventDefault();
        ev.stopPropagation();
        const n = parseInt(line.getAttribute('data-line-number'), 10);
        window.fgSwitchView('timeline');
        ensure().revealLine(n);
    }, true);
    window.addEventListener('DOMContentLoaded', () => {
        if (get('view') === 'timeline' && !location.hash) window.fgSwitchView('timeline');
    });
})();
