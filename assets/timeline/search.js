const SEARCH_BATCH = 4096;

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
