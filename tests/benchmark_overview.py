#!/usr/bin/env python3
"""Compare overview rebuild work while verifying complete model and ruler fidelity."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import tempfile


def instrument(text):
    marker = 'function buildOverview() {'
    if text.count(marker) != 1:
        raise ValueError('Cannot instrument the Timeline overview builder')
    start = text.index(marker) + len(marker)
    closing = text.index('\n}', start) + 1
    text = text[:start] + '\n    const overviewStarted = performance.now();' + text[start:closing] + '''
    (window.overviewBuilds ||= []).push({width:W, duration:performance.now() - overviewStarted});
''' + text[closing:]
    marker = 'const probe = () => ({ N, NA, SYN, S,'
    if text.count(marker) != 1:
        raise ValueError('Cannot inspect the complete Timeline model')
    return text.replace(marker, marker + ''' sFlags, sLine, sLdAll, sDur, sSelf, sSelfRaw,
        overview: () => ({density:ovDens, maximum:ovMax, width:W, start:tMin, end:tMax, tracks:ORDER}),''', 1)


def fixture(report, destination, scenario='original', count=10000):
    source = report.read_bytes()
    text = source.decode('utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', text, re.S)
    if not match:
        raise ValueError('Expected a single-file Timeline report: ' + str(report))
    data = json.loads(match[2])
    if scenario != 'original':
        flat = scenario == 'flat'
        task_count = 1 if flat else count
        data.update(n=count, mode='abs', strings=['overview_work'],
                    tasks=[dict(pid=index + 1, comm='overview_task', kind=0, cpu=-1)
                           for index in range(task_count)],
                    errs=[], chains=[], errnames={}, offcpu=[], resched=[], wakeups=[], irqoff=[],
                    sections=[], atomic=[], lost=[], overhead=None, inline={}, sources={})
        for key in ('uftrace', 'tail_calls', 'kvm'):
            data.pop(key, None)
        data['cols'] = dict(start=[0] + ([1000] if flat else [0]) * (count - 1),
                            dur=[1000 if flat else 1000000] * count, depth=[0] * count,
                            task=[0] * count if flat else list(range(count)), flags=[0] * count,
                            name=[0] * count, parent=[-1] * count, line=list(range(1, count + 1)),
                            eline=list(range(1, count + 1)), caller=None, ret=None, args=None,
                            module=None, cpu=None)
        data['stats'].update(tasks=task_count, slices=count)
        text = text[:match.start(2)] + json.dumps(data, separators=(',', ':')) + text[match.end(2):]
    destination.write_text(instrument(text), encoding='utf-8')
    logical = dict(data, stats=dict(data.get('stats') or {}))
    logical['stats'].pop('build_time', None)
    return dict(calls=data['n'], source_bytes=len(source), source_sha256=hashlib.sha256(source).hexdigest(),
                input_sha256=hashlib.sha256(json.dumps(logical, sort_keys=True, separators=(',', ':')).encode()).hexdigest())


def measure(browser, report):
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    page = context.new_page()
    page.set_default_timeout(180000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => await fgSwitchView('timeline')")
        result = page.evaluate('''async () => {
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            await document.fonts.ready;
            await settle();
            const model = fgView('timeline').probe();
            const sha = async value => {
                const bytes = new TextEncoder().encode(JSON.stringify(value));
                const hash = await crypto.subtle.digest('SHA-256', bytes);
                return Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2,'0')).join('');
            };
            const modelHash = () => {
                const logicalInput = {...fgData.read('tlData'),stats:{...fgData.read('tlData').stats}};
                delete logicalInput.stats.build_time;
                return sha({input:logicalInput, N:model.N, NA:model.NA, S:model.S, SYN:model.SYN,
                    columns:[model.sStart,model.sEnd,model.sDepth,model.sTask,model.sName,model.sParent,model.sFlags,
                        model.sLine,model.sLdAll,model.sDur,model.sSelf,model.sSelfRaw].map(column => Array.from(column)),
                    rows:model.TRACKS.map(track => [track.rowsT.map(row => Array.from(row)),track.rowsI.map(row => Array.from(row))])});
            };
            const density = () => { const value=model.overview(); return {width:value.width,
                maximum:value.maximum,values:Array.from(value.density)}; };
            const ruler = () => document.querySelector('.tl-head-canvas').toDataURL();
            const original = await modelHash(), initialDensity = density(), pixels = ruler();
            const initialBuilds = overviewBuilds.slice(), phases = {};
            for (const [phase, repetitions] of [['same_size',8],['theme',4]]) {
                overviewBuilds.length = 0;
                const frames = [];
                for (let index=0;index<repetitions;index++) {
                    const start=performance.now();
                    if (phase === 'same_size') fgView('timeline').resize();
                    else document.documentElement.dataset.theme = index % 2 ? 'light' : 'dark';
                    await settle();
                    frames.push(performance.now()-start);
                }
                if (JSON.stringify(density()) !== JSON.stringify(initialDensity) || ruler() !== pixels)
                    throw new Error(phase + ' changed the overview or ruler pixels');
                phases[phase] = {builds:overviewBuilds.slice(),feedback_ms:frames};
            }
            if (await modelHash() !== original) throw new Error('Interaction changed the complete Timeline model');
            window.overviewModelHash = modelHash;
            window.overviewDensity = density;
            window.overviewRuler = ruler;
            window.overviewSettle = settle;
            return {model_sha256:original, density:initialDensity, ruler:pixels, initial_builds:initialBuilds,phases,
                calls:model.N,frames:model.NA,tracks:model.TRACKS.length};
        }''')
        result['width_changes'] = []
        for width in (1240, 1500):
            page.evaluate('overviewBuilds.length = 0')
            page.set_viewport_size({'width': width, 'height': 900})
            page.evaluate('overviewSettle()')
            result['width_changes'].append(page.evaluate('''() => ({builds:overviewBuilds.slice(),
                density:overviewDensity(),ruler:overviewRuler()})'''))
        if page.evaluate('overviewModelHash()') != result['model_sha256']:
            raise RuntimeError('Width changes altered the complete Timeline model')
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        context.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--scenario', choices=('original', 'flat', 'parallel'), default='original')
    parser.add_argument('--count', type=int, default=10000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.count < 1 or arguments.rounds < 1:
        parser.error('--count and --rounds must be positive')
    samples = {'before': [], 'after': []}
    with tempfile.TemporaryDirectory(prefix='fg-overview-benchmark-') as directory:
        reports = {name: Path(directory, name + '.html') for name in samples}
        inputs = {name: fixture(getattr(arguments,name), reports[name], arguments.scenario, arguments.count)
                  for name in samples}
        if inputs['before']['input_sha256'] != inputs['after']['input_sha256']:
            raise RuntimeError('Input payloads differ beyond generated build time')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            version = browser.version
            try:
                for ordinal in range(arguments.rounds):
                    for name in ('before', 'after') if ordinal % 2 == 0 else ('after', 'before'):
                        result = measure(browser, reports[name].resolve())
                        for previous in [record for records in samples.values() for record in records]:
                            for key in ('model_sha256', 'density', 'ruler', 'calls', 'frames', 'tracks'):
                                if result[key] != previous[key]:
                                    raise RuntimeError('Complete model/overview mismatch: ' + key)
                            for current_width, previous_width in zip(result['width_changes'], previous['width_changes']):
                                if current_width['density'] != previous_width['density'] or current_width['ruler'] != previous_width['ruler']:
                                    raise RuntimeError('Width-specific overview mismatch')
                        samples[name].append(result)
            finally:
                browser.close()
    for records in samples.values():
        for record in records:
            record['density_sha256'] = hashlib.sha256(json.dumps(record.pop('density'),sort_keys=True).encode()).hexdigest()
            record['ruler_sha256'] = hashlib.sha256(record.pop('ruler').encode()).hexdigest()
            for width in record['width_changes']:
                width['density_sha256'] = hashlib.sha256(json.dumps(width.pop('density'),sort_keys=True).encode()).hexdigest()
                width['ruler_sha256'] = hashlib.sha256(width.pop('ruler').encode()).hexdigest()
    result = dict(browser=version, viewport=dict(width=1500,height=900), scenario=arguments.scenario,
                  rounds=arguments.rounds, inputs=inputs, samples=samples,
                  scope='Overview computation and two-rAF feedback, not startup, physical display latency or peak memory',
                  medians={name:{phase:dict(builds=median(len(record['phases'][phase]['builds']) for record in records),
                      build_ms=median(sum(item['duration'] for item in record['phases'][phase]['builds']) for record in records),
                      feedback_ms=median(median(record['phases'][phase]['feedback_ms']) for record in records))
                      for phase in ('same_size','theme')} for name,records in samples.items()})
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['medians'], indent=2))


if __name__ == '__main__':
    main()
