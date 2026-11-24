/* FuncGraph Timeline: canvas rendering */
let W = 0, H = 0, DPR = 1, dark = false, P = null;
let sliceStyles = null;
const ctx = elCv.getContext('2d'), hctx = elHead.getContext('2d');
const hoverTimeCtx = elHoverTime.getContext('2d');
const HOVER_TIME_HEIGHT = 20;
let hoverTimeWidth = 1, hoverTimeKey = '';
const FONT = '11px ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
const FONT_B = '600 11.5px system-ui, -apple-system, "Segoe UI", sans-serif';
const FONT_S = '11px system-ui, -apple-system, "Segoe UI", sans-serif';
const FONT_INLINE = '500 9px ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
let charW = null, trackFontDpr = null;
const CTX_HUE = [214, 152, 30, 282];

function palette() {
    dark = document.documentElement.getAttribute('data-theme') === 'dark';
    P = dark ? {
        bg: '#1f2126', hdr: '#272a31', hdrText: '#e6e9ef', sub: '#8d95a3', grid: 'rgba(255,255,255,.05)',
        text: '#f3f5f8', textDim: '#c9ced6', sel: '#ffffff', hov: 'rgba(255,255,255,.7)',
        inlineFill: 'rgba(150,165,185,.14)', inlineHover: 'rgba(150,165,185,.25)', inlineEdge: 'rgba(170,185,205,.5)', inlineTag: '#a6b2c4',
        off: 'rgba(150,158,170,.16)', offLine: 'rgba(170,178,190,.55)', offPre: 'rgba(245,158,11,.20)',
        ovBar: '#4f7fd9', ovWin: 'rgba(255,255,255,.10)', ovMask: 'rgba(0,0,0,.35)', axis: '#a7afbc',
        range: 'rgba(80,140,255,.16)', rangeLine: '#6ea0ff', selBand: 'rgba(110,160,255,.07)', selLine: 'rgba(130,175,255,.8)', selTag: '#3b6fd8',
        errO: '#e53935', errOh: '#d9822b', errP: '#7d3434', errPh: '#6b4a26', errLine: '#ff6b6b', errLineH: '#f0a050',
        rs: '#f59e0b', io: 'rgba(225,230,240,.75)', mis: '#c18cf0', dense: 0.75, denseFill: '#a3a3a3'
    } : {
        bg: '#ffffff', hdr: '#f3f5f8', hdrText: '#1d2433', sub: '#7a8494', grid: 'rgba(0,0,0,.045)',
        text: '#16202e', textDim: '#3b4555', sel: '#111827', hov: 'rgba(17,24,39,.55)',
        inlineFill: 'rgba(110,120,140,.10)', inlineHover: 'rgba(110,120,140,.18)', inlineEdge: 'rgba(90,105,125,.5)', inlineTag: '#647184',
        off: 'rgba(120,128,140,.10)', offLine: 'rgba(110,118,130,.55)', offPre: 'rgba(245,158,11,.16)',
        ovBar: '#3b6fd8', ovWin: 'rgba(59,111,216,.10)', ovMask: 'rgba(255,255,255,.55)', axis: '#5b6573',
        range: 'rgba(59,111,216,.12)', rangeLine: '#3b6fd8', selBand: 'rgba(59,111,216,.05)', selLine: 'rgba(59,111,216,.75)', selTag: '#3b6fd8',
        errO: '#d32f2f', errOh: '#e8873a', errP: '#f6b8b8', errPh: '#f8d6b3', errLine: '#c62828', errLineH: '#d9822b',
        rs: '#f59e0b', io: '#4b5563', mis: '#8e44ad', dense: 0.85, denseFill: '#737373'
    };
    sliceStyles = {
        inline: [P.inlineFill, P.textDim, P.inlineEdge],
        inlineHover: [P.inlineHover, P.textDim, P.inlineEdge],
        ghost: [dark ? 'rgba(120,130,150,.22)' : 'rgba(110,120,140,.13)', P.textDim,
            dark ? 'rgba(170,180,200,.7)' : 'rgba(90,100,120,.6)'],
        origin: [P.errO, '#ffffff', null],
        handledOrigin: [P.errOh, '#ffffff', null],
        propagated: [P.errP, dark ? '#ffe3e3' : '#5a1010', P.errLine],
        handledPropagated: [P.errPh, dark ? '#ffe3e3' : '#5a1010', P.errLineH]
    };
    colorCache.clear();
    updateHeatLegend();
    hatch = makeHatch(dark ? 'rgba(0,0,0,.30)' : 'rgba(0,0,0,.20)');
    offHatch = makeHatch(dark ? 'rgba(200,205,215,.14)' : 'rgba(90,98,110,.14)');
}
function makeHatch(color) {
    const c = document.createElement('canvas');
    c.width = c.height = 6;
    const g = c.getContext('2d');
    g.strokeStyle = color;
    g.lineWidth = 1.2;
    g.beginPath(); g.moveTo(0, 6); g.lineTo(6, 0); g.moveTo(-3, 3); g.lineTo(3, -3); g.moveTo(3, 9); g.lineTo(9, 3); g.stroke();
    return ctx.createPattern(c, 'repeat');
}
let hatch = null, offHatch = null;

// ---- colors ----
const COMPILER_SUFFIX = /(?:\.(?:isra|constprop|part|clone|lto|lto_priv)\.\d+|\.(?:cold|hot)(?:\.\d+)?|\.llvm\.[0-9a-fA-F]+)+$/;
const sliceNameCache = new Map();
function symbolLabel(name) {
    const cached = sliceNameCache.get(name);
    if (cached !== undefined) return cached;
    const clone = / \[clone ([^\]]+)\]$/.exec(name);
    const base = clone && !clone[1].replace(COMPILER_SUFFIX, '') ? name.slice(0, clone.index) : name;
    const label = base.replace(COMPILER_SUFFIX, '') || name;
    sliceNameCache.set(name, label);
    return label;
}
function sliceLabel(index) { return symbolLabel(nameOf(index)); }
function symbolSiteLabel(value) {
    const site = value.match(/^(.+?)(\+0[xX][\da-fA-F]+\/0[xX][\da-fA-F]+.*| \(offset unavailable\))$/);
    return site ? symbolLabel(site[1]) + site[2] : symbolLabel(value);
}
const nameHash = new Int32Array(S.length).fill(-1);
function hashOf(si) {
    let h = nameHash[si];
    if (h >= 0) return h;
    const s = S[si];
    h = 2166136261;
    for (let k = 0; k < s.length; k++) { h ^= s.charCodeAt(k); h = Math.imul(h, 16777619); }
    h = (h >>> 1) & 0x3fffffff;
    nameHash[si] = h;
    return h;
}
const colorCache = new Map();
function hsl(h, s, l) { return 'hsl(' + h.toFixed(0) + ',' + s.toFixed(0) + '%,' + l.toFixed(0) + '%)'; }
const HEAT_BINS = 20;
function heatBin(sliceIndex) {
    const value = colorMode === 'total' ? sDur[sliceIndex] : sSelf[sliceIndex];
    const logarithm = colorMode === 'total' ? durationColorLog : selfColorLog;
    return logarithm > 0 ? Math.min(HEAT_BINS - 1, Math.max(0, Math.floor((HEAT_BINS - 0.01) * Math.log1p(value) / logarithm))) : 0;
}
function heatColor(bin) {
    const ratio = bin / (HEAT_BINS - 1);
    return hsl(220 - 220 * ratio, 30 + 50 * ratio, dark ? 30 + 18 * ratio : 82 - 22 * ratio);
}
function updateHeatLegend() {
    const legend = tlRoot.querySelector('.tl-heat-scale');
    legend.hidden = colorMode !== 'heat' && colorMode !== 'total';
    const picker = tlRoot.querySelector('[data-role=color]');
    if (legend.hidden) picker.removeAttribute('aria-describedby');
    else picker.setAttribute('aria-describedby', 'tlHeatScale');
    const spaceKey = tlRoot.querySelector('.tl-space-key');
    if (spaceKey) spaceKey.hidden = colorMode !== 'ctx';
    if (legend.hidden) return;
    const total = colorMode === 'total';
    const colors = Array.from({ length: HEAT_BINS }, (_, bin) => heatColor(bin));
    legend.querySelector('.tl-heat-ramp').style.background = 'linear-gradient(to right, ' + colors.join(', ') + ')';
    legend.querySelector('.tl-heat-caption').textContent = '0–' + fmtDur(total ? maxDuration : maxSelf) + ' · log · all calls';
    legend.querySelector('.tl-heat-note').textContent = (total ? 'Includes children and waits; not CPU time.' : 'Excludes children; not CPU time.') +
        (adjOn ? ' Overhead estimate.' : ' Measured.');
}
function baseSliceStyle(i) {
    const f = sFlags[i], c = f & FL.CTX;
    let key, h, s, l, bin;
    switch (colorMode) {
        case 'name': key = sName[i]; break;
        case 'total':
        case 'heat': bin = heatBin(i); key = colorMode + bin; break;
        case 'cpu': key = 'c' + cpuOf(i); break;
        case 'module': key = 'm' + (sMod ? sMod[i] : -1); break;
        default: key = sName[i] * 8 + c + (f & FL.USER ? 4 : 0);
    }
    const cached = colorCache.get(key);
    if (cached) return cached;
    if (bin !== undefined) {
        const style = [heatColor(bin), P.text, null];
        colorCache.set(key, style);
        return style;
    }
    const hh = colorMode === 'module' ? (sMod && sMod[i] >= 0 ? hashOf(sMod[i]) : 0) : hashOf(sName[i]);
    switch (colorMode) {
        case 'name': h = hh % 360; s = 48 + hh % 20; l = dark ? 38 + (hh >> 9) % 10 : 68 + (hh >> 9) % 12; break;
        case 'cpu': {
            const cp = cpuOf(i);
            h = cp < 0 ? 210 : (cp * 137.508) % 360; s = cp < 0 ? 8 : 52; l = dark ? 40 : 70; break;
        }
        case 'module':
            if (sMod && sMod[i] >= 0) { h = hh % 360; s = 62; l = dark ? 40 : 66; }
            else { h = 214; s = 14; l = dark ? 34 : 80; }
            break;
        default:
            h = (f & FL.USER ? 166 : CTX_HUE[c]) + ((hh % 13) - 6) * 2.4;
            s = (f & FL.USER ? 28 : c === 0 ? 46 : 58) + hh % 16;
            l = dark ? 33 + (hh >> 7) % 11 : 67 + (hh >> 7) % 13;
    }
    const style = [hsl(h, s, l), P.text, null];
    colorCache.set(key, style);
    return style;
}
function sliceStyle(i) {
    // returns [fill, textColor, borderColor|null]
    if (i >= N) return isGhost(i) ? sliceStyles.ghost : i === hov ? sliceStyles.inlineHover : sliceStyles.inline;
    if (ov.err && USER_ERRORS.has(i)) return sliceStyles.handledOrigin;
    if (ov.err && eRole[i] >= 0 && (showMinor || eChain[i] < 0 || !CHAINS[eChain[i]][6])) {
        const handled = eFate[i] === 0;
        if (eRole[i] === 0) return handled ? sliceStyles.handledOrigin : sliceStyles.origin;
        return handled ? sliceStyles.handledPropagated : sliceStyles.propagated;
    }
    return baseSliceStyle(i);
}
const DENSE_MATCH_BLOCK = 1024;
let matchingCache = null;
function matchingState() {
    if (!searchHits && !textHits) { matchingCache = null; return null; }
    if (!matchingCache || matchingCache.search !== searchHits || matchingCache.text !== textHits) {
        matchingCache = { search: searchHits, text: textHits, rows: new WeakMap(), synthetic: new Int8Array(NA - N).fill(-1) };
    }
    return matchingCache;
}
function isDim(i) {
    if (i >= N) {
        const state = matchingState();
        if (!state) return false;
        const index = i - N;
        if (state.synthetic[index] < 0) state.synthetic[index] = SYN[index].kids.some(child => !isDim(child)) ? 0 : 1;
        return !!state.synthetic[index];
    }
    if (searchHits && !searchHits[sName[i]]) return true;
    if (textHits && !textHits[i]) return true;
    if (range && range.track !== null && range.track !== sTask[i]) return false;
    return false;
}
function denseSummary(row, first, end) {
    const count = end - first, state = matchingState();
    if (!state) return { index: row[first], count, matches: count };
    if (count === 1) return { index: row[first], count, matches: isDim(row[first]) ? 0 : 1 };
    let blocks = state.rows.get(row);
    if (!blocks) state.rows.set(row, blocks = []);
    let representative = first, matches = 0;
    let block = Math.floor(first / DENSE_MATCH_BLOCK);
    for (let start = block * DENSE_MATCH_BLOCK; start < end; start += DENSE_MATCH_BLOCK, block++) {
        let prefix = blocks[block];
        if (!prefix) {
            const length = Math.min(row.length - start, DENSE_MATCH_BLOCK);
            prefix = new Uint16Array(length + 1);
            for (let offset = 0; offset < length; offset++)
                prefix[offset + 1] = prefix[offset] + (isDim(row[start + offset]) ? 0 : 1);
            blocks[block] = prefix;
        }
        let low = Math.max(0, first - start), high = Math.min(end - start, prefix.length - 1);
        const before = prefix[low], localMatches = prefix[high] - before;
        if (!matches && localMatches) {
            while (low < high) {
                const middle = (low + high) >>> 1;
                if (prefix[middle + 1] > before) high = middle;
                else low = middle + 1;
            }
            representative = start + low;
        }
        matches += localMatches;
    }
    return { index: row[representative], count, matches };
}
function lbPixel(row, pixel, start, scale, low = 0) {
    let high = row.length;
    while (low < high) {
        const middle = (low + high) >>> 1;
        if ((sStart[row[middle]] - start) * scale < pixel) low = middle + 1;
        else high = middle;
    }
    return low;
}
function densePixelAt(row, pixel, start, scale) {
    const first = lbPixel(row, pixel, start, scale);
    let end = lbPixel(row, pixel + 1, start, scale, first);
    if (end > first && (sEnd[row[end - 1]] - sStart[row[end - 1]]) * scale >= DENSE_SLICE_WIDTH) end--;
    return end > first ? denseSummary(row, first, end) : null;
}

