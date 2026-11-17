#!/usr/bin/env python3
"""Measure actual Overhead button handlers and verify every duration-basis state."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

from benchmark_preparation import fixture as preparation_fixture


def fixture(source, destination, scenario, pairs):
    preparation_fixture(source, destination, 'original' if scenario == 'original' else 'inline', pairs)
    text = destination.read_text()
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', text, re.S)
    data = json.loads(match[2])
    if scenario != 'original':
        data['cols']['adj'] = [6000, 2000] * pairs
        data['overhead'] = {'child': 1000, 'self': 2000, 'source': 'synthetic benchmark'}
        if scenario == 'plain':
            data['inline'] = {}
    if not data['cols'].get('adj'):
        raise ValueError('Report has no overhead estimate: ' + str(source))
    text = text[:match.start(2)] + json.dumps(data, separators=(',', ':')) + text[match.end(2):]
    marker = 'const probe = () => ({ N, NA, SYN, S,'
    text = text.replace(marker, '''const originalSetAdjusted = setAdjusted;
let adjustmentTime = 0;
setAdjusted = function(on) {
    const started = performance.now();
    try { return originalSetAdjusted(on); }
    finally { adjustmentTime = performance.now() - started; }
};
''' + marker + ''' durationState: () => ({sDur, sSelf, sSelfRaw, sDurRaw, sDurAdj,
    maxSelf, maxDuration, selfColorLog, durationColorLog, adjOn}), adjustmentTime: () => adjustmentTime,''', 1)
    destination.write_text(text)
    logical = dict(data, stats=dict(data.get('stats') or {}))
    logical['stats'].pop('build_time', None)
    return dict(calls=data['n'], input_sha256=hashlib.sha256(
        json.dumps(logical, sort_keys=True, separators=(',', ':')).encode()).hexdigest())


def measure(browser, report, detail):
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    page = context.new_page()
    page.set_default_timeout(180000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
    page.add_init_script("localStorage.clear(); localStorage.setItem('theme', 'light');")
    try:
        page.goto(report.as_uri(), wait_until='load')
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        page.wait_for_function("() => !document.getElementById('timelineView').hasAttribute('aria-busy') && fgView('timeline')?.probe")
        if detail:
            page.evaluate("() => fgView('timeline').probe().select(0)")
        page.evaluate('''() => {
            window.overheadFingerprint = async () => {
                const model = fgView('timeline').probe(), state = model.durationState(), digests = {};
                const hash = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
                    .map(value => value.toString(16).padStart(2, '0')).join('');
                for (const key of ['sStart', 'sEnd', 'sDepth', 'sParent', 'sTask', 'sName', 'sFlags', 'sLine', 'sLdAll'])
                    digests[key] = await hash(model[key]);
                for (const key of ['sDur', 'sSelf', 'sSelfRaw', 'sDurRaw', 'sDurAdj']) digests[key] = await hash(state[key]);
                for (const key of ['S', 'SYN', 'TRACKS'])
                    digests[key] = await hash(new TextEncoder().encode(JSON.stringify(model[key])));
                return {N:model.N, NA:model.NA, digests, adjusted:state.adjOn,
                    scales:[state.maxSelf, state.maxDuration, state.selfColorLog, state.durationColorLog]};
            };
        }''')
        measured = page.evaluate('() => overheadFingerprint()')
        adjusted = None
        switches = []
        for ordinal in range(6):
            result = page.evaluate('''async () => {
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                const started = performance.now();
                document.querySelector('[data-act=adj]').click();
                const handler = performance.now() - started;
                const adjustment = fgView('timeline').probe().adjustmentTime();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                return {handler_ms:handler, adjustment_ms:adjustment, frame_ms:performance.now() - started};
            }''')
            current = page.evaluate('() => overheadFingerprint()')
            if ordinal == 0:
                adjusted = current
            expected = adjusted if ordinal % 2 == 0 else measured
            if current != expected:
                raise RuntimeError('Repeated switch changed the complete model')
            for key, value in measured['digests'].items():
                if key not in ('sDur', 'sSelf') and current['digests'][key] != value:
                    raise RuntimeError('Overhead changed measured geometry or raw columns: ' + key)
            result.update(ordinal=ordinal + 1, adjusted=current['adjusted'])
            switches.append(result)
        if errors:
            raise RuntimeError('; '.join(errors))
        return dict(states={'measured': measured, 'adjusted': adjusted}, switches=switches)
    finally:
        context.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--scenario', choices=('original', 'plain', 'inline'), default='plain')
    parser.add_argument('--pairs', type=int, default=120000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--detail', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.pairs < 1 or args.rounds < 1:
        parser.error('--pairs and --rounds must be positive')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fg-overhead-benchmark-') as temporary, sync_playwright() as playwright:
        reports = []
        for index, source in enumerate(args.reports):
            destination = Path(temporary) / f'{index}.html'
            facts = fixture(source.resolve(), destination, args.scenario, args.pairs)
            if reports and facts != reports[0]['facts']:
                raise ValueError('Timeline inputs differ beyond stats.build_time: ' + str(source))
            reports.append(dict(source=str(source.resolve()), path=destination, facts=facts))
        browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        output = dict(browser=browser.version, scenario=args.scenario, pairs=args.pairs, detail=args.detail, runs=[])
        expected = None
        try:
            for iteration in range(args.rounds):
                for report in reports if iteration % 2 == 0 else reversed(reports):
                    result = measure(browser, report['path'], args.detail)
                    if expected is None:
                        expected = result['states']
                    if result['states'] != expected:
                        raise RuntimeError('Complete states differ: ' + report['source'])
                    result.update(report=report['source'], iteration=iteration + 1, fixture=report['facts'])
                    output['runs'].append(result)
        finally:
            browser.close()
        args.output.write_text(json.dumps(output, indent=2) + '\n')
        for run in output['runs']:
            print(run['report'], run['iteration'], json.dumps(run['switches']))


if __name__ == '__main__':
    main()
