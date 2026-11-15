const _lsPrefix = (function () {
    let hash = 0;
    const title = document.title || '';
    for (let index = 0; index < title.length; index++) hash = ((hash << 5) - hash + title.charCodeAt(index)) | 0;
    return 'fg_' + (hash >>> 0).toString(36) + '_';
})();
let currentTheme = localStorage.getItem('theme') ||
    (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
document.documentElement.setAttribute('data-theme', currentTheme);
(function () {
    const guides = document.getElementById('fgTreeGuides');
    if (guides && localStorage.getItem(_lsPrefix + 'foldGuides') === 'false') guides.sheet.disabled = true;
})();
(function () {
    const script = document.currentScript;
    if (!script.dataset.views) return;
    const views = (script.dataset.views || '').split(' ');
    let name = script.dataset.defaultView || 'text';
    if (script.dataset.deferredText && location.hash) name = 'text';
    if ((name === 'text' || script.dataset.deferredText) && !location.hash) {
        const saved = localStorage.getItem(_lsPrefix + 'view');
        if (saved && (views.includes(saved) || (script.dataset.deferredText && saved === 'text'))) name = saved;
    }
    const initial = window.fgInitialView = { name, style: null };
    if (name === 'text') return;
    const style = document.createElement('style');
    style.textContent = '#content { content-visibility: hidden; }';
    document.head.appendChild(style);
    initial.style = style;
})();
(function () {
    const script = document.currentScript;
    if (!script.dataset.inlineText || script.dataset.deferredText ||
        (window.fgInitialView && window.fgInitialView.name !== 'text') ||
        !window.CSS?.supports('content-visibility: hidden')) return;
    const style = document.createElement('style');
    style.textContent = '#content { content-visibility: hidden; }';
    document.head.appendChild(style);
    const startup = window.fgInlineTextStartup = {
        start() {
            document.getElementById('textStartupStatus').hidden = false;
            document.getElementById('textViewport').setAttribute('aria-busy', 'true');
        },
        reveal() {
            style.remove();
            document.getElementById('textStartupStatus')?.remove();
        },
        finish() {
            style.remove();
            document.getElementById('textStartupStatus')?.remove();
            document.getElementById('textViewport')?.removeAttribute('aria-busy');
            delete window.fgInlineTextStartup;
        }
    };
    window.addEventListener('load', () => { setTimeout(() => startup.finish(), 0); }, { once: true });
})();