// ---- detail panel split (drag handle between canvas and panel) ----
function prefGet(k) { try { return localStorage.getItem('fgTl.' + k); } catch (e) { return null; } }
function prefSet(k, v) { try { if (v === null) localStorage.removeItem('fgTl.' + k); else localStorage.setItem('fgTl.' + k, v); return true; } catch (e) { return false; } }
function detailDefault() { return Math.round(Math.min(400, Math.max(180, window.innerHeight * 0.32))); }
let detailFracFallback = null;
let detailWidthFracFallback = null;
// stored as a fraction of the window height, so the split looks the same on other screens
function detailPx() { const v = detailFracFallback ?? +prefGet('detailFrac'); return v > 0 && v < 1 ? Math.round(v * window.innerHeight) : detailDefault(); }
function saveDetailFraction(value, wide) {
    const fallback = prefSet(wide ? 'detailWidthFrac' : 'detailFrac', value) ? null : +value;
    if (wide) detailWidthFracFallback = fallback;
    else detailFracFallback = fallback;
}
let detailExpanded = false;
let detailDragSize = null;
let dividerPreviewPending = false;
const landscapeDetails = window.matchMedia('(any-pointer: coarse) and (min-width: 660px) and (max-height: 480px) and (orientation: landscape)');
function isLandscapeDetail() { return tlRoot.classList.contains('tl-landscape-details'); }
function syncLandscapeDetails() {
    const enabled = landscapeDetails.matches && !detailExpanded && detailSliceIndex !== null &&
        detailSliceIndex < N && elDetail.classList.contains('show');
    if (enabled === isLandscapeDetail()) return false;
    stopDetailDrag?.();
    tlRoot.classList.toggle('tl-landscape-details', enabled);
    if (enabled) elDetail.style.removeProperty('height');
    elResizer.setAttribute('aria-orientation', enabled ? 'vertical' : 'horizontal');
    elResizer.title = `Drag or use ${enabled ? 'Left/Right' : 'Up/Down'} to resize details; Home/End for limits; Enter or double-click to reset`;
    return true;
}
function availableDetailWidth() {
    return Math.max(0, elResizer.parentElement.clientWidth - elResizer.offsetWidth);
}
function detailWidthLimits(available) {
    const minimum = Math.min(280, Math.floor(available / 2));
    return [minimum, Math.max(minimum, available - 320)];
}
function preferredDetailWidth(available) {
    const fraction = detailWidthFracFallback ?? +prefGet('detailWidthFrac');
    return available * (fraction > 0 && fraction < 1 ? fraction : 0.5);
}
function landscapeDetailSplit(available = availableDetailWidth()) {
    const [minimum, maximum] = detailWidthLimits(available);
    const preferred = detailDragSize ?? preferredDetailWidth(available);
    return {minimum, maximum, width: Math.round(Math.max(minimum, Math.min(preferred, maximum)))};
}
function prepareClosedLandscapeDetailSplit() {
    const dividerWidth = parseFloat(getComputedStyle(elResizer).getPropertyValue('--tl-landscape-divider-width'));
    const stageWidth = elStage.offsetWidth;
    return Number.isFinite(dividerWidth) && stageWidth > dividerWidth ? landscapeDetailSplit(stageWidth - dividerWidth) : null;
}
function setDetailSeparatorValue(minimum, maximum, size, wide) {
    elResizer.setAttribute('aria-valuemin', minimum);
    elResizer.setAttribute('aria-valuemax', maximum);
    elResizer.setAttribute('aria-valuenow', size);
    elResizer.setAttribute('aria-valuetext', 'Details ' + size + ' pixels' + (wide ? ' wide' : ''));
}
function maxDetailH(separatorHeight = elResizer.offsetHeight) {
    const notesMinimum = !compactToolbar.matches && !elNotes.classList.contains('hide') ?
        parseFloat(getComputedStyle(elNotes).minHeight) || 0 : 0;
    const controlsHeight = elToolbar.offsetHeight + notesMinimum + (tlRoot.querySelector('.tl-event-only')?.offsetHeight || 0);
    const style = getComputedStyle(tlRoot);
    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    return Math.round(Math.max(60, tlRoot.clientHeight - padding - 160 - controlsHeight - separatorHeight));
}
function clampDetailH(height, maximum = maxDetailH()) {
    return Math.round(Math.max(60, Math.min(height, maximum)));
}
const SHORT_DETAIL_HEIGHT = 140;
function prepareClosedVerticalDetailSplit() {
    const separatorHeight = parseFloat(getComputedStyle(elResizer).height);
    if (!Number.isFinite(separatorHeight)) return null;
    const maximum = maxDetailH(separatorHeight), height = clampDetailH(detailPx(), maximum);
    const style = getComputedStyle(elDetail);
    const borders = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    return {maximum, height, short: height - borders < SHORT_DETAIL_HEIGHT};
}
function updateShortDetail(height = elDetail.clientHeight) {
    const short = height < SHORT_DETAIL_HEIGHT || ((AN.kind === 'lat' || AN.kind === 'sec') && windowPaneNeedsFlow(height));
    const changed = short !== elDetail.classList.contains('tl-detail-short');
    if (changed) elDetail.classList.toggle('tl-detail-short', short);
    if (AN.kind === 'children') positionChildPager(undefined, height);
    if (!changed) return;
    positionCallContext();
    if (AN.kind === 'dist') positionDistPager();
    if (AN.kind === 'errors') positionErrorPagers();
    if (AN.kind === 'tasks') positionTaskPager();
    if (AN.kind === 'range' || AN.kind === 'lat' || AN.kind === 'sec') positionSummaryPager();
    sizeDetailHead();
}

