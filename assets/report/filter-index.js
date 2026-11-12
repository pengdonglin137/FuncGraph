(function () {
    const fields = Object.freeze(['cpu', 'pid', 'comm', 'ret', 'params', 'duration', 'func',
        'fold-id', 'fold-type', 'fold-ancestors', 'irqs', 'resched', 'hirq', 'preempt']);
    const columns = new Map(fields.map((field, index) => ['data-' + field, index + 2]));
    const models = new WeakMap();
    const foldModels = new WeakMap();
    class TextRecord {
        constructor(row, version) { this.row = row; this.version = version; }
        get textContent() { return this.row[1]; }
        get source() { return this.version >= 2 ? this.row[this.row.length - 1] : undefined; }
        getAttribute(name) {
            if (name === 'data-line-number') return String(this.row[0]);
            if (name === 'data-space') return this.version === 3 ? this.row[fields.length + 2] : null;
            const index = columns.get(name);
            return index === undefined ? null : this.row[index];
        }
        querySelector(selector) { return selector === '.line-content' ? this : null; }
    }
    function* prepare(data) {
        const expected = data?.version === 3 ? [...fields, 'space'] : fields;
        if (!data || ![1, 2, 3].includes(data.version) || !Array.isArray(data.fields) || !Array.isArray(data.rows) ||
            data.fields.length !== expected.length || data.fields.some((field, index) => field !== expected[index])) {
            throw new Error('Unsupported Text filter index');
        }
        if (!models.has(data)) {
            const records = [];
            let previous = 0;
            for (let index = 0; index < data.rows.length; index++) {
                const row = data.rows[index];
                const source = row?.[expected.length + 2];
                if (!Array.isArray(row) || row.length !== expected.length + (data.version >= 2 ? 3 : 2) ||
                    !Number.isSafeInteger(row[0]) || row[0] <= previous || typeof row[1] !== 'string' ||
                    row.some((value, column) => column >= 2 && column < expected.length + 2 && value !== null && typeof value !== 'string') ||
                    data.version >= 2 && source !== null && (!Number.isSafeInteger(source) || source < 0)) {
                    throw new Error('Invalid Text filter index row ' + (index + 1));
                }
                previous = row[0];
                records.push(new TextRecord(row, data.version));
                if (index % 1024 === 0) yield 'Checking full-trace filter index…';
            }
            models.set(data, { records, version: data.version });
        }
        return models.get(data);
    }
    function* filter(model, text) {
        const { ast, sortDir } = parseFilterExpr(text);
        const graphSets = yield* fgGraphSetsSteps(collectGraphConditions(ast), model.records);
        const lines = ast ? new Set() : null;
        let visible = 0;
        let scanned = 0;
        for (const line of model.records) {
            const trimmed = line.querySelector('.line-content').textContent.trim();
            if (!ast || trimmed !== '' && !/^-+$/.test(trimmed) && evalExpr(line, ast, graphSets)) {
                visible++;
                if (lines) lines.add(+line.getAttribute('data-line-number'));
            }
            scanned++;
            if (scanned % 1024 === 0) yield 'Filtering all Text rows: ' + scanned + ' / ' + model.records.length + '…';
        }
        return { text: ast || sortDir ? text : '', lines, visible, total: model.records.length, sortDir, graphSets };
    }
    function* steps(data, text) {
        const model = yield* prepare(data);
        const { graphSets, ...result } = yield* filter(model, text);
        return result;
    }
    function* prepareFolding(model) {
        if (foldModels.has(model)) return foldModels.get(model);
        const blocks = new Map();
        const ancestry = new Map();
        const memberRanges = new Map();
        let rangeValues = 0;
        function addMember(id, index) {
            if (!memberRanges.has(id)) memberRanges.set(id, []);
            const ranges = memberRanges.get(id);
            if (ranges.length && ranges[ranges.length - 1] === index) ranges[ranges.length - 1] = index + 1;
            else { ranges.push(index, index + 1); rangeValues += 2; }
        }
        function* recordsIn(start, end) {
            for (let index = start; index < end; index++) yield model.records[index];
        }
        const ancestors = record => {
            const text = record.getAttribute('data-fold-ancestors') || '';
            if (!ancestry.has(text)) ancestry.set(text, Array.from(new Set(text.split(/\s+/).filter(Boolean))));
            return ancestry.get(text);
        };
        const block = id => {
            if (!blocks.has(id)) blocks.set(id, { id, entry: null, exit: null, calls: 0 });
            return blocks.get(id);
        };
        let scanned = 0;
        for (const record of model.records) {
            const id = record.getAttribute('data-fold-id');
            const type = record.getAttribute('data-fold-type');
            const parents = ancestors(record);
            const ownsAncestor = parents.includes(id);
            const call = type === 'entry' || type === 'inner' && record.getAttribute('data-func');
            if (id && (type === 'entry' || type === 'exit')) block(id)[type] = record;
            for (const ancestor of parents) {
                addMember(ancestor, scanned);
                if (call && (ancestor !== id || type === 'inner')) block(ancestor).calls++;
            }
            if (!ownsAncestor) addMember(id, scanned);
            if (call && type === 'inner' && id && !ownsAncestor) block(id).calls++;
            scanned++;
            if (scanned % 512 === 0) yield 'Preparing Text fold metadata: ' + scanned + ' / ' + model.records.length + '…';
        }
        const rangeIndex = new Uint32Array(rangeValues + memberRanges.size);
        let indexed = 0, position = 0;
        for (const [id, ranges] of memberRanges) {
            rangeIndex[position] = ranges.length;
            rangeIndex.set(ranges, position + 1);
            memberRanges.set(id, position);
            position += ranges.length + 1;
            indexed++;
            if (indexed % 512 === 0) yield 'Indexing Text fold ranges: ' + indexed + ' / ' + memberRanges.size + '…';
        }
        const result = {
            records: model.records,
            blocks,
            *members(id) {
                const position = memberRanges.get(id);
                if (position === undefined) return;
                const end = position + 1 + rangeIndex[position];
                for (let offset = position + 1; offset < end; offset += 2) yield* recordsIn(rangeIndex[offset], rangeIndex[offset + 1]);
            },
            *membersOf(ids) {
                const selected = [];
                for (const id of new Set(ids)) {
                    const position = memberRanges.get(id);
                    if (position === undefined) continue;
                    const end = position + 1 + rangeIndex[position];
                    for (let offset = position + 1; offset < end; offset += 2) selected.push([rangeIndex[offset], rangeIndex[offset + 1]]);
                }
                selected.sort((left, right) => left[0] - right[0] || right[1] - left[1]);
                let start = -1, end = -1;
                for (const range of selected) {
                    if (range[0] > end) {
                        if (start >= 0) yield* recordsIn(start, end);
                        [start, end] = range;
                    } else end = Math.max(end, range[1]);
                }
                if (start >= 0) yield* recordsIn(start, end);
            },
            visible(record, folds, matches) {
                if (matches !== null && !matches.has(record.row[0])) return false;
                const own = record.getAttribute('data-fold-id');
                return !(record.getAttribute('data-fold-type') !== 'entry' && folds[own] === true) &&
                    !ancestors(record).some(ancestor => folds[ancestor] === true);
            },
            visibleLines(folds, matches) {
                const lines = new Set();
                for (const record of model.records) {
                    if (result.visible(record, folds, matches)) lines.add(record.row[0]);
                }
                return lines;
            },
        };
        foldModels.set(model, result);
        return result;
    }
    function folding(model) {
        const steps = prepareFolding(model);
        let result;
        do { result = steps.next(); } while (!result.done);
        return result.value;
    }
    function navigation(order, expandable, hidden = []) {
        const original = new Set(order);
        const sources = new Set(expandable);
        const concealed = new Set(hidden);
        if (original.size !== order.length || order.some(line => !Number.isSafeInteger(line) || line <= 0) ||
            [...sources, ...concealed].some(line => !original.has(line))) {
            throw new Error('Invalid Text navigation records');
        }
        let positions = new Map();
        let visibleTree = new Uint32Array(1), expandableTree = new Uint32Array(1);
        let visibleCount = 0, expandableCount = 0;
        let visibleSnapshot = null, expandableSnapshot = null;
        let updates = 0, rebuildPending = false;
        const rebuildThreshold = Math.max(1, Math.floor(order.length / Math.max(1, Math.ceil(Math.log2(order.length + 1)))));
        function rebuildTrees() {
            const length = result.order.length;
            visibleTree = new Uint32Array(length + 1);
            expandableTree = new Uint32Array(length + 1);
            visibleCount = expandableCount = 0;
            result.order.forEach((line, index) => {
                if (concealed.has(line)) return;
                visibleTree[index + 1] = 1;
                visibleCount++;
                if (sources.has(line)) { expandableTree[index + 1] = 1; expandableCount++; }
            });
            for (let index = 1; index <= length; index++) {
                const parent = index + (index & -index);
                if (parent <= length) {
                    visibleTree[parent] += visibleTree[index];
                    expandableTree[parent] += expandableTree[index];
                }
            }
            updates = 0;
            rebuildPending = false;
        }
        function countBefore(tree, end) {
            let count = 0;
            for (let index = Math.min(tree.length - 1, Math.max(0, Math.ceil(end))); index > 0; index -= index & -index) {
                count += tree[index];
            }
            return count;
        }
        function positionAt(tree, rank) {
            let position = 0;
            for (let stride = 2 ** Math.floor(Math.log2(tree.length - 1)); stride >= 1; stride /= 2) {
                const next = position + stride;
                if (next < tree.length && tree[next] <= rank) {
                    position = next;
                    rank -= tree[next];
                }
            }
            return position;
        }
        function snapshot(expandableOnly) {
            const indices = [];
            result.order.forEach((line, index) => {
                if (!concealed.has(line) && (!expandableOnly || sources.has(line))) indices.push(index);
            });
            return indices;
        }
        const result = {
            order: [],
            get visibleCount() { return visibleCount; },
            get expandableCount() { return expandableCount; },
            get visible() { return visibleSnapshot ??= snapshot(false); },
            get expandable() { return expandableSnapshot ??= snapshot(true); },
            indexOf(line) { return positions.get(line) ?? -1; },
            setVisible(line, visible) {
                if (!original.has(line)) throw new Error('Invalid Text navigation line');
                if (concealed.has(line) === !visible) return;
                if (visible) concealed.delete(line);
                else concealed.add(line);
                const change = visible ? 1 : -1, expandable = sources.has(line);
                visibleCount += change;
                visibleSnapshot = null;
                if (expandable) { expandableCount += change; expandableSnapshot = null; }
                if (rebuildPending || ++updates >= rebuildThreshold) {
                    rebuildPending = true;
                    return;
                }
                for (let index = positions.get(line) + 1; index < visibleTree.length; index += index & -index) {
                    visibleTree[index] += change;
                    if (expandable) expandableTree[index] += change;
                }
            },
            reorder(next) {
                const updated = new Map(next.map((line, index) => [line, index]));
                if (next.length !== original.size || updated.size !== original.size ||
                    next.some(line => !original.has(line))) throw new Error('Invalid Text navigation order');
                result.order = next.slice();
                positions = updated;
                rebuildTrees();
                visibleSnapshot = expandableSnapshot = null;
            },
            refresh() {
                if (rebuildPending) rebuildTrees();
                updates = 0;
                return result;
            },
            next(current, direction) {
                if (!expandableCount) return -1;
                result.refresh();
                if (current < 0) return positionAt(expandableTree, direction > 0 ? 0 : expandableCount - 1);
                const rank = direction > 0 ? countBefore(expandableTree, Math.floor(current) + 1) : countBefore(expandableTree, current) - 1;
                return positionAt(expandableTree, (rank + expandableCount) % expandableCount);
            },
            boundary(last) {
                const count = expandableCount || visibleCount;
                if (count) result.refresh();
                return count ? positionAt(expandableCount ? expandableTree : visibleTree, last ? count - 1 : 0) : -1;
            },
        };
        result.reorder(order);
        result.refresh();
        return result;
    }
    function* prepareNavigation(model) {
        if (model.version < 2) throw new Error('Text source metadata is unavailable');
        const order = [], expandable = [];
        for (const record of model.records) {
            order.push(record.row[0]);
            if (record.source !== null) expandable.push(record.row[0]);
            if (order.length % 512 === 0) yield 'Preparing Text navigation: ' + order.length + ' / ' + model.records.length + '…';
        }
        return navigation(order, expandable);
    }
    window.fgTextModel = { fields, prepare, filter, folding, prepareFolding, navigation, prepareNavigation };
    window.fgIndexedFilter = async function (data, text, signal, progress) {
        const iterator = steps(data, text);
        try {
            while (true) {
                if (signal.aborted) throw new DOMException('Filter cancelled', 'AbortError');
                const start = performance.now();
                let result;
                do {
                    result = iterator.next();
                    if (result.done) return result.value;
                } while (performance.now() - start < 8);
                progress(result.value);
                await new Promise(resolve => setTimeout(resolve, 0));
            }
        } finally { iterator.return(); }
    };
})();
