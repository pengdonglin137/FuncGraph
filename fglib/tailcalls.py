"""Opt-in static tail-jump evidence for recorded caller/parent mismatches."""

import hashlib
import os
import re
import struct
import subprocess
import tempfile

from .timeline import F_CALLER_MISMATCH

MAX_FUNCTION_BYTES = 256 * 1024
MAX_CODE_BYTES = 4 * 1024 * 1024
MAX_FUNCTIONS = 4096
MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def objdump_command():
    llvm = os.environ.get('LLVM', '')
    if llvm:
        prefix = llvm + 'llvm-' if llvm.endswith('/') else 'llvm-'
        return prefix + 'objdump' + (llvm if llvm.startswith('-') else '')
    prefix = os.environ.get('CROSS_COMPILE', '')
    return prefix + ('' if not prefix or prefix.endswith('-') else '-') + 'objdump'


def _pairs(data):
    columns = data['cols']
    pairs = {}
    modules = columns.get('module')
    for index, parent in enumerate(columns['parent']):
        if parent < 0 or not columns['flags'][index] & F_CALLER_MISMATCH:
            continue
        parent_module = modules[parent] if modules else -1
        child_module = modules[index] if modules else -1
        coordinates = [columns['name'][parent], parent_module, columns['name'][index], child_module]
        key = ':'.join(map(str, coordinates))
        if key in pairs:
            continue
        pairs[key] = dict(parent=coordinates[0], parent_module=parent_module, child=coordinates[2],
                          child_module=child_module, status='unresolved', reason='no_direct_tail_jump', sites=[])
    return pairs


def _write_image(path, functions, machine):
    """Give objdump only selected bytes at their original linked addresses."""
    image = bytearray(64)
    names = bytearray(b'\0')
    sections = [(0,) * 10]
    for index, function in enumerate(functions):
        name = len(names)
        names.extend(('.fg' + str(index)).encode() + b'\0')
        sections.append((name, 1, 6, function['address'], len(image), len(function['code']), 0, 0, 1, 0))
        image.extend(function['code'])
    name = len(names)
    names.extend(b'.shstrtab\0')
    sections.append((name, 3, 0, 0, len(image), len(names), 0, 0, 1, 0))
    image.extend(names)
    image.extend(b'\0' * (-len(image) % 8))
    section_offset = len(image)
    for section in sections:
        image.extend(struct.pack('<IIQQQQIIQQ', *section))
    ident = b'\x7fELF\x02\x01\x01' + b'\0' * 9
    image[:64] = struct.pack('<16sHHIQQQIHHHHHH', ident, 2, machine, 1, 0, 0, section_offset,
                             0, 64, 0, 0, 64, len(sections), len(sections) - 1)
    with open(path, 'wb') as stream:
        stream.write(image)


def parse_disassembly(text, functions, machine):
    """Keep direct external unconditional jumps, never calls or internal branches."""
    result = [[] for function in functions]
    current = None
    mnemonic = {'jmp', 'jmpq'} if machine == 62 else {'b'}
    for line in text.splitlines():
        section = re.fullmatch(r'Disassembly of section \.fg([0-9]+):', line.strip())
        if section:
            current = int(section.group(1))
            continue
        if current is None or current >= len(functions):
            continue
        instruction = re.fullmatch(r'\s*([0-9a-fA-F]+):\s+([a-zA-Z0-9_.]+)\s*(.*?)\s*', line)
        if not instruction or instruction.group(2) not in mnemonic:
            continue
        operand = re.match(r'(?:0x)?([0-9a-fA-F]+)(?:\s|$)', instruction.group(3))
        if not operand:
            continue
        address, target = int(instruction.group(1), 16), int(operand.group(1), 16)
        function = functions[current]
        start, end = function['address'], function['address'] + len(function['code'])
        if start <= address < end and not start <= target < end:
            result[current].append(dict(address=hex(address), offset=address - start,
                                        mnemonic=instruction.group(2), target=hex(target)))
    return result


