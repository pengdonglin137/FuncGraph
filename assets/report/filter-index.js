(function () {
    const fields = Object.freeze(['cpu', 'pid', 'comm', 'ret', 'params', 'duration', 'func',
        'fold-id', 'fold-type', 'fold-ancestors', 'irqs', 'resched', 'hirq', 'preempt']);
    const columns = new Map(fields.map((field, index) => ['data-' + field, index + 2]));
    const models = new WeakMap();
    class TextRecord {
        constructor(row) { this.row = row; }
        get textContent() { return this.row[1]; }
        getAttribute(name) {
            if (name === 'data-line-number') return String(this.row[0]);
            const index = columns.get(name);
            return index === undefined ? null : this.row[index];
        }
        querySelector(selector) { return selector === '.line-content' ? this : null; }
    }
    function* prepare(data) {
        if (!data || data.version !== 1 || !Array.isArray(data.fields) || !Array.isArray(data.rows) ||
            data.fields.length !== fields.length || data.fields.some((field, index) => field !== fields[index])) {
            throw new Error('Unsupported Text filter index');
        }
        if (!models.has(data)) {
            const records = [];
            let previous = 0;
            for (let index = 0; index < data.rows.length; index++) {
                const row = data.rows[index];
                if (!Array.isArray(row) || row.length !== fields.length + 2 ||
                    !Number.isSafeInteger(row[0]) || row[0] <= previous || typeof row[1] !== 'string' ||
                    row.some((value, column) => column >= 2 && value !== null && typeof value !== 'string')) {
                    throw new Error('Invalid Text filter index row ' + (index + 1));
                }
                previous = row[0];
                records.push(new TextRecord(row));
                if (index % 1024 === 0) yield 'Checking full-trace filter index…';
            }
            models.set(data, { records });
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
    window.fgTextModel = { fields, prepare, filter };
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
