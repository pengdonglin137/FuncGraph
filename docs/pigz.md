# Case study: trace a gzip export with pigz and zlib

Compress a small CSV of synthetic orders with pigz v2.8 and its instrumented
zlib v1.3.2 dependency. The first run needs no root, `vmlinux` or tracefs.
Use x86-64 Linux with Git, GCC, Make, binutils, Python and the
[patched uftrace build](uftrace.md#reproducible-native-build).

## 1. Build and run

From the FuncGraph checkout, choose a new work directory:

```bash
bash samples/pigz_case.sh /tmp/fg-pigz build/uftrace-x86_64/bin/uftrace
```

The script pins both source commits. To instrument an existing build manually,
rebuild **both** projects from clean trees:

```bash
flags='-pg -g -O0 -fno-omit-frame-pointer'
(cd zlib && CFLAGS="$flags" ./configure --static && make -j2 libz.a)
make -C pigz CFLAGS="$flags -I../zlib" LDFLAGS=-pg LIBS='../zlib/libz.a -lm -pthread'
```

`-pg` enables function recording; link the executable with it as well. `-g`
keeps file/line information; `-O0` and `-fno-omit-frame-pointer` make the
debug call chain easier to follow. This is a diagnostic build, not a throughput
benchmark. Use the explicit `libz.a`, not `-lz`: the script checks that
`deflate` is defined in the binary and that there is no `libz.so` dependency.

## 2. Inspect the compression trace

The script captures `pigz -p 1 -6 -c orders.csv`, verifies that decompression
reproduces the original CSV byte-for-byte, and generates `/tmp/fg-pigz/report.html`.
Switch to **Timeline** and search for `deflate`. The observed chain goes from
`process → single_compress` in pigz through `deflate → deflate_slow → _tr_flush_block`
in zlib. The explicit `-A`/`-R` specs record the `z_stream` pointer (hex, not
dereferenced), `flush = 4` (`Z_FINISH`), and return `1` (`Z_STREAM_END`). The
detail panel shows the recorded function address separately from the argument
pointer. Text shows argument values on the entry and the result on the exit.
`-p 1` makes the example easy to follow; it does not demonstrate multi-worker behavior.
Click `pigz.c:2266`, `deflate.c:981` or `trees.c:997` in the stack to open the
embedded source excerpt, or expand the corresponding entry in Text. These
locations do not link to a kernel repository.

## 3. Follow the process from exec to exit

Do this only in an **idle experimental VM with tracefs snapshot/restore**;
see [capture safety](uftrace.md#capture-safety-and-current-limits). The following
is the recorder command to run inside that guarded session (x86-64). Run the
build above on the VM first and reuse its workspace and `orders.csv`.

```bash
uftrace record -K 30 --clock=mono --kernel-full-info --no-sched --srcline \
    -F '__x64_sys_read@kernel;__x64_sys_write@kernel' \
    -E 'sched:sched_process_exec@kernel;sched:sched_process_exit@kernel' \
    -A 'deflate@arg1/x64,arg2/i32' -R 'deflate@retval/i32' \
    -d capture-mixed ./pigz/pigz -p 1 -6 -c orders.csv > orders-mixed.csv.gz
./pigz/pigz -dc orders-mixed.csv.gz | cmp - orders.csv
uftrace dump --fgraph --srcline -d capture-mixed > capture-mixed.jsonl
funcgraph capture-mixed.jsonl --timeline --enable-fold --filter --user-src "$PWD" --output mixed.html
```

In Timeline, click **Fit** and then **Exec / exit**. The panel links each
milestone to its original Text record. The **Markers** overlay draws ticks
on the task track. `sched_process_exec` means the new executable image was
installed, not the start of the `execve` syscall; `sched_process_exit` marks
task exit, not when the parent reaps it. No missing endpoint is inferred.

The scoped QEMU Linux `7.4.0-rc2` run took about **277 ms** from the recorded
exec event to exit, including tracing and startup/cleanup; it is not a speed
benchmark. Four read and four write roots in the main workload linked to user
calls. The archive round-tripped byte-for-byte.

A short capture can still produce a large report (about 89 MiB as a single HTML
file for this run). To open Timeline before loading Text, replace
`--output mixed.html` with `--report-mode sidecar --output mixed-report`, then run
`fgview mixed-report --open` (see the [viewer guide](viewer.md)).

Find `read` or `write` in the pigz path. The divider is over its linked kernel
root, not over `deflate`: compression runs in user space, and the I/O calls
enter the kernel separately. The output is redirected to a file, so the
recorded write path need not match a pipe or terminal. Keep the original build
tree to embed source; recorded absolute source positions are not remapped when
the capture is copied to another machine.
