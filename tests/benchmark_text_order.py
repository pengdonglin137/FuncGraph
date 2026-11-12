"""Compare complete Text task/time ordering over fgview HTTP in isolated browsers."""
import argparse
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

import helpers
from fglib.viewer import create_server
from benchmark_startup import report_info
from benchmark_text import renderer_memory, settle


def measure(browser, report, timeout):
    server = create_server(Path(report['root']))
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    try:
        page = context.new_page()
        page.set_default_timeout(timeout)
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url))
        page.add_init_script('''(() => {
            const title = TITLE; let hash = 0;
            for (let index = 0; index < title.length; index++) hash = ((hash << 5) - hash + title.charCodeAt(index)) | 0;
            localStorage.clear(); localStorage.setItem('theme', 'light');
            localStorage.setItem('fg_' + (hash >>> 0).toString(36) + '_view', 'timeline');
            window.orderViolations = []; window.orderLongTasks = [];
            document.addEventListener('securitypolicyviolation', event => orderViolations.push(event.violatedDirective));
            new PerformanceObserver(list => orderLongTasks.push(...list.getEntries().map(entry =>
                ({start:entry.startTime, duration:entry.duration})))).observe({type:'longtask', buffered:true});
        })()'''.replace('TITLE', json.dumps(report['title'])))
        page.goto(server.url, wait_until='load', timeout=timeout)
        page.evaluate('async () => { await fgSwitchView("timeline"); }')
        settle(page)
        opened = page.evaluate('''async () => {
            const start = performance.now(); await fgSwitchView('text');
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            return {ready_ms:performance.now()-start, rows:document.querySelectorAll('#content .line-container').length};
        }''')
        actions = []
        for mode in ['task', 'time'] * 4:
            action = page.evaluate('''async mode => {
                const start = performance.now(); fgSetTextOrder(mode);
                const synchronous_ms = performance.now()-start;
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                return {mode, start, synchronous_ms, feedback_ms:performance.now()-start};
            }''', mode)
            page.wait_for_timeout(30)
            actions.append(action)
        rss = sum(process['rss_bytes'] for process in renderer_memory(browser))
        tasks = page.evaluate('orderLongTasks')
        for action in actions:
            durations = [task['duration'] for task in tasks if task['start'] < action['start'] + action['feedback_ms']
                         and task['start'] + task['duration'] > action['start']]
            action['longest_task_ms'] = max(durations, default=0)
            action['blocked_ms'] = sum(max(0, duration - 50) for duration in durations)
        identities = page.evaluate('''async () => {
            const root = document.getElementById('content'), model = fgDomTextModels.get(root);
            const digest = async value => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
                new TextEncoder().encode(JSON.stringify(value)))), byte => byte.toString(16).padStart(2,'0')).join('');
            const result = {model_sha256:await digest(model.records.map(record => record.row)), states:{}};
            for (const mode of ['time','task']) {
                fgSetTextOrder(mode);
                const rows = Array.from(root.querySelectorAll('.line-container'), row =>
                    [+row.dataset.lineNumber, row.classList.contains('grp-hidden'), row.classList.contains('task-switch'), row.style.display]);
                const boundaries = [], chunks = []; let position = 0;
                for (const child of root.children) {
                    if (child.classList.contains('text-chunk')) {
                        const count = child.querySelectorAll('.line-container').length;
                        chunks.push(count); position += count;
                    } else boundaries.push([position,child.className,child.textContent]);
                }
                result.states[mode] = {sha256:await digest({rows,boundaries,chunks}), rows:rows.length,
                    groups:boundaries.filter(boundary => boundary[1] === 'task-group-head').length,
                    gaps:boundaries.filter(boundary => boundary[1] === 'task-gap').length,
                    navigation_equal:JSON.stringify(fgTextNavigation.order) === JSON.stringify(rows.map(row => row[0]))};
            }
            return result;
        }''')
        violations = page.evaluate('orderViolations')
        if errors or violations or any(not state['navigation_equal'] or state['rows'] != opened['rows'] for state in identities['states'].values()):
            raise RuntimeError(json.dumps(dict(errors=errors, violations=violations, identities=identities)))
        return dict(opened=opened, actions=actions, sampled_renderer_rss_bytes=rss, identities=identities,
                    text_requests=sum('/text-' in url and url.endswith('.html') or url.endswith('/text.html') for url in requests),
                    errors=errors, violations=violations)
    finally:
        context.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=180000)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if min(arguments.rounds, arguments.timeout) < 1:
        parser.error('--rounds and --timeout must be positive')
    if any(not (path / 'index.html').is_file() for path in arguments.reports):
        parser.error('each report must be a complete sidecar directory retaining Text')
    reports = [dict(report_info(path.resolve() / 'index.html'), root=str(path.resolve())) for path in arguments.reports]
    output = dict(reports=reports, runs=[], basis='First cold and subsequent warm full-DOM reorders; two-rAF feedback proxy, not physical input-to-pixel; sampled RSS before untimed complete-state digests')
    identity = None
    with sync_playwright() as playwright:
        for iteration in range(arguments.rounds):
            for report in reports if iteration % 2 == 0 else reversed(reports):
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                try:
                    facts = measure(browser, report, arguments.timeout)
                    if identity is not None and identity != facts['identities']:
                        raise RuntimeError('Complete Text order, groups, gaps, chunk boundaries or models differ')
                    identity = facts['identities']
                    facts.update(report=report['path'], round=iteration + 1, browser=browser.version)
                    output['runs'].append(facts)
                    arguments.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(report['path'], 'cold task:', round(facts['actions'][0]['feedback_ms'], 1),
                          'ms; rows:', facts['opened']['rows'], flush=True)
                finally:
                    browser.close()


if __name__ == '__main__':
    main()
