#!/usr/bin/env python3
"""Regenerate the screenshots of the user guides (docs/img/*.png).

    python3 docs/img/make_shots.py            # all
    python3 docs/img/make_shots.py tl_errors  # only these

Inputs are all in the repository: sample.html (report of samples/sample_io.txt, built with
vmlinux and sources, see README "Sample"), and reports built here without vmlinux from
tests/data/pi4_sched.txt (sched events, off-CPU, wakeups), tests/data/pi4_nodur.txt (depth gaps)
and samples/sample_io_slow.txt (Compare). Needs Playwright with Chromium.
Re-run after UI changes and commit the images together with the change.
"""
import os
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, 'docs', 'img')
W, H = 1400, 860
TMP = tempfile.mkdtemp(prefix='fgshots-')


def report(name, *args):
    out = os.path.join(TMP, name + '.html')
    subprocess.run([sys.executable, os.path.join(ROOT, 'funcgraph.py'), '--timeline', '--enable-fold', '--filter',
                    '--output', out] + list(args), check=True, capture_output=True)
    return out


# ---- page helpers ----
PROBE = "fgView('timeline').probe()"


def open_page(b, path, view=None, height=H, width=W, notes=False, touch=False):
    pg = b.new_page(viewport={'width': width, 'height': height}, is_mobile=touch, has_touch=touch)
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path, wait_until='load')
    pg.evaluate('localStorage.clear()')
    pg.reload(wait_until='load')
    pg.errs = errs
    if view:
        pg.evaluate(f"fgSwitchView('{view}')")
        pg.wait_for_timeout(500)
        if view == 'timeline' and os.path.abspath(path) == os.path.join(ROOT, 'sample.html'):
            pg.evaluate("""() => {
                const model = fgView('timeline').probe();
                let start = Infinity, end = -Infinity;
                for (let index = 0; index < model.N; index++) {
                    if (!model.nameOf(index).startsWith('__x64_sys_')) continue;
                    start = Math.min(start, model.sStart[index]);
                    end = Math.max(end, model.sEnd[index]);
                }
                if (end > start) {
                    const pad = (end - start) * 0.025;
                    model.zoomTo(start - pad, end + pad, -1);
                }
            }""")
        if view == 'timeline' and not notes and pg.locator('[data-act=notes].on').count():
            pg.click('[data-act=notes]')      # more room for the tracks
            pg.wait_for_timeout(200)
    return pg


def find(pg, name, nth=0, longest=False):
    return pg.evaluate("""([nm, nth, longest]) => { const q = fgView('timeline').probe(); let best = -1, k = 0;
        for (let i = 0; i < q.N; i++) if (q.nameOf(i) === nm) {
            if (longest) { if (best < 0 || q.sEnd[i] - q.sStart[i] > q.sEnd[best] - q.sStart[best]) best = i; }
            else if (k++ === nth) return i; }
        return best; }""", [name, nth, longest])


def zoom(pg, i, pad=0.04, select=True):
    pg.evaluate(f"""(() => {{ const q = {PROBE}, s = q.sStart[{i}], e = q.sEnd[{i}], d = (e - s) * {pad};
        q.zoomTo(s - d, e + d, -1); {'q.select(' + str(i) + ');' if select else ''} }})()""")
    pg.wait_for_timeout(400)


def hover(pg, i):
    x, y = pg.evaluate(f"{PROBE}.xyOf({i})")
    pg.mouse.move(x, y)
    pg.wait_for_timeout(300)


def box(pg, sel):
    return pg.locator(sel).first.bounding_box()


def badges(pg, items):
    """Numbered circles at the top-left corner of page elements (selector) or rectangles."""
    pg.evaluate("""(items) => { for (const [n, sel, dx, dy] of items) {
        const el = typeof sel === 'string' ? document.querySelector(sel) : null;
        const r = el ? el.getBoundingClientRect() : { left: sel[0], top: sel[1] };
        const b = document.createElement('div'); b.textContent = n;
        b.style.cssText = 'position:fixed;z-index:9999;width:22px;height:22px;border-radius:11px;background:#e8590c;' +
            'color:#fff;font:700 13px/22px system-ui,sans-serif;text-align:center;box-shadow:0 0 0 2px #fff;pointer-events:none;' +
            'left:' + (r.left + (dx || 0)) + 'px;top:' + (r.top + (dy || 0)) + 'px';
        document.body.appendChild(b); } }""", items)


