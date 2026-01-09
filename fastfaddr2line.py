#!/usr/bin/env python3
import os
import re
import sys
import subprocess
import argparse
import time
from collections import defaultdict
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

class Symbol:
    def __init__(self, name, addr, size, section):
        self.name = name
        self.addr = addr
        self.size = size
        self.section = section

class Section:
    def __init__(self, name, start_addr, end_addr):
        self.name = name
        self.start_addr = start_addr
        self.end_addr = end_addr
        self.symbols = []  # List of Symbols sorted by addr

def parse_arguments():
    parser = argparse.ArgumentParser(description="Python implementation of faddr2line")
    parser.add_argument("executable", nargs="?", default="vmlinux", help="Path to the executable")
    parser.add_argument("addresses", nargs="*", help="Addresses in format func+offset/length")
    parser.add_argument("-e", "--exe", dest="executable", help="Path to the executable")
    parser.add_argument("-f", "--functions", action="store_true", help="Show function names")
    parser.add_argument("-s", "--basenames", action="store_true", help="Strip directory names")
    parser.add_argument("-i", "--inlines", action="store_true", help="Show inline functions")
    parser.add_argument("-p", "--pretty-print", action="store_true", help="Make output more human-readable")
    parser.add_argument("-l", "--list", action="store_true", help="Show source code")
    parser.add_argument("-C", "--demangle", action="store_true", help="Demangle C++ symbols")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    return parser.parse_args()

def log(message, verbose_flag):
    if verbose_flag:
        # 添加时间戳
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        millis = int((time.time() % 1) * 1000)
        print(f"[{timestamp}.{millis:03d}] {message}", file=sys.stderr)

def shorten_path(path, kernel_src_root, basename_only=False):
    """Convert absolute path to relative path based on kernel source root"""
    if not kernel_src_root or not path.startswith(kernel_src_root):
        if basename_only:
            return os.path.basename(path)
        return path
    
    # Extract relative path
    rel_path = path[len(kernel_src_root):].lstrip("/")
    
    if basename_only:
        return os.path.basename(rel_path)
    
    return rel_path

def load_elf_data(executable, verbose=False):
    log(f"Loading ELF data from {executable}", verbose)
    start_time = time.time()
    
    with open(executable, "rb") as f:
        elf = ELFFile(f)
        symtab = elf.get_section_by_name(".symtab")
        if not symtab or not isinstance(symtab, SymbolTableSection):
            raise ValueError("No symbol table found")

        sections = {}
        symbol_by_name = defaultdict(list)
        func_count = 0
        
        for sym in symtab.iter_symbols():
            if sym.entry.st_info.type != "STT_FUNC":
                continue
                
            name = sym.name
            addr = sym.entry.st_value
            size = sym.entry.st_size
            shndx = sym.entry.st_shndx
            
            if shndx == "SHN_UNDEF" or shndx == 0:
                continue
                
            try:
                section = elf.get_section(shndx)
                if not section:
                    continue
                    
                sec_name = section.name
                if sec_name not in sections:
                    sec_start = section["sh_addr"]
                    sec_end = sec_start + section["sh_size"]
                    sections[sec_name] = Section(sec_name, sec_start, sec_end)
                    log(f"Created section {sec_name}: 0x{sec_start:x}-0x{sec_end:x}", verbose)
                
                sec_obj = sections[sec_name]
                symbol = Symbol(name, addr, size, sec_name)
                sec_obj.symbols.append(symbol)
                symbol_by_name[name].append(symbol)
                func_count += 1
            except Exception as e:
                log(f"Error processing symbol {name}: {str(e)}", verbose)
                continue
        
        # Sort symbols within each section by address
        for sec_name, sec_obj in sections.items():
            sec_obj.symbols.sort(key=lambda s: s.addr)
            log(f"Section {sec_name} has {len(sec_obj.symbols)} symbols", verbose)
        
        elapsed = (time.time() - start_time) * 1000
        log(f"Loaded {func_count} functions across {len(sections)} sections in {elapsed:.2f}ms", verbose)
        return symbol_by_name, sections, elf

