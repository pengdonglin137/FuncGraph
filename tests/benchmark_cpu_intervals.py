#!/usr/bin/env python3
"""Compare CPU interrupt query/paint work, preserving full previews and pixels."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import tempfile

from benchmark_cpu_hover import fixture as hover_fixture, instrument as hover_instrument


def fixture(text, count, nested=False):
    text = hover_fixture(text, count)
    if count is None:
        return text
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', text, re.S)
    data = json.loads(match[2])
    span = data['cols']['dur'][0]
    data['tasks'][0]['runs'] = [[start, min(start + 128000, span), 0] for start in range(0, span, 128000)]
    if nested:
        for ordinal in range(count):
            position = ordinal + 1
            data['cols']['dur'][position] = min((5000, 400, 100)[ordinal % 3], span - (ordinal * 2000 + 1000))
            if ordinal % 3:
                data['cols']['parent'][position] = position - ordinal % 3
                data['cols']['depth'][position] = 2
    return text[:match.start(2)] + json.dumps(data, separators=(',', ':')) + text[match.end(2):]


def instrument(text):
    text = hover_instrument(text)
    text = text.replace('function drawCpuTrack(top, vs, ve, k) {',
                        'function measuredCpuTrack(top, vs, ve, k) {', 1)
    marker = 'const CPU_HAS_SWITCHES ='
    build = '''
window.cpuIntervalBuild = {calls:0, records:0, milliseconds:0, yields:0};
const originalCpuIntervalIndex = indexSchedulingIntervals;
indexSchedulingIntervals = function*(...parameters) {
    cpuIntervalBuild.calls++;
    cpuIntervalBuild.records += parameters[0].length;
    const iterator = originalCpuIntervalIndex(...parameters);
    for (;;) {
        const started = performance.now(), step = iterator.next();
        cpuIntervalBuild.milliseconds += performance.now() - started;
        if (step.done) return step.value;
        cpuIntervalBuild.yields++;
        yield step.value;
    }
};
'''
    text = text.replace(marker, build + marker, 1)
    wrapper = '''
let measuringCpuPaint = false;
function drawCpuTrack(...parameters) {
    if (!measuringCpuPaint) return measuredCpuTrack(...parameters);
    const started = performance.now();
    measuringCpuTip = true;
    try { return measuredCpuTrack(...parameters); }
    finally {
        cpuHoverStats.calls++;
        cpuHoverStats.milliseconds += performance.now() - started;
        measuringCpuTip = false;
    }
}
function cpuIntervalPaint(start, end, linear = false) {
    const original = visitSchedulingIntervals;
    if (linear) visitSchedulingIntervals = (records, index, left, right, visit, timeColumn = 1) => {
        for (const record of records)
            if (record[timeColumn] <= right && record[timeColumn + 1] >= left && visit(record)) return record;
        return null;
    };
    try {
        ctx.clearRect(0, 0, W, H);
        measuringCpuPaint = true;
        drawCpuTrack(0, start, end, W / (end - start));
        return elCv.toDataURL();
    } finally { visitSchedulingIntervals = original; measuringCpuPaint = false; }
}
function cpuTestIntervalIndex(records, timeColumn) {
    const iterator = indexSchedulingIntervals(records, timeColumn);
    let step;
    do { step = iterator.next(); } while (!step.done);
    return step.value;
}
'''
    text = text.replace('function tipCpu(segment) {', wrapper + 'function tipCpu(segment) {', 1)
    return text.replace('cpuTip:tipCpu,', '''cpuTip:tipCpu, cpuPaint:cpuIntervalPaint,
        intervalIndex:cpuTestIntervalIndex, intervalVisit:visitSchedulingIntervals,
        irqIndex:typeof CPU_IRQ_INDEX === 'undefined' ? null : CPU_IRQ_INDEX,''', 1)


def measure(browser, report, moves):
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        ready = page.evaluate('''async () => {
            const started = performance.now();
            await fgSwitchView('timeline');
            const elapsed = performance.now() - started;
            for (let frame=0;frame<2;frame++) await new Promise(requestAnimationFrame);
            return elapsed;
        }''')
        result = page.evaluate('''moves => {
            const model = fgView('timeline').probe(), segments = [];
            for (const [cpu, runs] of model.CPU_SEGS) for (let ordinal=0;ordinal<runs.s.length;ordinal++)
                segments.push({cpu,j:ordinal,t:runs.t[ordinal],s:runs.s[ordinal],e:runs.e[ordinal]});
            if (!segments.length) throw new Error('No CPU segments');
            const reset = () => Object.assign(cpuHoverStats,{calls:0,lookups:0,records:0,milliseconds:0});
            const visits = Array.from({length:moves},(_,ordinal)=>segments[Math.floor(ordinal*segments.length/moves)]);
            const previews = [], pixels = [];
            reset();
            for (const segment of visits) previews.push(model.cpuTip(segment));
            const hover = {...cpuHoverStats};
            reset();
            for (const segment of visits) pixels.push(model.cpuPaint(segment.s,segment.e));
            const paint = {...cpuHoverStats};
            let start=Infinity, end=-Infinity;
            for (const segment of segments) { start=Math.min(start,segment.s); end=Math.max(end,segment.e); }
            reset();
            previews.push(model.cpuTip({...segments[0],s:start,e:end}));
            const fullHover={...cpuHoverStats};
            reset();
            pixels.push(model.cpuPaint(start,end));
            const fullPaint={...cpuHoverStats}, timings={};
            cpuHoverStats.countRecords=false;
            model.clearCpuSummary();
            reset();
            for (let ordinal=0;ordinal<visits.length;ordinal++)
                if (model.cpuTip(visits[ordinal])!==previews[ordinal]) throw new Error('Timing changed preview');
            timings.hover={...cpuHoverStats};
            reset();
            for (let ordinal=0;ordinal<visits.length;ordinal++)
                if (model.cpuPaint(visits[ordinal].s,visits[ordinal].e)!==pixels[ordinal]) throw new Error('Timing changed pixels');
            timings.paint={...cpuHoverStats};
            model.clearCpuSummary();
            reset();
            if (model.cpuTip({...segments[0],s:start,e:end})!==previews.at(-1)) throw new Error('Full timing changed preview');
            timings.fullHover={...cpuHoverStats};
            reset();
            if (model.cpuPaint(start,end)!==pixels.at(-1)) throw new Error('Full timing changed pixels');
            timings.fullPaint={...cpuHoverStats};
            return {hover,paint,fullHover,fullPaint,timings,visits,previews,pixels,build:{...cpuIntervalBuild},
                indexBytes:model.irqIndex ? Array.from(model.irqIndex.values()).reduce((sum,index)=>sum+(index.byteLength||0),0) : 0,
                input:JSON.stringify(fgData.read('tlData'))};
        }''', moves)
        if errors:
            raise RuntimeError('; '.join(errors))
        data = json.loads(result.pop('input'))
        data.get('stats', {}).pop('build_time', None)
        result['input_sha256'] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        for key in ('previews', 'pixels'):
            result[key + '_sha256'] = hashlib.sha256(json.dumps(result.pop(key)).encode()).hexdigest()
        result['timeline_ready_ms'] = ready
        return result
    finally:
        context.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--interrupts', type=int)
    parser.add_argument('--nested', action='store_true')
    parser.add_argument('--moves', type=int, default=24)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if min(arguments.moves, arguments.rounds) < 1 or arguments.interrupts is not None and arguments.interrupts < 1:
        parser.error('Counts must be positive')
    samples = {'before': [], 'after': []}
    with tempfile.TemporaryDirectory(prefix='fg-cpu-intervals-') as directory, sync_playwright() as playwright:
        reports = {}
        for name in samples:
            report = Path(directory, name + '.html')
            report.write_text(instrument(fixture(getattr(arguments, name).read_text(), arguments.interrupts, arguments.nested)))
            reports[name] = report
        browser = playwright.chromium.launch()
        try:
            for ordinal in range(arguments.rounds):
                for name in ('before', 'after') if ordinal % 2 == 0 else ('after', 'before'):
                    result = measure(browser, reports[name], arguments.moves)
                    for previous in [record for records in samples.values() for record in records]:
                        for key in ('input_sha256', 'visits', 'previews_sha256', 'pixels_sha256'):
                            if result[key] != previous[key]:
                                raise RuntimeError('Comparison differs: ' + key)
                    samples[name].append(result)
            version = browser.version
        finally:
            browser.close()
    result = dict(browser=version, synthetic_interrupts=arguments.interrupts, nested=arguments.nested,
                  moves=arguments.moves, rounds=arguments.rounds, samples=samples,
                  medians={name: {phase: {key: median(record['timings'][phase][key] if key == 'milliseconds' else record[phase][key]
                                                     for record in records)
                                        for key in ('calls', 'lookups', 'records', 'milliseconds')}
                                  for phase in ('hover', 'paint', 'fullHover', 'fullPaint')} for name, records in samples.items()},
                  scope='Record accesses counted separately from direct formatter/track paint timings without per-record proxies; not end-to-end pointer latency')
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['medians'], indent=2))


if __name__ == '__main__':
    main()