def shot(pg, name, clip=None):
    path = os.path.join(OUT, name + '.png')
    pg.screenshot(path=path, clip=clip)
    if pg.errs:
        raise SystemExit(f'{name}: JavaScript errors: {pg.errs}')
    print(f'  {name}.png  {os.path.getsize(path) // 1024} KB')


# ---- shots ----
def text_view(b, rep):
    pg = open_page(b, rep['sample'])
    pg.fill('#filterInput', 'graph:udp_sendmsg')
    pg.keyboard.press('Enter')
    pg.wait_for_timeout(400)
    pg.evaluate("""() => { const l = document.querySelector('.line-container.expandable[data-func="udp_sendmsg"]');
        l.click(); window.scrollTo(0, 0); }""")
    pg.wait_for_timeout(400)
    badges(pg, [[1, '.page-header', -6, 4], [2, '.view-tabs', -6, 4], [3, '.controls', -6, 2],
                [4, '#filterInput', -8, 0], [5, '.line-container.expandable[data-func="udp_sendmsg"] .line-number', -4, 0],
                [6, '.expanded-content[style*="block"]', 6, 6]])
    shot(pg, 'text_view', {'x': 0, 'y': 0, 'width': W, 'height': 620})


def text_folding(b, rep):
    pg = open_page(b, rep['sample'])
    pg.fill('#filterInput', 'graph:udp_sendmsg')
    pg.keyboard.press('Enter')
    pg.wait_for_timeout(400)
    pg.locator('.fold-entry[data-func="ip_route_output_key_hash_rcu"] .fold-icon').first.click()
    pg.locator('.fold-entry[data-func="ipv4_mtu"] .fold-icon').first.click()
    pg.evaluate('window.scrollTo(0, 0)')
    pg.locator('.fold-entry[data-func="ip_route_output_flow"] .fold-icon').first.hover()
    pg.wait_for_timeout(200)
    shot(pg, 'text_folding')


def tl_layout(b, rep):
    pg = open_page(b, rep['sample'], 'timeline', width=1600, notes=True)
    pg.evaluate(f"{PROBE}.select({find(pg, '__x64_sys_sendto')})")
    pg.wait_for_timeout(400)
    hc = box(pg, '.tl-head-canvas')
    badges(pg, [[1, '.tl-toolbar', -8, 4], [2, '[data-act=fit]', -10, -8], [3, '.tl-notes', -8, 2],
                [4, [hc['x'] - 8, hc['y'] + 2]], [5, [hc['x'] - 8, hc['y'] + 34]],
                [6, '.tl-scroll', -8, 4], [7, '.tl-detail', -8, 4]])
    shot(pg, 'tl_layout')


def tl_errors(b, rep):
    pg = open_page(b, rep['sample'], 'timeline')
    top = find(pg, '__x64_sys_recvfrom', 1)
    zoom(pg, top, pad=0.6, select=False)
    hover(pg, find(pg, 'udp_recvmsg', 1))
    c = box(pg, '.tl-stage')
    shot(pg, 'tl_errors', {'x': c['x'], 'y': c['y'], 'width': c['width'], 'height': 430})


def tl_compact(b, rep):
    pg = open_page(b, rep['sched'], 'timeline', width=390, height=844, touch=True)
    pg.evaluate(f"{PROBE}.select(0)")
    pg.wait_for_timeout(400)
    shot(pg, 'tl_compact')


def tl_search(b, rep):
    pg = open_page(b, rep['sample'], 'timeline')
    pg.fill('.tl-search', 'recv')
    pg.press('.tl-search', 'Enter')
    pg.press('.tl-search', 'Enter')
    pg.evaluate('document.activeElement.blur()')
    pg.keyboard.press('f')
    pg.wait_for_timeout(400)
    shot(pg, 'tl_search')