let layoutViewportWidth = 0, layoutViewportHeight = 0;
// ---- main canvas ----
function resize(fromDivider = false) {
    closeMovedCallActions();
    const landscapeChanged = syncLandscapeDetails();
    const ratio = window.devicePixelRatio || 1;
    const stableDivider = fromDivider && !landscapeChanged && ratio === DPR;
    if (stableDivider && isLandscapeDetail()) {
        const {minimum, maximum, width} = landscapeDetailSplit();
        if (width === +elResizer.getAttribute('aria-valuenow') &&
            minimum === +elResizer.getAttribute('aria-valuemin') && maximum === +elResizer.getAttribute('aria-valuemax')) return;
    }
    if (ratio !== DPR) tracksDirty = rulerDirty = true;
    DPR = ratio;
    const top = Math.max(0, tlRoot.getBoundingClientRect().top);
    const fullscreen = tlRoot.classList.contains('tl-fullscreen');
    layoutViewportWidth = innerWidth;
    layoutViewportHeight = innerHeight;
    const minimumHeight = fullscreen ? 0 : detailExpanded ? 60 : landscapeDetails.matches ? 160 : 288;
    tlRoot.style.height = Math.max(minimumHeight, layoutViewportHeight - top - (fullscreen ? 0 : 12)) + 'px';
    const fullscreenJump = fullscreenTraceJump();
    if (!detailExpanded && fullscreenJump) {
        const stageHeight = Math.max(HEAD_H + ROW + 4, Math.min(160, fullscreenChartRoom(elStage)));
        tlRoot.style.setProperty('--tl-stage-min-height', stageHeight + 'px');
    } else if (!fullscreenJump) tlRoot.style.removeProperty('--tl-stage-min-height');
    if (compactToolbar.matches) tlRoot.style.setProperty('--tl-toolbar-height', elToolbar.offsetHeight + 'px');
    fitToolbarOverlays();
    if (detailExpanded) {
        updateShortDetail();
        scheduleDistHistogram();
        return;
    }
    resizeDetailSplit(stableDivider);
}
function resizeDetailSplit(stableDivider = false, preparedSplit = null) {
    if (isLandscapeDetail()) {
        const {minimum, maximum, width} = preparedSplit?.width === undefined ? landscapeDetailSplit() : preparedSplit;
        elDetail.style.setProperty('--tl-detail-width', width + 'px');
        updateShortDetail();
        setDetailSeparatorValue(minimum, maximum, width, true);
    } else if (elDetail.classList.contains('show')) {
        const maximum = preparedSplit?.height === undefined ? maxDetailH() : preparedSplit.maximum;
        const height = preparedSplit?.height ?? clampDetailH(detailDragSize ?? detailPx(), maximum);
        const heightPx = height + 'px';
        if (stableDivider && elDetail.style.height === heightPx &&
            maximum === +elResizer.getAttribute('aria-valuemax') && height === +elResizer.getAttribute('aria-valuenow') &&
            H === elScroll.clientHeight && W === elScroll.clientWidth) return;
        if (elDetail.style.height !== heightPx) elDetail.style.height = heightPx;
        updateShortDetail();
        setDetailSeparatorValue(60, maximum, height, false);
    }
    const previousWidth = W, previousHeight = H;
    H = elScroll.clientHeight;
    fitInitialCpuTrack();
    syncTrackViewportWidth();
    if (W !== previousWidth || H !== previousHeight || paintedTrackScrollTop !== elScroll.scrollTop) drawTracks();
    else if (tracksDirty || rulerDirty) drawCursor();
    scheduleDistHistogram();
}
function syncTrackViewportWidth() {
    const width = elScroll.clientWidth;
    if (!width) return;
    if (width !== W) {
        W = width;
        rulerDirty = true;
        // both canvases are drawn for W = width of the track area without its scrollbar; the ruler
        // is wider in CSS (no scrollbar above it) and would be stretched: x positions drift apart
        elCv.style.width = elHead.style.width = W + 'px';
        hoverTimeWidth = Math.max(1, Math.min(112, Math.floor(W / 2) - 8));
        elHoverTime.style.width = hoverTimeWidth + 'px';
    }
    const badgePixelWidth = Math.max(1, Math.round(hoverTimeWidth * DPR));
    const badgePixelHeight = Math.max(1, Math.round(HOVER_TIME_HEIGHT * DPR));
    if (elHoverTime.width !== badgePixelWidth) { elHoverTime.width = badgePixelWidth; hoverTimeKey = ''; }
    if (elHoverTime.height !== badgePixelHeight) { elHoverTime.height = badgePixelHeight; hoverTimeKey = ''; }
}
function syncCanvasSize(canvas, pixelWidth, pixelHeight) {
    const widthChanged = canvas.width !== pixelWidth, heightChanged = canvas.height !== pixelHeight;
    if (widthChanged) canvas.width = pixelWidth;
    if (heightChanged) canvas.height = pixelHeight;
    return widthChanged || heightChanged;
}
function syncCanvasSizes() {
    const pixelWidth = Math.max(1, Math.round(W * DPR));
    const tracksResized = syncCanvasSize(elCv, pixelWidth, Math.max(1, Math.round(H * DPR)));
    if (tracksResized || trackFontDpr !== DPR) ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    const rulerResized = syncCanvasSize(elHead, pixelWidth, Math.max(1, Math.round(HEAD_H * DPR)));
    if (trackFontDpr !== DPR) {
        ctx.font = FONT;
        charW = ctx.measureText('abcdefghijklmnopqrstuvwxyz_0123').width / 31;
        trackFontDpr = DPR;
    }
    return rulerResized;
}
const visibleTracks = [];
elCv.addEventListener('contextrestored', () => {
    trackFontDpr = null;
    drawTracks();
});
elHead.addEventListener('contextrestored', drawRuler);
let wakePaintPlan = null;
let wakeRulerStatus = null;
let initialTrackViewport = true;
function fitInitialCpuTrack() {
    if (!initialTrackViewport || !H) return;
    initialTrackViewport = false;
    if (!N || !compactToolbar.matches || innerHeight > 480 || innerWidth <= innerHeight) return;
    const cpuTrack = visibleTracks[0];
    if (cpuTrack?.kind !== 9 || cpuTrack.collapsed) return;
    const firstCalls = visibleTracks.find(track => track.rows.length);
    if (!firstCalls) return;
    const viewport = window.visualViewport;
    const bottom = (viewport?.offsetTop ?? 0) + (viewport?.height ?? innerHeight);
    const visibleHeight = Math.min(H, bottom - elScroll.getBoundingClientRect().top);
    if (visibleHeight < firstCalls.y + HDR + ROW) {
        cpuTrack.collapsed = true;
        layout();
    }
}
function layout() {
    wakePaintPlan = null;
    visibleTracks.length = 0;
    let y = 0;
    for (const t of ORDER) {
        t.y = y;
        if (t.hidden) { t.h = 0; continue; }
        t.h = HDR + (t.collapsed ? MINI : (t.maxD + 1 + (t.kvmEvents.length ? 1 : 0)) * ROW) + GAP;
        visibleTracks.push(t);
        y += t.h;
    }
    totalH = y;
    const height = totalH + 'px';
    if (elSpacer.style.getPropertyValue('--tl-track-content-height') !== height) {
        elSpacer.style.setProperty('--tl-track-content-height', height);
    }
    if (H && !detailExpanded) syncTrackViewportWidth();
}
function firstTrackEndingAfter(position) {
    let low = 0, high = visibleTracks.length;
    while (low < high) {
        const middle = (low + high) >>> 1, track = visibleTracks[middle];
        if (track.y + track.h <= position) low = middle + 1;
        else high = middle;
    }
    return low;
}

let rafPending = false, tracksDirty = true, rulerDirty = true;
let paintedTrackScrollTop = -1;
function draw() {
    rulerDirty = true;
    drawTracks();
}
function drawTracks() {
    tracksDirty = true;
    drawCursor();
}
function drawRuler() {
    rulerDirty = true;
    drawCursor();
}
function drawCursor() {
    if (detailExpanded || rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
        try {
            if (detailExpanded || window.fgCurrentView() !== 'timeline') return;
            if (dividerPreviewPending) {
                dividerPreviewPending = false;
                resize(true);
            }
        } finally { rafPending = false; }
        const hover = flushHover();
        if (hover?.changed) tracksDirty = true;
        const updateRangeOverlay = tracksDirty || rulerDirty;
        if (tracksDirty) {
            tracksDirty = false;
            if (syncCanvasSizes()) rulerDirty = true;
            render();
        }
        if (rulerDirty) {
            rulerDirty = false;
            renderHead();
        }
        if (updateRangeOverlay) renderRangeOverlay();
        if (rangePick || !elRangeAnchor.hidden) renderRangeAnchor();
        renderCursor();
        if (hover) renderHoverTip(hover);
    });
}
function renderRangeAnchor() {
    const start = rangePick?.start;
    const offset = start == null ? -1 : Math.round((start - view.s) * W / (view.e - view.s));
    const hidden = offset < 0 || offset + 2 > W;
    if (elRangeAnchor.hidden !== hidden) elRangeAnchor.hidden = hidden;
    if (hidden) return;
    elRangeAnchor.style.left = offset + 'px';
    elRangeAnchor.style.color = P.rangeLine;
}
function renderRangeOverlay() {
    const visible = paintedRange();
    if (elRangeOverlay.hidden !== !visible) elRangeOverlay.hidden = !visible;
    if (!visible) return;
    const scale = W / (view.e - view.s);
    const start = (visible.s - view.s) * scale, end = (visible.e - view.s) * scale;
    const left = Math.max(0, Math.min(start, end));
    const right = Math.min(W, Math.max(start, end));
    const track = visible.track === null ? null : TRACKS[visible.track];
    elRangeOverlay.style.width = W + 'px';
    elRangeOverlay.style.height = H + 'px';
    elRangeOverlay.style.color = P.rangeLine;
    elRangeBand.style.left = left + 'px';
    elRangeBand.style.width = Math.max(0, right - left) + 'px';
    elRangeBand.style.top = (track ? track.y - elScroll.scrollTop : 0) + 'px';
    elRangeBand.style.height = (track ? Math.max(0, track.h - GAP) : H) + 'px';
    elRangeBand.style.background = P.range;
    elRangeStart.style.left = Math.max(-1, Math.min(W, Math.round(start))) + 'px';
    elRangeEnd.style.left = Math.max(-1, Math.min(W, Math.round(end))) + 'px';
}

function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const m = raw / p;
    return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}
function rulerStep(start, end) {
    return Math.max(1, niceStep((end - start) / Math.max(1, W / 110)));
}

