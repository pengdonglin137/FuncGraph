#!/usr/bin/env python3
"""Measure repeated Timeline -> Text feedback without timing gates or dropping data."""
import argparse
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright

from benchmark_startup import report_info


def settle(page):
    page.evaluate('async () => await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')


def snapshot(page):
    return page.evaluate('''async () => {
        const root = document.getElementById('content');
        const rows = Array.from(root.querySelectorAll('.line-container'), row => [row.id,
            Array.from(row.attributes).filter(attribute => attribute.name.startsWith('data-'))
                .map(attribute => [attribute.name, attribute.value]), row.textContent]);
        const sources = Array.from(root.querySelectorAll('.expanded-content'), source =>
            [source.id, source.getAttribute('data-src'), source.style.display, source.textContent]);
        async function digest(value) {
            const bytes = new TextEncoder().encode(JSON.stringify(value));
            const result = await crypto.subtle.digest('SHA-256', bytes);
            return Array.from(new Uint8Array(result), value => value.toString(16).padStart(2, '0')).join('');
        }
        return {rows:rows.length, text_sha256:await digest(rows), sources_sha256:await digest(sources),
            timeline_sha256:await digest(fgData.read('tlData')),
            top:scrollY, left:document.getElementById('textViewport').scrollLeft};
    }''')


def measure(page, session, stage):
    metrics = lambda: {item['name']: item['value'] for item in session.send('Performance.getMetrics')['metrics']}
    before = metrics()
    feedback = page.evaluate('''async () => {
        const start = performance.now();
        await fgSwitchView('text');
        const switched_ms = performance.now() - start;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return {switched_ms, feedback_ms:performance.now()-start};
    }''')
    page.wait_for_timeout(250)
    after = metrics()
    feedback.update(stage=stage, metrics={name: after[name]-before[name] for name in
        ('TaskDuration', 'ScriptDuration', 'LayoutDuration', 'RecalcStyleDuration', 'LayoutCount', 'RecalcStyleCount')})
    print(json.dumps(feedback), flush=True)
    return feedback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--source-line', type=int)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--executable')
    parser.add_argument('--timeout', type=int, default=180000)
    arguments = parser.parse_args()
    if arguments.rounds < 1 or arguments.timeout < 1 or arguments.source_line is not None and arguments.source_line < 1:
        parser.error('rounds, timeout and source-line must be positive')
    result = dict(report=report_info(arguments.report.resolve()), runs=[], fidelity=[],
        basis='Serial browser runs; double-rAF feedback proxy, not a presentation guarantee; metric durations are seconds; complete Text/Timeline/source digests outside timing windows')
    with sync_playwright() as playwright:
        options = dict(headless=not arguments.headed, ignore_default_args=['--hide-scrollbars'])
        if arguments.executable:
            options['executable_path'] = arguments.executable
        browser = playwright.chromium.launch(**options)
        try:
            page = browser.new_page(viewport={'width': 1500, 'height': 900})
            page.set_default_timeout(arguments.timeout)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(arguments.report.resolve().as_uri(), wait_until='load', timeout=arguments.timeout)
            page.evaluate('lsSet("view", "timeline")')
            start = time.monotonic()
            page.reload(wait_until='load', timeout=arguments.timeout)
            page.evaluate('async () => await fgSwitchView("timeline")')
            settle(page)
            result.update(browser=browser.version, timeline_reload_ms=(time.monotonic()-start)*1000)
            session = page.context.new_cdp_session(page)
            session.send('Performance.enable')
            phases = ['top'] + (['source'] if arguments.source_line else [])
            for phase in phases:
                if phase == 'source':
                    row = page.locator('#L' + str(arguments.source_line))
                    row.click(position={'x': 450, 'y': 10})
                    page.wait_for_function('''number => {
                        const source = document.getElementById('L' + number)?.nextElementSibling;
                        return source?.matches('.expanded-content') && source.style.display === 'block' && source.querySelector('.source-line');
                    }''', arg=arguments.source_line)
                    settle(page)
                    page.evaluate('async () => await fgSwitchView("timeline")')
                    settle(page)
                expected = None
                for iteration in range(arguments.rounds):
                    result['runs'].append(measure(page, session, phase + '-' + str(iteration)))
                    actual = snapshot(page)
                    if expected is None:
                        expected = actual
                    elif expected != actual:
                        raise RuntimeError('Text/source/model/reading position changed: ' + json.dumps(dict(expected=expected, actual=actual)))
                    if iteration + 1 < arguments.rounds:
                        page.evaluate('async () => await fgSwitchView("timeline")')
                        settle(page)
                        page.wait_for_timeout(250)
                result['fidelity'].append(dict(phase=phase, **expected))
            if errors:
                raise RuntimeError(json.dumps(errors))
            result['errors'] = errors
        finally:
            browser.close()
    arguments.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
