"""Conservative entry-register checks for BTF prototypes with compiler suffixes."""

import struct


def _attribute(die, name):
    for _depth in range(32):
        if name in die.attributes:
            return die, die.attributes[name]
        origin = next((key for key in ('DW_AT_abstract_origin', 'DW_AT_specification')
                       if key in die.attributes), None)
        if origin is None:
            break
        die = die.get_DIE_from_attribute(origin)
    return None, None


def _scalar(die, word_size):
    for _depth in range(32):
        if die.tag == 'DW_TAG_pointer_type':
            return 'ptr', word_size, word_size * 8
        if die.tag == 'DW_TAG_base_type':
            size = die.attributes.get('DW_AT_byte_size')
            encoding = die.attributes.get('DW_AT_encoding')
            if size is None or encoding is None:
                return None
            kind = {2: 'bool', 5: 'sint', 6: 'sint', 7: 'uint', 8: 'uint'}.get(encoding.value)
            bits = die.attributes.get('DW_AT_bit_size')
            return kind, size.value, bits.value if bits else size.value * 8
        if die.tag not in ('DW_TAG_typedef', 'DW_TAG_const_type', 'DW_TAG_volatile_type',
                           'DW_TAG_restrict_type', 'DW_TAG_atomic_type'):
            return None
        owner, typed = _attribute(die, 'DW_AT_type')
        if typed is None:
            return None
        die = owner.get_DIE_from_attribute('DW_AT_type')
    return None


def _entry_register(parameter, address, locations, cu):
    from elftools.dwarf.locationlists import BaseAddressEntry, LocationExpr

    location = parameter.attributes.get('DW_AT_location')
    if location is None:
        return None
    parsed = locations.parse_from_attribute(location, cu['version'], die=parameter)
    if isinstance(parsed, LocationExpr):
        expressions = [parsed.loc_expr]
    else:
        low_pc = cu.get_top_DIE().attributes.get('DW_AT_low_pc')
        base = low_pc.value if low_pc else 0
        expressions = []
        for entry in parsed:
            if isinstance(entry, BaseAddressEntry):
                base = entry.base_address
            else:
                offset = 0 if entry.is_absolute else base
                if offset + entry.begin_offset <= address < offset + entry.end_offset:
                    expressions.append(entry.loc_expr)
    if len(expressions) != 1:
        return None
    expression = expressions[0]
    if len(expression) == 1 and 0x50 <= expression[0] <= 0x6f:
        return expression[0] - 0x50
    return None


def _matches(die, address, base, fields, word_size, registers, locations):
    owner, name = _attribute(die, 'DW_AT_name')
    if name is None or name.value.decode('utf-8', 'replace') != base:
        return False
    children = list(die.iter_children())
    if any(child.tag == 'DW_TAG_unspecified_parameters' for child in children):
        return False
    parameters = [child for child in children if child.tag == 'DW_TAG_formal_parameter']
    if len(parameters) != len(fields) or len(parameters) > len(registers):
        return False
    for position, (parameter, (field, kind, size, bits)) in enumerate(zip(parameters, fields)):
        owner, name = _attribute(parameter, 'DW_AT_name')
        typed_owner, typed = _attribute(parameter, 'DW_AT_type')
        if name is None or name.value.decode('utf-8', 'replace') != field or typed is None:
            return False
        if _scalar(typed_owner.get_DIE_from_attribute('DW_AT_type'), word_size) != (kind, size, bits):
            return False
        if _entry_register(parameter, address, locations, die.cu) != registers[position]:
            return False
    return True


def verified_clones(path, requests):
    """Return only clones whose complete scalar BTF layout matches DWARF at entry.

    Optional pyelftools is used once per ELF batch. Missing/ambiguous symbols,
    aranges, debug locations, changed signatures and unsupported ABIs fail closed.
    No return-type equivalence is inferred from parameter locations.
    """
    try:
        from elftools.common.exceptions import DWARFError, ELFError
        from elftools.dwarf.dwarfinfo import DebugSectionDescriptor
        from elftools.dwarf.locationlists import LocationParser
        from elftools.elf.elffile import ELFFile
    except ImportError:
        return set()
    errors = (OSError, ELFError, DWARFError, ValueError, KeyError, IndexError,
              TypeError, AttributeError, struct.error, AssertionError)
    verified = set()
    dwarf = None
    try:
        with open(path, 'rb') as source:
            elf = ELFFile(source)
            registers = {'EM_X86_64': (5, 4, 1, 2, 8, 9), 'EM_AARCH64': tuple(range(8))}.get(elf['e_machine'])
            symbols = elf.get_section_by_name('.symtab')
            if registers is None or elf.elfclass != 64 or symbols is None or not elf.has_dwarf_info():
                return verified
            addresses = {}
            for symbol in symbols.iter_symbols():
                if symbol.name in requests and symbol['st_info']['type'] == 'STT_FUNC' and isinstance(symbol['st_shndx'], int):
                    addresses.setdefault(symbol.name, set()).add(symbol['st_value'])
            if not addresses:
                return verified
            dwarf = elf.get_dwarf_info(follow_links=False)
            aranges = dwarf.get_aranges()
            if aranges is None:
                return verified
            groups = {}
            for name, values in addresses.items():
                if len(values) != 1 or requests[name][2] != elf.elfclass // 8:
                    continue
                address = next(iter(values))
                offset = aranges.cu_offset_at_addr(address)
                if offset is not None:
                    groups.setdefault(offset, []).append((name, address))
            locations = LocationParser(dwarf.location_lists())
            for offset, names in groups.items():
                wanted = {address for name, address in names}
                functions = {}
                for die in dwarf.get_CU_at(offset).iter_DIEs():
                    address = die.attributes.get('DW_AT_low_pc')
                    if die.tag == 'DW_TAG_subprogram' and address is not None and address.value in wanted:
                        functions[address.value] = None if address.value in functions else die
                for name, address in names:
                    die = functions.get(address)
                    if die is not None:
                        base, fields, word_size = requests[name]
                        try:
                            if _matches(die, address, base, fields, word_size, registers, locations):
                                verified.add(name)
                        except errors:
                            pass
    except errors:
        return set()
    finally:
        if dwarf is not None:
            for section in vars(dwarf).values():
                if isinstance(section, DebugSectionDescriptor):
                    section.stream.close()
    return verified
