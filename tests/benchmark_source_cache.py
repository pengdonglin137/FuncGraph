"""Replay a report's complete source pool through real HTTP and the browser provider.

This measures retained serialized resource bytes, not heap, RSS or report latency.
Use --scripts to compare exact provider revisions with the same source payloads.
"""
import argparse
import hashlib
import json
from pathlib import Path

import helpers
from test_data_provider import DataProviderTest, encode, sync_playwright


def replay(script, sources):
    case = DataProviderTest('test_source_chunks_load_only_the_requested_group_and_share_cache')
    case.setUp()
    try:
        current = (Path(helpers.ROOT) / 'assets/report/data.js').read_text(encoding='utf-8')
        if case.html.count(current) != 1:
            raise ValueError('The generated harness must contain exactly one current provider')
        case.html = case.html.replace(current, script.read_text(encoding='utf-8'), 1)
        chunks = {'fgSrc' + str(first): encode(sources[first:first + 64]) for first in range(0, len(sources), 64)}
        case.payloads.update(chunks)
        case.publish(source_catalog=dict(version=1, count=len(sources), chunk_size=64))
        result = case.page.evaluate('''async count => {
            const hashes = [];
            let records = 0;
            for (let first = 0; first < count; first += 64) {
                const source = await fgData.load('fgSrc' + first);
                if (fgData.readSource(first) !== source[0]) throw new Error('Source identity mismatch');
                records += source.length;
                const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(source)));
                hashes.push(Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join(''));
            }
            const retained = [];
            for (let first = 0; first < count; first += 64) if (fgData.sourceReady(first)) retained.push(first);
            if (count) await fgData.loadSource(0);
            return {records, hashes, retained};
        }''', len(sources))
        if result.pop('hashes') != [hashlib.sha256(body).hexdigest() for body in chunks.values()]:
            raise ValueError('Decoded source contents differ from the original resource payloads')
        if result['records'] != len(sources):
            raise ValueError('Incomplete source replay')
        result.update(script=str(script), script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
                      chunks=len(chunks), total_resource_bytes=sum(map(len, chunks.values())),
                      retained_chunks=len(result['retained']),
                      retained_resource_bytes=sum(len(chunks['fgSrc' + str(first)]) for first in result['retained']),
                      first_chunk_requests=case.count('fgSrc0.json'), browser=case.browser.version)
        return result
    finally:
        case.tearDown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path, help='single-file HTML containing the original fgSrc pool')
    parser.add_argument('--scripts', type=Path, nargs='+', default=[Path(helpers.ROOT) / 'assets/report/data.js'])
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if sync_playwright is None:
        parser.error('Playwright and Chromium are required')
    sources = helpers.json_script(arguments.report.read_text(encoding='utf-8'), 'fgSrc')
    if not isinstance(sources, list) or any(not isinstance(source, str) for source in sources):
        parser.error('The report must contain an embedded source-string array')
    result = dict(report=str(arguments.report.resolve()), report_sha256=hashlib.sha256(arguments.report.read_bytes()).hexdigest(),
                  basis='provider-only HTTP replay; verified serialized UTF-8 JSON bytes, not heap, RSS or end-to-end latency', runs=[])
    DataProviderTest.setUpClass()
    try:
        for script in arguments.scripts:
            run = replay(script.resolve(), sources)
            result['runs'].append(run)
            arguments.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
            print(script, run['chunks'], 'chunks;', run['retained_chunks'], 'retained;',
                  run['retained_resource_bytes'], 'retained resource bytes', flush=True)
    finally:
        DataProviderTest.tearDownClass()


if __name__ == '__main__':
    main()
