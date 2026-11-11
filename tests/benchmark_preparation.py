#!/usr/bin/env python3
"""Measure Timeline generator steps and verify complete model identity."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

from playwright.sync_api import sync_playwright


def fixture(report, destination, scenario, pairs):
    source = report.read_bytes()
    text = source.decode('utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', text, re.S)
    if not match:
        raise ValueError('Expected a single-file Timeline report: ' + str(report))
    data = json.loads(match[2])
    if scenario != 'original':
        count = pairs * 2
        starts = [value for index in range(pairs) for value in (10000 if index else 0, 100)]
        for index in range(2, count, 2):
            starts[index] -= 100
        data.update(n=count, mode='abs', strings=['parent', 'child', 'caller+0x4/0x40'],
                    tasks=[dict(pid=1, comm='preparation-benchmark', kind='task', cpu=-1)],
                    errs=[], chains=[], errnames={}, offcpu=[], resched=[], wakeups=[], irqoff=[],
                    sections=[], atomic=[], lost=[], overhead=None, inline={}, sources={})
        for key in ('uftrace', 'tail_calls'):
            data.pop(key, None)
        data['cols'] = dict(start=starts, dur=[9000, 4000] * pairs, depth=[0, 4] * pairs,
                            task=[0] * count, flags=[0] * count, name=[0, 1] * pairs,
                            parent=[value for index in range(pairs) for value in (-1, index * 2)],
                            line=list(range(1, count + 1)), eline=list(range(1, count + 1)),
                            caller=[-1, 2] * pairs, ret=None, args=None, module=None, cpu=None)
        if scenario in ('inline', 'interleaved'):
            data['cols']['depth'] = [0, 1] * pairs
            data['inline'] = {'2': [['parent', 'parent.c', 1], ['outer', 'outer.h', 2],
                                    ['middle', 'middle.h', 3], ['inner', 'inner.h', 4]]}
            if scenario == 'interleaved':
                data['cols']['caller'] = [value for index in range(pairs)
                                          for value in (-1, 2 if index % 2 else -1)]
        text = text[:match.start(2)] + json.dumps(data, separators=(',', ':')) + text[match.end(2):]
    marker = 'const probe = () => ({ N, NA, SYN, S,'
    if text.count(marker) != 1:
        raise ValueError('Cannot instrument the complete Timeline model')
    text = text.replace(marker, marker + ' sFlags, sLine, sLdAll, sDur, sSelf, sSelfRaw,', 1)
    destination.write_text(text, encoding='utf-8')
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
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
    page.add_init_script('''localStorage.clear();
    localStorage.setItem('theme', 'light');
    window.addEventListener('DOMContentLoaded', () => {
        window.preparationSteps = [];
        window.preparationLongTasks = [];
        window.preparationStarted = performance.now();
        window.preparationStaged = fgViews.timeline.staged;
        const original = __tlSteps;
        __tlSteps = function* () {
            const steps = original();
            let label = 'Starting Timeline…', input;
            try {
                while (true) {
                    const start = performance.now(), result = steps.next(input);
                    preparationSteps.push({label, duration:performance.now() - start});
                    if (result.done) { window.preparationCompleted = performance.now(); return result.value; }
                    label = typeof result.value === 'string' ? result.value : result.value.label;
                    input = yield result.value;
                }
            } finally { steps.return(); }
        };
        new PerformanceObserver(list => {
            for (const entry of list.getEntries()) preparationLongTasks.push({start:entry.startTime, duration:entry.duration});
        }).observe({type:'longtask', buffered:true});
        window.preparationReady = fgSwitchView('timeline');
    });''')
    try:
        page.goto(report.as_uri(), wait_until='load')
        page.wait_for_function('''fgCurrentView() === 'timeline' &&
            !document.getElementById('timelineView').hasAttribute('aria-busy') && fgView('timeline')?.probe''')
        metrics = page.evaluate('''async () => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const finished = performance.now();
            await new Promise(resolve => setTimeout(resolve, 50));
            return {frame_ms:finished - preparationStarted, elapsed_ms:preparationCompleted - preparationStarted,
                staged:preparationStaged, steps:preparationSteps,
                longtasks:preparationLongTasks.filter(task => task.start >= preparationStarted && task.start < finished)};
        }''')
        model = page.evaluate('''async () => {
            const model = fgView('timeline').probe(), digests = {};
            const hash = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
                .map(value => value.toString(16).padStart(2, '0')).join('');
            for (const key of ['sStart', 'sEnd', 'sDepth', 'sParent', 'sTask', 'sName',
                    'sFlags', 'sLine', 'sLdAll', 'sDur', 'sSelf', 'sSelfRaw']) digests[key] = await hash(model[key]);
            for (const key of ['S', 'SYN']) digests[key] = await hash(new TextEncoder().encode(JSON.stringify(model[key])));
            digests.tracks = await hash(new TextEncoder().encode(JSON.stringify(model.TRACKS.map(track => ({
                lo:track.lo, hi:track.hi, maxDT:track.maxDT, maxDI:track.maxDI, busy:track.busy,
                rowsT:track.rowsT.map(row => Array.from(row)), rowsI:track.rowsI.map(row => Array.from(row)),
                off:track.off, rs:track.rs, io:track.io, errs:track.errs, cpus:Array.from(track.cpus)
            })))));
            return {calls:model.N, frames:model.NA, tracks:model.TRACKS.length, digests};
        }''')
        if errors:
            raise RuntimeError('; '.join(errors))
        metrics['model'] = model
        metrics['longest_step_ms'] = max(step['duration'] for step in metrics['steps'])
        metrics['longest_task_ms'] = max((task['duration'] for task in metrics['longtasks']), default=0)
        return metrics
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--scenario', choices=('original', 'gaps', 'inline', 'interleaved'), default='original')
    parser.add_argument('--pairs', type=int, default=60000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.pairs < 1 or args.rounds < 1:
        parser.error('--pairs and --rounds must be positive')
    with tempfile.TemporaryDirectory(prefix='fg-preparation-benchmark-') as temporary, sync_playwright() as playwright:
        reports = []
        for index, source in enumerate(args.reports):
            destination = Path(temporary) / f'{index}.html'
            facts = fixture(source.resolve(), destination, args.scenario, args.pairs)
            if reports and facts['input_sha256'] != reports[0]['facts']['input_sha256']:
                raise ValueError('Timeline inputs differ beyond stats.build_time: ' + str(source))
            reports.append(dict(source=str(source.resolve()), path=destination, facts=facts))
        browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        output = dict(browser=browser.version, scenario=args.scenario, pairs=args.pairs, runs=[])
        expected = None
        try:
            for iteration in range(args.rounds):
                for report in reports if iteration % 2 == 0 else reversed(reports):
                    result = measure(browser, report['path'])
                    if expected is None:
                        expected = result['model']
                    if result['model'] != expected:
                        raise RuntimeError('Complete models differ: ' + report['source'])
                    result.update(report=report['source'], iteration=iteration + 1, fixture=report['facts'])
                    output['runs'].append(result)
                    args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(f"{report['source']}: frame {result['frame_ms']:.1f} ms, "
                          f"longest step {result['longest_step_ms']:.1f} ms, "
                          f"longest task {result['longest_task_ms']:.1f} ms", flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
