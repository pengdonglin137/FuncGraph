#!/usr/bin/env python3
"""Run repeatable mouse or touch Timeline batches against a standalone report."""
import argparse
import json
from math import isfinite
from pathlib import Path
from statistics import median, quantiles
from time import monotonic

METRICS = {'Nodes', 'Documents', 'JSEventListeners', 'JSHeapUsedSize', 'LayoutCount',
           'LayoutDuration', 'RecalcStyleDuration', 'ScriptDuration', 'TaskDuration'}
RENDER_EVENTS = {'Layout', 'UpdateLayoutTree', 'PrePaint', 'Paint'}
FRAME_EVENTS = RENDER_EVENTS | {'FireAnimationFrame', 'TimerFire'}
WARM_WINDOW_BATCHES = 3


def emit(**fields):
    print(json.dumps(fields), flush=True)


def verify_retained_growth(checkpoints, limits):
    metrics = ('Nodes', 'JSEventListeners')
    first = checkpoints[0][1]
    samples = [dict(batch=batch, **{key: sample[key] - first[key] for key in metrics})
               for batch, sample in checkpoints[1:]]
    maximum = {key: max(0, *(sample[key] for sample in samples)) for key in metrics}
    budget = dict(zip(metrics, limits))
    passed = all(maximum[key] <= budget[key] for key in metrics)
    emit(retained_growth=dict(first_batch=checkpoints[0][0], samples=samples,
                              maximum=maximum, limits=budget, passed=passed))
    if not passed:
        raise RuntimeError(f'Retained growth exceeds the supplied budget: {maximum} > {budget}')


def verify_cold_frame_time(sample, index, limit):
    elapsed = sample['two_frames_ms']
    passed = elapsed <= limit
    emit(cold_frame=dict(index=index, handler_ms=sample['handler_ms'], two_frames_ms=elapsed,
                         limit_ms=limit, passed=passed))
    if not passed:
        raise RuntimeError(f'First click on slice {index} exceeds the supplied budget: {elapsed:.2f} ms > {limit:.2f} ms')


def verify_warm_frame_time(samples, batch_size, limit):
    warm = [sample['two_frames_ms'] for sample in samples[batch_size:]]
    p95 = quantiles(warm, n=20, method='inclusive')[-1]
    emit(warm_frame_latency=dict(samples=len(warm), p95_ms=p95, limit_ms=limit, passed=p95 <= limit))
    if p95 > limit:
        raise RuntimeError(f'Warm click latency exceeds the supplied budget: {p95:.2f} ms > {limit:.2f} ms')


def verify_warm_frame_drift(samples, batch_size, limit):
    window_size = WARM_WINDOW_BATCHES * batch_size
    early = samples[batch_size:batch_size + window_size]
    late = samples[-window_size:]
    early_p95, late_p95 = (quantiles([sample['two_frames_ms'] for sample in window],
                                    n=20, method='inclusive')[-1] for window in (early, late))
    ratio = late_p95 / early_p95
    emit(warm_frame_drift=dict(samples_per_window=window_size, early_p95_ms=early_p95, late_p95_ms=late_p95,
                               ratio=ratio, limit=limit, passed=ratio <= limit))
    if ratio > limit:
        raise RuntimeError(f'Warm click latency drift exceeds the supplied budget: {ratio:.2f}× > {limit:.2f}×')


def attribute_long_tasks(tasks, samples, indices):
    result = []
    for task in tasks:
        start = task['startTime']
        end = start + task['duration']
        activations = []
        for position, sample in enumerate(samples):
            selection = sample['start_ms']
            overlap = min(end, selection + sample['two_frames_ms']) - max(start, selection)
            if overlap > 0:
                batch, slot = divmod(position, len(indices))
                activations.append(dict(batch=batch, index=indices[slot], offset_ms=start - selection, overlap_ms=overlap))
        if activations:
            result.append(dict(task, activations=activations))
    return result


