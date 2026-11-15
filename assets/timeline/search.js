const SEARCH_BATCH = 4096;

function searchNeedsWorker(query) {
    if (query === '.*') return false;
    if (query.length > 512) return true;
    for (let position = 0; position < query.length; position++) {
        const character = query[position];
        if (character === '^' && position === 0 || character === '$' && position === query.length - 1) continue;
        if (character === '\\') {
            if (++position >= query.length || !'\\^$.*+?()[]{}|/-'.includes(query[position])) return true;
        } else if ('^$.*+?()[]{}|'.includes(character)) return true;
    }
    return false;
}

function searchRegexWorkerMain() {
    let matcher = null;
    self.onmessage = event => {
        const data = event.data;
        try {
            if (data.type === 'init') {
                matcher = new RegExp(data.query, 'i');
                self.postMessage({ type: 'ready' });
            } else if (data.type === 'batch') {
                const hits = new Uint8Array(data.names.length);
                for (let position = 0; position < data.names.length; position++) hits[position] = matcher.test(data.names[position]) ? 1 : 0;
                self.postMessage({ type: 'hits', id: data.id, buffer: hits.buffer }, [hits.buffer]);
            }
        } catch (error) {
            self.postMessage({ type: 'error', kind: data.type === 'init' ? 'regex' : 'work', message: error.message });
        }
    };
    self.postMessage({ type: 'started' });
}

function matchSearchNamesWorker(index, strings, query, signal) {
    if (!globalThis.Worker || !globalThis.Blob || !globalThis.URL?.createObjectURL) return null;
    return new Promise((resolve, reject) => {
        let worker = null, url = null, timer = 0, finished = false, started = false;
        let position = 0, batch = null, sequence = 0;
        const hits = new Uint8Array(strings.length);
        function cleanup() {
            finished = true;
            clearTimeout(timer);
            worker?.terminate();
            if (url) URL.revokeObjectURL(url);
            signal.removeEventListener('abort', abort);
        }
        function fail(message, kind = 'work') {
            if (finished) return;
            cleanup();
            const error = new Error(message);
            error.searchKind = kind;
            reject(error);
        }
        function abort() { fail('Function search cancelled.', 'abort'); }
        function sendBatch() {
            if (finished) return;
            if (position === index.names.length) { cleanup(); resolve(hits); return; }
            try {
                const names = [], start = position;
                let characters = 0;
                while (position < index.names.length && names.length < 256) {
                    const name = strings[index.names[position]];
                    if (names.length && characters + name.length > 65536) break;
                    names.push(name);
                    characters += name.length;
                    position++;
                }
                batch = index.names.subarray(start, position);
                worker.postMessage({ type: 'batch', id: ++sequence, names });
            }
            catch (error) { fail(error.message); }
        }
        if (signal.aborted) { abort(); return; }
        signal.addEventListener('abort', abort, { once: true });
        try {
            url = URL.createObjectURL(new Blob(['(' + searchRegexWorkerMain.toString() + ')()'], { type: 'text/javascript' }));
            worker = new Worker(url, { name: 'FuncGraph function search' });
            worker.onmessage = event => {
                if (finished) return;
                const data = event.data;
                if (data?.type === 'started' && !started) {
                    started = true;
                    clearTimeout(timer);
                    URL.revokeObjectURL(url);
                    url = null;
                    try { worker.postMessage({ type: 'init', query }); }
                    catch (error) { fail(error.message); }
                } else if (data?.type === 'ready' && started && !batch && !position) sendBatch();
                else if (data?.type === 'hits' && batch && data.id === sequence &&
                    data.buffer instanceof ArrayBuffer && data.buffer.byteLength === batch.length) {
                    const values = new Uint8Array(data.buffer);
                    for (let offset = 0; offset < values.length; offset++) {
                        if (values[offset] > 1) { fail('Invalid background function-search result.'); return; }
                        hits[batch[offset]] = values[offset];
                    }
                    batch = null;
                    sendBatch();
                } else if (data?.type === 'error' && typeof data.message === 'string') fail(data.message, data.kind === 'regex' ? 'regex' : 'work');
                else fail('Invalid background function-search response.');
            };
            worker.onerror = event => { event.preventDefault(); fail('Background function search failed.', started ? 'work' : 'unavailable'); };
            worker.onmessageerror = () => fail('Could not read background function-search results.');
            timer = setTimeout(() => fail('Background function search could not start.', 'unavailable'), 5000);
        } catch (error) { fail(error.message, 'unavailable'); }
    });
}

