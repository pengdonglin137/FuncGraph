#!/usr/bin/env python3
"""Golden-output regression test against real kernel trees.

Generates reports for several option combinations and compares them byte for
byte (after masking timings) with a saved baseline. Use it around refactors:

    python3 tests/golden.py --update      # before the change: record baseline
    python3 tests/golden.py               # after the change: must report "same" everywhere

Kernel trees are taken from FG_X86_KERNEL / FG_PI4_KERNEL (defaults below);
configurations whose inputs are missing are skipped. Baselines live in
FG_GOLDEN_DIR (default /tmp/fg_golden). FG_ROOT selects the checkout whose
funcgraph.py is run (default: this one), e.g. a `git worktree` of the old version.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Code under test (defaults to this checkout; point it at another worktree to compare versions)
PROG = os.path.join(os.environ.get('FG_ROOT', ROOT), 'funcgraph.py')
X86 = os.environ.get('FG_X86_KERNEL', '/vol_1t/Qemu/x86_64/linux-6.18')
PI4 = os.environ.get('FG_PI4_KERNEL', '/vol_1t/raspi/linux')
VM72 = os.environ.get('FG_VM72_KERNEL', '/vol_1t/Qemu/trim_retval')
X86_MODS = os.environ.get('FG_X86_MODULES', '/vol_1t/Qemu/x86_64/modules_')
GOLDEN = os.environ.get('FG_GOLDEN_DIR', '/tmp/fg_golden')
DATA = os.path.join(ROOT, 'tests', 'data')
BOOTLIN = 'https://elixir.bootlin.com/linux/v6.18/source'

# name -> (environment, required paths, argv)
CONFIGS = {
    'x86_full': ({}, [f'{X86}/x86_64/vmlinux'], [
        '--enable-fold', '--func-links', '--fast', '--filter', '--highlight-code',
        '--vmlinux', f'{X86}/x86_64/vmlinux', '--kernel-src', X86,
        '--module-dirs', f'{X86}/x86_64/modules_install/', X86_MODS + '/',
        '--module-src', X86_MODS + '/', X86, '--path-prefix', '/vol_1t/Qemu/x86_64/modules/',
        '--base-url', BOOTLIN, '--entry-offset', '0', os.path.join(ROOT, 'ftrace.txt')]),
    'x86_min': ({}, [f'{X86}/x86_64/vmlinux'], [
        '--fast', '--vmlinux', f'{X86}/x86_64/vmlinux', os.path.join(ROOT, 'ftrace.txt')]),
    'x86_timeline': ({}, [f'{X86}/x86_64/vmlinux'], [
        '--timeline', '--enable-fold', '--fast', '--filter', '--vmlinux', f'{X86}/x86_64/vmlinux',
        '--kernel-src', X86, os.path.join(ROOT, 'ftrace.txt')]),
    'pi4_full': ({'CROSS_COMPILE': 'aarch64-linux-gnu-'}, [f'{PI4}/vmlinux'], [
        '--timeline', '--enable-fold', '--func-links', '--fast', '--filter', '--highlight-code',
        '--vmlinux', f'{PI4}/vmlinux', '--kernel-src', PI4, '--base-url', BOOTLIN,
        os.path.join(DATA, 'pi4_full.txt')]),
    'pi4_default': ({'CROSS_COMPILE': 'aarch64-linux-gnu-'}, [f'{PI4}/vmlinux'], [
        '--timeline', '--enable-fold', '--fast', '--filter', '--vmlinux', f'{PI4}/vmlinux',
        os.path.join(DATA, 'pi4_default.txt')]),
    'vm72_full': ({}, [f'{VM72}/x86_64/vmlinux'], [
        '--timeline', '--enable-fold', '--func-links', '--fast', '--filter', '--vmlinux', f'{VM72}/x86_64/vmlinux',
        '--kernel-src', VM72, '--base-url', BOOTLIN, os.path.join(DATA, 'vm72_full.txt')]),
    'pi4_sched_nobtf': ({'CROSS_COMPILE': 'aarch64-linux-gnu-'}, [f'{PI4}/vmlinux'], [
        '--timeline', '--no-btf', '--fast', '--vmlinux', f'{PI4}/vmlinux',
        os.path.join(DATA, 'pi4_sched.txt')]),
}

MASKS = [
    # Timing rows are only emitted when the measured time is > 0, so drop them entirely
    (re.compile(r' *<div class="info-item"><div class="info-label">(?:Parse Time|Vmlinux Time|Modules Time|Total Time):</div>.*?</div></div>\n'), ''),
    (re.compile(r'(<div class="info-label">(?:Output|Generated At|Python|OS|Hostname|Processor):</div><div class="info-value">)[^<]*'), r'\1#'),
    (re.compile(r'"build_time":[0-9.]+'), '"build_time":#'),
]


def normalize(text):
    for rx, rep in MASKS:
        text = rx.sub(rep, text)
    return text


def generate(name, outdir):
    env_extra, needs, argv = CONFIGS[name]
    missing = [p for p in needs if not os.path.exists(p)]
    if missing:
        return None, f'skip ({missing[0]} not found)'
    out = os.path.join(outdir, name + '.html')
    env = dict(os.environ)
    env.pop('CROSS_COMPILE', None)
    env.update(env_extra)
    t = time.time()
    p = subprocess.run([sys.executable, PROG, '--output', out] + argv,
                       capture_output=True, text=True, env=env)
    if p.returncode != 0:
        return None, f'FAILED to generate:\n{p.stderr[-1500:]}'
    with open(out, encoding='utf-8', newline='') as f:
        return normalize(f.read()), f'{time.time() - t:.1f}s'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--update', action='store_true', help='record a new baseline')
    ap.add_argument('--only', nargs='*', help='limit to these configurations')
    args = ap.parse_args()
    names = args.only or list(CONFIGS)
    work = os.path.join(GOLDEN, 'baseline' if args.update else 'current')
    os.makedirs(work, exist_ok=True)
    bad = ran = 0
    for name in names:
        text, info = generate(name, work)
        if text is None:
            print(f'  {name:18s} {info}')
            bad += info.startswith('FAILED')
            continue
        ran += 1
        if args.update:
            print(f'  {name:18s} recorded ({info})')
            continue
        base_path = os.path.join(GOLDEN, 'baseline', name + '.html')
        if not os.path.exists(base_path):
            print(f'  {name:18s} no baseline, run with --update first')
            bad += 1
            continue
        with open(base_path, encoding='utf-8', newline='') as f:
            base = normalize(f.read())
        if base == text:
            print(f'  {name:18s} same ({info})')
            continue
        bad += 1
        i = next((k for k in range(min(len(base), len(text))) if base[k] != text[k]), min(len(base), len(text)))
        print(f'  {name:18s} DIFF at byte {i}\n      baseline: {base[max(0, i - 100):i + 100]!r}\n      current : {text[max(0, i - 100):i + 100]!r}')
    if not args.update:
        if not os.environ.get('FG_GOLDEN_KEEP'):
            shutil.rmtree(work, ignore_errors=True)
    print('golden:', 'recorded' if args.update else ('PASS' if not bad and ran else 'FAIL' if bad else 'nothing to compare'))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