def find_matching_symbol(func_name, offset, length, symbol_by_name, sections, verbose=False):
    log(f"Searching for symbol: {func_name}+0x{offset:x}/0x{length:x}", verbose)
    start_time = time.time()
    
    if func_name not in symbol_by_name:
        log(f"Symbol '{func_name}' not found in symbol table", verbose)
        return None
    
    candidates = symbol_by_name[func_name]
    log(f"Found {len(candidates)} candidate symbols for '{func_name}'", verbose)
    
    for sym in candidates:
        sec_name = sym.section
        if sec_name not in sections:
            log(f"Section {sec_name} not found for symbol {func_name}", verbose)
            continue
            
        sec = sections[sec_name]
        symbols = sec.symbols
        
        # Use linear search instead of binary search
        pos = -1
        for i, s in enumerate(symbols):
            if s.addr == sym.addr:
                pos = i
                break
                
        if pos == -1:
            log(f"Symbol {func_name} not found in section {sec_name}", verbose)
            continue
            
        # Improved calculation of actual symbol length
        # Skip symbols with same address (like aliases)
        actual_len = None
        index = pos + 1
        while index < len(symbols):
            next_sym = symbols[index]
            if next_sym.addr > sym.addr:
                actual_len = next_sym.addr - sym.addr
                break
            index += 1
        
        # If no next symbol with different address found, use section end
        if actual_len is None:
            actual_len = sec.end_addr - sym.addr
            
        log(f"Candidate symbol at 0x{sym.addr:x}, calculated length: 0x{actual_len:x}", verbose)
        
        # Compare with requested length
        if actual_len == length:
            target_addr = sym.addr + offset
            elapsed = (time.time() - start_time) * 1000
            log(f"Match found! Target address: 0x{target_addr:x} (search took {elapsed:.2f}ms)", verbose)
            return sym.addr, sec_name, target_addr
    
    elapsed = (time.time() - start_time) * 1000
    log(f"No matching symbol found for {func_name}+0x{offset:x}/0x{length:x} (search took {elapsed:.2f}ms)", verbose)
    return None

def start_addr2line(executable, cross_compile="", verbose=False):
    # Build addr2line command with cross-compilation prefix
    prefix = cross_compile if cross_compile else ""
    addr2line_cmd = prefix + "addr2line"
    
    cmd = [
        addr2line_cmd,
        "--functions",
        "--pretty-print",
        "--inlines",
        "--addresses",
        f"--exe={executable}"
    ]
    
    log(f"Starting addr2line: {' '.join(cmd)}", verbose)
    start_time = time.time()
    
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,  # 文本模式
        bufsize=1   # 行缓冲
    )
    
    elapsed = (time.time() - start_time) * 1000
    log(f"addr2line process started in {elapsed:.2f}ms", verbose)
    return proc

