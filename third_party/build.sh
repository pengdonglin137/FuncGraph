#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
prefix=${1:-"$HOME/.local/funcgraph-raw"}
case "$prefix" in
    /*) ;;
    *) printf 'Installation prefix must be absolute\n' >&2; exit 1 ;;
esac
for library in libtraceevent libtracefs trace-cmd; do
    if [[ ! -f "$root/$library/Makefile" ]]; then
        printf 'Missing submodules. Run: git submodule update --init --recursive\n' >&2
        exit 1
    fi
done
python3 "$root/check_pins.py" --only libtraceevent libtracefs trace-cmd
read -r -a compiler <<< "${CC:-cc}"
read -r -a extra_flags <<< "${EXTRA_CFLAGS:-}"
if ! printf '#include <linux/btf.h>\nint main(void) { return sizeof(struct btf_enum64); }\n' | \
    "${compiler[@]}" "${extra_flags[@]}" -x c - -fsyntax-only 2>/dev/null; then
    printf 'Recent Linux UAPI headers (with struct btf_enum64) are required by libtraceevent 1.9.\n' >&2
    printf 'Install newer linux-libc-dev, or set EXTRA_CFLAGS=-I/path/to/exported/kernel/headers/include\n' >&2
    exit 1
fi
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="$prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
for library in libtraceevent libtracefs trace-cmd; do
    pkgconfig_template="${library/trace-cmd/libtracecmd}.pc.template"
    make -C "$root/$library" -j"${JOBS:-2}" prefix="$prefix" libdir_relative=lib libdir="$prefix/lib" \
        pkgconfig_dir="$prefix/lib/pkgconfig" NO_PYTHON=1 libs
    make -C "$root/$library" -W "$pkgconfig_template" prefix="$prefix" libdir_relative=lib libdir="$prefix/lib" \
        pkgconfig_dir="$prefix/lib/pkgconfig" NO_PYTHON=1 DESTDIR=/ install_libs
done
printf '\nUse these variables when running FuncGraph:\n'
printf 'export PKG_CONFIG_PATH=%q\n' "$PKG_CONFIG_PATH"
printf 'export LD_LIBRARY_PATH=%q\n' "$LD_LIBRARY_PATH"
