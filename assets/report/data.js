(function () {
    const descriptorNode = document.getElementById('fgDataManifest');
    const embedded = name => {
        const element = document.getElementById(name);
        return element && element.tagName === 'SCRIPT' && element.type === 'application/json' ? element : null;
    };
    const cache = new Map();
    const sourceCache = new Map();
    const SOURCE_CACHE_CHUNKS = 8;
    const SOURCE_CACHE_BYTES = 16 * 1024 * 1024;
    let sourceCacheBytes = 0;
    const requests = new Map();
    let descriptor = null;
    function cacheRelease(key) {
        if (sourceCache.has(key)) {
            sourceCacheBytes -= sourceCache.get(key);
            sourceCache.delete(key);
        }
        return cache.delete(key);
    }
    function cacheRead(key) {
        if (sourceCache.has(key)) {
            const bytes = sourceCache.get(key);
            sourceCache.delete(key);
            sourceCache.set(key, bytes);
        }
        return cache.get(key);
    }
    function cacheStore(key, value, bytes) {
        const source = bytes !== undefined && /^data:fgSrc(?:0|[1-9][0-9]*)$/.test(key) && embedded('fgSourceChunks');
        if (source) {
            const info = sourceInfo(Number(key.slice('data:fgSrc'.length)));
            if ('data:' + info.name !== key) throw new Error('Invalid source chunk identity: ' + key);
            sourceValue(info, value);
        }
        cacheRelease(key);
        if (source) {
            if (bytes > SOURCE_CACHE_BYTES) return;
            while (sourceCache.size >= SOURCE_CACHE_CHUNKS || sourceCacheBytes + bytes > SOURCE_CACHE_BYTES) {
                cacheRelease(sourceCache.keys().next().value);
            }
            sourceCache.set(key, bytes);
            sourceCacheBytes += bytes;
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
        const digest = await window.crypto.subtle.digest('SHA-256', bytes);
        const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
        aborted(signal);
        if (hash !== resource.sha256) throw new Error('Report resource checksum mismatch: ' + resource.file);
        const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
        return resource.format === 'json' ? JSON.parse(text) : text;
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
                if (requests.get(key) === flight) requests.delete(key);
            });
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
                if (!flight.users && requests.get(key) === flight) {
                    requests.delete(key);
                    flight.controller.abort();
                }
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
        return cache.has('data:' + name) || !!embedded(name);
    }
    function read(name) {
        const key = 'data:' + name;
        if (cache.has(key)) return cacheRead(key);
        const element = embedded(name);
        if (!element) throw new Error('Report data ' + name + ' is not ready; await fgData.load() first');
        const value = JSON.parse(element.textContent);
        cacheStore(key, value);
        return value;
    }
    function load(name, options = {}) {
        const signal = options.signal;
        try {
            aborted(signal);
            if (ready(name)) return Promise.resolve(read(name));
        } catch (error) { return Promise.reject(error); }
        let resourceSize;
        return request('data:' + name, signal, async active => {
            const value = await manifest(active);
            if (!Object.prototype.hasOwnProperty.call(value.resources, name)) throw new Error('Report resource not found: ' + name);
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
    window.fgData = {
        read, load, ready,
        readSource, loadSource, sourceReady: source => ready(sourceInfo(source).name),
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
