(function () {
    const fields = Object.freeze(['cpu', 'pid', 'comm', 'ret', 'params', 'duration', 'func',
        'fold-id', 'fold-type', 'fold-ancestors', 'irqs', 'resched', 'hirq', 'preempt']);
    const columns = new Map(fields.map((field, index) => ['data-' + field, index + 2]));
    const models = new WeakMap();
    const foldModels = new WeakMap();
    class TextRecord {
        constructor(row) { this.row = row; }
        get textContent() { return this.row[1]; }
        get source() { return this.row.length === fields.length + 3 ? this.row[fields.length + 2] : undefined; }
        getAttribute(name) {
            if (name === 'data-line-number') return String(this.row[0]);
            const index = columns.get(name);
            return index === undefined ? null : this.row[index];
        }
        querySelector(selector) { return selector === '.line-content' ? this : null; }
    }
    function* prepare(data) {
        if (!data || ![1, 2].includes(data.version) || !Array.isArray(data.fields) || !Array.isArray(data.rows) ||
            data.fields.length !== fields.length || data.fields.some((field, index) => field !== fields[index])) {
            throw new Error('Unsupported Text filter index');
        }
        if (!models.has(data)) {
            const records = [];
            let previous = 0;
            for (let index = 0; index < data.rows.length; index++) {
                const row = data.rows[index];
                const source = row?.[fields.length + 2];
                if (!Array.isArray(row) || row.length !== fields.length + (data.version === 2 ? 3 : 2) ||
                    !Number.isSafeInteger(row[0]) || row[0] <= previous || typeof row[1] !== 'string' ||
                    row.some((value, column) => column >= 2 && column < fields.length + 2 && value !== null && typeof value !== 'string') ||
                    data.version === 2 && source !== null && (!Number.isSafeInteger(source) || source < 0)) {
                    throw new Error('Invalid Text filter index row ' + (index + 1));
                }
                previous = row[0];
                records.push(new TextRecord(row));
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
            if (id && (type === 'entry' || type === 'exit')) block(id)[type] = record;
            if (type === 'entry' || type === 'inner' && record.getAttribute('data-func')) {
                for (const ancestor of parents) {
                    if (ancestor !== id || type === 'inner') block(ancestor).calls++;
                }
                if (type === 'inner' && id && !parents.includes(id)) block(id).calls++;
            }
            scanned++;
            if (scanned % 512 === 0) yield 'Preparing Text fold metadata: ' + scanned + ' / ' + model.records.length + '…';
        }
        const result = {
            records: model.records,
            blocks,
            *members(id) {
                for (const record of model.records) {
                    if (record.getAttribute('data-fold-id') === id || ancestors(record).includes(id)) yield record;
                }
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
        let dirty = true;
        const result = {
            order: [], expandable: [], visible: [],
            indexOf(line) { return positions.get(line) ?? -1; },
            setVisible(line, visible) {
                if (!original.has(line)) throw new Error('Invalid Text navigation line');
                if (concealed.has(line) === !visible) return;
                if (visible) concealed.delete(line);
                else concealed.add(line);
                dirty = true;
            },
            reorder(next) {
                const updated = new Map(next.map((line, index) => [line, index]));
                if (next.length !== original.size || updated.size !== original.size ||
                    next.some(line => !original.has(line))) throw new Error('Invalid Text navigation order');
                result.order = next.slice();
                positions = updated;
                dirty = true;
            },
            refresh() {
                if (!dirty) return;
                const visible = [], candidates = [];
                result.order.forEach((line, index) => {
                    if (concealed.has(line)) return;
                    visible.push(index);
                    if (sources.has(line)) candidates.push(index);
                });
                result.visible = visible;
                result.expandable = candidates;
                dirty = false;
            },
            next(current, direction) {
                result.refresh();
                const candidates = result.expandable;
                if (!candidates.length) return -1;
                if (current < 0) return candidates[direction > 0 ? 0 : candidates.length - 1];
                let lower = 0, upper = candidates.length;
                while (lower < upper) {
                    const middle = Math.floor((lower + upper) / 2);
                    if (candidates[middle] < current || direction > 0 && candidates[middle] === current) lower = middle + 1;
                    else upper = middle;
                }
                return direction > 0 ? candidates[lower % candidates.length] :
                    candidates[(lower - 1 + candidates.length) % candidates.length];
            },
            boundary(last) {
                result.refresh();
                const candidates = result.expandable.length ? result.expandable : result.visible;
                return candidates.length ? candidates[last ? candidates.length - 1 : 0] : -1;
            },
        };
        result.reorder(order);
        result.refresh();
        return result;
    }
    function* prepareNavigation(model) {
        if (model.version !== 2) throw new Error('Text source metadata is unavailable');
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
