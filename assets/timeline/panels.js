/* FuncGraph Timeline: detail panel, range summary, wiring, init */
function pill(cls, txt, title) { return '<span class="tl-pill ' + cls + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(txt) + '</span>'; }
function kv(k, v, cls, field) {
    const pair = '<span class="k"' + (field ? ' tabindex="-1"' : '') + '>' + k + '</span><span class="v' +
        (cls ? ' ' + cls : '') + '">' + v + '</span>';
    return field ? '<div class="tl-recorded-field" data-recorded-field="' + field + '">' + pair + '</div>' : pair;
}
function lineLink(number, label) {
    return '<button type="button" class="tl-link tl-detail-link" data-line="' + number + '">' +
        esc(label || 'line ' + number) + '</button>';
}
function detailToken(kind, value, title) { return '<span class="tl-code-' + kind + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(value) + '</span>'; }
function detailEvidenceHtml(label, content, className = '') {
    return '<details class="tl-context-note tl-value-evidence' + (className ? ' ' + className : '') + '"><summary>' +
        esc(label) + '</summary><div class="tl-value-evidence-body">' + content + '</div></details>';
}
function detailSymbolHtml(value) {
    const site = value.match(/^(.+?)(\+0[xX][\da-fA-F]+\/0[xX][\da-fA-F]+)(.*)$/);
    if (site) return detailToken('symbol', symbolLabel(site[1]), site[1]) + detailToken('offset', site[2]) + esc(site[3]);
    const address = value.match(/^(0[xX][\da-fA-F]+)(.*)$/);
    if (address) return detailToken('raw', address[1]) + esc(address[2]);
    return detailToken('symbol', symbolSiteLabel(value), value);
}
function detailValueHtml(value, parameters = false) {
    const tokens = /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b[A-Za-z_]\w*(?=\s*=(?!=))|(?<![\w.])[-+]?(?:0[xX][\da-fA-F]+|\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)(?![\w.])|\b(?:NULL|nullptr|true|false|void)\b/g;
    let html = '', offset = 0;
    for (const match of value.matchAll(tokens)) {
        const token = match[0], end = match.index + token.length;
        let kind = null;
        if (token[0] === '"' || token[0] === "'") kind = 'string';
        else if (/^[A-Za-z_]/.test(token) && /^\s*=/.test(value.slice(end))) kind = parameters ? 'parameter' : null;
        else if (/^[A-Za-z_]/.test(token)) kind = 'keyword';
        else kind = 'number';
        html += esc(value.slice(offset, match.index)) + (kind ? detailToken(kind, token) : esc(token));
        offset = end;
    }
    return html + esc(value.slice(offset));
}
function detailArgumentUnit(html) { return '<span class="tl-argument">' + html + '</span>'; }
const ENUM_NAME_PREVIEW = 8;
const ENUM_NAME_PAGE = 24;
const ENUM_FOLD_NAME_LIMIT = 65536;
const ENUM_FOLD_UNIT_LIMIT = 1024 * 1024;
const enumNameFilters = new WeakMap();
const enumRenderedPages = new WeakMap();
let enumFoldedNameCache = null;
let enumFeedbackFrame = 0;
function enumNamesOf(disclosure) {
    const {enumType, enumValue} = disclosure.dataset;
    return D.btf_detail?.enums?.[enumType]?.values?.[enumValue];
}
function enumNameRows(disclosure, names) {
    const recorded = disclosure.dataset.enumRecordedLabel;
    const shown = recorded ?? enumNamesOf(disclosure)?.[0];
    const basis = recorded === undefined ? 'BTF first match' : 'Recorded label';
    return names.map(name => name === shown ? '<li aria-current="true">' + detailToken('symbol', name) +
        ' <span class="tl-enum-name-basis">' + basis + '</span></li>' : '<li>' + detailToken('symbol', name) + '</li>').join('');
}
function enumPageButtons(footer = false) {
    const button = (step, label, disabled) => '<button type="button" class="tl-btn" data-enum-page="' + step + '"' +
        (footer ? ' aria-label="' + label + ' alias page"' : '') + (disabled ? ' disabled' : '') + '>' + label + '</button>';
    return button(-1, 'Previous', true) + button(1, 'Next', false);
}
function detailViewportTop(viewportTop = window.visualViewport?.offsetTop ?? 0) {
    const header = tlRoot.classList.contains('tl-fullscreen') ? (fullscreenTraceJump() ? elToolbar : null) :
        window.fgPageChrome().tabs[0]?.parentElement;
    return Math.ceil(Math.max(viewportTop, header?.getBoundingClientRect().bottom ?? viewportTop));
}
function focusEnumName(row) {
    if (!row) return;
    row.tabIndex = -1;
    row.focus({preventScroll: true});
    revealDetailTable(row, detailViewportTop());
}
function detailReadingBounds() {
    const viewport = window.visualViewport, viewportTop = viewport?.offsetTop ?? 0;
    const paneTop = elDetail.getBoundingClientRect().top + elDetail.clientTop;
    const top = Math.ceil(Math.max(paneTop + detailHeadInset(), detailViewportTop(viewportTop)));
    const bottom = Math.floor(Math.min(paneTop + elDetail.clientHeight, viewportTop + (viewport?.height ?? innerHeight)));
    return { top, bottom };
}
function revealEnumLocateNotice(notice) {
    const { top, bottom } = detailReadingBounds();
    if (bottom <= top) return;
    const bounds = notice.getBoundingClientRect();
    if (bounds.top < top || bounds.height > bottom - top) elDetail.scrollTop += Math.floor(bounds.top - top);
    else if (bounds.bottom > bottom) elDetail.scrollTop += Math.ceil(bounds.bottom - bottom);
}
function setEnumStatusText(element, text) {
    if (element.textContent === text) return false;
    element.textContent = text;
    return true;
}
function enumFeedbackBottom(status, list) {
    const firstName = list.firstElementChild?.firstElementChild;
    if (firstName) {
        const range = document.createRange();
        range.selectNodeContents(firstName);
        const line = range.getClientRects()[0];
        if (line) return line.bottom;
    }
    return status.getBoundingClientRect().bottom;
}
function paintEnumNamePage(pager, names, start, trigger = null) {
    const filter = enumNameFilters.get(pager), matches = filter?.matches;
    const count = matches?.length ?? names.length;
    const body = pager.parentElement, list = body.querySelector('ul');
    const restoreControlFocus = trigger && document.activeElement === trigger;
    const end = Math.min(count, start + ENUM_NAME_PAGE);
    const rendered = enumRenderedPages.get(list);
    if (Number(list.dataset.start) !== start) list.dataset.start = start;
    const pageChanged = !rendered || rendered.length !== end - start ||
        rendered.some((name, offset) => name !== names[matches ? matches[start + offset] : start + offset]);
    if (pageChanged) {
        const page = matches ? matches.slice(start, end).map(index => names[index]) : names.slice(start, end);
        list.innerHTML = enumNameRows(pager.closest('.tl-enum'), page);
        enumRenderedPages.set(list, page);
    }
    const suffix = filter ? ' matches (' + names.length + ' names total)' : '';
    const status = pager.querySelector('.tl-enum-page-status');
    const statusChanged = setEnumStatusText(status, !count ? 'No matching names' : filter && count <= ENUM_NAME_PAGE ?
        count + (count === 1 ? ' match' : ' matches') : 'Names ' + (start + 1) + '–' + end + ' of ' + count + suffix);
    for (const control of body.querySelectorAll('[data-enum-page]')) {
        const disabled = control.dataset.enumPage === '-1' ? start === 0 : end === count;
        if (control.disabled !== disabled) control.disabled = disabled;
    }
    const footer = body.querySelector('.tl-enum-page-bottom');
    const hideFooter = count <= ENUM_NAME_PAGE;
    if (footer.hidden !== hideFooter) footer.hidden = hideFooter;
    if (!hideFooter) setEnumStatusText(footer.querySelector('.tl-enum-page-position'),
        (start + 1) + '–' + end + ' / ' + count);
    if (!trigger) return pageChanged || statusChanged ? {status, list} : null;
    if (!pager.contains(trigger)) focusEnumName(list.firstElementChild);
    else {
        const control = trigger.disabled ? pager.querySelector('[data-enum-page="' + -Number(trigger.dataset.enumPage) + '"]') : trigger;
        if (!revealDetailBelow(control, enumFeedbackBottom(status, list), { edge: 0 })) focusEnumName(list.firstElementChild);
        else if (restoreControlFocus && control !== trigger) control.focus({preventScroll: true});
    }
}
function matchingEnumNames(names, query, source) {
    const cached = enumFoldedNameCache?.names === names;
    const folded = cached && enumFoldedNameCache.folded;
    const remember = !cached && !source, count = source?.length ?? names.length;
    let nextFolded = remember && count <= ENUM_FOLD_NAME_LIMIT ? [] : null;
    let units = 0, matches = null;
    for (let offset = 0; offset < count; offset++) {
        const index = source ? source[offset] : offset;
        const value = folded ? folded[index] : names[index].toLowerCase();
        if (nextFolded) {
            units += value.length;
            if (units > ENUM_FOLD_UNIT_LIMIT) nextFolded = null;
            else nextFolded.push(value);
        }
        if (value.includes(query)) {
            if (matches) matches.push(index);
        } else if (!matches) {
            matches = source ? source.slice(0, offset) : [];
            if (!source) for (let prior = 0; prior < offset; prior++) matches.push(prior);
        }
    }
    if (remember) enumFoldedNameCache = {names, folded: nextFolded};
    return matches ?? source;
}
function cancelEnumFeedback() {
    if (enumFeedbackFrame) cancelAnimationFrame(enumFeedbackFrame);
    enumFeedbackFrame = 0;
}
function filterEnumNames(event) {
    const input = event.target.closest('[data-enum-filter]');
    if (input && !event.isComposing) updateEnumNameFilter(input);
}
function updateEnumNameFilter(input) {
    const pager = input.closest('.tl-enum-pager'), disclosure = pager.closest('.tl-enum');
    const names = disclosure.open && !pager.hidden && enumNamesOf(disclosure);
    if (!names) return;
    const clear = input.parentElement.querySelector('[data-enum-clear]');
    const disabled = !input.value;
    if (clear.disabled !== disabled) clear.disabled = disabled;
    const query = input.value.trim().toLowerCase(), previous = enumNameFilters.get(pager);
    if (query === (previous?.query || '')) return;
    if (query) {
        const source = previous && query.includes(previous.query) ? previous.matches : null;
        enumNameFilters.set(pager, {query, matches: matchingEnumNames(names, query, source)});
    } else enumNameFilters.delete(pager);
    const status = pager.parentElement.querySelector('.tl-enum-locate-status');
    if (status?.textContent) status.textContent = '';
    const feedback = paintEnumNamePage(pager, names, 0);
    if (!feedback || document.activeElement !== input) return;
    cancelEnumFeedback();
    enumFeedbackFrame = requestAnimationFrame(() => {
        enumFeedbackFrame = 0;
        if (document.activeElement !== input || pager.hidden || !disclosure.open || window.fgCurrentView() !== 'timeline') return;
        revealDetailBelow(input, enumFeedbackBottom(feedback.status, feedback.list), { edge: 0 });
    });
}
function clearEnumNameFilter(input) {
    input.value = '';
    input.focus({preventScroll: true});
    updateEnumNameFilter(input);
}
function enumFilterKey(event) {
    if ((event.key !== 'Enter' && event.key !== 'Escape') || event.defaultPrevented || event.isComposing ||
            event.keyCode === 229 ||
            event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    const input = event.target.closest('[data-enum-filter]');
    const pager = input?.closest('.tl-enum-pager');
    if (!pager || pager.hidden || !pager.closest('.tl-enum').open) return;
    if (event.key === 'Escape') {
        if (!input.value) return;
        event.preventDefault();
        clearEnumNameFilter(input);
        return;
    }
    const row = pager.parentElement.querySelector('li');
    if (!row) return;
    event.preventDefault();
    focusEnumName(row);
}
function toggleAllEnumNames(button, start = 0) {
    const disclosure = button.closest('.tl-enum');
    const names = disclosure?.open && enumNamesOf(disclosure);
    if (!names || names.length <= ENUM_NAME_PREVIEW) return;
    cancelEnumFeedback();
    const body = button.closest('.tl-enum-body'), list = body.querySelector('ul');
    const paged = names.length > ENUM_NAME_PAGE;
    const showAll = button.getAttribute('aria-expanded') !== 'true';
    if (paged) {
        const pager = body.querySelector('.tl-enum-pager');
        pager.hidden = !showAll;
        if (showAll) paintEnumNamePage(pager, names, start);
        else {
            enumCopyRequest++;
            body.querySelector('.tl-enum-page-bottom').hidden = true;
            enumNameFilters.delete(pager);
            enumRenderedPages.delete(list);
            pager.querySelector('[data-enum-filter]').value = '';
            pager.querySelector('[data-enum-clear]').disabled = true;
            pager.querySelector('.tl-enum-copy-status').textContent = '';
            const status = body.querySelector('.tl-enum-locate-status');
            if (status?.textContent) status.textContent = '';
            list.innerHTML = enumNameRows(disclosure, names.slice(0, ENUM_NAME_PREVIEW));
        }
    } else if (showAll) list.insertAdjacentHTML('beforeend', enumNameRows(disclosure, names.slice(ENUM_NAME_PREVIEW)));
    else while (list.childElementCount > ENUM_NAME_PREVIEW) list.lastElementChild.remove();
    button.setAttribute('aria-expanded', String(showAll));
    button.textContent = showAll ? 'Show fewer names' : (paged ? 'Browse all ' : 'Show all ') + names.length + ' names';
    return showAll && paged ? body.querySelector('[data-enum-filter]') : null;
}
function locateRecordedEnumName(button) {
    const disclosure = button.closest('.tl-enum');
    const names = disclosure?.open && enumNamesOf(disclosure);
    const recorded = disclosure?.dataset.enumRecordedLabel;
    if (!names || recorded === undefined) return;
    cancelEnumFeedback();
    const body = button.closest('.tl-enum-body'), list = body.querySelector('ul');
    const mountedLabel = list.querySelector('li[aria-current="true"]');
    if (mountedLabel) { focusEnumName(mountedLabel); return; }
    const pager = body.querySelector('.tl-enum-pager');
    const matches = pager && enumNameFilters.get(pager)?.matches;
    const recordedIndex = names.indexOf(recorded);
    const index = matches ? matches.indexOf(recordedIndex) : recordedIndex;
    if (index < 0) {
        if (matches) {
            const notice = body.querySelector('.tl-enum-locate-status');
            notice.textContent = 'Recorded label is hidden by the current filter. Clear or edit the filter to locate it.';
            revealEnumLocateNotice(notice);
        }
        return;
    }
    const start = index - index % ENUM_NAME_PAGE;
    const toggle = body.querySelector('[data-enum-show-all]');
    if (toggle.getAttribute('aria-expanded') !== 'true') toggleAllEnumNames(toggle, start);
    else if (pager) paintEnumNamePage(pager, names, start);
    focusEnumName(list.querySelector('li[aria-current="true"]'));
}
function changeEnumNamePage(button) {
    const disclosure = button.closest('.tl-enum');
    const names = disclosure?.open && enumNamesOf(disclosure);
    const body = button.closest('.tl-enum-body'), pager = body?.querySelector('.tl-enum-pager');
    if (!names || !pager || pager.hidden || button.disabled) return;
    const start = Number(body.querySelector('ul').dataset.start) + Number(button.dataset.enumPage) * ENUM_NAME_PAGE;
    const count = enumNameFilters.get(pager)?.matches?.length ?? names.length;
    if (start >= 0 && start < count) {
        cancelEnumFeedback();
        paintEnumNamePage(pager, names, start, button);
    }
}
let enumCopyRequest = 0;
async function copyEnumNames(button) {
    const disclosure = button.closest('.tl-enum');
    const names = disclosure?.open && enumNamesOf(disclosure);
    const pager = button.closest('.tl-enum-pager');
    if (!names || pager.hidden) return;
    const status = pager.querySelector('.tl-enum-copy-status');
    const request = ++enumCopyRequest;
    const current = () => request === enumCopyRequest && button.isConnected && disclosure.open && !pager.hidden &&
        elDetail.classList.contains('show') && window.fgCurrentView() === 'timeline';
    status.textContent = '';
    const copied = await fgCopyPlainText(names.join('\n'), current);
    if (current()) showBtfCopyFeedback(button, status, copied ? names.length + ' names copied' :
        'Copy failed. Clear any filter and select names on each page to copy them.');
}
function btfEnumMatch(raw, typeId, nativeArgument = false) {
    const type = D.btf_detail?.enums?.[typeId];
    if (typeof raw !== 'string' || !type) return null;
    const wrapped = nativeArgument && type.enum64 && raw.match(/^\(ENUM64\)\[(0x[\da-f]{1,16})\]$/i);
    const annotated = nativeArgument && !wrapped && raw.match(/^(-?\d+|0[xX][\da-fA-F]+)[ \t]+\[([A-Za-z_]\w*)\]$/);
    const literal = wrapped ? wrapped[1] : annotated ? annotated[1] : raw;
    if (!/^(?:-?\d+|0x[\da-f]+)$/i.test(literal)) return null;
    const {bits, signed} = type;
    const value = scalarValue(literal, [signed ? 'senum' : 'enum', bits]);
    const names = type.values[value] || [];
    const recordedMatch = annotated && names.includes(annotated[2]);
    const name = recordedMatch ? annotated[2] : names[0];
    return {raw, typeId, type, value, names, name, recordedMatch, labelMismatch: !!annotated && !recordedMatch};
}
function detailEnumHtml(match, prefix = '') {
    if (!match) return null;
    const {raw, typeId, type, value, names, name, recordedMatch, labelMismatch} = match;
    const {bits, signed} = type;
    const number = '<span class="tl-enum-number">' + (name ? '(' : '') + detailToken('number', value) + (name ? ')' : '') + '</span>';
    const label = name ? detailToken('symbol', name) + ' ' + number : number;
    const status = !names.length ? 'No BTF match' : names.length > 1 ?
        (recordedMatch ? '' : 'first of ') + names.length + ' names' : '';
    const provenance = recordedMatch ? 'Recorded label' : labelMismatch ? 'Label mismatch' : '';
    const badges = (status ? ' <span class="tl-enum-status">' + status + '</span>' : '') +
        (provenance ? ' <span class="tl-enum-status">' + provenance + '</span>' : '');
    const metadata = (type.name ? 'enum ' + esc(type.name) : 'Anonymous enum') + ' · ' + bits + '-bit ' + (signed ? 'signed' : 'unsigned');
    const aliasAttributes = names.length > 1 ? ' data-enum-type="' + esc(typeId) + '" data-enum-value="' + esc(value) + '"' +
        (recordedMatch ? ' data-enum-recorded-label="' + esc(name) + '"' : '') : '';
    const mismatch = labelMismatch ? '<div>Recorded label does not match this BTF value.</div>' : '';
    return '<details class="tl-enum"' + aliasAttributes + '><summary>' + prefix + label + badges +
        '</summary><div class="tl-context-note tl-enum-body">' + metadata + '<div>Recorded: ' + detailToken('raw', raw) +
        '</div>' + mismatch + '</div></details>';
}
function btfPrototypeBody(signature) {
    const provenance = signature.clone ? 'Original function · entry ABI verified' : 'Exact BTF function';
    return '<div class="tl-prototype-body"><div class="tl-prototype-controls"><span class="tl-context-note tl-prototype-source">' + provenance +
        (signature.source ? ' · ' + esc(signature.source) : '') + '</span>' +
        '<button type="button" class="tl-btn" data-copy-prototype>Copy declaration</button></div>' +
        '<div class="tl-context-note tl-copy-status" role="status"></div><pre><code>' + esc(signature.prototype) + '</code></pre></div>';
}
function mountBtfPrototype(disclosure) {
    if (disclosure.childElementCount !== 1) return;
    const signature = D.btf_detail?.functions?.[disclosure.dataset.btfPrototype];
    if (signature?.prototype) disclosure.insertAdjacentHTML('beforeend', btfPrototypeBody(signature));
}
function revealDisclosureStart(summary) {
    const content = summary.parentElement.querySelector('pre code, .tl-enum-body .tl-code-raw, .tl-value-evidence-body > :first-child');
    if (!content) return;
    const range = document.createRange();
    range.selectNodeContents(content);
    const lines = range.getClientRects(), firstLine = lines[0];
    if (!firstLine) return;
    const preferredBottom = summary.parentElement.hasAttribute('data-btf-prototype') ?
        lines[lines.length - 1].bottom : firstLine.bottom;
    const canExpand = !detailExpanded && (compactCallDetails.matches || elDetail.classList.contains('tl-detail-short'));
    const reading = canExpand ? { summary, top: elDetail.scrollTop, left: elDetail.scrollLeft } : null;
    if (revealDetailBelow(summary, firstLine.bottom, { preferredBottom }) || !reading) return;
    setDetailExpanded(true, true, reading);
    let paneTop = elDetail.getBoundingClientRect().top + elDetail.clientTop;
    const covered = Math.ceil(detailViewportTop() - paneTop);
    if (covered > 0) {
        detailOuterScroller().scrollBy({ top: -covered, behavior: 'instant' });
        paneTop = elDetail.getBoundingClientRect().top + elDetail.clientTop;
    }
    const top = Math.max(paneTop + detailHeadInset(), detailViewportTop());
    elDetail.scrollTop += summary.getBoundingClientRect().top - top - 4;
}
function revealDetailBelow(anchor, contentBottom, { priority = null, edge = 4, visibleAnchorOnly = false,
        preferredBottom = contentBottom } = {}) {
    const viewport = window.visualViewport;
    const paneTop = elDetail.getBoundingClientRect().top + elDetail.clientTop;
    const viewportTop = viewport?.offsetTop ?? 0;
    const bottom = Math.min(paneTop + elDetail.clientHeight, viewportTop + (viewport?.height ?? innerHeight));
    const needed = contentBottom + edge - bottom;
    const preferred = Math.ceil(preferredBottom + edge - bottom);
    if (preferred <= 0) return true;
    const top = Math.max(paneTop + detailHeadInset(), detailViewportTop(viewportTop));
    const anchorTop = anchor.getBoundingClientRect().top;
    if (visibleAnchorOnly && anchorTop >= bottom) return false;
    const available = anchorTop - top - edge;
    if (preferred <= available) { elDetail.scrollTop += preferred; return true; }
    if (preferredBottom > contentBottom) {
        const availableWithoutPadding = Math.floor(anchorTop - top);
        if (availableWithoutPadding > 0 && Math.ceil(preferredBottom - bottom) <= availableWithoutPadding) {
            elDetail.scrollTop += Math.min(preferred, availableWithoutPadding);
            return true;
        }
    }
    if (needed <= 0) return true;
    const prioritize = priority !== null && needed > available && bottom > top;
    const limit = prioritize ? priority.getBoundingClientRect().top - top : available;
    const offset = Math.min(Math.ceil(needed), Math.floor(limit));
    if (offset > 0) elDetail.scrollTop += offset;
    return offset >= needed;
}
function showBtfCopyFeedback(button, status, message) {
    status.textContent = message;
    if (document.activeElement === button)
        revealDetailBelow(button, status.getBoundingClientRect().bottom, { visibleAnchorOnly: true });
}
let prototypeCopyRequest = 0;
async function copyBtfPrototype(button) {
    const disclosure = button.closest('[data-btf-prototype]');
    const text = D.btf_detail?.functions?.[disclosure?.dataset.btfPrototype]?.prototype;
    if (!text || !disclosure?.open) return;
    const status = disclosure.querySelector('.tl-copy-status');
    const request = ++prototypeCopyRequest;
    const current = () => request === prototypeCopyRequest && button.isConnected && disclosure.open &&
        elDetail.classList.contains('show') && window.fgCurrentView() === 'timeline';
    status.textContent = '';
    const copied = await fgCopyPlainText(text, current);
    if (current()) showBtfCopyFeedback(button, status, copied ? 'Declaration copied' :
        'Copy failed. Select the declaration to copy it.');
}
function mountBtfDisclosure(disclosure) {
    const {btfPrototype, enumType, enumRecordedLabel} = disclosure.dataset;
    if (btfPrototype !== undefined) mountBtfPrototype(disclosure);
    else if (enumType !== undefined) {
        const note = disclosure.lastElementChild;
        const names = enumNamesOf(disclosure);
        if (names?.length > 1 && !note.querySelector('ul')) {
            const longList = names.length > ENUM_NAME_PREVIEW;
            const paged = names.length > ENUM_NAME_PAGE;
            const locate = longList && enumRecordedLabel !== undefined ?
                '<button type="button" class="tl-btn" data-enum-locate>Locate recorded label</button>' : '';
            const control = longList ? '<div class="tl-enum-page-actions"><button type="button" class="tl-btn" data-enum-show-all aria-expanded="false">' +
                (paged ? 'Browse all ' : 'Show all ') + names.length + ' names</button>' + locate + '</div>' +
                (locate && paged ? '<div class="tl-enum-locate-status" role="status"></div>' : '') : '';
            const pager = paged ? '<div class="tl-enum-pager" hidden><div class="tl-enum-page-actions">' + enumPageButtons() +
                '<button type="button" class="tl-btn" data-enum-copy>Copy all names</button></div>' +
                '<div class="tl-enum-copy-status" role="status"></div>' +
                '<div class="tl-enum-filter-row"><input type="search" class="tl-enum-filter" data-enum-filter enterkeyhint="go" ' +
                'aria-label="Filter alias names" placeholder="Filter alias names" spellcheck="false">' +
                '<button type="button" class="tl-btn" data-enum-clear aria-label="Clear alias filter" disabled>Clear</button></div>' +
                '<div class="tl-enum-page-status" role="status"></div></div>' : '';
            const explanation = enumRecordedLabel !== undefined ? 'BTF names for this value, in BTF order:' :
                'BTF names for this value; the trace does not distinguish these names:';
            note.insertAdjacentHTML('beforeend', '<div>' + explanation + '</div>' + control + pager +
                '<ul>' + enumNameRows(disclosure, longList ? names.slice(0, ENUM_NAME_PREVIEW) : names) + '</ul>' +
                (paged ? '<div class="tl-enum-page-bottom tl-enum-page-actions" hidden>' + enumPageButtons(true) +
                    '<span class="tl-enum-page-position" aria-hidden="true"></span></div>' : ''));
        }
    }
}
function loadBtfDisclosure(event) {
    const disclosure = event.target;
    if (!elDetail.contains(disclosure)) return;
    if (disclosure.open) { mountBtfDisclosure(disclosure); return; }
    const {btfPrototype, enumType} = disclosure.dataset;
    const status = btfPrototype !== undefined && disclosure.querySelector('.tl-copy-status');
    if (status) {
        prototypeCopyRequest++;
        status.textContent = '';
    }
    const enumStatus = enumType !== undefined && disclosure.querySelector('.tl-enum-copy-status');
    if (enumStatus) {
        enumCopyRequest++;
        enumStatus.textContent = '';
    }
}
function detailEnumArgumentRows(parts, truncation = '') {
    if (!parts.some(part => part.symbolic)) return null;
    const rows = parts.map(({symbolic, plain}, position) => {
        if (symbolic) return '<div class="tl-enum-arg">' + symbolic + '</div>';
        const adjacentPlain = parts[position + 1] && !parts[position + 1].symbolic;
        return '<span class="tl-enum-arg tl-argument">' + plain + (adjacentPlain ? ',</span> ' : '</span>');
    });
    if (truncation) rows.push('<div>' + esc(truncation) + '</div>');
    return rows.join('');
}
function btfEnumArguments(index, raw, signature) {
    if (!signature?.args || D.uftrace?.kernel_details?.[sLine[index]]?.raw_payload) return null;
    if (!D.uftrace) return cachedBtfNamedEnumArguments(raw, signature, sArgs?.[index]);
    const formats = kernelTypesOf(index), names = D.uftrace?.kernel_arg_names?.[sName[index]] || [];
    const fields = raw.split(', ');
    const count = Math.min(fields.length, formats?.argc ?? fields.length);
    const parts = [];
    for (let position = 0; position < count; position++) {
        const value = fields[position];
        if (!/^(?:-?\d+|0x[\da-f]+)$/i.test(value)) return null;
        const entry = signature.args[position];
        const type = entry?.[1];
        const name = entry?.[0] || names[position] || 'slot' + (position + 1);
        const match = btfEnumMatch(value, type);
        parts.push({name, match, value: match ? value : scalarValue(value, formats?.args?.[position])});
    }
    return {parts, truncation: formats?.argc > fields.length ? '…' : ''};
}
const BTF_NAMED_ARGUMENT_CACHE_LIMIT = 128;
const btfNamedArgumentCache = new Array(BTF_NAMED_ARGUMENT_CACHE_LIMIT);
function cachedBtfNamedEnumArguments(raw, signature, stringId) {
    if (!(stringId >= 0) || raw.length > 512) return btfNamedEnumArguments(raw, signature);
    const slot = stringId & (BTF_NAMED_ARGUMENT_CACHE_LIMIT - 1);
    const entry = btfNamedArgumentCache[slot];
    if (entry?.stringId === stringId && entry.signature === signature) return entry.result;
    const result = btfNamedEnumArguments(raw, signature);
    btfNamedArgumentCache[slot] = {stringId, signature, result};
    return result;
}
function btfNamedEnumArguments(raw, signature) {
    const tokens = splitDetailArguments(raw);
    if (!tokens) return null;
    const truncated = tokens[tokens.length - 1].trim() === '...';
    if (truncated) tokens.pop();
    const fields = tokens.map(field => field.trim().match(/^([a-zA-Z_]\w*)\s*=\s*([\s\S]+)$/));
    if (fields.some(field => !field)) return null;
    const byName = new Map(), seen = new Set();
    for (const [name, type] of Object.values(signature.args)) {
        if (name) byName.set(name, byName.has(name) ? null : type);
    }
    for (const [, name] of fields) {
        if (seen.has(name)) byName.delete(name);
        seen.add(name);
    }
    const parts = fields.map(([field, name, value]) => {
        const type = byName.get(name);
        return {name, field, value, match: type == null ? null : btfEnumMatch(value, type, true)};
    });
    return {parts, truncation: truncated ? '...' : ''};
}
function detailEnumArguments(index, raw, signature) {
    const args = btfEnumArguments(index, raw, signature);
    if (!args) return null;
    const rows = args.parts.map(({name, match, field, value}) => {
        const prefix = detailToken('parameter', name) + '=';
        return {symbolic: detailEnumHtml(match, prefix), plain: match ? '' : field ? detailValueHtml(field, true) : prefix + detailValueHtml(value)};
    });
    return detailEnumArgumentRows(rows, args.truncation);
}
function detailNamedArgumentsHtml(value, names) {
    if (!Array.isArray(names) || !names.length) return null;
    const parts = [];
    let offset = 0;
    for (let position = 0; position < names.length; position++) {
        const name = names[position] === null ? 'slot' + (position + 1) : names[position];
        if (typeof name !== 'string' || !value.startsWith(name + '=', offset)) return null;
        const match = value.slice(offset + name.length + 1).match(/^("(?:\\.|[^"\\])*"|-?\d+|0x[\da-fA-F]+)(?=, |$)/);
        if (!match) return null;
        const literal = match[0], end = offset + name.length + 1 + literal.length;
        const separator = end < value.length ? ',' : '';
        parts.push(detailArgumentUnit(detailToken('parameter', name) + '=' + detailValueHtml(literal) + separator));
        offset = end + (separator ? 2 : 0);
    }
    return offset === value.length ? parts.join(' ') : null;
}
function splitDetailArguments(value) {
    if (!/["'()[\]{}]/.test(value)) return value.split(',');
    const parts = [], closing = [];
    let quote = '', escaped = false, offset = 0;
    for (let position = 0; position < value.length; position++) {
        const character = value[position];
        if (quote) {
            if (escaped) escaped = false;
            else if (character === '\\') escaped = true;
            else if (character === quote) quote = '';
        } else if (character === '"' || character === "'") quote = character;
        else if ('([{'.includes(character)) closing.push({ '(': ')', '[': ']', '{': '}' }[character]);
        else if (')]}'.includes(character)) {
            if (closing.pop() !== character) return null;
        } else if (character === ',' && !closing.length) {
            parts.push(value.slice(offset, position));
            offset = position + 1;
        }
    }
    if (quote || closing.length) return null;
    parts.push(value.slice(offset));
    return parts;
}
function detailArgumentsHtml(value, names) {
    const named = detailNamedArgumentsHtml(value, names);
    if (named !== null) return named;
    const parts = splitDetailArguments(value);
    if (!parts) return detailArgumentUnit(detailValueHtml(value, true));
    return parts.map((field, position) => {
        const part = position < parts.length - 1 ? field + ',' : field;
        const leading = part.match(/^\s*/)[0], trailing = part.slice(leading.length).match(/\s*$/)[0];
        const body = part.slice(leading.length, part.length - trailing.length);
        return esc(leading) + (body ? detailArgumentUnit(detailValueHtml(body, true)) : '') + esc(trailing);
    }).join('');
}
const DETAIL_ARGUMENT_CACHE_LIMIT = 128;
const detailArgumentKeys = new Array(DETAIL_ARGUMENT_CACHE_LIMIT);
const detailArgumentMarkup = new Array(DETAIL_ARGUMENT_CACHE_LIMIT);
function detailRecordedArgumentsHtml(value, stringId) {
    if (value.length > 512) return detailArgumentsHtml(value);
    const slot = stringId & (DETAIL_ARGUMENT_CACHE_LIMIT - 1);
    if (detailArgumentKeys[slot] === stringId) return detailArgumentMarkup[slot];
    const html = detailArgumentsHtml(value);
    if (html.length <= 2048) {
        detailArgumentKeys[slot] = stringId;
        detailArgumentMarkup[slot] = html;
    }
    return html;
}
const PROCESS_PAGE = 100;
const processView = { page: 0 };
function showProcessEvents() {
    const reuse = AN.kind === 'process' && elDetail.classList.contains('show') && elDetail.querySelector('.tl-process-table');
    AN.kind = 'process';
    range = null;
    processView.page = 0;
    if (!reuse) openDetail('<div class="tl-detail-head tl-process-head"><span class="tl-detail-title">Recorded exec / exit</span>' +
        '<span class="sp"></span><button class="tl-btn" data-process-export>Export CSV</button>' +
        '<button class="tl-close" title="Close (Esc)">×</button></div>' +
        '<div class="tl-process-tools"><span role="status" aria-live="polite" aria-atomic="true"></span>' +
        '<span role="group" aria-label="Process event pages"><button class="tl-btn" data-process-page="-1">Previous page</button> ' +
        '<button class="tl-btn" data-process-page="1">Next page</button></span></div>' +
        '<div class="tl-process-note tl-context-note">Unfiltered recorded kernel milestones. CSV includes every event.' +
        '<details><summary>Timing and event meanings</summary>These milestones are not inferred task lifetimes. ' +
        'Exec marks the installed image; exit marks a task exiting, not its final reap. ' +
        'Click a trace time to center the timeline without changing zoom or filters.</details></div>' +
        '<table class="tl-process-table"><thead><tr><th scope="col">Event</th><th scope="col">Task</th>' +
        '<th scope="col">Trace time</th><th scope="col">Text</th></tr></thead><tbody></tbody></table>');
    renderProcessEvents();
    elDetail.scrollTop = 0;
    draw();
}
function renderProcessEvents() {
    const count = PROCESS_EVENTS.length;
    processView.page = Math.max(0, Math.min(processView.page, Math.ceil(count / PROCESS_PAGE) - 1));
    const start = processView.page * PROCESS_PAGE, end = Math.min(start + PROCESS_PAGE, count), rows = [];
    for (let index = start; index < end; index++) {
        const [task, time, phase, line] = PROCESS_EVENTS[index];
        rows.push('<tr data-process-index="' + index + '"><td>' +
            (phase === 'exec' ? 'exec (new image installed)' : 'exit (task exiting)') + '</td><td>' +
            esc(trackLabel(TRACKS[task])) + '</td><td><button class="tl-link tl-process-link" data-process-time="' + index +
            '" title="Center timeline at this recorded event">+' + fmtDur(time) + '</button></td><td>' +
            '<button class="tl-link tl-process-link" data-line="' + line + '">line ' + line + '</button></td></tr>');
    }
    elDetail.querySelector('.tl-process-table tbody').innerHTML = rows.join('');
    elDetail.querySelector('.tl-process-tools [role=status]').textContent = count ? 'Events ' + (start + 1) + '–' + end + ' of ' + count : 'No recorded events';
    const previous = elDetail.querySelector('[data-process-page="-1"]');
    const next = elDetail.querySelector('[data-process-page="1"]');
    const focused = document.activeElement;
    previous.disabled = start === 0;
    next.disabled = end >= count;
    if (focused === next && next.disabled && !previous.disabled) previous.focus({ preventScroll: true });
    else if (focused === previous && previous.disabled && !next.disabled) next.focus({ preventScroll: true });
}
function focusProcessEvent(index) {
    const event = PROCESS_EVENTS[index];
    if (!event) return;
    range = null;
    setSelection(-1);
    const task = TRACKS[event[0]];
    if (!task.hidden && task.h > 0) elScroll.scrollTop = Math.max(0, task.y - H / 3);
    if (!panBy(event[1] - (view.s + view.e) / 2)) draw();
}
function exportProcessEvents() {
    function* rows() {
        for (let index = 0; index < PROCESS_EVENTS.length; index++) {
            const [task, time, phase, line] = PROCESS_EVENTS[index], identity = TRACKS[task];
            const timestamp = Number.isSafeInteger(time) ? String(BigInt(D.uftrace.origin_ns) + BigInt(time)) : '';
            yield [index, phase, task, identity.pid, identity.comm, time, timestamp, line, D.uftrace.clock || '', D.uftrace.aligned];
        }
    }
    downloadTimelineCsv('funcgraph-process-events.csv', ['event_index', 'event', 'task_index', 'pid', 'comm',
        'trace_time_ns', 'timestamp_ns', 'trace_line', 'clock', 'clock_aligned'], rows());
}
const DETAIL_SOURCE_URL_LIMIT = 128;
const DETAIL_SOURCE_URL_UNITS = 2048;
const detailSourceUrls = new Map();
let detailSourceBase = null;
function safeSourceUrl(href) {
    if (!href) return '';
    const base = document.baseURI;
    if (base !== detailSourceBase) {
        detailSourceUrls.clear();
        detailSourceBase = base;
    }
    const canCache = typeof href === 'string' && href.length <= DETAIL_SOURCE_URL_UNITS;
    if (canCache) {
        const cached = detailSourceUrls.get(href);
        if (cached !== undefined) return cached;
    }
    let result = '';
    try {
        const url = new URL(href, base);
        if (['http:', 'https:', 'file:', 'vscode:', 'vscodium:'].includes(url.protocol)) result = url.href;
    } catch (error) {}
    if (canCache && result.length <= DETAIL_SOURCE_URL_UNITS) {
        if (detailSourceUrls.size === DETAIL_SOURCE_URL_LIMIT) detailSourceUrls.delete(detailSourceUrls.keys().next().value);
        detailSourceUrls.set(href, result);
    }
    return result;
}

const CALL_NAVIGATION = [['parent', '↑ Parent', '↑ Parent', 'Recorded parent', '↑'],
    ['child', '↓ First child', '↓ Child', 'First recorded child', '↓'],
    ['previous', '‹ Previous', '‹ Prev', 'Previous recorded sibling / task root', '['],
    ['next', 'Next ›', 'Next ›', 'Next recorded sibling / task root', ']']];
function callNavDescription(title, key, target) {
    return title + ' (' + key + '): ' + (target === null ? 'preparing…' : target >= 0 ? nameOf(target) : 'none');
}
function callNavigationTargets(index) {
    return CALL_NAVIGATION.map(([action]) => callTarget(index, action));
}
function callNavigationHtml(index, targets = callNavigationTargets(index)) {
    const synthetic = isSyn(index);
    return '<div class="tl-call-nav' + (synthetic ? '' : ' tl-recorded-call-nav') + '" role="group" aria-label="Recorded call navigation"' +
        (targets.includes(null) ? ' aria-busy="true"' : '') + '>' + CALL_NAVIGATION.map(([action, label, shortLabel, title, key], position) => {
        const target = targets[position], description = callNavDescription(title, key, target);
        const face = synthetic || label === shortLabel ? synthetic && action === 'child' ? '↓ First recorded call' : label :
            '<span class="tl-nav-long">' + label + '</span><span class="tl-nav-short">' + shortLabel + '</span>';
        return '<button class="tl-btn" data-call-nav="' + action + '" title="' + esc(description) + '" aria-label="' + esc(description) + '"' +
            (target === null || target < 0 ? ' disabled' : '') + '>' + face + '</button>';
    }).join('') + '</div>';
}
function syncCallNavigationControl(control, description, disabled) {
    if (control.disabled !== disabled) control.disabled = disabled;
    if (control.title !== description) control.title = description;
    if (control.getAttribute('aria-label') !== description) control.setAttribute('aria-label', description);
}
function refreshCallNavigation(index, nav) {
    for (const [action, , , title, key] of CALL_NAVIGATION) {
        if (action === 'parent') continue;
        const button = nav.querySelector('[data-call-nav="' + action + '"]');
        const target = callTarget(index, action), description = callNavDescription(title, key, target);
        syncCallNavigationControl(button, description, target < 0);
    }
    nav.removeAttribute('aria-busy');
    if (!elCallActions.hidden && index === callActionsSelection) syncCallNavigationActions(nav);
}

const DETAIL_PAGE = 100;
const STACK_CHECKPOINT_STRIDE = 32;
const CONTEXT_CSV_DEFER_ROWS = 4096;
let detailContext = null;
let contextCsvJob = null;
function detailParents(start, stop) {
    const ids = [], seen = new Set(), task = sTask[start];
    let current = start, status = 'complete';
    while (current >= 0) {
        if (current >= N || sTask[current] !== task) { status = 'invalid_parent'; break; }
        if (seen.has(current)) { status = 'cycle'; break; }
        seen.add(current); ids.push(current);
        if (current === stop) break;
        const parent = sParent[current];
        if (parent >= 0 && !seen.has(parent) && sDepth[parent] >= sDepth[current]) { status = 'invalid_parent'; break; }
        current = parent;
    }
    if (status === 'complete' && stop !== undefined && ids[ids.length - 1] !== stop) status = 'missing_chain_top';
    return { ids: ids.reverse(), status };
}
function detailSourceChain(index) {
    const chain = D.sources?.callers?.[sCaller && sCaller[index]] || inlChain(index);
    return chain && chain.length ? chain : null;
}
function detailSourceFields(position, basis) {
    return position ? { file: position[0], line: position[1], href: position[2] || '', sourceKind: basis } : {};
}
function detailRealRow(index, level, site) {
    const position = sFlags[index] & FL.USER ? userPosition(index) : D.sources?.functions?.[sName[index] + ':' + (sMod ? sMod[index] : -1)];
    return { kind: 'traced', name: nameOf(index), index, owner: index, depth: sDepth[index], level,
        ...detailSourceFields(site ? site.slice(1) : position, site ? 'call_site' : sFlags[index] & FL.USER ? 'recorded_user_source' : 'function_entry') };
}
function detailList(kind, title, count, createRows, status = 'complete', current = -1) {
    return { kind, title, count, createRows, status, current, start: 0 };
}
function detailContextData(root) {
    if (detailContext && detailContext.root === root) return detailContext;
    const data = { root, lists: new Map(), facts: null };
    if (root >= N) {
        data.facts = isGhost(root) ? ghostFacts(root) : inlineFacts(root);
        const facts = data.facts;
        data.lists.set('children', detailList('children', 'Traced calls inside (' + facts.kids.length + ')', facts.kids.length, function* (start = 0) {
            for (let position = start; position < facts.kids.length; position++) yield detailRealRow(facts.kids[position], 0);
        }));
        if (!isGhost(root)) data.lists.set('inline', detailList('inline', 'Inline chain at the call site', facts.ch.length, function* (start = 0) {
            const frames = detailSourceChain(facts.kids[0]) || facts.ch;
            for (let position = start; position < facts.ch.length; position++) {
                const frame = frames[position];
                yield { kind: position ? 'inline' : 'call_site', name: frame[0], owner: facts.kids[0], level: position,
                    ...detailSourceFields(frame.slice(1), 'call_site'), current: position === facts.k };
            }
        }, undefined, facts.k));
    } else {
        const path = detailParents(root), first = path.ids[0];
        data.recordedFrames = path.ids.length;
        const outside = path.status === 'complete' && sDepth[first] > 0 ? sDepth[first] : 0;
        const frameCount = index => 1 + Math.max(0, gapOf(index)) +
            (returnSiteOwner(index) === index ? Math.max(0, (detailSourceChain(index)?.length || 0) - 1) : 0);
        let count, blockEnds;
        function stackCount() {
            if (count === undefined) {
                let end = 0;
                for (let position = 0; position < path.ids.length; position++) {
                    if (position && position % STACK_CHECKPOINT_STRIDE === 0) (blockEnds ||= []).push(end);
                    end += frameCount(path.ids[position]);
                }
                count = end + (outside ? 1 : 0);
            }
            return count;
        }
        const stack = detailList('stack', 'Call stack (' + data.recordedFrames + ' recorded frames)', 0, function* (start = 0) {
            if (outside && !start) yield { kind: 'outside', name: '… ' + outside + ' frame(s) outside trace window', owner: first, level: 0, missing: outside };
            const startLevel = Math.max(0, start - (outside ? 1 : 0));
            let firstFrame = 0, level = 0;
            if (startLevel) {
                stackCount();
                let block = 0, limit = blockEnds?.length ?? 0;
                while (block < limit) {
                    const middle = (block + limit) >>> 1;
                    if (blockEnds[middle] <= startLevel) block = middle + 1;
                    else limit = middle;
                }
                firstFrame = block * STACK_CHECKPOINT_STRIDE;
                level = block ? blockEnds[block - 1] : 0;
                for (; firstFrame < path.ids.length; firstFrame++) {
                    const end = level + frameCount(path.ids[firstFrame]);
                    if (end > startLevel) break;
                    level = end;
                }
            }
            let frameGap = firstFrame < path.ids.length ? gapOf(path.ids[firstFrame]) : 0;
            let frameChain;
            for (let position = firstFrame; position < path.ids.length; position++) {
                const index = path.ids[position];
                const gap = Math.max(0, frameGap);
                const chain = frameChain === undefined ? detailSourceChain(index) : frameChain;
                const frameStart = level;
                const boundary = !!(sFlags[index] & FL.BOUNDARY);
                for (let offset = Math.max(0, startLevel - level); offset < gap; offset++) yield {
                    kind: 'untraced', name: offset === gap - 1 ? callerFn(index) : '?', boundary: boundary && offset === 0,
                    owner: index, depth: sDepth[index] - gap + offset, level: level + offset,
                    ...detailSourceFields(offset === gap - 1 && chain && chain[0][0] === callerFn(index) ? chain[0].slice(1) : null, 'call_site') };
                level += gap;
                if (chain && returnSiteOwner(index) === index) {
                    const inlineCount = chain.length - 1;
                    for (let offset = Math.max(0, startLevel - level); offset < inlineCount; offset++) {
                        const frame = chain[offset + 1];
                        const inlineLevel = level + offset;
                        yield { kind: 'inline', name: frame[0], owner: index, level: inlineLevel, boundary: boundary && inlineLevel === frameStart,
                            ...detailSourceFields(frame.slice(1), 'call_site') };
                    }
                    level += inlineCount;
                }
                const child = path.ids[position + 1];
                frameGap = child === undefined ? 0 : gapOf(child);
                frameChain = child === undefined || frameGap ? undefined : detailSourceChain(child);
                const site = frameChain && frameChain[0][0] === nameOf(index) ? frameChain[0] : null;
                const row = detailRealRow(index, level, site);
                row.boundary = boundary && level === frameStart;
                level++;
                const userSite = child !== undefined && sFlags[index] & FL.USER && sFlags[child] & FL.USER &&
                    sParent[child] === index && !frameGap &&
                    D.uftrace?.user_details?.[sLine[child]]?.callsite?.name === nameOf(index) ? userCallSite(child) : null;
                if (userSite) Object.assign(row, detailSourceFields(userSite, 'call_site'), { sourceOwner: child });
                yield row;
            }
        }, path.status);
        Object.defineProperties(stack, {
            count: { get: stackCount },
            current: { get: () => stackCount() - 1 },
        });
        data.lists.set('stack', stack);
        if (eChain[root] >= 0) {
            const chain = CHAINS[eChain[root]], errorPath = detailParents(chain[0], chain[1]);
            const status = errorPath.status === 'complete' && errorPath.ids.length !== chain[4] ? 'length_mismatch' : errorPath.status;
            data.lists.set('error', detailList('error', 'Error chain (' + errorPath.ids.length + ' recorded frames, ' + (errName(chain[2]) || chain[2]) + ')',
                errorPath.ids.length, function* (start = 0) {
                    for (let position = start; position < errorPath.ids.length; position++) yield detailRealRow(errorPath.ids[position], position);
                }, status));
        }
    }
    detailContext = data;
    return data;
}
function detailRowsHtml(list, rows) {
    return rows.map(row => {
        const traced = row.kind === 'traced', current = traced ? row.index === detailContext.root : row.current;
        const label = traced ? list.kind === 'error' ? String(row.depth) : fmtDur(durOf(row.index)) : row.kind === 'outside' ? '' : row.kind === 'call_site' ? 'call site' : row.kind;
        const suffix = list.kind === 'error' ? retOf(row.index) || '' : '';
        const rowSpace = MIXED && row.owner >= 0 && row.kind !== 'outside' ? spaceOf(row.owner) : null;
        const title = row.name + (suffix ? ' — ' + suffix : '') + (row.kind === 'untraced' ? ' (not traced: skipped depth)' : '');
        const main = '<' + (traced ? 'button type="button"' : 'div') + ' class="tl-crumb tl-context-select' + (current ? ' cur' : '') + '"' + (traced ? ' data-slice="' + row.index + '"' : '') +
            (current ? ' aria-current="true"' : '') + ' title="' + esc(title) + '"><span class="tl-context-value"' + (traced && list.kind === 'stack' ? ' data-stack-duration="' + row.index + '"' : '') +
            '>' + esc(label) + '</span><span class="tl-context-text">' +
            (rowSpace ? '<span class="tl-stack-space">' + (rowSpace === 'user' ? 'U' : 'K') + '</span>' : '') + detailSymbolHtml(row.name) +
            (traced && tailJumpOf(row.index) ? ' <span class="tl-tail-tag" title="' + esc(callerRelationText(row.index)) + '">⇢ tail jump</span>' : '') +
            (suffix ? ' <span class="' + (eRole[row.index] >= 0 ? 'tl-code-error' : 'd') + '">' + detailValueHtml(suffix) + '</span>' : '') + '</span></' + (traced ? 'button' : 'div') + '>';
        let source = '';
        if (row.kind !== 'outside') {
            const at = row.file ? row.file + ':' + row.line : 'Source unavailable';
            const basis = row.file ? row.sourceKind === 'function_entry' ? 'Resolved function entry, not the calling line' :
                row.sourceKind === 'recorded_user_source' ? 'Recorded user function entry, not a call site' : 'Call site inside this frame' : '';
            const href = safeSourceUrl(row.href);
            const sourceOwner = row.sourceOwner === undefined ? row.index : row.sourceOwner;
            const embedded = traced && (row.sourceKind === 'recorded_user_source' && !userCallSite(row.index) || row.sourceOwner !== undefined) &&
                callSiteBlock(sourceOwner) !== null;
            const tag = href ? 'a' : embedded ? 'button' : 'span';
            source = '<div class="tl-context-source">' +
                '<' + tag + (href ? ' href="' + esc(href) + '" target="_blank" rel="noopener noreferrer"' :
                    embedded ? ' type="button" class="tl-source-open" data-slice="' + sourceOwner + '"' : '') +
                ' title="' + esc(at + (basis ? ' — ' + basis : '') + (row.file && !href && !embedded ? ' — no source URL configured' : '')) + '">' +
                (row.file ? '<span class="tl-source-file">' + esc(row.file) + '</span><span class="tl-source-line">:' + esc(row.line) + '</span>' : esc(at)) +
                '</' + tag + '></div>';
        }
        const boundary = list.kind === 'stack' && row.boundary;
        return '<div class="tl-context-row' + (traced ? '' : ' inl') + '" data-context-kind="' + row.kind + '"' +
            (boundary ? ' data-space-boundary="user-kernel" title="User → kernel"' : '') + '>' + main + source + '</div>';
    }).join('');
}
function detailContextPageEnd(list) { return Math.min(list.start + DETAIL_PAGE, list.count); }
function detailContextPageStatus(list) {
    return 'Showing ' + (list.start + 1) + '–' + detailContextPageEnd(list) + ' of ' + list.count + ' context rows';
}
function detailContextPageNumber(list) {
    return (Math.floor(list.start / DETAIL_PAGE) + 1) + ' / ' + Math.ceil(list.count / DETAIL_PAGE);
}
function detailContextPageRows(list) {
    const rows = [];
    for (const row of list.createRows(list.start)) {
        rows.push(row);
        if (rows.length === DETAIL_PAGE) break;
    }
    return detailRowsHtml(list, rows);
}
function detailContextPagerHtml(list, atBottom = false) {
    if (list.count <= DETAIL_PAGE) return '';
    const suffix = atBottom ? ' at bottom' : '';
    const button = (step, label, short, accessible, disabled) => '<button type="button" class="tl-btn" data-context-page="' + step +
        '" aria-label="' + accessible + suffix + '"' + (disabled ? ' disabled' : '') +
        '><span class="tl-context-page-full">' + label + '</span><span class="tl-context-page-short" aria-hidden="true">' + short + '</span></button>';
    const status = atBottom ? '' : detailContextPageStatus(list);
    return '<div class="tl-context-pager"' + (atBottom ? ' data-context-page-bottom' : ' data-context-page-top') +
        ' role="group" aria-label="Context pages' + suffix + '">' +
        (atBottom ? '' : '<span role="status">' + status + '</span><span class="tl-context-page-short" data-context-page-count aria-hidden="true" title="' +
            status + '">' + detailContextPageNumber(list) + '</span>') +
        button(-1, 'Previous', '←', 'Previous context page', !list.start) +
        button(1, 'Next', '→', 'Next context page', detailContextPageEnd(list) >= list.count) +
        (list.current >= 0 ? button('current', 'Current frame', 'Current', 'Go to current frame', false) : '') + '</div>';
}
function detailListBodyHtml(list) {
    return (list.status === 'complete' ? '' : '<div class="tl-context-note">Incomplete context: ' + list.status.replace(/_/g, ' ') +
        '. Showing only the observed parent path; no missing frames are invented.</div>') +
        detailContextPagerHtml(list) + '<div class="tl-crumbs">' + detailContextPageRows(list) + '</div>' + detailContextPagerHtml(list, true);
}
function detailListHtml(kind, deferred = false) {
    const list = detailContext.lists.get(kind), title = esc(list.title);
    const compactStack = deferred && kind === 'stack';
    const heading = deferred ? '<button type="button" class="tl-btn tl-stack-toggle" data-stack-toggle aria-expanded="false" aria-controls="tlStackBody" aria-label="' + title + '">' +
        '<svg aria-hidden="true" focusable="false" width="12" height="12" viewBox="0 0 12 12"><path d="m4 2 4 4-4 4" fill="none" stroke="currentColor"/></svg>' +
        '<span>' + (compactStack ? '<span class="tl-stack-full">' + title + '</span><span class="tl-stack-short" aria-hidden="true">Call stack (' +
            detailContext.recordedFrames + ')</span>' : title) + '</span></button>' : '<span class="tl-sec">' + title + '</span>';
    const exportLabel = kind === 'children' ? 'calls' : kind === 'inline' ? 'inline chain' : kind === 'error' ? 'error chain' : 'stack';
    const exportText = 'Export ' + exportLabel + ' CSV';
    const exportFace = compactStack ? '<span class="tl-stack-csv-full">' + exportText + '</span>' +
        '<span class="tl-stack-csv-short" aria-hidden="true">CSV</span>' : exportText;
    return '<section class="tl-context-list" data-context-list="' + kind + '"><div class="tl-context-head' +
        (deferred ? ' tl-stack-head' : '') + '">' + heading +
        '<button type="button" class="tl-btn" data-context-export="' + kind + '"' +
        (compactStack ? ' aria-label="' + exportText + '"' : '') + '>' + exportFace + '</button>' +
        (compactStack || list.count >= CONTEXT_CSV_DEFER_ROWS ? '<span class="tl-context-note" data-context-csv-status role="status" hidden></span>' : '') + '</div>' +
        (deferred ? '<div id="tlStackBody" class="tl-stack-body" hidden></div>' : detailListBodyHtml(list)) + '</section>';
}
function toggleDetailStack(button) {
    const body = elDetail.querySelector('.tl-stack-body');
    const open = body.hidden;
    if (open && !body.firstElementChild) {
        body.innerHTML = detailListBodyHtml(detailContext.lists.get('stack')) +
            (detailContext.lists.has('error') ? '' : detailContextHint());
    }
    body.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
}
function changeDetailContextPage(control) {
    const section = control.closest('[data-context-list]');
    const list = detailContext.lists.get(section.dataset.contextList);
    const toCurrent = control.dataset.contextPage === 'current';
    const step = Number(control.dataset.contextPage);
    const start = toCurrent ? Math.floor(list.current / DETAIL_PAGE) * DETAIL_PAGE : list.start + step * DETAIL_PAGE;
    if (start < 0 || start >= list.count) return;
    const rows = section.querySelector('.tl-crumbs');
    if (start !== list.start) {
        list.start = start;
        const pager = section.querySelector('[data-context-page-top]');
        const status = detailContextPageStatus(list), pageNumber = pager.querySelector('[data-context-page-count]');
        pager.querySelector('[role=status]').textContent = status;
        pageNumber.textContent = detailContextPageNumber(list);
        pageNumber.title = status;
        for (const button of section.querySelectorAll('[data-context-page="-1"], [data-context-page="1"]')) {
            button.disabled = button.dataset.contextPage === '-1' ? !start : detailContextPageEnd(list) >= list.count;
        }
        rows.innerHTML = detailContextPageRows(list);
        if (!toCurrent) {
            const target = pager.querySelector('[data-context-page="' + (control.disabled ? -step : step) + '"]');
            revealDetailTable(pager);
            if (document.activeElement !== target) target.focus({ preventScroll: true });
        }
    }
    if (toCurrent) {
        const target = rows.querySelector('[aria-current="true"]');
        if (!target) return;
        if (target.tabIndex < 0) target.tabIndex = -1;
        revealDetailTable(target);
        target.focus({ preventScroll: true });
    }
}
function detailContextHint() {
    return '<div class="tl-context-note">Context is unfiltered. Durations follow Overhead; frame durations overlap and must not be summed. ' +
        'Inline, untraced and outside-window rows are context, not extra measured calls. ' +
        'Source positions are on the right; hover for the full path and call-site / function-entry basis. Unresolved locations are not guessed.</div>';
}
function setContextCsvLabel(button, label, shortLabel) {
    const full = button.querySelector('.tl-stack-csv-full');
    if (!full) { button.textContent = label; return; }
    full.textContent = label;
    button.querySelector('.tl-stack-csv-short').textContent = shortLabel;
    button.setAttribute('aria-label', label);
}
function cancelContextCsv() {
    const job = contextCsvJob;
    if (!job) return;
    contextCsvJob = null;
    setContextCsvLabel(job.button, job.label, 'CSV');
    job.status.hidden = true;
    job.status.textContent = '';
}
function showContextCsvProgress(job) {
    setContextCsvLabel(job.button, 'Cancel CSV', 'Cancel');
    job.status.hidden = false;
    job.status.textContent = timelineCsvProgressText(job.progress);
}
function restoreContextCsvControls(job, restoreFocus) {
    const kind = job.button.dataset.contextExport;
    const button = elDetail.querySelector('[data-context-export="' + kind + '"]');
    const status = button?.parentElement.querySelector('[data-context-csv-status]');
    if (!status || detailContext?.lists.get(kind) !== job.list) { cancelContextCsv(); return; }
    job.button = button;
    job.status = status;
    showContextCsvProgress(job);
    if (restoreFocus) button.focus({preventScroll: true});
}
function queueContextCsv(afterPaint = false) {
    const job = contextCsvJob;
    if (job) queueTimelineCsv(job, () => contextCsvJob === job, cancelContextCsv, afterPaint);
}
function exportDetailCsv(button) {
    const wasRunning = contextCsvJob?.button === button;
    cancelContextCsv();
    if (wasRunning) return;
    const kind = button.dataset.contextExport;
    const data = detailContext, list = data.lists.get(kind), unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const durations = sDur, selfTimes = sSelf, adjusted = adjOn;
    const columns = ['list', 'position', 'kind', 'function', 'recorded_slice_index', 'owner_slice_index', 'depth', 'display_level',
        'trace_line', 'exit_trace_line', 'task_index', 'pid', 'comm', 'cpu', 'return', 'call_site', 'args', 'module', 'source_file', 'source_line',
        'outside_frame_count', 'duration_' + unit, 'self_' + unit, 'start_' + unit, 'end_' + unit, 'time_mode', 'duration_basis', 'position_basis',
        'selected_slice_index', 'chain_index', 'chain_origin_slice', 'chain_top_slice', 'chain_reported_frames', 'errno', 'fate', 'path_status',
        'source_kind', 'source_url', 'relationship', 'relationship_basis', 'tail_jump_sites', 'trace_binary_version_verified',
        'source_context_owner_slice_index', 'source_context_basis'];
    if (D.uftrace) columns.push('space', 'address_space_boundary');
    function* rows() {
        let position = 0;
        for (const row of list.createRows()) {
            const traced = row.kind === 'traced', index = row.index, owner = row.owner, task = TRACKS[sTask[owner]];
            const chainIndex = kind === 'error' ? eChain[data.root] : -1;
            const chain = chainIndex >= 0 ? CHAINS[chainIndex] : null;
            const evidence = traced ? tailJumpOf(index) : null;
            yield [kind, position++, row.kind, row.name, traced ? index : '', owner, row.depth === undefined ? '' : row.depth, row.level,
                traced ? sLine[index] : '', traced && sEline ? sEline[index] || '' : '', sTask[owner], task.pid, task.comm,
                traced && cpuOf(index) >= 0 ? cpuOf(index) : '', traced ? retOf(index) || '' : '', traced ? callerOf(index) || '' : '',
                traced ? argsOf(index) || '' : '', traced ? modOf(index) || '' : '', row.file || '', row.line || '', row.missing || '',
                traced ? durations[index] / scale : '', traced ? selfTimes[index] / scale : '', traced ? sStart[index] / scale : '', traced ? sEnd[index] / scale : '', MODE,
                traced ? MODE === 'count' ? 'call_order' : adjusted ? 'overhead_subtracted_estimate' : 'measured_duration' : '',
                traced ? TIMED ? 'relative_timestamp_span' : MODE === 'count' ? 'call_order_geometry' : 'packed_geometry' : '',
                data.root, chainIndex >= 0 ? chainIndex : '', chain ? chain[0] : '', chain ? chain[1] : '', chain ? chain[4] : '',
                chain ? chain[2] : '', chain ? ERROR_FATES[chain[3]] : '', list.status, row.sourceKind || '', row.href || '',
                evidence ? 'tail_jump_evidence' : traced && sFlags[index] & FL.MISMATCH ? 'caller_mismatch_unresolved' : '',
                evidence ? 'supplied_binary_static' : '',
                evidence ? evidence.sites.map(site => site.symbol || site.address).join('; ') : '',
                evidence ? 'false' : '', traced && sCaller && sCaller[index] >= 0 ? returnSiteOwner(index) : '',
                traced && sCaller && sCaller[index] >= 0 ? returnSiteOwner(index) === index ? 'recorded_return_site' : 'shared_recorded_return_site' : '',
                ...(D.uftrace ? [traced ? spaceOf(index) : '', traced && (sFlags[index] & FL.BOUNDARY) ? 'user_to_kernel' : ''] : [])];
        }
    }
    const filename = 'funcgraph-context-' + kind + '-' + (MODE === 'count' ? 'count' : adjusted ? 'adjusted' : 'raw') + '.csv';
    if (list.count < CONTEXT_CSV_DEFER_ROWS) { downloadTimelineCsv(filename, columns, rows()); return; }
    const status = button.parentElement.querySelector('[data-context-csv-status]');
    contextCsvJob = { button, status, filename, list, rows: rows(), total: list.count, batchRows: 128, written: 0, progress: 0,
        label: button.getAttribute('aria-label') || button.textContent,
        parts: ['\ufeff' + columns.join(',') + '\r\n'], scheduled: false };
    showContextCsvProgress(contextCsvJob);
    queueContextCsv(true);
}

function callSiteHeading(label) {
    return '<summary class="tl-sec"><span class="tl-cs-title" title="' + esc(label) + '">' + esc(label) + '</span></summary>';
}
function showInlineDetail(i, dense) {
    const f = detailContextData(i).facts;
    let h = '<div class="tl-detail-head tl-slice-head"><span class="tl-detail-title" title="' + esc(f.fn) + '">' + detailSymbolHtml(symbolLabel(f.fn)) + '</span>' + pill('mute', 'inlined') +
        pill('mute', 'into ' + symbolLabel(f.intoFn), f.intoFn) + '<span class="sp"></span>' + lineLink(sLine[f.kids[0]], 'Open first call in text view ↵') +
        '<button class="tl-close" title="Close (Esc)">×</button></div>' + callNavigationHtml(i) + '<div class="tl-detail-body"><div class="tl-left"><div class="tl-kv">';
    h += kv('traced extent', fmtDur(f.span) + ' &nbsp;<span class="d">from the first traced call inside to the end of the last one</span>');
    h += kv('start / end', fmtDur(sStart[i]) + ' → ' + fmtDur(sEnd[i]));
    h += kv('inlined into', detailSymbolHtml(f.intoFn) + ' at ' + esc(f.atFile) + ':' + f.atLine);
    h += kv('defined in', esc(f.file));
    h += kv('traced calls', f.kids.length + ', together ' + fmtDur(f.covered));
    h += kv('between them', fmtDur(f.gap) + ' <span class="d">own inlined code, untraced callees, interrupts</span>');
    if (f.mismatch) h += kv('note', 'containing function ' + esc(f.ch[0][0]) + ' ≠ recorded parent ' + esc(nameOf(f.y.p)) + ' (tail call, inline or untraced code; cause unresolved)');
    h += '<span class="k">limits</span><span class="v d">Inlined code has no entry/exit events: only the traced calls inside it are visible, so the real start may be earlier and the real end later.</span>';
    h += '</div><details class="tl-cs" open>' + callSiteHeading('Inline source context') +
        '<div class="tl-context-note">Source at the first traced call inside this frame, not its full body or function entry.</div>' +
        '<div class="tl-cs-src"></div></details></div><div>' + detailListHtml('inline') + detailListHtml('children') + detailContextHint() + '</div></div>';
    openDetail(h, () => fillCallSite(f.kids[0], f.ch[f.k]), i, dense);
}
function showGhostDetail(i, dense) {
    const f = detailContextData(i).facts, y = f.y;
    const nm = y.name === '?' ? 'unknown frame' : y.name;
    let h = '<div class="tl-detail-head tl-slice-head"><span class="tl-detail-title" title="' + esc(nm) + '">' + detailSymbolHtml(symbolLabel(nm)) + '</span>' + pill('mute', 'not traced') +
        '<span class="sp"></span>' + lineLink(sLine[f.kids[0]], 'Open first call in text view ↵') +
        '<button class="tl-close" title="Close (Esc)">×</button></div>' + callNavigationHtml(i) + '<div class="tl-detail-body"><div class="tl-kv">';
    h += kv('extent', fmtDur(f.span) + ' &nbsp;<span class="d">from the first call inside to the end of the last one</span>');
    h += kv('start / end', fmtDur(sStart[i]) + ' → ' + fmtDur(sEnd[i]));
    h += kv('inside', '<button type="button" class="tl-link tl-detail-link" data-slice="' + y.p + '">' + esc(nameOf(y.p)) + '</button>');
    h += kv('traced calls', f.kids.length + ', together ' + fmtDur(f.covered));
    h += kv('between them', fmtDur(f.gap) + ' <span class="d">untraced code</span>');
    h += '<span class="k">why</span><span class="v d">The kernel\'s call depth jumps by ' + (y.gap + 1) + ' levels from ' + esc(nameOf(y.p)) +
        ' to the calls inside: ' + (y.gap > 1 ? y.gap + ' frames were' : 'a frame was') + ' entered without a recorded entry ' +
        '(not traceable, e.g. noinstr entry code or the unwinder, or dropped). ' +
        (y.name === '?' ? 'Its name is unknown.' : 'The name comes from the return address of the calls inside (their caller).') +
        ' Its real start and end are not known.</span>';
    h += '</div><div>' + detailListHtml('children') + detailContextHint() + '</div></div>';
    openDetail(h, null, i, dense);
}
function showsRecordedCall(index) {
    return elDetail.firstElementChild?.dataset.recordedCall === String(index);
}
function callTimingText(index) {
    const flags = sFlags[index], duration = durOf(index), self = sSelf[index];
    return {
        duration: flags & FL.ORPHAN && D.uftrace ? 'unknown · entry not recorded' :
            fmtDur(duration) + (flags & FL.EST ? ' (estimated)' : flags & FL.TRUNC && D.uftrace ? ' (incomplete span)' : ''),
        self: fmtDur(self) + (duration > 0 ? ' (' + (100 * self / duration).toFixed(1) + '%)' : '')
    };
}
function callStartHtml(index) {
    const start = sStart[index];
    const position = '<span class="tl-time-value"' + (TIMED ? ' title="Offset from the report origin"' : '') +
        '>+' + fmtDur(start) + '</span>';
    return MODE === 'abs' ? position + ' <span class="tl-time-value tl-absolute-time" title="Absolute trace timestamp">' +
        fmtAbs(start) + ' s</span>' : position;
}
function refreshCallTimings(index) {
    const timing = callTimingText(index);
    const update = (node, text) => { if (node.textContent !== text) node.textContent = text; };
    update(elDetail.querySelector('.tl-call-duration'), timing.duration);
    update(elDetail.querySelector('.tl-call-self'), timing.self);
    for (const node of elDetail.querySelectorAll('[data-stack-duration]')) {
        update(node, fmtDur(durOf(+node.dataset.stackDuration)));
    }
    refreshChildPreview(index);
    sizeDetailHead();
}
const detailWork = new Map();
let detailWorkFramePending = false, detailWorkTimer = null;
const DETAIL_COLD_TRACK_SLICES = 32768;
const DETAIL_WORK_BUDGET = 6;
function advanceDetailWork(steps, budget) {
    const deadline = performance.now() + budget;
    let result;
    do { result = steps.next(); } while (!result.done && performance.now() < deadline);
    return result;
}
function deferDetailWork(key, steps, finish, after = null) {
    detailWork.set(key, {steps, finish, after});
    queueDetailWork();
}
function clearDetailWork() {
    detailWork.clear();
    if (detailWorkTimer !== null) {
        clearTimeout(detailWorkTimer);
        detailWorkTimer = null;
    }
}
function flushDetailPaintWork() {
    detailWorkFramePending = false;
    if (!detailWork.size || window.fgCurrentView() !== 'timeline') return;
    detailWorkTimer = setTimeout(runDetailWork, 0);
}
function nextDetailWork() {
    for (const entry of detailWork) {
        const after = entry[1].after;
        if (after === null || !detailWork.has(after)) return entry;
    }
    return null;
}
function runDetailWork() {
    detailWorkTimer = null;
    if (!detailWork.size || window.fgCurrentView() !== 'timeline') return;
    const deadline = performance.now() + DETAIL_WORK_BUDGET;
    do {
        const next = nextDetailWork();
        if (!next) return;
        const [key, job] = next;
        const result = job.steps.next();
        detailWork.delete(key);
        if (result.done) job.finish(result.value);
        else detailWork.set(key, job);
    } while (detailWork.size && window.fgCurrentView() === 'timeline' && performance.now() < deadline);
    queueDetailWork();
}
function queueDetailWork() {
    if (!detailWorkFramePending && detailWorkTimer === null && detailWork.size && window.fgCurrentView() === 'timeline') {
        detailWorkFramePending = true;
        requestAnimationFrame(flushDetailPaintWork);
    }
}
function paintChildPreview(preview, data) {
    const focused = document.activeElement;
    const group = preview.contains(focused) ? focused.dataset.childPreviewGroup : undefined;
    const heading = preview.previousElementSibling;
    const explorer = heading.querySelector('[data-child-open]');
    if (data && !data.callCount) {
        if (focused === explorer || group !== undefined)
            elDetail.querySelector('[data-call-actions]')?.focus({preventScroll: true});
        heading.remove();
        preview.remove();
        syncCallChildrenAction(null);
        return;
    }
    heading.querySelector('[data-child-title]').textContent = childPreviewTitle(data);
    if (data) explorer.removeAttribute('aria-disabled');
    else explorer.setAttribute('aria-disabled', 'true');
    syncCallChildrenAction(explorer);
    preview.innerHTML = childPreviewContent(data);
    if (group !== undefined) (preview.querySelector('[data-child-preview-group="' + group + '"]') || explorer).focus({preventScroll: true});
}
function refreshChildPreview(root) {
    const preview = elDetail.querySelector('[data-child-preview]');
    if (!preview || detailWork.has('call-tree')) return;
    detailWork.delete('children');
    const steps = childPreviewSteps(root), result = advanceDetailWork(steps, 3);
    paintChildPreview(preview, result.value);
    if (!result.done) deferDetailWork('children', steps, data => paintChildPreview(preview, data));
}
function showDetail(i, dense, revealOnOpen = null) {
    range = null;
    AN.kind = null;
    if (i >= N) { if (isGhost(i)) showGhostDetail(i, dense); else showInlineDetail(i, dense); return; }
    const context = detailContextData(i);
    const track = TRACKS[sTask[i]];
    const f = sFlags[i];
    const deferColdTrack = !(f & FL.LEAF) && track.hi - track.lo >= DETAIL_COLD_TRACK_SLICES && !callTreeFor(i);
    const breakdown = breakdownSteps(i), initialBreakdown = deferColdTrack ? {done: false} : advanceDetailWork(breakdown, 3);
    const previewSteps = childPreviewSteps(i), initialPreview = deferColdTrack ? {done: false} : advanceDetailWork(previewSteps, 3);
    const navigation = callNavigationTargets(i);
    const navigationPending = navigation.includes(null);
    const needsCallTree = deferColdTrack || navigationPending;
    const after = needsCallTree && !(f & FL.LEAF) ? 'call-tree' : null;
    const timing = callTimingText(i);
    let pills = D.uftrace ? pill('mute', f & FL.USER ? 'User space' : 'Kernel space') : '';
    if (!D.uftrace || f & FL.LAT) pills += pill('ctx' + (f & FL.CTX), contextName(f) + (f & FL.CTXINF ? '?' : ''));
    if (f & FL.BOUNDARY) pills += pill('mute', 'User → kernel', 'Same thread and contained recorded interval; exact user call site is not known');
    if (f & FL.IRQOFF) pills += pill('warn', 'irqs off');
    if (f & FL.BHOFF) pills += pill('warn', 'BH off');
    const resched = (f >> FL.RS_SHIFT) & 7;
    for (const label of RESCHED_LABELS[resched]) pills += pill('warn', label, RESCHED_NAMES[resched]);
    // preemption: the preempt-count digit above the trace's baseline (the tracer itself may add one)
    const pd = preemptOff(f);
    if (pd > 0) pills += pill('warn', 'preempt off' + (pd > 1 ? ' ×' + pd : ''), 'Recorded preempt count ' + ((f >> FL.PR_SHIFT) & 15) + '; tracing-adjusted depth ' + pd + ' (inferred)');
    if ((f >> FL.MG_SHIFT) & 15) pills += pill('warn', 'migrate off', 'migrate_disable() depth ' + ((f >> FL.MG_SHIFT) & 15));
    if (eRole[i] >= 0) pills += pill('err', eRole[i] === 0 ? 'error origin' : 'error propagated');
    const userFailure = USER_ERRORS.get(i);
    if (userFailure) pills += pill('err', 'API failure hint', userFailure.note);
    if (ATOMIC.has(i)) pills += pill('err', 'may sleep in atomic context');
    if (f & FL.TRUNC) pills += pill('mute', 'no exit');
    if (f & FL.ORPHAN) pills += pill('mute', 'no entry');
    if (f & FL.EST) pills += pill('mute', 'estimated');
    if (f & FL.MISMATCH) pills += pill('mute', tailJumpOf(i) ? 'tail jump (static)' : 'caller mismatch', callerRelationText(i));
    const symbol = esc(nameOf(i)), module = modOf(i), moduleLabel = module ? ' [' + esc(module) + ']' : '';
    let h = '<div class="tl-detail-head tl-slice-head tl-recorded-call-head" data-recorded-call="' + i + '">' +
        '<button type="button" class="tl-call-symbol-title" data-call-symbol aria-expanded="false" aria-label="Recorded symbol: ' + symbol + moduleLabel + '">' +
        '<span class="tl-detail-title" title="' + symbol + '">' + detailSymbolHtml(sliceLabel(i)) + moduleLabel + '</span>' +
        '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="m2 4 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg></button>' +
        (pills ? '<div class="tl-call-context">' + pills + '</div>' : '') + '<div class="tl-call-controls">' +
        (childListReturn?.index === i ? '<button class="tl-btn tl-child-list-return" type="button" data-child-list-return ' +
            'aria-label="Back to child calls" title="Back to child calls">←<span> Child calls</span></button>' : '') +
        '<button class="tl-btn tl-call-actions-button" type="button" data-call-actions aria-haspopup="menu" ' +
        'aria-controls="tlCallActions" aria-expanded="false" aria-label="Selected call actions" title="Selected call actions">' +
        '<span class="tl-call-actions-full">Actions ▾</span><span class="tl-call-actions-short" aria-hidden="true">⋯</span></button>' +
        '<button class="tl-close" title="Close (Esc)">×</button></div></div>';
    h += callNavigationHtml(i, navigation) + '<div class="tl-detail-body tl-recorded-call-body"><div class="tl-left"><div class="tl-kv tl-call-facts">';
    h += kv('duration', timing.duration, 'tl-call-duration');
    h += kv('self', timing.self, 'tl-call-self');
    h += kv('start', callStartHtml(i));
    const btf = btfDetailOf(i);
    if (btf?.prototype) {
        const key = btfDetailKey(i), previous = elDetail.querySelector('.tl-btf-prototype');
        const opened = previous?.dataset.btfPrototype === key && previous.open;
        h += '<details class="v tl-btf-prototype" data-btf-prototype="' + key + '"' + (opened ? ' open' : '') +
            '><summary>Function prototype <span class="tl-muted">· BTF' + (btf.clone ? ' · original function' : '') +
            '</span></summary>' + (opened ? btfPrototypeBody(btf) : '') + '</details>';
    }
    h += kv('task', esc(trackLabel(track)) + (cpuOf(i) >= 0 ? ' &nbsp;on CPU ' + cpuOf(i) : ''));
    if (f & FL.LAT) h += kv('flags', esc(ctxFlagText(f).replace(/^ · /, '').replace(' (preempt off)', '') || 'none'));
    if (ATOMIC.has(i)) h += kv('context', 'called with ' + esc(atomicText(ATOMIC.get(i))) + ' (sleeping function in atomic context)', 'err');
    const r = retOf(i), rawReturn = rawRetOf(i), typedReturn = kernelTypesOf(i)?.ret;
    const c = callerOf(i);
    if (c) h += kv('caller site', detailSymbolHtml(c) + '<span class="tl-cs-at"></span>' + (f & FL.MISMATCH ? '<div class="tl-context-note tl-tail-note">' + esc(callerRelationText(i)) + '</div>' : ''));
    const tailJump = tailJumpOf(i);
    if (tailJump) h += kv('tail jump', tailJump.sites.map(site => {
        const source = site.source;
        const url = source && safeSourceUrl(source[2]);
        const position = source ? esc(source[0] + ':' + source[1]) : 'Source unavailable';
        return '<div>' + detailSymbolHtml(site.symbol || site.address) + ' · ' + esc(site.mnemonic) + ' → ' + detailSymbolHtml(nameOf(i)) +
            ' · ' + (url ? '<a class="tl-link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + position + '</a>' : position) + '</div>';
    }).join('') + '<div class="tl-context-note tl-tail-note">Static sites in supplied binary; actual branch site not captured.</div>');
    const a = argsOf(i);
    const userDetail = f & FL.USER ? D.uftrace?.user_details?.[sLine[i]] : null;
    if (userDetail) {
        const callAddress = userCallAddress(i);
        const userSite = userCallSite(i);
        if (callAddress) h += kv('caller site', detailSymbolHtml(callAddress) +
            '<span class="tl-cs-at">' + (userSite ? '<br><span data-callsite-position>' + esc(userSite[0] + ':' + userSite[1]) + '</span>' : '') + '</span>' +
            (!userSite ? '<div class="tl-context-note">Call-site source unavailable; function entry is a fallback.</div>' : ''));
        else h += kv('caller site', '<span class="tl-muted">Not recorded · capture with -T &apos;.@callsite&apos;</span>');
        if (userDetail.args !== undefined) {
            const provenance = '<div class="tl-context-note">' + (userDetail.arg_name_sources?.includes('prototype') ?
                'Names from uftrace built-in prototypes matched to the saved capture specification; recorded user DWARF takes precedence. Unnamed values use slotN placeholders.' : userDetail.arg_names ?
                'Names from recorded user DWARF; values follow the saved capture specification. Unnamed values use slotN placeholders.' :
                'Parameter names were not recorded; slotN identifies a captured value, not a source parameter name.') + '</div>';
            h += kv('user args', detailArgumentsHtml(userDetail.args, userDetail.arg_names) +
                detailEvidenceHtml(a ? 'Original argument bytes' : 'Argument provenance',
                    provenance + (a ? detailToken('raw', a) : '')), 'tl-arguments', 'arguments');
        }
        else if (a) h += kv('raw argument bytes', detailToken('raw', a), '', 'arguments');
        if (userDetail.retval !== undefined) h += kv('user return', detailValueHtml(userDetail.retval) +
            (userFailure ? ' · ' + esc(userFailure.summary) + '<div class="tl-context-note">' + esc(userFailure.note) + '</div>' : ''),
            userFailure ? 'err' : '', 'return');
        else if (userDetail.return_payload) h += kv('raw return bytes', detailToken('raw', userDetail.return_payload), '', 'return');
    } else if (a) {
        const symbolicArgs = detailEnumArguments(i, a, btf);
        const namedArgs = symbolicArgs ? null : namedArgsOf(i);
        if (symbolicArgs || namedArgs) h += kv('args (BTF)', (symbolicArgs || detailArgumentsHtml(namedArgs)) +
            detailEvidenceHtml(D.uftrace ? 'Raw argument slots' : 'Original argument record', detailToken('raw', a)), 'tl-arguments', 'arguments');
        else h += kv(D.uftrace ? (f & FL.USER ? 'raw payload' : 'raw args') : 'args',
            D.uftrace ? detailToken('raw', a) : detailRecordedArgumentsHtml(a, sArgs[i]), D.uftrace ? '' : 'tl-arguments', 'arguments');
    }
    const symbolicReturn = detailEnumHtml(btfEnumMatch(rawReturn, btf?.ret));
    const returnEvidence = (typedReturn || symbolicReturn) && rawReturn !== null ?
        detailEvidenceHtml(D.uftrace ? 'Original return bits' : 'Original return record', detailToken('raw', rawReturn) +
            (typedReturn?.[0] === 'void' ? '<div>void (BTF) · these bits are not a return value.</div>' : ''), 'tl-return-evidence') : '';
    if (r !== null) {
        let rv = symbolicReturn || detailValueHtml(r);
        if (eRole[i] >= 0) rv += ' ' + esc(errName(eErr[i])) + ' · ' + esc(errRoleText(i));
        else if (f & FL.NULLRET) rv += ' (' + detailToken('keyword', 'NULL') + ')';
        h += kv(typedReturn || symbolicReturn ? 'return (BTF)' : D.uftrace ? 'raw return' : 'return', rv + returnEvidence,
            eRole[i] >= 0 ? 'err' : '', 'return');
    } else if (returnEvidence) {
        h += kv('return type', detailToken('keyword', 'void') + returnEvidence);
    }
    h += kv('depth', String(sDepth[i]));
    if (userDetail) h += kv('recorded address', detailToken('raw', userDetail.address) + '<div class="tl-context-note">Function entry address, not the call site.</div>');
    h += kv('trace lines', lineLink(sLine[i]) + (sEline && sEline[i] && sEline[i] !== sLine[i] ? ' → ' + lineLink(sEline[i]) : ''));
    h += '</div>';
    const sourceBlock = callSiteBlock(i);
    if (sourceBlock !== null) h += '<details class="tl-cs" open>' + callSiteHeading(returnSiteOwner(i) !== i ? 'Shared return-site source' :
            f & FL.USER ? userCallSite(i) ? 'User call site source' : 'User function source (entry)' :
            !sCaller || sCaller[i] < 0 || D.uftrace && !D.sources?.callers?.[sCaller[i]]?.length ? 'Kernel function source' : 'Call site source') +
        (returnSiteOwner(i) !== i ? '<div class="tl-context-note">' + esc(sharedReturnSiteText(i)) + '</div>' : '') + '<div class="tl-cs-src"></div></details>';
    h += '</div><div>';
    h += initialBreakdown.done ? breakdownHtml(initialBreakdown.value) :
        '<div class="tl-context-note" data-detail-breakdown role="status">Calculating time breakdown…</div>';
    const hasErrorChain = context.lists.has('error');
    const stackNeedsSplit = !compactCallDetails.matches && !detailExpanded && !elDetail.classList.contains('show');
    const initialSplit = stackNeedsSplit && !landscapeDetails.matches ? prepareClosedVerticalDetailSplit() : null;
    const stackHtml = deferred => detailListHtml('stack', deferred) + (hasErrorChain || !deferred ? detailContextHint() : '');
    if (hasErrorChain) h += detailListHtml('error');
    h += stackNeedsSplit ? '<div data-stack-mount>' + (initialSplit ? stackHtml(initialSplit.short) : '') + '</div>' :
        stackHtml(deferCallStack());
    h += childPreviewHtml(i, initialPreview.value);
    h += '</div></div>';
    const previousSource = reusableCallSiteSource(sourceBlock);
    openDetail(h, () => {
        if (sourceBlock !== null) fillCallSite(i, undefined, sourceBlock, previousSource);
        if (needsCallTree) {
            const nav = navigationPending ? elDetail.querySelector('.tl-call-nav') : null;
            deferDetailWork('call-tree', callTreeSteps(i), () => { if (nav) refreshCallNavigation(i, nav); });
        }
        if (!initialBreakdown.done) {
            const target = elDetail.querySelector('[data-detail-breakdown]');
            deferDetailWork('breakdown', breakdown, result => { target.outerHTML = breakdownHtml(result); }, after);
        }
        if (!initialPreview.done) {
            const preview = elDetail.querySelector('[data-child-preview]');
            deferDetailWork('children', previewSteps, data => paintChildPreview(preview, data), after);
        }
    }, i, dense, initialSplit, revealOnOpen);
    if (stackNeedsSplit) {
        const deferred = deferCallStack();
        if (initialSplit?.short !== deferred) elDetail.querySelector('[data-stack-mount]').innerHTML = stackHtml(deferred);
    }
}
function callSiteBlock(i) {
    if (typeof fgSourceForLine !== 'function' || isSyn(i)) return null;
    return fgSourceForLine(sLine[i]);
}
const CALL_SITE_POSITION_LIMIT = 256, csPosCache = new Map();
function cachedCallSitePosition(key) {
    const position = csPosCache.get(key);
    if (position !== undefined) {
        csPosCache.delete(key);
        csPosCache.set(key, position);
    }
    return position;
}
function rememberCallSitePosition(key, position) {
    if (csPosCache.size >= CALL_SITE_POSITION_LIMIT) csPosCache.delete(csPosCache.keys().next().value);
    csPosCache.set(key, position);
    return position;
}
function callSitePos(i) {
    if (isSyn(i)) return null;
    const userSite = userCallSite(i);
    if (userSite) return { func: '', at: userSite[0] + ':' + userSite[1], href: null, inl: false };
    if (D.uftrace && !D.uftrace.caller_symbols?.[sCaller?.[i]]) return null;
    const chain = D.sources?.callers?.[sCaller?.[i]];
    if (Array.isArray(chain)) {
        if (!chain.length) return null;
        const frame = chain[chain.length - 1];
        if (Array.isArray(frame) && typeof frame[0] === 'string' && frame[0] &&
            typeof frame[1] === 'string' && frame[1] && Number.isSafeInteger(frame[2]) && frame[2] > 0) {
            return cachedCallSitePosition(chain) ?? rememberCallSitePosition(chain, {
                func:frame[0], at:frame[1] + ':' + frame[2], href:safeSourceUrl(frame[3]) || null, inl:chain.length > 1
            });
        }
    }
    const k = callSiteBlock(i);
    if (k === null) return null;
    const cached = cachedCallSitePosition(k);
    if (cached !== undefined) return cached;
    try {
        if (typeof fgData !== 'undefined' && fgData.sourceReady && !fgData.sourceReady(k)) {
            if (typeof loadCallSitePosition === 'function') loadCallSitePosition(i, k);
            return null;
        }
    } catch (error) { return null; }
    let r = null;
    if (typeof fgSourceHtml === 'function') {
        const tmp = document.createElement('div');
        tmp.innerHTML = fgSourceHtml(k);
        const locs = Array.from(tmp.querySelectorAll('.location-link'));
        const first = locs.findIndex(location => !location.querySelector('strong'));
        const a = first >= 0 ? locs[first] : null;
        if (a) {
            const txt = a.textContent.replace(/^\(inlined\s+by\)\s*/, '');
            const m = txt.match(/^(\S+)\s+at\s+(.*)$/);
            if (m) r = { func: m[1], at: m[2], href: a.getAttribute && a.getAttribute('href'),
                inl: /^\s*\(inlined\s+by\)\s+/.test(locs[first + 1]?.textContent || '') };
        }
    }
    return rememberCallSitePosition(k, r);
}
let detailCallSite = null;
const callSiteObserver = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    const state = detailCallSite;
    if (state?.box.open && window.fgCurrentView() === 'timeline' &&
        entries.some(entry => entry.target === state.box.firstElementChild && entry.isIntersecting)) startCallSiteSource(state);
}, { root: elDetail }) : null;
function reusableCallSiteSource(key) {
    const state = detailCallSite;
    if (key === null || !state?.modeButton || !state.box.open || state.inlineFrame ||
            +state.source.dataset.src !== key || fgSourceJobs.has(state.source)) return null;
    return state.source;
}
function reuseCallSiteSource(state, previous) {
    const rendered = fgRenderedSourceLayouts.get(previous);
    if (!rendered || (rendered.layout !== 'both' && rendered.layout !== fgSourceMode())) return false;
    const source = fgCloneSourceLayout(previous, rendered);
    source.fgSourcePresentation = callSitePresentation;
    if (rendered.layout === 'chain') {
        for (const frame of source.querySelectorAll(':scope > details.src-frame')) {
            const open = frame.classList.contains('src-call');
            if (frame.open !== open) frame.open = open;
        }
    }
    state.source.replaceWith(source);
    state.source = source;
    fgTrackSource(source);
    finishCallSite(state, source.querySelectorAll('.source-line'));
    return true;
}
function startCallSiteSource(state) {
    callSiteObserver?.unobserve(state.box.firstElementChild);
    if (!state.box.open) return;
    const previous = state.previousSource;
    state.previousSource = null;
    if (!previous || !reuseCallSiteSource(state, previous)) fgFill(state.source);
}
function saveCallSiteOpen(state) {
    if (!state || state.open === state.box.open) return false;
    state.open = state.box.open;
    prefSet('csOpen', state.open ? '1' : '0');
    return true;
}
function clearDetailCallSite() {
    if (!detailCallSite) return;
    callSiteObserver?.unobserve(detailCallSite.box.firstElementChild);
    saveCallSiteOpen(detailCallSite);
    fgUntrackSource(detailCallSite.source);
    fgUntrackSource(detailCallSite.modeButton);
    detailCallSite = null;
}
function unavailableCallSite({ box, source, inlineFrame }) {
    if (!inlineFrame) {
        const jump = callDetailReturn?.section === box ? callDetailReturn : null;
        const restoreFocus = box.contains(document.activeElement) || jump?.frame.contains(document.activeElement);
        box.remove();
        if (jump && restoreFocus) restoreCallDetail();
        else {
            if (jump) clearCallDetailReturn();
            if (restoreFocus) elDetail.querySelector('[data-call-actions]')?.focus();
        }
        return;
    }
    source.removeAttribute('data-src');
    source.className = 'tl-cs-src tl-context-note';
    source.textContent = 'Source excerpt not included in this report. Use the inline chain source links when available.';
}
function renderCallSitePosition(index) {
    const at = elDetail.querySelector('.tl-cs-at');
    if (!at || at.querySelector('[data-callsite-position]')) return;
    const pos = callSitePos(index);
    if (!pos) return;
    const link = document.createElement(pos.href ? 'a' : 'span');
    link.className = 'tl-link';
    link.dataset.callsitePosition = '';
    if (pos.href) { link.href = pos.href; link.target = '_blank'; }
    link.textContent = pos.at;
    at.append(document.createElement('br'), link);
    if (pos.inl) { const label = document.createElement('span'); label.className = 'tl-muted'; label.textContent = '  in ' + pos.func + ' (inlined)'; at.append(label); }
}
const callSiteScrollOffsets = new WeakMap();
function callSiteCodeShown(code) {
    return !code.closest('details:not([open])');
}
function callSiteCodeScroll(code) {
    return callSiteCodeShown(code) ? code.scrollLeft : callSiteScrollOffsets.get(code) ?? code.scrollLeft;
}
function rememberCallSiteCodeScroll(code, offset) {
    if (offset) callSiteScrollOffsets.set(code, offset);
    else callSiteScrollOffsets.delete(code);
}
function restoreCallSiteCodeScroll(code) {
    if (!callSiteScrollOffsets.has(code) || !callSiteCodeShown(code)) return;
    code.scrollLeft = callSiteScrollOffsets.get(code);
}
function relayoutCallSiteCode(source, update) {
    const blocks = source.querySelectorAll('.tl-source-code');
    for (const code of blocks) rememberCallSiteCodeScroll(code, callSiteCodeScroll(code));
    update();
    for (const code of blocks) restoreCallSiteCodeScroll(code);
}
function captureCallSiteCodeScroll(source) {
    const offsets = new Map();
    for (const code of source.querySelectorAll('.tl-source-code')) {
        const offset = callSiteCodeScroll(code);
        if (offset) offsets.set(code.dataset.sourceExcerptIndex, offset);
    }
    if (!offsets.size) return null;
    return element => {
        for (const code of element.querySelectorAll('.tl-source-code')) {
            const offset = offsets.get(code.dataset.sourceExcerptIndex);
            if (!offset) continue;
            callSiteScrollOffsets.set(code, offset);
            restoreCallSiteCodeScroll(code);
        }
    };
}
const callSitePresentation = {
    prepare: prepareCallSiteCode, relayout: relayoutCallSiteCode, captureScroll: captureCallSiteCodeScroll,
    isCollapsed: source => !source.parentElement?.open
};
function prepareCallSiteCode(source) {
    const lines = source.querySelectorAll('.source-line');
    let blockIndex = 0;
    for (const line of lines) {
        let code = line.previousElementSibling;
        if (!code?.classList.contains('tl-source-code')) {
            code = document.createElement('div');
            code.className = 'tl-source-code';
            code.dataset.sourceExcerptIndex = blockIndex++;
            code.tabIndex = 0;
            code.setAttribute('role', 'group');
            code.setAttribute('aria-label', 'Source code excerpt');
            line.before(code);
        }
        code.appendChild(line);
    }
}
function callSiteReady(event) {
    const state = detailCallSite;
    if (!state || event.target !== state.source) return;
    finishCallSite(state, state.source.querySelectorAll('.source-line'));
}
function finishCallSite(state, lines) {
    const { box, index } = state;
    renderCallSitePosition(index);
    if (!lines.length) {
        clearDetailCallSite();
        unavailableCallSite(state);
        return;
    }
    if (state.modeButton) return;
    const mode = document.createElement('button');
    mode.type = 'button';
    mode.className = 'tl-btn';
    mode.dataset.fgSourceMode = '';
    mode.innerHTML = '<span class="tl-source-mode-prefix">Source: </span><span data-fg-source-mode-value></span>';
    fgRefreshSourceLayout(mode);
    mode.title = 'Switch source layout between inline chain and flat frames';
    box.querySelector('summary').append(' ', mode);
    state.modeButton = mode;
    fgTrackSource(mode);
    const jump = callDetailReturn;
    if (jump?.section === box && jump.control === document.activeElement &&
            window.fgCurrentView() === 'timeline') {
        for (const line of lines) {
            const bounds = line.getClientRects()[0];
            if (!bounds) continue;
            revealDetailBelow(jump.control, bounds.bottom);
            break;
        }
    }
}
function fillCallSite(index, inlineFrame, key = callSiteBlock(index), previousSource = null) {
    const box = elDetail.querySelector('.tl-cs');
    if (!box) return;
    const source = box.querySelector('.tl-cs-src');
    if (key === null) { unavailableCallSite({ box, source, inlineFrame }); return; }
    source.className = 'tl-cs-src expanded-content';
    source.dataset.src = key;
    source.fgSourceFocus = inlineFrame;
    source.fgSourcePresentation = callSitePresentation;
    if (prefGet('csOpen') === '0') box.open = false;
    if (box.open) previousSource?.remove();
    detailCallSite = { box, source, inlineFrame, index, open: box.open, previousSource: box.open ? previousSource : null };
    renderCallSitePosition(index);
    if (box.open) {
        if (!inlineFrame && callSiteObserver) callSiteObserver.observe(box.firstElementChild);
        else startCallSiteSource(detailCallSite);
    }
}
const compactCallDetails = window.matchMedia('(max-width: 960px), (max-height: 480px)');
function deferCallStack() {
    return compactCallDetails.matches || (!detailExpanded && elDetail.classList.contains('tl-detail-short'));
}
const flowSliceHead = window.matchMedia('(max-height: 600px), (max-width: 640px)');
const pinCompactSliceHead = window.matchMedia('(min-height: 481px)');
let observedDetailHead = null, observedSliceHead = null, observedDetailHeadHeight = 0;
function detailHeadInset() {
    if (elDetail.classList.contains('tl-detail-short')) return 0;
    const head = elDetail.querySelector('.tl-detail-head');
    const style = head && getComputedStyle(head);
    return style?.position === 'sticky' && style.top !== 'auto' ? head.offsetHeight : 0;
}
function sizeDetailHead() {
    const flowing = !tlRoot.classList.contains('tl-detail-reading') && flowSliceHead.matches &&
        !(compactToolbar.matches && pinCompactSliceHead.matches);
    const short = elDetail.classList.contains('tl-detail-short');
    const height = observedSliceHead ?
        (flowing || short ? 0 : observedDetailHeadHeight) : detailHeadInset();
    const offset = height + 'px';
    const recorded = (observedSliceHead || elDetail.firstElementChild)?.classList.contains('tl-recorded-call-head');
    const padding = recorded && !short ? offset : '';
    if (elDetail.style.scrollPaddingBlockStart !== padding) elDetail.style.scrollPaddingBlockStart = padding;
    if (!recorded && elDetail.style.getPropertyValue('--tl-detail-table-top') !== offset)
        elDetail.style.setProperty('--tl-detail-table-top', offset);
    scheduleDistHistogram();
}
function revealDetailTable(table, minimumTop = -Infinity) {
    const top = elDetail.getBoundingClientRect().top + elDetail.clientTop + detailHeadInset();
    elDetail.scrollTop += table.getBoundingClientRect().top - Math.max(top, minimumTop);
}
function summaryPageAtRightEdge() {
    const left = elDetail.scrollLeft;
    return left > 0 && elDetail.scrollWidth - elDetail.clientWidth - left <= 1;
}
function revealSummaryPage(table, keepRightEdge) {
    if (keepRightEdge) elDetail.scrollLeft = elDetail.scrollWidth - elDetail.clientWidth;
    revealPagedTable(table, '.tl-range-pages, .an-window-pages');
}
function revealPagedTable(table, controlsSelector, flow = elDetail.classList.contains('tl-detail-short')) {
    if (!flow) { revealDetailTable(table); return; }
    const controls = table.previousElementSibling?.querySelector(controlsSelector);
    const firstRow = table.tBodies[0]?.firstElementChild;
    if (!controls || !firstRow) return;
    const top = elDetail.getBoundingClientRect().top + elDetail.clientTop;
    const bottom = top + elDetail.clientHeight;
    const rowShift = Math.ceil(Math.max(0, firstRow.getBoundingClientRect().bottom - bottom));
    const controlLimit = Math.floor(controls.getBoundingClientRect().top - top);
    const shift = Math.min(rowShift, controlLimit);
    if (shift) elDetail.scrollTop += shift;
}
function positionTablePager(table, pager, flow = elDetail.classList.contains('tl-detail-short')) {
    if (!table || !pager) return;
    if ((flow ? pager.nextElementSibling : pager.previousElementSibling) === table) return;
    const focused = document.activeElement;
    if (flow) table.before(pager);
    else table.after(pager);
    if (pager.contains(focused)) focused.focus({ preventScroll: !flow });
}
function positionSummaryPager() {
    const rangeSummary = AN.kind === 'range';
    positionTablePager(elDetail.querySelector(rangeSummary ? '.tl-range-table' : '.an-window-table'),
        elDetail.querySelector(rangeSummary ? '.tl-range-footer:has(.tl-range-pages)' : '.an-window-footer:has(.an-window-pages)'));
}
const detailHeadObserver = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(entries => {
    const head = entries.find(entry => entry.target === observedDetailHead);
    if (head && head.target === observedSliceHead)
        observedDetailHeadHeight = Math.round(head.borderBoxSize?.[0]?.blockSize ?? head.target.offsetHeight);
    const pane = observedSliceHead && entries.find(entry => entry.target === elDetail);
    if (pane?.contentRect.height > 0) updateShortDetail(Math.round(pane.contentRect.height));
    if (head) sizeDetailHead();
}) : null;
detailHeadObserver?.observe(elDetail);
function updateDetailExpand() {
    const button = elDetail.querySelector('[data-detail-expand]');
    if (!button) return;
    const label = detailExpanded ? 'Restore chart and detail split' : 'Expand details into the chart area';
    button.title = label;
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-pressed', String(detailExpanded));
}
function focusTimelineControl() {
    const target = detailExpanded ? elDetail.querySelector('[data-detail-expand]') : elCv;
    if (target) target.focus({ preventScroll: true });
}
let pendingTextFocus = null;
function restoreTextFocus() {
    const request = pendingTextFocus;
    if (!request) return;
    const tab = document.querySelector('.view-tab[data-view="text"]'), active = document.activeElement;
    if (!request.current() || active !== document.body && active !== tab && !tlRoot.contains(active)) {
        pendingTextFocus = null;
        return;
    }
    if (!document.fullscreenElement) tab?.focus({ preventScroll: true });
    if (!fullscreenPending && !nativeFullscreen && document.fullscreenElement !== tlRoot) pendingTextFocus = null;
}
for (const kind of ['pointerdown', 'keydown', 'focusin']) document.addEventListener(kind, event => {
    if (!pendingTextFocus) return;
    if (kind === 'focusin' && (event.target === document.body || tlRoot.contains(event.target) ||
        event.target?.matches?.('.view-tab[data-view="text"]'))) return;
    pendingTextFocus = null;
}, true);
let detailSplitScroll = null;
function detailOuterScroller() { return tlRoot.classList.contains('tl-fullscreen') ? tlRoot : window; }
function detailOuterScrollTop(scroller) { return scroller === window ? window.scrollY : scroller.scrollTop; }
function setDetailExpanded(on, preserveSplitScroll = false, reading = null) {
    stopDetailDrag?.();
    detailExpanded = on && elDetail.classList.contains('show');
    const scroller = preserveSplitScroll ? detailOuterScroller() : null;
    const previous = detailSplitScroll;
    detailSplitScroll = detailExpanded && scroller ? { scroller, reading, top: detailOuterScrollTop(scroller) } : null;
    tlRoot.classList.toggle('tl-detail-expanded', detailExpanded);
    tlRoot.classList.toggle('tl-detail-reading', detailExpanded && !!reading);
    updateDetailExpand();
    resize();
    sizeDetailHead();
    if (!detailExpanded && scroller && previous?.scroller === scroller &&
        previous.top !== detailOuterScrollTop(scroller))
        scroller.scrollTo({ top: previous.top, behavior: 'instant' });
    const button = elDetail.querySelector('[data-detail-expand]');
    if (button === document.activeElement && !button.getClientRects().length) {
        elDetail.querySelector('.tl-close')?.focus({ preventScroll: true });
    }
}
function closeCallActions(restoreFocus = false) {
    if (elCallActions.hidden) return;
    const trigger = callActionsTrigger;
    elCallActions.hidden = true;
    trigger?.setAttribute('aria-expanded', 'false');
    callActionsTrigger = null;
    callActionsSelection = -1;
    callActionsLayout = null;
    if (restoreFocus && trigger?.isConnected && trigger.getClientRects().length && window.fgCurrentView() === 'timeline')
        trigger.focus({ preventScroll: true });
}
function callActionItems() {
    return Array.from(elCallActions.querySelectorAll('[role=menuitem]:not(:disabled)'));
}
function updateCallSymbolAction(index) {
    const title = elDetail.querySelector('.tl-recorded-call-head .tl-detail-title');
    const action = elCallActions.querySelector('[data-call-action=symbol]');
    const needed = nameOf(index) !== sliceLabel(index) || title &&
        (title.scrollHeight > title.clientHeight + 1 || title.scrollWidth > title.clientWidth + 1);
    if (!needed) action?.remove();
    else if (!action) elCallActions.querySelector('[data-call-action=text]').insertAdjacentHTML('afterend',
        '<button type="button" role="menuitem" tabindex="-1" data-call-action="symbol">Show recorded symbol</button>');
}
const callDetailActions = [
    ['detail-prototype', 'Show function prototype', '.tl-btf-prototype > summary', true],
    ['detail-arguments', 'Show recorded arguments', '[data-recorded-field=arguments] > .k', true],
    ['detail-return', 'Show recorded return value', '[data-recorded-field=return] > .k', true],
    ['detail-source', 'Show source excerpt', '.tl-cs > summary', true],
    ['detail-stack', 'Show call stack', '[data-stack-toggle]']
];
let callDetailReturn = null, callDetailReturnFrame = 0;
const callDetailReturnObserver = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(syncCallDetailReturnEnd) : null;
function queueCallDetailReturnEnd() {
    if (!callDetailReturnObserver || !callDetailReturn?.end || callDetailReturnFrame || window.fgCurrentView() !== 'timeline') return;
    callDetailReturnFrame = requestAnimationFrame(() => {
        callDetailReturnFrame = 0;
        syncCallDetailReturnEnd();
    });
}
function syncCallDetailReturnEnd() {
    const jump = callDetailReturn;
    if (!jump?.end || window.fgCurrentView() !== 'timeline' || !elDetail.clientHeight) return;
    const { section, content, control, button, end } = jump;
    let showEnd = false;
    if (section.matches('details[open]') || section.querySelector('details[open]')) {
        const { top, bottom } = detailReadingBounds();
        const height = bottom - top;
        if (height <= 0) return;
        const heading = control.getBoundingClientRect();
        const start = Math.min(heading.top, button.getBoundingClientRect().top);
        const sectionEnd = Math.max(heading.bottom, content.getBoundingClientRect().bottom);
        showEnd = sectionEnd - start + 8 > height;
    }
    if (end.hidden === !showEnd) return;
    const moveFocus = !showEnd && document.activeElement === end;
    end.hidden = !showEnd;
    if (moveFocus) {
        revealDetailTable(button, detailViewportTop() + 4);
        button.focus({ preventScroll: true });
    }
}
function clearCallDetailReturn() {
    const jump = callDetailReturn;
    if (!jump) return;
    callDetailReturn = null;
    if (callDetailReturnFrame) {
        cancelAnimationFrame(callDetailReturnFrame);
        callDetailReturnFrame = 0;
    }
    if (jump.end) callDetailReturnObserver?.disconnect();
    if (jump.section.parentElement === jump.frame) jump.frame.replaceWith(jump.section);
    else jump.frame.remove();
}
function restoreCallDetail() {
    const jump = callDetailReturn;
    if (!jump) return;
    const reading = detailSplitScroll?.reading;
    const endedReading = reading && reading === jump.reading && !reading.summary.parentElement?.open;
    clearCallDetailReturn();
    if (endedReading) setDetailExpanded(false, true);
    else if ((jump.expandedForJump || reading && reading !== jump.reading) && detailExpanded) {
        setDetailExpanded(false);
        if (jump.scroller === detailOuterScroller() && detailOuterScrollTop(jump.scroller) !== jump.pageTop)
            jump.scroller.scrollTo({ top: jump.pageTop, behavior: 'instant' });
    }
    restoreDetailOpener(jump.trigger, endedReading ? reading : jump);
}
function restoreDetailOpener(opener, { top, left }) {
    elDetail.scrollTo({ top, left, behavior: 'instant' });
    if (!opener.isConnected) return;
    const paneTop = elDetail.getBoundingClientRect().top + elDetail.clientTop;
    const bounds = opener.getBoundingClientRect();
    const bottom = paneTop + elDetail.clientHeight;
    if (bounds.top < paneTop) elDetail.scrollTop += bounds.top - paneTop;
    else if (bounds.bottom > bottom) elDetail.scrollTop += bounds.bottom - bottom;
    opener.focus({ preventScroll: true });
}
function closeReadingDisclosure(summary = detailSplitScroll?.reading?.summary) {
    const reading = detailSplitScroll?.reading;
    if (!reading || callDetailReturn || summary !== reading.summary || !summary.parentElement?.open) return false;
    summary.parentElement.open = false;
    setDetailExpanded(false, true);
    restoreDetailOpener(summary, reading);
    return true;
}
function syncCallChildrenAction(explorer) {
    if (elCallActions.hidden) return;
    const action = elCallActions.querySelector('[data-call-action=children]');
    if (!action) return;
    const focused = document.activeElement === action;
    if (!explorer) {
        const group = action.parentElement;
        action.remove();
        if (!group.childElementCount) group.remove();
    } else {
        action.disabled = explorer.getAttribute('aria-disabled') === 'true';
        action.title = action.disabled ? 'Preparing child summary…' : '';
    }
    if (focused && (!explorer || action.disabled)) focusOverlayControl(elCallActions, callActionItems()[0]);
}
function updateCallDetailActions() {
    elCallActions.querySelector('[data-call-detail-actions]')?.remove();
    const compact = compactCallDetails.matches || elDetail.classList.contains('tl-detail-short');
    const buttons = callDetailActions.filter(([action, , selector]) =>
        (compact || action === 'detail-stack') && elDetail.querySelector(selector))
        .map(([action, label]) => '<button type="button" role="menuitem" tabindex="-1" data-call-action="' + action + '">' + label + '</button>');
    const explorer = compact && elDetail.querySelector('[data-child-open]');
    if (explorer) buttons.push('<button type="button" role="menuitem" tabindex="-1" data-call-action="children"' +
        (explorer.getAttribute('aria-disabled') === 'true' ? ' disabled title="Preparing child summary…"' : '') + '>Explore all children</button>');
    if (!buttons.length) return;
    const previous = elCallActions.querySelector('[data-call-action=symbol]') || elCallActions.querySelector('[data-call-action=text]');
    previous.insertAdjacentHTML('afterend', '<div role="group" aria-label="In this call’s details" data-call-detail-actions>' + buttons.join('') + '</div>');
}
function updateDenseZoomAction() {
    const action = elCallActions.querySelector('[data-call-action=zoom-pixel]');
    const compact = compactCallDetails.matches || elDetail.classList.contains('tl-detail-short');
    if (!compact || !elDetail.querySelector('[data-dense-start]')) { action?.remove(); return; }
    const anchor = elCallActions.querySelector('[data-call-action=text]');
    if (!action) anchor.insertAdjacentHTML('afterend',
        '<button type="button" role="menuitem" tabindex="-1" data-call-action="zoom-pixel">Zoom into pixel</button>');
    else if (anchor.nextElementSibling !== action) anchor.after(action);
}
function syncCallNavigationActions(nav) {
    for (const item of elCallActions.querySelectorAll('[data-call-nav-actions] button')) {
        const button = nav.querySelector('[data-call-nav="' + item.dataset.callAction.slice('navigate-'.length) + '"]');
        syncCallNavigationControl(item, button.title, button.disabled);
    }
}
function updateCallNavigationActions() {
    elCallActions.querySelector('[data-call-nav-actions]')?.remove();
    if (!compactCallDetails.matches || detailExpanded || isLandscapeDetail() || !elDetail.classList.contains('tl-detail-short')) return;
    const nav = elDetail.querySelector('.tl-recorded-call-nav');
    if (!nav) return;
    const faces = {parent: ['↑', 'Parent'], child: ['↓', 'Child'], previous: ['‹', 'Prev'], next: ['›', 'Next']};
    const items = CALL_NAVIGATION.map(([action]) => {
        const [arrow, label] = faces[action];
        return '<button type="button" role="menuitem" tabindex="-1" data-call-action="navigate-' + action + '">' +
            '<span class="tl-call-nav-arrow" aria-hidden="true">' + arrow + '</span><span>' + label + '</span></button>';
    });
    const previous = elCallActions.querySelector('[data-call-action=symbol]') || elCallActions.querySelector('[data-call-action=text]');
    previous.insertAdjacentHTML('afterend', '<div role="group" aria-label="Navigate recorded calls" data-call-nav-actions>' +
        '<div class="tl-call-nav-menu-title" aria-hidden="true">Navigate calls</div>' + items.join('') + '</div>');
    syncCallNavigationActions(nav);
}
function showCallDetailSection(action, trigger) {
    const destination = callDetailActions.find(([key]) => key === action);
    const control = destination && elDetail.querySelector(destination[2]);
    if (!control) { trigger?.focus({ preventScroll: true }); return; }
    const scroller = detailOuterScroller();
    const origin = callDetailReturn || { trigger, top: elDetail.scrollTop, left: elDetail.scrollLeft,
        scroller, pageTop: detailOuterScrollTop(scroller), reading: detailSplitScroll?.reading };
    const field = control.closest('.tl-recorded-field');
    if (control.matches('[data-stack-toggle]')) {
        if (control.getAttribute('aria-expanded') === 'false') toggleDetailStack(control);
    } else if (!field) {
        control.parentElement.open = true;
        if (control.parentElement === detailCallSite?.box) startCallSiteSource(detailCallSite);
    }
    if (!control.isConnected) { trigger?.focus(); return; }
    detailScrollResetPending = false;
    clearCallDetailReturn();
    const section = field || control.closest('details, .tl-context-list');
    const [, , , needsReadingRoom] = destination;
    const prototypeJump = action === 'detail-prototype';
    const short = elDetail.classList.contains('tl-detail-short');
    const prototypeBounds = prototypeJump && !detailExpanded && !short ? detailReadingBounds() : null;
    const clippedPrototype = prototypeBounds && prototypeBounds.bottom - prototypeBounds.top < SHORT_DETAIL_HEIGHT;
    const expand = needsReadingRoom && !detailExpanded && (short || clippedPrototype);
    const sourceJump = action === 'detail-source';
    const frame = document.createElement('div');
    frame.className = 'tl-detail-jump';
    if (sourceJump) frame.classList.add('tl-detail-source-jump');
    if (prototypeJump && expand) frame.classList.add('tl-detail-prototype-jump');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'tl-btn tl-detail-return';
    button.dataset.detailReturn = '';
    button.setAttribute('aria-label', 'Return to call details');
    button.title = 'Return to call details';
    button.innerHTML = '←<span class="tl-detail-return-label"> Call details</span>';
    section.before(frame);
    frame.append(button, section);
    const end = needsReadingRoom ? button.cloneNode(true) : null;
    const content = field?.lastElementChild ?? section;
    if (end) {
        end.dataset.detailReturn = 'end';
        end.setAttribute('aria-label', 'Return to call details from end of section');
        if (callDetailReturnObserver) {
            end.hidden = true;
            frame.classList.add('tl-detail-jump-measured');
        }
        frame.append(end);
    }
    callDetailReturn = { ...origin, section, frame, button, control, end, content, expandedForJump: expand || origin.expandedForJump };
    if (expand) setDetailExpanded(true);
    const { top, bottom } = detailReadingBounds();
    const heading = control.getBoundingClientRect(), back = button.getBoundingClientRect();
    const start = Math.min(heading.top, back.top);
    const fits = Math.max(heading.bottom, back.bottom) - start + 8 <= bottom - top;
    elDetail.scrollTop += (fits ? start : heading.top) - top - 4;
    control.focus({ preventScroll: true });
    if (end && callDetailReturnObserver) {
        callDetailReturnObserver.observe(elDetail);
        callDetailReturnObserver.observe(elDetail.firstElementChild);
        callDetailReturnObserver.observe(content);
    }
}
const recordedSymbolOrigins = new WeakMap();
function showRecordedSymbol(index, opener) {
    let panel = elDetail.querySelector('.tl-recorded-symbol');
    if (!panel) {
        const origin = { opener, top: elDetail.scrollTop, left: elDetail.scrollLeft };
        const body = elDetail.querySelector('.tl-detail-body');
        body.insertAdjacentHTML('beforebegin', '<section class="tl-recorded-symbol" aria-label="Recorded symbol">' +
            '<button type="button" class="tl-btn tl-recorded-symbol-back" data-hide-symbol aria-label="Hide symbol" title="Hide symbol and return to call details">←</button>' +
            '<h3 tabindex="-1">Recorded symbol</h3><pre><code>' + esc(nameOf(index)) + '</code></pre>' +
            (modOf(index) ? '<div class="tl-context-note">Module: ' + esc(modOf(index)) + '</div>' : '') +
            '<div class="tl-recorded-symbol-controls">' +
            '<button type="button" class="tl-btn" data-copy-symbol>Copy symbol</button>' +
            '<button type="button" class="tl-btn" data-hide-symbol>Hide symbol</button>' +
            '<div class="tl-context-note tl-copy-status" role="status"></div></div></section>');
        panel = body.previousElementSibling;
        recordedSymbolOrigins.set(panel, origin);
    }
    elDetail.querySelector('[data-call-symbol]').setAttribute('aria-expanded', 'true');
    const heading = panel.querySelector('h3');
    const inset = detailHeadInset();
    elDetail.scrollTop += panel.getBoundingClientRect().top - elDetail.getBoundingClientRect().top - elDetail.clientTop - inset;
    heading.focus({ preventScroll: true });
}
function hideRecordedSymbol(panel, opener) {
    const title = elDetail.querySelector('[data-call-symbol]');
    const origin = recordedSymbolOrigins.get(panel);
    panel.remove();
    title?.setAttribute('aria-expanded', 'false');
    if (origin) restoreDetailOpener(opener || origin.opener, origin);
}
async function copyRecordedSymbol(button) {
    const panel = button.closest('.tl-recorded-symbol');
    const exact = panel.querySelector('code').textContent;
    const status = panel.querySelector('[role=status]');
    const current = () => button.isConnected && elDetail.classList.contains('show') && window.fgCurrentView() === 'timeline';
    status.textContent = '';
    const copied = await fgCopyPlainText(exact, current);
    if (current()) status.textContent = copied ? 'Symbol copied' : 'Copy failed. Select the symbol to copy it.';
}
function focusOverlayControl(overlay, item) {
    if (!item) return;
    item.focus({ preventScroll: true });
    if (item === overlay) return;
    const menu = overlay.getBoundingClientRect(), bounds = item.getBoundingClientRect();
    const top = menu.top + overlay.clientTop, bottom = top + overlay.clientHeight;
    if (bounds.top < top) overlay.scrollTop += bounds.top - top;
    else if (bounds.bottom > bottom) overlay.scrollTop += bounds.bottom - bottom;
}
function openCallActions(trigger, last = false) {
    if (sel < 0 || sel >= N) return;
    closeCallActions();
    setToolbarOptions(false);
    updateCallSymbolAction(sel);
    updateCallDetailActions();
    updateCallNavigationActions();
    updateDenseZoomAction();
    callActionsTrigger = trigger;
    callActionsSelection = sel;
    const pendingFilter = window.fgFilterProgress && !window.fgFilterProgress.error;
    for (const button of elCallActions.querySelectorAll('[data-call-action^="focus-"]')) {
        const available = button.dataset.callAction !== 'focus-task' || callTaskFilter(sel) !== null;
        button.disabled = !!pendingFilter || !available;
        button.title = pendingFilter ? 'Wait for filtering to finish, or cancel it first' : !available ?
            'No task identity available to the shared filter' : 'Add an exact condition to the filter draft; Apply to confirm';
    }
    trigger.setAttribute('aria-expanded', 'true');
    elCallActions.hidden = false;
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
    const edge = 8, gap = 4;
    elCallActions.style.width = Math.max(1, Math.min(248, width - 2 * edge)) + 'px';
    elCallActions.style.maxHeight = Math.max(1, height - 2 * edge) + 'px';
    const anchor = trigger.getBoundingClientRect(), menu = elCallActions.getBoundingClientRect();
    callActionsLayout = { x: anchor.x, y: anchor.y, width: anchor.width, height: anchor.height,
        scrollTop: elDetail.scrollTop, scrollX, scrollY, viewportLeft: left, viewportTop: top,
        viewportWidth: width, viewportHeight: height };
    const below = anchor.bottom + gap;
    const above = anchor.top - gap - menu.height;
    const fitsBelow = below + menu.height <= top + height - edge;
    let target = fitsBelow ? below : above;
    let horizontal = anchor.right - menu.width;
    if (!fitsBelow && above < top + edge && tlRoot.classList.contains('tl-landscape-details')) {
        const before = anchor.left - gap - menu.width, after = anchor.right + gap;
        if (before >= left + edge || after + menu.width <= left + width - edge) {
            horizontal = before >= left + edge ? before : after;
            target = anchor.top;
        }
    }
    elCallActions.style.left = Math.max(left + edge, Math.min(horizontal, left + width - menu.width - edge)) + 'px';
    elCallActions.style.top = Math.max(top + edge, Math.min(target, top + height - menu.height - edge)) + 'px';
    elCallActions.scrollTop = 0;
    const items = callActionItems();
    focusOverlayControl(elCallActions, items[last ? items.length - 1 : 0]);
}
function callActionsKey(event) {
    const trigger = event.target.closest?.('[data-call-actions]');
    if (elCallActions.hidden) {
        const jump = callDetailReturn;
        if (jump && event.key === 'Escape' && event.target.closest?.('.tl-detail-jump') === jump.frame &&
                !event.target.closest('input, textarea, select, [contenteditable]')) {
            event.preventDefault();
            restoreCallDetail();
            return true;
        }
        if (jump && event.key === 'Tab' && !event.altKey && !event.ctrlKey && !event.metaKey &&
                event.target === (event.shiftKey ? jump.control : jump.button)) {
            const next = event.shiftKey ? jump.button : jump.control;
            const { top, bottom } = detailReadingBounds();
            const bounds = next.getBoundingClientRect();
            if (bounds.top < top || bounds.height > bottom - top) elDetail.scrollTop += Math.floor(bounds.top - top);
            else if (bounds.bottom > bottom) elDetail.scrollTop += Math.ceil(bounds.bottom - bottom);
            event.preventDefault();
            next.focus({ preventScroll: true });
            return true;
        }
        const symbol = event.key === 'Escape' && event.target.closest?.('.tl-recorded-symbol');
        if (symbol) {
            event.preventDefault();
            hideRecordedSymbol(symbol);
            return true;
        }
        if (trigger && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
            event.preventDefault();
            openCallActions(trigger, event.key === 'ArrowUp');
            return true;
        }
        return false;
    }
    if (event.key === 'Escape' || event.key === 'Tab') {
        if (event.key === 'Escape') event.preventDefault();
        closeCallActions(true);
        return true;
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const items = callActionItems(), current = items.indexOf(document.activeElement);
        const index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 :
            (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        focusOverlayControl(elCallActions, items[index]);
        return true;
    }
    if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') &&
            !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        return true;
    }
    if (event.key.length === 1 && event.key !== ' ' && !event.ctrlKey && !event.metaKey && !event.altKey)
        closeCallActions(true);
    return false;
}
function dismissCallActions(event) {
    if (!elCallActions.hidden && !elCallActions.contains(event.target) && !callActionsTrigger?.contains(event.target))
        closeCallActions();
}
function closeMovedCallActions() {
    if (elCallActions.hidden) return;
    const anchor = callActionsTrigger.getBoundingClientRect(), previous = callActionsLayout;
    const viewport = window.visualViewport;
    if (anchor.x !== previous.x || anchor.y !== previous.y || anchor.width !== previous.width ||
        anchor.height !== previous.height || elDetail.scrollTop !== previous.scrollTop ||
        scrollX !== previous.scrollX || scrollY !== previous.scrollY ||
        (viewport?.offsetLeft || 0) !== previous.viewportLeft || (viewport?.offsetTop || 0) !== previous.viewportTop ||
        (viewport?.width || innerWidth) !== previous.viewportWidth || (viewport?.height || innerHeight) !== previous.viewportHeight)
        closeCallActions(elCallActions.contains(document.activeElement));
}
document.addEventListener('pointerdown', dismissCallActions);
document.addEventListener('focusin', dismissCallActions);
elDetail.addEventListener('scroll', closeMovedCallActions, { passive: true });
elDetail.addEventListener('scroll', event => {
    const code = event.target;
    if (code.matches('.tl-source-code') && window.fgCurrentView() === 'timeline' && callSiteCodeShown(code))
        rememberCallSiteCodeScroll(code, code.scrollLeft);
}, { capture: true, passive: true });
elDetail.addEventListener('toggle', loadBtfDisclosure, true);
for (const kind of ['input', 'compositionend']) elDetail.addEventListener(kind, filterEnumNames);
elDetail.addEventListener('keydown', enumFilterKey);
elDetail.addEventListener('fg-source-ready', callSiteReady, true);
elDetail.addEventListener('toggle', event => {
    const state = detailCallSite;
    if (event.target.open && state?.box.contains(event.target) && event.target.matches('.tl-cs, .src-frame')) {
        for (const code of event.target.querySelectorAll('.tl-source-code')) restoreCallSiteCodeScroll(code);
    }
    if (event.target !== state?.box || !saveCallSiteOpen(state)) return;
    if (state.open) startCallSiteSource(state);
    else {
        callSiteObserver?.unobserve(state.box.firstElementChild);
        fgCancelSource(state.source);
    }
}, true);
function callDetailViewportChanged() {
    closeMovedCallActions();
    queueCallDetailReturnEnd();
}
window.addEventListener('scroll', callDetailViewportChanged, { passive: true });
window.visualViewport?.addEventListener('scroll', callDetailViewportChanged, { passive: true });
window.visualViewport?.addEventListener('resize', callDetailViewportChanged, { passive: true });
elCallActions.addEventListener('click', event => {
    const action = event.target.closest('[data-call-action]');
    if (!action || action.disabled) return;
    const index = callActionsSelection;
    const trigger = callActionsTrigger;
    closeCallActions();
    if (index < 0 || index >= N || index !== sel) return;
    if (action.dataset.callAction === 'distribution') {
        showDist(sName[index]);
        focusTimelineControl();
    } else if (action.dataset.callAction === 'text') {
        gotoText(sLine[index], trigger);
        if (window.fgCurrentView() !== 'text') focusTimelineControl();
    } else if (action.dataset.callAction === 'symbol') {
        showRecordedSymbol(index, trigger);
    } else if (action.dataset.callAction === 'children') {
        const explorer = elDetail.querySelector('[data-child-open]');
        if (!explorer || explorer.getAttribute('aria-disabled') === 'true') {
            trigger?.focus({ preventScroll: true });
            return;
        }
        showChildren(index, { top: elDetail.scrollTop, left: elDetail.scrollLeft });
        const parent = elDetail.querySelector('[data-child-parent]');
        resetDetailScroll(parent);
        parent?.focus({ preventScroll: true });
    } else if (action.dataset.callAction === 'zoom-pixel') {
        const zoom = elDetail.querySelector('[data-dense-start]');
        if (zoom) zoomIntoDensePixel(zoom);
        const bounds = trigger.getBoundingClientRect();
        if (trigger.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)))
            trigger.focus({preventScroll: true});
        else focusTimelineControl();
    } else if (action.dataset.callAction.startsWith('navigate-')) {
        navigateCall(action.dataset.callAction.slice('navigate-'.length), 'actions');
    } else if (action.dataset.callAction.startsWith('detail-')) {
        showCallDetailSection(action.dataset.callAction, trigger);
    } else if (action.dataset.callAction.startsWith('focus-')) {
        stageCallFilter(index, action.dataset.callAction);
    }
});
let detailSliceIndex = null;
let detailScrollResetPending = false;
let closedDetailWasScrolled = false;
let detailScrollFrame = 0;
let rangeJob = null;
let rangeCsvJob = null;
function detailIsScrolled() { return elDetail.scrollTop !== 0 || elDetail.scrollLeft !== 0; }
function resetDetailScroll(control = null) {
    detailScrollResetPending = false;
    let top = 0;
    if (control) {
        const edge = 4;
        const origin = elDetail.getBoundingClientRect().top + elDetail.clientTop - elDetail.scrollTop;
        const bounds = control.getBoundingClientRect();
        const bottom = Math.ceil(bounds.bottom - origin - elDetail.clientHeight + edge);
        if (bottom > 0) {
            const start = Math.floor(bounds.top - origin - detailHeadInset() - edge);
            top = Math.max(0, Math.min(start, bottom));
        }
    }
    elDetail.scrollTo({ top, left: 0, behavior: 'instant' });
}
function scheduleDetailScrollReset() {
    if (!detailScrollResetPending || detailScrollFrame) return;
    detailScrollFrame = requestAnimationFrame(() => {
        detailScrollFrame = 0;
        if (!detailScrollResetPending || window.fgCurrentView() !== 'timeline') return;
        resetDetailScroll();
    });
}
function updateDenseSelection(head, dense) {
    if (!head) return;
    const next = head.nextElementSibling;
    const notice = next?.classList.contains('tl-dense-selection') ? next : null;
    if (!dense) { notice?.remove(); return; }
    const description = dense.count + ' frames shared this pixel' +
        (dense.filtered ? '; ' + dense.matches + ' matched the search/filter' : '') + '. Showing the ' +
        (dense.filtered && dense.matches ? 'first matching' : 'first') + ' frame.';
    const start = String(dense.start), end = String(dense.end);
    if (notice) {
        if (notice.firstElementChild.textContent !== description) notice.firstElementChild.textContent = description;
        const button = notice.lastElementChild;
        if (button.dataset.denseStart !== start) button.dataset.denseStart = start;
        if (button.dataset.denseEnd !== end) button.dataset.denseEnd = end;
    } else {
        head.insertAdjacentHTML('afterend', '<div class="tl-context-note tl-dense-selection"><span>' + description +
            '</span><button type="button" class="tl-btn" data-dense-start="' + start + '" data-dense-end="' + end + '">Zoom into pixel</button></div>');
    }
}
function zoomIntoDensePixel(button) {
    const {denseStart, denseEnd} = button.dataset;
    const start = Number(denseStart), end = Number(denseEnd);
    const roundingRoom = Math.max(Math.abs(view.s), Math.abs(view.e)) * Number.EPSILON * 2;
    if (Math.max(end - start, MIN_ZOOM_SPAN) * 1.16 >= view.e - view.s - roundingRoom) {
        toast('Cannot zoom further into this pixel. Use Find or Text to locate a specific call.');
        return;
    }
    if (detailExpanded) setDetailExpanded(false);
    zoomTo(start, end, -1);
    if (sel >= 0) revealRow(sel, {expand: false});
}
function showDetailSplit(initialSplit) {
    if (initialSplit) elDetail.style.height = initialSplit.height + 'px';
    elDetail.classList.add('show');
    elResizer.classList.add('show');
}
function positionCallContext(head = elDetail.firstElementChild) {
    if (!head?.classList.contains('tl-recorded-call-head')) return;
    if (elDetail.classList.contains('tl-detail-short')) {
        const context = head.querySelector(':scope > .tl-call-context');
        if (context) elDetail.querySelector(':scope > .tl-recorded-call-body > .tl-left')?.prepend(context);
    } else {
        const context = elDetail.querySelector(':scope > .tl-recorded-call-body > .tl-left > .tl-call-context');
        if (context) head.querySelector(':scope > .tl-call-controls')?.before(context);
    }
}
function openDetail(html, fillContent, sliceIndex = null, dense = null, initialSplit = null, revealInEmptySplit = null) {
    if (childListReturn?.index !== sliceIndex) childListReturn = null;
    rangeJob = null;
    rangeCsvJob = null;
    cancelChildrenCsv();
    cancelDistCsv();
    cancelSlowFastCsv();
    cancelWindowCsv(false);
    cancelErrorCsv();
    cancelTaskCsv();
    resetWaitWork();
    clearDetailWork();
    const keepCsv = sliceIndex !== null && sliceIndex === detailSliceIndex ? contextCsvJob : null;
    const keepCsvFocus = keepCsv?.button === document.activeElement;
    if (!keepCsv) cancelContextCsv();
    if (AN.kind !== 'kvm') selKvm = null;
    const was = elDetail.classList.contains('show');
    const preparedSplit = initialSplit ?? (!was && landscapeDetails.matches && !detailExpanded && sliceIndex !== null &&
        sliceIndex < N ? prepareClosedLandscapeDetailSplit() : null);
    const measureBeforeMount = revealInEmptySplit && (preparedSplit?.width !== undefined || initialSplit && !initialSplit.short);
    if (sliceIndex !== detailSliceIndex) {
        detailSliceIndex = sliceIndex;
        detailScrollResetPending = sliceIndex !== null && (detailScrollResetPending ||
            window.fgCurrentView() !== 'timeline' || (was ? detailIsScrolled() : closedDetailWasScrolled));
    }
    closeCallActions();
    const reading = detailSplitScroll?.reading;
    const restoreSplit = (callDetailReturn?.expandedForJump || reading) && detailExpanded;
    clearCallDetailReturn();
    const replacingSlice = observedSliceHead !== null;
    if (sliceIndex !== null && detailCallSite?.box.open && fgSourceJobs.has(detailCallSite.source)) {
        fgData.keepSourceFlight(+detailCallSite.source.dataset.src, callSiteBlock(sliceIndex));
    }
    clearDetailCallSite();
    fgCancelSources(elDetail);
    if (measureBeforeMount) {
        showDetailSplit(initialSplit);
        if (preparedSplit.width !== undefined) syncLandscapeDetails();
        resizeDetailSplit(false, preparedSplit);
        const viewport = window.visualViewport;
        const bottom = (viewport?.offsetTop ?? 0) + (viewport?.height ?? innerHeight);
        if (elDetail.getBoundingClientRect().bottom <= bottom) revealInEmptySplit();
    }
    elDetail.innerHTML = html;
    const head = elDetail.querySelector('.tl-detail-head');
    if (elDetail.classList.contains('tl-detail-short')) positionCallContext(head);
    if (dense) updateDenseSelection(head, dense);
    const close = head && head.querySelector('.tl-close');
    if (close) {
        close.setAttribute('aria-label', 'Close details');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'tl-btn tl-detail-expand';
        button.dataset.detailExpand = '';
        button.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path class="tl-expand-icon" d="M6 2H2v4m0-4 5 5m3 7h4v-4m0 4-5-5"/><path class="tl-restore-icon" d="M2 6h4V2m0 4L1 1m13 9h-4v4m0-4 5 5"/></svg>';
        close.before(button);
        updateDetailExpand();
    }
    if (!measureBeforeMount) showDetailSplit(initialSplit);
    if (keepCsv) restoreContextCsvControls(keepCsv, keepCsvFocus);
    const landscapeChanged = syncLandscapeDetails();
    fillContent?.();
    if (detailHeadObserver) {
        if (observedDetailHead) detailHeadObserver.unobserve(observedDetailHead);
        observedDetailHead = head;
        observedSliceHead = head?.classList.contains('tl-slice-head') ? head : null;
        if (head) detailHeadObserver.observe(head, {box: 'border-box'});
    }
    sizeDetailHead();
    if (restoreSplit) setDetailExpanded(false, !!reading);
    else if (landscapeChanged || (!was && !measureBeforeMount)) resizeDetailSplit(false, preparedSplit);
    else if (was && (!replacingSlice || !observedSliceHead)) updateShortDetail();
    scheduleDetailScrollReset();
}
function hideDetail() {
    childListReturn = null;
    rangeJob = null;
    rangeCsvJob = null;
    cancelChildrenCsv();
    cancelDistCsv();
    cancelSlowFastCsv();
    cancelWindowCsv(false);
    cancelErrorCsv();
    cancelTaskCsv();
    resetWaitWork();
    clearDetailWork();
    cancelContextCsv();
    closeCallActions();
    clearCallDetailReturn();
    selKvm = null;
    if (!elDetail.classList.contains('show')) return;
    closedDetailWasScrolled = window.fgCurrentView() !== 'timeline' || detailIsScrolled();
    stopDetailDrag?.();
    detailSliceIndex = null;
    detailScrollResetPending = false;
    clearDetailCallSite();
    fgCancelSources(elDetail);
    if (observedDetailHead) detailHeadObserver?.unobserve(observedDetailHead);
    observedDetailHead = observedSliceHead = null;
    observedDetailHeadHeight = 0;
    elDetail.classList.remove('show');
    elDetail.replaceChildren();
    elResizer.classList.remove('show');
    detailExpanded = false;
    detailSplitScroll = null;
    tlRoot.classList.remove('tl-detail-expanded', 'tl-detail-reading');
    resize();
}
// Drag the divider to resize details; double-click resets the current split.
let stopDetailDrag = null;
function saveDetailSize(size, wide) {
    const available = wide ? availableDetailWidth() : window.innerHeight;
    if (available) saveDetailFraction(wide ? String(size / available) : (size / available).toFixed(4), wide);
}
elResizer.addEventListener('pointerdown', ev => {
    if (ev.button !== 0 || stopDetailDrag) return;
    ev.preventDefault();
    const pointer = ev.pointerId, wide = isLandscapeDetail();
    const start = wide ? ev.clientX : ev.clientY, startSize = wide ? elDetail.offsetWidth : elDetail.offsetHeight;
    elResizer.setPointerCapture(pointer);
    elResizer.classList.add('drag');
    document.body.classList.add('tl-resizing');
    document.body.classList.toggle('tl-resizing-width', wide);
    const move = event => {
        if (event.pointerId !== pointer) return;
        detailDragSize = startSize + start - (wide ? event.clientX : event.clientY);
        dividerPreviewPending = true;
        drawCursor();
    };
    const finish = event => {
        if (event.pointerId !== pointer) return;
        if (syncLandscapeDetails()) resize();
        else stopDetailDrag(true);
    };
    const endEvents = ['pointerup', 'pointercancel', 'lostpointercapture'];
    stopDetailDrag = commit => {
        stopDetailDrag = null;
        elResizer.removeEventListener('pointermove', move);
        for (const event of endEvents) elResizer.removeEventListener(event, finish);
        const pending = dividerPreviewPending;
        dividerPreviewPending = false;
        elResizer.classList.remove('drag');
        document.body.classList.remove('tl-resizing', 'tl-resizing-width');
        if (elResizer.hasPointerCapture(pointer)) elResizer.releasePointerCapture(pointer);
        if (commit && detailDragSize !== null) {
            if (pending) resize(true);
            saveDetailSize(Number(elResizer.getAttribute('aria-valuenow')), wide);
        }
        detailDragSize = null;
    };
    elResizer.addEventListener('pointermove', move);
    for (const event of endEvents) elResizer.addEventListener(event, finish);
});
elResizer.addEventListener('dblclick', () => { saveDetailFraction(null, isLandscapeDetail()); resize(); });
function resizeDetailKey(event) {
    if (event.target !== elResizer || !elDetail.classList.contains('show') || detailExpanded) return false;
    const wide = isLandscapeDetail(), current = wide ? elDetail.offsetWidth : elDetail.offsetHeight;
    let size = current;
    switch (event.key) {
        case 'ArrowUp': if (!wide) size += 16; break;
        case 'ArrowDown': if (!wide) size -= 16; break;
        case 'ArrowLeft': if (wide) size += 16; break;
        case 'ArrowRight': if (wide) size -= 16; break;
        case 'Home': size = -Infinity; break;
        case 'End': size = Infinity; break;
        case 'Enter':
            event.preventDefault();
            saveDetailFraction(null, wide);
            resize();
            return true;
        default: return false;
    }
    event.preventDefault();
    if (size === current) return true;
    if (wide) {
        const [minimum, maximum] = detailWidthLimits(availableDetailWidth());
        size = Math.max(minimum, Math.min(size, maximum));
    } else size = clampDetailH(size);
    if (size !== current) {
        saveDetailSize(size, wide);
        resize();
    }
    return true;
}