const DENSE_SLICE_WIDTH = 1.5;
const DENSE_AGGREGATE = -1, SLICE_DIM_OPACITY = 0.18;
const DENSE_ROW_GROUP_LIMIT = 32768;
const DENSE_ROW_PAINT_LIMIT = 32768;
const WIDE_ROW_CACHE_LIMIT = 1024;
let denseRowView = null;
function prepareDenseRowView(start, end, scale, matching) {
    if (!denseRowView || denseRowView.start !== start || denseRowView.end !== end || denseRowView.scale !== scale) {
        denseRowView = { start, end, scale, rows: new WeakMap(), remaining: DENSE_ROW_GROUP_LIMIT,
            wideRemaining: WIDE_ROW_CACHE_LIMIT, recordPaints: false };
    } else denseRowView.recordPaints = true;
    if (denseRowView.matching !== matching) {
        denseRowView.matching = matching;
        denseRowView.paints = new WeakMap();
        denseRowView.paintRemaining = DENSE_ROW_PAINT_LIMIT;
    }
}
function render() {
    const matching = matchingState();
    ctx.fillStyle = P.bg;
    ctx.fillRect(0, 0, elCv.width / DPR, elCv.height / DPR);
    const vs = view.s, ve = view.e, k = W / (ve - vs), sy = elScroll.scrollTop;
    paintedTrackScrollTop = sy;
    prepareDenseRowView(vs, ve, k, matching);
    // grid
    const step = rulerStep(vs, ve);
    ctx.fillStyle = P.grid;
    for (let t = Math.ceil(vs / step) * step; t < ve; t += step) ctx.fillRect(Math.round((t - vs) * k), 0, 1, H);
    for (let index = firstTrackEndingAfter(sy); index < visibleTracks.length; index++) {
        const t = visibleTracks[index];
        const y = t.y - sy;
        if (y > H) break;
        drawTrack(t, y, vs, ve, k);
    }
    if (ov.wk && WAKES.length) drawWakeups(vs, ve, k, sy);
    else {
        wakePaintPlan = null;
        setWakeRulerStatus(null);
    }
    if (sel >= 0 && !paintedRange()) {
        // selected slice: start and end lines over all tracks (as in Perfetto), the ruler shows its span
        const x0 = (sStart[sel] - vs) * k, x1 = (sEnd[sel] - vs) * k;
        if (x1 >= 0 && x0 <= W) {
            ctx.fillStyle = P.selBand;
            ctx.fillRect(Math.max(0, x0), 0, Math.min(W, x1) - Math.max(0, x0), H);
            ctx.fillStyle = P.selLine;
            if (x0 >= 0) ctx.fillRect(Math.round(x0), 0, 1, H);
            if (x1 <= W && x1 - x0 >= 1) ctx.fillRect(Math.round(x1), 0, 1, H);
            if (x1 - x0 < DENSE_SLICE_WIDTH) drawThinSelection((x0 + x1) / 2, sy);
        }
    }
}
function drawThinSelection(position, scrollTop) {
    const track = TRACKS[sTask[sel]], depth = rowOf(sel);
    if (track.hidden || (track.collapsed && depth !== 0)) return;
    const radius = track.collapsed ? 3 : 5;
    const center = track.y - scrollTop + HDR + (track.collapsed ? MINI / 2 : depth * ROW + (ROW - 1) / 2);
    if (center < -radius || center > H + radius) return;
    ctx.fillStyle = P.bg;
    ctx.strokeStyle = P.sel;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(position, center - radius);
    ctx.lineTo(position + radius, center);
    ctx.lineTo(position, center + radius);
    ctx.lineTo(position - radius, center);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
}
function drawSelSpan(vs, k) {
    // ruler: bracket from start to end of the selected slice with its duration (and position in the overview)
    if (sel < 0 || paintedRange()) return;
    const s = sStart[sel], e = sEnd[sel];
    hctx.fillStyle = P.selTag;
    hctx.fillRect(Math.round(ovX(s)), 0, Math.max(1.5, (e - s) * ovK()), OV_H);
    const x0 = (s - vs) * k, x1 = (e - vs) * k;
    if (x1 < 0 || x0 > W) return;
    const y = OV_H + 2, hh = HEAD_H - OV_H - 3;
    const a = Math.max(0, x0), b = Math.min(W, x1);
    hctx.globalAlpha = 0.14;
    hctx.fillRect(a, y, Math.max(1, b - a), hh);
    hctx.globalAlpha = 1;
    if (x0 >= 0) hctx.fillRect(Math.round(x0), y, 1, hh);
    if (x1 <= W) hctx.fillRect(Math.round(x1), y, 1, hh);
    hctx.fillRect(a, HEAD_H - 3, Math.max(1, b - a), 2);
    const isS = sel >= N;
    const lab = fmtDur(isS ? e - s : durOf(sel));
    hctx.font = FONT_S;
    const tw = hctx.measureText(lab).width + 10;
    if (tw > W) return;
    let lx = (a + b) / 2 - tw / 2;
    if (b - a < tw + 4) lx = x1 + 4 + tw <= W ? x1 + 4 : x0 - 4 - tw;
    lx = Math.max(0, Math.min(W - tw, lx));
    const ly = HEAD_H - 17;
    hctx.fillStyle = P.selTag;
    hctx.beginPath();
    if (hctx.roundRect) hctx.roundRect(lx, ly, tw, 14, 3); else hctx.rect(lx, ly, tw, 14);
    hctx.fill();
    hctx.fillStyle = '#fff';
    hctx.textBaseline = 'middle';
    hctx.fillText(lab, lx + 5, ly + 7.5);
    hctx.textBaseline = 'top';
    return [lx, lx + tw];
}

