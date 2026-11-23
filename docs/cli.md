# Command-line reference

[简体中文](cli.cn.md) · [Back to README](../README.md#quick-start)

Use `fgreport` to read an existing trace and `fgcapture` to record a new one. This reference covers FuncGraph's own arguments and the kernel capture script. The `fguftrace` section explains how to look up the native flags it forwards. Short flags are local to each command: `fgreport -n` previews a command, whereas `fgcapture -n` excludes kernel call trees.

## fgreport

`fgreport [OPTIONS] TRACE ...` or `fgreport [OPTIONS] --uftrace DIR`. Enables Timeline, folding and interactive filtering by default. With `vmlinux`, it also enables source links and picks fast resolution and highlighting according to dependencies and configuration. Put `funcgraph` options after `--`, for example `fgreport -o task.html trace.txt -- --pid 1234`.

| Argument | Meaning and defaults |
| --- | --- |
| `TRACE ...` | One or more raw input files; `-` reads stdin. Use files or `--uftrace`, not both. |
| `--uftrace DIR` | Read a uftrace recording directory. Pass an exported JSONL as a regular file instead. |
| `--uftrace-bin PATH` | Override the automatically selected patched uftrace executable. |
| `-p NAME` / `--preset NAME` | Use a saved preset. By default the wrapper checks `FG_PRESET`, then `[fgreport] preset` in the configuration. |
| `-P` / `--no-preset` | Skip wrapper preset selection; you can still [pass options by hand](../README.md#pass-options-without-a-preset). To have the converter ignore config files, pass `-- --no-config` too. |
| `-o PATH` / `--output PATH` | Write to this HTML file or report directory. By default, derive the `.html` or `.report` name from the first input, or use `stdin` as the stem for stdin. |
| `--open` | Try to open the generated report in a browser. In directory mode this starts `fgview`; stop it with `Ctrl+C`. Omit to generate only. |
| `--report-mode MODE` | `single`, `single-windowed`, `sidecar` or `auto`; see [funcgraph](#funcgraph). Without an override, the default is offline HTML. |
| `--windowed-compression none/gzip` | Compression for embedded data in a windowed HTML report. |
| `--sidecar-threshold BYTES` | Explicit size threshold required by `auto`. |
| `--lite` | Don't automatically add source links, fast resolution or highlighting. Does not ignore configuration or force symbol resolution off. |
| `--text` | In explicit `single` mode, keep Text when the first text input exceeds 150,000 lines. Does not override an explicit `--no-text-view`. |
| `-c BASE` / `--compare BASE` | Add Compare with `BASE` as the baseline file; the positional input is the current trace. |
| `-n` / `--dry-run` | Print the generated `funcgraph` command without generating or opening a report. |
| `-h` / `--help` | Show help. |

Subcommands:

| Argument | Meaning |
| --- | --- |
| `init NAME` | Create or update a preset. The default path is `~/.config/funcgraph/config.ini`; override it with `FG_CONFIG`. Only supplied keys are updated. |
| `init --default` | Set this as the default preset for the wrapper. |
| `init --vmlinux`, `--kernel-src`, `--module-dirs`, `--module-srcs`, `--path-prefix`, `--btf` | Save the corresponding [funcgraph](#funcgraph) symbol, path and type options. `init` accepts one value per option; quote a list of paths as a single argument. |
| `init --base-url`, `--source-browser`, `--source-browser-rev`, `--source-url`, `--source-rev`, `--overhead` | Save the corresponding source, revision and overhead options. Setting `--source-browser` without `--base-url` clears the preset's previous custom link base. |
| `init --report-mode`, `--sidecar-threshold`, `--windowed-compression` | Save the corresponding output options. |
| `init --cross-compile PREFIX` | Save a `CROSS_COMPILE` toolchain prefix. An existing environment variable takes priority. |
| `init --llvm VALUE` | Save the `LLVM` toolchain selection. An existing environment variable takes priority. |
| `init -h` / `init --help` | Show preset-editing options. |
| `presets` | List presets; `*` marks the wrapper's default. No options required. |

## fgcapture

`fgcapture [MODE] [OPTIONS] [-- COMMAND ARGS...]`. Default kernel mode forwards arguments to `fgcapture.sh`. Kernel and mixed capture require root and tracefs; user mode needs suitable instrumentation and uftrace, not tracefs. Start with a short, repeatable workload and a small set of events. See the [capture guide](capture.md) for complete examples.

Kernel and mixed modes refuse to record when `tracing_on` is not `0`; they do not take over existing sessions.

| Argument | Mode and meaning |
| --- | --- |
| `--kernel` | Optional: kernel mode is the default. Record a kernel function graph as text without rebuilding the target application. |
| `--user` | Record user space only to a uftrace directory. |
| `--mixed` | Record user and kernel functions and available scheduling and exec/exit events in one run, to a uftrace directory. |
| `-- COMMAND ARGS...` | Run the target with these arguments; required for user/mixed. Kernel mode limits function tracing to the command and its children, but this does not automatically PID-filter extra events. |
| `-o FILE` | Kernel text destination, default `./ftrace.txt`. Kernel mode only accepts the short spelling. |
| `-o DIR` / `--output DIR` | New user/mixed recording directory, default `user.data` / `mixed.data`; refuses to overwrite. Capture evidence goes into a sibling directory with `.fgcapture` appended to the name. |
| `-d SECONDS` | Kernel only. Record for this long when no command is given; defaults to 1 second. |
| `-p PID[,PID]` | Kernel only. Limit function tracing to these PIDs, not the extra event stream. |
| `-g FUNC[,FUNC]` / `--graph LIST` | Select kernel call-tree roots. Kernel mode uses `-g`; mixed also accepts the long form and defaults to architecture-specific syscall roots. |
| `-n FUNC[,FUNC]` / `--notrace LIST` | Exclude kernel call trees rooted at these functions. Kernel mode uses `-n`; mixed also accepts the long form. |
| `-b KB` | Per-CPU buffer size for kernel and mixed modes, default 16,384 KiB. Not used in user mode. |
| `--kernel-depth N` | Maximum kernel call depth in mixed mode, default 8. Not a kernel-script option. |
| `--args` / `--no-args` | Request arguments by default. Disabling leaves kernel retval/retaddr available, disables automatic user arguments and returns, and also disables kernel args in mixed mode. |
| `--sched` | Enable available scheduling and exec/exit events in kernel mode, enabled by default in mixed mode. In user mode, request native perf scheduling records. |
| `--no-sched` | Disable scheduling in user/mixed; mixed still keeps available exec/exit records. For the kernel script, omit `--sched` instead. |
| `-e SELECTOR` / `--event SELECTOR` | Add a trace event in kernel/mixed, repeatable. Use `SYSTEM:EVENT`, a whole `SYSTEM` or a quoted glob such as `'kvm:kvm_*'`. |
| `--uftrace-bin PATH` | Override recorder auto-selection for user/mixed only. |
| `-A SPEC` / `--argument SPEC` | Forward an additional argument specification to uftrace in user/mixed; repeatable. |
| `-R SPEC` / `--retval SPEC` | Forward an additional return specification to uftrace in user/mixed; repeatable. |
| `--calibrate` | Kernel only. Run tracing overhead calibration and write the result into the text header for use by the report. |
| `--keep` | Kernel only. Do not restore the previous tracefs settings. Avoid for routine capture. |
| `-h` / `--help` | Show Python entry-point help. Use `fgcapture.sh --help` for kernel options. |

`fgcapture.sh` retains this kernel interface, accepts `-e` / `--event` (including `--event=SELECTOR`), and delegates `--user` / `--mixed` to `fgcapture`. The kernel script accepts the `FG_TRACEFS` environment override; mixed mode locates tracefs separately.

## funcgraph

The underlying converter. Unlike `fgreport`, it doesn't automatically enable Timeline, folding or filtering. Pass these options after `--` on `fgreport` instead of switching entry points if you only need an override.

| Input and configuration | Meaning and defaults |
| --- | --- |
| `TRACE ...` | ftrace text, a trace-cmd container or FuncGraph JSONL. Supports `.gz`, `.xz`, `.bz2` and one `-` (stdin). Multiple text inputs can be merged by time. |
| `--input-format auto/text/raw/uftrace` | Auto-detect by default, or choose a decoder. `uftrace` as a file format means FuncGraph JSONL, not a recording directory. |
| `--uftrace DIR` | Decode a recording directory with patched uftrace, instead of positional input. |
| `--uftrace-bin PATH` | Override the recorder executable; defaults to the bundled executable, then uftrace on PATH. |
| `--raw-instance NAME` | Use the named trace-cmd buffer instead of the top-level one; also applies to the Compare baseline. |
| `--config FILE` | Use this INI, otherwise look for `./funcgraph.ini` then `~/.config/funcgraph/config.ini`. |
| `--preset NAME` | Load `[preset NAME]` from the INI. Explicit options override preset values. |
| `--no-config` | Ignore configuration files. |
| `--verbose` | Print additional parsing and diagnostic information. |
| `-h` / `--help` | Show help. |

| Output and views | Meaning and defaults |
| --- | --- |
| `--output PATH` | Output HTML or a dedicated report directory, default `ftrace_viz.html`. The parent directory must exist. |
| `--report-mode MODE` | `single`: one HTML with Full Text; `single-windowed`: offline HTML with on-demand Text; `sidecar`: split resources in a directory; `auto`: choose split resources by threshold. Explicit modes other than `single` need Timeline. If omitted, output offline HTML, automatically gzip-windowed with Timeline and at least 75,000 Text rows. |
| `--windowed-compression none/gzip` | Compression for embedded Text and sources; explicit `single-windowed` defaults to `none`. Gzip requires browser `DecompressionStream` support. |
| `--sidecar-threshold BYTES` | Required for `auto`. Split only when the full, uncompressed UTF-8 HTML is **larger** than this nonnegative byte count. This is not the input file size. |
| `--timeline` | Add a task/CPU Timeline. |
| `--enable-fold` | Enable folding of paired calls in Text. |
| `--filter` | Enable interactive filtering (CPU, PID, event type and other fields) without reducing saved report data. |
| `--compare BASE_TRACE` | Add a per-function comparison against an existing baseline file. |
| `--export FILE.json/FILE.csv` | Also export analysis results: multiple metrics in JSON or per-function statistics in CSV. |
| `--no-text-view` | Omit per-line Text; needs Timeline or Compare. Also skips normal source resolution unless requested. |
| `--keep-source` | With `--timeline --no-text-view`, keep resolved source and inline frames for Timeline. |
| `--overhead CHILD_NS[,SELF_NS]` | Set overhead correction in nanoseconds per child call and per call itself. Defaults to the capture header's calibration when present; does not rewrite recorded timing. |

| Conversion-time filter | Meaning |
| --- | --- |
| `--pid PID[,PID]` | Keep selected tasks by PID. |
| `--comm RE[,RE]` | Select tasks with regular expressions on `comm`. |
| `--cpu N[,N]` | Select call trees by the CPU at entry; descendants or exits may still appear on other CPUs after migration so the call tree stays intact. |
| `--time-range START:END` | Select on call start time: absolute seconds or offsets from the first timestamp, e.g. `+2ms:+5ms`. Either endpoint can be omitted. |
| `--graph RE[,RE]` | Keep call trees rooted at matching functions. |
| `--notrace RE[,RE]` | Drop call trees rooted at matching functions. |

These filters apply to ftrace text and decoded trace-cmd input, not uftrace directories or JSONL. Compare uses the same conditions on both sides. Clearing browser filters won't restore omitted data. If you only want to inspect a CPU or event, start with the Text dropdowns.

| Source, symbols and types | Meaning and defaults |
| --- | --- |
| `--vmlinux FILE` | ELF matching the recorded kernel, for source, links and default BTF. Optional for replay. |
| `--kernel-src DIR` | Local kernel source root. |
| `--user-src DIR` | Local source root for recorded user source. |
| `--module-dirs DIR ...` | Search these directories for kernel `.ko` files. |
| `--module-srcs DIR ...` | Kernel module source roots. |
| `--auto-search` | Also search common module locations. |
| `--path-prefix PREFIX ...` | Strip alternate build-time prefixes so recorded source paths can map to local files. |
| `--entry-offset N` | Add a byte offset to the function entry address, default 0, for builds with instrumented entries. |
| `--fast` | Use the `fastfaddr2line` implementation for `vmlinux` resolution; requires `pyelftools`. |
| `--use-external` | Force the kernel tree's external `faddr2line`; `--fast` wins if both are set. |
| `--highlight-code` | Highlight embedded C source; requires Pygments. |
| `--func-links` | Link resolvable function names to source locations. |
| `--source-browser github/elixir` | Select how online source links are constructed; defaults to GitHub. |
| `--source-browser-rev REV` | Online link tag, branch or commit; defaults to GitHub `master` or Elixir `latest`. Does not select embedded source. |
| `--base-url URL` | Custom online source link base, overriding the built-in browser URL. |
| `--module-url URL:MOD1,MOD2` | Set an online base for the selected modules; repeatable. |
| `--source-url TEMPLATE` | Fetch embedded source using a URL template with `{path}` and optional `{rev}`. |
| `--source-rev REV` | Fill the URL template revision; without `--source-url`, use `git show REV:path` from `--kernel-src`. |
| `--source-cache DIR` | Cache directory for fetched sources, default `~/.cache/funcgraph/src`. |
| `--tail-calls` | With `--timeline`, `--vmlinux`, `pyelftools` and objdump, supplement the report with static tail-jump evidence, not proof that the jump executed. |
| `--btf FILE ...` | Supply raw BTF or ELF files containing BTF; by default, use available BTF from the kernel and resolved modules. |
| `--no-btf` | Disable BTF; fall back to numeric return-value heuristics. |

## fgview

| Argument | Meaning |
| --- | --- |
| `REPORT` | An existing HTML file or a dedicated report directory containing `index.html`. A plain HTML can normally be opened directly in the browser. |
| `--port PORT` | Listen on this loopback port; default `0` selects an available port. |
| `--open` | Open the default browser; otherwise only print the URL. |
| `--version` | Print the FuncGraph version. |
| `-h` / `--help` | Show help. |

Serve only the report path, not your whole workspace or source tree. Press `Ctrl+C` to stop.

## fguftrace

`fguftrace [COMMAND OPTIONS...]` forwards arguments unchanged to the bundled uftrace or, if not bundled, the executable on PATH. Use `fguftrace --help` for general help, `fguftrace record --help` or `fguftrace dump --help` for command-specific options, and `fguftrace --version` for the version. See [uftrace integration](uftrace.md) for FuncGraph's patched export options, including `dump --fgraph --srcline -d DIR`. Normal report generation handles the export for you. `--uftrace-bin PATH` belongs to `fgcapture`, `fgreport` and `funcgraph`, not this launcher.

## fastfaddr2line

Standalone source-address resolver; most users access it through `funcgraph --fast` or `fgreport`.

| Argument | Meaning |
| --- | --- |
| `EXECUTABLE` | ELF to resolve against, default `vmlinux`. |
| `ADDRESS ...` | A function name, `func+0xOFFSET` or `func+0xOFFSET/0xLENGTH`, e.g. `vfs_read+0x10/0x100`. |
| `-e FILE` / `--exe FILE` | Can override the ELF when placed after `<ELF> <queries...>`; it does not replace the leading ELF positional, which would still consume the first query. Prefer the positional form alone. |
| `-f` / `--functions` | Compatibility flag; function names are already shown by default. |
| `-s` / `--basenames` | Show file basenames instead of paths. |
| `-i` / `--inlines` | Compatibility flag; inline frames are already requested by default. |
| `-p` / `--pretty-print` | Compatibility flag; human-readable output is already the default. |
| `-l` / `--list` | Show source context instead of the normal location listing. |
| `-C` / `--demangle` | Accepted, but currently does not enable C++ name demangling. |
| `-v` / `--verbose` | Enable detailed logging. |
| `--path-prefix PREFIX` | Map build-time source prefixes; repeatable. |
| `--module-srcs DIR` | Add module source roots; repeatable. |
| `--entry-offset N` | Add a byte offset to function entries, default 0. |
| `-h` / `--help` | Show help. |