def _load_functions(path, names, parents):
    from elftools.elf.elffile import ELFFile

    with open(path, 'rb') as stream:
        initial = os.fstat(stream.fileno())
        elf = ELFFile(stream)
        machine = {'EM_X86_64': 62, 'EM_AARCH64': 183}.get(elf['e_machine'])
        if elf.elfclass != 64 or not elf.little_endian or not machine or elf['e_type'] not in ('ET_EXEC', 'ET_DYN'):
            raise ValueError('unsupported_binary')
        table = elf.get_section_by_name('.symtab')
        if table is None:
            raise ValueError('symbol_table_unavailable')
        symbols = {name: set() for name in names}
        for symbol in table.iter_symbols():
            name = symbol.name
            if name in symbols and symbol['st_info']['type'] == 'STT_FUNC' and isinstance(symbol['st_shndx'], int):
                symbols[name].add((symbol['st_value'], symbol['st_size'], symbol['st_shndx']))
        functions, reasons = {}, {}
        total = 0
        selected = 0
        for name in sorted(names):
            matches = symbols[name]
            if len(matches) != 1:
                reasons[name] = 'symbol_unavailable' if not matches else 'ambiguous_symbol'
                continue
            address, size, section_index = next(iter(matches))
            section = elf.get_section(section_index)
            offset = address - section['sh_addr']
            if not size or not section['sh_flags'] & 4 or section['sh_type'] != 'SHT_PROGBITS' or offset < 0 or offset + size > section['sh_size']:
                reasons[name] = 'unsupported_function_extent'
                continue
            function = dict(name=name, address=address, size=size, code=b'')
            functions[name] = function
            if name not in parents:
                continue
            if size > MAX_FUNCTION_BYTES:
                function['code_reason'] = 'function_byte_budget'
            elif selected >= MAX_FUNCTIONS or total + size > MAX_CODE_BYTES:
                function['code_reason'] = 'analysis_budget'
            else:
                stream.seek(section['sh_offset'] + offset)
                function['code'] = stream.read(size)
                if len(function['code']) != size:
                    raise ValueError('incomplete_function_bytes')
                selected += 1
                total += size
        build_id = None
        note = elf.get_section_by_name('.note.gnu.build-id')
        if note is not None:
            for record in note.iter_notes():
                if record['n_type'] == 'NT_GNU_BUILD_ID':
                    build_id = record['n_desc']
        final = os.fstat(stream.fileno())
        if (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns) != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns):
            raise ValueError('binary_changed_during_read')
        return functions, reasons, machine, build_id


def analyze(data, binary, tool=None):
    """Return deduplicated static evidence, without changing recorded model columns."""
    pairs = _pairs(data)
    result = dict(version=1, status='not_needed', pairs=pairs, basis='supplied_binary', trace_build_id_verified=False)
    if not pairs:
        return result
    strings = data['strings']
    pending = []
    for pair in pairs.values():
        if pair['parent_module'] >= 0 or pair['child_module'] >= 0:
            pair['reason'] = 'module_not_analyzed'
        else:
            pending.append(pair)
    if not pending:
        result['status'] = 'unavailable'
        result['reason'] = 'module_not_analyzed'
        return result
    try:
        names = {strings[pair[field]] for pair in pending for field in ['parent', 'child']}
        parents = {strings[pair['parent']] for pair in pending}
        functions, reasons, machine, build_id = _load_functions(binary, names, parents)
        selected = []
        total = 0
        for name in sorted(parents):
            function = functions.get(name)
            if function is None:
                continue
            if not function['code']:
                continue
            total += len(function['code'])
            selected.append(function)
        result['binary'] = dict(name=os.path.basename(binary), architecture='x86_64' if machine == 62 else 'arm64', build_id=build_id)
        digest = hashlib.sha256()
        for function in selected:
            digest.update(function['name'].encode() + b'\0' + struct.pack('<QQ', function['address'], len(function['code'])) + function['code'])
        result['code_sha256'] = digest.hexdigest()
        result['analyzed_functions'] = len(selected)
        result['analyzed_bytes'] = total
        sites = {}
        if selected:
            with tempfile.TemporaryDirectory(prefix='funcgraph-tail-') as directory:
                image = os.path.join(directory, 'selected.elf')
                _write_image(image, selected, machine)
                with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                    process = subprocess.run([tool or objdump_command(), '-d', '--no-show-raw-insn', image],
                                             stdout=output, stderr=errors, timeout=30,
                                             env=dict(os.environ, LC_ALL='C'))
                    if process.returncode or output.tell() > MAX_OUTPUT_BYTES:
                        raise ValueError('disassembly_failed' if process.returncode else 'disassembly_output_budget')
                    output.seek(0)
                    parsed = parse_disassembly(output.read().decode('utf-8', errors='replace'), selected, machine)
                    sites = {function['name']: jumps for function, jumps in zip(selected, parsed)}
        for pair in pending:
            parent, child = strings[pair['parent']], strings[pair['child']]
            if parent in reasons or child in reasons:
                pair['reason'] = reasons.get(parent) or reasons[child]
                continue
            if functions[parent].get('code_reason'):
                pair['reason'] = functions[parent]['code_reason']
                continue
            target = functions[child]['address']
            pair['sites'] = [site for site in sites.get(parent, []) if int(site['target'], 16) == target]
            if pair['sites']:
                pair['status'] = 'supported'
                pair['reason'] = 'direct_external_jump'
                for site in pair['sites']:
                    site['symbol'] = parent + '+0x%x/0x%x' % (site['offset'], functions[parent]['size'])
        result['status'] = 'available'
    except ImportError:
        result.update(status='unavailable', reason='pyelftools_unavailable')
    except FileNotFoundError:
        result.update(status='unavailable', reason='binary_or_objdump_unavailable')
    except subprocess.TimeoutExpired:
        result.update(status='unavailable', reason='disassembly_timeout')
    except (OSError, ValueError, struct.error) as error:
        result.update(status='unavailable', reason=str(error))
    except Exception as error:
        result.update(status='unavailable', reason='binary_analysis_failed: ' + type(error).__name__)
    if result['status'] == 'unavailable':
        for pair in pending:
            pair.update(status='unresolved', reason=result['reason'], sites=[])
    return result