function drawProcessMarkers(events, top, start, end, scale) {
    for (let index = firstProcessEventAt(events, start); index < events.length;) {
        const time = events[index][1];
        if (time > end) break;
        const position = Math.min(W - 1, Math.max(0, Math.round((time - start) * scale)));
        ctx.fillRect(position, top - 6, 1, 6);
        if (position >= W - 1) break;
        let next = index + 1;
        if (next < events.length && Math.round((events[next][1] - start) * scale) <= position) {
            let high = events.length;
            while (next < high) {
                const middle = (next + high) >>> 1;
                if (Math.round((events[middle][1] - start) * scale) <= position) next = middle + 1;
                else high = middle;
            }
        }
        index = next;
    }
}
function firstViewportRow(top) {
    return Math.max(0, Math.ceil((-top - ROW) / ROW));
}
function drawTrackErrorOrigins(track, top, viewStart, scale) {
    const chains = track.errs, bounds = track.errorIndex, ordered = bounds === true;
    const right = W + 6;
    let first = 0;
    if (ordered) {
        let high = chains.length;
        while (first < high) {
            const middle = (first + high) >>> 1;
            if ((sStart[CHAINS[chains[middle]][0]] - viewStart) * scale < -6) first = middle + 1;
            else high = middle;
        }
    }
    const step = bounds && !ordered ? ERROR_BLOCK_SIZE : chains.length;
    for (let start = first, offset = 0; start < chains.length; start += step, offset += 2) {
        if (bounds && !ordered && ((bounds[offset] - viewStart) * scale > right ||
                (bounds[offset + 1] - viewStart) * scale < -6)) continue;
        const stop = Math.min(start + step, chains.length);
        for (let position = start; position < stop; position++) {
            const chain = CHAINS[chains[position]], index = chain[0];
            const marker = (sStart[index] - viewStart) * scale;
            if (ordered && marker > right) return;
            if (marker < -6 || marker > right || (errFilter && chain[3] !== 1)) continue;
            const row = top + rowOf(index) * ROW;
            if (row < -ROW || row > H || (sEnd[index] - sStart[index]) * scale >= 6) continue;
            ctx.fillStyle = chain[3] === 0 ? P.errOh : P.errO;
            ctx.beginPath(); ctx.moveTo(marker, row - 1); ctx.lineTo(marker + 4.5, row + 7); ctx.lineTo(marker - 4.5, row + 7); ctx.closePath(); ctx.fill();
        }
    }
}
const trackHeaders = new WeakMap();
function measuredTrackHeader(track) {
    let header = trackHeaders.get(track);
    if (!header || header.collapsed !== track.collapsed) {
        const label = (track.collapsed ? '▸ ' : '▾ ') + trackLabel(track);
        header = { label, width: ctx.measureText(label).width, collapsed: track.collapsed, subtitle: null, hits: null };
        trackHeaders.set(track, header);
    }
    const hits = textHits ? track.hits : null;
    if (header.subtitle === null || header.hits !== hits) {
        header.subtitle = trackSub(track);
        header.hits = hits;
    }
    return header;
}
function drawTrackHeader(track, top, start, end, scale) {
    ctx.fillStyle = P.hdr;
    ctx.fillRect(0, top, W, HDR);
    // track-level windows in the header band
    if (ov.rs && track.rs.length) visitSchedulingIntervals(track.rs, track.rsIndex, start, end,
        record => bandRect(record[1], record[2], top + HDR - 4, 3, P.rs, start, scale));
    // irqs-off periods: dark grey like the hatching (red is reserved for errors)
    if (ov.irq && track.io.length) visitSchedulingIntervals(track.io, track.ioIndex, start, end,
        record => bandRect(record[1], record[2], top + HDR - 2, 2, P.io, start, scale));
    ctx.font = FONT_B;
    ctx.fillStyle = P.hdrText;
    ctx.textBaseline = 'middle';
    const {label, width, subtitle} = measuredTrackHeader(track);
    ctx.fillText(label, 8, top + 9.5);
    ctx.font = FONT_S;
    ctx.fillStyle = P.sub;
    ctx.fillText(subtitle, 18 + width, top + 9.5);
    ctx.font = FONT;
    if (ov.mk && PROCESS_BY_TASK.has(track.id)) {
        ctx.fillStyle = P.hdrText;
        drawProcessMarkers(PROCESS_BY_TASK.get(track.id), top + HDR, start, end, scale);
    }
}
function drawTrack(t, y, vs, ve, k) {
    const top = y + HDR;
    if (top > 0) drawTrackHeader(t, y, vs, ve, k);
    else ctx.font = FONT;
    if (t.kind === 9) { if (!t.collapsed) drawCpuTrack(top, vs, ve, k); return; }
    if (t.collapsed) {
        if (t.rows.length) drawRow(t.rows[0], top + 1, MINI - 2, vs, ve, k, false);
        else if (t.kvmEvents.length) drawKvmTrack(t, top + 1, MINI - 2, vs, ve, k);
        return;
    }
    for (let depth = firstViewportRow(top); depth < t.rows.length; depth++) {
        const ry = top + depth * ROW;
        if (ry > H) break;
        drawRow(t.rows[depth], ry, ROW - 1, vs, ve, k, true);
    }
    if (ov.off && t.off.length) drawOffTrack(t, top, vs, ve, k);
    if (t.kvmEvents.length) drawKvmTrack(t, top + (t.maxD + 1) * ROW, ROW - 1, vs, ve, k);
    if (ov.err) {
        // origins are always marked, even when merged into dense blocks
        if (t.errs.length) drawTrackErrorOrigins(t, top, vs, k);
        if (!errFilter && t.userErrors?.length) for (let marker = lbStart(t.userErrors, vs - 6 / k, 0);
            marker < t.userErrors.length && sStart[t.userErrors[marker]] <= ve + 6 / k; marker++) {
            const index = t.userErrors[marker];
            const position = (sStart[index] - vs) * k, row = top + rowOf(index) * ROW;
            if (position < -6 || position > W + 6 || row < -ROW || row > H || (sEnd[index] - sStart[index]) * k >= 6) continue;
            ctx.fillStyle = P.errOh;
            ctx.beginPath(); ctx.moveTo(position, row - 1); ctx.lineTo(position + 4.5, row + 7); ctx.lineTo(position - 4.5, row + 7); ctx.closePath(); ctx.fill();
        }
    }
}
function taskColor(track) {
    const key = 'task' + track.id;
    let c = colorCache.get(key);
    if (!c) {
        let h = 2166136261;
        const s = track.comm;
        for (let q = 0; q < s.length; q++) { h ^= s.charCodeAt(q); h = Math.imul(h, 16777619); }
        h = (h >>> 1) % 360;
        c = hsl(h, 50, dark ? 42 : 70);
        colorCache.set(key, c);
    }
    return c;
}
const cpuHeaders = new Map();
function drawCpuTrack(top, vs, ve, k) {
    for (let row = firstViewportRow(top); row < CPU_LIST.length; row++) {
        const ry = top + row * ROW;
        if (ry > H) break;
        const cpu = CPU_LIST[row];
        const sg = CPU_SEGS.get(cpu);
        let header = cpuHeaders.get(cpu);
        if (!header) {
            const label = 'CPU ' + cpu;
            ctx.font = FONT_S;
            header = { label, width: ctx.measureText(label).width + 10 };
            cpuHeaders.set(cpu, header);
        }
        ctx.font = FONT;
        let lo = 0, hi = sg.e.length;
        while (lo < hi) { const m = (lo + hi) >> 1; if (sg.e[m] > vs) hi = m; else lo = m + 1; }
        let previousTid = -1, track, color, alpha, activeAlpha, activeFill;
        for (let j = lo; j < sg.s.length && sg.s[j] < ve; j++) {
            const x0 = (sg.s[j] - vs) * k, x1 = (sg.e[j] - vs) * k;
            const cx0 = Math.max(x0, -2), w = Math.max(1, Math.min(x1, W + 2) - cx0);
            const tid = sg.t[j];
            if (tid !== previousTid) {
                previousTid = tid;
                track = TRACKS[tid];
                color = taskColor(track);
                alpha = textHits && !track.hits ? 0.25 : 1;
            }
            if (alpha !== activeAlpha) ctx.globalAlpha = activeAlpha = alpha;
            if (color !== activeFill) ctx.fillStyle = activeFill = color;
            ctx.fillRect(cx0, ry, w > 3 ? w - 1 : w, ROW - 1);
            if (!CPU_HAS_SWITCHES && w > 3) {
                ctx.strokeStyle = P.text;
                ctx.lineWidth = 1;
                ctx.setLineDash([3, 3]);
                ctx.strokeRect(cx0 + 0.5, ry + 0.5, w - 1, ROW - 2);
                ctx.setLineDash([]);
            }
            const tx = Math.max(cx0, header.width) + 4, tw = Math.min(x1, W) - tx - 4;
            if (tw > 30) {
                let txt = trackLabel(track) + (CPU_HAS_SWITCHES ? '' : ' · observed');
                const maxc = Math.floor(tw / charW);
                if (txt.length > maxc) txt = txt.slice(0, Math.max(1, maxc - 1)) + '…';
                if (activeFill !== P.text) ctx.fillStyle = activeFill = P.text;
                ctx.textBaseline = 'middle';
                ctx.fillText(txt, tx, ry + ROW / 2);
            }
            if (hovCpu && hovCpu.cpu === cpu && hovCpu.j === j) { ctx.strokeStyle = P.hov; ctx.lineWidth = 1; ctx.strokeRect(cx0 + 0.5, ry + 0.5, w - 1, ROW - 2); }
        }
        if (activeAlpha !== 1) ctx.globalAlpha = 1;
        visitCpuInterrupts(cpu, vs, ve, record => {
            const x0 = (record[0] - vs) * k, x1 = (record[1] - vs) * k;
            ctx.fillStyle = record[2] === 1 ? 'hsl(152,60%,40%)' : record[2] === 2 ? 'hsl(30,85%,50%)' : 'hsl(282,60%,50%)';
            ctx.fillRect(Math.max(0, x0), ry + ROW - 5, Math.max(1.5, x1 - Math.max(0, x0)), 3);
        });
        // CPU label
        ctx.font = FONT_S;
        ctx.fillStyle = P.hdr;
        ctx.globalAlpha = 0.9;
        ctx.fillRect(0, ry, header.width, ROW - 1);
        ctx.globalAlpha = 1;
        ctx.fillStyle = P.sub;
        ctx.textBaseline = 'middle';
        ctx.fillText(header.label, 5, ry + ROW / 2);
        ctx.font = FONT;
    }
}
function trackAnchorY(tid, slice, sy) {
    const t = TRACKS[tid];
    if (!t || t.hidden || t.h === 0) return null;
    if (slice >= 0 && !t.collapsed) return t.y - sy + HDR + rowOf(slice) * ROW + ROW / 2;
    return t.y - sy + HDR / 2;
}
const WAKE_MAX = 15;
const WAKE_CURVE_LIMIT = 400;
const WAKE_BUCKET_WIDTH = 4;
const WAKE_PAINT_CACHE_BYTES = 256 * 1024;
function wakeHead(x, y, direction) {
    ctx.moveTo(x, y); ctx.lineTo(x - 3, y - 4 * direction); ctx.lineTo(x + 3, y - 4 * direction); ctx.closePath();
}
function wakeCurve(off, startX, sourceY, endX, targetY, color) {
    const hot = hovOff && off === hovOff;
    ctx.fillStyle = ctx.strokeStyle = hot ? '#0b7a6b' : color;
    ctx.lineWidth = hot ? 2 : 1.3;
    ctx.beginPath(); ctx.arc(startX, sourceY, 2.5, 0, 6.3); ctx.fill();
    ctx.beginPath(); ctx.moveTo(startX, sourceY);
    const midpoint = (startX + endX) / 2;
    ctx.bezierCurveTo(midpoint, sourceY, midpoint, targetY, endX, targetY);
    ctx.stroke();
    const direction = targetY >= sourceY ? 1 : -1;
    ctx.beginPath(); ctx.moveTo(endX, targetY); ctx.lineTo(endX - 5, targetY - 4 * direction);
    ctx.lineTo(endX + 1, targetY - 6 * direction); ctx.closePath(); ctx.fill();
}
function prepareWakePaint(vs, ve, k, sy, focus, priorityWake) {
    const small = [], curves = [], heads = new Map();
    const columns = Math.ceil(W / WAKE_BUCKET_WIDTH);
    let count = 0, priority = null, limited = false;
    function bucket(sourceY, endX, targetY) {
        if (!columns || endX < -3 || endX > W + 3 || targetY < -4 || targetY > H + 4) return;
        let row = heads.get(targetY);
        if (!row) heads.set(targetY, row = new Uint8Array(columns));
        const column = Math.max(0, Math.min(columns - 1, Math.floor(endX / WAKE_BUCKET_WIDTH)));
        row[column] |= targetY >= sourceY ? 1 : 2;
    }
    function dense(wake, off, startX, sourceY, endX, targetY) {
        if (wake === priorityWake) {
            priority = [off, startX, sourceY, endX, targetY];
            if (curves.length === WAKE_CURVE_LIMIT) {
                const dropped = curves.pop();
                bucket(dropped[2], dropped[3], dropped[4]);
                limited = true;
            }
        } else if (focus.has(wake[0]) || focus.has(wake[2])) {
            if (curves.length < WAKE_CURVE_LIMIT - (priority ? 1 : 0)) {
                curves.push([off, startX, sourceY, endX, targetY]);
            } else {
                bucket(sourceY, endX, targetY);
                limited = true;
            }
        } else {
            bucket(sourceY, endX, targetY);
        }
    }
    for (let block = 0, start = 0; block < WAKE_BOUNDS.length; block += 2, start += WAKE_BLOCK_SIZE) {
        if (WAKE_BOUNDS[block] > ve || WAKE_BOUNDS[block + 1] < vs) continue;
        const firstTrack = WAKE_TRACK_BOUNDS[block];
        if (firstTrack >= 0) {
            const lastTrack = ORDER[WAKE_TRACK_BOUNDS[block + 1]];
            if (ORDER[firstTrack].y > sy + H || lastTrack.y + lastTrack.h < sy) continue;
        }
        const stop = Math.min(start + WAKE_BLOCK_SIZE, WAKES.length);
        for (let index = start; index < stop; index++) {
            const wake = WAKES[index], off = wakeOff(wake);
            const resume = off ? off[2] : wake[1];
            if (resume < vs || wake[1] > ve) continue;
            const y0 = trackAnchorY(wake[0], wake[4], sy);
            const target = TRACKS[wake[2]];
            if (y0 === null || !target || target.hidden || target.h === 0) continue;
            const y1 = off && !target.collapsed ? target.y - sy + HDR + offRow(off) * ROW + ROW / 2 : target.y - sy + HDR / 2;
            if ((y0 < 0 && y1 < 0) || (y0 > H && y1 > H)) continue;
            const startX = (wake[1] - vs) * k, endX = (resume - vs) * k;
            count++;
            if (count <= WAKE_MAX) small.push([wake, off, startX, y0, endX, y1]);
            else {
                if (count === WAKE_MAX + 1) {
                    for (const entry of small) dense(...entry);
                    small.length = 0;
                }
                dense(wake, off, startX, y0, endX, y1);
            }
        }
    }
    let crowdedCount = count > WAKE_MAX ? count : 0;
    for (const entry of small) {
        const endX = entry[4];
        const crowded = small.some(other => other !== entry && other[4] >= endX - 12 && other[4] <= endX + 12);
        entry.push(crowded);
        if (crowded) crowdedCount++;
    }
    const status = crowdedCount ? {
        summary: crowdedCount + ' of ' + count + ' wakeups bunched',
        hint: limited ? ' · ' + WAKE_CURVE_LIMIT + ' full arrows, zoom in for more' : focus.size ?
            ' · arrows for the focused task' : ' · hover or select a task to see its arrows, zoom in for all'
    } : null;
    return { count, small, curves, heads, priority, status };
}
function sameWakeFocus(previous, next) {
    if (previous.size !== next.size) return false;
    for (const task of next) if (!previous.has(task)) return false;
    return true;
}
function setWakeRulerStatus(status) {
    if (status?.summary === wakeRulerStatus?.summary && status?.hint === wakeRulerStatus?.hint) return;
    wakeRulerStatus = status;
    rulerDirty = true;
}
function drawWakeups(vs, ve, k, sy) {
    const focus = new Set();
    if (sel >= 0) focus.add(sTask[sel]);
    if (hov >= 0) focus.add(sTask[hov]);
    if (hovOff) focus.add(hovOff[0]);
    const visible = paintedRange();
    if (visible && visible.track !== null) focus.add(visible.track);
    const priorityWake = hovOff && WAKE_OF_OFF.get(OFF_IDX.get(hovOff));
    let plan = wakePaintPlan;
    if (!plan || plan.start !== vs || plan.end !== ve || plan.scale !== k || plan.top !== sy ||
            plan.width !== W || plan.height !== H || plan.inline !== inlineView || plan.priorityWake !== priorityWake ||
            !sameWakeFocus(plan.focus, focus)) {
        const paint = prepareWakePaint(vs, ve, k, sy, focus, priorityWake);
        plan = { start: vs, end: ve, scale: k, top: sy, width: W, height: H, inline: inlineView, focus, priorityWake, paint };
        wakePaintPlan = paint.heads.size * Math.ceil(W / WAKE_BUCKET_WIDTH) <= WAKE_PAINT_CACHE_BYTES ? plan : null;
    }
    const { count, small, curves, heads, priority, status } = plan.paint;
    setWakeRulerStatus(status);
    if (!count) return;
    const col = dark ? 'rgba(52,211,190,.75)' : 'rgba(15,157,138,.7)';
    if (count > WAKE_MAX) {
        if (heads.size) {
            ctx.fillStyle = col;
            ctx.globalAlpha = 0.55;
            ctx.beginPath();
            for (const [targetY, row] of heads) for (let column = 0; column < row.length; column++) {
                const flags = row[column];
                if (!flags) continue;
                const endX = Math.min(W, (column + 0.5) * WAKE_BUCKET_WIDTH);
                if (flags & 1) wakeHead(endX, targetY, 1);
                if (flags & 2) wakeHead(endX, targetY, -1);
            }
            ctx.fill();
            ctx.globalAlpha = 1;
        }
        for (const curve of curves) wakeCurve(...curve, col);
        if (priority) wakeCurve(...priority, col);
    } else {
        for (const [wake, off, startX, sourceY, endX, targetY, crowded] of small) {
            if (!crowded || wake === priorityWake || (hovOff && off === hovOff) || focus.has(wake[0]) || focus.has(wake[2])) {
                wakeCurve(off, startX, sourceY, endX, targetY, col);
            } else {
                ctx.fillStyle = col;
                ctx.globalAlpha = 0.55;
                ctx.beginPath(); wakeHead(endX, targetY, targetY >= sourceY ? 1 : -1); ctx.fill();
                ctx.globalAlpha = 1;
            }
        }
    }
}
function bandRect(s, e, y, h, color, vs, k) {
    const x0 = (s - vs) * k, x1 = (e - vs) * k;
    if (x1 < 0 || x0 > W) return;
    ctx.fillStyle = color;
    ctx.fillRect(Math.max(0, x0), y, Math.max(1.5, Math.min(W, x1) - Math.max(0, x0)), h);
}