def click_trace(page, session, activation):
    events = []
    completed = []
    session.on('Tracing.dataCollected', lambda message: events.extend(message['value']))
    session.on('Tracing.tracingComplete', lambda message: completed.append(message))
    session.send('Tracing.start', {
        'categories': 'devtools.timeline,blink.user_timing,disabled-by-default-devtools.timeline.stack',
    })

    def finish(path):
        session.send('Tracing.end')
        deadline = monotonic() + 10
        while not completed and monotonic() < deadline:
            page.wait_for_timeout(10)
        if not completed:
            raise RuntimeError(f'Timed out waiting for the trace of activation {activation}')
        with path.open('x', encoding='utf-8') as output:
            json.dump({'traceEvents': events}, output)
        names = ('fg-selection-start', 'fg-selection-handler-end', 'fg-selection-frame-end')
        markers = {event['name']: event for event in events if event['name'] in names}
        if len(markers) != len(names):
            raise RuntimeError(f'Activation {activation} trace lacks timing markers: {list(markers)}')
        start, handler, frames = (markers[name]['ts'] for name in names)
        origin = markers[names[0]]
        timings, layouts, style_updates = {}, [], []
        phase_ms = {'handler': {}, 'frame': {}}
        for event in events:
            name = event.get('name')
            if (event.get('ph') == 'X' and name in FRAME_EVENTS and
                    (event['pid'], event['tid']) == (origin['pid'], origin['tid'])):
                begins, ends = event['ts'], event['ts'] + event['dur']
                for phase, lower, upper in (('handler', start, handler), ('frame', handler, frames)):
                    overlap = min(upper, ends) - max(lower, begins)
                    if overlap > 0:
                        phase_ms[phase][name] = phase_ms[phase].get(name, 0) + overlap / 1000
                if not (name in RENDER_EVENTS and start <= begins < frames):
                    continue
                timings.setdefault(name, []).append(event['dur'] / 1000)
                if name in ('Layout', 'UpdateLayoutTree'):
                    event_args = event.get('args', {})
                    begin_data = event_args.get('beginData', {})
                    stack = (begin_data.get('stackTrace') or
                             event_args.get('data', {}).get('stackTrace') or [])
                    facts = dict(phase='handler' if begins < handler else 'frame', ms=event['dur'] / 1000,
                                 functions=[frame['functionName'] for frame in stack if frame.get('functionName')])
                    if name == 'UpdateLayoutTree':
                        style_updates.append(dict(facts, elements=event_args.get('elementCount')))
                    else:
                        layout_roots = event_args.get('endData', {}).get('layoutRoots')
                        layouts.append(dict(facts,
                                        dirty_objects=begin_data.get('dirtyObjects'),
                                        total_objects=begin_data.get('totalObjects'),
                                        partial_layout=begin_data.get('partialLayout'),
                                        root_names=None if layout_roots is None else [root.get('nodeName') for root in layout_roots]))
        emit(trace=str(path), activation=activation, handler_ms=(handler - start) / 1000, two_frames_ms=(frames - start) / 1000,
             renderer_events_ms=timings, renderer_phase_ms=phase_ms, layouts=layouts, style_updates=style_updates)

    return finish


