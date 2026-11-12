(function () {
    const fields = Object.freeze(['cpu', 'pid', 'comm', 'ret', 'params', 'duration', 'func',
        'fold-id', 'fold-type', 'fold-ancestors', 'irqs', 'resched', 'hirq', 'preempt']);
    const columns = new Map(fields.map((field, index) => ['data-' + field, index + 2]));
    const presentationFields = Object.freeze([...fields, 'space', 'sort-duration', 'fold-ret', 'call-depth']);
    const presentationColumns = new Map(presentationFields.map((field, index) => ['data-' + field, index + 2]));
    const models = new WeakMap();
    const foldModels = new WeakMap();
    const orderModels = new WeakMap();
    class TextRecord {
        constructor(row, version) { this.row = row; this.version = version; }
        get textContent() { return this.row[1]; }
        get source() { return this.version >= 2 ? this.row[this.row.length - 1] : undefined; }
        getAttribute(name) {
            if (name === 'data-line-number') return String(this.row[0]);
            if (this.version === 4) {
                const position = presentationColumns.get(name);
                return position === undefined ? null : this.row[position];
            }
            if (name === 'data-space') return this.version === 3 ? this.row[fields.length + 2] : null;
            const index = columns.get(name);
            return index === undefined ? null : this.row[index];
        }
        querySelector(selector) { return selector === '.line-content' ? this : null; }
    }
    function* prepare(data) {
        const expected = data?.version === 4 ? presentationFields : data?.version === 3 ? [...fields, 'space'] : fields;
        if (!data || ![1, 2, 3, 4].includes(data.version) || !Array.isArray(data.fields) || !Array.isArray(data.rows) ||
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
    function locate(model, number) {
        if (!Number.isSafeInteger(number) || number <= 0) throw new Error('Invalid Text line identity');
        let first = 0, end = model.records.length;
        while (first < end) {
            const middle = first + Math.floor((end - first) / 2);
            if (model.records[middle].row[0] < number) first = middle + 1;
            else end = middle;
        }
        const record = model.records[first];
        return record?.row[0] === number ? { ordinal: first, record } : null;
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
    function* prepareOrder(model, mode) {
        if (mode !== 'time' && mode !== 'task') throw new Error('Invalid Text order');
        const cached = orderModels.get(model)?.get(mode);
        if (cached) return cached;
        const order = [], boundaries = [], hidden = new Set(), switches = new Set();
        if (mode === 'time') {
            for (const record of model.records) {
                order.push(record.row[0]);
                if (order.length % 512 === 0) yield 'Preparing Text order: ' + order.length + ' / ' + model.records.length + '…';
            }
        } else {
            const groups = new Map(), head = [];
            const realBefore = new Uint32Array(model.records.length);
            let realCount = 0, current = '';
            for (let index = 0; index < model.records.length; index++) {
                const record = model.records[index];
                const separator = /^\s*(-{5,})?\s*$/.test(record.textContent);
                realBefore[index] = realCount;
                if (!separator) realCount++;
                const pid = record.getAttribute('data-pid'), cpu = record.getAttribute('data-cpu');
                let key = pid === '' || pid === null ? cpu ? 'cpu' + cpu : '' : pid === '0' ? 'idle' + cpu : 'p' + pid;
                if (!key) {
                    if (!current) head.push(index);
                    else {
                        if (separator) hidden.add(record.row[0]);
                        key = current;
                    }
                }
                if (key) {
                    current = key;
                    if (!groups.has(key)) groups.set(key, []);
                    groups.get(key).push(index);
                }
                if ((index + 1) % 512 === 0) yield 'Grouping complete Text rows: ' + (index + 1) + ' / ' + model.records.length + '…';
            }
            for (const index of head) order.push(model.records[index].row[0]);
            const tasks = Array.from(groups).sort((left, right) => {
                const leftIdle = left[0].startsWith('idle'), rightIdle = right[0].startsWith('idle');
                return leftIdle !== rightIdle ? leftIdle ? 1 : -1 : right[1].length - left[1].length;
            });
            let summarized = 0;
            for (const [key, members] of tasks) {
                let first = null, count = 0;
                const cpus = new Set();
                for (const index of members) {
                    const record = model.records[index];
                    if (++summarized % 512 === 0) yield 'Summarizing task rows: ' + summarized + ' / ' + model.records.length + '…';
                    if (hidden.has(record.row[0])) continue;
                    first ||= record;
                    count++;
                    const cpu = record.getAttribute('data-cpu');
                    if (cpu) cpus.add(cpu);
                }
                first ||= model.records[members[0]];
                boundaries.push({ at: order.length, kind: 'task-group-head',
                    text: (first.getAttribute('data-comm') || key) + '  ' + (first.getAttribute('data-pid') || '') +
                        '   ·  ' + count + ' lines  ·  CPU ' + Array.from(cpus).sort((left, right) => left - right).join(',') });
                let pendingSwitch = null, previous = -1, previousTime = null;
                for (const index of members) {
                    const record = model.records[index], number = record.row[0];
                    const switched = /^\s*(\d+)\)\s+(.+?)-(\d+)\s+=>\s+(.+?)-(\d+)\s*$/.exec(record.textContent);
                    const timestamp = /^\s*(\d+\.\d+)\s*\|/.exec(record.textContent);
                    if (pendingSwitch && !switched && !hidden.has(number)) {
                        const elapsed = timestamp && previousTime !== null ?
                            ' after ' + ((parseFloat(timestamp[1]) - previousTime) * 1e3).toFixed(3) + ' ms' : '';
                        boundaries.push({ at: order.length, kind: 'task-gap',
                            text: '⇣ switched out on CPU ' + pendingSwitch[1] + ' → ' + pendingSwitch[4].trim() + '-' + pendingSwitch[5] +
                                '; resumes at line ' + number + elapsed + ' (' + (realBefore[index] - realBefore[previous] - 1) +
                                ' lines of other tasks in between)' });
                        pendingSwitch = null;
                    }
                    if (switched) { pendingSwitch = switched; switches.add(number); }
                    if (timestamp) previousTime = parseFloat(timestamp[1]);
                    if (!hidden.has(number)) previous = index;
                    order.push(number);
                    if (order.length % 512 === 0) yield 'Ordering task rows: ' + order.length + ' / ' + model.records.length + '…';
                }
            }
        }
        if (!orderModels.has(model)) orderModels.set(model, new Map());
        const plans = orderModels.get(model);
        if (!plans.has(mode)) plans.set(mode, { order, boundaries, hidden, switches });
        return plans.get(mode);
    }
    function order(model, mode) {
        const steps = prepareOrder(model, mode);
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
    window.fgTextModel = { fields, presentationFields, prepare, locate, filter, folding, prepareFolding, order, prepareOrder, navigation, prepareNavigation };
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