// Notes strip (hidden state remembered) and maximized layout (no page header / tabs).
function updateNotes(on) {
    on = on && elNotes.children.length > 0;
    elNotes.classList.toggle('hide', !on);
    elNotesButton.classList.toggle('on', on);
    elNotesButton.setAttribute('aria-pressed', String(on));
}
function toolbarOverlayPlacement(bounds) {
    const start = bounds.viewportTop + 8, end = Math.max(start, bounds.viewportBottom - 8);
    const belowTop = Math.max(start, bounds.belowTop), aboveBottom = Math.min(end, bounds.aboveBottom);
    const below = Math.max(0, end - belowTop), above = Math.max(0, aboveBottom - start);
    const preferred = Math.min(bounds.preferredHeight, end - start);
    const minimum = Math.min(preferred, bounds.minimumHeight);
    if (below >= preferred || (below >= above && below >= minimum)) return { top: belowTop, height: below };
    if (above >= minimum) return { top: aboveBottom - Math.min(preferred, above), height: above };
    return { top: start, height: end - start };
}
function fitToolbarOverlay(element, opener) {
    const scrollTop = element.scrollTop;
    element.style.removeProperty('--tl-overlay-top');
    element.style.removeProperty('--tl-overlay-room');
    const parent = element.offsetParent;
    if (!parent) return;
    const viewport = window.visualViewport, viewportTop = viewport?.offsetTop ?? 0;
    const style = getComputedStyle(element), opening = opener.getBoundingClientRect();
    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) +
        parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const placement = toolbarOverlayPlacement({
        viewportTop, viewportBottom: viewportTop + (viewport?.height ?? window.innerHeight),
        belowTop: elToolbar.getBoundingClientRect().bottom, aboveBottom: opening.top - 6,
        preferredHeight: element.getBoundingClientRect().height, minimumHeight: padding + opening.height,
    });
    element.style.setProperty('--tl-overlay-top', placement.top - parent.getBoundingClientRect().top - parent.clientTop + 'px');
    element.style.setProperty('--tl-overlay-room', placement.height + 'px');
    element.scrollTop = scrollTop;
}
function fitToolbarOverlays() {
    if (elOptions.classList.contains('open')) fitToolbarOverlay(elOptions, elMore);
    if (compactToolbar.matches && !elNotes.classList.contains('hide')) fitToolbarOverlay(elNotes, elNotesButton);
}
let toolbarOverlayFrame = 0;
function scheduleToolbarOverlays() {
    if (toolbarOverlayFrame || window.fgCurrentView() !== 'timeline') return;
    if (!elOptions.classList.contains('open') && (!compactToolbar.matches || elNotes.classList.contains('hide'))) return;
    toolbarOverlayFrame = requestAnimationFrame(() => {
        toolbarOverlayFrame = 0;
        if (window.fgCurrentView() === 'timeline') fitToolbarOverlays();
    });
}
function setNotes(on) {
    updateNotes(on);
    prefSet(compactToolbar.matches ? 'notesCompact' : 'notes', on ? '1' : '0');
    if (!compactToolbar.matches) resize();
    else fitToolbarOverlays();
}
function focusToolbarOptions() {
    const menu = elOptions.getBoundingClientRect();
    const top = menu.top + elOptions.clientTop, bottom = top + elOptions.clientHeight;
    const closed = Array.from(elOptions.querySelectorAll('details:not([open])'));
    let candidate = null;
    for (const control of elOptions.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, a[href]')) {
        if (control.tabIndex < 0 || control.closest('[inert]') || getComputedStyle(control).visibility !== 'visible') continue;
        if (closed.some(details => details.contains(control) && !details.querySelector(':scope > summary')?.contains(control))) continue;
        const bounds = control.getBoundingClientRect();
        if (!bounds.width || !bounds.height || bounds.bottom <= top || bounds.top >= bottom) continue;
        candidate ||= control;
        if (bounds.top >= top && bounds.bottom <= bottom) {
            candidate = control;
            break;
        }
    }
    focusOverlayControl(elOptions, candidate || elOptions);
}
function setToolbarOptions(on, restoreFocus = false) {
    const open = !!on;
    if (open) closeCallActions();
    if (open && compactToolbar.matches && !elNotes.classList.contains('hide')) setNotes(false);
    elOptions.classList.toggle('open', open);
    elMore.setAttribute('aria-expanded', String(open));
    if (open) fitToolbarOverlays();
    if (open) focusToolbarOptions();
    if (restoreFocus) elMore.focus({ preventScroll: true });
}
const fullscreenButton = tlRoot.querySelector('[data-act=max]');
const fullscreenToolbar = fullscreenButton.parentElement;
const fullscreenHelp = fullscreenToolbar.querySelector('[data-act=help]');
const quickActions = fullscreenToolbar.querySelector('.tl-quick');
const zoomGroup = quickActions.querySelector('.tl-zoom-group');
const zoomTools = elOptions.querySelector('.tl-zoom-tools');
const jumpTools = elOptions.querySelector('.tl-jump-tools');
const fullscreenSearch = elSearch.closest('.tl-search-group');
const inlineButton = quickActions.querySelector('[data-act=inl]');
const fullscreenJumpControls = [fullscreenSearch, inlineButton, elNotesButton, fullscreenHelp];
function fullscreenTraceJump() {
    return compactToolbar.matches && tlRoot.classList.contains('tl-fullscreen') &&
        !!fullscreenToolbar.querySelector(':scope > .fg-jump-return:not([hidden])');
}
function fullscreenChartRoom(element) {
    const top = element.getBoundingClientRect().top + tlRoot.scrollTop;
    const bottom = parseFloat(getComputedStyle(tlRoot).paddingBottom);
    return Math.floor(innerHeight - bottom - top);
}
function inlineFullscreenToolbarFits() {
    return fullscreenChartRoom(elScroll) >= HDR + 2 * ROW + 8;
}
function placeFullscreenJumpControls(inTools) {
    if (inTools) jumpTools.append(...fullscreenJumpControls);
    else {
        fullscreenToolbar.insertBefore(fullscreenSearch, fullscreenToolbar.querySelector(':scope > :not(.fg-jump-return)'));
        quickActions.prepend(inlineButton, elNotesButton);
        fullscreenButton.after(fullscreenHelp);
    }
    jumpTools.hidden = !inTools;
}
function placeToolbarZoom(inTools) {
    if (inTools === zoomTools.contains(zoomGroup)) return;
    if (inTools) zoomTools.append(zoomGroup);
    else quickActions.prepend(zoomGroup);
    zoomTools.hidden = !inTools;
}
function syncToolbar() {
    const compact = compactToolbar.matches;
    const hadJumpInTools = !jumpTools.hidden, hadZoomInTools = zoomTools.contains(zoomGroup);
    const active = document.activeElement;
    const focusInOptions = elOptions.contains(active);
    tlRoot.classList.toggle('tl-compact', compact);
    const following = compact ? fullscreenToolbar.querySelector(':scope > .fg-jump-status') : fullscreenButton;
    const moved = quickActions.nextElementSibling !== following;
    if (moved) fullscreenToolbar.insertBefore(quickActions, following);
    const fullscreenJump = fullscreenTraceJump();
    const narrow = shortNarrowToolbar.matches;
    let jumpInTools = fullscreenJump && detailExpanded && hadJumpInTools;
    if (fullscreenJump && !detailExpanded) {
        if (hadJumpInTools) placeFullscreenJumpControls(false);
        placeToolbarZoom(narrow);
        jumpInTools = !inlineFullscreenToolbarFits();
    }
    if (jumpInTools === jumpTools.hidden) placeFullscreenJumpControls(jumpInTools);
    const zoomInTools = narrow || jumpInTools;
    placeToolbarZoom(zoomInTools);
    if (moved || hadJumpInTools !== jumpInTools || hadZoomInTools !== zoomInTools) {
        const focusMovedToTools = elOptions.contains(active);
        setToolbarOptions(false, focusMovedToTools);
        if (!focusMovedToTools && (focusInOptions || quickActions.contains(active)) && !active.closest('[hidden]'))
            active.focus({ preventScroll: true });
    } else if (focusInOptions && elOptions.classList.contains('open') && document.activeElement !== active) {
        active.focus({ preventScroll: true });
    }
    updateNotes(compact ? prefGet('notesCompact') === '1' : prefGet('notes') !== '0');
}
function resizeAfterToolbarLayout() {
    resize();
    if (!fullscreenTraceJump() || detailExpanded || tlRoot.scrollTop || sel < 0 || TRACKS[sTask[sel]]?.hidden) return;
    revealRow(sel, { expand: false, nearest: true });
}
function dismissToolbarOptions(event) {
    if (elOptions.classList.contains('open') && !elOptions.contains(event.target) && !elMore.contains(event.target)) {
        setToolbarOptions(false);
    }
}
document.addEventListener('pointerdown', dismissToolbarOptions);
document.addEventListener('focusin', dismissToolbarOptions);
compactToolbar.addEventListener('change', () => {
    syncToolbar();
    if (AN.kind === 'tasks') positionTaskPager();
    if (window.fgCurrentView() === 'timeline') resizeAfterToolbarLayout();
});
shortNarrowToolbar.addEventListener('change', syncToolbar);
let fullscreenPending = false, nativeFullscreen = false;
function setMax(on, restoreFocus = false) {
    if (!on) setToolbarOptions(false);
    document.body.classList.toggle('tl-max', on);
    tlRoot.classList.toggle('tl-fullscreen', on);
    window.fgPageChrome().top.forEach(e => e.classList.toggle('fg-off', on));
    window.fgPlaceTraceReturn(on ? fullscreenToolbar : null);
    fullscreenButton.setAttribute('aria-pressed', String(on));
    const label = on ? 'Exit full screen' : 'Full screen';
    fullscreenButton.querySelector('.tl-toolbar-long').textContent = label;
    fullscreenButton.querySelector('.tl-toolbar-short').textContent = on ? 'Exit' : 'Full';
    fullscreenButton.setAttribute('aria-label', label);
    fullscreenButton.title = on ? 'Exit full screen (M / Escape)' : 'Enter full screen (M); Escape to exit';
    fullscreenHelp.hidden = !on;
    syncToolbar();
    resizeAfterToolbarLayout();
    sizeDetailHead();
    if (restoreFocus && window.fgCurrentView() === 'timeline') fullscreenButton.focus({ preventScroll: true });
}
function leaveFullscreen(restoreFocus = true) {
    setMax(false, restoreFocus);
    if (document.fullscreenElement === tlRoot) {
        document.exitFullscreen().catch(() => {
            if (document.fullscreenElement === tlRoot && window.fgCurrentView() === 'timeline') {
                setMax(true);
                toast('Use Escape or the browser controls to exit full screen.');
            }
        });
    }
}
function toggleFullscreen() {
    setToolbarOptions(false);
    if (tlRoot.classList.contains('tl-fullscreen')) { leaveFullscreen(); return; }
    enterFullscreen();
}
async function enterFullscreen({ browser = true, lockExit = true } = {}) {
    if (fullscreenPending) return;
    setMax(true);
    if (!browser) return;
    if (!tlRoot.requestFullscreen || document.fullscreenEnabled === false) {
        toast('Using page full screen; browser full screen is unavailable.');
        return;
    }
    fullscreenPending = true;
    fullscreenButton.disabled = lockExit;
    try {
        await tlRoot.requestFullscreen();
    } catch (_) {
        if (tlRoot.classList.contains('tl-fullscreen') && window.fgCurrentView() === 'timeline')
            toast('Using page full screen; browser full screen was not permitted.');
    } finally {
        fullscreenPending = false;
        fullscreenButton.disabled = false;
        if (!tlRoot.classList.contains('tl-fullscreen') || window.fgCurrentView() !== 'timeline') {
            leaveFullscreen(false);
        }
        restoreTextFocus();
    }
}
function traceJumpFullscreen() {
    if (!fullscreenPending && tlRoot.classList.contains('tl-fullscreen'))
        return { browser: document.fullscreenElement === tlRoot, top: tlRoot.scrollTop };
    return null;
}
function restoreTraceJumpFullscreen(state) {
    if (fullscreenPending || nativeFullscreen || document.fullscreenElement) return null;
    enterFullscreen({ browser: state.browser, lockExit: false });
    tlRoot.scrollTop = state.top;
    return fullscreenButton;
}
document.addEventListener('fullscreenchange', () => {
    if (document.fullscreenElement === tlRoot) {
        nativeFullscreen = true;
        if (window.fgCurrentView() !== 'timeline' || !tlRoot.classList.contains('tl-fullscreen')) leaveFullscreen(false);
        else { syncToolbar(); resizeAfterToolbarLayout(); sizeDetailHead(); }
    } else if (nativeFullscreen) {
        nativeFullscreen = false;
        setMax(false, true);
    }
    restoreTextFocus();
});
elDetail.addEventListener('click', ev => {
    const sourceMode = ev.target.closest('[data-fg-source-mode]');
    if (sourceMode && detailCallSite?.box.contains(sourceMode)) {
        ev.preventDefault(); ev.stopPropagation(); fgToggleSrcMode();
        return;
    }
    const denseZoom = ev.target.closest('[data-dense-start]');
    if (denseZoom) { zoomIntoDensePixel(denseZoom); return; }
    const copyPrototype = ev.target.closest('[data-copy-prototype]');
    if (copyPrototype) { copyBtfPrototype(copyPrototype); return; }
    const summary = ev.target.closest(':is([data-btf-prototype], .tl-enum, .tl-value-evidence) > summary');
    if (summary && closeReadingDisclosure(summary)) {
        ev.preventDefault();
        return;
    }
    if (summary && !summary.parentElement.open) {
        ev.preventDefault();
        if (layoutViewportWidth !== innerWidth || layoutViewportHeight !== innerHeight) resize();
        const disclosure = summary.parentElement;
        disclosure.open = true;
        mountBtfDisclosure(disclosure);
        revealDisclosureStart(summary);
        return;
    }
    const symbolTitle = ev.target.closest('[data-call-symbol]');
    if (symbolTitle) {
        const panel = elDetail.querySelector('.tl-recorded-symbol');
        if (panel) hideRecordedSymbol(panel, symbolTitle);
        else showRecordedSymbol(sel, symbolTitle);
        return;
    }
    const copySymbol = ev.target.closest('[data-copy-symbol]');
    if (copySymbol) { copyRecordedSymbol(copySymbol); return; }
    const hideSymbol = ev.target.closest('[data-hide-symbol]');
    if (hideSymbol) { hideRecordedSymbol(hideSymbol.closest('.tl-recorded-symbol')); return; }
    const detailReturn = ev.target.closest('[data-detail-return]');
    if (detailReturn && detailReturn.parentElement === callDetailReturn?.frame) { restoreCallDetail(); return; }
    if (ev.target.closest('[data-child-list-return]')) { restoreChildList(); return; }
    const enumPreview = ev.target.closest('[data-enum-show-all]');
    if (enumPreview) {
        const filter = toggleAllEnumNames(enumPreview);
        if (filter) {
            revealDetailBelow(enumPreview, filter.getBoundingClientRect().bottom, { priority: filter });
            if (ev.detail === 0 && document.activeElement === enumPreview) filter.focus({preventScroll: true});
        }
        return;
    }
    const enumLocate = ev.target.closest('[data-enum-locate]');
    if (enumLocate) { locateRecordedEnumName(enumLocate); return; }
    const enumPage = ev.target.closest('[data-enum-page]');
    if (enumPage) { changeEnumNamePage(enumPage); return; }
    const enumCopy = ev.target.closest('[data-enum-copy]');
    if (enumCopy) { copyEnumNames(enumCopy); return; }
    const enumClear = ev.target.closest('[data-enum-clear]');
    if (enumClear) {
        if (!enumClear.disabled) clearEnumNameFilter(enumClear.parentElement.querySelector('[data-enum-filter]'));
        return;
    }
    const actions = ev.target.closest('[data-call-actions]');
    if (actions) {
        if (elCallActions.hidden) openCallActions(actions);
        else closeCallActions(true);
        return;
    }
    if (ev.target.closest('[data-detail-expand]')) {
        if (callDetailReturn?.expandedForJump) clearCallDetailReturn();
        setDetailExpanded(!detailExpanded, true);
        return;
    }
    const processPage = ev.target.closest('[data-process-page]');
    if (processPage && AN.kind === 'process') {
        processView.page += +processPage.dataset.processPage;
        renderProcessEvents(); return;
    }
    const processTime = ev.target.closest('[data-process-time]');
    if (processTime && AN.kind === 'process') { focusProcessEvent(+processTime.dataset.processTime); return; }
    if (ev.target.closest('[data-process-export]') && AN.kind === 'process') { exportProcessEvents(); return; }
    const navigation = ev.target.closest('[data-call-nav]');
    if (navigation) { navigateCall(navigation.dataset.callNav, true); return; }
    const children = ev.target.closest('[data-child-open]');
    if (children) {
        if (children.getAttribute('aria-disabled') !== 'true') showChildren(+children.dataset.childOpen);
        return;
    }
    const stackToggle = ev.target.closest('[data-stack-toggle]');
    if (stackToggle) { toggleDetailStack(stackToggle); return; }
    const contextPage = ev.target.closest('[data-context-page]');
    if (contextPage) { changeDetailContextPage(contextPage); return; }
    const contextExport = ev.target.closest('[data-context-export]');
    if (contextExport) { exportDetailCsv(contextExport); return; }
    const dl = ev.target.closest('[data-dist]');
    if (dl) { showDist(+dl.dataset.dist); return; }
    const lineLink = ev.target.closest('[data-line]');
    if (lineLink) { gotoText(+lineLink.dataset.line, lineLink); return; }
    const sliceLink = ev.target.closest('[data-slice]');
    if (sliceLink) {
        const restoreFocus = sliceLink.matches('.tl-detail-link:focus');
        select(+sliceLink.dataset.slice, { reveal: true });
        if (restoreFocus) elDetail.querySelector('.tl-close')?.focus({ preventScroll: true });
        return;
    }
    if (ev.target.closest('.tl-close')) {
        range = null;
        select(-1);
        focusTimelineControl();
        return;
    }
    if (ev.target.closest('[data-act=range-csv]')) { exportRangeCsv(); return; }
    const rangePage = ev.target.closest('[data-range-page]');
    if (rangePage) { changeRangePage(+rangePage.dataset.rangePage); return; }
    const header = ev.target.closest('.tl-range-table th[data-sort]');
    if (header) {
        requestRangeOrder(header);
        return;
    }
    const tr = ev.target.closest('tr[data-name]');
    if (tr) { elSearch.value = '^' + tr.dataset.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$'; applySearch(elSearch.value); }
});

// ---- range summary ----
elDetail.addEventListener('input', event => {
    if (event.target.matches('[data-act=range-page]')) event.target.setCustomValidity('');
});
elDetail.addEventListener('submit', event => {
    if (!event.target.matches('[data-range-jump]')) return;
    event.preventDefault();
    changeRangePage(0, event.target.elements.page);
});
let sumSort = 'self';
let sumCache = null;
const rangeTableSummaries = new WeakMap();
const SUM_PAGE = 100;
const RANGE_DEFER_CALLS = 32768;
function rangeSummaryHits() {
    return filteredCallCount === N ? null : textHits;
}
function cachedRangeSummary(selection) {
    return sumCache && sumCache.start === selection.s && sumCache.end === selection.e && sumCache.track === selection.track &&
        sumCache.hits === rangeSummaryHits() && sumCache.durations === sDur ? sumCache : null;
}
function rangeTracks(selection) {
    return selection.track !== null ? [TRACKS[selection.track]] : ORDER;
}
function* rangeCallBlocks(track, startTime, endTime) {
    const bounds = track.rangeBounds;
    for (let offset = 0, start = track.lo; start >= 0 && start < track.hi; offset += 2, start += RANGE_BLOCK_SIZE) {
        if (!bounds || (bounds[offset] < endTime && bounds[offset + 1] >= startTime))
            yield [start, Math.min(start + RANGE_BLOCK_SIZE, track.hi)];
    }
}
const RANGE_COUNTING_CALLS = 'Summarizing calls…';
const RANGE_COUNTING_WAIT = 'Summarizing off-CPU time…';
function rangePreparationStatus(selection) {
    let candidates = 0;
    for (const track of rangeTracks(selection)) {
        for (const [start, stop] of rangeCallBlocks(track, selection.s, selection.e)) {
            candidates += stop - start;
            if (candidates >= RANGE_DEFER_CALLS) return RANGE_COUNTING_CALLS;
        }
        for (const [start, stop] of offIntervalBlocks(track, selection.s, selection.e)) {
            candidates += stop - start;
            if (candidates >= RANGE_DEFER_CALLS) return RANGE_COUNTING_WAIT;
        }
    }
    return null;
}
function* rangeSummarySteps(selection, hits, durations, selfTimes) {
    const rs = selection.s, re = selection.e;
    const agg = new Map();
    const rows = [];
    const ctxSelf = [0, 0, 0, 0, 0];
    let offT = 0, nCalls = 0, prepared = 0;
    for (const track of rangeTracks(selection)) {
        for (const [start, stop] of rangeCallBlocks(track, rs, re)) {
            for (let index = start; index < stop; index++) {
                const begin = sStart[index], end = sEnd[index];
                if (begin >= re || (begin === end ? begin < rs : end <= rs)) continue;
                if (hits && !hits[index]) continue;
                const duration = end - begin, overlap = Math.min(end, re) - Math.max(begin, rs);
                const fraction = duration > 0 ? overlap / duration : 0;
                const selfPart = selfTimes[index] * fraction;
                const name = sName[index], callDuration = durations[index];
                let stats = agg.get(name);
                if (!stats) {
                    stats = { calls: 0, incl: 0, self: 0, max: 0, err: 0, ex: index };
                    agg.set(name, stats);
                    rows.push([name, stats]);
                }
                if (begin >= rs) { stats.calls++; nCalls++; }
                stats.incl += callDuration * fraction;
                stats.self += selfPart;
                if (callDuration > stats.max) { stats.max = callDuration; stats.ex = index; }
                if (eRole[index] === 0) stats.err++;
                ctxSelf[sFlags[index] & FL.USER ? 4 : sFlags[index] & FL.CTX] += selfPart;
            }
            prepared += stop - start;
            if (prepared >= PREPARE_BATCH) { prepared = 0; yield RANGE_COUNTING_CALLS; }
        }
        for (const [start, stop] of offIntervalBlocks(track, rs, re)) {
            for (let index = start; index < stop; index++) {
                const record = track.off[index];
                if (record[2] > rs && record[1] < re) offT += Math.min(record[2], re) - Math.max(record[1], rs);
            }
            prepared += stop - start;
            if (prepared >= PREPARE_BATCH) { prepared = 0; yield RANGE_COUNTING_WAIT; }
        }
    }
    let maxv = 0;
    for (let index = 0; index < rows.length; index++) {
        if (index && index % PREPARE_BATCH === 0) yield RANGE_COUNTING_CALLS;
        maxv = Math.max(maxv, rows[index][1].self);
    }
    return { start: rs, end: re, track: selection.track, hits, durations,
        rows, ctxSelf, offT, nCalls, maxv: maxv || 1, sort: null, page: 0 };
}
function rangeSummary() {
    const cached = cachedRangeSummary(range);
    if (cached) return cached;
    const steps = rangeSummarySteps(range, rangeSummaryHits(), sDur, sSelf);
    let result = steps.next();
    while (!result.done) result = steps.next();
    return (sumCache = result.value);
}
function rangeComparator(sort) {
    return sort === 'name' ? (left, right) => S[left[0]] < S[right[0]] ? -1 : S[left[0]] > S[right[0]] ? 1 : 0 :
        (left, right) => right[1][sort] - left[1][sort];
}
function sortedRangeSummary() {
    const summary = rangeSummary();
    if (summary.sort !== sumSort) {
        summary.rows.sort(rangeComparator(sumSort));
        summary.sort = sumSort;
        summary.page = 0;
    }
    return summary;
}
function rangeRowsHtml(summary, start, end) {
    if (!summary.rows.length) return '<tr><td colspan="6">' + rangeEmptyText() + '</td></tr>';
    let html = '';
    for (let index = start; index < end; index++) {
        const [name, stats] = summary.rows[index];
        html += '<tr data-name="' + esc(S[name]) + '"><td><span class="bar" style="width:' + (60 * stats.self / summary.maxv).toFixed(1) + 'px"></span>' +
            (MIXED ? '<span class="tl-stack-space">' + (spaceOf(stats.ex) === 'user' ? 'U' : 'K') + '</span>' : '') +
            '<button type="button" class="tl-link tl-detail-link" title="Find this function in Timeline">' + esc(S[name]) + '</button></td>' +
            '<td>' + stats.calls + '</td><td>' + fmtDur(stats.self) + '</td><td>' + fmtDur(stats.incl) + '</td><td>' + fmtDur(stats.max) + '</td>' +
            '<td class="' + (stats.err ? 'err' : '') + '">' + (stats.err || '') + '</td></tr>';
    }
    return html;
}
function updateRangeControls(summary) {
    const start = summary.page * SUM_PAGE, end = Math.min(start + SUM_PAGE, summary.rows.length);
    const status = elDetail.querySelector('.tl-range-footer [role=status]');
    const label = 'Showing ' + (summary.rows.length ? start + 1 + '–' + end : '0') + ' of ' + summary.rows.length + ' functions';
    if (status.textContent !== label) status.textContent = label;
    const input = elDetail.querySelector('[data-act=range-page]');
    if (!input) return;
    input.setCustomValidity('');
    input.value = summary.page + 1;
    elDetail.querySelector('#tl-range-page-total').textContent = 'of ' + Math.ceil(summary.rows.length / SUM_PAGE);
    const previous = elDetail.querySelector('[data-act=range-prev]'), next = elDetail.querySelector('[data-act=range-next]');
    const focused = document.activeElement;
    previous.disabled = summary.page === 0;
    next.disabled = end >= summary.rows.length;
    if (focused === next && next.disabled && !previous.disabled) previous.focus({ preventScroll: true });
    else if (focused === previous && previous.disabled && !next.disabled) next.focus({ preventScroll: true });
}
function replaceRangeRows(table, summary) {
    const start = summary.page * SUM_PAGE;
    const keepRightEdge = summaryPageAtRightEdge();
    table.tBodies[0].innerHTML = rangeRowsHtml(summary, start, Math.min(start + SUM_PAGE, summary.rows.length));
    updateRangeControls(summary);
    revealSummaryPage(table, keepRightEdge);
}
function changeRangePage(direction, input = null) {
    if (!range || AN.kind !== 'range' || rangeJob?.table) return;
    const summary = sortedRangeSummary(), last = Math.max(0, Math.ceil(summary.rows.length / SUM_PAGE) - 1);
    const entered = input?.value.trim();
    const page = input ? Number(entered) - 1 : summary.page + direction;
    if (input && (!/^\d+$/.test(entered) || page < 0 || page > last)) {
        input.setCustomValidity('Enter a whole page number from 1 to ' + (last + 1) + '.');
        input.reportValidity();
        return;
    }
    const target = Math.max(0, Math.min(last, page));
    if (target === summary.page) { updateRangeControls(summary); return; }
    summary.page = target;
    replaceRangeRows(elDetail.querySelector('.tl-range-table'), summary);
}
function rangeSortDirection(column, sort) {
    return column === sort ? (column === 'name' ? 'ascending' : 'descending') : 'none';
}
function setRangeOrdering(table, summary, label = '') {
    const busy = !!label;
    const status = elDetail.querySelector('.tl-range-work-status');
    status.hidden = !busy;
    status.textContent = label;
    if (busy) table.setAttribute('aria-busy', 'true');
    else table.removeAttribute('aria-busy');
    elDetail.querySelector('[data-act=range-csv]').disabled = busy || !summary.rows.length;
    for (const control of elDetail.querySelectorAll('.tl-range-jump :is(input, button)')) control.disabled = busy;
    if (busy) {
        for (const button of elDetail.querySelectorAll('[data-range-page]')) button.disabled = true;
    }
}
function setRangeSortHeaders(table, sort) {
    let activeHeader = null;
    for (const header of table.querySelectorAll('th[data-sort]')) {
        const direction = rangeSortDirection(header.dataset.sort, sort);
        if (header.getAttribute('aria-sort') !== direction) header.setAttribute('aria-sort', direction);
        if (direction !== 'none') activeHeader = header;
    }
    return activeHeader;
}
function paintRangeOrder(table, summary) {
    const restoreFocus = table.tBodies[0].contains(document.activeElement);
    const activeHeader = setRangeSortHeaders(table, summary.sort);
    setRangeOrdering(table, summary);
    replaceRangeRows(table, summary);
    rangeTableSummaries.set(table, summary);
    if (restoreFocus) activeHeader?.firstElementChild.focus({ preventScroll: true });
}
function* prepareRangeOrder(summary, sort) {
    const rows = new Array(summary.rows.length);
    for (let start = 0; start < rows.length; start += PREPARE_BATCH) {
        const stop = Math.min(start + PREPARE_BATCH, rows.length);
        for (let index = start; index < stop; index++) rows[index] = summary.rows[index];
        yield;
    }
    yield* sortTimelineRow(rows, rangeComparator(sort));
    return { ...summary, rows, sort, page: 0 };
}
function requestRangeOrder(header) {
    if (!range || AN.kind !== 'range') return;
    const sort = header.dataset.sort, table = header.closest('.tl-range-table');
    const button = header.querySelector('button');
    button.focus({ preventScroll: true });
    if (rangeJob?.table === table && rangeJob.sort === sort) return;
    sumSort = sort;
    const summary = cachedRangeSummary(range);
    if (!summary) { requestRangeSummary(); return; }
    rangeJob = null;
    if (summary.sort === sort && rangeCsvJob) return;
    cancelRangeCsv();
    if (summary.sort === sort) { setRangeOrdering(table, summary); updateRangeControls(summary); return; }
    if (summary.rows.length < RANGE_DEFER_CALLS) { paintRangeOrder(table, sortedRangeSummary()); return; }
    setRangeOrdering(table, summary, 'Ordering by ' + button.textContent + '…');
    rangeJob = { table, selection: range, hits: summary.hits, durations: sDur, sort, scheduled: false,
        steps: prepareRangeOrder(summary, sort) };
    queueRangeSummary(true);
}
function timelineCsvCell(value) {
    let text = String(value);
    if (typeof value === 'string' && (/^[\s]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text))) text = "'" + text;
    return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}