def get_kernel_src_root(proc, start_addr, verbose=False):
    """Get kernel source root by analyzing addr2line output for start_kernel"""
    log(f"Detecting kernel source root using start_kernel at 0x{start_addr:x}", verbose)
    start_time = time.time()
    
    # 发送地址和哨兵值（按照正确顺序）
    proc.stdin.write(f"{start_addr:x}\n")
    proc.stdin.write(",\n")
    proc.stdin.flush()
    log(f"Sent start_kernel address 0x{start_addr:x} and sentinel ',' to addr2line", verbose)
    
    # 读取输出 - 简化版本，不使用select
    output_lines = []
    max_lines = 100  # 防止无限循环
    
    for _ in range(max_lines):
        line = proc.stdout.readline()
        if not line:  # EOF
            break
            
        line_stripped = line.rstrip()
        if not line_stripped:  # 空行
            continue
            
        log(f"Read line: {line_stripped}", verbose)
        
        # 检查终止条件 - 已按要求修改正则表达式
        if (line_stripped == "?? ??:0" or 
            line_stripped == "," or 
            re.match(r'^0x00*: ', line_stripped)):
            log(f"Encountered termination line: {line_stripped}", verbose)
            break
            
        # 移除开头的地址部分
        cleaned_line = re.sub(r'^0x[0-9a-fA-F]+: ', '', line_stripped)
        output_lines.append(cleaned_line)
    
    elapsed = (time.time() - start_time) * 1000
    log(f"Got kernel source root output in {elapsed:.2f}ms", verbose)
    
    if not output_lines:
        log("No output from addr2line for start_kernel", verbose)
        return ""
    
    # 合并输出文本
    output_text = "\n".join(output_lines)
    log(f"Full output: {output_text}", verbose)
    
    # 关键修复：使用更灵活的正则表达式匹配路径
    pattern = r'at\s+([^:\s]+):\d+'
    match = re.search(pattern, output_text)
    
    if match:
        full_path = match.group(1)
        log(f"Found source path: {full_path}", verbose)
        
        # 尝试提取内核源码根目录
        common_dirs = ["/arch/", "/block/", "/crypto/", "/drivers/", "/fs/", "/include/", 
                      "/init/", "/ipc/", "/kernel/", "/lib/", "/mm/", "/net/", "/samples/", 
                      "/scripts/", "/security/", "/sound/", "/tools/", "/usr/", "/virt/"]
        
        best_prefix = ""
        for dir_suffix in common_dirs:
            idx = full_path.find(dir_suffix)
            if idx > 0:
                prefix = full_path[:idx]
                if len(prefix) > len(best_prefix):
                    best_prefix = prefix
        
        if best_prefix:
            kernel_src_root = best_prefix.rstrip('/')
            log(f"Detected kernel source root from common dirs: {kernel_src_root}", verbose)
            return kernel_src_root
        
        init_main_match = re.search(r'([^:\s]+/init/main\.c):\d+', output_text)
        if init_main_match:
            full_init_path = init_main_match.group(1)
            kernel_src_root = full_init_path.rsplit('/init/main.c', 1)[0]
            log(f"Detected kernel source root from init/main.c: {kernel_src_root}", verbose)
            return kernel_src_root
        
        file_match = re.search(r'([^:\s]+/[a-zA-Z0-9_\-]+\.c):\d+', output_text)
        if file_match:
            full_file_path = file_match.group(1)
            parts = full_file_path.split('/')
            if len(parts) > 2:
                for i in range(1, len(parts)-1):
                    candidate = '/'.join(parts[:i])
                    if os.path.exists(candidate) or os.path.isdir(candidate):
                        kernel_src_root = candidate
                        log(f"Detected kernel source root candidate: {kernel_src_root}", verbose)
                        return kernel_src_root
        
        kernel_src_root = os.path.dirname(full_path)
        log(f"Using directory of source file as root: {kernel_src_root}", verbose)
        return kernel_src_root
    
    file_match = re.search(r'([^:\s]+/[a-zA-Z0-9_\-]+\.[chS]):\d+', output_text)
    if file_match:
        full_path = file_match.group(1)
        log(f"Found source file via fallback: {full_path}", verbose)
        
        init_main_match = re.search(r'([^:\s]+/init/main\.c):\d+', output_text)
        if init_main_match:
            full_init_path = init_main_match.group(1)
            kernel_src_root = full_init_path.rsplit('/init/main.c', 1)[0]
            log(f"Detected kernel source root from init/main.c: {kernel_src_root}", verbose)
            return kernel_src_root
        
        best_prefix = ""
        for dir_suffix in common_dirs:
            idx = full_path.find(dir_suffix)
            if idx > 0:
                prefix = full_path[:idx]
                if len(prefix) > len(best_prefix):
                    best_prefix = prefix
        
        if best_prefix:
            kernel_src_root = best_prefix.rstrip('/')
            log(f"Detected kernel source root from common dirs: {kernel_src_root}", verbose)
            return kernel_src_root
    
    log("Could not determine kernel source root", verbose)
    return ""

