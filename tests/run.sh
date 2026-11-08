#!/bin/bash
# FuncGraph self-tests.
#
#   tests/run.sh            unit + end-to-end + browser smoke tests (hermetic, no kernel tree needed)
#   tests/run.sh --golden   also compare reports for real kernels against the recorded baseline
#                           (record it first with: python3 tests/golden.py --update)
#
# Optional environment:
#   FG_VMLINUX / FG_KERNEL_SRC   real vmlinux (+ source tree) for BTF and faddr2line agreement tests
#   FG_TRACES                    extra trace files checked against timeline invariants
set -e
cd "$(dirname "$0")"
echo "=== unit / end-to-end / browser ==="
GOLDEN=0
for a in "$@"; do [ "$a" = "--golden" ] && GOLDEN=1; done
python3 -m unittest discover -s . -p 'test_*.py'
if [ "$GOLDEN" = 1 ]; then
    echo "=== golden output comparison ==="
    python3 golden.py
fi