function saveTimelineCsv(filename, parts) {
    const url = URL.createObjectURL(new Blob(parts, { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.hidden = true;
    tlRoot.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function downloadTimelineCsv(filename, columns, rows) {
    const lines = [columns.join(',')];
    for (const row of rows) lines.push(row.map(timelineCsvCell).join(','));
    saveTimelineCsv(filename, ['\ufeff', lines.join('\r\n'), '\r\n']);
}
function cancelRangeCsv() {
    const job = rangeCsvJob;
    if (!job) return;
    rangeCsvJob = null;
    job.button.textContent = 'Export CSV';
    job.status.hidden = true;
    job.status.textContent = '';
}
function queueRangeCsv(afterPaint = false) {
    const job = rangeCsvJob;
    if (job) queueTimelineCsv(job, () => rangeCsvJob === job, cancelRangeCsv, afterPaint);
}
function timelineCsvProgressText(progress) {
    return 'Preparing CSV…' + (progress ? ' ' + progress + '%' : '');
}
function queueTimelineCsv(job, current, finish, afterPaint = false) {
    if (job.scheduled || window.fgCurrentView() !== 'timeline') return;
    job.scheduled = true;
    const advance = () => {
        job.scheduled = false;
        if (!current() || window.fgCurrentView() !== 'timeline') return;
        const deadline = performance.now() + 6;
        try {
            let done = false;
            do {
                const lines = [];
                while (lines.length < job.batchRows) {
                    const next = job.rows.next();
                    if (next.done) { done = true; break; }
                    if (next.value === undefined) break;
                    lines.push(next.value.map(timelineCsvCell).join(','));
                }
                if (lines.length) {
                    job.written += lines.length;
                    job.parts.push(new Blob([lines.join('\r\n'), '\r\n']));
                }
            } while (!done && performance.now() < deadline);
            if (done) {
                finish();
                saveTimelineCsv(job.filename, job.parts);
                return;
            }
            if (job.total === null) {
                if (job.written >= Math.max(512, job.progress * 2)) {
                    job.progress = job.written;
                    job.status.textContent = timelineCsvProgressText(0) + ' ' + job.written + ' rows';
                }
            } else {
                const progress = Math.min(75, 25 * Math.floor(job.written / job.total * 4));
                if (progress > job.progress) {
                    job.progress = progress;
                    job.status.textContent = timelineCsvProgressText(progress);
                }
            }
            queueTimelineCsv(job, current, finish);
        } catch (error) {
            finish();
            toast('Could not export CSV: ' + error.message);
        }
    };
    if (afterPaint) requestAnimationFrame(() => setTimeout(advance, 0));
    else postRangeWork(advance);
}
function exportRangeCsv() {
    if (rangeCsvJob) { cancelRangeCsv(); return; }
    if (!range || AN.kind !== 'range' || rangeJob?.table) return;
    const summary = sortedRangeSummary();
    if (!summary.rows.length) return;
    const unit = MODE === 'count' ? 'units' : 'ns', scale = MODE === 'count' ? 1000 : 1;
    const basis = MODE === 'count' ? 'call_order' : adjOn ? 'overhead_subtracted_estimate' : 'measured';
    const scope = range.track !== null ? trackLabel(TRACKS[range.track]) : 'all tracks';
    const { s: start, e: end } = range;
    const filter = (window.fgFilterState || {}).text || '';
    const columns = ['function', 'calls', 'self_estimate_' + unit, 'inclusive_estimate_' + unit, 'max_call_' + unit,
        'error_origins_overlapping', 'range_start_' + unit, 'range_end_' + unit, 'scope', 'scope_off_cpu_' + unit,
        'time_mode', 'duration_basis', 'filter'];
    if (D.uftrace) columns.push('space');
    function* rows() {
        for (const [name, stats] of summary.rows) yield [S[name], stats.calls, stats.self / scale, stats.incl / scale, stats.max / scale, stats.err,
            start / scale, end / scale, scope, summary.offT / scale, MODE, basis, filter,
            ...(D.uftrace ? [spaceOf(stats.ex)] : [])];
    }
    const filename = 'funcgraph-range-' + (MODE === 'count' ? 'count' : adjOn ? 'adjusted' : 'raw') + '.csv';
    if (summary.rows.length < RANGE_DEFER_CALLS) { downloadTimelineCsv(filename, columns, rows()); return; }
    const button = elDetail.querySelector('[data-act=range-csv]'), status = elDetail.querySelector('.tl-range-work-status');
    button.textContent = 'Cancel CSV';
    status.hidden = false;
    status.textContent = 'Preparing CSV…';
    rangeCsvJob = { button, status, filename, rows: rows(), total: summary.rows.length, batchRows: 512, written: 0, progress: 0,
        parts: ['\ufeff' + columns.join(',') + '\r\n'], scheduled: false };
    queueRangeCsv(true);
}
function rangeHeaderHtml(className) {
    return '<div class="tl-detail-head ' + className + '"><span class="tl-detail-title">Range ' + fmtDur(range.e - range.s) + '</span>' +
        pill('mute', range.track !== null ? trackLabel(TRACKS[range.track]) : 'all tracks');
}
function* prepareRangeSummary(selection, hits, durations, selfTimes, sort) {
    const summary = yield* rangeSummarySteps(selection, hits, durations, selfTimes);
    yield 'Ordering functions…';
    yield* sortTimelineRow(summary.rows, rangeComparator(sort), 'Ordering functions…');
    summary.sort = sort;
    return summary;
}
let rangeWorkChannel = null;
const rangeWorkQueue = [];
function postRangeWork(callback) {
    if (typeof MessageChannel !== 'function') { setTimeout(callback, 0); return; }
    if (!rangeWorkChannel) {
        rangeWorkChannel = new MessageChannel();
        rangeWorkChannel.port1.onmessage = () => rangeWorkQueue.shift()?.();
    }
    rangeWorkQueue.push(callback);
    rangeWorkChannel.port2.postMessage(null);
}
function queueRangeSummary(afterPaint = false) {
    const job = rangeJob;
    if (!job || job.scheduled || window.fgCurrentView() !== 'timeline') return;
    job.scheduled = true;
    const advance = () => {
        job.scheduled = false;
        if (rangeJob !== job || window.fgCurrentView() !== 'timeline') return;
        if (!range) { hideDetail(); return; }
        if (range !== job.selection) return;
        if (rangeSummaryHits() !== job.hits || sDur !== job.durations || sumSort !== job.sort) { requestRangeSummary(); return; }
        const deadline = performance.now() + 6;
        let result;
        do { result = job.steps.next(); } while (!result.done && performance.now() < deadline);
        if (!result.done) {
            if (job.status && job.status.textContent !== result.value) job.status.textContent = result.value;
            queueRangeSummary();
            return;
        }
        sumCache = result.value;
        if (job.table) {
            rangeJob = null;
            paintRangeOrder(job.table, sumCache);
            return;
        }
        const focused = elDetail.contains(document.activeElement) ? document.activeElement : null;
        showRangeSummary();
        if (focused) elDetail.querySelector(focused.matches('[data-detail-expand]') ? '[data-detail-expand]' : '.tl-close')?.focus({preventScroll:true});
    };
    if (afterPaint) requestAnimationFrame(() => setTimeout(advance, 0));
    else postRangeWork(advance);
}
function requestRangeSummary(table = null) {
    if (!range) return;
    const hits = rangeSummaryHits();
    if (rangeJob?.selection === range && rangeJob.hits === hits && rangeJob.durations === sDur && rangeJob.sort === sumSort) {
        queueRangeSummary();
        return;
    }
    if (cachedRangeSummary(range)) { showRangeSummary(table); return; }
    const status = rangePreparationStatus(range);
    if (!status) { showRangeSummary(table); return; }
    sumCache = null;
    AN.kind = 'range';
    setSelection(-1);
    openDetail(rangeHeaderHtml('tl-range-pending-head') + '<button class="tl-close" title="Close (Esc)">×</button></div>' +
        '<p class="tl-range-pending an-hint" role="status">' + status + '</p>');
    rangeJob = { selection: range, hits, durations: sDur, sort: sumSort, scheduled: false,
        status: elDetail.querySelector('.tl-range-pending'), steps: prepareRangeSummary(range, hits, sDur, sSelf, sumSort) };
    draw();
    queueRangeSummary(true);
}
function refreshRangeFilter() {
    const summary = cachedRangeSummary(range), table = elDetail.querySelector('.tl-range-table');
    const shown = rangeTableSummaries.get(table);
    if (!summary || shown !== summary) {
        const sameScope = shown && shown.start === range.s && shown.end === range.e && shown.track === range.track;
        requestRangeSummary(sameScope ? table : null);
        return;
    }
    cancelRangeCsv();
    elDetail.querySelector('.tl-range-filtered').hidden = !textHits;
    if (!summary.rows.length) table.tBodies[0].rows[0].cells[0].textContent = rangeEmptyText();
    if (!detailHeadObserver) sizeDetailHead();
}
function rangeEmptyText() {
    return 'No calls overlap this range' + (textHits ? ' with the current filter' : '') + '.';
}
function rangeFactsHtml({ctxSelf, offT, nCalls}) {
    let html = pill('mute', nCalls + ' calls');
    html += '<span class="tl-pill warn tl-range-filtered"' + (textHits ? '' : ' hidden') + '>filtered</span>';
    if (adjOn) html += pill('warn', 'overhead subtracted');
    if (ctxSelf[4] > 0) html += pill('mute', 'user ' + fmtDur(ctxSelf[4]));
    for (let context = 0; context < 4; context++) if (ctxSelf[context] > 0)
        html += pill('ctx' + context, CTX_NAMES[context] + ' ' + fmtDur(ctxSelf[context]));
    if (offT > 0) html += pill('mute', 'off-CPU ' + fmtDur(offT));
    return html;
}
const rangePagerHtml = '<div class="tl-range-pages"><button class="tl-btn" type="button" data-act="range-prev" data-range-page="-1" ' +
    'aria-label="Previous page of functions"><span class="tl-range-page-label">Previous</span><span class="tl-range-page-arrow" aria-hidden="true">‹</span></button>' +
    '<form class="tl-range-jump" data-range-jump><label>Page <input type="text" name="page" data-act="range-page" inputmode="numeric" ' +
    'autocomplete="off" aria-describedby="tl-range-page-total"></label><span id="tl-range-page-total"></span>' +
    '<button type="submit" class="tl-btn" aria-label="Go to page of functions">Go</button></form>' +
    '<button class="tl-btn" type="button" data-act="range-next" data-range-page="1" aria-label="Next page of functions">' +
    '<span class="tl-range-page-label">Next</span><span class="tl-range-page-arrow" aria-hidden="true">›</span></button></div>';
function refreshRangeTable(table, summary) {
    rangeJob = null;
    cancelRangeCsv();
    const focused = document.activeElement;
    const restoreFocus = elDetail.contains(focused);
    const footer = elDetail.querySelector('.tl-range-footer'), pager = footer.querySelector('.tl-range-pages');
    if (summary.rows.length <= SUM_PAGE && pager) {
        pager.remove();
        positionTablePager(table, footer, false);
    } else if (summary.rows.length > SUM_PAGE && !pager) {
        footer.insertAdjacentHTML('beforeend', rangePagerHtml);
        positionTablePager(table, footer);
    }
    elDetail.querySelector('.tl-range-facts').innerHTML = rangeFactsHtml(summary);
    const activeHeader = setRangeSortHeaders(table, summary.sort);
    setRangeOrdering(table, summary);
    const start = summary.page * SUM_PAGE;
    table.tBodies[0].innerHTML = rangeRowsHtml(summary, start, Math.min(start + SUM_PAGE, summary.rows.length));
    rangeTableSummaries.set(table, summary);
    updateRangeControls(summary);
    if (!detailHeadObserver) sizeDetailHead();
    if (restoreFocus && (!focused.isConnected || focused.disabled) && !elDetail.contains(document.activeElement))
        activeHeader?.firstElementChild.focus({ preventScroll: true });
}
function showRangeSummary(table = null) {
    if (!range) return;
    AN.kind = 'range';
    const summary = sortedRangeSummary(), { rows } = summary;
    setSelection(-1);
    if (table) { refreshRangeTable(table, summary); draw(); return; }
    let h = rangeHeaderHtml('tl-range-head');
    h += '<span class="tl-range-facts">' + rangeFactsHtml(summary) + '</span>';
    h += '<span class="tl-range-work-status" role="status" hidden></span>';
    h += '<span class="tl-range-actions"><button class="tl-btn" data-act="range-csv"' + (rows.length ? '' : ' disabled') +
        '>Export CSV</button><button class="tl-close" title="Close (Esc)">×</button></span></div>';
    h += '<table class="tl-sum tl-range-table"><thead><tr>';
    for (const [key, label] of [['name', 'function'], ['calls', 'calls'], ['self', 'self'], ['incl', 'inclusive'], ['max', 'max'], ['err', 'errors']]) {
        h += '<th data-sort="' + key + '" aria-sort="' + rangeSortDirection(key, summary.sort) +
            '"><button class="tl-sum-sort">' + label + '</button></th>';
    }
    const start = summary.page * SUM_PAGE;
    h += '</tr></thead><tbody>' + rangeRowsHtml(summary, start, Math.min(start + SUM_PAGE, rows.length));
    h += '</tbody></table><div class="tl-range-footer"><span role="status" aria-live="polite" aria-atomic="true"></span>';
    if (rows.length > SUM_PAGE) h += rangePagerHtml;
    h += '</div><div class="an-hint">Self and inclusive are proportional overlap estimates. Calls start in [range start, range end); ' +
        'max uses whole-call duration; errors count overlapping origins. Off-CPU covers the selected tracks, independent of the filter.</div>';
    openDetail(h);
    rangeTableSummaries.set(elDetail.querySelector('.tl-range-table'), summary);
    positionSummaryPager();
    updateRangeControls(summary);
    draw();
}

// ---- jump to the text view ----
function gotoText(lineNumber, origin = null) {
    if (D.no_text) { toast('Report generated with --no-text-view: no text view (trace line ' + lineNumber + ')'); return; }
    if (window.fgReturnToTextOrigin(lineNumber)) return;
    const notice = message => window.fgSetTraceJumpStatus('Line ' + lineNumber + ' ' + message);
    const excluded = () => window.fgFilterState.lines?.has(lineNumber) === false;
    const hidden = row => row.style.display === 'none' || row.classList.contains('grp-hidden');
    const renderer = fgTextWindow;
    const unmounted = !!renderer && !excluded() && !document.getElementById('L' + lineNumber);
    const staged = unmounted && window.fgCurrentView() !== 'text';
    if (staged) renderer.setJumpPending(true);
    window.fgBeginTraceJump('text', null, origin);
    let waiting = null;
    const finishWaiting = () => {
        if (!waiting) return;
        waiting.controller.signal.removeEventListener('abort', finishWaiting);
        waiting = null;
        renderer.setJumpPending(false);
        window.fgSetTraceJumpStatus('');
    };
    const result = fgRequestTextLine(lineNumber, (row, request) => {
        finishWaiting();
        if (excluded()) return notice('is hidden by the current filter');
        if (!fgTextWindow && row.style.display === 'none') {
            const unfold = fgTextFoldBlockers(row);
            if (unfold.length) fgSetFoldStates(unfold.map(id => [id, false]));
        }
        if (hidden(row)) return notice('is hidden by the current Text layout');
        fgSelectMountedTextLine(row, false);
        const reveal = () => {
            if (!request.current() || keyboardSelectedLine !== row || !row.isConnected || excluded() || hidden(row)) return;
            fgScrollTextLine(row, { block: 'center', behavior: 'instant', centerAfterSticky: !!fgTextWindow });
            fgRevealTextCallName(row);
            fgFlashTextJump(row);
        };
        if (unmounted) reveal();
        else requestAnimationFrame(reveal);
    }, { revealFolds: true, deferScroll: unmounted, missing: () => {
        finishWaiting();
        if (fgTextDeferred()) return;
        const recorded = fgTextWindow && fgTextModel.locate(fgTextWindow.model, lineNumber);
        notice(!recorded ? 'not found' : excluded() ? 'is hidden by the current filter' : 'is hidden by the current Text layout');
    }, error: error => {
        finishWaiting();
        window.fgSetTraceJumpStatus('Unable to open trace line ' + lineNumber + ': ' + error.message);
    } });
    if (unmounted && result?.then && fgTextLineRequest?.current()) {
        waiting = fgTextLineRequest;
        waiting.controller.signal.addEventListener('abort', finishWaiting, { once: true });
        renderer.setJumpPending(true, (loaded, total) => {
            if (!waiting?.current()) return false;
            window.fgSetTraceJumpStatus('Locating L' + lineNumber + ' · ' + loaded + ' / ' + total + ' rows', true);
            return true;
        });
        window.fgSetTraceJumpStatus('Locating L' + lineNumber + '…', true);
        result.then(finishWaiting);
    } else if (staged) renderer.setJumpPending(false);
    pendingTextFocus = fgTextLineRequest;
    restoreTextFocus();
    return result;
}
function revealLine(lineNumber, focusChart = false) {
    const index = sliceForLine(lineNumber);
    const exact = index >= 0 && (sLine[index] === lineNumber || sEline?.[index] === lineNumber);
    const message = index < 0 ? 'No Timeline slice for L' + lineNumber + '.' : exact ? '' :
        'L' + lineNumber + ': no slice. Prior call L' + sLine[index] + '.';
    const statusChanged = message && window.fgSetTraceJumpStatus(message);
    if (index < 0) {
        if (statusChanged) resize();
        return;
    }
    if (detailExpanded) setDetailExpanded(false);
    else if (statusChanged) resize();
    select(index, { reveal: true, revealPage: true, frameTrace: exact });
    if (focusChart && exact) focusTimelineControl();
}

// ---- text-view filter integration ----
let filteredCallCount = null;
let smallFilterLines = null;
let filterRetryButton = null;
let filterAppliedText = '';
let focusFilterDraft = null, focusFilterUndo = null, focusFilterRequest = null;
const elFundo = elFbar.querySelector('[data-act=fundo]');
function callTaskFilter(index) {
    if (!D.caps.proc && !D.uftrace) return null;
    const task = TRACKS[sTask[index]];
    if (!task || !Number.isSafeInteger(task.pid) || task.pid < 0 || ![0, 1].includes(task.kind)) return null;
    if (task.kind === 1 && (!Number.isSafeInteger(task.cpu) || task.cpu < 0)) return null;
    return 'pid:^' + task.pid + '$' + (task.kind === 1 ? ' AND cpu:^' + task.cpu + '$' : '');
}
function stageCallFilter(index, action) {
    if (!document.getElementById('filterInput') || window.fgFilterProgress && !window.fgFilterProgress.error) return;
    const clause = action === 'focus-task' ? callTaskFilter(index) :
        'func:=' + JSON.stringify(nameOf(index)) + ' AND space:' + (sFlags[index] & FL.USER ? 'user' : 'kernel');
    if (!clause) return;
    const base = elFtext.value.trim();
    const query = base === clause || focusFilterDraft?.query === base && focusFilterDraft.clause === clause ? base :
        base ? '(' + base + ') AND (' + clause + ')' : clause;
    focusFilterDraft = { before: filterAppliedText, query, clause };
    elFtext.value = query;
    renderFilterControls();
    setToolbarOptions(true);
    elFtext.focus({ preventScroll: true });
}
function updateFocusFilter(state) {
    if (focusFilterRequest && state !== focusFilterRequest.state) {
        const request = focusFilterRequest;
        focusFilterRequest = null;
        focusFilterUndo = state.text === request.query && !request.undo ? { before: request.before, query: request.query } : null;
        focusFilterDraft = null;
    }
    if (focusFilterUndo && state.text !== focusFilterUndo.query) focusFilterUndo = null;
    if (focusFilterDraft && state.text !== focusFilterDraft.before) focusFilterDraft = null;
}
function renderFilterControls() {
    const status = window.fgFilterProgress;
    const pending = status && !status.error;
    const draft = elFtext.value.trim();
    const changed = draft !== filterAppliedText.trim();
    const requested = pending && draft === status.text.trim();
    elFdraft.hidden = pending ? requested : !changed;
    elFapply.disabled = pending ? requested : !changed && !status?.error;
    elFcurrent.hidden = !filterAppliedText;
    elFcurrent.querySelector('code').textContent = filterAppliedText;
    elMoreFilter.title = filterAppliedText;
    elFundo.hidden = !focusFilterUndo;
    elFundo.disabled = !!pending || changed;
    elFundo.title = changed ? 'Apply or discard the current draft first' : 'Restore the previous filter: ' + (focusFilterUndo?.before || 'All calls');
    if (filterRetryButton && status?.error) {
        filterRetryButton.disabled = draft !== status.text.trim();
        filterRetryButton.title = filterRetryButton.disabled ? 'Choose Apply to use the edited query' : 'Retry the failed query';
    }
}
function renderFilterProgress() {
    const status = window.fgFilterProgress;
    elFcount.setAttribute('role', status?.error ? 'alert' : 'status');
    elFcount.setAttribute('aria-live', status?.error ? 'assertive' : 'polite');
    elFcount.setAttribute('aria-busy', String(!!status && !status.error));
    elFcount.textContent = status ? status.message : !N ?
        (filteredCallCount === null ? 'No recorded calls · event tracks unfiltered' : 'Text filter only · event tracks unfiltered') :
        filteredCallCount === null ? 'All ' + N + ' calls' : filteredCallCount + ' of ' + N + ' calls' +
        (filteredCallCount === 0 ? ' · No matches. Adjust the filter or Clear.' : '');
    elFcount.title = status ? status.message : '';
    if (filterRetryButton) { filterRetryButton.remove(); filterRetryButton = null; }
    if (status?.error) {
        filterRetryButton = document.createElement('button');
        filterRetryButton.type = 'button';
        filterRetryButton.className = 'tl-btn';
        filterRetryButton.dataset.act = 'fretry';
        filterRetryButton.textContent = 'Retry filter';
        elFbar.querySelector('.tl-filter-result').appendChild(filterRetryButton);
    }
    renderFilterControls();
}
window.fgOnFilterProgress = renderFilterProgress;
let filterLayoutReady = false;
function syncFilteredTracks() {
    const hideButton = tlRoot.querySelector('[data-act=fhide]');
    hideButton.classList.toggle('on', hideUnmatched);
    hideButton.setAttribute('aria-pressed', String(hideUnmatched));
    const hide = hideUnmatched && textHits !== null;
    let changed = false;
    for (const track of ORDER) {
        const hidden = hide && track.kind !== 9 && !track.kvmEvents.length && track.hits === 0;
        if (track.hidden === hidden) continue;
        track.hidden = hidden;
        changed = true;
    }
    if (!changed && filterLayoutReady) return false;
    filterLayoutReady = true;
    const scrollTop = elScroll.scrollTop;
    const anchor = visibleTracks[firstTrackEndingAfter(scrollTop)];
    const offset = anchor ? scrollTop - anchor.y : 0;
    layout();
    if (anchor) elScroll.scrollTop = anchor.y + (anchor.hidden ? 0 : offset);
    return true;
}
function setHideUnmatched(hide) {
    if (hide === hideUnmatched) return;
    hideUnmatched = hide;
    if (!syncFilteredTracks()) return;
    if (sel >= 0 && TRACKS[sTask[sel]].hidden) {
        if (AN.kind) setSelection(-1);
        else select(-1);
    }
    draw();
}
function updateTextHits(lines) {
    const previous = smallFilterLines;
    smallFilterLines = lines && lines.size <= 64 ? [...lines] : null;
    if (previous && smallFilterLines && previous.length === smallFilterLines.length &&
        previous.every(line => lines.has(line))) return false;
    const next = lines && N ? new Uint8Array(N) : null;
    if (!next && !textHits) {
        filteredCallCount = lines ? 0 : null;
        return false;
    }
    const trackHits = new Uint32Array(TRACKS.length);
    let count = 0, same = !!(next && textHits);
    if (next && lines.size) {
        for (let index = 0; index < N; index++) {
            const entry = sLine[index], exit = sEline?.[index];
            if (!lines.has(entry) && !(exit && lines.has(exit))) continue;
            next[index] = 1;
            trackHits[sTask[index]]++;
            count++;
            if (same && !textHits[index]) same = false;
        }
    }
    if (same && count === filteredCallCount) return false;
    textHits = next;
    filteredCallCount = lines ? count : null;
    for (const track of ORDER) track.hits = trackHits[track.id] ?? 0;
    return true;
}
function applyTextFilter() {
    const st = window.fgFilterState || { text: '', lines: null };
    updateFocusFilter(st);
    const edited = elFtext.value.trim() !== filterAppliedText.trim();
    const queryChanged = (st.text || '') !== filterAppliedText;
    filterAppliedText = st.text || '';
    if (!edited) elFtext.value = filterAppliedText;
    elMoreFilter.hidden = !st.lines;
    elMore.setAttribute('aria-label', st.lines ? 'Tools · Filter on' : 'Tools');
    elFbar.classList.toggle('on', !!st.lines);
    const callsChanged = updateTextHits(st.lines);
    if (!callsChanged && filterLayoutReady) {
        if (queryChanged) {
            if (AN.kind === 'range') cancelRangeCsv();
            else refreshPanel('filter');
        }
        renderFilterProgress();
        return;
    }
    let returnToChildren = false;
    if (sel >= 0 && textHits && !(sel < N ? textHits[sel] : SYN[sel - N].kids.some(index => textHits[index]))) {
        returnToChildren = childListReturn?.index === sel;
        if (AN.kind || returnToChildren) setSelection(-1);
        else select(-1);
    }
    rebuildSearchMatches();
    rebuildErrList();
    syncFilteredTracks();
    draw();
    if (returnToChildren) restoreChildList(elDetail.contains(document.activeElement));
    else refreshPanel('filter');
    renderFilterProgress();
}
window.fgOnFilter = applyTextFilter;
function submitTimelineFilter(undo = false) {
    const fi = document.getElementById('filterInput');
    if (!fi) return;
    const query = elFtext.value.trim();
    focusFilterRequest = undo ? { state: window.fgFilterState, query, undo: true } :
        focusFilterDraft?.query === query && focusFilterDraft.before === filterAppliedText && query !== filterAppliedText ?
            { state: window.fgFilterState, query, before: filterAppliedText, undo: false } : null;
    fi.value = elFtext.value;
    if (elFtext.value.trim()) {
        const indexed = fgTextDeferred() && document.getElementById('content').hasAttribute('data-fg-text-index');
        const pending = applyFilter();
        if (!indexed && pending && typeof pending.then === 'function') {
            elFcount.textContent = 'Loading Text for full-trace filtering…';
            pending.then(applied => { if (applied === false) toast('Text filter not applied. Open Text to retry loading its data.'); });
        }
    } else clearFilter();
    renderFilterControls();
}
elFtext.addEventListener('input', () => { focusFilterDraft = null; renderFilterControls(); });
elFexample.addEventListener('change', () => {
    if (!elFexample.value) return;
    focusFilterDraft = null;
    elFtext.value = elFexample.value;
    elFexample.value = '';
    renderFilterControls();
    elFtext.focus({ preventScroll: true });
});
function restoreTimelineFilterDraft() {
    if (window.fgFilterProgress) fgCancelPendingFilter();
    focusFilterDraft = null; focusFilterRequest = null;
    elFtext.value = (window.fgFilterState || {}).text || '';
    renderFilterControls();
}
elFtext.addEventListener('keydown', ev => {
    if (timelineShortcutBlocked(ev)) return;
    ev.stopPropagation();
    if (ev.key === 'Enter') {
        ev.preventDefault();
        elFtext.blur();
        submitTimelineFilter();
    } else if (ev.key === 'Escape') {
        ev.preventDefault();
        restoreTimelineFilterDraft();
        elFtext.blur();
    }
});
elFbar.addEventListener('click', ev => {
    const b = ev.target.closest('[data-act]');
    if (!b) return;
    if (b.dataset.act === 'fapply') submitTimelineFilter();
    else if (b.dataset.act === 'fclear') { elFtext.value = ''; clearFilter(); }
    else if (b.dataset.act === 'fundo' && focusFilterUndo && !b.disabled) {
        elFtext.value = focusFilterUndo.before;
        submitTimelineFilter(true);
    }
    else if (b.dataset.act === 'fretry') {
        const status = window.fgFilterProgress;
        if (status) {
            document.getElementById('filterInput').value = elFtext.value = status.text;
            elFtext.focus({ preventScroll: true });
            submitTimelineFilter();
        }
    }
    else if (b.dataset.act === 'fhide') setHideUnmatched(!hideUnmatched);
});
if (!document.getElementById('filterInput')) {
    elFbar.style.display = 'none';
    elSearch.title = 'Highlight and navigate function-name matches; shared filtering is not enabled for this report';
    tlRoot.querySelector('#tlSearchHelp').textContent = 'Find highlights and navigates function names. Shared filtering is not enabled for this report.';
}

// ---- toolbar ----
tlRoot.querySelector('[data-role=color]').addEventListener('change', ev => {
    const picker = ev.currentTarget;
    if (!picker.selectedOptions.length || picker.selectedOptions[0].disabled) {
        picker.value = colorMode;
        return;
    }
    colorMode = picker.value;
    colorCache.clear();
    updateHeatLegend();
    draw();
});
tlRoot.querySelectorAll('[data-ov]').forEach(b => {
    b.addEventListener('click', () => {
        const k = b.dataset.ov;
        ov[k] = !ov[k];
        b.classList.toggle('off', !ov[k]);
        draw();
    });
});
elToolbar.addEventListener('click', ev => {
    const b = ev.target.closest('[data-act]');
    if (!b) return;
    if (elOptions.classList.contains('open') && b.matches('[data-act^=an-], [data-act=position], [data-act=saved-views], [data-act=fitcalls], [data-act=process-events], [data-act=pick-range], [data-act=max], [data-act=help]')) {
        setToolbarOptions(false, true);
    }
    switch (b.dataset.act) {
        case 'options': setToolbarOptions(!elOptions.classList.contains('open')); break;
        case 'searchprev': case 'searchnext':
            if (b.getAttribute('aria-disabled') !== 'true') searchNext(b.dataset.act === 'searchprev' ? -1 : 1);
            break;
        case 'errprev': gotoErr(-1); break;
        case 'errnext': gotoErr(1); break;
        case 'errmode': errFilter = errFilter ? 0 : 1; rebuildErrList(); draw(); break;
        case 'zoomout': zoomAt(1.5, W / 2); break;
        case 'zoomin': zoomAt(1 / 1.5, W / 2); break;
        case 'fit': fitAll(); break;
        case 'fitcalls': fitCalls(); break;
        case 'help': showKeyboardHint(jumpTools.contains(b) ? elMore : b); break;
        case 'notes':
            if (jumpTools.contains(b)) setToolbarOptions(false, true);
            setNotes(elNotes.classList.contains('hide'));
            break;
        case 'max': toggleFullscreen(); break;
        case 'adj': toggleAdjusted(); break;
        case 'inl': toggleInlineView(); break;
        case 'an-errors': showErrors(); break;
        case 'an-lat': showLatency(); break;
        case 'an-sec': showSections(); break;
        case 'an-tasks': showTasks(null); break;
        case 'position': showPosition(); break;
        case 'pick-range': beginRangePick(); break;
        case 'saved-views': showSavedViews(); break;
        case 'process-events': showProcessEvents(); break;
        case 'expand': {
            const any = ORDER.some(t => t.collapsed);
            for (const t of ORDER) t.collapsed = !any;
            layout(); draw(); break;
        }
    }
});

function updateInlineButton() {
    const button = tlRoot.querySelector('[data-act=inl]');
    const label = 'Inline: ' + (inlineView ? 'shown' : 'hidden');
    button.querySelector('.tl-toolbar-long').textContent = label;
    button.setAttribute('aria-pressed', String(inlineView));
    button.classList.toggle('on', inlineView);
}
function toggleInlineView(force) {
    if (!HAS_INL) { toast('No inline information: needs resolved call sites (funcgraph-retaddr + vmlinux with debug info)'); return; }
    const next = force === undefined ? !inlineView : !!force;
    if (next === inlineView) return;
    const scrollTop = elScroll.scrollTop;
    const anchor = visibleTracks[firstTrackEndingAfter(scrollTop)];
    const offset = anchor ? scrollTop - anchor.y : 0;
    const selectedTrack = sel >= 0 ? TRACKS[sTask[sel]] : null;
    const selectedY = selectedTrack && !selectedTrack.collapsed && !selectedTrack.hidden
        ? selectedTrack.y + HDR + rowOf(sel) * ROW : -Infinity;
    const selectedRowVisible = selectedY >= scrollTop && selectedY + ROW <= scrollTop + H;
    inlineView = next;
    applyView();
    updateInlineButton();
    if (typeof lsSet === 'function') lsSet('tlInline', inlineView ? '1' : '0');
    if (sel >= N && !inlineView && !isGhost(sel)) select(-1);
    clearTimelineHover();
    layout();
    if (anchor) {
        const nextTop = anchor.y + Math.min(offset, anchor.h - 1);
        if (nextTop !== elScroll.scrollTop) elScroll.scrollTop = nextTop;
    }
    if (sel >= 0 && selectedRowVisible) revealRow(sel, { expand: false, nearest: true });
    drawTracks();
}
function toggleAdjusted() {
    if (!sDurAdj) { toast('No overhead estimate: capture with fgcapture.sh --calibrate or pass --overhead'); return; }
    setAdjusted(!adjOn);
    const b = tlRoot.querySelector('[data-act=adj]');
    b.textContent = 'Overhead: ' + (adjOn ? 'subtracted' : 'measured');
    b.classList.toggle('on', adjOn);
    colorCache.clear();
    updateHeatLegend();
    refreshPanel('timings');
    draw();
    toast(adjOn ? 'Durations: overhead subtracted (estimate)' : 'Durations: as measured');
}
function refreshPanel(reason) {
    if (!elDetail.classList.contains('show') || AN.kind === 'process') return;
    if (AN.kind === 'errors') showErrors();
    else if (AN.kind === 'lat') showLatency();
    else if (AN.kind === 'sec') showSections();
    else if (AN.kind === 'dist') showDist(AN.func);
    else if (AN.kind === 'wait') showWaitChain(AN.off);
    else if (AN.kind === 'tasks') showTasks();
    else if (AN.kind === 'children') showChildren();
    else if (range) {
        if (reason === 'filter') refreshRangeFilter();
        else requestRangeSummary();
    }
    else if (sel >= 0) {
        if (!showsRecordedCall(sel)) showDetail(sel);
        else if (reason === 'timings') refreshCallTimings(sel);
    }
}

// ---- capability notes ----
(function notes() {
    const c = D.caps, out = [];
    const note = (html, info) => out.push('<span class="tl-note' + (info ? ' info' : '') + '">' + html + '</span>');
    if (KVM) {
        note('KVM row: <b>Guest</b> = entry → exit; <b>Host</b> = exit → next entry, grouped by Host TID. Boundary windows are wall time, not function calls or CPU time; waits may be included. Guest internals are not recorded by these events.', true);
        if (KVM.issues.length || KVM.losses.length) note('KVM boundary gaps: ' + KVM.issues.length + '; loss markers: ' + KVM.losses.length + '. Uncertain boundaries are not joined. Original events remain available in the KVM details and Text.');
    }
    if (MODE === 'compressed') note('No timestamps: slices are packed by duration and gaps are lost. Enable <code>options/funcgraph-abstime</code>.');
    else if (MODE === 'count') note('No timestamps or durations: the x axis shows call order only. Enable <code>funcgraph-abstime</code> and <code>funcgraph-duration</code>.');
    if (D.uftrace) {
        if (MIXED && !D.uftrace.aligned) note('Clock alignment is not verified. Address spaces are shown separately; do not compare times across them.');
        if (D.stats.detached_kernel_roots) note(D.stats.detached_kernel_roots + ' overlapping kernel roots could not be linked; shown on an unjoined lane.', true);
        if (HAS_KERNEL && !D.btf && c.retval) note('Kernel returns are raw bits; errno inference requires kernel BTF.', true);
        if (HAS_KERNEL && !c.sched_events) note('No usable kernel sched_switch records. CPU rows show observed kernel-call coverage, not task start/exit or continuous CPU time. User calls without a recorded CPU are not extended onto a CPU; gaps and off-CPU periods remain unknown.', true);
    } else {
    if (N && !c.latency) note('No latency columns: context is inferred from IRQ/softirq entry function names. Enable <code>options/latency-format</code> for exact irqs-off / need-resched / hardirq / softirq / preempt flags.');
    if (!c.proc && !c.switch_markers) note('No task info: tracks are per CPU. Enable <code>options/funcgraph-proc</code>.');
    if (N && !c.retval) note('No return values: error paths need <code>options/funcgraph-retval</code>.');
    else if (N && !D.btf && c.retval_btf) note('The kernel printed type-aware return values (BTF-aware funcgraph-retval): void functions have no ret=, errnos and ERR_PTRs are signed.', true);
    else if (N && !D.btf) note('No BTF: errors are guessed from the value alone (void functions may show false positives). Use a vmlinux with <code>CONFIG_DEBUG_INFO_BTF</code> or pass <code>--btf</code>.');
    if (N && !c.retaddr) note('No call sites: enable <code>options/funcgraph-retaddr</code> to see where each call came from.', true);
    else if (N && !c.args && !c.retval_btf) note('Arguments are not printed while <code>funcgraph-retaddr</code> is on (kernel limitation, e.g. 6.18).', true);
    if (TIMED && !c.switch_markers && c.proc) note('No context-switch markers seen: off-CPU periods cannot be shown.', true);
    }
    if (N && D.overhead) note('Tracing overhead estimate (' + esc(D.overhead.source) + '): ' + fmtDur(D.overhead.child) +
        ' per traced child call, ' + fmtDur(D.overhead.self) + ' per call. Toggle <b>Overhead</b> to subtract it; slice positions stay as measured.', true);
    else if (N && TIMED) note(D.uftrace ? 'Durations include tracing overhead; no per-address-space correction was recorded.' :
        'Durations include tracing overhead (measured 0.2 us per traced call on a QEMU x86_64 VM, 1.1 us on a Raspberry Pi 4). Capture with <code>fgcapture.sh --calibrate</code> to get a correction.', true);
    if (D.lost.length) note(D.lost.length + ' "LOST EVENTS" record(s): some slices are incomplete (marked with dotted edges). Increase <code>buffer_size_kb</code>.');
    const st = D.stats;
    if (st.truncated || st.orphans) note(st.truncated + ' call(s) without exit, ' + st.orphans + ' without entry (trace window edges).', true);
    elNotes.innerHTML = out.join('');
})();

elHelp.innerHTML = `
<button class="tl-btn tl-help-close" type="button" aria-label="Close help">×</button>
<h4>Timeline Help</h4>
<h4>Navigate</h4><ul>
<li>CPU rows show recorded coverage, not the beginning of <code>main</code>. Without <code>sched_switch</code>, dashed segments span CPU-tagged observations only; their duration is not continuous CPU running time. Missing CPU placement remains unknown.</li>
<li><kbd>W</kbd>/<kbd>S</kbd> or <kbd>Ctrl</kbd>+wheel: zoom at cursor</li>
<li><kbd>A</kbd>/<kbd>D</kbd>, drag, or <kbd>Shift</kbd>+wheel: pan</li>
<li>Wheel: scroll tracks · click track header: collapse</li>
<li><kbd>0</kbd> fit all · <kbd>F</kbd> zoom to selection · double-click: zoom to slice</li>
<li><kbd>Shift</kbd>+drag: summarize a range in this track (<kbd>Shift</kbd>+<kbd>Alt</kbd>: all tracks)</li>
<li><b>Tools → Select range</b>: choose the scope, then tap or click two points. With keyboard focus in the chart, <kbd>Enter</kbd> picks its center; pan with the arrow keys between picks.</li></ul>
<p>Range summaries use <b>Previous / Next</b> for 300-function pages. Sorting or changing the range, shared filter or Overhead resets the page; <b>Export CSV</b> includes every function in that scope.</p>
<p><b>Critical sections</b> uses 200-row pages; <b>Scheduling latency</b> uses 300-row pages. <b>Previous / Next</b> does not grow the table. Each section kind remembers its page; sort, shared-filter or Overhead changes reset it. CSV always exports the complete sorted scope, not just the page.</p>
<h4>Touch</h4><ul>
<li>Tap a call or track header. Swipe horizontally on the tracks to pan; swipe vertically to scroll.</li>
<li>Pinch the tracks to zoom around your fingers, or use <b>−</b>, <b>+</b> and <b>Fit trace</b> on touch devices.</li>
<li><b>Fit trace</b> (<kbd>0</kbd> / <kbd>Home</kbd>) and the overview include all recorded scheduling context. The initial view focuses on calls and recorded exec / exit milestones. <b>Tools → Navigate → Fit calls</b> shows all recorded calls without changing selection, filters or analysis.</li>
<li><b>Tools</b> opens grouped navigation, display, analysis and filter controls without resizing the tracks. Click outside or press <kbd>Escape</kbd> to close it. <b>Notes</b> shows capture limitations. In very short full-screen views after a jump from Text, Search is in <b>Tools</b>.</li></ul>
<h4>Search and filter</h4><ul>
<li><b>Search</b>: function-name regex; highlights matches and navigates between calls with Enter / Shift+Enter.</li>
<li><b>Filter</b>: conditions such as <code>pid:1234 AND graph:vfs_read</code>, applied with Enter and shared with Text. Analysis panels use this filter, not the search expression.</li>
<li>Search navigation stays within the filtered calls. <b>Hide unmatched</b> hides tracks without filter matches.</li></ul>
${N ? '' : '<p>No function calls were recorded. Search has no call matches; shared filters apply to Text lines only and do not hide or filter event tracks.</p>'}
<h4>Colors</h4>
<p><b>Tools → Color → Total time</b> includes child calls and recorded waits; <b>Self time</b> excludes child calls. Both use a blue-to-red log scale from zero to the maximum across all recorded calls, independent of zoom and filters. Neither measures CPU time. The legend inside Tools follows the theme and Overhead setting; slice positions and widths stay as recorded. Error overlays take priority; turn off <b>Errors</b> to compare duration colors. Duration colors are unavailable without durations or recorded calls. <b>Context</b> remains the default.</p>
<h4>Select</h4><ul>
<li>Call details group <b>Duration distribution</b> and <b>Open in Text</b> under <b>Actions</b>. Use arrow keys / Home / End inside the menu; Escape closes it before leaving full screen. Text is disabled when it is not included in the report.</li>
<li><b>Actions → Focus this task / Focus this function</b> adds an exact task or space-qualified raw symbol to the current filter draft. Choose <b>Apply</b> to confirm; <b>Undo focus</b> restores the previously applied filter. Missing task identity disables task focus. Reports without filtering omit these actions.</li>
<li><b>Tools → Saved views…</b>: name a position, selected call or range and applied filter, with optional notes. Click a saved name to restore it. Import / export transfers views for matching report data only; share the report separately. Browser storage failures are shown in the dialog.</li>
<li>Call details have native navigation buttons: <kbd>↑</kbd> recorded parent · <kbd>↓</kbd> first recorded child · <kbd>[</kbd>/<kbd>]</kbd> recorded siblings (task roots stay within the task); synthetic frames are skipped</li>
<li><kbd>E</kbd>/<kbd>Shift+E</kbd> next/prev error · <kbd>/</kbd> search, <kbd>N</kbd>/<kbd>Shift+N</kbd> next/prev match</li>
<li>Click an off-CPU segment: wait chain (who woke the task, wake latency, and who woke the waker)</li>
<li><b>Tasks</b>: find recorded tasks by name / PID / CPU, including tasks without traced calls; browse 100 identities per page with Previous / Next and export all matching rows, not just this page</li>
<li><kbd>G</kbd> or <b>Go to…</b>: center at an offset without changing zoom, or enter start/end to show an exact interval. Timestamped reports accept ns/us/ms/s; recorded timestamps require a known exact origin. Packed and call-order coordinates are explicitly not wall-clock time. Selection, filters and analysis stay unchanged.</li>
<li><b>Errors</b>: independent pages of 200 kernel error groups, 200 user API hint groups and 100 atomic groups. Previous / Next replaces rows without losing a group's occurrence position; CSV exports include the complete current scope, not just the page. User hints match explicit API symbol conventions, not captured errno or verified propagation. <code>result:failure</code> matches both spaces; <code>ret:all</code> remains kernel errno only.</li>
<li><b>Duration distribution</b>: browse 50 slowest-first calls per page. Previous / Next do not change percentiles or histogram scope; CSV includes all calls in the selected function, call site and duration bucket, not just the page. Toggle <b>Histogram</b> to give calls more room while keeping all six statistics; this report-local choice does not change the saved panel split.</li>
<li><b>Explore all children</b> in call details: parent-scoped groups/calls, <b>Page → Go</b> for direct paging, longest-call navigation and full CSV</li>
<li><kbd>I</kbd>: show / hide inlined functions as dashed frames (extent = first to last traced call inside)</li>
<li><kbd>M</kbd> or <b>Full screen</b>: give Timeline the whole screen; <b>Exit full screen</b> or <kbd>Escape</kbd> restores the page. If the browser disallows native full screen, the page viewport is used instead. Zoom, selection, filters and the panel split stay unchanged.</li>
<li>Drag the divider to resize details, or focus it and use <kbd>↑</kbd>/<kbd>↓</kbd> for height or <kbd>←</kbd>/<kbd>→</kbd> for width (16px). Use <kbd>Home</kbd>/<kbd>End</kbd> for the limits, or <kbd>Enter</kbd> or double-click to reset.</li>
<li>In a very short detail panel, the expand icon next to Close borrows the chart area. Press it again to restore the split; closing the panel restores the chart too. The saved panel height, query and current task page are not changed.</li>
<li><kbd>O</kbd>: durations as measured / with the tracing overhead subtracted</li>
<li><kbd>H</kbd>: duration distribution of the selected function · toolbar <b>Errors</b> / <b>Scheduling latency</b>: summaries</li>
<li><kbd>Enter</kbd>: open in text view · <kbd>Alt</kbd>+click a text line: show it here</li></ul>
<h4>Legend</h4><div class="tl-legend">
${HAS_USER ? '<i style="background:#9bcdbf"></i><span>user space (Context color)</span>' : ''}
${MIXED ? '<i style="background:currentColor;height:1px"></i><span>user → kernel boundary, only over a linked kernel call</span>' : ''}
<i style="background:hsl(214,55%,70%)"></i><span>task context</span>
${KVM ? '<i style="background:#d5e9e1"></i><span>KVM Guest boundary window (wall time, not CPU time)</span><i style="background:#dce3ee"></i><span>KVM Host boundary window (may include waits; not necessarily QEMU userspace)</span>' : ''}
<i style="background:hsl(152,62%,68%)"></i><span>softirq</span>
<i style="background:hsl(30,62%,68%)"></i><span>hardirq</span>
<i style="background:hsl(282,62%,70%)"></i><span>NMI</span>
<i style="background:#d32f2f"></i><span>error origin (reaches user space)</span>
<i style="background:#e8873a"></i><span>error origin (handled by caller)</span>
${HAS_USER ? '<i style="background:#e8873a"></i><span>user API failure hint (symbol-matched convention; not a kernel error chain)</span>' : ''}
<i style="background:#f6b8b8;outline:1.5px solid #c62828"></i><span>error propagation</span>
<i style="background:repeating-linear-gradient(135deg,#888 0 2px,#ddd 2px 4px)"></i><span>irqs disabled when the call started</span>
<i style="background:#4b5563;height:2px"></i><span>under the track header: irqs-off periods</span>
<i style="background:#f59e0b"></i><span>reschedule request pending, including lazy / preempt requests (header band / corner)</span>
<i style="background:repeating-linear-gradient(135deg,#aaa 0 2px,#eee 2px 4px)"></i><span>off-CPU (amber tint: preempted)</span>
<i style="background:#8e44ad"></i><span>caller ≠ recorded parent (cause unresolved); ⇢ = static tail-jump evidence</span>
<i style="background:#0f9d8a;height:2px"></i><span>wakeup: waker → woken task (sched_waking/wakeup); bunched: small marker only</span>
<i style="background:rgba(110,120,140,.13);outline:1px dashed #6b7280"></i><span>inline frame (Inline view)</span>
<i style="background:#8fb3ef;border-bottom:2px solid #c62828"></i><span>red underline: sleeping function called in atomic context</span>
<i style="background:#8fb3ef;border-right:2px dotted #6b7280"></i><span>dotted right / left edge: exit / entry not captured</span>
<i style="background:#d32f2f;clip-path:polygon(50% 0,100% 100%,0 100%)"></i><span>error origin too narrow to label (triangle)</span>
<i style="background:#f0a35a;height:3px"></i><span>CPU track strip: hardirq (orange) / softirq (green) / NMI (purple)</span></div>`;
function fitTimelineHelp() {
    if (!elHelp.classList.contains('show')) return;
    const viewport = window.visualViewport;
    const width = viewport?.width ?? innerWidth, height = viewport?.height ?? innerHeight;
    const edge = Math.min(12, width / 4, height / 4);
    const panelWidth = Math.min(330, width - 2 * edge);
    const viewportTop = viewport?.offsetTop ?? 0, bottom = viewportTop + height - edge;
    const entry = document.getElementById(tlRoot.classList.contains('tl-fullscreen') ? 'timelineHelpBtn' : 'viewHelpBtn');
    const entryBox = entry?.getBoundingClientRect();
    const belowEntry = entryBox?.bottom + 6;
    const top = entryBox?.height && belowEntry > viewportTop + edge && bottom - belowEntry >= 144 ?
        belowEntry : viewportTop + edge;
    elHelp.style.left = ((viewport?.offsetLeft ?? 0) + width - edge - panelWidth) + 'px';
    elHelp.style.top = top + 'px';
    elHelp.style.width = panelWidth + 'px';
    elHelp.style.maxHeight = (bottom - top) + 'px';
}
function showTimelineHelp(opener) {
    closeCallActions();
    setToolbarOptions(false);
    elHelp.classList.add('show');
    fitTimelineHelp();
    fgOpenHelpPanel(elHelp, () => elHelp.classList.remove('show'), opener);
}
function closeTimelineHelp(restoreFocus = true) {
    fgCloseHelp(restoreFocus, elHelp);
    elHelp.classList.remove('show');
}
elHelp.querySelector('.tl-help-close').addEventListener('click', () => closeTimelineHelp());

// ---- init ----
palette();
applyTextFilter();
if (!CHAINS.length && !USER_ERRORS.size) tlRoot.querySelector('.tl-errnav').style.display = 'none';
if (!D.caps.latency) { tlRoot.querySelector('[data-ov=irq]').classList.add('na'); tlRoot.querySelector('[data-ov=preempt]').classList.add('na'); }
if (!D.resched.length && !D.caps.latency) tlRoot.querySelector('[data-ov=rs]').classList.add('na');
if (!D.offcpu.length) tlRoot.querySelector('[data-ov=off]').classList.add('na');
if (!sDurAdj) tlRoot.querySelector('[data-act=adj]').style.display = 'none';
if (!HAS_INL) tlRoot.querySelector('[data-act=inl]').style.display = 'none';
if (!sDurAdj) tlRoot.querySelector('[aria-label="Timeline display"]').style.display = 'none';
if (HAS_INL && typeof lsGet === 'function' && lsGet('tlInline') === '1') toggleInlineView(true);
const noteCount = elNotes.children.length;
if (noteCount) {
    const label = 'Notes ' + noteCount;
    elNotesButton.querySelector('.tl-toolbar-long').textContent = label;
    elNotesButton.setAttribute('aria-label', label);
}
else elNotesButton.hidden = true;
syncToolbar();
if (!WAKES.length) tlRoot.querySelector('[data-ov=wk]').classList.add('na');
if (!D.caps.retval && !USER_ERRORS.size) tlRoot.querySelector('[data-ov=err]').classList.add('na');
new MutationObserver(() => { palette(); draw(); scheduleDistHistogram(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
window.addEventListener('resize', () => {
    if (window.fgCurrentView() !== 'timeline') return;
    if (fullscreenTraceJump()) syncToolbar();
    resizeAfterToolbarLayout();
    sizeDetailHead();
    queueCallDetailReturnEnd();
});
landscapeDetails.addEventListener('change', () => { if (window.fgCurrentView() === 'timeline') resize(); });
window.addEventListener('resize', fitTimelineHelp);
window.addEventListener('scroll', scheduleToolbarOverlays, { passive: true });
tlRoot.addEventListener('scroll', () => {
    dismissTimelineHover();
    callDetailViewportChanged();
    scheduleToolbarOverlays();
}, { passive: true });
window.visualViewport?.addEventListener('resize', scheduleToolbarOverlays);
window.visualViewport?.addEventListener('scroll', scheduleToolbarOverlays);
window.visualViewport?.addEventListener('resize', fitTimelineHelp);
window.visualViewport?.addEventListener('scroll', fitTimelineHelp);
if (typeof ResizeObserver !== 'undefined') {
    let controlsFrame = 0;
    const visibleControlSizes = new Map();
    const controlsObserver = new ResizeObserver(entries => {
        if (window.fgCurrentView() !== 'timeline') return;
        const compact = compactToolbar.matches;
        let needsResize = false;
        for (const {target, contentRect} of entries) {
            if (target === elOptions || (compact && target === elNotes)) scheduleToolbarOverlays();
            if (target !== elToolbar && (target !== elNotes || compact)) continue;
            const {width, height} = contentRect;
            if (width === 0 && height === 0) continue;
            const previous = visibleControlSizes.get(target);
            if (previous?.width === width && previous.height === height) continue;
            visibleControlSizes.set(target, {width, height});
            needsResize = true;
        }
        if (!needsResize || controlsFrame) return;
        controlsFrame = requestAnimationFrame(() => {
            controlsFrame = 0;
            if (window.fgCurrentView() !== 'timeline') return;
            if (fullscreenTraceJump() && !detailExpanded && jumpTools.hidden && !inlineFullscreenToolbarFits()) syncToolbar();
            resizeAfterToolbarLayout();
        });
    });
    controlsObserver.observe(elToolbar);
    controlsObserver.observe(elNotes);
    controlsObserver.observe(elOptions);
}

// probe: read access to the model and a few actions, for tests and the browser console
const probe = () => ({ N, NA, SYN, S, sName, sStart, sEnd, sDepth, sParent, sTask, nameOf, isGhost, gapOf, TRACKS, styleOf: index => sliceStyle(index).slice(),
    kvm: { events: KVM_EVENTS, intervals: KVM_INTERVALS, select: showKvmDetail, xyOf: kvmCoordinates, selected: () => selKvm },
    select: (i) => select(i, { reveal: true }), zoomTo, sel: () => sel, view: () => [view.s, view.e],
    // page coordinates of slice i (center) / of off-CPU record r (center), for scripted mouse input
    xyOf: (i) => { const r = elCv.getBoundingClientRect(), k = W / (view.e - view.s), t = TRACKS[sTask[i]];
        return [r.left + ((Math.max(sStart[i], view.s) + Math.min(sEnd[i], view.e)) / 2 - view.s) * k,
                r.top + t.y - elScroll.scrollTop + HDR + rowOf(i) * ROW + ROW / 2]; },
    offXY: (r) => { const c = elCv.getBoundingClientRect(), k = W / (view.e - view.s), t = TRACKS[r[0]];
        return [c.left + ((Math.max(r[1], view.s) + Math.min(r[2], view.e)) / 2 - view.s) * k,
                c.top + t.y - elScroll.scrollTop + HDR + offRow(r) * ROW + ROW / 2]; },
    range: (s, e, track) => { range = { s: s, e: e, track: track === undefined ? null : track }; select(-1); showRangeSummary(); draw(); },
    errors: showErrors, latency: showLatency, sections: showSections, dist: showDist, wait: showWaitChain,
    inline: (on) => { if (!!on !== inlineView) toggleInlineView(); } });
return { resize: () => {
    resumeSearch(); resize(); scheduleDetailScrollReset(); queueRangeSummary(); queueRangeCsv(); queueContextCsv(); queueChildrenCsv();
    queueDistCsv(); queueSlowFastCsv(); queueWindowCsv(); queueErrorCsv(); queueTaskCsv(); queueWaitCsv();
    queueDetailWork();
}, revealLine: revealLine, draw: draw, probe: probe, traceJumpFullscreen, restoreTraceJumpFullscreen,
    suspend: () => { cancelTimelineGestures(); stopDetailDrag?.(); closeTimelineHelp(false); suspendSearch(); closeSavedViews(); closeCallActions(); cancelRangePick(false, false); if (elPosition.open) elPosition.close(); if (tlRoot.classList.contains('tl-fullscreen') || document.fullscreenElement === tlRoot) leaveFullscreen(false); setToolbarOptions(false); if (clearTimelineHover()) tracksDirty = true; }, showHelp: showTimelineHelp };