def measure(browser, args):
    width, height = args.viewport
    page = browser.new_page(viewport={'width': width, 'height': height}, is_mobile=args.touch, has_touch=args.touch)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        if args.detail_fraction is not None:
            page.add_init_script(f"localStorage.setItem('fgTl.detailFrac', {json.dumps(str(args.detail_fraction))})")
        page.goto(args.report.resolve().as_uri(), timeout=120000)
        text = page.evaluate('''async ({mode, touch, scrollTop, traceActivation}) => {
            await fgSwitchView('timeline');
            if (mode === 'windowed' || (mode === 'full' &&
                    !document.querySelector('#content .line-container'))) {
                await fgSwitchView('text');
                if (typeof fgTextWindow === 'undefined' || !fgTextWindow)
                    throw Error('Windowed Text did not initialize');
                if (mode === 'full') {
                    await fgTextWindow.fullText();
                    if (fgTextWindow) throw Error('Full Text did not finish');
                }
                await fgSwitchView('timeline');
            }
            if (scrollTop) document.querySelector('.tl-scroll').scrollTop = scrollTop;
            await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
            const canvas = document.querySelector('.tl-canvas');
            const activation = touch ? 'click' : 'mouseup';
            const longTasks = [];
            const observer = window.PerformanceObserver?.supportedEntryTypes?.includes('longtask') ?
                new PerformanceObserver(list => longTasks.push(...list.getEntries())) : null;
            observer?.observe({type: 'longtask'});
            window.selectionLongTasks = () => {
                if (!observer) return null;
                longTasks.push(...observer.takeRecords());
                observer.disconnect();
                return longTasks.map(({startTime, duration}) => ({startTime, duration}));
            };
            let start, finish, tracing = false, activationCount = 0;
            window.addEventListener(activation, event => {
                if (event.target !== canvas) throw Error('Missed canvas');
                window.selectionSample = new Promise(done => { finish = done; });
                start = performance.now();
                tracing = ++activationCount === traceActivation;
                if (tracing) performance.mark('fg-selection-start');
            }, {capture: true});
            window.addEventListener(activation, () => {
                const handler = performance.now() - start, started = start, done = finish, mark = tracing;
                if (mark) performance.mark('fg-selection-handler-end');
                requestAnimationFrame(() => requestAnimationFrame(() => {
                    if (mark) performance.mark('fg-selection-frame-end');
                    done({start_ms: started, handler_ms: handler, two_frames_ms: performance.now() - started});
                }));
            });
            return {rows: document.querySelectorAll('#content .line-container').length,
                windowed: typeof fgTextWindow !== 'undefined' && !!fgTextWindow};
        }''', {'mode': args.text, 'touch': args.touch, 'scrollTop': args.scroll_top, 'traceActivation': args.trace_activation})
        if args.text == 'windowed' and not (text['windowed'] and text['rows'] > 0):
            raise RuntimeError(f'Expected mounted Windowed Text: {text}')
        if args.text == 'full' and text['windowed']:
            raise RuntimeError(f'Expected mounted Full Text: {text}')
        if args.rows is not None and text['rows'] != args.rows:
            raise RuntimeError(f'Expected {args.rows} mounted Text rows: {text}')

        session = page.context.new_cdp_session(page)
        session.send('Emulation.setCPUThrottlingRate', {'rate': args.cpu_rate})
        session.send('Performance.enable')

        def metrics():
            return {item['name']: item['value'] for item in session.send('Performance.getMetrics')['metrics']
                    if item['name'] in METRICS}

        def collect():
            session.send('HeapProfiler.collectGarbage')
            return metrics()

        emit(report=str(args.report), browser=browser.version, text=args.text, indices=args.indices,
             cpu_rate=args.cpu_rate, viewport=args.viewport, touch=args.touch, scroll_top=args.scroll_top,
             detail_fraction=args.detail_fraction, gc_every=args.gc_every,
             **text, before=collect() if args.gc_every else metrics())
        checkpoints = []
        measured = []

        def checkpoint(batch):
            sample = collect()
            checkpoints.append((batch, sample))
            emit(batches_completed=batch, after_gc=sample)

        for batch in range(args.batches):
            samples = []
            for index in args.indices:
                x, y = page.evaluate('''index => {
                    window.selectionSample = null;
                    const point = fgView('timeline').probe().xyOf(index);
                    if (!document.elementFromPoint(...point)?.matches('.tl-canvas'))
                        throw Error('Slice ' + index + ' is outside the visible chart');
                    return point;
                }''', index)
                activation = batch * len(args.indices) + len(samples) + 1
                finish_trace = click_trace(page, session, activation) if activation == args.trace_activation else None
                if args.touch:
                    page.touchscreen.tap(x, y)
                else:
                    page.mouse.click(x, y)
                selected, sample = page.evaluate('''async () => {
                    const sample = await selectionSample;
                    const detail = document.getElementById('timelineView').querySelector('.tl-detail');
                    const stack = detail.querySelector('[data-context-list=stack]');
                    if (sample && stack) {
                        sample.stack_rows = stack.querySelectorAll('[data-context-kind]').length;
                        sample.stack_elements = stack.querySelectorAll('*').length;
                    }
                    const source = detail.querySelector('.tl-cs-src');
                    if (sample) {
                        sample.source_lines = source?.querySelectorAll('.source-line').length ?? 0;
                        sample.source_elements = source?.querySelectorAll('*').length ?? 0;
                    }
                    return [fgView('timeline').probe().sel(), sample];
                }''')
                if selected != index or sample is None:
                    raise RuntimeError(f'Click at {x}, {y} selected {selected}, expected {index}')
                if finish_trace:
                    finish_trace(args.trace_path)
                samples.append(sample)
            measured.extend(samples)
            summary = {}
            for key in ('handler_ms', 'two_frames_ms'):
                values = [sample[key] for sample in samples]
                summary[key] = dict(median=median(values), p95=quantiles(values, n=20, method='inclusive')[-1],
                                    maximum=max(values))
            emit(batch=batch, summary=summary, samples=samples, after=metrics())
            if args.gc_every and (batch + 1) % args.gc_every == 0 and batch + 1 < args.batches:
                checkpoint(batch + 1)
        if errors:
            raise RuntimeError('\n'.join(errors))
        long_tasks = page.evaluate('() => selectionLongTasks()')
        if long_tasks is not None:
            long_tasks = attribute_long_tasks(long_tasks, measured, args.indices)
        emit(selection_long_tasks=long_tasks)
        checkpoint(args.batches)
        if args.cold_frame_limit_ms is not None:
            verify_cold_frame_time(measured[0], args.indices[0], args.cold_frame_limit_ms)
        if args.warm_frame_limit_ms is not None:
            verify_warm_frame_time(measured, len(args.indices), args.warm_frame_limit_ms)
        if args.retained_growth_limit is not None:
            verify_retained_growth(checkpoints, args.retained_growth_limit)
        if args.warm_frame_ratio_limit is not None:
            verify_warm_frame_drift(measured, len(args.indices), args.warm_frame_ratio_limit)
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--indices', required=True, type=lambda value: [int(index) for index in value.split(',')],
                        help='comma-separated indices of visible Timeline calls; use the same order in all reports')
    parser.add_argument('--text', choices=('initial', 'windowed', 'full'), default='initial')
    parser.add_argument('--rows', type=int, help='expected number of mounted Text rows before any clicks')
    parser.add_argument('--batches', type=int, default=12)
    parser.add_argument('--gc-every', type=int, default=0,
                        help='force GC before the run and after every N batches to sample retained state (0: final only)')
    parser.add_argument('--retained-growth-limit', type=lambda value: tuple(map(int, value.split(','))),
                        metavar='NODES,LISTENERS', help='fail if a post-GC checkpoint grows past these limits relative to the first completed checkpoint')
    parser.add_argument('--cold-frame-limit-ms', type=float, metavar='MS',
                        help='fail if first-click two-frame latency exceeds this budget (milliseconds)')
    parser.add_argument('--warm-frame-limit-ms', type=float, metavar='MS',
                        help='fail if the combined two-frame p95 after the first batch exceeds MS')
    parser.add_argument('--warm-frame-ratio-limit', type=float, metavar='RATIO',
                        help='fail if the last three batches exceed this ratio of the first three warm batches at p95 two-frame latency (skips the cold first batch)')
    parser.add_argument('--cpu-rate', type=float, default=4)
    parser.add_argument('--viewport', type=lambda value: tuple(map(int, value.lower().split('x'))), default=(1500, 900),
                        help='browser viewport, e.g. 844x390')
    parser.add_argument('--touch', action='store_true', help='tap calls in a coarse-pointer mobile viewport')
    parser.add_argument('--scroll-top', type=int, default=0, help='initial vertical offset within the Timeline tracks')
    parser.add_argument('--detail-fraction', type=float, help='saved vertical detail split as a fraction of viewport height')
    trace_group = parser.add_mutually_exclusive_group()
    trace_group.add_argument('--trace-first', type=Path, help='save a Chromium trace of the first activation to a new JSON file')
    trace_group.add_argument('--trace-click', nargs=2, metavar=('NUMBER', 'PATH'),
                             help='save a Chromium trace of the specified 1-based activation across batches')
    args = parser.parse_args()
    if len(set(args.indices)) < 2 or min(args.indices) < 0 or args.batches < 1 or args.cpu_rate < 1:
        parser.error('provide at least two distinct nonnegative slice indices, one batch and a CPU rate ≥ 1')
    if args.text == 'full' and not (args.rows and args.rows > 0):
        parser.error('Full Text requires --rows with the expected complete row count')
    if len(args.viewport) != 2 or min(args.viewport) < 1:
        parser.error('--viewport must be two positive dimensions WIDTHxHEIGHT')
    if args.scroll_top < 0:
        parser.error('--scroll-top must be nonnegative')
    if args.gc_every < 0:
        parser.error('--gc-every must be nonnegative')
    if args.retained_growth_limit is not None:
        if len(args.retained_growth_limit) != 2 or min(args.retained_growth_limit) < 0:
            parser.error('--retained-growth-limit needs two nonnegative counts, NODES,LISTENERS')
        if not 0 < args.gc_every < args.batches:
            parser.error('--retained-growth-limit needs at least two post-GC checkpoints (0 < --gc-every < --batches)')
    if args.cold_frame_limit_ms is not None and not (isfinite(args.cold_frame_limit_ms) and args.cold_frame_limit_ms > 0):
        parser.error('--cold-frame-limit-ms must be finite and positive')
    if args.warm_frame_limit_ms is not None:
        if not (isfinite(args.warm_frame_limit_ms) and args.warm_frame_limit_ms > 0):
            parser.error('--warm-frame-limit-ms must be finite and positive')
        if args.batches < 2:
            parser.error('--warm-frame-limit-ms needs at least two batches')
    if args.warm_frame_ratio_limit is not None:
        if not isfinite(args.warm_frame_ratio_limit) or args.warm_frame_ratio_limit < 1:
            parser.error('--warm-frame-ratio-limit must be finite and at least 1')
        if args.batches < 1 + 2 * WARM_WINDOW_BATCHES:
            parser.error('--warm-frame-ratio-limit needs at least seven batches')
    if args.detail_fraction is not None and not 0 < args.detail_fraction < 1:
        parser.error('--detail-fraction must be strictly between 0 and 1')
    args.trace_activation, args.trace_path = (1, args.trace_first) if args.trace_first else (None, None)
    if args.trace_click:
        try:
            args.trace_activation = int(args.trace_click[0])
        except ValueError:
            parser.error('--trace-click NUMBER must be an integer')
        if not 1 <= args.trace_activation <= args.batches * len(args.indices):
            parser.error('--trace-click NUMBER must name an activation in the measured sequence')
        args.trace_path = Path(args.trace_click[1])
    if args.trace_path and (args.trace_path.exists() or not args.trace_path.parent.is_dir()):
        parser.error('tracing needs a new file in an existing directory')
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        measure(browser, args)


if __name__ == '__main__':
    main()