def tl_dense(b, rep):
    pg = open_page(b, rep['sched'], 'timeline')
    candidate = pg.evaluate("""() => {
        const probe = fgView('timeline').probe(), canvas = document.querySelector('.tl-canvas');
        const view = probe.view(), scale = canvas.getBoundingClientRect().width / (view[1] - view[0]);
        let best = null;
        for (const task of probe.TRACKS) for (const row of task.rowsT) {
            let first = 0;
            while (first < row.length) {
                const pixel = Math.floor((probe.sStart[row[first]] - view[0]) * scale);
                let end = first;
                while (end < row.length && Math.floor((probe.sStart[row[end]] - view[0]) * scale) === pixel) end++;
                const ids = Array.from(row.slice(first, end));
                if (ids.length > 1 && ids.every(index => (probe.sEnd[index] - probe.sStart[index]) * scale < 1.5) &&
                    (!best || ids.length > best.count)) best = {index: ids[0], count: ids.length, name: probe.nameOf(ids[0])};
                first = end;
            }
        }
        if (!best) throw new Error('No dense pixel in the real scheduling fixture');
        probe.select(best.index); probe.select(-1);
        return best;
    }""")
    pg.fill('.tl-ftext', 'func:' + candidate['name'].split('.')[0])
    pg.press('.tl-ftext', 'Enter')
    pg.wait_for_timeout(400)
    point = pg.evaluate("""index => {
        const probe = fgView('timeline').probe(), canvas = document.querySelector('.tl-canvas');
        const bounds = canvas.getBoundingClientRect(), view = probe.view(), point = probe.xyOf(index);
        return [bounds.left + Math.floor((probe.sStart[index] - view[0]) * bounds.width / (view[1] - view[0])) + 0.5, point[1]];
    }""", candidate['index'])
    pg.mouse.move(*point)
    pg.wait_for_timeout(300)
    tooltip = pg.locator('.tl-tip').inner_text()
    if 'Dense pixel' not in tooltip or '0 match the current' in tooltip:
        raise SystemExit('Dense-pixel screenshot did not hit the grouped frames')
    shot(pg, 'tl_dense')


def tl_irqoff(b, rep):
    pg = open_page(b, rep['sample'], 'timeline')
    zoom(pg, find(pg, 'do_softirq'), pad=0.25, select=False)
    c = box(pg, '.tl-stage')
    hover(pg, find(pg, 'do_softirq'))
    shot(pg, 'tl_irqoff', {'x': c['x'], 'y': c['y'], 'width': c['width'], 'height': 500})


def tl_offcpu(b, rep):
    pg = open_page(b, rep['sched'], 'timeline')
    # longest off-CPU period with a known waker, zoomed out around it, hovered (tooltip + wakeup arrow)
    pg.evaluate(f"""(() => {{ const q = {PROBE}; let best = null;
        for (const t of q.TRACKS) for (const r of t.off) if (r[9] !== null && r[9] >= 0 && (!best || r[2] - r[1] > best[2] - best[1])) best = r;
        window.__off = best; const d = (best[2] - best[1]) * 0.35; q.zoomTo(best[1] - d, best[2] + d, -1); }})()""")
    pg.evaluate(f"document.querySelector('.tl-scroll').scrollTop = Math.max(0, {PROBE}.TRACKS[window.__off[0]].y - 60)")
    pg.wait_for_timeout(400)
    x, y = pg.evaluate(f"{PROBE}.offXY(window.__off)")
    pg.mouse.move(x, y)
    pg.wait_for_timeout(400)
    c = box(pg, '.tl-stage')
    tip = box(pg, '.tl-tip')
    hgt = max(300, (tip['y'] + tip['height'] + 8 - c['y']) if tip else 300)
    shot(pg, 'tl_offcpu', {'x': c['x'], 'y': c['y'], 'width': c['width'], 'height': min(c['height'], hgt)})


def tl_untraced(b, rep):
    pg = open_page(b, rep['nodur'], 'timeline')
    # innermost untraced frame (this fixture has no call sites, so its name is unknown)
    g = pg.evaluate(f"(() => {{ const q = {PROBE}; for (let i = q.N; i < q.NA; i++) if (q.isGhost(i) && q.SYN[i - q.N].level === q.SYN[i - q.N].gap) return i; return -1; }})()")
    p = pg.evaluate(f"{PROBE}.SYN[{g} - {PROBE}.N].p")
    zoom(pg, p, pad=0.1, select=False)
    pg.evaluate(f"{PROBE}.select({g})")
    pg.wait_for_timeout(400)
    shot(pg, 'tl_untraced')


