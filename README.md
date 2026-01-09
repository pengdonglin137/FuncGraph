# funcgraph_visualization

![Funcgraph_retaddr可视化](sample.png)

#### 介绍
用来可视化开启funcgraph-retaddr后的function_graph的trace输出，大幅提高根据trace直接定位到代码行的效率。此外，用python重新实现了Linux内核中的faddr2line的功能，处理性能得到数量级的提升。

#### 使用说明

- 帮助

```bash
$ ./funcgraph_to_html.py  -h
usage: funcgraph_to_html.py [-h] --vmlinux VMLINUX [--kernel-src KERNEL_SRC] [--module-dirs [MODULE_DIRS ...]] [--base-url BASE_URL] [--output OUTPUT] [--auto-search] [--verbose] [--fast] [--use-external]
                            ftrace_file

Convert ftrace output to interactive HTML

positional arguments:
  ftrace_file           Path to ftrace output file

options:
  -h, --help            show this help message and exit
  --vmlinux VMLINUX     Path to vmlinux file
  --kernel-src KERNEL_SRC
                        Path to kernel source root (e.g., /path/to/linux-source)
  --module-dirs [MODULE_DIRS ...]
                        Directories to search for kernel modules
  --base-url BASE_URL   Base URL for source code links
  --output OUTPUT       Output HTML file path
  --auto-search         Automatically search common module directories
  --verbose             Enable verbose output for debugging
  --fast                Use fastfaddr2line.py for vmlinux processing
  --use-external        Force using external faddr2line

```

- 参考
```bash
funcgraph_to_html.py --fast  --vmlinux ./bpf_next/vmlinux --kernel-src ${PWD}/bpf_next  --module-dirs ./bpf_next/modules_install/ --base-url https://elixir.bootlin.com/linux/v6.19-rc1/source --output output1.html ftrace.txt
```

