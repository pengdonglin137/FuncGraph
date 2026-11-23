(function () {
    const fields = Object.freeze(['cpu', 'pid', 'comm', 'ret', 'params', 'duration', 'func',
        'fold-id', 'fold-type', 'fold-ancestors', 'irqs', 'resched', 'hirq', 'preempt']);
    const columns = new Map(fields.map((field, index) => ['data-' + field, index + 2]));
    const presentationFields = Object.freeze([...fields, 'space', 'sort-duration', 'fold-ret', 'call-depth']);
    const presentationColumns = new Map(presentationFields.map((field, index) => ['data-' + field, index + 2]));
    const symbolFields = Object.freeze([...presentationFields, 'raw-func']);
    const symbolColumns = new Map(symbolFields.map((field, index) => ['data-' + field, index + 2]));
    const outcomeFields = Object.freeze([...symbolFields, 'user-error']);
    const outcomeColumns = new Map(outcomeFields.map((field, index) => ['data-' + field, index + 2]));
    const models = new WeakMap();
    const foldModels = new WeakMap();
    const orderModels = new WeakMap();
    class TextRecord {
        constructor(row, version, event = false) { this.row = row; this.version = version; this.event = event; }
        get textContent() { return this.row[1]; }
        get source() { return this.version >= 2 ? this.row[this.row.length - 1] : undefined; }
        getAttribute(name) {
            if (name === 'data-line-number') return String(this.row[0]);
            if (name === 'data-trace-event') return this.event ? '1' : null;
            if (this.version >= 4) {
                const position = (this.version === 6 ? outcomeColumns : this.version === 5 ? symbolColumns : presentationColumns).get(name);
                return position === undefined ? null : this.row[position];
            }
            if (name === 'data-space') return this.version === 3 ? this.row[fields.length + 2] : null;
            const index = columns.get(name);
            return index === undefined ? null : this.row[index];
        }
        querySelector(selector) { return selector === '.line-content' ? this : null; }
    }
    function* prepare(data) {
        const expected = data?.version === 6 ? outcomeFields : data?.version === 5 ? symbolFields : data?.version === 4 ? presentationFields : data?.version === 3 ? [...fields, 'space'] : fields;
        if (!data || ![1, 2, 3, 4, 5, 6].includes(data.version) || !Array.isArray(data.fields) || !Array.isArray(data.rows) ||
            data.fields.length !== expected.length || data.fields.some((field, index) => field !== expected[index])) {
            throw new Error('Unsupported Text filter index');
        }
        if (!models.has(data)) {
            const records = [], eventRecords = [];
            const events = data.events === undefined ? [] : data.events;
            if (!Array.isArray(events) || events.some((number, index) => !Number.isSafeInteger(number) || number <= 0 ||
                index && number <= events[index - 1])) throw new Error('Invalid Text event index');
            const eventNumbers = new Set(events);
            const attributeEnd = expected.length + 2;
            const hasSource = data.version >= 2;
            const rowLength = attributeEnd + Number(hasSource);
            let previous = 0;
            for (let index = 0; index < data.rows.length; index++) {
                const row = data.rows[index];
                const source = row?.[attributeEnd];
                if (!Array.isArray(row) || row.length !== rowLength || !Number.isSafeInteger(row[0]) ||
                    row[0] <= previous || typeof row[1] !== 'string' ||
                    hasSource && source !== null && (!Number.isSafeInteger(source) || source < 0)) {
                    throw new Error('Invalid Text filter index row ' + (index + 1));
                }
                for (let column = 2; column < attributeEnd; column++) {
                    const attribute = row[column];
                    if (attribute !== null && typeof attribute !== 'string') throw new Error('Invalid Text filter index row ' + (index + 1));
                }
                previous = row[0];
                const record = new TextRecord(row, data.version, eventNumbers.delete(row[0]));
                records.push(record);
                if (record.event) eventRecords.push(record);
                if (index % 1024 === 0) yield 'Checking full-trace filter index…';
            }
            if (eventNumbers.size) throw new Error('Text event index references missing rows');
            models.set(data, { records, eventRecords, version: data.version });
        }
        return models.get(data);
    }
    function* filter(model, text) {
        const { ast, sortDir } = parseFilterExpr(text);
        const total = model.records.length;
        if (!ast) return { text: sortDir ? text : '', lines: null, visible: total, total, sortDir, graphSets: {} };
        const graphSets = yield* fgGraphSetsSteps(collectGraphConditions(ast), model.records);
        const lines = new Set();
        let scanned = 0;
        for (const line of model.records) {
            const trimmed = line.querySelector('.line-content').textContent.trim();
            if (trimmed !== '' && !/^-+$/.test(trimmed) && evalExpr(line, ast, graphSets)) {
                lines.add(+line.getAttribute('data-line-number'));
            }
            scanned++;
            if (scanned % 1024 === 0) yield 'Filtering all Text rows: ' + scanned + ' / ' + total + '…';
        }
        return { text, lines, visible: lines.size, total, sortDir, graphSets };
    }
    function locate(model, number) {
        if (!Number.isSafeInteger(number) || number <= 0) throw new Error('Invalid Text line identity');
        const initial = model.records[0];
        if (!initial || number < initial.row[0]) return null;
        if (number === initial.row[0]) return { ordinal: 0, record: initial };
        const ordinal = number - initial.row[0], candidate = model.records[ordinal];
        if (candidate?.row[0] === number) return { ordinal, record: candidate };
        let first = 1, end = model.records.length;
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
    function visibility(folds, matches, parentNames = null) {
        const hasFolds = Object.keys(folds).some(id => folds[id] === true), ancestryVisible = new Map();
        return record => {
            if (matches !== null && !matches.has(record.row?.[0] ?? +record.getAttribute('data-line-number'))) return false;
            if (!hasFolds) return true;
            const own = record.getAttribute('data-fold-id');
            if (record.getAttribute('data-fold-type') !== 'entry' && folds[own] === true) return false;
            const key = record.getAttribute('data-fold-ancestors') || '';
            let visible = ancestryVisible.get(key);
            if (visible === undefined) {
                const parents = parentNames ? parentNames(record) : key.split(/\s+/).filter(Boolean);
                visible = !parents.some(ancestor => folds[ancestor] === true);
                ancestryVisible.set(key, visible);
            }
            return visible;
        };
    }
    function* prepareFolding(model) {
        if (foldModels.has(model)) return foldModels.get(model);
        const blocks = new Map();
        const ancestry = new Map();
        const firstRangeOffsets = new Map(), firstRanges = [], extraRanges = new Map();
        const foldId = columns.get('data-fold-id'), foldType = columns.get('data-fold-type');
        const foldAncestors = columns.get('data-fold-ancestors'), functionName = columns.get('data-func');
        function addRange(id, start, end) {
            const offset = firstRangeOffsets.get(id);
            if (offset === undefined) {
                firstRangeOffsets.set(id, firstRanges.length);
                firstRanges.push(start, end);
                return;
            }
            if (firstRanges[offset + 1] === start) {
                firstRanges[offset + 1] = end;
                return;
            }
            let ranges = extraRanges.get(offset);
            if (!ranges) extraRanges.set(offset, ranges = []);
            if (ranges.length && ranges[ranges.length - 1] === start) ranges[ranges.length - 1] = end;
            else ranges.push(start, end);
        }
        function* recordsIn(start, end) {
            for (let index = start; index < end; index++) yield model.records[index];
        }
        const ancestorNames = text => {
            let parents = ancestry.get(text);
            if (!parents) {
                const separator = text.lastIndexOf(' ');
                const prefix = separator > 0 ? ancestry.get(text.slice(0, separator)) : null;
                if (prefix) {
                    const tail = text.slice(separator + 1);
                    if (tail && !/\s/.test(tail)) parents = prefix.includes(tail) ? prefix : [...prefix, tail];
                }
                if (!parents) parents = text && !/\s/.test(text) ? [text] : Array.from(new Set(text.split(/\s+/).filter(Boolean)));
                ancestry.set(text, parents);
            }
            return parents;
        };
        const ancestors = record => ancestorNames(record.getAttribute('data-fold-ancestors') || '');
        const block = id => {
            let state = blocks.get(id);
            if (!state) blocks.set(id, state = { id, entry: null, exit: null, calls: 0 });
            return state;
        };
        let scanned = 0, calls = 0, active = ancestorNames('');
        const rangeStarts = [], callStarts = [];
        function closeAncestors(keep) {
            for (let depth = active.length - 1; depth >= keep; depth--) {
                const id = active[depth], count = calls - callStarts[depth];
                addRange(id, rangeStarts[depth], scanned);
                if (count) block(id).calls += count;
            }
        }
        for (const record of model.records) {
            const row = record.row, id = row[foldId], type = row[foldType];
            const parents = ancestorNames(row[foldAncestors] || '');
            if (parents !== active) {
                let keep = 0;
                const shared = Math.min(active.length, parents.length);
                while (keep < shared && active[keep] === parents[keep]) keep++;
                closeAncestors(keep);
                for (let depth = keep; depth < parents.length; depth++) {
                    rangeStarts[depth] = scanned;
                    callStarts[depth] = calls;
                }
                active = parents;
            }
            const call = type === 'entry' || type === 'inner' && row[functionName];
            if (id && (type === 'entry' || type === 'exit')) block(id)[type] = record;
            const own = id ? parents.lastIndexOf(id) : -1;
            if (call) {
                calls++;
                if (type === 'entry' && own >= 0) callStarts[own]++;
                if (type === 'inner' && id && own < 0) block(id).calls++;
            }
            if (own < 0) addRange(id, scanned, scanned + 1);
            scanned++;
            if (scanned % 512 === 0) yield 'Preparing Text fold metadata: ' + scanned + ' / ' + model.records.length + '…';
        }
        closeAncestors(0);
        const result = {
            records: model.records,
            eventRecords: model.eventRecords,
            blocks,
            *members(id) {
                const offset = firstRangeOffsets.get(id);
                if (offset === undefined) return;
                yield* recordsIn(firstRanges[offset], firstRanges[offset + 1]);
                const ranges = extraRanges.get(offset);
                for (let index = 0; ranges && index < ranges.length; index += 2) yield* recordsIn(ranges[index], ranges[index + 1]);
            },
            *membersOf(ids) {
                const selected = [];
                for (const id of new Set(ids)) {
                    const offset = firstRangeOffsets.get(id);
                    if (offset === undefined) continue;
                    selected.push([firstRanges[offset], firstRanges[offset + 1]]);
                    const ranges = extraRanges.get(offset);
                    for (let index = 0; ranges && index < ranges.length; index += 2) selected.push([ranges[index], ranges[index + 1]]);
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
            hiddenMask(folds) {
                let hidden = null;
                for (const id of Object.keys(folds)) if (folds[id] === true) {
                    for (const key of id === 'null' ? [id, null] : [id]) {
                        const offset = firstRangeOffsets.get(key);
                        if (offset === undefined) continue;
                        if (hidden === null) hidden = new Uint8Array(model.records.length);
                        hidden.fill(1, firstRanges[offset], firstRanges[offset + 1]);
                        const ranges = extraRanges.get(offset);
                        for (let index = 0; ranges && index < ranges.length; index += 2) hidden.fill(1, ranges[index], ranges[index + 1]);
                    }
                }
                if (hidden === null) return null;
                for (let index = 0; index < hidden.length; index++) if (hidden[index]) {
                    const record = model.records[index];
                    if (record.getAttribute('data-fold-type') === 'entry' &&
                        folds[record.getAttribute('data-fold-id')] === true &&
                        !ancestors(record).some(ancestor => folds[ancestor] === true)) hidden[index] = 0;
                }
                return hidden;
            },
            visibility(folds, matches) { return visibility(folds, matches, ancestors); },
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
        const ordinals = mode === 'task' ? new Uint32Array(model.records.length) : null;
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
            for (const index of head) {
                ordinals[order.length] = index;
                order.push(model.records[index].row[0]);
            }
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
                    ordinals[order.length] = index;
                    order.push(number);
                    if (order.length % 512 === 0) yield 'Ordering task rows: ' + order.length + ' / ' + model.records.length + '…';
                }
            }
        }
        if (!orderModels.has(model)) orderModels.set(model, new Map());
        const plans = orderModels.get(model);
        if (!plans.has(mode)) plans.set(mode, { order, ordinals, boundaries, hidden, switches });
        return plans.get(mode);
    }
    function order(model, mode) {
        const steps = prepareOrder(model, mode);
        let result;
        do { result = steps.next(); } while (!result.done);
        return result.value;
    }
    function rankDuration(model, plan, direction) {
        if (direction !== 'asc' && direction !== 'desc') throw new Error('Invalid Text duration order');
        const durations = new Float64Array(plan.order.length), ranked = new Array(plan.order.length);
        let finite = true;
        for (let index = 0; index < ranked.length; index++) {
            const ordinal = plan.ordinals ? plan.ordinals[index] : index, record = model.records[ordinal];
            const duration = parseFloat(record.getAttribute('data-sort-duration') || record.getAttribute('data-duration') || '0');
            durations[index] = duration; ranked[index] = index;
            if (!Number.isFinite(duration)) finite = false;
        }
        return rankDurationValues(plan, durations, direction, finite, ranked);
    }
    function rankDurationValues(plan, durations, direction, finite, ranked) {
        if (!finite) {
            for (let index = 0; index < ranked.length; index++) {
                ranked[index] = { number: plan.order[index], ordinal: plan.ordinals ? plan.ordinals[index] : index,
                    duration: durations[index] };
            }
            ranked.sort((left, right) => direction === 'desc' ? right.duration - left.duration : left.duration - right.duration);
            return { order: ranked.map(item => item.number), ordinals: Uint32Array.from(ranked, item => item.ordinal) };
        }
        ranked.sort((left, right) => direction === 'desc' ? durations[right] - durations[left] : durations[left] - durations[right]);
        return { order: ranked.map(index => plan.order[index]),
            ordinals: Uint32Array.from(ranked, index => plan.ordinals ? plan.ordinals[index] : index) };
    }
    function* prepareDuration(model, plan, direction) {
        if (direction !== 'asc' && direction !== 'desc') throw new Error('Invalid Text duration order');
        const length = plan.order.length, durations = new Float64Array(length);
        let finite = true;
        for (let index = 0; index < length; index++) {
            const ordinal = plan.ordinals ? plan.ordinals[index] : index, record = model.records[ordinal];
            const duration = parseFloat(record.getAttribute('data-sort-duration') || record.getAttribute('data-duration') || '0');
            durations[index] = duration;
            if (!Number.isFinite(duration)) finite = false;
            if ((index + 1) % 2048 === 0) yield 'Reading exact durations: ' + (index + 1) + ' / ' + length + '…';
        }
        if (!finite) {
            yield 'Sorting non-finite durations with legacy ordering…';
            return rankDurationValues(plan, durations, direction, false, new Array(length));
        }
        let ranked = new Uint32Array(length), scratch = new Uint32Array(length);
        for (let index = 0; index < length; index++) {
            ranked[index] = index;
            if ((index + 1) % 2048 === 0) yield 'Preparing stable duration positions…';
        }
        const compare = direction === 'desc' ? (left, right) => durations[right] - durations[left] :
            (left, right) => durations[left] - durations[right];
        const run = 1024;
        for (let start = 0; start < length; start += run) {
            ranked.subarray(start, Math.min(start + run, length)).sort(compare);
            yield 'Sorting duration runs: ' + Math.min(start + run, length) + ' / ' + length + '…';
        }
        for (let width = run; width < length; width *= 2) {
            for (let start = 0; start < length; start += width * 2) {
                const middle = Math.min(start + width, length), end = Math.min(start + width * 2, length);
                let left = start, right = middle;
                for (let index = start; index < end; index++) {
                    scratch[index] = left < middle && (right >= end || compare(ranked[left], ranked[right]) <= 0) ?
                        ranked[left++] : ranked[right++];
                    if ((index + 1) % 2048 === 0) yield 'Merging complete duration order…';
                }
            }
            const previous = ranked; ranked = scratch; scratch = previous;
        }
        const order = new Array(length), ordinals = new Uint32Array(length);
        for (let index = 0; index < length; index++) {
            const position = ranked[index];
            order[index] = plan.order[position];
            ordinals[index] = plan.ordinals ? plan.ordinals[position] : position;
            if ((index + 1) % 2048 === 0) yield 'Building duration order: ' + (index + 1) + ' / ' + length + '…';
        }
        return { order, ordinals };
    }
    async function rankDurationAsync(model, plan, direction, signal, progress = () => {}) {
        const iterator = prepareDuration(model, plan, direction);
        try {
            while (true) {
                if (signal?.aborted) throw new DOMException('Duration order cancelled', 'AbortError');
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
    }
    function navigation(order, expandable, hidden = []) {
        if (!Array.isArray(order)) throw new Error('Invalid Text navigation order');
        const positions = new Map();
        for (let index = 0; index < order.length; index++) {
            const line = order[index];
            if (!Number.isSafeInteger(line) || line <= 0 || positions.has(line)) throw new Error('Invalid Text navigation records');
            positions.set(line, index);
        }
        const sources = new Set(expandable);
        const concealed = new Set(hidden);
        for (const lines of [sources, concealed]) for (const line of lines) {
            if (!positions.has(line)) throw new Error('Invalid Text navigation records');
        }
        return createNavigation(order.slice(), positions, sources, concealed);
    }
    function createNavigation(order, positions, sources, concealed, initialTrees = null) {
        let visibleTree = initialTrees?.visible, expandableTree = initialTrees?.expandable;
        let visibleCount = order.length, expandableCount = sources.size;
        let visibleSnapshot = null, expandableSnapshot = null;
        let visibilityState = null;
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
        function changeVisibility(line, visible, position) {
            if (visible) concealed.delete(line);
            else concealed.add(line);
            const change = visible ? 1 : -1, expandable = sources.has(line);
            visibleCount += change;
            visibleSnapshot = null;
            if (expandable) { expandableCount += change; expandableSnapshot = null; }
            if (visibilityState !== null) {
                position ??= positions.get(line);
                visibilityState[position] = visible ? 1 : 0;
            }
            if (rebuildPending || ++updates >= rebuildThreshold) {
                rebuildPending = true;
                return;
            }
            position ??= positions.get(line);
            for (let index = position + 1; index < visibleTree.length; index += index & -index) {
                visibleTree[index] += change;
                if (expandable) expandableTree[index] += change;
            }
        }
        const result = {
            order,
            get visibleCount() { return visibleCount; },
            get expandableCount() { return expandableCount; },
            get visible() { return visibleSnapshot ??= snapshot(false); },
            get expandable() { return expandableSnapshot ??= snapshot(true); },
            indexOf(line) { return positions.get(line) ?? -1; },
            setVisible(line, visible) {
                if (!positions.has(line)) throw new Error('Invalid Text navigation line');
                if (concealed.has(line) === !visible) return;
                changeVisibility(line, visible);
            },
            setVisibility(values) {
                if (!(values instanceof Uint8Array) || values.length !== result.order.length) {
                    throw new Error('Invalid Text navigation visibility');
                }
                for (let index = 0; index < values.length; index++) {
                    if (values[index] > 1) throw new Error('Invalid Text navigation visibility');
                }
                for (let index = 0; index < values.length; index++) {
                    const line = result.order[index], visible = values[index] === 1;
                    if (visibilityState === null ? concealed.has(line) === !visible : visibilityState[index] === values[index]) continue;
                    changeVisibility(line, visible, index);
                }
                if (visibilityState === null) visibilityState = values.slice();
                return result;
            },
            reorder(next) {
                if (!Array.isArray(next) || next.length !== positions.size) throw new Error('Invalid Text navigation order');
                if (result.order.length === positions.size) {
                    let unchanged = true;
                    for (let index = 0; index < next.length; index++) {
                        if (positions.get(next[index]) !== index || result.order[index] !== next[index]) { unchanged = false; break; }
                    }
                    if (unchanged) { result.refresh(); return; }
                }
                const updated = new Map();
                for (let index = 0; index < next.length; index++) {
                    const line = next[index];
                    if (!positions.has(line)) throw new Error('Invalid Text navigation order');
                    updated.set(line, index);
                }
                if (updated.size !== positions.size) throw new Error('Invalid Text navigation order');
                result.order = next.slice();
                positions = updated;
                visibilityState = null;
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
        if (!initialTrees) rebuildTrees();
        return result;
    }
    function* prepareNavigation(model) {
        if (model.version < 2) throw new Error('Text source metadata is unavailable');
        const length = model.records.length;
        const order = new Array(length), positions = new Map(), sources = new Set();
        const visible = new Uint32Array(length + 1), expandable = new Uint32Array(length + 1);
        for (let index = 0; index < length; index++) {
            const record = model.records[index], line = record.row[0];
            if (!Number.isSafeInteger(line) || line <= 0 || positions.has(line)) throw new Error('Invalid Text navigation records');
            order[index] = line;
            positions.set(line, index);
            const position = index + 1, width = position & -position;
            visible[position] = width;
            if (record.source !== null) {
                sources.add(line);
                expandable[position]++;
            }
            if (position + width <= length) expandable[position + width] += expandable[position];
            if (position % 512 === 0) yield 'Preparing Text navigation: ' + position + ' / ' + length + '…';
        }
        return createNavigation(order, positions, sources, new Set(), { visible, expandable });
    }
    window.fgTextModel = { fields, presentationFields, symbolFields, outcomeFields, prepare, locate, filter, folding, visibility, prepareFolding, order, prepareOrder, rankDuration, prepareDuration, rankDurationAsync, navigation, prepareNavigation };
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
