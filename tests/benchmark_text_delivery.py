"""Compare complete sidecar Text delivery in alternating isolated fgview browsers."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from benchmark_sidecar import measure
from benchmark_startup import report_info


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
    reports = [dict(report_info(path.resolve() / 'index.html'), root=str(path.resolve()), mode='sidecar')
               for path in arguments.reports]
    output = dict(reports=reports, runs=[], basis='Timeline then complete Text; parsed-status and two-rAF proxies; sampled RSS, not peak; full model digests after timing and RSS sampling')
    with sync_playwright() as playwright:
        for iteration in range(arguments.rounds):
            for report in reports if iteration % 2 == 0 else reversed(reports):
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                try:
                    facts = measure(browser, report, arguments.timeout, True)
                    facts.update(report=report['path'], round=iteration + 1, browser=browser.version)
                    output['runs'].append(facts)
                    identities = {(run['calls'], run['frames'], run['tracks'], run['first_text']['rows'],
                                   run['first_text']['model_sha256']) for run in output['runs']}
                    if len(identities) != 1:
                        raise RuntimeError('Complete Timeline/Text model identities differ')
                    arguments.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    text = facts['first_text']
                    print(report['path'], text['duration_ms'], text['parsed_feedback_ms'], text['longest_task_ms'], flush=True)
                finally:
                    browser.close()


if __name__ == '__main__':
    main()