function* buildSearchIndex(callNames, count) {
    const counts = new Map();
    for (let index = 0; index < count; index++) {
        if (index && index % SEARCH_BATCH === 0) yield 'Indexing function calls…';
        const name = callNames[index];
        counts.set(name, (counts.get(name) || 0) + 1);
    }
    const names = new Int32Array(counts.size), offsets = new Uint32Array(counts.size + 1);
    let position = 0, total = 0;
    for (const [name, size] of counts) {
        if (position && position % SEARCH_BATCH === 0) yield 'Indexing function calls…';
        names[position] = name;
        offsets[position++] = total;
        counts.set(name, total);
        total += size;
    }
    offsets[position] = total;
    const calls = new Uint32Array(count);
    for (let index = 0; index < count; index++) {
        if (index && index % SEARCH_BATCH === 0) yield 'Indexing function calls…';
        const name = callNames[index], cursor = counts.get(name);
        calls[cursor] = index;
        counts.set(name, cursor + 1);
    }
    return { names, offsets, calls };
}

function* matchSearchNames(index, strings, matcher) {
    const hits = new Uint8Array(strings.length);
    for (let position = 0; position < index.names.length; position++) {
        if (position && position % 256 === 0) yield 'Searching function names…';
        const name = index.names[position];
        if (matcher.test(strings[name])) hits[name] = 1;
    }
    return hits;
}

function searchHeapDown(heap, position) {
    const item = heap[position];
    while (position * 2 + 1 < heap.length) {
        let child = position * 2 + 1;
        if (child + 1 < heap.length && heap[child + 1].value < heap[child].value) child++;
        if (heap[child].value >= item.value) break;
        heap[position] = heap[child];
        position = child;
    }
    heap[position] = item;
}

function* collectSearchCalls(index, callNames, count, hits) {
    const groups = [];
    let total = 0;
    for (let position = 0; position < index.names.length; position++) {
        if (position && position % SEARCH_BATCH === 0) yield 'Searching function calls…';
        if (!hits[index.names[position]]) continue;
        const start = index.offsets[position], end = index.offsets[position + 1];
        total += end - start;
        groups.push({ position: start, end, value: index.calls[start] });
    }
    if (!total) return new Uint32Array(0);
    if (groups.length === 1) return index.calls.subarray(groups[0].position, groups[0].end);
    const matches = new Uint32Array(total);
    if (total >= count / 8) {
        let position = 0;
        for (let call = 0; call < count; call++) {
            if (call && call % SEARCH_BATCH === 0) yield 'Searching function calls…';
            if (hits[callNames[call]]) matches[position++] = call;
        }
        return matches;
    }
    for (let position = Math.floor(groups.length / 2) - 1; position >= 0; position--) {
        if (position % SEARCH_BATCH === 0) yield 'Searching function calls…';
        searchHeapDown(groups, position);
    }
    for (let position = 0; position < total; position++) {
        if (position && position % SEARCH_BATCH === 0) yield 'Searching function calls…';
        const first = groups[0];
        matches[position] = first.value;
        if (++first.position < first.end) first.value = index.calls[first.position];
        else {
            const last = groups.pop();
            if (groups.length) groups[0] = last;
        }
        if (groups.length) searchHeapDown(groups, 0);
    }
    return matches;
}

function* filterSearchCalls(matches, accepted) {
    if (!accepted || !matches.length) return matches;
    let count = 0;
    for (let position = 0; position < matches.length; position++) {
        if (position && position % SEARCH_BATCH === 0) yield 'Filtering search matches…';
        if (accepted[matches[position]]) count++;
    }
    if (count === matches.length) return matches;
    const visible = new Uint32Array(count);
    if (!count) return visible;
    let offset = 0;
    for (let position = 0; position < matches.length; position++) {
        if (position && position % SEARCH_BATCH === 0) yield 'Filtering search matches…';
        const call = matches[position];
        if (accepted[call]) visible[offset++] = call;
    }
    return visible;
}
