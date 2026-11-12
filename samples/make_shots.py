#!/usr/bin/env python3
"""Regenerate the screenshots from sample.html and sample_mixed.html.

    python3 samples/make_shots.py
    python3 samples/make_shots.py --mixed-only

Requires Playwright and Chromium. Captures the kernel overview and Text view,
and the mixed Timeline around sendto with dispatch_datagram selected.
"""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mixed-only', action='store_true', help='Refresh only sample_mixed.png')
    arguments = parser.parse_args()
    specification = importlib.util.spec_from_file_location('shots', ROOT / 'docs/img/make_shots.py')
    shots = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(shots)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        if not arguments.mixed_only:
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

        page = shots.open_page(browser, str(ROOT / 'sample_mixed.html'), 'timeline', width=1600, height=900)
        sendto = shots.find(page, 'sendto')
        dispatch = shots.find(page, 'dispatch_datagram')
        if min(sendto, dispatch) < 0:
            raise RuntimeError('sample_mixed.html is missing the recorded UDP path')
        shots.zoom(page, sendto, pad=0.04, select=False)
        page.evaluate('index => fgView("timeline").probe().select(index)', dispatch)
        page.locator('.k', has_text='user args').wait_for(state='visible')
        page.locator('.tl-cs > summary', has_text='User call site source').wait_for(state='visible')
        handle = page.locator('.tl-resizer').bounding_box()
        page.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] + handle['height'] / 2)
        page.mouse.down()
        page.mouse.move(handle['x'] + handle['width'] / 2, 420, steps=12)
        page.mouse.up()
        page.mouse.move(800, 25)
        save(page, ROOT / 'sample_mixed.png')
        browser.close()


if __name__ == '__main__':
    main()