def tl_detail(b, rep):
    pg = open_page(b, rep['sample'], 'timeline', height=980)
    i = find(pg, 'udp_sendmsg')
    zoom(pg, i, pad=0.3)
    r = box(pg, '.tl-resizer')
    pg.mouse.move(r['x'] + r['width'] / 2, r['y'] + 4)
    pg.mouse.down()
    pg.mouse.move(r['x'] + r['width'] / 2, r['y'] - 260, steps=4)
    pg.mouse.up()
    pg.wait_for_timeout(400)
    pg.locator('[data-context-list=stack] .tl-context-source a').first.hover()
    d = box(pg, '.tl-detail')
    shot(pg, 'tl_detail', {'x': 0, 'y': d['y'] - 70, 'width': W, 'height': 980 - d['y'] + 70})


def tl_inline(b, rep):
    pg = open_page(b, rep['sample'], 'timeline')
    pg.evaluate(f"{PROBE}.inline(true)")
    i = find(pg, '__x64_sys_sendto')
    zoom(pg, i, pad=0.05, select=False)
    hover(pg, pg.evaluate(f"(() => {{ const q = {PROBE}; for (let i = q.N; i < q.NA; i++) if (!q.isGhost(i) && q.sStart[i] >= q.sStart[{i}] && q.sEnd[i] <= q.sEnd[{i}]) return i; return -1; }})()"))
    c = box(pg, '.tl-stage')
    shot(pg, 'tl_inline', {'x': c['x'], 'y': c['y'], 'width': c['width'], 'height': 400})


def tl_tail(b, rep):
    pg = open_page(b, rep['sample'], 'timeline', height=980)
    parent = find(pg, '__x64_sys_read')
    selected = find(pg, 'ksys_read')
    zoom(pg, parent, pad=0.12, select=False)
    pg.evaluate('index => fgView("timeline").probe().select(index)', selected)
    pg.wait_for_timeout(400)
    pg.locator('.tl-tail-tag').first.wait_for()
    resizer = box(pg, '.tl-resizer')
    pg.mouse.move(resizer['x'] + resizer['width'] / 2, resizer['y'] + 4)
    pg.mouse.down()
    pg.mouse.move(resizer['x'] + resizer['width'] / 2, resizer['y'] - 210, steps=4)
    pg.mouse.up()
    pg.wait_for_timeout(400)
    shot(pg, 'tl_tail')


def tl_help(browser, reports):
    page = open_page(browser, reports['sample'], 'timeline')
    page.locator('#viewHelpBtn').click()
    page.wait_for_selector('.tl-help.show')
    shot(page, 'tl_help', {'x': W - 520, 'y': 0, 'width': 520, 'height': H})


def an_range(b, rep):
    pg = open_page(b, rep['sample'], 'timeline')
    i = find(pg, '__x64_sys_write')
    pg.evaluate(f"(() => {{ const q = {PROBE}; q.range(q.sStart[{i}], q.sEnd[{i}], q.sTask[{i}]); }})()")
    pg.wait_for_timeout(400)
    shot(pg, 'an_range')


def an_dist(b, rep):
    pg = open_page(b, rep['sched'], 'timeline')
    nm = pg.evaluate(f"""(() => {{ const q = {PROBE}, c = new Map(); for (let i = 0; i < q.N; i++) c.set(q.sName[i], (c.get(q.sName[i]) || 0) + 1);
        let best = -1, n = 0; for (const [k, v] of c) if (v > n && !/^(preempt_count|__rcu_read|rcu_read_lock)/.test(q.S[k])) {{ best = k; n = v; }} return best; }})()""")
    pg.evaluate(f"{PROBE}.dist({nm})")
    pg.wait_for_timeout(400)
    shot(pg, 'an_dist')


def an_slowfast(b, rep):
    pg = open_page(b, rep['sched'], 'timeline', height=980)
    index = find(pg, 'next_uptodate_folio')
    pg.evaluate(f"{PROBE}.dist({PROBE}.sName[{index}])")
    pg.locator('[data-act=slowfast]').click()
    handle = box(pg, '.tl-resizer')
    pg.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] + 4)
    pg.mouse.down()
    pg.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] - 200, steps=4)
    pg.mouse.up()
    pg.evaluate("""() => {
        const panel = document.querySelector('.tl-detail');
        const comparison = panel.querySelector('.an-slowfast-tools');
        panel.scrollTop += comparison.getBoundingClientRect().top - panel.getBoundingClientRect().top - panel.querySelector('.tl-detail-head').offsetHeight - 4;
    }""")
    pg.wait_for_timeout(400)
    shot(pg, 'an_slowfast')


def panel(name, key, func):
    def run(b, rep):
        pg = open_page(b, rep[key], 'timeline')
        pg.evaluate(f"{PROBE}.{func}()")
        pg.wait_for_timeout(400)
        shot(pg, name)
    return run