function drawDenseRun(index, left, right, dim, top, height, paint) {
    const aggregate = index === DENSE_AGGREGATE;
    const opacity = aggregate ? (dim ? 0.2 : P.dense) : (dim ? SLICE_DIM_OPACITY : 1);
    if (opacity !== paint.opacity) ctx.globalAlpha = paint.opacity = opacity;
    const fill = aggregate ? P.denseFill : sliceStyle(index)[0];
    if (fill !== paint.fill) ctx.fillStyle = paint.fill = fill;
    ctx.fillRect(left, top, Math.max(1, right - left), height);
}
function drawRow(row, ry, h, vs, ve, k, labels) {
    const count = row.length;
    if (!count) return;
    const paint = { opacity: 1, fill: null };
    const savedPaints = denseRowView.paints.get(row);
    if (savedPaints) {
        for (let offset = 0; offset < savedPaints.length; offset += 4) {
            const index = savedPaints[offset], left = savedPaints[offset + 1], right = savedPaints[offset + 2], dim = savedPaints[offset + 3];
            if (dim === null) {
                drawSlice(index, left, right, ry, h, labels, paint.opacity);
                paint.opacity = 1;
                paint.fill = null;
            } else drawDenseRun(index, left, right, dim, ry, h, paint);
        }
        if (paint.opacity !== 1) ctx.globalAlpha = 1;
        return;
    }
    const filtered = !!(searchHits || textHits);
    const cached = denseRowView.rows.get(row);
    const first = cached?.first ?? Math.min(lbEnd(row, vs), lbStart(row, vs, 0));
    if (first >= count || sStart[row[first]] > ve) return;
    const cachedEnds = cached?.ends;
    const capacity = cached ? 0 : denseRowView.remaining;
    let j = first, group = 0, recordedEnds = null;
    let denseLeft = null, denseRight = 0, denseDim = false;
    let paints = denseRowView.recordPaints && denseRowView.paintRemaining && (!cached || cachedEnds) ? [] : null;
    let hasDenseRun = false;
    const keepPaint = (index, left, right, dim) => {
        if (!paints) return;
        if (paints.length >= denseRowView.paintRemaining * 4) paints = null;
        else paints.push(index, left, right, dim);
    };
    const flush = () => {
        if (denseLeft === null) return;
        drawDenseRun(DENSE_AGGREGATE, denseLeft, denseRight, denseDim, ry, h, paint);
        keepPaint(DENSE_AGGREGATE, denseLeft, denseRight, denseDim);
        denseLeft = null;
    };
    while (j < count) {
        const i = row[j];
        const s = sStart[i];
        if (s > ve) break;
        const x0 = (s - vs) * k, x1 = (sEnd[i] - vs) * k;
        if ((sEnd[i] - s) * k < DENSE_SLICE_WIDTH) {
            const px = Math.floor(x0);
            // skip the other thin slices that start in this pixel. Slices of a row do not overlap,
            // so only the last one starting here can be wide: never skip that one.
            let jj;
            if (cachedEnds && group < cachedEnds.length) jj = cachedEnds[group++];
            else {
                jj = lbPixel(row, px + 1, vs, k, j + 1);
                if (jj - 1 > j && (sEnd[row[jj - 1]] - sStart[row[jj - 1]]) * k >= DENSE_SLICE_WIDTH) jj--;
                if (capacity && (recordedEnds?.length || 0) < capacity) (recordedEnds ||= []).push(jj);
            }
            const dim = filtered && !denseSummary(row, j, jj).matches;
            hasDenseRun = true;
            if (jj - j === 1) {
                flush();
                drawDenseRun(i, px, px + 1, dim, ry, h, paint);
                keepPaint(i, px, px + 1, dim);
            } else if (denseLeft !== null && px === denseRight && denseDim === dim) denseRight = px + 1;
            else { flush(); denseLeft = px; denseRight = px + 1; denseDim = dim; }
            j = jj > j ? jj : j + 1;
            continue;
        }
        flush();
        drawSlice(i, x0, x1, ry, h, labels, paint.opacity);
        paint.opacity = 1;
        paint.fill = null;
        keepPaint(i, x0, x1, null);
        j++;
    }
    flush();
    if (paint.opacity !== 1) ctx.globalAlpha = 1;
    if (recordedEnds) {
        denseRowView.rows.set(row, { first, ends: recordedEnds });
        denseRowView.remaining -= recordedEnds.length;
    } else if (!cached && !hasDenseRun && denseRowView.wideRemaining) {
        denseRowView.rows.set(row, { first, ends: null });
        denseRowView.wideRemaining--;
    }
    if (paints && hasDenseRun) {
        denseRowView.paints.set(row, paints);
        denseRowView.paintRemaining -= paints.length / 4;
    }
}

