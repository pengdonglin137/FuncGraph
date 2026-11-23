(function () {
    const descriptorNode = document.getElementById('fgDataManifest');
    const gzipType = 'application/x-funcgraph-gzip';
    const embedded = name => {
        const element = document.getElementById(name);
        return element && element.tagName === 'SCRIPT' && (element.type === 'application/json' || element.type === gzipType) ? element : null;
    };
    const cache = new Map();
    const sourceCache = { entries: new Map(), bytes: 0, chunks: 8, limit: 16 * 1024 * 1024 };
    const textCache = { entries: new Map(), bytes: 0, chunks: 4, limit: 16 * 1024 * 1024 };
    const requests = new Map();
    const checksumWorkers = [];
    let checksumWorkerUnavailable = false;
    let textIndexWorkerUnavailable = false;
    let checksumSequence = 0;
    let descriptor = null;
    function checksumWorkerMain() {
        self.onmessage = async event => {
            const { id, buffer } = event.data;
            try {
                const digest = await self.crypto.subtle.digest('SHA-256', buffer);
                self.postMessage({ id, buffer, digest }, [buffer, digest]);
            } catch (error) {
                self.postMessage({ id, buffer, error: error.message }, [buffer]);
            }
        };
        self.postMessage({ ready: !!self.crypto?.subtle });
    }
    function stopChecksumWorker(state, error) {
        const index = checksumWorkers.indexOf(state);
        if (index >= 0) checksumWorkers.splice(index, 1);
        clearTimeout(state.timer);
        state.worker?.terminate();
        if (state.url) URL.revokeObjectURL(state.url);
        state.url = null;
        state.resolve(null);
        for (const job of state.jobs.values()) {
            job.cleanup();
            job.reject(error);
        }
        state.jobs.clear();
    }
    function prepareChecksumWorker() {
        if (checksumWorkerUnavailable || !window.Worker || !window.Blob || !window.URL?.createObjectURL) return null;
        let available = checksumWorkers[0];
        for (const state of checksumWorkers) if (state.pending < available.pending) available = state;
        if (available && (!available.pending || checksumWorkers.length >= 2)) {
            available.pending++;
            return available;
        }
        const state = { worker: null, jobs: new Map(), ready: null, resolve: null, url: null, timer: 0, pending: 1 };
        state.ready = new Promise(resolve => { state.resolve = resolve; });
        checksumWorkers.push(state);
        const failed = message => {
            if (!checksumWorkers.includes(state)) return;
            checksumWorkerUnavailable = true;
            const error = new Error(typeof message === 'string' ? message :
                'Report checksum worker failed; retry to use the main-thread verifier');
            for (const current of [...checksumWorkers]) stopChecksumWorker(current, error);
        };
        try {
            state.url = URL.createObjectURL(new Blob(['(' + checksumWorkerMain.toString() + ')()'], { type: 'text/javascript' }));
            state.worker = new Worker(state.url, { name: 'FuncGraph checksums' });
            state.worker.onmessage = event => {
                if (!checksumWorkers.includes(state)) return;
                const data = event.data;
                if (data?.ready !== undefined) {
                    if (!data.ready) { failed(); return; }
                    clearTimeout(state.timer);
                    URL.revokeObjectURL(state.url);
                    state.url = null;
                    state.resolve(state);
                    return;
                }
                const job = state.jobs.get(data?.id);
                if (!job) return;
                if (data.error) {
                    failed('Report checksum worker failed: ' + data.error + '; retry to use the main-thread verifier');
                    return;
                }
                if (!(data.buffer instanceof ArrayBuffer) || data.buffer.byteLength !== job.bytes ||
                    !(data.digest instanceof ArrayBuffer) || data.digest.byteLength !== 32) {
                    failed('Invalid report checksum result; retry to use the main-thread verifier');
                    return;
                }
                state.jobs.delete(data.id);
                job.cleanup();
                job.resolve({ bytes: data.buffer, digest: data.digest });
            };
            state.worker.onerror = event => { event.preventDefault(); failed(); };
            state.worker.onmessageerror = failed;
            state.timer = setTimeout(failed, 5000);
        } catch (error) { failed(); }
        return state;
    }
    async function checksum(bytes, signal) {
        const reserved = bytes.byteLength >= 1024 * 1024 ? prepareChecksumWorker() : null;
        try {
            const state = reserved ? await reserved.ready : null;
            aborted(signal);
            if (!state || !checksumWorkers.includes(state)) {
                const digest = await window.crypto.subtle.digest('SHA-256', bytes);
                return { bytes, digest };
            }
            return await new Promise((resolve, reject) => {
                const id = ++checksumSequence;
                const cleanup = () => signal?.removeEventListener('abort', cancel);
                const cancel = () => {
                    state.jobs.delete(id);
                    cleanup();
                    const error = new DOMException('Data request canceled', 'AbortError');
                    reject(error);
                    if (!state.jobs.size && state.pending <= 1) stopChecksumWorker(state, error);
                };
                state.jobs.set(id, { resolve, reject, cleanup, bytes: bytes.byteLength });
                signal?.addEventListener('abort', cancel, { once: true });
                const buffer = bytes instanceof ArrayBuffer ? bytes : bytes.buffer;
                try { state.worker.postMessage({ id, buffer }, [buffer]); }
                catch (error) {
                    state.jobs.delete(id);
                    cleanup();
                    reject(error);
                }
            });
        } finally { if (reserved) reserved.pending--; }
    }
    function cacheRelease(key) {
        for (const policy of [sourceCache, textCache]) {
            if (!policy.entries.has(key)) continue;
            policy.bytes -= policy.entries.get(key);
            policy.entries.delete(key);
        }
        return cache.delete(key);
    }
    function cacheRead(key) {
        for (const policy of [sourceCache, textCache]) {
            if (!policy.entries.has(key)) continue;
            const bytes = policy.entries.get(key);
            policy.entries.delete(key);
            policy.entries.set(key, bytes);
        }
        return cache.get(key);
    }
    function cacheStore(key, value, bytes) {
        const source = bytes !== undefined && /^data:fgSrc(?:0|[1-9][0-9]*)$/.test(key) && embedded('fgSourceChunks');
        const text = bytes !== undefined && /^data:fgText(?:0|[1-9][0-9]*)$/.test(key) && embedded('fgTextChunks');
        if (source) {
            const info = sourceInfo(Number(key.slice('data:fgSrc'.length)));
            if ('data:' + info.name !== key) throw new Error('Invalid source chunk identity: ' + key);
            sourceValue(info, value);
        }
        if (text) {
            const info = textInfo(Number(key.slice('data:fgText'.length)));
            if ('data:' + info.name !== key) throw new Error('Invalid Text chunk identity: ' + key);
            textValue(info, value);
        }
        cacheRelease(key);
        const policy = source ? sourceCache : text ? textCache : null;
        if (policy) {
            if (bytes > policy.limit) return;
            while (policy.entries.size >= policy.chunks || policy.bytes + bytes > policy.limit) {
                cacheRelease(policy.entries.keys().next().value);
            }
            policy.entries.set(key, bytes);
            policy.bytes += bytes;
        }
        cache.set(key, value);
    }
    function aborted(signal) {
        if (signal && signal.aborted) throw new DOMException('Data request canceled', 'AbortError');
    }
    function validateResource(resource) {
        if (!resource || typeof resource !== 'object' || typeof resource.file !== 'string' ||
            !Number.isSafeInteger(resource.bytes) || resource.bytes < 0 ||
            typeof resource.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(resource.sha256) ||
            !['json', 'text'].includes(resource.format)) throw new Error('Invalid report resource descriptor');
        if (!resource.file.split('/').every(part => /^[A-Za-z0-9_.-]+$/.test(part) && part !== '.' && part !== '..')) {
            throw new Error('Report resource paths must stay inside the report directory');
        }
        return resource;
    }
    function resourceURL(resource, base) {
        validateResource(resource);
        const result = new URL(resource.file, base);
        if (!['http:', 'https:'].includes(base.protocol)) {
            throw new Error('Sidecar data needs HTTP. Run fgview REPORT_DIRECTORY and open its http://127.0.0.1 address.');
        }
        if (result.origin !== base.origin || !result.pathname.startsWith(base.pathname)) {
            throw new Error('Report resource paths must stay inside the report directory');
        }
        return result;
    }
    function settings() {
        if (descriptor) return descriptor;
        if (!descriptorNode) throw new Error('External report data is not configured');
        const value = JSON.parse(descriptorNode.textContent);
        if (!value || value.version !== 1 || typeof value.report_id !== 'string' ||
            !/^[a-f0-9]{32}$/.test(value.report_id)) throw new Error('Invalid report data identity or version');
        validateResource(value.manifest);
        if (value.manifest.format !== 'json') throw new Error('The report manifest must be JSON');
        descriptor = value;
        return descriptor;
    }
    async function resourceBytes(response, resource, signal) {
        const mismatch = () => new Error('Report resource size mismatch: ' + resource.file);
        if (!response.body) {
            const bytes = await response.arrayBuffer();
            if (bytes.byteLength !== resource.bytes) throw mismatch();
            return bytes;
        }
        const reader = response.body.getReader();
        const bytes = new Uint8Array(resource.bytes);
        let offset = 0;
        let complete = false;
        try {
            while (true) {
                aborted(signal);
                const chunk = await reader.read();
                if (chunk.done) break;
                if (chunk.value.byteLength > bytes.byteLength - offset) throw mismatch();
                bytes.set(chunk.value, offset);
                offset += chunk.value.byteLength;
            }
            if (offset !== bytes.byteLength) throw mismatch();
            complete = true;
            return bytes;
        } finally {
            if (!complete) await reader.cancel().catch(() => {});
            reader.releaseLock();
        }
    }
    async function verified(resource, base, signal) {
        const url = resourceURL(resource, base);
        aborted(signal);
        if (!window.crypto || !window.crypto.subtle) throw new Error('Report integrity checks require a secure HTTP context');
        const response = await fetch(url.href, { signal, credentials: 'omit', cache: 'no-store', redirect: 'error' });
        if (!response.ok) throw new Error('Unable to load ' + resource.file + ' (HTTP ' + response.status + ')');
        const bytes = await resourceBytes(response, resource, signal);
        aborted(signal);
        if (bytes.byteLength !== resource.bytes) throw new Error('Report resource size mismatch: ' + resource.file);
        const checked = await checksum(bytes, signal);
        const hash = Array.from(new Uint8Array(checked.digest), byte => byte.toString(16).padStart(2, '0')).join('');
        aborted(signal);
        if (hash !== resource.sha256) throw new Error('Report resource checksum mismatch: ' + resource.file);
        const text = new TextDecoder('utf-8', { fatal: true }).decode(checked.bytes);
        return resource.format === 'json' ? JSON.parse(text) : text;
    }
    function dropUnusedRequest(key, flight) {
        if (flight.users || flight.handoff || requests.get(key) !== flight) return;
        requests.delete(key);
        flight.controller.abort();
    }
    function request(key, signal, loader, store = cacheStore) {
        try { aborted(signal); } catch (error) { return Promise.reject(error); }
        if (cache.has(key)) return Promise.resolve(cacheRead(key));
        let flight = requests.get(key);
        if (!flight) {
            flight = { controller: new AbortController(), users: 0, promise: null };
            requests.set(key, flight);
            flight.promise = Promise.resolve().then(() => loader(flight.controller.signal)).then(value => {
                aborted(flight.controller.signal);
                if (requests.get(key) === flight) store(key, value);
                return value;
            }).finally(() => {
                if (flight.handoff) clearTimeout(flight.handoff);
                if (requests.get(key) === flight) requests.delete(key);
            });
        }
        if (flight.handoff) {
            clearTimeout(flight.handoff);
            flight.handoff = null;
        }
        return new Promise((resolve, reject) => {
            flight.users++;
            let finished = false;
            const release = () => {
                if (finished) return false;
                finished = true;
                if (signal) signal.removeEventListener('abort', cancel);
                flight.users--;
                return true;
            };
            const cancel = () => {
                if (!release()) return;
                reject(new DOMException('Data request canceled', 'AbortError'));
                dropUnusedRequest(key, flight);
            };
            if (signal) signal.addEventListener('abort', cancel, { once: true });
            flight.promise.then(value => {
                if (release()) resolve(value);
            }, error => {
                if (release()) reject(error);
            });
        });
    }
    function manifest(signal) {
        return request('manifest', signal, async active => {
            const config = settings();
            const base = new URL('.', location.href);
            const value = await verified(config.manifest, base, active);
            if (!value || value.version !== 1 || value.report_id !== config.report_id ||
                !value.resources || typeof value.resources !== 'object' || Array.isArray(value.resources)) {
                throw new Error('Report manifest identity or version mismatch');
            }
            for (const resource of Object.values(value.resources)) validateResource(resource);
            return value;
        });
    }
    function ready(name) {
        return cache.has('data:' + name) || embedded(name)?.type === 'application/json';
    }
    function read(name) {
        const key = 'data:' + name;
        if (cache.has(key)) return cacheRead(key);
        const element = embedded(name);
        if (element?.type !== 'application/json') throw new Error('Report data ' + name + ' is not ready; await fgData.load() first');
        const payload = element.textContent;
        const value = JSON.parse(payload);
        const chunk = /^fgText(?:0|[1-9][0-9]*)$/.test(name) && embedded('fgTextChunks') ||
            /^fgSrc(?:0|[1-9][0-9]*)$/.test(name) && embedded('fgSourceChunks');
        cacheStore(key, value, chunk ? payload.length * 2 : undefined);
        return value;
    }
    async function inflateEmbedded(element, name, expected, signal) {
        aborted(signal);
        if (/^fgSrc[0-9]+$/.test(name)) {
            await new Promise(resolve => {
                if (typeof MessageChannel !== 'function') { setTimeout(resolve, 0); return; }
                const channel = new MessageChannel();
                channel.port1.onmessage = () => {
                    channel.port1.close();
                    channel.port2.close();
                    resolve();
                };
                channel.port2.postMessage(null);
            });
            aborted(signal);
        }
        if (typeof DecompressionStream !== 'function') {
            throw new Error('This browser cannot decompress report data. Use a browser with gzip DecompressionStream or regenerate without --windowed-compression gzip.');
        }
        if (!Number.isSafeInteger(expected) || expected <= 0) throw new Error('Invalid embedded gzip size: ' + name);
        const encoded = element.textContent;
        if (name === 'fgTextIndex' && expected >= 8 * 1024 * 1024) {
            const value = await decodeTextIndex(encoded, expected, signal);
            aborted(signal);
            if (value !== undefined) return value;
        }
        return inflateGzip(encoded, name, expected, signal);
    }
    async function inflateGzip(encoded, name, expected, signal) {
        let bytes;
        if (typeof Uint8Array.fromBase64 === 'function') bytes = Uint8Array.fromBase64(encoded);
        else {
            const payload = atob(encoded);
            bytes = new Uint8Array(payload.length);
            for (let index = 0; index < bytes.length; index++) bytes[index] = payload.charCodeAt(index);
        }
        let offset = 0;
        const compressed = new ReadableStream({
            pull(controller) {
                const end = Math.min(offset + 64 * 1024, bytes.length);
                if (end > offset) controller.enqueue(bytes.subarray(offset, end));
                offset = end;
                if (offset === bytes.length) controller.close();
            },
        });
        const reader = compressed.pipeThrough(new DecompressionStream('gzip')).getReader();
        const cancel = () => { reader.cancel().catch(() => {}); };
        signal.addEventListener('abort', cancel, { once: true });
        try {
            const decoder = new TextDecoder('utf-8', { fatal: true });
            const parts = [];
            let length = 0;
            while (true) {
                const { value, done } = await reader.read();
                aborted(signal);
                if (done) break;
                length += value.byteLength;
                if (length > expected) throw new Error('Embedded gzip size mismatch: ' + name);
                parts.push(decoder.decode(value, { stream: true }));
            }
            if (length !== expected) throw new Error('Embedded gzip size mismatch: ' + name);
            parts.push(decoder.decode());
            const text = parts.join('');
            return /^fgText[0-9]+$/.test(name) ? text : JSON.parse(text);
        } finally {
            signal.removeEventListener('abort', cancel);
            cancel();
        }
    }
    function textIndexWorkerMain() {
        function sendRows(rows, first) {
            const width = rows[0]?.length;
            if (rows.length < 2 || !Number.isInteger(width) || width < 1 || width > 32 ||
                rows.some(row => !Array.isArray(row) || row.length !== width)) {
                self.postMessage({ first, rows });
                return;
            }
            const changes = new Uint32Array(rows.length - 1);
            const cells = rows[0].slice();
            for (let index = 1; index < rows.length; index++) {
                const previous = rows[index - 1], current = rows[index];
                let mask = 0;
                for (let column = 0; column < width; column++) if (!Object.is(current[column], previous[column])) {
                    mask |= 1 << column;
                    cells.push(current[column]);
                }
                changes[index - 1] = mask;
            }
            self.postMessage({ first, width, changes, cells }, [changes.buffer]);
        }
        self.onmessage = async ({ data: { encoded, expected, batchSize } }) => {
            try {
                const value = await inflateGzip(encoded, 'fgTextIndex', expected, new AbortController().signal);
                if (Array.isArray(value?.rows) && value.rows.length > batchSize) {
                    const rows = value.rows;
                    value.rows = [];
                    self.postMessage({ value, count: rows.length });
                    for (let first = 0; first < rows.length; first += batchSize) {
                        sendRows(rows.slice(first, first + batchSize), first);
                    }
                } else self.postMessage({ value });
                self.postMessage({ done: true });
            } catch (error) {
                self.postMessage({ error: error.message, name: error.name });
            }
        };
        self.postMessage({ ready: true });
    }
    function appendTextIndexRows(target, data, length) {
        if (Array.isArray(data.rows)) {
            if (data.rows.length !== length) return false;
            target.push(...data.rows);
            return true;
        }
        const { width, changes, cells } = data;
        if (!Number.isInteger(width) || width < 1 || width > 32 || length < 2 || !(changes instanceof Uint32Array) ||
            changes.length !== length - 1 || !Array.isArray(cells) || cells.length < width || cells.length > length * width) return false;
        let previous = cells.slice(0, width), offset = width;
        target.push(previous);
        for (let mask of changes) {
            if (width < 32 && mask >>> width) return false;
            const current = previous.slice();
            while (mask) {
                current[31 - Math.clz32(mask & -mask)] = cells[offset++];
                mask &= mask - 1;
            }
            if (offset > cells.length) return false;
            target.push(current);
            previous = current;
        }
        return offset === cells.length;
    }
    function decodeTextIndex(encoded, expected, signal) {
        if (textIndexWorkerUnavailable || !window.Worker || !window.Blob || !window.URL?.createObjectURL) return undefined;
        return new Promise((resolve, reject) => {
            const batchSize = 1024;
            let worker, url, timer, value, count, started = false, received = false;
            const finish = (result, error) => {
                clearTimeout(timer);
                signal.removeEventListener('abort', cancel);
                if (worker) {
                    worker.onmessage = worker.onerror = worker.onmessageerror = null;
                    worker.terminate();
                }
                if (url) URL.revokeObjectURL(url);
                if (error) reject(error);
                else resolve(result);
            };
            const cancel = () => finish(undefined, new DOMException('Data request canceled', 'AbortError'));
            const unavailable = () => {
                textIndexWorkerUnavailable = true;
                finish(undefined);
            };
            try {
                url = URL.createObjectURL(new Blob([aborted.toString(), '\n', inflateGzip.toString(), '\n(',
                    textIndexWorkerMain.toString(), ')()'], { type: 'text/javascript' }));
                worker = new Worker(url, { name: 'FuncGraph Text index' });
                worker.onmessage = ({ data }) => {
                    if (data?.ready && !started) {
                        clearTimeout(timer);
                        URL.revokeObjectURL(url);
                        url = null;
                        started = true;
                        try { worker.postMessage({ encoded, expected, batchSize }); }
                        catch (error) { finish(undefined, error); }
                    } else if (started && typeof data?.error === 'string') {
                        const Constructor = data.name === 'SyntaxError' ? SyntaxError : data.name === 'TypeError' ? TypeError : Error;
                        finish(undefined, new Constructor(data.error));
                    } else if (started && data && !received && Object.prototype.hasOwnProperty.call(data, 'value') &&
                               (data.count === undefined || Number.isSafeInteger(data.count) && data.count > batchSize &&
                                Array.isArray(data.value?.rows) && !data.value.rows.length)) {
                        value = data.value;
                        count = data.count;
                        received = true;
                    } else if (received && data?.done && (count === undefined || value.rows.length === count)) {
                        finish(value);
                    } else if (!(count !== undefined && data?.first === value.rows.length && data.first < count &&
                                 appendTextIndexRows(value.rows, data, Math.min(batchSize, count - data.first)))) {
                        finish(undefined, new Error('Invalid Text index worker result'));
                    }
                };
                worker.onerror = event => {
                    event.preventDefault();
                    if (started) finish(undefined, new Error('Text index worker failed'));
                    else unavailable();
                };
                worker.onmessageerror = () => finish(undefined, new Error('Unable to read the Text index worker result'));
                timer = setTimeout(unavailable, 5000);
                signal.addEventListener('abort', cancel, { once: true });
            } catch (_) { unavailable(); }
        });
    }
    function load(name, options = {}) {
        const signal = options.signal;
        try {
            aborted(signal);
            if (/^fgText[0-9]+$/.test(name) && embedded('fgTextChunks')) {
                const info = textInfo(Number(name.slice('fgText'.length)));
                if (info.name !== name) throw new Error('Invalid Text chunk identity: ' + name);
            }
            if (ready(name)) return Promise.resolve(read(name));
            const element = embedded(name);
            if (element?.type === gzipType) {
                const expected = Number(element.dataset.bytes);
                return request('data:' + name, signal, active => inflateEmbedded(element, name, expected, active),
                    (key, value) => cacheStore(key, value, expected));
            }
        } catch (error) { return Promise.reject(error); }
        let resourceSize;
        return request('data:' + name, signal, async active => {
            const value = await manifest(active);
            if (!Object.prototype.hasOwnProperty.call(value.resources, name)) throw new Error('Report resource not found: ' + name);
            if (/^fgText[0-9]+$/.test(name) && embedded('fgTextChunks') && value.resources[name].format !== 'text') {
                throw new Error('Text chunk resources must use text format');
            }
            if (/^fgText[0-9]+$/.test(name) && embedded('fgTextChunks')) {
                const catalog = textChunks();
                const names = Object.keys(value.resources).filter(name => /^fgText[0-9]+$/.test(name));
                if (names.length !== Math.ceil(catalog.count / catalog.chunk_size) || names.some(name => {
                    const first = Number(name.slice('fgText'.length));
                    return !/^fgText(?:0|[1-9][0-9]*)$/.test(name) || !Number.isSafeInteger(first) ||
                        first >= catalog.count || first % catalog.chunk_size;
                })) throw new Error('Text chunk resources do not match catalog');
            }
            resourceSize = value.resources[name].bytes;
            const config = settings();
            const url = resourceURL(config.manifest, new URL('.', location.href));
            return verified(value.resources[name], new URL('.', url), active);
        }, (key, value) => cacheStore(key, value, resourceSize));
    }
    function sourceInfo(source) {
        if (!Number.isSafeInteger(source) || source < 0) throw new Error('Invalid source identity');
        if (!embedded('fgSourceChunks')) return { name: 'fgSrc', index: source, count: null };
        const catalog = read('fgSourceChunks');
        if (!catalog || catalog.version !== 1 || !Number.isSafeInteger(catalog.count) || catalog.count < 0 ||
            !Number.isSafeInteger(catalog.chunk_size) || catalog.chunk_size <= 0 || source >= catalog.count) {
            throw new Error('Invalid source chunk catalog or identity');
        }
        const first = Math.floor(source / catalog.chunk_size) * catalog.chunk_size;
        return { name: 'fgSrc' + first, index: source - first, count: Math.min(catalog.chunk_size, catalog.count - first) };
    }
    const checkedSources = new WeakSet();
    function sourceValue(info, data) {
        if (!Array.isArray(data) || info.count !== null && data.length !== info.count ||
            !checkedSources.has(data) && data.some(value => typeof value !== 'string')) {
            cacheRelease('data:' + info.name);
            throw new Error('Invalid source chunk contents: ' + info.name);
        }
        checkedSources.add(data);
        return data[info.index] || '';
    }
    function readSource(source) {
        const info = sourceInfo(source);
        return sourceValue(info, read(info.name));
    }
    function loadSource(source, options = {}) {
        try {
            const info = sourceInfo(source);
            return load(info.name, options).then(data => sourceValue(info, data));
        } catch (error) { return Promise.reject(error); }
    }
    const SOURCE_HANDOFF_MS = 120;
    function keepSourceFlight(previous, next) {
        if (!descriptorNode || next === null) return;
        try {
            const {name} = sourceInfo(previous);
            if (embedded(name) || sourceInfo(next).name !== name) return;
            const key = 'data:' + name, flight = requests.get(key);
            if (!flight?.users || flight.handoff) return;
            flight.handoff = setTimeout(() => {
                flight.handoff = null;
                dropUnusedRequest(key, flight);
            }, SOURCE_HANDOFF_MS);
        } catch (_) {}
    }
    function textChunks() {
        if (!embedded('fgTextChunks')) return null;
        const catalog = read('fgTextChunks');
        if (!catalog || catalog.version !== 1 || !Number.isSafeInteger(catalog.count) || catalog.count < 0 ||
            !Number.isSafeInteger(catalog.chunk_size) || catalog.chunk_size <= 0 ||
            !Number.isSafeInteger(catalog.paint_rows) || catalog.paint_rows <= 0 || catalog.chunk_size % catalog.paint_rows) {
            throw new Error('Invalid Text chunk catalog');
        }
        return catalog;
    }
    function textInfo(row) {
        const catalog = textChunks();
        if (!catalog || !Number.isSafeInteger(row) || row < 0 || row >= catalog.count) throw new Error('Invalid Text row identity');
        const first = Math.floor(row / catalog.chunk_size) * catalog.chunk_size;
        return { name: 'fgText' + first, first, count: Math.min(catalog.chunk_size, catalog.count - first), paint_rows: catalog.paint_rows };
    }
    function textValue(info, value) {
        if (typeof value !== 'string') {
            cacheRelease('data:' + info.name);
            throw new Error('Invalid Text chunk contents: ' + info.name);
        }
        return value;
    }
    function loadTextChunk(row, options = {}) {
        try {
            const info = textInfo(row);
            return load(info.name, options).then(value => textValue(info, value));
        } catch (error) { return Promise.reject(error); }
    }
    function textBlocks(info, value, rows) {
        const marker = '<div class="text-chunk" data-chunk-size="' + info.paint_rows + '">';
        const count = Math.ceil(info.count / info.paint_rows);
        const selected = new Set(rows.map(row => Math.floor((row - info.first) / info.paint_rows)));
        const blocks = new Map();
        let start = 0;
        for (let index = 0; index < count; index++) {
            if (!value.startsWith(marker, start)) throw new Error('Invalid Text paint block boundary');
            const next = value.indexOf(marker, start + marker.length);
            const end = next < 0 ? value.length : next;
            if ((index === count - 1) !== (next < 0) || !value.endsWith('</div></div>', end)) {
                throw new Error('Text paint blocks do not match catalog');
            }
            if (selected.has(index)) blocks.set(index, { first: info.first + index * info.paint_rows,
                count: Math.min(info.paint_rows, info.count - index * info.paint_rows), html: value.slice(start, end) });
            start = end;
        }
        return rows.map(row => blocks.get(Math.floor((row - info.first) / info.paint_rows)));
    }
    function loadTextBlocks(rows, options = {}) {
        try {
            aborted(options.signal);
            if (!Array.isArray(rows)) throw new Error('Invalid Text block group');
            if (!rows.length) return Promise.resolve([]);
            const ordinals = Array.from(rows);
            const info = textInfo(ordinals[0]);
            if (ordinals.some(row => textInfo(row).name !== info.name)) throw new Error('Text block group crosses network chunks');
            return load(info.name, options).then(value => {
                aborted(options.signal);
                try { return textBlocks(info, textValue(info, value), ordinals); }
                catch (error) { cacheRelease('data:' + info.name); throw error; }
            });
        } catch (error) { return Promise.reject(error); }
    }
    function loadTextBlock(row, options = {}) {
        return loadTextBlocks([row], options).then(blocks => blocks[0]);
    }
    window.fgData = {
        read, load, ready,
        readSource, loadSource, keepSourceFlight, sourceReady: source => ready(sourceInfo(source).name),
        textChunks, loadTextChunk, loadTextBlock, loadTextBlocks,
        releaseTextChunks: () => { for (const key of Array.from(textCache.entries.keys())) cacheRelease(key); },
        release: name => cacheRelease('data:' + name),
        external: name => !!descriptorNode && !embedded(name),
        length: name => {
            const element = embedded(name);
            if (!element) return 0;
            const first = element.firstChild;
            return first && first === element.lastChild ? first.length : element.textContent.length;
        },
    };
})();