def resolve_addresses(proc, addr_specs_and_addrs, kernel_src_root, options):
    """Resolve addresses using addr2line one by one with empty line separation"""
    for i, (addr_spec, addr) in enumerate(addr_specs_and_addrs):
        log(f"Processing address {i+1}/{len(addr_specs_and_addrs)}: {addr_spec} -> 0x{addr:x}", options.verbose)
        start_time = time.time()
        
        # 关键修复：按照faddr2line的注释实现协议
        # 1. 发送地址
        proc.stdin.write(f"{addr:x}\n")
        proc.stdin.flush()
        log(f"Sent address 0x{addr:x} to addr2line", options.verbose)
        
        # 2. 发送哨兵值（逗号）
        proc.stdin.write(",\n")
        proc.stdin.flush()
        log(f"Sent sentinel ',' to addr2line", options.verbose)
        
        # 3. 读取输出 - 简化版本，不使用select
        output_lines = []
        raw_lines = []  # 保存原始行（未处理路径）
        max_lines = 100  # 防止无限循环
        
        for _ in range(max_lines):
            line = proc.stdout.readline()
            if not line:  # EOF
                break
                
            line_stripped = line.rstrip()
            if not line_stripped:  # 空行
                continue
                
            log(f"Read line: {line_stripped}", options.verbose)
            
            # 关键修复：先检查终止条件，再处理行内容
            if (line_stripped == "?? ??:0" or 
                line_stripped == "," or 
                re.match(r'^0x00*: ', line_stripped)):
                log(f"Encountered termination line: {line_stripped}", options.verbose)
                break
                
            # 移除开头的地址部分
            cleaned_line_raw = re.sub(r'^0x[0-9a-fA-F]+: ', '', line_stripped)
            raw_lines.append(cleaned_line_raw)
            
            # 对内容行进行路径处理
            cleaned_line = cleaned_line_raw
            if kernel_src_root:
                cleaned_line = re.sub(
                    r'([^ \t\n]+/[a-zA-Z0-9_\-]+\.[chS]):(\d+)',
                    lambda m: shorten_path(m.group(1), kernel_src_root, options.basenames) + ":" + m.group(2),
                    cleaned_line
                )
            elif options.basenames:
                match = re.search(r'([^ \t\n]+/([a-zA-Z0-9_\-]+\.[chS])):(\d+)', cleaned_line)
                if match:
                    cleaned_line = cleaned_line.replace(match.group(1), match.group(2))
            
            output_lines.append(cleaned_line)
        
        elapsed = (time.time() - start_time) * 1000
        log(f"Got output for 0x{addr:x} in {elapsed:.2f}ms", options.verbose)
        
        # 打印地址规范头（像faddr2line一样）
        print(f"{addr_spec}:")
        
        if not output_lines:
            log(f"No output received for address 0x{addr:x}", options.verbose)
            print("??:0")
        else:
            # 关键修改：如果开启了list选项，则不打印处理后的行
            if not options.list:
                # 打印处理后的所有行
                for line in output_lines:
                    print(line)
        
        # 如果启用了列表选项，显示源代码上下文（替代处理后的行）
        if options.list:
            print_source_code(raw_lines, kernel_src_root, options)
        
        # 关键修改：在处理完一个地址规范后，如果不是最后一个，则输出空行分隔
        if i < len(addr_specs_and_addrs) - 1:
            print()

def print_source_code(lines, kernel_src_root, options):
    """Print source code context for given lines in the desired format with alignment"""
    if not lines:
        return
        
    # 用于跟踪已处理的文件位置，避免重复输出
    processed_locations = set()
    
    for line in lines:
        # 跳过空行
        if not line.strip():
            continue
            
        # 匹配文件路径和行号（包括内联函数）
        # 匹配格式如：path/to/file.c:123 或 (inlined by) path/to/file.c:123
        file_match = re.search(r'([^ \t\n]+):(\d+)', line)
        if not file_match:
            continue
            
        file_path = file_match.group(1)
        line_num = int(file_match.group(2))
        
        # 关键修复：过滤无效行（如 "??:0"）
        if file_path == "??" or line_num == 0:
            log(f"Skipping invalid source location: {file_path}:{line_num}", options.verbose)
            continue
            
        location_key = (file_path, line_num)
        
        # 避免重复处理相同的位置
        if location_key in processed_locations:
            continue
        processed_locations.add(location_key)
        
        # 打印位置行（原始行）
        print(line)
        
        try:
            # 尝试查找文件
            search_paths = [file_path]
            if kernel_src_root and file_path.startswith(kernel_src_root):
                rel_file = file_path[len(kernel_src_root):].lstrip("/")
                search_paths.append(rel_file)
            
            # 添加basename版本
            basename = os.path.basename(file_path)
            search_paths.append(basename)
            if kernel_src_root:
                search_paths.append(os.path.join(kernel_src_root, basename))
            
            full_path = None
            for path in search_paths:
                if os.path.exists(path):
                    full_path = path
                    break
            
            if not full_path:
                print(f"  Source file not found: {file_path}")
                continue
            
            with open(full_path, "r") as f:
                all_lines = f.readlines()
            
            # 计算要显示的上下文范围
            start = max(0, line_num - 6)
            end = min(len(all_lines), line_num + 5)
            
            # 打印上下文 - 使用制表符对齐
            for i in range(start, end):
                if i + 1 == line_num:
                    # 当前行：使用 >行号< 格式，后接制表符
                    print(f">{i+1}<\t{all_lines[i].rstrip()}")
                else:
                    # 非当前行：行号前后各加一个空格，后接制表符
                    print(f" {i+1} \t{all_lines[i].rstrip()}")
                
        except Exception as e:
            print(f"  Error reading source: {str(e)}")
        
        # 添加空行分隔不同位置
        print()