function drawSlice(i, x0, x1, y, h, labels, previousOpacity) {
    const cx0 = Math.max(x0, -2), cx1 = Math.min(x1, W + 2);
    const w = cx1 - cx0;
    const ww = w > 3 ? w - 1 : w;
    const st = sliceStyle(i);
    const dim = isDim(i);
    const f = sFlags[i];
    const inlineFrame = i >= N && !isGhost(i);
    const opacity = dim ? SLICE_DIM_OPACITY : 1;
    if (opacity !== previousOpacity) ctx.globalAlpha = opacity;
    ctx.fillStyle = st[0];
    ctx.fillRect(cx0, y, ww, h);
    if (f & FL.BOUNDARY) {
        ctx.fillStyle = P.axis;
        ctx.fillRect(cx0, Math.round(y * DPR) / DPR, w, Math.max(1, Math.round(DPR)) / DPR);
    }
    if (ov.irq && (f & FL.IRQOFF) && w > 2) { ctx.fillStyle = hatch; ctx.fillRect(cx0, y, ww, h); }
    if (ov.preempt && w > 2) {
        const p = preemptOff(f);   // above the tracer's own level
        if (p) { ctx.fillStyle = dark ? 'rgba(255,255,255,' + (0.1 + 0.08 * p) + ')' : 'rgba(0,0,0,' + (0.08 + 0.07 * p) + ')'; ctx.fillRect(cx0, y + h - 3, ww, 3); }
    }
    if (inlineFrame) {
        if (w > 3 && h > 4) {
            ctx.strokeStyle = st[2]; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
            ctx.strokeRect(cx0 + 0.5, y + 0.5, ww - 1, h - 1);
            ctx.setLineDash([]);
        }
    } else if (i >= N) {
        ctx.strokeStyle = st[2]; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
        ctx.strokeRect(cx0 + 0.5, y + 0.5, ww - 1, h - 1);
        ctx.setLineDash([]);
    } else if (st[2]) { ctx.strokeStyle = st[2]; ctx.lineWidth = 1.5; ctx.strokeRect(cx0 + 0.75, y + 0.75, ww - 1.5, h - 1.5); }
    if (ov.mk && w > 5) {
        if (f & FL.TRUNC) { ctx.fillStyle = P.sub; for (let yy = y; yy < y + h; yy += 4) ctx.fillRect(cx0 + ww - 2, yy, 2, 2); }
        if (f & FL.ORPHAN) { ctx.fillStyle = P.sub; for (let yy = y; yy < y + h; yy += 4) ctx.fillRect(cx0, yy, 2, 2); }
        if (f & FL.MISMATCH) {
            if (tailJumpOf(i)) {
                if (w > 8) {
                    ctx.strokeStyle = P.sub; ctx.lineWidth = 1; ctx.beginPath();
                    ctx.moveTo(cx0 + 1, y + 3.5); ctx.lineTo(cx0 + 6, y + 3.5);
                    ctx.moveTo(cx0 + 4, y + 1.5); ctx.lineTo(cx0 + 6, y + 3.5); ctx.lineTo(cx0 + 4, y + 5.5); ctx.stroke();
                }
            } else { ctx.fillStyle = P.mis; ctx.fillRect(cx0, y, 3, 3); }
        }
        if (ATOMIC.has(i)) { ctx.strokeStyle = P.errLine; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx0, y + h - 1); ctx.lineTo(cx0 + ww, y + h - 1); ctx.stroke(); }
    }
    if (ov.rs && w > 6 && ((f >> FL.RS_SHIFT) & 7)) {
        ctx.fillStyle = P.rs;
        ctx.beginPath(); ctx.moveTo(cx0 + ww - 6, y); ctx.lineTo(cx0 + ww, y); ctx.lineTo(cx0 + ww, y + 6); ctx.closePath(); ctx.fill();
    }
    if (labels && w > 26) {
        const nm = sliceLabel(i);
        let txt = i >= N && isGhost(i) ? (nm === '?' ? 'untraced frame' : 'untraced ' + nm) : nm;
        if (ov.err && eRole[i] === 0 && (showMinor || !CHAINS[eChain[i]][6])) { const en = errName(eErr[i]); txt += ' = ' + (en || eErr[i]); }
        const labelInset = inlineFrame && w - 8 >= 24 + 6 * charW ? 24 : 0;
        ctx.textBaseline = 'middle';
        if (labelInset) {
            ctx.font = FONT_INLINE; ctx.fillStyle = P.inlineTag;
            ctx.fillText('inl', Math.max(cx0, 0) + 4, y + h / 2 + 0.5);
            ctx.font = FONT;
        }
        const maxc = Math.floor((w - 8 - labelInset) / charW);
        if (maxc >= 3) {
            if (txt.length > maxc) txt = txt.slice(0, maxc - 1) + '…';
            ctx.fillStyle = st[1];
            ctx.fillText(txt, Math.max(cx0, 0) + 4 + labelInset, y + h / 2 + 0.5);
        }
    }
    if (dim) ctx.globalAlpha = 1;
    if (i === sel) { ctx.strokeStyle = P.sel; ctx.lineWidth = 2; ctx.strokeRect(cx0 + 1, y + 1, Math.max(1, ww - 2), h - 2); }
    else if (i === hov) { ctx.strokeStyle = P.hov; ctx.lineWidth = 1; ctx.strokeRect(cx0 + 0.5, y + 0.5, Math.max(1, ww - 1), h - 1); }
}

function drawOff(r, y, vs, k) {
    const x0 = (r[1] - vs) * k, x1 = (r[2] - vs) * k;
    if (x1 < 0 || x0 > W || y > H || y + ROW < 0) return;
    const cx0 = Math.max(x0, -2), cx1 = Math.min(x1, W + 2), w = Math.max(1, cx1 - cx0);
    ctx.fillStyle = r[8] ? P.offPre : P.off;
    ctx.fillRect(cx0, y, w, ROW - 1);
    ctx.fillStyle = offHatch;
    ctx.fillRect(cx0, y, w, ROW - 1);
    ctx.fillStyle = P.offLine;
    ctx.fillRect(cx0, y + ROW - 2, w, 1);
    if (w > 40) {
        let txt = (r[8] ? 'preempted ' : 'off-CPU ') + fmtDur(r[2] - r[1]);
        if (r[4] >= 0) txt += ' · ' + S[r[4]];
        if (r[5] >= 0) txt += ' [' + S[r[5]] + ']';
        const maxc = Math.floor((w - 8) / charW);
        if (txt.length > maxc) txt = txt.slice(0, Math.max(1, maxc - 1)) + '…';
        ctx.fillStyle = P.sub;
        ctx.textBaseline = 'middle';
        ctx.fillText(txt, Math.max(cx0, 0) + 4, y + ROW / 2);
    }
    if (hovOff === r) { ctx.strokeStyle = P.hov; ctx.lineWidth = 1; ctx.strokeRect(cx0 + 0.5, y + 0.5, w - 1, ROW - 2); }
}

const DENSE_OFF_LIMIT = 4;
function groupsDenseOff(track) {
    return track.offIndex === true && track.off.length >= 1024;
}
function offStartPixel(record, start, scale) {
    const position = (record[1] - start) * scale;
    return position >= 0 && position < W && (record[2] - start) * scale - position <= 1 ? Math.floor(position) : -1;
}
function drawDenseOff(pixel, top, mask, hovered) {
    ctx.fillStyle = mask === 2 ? P.offPre : P.off;
    ctx.fillRect(pixel, top, 1, ROW - 1);
    ctx.fillStyle = offHatch;
    ctx.fillRect(pixel, top, 1, ROW - 1);
    if (mask === 3) {
        ctx.fillStyle = P.offPre;
        ctx.fillRect(pixel, top + ROW - 6, 1, 4);
    }
    ctx.fillStyle = P.offLine;
    ctx.fillRect(pixel, top + ROW - 2, 1, 1);
    if (hovered) {
        ctx.strokeStyle = P.hov;
        ctx.lineWidth = 1;
        ctx.strokeRect(pixel - 0.5, top + 0.5, 2, ROW - 2);
    }
}
function drawOffTrack(track, top, start, end, scale) {
    const first = firstViewportRow(top), last = Math.floor((H - top) / ROW);
    if (!groupsDenseOff(track)) {
        visitOffIntervals(track, start, end, first, last,
            (record, depth) => drawOff(record, top + depth * ROW, start, scale));
        return;
    }
    const groups = [];
    const flush = (group, rowTop) => {
        if (!group.count) return;
        if (group.count >= DENSE_OFF_LIMIT) drawDenseOff(group.pixel, rowTop, group.mask, group.hovered);
        else for (let index = 0; index < group.count; index++) drawOff(group.first[index], rowTop, start, scale);
        group.count = group.mask = 0;
        group.hovered = false;
    };
    visitOffIntervals(track, start, end, first, last, (record, depth) => {
        const offset = depth - first, pixel = offStartPixel(record, start, scale);
        let group = groups[offset];
        if (group && group.pixel !== pixel) flush(group, top + depth * ROW);
        if (pixel < 0) { drawOff(record, top + depth * ROW, start, scale); return; }
        if (!group) groups[offset] = group = {first: [], pixel, count: 0, mask: 0, hovered: false};
        group.pixel = pixel;
        if (group.count < DENSE_OFF_LIMIT - 1) group.first[group.count] = record;
        group.count++;
        group.mask |= record[8] ? 2 : 1;
        if (record === hovOff) group.hovered = true;
    });
    groups.forEach((group, offset) => flush(group, top + (first + offset) * ROW));
}

