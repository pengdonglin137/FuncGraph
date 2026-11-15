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
