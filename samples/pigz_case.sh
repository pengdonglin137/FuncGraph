#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo 'Usage: bash samples/pigz_case.sh NEW_WORK_DIRECTORY [PATCHED_UFTRACE=fguftrace]' >&2
    exit 2
fi
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
tracer=$(command -v -- "${2:-fguftrace}") || {
    echo 'Install the native FuncGraph wheel, or supply a patched uftrace path.' >&2
    exit 1
}
tracer=$(realpath -- "$tracer")
test -x "$tracer"
mkdir -- "$1"
cd -- "$1"
work=$PWD

git -c advice.detachedHead=false clone --depth 1 --branch v2.8 https://github.com/madler/pigz.git pigz
git -c advice.detachedHead=false clone --depth 1 --branch v1.3.2 https://github.com/madler/zlib.git zlib
test "$(git -C pigz rev-parse HEAD)" = fe4894f57739e3039a2ffc2a2a360d35e19bacbe
test "$(git -C zlib rev-parse HEAD)" = da607da739fa6047df13e66a2af6b8bec7c2a498

flags='-pg -g -O0 -fno-omit-frame-pointer'
(cd zlib && CFLAGS="$flags" ./configure --static && make -j2 libz.a)
make -C pigz -j2 CFLAGS="$flags -I../zlib" LDFLAGS=-pg LIBS='../zlib/libz.a -lm -pthread'
readelf -d pigz/pigz > dynamic.txt
if grep -q 'NEEDED.*libz\.so' dynamic.txt; then
    echo 'Unexpected dependency on system libz.so' >&2
    exit 1
fi
nm pigz/pigz > symbols.txt
grep -Eq ' T deflate$' symbols.txt

printf 'order_id,customer_id,total\n' > orders.csv
for ((order=1; order<=128; order++)); do
    printf '%04d,C%02d,%d\n' "$order" "$((order % 12))" "$((order * 7))"
done >> orders.csv
"$tracer" record --no-sched --srcline -A 'deflate@arg1/x64,arg2/i32' -R 'deflate@retval/i32' \
    -d capture ./pigz/pigz -p 1 -6 -c orders.csv > orders.csv.gz
./pigz/pigz -dc orders.csv.gz | cmp - orders.csv
"$tracer" dump --fgraph --srcline -d capture > capture.jsonl

ln -s pigz/pigz.c pigz.c
python3 "$root/funcgraph.py" --no-config --timeline --enable-fold --filter --user-src "$work" \
    --output report.html capture.jsonl
echo "Open $work/report.html and inspect single_compress → deflate → deflate_slow → _tr_flush_block."
