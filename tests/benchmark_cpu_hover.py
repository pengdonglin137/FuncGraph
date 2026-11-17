#!/usr/bin/env python3
"""Measure CPU tooltip formatting, not end-to-end pointer or startup latency."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import tempfile


def instrument(text):
    start = text.index('function tipCpu(c) {')
    stop = text.index('\nfunction revealTaskTrack', start)
    original = text[start:stop].replace('function tipCpu(c)', 'function measuredCpuTip(c)', 1)
    wrapper = '''
window.cpuHoverStats = {calls:0, lookups:0, records:0, milliseconds:0};
let measuringCpuTip = false;
const cpuHoverGet = CPU_IRQ.get.bind(CPU_IRQ);
const cpuHoverRecords = new WeakMap();
CPU_IRQ.get = cpu => {
    const records = cpuHoverGet(cpu);
    if (measuringCpuTip) {
        cpuHoverStats.lookups++;
        if (records && cpuHoverStats.countRecords !== false) {
            if (!cpuHoverRecords.has(records)) cpuHoverRecords.set(records, new Proxy(records, {
                get(target, key, receiver) {
                    if (typeof key === 'string' && /^(0|[1-9][0-9]*)$/.test(key)) cpuHoverStats.records++;
                    return Reflect.get(target, key, receiver);
                }
            }));
            return cpuHoverRecords.get(records);
        }
    }
    return records;
};
function tipCpu(segment) {
    const started = performance.now();
    measuringCpuTip = true;
    try { return measuredCpuTip(segment); }
    finally {
        cpuHoverStats.calls++;
        cpuHoverStats.milliseconds += performance.now() - started;
        measuringCpuTip = false;
    }
}
'''
    text = text[:start] + original + wrapper + text[stop:]
    marker = 'const probe = () => ({ N, NA, SYN, S,'
    if text.count(marker) != 1:
        raise ValueError('Cannot instrument the Timeline probe')
    return text.replace(marker, marker + ''' CPU_IRQ, CPU_SEGS, CPU_LIST, cpuTip:tipCpu,
        clearCpuSummary:()=>{ if (typeof cpuIrqSummary !== 'undefined') cpuIrqSummary=null; },''', 1)


def fixture(text, count):
    if count is None:
        return text
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', text, re.S)
    if not match:
        raise ValueError('Expected a single-file report')
    data = json.loads(match[2])
    span = count * 2000 + 1000
    data.update(n=count + 1, mode='abs', strings=['cpu_work', 'interrupt_work'],
                tasks=[dict(pid=42, comm='cpu_hover', kind=0, cpu=0, runs=[[0, span, 0]])],
                errs=[], chains=[], errnames={}, offcpu=[], resched=[], wakeups=[], irqoff=[],
                sections=[], atomic=[], lost=[], overhead=None, inline={}, sources={})
    for key in ('uftrace', 'tail_calls', 'kvm'):
        data.pop(key, None)
    data['caps']['sched_events'] = True
    data['cols'] = dict(start=[0, 1000] + [2000] * (count - 1), dur=[span] + [400] * count,
                        depth=[0] + [1] * count, task=[0] * (count + 1),
                        flags=[0] + [index % 3 + 1 for index in range(count)],
                        name=[0] + [1] * count, parent=[-1] + [0] * count,
                        line=list(range(1, count + 2)), eline=list(range(1, count + 2)),
                        cpu=[0] * (count + 1), caller=None, ret=None, args=None, module=None)
    data['stats'].update(tasks=1, slices=count + 1)
    return text[:match.start(2)] + json.dumps(data, separators=(',', ':')) + text[match.end(2):]


def measure(browser, report, moves):
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => await fgSwitchView('timeline')")
        result = page.evaluate('''moves => {
            const model = fgView('timeline').probe();
            let segment = null;
            for (const [cpu, runs] of model.CPU_SEGS) for (let index=0;index<runs.s.length;index++) {
                const candidate = {cpu, j:index, t:runs.t[index], s:runs.s[index], e:runs.e[index]};
                if (!segment || candidate.e-candidate.s > segment.e-segment.s) segment = candidate;
            }
            if (!segment) throw new Error('Report has no CPU task segments');
            const before = JSON.stringify(fgData.read('tlData'));
            const reset = () => Object.assign(cpuHoverStats,{calls:0,lookups:0,records:0,milliseconds:0});
            reset();
            const html = model.cpuTip(segment), cold = {...cpuHoverStats};
            reset();
            for (let index=0;index<moves;index++)
                if (model.cpuTip({...segment}) !== html) throw new Error('CPU tooltip content changed');
            const repeated = {...cpuHoverStats};
            cpuHoverStats.countRecords = false;
            model.clearCpuSummary();
            reset();
            if (model.cpuTip(segment) !== html) throw new Error('CPU timing changed content');
            const coldTiming = {...cpuHoverStats};
            reset();
            for (let index=0;index<moves;index++)
                if (model.cpuTip({...segment}) !== html) throw new Error('Repeated timing changed content');
            if (JSON.stringify(fgData.read('tlData')) !== before) throw new Error('Recorded data changed');
            return {segment,cold,repeated,timings:{cold:coldTiming,repeated:{...cpuHoverStats}},html,input:before,
                interrupts:Array.from(model.CPU_IRQ.values(),records=>records.length).reduce((total,count)=>total+count,0)};
        }''', moves)
        if errors:
            raise RuntimeError('\n'.join(errors))
        logical = json.loads(result.pop('input'))
        logical.get('stats', {}).pop('build_time', None)
        result['input_sha256'] = hashlib.sha256(json.dumps(logical, sort_keys=True).encode()).hexdigest()
        return result
    finally:
        context.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--interrupts', type=int)
    parser.add_argument('--moves', type=int, default=24)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.moves < 1 or arguments.rounds < 1 or arguments.interrupts is not None and arguments.interrupts < 1:
        parser.error('Counts must be positive')
    samples = {'before': [], 'after': []}
    with tempfile.TemporaryDirectory(prefix='fg-cpu-hover-') as directory:
        reports = {}
        for name in samples:
            report = Path(directory, name + '.html')
            report.write_text(instrument(fixture(getattr(arguments, name).read_text(), arguments.interrupts)))
            reports[name] = report
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for ordinal in range(arguments.rounds):
                    for name in ('before', 'after') if ordinal % 2 == 0 else ('after', 'before'):
                        result = measure(browser, reports[name], arguments.moves)
                        for previous in [record for records in samples.values() for record in records]:
                            for key in ('input_sha256', 'segment', 'html', 'interrupts'):
                                if result[key] != previous[key]:
                                    raise RuntimeError('Comparison differs: ' + key)
                        samples[name].append(result)
                version = browser.version
            finally:
                browser.close()
    for records in samples.values():
        for record in records:
            record['html_sha256'] = hashlib.sha256(record.pop('html').encode()).hexdigest()
    result = dict(browser=version, synthetic_interrupts=arguments.interrupts, moves=arguments.moves,
                  rounds=arguments.rounds, samples=samples,
                  medians={name:{phase:{key:median(record['timings'][phase][key] if key == 'milliseconds' else record[phase][key]
                                                  for record in records)
                              for key in ('calls','lookups','records','milliseconds')}
                              for phase in ('cold','repeated')} for name,records in samples.items()},
                  scope='CPU tooltip formatting only; record counts collected separately from timing passes without per-record proxies; not total pointer, rendering or startup latency')
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['medians'], indent=2))


if __name__ == '__main__':
    main()
