#!/usr/bin/env python3
"""Regenerate sample.png and samples/sample_text.png from the current sample.html.

    python3 samples/make_shots.py

Requires Playwright and Chromium. Keeps the overview at 1841x951 with
process_backlog selected, and the Text view at 1600x1000.
"""
import importlib.util
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent


def save(page, path):
    page.wait_for_timeout(400)
    if page.errs:
        raise RuntimeError(f'{path.name}: JavaScript errors: {page.errs}')
    page.screenshot(path=str(path))
    print(f'{path.relative_to(ROOT)}: {path.stat().st_size} bytes')
    page.close()


def main():
    specification = importlib.util.spec_from_file_location('shots', ROOT / 'docs/img/make_shots.py')
    shots = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(shots)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        page = shots.open_page(browser, str(ROOT / 'sample.html'), 'timeline', width=1841, height=951)
        selected = shots.find(page, 'process_backlog')
        if selected < 0:
            raise RuntimeError('sample.html has no recorded process_backlog call')
        page.evaluate('index => fgView("timeline").probe().select(index)', selected)
        save(page, ROOT / 'sample.png')

        page = shots.open_page(browser, str(ROOT / 'sample.html'), width=1600, height=1000)
        page.fill('#filterInput', 'graph:udp_sendmsg')
        page.locator('#filterInput').press('Enter')
        page.locator('.line-container.expandable[data-func="udp_sendmsg"]').first.click()
        page.evaluate('window.scrollTo(0, 0)')
        save(page, ROOT / 'samples/sample_text.png')
        browser.close()


if __name__ == '__main__':
    main()