// ---- header: overview + axis ----
let ovWidth = -1, ovDens = null, ovMax = 1;
let rulerBackdrop = null, rulerBackdropBasis = null, rulerAxisBasis = null;
function getRulerBackdrop() {
    const width = elHead.width, height = elHead.height;
    const errors = ov.err && CHAINS.length > 0, filter = errors && errFilter, minor = errors && showMinor;
    const previous = rulerBackdropBasis;
    if (previous && previous.width === width && previous.height === height && previous.ratio === DPR &&
            previous.palette === P && previous.density === ovDens && previous.errors === errors &&
            previous.filter === filter && previous.minor === minor) return rulerBackdrop;
    if (!rulerBackdrop) {
        rulerBackdrop = document.createElement('canvas');
        rulerBackdrop.addEventListener('contextrestored', () => {
            rulerBackdropBasis = null;
            drawRuler();
        });
    }
    const layer = rulerBackdrop;
    if (layer.width !== width) layer.width = width;
    if (layer.height !== height) layer.height = height;
    const backdrop = layer.getContext('2d');
    backdrop.setTransform(1, 0, 0, 1, 0, 0);
    backdrop.fillStyle = P.hdr;
    backdrop.fillRect(0, 0, width, height);
    backdrop.setTransform(DPR, 0, 0, DPR, 0, 0);
    backdrop.fillStyle = P.ovBar;
    backdrop.globalAlpha = 0.55;
    for (let pixel = 0; pixel < ovDens.length; pixel++) {
        if (!ovDens[pixel]) continue;
        const barHeight = Math.max(1, (OV_H - 6) * ovDens[pixel] / ovMax);
        backdrop.fillRect(pixel, 3 + OV_H - 6 - barHeight, 1, barHeight);
    }
    backdrop.globalAlpha = 1;
    if (errors) for (const chain of CHAINS) {
        if ((filter && chain[3] !== 1) || (chain[6] && !minor)) continue;
        backdrop.fillStyle = chain[3] === 0 ? P.errOh : P.errO;
        backdrop.fillRect(Math.round(ovX(sStart[chain[0]])), 1, 1.5, chain[3] === 1 ? OV_H - 4 : 8);
    }
    rulerBackdropBasis = { width, height, ratio: DPR, palette: P, density: ovDens, errors, filter, minor };
    rulerAxisBasis = null;
    return layer;
}
function addOverviewSpan(density, left, right) {
    const first = Math.floor(left), last = Math.min(density.length - 1, Math.floor(right));
    if (!(first <= last)) return;
    if (first === last) {
        density[first] += Math.min(right, first + 1) - left;
        return;
    }
    density[first] += first + 1 - left;
    for (let pixel = first + 1; pixel < last; pixel++) density[pixel] += 1;
    density[last] += Math.min(right, last + 1) - last;
}
function buildOverview() {
    ovDens = new Float32Array(Math.max(1, Math.ceil(W)));
    const hasTasks = ORDER.some(track => track.kind === 0);
    for (const t of ORDER) {
        if (t.kind !== 0 && hasTasks) continue;
        if (t.kvm.length) for (const index of t.kvm) {
            const interval = KVM_INTERVALS[index];
            addOverviewSpan(ovDens, Math.max(0, ovX(interval[1])), ovX(interval[2]));
        }
        if (!t.rowsT.length) continue;
        for (const index of t.rowsT[0]) addOverviewSpan(ovDens, Math.max(0, ovX(sStart[index])), ovX(sEnd[index]));
    }
    ovMax = 0;
    for (let p = 0; p < ovDens.length; p++) if (ovDens[p] > ovMax) ovMax = ovDens[p];
    ovMax = ovMax || 1;
    ovWidth = W;
}
function rulerTimeFormat(magnitude, interval) {
    const [unit, suffix] = MODE === 'count' ? [1000, ' u'] : magnitude >= 1e9 ? [1e9, ' s'] :
        magnitude >= 1e6 ? [1e6, ' ms'] : magnitude >= 1e3 ? [1e3, ' us'] : [1, ' ns'];
    const fraction = Math.max(0, Math.ceil(Math.log10(unit / Math.max(1, interval))));
    return value => {
        const text = (value / unit).toFixed(fraction);
        return (text.startsWith('-') && Number(text) === 0 ? text.slice(1) : text) + suffix;
    };
}
function drawRulerStatus(duration, selection) {
    const span = 'span ' + fmtDur(duration);
    const visible = paintedRange();
    const summary = (visible ? 'selected ' + fmtDur(visible.e - visible.s) + '   ' : '') + span;
    const extra = MODE === 'abs' ? '   start ' + fmtAbs(view.s) + ' s' :
        MODE === 'compressed' ? '   compressed time (no timestamps)' : MODE === 'count' ? '   call order (no timing)' : '';
    const edge = 8, row = OV_H + 18;
    const rightStart = selection ? selection[1] + edge : edge;
    let rightEnd = W - edge, leftEnd = selection ? selection[0] - edge : rightEnd;
    const candidates = extra ? [summary + extra, summary] : [summary];
    if (visible) candidates.push(span);
    const wakeWidth = wakeRulerStatus ? hctx.measureText(wakeRulerStatus.summary).width : 0;
    const leftWidth = selection ? leftEnd - edge : 0;
    const rightWidth = rightEnd - rightStart;
    const fitsBeside = (remaining, opposite) => !wakeWidth || wakeWidth <= opposite || wakeWidth + edge <= remaining;
    let placed = null, fallback = null;
    for (const text of candidates) {
        const width = hctx.measureText(text).width;
        if (width <= rightWidth) {
            const entry = { text, width, right: true };
            fallback ??= entry;
            if (fitsBeside(rightWidth - width, leftWidth)) { placed = entry; break; }
        }
        if (selection && width <= leftWidth) {
            const entry = { text, width, right: false };
            fallback ??= entry;
            if (fitsBeside(leftWidth - width, rightWidth)) { placed = entry; break; }
        }
    }
    placed ??= fallback;
    hctx.fillStyle = P.sub;
    hctx.textAlign = 'right';
    if (placed) {
        if (placed.right) {
            hctx.fillText(placed.text, rightEnd, row);
            rightEnd -= placed.width + edge;
            if (!selection) leftEnd = rightEnd;
        } else {
            hctx.fillText(placed.text, leftEnd, row);
            leftEnd -= placed.width + edge;
        }
    }
    hctx.textAlign = 'left';
    if (!wakeRulerStatus) return;
    const wakeSummary = wakeRulerStatus.summary;
    hctx.fillStyle = P.axis;
    for (const text of [wakeSummary + wakeRulerStatus.hint, wakeSummary]) {
        const width = text === wakeSummary ? wakeWidth : hctx.measureText(text).width;
        if (width <= leftEnd - edge) { hctx.fillText(text, edge, row); break; }
        if (selection && width <= rightEnd - rightStart) { hctx.fillText(text, rightStart, row); break; }
    }
}
let formatHoverTime = fmtDur;
function paintRulerAxis(layer, start, end, scale) {
    if (rulerAxisBasis?.start === start && rulerAxisBasis.end === end) return;
    rulerAxisBasis = {start, end};
    const backdrop = layer.getContext('2d');
    const step = rulerStep(start, end);
    const magnitude = Math.max(Math.abs(start), Math.abs(end));
    const formatTick = rulerTimeFormat(magnitude, step);
    formatHoverTime = rulerTimeFormat(magnitude, 1 / scale);
    const axisTop = Math.ceil(OV_H * DPR);
    backdrop.setTransform(1, 0, 0, 1, 0, 0);
    backdrop.fillStyle = P.hdr;
    backdrop.fillRect(0, axisTop, layer.width, layer.height - axisTop);
    backdrop.setTransform(DPR, 0, 0, DPR, 0, 0);
    backdrop.font = FONT_S;
    backdrop.textBaseline = 'top';
    backdrop.fillStyle = P.axis;
    let nextLabel = 4;
    for (let time = Math.ceil(start / step) * step; time < end; time += step) {
        const horizontal = Math.round((time - start) * scale);
        backdrop.fillRect(horizontal, OV_H + 2, 1, 6);
        const label = formatTick(time);
        const width = Math.ceil(backdrop.measureText(label).width);
        const left = Math.max(4, Math.min(Math.round(horizontal - width / 2), W - width - 4));
        if (left < nextLabel || left + width > W - 4) continue;
        backdrop.fillText(label, left, OV_H + 6);
        nextLabel = left + width + 6;
    }
}
function renderHead() {
    if (ovWidth !== W) buildOverview();
    const start = view.s, end = view.e, scale = W / (end - start);
    const layer = getRulerBackdrop();
    paintRulerAxis(layer, start, end, scale);
    hctx.setTransform(1, 0, 0, 1, 0, 0);
    hctx.drawImage(layer, 0, 0);
    hctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    const wx0 = Math.max(0, ovX(view.s)), wx1 = Math.max(wx0 + 2, ovX(view.e));
    hctx.fillStyle = P.ovMask;
    hctx.fillRect(0, 0, wx0, OV_H);
    hctx.fillRect(wx1, 0, W - wx1, OV_H);
    hctx.strokeStyle = P.rangeLine;
    hctx.lineWidth = 1;
    hctx.strokeRect(wx0 + 0.5, 0.5, wx1 - wx0 - 1, OV_H - 1);
    hctx.font = FONT_S;
    hctx.textBaseline = 'top';
    drawRulerStatus(end - start, drawSelSpan(start, scale));
}
function renderCursor() {
    const visible = cursorX >= 0 && cursorX < W && H > 0;
    if (elHoverCursor.hidden === visible) elHoverCursor.hidden = !visible;
    if (!visible) return;
    const position = 'translateX(' + Math.min(W - 1, Math.round(cursorX)) + 'px)';
    if (elHoverCursor.style.transform !== position) elHoverCursor.style.transform = position;
    const side = cursorX < W / 2 ? 'right' : 'left';
    if (elHoverCursor.dataset.side !== side) elHoverCursor.dataset.side = side;
    const time = formatHoverTime(view.s + cursorX / W * (view.e - view.s));
    const key = [time, side, hoverTimeWidth, DPR, dark].join('|');
    if (hoverTimeKey === key) return;
    hoverTimeKey = key;
    hoverTimeCtx.setTransform(elHoverTime.width / hoverTimeWidth, 0, 0, elHoverTime.height / HOVER_TIME_HEIGHT, 0, 0);
    hoverTimeCtx.clearRect(0, 0, hoverTimeWidth, HOVER_TIME_HEIGHT);
    if (hoverTimeWidth <= 12) return;
    hoverTimeCtx.font = FONT_S;
    const width = Math.min(hoverTimeWidth, Math.ceil(hoverTimeCtx.measureText(time).width) + 12);
    const left = side === 'right' ? 0 : hoverTimeWidth - width;
    hoverTimeCtx.beginPath();
    if (hoverTimeCtx.roundRect) hoverTimeCtx.roundRect(left + 0.5, 0.5, width - 1, HOVER_TIME_HEIGHT - 1, 4);
    else hoverTimeCtx.rect(left + 0.5, 0.5, width - 1, HOVER_TIME_HEIGHT - 1);
    hoverTimeCtx.fillStyle = P.hdr;
    hoverTimeCtx.fill();
    hoverTimeCtx.strokeStyle = P.sub;
    hoverTimeCtx.stroke();
    hoverTimeCtx.fillStyle = P.text;
    hoverTimeCtx.textBaseline = 'middle';
    hoverTimeCtx.fillText(time, left + 6, HOVER_TIME_HEIGHT / 2, width - 12);
}
