# funcgraph_visualization

![Funcgraph_retaddr Visualization Example](sample.png)

## Introduction

funcgraph_visualization is a powerful ftrace visualization tool, primarily used for:

1. **Visualizing funcgraph-retaddr output**: Converting the Linux kernel's function_graph tracer output to interactive HTML format, significantly improving the efficiency of locating code lines directly through traces
2. **Fast faddr2line implementation**: Rewriting the faddr2line functionality in the Linux kernel using Python, achieving orders of magnitude performance improvement

## Features

- **Interactive HTML output**: Click on functions to jump to corresponding source code locations
- **Kernel module support**: Can parse symbol information of kernel modules
- **Source code linking**: Supports setting base-url to directly link to online code repositories (such as bootlin)
- **High-performance processing**: fastfaddr2line.py is orders of magnitude faster than traditional addr2line methods
- **Flexible parameter configuration**: Supports specifying vmlinux, kernel source code, module directories, etc.

## Requirements

- Python 3.6+
- gawk (for certain data processing functions)
- addr2line tool (usually installed with binutils)
- ELF analysis library (optional, for fastfaddr2line.py)

## Installation

```bash
# Clone repository
git clone https://gitee.com/pengdonglin137/funcgraph_visualization.git
cd funcgraph_visualization

# Ensure scripts have execution permissions
chmod +x *.py
```

## Usage

### Basic Usage

```bash
./funcgraph_to_html.py --vmlinux ./path/to/vmlinux --kernel-src /path/to/linux-source \
    --output output.html ftrace.txt
```

### Complete Parameter Description

```bash
./funcgraph_to_html.py -h
```

Parameter description:
- `ftrace_file`: Path to ftrace output file (required)
- `--vmlinux VMLINUX`: Path to vmlinux file
- `--kernel-src KERNEL_SRC`: Root directory of kernel source code
- `--module-dirs [MODULE_DIRS ...]`: Kernel module search directories
- `--base-url BASE_URL`: Base URL for source code links
- `--output OUTPUT`: Output HTML file path
- `--auto-search`: Automatically search common module directories
- `--verbose`: Enable detailed debug output
- `--fast`: Use fastfaddr2line.py to process vmlinux
- `--use-external`: Force use of external faddr2line

### Advanced Usage

Using fastfaddr2line mode (recommended):

```bash
./funcgraph_to_html.py --fast --vmlinux ./linux-6.18/vmlinux \
    --kernel-src ./linux-6.18 \
    --module-dirs ./linux-6.18/modules_install/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output output.html ftrace.txt
```

### Using fastfaddr2line standalone

```bash
# View help
./fastfaddr2line.py -h

# Parse a single address
./fastfaddr2line.py vmlinux arch_stack_walk+0x150/0x4a8
```

## How to Capture Traces

Execute the following commands on a Linux system to capture function_graph traces:

```bash
# Enter tracing directory
cd /sys/kernel/tracing

# Stop current tracing
echo 0 > tracing_on

# Enable return address and return value display
echo 1 > options/funcgraph-retaddr
echo 1 > options/funcgraph-retval

# Set function_graph tracer
echo function_graph > current_tracer

# Start tracing (stop after 1 second)
echo 1 > tracing_on
sleep 1
echo 0 > tracing_on

# Save trace results
cat trace > ~/ftrace.txt
```

## Project Structure

```
funcgraph_visualization/
├── README.md                    # Chinese documentation
├── README.en.md                 # English documentation
├── funcgraph_to_html.py         # Main program: Convert ftrace to HTML
├── fastfaddr2line.py            # High-performance address resolution tool
├── ftrace.txt                   # Trace data example
├── sample.png                   # Output screenshot
└── sample.html                  # HTML output example
```

## Working Principle

1. **Parse ftrace output**: funcgraph_to_html.py parses function_graph formatted trace data
2. **Extract function addresses**: Get return addresses of each function from the trace
3. **Symbol resolution**: Use fastfaddr2line or addr2line to convert addresses to source code locations
4. **Generate HTML**: Build interactive HTML page to display function call relationships and source code

## Reference Articles

- [Major Upgrade of ftrace Visualization Tool](https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q)
- [ftrace Visualization Tool (Continued)](https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw)
- [Created an ftrace Visualization Tool with Click-to-Jump Support](https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA)

## License

This project follows an open-source license. Please see the LICENSE file in the repository for specific information.

## Contributing

Issues and Pull Requests are welcome to improve this project.