def main():
    # 先解析参数
    options = parse_arguments()
    
    # 记录脚本启动时间（只在verbose模式下输出）
    start_time = time.time()
    log("Script started", options.verbose)
    
    executable = options.executable
    
    # 从环境变量获取交叉编译前缀
    cross_compile = os.environ.get("CROSS_COMPILE", "")
    if cross_compile and not cross_compile.endswith("-"):
        cross_compile += "-"
    
    log(f"Using cross-compilation prefix: '{cross_compile}'", options.verbose)
    
    try:
        symbol_by_name, sections, elf = load_elf_data(executable, options.verbose)
    except Exception as e:
        print(f"Error loading ELF data: {str(e)}", file=sys.stderr)
        sys.exit(1)
    
    # 启动addr2line进程
    try:
        proc = start_addr2line(executable, cross_compile, options.verbose)
    except FileNotFoundError:
        print(f"Error: {cross_compile}addr2line not found. Please install binutils.", file=sys.stderr)
        sys.exit(1)
    
    # 查找start_kernel地址以检测源码根目录
    kernel_src_root = ""
    if "start_kernel" in symbol_by_name:
        start_sym = symbol_by_name["start_kernel"][0]
        kernel_src_root = get_kernel_src_root(proc, start_sym.addr, options.verbose)
        log(f"Detected kernel source root: {kernel_src_root}", options.verbose)
    else:
        log("start_kernel symbol not found for source root detection", options.verbose)
    
    # 处理每个地址规范
    addr_specs_and_addrs = []
    addr_pattern = re.compile(r"([^+]+)\+0x([0-9a-fA-F]+)/0x([0-9a-fA-F]+)")
    
    for addr_spec in options.addresses:
        match = addr_pattern.match(addr_spec)
        if not match:
            print(f"Invalid address format: {addr_spec}", file=sys.stderr)
            continue
            
        func_name = match.group(1)
        offset = int(match.group(2), 16)
        length = int(match.group(3), 16)
        
        result = find_matching_symbol(func_name, offset, length, symbol_by_name, sections, options.verbose)
        if not result:
            print(f"Symbol not found: {addr_spec}", file=sys.stderr)
            continue
            
        sym_addr, _, target_addr = result
        addr_specs_and_addrs.append((addr_spec, target_addr))
        log(f"Resolved {addr_spec} -> 0x{target_addr:x}", options.verbose)
    
    # 使用addr2line解析地址
    if addr_specs_and_addrs:
        resolve_addresses(proc, addr_specs_and_addrs, kernel_src_root, options)
    else:
        log("No valid addresses to resolve", options.verbose)
    
    # 清理
    try:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=1)
    except:
        pass
    
    # 记录脚本完成时间（只在verbose模式下输出）
    total_elapsed = (time.time() - start_time) * 1000
    log(f"Script completed in {total_elapsed:.2f}ms", options.verbose)

if __name__ == "__main__":
    main()
