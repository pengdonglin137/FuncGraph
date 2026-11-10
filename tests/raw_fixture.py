"""Small synthetic trace-cmd v6 containers; no tracefs access or kernel required."""
import struct


def container(endian='<', width=8, clock='mono', lost=0, retaddr=False,
              symbols=True, bad_depth=False, args=True, module=False,
              sched=False, retval=True, dynamic_args=False):
    def packed(layout, *values):
        return struct.pack(endian + layout, *values)

    def sized(data, layout='Q'):
        return packed(layout, len(data)) + data

    def event_format(name, identifier, fields, printing):
        common = [('unsigned short', 'common_type', 0, 2),
                  ('unsigned char', 'common_flags', 2, 1),
                  ('unsigned char', 'common_preempt_count', 3, 1),
                  ('int', 'common_pid', 4, 4)]
        def field_text(field):
            datatype, field_name, offset, size = field
            return f'\tfield:{datatype} {field_name};\toffset:{offset};\tsize:{size};\tsigned:0;\n'
        return (f'name: {name}\nID: {identifier}\nformat:\n' +
                ''.join(map(field_text, common)) + '\n' +
                ''.join(map(field_text, fields)) + f'\nprint fmt: {printing}\n').encode()

    word = 'Q' if width == 8 else 'I'
    entry_name = 'fgraph_retaddr_entry' if retaddr else 'funcgraph_entry'
    entry_fields = [('unsigned long', 'func', 8, width),
                    ('unsigned long', 'depth', 8 + width, width)]
    if retaddr:
        entry_fields.append(('unsigned long', 'retaddr', 8 + 2 * width, width))
    entry_fields.append(('__data_loc unsigned long' if dynamic_args else 'unsigned long',
                         'args[]', 8 + width * (3 if retaddr else 2), 4 if dynamic_args else 0))
    entry = event_format(entry_name, 11, entry_fields, '"entry"')
    exit_fields = [('unsigned long', 'func', 8, width),
                   ('unsigned int', 'depth', 8 + width, 4),
                   ('unsigned int', 'overrun', 12 + width, 4),
                   ('unsigned long long', 'calltime', 16 + width, 8),
                   ('unsigned long long', 'rettime', 24 + width, 8),
                   ('unsigned long', 'retval', 32 + width, width)]
    exiting = event_format('funcgraph_exit', 12, exit_fields if retval else exit_fields[:-1], '"exit"')
    systems = packed('I', 0)
    if sched:
        switch_fields = [('char', 'prev_comm[16]', 8, 16), ('int', 'prev_pid', 24, 4),
                         ('int', 'prev_prio', 28, 4), ('long', 'prev_state', 32, width),
                         ('char', 'next_comm[16]', 32 + width, 16),
                         ('int', 'next_pid', 48 + width, 4), ('int', 'next_prio', 52 + width, 4)]
        switching = event_format('sched_switch', 13, switch_fields,
                                '"prev_comm=%s prev_pid=%d prev_prio=%d prev_state=%s ==> '
                                'next_comm=%s next_pid=%d next_prio=%d", REC->prev_comm, '
                                'REC->prev_pid, REC->prev_prio, '
                                'REC->prev_state ? "D" : "R", REC->next_comm, '
                                'REC->next_pid, REC->next_prio')
        wakeup = event_format('sched_wakeup', 14, [('char', 'comm[16]', 8, 16),
                             ('int', 'pid', 24, 4), ('int', 'prio', 28, 4), ('int', 'target_cpu', 32, 4)],
                             '"comm=%s pid=%d prio=%d target_cpu=%d", REC->comm, '
                             'REC->pid, REC->prio, REC->target_cpu')
        systems = packed('I', 1) + b'sched\0' + packed('I', 2) + sized(switching) + sized(wakeup)
    header_size = 8 + width
    header = (f'field:u64 timestamp; offset:0; size:8; signed:0;\n'
              f'field:local_t commit; offset:8; size:{width}; signed:1;\n'
              f'field:int overwrite; offset:8; size:1; signed:1;\n'
              f'field:char data; offset:{header_size}; size:{4096 - header_size}; signed:1;\n').encode()
    kallsyms = (b'0000000000001000 T root\n0000000000001100 T child\n'
                b'0000000000001200 T boundary\n') if symbols else b''
    if module:
        kallsyms = kallsyms.replace(b'child\n', b'child\t[demo_mod]\n')
    clock_text = ('[' + clock + ']').encode()
    metadata = (b'\x17\x08Dtracing6\0' + bytes([int(endian == '>'), width]) + packed('I', 4096) +
                b'header_page\0' + sized(header) + b'header_event\0' + sized(b'') +
                packed('I', 2) + sized(entry) + sized(exiting) + systems +
                sized(kallsyms, 'I') + sized(b'', 'I') + sized(b'42 worker\n') + packed('I', 1) +
                b'options\0\0\0' + packed('HI', 4, len(clock_text)) + clock_text + packed('H', 0) +
                b'flyrecord\0')
    base = 1000000000
    def entry_data(address, depth):
        values = packed(word * 2, address, 5000 if bad_depth else depth)
        if retaddr:
            values += packed(word, 0x1008)
        arguments = packed(word * 2, 0x123, 0x456) if args else b''
        if dynamic_args:
            values += packed('I', len(arguments) << 16 | (8 + len(values) + 4))
        values += arguments
        return packed('HBBi', 11, 0, 1, 42) + values

    def exit_data(address, depth, start, end, returned):
        return (packed('HBBi', 12, 0, 1, 42) + packed(word, address) +
                packed('IIQQ', depth, 0, base + start, base + end) +
                (packed(word, returned) if retval else b''))

    records = [(0, entry_data(0x1000, 0)), (101, entry_data(0x1100, 1)),
               (310, exit_data(0x1100, 1, 101, 310, (1 << (width * 8)) - 2)),
               (513, exit_data(0x1000, 0, 0, 513, 0))]
    if sched:
        def switching_data(previous, following, prev_state):
            return (packed('HBBi', 13, 1, 2, previous) + b'worker\0'.ljust(16, b'\0') +
                    packed('ii' + word, previous, 120, prev_state) +
                    b'worker\0'.ljust(16, b'\0') + packed('ii', following, 120))
        records.extend([(200, switching_data(42, 43, 2)), (250, switching_data(43, 42, 0)),
                        (225, packed('HBBi', 14, 0, 1, 43) + b'worker\0'.ljust(16, b'\0') +
                         packed('iii', 42, 120, 0))])
        records.sort(key=lambda item: item[0])
    payload = b''
    previous = 0
    for timestamp, data in records:
        length = len(data) // 4
        delta = timestamp - previous
        header_value = (length << 27 | delta) if endian == '>' else (delta << 5 | length)
        payload += packed('I', header_value) + data
        previous = timestamp
    flags = len(payload)
    trailer = b''
    if lost:
        flags |= 1 << 31
        if lost > 0:
            flags |= 1 << 30
            trailer = packed(word, lost)
    page = packed('Q' + word, base, flags) + payload + trailer
    offset = (len(metadata) + 16 + 8 + len(clock_text) + 4095) // 4096 * 4096
    metadata += packed('QQ', offset, 4096) + sized(clock_text)
    return metadata.ljust(offset, b'\0') + page.ljust(4096, b'\0')