def an_wait(b, rep):
    pg = open_page(b, rep['sched'], 'timeline')
    pg.evaluate(f"""(() => {{ const q = {PROBE}; let best = null;
        for (const t of q.TRACKS) for (const r of t.off) if (r[9] !== null && r[9] >= 0 && (!best || r[2] - r[1] > best[2] - best[1])) best = r;
        q.wait(best || q.TRACKS.find(t => t.off.length).off[0]); }})()""")
    pg.wait_for_timeout(400)
    shot(pg, 'an_wait')


def an_children(b, rep, calls=False):
    pg = open_page(b, rep['sample' if calls else 'sched'], 'timeline', height=1000)
    parent = find(pg, '__skb_recv_udp' if calls else 'path_openat')
    pg.evaluate(f"{PROBE}.select({parent})")
    pg.click('[data-child-open]')
    if calls:
        pg.click('[data-child-mode=calls]')
        pg.fill('[data-child-query]', '__skb_try_recv_from_queue')
    handle = box(pg, '.tl-resizer')
    pg.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] + handle['height'] / 2)
    pg.mouse.down()
    pg.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] - 100, steps=5)
    pg.mouse.up()
    pg.evaluate(f"{PROBE}.zoomTo({PROBE}.sStart[{parent}], {PROBE}.sEnd[{parent}], {parent})")
    pg.wait_for_timeout(400)
    shot(pg, 'an_children_calls' if calls else 'an_children')


def an_tasks(b, rep):
    pg = open_page(b, rep['sample'], 'timeline', height=1000)
    pg.click('[data-act=an-tasks]')
    handle = box(pg, '.tl-resizer')
    pg.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] + handle['height'] / 2)
    pg.mouse.down()
    pg.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] - 100, steps=5)
    pg.mouse.up()
    pg.wait_for_timeout(400)
    shot(pg, 'an_tasks')


def compare(b, rep):
    pg = open_page(b, rep['compare'], 'compare')
    shot(pg, 'compare', {'x': 0, 'y': 0, 'width': W, 'height': 560})


def tl_loading(browser, reports):
    page = open_page(browser, reports['sample'])
    page.evaluate('''() => {
        const originalFrame = window.requestAnimationFrame;
        window.requestAnimationFrame = () => 0;
        try {
            fgViews.timeline.staged = true;
            fgSwitchView('timeline');
        } finally { window.requestAnimationFrame = originalFrame; }
    }''')
    shot(page, 'tl_loading', {'x': 0, 'y': 0, 'width': W, 'height': 420})
    page.evaluate("fgSwitchView('text')")


SHOTS = {
    'text_view': text_view, 'text_folding': text_folding, 'tl_loading': tl_loading, 'tl_layout': tl_layout, 'tl_compact': tl_compact, 'tl_search': tl_search, 'tl_errors': tl_errors, 'tl_dense': tl_dense, 'tl_irqoff': tl_irqoff,
    'tl_offcpu': tl_offcpu, 'tl_untraced': tl_untraced, 'tl_detail': tl_detail, 'tl_inline': tl_inline, 'tl_tail': tl_tail, 'tl_help': tl_help,
    'an_range': an_range, 'an_dist': an_dist, 'an_slowfast': an_slowfast, 'an_sections': panel('an_sections', 'sample', 'sections'),
    'an_errors': panel('an_errors', 'sched', 'errors'), 'an_wait': an_wait, 'an_tasks': an_tasks,
    'an_children': an_children, 'an_children_calls': lambda browser, reports: an_children(browser, reports, calls=True),
    'an_lat': panel('an_lat', 'sched', 'latency'), 'compare': compare,
}


def main():
    names = sys.argv[1:] or list(SHOTS)
    rep = {
        'sample': os.path.join(ROOT, 'sample.html'),
        'sched': report('sched', os.path.join(ROOT, 'tests', 'data', 'pi4_sched.txt')),
        'nodur': report('nodur', os.path.join(ROOT, 'tests', 'data', 'pi4_nodur.txt')),
        'compare': report('compare', '--compare', os.path.join(ROOT, 'samples', 'sample_io.txt'),
                          os.path.join(ROOT, 'samples', 'sample_io_slow.txt')),
    }
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        for n in names:
            SHOTS[n](b, rep)
        b.close()


if __name__ == '__main__':
    main()
